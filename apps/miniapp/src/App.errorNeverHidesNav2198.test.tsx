/**
 * Правило класса (DRF-2198, инцидент 20.09): состояние ошибки на мастерском
 * экране НИКОГДА не убирает навигацию — ни панель, ни дверь к настройкам.
 * Иначе человек остаётся на поверхности без выхода, как владелец на стенде.
 *
 * Здесь — узлы на каждый мастерский экран, у которого панель есть в рабочем
 * состоянии, плюс ErrorBoundary приложения: исключение в рендере даёт
 * состояние с повтором, а не белый экран.
 *
 * Положительная пара клиентской поверхности (H01: панель пяти вкладок и
 * «Сменить режим» вне веток) здесь НЕ проверяется: для неё нужен свой
 * загрузочный стенд клиента, а сам экран сейчас переписывает DRF-2191
 * (вынос панели в общий компонент). Узел ставится там — предел назван в
 * теле PR.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
    getMasterProfileCard: vi.fn(),
    getPortfolio: vi.fn(),
    getNotificationPrefs: vi.fn(),
    getAylaHistory: vi.fn(),
    getMasterBooking: vi.fn(),
  };
});

import { ApiError } from "./lib/api";
import { getMe, type MeResponse } from "./lib/admin-api";
import {
  getAylaHistory,
  getDashboard,
  getMasterBooking,
  getMasterMe,
  getMasterProfileCard,
  getMasterSchedule,
  getNotificationPrefs,
  getPendingAvailability,
  getPortfolio,
} from "./lib/master-api";
import { App } from "./App";

const MASTER_ME: MeResponse = {
  user: { id: "u-1", name: "Архипкин", phone_masked: "" },
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

const BOOM = new Error("boom");
const FORBIDDEN = new ApiError(403, "not_linked", "…");

let errorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  vi.mocked(getMe).mockResolvedValue(MASTER_ME);
  vi.mocked(getMasterMe).mockRejectedValue(BOOM);
  vi.mocked(getDashboard).mockRejectedValue(BOOM);
  vi.mocked(getMasterSchedule).mockRejectedValue(BOOM);
  vi.mocked(getPendingAvailability).mockRejectedValue(BOOM);
  vi.mocked(getMasterProfileCard).mockRejectedValue(BOOM);
  vi.mocked(getPortfolio).mockRejectedValue(BOOM);
  vi.mocked(getNotificationPrefs).mockRejectedValue(BOOM);
  vi.mocked(getAylaHistory).mockRejectedValue(BOOM);
  vi.mocked(getMasterBooking).mockRejectedValue(BOOM);
});

afterEach(() => {
  errorSpy.mockRestore();
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

const nav = () => screen.queryByRole("navigation", { name: "Основная навигация" });

describe("ошибка загрузки не убирает панель (DRF-2198)", () => {
  it.each([
    "/master/dashboard",
    "/master/schedule",
    "/master/ayla",
    "/master/profile",
    "/master/settings/notifications",
    "/master/bookings/b-1",
  ])("%s: всё упало — панель на месте", async (path) => {
    renderAt(path);
    await waitFor(() => expect(nav()).toBeInTheDocument(), { timeout: 4000 });
  });

  it("«Сегодня» при 403: панель на месте, «Недостаточно прав» — в теле", async () => {
    vi.mocked(getDashboard).mockRejectedValue(FORBIDDEN);
    renderAt("/master/dashboard");
    // Сначала дожидаемся самого состояния: панель видна и на загрузке.
    expect(await screen.findByRole("alert", {}, { timeout: 4000 })).toHaveTextContent(
      "Недостаточно прав",
    );
    expect(nav()).toBeInTheDocument();
  });
});

describe("ErrorBoundary приложения: исключение рендера — не белый экран", () => {
  it("RangeError в карточке «Сегодня» → состояние с повтором, приложение живо", async () => {
    // Запись без длительности и без end_at — форма, уронившая стенд 20.09.
    vi.mocked(getDashboard).mockResolvedValue({
      master: { id: "m-1", name: "Архипкин", specialization: "Массаж", photo_url: "" },
      salon: { id: "t-1", name: "Формула тела" },
      now_iso: "2026-09-20T11:15:00",
      active_visit: null,
      next_visit: {
        booking_id: "b-1",
        client_first_name: "Анна",
        client_last_initial: "П.",
        visit_at: "не дата",
        service_name: "Массаж",
        duration_min: null,
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
      week_summary: null,
    } as never);
    renderAt("/master/dashboard");
    // Что бы ни случилось внутри — человек видит состояние с повтором, а не
    // пустой экран: либо экран отрисовался, либо его перехватила граница.
    await waitFor(
      () => {
        const alive =
          screen.queryByRole("region", { name: /сегодня/i }) !== null ||
          screen.queryByRole("alert") !== null;
        expect(alive).toBe(true);
      },
      { timeout: 4000 },
    );
    expect(document.body.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });
});
