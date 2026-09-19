/**
 * DRF-2108 — окно возврата записи с провода (`restore_window_expires_at`),
 * не константа экрана: `null` без поля, при кривой дате и когда окно уже
 * закрылось; минуты — вверх («ещё 15», а не «14» при 14:30).
 */
import { describe, expect, it } from "vitest";

import { restoreWindowMinutesLeft, minutesRu } from "./restore-window";

const NOW = new Date("2026-09-19T12:00:00Z");
const at = (seconds: number) => new Date(NOW.getTime() + seconds * 1000).toISOString();

describe("restoreWindowMinutesLeft", () => {
  it("считает минуты вверх от «сейчас»", () => {
    expect(restoreWindowMinutesLeft(at(14 * 60 + 30), NOW)).toBe(15);
    expect(restoreWindowMinutesLeft(at(60), NOW)).toBe(1);
    expect(restoreWindowMinutesLeft(at(1), NOW)).toBe(1);
  });

  it("без поля, с кривой датой и после окна — null", () => {
    expect(restoreWindowMinutesLeft(null, NOW)).toBeNull();
    expect(restoreWindowMinutesLeft(undefined, NOW)).toBeNull();
    expect(restoreWindowMinutesLeft("not-a-date", NOW)).toBeNull();
    expect(restoreWindowMinutesLeft(at(-1), NOW)).toBeNull();
  });
});

describe("minutesRu", () => {
  it("склоняет", () => {
    expect(minutesRu(1)).toBe("1 минуту");
    expect(minutesRu(4)).toBe("4 минуты");
    expect(minutesRu(15)).toBe("15 минут");
    expect(minutesRu(21)).toBe("21 минуту");
  });
});
