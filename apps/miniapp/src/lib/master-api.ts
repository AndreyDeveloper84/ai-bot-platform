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

const MASTER_API_BASE = "/api/v1/master";

interface ErrorBody {
  error: string;
  detail: string;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const initData = getInitData();
  const headers = new Headers(init.headers);
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
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
}): Promise<ProfilePatchResponse> =>
  request("/profile", {
    method: "PATCH",
    body: JSON.stringify({ bio: patch.bio ?? "" }),
  });

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

/** §M4 line 527 — bio UI cap (server-side MAX_BIO_LENGTH = 280). */
export const MASTER_PROFILE_BIO_MAX = 280;
/** §M4 line 550 — photo upload cap. Mirrors backend PHOTO_MAX_BYTES. */
export const MASTER_PROFILE_PHOTO_MAX_BYTES = 10 * 1024 * 1024;
/** §M4 line 550 — accepted MIME types. */
export const MASTER_PROFILE_PHOTO_MIME_ALLOWLIST = new Set<string>([
  "image/jpeg",
  "image/png",
  "image/webp",
]);

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
  | "double_booking"
  | "outside_hours"
  | "overlapping_exception";

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
  | "vacation"
  | "sick"
  | "personal"
  | "other";

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

// --- M5 conversations types -----------------------------------------------
// Mirrors apps/master_api/services/conversations.py::ConversationsListResponse.

export type ConversationSection =
  | "awaiting_master"
  | "ai_drafted"
  | "ai_handling"
  | "resolved"
  | "other";

export type ConversationFilter = "active" | "all" | "resolved";

export interface MasterConversationItem {
  conversation_id: string;
  client_first_name: string;
  client_last_initial: string;
  is_returning_customer: boolean;
  section: ConversationSection | string;
  last_message_excerpt: string;
  last_message_at: string | null;
  sla_tier: SlaTier;
  ai_drafted_reply_available: boolean;
  reason_chip: string | null;
  resolved_outcome: string | null;
}

export interface ConversationSectionCounts {
  awaiting_master: number;
  ai_drafted: number;
  ai_handling: number;
  resolved_today: number;
}

export interface MasterConversationsResponse {
  items: MasterConversationItem[];
  section_counts: ConversationSectionCounts;
  next_cursor: string | null;
}

export const getMasterConversations = (
  params: {
    filter?: ConversationFilter;
    search?: string;
    cursor?: string;
    limit?: number;
  } = {},
): Promise<MasterConversationsResponse> => {
  const search = new URLSearchParams();
  if (params.filter) search.set("filter", params.filter);
  if (params.search) search.set("search", params.search);
  if (params.cursor) search.set("cursor", params.cursor);
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  const qs = search.toString();
  return request(`/conversations${qs ? `?${qs}` : ""}`, { method: "GET" });
};

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

export function findForbiddenPiiKeys(
  item: Record<string, unknown>,
): string[] {
  return FORBIDDEN_PII_KEYS.filter((k) => k in item);
}

// --- M6 conversation detail types ----------------------------------------
// Mirrors apps/master_api/services/conversation_detail.py
// (ConversationDetailResponse / MessageItem / MessageResponse).
// Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M6.

export type ConversationTier =
  | "ai_continuity"
  | "human_supervised"
  | "human_locked";

/** «Передать админу» reason classes — mirrors backend `VALID_REASON_CLASSES`. */
export type PromoteReasonClass =
  | "complaint"
  | "financial"
  | "medical"
  | "other";

export interface ConversationMessage {
  message_id: string;
  /** "user" | "assistant" | "system" — backend uses lowercase Message.Role. */
  role: string;
  content: string;
  /** ISO datetime — empty string if the server couldn't render it. */
  sent_at: string;
  composed_by_master: boolean;
  composed_by_master_id: string | null;
}

export interface ConversationAiDraft {
  draft_id: string | null;
  content: string | null;
  created_at: string | null;
}

export interface ConversationPermissions {
  can_compose: boolean;
  can_promote_to_human_locked: boolean;
}

export interface ConversationDetailResponse {
  conversation_id: string;
  tier: ConversationTier | string;
  /** complaint/financial/medical/other when HUMAN_LOCKED, null otherwise. */
  tier_reason: string | null;
  is_active: boolean;
  tier_locked_by_admin_name: string | null;
  tier_locked_since: string | null;
  client_first_name: string;
  client_last_initial: string;
  is_returning_customer: boolean;
  visit_count: number;
  messages: ConversationMessage[];
  ai_draft: ConversationAiDraft;
  permissions: ConversationPermissions;
}

export interface SendMessageResponse {
  message_id: string;
  content: string;
  sent_at: string;
  composed_by_master: boolean;
}

export interface MarkReadResponse {
  marked_count: number;
}

export interface PromoteResponse {
  conversation_id: string;
  tier: ConversationTier | string;
  tier_locked_at: string;
}

/** Mirrors backend MAX_COMPOSE_LENGTH (apps/master_api/services/conversation_detail.py). */
export const MASTER_COMPOSE_MAX_LENGTH = 2000;
/** Threshold at which the live counter starts being visible. */
export const MASTER_COMPOSE_COUNTER_THRESHOLD = 1500;

export const getConversationDetail = (
  conversationId: string,
): Promise<ConversationDetailResponse> =>
  request(`/conversations/${conversationId}`, { method: "GET" });

export const sendMasterMessage = (
  conversationId: string,
  content: string,
): Promise<SendMessageResponse> =>
  request(`/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });

export const markConversationRead = (
  conversationId: string,
): Promise<MarkReadResponse> =>
  request(`/conversations/${conversationId}/mark-read`, {
    method: "POST",
    body: JSON.stringify({}),
  });

export const promoteConversationToHumanLocked = (
  conversationId: string,
  reasonClass: PromoteReasonClass,
  reasonText?: string,
): Promise<PromoteResponse> =>
  request(`/conversations/${conversationId}/promote`, {
    method: "POST",
    body: JSON.stringify({
      reason_class: reasonClass,
      reason_text: reasonText ?? "",
    }),
  });

// --- M6 AI drafts (Bundle B / item 4 frontend) ----------------------------
// Mirrors apps/master_api/services/ai_drafts.py
// (DraftResponse / DraftMessageResponse) + apps/master_api/views.py
// (conversation_draft_generate / send_as_me / release_to_ai).
//
// Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M6
// (lines 632-738). Backend PR #535.
//
// Error envelopes are the standard `{error: slug, detail: string}` shape
// surfaced by the shared `request()` helper as `ApiError(status, slug,
// detail)`. Callers `catch (err)` and switch on `err.slug` — there is
// no separate structured response type needed.

/**
 * Generated draft payload returned by `POST .../drafts/generate`. Also
 * the shape served back by `getConversationDetail()`'s `ai_draft` field
 * when an ACTIVE draft exists for the caller's master — backend response
 * only includes `draft_id`, `content`, `created_at` in that read path
 * (no provider/model echo). We model both shapes via optional fields so
 * the response interfaces stay structurally compatible.
 */
export interface AiDraftPayload {
  draft_id: string;
  content: string;
  created_at: string;
  /** Present only on POST .../drafts/generate; absent on detail GET. */
  llm_provider?: string;
  /** Present only on POST .../drafts/generate; absent on detail GET. */
  llm_model?: string;
}

/**
 * 201 response from send-as-me / release-to-ai. `composed_by_master`
 * distinguishes the two paths (master-authored vs released-to-AI);
 * `was_edited` is true only when the master tapped «Отредактировать»
 * and passed `override_content`.
 */
export interface DraftMessageResponse {
  message_id: string;
  content: string;
  sent_at: string;
  composed_by_master: boolean;
  was_edited: boolean;
}

/** Slugs the backend may emit on 4xx/5xx for the draft endpoints. */
export type DraftErrorSlug =
  | "draft_already_acted"
  | "llm_unavailable"
  | "conversation_locked"
  | "tier_locked"
  | "bad_request"
  | "not_found";

/**
 * POST /api/v1/master/conversations/:id/drafts/generate
 *
 * Empty body. Returns the freshly-generated draft (or the existing
 * ACTIVE draft if we're inside the 60s idempotency window AND no new
 * customer message has arrived since).
 *
 * Throws `ApiError` with `.slug ∈ DraftErrorSlug`:
 *   - 400 `conversation_locked` (HUMAN_LOCKED tier)
 *   - 404 `not_found` (master not involved)
 *   - 503 `llm_unavailable` (provider failure)
 */
export const generateDraft = (
  conversationId: string,
): Promise<AiDraftPayload> =>
  request(`/conversations/${conversationId}/drafts/generate`, {
    method: "POST",
    body: JSON.stringify({}),
  });

/**
 * POST /api/v1/master/conversations/:id/drafts/:draftId/send-as-me
 *
 * Body: `{override_content?: string}`. When `overrideContent` is
 * undefined we omit the field entirely — the backend uses the draft's
 * LLM text. When provided, the backend uses the edited text and stamps
 * `was_edited=true` in the message's attribution metadata.
 *
 * Throws `ApiError` with `.slug ∈ DraftErrorSlug`:
 *   - 400 `draft_already_acted` (status != ACTIVE; race lost)
 *   - 400 `bad_request` (override too long / empty)
 *   - 403 `tier_locked` (HUMAN_LOCKED)
 *   - 404 `not_found`
 */
export const sendDraftAsMaster = (
  conversationId: string,
  draftId: string,
  overrideContent?: string,
): Promise<DraftMessageResponse> => {
  const body: Record<string, string> = {};
  if (overrideContent !== undefined) body.override_content = overrideContent;
  return request(`/conversations/${conversationId}/drafts/${draftId}/send-as-me`, {
    method: "POST",
    body: JSON.stringify(body),
  });
};

/**
 * POST /api/v1/master/conversations/:id/drafts/:draftId/release-to-ai
 *
 * Empty body. Releases the draft as a plain assistant message (no
 * master attribution — customer-side render is indistinguishable from
 * a fully-auto reply).
 *
 * Throws `ApiError` with `.slug ∈ DraftErrorSlug`:
 *   - 400 `draft_already_acted`
 *   - 403 `tier_locked`
 *   - 404 `not_found`
 */
export const releaseDraftToAi = (
  conversationId: string,
  draftId: string,
): Promise<DraftMessageResponse> =>
  request(`/conversations/${conversationId}/drafts/${draftId}/release-to-ai`, {
    method: "POST",
    body: JSON.stringify({}),
  });

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
  | "urgent_forced_on"
  | "time_invalid"
  | "bad_request";

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
export const getNotificationPrefs = async (): Promise<MasterNotificationPrefs> => {
  const env = await request<PrefsEnvelope>("/notification-prefs/", { method: "GET" });
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
  | "trial"
  | "active"
  | "past_due"
  | "canceled"
  | "none";

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
