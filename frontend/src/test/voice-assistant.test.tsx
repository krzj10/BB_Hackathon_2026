import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VoiceControl } from "../components/voice/VoiceControl";
import type { AssistantAskRequest, EvaClient } from "../api/client";
import type { AssistantMessageResponse, TranscribeResponse } from "../api/types.generated";

/**
 * B03 voice turn: the transcript is only REASONED ABOUT when the user presses
 * "Ask Eva"; the reply is rendered and a proposed action is reported without
 * any capability to confirm it from this component.
 */

const TRANSCRIPT_TEXT = "Które decyzje czekają na moją odpowiedź?";

class FakeTrack {
  kind = "audio";
  stop = vi.fn();
  enabled = true;
}

class FakeRecorder {
  static instances: FakeRecorder[] = [];
  static isTypeSupported = vi.fn((mime: string) => mime.startsWith("audio/webm"));
  state = "inactive";
  start = vi.fn(() => {
    this.state = "recording";
  });
  stop = vi.fn(() => {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["chunk"], { type: "audio/webm" }) });
    this.onstop?.();
  });
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() {
    FakeRecorder.instances.push(this);
  }
}

function setupMedia(): void {
  FakeRecorder.instances = [];
  const track = new FakeTrack();
  const stream = { getTracks: () => [track] } as unknown as MediaStream;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(() => Promise.resolve(stream)) },
  });
  vi.stubGlobal("MediaRecorder", FakeRecorder);
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

function voiceClient(
  askAssistant: EvaClient["askAssistant"]
): EvaClient {
  return {
    transcribeAudio: (request: { requestId: string }) =>
      Promise.resolve({
        // The A05 contract echoes the caller's request id; the hook rejects a
        // mismatch as an unexpected response.
        request_id: request.requestId,
        transcript: {
          text: TRANSCRIPT_TEXT,
          language: "pl",
          language_confidence: 0.97,
          duration_ms: 2100,
          provider: "faster-whisper",
        },
      } satisfies TranscribeResponse),
    askAssistant,
  } as unknown as EvaClient;
}

async function recordUtterance(client: EvaClient) {
  render(<VoiceControl client={client} />);
  const orb = screen.getByRole("button", { name: /EVA voice orb/i });
  fireEvent.pointerDown(orb, { pointerId: 1 });
  await flush();
  fireEvent.pointerUp(orb, { pointerId: 1 });
  await flush();
  expect(await screen.findByText(TRANSCRIPT_TEXT)).toBeInTheDocument();
}

afterEach(() => {
  vi.unstubAllGlobals();
  // @ts-expect-error test cleanup of the injected jsdom property
  delete navigator.mediaDevices;
  window.sessionStorage.clear();
});

describe("VoiceControl assistant turn", () => {
  it("reasons only on request and renders Eva's reply", async () => {
    setupMedia();
    const askAssistant = vi.fn(
      async (): Promise<AssistantMessageResponse> => ({
        request_id: "req-ask-1",
        session_id: "sess-test",
        reply_text: "Czekają dwie decyzje: faktura i dostawca sprzętu.",
        language: "pl",
        tool_results: [],
      })
    );
    const client = voiceClient(askAssistant);

    await recordUtterance(client);
    // Nothing is reasoned about before the explicit press.
    expect(askAssistant).not.toHaveBeenCalled();
    expect(screen.getByText(/has not reasoned about it yet/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Ask Eva" }));

    expect(await screen.findByText("Czekają dwie decyzje: faktura i dostawca sprzętu.")).toBeInTheDocument();
    expect(askAssistant).toHaveBeenCalledTimes(1);
    const [sent] = (askAssistant.mock.calls as unknown as [AssistantAskRequest][])[0];
    expect(sent.text).toBe(TRANSCRIPT_TEXT);
    expect(sent.language).toBe("pl");
    expect(sent.sessionId).toBeTruthy();
    expect(sent.requestId).toBeTruthy();
  });

  it("reports a proposed action without any confirmation capability", async () => {
    setupMedia();
    const client = voiceClient(async (): Promise<AssistantMessageResponse> => ({
      request_id: "req-ask-2",
      session_id: "sess-test",
      reply_text: "Przygotowałem propozycję.",
      language: "pl",
      proposed_action: {
        id: "act-1",
        session_id: "sess-test",
        request_id: "req-ask-2",
        revision: 1,
        tool: "calendar.update_agenda",
        arguments: {},
        arguments_digest: "sha256:x",
        summary: "Update agenda",
        reason: "r",
        impact: "i",
        policy_version: "policy-v1",
        risk: "high",
        requires_approval: true,
        voice_approval_allowed: false,
        created_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        status: "pending",
      },
      tool_results: [],
    }));

    await recordUtterance(client);
    fireEvent.click(screen.getByRole("button", { name: "Ask Eva" }));

    expect(await screen.findByText(/calendar\.update_agenda/)).toBeInTheDocument();
    expect(screen.getByText(/still requires the guarded approval UI/)).toBeInTheDocument();
    // This slice exposes no way to approve or confirm anything.
    expect(screen.queryByRole("button", { name: /approve|confirm|zatwierdz/i })).not.toBeInTheDocument();
  });

  it("surfaces an inference failure honestly instead of inventing an answer", async () => {
    setupMedia();
    const client = voiceClient(async () => {
      throw new Error("inference unavailable: no configured self-hosted route");
    });

    await recordUtterance(client);
    fireEvent.click(screen.getByRole("button", { name: "Ask Eva" }));

    const alert = await screen.findByText(/inference unavailable/);
    expect(alert.textContent ?? "").toContain("inference unavailable");
    expect(screen.queryByText(/Eva replied/i)).not.toBeInTheDocument();
  });
});
