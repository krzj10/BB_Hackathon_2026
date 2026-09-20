import { createMockClient } from "./mock";
import { getEvaSessionId } from "../lib/session";
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
  TranscribeResponse,
  AssistantMessageResponse,
} from "./types.generated";

/**
 * Frontend-only transport input for the A05 multipart endpoint. This is NOT a
 * domain model — the canonical response is the generated TranscribeResponse.
 * `language` is the optional PRIOR CONVERSATION-LANGUAGE HINT (it never
 * forces the provider transcription language).
 */
export interface TranscribeAudioRequest {
  audio: Blob;
  requestId: string;
  sessionId: string;
  language?: string;
  signal?: AbortSignal;
}

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
  transcribeAudio(request: TranscribeAudioRequest): Promise<TranscribeResponse>;
  askAssistant(request: AssistantAskRequest): Promise<AssistantMessageResponse>;
}

/**
 * Frontend-only transport input for POST /api/assistant/message (B03). The
 * canonical response stays the generated AssistantMessageResponse; `language`
 * is the reply language request and `activeContext` carries the screen the
 * user is looking at, exactly as the contract defines it.
 */
export interface AssistantAskRequest {
  text: string;
  requestId: string;
  sessionId: string;
  language?: string;
  activeContext?: Record<string, unknown> | null;
  signal?: AbortSignal;
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
  // JSON requests carry an explicit application/json Content-Type; multipart
  // FormData requests MUST NOT (the browser generates the multipart boundary).
  const supplied = ((init?.headers as Record<string, string> | undefined) ?? {});
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...supplied,
  };
  if (!(init?.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  // Single browser-session authority: every EVA application API request
  // carries the same non-secret per-tab correlation id. An explicitly
  // supplied header (the voice path passes its own) is never replaced.
  headers["X-EVA-Session-ID"] = supplied["X-EVA-Session-ID"] ?? getEvaSessionId();
  const response = await fetch(path, { method, ...init, headers });
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

/** Canonical A05 audio filename stem per accepted container (backend strips MIME parameters anyway). */
function audioFileNameFor(mime: string): string {
  const base = mime.split(";")[0].trim().toLowerCase();
  if (base.includes("webm")) return "recording.webm";
  if (base.includes("ogg")) return "recording.ogg";
  if (base.includes("wav")) return "recording.wav";
  if (base.includes("mp4")) return "recording.mp4";
  return "recording.audio";
}

/**
 * Idempotency key for a single user-initiated local command. The backend keys
 * its idempotent replay on (session, request id), so every distinct click gets
 * a fresh id while one click keeps one id for its whole lifetime.
 */
function newCommandId(): string {
  const webCrypto = typeof crypto !== "undefined" ? crypto : undefined;
  if (webCrypto && typeof webCrypto.randomUUID === "function") return webCrypto.randomUUID();
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
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
    startFocus: (request) => requestJson("POST", "/api/focus/start", { body: JSON.stringify(request), headers: { "X-EVA-Request-ID": newCommandId() } }) as Promise<FocusSessionResponse>,
    stopFocus: (request) => requestJson("POST", "/api/focus/stop", { body: JSON.stringify(request ?? {}), headers: { "X-EVA-Request-ID": newCommandId() } }) as Promise<FocusStopResponse>,
    getFocusSummary: (sessionId) => requestJson("GET", `/api/focus/${encodeURIComponent(sessionId)}/summary`, undefined) as Promise<FocusSummaryResponse>,
    getDecision: (decisionId) => requestJson("GET", `/api/decisions/${encodeURIComponent(decisionId)}`, undefined) as Promise<DecisionResponse>,
    proposeDecisionOutcome: (decisionId, request) => requestJson("POST", `/api/decisions/${encodeURIComponent(decisionId)}/outcome-proposals`, { body: JSON.stringify(request) }) as Promise<ProposedActionResponse>,
    // Routes with an all-optional request model still require a JSON body, so
    // an omitted argument is sent as "{}" rather than an empty request.
    deferDecision: (decisionId, request) => requestJson("POST", `/api/decisions/${encodeURIComponent(decisionId)}/defer`, { body: JSON.stringify(request ?? {}) }) as Promise<DecisionResponse>,
    getAction: (actionId) => requestJson("GET", `/api/actions/${encodeURIComponent(actionId)}`, undefined) as Promise<ActionResponse>,
    getApprovalChallenge: (actionId) => requestJson("POST", `/api/actions/${encodeURIComponent(actionId)}/challenge`, undefined) as Promise<ApprovalChallengeResponse>,
    confirmAction: (actionId, request) => requestJson("POST", `/api/actions/${encodeURIComponent(actionId)}/confirm`, { body: JSON.stringify(request) }) as Promise<ActionConfirmResponse>,
    getLlmSettings: () => requestJson("GET", "/api/settings/llm", undefined) as Promise<LlmSettingsResponse>,
    updateLlmSettings: (request) => requestJson("PUT", "/api/settings/llm", { body: JSON.stringify(request) }) as Promise<LlmSettingsResponse>,
    testLlmConnection: () => requestJson("POST", "/api/settings/llm/test", undefined) as Promise<LlmTestConnectionResponse>,
    detectLlmModels: () => requestJson("POST", "/api/settings/llm/detect", undefined) as Promise<DetectModelsResponse>,
    transcribeAudio: (request: TranscribeAudioRequest) => {
      // Exact A05 multipart field names: audio / request_id / language (only
      // when supplied). The browser generates the multipart Content-Type
      // boundary; we never set it manually. Origin is supplied automatically
      // by the browser and is never spoofed.
      const form = new FormData();
      form.append("audio", request.audio, audioFileNameFor(request.audio.type));
      form.append("request_id", request.requestId);
      if (request.language !== undefined && request.language !== "") {
        form.append("language", request.language);
      }
      return requestJson("POST", "/api/voice/transcribe", {
        body: form,
        headers: { "X-EVA-Session-ID": request.sessionId },
        ...(request.signal ? { signal: request.signal } : {}),
      }) as Promise<TranscribeResponse>;
    },

    askAssistant: (request: AssistantAskRequest) => {
      // B03 assistant turn. The claimed body session MUST equal the trusted
      // header (the backend rejects a mismatch with 403), so both carry the
      // exact same per-tab id; no reasoning happens client-side.
      return requestJson("POST", "/api/assistant/message", {
        body: JSON.stringify({
          request_id: request.requestId,
          session_id: request.sessionId,
          text: request.text,
          language: request.language ?? "pl",
          ...(request.activeContext ? { active_context: request.activeContext } : {}),
        }),
        headers: { "X-EVA-Session-ID": request.sessionId },
        ...(request.signal ? { signal: request.signal } : {}),
      }) as Promise<AssistantMessageResponse>;
    },
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