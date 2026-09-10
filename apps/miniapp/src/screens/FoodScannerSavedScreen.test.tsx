/**
 * Экран «Записала» — без ориентира нет ни шкалы, ни процента.
 *
 * # Что этот файл сторожит
 *
 * Владелец 09.09.2026 (§82, раздел 1; §85, раздел 8) сказал дословно:
 * при отсутствии активного ориентира **не показываем** цель, шкалу
 * выполнения, проценты, дефицит или превышение — только фактические
 * данные. С этой правки ориентира нет НИ У КОГО: плоские 2000 ккал
 * удалены, формула воды 30 мл × вес снята до утверждения методики.
 *
 * Экран это уже умеет — `calories_target` объявлен необязательным, и
 * процент считается только когда известны оба числа. Но «умеет» без
 * замера это обещание кода о самом себе. Здесь оно проверяется:
 *
 * 1. шкалы нет — не «шкала на нуле», а НЕТ элемента `progressbar`;
 * 2. процента нет — ни строкой, ни в `aria-label` для того человека,
 *    который не может проверить глазами;
 * 3. слов о дефиците, превышении и «выполнении» нет;
 * 4. **съеденное при этом на месте** — снимается ориентир, не факт.
 *
 * Четвёртое — контроль присутствия. Без него первые три прошли бы
 * победно на экране, который не отрисовал вообще ничего.
 *
 * Обратная сторона (ориентир ЕСТЬ → шкала и процент показываются)
 * проверяется тем же файлом: §85 их разрешил, и молчаливая потеря
 * шкалы после утверждения методики была бы вторым дефектом.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, getWellnessToday: vi.fn() };
});

import {
  getWellnessToday,
  type WellnessToday,
} from "../lib/customer-wellness";
import { FoodScannerSavedScreen } from "./FoodScannerSavedScreen";

const mockedToday = vi.mocked(getWellnessToday);

function renderScreen() {
  return render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/customer/food-scanner/saved",
          state: { dishName: "Овсянка с ягодами", calories: 320, edMode: false },
        },
      ]}
    >
      <FoodScannerSavedScreen />
    </MemoryRouter>,
  );
}

/** Слова, запрещённые решением при отсутствии ориентира. */
const JUDGEMENT_WORDS = [
  /дефицит/i,
  /превышен/i,
  /выполнен/i,
  /не добрал/i,
  /осталось/i,
  /норм[аеуы]/i,
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ориентира нет — экран показывает факт и молчит про цель", () => {
  const NO_TARGET: WellnessToday = {
    calories_eaten: 320,
    // `calories_target` ОТСУТСТВУЕТ — не ноль и не null. Ровно так его
    // теперь отдаёт бэкенд: ключа в ответе нет вовсе.
    water_glasses_eaten: 2,
    active_goals: [],
    display_name: "Анна",
    // Признак приходит от источника, и его отсутствие ПРЯЧЕТ числа
    // (fail-closed, §10 Appendix ED Mode). Здесь он явный: предмет
    // теста — отсутствие ОРИЕНТИРА, а не режим РПП, и смешивать два
    // разных «чисел нет» нельзя.
    nutrition_numbers_hidden: false,
  };

  it("съеденное показано — это контроль присутствия", async () => {
    mockedToday.mockResolvedValue(NO_TARGET);
    renderScreen();

    // POSITIVE ВПЕРЕДИ: экран отрисован и число дня на нём есть.
    // Отрицания ниже — про ЭТОТ экран, а не про пустой.
    expect(await screen.findByText("320 ккал")).toBeInTheDocument();
    expect(screen.getByText(/Овсянка с ягодами/)).toBeInTheDocument();
  });

  it("шкалы нет — элемента, а не «шкала на нуле»", async () => {
    mockedToday.mockResolvedValue(NO_TARGET);
    renderScreen();
    await screen.findByText("320 ккал");

    // Полоса при нуле не видна глазом, но `role="progressbar"`
    // озвучивается как «0 из 100» — то есть противоречит строке над
    // ней ровно для того человека, который не может её увидеть.
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("процента нет ни строкой, ни в озвучке", async () => {
    mockedToday.mockResolvedValue(NO_TARGET);
    const { container } = renderScreen();
    await screen.findByText("320 ккал");

    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/процент/i)).not.toBeInTheDocument();
    // Второе число не появляется и в виде «320 / 2000».
    expect(screen.queryByText(/\d+\s*\/\s*\d+/)).not.toBeInTheDocument();
    // Озвучка — отдельная поверхность: `aria-label` не проверяется
    // текстовым запросом и уже расходился со строкой на экране.
    expect(container.innerHTML).not.toMatch(/процент/i);
  });

  it("нет слов о дефиците, превышении и выполнении", async () => {
    mockedToday.mockResolvedValue(NO_TARGET);
    const { container } = renderScreen();
    await screen.findByText("320 ккал");

    for (const word of JUDGEMENT_WORDS) {
      expect(container.textContent ?? "").not.toMatch(word);
    }
  });
});

describe("ориентир есть — §85 вернул шкалу и процент", () => {
  const WITH_TARGET: WellnessToday = {
    calories_eaten: 1080,
    calories_target: 2000,
    water_glasses_eaten: 4,
    active_goals: [],
    display_name: "Анна",
    nutrition_numbers_hidden: false,
  };

  it("оба числа, шкала и процент показываются", async () => {
    mockedToday.mockResolvedValue(WITH_TARGET);
    renderScreen();

    // Контроль присутствия для тестов выше: они утверждают ОТСУТСТВИЕ
    // шкалы и процента, и прошли бы победно в мире, где экран не умеет
    // рисовать их вовсе. Умеет — при ориентире.
    expect(await screen.findByText("1080 / 2000 ккал")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.getByText("54 %")).toBeInTheDocument();
  });
});
