/**
 * Избранные блюда — экран на серверном источнике (DRF-2092, F12).
 *
 * Что сторожится:
 *   - список приходит с сервера (customer/saved-meals), не из устройства;
 *   - пусто — названо словами и сказано, как добавить;
 *   - «Удалить» — строка уходит после подтверждения сервером, не раньше;
 *   - «Записать» — на экран текста той же тропой F8 с сохранённой порцией
 *     (`fromSaved`), тропа записи не дублируется;
 *   - отказы по слагам: `nutrition_disabled` → «дневник недоступен»,
 *     `consent_required` → гейт согласия с возвратом сюда,
 *     `ayla_unavailable` → фраза + «Повторить», который повторяет запрос;
 *   - числа под тем же ED-признаком, что дневник.
 */
import { act, configure, fireEvent, getConfig, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/saved-meals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/saved-meals")>();
  return { ...original, listSavedMeals: vi.fn(), deleteSavedMeal: vi.fn() };
});
vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, getWellnessToday: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { ApiError } from "../lib/api";
import { getWellnessToday, type WellnessToday } from "../lib/customer-wellness";
import { deleteSavedMeal, listSavedMeals, type SavedMeal } from "../lib/saved-meals";
import { FAVORITES_COPY, FAVORITES_ROUTE, FoodScannerFavoritesScreen } from "./FoodScannerFavoritesScreen";
import { MANUAL_ROUTE } from "./FoodScannerManualScreen";

const GUARD_ASYNC_TIMEOUT_MS = 20;
let previousAsyncUtilTimeout = 1000;
beforeAll(() => {
  previousAsyncUtilTimeout = getConfig().asyncUtilTimeout;
  configure({ asyncUtilTimeout: GUARD_ASYNC_TIMEOUT_MS });
});
afterAll(() => {
  configure({ asyncUtilTimeout: previousAsyncUtilTimeout });
});

const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

const mockedList = vi.mocked(listSavedMeals);
const mockedDelete = vi.mocked(deleteSavedMeal);
const mockedToday = vi.mocked(getWellnessToday);

const BORSCH: SavedMeal = {
  id: "sm-1",
  dish_name: "Борщ",
  portion_g: 250,
  calories: 125,
  protein_g: 5,
  fat_g: 7.5,
  carbs_g: 10,
  source_food_log_id: null,
  created_at: "2026-09-18T12:00:00+00:00",
};
const OMELETTE: SavedMeal = { ...BORSCH, id: "sm-2", dish_name: "Омлет", portion_g: 150, calories: 230 };

function LocationProbe() {
  const location = useLocation();
  const state = location.state as { returnTo?: string; fromSaved?: unknown } | null;
  return (
    <div data-testid="location">
      {location.pathname}|{state?.returnTo ?? ""}|{state?.fromSaved ? JSON.stringify(state.fromSaved) : ""}
    </div>
  );
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={[FAVORITES_ROUTE]}>
      <Routes>
        <Route path={FAVORITES_ROUTE} element={<FoodScannerFavoritesScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedToday.mockResolvedValue({ nutrition_numbers_hidden: false } as WellnessToday);
  mockedList.mockResolvedValue([BORSCH, OMELETTE]);
  mockedDelete.mockResolvedValue(undefined);
});

describe("список с сервера", () => {
  it("рисует строки, которые отдал сервер: блюдо, порция, ккал", async () => {
    renderScreen();
    await settle();

    expect(mockedList).toHaveBeenCalledTimes(1);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Борщ");
    expect(items[0]).toHaveTextContent("250 г");
    expect(items[0]).toHaveTextContent("125 ккал");
    expect(items[1]).toHaveTextContent("Омлет");
  });

  it("числа скрыты под тем же ED-признаком, что дневник", async () => {
    mockedToday.mockResolvedValue({ nutrition_numbers_hidden: true } as WellnessToday);
    renderScreen();
    await settle();

    const first = screen.getAllByRole("listitem")[0];
    expect(first).toHaveTextContent("Борщ");
    expect(first).not.toHaveTextContent("ккал");
  });

  it("пусто — названо словами и сказано, как добавить", async () => {
    mockedList.mockResolvedValue([]);
    renderScreen();
    await settle();

    expect(screen.getByText(FAVORITES_COPY.emptyTitle)).toBeInTheDocument();
    expect(screen.getByText(FAVORITES_COPY.emptyHint)).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).toBeNull();
  });
});

describe("действия", () => {
  it("«Удалить» — строка уходит после подтверждения сервером", async () => {
    renderScreen();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: `${FAVORITES_COPY.remove}: Борщ` }));
    await settle();

    expect(mockedDelete).toHaveBeenCalledWith("sm-1");
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent("Омлет");
  });

  it("«Удалить» при отказе сервера строку не убирает и называет причину", async () => {
    mockedDelete.mockRejectedValue(new ApiError(502, "ayla_unavailable", "down"));
    renderScreen();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: `${FAVORITES_COPY.remove}: Борщ` }));
    await settle();

    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText(FAVORITES_COPY.unavailable)).toBeInTheDocument();
  });

  it("«Записать» — на экран текста той же тропой с сохранённой порцией", async () => {
    renderScreen();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: `${FAVORITES_COPY.record}: Борщ` }));
    await settle();

    const probe = screen.getByTestId("location");
    expect(probe).toHaveTextContent(MANUAL_ROUTE);
    expect(probe).toHaveTextContent(JSON.stringify({ dish_name: "Борщ", portion_g: 250 }));
  });
});

describe("отказы по имени", () => {
  it("nutrition_disabled → дневник недоступен, списка нет", async () => {
    mockedList.mockRejectedValue(new ApiError(404, "nutrition_disabled", "off"));
    renderScreen();
    await settle();

    expect(screen.getByText(FAVORITES_COPY.diaryOff)).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).toBeNull();
  });

  it("consent_required → гейт согласия с возвратом сюда", async () => {
    mockedList.mockRejectedValue(new ApiError(403, "consent_required", "consent"));
    renderScreen();
    await settle();

    const probe = screen.getByTestId("location");
    expect(probe).toHaveTextContent("/customer/food-scanner/capture");
    expect(probe).toHaveTextContent(FAVORITES_ROUTE);
  });

  it("ayla_unavailable → фраза и «Повторить», который повторяет запрос", async () => {
    mockedList.mockRejectedValueOnce(new ApiError(502, "ayla_unavailable", "down"));
    renderScreen();
    await settle();

    expect(screen.getByText(FAVORITES_COPY.unavailable)).toBeInTheDocument();
    mockedList.mockResolvedValueOnce([BORSCH]);
    fireEvent.click(screen.getByRole("button", { name: FAVORITES_COPY.retry }));
    await settle();

    expect(mockedList).toHaveBeenCalledTimes(2);
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(1);
  });
});
