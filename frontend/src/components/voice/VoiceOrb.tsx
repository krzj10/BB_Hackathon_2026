import { useState } from "react";
import { cn } from "../../lib/utils";
import type { VoiceState } from "../../api/types.generated";

/**
 * Canonical A00 VoiceState -> presentation treatment. This is a rendering
 * map only, not a second state machine: several canonical states share one
 * visual treatment, while every state keeps its own truthful label.
 */
const ORB_TREATMENT: Record<VoiceState, "idle" | "listening" | "processing" | "speaking" | "error"> = {
  idle: "idle",
  wake_listening: "listening",
  listening: "listening",
  transcribing: "processing",
  thinking: "processing",
  speaking: "speaking",
  awaiting_approval: "processing",
  interrupted: "idle",
  error: "error",
};

const STATE_LABELS: Record<VoiceState, string> = {
  idle: "EVA is idle",
  wake_listening: "Wake word detected — listening",
  listening: "Listening",
  transcribing: "Transcribing",
  thinking: "Thinking",
  speaking: "Speaking",
  awaiting_approval: "Waiting for your approval",
  interrupted: "Interrupted",
  error: "Voice error",
};

const CANONICAL_STATE_ORDER: VoiceState[] = [
  "idle",
  "wake_listening",
  "listening",
  "transcribing",
  "thinking",
  "speaking",
  "awaiting_approval",
  "interrupted",
  "error",
];

/**
 * Global EVA Voice Orb — presentation only. No microphone/STT/TTS/session
 * behavior lives here. In the B02A preview, activation deterministically
 * cycles canonical states for visual inspection; it does not pretend any
 * voice processing is connected.
 */
export function VoiceOrb({
  state: controlledState,
  className,
}: {
  /** Canonical VoiceState from A00; omitted = deterministic preview cycling. */
  state?: VoiceState;
  className?: string;
}) {
  const [previewState, setPreviewState] = useState<VoiceState>("idle");
  const state = controlledState ?? previewState;
  const treatment = ORB_TREATMENT[state];

  const cycle = () => {
    if (controlledState !== undefined) return;
    setPreviewState(
      (current) =>
        CANONICAL_STATE_ORDER[(CANONICAL_STATE_ORDER.indexOf(current) + 1) % CANONICAL_STATE_ORDER.length]
    );
  };

  return (
    <div
      className={cn(
        "group fixed bottom-20 right-5 z-50 md:bottom-8 md:right-8",
        className
      )}
    >
      <span
        role="status"
        className={cn(
          "absolute -top-9 right-0 whitespace-nowrap rounded-full border border-border/70 bg-card px-2.5 py-1 text-[11px] font-medium text-muted-foreground shadow-panel transition-opacity duration-200",
          state !== "idle"
            ? "opacity-100"
            : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
        )}
      >
        {STATE_LABELS[state]}
      </span>

      <button
        type="button"
        onClick={cycle}
        aria-label={`EVA voice orb — ${STATE_LABELS[state]}${
          controlledState === undefined ? " Activate to cycle preview states." : ""
        }`}
        className={cn(
          "relative grid size-16 place-items-center rounded-full bg-orb ring-1 ring-inset ring-white/15",
          "transition-transform duration-200 ease-smooth motion-safe:hover:scale-105 motion-safe:active:scale-95",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          treatment === "idle" && state === "idle" && "animate-orb-breathe shadow-orb"
        )}
      >
        {/* Spherical highlight */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-[6px] rounded-full bg-[radial-gradient(circle_at_30%_25%,oklch(1_0_0/0.18),transparent_55%)]"
        />

        {/* Core dot — static color distinguishes state under reduced motion */}
        <span
          aria-hidden
          className={cn(
            "size-2.5 rounded-full transition-colors duration-300",
            treatment === "error"
              ? "bg-danger"
              : treatment === "idle"
                ? "bg-white/70"
                : "bg-orb-ring"
          )}
        />

        {treatment === "listening" && (
          <span
            aria-hidden
            className="absolute inset-0 animate-orb-ring rounded-full border border-orb-ring"
          />
        )}

        {treatment === "processing" && (
          <span
            aria-hidden
            className="absolute -inset-[3px] animate-orb-spin rounded-full border border-transparent border-r-orb-ring/40 border-t-orb-ring"
          />
        )}

        {treatment === "speaking" && (
          <>
            <span
              aria-hidden
              className="absolute inset-0 animate-orb-echo rounded-full border border-orb-ring/70"
            />
            <span
              aria-hidden
              className="absolute inset-0 animate-orb-echo rounded-full border border-orb-ring/50 [animation-delay:350ms]"
            />
          </>
        )}

        {treatment === "error" && (
          <span
            aria-hidden
            className="absolute inset-0 rounded-full border border-danger/70"
          />
        )}
      </button>
    </div>
  );
}
