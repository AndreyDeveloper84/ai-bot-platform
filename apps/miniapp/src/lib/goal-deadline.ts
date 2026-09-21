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
  // Календарная проверка: «31 февраля» с экрана цели документ приносит
  // напрямую, мимо фильтра бота — такую строку не рисуем.
  const probe = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d)));
  if (!month || probe.getUTCMonth() !== Number(mo) - 1 || probe.getUTCDate() !== Number(d)) return "";
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
  // Прокси бота сводит любой 4xx каталога к 400: протухший документ (409
  // ANKETA_STEP_MISMATCH) обязан по-прежнему перечитываться, поэтому слова
  // берутся только у настоящей валидации каталога.
  const details = err.details ?? {};
  if (details.ayla_status !== undefined && details.ayla_status !== 400) return null;
  const ayla = details.ayla_error as
    | { error?: { code?: string; details?: { answer?: { text?: unknown } } } }
    | undefined;
  if (ayla?.error?.code !== "VALIDATION_ERROR") return null;
  const text = ayla.error.details?.answer?.text;
  const first = Array.isArray(text) ? text[0] : text;
  return typeof first === "string" && first.trim() ? first.trim() : null;
}
