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
    /**
     * AMD-017 card read-model: {last4, brand} once a card is bound
     * (filled from the webhook only when payment_method.saved == true),
     * null until then / after revoke.
     */
    card: { last4: string; brand: string } | null;
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

// --- D7 — card binding -------------------------------------------------------

/**
 * Consent text version for the master card-binding checkbox.
 * PLACEHOLDER pending the legal-approved offer text.
 * TODO(legal): replace with the ratified version before pilot. The
 * upstream endpoint takes only tariff+return_url today — the version is
 * tracked here so the legal swap is one edit.
 */
export const MASTER_CARD_CONSENT_VERSION = "offer-0.0-todo-legal";

export interface MasterCardSetupResult {
  confirmation_url: string;
}

/**
 * D7 — start card binding for the session master. The response carries
 * the YooKassa confirmation_url to open in the webview (first payment
 * with save_payment_method). `tariff` decides the bound account
 * (solo=personal / salon=tenant) — pass the C2 subscription tariff.
 */
export async function setupMasterCard(params: {
  tariff: TariffCode;
  returnUrl: string;
}): Promise<MasterCardSetupResult> {
  const res = await request<Envelope<MasterCardSetupResult>>(
    "/billing/card-setup",
    {
      method: "POST",
      body: JSON.stringify({
        tariff: params.tariff,
        return_url: params.returnUrl,
      }),
    },
  );
  return res.data;
}

// --- One-shot debt collection (past_due CTA) --------------------------------

export interface PayDebtResult {
  payment_id: string;
  invoice_id: string;
  /** Null when charged via the saved method — no webview needed. */
  confirmation_url: string | null;
  amount: string;
  status: string;
  subscription_status: string;
}

/**
 * One-shot debt collection for a past_due subscription. 409 `no_debt`
 * means the debt is already gone — the screen shows «долга нет, статус
 * обновится» and refetches C2 for the truth. Errors: 403 foreign
 * specialist, 502 upstream.
 */
export async function payDebt(returnUrl: string): Promise<PayDebtResult> {
  const res = await request<Envelope<PayDebtResult>>("/billing/pay-debt", {
    method: "POST",
    body: JSON.stringify({ return_url: returnUrl }),
  });
  return res.data;
}
