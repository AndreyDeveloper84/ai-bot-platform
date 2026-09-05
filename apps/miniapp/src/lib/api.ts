import { getInitData } from "./max-sdk";
import { applyDevBypassHeaders } from "./dev-bypass";

const API_BASE = "/api/v1/customer";

export class ApiError extends Error {
  constructor(readonly status: number, readonly slug: string, readonly detail: string) {
    super(`[${status}] ${slug}: ${detail}`);
    this.name = "ApiError";
  }
}

interface ErrorBody {
  error: string;
  detail: string;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const initData = getInitData();
  const headers = new Headers(init.headers);
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let body: ErrorBody = { error: "http_error", detail: res.statusText };
    try {
      body = (await res.json()) as ErrorBody;
    } catch {
      /* non-JSON 5xx */
    }
    throw new ApiError(res.status, body.error, body.detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- auth ---

/**
 * Server-cached pending booking intent — W4 #844 extension to
 * `/auth/verify`. Backend field names per
 * `apps/miniapp_api/pending_intent.py::_ALLOWED_FIELDS`:
 *
 *   - `master_id`     : str
 *   - `service_id`    : str
 *   - `slot_iso`      : str  (ISO 8601 with offset)
 *   - `price_quoted`  : int | float  (NOTE: not `price_rub`)
 *   - `note`          : str  (truncated to 500 chars server-side)
 *   - `loyalty_apply` : bool (NOTE: not `loyalty_choice`)
 *   - `entry_point`   : str  (provenance, DRF-1484 §24.5; truncated to
 *                             64 chars server-side)
 *
 * Field-name mismatch with the frontend sessionStorage shape
 * (`PendingBookingIntent` in `pending-booking-intent.ts`) is by
 * design — the backend cache schema is the source of truth and the
 * sessionStorage fallback was specced earlier; the booking-confirm
 * screen normalises both shapes when restoring.
 *
 * `tenant_id` is deliberately NOT part of this contract (§24.5 owner
 * decision): tenant belongs to the execution/request context and is
 * server-resolved, never a property of the durable intent snapshot.
 */
export interface ServerPendingBookingIntent {
  master_id: string;
  service_id: string;
  slot_iso: string;
  price_quoted?: number;
  note?: string;
  loyalty_apply?: boolean;
  entry_point?: string;
}

export interface AuthVerifyResponse {
  user: { id: string; channel_user_id: string; display_name: string; client_name: string };
  tenant: { slug: string; name: string; timezone: string };
  /**
   * W4 #844 — server-side cached `pending_booking_intent`. Present
   * (or `null`) on every `/auth/verify` response per backend
   * `views.py::auth_verify`. Used by CustomerBookingConfirmScreen to
   * restore draft across the OAuth round-trip (defence-in-depth
   * multi-device — sessionStorage stays the PRIMARY restore path).
   */
  pending_booking_intent?: ServerPendingBookingIntent | null;
}

/**
 * POST /auth/verify with an optional body. Per the backend contract
 * (`pending_intent.py` docstring), passing `pending_booking_intent`
 * caches it server-side keyed by `BotUser.id` (10min TTL); passing
 * `null` or omitting the body returns the current cached value
 * without mutation.
 *
 * Frontend callers:
 *   - HelloScreen / boot — `authVerify()` (no body) just reads.
 *   - CustomerBookingConfirmScreen (anonymous-gate restore) —
 *     `authVerify({ readCached: true })` triggers the same read path
 *     but the helper makes intent explicit at the call site.
 */
export function authVerify(
  opts?: { intent?: ServerPendingBookingIntent | null; readCached?: boolean },
): Promise<AuthVerifyResponse> {
  if (opts && (opts.intent !== undefined || opts.readCached)) {
    // Send a JSON body. The backend only reads it when content-type
    // is application/json — request() sets that header automatically
    // when `body` is non-empty (api.ts §line ~23).
    const body: Record<string, unknown> = {};
    if (opts.intent !== undefined) {
      body.pending_booking_intent = opts.intent;
    }
    return request("/auth/verify", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  return request("/auth/verify", { method: "POST" });
}

// --- catalog: services ---
export interface Service {
  id: string;
  slug: string;
  name: string;
  short_description: string;
  description: string;
  price_from: string | null;
  duration_min: number | null;
  is_popular: boolean;
  contraindications: string;
  /**
   * DRF-1164 — can the customer book this service at all? False when no
   * bookable master performs it. Backend-computed (`apps/miniapp_api`
   * annotates the catalog query); NEVER derived client-side from a
   * master list, or the answer would differ per screen.
   */
  is_bookable: boolean;
}
export const fetchServices = (): Promise<{ services: Service[] }> =>
  request("/services", { method: "GET" });
export const fetchService = (id: string): Promise<{ service: Service }> =>
  request(`/services/${id}`, { method: "GET" });

// --- catalog: masters ---
export interface Master {
  id: string;
  name: string;
  specialization: string;
  bio: string;
  experience: string;
  rating: string | null;
  photo_url: string;
}
export interface MasterDetail extends Master {
  service_ids: string[];
}
export const fetchMasters = (params?: {
  serviceId?: string;
}): Promise<{ masters: Master[] }> => {
  const q = new URLSearchParams();
  if (params?.serviceId) q.set("service_id", params.serviceId);
  const qs = q.toString();
  return request(`/masters${qs ? `?${qs}` : ""}`, { method: "GET" });
};
export const fetchMaster = (id: string): Promise<{ master: MasterDetail }> =>
  request(`/masters/${id}`, { method: "GET" });

// --- catalog: recommendations (Ayla scorer proxy) ---
/**
 * POST /recommendations — proxies onto Ayla's catalog scoring
 * (`apps/miniapp_api/views.py::customer_recommendations`). Empty body →
 * Ayla's default ranking. The Ayla response is passed through verbatim
 * by the proxy (that view builds no translation layer), so ANY field
 * Ayla starts sending arrives here untouched. Failures (502/503) are
 * the caller's to isolate — picks are optional chrome, never an error
 * screen.
 *
 * # WHY fields (owner ruling 25.08)
 *
 * «Нет displayable WHY → нет блока „Ayla подобрала"». The branded
 * sections may only render a pick the SOURCE explained, so the WHY
 * fields are declared optional here and consumed in
 * `customer-booking.ts::getCatalogBrowse`. Today Ayla sends neither —
 * both stay `undefined` and every branded section hides itself.
 *
 * Two accepted shapes, because the canon names both:
 *
 *   - `reasons: string[]` — owner ruling 25.08, «2–3 коротких
 *     displayable reason»;
 *   - `reasoning_text: string` — `docs/screens/customer-booking-flow.md`
 *     §10.3, one backend-generated line per item.
 *
 * Both must arrive DISPLAY-READY. The frontend never generates,
 * translates or decorates WHY: no internal reason codes, no confidence
 * numbers, no chain-of-thought, no generic stand-ins.
 */
export interface RecommendationScore {
  service_id: string;
  score: number;
  /** Owner ruling 25.08 — display-ready WHY lines, 2–3 short ones. */
  reasons?: string[] | null;
  /** May spec §10.3 — a single display-ready WHY line. */
  reasoning_text?: string | null;
}
export const fetchRecommendations = (): Promise<{
  recommendations: RecommendationScore[];
}> => request("/recommendations", { method: "POST" });

// --- slots ---
export interface FreeSlot {
  date: string;
  start: string;
}
export const fetchSlots = (params: {
  masterId: string;
  serviceId: string;
  dateFrom: string;
  dateTo: string;
}): Promise<{ slots: FreeSlot[] }> => {
  const q = new URLSearchParams({
    master_id: params.masterId,
    service_id: params.serviceId,
    date_from: params.dateFrom,
    date_to: params.dateTo,
  });
  return request(`/slots?${q.toString()}`, { method: "GET" });
};

// --- bookings ---
export interface CreatedBooking {
  id: string;
  service_name: string;
  master_name: string;
  visit_at: string;
  duration_min: number;
  status: string;
}
export const createBooking = (body: {
  service_id: string;
  master_id: string;
  visit_at: string;
  /** AMD-002: user-chosen online payment (C7). */
  payment_required?: boolean;
}): Promise<{ booking: CreatedBooking }> =>
  request("/bookings", { method: "POST", body: JSON.stringify(body) });

// --- bookings: list / detail / cancel / reschedule ---
// Mirrors `apps/miniapp_api/views.py` per
// customer-cancellation-reschedule-spec §3-§5.
export type BookingStatus =
  | "confirmed"
  | "cancel_requested"
  | "reschedule_requested"
  | "cancelled"
  | "rescheduled";

export interface BookingItem {
  id: string;
  status: BookingStatus;
  service_id: string | null;
  service_name: string;
  master_id: string | null;
  master_name: string;
  visit_at: string;
  duration_min: number | null;
  cancel_requested_at: string | null;
  undo_window_seconds: number;
  cancellable: boolean;
  reschedulable: boolean;
  // Phase 4 — post-visit feedback. NULL until customer rates.
  rating: number | null;
  can_rate: boolean;
  /**
   * C7.3 payment read model — present only when the event stream
   * produced a mirror row (hold signal or a payment.* event).
   * `amount` is a Decimal string per §1 (e.g. "2000.00").
   */
  payment?: { capture_state?: string | null; amount?: string | null } | null;
}

export const fetchMyBookings = (params?: {
  past?: boolean;
  limit?: number;
  before?: string;
}): Promise<{ items: BookingItem[]; next_cursor: string | null }> => {
  const q = new URLSearchParams();
  if (params?.past) q.append("status", "past");
  if (params?.limit) q.set("limit", String(params.limit));
  if (params?.before) q.set("before", params.before);
  const qs = q.toString();
  return request(`/bookings/list${qs ? `?${qs}` : ""}`, { method: "GET" });
};

export const fetchBooking = (id: string): Promise<{ booking: BookingItem }> =>
  request(`/bookings/${id}`, { method: "GET" });

export type CancelReasonClass = "timing" | "plans_changed" | "not_needed" | "other";

export const cancelBookingRequest = (
  id: string,
  body?: { reason_class?: CancelReasonClass; reason_text?: string },
): Promise<{ booking: BookingItem }> =>
  request(`/bookings/${id}/cancel`, {
    method: "POST",
    body: JSON.stringify(body ?? {}),
  });

export const cancelBookingConfirm = (id: string): Promise<{ booking: BookingItem }> =>
  request(`/bookings/${id}/cancel/confirm`, { method: "POST" });

export const cancelBookingUndo = (id: string): Promise<{ booking: BookingItem }> =>
  request(`/bookings/${id}/cancel/undo`, { method: "POST" });

export const rescheduleBookingRequest = (
  id: string,
  body: { new_master_id: string; new_service_id: string; new_visit_at: string },
): Promise<{ booking: BookingItem }> =>
  request(`/bookings/${id}/reschedule`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const rescheduleBookingConfirm = (
  id: string,
): Promise<{ old_booking: BookingItem; new_booking: BookingItem }> =>
  request(`/bookings/${id}/reschedule/confirm`, { method: "POST" });

// --- profile (Phase 3 / F4) ---
export interface Preferences {
  notify_reminders: boolean;
  notify_retention: boolean;
  notify_promo: boolean;
  notify_birthday: boolean;
  birthday_date: string | null; // ISO 8601 yyyy-mm-dd
  // DRF-1371 removed the free-text contraindications member. The column is
  // gone, GET /me no longer sends it, and PATCH /me now answers 400 for it —
  // re-adding it here would only let a screen write a field the API rejects.
}

export interface Profile {
  bot_user_id: string;
  display_name: string;
  client_name: string;
  phone_masked: string;
  timezone: string;
  joined_at: string; // ISO 8601 datetime
  preferences: Preferences;
  favorites: {
    master_name: string | null;
    service_name: string | null;
  };
}

export const fetchProfile = (): Promise<Profile> => request("/me", { method: "GET" });

export const updateProfile = (
  patch: Partial<Pick<Profile, "client_name" | "timezone">> & Partial<Preferences>,
): Promise<Profile> =>
  request("/me", { method: "PATCH", body: JSON.stringify(patch) });

export const deleteAccount = (): Promise<{ deleted: true }> =>
  request("/me/delete", {
    method: "POST",
    body: JSON.stringify({ confirmation: "УДАЛИТЬ" }),
  });

// --- feedback (Phase 4 / F5) ---
export interface FeedbackResult {
  booking_id: string;
  rating: number;
  comment: string;
  feedback_at: string;
  handoff_created: boolean;
  task_id: string | null;
}

export const submitFeedback = (
  bookingId: string,
  body: { rating: number; comment?: string },
): Promise<FeedbackResult> =>
  request(`/bookings/${bookingId}/feedback`, {
    method: "POST",
    body: JSON.stringify(body),
  });
