/**
 * Saved cards seam — C7.2 (frozen contract PILOT_CONTRACTS §7.5).
 *
 * NOT WIRED YET: the customer-facing passthrough path is W3's to ship
 * (Ayla internal endpoints exist per C7.2 — setup / list / delete on
 * `/api/v1/internal/users/{id}/cards*`); inventing the bot-side path
 * here would be exactly the endpoint-guessing the pilot forbids. The
 * screen renders from this seam; when W3 freezes the passthrough path,
 * ONLY this file's bodies change (screen + tests untouched).
 */

export interface SavedCard {
  id: string;
  /** Card brand as reported by the backend («mir», «visa», …). */
  brand: string;
  last4: string;
}

/**
 * Returns the customer's saved cards. Pre-passthrough: always empty —
 * the screen shows its honest empty state.
 */
export async function getSavedCards(): Promise<SavedCard[]> {
  return [];
}
