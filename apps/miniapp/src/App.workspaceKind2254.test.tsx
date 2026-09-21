/**
 * DRF-2254 — экраны самообслуживания мастера гейтятся по `workspace_kind`.
 *
 * «Чьё место и кто ведёт услуги» — `Tenant.kind` каталога, единственный
 * источник; `/me` отдаёт его как `workspace_kind`. `is_solo_provider` —
 * только раскладка (соло-поверхность монтируется по нему, как прежде).
 *
 * Случай A замера: один человек (соло-поверхность), а каталог называет
 * пространство салоном — «Место», «Услуги», направления и выбор услуг не
 * рисуются, прямые ссылки уводят на «Мой день» (раньше — отказ каталога
 * «место ведёт владелец салона» самой владелице). `solo` и «не знаю» (null
 * или поле отсутствует) — всё как прежде: авторитетен отказ каталога.
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
    getServiceLocations: vi.fn(),
    getServiceSelection: vi.fn(),
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
  getServiceLocations,
  getServiceSelection,
  type DashboardResponse,
} from "./lib/master-api";
import { getPayoutPreview } from "./lib/master-billing";
import { AVATAR_SHEET_COPY, masterAvatarSheetItems } from "./lib/avatar-sheet";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDashboard = vi.mocked(getDashboard);
const mockedLocations = vi.mocked(getServiceLocations);
const mockedSelection = vi.mocked(getServiceSelection);

const SOLO_ME: MeResponse = {
  user: { id: "u-1", name: "Ольга Иванова", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Demo", slug: "demo" },
  role: "master",
  capabilities: [],
  is_customer: true,
  is_master: true,
  is_receptionist: false,
  is_admin: false,
  is_owner: false,
  master_id: "m-1",
  landing_path: "/master",
  is_solo_provider: true,
};

const DASHBOARD: DashboardResponse = {
  master: { id: "m-1", name: "Ольга Иванова", specialization: "Массаж", photo_url: "" },
  salon: { id: "t-1", name: "Demo" },
  now_iso: "2026-09-21T09:00:00+03:00",
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
  week_summary: { week_start: "2026-09-21", week_end: "2026-09-27", bookings: 0, completed: 0, rating: null },
};

const SELF_SERVICE_PATHS = [
  "/solo/place",
  "/solo/services",
  "/solo/directions",
  "/solo/services/select",
] as const;

function renderAppAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

async function avatarSheetNames(): Promise<string[]> {
  await userEvent.click(await screen.findByRole("button", { name: AVATAR_SHEET_COPY.trigger }));
  const sheet = await screen.findByRole("dialog", { name: AVATAR_SHEET_COPY.title });
  return within(sheet)
    .getAllByRole("button")
    .map((b) => b.textContent?.trim() ?? "");
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mockedDashboard.mockResolvedValue(DASHBOARD);
  vi.mocked(getMasterSchedule).mockResolvedValue({
    tenant_tz: "Europe/Moscow",
    from: "2026-09-21",
    to: "2026-09-27",
    days: [],
  });
  vi.mocked(getPendingAvailability).mockResolvedValue({ items: [] });
  vi.mocked(getAylaHistory).mockResolvedValue({ messages: [] });
  vi.mocked(getMasterMe).mockResolvedValue({
    master: { id: "m-1", name: "Ольга Иванова", specialization: "Массаж", bio: "", photo_url: "", services: [] },
    salon: { tenant_id: "t-1", name: "Demo" },
    permissions: { can_edit_schedule: true, can_edit_services: true, can_message_customers: false },
  });
  vi.mocked(getPayoutPreview).mockRejectedValue(new Error("not under test"));
  mockedLocations.mockRejectedValue(new Error("not under test"));
  mockedSelection.mockRejectedValue(new Error("not under test"));
});

describe("случай A — соло-раскладка, каталог: салон (DRF-2254)", () => {
  beforeEach(() => {
    mockedGetMe.mockResolvedValue({ ...SOLO_ME, workspace_kind: "salon" });
  });

  it("в листе аватара нет «Услуг», остальное на месте", async () => {
    renderAppAt("/solo/my-day");
    const names = await avatarSheetNames();
    expect(names).toContain(AVATAR_SHEET_COPY.profile);
    expect(names).not.toContain(AVATAR_SHEET_COPY.services);
  });

  it.each(SELF_SERVICE_PATHS)("прямая ссылка %s уводит на «Мой день», каталог не спрашивается", async (path) => {
    renderAppAt(path);
    await waitFor(() => expect(mockedDashboard).toHaveBeenCalled());
    expect(mockedLocations).not.toHaveBeenCalled();
    expect(mockedSelection).not.toHaveBeenCalled();
  });

  it("в настройках нет «Места работы», «Рабочие часы» есть", async () => {
    renderAppAt("/solo/settings");
    expect(await screen.findByRole("button", { name: "Рабочие часы" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Место работы" })).toBeNull();
  });
});

describe.each([
  ["solo", "solo" as const],
  ["null — не знаю", null],
  ["поле отсутствует (старый бэкенд)", undefined],
])("%s — как прежде (DRF-2254)", (_label, kind) => {
  beforeEach(() => {
    mockedGetMe.mockResolvedValue({ ...SOLO_ME, workspace_kind: kind });
  });

  it("«Услуги» в листе аватара есть", async () => {
    renderAppAt("/solo/my-day");
    expect(await avatarSheetNames()).toContain(AVATAR_SHEET_COPY.services);
  });

  it("экран «Место работы» открывается и спрашивает каталог", async () => {
    renderAppAt("/solo/place");
    await waitFor(() => expect(mockedLocations).toHaveBeenCalled());
  });

  it("в настройках есть «Место работы»", async () => {
    renderAppAt("/solo/settings");
    expect(await screen.findByRole("button", { name: "Место работы" })).toBeInTheDocument();
  });
});

describe("состав соло-листа при selfService=false (DRF-2254)", () => {
  it("без «Услуг», порядок прежний", () => {
    expect(
      masterAvatarSheetItems({ surface: "solo", salonAdmin: true, selfService: false }).map((i) => i.key),
    ).toEqual(["profile", "customers", "reviews", "salon", "settings"]);
  });
});
