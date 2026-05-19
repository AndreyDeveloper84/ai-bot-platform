import { getInitData } from "./max-sdk";

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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const initData = getInitData();
  const headers = new Headers(init.headers);
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
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
export interface AuthVerifyResponse {
  user: { id: string; channel_user_id: string; display_name: string; client_name: string };
  tenant: { slug: string; name: string; timezone: string };
}
export const authVerify = (): Promise<AuthVerifyResponse> =>
  request("/auth/verify", { method: "POST" });

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
}): Promise<{ booking: CreatedBooking }> =>
  request("/bookings", { method: "POST", body: JSON.stringify(body) });

// --- visits / reschedule ---
export interface Visit {
  id: string;
  service_name: string;
  master_name: string;
  visit_at: string | null;
  duration_min: number | null;
  status: string;
  service_id: string | null;
  master_id: string | null;
  rating: number | null;
  can_rate: boolean;
}

export const fetchVisits = (status: "upcoming" | "past" | "all" = "upcoming"): Promise<{
  visits: Visit[];
}> => request(`/visits?status=${status}`, { method: "GET" });

export const rescheduleBooking = (
  oldId: string,
  body: { visit_at: string },
): Promise<{ booking: CreatedBooking }> =>
  request(`/bookings/${oldId}/reschedule`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// --- profile (Phase 3 / F4) ---
export interface Preferences {
  notify_reminders: boolean;
  notify_retention: boolean;
  notify_promo: boolean;
  notify_birthday: boolean;
  birthday_date: string | null; // ISO 8601 yyyy-mm-dd
  allergies: string;
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

export const fetchProfile = (): Promise<Profile> =>
  request("/me", { method: "GET" });

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
