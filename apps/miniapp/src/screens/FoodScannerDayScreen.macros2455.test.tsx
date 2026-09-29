/**
 * Карточка дневника несёт БЖУ, а не одни калории (DRF-2455, §77 п.40).
 *
 * Слова владельца: «фото блюд сохраняем и показываем их в дневнике со
 * всей информацией о записи: время, ккал, бжу, фото». Время, название и
 * калории карточка показывала; белки-жиры-углеводы **приходили в ответе
 * и не показывались** — `FoodDiaryEntry` несёт их с самого начала.
 *
 * Вид строки берётся у экрана результата, где он согласован:
 * «Б 11 · Ж 6 · У 54 г». Новых слов здесь не появляется.
 *
 * Узлы:
 * * k1 — числа есть: показаны и калории, и БЖУ;
 * * k2 — чисел нет (DRF-2371): нет ни калорий, ни БЖУ — «Б null» и «Б 0»
 *   одинаково утверждали бы расчёт, которого не было;
 * * k3 — часть макросов отсутствует по отдельности: показывается ровно
 *   то, что посчитано;
 * * k4 — режим без чисел (ED): не показывается ничего числового, как и
 *   было для калорий.
 *
 * Подмена для проверки k2/k3: вернуть безусловный вывод строки макросов —
 * узлы краснеют на «Б null».
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/diary-days", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/diary-days")>();
  return { ...original, getDiaryDay: vi.fn() };
});

import { getDiaryDay, type DiaryDay } from "../lib/diary-days";
import { DAY_ROUTE_PATTERN, FoodScannerDayScreen, dayRoute } from "./FoodScannerDayScreen";

const mockedDay = vi.mocked(getDiaryDay);

const COUNTED = {
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

function day(entries: unknown[], hidden = false): DiaryDay {
  return {
    date: "2026-09-14",
    calories_total: 320,
    entries: entries as DiaryDay["entries"],
    nutrition_numbers_hidden: hidden,
  };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={[dayRoute("2026-09-14")]}>
      <Routes>
        <Route path={DAY_ROUTE_PATTERN} element={<FoodScannerDayScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("DRF-2455 — БЖУ в карточке дня", () => {
  it("k1: посчитанная запись показывает калории и БЖУ", async () => {
    mockedDay.mockResolvedValue(day([COUNTED]));
    renderScreen();

    // Утверждение о наличии: запись на экране.
    expect(await screen.findByText("Овсянка с ягодами")).toBeInTheDocument();
    expect(screen.getByText("~320 ккал")).toBeInTheDocument();
    expect(screen.getByText("Б 11 · Ж 6 · У 54 г")).toBeInTheDocument();
  });

  it("k2: запись без чисел не получает ни калорий, ни БЖУ", async () => {
    mockedDay.mockResolvedValue(
      day([{ ...COUNTED, calories: null, protein_g: null, fat_g: null, carbs_g: null }]),
    );
    renderScreen();

    expect(await screen.findByText("Овсянка с ягодами")).toBeInTheDocument();
    expect(screen.queryByText(/ккал/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Б /)).not.toBeInTheDocument();
  });

  it("k3: отсутствующий макрос не печатается, посчитанные — печатаются", async () => {
    mockedDay.mockResolvedValue(day([{ ...COUNTED, protein_g: null }]));
    renderScreen();

    expect(await screen.findByText("~320 ккал")).toBeInTheDocument();
    expect(screen.getByText("Ж 6 · У 54 г")).toBeInTheDocument();
  });

  it("k4: в режиме без чисел не показывается ничего числового", async () => {
    mockedDay.mockResolvedValue(day([COUNTED], true));
    renderScreen();

    expect(await screen.findByText("Овсянка с ягодами")).toBeInTheDocument();
    expect(screen.queryByText(/ккал/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Б 11/)).not.toBeInTheDocument();
  });
});
