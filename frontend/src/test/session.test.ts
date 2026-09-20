import { afterEach, describe, expect, it, vi } from "vitest";
import { getEvaSessionId, newRequestId } from "../lib/session";

afterEach(() => {
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("EVA session identity", () => {
  it("returns a stable non-empty identifier for the tab/session", () => {
    const first = getEvaSessionId();
    const second = getEvaSessionId();
    expect(first).toBeTruthy();
    expect(second).toBe(first);
  });

  it("keeps the same id across calls via sessionStorage", () => {
    const id = getEvaSessionId();
    expect(window.sessionStorage.getItem("eva-session-id")).toBe(id);
  });

  it("a fresh session context produces a different id", () => {
    const first = getEvaSessionId();
    window.sessionStorage.clear();
    const second = getEvaSessionId();
    expect(second).toBeTruthy();
    expect(second).not.toBe(first);
  });

  it("never touches localStorage and never builds a secret", () => {
    const store = new Map<string, string>();
    const localStorageSpy = {
      getItem: vi.fn((key: string) => store.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => void store.set(key, value)),
    };
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: localStorageSpy,
    });
    getEvaSessionId();
    expect(localStorageSpy.getItem).not.toHaveBeenCalled();
    expect(localStorageSpy.setItem).not.toHaveBeenCalled();
    expect(store.size).toBe(0);
  });

  it("produces unique non-empty request ids", () => {
    const a = newRequestId();
    const b = newRequestId();
    expect(a).toBeTruthy();
    expect(b).toBeTruthy();
    expect(a).not.toBe(b);
  });
});
