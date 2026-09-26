/**
 * DRF-2455 — снимок записи дневника, только через прокси бота.
 *
 * Источник один: `GET /api/v1/customer/diary/entry/<id>/photo`
 * (`apps/miniapp_api/views_diary_days.py:customer_food_photo`). Сырой адрес
 * хранилища сюда не попадает ни в каком виде: он внутренний для контейнера
 * и подписан со сроком (DRF-2539).
 *
 * Почему не `<img src="/api/...">`: приложение подтверждает себя заголовком
 * `Authorization: MaxInitData …`, а браузерная картинка заголовков не шлёт —
 * прокси без initData не отвечает вовсе (его узел k4). Класть подпись в
 * адрес нельзя: она уехала бы в журналы и Referer. Поэтому байты берутся
 * тем же заголовком, что у всех запросов, и отдаются картинке `blob:`-адресом.
 * Тот же приём, что у выгрузки данных (`personal-data.ts:exportPersonalData`).
 *
 * ### Кэш и освобождение
 *
 * Снимки берутся «в аренду» по id записи ({@link acquireDiaryEntryPhoto}):
 * одна запись — один запрос, сколько бы карточек её ни показывали. Когда
 * арендаторов не остаётся ни одного (дневник ушёл с экрана), всё
 * незавершённое отменяется, а все выданные адреса освобождаются. Уборка
 * отложена на тик, чтобы её не сработал переход «день ↔ главная дневника»
 * и повторный монтаж под `StrictMode`.
 *
 * **Предел, названный:** кэш живёт, пока открыт дневник, — не до
 * перезагрузки. Вернувшись в дневник с другого экрана, снимки загрузятся
 * заново. Между сессиями не хранится ничего: чужие снимки в хранилище
 * браузера не кладём.
 */
import { ApiError } from "./api";
import type { FoodDiaryEntry } from "./customer-wellness";
import { applyDevBypassHeaders } from "./dev-bypass";
import { getInitData } from "./max-sdk";
import { applySalonChoiceHeader } from "./salon-choice";

const API_BASE = "/api/v1/customer";

/** Больше снимков за один открытый дневник не держим: неделя — 7 дней по 4–8 записей. */
export const MAX_CACHED_PHOTOS = 64;

type PhotoEntry = Pick<FoodDiaryEntry, "id" | "has_photo">;

interface ErrorBody {
  error: string;
  detail: string;
  details?: Record<string, unknown>;
}

function buildAuthHeaders(): Headers {
  const headers = new Headers();
  const initData = getInitData();
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
  applySalonChoiceHeader(headers);
  return headers;
}

/**
 * Путь прокси для записи — или `null`, если снимка нет. `null` значит «не
 * ходить вовсе»: запись старше 30 суток или без скана — обычное состояние.
 */
export function diaryEntryPhotoPath(entry: PhotoEntry): string | null {
  if (entry.has_photo !== true) return null;
  // id приходит с сервера (UUID); «.» и «..» сегментом пути нормализовались
  // бы браузером в другой адрес, поэтому такой id — «снимка нет».
  if (!entry.id || entry.id === "." || entry.id === "..") return null;
  return `/diary/entry/${encodeURIComponent(entry.id)}/photo`;
}

/**
 * Загрузить снимок и вернуть `blob:`-адрес. Адрес принадлежит вызывающему.
 *
 * * `has_photo` не `true` → `null` без единого запроса;
 * * 404 → `null`: снимок удалён по сроку между сводкой и загрузкой, или
 *   запись чужая — прокси отвечает одинаково, и для экрана это «фото нет»;
 * * отменённый запрос → `null`, и адрес НЕ создаётся: поздний байт для
 *   карточки, которой уже нет, не оставляет сироту;
 * * остальные отказы — `ApiError`, как у всех запросов приложения.
 */
export async function loadDiaryEntryPhoto(
  entry: PhotoEntry,
  signal?: AbortSignal,
): Promise<string | null> {
  const path = diaryEntryPhotoPath(entry);
  if (path === null) return null;

  let blob: Blob;
  try {
    const res = await fetch(`${API_BASE}${path}`, { headers: buildAuthHeaders(), signal });
    if (res.status === 404) return null;
    if (!res.ok) {
      let body: ErrorBody = { error: "http_error", detail: res.statusText };
      try {
        body = (await res.json()) as ErrorBody;
      } catch {
        /* non-JSON */
      }
      throw new ApiError(res.status, body.error, body.detail, body.details);
    }
    blob = await res.blob();
  } catch (err) {
    if (signal?.aborted) return null;
    throw err;
  }
  if (signal?.aborted) return null;
  return URL.createObjectURL(blob);
}

// ── аренда ───────────────────────────────────────────────────────────────────

interface Slot {
  refs: number;
  controller: AbortController;
  promise: Promise<string | null>;
  /** Выданный адрес — его и освобождаем. `undefined`, пока не пришёл. */
  src?: string | null;
  /** Порядок последней аренды — для вытеснения самых давних свободных. */
  touched: number;
}

const slots = new Map<string, Slot>();
let clock = 0;
let sweepScheduled = false;

function drop(id: string, slot: Slot): void {
  slot.controller.abort();
  if (slot.src) URL.revokeObjectURL(slot.src);
  slots.delete(id);
}

function sweep(): void {
  sweepScheduled = false;
  for (const slot of slots.values()) if (slot.refs > 0) return;
  for (const [id, slot] of [...slots]) drop(id, slot);
}

function scheduleSweep(): void {
  if (sweepScheduled) return;
  sweepScheduled = true;
  setTimeout(sweep, 0);
}

function evictIdle(): void {
  if (slots.size <= MAX_CACHED_PHOTOS) return;
  const idle = [...slots].filter(([, s]) => s.refs === 0).sort(([, a], [, b]) => a.touched - b.touched);
  for (const [id, slot] of idle) {
    if (slots.size <= MAX_CACHED_PHOTOS) break;
    drop(id, slot);
  }
}

export interface PhotoLease {
  promise: Promise<string | null>;
  release: () => void;
}

/**
 * Взять снимок записи в аренду. `release` обязателен ровно один раз — его
 * зовёт уход карточки. Без `has_photo` аренда пустая: `null`, без запроса.
 */
export function acquireDiaryEntryPhoto(entry: PhotoEntry): PhotoLease {
  if (diaryEntryPhotoPath(entry) === null) {
    return { promise: Promise.resolve(null), release: () => {} };
  }
  let slot = slots.get(entry.id);
  if (!slot) {
    const controller = new AbortController();
    const fresh: Slot = { refs: 0, controller, touched: 0, promise: Promise.resolve(null) };
    fresh.promise = loadDiaryEntryPhoto(entry, controller.signal).then(
      (src) => {
        // Слот могли убрать, пока шёл запрос: адрес, выданный после
        // уборки, никто бы не освободил.
        if (slots.get(entry.id) !== fresh) {
          if (src) URL.revokeObjectURL(src);
          return null;
        }
        fresh.src = src;
        return src;
      },
      (err: unknown) => {
        if (slots.get(entry.id) === fresh) slots.delete(entry.id);
        throw err;
      },
    );
    slots.set(entry.id, fresh);
    slot = fresh;
  }
  slot.refs += 1;
  slot.touched = ++clock;
  evictIdle();

  const held = slot;
  let released = false;
  return {
    promise: held.promise,
    release: () => {
      if (released) return;
      released = true;
      held.refs -= 1;
      scheduleSweep();
    },
  };
}

/**
 * Только для узлов: сколько слотов было, и убрать всё (отменить, освободить).
 * Узел, упавший до ухода карточек, не должен оставлять кэш следующему.
 */
export function resetDiaryPhotoCacheForTests(): number {
  const n = slots.size;
  for (const [id, slot] of [...slots]) drop(id, slot);
  sweepScheduled = false;
  return n;
}
