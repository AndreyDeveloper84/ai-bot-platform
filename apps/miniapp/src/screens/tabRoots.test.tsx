/**
 * Вкладки панели — корни (лист П6 из DRF-2191; макет DRF-1321 v1.2).
 *
 * Сегодня «План» (`PlanLiteScreen`) и «Дневник» (`FoodScannerDiaryScreen`)
 * — листовые экраны: панели нет, а стрелка «назад» ведёт по ФИКСИРОВАННОМУ
 * адресу (план → экран цели, дневник → Главная; `backTo` несёт адрес, не
 * смещение по истории — DRF-1493). После DRF-2191 в эти вкладки заходят с
 * четырёх экранов, и симптом виден: с «Записей» тап «План» убирает панель, а
 * «назад» уводит не туда, откуда пришли.
 *
 * Здесь закрепляется макет: пять вкладок панели — пять корней. Корень —
 * объявление `screenRoot(...)` (DRF-1493: молчание невалидно), у корня нет
 * стрелки «назад» (ни нарисованной, ни системной в MAX), и панель на нём
 * есть — одна и та же `CustomerTabBar`.
 *
 * Чего здесь нет: истории переходов. `navigate(-1)` запрещён контрактом —
 * человек приходит по deep link из бота, истории у него нет.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/plan-lite", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/plan-lite")>();
  return { ...original, getPlanLite: vi.fn(), getPlanLiteProposal: vi.fn() };
});
vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return { ...original, fetchDecisionContext: vi.fn() };
});
vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, loadDiaryToday: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), getInitData: () => "", signalReady: vi.fn() };
});

import { CUSTOMER_TABS } from "../components/CustomerTabBar";
import { fetchDecisionContext } from "../lib/customer-goals";
import { loadDiaryToday } from "../lib/customer-wellness";
import { setBackButton } from "../lib/max-sdk";
import { getPlanLite, getPlanLiteProposal } from "../lib/plan-lite";
import { ApiError } from "../lib/api";
import { FoodScannerDiaryScreen } from "./FoodScannerDiaryScreen";
import { PlanLiteScreen } from "./PlanLiteScreen";

const mockedPlan = vi.mocked(getPlanLite);
const mockedProposal = vi.mocked(getPlanLiteProposal);
const mockedContext = vi.mocked(fetchDecisionContext);
const mockedDiary = vi.mocked(loadDiaryToday);
const mockedBackButton = vi.mocked(setBackButton);

/** Каждая вкладка панели — экран под своим маршрутом. */
const TAB_SCREENS: Record<string, () => JSX.Element> = {
  "/customer/plan": PlanLiteScreen,
  "/customer/food-scanner/diary": FoodScannerDiaryScreen,
};

function renderAt(path: string) {
  const Screen = TAB_SCREENS[path];
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={path} element={<Screen />} />
        <Route path="*" element={<div>ДРУГОЙ ЭКРАН</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedPlan.mockResolvedValue(null);
  mockedProposal.mockRejectedValue(new ApiError(404, "no_template", "none"));
  mockedContext.mockResolvedValue({
    version: 2,
    known: { goal: null, anketa: [] },
    missing: [],
    suggestions: [],
    intents: [],
    next: null,
  } as never);
  mockedDiary.mockResolvedValue({ entries: [] } as never);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("вкладки панели — корни (макет DRF-1321)", () => {
  it("каждая вкладка панели ведёт на экран, который рисует ту же панель", async () => {
    for (const tab of CUSTOMER_TABS) {
      if (!TAB_SCREENS[tab.route]) continue; // Главная/Записи/Профиль — уже корни (DRF-2191)
      const view = renderAt(tab.route);
      const nav = await screen.findByRole("navigation", { name: "Основная навигация" });
      expect(
        within(nav).getAllByRole("button").map((b) => b.getAttribute("aria-label")),
        `${tab.label}: панель на экране вкладки`,
      ).toEqual(["Главная", "План", "Дневник", "Записи", "Профиль"]);
      expect(
        within(nav).getByRole("button", { name: tab.label }),
        `${tab.label}: своя вкладка подсвечена`,
      ).toHaveAttribute("aria-current", "page");
      view.unmount();
    }
  });

  it("на экране вкладки нет стрелки «назад» — ни нарисованной, ни системной в MAX", async () => {
    for (const route of Object.keys(TAB_SCREENS)) {
      const view = renderAt(route);
      await screen.findByRole("navigation", { name: "Основная навигация" });
      expect(screen.queryByRole("button", { name: "Назад" }), route).toBeNull();
      // `setBackButton` зовётся с `undefined` (корень) — системная кнопка спрятана.
      const calls = mockedBackButton.mock.calls.map((c) => c[0]);
      expect(calls.every((arg) => arg === undefined), `${route}: системная «назад» спрятана`).toBe(
        true,
      );
      view.unmount();
      mockedBackButton.mockClear();
    }
  });
});

describe("перепись: у экранов вкладок объявлен корень", () => {
  const SOURCES = import.meta.glob(["./PlanLiteScreen.tsx", "./FoodScanner*.tsx"], {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;

  it("PlanLiteScreen и FoodScannerDiaryScreen объявляют screenRoot, а не backTo", () => {
    for (const name of ["PlanLiteScreen.tsx", "FoodScannerDiaryScreen.tsx"]) {
      const src = Object.entries(SOURCES).find(([p]) => p.endsWith(name))?.[1];
      expect(src, name).toBeTruthy();
      expect(src, `${name}: корень объявлен`).toMatch(/useScreenBack\(\s*screenRoot\(/);
      expect(src, `${name}: фиксированного родителя нет`).not.toMatch(/useScreenBack\(\s*backTo\(/);
    }
  });

  it("положительная пара: листовые экраны дневника по-прежнему возвращаются к своему родителю", () => {
    // «Неделя», «День», «Избранное», ручной ввод — не вкладки: у них родитель
    // есть и остаётся, иначе перепись выродилась бы в «у всех корень».
    for (const name of ["FoodScannerWeekScreen.tsx", "FoodScannerFavoritesScreen.tsx"]) {
      const src = Object.entries(SOURCES).find(([p]) => p.endsWith(name))?.[1];
      expect(src, name).toBeTruthy();
      expect(src, `${name}: родитель задан адресом`).toMatch(/useScreenBack\(\s*backTo\(/);
    }
  });
});
