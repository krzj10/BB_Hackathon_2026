import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VoiceOrb } from "../components/voice/VoiceOrb";

describe("VoiceOrb", () => {
  it("renders as a keyboard-focusable button with an idle state label", () => {
    render(<VoiceOrb />);

    const orb = screen.getByRole("button", { name: /eva voice orb/i });
    expect(orb).toBeInTheDocument();
    expect(orb.getAttribute("aria-label")).toContain("idle");
    expect(screen.getByRole("status")).toHaveTextContent("EVA is idle");
  });

  it("cycles through the preview states on activation", () => {
    render(<VoiceOrb />);
    const orb = screen.getByRole("button", { name: /eva voice orb/i });

    fireEvent.click(orb);
    expect(orb.getAttribute("aria-label")).toContain("Listening");
    expect(screen.getByRole("status")).toHaveTextContent("Listening");

    fireEvent.click(orb);
    expect(orb.getAttribute("aria-label")).toContain("Thinking");

    fireEvent.click(orb);
    expect(orb.getAttribute("aria-label")).toContain("Speaking");

    fireEvent.click(orb);
    expect(orb.getAttribute("aria-label")).toContain("idle");
  });
});
