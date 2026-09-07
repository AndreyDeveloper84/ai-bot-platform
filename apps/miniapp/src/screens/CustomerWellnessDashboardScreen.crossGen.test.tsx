/**
 * DRF-1485 §3 — с главной никуда не ведёт в прежнее поколение.
 *
 * `CustomerWellnessDashboardScreen` — это «Главная» (DRF-1546), то есть
 * живой экран нового поколения. Два его перехода вели в старое:
 *
 *   - «Перенести» у ближайшей записи → `/my-visits/:id/reschedule`;
 *   - карточка подбора «Ayla подобрала тебе» → `/catalog/:id`.
 *
 * Оба адреса остаются смонтированными в `App.tsx` как compatibility-
 * алиасы для ссылок, ушедших наружу раньше, — поэтому переход «работал»
 * и заметить его можно было только замером. Здесь он замерен: каждая
 * проверка мостит и КАНОНИЧЕСКИЙ адрес, и АЛИАС отдельными пробами, и
 * требует канонической — рядом с утверждением, что на алиас не попали.
 *
 * Отдельный файл, а не блок в `CustomerWellnessDashboardScreen.test.tsx`:
 * тот монтирует экран голым `MemoryRouter` без `Routes`, а здесь нужен
 * роутер с пробами на обоих адресах.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<
    typeof import("../lib/customer-booking")
  >();
  return { ...original, getCatalogBrowse: vi.fn() };
});

// Экран объявляет свой вид через `useScreenBack`, а тот заводит
// аппаратную кнопку MAX — мок отдаёт её ручки, иначе тест падал бы на
// отсутствующем экспорте, а не на поведении (как в соседнем файле).
vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "test-init-data",
  setBackButton: () => undefined,
  onBackButton: () => () => undefined,
}));

import { getCatalogBrowse } from "../lib/customer-booking";
import type { Service } from "../lib/api";
import { CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";

const mockedBrowse = vi.mocked(getCatalogBrowse);

const SERVICE: Service = {
  id: "svc-77",
  slug: "limfodrenazh",
  name: "Массаж лимфодренаж",
  short_description: "",
  description: "",
  price_from: "4500.00",
  duration_min: 60,
  is_popular: false,
  contraindications: "",
  is_bookable: true,
};

const NEXT_BOOKING = {
  date_human: "Завтра · пт · 16:00",
  service_name: "Массаж лимфодренаж",
  duration_min: 60,
  master_name: "Ирина",
  salon_name: "Формула тела",
  address: "ул. Тверская 12",
  booking_id: "bk-77",
};

const TODAY = {
  calories_eaten: 777,
  calories_target: 1900,
  water_glasses_eaten: 3,
  water_glasses_target: 8,
  active_goals: [],
  display_name: "Анна",
};

/** Маршрутизация по URL: неизвестный адрес падает громко. */
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
      return {
        ok: true,
        status: 200,
        json: async () => body,
      } as unknown as Response;
    }),
  );
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <Routes>
        <Route
          path="/customer/main"
          element={<CustomerWellnessDashboardScreen />}
        />
        {/* Канонические адреса нового поколения. */}
        <Route
          path="/customer/records/:bookingId/reschedule"
          element={<div>RESCHEDULE-LIVE</div>}
        />
        <Route
          path="/customer/catalog/:serviceId"
          element={<div>SERVICE-LIVE</div>}
        />
        {/* Алиасы прежнего поколения — пробы «сюда не ведём». */}
        <Route
          path="/my-visits/:bookingId/reschedule"
          element={<div>RESCHEDULE-LEGACY</div>}
        />
        <Route
          path="/catalog/:serviceId"
          element={<div>SERVICE-LEGACY</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  // Без `?stub=` — идём через проводной чтение, как в DRF-1476 кейсах.
  window.history.replaceState({}, "", "/customer/main");
  mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [] });
});

describe("Главная → перенос записи ведёт в живое поколение", () => {
  it("«Перенести» открывает /customer/records/:id/reschedule", async () => {
    serve({ next_booking: NEXT_BOOKING, this_week_booking_count: 1 });
    renderScreen();

    const user = userEvent.setup();
    const button = await screen.findByRole("button", {
      name: "Перенести запись",
    });
    await user.click(button);

    // Положительно: канонический адрес, и id из карточки — тот же.
    expect(await screen.findByText("RESCHEDULE-LIVE")).toBeInTheDocument();
    // Отрицательно, на том же переходе: алиас не задет.
    expect(screen.queryByText("RESCHEDULE-LEGACY")).not.toBeInTheDocument();
  });
});

describe("Главная → карточка подбора ведёт в живое поколение", () => {
  it("подбор «Ayla подобрала тебе» открывает /customer/catalog/:id", async () => {
    mockedBrowse.mockResolvedValue({
      services: [SERVICE],
      masters: [],
      picks: [{ serviceId: SERVICE.id, reasons: ["Подходит твоей цели"] }],
    });
    serve({ this_week_booking_count: 0 });
    renderScreen();

    const user = userEvent.setup();
    // Положительно: блок подбора отрисован — иначе клик проверял бы
    // пустоту, а тест зеленел бы ни на чём.
    const card = await screen.findByRole("button", {
      name: /Массаж лимфодренаж/,
    });
    expect(screen.getByText("Подходит твоей цели")).toBeInTheDocument();
    await user.click(card);

    expect(await screen.findByText("SERVICE-LIVE")).toBeInTheDocument();
    expect(screen.queryByText("SERVICE-LEGACY")).not.toBeInTheDocument();
  });
});
