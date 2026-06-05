/**
 * Master internal-chat — thread detail («Со студией → разговор»).
 *
 * Route: /master/internal-chat/threads/:threadId
 *
 * Spec source: docs/design/handoffs/2026-05-19-master-admin-internal-chat-handoff.md
 *
 * Spec quote (§3.4 «Thread view», lines 208-237):
 *
 *     «← 💰 Спор по доходу 17 мая … Тема: спор по доходу / Связано:
 *      запись 17 мая (маникюр Мария И.) … ── Вы, 19 мая 10:00 ── …
 *      ── Студия, 19 мая 13:30 ── … — Натали … ── Студия, 19 мая 14:01 ──
 *      / ✓ Спор закрыт … ── Закрыто ── / Тема исчерпана. Если что-то ещё —
 *      откройте новую. … [📎] [Отправить] (нельзя — закрыто)»
 *
 * Spec quote (§3.6 «Escalate to founder», lines 247-260):
 *
 *     «[Эскалировать к основателю] … Эскалация — это серьёзный шаг.
 *      {{founder}} увидит весь разговор + ваше сообщение. Будет
 *      реагировать в течение 7 рабочих дней. Эскалируем? [Да, эскалирую]
 *      [Нет, обсудим со студией ещё]»
 *
 * §2.7 sender-display: master ALWAYS sees «Студия» as default;
 * admin-signed messages render as «Студия — Натали». Individual admin
 * identity hidden unless explicitly signed by admin.
 *
 * §2.11 no customer name leak: this screen does NOT auto-render the
 * linked artifact's customer name. The «Связано: …» line shows the
 * artifact type + id only — no customer-name lookup. If the master
 * himself wrote the customer's full name in a message body, we render
 * it as-is (master/admin policy enforcement, not a leak — the master
 * already knows the customer).
 *
 * Backend contracts:
 *   GET  /api/v1/internal-chat/master/threads/:id/
 *   POST /api/v1/internal-chat/master/threads/:id/messages/
 *   POST /api/v1/internal-chat/master/threads/:id/mark-read/
 *   POST /api/v1/internal-chat/master/threads/:id/escalate-to-founder/
 *
 * State matrix:
 *   - loading        → centered loading hint
 *   - error          → callout + retry
 *   - ready          → header + message bubbles + composer
 *   - closed thread  → composer hidden, banner «Обсуждение завершено»
 *   - archived       → read-only display, no composer
 *   - send error     → inline retry, no data loss
 *   - escalate sheet → confirm dialog → POST → success toast
 *   - escalate error → inline retry
 *
 * Bridge API:
 *   - BackButton.show() → /master/internal-chat
 *   - enableClosingConfirmation() while composer or escalate-reason has unsaved text
 *   - HapticFeedback.impactOccurred('medium') on send
 *   - HapticFeedback.notificationOccurred('success') on escalate confirm
 *   - HapticFeedback.notificationOccurred('error') on send/escalate error
 *   - Mark-read fires once on first successful load + on every accepted send
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Snackbar } from "../components/Snackbar";
import { ApiError } from "../lib/api";
import {
  INTERNAL_CHAT_ESCALATION_REASON_MAX,
  INTERNAL_CHAT_MESSAGE_MAX,
  TOPIC_LABELS_RU,
  type InternalChatMessage,
  type InternalChatThreadDetail,
  escalateMasterInternalThreadToFounder,
  getMasterThread,
  isComposerLocked,
  markMasterInternalThreadRead,
  senderDisplayForMaster,
  sendMasterInternalMessage,
} from "../lib/internal-chat-api";
import {
  hapticImpact,
  hapticNotify,
  hapticSelection,
  onBackButton,
  setBackButton,
  setClosingConfirmation,
} from "../lib/max-sdk";

// --- Russian copy (verbatim from §3.4 / §3.6) ----------------------------

const COPY = {
  topicPrefix: "Тема:",
  linkedPrefix: "Связано:",
  loading: "Загружаем обсуждение…",
  loadError: "Не получилось загрузить обсуждение. Попробуйте снова.",
  retry: "Попробовать снова",
  composerPlaceholder: "Напишите сообщение…",
  composerSend: "Отправить",
  composerSending: "Отправляем…",
  composerLockedClosed: "Обсуждение завершено",
  composerLockedHelper: "Если есть ещё вопрос — откройте новое обсуждение.",
  composerLockedArchived:
    "Обсуждение в архиве — недоступно для новых сообщений.",
  closedBannerTitle: "── Закрыто ──",
  closedBannerBody:
    "Тема исчерпана. Если что-то ещё — откройте новую.",
  escalateButton: "⚠ Эскалировать к основателю",
  escalateTitle: "Эскалация — это серьёзный шаг",
  escalateBody:
    "Основатель увидит весь разговор и ваше сообщение. Будет реагировать в течение 7 рабочих дней.",
  escalateReasonLabel: "Что хотите сказать основателю? (необязательно)",
  escalateReasonPlaceholder: "Кратко опишите ситуацию…",
  escalateQuestion: "Эскалируем?",
  escalateConfirm: "Да, эскалирую",
  escalateCancel: "Нет, обсудим со студией ещё",
  escalateSubmitting: "Эскалируем…",
  escalateSuccessToast: "Передано основателю",
  escalateErrorToast: "Не получилось эскалировать. Попробуйте ещё раз.",
  toastSent: "Отправлено",
  toastSendError: "Не отправилось. Попробуйте ещё раз.",
  toastSendRetry: "Повторить",
  emptyThread: "Здесь будут сообщения.",
  systemMessageLabel: "Система",
  founderBadge: "Основатель",
  linkedBookingLabel: (id: string) => `запись (${id.slice(0, 8)})`,
  linkedReviewLabel: (id: string) => `отзыв (${id.slice(0, 8)})`,
  linkedEarningLabel: (id: string) => `доход (${id.slice(0, 8)})`,
  linkedLeaveLabel: (id: string) => `график (${id.slice(0, 8)})`,
  linkedGenericLabel: (type: string, id: string) =>
    `${type} (${id.slice(0, 8)})`,
  composerTooLong: `Длиннее ${INTERNAL_CHAT_MESSAGE_MAX} символов нельзя.`,
  reasonTooLong: `Длиннее ${INTERNAL_CHAT_ESCALATION_REASON_MAX} символов нельзя.`,
  threadNotFound: "Обсуждение не найдено. Возможно, его уже закрыли.",
  goBack: "Назад к списку",
  alreadyEscalated: "Обсуждение уже передано основателю.",
  sensitiveBadge: "Конфиденциально",
};

// --- helpers --------------------------------------------------------------

function formatStamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  // «19 мая 13:30» style — but without locale dep we keep it ISO-ish:
  // YYYY-MM-DD HH:MM in local time.
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

// --- state-machine types --------------------------------------------------

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; data: InternalChatThreadDetail }
  | { kind: "error"; err: unknown };

interface PendingMessage {
  tempId: string;
  body: string;
  status: "sending" | "failed";
}

type EscalateSheet =
  | { kind: "closed" }
  | { kind: "open"; reason: string; submitting: boolean; err: string };

// --- component ------------------------------------------------------------

export function MasterInternalChatThreadScreen() {
  const navigate = useNavigate();
  const { threadId } = useParams<{ threadId: string }>();
  const id = threadId ?? "";

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [composeValue, setComposeValue] = useState("");
  const [pending, setPending] = useState<PendingMessage[]>([]);
  const [escalateSheet, setEscalateSheet] = useState<EscalateSheet>({
    kind: "closed",
  });
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
      navigate("/master/internal-chat");
    });
    return () => {
      off();
      setBackButton(false);
      setClosingConfirmation(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Closing confirmation while composer / escalate-reason dirty ---
  useEffect(() => {
    const composerDirty = composeValue.trim().length > 0;
    const escalateDirty =
      escalateSheet.kind === "open" && escalateSheet.reason.trim().length > 0;
    setClosingConfirmation(composerDirty || escalateDirty);
  }, [composeValue, escalateSheet]);

  // --- Initial load ---
  const load = useCallback(async () => {
    setPhase({ kind: "loading" });
    try {
      const res = await getMasterThread(id);
      setPhase({ kind: "ready", data: res.thread });
    } catch (err) {
      setPhase({ kind: "error", err });
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // --- Mark-read once on first successful load ---
  useEffect(() => {
    if (phase.kind !== "ready") return;
    if (markReadFiredRef.current) return;
    markReadFiredRef.current = true;
    void markMasterInternalThreadRead(id).catch((err) => {
      // Non-fatal — list view will pick up the unread state on next
      // refresh. Don't surface the failure to the master.
      console.warn("[internal-chat] mark-read failed", err);
    });
  }, [phase, id]);

  // --- Auto-scroll on message change ---
  useEffect(() => {
    if (phase.kind !== "ready") return;
    messageListEndRef.current?.scrollIntoView({ block: "end" });
  }, [phase, pending.length]);

  // --- Send handler ---
  const doSend = useCallback(
    async (text: string) => {
      const tempId = `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setPending((curr) => [
        ...curr,
        { tempId, body: text, status: "sending" },
      ]);
      try {
        hapticImpact("medium");
        const res = await sendMasterInternalMessage(id, text);
        // Merge: drop the temp row, append the real message, refresh thread summary.
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
        // Fire mark-read after every send so subsequent admin replies
        // don't pile up unread counts.
        void markMasterInternalThreadRead(id).catch(() => undefined);
        setToast({ visible: true, message: COPY.toastSent });
      } catch (e) {
        hapticNotify("error");
        setPending((curr) =>
          curr.map((p) =>
            p.tempId === tempId ? { ...p, status: "failed" } : p,
          ),
        );
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
            void doSend(text);
          },
        });
      }
    },
    [id],
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
    setComposeValue("");
    void doSend(text);
  }, [composeValue, doSend, phase]);

  // --- Escalate handlers ---
  const openEscalate = useCallback(() => {
    if (phase.kind !== "ready") return;
    if (phase.data.founder_added_at) {
      setToast({ visible: true, message: COPY.alreadyEscalated });
      return;
    }
    hapticImpact("light");
    setEscalateSheet({ kind: "open", reason: "", submitting: false, err: "" });
  }, [phase]);

  const cancelEscalate = useCallback(() => {
    hapticSelection();
    setEscalateSheet({ kind: "closed" });
  }, []);

  const updateEscalateReason = useCallback((value: string) => {
    setEscalateSheet((curr) => {
      if (curr.kind !== "open") return curr;
      return { ...curr, reason: value, err: "" };
    });
  }, []);

  const submitEscalate = useCallback(async () => {
    if (escalateSheet.kind !== "open") return;
    const reason = escalateSheet.reason.trim();
    if (reason.length > INTERNAL_CHAT_ESCALATION_REASON_MAX) {
      setEscalateSheet({ ...escalateSheet, err: COPY.reasonTooLong });
      hapticNotify("error");
      return;
    }
    setEscalateSheet({ ...escalateSheet, submitting: true, err: "" });
    try {
      const res = await escalateMasterInternalThreadToFounder(id, reason);
      hapticNotify("success");
      setEscalateSheet({ kind: "closed" });
      setToast({ visible: true, message: COPY.escalateSuccessToast });
      // Reflect new status / founder_added_at in the cached payload.
      setPhase((curr) => {
        if (curr.kind !== "ready") return curr;
        return {
          kind: "ready",
          data: {
            ...curr.data,
            status: res.thread.status,
            founder_added_at: res.thread.founder_added_at,
            last_activity_at: res.thread.last_activity_at,
          },
        };
      });
    } catch (e) {
      hapticNotify("error");
      const detail =
        e instanceof ApiError
          ? e.detail || COPY.escalateErrorToast
          : COPY.escalateErrorToast;
      setEscalateSheet({ ...escalateSheet, submitting: false, err: detail });
    }
  }, [escalateSheet, id]);

  // --- Render branches ---
  if (phase.kind === "loading") {
    return (
      <div className="screen internal-chat-thread">
        <p className="internal-chat-thread__loading">{COPY.loading}</p>
      </div>
    );
  }
  if (phase.kind === "error") {
    const not_found =
      phase.err instanceof ApiError && phase.err.status === 404;
    return (
      <div className="screen internal-chat-thread">
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>
            {not_found ? COPY.threadNotFound : COPY.loadError}
          </p>
          <div
            style={{
              marginTop: "var(--s-3)",
              display: "flex",
              gap: "var(--s-2)",
            }}
          >
            {!not_found ? (
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
              onClick={() => navigate("/master/internal-chat")}
            >
              {COPY.goBack}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const thread = phase.data;
  const topicLabel = TOPIC_LABELS_RU[thread.topic] ?? thread.topic_display;
  const linkedLabel = linkedArtifactLabel(
    thread.linked_artifact_type,
    thread.linked_artifact_id,
  );
  const composerLocked = isComposerLocked(thread.status);
  const isArchived = thread.status === "auto_closed_inactive";
  const canEscalate = !thread.founder_added_at && !composerLocked;

  return (
    <div className="screen internal-chat-thread">
      <header className="internal-chat-thread__header">
        <h1 className="screen__title internal-chat-thread__title">
          {topicLabel}
          {thread.subject ? `: ${thread.subject}` : ""}
        </h1>
        <div className="internal-chat-thread__meta">
          <div>
            {COPY.topicPrefix} {topicLabel}
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
        pending={pending}
        onRetryPending={(p) => {
          setPending((curr) => curr.filter((x) => x.tempId !== p.tempId));
          void doSend(p.body);
        }}
        endRef={messageListEndRef}
      />

      {composerLocked ? (
        <ClosedBanner archived={isArchived} />
      ) : null}

      <div className="internal-chat-thread__footer">
        {canEscalate ? (
          <button
            type="button"
            className="btn-secondary internal-chat-thread__escalate"
            onClick={openEscalate}
          >
            {COPY.escalateButton}
          </button>
        ) : null}

        {!composerLocked ? (
          <Composer
            value={composeValue}
            onChange={setComposeValue}
            onSend={onSend}
            disabled={composerLocked}
          />
        ) : (
          <p className="internal-chat-thread__locked-helper" role="status">
            {isArchived
              ? COPY.composerLockedArchived
              : COPY.composerLockedHelper}
          </p>
        )}
      </div>

      {escalateSheet.kind === "open" ? (
        <EscalateSheet
          state={escalateSheet}
          onChange={updateEscalateReason}
          onCancel={cancelEscalate}
          onSubmit={() => void submitEscalate()}
        />
      ) : null}

      <Snackbar
        visible={toast.visible}
        message={toast.message}
        actionLabel={toast.actionLabel}
        onAction={toast.onAction}
        durationMs={3500}
        onTimeout={() =>
          setToast({ visible: false, message: "" })
        }
        onDismiss={() =>
          setToast({ visible: false, message: "" })
        }
      />
    </div>
  );
}

// --- subcomponents --------------------------------------------------------

function MessageList({
  messages,
  pending,
  onRetryPending,
  endRef,
}: {
  messages: InternalChatMessage[];
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
        <Bubble key={m.id} message={m} />
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

function Bubble({ message }: { message: InternalChatMessage }) {
  const isMaster = message.sender_role === "master";
  const isSystem = message.is_system_message || message.sender_role === "system";
  const display = senderDisplayForMaster(message);
  const stamp = formatStamp(message.sent_at);
  if (isSystem) {
    return (
      <div className="internal-chat-bubble internal-chat-bubble--system" role="note">
        <em>{message.body}</em>
        <div className="internal-chat-bubble__stamp">{stamp}</div>
      </div>
    );
  }
  return (
    <div
      className={
        isMaster
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
        <span className="internal-chat-bubble__sender">Вы</span>
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
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled: boolean;
}) {
  const remaining = INTERNAL_CHAT_MESSAGE_MAX - value.length;
  const overLimit = remaining < 0;
  const empty = value.trim().length === 0;
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

function ClosedBanner({ archived }: { archived: boolean }) {
  return (
    <div className="callout internal-chat-thread__closed-banner" role="status">
      <strong>
        {archived ? COPY.composerLockedArchived : COPY.closedBannerTitle}
      </strong>
      <p style={{ margin: 0 }}>
        {archived ? COPY.composerLockedHelper : COPY.closedBannerBody}
      </p>
    </div>
  );
}

function EscalateSheet({
  state,
  onChange,
  onCancel,
  onSubmit,
}: {
  state: { kind: "open"; reason: string; submitting: boolean; err: string };
  onChange: (v: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const overLimit = state.reason.length > INTERNAL_CHAT_ESCALATION_REASON_MAX;
  return (
    <div
      className="internal-chat-sheet"
      role="dialog"
      aria-modal="true"
      aria-label={COPY.escalateTitle}
    >
      <div className="internal-chat-sheet__card">
        <h2 className="internal-chat-sheet__title">{COPY.escalateTitle}</h2>
        <p className="internal-chat-sheet__helper">{COPY.escalateBody}</p>
        <label className="internal-chat-sheet__label">
          {COPY.escalateReasonLabel}
        </label>
        <textarea
          className="internal-chat-sheet__textarea"
          value={state.reason}
          onChange={(e) => onChange(e.target.value)}
          placeholder={COPY.escalateReasonPlaceholder}
          rows={4}
          maxLength={INTERNAL_CHAT_ESCALATION_REASON_MAX + 50}
          disabled={state.submitting}
          aria-label={COPY.escalateReasonLabel}
        />
        <div
          className={
            overLimit
              ? "internal-chat-sheet__counter internal-chat-sheet__counter--bad"
              : "internal-chat-sheet__counter"
          }
          aria-live="polite"
        >
          {state.reason.length} / {INTERNAL_CHAT_ESCALATION_REASON_MAX}
        </div>
        {state.err ? (
          <p className="internal-chat-sheet__error" role="alert">
            {state.err}
          </p>
        ) : null}
        <p className="internal-chat-sheet__question">
          <strong>{COPY.escalateQuestion}</strong>
        </p>
        <div className="internal-chat-sheet__actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
            disabled={state.submitting}
          >
            {COPY.escalateCancel}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={onSubmit}
            disabled={state.submitting || overLimit}
          >
            {state.submitting ? COPY.escalateSubmitting : COPY.escalateConfirm}
          </button>
        </div>
      </div>
    </div>
  );
}
