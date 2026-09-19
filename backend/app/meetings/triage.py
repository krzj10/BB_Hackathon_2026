"""Deterministic meeting triage (A03).

MeetingTriage.evaluate(meeting, evidence) -> priority + structured Reasons is
the frozen service boundary (docs/api-contracts.md). All rule decisions come
from the authoritative LoadedPolicy - never from LLM output, user preferences,
ORG data or Focus state.

Evidence is a small internal structured representation consumed by policy;
extracting such signals from Gmail is A06's job. This module performs no free
-text amount parsing and no currency conversion.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

from app.approvals.policy import LoadedPolicy
from app.contracts.domain import (
    Meeting,
    MeetingPriority,
    Money,
    Participant,
    Reason,
    ReasonOrigin,
)

#: A02 read adapter placeholder marker: a priority carrying it is provisional
#: and must never be mistaken for an authoritative policy decision.
UNTRIAGED_REASON_CODE = "untriaged_read_default"

_PRIORITY_ORDER = {
    MeetingPriority.LOW: 0,
    MeetingPriority.MEDIUM: 1,
    MeetingPriority.HIGH: 2,
}


@dataclass(frozen=True)
class TriageEvidence:
    """Structured signals the policy kernel evaluates (produced upstream,
    e.g. by A06 - never parsed from free text here)."""

    financial_decision_intent: bool = False
    money: Money | None = None
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TriageResult:
    priority: MeetingPriority
    reasons: list[Reason] = field(default_factory=list)


def _max_priority(*priorities: MeetingPriority) -> MeetingPriority:
    return max(priorities, key=lambda p: _PRIORITY_ORDER[p])


class MeetingTriage:
    """Deterministic v2.0 triage rules over meeting content + evidence."""

    def __init__(self, policy: LoadedPolicy) -> None:
        self._policy = policy

    # -- public frozen boundary ------------------------------------------------

    def evaluate(
        self, meeting: Meeting, evidence: TriageEvidence | None = None
    ) -> TriageResult:
        texts = [meeting.title, meeting.description, meeting.location, meeting.agenda]
        evaluated = self._evaluate_signals(
            texts=texts,
            attendees=meeting.attendees,
            evidence=evidence,
            extra_source_ids=[source.id for source in meeting.sources],
        )
        prior = meeting.priority
        provisional = any(
            reason.code == UNTRIAGED_REASON_CODE for reason in meeting.priority_reasons
        )
        if provisional:
            # A02 placeholder: re-evaluation wins outright; the provisional
            # value can neither hold back an escalation nor justify a level.
            return evaluated
        # An existing authoritative HIGH is never silently downgraded by a
        # later partial re-read; everything else follows fresh evaluation
        # (escalations always apply).
        if prior == MeetingPriority.HIGH and _PRIORITY_ORDER[prior] > _PRIORITY_ORDER[evaluated.priority]:
            reasons = list(evaluated.reasons)
            reasons.append(
                self._reason(
                    "meeting.authoritative_priority_retained",
                    f"previous authoritative {prior.value} priority retained over "
                    f"{evaluated.priority.value} re-evaluation",
                    [],
                )
            )
            return TriageResult(priority=prior, reasons=reasons)
        return evaluated

    # -- proposed-content triage (calendar.create_event) ------------------------

    def evaluate_proposed(
        self,
        *,
        title: str,
        attendees: Iterable[Participant] = (),
        description: str | None = None,
        location: str | None = None,
        agenda: str | None = None,
        evidence: TriageEvidence | None = None,
    ) -> TriageResult:
        """Triage a PROPOSED meeting against its content before approval.

        No external Google identity exists for proposals and none is invented:
        this works on the proposed content only."""
        return self._evaluate_signals(
            texts=[title, description, location, agenda],
            attendees=list(attendees),
            evidence=evidence,
            extra_source_ids=[],
        )

    # -- rule core ---------------------------------------------------------------

    def _evaluate_signals(
        self,
        *,
        texts: list[str | None],
        attendees: list[Participant],
        evidence: TriageEvidence | None,
        extra_source_ids: list[str],
    ) -> TriageResult:
        cfg = self._policy.config.meeting_triage
        haystack = _fold(" ".join(text for text in texts if text) + " " + " ".join(
            f"{p.name or ''} {p.company or ''}" for p in attendees
        ))
        reasons: list[Reason] = []
        priority = MeetingPriority.MEDIUM  # conservative default floor

        high_hit = _first_marker(haystack, cfg.high_markers)
        medium_hit = _first_marker(haystack, cfg.medium_markers)
        low_hit = _first_marker(haystack, cfg.low_markers)

        financial = self._financial_reasons(evidence, reasons)

        if high_hit is not None:
            priority = MeetingPriority.HIGH
            reasons.append(
                self._reason(
                    "meeting.board_investor_high",
                    f"board/investor/strategic context marker matched: {high_hit!r}",
                    extra_source_ids,
                )
            )
        elif financial is MeetingPriority.HIGH:
            priority = MeetingPriority.HIGH
        elif medium_hit is not None:
            priority = MeetingPriority.MEDIUM
            reasons.append(
                self._reason(
                    "meeting.team_customer_floor",
                    f"team/project/customer context marker matched: {medium_hit!r}",
                    extra_source_ids,
                )
            )
        elif low_hit is not None:
            # LOW means "informal optional INTERNAL catch-up": an informal text
            # marker alone never proves it. LOW eligibility requires affirmative
            # internal evidence for every attendee (Participant.internal is
            # used here as TRIAGE evidence only - the notification-authorization
            # rule in the approval engine deliberately never trusts it).
            if _affirmatively_internal(attendees):
                priority = MeetingPriority.LOW
                reasons.append(
                    self._reason(
                        "meeting.internal_optional",
                        f"informal optional internal catch-up marker matched: {low_hit!r}; "
                        "all attendees affirmatively internal",
                        extra_source_ids,
                    )
                )
            else:
                reasons.append(
                    self._reason(
                        "meeting.informal_internal_unconfirmed",
                        f"informal marker matched ({low_hit!r}) but internal-only attendance "
                        "is not affirmatively established (every attendee must carry an "
                        "explicit internal classification; unknown, external or missing "
                        "attendees prove nothing); conservative MEDIUM floor applies",
                        extra_source_ids,
                    )
                )
        else:
            reasons.append(
                self._reason(
                    "meeting.default_medium_floor",
                    "no triage marker matched; conservative MEDIUM floor applies",
                    extra_source_ids,
                )
            )
        return TriageResult(priority=priority, reasons=reasons)

    def _financial_reasons(
        self, evidence: TriageEvidence | None, sink: list[Reason]
    ) -> MeetingPriority | None:
        """Apply the financial HIGH rule: CURRENT decision intent AND PLN
        amount >= threshold. Amount alone never triggers (quoted/historical
        amounts are not a new decision); unknown currency is never converted."""
        if evidence is None or evidence.money is None:
            return None
        fin = self._policy.config.financial_high
        money = evidence.money
        source_ids = list(evidence.source_ids)
        if money.currency != fin.currency:
            sink.append(
                self._reason(
                    "meeting.financial_unsupported_currency",
                    f"amount in {money.currency} does not match the policy decision "
                    f"currency {fin.currency}; no conversion is performed",
                    source_ids,
                )
            )
            return None
        if not evidence.financial_decision_intent:
            sink.append(
                self._reason(
                    "meeting.financial_amount_without_intent",
                    "financial amount present without current decision intent; "
                    "not escalated (quoted or historical amounts are not decisions)",
                    source_ids,
                )
            )
            return None
        if money.amount_minor_units >= fin.threshold_minor_units:
            sink.append(
                self._reason(
                    "meeting.financial_decision_high",
                    f"current financial decision intent with {money.currency} amount "
                    f"{money.amount_minor_units} >= threshold "
                    f"{fin.threshold_minor_units} minor units",
                    source_ids,
                )
            )
            return MeetingPriority.HIGH
        sink.append(
            self._reason(
                "meeting.financial_below_threshold",
                f"financial decision amount {money.amount_minor_units} is below the "
                f"{fin.threshold_minor_units} minor-unit threshold",
                source_ids,
            )
        )
        return None

    def _reason(self, code: str, text: str, source_ids: list[str]) -> Reason:
        return Reason(
            code=code,
            origin=ReasonOrigin.RULE,
            text=text,
            source_ids=list(dict.fromkeys(source_ids)),  # dedupe, keep order
            policy_version=self._policy.version,
        )


def _fold(text: str) -> str:
    """Deterministic case/diacritic folding so ASCII markers match Polish
    text (zarząd -> zarzad). Unicode-normalize, drop combining marks, lower."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def _first_marker(haystack: str, markers: list[str]) -> str | None:
    folded_hay = _fold(haystack)
    for marker in markers:
        if _fold(marker) in folded_hay:
            return marker
    return None


def _affirmatively_internal(attendees: list[Participant]) -> bool:
    """LOW-eligibility check (triage evidence only): attendees exist AND every
    one carries an explicit internal=True classification. internal=None is not
    internal; nothing is ever inferred from email domain, company name,
    display name, organizer or the absence of attendees."""
    return bool(attendees) and all(p.internal is True for p in attendees)
