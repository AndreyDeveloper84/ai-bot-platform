/**
 * DRF-2687 — клиентская кнопка из чата открывает клиентский экран и у
 * сотрудника салона.
 *
 * Живой проход 30.09: мастер надиктовал боту еду, бот ответил «Чтобы
 * записать еду, нужно разрешить дневник питания» с кнопкой «Открыть и
 * разрешить» (payload `open_food_scan` → `/customer/food-scanner/capture`).
 * Mini App открылся на главной мастера: каскад ролей выбрал мастерское
 * дерево, `/customer/*` в нём нет, catch-all увёл на `/master/dashboard`.
 * Согласие дать было негде.
 *
 * Узлы держат обе стороны: сотрудник по клиентской кнопке попадает на
 * клиентский экран, а без кнопки и по своей кнопке — в кабинет, как раньше.
 * Вариант под `StrictMode` обязателен: приложение обёрнуто в него
 * (`main.tsx`), и именно там порядок эффектов отдавал победу catch-all.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
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
  return { ...original, getDashboard: vi.fn() };
});

vi.mock("./lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/food-scanner")>();
  return { ...original, fetchDiaryConsentGate: vi.fn(), grantConsent: vi.fn() };
});

vi.mock("./lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/max-sdk")>();
  return { ...original, getStartPayload: vi.fn() };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import { fetchDiaryConsentGate } from "./lib/food-scanner";
import { getDashboard } from "./lib/master-api";
import { getStartPayload } from "./lib/max-sdk";
import { readLastSurface, writeLastSurface } from "./state/surface";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedGate = vi.mocked(fetchDiaryConsentGate);
const mockedDashboard = vi.mocked(getDashboard);
const mockedPayload = vi.mocked(getStartPayload);

const CUSTOMER_ME: MeResponse = {
  user: { id: "u-1", name: "Ольга", phone_masked: "+7 *** **34" },
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

const MASTER_ME: MeResponse = {
  ...CUSTOMER_ME,
  role: "master",
  is_master: true,
  master_id: "m-1",
  landing_path: "/master/dashboard",
};

const OWNER_ME: MeResponse = { ...CUSTOMER_ME, role: "owner", is_owner: true };

const OWNER_MASTER_ME: MeResponse = { ...MASTER_ME, role: "owner", is_owner: true };

const SCANNER = "/customer/food-scanner/capture";

function PathProbe() {
  return <output data-testid="path">{useLocation().pathname}</output>;
}

function renderApp({ strict = false }: { strict?: boolean } = {}) {
  const tree = (
    <MemoryRouter initialEntries={["/"]}>
      <App />
      <PathProbe />
    </MemoryRouter>
  );
  render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}

async function expectConsentScreen() {
  // Экран согласия дневника: кнопка «Разрешить» на адресе сканера.
  expect(await screen.findByRole("button", { name: /разреш/i })).toBeInTheDocument();
  expect(screen.getByTestId("path")).toHaveTextContent(SCANNER);
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mockedPayload.mockReturnValue("");
  mockedGate.mockResolvedValue({ canonical: false, grantedAt: null, currentDocumentVersion: "" });
  // Дашборд мастера, если он всё-таки смонтируется, остаётся в загрузке:
  // узлам важен сам факт запроса, а не его содержимое.
  mockedDashboard.mockReturnValue(new Promise(() => {}));
});

describe("сотрудник по клиентской кнопке из чата", () => {
  it.each([
    ["мастер", MASTER_ME],
    ["владелец", OWNER_ME],
    ["владелец и мастер", OWNER_MASTER_ME],
  ])("%s с open_food_scan видит экран согласия дневника", async (_label, me) => {
    mockedGetMe.mockResolvedValue(me);
    mockedPayload.mockReturnValue("open_food_scan");

    renderApp();

    await expectConsentScreen();
    // Кабинет не монтировался вовсе — не «мелькнул и ушёл».
    expect(mockedGate).toHaveBeenCalled();
    expect(mockedDashboard).not.toHaveBeenCalled();
  });

  it("под StrictMode мастер тоже попадает на экран согласия", async () => {
    mockedGetMe.mockResolvedValue(MASTER_ME);
    mockedPayload.mockReturnValue("open_food_scan");

    renderApp({ strict: true });

    await expectConsentScreen();
    expect(mockedGate).toHaveBeenCalled();
    expect(mockedDashboard).not.toHaveBeenCalled();
  });

  it("кнопка не переписывает сохранённый режим многоролевого", async () => {
    mockedGetMe.mockResolvedValue(OWNER_MASTER_ME);
    mockedPayload.mockReturnValue("open_food_scan");
    writeLastSurface("admin");

    renderApp();

    await expectConsentScreen();
    expect(readLastSurface()).toBe("admin");
  });
});

describe("кабинет сотрудника — как раньше", () => {
  it("мастер без payload открывает свой дашборд", async () => {
    mockedGetMe.mockResolvedValue(MASTER_ME);

    renderApp();

    await waitFor(() => expect(mockedDashboard).toHaveBeenCalled());
    expect(screen.getByTestId("path")).toHaveTextContent("/master/dashboard");
    expect(mockedGate).not.toHaveBeenCalled();
  });

  it("мастер по своей кнопке попадает в свой раздел, не на клиентский экран", async () => {
    mockedGetMe.mockResolvedValue(MASTER_ME);
    mockedPayload.mockReturnValue("open_master_schedule");

    renderApp();

    await waitFor(() =>
      expect(screen.getByTestId("path")).toHaveTextContent("/master/schedule"),
    );
    expect(mockedGate).not.toHaveBeenCalled();
  });
});

describe("клиент — без изменений", () => {
  it("клиент с open_food_scan видит экран согласия", async () => {
    mockedGetMe.mockResolvedValue(CUSTOMER_ME);
    mockedPayload.mockReturnValue("open_food_scan");

    renderApp();

    await expectConsentScreen();
  });
});
