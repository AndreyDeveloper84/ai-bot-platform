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
import { applyDevBypassHeaders } from "./dev-bypass";
import { applySalonChoiceHeader } from "./salon-choice";

const MASTER_API_BASE = "/api/v1/master";

interface ErrorBody {
  error: string;
  detail: string;
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const initData = getInitData();
  const headers = new Headers(init.headers);
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
  applySalonChoiceHeader(headers);
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
  /** DRF-2152 — начало–конец и «до визита N мин» с сервера; экран не тикает. */
  end_at: string; // ISO
  minutes_until: number;
}

/** DRF-2152 — запись дня после ближайшей. Набор полей закрыт макетом DRF-1182:
 * имя, услуга, время — без телефона/цены/оплаты/источника. */
export interface DashboardUpcomingVisit {
  booking_id: string;
  client_first_name: string;
  client_last_initial: string;
  service_name: string;
  visit_at: string; // ISO
  end_at: string; // ISO
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

/** DRF-1846 — календарная неделя в поясе салона. Числа только от сервера;
 * `rating` — `null`, пока за оценкой нет ни одного отзыва. Выручки нет:
 * у зеркала броней нет цены. */
export interface DashboardWeekSummary {
  week_start: string; // YYYY-MM-DD, понедельник
  week_end: string; // YYYY-MM-DD, воскресенье
  bookings: number;
  completed: number;
  rating: { value: number; review_count: number } | null;
}

export interface DashboardTabBadges {
  conversations_unread: number;
  schedule_has_pending_change: boolean;
  profile_has_owner_pending_change: boolean;
}

export interface DashboardStatesFlags {
  is_day_done: boolean;
  is_offline_safe_response: boolean;
  /** DRF-2152: true — выходной (рамка дня прочитана, блока нет); false —
   * рабочий день; null — рамка не прочитана (каталог не ответил): «не знаю»
   * ≠ «выходной» (DRF-1111), экран говорит «не удалось проверить». */
  day_off: boolean | null;
  /** DRF-2200: задан ли недельный график ВООБЩЕ. `day_off` про сегодня, а
   * это — про то, есть ли у мастера часы: «часы не заданы» и «сегодня
   * выходной» — разные слова и разные двери (макет DRF-1186). null — рамка
   * не прочитана: «не знаю», как и у `day_off`. Поле необязательное: Mini
   * App обновляется отдельно от бэкенда, и от сервера без М-7 оно просто не
   * придёт — экран читает это как «не знаю», а не как «часов нет». */
  hours_set?: boolean | null;
}

export interface DashboardResponse {
  master: DashboardMaster;
  salon: DashboardSalon;
  now_iso: string;
  active_visit: DashboardActiveVisit | null;
  next_visit: DashboardNextVisit | null;
  upcoming_today: DashboardUpcomingVisit[];
  inbox_preview: DashboardInboxItem[];
  today_summary: DashboardTodaySummary;
  tab_badges: DashboardTabBadges;
  states: DashboardStatesFlags;
  week_summary: DashboardWeekSummary;
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

// --- М-4 booking detail (DRF-2156) — контракт М-2 GET master/bookings/<uuid>
// (DRF-2154, зафиксирован ayla-22 20.09). Экран «Детали записи» по макету
// DRF-1185: постоянная часть (клиент, дата, время, услуга) + ОДИН контекстный
// блок по `temporal_state`. Состояние считает СЕРВЕР по своим часам
// (`checked_at`); `completed` — только по подтверждённому `status`, экран
// ничего не переводит по часам устройства. На экран не выходят: телефон,
// оплата, история, заметки (их в контракте и нет — DRF-1039).

export type BookingTemporalState =
  "upcoming" | "now" | "after" | "completed" | "unknown";

/** Сырой статус зеркала Ayla. Экран смотрит только на cancelled/no_show. */
export type MasterBookingStatus =
  | "confirmed"
  | "awaiting_payment"
  | "pending_payment"
  | "completed"
  | "cancelled"
  | "no_show"
  | (string & {});

export interface MasterBookingDetail {
  /** Ayla appointment_id — тот же uuid, что booking_id в dashboard/schedule. */
  id: string;
  client: {
    /** «Анна П.»; без bot_user — «Гость». */
    name_initial: string;
    /** Последний completed визит у этого мастера, YYYY-MM-DD; на экран М-4 не выходит. */
    last_visit_date: string | null;
  };
  service: { id: string; name: string };
  start_at: string; // ISO со смещением тенанта
  end_at: string; // ISO со смещением тенанта
  duration_min: number;
  status: MasterBookingStatus;
  temporal_state: BookingTemporalState;
  /** Только для upcoming, по часам сервера; иначе null. */
  minutes_until: number | null;
  /** Часы сервера в tz тенанта — точка отсчёта для «Сегодня/Завтра». */
  checked_at: string;
}

/** 404 `not_found` одним телом — и чужая, и несуществующая запись. */
export const getMasterBooking = (
  id: string,
  opts: { signal?: AbortSignal } = {},
): Promise<MasterBookingDetail> =>
  request(`/bookings/${encodeURIComponent(id)}`, {
    method: "GET",
    signal: opts.signal,
  });

// --- M4 master profile (read-by-self + edit own bio/photo) --------------
// Mirrors apps/master_api/views.py::me() + onboarding_profile() (PATCH).
// Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M4
// (lines 480-553). The PATCH endpoint is reused via the /profile URL
// alias added in apps/master_api/urls.py (Option B per PR body) — same
// view function as /onboarding/profile, idempotent + last-write-wins.

export interface MasterMeServiceItem {
  id: string;
  name: string;
  duration_min: number | null;
}

export interface MasterMeMaster {
  id: string;
  name: string;
  specialization: string;
  bio: string;
  photo_url: string;
  services: MasterMeServiceItem[];
}

export interface MasterMeSalon {
  tenant_id: string;
  name: string;
}

export interface MasterMePermissions {
  can_edit_schedule: boolean;
  can_edit_services: boolean;
  can_message_customers: boolean;
}

export interface MasterMeResponse {
  master: MasterMeMaster;
  salon: MasterMeSalon;
  permissions: MasterMePermissions;
}

export const getMasterMe = (): Promise<MasterMeResponse> =>
  request("/me", { method: "GET" });

// --- M2/M15 onboarding readiness (DRF-1794 / DRF-1807) --------------------
// Mirrors apps/master_api/services/onboarding_readiness.py. Проекция по
// фактам: ничего не хранится, экран ничего не вычисляет — только рисует.

export type ReadinessItemKey = "services" | "location" | "hours" | "profile";
/**
 * `done` / `missing` — факт; `unknown` — канон не ответил (`reason` — имя
 * исключения): экран НЕ пишет «настройте», а показывает «не удалось
 * прочитать»; `unavailable` — шага у мастера сейчас нет: либо возможности
 * ещё нет (`capability_not_built`), либо он ведётся не в приложении
 * (`managed_outside_app` — салонное рабочее пространство, DRF-2254). До
 * DRF-2326 такой пункт не рисовался вовсе; теперь рисуется названным
 * недоступным, с причиной и без тапа.
 */
export type ReadinessItemState = "done" | "missing" | "unknown" | "unavailable";

export interface ReadinessItem {
  key: ReadinessItemKey | string;
  state: ReadinessItemState | string;
  detail: Record<string, unknown>;
  reason: string | null;
  /** DRF-2254: `null` — вести некуда (пункт ведётся вне приложения, `managed_outside_app`). */
  deep_link: string | null;
}

/** Связь личности — условие ПУБЛИКАЦИИ (ruling 6), не настройки. */
export type IdentityState = "linked" | "pending" | "rejected" | "unlinked";

export interface OnboardingReadiness {
  /**
   * DRF-2350: «закрыто всё, что мастер может закрыть САМ» — НЕ «настроено
   * всё». Недоступные шаги отправку больше не держат и переехали в
   * `managed_elsewhere`. Имя поля на проводе осталось прежним; смысл — нет.
   */
  ready: boolean;
  blocking: string[];
  /**
   * Требуемые шаги, которых у мастера сейчас нет, — `ключ:причина`.
   *
   * Необязательное намеренно: поле новое, и в раскатке Mini App может
   * какое-то время говорить с сервером, который его ещё не отдаёт. Экран
   * читает его через `?? []` — отсутствие значит «недоступных шагов нет»,
   * то есть прежнее поведение, а не пустой экран.
   */
  managed_elsewhere?: string[];
  items: ReadinessItem[];
  identity: { state: IdentityState | string; link_status: string | null };
  setup_state: "READY" | "SETUP_PENDING" | string;
  sale_block: string | null;
}

export const getOnboardingReadiness = (): Promise<OnboardingReadiness> =>
  request("/onboarding/readiness", { method: "GET" });

// --- M24/M25 working hours (DRF-1816 / DRF-1817) ---------------------------
// Mirrors apps/master_api/views.py::working_hours — a proxy to the catalog's
// working-hours route. The response is the catalog's readback, never an echo.

export interface WorkingHoursDay {
  day_of_week: number; // 0 = Monday … 6 = Sunday
  day_name?: string;
  is_working_day: boolean;
  start_time: string | null; // "HH:MM"
  end_time: string | null;
  break_start: string | null;
  break_end: string | null;
}

export interface WorkingHoursResponse {
  specialist_id: string | null;
  timezone: string | null;
  schedule: WorkingHoursDay[];
  /** PUT only — §83: соло подтверждает своё расписание сразу после записи. */
  schedule_confirmed?: boolean;
}

export const getWorkingHours = (): Promise<WorkingHoursResponse> =>
  request("/working-hours", { method: "GET" });

export const putWorkingHours = (
  schedule: WorkingHoursDay[],
): Promise<WorkingHoursResponse> =>
  request("/working-hours", {
    method: "PUT",
    body: JSON.stringify({ schedule }),
  });

// --- M19 место работы (DRF-1811) -----------------------------------------------
// Mirrors apps/master_api/views.py::service_locations / service_location_detail /
// address_suggest — proxies to the catalog (M11 #502, M12 #476). The answer is
// the catalog's readback: `shown_to_clients_after_publication` is the catalog's
// word (CONFIRMED only), coordinates are null until geocoded.

export type PlaceKind = "private_studio" | "salon_or_studio";
export type PlaceStatus = "confirmed" | "review_required" | "inactive" | string;
export type AreaCoverage = "whole_city" | "later";

export interface ServicePlace {
  id: string;
  kind: PlaceKind | "";
  label: string;
  address: string;
  city: string;
  note_for_client: string;
  status: PlaceStatus;
  geocode_status: string;
  latitude: string | null;
  longitude: string | null;
  shown_to_clients_after_publication: boolean;
}

export interface ServiceArea {
  id: string;
  kind: "mobile";
  city: string;
  coverage: AreaCoverage;
  configured: boolean;
}

export interface ServiceLocationsState {
  specialist_id: string | null;
  city: string;
  places: ServicePlace[];
  areas: ServiceArea[];
}

export type ServiceLocationCreate =
  | {
      kind: PlaceKind;
      address: string;
      label?: string;
      note_for_client?: string;
    }
  | { kind: "mobile"; coverage: AreaCoverage };

export const getServiceLocations = (): Promise<ServiceLocationsState> =>
  request("/service-locations", { method: "GET" });

export const createServiceLocation = (
  body: ServiceLocationCreate,
): Promise<ServiceLocationsState> =>
  request("/service-locations", { method: "POST", body: JSON.stringify(body) });

export const patchServiceLocation = (
  itemId: string,
  body: Partial<{
    kind: PlaceKind;
    address: string;
    label: string;
    note_for_client: string;
    coverage: AreaCoverage;
  }>,
): Promise<ServiceLocationsState> =>
  request(`/service-locations/${encodeURIComponent(itemId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export interface AddressSuggestion {
  value: string;
  unrestricted_value: string;
}

export interface AddressSuggestResponse {
  available: boolean;
  reason?: string | null;
  city?: string | null;
  suggestions: AddressSuggestion[];
}

/**
 * Подсказки адреса — POST с телом: адрес мастера не должен оседать в URL.
 * 503 (геокодер не настроен — стенд пилота) и 409 (нет города) приходят с
 * `available: false`; экран читает их как «подсказок нет», не как ошибку.
 */
export const suggestAddress = (q: string): Promise<AddressSuggestResponse> =>
  request("/geocoding/suggest", {
    method: "POST",
    body: JSON.stringify({ q }),
  });

// --- M18a «Свои услуги» — заявки о разрыве канона (DRF-1896 / DRF-1802) ---------
// Mirrors apps/master_api/views.py::canon_gap_requests / canon_gap_similar /
// canon_gap_request_detail — a proxy to the catalog (DRF-1801). The answer is
// the catalog's, never an echo; the owner decides, the screen only shows.

export type CanonGapStatus =
  "pending" | "approved" | "needs_clarification" | "rejected";

export interface CanonGapRequest {
  id: string;
  specialist_id: string;
  name: string;
  description: string;
  duration_minutes: number;
  price: string;
  status: CanonGapStatus | string;
  /** Одно из четырёх слов мастеру — от сервера, не вычисляется на экране. */
  status_label: string;
  resolved_template_id: string | null;
  clarification_question: string | null;
  rejection_reason: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface CanonGapSimilar {
  template_id: string;
  name: string;
  matched_by: "synonym" | "canonical_name" | string;
}

export interface CanonGapRequestCreate {
  name: string;
  description: string;
  duration_minutes: number;
  price: string;
}

export const listCanonGapRequests = (): Promise<{
  requests: CanonGapRequest[];
}> => request("/canon-gap-requests", { method: "GET" });

export const createCanonGapRequest = (
  body: CanonGapRequestCreate,
): Promise<{ request: CanonGapRequest; similar: CanonGapSimilar[] }> =>
  request("/canon-gap-requests", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getSimilarCanonTemplates = (
  name: string,
): Promise<{ similar: CanonGapSimilar[] }> =>
  request(`/canon-gap-requests/similar?name=${encodeURIComponent(name)}`, {
    method: "GET",
  });

// --- DRF-1895 (M10b) выбор канонических услуг и цена мастера ------------------
// Mirrors apps/master_api/views.py::service_selection / service_offer /
// selected_service — a proxy to the catalog (M8a / M8b). The counters
// `selected` / `configured` are the server's; the screen never computes them.

export interface SelectedServiceOffer {
  id: string;
  price: string;
  duration_minutes: number;
  is_active: boolean;
}

export interface SelectedService {
  salon_service_id: string;
  template_id: string;
  name: string;
  category_id: string | null;
  is_active: boolean;
  mapping_status: string;
  offer: SelectedServiceOffer | null;
  configured: boolean;
  /** Каталог M8a/M8b: категория услуги и корень её дерева — «направление» (DRF-1912). */
  category_name: string | null;
  direction_id: string | null;
  direction_name: string | null;
  direction_sort_order: number | null;
}

export interface ServiceSelectionState {
  specialist_id: string;
  tenant_id: string;
  selected: number;
  configured: number;
  services: SelectedService[];
}

export const getServiceSelection = (): Promise<ServiceSelectionState> =>
  request("/services/selection", { method: "GET" });

export const selectServices = (
  templateIds: string[],
): Promise<ServiceSelectionState & { created: number }> =>
  request("/services/selection", {
    method: "POST",
    body: JSON.stringify({ template_ids: templateIds }),
  });

export const putServiceOffer = (
  salonServiceId: string,
  body: { price: string; duration_minutes: number },
): Promise<ServiceSelectionState & { offer_id: string }> =>
  request(`/services/${salonServiceId}/offer`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const removeService = (
  salonServiceId: string,
): Promise<ServiceSelectionState & { removal: "deleted" | "deactivated" }> =>
  request(`/services/${salonServiceId}`, { method: "DELETE" });

// --- DRF-1799 (M7) канон для экрана 03 ------------------------------------------
// Mirrors apps/master_api/views.py::service_directions / service_templates —
// proxies to the catalog's canon. Directions are exactly the catalog's list (no
// count or codes kept here); template rows carry no price or duration.

export interface ServiceDirection {
  id: string;
  name: string;
  slug: string;
  icon: string;
  sort_order: number;
}

export interface ServiceTemplate {
  id: string;
  name: string;
  name_short: string | null;
  is_popular: boolean;
  category_id: string | null;
  category_name: string | null;
}

export const getServiceDirections = (): Promise<{
  directions: ServiceDirection[];
}> => request("/services/directions", { method: "GET" });

export const getServiceTemplates = (
  directionId: string,
): Promise<{ direction_id: string; templates: ServiceTemplate[] }> =>
  request(
    `/services/templates?direction_id=${encodeURIComponent(directionId)}`,
    { method: "GET" },
  );

// --- M5/M26 publication (DRF-1797 / DRF-1818) ------------------------------
// Mirrors apps/master_api/views.py::publication_status / publication_publish —
// thin proxies of the catalog M4 routes. The screen reads the catalog's answer
// and never recomputes readiness on its own.

export type PublicationProfileStatus = "draft" | "pending" | "active";

export interface PublicationMissingItem {
  code: string;
  /** Те же ключи, что у пунктов readiness бота, плюс `identity`. */
  section: string;
  detail: Record<string, unknown>;
}

export interface PublicationReadiness {
  status: "READY" | "NOT_READY" | string;
  missing: PublicationMissingItem[];
}

export interface PublicationRequestRecord {
  id: string;
  command_id: string;
  outcome: string;
  from_status: string;
  to_status: string;
  created_at: string;
}

export interface PublicationStatus {
  specialist_id: string;
  profile_status: PublicationProfileStatus | string;
  readiness: PublicationReadiness;
  last_request: PublicationRequestRecord | null;
}

export interface PublishResponse {
  specialist_id: string;
  profile_status: PublicationProfileStatus | string;
  replayed: boolean;
  request: PublicationRequestRecord;
}

export const getPublicationStatus = (): Promise<PublicationStatus> =>
  request("/publication/status", { method: "GET" });

/** `commandId` — ключ одной попытки: повтор с тем же ключом каталог не выполнит второй раз. */
export const publishProfile = (
  commandId: string,
  signal?: AbortSignal,
): Promise<PublishResponse> =>
  request("/publication", {
    method: "POST",
    body: JSON.stringify({ command_id: commandId }),
    signal,
  });

/**
 * Пункты, которые мастер может закрыть САМ: всё, кроме `unavailable`.
 *
 * До DRF-2326 имя было `drawnReadinessItems` — «что экран рисует». Экран 01
 * теперь рисует и недоступные пункты (спрятанный шаг мастер читает как «у
 * меня всё», хотя профиль всё равно не отправить), поэтому имя врало бы.
 * Отбор остался прежним, и смысл у него всегда был этот: бар готовности,
 * «следующий шаг» и карточка «продолжить настройку» считают достижимое —
 * недоступный пункт в знаменателе обещал бы работу, которой мастер сделать
 * не может.
 */
export const actionableReadinessItems = (items: ReadinessItem[]): ReadinessItem[] =>
  items.filter((item) => item.state !== "unavailable");

/**
 * Бар готовности — доля закрытых пунктов среди достижимых. Число не
 * показывается словами: ни процентов, ни «N из M» (макет: «no fake percent
 * complete»; доктрина 12.09 — счётчик как обещание времени).
 */
export const readinessFill = (
  items: ReadinessItem[],
): { done: number; total: number } => {
  const drawn = actionableReadinessItems(items);
  return {
    done: drawn.filter((item) => item.state === "done").length,
    total: drawn.length,
  };
};

/**
 * PATCH master profile — bio only (JSON path).
 *
 * Routes to ``/api/v1/master/profile`` (the M4 alias to the existing
 * onboarding profile view). Same view function — idempotent + last
 * write wins. The audit event is still ``MASTER_PROFILE_INITIALIZED``
 * until the backend cleanup ticket adds a dedicated
 * ``MASTER_PROFILE_UPDATED`` slug (tracked separately).
 */
export const patchMasterProfile = (patch: {
  bio?: string;
  display_name?: string;
}): Promise<ProfilePatchResponse> => {
  // DRF-1814: только переданные поля — имя без «о себе» не затирает «о себе»
  // пустой строкой (владелец полей — каталог, он пишет ровно то, что пришло).
  const body: Record<string, string> = {};
  if (patch.bio !== undefined) body.bio = patch.bio;
  if (patch.display_name !== undefined) body.display_name = patch.display_name;
  return request("/profile", { method: "PATCH", body: JSON.stringify(body) });
};

/**
 * Upload a new profile photo (multipart). Bypasses the shared
 * ``request()`` helper so the browser sets the multipart boundary
 * correctly (MM3/MM4 lesson: ``request()`` injects
 * ``application/json`` when a body is present, which clobbers the
 * boundary string and the backend MultiPartParser rejects with 400).
 */
export const uploadMasterProfilePhoto = async (
  file: File,
): Promise<ProfilePatchResponse> => {
  const fd = new FormData();
  fd.set("photo", file);
  const initData = getInitData();
  const headers = new Headers();
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
  applySalonChoiceHeader(headers);
  // No Content-Type — let fetch set the multipart boundary.
  const res = await fetch(`${MASTER_API_BASE}/profile`, {
    method: "PATCH",
    headers,
    body: fd,
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
  return (await res.json()) as ProfilePatchResponse;
};

// DRF-1814: лимитов «о себе» и байтов фото здесь больше НЕТ. Они приходят в
// `MasterProfileCard.limits` из каталога (DRF-1960) — те же числа, которыми
// каталог проверяет запись. Литерал 280 при лимите каталога 500 был двумя
// числами на один предмет (GAP_MAP §3 «лимиты — данные контракта»).
/** Форматы фото — как у каталога (`ALLOWED_FORMATS`); предпроверка до сети. */
export const MASTER_PROFILE_PHOTO_MIME_ALLOWLIST = new Set<string>([
  "image/jpeg",
  "image/png",
  "image/webp",
]);

// --- Экран 07 «Профиль мастера» (DRF-1814, часть B) ------------------------
// Зеркало apps/master_api/views_profile_card.py (часть A): карточка с
// лимитами каталога, бейджем из реального слота и чипами из выбранных
// шаблонов; портфолио — прокси в каталог, субъект — мастер из initData.

export interface ProfileLimits {
  bio: number;
  display_name_min: number;
  avatar_bytes: number;
  portfolio_bytes: number;
  portfolio_count: number;
}

export interface MasterProfileCard {
  master: { id: string; name: string; bio: string; photo_url: string };
  limits: ProfileLimits;
  portfolio: { count: number; limit: number };
  accepts_today: boolean;
  accepts_today_reason: string | null;
  categories: string[];
  categories_reason: string | null;
}

export interface PortfolioItem {
  id: string;
  image_url: string;
  sort_order: number;
  created_at: string;
}

export interface PortfolioList {
  items: PortfolioItem[];
  count: number;
  limit: number;
}

export const getMasterProfileCard = (): Promise<MasterProfileCard> =>
  request("/profile/card", { method: "GET" });

export const getPortfolio = (): Promise<PortfolioList> =>
  request("/profile/portfolio", { method: "GET" });

export const deletePortfolioItem = (
  itemId: string,
): Promise<{ count: number; limit: number }> =>
  request(`/profile/portfolio/${encodeURIComponent(itemId)}`, {
    method: "DELETE",
  });

/** Загрузка работы — multipart `image`; тот же обход `request()`, что у фото профиля. */
export const uploadPortfolioPhoto = async (
  file: File,
): Promise<PortfolioItem> => {
  const fd = new FormData();
  fd.set("image", file);
  const initData = getInitData();
  const headers = new Headers();
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
  applySalonChoiceHeader(headers);
  const res = await fetch(`${MASTER_API_BASE}/profile/portfolio`, {
    method: "POST",
    headers,
    body: fd,
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
  return (await res.json()) as PortfolioItem;
};

export const MASTER_SESSION_STORAGE_KEY = "master_token";

// --- M3 schedule types ----------------------------------------------------
// Mirrors apps/master_api/services/schedule.py::ScheduleResponse.to_dict().
// Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M3.

export interface ScheduleFreeWindow {
  start: string; // HH:MM (tenant-local)
  end: string; // HH:MM
  duration_min: number;
}

export type ScheduleConflictType =
  "double_booking" | "outside_hours" | "overlapping_exception";

export interface ScheduleConflict {
  type: ScheduleConflictType | string;
  booking_id: string;
  description: string;
}

export interface ScheduleBooking {
  booking_id: string;
  visit_at: string; // ISO UTC
  duration_min: number;
  service_name: string;
  client_first_name: string;
  client_last_initial: string;
  is_in_progress: boolean;
  is_returning_customer: boolean;
}

export interface ScheduleBlock {
  exception_id: string;
  start: string; // ISO UTC
  end: string;
  reason: string; // lunch|vacation|sick|personal|other
  approved: boolean;
}

export interface ScheduleDay {
  date: string; // YYYY-MM-DD (tenant-local)
  is_off_day: boolean;
  working_hours: { start: string; end: string } | null;
  bookings: ScheduleBooking[];
  blocks: ScheduleBlock[];
  free_windows: ScheduleFreeWindow[];
  conflicts: ScheduleConflict[];
}

export interface MasterScheduleResponse {
  tenant_tz: string;
  from: string; // YYYY-MM-DD
  to: string; // YYYY-MM-DD
  days: ScheduleDay[];
}

export type AvailabilityReasonClass =
  "vacation" | "sick" | "personal" | "other";

export interface AvailabilityRequestBody {
  start: string; // ISO datetime
  end: string; // ISO datetime
  reason_class: AvailabilityReasonClass;
  reason_text?: string;
}

export interface AvailabilityRequestResponse {
  request_id: string;
  status: string;
  requested_start: string | null;
  requested_end: string | null;
  reason_class: string;
  created_at: string;
}

export interface PendingAvailabilityItem {
  request_id: string;
  requested_start: string | null;
  requested_end: string | null;
  reason_class: string;
  reason_text: string;
  status: string;
  decided_at: string | null;
  decided_by_name: string | null;
  rejection_reason: string | null;
}

export interface PendingAvailabilityResponse {
  items: PendingAvailabilityItem[];
}

export const getMasterSchedule = (
  params: { from?: string; to?: string } = {},
): Promise<MasterScheduleResponse> => {
  const search = new URLSearchParams();
  if (params.from) search.set("from", params.from);
  if (params.to) search.set("to", params.to);
  const qs = search.toString();
  return request(`/schedule${qs ? `?${qs}` : ""}`, { method: "GET" });
};

export const requestAvailability = (
  body: AvailabilityRequestBody,
): Promise<AvailabilityRequestResponse> =>
  request("/availability", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getPendingAvailability =
  (): Promise<PendingAvailabilityResponse> =>
    request("/availability/pending", { method: "GET" });

// --- Переписка мастер↔клиент снята (DRF-1255, OD-7) ------------------------
// Клиентских функций к /conversations* здесь больше нет: у мастера нет прямой
// переписки с клиентом. Бэкенд и данные — DRF-1528 (не удаляются здесь).
// Поля dashboard `inbox_preview` / `conversations_unread` остаются в типе как
// контракт сервера до DRF-1528; UI их не читает.

/**
 * Fields that must NEVER appear in ANY master-facing response — not just
 * the conversations list this gate was originally wired to. Spec §M5
 * lines 608-615:
 *
 *     «❌ No LTV / financial signal · ❌ No reveal-phone hint · …
 *      ✅ Customer first name only»
 *
 * The authoritative copy of this list now lives on the backend
 * (`apps/master_api/pii.py`), where it is enforced across every master
 * read endpoint by `apps/master_api/tests/test_pii_boundary.py`. Keep the
 * two lists identical — that test asserts they have not drifted.
 *
 * DRF-1360: for months this list said `phone_masked` was forbidden while
 * the customer roster on the next tab shipped it in every row, because
 * the check was wired to one screen instead of the surface. The backend
 * sweep is the fix; this stays as client-side defence-in-depth.
 *
 * {@link findForbiddenPiiKeys} returns the forbidden keys observed (empty
 * when clean). Screens `console.warn` if non-empty and keep rendering —
 * defence-in-depth, not a hard failure (backend is the authority).
 */
export const FORBIDDEN_PII_KEYS = [
  "phone",
  "phone_number",
  "phone_masked",
  "ltv",
  "ltv_rub",
  "email",
  "client_last_name",
  "client_full_name",
] as const;

export function findForbiddenPiiKeys(item: Record<string, unknown>): string[] {
  return FORBIDDEN_PII_KEYS.filter((k) => k in item);
}

// --- M7 notification preferences (Bundle B / item 3) --------------------
// Mirrors apps/master_api/services/notification_prefs.py +
// apps/master_api/views.py::notification_prefs. Backend envelope:
//   GET   200 → {prefs: MasterNotificationPrefs}
//   PATCH 200 → {prefs: MasterNotificationPrefs}
//   PATCH 400 → {error: slug, detail: string}  (parsed by request() → ApiError.slug)
//
// Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M7
// (lines 777-843). Quiet hours are stored as naive HH:MM strings in
// the tenant's local timezone — no client-side TZ coercion.

export interface MasterNotificationPrefs {
  new_booking: boolean;
  booking_change: boolean;
  personal_message: boolean;
  /** Always true. Backend rejects PATCH urgent=false with 400 urgent_forced_on. */
  urgent: boolean;
  quiet_hours_enabled: boolean;
  /** "HH:MM" tenant-local. quiet_start > quiet_end means overnight window. */
  quiet_start: string;
  /** "HH:MM" tenant-local. */
  quiet_end: string;
  morning_brief: boolean;
  evening_summary: boolean;
  /** ISO datetime. */
  updated_at: string;
}

/**
 * Slugs the backend may emit on 400 (apps/master_api/services/notification_prefs.py).
 * Surfaced via ApiError.slug — callers do not parse the body themselves.
 */
export type NotificationPrefsErrorSlug =
  "urgent_forced_on" | "time_invalid" | "bad_request";

/** Partial-update shape — all fields optional, urgent intentionally NOT settable. */
export type NotificationPrefsPatch = Partial<
  Omit<MasterNotificationPrefs, "urgent" | "updated_at">
>;

interface PrefsEnvelope {
  prefs: MasterNotificationPrefs;
}

/**
 * GET current prefs. First call lazily creates the row on the backend
 * with §M7 defaults (transparent — the UI does not show a «first load»
 * banner). Subsequent calls are read-only.
 */
export const getNotificationPrefs =
  async (): Promise<MasterNotificationPrefs> => {
    const env = await request<PrefsEnvelope>("/notification-prefs/", {
      method: "GET",
    });
    return env.prefs;
  };

/**
 * PATCH a subset of fields. On 400 the request() helper throws ApiError —
 * callers catch and inspect ``.slug`` against ``NotificationPrefsErrorSlug``.
 *
 * Trailing slash on the path matters: Django ``urlpatterns`` strips
 * ``APPEND_SLASH`` redirects on PATCH (POST/PATCH/DELETE on a slashless
 * URL get a 405 instead of the friendly 301).
 */
export const patchNotificationPrefs = async (
  patch: NotificationPrefsPatch,
): Promise<MasterNotificationPrefs> => {
  const env = await request<PrefsEnvelope>("/notification-prefs/", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return env.prefs;
};

// --- Tier 2 read-only Клиенты / Услуги (master-solo-surface §4.3 / §4.4) -
// Mirrors apps/master_api/services/customers.py + catalog.py. Backend
// envelopes:
//   GET /customers → {customers: MasterCustomer[]}
//   GET /catalog   → {services:  MasterServiceItem[]}
// Both endpoints are read-only and tenant-scoped at the backend.

/**
 * One row in the solo-provider customer roster.
 *
 * **No customer phone, in any form.** Not full, not masked, not the last
 * N digits. Owner decision DRF-1039, restated verbatim in DRF-1360
 * (OD-W2-2): «телефон клиента исполнителю не передаётся ни в каком виде»
 * — there is no "last four digits are only an identifier" exception.
 * This row carried `phone_masked` ("+7 ••• ••• 14 67") until DRF-1360.
 *
 * A phone-reveal endpoint (audit event or not) is **out of scope, not
 * deferred**: do not build it without a new, separate owner decision on
 * PII. `phone_masked` is in {@link FORBIDDEN_PII_KEYS}; the backend sweep
 * `apps/master_api/tests/test_pii_boundary.py` fails CI if it returns.
 *
 * Telling two same-named customers apart is a separate open owner
 * question — a phone fragment is not the answer to it.
 */
export interface MasterCustomer {
  bot_user_id: string;
  /** First display name only (Tau §4.3 card title). */
  first_name: string;
  /** ISO 8601 UTC, or null when no qualifying visit. */
  last_visit_at: string | null;
  last_visit_service_name: string;
  total_visits: number;
  /** True when total_visits >= 2 (mirrors master dashboard semantics). */
  is_returning: boolean;
  /**
   * True when last_visit_at > 60d ago AND total_visits >= 3.
   * Drives the "Давно не были" section per Tau §4.3.
   */
  at_risk: boolean;
}

interface CustomersEnvelope {
  customers: MasterCustomer[];
}

/**
 * GET the read-only customer roster for the calling master. Sorted by
 * `last_visit_at` DESC. Empty array when the master has no bookings yet
 * (cold-start tenant).
 */
export const getMasterCustomers = async (): Promise<MasterCustomer[]> => {
  const env = await request<CustomersEnvelope>("/customers", { method: "GET" });
  return env.customers;
};

/**
 * One row in the master's services catalog. Sourced from the
 * `MasterService` mapping (PR #518); price + duration are snapshot from
 * the linked `CatalogService` mirror.
 */
export interface MasterServiceItem {
  service_id: string;
  name: string;
  /** Integer roubles. Null when service is "by request" / legacy unpriced. */
  price_rub: number | null;
  /** 0 when unknown (legacy mirror rows). */
  duration_min: number;
  description: string;
  /** Coarse bucket derived from service slug. Used for Tau §4.4 section headers. */
  category: string;
  is_active: boolean;
}

interface CatalogEnvelope {
  services: MasterServiceItem[];
}

/**
 * GET the read-only services list for the calling master. Sorted by
 * `(category, name)`. Empty array when no services are mapped — the
 * screen renders Tau's empty-state copy.
 */
export const getMasterCatalog = async (): Promise<MasterServiceItem[]> => {
  const env = await request<CatalogEnvelope>("/catalog", { method: "GET" });
  return env.services;
};

// --- Billing (D7 / C2 / card-setup) -----------------------------------------
//
// Mirrors the W3 proxies in apps/master_api:
//   GET  /api/v1/master/billing/status        (C2, verbatim `data`)
//   POST /api/v1/master/billing/card-setup    (D7 card binding start)
// Error slugs surfaced to the screen: 403 forbidden, 400 validation_error,
// 503 specialist_mapping_unavailable, 404 specialist_not_found,
// 502 billing_upstream_unavailable.

export type SubscriptionStatus =
  "trial" | "active" | "past_due" | "canceled" | "none";

export interface BillingStatusNextCharge {
  subscription_amount: string;
  fees_amount: string;
  total_amount: string;
  date: string;
}

export interface BillingStatusSubscription {
  status: SubscriptionStatus;
  tariff: "solo" | "salon" | null;
  current_period_end: string | null;
  next_charge: BillingStatusNextCharge | null;
}

export interface BillingStatusFees {
  pending_total: string;
  pending_count: number;
}

export interface BillingStatusInvoice {
  id: string;
  amount: string;
  status: string;
  paid_at: string;
}

export interface BillingCard {
  brand: string;
  last4: string;
}

/** C2 status payload (verbatim upstream `data`). */
export interface BillingStatus {
  specialist_id: string;
  subscription: BillingStatusSubscription;
  fees: BillingStatusFees;
  last_invoice: BillingStatusInvoice | null;
  /** Forward-compat: present when the upstream exposes the bound card. */
  card?: BillingCard | null;
}

interface BillingStatusEnvelope {
  data: BillingStatus;
}

/** GET the session master's subscription/fees/invoice status (C2). */
export const getBillingStatus = async (): Promise<BillingStatus> => {
  const env = await request<BillingStatusEnvelope>("/billing/status", {
    method: "GET",
  });
  return env.data;
};

export type BillingTariff = "solo" | "salon";

export interface CardSetupResponse {
  confirmation_url: string;
}

interface CardSetupEnvelope {
  data: CardSetupResponse;
}

/**
 * Start card binding (D7). The proxy validates tariff/return_url
 * (400 validation_error), forbids foreign identities (403), and fails
 * closed with 503 specialist_mapping_unavailable when the master has
 * no Ayla link yet. On success the caller opens
 * `confirmation_url` in the payment webview.
 */
export const cardSetup = async (input: {
  tariff: BillingTariff;
  return_url: string;
}): Promise<CardSetupResponse> => {
  const env = await request<CardSetupEnvelope>("/billing/card-setup", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return env.data;
};

// --- Раздел «Ayla»: диалог мастера с ассистентом (DRF-1180) ---------------
// Зеркалит apps/master_api/views_assistant.py.
//
// Решение владельца (OD-7 от 21.08, повторено 05.09): «Раздел «Ayla» —
// это диалог мастера с Ayla, тот же, что в боте, но через Mini App».
// Поэтому история читается с сервера, а не копится в браузере: она
// общая с салонным ботом.

export interface AylaMessage {
  id: string;
  /** "user" | "assistant" */
  role: string;
  content: string;
  /** Имя сработавшего инструмента или "" — для отладки, не для показа. */
  tool: string;
  /** ISO datetime, "" если сервер не смог его отрендерить. */
  created_at: string;
}

/**
 * Предложенное, но НЕ выполненное действие, меняющее данные.
 *
 * Эпик DRF-1180: «сначала она должна показать, что именно собирается
 * сделать, получить подтверждение пользователя и только после этого
 * выполнять действие». `summary` — то, что человек читает; `token` —
 * единственное, чем это можно выполнить, и аргументы лежат внутри
 * него, а не в этом объекте: иначе экран мог бы показать одно, а
 * отправить на исполнение другое.
 */
/** Строки карточки предложения (DRF-2153, макет DRF-1187): клиент / услуга / дата / время. */
export interface AylaBookingDetails {
  client: string;
  service: string;
  duration_min: number;
  /** «21 августа, четверг» */
  date: string;
  /** «12:30» */
  time: string;
  /** «12:30–13:30» */
  time_range?: string;
}

export interface AylaDayOffDetails {
  kind: "day_off";
  title: string;
  /** «Среда, 26 августа» */
  date: string;
  /** «Не работаю весь день» */
  change: string;
}

export interface AylaPendingAction {
  action: string;
  summary: string;
  confirm_label: string;
  token: string;
  expires_in_sec: number;
  details?: AylaBookingDetails | AylaDayOffDetails;
}

/** Карточки ответа — структура вместо абзаца (DRF-2153). Строятся сервером из данных. */
export interface AylaFreeWindow {
  start: string;
  end: string;
  book_url: string;
}
export type AylaCard =
  | {
      kind: "free_windows";
      date: string;
      day_off?: boolean;
      stale: boolean;
      notice: string | null;
      footer?: string | null;
      recheck: string | null;
      windows: AylaFreeWindow[];
      book_url?: string;
    }
  | {
      kind: "day";
      date: string;
      count: number;
      visits: {
        time: string;
        client: string;
        service: string;
        duration_min: number;
      }[];
    }
  | { kind: "clarify_client"; options: { client_id: string; label: string }[] }
  | {
      kind: "choose_service";
      options: { service_id: string; name: string; duration_min: number }[];
    }
  | {
      kind: "slot_taken";
      range?: string;
      alternatives: { time: string; start_at: string | null }[];
      /** «Выбрать другое время» → форма М-3 на тот же день. */
      book_url?: string;
    }
  | { kind: "open"; url: string; label: string };

/** Выбор из карточки — уходит модели уточнением, в нить пишется только текст кнопки. */
export interface AylaSelect {
  client_id?: string;
  start_at?: string;
}

export interface AylaAskResponse {
  answer: string;
  tool: string;
  pending_action: AylaPendingAction | null;
  cards?: AylaCard[];
  message_id: string;
}

export interface AylaConfirmResponse {
  answer: string;
  action: string;
  /** `false` — не выполнено: «занято» / «проверяем результат». */
  executed: boolean;
  /** Дверь после результата: «Открыть запись» → обычный экран деталей. */
  open?: { url: string; label: string } | null;
  cards?: AylaCard[];
  details?: AylaBookingDetails | AylaDayOffDetails | null;
  message_id: string;
}

/** Стартовый экран Ayla: контекст дня + чипы (DRF-2153). */
export interface AylaContextResponse {
  today: {
    date: string;
    count: number;
    next: {
      client_name_initial: string;
      time: string;
      service_name: string;
      duration_min: number;
    } | null;
  };
  chips: string[];
  chip_hints?: Record<string, string>;
}

export const getAylaContext = (): Promise<AylaContextResponse> =>
  request("/assistant/context", { method: "GET" });

/** Что уже сказано в диалоге, старое первым. */
export const getAylaHistory = (
  limit?: number,
): Promise<{ messages: AylaMessage[] }> =>
  request(`/assistant/history${limit ? `?limit=${limit}` : ""}`, {
    method: "GET",
  });

/** Задать вопрос. Ответ может нести предложение — оно НЕ выполнено. */
export const askAyla = (
  text: string,
  select?: AylaSelect,
): Promise<AylaAskResponse> =>
  request("/assistant/ask", {
    method: "POST",
    body: JSON.stringify(select ? { text, select } : { text }),
  });

/** Выполнить предложение. Только по талону — своих аргументов нет. */
export const confirmAylaAction = (
  token: string,
): Promise<AylaConfirmResponse> =>
  request("/assistant/confirm", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

// --- DRF-1845 «Принимаю записи» ---------------------------------------------
// Mirrors apps/master_api/views.py::accepting_bookings — a proxy to the
// catalog's availability route. The flag lives in the catalog only; the answer
// is its readback. The bot sees a pause on its next catalog sync (≤15 min).

export interface AcceptingBookingsResponse {
  accepting_bookings: boolean;
  status: string | null;
}

export const getAcceptingBookings = (): Promise<AcceptingBookingsResponse> =>
  request("/accepting-bookings", { method: "GET" });

export const setAcceptingBookings = (
  accepting: boolean,
): Promise<AcceptingBookingsResponse> =>
  request("/accepting-bookings", {
    method: "PATCH",
    body: JSON.stringify({ accepting_bookings: accepting }),
  });

// --- DRF-1857 «Мои отзывы» --------------------------------------------------
// Mirrors apps/master_api/views.py::reviews — a proxy to the catalog's
// own-reviews route under the master as subject. Rows are whitelisted by the
// bot; the client is «Имя Ф.» / «Клиент» / null (anonymous); no rating until a
// review exists.

export interface MasterReview {
  id: string;
  rating: number;
  text: string;
  client_name: string | null;
  service_name: string | null;
  created_at: string;
}

export interface MasterReviewsResponse {
  review_count: number;
  rating: number | null;
  reviews: MasterReview[];
}

export const getMasterReviews = (): Promise<MasterReviewsResponse> =>
  request("/reviews", { method: "GET" });

// --- Ручки М-2 для «Новой записи» мастера (DRF-2154 → DRF-2155, М-3) -------
//
// Субъект — мастер из initData: ни в путях, ни в телах нет master_id.
// Телефон клиента — только ВХОД для нового гостя; наружу не приходит ни
// в списке поиска, ни в деталях (DRF-1039, владелец 20.09).

import type { BookingSlot } from "./admin-api";

/** Строка `GET /customers?q=` — «Анна П.» + дата последнего визита у этого мастера. */
export interface MasterCustomerSearchRow {
  /** Ayla client id — уходит в `client_id` при создании. */
  id: string;
  /** «Имя Ф.»; «Без имени», когда каталог не знает имени. */
  name: string;
  named: boolean;
  /** YYYY-MM-DD последнего completed визита у этого мастера; null — новый клиент. */
  last_visit_date: string | null;
}

export const searchMasterCustomers = async (
  q: string,
  opts: { signal?: AbortSignal } = {},
): Promise<MasterCustomerSearchRow[]> => {
  const env = await request<{ results: MasterCustomerSearchRow[] }>(
    `/customers?q=${encodeURIComponent(q)}`,
    { method: "GET", signal: opts.signal },
  );
  return env.results;
};

export interface MasterBookingSlotsResponse {
  date: string;
  timezone: string;
  service_id: string;
  duration_min: number;
  slots: BookingSlot[];
}

export const getMasterBookingSlots = (
  params: { serviceId: string; date: string },
  opts: { signal?: AbortSignal } = {},
): Promise<MasterBookingSlotsResponse> => {
  const qs = new URLSearchParams({
    date: params.date,
    service_id: params.serviceId,
  });
  return request(`/booking-slots?${qs.toString()}`, {
    method: "GET",
    signal: opts.signal,
  });
};

export interface MasterCreateBookingBody {
  service_id: string;
  /** ISO start, как отдало расписание. */
  start_at: string;
  /** Один ключ на попытку, тот же при повторе — иначе повтор станет второй записью. */
  idempotency_key: string;
  /** Ровно один путь (§14). Телефон — вход нового гостя, обратно не приходит. */
  client_id?: string;
  client_name?: string;
  client_phone?: string;
}

export interface MasterCreateBookingResult {
  outcome: "committed" | "conflict" | "blocked" | "pending" | "failed";
  detail: string;
  appointment_id?: string;
  /** `slot_taken` при conflict, `result_pending` при pending, иначе — как у стойки. */
  reason_code?: string;
  /** Ближайшие окна того дня при `slot_taken`; null — слоты были недоступны. */
  alternatives?: BookingSlot[] | null;
  alternatives_unavailable?: boolean;
  /** На `pending` — чтобы повтор был той же записью. */
  idempotency_key?: string;
}

/**
 * Создать запись к себе. Не через `request`: 409 «занято» и 202 «проверяем
 * результат» — исходы §18, не ошибки. Сеть упала / тело не прочитано —
 * `pending` с тем же ключом: запись могла лечь, «failed» подтолкнул бы
 * нажать ещё раз.
 */
export const createMasterBooking = async (
  body: MasterCreateBookingBody,
): Promise<MasterCreateBookingResult> => {
  const initData = getInitData();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
  applySalonChoiceHeader(headers);

  let res: Response;
  try {
    res = await fetch(`${MASTER_API_BASE}/bookings`, {
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
    const data = (await res.json()) as Partial<MasterCreateBookingResult> & {
      error?: string;
    };
    if (data.outcome) return data as MasterCreateBookingResult;
    return { outcome: "failed", detail: data.detail ?? "неизвестная ошибка" };
  } catch {
    return {
      outcome: "pending",
      detail: "ответ не прочитан",
      idempotency_key: body.idempotency_key,
    };
  }
};
