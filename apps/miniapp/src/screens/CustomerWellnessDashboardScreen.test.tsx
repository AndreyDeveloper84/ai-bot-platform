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

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return {
    ...original,
    getCatalogBrowse: vi.fn(),
  };
});

import { getCatalogBrowse } from "../lib/customer-booking";

const mockedBrowse = vi.mocked(getCatalogBrowse);

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
    mockedBrowse.mockResolvedValue({
      services: [],
      masters: [],
      picks: [],
    });
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

  const PEDIKYUR = {
    id: "svc-2",
    slug: "pedikyur",
    name: "Педикюр",
    short_description: "",
    description: "",
    price_from: "2200.00",
    duration_min: 90,
    is_popular: false,
    contraindications: "",
  };

  it("DEV build, Block 7: renders scorer picks WITH the WHY the source sent", async () => {
    mockedBrowse.mockResolvedValue({
      services: [PEDIKYUR],
      masters: [],
      picks: [{ serviceId: "svc-2", reasons: ["Свободно раньше всех остальных"] }],
    });
    await renderScreen(false);
    expect(
      await screen.findByRole("heading", { name: /Ayla подобрала тебе/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Педикюр")).toBeInTheDocument();
    expect(screen.getByText(/2 200 ₽/)).toBeInTheDocument();
    expect(screen.getByText("Свободно раньше всех остальных")).toBeInTheDocument();
  });

  // Owner ruling 25.08 — same gate on the second branded surface.
  it("DEV build, Block 7: no WHY → no branded block, dashboard unaffected", async () => {
    mockedBrowse.mockResolvedValue({
      services: [PEDIKYUR],
      masters: [],
      picks: [],
    });
    await renderScreen(false);
    // Dashboard itself still renders.
    expect(await screen.findByText(/Вода:/)).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /Ayla подобрала тебе/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Педикюр")).not.toBeInTheDocument();
  });
});
