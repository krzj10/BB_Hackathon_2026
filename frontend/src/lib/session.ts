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

// Module-memory fallback: when sessionStorage is unavailable (privacy mode,
// blocked storage) the identifier would otherwise change on every call,
// breaking future B03 session continuity.
let memorySessionId: string | null = null;

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
    // storage unavailable (privacy mode) — fall back to module memory
  }
  if (stored) {
    // Synchronize the in-memory fallback with the persisted value.
    memorySessionId = stored;
    return stored;
  }
  if (memorySessionId) return memorySessionId;
  const id = randomUuid();
  memorySessionId = id;
  try {
    window.sessionStorage.setItem(SESSION_KEY, id);
  } catch {
    // non-fatal: identity stays in module memory for the page lifetime
  }
  return id;
}

/**
 * Test-only: clears the in-memory fallback so a test can simulate a fresh
 * context. Never called by production code.
 */
export function resetEvaSessionIdForTests(): void {
  memorySessionId = null;
}

/** Unique per-request identifier (every transcription attempt gets one). */
export function newRequestId(): string {
  return randomUuid();
}
