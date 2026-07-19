/**
 * Client payment status read model — C7.3 (frozen contract
 * PILOT_CONTRACTS_2026-08-15 §7.5 + ADR payments-capture-strategy).
 *
 * Single place mapping internal capture states to customer-visible
 * labels, mirroring `booking-status.ts` discipline: render code MUST go
 * through {@link mapPaymentStatus} so a taxonomy change is one edit.
 *
 * Locked UX table (C7.3):
 *
 *   authorized        → «Зарезервировано»
 *   capture_scheduled → «Оплата будет подтверждена после визита»
 *   captured          → «Оплата завершена»
 *   released/canceled → «Резерв отменён, деньги разблокированы»
 *   failed            → «Оплата не прошла»
 *   refunded          → «Оплата возвращена»
 *
 * `waiting_for_capture` is NEVER shown to customers (ADR). Unknown or
 * empty values fail safe to hidden (never a wrong label) + DEV warn.
 */

import type { StatusIcon, StatusRendering } from "./booking-status";

export interface PaymentStatusRendering {
  label: string;
  icon: StatusIcon;
  tint: StatusRendering["tint"];
}

const PAYMENT_STATUS_MAP: Record<string, PaymentStatusRendering> = {
  authorized: { label: "Зарезервировано", icon: "clock", tint: "muted" },
  capture_scheduled: {
    label: "Оплата будет подтверждена после визита",
    icon: "clock",
    tint: "muted",
  },
  captured: { label: "Оплата завершена", icon: "check", tint: "sage" },
  released: {
    label: "Резерв отменён, деньги разблокированы",
    icon: "minus",
    tint: "muted",
  },
  canceled: {
    label: "Резерв отменён, деньги разблокированы",
    icon: "minus",
    tint: "muted",
  },
  failed: { label: "Оплата не прошла", icon: "alert", tint: "warning" },
  refunded: { label: "Оплата возвращена", icon: "money-check", tint: "sage" },
};

/** States hidden from customers per ADR (internal mechanic). */
const HIDDEN_STATES = new Set(["waiting_for_capture"]);

export interface MappedPaymentStatus {
  /** False → render nothing (hidden state / unknown / absent). */
  visible: boolean;
  rendering?: PaymentStatusRendering;
}

/**
 * Map a backend capture_state string to a customer-visible rendering.
 * Unknown values fail safe to hidden — a missing badge is honest, a
 * wrong label is not.
 */
export function mapPaymentStatus(
  state: string | null | undefined,
): MappedPaymentStatus {
  if (typeof state !== "string" || !state.trim()) {
    return { visible: false };
  }
  const key = state.trim().toLowerCase();
  if (HIDDEN_STATES.has(key)) {
    return { visible: false };
  }
  const rendering = PAYMENT_STATUS_MAP[key];
  if (!rendering) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn(
        `[payment-status] unknown capture_state "${state}" — hidden. Add a mapping to PAYMENT_STATUS_MAP.`,
      );
    }
    return { visible: false };
  }
  return { visible: true, rendering };
}
