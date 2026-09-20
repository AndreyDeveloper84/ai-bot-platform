/**
 * Двери в «Новую запись» мастера (DRF-2155, М-3, п.3 и п.5 листа).
 *
 * * «Сегодня» (М-1): на пустом дне — кнопка «Добавить запись» →
 *   `/master/booking/new` (на `/solo/my-day` — `/solo/booking/new`);
 * * «Расписание»: главный тап по свободному окну → форма с
 *   `?date&from&to` («Выбранное окно»), а «недоступно» — отдельное,
 *   вторичное действие в карточке окна, не главный тап (до М-3 главный тап
 *   открывал лист «Помечу как недоступно» — противоположно макету).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getDashboard: vi.fn(),
    getMasterMe: vi.fn(),
    getMasterConversations: vi.fn(),
    getMasterSchedule: vi.fn(),
    getPendingAvailability: vi.fn(),
  };
});

import {
  getDashboard,
  getMasterMe,
  getMasterSchedule,
  getPendingAvailability,
  type DashboardResponse,
  type MasterScheduleResponse,
} from "../lib/master-api";
import { MasterDashboardScreen } from "./MasterDashboardScreen";
import { MasterScheduleScreen } from "./MasterScheduleScreen";

const mockedDashboard = vi.mocked(getDashboard);
const mockedSchedule = vi.mocked(getMasterSchedule);
const mockedPending = vi.mocked(getPendingAvailability);
const mockedMe = vi.mocked(getMasterMe);

const NOW = "2026-09-20T11:15:00";
const TODAY = "2026-09-20";

function dashboard(): DashboardResponse {
  return {
    master: {
      id: "m-1",
      name: "Архипкин",
      specialization: "Массаж",
      photo_url: "",
    },
    salon: { id: "t-1", name: "Формула тела" },
    now_iso: NOW,
    active_visit: null,
    next_visit: null,
    upcoming_today: [],
    inbox_preview: [],
    today_summary: {
      total_clients_today: 0,
      completed_count: 0,
      next_free_window: null,
    },
    tab_badges: {
      conversations_unread: 0,
      schedule_has_pending_change: false,
      profile_has_owner_pending_change: false,
    },
    states: {
      is_day_done: false,
      is_offline_safe_response: false,
      day_off: false,
    },
    week_summary: {
      week_start: "2026-09-14",
      week_end: "2026-09-20",
      bookings: 0,
      completed: 0,
      rating: null,
    },
  } as DashboardResponse;
}

function schedule(): MasterScheduleResponse {
  return {
    tenant_tz: "Europe/Moscow",
    from: TODAY,
    to: TODAY,
    days: [
      {
        date: TODAY,
        is_off_day: false,
        working_hours: { start: "09:00", end: "18:00" },
        bookings: [],
        blocks: [],
        free_windows: [{ start: "14:00", end: "17:00", duration_min: 180 }],
        conflicts: [],
      },
    ],
  } as MasterScheduleResponse;
}

function WhereAmI() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/master/dashboard" element={<MasterDashboardScreen />} />
        <Route path="/solo/my-day" element={<MasterDashboardScreen />} />
        <Route path="/master/schedule" element={<MasterScheduleScreen />} />
        <Route path="/solo/schedule" element={<MasterScheduleScreen />} />
        <Route path="/master/booking/new" element={<WhereAmI />} />
        <Route path="/solo/booking/new" element={<WhereAmI />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedDashboard.mockResolvedValue(dashboard());
  mockedSchedule.mockResolvedValue(schedule());
  mockedPending.mockResolvedValue({ items: [] });
  // Шапка тянет личность — отказ хук глотает; главное, что промис есть.
  mockedMe.mockRejectedValue(new Error("not needed here"));
});

/** Адрес формы с разобранным окном — `URLSearchParams` кодирует «:». */
function parsedWhere(text: string): {
  path: string;
  params: Record<string, string>;
} {
  const url = new URL(text, "http://x");
  return { path: url.pathname, params: Object.fromEntries(url.searchParams) };
}

describe("«Сегодня»: «Добавить запись» на пустом дне", () => {
  it("мастер салона → /master/booking/new", async () => {
    renderAt("/master/dashboard");
    (await screen.findByRole("button", { name: "Добавить запись" })).click();
    expect(await screen.findByTestId("where")).toHaveTextContent(
      "/master/booking/new",
    );
  });

  it("соло → /solo/booking/new", async () => {
    renderAt("/solo/my-day");
    (await screen.findByRole("button", { name: "Добавить запись" })).click();
    expect(await screen.findByTestId("where")).toHaveTextContent(
      "/solo/booking/new",
    );
  });
});

describe("«Расписание»: свободное окно ведёт в запись, «недоступно» — вторично", () => {
  it("главный тап по окну → форма с ?date&from&to", async () => {
    renderAt("/master/schedule");
    (
      await screen.findByRole("button", { name: "Записать на 14:00–17:00" })
    ).click();
    const where = parsedWhere(
      (await screen.findByTestId("where")).textContent ?? "",
    );
    expect(where).toEqual({
      path: "/master/booking/new",
      params: { date: "2026-09-20", from: "14:00", to: "17:00" },
    });
  });

  it("на /solo/schedule — /solo/booking/new", async () => {
    renderAt("/solo/schedule");
    (
      await screen.findByRole("button", { name: "Записать на 14:00–17:00" })
    ).click();
    const where = parsedWhere(
      (await screen.findByTestId("where")).textContent ?? "",
    );
    expect(where).toEqual({
      path: "/solo/booking/new",
      params: { date: "2026-09-20", from: "14:00", to: "17:00" },
    });
  });

  it("«Недоступно» — отдельная кнопка окна, открывает прежний лист заявки", async () => {
    renderAt("/master/schedule");
    await screen.findByRole("button", { name: "Записать на 14:00–17:00" });
    // Имена для AT различают окна: «Записать на 14:00–17:00» / «Недоступно: 14:00–17:00»;
    // видимый текст окна на месте.
    expect(screen.getByText(/14:00 · свободно/)).toBeInTheDocument();
    screen.getByRole("button", { name: "Недоступно: 14:00–17:00" }).click();
    await waitFor(() =>
      expect(
        screen.getByLabelText("Помечу как недоступно"),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("where")).toBeNull();
  });
});
