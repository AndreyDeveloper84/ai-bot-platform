/**
 * DRF-1846 — «ЭТА НЕДЕЛЯ» на дашборде мастера.
 *
 * Заперто: числа рисуются как пришли от сервера, с русским числом у слов;
 * оценка — только когда сервер её прислал (за ней есть отзывы), иначе
 * «Отзывов пока нет»; слова «выручка» на блоке нет — у зеркала броней нет цены.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DashboardWeekSummary } from "../lib/master-api";
import { WeekSection } from "./MasterDashboardScreen";

const WEEK: DashboardWeekSummary = {
  week_start: "2026-05-18",
  week_end: "2026-05-24",
  bookings: 12,
  completed: 9,
  rating: null,
};

function renderWeek(overrides: Partial<DashboardWeekSummary> = {}) {
  return render(<WeekSection summary={{ ...WEEK, ...overrides }} />);
}

describe("WeekSection", () => {
  it("рисует оба числа сервера", () => {
    renderWeek();
    expect(screen.getByText("На этой неделе: 12 записей · 9 состоялось")).toBeInTheDocument();
  });

  it.each([
    [1, "1 запись"],
    [3, "3 записи"],
    [5, "5 записей"],
    [21, "21 запись"],
  ])("число у слова «запись»: %i", (bookings, phrase) => {
    renderWeek({ bookings, completed: 0 });
    expect(screen.getByText(`На этой неделе: ${phrase} · 0 состоялось`)).toBeInTheDocument();
  });

  it("без отзывов оценки нет — только «Отзывов пока нет»", () => {
    const { container } = renderWeek({ rating: null });
    expect(screen.getByText("Отзывов пока нет")).toBeInTheDocument();
    expect(container.textContent).not.toContain("★");
  });

  it("оценка с числом отзывов, когда сервер её прислал", () => {
    renderWeek({ rating: { value: 4.8, review_count: 12 } });
    expect(screen.getByText("★ 4.8 · 12 отзывов")).toBeInTheDocument();
    expect(screen.queryByText("Отзывов пока нет")).not.toBeInTheDocument();
  });

  it("один отзыв — «1 отзыв»", () => {
    renderWeek({ rating: { value: 5, review_count: 1 } });
    expect(screen.getByText("★ 5.0 · 1 отзыв")).toBeInTheDocument();
  });

  it("выручки на блоке нет", () => {
    const { container } = renderWeek({ rating: { value: 4.8, review_count: 12 } });
    expect(container.textContent?.toLowerCase()).not.toMatch(/выручк|₽/);
  });
});
