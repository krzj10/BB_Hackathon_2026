"""A00 contract validation tests.

Covers: aware-datetime enforcement, interval ordering, strict extra-field
rejection, lowercase enums, integer money, HIGH approval boundaries, claim
provenance, decision outcome invariants, derived Focus activity, event
envelope typing, discriminated Calendar proposals, settings/cloud defaults,
generated-artifact drift and the health endpoint with external services
unavailable.

Run: python -m pytest backend/tests/unit/test_contracts.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app.config import Settings
from app.contracts import api, domain
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "contracts" / "fixtures"
SCHEMA_PATH = REPO_ROOT / "contracts" / "schema" / "eva.schema.json"
TS_PATH = REPO_ROOT / "frontend" / "src" / "api" / "types.generated.ts"

CALENDAR_PROPOSAL_ADAPTER = TypeAdapter(domain.CalendarProposalArguments)

# Every canonical fixture and the contract it must validate against.
FIXTURE_MODELS: dict[str, object] = {
    "meeting_acme_high.json": domain.Meeting,
    "meeting_team_sync_medium.json": domain.Meeting,
    "briefing_acme_pl.json": domain.ExecutiveBriefing,
    "attention_finance_decision.json": domain.AttentionItem,
    "decision_finance_pln_needs_review.json": domain.Decision,
    "decision_finance_pln_resolved.json": domain.Decision,
    "proposed_action_agenda_high.json": domain.ProposedAction,
    "approval_request_ui.json": domain.ApprovalRequest,
    "approval_receipt_ui.json": domain.ApprovalReceipt,
    "focus_session_active.json": domain.FocusSession,
    "focus_completion_summary.json": domain.FocusCompletionSummary,
    "tool_definition_calendar_update_agenda.json": domain.ToolDefinition,
    "tool_call_agenda.json": domain.ToolCall,
    "tool_result_agenda_ok.json": domain.ToolResult,
    "calendar_proposal_create.json": CALENDAR_PROPOSAL_ADAPTER,
    "calendar_proposal_reschedule.json": CALENDAR_PROPOSAL_ADAPTER,
    "calendar_proposal_update_agenda.json": CALENDAR_PROPOSAL_ADAPTER,
    "decision_outcome_proposal_request.json": api.DecisionOutcomeProposalRequest,
    "assistant_request_meeting.json": domain.AssistantRequest,
    "normalized_source_event_gmail.json": domain.NormalizedSourceEvent,
    "transcript_pl.json": domain.Transcript,
    "event_envelope_attention_created.json": domain.EventEnvelope,
    "health_response.json": api.HealthResponse,
    "llm_settings_response.json": api.LlmSettingsResponse,
}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def validate(model: object, data: dict):
    if isinstance(model, TypeAdapter):
        return model.validate_python(data)
    return model.model_validate(data)


# ---------------------------------------------------------------------------
# Fixture bundle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURE_MODELS))
def test_valid_fixture_passes_python_validation(name: str) -> None:
    data = load_fixture(name)
    obj = validate(FIXTURE_MODELS[name], data)
    # Serialization must round-trip through JSON mode and revalidate.
    dumped = obj.model_dump(mode="json")
    validate(FIXTURE_MODELS[name], dumped)


def test_fixture_bundle_covers_contract_inventory() -> None:
    assert len(FIXTURE_MODELS) >= 20


# ---------------------------------------------------------------------------
# Invariants from the plan's exact acceptance example
# ---------------------------------------------------------------------------


@pytest.fixture()
def valid_high_action() -> dict:
    return load_fixture("proposed_action_agenda_high.json")


def test_high_action_cannot_allow_voice(valid_high_action):
    payload = {**valid_high_action, "voice_approval_allowed": True}
    with pytest.raises(ValidationError):
        domain.ProposedAction.model_validate(payload)


def test_high_action_always_requires_approval(valid_high_action):
    payload = {**valid_high_action, "requires_approval": False}
    with pytest.raises(ValidationError):
        domain.ProposedAction.model_validate(payload)


def test_medium_action_may_allow_voice(valid_high_action):
    payload = {
        **valid_high_action,
        "risk": "medium",
        "requires_approval": True,
        "voice_approval_allowed": True,
    }
    action = domain.ProposedAction.model_validate(payload)
    assert action.voice_approval_allowed is True


def test_action_expiry_must_follow_creation(valid_high_action):
    payload = {**valid_high_action, "expires_at": valid_high_action["created_at"]}
    with pytest.raises(ValidationError):
        domain.ProposedAction.model_validate(payload)


# ---------------------------------------------------------------------------
# Aware datetimes, intervals, extra fields, enums, money
# ---------------------------------------------------------------------------


def test_naive_datetime_rejected() -> None:
    data = load_fixture("attention_finance_decision.json")
    data["received_at"] = "2026-09-21T11:30:00"  # no offset
    with pytest.raises(ValidationError):
        domain.AttentionItem.model_validate(data)


def test_timed_span_end_must_follow_start() -> None:
    with pytest.raises(ValidationError):
        domain.TimedSpan(
            start="2026-09-21T15:00:00+02:00",
            end="2026-09-21T14:00:00+02:00",
            timezone="Europe/Warsaw",
        )
    with pytest.raises(ValidationError):
        domain.TimedSpan(
            start="2026-09-21T15:00:00+02:00",
            end="2026-09-21T15:00:00+02:00",
            timezone="Europe/Warsaw",
        )


def test_all_day_span_requires_strictly_exclusive_end() -> None:
    with pytest.raises(ValidationError):
        domain.AllDaySpan(start_date="2026-09-21", end_exclusive="2026-09-21")
    span = domain.AllDaySpan(start_date="2026-09-21", end_exclusive="2026-09-22")
    assert span.kind == "all_day"


def test_meeting_span_discriminator_routes_correctly() -> None:
    meeting = domain.Meeting.model_validate(load_fixture("meeting_acme_high.json"))
    assert isinstance(meeting.span, domain.TimedSpan)
    data = load_fixture("meeting_acme_high.json")
    data["span"] = {"kind": "all_day", "start_date": "2026-09-21", "end_exclusive": "2026-09-22"}
    meeting = domain.Meeting.model_validate(data)
    assert isinstance(meeting.span, domain.AllDaySpan)


def test_extra_fields_are_rejected_everywhere() -> None:
    data = load_fixture("meeting_acme_high.json")
    data["invented_field"] = "x"
    with pytest.raises(ValidationError):
        domain.Meeting.model_validate(data)


def test_tool_call_carries_no_authoritative_risk_or_approval_fields() -> None:
    data = load_fixture("tool_call_agenda.json")
    for forbidden in ("risk", "requires_approval", "voice_approval_allowed", "approved"):
        payload = {**data, forbidden: True}
        with pytest.raises(ValidationError):
            domain.ToolCall.model_validate(payload)


def test_approval_request_channel_is_server_derived() -> None:
    data = load_fixture("approval_request_ui.json")
    data["channel"] = "ui"
    with pytest.raises(ValidationError):
        domain.ApprovalRequest.model_validate(data)


def test_enum_values_are_lowercase_and_strict() -> None:
    data = load_fixture("meeting_acme_high.json")
    assert data["priority"] == "high"
    data["priority"] = "HIGH"
    with pytest.raises(ValidationError):
        domain.Meeting.model_validate(data)


def test_money_requires_integer_minor_units() -> None:
    assert domain.Money(amount_minor_units=1240000, currency="PLN")
    for bad in (12400.0, 12400.5, "12400", True):
        with pytest.raises(ValidationError):
            domain.Money.model_validate({"amount_minor_units": bad, "currency": "PLN"})
    with pytest.raises(ValidationError):
        domain.Money.model_validate({"amount_minor_units": -1, "currency": "PLN"})
    with pytest.raises(ValidationError):
        domain.Money.model_validate({"amount_minor_units": 100, "currency": "pln"})


# ---------------------------------------------------------------------------
# Claims, decisions, focus
# ---------------------------------------------------------------------------


def test_fact_claim_requires_sources() -> None:
    with pytest.raises(ValidationError):
        domain.Claim(text="Spotkanie odbylo sie 2 wrzesnia.", kind="fact", source_ids=[])
    claim = domain.Claim(text="Spotkanie odbylo sie 2 wrzesnia.", kind="fact", source_ids=["s1"])
    assert claim.source_ids == ["s1"]
    # Inference and suggestion may carry no sources.
    domain.Claim(text="Byc moz opoznienie.", kind="inference")


def test_decision_outcome_invariants() -> None:
    base = load_fixture("decision_finance_pln_needs_review.json")

    outcome_without_resolution = {**base, "outcome": "accept"}
    with pytest.raises(ValidationError):
        domain.Decision.model_validate(outcome_without_resolution)

    outcome_without_timestamp = {
        **base,
        "status": "resolved",
        "outcome": "accept",
    }
    with pytest.raises(ValidationError):
        domain.Decision.model_validate(outcome_without_timestamp)

    resolved_without_outcome = {**base, "status": "resolved"}
    with pytest.raises(ValidationError):
        domain.Decision.model_validate(resolved_without_outcome)

    deferred = {**base, "status": "deferred"}
    decision = domain.Decision.model_validate(deferred)
    assert decision.outcome is None and decision.outcome_recorded_at is None

    resolved = domain.Decision.model_validate(load_fixture("decision_finance_pln_resolved.json"))
    assert resolved.outcome == domain.DecisionOutcome.ACCEPT


def test_decision_requires_linked_attention_item() -> None:
    base = load_fixture("decision_finance_pln_needs_review.json")
    with pytest.raises(ValidationError):
        domain.Decision.model_validate({**base, "attention_item_id": ""})


def test_financial_decision_stays_decision_required_when_urgent() -> None:
    item = domain.AttentionItem.model_validate(load_fixture("attention_finance_decision.json"))
    assert item.attention_type == domain.AttentionType.DECISION_REQUIRED
    assert item.urgent is True


def test_focus_session_active_is_derived_from_timestamps() -> None:
    session = domain.FocusSession.model_validate(load_fixture("focus_session_active.json"))
    assert session.is_active(datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)) is False
    warsaw_mid = datetime.fromisoformat("2026-09-21T13:00:00+02:00")
    assert session.is_active(warsaw_mid) is True
    assert session.is_active(datetime.fromisoformat("2026-09-21T14:00:00+02:00")) is False
    stopped = session.model_copy(
        update={"stopped_at": datetime.fromisoformat("2026-09-21T12:30:00+02:00")}
    )
    assert stopped.is_active(warsaw_mid) is False
    # 'active' is a derived method, never a serialized field.
    assert "active" not in session.model_dump()


# ---------------------------------------------------------------------------
# Calendar proposals and events
# ---------------------------------------------------------------------------


def test_calendar_proposal_discriminator_routing() -> None:
    create = CALENDAR_PROPOSAL_ADAPTER.validate_python(load_fixture("calendar_proposal_create.json"))
    resched = CALENDAR_PROPOSAL_ADAPTER.validate_python(
        load_fixture("calendar_proposal_reschedule.json")
    )
    agenda = CALENDAR_PROPOSAL_ADAPTER.validate_python(
        load_fixture("calendar_proposal_update_agenda.json")
    )
    assert isinstance(create, domain.CalendarCreateEventArguments)
    assert isinstance(resched, domain.CalendarRescheduleEventArguments)
    assert isinstance(agenda, domain.CalendarUpdateAgendaArguments)


def test_calendar_proposal_rejects_unknown_or_missing_tool() -> None:
    data = load_fixture("calendar_proposal_create.json")
    with pytest.raises(ValidationError):
        CALENDAR_PROPOSAL_ADAPTER.validate_python({k: v for k, v in data.items() if k != "tool"})
    with pytest.raises(ValidationError):
        CALENDAR_PROPOSAL_ADAPTER.validate_python({**data, "tool": "calendar.delete_event"})


def test_calendar_proposal_rejects_wrong_arguments_per_tool() -> None:
    reschedule = load_fixture("calendar_proposal_reschedule.json")
    # A reschedule must not carry create-only fields.
    with pytest.raises(ValidationError):
        CALENDAR_PROPOSAL_ADAPTER.validate_python({**reschedule, "title": "sneak-in"})


def test_event_envelope_schema_version_locked_to_1() -> None:
    data = load_fixture("event_envelope_attention_created.json")
    with pytest.raises(ValidationError):
        domain.EventEnvelope.model_validate({**data, "schema_version": 2})


def test_event_envelope_type_must_match_payload_discriminator() -> None:
    data = load_fixture("event_envelope_attention_created.json")
    with pytest.raises(ValidationError):
        domain.EventEnvelope.model_validate({**data, "type": "heartbeat"})


def test_event_envelope_valid_heartbeat_and_routing() -> None:
    envelope = domain.EventEnvelope.model_validate(
        {
            "schema_version": 1,
            "event_id": "evtw-demo-hb-001",
            "sequence": 1,
            "session_id": "sess-demo-001",
            "occurred_at": "2026-09-21T12:00:00+02:00",
            "type": "heartbeat",
            "payload": {"type": "heartbeat", "server_time": "2026-09-21T12:00:00+02:00"},
        }
    )
    assert isinstance(envelope.payload, domain.HeartbeatPayload)


# ---------------------------------------------------------------------------
# Settings contract
# ---------------------------------------------------------------------------


def test_settings_defaults_keep_cloud_disabled_and_startup_needs_no_cloud_keys() -> None:
    settings = Settings(_env_file=None)
    assert settings.eva_allow_cloud_inference is False
    assert settings.eva_allow_workspace_cloud_inference is False
    # No cloud key/model/endpoint required at startup; mandatory self-hosted
    # values are reported, not invented.
    assert settings.missing_required_self_hosted_settings() == ["EVA_LLM_BASE_URL", "EVA_LLM_MODEL"]
    assert settings.self_hosted_configured is False


def test_settings_origin_allowlist_parsing_and_matching() -> None:
    settings = Settings(
        _env_file=None,
        EVA_LLM_ALLOWED_ORIGINS="http://100.64.0.2:8321/, https://infer.tailnet.example",
        EVA_LLM_BASE_URL="https://infer.tailnet.example/v1",
        EVA_LLM_MODEL="actual-server-model-id",
    )
    assert settings.eva_llm_allowed_origins == [
        "http://100.64.0.2:8321",
        "https://infer.tailnet.example",
    ]
    assert settings.is_allowed_self_hosted_origin("https://infer.tailnet.example/v1/chat")
    assert not settings.is_allowed_self_hosted_origin("https://api.openai.com/v1")
    assert not settings.is_allowed_self_hosted_origin("http://evil.example/")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, EVA_LLM_ALLOWED_ORIGINS="ftp://nope")


def test_secrets_never_appear_in_repr() -> None:
    settings = Settings(
        _env_file=None, google_client_secret="sup3r-secret", EVA_LLM_API_KEY="runtime-key"
    )
    text = repr(settings)
    assert "sup3r-secret" not in text
    assert "runtime-key" not in text


# ---------------------------------------------------------------------------
# Generated artifacts (schema bundle + TypeScript)
# ---------------------------------------------------------------------------


def test_schema_bundle_contains_contract_inventory() -> None:
    bundle = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    models = bundle["models"]
    for required in (
        "Meeting",
        "ExecutiveBriefing",
        "AttentionItem",
        "Decision",
        "ProposedAction",
        "FocusSession",
        "FocusCompletionSummary",
        "EventEnvelope",
        "ToolDefinition",
        "HealthResponse",
        "ChatMessage",  # provider models are in the internal bundle
        "AudioInput",
    ):
        assert required in models, f"{required} missing from schema bundle"
    assert bundle["serialization"]["extra_fields"] == "forbid"


def test_typescript_is_generated_and_excludes_provider_internals() -> None:
    text = TS_PATH.read_text(encoding="utf-8")
    assert "DO NOT EDIT" in text
    for marker in (
        "export interface Meeting {",
        "export interface ProposedAction {",
        "export interface EventEnvelope {",
        "export interface FocusCompletionSummary {",
        "export type CalendarProposalRequest = CalendarCreateEventArguments | "
        "CalendarRescheduleEventArguments | CalendarUpdateAgendaArguments;",
        "export type MeetingSpan = TimedSpan | AllDaySpan;",
    ):
        assert marker in text, f"missing in generated TS: {marker}"
    # Provider-only types must not leak into the frontend contract surface.
    assert "interface ChatMessage" not in text
    assert "AudioInput" not in text


def test_generated_artifacts_are_in_sync() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "export_contracts.py"), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Health endpoint (TestClient) - must start with external services unavailable
# ---------------------------------------------------------------------------


def test_health_starts_with_external_services_unavailable() -> None:
    app = create_app(Settings(_env_file=None))
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = api.HealthResponse.model_validate(response.json())
    assert payload.status in (api.HealthStatus.DEGRADED, api.HealthStatus.UNAVAILABLE)
    assert {"database", "llm", "llm_fallback", "google", "stt"} <= set(payload.components)
    assert payload.components["llm"].status == api.HealthStatus.UNAVAILABLE


def test_health_reports_configured_llm_as_degraded_not_ready() -> None:
    settings = Settings(
        _env_file=None,
        EVA_LLM_BASE_URL="https://infer.tailnet.example/v1",
        EVA_LLM_MODEL="actual-server-model-id",
    )
    client = TestClient(create_app(settings))
    payload = api.HealthResponse.model_validate(client.get("/api/health").json())
    assert payload.components["llm"].status == api.HealthStatus.DEGRADED
