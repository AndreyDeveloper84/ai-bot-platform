/**
 * Дневник за сегодня — настоящие записи и четыре различимых состояния.
 *
 * # Что этот файл сторожит
 *
 * **1. БЖУ не вычисляется из калорий.** До 08.09.2026 экран кормился
 * заглушкой `fetchDailySummary`, которая считала белки, жиры и углеводы
 * как `calories * 0.075 / 0.018 / 0.105` и показывала это человеку как
 * факт о том, что он съел. Выдуманную НОРМУ можно оспорить; выдуманный
 * факт о себе человек оспаривать не станет.
 *
 * Фикстура подобрана так, что коэффициенты и правда **не совпадают** с
 * настоящими числами: подставьте формулу обратно — тест покраснеет.
 *
 * **2. Четыре состояния, и свести любые два нельзя.** «Ответ не
 * пришёл», «ответ пришёл без записей», «за день пусто» и «записи» —
 * у каждого своя правда и своя цена молчания.
 *
 * **3. Ни одна съеденная тарелка не исчезает.** Незнакомый `meal_type`
 * попадает в «Другое», а не выбрасывается (§78).
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, loadDiaryToday: vi.fn() };
});

import { loadDiaryToday, type WellnessToday } from "../lib/customer-wellness";
import { FoodScannerDiaryScreen } from "./FoodScannerDiaryScreen";

const mockedLoad = vi.mocked(loadDiaryToday);

/** Овсянка: 320 ккал, и БЖУ у неё СВОЁ, не производное от калорий. */
const OATS = {
  id: "fl-1",
  dish_name: "Овсянка с ягодами",
  calories: 320,
  protein_g: 11,
  fat_g: 6,
  carbs_g: 54,
  meal_type: "breakfast",
  logged_at: "2026-09-08T05:31:00Z",
};

const SOUP = {
  id: "fl-2",
  dish_name: "Куриный суп",
  calories: 210,
  protein_g: 18,
  fat_g: 7,
  carbs_g: 12,
  meal_type: "lunch",
  logged_at: "2026-09-08T09:05:00Z",
};

function today(extra: Partial<WellnessToday> = {}): WellnessToday {
  return {
    display_name: "Анна",
    calories_eaten: 530,
    calories_target: 2100,
    pfc: { protein_g: 29, fat_g: 13, carbs_g: 66 },
    ...extra,
  } as WellnessToday;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/food-scanner/diary"]}>
      <Routes>
        <Route
          path="/customer/food-scanner/diary"
          element={<FoodScannerDiaryScreen />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("дневник рисует НАСТОЯЩИЕ числа источника", () => {
  it("БЖУ приходит от источника, а не вычисляется из калорий", async () => {
    mockedLoad.mockResolvedValue({
      state: "entries",
      entries: [OATS, SOUP],
      hideNumbers: false,
      today: today(),
    });
    renderScreen();

    // Присутствие: обе тарелки и их калории на экране.
    expect(await screen.findByText("Овсянка с ягодами")).toBeInTheDocument();
    expect(screen.getByText("Куриный суп")).toBeInTheDocument();
    expect(screen.getByText("~320 ккал")).toBeInTheDocument();

    // Итог — ровно то, что прислал источник.
    expect(screen.getByText("530 / 2100 ккал")).toBeInTheDocument();
    expect(screen.getByText(/Б 29 · Ж 13 · У 66/)).toBeInTheDocument();

    // Отсутствие — та самая формула. 530 × 0.075 = 40, × 0.018 = 10,
    // × 0.105 = 56. Ни одно из этих чисел на экране появиться не может.
    expect(screen.queryByText(/Б 40/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Ж 10/)).not.toBeInTheDocument();
    expect(screen.queryByText(/У 56/)).not.toBeInTheDocument();
  });

  it("цели нет — нет и цели на экране, а съеденное остаётся", async () => {
    // Анкету человек не проходил: `calories_target` и `pfc` отсутствуют.
    mockedLoad.mockResolvedValue({
      state: "entries",
      entries: [OATS],
      hideNumbers: false,
      today: today({ calories_target: undefined, pfc: undefined }),
    });
    renderScreen();

    expect(await screen.findByText("530 ккал")).toBeInTheDocument();
    // Ни «/ 0 ккал», ни пустой строки БЖУ: чужого числа за своё не выдаём.
    expect(screen.queryByText(/\/ 0 ккал/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Б .* · Ж/)).not.toBeInTheDocument();
  });

  it("признак «прятать числа» прячет именно числа, а не еду", async () => {
    mockedLoad.mockResolvedValue({
      state: "entries",
      entries: [OATS],
      hideNumbers: true,
      today: today(),
    });
    renderScreen();

    // Присутствие: что человек ел — остаётся.
    expect(await screen.findByText("Овсянка с ягодами")).toBeInTheDocument();
    // Отсутствие: калорий и БЖУ нет нигде.
    expect(screen.queryByText("~320 ккал")).not.toBeInTheDocument();
    expect(screen.queryByText(/530/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Б 29/)).not.toBeInTheDocument();
  });
});

describe("четыре состояния различимы попарно", () => {
  it("записи — список и подпись про количество", async () => {
    mockedLoad.mockResolvedValue({
      state: "entries",
      entries: [OATS, SOUP],
      hideNumbers: false,
      today: today(),
    });
    renderScreen();
    expect(await screen.findByText(/Сегодня — 2/)).toBeInTheDocument();
  });

  it("за день пусто — так и сказано, и это НЕ ошибка", async () => {
    mockedLoad.mockResolvedValue({
      state: "empty",
      hideNumbers: false,
      today: today({ calories_eaten: 0 }),
    });
    renderScreen();
    expect(
      await screen.findByText(/Пока ничего не записано/),
    ).toBeInTheDocument();
    // Отсутствие: слова про неудачу здесь не звучат.
    expect(screen.queryByText(/не удалось/i)).not.toBeInTheDocument();
  });

  it("ответ пришёл БЕЗ записей — своё состояние, не пустой день", async () => {
    mockedLoad.mockResolvedValue({ state: "unreadable" });
    renderScreen();

    expect(
      await screen.findByText(/Не удалось загрузить дневник/),
    ).toBeInTheDocument();
    // Человеку сказано главное: записи не потеряны.
    expect(screen.getByText(/Записи не потерялись/)).toBeInTheDocument();
    // Отсутствие: «ничего не записано» тут было бы ложью.
    expect(screen.queryByText(/Пока ничего не записано/)).not.toBeInTheDocument();
  });

  it("ответ не пришёл — состояние ошибки, а не пустой день", async () => {
    mockedLoad.mockRejectedValue(new Error("[502] upstream"));
    renderScreen();

    expect(await screen.findByRole("button", { name: /Повторить|снова/i })).toBeInTheDocument();
    expect(screen.queryByText(/Пока ничего не записано/)).not.toBeInTheDocument();
  });
});

describe("ни одна съеденная тарелка не исчезает", () => {
  it("незнакомый приём пищи попадает в «Другое», а не выбрасывается", async () => {
    const midnight = { ...OATS, id: "fl-9", dish_name: "Ночной кефир", meal_type: "midnight" };
    mockedLoad.mockResolvedValue({
      state: "entries",
      entries: [OATS, midnight],
      hideNumbers: false,
      today: today(),
    });
    renderScreen();

    // Положительно: обе на экране, и обе посчитаны.
    expect(await screen.findByText("Ночной кефир")).toBeInTheDocument();
    expect(screen.getByText(/Сегодня — 2/)).toBeInTheDocument();
    // И у неё есть своя группа с именем, а не чужая.
    const other = screen.getByRole("region", { name: /Другое/ });
    expect(within(other).getByText("Ночной кефир")).toBeInTheDocument();
  });
});
