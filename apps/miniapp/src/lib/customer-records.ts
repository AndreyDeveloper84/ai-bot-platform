/**
 * Customer records stub lib — Tier 1 Priority 5 Phase B.
 *
 * Spec: `docs/screens/customer-records-flow.md` §3 (R1 main list) + §5
 * (R3 booking detail) + §12.1 (backend contract for W4 proxy) +
 * `docs/design/policies/ayla-mediated-messaging.md` §4 (quick reasons
 * for «Сообщить по записи» modal).
 *
 * # Contracts (W4 proxy / Ayla canonical per ADR-0009)
 *
 *   GET /api/v1/me/bookings?section=upcoming    → BookingsListResponse
 *   GET /api/v1/me/bookings?section=history     → BookingsListResponse
 *   GET /api/v1/me/bookings/{id}                → BookingDetail
 *   POST /api/v1/bookings/{id}/repeat-intent    → RepeatIntentResponse
 *   POST /api/v1/bookings/{id}/messages         → MessageSendResult
 *
 * WIRING NOTE: this module ships STUBS only. W4 owns the proxy
 * endpoints; swap stub function bodies to `request(...)` calls when
 * W4 ships. Function signatures DO NOT change.
 *
 * # Stub variants for dev QA (Wellness pattern reuse)
 *
 *   ?stub=default — multi-tenant happy path (3 upcoming across 2
 *                   tenants, 5 history with mixed completed / cancelled
 *                   / no-show / provider-cancelled / refund states)
 *   ?stub=empty   — total_count=0 both sections
 *   ?stub=partial — 1 upcoming + 0 history
 *
 * Production bundle ships only `default`. The `empty` + `partial`
 * blobs alias to default via the same `import.meta.env.DEV` guard
 * pattern used by `customer-wellness.ts` (Wellness PR #886 lesson —
 * the `?stub=` query was a phishing vector when read in prod).
 *
 * # Voice + factual-only rule
 *
 * All copy in stub data MUST stay factual + obey memory
 * `project_records_voice_principles`:
 *   - No exclamation marks anywhere
 *   - No selling tone («Запишись снова!» — forbidden)
 *   - No English jargon
 *   - «Не пришла» NOT «Не состоялась»
 *   - «Сообщить по записи» NOT «Написать» (F1 locked)
 *   - «ты» canonical (F2 locked)
 */

// ---------------------------------------------------------------------------
// Contract types — verbatim per Tau §12.1 + ТЛ Q4 verdict (backend wires
// `refund_status` / `refund_amount` / `no_show_penalty` directly — frontend
// does NOT compute these from price/policy).
// ---------------------------------------------------------------------------

import {
  type CustomerVisibleStatus,
  mapBookingStatus,
  type StatusRendering,
} from "./booking-status";

/**
 * Top-level booking sections. Default landing «upcoming» per Tau §2 +
 * Q-R-10 verdict.
 */
export type BookingSection = "upcoming" | "history";

/**
 * Per Tau §6 action vocabulary. The set is computed server-side per
 * status × time-to-visit; frontend renders whichever buttons the
 * backend reports. Conservative fallback: empty set → no destructive
 * actions surfaced.
 */
export type BookingAction =
  | "open"
  | "message" // «Сообщить по записи» — AMM §4 modal
  | "route" // Open maps deeplink for `route_deeplink`
  | "reschedule"
  | "cancel"
  | "repeat" // «Записаться ещё» — past booking CTA
  | "review"; // «Оставить отзыв» — pending feedback

/**
 * Booking list item — shape returned by `/api/v1/me/bookings?section=...`.
 * Field set kept LEAN (R2 §3 — list cards are dense; detail screen does
 * the heavy lifting). Backend pre-formats `datetime_relative_label` in
 * the customer's TZ so frontend doesn't have to TZ-juggle.
 */
export interface BookingListItem {
  booking_id: string;
  /**
   * Raw backend status (the wider taxonomy). Render via
   * {@link mapBookingStatus} — NEVER hard-code label per status string.
   */
  status: string;
  visit_at_iso: string;
  /** Pre-formatted in customer's TZ («Завтра · пт · 16:00»). */
  datetime_relative_label: string;
  duration_min: number;
  service_name: string;
  master_first_name: string;
  /**
   * True iff this is the nearest upcoming booking (≤24h to visit).
   * Drives full-actions rendering on `BookingCard` per Tau §4.1.
   */
  is_nearest: boolean;
  /** Per Tau §6 — different sets per status. */
  actions_available: BookingAction[];
  tenant_id: string;
  tenant_name: string;
  /** Approximate price in rubles. Optional — list view hides if absent. */
  price_approx?: number;
}

/**
 * Backend pre-computes Variant C groupings (per Tau §3.3 + booking-flow
 * §8). For single-tenant a single grouping wraps all items; for
 * multi-tenant the array carries one grouping per tenant with
 * tenant-stable ordering (most recent visit first).
 */
export interface BookingGrouping {
  tenant_id: string;
  tenant_name: string;
  bookings: BookingListItem[];
}

export interface BookingsListResponse {
  section: BookingSection;
  total_count: number;
  groupings: BookingGrouping[];
  /** «Показать ещё» pagination — Tau §3.4 filter chip on history. */
  has_more?: boolean;
  next_cursor?: string | null;
}

/**
 * Detail screen payload — R3 §5. Backend pre-renders the long-form
 * fields (address, cancellation_policy_text, refund_amount) so the
 * frontend renders verbatim with no string math.
 */
export interface BookingDetail extends BookingListItem {
  address_full: string;
  /** «Открыть в Картах» — protocol URL. Optional. */
  route_deeplink?: string;
  note_to_master?: string;
  master_avatar_url?: string;
  cancellation_policy_text?: string;
  /**
   * Original slot ISO when the booking was rescheduled (Tau §6
   * detail-enhancement note — «Перенесена с {original_datetime}»).
   */
  rescheduled_origin_dt?: string;
  /**
   * Per Tau §6 detail enhancement: emotionally distinguish customer
   * cancellation («Отменена вами») from provider cancellation. The
   * backend reports who cancelled / why; frontend renders the
   * appropriate copy variant in R3.
   */
  cancellation_context?:
    | "by_customer"
    | "by_provider"
    | "by_system_no_show";
  /** Per ТЛ Q4 — wired directly, NOT computed from price. */
  refund_status?: "none" | "pending" | "completed";
  refund_amount?: number;
  no_show_penalty?: number;
  completed_at?: string;
  can_review?: boolean;
  review_submitted?: boolean;
  /**
   * Drives «Записаться ещё» visibility (Tau §6 / R6 §8). Backend
   * gates on master-still-active + service-not-deprecated + salon-
   * not-suspended; frontend just renders the CTA.
   */
  repeat_intent_available?: boolean;
}

export interface RepeatIntentResponse {
  /** True iff the F3 prefilled path is available. */
  prefill_available: boolean;
  /** Set when prefill_available=false. */
  fallback_reason?:
    | "master_deactivated"
    | "service_deprecated"
    | "salon_suspended";
  /**
   * Smart fallback content per Tau §10.4 + Q-R-3 verdict. Surface
   * verbatim in the launch screen voice line.
   */
  alternative_suggestion?: {
    master_id: string;
    service_id: string;
    reason_text: string;
  };
  /** Customer is redirected here once the prefill / fallback is selected. */
  booking_flow_url?: string;
}

/**
 * Per Ayla-Mediated Messaging policy §4 — 4 main quick reasons. Each
 * reason maps to a flow type (chip vs free text). Frontend renders
 * the chips; backend records the reason key + free-text comment.
 */
export type MessageReason =
  | "running_late"
  | "cannot_find_entrance"
  | "ask_about_preparation"
  | "other";

export interface MessageSendInput {
  booking_id: string;
  reason: MessageReason;
  /** Optional free text for «Не могу найти вход» / «Уточнить» / «Другое». */
  text?: string;
}

export interface MessageSendResult {
  delivered: boolean;
  /** Master first name surfaced back to customer for confirmation toast. */
  master_first_name: string;
  tenant_name: string;
}

// ---------------------------------------------------------------------------
// Stub variant picker — dev-only QA hook (mirror customer-wellness.ts).
// ---------------------------------------------------------------------------

type StubVariant = "default" | "empty" | "partial";

function pickStubVariant(): StubVariant {
  // Production: always `default`. Reading `?stub=` in prod was a
  // phishing vector in the booking flow PR — apply the same gate.
  if (!import.meta.env.DEV) return "default";
  if (typeof window === "undefined") return "default";
  try {
    const sp = new URLSearchParams(window.location.search);
    const v = sp.get("stub");
    if (v === "empty" || v === "partial") return v;
  } catch {
    /* SSR / parse failure — fall through */
  }
  return "default";
}

// ---------------------------------------------------------------------------
// Stub data — voice-audited per Tau spec + memory
// `project_records_voice_principles`. No exclamation marks, no selling
// tone, no English jargon, «Не пришла» NOT «Не состоялась».
// ---------------------------------------------------------------------------

const TENANT_BEAUTY_PLACE = {
  id: "tenant-beauty-place",
  name: "Beauty Place",
};
const TENANT_CASA_BELLA = {
  id: "tenant-casa-bella",
  name: "Casa Bella",
};

const DEFAULT_UPCOMING: BookingsListResponse = {
  section: "upcoming",
  total_count: 3,
  has_more: false,
  next_cursor: null,
  groupings: [
    {
      tenant_id: TENANT_BEAUTY_PLACE.id,
      tenant_name: TENANT_BEAUTY_PLACE.name,
      bookings: [
        {
          booking_id: "stub-up-001",
          status: "confirmed",
          visit_at_iso: "2026-05-28T16:00:00+03:00",
          datetime_relative_label: "Завтра · чт · 16:00",
          duration_min: 90,
          service_name: "Маникюр гель-лак",
          master_first_name: "Анна",
          is_nearest: true,
          actions_available: [
            "open",
            "message",
            "route",
            "reschedule",
            "cancel",
          ],
          tenant_id: TENANT_BEAUTY_PLACE.id,
          tenant_name: TENANT_BEAUTY_PLACE.name,
          price_approx: 1800,
        },
        {
          booking_id: "stub-up-002",
          status: "confirmed",
          visit_at_iso: "2026-05-30T14:00:00+03:00",
          datetime_relative_label: "Пятница 30 мая · 14:00",
          duration_min: 60,
          service_name: "Массаж лимфодренаж",
          master_first_name: "Ирина",
          is_nearest: false,
          actions_available: ["open", "message"],
          tenant_id: TENANT_BEAUTY_PLACE.id,
          tenant_name: TENANT_BEAUTY_PLACE.name,
          price_approx: 2200,
        },
      ],
    },
    {
      tenant_id: TENANT_CASA_BELLA.id,
      tenant_name: TENANT_CASA_BELLA.name,
      bookings: [
        {
          booking_id: "stub-up-003",
          status: "rescheduled",
          visit_at_iso: "2026-06-04T11:00:00+03:00",
          datetime_relative_label: "Среда 4 июня · 11:00",
          duration_min: 45,
          service_name: "Брови — коррекция",
          master_first_name: "Карина",
          is_nearest: false,
          actions_available: ["open", "message"],
          tenant_id: TENANT_CASA_BELLA.id,
          tenant_name: TENANT_CASA_BELLA.name,
          price_approx: 1500,
        },
      ],
    },
  ],
};

const DEFAULT_HISTORY: BookingsListResponse = {
  section: "history",
  total_count: 5,
  has_more: false,
  next_cursor: null,
  groupings: [
    {
      tenant_id: TENANT_BEAUTY_PLACE.id,
      tenant_name: TENANT_BEAUTY_PLACE.name,
      bookings: [
        {
          booking_id: "stub-hist-001",
          status: "completed",
          visit_at_iso: "2026-05-20T16:00:00+03:00",
          datetime_relative_label: "20 мая",
          duration_min: 90,
          service_name: "Маникюр гель-лак",
          master_first_name: "Анна",
          is_nearest: false,
          actions_available: ["open", "repeat", "review"],
          tenant_id: TENANT_BEAUTY_PLACE.id,
          tenant_name: TENANT_BEAUTY_PLACE.name,
          price_approx: 2400,
        },
        {
          booking_id: "stub-hist-002",
          status: "completed",
          visit_at_iso: "2026-05-06T16:00:00+03:00",
          datetime_relative_label: "6 мая",
          duration_min: 120,
          service_name: "Маникюр + парафин",
          master_first_name: "Анна",
          is_nearest: false,
          actions_available: ["open", "repeat"],
          tenant_id: TENANT_BEAUTY_PLACE.id,
          tenant_name: TENANT_BEAUTY_PLACE.name,
          price_approx: 2700,
        },
        {
          booking_id: "stub-hist-003",
          status: "cancelled",
          visit_at_iso: "2026-04-28T16:00:00+03:00",
          datetime_relative_label: "28 апреля",
          duration_min: 90,
          service_name: "Маникюр",
          master_first_name: "Анна",
          is_nearest: false,
          actions_available: ["open", "repeat"],
          tenant_id: TENANT_BEAUTY_PLACE.id,
          tenant_name: TENANT_BEAUTY_PLACE.name,
          price_approx: 1800,
        },
        {
          booking_id: "stub-hist-004",
          status: "provider_cancelled",
          visit_at_iso: "2026-04-22T11:00:00+03:00",
          datetime_relative_label: "22 апреля",
          duration_min: 60,
          service_name: "Массаж лимфодренаж",
          master_first_name: "Ирина",
          is_nearest: false,
          actions_available: ["open", "repeat"],
          tenant_id: TENANT_BEAUTY_PLACE.id,
          tenant_name: TENANT_BEAUTY_PLACE.name,
          price_approx: 2200,
        },
        {
          booking_id: "stub-hist-005",
          status: "no_show",
          visit_at_iso: "2026-04-15T16:00:00+03:00",
          datetime_relative_label: "15 апреля",
          duration_min: 90,
          service_name: "Маникюр",
          master_first_name: "Анна",
          is_nearest: false,
          actions_available: ["open", "repeat"],
          tenant_id: TENANT_BEAUTY_PLACE.id,
          tenant_name: TENANT_BEAUTY_PLACE.name,
          price_approx: 1800,
        },
      ],
    },
  ],
};

const EMPTY_LIST: BookingsListResponse = {
  section: "upcoming",
  total_count: 0,
  has_more: false,
  next_cursor: null,
  groupings: [],
};

const PARTIAL_UPCOMING: BookingsListResponse = {
  section: "upcoming",
  total_count: 1,
  has_more: false,
  next_cursor: null,
  groupings: [
    {
      tenant_id: TENANT_BEAUTY_PLACE.id,
      tenant_name: TENANT_BEAUTY_PLACE.name,
      bookings: [
        {
          booking_id: "stub-up-001",
          status: "confirmed",
          visit_at_iso: "2026-05-28T16:00:00+03:00",
          datetime_relative_label: "Завтра · чт · 16:00",
          duration_min: 90,
          service_name: "Маникюр гель-лак",
          master_first_name: "Анна",
          is_nearest: true,
          actions_available: [
            "open",
            "message",
            "route",
            "reschedule",
            "cancel",
          ],
          tenant_id: TENANT_BEAUTY_PLACE.id,
          tenant_name: TENANT_BEAUTY_PLACE.name,
          price_approx: 1800,
        },
      ],
    },
  ],
};

const PARTIAL_HISTORY: BookingsListResponse = {
  section: "history",
  total_count: 0,
  has_more: false,
  next_cursor: null,
  groupings: [],
};

// Production bundle: empty + partial alias to default (Vite tree-shakes).
const UPCOMING_STUB: Record<StubVariant, BookingsListResponse> = import.meta.env
  .DEV
  ? {
      default: DEFAULT_UPCOMING,
      empty: { ...EMPTY_LIST, section: "upcoming" },
      partial: PARTIAL_UPCOMING,
    }
  : {
      default: DEFAULT_UPCOMING,
      empty: DEFAULT_UPCOMING,
      partial: DEFAULT_UPCOMING,
    };

const HISTORY_STUB: Record<StubVariant, BookingsListResponse> = import.meta.env
  .DEV
  ? {
      default: DEFAULT_HISTORY,
      empty: { ...EMPTY_LIST, section: "history" },
      partial: PARTIAL_HISTORY,
    }
  : {
      default: DEFAULT_HISTORY,
      empty: DEFAULT_HISTORY,
      partial: DEFAULT_HISTORY,
    };

// ---------------------------------------------------------------------------
// Booking detail stub — per `stub-up-001` / `stub-hist-001` etc.
// ---------------------------------------------------------------------------

const DETAIL_STUB: Record<string, BookingDetail> = {
  "stub-up-001": {
    booking_id: "stub-up-001",
    status: "confirmed",
    visit_at_iso: "2026-05-28T16:00:00+03:00",
    datetime_relative_label: "Завтра · чт · 16:00",
    duration_min: 90,
    service_name: "Маникюр гель-лак",
    master_first_name: "Анна",
    is_nearest: true,
    actions_available: ["open", "message", "route", "reschedule", "cancel"],
    tenant_id: TENANT_BEAUTY_PLACE.id,
    tenant_name: TENANT_BEAUTY_PLACE.name,
    price_approx: 1800,
    address_full: "ул. Тверская 12",
    route_deeplink: "https://maps.yandex.ru/?text=Beauty+Place",
    cancellation_policy_text:
      "Отмена за 12+ часов — без штрафа. Меньше 12 часов — салон может удержать 50% (около 900 ₽).",
    repeat_intent_available: true,
  },
  "stub-up-002": {
    booking_id: "stub-up-002",
    status: "confirmed",
    visit_at_iso: "2026-05-30T14:00:00+03:00",
    datetime_relative_label: "Пятница 30 мая · 14:00",
    duration_min: 60,
    service_name: "Массаж лимфодренаж",
    master_first_name: "Ирина",
    is_nearest: false,
    actions_available: ["open", "message", "route", "reschedule", "cancel"],
    tenant_id: TENANT_BEAUTY_PLACE.id,
    tenant_name: TENANT_BEAUTY_PLACE.name,
    price_approx: 2200,
    address_full: "ул. Тверская 12",
    route_deeplink: "https://maps.yandex.ru/?text=Beauty+Place",
    cancellation_policy_text:
      "Отмена за 12+ часов — без штрафа. Меньше 12 часов — салон может удержать 50% (около 1 100 ₽).",
    repeat_intent_available: true,
  },
  "stub-up-003": {
    booking_id: "stub-up-003",
    status: "rescheduled",
    visit_at_iso: "2026-06-04T11:00:00+03:00",
    datetime_relative_label: "Среда 4 июня · 11:00",
    duration_min: 45,
    service_name: "Брови — коррекция",
    master_first_name: "Карина",
    is_nearest: false,
    actions_available: ["open", "message", "route", "reschedule", "cancel"],
    tenant_id: TENANT_CASA_BELLA.id,
    tenant_name: TENANT_CASA_BELLA.name,
    price_approx: 1500,
    address_full: "ул. Большая Никитская 7",
    route_deeplink: "https://maps.yandex.ru/?text=Casa+Bella",
    rescheduled_origin_dt: "2026-05-30T11:00:00+03:00",
    cancellation_policy_text:
      "Отмена за 12+ часов — без штрафа. Меньше 12 часов — салон может удержать 50%.",
    repeat_intent_available: true,
  },
  "stub-hist-001": {
    booking_id: "stub-hist-001",
    status: "completed",
    visit_at_iso: "2026-05-20T16:00:00+03:00",
    datetime_relative_label: "20 мая",
    duration_min: 90,
    service_name: "Маникюр гель-лак",
    master_first_name: "Анна",
    is_nearest: false,
    actions_available: ["open", "repeat", "review"],
    tenant_id: TENANT_BEAUTY_PLACE.id,
    tenant_name: TENANT_BEAUTY_PLACE.name,
    price_approx: 2400,
    address_full: "ул. Тверская 12",
    completed_at: "2026-05-20T17:30:00+03:00",
    can_review: true,
    review_submitted: false,
    repeat_intent_available: true,
  },
  "stub-hist-002": {
    booking_id: "stub-hist-002",
    status: "completed",
    visit_at_iso: "2026-05-06T16:00:00+03:00",
    datetime_relative_label: "6 мая",
    duration_min: 120,
    service_name: "Маникюр + парафин",
    master_first_name: "Анна",
    is_nearest: false,
    actions_available: ["open", "repeat"],
    tenant_id: TENANT_BEAUTY_PLACE.id,
    tenant_name: TENANT_BEAUTY_PLACE.name,
    price_approx: 2700,
    address_full: "ул. Тверская 12",
    completed_at: "2026-05-06T18:00:00+03:00",
    can_review: false,
    review_submitted: true,
    repeat_intent_available: true,
  },
  "stub-hist-003": {
    booking_id: "stub-hist-003",
    status: "cancelled",
    visit_at_iso: "2026-04-28T16:00:00+03:00",
    datetime_relative_label: "28 апреля",
    duration_min: 90,
    service_name: "Маникюр",
    master_first_name: "Анна",
    is_nearest: false,
    actions_available: ["open", "repeat"],
    tenant_id: TENANT_BEAUTY_PLACE.id,
    tenant_name: TENANT_BEAUTY_PLACE.name,
    price_approx: 1800,
    address_full: "ул. Тверская 12",
    cancellation_context: "by_customer",
    refund_status: "none",
    repeat_intent_available: true,
  },
  "stub-hist-004": {
    booking_id: "stub-hist-004",
    status: "provider_cancelled",
    visit_at_iso: "2026-04-22T11:00:00+03:00",
    datetime_relative_label: "22 апреля",
    duration_min: 60,
    service_name: "Массаж лимфодренаж",
    master_first_name: "Ирина",
    is_nearest: false,
    actions_available: ["open", "repeat"],
    tenant_id: TENANT_BEAUTY_PLACE.id,
    tenant_name: TENANT_BEAUTY_PLACE.name,
    price_approx: 2200,
    address_full: "ул. Тверская 12",
    cancellation_context: "by_provider",
    refund_status: "completed",
    refund_amount: 2200,
    repeat_intent_available: true,
  },
  "stub-hist-005": {
    booking_id: "stub-hist-005",
    status: "no_show",
    visit_at_iso: "2026-04-15T16:00:00+03:00",
    datetime_relative_label: "15 апреля",
    duration_min: 90,
    service_name: "Маникюр",
    master_first_name: "Анна",
    is_nearest: false,
    actions_available: ["open", "repeat"],
    tenant_id: TENANT_BEAUTY_PLACE.id,
    tenant_name: TENANT_BEAUTY_PLACE.name,
    price_approx: 1800,
    address_full: "ул. Тверская 12",
    cancellation_context: "by_system_no_show",
    no_show_penalty: 100,
    repeat_intent_available: true,
  },
};

// ---------------------------------------------------------------------------
// Public functions
// ---------------------------------------------------------------------------

/**
 * Fetch the list of bookings for a section. Per Tau §3 — backend
 * pre-computes Variant C groupings; frontend renders verbatim.
 *
 * WIRING NOTE — STUB MODE: returns hardcoded sample data per the
 * picked variant. Replace with real
 * `GET /api/v1/me/bookings?section=...` once W4 proxy ships.
 */
export async function getMyBookings(
  section: BookingSection,
  _opts?: { filter?: string },
): Promise<BookingsListResponse> {
  const variant = pickStubVariant();
  // Simulate realistic latency for skeleton testing (~350ms).
  await new Promise<void>((resolve) => setTimeout(resolve, 350));
  return section === "upcoming"
    ? UPCOMING_STUB[variant]
    : HISTORY_STUB[variant];
}

/**
 * Fetch a single booking detail. Per Tau §5 — single tall scroll
 * detail screen consumes the full payload.
 *
 * WIRING NOTE — STUB MODE: returns the precomputed detail blob for
 * known stub IDs. Unknown IDs (e.g. a deeplink from a real booking
 * card that the stub doesn't have) resolve to a 404-shaped error so
 * the screen exercises the «booking deleted (race)» state from
 * Tau §9.2.
 */
export async function getBookingDetail(bookingId: string): Promise<BookingDetail> {
  await new Promise<void>((resolve) => setTimeout(resolve, 300));
  const blob = DETAIL_STUB[bookingId];
  if (!blob) {
    // Mimic API 404 — caller renders «Этой записи больше нет.»
    throw new BookingNotFoundError(bookingId);
  }
  return blob;
}

/**
 * Surface a typed error so the detail screen can branch into the
 * Tau §9.2 «booking removed» state vs the generic error path.
 */
export class BookingNotFoundError extends Error {
  constructor(public readonly bookingId: string) {
    super(`booking ${bookingId} not found`);
    this.name = "BookingNotFoundError";
  }
}

/**
 * Request the repeat-intent for a past booking. Per Tau §10.4 —
 * three states:
 *   - prefill_available=true → land on booking-flow F3 with master +
 *     service preselected.
 *   - prefill_available=false + alternative_suggestion → graceful
 *     fallback voice line.
 *   - prefill_available=false + no alternative → catalog F1.
 *
 * WIRING NOTE — STUB MODE: always returns happy-path prefill for
 * stub bookings flagged `repeat_intent_available=true`.
 */
export async function getRepeatIntent(
  bookingId: string,
): Promise<RepeatIntentResponse> {
  await new Promise<void>((resolve) => setTimeout(resolve, 200));
  const detail = DETAIL_STUB[bookingId];
  if (!detail || !detail.repeat_intent_available) {
    return {
      prefill_available: false,
    };
  }
  return {
    prefill_available: true,
    // Stub: route to existing customer booking flow. Real backend will
    // include resolved master/service ids + a deep-link.
    booking_flow_url: "/customer/catalog",
  };
}

/**
 * Send a message via the AMM modal. Per AMM §5.1 — frontend posts a
 * reason key + optional text; backend routes to master mobile.
 *
 * WIRING NOTE — STUB MODE: always succeeds. Real backend will fail
 * with a typed error (e.g. anti-spam 3/day, master suspended) that
 * the modal's confirmation state handles.
 */
export async function sendBookingMessage(
  input: MessageSendInput,
): Promise<MessageSendResult> {
  await new Promise<void>((resolve) => setTimeout(resolve, 400));
  const detail = DETAIL_STUB[input.booking_id];
  return {
    delivered: true,
    master_first_name: detail?.master_first_name ?? "мастеру",
    tenant_name: detail?.tenant_name ?? "салон",
  };
}

// ---------------------------------------------------------------------------
// Render helpers — keep render-time logic centralised.
// ---------------------------------------------------------------------------

/**
 * Map raw backend status to the customer-visible badge rendering.
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
 * Time-grouping label for upcoming bookings (Tau §3.2 «── Завтра ──» /
 * «── На этой неделе ──» / «── Через неделю ──»). Computed against
 * customer's local clock (Date math — backend cannot inject «завтра»
 * because of TZ drift between request + render).
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

/**
 * Bucket bookings by tenant-stable groupings AND by time-group within
 * each tenant. Used by `CustomerRecordsScreen` to satisfy the Variant
 * C adaptive layout from Tau §3.2-§3.3.
 *
 * Returns groupings in backend order (already tenant-sorted), with
 * each grouping's `bookings` array carrying a `_timeGroup` marker
 * computed from `visit_at_iso`. For history section we group by month
 * instead.
 */
export interface AnnotatedBookingItem extends BookingListItem {
  _timeGroup: string;
}
export interface AnnotatedGrouping {
  tenant_id: string;
  tenant_name: string;
  bookings: AnnotatedBookingItem[];
}

export function annotateGroupings(
  groupings: BookingGrouping[],
  section: BookingSection,
  now: Date = new Date(),
): AnnotatedGrouping[] {
  return groupings.map((g) => ({
    tenant_id: g.tenant_id,
    tenant_name: g.tenant_name,
    bookings: g.bookings.map((b) => ({
      ...b,
      _timeGroup:
        section === "upcoming"
          ? timeGroupLabel(new Date(b.visit_at_iso), now)
          : monthGroupLabel(new Date(b.visit_at_iso)),
    })),
  }));
}

const RU_MONTHS_GENITIVE = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

function monthGroupLabel(d: Date): string {
  // «Май 2026» — nominative case for headings (Tau §3.4).
  const m = d.getMonth();
  const y = d.getFullYear();
  // Use the genitive's stem capitalised. «Мая 2026» reads odd as a
  // header — switch to nominative.
  const NOMINATIVE = [
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
  return `${NOMINATIVE[m]} ${y}`;
}

/**
 * Re-export for callers that want the genitive month name (used in
 * date-relative labels like «28 апреля»).
 */
export const RU_MONTHS_GEN = RU_MONTHS_GENITIVE;

/**
 * Pluraliser — «1 запись / 2 записи / 5 записей». Reused for the
 * tab counter badges + «Показать ещё (N)» CTA.
 */
export function ruPluralBookings(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "записей";
  if (mod10 === 1) return "запись";
  if (mod10 >= 2 && mod10 <= 4) return "записи";
  return "записей";
}
