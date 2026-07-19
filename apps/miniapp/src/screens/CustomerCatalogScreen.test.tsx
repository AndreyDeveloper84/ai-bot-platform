/**
 * Gate tests: `CustomerCatalogScreen` serves hardcoded fake salons via
 * the `getCatalogRecommendations()` stub — unacceptable in prod for the
 * pilot (orchestrator, commit 4). Prod builds render the honest
 * `PilotComingSoonScreen`; real customer endpoints replace the stub lib
 * as the FIRST item of pilot phase 3. DEV builds keep the stub.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

async function renderScreen(prod: boolean) {
  vi.resetModules();
  if (prod) vi.stubEnv("DEV", false);
  try {
    const { CustomerCatalogScreen } = await import("./CustomerCatalogScreen");
    render(
      <MemoryRouter initialEntries={["/customer/catalog"]}>
        <CustomerCatalogScreen />
      </MemoryRouter>,
    );
  } finally {
    if (prod) vi.unstubAllEnvs();
  }
}

describe("CustomerCatalogScreen gating", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("DEV build: renders the stub catalog as before", async () => {
    await renderScreen(false);
    expect(await screen.findByText("Beauty Place")).toBeInTheDocument();
    expect(screen.queryByText(/выдуманных/)).not.toBeInTheDocument();
  });

  it("prod build: renders the honest placeholder, never fake salons", async () => {
    await renderScreen(true);
    expect(
      await screen.findByRole("heading", { name: "Услуги" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/выдуманных/)).toBeInTheDocument();
    expect(screen.queryByText("Beauty Place")).not.toBeInTheDocument();
    expect(screen.queryByText("Формула тела")).not.toBeInTheDocument();
  });
});
