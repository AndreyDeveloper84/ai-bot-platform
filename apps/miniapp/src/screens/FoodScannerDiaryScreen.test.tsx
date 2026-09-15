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
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../lib/customer-wellness")>();
  return {
    ...original,
    loadDiaryToday: vi.fn(),
    deleteFoodEntry: vi.fn(),
    restoreFoodEntry: vi.fn(),
    correctFoodEntryGrams: vi.fn(),
  };
});

import {
  correctFoodEntryGrams,
  deleteFoodEntry,
  DIARY_CONSENT_REQUIRED_TEXT,
  loadDiaryToday,
  restoreFoodEntry,
  type WellnessToday,
} from "../lib/customer-wellness";
import { ApiError } from "../lib/api";
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

  it("DRF-1927: нет согласия — говорим про согласие, не про сбой и не про пустой день", async () => {
    mockedLoad.mockResolvedValue({ state: "consent_required" });
    renderScreen();

    expect(await screen.findByText(DIARY_CONSENT_REQUIRED_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(/Не удалось загрузить дневник/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Пока ничего не записано/)).not.toBeInTheDocument();
    // Повтор ничего не даст — кнопки повтора нет.
    expect(screen.queryByText(/Попробовать снова/)).not.toBeInTheDocument();
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


describe("пустой день не зовёт в неработающий скан (DRF-1839)", () => {
  it("прод: подпись зовёт в чат, кнопки скана нет", async () => {
    // Экран скана под `guardProd` падает в прод-сборке; работающий вход
    // записи — текстовый ввод в чате (DRF-1837).
    vi.stubEnv("DEV", false);
    try {
      mockedLoad.mockResolvedValue({
        state: "empty",
        hideNumbers: false,
        today: today({ calories_eaten: 0 }),
      });
      renderScreen();
      expect(
        await screen.findByText(/Напиши Ayla в чате, что было/),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Добавить приём" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(/через скан/)).not.toBeInTheDocument();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("DEV: кнопка скана остаётся — заглушки там живы", async () => {
    mockedLoad.mockResolvedValue({
      state: "empty",
      hideNumbers: false,
      today: today({ calories_eaten: 0 }),
    });
    renderScreen();
    expect(
      await screen.findByRole("button", { name: "Добавить приём" }),
    ).toBeInTheDocument();
  });

  it("запись из чата (meal_type other) видна в «Другое»", async () => {
    // Текстовый ввод DRF-1837 пишет тип приёма «не указан» — он обязан
    // остаться на экране, а не пропасть из списка.
    mockedLoad.mockResolvedValue({
      state: "entries",
      entries: [{ ...SOUP, id: "fl-chat", dish_name: "борщ", meal_type: "other" }],
      hideNumbers: false,
      today: today(),
    });
    renderScreen();
    expect(await screen.findByText("борщ")).toBeInTheDocument();
    expect(screen.getByText("Другое")).toBeInTheDocument();
  });
});


describe("строка диетолога (DRF-1897)", () => {
  const LINE = "Третий вечер ужин после девяти. Если хочешь, подумаем, что можно сдвинуть.";

  it("пришла — стоит под итогами дословно", async () => {
    mockedLoad.mockResolvedValue({
      state: "entries",
      entries: [OATS],
      hideNumbers: false,
      today: today({ coach_observation: LINE }),
    });
    renderScreen();

    const line = await screen.findByText(LINE);
    const totals = screen.getByRole("region", { name: "Сегодня" });
    // Под итогами: итоги в документе раньше строки.
    expect(
      totals.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("не пришла — ни абзаца", async () => {
    mockedLoad.mockResolvedValue({
      state: "entries",
      entries: [OATS],
      hideNumbers: false,
      today: today(),
    });
    const { container } = renderScreen();

    // POSITIVE first: the ready diary did render.
    expect(await screen.findByText("Овсянка с ягодами")).toBeInTheDocument();
    expect(container.querySelector(".food-scanner-diary__observation")).toBeNull();
  });
});


describe("правка и удаление записи (DRF-1838)", () => {
  const TEXT_ENTRY = {
    ...OATS,
    id: "fl-text",
    dish_name: "Гречка",
    entry_origin: "text_estimated_confirmed",
  };
  const PHOTO_ENTRY = { ...SOUP, id: "fl-photo", entry_origin: "photo_estimated_confirmed" };
  const mockedDelete = vi.mocked(deleteFoodEntry);
  const mockedRestore = vi.mocked(restoreFoodEntry);
  const mockedCorrect = vi.mocked(correctFoodEntryGrams);

  function serveDay(entries: Array<typeof OATS & { entry_origin?: string | null }>) {
    mockedLoad.mockResolvedValue({
      state: "entries",
      entries,
      hideNumbers: false,
      today: today(),
    });
  }

  const DELETION = { entry_id: "fl-text", restore_window_expires_at: "2026-09-15T12:15:00+00:00" };

  it("у каждой записи «Удалить», «Исправить граммы» — только у записи текстом", async () => {
    serveDay([TEXT_ENTRY, PHOTO_ENTRY]);
    renderScreen();

    expect(await screen.findByRole("button", { name: "Удалить: Гречка" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Удалить: Куриный суп" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Исправить граммы: Гречка" })).toBeInTheDocument();
    // У фото-записи порция считается от скана, не от 100 г — «граммы ÷ 100» соврали бы.
    expect(
      screen.queryByRole("button", { name: "Исправить граммы: Куриный суп" }),
    ).not.toBeInTheDocument();
  });

  it("«Удалить» убирает запись, перечитывает день и предлагает вернуть", async () => {
    serveDay([TEXT_ENTRY]);
    mockedDelete.mockResolvedValue(DELETION);
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: "Удалить: Гречка" }));

    expect(await screen.findByText("Убрано: Гречка. Вернуть можно 15 минут.")).toBeInTheDocument();
    expect(mockedDelete).toHaveBeenCalledWith("fl-text");
    expect(mockedLoad).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Вернуть" })).toBeInTheDocument();
  });

  it("«Вернуть» в окне возвращает запись и перечитывает день", async () => {
    serveDay([TEXT_ENTRY]);
    mockedDelete.mockResolvedValue(DELETION);
    mockedRestore.mockResolvedValue("restored");
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: "Удалить: Гречка" }));
    fireEvent.click(await screen.findByRole("button", { name: "Вернуть" }));

    expect(await screen.findByText("Вернула: Гречка.")).toBeInTheDocument();
    expect(mockedRestore).toHaveBeenCalledWith("fl-text");
    expect(mockedLoad).toHaveBeenCalledTimes(3);
  });

  it("после окна — сказано, что удалено окончательно, «Вернуть» больше нет", async () => {
    serveDay([TEXT_ENTRY]);
    mockedDelete.mockResolvedValue(DELETION);
    mockedRestore.mockResolvedValue("expired");
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: "Удалить: Гречка" }));
    fireEvent.click(await screen.findByRole("button", { name: "Вернуть" }));

    expect(
      await screen.findByText("Уже не вернуть: прошло больше 15 минут, запись удалена окончательно."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Вернуть" })).not.toBeInTheDocument();
  });

  it("«Исправить граммы» отправляет граммы и перечитывает день", async () => {
    serveDay([TEXT_ENTRY]);
    mockedCorrect.mockResolvedValue(undefined);
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: "Исправить граммы: Гречка" }));
    fireEvent.change(screen.getByLabelText("Сколько граммов было: Гречка"), {
      target: { value: "250" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText("Исправила: Гречка.")).toBeInTheDocument();
    expect(mockedCorrect).toHaveBeenCalledWith("fl-text", 250);
    expect(mockedLoad).toHaveBeenCalledTimes(2);
  });

  it("граммы вне 10…2000 не отправляются", async () => {
    serveDay([TEXT_ENTRY]);
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: "Исправить граммы: Гречка" }));
    fireEvent.change(screen.getByLabelText("Сколько граммов было: Гречка"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText("Граммы — числом от 10 до 2000.")).toBeInTheDocument();
    expect(mockedCorrect).not.toHaveBeenCalled();
  });

  it("без согласия — сказано, как его дать", async () => {
    serveDay([TEXT_ENTRY]);
    mockedCorrect.mockRejectedValue(new ApiError(403, "consent_required", "consent"));
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: "Исправить граммы: Гречка" }));
    fireEvent.change(screen.getByLabelText("Сколько граммов было: Гречка"), {
      target: { value: "250" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(
      await screen.findByText(
        "Чтобы менять дневник, нужно согласие на обработку личных данных — дай его в чате с Ayla.",
      ),
    ).toBeInTheDocument();
  });

  it("неизвестный исход удаления — не «ничего не изменилось»", async () => {
    serveDay([TEXT_ENTRY]);
    mockedDelete.mockRejectedValue(new ApiError(502, "ayla_uncertain", "timeout"));
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: "Удалить: Гречка" }));

    expect(
      await screen.findByText("Не знаю, дошло ли — обнови дневник, прежде чем повторять."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/ничего не изменилось/)).not.toBeInTheDocument();
  });
});


describe("правка и удаление записи — края из ревью (DRF-1838)", () => {
  const ENTRY = { ...OATS, id: "fl-edge", dish_name: "Гречка", entry_origin: "text_estimated_confirmed" };
  const mockedDelete = vi.mocked(deleteFoodEntry);
  const mockedRestore = vi.mocked(restoreFoodEntry);
  const mockedCorrect = vi.mocked(correctFoodEntryGrams);
  const DELETION = { entry_id: "fl-edge", restore_window_expires_at: "2026-09-15T12:15:00+00:00" };

  function serve() {
    mockedLoad.mockResolvedValue({
      state: "entries",
      entries: [ENTRY],
      hideNumbers: false,
      today: today(),
    });
  }

  it("двойной тап «Удалить» шлёт одно удаление", async () => {
    serve();
    mockedDelete.mockReturnValue(new Promise(() => {}));
    renderScreen();

    const button = await screen.findByRole("button", { name: "Удалить: Гречка" });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(mockedDelete).toHaveBeenCalledTimes(1);
  });

  it("двойной тап «Вернуть» шлёт один возврат", async () => {
    serve();
    mockedDelete.mockResolvedValue(DELETION);
    mockedRestore.mockReturnValue(new Promise(() => {}));
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: "Удалить: Гречка" }));
    const undo = await screen.findByRole("button", { name: "Вернуть" });
    fireEvent.click(undo);
    fireEvent.click(undo);

    expect(mockedRestore).toHaveBeenCalledTimes(1);
  });

  it("неизвестный исход перечитывает день — показать правду, а не прежний список", async () => {
    serve();
    mockedDelete.mockRejectedValue(new ApiError(502, "ayla_uncertain", "timeout"));
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: "Удалить: Гречка" }));

    expect(
      await screen.findByText("Не знаю, дошло ли — обнови дневник, прежде чем повторять."),
    ).toBeInTheDocument();
    expect(mockedLoad).toHaveBeenCalledTimes(2);
  });

  it("отказ каталога — своя фраза, не «ничего не изменилось» и не «не отвечает»", async () => {
    serve();
    mockedCorrect.mockRejectedValue(new ApiError(400, "ayla_bad_request", "rejected"));
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: "Исправить граммы: Гречка" }));
    fireEvent.change(screen.getByLabelText("Сколько граммов было: Гречка"), {
      target: { value: "250" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(
      await screen.findByText("Дневник не принял изменение — проверь запись и попробуй ещё раз."),
    ).toBeInTheDocument();
  });
});
