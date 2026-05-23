/**
 * Master ↔ Admin internal-chat API client — both surfaces.
 *
 * Mirrors backend at apps/internal_chat/views.py + apps/internal_chat/urls.py
 * routed under /api/v1/internal-chat/{master,admin}/. The master and
 * admin endpoint families share envelopes / payload shapes (returned by
 * the same ``_serialize_thread_*`` helpers in views.py) so it is cheaper
 * to keep them in a single client module than to split parallel files.
 *
 * Auth: MAX initData header.
 *   - master/* → require_master_init_data decorator (apps/master_api/auth)
 *   - admin/*  → require_admin_role decorator      (apps/admin_api/auth)
 *
 * Spec sources:
 *   - docs/design/handoffs/2026-05-19-master-admin-internal-chat-handoff.md
 *   - apps/internal_chat/models.py + views.py
 *   - apps/internal_chat/tests/test_{master,admin}_views.py
 *
 * §2.7 sender-display rule lives in the UI layer — the API returns the
 * raw sender_admin_signed_name field; the screen decides display via
 * senderDisplayForMaster (master side) or senderDisplayForAdmin
 * (admin side). The two have opposite privacy intents and MUST NOT
 * cross-call.
 *
 * §2.11 enforcement is also a UI concern — the API faithfully returns
 * what the backend stores; the UI never auto-formats customer names.
 */

import { ApiError } from "./api";
import { applyDevBypassHeaders } from "./dev-bypass";
import { getInitData } from "./max-sdk";

const INTERNAL_CHAT_API_BASE = "/api/v1/internal-chat";

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

  const res = await fetch(`${INTERNAL_CHAT_API_BASE}${path}`, {
    ...init,
    headers,
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
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- topic taxonomy (handoff §5.1, mirrors TopicChoices) -------------------

/**
 * Canonical topic codes — DB-enforced via apps/internal_chat/models.py
 * TopicChoices. Keep in sync with the backend; an unknown code returns
 * 400 from the POST /master/threads/ endpoint.
 */
export const TOPIC_CODES = [
  "earnings_dispute",
  "leave_request",
  "review_concern",
  "schedule_change",
  "offboarding_discussion",
  "other_master_complaint",
  "general",
] as const;

export type TopicCode = (typeof TOPIC_CODES)[number];

/**
 * Russian topic labels — verbatim from handoff §3.3 + §5.1. Used for the
 * topic-picker sheet and the thread list rows. Hardcoded here (not
 * derived from the backend's ``topic_display``, which is English by
 * design — Django ``choices`` second-tuple values default to language
 * neutral identifiers; we Russianise at the UI layer).
 */
export const TOPIC_LABELS_RU: Record<TopicCode, string> = {
  earnings_dispute: "💰 По доходу или комиссии",
  leave_request: "🛌 Выходные или график",
  review_concern: "📋 По отзыву",
  schedule_change: "📅 Изменить расписание",
  offboarding_discussion: "🚪 Думаю об уходе",
  other_master_complaint: "👥 Про другого мастера",
  general: "❓ Что-то другое",
};

/** Lifecycle status codes — handoff §6.1 / models.StatusChoices. */
export const THREAD_STATUS_CODES = [
  "open",
  "admin_responded",
  "master_responded",
  "active_discussion",
  "resolved",
  "auto_closed_inactive",
  "escalated_to_founder",
] as const;

export type ThreadStatus = (typeof THREAD_STATUS_CODES)[number];

/**
 * UI-side status grouping for the filter chips.
 *
 *  - active  → master is awaiting a reply OR there's a back-and-forth in progress
 *  - resolved→ admin marked the thread resolved (composer hidden)
 *  - archived→ auto-closed-inactive (90d/14d rules — handoff §4.8)
 *
 * "all" is the default unfiltered listing.
 */
export type StatusFilter = "all" | "active" | "resolved" | "archived";

export const ACTIVE_STATUSES: ReadonlySet<ThreadStatus> = new Set<ThreadStatus>(
  [
    "open",
    "admin_responded",
    "master_responded",
    "active_discussion",
    "escalated_to_founder",
  ],
);

export function isActiveStatus(status: ThreadStatus): boolean {
  return ACTIVE_STATUSES.has(status);
}

export function isResolvedStatus(status: ThreadStatus): boolean {
  return status === "resolved";
}

export function isArchivedStatus(status: ThreadStatus): boolean {
  return status === "auto_closed_inactive";
}

/**
 * A status code that hides the composer in the master surface.
 * Mirrors the backend's 409 «thread_closed» rejection at POST
 * /messages/ — resolved + auto_closed_inactive both block sends.
 */
export function isComposerLocked(status: ThreadStatus): boolean {
  return isResolvedStatus(status) || isArchivedStatus(status);
}

// --- payload types --------------------------------------------------------

/**
 * Compact thread row in the list payload.
 *
 * Shape mirrors apps/internal_chat/views.py::_serialize_thread_summary —
 * any field rename on the backend MUST flow here too.
 */
export interface InternalChatThreadSummary {
  id: string;
  topic: TopicCode;
  topic_display: string;
  subject: string;
  status: ThreadStatus;
  is_sensitive: boolean;
  linked_artifact_type: string;
  linked_artifact_id: string | null;
  assigned_admin_id: string | null;
  founder_added_at: string | null;
  sla_due_at: string;
  created_at: string;
  last_activity_at: string;
  resolved_at: string | null;
  /**
   * Master pointer is attached by the ADMIN list endpoint
   * (apps/internal_chat/views.py::admin_threads_collection) — it is
   * absent in the master list payload because the master only sees
   * own threads. UI code should treat it as optional and gracefully
   * fall back to «Мастер» when missing.
   */
  master?: { id: string; name: string };
}

export interface InternalChatMessage {
  id: string;
  sender_role: "master" | "admin" | "founder" | "system";
  sender_user_id: string | null;
  sender_admin_signed_name: string;
  body: string;
  is_system_message: boolean;
  sent_at: string;
  read_by_master_at: string | null;
  read_by_admin_at: string | null;
  read_by_founder_at: string | null;
}

export interface InternalChatThreadDetail extends InternalChatThreadSummary {
  master: { id: string; name: string };
  messages: InternalChatMessage[];
}

export interface ListThreadsResponse {
  items: InternalChatThreadSummary[];
  total_count: number;
  offset: number;
  limit: number;
}

export interface ThreadEnvelope {
  thread: InternalChatThreadDetail;
}

export interface ThreadSummaryEnvelope {
  thread: InternalChatThreadSummary;
}

export interface MessageEnvelope {
  message: InternalChatMessage;
  thread: InternalChatThreadSummary;
}

export interface MarkReadResponse {
  marked: number;
}

// --- constants ------------------------------------------------------------

export const INTERNAL_CHAT_MESSAGE_MAX = 4000;
export const INTERNAL_CHAT_SUBJECT_MAX = 200;
export const INTERNAL_CHAT_ESCALATION_REASON_MAX = 1000;

// --- master endpoints -----------------------------------------------------

/**
 * List the master's own threads — backend filters by tenant+master in
 * the decorator + view. Server caps ``limit`` at MAX_LIST_LIMIT=50.
 */
export const listMasterThreads = (params?: {
  limit?: number;
  offset?: number;
}): Promise<ListThreadsResponse> => {
  const q = new URLSearchParams();
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  return request(`/master/threads/${qs ? `?${qs}` : ""}`, { method: "GET" });
};

export const getMasterThread = (
  threadId: string,
): Promise<ThreadEnvelope> =>
  request(`/master/threads/${threadId}/`, { method: "GET" });

/**
 * Create a new master-opened thread. Backend requires ``topic`` +
 * ``first_message_body``; optional ``subject`` (auto-empty) +
 * ``linked_artifact_type`` / ``linked_artifact_id``.
 *
 * Returns 201 with the full detail payload (messages included).
 */
export const createMasterThread = (body: {
  topic: TopicCode;
  first_message_body: string;
  subject?: string;
  linked_artifact_type?: string;
  linked_artifact_id?: string;
}): Promise<ThreadEnvelope> =>
  request(`/master/threads/`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const sendMasterInternalMessage = (
  threadId: string,
  body: string,
): Promise<MessageEnvelope> =>
  request(`/master/threads/${threadId}/messages/`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });

export const markMasterInternalThreadRead = (
  threadId: string,
): Promise<MarkReadResponse> =>
  request(`/master/threads/${threadId}/mark-read/`, { method: "POST" });

export const escalateMasterInternalThreadToFounder = (
  threadId: string,
  reason: string,
): Promise<ThreadSummaryEnvelope> =>
  request(`/master/threads/${threadId}/escalate-to-founder/`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });

// --- admin endpoints ------------------------------------------------------

/**
 * List ALL threads in the tenant (admin queue).
 *
 * Mirrors backend at apps/internal_chat/views.py::admin_threads_collection.
 * Auth: MAX initData header gated by require_admin_role decorator.
 * Owner + Admin see every row. Receptionist sees the list too but
 * sensitive-thread subjects are redacted server-side. Customer / unrelated
 * users → 403.
 *
 * Query params:
 *   - status:             filter by ThreadStatus
 *   - topic:              filter by TopicCode
 *   - master_id:          filter to a single master
 *   - assigned_admin_id:  filter to threads pinned to an admin
 *   - search:             icontains on subject (case-insensitive)
 *   - limit / offset:     pagination (server caps limit at 50)
 */
export const listAdminThreads = (params?: {
  status?: ThreadStatus;
  topic?: TopicCode;
  master_id?: string;
  assigned_admin_id?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<ListThreadsResponse> => {
  const q = new URLSearchParams();
  if (params?.status) q.set("status", params.status);
  if (params?.topic) q.set("topic", params.topic);
  if (params?.master_id) q.set("master_id", params.master_id);
  if (params?.assigned_admin_id)
    q.set("assigned_admin_id", params.assigned_admin_id);
  if (params?.search) q.set("search", params.search);
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  return request(`/admin/threads/${qs ? `?${qs}` : ""}`, { method: "GET" });
};

export const getAdminThread = (
  threadId: string,
): Promise<ThreadEnvelope> =>
  request(`/admin/threads/${threadId}/`, { method: "GET" });

/**
 * Send a message as admin. Body field ``sender_admin_signed_name`` is the
 * §2.7 opt-in signature: when non-empty, the master will see
 * «Студия — Натали» instead of the default «Студия». An empty value
 * (or omission) keeps the message attributed to the generic admin team.
 */
export const sendAdminMessage = (
  threadId: string,
  payload: { body: string; sender_admin_signed_name?: string },
): Promise<MessageEnvelope> =>
  request(`/admin/threads/${threadId}/messages/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const markAdminThreadRead = (
  threadId: string,
): Promise<MarkReadResponse> =>
  request(`/admin/threads/${threadId}/mark-read/`, { method: "POST" });

/**
 * Close (resolve) a thread from the admin side. Mirrors
 * apps/internal_chat/views.py::admin_close_thread — returns the
 * thread summary with ``status=resolved`` and ``resolved_at`` set.
 */
export const closeAdminThread = (
  threadId: string,
): Promise<ThreadSummaryEnvelope> =>
  request(`/admin/threads/${threadId}/close/`, { method: "POST" });

// --- derived helpers ------------------------------------------------------

/**
 * Sender-display per handoff §2.7. Master always sees «Студия» as the
 * admin team identity UNLESS the responding admin explicitly signed
 * (``sender_admin_signed_name`` non-empty), in which case the signature
 * appears appended as «Студия — Натали». For master messages we return
 * «Вы».
 *
 * Founder messages render as «Основатель» (handoff §3.6). System
 * messages render as «Система».
 */
export function senderDisplayForMaster(message: InternalChatMessage): string {
  if (message.sender_role === "master") return "Вы";
  if (message.sender_role === "founder") return "Основатель";
  if (message.sender_role === "system") return "Система";
  // admin path
  const signed = (message.sender_admin_signed_name || "").trim();
  if (signed) return `Студия — ${signed}`;
  return "Студия";
}

/**
 * Compute unread-count for the master from the message list — admin /
 * founder / system messages that lack ``read_by_master_at`` are unread.
 * Used to render the dot on the thread row + on the profile entry.
 */
export function unreadCountForMaster(
  messages: InternalChatMessage[],
): number {
  let n = 0;
  for (const m of messages) {
    if (m.sender_role === "master") continue;
    if (m.read_by_master_at == null) n += 1;
  }
  return n;
}

/**
 * Sender-display from the ADMIN perspective.
 *
 * Admin SEES the master's full identity per handoff §4.2 (which differs
 * from §2.7 — that privacy rule applies only to the master direction).
 * Admin → admin shows «Вы» for own messages; admin → other-admin shows
 * the signed name when present, else generic «Студия» — admins on the
 * same tenant are surface-level interchangeable in the queue.
 *
 * @param message     The message to label.
 * @param masterName  Display name of the thread's master (from thread.master.name);
 *                    falls back to «Мастер» when missing.
 * @param meBotUserId Optional — current admin's BotUser.id. When provided,
 *                    own admin messages render as «Вы».
 */
export function senderDisplayForAdmin(
  message: InternalChatMessage,
  masterName: string,
  meBotUserId?: string | null,
): string {
  if (message.sender_role === "master") return masterName || "Мастер";
  if (message.sender_role === "founder") return "Основатель";
  if (message.sender_role === "system") return "Система";
  // admin path — show «Вы» for self, else use the signed name when present
  if (meBotUserId && message.sender_user_id === meBotUserId) return "Вы";
  const signed = (message.sender_admin_signed_name || "").trim();
  if (signed) return `Студия — ${signed}`;
  return "Студия";
}

/**
 * Compute admin-side unread counts from a fetched message list.
 *
 * Counts messages NOT yet ``read_by_admin_at`` that were authored by
 * the master / founder / system — admin's own outbound messages are
 * never "unread to admin". Used to render the thread-card unread dot
 * and the AdminTeamScreen nav-badge total.
 */
export function unreadCountForAdmin(
  messages: InternalChatMessage[],
): number {
  let n = 0;
  for (const m of messages) {
    if (m.sender_role === "admin") continue;
    if (m.read_by_admin_at == null) n += 1;
  }
  return n;
}

/**
 * Heuristic: does this thread need an admin response right now?
 *
 * Used for the admin home nav badge — the master-side already updates
 * ``status`` to ``master_responded`` when the master sends a message
 * into an open thread, so this is the cheapest proxy for «admin owes
 * a reply». Active threads stuck in ``open`` (master started, admin
 * hasn't replied yet) also need attention.
 */
export function threadNeedsAdminResponse(
  thread: InternalChatThreadSummary,
): boolean {
  return thread.status === "master_responded" || thread.status === "open";
}

/**
 * Group threads into the three admin filter buckets per handoff §4.1:
 *   - awaitingResponse → status ∈ {open, master_responded}
 *   - inDiscussion     → status ∈ {admin_responded, active_discussion}
 *   - escalated        → founder_added_at set OR status = escalated_to_founder
 *   - resolved         → status = resolved
 *   - archived         → status = auto_closed_inactive
 *
 * Escalated threads are removed from the awaiting/discussion buckets
 * (deduped) so the queue stays clean.
 */
export type AdminThreadBucket =
  | "awaiting"
  | "discussion"
  | "escalated"
  | "resolved"
  | "archived";

export function adminBucketFor(
  thread: InternalChatThreadSummary,
): AdminThreadBucket {
  if (
    thread.founder_added_at ||
    thread.status === "escalated_to_founder"
  )
    return "escalated";
  if (thread.status === "resolved") return "resolved";
  if (thread.status === "auto_closed_inactive") return "archived";
  if (thread.status === "open" || thread.status === "master_responded")
    return "awaiting";
  return "discussion";
}
