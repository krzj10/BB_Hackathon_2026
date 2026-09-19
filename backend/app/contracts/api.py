"""EVA canonical API contracts (Stream A - Core Platform).

Response envelopes for every endpoint in docs/EVA_IMPLEMENTATION_PLAN.md v2.0
section 9, plus the sanitized settings/health shapes delivered to the frontend.
Typed WebSocket payloads live in contracts/domain.py (EventEnvelope).

Provider interfaces and raw credentials never cross this boundary: settings
responses expose presence flags only, never secret values.
"""

from __future__ import annotations

from datetime import date

from pydantic import AwareDatetime, EmailStr, Field, field_validator

from .domain import (
    ActiveContext,
    ApprovalReceipt,
    AttentionItem,
    AttentionPriority,
    CalendarProposalArguments,
    ContractModel,
    Decision,
    DecisionOutcome,
    ExecutiveBriefing,
    FocusCompletionSummary,
    FocusSession,
    HealthStatus,
    Language,
    Meeting,
    MeetingRef,
    ProposedAction,
    Reason,
    RetrievalStatus,
    SourceRef,
    ToolResult,
    Transcript,
)
from .providers import LLMModelInfo, ProviderHealth

# ---------------------------------------------------------------------------
# Health and integrations
# ---------------------------------------------------------------------------


class HealthResponse(ContractModel):
    status: HealthStatus
    server_time: AwareDatetime
    version: str = Field(min_length=1)
    components: dict[str, ProviderHealth]


class IntegrationStatus(ContractModel):
    name: str = Field(min_length=1)
    connected: bool
    detail: str | None = None
    granted_scopes: list[str] = Field(default_factory=list)


class IntegrationsResponse(ContractModel):
    integrations: list[IntegrationStatus]


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


class TodayCalendarResponse(ContractModel):
    day: date
    timezone: str = Field(min_length=1)
    meetings: list[Meeting]
    retrieval_status: RetrievalStatus
    retrieval_notes: list[str] = Field(default_factory=list)


class MeetingResponse(ContractModel):
    meeting: Meeting


# POST /api/calendar/proposals body is the discriminated union of create /
# reschedule / update_agenda arguments (domain.CalendarProposalArguments).
CalendarProposalRequest = CalendarProposalArguments


class ProposedActionResponse(ContractModel):
    action: ProposedAction


class ApprovalChallengeResponse(ContractModel):
    """Direct response of POST /api/actions/{action_id}/challenge (route is
    A04-owned; the contract is frozen here).

    The raw one-time challenge travels ONLY in this direct authenticated API
    response - never inside ProposedAction, ActionProposedPayload,
    ActionStatusChangedPayload, EventEnvelope, ToolResult or any other durable
    event/cache surface. There is deliberately no channel field: the approval
    channel stays server-derived at confirmation time."""

    action_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    arguments_digest: str = Field(min_length=1)
    challenge: str = Field(min_length=1)
    expires_at: AwareDatetime


class ActionConfirmResponse(ContractModel):
    action: ProposedAction
    receipt: ApprovalReceipt | None = None
    result: ToolResult | None = None


class ActionResponse(ContractModel):
    action: ProposedAction
    last_result: ToolResult | None = None


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------


class TranscribeResponse(ContractModel):
    request_id: str = Field(min_length=1)
    transcript: Transcript


# ---------------------------------------------------------------------------
# Assistant and briefing
# ---------------------------------------------------------------------------


class AssistantMessageResponse(ContractModel):
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    reply_text: str
    language: Language
    active_context: ActiveContext | None = None
    proposed_action: ProposedAction | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)


class BriefingRequest(ContractModel):
    meeting_ref: MeetingRef
    language: Language


class BriefingResponse(ContractModel):
    briefing: ExecutiveBriefing


# ---------------------------------------------------------------------------
# Attention
# ---------------------------------------------------------------------------


class AttentionListResponse(ContractModel):
    items: list[AttentionItem]


class AttentionExplanationResponse(ContractModel):
    """Reads stored rule codes/evidence/policy version. Never newly invented
    LLM justification."""

    item_id: str = Field(min_length=1)
    priority_reasons: list[Reason] = Field(default_factory=list)
    delivery_reasons: list[Reason] = Field(default_factory=list)
    policy_version: str = Field(min_length=1)
    sources: list[SourceRef] = Field(default_factory=list)


class CheckNowResponse(ContractModel):
    """Invokes the same real ingestion path as polling."""

    checked_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    new_items: list[AttentionItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


class DecisionListResponse(ContractModel):
    items: list[Decision]


class DecisionResponse(ContractModel):
    decision: Decision


class DecisionOutcomeProposalRequest(ContractModel):
    """User-initiated local outcome proposal. Executes no payment, purchase,
    supplier or contract commitment; for HIGH risk it requires explicit UI
    confirmation through the standard approval flow."""

    outcome: DecisionOutcome
    session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)


class DeferDecisionRequest(ContractModel):
    reason: str | None = None


# ---------------------------------------------------------------------------
# Focus
# ---------------------------------------------------------------------------


class FocusStartRequest(ContractModel):
    duration_minutes: int = Field(ge=1, le=720)
    threshold: AttentionPriority
    sender_overrides: list[EmailStr] = Field(default_factory=list)


class FocusStopRequest(ContractModel):
    pass


class FocusSessionResponse(ContractModel):
    session: FocusSession


class FocusStopResponse(ContractModel):
    """An explicit stop always returns the completion summary computed from
    persisted session items (same summary served after reconnect/restart via
    GET /api/focus/{id}/summary)."""

    session: FocusSession
    summary: FocusCompletionSummary


class FocusCurrentResponse(ContractModel):
    session: FocusSession | None = None


class FocusSummaryResponse(ContractModel):
    summary: FocusCompletionSummary


# ---------------------------------------------------------------------------
# Settings (sanitized; B owns the routes and implementation)
# ---------------------------------------------------------------------------


class LlmSettingsResponse(ContractModel):
    """Sanitized settings view. Secret values are never serialized - only
    presence flags. Optional cloud permissions default to false and remain
    false while no cloud adapter exists."""

    provider: str = Field(min_length=1)
    base_url: str | None = None
    model: str | None = None
    api_key_present: bool = False
    fallback_base_url: str | None = None
    fallback_model: str | None = None
    fallback_api_key_present: bool = False
    configured: bool
    allow_cloud_inference: bool = False
    allow_workspace_cloud_inference: bool = False


class LlmSettingsUpdateRequest(ContractModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = Field(default=None, description="write-only; never echoed")
    fallback_base_url: str | None = None
    fallback_model: str | None = None
    fallback_api_key: str | None = Field(default=None, description="write-only; never echoed")

    @field_validator("base_url", "fallback_base_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("endpoint must be an absolute http(s) URL")
        return value


class LlmTestConnectionResponse(ContractModel):
    health: ProviderHealth
    latency_ms: int | None = Field(default=None, ge=0)
    model: str | None = None


class DetectModelsResponse(ContractModel):
    models: list[LLMModelInfo]
