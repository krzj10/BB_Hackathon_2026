"""A04 integration tests: registry, guarded executor and Calendar writes.

Everything is hermetic: an in-memory fake Google transport with call counters
proves exactly which provider requests happen (and that denied paths produce
ZERO mutation calls). No live Google, no network, synthetic data only.

Run: python -m pytest backend/tests/integration/test_actions.py -q
"""

from __future__ import annotations

import copy
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent.tool_executor import ToolExecutor, generate_google_event_id
from app.agent.tool_registry import FORBIDDEN_TOOL_NAMES, RegistryError, build_default_registry
from app.approvals.engine import ActionApprovalEngine
from app.approvals.policy import load_policy
from app.config import Settings
from app.contracts.api import IntegrationStatus
from app.contracts.domain import (
    ActionRisk,
    ApprovalChannel,
    ApprovalReceipt,
    ProposedAction,
    ProposedActionStatus,
    ToolCall,
)
from app.db.repositories import ActionRepository
from app.google.auth import CALENDAR_SCOPE, GMAIL_READONLY_SCOPE
from app.google.calendar import EVA_AGENDA_END, EVA_AGENDA_START, CalendarService
from app.google.http import GoogleApiError, GoogleErrorCategory
from app.main import create_app

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
SESSION = "sess-a04"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class FakeGoogleHttp:
    """In-memory Google Calendar transport with request accounting."""

    def __init__(self) -> None:
        self.events: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []  # (METHOD, url)
        self.post_bodies: list[dict] = []
        self.post_params: list[dict] = []
        self.patch_bodies: list[dict] = []
        self.patch_headers: list[dict] = []
        self.shape_violations: list[str] = []  # REST-shape regressions (PART 27)
        self._etag_seq = 0
        self.post_hook = None  # callable(body, params); may raise after applying
        self.post_response_id_override = None  # provider returns a DIFFERENT id
        self.patch_hook = None  # callable(event_id, body)

    # -- accounting helpers -------------------------------------------------
    @property
    def mutations(self) -> list[tuple[str, str]]:
        return [c for c in self.calls if c[0] in ("POST", "PATCH")]

    def _new_etag(self) -> str:
        self._etag_seq += 1
        return f"etag-{self._etag_seq}"

    # -- transport surface ----------------------------------------------------
    def get_json(self, url, params):
        self.calls.append(("GET", url))
        if url.endswith("/events"):
            items = list(self.events.values())
            time_min = (params or {}).get("timeMin")
            time_max = (params or {}).get("timeMax")
            if time_min and time_max:
                items = [e for e in items if _overlaps(e, time_min, time_max)]
            return {"items": copy.deepcopy(items)}
        event_id = url.rsplit("/", 1)[-1]
        event = self.events.get(event_id)
        if event is None:
            raise GoogleApiError(GoogleErrorCategory.NOT_FOUND, status_code=404, endpoint_kind="events")
        return copy.deepcopy(event)

    def post_json(self, url, params, body, headers=None):
        self.calls.append(("POST", url))
        self.post_bodies.append(copy.deepcopy(body))
        self.post_params.append(dict(params or {}))
        # Strict REST-shape enforcement (PART 27): Google events.insert has NO
        # eventId query parameter - the client id lives in body["id"], and the
        # only supported option here is sendUpdates. Violations are recorded
        # so tests fail loudly even if the executor classifies the error.
        params = params or {}
        if "eventId" in params:
            self.shape_violations.append("eventId must never be a query parameter")
        unsupported = set(params) - {"sendUpdates"}
        if unsupported:
            self.shape_violations.append(f"unsupported create query params: {sorted(unsupported)}")
        # The client-supplied ID comes from the Event resource body, exactly
        # like the real API. Without one, emulate a provider-generated id.
        event_id = body.get("id") or f"srv-{len(self.events)}"
        if event_id in self.events and self.post_hook is None:
            raise GoogleApiError(GoogleErrorCategory.CONFLICT, status_code=409, endpoint_kind="events")

        def apply() -> None:
            stored = {
                "id": event_id,
                "summary": body.get("summary"),
                "start": copy.deepcopy(body.get("start")),
                "end": copy.deepcopy(body.get("end")),
                "etag": self._new_etag(),
            }
            if "description" in body:
                stored["description"] = body["description"]
            if "location" in body:
                stored["location"] = body["location"]
            if "attendees" in body:
                stored["attendees"] = copy.deepcopy(body["attendees"])
            self.events[event_id] = stored

        if self.post_hook is not None:
            self.post_hook(copy.deepcopy(body), dict(params or {}), apply)
        else:
            apply()
        result = (
            copy.deepcopy(self.events[event_id])
            if event_id in self.events
            else {"id": event_id}
        )
        if self.post_response_id_override is not None:
            result["id"] = self.post_response_id_override
        return result

    def patch_json(self, url, params, body, headers=None):
        self.calls.append(("PATCH", url))
        self.patch_bodies.append(copy.deepcopy(body))
        self.patch_headers.append(dict(headers or {}))
        event_id = url.rsplit("/", 1)[-1]
        event = self.events.get(event_id)
        if event is None:
            raise GoogleApiError(GoogleErrorCategory.NOT_FOUND, status_code=404, endpoint_kind="events")
        if_match = (headers or {}).get("If-Match")
        if if_match != event["etag"]:
            raise GoogleApiError(
                GoogleErrorCategory.PRECONDITION_FAILED, status_code=412, endpoint_kind="events"
            )
        if self.patch_hook is not None:
            self.patch_hook(event_id, copy.deepcopy(body), lambda: self._apply_patch(event_id, body))
        else:
            self._apply_patch(event_id, body)
        return copy.deepcopy(self.events[event_id])

    def _apply_patch(self, event_id: str, body: dict) -> None:
        event = self.events[event_id]
        for key, value in body.items():
            event[key] = copy.deepcopy(value)
        event["etag"] = self._new_etag()


class FakeAuth:
    def __init__(self) -> None:
        self.scopes = {CALENDAR_SCOPE, GMAIL_READONLY_SCOPE}

    def authorized_session(self):
        return object()

    def status(self) -> IntegrationStatus:
        return IntegrationStatus(
            name="google", connected=True, granted_scopes=sorted(self.scopes)
        )


class Env(SimpleNamespace):
    pass


@pytest.fixture()
def env(tmp_path) -> Env:
    fake = FakeGoogleHttp()
    clock = Clock()
    settings = Settings(
        eva_db_url=f"sqlite:///{tmp_path / 'a04.db'}",
        eva_app_allowed_origins=["http://app.local"],
        google_client_id="client-id-synthetic",
        google_client_secret="client-secret-synthetic",
        _env_file=None,
    )
    app = create_app(settings)
    auth = FakeAuth()
    app.state.google_auth = auth
    app.state.google_http_factory = lambda session: fake
    app.state.clock = clock  # routes + executor share the fixed test clock

    def _read_result(tool: str, data: dict):
        from app.contracts.domain import ToolResult, ToolResultStatus

        return ToolResult(
            call_id=f"read-{tool}", tool=tool, status=ToolResultStatus.OK, data=data, duration_ms=0
        )

    executor = ToolExecutor(
        registry=app.state.tool_registry,
        repo=app.state.action_repository,
        engine=app.state.approval_engine,
        clock=clock,
        calendar_service_factory=lambda: CalendarService(fake),
        granted_scopes_provider=lambda: set(auth.scopes),
        local_handlers={"focus.stop": lambda args: {"stopped": True}},
        read_handlers={
            "calendar.get_event": lambda args: _read_result(
                "calendar.get_event",
                {"meeting": CalendarService(fake).get_event(
                    calendar_id=args.calendar_id, event_id=args.event_id
                ).model_dump(mode="json")},
            ),
            "calendar.list_events": lambda args: _read_result(
                "calendar.list_events", {"meetings": []}
            ),
        },
    )
    app.state.tool_executor = executor
    return Env(
        app=app, client=TestClient(app), fake=fake, clock=clock,
        repo=app.state.action_repository, engine=app.state.approval_engine,
        registry=app.state.tool_registry, executor=executor, auth=auth,
        db_url=settings.eva_db_url,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def headers(session: str = SESSION, request_id: str | None = "req-1", origin: str | None = None):
    h = {}
    if session:
        h["X-EVA-Session-ID"] = session
    if request_id:
        h["X-EVA-Request-ID"] = request_id
    if origin:
        h["Origin"] = origin
    return h


def timed_span_iso(start_hour: int = 10, hours: int = 1) -> dict:
    start = NOW.replace(hour=start_hour)
    return {
        "kind": "timed",
        "start": start.isoformat(),
        "end": (start + timedelta(hours=hours)).isoformat(),
        "timezone": "Europe/Warsaw",
    }


def raw_event(event_id: str = "evt-1", summary: str = "Vendor review", etag: str = "etag-1", **extra) -> dict:
    raw = {
        "id": event_id,
        "summary": summary,
        "etag": etag,
        "start": {"dateTime": "2026-09-21T14:00:00+02:00", "timeZone": "Europe/Warsaw"},
        "end": {"dateTime": "2026-09-21T15:00:00+02:00", "timeZone": "Europe/Warsaw"},
    }
    raw.update(extra)
    return raw


def _instant(value: str) -> datetime:
    if len(value) == 10:  # all-day date (Google's end.date is already exclusive)
        return datetime.fromisoformat(value + "T00:00:00+00:00")
    return datetime.fromisoformat(value)


def _overlaps(event: dict, time_min: str, time_max: str) -> bool:
    start = _instant(event["start"].get("dateTime") or event["start"]["date"])
    end = _instant(event["end"].get("dateTime") or event["end"]["date"])
    return start < _instant(time_max) and end > _instant(time_min)


def seed_event(env: Env, **kwargs) -> dict:
    event = raw_event(**kwargs)
    env.fake.events[event["id"]] = event
    return event


def propose(env: Env, body: dict, *, session=SESSION, request_id="req-1", origin=None):
    return env.client.post("/api/calendar/proposals", json=body, headers=headers(session, request_id, origin))


def create_body(title: str = "Team sync", **overrides) -> dict:
    body = {
        "tool": "calendar.create_event",
        "title": title,
        "span": timed_span_iso(),
        "attendees": [],
        "send_updates": "none",
    }
    body.update(overrides)
    return body


def challenge(env: Env, action_id: str, *, session=SESSION):
    response = env.client.post(f"/api/actions/{action_id}/challenge", headers=headers(session, None))
    assert response.status_code == 200, response.text
    return response


def canonical_digest(env: Env, tool: str, arguments: dict) -> str:
    from app.approvals.engine import arguments_digest, canonical_arguments

    return arguments_digest(canonical_arguments(env.engine.validate_arguments(tool, arguments)))


def confirm(env: Env, action_id: str, revision: int, digest: str, choice: str, *, session=SESSION):
    issued = env.client.post(f"/api/actions/{action_id}/challenge", headers=headers(session, None))
    assert issued.status_code == 200, issued.text
    token = issued.json()["challenge"]
    return env.client.post(
        f"/api/actions/{action_id}/confirm",
        json={
            "action_id": action_id, "revision": revision, "arguments_digest": digest,
            "choice": choice, "challenge": token,
        },
        headers=headers(session, None),
    )


def propose_and_approve(env: Env, body: dict, *, request_id="req-1", session=SESSION):
    proposal = propose(env, body, request_id=request_id)
    assert proposal.status_code == 200, proposal.text
    action = proposal.json()["action"]
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve", session=session)
    return action, confirmation


_seed_counter = iter(range(1, 10_000))


def seed_action(
    env: Env, *, tool="calendar.create_event", arguments=None,
    status=ProposedActionStatus.PENDING,
    requires_approval=True, risk=ActionRisk.MEDIUM, policy_version=None, digest=None,
    resource_version=None, expires_at=None, voice=False, revision=1, session_id=SESSION,
) -> ProposedAction:
    args = arguments or create_body()
    action = ProposedAction(
        id=f"action-seed-{next(_seed_counter):04d}",
        session_id=session_id,
        request_id="req-seed",
        revision=revision,
        tool=tool,
        arguments=args,
        # Canonical digest by default; mismatch scenarios pass one explicitly.
        arguments_digest=digest or canonical_digest(env, tool, args),
        summary="seed", reason="seed", impact="seed",
        resource_version=resource_version,
        policy_version=policy_version or env.engine.policy_version,
        risk=risk, requires_approval=requires_approval, voice_approval_allowed=voice,
        created_at=env.clock.now - timedelta(minutes=1),
        expires_at=expires_at or env.clock.now + timedelta(minutes=5),
        status=status,
    )
    env.repo.create_action(action)
    return action


def authorize_directly(env: Env, action: ProposedAction, channel=ApprovalChannel.UI) -> None:
    """Move a seeded PENDING action to APPROVED with a durable receipt."""
    receipt = ApprovalReceipt(
        id=f"receipt-{action.id}", action_id=action.id, revision=action.revision,
        arguments_digest=action.arguments_digest, channel=channel,
        approved_at=env.clock.now, policy_version=action.policy_version,
    )
    ok = env.repo.approve_with_receipt(
        receipt, expected_revision=action.revision,
        expected_arguments_digest=action.arguments_digest,
        expected_policy_version=action.policy_version, now=env.clock.now,
    )
    assert ok


# =========================================================================== #
# Registry (PART VI / VII)
# =========================================================================== #


def test_registry_contains_expected_tools_with_effects(env) -> None:
    expected = {
        "calendar.list_events": "read", "calendar.get_event": "read",
        "gmail.search": "read", "gmail.get_thread": "read",
        "calendar.create_event": "external_write", "calendar.reschedule_event": "external_write",
        "calendar.update_agenda": "external_write",
        "decision.record_outcome": "local_write",
        "focus.start": "local_write", "focus.stop": "local_write",
    }
    assert env.registry.names() == frozenset(expected)
    for name, effect in expected.items():
        entry = env.registry.get(name)
        assert entry.effect.value == effect, name
        if name.startswith("calendar.") and name != "calendar.list_events" and name != "calendar.get_event":
            assert entry.risk_floor in (ActionRisk.MEDIUM, ActionRisk.HIGH)
            assert CALENDAR_SCOPE in entry.required_scopes
    assert env.registry.get("focus.start").risk_floor is ActionRisk.LOW
    assert env.registry.get("decision.record_outcome").effect.value == "local_write"


def test_forbidden_tool_names_absent_and_unregistrable(env) -> None:
    forbidden = {
        "approval.confirm", "approval.override", "payment.execute", "purchase.execute",
        "contract.sign", "supplier.commit", "legal.commit", "calendar.delete_event",
        "gmail.send", "gmail.modify",
    }
    assert forbidden.isdisjoint(env.registry.names())
    assert forbidden.issubset(FORBIDDEN_TOOL_NAMES)
    entry = env.registry.get("focus.stop")
    tampered = entry.definition.model_copy(update={"name": "approval.confirm"})
    from app.agent.tool_registry import RegisteredTool

    with pytest.raises(RegistryError):
        type(env.registry)([RegisteredTool(definition=tampered, args_model=entry.args_model)])


def test_unknown_tool_fails_closed(env) -> None:
    with pytest.raises(RegistryError):
        env.registry.get("calendar.delete_everything")
    result = env.executor.execute_read(
        ToolCall(id="c1", name="nope.nope", arguments={})
    )
    assert result.error is not None and result.error.code == "unknown_tool"


# =========================================================================== #
# Read dispatch (PART VIII)
# =========================================================================== #


def test_read_dispatch_uses_a02_services_and_returns_tool_result(env) -> None:
    seed_event(env, event_id="evt-read-1", summary="Read me")
    result = env.executor.execute_read(
        ToolCall(id="c1", name="calendar.get_event", arguments={"event_id": "evt-read-1"})
    )
    assert result.status.value == "ok"
    assert result.data["meeting"]["ref"]["event_id"] == "evt-read-1"
    # Reads never produce mutations or proposals.
    assert env.fake.mutations == []


def test_read_dispatch_rejects_mutation_tools(env) -> None:
    result = env.executor.execute_read(
        ToolCall(id="c1", name="calendar.create_event", arguments=create_body())
    )
    assert result.error is not None and result.error.code == "not_a_read_tool"
    assert env.fake.mutations == []


# =========================================================================== #
# Proposal lifecycle through the API (PART XL / XLI)
# =========================================================================== #


def test_ordinary_create_proposal_is_medium_requiring_ui_approval(env) -> None:
    response = propose(env, create_body("Team sync"))
    assert response.status_code == 200, response.text
    action = response.json()["action"]
    assert action["risk"] == "medium" and action["requires_approval"] is True
    assert action["voice_approval_allowed"] is True


def test_high_create_is_ui_only_and_voice_denied_with_zero_writes(env) -> None:
    proposal = propose(env, create_body("Board strategy session"), request_id="req-high")
    action = proposal.json()["action"]
    assert action["risk"] == "high" and action["voice_approval_allowed"] is False
    # There is NO caller-selectable channel anywhere: an extra field on the
    # frozen ApprovalRequest is rejected outright (422), so a browser caller
    # can never claim voice approval of a UI-only action.
    issued = env.client.post(f"/api/actions/{action['id']}/challenge", headers=headers())
    assert issued.status_code == 200
    bad = env.client.post(
        f"/api/actions/{action['id']}/confirm",
        json={
            "action_id": action["id"], "revision": action["revision"],
            "arguments_digest": action["arguments_digest"], "choice": "approve",
            "challenge": issued.json()["challenge"], "channel": "voice",
        },
        headers=headers(),
    )
    assert bad.status_code == 422
    assert env.repo.get_action(action["id"]).status is ProposedActionStatus.PENDING
    assert env.fake.mutations == []


def test_proposal_idempotency_same_digest_replays_different_digest_409(env) -> None:
    first = propose(env, create_body("Team sync"), request_id="req-idem")
    second = propose(env, create_body("Team sync"), request_id="req-idem")
    assert first.json()["action"]["id"] == second.json()["action"]["id"]
    conflict = propose(env, create_body("Different meeting", location="room B"), request_id="req-idem")
    assert conflict.status_code == 409


def test_proposal_requires_session_and_request_headers_and_origin(env) -> None:
    assert propose(env, create_body(), session=None).status_code == 400
    assert propose(env, create_body("X"), request_id=None).status_code == 400
    denied = propose(env, create_body("Y"), origin="http://evil.example")
    assert denied.status_code == 403
    allowed = propose(env, create_body("Z"), origin="http://app.local", request_id="req-origin")
    assert allowed.status_code == 200


# =========================================================================== #
# Challenge endpoint (PART XLII / L)
# =========================================================================== #


def test_challenge_endpoint_no_store_and_bindings(env) -> None:
    proposal = propose(env, create_body("Team sync"), request_id="req-ch")
    action = proposal.json()["action"]
    response = challenge(env, action["id"])
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    payload = response.json()
    assert payload["action_id"] == action["id"]
    assert payload["revision"] == action["revision"]
    assert payload["arguments_digest"] == action["arguments_digest"]
    assert payload["challenge"] and "channel" not in payload


def test_challenge_wrong_session_and_bad_origin_rejected(env) -> None:
    proposal = propose(env, create_body("Team sync"), request_id="req-sess")
    action = proposal.json()["action"]
    wrong = env.client.post(
        f"/api/actions/{action['id']}/challenge", headers=headers(session="sess-other", request_id=None)
    )
    assert wrong.status_code == 403
    bad_origin = env.client.post(
        f"/api/actions/{action['id']}/challenge",
        headers={"X-EVA-Session-ID": SESSION, "Origin": "http://evil.example"},
    )
    assert bad_origin.status_code == 403


# =========================================================================== #
# Approved create execution (PART XI-XIII, XV, XXIX, LVI)
# =========================================================================== #


def test_approved_create_exactly_one_insert_and_verified_readback(env) -> None:
    action, confirmation = propose_and_approve(env, create_body("Vendor kickoff"), request_id="req-cr")
    assert confirmation.status_code == 200, confirmation.text
    body = confirmation.json()
    assert body["action"]["status"] == "succeeded"
    assert body["receipt"] is not None
    result = body["result"]
    assert result["status"] == "ok" and result["data"]["verified"] is True
    posts = [c for c in env.fake.calls if c[0] == "POST"]
    assert len(posts) == 1
    # Exactly one event exists with the client-generated id.
    assert list(env.fake.events) == [result["data"]["event_id"]]


def test_create_uses_durable_client_event_id_surviving_reopen(env) -> None:
    action, confirmation = propose_and_approve(env, create_body("Reconcile me"), request_id="req-cid")
    event_id = confirmation.json()["result"]["data"]["event_id"]
    stored = env.repo.get_google_event_id(action["id"], action["revision"])
    assert stored == event_id == generate_google_event_id(action["id"], action["revision"])
    # Google-valid charset and length (PART LVII).
    assert 8 <= len(stored) <= 1024
    assert all(ch in "0123456789abcdefghijklmnopqrstuv" for ch in stored)
    # DB reopen preserves the create id AND the durable result.
    from app.db.schema import init_schema
    from app.db.session import Database

    reopened = ActionRepository(Database(env.db_url))
    assert reopened.get_google_event_id(action["id"], action["revision"]) == event_id
    last = reopened.get_last_result(action["id"])
    assert last is not None and last.status.value == "ok"


def test_create_send_updates_mapping_all_external_none(env) -> None:
    for i, (send, expected) in enumerate((("all", "all"), ("external_only", "externalOnly"), ("none", "none"))):
        proposal = propose(
            env,
            create_body(f"Notify {send}", send_updates=send, span=timed_span_iso(8 + i)),
            request_id=f"req-send-{send}",
        )
        action = proposal.json()["action"]
        confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
        assert confirmation.status_code == 200, confirmation.text
        assert confirmation.json()["action"]["status"] == "succeeded"
    applied = [p.get("sendUpdates") for p in env.fake.post_params]
    assert applied == ["all", "externalOnly", "none"]


# =========================================================================== #
# Timeout / reconciliation (PART XXIX, XXX, LIII, LIV, LVIII)
# =========================================================================== #


def test_create_timeout_after_server_success_reconciles_to_succeeded(env) -> None:
    proposal = propose(env, create_body("Lost response"), request_id="req-to1")
    action = proposal.json()["action"]

    def hook(body, params, apply):
        apply()  # server applied the event...
        raise GoogleApiError(GoogleErrorCategory.TRANSPORT, endpoint_kind="events")  # ...response lost

    env.fake.post_hook = hook
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    assert confirmation.status_code == 200
    body = confirmation.json()
    assert body["action"]["status"] == "succeeded"
    posts_before = len([c for c in env.fake.calls if c[0] == "POST"])
    # Second execute: zero additional POSTs.
    env.executor.execute(action["id"])
    assert len([c for c in env.fake.calls if c[0] == "POST"]) == posts_before


def test_create_timeout_unresolved_becomes_unknown_and_blocks_replay(env) -> None:
    proposal = propose(env, create_body("Never arrived"), request_id="req-to2")
    action = proposal.json()["action"]

    def hook(body, params, apply):
        raise GoogleApiError(GoogleErrorCategory.TRANSPORT, endpoint_kind="events")  # nothing applied

    env.fake.post_hook = hook
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()
    assert body["action"]["status"] == "unknown"
    assert body["result"]["status"] == "unknown"
    posts_before = len([c for c in env.fake.calls if c[0] == "POST"])
    outcome = env.executor.execute(action["id"])  # replay must NOT mutate again
    assert outcome.action.status is ProposedActionStatus.UNKNOWN
    assert len([c for c in env.fake.calls if c[0] == "POST"]) == posts_before


def test_create_conflict_reconciles_when_event_matches(env) -> None:
    proposal = propose(env, create_body("Duplicate delivery"), request_id="req-conf")
    action = proposal.json()["action"]
    google_id = generate_google_event_id(action["id"], action["revision"])

    def hook(body, params, apply):
        apply()  # event exists (e.g. retried POST already landed)
        raise GoogleApiError(GoogleErrorCategory.CONFLICT, status_code=409, endpoint_kind="events")

    env.fake.post_hook = hook
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()
    assert body["action"]["status"] == "succeeded"
    assert body["result"]["data"].get("reconciled") is True
    assert google_id in env.fake.events


def test_owned_calendar_conflict_blocks_create_with_zero_posts(env) -> None:
    # Overlapping owned event inside the proposal window (10:00-11:00 UTC).
    seed_event(
        env, event_id="evt-overlap", summary="Existing overlap",
        start={"dateTime": "2026-09-21T12:15:00+02:00", "timeZone": "Europe/Warsaw"},
        end={"dateTime": "2026-09-21T12:45:00+02:00", "timeZone": "Europe/Warsaw"},
    )
    proposal = propose(env, create_body("Clashing meeting"), request_id="req-clash")
    action = proposal.json()["action"]
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()
    assert body["action"]["status"] == "failed"
    assert body["result"]["error"]["code"] == "owned_calendar_conflict"
    assert [c for c in env.fake.calls if c[0] == "POST"] == []


# =========================================================================== #
# Reschedule (PART XXIII-XXVI, LVI)
# =========================================================================== #


def new_span(start_hour: int = 16, hours: int = 1) -> dict:
    return timed_span_iso(start_hour, hours)


def reschedule_body(event_id="evt-1", **overrides) -> dict:
    body = {
        "tool": "calendar.reschedule_event",
        "ref": {"calendar_id": "primary", "event_id": event_id},
        "new_span": new_span(),
        "send_updates": "none",
    }
    body.update(overrides)
    return body


def agenda_body(event_id="evt-1", mode="add", markdown="- Decide: renewal terms", **overrides) -> dict:
    body = {
        "tool": "calendar.update_agenda",
        "ref": {"calendar_id": "primary", "event_id": event_id},
        "mode": mode,
        "agenda_markdown": markdown,
        "send_updates": "none",
    }
    body.update(overrides)
    return body


def test_reschedule_patches_only_span_with_if_match_and_preserves_fields(env) -> None:
    seed_event(
        env, event_id="evt-1", summary="Vendor review", etag="etag-v1",
        description="keep me", location="waraw", attendees=[{"email": "a@example.com"}],
    )
    action, confirmation = propose_and_approve(env, reschedule_body(), request_id="req-res")
    assert confirmation.status_code == 200, confirmation.text
    assert confirmation.json()["action"]["status"] == "succeeded"
    assert len(env.fake.patch_bodies) == 1
    patch = env.fake.patch_bodies[0]
    assert set(patch) == {"start", "end"}  # nothing else touched
    assert env.fake.patch_headers[0]["If-Match"] == "etag-v1"
    stored = env.fake.events["evt-1"]
    assert stored["description"] == "keep me" and stored["location"] == "waraw"
    assert stored["attendees"] == [{"email": "a@example.com"}]


def test_stale_etag_before_request_supersedes_with_zero_patches(env) -> None:
    seed_event(env, event_id="evt-1", etag="etag-v1")
    proposal = propose(env, reschedule_body(), request_id="req-stale")
    action = proposal.json()["action"]
    # Someone else changes the event after the proposal snapshot.
    env.fake.events["evt-1"]["etag"] = "etag-v2-changed"
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()
    assert body["action"]["status"] == "superseded"
    assert body["result"]["error"]["code"] == "stale_resource_version"
    assert env.fake.mutations == []


def test_http_412_supersedes_exactly_one_patch_no_retry(env) -> None:
    seed_event(env, event_id="evt-1", etag="etag-v1")
    proposal = propose(env, reschedule_body(), request_id="req-412")
    action = proposal.json()["action"]

    def hook(event_id, body, apply):
        raise GoogleApiError(
            GoogleErrorCategory.PRECONDITION_FAILED, status_code=412, endpoint_kind="events"
        )

    env.fake.patch_hook = hook
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()
    assert body["action"]["status"] == "superseded"
    assert body["result"]["error"]["code"] == "precondition_failed"
    patches = [c for c in env.fake.calls if c[0] == "PATCH"]
    assert len(patches) == 1  # no retry
    attempts = env.repo.list_attempts(action["id"])
    assert len(attempts) == 1 and attempts[0].outcome == "failed"
    assert attempts[0].detail == "precondition_failed"


def test_recurring_event_mutation_blocked_zero_patches(env) -> None:
    seed_event(env, event_id="evt-1", etag="etag-v1", recurringEventId="series-1")
    action, confirmation = propose_and_approve(env, reschedule_body(), request_id="req-rec")
    body = confirmation.json()
    assert body["action"]["status"] == "superseded"
    assert body["result"]["error"]["code"] == "recurring_series_ambiguous"
    assert env.fake.mutations == []


def test_cross_kind_reschedule_blocked(env) -> None:
    seed_event(env, event_id="evt-1", etag="etag-v1")  # timed event
    all_day = {"kind": "all_day", "start_date": "2026-09-25", "end_exclusive": "2026-09-26"}
    action, confirmation = propose_and_approve(
        env, reschedule_body(new_span=all_day), request_id="req-kind"
    )
    body = confirmation.json()
    assert body["action"]["status"] == "superseded"
    assert body["result"]["error"]["code"] == "cross_kind_reschedule_unsupported"
    assert env.fake.mutations == []


def test_all_day_reschedule_preserves_exclusive_end_dates(env) -> None:
    all_day_event = {
        "id": "evt-allday", "summary": "Conference day", "etag": "etag-ad-1",
        "start": {"date": "2026-09-25"}, "end": {"date": "2026-09-27"},
    }
    env.fake.events["evt-allday"] = all_day_event
    body = reschedule_body(
        event_id="evt-allday",
        new_span={"kind": "all_day", "start_date": "2026-10-05", "end_exclusive": "2026-10-07"},
    )
    action, confirmation = propose_and_approve(env, body, request_id="req-ad")
    assert confirmation.json()["action"]["status"] == "succeeded"
    stored = env.fake.events["evt-allday"]
    assert stored["start"] == {"date": "2026-10-05"} and stored["end"] == {"date": "2026-10-07"}


# =========================================================================== #
# Agenda (PART XXVII, LV)
# =========================================================================== #


def test_agenda_add_then_update_preserves_unrelated_text(env) -> None:
    seed_event(
        env, event_id="evt-1", etag="etag-v1",
        description="user text before\nlegacy notes\nuser text after",
    )
    action, confirmation = propose_and_approve(
        env, agenda_body(markdown="- Decide: renewal"), request_id="req-ag1"
    )
    assert confirmation.json()["action"]["status"] == "succeeded"
    stored = env.fake.events["evt-1"]["description"]
    assert stored.startswith("user text before\nlegacy notes\nuser text after")
    assert EVA_AGENDA_START in stored and "- Decide: renewal" in stored

    # UPDATE replaces only the EVA section; surrounding text byte-preserved.
    seed_event(env, event_id="evt-1", etag=env.fake.events["evt-1"]["etag"], description=stored)
    action2, confirmation2 = propose_and_approve(
        env, agenda_body(mode="update", markdown="- Decided: renew at X"), request_id="req-ag2"
    )
    assert confirmation2.json()["action"]["status"] == "succeeded"
    final = env.fake.events["evt-1"]["description"]
    assert "- Decided: renew at X" in final and "- Decide: renewal" not in final
    assert final.count(EVA_AGENDA_START) == 1
    prefix, _, suffix = final.partition(EVA_AGENDA_START)
    assert prefix.startswith("user text before\nlegacy notes\nuser text after")
    assert suffix.split(EVA_AGENDA_END, 1)[1] == stored.split(EVA_AGENDA_END, 1)[1]


def test_agenda_add_over_existing_section_fails_closed_without_patch(env) -> None:
    existing = f"keep\n{EVA_AGENDA_START}\nold agenda\n{EVA_AGENDA_END}\ntail"
    seed_event(env, event_id="evt-1", etag="etag-v1", description=existing)
    action, confirmation = propose_and_approve(
        env, agenda_body(markdown="- New content"), request_id="req-agdup"
    )
    body = confirmation.json()
    assert body["action"]["status"] == "failed"
    assert body["result"]["error"]["code"] == "agenda_section_exists"
    assert env.fake.mutations == []  # zero PATCH: no duplicate possible
    assert env.fake.events["evt-1"]["description"].count(EVA_AGENDA_START) == 1


# =========================================================================== #
# Denied paths -> ZERO Google mutations (PART V, XIX-XXII, LXI)
# =========================================================================== #


def test_missing_calendar_scope_blocks_with_zero_mutations(env) -> None:
    proposal = propose(env, create_body("Scopeless"), request_id="req-scope")
    action = proposal.json()["action"]
    env.auth.scopes = {GMAIL_READONLY_SCOPE}  # Gmail scope is NOT sufficient
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()
    assert body["result"]["error"]["code"] == "google_scope_missing"
    assert env.fake.mutations == []
    # Action keeps its approved state for a later attempt after reconnect.
    fresh = env.repo.get_action(action["id"])
    assert fresh.status is ProposedActionStatus.APPROVED


def test_changed_policy_supersedes_with_zero_mutations(env) -> None:
    action = seed_action(env, policy_version="v1-some-older-policy")
    authorize_directly(env, action)
    outcome = env.executor.execute(action.id)
    assert outcome.action.status is ProposedActionStatus.SUPERSEDED
    assert outcome.result.error.code == "policy_changed"
    assert env.fake.mutations == []


def test_tampered_digest_supersedes_with_zero_mutations(env) -> None:
    action = seed_action(env, digest="sha256:" + "f" * 64)  # != canonical arguments
    authorize_directly(env, action)
    outcome = env.executor.execute(action.id)
    assert outcome.action.status is ProposedActionStatus.SUPERSEDED
    assert outcome.result.error.code == "arguments_digest_mismatch"
    assert env.fake.mutations == []


def test_approved_without_receipt_cannot_execute(env) -> None:
    action = seed_action(env, status=ProposedActionStatus.APPROVED)  # APPROVED, no receipt exists
    outcome = env.executor.execute(action.id)
    assert outcome.action.status is ProposedActionStatus.APPROVED  # nothing moved
    assert env.fake.mutations == []


def test_high_receipt_with_wrong_channel_cannot_execute(env) -> None:
    action = seed_action(
        env, arguments=create_body("Board strategy session"), risk=ActionRisk.HIGH,
        requires_approval=True, voice=False, status=ProposedActionStatus.APPROVED,
    )
    receipt = ApprovalReceipt(
        id="receipt-voice-forgery", action_id=action.id, revision=action.revision,
        arguments_digest=action.arguments_digest, channel=ApprovalChannel.VOICE,
        approved_at=env.clock.now, policy_version=action.policy_version,
    )
    env.repo.record_receipt(receipt)  # synthetic inconsistent state
    outcome = env.executor.execute(action.id)
    assert outcome.action.status is ProposedActionStatus.APPROVED
    assert env.fake.mutations == []


def test_expired_proposal_never_executes(env) -> None:
    action = seed_action(env, expires_at=env.clock.now + timedelta(seconds=30))
    authorize_directly(env, action)
    env.clock.now += timedelta(minutes=10)
    outcome = env.executor.execute(action.id)
    assert outcome.action.status is ProposedActionStatus.EXPIRED
    assert env.fake.mutations == []


def test_rejected_proposal_never_executes(env) -> None:
    proposal = propose(env, create_body("Doomed meeting"), request_id="req-rej")
    action = proposal.json()["action"]
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "reject")
    assert confirmation.status_code == 200
    assert confirmation.json()["action"]["status"] == "rejected"
    assert confirmation.json()["result"] is None
    outcome = env.executor.execute(action["id"])
    assert outcome.action.status is ProposedActionStatus.REJECTED
    assert env.fake.mutations == []


def test_wrong_session_cannot_read_challenge_or_confirm_or_get(env) -> None:
    proposal = propose(env, create_body("Private plan"), request_id="req-ws")
    action = proposal.json()["action"]
    for method, url in (
        ("post", f"/api/actions/{action['id']}/challenge"),
        ("get", f"/api/actions/{action['id']}"),
    ):
        response = getattr(env.client, method)(url, headers=headers(session="sess-intruder", request_id=None))
        assert response.status_code == 403, url
    assert env.fake.mutations == []


def test_no_public_execute_endpoint_exists(env) -> None:
    proposal = propose(env, create_body("No direct run"), request_id="req-noexec")
    action = proposal.json()["action"]
    response = env.client.post(
        f"/api/actions/{action['id']}/execute", headers=headers(request_id=None)
    )
    assert response.status_code in (404, 405)


# =========================================================================== #
# No-approval execution + concurrency (PART XVII-XVIII, XXXIV)
# =========================================================================== #


def test_no_approval_proposal_executes_immediately_without_receipt(env) -> None:
    # The frozen proposals union carries CALENDAR tools only; local tools are
    # proposed through the internal agent path, never this browser route.
    rejected = propose(env, {"tool": "focus.stop"}, request_id="req-focus-api")
    assert rejected.status_code == 422
    assert env.fake.mutations == []

    # Internal no-approval execution: durable creation -> executor's atomic
    # claim -> local handler; NO synthetic receipt is ever written.
    action = seed_action(
        env, tool="focus.stop", arguments={"tool": "focus.stop"},
        requires_approval=False, risk=ActionRisk.LOW, status=ProposedActionStatus.PENDING,
    )
    outcome = env.executor.execute(action.id)
    assert outcome.action.status is ProposedActionStatus.SUCCEEDED
    assert env.repo.get_receipt(action.id, action.revision) is None


def test_two_concurrent_no_approval_claims_have_one_winner(env) -> None:
    action = seed_action(
        env, tool="focus.stop", arguments={"tool": "focus.stop"},
        requires_approval=False, risk=ActionRisk.LOW, status=ProposedActionStatus.PENDING,
    )
    barrier = threading.Barrier(2)
    results: list[int | None] = []
    lock = threading.Lock()

    def attempt() -> None:
        barrier.wait()
        won = env.repo.claim_no_approval_execution(
            action.id, expected_revision=action.revision,
            expected_arguments_digest=action.arguments_digest,
            expected_policy_version=action.policy_version, started_at=env.clock.now,
        )
        with lock:
            results.append(won)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert len(env.repo.list_attempts(action.id)) == 1


def test_two_concurrent_approved_executes_perform_one_mutation(env) -> None:
    proposal = propose(env, create_body("Double click"), request_id="req-dc")
    action = proposal.json()["action"]
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    # The confirm above already executed the FIRST action once. To measure the
    # true double-click race cleanly, use a SECOND freshly approved action at
    # a non-overlapping time and fire two concurrent execute() calls at it.
    assert confirmation.status_code == 200
    proposal2 = propose(
        env, create_body("Double click two", span=timed_span_iso(18)), request_id="req-dc2"
    )
    action2 = proposal2.json()["action"]
    seeded = env.repo.get_action(action2["id"])
    authorize_directly(env, seeded)

    barrier = threading.Barrier(2)
    outcomes = []

    def attempt() -> None:
        barrier.wait()
        try:
            outcome = env.executor.execute(action2["id"])
            outcomes.append(outcome.action.status.value)
        except Exception as exc:  # pragma: no cover - debug aid
            outcomes.append(f"error:{type(exc).__name__}")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    posts_total = len([c for c in env.fake.calls if c[0] == "POST"])
    assert posts_total == 2  # action1 once + action2 exactly once despite two racers
    # Exactly one mutation attempt for action2, final state SUCCEEDED; the
    # loser observed the current durable state instead of mutating.
    assert len(env.repo.list_attempts(action2["id"])) == 1
    assert "succeeded" in outcomes and set(outcomes) <= {"succeeded", "executing"}
    final = env.repo.get_action(action2["id"])
    assert final.status is ProposedActionStatus.SUCCEEDED


# =========================================================================== #
# Terminal-state replay guards (PART XXXII, XXXIII)
# =========================================================================== #


def test_terminal_states_never_replay_to_google(env) -> None:
    action, confirmation = propose_and_approve(env, create_body("Once only"), request_id="req-once")
    assert confirmation.json()["action"]["status"] == "succeeded"
    calls_before = list(env.fake.calls)
    for _ in range(3):
        outcome = env.executor.execute(action["id"])
        assert outcome.action.status is ProposedActionStatus.SUCCEEDED
        assert outcome.result is not None  # stored durable result replayed
    assert env.fake.calls == calls_before  # zero new requests of ANY kind


# =========================================================================== #
# GET action snapshot (PART XLVI, XV)
# =========================================================================== #


def test_get_action_returns_persisted_last_result(env) -> None:
    action, _ = propose_and_approve(env, create_body("Snapshot me"), request_id="req-snap")
    response = env.client.get(f"/api/actions/{action['id']}", headers=headers(request_id=None))
    assert response.status_code == 200
    payload = response.json()
    assert payload["action"]["status"] == "succeeded"
    assert payload["last_result"]["status"] == "ok"
    assert payload["last_result"]["data"]["verified"] is True


# =========================================================================== #
# Local handler boundary (PART LX / LXI)
# =========================================================================== #


def test_local_tool_without_handler_fails_closed(tmp_path, env) -> None:
    action = seed_action(
        env, tool="focus.stop", arguments={"tool": "focus.stop"},
        requires_approval=False, risk=ActionRisk.LOW, status=ProposedActionStatus.PENDING,
    )
    executor_without_handlers = ToolExecutor(
        registry=env.registry, repo=env.repo, engine=env.engine, clock=env.clock,
    )
    outcome = executor_without_handlers.execute(action.id)
    assert outcome.action.status is ProposedActionStatus.FAILED
    assert outcome.result.error.code == "handler_unavailable"


# =========================================================================== #
# REMEDIATION - create request shape (body id, no eventId param)
# =========================================================================== #


def test_create_request_shape_body_id_and_no_eventid_param(env) -> None:
    """Fails on the pre-remediation shape (eventId query param): the client-
    generated ID must ride in the Event resource body under 'id', while the
    query string carries only supported options such as sendUpdates."""
    action, confirmation = propose_and_approve(env, create_body("Shape check"), request_id="req-shape")
    assert confirmation.json()["action"]["status"] == "succeeded"
    stored = env.repo.get_google_event_id(action["id"], action["revision"])
    assert stored == generate_google_event_id(action["id"], action["revision"])

    assert env.fake.shape_violations == []
    assert "eventId" not in env.fake.post_params[0]
    assert env.fake.post_bodies[0]["id"] == stored
    assert env.fake.post_params[0].get("sendUpdates") == "none"
    # Read-back GET used the SAME durable client id.
    get_urls = [url for method, url in env.fake.calls if method == "GET"]
    assert any(url.endswith(f"/events/{stored}") for url in get_urls)


# =========================================================================== #
# REMEDIATION - atomic proposal idempotency (no orphan losers)
# =========================================================================== #


def _count(env: Env, sql: str, params: tuple) -> int:
    from app.db.session import Database

    with Database(env.db_url).connect() as conn:  # fresh connection = durable truth
        return conn.execute(sql, params).fetchone()[0]


def test_concurrent_same_digest_yields_exactly_one_action(env) -> None:
    body = create_body("Race sync", span=timed_span_iso(15))
    barrier = threading.Barrier(2)
    results: list[tuple[int, str | None]] = []
    lock = threading.Lock()

    def attempt() -> None:
        client = TestClient(env.app)  # independent request threads
        barrier.wait()
        response = client.post("/api/calendar/proposals", json=body, headers=headers(request_id="req-race1"))
        action_id = response.json().get("action", {}).get("id") if response.status_code == 200 else None
        with lock:
            results.append((response.status_code, action_id))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert [r[0] for r in results] == [200, 200], results
    assert results[0][1] is not None and results[0][1] == results[1][1]

    assert _count(env, "SELECT COUNT(*) FROM proposal_idempotency WHERE session_id=? AND request_id=?",
                  (SESSION, "req-race1")) == 1
    assert _count(env, "SELECT COUNT(*) FROM proposed_actions WHERE session_id=? AND request_id=?",
                  (SESSION, "req-race1")) == 1  # no orphan loser action
    # Only one action exists, hence exactly one challengeable surface.
    snapshot = env.client.get(f"/api/actions/{results[0][1]}", headers=headers(request_id=None))
    assert snapshot.status_code == 200


def test_concurrent_different_digest_one_wins_one_conflict(env) -> None:
    barrier = threading.Barrier(2)
    outcomes: list[int] = []
    lock = threading.Lock()

    def attempt(title: str) -> None:
        client = TestClient(env.app)
        body = create_body(title, span=timed_span_iso(16))
        barrier.wait()
        response = client.post("/api/calendar/proposals", json=body, headers=headers(request_id="req-race2"))
        with lock:
            outcomes.append(response.status_code)

    threads = [
        threading.Thread(target=attempt, args=("Alpha plan",)),
        threading.Thread(target=attempt, args=("Beta plan",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(outcomes) == [200, 409]  # winner independent of scheduling

    assert _count(env, "SELECT COUNT(*) FROM proposal_idempotency WHERE session_id=? AND request_id=?",
                  (SESSION, "req-race2")) == 1
    assert _count(env, "SELECT COUNT(*) FROM proposed_actions WHERE session_id=? AND request_id=?",
                  (SESSION, "req-race2")) == 1  # exactly one action; no orphan PENDING


# =========================================================================== #
# REMEDIATION - hardened reconciliation matching
# =========================================================================== #


def test_conflict_reconciliation_mismatch_on_attendees_fails(env) -> None:
    proposal = propose(env, create_body("Reconcile target"), request_id="req-mm1")
    action = proposal.json()["action"]

    def hook(body, params, apply):
        # An unrelated-but-similar event landed under our client id (same
        # title AND span - only the attendee set differs). Title/time alone
        # must NOT be treated as proof of our successful create.
        env.fake.events[body["id"]] = {
            "id": body["id"], "summary": "Reconcile target", "etag": "etag-x1",
            "start": {"dateTime": "2026-09-21T12:00:00+02:00", "timeZone": "Europe/Warsaw"},
            "end": {"dateTime": "2026-09-21T13:00:00+02:00", "timeZone": "Europe/Warsaw"},
            "attendees": [{"email": "mallory@example.com"}],
        }
        raise GoogleApiError(GoogleErrorCategory.CONFLICT, status_code=409, endpoint_kind="events")

    env.fake.post_hook = hook
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()
    assert body["action"]["status"] == "failed"
    assert body["result"]["error"]["code"] == "create_conflict_mismatch"


def test_timeout_reconciliation_mismatch_on_location_stays_unknown(env) -> None:
    proposal = propose(env, create_body("Timeout mismatch"), request_id="req-mm2")
    action = proposal.json()["action"]

    def hook(body, params, apply):
        # Something landed under our id with a DIFFERENT location (approved
        # had none); the response is then lost. Ambiguous + unverified state
        # must stay UNKNOWN - never verified success.
        env.fake.events[body["id"]] = {
            "id": body["id"], "summary": "Timeout mismatch", "etag": "etag-x2",
            "location": "Room of requirement: 7",
            "start": {"dateTime": "2026-09-21T12:00:00+02:00", "timeZone": "Europe/Warsaw"},
            "end": {"dateTime": "2026-09-21T13:00:00+02:00", "timeZone": "Europe/Warsaw"},
        }
        raise GoogleApiError(GoogleErrorCategory.TRANSPORT, endpoint_kind="events")

    env.fake.post_hook = hook
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()
    assert body["action"]["status"] == "unknown"  # never verified success
    assert body["result"]["status"] == "unknown"


def test_conflict_reconciliation_matches_case_insensitive_attendees(env) -> None:
    proposal = propose(
        env,
        create_body("Match me", attendees=[{"email": "Alice@Example.com"}, {"email": "bob@example.com"}]),
        request_id="req-mm3",
    )
    action = proposal.json()["action"]
    google_id = generate_google_event_id(action["id"], action["revision"])

    def hook(body, params, apply):
        apply()  # event lands (duplicate delivery), then the response is lost
        raise GoogleApiError(GoogleErrorCategory.CONFLICT, status_code=409, endpoint_kind="events")

    env.fake.post_hook = hook
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()
    # The applied event carries the approved attendee emails (provider may
    # case-normalize); identity comparison is case-insensitive -> reconciled.
    assert body["action"]["status"] == "succeeded"
    assert body["result"]["data"].get("reconciled") is True


# =========================================================================== #
# REMEDIATION - recurring MASTER detection (recurrence[] without id)
# =========================================================================== #


def test_recurring_master_recurrence_array_blocks_reschedule(env) -> None:
    seed_event(
        env, event_id="evt-1", etag="etag-v1",
        recurrence=["RRULE:FREQ=WEEKLY"],  # series master: no recurringEventId
    )
    action, confirmation = propose_and_approve(env, reschedule_body(), request_id="req-rm1")
    body = confirmation.json()
    assert body["action"]["status"] == "superseded"
    assert body["result"]["error"]["code"] == "recurring_series_ambiguous"
    assert env.fake.mutations == []  # zero PATCH


def test_recurring_master_blocks_agenda_update(env) -> None:
    seed_event(
        env, event_id="evt-1", etag="etag-v1", description="series master text",
        recurrence=["RRULE:FREQ=MONTHLY;BYMONTHDAY=5"],
    )
    action, confirmation = propose_and_approve(env, agenda_body(), request_id="req-rm2")
    body = confirmation.json()
    assert body["action"]["status"] == "superseded"
    assert body["result"]["error"]["code"] == "recurring_series_ambiguous"
    assert env.fake.mutations == []


def test_recurring_events_remain_readable(env) -> None:
    """Mutation eligibility only - A02-style reads of recurring events stay
    fully allowed (both instances and masters)."""
    seed_event(env, event_id="evt-master", summary="Weekly sync", recurrence=["RRULE:FREQ=WEEKLY"])
    seed_event(env, event_id="evt-inst", summary="Weekly sync instance", recurringEventId="series-1")
    for event_id in ("evt-master", "evt-inst"):
        result = env.executor.execute_read(
            ToolCall(id=f"c-{event_id}", name="calendar.get_event", arguments={"event_id": event_id})
        )
        assert result.status.value == "ok", event_id
    response = env.client.get("/api/calendar/events/evt-master")
    assert response.status_code == 200


# =========================================================================== #
# REMEDIATION - create identity freeze (durable ID is the only anchor)
# =========================================================================== #


def test_create_identity_freeze_follows_durable_id_not_response_id(env) -> None:
    proposal = propose(env, create_body("Identity freeze"), request_id="req-id1")
    action = proposal.json()["action"]
    durable_id = generate_google_event_id(action["id"], action["revision"])

    # POST applies correctly under the DURABLE client id, but the provider
    # response reports a DIFFERENT id - identity must never switch to it.
    env.fake.post_response_id_override = "unexpected-provider-id"
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()

    assert durable_id in env.fake.events                      # applied under durable id
    assert body["action"]["status"] == "succeeded"            # reconciled via durable GET
    assert body["result"]["data"].get("reconciled") is True
    assert body["result"]["data"]["event_id"] == durable_id   # never the returned id
    posts = [c for c in env.fake.calls if c[0] == "POST"]
    assert len(posts) == 1                                    # exactly one POST, ever
    # The unexpected id was never followed by any request.
    assert all("unexpected-provider-id" not in url for _, url in env.fake.calls)


def test_create_identity_freeze_unverifiable_mismatch_stays_unknown(env) -> None:
    proposal = propose(env, create_body("Ghost identity"), request_id="req-id2")
    action = proposal.json()["action"]

    def hook(body, params, apply):
        # POST response claims success under a foreign id while NOTHING is
        # verifiable under the durable id -> ambiguous, must stay UNKNOWN.
        return None

    env.fake.post_hook = hook
    env.fake.post_response_id_override = "unexpected-provider-id"
    confirmation = confirm(env, action["id"], action["revision"], action["arguments_digest"], "approve")
    body = confirmation.json()
    assert body["action"]["status"] == "unknown"

    # Replay of the UNKNOWN action performs ZERO new POSTs.
    posts_before = len([c for c in env.fake.calls if c[0] == "POST"])
    outcome = env.executor.execute(action["id"])
    assert outcome.action.status is ProposedActionStatus.UNKNOWN
    assert len([c for c in env.fake.calls if c[0] == "POST"]) == posts_before
