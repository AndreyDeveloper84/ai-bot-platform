/**
 * Customer booking-flow client lib — Tier 1 Priority 3 Phase B refresh.
 *
 * Spec: `docs/screens/customer-booking-flow.md` §1–§10 (Ayla 3-layer
 * trust ethos + 5-screen F1-F5 + anonymous gate §6.2).
 *
 * Endpoint contracts:
 *   - `getCatalogRecommendations()`   — Tau §10.1 (3-layer hierarchy).
 *     **STUB-ONLY for now** per TL Q1: Alpha owns the real
 *     `/api/v1/catalog/recommendations` endpoint, ETA 6-8h. Frontend
 *     builds against this stub matching Tau §10.1 verbatim; swap the
 *     fetch when Alpha ships. The function signature does NOT change.
 *   - `getCustomerMaster()`, `getCustomerSlots()`, `createCustomerBooking()`
 *     — wire to existing `/api/v1/customer/*` (per TL Q2, namespace
 *     unchanged).
 *
 * Voice / reasoning_text rule (Tau §10.3 + memory
 * `project_ayla_ranking_philosophy`): the `reasoning_text` field on
 * Layer 2 cards is GENERATED SERVER-SIDE; frontend renders it VERBATIM
 * and NEVER paraphrases / generates / rewrites. Anti-patterns
 * («рекомендуем» / «лучший» / «спонсировано» / «премиум» / «топ»)
 * are filtered at backend; frontend trusts but does not re-impose.
 *
 * Tier mapping (Tau §16):
 *   - Layer 2 hard-capped at 3 items per founder cut #1 (defence in
 *     depth — backend MAY return ≤3, frontend silently truncates if
 *     backend ships 4+).
 */

import { fetchMaster, fetchSlots, createBooking } from "./api";
import type {
  MasterDetail,
  FreeSlot,
  CreatedBooking,
} from "./api";

// ---------------------------------------------------------------------------
// Tau §10.1 contract — verbatim shape. Do NOT mutate fields without
// updating Tau spec first.
// ---------------------------------------------------------------------------

export interface CatalogLayer1Item {
  /** Tenant id. */
  id: string;
  /** Tenant display name. */
  title: string;
  /** ISO date of last visit (server-side). */
  last_visit_date: string;
  /** Pre-rendered services summary string per Tau §3.1
   *  (e.g. «маникюр, педикюр»). */
  services_summary: string;
  /** Optional cover photo. */
  photo_url?: string;
}

export interface CatalogLayer2Item {
  /** Tenant id. */
  id: string;
  /** Tenant display name. */
  title: string;
  /**
   * Reasoning text per Tau §10.3 — generated SERVER-SIDE.
   * Frontend renders verbatim. NEVER mutate / paraphrase.
   */
  reasoning_text: string;
  /** Starting price in rubles (integer). */
  starting_price: number;
  /** Human-readable next slot («сегодня 16:00») OR ISO. */
  next_available_slot: string;
  /** Optional master avatar. */
  master_avatar_url?: string;
  /** Rating, e.g. 4.9. */
  rating: number;
}

export interface CatalogLayer3Item {
  /** Category label, e.g. «Ногти». */
  category: string;
  /** Count of masters / services in the category. */
  count: number;
}

export interface CatalogRecommendations {
  layer_1_your_places: CatalogLayer1Item[];
  /** **Hard-capped at 3 items per founder cut #1.** */
  layer_2_ayla_picks: CatalogLayer2Item[];
  layer_3_explore: CatalogLayer3Item[];
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
}
export interface BookingCreateResponse {
  booking: CreatedBooking;
}

// ---------------------------------------------------------------------------
// Public functions
// ---------------------------------------------------------------------------

/**
 * Fetch 3-layer catalog recommendations.
 *
 * WIRING NOTE — STUB MODE: returns hardcoded sample data matching
 * Tau §10.1 contract. Replace stub body with a real
 * `GET /api/v1/catalog/recommendations` call after Alpha ships
 * endpoint per TL Q1. Function signature does NOT change.
 *
 * Hard cap: Layer 2 silently truncated to 3 items per founder cut #1.
 */
export async function getCatalogRecommendations(): Promise<CatalogRecommendations> {
  // ─── BEGIN STUB ──────────────────────────────────────────────────────
  // Sample data shaped per Tau §10.1 verbatim. reasoning_text values
  // taken from Tau §2.3 allowed-list verbatim — they DO NOT contain
  // forbidden tokens («рекомендуем» / «лучший» / «спонсировано»).
  //
  // To enter "anonymous" / "loyal" mode preview during local dev,
  // append `?stub=anonymous` or `?stub=loyal` to the URL — see
  // `pickStubVariant()` below.
  const variant = pickStubVariant();
  const fake: CatalogRecommendations = STUB_DATA[variant];
  // Simulate realistic network latency for skeleton testing — 300ms.
  await new Promise<void>((resolve) => setTimeout(resolve, 300));
  // Defence-in-depth: cap Layer 2 at 3 even if a future backend
  // wiring drift returns more (founder cut #1).
  return {
    ...fake,
    layer_2_ayla_picks: fake.layer_2_ayla_picks.slice(0, 3),
  };
  // ─── END STUB ────────────────────────────────────────────────────────
}

type StubVariant = "standard" | "anonymous" | "loyal";

function pickStubVariant(): StubVariant {
  if (typeof window === "undefined") return "standard";
  try {
    const sp = new URLSearchParams(window.location.search);
    const v = sp.get("stub");
    if (v === "anonymous" || v === "loyal") return v;
  } catch {
    /* SSR / parse failure — fall through */
  }
  return "standard";
}

const STUB_DATA: Record<StubVariant, CatalogRecommendations> = {
  standard: {
    layer_1_your_places: [
      {
        id: "tenant-formula-tela",
        title: "Формула тела",
        last_visit_date: "2026-05-06",
        services_summary: "массаж, спа",
      },
      {
        id: "tenant-studio-natali",
        title: "Студия Натали",
        last_visit_date: "2026-04-15",
        services_summary: "маникюр, педикюр",
      },
    ],
    layer_2_ayla_picks: [
      {
        id: "tenant-beauty-place",
        title: "Beauty Place",
        // Voice: verbatim from Tau §2.3 allowed-list.
        reasoning_text: "20 минут от тебя, рейтинг 4.9",
        starting_price: 1800,
        next_available_slot: "сегодня 18:00",
        rating: 4.9,
      },
      {
        id: "tenant-studio-lotus",
        title: "Студия Лотос",
        reasoning_text: "Свободно раньше всех остальных",
        starting_price: 1600,
        next_available_slot: "сегодня 16:00",
        rating: 4.7,
      },
      {
        id: "tenant-casa-bella",
        title: "Casa Bella",
        reasoning_text: "Подходит под твою цель — снижение стресса",
        starting_price: 2800,
        next_available_slot: "завтра 11:00",
        rating: 4.8,
      },
    ],
    layer_3_explore: [
      { category: "Ногти", count: 24 },
      { category: "Массаж", count: 18 },
      { category: "Ресницы", count: 12 },
      { category: "Косметология", count: 15 },
    ],
  },
  anonymous: {
    // Anonymous: Layer 1 empty (skipped). Layer 2 prominent. Layer 3.
    layer_1_your_places: [],
    layer_2_ayla_picks: [
      {
        id: "tenant-beauty-place",
        title: "Beauty Place",
        reasoning_text: "20 минут от тебя, рейтинг 4.9",
        starting_price: 1800,
        next_available_slot: "сегодня 18:00",
        rating: 4.9,
      },
      {
        id: "tenant-studio-lotus",
        title: "Студия Лотос",
        reasoning_text: "Свободно сегодня в 18:00",
        starting_price: 1600,
        next_available_slot: "сегодня 18:00",
        rating: 4.7,
      },
    ],
    layer_3_explore: [
      { category: "Ногти", count: 24 },
      { category: "Массаж", count: 18 },
      { category: "Ресницы", count: 12 },
      { category: "Косметология", count: 15 },
    ],
  },
  loyal: {
    // Loyal: Layer 1 prominent, Layer 2 softer (1-2 items).
    layer_1_your_places: [
      {
        id: "tenant-formula-tela",
        title: "Формула тела",
        last_visit_date: "2026-05-20",
        services_summary: "Анна свободна — четверг 16:00, твоё обычное время",
      },
      {
        id: "tenant-studio-natali",
        title: "Студия Натали",
        last_visit_date: "2026-04-15",
        services_summary: "Карина свободна — пятница 12:00",
      },
    ],
    layer_2_ayla_picks: [
      {
        id: "tenant-beauty-place",
        title: "Beauty Place",
        reasoning_text: "20 минут от тебя, рейтинг 4.9",
        starting_price: 1800,
        next_available_slot: "сегодня 18:00",
        rating: 4.9,
      },
    ],
    layer_3_explore: [
      { category: "Ногти", count: 24 },
      { category: "Массаж", count: 18 },
    ],
  },
};

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
