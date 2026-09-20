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
  AttentionExplanationResponse,
  ProposedActionResponse,
  ActionResponse,
  ActionConfirmResponse,
  ApprovalChallengeResponse,
  BriefingRequest,
  BriefingResponse,
  FocusStartRequest,
  FocusStopRequest,
  DecisionOutcomeProposalRequest,
  DeferDecisionRequest,
  ApprovalRequest,
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
  AttentionExplanationResponse,
  ProposedActionResponse,
  ActionResponse,
  ActionConfirmResponse,
  ApprovalChallengeResponse,
  BriefingRequest,
  BriefingResponse,
  FocusStartRequest,
  FocusStopRequest,
  DecisionOutcomeProposalRequest,
  DeferDecisionRequest,
  ApprovalRequest,
  LlmSettingsResponse,
  LlmSettingsUpdateRequest,
  LlmTestConnectionResponse,
  DetectModelsResponse,
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
  getBriefing(request: BriefingRequest): Promise<BriefingResponse>;
  getAttentionExplanation(attentionId: string): Promise<AttentionExplanationResponse>;
  startFocus(request: FocusStartRequest): Promise<FocusSessionResponse>;
  stopFocus(request?: FocusStopRequest): Promise<FocusStopResponse>;
  getFocusSummary(sessionId: string): Promise<FocusSummaryResponse>;
  getDecision(decisionId: string): Promise<DecisionResponse>;
  proposeDecisionOutcome(decisionId: string, request: DecisionOutcomeProposalRequest): Promise<ProposedActionResponse>;
  deferDecision(decisionId: string, request?: DeferDecisionRequest): Promise<DecisionResponse>;
  getAction(actionId: string): Promise<ActionResponse>;
  getApprovalChallenge(actionId: string): Promise<ApprovalChallengeResponse>;
  confirmAction(actionId: string, request: ApprovalRequest): Promise<ActionConfirmResponse>;
  getLlmSettings(): Promise<LlmSettingsResponse>;
  updateLlmSettings(request: LlmSettingsUpdateRequest): Promise<LlmSettingsResponse>;
  testLlmConnection(): Promise<LlmTestConnectionResponse>;
  detectLlmModels(): Promise<DetectModelsResponse>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;
  readonly method: string;

  constructor(method: string, status: number, path: string, detail?: string) {
    super(`${method} ${path} failed with HTTP ${status}${detail ? `: ${detail}` : ""}`);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
    this.method = method;
  }
}

export type EvaClientMode = "rest" | "mock";

async function requestJson<T>(method: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { Accept: "application/json", "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(method, response.status, path, detail || undefined);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(method, response.status, path, "response was not valid JSON");
  }
}

/** REST transport against the frozen A00 endpoints (docs/api-contracts.md §8). */
export function createRestClient(): EvaClient {
  return {
    getTodayCalendar: () => requestJson("GET", "/api/calendar/today", undefined) as Promise<TodayCalendarResponse>,
    getAttention: () => requestJson("GET", "/api/attention", undefined) as Promise<AttentionListResponse>,
    getDecisions: () => requestJson("GET", "/api/decisions", undefined) as Promise<DecisionListResponse>,
    getCurrentFocus: () => requestJson("GET", "/api/focus/current", undefined) as Promise<FocusCurrentResponse>,
    getBriefing: (request) => requestJson("POST", "/api/briefing/meeting", { body: JSON.stringify(request) }) as Promise<BriefingResponse>,
    getAttentionExplanation: (attentionId) => requestJson("GET", `/api/attention/${encodeURIComponent(attentionId)}/explanation`, undefined) as Promise<AttentionExplanationResponse>,
    startFocus: (request) => requestJson("POST", "/api/focus/start", { body: JSON.stringify(request) }) as Promise<FocusSessionResponse>,
    stopFocus: (request) => requestJson("POST", "/api/focus/stop", { body: request ? JSON.stringify(request) : undefined }) as Promise<FocusStopResponse>,
    getFocusSummary: (sessionId) => requestJson("GET", `/api/focus/${encodeURIComponent(sessionId)}/summary`, undefined) as Promise<FocusSummaryResponse>,
    getDecision: (decisionId) => requestJson("GET", `/api/decisions/${encodeURIComponent(decisionId)}`, undefined) as Promise<DecisionResponse>,
    proposeDecisionOutcome: (decisionId, request) => requestJson("POST", `/api/decisions/${encodeURIComponent(decisionId)}/outcome-proposals`, { body: JSON.stringify(request) }) as Promise<ProposedActionResponse>,
    deferDecision: (decisionId, request) => requestJson("POST", `/api/decisions/${encodeURIComponent(decisionId)}/defer`, { body: request ? JSON.stringify(request) : undefined }) as Promise<DecisionResponse>,
    getAction: (actionId) => requestJson("GET", `/api/actions/${encodeURIComponent(actionId)}`, undefined) as Promise<ActionResponse>,
    getApprovalChallenge: (actionId) => requestJson("POST", `/api/actions/${encodeURIComponent(actionId)}/challenge`, undefined) as Promise<ApprovalChallengeResponse>,
    confirmAction: (actionId, request) => requestJson("POST", `/api/actions/${encodeURIComponent(actionId)}/confirm`, { body: JSON.stringify(request) }) as Promise<ActionConfirmResponse>,
    getLlmSettings: () => requestJson("GET", "/api/settings/llm", undefined) as Promise<LlmSettingsResponse>,
    updateLlmSettings: (request) => requestJson("PUT", "/api/settings/llm", { body: JSON.stringify(request) }) as Promise<LlmSettingsResponse>,
    testLlmConnection: () => requestJson("POST", "/api/settings/llm/test", undefined) as Promise<LlmTestConnectionResponse>,
    detectLlmModels: () => requestJson("POST", "/api/settings/llm/detect", undefined) as Promise<DetectModelsResponse>,
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