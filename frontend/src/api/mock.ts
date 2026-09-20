import { ApiError, type EvaClient, type TranscribeAudioRequest } from "./client";
import type {
  AttentionItem,
  AttentionListResponse,
  AttentionExplanationResponse,
  Decision,
  DecisionListResponse,
  DecisionResponse,
  FocusCurrentResponse,
  FocusSession,
  FocusCompletionSummary,
  FocusSessionResponse,
  Meeting,
  TodayCalendarResponse,
  ExecutiveBriefing,
  ProposedAction,
  ActionResponse,
  ActionConfirmResponse,
  ToolResult,
  BriefingRequest,
  BriefingResponse,
  FocusStartRequest,
  FocusStopRequest,
  DecisionOutcomeProposalRequest,
  DeferDecisionRequest,
  ApprovalRequest,
  ApprovalChallengeResponse,
  DecisionOutcome,
  LlmSettingsResponse,
  LlmSettingsUpdateRequest,
  LlmTestConnectionResponse,
  DetectModelsResponse,
  Transcript,
  TranscribeResponse,
} from "./types.generated";
import acmeFixture from "../../../contracts/fixtures/meeting_acme_high.json";
import teamSyncFixture from "../../../contracts/fixtures/meeting_team_sync_medium.json";
import attentionFixture from "../../../contracts/fixtures/attention_finance_decision.json";
import decisionFixture from "../../../contracts/fixtures/decision_finance_pln_needs_review.json";
import decisionResolvedFixture from "../../../contracts/fixtures/decision_finance_pln_resolved.json";
import focusSessionFixture from "../../../contracts/fixtures/focus_session_active.json";
import focusCompletionFixture from "../../../contracts/fixtures/focus_completion_summary.json";
import briefingFixture from "../../../contracts/fixtures/briefing_acme_pl.json";
import proposedActionFixture from "../../../contracts/fixtures/proposed_action_agenda_high.json";
import toolResultFixture from "../../../contracts/fixtures/tool_result_agenda_ok.json";
import llmSettingsFixture from "../../../contracts/fixtures/llm_settings_response.json";
import transcriptPlFixture from "../../../contracts/fixtures/transcript_pl.json";

export type MockMode =
  | "fixtures"
  | "empty"
  | "error"
  | "pending"
  | "focus-active"
  | "focus-completed"
  | "action-pending"
  | "action-high-confirmation"
  | "action-unknown"
  | "decision-resolved"
  | "partial-evidence";

/**
 * The A00 fixtures are validated by the backend/JSON-Schema/tscheck gates in
 * the same commit as the generated types; this boundary only repairs the
 * widened JSON primitive types. A failure here means fixtures and generated
 * contracts drifted apart.
 */
function canonical<T>(value: unknown, name: string): T {
  if (value === null || typeof value !== "object") {
    throw new Error(`canonical fixture ${name} is malformed`);
  }
  return value as T;
}

const acmeMeeting = canonical<Meeting>(acmeFixture, "meeting_acme_high");
const teamSyncMeeting = canonical<Meeting>(teamSyncFixture, "meeting_team_sync_medium");
const financeAttention = canonical<AttentionItem>(attentionFixture, "attention_finance_decision");
const financeDecision = canonical<Decision>(decisionFixture, "decision_finance_pln_needs_review");
const financeDecisionResolved = canonical<Decision>(decisionResolvedFixture, "decision_finance_pln_resolved");
const activeFocusSession = canonical<FocusSession>(focusSessionFixture, "focus_session_active");
const focusCompletionSummary = canonical<FocusCompletionSummary>(focusCompletionFixture, "focus_completion_summary");
const briefing = canonical<ExecutiveBriefing>(briefingFixture, "briefing_acme_pl");
const proposedActionAgenda = canonical<ProposedAction>(proposedActionFixture, "proposed_action_agenda_high");
const transcriptPl = canonical<Transcript>(transcriptPlFixture, "transcript_pl");
const toolResult = canonical<ToolResult>(toolResultFixture, "tool_result_agenda_ok");
const llmSettings = canonical<LlmSettingsResponse>(llmSettingsFixture, "llm_settings_response");

const FIXTURE_DAY = "2026-09-21";

const fixtureToday: TodayCalendarResponse = {
  day: FIXTURE_DAY,
  timezone: "Europe/Warsaw",
  meetings: [teamSyncMeeting, acmeMeeting],
  retrieval_status: "complete",
};
const fixtureAttention: AttentionListResponse = { items: [financeAttention] };
const fixtureDecisions: DecisionListResponse = { items: [financeDecision] };

const emptyToday: TodayCalendarResponse = {
  day: FIXTURE_DAY,
  timezone: "Europe/Warsaw",
  meetings: [],
  retrieval_status: "complete",
};
const emptyAttention: AttentionListResponse = { items: [] };
const emptyDecisions: DecisionListResponse = { items: [] };
const emptyFocus: FocusCurrentResponse = { session: null };

const partialBriefing: ExecutiveBriefing = {
  ...briefing,
  retrieval_status: "partial",
  retrieval_notes: ["Korespondencja ograniczona do ostatnich 30 dni; watki starsze nieodpytane."],
};

// Canonical attention explanation built strictly from the fixture's own stored
// data. policy_version is derived from canonical fixture Reason data; a fixture
// without a usable policy version is a contract drift and fails loudly instead
// of silently substituting a fabricated value.
const canonicalExplanationPolicyVersion: string = (() => {
  const derived =
    financeAttention.reasons?.find((r) => r.policy_version)?.policy_version ??
    financeAttention.delivery_reasons?.find((r) => r.policy_version)?.policy_version;
  if (!derived) {
    throw new Error(
      "canonical attention fixture contains no usable policy_version; refusing to fabricate one"
    );
  }
  return derived;
})();

const attentionExplanation: AttentionExplanationResponse = {
  item_id: financeAttention.id,
  priority_reasons: financeAttention.reasons ?? [],
  delivery_reasons: financeAttention.delivery_reasons ?? [],
  policy_version: canonicalExplanationPolicyVersion,
  sources: financeAttention.sources ?? [],
};

const decisionDetail: DecisionResponse = {
  decision: financeDecision,
};

const decisionDetailResolved: DecisionResponse = {
  decision: financeDecisionResolved,
};

const actionPendingResponse: ActionResponse = {
  action: {
    ...proposedActionAgenda,
    status: "pending",
  },
};

const actionExecutingResponse: ActionResponse = {
  action: {
    ...proposedActionAgenda,
    status: "executing",
  },
};

const actionSucceededResponse: ActionResponse = {
  action: {
    ...proposedActionAgenda,
    status: "succeeded",
  },
  last_result: toolResult,
};

const actionUnknownResponse: ActionResponse = {
  action: {
    ...proposedActionAgenda,
    status: "unknown",
  },
  last_result: {
    ...toolResult,
    status: "unknown",
  },
};

export interface MockClientOptions {
  mode?: MockMode;
}

function respond<T>(mode: MockMode, fixtures: T, empty: T): Promise<T> {
  switch (mode) {
    case "pending":
      return new Promise<T>(() => {});
    case "error":
      return Promise.reject(new Error("Simulated transport failure (mock error mode)"));
    case "empty":
      return Promise.resolve(empty);
    case "fixtures":
      return Promise.resolve(fixtures);
    default:
      return Promise.resolve(fixtures);
  }
}

function pickCase<T>(mode: MockMode, cases: Partial<Record<MockMode, T>>, fallback: T): T {
  return cases[mode] ?? cases["fixtures"] ?? fallback;
}

function respondByMode<T>(mode: MockMode, cases: Partial<Record<MockMode, T>>, fallback: T): Promise<T> {
  if (mode === "pending") {
    return new Promise<T>(() => {});
  }
  if (mode === "error") {
    return Promise.reject(new Error("Simulated transport failure (mock error mode)"));
  }
  return Promise.resolve(pickCase(mode, cases, fallback));
}

/** Stateful mock operations: deterministic single value, or error/pending behavior. */
function statefulRespond<T>(mode: MockMode, value: T): Promise<T> {
  if (mode === "pending") {
    return new Promise<T>(() => {});
  }
  if (mode === "error") {
    return Promise.reject(new Error("Simulated transport failure (mock error mode)"));
  }
  return Promise.resolve(value);
}

export function createMockClient(options: MockClientOptions = {}): EvaClient {
  const mode = options.mode ?? "fixtures";

  // ---- Client-local mutable mock state -------------------------------------
  // Everything below lives inside this closure: two independently created
  // mock clients never share or reset each other's Focus/approval state.
  let focusState: FocusSession | null = activeFocusSession;

  // Approval state: the current pending proposal, the one-time challenge
  // issued for it, and whether that challenge has already been consumed.
  let storedProposal: ProposedAction | null = null;
  let issuedChallenge: ApprovalChallengeResponse | null = null;
  let challengeConsumed = false;

  // Settings state (sanitized only — the mock never stores or returns an
  // actual API-key string; a submitted key updates presence flags and is
  // dropped immediately after that call is processed).
  let settingsState: LlmSettingsResponse = llmSettings;

  return {
    getTodayCalendar: () => respond(mode, fixtureToday, emptyToday),
    getAttention: () => respond(mode, fixtureAttention, emptyAttention),
    getDecisions: () => respond(mode, fixtureDecisions, emptyDecisions),
    getCurrentFocus: () => respond(mode, { session: focusState }, emptyFocus),

    getBriefing: (request: BriefingRequest) => {
      // Preserve both the requested meeting_ref and language in the response.
      const fixtureResponse: BriefingResponse = {
        briefing: {
          ...briefing,
          meeting: { ...briefing.meeting, ref: request.meeting_ref },
          language: request.language,
        },
      };
      return respondByMode(mode, {
        fixtures: fixtureResponse,
        empty: { briefing: { ...briefing, previous_interactions: [], open_topics: [], previous_decisions: [], risks: [], suggestions: [], sources: [], spoken_summary: "" } },
        "partial-evidence": { briefing: partialBriefing },
        "focus-active": { briefing: briefing },
        "focus-completed": { briefing: briefing },
        "action-pending": { briefing: briefing },
        "action-high-confirmation": { briefing: briefing },
        "action-unknown": { briefing: briefing },
        "decision-resolved": { briefing: briefing },
      }, { briefing: briefing });
    },

    getAttentionExplanation: (attentionId: string) =>
      respondByMode(mode, {
        fixtures: { ...attentionExplanation, item_id: attentionId },
        empty: { item_id: attentionId, priority_reasons: [], delivery_reasons: [], policy_version: "policy-empty-preview (synthetic)", sources: [] },
        "focus-active": { ...attentionExplanation, item_id: attentionId },
        "focus-completed": { ...attentionExplanation, item_id: attentionId },
        "action-pending": { ...attentionExplanation, item_id: attentionId },
        "action-high-confirmation": { ...attentionExplanation, item_id: attentionId },
        "action-unknown": { ...attentionExplanation, item_id: attentionId },
        "decision-resolved": { ...attentionExplanation, item_id: attentionId },
        "partial-evidence": { ...attentionExplanation, item_id: attentionId },
      }, { ...attentionExplanation, item_id: attentionId }),

    startFocus: (request: FocusStartRequest) => {
      focusState = {
        id: "focus-demo-001",
        starts_at: new Date().toISOString(),
        ends_at: new Date(Date.now() + request.duration_minutes * 60 * 1000).toISOString(),
        threshold: request.threshold,
        sender_overrides: request.sender_overrides,
        policy_version: "policy-v1",
        stopped_at: null,
      };
      const sessionResponse: FocusSessionResponse = { session: focusState };
      return respondByMode(mode, {
        fixtures: sessionResponse,
        empty: { session: { id: "", starts_at: "", ends_at: "", threshold: "medium", policy_version: "", stopped_at: null } },
        "focus-active": sessionResponse,
        "focus-completed": sessionResponse,
        "action-pending": sessionResponse,
        "action-high-confirmation": sessionResponse,
        "action-unknown": sessionResponse,
        "decision-resolved": sessionResponse,
        "partial-evidence": sessionResponse,
      }, sessionResponse);
    },

    stopFocus: (_request?: FocusStopRequest) => {
      if (!focusState) {
        return Promise.reject(new Error("No active Focus session to stop"));
      }
      const stoppedSession: FocusSession = {
        ...focusState,
        stopped_at: new Date().toISOString(),
      };
      focusState = null;
      return respondByMode(mode, {
        fixtures: { session: stoppedSession, summary: focusCompletionSummary },
        empty: { session: { ...activeFocusSession, stopped_at: "2026-09-21T13:30:00+02:00" }, summary: focusCompletionSummary },
        "focus-active": { session: stoppedSession, summary: focusCompletionSummary },
        "focus-completed": { session: stoppedSession, summary: focusCompletionSummary },
        "action-pending": { session: stoppedSession, summary: focusCompletionSummary },
        "action-high-confirmation": { session: stoppedSession, summary: focusCompletionSummary },
        "action-unknown": { session: stoppedSession, summary: focusCompletionSummary },
        "decision-resolved": { session: stoppedSession, summary: focusCompletionSummary },
        "partial-evidence": { session: stoppedSession, summary: focusCompletionSummary },
      }, { session: stoppedSession, summary: focusCompletionSummary });
    },

    getFocusSummary: (_sessionId: string) =>
      respondByMode(mode, {
        fixtures: { summary: focusCompletionSummary },
        empty: { summary: { focus_session_id: "", ended_at: "", total_received: 0, deferred_count: 0, decision_count: 0, action_count: 0, fyi_count: 0, attention_item_ids: [] } },
        "focus-active": { summary: focusCompletionSummary },
        "focus-completed": { summary: focusCompletionSummary },
        "action-pending": { summary: focusCompletionSummary },
        "action-high-confirmation": { summary: focusCompletionSummary },
        "action-unknown": { summary: focusCompletionSummary },
        "decision-resolved": { summary: focusCompletionSummary },
        "partial-evidence": { summary: focusCompletionSummary },
      }, { summary: focusCompletionSummary }),

    getDecision: (_decisionId: string) =>
      respondByMode(mode, {
        fixtures: decisionDetail,
        empty: { decision: { ...financeDecision, context: [], alternatives: [], risks: [], preference_conflicts: [], sources: [] } },
        "focus-active": decisionDetail,
        "focus-completed": decisionDetail,
        "action-pending": decisionDetail,
        "action-high-confirmation": decisionDetail,
        "action-unknown": decisionDetail,
        "decision-resolved": decisionDetailResolved,
        "partial-evidence": decisionDetail,
      }, decisionDetail),

    proposeDecisionOutcome: (decisionId: string, request: DecisionOutcomeProposalRequest) => {
      const outcomeProposedAction: ProposedAction = {
        id: `act-demo-decision-record-${decisionId}`,
        session_id: request.session_id,
        request_id: request.request_id,
        revision: 1,
        tool: "decision.record_outcome",
        arguments: {
          tool: "decision.record_outcome",
          decision_id: decisionId,
          outcome: request.outcome,
        },
        arguments_digest: `sha256:demo-digest-decision-record-${decisionId}`,
        summary: `Record ${request.outcome.toUpperCase()} outcome for financial decision: ${financeDecision.title}`,
        reason: `User recorded ${request.outcome.toUpperCase()} outcome for financial decision`,
        impact: "Local decision record only. No payment, purchase, supplier commitment, or external instruction is sent.",
        before: { outcome: null, status: "needs_review" },
        after: { outcome: request.outcome, status: "resolved" },
        resource_version: "1",
        policy_version: "policy-v1",
        risk: "high",
        requires_approval: true,
        voice_approval_allowed: false,
        created_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
        status: "pending",
      };

      // Store the exact proposed action; any previously issued challenge is
      // invalidated because it was bound to a different proposal.
      storedProposal = outcomeProposedAction;
      issuedChallenge = null;
      challengeConsumed = false;

      return statefulRespond(mode, { action: outcomeProposedAction });
    },

    deferDecision: (_decisionId: string, _request?: DeferDecisionRequest) => {
      const deferredDecision: Decision = {
        ...financeDecision,
        status: "deferred",
        outcome: null,
        outcome_recorded_at: null,
        proposed_action_id: null,
      };
      return respondByMode(mode, {
        fixtures: { decision: deferredDecision },
        empty: { decision: deferredDecision },
        "focus-active": { decision: deferredDecision },
        "focus-completed": { decision: deferredDecision },
        "action-pending": { decision: deferredDecision },
        "action-high-confirmation": { decision: deferredDecision },
        "action-unknown": { decision: deferredDecision },
        "decision-resolved": { decision: deferredDecision },
        "partial-evidence": { decision: deferredDecision },
      }, { decision: deferredDecision });
    },

    getAction: (actionId: string) => {
      // The canonical stored action state is authoritative for its own id.
      if (storedProposal && actionId === storedProposal.id) {
        return statefulRespond(mode, { action: storedProposal } as ActionResponse);
      }
      return respondByMode(mode, {
        fixtures: actionPendingResponse,
        empty: actionPendingResponse,
        "focus-active": actionPendingResponse,
        "focus-completed": actionPendingResponse,
        "action-pending": actionPendingResponse,
        "action-high-confirmation": actionExecutingResponse,
        "action-unknown": actionUnknownResponse,
        "decision-resolved": actionSucceededResponse,
        "partial-evidence": actionPendingResponse,
      }, actionPendingResponse);
    },

    getApprovalChallenge: (actionId: string) => {
      if (mode === "pending") {
        return new Promise<ApprovalChallengeResponse>(() => {});
      }
      if (mode === "error") {
        return Promise.reject(new Error("Simulated transport failure (mock error mode)"));
      }
      if (!storedProposal) {
        return Promise.reject(new Error("No pending action proposal exists to challenge"));
      }
      if (actionId !== storedProposal.id) {
        return Promise.reject(
          new Error(`Challenge requested for action "${actionId}" does not match the stored proposal "${storedProposal.id}"`)
        );
      }
      if (storedProposal.status !== "pending") {
        return Promise.reject(new Error(`Action is not pending (status: ${storedProposal.status}); no challenge issued`));
      }
      // Issue a fresh one-time demo challenge bound to the STORED action
      // binding (id/revision/digest/expiry) — never to request data.
      issuedChallenge = {
        action_id: storedProposal.id,
        revision: storedProposal.revision,
        arguments_digest: storedProposal.arguments_digest,
        challenge: "demo-one-time-challenge-0001",
        expires_at: storedProposal.expires_at,
      };
      challengeConsumed = false;
      return Promise.resolve(issuedChallenge);
    },

    confirmAction: (actionId: string, request: ApprovalRequest) => {
      if (mode === "pending") {
        return new Promise<ActionConfirmResponse>(() => {});
      }
      if (mode === "error") {
        return Promise.reject(new Error("Simulated transport failure (mock error mode)"));
      }
      // URL/action argument binding
      if (actionId !== request.action_id) {
        return Promise.reject(new Error("Confirm URL action id does not match request action_id"));
      }
      if (!storedProposal) {
        return Promise.reject(new Error("No stored action proposal exists"));
      }
      // Request must match the stored proposal binding exactly
      if (request.action_id !== storedProposal.id) {
        return Promise.reject(new Error("Request action_id does not match the stored proposal"));
      }
      if (request.revision !== storedProposal.revision) {
        return Promise.reject(new Error("Request revision does not match the stored proposal"));
      }
      if (request.arguments_digest !== storedProposal.arguments_digest) {
        return Promise.reject(new Error("Request arguments_digest does not match the stored proposal"));
      }
      // Challenge must be the currently issued one, bound to the same
      // action/revision/digest, and not already consumed.
      if (!issuedChallenge) {
        return Promise.reject(new Error("No challenge has been issued for this action"));
      }
      if (
        issuedChallenge.action_id !== request.action_id ||
        issuedChallenge.revision !== request.revision ||
        issuedChallenge.arguments_digest !== request.arguments_digest
      ) {
        return Promise.reject(new Error("Issued challenge is bound to a different action binding"));
      }
      if (request.challenge !== issuedChallenge.challenge) {
        return Promise.reject(new Error("Invalid challenge"));
      }
      if (challengeConsumed) {
        return Promise.reject(new Error("Challenge has already been consumed"));
      }

      // Consume the one-time challenge.
      challengeConsumed = true;

      if (request.choice === "reject") {
        const rejectedAction: ProposedAction = { ...storedProposal, status: "rejected" };
        storedProposal = rejectedAction;
        return Promise.resolve({ action: rejectedAction, receipt: null, result: null });
      }

      // Simulated complete local executor flow for decision.record_outcome:
      // the local write succeeds, so the canonical status genuinely advances
      // to "succeeded" together with the ToolResult (never "approved" while
      // claiming execution).
      const recordedOutcome: DecisionOutcome = (storedProposal.arguments as { outcome?: DecisionOutcome }).outcome ?? "accept";
      const recordedAt = new Date().toISOString();
      const succeededAction: ProposedAction = { ...storedProposal, status: "succeeded" };
      const confirmResponse: ActionConfirmResponse = {
        action: succeededAction,
        receipt: {
          id: `rcpt-demo-${storedProposal.id}`,
          action_id: storedProposal.id,
          revision: storedProposal.revision,
          arguments_digest: storedProposal.arguments_digest,
          channel: "ui",
          approved_at: recordedAt,
          policy_version: storedProposal.policy_version,
        },
        result: {
          call_id: `call-demo-${storedProposal.id}`,
          tool: storedProposal.tool,
          status: "ok",
          data: {
            decision_id: (storedProposal.arguments as { decision_id?: string }).decision_id ?? null,
            outcome: recordedOutcome,
            recorded_at: recordedAt,
          },
          error: null,
          sources: [],
          action_id: storedProposal.id,
          duration_ms: 120,
        },
      };
      storedProposal = succeededAction;
      return Promise.resolve(confirmResponse);
    },

    getLlmSettings: () => statefulRespond(mode, settingsState),

    updateLlmSettings: (request: LlmSettingsUpdateRequest) => {
      if (mode === "pending") {
        return new Promise<LlmSettingsResponse>(() => {});
      }
      if (mode === "error") {
        return Promise.reject(new Error("Simulated transport failure (mock error mode)"));
      }
      const next: LlmSettingsResponse = { ...settingsState };
      if (request.base_url !== undefined) next.base_url = request.base_url;
      if (request.model !== undefined) next.model = request.model;
      // api_key is write-only: a provided string flips presence to true; an
      // explicit null removes the key; an omitted field preserves it. The
      // secret string itself is never stored, returned, or logged.
      if (request.api_key !== undefined) next.api_key_present = request.api_key !== null;
      if (request.fallback_base_url !== undefined) next.fallback_base_url = request.fallback_base_url;
      if (request.fallback_model !== undefined) next.fallback_model = request.fallback_model;
      if (request.fallback_api_key !== undefined) next.fallback_api_key_present = request.fallback_api_key !== null;
      // The backend derives `configured`; the mock mirrors that honestly for
      // the synthetic demo endpoint (allowlisted server-side).
      next.configured = Boolean(next.base_url && next.model);
      settingsState = next;
      return Promise.resolve(settingsState);
    },

    testLlmConnection: () => {
      if (mode === "pending") {
        return new Promise<LlmTestConnectionResponse>(() => {});
      }
      if (mode === "error") {
        // A genuine failure — never silently replaced by success.
        return Promise.reject(new Error("connection refused (mock error mode)"));
      }
      // Deterministic synthetic success against the currently configured demo
      // model; no real LLM endpoint is contacted.
      return Promise.resolve({
        health: {
          status: "ready",
          detail: "Mock transport: no real endpoint contacted",
          provider: settingsState.provider,
          model: settingsState.model ?? null,
        },
        latency_ms: 42,
        model: settingsState.model ?? null,
      });
    },

    detectLlmModels: () => {
      if (mode === "pending") {
        return new Promise<DetectModelsResponse>(() => {});
      }
      if (mode === "error") {
        return Promise.reject(new Error("connection refused (mock error mode)"));
      }
      // Deterministic synthetic demo identities — not claims about live
      // installed models.
      return Promise.resolve({
        models: [
          {
            id: "demo-served-model-id",
            display_name: "Demo Served Model",
            supports_tools: true,
            supports_structured_output: true,
            context_window: 128000,
          },
          {
            id: "demo-fast-model-id",
            display_name: "Demo Fast Model",
            supports_tools: false,
            supports_structured_output: true,
            context_window: 32000,
          },
          {
            id: "demo-capability-unknown-model-id",
            display_name: "Demo Unknown Capabilities Model",
            supports_tools: null,
            supports_structured_output: null,
            context_window: null,
          },
        ],
      });
    },

    transcribeAudio: (request: TranscribeAudioRequest) => {
      // Deterministic canonical fixture transcript; the mock echoes the
      // supplied requestId (A05 contract) and never randomizes transcripts.
      // No assistant reasoning exists in this slice — STT only.
      if (mode === "pending") {
        return new Promise<TranscribeResponse>(() => {});
      }
      if (mode === "error") {
        return Promise.reject(
          new ApiError("POST", 422, "/api/voice/transcribe", "audio_silent: no speech detected in the recording")
        );
      }
      const response: TranscribeResponse = {
        request_id: request.requestId,
        transcript: { ...transcriptPl },
      };
      return Promise.resolve(response);
    },
  };
}