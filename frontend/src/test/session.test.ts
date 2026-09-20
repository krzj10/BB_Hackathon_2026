import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getEvaSessionId,
  newRequestId,
  resetEvaSessionIdForTests,
} from "../lib/session";

beforeEach(() => {
  resetEvaSessionIdForTests();
});

afterEach(() => {
  window.sessionStorage.clear();
  resetEvaSessionIdForTests();
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
    resetEvaSessionIdForTests();
    window.sessionStorage.clear();
    const second = getEvaSessionId();
    expect(second).toBeTruthy();
    expect(second).not.toBe(first);
  });

  it("keeps one stable id across calls when sessionStorage throws on get and set", () => {
    const original = window.sessionStorage;
    const throwingStorage = {
      getItem: vi.fn(() => {
        throw new DOMException("storage blocked", "SecurityError");
      }),
      setItem: vi.fn(() => {
        throw new DOMException("storage blocked", "SecurityError");
      }),
    };
    Object.defineProperty(window, "sessionStorage", {
      configurable: true,
      value: throwingStorage,
    });
    try {
      const first = getEvaSessionId();
      const second = getEvaSessionId();
      const third = getEvaSessionId();
      expect(first).toBeTruthy();
      expect(second).toBe(first);
      expect(third).toBe(first);
    } finally {
      Object.defineProperty(window, "sessionStorage", {
        configurable: true,
        value: original,
      });
    }
  });

  it("reuses the in-memory fallback when the persisted id disappears mid-session", () => {
    const first = getEvaSessionId();
    window.sessionStorage.clear(); // storage emptied but still working
    const second = getEvaSessionId();
    expect(second).toBe(first); // memory fallback survives the empty store
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
