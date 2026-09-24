/**
 * «~0 ккал» — число, которого никто не считал (DRF-2371).
 *
 * Каталог отдаёт скан без чисел, когда порция неизвестна или блюда нет в
 * справочнике: `nutrition.calories === null`. Экран умножал это на
 * множитель порции — `Math.round(null * 1)` даёт **0** — и печатал
 * «Калории: ~0 ккал». Ноль здесь не приближение, а выдумка: он читается
 * как «посчитано, и вышло почти ничего».
 *
 * Узлы:
 * * k1 — числа нет: слова «ккал» на экране нет вовсе, нуля нет;
 * * k2 — числа нет: есть существующая дорога «Написать вручную» (там
 *   согласованный вопрос «Сколько граммов?» и пересчёт по весу);
 * * k3 — положительная пара: посчитанный скан показывает число как прежде
 *   и дорогой не мешает.
 *
 * Подмена для проверки узла: вернуть в экран прежний расчёт
 * (`Math.round(result.nutrition.calories * portionMultiplier)` без
 * проверки на `null`) — k1 краснеет на «~0 ккал».
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return { ...original, logMeal: vi.fn() };
});
vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, getWellnessToday: vi.fn() };
});
vi.mock("../hooks/useScreenBack", () => ({ useScreenBack: () => vi.fn() }));

import { getWellnessToday } from "../lib/customer-wellness";
import { logMeal, type ScanResponse } from "../lib/food-scanner";
import { FoodScannerResultScreen } from "./FoodScannerResultScreen";

const mockedToday = vi.mocked(getWellnessToday);

/** Порция неизвестна: провайдер назвал блюдо, но не вес — считать нечем. */
const WITHOUT_NUMBERS: ScanResponse = {
  scan_id: "scan-2371-1",
  dish_name: "Ризотто с трюфелем",
  confidence: 0.81,
  portion_g: null,
  nutrition: { calories: null, protein_g: null, fat_g: null, carbs_g: null },
  beauty_insights: null,
};

const WITH_NUMBERS: ScanResponse = {
  ...WITHOUT_NUMBERS,
  scan_id: "scan-2371-2",
  dish_name: "Борщ",
  portion_g: 300,
  nutrition: { calories: 250, protein_g: 12, fat_g: 8, carbs_g: 32 },
};

function renderResult(result: ScanResponse) {
  return render(
    <MemoryRouter
      initialEntries={[
        { pathname: "/customer/food-scanner/result", state: { result, mealType: "lunch" } },
      ]}
    >
      <Routes>
        <Route path="/customer/food-scanner/result" element={<FoodScannerResultScreen />} />
        <Route path="/customer/food-scanner/manual" element={<div>manual-screen</div>} />
        <Route path="/customer/food-scanner/capture" element={<div>capture-screen</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedToday.mockResolvedValue({ display_name: "", nutrition_numbers_hidden: false } as never);
  vi.mocked(logMeal).mockResolvedValue({
    log_id: "log-1",
    dish_name: "Ризотто с трюфелем",
    meal_type: "lunch",
    calories: null,
  } as never);
});

describe("DRF-2371 — скан без чисел", () => {
  it("k1: блюдо названо, а числа калорий на экране нет — ни нуля, ни другого", async () => {
    renderResult(WITHOUT_NUMBERS);

    // Утверждение о наличии — раньше утверждения об отсутствии: карточка есть.
    expect(await screen.findByRole("heading", { level: 1, name: "Я распознала так" })).toBeInTheDocument();
    expect(screen.getByText("Ризотто с трюфелем")).toBeInTheDocument();
    // А числа — нет. Проверка по строке «Калории», а не по «~0»: React
    // печатает число отдельным текстовым узлом, и совпадение по «~0»
    // прошло бы мимо дефекта, ничего не доказав.
    await waitFor(() => {
      expect(screen.queryByText(/Калории/)).not.toBeInTheDocument();
    });
    expect(screen.queryByText(/ккал/)).not.toBeInTheDocument();
  });

  it("k2: вместо числа — существующая дорога «Написать вручную»", async () => {
    renderResult(WITHOUT_NUMBERS);

    const manual = await screen.findByRole("button", { name: "Написать вручную" });
    fireEvent.click(manual);
    expect(await screen.findByText("manual-screen")).toBeInTheDocument();
  });

  it("k3: посчитанный скан показывает число как прежде и дороги не навязывает", async () => {
    renderResult(WITH_NUMBERS);

    expect(await screen.findByText("Калории: ~250 ккал")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Написать вручную" })).not.toBeInTheDocument();
  });
});

describe("DRF-2371 — признак происхождения порции решает показ", () => {
  it("«provider»: вес назвали — число показано", async () => {
    renderResult({ ...WITH_NUMBERS, portion_source: "provider" });

    expect(await screen.findByText("Калории: ~250 ккал")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Написать вручную" })).not.toBeInTheDocument();
  });

  it("«unknown»: числа есть, но веса никто не называл — числа нет, есть ход", async () => {
    // Ровно случай каталога: вес не назван, счёт идёт по базовой константе.
    // Показать такое число как названное — та же ложь, что «~0 ккал».
    renderResult({ ...WITH_NUMBERS, portion_source: "unknown" });

    expect(await screen.findByRole("button", { name: "Написать вручную" })).toBeInTheDocument();
    expect(screen.queryByText(/Калории/)).not.toBeInTheDocument();
  });

  it("«typical»: вес подставлен справочником — числа нет, есть ход подтверждения", async () => {
    renderResult({ ...WITH_NUMBERS, portion_source: "typical" });

    expect(await screen.findByRole("button", { name: "Написать вручную" })).toBeInTheDocument();
    expect(screen.queryByText(/Калории/)).not.toBeInTheDocument();
  });

  it("незнакомое значение читается осторожно, а не как названное", async () => {
    renderResult({ ...WITH_NUMBERS, portion_source: "confirmed" });

    expect(await screen.findByRole("button", { name: "Написать вручную" })).toBeInTheDocument();
    expect(screen.queryByText(/Калории/)).not.toBeInTheDocument();
  });

  it("поля нет (старый ответ): число с названным весом показывается как прежде", async () => {
    // До половины B (DRF-2444) числа существуют только при названном весе —
    // регресса на старых ответах быть не должно.
    renderResult(WITH_NUMBERS);

    expect(await screen.findByText("Калории: ~250 ккал")).toBeInTheDocument();
  });
});
