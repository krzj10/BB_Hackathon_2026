import * as React from "react";

import { getEvaClient, type EvaClient } from "../../api/client";
import { getEvaSessionId, newRequestId } from "../../lib/session";
import { useSpeechSynthesis } from "../../hooks/useSpeechSynthesis";
import { useVoiceSession } from "../../hooks/useVoiceSession";
import { Badge } from "../ui/badge";
import { VoiceOrb } from "./VoiceOrb";

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
export function VoiceControl({ client = getEvaClient() }: { client?: EvaClient } = {}) {
  const voice = useVoiceSession({ client });
  const speech = useSpeechSynthesis();
  const transcript = voice.transcript;

  const [reply, setReply] = React.useState<{ text: string; language: string } | null>(null);
  const [proposedTool, setProposedTool] = React.useState<string | null>(null);
  const [asking, setAsking] = React.useState(false);
  const [askError, setAskError] = React.useState<string | null>(null);

  // A new recording supersedes the previous answer: one truth on screen.
  React.useEffect(() => {
    setReply(null);
    setProposedTool(null);
    setAskError(null);
  }, [transcript?.text]);

  const askEva = React.useCallback(async () => {
    if (!transcript || asking) return;
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
  }, [transcript, asking, client, speech]);

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
          className="fixed bottom-40 right-5 z-50 max-w-xs rounded-card border border-danger/40 bg-card px-3 py-2 text-[12.5px] text-danger shadow-panel md:bottom-36 md:right-8"
        >
          {voice.error}
        </div>
      )}

      {!voice.error && voice.notice && (
        <div
          role="status"
          className="fixed bottom-40 right-5 z-50 max-w-xs rounded-card border border-border/70 bg-card px-3 py-2 text-[12.5px] text-muted-foreground shadow-panel md:bottom-36 md:right-8"
        >
          {voice.notice}
        </div>
      )}

      {transcript && !voice.error && (
        <section
          aria-label="Latest voice transcript"
          className="fixed bottom-40 right-5 z-50 w-80 max-w-[calc(100vw-2.5rem)] rounded-card border border-border/70 bg-card p-3 shadow-panel md:bottom-36 md:right-8"
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
          <p className="mt-1.5 text-[13.5px] leading-relaxed text-foreground">
            {transcript.text}
          </p>

          <div className="mt-2.5 flex items-center gap-2">
            <button
              type="button"
              onClick={askEva}
              disabled={asking}
              className="rounded-md border border-border/70 bg-secondary px-2.5 py-1 text-[12px] font-medium text-foreground transition-colors hover:bg-secondary/70 disabled:opacity-60"
            >
              {asking ? "Eva is thinking…" : "Ask Eva"}
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
              <p className="mt-1 whitespace-pre-line text-[13.5px] leading-relaxed text-foreground">
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
