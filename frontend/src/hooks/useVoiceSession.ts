import * as React from "react";
import { ApiError, getEvaClient, type EvaClient } from "../api/client";
import type { Transcript, VoiceState } from "../api/types.generated";
import { getEvaSessionId, newRequestId } from "../lib/session";

/**
 * Browser-side PTT/STT lifecycle for the A05 voice slice.
 *
 * Canonical VoiceState transitions implemented truthfully for existing
 * capabilities only:
 *
 *   idle -> listening -> transcribing -> idle (transcript retained separately)
 *   listening/transcribing -> error
 *   listening/transcribing/speaking -> interrupted -> idle
 *
 * `thinking` is NEVER entered: there is no assistant call in this slice
 * (B03). No assistant reply is synthesized. PTT/STT never manufactures
 * `awaiting_approval`.
 *
 * The canonical VoiceState is distinct from the INTERNAL capture lifecycle
 * below (VoiceCapturePhase), which models the browser-side microphone and
 * recorder machinery — including the permission-prompt window during which
 * no canonical state change has happened yet.
 */

/** Server hard limit is 30 s; stop at 29 s to leave a margin. */
export const MAX_RECORDING_SECONDS = 29;
/** Frontend mirror of the backend 10 MiB raw-upload bound. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/**
 * Recording MIME preference. Every entry's base MIME is accepted by the A05
 * allowlist (backend strips `;codecs=...` parameters before comparison).
 */
const RECORDING_MIME_PREFERENCE = [
  "audio/webm;codecs=opus",
  "audio/ogg;codecs=opus",
  "audio/webm",
  "audio/ogg",
  "audio/wav",
  "audio/x-wav",
  "audio/mp4",
];

/** Pick the first A05-accepted MIME this browser can actually record. */
export function pickRecordingMime(
  isTypeSupported: (mime: string) => boolean
): string | null {
  for (const mime of RECORDING_MIME_PREFERENCE) {
    if (isTypeSupported(mime)) return mime;
  }
  return null;
}

/**
 * INTERNAL browser capture lifecycle — not a canonical state machine.
 *
 * - `acquiring`: getUserMedia is pending (permission prompt may be open).
 *   A second acquisition can never start, and a release/cancel during this
 *   window invalidates the acquisition so nothing records afterwards.
 * - `stopping`: the recorder is flushing its final dataavailable/onstop.
 *   A new capture MAY start during this interval; the stale finalize
 *   detects the generation change and discards itself without uploading.
 */
export type VoiceCapturePhase =
  | "idle"
  | "acquiring"
  | "listening"
  | "stopping"
  | "transcribing";

/**
 * Capture-local recording state. Every MediaRecorder generation owns its
 * own chunks/MIME/stream so a late `dataavailable`/`onstop` from recording
 * A can never append into, clear, or upload recording B's audio.
 */
interface ActiveCapture {
  generation: number;
  stream: MediaStream;
  recorder: MediaRecorder | null;
  mime: string;
  chunks: Blob[];
  discarded: boolean;
}

export interface VoiceSessionErrorContext {
  status?: number;
  code?: string;
}

/** DOMException instances are not `instanceof Error` everywhere: read the name safely. */
function errorName(error: unknown): string {
  if (typeof error === "object" && error !== null && "name" in error) {
    return String((error as { name: unknown }).name);
  }
  return "";
}

/** Truthful, concise user-facing error text. No raw exception dumps. */
export function mapMediaError(error: unknown): string {
  const name = errorName(error);
  if (name === "NotAllowedError") {
    return "Microphone access was denied. Allow microphone access and try again.";
  }
  if (name === "NotFoundError") {
    return "No microphone was found. Connect a microphone and try again.";
  }
  if (name === "NotReadableError") {
    return "The microphone is in use by another application. Try again.";
  }
  if (name === "NotSupportedError") {
    return "This browser cannot record audio in a format EVA accepts.";
  }
  if (name === "UnsupportedError" || typeof MediaRecorder === "undefined") {
    return "This browser does not support audio recording.";
  }
  return "The microphone could not be started. Try again.";
}

/** Map A05 HTTP outcomes to concise user-facing messages. */
export function mapTranscribeError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    if (errorName(error) === "AbortError") {
      return "Speech recognition was cancelled.";
    }
    return "Speech recognition failed. Try again.";
  }
  switch (error.status) {
    case 413:
      return "Recording is too large. Try a shorter message.";
    case 415:
      return "This browser produced an unsupported audio format.";
    case 503:
      return "Speech recognition is temporarily unavailable.";
    case 504:
      return "Speech recognition timed out. Try again.";
    case 400:
      return "EVA could not process the recording. Try again.";
    default: {
      // Representative 422 sub-codes keep the message truthful.
      const detail = error.message;
      if (detail.includes("audio_too_long")) {
        return "Recording is too long. Try a shorter message.";
      }
      if (detail.includes("audio_silent") || detail.includes("no_speech")) {
        return "No clear speech was detected. Try again.";
      }
      if (detail.includes("audio_empty")) {
        return "No audio was captured. Try again.";
      }
      return "Speech recognition failed. Try again.";
    }
  }
}

export function voiceErrorContext(error: unknown): VoiceSessionErrorContext {
  if (error instanceof ApiError) {
    return { status: error.status, code: error.message.slice(0, 120) };
  }
  if (error instanceof Error && error.name === "AbortError") {
    return { code: "aborted" };
  }
  return {};
}

function stopStreamTracks(stream: MediaStream | null | undefined): void {
  if (!stream) return;
  for (const track of stream.getTracks()) {
    try {
      track.stop();
    } catch {
      // a track that refuses to stop must never break cleanup
    }
  }
}

export interface UseVoiceSessionOptions {
  /** Transport override for tests; defaults to the app client. */
  client?: EvaClient;
  /** Maximum recording duration in seconds (default 29). */
  maxRecordingSeconds?: number;
  /** Called with the canonical state whenever it changes (optional). */
  onStateChange?: (state: VoiceState) => void;
}

export interface VoiceSession {
  /** Canonical A00 VoiceState. */
  state: VoiceState;
  /** Latest accepted transcript; null when none. */
  transcript: Transcript | null;
  /** User-facing error message; null when none. */
  error: string | null;
  /** Informational notice (e.g. max-duration auto-stop); null when none. */
  notice: string | null;
  /** True when a new PTT interaction may begin. */
  canStart: boolean;
  /** True while the microphone is actively captured. */
  isCapturing: boolean;
  /** Elapsed recording time in ms (0 while not recording). */
  recordingMs: number;
  /** Request id of the in-flight transcription attempt. */
  requestId: string | null;
  startListening: () => void;
  stopListening: () => void;
  cancel: () => void;
}

type CleanupMode = "transcribe" | "discard";

export function useVoiceSession(options: UseVoiceSessionOptions = {}): VoiceSession {
  const client = options.client ?? getEvaClient();
  const maxSeconds = options.maxRecordingSeconds ?? MAX_RECORDING_SECONDS;
  const onStateChangeRef = React.useRef(options.onStateChange);
  onStateChangeRef.current = options.onStateChange;

  const [state, setState] = React.useState<VoiceState>("idle");
  const [transcript, setTranscript] = React.useState<Transcript | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [recordingMs, setRecordingMs] = React.useState(0);

  // Generation token: monotonic ownership of the current voice interaction.
  // AbortController alone cannot reject late server completions, so every
  // async continuation re-checks its generation before touching state.
  const generationRef = React.useRef(0);
  const phaseRef = React.useRef<VoiceCapturePhase>("idle");
  // The capture that owns the current recording; null while acquiring.
  const captureRef = React.useRef<ActiveCapture | null>(null);
  const abortRef = React.useRef<AbortController | null>(null);
  const activeRequestIdRef = React.useRef<string | null>(null);
  const languageHintRef = React.useRef<string | undefined>(undefined);
  const startTsRef = React.useRef(0);
  const tickerRef = React.useRef<number | null>(null);
  const autoStopRef = React.useRef<number | null>(null);
  const idleReturnRef = React.useRef<number | null>(null);
  // Mirror of the canonical state so timeouts can route transitions through
  // applyState without stale-closure reads.
  const stateRef = React.useRef<VoiceState>("idle");

  const applyState = React.useCallback((next: VoiceState) => {
    stateRef.current = next;
    setState(next);
    onStateChangeRef.current?.(next);
  }, []);

  const clearTimers = React.useCallback(() => {
    if (tickerRef.current !== null) {
      window.clearInterval(tickerRef.current);
      tickerRef.current = null;
    }
    if (autoStopRef.current !== null) {
      window.clearTimeout(autoStopRef.current);
      autoStopRef.current = null;
    }
  }, []);

  const supersedeInFlight = React.useCallback(() => {
    // Invalidate any older transcription so its late response is ignored.
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const cleanupForCancel = React.useCallback(() => {
    const capture = captureRef.current;
    if (capture) {
      const recorder = capture.recorder;
      if (recorder && recorder.state === "recording") {
        // Discard chunks: cancelled audio is never transcribed or persisted.
        recorder.ondataavailable = null;
        recorder.onstop = null;
        recorder.stop();
      }
      capture.chunks = [];
      capture.discarded = true;
      stopStreamTracks(capture.stream);
      if (captureRef.current === capture) captureRef.current = null;
    }
    clearTimers();
    setRecordingMs(0);
  }, [clearTimers]);

  const runTranscription = React.useCallback(
    async (
      audio: Blob,
      generation: number,
      stoppedAtMax: boolean
    ): Promise<void> => {
      // Nothing is uploaded if the completed Blob exceeds the backend bound.
      if (audio.size > MAX_UPLOAD_BYTES) {
        if (generation !== generationRef.current) return;
        phaseRef.current = "idle";
        applyState("error");
        setError("Recording is too large. Try a shorter message.");
        return;
      }
      if (audio.size === 0) {
        if (generation !== generationRef.current) return;
        phaseRef.current = "idle";
        applyState("error");
        setError("No audio was captured. Try again.");
        return;
      }

      const requestId = newRequestId();
      activeRequestIdRef.current = requestId;
      const controller = new AbortController();
      abortRef.current = controller;
      phaseRef.current = "transcribing";
      applyState("transcribing");
      setNotice(stoppedAtMax ? `Recording stopped at the maximum duration (${maxSeconds} seconds).` : null);

      try {
        const response = await client.transcribeAudio({
          audio,
          requestId,
          sessionId: getEvaSessionId(),
          language: languageHintRef.current,
          signal: controller.signal,
        });

        // Stale-response rejection: a late completion from a superseded
        // generation must NEVER overwrite transcript/state/error.
        if (generation !== generationRef.current) return;
        if (response.request_id !== activeRequestIdRef.current) {
          // Mismatched echo: defense in depth — never publish it.
          phaseRef.current = "idle";
          activeRequestIdRef.current = null;
          applyState("error");
          setError("Speech recognition returned an unexpected response. Try again.");
          return;
        }

        activeRequestIdRef.current = null;
        // Canonical returned language becomes the NEXT request's hint; the
        // returned Transcript is displayed exactly as the server produced it.
        languageHintRef.current = response.transcript.language;
        setTranscript(response.transcript);
        setError(null);
        // The max-duration notice must not linger under the transcript card.
        setNotice(null);
        // Truthful idle: no assistant reasoning exists in this slice, so
        // `thinking` must NOT be entered merely because STT completed.
        phaseRef.current = "idle";
        applyState("idle");
      } catch (err) {
        // Aborted/superseded requests are silent: cancel() owns the state.
        if (generation !== generationRef.current) return;
        if (errorName(err) === "AbortError") return;
        phaseRef.current = "idle";
        activeRequestIdRef.current = null;
        applyState("error");
        setError(mapTranscribeError(err));
      } finally {
        // A settled request no longer owns the abort controller — but the
        // identity check can never clear a newer generation's controller.
        if (abortRef.current === controller) abortRef.current = null;
        if (activeRequestIdRef.current === requestId) {
          activeRequestIdRef.current = null;
        }
      }
    },
    [applyState, client, maxSeconds]
  );

  const finishCapture = React.useCallback(
    (capture: ActiveCapture, mode: CleanupMode, stoppedAtMax: boolean) => {
      clearTimers();
      let finalized = false;
      const finalize = () => {
        if (finalized) return;
        finalized = true;
        capture.discarded = true;
        // Release every MediaStreamTrack on every path (idempotent).
        stopStreamTracks(capture.stream);
        setRecordingMs(0);
        if (captureRef.current === capture) captureRef.current = null;
        if (mode === "discard") {
          capture.chunks = [];
          return;
        }
        // A superseded capture must never be uploaded: this check runs in
        // the recorder's final onstop path BEFORE any await, so a recording
        // whose generation was replaced is discarded outright.
        if (capture.generation !== generationRef.current) {
          capture.chunks = [];
          return;
        }
        const blob = new Blob(capture.chunks, { type: capture.mime || "audio/webm" });
        capture.chunks = [];
        void runTranscription(blob, capture.generation, stoppedAtMax);
      };

      const recorder = capture.recorder;
      if (recorder && recorder.state === "recording") {
        recorder.onstop = finalize;
        recorder.stop();
      } else {
        finalize();
      }
      // The microphone is released immediately, regardless of when the
      // recorder's asynchronous final flush lands.
      stopStreamTracks(capture.stream);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [clearTimers]
  );

  const startListening = React.useCallback(() => {
    // Double-start guard covering BOTH the listening phase and the
    // acquisition window: repeated pointer/key events while getUserMedia is
    // still pending must never create a second microphone request.
    if (phaseRef.current === "acquiring" || phaseRef.current === "listening") return;

    // Starting a new PTT while an older transcription is in progress:
    // invalidate the old request, ignore any late response, start cleanly.
    if (phaseRef.current === "transcribing") {
      supersedeInFlight();
      phaseRef.current = "idle";
    }
    // phase "stopping" falls through deliberately: the previous recorder is
    // only flushing. Its stale finalize detects the generation change below
    // and discards itself without uploading (capture-local chunk ownership).

    const generation = ++generationRef.current;
    phaseRef.current = "acquiring";
    setError(null);
    setNotice(null);

    void (async () => {
      // Retain the local stream so every failure after acquisition still
      // stops the microphone (no leak on constructor/start errors).
      let stream: MediaStream | null = null;
      try {
        if (
          typeof navigator === "undefined" ||
          !navigator.mediaDevices?.getUserMedia ||
          typeof MediaRecorder === "undefined"
        ) {
          throw new DOMException("unsupported", "UnsupportedError");
        }
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (generation !== generationRef.current || phaseRef.current !== "acquiring") {
          // Released/cancelled while the permission prompt was pending:
          // no recording may begin after the press has already ended.
          stopStreamTracks(stream);
          return;
        }

        // Own the stream in capture state BEFORE anything below can throw.
        const capture: ActiveCapture = {
          generation,
          stream,
          recorder: null,
          mime: "",
          chunks: [],
          discarded: false,
        };
        captureRef.current = capture;

        const mime = pickRecordingMime((candidate) => MediaRecorder.isTypeSupported(candidate));
        if (!mime) {
          throw new DOMException("unsupported recording format", "NotSupportedError");
        }
        const recorder = new MediaRecorder(stream, { mimeType: mime });
        capture.recorder = recorder;
        capture.mime = mime;

        // Bound to THIS capture's own chunks — never a shared array.
        recorder.ondataavailable = (event) => {
          if (capture.discarded) return;
          if (event.data && event.data.size > 0) capture.chunks.push(event.data);
        };
        recorder.onerror = () => {
          if (capture.generation !== generationRef.current) return;
          if (phaseRef.current !== "listening") return;
          phaseRef.current = "idle";
          finishCapture(capture, "discard", false);
          applyState("error");
          setError("Recording failed. Try again.");
        };

        recorder.start();
        phaseRef.current = "listening";
        startTsRef.current = Date.now();
        setRecordingMs(0);
        applyState("listening");

        tickerRef.current = window.setInterval(() => {
          setRecordingMs(Date.now() - startTsRef.current);
        }, 250);
        autoStopRef.current = window.setTimeout(() => {
          // Max-duration auto-stop: proceed to transcription truthfully.
          if (phaseRef.current === "listening") {
            stopListeningRef.current(true);
          }
        }, maxSeconds * 1000);
      } catch (err) {
        if (generation !== generationRef.current) return;
        phaseRef.current = "idle";
        // Every error path after getUserMedia resolved still stops the mic.
        stopStreamTracks(stream);
        const capture = captureRef.current;
        if (capture && capture.generation === generation) {
          capture.chunks = [];
          capture.discarded = true;
          if (captureRef.current === capture) captureRef.current = null;
        }
        applyState("error");
        setError(mapMediaError(err));
      }
    })();
  }, [applyState, finishCapture, maxSeconds, supersedeInFlight]);

  const stopListeningRef = React.useRef<(stoppedAtMax: boolean) => void>(() => {});

  const stopListening = React.useCallback(() => {
    stopListeningRef.current(false);
  }, []);

  const cancel = React.useCallback(() => {
    if (phaseRef.current === "idle") return;
    supersedeInFlight();
    phaseRef.current = "idle";
    cleanupForCancel();
    applyState("interrupted");
    // Canonical interrupted -> idle transition (short courtesy pause).
    if (idleReturnRef.current !== null) window.clearTimeout(idleReturnRef.current);
    idleReturnRef.current = window.setTimeout(() => {
      idleReturnRef.current = null;
      // Route through the same transition helper so onStateChange also
      // observes the canonical interrupted -> idle transition.
      if (stateRef.current === "interrupted") applyState("idle");
    }, 1500);
  }, [applyState, cleanupForCancel, supersedeInFlight]);

  stopListeningRef.current = (stoppedAtMax: boolean) => {
    if (phaseRef.current === "acquiring") {
      // Released before the permission prompt resolved: invalidate the
      // acquisition so no recording can start after the press has ended.
      supersedeInFlight();
      phaseRef.current = "idle";
      return;
    }
    if (phaseRef.current !== "listening") return;
    const capture = captureRef.current;
    if (!capture) {
      phaseRef.current = "idle";
      return;
    }
    phaseRef.current = "stopping";
    finishCapture(capture, "transcribe", stoppedAtMax);
  };

  // Unmount: abort the fetch, stop every track, release every timer.
  React.useEffect(() => {
    return () => {
      generationRef.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
      const capture = captureRef.current;
      if (capture) {
        const recorder = capture.recorder;
        if (recorder && recorder.state === "recording") {
          recorder.ondataavailable = null;
          recorder.onstop = null;
          recorder.stop();
        }
        capture.chunks = [];
        capture.discarded = true;
        stopStreamTracks(capture.stream);
      }
      captureRef.current = null;
      clearTimers();
      if (idleReturnRef.current !== null) window.clearTimeout(idleReturnRef.current);
    };
  }, [clearTimers]);

  return {
    state,
    transcript,
    error,
    notice,
    canStart: state !== "listening",
    isCapturing: state === "listening",
    recordingMs,
    requestId: activeRequestIdRef.current,
    startListening,
    stopListening,
    cancel,
  };
}
