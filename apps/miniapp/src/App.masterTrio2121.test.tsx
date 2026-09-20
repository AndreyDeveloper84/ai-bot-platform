/**
 * Мастерская поверхность: панель ровно три, профиль через аватар (DRF-2121).
 *
 * §28 п.2–3 (05.09) и §50: нижняя навигация мастера — «Сегодня | Расписание
 * | Ayla», без «Профиль» и «Ещё»; вход в Профиль — аватар в правом верхнем
 * углу на всех трёх главных разделах. Лист аватара — та же компонента, что
 * у салонной админки (DRF-2115), с пунктами мастера: Профиль · Со студией ·
 * Настройки.
 *
 * Маршруты `/master/conversations` (переписка мастер↔клиент, подлежит
 * снятию — DRF-1039/1255) и `/master/profile` остаются по прямым ссылкам.
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
    getMasterConversations: vi.fn(),
    getMasterProfileCard: vi.fn(),
    getPortfolio: vi.fn(),
  };
});

vi.mock("./lib/internal-chat-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/internal-chat-api")>();
  return {
    ...original,
    listMasterThreads: vi.fn(async () => ({ items: [], total_count: 0, offset: 0, limit: 50 })),
  };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import {
  getAylaHistory,
  getDashboard,
  getMasterConversations,
  getMasterMe,
  getMasterProfileCard,
  getMasterSchedule,
  getPendingAvailability,
  getPortfolio,
  type DashboardResponse,
} from "./lib/master-api";
import { AVATAR_SHEET_COPY, masterAvatarSheetItems } from "./lib/avatar-sheet";
import { MASTER_TAB_LABELS } from "./components/MasterTabBar";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDashboard = vi.mocked(getDashboard);
const mockedSchedule = vi.mocked(getMasterSchedule);
const mockedPending = vi.mocked(getPendingAvailability);
const mockedAyla = vi.mocked(getAylaHistory);
const mockedMasterMe = vi.mocked(getMasterMe);
const mockedConversations = vi.mocked(getMasterConversations);
const mockedProfileCard = vi.mocked(getMasterProfileCard);
const mockedPortfolio = vi.mocked(getPortfolio);

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

const DASHBOARD: DashboardResponse = {
  master: { id: "m-1", name: "Иван Смирнов", specialization: "Массаж", photo_url: "" },
  salon: { id: "t-1", name: "Формула тела" },
  now_iso: "2026-09-19T09:00:00+03:00",
  active_visit: null,
  next_visit: null,
  upcoming_today: [],
  inbox_preview: [],
  today_summary: { total_clients_today: 0, completed_count: 0, next_free_window: null },
  tab_badges: {
    conversations_unread: 2,
    schedule_has_pending_change: false,
    profile_has_owner_pending_change: true,
  },
  states: { is_day_done: false, is_offline_safe_response: false, day_off: false },
  week_summary: { week_start: "2026-09-14", week_end: "2026-09-20", bookings: 0, completed: 0, rating: null },
};

const TRIO = [...MASTER_TAB_LABELS];

function renderAppAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function tabLabels(): string[] {
  const bar = screen.getByRole("navigation", { name: "Основная навигация" });
  return Array.from(bar.querySelectorAll("button, a")).map(
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
  mockedGetMe.mockResolvedValue(MASTER_ME);
  mockedDashboard.mockResolvedValue(DASHBOARD);
  mockedSchedule.mockResolvedValue({ tenant_tz: "Europe/Moscow", from: "2026-09-19", to: "2026-09-25", days: [] });
  mockedPending.mockResolvedValue({ items: [] });
  mockedAyla.mockResolvedValue({ messages: [] });
  mockedMasterMe.mockResolvedValue({
    master: { id: "m-1", name: "Иван Смирнов", specialization: "Массаж", bio: "", photo_url: "", services: [] },
    salon: { tenant_id: "t-1", name: "Формула тела" },
    permissions: { can_edit_schedule: true, can_edit_services: true, can_message_customers: false },
  });
  mockedConversations.mockResolvedValue({
    items: [],
    section_counts: { awaiting_master: 0, ai_drafted: 0, ai_handling: 0, resolved_today: 0 },
    next_cursor: null,
  });
  mockedProfileCard.mockResolvedValue({
    master: { id: "m-1", name: "Иван Смирнов", bio: "", photo_url: "" },
    limits: { bio: 500, display_name_min: 2, avatar_bytes: 1_000_000, portfolio_bytes: 1_000_000, portfolio_count: 10 },
    portfolio: { count: 0, limit: 10 },
    accepts_today: false,
    accepts_today_reason: null,
    categories: [],
    categories_reason: null,
  });
  mockedPortfolio.mockResolvedValue({ items: [], count: 0, limit: 10 });
});

describe("панель мастера — ровно три (DRF-2121)", () => {
  it.each(["/master/dashboard", "/master/schedule", "/master/ayla"])(
    "на %s — «Сегодня · Расписание · Ayla», без Профиль/Диалоги/Ещё",
    async (path) => {
      renderAppAt(path);
      await waitFor(() => expect(tabLabels().length).toBeGreaterThan(0));
      expect(tabLabels()).toEqual(TRIO);
      for (const gone of ["Профиль", "Диалоги", "Ещё", "Дом"]) {
        expect(tabLabels()).not.toContain(gone);
      }
    },
  );

  it("мастер без адреса садится на «Сегодня»", async () => {
    renderAppAt("/");
    await waitFor(() => expect(mockedDashboard).toHaveBeenCalled());
    expect(tabLabels()).toEqual(TRIO);
  });
});

describe("аватар мастера на всех трёх разделах (DRF-2121, §28 п.3)", () => {
  it.each(["/master/dashboard", "/master/schedule", "/master/ayla"])(
    "на %s аватар с инициалами открывает лист «Профиль · Со студией · Настройки»",
    async (path) => {
      renderAppAt(path);
      const trigger = await screen.findByRole("button", { name: AVATAR_SHEET_COPY.trigger });
      await waitFor(() => expect(trigger).toHaveTextContent("ИС"));
      const sheet = await openAvatarSheet();
      const names = within(sheet)
        .getAllByRole("button")
        .map((b) => b.textContent?.trim());
      expect(names).toEqual([
        AVATAR_SHEET_COPY.profile,
        AVATAR_SHEET_COPY.studio,
        AVATAR_SHEET_COPY.settings,
      ]);
    },
  );

  it.each([
    ["profile", /Фото и имя/],
    ["studio", /Со студией/],
    ["settings", /Настройки/],
  ] as const)("пункт %s ведёт на свой экран", async (key, heading) => {
    renderAppAt("/master/dashboard");
    const sheet = await openAvatarSheet();
    await userEvent.click(within(sheet).getByRole("button", { name: AVATAR_SHEET_COPY[key] }));
    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("точка «владелец ждёт изменений профиля» — на аватаре «Сегодня», не в панели", async () => {
    renderAppAt("/master/dashboard");
    const trigger = await screen.findByRole("button", { name: AVATAR_SHEET_COPY.trigger });
    expect(within(trigger).getByLabelText("есть изменения")).toBeInTheDocument();
    const bar = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(within(bar).queryByLabelText("есть изменения")).toBeNull();
  });

  it("кнопки «Диалоги» в шапке «Сегодня» нет (снята DRF-2152, §50 п.5)", async () => {
    renderAppAt("/master/dashboard");
    // Присутствие первым: экран «Сегодня» отрисован…
    expect(await screen.findByRole("region", { name: /сегодня/i })).toBeInTheDocument();
    // …и переписок в шапке нет; /master/conversations живёт по прямой ссылке.
    expect(screen.queryByRole("button", { name: /Диалоги/ })).toBeNull();
  });
});

describe("состав листа мастера (DRF-2121)", () => {
  it("три пункта, набор закрыт", () => {
    const items = masterAvatarSheetItems();
    expect(items.map((i) => i.key)).toEqual(["profile", "studio", "settings"]);
    expect(items.map((i) => i.to)).toEqual([
      "/master/profile",
      "/master/internal-chat",
      "/master/settings",
    ]);
  });
});

describe("прямые ссылки живут (DRF-2121)", () => {
  it("/master/conversations открывается по прямой ссылке, из панели — нет", async () => {
    renderAppAt("/master/conversations");
    expect(await screen.findByRole("heading", { name: /Диалоги/ })).toBeInTheDocument();
    expect(tabLabels()).toEqual(TRIO);
  });
});
