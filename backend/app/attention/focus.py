"""B04 Focus service: one active session, delivery-only interruption policy.

Invariants enforced here (plan §8 / work plan B04):

- Exactly ONE active focus session; "active" is derived from timestamps and
  stopped_at only - never a stored boolean.
- The session threshold affects DELIVERY of Attention items exclusively. A
  deferred item is still persisted with its full deterministic classification;
  deferral suppresses surfacing, never priority, type or Decision creation.
- ``sender_overrides`` are EXACT normalized addresses and can only re-admit
  delivery (interrupt exception). They never change priority, risk or any
  approval semantics.
- The completion summary is computed from persisted, deduplicated Attention
  rows for the session window - no LLM-generated counts, reproducible after
  reload because it reads the same stored rows.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from ..contracts.domain import (
    AttentionItem,
    AttentionPriority,
    AttentionType,
    DeliveryDecision,
    FocusCompletionSummary,
    FocusSession,
    FocusStartArguments,
    Reason,
    ReasonOrigin,
)
from ..db.repositories import AttentionRepository, FocusRepository

logger = logging.getLogger("eva.attention.focus")

#: Session id recorded on live events that originate from background work
#: (polling, local tool handlers). EVA is a single-user local app; WS clients
#: subscribe with their own id and receive the shared stream.
SYSTEM_EVENT_SESSION = "eva-system"

_PRIORITY_RANK = {
    AttentionPriority.LOW: 0,
    AttentionPriority.MEDIUM: 1,
    AttentionPriority.HIGH: 2,
}


class FocusConflictError(Exception):
    """Stable-code rejection of a focus command (already active / none active)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _rule_reason(code: str, text: str, policy_version: str) -> Reason:
    return Reason(
        code=code, origin=ReasonOrigin.RULE, text=text, policy_version=policy_version
    )


class FocusService:
    def __init__(
        self,
        *,
        repo: FocusRepository,
        attention_repo: AttentionRepository,
        clock,
        policy_version: str,
        outbox=None,
    ) -> None:
        self._repo = repo
        self._attention_repo = attention_repo
        self._clock = clock
        self._policy_version = policy_version
        self._outbox = outbox

    # ------------------------------------------------------------------ #
    def current(self, now: datetime | None = None) -> FocusSession | None:
        return self._repo.get_active(now or self._clock())

    # ------------------------------------------------------------------ #
    def decide_delivery(
        self,
        *,
        priority: AttentionPriority,
        urgent: bool,
        sender_email: str,
        now: datetime,
    ) -> tuple[DeliveryDecision, list[Reason]]:
        """Delivery policy for one freshly classified item.

        The session can only DEFER delivery of below-threshold items; it never
        lowers priority or type. Urgent items and exact-sender exceptions are
        delivered through an active session."""
        session = self._repo.get_active(now)
        if session is None:
            return DeliveryDecision.DELIVERED, [
                _rule_reason(
                    "focus.inactive", "no focus session active; item is surfaced",
                    self._policy_version,
                )
            ]
        sender = (sender_email or "").strip().lower()
        if sender and sender in {s.strip().lower() for s in session.sender_overrides}:
            return DeliveryDecision.DELIVERED, [
                _rule_reason(
                    "focus.sender_exception",
                    "exact sender address is an interrupt exception of the active "
                    f"focus session {session.id}",
                    self._policy_version,
                )
            ]
        if urgent or _PRIORITY_RANK[priority] >= _PRIORITY_RANK[session.threshold]:
            return DeliveryDecision.DELIVERED, [
                _rule_reason(
                    "focus.threshold_met",
                    f"priority {priority.value} meets the active focus threshold "
                    f"{session.threshold.value}"
                    + (" (urgent)" if urgent else ""),
                    self._policy_version,
                )
            ]
        return DeliveryDecision.DEFERRED, [
            _rule_reason(
                "focus.deferred_below_threshold",
                f"priority {priority.value} is below the active focus threshold "
                f"{session.threshold.value}; persisted and deferred until the "
                "session ends",
                self._policy_version,
            )
        ]

    # ------------------------------------------------------------------ #
    def start(self, args: FocusStartArguments, *, now: datetime) -> FocusSession:
        if self._repo.get_active(now) is not None:
            raise FocusConflictError(
                "focus_already_active", "a focus session is already active"
            )
        session = FocusSession(
            id=f"focus-{uuid.uuid4()}",
            starts_at=now,
            ends_at=now + timedelta(minutes=args.duration_minutes),
            threshold=args.threshold,
            sender_overrides=[s.strip().lower() for s in args.sender_overrides],
            policy_version=self._policy_version,
        )
        self._repo.start(session)
        _emit_focus_event(self._outbox, "focus_started", session=session, now=now)
        return session

    def stop(self, *, now: datetime) -> tuple[FocusSession, FocusCompletionSummary]:
        session = self._repo.get_active(now)
        if session is None:
            raise FocusConflictError(
                "focus_not_active", "no active focus session to stop"
            )
        self._repo.stop(session.id, stopped_at=now)
        summary = self.compute_summary(session, end_at=now + timedelta(microseconds=1))
        self._repo.put_summary(summary)
        stopped = self._repo.get(session.id) or session.model_copy(
            update={"stopped_at": now}
        )
        _emit_focus_event(
            self._outbox, "focus_ended", session=stopped, summary=summary, now=now
        )
        return stopped, summary

    # ------------------------------------------------------------------ #
    def compute_summary(
        self, session: FocusSession, *, end_at: datetime
    ) -> FocusCompletionSummary:
        """Counts from REAL deduplicated rows in [starts_at, end_at)."""
        end = min(end_at, session.ends_at + timedelta(microseconds=1))
        items = self._attention_repo.list_received_between(session.starts_at, end)
        deferred = sum(1 for i in items if i.delivery is DeliveryDecision.DEFERRED)
        decisions = sum(1 for i in items if i.attention_type is AttentionType.DECISION_REQUIRED)
        actions = sum(1 for i in items if i.attention_type is AttentionType.ACTION_REQUIRED)
        fyi = len(items) - decisions - actions  # FYI/URGENT types complete the split
        return FocusCompletionSummary(
            focus_session_id=session.id,
            ended_at=end,
            total_received=len(items),
            deferred_count=deferred,
            decision_count=decisions,
            action_count=actions,
            fyi_count=max(0, fyi),
            attention_item_ids=[i.id for i in items],
        )

    def summary_for(self, session_id: str, *, now: datetime) -> FocusCompletionSummary:
        """Stored summary when present (stable across reload); otherwise the
        same computation from persisted rows for a naturally expired session."""
        stored = self._repo.get_summary(session_id)
        if stored is not None:
            return stored
        session = self._repo.get(session_id)
        if session is None:
            raise KeyError(f"unknown focus session {session_id!r}")
        end = min(now, session.ends_at + timedelta(microseconds=1))
        summary = self.compute_summary(session, end_at=end)
        # Persist once the session can no longer grow so later reads are stable.
        if not session.is_active(now):
            self._repo.put_summary(summary)
        return summary


def _emit_focus_event(outbox, kind: str, **payload_models) -> None:
    """Best-effort live-event append; a failed outbox write must never undo a
    durable focus state change (REST snapshots stay authoritative)."""
    if outbox is None:
        return
    try:
        from ..contracts.domain import (
            EventEnvelope,
            FocusEndedPayload,
            FocusStartedPayload,
        )

        now = payload_models.pop("now")
        session = payload_models["session"]
        if kind == "focus_started":
            payload = FocusStartedPayload(session=session)
        else:
            payload = FocusEndedPayload(session=session, summary=payload_models["summary"])
        outbox.append(
            EventEnvelope(
                event_id=f"evt-{uuid.uuid4()}",
                sequence=0,
                session_id=SYSTEM_EVENT_SESSION,
                occurred_at=now,
                type=payload.type,
                payload=payload,
            )
        )
    except Exception as exc:  # sanitized; delivery hardening is A07
        logger.error("focus live-event append failed (%s)", type(exc).__name__)
