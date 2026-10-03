/**
 * DRF-2761 — оценка калорий ИИ на экранах Mini App: число с пометкой.
 *
 * Решение владельца 02.10.2026 (пересмотр вопроса 40): при промахе
 * справочника калории оценивает ИИ, и число показывается с пометкой —
 * дословно «Оценка ИИ». Владелец отдельно назвал поверхности: карточка,
 * подтверждение **и дневник, экраны дня, Mini App**.
 *
 * Оценка едет своим полем (`kcal_ai_estimate` у оценки, `ai_calories` у
 * записи), проверенное `kcal` / `calories` при ней пустое. Узлы:
 *
 *  - m1 — форма одна на все экраны: «≈ N ккал · Оценка ИИ»;
 *  - m2 — карточка текстового ввода: оценка показана (и без названных
 *    граммов), только калории; проверенное число оценку бьёт;
 *  - m3 — экран дня и дневник: запись с оценкой несёт число с пометкой;
 *    проверенная запись — как раньше, без пометки;
 *  - m4 — режим без чисел скрывает и оценку;
 *  - m5 — мусор в поле оценки числом не становится.
 *
 * В итог дня оценка не входит: её не суммирует каталог (заперто там).
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { primeDisplayName } from "../components/CustomerAvatarEntry";

vi.mock("../lib/diary-days", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/diary-days")>();
  return { ...original, getDiaryDay: vi.fn() };
});

vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, loadDiaryToday: vi.fn() };
});

import {
  loadDiaryToday,
  type FoodDiaryEntry,
  type WellnessToday,
} from "../lib/customer-wellness";
import { getDiaryDay, type DiaryDay } from "../lib/diary-days";
import type { FoodTextEstimate } from "../lib/food-scanner";
import { AI_ESTIMATE_MARK, aiCaloriesOf, aiKcalPhrase } from "../lib/format";
import { DAY_ROUTE_PATTERN, FoodScannerDayScreen, dayRoute } from "./FoodScannerDayScreen";
import { FoodScannerDiaryScreen } from "./FoodScannerDiaryScreen";
import { renderEstimateLines } from "./FoodScannerManualScreen";

const mockedDay = vi.mocked(getDiaryDay);
const mockedToday = vi.mocked(loadDiaryToday);

/** Запись с оценкой ИИ: проверенных чисел нет вовсе. */
const ESTIMATED = {
  id: "fl-ai",
  dish_name: "Зыбзик квантовый",
  calories: null,
  ai_calories: 750,
  protein_g: null,
  fat_g: null,
  carbs_g: null,
  meal_type: "lunch",
  logged_at: "2026-09-14T09:05:00Z",
  entry_origin: "text_estimated_confirmed",
};

/** Проверенная запись — как до листа. */
const VERIFIED = {
  id: "fl-1",
  dish_name: "Овсянка с ягодами",
  calories: 320,
  protein_g: 11,
  fat_g: 6,
  carbs_g: 54,
  meal_type: "breakfast",
  logged_at: "2026-09-14T05:31:00Z",
  entry_origin: "photo_estimated_confirmed",
};

function estimate(over: Partial<FoodTextEstimate> = {}): FoodTextEstimate {
  return {
    matched_dish: "зыбзик",
    portion_g: 300,
    portion_estimated: false,
    kcal: null,
    portion_source: "provider",
    protein_g: null,
    fat_g: null,
    carbs_g: null,
    kcal_ai_estimate: 750,
    ...over,
  };
}

function renderDay(entries: unknown[], hidden = false) {
  mockedDay.mockResolvedValue({
    date: "2026-09-14",
    calories_total: 320,
    entries: entries as DiaryDay["entries"],
    nutrition_numbers_hidden: hidden,
  });
  return render(
    <MemoryRouter initialEntries={[dayRoute("2026-09-14")]}>
      <Routes>
        <Route path={DAY_ROUTE_PATTERN} element={<FoodScannerDayScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderDiary(entries: unknown[], hideNumbers = false) {
  mockedToday.mockResolvedValue({
    state: "entries",
    entries: entries as FoodDiaryEntry[],
    hideNumbers,
    today: {
      display_name: "Анна",
      calories_eaten: 320,
      calories_target: 2100,
      pfc: { protein_g: 11, fat_g: 6, carbs_g: 54 },
    } as WellnessToday,
  });
  return render(
    <MemoryRouter initialEntries={["/customer/food-scanner/diary"]}>
      <Routes>
        <Route path="/customer/food-scanner/diary" element={<FoodScannerDiaryScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  primeDisplayName("Тест Тестов");
});

describe("m1 — одна форма на все экраны", () => {
  it("пометка — слова владельца дословно", () => {
    expect(AI_ESTIMATE_MARK).toBe("Оценка ИИ");
    expect(aiKcalPhrase(319.6)).toBe("≈ 320 ккал · Оценка ИИ");
  });

  it("проверенное бьёт оценку; мусор числом не становится (m5)", () => {
    expect(aiCaloriesOf({ calories: null, ai_calories: 750 })).toBe(750);
    expect(aiCaloriesOf({ ai_calories: 0 })).toBe(0);
    expect(aiCaloriesOf({ calories: 320, ai_calories: 750 })).toBeNull();
    expect(aiCaloriesOf({ calories: null })).toBeNull();
    for (const junk of ["750", true, Number.NaN, Number.POSITIVE_INFINITY, -5, null, {}]) {
      expect(aiCaloriesOf({ calories: null, ai_calories: junk })).toBeNull();
    }
  });
});

describe("m2 — карточка текстового ввода", () => {
  it("промах без оценки: числа в карточке нет — так было до листа", () => {
    expect(renderEstimateLines(estimate({ kcal_ai_estimate: null }))).toEqual([
      "Я распознала так: зыбзик.",
      "Порция — 300 г, по твоим словам.",
    ]);
  });

  it("промах с оценкой: число с пометкой, только калории", () => {
    expect(renderEstimateLines(estimate())).toEqual([
      "Я распознала так: зыбзик.",
      "Порция — 300 г, по твоим словам.",
      "≈ 750 ккал · Оценка ИИ.",
    ]);
  });

  it("граммов человек не называл — оценка всё равно показана", () => {
    const lines = renderEstimateLines(
      estimate({
        portion_g: 100,
        portion_estimated: true,
        portion_source: "unknown",
        kcal_ai_estimate: 250,
      }),
    );

    expect(lines).toEqual([
      "Я распознала так: зыбзик.",
      "Порция — примерно 100 г, это оценка: граммов в сообщении не было.",
      "≈ 250 ккал · Оценка ИИ.",
    ]);
  });

  it("проверенное число бьёт оценку и идёт без пометки", () => {
    const lines = renderEstimateLines(
      estimate({ matched_dish: "борщ", kcal: 150, protein_g: 6, fat_g: 9, carbs_g: 12 }),
    );

    expect(lines).toEqual([
      "Я распознала так: борщ.",
      "Порция — 300 г, по твоим словам.",
      "Примерно 150 ккал · Б 6 · Ж 9 · У 12 — оценка по справочнику блюд.",
    ]);
  });
});

describe("m3 — экран дня", () => {
  it("запись с оценкой несёт число с пометкой; проверенная — как раньше", async () => {
    renderDay([VERIFIED, ESTIMATED]);

    expect(await screen.findByText("Зыбзик квантовый")).toBeInTheDocument();
    expect(screen.getByText("≈ 750 ккал · Оценка ИИ")).toBeInTheDocument();
    // Проверенная запись — прежним видом и без пометки; пометка на экране одна.
    expect(screen.getByText("~320 ккал")).toBeInTheDocument();
    expect(screen.getAllByText(/Оценка ИИ/)).toHaveLength(1);
  });

  it("запись без чисел и без оценки не получает ничего — как до листа", async () => {
    renderDay([{ ...ESTIMATED, ai_calories: null }]);

    expect(await screen.findByText("Зыбзик квантовый")).toBeInTheDocument();
    expect(screen.queryByText(/ккал/)).not.toBeInTheDocument();
  });

  it("m4: режим без чисел скрывает и оценку", async () => {
    renderDay([VERIFIED, ESTIMATED], true);

    expect(await screen.findByText("Зыбзик квантовый")).toBeInTheDocument();
    expect(screen.queryByText(/ккал/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Оценка ИИ/)).not.toBeInTheDocument();
  });
});

describe("m3 — дневник", () => {
  it("запись с оценкой несёт число с пометкой; проверенная — как раньше", async () => {
    renderDiary([VERIFIED, ESTIMATED]);

    expect(await screen.findByText("Зыбзик квантовый")).toBeInTheDocument();
    expect(screen.getByText("≈ 750 ккал · Оценка ИИ")).toBeInTheDocument();
    expect(screen.getByText("~320 ккал")).toBeInTheDocument();
    expect(screen.getAllByText(/Оценка ИИ/)).toHaveLength(1);
  });

  it("запись без оценки пометки не получает", async () => {
    renderDiary([VERIFIED, { ...ESTIMATED, ai_calories: null }]);

    expect(await screen.findByText("Зыбзик квантовый")).toBeInTheDocument();
    expect(screen.getByText("~320 ккал")).toBeInTheDocument();
    expect(screen.queryByText(/Оценка ИИ/)).not.toBeInTheDocument();
  });

  it("m4: режим без чисел скрывает и оценку", async () => {
    renderDiary([VERIFIED, ESTIMATED], true);

    expect(await screen.findByText("Зыбзик квантовый")).toBeInTheDocument();
    expect(screen.queryByText(/~320 ккал/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Оценка ИИ/)).not.toBeInTheDocument();
  });
});
