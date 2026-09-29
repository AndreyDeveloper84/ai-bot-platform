/**
 * DRF-1482 — контракт пустого каталога: `empty_reason` и причина
 * пустого экрана «ноль услуг при непустых мастерах».
 *
 * Spec: `docs/screens/customer-catalog-empty-states-spec.md` §1–§2.
 *
 * The pilot defect this closes: a free-text search could filter the
 * services list to zero while masters stayed on screen, and the person
 * got NO explanation — the old gate (`visibleServices === 0 &&
 * masters === 0`) simply rendered nothing. Now every empty situation
 * resolves to a reason, and the screen answers «Что Ayla предлагает
 * сделать дальше?» (spec §0.1) instead of «Почему пусто?».
 *
 * Contract (spec §2): `empty_reason ∈ {search_no_match, region_empty,
 * booking_unavailable}`. The reason lives on the server so the API can
 * grow new reasons without breaking the client; the client maps
 * `reason → состояние` and never accumulates conditional logic. What
 * the client still owns:
 *
 *   - `search_no_match` — free-text search never leaves the Mini App,
 *     so only the client can know a search produced zero;
 *   - forward-compat — a reason value this build does not know (the
 *     spec's post-pilot growth: quality_filtered, premium_only, …)
 *     resolves to `"unknown"` and renders the spec §1 default
 *     (booking_unavailable copy), never a blank screen;
 *   - derivation fallback — while an older backend sends no
 *     `empty_reason`, the client derives region_empty /
 *     booking_unavailable from the same signals the server uses
 *     (`is_bookable` comes from DRF-1164 and is NEVER re-derived
 *     client-side from the masters list).
 */

import type { Service } from "./api";

/** Reasons from the spec §2 enum. */
export type CatalogEmptyReason =
  | "search_no_match"
  | "region_empty"
  | "booking_unavailable";

/**
 * Resolved empty state: a known reason, `"unknown"` for a reason value
 * this build does not understand (renders the spec default), or `null`
 * when the catalog has something to offer.
 */
export type ResolvedCatalogEmpty = CatalogEmptyReason | "unknown";

const KNOWN_REASONS: readonly string[] = [
  "search_no_match",
  "region_empty",
  "booking_unavailable",
];

export interface CatalogEmptySignals {
  /** Trimmed, lower-cased free-text query ("" when no search). */
  query: string;
  /** Services left after the search filter is applied. */
  visibleServices: number;
  /** The full, unfiltered catalog (each row carries DRF-1164 is_bookable). */
  services: Service[];
  /** Bookable masters from the mirror. */
  mastersCount: number;
  /** `empty_reason` as `GET /services` sent it; null/absent on old backends. */
  serverReason?: string | null;
}

/**
 * Map the catalog signals to the ONE empty reason, or `null` when the
 * catalog has something to offer. Precedence:
 *
 *   1. `search_no_match` — the search is the proximate cause when it
 *      filters a non-empty catalog to zero (masters may stay visible;
 *      that was the defect);
 *   2. a known server `empty_reason` — the server is authoritative for
 *      the reasons it can see (spec §2);
 *   3. an unrecognized server value → `"unknown"` (forward-compat,
 *      spec §1 default row);
 *   4. client derivation for backends that predate the field;
 *   5. `null` — no empty state.
 */
export function resolveCatalogEmpty(
  signals: CatalogEmptySignals,
): ResolvedCatalogEmpty | null {
  const { query, visibleServices, services, mastersCount, serverReason } =
    signals;

  if (query && visibleServices === 0 && services.length > 0) {
    return "search_no_match";
  }
  if (serverReason != null && serverReason !== "") {
    return KNOWN_REASONS.includes(serverReason)
      ? (serverReason as CatalogEmptyReason)
      : "unknown";
  }
  if (services.length === 0 && mastersCount === 0) {
    return "region_empty";
  }
  if (services.length > 0 && services.every((s) => !s.is_bookable)) {
    return "booking_unavailable";
  }
  return null;
}
