/**
 * Раскрытие дневника питания на экране согласия — Z9 (решение владельца).
 *
 * # Что этот файл сторожит
 *
 * Z9 — не имя сущности в коде, а метка разрыва: у обработки данных дневника
 * не было canonical user-facing disclosure. Два условия Gate A ложатся сюда:
 *
 *   * UI показывает **именно текущую** версию раскрытия — не «какую-то»;
 *   * старое обещание «удаляю фото сразу после распознавания» **отсутствует**.
 *
 * # Почему второй узел — про отсутствие, и почему это законно
 *
 * Обычно утверждение отсутствия ничего не стоит: оно зелено и когда экран
 * пуст. Поэтому присутствие проверяется первым и на тех же данных —
 * сначала убеждаемся, что блок согласия отрисован и текст раскрытия на
 * месте, и лишь потом требуем отсутствия запрещённой формулировки.
 *
 * Запрет не косметический. Владелец назвал его прямо: фотография может
 * храниться до 30 дней (политика хранилища), и обещать немедленное
 * удаление значит утверждать действие, которого код не совершает.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return {
    ...original,
    // Экран спрашивает согласие ОДНИМ вызовом, который сам решает, какой
    // путь живой (F10). Мокать `fetchConsentAt` теперь бессмысленно: экран
    // его не зовёт, и тест молча уходил бы в настоящую сеть.
    fetchDiaryConsentGate: vi.fn(),
    grantConsent: vi.fn(),
  };
});

import { fetchDiaryConsentGate } from "../lib/food-scanner";
import {
  BUTTON_DECLINE,
  BUTTON_GRANT,
  DISCLOSURE_BODY,
  DISCLOSURE_HEADLINE,
  FOOD_DIARY_DISCLOSURE_VERSION,
} from "../lib/food-diary-disclosure";
import { FoodScannerCaptureScreen } from "./FoodScannerCaptureScreen";

const mockedFetch = vi.mocked(fetchDiaryConsentGate);

/** Согласия нет, путь канонический — то состояние, в котором виден гейт. */
const NO_CONSENT_CANONICAL = {
  canonical: true,
  grantedAt: null,
  currentDocumentVersion: FOOD_DIARY_DISCLOSURE_VERSION,
};

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/food-scanner/capture"]}>
      <Routes>
        <Route
          path="/customer/food-scanner/capture"
          element={<FoodScannerCaptureScreen />}
        />
        <Route path="/customer/main" element={<div>ДОМ</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("экран показывает ТЕКУЩЕЕ раскрытие", () => {
  it("версия раскрытия на экране совпадает с текущей константой", async () => {
    mockedFetch.mockResolvedValue(NO_CONSENT_CANONICAL); // гейт показывается
    renderScreen();

    // Ждём по ЗАГОЛОВКУ, а не по кнопке: узел про версию не должен
    // краснеть из-за написания кнопки — у неё свой узел ниже.
    expect(await screen.findByText(DISCLOSURE_HEADLINE)).toBeInTheDocument();

    // Версия — не украшение: человек согласился на ТОТ текст, который
    // ему показали, и запись согласия обязана нести ту же строку.
    expect(
      await screen.findByTestId("food-diary-disclosure-version"),
    ).toHaveTextContent(FOOD_DIARY_DISCLOSURE_VERSION);
  });

  it("на экране сказано всё, что обязано быть сказано", async () => {
    mockedFetch.mockResolvedValue(NO_CONSENT_CANONICAL);
    const { container } = renderScreen();
    await screen.findByText(DISCLOSURE_HEADLINE);

    // Требование владельца — не «шесть абзацев», а «текст честно говорит
    // вот эти вещи». Счёт абзацев был прокси для содержания и ошибался в
    // обе стороны: свёл бы два утверждения в один абзац — текст верен, а
    // узел красен. Поэтому спрашиваем СМЫСЛ, и спрашиваем у ЭКРАНА.
    const shown = (container.textContent ?? "").toLowerCase();
    for (const required of [
      "текстом", // еда и текстом, и фотографией
      "фотограф",
      "распозна", // зачем нужен снимок
      "30 дней", // сколько он живёт
      "отдельно", // запись хранится отдельно от снимка
      "отозвать", // право на отзыв
      "после отзыва", // и что происходит после
      "новой обработки",
    ]) {
      expect(shown).toContain(required);
    }

    // И каждый абзац источника действительно отрисован — иначе смысл мог
    // бы найтись в одном месте, а остальной текст молча не дойти.
    for (const paragraph of DISCLOSURE_BODY) {
      expect(screen.getByText(paragraph)).toBeInTheDocument();
    }
  });
});

describe("запрещённое обещание немедленного удаления", () => {
  it("присутствие — потом отсутствие: блок есть, а «сразу» в нём нет", async () => {
    mockedFetch.mockResolvedValue(NO_CONSENT_CANONICAL);
    const { container } = renderScreen();

    // ПРИСУТСТВИЕ на тех же данных: иначе отсутствие ничего не значит.
    await screen.findByText(DISCLOSURE_HEADLINE);
    expect(container.textContent).toContain(DISCLOSURE_BODY[0]);

    // ОТСУТСТВИЕ: формулировки, которую владелец запретил пунктом 3.
    expect(container.textContent).not.toMatch(/сразу после распозна/i);
    expect(container.textContent).not.toMatch(/удаля\w* фото сразу/i);
  });
});

describe("флаг выключен — канонический текст НЕ показывается", () => {
  /**
   * WORKING PRODUCT COPY: до Privacy/Legal review раскрытие Z9 не должно
   * доехать до живых людей включением по умолчанию. Сервер объявляет канон
   * только под флагом `FOOD_DIARY_CANONICAL_CONSENT`; без поля в `/me`
   * экран обязан показывать нынешний короткий текст — слово в слово.
   */
  it("без канона на экране старый текст, и ни абзаца раскрытия", async () => {
    mockedFetch.mockResolvedValue({
      canonical: false,
      grantedAt: null,
      currentDocumentVersion: "",
    });
    const { container } = renderScreen();

    // ПРИСУТСТВИЕ: гейт отрисован, и это СТАРЫЙ текст — литералом, не
    // константой: сверка с константой сторожила бы модуль сам с собой.
    expect(
      await screen.findByText("Можно показать тебе фото-скан?"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Хорошо, разрешаю" }),
    ).toBeInTheDocument();

    // ОТСУТСТВИЕ на тех же данных: ни заголовка, ни одного абзаца Z9, ни
    // строки версии.
    expect(screen.queryByText(DISCLOSURE_HEADLINE)).toBeNull();
    for (const paragraph of DISCLOSURE_BODY) {
      expect(screen.queryByText(paragraph)).toBeNull();
    }
    expect(screen.queryByTestId("food-diary-disclosure-version")).toBeNull();
    expect(container.textContent).not.toContain("30 дней");
  });
});

describe("кнопки названы дословно", () => {
  it("на экране «Разрешить» и «Не сейчас», а не их пересказ", async () => {
    mockedFetch.mockResolvedValue(NO_CONSENT_CANONICAL);
    renderScreen();
    await screen.findByText(DISCLOSURE_HEADLINE);

    // Дословность здесь не косметика. Владелец назвал ровно два слова, а
    // проверка по /разреш/i зеленела бы и на «Хорошо, разрешаю» — то есть
    // сторожила бы написание, которого решение не содержит, и пропустила
    // бы ровно тот дефект, ради которого её ставили.
    // Слова владельца — ЛИТЕРАЛОМ. Сверять экран с константой значило бы
    // сверять модуль с самим собой: правка константы поменяла бы разом и
    // ожидание, и то, что рисует экран. Проба это показала — узел молчал,
    // когда я вернул в константу прежнее «Хорошо, разрешаю».
    expect(BUTTON_GRANT).toBe("Разрешить");
    expect(BUTTON_DECLINE).toBe("Не сейчас");
    expect(
      screen.getByRole("button", { name: "Разрешить" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Не сейчас" }),
    ).toBeInTheDocument();
  });
});
