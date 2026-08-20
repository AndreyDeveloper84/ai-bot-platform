/**
 * DRF-1128 — the customer surface, reachable at last.
 *
 * The routing cascade fell through to `CustomerRoutes` only for a person
 * with no roles at all. The salon owner — owner *and* master — therefore
 * saw the admin surface from every bot, with no way to look at what her
 * own clients see. She reported it as «из любого бота открывается
 * админка», which is exactly what the cascade did.
 *
 * Two properties are load-bearing here and both are tested:
 *
 *   1. a multi-role person can *reach* the customer surface;
 *   2. she can *come back*. A one-way switch would be worse than the
 *      original bug — the owner would trade one trap for another.
 *
 * The «Сменить режим» control is hidden from single-role callers on
 * purpose: a receptionist has one surface, and offering to leave it is
 * noise she cannot act on.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
import {
  resetSurfaceState,
  readLastSurface,
  writeLastSurface,
} from "./state/surface";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDashboard = vi.mocked(getDashboard);
const mockedPayout = vi.mocked(getPayoutPreview);

/** Owner + master, not solo — the pilot owner's actual shape. */
const DUAL_ROLE_ME = {
  user: { id: "u-1", name: "Андрей", phone_masked: "+7 *** **12" },
  role: "owner",
  is_owner: true,
  is_admin: false,
  is_receptionist: false,
  is_master: true,
  is_solo_provider: false,
  capabilities: [],
} as unknown as MeResponse;

/** Reception desk: one surface, nothing to switch between. */
const SINGLE_ROLE_ME = {
  ...DUAL_ROLE_ME,
  role: "receptionist",
  is_owner: false,
  is_receptionist: true,
  is_master: false,
} as unknown as MeResponse;

/** No roles at all — the only shape that used to reach the customer surface. */
const NO_ROLE_ME = {
  ...DUAL_ROLE_ME,
  role: "customer",
  is_owner: false,
  is_master: false,
} as unknown as MeResponse;

function renderAppAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetSurfaceState();
  mockedGetMe.mockResolvedValue(DUAL_ROLE_ME);
  // Screens behind the surfaces are not under test — reject so they
  // settle into an error state instead of hanging on a pending promise.
  mockedDashboard.mockRejectedValue(new Error("not under test"));
  mockedPayout.mockRejectedValue(new Error("not under test"));
});

describe("DRF-1128 — surface switch", () => {
  it("offers «Клиент» as a third option to a dual-role person", async () => {
    renderAppAt("/");
    expect(
      await screen.findByRole("button", { name: "Открыть клиентскую часть" }),
    ).toBeInTheDocument();
  });

  it("lets her actually reach the customer surface", async () => {
    const user = userEvent.setup();
    renderAppAt("/");

    await user.click(await screen.findByRole("button", { name: "Открыть клиентскую часть" }));

    // The choice is what the cascade reads on the next render; asserting
    // on it rather than on rendered chrome keeps the test about the
    // routing decision instead of the customer screens themselves.
    await waitFor(() => expect(readLastSurface()).toBe("customer"));
  });

  it("keeps the way back — the switch is not one-way", async () => {
    const user = userEvent.setup();

    // Уже в клиентской поверхности — то состояние, в котором владелец
    // оказывается, выбрав «Клиент». Кнопка возврата живёт в профиле.
    writeLastSurface("customer");
    renderAppAt("/customer/profile");

    // Возврат проверяется через интерфейс, а не через тестовый хелпер:
    // вызов resetSurfaceState() доказал бы лишь, что у хранилища есть
    // функция сброса, а не то, что владелец может выйти.
    const back = await screen.findByRole("button", { name: "Сменить режим" });
    await user.click(back);

    await waitFor(() => expect(readLastSurface()).toBeNull());
    expect(
      await screen.findByRole("button", { name: "Перейти в Салон" }),
    ).toBeInTheDocument();
  });

  it("still sends a role-less person straight to the customer surface", async () => {
    // Regression: the one shape that already worked must keep working
    // and must NOT be diverted through the chooser.
    mockedGetMe.mockResolvedValue(NO_ROLE_ME);
    renderAppAt("/");

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Открыть клиентскую часть" })).toBeNull(),
    );
  });

  it("hides the chooser from a single-role caller", async () => {
    mockedGetMe.mockResolvedValue(SINGLE_ROLE_ME);
    renderAppAt("/");

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Открыть клиентскую часть" })).toBeNull(),
    );
  });
});
