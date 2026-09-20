/**
 * Срок цели на экранах клиента (DRF-2173, H01-2).
 *
 * Источник — `known.goal.target_date` (ISO-дата) и `target_date_passed`
 * (факт сервера) из decision-context каталога; Главная получает то же
 * через `wellness/today.active_goals[]`. Здесь только представление:
 * «До 1 ноября 2026» — как на макете DRF-1321 v1.2 — и извлечение слов
 * отказа каталога («Этот срок уже прошёл…») из 400, который бот-прокси
 * пробрасывает как есть.
 *
 * Чего здесь нет по решению владельца (лист п.4): напоминаний, процентов
 * «времени прошло», пересчёта плана по сроку.
 */
import { ApiError } from "./api";
import { MONTHS_GEN } from "./format";

/** Шаг анкеты, на котором спрашивается срок (каталог `goals/anketa.py`). */
export const DEADLINE_STEP = "deadline";
/** Плейсхолдер поля текста на шаге срока — чипа «своя дата» нет (сторож C03 DRF-1751). */
export const DEADLINE_TEXT_PLACEHOLDER = "например, до 1 ноября";
/** Строка экрана цели при прошедшем сроке (текст листа DRF-2173). */
export const DEADLINE_PASSED_CTA = "Срок прошёл — обновить?";

/** «До 1 ноября 2026» — как на макете; пустая строка, если дата нечитаема. */
export function formatGoalDue(iso: string | null | undefined): string {
  // Дословно по макету: «До 1 ноября 2026» — без « г.», которое дописывает
  // `toLocaleDateString`; месяц — в родительном, как у остальных дат экрана.
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  if (!m) return "";
  const [, y, mo, d] = m;
  const month = MONTHS_GEN[Number(mo) - 1];
  if (!month) return "";
  return `До ${Number(d)} ${month} ${y}`;
}

/**
 * Слова отказа каталога на ответ шага (400 `VALIDATION_ERROR`): бот-прокси
 * кладёт тело каталога в `details.ayla_error`; каталог кладёт фразу в
 * `error.details.answer.text[]`. Нет фразы — `null`, и экран говорит
 * общее «Не получилось отправить».
 */
export function answerRefusalText(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 400) return null;
  const ayla = err.details?.ayla_error as { error?: { details?: { answer?: { text?: unknown } } } } | undefined;
  const text = ayla?.error?.details?.answer?.text;
  const first = Array.isArray(text) ? text[0] : text;
  return typeof first === "string" && first.trim() ? first : null;
}
