/* eslint-disable */
/**
 * EVA canonical contract types - version 2.0 (A00).
 *
 * GENERATED FILE - DO NOT EDIT.
 * Source of truth: backend/app/contracts/{domain,api,providers}.py
 * Regenerate: python scripts/export_contracts.py
 * Drift check: python scripts/export_contracts.py --check
 * Stream B must never edit this file (EVA_DEVELOPMENT_WORKFLOW.md section 6).
 */

/** ISO 8601 date-time with timezone offset. Null means unknown/not retrieved. */
export type DateTime = string;
/** Calendar date YYYY-MM-DD. All-day spans use an exclusive end date. */
export type DateOnly = string;
export type Email = string;
export type Uri = string;

export type MeetingPriority =
  | "low"
  | "medium"
  | "high";

export type ActionRisk =
  | "low"
  | "medium"
  | "high";

export type AttentionPriority =
  | "low"
  | "medium"
  | "high";

export type AttentionType =
  | "fyi"
  | "action_required"
  | "decision_required"
  | "urgent";

export type ClaimKind =
  | "fact"
  | "inference"
  | "suggestion";

export type ReasonOrigin =
  | "rule"
  | "preference"
  | "llm";

export type Language =
  | "en"
  | "pl";

export type VoiceState =
  | "idle"
  | "wake_listening"
  | "listening"
  | "transcribing"
  | "thinking"
  | "speaking"
  | "awaiting_approval"
  | "interrupted"
  | "error";

/** Lifecycle: pending -> approved -> executing -> succeeded/failed/unknown. Other terminal/non-executable states: rejected, expired, superseded. */
export type ProposedActionStatus =
  | "pending"
  | "approved"
  | "executing"
  | "succeeded"
  | "failed"
  | "unknown"
  | "rejected"
  | "expired"
  | "superseded";

export type DecisionStatus =
  | "needs_review"
  | "deferred"
  | "resolved"
  | "dismissed";

/** Internal outcome only. Recording it executes no payment, purchase, supplier or contract commitment. */
export type DecisionOutcome =
  | "accept"
  | "reject";

export type ToolEffect =
  | "read"
  | "local_write"
  | "external_write";

export type ToolResultStatus =
  | "ok"
  | "partial"
  | "error"
  | "unknown";

export type HealthStatus =
  | "ready"
  | "degraded"
  | "unavailable";

export type SourceKind =
  | "calendar_event"
  | "gmail_message"
  | "gmail_thread"
  | "contact"
  | "drive_file"
  | "policy_rule"
  | "attention_item"
  | "decision"
  | "other";

export type SourceSystem =
  | "gmail"
  | "calendar"
  | "chat"
  | "manual"
  | "other";

/** Notification delivery state of an AttentionItem (delivery only; never affects action authorization). */
export type DeliveryDecision =
  | "delivered"
  | "deferred"
  | "suppressed";

export type ApprovalChannel =
  | "ui"
  | "voice";

export type ApprovalChoice =
  | "approve"
  | "reject";

/** Google notification choice; part of the approved impact of a mutation. */
export type SendUpdates =
  | "all"
  | "external_only"
  | "none";

export type RetrievalStatus =
  | "complete"
  | "partial"
  | "unavailable";

export type AgendaSectionMode =
  | "add"
  | "update";

export type ActiveContextMode =
  | "general"
  | "meeting"
  | "attention_item"
  | "decision"
  | "focus";

export type EventType =
  | "voice_state_changed"
  | "transcript_ready"
  | "briefing_ready"
  | "attention_item_created"
  | "decision_created"
  | "decision_updated"
  | "focus_started"
  | "focus_ended"
  | "action_proposed"
  | "action_status_changed"
  | "inference_unavailable"
  | "heartbeat";

export interface Money {
  amount_minor_units: number;
  currency: string;
}

/** Opaque, resolvable evidence pointer. */
export interface SourceRef {
  id: string;
  kind: SourceKind;
  resource_id: string;
  title: string;
  url?: string | null;
  retrieved_at: DateTime;
}

/** A single briefing/attention statement with provenance. Invariant: historical facts require sources; inference and suggestion may carry none but must never be presented as sourced fact. */
export interface Claim {
  text: string;
  kind: ClaimKind;
  source_ids?: string[];
}

/** Deterministic or preference/LLM-origin explanation bound to evidence. */
export interface Reason {
  code: string;
  origin: ReasonOrigin;
  text: string;
  source_ids?: string[];
  policy_version?: string | null;
}

export interface Participant {
  email: Email;
  name?: string | null;
  company?: string | null;
  role?: string | null;
  internal?: boolean | null;
  previous_meeting_count?: number | null;
  previous_email_count?: number | null;
  previous_chat_count?: number | null;
}

export interface MeetingRef {
  calendar_id: string;
  event_id: string;
}

export interface TimedSpan {
  kind?: "timed";
  start: DateTime;
  end: DateTime;
  /** IANA timezone identifier */
  timezone: string;
}

export interface AllDaySpan {
  kind?: "all_day";
  start_date: DateOnly;
  end_exclusive: DateOnly;
}

export interface Meeting {
  ref: MeetingRef;
  etag?: string | null;
  title: string;
  span: TimedSpan | AllDaySpan;
  attendees?: Participant[];
  organizer_email?: Email | null;
  editable?: boolean;
  recurring_event_id?: string | null;
  description?: string | null;
  location?: string | null;
  agenda?: string | null;
  priority: MeetingPriority;
  priority_reasons?: Reason[];
  sources?: SourceRef[];
}

export interface ExecutiveBriefing {
  id: string;
  meeting: Meeting;
  language: Language;
  generated_at: DateTime;
  previous_interactions?: Claim[];
  open_topics?: Claim[];
  previous_decisions?: Claim[];
  risks?: Claim[];
  suggestions?: Claim[];
  spoken_summary: string;
  sources?: SourceRef[];
  retrieval_status: RetrievalStatus;
  retrieval_notes?: string[];
}

export interface AttentionItem {
  id: string;
  source: SourceSystem;
  source_id: string;
  sender_email: Email;
  title: string;
  content_preview: string;
  received_at: DateTime;
  attention_type: AttentionType;
  urgent?: boolean;
  priority: AttentionPriority;
  confidence: number;
  reasons?: Reason[];
  sources?: SourceRef[];
  deadline?: DateTime | null;
  related_meeting?: MeetingRef | null;
  decision_id?: string | null;
  delivery: DeliveryDecision;
  delivery_reasons?: Reason[];
}

/** Fed exclusively by decision_required Attention items. A recorded accept/reject is a local, audited outcome: it executes no payment, purchase, supplier or contract commitment. */
export interface Decision {
  id: string;
  attention_item_id: string;
  title: string;
  money?: Money | null;
  deadline?: DateTime | null;
  context?: Claim[];
  alternatives?: Claim[];
  risks?: Claim[];
  preference_conflicts?: Reason[];
  suggested_next_step?: string | null;
  risk: ActionRisk;
  status: DecisionStatus;
  outcome?: DecisionOutcome | null;
  outcome_recorded_at?: DateTime | null;
  proposed_action_id?: string | null;
  sources?: SourceRef[];
}

/** Immutable-by-revision mutation proposal bound to digest, policy version and expiry. The LLM cannot choose authoritative risk or issue receipts. */
export interface ProposedAction {
  id: string;
  session_id: string;
  request_id: string;
  revision: number;
  /** internal dotted tool name */
  tool: string;
  arguments: Record<string, unknown>;
  arguments_digest: string;
  summary: string;
  reason: string;
  impact: string;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  resource_version?: string | null;
  policy_version: string;
  risk: ActionRisk;
  requires_approval: boolean;
  voice_approval_allowed: boolean;
  created_at: DateTime;
  expires_at: DateTime;
  status: ProposedActionStatus;
}

/** Approval bound to action id, revision and arguments digest with a one-time challenge. Channel is derived server-side, never client-supplied. */
export interface ApprovalRequest {
  action_id: string;
  revision: number;
  arguments_digest: string;
  choice: ApprovalChoice;
  /** single-use challenge token */
  challenge: string;
}

/** Server-issued receipt; never model-generated. */
export interface ApprovalReceipt {
  id: string;
  action_id: string;
  revision: number;
  arguments_digest: string;
  channel: ApprovalChannel;
  approved_at: DateTime;
  policy_version: string;
}

/** One active session; 'active' is derived from timestamps only. */
export interface FocusSession {
  id: string;
  starts_at: DateTime;
  ends_at: DateTime;
  threshold: AttentionPriority;
  /** exact-address allow_interrupt exceptions only */
  sender_overrides?: Email[];
  policy_version: string;
  stopped_at?: DateTime | null;
}

/** Counts computed from stored, deduplicated Attention items received during the session; no new LLM-generated counts. */
export interface FocusCompletionSummary {
  focus_session_id: string;
  ended_at: DateTime;
  total_received: number;
  deferred_count: number;
  decision_count: number;
  action_count: number;
  fyi_count: number;
  attention_item_ids?: string[];
}

export interface ToolDefinition {
  /** internal dotted tool name */
  name: string;
  description: string;
  effect: ToolEffect;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  required_scopes?: string[];
  risk_floor: ActionRisk;
  timeout_seconds: number;
}

/** Model-produced call. Carries no authoritative risk or approval fields - extra-field rejection enforces that boundary. */
export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolError {
  code: string;
  message: string;
  retryable?: boolean;
}

export interface ToolResult {
  call_id: string;
  tool: string;
  status: ToolResultStatus;
  data?: Record<string, unknown> | null;
  error?: ToolError | null;
  sources?: SourceRef[];
  action_id?: string | null;
  duration_ms: number;
}

export interface CalendarCreateEventArguments {
  tool?: "calendar.create_event";
  title: string;
  span: TimedSpan | AllDaySpan;
  attendees?: Participant[];
  description?: string | null;
  location?: string | null;
  send_updates?: SendUpdates;
}

export interface CalendarRescheduleEventArguments {
  tool?: "calendar.reschedule_event";
  ref: MeetingRef;
  new_span: TimedSpan | AllDaySpan;
  expected_etag?: string | null;
  send_updates?: SendUpdates;
}

/** Adds/updates an EVA-delimited section within the event description, preserving all other text. */
export interface CalendarUpdateAgendaArguments {
  tool?: "calendar.update_agenda";
  ref: MeetingRef;
  mode: AgendaSectionMode;
  agenda_markdown: string;
  expected_etag?: string | null;
  send_updates?: SendUpdates;
}

/** Local outcome proposal for the Decision Inbox. Registered as local_write; it never invokes an external financial API and sends no message. */
export interface DecisionRecordOutcomeArguments {
  tool?: "decision.record_outcome";
  decision_id: string;
  outcome: DecisionOutcome;
}

export interface FocusStartArguments {
  tool?: "focus.start";
  duration_minutes: number;
  threshold: AttentionPriority;
  sender_overrides?: Email[];
}

export interface FocusStopArguments {
  tool?: "focus.stop";
}

export interface ActiveContext {
  mode: ActiveContextMode;
  meeting?: MeetingRef | null;
  decision_id?: string | null;
  section?: string | null;
}

export interface AssistantRequest {
  request_id: string;
  session_id: string;
  text: string;
  language: Language;
  active_context?: ActiveContext | null;
}

export interface NormalizedSourceEvent {
  source: SourceSystem;
  source_id: string;
  sender_email: Email;
  subject?: string | null;
  body?: string | null;
  received_at: DateTime;
  sources?: SourceRef[];
}

export interface Transcript {
  text: string;
  /** BCP-47 tag */
  language: string;
  language_confidence?: number | null;
  duration_ms: number;
  provider: string;
}

export interface VoiceStateChangedPayload {
  type?: "voice_state_changed";
  voice_state: VoiceState;
}

export interface TranscriptReadyPayload {
  type?: "transcript_ready";
  request_id: string;
  transcript: Transcript;
}

export interface BriefingReadyPayload {
  type?: "briefing_ready";
  briefing: ExecutiveBriefing;
}

export interface AttentionItemCreatedPayload {
  type?: "attention_item_created";
  item: AttentionItem;
}

export interface DecisionCreatedPayload {
  type?: "decision_created";
  decision: Decision;
}

export interface DecisionUpdatedPayload {
  type?: "decision_updated";
  decision: Decision;
}

export interface FocusStartedPayload {
  type?: "focus_started";
  session: FocusSession;
}

export interface FocusEndedPayload {
  type?: "focus_ended";
  session: FocusSession;
  summary: FocusCompletionSummary;
}

export interface ActionProposedPayload {
  type?: "action_proposed";
  action: ProposedAction;
}

export interface ActionStatusChangedPayload {
  type?: "action_status_changed";
  action_id: string;
  revision: number;
  status: ProposedActionStatus;
  action?: ProposedAction | null;
}

export interface InferenceUnavailablePayload {
  type?: "inference_unavailable";
  provider?: string | null;
  detail?: string | null;
}

export interface HeartbeatPayload {
  type?: "heartbeat";
  server_time: DateTime;
}

/** Persisted with monotonic per-session sequence; consumers deduplicate by event_id and reject stale request ids. */
export interface EventEnvelope {
  schema_version?: 1;
  event_id: string;
  sequence: number;
  session_id: string;
  request_id?: string | null;
  occurred_at: DateTime;
  type: EventType;
  payload: VoiceStateChangedPayload | TranscriptReadyPayload | BriefingReadyPayload | AttentionItemCreatedPayload | DecisionCreatedPayload | DecisionUpdatedPayload | FocusStartedPayload | FocusEndedPayload | ActionProposedPayload | ActionStatusChangedPayload | InferenceUnavailablePayload | HeartbeatPayload;
}

export interface HealthResponse {
  status: HealthStatus;
  server_time: DateTime;
  version: string;
  components: Record<string, ProviderHealth>;
}

export interface IntegrationStatus {
  name: string;
  connected: boolean;
  detail?: string | null;
  granted_scopes?: string[];
}

export interface IntegrationsResponse {
  integrations: IntegrationStatus[];
}

export interface TodayCalendarResponse {
  day: DateOnly;
  timezone: string;
  meetings: Meeting[];
  retrieval_status: RetrievalStatus;
  retrieval_notes?: string[];
}

export interface MeetingResponse {
  meeting: Meeting;
}

export interface ProposedActionResponse {
  action: ProposedAction;
}

export interface ActionConfirmResponse {
  action: ProposedAction;
  receipt?: ApprovalReceipt | null;
  result?: ToolResult | null;
}

export interface ActionResponse {
  action: ProposedAction;
  last_result?: ToolResult | null;
}

export interface TranscribeResponse {
  request_id: string;
  transcript: Transcript;
}

export interface AssistantMessageResponse {
  request_id: string;
  session_id: string;
  reply_text: string;
  language: Language;
  active_context?: ActiveContext | null;
  proposed_action?: ProposedAction | null;
  tool_results?: ToolResult[];
}

export interface BriefingRequest {
  meeting_ref: MeetingRef;
  language: Language;
}

export interface BriefingResponse {
  briefing: ExecutiveBriefing;
}

export interface AttentionListResponse {
  items: AttentionItem[];
}

/** Reads stored rule codes/evidence/policy version. Never newly invented LLM justification. */
export interface AttentionExplanationResponse {
  item_id: string;
  priority_reasons?: Reason[];
  delivery_reasons?: Reason[];
  policy_version: string;
  sources?: SourceRef[];
}

/** Invokes the same real ingestion path as polling. */
export interface CheckNowResponse {
  checked_count: number;
  duplicate_count: number;
  new_items?: AttentionItem[];
}

export interface DecisionListResponse {
  items: Decision[];
}

export interface DecisionResponse {
  decision: Decision;
}

/** User-initiated local outcome proposal. Executes no payment, purchase, supplier or contract commitment; for HIGH risk it requires explicit UI confirmation through the standard approval flow. */
export interface DecisionOutcomeProposalRequest {
  outcome: DecisionOutcome;
  session_id: string;
  request_id: string;
}

export interface DeferDecisionRequest {
  reason?: string | null;
}

export interface FocusStartRequest {
  duration_minutes: number;
  threshold: AttentionPriority;
  sender_overrides?: Email[];
}

export interface FocusStopRequest {

}

export interface FocusSessionResponse {
  session: FocusSession;
}

export interface FocusStopResponse {
  session: FocusSession;
  summary?: FocusCompletionSummary | null;
}

export interface FocusCurrentResponse {
  session?: FocusSession | null;
}

export interface FocusSummaryResponse {
  summary: FocusCompletionSummary;
}

/** Sanitized settings view. Secret values are never serialized - only presence flags. Optional cloud permissions default to false and remain false while no cloud adapter exists. */
export interface LlmSettingsResponse {
  provider: string;
  base_url?: string | null;
  model?: string | null;
  api_key_present?: boolean;
  fallback_base_url?: string | null;
  fallback_model?: string | null;
  fallback_api_key_present?: boolean;
  configured: boolean;
  allow_cloud_inference?: boolean;
  allow_workspace_cloud_inference?: boolean;
}

export interface LlmSettingsUpdateRequest {
  base_url?: string | null;
  model?: string | null;
  /** write-only; never echoed */
  api_key?: string | null;
  fallback_base_url?: string | null;
  fallback_model?: string | null;
  /** write-only; never echoed */
  fallback_api_key?: string | null;
}

export interface LlmTestConnectionResponse {
  health: ProviderHealth;
  latency_ms?: number | null;
  model?: string | null;
}

export interface DetectModelsResponse {
  models: LLMModelInfo[];
}

export interface LLMModelInfo {
  /** actual id exposed by the running server */
  id: string;
  display_name: string;
  supports_tools?: boolean | null;
  supports_structured_output?: boolean | null;
  context_window?: number | null;
}

export interface ProviderHealth {
  status: HealthStatus;
  detail?: string | null;
}

/** Discriminated on the "kind" field. */
export type MeetingSpan = TimedSpan | AllDaySpan;

/** Discriminated on the "tool" field. */
export type CalendarProposalRequest = CalendarCreateEventArguments | CalendarRescheduleEventArguments | CalendarUpdateAgendaArguments;

/** Discriminated on the "type" field. */
export type EventPayload = VoiceStateChangedPayload | TranscriptReadyPayload | BriefingReadyPayload | AttentionItemCreatedPayload | DecisionCreatedPayload | DecisionUpdatedPayload | FocusStartedPayload | FocusEndedPayload | ActionProposedPayload | ActionStatusChangedPayload | InferenceUnavailablePayload | HeartbeatPayload;

