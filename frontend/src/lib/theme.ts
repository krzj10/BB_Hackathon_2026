/*
 * Shared theme-resolution primitives.
 *
 * The inline pre-paint script in index.html mirrors this exact truth table
 * (it runs before the bundle and cannot import modules) — keep both in sync.
 */

export type Theme = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "eva-theme";

export function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Missing and invalid values fall back to "system", matching the pre-paint script. */
export function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

/**
 * dark => dark, light => light, system/missing/invalid => follow the OS
 * preference. Single source of truth for both the provider and pre-paint.
 */
export function isDarkTheme(
  theme: Theme,
  prefersDark: boolean = systemPrefersDark()
): boolean {
  if (theme === "dark") return true;
  if (theme === "light") return false;
  return prefersDark;
}
