/**
 * F3 — ED-признак карточки результата берётся из дневника (DRF-2106).
 *
 * До этого экран спрашивал `fetchHealthFlags()` — stub за `guardProd`, который
 * в ПРОД-сборке бросал: `edMode` оставался `true`, калории и БЖУ прятались у
 * всех, в консоли — `console.error`. Теперь признак — тот же
 * `nutrition_numbers_hidden` из `wellness/today`, что у Saved / Favorites /
 * Week, fail-closed: числа видны только при явном `false`.
 *
 * Прогон под `DEV=false` намеренно: именно прод-ветка и была сломана.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, getWellnessToday: vi.fn() };
});
vi.mock("../hooks/useScreenBack", () => ({ useScreenBack: () => vi.fn() }));

import { DIARY_OFF_TEXT, getWellnessToday } from "../lib/customer-wellness";
import type { ScanResponse } from "../lib/food-scanner";
import { FoodScannerResultScreen } from "./FoodScannerResultScreen";

const mockedToday = vi.mocked(getWellnessToday);

const RESULT: ScanResponse = {
  scan_id: "scan-2106-1",
  dish_name: "Борщ",
  confidence: 0.9,
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
        <Route path="/customer/food-scanner/capture" element={<div>capture-screen</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const errorSpy = vi.spyOn(console, "error");

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("DEV", false);
  errorSpy.mockImplementation(() => undefined);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("FoodScannerResultScreen — ED-признак из дневника, прод-сборка", () => {
  it("nutrition_numbers_hidden: false → калории и БЖУ видны, console.error не было", async () => {
    mockedToday.mockResolvedValue({ display_name: "", nutrition_numbers_hidden: false } as never);
    renderResult();

    expect(await screen.findByText(/Калории: ~250 ккал/)).toBeInTheDocument();
    expect(screen.getByText(/Б 12 · Ж 8 · У 32 г/)).toBeInTheDocument();
    expect(screen.queryByText("Примерно — записала.")).not.toBeInTheDocument();
    expect(mockedToday).toHaveBeenCalledTimes(1);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("nutrition_numbers_hidden: true → числа спрятаны", async () => {
    mockedToday.mockResolvedValue({ display_name: "", nutrition_numbers_hidden: true } as never);
    renderResult();

    expect(await screen.findByText("Примерно — записала.")).toBeInTheDocument();
    // Признак прочитан — и именно он спрятал числа (положительная стража).
    await waitFor(() => expect(mockedToday).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/Калории:/)).not.toBeInTheDocument();
  });

  it("ключа нет или чтение упало → числа спрятаны (fail-closed), без console.error", async () => {
    mockedToday.mockResolvedValueOnce({ display_name: "" } as never);
    const first = renderResult();
    expect(await screen.findByText("Примерно — записала.")).toBeInTheDocument();
    expect(screen.queryByText(/Калории:/)).not.toBeInTheDocument();
    first.unmount();

    mockedToday.mockRejectedValueOnce(new Error("network"));
    renderResult();
    expect(await screen.findByText("Примерно — записала.")).toBeInTheDocument();
    await waitFor(() => expect(mockedToday).toHaveBeenCalledTimes(2));
    expect(screen.queryByText(/Калории:/)).not.toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();
  });
});

describe("FoodScannerResultScreen — контур выключили между сканом и записью (DRF-2071)", () => {
  it("сводка с nutrition_disabled → «недоступен», без «Записать» и «Уточнить»; «Не то» остаётся", async () => {
    mockedToday.mockResolvedValue({ display_name: "", nutrition_disabled: true } as never);
    renderResult();

    expect(await screen.findByText(DIARY_OFF_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Записать в дневник" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Уточнить" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Не то" })).toBeInTheDocument();
    // Числа при этом спрятаны — ключа признака нет (fail-closed).
    expect(screen.queryByText(/Калории:/)).not.toBeInTheDocument();
  });

  it("положительная стража: без маркера «Записать» и «Уточнить» на месте", async () => {
    mockedToday.mockResolvedValue({ display_name: "", nutrition_numbers_hidden: false } as never);
    renderResult();

    expect(await screen.findByRole("button", { name: "Записать в дневник" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Уточнить" })).toBeInTheDocument();
    expect(screen.queryByText(DIARY_OFF_TEXT)).not.toBeInTheDocument();
  });
});
