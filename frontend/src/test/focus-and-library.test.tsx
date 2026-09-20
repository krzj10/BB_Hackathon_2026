import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { createRestClient, type EvaClient } from "../api/client";
import type { FocusSession, Meeting } from "../api/types.generated";

/* -------------------------------------------------------------------------- */
/* helpers                                                                     */
/* -------------------------------------------------------------------------- */

const jsonResponse = (body: unknown) =>
  Promise.resolve({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);

async function importWithClient<T>(moduleName: string, client: EvaClient): Promise<T> {
  vi.resetModules();
  vi.doMock("../api/client", async (importOriginal) => {
    const actual = await importOriginal<typeof import("../api/client")>();
    return { ...actual, getEvaClient: () => client };
  });
  return (await import(moduleName)) as T;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.doUnmock("../api/client");
  vi.resetModules();
});

function sessionOver(remainingMs: number): FocusSession {
  const now = Date.now();
  return {
    id: "focus-timer-1",
    starts_at: new Date(now - 60_000).toISOString(),
    ends_at: new Date(now + remainingMs).toISOString(),
    threshold: "medium",
    policy_version: "policy-v1",
    stopped_at: null,
  };
}

function meeting(eventId: string, title: string, startMs: number, endMs: number): Meeting {
  return {
    ref: { calendar_id: "primary", event_id: eventId },
    title,
    priority: eventId.includes("acme") ? "high" : "medium",
    span: {
      kind: "timed",
      start: new Date(startMs).toISOString(),
      end: new Date(endMs).toISOString(),
      timezone: "Europe/Warsaw",
    },
  } as Meeting;
}

/* -------------------------------------------------------------------------- */
/* Focus transport                                                             */
/* -------------------------------------------------------------------------- */

describe("Focus REST transport carries the command id the backend requires", () => {
  it("start sends X-EVA-Request-ID and a fresh id per click", async () => {
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse({ session: sessionOver(60_000) }));
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    await client.startFocus({ duration_minutes: 60, threshold: "medium" });
    await client.startFocus({ duration_minutes: 30, threshold: "high" });

    const first = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const second = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(first[0]).toBe("/api/focus/start");
    const firstHeaders = first[1].headers as Record<string, string>;
    const secondHeaders = second[1].headers as Record<string, string>;
    expect(firstHeaders["X-EVA-Request-ID"]).toBeTruthy();
    expect(firstHeaders["X-EVA-Session-ID"]).toBeTruthy();
    expect(secondHeaders["X-EVA-Request-ID"]).not.toBe(firstHeaders["X-EVA-Request-ID"]);
    expect(JSON.parse(first[1].body as string)).toEqual({ duration_minutes: 60, threshold: "medium" });
  });

  it("stop sends X-EVA-Request-ID and always a JSON body", async () => {
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse({ session: sessionOver(0), summary: {} }));
    vi.stubGlobal("fetch", fetchMock);

    await createRestClient().stopFocus();

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/focus/stop");
    expect((init.headers as Record<string, string>)["X-EVA-Request-ID"]).toBeTruthy();
    // The route model has no required fields but FastAPI still needs a body.
    expect(JSON.parse(init.body as string)).toEqual({});
  });

  it("decision defer sends an explicit empty body when no reason is given", async () => {
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse({ decision: {} }));
    vi.stubGlobal("fetch", fetchMock);

    await createRestClient().deferDecision("dec-1");

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({});
  });
});

/* -------------------------------------------------------------------------- */
/* Focus timer                                                                 */
/* -------------------------------------------------------------------------- */

describe("Focus panel runs a live countdown that can be stopped", () => {
  it("shows the remaining time of an active session and stops on demand", async () => {
    const stopFocus = vi.fn(async () => ({
      session: { ...sessionOver(0), stopped_at: new Date().toISOString() },
      summary: {
        focus_session_id: "focus-timer-1",
        ended_at: new Date().toISOString(),
        total_received: 2,
        deferred_count: 1,
        decision_count: 1,
        action_count: 0,
        fyi_count: 0,
      },
    }));
    let current: FocusSession | null = sessionOver(95_000);
    const client = {
      getCurrentFocus: async () => ({ session: current }),
      stopFocus: async () => {
        current = null;
        return stopFocus();
      },
      getFocusSummary: async () => ({ summary: {} as never }),
    } as unknown as EvaClient;

    const { FocusPanel } = await importWithClient<typeof import("../components/focus/FocusPanel")>(
      "../components/focus/FocusPanel",
      client,
    );
    render(<FocusPanel />);

    await screen.findByText("Focus active");
    const timer = screen.getByRole("timer");
    // 95 s remaining → m:ss format, ticking down.
    expect(timer.textContent).toMatch(/^01:3\d$/);
    expect(screen.getByText("Time remaining")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow");

    fireEvent.click(screen.getByRole("button", { name: "Stop Focus" }));
    expect(await screen.findByText("Focus Completed")).toBeInTheDocument();
    expect(stopFocus).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Focus active")).not.toBeInTheDocument();
  });

  it("presents the summary on its own when the countdown reaches zero", async () => {
    const ended = sessionOver(1_200);
    const getFocusSummary = vi.fn(async (_sessionId: string) => ({
      summary: {
        focus_session_id: ended.id,
        ended_at: new Date().toISOString(),
        total_received: 3,
        deferred_count: 2,
        decision_count: 1,
        action_count: 0,
        fyi_count: 0,
      },
    }));
    let current: FocusSession | null = ended;
    const client = {
      getCurrentFocus: async () => ({ session: current }),
      getFocusSummary: async (sessionId: string) => {
        // The backend has expired the session by the time the summary is asked for.
        current = null;
        return getFocusSummary(sessionId);
      },
    } as unknown as EvaClient;

    const { FocusPanel } = await importWithClient<typeof import("../components/focus/FocusPanel")>(
      "../components/focus/FocusPanel",
      client,
    );
    render(<FocusPanel />);
    await screen.findByText("Focus active");

    // Nobody pressed Stop: the timer expiring is what surfaces the summary.
    expect(await screen.findByText("Focus Completed", {}, { timeout: 5_000 })).toBeInTheDocument();
    expect(getFocusSummary).toHaveBeenCalledWith(ended.id);
    expect(screen.getByText("3")).toBeInTheDocument();
  }, 10_000);
});

/* -------------------------------------------------------------------------- */
/* Briefing library                                                            */
/* -------------------------------------------------------------------------- */

describe("briefing library", () => {
  it("leads with the scenario presets and flags overlapping meetings", async () => {
    const base = Date.now();
    const { buildBriefingAnchors } = await import("../pages/Briefings");
    const anchors = buildBriefingAnchors([
      meeting("evt-later", "Investor call", base + 40 * 60_000, base + 80 * 60_000),
      meeting("evt-acme", "Przegląd oferty ACME", base + 5 * 60_000, base + 50 * 60_000),
      meeting("evt-deep", "Deep work", base + 120 * 60_000, base + 240 * 60_000),
    ]);

    expect(anchors[0].label).toBe("Monday Briefing");
    expect(anchors[0].ref.event_id).toBe("evt-acme");
    expect(anchors[1].label).toBe("Briefing after PTO");
    // The ACME block still runs when the investor call starts → both conflict.
    expect(anchors.filter((anchor) => anchor.ref.event_id === "evt-acme")).toHaveLength(3);
    expect(anchors.every((anchor) => anchor.ref.event_id !== "evt-acme" || anchor.conflicting)).toBe(true);
    expect(anchors.find((anchor) => anchor.ref.event_id === "evt-later")?.conflicting).toBe(true);
    expect(anchors.find((anchor) => anchor.ref.event_id === "evt-deep")?.conflicting).toBe(false);
    // Every meeting gets its own briefing on top of the two presets.
    expect(anchors).toHaveLength(5);
  });

  it("renders the preset list and loads the selected briefing", async () => {
    const base = Date.now();
    const meetings = [meeting("evt-acme", "Przegląd oferty ACME", base + 5 * 60_000, base + 50 * 60_000)];
    const getBriefing = vi.fn(async () => ({
      briefing: {
        id: "brf-1",
        meeting: meetings[0],
        language: "pl",
        generated_at: new Date().toISOString(),
        spoken_summary: "Podsumowanie z serwisa briefingów.",
        sources: [],
        retrieval_status: "complete",
      },
    }));
    const client = {
      getTodayCalendar: async () => ({
        day: "2026-09-21",
        timezone: "Europe/Warsaw",
        meetings,
        retrieval_status: "complete",
      }),
      getBriefing,
    } as unknown as EvaClient;

    const { default: BriefingsPage } = await importWithClient<typeof import("../pages/Briefings")>(
      "../pages/Briefings",
      client,
    );
    render(<BriefingsPage />);

    expect(await screen.findByText("Monday Briefing")).toBeInTheDocument();
    expect(screen.getByText("Briefing after PTO")).toBeInTheDocument();
    expect(await screen.findByText("Podsumowanie z serwisa briefingów.")).toBeInTheDocument();
    expect(getBriefing).toHaveBeenCalledWith({
      meeting_ref: { calendar_id: "primary", event_id: "evt-acme" },
      language: "pl",
    });
  });
});

/* -------------------------------------------------------------------------- */
/* Knowledge                                                                   */
/* -------------------------------------------------------------------------- */

describe("knowledge page", () => {
  it("lists grouped entries and narrows them with search", async () => {
    const client = {
      getAttention: async () => ({
        items: [
          {
            id: "att-1",
            sender_email: "anna.kowalska@acme.example",
            title: "Zaktualizowana wycena ACME",
          },
        ],
      }),
    } as unknown as EvaClient;

    const { default: KnowledgePage } = await importWithClient<typeof import("../pages/Knowledge")>(
      "../pages/Knowledge",
      client,
    );
    render(<KnowledgePage />);

    expect(await screen.findByText("Anna Kowalska")).toBeInTheDocument();
    expect(screen.getByText("Eva nie wysyła e-maili")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "Szukaj w wiedzy Evy" }), {
      target: { value: "ACME" },
    });
    expect(await screen.findByText("Anna Kowalska")).toBeInTheDocument();
    expect(screen.queryByText("Ewa Loś")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "Szukaj w wiedzy Evy" }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /^People ·/ }));
    expect(await screen.findByText("Marta Nowak")).toBeInTheDocument();
    // Only people remain: the account and rule entries are filtered out.
    expect(screen.queryByText("ACME", { selector: "h3" })).not.toBeInTheDocument();
    expect(screen.queryByText("Eva nie wysyła e-maili")).not.toBeInTheDocument();
  });
});
