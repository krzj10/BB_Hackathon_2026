import type { EvaClient } from "./client";
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
  FocusStartRequest,
  FocusStopRequest,
  DecisionOutcomeProposalRequest,
  DeferDecisionRequest,
  ApprovalRequest,
  ApprovalChallengeResponse,
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
const toolResult = canonical<ToolResult>(toolResultFixture, "tool_result_agenda_ok");

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

// Build canonical attention explanation from the fixture's own reasons and delivery_reasons.
// policy_version is derived from fixture Reason data, never fabricated separately.
const attentionExplanation: AttentionExplanationResponse = {
  item_id: financeAttention.id,
  priority_reasons: financeAttention.reasons ?? [],
  delivery_reasons: financeAttention.delivery_reasons ?? [],
  policy_version:
    financeAttention.reasons?.[0]?.policy_version ??
    financeAttention.delivery_reasons?.[0]?.policy_version ??
    "policy-v1",
  sources: financeAttention.sources ?? [],
};

const decisionDetail: DecisionResponse = {
  decision: financeDecision,
};

const decisionDetailResolved: DecisionResponse = {
  decision: financeDecisionResolved,
};

// Mock ProposedAction for decision.record_outcome (HIGH risk, requires approval)
const decisionRecordOutcomeProposedAction: ProposedAction = {
  id: "act-demo-decision-record-001",
  session_id: "sess-demo-001",
  request_id: "req-demo-001",
  revision: 1,
  tool: "decision.record_outcome",
  arguments: {
    tool: "decision.record_outcome",
    decision_id: financeDecision.id,
    outcome: "accept",
  },
  arguments_digest: "sha256:demo-digest-decision-record-0001",
  summary: "Record ACCEPT outcome for financial decision: Faktura za migracje - 12 400 PLN",
  reason: "User recorded ACCEPT outcome for financial decision",
  impact: "Local decision record only. No payment, purchase, supplier commitment, or external instruction is sent.",
  before: { outcome: null, status: "needs_review" },
  after: { outcome: "accept", status: "resolved" },
  resource_version: "1",
  policy_version: "policy-v1",
  risk: "high",
  requires_approval: true,
  voice_approval_allowed: false,
  created_at: "2026-09-21T13:45:00+02:00",
  expires_at: "2026-09-21T13:50:00+02:00",
  status: "pending",
};

const approvalChallenge: ApprovalChallengeResponse = {
  action_id: decisionRecordOutcomeProposedAction.id,
  revision: decisionRecordOutcomeProposedAction.revision,
  arguments_digest: decisionRecordOutcomeProposedAction.arguments_digest,
  challenge: "demo-one-time-challenge-0001",
  expires_at: decisionRecordOutcomeProposedAction.expires_at,
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

// In-memory mock state for Focus lifecycle
let mockFocusState: FocusSession | null = activeFocusSession;

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

export function createMockClient(options: MockClientOptions = {}): EvaClient {
  const mode = options.mode ?? "fixtures";

  // Reset mock Focus state for each new client
  mockFocusState = activeFocusSession;

  return {
    getTodayCalendar: () => respond(mode, fixtureToday, emptyToday),
    getAttention: () => respond(mode, fixtureAttention, emptyAttention),
    getDecisions: () => respond(mode, fixtureDecisions, emptyDecisions),
    getCurrentFocus: () => respond(mode, { session: mockFocusState }, emptyFocus),

    getBriefing: (request: BriefingRequest) =>
      respondByMode(mode, {
        fixtures: { briefing: { ...briefing, meeting: { ...briefing.meeting, ref: request.meeting_ref } } },
        empty: { briefing: { ...briefing, previous_interactions: [], open_topics: [], previous_decisions: [], risks: [], suggestions: [], sources: [], spoken_summary: "" } },
        "partial-evidence": { briefing: partialBriefing },
        "focus-active": { briefing: briefing },
        "focus-completed": { briefing: briefing },
        "action-pending": { briefing: briefing },
        "action-high-confirmation": { briefing: briefing },
        "action-unknown": { briefing: briefing },
        "decision-resolved": { briefing: briefing },
      }, { briefing: briefing }),

    getAttentionExplanation: (attentionId: string) =>
      respondByMode(mode, {
        fixtures: { ...attentionExplanation, item_id: attentionId },
        empty: { item_id: attentionId, priority_reasons: [], delivery_reasons: [], policy_version: "policy-v1", sources: [] },
        "focus-active": { ...attentionExplanation, item_id: attentionId },
        "focus-completed": { ...attentionExplanation, item_id: attentionId },
        "action-pending": { ...attentionExplanation, item_id: attentionId },
        "action-high-confirmation": { ...attentionExplanation, item_id: attentionId },
        "action-unknown": { ...attentionExplanation, item_id: attentionId },
        "decision-resolved": { ...attentionExplanation, item_id: attentionId },
        "partial-evidence": { ...attentionExplanation, item_id: attentionId },
      }, { ...attentionExplanation, item_id: attentionId }),

    startFocus: (request: FocusStartRequest) => {
      mockFocusState = {
        id: "focus-demo-001",
        starts_at: new Date().toISOString(),
        ends_at: new Date(Date.now() + request.duration_minutes * 60 * 1000).toISOString(),
        threshold: request.threshold,
        sender_overrides: request.sender_overrides,
        policy_version: "policy-v1",
        stopped_at: null,
      };
      const sessionResponse: FocusSessionResponse = { session: mockFocusState };
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
      if (!mockFocusState) {
        return Promise.reject(new Error("No active Focus session to stop"));
      }
      const stoppedSession: FocusSession = {
        ...mockFocusState,
        stopped_at: new Date().toISOString(),
      };
      mockFocusState = null;
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
      // Build a ProposedAction for decision.record_outcome based on the request
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

      return respondByMode(mode, {
        fixtures: { action: outcomeProposedAction },
        empty: { action: outcomeProposedAction },
        "focus-active": { action: outcomeProposedAction },
        "focus-completed": { action: outcomeProposedAction },
        "action-pending": { action: outcomeProposedAction },
        "action-high-confirmation": { action: outcomeProposedAction },
        "action-unknown": { action: outcomeProposedAction },
        "decision-resolved": { action: outcomeProposedAction },
        "partial-evidence": { action: outcomeProposedAction },
      }, { action: outcomeProposedAction });
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

    getAction: (_actionId: string) =>
      respondByMode(mode, {
        fixtures: actionPendingResponse,
        empty: actionPendingResponse,
        "focus-active": actionPendingResponse,
        "focus-completed": actionPendingResponse,
        "action-pending": actionPendingResponse,
        "action-high-confirmation": actionExecutingResponse,
        "action-unknown": actionUnknownResponse,
        "decision-resolved": actionSucceededResponse,
        "partial-evidence": actionPendingResponse,
      }, actionPendingResponse),

    getApprovalChallenge: (_actionId: string) =>
      respondByMode(mode, {
        fixtures: approvalChallenge,
        empty: { action_id: "", revision: 0, arguments_digest: "", challenge: "", expires_at: "" },
        "focus-active": approvalChallenge,
        "focus-completed": approvalChallenge,
        "action-pending": approvalChallenge,
        "action-high-confirmation": approvalChallenge,
        "action-unknown": approvalChallenge,
        "decision-resolved": approvalChallenge,
        "partial-evidence": approvalChallenge,
      }, approvalChallenge),

    confirmAction: (_actionId: string, request: ApprovalRequest) => {
      // Verify the challenge matches
      if (request.challenge !== approvalChallenge.challenge) {
        return Promise.reject(new Error("Invalid challenge"));
      }
      if (request.choice === "approve") {
        const approvedAction: ProposedAction = {
          ...decisionRecordOutcomeProposedAction,
          status: "approved",
        };
        const approveResult: ActionConfirmResponse = {
          action: approvedAction,
          receipt: {
            id: "rcpt-demo-decision-record-001",
            action_id: decisionRecordOutcomeProposedAction.id,
            revision: decisionRecordOutcomeProposedAction.revision,
            arguments_digest: decisionRecordOutcomeProposedAction.arguments_digest,
            channel: "ui",
            approved_at: "2026-09-21T13:46:00+02:00",
            policy_version: "policy-v1",
          },
          result: {
            call_id: "call-demo-decision-record-001",
            tool: "decision.record_outcome",
            status: "ok",
            data: { decision_id: financeDecision.id, outcome: "accept", recorded_at: "2026-09-21T13:46:00+02:00" },
            error: null,
            sources: [],
            action_id: decisionRecordOutcomeProposedAction.id,
            duration_ms: 120,
          },
        };
        return respondByMode(mode, {
          fixtures: approveResult,
          empty: approveResult,
          "focus-active": approveResult,
          "focus-completed": approveResult,
          "action-pending": approveResult,
          "action-high-confirmation": approveResult,
          "action-unknown": approveResult,
          "decision-resolved": approveResult,
          "partial-evidence": approveResult,
        }, approveResult);
      } else {
        const rejectedAction: ProposedAction = {
          ...decisionRecordOutcomeProposedAction,
          status: "rejected",
        };
        return respondByMode(mode, {
          fixtures: { action: rejectedAction, receipt: null, result: null },
          empty: { action: rejectedAction, receipt: null, result: null },
          "focus-active": { action: rejectedAction, receipt: null, result: null },
          "focus-completed": { action: rejectedAction, receipt: null, result: null },
          "action-pending": { action: rejectedAction, receipt: null, result: null },
          "action-high-confirmation": { action: rejectedAction, receipt: null, result: null },
          "action-unknown": { action: rejectedAction, receipt: null, result: null },
          "decision-resolved": { action: rejectedAction, receipt: null, result: null },
          "partial-evidence": { action: rejectedAction, receipt: null, result: null },
        }, { action: rejectedAction, receipt: null, result: null });
      }
    },
  };
}