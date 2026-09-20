import { useVoiceSession } from "../../hooks/useVoiceSession";
import { getEvaClient } from "../../api/client";
import { Badge } from "../ui/badge";
import { VoiceOrb } from "./VoiceOrb";

/**
 * Voice control slice (B02B): binds the VoiceOrb to the real PTT/STT
 * lifecycle (A05) and presents the latest transcript. Deliberately NOT a
 * chat history — only the latest transcript exists, and no assistant
 * reasoning happens in this slice, so it is labeled truthfully.
 */
export function VoiceControl() {
  const voice = useVoiceSession({ client: getEvaClient() });
  const transcript = voice.transcript;

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
          <p className="mt-2 text-[10.5px] text-subtle-foreground">
            {transcript.provider} · {(transcript.duration_ms / 1000).toFixed(1)}s ·{" "}
            transcribed speech only — EVA has not reasoned about it yet.
          </p>
        </section>
      )}
    </>
  );
}
