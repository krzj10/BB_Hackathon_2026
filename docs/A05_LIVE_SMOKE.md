# A05 Voice — Human Live Acceptance Procedure (G-gate input, not automated)

Synthetic/hermetic tests prove wiring, NOT live acceptance. This procedure is
run by a human on the rehearsal machine and its results recorded manually.
Record ONLY: provider/model identifier, language, timing/status. Do NOT commit
recordings unless explicitly synthetic/licensed and intended; never log tokens.

## Prerequisites
- Real `faster-whisper` installed; model downloaded and WARMED (e.g.
  `EVA_WHISPER_MODEL=base`, CPU int8) before the run.
- whisper.cpp fallback: binary + model present if available on this machine
  (`EVA_WHISPER_CPP_BINARY`, `EVA_WHISPER_CPP_MODEL_PATH`); otherwise record
  "fallback unavailable" truthfully.
- ffmpeg on PATH; backend running with the A05 pipeline (health must show
  `components.stt.status = ready` — it reports real runtime readiness).

## Steps (each: run, then note provider/model id, detected language, ms/status)
1. **Real Polish speech** — record ~5 s of natural Polish via the browser PTT
   (Experience UI) or a POST to `/api/voice/transcribe` with a session header;
   verify transcript text is plausible Polish, `language=pl`, provider
   id matches the primary.
2. **Real English speech** — same flow; expect `language=en`.
3. **Silence** — record 3 s of silence; expect HTTP 422 `no_speech_detected`
   (never a fabricated 200 with empty text).
4. **Provider unavailable** — stop/rename the model dir or set an invalid
   model, restart backend: health shows DEGRADED ("primary unavailable; local
   fallback ready") when whisper.cpp works, else UNAVAILABLE; a request either
   succeeds via fallback (provider id = whisper.cpp) or returns 503 with a
   stable sanitized code. No stack traces/paths in responses or logs.
5. **Fallback timing behavior** — with concurrency 1 and a slow utterance, a
   second concurrent request must return a bounded timeout
   (`transcription_timeout`), never hang; after the first finishes, a retry
   succeeds.
6. **Microphone/browser sample (Experience)** — full PTT round-trip through
   the UI once B-side wiring is live: press, speak, release, transcript
   rendered; note end-to-end latency and any interruption behavior.

## Recording template (fill per run, commit only this table)
| step | provider/model | language | ms | status | notes |
|------|----------------|----------|----|--------|-------|
| 1    |                | pl       |    |        |       |
| 2    |                | en       |    |        |       |
| 3    |                | —        |    | 422    |       |
| 4    |                |          |    |        |       |
| 5    |                |          |    |        |       |
| 6    |                |          |    |        |       |

A05 live acceptance is COMPLETE only when steps 1-5 pass on real binaries and
models; step 6 additionally requires the Experience UI wiring.
