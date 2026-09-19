/**
 * DRF-1149 safety net — the «Салон» entry in the solo «Ещё» sheet.
 *
 * The solo surface mounts every `/admin/*` route but surfaced none of
 * them, so the only way into the admin screens was typing a URL. When
 * the pilot salon was mis-counted as solo, that left an owner with four
 * masters stranded on a surface with no way out. The counting bug is
 * fixed in `is_solo_provider`; this item is the second lock on the same
 * door, and these tests are what keep it there.
 *
 * The sheet is opened by deep-linking to `/solo/more`, which
 * `UnifiedSoloSurface` reads on mount.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

// DRF-1893 (15.09.2026 UTC): App не стартует без initData (решение владельца U —
// пустой initData это отказ транспорта, экран «Открой Ayla из MAX»). Эти
// тесты — про запуск из MAX, поэтому канал объявлен опознанным явно: в jsdom
// моста MAX нет, и без этой строки App честно показал бы экран отказа.
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
  return { ...original, getDashboard: vi.fn() };
});

vi.mock("./lib/master-billing", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/master-billing")>();
  return { ...original, getPayoutPreview: vi.fn() };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import { getDashboard } from "./lib/master-api";
import { getPayoutPreview } from "./lib/master-billing";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDashboard = vi.mocked(getDashboard);
const mockedPayout = vi.mocked(getPayoutPreview);

/** Solo owner who is also her own master — the shape that renders the solo surface. */
const SOLO_OWNER_ME: MeResponse = {
  user: { id: "u-1", name: "Ольга", phone_masked: "+7 *** **12" },
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

/** Same surface, no admin-side role — «Салон» must not appear. */
const SOLO_MASTER_ONLY_ME: MeResponse = {
  ...SOLO_OWNER_ME,
  role: "master",
  is_owner: false,
  is_admin: false,
  is_receptionist: false,
};

function renderAppAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedGetMe.mockResolvedValue(SOLO_OWNER_ME);
  // The dashboard behind the sheet is irrelevant here — reject so the
  // screen settles into its error state instead of hanging on a pending
  // promise. The sheet renders regardless.
  // DRF-2127: аватар живёт в шапке дашборда — ему нужны данные мастера.
  mockedDashboard.mockResolvedValue({
    master: { id: "m-1", name: "Ольга", specialization: "", photo_url: "" },
    salon: { id: "t-1", name: "Demo" },
    now_iso: "2026-09-19T09:00:00+03:00",
    active_visit: null,
    next_visit: null,
    inbox_preview: [],
    today_summary: { total_clients_today: 0, completed_count: 0, next_free_window: null },
    tab_badges: {
      conversations_unread: 0,
      schedule_has_pending_change: false,
      profile_has_owner_pending_change: false,
    },
    states: { is_day_done: false, is_offline_safe_response: false },
    week_summary: { week_start: "2026-09-14", week_end: "2026-09-20", bookings: 0, completed: 0, rating: null },
  });
  mockedPayout.mockRejectedValue(new Error("not under test"));
});

describe("solo surface — admin escape hatch (DRF-1149 → лист аватара, DRF-2127)", () => {
  // Лист «Ещё» снят вместе с пятивкладочной панелью (§28/§50); правило
  // DRF-1149 живёт в листе аватара: «Управление салоном» — при владельческой
  // роли поверх соло-профиля.
  async function openAvatarSheet() {
    await userEvent.click(await screen.findByRole("button", { name: "Меню профиля" }));
    return screen.findByRole("dialog", { name: "Меню" });
  }

  it("shows «Управление салоном» in the avatar sheet for an owner", async () => {
    renderAppAt("/solo/my-day");
    const sheet = await openAvatarSheet();
    expect(
      within(sheet).getByRole("button", { name: "Управление салоном" }),
    ).toBeInTheDocument();
  });

  it("keeps the solo base items alongside it", async () => {
    renderAppAt("/solo/my-day");
    const sheet = await openAvatarSheet();
    for (const label of ["Профиль", "Клиенты", "Услуги", "Отзывы", "Настройки"]) {
      expect(within(sheet).getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("hides «Управление салоном» from a master-only caller", async () => {
    mockedGetMe.mockResolvedValue(SOLO_MASTER_ONLY_ME);
    renderAppAt("/solo/my-day");
    const sheet = await openAvatarSheet();
    expect(within(sheet).getByRole("button", { name: "Профиль" })).toBeInTheDocument();
    expect(
      within(sheet).queryByRole("button", { name: "Управление салоном" }),
    ).not.toBeInTheDocument();
  });
});
