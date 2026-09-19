import { useState } from "react";
import { cn } from "../../lib/utils";

/*
 * Visual states only. These are presentation chrome for the B02A
 * foundation; the canonical VoiceState contract (A00) will drive
 * these states once the contract merge lands (B02B/B03 wiring).
 */
export type OrbVisualState = "idle" | "listening" | "thinking" | "speaking";

const STATE_ORDER: OrbVisualState[] = ["idle", "listening", "thinking", "speaking"];

const STATE_LABELS: Record<OrbVisualState, string> = {
  idle: "EVA is idle",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking…",
};

/*
 * Global EVA Voice Orb — the primary interaction affordance.
 * Fixed, always present, keyboard accessible. In this visual
 * foundation, activating it cycles the preview states so the
 * motion design can be reviewed in both themes.
 */
export function VoiceOrb({ className }: { className?: string }) {
  const [state, setState] = useState<OrbVisualState>("idle");

  const cycle = () =>
    setState((current) => STATE_ORDER[(STATE_ORDER.indexOf(current) + 1) % STATE_ORDER.length]);

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
        aria-label={`EVA voice orb — ${STATE_LABELS[state]} Activate to cycle preview states.`}
        className={cn(
          "relative grid size-16 place-items-center rounded-full bg-orb ring-1 ring-inset ring-white/15",
          "transition-transform duration-200 ease-smooth motion-safe:hover:scale-105 motion-safe:active:scale-95",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          state === "idle" && "animate-orb-breathe shadow-orb"
        )}
      >
        {/* Spherical highlight */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-[6px] rounded-full bg-[radial-gradient(circle_at_30%_25%,oklch(1_0_0/0.18),transparent_55%)]"
        />

        {/* Core dot */}
        <span
          aria-hidden
          className={cn(
            "size-2.5 rounded-full transition-colors duration-300",
            state === "idle" ? "bg-white/70" : "bg-orb-ring"
          )}
        />

        {state === "listening" && (
          <span
            aria-hidden
            className="absolute inset-0 animate-orb-ring rounded-full border border-orb-ring"
          />
        )}

        {state === "thinking" && (
          <span
            aria-hidden
            className="absolute -inset-[3px] animate-orb-spin rounded-full border border-transparent border-r-orb-ring/40 border-t-orb-ring"
          />
        )}

        {state === "speaking" && (
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
      </button>
    </div>
  );
}
