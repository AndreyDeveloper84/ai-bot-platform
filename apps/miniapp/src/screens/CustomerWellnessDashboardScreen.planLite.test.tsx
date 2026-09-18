/**
 * Вход в «Мой план» с дашборда (DRF-2101) — только под флагом сборки и
 * только когда цель есть: без цели плана не бывает, и кнопка не обещает
 * того, чего сервер не даст.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCatalogBrowse: vi.fn() };
});
vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "",
  setBackButton: vi.fn(),
  signalReady: vi.fn(),
  applyTheme: vi.fn(),
  hapticImpact: vi.fn(),
}));

import { getCatalogBrowse } from "../lib/customer-booking";
import { PLAN_LITE_COPY, PLAN_LITE_ROUTE } from "./PlanLiteScreen";

const mockedBrowse = vi.mocked(getCatalogBrowse);

function serveLiveHome(today: Record<string, unknown> = {}) {
  const body = {
    calories_eaten: 1240,
    calories_target: 2100,
    pfc: { protein_g: 65, fat_g: 40, carbs_g: 120 },
    water_glasses_eaten: 4,
    water_glasses_target: 8,
    active_goals: [{ title: "Подтянуть фигуру", week_num: 1 }],
    display_name: "Анна",
    ...today,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const u = String(url);
      const payload = u.includes("/wellness/today")
        ? body
        : u.includes("/recent-activity")
          ? { this_week_booking_count: 0 }
          : null;
      if (payload === null) throw new Error(`unexpected fetch: ${u}`);
      return { ok: true, status: 200, json: async () => payload } as unknown as Response;
    }),
  );
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

async function renderScreen() {
  vi.resetModules();
  vi.stubEnv("DEV", false);
  const { CustomerWellnessDashboardScreen } = await import("./CustomerWellnessDashboardScreen");
  render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <Routes>
        <Route path="/customer/main" element={<CustomerWellnessDashboardScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [], picksOutcome: "OK" } as never);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("дашборд → «Мой план»", () => {
  it("с флагом и целью — кнопка ведёт на экран плана", async () => {
    vi.stubEnv("VITE_PLAN_LITE", "1");
    serveLiveHome();
    await renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: PLAN_LITE_COPY.entryFromDashboard }));

    expect(screen.getByTestId("location")).toHaveTextContent(PLAN_LITE_ROUTE);
  });

  it("с флагом, но без цели — кнопки нет: плана без цели не бывает", async () => {
    vi.stubEnv("VITE_PLAN_LITE", "1");
    serveLiveHome({ active_goals: [] });
    await renderScreen();
    await screen.findByRole("button", { name: "Выбери цель" });

    expect(screen.queryByRole("button", { name: PLAN_LITE_COPY.entryFromDashboard })).toBeNull();
  });

  it("без флага кнопки нет даже с целью", async () => {
    vi.stubEnv("VITE_PLAN_LITE", "");
    serveLiveHome();
    await renderScreen();
    await screen.findByRole("button", { name: "Моя цель" });

    expect(screen.queryByRole("button", { name: PLAN_LITE_COPY.entryFromDashboard })).toBeNull();
  });
});
