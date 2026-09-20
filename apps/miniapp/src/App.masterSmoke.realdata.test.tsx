/**
 * Дымовой прогон мастерской поверхности на «боевых» формах данных (без
 * ErrorBoundary любое исключение в рендере = белый экран всего приложения).
 *
 * Инцидент 20.09: запись без duration_min на «Сегодня» → `new Date(NaN)
 * .toISOString()` → RangeError → белый экран. Здесь — старая/рваная форма
 * ответа дашборда и расписания, соло и салонный мастер.
 */
import { render, screen } from "@testing-library/react";
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
    getMasterMe: vi.fn(),
    getOnboardingReadiness: vi.fn(),
  };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import {
  getDashboard,
  getMasterMe,
  getMasterSchedule,
  getOnboardingReadiness,
  getPendingAvailability,
} from "./lib/master-api";
import { App } from "./App";

const ME: MeResponse = {
  user: { id: "u-1", name: "Архипкин Денис", phone_masked: "" },
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

// Боевой ответ дашборда мастера без услуг и записей, выходной, поля как есть.
const DASHBOARD_LIVE: unknown = {
  master: { id: "m-1", name: "Архипкин", specialization: "", photo_url: null },
  salon: { id: "t-1", name: "Формула тела" },
  now_iso: "2026-09-20T18:46:12.123456+03:00",
  active_visit: null,
  next_visit: null,
  upcoming_today: [],
  inbox_preview: [],
  today_summary: { total_clients_today: 0, completed_count: 0, next_free_window: null },
  tab_badges: { conversations_unread: 0, schedule_has_pending_change: false, profile_has_owner_pending_change: false },
  states: { is_day_done: false, is_offline_safe_response: false, day_off: true },
  week_summary: { week_start: "2026-09-14", week_end: "2026-09-20", bookings: 0, completed: 0, rating: null },
};

// Старая форма (бот без М-1 полей): нет end_at / minutes_until / upcoming_today / day_off.
const DASHBOARD_OLD_SHAPE: unknown = {
  master: { id: "m-1", name: "Архипкин", specialization: "Массаж", photo_url: "" },
  salon: { id: "t-1", name: "Формула тела" },
  now_iso: "2026-09-20T11:15:00+03:00",
  active_visit: {
    booking_id: "b-0",
    client_first_name: "Мария",
    client_last_initial: "",
    service_name: "",
    started_at: "2026-09-20T11:00:00+03:00",
    duration_min: null,
    minutes_remaining: 45,
    is_in_progress: true,
    note: "",
  },
  next_visit: {
    booking_id: "b-1",
    client_first_name: "Анна",
    client_last_initial: "П.",
    visit_at: "2026-09-20T12:00:00+03:00",
    service_name: null,
    duration_min: undefined,
    is_returning_customer: true,
    customer_intent_hint: "",
  },
  inbox_preview: [],
  today_summary: { total_clients_today: 2, completed_count: 0, next_free_window: null },
  tab_badges: { conversations_unread: 0, schedule_has_pending_change: false, profile_has_owner_pending_change: false },
  states: { is_day_done: false, is_offline_safe_response: false },
  week_summary: null,
};

const SCHEDULE_LIVE: unknown = {
  tenant_tz: "Europe/Moscow",
  from: "2026-09-20",
  to: "2026-09-20",
  days: [
    {
      date: "2026-09-21", // тенантская дата ≠ дате устройства
      is_off_day: false,
      working_hours: { start: "09:00", end: "18:00" },
      bookings: [
        {
          booking_id: "b-9",
          visit_at: "2026-09-21T07:00:00Z",
          duration_min: null,
          service_name: null,
          client_first_name: "",
          client_last_initial: "",
          is_in_progress: false,
          is_returning_customer: false,
        },
      ],
      blocks: [{ exception_id: "e-1", start: "2026-09-21T10:00:00Z", end: "2026-09-21T11:00:00Z", reason: "lunch", approved: true }],
      free_windows: [{ start: "12:00", end: "13:30", duration_min: 90 }],
      conflicts: [],
    },
  ],
};

const SOLO_ME: MeResponse = { ...ME, is_customer: true, is_solo_provider: true };

function renderAppAt(path: string) {
  vi.mocked(getMe).mockResolvedValue(path.startsWith("/solo/") ? SOLO_ME : ME);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  window.localStorage.clear();
  vi.mocked(getMe).mockResolvedValue(ME);
  vi.mocked(getPendingAvailability).mockResolvedValue({ items: [] });
  vi.mocked(getOnboardingReadiness).mockRejectedValue(new Error("503"));
  vi.mocked(getMasterMe).mockResolvedValue({
    master: { id: "m-1", name: "Архипкин", specialization: "", bio: "", photo_url: "", services: [] },
    salon: { tenant_id: "t-1", name: "Формула тела" },
    permissions: { can_edit_schedule: false, can_edit_services: false, can_message_customers: false },
  });
});

describe("дым: боевые формы данных не роняют мастерскую поверхность", () => {
  it.each([
    ["/master/dashboard", DASHBOARD_LIVE],
    ["/master/dashboard", DASHBOARD_OLD_SHAPE],
    ["/solo/my-day", DASHBOARD_LIVE],
    ["/solo/my-day", DASHBOARD_OLD_SHAPE],
  ])("%s с payload %#", async (path, payload) => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(getDashboard).mockResolvedValue(payload as never);
    vi.mocked(getMasterSchedule).mockResolvedValue(SCHEDULE_LIVE as never);
    renderAppAt(path);
    await screen.findByRole("region", { name: /сегодня/i }, { timeout: 4000 });
    const thrown = errors.mock.calls.filter((c) => /The above error occurred|Uncaught|TypeError|ReferenceError/.test(String(c[0]) + String(c[1])));
    errors.mockRestore();
    expect(thrown).toEqual([]);
  });

  it.each(["/master/schedule", "/solo/schedule"])("%s с боевым расписанием", async (path) => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(getDashboard).mockResolvedValue(DASHBOARD_LIVE as never);
    vi.mocked(getMasterSchedule).mockResolvedValue(SCHEDULE_LIVE as never);
    renderAppAt(path);
    await screen.findByRole("heading", { name: /Расписание/ }, { timeout: 4000 });
    const thrown = errors.mock.calls.filter((c) => /The above error occurred|Uncaught|TypeError|ReferenceError/.test(String(c[0]) + String(c[1])));
    errors.mockRestore();
    expect(thrown).toEqual([]);
  });
});
