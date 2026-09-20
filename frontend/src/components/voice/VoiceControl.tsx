import * as React from "react";

import { getEvaClient, type EvaClient } from "../../api/client";
import { getEvaSessionId, newRequestId } from "../../lib/session";
import { useSpeechSynthesis } from "../../hooks/useSpeechSynthesis";
import { useVoiceSession } from "../../hooks/useVoiceSession";
import { Badge } from "../ui/badge";
import { VoiceOrb } from "./VoiceOrb";

/** Cooldown before the assistant can be asked about a fresh transcript: it
 * gives the presenter time to read the transcription (and keeps an accidental
 * double-press from spending an inference round). */
const ASK_COOLDOWN_SECONDS = 5;

/**
 * Voice control slice (B02B + B03 turn): binds the VoiceOrb to the real PTT/STT
 * lifecycle (A05), then — only on an explicit press — sends that transcript to
 * the B03 assistant and plays the reply through browser SpeechSynthesis.
 *
 * Truthful boundaries kept here:
 * - nothing is reasoned about until the user asks (the transcript card says so);
 * - one transcript and one answer exist at a time, never a fake chat history;
 * - a proposed action from the assistant is REPORTED only: approval stays in
 *   the guarded UI flow, this component can never confirm it.
 */
export function VoiceControl({
  client = getEvaClient(),
  askCooldownSeconds = ASK_COOLDOWN_SECONDS,
}: {
  client?: EvaClient;
  /** Test/preview seam for the countdown; production uses 5 seconds. */
  askCooldownSeconds?: number;
} = {}) {
  const voice = useVoiceSession({ client });
  const speech = useSpeechSynthesis();
  const transcript = voice.transcript;

  const [reply, setReply] = React.useState<{ text: string; language: string } | null>(null);
  const [proposedTool, setProposedTool] = React.useState<string | null>(null);
  const [asking, setAsking] = React.useState(false);
  const [askError, setAskError] = React.useState<string | null>(null);
  const [askCountdown, setAskCountdown] = React.useState(0);

  // A new recording supersedes the previous answer (one truth on screen) and
  // restarts the Ask Eva cooldown; the interval is always cleared on change.
  React.useEffect(() => {
    setReply(null);
    setProposedTool(null);
    setAskError(null);
    if (!transcript?.text) {
      setAskCountdown(0);
      return;
    }
    setAskCountdown(Math.max(0, askCooldownSeconds));
    if (askCooldownSeconds <= 0) return;
    const ticker = window.setInterval(() => {
      // Clamped at zero: later ticks are no-ops for React state.
      setAskCountdown((current) => (current <= 1 ? 0 : current - 1));
    }, 1000);
    return () => window.clearInterval(ticker);
  }, [transcript?.text, askCooldownSeconds]);

  const canAsk = Boolean(transcript) && !asking && askCountdown === 0;

  const askEva = React.useCallback(async () => {
    if (!canAsk || !transcript) return;
    const language = transcript.language ?? "pl";
    setAsking(true);
    setAskError(null);
    setReply(null);
    setProposedTool(null);
    speech.cancel();
    try {
      const response = await client.askAssistant({
        text: transcript.text,
        requestId: newRequestId(),
        sessionId: getEvaSessionId(),
        language,
      });
      const replyLanguage = response.language ?? language;
      setReply({ text: response.reply_text, language: replyLanguage });
      if (response.proposed_action) setProposedTool(response.proposed_action.tool);
      speech.speak(response.reply_text, replyLanguage);
    } catch (error) {
      setAskError(error instanceof Error ? error.message : "Eva could not answer");
    } finally {
      setAsking(false);
    }
  }, [canAsk, transcript, client, speech]);

  return (
    <>
      <VoiceOrb
        state={voice.state}
        instructional="Hold to talk"
        onPressStart={voice.startListening}
        onPressEnd={voice.stopListening}
        onPressCancel={voice.cancel}
      />

      {voice.error && (
        <div
          role="alert"
          className="fixed bottom-40 right-5 z-50 max-h-[40dvh] max-w-[calc(100vw-2.5rem)] overflow-y-auto overscroll-contain break-words rounded-card border border-danger/40 bg-card px-3 py-2 text-[12.5px] text-danger shadow-panel [overflow-wrap:anywhere] md:bottom-36 md:right-8"
        >
          {voice.error}
        </div>
      )}

      {!voice.error && voice.notice && (
        <div
          role="status"
          className="fixed bottom-40 right-5 z-50 max-h-[40dvh] max-w-[calc(100vw-2.5rem)] overflow-y-auto overscroll-contain break-words rounded-card border border-border/70 bg-card px-3 py-2 text-[12.5px] text-muted-foreground shadow-panel [overflow-wrap:anywhere] md:bottom-36 md:right-8"
        >
          {voice.notice}
        </div>
      )}

      {transcript && !voice.error && (
        <section
          aria-label="Latest voice transcript"
          className="fixed bottom-40 right-5 z-50 flex max-h-[calc(100dvh-12rem)] w-80 max-w-[calc(100vw-2.5rem)] flex-col overflow-y-auto overscroll-contain rounded-card border border-border/70 bg-card p-3 shadow-panel md:bottom-36 md:right-8"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-medium uppercase tracking-[0.06em] text-subtle-foreground">
              You said
            </span>
            <div className="flex items-center gap-1.5">
              <Badge variant="secondary">{transcript.language}</Badge>
              {typeof transcript.language_confidence === "number" && (
                <span className="text-[10.5px] tabular-nums text-subtle-foreground">
                  {(transcript.language_confidence * 100).toFixed(0)}%
                </span>
              )}
            </div>
          </div>
          <p className="mt-1.5 min-w-0 break-words text-[13.5px] leading-relaxed text-foreground [overflow-wrap:anywhere]">
            {transcript.text}
          </p>

          <div className="mt-2.5 flex items-center gap-2">
            <button
              type="button"
              onClick={askEva}
              disabled={!canAsk}
              aria-label={
                askCountdown > 0 ? `Ask Eva available in ${askCountdown} seconds` : "Ask Eva"
              }
              className="rounded-md border border-border/70 bg-secondary px-2.5 py-1 text-[12px] font-medium text-foreground transition-colors hover:bg-secondary/70 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {asking
                ? "Eva is thinking…"
                : askCountdown > 0
                  ? (
                      <>
                        Ask Eva in <span className="tabular-nums">{askCountdown}</span>s
                      </>
                    )
                  : "Ask Eva"}
            </button>
            {reply && speech.supported && (
              <button
                type="button"
                onClick={() => (speech.speaking ? speech.cancel() : speech.speak(reply.text, reply.language))}
                className="rounded-md border border-border/70 px-2 py-1 text-[11.5px] text-muted-foreground hover:bg-secondary/60"
              >
                {speech.speaking ? "Stop speaking" : "Speak again"}
              </button>
            )}
          </div>

          {askError && (
            <p role="alert" className="mt-2 text-[12px] leading-snug text-danger">
              {askError}
            </p>
          )}

          {reply && (
            <div aria-label="Eva reply" className="mt-2.5 border-t border-border/60 pt-2.5">
              <span className="text-[11px] font-medium uppercase tracking-[0.06em] text-subtle-foreground">
                Eva replied
              </span>
              <p className="mt-1 min-w-0 whitespace-pre-line break-words text-[13.5px] leading-relaxed text-foreground [overflow-wrap:anywhere]">
                {reply.text}
              </p>
              {proposedTool && (
                <p className="mt-1.5 text-[11px] leading-snug text-muted-foreground">
                  Proposed action “{proposedTool}” — it still requires the guarded
                  approval UI and has not been executed.
                </p>
              )}
            </div>
          )}

          <p className="mt-2 text-[10.5px] text-subtle-foreground">
            {reply
              ? `${transcript.provider} · ${(transcript.duration_ms / 1000).toFixed(1)}s · answered by the configured self-hosted model`
              : `${transcript.provider} · ${(transcript.duration_ms / 1000).toFixed(1)}s · transcribed speech only — EVA has not reasoned about it yet.`}
          </p>
        </section>
      )}
    </>
  );
}
