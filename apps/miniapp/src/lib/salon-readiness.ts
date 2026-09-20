/**
 * Готовность салона на «Сегодня» (DRF-2117, §50 п.4) — чистые правила.
 *
 * Источник — `GET /api/v1/admin/readiness/` (#1878). Правило карточки —
 * контракт ayla-22 (20.09): `source_problem != null` → «не удалось
 * проверить» (счёта нет, числа нет); иначе `ready` → «салон готов»; иначе
 * N = `problems.length`; N == 0 при `unknown` — тоже «не удалось проверить»
 * (форма, которую каталог не производит, но «готов» из неё не выводится).
 *
 * Число — только из ответа: сбой ручки — «не удалось проверить», не ноль.
 * Строка сводки «Одна ситуация требует внимания.» печатается только когда
 * счёт есть; при отказе источника её нет вовсе (решение главного окна
 * 20.09) — «0 ситуаций» было бы утверждением, которого никто не делал.
 */

import type { SalonReadinessResponse } from "./admin-api";

export const READINESS_PATH = "/admin/readiness";

export type ReadinessState =
  | { kind: "loading" }
  | { kind: "failed" }
  | { kind: "unknown" }
  | { kind: "ready" }
  | { kind: "problems"; n: number };

export function readinessState(doc: SalonReadinessResponse): ReadinessState {
  if (doc.source_problem) return { kind: "unknown" };
  if (doc.ready) return { kind: "ready" };
  const n = Array.isArray(doc.problems) ? doc.problems.length : 0;
  if (n === 0) return { kind: "unknown" };
  return { kind: "problems", n };
}

/** «1 проблема», «2 проблемы», «5 проблем», «21 проблема». */
export function problemsLabel(n: number): string {
  return `${n} ${pluralRu(n, "проблема", "проблемы", "проблем")}`;
}

export function readinessCardText(state: ReadinessState): string {
  switch (state.kind) {
    case "loading":
      return "Готовность — проверяем…";
    case "failed":
    case "unknown":
      return "Готовность — не удалось проверить";
    case "ready":
      return "Готовность — салон готов";
    case "problems":
      return `Готовность — ${problemsLabel(state.n)}`;
  }
}

/**
 * Строка сводки. Ноль — пусто (не «0 ситуаций»): вызывающий печатает её
 * только при `kind === "problems"`.
 */
export function attentionLine(n: number): string {
  if (n <= 0) return "";
  if (n === 1) return "Одна ситуация требует внимания.";
  const noun = pluralRu(n, "ситуация", "ситуации", "ситуаций");
  const verb = n % 10 === 1 && n % 100 !== 11 ? "требует" : "требуют";
  return `${n} ${noun} ${verb} внимания.`;
}

/** «2026-09-20T09:00:00+00:00» → «12:00» по часам зрителя; мусор → "". */
export function checkedAtLabel(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(new Date(t));
}

function pluralRu(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
