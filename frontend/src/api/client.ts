import { createMockClient } from "./mock";
export type {
  AttentionListResponse,
  DecisionListResponse,
  DecisionResponse,
  FocusCurrentResponse,
  FocusSessionResponse,
  FocusStopResponse,
  FocusSummaryResponse,
  TodayCalendarResponse,
  ExecutiveBriefing,
  AttentionExplanationResponse,
  ActionResponse,
  ActionConfirmResponse,
} from "./types.generated";
import type {
  AttentionListResponse,
  DecisionListResponse,
  DecisionResponse,
  FocusCurrentResponse,
  FocusSessionResponse,
  FocusStopResponse,
  FocusSummaryResponse,
  TodayCalendarResponse,
  ExecutiveBriefing,
  AttentionExplanationResponse,
  ActionResponse,
  ActionConfirmResponse,
} from "./types.generated";

/**
 * The single typed transport boundary for B02A/B02B screens. Screens depend on
 * this interface only; the mock transport swaps to REST without rewrites.
 */
export interface EvaClient {
  getTodayCalendar(): Promise<TodayCalendarResponse>;
  getAttention(): Promise<AttentionListResponse>;
  getDecisions(): Promise<DecisionListResponse>;
  getCurrentFocus(): Promise<FocusCurrentResponse>;
  getBriefing(meetingRef: { calendar_id: string; event_id: string }): Promise<ExecutiveBriefing>;
  getAttentionExplanation(attentionId: string): Promise<AttentionExplanationResponse>;
  startFocus(durationMinutes: number, threshold: "high" | "medium" | "low", senderOverrides?: string[]): Promise<FocusSessionResponse>;
  stopFocus(): Promise<FocusStopResponse>;
  getFocusSummary(sessionId: string): Promise<FocusSummaryResponse>;
  getDecision(decisionId: string): Promise<DecisionResponse>;
  recordDecisionOutcome(decisionId: string, outcome: "accept" | "reject"): Promise<ActionResponse>;
  deferDecision(decisionId: string): Promise<ActionResponse>;
  getAction(actionId: string): Promise<ActionResponse>;
  confirmAction(actionId: string, revision: number, argumentsDigest: string, choice: "approve" | "reject", challenge: string): Promise<ActionConfirmResponse>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;

  constructor(status: number, path: string, detail?: string) {
    super(`GET ${path} failed with HTTP ${status}${detail ? `: ${detail}` : ""}`);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
  }
}

export type EvaClientMode = "rest" | "mock";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json", "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, path, detail || undefined);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, path, "response was not valid JSON");
  }
}

/** REST transport against the frozen A00 endpoints (docs/api-contracts.md §8). */
export function createRestClient(): EvaClient {
  return {
    getTodayCalendar: () => requestJson<TodayCalendarResponse>("/api/calendar/today"),
    getAttention: () => requestJson<AttentionListResponse>("/api/attention"),
    getDecisions: () => requestJson<DecisionListResponse>("/api/decisions"),
    getCurrentFocus: () => requestJson<FocusCurrentResponse>("/api/focus/current"),
    getBriefing: (meetingRef) => requestJson<ExecutiveBriefing>(`/api/briefing/meeting?calendar_id=${encodeURIComponent(meetingRef.calendar_id)}&event_id=${encodeURIComponent(meetingRef.event_id)}`),
    getAttentionExplanation: (attentionId) => requestJson<AttentionExplanationResponse>(`/api/attention/${encodeURIComponent(attentionId)}/explanation`),
    startFocus: (durationMinutes, threshold, senderOverrides) => requestJson<FocusSessionResponse>("/api/focus/start", {
      method: "POST",
      body: JSON.stringify({ duration_minutes: durationMinutes, threshold, sender_overrides: senderOverrides }),
    }),
    stopFocus: () => requestJson<FocusStopResponse>("/api/focus/stop", { method: "POST" }),
    getFocusSummary: (sessionId) => requestJson<FocusSummaryResponse>(`/api/focus/${encodeURIComponent(sessionId)}/summary`),
    getDecision: (decisionId) => requestJson<DecisionResponse>(`/api/decisions/${encodeURIComponent(decisionId)}`),
    recordDecisionOutcome: (decisionId, outcome) => requestJson<ActionResponse>(`/api/decisions/${encodeURIComponent(decisionId)}/outcome-proposals`, {
      method: "POST",
      body: JSON.stringify({ outcome }),
    }),
    deferDecision: (decisionId) => requestJson<ActionResponse>(`/api/decisions/${encodeURIComponent(decisionId)}/defer`, { method: "POST" }),
    getAction: (actionId) => requestJson<ActionResponse>(`/api/actions/${encodeURIComponent(actionId)}`),
    confirmAction: (actionId, revision, argumentsDigest, choice, challenge) => requestJson<ActionConfirmResponse>(`/api/actions/${encodeURIComponent(actionId)}/confirm`, {
      method: "POST",
      body: JSON.stringify({ revision, arguments_digest: argumentsDigest, choice, challenge }),
    }),
  };
}

function activeMode(): EvaClientMode {
  return import.meta.env.VITE_EVA_CLIENT === "rest" ? "rest" : "mock";
}

export function createEvaClient(mode: EvaClientMode = activeMode()): EvaClient {
  return mode === "rest" ? createRestClient() : createMockClient();
}

let singleton: EvaClient | undefined;

export function getEvaClient(): EvaClient {
  singleton ??= createEvaClient();
  return singleton;
}
