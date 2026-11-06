/**
 * DRF-2766 фаза 2 — итог дня с оценками ИИ на экранах Mini App.
 *
 * Решение владельца 04.10, п.3: оценки ИИ входят в дневной итог; итог
 * помечается «≈ … ккал, включая оценки ИИ»; запись без значения — не ноль,
 * итог неполный. Сервер (часть А) отдаёт счётчики; экран обязан их сказать.
 *
 * Что заперто — каждая пометка в паре с днём без неё:
 *  - помощник: счётчики → признаки; мусор и отсутствие — «нет»;
 *  - неделя: строка дня называет оценки и неполный итог;
 *  - сохранённые: «≈ 447 ккал, включая оценки ИИ», «Итог неполный…»,
 *    «Калории пока не посчитаны.» вместо пустоты, когда итога нет.
 *
 * Дашборд — в `CustomerWellnessDashboardScreen.test.tsx`. Данные синтетические.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/diary-days", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/diary-days")>();
  return { ...original, getDiaryDays: vi.fn() };
});
vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, getWellnessToday: vi.fn() };
});

import { getWellnessToday, type WellnessToday } from "../lib/customer-wellness";
import { getDiaryDays, type DiaryDays } from "../lib/diary-days";
import { approxPrefix, kcalTotalMarks } from "../lib/format";
import { FoodScannerSavedScreen } from "./FoodScannerSavedScreen";
import { FoodScannerWeekScreen, WEEK_COPY, WEEK_ROUTE } from "./FoodScannerWeekScreen";

const mockedDays = vi.mocked(getDiaryDays);
const mockedToday = vi.mocked(getWellnessToday);

const AI_NOTE = "включая оценки ИИ";
const INCOMPLETE = "Итог неполный: не у всех записей есть калории.";
const UNKNOWN = "Калории пока не посчитаны.";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("помощник признаков итога", () => {
  it("счётчики больше нуля — признаки есть", () => {
    expect(kcalTotalMarks({ calories_ai_included: 1, calories_unscored: 2 })).toEqual({
      approx: true,
      incomplete: true,
    });
    expect(kcalTotalMarks({ kcal_ai_included: 1, uncounted_meals: 1 })).toEqual({
      approx: true,
      incomplete: true,
    });
  });

  it.each([[{}], [{ calories_ai_included: 0 }], [{ calories_ai_included: "1" }], [{ calories_unscored: -1 }], [{ calories_ai_included: Number.NaN }]])(
    "отсутствие и мусор — признаков нет: %j",
    (source) => {
      expect(kcalTotalMarks(source)).toEqual({ approx: false, incomplete: false });
    },
  );

  it("«≈ » — только при оценках", () => {
    expect(approxPrefix({ approx: true, incomplete: false })).toBe("≈ ");
    expect(approxPrefix({ approx: false, incomplete: true })).toBe("");
  });
});

function weekWith(row: DiaryDays["days"][number]): DiaryDays {
  return {
    timezone: "Europe/Moscow",
    from: "2026-09-16",
    to: "2026-09-16",
    days: [row],
    nutrition_numbers_hidden: false,
  };
}

function renderWeek() {
  return render(
    <MemoryRouter initialEntries={[WEEK_ROUTE]}>
      <Routes>
        <Route path={WEEK_ROUTE} element={<FoodScannerWeekScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("неделя", () => {
  it("день с оценками и записью без калорий — оба названы", async () => {
    mockedDays.mockResolvedValue(
      weekWith({
        date: "2026-09-16",
        meals_count: 3,
        kcal: 447,
        has_entries: true,
        kcal_ai_included: 1,
        uncounted_meals: 1,
      }),
    );
    renderWeek();

    expect(
      await screen.findByText(`${WEEK_COPY.meals(3, 447)} · ${AI_NOTE} · итог неполный`),
    ).toBeInTheDocument();
  });

  it("день целиком проверенный — без пометок", async () => {
    mockedDays.mockResolvedValue(
      weekWith({ date: "2026-09-16", meals_count: 2, kcal: 300, has_entries: true }),
    );
    renderWeek();

    expect(await screen.findByText(WEEK_COPY.meals(2, 300))).toBeInTheDocument();
    expect(screen.queryByText(new RegExp(AI_NOTE))).not.toBeInTheDocument();
  });
});

function renderSaved() {
  return render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/customer/food-scanner/saved",
          state: { dishName: "Шакшука", calories: null, edMode: false },
        },
      ]}
    >
      <FoodScannerSavedScreen />
    </MemoryRouter>,
  );
}

const TODAY: WellnessToday = {
  calories_eaten: 447,
  water_glasses_eaten: 2,
  active_goals: [],
  display_name: "Анна",
  nutrition_numbers_hidden: false,
};

describe("сохранённые: итог дня", () => {
  it("с оценками — «≈ 447 ккал, включая оценки ИИ»", async () => {
    mockedToday.mockResolvedValue({ ...TODAY, calories_ai_included: 1, calories_unscored: 0 });
    renderSaved();

    expect(await screen.findByTestId("saved-kcal-total")).toHaveTextContent(`≈ 447 ккал, ${AI_NOTE}`);
    expect(screen.queryByText(INCOMPLETE)).not.toBeInTheDocument();
  });

  it("целиком проверенный — без «≈» и без пометки", async () => {
    mockedToday.mockResolvedValue({ ...TODAY, calories_ai_included: 0, calories_unscored: 0 });
    renderSaved();

    const total = await screen.findByTestId("saved-kcal-total");
    expect(total).toHaveTextContent("447 ккал");
    expect(total.textContent).not.toContain("≈");
    expect(total.textContent).not.toContain(AI_NOTE);
  });

  it("с записью без калорий — «Итог неполный»", async () => {
    mockedToday.mockResolvedValue({ ...TODAY, calories_ai_included: 0, calories_unscored: 1 });
    renderSaved();

    expect(await screen.findByTestId("saved-kcal-total")).toHaveTextContent("447 ккал");
    expect(screen.getByText(INCOMPLETE)).toBeInTheDocument();
  });

  it("итога нет — «Калории пока не посчитаны.», а не ноль", async () => {
    const { calories_eaten: _omit, ...withoutTotal } = TODAY;
    void _omit;
    mockedToday.mockResolvedValue({ ...withoutTotal, calories_unscored: 2 });
    renderSaved();

    expect(await screen.findByTestId("saved-kcal-total")).toHaveTextContent(UNKNOWN);
    expect(screen.queryByText(/^0 ккал/)).not.toBeInTheDocument();
  });
});
