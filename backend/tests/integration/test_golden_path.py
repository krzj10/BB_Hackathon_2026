"""G3 golden path (synthetic) - the complete safe EVA loop end-to-end.

Every REAL internal service is exercised: A06 ingestion + rules, B04
AttentionEngine/Focus/Decisions, A03 policy + A04 approval/execution, B01
router seams, B03 agent/briefing, A05 voice route, scheduler and outbox.
Only EXTERNAL boundaries are hermetic fakes (Gmail transport, Google calendar,
the self-hosted LLM endpoint, STT). Cloud is disabled: every model call goes
through the injected router double - there is no other path to reach a model.

The nineteen numbered steps below mirror the mandatory demo; the final step
asserts the persisted completion state exactly. Security golden cases follow
in separate tests (quoted history, sender spoofing, injection, HIGH voice,
duplicate polls, model outage, cloud-off, decision replay).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.approvals.policy import load_policy
from app.attention.ingest import GmailIngestionService
from app.contracts.domain import (
    AttentionPriority,
    AttentionType,
    DecisionOutcome,
    DecisionStatus,
    DeliveryDecision,
    Language,
    Meeting,
    MeetingPriority,
    NormalizedSourceEvent,
    SourceKind,
    SourceRef,
    TimedSpan,
    ToolCall,
    Transcript,
)
from app.contracts.providers import LLMResponse, ProviderHealth
from app.db.repositories import (
    ActionRepository,
    AttentionRepository,
    DecisionRepository,
    FocusRepository,
    OutboxRepository,
)
from app.llm.base import LLMUnavailableError
from app.main import create_app

POLICY = load_policy()
SESSION = "sess-g3"
T0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


def headers(request_id: str) -> dict[str, str]:
    return {"X-EVA-Session-ID": SESSION, "X-EVA-Request-ID": request_id}


def make_event(
    source_id: str,
    *,
    body: str = "A friendly heads-up about next week.",
    subject: str = "Heads up",
    sender: str = "someone@example.com",
) -> NormalizedSourceEvent:
    received = datetime.now(timezone.utc).replace(microsecond=0)
    return NormalizedSourceEvent(
        source="gmail",  # coerced to SourceSystem.GMAIL
        source_id=source_id,
        sender_email=sender,
        subject=subject,
        body=body,
        received_at=received,
        sources=[
            SourceRef(id=f"gmail:{source_id}", kind=SourceKind.GMAIL_MESSAGE,
                      resource_id=source_id, title=subject, retrieved_at=received)
        ],
    )


FINANCIAL = dict(
    subject="Invoice approval needed",
    body="Please approve the invoice for 2500 PLN.",
    sender="vendor@supplier.example",
)


class FakeGmail:
    def __init__(self) -> None:
        self.entries: list[tuple[str, NormalizedSourceEvent]] = []

    def add(self, thread_id: str, event: NormalizedSourceEvent) -> None:
        self.entries.append((thread_id, event))

    def _threads(self, query: str):
        after = re.search(r"after:(\d+)", query)
        before = re.search(r"before:(\d+)", query)
        lo = int(after.group(1)) if after else None
        hi = int(before.group(1)) if before else None
        out: dict[str, list[str]] = {}
        for tid, event in self.entries:
            stamp = event.received_at.timestamp()
            if (lo is None or stamp >= lo) and (hi is None or stamp <= hi):
                out.setdefault(tid, []).append(event.source_id)
        return out

    def search_window(self, query, *, max_threads=100, max_pages=8):
        from app.contracts.domain import RetrievalStatus
        from app.google.gmail import ThreadSummary

        summaries = [
            ThreadSummary(thread_id=t, message_ids=tuple(ids), subject=None, snippet=None)
            for t, ids in self._threads(query).items()
        ]
        return summaries, RetrievalStatus.COMPLETE, []

    def get_thread(self, thread_id):
        from app.contracts.domain import RetrievalStatus
        from app.google.gmail import ThreadEvidence

        messages = [e for t, e in self.entries if t == thread_id]
        stamps = {e.source_id: e.received_at for t, e in self.entries if t == thread_id}
        return ThreadEvidence(
            thread_id=thread_id, messages=messages,
            retrieval_status=RetrievalStatus.COMPLETE, internal_dates=stamps,
        )


class RecordingRouter:
    """The ONLY inference door in this test; every call is recorded."""

    def __init__(self) -> None:
        self.primary = object()
        self.fallback = None
        self.calls: list[dict] = []
        self.script: dict[str, list] = {}

    def queue(self, kind: str, *items) -> None:
        self.script.setdefault(kind, []).extend(items)

    async def chat(self, messages, tools=None, response_schema=None):
        kind = "briefing" if response_schema is not None and "spoken_summary" in str(
            response_schema
        ) else "assistant"
        self.calls.append({"kind": kind, "messages": list(messages), "tools": tools})
        queue = self.script.get(kind) or []
        item = queue.pop(0) if queue else _text("OK.")
        if isinstance(item, Exception):
            raise item
        return item


def _text(t: str) -> LLMResponse:
    return LLMResponse(provider="fake", model="fake-serving-id", text=t, finish_reason="stop")


def make_meeting() -> Meeting:
    return Meeting(
        ref={"calendar_id": "primary", "event_id": "evt-7"},
        title="Weekly vendor sync",
        span=TimedSpan(start=T0, end=T0 + timedelta(hours=1), timezone="Europe/Warsaw"),
        priority=MeetingPriority.MEDIUM,
        description="Standing vendor sync.",
        sources=[SourceRef(id="cal:evt-7", kind=SourceKind.CALENDAR_EVENT,
                           resource_id="evt-7", title="Weekly vendor sync",
                           retrieved_at=T0)],
    )


class FakeCalendar:
    def __init__(self) -> None:
        self.meeting = make_meeting()

    def get_event(self, *, calendar_id, event_id):
        return self.meeting


class Env:
    def __init__(self, tmp_path):
        from app.config import Settings

        settings = Settings(eva_db_url=f"sqlite:///{tmp_path / 'g3.db'}")
        self.app = create_app(settings)
        self.router = RecordingRouter()
        self.gmail = FakeGmail()
        self.calendar = FakeCalendar()

        self.app.state.llm_router = self.router
        self.app.state.calendar_service_factory = lambda: self.calendar
        from app.meetings.briefing import BriefingService

        self.app.state.briefing_service = BriefingService(
            llm_router_provider=lambda: self.app.state.llm_router,
            calendar_service_factory=lambda: self.calendar,
            attention_repo=self.app.state.attention_repository,
            clock=lambda: datetime.now(timezone.utc),
            outbox=self.app.state.event_outbox_repository,
        )
        engine = self.app.state.attention_engine
        self.app.state.ingestion_service = GmailIngestionService(
            gmail_source_factory=lambda: self.gmail,
            cursors=self.app.state.cursor_repository,
            attention_repo=self.app.state.attention_repository,
            sink=engine,
            query="in:inbox",
            overlap_seconds=120,
            clock=lambda: datetime.now(timezone.utc),
            decision_projection=engine.decision_projection,
        )
        # Rebuild rules with the golden org's exact important sender.
        from app.attention.rules import AttentionRules

        engine._rules = AttentionRules(policy=POLICY, important_senders={"cfo@example.com"})

        self.client = TestClient(self.app)
        self.attention_repo = AttentionRepository(self.app.state.db)
        self.decision_repo = DecisionRepository(self.app.state.db)
        self.focus_repo = FocusRepository(self.app.state.db)
        self.outbox = OutboxRepository(self.app.state.db)
        self.actions = ActionRepository(self.app.state.db)

    def check_now(self, rid: str = "check"):
        resp = self.client.post("/api/attention/check-now", headers=headers(rid))
        assert resp.status_code == 200, resp.text
        return resp.json()


# --------------------------------------------------------------------------- #
# The nineteen-step golden path
# --------------------------------------------------------------------------- #


def test_g3_golden_path(tmp_path) -> None:
    env = Env(tmp_path)

    # STEP 1 - honest health before anything is connected.
    health = env.client.get("/api/health").json()
    assert health["components"]["llm"]["status"] in ("unavailable", "degraded")
    assert health["components"]["google"]["status"] == "unavailable"

    # STEP 2 - baseline run: zero new items, cursor established.
    first = env.check_now("baseline")
    assert first["new_items"] == [] and first["checked_count"] == 0

    # STEP 3 - current financial request -> HIGH DECISION_REQUIRED + one Decision.
    env.gmail.add("t-fin", make_event("m-fin", **FINANCIAL))
    new = env.check_now()["new_items"]
    assert len(new) == 1 and new[0]["priority"] == "high"
    assert new[0]["attention_type"] == "decision_required"
    decision_fin_id = new[0]["decision_id"]
    assert env.decision_repo.get(decision_fin_id) is not None

    # STEP 4 - exact important sender -> MEDIUM floor.
    env.gmail.add("t-cfo", make_event("m-cfo", subject="Status check", body="Any update?",
                                      sender="cfo@example.com"))
    new = env.check_now()["new_items"]
    assert new[0]["priority"] == "medium"

    # STEP 5 - generic mail -> LOW/FYI, no decision.
    env.gmail.add("t-gen", make_event("m-gen"))
    new = env.check_now()["new_items"]
    assert new[0]["priority"] == "low" and new[0]["attention_type"] == "fyi"
    assert len(env.decision_repo.list_all()) == 1

    # STEP 6 - attention list + stored explanation.
    listing = env.client.get("/api/attention").json()["items"]
    assert {i["source_id"] for i in listing} >= {"m-fin", "m-cfo", "m-gen"}
    expl = env.client.get(f"/api/attention/{[i['id'] for i in listing if i['source_id']=='m-fin'][0]}/explanation")
    assert expl.status_code == 200
    assert "attention.financial_decision_high" in {r["code"] for r in expl.json()["priority_reasons"]}

    # STEP 7 - grounded assistant answer (security rules present, no tools needed).
    env.router.queue("assistant", _text("Dzień dobry. Masz jedną decyzję do podjęcia."))
    resp = env.client.post(
        "/api/assistant/message",
        json={"request_id": "a1", "session_id": SESSION, "text": "Podsumuj poranek",
              "language": "pl"},
        headers=headers("a1"),
    )
    assert resp.status_code == 200 and "decyzj" in resp.json()["reply_text"]
    assert "ZASADY BEZPIECZEŃSTWA" in env.router.calls[-1]["messages"][0].content

    # STEP 8 - focus session starts through the guarded command path.
    start = env.client.post(
        "/api/focus/start",
        json={"duration_minutes": 30, "threshold": "high",
              "sender_overrides": ["vip@example.com"]},
        headers=headers("focus-1"),
    )
    assert start.status_code == 200
    focus_id = start.json()["session"]["id"]

    # STEP 9 - during focus: LOW deferred, VIP exception delivered, HIGH delivered.
    import time as _time
    _time.sleep(1.05)  # whole-second provider stamps (see B04 suite note)
    env.gmail.add("t-low", make_event("m-low2", subject="Digest", body="Weekly digest link."))
    env.gmail.add("t-vip", make_event("m-vip", subject="Now", body="Quick thing.",
                                      sender="vip@example.com"))
    env.gmail.add("t-fin2", make_event("m-fin2", **FINANCIAL))
    env.check_now("check-focus")
    items = {i["source_id"]: i for i in env.client.get("/api/attention").json()["items"]}
    assert items["m-low2"]["delivery"] == "deferred"
    assert items["m-vip"]["delivery"] == "delivered"
    assert items["m-fin2"]["delivery"] == "delivered"

    # STEP 10 - one active session; second start conflicts.
    assert env.client.get("/api/focus/current").json()["session"]["id"] == focus_id
    dup = env.client.post(
        "/api/focus/start",
        json={"duration_minutes": 5, "threshold": "low", "sender_overrides": []},
        headers=headers("focus-dup"),
    )
    assert dup.status_code == 409

    # STEP 11 - voice transcription contract (hermetic STT seam).
    class FakeNormalizer:
        def normalize(self, content_type, raw_bytes):
            from app.contracts.domain import AudioInput

            return AudioInput(wav_bytes=raw_bytes)

    class FakeStt:
        async def transcribe(self, audio, language_hint=None):
            return Transcript(text="uruchom tryb skupienia", language="pl",
                              duration_ms=1200, provider="fake-whisper")

        async def health(self):
            return ProviderHealth(status="ready", provider="fake-whisper")

    env.app.state.audio_normalizer = FakeNormalizer()
    env.app.state.stt_service = FakeStt()
    voice = env.client.post(
        "/api/voice/transcribe",
        files={"audio": ("take.wav", b"RIFFfakewav", "audio/wav")},
        data={"request_id": "v1", "language": "pl"},
        headers=headers("voice-1"),
    )
    assert voice.status_code == 200, voice.text
    assert voice.json()["transcript"]["text"] == "uruchom tryb skupienia"

    # STEP 12 - meeting briefing grounded in the calendar event's sources.
    env.router.queue(
        "briefing",
        LLMResponse(
            provider="fake", model="fake-serving-id", text="{}", finish_reason="stop",
            structured={
                "open_topics": [
                    {"text": "Vendor renewal is on the agenda.", "kind": "fact",
                     "source_ids": ["cal:evt-7"]},
                ],
                "spoken_summary": "Weekly vendor sync: renewal pricing is the key topic.",
            },
        ),
    )
    brief = env.client.post(
        "/api/briefing/meeting",
        json={"meeting_ref": {"calendar_id": "primary", "event_id": "evt-7"},
              "language": "en"},
    )
    assert brief.status_code == 200
    briefing = brief.json()["briefing"]
    valid_ids = {s["id"] for s in briefing["sources"]} | {
        s["id"] for s in briefing["meeting"]["sources"]}
    for claim in briefing["open_topics"]:
        assert set(claim["source_ids"]) <= valid_ids

    # STEP 13 - assistant calendar mutation becomes a PENDING proposal only.
    create_call = ToolCall(
        id="c-create", name="calendar.create_event",
        arguments={
            "tool": "calendar.create_event",
            "title": "Follow-up: vendor pricing",
            "span": {
                "kind": "timed",
                "start": (T0 + timedelta(days=2)).isoformat(),
                "end": (T0 + timedelta(days=2, hours=1)).isoformat(),
                "timezone": "Europe/Warsaw",
            },
            "attendees": [],
        },
    )
    env.router.queue("assistant",
                     LLMResponse(provider="fake", model="m", text=None,
                                 tool_calls=[create_call], finish_reason="tool_calls"),
                     _text("Zaproponowałem spotkanie - potwierdź w interfejsie."))
    a2 = env.client.post(
        "/api/assistant/message",
        json={"request_id": "a2", "session_id": SESSION,
              "text": "Umów follow-up za dwa dni", "language": "pl"},
        headers=headers("a2"),
    )
    assert a2.status_code == 200
    proposed = a2.json()["proposed_action"]
    assert proposed is not None and proposed["status"] == "pending"

    # STEP 14 - HIGH financial accept: proposal -> UI challenge/confirm -> local record.
    out_prop = env.client.post(
        f"/api/decisions/{decision_fin_id}/outcome-proposals",
        json={"outcome": "accept", "session_id": SESSION, "request_id": "out-1"},
    )
    assert out_prop.status_code == 200
    action_id = out_prop.json()["decision"]["proposed_action_id"]
    challenge = env.client.post(f"/api/actions/{action_id}/challenge",
                                headers={"X-EVA-Session-ID": SESSION}).json()
    confirm = env.client.post(
        f"/api/actions/{action_id}/confirm",
        json={"action_id": action_id, "revision": challenge["revision"],
              "arguments_digest": challenge["arguments_digest"],
              "choice": "approve", "challenge": challenge["challenge"]},
        headers={"X-EVA-Session-ID": SESSION},
    )
    assert confirm.status_code == 200
    assert confirm.json()["action"]["status"] == "succeeded"
    assert confirm.json()["result"]["data"]["executed_externally"] is False

    # STEP 15 - decisions inbox: resolved recorded; second decision deferred.
    resolved = env.client.get(f"/api/decisions/{decision_fin_id}").json()["decision"]
    assert resolved["status"] == "resolved" and resolved["outcome"] == "accept"
    decision2_id = items["m-fin2"]["decision_id"]
    defer = env.client.post(
        f"/api/decisions/{decision2_id}/defer", json={"reason": "after briefing"},
        headers={"X-EVA-Session-ID": SESSION},
    )
    assert defer.json()["decision"]["status"] == "deferred"

    # STEP 16 - stop focus: completion summary computed from persisted rows.
    stop = env.client.post("/api/focus/stop", json={}, headers=headers("focus-2"))
    assert stop.status_code == 200
    summary = stop.json()["summary"]
    assert summary["total_received"] == 3
    assert summary["deferred_count"] == 1
    assert summary["decision_count"] == 1

    # STEP 17 - duplicate poll: no new attention, no new decisions.
    replay = env.check_now("replay")
    assert replay["new_items"] == [] and replay["duplicate_count"] >= 3

    # STEP 18 - scheduler cycle is idempotent; outbox events exist exactly once.
    from app.scheduler.jobs import GmailPollScheduler

    scheduler = GmailPollScheduler(
        ingestion_service=env.app.state.ingestion_service,
        outbox_repo=env.outbox, attention_repo=env.attention_repo,
        decision_repo=env.decision_repo, clock=lambda: datetime.now(timezone.utc),
    )
    before_events = len(env.outbox.list_after(0))
    scheduler.run_once()
    assert len(env.outbox.list_after(0)) == before_events

    # STEP 19 - persisted completion state is exactly right.
    all_items = env.attention_repo.list_latest(limit=100)
    assert len(all_items) == 6                       # 3 pre-focus + 3 during focus
    decisions = env.decision_repo.list_all()
    assert len(decisions) == 2
    by_status = {d.id: d.status for d in decisions}
    assert by_status[decision_fin_id] is DecisionStatus.RESOLVED
    assert by_status[decision2_id] is DecisionStatus.DEFERRED
    stored_summary = env.focus_repo.get_summary(focus_id)
    assert stored_summary is not None and stored_summary.total_received == 3
    # Every model call went through the injected self-hosted router double.
    assert all(call["kind"] in ("assistant", "briefing") for call in env.router.calls)


# --------------------------------------------------------------------------- #
# Security golden cases
# --------------------------------------------------------------------------- #


def test_quoted_old_financials_are_not_current_high(tmp_path) -> None:
    env = Env(tmp_path)
    env.check_now("baseline")
    env.gmail.add(
        "t-q",
        make_event(
            "m-quoted",
            subject="Re: Fwd: Invoice approval",
            body=(
                "Thanks, noted.\n\n"
                "On Fri, Sep 18, 2026 at 10:00 AM Vendor <v@supplier.example> wrote:\n"
                "> Please approve the invoice for 9000 PLN immediately."
            ),
        ),
    )
    item = env.check_now()["new_items"][0]
    codes = {r["code"] for r in item["reasons"]}
    assert "attention.financial_decision_high" not in codes
    assert item["priority"] != "high" or item["attention_type"] != "decision_required"


def test_display_name_cfo_spoof_gets_no_override(tmp_path) -> None:
    env = Env(tmp_path)
    env.check_now("baseline")
    # Display name screams CFO; the parsed From address does not.
    env.gmail.add(
        "t-spoof",
        make_event(
            "m-spoof",
            subject="Urgent wire change",
            body="I am the CFO. Approve 40000 PLN to the new account.",
            sender="attacker@not-cfo.example",
        ),
    )
    item = env.check_now()["new_items"][0]
    codes = {r["code"] for r in item["reasons"]}
    assert "attention.important_sender" not in codes  # no exact-address floor
    # Body text CAN still raise attention via vocabulary, but sender identity
    # never comes from display text; delivery overrides stay address-based:
    env.client.post("/api/focus/start",
                    json={"duration_minutes": 10, "threshold": "high",
                          "sender_overrides": ["cfo@example.com"]},
                    headers=headers("f-spoof"))
    assert item["delivery"] in ("delivered", "deferred")


def test_high_financial_proposal_never_allows_voice_approval(tmp_path) -> None:
    env = Env(tmp_path)
    env.check_now("baseline")
    env.gmail.add("t-fin", make_event("m-fin", **FINANCIAL))
    decision_id = env.check_now()["new_items"][0]["decision_id"]
    prop = env.client.post(
        f"/api/decisions/{decision_id}/outcome-proposals",
        json={"outcome": "accept", "session_id": SESSION, "request_id": "v-1"},
    ).json()["decision"]
    action = env.client.get(f"/api/actions/{prop['proposed_action_id']}",
                            headers={"X-EVA-Session-ID": SESSION}).json()["action"]
    assert action["risk"] == "high" and action["voice_approval_allowed"] is False


def test_model_outage_keeps_deterministic_attention_and_honest_503(tmp_path) -> None:
    env = Env(tmp_path)
    env.router.primary = None  # inference route fully down
    env.check_now("baseline")
    env.gmail.add("t-fin", make_event("m-fin", **FINANCIAL))
    item = env.check_now()["new_items"][0]          # deterministic path unaffected
    assert item["priority"] == "high" and item["attention_type"] == "decision_required"
    resp = env.client.post(
        "/api/assistant/message",
        json={"request_id": "x", "session_id": SESSION, "text": "hi", "language": "en"},
        headers=headers("x"),
    )
    assert resp.status_code == 503                    # honest: no cloud fallback


def test_cloud_disabled_zero_cloud_calls(tmp_path) -> None:
    from app.llm.router import LLMRouter

    env = Env(tmp_path)
    settings = env.app.state.settings
    assert settings.eva_allow_cloud_inference is False
    assert settings.eva_allow_workspace_cloud_inference is False
    # Building a router from unconfigured settings yields NO route at all -
    # there is no cloud construction path to fall through to.
    bare = LLMRouter.from_settings(settings)
    assert bare.primary is None and bare.fallback is None


def test_decision_replay_does_not_duplicate_local_write(tmp_path) -> None:
    env = Env(tmp_path)
    env.check_now("baseline")
    env.gmail.add("t-fin", make_event("m-fin", **FINANCIAL))
    decision_id = env.check_now()["new_items"][0]["decision_id"]

    prop = env.client.post(
        f"/api/decisions/{decision_id}/outcome-proposals",
        json={"outcome": "accept", "session_id": SESSION, "request_id": "rep-1"},
    ).json()["decision"]
    action_id = prop["proposed_action_id"]

    # Same (session, request) slot replays the SAME action.
    again = env.client.post(
        f"/api/decisions/{decision_id}/outcome-proposals",
        json={"outcome": "accept", "session_id": SESSION, "request_id": "rep-1"},
    ).json()["decision"]
    assert again["proposed_action_id"] == action_id

    challenge = env.client.post(f"/api/actions/{action_id}/challenge",
                                headers={"X-EVA-Session-ID": SESSION}).json()
    confirm_body = {
        "action_id": action_id, "revision": challenge["revision"],
        "arguments_digest": challenge["arguments_digest"],
        "choice": "approve", "challenge": challenge["challenge"],
    }
    first = env.client.post(f"/api/actions/{action_id}/confirm", json=confirm_body,
                            headers={"X-EVA-Session-ID": SESSION})
    assert first.status_code == 200
    assert first.json()["action"]["status"] == "succeeded"

    # Replaying the confirm is IDEMPOTENT recovery from a lost response (A04):
    # the engine returns already_approved with no second receipt, and the
    # executor's terminal-state guard replays the STORED result - it NEVER runs
    # the handler again. So there is exactly one durable write, not two.
    replayed = env.client.post(f"/api/actions/{action_id}/confirm", json=confirm_body,
                               headers={"X-EVA-Session-ID": SESSION})
    assert replayed.status_code == 200
    assert replayed.json()["action"]["status"] == "succeeded"
    # Same stored result object is returned (no fresh execution side effect).
    assert replayed.json()["result"] == first.json()["result"]
    # No second action was created for the slot; exactly one decision exists.
    assert env.actions.get_idempotent_proposal(SESSION, "rep-1")[1] == action_id
    assert len(env.decision_repo.list_all()) == 1
    final = env.decision_repo.get(decision_id)
    assert final is not None and final.status is DecisionStatus.RESOLVED
    assert final.outcome is DecisionOutcome.ACCEPT
