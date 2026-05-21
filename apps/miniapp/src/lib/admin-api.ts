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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
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
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
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
