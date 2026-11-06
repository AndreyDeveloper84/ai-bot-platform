/**
 * Saved cards client — C7.2 (frozen contract §7.5 + W3 passthrough).
 *
 *   GET    /api/v1/customer/me/cards/        → {cards: [{id, last4, brand}]}
 *   POST   /api/v1/customer/me/cards/setup/  → {confirmation_url}
 *   DELETE /api/v1/customer/me/cards/{id}/   → 204 (idempotent)
 *
 * Consent boundary (C7.2, locked): card saving is a SEPARATE voluntary
 * action — never a side effect of paying. The client sends
 * `consent_version` + `consented_at` with every setup call. The server
 * requires `consent_version` to be present (400 without): its presence is
 * the act of consent. Neither value is what gets recorded (DRF-2681) —
 * the catalog stamps its own version and its own time, and `consented_at`
 * is dropped at the bot door. The saved method is used ONLY for
 * user-initiated payments — no autocharges in the pilot (AYLA-DEC-0001);
 * after a revoke, the method is never charged again.
 */

import { request } from "./api";

export interface SavedCard {
  id: string;
  /** Card brand as reported by the backend («mir», «visa», …). */
  brand: string;
  last4: string;
}

/**
 * Version of the consent text THIS build shows on the cards screen.
 *
 * Not the recorded truth (DRF-2681): the catalog stamps the version from
 * its own `CLIENT_CARD_CONSENT_VERSION` setting and only compares this
 * value with it, logging a mismatch. So the two change together — a new
 * text here without the setting there warns on every single binding.
 *
 * PLACEHOLDER pending the legal-approved offer text (orchestrator 2026-07-19).
 * TODO(legal): replace with the ratified offer version before pilot.
 */
export const CLIENT_CARDS_CONSENT_VERSION = "offer-client-cards-0.0-todo-legal";

interface CardsEnvelope {
  cards: Array<{ id: string; brand: string; last4: string }>;
}

/** List the customer's saved cards. */
export async function getSavedCards(): Promise<SavedCard[]> {
  const res = await request<CardsEnvelope>("/me/cards/", { method: "GET" });
  return res.cards;
}

/**
 * Start card binding — returns the confirmation_url to open in the
 * webview. Call ONLY after the user explicitly checked the consent box
 * in the UI (C7.2): the timestamp is taken at the moment of the call.
 */
export function setupCard(): Promise<{ confirmation_url: string }> {
  return request("/me/cards/setup/", {
    method: "POST",
    body: JSON.stringify({
      consent_version: CLIENT_CARDS_CONSENT_VERSION,
      consented_at: new Date().toISOString(),
    }),
  });
}

/** Revoke a saved card. Idempotent server-side (repeat → 204). */
export async function deleteCard(cardId: string): Promise<void> {
  await request<void>(`/me/cards/${cardId}/`, { method: "DELETE" });
}
