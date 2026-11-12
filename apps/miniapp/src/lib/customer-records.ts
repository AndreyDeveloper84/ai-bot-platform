/**
 * «Записи» клиента — настоящие ручки и то, что экран из них выводит.
 *
 * Макет: «Записи.png» (набор владельца от 06.10.2026, DRF-2952). Правило
 * родительской задачи DRF-2948: макет задаёт вид, а что можно показать —
 * решает провод. Поэтому здесь нет ни расстояния до салона, ни «подготовки к
 * процедуре»: их источника не существует.
 *
 * Провод (`apps/miniapp_api/views.py`, оба пути отдают одну форму):
 *
 *   GET /api/v1/customer/bookings/list[?status=past][&limit][&before]
 *   GET /api/v1/customer/bookings/<id>
 *
 * Что выводится (только представление, данных не выдумываем):
 *
 *   - У источника нет статуса «прошла» для локального пути: подтверждённая
 *     запись, время которой миновало, показывается как `completed`. Сервер
 *     тем же правилом считает `can_rate` — это его зеркало.
 *   - «Обычный» статус на карточке не рисуется (макет): предстоящая
 *     подтверждённая и прошедшая состоявшаяся выглядят просто карточкой.
 *     Любой другой статус виден значком — он и есть новость.
 */

import { fetchBooking, fetchMyBookings, type BookingItem } from "./api";
import {
  type CustomerVisibleStatus,
  mapBookingStatus,
  type StatusRendering,
} from "./booking-status";
import { formatRecordWhen } from "./format";

/** Разделы списка. По умолчанию открыт «Предстоящие». */
export type BookingSection = "upcoming" | "history";

/** Строка списка — запись с провода плюс выведенное для показа. */
export interface RecordItem {
  bookingId: string;
  /** Статус для показа: `completed`, когда подтверждена и время прошло. */
  status: string;
  /** Время визита с провода, в поясе салона (DRF-2589). */
  visitAt: string;
  /** «22 сентября, 14:00»; год — когда он не текущий. */
  whenLabel: string;
  serviceName: string;
  masterName: string;
  /** DRF-2436 B / п.15 — салон записи; `""`, когда провод его не несёт. */
  salonName: string;
  /** Значение для `MasterPhoto`; `""` — фото нет. */
  masterPhotoUrl: string;
  /**
   * Адрес дословно с провода — три состояния (`lib/visit-address.ts`).
   * Фразу собирает `visitAddressText`, не карточка.
   */
  address: string | null | undefined;
  /** DRF-2172 — снимок цены, Decimal-строка или `null`. */
  price: string | null;
  /** C7.3 — сырое состояние оплаты, когда провод его несёт. */
  paymentState: string | null;
  rating: number | null;
  /** Сервер разрешил оценить визит (прошла, подтверждена, без оценки). */
  canRate: boolean;
}

export interface RecordsPage {
  section: BookingSection;
  items: RecordItem[];
  nextCursor: string | null;
}

/**
 * Статус для показа. Прошедшая подтверждённая сворачивается в `completed` —
 * зеркало серверного `can_rate`. Остальное идёт дословно в
 * {@link mapBookingStatus}.
 */
export function displayStatusFor(item: BookingItem, now: Date = new Date()): string {
  if (item.status === "confirmed") {
    const visit = new Date(item.visit_at);
    if (!Number.isNaN(visit.getTime()) && visit.getTime() < now.getTime()) {
      return "completed";
    }
  }
  return item.status;
}

/**
 * Обычен ли статус для своего раздела — тогда карточка его не называет.
 * Незнакомый статус обычным не бывает: его обязаны увидеть.
 */
export function isOrdinaryStatus(displayStatus: string, section: BookingSection): boolean {
  const { status } = mapBookingStatus(displayStatus);
  return section === "upcoming" ? status === "confirmed" : status === "completed";
}

function toRecordItem(item: BookingItem, now: Date): RecordItem {
  return {
    bookingId: item.id,
    status: displayStatusFor(item, now),
    visitAt: item.visit_at,
    whenLabel: formatRecordWhen(item.visit_at, now),
    serviceName: item.service_name,
    masterName: item.master_name,
    salonName: item.salon_name ?? "",
    masterPhotoUrl: item.master_photo_url ?? "",
    address: item.address,
    price: item.price ?? null,
    paymentState: item.payment?.capture_state ?? null,
    rating: item.rating,
    canRate: item.can_rate,
  };
}

/**
 * Записи раздела — настоящий `GET /bookings/list` (предстоящие по умолчанию,
 * `?status=past` — прошедшие). Порядок задаёт сервер: предстоящие по
 * возрастанию времени, прошедшие по убыванию.
 */
export async function getMyBookings(section: BookingSection, before?: string): Promise<RecordsPage> {
  const res = await fetchMyBookings({
    past: section === "history",
    ...(before ? { before } : {}),
  });
  const now = new Date();
  return {
    section,
    items: res.items.map((row) => toRecordItem(row, now)),
    nextCursor: res.next_cursor,
  };
}

/** Одна запись — настоящий `GET /bookings/<id>`. */
export async function getBookingDetail(bookingId: string): Promise<BookingItem> {
  const { booking } = await fetchBooking(bookingId);
  return booking;
}

/**
 * Статусы «в пути»: сервер начал действие, но не закончил его.
 *
 * Общий словарь (`booking-status.ts`) сводит `cancel_requested` к «Отменена»,
 * а `reschedule_requested` — к «Перенесена». Это слова о СДЕЛАННОМ, сказанные
 * раньше дела: тот же дефект, что DRF-2346, только через подпись статуса, а не
 * через сообщение. В «Записях» клиента у этих двух статусов свои слова — о
 * ходе. Слова рабочие, владельцу на сверку.
 */
export const IN_PROGRESS_COPY: Record<string, string> = {
  cancel_requested: "Отмена обрабатывается",
  reschedule_requested: "Перенос не завершён",
};

/** Подпись хода дела для статуса «в пути» — или `null`, если статус не такой. */
export function progressLabel(rawStatus: string): string | null {
  return IN_PROGRESS_COPY[rawStatus] ?? null;
}

/** Статусы, после которых с записью делать уже нечего. */
const OVER_STATUSES: ReadonlySet<string> = new Set([
  "cancelled",
  "rescheduled",
  "completed",
  "no_show",
]);

/**
 * Запись позади: визит прошёл или запись закрыта. Тогда уместно «повторить»,
 * а переносить и отменять нечего. Ожидающая оплаты и «в пути» — не позади.
 */
export function isOver(item: BookingItem, now: Date = new Date()): boolean {
  if (OVER_STATUSES.has(item.status)) return true;
  const visit = new Date(item.visit_at);
  return !Number.isNaN(visit.getTime()) && visit.getTime() < now.getTime();
}

/** Статус → значок для человека; экраны берут его только отсюда. */
export function renderStatus(rawStatus: string): {
  status: CustomerVisibleStatus | "unknown";
  rendering: StatusRendering;
} {
  return mapBookingStatus(rawStatus);
}

/** Первые буквы имени — плитка вместо фото, когда фото нет. */
export function initialsOf(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}
