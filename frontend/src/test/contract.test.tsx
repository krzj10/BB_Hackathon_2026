import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EvaClient } from "../api/client";
import { ApiError, createRestClient } from "../api/client";
import { createMockClient } from "../api/mock";
import fixtureAcme from "../../../contracts/fixtures/meeting_acme_high.json";
import fixtureTeamSync from "../../../contracts/fixtures/meeting_team_sync_medium.json";
import fixtureAttention from "../../../contracts/fixtures/attention_finance_decision.json";
import fixtureDecision from "../../../contracts/fixtures/decision_finance_pln_needs_review.json";
import fixtureFocus from "../../../contracts/fixtures/focus_session_active.json";
import fixtureBriefing from "../../../contracts/fixtures/briefing_acme_pl.json";
import Today from "../pages/Today";
import Attention from "../pages/Attention";
import Decisions from "../pages/Decisions";
import { ActionApproval } from "../components/approvals/ActionApproval";
import { VoiceOrb } from "../components/voice/VoiceOrb";
import type {
  AttentionListResponse,
  AttentionExplanationResponse,
  Decision,
  DecisionListResponse,
  DecisionResponse,
  FocusCurrentResponse,
  FocusSession,
  FocusCompletionSummary,
  Meeting,
  ProposedAction,
  ActionConfirmResponse,
  ApprovalChallengeResponse,
  ApprovalRequest,
  BriefingRequest,
  FocusStartRequest,
  DecisionOutcomeProposalRequest,
  TodayCalendarResponse,
  VoiceState,
  ExecutiveBriefing,
} from "../api/types.generated";

function stubEvaClient(): Pick<
  EvaClient,
  | "getBriefing"
  | "getAttentionExplanation"
  | "startFocus"
  | "stopFocus"
  | "getFocusSummary"
  | "getDecision"
  | "proposeDecisionOutcome"
  | "deferDecision"
  | "getAction"
  | "getApprovalChallenge"
  | "confirmAction"
  | "getLlmSettings"
  | "updateLlmSettings"
  | "testLlmConnection"
  | "detectLlmModels"
> {
  return {
    getBriefing: async () => ({ briefing: { id: "stub" } as unknown as ExecutiveBriefing }),
    getAttentionExplanation: async () => ({ item_id: "stub", policy_version: "policy-v1" }),
    startFocus: async () => ({ session: {} as unknown as FocusSession }),
    stopFocus: async () => ({ session: {} as unknown as FocusSession, summary: {} as unknown as FocusCompletionSummary }),
    getFocusSummary: async () => ({ summary: {} as unknown as FocusCompletionSummary }),
    getDecision: async () => ({ decision: {} as unknown as Decision }),
    proposeDecisionOutcome: async () => ({ action: {} as unknown as ProposedAction }),
    deferDecision: async () => ({ decision: {} as unknown as Decision }),
    getAction: async () => ({ action: {} as unknown as ProposedAction }),
    getApprovalChallenge: async () => ({ action_id: "", revision: 0, arguments_digest: "", challenge: "", expires_at: "" }),
    confirmAction: async () => ({ action: {} as unknown as ProposedAction }),
    getLlmSettings: async () => ({ provider: "", configured: false }),
    updateLlmSettings: async () => ({ provider: "", configured: false }),
    testLlmConnection: async () => ({ health: { status: "unavailable" } }),
    detectLlmModels: async () => ({ models: [] }),
  };
}

/** Minimal valid ProposedAction built from the generated contract (no parallel schema). */
function syntheticAction(overrides: Partial<ProposedAction> = {}): ProposedAction {
  return {
    id: "act-test-decision-001",
    session_id: "sess-test",
    request_id: "req-test",
    revision: 1,
    tool: "decision.record_outcome",
    arguments: { tool: "decision.record_outcome", decision_id: "dec-demo-finance-001", outcome: "accept" },
    arguments_digest: "sha256:test-digest",
    summary: "Record ACCEPT outcome for financial decision",
    reason: "User recorded the outcome locally",
    impact: "Local decision record only. No payment, purchase, supplier commitment, or external instruction is sent.",
    before: { outcome: null, status: "needs_review" },
    after: { outcome: "accept", status: "resolved" },
    policy_version: "policy-v1",
    risk: "high",
    requires_approval: true,
    voice_approval_allowed: false,
    created_at: "2026-09-21T12:00:00+02:00",
    expires_at: "2026-09-21T12:05:00+02:00",
    status: "pending",
    ...overrides,
  };
}

/** Inject a client into a freshly imported page module (same pattern as B02A tests). */
async function importWithClient<T>(moduleName: string, client: EvaClient): Promise<T> {
  vi.resetModules();
  vi.doMock("../api/client", async (importOriginal) => {
    const actual = await importOriginal<typeof import("../api/client")>();
    return { ...actual, getEvaClient: () => client };
  });
  const mod = (await import(moduleName)) as T;
  return mod;
}

function cleanupClientMock() {
  vi.doUnmock("../api/client");
  vi.resetModules();
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  cleanupClientMock();
});

describe("mock transport consumes canonical A00 contract shapes", () => {
  it("returns responses typed as the generated response envelopes", async () => {
    const client = createMockClient();

    const today: TodayCalendarResponse = await client.getTodayCalendar();
    const attention: AttentionListResponse = await client.getAttention();
    const decisions: DecisionListResponse = await client.getDecisions();
    const focus: FocusCurrentResponse = await client.getCurrentFocus();

    expect(today.day).toBe("2026-09-21");
    expect(today.meetings.map((m) => m.title)).toEqual(
      expect.arrayContaining([fixtureTeamSync.title, fixtureAcme.title])
    );
    expect(attention.items[0].id).toBe(fixtureAttention.id);
    expect(attention.items[0].attention_type).toBe("decision_required");
    expect(decisions.items[0].money).toEqual(fixtureDecision.money);
    expect(focus.session?.id).toBe(fixtureFocus.id);
    expect(focus.session?.sender_overrides).toEqual(["cfo.demo@example.com"]);
  });

  it("exposes every canonical fixture value untouched (no parallel schemas)", async () => {
    const client = createMockClient();
    const today = await client.getTodayCalendar();
    const acme = today.meetings.find((m) => m.title === fixtureAcme.title);

    expect(acme).toEqual(fixtureAcme);
  });

  it("supports deterministic empty, error and pending modes (no randomness)", async () => {
    const empty = createMockClient({ mode: "empty" });
    await expect(empty.getTodayCalendar()).resolves.toMatchObject({ meetings: [] });
    await expect(empty.getAttention()).resolves.toMatchObject({ items: [] });
    await expect(empty.getCurrentFocus()).resolves.toMatchObject({ session: null });

    const failing = createMockClient({ mode: "error" });
    await expect(failing.getTodayCalendar()).rejects.toThrow("Simulated transport failure");

    const pending = createMockClient({ mode: "pending" });
    let settled = false;
    void pending.getTodayCalendar().then(() => {
      settled = true;
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(settled).toBe(false);
  });

  it("builds the attention explanation from canonical stored fixture reasons only", async () => {
    const client = createMockClient();
    const explanation: AttentionExplanationResponse = await client.getAttentionExplanation(fixtureAttention.id);

    expect(explanation.item_id).toBe(fixtureAttention.id);
    expect(explanation.priority_reasons).toEqual(fixtureAttention.reasons);
    expect(explanation.delivery_reasons).toEqual(fixtureAttention.delivery_reasons);
    expect(explanation.sources).toEqual(fixtureAttention.sources);
    // policy_version derived from the fixture's own Reason data
    expect(explanation.policy_version).toBe(fixtureAttention.reasons?.[0]?.policy_version);
    // no invented codes
    const allCodes = [
      ...(explanation.priority_reasons ?? []),
      ...(explanation.delivery_reasons ?? []),
    ].map((r) => r.code);
    expect(allCodes).not.toContain("FINANCE_DECISION_REQUIRED");
    expect(allCodes).not.toContain("FOCUS_INACTIVE");
    // every source_id resolves to an actual SourceRef.id
    const sourceIds = new Set((explanation.sources ?? []).map((s) => s.id));
    for (const reason of [...(explanation.priority_reasons ?? []), ...(explanation.delivery_reasons ?? [])]) {
      for (const sid of reason.source_ids ?? []) {
        expect(sourceIds.has(sid)).toBe(true);
      }
    }
  });
});

describe("Today renders through the typed transport", () => {
  it("renders canonical meeting, attention and decision fixture data", async () => {
    render(<Today />);

    await waitFor(() => {
      expect(screen.getByText("ACME Contract Review")).toBeInTheDocument();
    });

    expect(screen.getByText("Team Sync")).toBeInTheDocument();
    expect(screen.getByText(/14:00–15:00/)).toBeInTheDocument();

    const attentionCard = screen
      .getByRole("heading", { level: 3, name: "Attention" })
      .closest("div")!.parentElement as HTMLElement;
    expect(
      within(attentionCard).getAllByText(/Decyzja: wydatki na migracje/i).length
    ).toBeGreaterThan(0);
    expect(within(attentionCard).getAllByText("High").length).toBeGreaterThan(0);

    const decisionCard = screen
      .getByRole("heading", { level: 3, name: "Decisions" })
      .closest("div")!.parentElement as HTMLElement;
    expect(within(decisionCard).getAllByText(/12 400 PLN/i).length).toBeGreaterThan(0);
  });

  it("renders a readable loading state", () => {
    render(<Today />);
    expect(screen.getByText(/Loading your day/)).toBeInTheDocument();
  });

  it("renders a readable empty state when everything is empty", async () => {
    const emptyClient: EvaClient = {
      getTodayCalendar: async () => ({
        day: "2026-09-21",
        timezone: "Europe/Warsaw",
        meetings: [],
        retrieval_status: "complete",
      }),
      getAttention: async () => ({ items: [] }),
      getDecisions: async () => ({ items: [] }),
      getCurrentFocus: async () => ({ session: null }),
      ...stubEvaClient(),
    };

    const { default: TodayFresh } = await importWithClient<typeof import("../pages/Today")>("../pages/Today", emptyClient);
    render(<TodayFresh />);

    expect(await screen.findByText("Nothing on your plate")).toBeInTheDocument();
  });

  it("renders a readable error state with a retry and never fakes success data", async () => {
    const failingClient: EvaClient = {
      getTodayCalendar: async () => {
        throw new Error("boom");
      },
      getAttention: async () => ({ items: [] }),
      getDecisions: async () => ({ items: [] }),
      getCurrentFocus: async () => ({ session: null }),
      ...stubEvaClient(),
    };

    const { default: TodayFresh } = await importWithClient<typeof import("../pages/Today")>("../pages/Today", failingClient);
    render(<TodayFresh />);

    expect(await screen.findByText("Today is unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("ACME Contract Review")).not.toBeInTheDocument();
  });
});

describe("VoiceOrb consumes canonical VoiceState", () => {
  it("accepts every canonical state and renders its truthful accessible label", () => {
    const states: Array<[VoiceState, string]> = [
      ["idle", "EVA is idle"],
      ["wake_listening", "Wake word detected — listening"],
      ["listening", "Listening"],
      ["transcribing", "Transcribing"],
      ["thinking", "Thinking"],
      ["speaking", "Speaking"],
      ["awaiting_approval", "Waiting for your approval"],
      ["interrupted", "Interrupted"],
      ["error", "Voice error"],
    ];

    for (const [state, label] of states) {
      const { unmount } = render(<VoiceOrb state={state} />);
      expect(screen.getByRole("status")).toHaveTextContent(label);
      expect(
        screen.getByRole("button", { name: new RegExp(`EVA voice orb — ${label}`) })
      ).toBeInTheDocument();
      unmount();
    }
  });

  it("stays a native button with a separate status region and does not cycle when controlled", () => {
    const { rerender } = render(<VoiceOrb state="listening" />);
    const button = screen.getByRole("button", { name: /EVA voice orb — Listening/ });
    expect(button.tagName).toBe("BUTTON");
    button.click();
    expect(screen.getByRole("status")).toHaveTextContent("Listening");
    rerender(<VoiceOrb state="speaking" />);
    expect(screen.getByRole("status")).toHaveTextContent("Speaking");
  });

  it("cycles deterministically through the canonical states in preview mode", () => {
    render(<VoiceOrb />);
    const expected: string[] = [
      "Wake word detected — listening",
      "Listening",
      "Transcribing",
      "Thinking",
      "Speaking",
      "Waiting for your approval",
      "Interrupted",
      "Voice error",
      "EVA is idle",
    ];
    for (const label of expected) {
      fireEvent.click(screen.getByRole("button", { name: /eva voice orb/i }));
      expect(screen.getByRole("status")).toHaveTextContent(label);
    }
  });

  it("applies idle breathing only for canonical idle, not interrupted", () => {
    const { unmount: unmountIdle } = render(<VoiceOrb state="idle" />);
    const idleButton = screen.getByRole("button", { name: /EVA voice orb — EVA is idle/ });
    expect(idleButton.className).toContain("animate-orb-breathe");
    unmountIdle();

    const { unmount: unmountInterrupted } = render(<VoiceOrb state="interrupted" />);
    const interruptedButton = screen.getByRole("button", { name: /EVA voice orb — Interrupted/ });
    expect(interruptedButton.className).not.toContain("animate-orb-breathe");
    unmountInterrupted();
  });
});

describe("Today hardening — deterministic fixture mode and truthful state aggregation", () => {
  it("renders canonical meetings regardless of the system date (deterministic fixture mode)", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-15T10:00:00+02:00"));

    render(<Today />);
    expect(await screen.findByText("ACME Contract Review")).toBeInTheDocument();
    expect(screen.getByText("Team Sync")).toBeInTheDocument();
  });

  it("renders canonical meetings when the system date is well past the fixture date", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2027-06-01T08:00:00+02:00"));

    render(<Today />);
    expect(await screen.findByText("ACME Contract Review")).toBeInTheDocument();
    expect(screen.getByText("Team Sync")).toBeInTheDocument();
  });

  it("attention loading does not become 'Nothing on your plate'", async () => {
    const partialClient: EvaClient = {
      getTodayCalendar: async () => ({
        day: "2026-09-21",
        timezone: "Europe/Warsaw",
        meetings: [fixtureAcme as Meeting],
        retrieval_status: "complete",
      }),
      getAttention: () => new Promise<AttentionListResponse>(() => {}),
      getDecisions: async () => ({ items: [] }),
      getCurrentFocus: async () => ({ session: null }),
      ...stubEvaClient(),
    };

    const { default: TodayFresh } = await importWithClient<typeof import("../pages/Today")>("../pages/Today", partialClient);
    render(<TodayFresh />);

    expect(await screen.findByText("ACME Contract Review")).toBeInTheDocument();
    expect(screen.queryByText("Nothing on your plate")).not.toBeInTheDocument();
    expect(screen.getByText("Loading attention…")).toBeInTheDocument();
  });

  it("attention partial error is visibly represented without faking success", async () => {
    const partialFailClient: EvaClient = {
      getTodayCalendar: async () => ({
        day: "2026-09-21",
        timezone: "Europe/Warsaw",
        meetings: [fixtureAcme as Meeting],
        retrieval_status: "complete",
      }),
      getAttention: async () => {
        throw new Error("attention endpoint down");
      },
      getDecisions: async () => ({ items: [fixtureDecision] as unknown as DecisionListResponse["items"] }),
      getCurrentFocus: async () => ({ session: null }),
      ...stubEvaClient(),
    };

    const { default: TodayFresh } = await importWithClient<typeof import("../pages/Today")>("../pages/Today", partialFailClient);
    render(<TodayFresh />);

    expect(await screen.findByText("ACME Contract Review")).toBeInTheDocument();
    expect(screen.getAllByText(/attention endpoint down/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    expect(screen.queryByText("Nothing needs attention.")).not.toBeInTheDocument();
    expect(screen.queryByText("Nothing on your plate")).not.toBeInTheDocument();
  });

  it("full empty state appears only after successful empty responses", async () => {
    const allEmptyClient: EvaClient = {
      getTodayCalendar: async () => ({
        day: "2026-09-21",
        timezone: "Europe/Warsaw",
        meetings: [],
        retrieval_status: "complete",
      }),
      getAttention: async () => ({ items: [] }),
      getDecisions: async () => ({ items: [] }),
      getCurrentFocus: async () => ({ session: null }),
      ...stubEvaClient(),
    };

    const { default: TodayFresh } = await importWithClient<typeof import("../pages/Today")>("../pages/Today", allEmptyClient);
    render(<TodayFresh />);

    expect(await screen.findByText("Nothing on your plate")).toBeInTheDocument();
  });

  it("retry retriggers the reads through the existing reload key", async () => {
    let attentionFails = true;
    const retryClient: EvaClient = {
      getTodayCalendar: async () => ({
        day: "2026-09-21",
        timezone: "Europe/Warsaw",
        meetings: [fixtureAcme as Meeting],
        retrieval_status: "complete",
      }),
      getAttention: async () => {
        if (attentionFails) throw new Error("first attempt fails");
        return { items: [fixtureAttention] } as unknown as AttentionListResponse;
      },
      getDecisions: async () => ({ items: [] }),
      getCurrentFocus: async () => ({ session: null }),
      ...stubEvaClient(),
    };

    const { default: TodayFresh } = await importWithClient<typeof import("../pages/Today")>("../pages/Today", retryClient);
    render(<TodayFresh />);

    expect(await screen.findByText("ACME Contract Review")).toBeInTheDocument();
    expect(screen.getAllByText(/first attempt fails/).length).toBeGreaterThan(0);
    expect(screen.queryByText("Decyzja: wydatki na migracje - 12 400 PLN")).not.toBeInTheDocument();

    attentionFails = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(
      await screen.findByText("Decyzja: wydatki na migracje - 12 400 PLN")
    ).toBeInTheDocument();
    expect(screen.queryByText(/first attempt fails/)).not.toBeInTheDocument();
  });

  it("Next up uses chronological ordering, not array order", async () => {
    const reversedClient: EvaClient = {
      getTodayCalendar: async () => ({
        day: "2026-09-21",
        timezone: "Europe/Warsaw",
        meetings: [fixtureAcme as Meeting, fixtureTeamSync as Meeting],
        retrieval_status: "complete",
      }),
      getAttention: async () => ({ items: [] }),
      getDecisions: async () => ({ items: [] }),
      getCurrentFocus: async () => ({ session: null }),
      ...stubEvaClient(),
    };

    const { default: TodayFresh } = await importWithClient<typeof import("../pages/Today")>("../pages/Today", reversedClient);
    render(<TodayFresh />);

    await screen.findByText("ACME Contract Review");

    const nextUpValue = screen
      .getByText("Next up")
      .nextElementSibling?.textContent;
    expect(nextUpValue).toBe("Team Sync · 09:00");
  });

  it("all-day meeting renders 'All day' without inventing a wall-clock end time", async () => {
    const allDayMeeting: Meeting = {
      ref: { calendar_id: "primary", event_id: "evt-allday-001" },
      title: "Off-site workshop",
      span: { kind: "all_day", start_date: "2026-09-22", end_exclusive: "2026-09-23" },
      priority: "medium",
    };

    const allDayClient: EvaClient = {
      getTodayCalendar: async () => ({
        day: "2026-09-21",
        timezone: "Europe/Warsaw",
        meetings: [allDayMeeting],
        retrieval_status: "complete",
      }),
      getAttention: async () => ({ items: [] }),
      getDecisions: async () => ({ items: [] }),
      getCurrentFocus: async () => ({ session: null }),
      ...stubEvaClient(),
    };

    const { default: TodayFresh } = await importWithClient<typeof import("../pages/Today")>("../pages/Today", allDayClient);
    render(<TodayFresh />);

    expect(await screen.findByText("Off-site workshop")).toBeInTheDocument();
    expect(screen.getByText("All day")).toBeInTheDocument();
    expect(screen.queryByText(/23:59/)).not.toBeInTheDocument();
  });
});

describe("Attention queue → detail → stored explanation → close (Rules-of-Hooks safe)", () => {
  it("opens detail with canonical stored reasons, then closes back to the queue", async () => {
    render(<Attention />);

    // Queue renders the canonical finance item
    const row = await screen.findByRole("button", { name: /Open Decyzja: wydatki na migracje/i });
    fireEvent.click(row);

    // Detail renders with the explanation section
    expect(await screen.findByText("Why am I seeing this?")).toBeInTheDocument();

    // Canonical stored reason texts appear (from attention_finance_decision.json)
    expect(screen.getByText(/Current decision intent with amount >= 1000 PLN threshold\./)).toBeInTheDocument();
    expect(screen.getByText(/Exact sender address maps to configured CFO \(minimum medium floor\)\./)).toBeInTheDocument();
    expect(screen.getByText(/Stored during active Focus session; surfaced once on qualifying exception\./)).toBeInTheDocument();

    // Canonical source ids appear on the stored reasons
    expect(screen.getAllByText(/src-mail-010/).length).toBeGreaterThan(0);

    // No invented explanations replace the stored ones
    expect(screen.queryByText(/FINANCE_DECISION_REQUIRED/)).not.toBeInTheDocument();
    expect(screen.queryByText(/FOCUS_INACTIVE/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Item involves a financial decision requiring review/)).not.toBeInTheDocument();

    // Reason categories stay separated
    expect(screen.getByText("Priority & Classification Reasons")).toBeInTheDocument();
    expect(screen.getByText("Delivery & Focus Reasons")).toBeInTheDocument();

    // Close returns to the queue (local state, no navigation)
    fireEvent.click(screen.getByRole("button", { name: "Close detail" }));
    expect(screen.queryByText("Why am I seeing this?")).not.toBeInTheDocument();
    expect(await screen.findByText("Attention")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open Decyzja: wydatki na migracje/i })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });

  it("renders loading and empty states for the queue", async () => {
    const emptyClient: EvaClient = {
      getTodayCalendar: async () => ({ day: "2026-09-21", timezone: "Europe/Warsaw", meetings: [], retrieval_status: "complete" }),
      getAttention: async () => ({ items: [] }),
      getDecisions: async () => ({ items: [] }),
      getCurrentFocus: async () => ({ session: null }),
      ...stubEvaClient(),
    };

    const { default: AttentionFresh } = await importWithClient<typeof import("../pages/Attention")>("../pages/Attention", emptyClient);
    render(<AttentionFresh />);

    expect(await screen.findByText("Nothing needs attention")).toBeInTheDocument();
  });

  it("renders a readable error state with retry and never fakes data", async () => {
    const failingClient: EvaClient = {
      getTodayCalendar: async () => ({ day: "2026-09-21", timezone: "Europe/Warsaw", meetings: [], retrieval_status: "complete" }),
      getAttention: async () => {
        throw new Error("attention down");
      },
      getDecisions: async () => ({ items: [] }),
      getCurrentFocus: async () => ({ session: null }),
      ...stubEvaClient(),
    };

    const { default: AttentionFresh } = await importWithClient<typeof import("../pages/Attention")>("../pages/Attention", failingClient);
    render(<AttentionFresh />);

    expect(await screen.findByText("Attention unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText(/Decyzja: wydatki na migracje/i)).not.toBeInTheDocument();
  });
});

describe("Decision Inbox: outcome proposal → HIGH approval → challenge → confirm", () => {
  it("RECORD ACCEPT opens approval WITHOUT fetching a challenge; explicit Confirm fetches it exactly once, then confirms", async () => {
    const client = createMockClient();
    const challengeSpy = vi.spyOn(client, "getApprovalChallenge");
    const confirmSpy = vi.spyOn(client, "confirmAction");

    const { default: DecisionsFresh } = await importWithClient<typeof import("../pages/Decisions")>("../pages/Decisions", client);
    render(<DecisionsFresh />);

    // Inbox renders the canonical financial decision
    const row = await screen.findByRole("button", { name: /Open Faktura za migracje/i });
    fireEvent.click(row);

    // Detail shows the financial safety copy
    expect(await screen.findByText(/internal\/local decision record/i)).toBeInTheDocument();

    // ASK EVA is present but explicitly preview/unavailable
    const askEva = screen.getByRole("button", { name: "ASK EVA" });
    expect(askEva).toBeDisabled();
    expect(askEva.getAttribute("title")).toBe("Preview — assistant integration not connected");

    // RECORD ACCEPT → proposal → approval UI; the raw challenge must NOT be
    // fetched merely because the approval screen was opened.
    fireEvent.click(screen.getByRole("button", { name: "RECORD ACCEPT" }));

    expect(await screen.findByText(/High Risk — Explicit Confirmation Required/i)).toBeInTheDocument();
    expect(screen.getByText(/Confirm \(Approve\)/)).toBeInTheDocument();
    expect(challengeSpy).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();

    // Explicit UI confirmation: challenge fetched exactly once, then confirm
    fireEvent.click(screen.getByRole("button", { name: "Confirm (Approve)" }));

    expect(await screen.findByText("Action status: Succeeded.")).toBeInTheDocument();
    expect(challengeSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    // confirmAction occurs only after the challenge call resolves
    expect(challengeSpy.mock.invocationCallOrder[0]).toBeLessThan(confirmSpy.mock.invocationCallOrder[0]);

    // Full ApprovalRequest fields used; challenge was bound to the stored action
    const [challengeActionId] = challengeSpy.mock.calls[0];
    expect(challengeActionId).toBe("act-demo-decision-record-dec-demo-finance-001");
    const [confirmActionId, confirmRequest] = confirmSpy.mock.calls[0] as [string, ApprovalRequest];
    expect(confirmActionId).toBe("act-demo-decision-record-dec-demo-finance-001");
    expect(confirmRequest).toMatchObject({
      action_id: "act-demo-decision-record-dec-demo-finance-001",
      revision: 1,
      arguments_digest: expect.stringContaining("sha256:"),
      choice: "approve",
      challenge: "demo-one-time-challenge-0001",
    });
    expect(screen.queryByText(/Approved & Executed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/approved and executed/i)).not.toBeInTheDocument();
  });

  it("explicit Reject fetches the challenge exactly once and preserves the REJECT outcome end-to-end", async () => {
    const client = createMockClient();
    const challengeSpy = vi.spyOn(client, "getApprovalChallenge");
    const confirmSpy = vi.spyOn(client, "confirmAction");

    const { default: DecisionsFresh } = await importWithClient<typeof import("../pages/Decisions")>("../pages/Decisions", client);
    render(<DecisionsFresh />);

    const row = await screen.findByRole("button", { name: /Open Faktura za migracje/i });
    fireEvent.click(row);

    // RECORD REJECT
    fireEvent.click(await screen.findByRole("button", { name: "RECORD REJECT" }));

    // Approval UI shows the REJECT proposal; no challenge fetched yet
    expect(await screen.findByText(/High Risk — Explicit Confirmation Required/i)).toBeInTheDocument();
    expect(challengeSpy).not.toHaveBeenCalled();

    // Explicit UI confirmation
    fireEvent.click(screen.getByRole("button", { name: "Confirm (Approve)" }));

    expect(await screen.findByText("Action status: Succeeded.")).toBeInTheDocument();
    expect(challengeSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy).toHaveBeenCalledTimes(1);

    // The stored proposal still carries outcome=reject — never silently ACCEPT
    const proposal = await client.getAction("act-demo-decision-record-dec-demo-finance-001");
    expect(proposal.action.arguments).toMatchObject({ outcome: "reject" });
    const confirmResponse = (await confirmSpy.mock.results[0].value) as ActionConfirmResponse;
    expect(confirmResponse.action.arguments).toMatchObject({ outcome: "reject" });
    expect(confirmResponse.result?.data).toMatchObject({ outcome: "reject" });
    expect(confirmResponse.receipt?.action_id).toBe(confirmResponse.action.id);
    expect(confirmResponse.receipt?.revision).toBe(1);
    expect(confirmResponse.receipt?.arguments_digest).toBe(confirmResponse.action.arguments_digest);
    expect(confirmResponse.receipt?.policy_version).toBe(confirmResponse.action.policy_version);
  });

  it("DEFER returns a canonical deferred Decision and never enters the approval flow", async () => {
    const client = createMockClient();
    const challengeSpy = vi.spyOn(client, "getApprovalChallenge");

    const { default: DecisionsFresh } = await importWithClient<typeof import("../pages/Decisions")>("../pages/Decisions", client);
    render(<DecisionsFresh />);

    const row = await screen.findByRole("button", { name: /Open Faktura za migracje/i });
    fireEvent.click(row);

    fireEvent.click(await screen.findByRole("button", { name: "DEFER" }));

    // Returns to the inbox without any approval flow
    expect(await screen.findByRole("button", { name: /Open Faktura za migracje/i })).toBeInTheDocument();
    expect(screen.queryByText(/Explicit Confirmation Required/i)).not.toBeInTheDocument();
    expect(challengeSpy).not.toHaveBeenCalled();

    // Canonical defer semantics: DecisionResponse with status=deferred, no outcome
    const deferResponse: DecisionResponse = await client.deferDecision("dec-demo-finance-001");
    expect(deferResponse.decision.status).toBe("deferred");
    expect(deferResponse.decision.outcome).toBeNull();
    expect(deferResponse.decision.outcome_recorded_at).toBeNull();
  });

  it("proposes a decision.record_outcome action with the requested outcome", async () => {
    const client = createMockClient();
    const request: DecisionOutcomeProposalRequest = {
      outcome: "reject",
      session_id: "sess-test-001",
      request_id: "req-test-001",
    };

    const response = await client.proposeDecisionOutcome("dec-demo-finance-001", request);
    expect(response.action.tool).toBe("decision.record_outcome");
    expect(response.action.arguments).toMatchObject({
      tool: "decision.record_outcome",
      decision_id: "dec-demo-finance-001",
      outcome: "reject",
    });
    expect(response.action.session_id).toBe("sess-test-001");
    expect(response.action.request_id).toBe("req-test-001");
    // Backend-owned fields come from the mock/backend, never computed by the page
    expect(response.action.risk).toBe("high");
    expect(response.action.requires_approval).toBe(true);
    expect(response.action.voice_approval_allowed).toBe(false);
  });
});

describe("Mock approval invariants: binding, replay protection, outcome preservation", () => {
  const propose = (client: EvaClient, outcome: "accept" | "reject" = "accept") =>
    client.proposeDecisionOutcome("dec-demo-finance-001", {
      outcome,
      session_id: "sess-invariant",
      request_id: "req-invariant",
    });

  it("binds the challenge to the exact stored proposed action (id/revision/digest/expiry)", async () => {
    const client = createMockClient();
    const { action } = await propose(client, "accept");

    const challenge = await client.getApprovalChallenge(action.id);
    expect(challenge).toMatchObject({
      action_id: action.id,
      revision: action.revision,
      arguments_digest: action.arguments_digest,
      expires_at: action.expires_at,
      challenge: expect.any(String),
    });
  });

  it("refuses a challenge for a different or unknown action id", async () => {
    const client = createMockClient();
    const { action } = await propose(client);

    await expect(client.getApprovalChallenge("act-some-other-action")).rejects.toThrow(/does not match the stored proposal/);
    await expect(client.getApprovalChallenge("act-never-proposed")).rejects.toThrow();
    void action;
  });

  it("rejects confirmations with wrong action id, revision, digest or challenge", async () => {
    const client = createMockClient();
    const { action } = await propose(client, "accept");
    const challenge = await client.getApprovalChallenge(action.id);
    const validRequest: ApprovalRequest = {
      action_id: action.id,
      revision: action.revision,
      arguments_digest: action.arguments_digest,
      choice: "approve",
      challenge: challenge.challenge,
    };

    // wrong action id: URL/argument pair disagrees
    await expect(
      client.confirmAction("act-other", validRequest)
    ).rejects.toThrow(/does not match request action_id/);
    // wrong action id vs stored proposal (URL and request agree on the wrong id)
    await expect(
      client.confirmAction("act-other", { ...validRequest, action_id: "act-other" })
    ).rejects.toThrow(/does not match the stored proposal/);
    // wrong revision
    await expect(
      client.confirmAction(action.id, { ...validRequest, revision: 99 })
    ).rejects.toThrow(/revision does not match/);
    // wrong digest
    await expect(
      client.confirmAction(action.id, { ...validRequest, arguments_digest: "sha256:wrong" })
    ).rejects.toThrow(/arguments_digest does not match/);
    // wrong challenge token
    await expect(
      client.confirmAction(action.id, { ...validRequest, challenge: "not-the-issued-token" })
    ).rejects.toThrow(/Invalid challenge/);
  });

  it("consumes the challenge once — a replayed confirmation is rejected", async () => {
    const client = createMockClient();
    const { action } = await propose(client, "accept");
    const challenge = await client.getApprovalChallenge(action.id);
    const request: ApprovalRequest = {
      action_id: action.id,
      revision: action.revision,
      arguments_digest: action.arguments_digest,
      choice: "approve",
      challenge: challenge.challenge,
    };

    await expect(client.confirmAction(action.id, request)).resolves.toBeTruthy();
    await expect(client.confirmAction(action.id, request)).rejects.toThrow(/already been consumed/);

    // After consumption the action's canonical state has genuinely advanced
    const after = await client.getAction(action.id);
    expect(after.action.status).toBe("succeeded");
  });

  it("RECORD REJECT stays reject through the completed mock flow (API level)", async () => {
    const client = createMockClient();
    const { action } = await propose(client, "reject");
    const challenge = await client.getApprovalChallenge(action.id);

    const response = await client.confirmAction(action.id, {
      action_id: action.id,
      revision: action.revision,
      arguments_digest: action.arguments_digest,
      choice: "approve",
      challenge: challenge.challenge,
    });

    expect(response.action.arguments).toMatchObject({ outcome: "reject" });
    expect(response.result?.data).toMatchObject({ outcome: "reject" });
    expect(response.receipt?.action_id).toBe(action.id);
    expect(response.receipt?.revision).toBe(action.revision);
    expect(response.receipt?.arguments_digest).toBe(action.arguments_digest);
    expect(response.receipt?.policy_version).toBe(action.policy_version);
  });

  it("two mock clients never share Focus or approval mutable state", async () => {
    const clientA = createMockClient();
    const clientB = createMockClient();

    // A stops Focus; B is unaffected
    await clientA.stopFocus();
    await expect(clientA.getCurrentFocus()).resolves.toMatchObject({ session: null });
    const bFocus = await clientB.getCurrentFocus();
    expect(bFocus.session).not.toBeNull();
    expect(bFocus.session?.id).toBe("focus-demo-001");

    // A proposes an outcome; B has no stored proposal and cannot be challenged
    const { action } = await propose(clientA, "accept");
    await expect(clientB.getApprovalChallenge(action.id)).rejects.toThrow(/No pending action proposal/);
    // ...and B's own proposal flow is independent
    const { action: actionB } = await propose(clientB, "reject");
    expect(actionB.id).toBe(action.id);
    const challengeB = await clientB.getApprovalChallenge(actionB.id);
    expect(challengeB.challenge).toBeTruthy();
  });
});

describe("requires_approval=false never requests a challenge", () => {
  it("registers the proposal and shows honest pending state without Confirm/Reject or challenge", async () => {
    const client = createMockClient();
    const challengeSpy = vi.spyOn(client, "getApprovalChallenge");
    const noApprovalAction = syntheticAction({
      id: "act-no-approval-001",
      risk: "low",
      requires_approval: false,
      voice_approval_allowed: false,
      status: "pending",
    });
    const proposeSpy = vi
      .spyOn(client, "proposeDecisionOutcome")
      .mockResolvedValue({ action: noApprovalAction });
    vi.spyOn(client, "getAction").mockResolvedValue({
      action: { ...noApprovalAction, status: "executing" },
    });

    const { default: DecisionsFresh } = await importWithClient<typeof import("../pages/Decisions")>("../pages/Decisions", client);
    render(<DecisionsFresh />);

    const row = await screen.findByRole("button", { name: /Open Faktura za migracje/i });
    fireEvent.click(row);
    fireEvent.click(await screen.findByRole("button", { name: "RECORD ACCEPT" }));

    // Honest pending state — no approval controls, no challenge
    expect(await screen.findByText(/Proposal registered — no approval required/i)).toBeInTheDocument();
    expect(proposeSpy).toHaveBeenCalledTimes(1);
    expect(challengeSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Confirm (Approve)" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();

    // Canonical action state remains obtainable through getAction
    fireEvent.click(screen.getByRole("button", { name: "Check action state" }));
    expect(await screen.findByText(/Latest canonical state:/)).toBeInTheDocument();
    expect(screen.getByText("Executing…")).toBeInTheDocument();
    expect(challengeSpy).not.toHaveBeenCalled();
  });
});

describe("ASK EVA is visible but explicitly not connected", () => {
  it("renders ASK EVA as disabled preview in decision detail", async () => {
    render(<Decisions />);

    const row = await screen.findByRole("button", { name: /Open Faktura za migracje/i });
    fireEvent.click(row);

    const askEva = await screen.findByRole("button", { name: "ASK EVA" });
    expect(askEva).toBeDisabled();
    expect(askEva.getAttribute("title")).toContain("Preview");
    expect(askEva.getAttribute("title")).toContain("not connected");
  });
});

describe("ActionApproval renders per backend fields and never fabricates a challenge", () => {
  it("emits only the user choice — no challenge is manufactured in the component", () => {
    // The reusable component must not contain any hardcoded challenge value
    expect(ActionApproval.toString()).not.toContain("demo-one-time-challenge-0001");

    const onConfirm = vi.fn();
    render(<ActionApproval action={syntheticAction()} onConfirm={onConfirm} />);

    fireEvent.click(screen.getByRole("button", { name: "Confirm (Approve)" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith("approve");
    expect(onConfirm.mock.calls[0]).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onConfirm).toHaveBeenLastCalledWith("reject");
    expect(onConfirm.mock.calls[1]).toHaveLength(1);
  });

  it("HIGH actions demand explicit UI confirmation and forbid voice", () => {
    render(<ActionApproval action={syntheticAction({ voice_approval_allowed: false })} onConfirm={vi.fn()} />);
    expect(screen.getByText(/High Risk — Explicit Confirmation Required/i)).toBeInTheDocument();
    expect(screen.getByText(/not sufficient/i)).toBeInTheDocument();
    expect(screen.getByText(/Confirm \(Approve\)/)).toBeInTheDocument();
  });

  it("does not present confirm controls when requires_approval is false", () => {
    render(<ActionApproval action={syntheticAction({ requires_approval: false, risk: "low" })} />);
    expect(screen.getByText(/Pending \(No Approval Required\)/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm (Approve)" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("renders canonical pending/executing/succeeded/failed/unknown states truthfully", () => {
    const { unmount } = render(<ActionApproval action={syntheticAction({ status: "unknown" })} />);
    expect(screen.getByText("Unknown State")).toBeInTheDocument();
    expect(screen.getByText(/may have succeeded, failed, or be in progress/i)).toBeInTheDocument();
    expect(screen.queryByText(/Action completed successfully/i)).not.toBeInTheDocument();
    unmount();

    render(<ActionApproval action={syntheticAction({ status: "succeeded" })} />);
    expect(screen.getAllByText("Succeeded").length).toBeGreaterThan(0);
    expect(screen.getByText(/Action completed successfully/i)).toBeInTheDocument();
  });

  it("never labels a merely approved action as executed", () => {
    render(<ActionApproval action={syntheticAction({ status: "approved" })} />);
    expect(screen.getAllByText("Approved").length).toBeGreaterThan(0);
    expect(screen.queryByText(/executed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Approved & Executed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Action completed successfully/i)).not.toBeInTheDocument();
  });
});

describe("Focus lifecycle (mock is internally coherent)", () => {
  it("startFocus uses the supplied request values; stop clears state and returns the canonical summary", async () => {
    const client = createMockClient();

    // Initial fixture state is active; stop it to reach the "off" state
    const firstStop = await client.stopFocus();
    expect(firstStop.summary).toMatchObject({
      focus_session_id: "focus-demo-001",
      total_received: 5,
      deferred_count: 3,
      decision_count: 1,
      action_count: 1,
      fyi_count: 0,
    });
    let current = await client.getCurrentFocus();
    expect(current.session).toBeNull();

    // Start with explicit request values — the mock must use them, not ignore them
    const request: FocusStartRequest = {
      duration_minutes: 90,
      threshold: "high",
      sender_overrides: ["ceo.demo@example.com"],
    };
    const started = await client.startFocus(request);
    expect(started.session.threshold).toBe("high");
    expect(started.session.sender_overrides).toEqual(["ceo.demo@example.com"]);
    const spanMs = new Date(started.session.ends_at).getTime() - new Date(started.session.starts_at).getTime();
    expect(spanMs).toBe(90 * 60 * 1000);

    current = await client.getCurrentFocus();
    expect(current.session?.threshold).toBe("high");
    expect(current.session?.sender_overrides).toEqual(["ceo.demo@example.com"]);

    // Stop again: session cleared, canonical completion summary returned
    const stop = await client.stopFocus();
    expect(stop.summary.total_received).toBe(5);
    expect(stop.session.stopped_at).not.toBeNull();
    current = await client.getCurrentFocus();
    expect(current.session).toBeNull();
  });

  it("UI: start form values → active session → stop → completion summary → off", async () => {
    const client = createMockClient();
    await client.stopFocus(); // start from Focus off

    const { default: TodayFresh } = await importWithClient<typeof import("../pages/Today")>("../pages/Today", client);
    render(<TodayFresh />);

    await screen.findByText("ACME Contract Review");
    expect(screen.getByText("Focus off")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Duration (minutes)"), { target: { value: "90" } });
    fireEvent.change(screen.getByLabelText("Priority threshold"), { target: { value: "high" } });
    fireEvent.change(screen.getByLabelText("Exact sender overrides (comma-separated emails)"), {
      target: { value: "ceo.demo@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start Focus" }));

    // Active session displays the requested values
    expect(await screen.findByText("Focus active")).toBeInTheDocument();
    expect(screen.getByText("High only")).toBeInTheDocument();
    expect(screen.getByText(/Sender overrides: ceo\.demo@example\.com/)).toBeInTheDocument();

    // Stop → canonical completion summary
    fireEvent.click(screen.getByRole("button", { name: "Stop Focus" }));
    expect(await screen.findByText("Focus Completed")).toBeInTheDocument();
    expect(screen.getByText("Total received")).toBeInTheDocument();
    expect(screen.getByText("Total received").nextElementSibling?.textContent).toBe("5");
    expect(screen.getByText("Deferred").nextElementSibling?.textContent).toBe("3");
    // "Decisions" also matches the Today right-rail card title; match the summary stat by its numeric sibling
    const decisionStats = screen.getAllByText("Decisions").map((el) => el.nextElementSibling?.textContent);
    expect(decisionStats).toContain("1");
    expect(screen.getByText("Actions").nextElementSibling?.textContent).toBe("1");
    expect(screen.getByText("FYI").nextElementSibling?.textContent).toBe("0");

    // Dismiss → Focus stays off, no magical re-activation
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(await screen.findByText("Focus off")).toBeInTheDocument();
    expect(screen.queryByText("Focus active")).not.toBeInTheDocument();
  });
});

describe("REST transport matches the frozen API exactly", () => {
  const jsonResponse = (body: unknown, status = 200) =>
    Promise.resolve({
      ok: true,
      status,
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response);

  it("POST /api/briefing/meeting sends BriefingRequest and returns BriefingResponse", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      jsonResponse({ briefing: fixtureBriefing })
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    const request: BriefingRequest = {
      meeting_ref: { calendar_id: "primary", event_id: "evt-demo-acme-contract-review-001" },
      language: "pl",
    };
    const response = await client.getBriefing(request);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/briefing/meeting");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(request);
    expect(response.briefing.id).toBe(fixtureBriefing.id);
    expect(response.briefing.spoken_summary).toBe(fixtureBriefing.spoken_summary);
  });

  it("POST /api/decisions/{id}/outcome-proposals sends the complete DecisionOutcomeProposalRequest", async () => {
    const action = syntheticAction();
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse({ action }));
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    const request: DecisionOutcomeProposalRequest = {
      outcome: "accept",
      session_id: "sess-rest-001",
      request_id: "req-rest-001",
    };
    const response = await client.proposeDecisionOutcome("dec-demo-finance-001", request);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/decisions/dec-demo-finance-001/outcome-proposals");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(request);
    expect(response.action.id).toBe(action.id);
  });

  it("POST /api/decisions/{id}/defer sends DeferDecisionRequest and returns DecisionResponse", async () => {
    const deferred: Decision = {
      ...(fixtureDecision as unknown as Decision),
      status: "deferred",
      outcome: null,
      outcome_recorded_at: null,
    };
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse({ decision: deferred }));
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    const response = await client.deferDecision("dec-demo-finance-001", { reason: "waiting for budget" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/decisions/dec-demo-finance-001/defer");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ reason: "waiting for budget" });
    expect(response.decision.status).toBe("deferred");
  });

  it("POST /api/actions/{id}/challenge sends no body and returns ApprovalChallengeResponse", async () => {
    const challenge: ApprovalChallengeResponse = {
      action_id: "act-demo-decision-record-001",
      revision: 1,
      arguments_digest: "sha256:demo-digest-decision-record-0001",
      challenge: "one-time-token",
      expires_at: "2026-09-21T13:50:00+02:00",
    };
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse(challenge));
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    const response = await client.getApprovalChallenge("act-demo-decision-record-001");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/actions/act-demo-decision-record-001/challenge");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(response.challenge).toBe("one-time-token");
  });

  it("POST /api/actions/{id}/confirm sends the complete ApprovalRequest", async () => {
    const action = syntheticAction({ status: "approved" });
    const fetchMock = vi.fn().mockImplementation(() =>
      jsonResponse({ action, receipt: null, result: null })
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    const request: ApprovalRequest = {
      action_id: "act-demo-decision-record-001",
      revision: 1,
      arguments_digest: "sha256:demo-digest-decision-record-0001",
      choice: "approve",
      challenge: "one-time-token",
    };
    const response = await client.confirmAction("act-demo-decision-record-001", request);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/actions/act-demo-decision-record-001/confirm");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(request);
    expect(response.action.status).toBe("approved");
  });

  it("POST /api/focus/start sends FocusStartRequest and returns FocusSessionResponse", async () => {
    const session = {
      id: "focus-rest-001",
      starts_at: "2026-09-21T12:00:00+02:00",
      ends_at: "2026-09-21T13:30:00+02:00",
      threshold: "high",
      sender_overrides: ["ceo.demo@example.com"],
      policy_version: "policy-v1",
      stopped_at: null,
    };
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse({ session }));
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    const request: FocusStartRequest = {
      duration_minutes: 90,
      threshold: "high",
      sender_overrides: ["ceo.demo@example.com"],
    };
    const response = await client.startFocus(request);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/focus/start");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(request);
    expect(response.session.threshold).toBe("high");
  });

  it("ApiError includes the actual HTTP method, not a hardcoded GET", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: async () => ({}),
        text: async () => "server error",
      } as Response)
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    const error = await client
      .startFocus({ duration_minutes: 60, threshold: "medium" })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).method).toBe("POST");
    expect((error as ApiError).message).toContain("POST");
    expect((error as ApiError).message).not.toContain("GET");
  });
});

describe("Voice/preview interaction cannot confirm a HIGH approval", () => {
  it("clicking the Voice Orb repeatedly never calls confirmAction while approval is pending", async () => {
    const client = createMockClient();
    const confirmSpy = vi.spyOn(client, "confirmAction");
    const challengeSpy = vi.spyOn(client, "getApprovalChallenge");

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });

    // Import ThemeProvider from the same reset module registry as App so the
    // React context instance is shared (a static import would be a stale copy
    // whose context App's ThemeToggle cannot see).
    const [{ default: AppFresh }, themeModule] = await Promise.all([
      import("../App"),
      import("../components/theme/ThemeProvider"),
    ]);
    const FreshThemeProvider = themeModule.ThemeProvider;

    render(
      <FreshThemeProvider>
        <MemoryRouter initialEntries={["/decisions"]}>
          <AppFresh />
        </MemoryRouter>
      </FreshThemeProvider>
    );

    const row = await screen.findByRole("button", { name: /Open Faktura za migracje/i });
    fireEvent.click(row);
    fireEvent.click(await screen.findByRole("button", { name: "RECORD ACCEPT" }));
    expect(await screen.findByText(/High Risk — Explicit Confirmation Required/i)).toBeInTheDocument();
    // Opening the approval screen must not have fetched a challenge
    expect(challengeSpy).not.toHaveBeenCalled();

    // Voice orb preview cycling must not confirm anything
    const orb = screen.getByRole("button", { name: /eva voice orb/i });
    for (let i = 0; i < 5; i += 1) {
      fireEvent.click(orb);
    }
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(challengeSpy).not.toHaveBeenCalled();
    expect(screen.getByText(/High Risk — Explicit Confirmation Required/i)).toBeInTheDocument();

    // Only the explicit UI control confirms
    fireEvent.click(screen.getByRole("button", { name: "Confirm (Approve)" }));
    expect(await screen.findByText("Action status: Succeeded.")).toBeInTheDocument();
    expect(challengeSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(challengeSpy.mock.invocationCallOrder[0]).toBeLessThan(confirmSpy.mock.invocationCallOrder[0]);
  });
});