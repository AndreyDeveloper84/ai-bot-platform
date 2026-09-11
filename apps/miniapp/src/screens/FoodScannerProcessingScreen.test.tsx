/**
 * F2 — экран отказа не врёт о природе отказа.
 *
 * На боевом контуре `/api/v1/customer/food/{scan,log,daily}` отвечают
 * 404, и `food-scanner.ts::guardProd` вне DEV бросает
 * `StubNotWiredError`. Экран ловил это общей веткой и писал «Сервис
 * распознавания временно недоступен. Попробуй через минуту» — то есть
 * называл отсутствие ручки временным сбоем и звал человека вернуться к
 * тому, чего нет. Через минуту ничего не изменится: менять нечего.
 *
 * Стража парная (`negative_assert_guard`, DRF-1411): к отрицательной
 * проверке «обещания „через минуту“ больше нет» приложены положительные
 * на тех же данных — настоящие сбои (не распознала / фото не скачалось)
 * сохранили свои тексты и свои действия. Правка, которая переписала бы
 * ВСЕ ветки в «не подключено», прошла бы отрицательную и упала на них.
 *
 * Тест умеет падать: уберите ветку `isNotWired` из `headline`/`body` —
 * покраснеет первый случай; снимите `!isNotWired` с кнопок — покраснеет
 * второй.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return { ...original, scanPhoto: vi.fn() };
});

import {
  FoodNotRecognizedError,
  PhotoBytesMissingError,
  StubNotWiredError,
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
  // jsdom не умеет createObjectURL — экран рисует превью через него.
  Object.defineProperty(URL, "createObjectURL", {
    value: () => "blob:preview",
    writable: true,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    value: () => undefined,
    writable: true,
  });
});

describe("ручки нет — так и сказано", () => {
  it("не называет отсутствие ручки временным сбоем и не зовёт «через минуту»", async () => {
    mockedScan.mockRejectedValue(new StubNotWiredError());
    renderScreen();
    expect(
      await screen.findByRole("heading", { name: "Пока не подключено" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/временно недоступен/)).not.toBeInTheDocument();
    expect(screen.queryByText(/через минуту/)).not.toBeInTheDocument();
  });

  it("не предлагает действий, которые упрутся в ту же стену", async () => {
    mockedScan.mockRejectedValue(new StubNotWiredError());
    renderScreen();
    await screen.findByRole("heading", { name: "Пока не подключено" });
    // `logMeal` закрыт тем же guardProd — «Написать вручную» ведёт туда же.
    expect(
      screen.queryByRole("button", { name: "Написать вручную" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Переснять" }),
    ).not.toBeInTheDocument();
    // Положительная стража: выход с экрана остался.
    expect(
      screen.getByRole("button", { name: "Назад на главную" }),
    ).toBeInTheDocument();
  });
});

describe("положительная стража: настоящие сбои не переписаны", () => {
  it("«не разобралась» сохраняет свой текст и оба действия", async () => {
    mockedScan.mockRejectedValue(new FoodNotRecognizedError());
    renderScreen();
    expect(
      await screen.findByRole("heading", { name: "Не разобралась" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Переснять" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Написать вручную" }),
    ).toBeInTheDocument();
  });

  it("«фото не скачалось» сохраняет свой текст и своё действие", async () => {
    mockedScan.mockRejectedValue(new PhotoBytesMissingError());
    renderScreen();
    expect(
      await screen.findByRole("heading", { name: "Не получилось загрузить" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Сделать заново" }),
    ).toBeInTheDocument();
  });
});
