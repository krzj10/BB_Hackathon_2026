import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSpeechSynthesis, pickVoice } from "../hooks/useSpeechSynthesis";

class FakeUtterance {
  text: string;
  lang = "";
  voice: { lang: string } | null = null;
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(text: string) {
    this.text = text;
  }
}

function fakeSynthesis() {
  const speak = vi.fn();
  const cancel = vi.fn();
  const voices: Array<{ lang: string; name: string }> = [
    { lang: "pl-PL", name: "whatever-installed-voice" },
    { lang: "en-US", name: "another-voice" },
  ];
  const synthesis = { speak, cancel, getVoices: () => voices };
  Object.defineProperty(window, "speechSynthesis", {
    configurable: true,
    value: synthesis,
  });
  Object.defineProperty(window, "SpeechSynthesisUtterance", {
    configurable: true,
    value: FakeUtterance,
  });
  return { speak, cancel, synthesis };
}

afterEach(() => {
  // @ts-expect-error test cleanup of injected jsdom properties
  delete window.speechSynthesis;
  // @ts-expect-error test cleanup of injected jsdom properties
  delete window.SpeechSynthesisUtterance;
});

let controller: ReturnType<typeof useSpeechSynthesis> | undefined;

function TtsHarness({ language }: { language?: string }) {
  controller = useSpeechSynthesis();
  return (
    <div>
      <p data-testid="speaking">{controller.speaking ? "yes" : "no"}</p>
      <p data-testid="supported">{controller.supported ? "yes" : "no"}</p>
      <button
        type="button"
        data-testid="speak"
        onClick={() => controller?.speak("Dzień dobry", language ?? "pl")}
      />
      <button type="button" data-testid="cancel" onClick={() => controller?.cancel()} />
    </div>
  );
}

describe("useSpeechSynthesis (browser TTS foundation)", () => {
  it("speak sets the canonical speaking state and prefers a matching installed voice", () => {
    const { speak, cancel } = fakeSynthesis();
    render(<TtsHarness language="pl" />);

    expect(screen.getByTestId("supported")).toHaveTextContent("yes");
    expect(screen.getByTestId("speaking")).toHaveTextContent("no");

    act(() => {
      screen.getByTestId("speak").click();
    });

    expect(screen.getByTestId("speaking")).toHaveTextContent("yes");
    expect(speak).toHaveBeenCalledTimes(1);
    expect(cancel).toHaveBeenCalledTimes(1); // replacing pipeline state cleanly
    const utterance = speak.mock.calls[0][0] as FakeUtterance;
    expect(utterance.text).toBe("Dzień dobry");
    // BCP-47 prefix matching — never a hardcoded installed voice name.
    expect(utterance.voice?.lang).toBe("pl-PL");
    expect(utterance.lang).toBe("pl-PL");
  });

  it("cancel interrupts speech and clears the speaking state", () => {
    const { cancel } = fakeSynthesis();
    render(<TtsHarness />);
    act(() => {
      screen.getByTestId("speak").click();
    });
    expect(screen.getByTestId("speaking")).toHaveTextContent("yes");

    act(() => {
      screen.getByTestId("cancel").click();
    });
    expect(screen.getByTestId("speaking")).toHaveTextContent("no");
    expect(cancel).toHaveBeenCalled();
  });

  it("a late onend from a superseded utterance never moves state to the wrong generation", () => {
    const { speak } = fakeSynthesis();
    render(<TtsHarness language="en" />);
    act(() => {
      screen.getByTestId("speak").click();
    });
    const utteranceA = speak.mock.calls[0][0] as FakeUtterance;

    // Replace A with B — canonical interruption of A.
    act(() => {
      screen.getByTestId("speak").click();
    });
    const utteranceB = speak.mock.calls[1][0] as FakeUtterance;

    act(() => {
      utteranceA.onend?.();
    });
    // Still belongs to generation B: state must NOT drop to idle.
    expect(screen.getByTestId("speaking")).toHaveTextContent("yes");

    act(() => {
      utteranceB.onend?.();
    });
    expect(screen.getByTestId("speaking")).toHaveTextContent("no");
  });

  it("a late onerror from a cancelled utterance does not corrupt state either", () => {
    const { speak } = fakeSynthesis();
    render(<TtsHarness />);
    act(() => {
      screen.getByTestId("speak").click();
    });
    const utteranceA = speak.mock.calls[0][0] as FakeUtterance;
    act(() => {
      screen.getByTestId("speak").click();
    });
    act(() => {
      utteranceA.onerror?.();
    });
    expect(screen.getByTestId("speaking")).toHaveTextContent("yes");
  });

  it("unmount cancels speech", () => {
    const { cancel } = fakeSynthesis();
    const { unmount } = render(<TtsHarness />);
    act(() => {
      screen.getByTestId("speak").click();
    });
    unmount();
    expect(cancel).toHaveBeenCalled();
  });

  it("maps Polish and English via BCP-47 prefixes without requiring a voice name", () => {
    expect(pickVoice([{ lang: "pl-PL" }, { lang: "en-US" }], "pl")).toMatchObject({ lang: "pl-PL" });
    expect(pickVoice([{ lang: "en-US" }, { lang: "pl-PL" }], "en")).toMatchObject({ lang: "en-US" });
    expect(pickVoice([{ lang: "en-GB" }, { lang: "de-DE" }], "en-US")).toMatchObject({ lang: "en-GB" });
    expect(pickVoice([], "pl")).toBeNull();
    expect(pickVoice([{ lang: "de-DE" }], "pl")).toBeNull();
  });

  it("reports unsupported browsers truthfully and speak is a no-op", () => {
    render(<TtsHarness />);
    expect(screen.getByTestId("supported")).toHaveTextContent("no");
    act(() => {
      screen.getByTestId("speak").click();
    });
    expect(screen.getByTestId("speaking")).toHaveTextContent("no");
  });
});
