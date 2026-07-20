/**
 * Client payments client — C7.1 (frozen contract §7.5 + W3 passthrough
 * `apps/miniapp_api/views.py::create_payment`).
 *
 *   POST /api/v1/customer/me/payments/  body {"appointment_id": "<uuid>"}
 *   → Ayla `data` verbatim: {payment_id, confirmation_url, amount,
 *     capture_state, currency}.
 *
 * Amounts NEVER come from the client (C7.1/C7.6 — Ayla prices from the
 * Booking snapshot). One active payment per booking server-side; a
 * repeat call returns the same payment (idempotent upstream).
 *
 * Errors surface as ApiError with the backend slug — notably the
 * C1-neutral 409 `unavailable` (contract §2) and 502
 * `upstream_unavailable`. Callers map them onto honest copy; the
 * booking already exists at this point, so a payment-create failure
 * must never look like a booking failure.
 */

import { request } from "./api";

export interface CreatedPayment {
  payment_id: string;
  /** Null when charged via a saved method (C7.1) — no webview needed. */
  confirmation_url: string | null;
  /** Decimal string per §1 (e.g. "2000.00"). */
  amount: string;
  capture_state: string;
  currency: string;
}

/** C7.1 — create the two-stage payment for an owned booking. */
export function createPayment(appointmentId: string): Promise<CreatedPayment> {
  return request("/me/payments/", {
    method: "POST",
    body: JSON.stringify({ appointment_id: appointmentId }),
  });
}
