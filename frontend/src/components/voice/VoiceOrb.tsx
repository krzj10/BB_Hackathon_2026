import React, { useRef, useState } from "react";
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
 * Global EVA Voice Orb — presentation plus PTT affordance. No microphone
 * capture/STT/TTS/session behavior lives here: the canonical VoiceState and
 * press handlers arrive from the voice controller (useVoiceSession). Without
 * PTT handlers it keeps the B02A deterministic preview cycling.
 */
export function VoiceOrb({
  state: controlledState,
  className,
  instructional,
  onPressStart,
  onPressEnd,
  onPressCancel,
  disabled,
}: {
  /** Canonical VoiceState from A00; omitted = deterministic preview cycling. */
  state?: VoiceState;
  className?: string;
  /** Concise PTT instruction for the idle state, e.g. "Hold to talk". */
  instructional?: string;
  /** Press-and-hold handlers (pointer + keyboard). */
  onPressStart?: () => void;
  onPressEnd?: () => void;
  onPressCancel?: () => void;
  disabled?: boolean;
}) {
  const [previewState, setPreviewState] = useState<VoiceState>("idle");
  const state = controlledState ?? previewState;
  const treatment = ORB_TREATMENT[state];
  const isPtt = Boolean(onPressStart);
  const pressedRef = useRef(false);

  const cycle = () => {
    if (controlledState !== undefined) return;
    setPreviewState(
      (current) =>
        CANONICAL_STATE_ORDER[(CANONICAL_STATE_ORDER.indexOf(current) + 1) % CANONICAL_STATE_ORDER.length]
    );
  };

  const beginPress = () => {
    if (disabled || pressedRef.current) return;
    pressedRef.current = true;
    onPressStart?.();
  };

  const endPress = () => {
    if (!pressedRef.current) return;
    pressedRef.current = false;
    onPressEnd?.();
  };

  const cancelPress = () => {
    if (!pressedRef.current) return;
    pressedRef.current = false;
    onPressCancel?.();
  };

  // Native keyboard PTT: hold Space/Enter to talk, release to transcribe.
  // Key repeat must never start multiple recordings; click (from Space/Enter
  // activation) must not re-trigger the cycle in PTT mode.
  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!isPtt) return;
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      if (!event.repeat) beginPress();
    }
  };

  const handleKeyUp = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!isPtt) return;
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      endPress();
    }
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (!isPtt) return;
    if (event.pointerType === "touch") event.preventDefault();
    // Capture the pointer so releasing outside the orb still ends cleanly.
    event.currentTarget.setPointerCapture?.(event.pointerId);
    beginPress();
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (!isPtt) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    endPress();
  };

  const handlePointerCancel = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (!isPtt) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    cancelPress();
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

      {isPtt && instructional && (
        <span
          className={cn(
            "absolute -top-16 right-0 whitespace-nowrap rounded-full bg-card px-2.5 py-1 text-[11px] font-medium text-muted-foreground shadow-panel ring-1 ring-inset ring-border/70",
            state === "idle" ? "opacity-100" : "opacity-0"
          )}
        >
          {state === "listening" ? "Release to transcribe" : instructional}
        </span>
      )}

      <button
        type="button"
        onClick={isPtt ? undefined : cycle}
        onKeyDown={isPtt ? handleKeyDown : undefined}
        onKeyUp={isPtt ? handleKeyUp : undefined}
        onPointerDown={isPtt ? handlePointerDown : undefined}
        onPointerUp={isPtt ? handlePointerUp : undefined}
        onPointerCancel={isPtt ? handlePointerCancel : undefined}
        onBlur={isPtt ? cancelPress : undefined}
        disabled={disabled}
        aria-label={`EVA voice orb — ${STATE_LABELS[state]}${
          controlledState === undefined ? " Activate to cycle preview states." : ""
        }`}
        className={cn(
          "relative grid size-16 place-items-center rounded-full bg-orb ring-1 ring-inset ring-white/15 touch-none select-none",
          "transition-transform duration-200 ease-smooth motion-safe:hover:scale-105 motion-safe:active:scale-95",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          treatment === "idle" && state === "idle" && "animate-orb-breathe shadow-orb",
          disabled && "cursor-not-allowed opacity-60"
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
