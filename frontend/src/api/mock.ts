import type { EvaClient } from "./client";
import type {
  AttentionItem,
  AttentionListResponse,
  Decision,
  DecisionListResponse,
  FocusCurrentResponse,
  FocusSession,
  Meeting,
  TodayCalendarResponse,
} from "./types.generated";
import acmeFixture from "../../../contracts/fixtures/meeting_acme_high.json";
import teamSyncFixture from "../../../contracts/fixtures/meeting_team_sync_medium.json";
import attentionFixture from "../../../contracts/fixtures/attention_finance_decision.json";
import decisionFixture from "../../../contracts/fixtures/decision_finance_pln_needs_review.json";
import focusSessionFixture from "../../../contracts/fixtures/focus_session_active.json";

export type MockMode = "fixtures" | "empty" | "error" | "pending";

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
const activeFocusSession = canonical<FocusSession>(focusSessionFixture, "focus_session_active");

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
  }
}

export function createMockClient(options: MockClientOptions = {}): EvaClient {
  const mode = options.mode ?? "fixtures";
  return {
    getTodayCalendar: () => respond(mode, fixtureToday, emptyToday),
    getAttention: () => respond(mode, fixtureAttention, emptyAttention),
    getDecisions: () => respond(mode, fixtureDecisions, emptyDecisions),
    getCurrentFocus: () => respond(mode, fixtureFocus, emptyFocus),
  };
}
