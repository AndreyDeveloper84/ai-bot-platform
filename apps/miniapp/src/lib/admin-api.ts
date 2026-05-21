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
}

export const getMe = (): Promise<MeResponse> =>
  request("/api/v1/me", { method: "GET" });

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
