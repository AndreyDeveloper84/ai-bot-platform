/**
 * Admin Mini App API client — admin bootstrap + MM5 deactivation cascade.
 *
 * Mirrors the backend endpoints:
 *   GET  /api/v1/me                                              (identity)
 *   GET  /api/v1/admin/masters/                                  (PR #405)
 *   POST /api/v1/admin/masters/<id>/deactivation-preview/        (PR #467)
 *   POST /api/v1/admin/masters/<id>/deactivate/                  (PR #467, owner-only)
 *   POST /api/v1/admin/masters/<id>/reactivate/                  (PR #467, owner-only)
 *
 * Auth mirrors the master surface — MAX initData header + dev-bypass
 * shim. Errors are surfaced as ApiError for consistent UI handling.
 *
 * Spec: docs/design/handoffs/2026-05-18-master-management-handoff.md
 *       §MM1 (roster list) + §MM5 (deactivation cascade).
 */

import { getInitData } from "./max-sdk";
import { ApiError } from "./api";
import { applyDevBypassHeaders } from "./dev-bypass";

interface ErrorBody {
  error: string;
  detail: string;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const result = await requestWithResponse<T>(path, init);
  return result.data;
}

interface ResponseEnvelope<T> {
  data: T;
  response: Response;
}

/**
 * Same as ``request`` but exposes the raw ``Response`` so callers can
 * inspect headers (e.g. ``X-Idempotent`` on the invite endpoint). The
 * regular ``request`` helper wraps this so existing call sites stay
 * one-liners. AbortSignal flows through via ``init.signal``.
 */
async function requestWithResponse<T>(
  path: string,
  init: RequestInit = {},
): Promise<ResponseEnvelope<T>> {
  const initData = getInitData();
  const headers = new Headers(init.headers);
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let parsed: ErrorBody = { error: "http_error", detail: res.statusText };
    try {
      parsed = (await res.json()) as ErrorBody;
    } catch {
      /* non-JSON 5xx */
    }
    throw new ApiError(res.status, parsed.error, parsed.detail);
  }
  if (res.status === 204) {
    return { data: undefined as T, response: res };
  }
  const data = (await res.json()) as T;
  return { data, response: res };
}

// --- /api/v1/me ----------------------------------------------------------

export interface MeUser {
  id: string;
  name: string;
  phone_masked: string;
}

export interface MeTenant {
  id: string;
  name: string;
  slug: string;
}

export type RoleSlug =
  | "customer"
  | "master"
  | "receptionist"
  | "admin"
  | "owner";

export interface MeResponse {
  user: MeUser;
  tenant: MeTenant;
  role: RoleSlug | string;
  capabilities: string[];
  is_customer: boolean;
  is_master: boolean;
  is_receptionist: boolean;
  is_admin: boolean;
  is_owner: boolean;
  master_id: string | null;
  landing_path: string;
  /**
   * Solo provider hint — true when the tenant has exactly one distinct
   * active person (the `len(active_staff_user_ids ∪
   * active_master_user_ids) == 1` rule per Tau §3.1). Powered by
   * `is_solo_provider(tenant)` on the backend (W4 PR #760). Frontend
   * uses this to skip the admin/master chooser and render the 8-tab
   * unified solo surface per Tau §5. Optional in the TS type to keep
   * graceful fallback if an older backend ship omits the field —
   * App.tsx treats absence as `false`.
   */
  is_solo_provider?: boolean;
}

export const getMe = (): Promise<MeResponse> =>
  request("/api/v1/me", { method: "GET" });

// --- /api/v1/admin/day/ --------------------------------------------------

/**
 * One visit on the salon's day.
 *
 * `client_last_initial` is an initial, not a surname, and there is no
 * phone field at all — the backend does not resolve one on this path
 * (DRF-1039).
 */
export interface SalonDayVisit {
  id: string;
  /**
   * Catalog service id — what the slots endpoint takes.
   *
   * Empty when the mirrored booking's Ayla service maps to nothing local.
   * A reschedule cannot be offered then: there is no way to ask which
   * starts are bookable, and offering arbitrary times would be the
   * client inventing availability (§17).
   */
  service_id: string;
  start_at: string | null;
  end_at: string | null;
  duration_min: number;
  status: string;
  service_name: string;
  client_first_name: string;
  client_last_initial: string;
  is_in_progress: boolean;
}

export interface SalonDayMaster {
  master_id: string;
  name: string;
  is_active: boolean;
  visits: SalonDayVisit[];
}

export interface SalonDaySummary {
  total: number;
  upcoming: number;
  completed: number;
  released: number;
}

export interface SalonDayResponse {
  date: string;
  timezone: string;
  summary: SalonDaySummary;
  masters: SalonDayMaster[];
  /**
   * Visits whose specialist matches no catalog master. Surfaced rather
   * than dropped — a booking nobody can see is the failure the salon
   * surface exists to prevent.
   */
  orphan_visits: SalonDayVisit[];
}

/** Statuses that mean the slot was freed — rendered struck through. */
export const RELEASED_VISIT_STATUSES: ReadonlySet<string> = new Set([
  "cancelled",
  "no_show",
]);

/**
 * GET /api/v1/admin/day/ — every master's visits for one calendar day.
 *
 * `date` is `YYYY-MM-DD` in the tenant's timezone; omit it for the
 * salon's today (which is not necessarily the server's today).
 */
export const getSalonDay = (
  date?: string,
  init: { signal?: AbortSignal } = {},
): Promise<SalonDayResponse> => {
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  return request<SalonDayResponse>(`/api/v1/admin/day/${qs}`, {
    method: "GET",
    signal: init.signal,
  });
};

// --- POST /api/v1/admin/bookings/ (UX contract §18) -----------------------

/**
 * What happened to a submitted booking.
 *
 * Mirrors `SubmitOutcome` in `booking-draft.ts` and the backend's
 * `outcome` field. The backend sends it explicitly rather than letting
 * the client infer it from a status code, because a code is lossy: 409
 * could be «slot taken» or «already exists», and the screen must react
 * differently.
 */
export interface CreateBookingResult {
  outcome: "committed" | "conflict" | "blocked" | "pending" | "failed";
  detail: string;
  appointment_id?: string;
  /** Returned on `pending` so a retry can be the same write, not a new one. */
  idempotency_key?: string;
}

export interface CreateBookingBody {
  master_id: string;
  service_id: string;
  /** ISO start, as the schedule gave it. */
  start_at: string;
  /** Stable across retries of the same draft — see the screen. */
  idempotency_key: string;
  /** Exactly one of these two paths (§14). */
  client_id?: string;
  client_name?: string;
  client_phone?: string;
}

/**
 * Create the appointment.
 *
 * Never throws on a business outcome — every one of the five is a normal
 * answer the screen renders differently. Only a transport failure throws,
 * and that is itself reported as `pending`: a write that did not answer
 * may still have landed, and «Do not claim creation» cuts both ways.
 */
export const createSalonBooking = async (
  body: CreateBookingBody,
): Promise<CreateBookingResult> => {
  const initData = getInitData();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);

  let res: Response;
  try {
    res = await fetch("/api/v1/admin/bookings/", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch {
    return {
      outcome: "pending",
      detail: "нет ответа от сети",
      idempotency_key: body.idempotency_key,
    };
  }

  try {
    const data = (await res.json()) as Partial<CreateBookingResult>;
    if (data.outcome) return data as CreateBookingResult;
    return { outcome: "failed", detail: data.detail ?? "неизвестная ошибка" };
  } catch {
    // A body we cannot read on a write is not evidence that nothing
    // happened. Treat it as unknown rather than as failure.
    return {
      outcome: "pending",
      detail: "ответ не прочитан",
      idempotency_key: body.idempotency_key,
    };
  }
};

// --- POST /api/v1/admin/bookings/<id>/cancel/ (UX contract §19) ----------

/** The salon's closed allowlist of cancellation reasons. */
export const CANCEL_REASONS = [
  { code: "master_unavailable", label: "Мастер не сможет" },
  { code: "tenant_closed_slot", label: "Салон закрыт" },
  { code: "other", label: "Другое" },
] as const;

export type CancelReasonCode = (typeof CANCEL_REASONS)[number]["code"];

export interface CancelBookingResult {
  outcome: "committed" | "conflict" | "blocked" | "pending" | "failed";
  detail: string;
  appointment_id?: string;
}

/**
 * Cancel a booking.
 *
 * Same five outcomes as creation and, as there, never throws on a
 * business answer. `pending` carries more weight here than anywhere
 * else: a cancellation reported as failed invites a second press, and a
 * second press on an already-cancelled booking is how a customer gets
 * told twice that their appointment is off.
 */
export const cancelSalonBooking = async (
  appointmentId: string,
  body: { reason_code?: CancelReasonCode; reason?: string } = {},
): Promise<CancelBookingResult> => {
  const initData = getInitData();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);

  let res: Response;
  try {
    res = await fetch(
      `/api/v1/admin/bookings/${encodeURIComponent(appointmentId)}/cancel/`,
      { method: "POST", headers, body: JSON.stringify(body) },
    );
  } catch {
    return { outcome: "pending", detail: "нет ответа от сети" };
  }

  try {
    const data = (await res.json()) as Partial<CancelBookingResult>;
    if (data.outcome) return data as CancelBookingResult;
    return { outcome: "failed", detail: data.detail ?? "неизвестная ошибка" };
  } catch {
    // Unreadable body on a write is not evidence that nothing happened.
    return { outcome: "pending", detail: "ответ не прочитан" };
  }
};

// --- closing a visit (UX contract §19) -----------------------------------

/** The canonical facts, read straight from the schedule. */
export interface BookingVersion {
  id: string;
  version: number;
  status: string;
  start_datetime: string;
}

/**
 * GET /api/v1/admin/bookings/<id>/ — the canonical version.
 *
 * Read before the confirmation, never inside the write. `expected_version`
 * protects against acting on a booking that changed since the operator
 * looked at it; a version fetched by the write itself would always match
 * and protect nothing. The human pause between this call and the confirm
 * IS the window the guard covers.
 */
export const getBookingVersion = (
  appointmentId: string,
  init: { signal?: AbortSignal } = {},
): Promise<BookingVersion> =>
  request<BookingVersion>(
    `/api/v1/admin/bookings/${encodeURIComponent(appointmentId)}/`,
    { signal: init.signal },
  );

export interface CompleteBookingResult {
  outcome: "committed" | "conflict" | "blocked" | "pending" | "failed";
  detail: string;
  appointment_id?: string;
}

/**
 * POST /api/v1/admin/bookings/<id>/complete/ — close the visit.
 *
 * `expectedVersion` must be the value the operator was shown. Same five
 * outcomes as the other writes, and the same rule: never throws on a
 * business answer, and `pending` is «unknown», not «failed».
 */
export const completeSalonBooking = async (
  appointmentId: string,
  expectedVersion: number,
): Promise<CompleteBookingResult> => {
  const initData = getInitData();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);

  let res: Response;
  try {
    res = await fetch(
      `/api/v1/admin/bookings/${encodeURIComponent(appointmentId)}/complete/`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({ expected_version: expectedVersion }),
      },
    );
  } catch {
    return { outcome: "pending", detail: "нет ответа от сети" };
  }

  try {
    const data = (await res.json()) as Partial<CompleteBookingResult>;
    if (data.outcome) return data as CompleteBookingResult;
    return { outcome: "failed", detail: data.detail ?? "неизвестная ошибка" };
  } catch {
    return { outcome: "pending", detail: "ответ не прочитан" };
  }
};

/**
 * POST /api/v1/admin/bookings/<id>/reschedule/ — move it.
 *
 * `expectedVersion` is the value the operator was shown, exactly as for
 * closure. `newStartAt` must be a start the schedule offered — the screen
 * picks it from the slots endpoint, and Ayla re-checks at commit anyway.
 */
export const rescheduleSalonBooking = async (
  appointmentId: string,
  expectedVersion: number,
  newStartAt: string,
): Promise<CompleteBookingResult> => {
  const initData = getInitData();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);

  let res: Response;
  try {
    res = await fetch(
      `/api/v1/admin/bookings/${encodeURIComponent(appointmentId)}/reschedule/`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({
          expected_version: expectedVersion,
          new_start_at: newStartAt,
        }),
      },
    );
  } catch {
    return { outcome: "pending", detail: "нет ответа от сети" };
  }

  try {
    const data = (await res.json()) as Partial<CompleteBookingResult>;
    if (data.outcome) return data as CompleteBookingResult;
    return { outcome: "failed", detail: data.detail ?? "неизвестная ошибка" };
  } catch {
    return { outcome: "pending", detail: "ответ не прочитан" };
  }
};

// --- salon customers (UX contract §13) -----------------------------------

/**
 * A customer as the salon may see them while booking.
 *
 * Name and masked phone, and nothing else. §13: the search «exposes only
 * fields needed to disambiguate a customer» and «must not surface
 * unrelated customers, hidden memory, medical inference, or full
 * history». The raw phone is deliberately absent — on the Ayla side the
 * lookup takes a phone as input and never returns one.
 */
export interface SalonCustomer {
  id: string;
  /** Never blank: an unnamed customer arrives as «Без имени». */
  name: string;
  /**
   * False when `name` is the placeholder rather than a real name.
   *
   * Upstream may hold no name at all — deliberately, since inventing
   * «Клиент №4» would make the record look more certain than it is.
   */
  named: boolean;
  /**
   * Absent today: the canonical lookup returns no phone at all
   * (DRF-1039 — the number is an input, never an output). Decision B-6
   * says the administrator should see a masked one, which this contract
   * cannot currently satisfy; reported, and optional here so that
   * nothing changes on this side when it can.
   */
  phone_masked?: string;
}

/**
 * The search capability is not reachable at all.
 *
 * Distinct from «found nothing» on purpose, and the distinction is the
 * whole point: §13 says «A failed search is not proof that the customer
 * does not exist». A surface that renders an unreachable search as «нет
 * такого клиента» invites the receptionist to create a duplicate of
 * somebody who is already there.
 */
export class CustomerSearchUnavailable extends Error {
  constructor(message = "customer search is not available") {
    super(message);
    this.name = "CustomerSearchUnavailable";
  }
}

/** Minimum the canonical lookup accepts; a shorter query is refused. */
export const CUSTOMER_SEARCH_MIN_QUERY = 2;

/**
 * GET /api/v1/admin/customers/?q=… — search this salon's customers.
 *
 * Matches a name from the start, or a phone **exactly** — a prefix match
 * on digits would turn this into a way to sweep the customer list a few
 * keystrokes at a time, so the canonical lookup does not offer one.
 *
 * Every failure becomes {@link CustomerSearchUnavailable}, never an empty
 * array. §13: «A failed search is not proof that the customer does not
 * exist» — the two look identical on screen and mean opposite things, and
 * getting it wrong creates a duplicate of somebody already in the book.
 */
export const searchSalonCustomers = async (
  query: string,
  init: { signal?: AbortSignal } = {},
): Promise<SalonCustomer[]> => {
  const q = query.trim();
  if (q.length < CUSTOMER_SEARCH_MIN_QUERY) return [];

  let payload: { results?: unknown };
  try {
    payload = await request<{ results?: unknown }>(
      `/api/v1/admin/customers/?q=${encodeURIComponent(q)}`,
      { signal: init.signal },
    );
  } catch (err) {
    // An aborted keystroke is not a failed search — let the caller's
    // abort handling see it unchanged.
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new CustomerSearchUnavailable(
      err instanceof Error ? err.message : undefined,
    );
  }

  if (!Array.isArray(payload.results)) {
    // A success shape we do not recognise is not «nobody matched».
    throw new CustomerSearchUnavailable("unrecognised search payload");
  }
  return payload.results as SalonCustomer[];
};

// --- /api/v1/admin/booking-slots/ ----------------------------------------

/** One bookable start, as the canonical schedule returned it. */
export interface BookingSlot {
  /** `HH:MM` in the salon's timezone — always present. */
  time: string;
  /**
   * Full ISO timestamp when the schedule sent one, otherwise null.
   *
   * Deliberately not reconstructed from `time` on either side: picking a
   * timezone to fill this in would be the client computing an
   * authoritative slot, which UX contract §17 rules out.
   */
  start_at: string | null;
  duration_min: number | null;
}

export interface BookingSlotsResponse {
  date: string;
  /**
   * IANA zone the times are in — the salon's, not the device's.
   *
   * §18 requires the review to state the timezone, and the server is the
   * only party that knows it: a receptionist on holiday still books into
   * the salon's day.
   */
  timezone: string;
  master_id: string;
  service_id: string;
  duration_min: number | null;
  slots: BookingSlot[];
}

/**
 * GET /api/v1/admin/booking-slots/ — bookable starts for one master,
 * one service, one day.
 *
 * All three arguments are required: a slot list that does not know the
 * service is a list of times that mean nothing (§12).
 *
 * The endpoint answers 503 / 502 when the schedule is unreachable rather
 * than an empty list, so callers must treat a thrown ApiError as «could
 * not ask» and NEVER as «no free time» (§16).
 */
export const getBookingSlots = (
  params: { masterId: string; serviceId: string; date: string },
  init: { signal?: AbortSignal } = {},
): Promise<BookingSlotsResponse> => {
  const q = new URLSearchParams({
    master_id: params.masterId,
    service_id: params.serviceId,
    date: params.date,
  });
  return request<BookingSlotsResponse>(`/api/v1/admin/booking-slots/?${q.toString()}`, {
    method: "GET",
    signal: init.signal,
  });
};

// --- /api/v1/admin/masters/ ----------------------------------------------

export interface MasterListItem {
  id: string;
  name: string;
  specialization: string;
  photo_url: string;
  is_active: boolean;
  invite_status: string;
  last_seen_at: string | null;
  services_count: number;
}

export interface MasterListResponse {
  items: MasterListItem[];
  next_cursor: string | null;
  total_count: number;
}

export interface ListMastersParams {
  is_active?: boolean;
  invite_status?: string[];
  search?: string;
  cursor?: string;
  limit?: number;
}

export const listMasters = (
  params: ListMastersParams = {},
  init: { signal?: AbortSignal } = {},
): Promise<MasterListResponse> => {
  const q = new URLSearchParams();
  if (params.is_active !== undefined) {
    q.set("is_active", params.is_active ? "true" : "false");
  }
  if (params.invite_status && params.invite_status.length > 0) {
    q.set("invite_status", params.invite_status.join(","));
  }
  if (params.search) q.set("search", params.search);
  if (params.cursor) q.set("cursor", params.cursor);
  if (params.limit !== undefined) q.set("limit", String(params.limit));
  const qs = q.toString();
  return request(`/api/v1/admin/masters/${qs ? `?${qs}` : ""}`, {
    method: "GET",
    signal: init.signal,
  });
};

// --- MM5 deactivation preview --------------------------------------------

export interface DeactivationFallbackMaster {
  master_id: string;
  name: string;
  does_this_service: boolean;
  is_free_at_slot: boolean;
  match_score: number;
}

export interface DeactivationFutureBooking {
  booking_id: string;
  visit_at: string | null;
  service_name: string;
  service_id: string;
  client_first_name: string;
  client_last_initial: string;
  duration_min: number;
  fallback_masters: DeactivationFallbackMaster[];
}

export interface DeactivationMaster {
  id: string;
  name: string;
  is_active: boolean;
  archived_at: string | null;
}

export interface DeactivationSummary {
  total_future_bookings: number;
  bookings_with_fallback: number;
  bookings_without_fallback: number;
  /**
   * Live future visits the Ayla mirror knows about for this master —
   * independent of what the cascade can act on (DRF-1139).
   */
  mirror_future_bookings?: number;
  /**
   * False when the mirror reports more live future visits than
   * `total_future_bookings`, i.e. this screen cannot see everything it
   * would archive. The backend refuses `/deactivate/` with 409
   * `inventory_incomplete` while this is false, and the UI must not
   * offer the action.
   *
   * Optional so an older backend ship (no such field) degrades to the
   * previous behaviour rather than blocking every deactivation —
   * absence means «unknown», and only an explicit `false` blocks.
   */
  inventory_complete?: boolean;
}

export interface DeactivationPreview {
  master: DeactivationMaster;
  future_bookings: DeactivationFutureBooking[];
  summary: DeactivationSummary;
}

export const previewDeactivation = (
  masterId: string,
): Promise<DeactivationPreview> =>
  request(`/api/v1/admin/masters/${masterId}/deactivation-preview/`, {
    method: "POST",
    body: JSON.stringify({}),
  });

// --- MM5 deactivation execute --------------------------------------------

export type BookingActionKind = "reassign" | "cancel";

export interface BookingActionEntry {
  booking_id: string;
  action: BookingActionKind;
  to_master_id?: string;
}

export interface DeactivationExecuteBody {
  bookings_plan: BookingActionEntry[];
  reason?: string;
  notify_reassigned_masters?: boolean;
  custom_notification_template?: string | null;
}

export interface DeactivationExecuteSummary {
  reassigned_count: number;
  cancelled_count: number;
  customer_notifications_dispatched: number;
  master_notifications_dispatched: number;
}

export interface DeactivationResult {
  master_id: string;
  is_active: boolean;
  archived_at: string | null;
  summary: DeactivationExecuteSummary;
}

export const executeDeactivation = (
  masterId: string,
  body: DeactivationExecuteBody,
): Promise<DeactivationResult> =>
  request(`/api/v1/admin/masters/${masterId}/deactivate/`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// --- MM5 reactivate -------------------------------------------------------

export interface ReactivationResult {
  master_id: string;
  is_active: boolean;
  archived_at: string | null;
}

export const reactivateMaster = (
  masterId: string,
  notifyMaster: boolean,
): Promise<ReactivationResult> =>
  request(`/api/v1/admin/masters/${masterId}/reactivate/`, {
    method: "POST",
    body: JSON.stringify({ notify_master: notifyMaster }),
  });

// --- MM2 invite create ----------------------------------------------------

/**
 * MM2 — POST /api/v1/admin/masters/invite/ (PR #408).
 *
 * Spec: docs/design/handoffs/2026-05-18-master-management-handoff.md §MM2
 * (lines 458-489). Request envelope mirrors the contract; ``role`` is
 * NOT sent (master-only PR — backend rejects other roles).
 *
 * Email contact_method is intentionally absent — the backend currently
 * only accepts ``max_username`` / ``max_phone``; the UI surfaces email
 * as disabled with a «скоро» tooltip per the same spec.
 */
export type InviteContactMethod = "max_username" | "max_phone";
export type InviteSchedulePreset = "default_mon_fri_10_19" | "none";
export type InviteMode = "invite" | "catalog_only";
export type MaxDmDelivery = "queued" | "delivered" | "failed" | "skipped";

export interface InviteMasterPayload {
  name: string;
  contact_method: InviteContactMethod;
  contact_value: string;
  services: string[];
  schedule_preset: InviteSchedulePreset;
  mode: InviteMode;
}

export interface InviteMasterResponse {
  master_id: string;
  invite_token: string | null;
  invite_expires_at: string | null;
  max_dm_delivery: MaxDmDelivery;
  fallback_link: string;
  /**
   * True when the backend returned 200 + ``X-Idempotent: true`` because
   * a matching pending invite already existed within the 7-day window.
   * The modal renders an «уже есть активное приглашение» banner.
   */
  was_idempotent: boolean;
}

export const inviteMaster = async (
  payload: InviteMasterPayload,
): Promise<InviteMasterResponse> => {
  const { data, response } = await requestWithResponse<
    Omit<InviteMasterResponse, "was_idempotent">
  >("/api/v1/admin/masters/invite/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  const wasIdempotent =
    response.headers.get("X-Idempotent")?.toLowerCase() === "true";
  return { ...data, was_idempotent: wasIdempotent };
};

// --- Catalog services (admin-side fetch) ---------------------------------

/**
 * Admin-side service-list fetch for the MM2 invite modal «Услуги» field.
 *
 * No dedicated ``/api/v1/admin/services`` endpoint exists today (the
 * services-mapping endpoint returns a master×service matrix, not a flat
 * service list). We fall back to ``/api/v1/customer/services`` because
 * it returns the same tenant's active services with the same auth
 * envelope (MaxInitData header + dev-bypass shim), and the «Услуги»
 * field only needs id+name+duration to render checkboxes.
 *
 * Returns ``[]`` on failure so the modal can hide the field with a
 * small notice rather than blocking the whole submit path.
 */
export interface CatalogServiceLite {
  id: string;
  name: string;
  duration_min: number | null;
}

interface CustomerServicesEnvelope {
  services: Array<{
    id: string;
    name: string;
    duration_min: number | null;
  }>;
}

export const getCatalogServicesForAdmin = async (): Promise<
  CatalogServiceLite[]
> => {
  const { data } = await requestWithResponse<CustomerServicesEnvelope>(
    "/api/v1/customer/services",
    { method: "GET" },
  );
  return data.services.map((s) => ({
    id: s.id,
    name: s.name,
    duration_min: s.duration_min,
  }));
};

// --- MM3 master detail / patch / photo / audit -----------------------------

/**
 * Full master detail returned by ``GET /api/v1/admin/masters/<id>/``.
 *
 * Mirrors :func:`apps.admin_api.views._detail_payload`. Keep field
 * names in sync with the backend serializer — silent rename here will
 * just produce ``undefined`` in the UI rather than a typecheck error,
 * so the contract is owned in both repos.
 */
export interface LinkedBotUser {
  id: string;
  display_name: string;
  phone_masked: string;
  last_seen_at: string | null;
}

export interface MasterDetailService {
  id: string;
  name: string;
}

export interface MasterDetail {
  id: string;
  name: string;
  specialization: string;
  bio: string;
  experience: string;
  rating: string | null;
  is_active: boolean;
  invite_status: string;
  mode: string;
  photo_url: string;
  max_handle: string;
  yclients_staff_id: string | null;
  invited_at: string | null;
  archived_at: string | null;
  linked_bot_user: LinkedBotUser | null;
  services: MasterDetailService[];
  working_hours_summary: string;
}

interface MasterDetailEnvelope {
  master: MasterDetail;
}

export const getMasterDetail = (
  masterId: string,
  init: { signal?: AbortSignal } = {},
): Promise<MasterDetail> =>
  request<MasterDetailEnvelope>(`/api/v1/admin/masters/${masterId}/`, {
    method: "GET",
    signal: init.signal,
  }).then((env) => env.master);

/**
 * PATCH body for ``/api/v1/admin/masters/<id>/``. Only fields the
 * backend whitelists in ``PATCH_EDITABLE_FIELDS`` belong here. The
 * MM3 inline-edit flow sends a single field at a time, but the type
 * stays a ``Partial`` so future bulk-save lands cleanly.
 *
 * Constraints (server-enforced — UI mirrors them for fast feedback):
 *   - ``name``           — non-blank, ≤ 200 chars
 *   - ``specialization`` — ≤ 255 chars (blank OK)
 *   - ``bio``            — ≤ 1000 chars server-side; UI caps at 280 per
 *     §MM3 line 531 «280 символов max»
 *   - ``experience``     — ≤ 255 chars
 *   - ``is_active``      — boolean; setting ``False`` requires Owner
 */
export interface MasterPatchPayload {
  name: string;
  specialization: string;
  bio: string;
  experience: string;
  is_active: boolean;
}

export const patchMaster = (
  masterId: string,
  patch: Partial<MasterPatchPayload>,
): Promise<MasterDetail> =>
  request<MasterDetailEnvelope>(`/api/v1/admin/masters/${masterId}/`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  }).then((env) => env.master);

/**
 * Multipart photo upload — JPEG / PNG / WebP, ≤ 10 MB. Mirrors
 * :func:`apps.admin_api.views.master_photo_upload`. The browser sets
 * the multipart Content-Type boundary automatically when ``body`` is
 * a ``FormData`` instance, so we must NOT pre-set Content-Type in the
 * shared ``request`` helper. We hit the lower-level ``fetch`` here
 * because ``request`` defaults to ``application/json`` when ``body``
 * is present.
 */
export interface MasterPhotoUploadResponse {
  photo_url: string;
}

export const uploadMasterPhoto = async (
  masterId: string,
  file: File,
): Promise<MasterPhotoUploadResponse> => {
  const formData = new FormData();
  formData.append("photo", file);
  const initData = getInitData();
  const headers = new Headers();
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
  // No Content-Type — let fetch set the multipart boundary.
  const res = await fetch(`/api/v1/admin/masters/${masterId}/photo/`, {
    method: "POST",
    headers,
    body: formData,
  });
  if (!res.ok) {
    let parsed: ErrorBody = { error: "http_error", detail: res.statusText };
    try {
      parsed = (await res.json()) as ErrorBody;
    } catch {
      /* non-JSON 5xx */
    }
    throw new ApiError(res.status, parsed.error, parsed.detail);
  }
  return (await res.json()) as MasterPhotoUploadResponse;
};

/**
 * Audit feed row — mirrors the backend builder in
 * :func:`apps.admin_api.views.master_audit_feed`.
 *
 * ``action`` is the registered slug from
 * :mod:`apps.events.vocabulary` (e.g. ``master.profile_updated_by_admin``).
 * ``payload`` carries event-specific extras; the MM3 UI reads
 * ``fields_changed`` for the profile-update row.
 */
export interface AuditEvent {
  id: string;
  action: string;
  actor_id: string | null;
  actor_role: string | null;
  occurred_at: string;
  payload: Record<string, unknown>;
}

export interface AuditFeedResponse {
  items: AuditEvent[];
  next_cursor: string | null;
}

export const getMasterAudit = (
  masterId: string,
  cursor?: string,
  init: { signal?: AbortSignal } = {},
): Promise<AuditFeedResponse> => {
  const q = new URLSearchParams();
  if (cursor) q.set("cursor", cursor);
  q.set("limit", "10");
  return request<AuditFeedResponse>(
    `/api/v1/admin/masters/${masterId}/audit/?${q.toString()}`,
    { method: "GET", signal: init.signal },
  );
};

// --- MM4 services ↔ masters matrix --------------------------------------

/**
 * Service row inside the services-mapping payload. Mirrors
 * :func:`apps.admin_api.views_services_mapping.services_mapping_get` ’s
 * service serializer. ``price_from`` is a Decimal string on the wire
 * (``"1500.00"``) — keep as string so we don't lose precision; the UI
 * formats with ``Intl.NumberFormat`` when rendering.
 */
export interface ServicesMappingService {
  id: string;
  name: string;
  duration_min: number;
  price_from: string | null;
  is_active: boolean;
}

/**
 * Master row inside the services-mapping payload. Mirrors
 * :func:`services_mapping_get` ’s master serializer.
 */
export interface ServicesMappingMaster {
  id: string;
  name: string;
  is_active: boolean;
  invite_status: string;
}

export interface ServicesMappingPair {
  master_id: string;
  service_id: string;
}

export interface ServicesMappingOrphans {
  services_without_masters: string[];
  masters_without_services: string[];
}

/**
 * GET /api/v1/admin/services-mapping/ — full matrix snapshot.
 *
 * Spec: docs/design/handoffs/2026-05-18-master-management-handoff.md §MM4
 * backend contract (lines 656-672). Backend impl: PR #411.
 *
 * ``snapshot_token`` is opaque HMAC-Option-A token (see backend
 * docstring); resend with POST /bulk/ to enable conflict detection.
 */
export interface ServicesMappingPayload {
  services: ServicesMappingService[];
  masters: ServicesMappingMaster[];
  mapping: ServicesMappingPair[];
  orphans: ServicesMappingOrphans;
  snapshot_token: string;
}

/**
 * POST /api/v1/admin/services-mapping/bulk/ — diff apply payload.
 *
 * ``changes`` carries enable/disable per cell; backend dedupes no-ops
 * (asking to enable an already-enabled pair is silently dropped from
 * ``applied`` count). ``snapshot_token`` is optional but recommended —
 * omit only for first save when GET hasn't completed yet (we won't hit
 * this path in the UI).
 */
export interface ServicesMappingChange {
  service_id: string;
  master_id: string;
  enabled: boolean;
}

export interface ServicesMappingBulkBody {
  changes: ServicesMappingChange[];
  snapshot_token?: string;
}

export interface ServicesMappingConflictEntry {
  service_id: string;
  master_id: string;
  current_value: boolean;
  your_value: boolean;
  changed_by: string | null;
  changed_at: string | null;
}

/**
 * POST /api/v1/admin/services-mapping/bulk/ — 200 OK response.
 *
 * ``applied`` is the count of (master, service) pairs whose stored
 * state actually changed. ``new_snapshot_token`` is the post-write
 * token; pass it on the next /bulk/ call to chain edits without a
 * re-fetch.
 */
export interface ServicesMappingBulkResult {
  applied: number;
  conflicts: [];
  new_snapshot_token: string;
}

/**
 * POST /api/v1/admin/services-mapping/bulk/ — 409 Conflict response.
 *
 * Wrapped in ApiError by the shared ``request`` helper (status >= 400
 * throws), but the body carries the conflict array. Callers MUST parse
 * the ApiError's response separately — the helper below uses
 * ``fetch`` directly to surface both the parsed body and the 409.
 */
export interface ServicesMappingConflictResponse {
  applied: 0;
  conflicts: ServicesMappingConflictEntry[];
}

export const getServicesMapping = (
  init: { signal?: AbortSignal } = {},
): Promise<ServicesMappingPayload> =>
  request<ServicesMappingPayload>("/api/v1/admin/services-mapping/", {
    method: "GET",
    signal: init.signal,
  });

/**
 * POST /bulk/. Returns the parsed success body OR a tagged conflict
 * envelope (status 409). Any other non-2xx becomes an ApiError throw.
 *
 * Why bypass ``request``: the shared helper throws on any non-2xx, so
 * 409's structured body (with the conflict array) would be flattened
 * into ApiError's ``detail`` string. Conflict resolution needs the
 * full conflict[] array — preserve it via direct ``fetch``.
 */
export interface ServicesMappingConflictEnvelope
  extends ServicesMappingConflictResponse {
  __conflict: true;
}

export const patchServicesMapping = async (
  body: ServicesMappingBulkBody,
): Promise<ServicesMappingBulkResult | ServicesMappingConflictEnvelope> => {
  const initData = getInitData();
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);

  const res = await fetch("/api/v1/admin/services-mapping/bulk/", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  if (res.status === 409) {
    const data = (await res.json()) as ServicesMappingConflictResponse;
    return { ...data, __conflict: true };
  }
  if (!res.ok) {
    let parsed: ErrorBody = { error: "http_error", detail: res.statusText };
    try {
      parsed = (await res.json()) as ErrorBody;
    } catch {
      /* non-JSON 5xx */
    }
    throw new ApiError(res.status, parsed.error, parsed.detail);
  }
  return (await res.json()) as ServicesMappingBulkResult;
};

// --- M3-admin: availability requests approve/reject (PR #521 backend) -----

/**
 * Availability request row — mirrors the JSON shape emitted by
 * :func:`apps.admin_api.services.availability.list_pending_for_admin`.
 *
 * Status uses the model's `ScheduleChangeRequest.Status` lowercase
 * slugs. Only the three values the admin surface ever shows are
 * declared as literals — `cancelled` / `auto_escalated` are out-of-scope
 * for this UI but fall through the `string` widening (defence in depth).
 */
export interface AvailabilityRequestItem {
  request_id: string;
  master_id: string | null;
  master_name: string;
  requested_start: string | null;
  requested_end: string | null;
  reason_class: string;
  reason_text: string;
  status: "pending" | "approved" | "rejected" | string;
  created_at: string;
  decided_at: string | null;
  decided_by_name: string;
  resolution_note: string;
}

export interface AvailabilityListResponse {
  items: AvailabilityRequestItem[];
  next_cursor: string | null;
}

/**
 * 409 envelope from approve/reject. Backend (PR #521) returns
 * ``{"error": "already_decided" | "overlap_conflict", "detail": "..."}``
 * — there's no structured `dates: [...]` field, conflicting dates are
 * embedded as a stringified Python list inside the detail
 * (e.g. ``"existing exceptions conflict on dates: ['2026-06-11']"``).
 * We parse them client-side; see ``parseOverlapDates`` in the screen.
 */
export interface AvailabilityConflict {
  __conflict: true;
  conflict: "already_decided" | "overlap_conflict";
  detail: string;
  /** Best-effort parse of dates embedded in the detail string. */
  dates?: string[];
}

export interface AvailabilityListParams {
  status?: "pending" | "decided" | "all";
  master_id?: string;
  cursor?: string;
  limit?: number;
}

export const getAvailabilityRequests = (
  params: AvailabilityListParams = {},
  init: { signal?: AbortSignal } = {},
): Promise<AvailabilityListResponse> => {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.master_id) q.set("master_id", params.master_id);
  if (params.cursor) q.set("cursor", params.cursor);
  if (params.limit !== undefined) q.set("limit", String(params.limit));
  const qs = q.toString();
  return request<AvailabilityListResponse>(
    `/api/v1/admin/availability-requests/${qs ? `?${qs}` : ""}`,
    { method: "GET", signal: init.signal },
  );
};

/**
 * Approve or reject the request via direct ``fetch`` — bypasses the
 * shared ``request`` helper because 409 carries a structured body
 * (`{error, detail}`) that ``request`` would flatten into ApiError's
 * stringified ``detail``. We need both the slug and the detail to
 * branch on already_decided vs overlap_conflict + surface conflicting
 * dates. Mirror of ``patchServicesMapping`` 409-handling pattern
 * (PR #518) — same rationale, same shape.
 */
interface DecisionEnvelope {
  request: AvailabilityRequestItem;
}

const DATE_REGEX = /\d{4}-\d{2}-\d{2}/g;

function parseDatesFromDetail(detail: string): string[] {
  const matches = detail.match(DATE_REGEX);
  return matches ? Array.from(new Set(matches)) : [];
}

async function decisionFetch(
  url: string,
  body: Record<string, unknown>,
): Promise<AvailabilityRequestItem | AvailabilityConflict> {
  const initData = getInitData();
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);

  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  if (res.status === 409) {
    let parsed: ErrorBody = { error: "http_error", detail: res.statusText };
    try {
      parsed = (await res.json()) as ErrorBody;
    } catch {
      /* non-JSON */
    }
    const slug = parsed.error;
    if (slug !== "already_decided" && slug !== "overlap_conflict") {
      // Unknown 409 — surface as ApiError so the caller's generic
      // error path renders it.
      throw new ApiError(res.status, slug, parsed.detail);
    }
    const detail = parsed.detail || "";
    return {
      __conflict: true,
      conflict: slug,
      detail,
      dates: slug === "overlap_conflict" ? parseDatesFromDetail(detail) : undefined,
    };
  }

  if (!res.ok) {
    let parsed: ErrorBody = { error: "http_error", detail: res.statusText };
    try {
      parsed = (await res.json()) as ErrorBody;
    } catch {
      /* non-JSON 5xx */
    }
    throw new ApiError(res.status, parsed.error, parsed.detail);
  }
  const envelope = (await res.json()) as DecisionEnvelope;
  return envelope.request;
}

export const approveAvailabilityRequest = (
  requestId: string,
): Promise<AvailabilityRequestItem | AvailabilityConflict> =>
  decisionFetch(
    `/api/v1/admin/availability-requests/${requestId}/approve/`,
    {},
  );

export const rejectAvailabilityRequest = (
  requestId: string,
  rejection_reason: string,
): Promise<AvailabilityRequestItem | AvailabilityConflict> =>
  decisionFetch(
    `/api/v1/admin/availability-requests/${requestId}/reject/`,
    { rejection_reason },
  );

// --- Staff access codes (DRF-1061 block 2.4) -----------------------------
//
// `POST /api/v1/admin/staff/invite/` — apps/admin_api/views_staff_invite.py.
//
// This is NOT `masters/invite/`, and the distinction is the whole point of
// the endpoint existing separately:
//
//   masters/invite/   CREATES a catalog master — a person who is not in the
//                     salon yet, with services, a card, a profile.
//   staff/invite/     GRANTS ACCESS to a person who already exists — a
//                     TenantStaff row, or a link from an existing catalog
//                     master to their MAX account.
//
// Before this client existed the only way to make an employee was a
// management command on the pilot host, i.e. somebody with SSH.

/** Roles an access code can grant. Mirrors `StaffInvite.Role`. */
export type StaffInviteRole = "owner" | "admin" | "receptionist" | "master";

export interface StaffInvitePayload {
  role: StaffInviteRole;
  /** Required for `role: "master"` — the EXISTING catalog row to link. */
  master_id?: string;
  /** Free-form label for the issuer's own records. Server caps at 200. */
  note?: string;
}

export interface StaffInviteResponse {
  invite_id: string;
  role: StaffInviteRole;
  /** Plaintext, formatted `AYLA-XXXX`. Returned exactly once — see below. */
  code: string;
  expires_at: string;
  /**
   * The server says this out loud rather than leaving it to be inferred:
   * only the code's hash is stored, so nothing — not this client, not the
   * admin API, not the database — can produce it again. A UI that treats
   * the code as re-readable is wrong, and the field exists so it cannot
   * claim it was not told.
   */
  code_is_shown_once: boolean;
}

export const issueStaffInvite = (
  payload: StaffInvitePayload,
): Promise<StaffInviteResponse> =>
  request("/api/v1/admin/staff/invite/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
