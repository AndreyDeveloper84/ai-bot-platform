/**
 * «Детали записи» в маршрутах приложения (DRF-2156, М-4).
 *
 * Тап по записи на «Сегодня» и в «Расписании» ведёт на `/master/bookings/:id`
 * (соло — `/solo/bookings/:id`), а не в переписки (§50 п.5: прямой переписки
 * мастера с клиентом нет). Экран монтируется в обоих деревьях. Под мастерской
 * панелью активна «Расписание» (макет DRF-1185).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/identity", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/identity")>();
  return { ...original, channelIdentity: () => "identified" as const };
});

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return { ...original, getMe: vi.fn() };
});

vi.mock("./lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/master-api")>();
  return {
    ...original,
    getDashboard: vi.fn(),
    getMasterSchedule: vi.fn(),
    getPendingAvailability: vi.fn(),
    getMasterBooking: vi.fn(),
    getMasterMe: vi.fn(),
  };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import {
  getDashboard,
  getMasterBooking,
  getMasterMe,
  getMasterSchedule,
  getPendingAvailability,
  type DashboardResponse,
  type MasterBookingDetail,
  type MasterScheduleResponse,
} from "./lib/master-api";
import { formatYmdLocal } from "./lib/masterDateFormat";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDashboard = vi.mocked(getDashboard);
const mockedSchedule = vi.mocked(getMasterSchedule);
const mockedPending = vi.mocked(getPendingAvailability);
const mockedBooking = vi.mocked(getMasterBooking);
const mockedMasterMe = vi.mocked(getMasterMe);

const MASTER_ME: MeResponse = {
  user: { id: "u-1", name: "Иван Смирнов", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula" },
  role: "master",
  capabilities: [],
  is_customer: false,
  is_master: true,
  is_receptionist: false,
  is_admin: false,
  is_owner: false,
  master_id: "m-1",
  landing_path: "/master/dashboard",
  is_solo_provider: false,
};

const SOLO_ME: MeResponse = {
  ...MASTER_ME,
  tenant: { id: "t-1", name: "Demo", slug: "demo" },
  is_customer: true,
  is_solo_provider: true,
};

/** Сегодня по часам устройства — «Расписание» открывается на нём. */
const TODAY = formatYmdLocal(new Date());
const TODAY_10 = `${TODAY}T10:00:00`;
const TODAY_11 = `${TODAY}T11:00:00`;

const DASHBOARD: DashboardResponse = {
  master: { id: "m-1", name: "Иван Смирнов", specialization: "Массаж", photo_url: "" },
  salon: { id: "t-1", name: "Формула тела" },
  now_iso: `${TODAY}T09:00:00`,
  active_visit: null,
  next_visit: {
    booking_id: "b-42",
    client_first_name: "Мария",
    client_last_initial: "К.",
    visit_at: TODAY_10,
    end_at: TODAY_11,
    minutes_until: 60,
    service_name: "Классический массаж",
    duration_min: 60,
    is_returning_customer: false,
    customer_intent_hint: "",
  },
  upcoming_today: [],
  inbox_preview: [],
  today_summary: { total_clients_today: 1, completed_count: 0, next_free_window: null },
  tab_badges: {
    conversations_unread: 0,
    schedule_has_pending_change: false,
    profile_has_owner_pending_change: false,
  },
  states: { is_day_done: false, is_offline_safe_response: false, day_off: false },
  week_summary: { week_start: TODAY, week_end: TODAY, bookings: 1, completed: 0, rating: null },
};

const SCHEDULE: MasterScheduleResponse = {
  tenant_tz: "Europe/Moscow",
  from: TODAY,
  to: TODAY,
  days: [
    {
      date: TODAY,
      is_off_day: false,
      working_hours: { start: "09:00", end: "18:00" },
      bookings: [
        {
          booking_id: "b-42",
          visit_at: TODAY_10,
          duration_min: 60,
          service_name: "Классический массаж",
          client_first_name: "Мария",
          client_last_initial: "К.",
          is_in_progress: false,
          is_returning_customer: false,
        },
      ],
      blocks: [],
      free_windows: [],
      conflicts: [],
    },
  ],
};

const DETAIL: MasterBookingDetail = {
  id: "b-42",
  client: { name_initial: "Мария К.", last_visit_date: null },
  service: { id: "s-1", name: "Классический массаж" },
  start_at: TODAY_10,
  end_at: TODAY_11,
  duration_min: 60,
  status: "confirmed",
  temporal_state: "upcoming",
  minutes_until: 60,
  checked_at: `${TODAY}T09:00:00`,
};

function renderAppAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

async function findDetail() {
  return screen.findByRole("main", { name: /Мария К\./ });
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mockedGetMe.mockResolvedValue(MASTER_ME);
  mockedDashboard.mockResolvedValue(DASHBOARD);
  mockedSchedule.mockResolvedValue(SCHEDULE);
  mockedPending.mockResolvedValue({ items: [] });
  mockedBooking.mockResolvedValue(DETAIL);
  mockedMasterMe.mockResolvedValue({
    master: { id: "m-1", name: "Иван Смирнов", specialization: "Массаж", bio: "", photo_url: "", services: [] },
    salon: { tenant_id: "t-1", name: "Формула тела" },
    permissions: { can_edit_schedule: true, can_edit_services: true, can_message_customers: false },
  });
});

describe("маршруты «Детали записи» (DRF-2156)", () => {
  it("/master/bookings/:id монтируется; панель — «Расписание» активна", async () => {
    renderAppAt("/master/bookings/b-42");
    await findDetail();
    expect(mockedBooking).toHaveBeenCalledWith("b-42", expect.anything());
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(nav.querySelector('[aria-label="Расписание"]')).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("До визита 1 ч")).toBeInTheDocument();
  });

  it("/solo/bookings/:id монтируется у соло; /solo/bookings без id — по-прежнему «Расписание»", async () => {
    mockedGetMe.mockResolvedValue(SOLO_ME);
    renderAppAt("/solo/bookings/b-42");
    await findDetail();
    expect(mockedBooking).toHaveBeenCalledWith("b-42", expect.anything());
  });
});

describe("тап по записи → детали, не переписки", () => {
  it("«Сегодня»: карточка ближайшей записи — ссылка на /master/bookings/b-42", async () => {
    renderAppAt("/master/dashboard");
    const card = await screen.findByRole("link", { name: /Мария К\./ });
    expect(card).toHaveAttribute("href", "/master/bookings/b-42");
    await userEvent.click(card);
    await findDetail();
    expect(screen.queryByText(/Экран переписок|Все диалоги/)).toBeNull();
  });

  it("«Сегодня» соло: ссылка ведёт на /solo/bookings/b-42", async () => {
    mockedGetMe.mockResolvedValue(SOLO_ME);
    renderAppAt("/solo/my-day");
    const card = await screen.findByRole("link", { name: /Мария К\./ });
    expect(card).toHaveAttribute("href", "/solo/bookings/b-42");
  });

  it("«Расписание»: тап по записи открывает детали этой записи", async () => {
    renderAppAt("/master/schedule");
    // М-6 (DRF-2157): карточка в «Расписании» — та же MasterBookingCard, ссылка.
    const card = await screen.findByRole("link", { name: /Мария К\./ });
    await userEvent.click(card);
    await findDetail();
    await waitFor(() => expect(mockedBooking).toHaveBeenCalledWith("b-42", expect.anything()));
  });

  it("«Расписание» соло: тап по записи ведёт на /solo/bookings/:id — панель одна, соло, «Расписание» активна", async () => {
    mockedGetMe.mockResolvedValue(SOLO_ME);
    renderAppAt("/solo/schedule");
    // М-6 (DRF-2157): карточка в «Расписании» — та же MasterBookingCard, ссылка.
    const card = await screen.findByRole("link", { name: /Мария К\./ });
    await userEvent.click(card);
    await findDetail();
    // Одна панель — соло (App), мастерская внутри экрана на /solo/* не рисуется.
    const navs = screen.getAllByRole("navigation", { name: "Основная навигация" });
    expect(navs).toHaveLength(1);
    expect(navs[0]).toHaveClass("solo-tabbar");
    expect(navs[0]?.querySelector('[aria-label="Расписание"]')).toHaveAttribute("aria-current", "page");
  });
});
