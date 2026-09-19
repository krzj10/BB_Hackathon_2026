"""EVA canonical domain contracts (Stream A - Core Platform).

Authoritative source: docs/EVA_IMPLEMENTATION_PLAN.md v2.0 section 4.
A00 freezes these models; changes require a versioned fixture and a coordinated
consumer update (see docs/api-contracts.md).

Serialization rules (frozen):
- snake_case JSON field names, lowercase enum values, strict extra-field rejection.
- Time-aware ISO datetimes; dates for all-day events with exclusive end.
- Opaque source IDs and (calendar_id, event_id) meeting identity.
- Integer minor units plus currency for money; no floating-point financial values.
- Null means unknown/not retrieved; never invent zero relationship counts.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class ContractModel(BaseModel):
    """Base for every canonical contract: strict extra-field rejection."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Enums (lowercase values; distinct concepts stay distinct enum types)
# ---------------------------------------------------------------------------


class MeetingPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AttentionPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AttentionType(str, Enum):
    FYI = "fyi"
    ACTION_REQUIRED = "action_required"
    DECISION_REQUIRED = "decision_required"
    URGENT = "urgent"


class ClaimKind(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    SUGGESTION = "suggestion"


class ReasonOrigin(str, Enum):
    RULE = "rule"
    PREFERENCE = "preference"
    LLM = "llm"


class Language(str, Enum):
    EN = "en"
    PL = "pl"


class VoiceState(str, Enum):
    IDLE = "idle"
    WAKE_LISTENING = "wake_listening"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    AWAITING_APPROVAL = "awaiting_approval"
    INTERRUPTED = "interrupted"
    ERROR = "error"


class ProposedActionStatus(str, Enum):
    """Lifecycle: pending -> approved -> executing -> succeeded/failed/unknown.

    Other terminal/non-executable states: rejected, expired, superseded.
    """

    PENDING = "pending"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class DecisionStatus(str, Enum):
    NEEDS_REVIEW = "needs_review"
    DEFERRED = "deferred"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class DecisionOutcome(str, Enum):
    """Internal outcome only. Recording it executes no payment, purchase,
    supplier or contract commitment."""

    ACCEPT = "accept"
    REJECT = "reject"


class ToolEffect(str, Enum):
    READ = "read"
    LOCAL_WRITE = "local_write"
    EXTERNAL_WRITE = "external_write"


class ToolResultStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"
    UNKNOWN = "unknown"


class HealthStatus(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class SourceKind(str, Enum):
    CALENDAR_EVENT = "calendar_event"
    GMAIL_MESSAGE = "gmail_message"
    GMAIL_THREAD = "gmail_thread"
    CONTACT = "contact"
    DRIVE_FILE = "drive_file"
    POLICY_RULE = "policy_rule"
    ATTENTION_ITEM = "attention_item"
    DECISION = "decision"
    OTHER = "other"


class SourceSystem(str, Enum):
    GMAIL = "gmail"
    CALENDAR = "calendar"
    CHAT = "chat"
    MANUAL = "manual"
    OTHER = "other"


class DeliveryDecision(str, Enum):
    """Notification delivery state of an AttentionItem (delivery only; never
    affects action authorization)."""

    DELIVERED = "delivered"
    DEFERRED = "deferred"
    SUPPRESSED = "suppressed"


class ApprovalChannel(str, Enum):
    UI = "ui"
    VOICE = "voice"


class SendUpdates(str, Enum):
    """Google notification choice; part of the approved impact of a mutation."""

    ALL = "all"
    EXTERNAL_ONLY = "external_only"
    NONE = "none"


class RetrievalStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# Money (integer minor units; floats rejected outright)
# ---------------------------------------------------------------------------


class Money(ContractModel):
    amount_minor_units: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("amount_minor_units", mode="before")
    @classmethod
    def _require_integer_minor_units(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                "money must be integer minor units; floating-point and string amounts are rejected"
            )
        return value


# ---------------------------------------------------------------------------
# Evidence: sources, claims, reasons
# ---------------------------------------------------------------------------


class SourceRef(ContractModel):
    """Opaque, resolvable evidence pointer."""

    id: str = Field(min_length=1)
    kind: SourceKind
    resource_id: str = Field(min_length=1)
    title: str
    url: str | None = None
    retrieved_at: AwareDatetime


class Claim(ContractModel):
    """A single briefing/attention statement with provenance.

    Invariant: historical facts require sources; inference and suggestion may
    carry none but must never be presented as sourced fact.
    """

    text: str = Field(min_length=1)
    kind: ClaimKind
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _facts_require_sources(self) -> "Claim":
        if self.kind == ClaimKind.FACT and not self.source_ids:
            raise ValueError("historical facts require at least one source id")
        return self


class Reason(ContractModel):
    """Deterministic or preference/LLM-origin explanation bound to evidence."""

    code: str = Field(min_length=1)
    origin: ReasonOrigin
    text: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)
    policy_version: str | None = None


# ---------------------------------------------------------------------------
# Calendar domain
# ---------------------------------------------------------------------------


class Participant(ContractModel):
    email: EmailStr
    name: str | None = None
    company: str | None = None
    role: str | None = None
    internal: bool | None = None
    # Null means unknown/not retrieved - never invent zero counts.
    previous_meeting_count: int | None = Field(default=None, ge=0)
    previous_email_count: int | None = Field(default=None, ge=0)
    previous_chat_count: int | None = Field(default=None, ge=0)


class MeetingRef(ContractModel):
    calendar_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)


class TimedSpan(ContractModel):
    kind: Literal["timed"] = "timed"
    start: AwareDatetime
    end: AwareDatetime
    timezone: str = Field(min_length=1, description="IANA timezone identifier")

    @model_validator(mode="after")
    def _end_after_start(self) -> "TimedSpan":
        if self.end <= self.start:
            raise ValueError("timed span end must follow start")
        return self


class AllDaySpan(ContractModel):
    kind: Literal["all_day"] = "all_day"
    start_date: date
    end_exclusive: date

    @model_validator(mode="after")
    def _end_after_start(self) -> "AllDaySpan":
        if self.end_exclusive <= self.start_date:
            raise ValueError("all-day span requires end_exclusive strictly after start_date")
        return self


MeetingSpan = Annotated[Union[TimedSpan, AllDaySpan], Field(discriminator="kind")]


class Meeting(ContractModel):
    ref: MeetingRef
    etag: str | None = None
    title: str = Field(min_length=1)
    span: MeetingSpan
    attendees: list[Participant] = Field(default_factory=list)
    organizer_email: EmailStr | None = None
    editable: bool = False
    recurring_event_id: str | None = None
    description: str | None = None
    location: str | None = None
    agenda: str | None = None
    priority: MeetingPriority
    priority_reasons: list[Reason] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Executive briefing
# ---------------------------------------------------------------------------


class ExecutiveBriefing(ContractModel):
    id: str = Field(min_length=1)
    meeting: Meeting
    language: Language
    generated_at: AwareDatetime
    previous_interactions: list[Claim] = Field(default_factory=list)
    open_topics: list[Claim] = Field(default_factory=list)
    previous_decisions: list[Claim] = Field(default_factory=list)
    risks: list[Claim] = Field(default_factory=list)
    suggestions: list[Claim] = Field(default_factory=list)
    spoken_summary: str = Field(min_length=1)
    sources: list[SourceRef] = Field(default_factory=list)
    retrieval_status: RetrievalStatus
    retrieval_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _claim_sources_resolve(self) -> "ExecutiveBriefing":
        """Evidence integrity: every source_id referenced by any Claim in this
        briefing must resolve to an actual SourceRef of the briefing's own
        evidence (briefing sources plus the meeting's sources). Unsourced
        inference/suggestion claims remain valid; unknown ids never."""
        valid_ids = {source.id for source in self.sources} | {
            source.id for source in self.meeting.sources
        }
        collections = (
            ("previous_interactions", self.previous_interactions),
            ("open_topics", self.open_topics),
            ("previous_decisions", self.previous_decisions),
            ("risks", self.risks),
            ("suggestions", self.suggestions),
        )
        for field_name, claims in collections:
            for claim in claims:
                unknown = [sid for sid in claim.source_ids if sid not in valid_ids]
                if unknown:
                    raise ValueError(
                        f"{field_name}: claim source_ids {unknown} do not resolve to "
                        "any SourceRef in this briefing's evidence"
                    )
        return self


# ---------------------------------------------------------------------------
# Attention, Decisions
# ---------------------------------------------------------------------------


class AttentionItem(ContractModel):
    id: str = Field(min_length=1)
    source: SourceSystem
    source_id: str = Field(min_length=1)
    sender_email: EmailStr
    title: str
    content_preview: str
    received_at: AwareDatetime
    attention_type: AttentionType
    urgent: bool = False
    priority: AttentionPriority
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[Reason] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    deadline: AwareDatetime | None = None
    related_meeting: MeetingRef | None = None
    decision_id: str | None = None
    delivery: DeliveryDecision
    delivery_reasons: list[Reason] = Field(default_factory=list)


class Decision(ContractModel):
    """Fed exclusively by decision_required Attention items.

    A recorded accept/reject is a local, audited outcome: it executes no
    payment, purchase, supplier or contract commitment.
    """

    id: str = Field(min_length=1)
    attention_item_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    money: Money | None = None
    deadline: AwareDatetime | None = None
    context: list[Claim] = Field(default_factory=list)
    alternatives: list[Claim] = Field(default_factory=list)
    risks: list[Claim] = Field(default_factory=list)
    preference_conflicts: list[Reason] = Field(default_factory=list)
    suggested_next_step: str | None = None
    risk: ActionRisk
    status: DecisionStatus
    outcome: DecisionOutcome | None = None
    outcome_recorded_at: AwareDatetime | None = None
    proposed_action_id: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _outcome_consistency(self) -> "Decision":
        if (self.outcome is None) != (self.outcome_recorded_at is None):
            raise ValueError("outcome and outcome_recorded_at must be set together")
        if self.outcome is not None and self.status != DecisionStatus.RESOLVED:
            raise ValueError("a recorded outcome requires status=resolved")
        if self.status == DecisionStatus.RESOLVED and self.outcome is None:
            raise ValueError("status=resolved requires a recorded accept/reject outcome")
        return self


# ---------------------------------------------------------------------------
# Proposed actions, approval, execution
# ---------------------------------------------------------------------------


class ProposedAction(ContractModel):
    """Immutable-by-revision mutation proposal bound to digest, policy version
    and expiry. The LLM cannot choose authoritative risk or issue receipts."""

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    tool: str = Field(min_length=1, description="internal dotted tool name")
    arguments: dict[str, Any]
    arguments_digest: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    resource_version: str | None = None
    policy_version: str = Field(min_length=1)
    risk: ActionRisk
    requires_approval: bool
    voice_approval_allowed: bool
    created_at: AwareDatetime
    expires_at: AwareDatetime
    status: ProposedActionStatus

    @model_validator(mode="after")
    def _risk_boundaries(self) -> "ProposedAction":
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")
        if self.risk == ActionRisk.HIGH:
            if not self.requires_approval:
                raise ValueError("HIGH-risk actions always require approval")
            if self.voice_approval_allowed:
                raise ValueError("HIGH-risk actions never allow voice approval")
        return self


class ApprovalChoice(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalRequest(ContractModel):
    """Approval bound to action id, revision and arguments digest with a
    one-time challenge. Channel is derived server-side, never client-supplied."""

    action_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    arguments_digest: str = Field(min_length=1)
    choice: ApprovalChoice
    challenge: str = Field(min_length=1, description="single-use challenge token")


class ApprovalReceipt(ContractModel):
    """Server-issued receipt; never model-generated."""

    id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    arguments_digest: str = Field(min_length=1)
    channel: ApprovalChannel
    approved_at: AwareDatetime
    policy_version: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Focus
# ---------------------------------------------------------------------------


class FocusSession(ContractModel):
    """One active session; 'active' is derived from timestamps only."""

    id: str = Field(min_length=1)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    threshold: AttentionPriority
    sender_overrides: list[EmailStr] = Field(
        default_factory=list, description="exact-address allow_interrupt exceptions only"
    )
    policy_version: str = Field(min_length=1)
    stopped_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _end_after_start(self) -> "FocusSession":
        if self.ends_at <= self.starts_at:
            raise ValueError("focus session ends_at must follow starts_at")
        if self.stopped_at is not None and self.stopped_at < self.starts_at:
            raise ValueError("stopped_at cannot precede starts_at")
        return self

    def is_active(self, now: AwareDatetime) -> bool:
        if self.stopped_at is not None:
            return False
        return self.starts_at <= now < self.ends_at


class FocusCompletionSummary(ContractModel):
    """Counts computed from stored, deduplicated Attention items received
    during the session; no new LLM-generated counts."""

    focus_session_id: str = Field(min_length=1)
    ended_at: AwareDatetime
    total_received: int = Field(ge=0)
    deferred_count: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    action_count: int = Field(ge=0)
    fyi_count: int = Field(ge=0)
    attention_item_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tools and execution boundary
# ---------------------------------------------------------------------------


class ToolDefinition(ContractModel):
    name: str = Field(min_length=1, description="internal dotted tool name")
    description: str = Field(min_length=1)
    effect: ToolEffect
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_scopes: list[str] = Field(default_factory=list)
    risk_floor: ActionRisk
    timeout_seconds: int = Field(gt=0)


class ToolCall(ContractModel):
    """Model-produced call. Carries no authoritative risk or approval fields -
    extra-field rejection enforces that boundary."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class ToolError(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False


class ToolResult(ContractModel):
    call_id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    status: ToolResultStatus
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    action_id: str | None = None
    duration_ms: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Discriminated Calendar proposal arguments (all mandatory mutation types)
# ---------------------------------------------------------------------------


class CalendarCreateEventArguments(ContractModel):
    tool: Literal["calendar.create_event"] = "calendar.create_event"
    title: str = Field(min_length=1)
    span: MeetingSpan
    attendees: list[Participant] = Field(default_factory=list)
    description: str | None = None
    location: str | None = None
    send_updates: SendUpdates = SendUpdates.NONE


class CalendarRescheduleEventArguments(ContractModel):
    tool: Literal["calendar.reschedule_event"] = "calendar.reschedule_event"
    ref: MeetingRef
    new_span: MeetingSpan
    expected_etag: str | None = None
    send_updates: SendUpdates = SendUpdates.ALL


class AgendaSectionMode(str, Enum):
    ADD = "add"
    UPDATE = "update"


class CalendarUpdateAgendaArguments(ContractModel):
    """Adds/updates an EVA-delimited section within the event description,
    preserving all other text."""

    tool: Literal["calendar.update_agenda"] = "calendar.update_agenda"
    ref: MeetingRef
    mode: AgendaSectionMode
    agenda_markdown: str = Field(min_length=1)
    expected_etag: str | None = None
    send_updates: SendUpdates = SendUpdates.NONE


CalendarProposalArguments = Annotated[
    Union[
        CalendarCreateEventArguments,
        CalendarRescheduleEventArguments,
        CalendarUpdateAgendaArguments,
    ],
    Field(discriminator="tool"),
]


class DecisionRecordOutcomeArguments(ContractModel):
    """Local outcome proposal for the Decision Inbox. Registered as
    local_write; it never invokes an external financial API and sends no
    message."""

    tool: Literal["decision.record_outcome"] = "decision.record_outcome"
    decision_id: str = Field(min_length=1)
    outcome: DecisionOutcome


class FocusStartArguments(ContractModel):
    tool: Literal["focus.start"] = "focus.start"
    duration_minutes: int = Field(ge=1, le=720)
    threshold: AttentionPriority
    sender_overrides: list[EmailStr] = Field(default_factory=list)


class FocusStopArguments(ContractModel):
    tool: Literal["focus.stop"] = "focus.stop"


# ---------------------------------------------------------------------------
# Assistant context and requests
# ---------------------------------------------------------------------------


class ActiveContextMode(str, Enum):
    GENERAL = "general"
    MEETING = "meeting"
    ATTENTION_ITEM = "attention_item"
    DECISION = "decision"
    FOCUS = "focus"


class ActiveContext(ContractModel):
    mode: ActiveContextMode
    meeting: MeetingRef | None = None
    decision_id: str | None = None
    section: str | None = None

    @model_validator(mode="after")
    def _fields_agree_with_mode(self) -> "ActiveContext":
        if self.mode == ActiveContextMode.MEETING and self.meeting is None:
            raise ValueError("mode=meeting requires a meeting reference")
        if self.mode == ActiveContextMode.DECISION and self.decision_id is None:
            raise ValueError("mode=decision requires a decision_id")
        if self.meeting is not None and self.mode != ActiveContextMode.MEETING:
            raise ValueError("meeting may only be populated when mode=meeting")
        if self.decision_id is not None and self.mode != ActiveContextMode.DECISION:
            raise ValueError("decision_id may only be populated when mode=decision")
        return self


class AssistantRequest(ContractModel):
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: Language
    active_context: ActiveContext | None = None


# ---------------------------------------------------------------------------
# Ingestion input (not a second Attention model)
# ---------------------------------------------------------------------------


class NormalizedSourceEvent(ContractModel):
    source: SourceSystem
    source_id: str = Field(min_length=1)
    sender_email: EmailStr
    subject: str | None = None
    body: str | None = Field(default=None, max_length=20_000)
    received_at: AwareDatetime
    sources: list[SourceRef] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------


class Transcript(ContractModel):
    text: str
    language: str = Field(min_length=2, description="BCP-47 tag")
    language_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    duration_ms: int = Field(ge=0)
    provider: str = Field(min_length=1)


class AudioInput(ContractModel):
    """Backend-only: normalized 16 kHz mono PCM WAV bytes."""

    wav_bytes: bytes
    sample_rate_hz: Literal[16000] = 16000
    channels: Literal[1] = 1


# ---------------------------------------------------------------------------
# WebSocket event envelope (typed payloads, discriminated by type)
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    VOICE_STATE_CHANGED = "voice_state_changed"
    TRANSCRIPT_READY = "transcript_ready"
    BRIEFING_READY = "briefing_ready"
    ATTENTION_ITEM_CREATED = "attention_item_created"
    DECISION_CREATED = "decision_created"
    DECISION_UPDATED = "decision_updated"
    FOCUS_STARTED = "focus_started"
    FOCUS_ENDED = "focus_ended"
    ACTION_PROPOSED = "action_proposed"
    ACTION_STATUS_CHANGED = "action_status_changed"
    INFERENCE_UNAVAILABLE = "inference_unavailable"
    HEARTBEAT = "heartbeat"


class VoiceStateChangedPayload(ContractModel):
    type: Literal["voice_state_changed"] = "voice_state_changed"
    voice_state: VoiceState


class TranscriptReadyPayload(ContractModel):
    type: Literal["transcript_ready"] = "transcript_ready"
    request_id: str = Field(min_length=1)
    transcript: Transcript


class BriefingReadyPayload(ContractModel):
    type: Literal["briefing_ready"] = "briefing_ready"
    briefing: ExecutiveBriefing


class AttentionItemCreatedPayload(ContractModel):
    type: Literal["attention_item_created"] = "attention_item_created"
    item: AttentionItem


class DecisionCreatedPayload(ContractModel):
    type: Literal["decision_created"] = "decision_created"
    decision: Decision


class DecisionUpdatedPayload(ContractModel):
    type: Literal["decision_updated"] = "decision_updated"
    decision: Decision


class FocusStartedPayload(ContractModel):
    type: Literal["focus_started"] = "focus_started"
    session: FocusSession


class FocusEndedPayload(ContractModel):
    type: Literal["focus_ended"] = "focus_ended"
    session: FocusSession
    summary: FocusCompletionSummary


class ActionProposedPayload(ContractModel):
    type: Literal["action_proposed"] = "action_proposed"
    action: ProposedAction


class ActionStatusChangedPayload(ContractModel):
    type: Literal["action_status_changed"] = "action_status_changed"
    action_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    status: ProposedActionStatus
    action: ProposedAction | None = None


class InferenceUnavailablePayload(ContractModel):
    type: Literal["inference_unavailable"] = "inference_unavailable"
    provider: str | None = None
    detail: str | None = None


class HeartbeatPayload(ContractModel):
    type: Literal["heartbeat"] = "heartbeat"
    server_time: AwareDatetime


EventPayload = Annotated[
    Union[
        VoiceStateChangedPayload,
        TranscriptReadyPayload,
        BriefingReadyPayload,
        AttentionItemCreatedPayload,
        DecisionCreatedPayload,
        DecisionUpdatedPayload,
        FocusStartedPayload,
        FocusEndedPayload,
        ActionProposedPayload,
        ActionStatusChangedPayload,
        InferenceUnavailablePayload,
        HeartbeatPayload,
    ],
    Field(discriminator="type"),
]


class EventEnvelope(ContractModel):
    """Persisted with monotonic per-session sequence; consumers deduplicate by
    event_id and reject stale request ids."""

    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    session_id: str = Field(min_length=1)
    request_id: str | None = None
    occurred_at: AwareDatetime
    type: EventType
    payload: EventPayload

    @model_validator(mode="after")
    def _type_matches_payload(self) -> "EventEnvelope":
        if self.type.value != self.payload.type:
            raise ValueError("envelope type must match payload discriminator")
        return self
