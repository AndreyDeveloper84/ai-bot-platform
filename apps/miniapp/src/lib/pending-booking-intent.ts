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
  /**
   * Optional. Only set when a real price is known (e.g. backend
   * exposes price on service detail). Round-1 amendment: NEVER ship
   * `0` as a sentinel — that violated the P0 «price preserved as
   * known» contract. Absence = price not yet known.
   */
  price_rub?: number;
  /** Optional customer-typed note to the master. */
  note?: string;
  /** Optional loyalty choice (boolean: apply available bonuses). */
  loyalty_choice?: boolean;
  /** Auxiliary display fields preserved for context restoration. */
  service_name?: string;
  master_name?: string;
  /**
   * Optional provenance — where the flow that produced this intent
   * started (DRF-1484 / OPEN_DECISIONS §24.5). Values are grounded in
   * the actual miniapp code, see `resolveEntryPoint`:
   *   - `"catalog"`            — service picked from the catalog
   *   - `"master"`             — flow started from a master's profile
   *   - `"deep_link:<payload>"`— app session opened via a bot deep link
   *   - `"direct"`             — anything else (fallback)
   *
   * Attribution only: validated loosely (any string passes), bounded
   * by {@link MAX_ENTRY_POINT_LEN}, and never required.
   *
   * `tenant_id` is deliberately ABSENT here (§24.5 owner decision):
   * tenant is a property of the current execution/request context
   * (server-resolved from the bot that signed the initData), not of
   * the durable user intent. Cross-tenant safety lives at the
   * create-booking boundary (tenant-scoped lookups, regression-locked
   * by `test_views_create_booking_tenant_guard.py`), not in this
   * snapshot.
   */
  entry_point?: string;
}

/** Bound for the provenance string (backend truncates to the same). */
export const MAX_ENTRY_POINT_LEN = 64;

/**
 * Resolve the intent's provenance at save time. Priority:
 *   1. explicit upstream value (`BookingDraft.entryPoint`) — set by the
 *      screen where the booking draft originated;
 *   2. the MAX `start_param` payload — the app session itself arrived
 *      via a bot deep link;
 *   3. `"direct"` fallback.
 * The payload is attacker-influenceable, so the composed value is
 * truncated to {@link MAX_ENTRY_POINT_LEN}; it is stored as inert
 * provenance text and never parsed back into a route.
 */
export function resolveEntryPoint(
  draftEntryPoint: string | null,
  startPayload: string,
): string {
  if (draftEntryPoint) return draftEntryPoint;
  if (startPayload) {
    return `deep_link:${startPayload}`.slice(0, MAX_ENTRY_POINT_LEN);
  }
  return "direct";
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
  // master_id non-empty string
  if (typeof o.master_id !== "string" || !o.master_id.trim()) return false;
  // service_id non-empty string
  if (typeof o.service_id !== "string" || !o.service_id.trim()) return false;
  // slot_iso non-empty + roughly ISO8601 (YYYY-MM-DDTHH:MM…)
  if (typeof o.slot_iso !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(o.slot_iso))
    return false;
  // price_rub optional + finite non-negative when present
  if (o.price_rub !== undefined) {
    if (
      typeof o.price_rub !== "number" ||
      !Number.isFinite(o.price_rub) ||
      o.price_rub < 0
    )
      return false;
  }
  // note optional + string when present
  if (o.note !== undefined && typeof o.note !== "string") return false;
  // loyalty_choice optional + boolean when present
  if (o.loyalty_choice !== undefined && typeof o.loyalty_choice !== "boolean")
    return false;
  // service_name / master_name optional strings
  if (o.service_name !== undefined && typeof o.service_name !== "string")
    return false;
  if (o.master_name !== undefined && typeof o.master_name !== "string")
    return false;
  // entry_point optional + string when present (provenance, loose values)
  if (o.entry_point !== undefined && typeof o.entry_point !== "string")
    return false;
  return true;
}

export const _PENDING_INTENT_STORAGE_KEY = STORAGE_KEY;
