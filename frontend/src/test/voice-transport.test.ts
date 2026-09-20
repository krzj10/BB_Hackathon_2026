import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createRestClient } from "../api/client";
import fixtureTranscript from "../../../contracts/fixtures/transcript_pl.json";

const transcriptResponse = {
  request_id: "req-echo-1",
  transcript: fixtureTranscript,
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("A05 multipart voice transport (POST /api/voice/transcribe)", () => {
  it("sends the exact endpoint, method, session header and canonical FormData fields", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(transcriptResponse));
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    const audio = new Blob(["abc"], { type: "audio/webm;codecs=opus" });
    const response = await client.transcribeAudio({
      audio,
      requestId: "req-echo-1",
      sessionId: "session-123",
      language: "pl",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/voice/transcribe");
    expect(init.method).toBe("POST");

    const headers = init.headers as Record<string, string>;
    expect(headers["X-EVA-Session-ID"]).toBe("session-123");
    expect(headers["Accept"]).toBe("application/json");
    // The browser must generate the multipart boundary — never a manual
    // multipart Content-Type.
    expect(headers["Content-Type"]).toBeUndefined();

    const body = init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("request_id")).toBe("req-echo-1");
    expect(body.get("language")).toBe("pl");
    const audioField = body.get("audio");
    expect(audioField).toBeInstanceOf(Blob);
    expect((audioField as Blob).size).toBe(3);

    // Canonical generated TranscribeResponse is consumed as-is.
    expect(response.request_id).toBe("req-echo-1");
    expect(response.transcript.text).toBe(fixtureTranscript.text);
    expect(response.transcript.language).toBe(fixtureTranscript.language);
    expect(response.transcript.provider).toBe(fixtureTranscript.provider);
    expect(response.transcript.duration_ms).toBe(fixtureTranscript.duration_ms);
    expect(response.transcript.language_confidence).toBe(fixtureTranscript.language_confidence);
  });

  it("omits the language field entirely when no hint is supplied", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(transcriptResponse));
    vi.stubGlobal("fetch", fetchMock);

    await createRestClient().transcribeAudio({
      audio: new Blob(["x"], { type: "audio/ogg" }),
      requestId: "req-2",
      sessionId: "session-123",
    });

    const body = (fetchMock.mock.calls[0] as unknown[])[1] as RequestInit;
    const form = body.body as FormData;
    expect(form.has("language")).toBe(false);
    expect(form.get("request_id")).toBe("req-2");
  });

  it("propagates A05 error statuses as ApiError with status intact", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "audio_too_large: exceeded limit" }, 413))
    );

    let caught: unknown;
    await createRestClient()
      .transcribeAudio({
        audio: new Blob(["x"], { type: "audio/wav" }),
        requestId: "req-3",
        sessionId: "session-123",
      })
      .catch((error: unknown) => {
        caught = error;
      });

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(413);
    expect((caught as ApiError).method).toBe("POST");
    expect((caught as ApiError).path).toBe("/api/voice/transcribe");
  });

  it("never sends an X-EVA session value in the URL or body — header only", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(transcriptResponse));
    vi.stubGlobal("fetch", fetchMock);

    await createRestClient().transcribeAudio({
      audio: new Blob(["x"], { type: "audio/mp4" }),
      requestId: "req-4",
      sessionId: "session-xyz",
    });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).not.toContain("session-xyz");
    expect(String(init.body)).not.toContain("session-xyz");
    expect((init.headers as Record<string, string>)["X-EVA-Session-ID"]).toBe("session-xyz");
  });
});
