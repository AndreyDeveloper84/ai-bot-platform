/**
 * Прямой переписки мастера с клиентом нет (OD-7 21.08, §50 п.5; DRF-1255).
 *
 * Снимаются экраны `MasterConversationsScreen` (список) и
 * `MasterConversationDetailScreen` (чтение + ОТПРАВКА ответа клиенту — ровно
 * то, чего по решению быть не должно) и всё, что в них ведёт. Старые адреса
 * (`/master/conversations`, `/master/conversations/:id`, `/solo/ai`) могут
 * жить в старых DM/deeplink'ах — они должны вести на «Сегодня», а не в пустой
 * экран. «Со студией» (`MasterInternalChat*`) — переписка мастера с салоном,
 * под запрет не подпадает и остаётся.
 *
 * Данные и бэкенд переписок не трогаются — это DRF-1528.
 */
import { render, screen, waitFor } from "@testing-library/react";
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
  getDashboard,
  getMasterMe,
  getMasterSchedule,
  getPendingAvailability,
  type DashboardResponse,
} from "./lib/master-api";
import { App } from "./App";

const APP_SOURCE = import.meta.glob("./App.tsx", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;
const SCREEN_FILES = import.meta.glob("./screens/Master*.tsx", { query: "?raw", import: "default", eager: true });
const MASTER_API_SOURCE = import.meta.glob("./lib/master-api.ts", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const mockedGetMe = vi.mocked(getMe);
const mockedDashboard = vi.mocked(getDashboard);

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
const SOLO_ME: MeResponse = { ...MASTER_ME, is_customer: true, is_solo_provider: true };

const DASHBOARD: DashboardResponse = {
  master: { id: "m-1", name: "Иван Смирнов", specialization: "Массаж", photo_url: "" },
  salon: { id: "t-1", name: "Формула тела" },
  now_iso: "2026-09-20T09:00:00",
  active_visit: null,
  next_visit: null,
  upcoming_today: [],
  inbox_preview: [],
  today_summary: { total_clients_today: 0, completed_count: 0, next_free_window: null },
  tab_badges: {
    conversations_unread: 3,
    schedule_has_pending_change: false,
    profile_has_owner_pending_change: false,
  },
  states: { is_day_done: false, is_offline_safe_response: false, day_off: false },
  week_summary: { week_start: "2026-09-14", week_end: "2026-09-20", bookings: 0, completed: 0, rating: null },
};

function renderAppAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mockedGetMe.mockResolvedValue(MASTER_ME);
  mockedDashboard.mockResolvedValue(DASHBOARD);
  vi.mocked(getMasterSchedule).mockResolvedValue({
    tenant_tz: "Europe/Moscow",
    from: "2026-09-19",
    to: "2026-09-25",
    days: [],
  });
  vi.mocked(getPendingAvailability).mockResolvedValue({ items: [] });
  vi.mocked(getMasterMe).mockResolvedValue({
    master: { id: "m-1", name: "Иван Смирнов", specialization: "Массаж", bio: "", photo_url: "", services: [] },
    salon: { tenant_id: "t-1", name: "Формула тела" },
    permissions: { can_edit_schedule: true, can_edit_services: true, can_message_customers: false },
  });
});

describe("файлы переписки мастер↔клиент сняты (DRF-1255)", () => {
  it("экранов MasterConversations*/MasterConversationDetail* больше нет", () => {
    const names = Object.keys(SCREEN_FILES).map((p) => p.replace(/^.*\//, ""));
    expect(names.filter((n) => /^MasterConversation/.test(n))).toEqual([]);
    // «Со студией» — остаётся.
    expect(names).toContain("MasterInternalChatListScreen.tsx");
    expect(names).toContain("MasterInternalChatThreadScreen.tsx");
  });

  it("App.tsx не монтирует их и не знает адресов переписок как экранов", () => {
    const src = Object.values(APP_SOURCE)[0] ?? "";
    expect(src).not.toContain("MasterConversationsScreen");
    expect(src).not.toContain("MasterConversationDetailScreen");
  });

  it("клиент API мастера не зовёт ручки переписки с клиентом (данные/бэкенд — DRF-1528)", () => {
    const src = Object.values(MASTER_API_SOURCE)[0] ?? "";
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    expect(code).not.toContain("`/conversations");
    expect(code).not.toContain('"/conversations');
    for (const fn of ["getMasterConversations", "getConversationDetail", "markConversationRead", "sendAsMe", "releaseToAi"]) {
      expect(code, `${fn} — функция переписки с клиентом`).not.toContain(`export const ${fn}`);
    }
  });
});

describe("старые адреса ведут на «Сегодня», не в пустой экран", () => {
  it.each(["/master/conversations", "/master/conversations/c-1"])("%s → /master/dashboard", async (path) => {
    renderAppAt(path);
    await waitFor(() => expect(screen.getByRole("region", { name: /сегодня/i })).toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: /Диалоги/ })).toBeNull();
    expect(screen.queryByText(/Экран переписок|Все диалоги/)).toBeNull();
  });

  it("соло: /solo/ai → /solo/my-day", async () => {
    mockedGetMe.mockResolvedValue(SOLO_ME);
    renderAppAt("/solo/ai");
    await waitFor(() => expect(screen.getByRole("region", { name: /сегодня/i })).toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: /Диалоги/ })).toBeNull();
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(nav).toHaveClass("solo-tabbar");
    // На соло-«Сегодня» нет входа «AI-помощник», ведущего в никуда.
    expect(screen.queryByText(/AI-помощник/)).toBeNull();
    expect(screen.queryByRole("button", { name: /AI-помощник/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /AI-помощник/ })).toBeNull();
  });

  it("на «Сегодня» слов «Диалоги»/«Открыть диалог»/«Все диалоги» нет, бейдж непрочитанных не рисуется", async () => {
    renderAppAt("/master/dashboard");
    await screen.findByRole("region", { name: /сегодня/i });
    expect(screen.queryByText(/Диалог/)).toBeNull();
    expect(screen.queryByText(/^3$/)).toBeNull();
  });
});
