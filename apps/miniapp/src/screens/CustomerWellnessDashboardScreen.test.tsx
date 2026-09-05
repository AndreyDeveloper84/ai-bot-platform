/**
 * Gate tests for `CustomerWellnessDashboardScreen`.
 *
 * The reads are wired to the backend now, so the dashboard no longer
 * invents anyone's day. The gate itself is unchanged and still under
 * test: prod renders the honest `PilotComingSoonScreen` until the
 * owner lifts it.
 *
 * The reads are mocked here rather than left to hit the network — this
 * file is about the gate and the layout, and a screen test that also
 * exercised HTTP would fail for reasons that have nothing to do with
 * either.
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

// DRF-1493: экран объявляет свой вид через `useScreenBack`, а тот
// заводит аппаратную кнопку MAX — мок должен отдавать и её ручки,
// иначе тест падает на отсутствующем экспорте, а не на поведении.
vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "test-init-data",
  setBackButton: () => undefined,
  onBackButton: () => () => undefined,
}));

import { getCatalogBrowse } from "../lib/customer-booking";

const mockedBrowse = vi.mocked(getCatalogBrowse);


/**
 * The reads are wired to the backend now, so a dev build without
 * `?stub=` goes to the network. These tests are about the GATE and the
 * LAYOUT, not about the data source — so they ask for the stub
 * explicitly, which is exactly what `?stub=` exists for.
 *
 * That the wired reads call the right endpoints is proven separately,
 * in `customer-wellness.test.ts`.
 */
function useDevStubData() {
  window.history.replaceState({}, "", "/customer/main?stub=default");
}

/** Fetch must never be reached in these tests — if it is, the stub
 *  selection above silently stopped working and the test would go
 *  green against real network shape instead of the layout. */
function forbidNetwork() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("network reached: ?stub= selection is broken");
    }),
  );
}

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
    useDevStubData();
    forbidNetwork();
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
    is_bookable: true,
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
