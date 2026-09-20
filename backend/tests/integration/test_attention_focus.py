"""B04 integration: Attention pipeline -> Focus delivery -> Decision Inbox.

Runs the REAL composition (create_app + real AttentionEngine, FocusService,
DecisionService, repositories, executor) against a hermetic FakeGmail and a
temp SQLite file. Only external boundaries are faked; no network, no models.

Covers the plan's B04 guarantees end-to-end: deterministic floors create
Decisions atomically; focus defers delivery only; summaries come from stored
rows; HIGH financial accept/reject travels the guarded proposal -> UI-confirm
path and records an internal outcome with zero external execution.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.approvals.policy import load_policy
from app.attention.engine import AmbiguitySuggestion, AttentionEngine
from app.attention.focus import FocusService
from app.attention.rules import AttentionRules
from app.config import Settings
from app.contracts.domain import (
    ActionRisk,
    AttentionItem,
    AttentionPriority,
    AttentionType,
    DecisionStatus,
    DeliveryDecision,
    FocusSession,
    NormalizedSourceEvent,
    RetrievalStatus,
    SourceKind,
    SourceRef,
    SourceSystem,
)
from app.db.repositories import (
    AttentionRepository,
    CursorRepository,
    DecisionRepository,
    FocusRepository,
    OutboxRepository,
)
from app.db.schema import init_schema
from app.db.session import Database
from app.google.gmail import ThreadEvidence, ThreadSummary
from app.main import create_app

POLICY = load_policy()
T0 = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)

SESSION = {"X-EVA-Session-ID": "sess-b04"}


def _headers(request_id: str) -> dict[str, str]:
    return {**SESSION, "X-EVA-Request-ID": request_id}


def make_event(
    source_id: str,
    *,
    body: str = "Hello there, just a friendly note.",
    subject: str = "Update",
    sender: str = "someone@example.com",
    received_at: datetime | None = None,
) -> NormalizedSourceEvent:
    # Wall-clock-relative stamps, truncated to whole seconds exactly like the
    # Gmail internalDate epoch (the real A06 window compares int(seconds)).
    received = received_at or datetime.now(timezone.utc).replace(microsecond=0)
    return NormalizedSourceEvent(
        source=SourceSystem.GMAIL,
        source_id=source_id,
        sender_email=sender,
        subject=subject,
        body=body,
        received_at=received,
        sources=[
            SourceRef(
                id=f"gmail:{source_id}", kind=SourceKind.GMAIL_MESSAGE,
                resource_id=source_id, title=subject, retrieved_at=received,
            )
        ],
    )


FINANCIAL_EVENT = dict(
    body="Please approve the invoice for 2500 PLN by Friday.",
    subject="Invoice approval needed",
    sender="vendor@supplier.example",
)


class FakeGmail:
    """ThreadSource double with Gmail window semantics (same contract A06's
    own suite pins)."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, NormalizedSourceEvent, datetime]] = []

    def add(self, thread_id: str, *events: NormalizedSourceEvent) -> None:
        for event in events:
            self.entries.append((thread_id, event, event.received_at))

    def _matching_threads(self, query: str) -> dict[str, list[str]]:
        after = re.search(r"after:(\d+)", query)
        before = re.search(r"before:(\d+)", query)
        lo = int(after.group(1)) if after else None
        hi = int(before.group(1)) if before else None
        threads: dict[str, list[str]] = {}
        for thread_id, event, internal_at in self.entries:
            stamp = internal_at.timestamp()
            if lo is not None and stamp < lo:
                continue
            if hi is not None and stamp > hi:
                continue
            threads.setdefault(thread_id, []).append(event.source_id)
        return threads

    def search_window(self, query, *, max_threads=100, max_pages=8):
        summaries = [
            ThreadSummary(thread_id=tid, message_ids=tuple(ids), subject=None, snippet=None)
            for tid, ids in self._matching_threads(query).items()
        ]
        return summaries, RetrievalStatus.COMPLETE, []

    def search(self, query, limit=25, **kwargs):
        return self.search_window(query)[:3]  # (summaries, status, notes)

    def get_thread(self, thread_id):
        messages = [e for tid, e, _ in self.entries if tid == thread_id]
        internal = {e.source_id: ts for tid, e, ts in self.entries if tid == thread_id}
        return ThreadEvidence(
            thread_id=thread_id, messages=messages,
            retrieval_status=RetrievalStatus.COMPLETE,
            internal_dates=internal,
        )


class Env(SimpleNamespace):
    def check_now(self):
        return self.client.post("/api/attention/check-now", headers=_headers("check-1"))

    def new_check_now(self, request_id: str = "check-x"):
        return self.client.post("/api/attention/check-now", headers=_headers(request_id))


def make_env(tmp_path, *, important=("cfo@example.com",)) -> Env:
    from app.attention.ingest import GmailIngestionService

    settings = Settings(eva_db_url=f"sqlite:///{tmp_path / 'b04.db'}")
    app = create_app(settings)
    fake = FakeGmail()
    # Rebuild rules+engine with the test's exact important-sender set (the ORG
    # handoff is empty in production composition); everything else stays the
    # real wired singletons from the composition root.
    rules = AttentionRules(policy=POLICY, important_senders=set(important))
    engine = AttentionEngine(
        rules=rules,
        policy_version=POLICY.version,
        focus_service=app.state.focus_service,
        clock=lambda: datetime.now(timezone.utc),
    )
    app.state.attention_engine = engine
    # Replace ONLY the Gmail boundary; focus/decisions/executor remain real.
    app.state.ingestion_service = GmailIngestionService(
        gmail_source_factory=lambda: fake,
        cursors=app.state.cursor_repository,
        attention_repo=app.state.attention_repository,
        sink=engine,
        query="in:inbox",
        overlap_seconds=120,
        clock=lambda: datetime.now(timezone.utc),
        decision_projection=engine.decision_projection,
    )
    client = TestClient(app)
    return Env(
        settings=settings, app=app, client=client, fake=fake, tmp=tmp_path,
        attention_repo=AttentionRepository(app.state.db),
        decision_repo=DecisionRepository(app.state.db),
        focus_repo=FocusRepository(app.state.db),
        outbox=OutboxRepository(app.state.db),
    )


@pytest.fixture()
def env(tmp_path):
    return make_env(tmp_path)


def seeded_env(tmp_path, *events: NormalizedSourceEvent) -> Env:
    """Baseline first, then the events appear in a later window."""
    e = make_env(tmp_path)
    assert e.check_now().status_code == 200          # baseline: zero new
    for i, event in enumerate(events):
        e.fake.add(f"t{i}", event)
    return e


# --------------------------------------------------------------------------- #
# Ingestion -> Attention + atomic Decision projection
# --------------------------------------------------------------------------- #


def test_financial_high_creates_item_and_exactly_one_decision(tmp_path) -> None:
    env = seeded_env(tmp_path, make_event("m1", **FINANCIAL_EVENT))

    resp = env.new_check_now()
    assert resp.status_code == 200
    body = resp.json()
    assert body["checked_count"] == 1 and len(body["new_items"]) == 1
    item = body["new_items"][0]
    assert item["priority"] == "high"
    assert item["attention_type"] == "decision_required"
    assert item["decision_id"]

    stored = env.attention_repo.get(item["id"])
    assert stored is not None and stored.decision_id == item["decision_id"]
    decision = env.decision_repo.get(item["decision_id"])
    assert decision is not None
    assert decision.attention_item_id == item["id"]
    assert decision.status is DecisionStatus.NEEDS_REVIEW
    assert decision.risk is ActionRisk.HIGH
    assert decision.money is not None
    assert decision.money.amount_minor_units == 250_000 and decision.money.currency == "PLN"
    # Every claim source resolves inside the decision's own evidence.
    for claim in decision.context:
        assert set(claim.source_ids) <= {s.id for s in decision.sources}
    assert len(env.decision_repo.list_all()) == 1


def test_duplicate_poll_creates_no_second_item_or_decision(tmp_path) -> None:
    env = seeded_env(tmp_path, make_event("m1", **FINANCIAL_EVENT))
    assert len(env.new_check_now().json()["new_items"]) == 1

    second = env.new_check_now("check-2")
    assert second.status_code == 200
    assert second.json()["new_items"] == []
    assert second.json()["duplicate_count"] >= 1
    listing = env.client.get("/api/attention").json()["items"]
    assert len(listing) == 1
    assert len(env.decision_repo.list_all()) == 1


def test_generic_mail_is_low_fyi_without_decision(tmp_path) -> None:
    env = seeded_env(tmp_path, make_event("m1"))
    item = env.new_check_now().json()["new_items"][0]
    assert item["priority"] == "low"
    assert item["attention_type"] == "fyi"
    assert item["decision_id"] is None
    assert env.decision_repo.list_all() == []


def test_important_sender_exact_address_floor(tmp_path) -> None:
    env = seeded_env(
        tmp_path, make_event("m1", subject="Quick hello", body="Hi!", sender="cfo@example.com")
    )
    item = env.new_check_now().json()["new_items"][0]
    assert item["priority"] == "medium"  # exact-address floor, not display-name


# --------------------------------------------------------------------------- #
# Focus: delivery-only policy + persisted completion summary
# --------------------------------------------------------------------------- #


def start_focus(env: Env, request_id: str, **body) -> dict:
    payload = {
        "duration_minutes": 30,
        "threshold": "high",
        "sender_overrides": [],
        **body,
    }
    resp = env.client.post("/api/focus/start", json=payload, headers=_headers(request_id))
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]


def test_focus_defers_below_threshold_but_sender_exception_and_high_pass(tmp_path) -> None:
    env = make_env(tmp_path)
    assert env.check_now().status_code == 200          # baseline first
    session = start_focus(
        env, "focus-1", sender_overrides=["vip@example.com"]
    )
    assert session["threshold"] == "high"
    # Cross one second boundary: Gmail stamps are whole seconds, so events
    # created in the SAME (fractional) second as starts_at would truncate to
    # before the session start - a real ±1s property of provider stamps.
    time.sleep(1.05)

    env.fake.add("t1", make_event("m-low"))                                # LOW  -> deferred
    env.fake.add("t2", make_event("m-vip", sender="vip@example.com"))      # exact sender -> delivered
    env.fake.add("t3", make_event("m-high", **FINANCIAL_EVENT))            # HIGH -> delivered
    assert env.new_check_now("check-2").status_code == 200

    items = {i["source_id"]: i for i in env.client.get("/api/attention").json()["items"]}
    assert items["m-low"]["delivery"] == "deferred"
    assert items["m-vip"]["delivery"] == "delivered"
    assert items["m-high"]["delivery"] == "delivered"
    # Deferral changed DELIVERY only - classification stands untouched.
    assert items["m-low"]["priority"] == "low"
    assert items["m-high"]["priority"] == "high"

    stop = env.client.post("/api/focus/stop", json={}, headers=_headers("focus-2"))
    assert stop.status_code == 200
    summary = stop.json()["summary"]
    assert summary["total_received"] == 3
    assert summary["deferred_count"] == 1
    assert summary["decision_count"] == 1
    assert summary["action_count"] + summary["fyi_count"] == 2
    assert sorted(summary["attention_item_ids"]) == sorted(items[i]["id"] for i in items)

    # After stop, the same summary is served from persistence (reload-stable).
    again = env.client.get(f"/api/focus/{session['id']}/summary")
    assert again.status_code == 200
    assert again.json()["summary"] == summary


def test_focus_summary_reproducible_after_full_reload(tmp_path) -> None:
    env = make_env(tmp_path)
    assert env.check_now().status_code == 200          # baseline first
    session = start_focus(env, "focus-1")
    time.sleep(1.05)  # see stamp-boundary note above
    env.fake.add("t0", make_event("m1"))
    assert env.new_check_now().status_code == 200   # item lands inside session

    stop = env.client.post("/api/focus/stop", json={}, headers=_headers("focus-2"))
    assert stop.status_code == 200
    summary = stop.json()["summary"]
    assert summary["total_received"] == 1

    # A brand-new app instance over the same database file (process restart).
    restarted = create_app(env.settings)
    client2 = TestClient(restarted)
    again = client2.get(f"/api/focus/{session['id']}/summary")
    assert again.status_code == 200
    assert again.json()["summary"] == summary


def test_only_one_active_focus_session(tmp_path) -> None:
    env = make_env(tmp_path)
    start_focus(env, "focus-1")
    resp = env.client.post(
        "/api/focus/start",
        json={"duration_minutes": 10, "threshold": "medium", "sender_overrides": []},
        headers=_headers("focus-dup"),
    )
    assert resp.status_code == 409


def test_expired_session_is_not_active(tmp_path) -> None:
    env = make_env(tmp_path)
    now = datetime.now(timezone.utc)
    stale = FocusSession(
        id="focus-stale", starts_at=now - timedelta(hours=2),
        ends_at=now - timedelta(hours=1), threshold=AttentionPriority.HIGH,
        policy_version=POLICY.version,
    )
    env.focus_repo.start(stale)
    assert env.client.get("/api/focus/current").json()["session"] is None


# --------------------------------------------------------------------------- #
# Decision Inbox: guarded accept/reject + defer
# --------------------------------------------------------------------------- #


def test_high_financial_accept_requires_ui_confirm_and_records_internally(tmp_path) -> None:
    env = seeded_env(tmp_path, make_event("m1", **FINANCIAL_EVENT))
    item = env.new_check_now().json()["new_items"][0]
    decision_id = item["decision_id"]

    proposal = env.client.post(
        f"/api/decisions/{decision_id}/outcome-proposals",
        json={"outcome": "accept", "session_id": "sess-b04", "request_id": "out-1"},
        headers=SESSION,
    )
    assert proposal.status_code == 200
    decision = proposal.json()["decision"]
    assert decision["proposed_action_id"]
    action_id = decision["proposed_action_id"]

    action = env.client.get(
        f"/api/actions/{action_id}", headers=SESSION
    ).json()["action"]
    assert action["risk"] == "high"
    assert action["requires_approval"] is True
    assert action["voice_approval_allowed"] is False  # HIGH is UI-only

    challenge_resp = env.client.post(
        f"/api/actions/{action_id}/challenge", headers=SESSION
    ).json()
    confirm = env.client.post(
        f"/api/actions/{action_id}/confirm",
        json={
            "action_id": action_id,
            "revision": challenge_resp["revision"],
            "arguments_digest": challenge_resp["arguments_digest"],
            "choice": "approve",
            "challenge": challenge_resp["challenge"],
        },
        headers=SESSION,
    )
    assert confirm.status_code == 200
    executed = confirm.json()
    assert executed["action"]["status"] == "succeeded"
    assert executed["result"]["data"]["executed_externally"] is False

    final = env.client.get(f"/api/decisions/{decision_id}").json()["decision"]
    assert final["status"] == "resolved"
    assert final["outcome"] == "accept"
    assert final["outcome_recorded_at"]

    # A second accept attempt on a resolved decision is rejected outright.
    again = env.client.post(
        f"/api/decisions/{decision_id}/outcome-proposals",
        json={"outcome": "reject", "session_id": "sess-b04", "request_id": "out-2"},
        headers=SESSION,
    )
    assert again.status_code == 409


def test_defer_is_internal_bookkeeping_only(tmp_path) -> None:
    env = seeded_env(tmp_path, make_event("m1", **FINANCIAL_EVENT))
    decision_id = env.new_check_now().json()["new_items"][0]["decision_id"]

    resp = env.client.post(
        f"/api/decisions/{decision_id}/defer",
        json={"reason": "discuss on Friday"},
        headers=SESSION,
    )
    assert resp.status_code == 200
    assert resp.json()["decision"]["status"] == "deferred"

    # Deferral creates NO action and executes nothing.
    stored = env.decision_repo.get(decision_id)
    assert stored is not None and stored.proposed_action_id is None
    # And it can still be resolved afterwards through the guarded path.
    proposal = env.client.post(
        f"/api/decisions/{decision_id}/outcome-proposals",
        json={"outcome": "accept", "session_id": "sess-b04", "request_id": "out-3"},
        headers=SESSION,
    )
    assert proposal.status_code == 200


# --------------------------------------------------------------------------- #
# Explanations: stored data only
# --------------------------------------------------------------------------- #


def test_explanation_reads_stored_reasons(tmp_path) -> None:
    env = seeded_env(tmp_path, make_event("m1", **FINANCIAL_EVENT))
    item = env.new_check_now().json()["new_items"][0]

    resp = env.client.get(f"/api/attention/{item['id']}/explanation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["policy_version"] == POLICY.version
    codes = {r["code"] for r in body["priority_reasons"]}
    assert "attention.financial_decision_high" in codes
    assert all(r["origin"] == "rule" for r in body["priority_reasons"])  # deterministic run
    assert body["delivery_reasons"] and body["sources"]

    missing = env.client.get("/api/attention/att-nope/explanation")
    assert missing.status_code == 404


# --------------------------------------------------------------------------- #
# Engine-level guarantees (classifier is optional; floors never weaken)
# --------------------------------------------------------------------------- #


def test_model_outage_and_weak_proposals_keep_the_deterministic_floor(tmp_path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'engine.db'}")
    init_schema(db)
    rules = AttentionRules(policy=POLICY, important_senders={"cfo@example.com"})
    focus = FocusService(
        repo=FocusRepository(db), attention_repo=AttentionRepository(db),
        clock=lambda: T0, policy_version=POLICY.version,
    )

    class ExplodingClassifier:
        def classify(self, event):  # noqa: ARG002
            raise RuntimeError("model outage")

    engine = AttentionEngine(
        rules=rules, policy_version=POLICY.version, focus_service=focus,
        clock=lambda: T0, classifier=ExplodingClassifier(),
    )
    item = engine.ingest(make_event("m1", **FINANCIAL_EVENT))
    assert item.priority is AttentionPriority.HIGH
    assert item.attention_type is AttentionType.DECISION_REQUIRED

    class Weakener(ExplodingClassifier):
        def classify(self, event):
            return AmbiguitySuggestion(priority=AttentionPriority.LOW,
                                       attention_type=AttentionType.FYI)

    engine2 = AttentionEngine(
        rules=rules, policy_version=POLICY.version, focus_service=focus,
        clock=lambda: T0, classifier=Weakener(),
    )
    item2 = engine2.ingest(make_event("m2", **FINANCIAL_EVENT))
    assert item2.priority is AttentionPriority.HIGH  # clamped back up
    assert item2.attention_type is AttentionType.DECISION_REQUIRED

    class Strengthener(ExplodingClassifier):
        def classify(self, event):
            return AmbiguitySuggestion(priority=AttentionPriority.MEDIUM,
                                       attention_type=AttentionType.ACTION_REQUIRED)

    engine3 = AttentionEngine(
        rules=rules, policy_version=POLICY.version, focus_service=focus,
        clock=lambda: T0, classifier=Strengthener(),
    )
    item3 = engine3.ingest(make_event("m3"))  # generic LOW/FYI floor
    assert item3.priority is AttentionPriority.MEDIUM
    assert item3.attention_type is AttentionType.ACTION_REQUIRED


# --------------------------------------------------------------------------- #
# Scheduler + live events
# --------------------------------------------------------------------------- #


def test_scheduler_publishes_events_once_even_on_replay(tmp_path) -> None:
    from app.scheduler.jobs import GmailPollScheduler

    env = seeded_env(tmp_path, make_event("m1", **FINANCIAL_EVENT))
    scheduler = GmailPollScheduler(
        ingestion_service=env.app.state.ingestion_service,
        outbox_repo=env.outbox,
        attention_repo=env.attention_repo,
        decision_repo=env.decision_repo,
        clock=lambda: datetime.now(timezone.utc),
    )
    scheduler.run_once()
    first = env.outbox.list_after(0)
    types = [e.type.value for _, e in first]
    assert "attention_item_created" in types and "decision_created" in types

    scheduler.run_once()  # duplicate window: no new items, no new events
    assert len(env.outbox.list_after(0)) == len(first)


def test_ws_stream_requires_session_and_delivers_events(tmp_path) -> None:
    from starlette.websockets import WebSocketDisconnect

    env = seeded_env(tmp_path, make_event("m1", **FINANCIAL_EVENT))
    env.new_check_now()

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with env.client.websocket_connect("/api/events") as ws:
            ws.receive_json()
    assert excinfo.value.code == 4401

    with env.client.websocket_connect("/api/events?session_id=sess-b04&after=0") as ws:
        payload = ws.receive_json()
        assert payload["type"] == "attention_item_created"
        assert payload["payload"]["item"]["source_id"] == "m1"


def test_final_dismissed_decision_cannot_receive_outcome_proposal(tmp_path) -> None:
    # P1 regression: EVERY final status (RESOLVED *and* DISMISSED) must reject a
    # new outcome proposal - not RESOLVED alone. Nothing sets DISMISSED in the
    # normal flow, so we drive it through the repository to lock the invariant.
    env = seeded_env(tmp_path, make_event("m1", **FINANCIAL_EVENT))
    decision_id = env.new_check_now().json()["new_items"][0]["decision_id"]

    stored = env.decision_repo.get(decision_id)
    assert stored is not None and stored.status is DecisionStatus.NEEDS_REVIEW
    env.decision_repo.update(stored.model_copy(update={"status": DecisionStatus.DISMISSED}))

    resp = env.client.post(
        f"/api/decisions/{decision_id}/outcome-proposals",
        json={"outcome": "accept", "session_id": "sess-b04", "request_id": "out-dismissed"},
        headers=SESSION,
    )
    assert resp.status_code == 409
    assert "final" in resp.json()["detail"]
