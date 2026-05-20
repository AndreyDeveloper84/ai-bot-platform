/**
 * Master Mini App API client — PR 1 / M0.
 *
 * Mirrors apps/master_api/views.py:
 *   POST /api/v1/master/onboarding/claim
 *   POST /api/v1/master/onboarding/accept
 *   POST /api/v1/master/onboarding/reject
 *   PATCH /api/v1/master/onboarding/profile
 *   GET  /api/v1/master/me
 *
 * Auth: same MAX initData header as the customer surface
 * (`Authorization: MaxInitData <raw>`), validated server-side by
 * apps/miniapp_api/auth.verify_init_data.
 */

import { getInitData } from "./max-sdk";
import { ApiError } from "./api";

const MASTER_API_BASE = "/api/v1/master";

interface ErrorBody {
  error: string;
  detail: string;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const initData = getInitData();
  const headers = new Headers(init.headers);
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  // Don't auto-set Content-Type for FormData (the browser writes the
  // boundary string). JSON callers explicitly set it.
  const body = init.body;
  const isFormData =
    typeof FormData !== "undefined" && body instanceof FormData;
  if (body && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${MASTER_API_BASE}${path}`, { ...init, headers });
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

// --- types -----------------------------------------------------------------

export interface MasterProfile {
  id: string;
  name: string;
  specialization: string;
  bio: string;
  photo_url: string;
  services?: { id: string; name: string; duration_min: number | null }[];
  working_hours_summary?: string;
}

export interface SalonInfo {
  tenant_id: string;
  name: string;
}

export interface MaxUserSnapshot {
  first_name: string;
  phone_masked: string;
  max_handle: string;
}

export interface ClaimResponse {
  master: MasterProfile & {
    services: { id: string; name: string; duration_min: number | null }[];
    working_hours_summary: string;
  };
  salon: SalonInfo;
  max_user: MaxUserSnapshot;
}

export interface AcceptResponse {
  master_id: string;
  session_token: string;
  expires_at: string;
}

export interface ProfilePatchResponse {
  master: Pick<MasterProfile, "id" | "name" | "bio" | "photo_url">;
}

// --- Dashboard (M1) types -------------------------------------------------
// Mirrors apps/master_api/services/dashboard.py:DashboardSnapshot.to_dict().
// Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M1.

export interface DashboardMaster {
  id: string;
  name: string;
  specialization: string;
  photo_url: string;
}

export interface DashboardSalon {
  id: string;
  name: string;
}

export interface DashboardActiveVisit {
  booking_id: string;
  client_first_name: string;
  client_last_initial: string;
  service_name: string;
  started_at: string; // ISO
  duration_min: number;
  minutes_remaining: number;
  is_in_progress: boolean;
  note: string;
}

export interface DashboardNextVisit {
  booking_id: string;
  client_first_name: string;
  client_last_initial: string;
  visit_at: string; // ISO
  service_name: string;
  duration_min: number;
  is_returning_customer: boolean;
  customer_intent_hint: string;
}

export type SlaTier = "red" | "yellow" | "white";

export interface DashboardInboxItem {
  conversation_id: string;
  client_first_name: string;
  client_last_initial: string;
  last_message_excerpt: string;
  last_message_at: string; // ISO
  sla_tier: SlaTier;
  ai_drafted_reply_available: boolean;
}

export interface DashboardTodaySummary {
  total_clients_today: number;
  completed_count: number;
  next_free_window: { start: string; end: string } | null;
}

export interface DashboardTabBadges {
  conversations_unread: number;
  schedule_has_pending_change: boolean;
  profile_has_owner_pending_change: boolean;
}

export interface DashboardStatesFlags {
  is_day_done: boolean;
  is_offline_safe_response: boolean;
}

export interface DashboardResponse {
  master: DashboardMaster;
  salon: DashboardSalon;
  now_iso: string;
  active_visit: DashboardActiveVisit | null;
  next_visit: DashboardNextVisit | null;
  inbox_preview: DashboardInboxItem[];
  today_summary: DashboardTodaySummary;
  tab_badges: DashboardTabBadges;
  states: DashboardStatesFlags;
}

// --- endpoints -------------------------------------------------------------

export const claimInvite = (token: string): Promise<ClaimResponse> =>
  request("/onboarding/claim", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

export const acceptInvite = (token: string): Promise<AcceptResponse> =>
  request("/onboarding/accept", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

export const rejectInvite = (token: string): Promise<void> =>
  request("/onboarding/reject", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

export const patchOnboardingProfile = (input: {
  bio?: string;
  photo?: File | null;
}): Promise<ProfilePatchResponse> => {
  // Use multipart when a photo is present (the only way to upload
  // binary). JSON otherwise — backend supports both.
  if (input.photo) {
    const fd = new FormData();
    if (input.bio !== undefined) fd.set("bio", input.bio);
    fd.set("photo", input.photo);
    return request("/onboarding/profile", { method: "PATCH", body: fd });
  }
  return request("/onboarding/profile", {
    method: "PATCH",
    body: JSON.stringify({ bio: input.bio ?? "" }),
  });
};

export const getDashboard = (): Promise<DashboardResponse> =>
  request("/dashboard", { method: "GET" });

export const MASTER_SESSION_STORAGE_KEY = "master_token";
