"""A01 integration tests - durable SQLite state and safety constraints.

Uses real file-backed SQLite databases (tmp_path), never :memory: for reopen
coverage, and never the runtime eva.db. Concurrency is deterministic via a
threading barrier - no sleep-based synchronization.

Run: python -m pytest backend/tests/integration/test_repositories.py -q
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import domain
from app.db.repositories import (
    ActionRepository,
    AttentionRepository,
    CursorRepository,
    DecisionRepository,
    FocusRepository,
    OutboxRepository,
    SessionRepository,
)
from app.db.schema import SCHEMA_VERSION, SchemaVersionError, init_schema
from app.db.session import Database

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "contracts" / "fixtures"

NOW = datetime.fromisoformat("2026-09-21T12:02:00+02:00")


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'a01-test.db'}")
    init_schema(database)
    yield database


def fresh_action(**overrides) -> domain.ProposedAction:
    data = {**load_fixture("proposed_action_agenda_high.json"), **overrides}
    return domain.ProposedAction.model_validate(data)


# ---------------------------------------------------------------------------
# Basic initialization / schema metadata
# ---------------------------------------------------------------------------


def test_new_database_initializes_with_schema_version(tmp_path) -> None:
    path = tmp_path / "init.db"
    database = Database(f"sqlite:///{path}")
    init_schema(database)
    with database.connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert row is not None and int(row[0]) == SCHEMA_VERSION


def test_reopen_compatible_schema_succeeds(tmp_path) -> None:
    path = tmp_path / "reopen-schema.db"
    init_schema(Database(f"sqlite:///{path}"))
    reopened = Database(f"sqlite:///{path}")
    init_schema(reopened)  # must not raise or recreate
    with reopened.connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert int(row[0]) == SCHEMA_VERSION


def test_incompatible_schema_version_is_refused_and_data_preserved(tmp_path) -> None:
    path = tmp_path / "incompat.db"
    database = Database(f"sqlite:///{path}")
    init_schema(database)
    with database.transaction() as conn:
        conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
    hostile = Database(f"sqlite:///{path}")
    with pytest.raises(SchemaVersionError):
        init_schema(hostile)
    # Refusal must not destroy anything.
    with hostile.connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert row[0] == "999"


# ---------------------------------------------------------------------------
# Action persistence
# ---------------------------------------------------------------------------


def test_action_roundtrip_is_canonical_and_expiry_preserved(db) -> None:
    action = fresh_action()
    repo = ActionRepository(db)
    repo.create_action(action)
    stored = repo.get_action(action.id)
    assert stored == action  # canonical Pydantic equality, incl. expires_at
    assert stored.expires_at == action.expires_at


def test_get_missing_action_returns_none(db) -> None:
    assert ActionRepository(db).get_action("nope") is None


def test_duplicate_action_id_rejected(db) -> None:
    repo = ActionRepository(db)
    action = fresh_action()
    repo.create_action(action)
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_action(action)


# ---------------------------------------------------------------------------
# Reopen persistence (real file, closed resources, fresh objects)
# ---------------------------------------------------------------------------


def test_durable_state_survives_close_and_reopen(tmp_path) -> None:
    path = tmp_path / "reopen.db"
    database = Database(f"sqlite:///{path}")
    init_schema(database)
    action = fresh_action()
    receipt = domain.ApprovalReceipt.model_validate(load_fixture("approval_receipt_ui.json"))
    ActionRepository(database).create_action(action)
    ActionRepository(database).record_receipt(receipt)
    OutboxRepository(database).append(
        domain.EventEnvelope.model_validate(load_fixture("event_envelope_attention_created.json"))
    )
    del database  # release all resources for this file

    reopened = Database(f"sqlite:///{path}")
    init_schema(reopened)
    assert ActionRepository(reopened).get_action(action.id) == action
    stored_receipt = ActionRepository(reopened).get_receipt(receipt.action_id, receipt.revision)
    assert stored_receipt == receipt
    pending = OutboxRepository(reopened).next_pending(limit=10)
    assert len(pending) == 1


# ---------------------------------------------------------------------------
# Atomic claim_action
# ---------------------------------------------------------------------------


def test_claim_execution_from_approved_wins(db) -> None:
    action = fresh_action()
    repo = ActionRepository(db)
    repo.create_action(action)
    assert repo.record_approval(action.id, now=NOW) is True
    assert repo.claim_action(action.id, now=NOW) is True
    assert repo.get_action(action.id).status == domain.ProposedActionStatus.EXECUTING


def test_claim_rejected_while_not_approved(db) -> None:
    action = fresh_action()
    repo = ActionRepository(db)
    repo.create_action(action)
    # Still PENDING: the APPROVED-conditional execution claim loses.
    assert repo.claim_action(action.id, now=NOW) is False
    assert repo.get_action(action.id).status == domain.ProposedActionStatus.PENDING


def test_claim_action_refuses_non_execution_transitions(db) -> None:
    action = fresh_action()
    repo = ActionRepository(db)
    repo.create_action(action)
    with pytest.raises(ValueError):
        repo.claim_action(action.id, domain.ProposedActionStatus.PENDING,
                          domain.ProposedActionStatus.APPROVED, now=NOW)
    with pytest.raises(ValueError):
        repo.claim_action(action.id, domain.ProposedActionStatus.PENDING,
                          domain.ProposedActionStatus.SUCCEEDED, now=NOW)


def test_finish_execution_rejects_non_final_status(db) -> None:
    action = fresh_action()
    repo = ActionRepository(db)
    repo.create_action(action)
    with pytest.raises(ValueError):
        repo.finish_execution(action.id, domain.ProposedActionStatus.APPROVED)


def test_record_approval_rejects_expired_proposal(db) -> None:
    action = fresh_action()  # expires 2026-09-21T12:05+02:00
    repo = ActionRepository(db)
    repo.create_action(action)
    later = datetime.fromisoformat("2026-09-21T13:00:00+02:00")
    assert repo.record_approval(action.id, now=later) is False
    assert repo.get_action(action.id).status == domain.ProposedActionStatus.PENDING


def test_expire_action_uses_expiry_boundary(db) -> None:
    action = fresh_action()
    repo = ActionRepository(db)
    repo.create_action(action)
    before = datetime.fromisoformat("2026-09-21T12:04:00+02:00")
    assert repo.expire_action(action.id, now=before) is False  # not expired yet
    after = datetime.fromisoformat("2026-09-21T13:00:00+02:00")
    assert repo.expire_action(action.id, now=after) is True
    assert repo.get_action(action.id).status == domain.ProposedActionStatus.EXPIRED


def test_concurrent_execution_claims_yield_exactly_one_winner(tmp_path) -> None:
    path = tmp_path / "claim-race.db"
    database = Database(f"sqlite:///{path}")
    init_schema(database)
    action = fresh_action()
    repo = ActionRepository(database)
    repo.create_action(action)
    assert repo.record_approval(action.id, now=NOW) is True
    del database

    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def attempt_claim(index: int) -> None:
        racer_db = Database(f"sqlite:///{path}")
        repo = ActionRepository(racer_db)
        barrier.wait()  # deterministic simultaneous start, no sleeps
        won = repo.claim_action(
            action.id,
            domain.ProposedActionStatus.APPROVED,
            domain.ProposedActionStatus.EXECUTING,
            now=NOW,
        )
        with lock:
            results.append(won)

    threads = [threading.Thread(target=attempt_claim, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results) == [False, True], f"expected exactly one winner, got {results}"
    final = ActionRepository(Database(f"sqlite:///{path}")).get_action(action.id)
    assert final.status == domain.ProposedActionStatus.EXECUTING


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_expired_action_cannot_begin_execution(db) -> None:
    action = fresh_action()  # fixture expires 2026-09-21T12:05+02:00
    repo = ActionRepository(db)
    repo.create_action(action)
    assert repo.record_approval(action.id, now=NOW) is True
    later = datetime.fromisoformat("2026-09-21T13:00:00+02:00")
    assert repo.claim_action(action.id, now=later) is False
    assert repo.get_action(action.id).status == domain.ProposedActionStatus.APPROVED


def test_claim_allowed_until_expiry(db) -> None:
    action = fresh_action()
    repo = ActionRepository(db)
    repo.create_action(action)
    assert repo.record_approval(action.id, now=NOW) is True
    just_before = datetime.fromisoformat("2026-09-21T12:04:59+02:00")
    assert repo.claim_action(action.id, now=just_before) is True


def test_outcome_of_started_execution_persists_after_expiry(db) -> None:
    """Expiry prevents STARTING an execution; it never blocks recording the
    durable result of one that already began before expiry."""
    action = fresh_action()  # expires 12:05
    repo = ActionRepository(db)
    repo.create_action(action)
    assert repo.record_approval(action.id, now=NOW) is True
    claimed_at = datetime.fromisoformat("2026-09-21T12:04:59+02:00")
    assert repo.claim_action(action.id, now=claimed_at) is True

    # Result arrives at 12:05:01 - one second after the proposal expired.
    finished = datetime.fromisoformat("2026-09-21T12:05:01+02:00")
    assert repo.finish_execution(action.id, domain.ProposedActionStatus.SUCCEEDED) is True
    stored = repo.get_action(action.id)
    assert stored.status == domain.ProposedActionStatus.SUCCEEDED

    # Only one final transition wins; a second outcome cannot overwrite it.
    assert repo.finish_execution(action.id, domain.ProposedActionStatus.FAILED) is False
    assert repo.get_action(action.id).status == domain.ProposedActionStatus.SUCCEEDED


def test_finish_execution_requires_executing_state(db) -> None:
    action = fresh_action()
    repo = ActionRepository(db)
    repo.create_action(action)
    assert repo.finish_execution(action.id, domain.ProposedActionStatus.UNKNOWN) is False


# ---------------------------------------------------------------------------
# Rollback safety
# ---------------------------------------------------------------------------


def test_failed_transaction_leaves_no_partial_state(db) -> None:
    action = fresh_action()
    ActionRepository(db).create_action(action)
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute(
                "UPDATE proposed_actions SET status = 'approved' WHERE id = ?", (action.id,)
            )
            raise RuntimeError("simulated mid-operation failure")
    assert ActionRepository(db).get_action(action.id).status == domain.ProposedActionStatus.PENDING


def test_constraint_failure_leaves_no_partial_receipt(db) -> None:
    receipt = domain.ApprovalReceipt.model_validate(load_fixture("approval_receipt_ui.json"))
    with pytest.raises(sqlite3.IntegrityError):
        ActionRepository(db).record_receipt(receipt)  # FK: unknown action
    assert ActionRepository(db).get_receipt(receipt.action_id, receipt.revision) is None


# ---------------------------------------------------------------------------
# Constraints (FK + uniqueness)
# ---------------------------------------------------------------------------


def test_high_risk_db_check_constraint_rejects_insecure_row(db) -> None:
    """Defense in depth: even a raw SQL insert cannot bypass the HIGH rule."""
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions (id, created_at, updated_at) VALUES ('s', ?, ?)",
                ("2026-09-21T10:00:00+00:00", "2026-09-21T10:00:00+00:00"),
            )
            conn.execute(
                """
                INSERT INTO proposed_actions (id, session_id, request_id, revision, tool,
                    arguments_json, arguments_digest, summary, reason, impact,
                    policy_version, risk, requires_approval, voice_approval_allowed,
                    created_at, expires_at, status)
                VALUES ('a', 's', 'r', 1, 'calendar.update_agenda', '{}', 'd', 's', 'r',
                    'i', 'policy-v1', 'high', 0, 0,
                    '2026-09-21T10:00:00+00:00', '2026-09-21T10:05:00+00:00', 'pending')
                """
            )


def test_attention_unique_source_source_id(db) -> None:
    item = domain.AttentionItem.model_validate(load_fixture("attention_finance_decision.json"))
    repo = AttentionRepository(db)
    assert repo.add(item) is True
    assert repo.add(item) is False  # deduplicated at the DB boundary
    assert repo.get(item.id) == item


def test_attention_unrelated_integrity_conflict_raises(db) -> None:
    """Only the canonical (source, source_id) dedup conflict maps to False;
    any other integrity violation must surface, not masquerade as a duplicate."""
    item = domain.AttentionItem.model_validate(load_fixture("attention_finance_decision.json"))
    repo = AttentionRepository(db)
    assert repo.add(item) is True
    clash = domain.AttentionItem.model_validate(
        {**item.model_dump(), "source_id": "msg-unrelated-clash"}
    )  # same primary id, different dedup key -> PK violation
    with pytest.raises(sqlite3.IntegrityError):
        repo.add(clash)


def test_decision_unique_attention_item_and_fk(db) -> None:
    attention = domain.AttentionItem.model_validate(
        load_fixture("attention_finance_decision.json")
    )
    AttentionRepository(db).add(attention)
    decision = domain.Decision.model_validate(load_fixture("decision_finance_pln_needs_review.json"))
    repo = DecisionRepository(db)
    repo.create(decision)
    assert repo.get(decision.id) == decision

    duplicate = decision.model_copy(update={"id": "dec-demo-finance-002"})
    with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(attention_item_id)
        repo.create(duplicate)

    orphan = decision.model_copy(update={"id": "dec-orphan", "attention_item_id": "attn-missing"})
    with pytest.raises(sqlite3.IntegrityError):  # FK to attention_items
        repo.create(orphan)


# ---------------------------------------------------------------------------
# Receipts and execution attempts
# ---------------------------------------------------------------------------


def test_duplicate_receipt_for_same_action_revision_rejected(db) -> None:
    action = fresh_action()
    receipt = domain.ApprovalReceipt.model_validate(load_fixture("approval_receipt_ui.json"))
    repo = ActionRepository(db)
    repo.create_action(action)
    repo.record_receipt(receipt)
    with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(action_id, revision)
        repo.record_receipt(receipt)


def test_attempt_lifecycle_is_distinct_from_action_lifecycle(db) -> None:
    action = fresh_action()
    repo = ActionRepository(db)
    repo.create_action(action)
    attempt_id = repo.start_attempt(action.id, action.revision, started_at=NOW)

    # Prevent accidental duplicate execution while an attempt is active.
    with pytest.raises(sqlite3.IntegrityError):
        repo.start_attempt(action.id, action.revision, started_at=NOW)

    repo.finish_attempt(attempt_id, outcome="succeeded", finished_at=NOW)
    attempts = repo.list_attempts(action.id)
    assert len(attempts) == 1
    assert attempts[0].outcome == "succeeded"
    # Action lifecycle is untouched by attempt bookkeeping.
    assert repo.get_action(action.id).status == domain.ProposedActionStatus.PENDING

    # After finishing, a new attempt may be recorded (retries after known outcomes).
    second = repo.start_attempt(action.id, action.revision, started_at=NOW)
    assert second != attempt_id


# ---------------------------------------------------------------------------
# Focus, cursors, outbox boundaries
# ---------------------------------------------------------------------------


def test_focus_session_and_summary_persistence(db) -> None:
    session = domain.FocusSession.model_validate(load_fixture("focus_session_active.json"))
    summary = domain.FocusCompletionSummary.model_validate(
        load_fixture("focus_completion_summary.json")
    )
    repo = FocusRepository(db)
    repo.start(session)
    assert repo.get(session.id) == session
    stopped_at = datetime.fromisoformat("2026-09-21T13:30:00+02:00")
    repo.stop(session.id, stopped_at=stopped_at)
    stopped = repo.get(session.id)
    assert stopped.stopped_at == stopped_at
    repo.put_summary(summary)
    assert repo.get_summary(session.id) == summary


def test_focus_stop_before_start_is_rejected_and_leaves_session_unchanged(db) -> None:
    session = domain.FocusSession.model_validate(load_fixture("focus_session_active.json"))
    repo = FocusRepository(db)
    repo.start(session)
    invalid = session.starts_at - timedelta(hours=1)  # violates stopped_at >= starts_at
    with pytest.raises(ValidationError):
        repo.stop(session.id, stopped_at=invalid)
    after = repo.get(session.id)
    assert after == session  # originally stored valid session untouched
    assert after.stopped_at is None


def test_ingestion_cursors_and_seen_sources(db) -> None:
    cursors = CursorRepository(db)
    assert cursors.get("gmail") is None
    cursors.set("gmail", "1727000000", now=NOW)
    assert cursors.get("gmail") == "1727000000"
    cursors.set("gmail", "1727000100", now=NOW)
    assert cursors.get("gmail") == "1727000100"
    assert cursors.mark_seen("gmail", "msg-001", now=NOW) is True
    assert cursors.mark_seen("gmail", "msg-001", now=NOW) is False


def test_seen_ledger_is_seen_check(db) -> None:
    """A06 durable dedup read surface."""
    cursors = CursorRepository(db)
    assert cursors.is_seen("gmail", "msg-a") is False
    cursors.mark_seen("gmail", "msg-a", now=NOW)
    assert cursors.is_seen("gmail", "msg-a") is True
    assert cursors.is_seen("gmail", "msg-b") is False
    assert cursors.is_seen("calendar", "msg-a") is False  # per-source ledger


def test_attention_list_received_between_half_open_and_stable(db) -> None:
    """A06 Focus-completion support read: [start, end), stable order."""
    repo = AttentionRepository(db)
    base = domain.AttentionItem.model_validate(load_fixture("attention_finance_decision.json"))
    t0 = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    for index in range(4):
        repo.add(
            base.model_copy(
                update={"id": f"it-{index}", "source_id": f"src-{index}",
                        "received_at": t0 + timedelta(minutes=index)}
            )
        )
    window = repo.list_received_between(t0 + timedelta(minutes=1), t0 + timedelta(minutes=3))
    assert [item.source_id for item in window] == ["src-1", "src-2"]  # end excluded
    assert repo.list_received_between(t0 + timedelta(hours=5), t0 + timedelta(hours=6)) == []


def test_outbox_append_is_ordered_deduplicated_and_durable(tmp_path) -> None:
    path = tmp_path / "outbox.db"
    database = Database(f"sqlite:///{path}")
    init_schema(database)
    repo = OutboxRepository(database)
    first = domain.EventEnvelope.model_validate(
        load_fixture("event_envelope_attention_created.json")
    )
    seq = repo.append(first)
    assert seq >= 1
    with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(event_id)
        repo.append(first)
    second = first.model_copy(update={"event_id": "evtw-demo-attention-002", "sequence": 43})
    seq2 = repo.append(second)
    assert seq2 > seq
    pending = OutboxRepository(database).next_pending(limit=10)
    assert [e.event_id for e in pending] == [first.event_id, second.event_id]


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


def test_session_persistence_and_action_link(db) -> None:
    sessions = SessionRepository(db)
    record = sessions.create("sess-demo-001", now=NOW)
    assert sessions.get("sess-demo-001").id == record.id
    action = fresh_action()
    ActionRepository(db).create_action(action)  # auto-links its session placeholder
    with db.connect() as conn:
        row = conn.execute(
            "SELECT session_id FROM proposed_actions WHERE id = ?", (action.id,)
        ).fetchone()
    assert row[0] == action.session_id


# ---------------------------------------------------------------------------
# A03 atomic approval+receipt and rejection operations
# ---------------------------------------------------------------------------

APPROVAL_NOW = datetime.fromisoformat("2026-09-21T12:02:00+02:00")  # inside fixture TTL


def bound_receipt(action, **overrides) -> domain.ApprovalReceipt:
    data = {
        "id": "rcpt-test-001",
        "action_id": action.id,
        "revision": action.revision,
        "arguments_digest": action.arguments_digest,
        "channel": "ui",
        "approved_at": APPROVAL_NOW,
        "policy_version": action.policy_version,
    }
    data.update(overrides)
    return domain.ApprovalReceipt.model_validate(data)


def seeded_action(db) -> domain.ProposedAction:
    action = fresh_action()
    ActionRepository(db).create_action(action)
    return action


def test_approve_with_receipt_commits_status_and_receipt_together(db) -> None:
    repo = ActionRepository(db)
    action = seeded_action(db)
    won = repo.approve_with_receipt(
        bound_receipt(action),
        expected_revision=action.revision,
        expected_arguments_digest=action.arguments_digest,
        expected_policy_version=action.policy_version,
        now=APPROVAL_NOW,
    )
    assert won is True
    assert repo.get_action(action.id).status is domain.ProposedActionStatus.APPROVED
    receipt = repo.get_receipt(action.id, action.revision)
    assert receipt is not None and receipt.arguments_digest == action.arguments_digest


def _assert_untouched(repo: ActionRepository, db, action) -> None:
    assert repo.get_action(action.id).status is domain.ProposedActionStatus.PENDING
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM approval_receipts WHERE action_id = ?", (action.id,)
        ).fetchone()
    assert rows["n"] == 0


def test_approve_with_receipt_digest_mismatch_changes_nothing(db) -> None:
    repo = ActionRepository(db)
    action = seeded_action(db)
    tampered_digest = "sha256:other"
    won = repo.approve_with_receipt(
        bound_receipt(action, arguments_digest=tampered_digest),
        expected_revision=action.revision,
        expected_arguments_digest=tampered_digest,
        expected_policy_version=action.policy_version,
        now=APPROVAL_NOW,
    )
    assert won is False
    _assert_untouched(repo, db, action)


def test_approve_with_receipt_policy_mismatch_changes_nothing(db) -> None:
    repo = ActionRepository(db)
    action = seeded_action(db)
    wrong_policy = "policy-v2-changed"
    won = repo.approve_with_receipt(
        bound_receipt(action, policy_version=wrong_policy),
        expected_revision=action.revision,
        expected_arguments_digest=action.arguments_digest,
        expected_policy_version=wrong_policy,  # differs from the STORED action row
        now=APPROVAL_NOW,
    )
    assert won is False
    _assert_untouched(repo, db, action)


def test_approve_with_receipt_revision_mismatch_changes_nothing(db) -> None:
    repo = ActionRepository(db)
    action = seeded_action(db)
    wrong_revision = action.revision + 1
    won = repo.approve_with_receipt(
        bound_receipt(action, revision=wrong_revision),
        expected_revision=wrong_revision,
        expected_arguments_digest=action.arguments_digest,
        expected_policy_version=action.policy_version,
        now=APPROVAL_NOW,
    )
    assert won is False
    _assert_untouched(repo, db, action)


def test_approve_with_receipt_inconsistent_binding_is_programming_error(db) -> None:
    repo = ActionRepository(db)
    action = seeded_action(db)
    with pytest.raises(ValueError):
        # A receipt whose own binding disagrees with the expected tuple can
        # never be submitted - that is a caller bug, not an approval outcome.
        repo.approve_with_receipt(
            bound_receipt(action),
            expected_revision=action.revision + 5,
            expected_arguments_digest=action.arguments_digest,
            expected_policy_version=action.policy_version,
            now=APPROVAL_NOW,
        )


def test_approve_with_receipt_after_expiry_changes_nothing(db) -> None:
    repo = ActionRepository(db)
    action = seeded_action(db)
    late = datetime.fromisoformat("2026-09-21T12:05:00+02:00")  # >= expires_at
    won = repo.approve_with_receipt(
        bound_receipt(action),
        expected_revision=action.revision,
        expected_arguments_digest=action.arguments_digest,
        expected_policy_version=action.policy_version,
        now=late,
    )
    assert won is False
    _assert_untouched(repo, db, action)


def test_reject_action_is_final(db) -> None:
    repo = ActionRepository(db)
    action = seeded_action(db)
    won = repo.reject_action(
        action.id,
        expected_revision=action.revision,
        expected_arguments_digest=action.arguments_digest,
    )
    assert won is True
    assert repo.get_action(action.id).status is domain.ProposedActionStatus.REJECTED
    # A rejected action can never become approved afterwards.
    later = repo.approve_with_receipt(
        bound_receipt(action),
        expected_revision=action.revision,
        expected_arguments_digest=action.arguments_digest,
        expected_policy_version=action.policy_version,
        now=APPROVAL_NOW,
    )
    assert later is False
    assert repo.get_action(action.id).status is domain.ProposedActionStatus.REJECTED


def test_concurrent_approve_with_receipt_has_exactly_one_winner(db) -> None:
    action = seeded_action(db)
    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def attempt(index: int) -> None:
        repo = ActionRepository(db)  # independent repository, same DB file
        receipt = bound_receipt(action, id=f"rcpt-race-{index}")
        barrier.wait()
        won = repo.approve_with_receipt(
            receipt,
            expected_revision=action.revision,
            expected_arguments_digest=action.arguments_digest,
            expected_policy_version=action.policy_version,
            now=APPROVAL_NOW,
        )
        with lock:
            results.append(won)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == [False, True]
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM approval_receipts WHERE action_id = ?", (action.id,)
        ).fetchone()
    assert rows["n"] == 1  # exactly one receipt for exactly one approval


def test_approve_with_receipt_policy_version_guard_is_programming_error(db) -> None:
    repo = ActionRepository(db)
    action = seeded_action(db)
    with pytest.raises(ValueError):
        # A receipt declaring a different policy version than the approved
        # expected version can never be submitted (repository invariant).
        repo.approve_with_receipt(
            bound_receipt(action, policy_version="policy-v9-mismatched"),
            expected_revision=action.revision,
            expected_arguments_digest=action.arguments_digest,
            expected_policy_version=action.policy_version,
            now=APPROVAL_NOW,
        )
    assert repo.get_action(action.id).status is domain.ProposedActionStatus.PENDING


def test_approve_with_receipt_rolls_back_when_insert_fails(db) -> None:
    """Atomicity: if the receipt INSERT fails after the conditional UPDATE
    matched (here via a deliberate PRIMARY KEY collision), the whole
    transaction rolls back - no partial APPROVED-without-receipt state."""
    repo = ActionRepository(db)
    first = seeded_action(db)
    assert repo.approve_with_receipt(
        bound_receipt(first, id="rcpt-clash"),
        expected_revision=first.revision,
        expected_arguments_digest=first.arguments_digest,
        expected_policy_version=first.policy_version,
        now=APPROVAL_NOW,
    ) is True

    second = fresh_action(id="act-demo-rollback-second-002")
    repo.create_action(second)
    clashing = bound_receipt(second, id="rcpt-clash")  # same receipt PK -> INSERT fails
    with pytest.raises(sqlite3.IntegrityError):
        repo.approve_with_receipt(
            clashing,
            expected_revision=second.revision,
            expected_arguments_digest=second.arguments_digest,
            expected_policy_version=second.policy_version,
            now=APPROVAL_NOW,
        )
    # The action UPDATE rolled back with the failed INSERT: still PENDING.
    assert repo.get_action(second.id).status is domain.ProposedActionStatus.PENDING
    assert repo.get_receipt(second.id, second.revision) is None
