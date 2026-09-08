/**
 * Home routing proof (DRF-1546).
 *
 * `/customer/main` = домашний экран (wellness dashboard). Решение
 * владельца §24.2 + §34: старший канон клиентской поверхности —
 * `docs/screens/customer-main-wellness-dashboard.md`, где этот экран
 * объявлен P0 BLOCKER пилота. До этой правки «Главная» рендерила
 * список записей, а сам экран стоял за `STUB_SURFACES_ENABLED`.
 *
 * Стража парная (DRF-1411): каждому «главная больше не список записей»
 * отвечает «а записи по-прежнему открываются» — правка, которая просто
 * снесла бы записи, прошла бы первую проверку и упала на второй.
 *
 * App boots through `getMe()` (role resolution) — mocked to a plain
 * customer. Данные домашнего экрана и записей замоканы на уровне
 * библиотек: этот файл про МАРШРУТИЗАЦИЮ, и поднимать под неё сеть
 * незачем.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return { ...original, getMe: vi.fn() };
});

// Домашний экран теперь спрашивает decision-context (приглашение в
// анкету цели). Мокаем, чтобы юнит-тест не ходил в сеть; отсутствие
// missing = приглашение не рисуется и на эти проверки не влияет.
vi.mock("./lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/customer-goals")>();
  return {
    ...original,
    fetchDecisionContext: vi.fn().mockResolvedValue({
      version: 1,
      known: { goal: null },
      missing: [],
      suggestions: [],
      intents: [],
    }),
  };
});

vi.mock("./lib/customer-wellness", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("./lib/customer-wellness")>();
  return {
    ...original,
    getWellnessToday: vi.fn(),
    getRecentActivity: vi.fn(),
  };
});

vi.mock("./lib/customer-booking", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("./lib/customer-booking")>();
  return { ...original, getCatalogBrowse: vi.fn() };
});

vi.mock("./lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/api")>();
  return {
    ...original,
    fetchMyBookings: vi.fn(),
    fetchServices: vi.fn(),
    fetchMasters: vi.fn(),
    fetchRecommendations: vi.fn(),
  };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import { getCatalogBrowse } from "./lib/customer-booking";
import { getRecentActivity, getWellnessToday } from "./lib/customer-wellness";
import {
  fetchMasters,
  fetchMyBookings,
  fetchRecommendations,
  fetchServices,
} from "./lib/api";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedList = vi.mocked(fetchMyBookings);
const mockedServices = vi.mocked(fetchServices);
const mockedMasters = vi.mocked(fetchMasters);
const mockedRecs = vi.mocked(fetchRecommendations);
const mockedToday = vi.mocked(getWellnessToday);
const mockedActivity = vi.mocked(getRecentActivity);
const mockedBrowse = vi.mocked(getCatalogBrowse);

const CUSTOMER_ME: MeResponse = {
  user: { id: "u-1", name: "Ольга", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Demo", slug: "demo" },
  role: "customer",
  capabilities: [],
  is_customer: true,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: false,
  master_id: null,
  landing_path: "/customer/main",
  is_solo_provider: false,
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
  vi.unstubAllEnvs();
  mockedGetMe.mockResolvedValue(CUSTOMER_ME);
  mockedList.mockResolvedValue({ items: [], next_cursor: null });
  mockedServices.mockResolvedValue({ services: [] });
  mockedMasters.mockResolvedValue({ masters: [] });
  mockedRecs.mockResolvedValue({
    data: {
      decision_id: "dec-1",
      request_id: "req-1",
      resolver_spec_version: "1.0",
      policy_versions: {},
      ordered: [],
    },
  });
  mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [] });
  mockedToday.mockResolvedValue({
    calories_eaten: 1240,
    calories_target: 2100,
    water_glasses_eaten: 4,
    water_glasses_target: 8,
    active_goals: [],
    display_name: "Анна",
  });
  mockedActivity.mockResolvedValue({ this_week_booking_count: 0 });
});

describe("home routing (DRF-1546)", () => {
  it("/customer/main renders the home screen, not the records list", async () => {
    renderAppAt("/customer/main");

    // POSITIVE: домашний экран на месте, с данными из ручки.
    expect(await screen.findByText(/4 \/ 8 стаканов/)).toBeInTheDocument();
    expect(screen.getByText(/1240 \/ 2100 ккал/)).toBeInTheDocument();
    // NEGATIVE (парная): списка записей на главной больше нет.
    expect(screen.queryByText(/Пока записей нет/)).not.toBeInTheDocument();
  });

  it("home is NOT hidden behind the pilot placeholder in a prod build", async () => {
    // Гейт снят именно с главной. Собранный как прод бандл обязан
    // рисовать экран, а не «выдуманных данных не показываем».
    vi.stubEnv("DEV", false);
    try {
      renderAppAt("/customer/main");
      expect(await screen.findByText(/4 \/ 8 стаканов/)).toBeInTheDocument();
      expect(screen.queryByText(/выдуманных данных/)).not.toBeInTheDocument();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("/customer/records still renders the real records screen", async () => {
    // Парная положительная стража: записи никуда не делись, их просто
    // сняли с «Главной».
    renderAppAt("/customer/records");

    expect(await screen.findByText(/Пока записей нет/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Записи" })).toBeInTheDocument();
  });

  it("/customer/wellness stays mounted as the bot-slug alias", async () => {
    // `open_wellness` / `open_water_add_250` резолвятся сюда — это
    // договор с ботом, а не внутренняя ссылка.
    renderAppAt("/customer/wellness");

    expect(await screen.findByText(/4 \/ 8 стаканов/)).toBeInTheDocument();
  });
});
