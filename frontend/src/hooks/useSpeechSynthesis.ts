import * as React from "react";

/**
 * Reusable browser SpeechSynthesis infrastructure (B02B foundation for B03).
 *
 * Boundaries honored here:
 * - This is real browser TTS ONLY — no server TTS is faked, no text is sent
 *   to any external provider, and no assistant reply is synthesized (B03
 *   wires actual reply playback when /api/assistant/message exists).
 * - Interruption and stale-callback protection: a cancelled/superseded
 *   utterance's onend/onerror never move the controller to a wrong state.
 * - Language mapping prefers an installed voice via BCP-47-ish prefixes for
 *   Polish and English; no specific installed voice name is ever required.
 */

export interface SpeechSynthesisController {
  /** True while an utterance owned by the current generation is speaking. */
  speaking: boolean;
  /** True when the browser exposes window.speechSynthesis. */
  supported: boolean;
  /** Speak text; returns false when synthesis is unsupported. */
  speak: (text: string, language: string) => boolean;
  /** Cancel current speech (canonical interruption). */
  cancel: () => void;
}

function languagePrefixes(language: string): string[] {
  const lower = language.toLowerCase();
  const base = lower.split("-")[0];
  const prefixes = [lower];
  if (base === "pl") {
    prefixes.push("pl-pl", "pl");
  } else if (base === "en") {
    prefixes.push("en-us", "en-gb", "en");
  } else if (lower !== base) {
    prefixes.push(base);
  }
  return prefixes;
}

/** Prefer an installed voice matching the language prefix; no name required. */
export function pickVoice(
  voices: ReadonlyArray<{ lang: string }>,
  language: string
): { lang: string } | null {
  if (voices.length === 0) return null;
  for (const prefix of languagePrefixes(language)) {
    const exact = voices.find((voice) => voice.lang.toLowerCase() === prefix);
    if (exact) return exact;
    const loose = voices.find((voice) =>
      voice.lang.toLowerCase().startsWith(prefix)
    );
    if (loose) return loose;
  }
  return null;
}

function defaultVoiceTag(language: string): string {
  const base = language.toLowerCase().split("-")[0];
  if (base === "pl") return "pl-PL";
  if (base === "en") return "en-US";
  return language;
}

export function useSpeechSynthesis(): SpeechSynthesisController {
  const generationRef = React.useRef(0);
  const [speaking, setSpeaking] = React.useState(false);

  const supported =
    typeof window !== "undefined" &&
    "speechSynthesis" in window &&
    "SpeechSynthesisUtterance" in window;

  const cancel = React.useCallback(() => {
    generationRef.current += 1;
    setSpeaking(false);
    if (supported) {
      try {
        window.speechSynthesis.cancel();
      } catch {
        // cancel must never throw into UI code
      }
    }
  }, [supported]);

  const speak = React.useCallback(
    (text: string, language: string): boolean => {
      if (!supported || !text.trim()) return false;
      // Replacing current speech is a canonical interruption of the old
      // generation: its late onend/onerror must not touch state.
      generationRef.current += 1;
      const generation = generationRef.current;
      try {
        window.speechSynthesis.cancel();
      } catch {
        // ignore; speak still proceeds
      }

      const utterance = new SpeechSynthesisUtterance(text);
      const voice = pickVoice(window.speechSynthesis.getVoices(), language);
      if (voice) {
        utterance.voice = voice as SpeechSynthesisVoice;
      }
      utterance.lang = voice?.lang ?? defaultVoiceTag(language);
      utterance.onend = () => {
        // Late callback from a superseded utterance is ignored.
        if (generationRef.current === generation) setSpeaking(false);
      };
      utterance.onerror = () => {
        if (generationRef.current === generation) setSpeaking(false);
      };
      setSpeaking(true);
      window.speechSynthesis.speak(utterance);
      return true;
    },
    [supported]
  );

  // Unmount cleanup: never leave the mic-less speech pipeline running.
  React.useEffect(() => {
    return () => {
      generationRef.current += 1;
      if (supported) {
        try {
          window.speechSynthesis.cancel();
        } catch {
          // unmount cleanup is best-effort
        }
      }
    };
  }, [supported]);

  return { speaking, supported, speak, cancel };
}
