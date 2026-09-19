/**
 * Записи одного дня — экран (DRF-2099).
 *
 * Предмет: дата из маршрута уходит в запрос как есть; записи дня — по
 * приёмам с временем и блюдом, калории под ED-признаком; пустой день —
 * «в этот день записей нет» (не ошибка); отказы — по слагу; кнопок
 * правки/удаления прошлых записей здесь НЕТ — это предел экрана.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/diary-days", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/diary-days")>();
  return { ...original, getDiaryDay: vi.fn() };
});

import { ApiError } from "../lib/api";
import { getDiaryDay, type DiaryDay } from "../lib/diary-days";
import { DAY_COPY, DAY_ROUTE_PATTERN, FoodScannerDayScreen, dayRoute } from "./FoodScannerDayScreen";
import { WEEK_ROUTE } from "./FoodScannerWeekScreen";

const mockedDay = vi.mocked(getDiaryDay);

const OATS = {
  id: "fl-1",
  dish_name: "Овсянка с ягодами",
  calories: 320,
  protein_g: 11,
  fat_g: 6,
  carbs_g: 54,
  meal_type: "breakfast",
  logged_at: "2026-09-14T05:31:00Z",
  entry_origin: "text_estimated_confirmed",
};
const SOUP = { ...OATS, id: "fl-2", dish_name: "Борщ", calories: 250, meal_type: "lunch" };

const DAY: DiaryDay = {
  date: "2026-09-14",
  calories_total: 570,
  entries: [OATS, SOUP],
  nutrition_numbers_hidden: false,
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderScreen(date = "2026-09-14") {
  return render(
    <MemoryRouter initialEntries={[dayRoute(date)]}>
      <Routes>
        <Route path={DAY_ROUTE_PATTERN} element={<FoodScannerDayScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedDay.mockResolvedValue(DAY);
});

describe("день", () => {
  it("дата из маршрута — в запрос; записи по приёмам с блюдом и калориями", async () => {
    renderScreen();
    await screen.findByText("Овсянка с ягодами");

    expect(mockedDay).toHaveBeenCalledWith("2026-09-14");
    expect(screen.getByText("Борщ")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Завтрак/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Обед/ })).toBeInTheDocument();
    expect(screen.getByText(DAY_COPY.kcal(320))).toBeInTheDocument();
    expect(screen.getByText(DAY_COPY.title("2026-09-14"))).toBeInTheDocument();
  });

  it("под ED-признаком калорий нет", async () => {
    mockedDay.mockResolvedValue({ ...DAY, nutrition_numbers_hidden: true });
    renderScreen();
    await screen.findByText("Овсянка с ягодами");

    expect(screen.queryByText(/ккал/)).toBeNull();
  });

  it("пустой день — «записей нет», не ошибка и не повтор", async () => {
    mockedDay.mockResolvedValue({ ...DAY, entries: [] });
    renderScreen("2026-09-13");

    expect(await screen.findByText(DAY_COPY.empty)).toBeInTheDocument();
    expect(mockedDay).toHaveBeenCalledWith("2026-09-13");
    expect(screen.queryByRole("button", { name: DAY_COPY.retry })).toBeNull();
  });

  it("правки и удаления прошлых записей здесь нет — предел экрана", async () => {
    renderScreen();
    await screen.findByText("Овсянка с ягодами");

    expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole("button", { name: /удалить/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /исправить/i })).toBeNull();
  });

  it("кривая дата в маршруте — отказ до запроса", async () => {
    renderScreen("14.09.2026");
    await settle();

    expect(await screen.findByText(DAY_COPY.badDate)).toBeInTheDocument();
    expect(mockedDay).not.toHaveBeenCalled();
  });

  it("назад — на неделю", async () => {
    renderScreen();
    await screen.findByText("Овсянка с ягодами");

    fireEvent.click(screen.getByRole("button", { name: "Назад" }));

    expect(await screen.findByTestId("location")).toHaveTextContent(WEEK_ROUTE);
  });

  it("consent_required — на ворота согласия", async () => {
    mockedDay.mockRejectedValue(new ApiError(403, "consent_required", "consent"));
    renderScreen();

    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/customer/food-scanner/capture",
    );
  });

  it("ayla_unavailable — фраза и «Повторить»", async () => {
    mockedDay.mockRejectedValueOnce(new ApiError(502, "ayla_unavailable", "down"));
    renderScreen();

    expect(await screen.findByText(DAY_COPY.unavailable)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: DAY_COPY.retry }));
    await screen.findByText("Овсянка с ягодами");
    expect(mockedDay).toHaveBeenCalledTimes(2);
  });
});
