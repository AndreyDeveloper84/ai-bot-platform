/**
 * Customer records lib — REAL endpoints (pilot phase 3.2).
 *
 * Spec: `docs/screens/customer-records-flow.md` §3 (R1 main list) + §5
 * (R3 booking detail). Voice rules unchanged (factual, «ты», no
 * exclamation marks, «Не пришла» NOT «Не состоялась»).
 *
 * # What changed in phase 3.2 (stub removal, orchestrator GO 2026-07-19)
 *
 * The Tier-1 Phase-B stub (invented bookings, tenant groupings, AMM
 * «Сообщить по записи», repeat-intent prefill, address/policy/refund
 * detail fields) is deleted: those fields have NO backend source —
 * `apps/miniapp_api` serves only the `BookingItem` rows below, and the
 * AMM `POST /bookings/{id}/messages` + repeat-intent endpoints do not
 * exist. Rendering them was the "fake data in prod" the pilot forbids.
 *
 * Real surface (verbatim from `views.py::_booking_to_dict`):
 *
 *   GET /api/v1/customer/bookings/list[?status=past][&limit][&before]
 *   GET /api/v1/customer/bookings/<id>
 *
 *   BookingItem: id, status (confirmed | cancel_requested |
 *   reschedule_requested | cancelled | rescheduled), service_id,
 *   service_name, master_id, master_name, visit_at, duration_min,
 *   cancel_requested_at, undo_window_seconds, cancellable,
 *   reschedulable, rating, can_rate.
 *
 * Derivation rules (presentation-only, never fabricated data):
 *
 *   - The backend has NO completed/no_show status: a confirmed booking
 *     whose visit lies in the past renders as «Прошла» (the server
 *     computes `can_rate` by the same rule — this is its mirror).
 *   - `is_nearest` — the FIRST upcoming item with the visit within
 *     24h (drives the full-actions card variant per Tau §4.1).
 *   - Actions come only from real flags: cancellable → "cancel",
 *     reschedulable → "reschedule", can_rate → "review"; history items
 *     also get "repeat" (plain navigation to the real catalog — no
 *     prefill endpoint exists). AMM "message" and maps "route" are
 *     post-pilot: no backend.
 */

import { fetchBooking, fetchMyBookings, type BookingItem } from "./api";
import {
  type CustomerVisibleStatus,
  mapBookingStatus,
  type StatusRendering,
} from "./booking-status";
import { formatVisitFull } from "./format";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Top-level booking sections. Default landing «upcoming» per Tau §2. */
export type BookingSection = "upcoming" | "history";

/**
 * Action vocabulary after phase 3.2 — every action maps to a REAL
 * endpoint/screen. «Сообщить по записи» (AMM) and «Маршрут» removed:
 * no backend (post-pilot).
 */
export type BookingAction =
  | "open"
  | "reschedule"
  | "cancel"
  | "repeat"
  | "review";

/** List card payload — real BookingItem + presentation derivations. */
export interface RecordItem {
  bookingId: string;
  /** Display status: "completed" when confirmed + visit in the past. */
  status: string;
  /** ISO datetime of the visit (raw). */
  visitAt: string;
  /** Pre-formatted «28 мая, чт в 16:00» (client-side, local TZ). */
  datetimeLabel: string;
  durationMin: number | null;
  serviceName: string;
  masterName: string;
  /** First upcoming item with the visit within 24h (Tau §4.1). */
  isNearest: boolean;
  actions: BookingAction[];
  rating: number | null;
  /** C7.3 — raw capture_state when the passthrough ships it; else null. */
  paymentState?: string | null;
}

export interface RecordsPage {
  section: BookingSection;
  totalCount: number;
  items: RecordItem[];
  nextCursor: string | null;
}

// ---------------------------------------------------------------------------
// Derivations
// ---------------------------------------------------------------------------

const NEAREST_WINDOW_MS = 24 * 60 * 60 * 1000;

/**
 * Display status for a booking row. Past + confirmed collapses to
 * "completed" — the mirror of the server's `can_rate` rule
 * (`_booking_to_dict`: past AND confirmed AND unrated). Everything
 * else passes through verbatim for {@link mapBookingStatus}.
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
 * Action set from real flags only. `section` decides the repeat/review
 * family (history) vs the manage family (upcoming) per Tau §4/§6.
 */
export function actionsFor(item: BookingItem, section: BookingSection): BookingAction[] {
  if (section === "history") {
    const actions: BookingAction[] = ["open", "repeat"];
    if (item.can_rate) actions.push("review");
    return actions;
  }
  const actions: BookingAction[] = ["open"];
  if (item.reschedulable) actions.push("reschedule");
  if (item.cancellable) actions.push("cancel");
  return actions;
}

function toRecordItem(
  item: BookingItem,
  section: BookingSection,
  isNearest: boolean,
  now: Date,
): RecordItem {
  return {
    bookingId: item.id,
    status: displayStatusFor(item, now),
    visitAt: item.visit_at,
    datetimeLabel: formatVisitFull(item.visit_at),
    durationMin: item.duration_min,
    serviceName: item.service_name,
    masterName: item.master_name,
    isNearest,
    actions: actionsFor(item, section),
    rating: item.rating,
    paymentState: item.payment?.capture_state ?? null,
  };
}

// ---------------------------------------------------------------------------
// Public functions
// ---------------------------------------------------------------------------

/**
 * Fetch the customer's bookings for a section — real
 * `GET /bookings/list` (upcoming by default, `?status=past` for
 * history). Backend sorts upcoming ASC / history DESC by visit_at;
 * grouping by time buckets is a render concern (`annotateItems`).
 */
export async function getMyBookings(
  section: BookingSection,
  before?: string,
): Promise<RecordsPage> {
  const res = await fetchMyBookings({
    past: section === "history",
    ...(before ? { before } : {}),
  });
  const now = new Date();
  let nearestAssigned = false;
  const items = res.items.map((row) => {
    let isNearest = false;
    if (section === "upcoming" && !nearestAssigned) {
      const visit = new Date(row.visit_at);
      if (
        !Number.isNaN(visit.getTime()) &&
        visit.getTime() - now.getTime() <= NEAREST_WINDOW_MS
      ) {
        isNearest = true;
        nearestAssigned = true;
      }
    }
    return toRecordItem(row, section, isNearest, now);
  });
  return {
    section,
    totalCount: items.length,
    items,
    nextCursor: res.next_cursor,
  };
}

/** Fetch a single booking — real `GET /bookings/<id>`. */
export async function getBookingDetail(bookingId: string): Promise<BookingItem> {
  const { booking } = await fetchBooking(bookingId);
  return booking;
}

// ---------------------------------------------------------------------------
// Render helpers — kept from the stub era, still honest (pure mapping).
// ---------------------------------------------------------------------------

/**
 * Map raw/display status to the customer-visible badge rendering.
 * Re-exports {@link mapBookingStatus} so screen code only imports
 * from `customer-records`.
 */
export function renderStatus(rawStatus: string): {
  status: CustomerVisibleStatus | "unknown";
  rendering: StatusRendering;
} {
  return mapBookingStatus(rawStatus);
}

/**
 * Time-grouping label for upcoming bookings (Tau §3.2 «Сегодня» /
 * «Завтра» / «На этой неделе» / «Через неделю» / «Позже»).
 */
export function timeGroupLabel(
  visitAt: Date,
  now: Date = new Date(),
): string {
  const ms = visitAt.getTime() - now.getTime();
  const hours = ms / (1000 * 60 * 60);
  const sameDay =
    visitAt.getFullYear() === now.getFullYear() &&
    visitAt.getMonth() === now.getMonth() &&
    visitAt.getDate() === now.getDate();
  if (sameDay) return "Сегодня";

  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const isTomorrow =
    visitAt.getFullYear() === tomorrow.getFullYear() &&
    visitAt.getMonth() === tomorrow.getMonth() &&
    visitAt.getDate() === tomorrow.getDate();
  if (isTomorrow) return "Завтра";

  if (hours < 24 * 7) return "На этой неделе";
  if (hours < 24 * 14) return "Через неделю";
  return "Позже";
}

export interface AnnotatedRecordItem extends RecordItem {
  _timeGroup: string;
}

/**
 * Attach a time-bucket label to each item (upcoming: day buckets;
 * history: month buckets «Май 2026»). Bucketing preserves list order —
 * the backend already sorted the rows.
 */
export function annotateItems(
  items: RecordItem[],
  section: BookingSection,
  now: Date = new Date(),
): AnnotatedRecordItem[] {
  return items.map((b) => ({
    ...b,
    _timeGroup:
      section === "upcoming"
        ? timeGroupLabel(new Date(b.visitAt), now)
        : monthGroupLabel(new Date(b.visitAt)),
  }));
}

const NOMINATIVE_MONTHS = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];

function monthGroupLabel(d: Date): string {
  return `${NOMINATIVE_MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

/**
 * Pluraliser — «1 запись / 2 записи / 5 записей». Reused for the tab
 * counter badges.
 */
export function ruPluralBookings(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "записей";
  if (mod10 === 1) return "запись";
  if (mod10 >= 2 && mod10 <= 4) return "записи";
  return "записей";
}
