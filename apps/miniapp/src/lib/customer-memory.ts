/**
 * «Что Ayla помнит» — память человека на экране профиля (DRF-2133, Память-2).
 *
 *   GET    /memory/               → { green, health, status }
 *   DELETE /memory/{id}/          → { id, deleted: true } · чужая → 404
 *   POST   /memory/forget-all/    → 202 { status: "deletion_pending" }
 *
 * Сервер: `apps/miniapp_api/views_memory.py`. Это второе окно в ту же
 * память, что у чата («что ты помнишь» / «забудь …»): подпись факта
 * (`label`) приходит готовой и совпадает с репликой бота («придерживаешься веганского питания» — 2-е лицо, DRF-1292); `provenance`
 * различает то, что человек сказал (`said`), и то, что Ayla предположила
 * (`inferred`) — второе показывается с пометкой, но не скрывается.
 *
 * Никаких переключателей «разрешить запоминать» здесь нет: согласие дано
 * при входе (ADR-0011), а этот экран — про прозрачность и право забыть.
 */

import { ApiError, request, requestWithStatus } from "./api";

export type MemoryProvenance = "said" | "inferred";
export type MemoryStatus = "active" | "deletion_pending";

export interface GreenFact {
  id: string;
  key: string | null;
  /** Фраза чата («придерживается веганского питания») или null, если у факта нет читаемой формы. */
  label: string | null;
  value: string | null;
  /** ISO-время, когда факт был записан. */
  said_at: string;
  provenance: MemoryProvenance;
}

export interface HealthFact {
  id: string;
  kind: string;
  value: string | null;
  said_at: string;
}

export interface MemoryResponse {
  green: GreenFact[];
  health: HealthFact[];
  status: MemoryStatus;
}

const MEMORY_PATH = "/memory/";

export async function fetchMemory(): Promise<MemoryResponse> {
  const doc = await request<MemoryResponse>(MEMORY_PATH);
  return {
    green: Array.isArray(doc?.green) ? doc.green : [],
    health: Array.isArray(doc?.health) ? doc.health : [],
    status: doc?.status === "deletion_pending" ? "deletion_pending" : "active",
  };
}

/**
 * «Забыть» одну запись. 404 — записи уже нет (забыта из чата, сменилась):
 * для экрана это тот же исход, не ошибка, — иначе кнопка «не получилось»
 * горела бы вечно на строке, которой нет.
 */
export async function forgetEntry(id: string): Promise<void> {
  try {
    await request<{ id: string; deleted: boolean }>(`${MEMORY_PATH}${encodeURIComponent(id)}/`, {
      method: "DELETE",
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return;
    throw err;
  }
}

/** «Забыть всё» — то же, что «забудь всё» в чате; 202 и статус. */
export async function forgetAll(): Promise<MemoryStatus> {
  const { data } = await requestWithStatus<{ status: MemoryStatus }>(`${MEMORY_PATH}forget-all/`, {
    method: "POST",
  });
  return data?.status === "deletion_pending" ? "deletion_pending" : "active";
}

/**
 * Подпись происхождения: «ты сказал(а) 19.09» / «мы предположили».
 * Дата — только у сказанного: у предположения важна пометка, не день.
 */
export function provenanceLabel(fact: Pick<GreenFact, "provenance" | "said_at">): string {
  if (fact.provenance === "inferred") return "мы предположили";
  return `ты сказал(а) ${shortDate(fact.said_at)}`;
}

/** «2026-09-19T20:58:47+00:00» → «19.09». Нечитаемое ISO — пустая строка, не «NaN.NaN». */
export function shortDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  if (!match) return "";
  return `${match[3]}.${match[2]}`;
}

/** Что показать в строке факта: подпись чата, иначе сырое значение. */
export function factText(fact: Pick<GreenFact, "label" | "value" | "key">): string {
  if (fact.label) return fact.label;
  if (fact.value) return fact.value;
  return fact.key ?? "";
}
