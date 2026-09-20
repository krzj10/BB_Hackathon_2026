import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VoiceOrb } from "../components/voice/VoiceOrb";
import type { VoiceState } from "../api/types.generated";

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

const CANONICAL_ORDER: VoiceState[] = [
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

describe("VoiceOrb", () => {
  it("renders as a keyboard-focusable native button with an idle status label", () => {
    render(<VoiceOrb />);

    const orb = screen.getByRole("button", { name: /eva voice orb/i });
    expect(orb.tagName).toBe("BUTTON");
    expect(orb).toHaveAttribute("type", "button");
    expect(screen.getByRole("status")).toHaveTextContent("EVA is idle");
  });

  it("cycles deterministically through all nine canonical states on activation", () => {
    render(<VoiceOrb />);
    const orb = screen.getByRole("button", { name: /eva voice orb/i });

    for (const state of CANONICAL_ORDER.slice(1)) {
      fireEvent.click(orb);
      expect(screen.getByRole("status")).toHaveTextContent(STATE_LABELS[state]);
    }

    fireEvent.click(orb);
    expect(screen.getByRole("status")).toHaveTextContent("EVA is idle");
  });

  it.each(
    Object.entries(STATE_LABELS) as Array<[VoiceState, string]>
  )("renders truthful status for canonical state %s", (state, label) => {
    render(<VoiceOrb state={state} />);
    expect(screen.getByRole("status")).toHaveTextContent(label);
    expect(
      screen.getByRole("button", { name: new RegExp(`EVA voice orb — ${label}`) })
    ).toBeInTheDocument();
  });

  it("does not cycle while controlled by a canonical state", () => {
    const { rerender } = render(<VoiceOrb state="listening" />);
    const button = screen.getByRole("button", { name: /EVA voice orb — Listening/ });

    fireEvent.click(button);
    expect(screen.getByRole("status")).toHaveTextContent("Listening");

    rerender(<VoiceOrb state="speaking" />);
    expect(screen.getByRole("status")).toHaveTextContent("Speaking");
  });
});
