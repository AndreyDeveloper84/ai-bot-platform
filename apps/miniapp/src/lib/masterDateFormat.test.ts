/**
 * Форматтеры длительности и даты для мастерских экранов (DRF-2156, М-4;
 * решение владельца §61 по М-1: «До визита N мин» → «До визита 1 ч 20 мин»
 * по макету DRF-1185 — один helper на «Сегодня» и на «Детали записи»).
 *
 * Метки времени — без смещения: читаются как локальные, так ожидания верны
 * и в МСК, и на UTC-раннере (урок #1892).
 */
import { describe, expect, it } from "vitest";

import {
  formatDateDotWeekdayRu,
  formatDurationRu,
  formatUpcomingAtRu,
} from "./masterDateFormat";

describe("formatDurationRu — «1 ч 20 мин» по макету DRF-1185", () => {
  it.each([
    [0, "0 мин"],
    [45, "45 мин"],
    [59, "59 мин"],
    [60, "1 ч"],
    [61, "1 ч 1 мин"],
    [80, "1 ч 20 мин"],
    [120, "2 ч"],
    [150, "2 ч 30 мин"],
  ])("%i → «%s»", (min, expected) => {
    expect(formatDurationRu(min)).toBe(expected);
  });

  it("отрицательное и нецелое — не падает, округляет вниз к нулю", () => {
    expect(formatDurationRu(-5)).toBe("0 мин");
    expect(formatDurationRu(80.7)).toBe("1 ч 20 мин");
  });
});

describe("formatDateDotWeekdayRu — «20 августа · среда»", () => {
  it("день, месяц в родительном, день недели строчными", () => {
    expect(formatDateDotWeekdayRu("2026-08-20T15:30:00")).toBe("20 августа · четверг");
    expect(formatDateDotWeekdayRu("2026-09-20T12:00:00")).toBe("20 сентября · воскресенье");
  });

  it("мусор возвращается как есть", () => {
    expect(formatDateDotWeekdayRu("not-a-date")).toBe("not-a-date");
  });
});

describe("formatUpcomingAtRu — «Сегодня в 15:30» / «Завтра в …» / «21 августа в …»", () => {
  const now = "2026-08-20T14:10:00";
  it("тот же календарный день — «Сегодня»", () => {
    expect(formatUpcomingAtRu("2026-08-20T15:30:00", now)).toBe("Сегодня в 15:30");
  });
  it("следующий день — «Завтра»", () => {
    expect(formatUpcomingAtRu("2026-08-21T09:00:00", now)).toBe("Завтра в 09:00");
  });
  it("дальше — дата словами", () => {
    expect(formatUpcomingAtRu("2026-08-25T15:30:00", now)).toBe("25 августа в 15:30");
  });
  it("прошлое (часы устройства ушли вперёд сервера) — дата словами, без «Сегодня»-лжи", () => {
    expect(formatUpcomingAtRu("2026-08-19T15:30:00", now)).toBe("19 августа в 15:30");
  });
});
