/**
 * Форматы дат раздела «Записи» клиента (DRF-2952) — живут в `lib/format.ts`,
 * единственном доме разбора времени салона (DRF-2589): часы берутся из
 * строки, сервер отдаёт время визита в поясе салона записи.
 */
import { describe, expect, it } from "vitest";

import { formatDayWeekday, formatMonthYear, formatRecordWhen, UNKNOWN_MARK } from "./format";

const NOW = new Date("2026-11-11T10:00:00+03:00");

describe("formatRecordWhen — «22 сентября, 14:00»", () => {
  it("часы — из строки, а не из пояса устройства", () => {
    expect(formatRecordWhen("2026-09-22T14:00:00+03:00", NOW)).toBe("22 сентября, 14:00");
    expect(formatRecordWhen("2026-09-22T14:00:00+07:00", NOW)).toBe("22 сентября, 14:00");
  });

  it("год дописан, когда он не текущий, — прошлогодняя запись не читается как нынешняя", () => {
    expect(formatRecordWhen("2025-09-12T16:00:00+03:00", NOW)).toBe("12 сентября 2025, 16:00");
    expect(formatRecordWhen("2027-01-05T09:30:00+03:00", NOW)).toBe("5 января 2027, 09:30");
  });

  it("чужая форма — прочерк, а не обрывок строки", () => {
    expect(formatRecordWhen("скоро", NOW)).toBe(UNKNOWN_MARK);
    expect(formatRecordWhen("2026-13-01T10:00:00+03:00", NOW)).toBe(UNKNOWN_MARK);
  });
});

describe("formatDayWeekday — «22 сентября, понедельник»", () => {
  it("день недели — по дате из строки", () => {
    expect(formatDayWeekday("2026-09-22T14:00:00+03:00", NOW)).toBe("22 сентября, вторник");
    expect(formatDayWeekday("2026-09-27T23:30:00+03:00", NOW)).toBe("27 сентября, воскресенье");
  });

  it("год — по тому же правилу", () => {
    expect(formatDayWeekday("2025-09-12T16:00:00+03:00", NOW)).toBe("12 сентября 2025, пятница");
  });
});

describe("formatMonthYear — заголовок недели в переносе", () => {
  it("«Сентябрь 2026»", () => {
    expect(formatMonthYear("2026-09-24")).toBe("Сентябрь 2026");
    expect(formatMonthYear("2027-01-01")).toBe("Январь 2027");
    expect(formatMonthYear("")).toBe("");
  });
});
