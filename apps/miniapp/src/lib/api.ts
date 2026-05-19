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
