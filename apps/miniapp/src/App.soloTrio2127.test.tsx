/**
 * Соло-поверхность: панель ровно три, разделы через аватар (DRF-2127).
 *
 * §28 п.2–3 / §50: у всех рабочих поверхностей нижняя панель — «Сегодня |
 * Расписание | Ayla», «Профиль» и «Ещё» запрещены; вход в профиль и
 * остальные разделы — аватар. Пять вкладок + «Ещё» соло-поверхности
 * (SoloBottomNav) переводятся на тройку тем же `AvatarSheet`.
 *
 * Куда ушло снятое с панели: Клиенты, Услуги, Отзывы, Настройки, Профиль —
 * в лист аватара; «Управление салоном» — там же при владельческой роли
 * (DRF-1149). Доходы (экран-заглушка, §33) и «AI-помощник» (на деле —
 * переписка с клиентами, DRF-1039/1255) — в листе не рисуются, адреса
 * живут по прямым ссылкам.
 *
 * На соло-адресах рисуется ровно ОДНА нижняя панель: `MasterTabBar`
 * общих мастерских экранов на `/solo/*` не монтируется.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
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
    getAylaHistory: vi.fn(),
    getMasterMe: vi.fn(),
  };
});

vi.mock("./lib/master-billing", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/master-billing")>();
  return { ...original, getPayoutPreview: vi.fn() };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import {
  getAylaHistory,
  getDashboard,
  getMasterMe,
  getMasterSchedule,
  getPendingAvailability,
  type DashboardResponse,
} from "./lib/master-api";
import { getPayoutPreview } from "./lib/master-billing";
import { AVATAR_SHEET_COPY, masterAvatarSheetItems } from "./lib/avatar-sheet";
import { MASTER_TAB_LABELS } from "./components/MasterTabBar";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDashboard = vi.mocked(getDashboard);
const mockedSchedule = vi.mocked(getMasterSchedule);
const mockedPending = vi.mocked(getPendingAvailability);
const mockedAyla = vi.mocked(getAylaHistory);
const mockedMasterMe = vi.mocked(getMasterMe);
const mockedPayout = vi.mocked(getPayoutPreview);

const SOLO_OWNER_ME: MeResponse = {
  user: { id: "u-1", name: "Ольга Иванова", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Demo", slug: "demo" },
  role: "owner",
  capabilities: [],
  is_customer: true,
  is_master: true,
  is_receptionist: false,
  is_admin: false,
  is_owner: true,
  master_id: "m-1",
  landing_path: "/admin/team",
  is_solo_provider: true,
};
const SOLO_MASTER_ONLY_ME: MeResponse = {
  ...SOLO_OWNER_ME,
  role: "master",
  is_owner: false,
  is_admin: false,
  is_receptionist: false,
};

const DASHBOARD: DashboardResponse = {
  master: { id: "m-1", name: "Ольга Иванова", specialization: "Массаж", photo_url: "" },
  salon: { id: "t-1", name: "Demo" },
  now_iso: "2026-09-19T09:00:00+03:00",
  active_visit: null,
  next_visit: null,
  upcoming_today: [],
  inbox_preview: [],
  today_summary: { total_clients_today: 0, completed_count: 0, next_free_window: null },
  tab_badges: {
    conversations_unread: 0,
    schedule_has_pending_change: false,
    profile_has_owner_pending_change: false,
  },
  states: { is_day_done: false, is_offline_safe_response: false, day_off: false },
  week_summary: { week_start: "2026-09-14", week_end: "2026-09-20", bookings: 0, completed: 0, rating: null },
};

const TRIO = [...MASTER_TAB_LABELS];
const SOLO_ROOTS = ["/solo/my-day", "/solo/schedule", "/solo/ayla"] as const;

function renderAppAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function navBars() {
  return screen.getAllByRole("navigation", { name: "Основная навигация" });
}

function tabLabels(): string[] {
  const bars = navBars();
  expect(bars).toHaveLength(1);
  return Array.from(bars[0]!.querySelectorAll("button, a")).map(
    (b) => b.getAttribute("aria-label") ?? b.textContent?.trim() ?? "",
  );
}

async function openAvatarSheet() {
  await userEvent.click(await screen.findByRole("button", { name: AVATAR_SHEET_COPY.trigger }));
  return screen.findByRole("dialog", { name: AVATAR_SHEET_COPY.title });
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mockedGetMe.mockResolvedValue(SOLO_OWNER_ME);
  mockedDashboard.mockResolvedValue(DASHBOARD);
  mockedSchedule.mockResolvedValue({ tenant_tz: "Europe/Moscow", from: "2026-09-19", to: "2026-09-25", days: [] });
  mockedPending.mockResolvedValue({ items: [] });
  mockedAyla.mockResolvedValue({ messages: [] });
  mockedMasterMe.mockResolvedValue({
    master: { id: "m-1", name: "Ольга Иванова", specialization: "Массаж", bio: "", photo_url: "", services: [] },
    salon: { tenant_id: "t-1", name: "Demo" },
    permissions: { can_edit_schedule: true, can_edit_services: true, can_message_customers: false },
  });
  mockedPayout.mockRejectedValue(new Error("not under test"));
});

describe("соло-панель — ровно три и ровно одна (DRF-2127)", () => {
  it.each(SOLO_ROOTS)("на %s — «Сегодня · Расписание · Ayla», одна панель", async (path) => {
    renderAppAt(path);
    await waitFor(() => expect(navBars().length).toBeGreaterThan(0));
    expect(tabLabels()).toEqual(TRIO);
    for (const gone of ["Ещё", "Профиль", "День", "Записи", "Клиенты", "Услуги", "Дом"]) {
      expect(tabLabels()).not.toContain(gone);
    }
  });

  it("«Ayla» на соло — диалог с ассистентом (OD-7), не переписка с клиентами", async () => {
    renderAppAt("/solo/ayla");
    expect(await screen.findByRole("heading", { name: "Ayla" })).toBeInTheDocument();
    expect(mockedAyla).toHaveBeenCalled();
    expect(navBars()).toHaveLength(1);
  });

  it("тап по вкладке «Ayla» ведёт на /solo/ayla, панель остаётся одна", async () => {
    renderAppAt("/solo/my-day");
    await waitFor(() => expect(navBars().length).toBeGreaterThan(0));
    await userEvent.click(within(navBars()[0]!).getByRole("link", { name: "Ayla" }));
    expect(await screen.findByRole("heading", { name: "Ayla" })).toBeInTheDocument();
    expect(navBars()).toHaveLength(1);
  });

  it("/solo/more (старая ссылка) ведёт на «Сегодня», листа «Ещё» нет", async () => {
    renderAppAt("/solo/more");
    await waitFor(() => expect(mockedDashboard).toHaveBeenCalled());
    expect(screen.queryByRole("dialog", { name: "Меню «Ещё»" })).toBeNull();
    expect(tabLabels()).toEqual(TRIO);
  });

  it("/solo/bookings остаётся алиасом расписания", async () => {
    renderAppAt("/solo/bookings");
    await waitFor(() => expect(mockedSchedule).toHaveBeenCalled());
    expect(tabLabels()).toEqual(TRIO);
  });
});

describe("аватар соло — разделы, снятые с панели (DRF-2127)", () => {
  it.each(SOLO_ROOTS)("на %s аватар открывает лист с соло-адресами", async (path) => {
    renderAppAt(path);
    const sheet = await openAvatarSheet();
    const names = within(sheet)
      .getAllByRole("button")
      .map((b) => b.textContent?.trim());
    expect(names).toEqual([
      AVATAR_SHEET_COPY.profile,
      AVATAR_SHEET_COPY.customers,
      AVATAR_SHEET_COPY.services,
      AVATAR_SHEET_COPY.reviews,
      AVATAR_SHEET_COPY.salon,
      AVATAR_SHEET_COPY.settings,
    ]);
  });

  it("«Управление салоном» — только при владельческой роли (DRF-1149)", async () => {
    mockedGetMe.mockResolvedValue(SOLO_MASTER_ONLY_ME);
    renderAppAt("/solo/my-day");
    const sheet = await openAvatarSheet();
    expect(within(sheet).queryByRole("button", { name: AVATAR_SHEET_COPY.salon })).toBeNull();
    expect(within(sheet).getByRole("button", { name: AVATAR_SHEET_COPY.profile })).toBeInTheDocument();
  });

  it("пункт «Клиенты» ведёт на /solo/customers с той же одной панелью", async () => {
    renderAppAt("/solo/my-day");
    const sheet = await openAvatarSheet();
    await userEvent.click(within(sheet).getByRole("button", { name: AVATAR_SHEET_COPY.customers }));
    expect(await screen.findByRole("heading", { name: /Клиенты/ })).toBeInTheDocument();
    expect(tabLabels()).toEqual(TRIO);
  });

  it("Доходы и AI-помощник в листе не рисуются (§33 / DRF-1039)", async () => {
    renderAppAt("/solo/my-day");
    const sheet = await openAvatarSheet();
    expect(within(sheet).getByRole("button", { name: AVATAR_SHEET_COPY.profile })).toBeInTheDocument();
    expect(within(sheet).queryByRole("button", { name: "Доходы" })).toBeNull();
    expect(within(sheet).queryByRole("button", { name: "AI-помощник" })).toBeNull();
  });
});

describe("состав соло-листа (DRF-2127)", () => {
  it("соло без роли — пять адресов /solo/*", () => {
    expect(masterAvatarSheetItems({ surface: "solo", salonAdmin: false }).map((i) => i.to)).toEqual([
      "/solo/profile",
      "/solo/customers",
      "/solo/services",
      "/solo/reviews",
      "/solo/settings",
    ]);
  });

  it("соло с владельческой ролью — плюс «Управление салоном» перед настройками", () => {
    expect(masterAvatarSheetItems({ surface: "solo", salonAdmin: true }).map((i) => i.key)).toEqual([
      "profile",
      "customers",
      "services",
      "reviews",
      "salon",
      "settings",
    ]);
  });

  it("мастерская поверхность — прежние три (DRF-2121)", () => {
    expect(masterAvatarSheetItems({ surface: "master" }).map((i) => i.to)).toEqual([
      "/master/profile",
      "/master/internal-chat",
      "/master/settings",
    ]);
  });
});
