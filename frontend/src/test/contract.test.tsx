import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EvaClient } from "../api/client";
import { createMockClient } from "../api/mock";
import fixtureAcme from "../../../contracts/fixtures/meeting_acme_high.json";
import fixtureTeamSync from "../../../contracts/fixtures/meeting_team_sync_medium.json";
import fixtureAttention from "../../../contracts/fixtures/attention_finance_decision.json";
import fixtureDecision from "../../../contracts/fixtures/decision_finance_pln_needs_review.json";
import fixtureFocus from "../../../contracts/fixtures/focus_session_active.json";
import Today from "../pages/Today";
import { VoiceOrb } from "../components/voice/VoiceOrb";
import type {
  AttentionListResponse,
  DecisionListResponse,
  FocusCurrentResponse,
  Meeting,
  TodayCalendarResponse,
  VoiceState,
} from "../api/types.generated";

afterEach(() => {
  vi.useRealTimers();
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
    };

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => emptyClient };
    });

    const { default: TodayFresh } = await import("../pages/Today");
    render(<TodayFresh />);

    expect(await screen.findByText("Nothing on your plate")).toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("renders a readable error state with a retry and never fakes success data", async () => {
    const failingClient: EvaClient = {
      getTodayCalendar: async () => {
        throw new Error("boom");
      },
      getAttention: async () => ({ items: [] }),
      getDecisions: async () => ({ items: [] }),
      getCurrentFocus: async () => ({ session: null }),
    };

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => failingClient };
    });

    const { default: TodayFresh } = await import("../pages/Today");
    render(<TodayFresh />);

    expect(await screen.findByText("Today is unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("ACME Contract Review")).not.toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
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
    };

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => partialClient };
    });

    const { default: TodayFresh } = await import("../pages/Today");
    render(<TodayFresh />);

    expect(await screen.findByText("ACME Contract Review")).toBeInTheDocument();
    expect(screen.queryByText("Nothing on your plate")).not.toBeInTheDocument();
    expect(screen.getByText("Loading attention…")).toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
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
    };

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => partialFailClient };
    });

    const { default: TodayFresh } = await import("../pages/Today");
    render(<TodayFresh />);

    expect(await screen.findByText("ACME Contract Review")).toBeInTheDocument();
    expect(screen.getAllByText(/attention endpoint down/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    expect(screen.queryByText("Nothing needs attention.")).not.toBeInTheDocument();
    expect(screen.queryByText("Nothing on your plate")).not.toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
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
    };

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => allEmptyClient };
    });

    const { default: TodayFresh } = await import("../pages/Today");
    render(<TodayFresh />);

    expect(await screen.findByText("Nothing on your plate")).toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
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
    };

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => retryClient };
    });

    const { default: TodayFresh } = await import("../pages/Today");
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
    vi.doUnmock("../api/client");
    vi.resetModules();
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
    };

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => reversedClient };
    });

    const { default: TodayFresh } = await import("../pages/Today");
    render(<TodayFresh />);

    await screen.findByText("ACME Contract Review");

    const nextUpValue = screen
      .getByText("Next up")
      .nextElementSibling?.textContent;
    expect(nextUpValue).toBe("Team Sync · 09:00");
    vi.doUnmock("../api/client");
    vi.resetModules();
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
    };

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => allDayClient };
    });

    const { default: TodayFresh } = await import("../pages/Today");
    render(<TodayFresh />);

    expect(await screen.findByText("Off-site workshop")).toBeInTheDocument();
    expect(screen.getByText("All day")).toBeInTheDocument();
    expect(screen.queryByText(/23:59/)).not.toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });
});
