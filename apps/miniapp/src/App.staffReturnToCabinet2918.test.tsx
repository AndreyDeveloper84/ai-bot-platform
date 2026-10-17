/**
 * DRF-2918 — «Вернуться в кабинет»: выход сотрудника с клиентского экрана.
 *
 * Продолжение DRF-2687. Кнопка из клиентского чата приводит сотрудника на
 * клиентский экран Mini App, а обратно в кабинет пути оттуда не было: «Сменить
 * режим» — только у многоролевых и только в профиле. Решение 08.10: кнопка
 * нужна, подпись — «Вернуться в кабинет».
 *
 * Доказательство «кабинет открыт» — адрес и запрос данных кабинета, а не
 * подпись панели: «Основная навигация» одна на пять панелей. Запреты («полосы
 * нет», «обратно не унесло») стоят после положительной пары и
 * `settleScenario`.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { settleScenario } from "./test/settleScenario";

vi.mock("./lib/identity", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/identity")>();
  return { ...original, channelIdentity: () => "identified" as const };
});

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return {
    ...original,
    getMe: vi.fn(),
    getSalonDay: vi.fn(),
    listMasters: vi.fn(),
    getAvailabilityRequests: vi.fn(),
    getHandoffQueue: vi.fn(),
    getSalonReadiness: vi.fn(),
  };
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
    getMasterProfileCard: vi.fn(),
    getPortfolio: vi.fn(),
  };
});

vi.mock("./lib/internal-chat-api", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("./lib/internal-chat-api")>();
  const empty = async () => ({
    items: [],
    total_count: 0,
    offset: 0,
    limit: 50,
  });
  return {
    ...original,
    listMasterThreads: vi.fn(empty),
    listAdminThreads: vi.fn(empty),
  };
});

vi.mock("./lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/food-scanner")>();
  return { ...original, fetchDiaryConsentGate: vi.fn(), grantConsent: vi.fn() };
});

vi.mock("./lib/customer-goals", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("./lib/customer-goals")>();
  return { ...original, fetchDecisionContext: vi.fn() };
});

vi.mock("./lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/max-sdk")>();
  return { ...original, getStartPayload: vi.fn() };
});

import {
  getAvailabilityRequests,
  getHandoffQueue,
  getMe,
  getSalonDay,
  getSalonReadiness,
  listMasters,
  type MeResponse,
} from "./lib/admin-api";
import { fetchDecisionContext } from "./lib/customer-goals";
import { fetchDiaryConsentGate } from "./lib/food-scanner";
import {
  getAylaHistory,
  getDashboard,
  getMasterMe,
  getMasterProfileCard,
  getMasterSchedule,
  getPendingAvailability,
  getPortfolio,
} from "./lib/master-api";
import { formatYmdLocal } from "./lib/masterDateFormat";
import { getStartPayload } from "./lib/max-sdk";
import {
  CABINET_RETURN_LABEL,
  SURFACE_SWITCH_LABEL,
} from "./components/SurfaceSwitch";
import { readLastSurface, writeLastSurface } from "./state/surface";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDay = vi.mocked(getSalonDay);
const mockedDashboard = vi.mocked(getDashboard);

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
};
const OWNER_ME: MeResponse = { ...CUSTOMER_ME, role: "owner", is_owner: true };
const RECEPTION_ME: MeResponse = {
  ...CUSTOMER_ME,
  role: "receptionist",
  is_receptionist: true,
};
/** Владелец, который сам принимает клиентов; команда, не соло. */
const OWNER_MASTER_ME: MeResponse = {
  ...MASTER_ME,
  role: "owner",
  is_owner: true,
};
/** Соло всегда многоролевой: владелец + админ + карточка мастера. */
const SOLO_ME: MeResponse = {
  ...OWNER_MASTER_ME,
  is_admin: true,
  is_solo_provider: true,
};

const CONSENT_SCREEN = "/customer/food-scanner/capture";

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

const pending = <T,>() => new Promise<T>(() => {});

const returnButton = () =>
  screen.queryByRole("button", { name: CABINET_RETURN_LABEL });

/**
 * Первый кадр дерева — холодная загрузка ленивых экранов: под нагрузкой
 * соседних файлов она не укладывается в секунду по умолчанию (прецедент —
 * `App.errorNeverHidesNav2198.test.tsx`). Это потолок ожидания, не пауза.
 */
const COLD = { timeout: 4000 };

/** Кнопка чата привела на экран согласия сканера еды — живой случай 30.09. */
async function landOnTheConsentScreen() {
  expect(
    await screen.findByRole("button", { name: /разреш/i }, COLD),
  ).toBeInTheDocument();
  expect(screen.getByTestId("path")).toHaveTextContent(CONSENT_SCREEN);
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  vi.mocked(getStartPayload).mockReturnValue("open_food_scan");
  vi.mocked(fetchDiaryConsentGate).mockResolvedValue({
    canonical: false,
    grantedAt: null,
    currentDocumentVersion: "",
  });
  // День — по часам устройства, не литералом (сторож
  // scheduleFixturesFollowClock): экраны сами выбирают «сегодня».
  const today = formatYmdLocal(new Date());
  vi.mocked(getMasterSchedule).mockResolvedValue({
    tenant_tz: "Europe/Moscow",
    from: today,
    to: today,
    days: [],
  });
  vi.mocked(getPendingAvailability).mockResolvedValue({ items: [] });
  // Данные кабинета узлам не нужны — важен сам факт запроса.
  mockedDashboard.mockReturnValue(pending());
  vi.mocked(getAylaHistory).mockReturnValue(pending());
  vi.mocked(getMasterMe).mockReturnValue(pending());
  vi.mocked(getMasterProfileCard).mockReturnValue(pending());
  vi.mocked(getPortfolio).mockReturnValue(pending());
  mockedDay.mockResolvedValue({
    date: today,
    timezone: "Europe/Moscow",
    summary: { total: 0, upcoming: 0, completed: 0, released: 0 },
    masters: [],
    orphan_visits: [],
  });
  vi.mocked(getSalonReadiness).mockResolvedValue({
    ready: true,
    unknown: false,
    source_problem: null,
    checked_at: "2026-09-20T09:00:00+00:00",
    masters_total: 1,
    problems: [],
    limits: [],
  });
  vi.mocked(listMasters).mockResolvedValue({
    items: [],
    next_cursor: null,
    total_count: 0,
  });
  vi.mocked(getAvailabilityRequests).mockResolvedValue({
    items: [],
    next_cursor: null,
  });
  vi.mocked(getHandoffQueue).mockResolvedValue({ waiting: 0, rows: [] });
  vi.mocked(fetchDecisionContext).mockReturnValue(pending());
});

describe("сотрудник на клиентском экране по кнопке из чата", () => {
  it.each([
    ["мастер", MASTER_ME, "/master/dashboard", mockedDashboard, false],
    [
      "мастер под StrictMode",
      MASTER_ME,
      "/master/dashboard",
      mockedDashboard,
      true,
    ],
    [
      "владелец без карточки мастера",
      OWNER_ME,
      "/admin/today",
      mockedDay,
      false,
    ],
    ["ресепшн", RECEPTION_ME, "/admin/day", mockedDay, false],
    ["соло-мастер", SOLO_ME, "/solo/my-day", mockedDashboard, false],
    [
      "соло-мастер под StrictMode",
      SOLO_ME,
      "/solo/my-day",
      mockedDashboard,
      true,
    ],
  ])(
    "%s: «Вернуться в кабинет» открывает кабинет",
    async (_who, me, cabinet, cabinetData, strict) => {
      mockedGetMe.mockResolvedValue(me);
      renderApp({ strict });
      await landOnTheConsentScreen();
      expect(cabinetData).not.toHaveBeenCalled();

      await userEvent.click(
        screen.getByRole("button", { name: CABINET_RETURN_LABEL }),
      );

      await waitFor(
        () => expect(screen.getByTestId("path")).toHaveTextContent(cabinet),
        COLD,
      );
      await waitFor(() => expect(cabinetData).toHaveBeenCalled(), COLD);
      // Стартовый payload обратно на клиентский экран не уносит, а в кабинете
      // полосе делать нечего.
      await settleScenario();
      expect(screen.getByTestId("path")).toHaveTextContent(cabinet);
      expect(returnButton()).not.toBeInTheDocument();
    },
  );

  it("владелец-мастер без выбранного режима попадает на выбор режима", async () => {
    mockedGetMe.mockResolvedValue(OWNER_MASTER_ME);
    renderApp();
    await landOnTheConsentScreen();

    await userEvent.click(
      screen.getByRole("button", { name: CABINET_RETURN_LABEL }),
    );

    expect(
      await screen.findByText(/несколько режимов/, {}, COLD),
    ).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/");
  });

  it("владелец-мастер с режимом «Салон» попадает в салон", async () => {
    mockedGetMe.mockResolvedValue(OWNER_MASTER_ME);
    writeLastSurface("admin");
    renderApp();
    await landOnTheConsentScreen();

    await userEvent.click(
      screen.getByRole("button", { name: CABINET_RETURN_LABEL }),
    );

    await waitFor(
      () =>
        expect(screen.getByTestId("path")).toHaveTextContent("/admin/today"),
      COLD,
    );
    await waitFor(() => expect(mockedDay).toHaveBeenCalled(), COLD);
  });

  it("полоса стоит и на других клиентских экранах, не только на согласии", async () => {
    mockedGetMe.mockResolvedValue(MASTER_ME);
    vi.mocked(getStartPayload).mockReturnValue("");
    render(
      <MemoryRouter initialEntries={["/customer/main"]}>
        <App />
        <PathProbe />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("button", { name: CABINET_RETURN_LABEL }, COLD),
    ).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/customer/main");
    // Клиентская Главная смонтирована под полосой: её нижняя панель на месте.
    expect(
      screen.getAllByRole("button", { name: "Профиль" }).length,
    ).toBeGreaterThan(0);
  });

  it("кнопка — разовое намерение: сохранённый режим не меняется", async () => {
    mockedGetMe.mockResolvedValue(OWNER_MASTER_ME);
    writeLastSurface("admin");
    renderApp();
    await landOnTheConsentScreen();

    await userEvent.click(
      screen.getByRole("button", { name: CABINET_RETURN_LABEL }),
    );

    await waitFor(() => expect(mockedDay).toHaveBeenCalled(), COLD);
    await settleScenario();
    expect(readLastSurface()).toBe("admin");
  });
});

describe("кому полоса не показывается", () => {
  it("обычному клиенту", async () => {
    mockedGetMe.mockResolvedValue(CUSTOMER_ME);
    renderApp();
    await landOnTheConsentScreen();
    await settleScenario();

    expect(returnButton()).not.toBeInTheDocument();
  });

  it("многоролевому в режиме «Клиент»: его выход — «Сменить режим» в профиле", async () => {
    mockedGetMe.mockResolvedValue(OWNER_MASTER_ME);
    writeLastSurface("customer");
    vi.mocked(getStartPayload).mockReturnValue("");
    render(
      <MemoryRouter initialEntries={["/customer/profile"]}>
        <App />
        <PathProbe />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("button", { name: SURFACE_SWITCH_LABEL }, COLD),
    ).toBeInTheDocument();
    await settleScenario();
    expect(returnButton()).not.toBeInTheDocument();
    expect(readLastSurface()).toBe("customer");
  });

  it("сотруднику в самом кабинете", async () => {
    mockedGetMe.mockResolvedValue(MASTER_ME);
    vi.mocked(getStartPayload).mockReturnValue("");
    renderApp();

    await waitFor(() => expect(mockedDashboard).toHaveBeenCalled(), COLD);
    await settleScenario();
    expect(returnButton()).not.toBeInTheDocument();
  });
});
