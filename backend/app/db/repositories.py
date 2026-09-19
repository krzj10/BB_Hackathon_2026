"""Canonical repositories over SQLite (A01).

Design rules enforced here:

- Every method opens its own short transaction; nothing external happens
  inside it. Later guarded execution (A04) and recovery (A07) must perform
  network work strictly before/after these calls, never from within them.
- Read APIs return frozen Pydantic contract values (or immutable named tuples),
  never mutable persistence rows; callers cannot flush state back.
- There is no generic ``update_action(**fields)``: security-relevant columns
  change only through the specific operations below, and status changes only
  via controlled transitions.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import NamedTuple

from app.contracts import domain
from app.db.session import Database, from_db, to_db

# Controlled action-status transition table (frozen lifecycle).
_ALLOWED_TRANSITIONS: dict[domain.ProposedActionStatus, frozenset[domain.ProposedActionStatus]] = {
    domain.ProposedActionStatus.PENDING: frozenset(
        {
            domain.ProposedActionStatus.APPROVED,
            domain.ProposedActionStatus.REJECTED,
            domain.ProposedActionStatus.EXPIRED,
            domain.ProposedActionStatus.SUPERSEDED,
        }
    ),
    domain.ProposedActionStatus.APPROVED: frozenset(
        {
            domain.ProposedActionStatus.EXECUTING,
            domain.ProposedActionStatus.REJECTED,
            domain.ProposedActionStatus.EXPIRED,
            domain.ProposedActionStatus.SUPERSEDED,
        }
    ),
    domain.ProposedActionStatus.EXECUTING: frozenset(
        {
            domain.ProposedActionStatus.SUCCEEDED,
            domain.ProposedActionStatus.FAILED,
            domain.ProposedActionStatus.UNKNOWN,
        }
    ),
}

_ATTEMPT_OUTCOMES = frozenset({"succeeded", "failed", "unknown"})


class AttemptRecord(NamedTuple):
    """Immutable view of one execution attempt (action lifecycle stays in
    ProposedAction; attempts are a separate durable lifecycle)."""

    id: int
    action_id: str
    revision: int
    started_at: datetime
    finished_at: datetime | None
    outcome: str | None
    detail: str | None


class SessionRecord(NamedTuple):
    id: str
    created_at: datetime
    updated_at: datetime


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _row_to_action(row: sqlite3.Row) -> domain.ProposedAction:
    """Rebuild the canonical model; validation runs on every read."""
    return domain.ProposedAction.model_validate(
        {
            "id": row["id"],
            "session_id": row["session_id"],
            "request_id": row["request_id"],
            "revision": row["revision"],
            "tool": row["tool"],
            "arguments": json.loads(row["arguments_json"]),
            "arguments_digest": row["arguments_digest"],
            "summary": row["summary"],
            "reason": row["reason"],
            "impact": row["impact"],
            "before": json.loads(row["before_json"]) if row["before_json"] else None,
            "after": json.loads(row["after_json"]) if row["after_json"] else None,
            "resource_version": row["resource_version"],
            "policy_version": row["policy_version"],
            "risk": row["risk"],
            "requires_approval": bool(row["requires_approval"]),
            "voice_approval_allowed": bool(row["voice_approval_allowed"]),
            "created_at": from_db(row["created_at"]),
            "expires_at": from_db(row["expires_at"]),
            "status": row["status"],
        }
    )


class ActionRepository:
    """Durable ProposedAction state plus receipts and execution attempts."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # -- persistence -------------------------------------------------------

    def create_action(self, action: domain.ProposedAction) -> None:
        """Store a new immutable-by-revision proposal. Raises IntegrityError on
        duplicate id."""
        with self._db.transaction() as conn:
            # Canonical parent placeholder: the action's session reference is
            # authoritative; richer session metadata goes to SessionRepository.
            conn.execute(
                "INSERT INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (action.session_id, to_db(action.created_at), to_db(action.created_at)),
            )
            conn.execute(
                """
                INSERT INTO proposed_actions (
                    id, session_id, request_id, revision, tool, arguments_json,
                    arguments_digest, summary, reason, impact, before_json, after_json,
                    resource_version, policy_version, risk, requires_approval,
                    voice_approval_allowed, created_at, expires_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action.id,
                    action.session_id,
                    action.request_id,
                    action.revision,
                    action.tool,
                    _dumps(action.arguments),
                    action.arguments_digest,
                    action.summary,
                    action.reason,
                    action.impact,
                    _dumps(action.before) if action.before is not None else None,
                    _dumps(action.after) if action.after is not None else None,
                    action.resource_version,
                    action.policy_version,
                    action.risk.value,
                    int(action.requires_approval),
                    int(action.voice_approval_allowed),
                    to_db(action.created_at),
                    to_db(action.expires_at),
                    action.status.value,
                ),
            )

    def get_action(self, action_id: str) -> domain.ProposedAction | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM proposed_actions WHERE id = ?", (action_id,)
            ).fetchone()
        return _row_to_action(row) if row is not None else None

    # -- controlled transitions ---------------------------------------------

    def claim_action(
        self,
        action_id: str,
        expected_status: domain.ProposedActionStatus,
        new_status: domain.ProposedActionStatus,
        *,
        now: datetime,
    ) -> bool:
        """Atomic conditional claim: exactly one concurrent caller can move an
        unexpired action out of ``expected_status``.

        Implemented as a single ``UPDATE ... WHERE id = ? AND status = ? AND
        expires_at > ?`` inside a write transaction; success is decided by the
        affected-row count, never by a prior SELECT. Expired proposals can
        never be claimed: expiry participates in the same atomic predicate.
        """
        if new_status not in _ALLOWED_TRANSITIONS.get(expected_status, frozenset()):
            raise ValueError(
                f"illegal action transition {expected_status.value} -> {new_status.value}"
            )
        with self._db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE proposed_actions
                   SET status = ?
                 WHERE id = ? AND status = ? AND expires_at > ?
                """,
                (new_status.value, action_id, expected_status.value, to_db(now)),
            )
            return cur.rowcount == 1

    # -- approval receipts ----------------------------------------------------

    def record_receipt(self, receipt: domain.ApprovalReceipt) -> None:
        """Persist a server-issued approval receipt. Raises IntegrityError if
        the action revision already has one (duplicate approvals are not a
        second authorization)."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO approval_receipts (
                    id, action_id, revision, arguments_digest, channel,
                    approved_at, policy_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.id,
                    receipt.action_id,
                    receipt.revision,
                    receipt.arguments_digest,
                    receipt.channel.value,
                    to_db(receipt.approved_at),
                    receipt.policy_version,
                ),
            )

    def get_receipt(self, action_id: str, revision: int) -> domain.ApprovalReceipt | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM approval_receipts WHERE action_id = ? AND revision = ?",
                (action_id, revision),
            ).fetchone()
        if row is None:
            return None
        return domain.ApprovalReceipt.model_validate(
            {
                "id": row["id"],
                "action_id": row["action_id"],
                "revision": row["revision"],
                "arguments_digest": row["arguments_digest"],
                "channel": row["channel"],
                "approved_at": from_db(row["approved_at"]),
                "policy_version": row["policy_version"],
            }
        )

    # -- execution attempts ----------------------------------------------------

    def start_attempt(
        self, action_id: str, revision: int, *, started_at: datetime
    ) -> int:
        """Record the start of an execution attempt. Raises IntegrityError if
        an attempt for this action revision is still in flight (duplicate
        execution blocked at the storage boundary)."""
        with self._db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO execution_attempts (action_id, revision, started_at)"
                " VALUES (?, ?, ?)",
                (action_id, revision, to_db(started_at)),
            )
            return int(cur.lastrowid)

    def finish_attempt(
        self, attempt_id: int, *, outcome: str, finished_at: datetime, detail: str | None = None
    ) -> None:
        """Close an in-flight attempt with a known outcome. ``unknown`` is the
        storage slot for later A07 reconciliation - no reconciliation behavior
        lives here."""
        if outcome not in _ATTEMPT_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(_ATTEMPT_OUTCOMES)}")
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE execution_attempts SET outcome = ?, finished_at = ?, detail = ?"
                " WHERE id = ? AND finished_at IS NULL",
                (outcome, to_db(finished_at), detail, attempt_id),
            )
            if cur.rowcount != 1:
                raise ValueError(f"attempt {attempt_id} is not an active attempt")

    def list_attempts(self, action_id: str) -> list[AttemptRecord]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_attempts WHERE action_id = ? ORDER BY id",
                (action_id,),
            ).fetchall()
        return [
            AttemptRecord(
                id=row["id"],
                action_id=row["action_id"],
                revision=row["revision"],
                started_at=from_db(row["started_at"]),
                finished_at=from_db(row["finished_at"]) if row["finished_at"] else None,
                outcome=row["outcome"],
                detail=row["detail"],
            )
            for row in rows
        ]


class SessionRepository:
    """Minimal durable session records (conversation-memory/RAG is out of scope)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, session_id: str, *, now: datetime) -> SessionRecord:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?)",
                (session_id, to_db(now), to_db(now)),
            )
        return SessionRecord(id=session_id, created_at=now, updated_at=now)

    def get(self, session_id: str) -> SessionRecord | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return SessionRecord(
            id=row["id"], created_at=from_db(row["created_at"]), updated_at=from_db(row["updated_at"])
        )


class AttentionRepository:
    """Canonical storage boundary for Attention items (A06 owns the pipeline).

    ``add`` returns False on an already-seen (source, source_id) pair: the DB
    UNIQUE constraint is the dedup authority.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, item: domain.AttentionItem) -> bool:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO attention_items (id, source, source_id, received_at, payload_json)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (item.id, item.source.value, item.source_id, to_db(item.received_at),
                     item.model_dump_json()),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def get(self, item_id: str) -> domain.AttentionItem | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM attention_items WHERE id = ?", (item_id,)
            ).fetchone()
        return domain.AttentionItem.model_validate_json(row["payload_json"]) if row else None


class DecisionRepository:
    """Canonical storage boundary for Decisions (B04 owns the inbox flow).

    UNIQUE(attention_item_id) enforces one Decision per Attention item; the FK
    rejects decisions without their originating Attention item. Both surface
    as IntegrityError - callers must not paper over integrity violations here.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, decision: domain.Decision) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO decisions (id, attention_item_id, status, proposed_action_id,"
                " payload_json) VALUES (?, ?, ?, ?, ?)",
                (decision.id, decision.attention_item_id, decision.status.value,
                 decision.proposed_action_id, decision.model_dump_json()),
            )

    def get(self, decision_id: str) -> domain.Decision | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        return domain.Decision.model_validate_json(row["payload_json"]) if row else None


class FocusRepository:
    """Canonical storage boundary for focus sessions and completion summaries
    (B04 owns the user-facing flow; 'active' stays derived from timestamps)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def start(self, session: domain.FocusSession) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO focus_sessions (id, starts_at, ends_at, stopped_at, payload_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (session.id, to_db(session.starts_at), to_db(session.ends_at),
                 to_db(session.stopped_at) if session.stopped_at else None,
                 session.model_dump_json()),
            )

    def stop(self, session_id: str, *, stopped_at: datetime) -> None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT payload_json FROM focus_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown focus session {session_id!r}")
            updated = domain.FocusSession.model_validate_json(row["payload_json"]).model_copy(
                update={"stopped_at": stopped_at}
            )
            conn.execute(
                "UPDATE focus_sessions SET stopped_at = ?, payload_json = ? WHERE id = ?",
                (to_db(stopped_at), updated.model_dump_json(), session_id),
            )

    def get(self, session_id: str) -> domain.FocusSession | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM focus_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return domain.FocusSession.model_validate_json(row["payload_json"]) if row else None

    def put_summary(self, summary: domain.FocusCompletionSummary) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO focus_completion_summaries"
                " (focus_session_id, ended_at, payload_json) VALUES (?, ?, ?)",
                (summary.focus_session_id, to_db(summary.ended_at), summary.model_dump_json()),
            )

    def get_summary(self, session_id: str) -> domain.FocusCompletionSummary | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM focus_completion_summaries WHERE focus_session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return domain.FocusCompletionSummary.model_validate_json(row["payload_json"])


class CursorRepository:
    """Poll cursors and the seen-source ledger (A06 owns polling behavior)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, source: str) -> str | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT high_water_mark FROM ingestion_cursors WHERE source = ?", (source,)
            ).fetchone()
        return row["high_water_mark"] if row else None

    def set(self, source: str, high_water_mark: str, *, now: datetime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO ingestion_cursors (source, high_water_mark, updated_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(source) DO UPDATE SET high_water_mark = excluded.high_water_mark,"
                " updated_at = excluded.updated_at",
                (source, high_water_mark, to_db(now)),
            )

    def mark_seen(self, source: str, source_id: str, *, now: datetime) -> bool:
        """True when this is the first sighting of (source, source_id)."""
        with self._db.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO seen_sources (source, source_id, first_seen_at)"
                " VALUES (?, ?, ?)",
                (source, source_id, to_db(now)),
            )
            return cur.rowcount == 1


class OutboxRepository:
    """Durable event-outbox storage boundary only.

    A01 provides ordered, deduplicated persistence of canonical EventEnvelopes.
    Delivery, replay, consumption marking and crash reconciliation are A07 -
    nothing here schedules or performs any dispatch.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, envelope: domain.EventEnvelope) -> int:
        """Persist one event; returns its monotonic sequence number. Raises
        IntegrityError on duplicate event_id (producers deduplicate by id)."""
        with self._db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO event_outbox (event_id, session_id, request_id, occurred_at,"
                " schema_version, type, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (envelope.event_id, envelope.session_id, envelope.request_id,
                 to_db(envelope.occurred_at), envelope.schema_version, envelope.type.value,
                 envelope.model_dump_json()),
            )
            return int(cur.lastrowid)

    def next_pending(self, *, limit: int = 50) -> list[domain.EventEnvelope]:
        """Read view of stored events in durable append order (no consumption)."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM event_outbox ORDER BY seq ASC LIMIT ?", (limit,)
            ).fetchall()
        return [domain.EventEnvelope.model_validate_json(r["payload_json"]) for r in rows]
