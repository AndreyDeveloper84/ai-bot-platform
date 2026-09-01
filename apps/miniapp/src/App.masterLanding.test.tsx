/**
 * DRF-1434 — куда попадает мастер после онбординга.
 *
 * Живой прогон на пилоте: Иван прошёл приглашение целиком и на пятом
 * экране увидел клиентское «Здравствуйте, Иван! Помогу записаться в
 * студию…» внутри бота для мастеров. Расписания он не увидел.
 *
 * Механика: приглашённый грузится с `is_master: false` (роль выдаётся
 * только на ACCEPTED + linked_bot_user, см. role_resolver.py), поэтому
 * `App` монтирует `CustomerRoutes`. `MasterOnboardingScreen` после
 * accept делает `navigate("/master/dashboard")`, но `boot.me` всё ещё
 * старый — дерево не переключилось, `/master/*` в `CustomerRoutes` не
 * объявлен, и его `<Route path="*" element={<HelloScreen />} />`
 * рисует клиентское приветствие. Ошибки нет нигде.
 *
 * Тесты держат ОБЕ стороны:
 *   1. мастер после онбординга видит поверхность мастера;
 *   2. клиент по-прежнему видит клиентскую;
 *   3. неподтверждённая роль на `/master/*` говорит об этом вслух,
 *      а не притворяется клиентским экраном.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return { ...original, getMe: vi.fn() };
});

vi.mock("./lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/master-api")>();
  return {
    ...original,
    claimInvite: vi.fn(),
    acceptInvite: vi.fn(),
    patchOnboardingProfile: vi.fn(),
    getDashboard: vi.fn(),
  };
});

vi.mock("./lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/api")>();
  return { ...original, authVerify: vi.fn() };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import {
  acceptInvite,
  claimInvite,
  getDashboard,
  patchOnboardingProfile,
  type ClaimResponse,
  type DashboardResponse,
} from "./lib/master-api";
import { authVerify } from "./lib/api";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedClaim = vi.mocked(claimInvite);
const mockedAccept = vi.mocked(acceptInvite);
const mockedPatch = vi.mocked(patchOnboardingProfile);
const mockedDashboard = vi.mocked(getDashboard);
const mockedAuthVerify = vi.mocked(authVerify);

/** Приглашённый: CatalogMaster ещё PENDING → `is_master: false`. */
const INVITEE_ME: MeResponse = {
  user: { id: "u-1", name: "Иван", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula" },
  role: "customer",
  capabilities: [],
  is_customer: true,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: false,
  master_id: null,
  landing_path: "/",
  is_solo_provider: false,
};

/** Тот же человек после accept — бэкенд уже отдаёт роль мастера. */
const ACCEPTED_MASTER_ME: MeResponse = {
  ...INVITEE_ME,
  role: "master",
  is_master: true,
  master_id: "m-1",
  landing_path: "/master/dashboard",
};

/** Обычный клиент — вторая, положительная сторона стража. */
const CUSTOMER_ME: MeResponse = { ...INVITEE_ME, user: { id: "u-2", name: "Ольга", phone_masked: "+7 *** **34" } };

const CLAIM: ClaimResponse = {
  master: {
    id: "m-1",
    name: "Я тест",
    specialization: "",
    bio: "",
    photo_url: "",
    services: [],
    working_hours_summary: "",
  },
  salon: { tenant_id: "t-1", name: "Формула тела" },
  max_user: { first_name: "Иван", phone_masked: "+7 *** **12", max_handle: "" },
};

const DASHBOARD: DashboardResponse = {
  master: { id: "m-1", name: "Иван", specialization: "Массаж", photo_url: "" },
  salon: { id: "t-1", name: "Формула тела" },
  now_iso: "2026-09-01T09:00:00+03:00",
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
};

function renderAppAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

/** Клиентское приветствие — то, что Иван увидел вместо расписания. */
const CLIENT_GREETING = /Помогу записаться в студию/;

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mockedClaim.mockResolvedValue(CLAIM);
  mockedAccept.mockResolvedValue({
    master_id: "m-1",
    session_token: "s-1",
    expires_at: "2026-09-02T09:00:00+03:00",
  });
  mockedPatch.mockResolvedValue({
    master: { id: "m-1", name: "Иван", bio: "", photo_url: "" },
  });
  mockedDashboard.mockResolvedValue(DASHBOARD);
  mockedAuthVerify.mockResolvedValue({
    user: { id: "u-1", client_name: "Иван", display_name: "Иван" },
    tenant: { id: "t-1", name: "Формула тела", slug: "formula" },
  } as never);
});

describe("DRF-1434 — приземление после онбординга мастера", () => {
  it("мастер после «Сохранить и продолжить» видит поверхность мастера", async () => {
    const user = userEvent.setup();
    // Первый /me — приглашённый (роли ещё нет); после accept бэкенд
    // отдаёт роль мастера.
    mockedGetMe
      .mockResolvedValueOnce(INVITEE_ME)
      .mockResolvedValue(ACCEPTED_MASTER_ME);

    renderAppAt("/onboarding/master?token=t-1");

    await user.click(await screen.findByRole("button", { name: "Это я, продолжить" }));
    await user.click(await screen.findByRole("button", { name: "Понятно" }));
    await user.click(
      await screen.findByRole("button", { name: "Сохранить и продолжить" }),
    );

    // Поверхность мастера — секция «СЕЙЧАС» дашборда.
    expect(await screen.findByText("СЕЙЧАС")).toBeInTheDocument();
    // И ровно то, чего быть не должно: клиентское приветствие.
    expect(screen.queryByText(CLIENT_GREETING)).not.toBeInTheDocument();
  });

  it("клиент по-прежнему видит клиентское приветствие на «/»", async () => {
    mockedGetMe.mockResolvedValue(CUSTOMER_ME);
    renderAppAt("/");
    expect(await screen.findByText(CLIENT_GREETING)).toBeInTheDocument();
  });

  it("неподтверждённая роль на /master/* говорит об этом, а не рисует клиентский экран", async () => {
    mockedGetMe.mockResolvedValue(INVITEE_ME);
    renderAppAt("/master/dashboard");
    expect(
      await screen.findByRole("heading", { name: /Доступ мастера ещё не подтверждён/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText(CLIENT_GREETING)).not.toBeInTheDocument();
  });
});
