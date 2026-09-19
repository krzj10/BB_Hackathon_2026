import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../App";
import { ThemeProvider } from "../components/theme/ThemeProvider";

const NAV_LABELS = [
  "Today",
  "Briefings",
  "Attention",
  "Decisions",
  "Calendar",
  "Knowledge",
  "Settings",
];

function renderAppAt(path: string) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </ThemeProvider>
  );
}

describe("app shell navigation", () => {
  it("renders all seven primary destinations in the sidebar", () => {
    renderAppAt("/");

    const sidebar = screen.getByRole("complementary", { name: "Primary" });
    for (const label of NAV_LABELS) {
      expect(within(sidebar).getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("renders a mobile tab bar with the same destinations", () => {
    renderAppAt("/");

    const tabbar = screen.getByRole("navigation", { name: "Primary mobile" });
    for (const label of NAV_LABELS) {
      expect(within(tabbar).getAllByText(label).length).toBeGreaterThan(0);
    }
  });
});

describe("Today page visual structure", () => {
  it("renders the greeting, status strip, timeline, attention and decisions", async () => {
    renderAppAt("/");

    expect(await screen.findByText(/Good (morning|afternoon|evening)/)).toBeInTheDocument();
    expect(screen.getByText("Timeline")).toBeInTheDocument();
    expect(screen.getByText("ACME Contract Review")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Attention" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Decisions" })).toBeInTheDocument();
    expect(screen.getAllByText("High").length).toBeGreaterThan(0);
  });
});

describe("Briefings page", () => {
  it("renders the Briefings screen with meeting details", async () => {
    renderAppAt("/briefings");

    expect(await screen.findByRole("heading", { level: 1, name: "ACME Contract Review" })).toBeInTheDocument();
    expect(await screen.findByText("Language: PL")).toBeInTheDocument();
    expect(await screen.findByText("Spoken Summary")).toBeInTheDocument();
  });
});
