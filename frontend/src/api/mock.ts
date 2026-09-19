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
  FocusStopResponse,
  Meeting,
  TodayCalendarResponse,
  ExecutiveBriefing,
  ProposedAction,
  ActionResponse,
  ActionConfirmResponse,
  ApprovalReceipt,
  ToolResult,
} from "./types.generated";
import acmeFixture from "../../../contracts/fixtures/meeting_acme_high.json";
import teamSyncFixture from "../../../contracts/fixtures/meeting_team_sync_medium.json";
import attentionFixture from "../../../contracts/fixtures/attention_finance_decision.json";
import decisionFixture from "../../../contracts/fixtures/decision_finance_pln_needs_review.json";
import decisionResolvedFixture from "../../../contracts/fixtures/decision_finance_pln_resolved.json";
import focusSessionFixture from "../../../contracts/fixtures/focus_session_active.json";
import focusCompletionFixture from "../../../contracts/fixtures/focus_completion_summary.json";
import focusStopFixture from "../../../contracts/fixtures/focus_stop_response.json";
import briefingFixture from "../../../contracts/fixtures/briefing_acme_pl.json";
import proposedActionFixture from "../../../contracts/fixtures/proposed_action_agenda_high.json";
import approvalReceiptFixture from "../../../contracts/fixtures/approval_receipt_ui.json";
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
const focusStopResponse = canonical<FocusStopResponse>(focusStopFixture, "focus_stop_response");
const briefing = canonical<ExecutiveBriefing>(briefingFixture, "briefing_acme_pl");
const proposedAction = canonical<ProposedAction>(proposedActionFixture, "proposed_action_agenda_high");
const approvalReceipt = canonical<ApprovalReceipt>(approvalReceiptFixture, "approval_receipt_ui");
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
const fixtureFocus: FocusCurrentResponse = { session: activeFocusSession };

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

const attentionExplanation: AttentionExplanationResponse = {
  item_id: financeAttention.id,
  priority_reasons: [
    {
      code: "FINANCE_DECISION_REQUIRED",
      origin: "rule",
      text: "Item involves a financial decision requiring review",
      source_ids: [financeDecision.id],
      policy_version: "policy-v1",
    },
  ],
  delivery_reasons: [
    {
      code: "FOCUS_INACTIVE",
      origin: "rule",
      text: "No active Focus session; delivered immediately",
      source_ids: [],
      policy_version: "policy-v1",
    },
  ],
  policy_version: "policy-v1",
  sources: [],
};

const decisionDetail: DecisionResponse = {
  decision: financeDecision,
};

const decisionDetailResolved: DecisionResponse = {
  decision: financeDecisionResolved,
};

const focusStartResponse: FocusSessionResponse = {
  session: {
    id: "focus-demo-001",
    starts_at: "2026-09-21T12:00:00+02:00",
    ends_at: "2026-09-21T14:00:00+02:00",
    threshold: "medium",
    sender_overrides: ["cfo.demo@example.com"],
    policy_version: "policy-v1",
    stopped_at: null,
  },
};

const actionPendingResponse: ActionResponse = {
  action: {
    ...proposedAction,
    status: "pending",
  },
};

const actionExecutingResponse: ActionResponse = {
  action: {
    ...proposedAction,
    status: "executing",
  },
};

const actionSucceededResponse: ActionResponse = {
  action: {
    ...proposedAction,
    status: "succeeded",
  },
  last_result: toolResult,
};

const actionUnknownResponse: ActionResponse = {
  action: {
    ...proposedAction,
    status: "unknown",
  },
  last_result: {
    ...toolResult,
    status: "unknown",
  },
};

const actionConfirmResponse: ActionConfirmResponse = {
  action: {
    ...proposedAction,
    status: "approved",
  },
  receipt: approvalReceipt,
  result: toolResult,
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

export function createMockClient(options: MockClientOptions = {}): EvaClient {
  const mode = options.mode ?? "fixtures";

  return {
    getTodayCalendar: () => respond(mode, fixtureToday, emptyToday),
    getAttention: () => respond(mode, fixtureAttention, emptyAttention),
    getDecisions: () => respond(mode, fixtureDecisions, emptyDecisions),
    getCurrentFocus: () => respond(mode, fixtureFocus, emptyFocus),

    getBriefing: () =>
      respondByMode(mode, {
        fixtures: briefing,
        empty: { ...briefing, previous_interactions: [], open_topics: [], previous_decisions: [], risks: [], suggestions: [], sources: [], spoken_summary: "" },
        "partial-evidence": partialBriefing,
        "focus-active": briefing,
        "focus-completed": briefing,
        "action-pending": briefing,
        "action-high-confirmation": briefing,
        "action-unknown": briefing,
        "decision-resolved": briefing,
      }, briefing),

    getAttentionExplanation: () =>
      respondByMode(mode, {
        fixtures: attentionExplanation,
        empty: { item_id: financeAttention.id, priority_reasons: [], delivery_reasons: [], policy_version: "policy-v1", sources: [] },
        "focus-active": attentionExplanation,
        "focus-completed": attentionExplanation,
        "action-pending": attentionExplanation,
        "action-high-confirmation": attentionExplanation,
        "action-unknown": attentionExplanation,
        "decision-resolved": attentionExplanation,
        "partial-evidence": attentionExplanation,
      }, attentionExplanation),

    startFocus: (_durationMinutes, _threshold, _senderOverrides) =>
      respondByMode(mode, {
        fixtures: focusStartResponse,
        empty: { session: { id: "", starts_at: "", ends_at: "", threshold: "medium", policy_version: "", stopped_at: null } },
        "focus-active": focusStartResponse,
        "focus-completed": focusStartResponse,
        "action-pending": focusStartResponse,
        "action-high-confirmation": focusStartResponse,
        "action-unknown": focusStartResponse,
        "decision-resolved": focusStartResponse,
        "partial-evidence": focusStartResponse,
      }, focusStartResponse),

    stopFocus: () =>
      respondByMode(mode, {
        fixtures: focusStopResponse,
        empty: { session: { ...activeFocusSession, stopped_at: "2026-09-21T13:30:00+02:00" }, summary: focusCompletionSummary },
        "focus-active": focusStopResponse,
        "focus-completed": focusStopResponse,
        "action-pending": focusStopResponse,
        "action-high-confirmation": focusStopResponse,
        "action-unknown": focusStopResponse,
        "decision-resolved": focusStopResponse,
        "partial-evidence": focusStopResponse,
      }, focusStopResponse),

    getFocusSummary: () =>
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

    getDecision: () =>
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

    recordDecisionOutcome: (_decisionId, _outcome) =>
      respondByMode(mode, {
        fixtures: actionPendingResponse,
        empty: actionPendingResponse,
        "focus-active": actionPendingResponse,
        "focus-completed": actionPendingResponse,
        "action-pending": actionPendingResponse,
        "action-high-confirmation": actionPendingResponse,
        "action-unknown": actionPendingResponse,
        "decision-resolved": actionPendingResponse,
        "partial-evidence": actionPendingResponse,
      }, actionPendingResponse),

    deferDecision: (_decisionId) =>
      respondByMode(mode, {
        fixtures: actionPendingResponse,
        empty: actionPendingResponse,
        "focus-active": actionPendingResponse,
        "focus-completed": actionPendingResponse,
        "action-pending": actionPendingResponse,
        "action-high-confirmation": actionPendingResponse,
        "action-unknown": actionPendingResponse,
        "decision-resolved": actionPendingResponse,
        "partial-evidence": actionPendingResponse,
      }, actionPendingResponse),

    getAction: (_actionId) =>
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

    confirmAction: (_actionId, _revision, _argumentsDigest, _choice, _challenge) =>
      respondByMode(mode, {
        fixtures: actionConfirmResponse,
        empty: actionConfirmResponse,
        "focus-active": actionConfirmResponse,
        "focus-completed": actionConfirmResponse,
        "action-pending": actionConfirmResponse,
        "action-high-confirmation": actionConfirmResponse,
        "action-unknown": actionConfirmResponse,
        "decision-resolved": actionConfirmResponse,
        "partial-evidence": actionConfirmResponse,
      }, actionConfirmResponse),
  };
}