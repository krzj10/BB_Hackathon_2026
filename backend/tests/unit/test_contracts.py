"""A00 contract tests - three-layer gate, layer 1+2 wiring.

Layer 1 (this file): Pydantic semantic/runtime validation of contracts and
fixtures, plus config/health semantics.
Layer 2 (also this file): real Draft 2020-12 JSON Schema validation of the
generated bundle - meta-validation, $ref resolution, named-contract fixture
validation and structural negative cases.
Layer 3: TypeScript compile-time gate in contracts/tscheck (run via npm; also
exercised here as a pytest test when the pinned compiler is installed).

Structural schema validation vs semantic runtime validation:
- JSON Schema enforces structure: required fields, types, enums,
  additionalProperties:false, and discriminator presence at union boundaries.
- Pydantic additionally enforces semantics that JSON Schema does not encode:
  end-after-start intervals, HIGH approval invariants, Decision outcome
  consistency, EventEnvelope outer/inner correlation (runtime defense in depth;
  the generated TypeScript already rejects mismatches at compile time).

Run: python -m pytest backend/tests/unit/test_contracts.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError
from pydantic import TypeAdapter, ValidationError

from app.config import Settings, UnsafeEndpointConfigurationError
from app.contracts import api, domain
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "contracts" / "fixtures"
SCHEMA_PATH = REPO_ROOT / "contracts" / "schema" / "eva.schema.json"
TS_PATH = REPO_ROOT / "frontend" / "src" / "api" / "types.generated.ts"
TSCHECK_DIR = REPO_ROOT / "contracts" / "tscheck"

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
    "focus_stop_response.json": api.FocusStopResponse,
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
# Fixture bundle (Pydantic runtime validation)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURE_MODELS))
def test_valid_fixture_passes_python_validation(name: str) -> None:
    data = load_fixture(name)
    obj = validate(FIXTURE_MODELS[name], data)
    dumped = obj.model_dump(mode="json")
    validate(FIXTURE_MODELS[name], dumped)


def test_fixture_bundle_covers_contract_inventory() -> None:
    assert len(FIXTURE_MODELS) >= 25


# ---------------------------------------------------------------------------
# Invariants from the plan's exact acceptance example (semantic runtime layer)
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
    for forbidden in (
        "risk",
        "requires_approval",
        "voice_approval_allowed",
        "approved",
        "approval_channel",
    ):
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
# Claims, decisions, focus, active context
# ---------------------------------------------------------------------------


def test_fact_claim_requires_sources() -> None:
    with pytest.raises(ValidationError):
        domain.Claim(text="Spotkanie odbylo sie 2 wrzesnia.", kind="fact", source_ids=[])
    claim = domain.Claim(text="Spotkanie odbylo sie 2 wrzesnia.", kind="fact", source_ids=["s1"])
    assert claim.source_ids == ["s1"]
    domain.Claim(text="Byc moz opoznienie.", kind="inference")


def test_decision_outcome_invariants() -> None:
    base = load_fixture("decision_finance_pln_needs_review.json")

    with pytest.raises(ValidationError):
        domain.Decision.model_validate({**base, "outcome": "accept"})

    with pytest.raises(ValidationError):
        domain.Decision.model_validate({**base, "status": "resolved", "outcome": "accept"})

    with pytest.raises(ValidationError):
        domain.Decision.model_validate({**base, "status": "resolved"})

    deferred = domain.Decision.model_validate({**base, "status": "deferred"})
    assert deferred.outcome is None and deferred.outcome_recorded_at is None

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
    warsaw_mid = datetime.fromisoformat("2026-09-21T13:00:00+02:00")
    assert session.is_active(warsaw_mid) is True
    assert session.is_active(datetime.fromisoformat("2026-09-21T14:00:00+02:00")) is False
    stopped = session.model_copy(
        update={"stopped_at": datetime.fromisoformat("2026-09-21T12:30:00+02:00")}
    )
    assert stopped.is_active(warsaw_mid) is False
    assert "active" not in session.model_dump()


def test_focus_stop_response_summary_is_required() -> None:
    data = load_fixture("focus_stop_response.json")
    response = api.FocusStopResponse.model_validate(data)
    assert response.summary.focus_session_id == data["session"]["id"]
    with pytest.raises(ValidationError):
        api.FocusStopResponse.model_validate({"session": data["session"]})


def _context(**overrides) -> dict:
    base = {"mode": "general", "meeting": None, "decision_id": None, "section": None}
    base.update(overrides)
    return base


def test_active_context_valid_shapes() -> None:
    domain.ActiveContext.model_validate(_context())
    domain.ActiveContext.model_validate(
        _context(mode="meeting", meeting={"calendar_id": "primary", "event_id": "evt-1"})
    )
    domain.ActiveContext.model_validate(_context(mode="decision", decision_id="dec-1"))
    domain.ActiveContext.model_validate(
        _context(mode="meeting", meeting={"calendar_id": "primary", "event_id": "evt-1"}, section="risks")
    )


def test_active_context_contradictions_rejected() -> None:
    with pytest.raises(ValidationError):
        domain.ActiveContext.model_validate(_context(mode="meeting"))
    with pytest.raises(ValidationError):
        domain.ActiveContext.model_validate(_context(mode="decision"))
    with pytest.raises(ValidationError):
        domain.ActiveContext.model_validate(
            _context(mode="general", meeting={"calendar_id": "primary", "event_id": "evt-1"})
        )
    with pytest.raises(ValidationError):
        domain.ActiveContext.model_validate(_context(mode="meeting", decision_id="dec-1",
            meeting={"calendar_id": "primary", "event_id": "evt-1"}))
    with pytest.raises(ValidationError):
        domain.ActiveContext.model_validate(_context(mode="focus", decision_id="dec-1"))


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
# Settings contract (mandatory self-hosted route semantics)
# ---------------------------------------------------------------------------

ALLOWLISTED_URL = "https://infer.tailnet.example/v1"


def _configured_settings(**overrides) -> Settings:
    base = dict(
        EVA_LLM_BASE_URL=ALLOWLISTED_URL,
        EVA_LLM_MODEL="actual-server-model-id",
        EVA_LLM_ALLOWED_ORIGINS="https://infer.tailnet.example",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_settings_defaults_keep_cloud_disabled_and_startup_needs_no_cloud_keys() -> None:
    settings = Settings(_env_file=None)
    assert settings.eva_allow_cloud_inference is False
    assert settings.eva_allow_workspace_cloud_inference is False
    assert settings.missing_required_self_hosted_settings() == ["EVA_LLM_BASE_URL", "EVA_LLM_MODEL"]
    assert settings.self_hosted_configured is False


def test_self_hosted_route_requires_explicit_nonempty_allowlist() -> None:
    settings = Settings(
        _env_file=None, EVA_LLM_BASE_URL=ALLOWLISTED_URL, EVA_LLM_MODEL="model-x"
    )
    assert settings.self_hosted_configured is False
    assert any("EVA_LLM_ALLOWED_ORIGINS" in b for b in settings.self_hosted_route_blockers())


def test_self_hosted_route_rejects_endpoint_outside_allowlist() -> None:
    settings = Settings(
        _env_file=None,
        EVA_LLM_BASE_URL="https://not-allowlisted.example/v1",
        EVA_LLM_MODEL="model-x",
        EVA_LLM_ALLOWED_ORIGINS="https://infer.tailnet.example",
    )
    assert settings.self_hosted_configured is False
    assert any(
        "not-allowlisted.example" in b and "ALLOWED_ORIGINS" in b
        for b in settings.self_hosted_route_blockers()
    )


def test_self_hosted_route_usable_when_endpoint_allowlisted() -> None:
    settings = _configured_settings()
    assert settings.self_hosted_configured is True
    assert settings.self_hosted_route_blockers() == []
    assert settings.is_allowed_self_hosted_origin(ALLOWLISTED_URL)
    assert not settings.is_allowed_self_hosted_origin("https://api.openai.com/v1")
    assert not settings.is_allowed_self_hosted_origin("http://evil.example/")


def test_optional_fallback_must_also_be_allowlisted() -> None:
    unallowlisted = _configured_settings(
        EVA_LLM_FALLBACK_BASE_URL="https://elsewhere.example/v1",
        EVA_LLM_FALLBACK_MODEL="fallback-model",
    )
    assert unallowlisted.self_hosted_fallback_configured is False

    allowlisted = Settings(
        _env_file=None,
        EVA_LLM_BASE_URL=ALLOWLISTED_URL,
        EVA_LLM_MODEL="model-x",
        EVA_LLM_ALLOWED_ORIGINS="https://infer.tailnet.example, https://backup.tailnet.example",
        EVA_LLM_FALLBACK_BASE_URL="https://backup.tailnet.example/v1",
        EVA_LLM_FALLBACK_MODEL="fallback-model",
    )
    assert allowlisted.self_hosted_fallback_configured is True


def test_settings_origin_allowlist_parsing_and_rejection() -> None:
    settings = Settings(
        _env_file=None,
        EVA_LLM_ALLOWED_ORIGINS="http://100.64.0.2:8321/, https://infer.tailnet.example",
    )
    assert settings.eva_llm_allowed_origins == [
        "http://100.64.0.2:8321",
        "https://infer.tailnet.example",
    ]
    with pytest.raises(UnsafeEndpointConfigurationError):
        Settings(_env_file=None, EVA_LLM_ALLOWED_ORIGINS="ftp://nope")


# ---------------------------------------------------------------------------
# ExecutiveBriefing evidence integrity (claims must resolve to SourceRefs)
# ---------------------------------------------------------------------------


def _briefing_payload() -> dict:
    return load_fixture("briefing_acme_pl.json")


def test_briefing_fact_with_unresolvable_source_id_fails() -> None:
    data = _briefing_payload()
    data["open_topics"][0] = {
        "text": "Nieznany wątek",
        "kind": "fact",
        "source_ids": ["missing-source"],
    }
    with pytest.raises(ValidationError) as excinfo:
        domain.ExecutiveBriefing.model_validate(data)
    assert "missing-source" in str(excinfo.value)


def test_briefing_unsourced_inference_and_suggestion_remain_valid() -> None:
    data = _briefing_payload()
    data["open_topics"] = [{"text": "Możliwy wątek", "kind": "inference", "source_ids": []}]
    data["suggestions"] = [{"text": "Zaproponuj termin", "kind": "suggestion"}]
    briefing = domain.ExecutiveBriefing.model_validate(data)
    assert briefing.open_topics[0].source_ids == []


def test_briefing_claim_referencing_real_source_passes() -> None:
    data = _briefing_payload()
    real_id = data["sources"][0]["id"]
    data["risks"] = [{"text": "Ryzyko terminu", "kind": "inference", "source_ids": [real_id]}]
    briefing = domain.ExecutiveBriefing.model_validate(data)
    assert briefing.risks[0].source_ids == [real_id]


def test_secrets_never_appear_in_repr() -> None:
    settings = Settings(
        _env_file=None, google_client_secret="sup3r-secret", EVA_LLM_API_KEY="runtime-key"
    )
    text = repr(settings)
    assert "sup3r-secret" not in text
    assert "runtime-key" not in text


# ---------------------------------------------------------------------------
# Health endpoint (TestClient)
# ---------------------------------------------------------------------------


def test_health_starts_with_external_services_unavailable() -> None:
    app = create_app(Settings(_env_file=None))
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = api.HealthResponse.model_validate(response.json())
    assert payload.status is api.HealthStatus.UNAVAILABLE
    assert {"database", "llm", "llm_fallback", "google", "stt"} <= set(payload.components)
    assert payload.components["llm"].status == api.HealthStatus.UNAVAILABLE


def test_health_exposes_actual_provider_and_model_structurally() -> None:
    payload = api.HealthResponse.model_validate(
        TestClient(create_app(_configured_settings())).get("/api/health").json()
    )
    llm = payload.components["llm"]
    assert llm.status == api.HealthStatus.DEGRADED
    assert llm.provider == "openai_compatible"
    assert llm.model == "actual-server-model-id"

    default_llm = api.HealthResponse.model_validate(
        TestClient(create_app(Settings(_env_file=None))).get("/api/health").json()
    ).components["llm"]
    assert default_llm.model is None


def test_optional_fallback_absence_does_not_fail_overall_readiness() -> None:
    settings = _configured_settings(GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="secret")
    app = create_app(settings)

    class _ReadyStt:
        """Isolate the fallback-optionality assertion from real STT model
        availability (A05 health is now runtime-truthful, see test_voice)."""

        async def health(self):
            from app.contracts.domain import HealthStatus as DomainHealth
            from app.contracts.providers import ProviderHealth

            return ProviderHealth(status=DomainHealth.READY, provider="faster-whisper")

    app.state.stt_service = _ReadyStt()
    payload = api.HealthResponse.model_validate(TestClient(app).get("/api/health").json())
    # Optional fallback is truthfully unavailable...
    assert payload.components["llm_fallback"].status == api.HealthStatus.UNAVAILABLE
    # ...but overall readiness reflects required components only (degraded here
    # because the database/google adapters are not probed, never unavailable
    # for the optional fallback alone).
    assert payload.status == api.HealthStatus.DEGRADED


def test_health_response_never_serializes_secrets() -> None:
    settings = _configured_settings(
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="sup3r-google-secret",
        EVA_LLM_API_KEY="sup3r-llm-key",
    )
    body = TestClient(create_app(settings)).get("/api/health").text
    assert "sup3r-google-secret" not in body
    assert "sup3r-llm-key" not in body


# ---------------------------------------------------------------------------
# Layer 2: real Draft 2020-12 JSON Schema validation of the generated bundle
# ---------------------------------------------------------------------------

BUNDLE = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _schema_for(def_name: str) -> Draft202012Validator:
    return Draft202012Validator({**BUNDLE, "$ref": f"#/$defs/{def_name}"})


def _iter_nodes(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_nodes(item)


def test_bundle_passes_draft_2020_12_meta_validation() -> None:
    Draft202012Validator.check_schema(BUNDLE)


def test_all_internal_refs_resolve_and_no_private_defs_remain() -> None:
    defs = BUNDLE["$defs"]
    for node in _iter_nodes(BUNDLE):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                assert ref.startswith("#/$defs/"), f"unresolvable external-form ref: {ref}"
                assert ref.rsplit("/", 1)[-1] in defs, f"unresolved ref: {ref}"
    # Model entries must not keep private $defs whose refs point at the root.
    for name, schema in defs.items():
        if isinstance(schema, dict):
            assert "$defs" not in schema, f"{name} retains a private $defs block"


def test_bundle_contains_contract_inventory_and_boundaries() -> None:
    defs = BUNDLE["$defs"]
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
        # canonical union boundaries + frontend-facing reference alias
        "MeetingSpan",
        "CalendarProposalArguments",
        "CalendarProposalRequest",
        "EventPayload",
    ):
        assert required in defs, f"{required} missing from $defs"
    assert BUNDLE["x-eva-serialization"]["extra_fields"] == "forbid"


# Structural validation of representative fixtures against named contracts.
JSONSCHEMA_FIXTURE_CONTRACTS: dict[str, str] = {
    "meeting_acme_high.json": "Meeting",
    "briefing_acme_pl.json": "ExecutiveBriefing",
    "attention_finance_decision.json": "AttentionItem",
    "decision_finance_pln_needs_review.json": "Decision",
    "proposed_action_agenda_high.json": "ProposedAction",
    "calendar_proposal_create.json": "CalendarProposalArguments",
    "calendar_proposal_reschedule.json": "CalendarProposalArguments",
    "calendar_proposal_update_agenda.json": "CalendarProposalArguments",
    "event_envelope_attention_created.json": "EventEnvelope",
    "focus_stop_response.json": "FocusStopResponse",
    "health_response.json": "HealthResponse",
    "transcript_pl.json": "Transcript",
    "llm_settings_response.json": "LlmSettingsResponse",
}


@pytest.mark.parametrize("name", sorted(JSONSCHEMA_FIXTURE_CONTRACTS))
def test_fixtures_validate_against_named_json_schema(name: str) -> None:
    _schema_for(JSONSCHEMA_FIXTURE_CONTRACTS[name]).validate(load_fixture(name))


# Structural negative cases (schema-level, not Pydantic-semantic).
def test_json_schema_rejects_missing_required_field() -> None:
    data = load_fixture("meeting_acme_high.json")
    del data["title"]
    with pytest.raises(JsonSchemaValidationError):
        _schema_for("Meeting").validate(data)


def test_json_schema_rejects_extra_field() -> None:
    data = load_fixture("meeting_acme_high.json")
    data["invented_field"] = "x"
    with pytest.raises(JsonSchemaValidationError):
        _schema_for("Meeting").validate(data)


def test_json_schema_rejects_invalid_enum_value() -> None:
    data = load_fixture("meeting_acme_high.json")
    data["priority"] = "HIGH"
    with pytest.raises(JsonSchemaValidationError):
        _schema_for("Meeting").validate(data)


def test_json_schema_boundary_requires_meeting_span_discriminator() -> None:
    without_kind = {
        "start_date": "2026-09-21",
        "end_exclusive": "2026-09-22",
    }
    with pytest.raises(JsonSchemaValidationError):
        _schema_for("MeetingSpan").validate(without_kind)
    _schema_for("MeetingSpan").validate({"kind": "all_day", **without_kind})


def test_json_schema_boundary_requires_proposal_tool() -> None:
    data = load_fixture("calendar_proposal_create.json")
    without_tool = {k: v for k, v in data.items() if k != "tool"}
    with pytest.raises(JsonSchemaValidationError):
        _schema_for("CalendarProposalArguments").validate(without_tool)


def test_json_schema_rejects_wrong_discriminator_member_combination() -> None:
    reschedule = load_fixture("calendar_proposal_reschedule.json")
    with pytest.raises(JsonSchemaValidationError):
        # tool says reschedule but carries create-only "title" (and lacks the
        # agenda fields for update_agenda): no oneOf member can match.
        _schema_for("CalendarProposalArguments").validate({**reschedule, "title": "sneak-in"})


# Nested discriminated-union boundaries: removing the discriminator from a
# nested object that crosses a union boundary must fail BOTH the JSON Schema
# (via $ref to the strengthened named boundary) and Pydantic.
def test_json_schema_meeting_requires_nested_span_kind() -> None:
    data = load_fixture("meeting_acme_high.json")
    del data["span"]["kind"]
    with pytest.raises(JsonSchemaValidationError):
        _schema_for("Meeting").validate(data)
    with pytest.raises(ValidationError):
        domain.Meeting.model_validate(data)


def test_json_schema_calendar_create_requires_nested_span_kind() -> None:
    data = load_fixture("calendar_proposal_create.json")
    del data["span"]["kind"]
    with pytest.raises(JsonSchemaValidationError):
        _schema_for("CalendarProposalArguments").validate(data)
    with pytest.raises(ValidationError):
        CALENDAR_PROPOSAL_ADAPTER.validate_python(data)


def test_json_schema_calendar_reschedule_requires_nested_new_span_kind() -> None:
    data = load_fixture("calendar_proposal_reschedule.json")
    del data["new_span"]["kind"]
    with pytest.raises(JsonSchemaValidationError):
        _schema_for("CalendarProposalArguments").validate(data)
    with pytest.raises(ValidationError):
        CALENDAR_PROPOSAL_ADAPTER.validate_python(data)


def test_json_schema_event_envelope_requires_payload_type() -> None:
    data = load_fixture("event_envelope_attention_created.json")
    del data["payload"]["type"]  # outer type and all other fields stay valid
    with pytest.raises(JsonSchemaValidationError):
        _schema_for("EventEnvelope").validate(data)
    with pytest.raises(ValidationError):
        domain.EventEnvelope.model_validate(data)


def test_json_schema_nested_boundaries_reuse_named_definitions() -> None:
    """Nested usages must $ref the strengthened boundary, not embed a weaker
    inline duplicate of the same union."""
    defs = BUNDLE["$defs"]
    for def_name, prop in (
        ("Meeting", "span"),
        ("CalendarCreateEventArguments", "span"),
        ("CalendarRescheduleEventArguments", "new_span"),
        ("EventEnvelope", "payload"),
    ):
        assert defs[def_name]["properties"][prop] == {
            "$ref": f"#/$defs/{'MeetingSpan' if 'span' in prop else 'EventPayload'}"
        }, f"{def_name}.{prop} must reference the strengthened boundary definition"


def test_json_schema_envelope_correlation_is_runtime_only_by_design() -> None:
    """JSON Schema does not encode outer type == payload.type correlation.

    Documented boundary: the generated TypeScript rejects mismatches at compile
    time and Pydantic's model_validator rejects them at runtime (see
    test_event_envelope_type_must_match_payload_discriminator). No second
    handwritten validation engine is introduced here.
    """
    data = load_fixture("event_envelope_attention_created.json")
    mismatched = {**data, "type": "heartbeat"}
    _schema_for("EventEnvelope").validate(mismatched)  # passes schema...
    with pytest.raises(ValidationError):
        domain.EventEnvelope.model_validate(mismatched)  # ...fails Pydantic + TS


# ---------------------------------------------------------------------------
# Layer 3: generated artifacts - drift gate and TypeScript compile gate
# ---------------------------------------------------------------------------


def test_generated_artifacts_are_in_sync() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "export_contracts.py"), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_typescript_is_generated_and_excludes_provider_internals() -> None:
    text = TS_PATH.read_text(encoding="utf-8")
    assert "DO NOT EDIT" in text
    for marker in (
        "export interface Meeting {",
        "export interface ProposedAction {",
        "export interface EventEnvelopeBase {",
        "export interface FocusCompletionSummary {",
        'export type RequireDiscriminator<T, K extends keyof T> = T & Required<Pick<T, K>>;',
        '  | RequireDiscriminator<TimedSpan, "kind">',
        '  | RequireDiscriminator<CalendarCreateEventArguments, "tool">',
        "export type CalendarProposalRequest = CalendarProposalArguments;",
        '  | (EventEnvelopeBase & { type: "heartbeat"; payload: RequireDiscriminator<HeartbeatPayload, "type"> })',
    ):
        assert marker in text, f"missing in generated TS: {marker}"
    # Provider-only types must not leak into the frontend contract surface.
    assert "interface ChatMessage" not in text
    assert "AudioInput" not in text


_TSC = TSCHECK_DIR / "node_modules" / "typescript" / "bin" / "tsc"
_HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not _HAS_NODE or not _TSC.exists(), reason="install: npm --prefix contracts/tscheck install")
def test_typescript_contract_gate_compiles_clean() -> None:
    """Real tsc run over the generated contract + @ts-expect-error type tests.

    Proves discriminators are mandatory at union boundaries, EventEnvelope is
    type/payload correlated and narrowing works - a broken generator cannot
    pass because unused/misplaced @ts-expect-error directives fail compilation.
    """
    result = subprocess.run(
        [
            "node",
            str(_TSC),
            "--noEmit",
            "-p",
            str(TSCHECK_DIR / "tsconfig.json"),
        ],
        cwd=TSCHECK_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# ApprovalChallengeResponse (A03 remediation: challenge transport contract)
# ---------------------------------------------------------------------------


def _challenge_payload(**overrides) -> dict:
    payload = {
        "action_id": "act-demo-agenda-acme-001",
        "revision": 1,
        "arguments_digest": "sha256:demo-digest-agenda-0001",
        "challenge": "synthetic-challenge-token-not-a-secret",
        "expires_at": "2026-09-21T12:05:00+02:00",
    }
    payload.update(overrides)
    return payload


def test_approval_challenge_response_valid() -> None:
    model = api.ApprovalChallengeResponse.model_validate(_challenge_payload())
    assert model.action_id == "act-demo-agenda-acme-001"
    assert model.revision == 1
    assert model.expires_at.tzinfo is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"action_id": ""},
        {"revision": 0},
        {"arguments_digest": ""},
        {"challenge": ""},
        {"expires_at": "2026-09-21T12:05:00"},  # naive datetime rejected
        {"channel": "ui"},  # extra field forbidden; channel is server-derived
    ],
)
def test_approval_challenge_response_rejects(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        api.ApprovalChallengeResponse.model_validate(_challenge_payload(**overrides))
