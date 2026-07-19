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
      pickServiceIds: [],
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

  it("DEV build, Block 7: renders real scorer picks (no reasoning_text)", async () => {
    mockedBrowse.mockResolvedValue({
      services: [
        {
          id: "svc-2",
          slug: "pedikyur",
          name: "Педикюр",
          short_description: "",
          description: "",
          price_from: "2200.00",
          duration_min: 90,
          is_popular: false,
          contraindications: "",
        },
      ],
      masters: [],
      pickServiceIds: ["svc-2"],
    });
    await renderScreen(false);
    expect(
      await screen.findByRole("heading", { name: /Ayla подобрала тебе/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Педикюр")).toBeInTheDocument();
    expect(screen.getByText(/2 200 ₽/)).toBeInTheDocument();
  });
});
