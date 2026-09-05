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


/**
 * DRF-1476 — the dashboard must not contradict the goal screen.
 *
 * Owner walkthrough 2026-09-05: a goal was chosen and active, and this
 * dashboard offered «Выбери цель». `active_goals` was hardcoded `[]`.
 *
 * These tests drive the REAL read path (no `?stub=`), stubbing `fetch`,
 * so they cover the lib→screen wiring and not just the renderer. Every
 * «the CTA is gone» assertion is paired with a «the CTA is there» case
 * on the same code path — a fix that hid the CTA from everyone would
 * pass the first and fail the second.
 */
describe("CustomerWellnessDashboardScreen — goal truthfulness (DRF-1476)", () => {
  const BASE_TODAY = {
    calories_eaten: 777,
    calories_target: 1900,
    water_glasses_eaten: 3,
    water_glasses_target: 8,
    display_name: "Анна",
  };

  /** Route by URL so all three reads resolve; unknown URLs fail loudly. */
  function serve(today: unknown, activity: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        const body = u.includes("/wellness/today")
          ? today
          : u.includes("/recent-activity")
            ? activity
            : null;
        if (body === null) throw new Error(`unexpected fetch: ${u}`);
        return { ok: true, status: 200, json: async () => body } as unknown as Response;
      }),
    );
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [] });
    // No `?stub=` — go through the wired read.
    window.history.replaceState({}, "", "/customer/main");
  });

  it("goal chosen: it is named on the dashboard and «Выбери цель» is gone", async () => {
    serve(
      {
        ...BASE_TODAY,
        active_goals: [{ title: "Позаботиться о коже лица", week_num: 2 }],
      },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    // POSITIVE: the person's actual goal is on screen, by name.
    expect(
      await screen.findByText(/Позаботиться о коже лица/),
    ).toBeInTheDocument();
    expect(screen.getByText("Моя цель")).toBeInTheDocument();
    // NEGATIVE (paired, same render): the bug is gone.
    expect(screen.queryByText("Выбери цель")).not.toBeInTheDocument();
  });

  it("no goal chosen: «Выбери цель» still shows, exactly as before", async () => {
    // The guard on the fix. Without it, «the CTA disappeared» would be
    // indistinguishable from a change that hides it from everyone.
    serve({ ...BASE_TODAY, active_goals: [] }, { this_week_booking_count: 0 });
    await renderScreen(false);

    expect(await screen.findByText("Выбери цель")).toBeInTheDocument();
    expect(screen.getByText(/Цель не выбрана/)).toBeInTheDocument();
    expect(screen.queryByText("Моя цель")).not.toBeInTheDocument();
  });

  it("goal layer unreachable: neither claim is made", async () => {
    // `active_goals` absent — the backend could not ask. Telling this
    // person to choose a goal is the original defect, restored by an
    // outage; telling her she has one would be the mirror lie.
    serve({ ...BASE_TODAY }, { this_week_booking_count: 0 });
    await renderScreen(false);

    // POSITIVE: the dashboard rendered and the goal row is honest.
    expect(await screen.findByText(/Не удалось загрузить/)).toBeInTheDocument();
    // Two neutral «Цель» labels — the pulse row head and the quick
    // action — neither of which asserts anything about having a goal.
    expect(screen.getAllByText("Цель")).toHaveLength(2);
    // NEGATIVE (paired): neither of the two claims appears.
    expect(screen.queryByText("Выбери цель")).not.toBeInTheDocument();
    expect(screen.queryByText("Моя цель")).not.toBeInTheDocument();
    expect(screen.queryByText(/Цель не выбрана/)).not.toBeInTheDocument();
  });

  it("goal without progress: the goal shows, no percentage is drawn", async () => {
    // Ayla stores no progress. A 0 % bar under a live goal is the same
    // class of lie, pointed the other way.
    serve(
      { ...BASE_TODAY, active_goals: [{ title: "Меньше стресса", week_num: 3 }] },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    // POSITIVE: goal and its real week are rendered.
    expect(await screen.findByText(/Меньше стресса/)).toBeInTheDocument();
    expect(screen.getByText(/3-я неделя/)).toBeInTheDocument();
    // NEGATIVE (paired): no invented percentage next to it.
    expect(screen.queryByText(/0 %/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("progressbar", { name: /Меньше стресса/ }),
    ).not.toBeInTheDocument();
  });

  it("goal with progress: the bar renders when a number really arrives", async () => {
    // Paired positive for the case above — proves the bar was hidden
    // for want of data, not deleted outright.
    serve(
      {
        ...BASE_TODAY,
        active_goals: [{ title: "Меньше стресса", week_num: 3, progress_pct: 78 }],
      },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    expect(await screen.findByText(/Меньше стресса/)).toBeInTheDocument();
    expect(screen.getByText(/78 %/)).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", { name: /Меньше стресса/ }),
    ).toBeInTheDocument();
  });
});

describe("CustomerWellnessDashboardScreen — weekly rollup (DRF-1476)", () => {
  const TODAY = {
    calories_eaten: 777,
    calories_target: 1900,
    water_glasses_eaten: 3,
    water_glasses_target: 8,
    active_goals: [],
    display_name: "Анна",
  };

  function serve(activity: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        const body = u.includes("/wellness/today")
          ? TODAY
          : u.includes("/recent-activity")
            ? activity
            : null;
        if (body === null) throw new Error(`unexpected fetch: ${u}`);
        return { ok: true, status: 200, json: async () => body } as unknown as Response;
      }),
    );
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [] });
    window.history.replaceState({}, "", "/customer/main");
  });

  it("rollup absent: Block 6 stays hidden, and invents no «0 из 7 дней»", async () => {
    serve({ this_week_booking_count: 0 });
    await renderScreen(false);

    // POSITIVE: the dashboard did render — so the absence below is the
    // gate working, not a blank screen.
    expect(await screen.findByText("Выбери цель")).toBeInTheDocument();
    // NEGATIVE (paired): no fabricated week.
    expect(screen.queryByText(/Прогресс недели/)).not.toBeInTheDocument();
    expect(screen.queryByText(/из 7 дней/)).not.toBeInTheDocument();
  });

  it("rollup present and past the cold-start gate: Block 6 renders it", async () => {
    // The guard: proves Block 6 is hidden above for want of data, and
    // has not simply been removed.
    serve({
      this_week_booking_count: 0,
      weekly_progress: {
        water_days_logged: 4,
        food_days_logged: 5,
        active_days_count: 5,
      },
    });
    await renderScreen(false);

    expect(await screen.findByText(/Прогресс недели/)).toBeInTheDocument();
    expect(screen.getByText(/4 из 7 дней/)).toBeInTheDocument();
    expect(screen.getByText(/5 из 7 дней/)).toBeInTheDocument();
  });

  it("rollup present but below the cold-start gate: still hidden (§11.4)", async () => {
    serve({
      this_week_booking_count: 0,
      weekly_progress: {
        water_days_logged: 2,
        food_days_logged: 1,
        active_days_count: 2,
      },
    });
    await renderScreen(false);

    expect(await screen.findByText("Выбери цель")).toBeInTheDocument();
    expect(screen.queryByText(/Прогресс недели/)).not.toBeInTheDocument();
  });
});
