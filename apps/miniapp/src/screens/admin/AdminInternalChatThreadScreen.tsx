/**
 * Admin internal-chat — thread detail screen.
 *
 * Route: /admin/internal-chat/threads/:threadId
 *
 * Spec source: docs/design/handoffs/2026-05-19-master-admin-internal-chat-handoff.md
 *
 * Spec quote (§4.2 «Thread view (admin)», lines 318-324):
 *
 *     «Same as master's view §3.4 but: Admin sees master's full identity;
 *      Admin sees linked artifact (booking, dispute, leave-request);
 *      Action buttons depending on topic: «закрыть спор», «согласовать
 *      отпуск», etc. that resolve linked artifact AND close thread»
 *
 * Spec quote (§2.7 admin sign-as-self, line 108):
 *
 *     «Master sees «Студия» (or salon name) as the sender by default.
 *      Admin can choose to sign as themselves («— Натали»). Reduces
 *      personal targeting.»
 *
 * Key admin-side differences vs MasterInternalChatThreadScreen:
 *   - Header shows MASTER NAME + topic label (admin SEES the master's
 *     full identity per §4.2 — the §2.7 privacy rule is master→admin
 *     direction only).
 *   - Bubbles: admin messages right-aligned (= «Вы» when sent by the
 *     current admin, otherwise «Студия» or «Студия — Натали»); master
 *     bubbles left-aligned and labelled with the master's name.
 *   - Composer carries a SIGNATURE TOGGLE: «Подписаться как {first_name}».
 *     Default = unsigned «Студия». When toggle is ON we send
 *     ``sender_admin_signed_name = <admin first name>`` so the master
 *     sees «Студия — Натали» (§2.7 opt-in).
 *   - «Завершить обсуждение ›» button → confirm sheet → POST /close.
 *     After close, composer hides and the closed banner appears
 *     («Обсуждение завершено вами 15 мая 14:22» style).
 *   - NO «Эскалировать к основателю» — escalation is a master-only
 *     action per §3.6 (admin cannot escalate their own thread to
 *     founder; founder is the master's appeal channel, not the
 *     admin's).
 *
 * Backend contracts (apps/internal_chat/views.py):
 *   GET    /api/v1/internal-chat/admin/threads/<id>/
 *   POST   /api/v1/internal-chat/admin/threads/<id>/messages/   body {body, sender_admin_signed_name?}
 *   POST   /api/v1/internal-chat/admin/threads/<id>/mark-read/
 *   POST   /api/v1/internal-chat/admin/threads/<id>/close/
 *
 * Error envelope mapping:
 *   - 403 sensitive_restricted → «У этого обсуждения ограниченный
 *     доступ — обратитесь к закреплённому администратору.» banner
 *   - 409 thread_closed → «Обсуждение уже завершено» toast + reload
 *   - 404 → «Обсуждение не найдено» (deleted / wrong tenant)
 *
 * State matrix:
 *   - loading        → centered hint
 *   - error          → callout + retry
 *   - ready          → header + bubbles + composer + close-button
 *   - closed thread  → composer hidden, banner shown
 *   - send error     → inline retry, no data loss
 *   - close confirm  → confirmation sheet → POST → banner
 *   - 409 on send    → toast + reload
 *   - sensitive 403  → permission banner
 *
 * Bridge API:
 *   - BackButton.show() → /admin/internal-chat
 *   - enableClosingConfirmation() while composer has unsaved text
 *   - HapticFeedback.impactOccurred('medium') on send
 *   - HapticFeedback.impactOccurred('heavy')  on close confirm
 *   - HapticFeedback.notification('success' | 'error') on send/close result
 *   - Mark-read fires once on first successful load + after every send
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Snackbar } from "../../components/Snackbar";
import { ApiError } from "../../lib/api";
import { type MeResponse } from "../../lib/admin-api";
import {
  INTERNAL_CHAT_MESSAGE_MAX,
  TOPIC_LABELS_RU,
  type InternalChatMessage,
  type InternalChatThreadDetail,
  closeAdminThread,
  getAdminThread,
  isComposerLocked,
  markAdminThreadRead,
  senderDisplayForAdmin,
  sendAdminMessage,
} from "../../lib/internal-chat-api";
import {
  hapticImpact,
  hapticNotify,
  hapticSelection,
  onBackButton,
  setBackButton,
  setClosingConfirmation,
} from "../../lib/max-sdk";

// --- Russian copy ---------------------------------------------------------

const COPY = {
  topicPrefix: "Тема:",
  linkedPrefix: "Связано:",
  masterPrefix: "Мастер:",
  loading: "Загружаем обсуждение…",
  loadError: "Не получилось загрузить обсуждение. Попробуйте снова.",
  retry: "Попробовать снова",
  composerPlaceholder: "Напишите ответ мастеру…",
  composerSend: "Отправить",
  composerSending: "Отправляем…",
  composerSignaturePrefix: "Подписаться как",
  composerSignatureHelpDefault:
    "По умолчанию мастер увидит «Студия» — без личной подписи.",
  composerSignatureHelpOn:
    "Мастер увидит «Студия — {name}» — подпись от вашего имени.",
  closeAction: "Завершить обсуждение ›",
  closeConfirmTitle: "Завершить обсуждение?",
  closeConfirmBody:
    "После завершения мастер не сможет писать в эту тему. Если что-то всплывёт — мастер сможет открыть новое обсуждение.",
  closeConfirmYes: "Да, завершить",
  closeConfirmCancel: "Отмена",
  closeSubmitting: "Завершаем…",
  closedBannerTitle: "── Завершено ──",
  closedBannerBody: (when: string) =>
    `Обсуждение завершено вами ${when}. Мастер не может больше отправлять сюда сообщения.`,
  closedBannerBodyArchived: "Обсуждение в архиве — недоступно для сообщений.",
  composerLockedClosedHelper:
    "Если есть ещё вопрос — мастер откроет новое обсуждение.",
  composerLockedArchivedHelper:
    "Обсуждение в архиве — недоступно для новых сообщений.",
  toastSent: "Отправлено",
  toastSendError: "Не отправилось. Попробуйте ещё раз.",
  toastSendRetry: "Повторить",
  toastClosedSuccess: "Обсуждение завершено",
  toastClosedError: "Не получилось завершить. Попробуйте ещё раз.",
  toastAlreadyClosed: "Обсуждение уже завершено",
  threadNotFound:
    "Обсуждение не найдено. Возможно, его уже закрыли или удалили.",
  permissionDenied:
    "У этого обсуждения ограниченный доступ — обратитесь к закреплённому администратору.",
  goBack: "Назад к списку",
  emptyThread: "Здесь будут сообщения.",
  founderBadge: "Основатель",
  sensitiveBadge: "Конфиденциально",
  linkedBookingLabel: (id: string) => `запись (${id.slice(0, 8)})`,
  linkedReviewLabel: (id: string) => `отзыв (${id.slice(0, 8)})`,
  linkedEarningLabel: (id: string) => `доход (${id.slice(0, 8)})`,
  linkedLeaveLabel: (id: string) => `график (${id.slice(0, 8)})`,
  linkedGenericLabel: (type: string, id: string) =>
    `${type} (${id.slice(0, 8)})`,
  composerTooLong: `Длиннее ${INTERNAL_CHAT_MESSAGE_MAX} символов нельзя.`,
};

// --- helpers --------------------------------------------------------------

function formatStamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min}`;
}

function linkedArtifactLabel(
  type: string,
  id: string | null,
): string | null {
  if (!id || !type) return null;
  switch (type) {
    case "booking":
      return COPY.linkedBookingLabel(id);
    case "review":
      return COPY.linkedReviewLabel(id);
    case "earning_dispute":
      return COPY.linkedEarningLabel(id);
    case "leave_request":
      return COPY.linkedLeaveLabel(id);
    default:
      return COPY.linkedGenericLabel(type, id);
  }
}

/**
 * Derive the admin's first name from /api/v1/me.user.name — used as the
 * §2.7 sign-as-self signature when the composer toggle is ON. The
 * backend stores the signed name verbatim, so this string IS what the
 * master will see appended after «Студия — ».
 *
 * Falls back to «администратор» when /me.user.name is empty (defensive;
 * shouldn't happen for owner/admin roles in normal operation).
 */
function firstNameFromMe(me: MeResponse): string {
  const raw = (me.user?.name || "").trim();
  if (!raw) return "администратор";
  const parts = raw.split(/\s+/);
  return parts[0] || raw;
}

// --- state-machine types --------------------------------------------------

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; data: InternalChatThreadDetail }
  | { kind: "error"; err: unknown };

interface PendingMessage {
  tempId: string;
  body: string;
  signed: string; // empty = unsigned, else first name
  status: "sending" | "failed";
}

type CloseSheet =
  | { kind: "closed" }
  | { kind: "open"; submitting: boolean; err: string };

interface Props {
  me: MeResponse;
}

// --- component ------------------------------------------------------------

export function AdminInternalChatThreadScreen({ me }: Props) {
  const navigate = useNavigate();
  const { threadId } = useParams<{ threadId: string }>();
  const id = threadId ?? "";

  const isAdmin = me.is_owner || me.is_admin;
  const adminFirstName = useMemo(() => firstNameFromMe(me), [me]);
  const myBotUserId = me.user?.id ?? null;

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [composeValue, setComposeValue] = useState("");
  const [signAsSelf, setSignAsSelf] = useState(false);
  const [pending, setPending] = useState<PendingMessage[]>([]);
  const [closeSheet, setCloseSheet] = useState<CloseSheet>({ kind: "closed" });
  const [toast, setToast] = useState<{
    visible: boolean;
    message: string;
    actionLabel?: string;
    onAction?: () => void;
  }>({ visible: false, message: "" });
  const markReadFiredRef = useRef(false);
  const messageListEndRef = useRef<HTMLDivElement | null>(null);

  // --- Bridge: BackButton ---
  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => {
      hapticSelection();
      navigate("/admin/internal-chat");
    });
    return () => {
      off();
      setBackButton(false);
      setClosingConfirmation(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Closing confirmation while composer dirty ---
  useEffect(() => {
    const dirty = composeValue.trim().length > 0;
    setClosingConfirmation(dirty);
  }, [composeValue]);

  // --- Initial load ---
  const load = useCallback(async () => {
    if (!isAdmin) return;
    setPhase({ kind: "loading" });
    try {
      const res = await getAdminThread(id);
      setPhase({ kind: "ready", data: res.thread });
    } catch (err) {
      setPhase({ kind: "error", err });
    }
  }, [id, isAdmin]);

  useEffect(() => {
    void load();
  }, [load]);

  // --- Mark-read once on first successful load ---
  useEffect(() => {
    if (phase.kind !== "ready") return;
    if (markReadFiredRef.current) return;
    markReadFiredRef.current = true;
    void markAdminThreadRead(id).catch((err) => {
      // Non-fatal — list refresh will pick up the unread state.
      console.warn("[admin-internal-chat] mark-read failed", err);
    });
  }, [phase, id]);

  // --- Auto-scroll on message change ---
  useEffect(() => {
    if (phase.kind !== "ready") return;
    messageListEndRef.current?.scrollIntoView({ block: "end" });
  }, [phase, pending.length]);

  // --- Send handler ---
  const doSend = useCallback(
    async (text: string, signed: string) => {
      const tempId = `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setPending((curr) => [
        ...curr,
        { tempId, body: text, signed, status: "sending" },
      ]);
      try {
        hapticImpact("medium");
        const res = await sendAdminMessage(id, {
          body: text,
          ...(signed ? { sender_admin_signed_name: signed } : {}),
        });
        setPending((curr) => curr.filter((p) => p.tempId !== tempId));
        setPhase((curr) => {
          if (curr.kind !== "ready") return curr;
          return {
            kind: "ready",
            data: {
              ...curr.data,
              status: res.thread.status,
              last_activity_at: res.thread.last_activity_at,
              messages: [...curr.data.messages, res.message],
            },
          };
        });
        // Fire mark-read after every send so subsequent master replies
        // don't pile up unread counts on the admin nav badge.
        void markAdminThreadRead(id).catch(() => undefined);
        setToast({ visible: true, message: COPY.toastSent });
      } catch (e) {
        hapticNotify("error");
        setPending((curr) =>
          curr.map((p) =>
            p.tempId === tempId ? { ...p, status: "failed" } : p,
          ),
        );
        // 409 thread_closed — race: master / another admin closed it
        // between load and send. Surface a clear message + reload.
        if (e instanceof ApiError && e.status === 409) {
          setToast({ visible: true, message: COPY.toastAlreadyClosed });
          void load();
          return;
        }
        const detail =
          e instanceof ApiError
            ? e.detail || COPY.toastSendError
            : COPY.toastSendError;
        setToast({
          visible: true,
          message: detail,
          actionLabel: COPY.toastSendRetry,
          onAction: () => {
            setPending((curr) => curr.filter((p) => p.tempId !== tempId));
            void doSend(text, signed);
          },
        });
      }
    },
    [id, load],
  );

  const onSend = useCallback(() => {
    const text = composeValue.trim();
    if (!text) return;
    if (text.length > INTERNAL_CHAT_MESSAGE_MAX) {
      hapticNotify("error");
      setToast({ visible: true, message: COPY.composerTooLong });
      return;
    }
    if (phase.kind !== "ready") return;
    if (isComposerLocked(phase.data.status)) return;
    const signed = signAsSelf ? adminFirstName : "";
    setComposeValue("");
    void doSend(text, signed);
  }, [composeValue, signAsSelf, adminFirstName, doSend, phase]);

  // --- Close handlers ---
  const openCloseSheet = useCallback(() => {
    if (phase.kind !== "ready") return;
    hapticImpact("heavy");
    setCloseSheet({ kind: "open", submitting: false, err: "" });
  }, [phase]);

  const cancelCloseSheet = useCallback(() => {
    hapticSelection();
    setCloseSheet({ kind: "closed" });
  }, []);

  const submitClose = useCallback(async () => {
    if (closeSheet.kind !== "open") return;
    setCloseSheet({ ...closeSheet, submitting: true, err: "" });
    try {
      const res = await closeAdminThread(id);
      hapticNotify("success");
      setCloseSheet({ kind: "closed" });
      setToast({ visible: true, message: COPY.toastClosedSuccess });
      setPhase((curr) => {
        if (curr.kind !== "ready") return curr;
        return {
          kind: "ready",
          data: {
            ...curr.data,
            status: res.thread.status,
            resolved_at: res.thread.resolved_at,
            last_activity_at: res.thread.last_activity_at,
          },
        };
      });
    } catch (e) {
      hapticNotify("error");
      const detail =
        e instanceof ApiError
          ? e.detail || COPY.toastClosedError
          : COPY.toastClosedError;
      setCloseSheet({ ...closeSheet, submitting: false, err: detail });
    }
  }, [closeSheet, id]);

  // --- Render guards ---
  if (!isAdmin) {
    return (
      <div className="screen internal-chat-thread">
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>
            Эта страница только для администраторов.
          </p>
          <div style={{ marginTop: "var(--s-3)" }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate("/admin/team")}
            >
              {COPY.goBack}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (phase.kind === "loading") {
    return (
      <div className="screen internal-chat-thread">
        <p className="internal-chat-thread__loading">{COPY.loading}</p>
      </div>
    );
  }
  if (phase.kind === "error") {
    const apiErr = phase.err instanceof ApiError ? phase.err : null;
    const notFound = apiErr?.status === 404;
    const forbidden = apiErr?.status === 403;
    let body = COPY.loadError;
    if (notFound) body = COPY.threadNotFound;
    else if (forbidden) body = COPY.permissionDenied;
    return (
      <div className="screen internal-chat-thread">
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>{body}</p>
          <div
            style={{
              marginTop: "var(--s-3)",
              display: "flex",
              gap: "var(--s-2)",
            }}
          >
            {!notFound && !forbidden ? (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void load()}
              >
                {COPY.retry}
              </button>
            ) : null}
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate("/admin/internal-chat")}
            >
              {COPY.goBack}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const thread = phase.data;
  const masterName = thread.master?.name?.trim() || "Мастер";
  const topicLabel = TOPIC_LABELS_RU[thread.topic] ?? thread.topic_display;
  const linkedLabel = linkedArtifactLabel(
    thread.linked_artifact_type,
    thread.linked_artifact_id,
  );
  const composerLocked = isComposerLocked(thread.status);
  const isArchived = thread.status === "auto_closed_inactive";
  const isResolved = thread.status === "resolved";
  const closedAtStamp =
    isResolved && thread.resolved_at ? formatStamp(thread.resolved_at) : "";

  return (
    <div className="screen internal-chat-thread">
      <header className="internal-chat-thread__header">
        <h1 className="screen__title internal-chat-thread__title">
          {masterName} — {topicLabel}
        </h1>
        <div className="internal-chat-thread__meta">
          <div>
            {COPY.masterPrefix} {masterName}
          </div>
          <div>
            {COPY.topicPrefix} {topicLabel}
            {thread.subject ? ` · ${thread.subject}` : ""}
          </div>
          {linkedLabel ? (
            <div>
              {COPY.linkedPrefix} {linkedLabel}
            </div>
          ) : null}
          {thread.is_sensitive ? (
            <div className="m-card__chip m-card__chip--warning">
              {COPY.sensitiveBadge}
            </div>
          ) : null}
          {thread.founder_added_at ? (
            <div className="m-card__chip m-card__chip--warning">
              {COPY.founderBadge}
            </div>
          ) : null}
        </div>
      </header>

      <MessageList
        messages={thread.messages}
        masterName={masterName}
        meBotUserId={myBotUserId}
        pending={pending}
        onRetryPending={(p) => {
          setPending((curr) => curr.filter((x) => x.tempId !== p.tempId));
          void doSend(p.body, p.signed);
        }}
        endRef={messageListEndRef}
      />

      {composerLocked ? (
        <ClosedBanner archived={isArchived} closedAtStamp={closedAtStamp} />
      ) : null}

      <div className="internal-chat-thread__footer">
        {!composerLocked ? (
          <>
            <Composer
              value={composeValue}
              onChange={setComposeValue}
              onSend={onSend}
              disabled={composerLocked}
              signAsSelf={signAsSelf}
              onToggleSign={() => {
                hapticSelection();
                setSignAsSelf((v) => !v);
              }}
              adminFirstName={adminFirstName}
            />
            <button
              type="button"
              className="btn-secondary internal-chat-thread__close-action"
              onClick={openCloseSheet}
            >
              {COPY.closeAction}
            </button>
          </>
        ) : (
          <p className="internal-chat-thread__locked-helper" role="status">
            {isArchived
              ? COPY.composerLockedArchivedHelper
              : COPY.composerLockedClosedHelper}
          </p>
        )}
      </div>

      {closeSheet.kind === "open" ? (
        <CloseConfirmSheet
          state={closeSheet}
          onCancel={cancelCloseSheet}
          onSubmit={() => void submitClose()}
        />
      ) : null}

      <Snackbar
        visible={toast.visible}
        message={toast.message}
        actionLabel={toast.actionLabel}
        onAction={toast.onAction}
        durationMs={3500}
        onTimeout={() => setToast({ visible: false, message: "" })}
        onDismiss={() => setToast({ visible: false, message: "" })}
      />
    </div>
  );
}

// --- subcomponents --------------------------------------------------------

function MessageList({
  messages,
  masterName,
  meBotUserId,
  pending,
  onRetryPending,
  endRef,
}: {
  messages: InternalChatMessage[];
  masterName: string;
  meBotUserId: string | null;
  pending: PendingMessage[];
  onRetryPending: (p: PendingMessage) => void;
  endRef: React.RefObject<HTMLDivElement>;
}) {
  if (messages.length === 0 && pending.length === 0) {
    return (
      <div className="internal-chat-thread__empty" role="status">
        <p style={{ margin: 0 }}>{COPY.emptyThread}</p>
      </div>
    );
  }
  return (
    <div className="internal-chat-thread__messages" role="log">
      {messages.map((m) => (
        <Bubble
          key={m.id}
          message={m}
          masterName={masterName}
          meBotUserId={meBotUserId}
        />
      ))}
      {pending.map((p) => (
        <PendingBubble
          key={p.tempId}
          pending={p}
          onRetry={() => onRetryPending(p)}
        />
      ))}
      <div ref={endRef} />
    </div>
  );
}

function Bubble({
  message,
  masterName,
  meBotUserId,
}: {
  message: InternalChatMessage;
  masterName: string;
  meBotUserId: string | null;
}) {
  const isAdminSide =
    message.sender_role === "admin" || message.sender_role === "founder";
  const isSystem =
    message.is_system_message || message.sender_role === "system";
  const display = senderDisplayForAdmin(message, masterName, meBotUserId);
  const stamp = formatStamp(message.sent_at);
  if (isSystem) {
    return (
      <div
        className="internal-chat-bubble internal-chat-bubble--system"
        role="note"
      >
        <em>{message.body}</em>
        <div className="internal-chat-bubble__stamp">{stamp}</div>
      </div>
    );
  }
  // Admin perspective bubble alignment:
  //   - Admin / founder messages (= "us") → right-aligned (master class
  //     in the master view IS «right-aligned own»; reuse here).
  //   - Master messages → left-aligned with master name label.
  return (
    <div
      className={
        isAdminSide
          ? "internal-chat-bubble internal-chat-bubble--master"
          : "internal-chat-bubble internal-chat-bubble--admin"
      }
    >
      <div className="internal-chat-bubble__header">
        <span className="internal-chat-bubble__sender">{display}</span>
        <span className="internal-chat-bubble__stamp">{stamp}</span>
      </div>
      <div className="internal-chat-bubble__body">{message.body}</div>
    </div>
  );
}

function PendingBubble({
  pending,
  onRetry,
}: {
  pending: PendingMessage;
  onRetry: () => void;
}) {
  return (
    <div
      className={
        pending.status === "failed"
          ? "internal-chat-bubble internal-chat-bubble--master internal-chat-bubble--failed"
          : "internal-chat-bubble internal-chat-bubble--master internal-chat-bubble--pending"
      }
    >
      <div className="internal-chat-bubble__header">
        <span className="internal-chat-bubble__sender">
          {pending.signed ? `Студия — ${pending.signed}` : "Студия"}
        </span>
        <span className="internal-chat-bubble__stamp">
          {pending.status === "failed" ? COPY.toastSendError : "отправляется…"}
        </span>
      </div>
      <div className="internal-chat-bubble__body">{pending.body}</div>
      {pending.status === "failed" ? (
        <button
          type="button"
          className="btn-secondary internal-chat-bubble__retry"
          onClick={onRetry}
        >
          {COPY.toastSendRetry}
        </button>
      ) : null}
    </div>
  );
}

function Composer({
  value,
  onChange,
  onSend,
  disabled,
  signAsSelf,
  onToggleSign,
  adminFirstName,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled: boolean;
  signAsSelf: boolean;
  onToggleSign: () => void;
  adminFirstName: string;
}) {
  const remaining = INTERNAL_CHAT_MESSAGE_MAX - value.length;
  const overLimit = remaining < 0;
  const empty = value.trim().length === 0;
  const signatureHelper = signAsSelf
    ? COPY.composerSignatureHelpOn.replace("{name}", adminFirstName)
    : COPY.composerSignatureHelpDefault;
  return (
    <div className="internal-chat-thread__composer">
      <textarea
        className="internal-chat-thread__textarea"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={COPY.composerPlaceholder}
        rows={3}
        maxLength={INTERNAL_CHAT_MESSAGE_MAX + 50}
        disabled={disabled}
        aria-label={COPY.composerPlaceholder}
      />
      <label
        className="internal-chat-thread__sign-toggle"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--s-2)",
          margin: "var(--s-2) 0",
        }}
      >
        <input
          type="checkbox"
          checked={signAsSelf}
          onChange={onToggleSign}
          disabled={disabled}
          aria-label={`${COPY.composerSignaturePrefix} ${adminFirstName}`}
        />
        <span>
          {COPY.composerSignaturePrefix} {adminFirstName}
        </span>
      </label>
      <div
        className="internal-chat-thread__sign-helper"
        role="note"
        style={{
          fontSize: "0.85em",
          color: "var(--c-text-secondary)",
          marginBottom: "var(--s-2)",
        }}
      >
        {signatureHelper}
      </div>
      <div className="internal-chat-thread__composer-row">
        <span
          className={
            overLimit
              ? "internal-chat-thread__counter internal-chat-thread__counter--bad"
              : "internal-chat-thread__counter"
          }
          aria-live="polite"
        >
          {value.length} / {INTERNAL_CHAT_MESSAGE_MAX}
        </span>
        <button
          type="button"
          className="btn-primary"
          onClick={onSend}
          disabled={disabled || empty || overLimit}
        >
          {COPY.composerSend}
        </button>
      </div>
    </div>
  );
}

function ClosedBanner({
  archived,
  closedAtStamp,
}: {
  archived: boolean;
  closedAtStamp: string;
}) {
  return (
    <div
      className="callout internal-chat-thread__closed-banner"
      role="status"
    >
      <strong>{COPY.closedBannerTitle}</strong>
      <p style={{ margin: 0 }}>
        {archived
          ? COPY.closedBannerBodyArchived
          : COPY.closedBannerBody(closedAtStamp || "сейчас")}
      </p>
    </div>
  );
}

function CloseConfirmSheet({
  state,
  onCancel,
  onSubmit,
}: {
  state: { kind: "open"; submitting: boolean; err: string };
  onCancel: () => void;
  onSubmit: () => void;
}) {
  return (
    <div
      className="internal-chat-sheet"
      role="dialog"
      aria-modal="true"
      aria-label={COPY.closeConfirmTitle}
    >
      <div className="internal-chat-sheet__card">
        <h2 className="internal-chat-sheet__title">
          {COPY.closeConfirmTitle}
        </h2>
        <p className="internal-chat-sheet__helper">{COPY.closeConfirmBody}</p>
        {state.err ? (
          <p className="internal-chat-sheet__error" role="alert">
            {state.err}
          </p>
        ) : null}
        <div className="internal-chat-sheet__actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
            disabled={state.submitting}
          >
            {COPY.closeConfirmCancel}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={onSubmit}
            disabled={state.submitting}
          >
            {state.submitting ? COPY.closeSubmitting : COPY.closeConfirmYes}
          </button>
        </div>
      </div>
    </div>
  );
}
