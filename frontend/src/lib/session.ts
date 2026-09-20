/**
 * Minimal browser session identity for EVA mutations (A05/B03).
 *
 * This is a NON-SECRET, per-browser-tab correlation identifier supplied via
 * the X-EVA-Session-ID header (and later B03 AssistantRequest.session_id).
 * It is never an API key, auth token, or credential, and must never appear
 * in URLs or logs. sessionStorage (not localStorage) keeps it stable for the
 * lifetime of the tab/session only.
 */
const SESSION_KEY = "eva-session-id";

function randomUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // crypto.getRandomValues fallback (older browsers): RFC 4122 v4 shape.
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Stable, non-secret session identifier for this browser tab/session. */
export function getEvaSessionId(): string {
  let stored: string | null = null;
  try {
    stored = window.sessionStorage.getItem(SESSION_KEY);
  } catch {
    // storage unavailable (privacy mode) — regenerate per call context
  }
  if (stored) return stored;
  const id = randomUuid();
  try {
    window.sessionStorage.setItem(SESSION_KEY, id);
  } catch {
    // non-fatal: identity stays in memory for this call
  }
  return id;
}

/** Unique per-request identifier (every transcription attempt gets one). */
export function newRequestId(): string {
  return randomUuid();
}
