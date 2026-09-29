/**
 * DRF-2539 — фото мастера и работы портфолио, только через прокси бота.
 *
 * Сервер отдаёт в `photo_url` / `image_url` не адрес хранилища, а свой путь
 * с версией: `/api/v1/customer/media/masters/<id>/photo?v=<хеш>`
 * (`apps/miniapp_api/master_media.py`). Прежний адрес
 * (`http://minio:9000/…?AWSAccessKeyId=…&Signature=…`) телефон не открывал
 * вовсе: хост внутренний, подпись живёт час.
 *
 * Байты берутся `fetch` с заголовком подписи и отдаются картинке
 * `blob:`-адресом — тот же приём и по той же причине, что у снимка дневника
 * (`diary-photo.ts`, DRF-2455): прокси без initData не отвечает, а браузерная
 * картинка заголовков не шлёт.
 *
 * ### Что грузим, а что нет
 *
 * Только путь под {@link MASTER_MEDIA_PREFIX}. Любое другое значение — пусто,
 * старый ответ из кэша, чужой адрес — «фото нет», без запроса: карточка
 * рисует инициалы. Заголовок подписи не уходит никуда, кроме нашей ручки.
 *
 * ### Кэш — витрине
 *
 * Аренда по пути (путь уже несёт версию: новое фото — новый путь). Одна
 * картинка — один запрос, сколько бы карточек её ни показывали. В отличие от
 * дневника, свободные картинки НЕ убираются, как только ушёл последний
 * арендатор: витрина → профиль мастера → назад показывает те же лица, и
 * повторная загрузка на каждом переходе — тот расход, от которого кэш и
 * нужен. Держим до {@link MAX_CACHED_MASTER_PHOTOS} свободных, вытесняем
 * самые давние. Поверх — `Cache-Control: private, max-age=300` ответа:
 * браузер не ходит в сеть повторно и после вытеснения.
 *
 * **Предел, названный:** между запусками приложения не хранится ничего —
 * кэш в памяти вкладки и в HTTP-кэше браузера на 5 минут.
 */
import { applyIdentityHeaders } from "./auth-headers";
import { ApiError } from "./api";

/** Префикс прокси на проводе — зеркало `MEDIA_PREFIX` в `master_media.py`. */
export const MASTER_MEDIA_PREFIX = "/api/v1/customer/media/masters/";

/** Свободных картинок держим не больше: экран витрины — десятки мастеров. */
export const MAX_CACHED_MASTER_PHOTOS = 48;

interface ErrorBody {
  error: string;
  detail: string;
  details?: Record<string, unknown>;
}

/**
 * Путь, который можно грузить, — или `null` («фото нет, не ходить»).
 *
 * Только наш путь: начинается с префикса, без схемы и хоста, без сегментов
 * «.» / «..» (браузер нормализовал бы их в другой адрес).
 */
export function masterPhotoPath(value: string | null | undefined): string | null {
  if (typeof value !== "string" || !value.startsWith(MASTER_MEDIA_PREFIX)) return null;
  const pathPart = value.split("?")[0] ?? "";
  if (pathPart.split("/").some((seg) => seg === "." || seg === "..")) return null;
  return value;
}

/**
 * Загрузить картинку и вернуть `blob:`-адрес. Адрес принадлежит вызывающему.
 *
 * Не наш путь → `null` без запроса; 404 → `null` (фото удалили между ответом
 * и загрузкой — для экрана это «фото нет»); отменённый запрос → `null` без
 * адреса; остальные отказы — `ApiError`.
 */
export async function loadMasterPhoto(
  value: string | null | undefined,
  signal?: AbortSignal,
): Promise<string | null> {
  const path = masterPhotoPath(value);
  if (path === null) return null;

  let blob: Blob;
  try {
    const headers = new Headers();
    applyIdentityHeaders(headers);
    const res = await fetch(path, { headers, signal });
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
  src?: string | null;
  touched: number;
}

const slots = new Map<string, Slot>();
let clock = 0;

function drop(path: string, slot: Slot): void {
  slot.controller.abort();
  if (slot.src) URL.revokeObjectURL(slot.src);
  slots.delete(path);
}

function evictIdle(): void {
  const idle = [...slots].filter(([, s]) => s.refs === 0);
  if (idle.length <= MAX_CACHED_MASTER_PHOTOS) return;
  idle.sort(([, a], [, b]) => a.touched - b.touched);
  for (const [path, slot] of idle.slice(0, idle.length - MAX_CACHED_MASTER_PHOTOS)) {
    drop(path, slot);
  }
}

export interface MasterPhotoLease {
  promise: Promise<string | null>;
  release: () => void;
}

/**
 * Взять картинку в аренду. `release` — ровно один раз, при уходе карточки.
 * Не наш путь — пустая аренда: `null`, без запроса.
 */
export function acquireMasterPhoto(value: string | null | undefined): MasterPhotoLease {
  const path = masterPhotoPath(value);
  if (path === null) return { promise: Promise.resolve(null), release: () => {} };

  let slot = slots.get(path);
  if (!slot) {
    const controller = new AbortController();
    const fresh: Slot = { refs: 0, controller, touched: 0, promise: Promise.resolve(null) };
    fresh.promise = loadMasterPhoto(path, controller.signal).then(
      (src) => {
        // Слот вытеснили, пока шёл запрос: адрес никто бы не освободил.
        if (slots.get(path) !== fresh) {
          if (src) URL.revokeObjectURL(src);
          return null;
        }
        fresh.src = src;
        return src;
      },
      (err: unknown) => {
        // Сбой не кэшируем: следующий показ попробует снова.
        if (slots.get(path) === fresh) slots.delete(path);
        throw err;
      },
    );
    slots.set(path, fresh);
    slot = fresh;
  }
  slot.refs += 1;
  slot.touched = ++clock;

  const held = slot;
  let released = false;
  return {
    promise: held.promise,
    release: () => {
      if (released) return;
      released = true;
      held.refs -= 1;
      held.touched = ++clock;
      evictIdle();
    },
  };
}

/** Только для узлов: сколько слотов было, и убрать всё (отменить, освободить). */
export function resetMasterPhotoCacheForTests(): number {
  const n = slots.size;
  for (const [path, slot] of [...slots]) drop(path, slot);
  return n;
}
