/**
 * F2 — отказ по бюджету распознавания не притворяется сбоем (DRF-2195).
 *
 * Каталог отвечает на скан двумя ШТАТНЫМИ отказами: 429 «личный потолок
 * на сутки» и 503 «общий дневной бюджет». Оба сегодня падают в общую
 * ветку экрана и получают текст «Сервис распознавания временно
 * недоступен. Попробуй через минуту» — ровно та ложь о природе отказа,
 * которую уже разбирали на `StubNotWiredError` (DRF-1546) и на
 * `photo_scan_disabled` (DRF-2109): через минуту ничего не изменится,
 * потому что меняться нечему — счёт снимется в полночь.
 *
 * И главное: дорога рядом и работает — записать еду словами. Экран
 * обязан её назвать и увести туда, а не звать возвращаться к фото.
 *
 * Стража парная (`negative_assert_guard`): к отрицательным проверкам
 * («через минуту» нет, «Переснять» нет) приложены положительные на тех
 * же данных — настоящая недоступность каталога сохранила свой текст и
 * свои кнопки. Правка, переписавшая бы ВСЕ ветки в «напиши словами»,
 * прошла бы отрицательные и упала на положительной.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return { ...original, scanPhoto: vi.fn() };
});

import {
  NutritionUnavailableError,
  ScanBudgetExhaustedError,
  ScanDailyLimitError,
  scanPhoto,
} from "../lib/food-scanner";
import { FoodScannerProcessingScreen } from "./FoodScannerProcessingScreen";

const mockedScan = vi.mocked(scanPhoto);

function renderScreen() {
  const photo = new File(["x"], "meal.jpg", { type: "image/jpeg" });
  render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/customer/food-scanner/processing",
          state: { photo, mealType: "lunch" },
        },
      ]}
    >
      <Routes>
        <Route
          path="/customer/food-scanner/processing"
          element={<FoodScannerProcessingScreen />}
        />
        <Route
          path="/customer/food-scanner/manual"
          element={<div>MANUAL-PROBE</div>}
        />
        <Route
          path="/customer/food-scanner/capture"
          element={<div>CAPTURE-PROBE</div>}
        />
        <Route path="/customer/main" element={<div>HOME-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(URL, "createObjectURL", {
    value: () => "blob:preview",
    writable: true,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    value: () => undefined,
    writable: true,
  });
});

describe("бюджет распознавания исчерпан — DRF-2195", () => {
  it("личный потолок на сутки: «сегодня», дорога словами, без «через минуту»", async () => {
    mockedScan.mockRejectedValue(new ScanDailyLimitError());
    renderScreen();

    expect(
      await screen.findByText("Сегодня фото не распознаю"),
    ).toBeInTheDocument();
    // Положительная часть: назван срок и названа рабочая дорога.
    expect(screen.getByText(/словами/)).toBeInTheDocument();
    // Отрицательная: не обещаем «через минуту» и не зовём переснимать —
    // ещё один снимок упрётся в тот же счётчик.
    expect(screen.queryByText(/через минуту/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Переснять" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Написать вручную" }),
    ).toBeInTheDocument();
  });

  it("общий дневной бюджет: свой текст, та же дорога словами", async () => {
    mockedScan.mockRejectedValue(new ScanBudgetExhaustedError());
    renderScreen();

    expect(
      await screen.findByText("Распознавание фото недоступно"),
    ).toBeInTheDocument();
    expect(screen.getByText(/словами/)).toBeInTheDocument();
    expect(screen.queryByText(/через минуту/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Переснять" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Написать вручную" }),
    ).toBeInTheDocument();
  });

  it("два отказа бюджета различимы между собой — в обе стороны", async () => {
    // Пара проверяется с двух концов: односторонний узел («у суточного не
    // тот заголовок») прошёл бы и на экране, который для ОБОИХ отказов
    // рисует текст суточного.
    mockedScan.mockRejectedValue(new ScanDailyLimitError());
    renderScreen();
    expect(
      await screen.findByText("Сегодня фото не распознаю"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Распознавание фото недоступно"),
    ).not.toBeInTheDocument();

    cleanup();
    mockedScan.mockRejectedValue(new ScanBudgetExhaustedError());
    renderScreen();
    expect(
      await screen.findByText("Распознавание фото недоступно"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Сегодня фото не распознаю"),
    ).not.toBeInTheDocument();
  });

  it("положительная пара: настоящая недоступность — прежний текст и «Переснять»", async () => {
    mockedScan.mockRejectedValue(new NutritionUnavailableError());
    renderScreen();

    expect(await screen.findByText("Сервис недоступен")).toBeInTheDocument();
    expect(screen.getByText(/через минуту/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Переснять" }),
    ).toBeInTheDocument();
  });

  it("у экрана отказа остаётся уход: правило #1918 (ошибка не снимает навигацию)", async () => {
    mockedScan.mockRejectedValue(new ScanDailyLimitError());
    renderScreen();

    expect(
      await screen.findByText("Сегодня фото не распознаю"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Назад на главную" }),
    ).toBeInTheDocument();
  });
});
