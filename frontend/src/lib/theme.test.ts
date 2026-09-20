import { afterEach, describe, expect, it, vi } from "vitest";
import { isDarkTheme, readStoredTheme, THEME_STORAGE_KEY, type Theme } from "./theme";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("isDarkTheme truth table (shared with the pre-paint script)", () => {
  const cases: Array<[Theme, boolean, boolean]> = [
    ["dark", false, true],
    ["dark", true, true],
    ["light", false, false],
    ["light", true, false],
    ["system", false, false],
    ["system", true, true],
  ];

  it.each(cases)("%s with system dark=%s resolves to dark=%s", (theme, prefersDark, expected) => {
    expect(isDarkTheme(theme, prefersDark)).toBe(expected);
  });
});

describe("readStoredTheme", () => {
  it("returns persisted explicit light and dark choices", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    expect(readStoredTheme()).toBe("light");

    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    expect(readStoredTheme()).toBe("dark");
  });

  it("falls back to system for missing or invalid values", () => {
    expect(readStoredTheme()).toBe("system");

    localStorage.setItem(THEME_STORAGE_KEY, "sepia");
    expect(readStoredTheme()).toBe("system");
  });

  it("falls back to system when storage access fails", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });

    expect(readStoredTheme()).toBe("system");
  });
});
