import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VoiceControl } from "../components/voice/VoiceControl";
import { VoiceOrb } from "../components/voice/VoiceOrb";
import { ActionApproval } from "../components/approvals/ActionApproval";
import type { ProposedAction, TranscribeResponse } from "../api/types.generated";

// The mocked transport echoes the requestId and returns a canonical
// transcript whose TEXT contains approval words — proving transcript content
// can never reach ActionApproval.confirmAction.
vi.mock("../api/client", () => ({
  ApiError: class extends Error {
    readonly status: number;
    constructor(_method: string, status: number, _path: string, detail?: string) {
      super(detail ?? "api error");
      this.status = status;
    }
  },
  getEvaClient: () => ({
    transcribeAudio: (request: { requestId: string }) =>
      Promise.resolve({
        request_id: request.requestId,
        transcript: {
          text: "Yes, please confirm and approve this now",
          language: "en",
          language_confidence: 0.98,
          duration_ms: 1800,
          provider: "faster-whisper",
        },
      } satisfies TranscribeResponse),
  }),
}));

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
    // Real MediaRecorder flushes the final dataavailable before onstop.
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

function syntheticHighRiskAction(): ProposedAction {
  return {
    id: "act-test-high-001",
    session_id: "session-test",
    request_id: "req-test",
    revision: 1,
    tool: "calendar.update_agenda",
    arguments: {},
    arguments_digest: "sha256:test",
    summary: "Update ACME agenda",
    reason: "test",
    impact: "test",
    before: {},
    after: {},
    resource_version: "1",
    policy_version: "policy-v1",
    risk: "high",
    requires_approval: true,
    voice_approval_allowed: false,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    status: "pending",
  };
}

function setupMedia(): FakeTrack {
  FakeRecorder.instances = [];
  const track = new FakeTrack();
  const stream = { getTracks: () => [track] } as unknown as MediaStream;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(() => Promise.resolve(stream)) },
  });
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  return track;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  // @ts-expect-error test cleanup of the injected jsdom property
  delete navigator.mediaDevices;
  window.sessionStorage.clear();
});

describe("VoiceControl PTT + approval isolation", () => {
  it("shows hold-to-talk instruction, then the latest transcript with language and provider", async () => {
    const track = setupMedia();
    render(<VoiceControl />);

    expect(screen.getByText("Hold to talk")).toBeInTheDocument();
    // Class/visibility behavior: the idle instruction is visually ENABLED.
    expect(screen.getByText("Hold to talk")).toHaveClass("opacity-100");
    expect(screen.getByRole("status")).toHaveTextContent("EVA is idle");

    const orb = screen.getByRole("button", { name: /EVA voice orb/i });
    fireEvent.pointerDown(orb, { pointerId: 1 });
    await flush();
    expect(screen.getByRole("status")).toHaveTextContent("Listening");
    expect(screen.getByText("Release to transcribe")).toBeInTheDocument();
    // While listening, the release instruction is visually ENABLED.
    expect(screen.getByText("Release to transcribe")).toHaveClass("opacity-100");
    expect(screen.getByText("Release to transcribe")).not.toHaveClass("opacity-0");

    const recorder = FakeRecorder.instances[0];
    recorder.ondataavailable?.({ data: new Blob(["speech"], { type: "audio/webm" }) });
    fireEvent.pointerUp(orb, { pointerId: 1 });
    await flush();

    // The injected transport resolves immediately: listening → transcribing
    // → idle within the flushed microtask chain.
    expect(screen.getByRole("status")).toHaveTextContent("EVA is idle");
    expect(screen.getByText("Yes, please confirm and approve this now")).toBeInTheDocument();
    expect(screen.getByText("en")).toBeInTheDocument();
    expect(screen.getByText(/faster-whisper/)).toBeInTheDocument();
    // Truthful label: no assistant reasoning has occurred yet.
    expect(screen.getByText(/has not reasoned about it yet/i)).toBeInTheDocument();
    expect(track.stop).toHaveBeenCalled();
  });

  it("a transcript full of approval words can never confirm a HIGH action; only the explicit button can", async () => {
    const track = setupMedia();
    const onConfirm = vi.fn();
    render(
      <>
        <VoiceControl />
        <ActionApproval action={syntheticHighRiskAction()} onConfirm={onConfirm} />
      </>
    );

    const orb = screen.getByRole("button", { name: /EVA voice orb/i });
    fireEvent.pointerDown(orb, { pointerId: 1 });
    await flush();
    FakeRecorder.instances[0].ondataavailable?.({ data: new Blob(["speech"]) });
    fireEvent.pointerUp(orb, { pointerId: 1 });
    await flush();
    await flush();

    // Transcript containing yes/confirm/approve is displayed...
    expect(screen.getByText("Yes, please confirm and approve this now")).toBeInTheDocument();
    // ...but it never invokes confirmAction — the explicit UI button must.
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Confirm (Approve)" }));
    expect(onConfirm).toHaveBeenCalledWith("approve");
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(track.stop).toHaveBeenCalled();
  });

  it("pressing the VoiceOrb cannot call confirmAction while an approval is pending", async () => {
    setupMedia();
    const onConfirm = vi.fn();
    render(
      <>
        <VoiceControl />
        <ActionApproval action={syntheticHighRiskAction()} onConfirm={onConfirm} />
      </>
    );

    const orb = screen.getByRole("button", { name: /EVA voice orb/i });
    fireEvent.pointerDown(orb, { pointerId: 1 });
    await flush();
    fireEvent.pointerUp(orb, { pointerId: 1 });
    await flush();
    await flush();

    expect(onConfirm).not.toHaveBeenCalled();
  });
});

describe("VoiceOrb PTT instruction visibility", () => {
  const pttProps = {
    instructional: "Hold to talk",
    onPressStart: () => {},
    onPressEnd: () => {},
    onPressCancel: () => {},
  };

  it("shows Hold to talk enabled while idle", () => {
    const { unmount } = render(<VoiceOrb state="idle" {...pttProps} />);
    const instruction = screen.getByText("Hold to talk");
    expect(instruction).toHaveClass("opacity-100");
    expect(instruction).not.toHaveClass("opacity-0");
    unmount();
  });

  it("switches to Release to transcribe, visually enabled, while listening", () => {
    const { unmount } = render(<VoiceOrb state="listening" {...pttProps} />);
    const instruction = screen.getByText("Release to transcribe");
    expect(instruction).toHaveClass("opacity-100");
    expect(instruction).not.toHaveClass("opacity-0");
    unmount();
  });

  it("hides the instruction while processing (transcribing)", () => {
    const { unmount } = render(<VoiceOrb state="transcribing" {...pttProps} />);
    const instruction = screen.getByText("Hold to talk");
    expect(instruction).toHaveClass("opacity-0");
    expect(instruction).not.toHaveClass("opacity-100");
    unmount();
  });
});
