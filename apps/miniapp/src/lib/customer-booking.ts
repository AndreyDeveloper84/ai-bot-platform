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
 * (502/503/network) the lib returns `pickServiceIds: []` and the
 * screens hide the picks section silently — never an error screen,
 * never fabricated picks. The founder cut #1 cap (≤3 picks) stays a
 * RENDER-side concern (screens slice), so this lib deliberately does
 * not truncate.
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
  CreatedBookingPayment,
  Master,
  MasterDetail,
  FreeSlot,
  CreatedBooking,
  Service,
} from "./api";

// ---------------------------------------------------------------------------
// Catalog browse — mirror + scorer composition.
// ---------------------------------------------------------------------------

export interface CatalogBrowseData {
  /** Active services from the bot mirror (verbatim). */
  services: Service[];
  /** Bookable masters from the bot mirror (verbatim). */
  masters: Master[];
  /**
   * Service ids ranked by the Ayla scorer (score desc), filtered to ids
   * present in the mirror. Empty when the scorer is unavailable or
   * returns nothing usable — picks sections hide silently then.
   */
  pickServiceIds: string[];
}

/**
 * Load everything the catalog screen needs. Mirror failures reject
 * (the screen renders its error state); scorer failure is swallowed
 * into `pickServiceIds: []` per the picks rule above.
 */
export async function getCatalogBrowse(): Promise<CatalogBrowseData> {
  const [servicesRes, mastersRes] = await Promise.all([
    fetchServices(),
    fetchMasters(),
  ]);
  let pickServiceIds: string[] = [];
  try {
    const recs = await fetchRecommendations();
    const known = new Set(servicesRes.services.map((s) => s.id));
    pickServiceIds = recs.recommendations
      .slice()
      .sort((a, b) => b.score - a.score)
      .map((r) => r.service_id)
      .filter((id) => known.has(id));
  } catch {
    /* Ayla scorer unavailable — optional chrome, never fake it. */
  }
  return {
    services: servicesRes.services,
    masters: mastersRes.masters,
    pickServiceIds,
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
  /** C7.4 — present once the W3 passthrough ships; checked tolerantly. */
  payment?: CreatedBookingPayment | null;
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
