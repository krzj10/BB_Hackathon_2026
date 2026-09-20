"""B04 Decision Inbox service.

Decisions are fed EXCLUSIVELY by decision_required Attention items (the
projection is created atomically with the item in AttentionRepository.add).
This service adds user-facing state transitions:

- DEFER / (UI "ask EVA") - internal inbox bookkeeping only, no external effect.
- ACCEPT / REJECT - ALWAYS a guarded local_write proposal through the A03/A04
  path (policy derives risk = max(local_write_floor, decision.risk); HIGH is
  UI-confirm-only). Recording an outcome stores an audited internal result; it
  NEVER pays, purchases, commits to a supplier/contract or sends anything -
  the registered tool set makes that structurally impossible.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from ..approvals.engine import (
    ActionPolicyError,
    ProposalContext,
    arguments_digest,
    canonical_arguments,
)
from ..contracts.domain import (
    Decision,
    DecisionOutcome,
    DecisionRecordOutcomeArguments,
    DecisionStatus,
    EventEnvelope,
    EventType,
    ProposedAction,
    ProposedActionStatus,
    ToolCall,
)
from ..db.repositories import ActionRepository, DecisionRepository

logger = logging.getLogger("eva.decisions")

_FINAL_STATUSES = frozenset({DecisionStatus.RESOLVED, DecisionStatus.DISMISSED})
_IN_FLIGHT_ACTION_STATUSES = frozenset(
    {
        ProposedActionStatus.PENDING,
        ProposedActionStatus.APPROVED,
        ProposedActionStatus.EXECUTING,
    }
)


class DecisionConflictError(Exception):
    """Stable-code rejection of an inbox transition (already final etc.)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class DecisionService:
    def __init__(
        self,
        *,
        repo: DecisionRepository,
        action_repo: ActionRepository,
        engine,
        executor,
        clock: Callable[[], datetime],
        outbox=None,
    ) -> None:
        self._repo = repo
        self._action_repo = action_repo
        self._engine = engine
        self._executor = executor
        self._clock = clock
        self._outbox = outbox

    # ------------------------------------------------------------------ #
    def get(self, decision_id: str) -> Decision | None:
        return self._repo.get(decision_id)

    def list_all(self) -> list[Decision]:
        return self._repo.list_all()

    # ------------------------------------------------------------------ #
    def defer(self, decision_id: str, *, reason: str | None = None) -> Decision:
        """Internal inbox state only. The optional free-text reason is not a
        Decision contract field and is deliberately NOT invented into the
        stored model - it stays out (sanitized log line)."""
        decision = self._require(decision_id)
        if decision.status in _FINAL_STATUSES:
            raise DecisionConflictError(
                "decision_final", f"decision is {decision.status.value}; cannot defer"
            )
        if reason:
            logger.info("decision %s deferred with user note (not stored)", decision.id)
        if decision.status is DecisionStatus.DEFERRED:
            return decision
        updated = self._transition(decision, status=DecisionStatus.DEFERRED)
        self._repo.update(updated)
        self._emit(updated)
        return updated

    # ------------------------------------------------------------------ #
    def create_outcome_proposal(
        self, decision_id: str, outcome: DecisionOutcome, *, session_id: str, request_id: str
    ) -> ProposedAction:
        """User-initiated local outcome proposal (frozen route body). Policy -
        not this service - decides risk/approval; HIGH decisions therefore
        require explicit UI confirmation through the standard A03 flow."""
        decision = self._require(decision_id)
        if decision.status in _FINAL_STATUSES:
            raise DecisionConflictError(
                "decision_final",
                f"decision is {decision.status.value}; outcome can no longer be proposed",
            )

        # One in-flight outcome proposal per decision (replay, never stack).
        if decision.proposed_action_id is not None:
            existing = self._action_repo.get_action(decision.proposed_action_id)
            if existing is not None and existing.status in _IN_FLIGHT_ACTION_STATUSES:
                if existing.arguments.get("outcome") != outcome.value:
                    raise DecisionConflictError(
                        "outcome_proposal_active",
                        "a different outcome proposal for this decision is still in flight",
                    )
                return existing

        arguments = {
            "tool": "decision.record_outcome",
            "decision_id": decision.id,
            "outcome": outcome.value,
        }
        try:
            validated = self._engine.validate_arguments(
                "decision.record_outcome", arguments
            )
        except ActionPolicyError as exc:
            raise DecisionConflictError(exc.code, exc.detail) from None
        digest = arguments_digest(canonical_arguments(validated))

        try:
            proposed = self._engine.propose(
                _outcome_call(request_id, arguments),
                ProposalContext(
                    session_id=session_id, request_id=request_id, now=self._clock(),
                    decision=decision,
                ),
                persist=False,
            )
        except ActionPolicyError as exc:
            raise DecisionConflictError(exc.code, exc.detail) from None
        slot = self._action_repo.create_idempotent_action(
            proposed,
            session_id=session_id,
            request_id=request_id,
            arguments_digest=digest,
        )
        if slot.status == "conflict":
            raise DecisionConflictError(
                "request_replay_mismatch",
                "this request id already produced a different proposal",
            )
        action_id = slot.action_id
        action = self._action_repo.get_action(action_id)
        if action is None:  # slot without an action cannot happen durably
            raise DecisionConflictError("internal", "idempotency record inconsistent")

        # Link the decision to its proposal (status untouched; the executor's
        # local handler records the outcome itself on execution).
        if decision.proposed_action_id != action.id:
            linked = self._transition(decision, proposed_action_id=action.id)
            self._repo.update(linked)

        if not action.requires_approval:
            executed = self._executor.execute(action.id)
            action = executed.action or action
        return action

    # ------------------------------------------------------------------ #
    def record_outcome(self, args: DecisionRecordOutcomeArguments) -> dict:
        """Local-write handler for the guarded executor. Runs ONLY after the
        proposal passed policy + (for HIGH) explicit UI approval."""
        decision = self._require(args.decision_id)
        if decision.status is DecisionStatus.DISMISSED:
            # Final state: an outcome can never be recorded on a dismissed
            # decision (defense-in-depth; proposals are already blocked).
            raise ValueError("decision is dismissed; outcome cannot be recorded")
        if decision.status is DecisionStatus.RESOLVED:
            if decision.outcome is args.outcome:
                # Durable idempotent replay of the same recorded outcome.
                return {
                    "decision_id": decision.id,
                    "outcome": args.outcome.value,
                    "already_recorded": True,
                }
            raise ValueError("decision already resolved with a different outcome")
        updated = self._transition(
            decision,
            status=DecisionStatus.RESOLVED,
            outcome=args.outcome.value,
            outcome_recorded_at=self._clock(),
        )
        self._repo.update(updated)
        self._emit(updated)
        return {
            "decision_id": updated.id,
            "outcome": updated.outcome.value if updated.outcome else None,
            "status": updated.status.value,
            "executed_externally": False,
        }

    # ------------------------------------------------------------------ #
    def _require(self, decision_id: str) -> Decision:
        decision = self._repo.get(decision_id)
        if decision is None:
            raise KeyError(f"unknown decision {decision_id!r}")
        return decision

    @staticmethod
    def _transition(decision: Decision, **changes) -> Decision:
        # Re-run canonical validators on the updated model (never bypass).
        return Decision.model_validate({**decision.model_dump(), **changes})

    def _emit(self, decision: Decision) -> None:
        if self._outbox is None:
            return
        from ..contracts.domain import DecisionUpdatedPayload

        try:
            self._outbox.append(
                EventEnvelope(
                    event_id=f"evt-decision-update-{decision.id}-{decision.status.value}",
                    sequence=0,
                    session_id="eva-system",
                    occurred_at=self._clock(),
                    type=EventType.DECISION_UPDATED,
                    payload=DecisionUpdatedPayload(decision=decision),
                )
            )
        except Exception as exc:  # live delivery hardening is A07
            logger.error("decision live-event append failed (%s)", type(exc).__name__)


def _outcome_call(request_id: str, arguments: dict) -> ToolCall:
    return ToolCall(
        id=f"decision-outcome-{request_id}",
        name="decision.record_outcome",
        arguments=arguments,
    )
