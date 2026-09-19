/**
 * EVA A00 contract type tests (compile-time gate).
 *
 * These assertions prove the generated TypeScript preserves the canonical
 * discriminated-union and envelope semantics: code that TypeScript accepts
 * here cannot be rejected by the Pydantic boundary purely because a
 * discriminator or correlation was lost in generation.
 *
 * Run: npm --prefix contracts/tscheck run typecheck
 */
import type {
  ApprovalChallengeResponse,
  AttentionItemCreatedPayload,
  CalendarProposalRequest,
  EventEnvelope,
  EventPayload,
  MeetingSpan,
} from "../../frontend/src/api/types.generated.ts";

// ---------------------------------------------------------------------------
// Valid constructions (must compile)
// ---------------------------------------------------------------------------

export const createProposal: CalendarProposalRequest = {
  tool: "calendar.create_event",
  title: "Demo Kickoff (synthetic)",
  span: {
    kind: "timed",
    start: "2026-09-22T10:00:00+02:00",
    end: "2026-09-22T10:30:00+02:00",
    timezone: "Europe/Warsaw",
  },
};

export const rescheduleProposal: CalendarProposalRequest = {
  tool: "calendar.reschedule_event",
  ref: { calendar_id: "primary", event_id: "evt-demo-team-sync-001" },
  new_span: {
    kind: "timed",
    start: "2026-09-21T10:00:00+02:00",
    end: "2026-09-21T10:30:00+02:00",
    timezone: "Europe/Warsaw"
  },
};

export const allDaySpan: MeetingSpan = {
  kind: "all_day",
  start_date: "2026-09-21",
  end_exclusive: "2026-09-22",
};

export const heartbeatEvent: EventEnvelope = {
  schema_version: 1,
  event_id: "evt-test-hb",
  sequence: 1,
  session_id: "session-test",
  occurred_at: "2026-09-21T12:00:00+02:00",
  type: "heartbeat",
  payload: { type: "heartbeat", server_time: "2026-09-21T12:00:00+02:00" },
};

// ---------------------------------------------------------------------------
// Negative cases (each must fail to compile)
// ---------------------------------------------------------------------------

// @ts-expect-error kind is mandatory at the MeetingSpan union boundary
export const invalidSpan: MeetingSpan = {
  start_date: "2026-09-21",
  end_exclusive: "2026-09-22",
};

// @ts-expect-error tool is mandatory at the CalendarProposalRequest boundary
export const missingTool: CalendarProposalRequest = {
  title: "no discriminator",
};

// @ts-expect-error type is mandatory at the EventPayload union boundary
export const missingPayloadType: EventPayload = {
  server_time: "2026-09-21T12:00:00+02:00",
};

declare const attentionPayload: AttentionItemCreatedPayload;

// Two-step assignment keeps the reported error position stable on the
// assignment line regardless of how deeply TypeScript drills into literals.
const mismatchedCandidate = {
  schema_version: 1,
  event_id: "evt-test-bad",
  sequence: 1,
  session_id: "session-test",
  occurred_at: "2026-09-21T12:00:00+02:00",
  type: "heartbeat" as const,
  payload: attentionPayload,
};

// @ts-expect-error outer event type must match the payload discriminator
export const mismatchedEvent: EventEnvelope = mismatchedCandidate;

const missingInnerTypeCandidate = {
  schema_version: 1,
  event_id: "evt-test",
  sequence: 1,
  session_id: "session-test",
  occurred_at: "2026-09-19T19:00:00Z",
  type: "heartbeat" as const,
  payload: {
    server_time: "2026-09-19T19:00:00Z",
  },
};

// @ts-expect-error EventEnvelope payload discriminator is mandatory at the EventPayload boundary
export const missingInnerPayloadType: EventEnvelope = missingInnerTypeCandidate;

// ---------------------------------------------------------------------------
// ApprovalChallengeResponse (A03 challenge transport contract)
// ---------------------------------------------------------------------------

export const issuedChallenge: ApprovalChallengeResponse = {
  action_id: "act-demo-agenda-acme-001",
  revision: 1,
  arguments_digest: "sha256:demo-digest-agenda-0001",
  challenge: "synthetic-challenge-token-not-a-secret",
  expires_at: "2026-09-21T12:05:00+02:00",
};

// @ts-expect-error challenge is mandatory on ApprovalChallengeResponse
export const missingChallenge: ApprovalChallengeResponse = {
  action_id: "act-demo-agenda-acme-001",
  revision: 1,
  arguments_digest: "sha256:demo-digest-agenda-0001",
  expires_at: "2026-09-21T12:05:00+02:00",
};

export const withChannel: ApprovalChallengeResponse = {
  action_id: "act-demo-agenda-acme-001",
  revision: 1,
  arguments_digest: "sha256:demo-digest-agenda-0001",
  challenge: "synthetic-challenge-token-not-a-secret",
  expires_at: "2026-09-21T12:05:00+02:00",
  // @ts-expect-error channel is server-derived at confirmation; not part of the contract
  channel: "ui",
};

// ---------------------------------------------------------------------------
// Narrowing (correlated envelope must narrow payload by outer type)
// ---------------------------------------------------------------------------

export function consume(event: EventEnvelope): string | undefined {
  if (event.type === "heartbeat") {
    const serverTime: string = event.payload.server_time;

    // @ts-expect-error item is not part of HeartbeatPayload
    event.payload.item;

    return serverTime;
  }

  if (event.type === "attention_item_created") {
    return event.payload.item.id;
  }

  return undefined;
}
