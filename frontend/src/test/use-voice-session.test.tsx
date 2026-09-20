import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useVoiceSession, type VoiceSession } from "../hooks/useVoiceSession";
import { ApiError } from "../api/client";
import type { EvaClient, TranscribeAudioRequest } from "../api/client";
import type { TranscribeResponse } from "../api/types.generated";

// ---- Browser media fakes ---------------------------------------------------

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function transcriptResponse(requestId: string, text = "Przygotuj briefing do spotkania z ACME", language = "pl"): TranscribeResponse {
  return {
    request_id: requestId,
    transcript: {
      text,
      language,
      language_confidence: 0.97,
      duration_ms: 2640,
      provider: "faster-whisper",
    },
  };
}

function voiceClient(
  impl: (request: TranscribeAudioRequest) => Promise<TranscribeResponse>
): EvaClient {
  return { transcribeAudio: impl } as unknown as EvaClient;
}

// ---- Harness ----------------------------------------------------------------

let lastSession: VoiceSession | undefined;

function VoiceHarness({ client }: { client: EvaClient }) {
  const voice = useVoiceSession({ client });
  lastSession = voice;
  return (
    <div>
      <p data-testid="state">{voice.state}</p>
      <p data-testid="recording-ms">{voice.recordingMs}</p>
      <button type="button" data-testid="start" onClick={voice.startListening} />
      <button type="button" data-testid="stop" onClick={voice.stopListening} />
      <button type="button" data-testid="cancel" onClick={voice.cancel} />
      {voice.transcript && <p data-testid="transcript">{voice.transcript.text}</p>}
      {voice.error && <p data-testid="error">{voice.error}</p>}
      {voice.notice && <p data-testid="notice">{voice.notice}</p>}
    </div>
  );
}

function setupEnvironment(getUserMediaImpl: ReturnType<typeof vi.fn>) {
  const getUserMedia = getUserMediaImpl;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  return getUserMedia;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

function makeStream() {
  const track = new FakeTrack();
  return { track, stream: { getTracks: () => [track] } as unknown as MediaStream };
}

beforeEach(() => {
  lastSession = undefined;
  FakeRecorder.instances = [];
  FakeRecorder.isTypeSupported = vi.fn((mime: string) => mime.startsWith("audio/webm"));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  // @ts-expect-error test cleanup of the injected jsdom property
  delete navigator.mediaDevices;
  window.sessionStorage.clear();
});

// ---- Tests -------------------------------------------------------------------

describe("useVoiceSession canonical state machine", () => {
  it("runs idle → listening → transcribing → idle and publishes the transcript", async () => {
    const { track, stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const pending = deferred<TranscribeResponse>();
    let echoedRequestId = "";
    const client = voiceClient((request) => {
      echoedRequestId = request.requestId;
      return pending.promise;
    });
    render(<VoiceHarness client={client} />);
    expect(screen.getByTestId("state")).toHaveTextContent("idle");

    fireEvent.click(screen.getByTestId("start"));
    await flush();

    expect(screen.getByTestId("state")).toHaveTextContent("listening");
    expect(FakeRecorder.instances).toHaveLength(1);
    const recorder = FakeRecorder.instances[0];
    expect(recorder.start).toHaveBeenCalled();
    expect(recorder.state).toBe("recording");

    recorder.ondataavailable?.({ data: new Blob(["chunk"], { type: "audio/webm" }) });
    fireEvent.click(screen.getByTestId("stop"));
    expect(track.stop).toHaveBeenCalled();
    expect(screen.getByTestId("state")).toHaveTextContent("transcribing");

    pending.resolve(transcriptResponse(echoedRequestId));
    await flush();

    expect(screen.getByTestId("state")).toHaveTextContent("idle");
    expect(screen.getByTestId("transcript")).toHaveTextContent(
      "Przygotuj briefing do spotkania z ACME"
    );
    expect(screen.queryByTestId("error")).not.toBeInTheDocument();
    // Truthful: no assistant reasoning exists in this slice — never thinking.
    expect(lastSession?.state).toBe("idle");
  });

  it("never enters thinking after transcription completes", async () => {
    const { stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const client = voiceClient((request) =>
      Promise.resolve(transcriptResponse(request.requestId))
    );
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    fireEvent.click(screen.getByTestId("stop"));
    await flush();
    expect(screen.getByTestId("state")).toHaveTextContent("idle");
    expect(lastSession?.state).not.toBe("thinking");
  });

  it("cancel moves listening → interrupted → idle and stops the recorder tracks", async () => {
    vi.useFakeTimers();
    const { track, stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const client = voiceClient(() => deferred<TranscribeResponse>().promise);
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    expect(screen.getByTestId("state")).toHaveTextContent("listening");

    fireEvent.click(screen.getByTestId("cancel"));
    expect(track.stop).toHaveBeenCalled();
    expect(screen.getByTestId("state")).toHaveTextContent("interrupted");

    act(() => {
      vi.advanceTimersByTime(1600);
    });
    expect(screen.getByTestId("state")).toHaveTextContent("idle");
    expect(screen.queryByTestId("transcript")).not.toBeInTheDocument();
  });
});

describe("useVoiceSession media boundaries", () => {
  it("maps permission denial to a readable retry-able message", async () => {
    const getUserMedia = setupEnvironment(
      vi.fn(() => Promise.reject(new DOMException("denied", "NotAllowedError")))
    );
    const client = voiceClient(() => deferred<TranscribeResponse>().promise);
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(screen.getByTestId("state")).toHaveTextContent("error");
    expect(screen.getByTestId("error")).toHaveTextContent(
      "Microphone access was denied. Allow microphone access and try again."
    );
  });

  it("shows an unsupported-browser error when MediaRecorder is missing", async () => {
    const { stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    vi.stubGlobal("MediaRecorder", undefined);
    const client = voiceClient(() => deferred<TranscribeResponse>().promise);
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    expect(screen.getByTestId("error")).toHaveTextContent(
      "This browser does not support audio recording."
    );
  });

  it("shows an unsupported-format error when no compatible MIME can be recorded", async () => {
    const { stream, track } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    FakeRecorder.isTypeSupported = vi.fn(() => false);
    const client = voiceClient(() => deferred<TranscribeResponse>().promise);
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    expect(track.stop).toHaveBeenCalled();
    expect(screen.getByTestId("error")).toHaveTextContent(
      "This browser cannot record audio in a format EVA accepts."
    );
  });

  it("selects the preferred compatible MIME (opus webm) and never an arbitrary container", async () => {
    const { stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const client = voiceClient(() => deferred<TranscribeResponse>().promise);
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    // Preference order begins with audio/webm;codecs=opus.
    expect(FakeRecorder.isTypeSupported).toHaveBeenCalledWith("audio/webm;codecs=opus");
    expect(lastSession?.state).toBe("listening");
  });

  it("auto-stops at the maximum recording duration and proceeds to transcription", async () => {
    vi.useFakeTimers();
    const { stream, track } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const pending = deferred<TranscribeResponse>();
    const client = voiceClient(() => pending.promise);
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();

    act(() => {
      vi.advanceTimersByTime(29_000);
    });
    expect(track.stop).toHaveBeenCalled();
    expect(screen.getByTestId("state")).toHaveTextContent("transcribing");
    expect(screen.getByTestId("notice")).toHaveTextContent(
      "Recording stopped at the maximum duration (29 seconds)."
    );

    pending.reject(new ApiError("POST", 504, "/api/voice/transcribe", "timeout"));
    await flush();
    expect(screen.getByTestId("state")).toHaveTextContent("error");
    expect(screen.getByTestId("error")).toHaveTextContent(
      "Speech recognition timed out. Try again."
    );
  });

  it("refuses to upload a Blob above the 10 MiB frontend bound", async () => {
    const { stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const transcribe = vi.fn(() => deferred<TranscribeResponse>().promise);
    const client = voiceClient(transcribe);
    render(<VoiceHarness client={client} />);

    fireEvent.click(screen.getByTestId("start"));
    await flush();
    const recorder = FakeRecorder.instances[0];
    recorder.ondataavailable?.({
      data: new Blob([new Uint8Array(11 * 1024 * 1024)], { type: "audio/webm" }),
    });
    fireEvent.click(screen.getByTestId("stop"));
    await flush();

    expect(transcribe).not.toHaveBeenCalled();
    expect(screen.getByTestId("state")).toHaveTextContent("error");
    expect(screen.getByTestId("error")).toHaveTextContent(
      "Recording is too large. Try a shorter message."
    );
  });
});

describe("useVoiceSession races and stale responses", () => {
  it("ignores a late response from a superseded transcription request", async () => {
    const { stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const firstPending = deferred<TranscribeResponse>();
    const secondPending = deferred<TranscribeResponse>();
    let call = 0;
    const sentRequestIds: string[] = [];
    const client = voiceClient((request) => {
      sentRequestIds.push(request.requestId);
      return call++ === 0 ? firstPending.promise : secondPending.promise;
    });

    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    FakeRecorder.instances[0].ondataavailable?.({ data: new Blob(["a"]) });
    fireEvent.click(screen.getByTestId("stop"));
    await flush();
    expect(screen.getByTestId("state")).toHaveTextContent("transcribing");

    // New PTT interaction supersedes the in-flight transcription.
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    expect(FakeRecorder.instances).toHaveLength(2);
    expect(screen.getByTestId("state")).toHaveTextContent("listening");

    FakeRecorder.instances[1].ondataavailable?.({ data: new Blob(["b"]) });
    fireEvent.click(screen.getByTestId("stop"));
    await flush();

    // Late response from request A must NEVER publish — even with a valid echo.
    firstPending.resolve(transcriptResponse(sentRequestIds[0], "STALE A"));
    await flush();
    expect(screen.queryByTestId("transcript")).not.toBeInTheDocument();

    secondPending.resolve(transcriptResponse(sentRequestIds[1], "FRESH B"));
    await flush();
    expect(screen.getByTestId("transcript")).toHaveTextContent("FRESH B");
    expect(screen.getByTestId("state")).toHaveTextContent("idle");
  });

  it("rejects a response whose request_id echo does not match the active request", async () => {
    const { stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    let sentRequestId = "";
    const client = voiceClient((request) => {
      sentRequestId = request.requestId;
      return Promise.resolve(transcriptResponse("different-id", "MISMATCHED"));
    });
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    fireEvent.click(screen.getByTestId("stop"));
    await flush();

    expect(sentRequestId).toBeTruthy();
    expect(screen.queryByTestId("transcript")).not.toBeInTheDocument();
    expect(screen.getByTestId("state")).toHaveTextContent("error");
  });

  it("double-start guard never creates two simultaneous recorders", async () => {
    const { stream } = makeStream();
    const getUserMedia = setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const client = voiceClient(() => deferred<TranscribeResponse>().promise);
    render(<VoiceHarness client={client} />);

    fireEvent.click(screen.getByTestId("start"));
    await flush();
    fireEvent.click(screen.getByTestId("start")); // repeat event while listening
    await flush();

    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(FakeRecorder.instances).toHaveLength(1);
    expect(screen.getByTestId("state")).toHaveTextContent("listening");
  });

  it("stops acquired tracks on unmount mid-recording", async () => {
    const { stream, track } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const client = voiceClient(() => deferred<TranscribeResponse>().promise);
    const { unmount } = render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    expect(screen.getByTestId("state")).toHaveTextContent("listening");

    unmount();
    expect(track.stop).toHaveBeenCalled();
  });

  it("aborts the in-flight transcription fetch on cancel", async () => {
    const { stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const pending = deferred<TranscribeResponse>();
    let capturedSignal: AbortSignal | undefined;
    const client = voiceClient((request) => {
      capturedSignal = request.signal;
      return pending.promise;
    });
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    fireEvent.click(screen.getByTestId("stop"));
    await flush();
    expect(screen.getByTestId("state")).toHaveTextContent("transcribing");

    fireEvent.click(screen.getByTestId("cancel"));
    expect(capturedSignal?.aborted).toBe(true);
  });
});

describe("useVoiceSession A05 error mapping", () => {
  const { stream } = makeStream();
  it.each([
    [413, "Recording is too large. Try a shorter message."],
    [415, "This browser produced an unsupported audio format."],
    [503, "Speech recognition is temporarily unavailable."],
    [504, "Speech recognition timed out. Try again."],
  ])("maps HTTP %i to a concise truthful message", async (status, expected) => {
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const client = voiceClient(() =>
      Promise.reject(new ApiError("POST", status, "/api/voice/transcribe", "detail"))
    );
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    fireEvent.click(screen.getByTestId("stop"));
    await flush();
    expect(screen.getByTestId("error")).toHaveTextContent(expected);
    expect(screen.getByTestId("state")).toHaveTextContent("error");
  });

  it("maps 422 no-speech to a no-speech message and never fakes a transcript", async () => {
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const client = voiceClient(() =>
      Promise.reject(
        new ApiError("POST", 422, "/api/voice/transcribe", "audio_silent: no speech detected")
      )
    );
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    fireEvent.click(screen.getByTestId("stop"));
    await flush();
    expect(screen.getByTestId("error")).toHaveTextContent(
      "No clear speech was detected. Try again."
    );
    expect(screen.queryByTestId("transcript")).not.toBeInTheDocument();
  });
});

describe("useVoiceSession language hint behavior", () => {
  it("stores a successful transcript language as the next request's hint", async () => {
    const { stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const languages: Array<string | undefined> = [];
    let call = 0;
    const client = voiceClient((request) => {
      languages.push(request.language);
      return Promise.resolve(
        transcriptResponse(
          request.requestId,
          call++ === 0 ? "Pierwsza wypowiedź" : "Second utterance",
          call === 1 ? "pl" : "en"
        )
      );
    });
    render(<VoiceHarness client={client} />);

    // First attempt: no hint yet.
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    fireEvent.click(screen.getByTestId("stop"));
    await flush();
    expect(screen.getByTestId("transcript")).toHaveTextContent("Pierwsza wypowiedź");
    expect(lastSession?.transcript?.language).toBe("pl");

    // Second attempt: previous language becomes the hint.
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    fireEvent.click(screen.getByTestId("stop"));
    await flush();

    expect(languages[0]).toBeUndefined();
    expect(languages[1]).toBe("pl");
    // The returned transcript keeps its own detected language untouched.
    expect(screen.getByTestId("transcript")).toHaveTextContent("Second utterance");
    expect(lastSession?.transcript?.language).toBe("en");
  });

  it("does not fabricate confidence when the transcript omits it", async () => {
    const { stream } = makeStream();
    setupEnvironment(vi.fn(() => Promise.resolve(stream)));
    const client = voiceClient((request) =>
      Promise.resolve({
        request_id: request.requestId,
        transcript: {
          text: "Hello there",
          language: "en",
          language_confidence: null,
          duration_ms: 900,
          provider: "faster-whisper",
        },
      })
    );
    render(<VoiceHarness client={client} />);
    fireEvent.click(screen.getByTestId("start"));
    await flush();
    fireEvent.click(screen.getByTestId("stop"));
    await flush();
    expect(lastSession?.transcript?.language_confidence).toBeNull();
  });
});
