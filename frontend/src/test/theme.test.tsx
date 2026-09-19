import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider, useTheme } from "../components/theme/ThemeProvider";
import { ThemeToggle } from "../components/theme/ThemeToggle";

function ThemeProbe() {
  const { theme } = useTheme();
  return <span data-testid="theme-value">{theme}</span>;
}

function renderWithProvider(ui: React.ReactElement) {
  return render(<ThemeProvider>{ui}</ThemeProvider>);
}

function mockSystemPreference(prefersDark: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: query === "(prefers-color-scheme: dark)" ? prefersDark : false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});

afterEach(() => {
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
});

describe("theme", () => {
  it("defaults to system (resolved light with mocked matchMedia) and exposes the raw theme", () => {
    renderWithProvider(
      <>
        <ThemeProbe />
        <ThemeToggle />
      </>
    );
    expect(screen.getByTestId("theme-value")).toHaveTextContent("system");
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("resolves system theme to dark when the OS prefers dark (no flash regression)", () => {
    mockSystemPreference(true);

    renderWithProvider(<ThemeProbe />);

    expect(screen.getByTestId("theme-value")).toHaveTextContent("system");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("a persisted light choice overrides a dark system preference", () => {
    localStorage.setItem("eva-theme", "light");
    mockSystemPreference(true);

    renderWithProvider(<ThemeProbe />);

    expect(screen.getByTestId("theme-value")).toHaveTextContent("light");
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("a persisted dark choice overrides a light system preference", () => {
    localStorage.setItem("eva-theme", "dark");
    mockSystemPreference(false);

    renderWithProvider(<ThemeProbe />);

    expect(screen.getByTestId("theme-value")).toHaveTextContent("dark");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("toggles to dark, applies the .dark class and persists the choice", () => {
    renderWithProvider(<ThemeToggle />);
    const toggle = screen.getByRole("button", { name: "Switch to dark theme" });

    fireEvent.click(toggle);

    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem("eva-theme")).toBe("dark");
    expect(
      screen.getByRole("button", { name: "Switch to light theme" })
    ).toBeInTheDocument();
  });

  it("toggles back to light and removes the .dark class", () => {
    localStorage.setItem("eva-theme", "dark");
    document.documentElement.classList.add("dark");

    renderWithProvider(<ThemeToggle />);
    const toggle = screen.getByRole("button", { name: "Switch to light theme" });
    fireEvent.click(toggle);

    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem("eva-theme")).toBe("light");
  });
});
