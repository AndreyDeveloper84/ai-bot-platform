/**
 * Booking status taxonomy — Tier 1 Priority 5 Phase B (customer records).
 *
 * Spec: `docs/screens/customer-records-flow.md` §6 (R4 — Status vocabulary).
 *
 * # Why this module
 *
 * The Ayla canonical backend (per ADR-0009) emits a wider booking-status
 * vocabulary than the customer Records UI surfaces. Tau §6 specifies 8
 * customer-visible states; the underlying state machine (per
 * `customer-cancellation-reschedule-spec.md` §2 + payment/refund lifecycle)
 * has up to ~17 internal values (chargeback, dispute, in-progress, etc).
 *
 * This module is the SINGLE place where backend statuses are mapped to
 * customer-visible labels + icons + colour tints. Render code MUST go
 * through `mapBookingStatus(...)` so that:
 *   - A backend taxonomy change is a one-file edit, not a hunt.
 *   - Unknown / new values fail safely to a generic «В обработке» badge
 *     (and emit a `console.warn` in DEV — defence-in-depth, per the
 *     w1-main tech-lead checklist before merge).
 *
 * # ТЛ verdicts (Q1, locked 2026-05-26 — W1 verifies before merge)
 *
 * The 8 customer-visible buckets:
 *
 *   confirmed         → ✓ «Подтверждена»          sage
 *   rescheduled       → calendar-arrow «Перенесена» muted
 *   cancelled_customer→ ✕ «Отменена»              muted
 *   provider_cancelled→ alert «Отменена салоном»  warning (NOT sage)
 *   completed         → check-double «Прошла»     sage
 *   no_show           → minus «Не пришла»         muted
 *   refund_pending    → clock «Возврат в обработке» muted
 *   refund_completed  → money-check «Возврат завершён» sage
 *
 * Per ТЛ verdict: chargeback / dispute / refund-disputed collapse into
 * `refund_pending` (customer doesn't care about the legal substrate —
 * what matters is «money is on its way back»).
 *
 * # SVG icons
 *
 * Per Tau §6 + memory `project_records_voice_principles` — NO emoji-only
 * icons (rendering inconsistent Android/iOS/MAX webview). We use small
 * inline SVG glyphs sized 16×16, drawn from a single component (no
 * external icon library — keeps bundle tiny). Emoji chips in the AMM
 * modal §4 are OK; status badges are SVG-only.
 *
 * # Voice rules (locked, founder)
 *
 *   - Russian feminine («Подтверждена» / «Прошла» — «запись» fem noun).
 *   - «Не пришла» — NOT «Не состоялась» (per founder review Tau Records
 *     Phase B 2026-05-26: «Не состоялась» reads obvuining; «Не пришла»
 *     is factual). Memory `project_records_voice_principles`.
 *   - `provider_cancelled` uses warning tint (muted amber), NEVER
 *     sage-green — a salon cancelling on the customer is NOT a positive
 *     outcome, even with a full refund. Memory ref above.
 */

// ---------------------------------------------------------------------------
// Customer-visible status keys (8)
// ---------------------------------------------------------------------------

export type CustomerVisibleStatus =
  | "confirmed"
  | "rescheduled"
  | "cancelled_customer"
  | "provider_cancelled"
  | "completed"
  | "no_show"
  | "refund_pending"
  | "refund_completed"
  | "awaiting_payment";

/** Colour tint vocabulary — drives CSS class suffix on `<StatusBadge>`. */
export type StatusTint = "sage" | "muted" | "warning";

/** Icon glyph keys — see {@link StatusBadge} for SVG impls. */
export type StatusIcon =
  | "check"
  | "calendar-arrow"
  | "x"
  | "alert"
  | "check-double"
  | "minus"
  | "clock"
  | "money-check"
  | "info";

export interface StatusRendering {
  label: string;
  icon: StatusIcon;
  tint: StatusTint;
}

/**
 * The customer-visible badge vocabulary. Single source of truth for
 * label + icon + tint per status. Render code MUST consume this map
 * via {@link mapBookingStatus} — never hard-code labels at the call
 * site, otherwise a backend taxonomy refresh fragments across screens.
 *
 * Per ТЛ Q1 PLACEHOLDER (locked 2026-05-26): the 8 buckets below are
 * the W1 starting point. If Ayla canonical ships finer-grained values
 * (e.g. `chargeback_initiated`), they MUST be added to the BACKEND→UI
 * map below — NOT to this 8-entry vocabulary — so the customer view
 * stays consolidated.
 */
export const STATUS_VOCABULARY: Record<CustomerVisibleStatus, StatusRendering> =
  {
    confirmed: { label: "Подтверждена", icon: "check", tint: "sage" },
    rescheduled: {
      label: "Перенесена",
      icon: "calendar-arrow",
      tint: "muted",
    },
    cancelled_customer: { label: "Отменена", icon: "x", tint: "muted" },
    provider_cancelled: {
      label: "Отменена салоном",
      icon: "alert",
      tint: "warning",
    },
    completed: { label: "Прошла", icon: "check-double", tint: "sage" },
    no_show: { label: "Не пришла", icon: "minus", tint: "muted" },
    refund_pending: {
      label: "Возврат в обработке",
      icon: "clock",
      tint: "muted",
    },
    refund_completed: {
      label: "Возврат завершён",
      icon: "money-check",
      tint: "sage",
    },
    // C7 online path — booking created, payment not finished yet
    // (webview in progress / pending). Neutral muted, NOT warning:
    // nothing went wrong, the customer just hasn't paid yet.
    awaiting_payment: {
      label: "Ожидает оплаты",
      icon: "clock",
      tint: "muted",
    },
  };

/**
 * Backend → UI status alias map. Per ТЛ Q1 verdict, these substrings
 * collapse into the 8 customer-visible buckets.
 *
 * Add NEW backend variants HERE — never widen STATUS_VOCABULARY.
 */
const BACKEND_ALIAS_MAP: Record<string, CustomerVisibleStatus> = {
  // confirmed family
  confirmed: "confirmed",
  in_progress: "confirmed", // visit started but not yet completed — treat as "still active" UI

  // C7 online path (payment created, not yet captured)
  awaiting_payment: "awaiting_payment",
  payment_pending: "awaiting_payment",

  // rescheduled
  rescheduled: "rescheduled",
  reschedule_requested: "rescheduled",

  // cancelled (by customer)
  cancelled: "cancelled_customer",
  cancelled_customer: "cancelled_customer",
  cancel_requested: "cancelled_customer",

  // cancelled (by provider / no-fault)
  provider_cancelled: "provider_cancelled",
  cancelled_by_provider: "provider_cancelled",
  cancelled_by_salon: "provider_cancelled",
  cancelled_by_system_no_show: "no_show", // system-initiated no-show closure

  // completed
  completed: "completed",
  finished: "completed",

  // no_show
  no_show: "no_show",
  noshow: "no_show",

  // refunds (collapse chargeback / dispute → pending per ТЛ verdict)
  refund_pending: "refund_pending",
  refund_initiated: "refund_pending",
  refund_processing: "refund_pending",
  chargeback: "refund_pending",
  chargeback_initiated: "refund_pending",
  dispute: "refund_pending",
  payment_dispute: "refund_pending",

  refund_completed: "refund_completed",
  refunded: "refund_completed",
};

/**
 * Map a backend booking status string to a customer-visible status +
 * rendering. Unknown values fall through to a generic «В обработке»
 * info badge AND emit a `console.warn` in DEV — so the next agent
 * working in this area sees the surface and adds the missing alias.
 *
 * @param backendStatus  Raw status string from Ayla canonical response.
 *                       Normalised: trimmed + lowercased before lookup.
 */
export function mapBookingStatus(backendStatus: string | undefined | null): {
  status: CustomerVisibleStatus | "unknown";
  rendering: StatusRendering;
} {
  if (typeof backendStatus !== "string" || !backendStatus.trim()) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn(
        `[booking-status] empty / non-string backend status — rendering generic fallback`,
      );
    }
    return {
      status: "unknown",
      rendering: { label: "В обработке", icon: "info", tint: "muted" },
    };
  }
  const key = backendStatus.trim().toLowerCase();
  const customerStatus = BACKEND_ALIAS_MAP[key];
  if (!customerStatus) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn(
        `[booking-status] unknown backend status "${backendStatus}" — falling back to generic. Add an alias to BACKEND_ALIAS_MAP.`,
      );
    }
    return {
      status: "unknown",
      rendering: { label: "В обработке", icon: "info", tint: "muted" },
    };
  }
  return {
    status: customerStatus,
    rendering: STATUS_VOCABULARY[customerStatus],
  };
}

/**
 * Which top-level section a status belongs to. Drives Variant C
 * grouping logic in `CustomerRecordsScreen` and decides whether the
 * sticky-bottom action CTA renders (upcoming only — see R3 §3 in
 * `customer-records-flow.md`).
 */
export function sectionFor(status: CustomerVisibleStatus | "unknown"):
  | "upcoming"
  | "history" {
  switch (status) {
    case "confirmed":
    case "rescheduled":
    case "awaiting_payment":
      return "upcoming";
    case "cancelled_customer":
    case "provider_cancelled":
    case "completed":
    case "no_show":
    case "refund_pending":
    case "refund_completed":
    case "unknown":
      return "history";
  }
}
