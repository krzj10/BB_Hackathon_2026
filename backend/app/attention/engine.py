"""B04 AttentionEngine: the real AttentionSink behind A06 ingestion.

Pipeline (plan §8, work plan B04):

    A06 ingestion -> NormalizedSourceEvent -> deterministic AttentionRules
    -> optional LLM ambiguity classification -> clamp_to_rule_floor
    -> AttentionItem -> Decision projection (decision_required only)

Non-negotiable rules enforced here:

- The deterministic rule result is a FLOOR. The optional LLM classifier may
  only STRENGTHEN priority/type via ``clamp_to_rule_floor``; a weaker proposal
  is clamped back up, and any classifier failure keeps the floor silently.
- Email content is untrusted EVIDENCE: it feeds rules and previews, never an
  instruction channel. The engine executes nothing - a DECISION_REQUIRED item
  projects exactly one Decision (persisted atomically with the item via
  AttentionRepository.add), which itself authorizes no external action.
- Focus state influences DELIVERY only; classification is computed before and
  independently of the delivery decision.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from ..contracts.domain import (
    ActionRisk,
    AttentionItem,
    AttentionPriority,
    AttentionType,
    Claim,
    ClaimKind,
    Decision,
    DecisionStatus,
    NormalizedSourceEvent,
    Reason,
    ReasonOrigin,
)
from .rules import AttentionRuleResult, AttentionRules, clamp_to_rule_floor

logger = logging.getLogger("eva.attention.engine")

#: Preview length stored on AttentionItem (bounded; bodies stay in the source).
PREVIEW_MAX_CHARS = 500

_PRIORITY_TO_RISK = {
    AttentionPriority.LOW: ActionRisk.LOW,
    AttentionPriority.MEDIUM: ActionRisk.MEDIUM,
    AttentionPriority.HIGH: ActionRisk.HIGH,
}


@dataclass(frozen=True)
class AmbiguitySuggestion:
    """What an LLM ambiguity classifier MAY propose - priority/type only.

    No money, no deadline, no approval semantics, no action: the classifier is
    a strengthener of the deterministic floor and nothing else."""

    priority: AttentionPriority
    attention_type: AttentionType | None = None
    note: str | None = None


class AttentionAmbiguityClassifier(Protocol):
    def classify(self, event: NormalizedSourceEvent) -> AmbiguitySuggestion | None: ...


def _preview(body: str | None) -> str:
    text = (body or "").strip()
    if not text:
        return "(no body)"
    if len(text) <= PREVIEW_MAX_CHARS:
        return text
    return text[:PREVIEW_MAX_CHARS].rstrip() + "…"


class AttentionEngine:
    """Frozen sink contract: ``ingest(source_event) -> AttentionItem``.

    Pure computation plus a bounded pending-decision handoff: A06 persists the
    returned item through ``AttentionRepository.add(item, decision)``, and the
    repository asks :meth:`decision_projection` for the linked Decision inside
    the SAME transaction (one Decision per item, never on a dedup miss)."""

    def __init__(
        self,
        *,
        rules: AttentionRules,
        policy_version: str,
        focus_service,
        clock: Callable[[], datetime],
        classifier: AttentionAmbiguityClassifier | None = None,
    ) -> None:
        self._rules = rules
        self._policy_version = policy_version
        self._focus = focus_service
        self._clock = clock
        self._classifier = classifier
        # item_id -> Decision awaiting atomic persistence; bounded FIFO. The
        # handoff lives microseconds between ingest() and add().
        self._pending_decisions: "OrderedDict[str, Decision]" = OrderedDict()

    # ------------------------------------------------------------------ #
    def ingest(self, source_event: NormalizedSourceEvent) -> AttentionItem:
        rule_result = self._rules.evaluate(source_event)
        priority = rule_result.priority_floor
        attention_type = rule_result.attention_type
        reasons = list(rule_result.reasons)

        if self._classifier is not None and self._ambiguous(rule_result):
            strengthened = self._classify_quietly(source_event, rule_result)
            if strengthened is not None:
                llm_priority, llm_type, note = strengthened
                priority, attention_type = clamp_to_rule_floor(
                    rule_result, llm_priority, llm_type
                )
                if (priority, attention_type) != (
                    rule_result.priority_floor,
                    rule_result.attention_type,
                ):
                    reasons.append(
                        Reason(
                            code="attention.llm_strengthened",
                            origin=ReasonOrigin.LLM,
                            text=(note or "LLM ambiguity classification strengthened the "
                                  "deterministic floor"),
                            source_ids=[s.id for s in source_event.sources[:3]],
                            policy_version=self._policy_version,
                        )
                    )

        now = self._clock()
        delivery, delivery_reasons = self._focus.decide_delivery(
            priority=priority,
            urgent=rule_result.urgent,
            sender_email=source_event.sender_email,
            now=now,
        )

        item_id = f"att-{uuid.uuid4()}"
        decision_id = (
            f"dec-{uuid.uuid4()}"
            if attention_type is AttentionType.DECISION_REQUIRED
            else None
        )
        item = AttentionItem(
            id=item_id,
            source=source_event.source,
            source_id=source_event.source_id,
            sender_email=source_event.sender_email,
            title=(source_event.subject or "(no subject)").strip()[:300],
            content_preview=_preview(source_event.body),
            received_at=source_event.received_at,
            attention_type=attention_type,
            urgent=rule_result.urgent,
            priority=priority,
            confidence=rule_result.confidence,
            reasons=reasons,
            sources=list(source_event.sources),
            deadline=rule_result.deadline,
            decision_id=decision_id,
            delivery=delivery,
            delivery_reasons=delivery_reasons,
        )
        if decision_id is not None:
            self._remember(item.id, self._build_decision(item, rule_result))
        return item

    # ------------------------------------------------------------------ #
    def decision_projection(self, item: AttentionItem) -> Decision | None:
        """Called by the persistence hook for a just-stored item. Returns the
        Decision built during ingest(); rebuilds conservatively from the item
        if the in-process handoff was lost (restart between ingest and add is
        impossible - both happen inside one ingestion loop iteration)."""
        cached = self._pending_decisions.pop(item.id, None)
        if cached is not None:
            return cached
        if item.attention_type is not AttentionType.DECISION_REQUIRED:
            return None
        return self._build_decision(item, None)

    # ------------------------------------------------------------------ #
    def _ambiguous(self, rule_result: AttentionRuleResult) -> bool:
        """Only genuinely low-signal cases reach the model: hard floors
        (financial HIGH, exact important sender, explicit decision vocabulary)
        need no opinion."""
        return (
            rule_result.priority_floor is AttentionPriority.LOW
            and not rule_result.financial_decision_intent
            and rule_result.confidence < 0.75
        )

    def _classify_quietly(
        self, event: NormalizedSourceEvent, rule_result: AttentionRuleResult
    ) -> tuple[AttentionPriority, AttentionType | None, str | None] | None:
        try:
            suggestion = self._classifier.classify(event)  # type: ignore[union-attr]
        except Exception as exc:  # sanitized; the deterministic floor stands
            logger.warning(
                "LLM ambiguity classification unavailable (%s); deterministic floor kept",
                type(exc).__name__,
            )
            return None
        if suggestion is None:
            return None
        if not isinstance(suggestion.priority, AttentionPriority) or (
            suggestion.attention_type is not None
            and not isinstance(suggestion.attention_type, AttentionType)
        ):
            logger.warning("LLM ambiguity suggestion had non-canonical values; ignored")
            return None
        return suggestion.priority, suggestion.attention_type, suggestion.note

    def _build_decision(
        self, item: AttentionItem, rule_result: AttentionRuleResult | None
    ) -> Decision:
        source_ids = [s.id for s in item.sources][:3]
        context_claim = Claim(
            text=item.content_preview,
            kind=ClaimKind.FACT if source_ids else ClaimKind.INFERENCE,
            source_ids=source_ids,
        )
        risks: list[Claim] = []
        if rule_result is not None and rule_result.financial_decision_intent:
            risks.append(
                Claim(
                    text=(
                        "Recording an accept/reject decision stores the internal "
                        "outcome only; it executes no payment, purchase, supplier "
                        "or contract commitment."
                    ),
                    kind=ClaimKind.INFERENCE,
                )
            )
        return Decision(
            id=item.decision_id or f"dec-{uuid.uuid4()}",
            attention_item_id=item.id,
            title=item.title,
            money=rule_result.money if rule_result is not None else None,
            deadline=item.deadline,
            context=[context_claim],
            alternatives=[],
            risks=risks,
            preference_conflicts=[],
            suggested_next_step=(
                "Review the evidence, then record accept or reject in the "
                "Decision Inbox."
            ),
            risk=_PRIORITY_TO_RISK[item.priority],
            status=DecisionStatus.NEEDS_REVIEW,
            sources=list(item.sources),
        )

    def _remember(self, item_id: str, decision: Decision) -> None:
        self._pending_decisions[item_id] = decision
        while len(self._pending_decisions) > 256:
            self._pending_decisions.popitem(last=False)
