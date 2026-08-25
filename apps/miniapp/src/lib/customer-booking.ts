/**
 * Customer booking-flow client lib — real mirror data (pilot phase 3.1).
 *
 * Spec: `docs/screens/customer-booking-flow.md` §1–§10 (Ayla 3-layer
 * trust ethos + 5-screen F1-F5 + anonymous gate §6.2).
 *
 * # What changed in phase 3.1 (stub removal, orchestrator GO 2026-07-19)
 *
 * The Tau §10.1 3-layer catalog stub (layer_1 your-places /
 * layer_2 ayla-picks with reasoning_text / layer_3 explore categories)
 * was deleted: NO backend ever produced that shape — the Ayla
 * recommendations endpoint (`POST /internal/me/catalog/recommendations/`,
 * proxied by the bot) returns `{service_id, score}` rows only, and the
 * fields the old cards rendered (reasoning_text, next_available_slot,
 * last_visit_date, category counts) have no source. Inventing them
 * client-side was exactly the "fake data in prod" the pilot forbids.
 *
 * The catalog now composes what genuinely exists:
 *
 *   - `fetchServices()` / `fetchMasters()` — bot-mirror catalog
 *     (`apps/miniapp_api` `GET /services`, `GET /masters`; real data
 *     after the W3 link_ayla_service_ids sync);
 *   - `fetchRecommendations()` — Ayla scorer proxy
 *     (`POST /recommendations` → `{service_id, score}`), joined onto
 *     the mirror services HERE (client-side, by id).
 *
 * Picks rule: the scorer is optional chrome. When it is unavailable
 * (502/503/network) the lib returns `picks: []` and the screens hide
 * the picks section silently — never an error screen, never fabricated
 * picks. The founder cut #1 cap (≤3 picks) stays a RENDER-side concern
 * (screens slice), so this lib deliberately does not truncate.
 *
 * # WHY gate (owner ruling 25.08)
 *
 * > «Нет displayable WHY → нет блока „Ayla подобрала".»
 *
 * `Recommendation = WHAT + WHY + WHAT NEXT`. A pick the source did not
 * explain is not a branded Ayla pick, so it never leaves this lib:
 * {@link getCatalogBrowse} keeps only the recommendations that carry
 * display-ready WHY (`reasons[]` per the ruling, or `reasoning_text`
 * per `docs/screens/customer-booking-flow.md` §10.3). Today
 * `POST /recommendations` answers `{service_id, score}` and nothing
 * else, so `picks` comes back empty and both branded sections hide
 * themselves. Nothing is flagged off and no code is deleted: the day
 * the scorer starts sending WHY, the same filter lets it through and
 * the blocks come back on their own.
 *
 * What this lib will NOT do: invent WHY, translate reason codes into
 * prose, or substitute a generic line («подходит тебе», «выбрано по
 * твоей цели», «Ayla рекомендует»). Text is rendered exactly as the
 * source sent it, or the pick is dropped.
 */

import {
  fetchMaster,
  fetchMasters,
  fetchRecommendations,
  fetchServices,
  fetchSlots,
  createBooking,
} from "./api";
import type {
  Master,
  MasterDetail,
  FreeSlot,
  CreatedBooking,
  RecommendationScore,
  Service,
} from "./api";

// ---------------------------------------------------------------------------
// Catalog browse — mirror + scorer composition.
// ---------------------------------------------------------------------------

/**
 * One branded Ayla pick: WHAT (the mirror service id) + WHY (the
 * display-ready lines the SOURCE sent). `reasons` is guaranteed
 * non-empty — a pick without WHY is dropped before it gets here.
 */
export interface ServicePick {
  /** Mirror service id — joined onto `services` by the screens. */
  serviceId: string;
  /** Display-ready WHY, verbatim from the source, 1–3 lines. */
  reasons: string[];
}

export interface CatalogBrowseData {
  /** Active services from the bot mirror (verbatim). */
  services: Service[];
  /** Bookable masters from the bot mirror (verbatim). */
  masters: Master[];
  /**
   * Picks ranked by the Ayla scorer (score desc), filtered to ids
   * present in the mirror AND to the ones the source explained. Empty
   * when the scorer is unavailable, returns nothing usable, or sends no
   * WHY — branded picks sections hide silently then.
   */
  picks: ServicePick[];
}

/** Owner ruling 25.08: WHY is «2–3 коротких» — never a wall of text. */
const MAX_REASONS = 3;

/**
 * Extract the display-ready WHY lines a single recommendation carries.
 *
 * Accepts both canon shapes (`reasons[]` / `reasoning_text`), keeps the
 * strings verbatim apart from trimming, drops blanks, and caps the
 * count. Returns `[]` when the source explained nothing — the caller
 * treats that as «not a branded pick».
 */
function displayableReasons(rec: RecommendationScore): string[] {
  const raw: unknown[] = Array.isArray(rec.reasons)
    ? rec.reasons
    : typeof rec.reasoning_text === "string"
      ? [rec.reasoning_text]
      : [];
  return raw
    .filter((r): r is string => typeof r === "string")
    .map((r) => r.trim())
    .filter((r) => r.length > 0)
    .slice(0, MAX_REASONS);
}

/**
 * Load everything the catalog screen needs. Mirror failures reject
 * (the screen renders its error state); scorer failure is swallowed
 * into `picks: []` per the picks rule above.
 */
export async function getCatalogBrowse(): Promise<CatalogBrowseData> {
  const [servicesRes, mastersRes] = await Promise.all([
    fetchServices(),
    fetchMasters(),
  ]);
  let picks: ServicePick[] = [];
  try {
    const recs = await fetchRecommendations();
    const known = new Set(servicesRes.services.map((s) => s.id));
    picks = recs.recommendations
      .slice()
      .sort((a, b) => b.score - a.score)
      .filter((r) => known.has(r.service_id))
      .map((r) => ({ serviceId: r.service_id, reasons: displayableReasons(r) }))
      // Owner ruling 25.08 — a pick the source did not explain is not a
      // branded Ayla pick. This single line is the whole gate: it lets
      // WHY through the moment the scorer starts sending it.
      .filter((p) => p.reasons.length > 0);
  } catch {
    /* Ayla scorer unavailable — optional chrome, never fake it. */
  }
  return {
    services: servicesRes.services,
    masters: mastersRes.masters,
    picks,
  };
}

// ---------------------------------------------------------------------------
// Customer master / slot / booking — wraps existing /customer/* endpoints.
// ---------------------------------------------------------------------------

export type CustomerMaster = MasterDetail;

export interface SlotsResponse {
  slots: FreeSlot[];
  /**
   * `is_suggested` per slot — Tau §10 W2 tier 1. NOT yet wired by
   * backend; frontend currently treats absence as false. When backend
   * ships per-slot `is_suggested`, plumb here (do NOT add the field
   * client-side; this is a backend-driven UX cue).
   */
}

export interface BookingCreatePayload {
  service_id: string;
  master_id: string;
  visit_at: string;
  /** AMD-002 / C7.4 — user's payment choice from the summary screen. */
  payment_required?: boolean;
}
export interface BookingCreateResponse {
  booking: CreatedBooking;
}

export const getCustomerMaster = (masterId: string): Promise<{ master: CustomerMaster }> =>
  fetchMaster(masterId);

/**
 * Fetch slots for a master across the next `days` window.
 *
 * NOTE: existing backend endpoint requires `service_id`. Caller must
 * pass both `masterId` AND `serviceId` (we accept a unified `params`
 * object). `days` defaults to 14 per Tau §10.1.
 */
export const getCustomerSlots = (params: {
  masterId: string;
  serviceId: string;
  days?: number;
}): Promise<SlotsResponse> => {
  const days = params.days ?? 14;
  const today = new Date();
  const future = new Date(today);
  future.setDate(today.getDate() + days);
  const isoDate = (d: Date): string =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return fetchSlots({
    masterId: params.masterId,
    serviceId: params.serviceId,
    dateFrom: isoDate(today),
    dateTo: isoDate(future),
  });
};

export const createCustomerBooking = (
  payload: BookingCreatePayload,
): Promise<BookingCreateResponse> => createBooking(payload);
