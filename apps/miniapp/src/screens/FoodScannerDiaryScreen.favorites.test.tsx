/**
 * Дневник → избранное (DRF-2092, F12): вход на экран избранного и
 * «В избранное» на записи.
 *
 * «В избранное» шлёт `food_log_id` записи — снимок делает каталог из своей
 * записи, экран числа не переписывает. 201 и 200 сервера — разные фразы:
 * «сохранила» и «уже в избранном» не одно и то же для человека.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, loadDiaryToday: vi.fn() };
});
vi.mock("../lib/saved-meals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/saved-meals")>();
  return { ...original, saveMealFromEntry: vi.fn() };
});

import { ApiError } from "../lib/api";
import { loadDiaryToday, type WellnessToday } from "../lib/customer-wellness";
import { saveMealFromEntry, type SavedMeal } from "../lib/saved-meals";
import { FoodScannerDiaryScreen } from "./FoodScannerDiaryScreen";
import { FAVORITES_COPY, FAVORITES_ROUTE } from "./FoodScannerFavoritesScreen";

const mockedLoad = vi.mocked(loadDiaryToday);
const mockedSave = vi.mocked(saveMealFromEntry);

const OATS = {
  id: "fl-1",
  dish_name: "Овсянка с ягодами",
  calories: 320,
  protein_g: 11,
  fat_g: 6,
  carbs_g: 54,
  meal_type: "breakfast",
  logged_at: "2026-09-08T05:31:00Z",
  entry_origin: "text_estimated_confirmed",
};

const SAVED: SavedMeal = {
  id: "sm-1",
  dish_name: "Овсянка с ягодами",
  portion_g: 100,
  calories: 320,
  protein_g: 11,
  fat_g: 6,
  carbs_g: 54,
  source_food_log_id: "fl-1",
  created_at: "2026-09-18T12:00:00+00:00",
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/food-scanner/diary"]}>
      <Routes>
        <Route path="/customer/food-scanner/diary" element={<FoodScannerDiaryScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedLoad.mockResolvedValue({
    state: "entries",
    entries: [OATS],
    hideNumbers: false,
    today: { display_name: "Анна", calories_eaten: 320, calories_target: 2100 } as WellnessToday,
  });
});

describe("дневник → избранное", () => {
  it("кнопка «Избранное» ведёт на экран избранного", async () => {
    renderScreen();
    await screen.findByText("Овсянка с ягодами");

    fireEvent.click(screen.getByRole("button", { name: FAVORITES_COPY.openFromDiary }));

    expect(await screen.findByTestId("location")).toHaveTextContent(FAVORITES_ROUTE);
  });

  it("«В избранное» шлёт id записи и говорит «сохранила»", async () => {
    mockedSave.mockResolvedValue({ created: true, meal: SAVED });
    renderScreen();
    await screen.findByText("Овсянка с ягодами");

    fireEvent.click(screen.getByRole("button", { name: `В избранное: Овсянка с ягодами` }));
    await settle();

    expect(mockedSave).toHaveBeenCalledWith("fl-1");
    expect(screen.getByText(FAVORITES_COPY.savedNotice("Овсянка с ягодами"))).toBeInTheDocument();
  });

  it("повтор — «уже в избранном», не вторая строка", async () => {
    mockedSave.mockResolvedValue({ created: false, meal: SAVED });
    renderScreen();
    await screen.findByText("Овсянка с ягодами");

    fireEvent.click(screen.getByRole("button", { name: `В избранное: Овсянка с ягодами` }));
    await settle();

    expect(screen.getByText(FAVORITES_COPY.alreadyNotice("Овсянка с ягодами"))).toBeInTheDocument();
  });

  it("отказ каталога назван, не проглочен", async () => {
    mockedSave.mockRejectedValue(new ApiError(502, "ayla_unavailable", "down"));
    renderScreen();
    await screen.findByText("Овсянка с ягодами");

    fireEvent.click(screen.getByRole("button", { name: `В избранное: Овсянка с ягодами` }));
    await settle();

    expect(screen.getByText(FAVORITES_COPY.unavailable)).toBeInTheDocument();
  });
});
