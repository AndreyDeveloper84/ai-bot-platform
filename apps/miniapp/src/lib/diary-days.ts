/**
 * Дневник за неделю — клиент Mini App (DRF-2099).
 *
 * Источник — каталог через прокси бота (`customer/diary/days`,
 * `customer/diary/day`): границы суток считает каталог по поясу человека
 * (`NutritionProfile.timezone`, иначе UTC), и здесь дата — строка
 * `YYYY-MM-DD` ИЗ ОТВЕТА, а не из часов устройства. «Сегодня» для
 * стрелок — `to` первой (беспараметровой) недели: её конец каталог ставит
 * на сегодня в поясе человека.
 *
 * Арифметика дат ниже — по календарю, без часовых поясов (UTC-полночь как
 * носитель числа): сдвиг на N дней не может «перескочить» день из-за
 * перевода часов на устройстве.
 *
 * Чего здесь нет: напоминаний, серий и «пропущенных дней» — по В-5
 * (DRF-1332) экран показывает только факт «N из 7 дней с записями».
 */
import { request } from "./api";
import type { FoodDiaryEntry } from "./customer-wellness";

export interface DiaryDayRow {
  /** `YYYY-MM-DD` в поясе человека (каталог). */
  date: string;
  meals_count: number;
  /** `null` — записей нет. */
  kcal: number | null;
  has_entries: boolean;
}

export interface DiaryDays {
  timezone: string;
  from: string;
  to: string;
  days: DiaryDayRow[];
  /**
   * Тот же признак, что у `wellness/today`: отсутствие ключа ПРЯЧЕТ
   * числа (fail-closed, §10 Appendix ED Mode).
   */
  nutrition_numbers_hidden?: boolean;
}

export interface DiaryDay {
  date: string;
  calories_total: number;
  entries: FoodDiaryEntry[];
  nutrition_numbers_hidden?: boolean;
}

/** Длина недели экрана — семь дней, конец включительно. */
export const WEEK_DAYS = 7;

/**
 * Предел стрелки «раньше» — по пределу ручки каталога (28 дней): четыре
 * недели включая текущую. Дальше кнопка неактивна, а не ошибка.
 */
export const MAX_BACK_DAYS = 28;

export async function getDiaryDays(from?: string, to?: string): Promise<DiaryDays> {
  const q = new URLSearchParams();
  if (from) q.set("from", from);
  if (to) q.set("to", to);
  const qs = q.toString();
  return request<DiaryDays>(qs ? `/diary/days?${qs}` : "/diary/days");
}

export async function getDiaryDay(date: string): Promise<DiaryDay> {
  return request<DiaryDay>(`/diary/day?${new URLSearchParams({ date }).toString()}`);
}

// ── календарная арифметика (чистая) ─────────────────────────────────────────

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

function toUtcMidnight(iso: string): number {
  const m = ISO_DATE.exec(iso);
  if (!m) throw new Error(`not an ISO date: ${iso}`);
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

function fromUtcMidnight(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** `2026-09-19` + (−7) → `2026-09-12`; по календарю, без пояса. */
export function shiftDays(iso: string, days: number): string {
  return fromUtcMidnight(toUtcMidnight(iso) + days * 86_400_000);
}

/** Неделя, которая заканчивается днём `to` (включительно). */
export function weekEndingAt(to: string): { from: string; to: string } {
  return { from: shiftDays(to, -(WEEK_DAYS - 1)), to };
}

/** Самый ранний допустимый `from` при «сегодня» = `today`. */
export function earliestFrom(today: string): string {
  return shiftDays(today, -(MAX_BACK_DAYS - 1));
}

/** Можно ли листать раньше: следующая неделя назад не выйдет за предел. */
export function canGoEarlier(from: string, today: string): boolean {
  return toUtcMidnight(shiftDays(from, -WEEK_DAYS)) >= toUtcMidnight(earliestFrom(today));
}

/** Можно ли листать позже: неделя ещё не упирается в сегодня. */
export function canGoLater(to: string, today: string): boolean {
  return toUtcMidnight(to) < toUtcMidnight(today);
}

/** Позже — не дальше сегодняшнего дня. */
export function laterWeek(to: string, today: string): { from: string; to: string } {
  const next = shiftDays(to, WEEK_DAYS);
  return weekEndingAt(toUtcMidnight(next) > toUtcMidnight(today) ? today : next);
}

export function earlierWeek(from: string): { from: string; to: string } {
  return weekEndingAt(shiftDays(from, -1));
}

const WEEKDAY_SHORT = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];

/** `2026-09-14` → «Пн, 14.09». */
export function dayLabel(iso: string): string {
  const ms = toUtcMidnight(iso);
  const d = new Date(ms);
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  return `${WEEKDAY_SHORT[d.getUTCDay()]}, ${dd}.${mm}`;
}

/** «14.09 – 20.09». */
export function periodLabel(from: string, to: string): string {
  const short = (iso: string) => {
    const d = new Date(toUtcMidnight(iso));
    return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
  };
  return `${short(from)} – ${short(to)}`;
}
