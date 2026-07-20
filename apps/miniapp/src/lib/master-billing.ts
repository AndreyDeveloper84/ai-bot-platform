/**
 * Master billing client — C2 (billing status) + C3 (payout preview),
 * frozen contract PILOT_CONTRACTS_2026-08-15 §3/§4 via the master_api
 * proxies (`GET /api/v1/master/billing/status`,
 * `GET /api/v1/master/payout-preview`). Proxies return the contract
 * `data` envelope verbatim; this lib only unwraps it.
 *
 * Contract notes consumed here:
 *   - Money — Decimal strings with exactly two places (§1).
 *   - AMD-005 — the specialist key is the Ayla User UUID (proxy-side).
 *   - AMD-013 — `next_charge.date` = current_period_end + 1 (charge-in-
 *     advance); canceled → next_charge null. UI copy: «следующее
 *     списание».
 *   - Error slugs: 503 `specialist_mapping_unavailable` (mirror not
 *     synced — new master), 404 `specialist_not_found`, 502
 *     `billing_upstream_unavailable`. Screens map them to honest
 *     states, never invented numbers.
 */

import { request } from "./master-api";

// --- C2 — subscription status ----------------------------------------------

export type SubscriptionStatus =
  | "trial"
  | "active"
  | "past_due"
  | "canceled"
  | "none";

export type TariffCode = "solo" | "salon";

export interface NextCharge {
  subscription_amount: string;
  fees_amount: string;
  total_amount: string;
  /** ISO date — current_period_end + 1 (AMD-013, charge-in-advance). */
  date: string;
}

export interface BillingStatus {
  specialist_id: string;
  subscription: {
    status: SubscriptionStatus;
    tariff: TariffCode | null;
    current_period_end: string | null;
    next_charge: NextCharge | null;
  };
  fees: { pending_total: string; pending_count: number };
  last_invoice: {
    id: string;
    amount: string;
    status: string;
    paid_at: string;
  } | null;
}

// --- C3 — payout preview ----------------------------------------------------

/** C3 capture states that count into pending_amount (§4). */
export type PayoutCaptureState =
  | "scheduled"
  | "captured_pending_settlement"
  | "settled"
  | "capture_failed"
  | "canceled"
  | "refunded";

export interface PayoutItem {
  appointment_id: string;
  completed_at: string;
  amount: string;
  platform_fee: string;
  specialist_income: string;
  capture_state: PayoutCaptureState;
}

export interface PayoutPreview {
  pending_amount: string;
  currency: string;
  expected_settlement_hint: string | null;
  items: PayoutItem[];
}

interface Envelope<T> {
  data: T;
}

/** C2 — subscription status / fees / last invoice for the session master. */
export async function getBillingStatus(): Promise<BillingStatus> {
  const res = await request<Envelope<BillingStatus>>("/billing/status", {
    method: "GET",
  });
  return res.data;
}

/** C3 — pending payout amount + per-appointment breakdown. */
export async function getPayoutPreview(): Promise<PayoutPreview> {
  const res = await request<Envelope<PayoutPreview>>("/payout-preview", {
    method: "GET",
  });
  return res.data;
}
