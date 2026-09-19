/**
 * Дневник → неделя (DRF-2099): вход на экран недели из дневника.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, loadDiaryToday: vi.fn() };
});

import { loadDiaryToday, type WellnessToday } from "../lib/customer-wellness";
import { FoodScannerDiaryScreen } from "./FoodScannerDiaryScreen";
import { WEEK_COPY, WEEK_ROUTE } from "./FoodScannerWeekScreen";

const mockedLoad = vi.mocked(loadDiaryToday);

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

beforeEach(() => {
  vi.clearAllMocks();
});

describe("дневник → неделя", () => {
  it("кнопка «Неделя» ведёт на экран недели — и из пустого дня тоже", async () => {
    mockedLoad.mockResolvedValue({
      state: "empty",
      hideNumbers: false,
      today: { display_name: "Анна", calories_eaten: 0 } as WellnessToday,
    });
    renderScreen();
    const button = await screen.findByRole("button", { name: WEEK_COPY.openFromDiary });

    fireEvent.click(button);

    expect(await screen.findByTestId("location")).toHaveTextContent(WEEK_ROUTE);
  });
});
