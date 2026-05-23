/**
 * Master ↔ Admin internal-chat API client — master-side.
 *
 * Mirrors backend at apps/internal_chat/views.py + apps/internal_chat/urls.py
 * routed under /api/v1/internal-chat/master/.
 *
 * Auth: MAX initData header (master-init-data gated decorator on the
 * backend — apps/master_api/auth.require_master_init_data).
 *
 * Spec sources:
 *   - docs/design/handoffs/2026-05-19-master-admin-internal-chat-handoff.md
 *   - apps/internal_chat/models.py + views.py + tests/test_master_views.py
 *
 * §2.7 sender-display rule lives in the UI layer
 * (MasterInternalChatThreadScreen) — the API returns the raw
 * sender_admin_signed_name field; the screen decides display.
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
