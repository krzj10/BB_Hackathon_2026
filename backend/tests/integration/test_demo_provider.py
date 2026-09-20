"""Demo data provider: deterministic fixtures, one-click reset, focus probes.

The whole point of demo mode is that ONLY the external Gmail boundary is
synthetic - so these tests assert the REAL pipeline's outcomes (rules, decision
projection) and that no Google surface is ever touched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.contracts.domain import AttentionPriority, AttentionType
from app.demo.fixtures import DEMO_MESSAGES, NOISE_MESSAGES
from app.main import create_app

SESSION = {"X-EVA-Session-ID": "sess-demo"}


def make_env(tmp_path) -> SimpleNamespace:
    settings = Settings(
        eva_db_url=f"sqlite:///{tmp_path / 'demo.db'}", eva_data_provider="demo"
    )
    app = create_app(settings)
    return SimpleNamespace(app=app, client=TestClient(app))


def _attention(env):
    return env.client.get("/api/attention", headers=SESSION).json()["items"]


def _decisions(env):
    return env.client.get("/api/decisions", headers=SESSION).json()["items"]


def test_reset_loads_the_canonical_dataset_deterministically(tmp_path) -> None:
    env = make_env(tmp_path)

    first = env.client.post("/api/demo/reset", headers=SESSION)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["provider"] == "demo"
    assert body["fixtures_loaded"] == len(DEMO_MESSAGES)
    assert body["attention_items"] == len(DEMO_MESSAGES)
    assert body["decisions"] == 4

    ids_first = sorted(item["source_id"] for item in _attention(env))
    # A second reset must restore the SAME canonical state, not accumulate.
    second = env.client.post("/api/demo/reset", headers=SESSION)
    assert second.status_code == 200
    assert second.json()["attention_items"] == len(DEMO_MESSAGES)
    assert sorted(item["source_id"] for item in _attention(env)) == ids_first


def test_decision_inbox_surfaces_the_executive_decisions(tmp_path) -> None:
    env = make_env(tmp_path)
    env.client.post("/api/demo/reset", headers=SESSION)

    by_source = {item["source_id"]: item for item in _attention(env)}
    decisions = {d["attention_item_id"]: d for d in _decisions(env)}

    expected_money = {
        "acme-pricing": 8_400_000,      # largest current PLN amount
        "workstation-supplier": 1_890_000,
        "travel-budget": 1_200_000,
    }
    for source_id, minor_units in expected_money.items():
        item = by_source[source_id]
        assert item["priority"] == AttentionPriority.HIGH.value
        assert item["attention_type"] == AttentionType.DECISION_REQUIRED.value
        decision = decisions[item["id"]]
        assert decision["money"]["amount_minor_units"] == minor_units
        assert decision["status"] == "needs_review"

    incident = by_source["prod-incident"]
    assert incident["urgent"] is True
    assert incident["priority"] == AttentionPriority.HIGH.value
    assert decisions[incident["id"]]["money"] is None


def test_noise_is_suppressed_to_low_fyi(tmp_path) -> None:
    env = make_env(tmp_path)
    env.client.post("/api/demo/reset", headers=SESSION)

    by_source = {item["source_id"]: item for item in _attention(env)}
    decision_item_ids = {d["attention_item_id"] for d in _decisions(env)}
    assert len(NOISE_MESSAGES) >= 15

    for message in NOISE_MESSAGES:
        item = by_source[message.fixture_id]
        assert item["priority"] == AttentionPriority.LOW.value, message.fixture_id
        assert item["attention_type"] == AttentionType.FYI.value, message.fixture_id
        assert item["id"] not in decision_item_ids


def test_injected_normal_message_does_not_escalate(tmp_path) -> None:
    env = make_env(tmp_path)
    env.client.post("/api/demo/reset", headers=SESSION)
    env.client.post(
        "/api/focus/start",
        json={"duration_minutes": 45, "threshold": "high", "sender_overrides": []},
        headers={**SESSION, "X-EVA-Request-ID": "focus-1"},
    )

    injected = env.client.post(
        "/api/demo/inject", json={"kind": "normal"}, headers=SESSION
    )
    assert injected.status_code == 200, injected.text
    result = injected.json()
    assert result["priority"] == AttentionPriority.LOW.value
    assert result["attention_type"] == AttentionType.FYI.value
    assert result["urgent"] is False
    # Nothing new entered the Decision Inbox.
    assert len(_decisions(env)) == 4


def test_injected_urgent_message_escalates(tmp_path) -> None:
    env = make_env(tmp_path)
    env.client.post("/api/demo/reset", headers=SESSION)
    env.client.post(
        "/api/focus/start",
        json={"duration_minutes": 45, "threshold": "high", "sender_overrides": []},
        headers={**SESSION, "X-EVA-Request-ID": "focus-2"},
    )

    injected = env.client.post(
        "/api/demo/inject", json={"kind": "urgent"}, headers=SESSION
    )
    assert injected.status_code == 200, injected.text
    result = injected.json()
    assert result["priority"] == AttentionPriority.HIGH.value
    assert result["urgent"] is True

    decisions = _decisions(env)
    assert len(decisions) == 5
    assert any(d["title"].startswith("PILNE:") for d in decisions)


def test_demo_mode_never_touches_google(tmp_path, monkeypatch) -> None:
    def _explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("demo mode must not build a GmailService")

    monkeypatch.setattr(main_module, "GmailService", _explode)
    env = make_env(tmp_path)

    result = env.client.post("/api/demo/reset", headers=SESSION)
    assert result.status_code == 200, result.text
    assert env.client.get("/api/attention", headers=SESSION).json()["items"]


def test_demo_routes_are_absent_in_default_mode(tmp_path) -> None:
    settings = Settings(eva_db_url=f"sqlite:///{tmp_path / 'real.db'}")
    client = TestClient(create_app(settings))

    assert client.post("/api/demo/reset", headers=SESSION).status_code == 404
    assert client.get("/api/demo/status", headers=SESSION).status_code == 404


def test_demo_reset_requires_a_session_header(tmp_path) -> None:
    env = make_env(tmp_path)
    assert env.client.post("/api/demo/reset").status_code == 400


def test_unknown_injection_kind_is_rejected(tmp_path) -> None:
    env = make_env(tmp_path)
    assert (
        env.client.post("/api/demo/inject", json={"kind": "chaos"}, headers=SESSION).status_code
        == 422
    )


# --------------------------------------------------------------------------- #
# Demo calendar: Today and the grounded briefing stop being empty without Google
# --------------------------------------------------------------------------- #


def test_demo_calendar_fills_today_with_a_real_conflict(tmp_path) -> None:
    env = make_env(tmp_path)

    today = env.client.get("/api/calendar/today", headers=SESSION)
    assert today.status_code == 200, today.text
    payload = today.json()
    assert payload["retrieval_status"] == "complete"
    ids = [meeting["ref"]["event_id"] for meeting in payload["meetings"]]
    assert "demo-acme-review" in ids and "demo-investor-call" in ids

    spans = {
        meeting["ref"]["event_id"]: (meeting["span"]["start"], meeting["span"]["end"])
        for meeting in payload["meetings"]
    }
    # The fixture pair must genuinely overlap: that is the conflict to show.
    acme_start, acme_end = spans["demo-acme-review"]
    investor_start, _investor_end = spans["demo-investor-call"]
    assert investor_start < acme_end

    deep_work = next(m for m in payload["meetings"] if m["ref"]["event_id"] == "demo-deep-work")
    assert "Protected focus block" in (deep_work["description"] or "")


def test_briefing_is_grounded_on_demo_email_and_calendar(tmp_path) -> None:
    env = make_env(tmp_path)
    env.client.post("/api/demo/reset", headers=SESSION)

    response = env.client.post(
        "/api/briefing/meeting",
        json={
            "meeting_ref": {"calendar_id": "primary", "event_id": "demo-acme-review"},
            "language": "pl",
        },
    )
    assert response.status_code == 200, response.text
    briefing = response.json()["briefing"]
    assert briefing["meeting"]["ref"]["event_id"] == "demo-acme-review"
    # Evidence joins BOTH surfaces: the calendar event and the linked threads.
    source_ids = [s["id"] for s in briefing["sources"]]
    assert any(sid.startswith("gmail:") for sid in source_ids), source_ids
    assert any(sid.startswith("demo-event:") for sid in source_ids), source_ids
    # No inference route exists in tests: the deterministic fallback is used and
    # says so honestly instead of pretending a model answered.
    notes = " ".join(briefing.get("retrieval_notes") or []).lower()
    assert "deterministic" in notes


def test_demo_calendar_refuses_writes(tmp_path) -> None:
    from app.google.calendar import CalendarReadError

    env = make_env(tmp_path)
    service = env.app.state.demo_calendar_service
    with pytest.raises(CalendarReadError):
        service.create_event(args=None, google_event_id="x")
    with pytest.raises(CalendarReadError):
        service.get_event(calendar_id="primary", event_id="no-such-event")
