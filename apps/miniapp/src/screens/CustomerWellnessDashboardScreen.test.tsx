/**
 * Gate tests: `CustomerWellnessDashboardScreen` is a stub surface (fake
 * wellness data in every build today). Pilot commit 4 (orchestrator):
 * hidden until S4/post-pilot — prod builds render the honest
 * `PilotComingSoonScreen` instead. DEV builds keep the stub for local
 * development.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

async function renderScreen(prod: boolean) {
  vi.resetModules();
  if (prod) vi.stubEnv("DEV", false);
  try {
    const { CustomerWellnessDashboardScreen } = await import(
      "./CustomerWellnessDashboardScreen"
    );
    render(
      <MemoryRouter initialEntries={["/customer/main"]}>
        <CustomerWellnessDashboardScreen />
      </MemoryRouter>,
    );
  } finally {
    if (prod) vi.unstubAllEnvs();
  }
}

describe("CustomerWellnessDashboardScreen gating", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("DEV build: renders the wellness stub surface as before", async () => {
    await renderScreen(false);
    expect(await screen.findByText(/Вода:/)).toBeInTheDocument();
    expect(screen.queryByText(/выдуманных данных/)).not.toBeInTheDocument();
  });

  it("prod build: renders the honest placeholder, never fake wellness data", async () => {
    await renderScreen(true);
    expect(
      await screen.findByRole("heading", { name: "Главная" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/выдуманных данных/)).toBeInTheDocument();
    expect(screen.queryByText(/Вода:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Питание:/)).not.toBeInTheDocument();
  });
});
