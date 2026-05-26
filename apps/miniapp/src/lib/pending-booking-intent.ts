/**
 * `pending_booking_intent` — anonymous gate context preservation.
 *
 * Spec: `docs/screens/customer-booking-flow.md` §6.2 +
 * `docs/design/policies/anonymous-to-registered-gate.md`.
 *
 * **P0 ACCEPTANCE CRITERION** (founder cut #6): after a customer
 * registers via the MAX OAuth flow triggered on F4 anonymous gate,
 * the full booking context MUST be preserved end-to-end. Losing any
 * field = P0 BUG.
 *
 * Strategy (TL Q4): frontend stores the intent in `sessionStorage`
 * BEFORE redirect to MAX OAuth; on callback to `/customer/booking/confirm`
 * the screen reads + clears the intent and rehydrates the flow.
 *
 * W4 backend extension (TL Q4 — defence in depth): when ready,
 * `POST /auth/verify` will additionally return any `pending_booking_intent`
 * that the server-side persisted for cross-device scenarios.
 * Frontend's sessionStorage path is the PRIMARY restore — backend
 * round-trip is supplementary.
 *
 * Storage key prefixed with `max:` matching the existing convention
 * in `lib/max-sdk.ts` (DeviceStorage fallback to localStorage).
 */

const STORAGE_KEY = "max:pending_booking_intent";

export interface PendingBookingIntent {
  master_id: string;
  service_id: string;
  /** ISO 8601 with offset (matches createBooking `visit_at`). */
  slot_iso: string;
  price_rub: number;
  /** Optional customer-typed note to the master. */
  note?: string;
  /** Optional loyalty choice (e.g. "apply_100") if customer set it. */
  loyalty_choice?: string;
  /** Auxiliary display fields preserved for context restoration. */
  service_name?: string;
  master_name?: string;
}

/**
 * Persist the intent before redirecting to MAX OAuth. Idempotent —
 * overwrites any prior intent. Safe in SSR / private-mode environments
 * (best effort, swallow exceptions).
 */
export function savePendingIntent(intent: PendingBookingIntent): void {
  if (typeof window === "undefined" || !window.sessionStorage) return;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(intent));
  } catch {
    /* private mode / quota — best effort. The downside is the user
     * loses context post-registration, but that is preferable to
     * crashing the redirect flow. */
  }
}

/**
 * Restore the intent post-OAuth callback AND remove it from storage in
 * a single atomic operation. Returns `null` if no intent stored or
 * if the payload is corrupt (in which case the corrupt entry is
 * cleared too — defence against poisoned state from a prior session).
 */
export function restorePendingIntent(): PendingBookingIntent | null {
  if (typeof window === "undefined" || !window.sessionStorage) return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    // Atomic clear — even if parse fails, we drop the bad entry so the
    // user doesn't keep restoring corrupt data on every load.
    window.sessionStorage.removeItem(STORAGE_KEY);
    const parsed = JSON.parse(raw) as unknown;
    if (!isValidIntent(parsed)) return null;
    return parsed;
  } catch {
    // JSON parse OR storage access failure — bail safely.
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
    return null;
  }
}

/**
 * Read-only peek (does NOT clear). Used by callers that need to
 * detect the presence of a pending intent without consuming it
 * (e.g. routing decisions during boot).
 */
export function peekPendingIntent(): PendingBookingIntent | null {
  if (typeof window === "undefined" || !window.sessionStorage) return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    return isValidIntent(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function clearPendingIntent(): void {
  if (typeof window === "undefined" || !window.sessionStorage) return;
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

function isValidIntent(v: unknown): v is PendingBookingIntent {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  return (
    typeof o.master_id === "string" &&
    typeof o.service_id === "string" &&
    typeof o.slot_iso === "string" &&
    typeof o.price_rub === "number"
  );
}

export const _PENDING_INTENT_STORAGE_KEY = STORAGE_KEY;
