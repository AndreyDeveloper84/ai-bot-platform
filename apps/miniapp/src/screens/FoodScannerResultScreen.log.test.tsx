/**
 * F3 «Я распознала так» → запись (DRF-2098).
 *
 * Карточка держит провенанс фото: переименование шлёт `dish_name` РЯДОМ со
 * `scan_id`, а не вместо него (§136 `photo_*`); ключ идемпотентности
 * уходит с карточки; заголовок — как у текстовой половины.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return {
    ...original,
    logMeal: vi.fn(),
  };
});
vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, getWellnessToday: vi.fn() };
});
vi.mock("../hooks/useScreenBack", () => ({ useScreenBack: () => vi.fn() }));

import { getWellnessToday } from "../lib/customer-wellness";
import { logMeal, type ScanResponse } from "../lib/food-scanner";
import { FoodScannerResultScreen } from "./FoodScannerResultScreen";

const mockedLog = vi.mocked(logMeal);
const mockedToday = vi.mocked(getWellnessToday);

const RESULT: ScanResponse = {
  scan_id: "scan-2098-1",
  dish_name: "Борщ",
  confidence: 0.83,
  portion_g: 300,
  nutrition: { calories: 250, protein_g: 12, fat_g: 8, carbs_g: 32 },
  beauty_insights: null,
};

function renderResult() {
  return render(
    <MemoryRouter
      initialEntries={[
        { pathname: "/customer/food-scanner/result", state: { result: RESULT, mealType: "lunch" } },
      ]}
    >
      <Routes>
        <Route path="/customer/food-scanner/result" element={<FoodScannerResultScreen />} />
        <Route path="/customer/food-scanner/saved" element={<div>saved-screen</div>} />
        <Route path="/customer/food-scanner/capture" element={<div>capture-screen</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedToday.mockResolvedValue({ display_name: "", nutrition_numbers_hidden: false } as never);
  mockedLog.mockResolvedValue({ log_id: "log-1", dish_name: "Борщ", meal_type: "lunch", calories: 250 });
});

describe("FoodScannerResultScreen — запись по скану", () => {
  it("заголовок карточки — «Я распознала так», как у текстовой половины", async () => {
    renderResult();
    expect(await screen.findByRole("heading", { level: 1, name: "Я распознала так" })).toBeInTheDocument();
  });

  it("«Записать в дневник» шлёт scan_id, множитель, тип приёма и ключ; без переименования dish_name нет", async () => {
    renderResult();
    fireEvent.click(await screen.findByRole("button", { name: /Записать в дневник/ }));

    await waitFor(() => expect(mockedLog).toHaveBeenCalledTimes(1));
    const req = mockedLog.mock.calls[0]?.[0];
    expect(req?.scan_id).toBe("scan-2098-1");
    expect(req?.dish_name).toBeUndefined();
    expect(req?.meal_type).toBe("lunch");
    expect(req?.portion_multiplier).toBe(1);
    expect(typeof req?.idempotency_key).toBe("string");
    expect(req?.idempotency_key.length).toBeGreaterThan(0);
    expect(await screen.findByText("saved-screen")).toBeInTheDocument();
  });

  it("переименование оставляет scan_id рядом с новым названием", async () => {
    renderResult();
    fireEvent.click(await screen.findByRole("button", { name: "Уточнить" }));
    fireEvent.click(await screen.findByRole("button", { name: "Название блюда" }));
    const input = await screen.findByRole("textbox", { name: "Название блюда" });
    fireEvent.change(input, { target: { value: "Свекольник" } });
    fireEvent.click(screen.getByRole("button", { name: /Записать в дневник/ }));

    await waitFor(() => expect(mockedLog).toHaveBeenCalledTimes(1));
    const req = mockedLog.mock.calls[0]?.[0];
    expect(req?.scan_id).toBe("scan-2098-1");
    expect(req?.dish_name).toBe("Свекольник");
  });

  it("повтор «Записать» на той же карточке несёт тот же ключ идемпотентности", async () => {
    mockedLog.mockRejectedValueOnce(new Error("lost")).mockResolvedValueOnce({
      log_id: "log-1",
      dish_name: "Борщ",
      meal_type: "lunch",
      calories: 250,
    });
    renderResult();
    const save = await screen.findByRole("button", { name: /Записать в дневник/ });
    fireEvent.click(save);
    await waitFor(() => expect(mockedLog).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(save).not.toBeDisabled());
    fireEvent.click(save);
    await waitFor(() => expect(mockedLog).toHaveBeenCalledTimes(2));
    expect(mockedLog.mock.calls[0]?.[0].idempotency_key).toBe(mockedLog.mock.calls[1]?.[0].idempotency_key);
  });
});
