/**
 * DRF-2818 — рабочая кнопка открывает рабочий экран и в режиме «Клиент».
 *
 * Зеркало DRF-2687. Владелец-мастер выбрал в Mini App режим «Клиент», а потом
 * нажал в салонном боте «Расписание» (payload `open_master_schedule`) — и
 * получил «Доступ мастера ещё не подтверждён»: каскад отдавал клиентское
 * дерево на любом адресе, а `/master/*` и `/admin/*` в нём — экран для того,
 * кому роль не выдана.
 *
 * Доказательство «рабочий экран смонтирован» — запрос его данных, а не
 * подпись панели: «Основная навигация» одна на пять панелей. Сохранённый
 * режим проверяется после `settleScenario`: эффект трекера идёт позже
 * первого кадра экрана.
 */
import { render, screen, waitFor } from "@testing-library/react";
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
  const original = await importOriginal<typeof import("./lib/internal-chat-api")>();
  const empty = async () => ({ items: [], total_count: 0, offset: 0, limit: 50 });
  return { ...original, listMasterThreads: vi.fn(empty), listAdminThreads: vi.fn(empty) };
});

vi.mock("./lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/food-scanner")>();
  return { ...original, fetchDiaryConsentGate: vi.fn(), grantConsent: vi.fn() };
});

vi.mock("./lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/customer-goals")>();
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
import { readLastSurface, writeLastSurface } from "./state/surface";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedPayload = vi.mocked(getStartPayload);
const mockedSchedule = vi.mocked(getMasterSchedule);
const mockedDay = vi.mocked(getSalonDay);
const mockedDashboard = vi.mocked(getDashboard);
const mockedDecision = vi.mocked(fetchDecisionContext);
const mockedGate = vi.mocked(fetchDiaryConsentGate);

/** Владелец, который сам принимает клиентов; команда, не соло. */
const OWNER_MASTER_ME: MeResponse = {
  user: { id: "u-1", name: "Ольга", phone_masked: "+7 *** **34" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula" },
  role: "owner",
  capabilities: [],
  is_customer: true,
  is_master: true,
  is_receptionist: false,
  is_admin: false,
  is_owner: true,
  master_id: "m-1",
  landing_path: "/admin/team",
  is_solo_provider: false,
};

const SOLO_ME: MeResponse = { ...OWNER_MASTER_ME, is_solo_provider: true };

const NOT_CONFIRMED = /ещё не подтверждён/;

function PathProbe() {
  return <output data-testid="path">{useLocation().pathname}</output>;
}

function renderApp({ at = "/", strict = false }: { at?: string; strict?: boolean } = {}) {
  const tree = (
    <MemoryRouter initialEntries={[at]}>
      <App />
      <PathProbe />
    </MemoryRouter>
  );
  render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}

const pending = <T,>() => new Promise<T>(() => {});

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mockedGetMe.mockResolvedValue(OWNER_MASTER_ME);
  mockedPayload.mockReturnValue("");
  // День — по часам устройства, не литералом (сторож
  // scheduleFixturesFollowClock): экраны сами выбирают «сегодня».
  const today = formatYmdLocal(new Date());
  mockedSchedule.mockResolvedValue({ tenant_tz: "Europe/Moscow", from: today, to: today, days: [] });
  vi.mocked(getPendingAvailability).mockResolvedValue({ items: [] });
  mockedDashboard.mockReturnValue(pending());
  // Остальные ручки кабинета мастера узлам не нужны — остаются в загрузке.
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
  vi.mocked(listMasters).mockResolvedValue({ items: [], next_cursor: null, total_count: 0 });
  vi.mocked(getAvailabilityRequests).mockResolvedValue({ items: [], next_cursor: null });
  vi.mocked(getHandoffQueue).mockResolvedValue({ waiting: 0, rows: [] });
  // Клиентская Главная, если смонтируется, остаётся в загрузке: узлам важен
  // сам факт её запроса.
  mockedDecision.mockReturnValue(pending());
  mockedGate.mockResolvedValue({ canonical: false, grantedAt: null, currentDocumentVersion: "" });
});

describe("владелец-мастер в режиме «Клиент» по рабочей кнопке", () => {
  it("«Расписание» открывает расписание мастера, а не «доступ не подтверждён»", async () => {
    writeLastSurface("customer");
    mockedPayload.mockReturnValue("open_master_schedule");

    renderApp();

    await waitFor(() => expect(mockedSchedule).toHaveBeenCalled());
    expect(screen.getByTestId("path")).toHaveTextContent("/master/schedule");
    expect(screen.queryByText(NOT_CONFIRMED)).not.toBeInTheDocument();
  });

  it("«Сегодня» салона открывает день салона", async () => {
    writeLastSurface("customer");
    mockedPayload.mockReturnValue("open_admin_today");

    renderApp();

    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    expect(screen.getByTestId("path")).toHaveTextContent("/admin/today");
    expect(screen.queryByText(NOT_CONFIRMED)).not.toBeInTheDocument();
  });

  it("то же под StrictMode", async () => {
    writeLastSurface("customer");
    mockedPayload.mockReturnValue("open_master_schedule");

    renderApp({ strict: true });

    await waitFor(() => expect(mockedSchedule).toHaveBeenCalled());
    expect(screen.getByTestId("path")).toHaveTextContent("/master/schedule");
    expect(screen.queryByText(NOT_CONFIRMED)).not.toBeInTheDocument();
  });

  it("кнопка — разовое намерение: сохранённый режим «Клиент» остаётся", async () => {
    writeLastSurface("customer");
    mockedPayload.mockReturnValue("open_master_schedule");

    renderApp();

    await waitFor(() => expect(mockedSchedule).toHaveBeenCalled());
    await settleScenario();
    expect(readLastSurface()).toBe("customer");
  });

  it("соло-мастер в режиме «Клиент» тоже попадает в расписание", async () => {
    mockedGetMe.mockResolvedValue(SOLO_ME);
    writeLastSurface("customer");
    mockedPayload.mockReturnValue("open_master_schedule");

    renderApp();

    await waitFor(() => expect(mockedSchedule).toHaveBeenCalled());
    expect(screen.queryByText(NOT_CONFIRMED)).not.toBeInTheDocument();
  });

  it("адрес соло-панели остаётся рабочей поверхностью", async () => {
    // Вкладки соло-панели ведут на `/solo/*`: без `/solo/` в предикате тап
    // по «Сегодня» выбрасывал бы соло-мастера на клиентскую Главную.
    mockedGetMe.mockResolvedValue(SOLO_ME);
    writeLastSurface("customer");

    renderApp({ at: "/solo/my-day" });

    await waitFor(() => expect(mockedDashboard).toHaveBeenCalled());
    expect(screen.getByTestId("path")).toHaveTextContent("/solo/my-day");
    expect(mockedDecision).not.toHaveBeenCalled();
  });
});

describe("режим «Клиент» — как раньше", () => {
  it("без кнопки открывается клиентская поверхность", async () => {
    writeLastSurface("customer");

    renderApp();

    await waitFor(() => expect(mockedDecision).toHaveBeenCalled());
    expect(screen.getByTestId("path")).toHaveTextContent("/");
    expect(mockedSchedule).not.toHaveBeenCalled();
    expect(mockedDay).not.toHaveBeenCalled();
  });

  it("клиентская кнопка открывает клиентский экран", async () => {
    writeLastSurface("customer");
    mockedPayload.mockReturnValue("open_food_scan");

    renderApp();

    expect(await screen.findByRole("button", { name: /разреш/i })).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/customer/food-scanner/capture");
  });

  it("несуществующий рабочий адрес возвращает на клиентскую поверхность", async () => {
    writeLastSurface("customer");

    renderApp({ at: "/master/typo" });

    await waitFor(() => expect(mockedDecision).toHaveBeenCalled());
    expect(screen.getByTestId("path")).toHaveTextContent("/");
    expect(screen.queryByText(NOT_CONFIRMED)).not.toBeInTheDocument();
  });
});

describe("трекер режима — как раньше для рабочих режимов", () => {
  it("с сохранённым «Салон» переход в кабинет мастера запоминается", async () => {
    writeLastSurface("admin");
    mockedPayload.mockReturnValue("open_master_schedule");

    renderApp();

    await waitFor(() => expect(mockedSchedule).toHaveBeenCalled());
    await waitFor(() => expect(readLastSurface()).toBe("master"));
  });
});
