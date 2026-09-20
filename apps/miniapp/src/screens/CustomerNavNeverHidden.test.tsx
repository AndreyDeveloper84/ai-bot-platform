/**
 * Правило класса на клиентской поверхности (DRF-2198, предел (ѣ) того PR):
 * состояние ошибки не убирает навигацию. У клиента это панель пяти вкладок
 * (`CustomerTabBar`, вынесена в общий компонент DRF-2191) и — у многоролевого
 * — «Сменить режим» на корне анкеты: единственный выход обратно к мастеру и
 * администратору (DRF-1469).
 *
 * Инцидент 20.09 был именно про это: на мастерской поверхности состояние
 * ошибки съело дверь к настройкам, и человек не смог выйти из аккаунта.
 * Здесь — положительная пара: у клиента выход переживает отказ ручек.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, getWellnessToday: vi.fn(), getRecentActivity: vi.fn() };
});
vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCatalogBrowse: vi.fn() };
});
vi.mock("../lib/plan-lite", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/plan-lite")>();
  return { ...original, getPlanLite: vi.fn() };
});
vi.mock("../lib/customer-last-topic", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-last-topic")>();
  return { ...original, getLastTopic: vi.fn() };
});
vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "test-init-data",
  setBackButton: () => undefined,
  onBackButton: () => () => undefined,
  closeApp: () => undefined,
  hapticSelection: () => undefined,
  hapticImpact: () => undefined,
  signalReady: () => undefined,
}));

import { getCatalogBrowse } from "../lib/customer-booking";
import { getLastTopic } from "../lib/customer-last-topic";
import { getRecentActivity, getWellnessToday } from "../lib/customer-wellness";
import { getPlanLite } from "../lib/plan-lite";
import { CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";

const TABS = ["Главная", "План", "Дневник", "Записи", "Профиль"] as const;

beforeEach(() => {
  vi.clearAllMocks();
  // Всё, чем живёт экран, отказало — ровно тот случай, когда человеку
  // особенно нужен выход.
  const boom = new Error("boom");
  vi.mocked(getWellnessToday).mockRejectedValue(boom);
  vi.mocked(getRecentActivity).mockRejectedValue(boom);
  vi.mocked(getCatalogBrowse).mockRejectedValue(boom);
  vi.mocked(getPlanLite).mockRejectedValue(boom);
  vi.mocked(getLastTopic).mockRejectedValue(boom);
});

describe("H01: панель клиента переживает отказ ручек дашборда", () => {
  it("пять вкладок на месте — по подписям, как их видит человек", async () => {
    render(
      <MemoryRouter initialEntries={["/customer/main"]}>
        <CustomerWellnessDashboardScreen />
      </MemoryRouter>,
    );
    const nav = await screen.findByRole("navigation", { name: "Основная навигация" }, { timeout: 4000 });
    for (const label of TABS) {
      expect(
        screen.getAllByRole("button", { name: label }).length,
        `вкладка «${label}» пропала при отказе ручек`,
      ).toBeGreaterThan(0);
    }
    expect(nav).toBeInTheDocument();
  });
});
