import { createMockClient } from "./mock";
export type {
  AttentionListResponse,
  DecisionListResponse,
  FocusCurrentResponse,
  TodayCalendarResponse,
} from "./types.generated";
import type {
  AttentionListResponse,
  DecisionListResponse,
  FocusCurrentResponse,
  TodayCalendarResponse,
} from "./types.generated";

/**
 * The single typed transport boundary for B02A screens. Screens depend on
 * this interface only; the mock transport swaps to REST without rewrites.
 */
export interface EvaClient {
  getTodayCalendar(): Promise<TodayCalendarResponse>;
  getAttention(): Promise<AttentionListResponse>;
  getDecisions(): Promise<DecisionListResponse>;
  getCurrentFocus(): Promise<FocusCurrentResponse>;
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

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new ApiError(response.status, path);
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
    getTodayCalendar: () => getJson<TodayCalendarResponse>("/api/calendar/today"),
    getAttention: () => getJson<AttentionListResponse>("/api/attention"),
    getDecisions: () => getJson<DecisionListResponse>("/api/decisions"),
    getCurrentFocus: () => getJson<FocusCurrentResponse>("/api/focus/current"),
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
