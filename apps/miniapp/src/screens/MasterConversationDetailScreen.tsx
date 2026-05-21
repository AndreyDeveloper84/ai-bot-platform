/**
 * Master M6 conversation detail — read + compose + safety promote.
 *
 * Route: /master/conversations/:id
 *
 * Spec source: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M6
 * (lines 632-775). State-matrix discipline per
 * docs/design/policies/customer-first-touch-and-mini-app-states.md §7.
 *
 * Spec quote (§M6, lines 706-712):
 *
 *     «When master taps «Отправить от себя» on a draft, the message renders
 *      to the customer as «Помощник: …». Same single assistant identity.
 *      Master's authorship is recorded in attribution metadata
 *      (actor_type=master, composed_by=master_id) — visible only in audit
 *      log and to owner/admin in Conversations module»
 *
 * Spec quote (§M6, line 677):
 *
 *     «[⚠ Передать админу]   Promote to HUMAN_LOCKED safety button»
 *
 * Identity-rendering decision (this PR):
 *   Master-composed messages render with the assistant bubble visual (so
 *   the «customer sees Помощник» story isn't broken in the master's own
 *   view), plus a discreet «вы» chip MASTER-FACING ONLY that helps the
 *   master tell apart their own messages from AI-authored ones. The chip
 *   never leaves the master mini app — backend stamps attribution
 *   metadata for the audit log, the customer surface stays «Помощник».
 *
 * Backend contract (PR #485):
 *   GET    /api/v1/master/conversations/:id
 *   POST   /api/v1/master/conversations/:id/messages
 *   POST   /api/v1/master/conversations/:id/mark-read
 *   POST   /api/v1/master/conversations/:id/promote
 *
 * Bridge API (§M6 lines 767-773):
 *   - BackButton.show()  (wired to router back to M5)
 *   - enableClosingConfirmation() when compose box is dirty
 *   - HapticFeedback.impactOccurred('medium')  on «Отправить»
 *   - HapticFeedback.notificationOccurred('error')  on send failure
 *   - HapticFeedback.impactOccurred('heavy')   on «Передать админу» confirm
 *   - NO ScreenCapture.disableScreenCapture() — master never sees PII
 *
 * Out of scope this PR (documented in PR body):
 *   - Pre-send persona/forbidden-phrase/length engine (needs persona engine)
 *   - AI draft generation (UI is ready; backend always returns null)
 *   - «Пусть помощник ответит» action — stubbed toast
 *   - «Позвать Аню» / «Открыть чат с Аней» — stubbed toasts
 *   - WebSocket / SSE live updates (poll-on-mount + after-send refresh)
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../lib/api";
import {
  MASTER_COMPOSE_COUNTER_THRESHOLD,
  MASTER_COMPOSE_MAX_LENGTH,
  getConversationDetail,
  markConversationRead,
  promoteConversationToHumanLocked,
  sendMasterMessage,
  type ConversationDetailResponse,
  type ConversationMessage,
  type PromoteReasonClass,
} from "../lib/master-api";
import {
  hapticImpact,
  hapticNotify,
  hapticSelection,
  setBackButton,
  setClosingConfirmation,
  signalReady,
} from "../lib/max-sdk";
import { Snackbar } from "../components/Snackbar";
import {
  formatDaySeparator,
  formatTimeHM,
  joinClientName,
  localDayKey,
} from "../lib/masterDateFormat";

// --- Russian copy (VERBATIM from §M6) -------------------------------------

const COPY = {
  returningChip: "постоянный клиент",
  visitOrdinal: (n: number) => `${n}-й визит`,
  // §M6 line 660: «помощник готовит ответ» — only when ai_draft present
  // BEFORE the card is shown (we keep it as a quiet pre-card hint).
  draftCardTitle: "✨ Предложенный ответ",
  draftSendAsMe: "Отправить от себя",
  draftEdit: "Отредактировать",
  draftReleaseToAi: "Пусть помощник ответит",
  // Compose
  composePlaceholder: "Напишите ответ...",
  sendAria: "Отправить",
  sendingHint: "отправляется…",
  sendFailedInline:
    "Не отправилось. Попробовать ещё раз?",
  sendRetry: "Попробовать ещё раз",
  promoteButton: "⚠ Передать админу",
  // Promote sheet — verbatim from §M6 lines 750-762
  promoteTitle: "Передать админу?",
  promoteBlurb1: "Помощник остановится, Аня получит уведомление.",
  promoteBlurb2: "Используйте, если:",
  promoteHint1: "Клиент жалуется",
  promoteHint2: "Финансовый вопрос",
  promoteHint3: "Медицинский вопрос",
  promoteHint4: "Не уверены, что ответить",
  promoteReasonLabel: "Причина:",
  reasonComplaint: "жалоба",
  reasonFinancial: "финансовый вопрос",
  reasonMedical: "медицинский вопрос",
  reasonOther: "другое",
  promoteConfirm: "Передать",
  promoteCancel: "Отмена",
  // HUMAN_LOCKED banner — §M6 lines 684-703
  lockedTitle: "Этот диалог требует внимания администратора",
  lockedSinceFmt: (admin: string, since: string) =>
    `${admin} отвечает с ${since}`,
  lockedSinceFallbackFmt: (since: string) =>
    `Администратор отвечает с ${since}`,
  lockedReasonComplaint: "Жалоба",
  lockedReasonFinancial: "Финансовый вопрос",
  lockedReasonMedical: "Медицинский вопрос",
  lockedReasonOther: "Требуется админ",
  lockedCallAnya: "Позвать Аню",
  lockedHelperPrefix: "Если хотите помочь — напишите Ане в её MAX",
  lockedOpenDmAnya: "Открыть чат с Аней",
  // State matrix
  loading: "Загружаем диалог…",
  errorTitle: "Не получилось загрузить",
  retry: "Попробовать снова",
  errorOffline: "Связь пропала. Попробуйте ещё раз.",
  emptyMessages: "Здесь будут сообщения с клиентом",
  // Toasts
  toastNotForYou: "Этот диалог не для вас",
  toastSessionExpired: "Войдите как мастер заново",
  toastComingSoon: "Скоро будет",
  toastTierLocked:
    "Диалог уже передан админу — отправка недоступна",
  toastPromoted: "Передано админу",
  toastReleaseAiSoon: "Скоро будет — помощник ещё не ходит сам",
  // Master-facing only chip — never leaks to customer surface
  youChip: "вы",
  myMessageHint: "от вас · отправлено клиенту как «Помощник»",
  // Counter
  counterFmt: (remaining: number) => `Осталось ${remaining} символов`,
  // Master-author messages chevron alt (a11y)
  bubbleYouAria: "Ваше сообщение (отправлено клиенту от помощника)",
};

const LOCKED_REASON_LABELS: Record<string, string> = {
  complaint: COPY.lockedReasonComplaint,
  financial: COPY.lockedReasonFinancial,
  medical: COPY.lockedReasonMedical,
  other: COPY.lockedReasonOther,
};

const REASON_OPTIONS: { value: PromoteReasonClass; label: string }[] = [
  { value: "complaint", label: COPY.reasonComplaint },
  { value: "financial", label: COPY.reasonFinancial },
  { value: "medical", label: COPY.reasonMedical },
  { value: "other", label: COPY.reasonOther },
];

// --- view-state machine ---------------------------------------------------

type LoadedPhase = {
  kind: "ready";
  data: ConversationDetailResponse;
};

type Phase =
  | { kind: "loading" }
  | LoadedPhase
  | { kind: "error_initial"; err: unknown };

/**
 * A message that's been optimistically added to the UI but not yet
 * confirmed by the server. Sticks around until either:
 *   - server returns 201 → swapped in by message_id, removed from queue
 *   - server returns error → status flips to "failed", user can retry
 */
interface PendingMessage {
  tempId: string;
  content: string;
  startedAt: string; // ISO local for grouping
  status: "sending" | "failed";
}

// --- component ------------------------------------------------------------

export function MasterConversationDetailScreen() {
  const navigate = useNavigate();
  const params = useParams<{ id: string }>();
  const conversationId = params.id ?? "";

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [pending, setPending] = useState<PendingMessage[]>([]);
  const [composeValue, setComposeValue] = useState("");
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoteSubmitting, setPromoteSubmitting] = useState(false);
  const [toast, setToast] = useState<{ visible: boolean; message: string }>(
    { visible: false, message: "" },
  );
  const markReadFiredRef = useRef(false);
  const messageListEndRef = useRef<HTMLDivElement | null>(null);
  const composeRef = useRef<HTMLTextAreaElement | null>(null);

  // --- Bridge: BackButton + ready -------------------------------------

  useEffect(() => {
    setBackButton(true);
    signalReady();
    return () => {
      setBackButton(false);
      // Always release closing confirmation on unmount — leaving it on
      // would block other screens from closing the mini app cleanly.
      setClosingConfirmation(false);
    };
  }, []);

  // --- Bridge: closing confirmation when compose is dirty -------------

  useEffect(() => {
    const dirty = composeValue.trim().length > 0;
    setClosingConfirmation(dirty);
  }, [composeValue]);

  // --- Load --------------------------------------------------------------

  const load = useCallback(async (): Promise<void> => {
    setPhase({ kind: "loading" });
    try {
      const data = await getConversationDetail(conversationId);
      setPhase({ kind: "ready", data });
      // Defence-in-depth: keep raw PII out of the master surface. The
      // backend strips PII (spec §M6 line 773 explicitly NO screen
      // capture on master, because nothing on this screen is sensitive
      // enough to need it). If a regression sneaks a phone or full name
      // into the payload, log it but don't fail — backend is the
      // authority.
      runDetailPiiCheck(data);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 404) {
          // Permission cliff per spec §M5 line 623 (cross-master deep
          // link). Bounce to the inbox with the «не для вас» toast — we
          // pass `permissionCliff: true` so the list shows the canonical
          // copy and our local toast isn't a competing message.
          navigate("/master/conversations", {
            replace: true,
            state: { permissionCliff: true },
          });
          return;
        }
        if (err.status === 401) {
          setToast({ visible: true, message: COPY.toastSessionExpired });
          navigate("/onboarding/master", { replace: true });
          return;
        }
      }
      setPhase({ kind: "error_initial", err });
    }
  }, [conversationId, navigate]);

  useEffect(() => {
    void load();
  }, [load]);

  // --- Fire mark-read once on first successful load -------------------

  useEffect(() => {
    if (phase.kind !== "ready") return;
    if (markReadFiredRef.current) return;
    markReadFiredRef.current = true;
    void markConversationRead(conversationId).catch((err) => {
      // Non-fatal — the badge will stay until the next list refresh
      // proves the conversation read. Don't surface the failure to the
      // master; it's a passive observability action.
      console.warn("[m6] mark-read failed", err);
    });
  }, [phase, conversationId]);

  // --- Scroll to bottom on initial load / new message -----------------

  useEffect(() => {
    if (phase.kind !== "ready") return;
    // After data flush, snap to the latest message — this is a chat
    // surface, the newest message is always the most relevant. Use
    // `auto` not `smooth` on initial load to avoid jank on long lists.
    messageListEndRef.current?.scrollIntoView({ block: "end" });
  }, [phase.kind, pending.length]);

  // --- Send ------------------------------------------------------------

  const onSend = useCallback(
    async (text: string): Promise<void> => {
      const body = text.trim();
      if (!body) return;
      if (body.length > MASTER_COMPOSE_MAX_LENGTH) return;
      if (phase.kind !== "ready") return;
      const tier = phase.data.tier;
      if (tier === "human_locked") {
        // Defensive — the compose box shouldn't be visible in this tier
        // but guard against a stale state if the conversation flipped
        // while the screen was open.
        setToast({ visible: true, message: COPY.toastTierLocked });
        hapticNotify("error");
        return;
      }

      hapticImpact("medium");

      const tempId = `pending-${Date.now()}-${Math.random()
        .toString(36)
        .slice(2, 8)}`;
      const startedAt = new Date().toISOString();
      const optimistic: PendingMessage = {
        tempId,
        content: body,
        startedAt,
        status: "sending",
      };
      setPending((prev) => [...prev, optimistic]);
      setComposeValue("");

      try {
        const result = await sendMasterMessage(conversationId, body);
        // Append the confirmed message into the existing list and drop
        // the optimistic placeholder. We don't re-fetch the whole detail
        // payload here — saves a round-trip and keeps scroll position.
        setPhase((prev) => {
          if (prev.kind !== "ready") return prev;
          const newMessage: ConversationMessage = {
            message_id: result.message_id,
            role: "assistant",
            content: result.content,
            sent_at: result.sent_at || new Date().toISOString(),
            composed_by_master: true,
            composed_by_master_id: null,
          };
          return {
            kind: "ready",
            data: {
              ...prev.data,
              messages: [...prev.data.messages, newMessage],
            },
          };
        });
        setPending((prev) => prev.filter((p) => p.tempId !== tempId));
      } catch (err) {
        hapticNotify("error");
        if (err instanceof ApiError && err.status === 403) {
          // Tier flipped to HUMAN_LOCKED while we composed. Reload.
          setPending((prev) => prev.filter((p) => p.tempId !== tempId));
          setToast({ visible: true, message: COPY.toastTierLocked });
          void load();
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setPending((prev) => prev.filter((p) => p.tempId !== tempId));
          navigate("/master/conversations", {
            replace: true,
            state: { permissionCliff: true },
          });
          return;
        }
        // Generic failure — keep the optimistic row but flip status to
        // "failed" so the user sees an inline retry CTA.
        setPending((prev) =>
          prev.map((p) =>
            p.tempId === tempId ? { ...p, status: "failed" } : p,
          ),
        );
      }
    },
    [conversationId, navigate, phase, load],
  );

  const onRetryPending = useCallback(
    async (tempId: string): Promise<void> => {
      const target = pending.find((p) => p.tempId === tempId);
      if (!target) return;
      // Drop the failed row first — onSend pushes a fresh one.
      setPending((prev) => prev.filter((p) => p.tempId !== tempId));
      await onSend(target.content);
    },
    [pending, onSend],
  );

  // --- Promote ---------------------------------------------------------

  const onPromote = useCallback(
    async (reasonClass: PromoteReasonClass): Promise<void> => {
      hapticImpact("heavy");
      setPromoteSubmitting(true);
      try {
        await promoteConversationToHumanLocked(conversationId, reasonClass);
        setPromoteOpen(false);
        setToast({ visible: true, message: COPY.toastPromoted });
        // Refresh so the screen swaps to HUMAN_LOCKED layout.
        await load();
      } catch (err) {
        hapticNotify("error");
        if (err instanceof ApiError && err.status === 409) {
          // Already locked — close the sheet, refresh to pick up state.
          setPromoteOpen(false);
          await load();
          return;
        }
        // Generic — keep sheet open, surface a toast.
        const detail =
          err instanceof ApiError ? err.detail : "Не получилось";
        setToast({ visible: true, message: detail });
      } finally {
        setPromoteSubmitting(false);
      }
    },
    [conversationId, load],
  );

  // --- AI draft action handlers (currently always null backend-side) --

  const onAcceptDraft = useCallback(
    async (content: string): Promise<void> => {
      // Send the draft as the master. The customer sees «Помощник: …»
      // (per §706-712) but the audit log records master attribution.
      await onSend(content);
    },
    [onSend],
  );

  const onEditDraft = useCallback((content: string): void => {
    setComposeValue(content);
    // Defer focus to the next tick — React needs to flush the value
    // before the textarea is ready to take selection.
    window.setTimeout(() => {
      composeRef.current?.focus();
      composeRef.current?.setSelectionRange(content.length, content.length);
      composeRef.current?.scrollIntoView({ block: "center" });
    }, 0);
  }, []);

  const onReleaseDraftToAi = useCallback((): void => {
    // Not implemented backend-side — show a toast so the tap isn't a
    // black hole. Documented in PR body as deferred.
    setToast({ visible: true, message: COPY.toastReleaseAiSoon });
  }, []);

  // --- Stubs for HUMAN_LOCKED footer actions -------------------------

  const onCallAnya = useCallback((): void => {
    hapticSelection();
    setToast({ visible: true, message: COPY.toastComingSoon });
  }, []);

  const onOpenAnyaDm = useCallback((): void => {
    hapticSelection();
    setToast({ visible: true, message: COPY.toastComingSoon });
  }, []);

  // --- Render dispatch -------------------------------------------------

  if (phase.kind === "loading") {
    return (
      <div className="master-dashboard m6-screen">
        <LoadingSkeleton />
        <Snackbar
          visible={toast.visible}
          message={toast.message}
          durationMs={3500}
          onTimeout={() => setToast({ visible: false, message: "" })}
        />
      </div>
    );
  }

  if (phase.kind === "error_initial") {
    return (
      <div className="master-dashboard m6-screen">
        <ErrorInitial err={phase.err} onRetry={() => void load()} />
        <Snackbar
          visible={toast.visible}
          message={toast.message}
          durationMs={3500}
          onTimeout={() => setToast({ visible: false, message: "" })}
        />
      </div>
    );
  }

  const { data } = phase;
  const isLocked = data.tier === "human_locked";

  return (
    <div className="master-dashboard m6-screen">
      <Header
        firstName={data.client_first_name}
        lastInitial={data.client_last_initial}
        isReturning={data.is_returning_customer}
        visitCount={data.visit_count}
        locked={isLocked}
        lockedReason={data.tier_reason}
      />
      {isLocked ? (
        <LockedBanner
          adminName={data.tier_locked_by_admin_name}
          since={data.tier_locked_since}
          onCallAnya={onCallAnya}
        />
      ) : null}
      <MessageList
        messages={data.messages}
        pending={pending}
        endRef={messageListEndRef}
        onRetryPending={onRetryPending}
      />
      {!isLocked && data.ai_draft?.content ? (
        <AiDraftCard
          content={data.ai_draft.content}
          onAccept={() => void onAcceptDraft(data.ai_draft.content ?? "")}
          onEdit={() => onEditDraft(data.ai_draft.content ?? "")}
          onRelease={onReleaseDraftToAi}
        />
      ) : null}
      {isLocked ? (
        <LockedFooter onOpenAnyaDm={onOpenAnyaDm} />
      ) : (
        <ComposeBar
          composeRef={composeRef}
          value={composeValue}
          onChange={setComposeValue}
          onSend={() => void onSend(composeValue)}
          onPromote={() => {
            hapticSelection();
            setPromoteOpen(true);
          }}
          canCompose={data.permissions.can_compose}
          canPromote={data.permissions.can_promote_to_human_locked}
        />
      )}
      {promoteOpen ? (
        <PromoteSheet
          onClose={() => setPromoteOpen(false)}
          onConfirm={onPromote}
          submitting={promoteSubmitting}
        />
      ) : null}
      <Snackbar
        visible={toast.visible}
        message={toast.message}
        durationMs={3500}
        onTimeout={() => setToast({ visible: false, message: "" })}
      />
    </div>
  );
}

// --- header --------------------------------------------------------------

function Header(props: {
  firstName: string;
  lastInitial: string;
  isReturning: boolean;
  visitCount: number;
  locked: boolean;
  lockedReason: string | null;
}) {
  const name = joinClientName(props.firstName, props.lastInitial);
  return (
    <header className="m6-header">
      <h1 className="screen__title m6-header__name">{name || "Клиент"}</h1>
      {props.locked && props.lockedReason ? (
        <p className="m6-header__sub m6-header__sub--locked">
          ⚠ {LOCKED_REASON_LABELS[props.lockedReason] ?? COPY.lockedReasonOther}{" "}
          · администратор работает
        </p>
      ) : props.isReturning ? (
        <p className="m6-header__sub">
          {COPY.returningChip} · {COPY.visitOrdinal(props.visitCount)}
        </p>
      ) : null}
    </header>
  );
}

// --- locked banner / footer ---------------------------------------------

function LockedBanner(props: {
  adminName: string | null;
  since: string | null;
  onCallAnya: () => void;
}) {
  const sinceHm = props.since ? formatTimeHM(props.since) : "";
  const adminLabel = (props.adminName ?? "").trim();
  return (
    <div
      className="callout callout--danger m6-locked-banner"
      role="alert"
    >
      <p style={{ margin: 0, fontWeight: 600 }}>{COPY.lockedTitle}</p>
      {sinceHm ? (
        <p style={{ margin: "var(--s-1) 0 0" }}>
          {adminLabel
            ? COPY.lockedSinceFmt(adminLabel, sinceHm)
            : COPY.lockedSinceFallbackFmt(sinceHm)}
        </p>
      ) : null}
      <button
        type="button"
        className="btn-secondary m6-locked-banner__cta"
        onClick={props.onCallAnya}
      >
        {COPY.lockedCallAnya}
      </button>
    </div>
  );
}

function LockedFooter(props: { onOpenAnyaDm: () => void }) {
  return (
    <div className="m6-locked-footer">
      <p className="m6-locked-footer__helper">{COPY.lockedHelperPrefix}</p>
      <button
        type="button"
        className="btn-secondary"
        onClick={props.onOpenAnyaDm}
      >
        {COPY.lockedOpenDmAnya}
      </button>
    </div>
  );
}

// --- message list -------------------------------------------------------

interface BubbleProps {
  /** Stable key for React. */
  id: string;
  /** Visual side: "right" for customer messages, "left" for assistant. */
  side: "left" | "right";
  /** Whether to render the master-only «вы» chip. */
  isMine: boolean;
  content: string;
  /** Footer time chip (HH:MM). Empty string hides it. */
  timeHm: string;
  /** Optional status overlay ("отправляется…" / inline retry). */
  pendingStatus?: PendingMessage["status"];
  onRetry?: () => void;
}

function Bubble({
  side,
  isMine,
  content,
  timeHm,
  pendingStatus,
  onRetry,
}: BubbleProps) {
  const sideClass =
    side === "right" ? "m6-bubble--right" : "m6-bubble--left";
  const stateClass =
    pendingStatus === "sending"
      ? " m6-bubble--sending"
      : pendingStatus === "failed"
        ? " m6-bubble--failed"
        : "";
  return (
    <div
      className={`m6-bubble ${sideClass}${stateClass}`}
      aria-label={isMine ? COPY.bubbleYouAria : undefined}
    >
      {isMine ? (
        <span className="m6-bubble__chip" title={COPY.myMessageHint}>
          {COPY.youChip}
        </span>
      ) : null}
      <p className="m6-bubble__content">{content}</p>
      <div className="m6-bubble__footer">
        {timeHm ? (
          <span className="m6-bubble__time">{timeHm}</span>
        ) : null}
        {pendingStatus === "sending" ? (
          <span className="m6-bubble__status">{COPY.sendingHint}</span>
        ) : null}
        {pendingStatus === "failed" ? (
          <button
            type="button"
            className="m6-bubble__retry"
            onClick={onRetry}
          >
            {COPY.sendFailedInline}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** All visible rows (confirmed + pending), in chronological order. */
interface ListRow {
  type: "separator" | "bubble";
  key: string;
  separatorLabel?: string;
  bubble?: BubbleProps;
}

function buildRows(
  messages: ConversationMessage[],
  pending: PendingMessage[],
  onRetryPending: (tempId: string) => void,
): ListRow[] {
  const rows: ListRow[] = [];
  let lastDay: string | null = null;

  type Combined = {
    sortKey: string;
    bubble: BubbleProps;
  };
  const combined: Combined[] = [];

  for (const m of messages) {
    const sentAt = m.sent_at || "";
    const side: "left" | "right" = m.role === "user" ? "right" : "left";
    // Master-composed messages render with the assistant identity
    // visually (so the «customer sees Помощник» story isn't broken in
    // the master's own view) — we just add the «вы» chip to help the
    // master tell their own messages from AI-authored ones.
    const isMine = m.role === "assistant" && m.composed_by_master === true;
    combined.push({
      sortKey: sentAt || "z",
      bubble: {
        id: m.message_id,
        side,
        isMine,
        content: m.content,
        timeHm: sentAt ? formatTimeHM(sentAt) : "",
      },
    });
  }
  for (const p of pending) {
    combined.push({
      sortKey: p.startedAt,
      bubble: {
        id: p.tempId,
        // Pending master messages always render right-aligned visually
        // until confirmed — assistant-left + sending hint would be
        // confusing. After confirm, they swap into the assistant row
        // with the «вы» chip.
        side: "right",
        isMine: true,
        content: p.content,
        timeHm: formatTimeHM(p.startedAt),
        pendingStatus: p.status,
        onRetry: () => onRetryPending(p.tempId),
      },
    });
  }

  combined.sort((a, b) => a.sortKey.localeCompare(b.sortKey));

  for (const row of combined) {
    const iso = row.sortKey === "z" ? new Date().toISOString() : row.sortKey;
    const day = localDayKey(iso);
    if (day !== lastDay) {
      lastDay = day;
      rows.push({
        type: "separator",
        key: `sep-${day}`,
        separatorLabel: formatDaySeparator(iso),
      });
    }
    rows.push({
      type: "bubble",
      key: row.bubble.id,
      bubble: row.bubble,
    });
  }

  return rows;
}

function MessageList(props: {
  messages: ConversationMessage[];
  pending: PendingMessage[];
  endRef: React.RefObject<HTMLDivElement>;
  onRetryPending: (tempId: string) => void;
}) {
  const rows = useMemo(
    () => buildRows(props.messages, props.pending, props.onRetryPending),
    [props.messages, props.pending, props.onRetryPending],
  );

  if (rows.length === 0) {
    return (
      <div className="m6-empty">
        <p className="master-dashboard__empty-line">{COPY.emptyMessages}</p>
        <div ref={props.endRef} />
      </div>
    );
  }

  return (
    <div className="m6-message-list" role="log" aria-live="polite">
      {rows.map((row) => {
        if (row.type === "separator") {
          return (
            <div key={row.key} className="m6-separator">
              <span>{row.separatorLabel}</span>
            </div>
          );
        }
        const b = row.bubble!;
        return <Bubble key={row.key} {...b} />;
      })}
      <div ref={props.endRef} />
    </div>
  );
}

// --- AI draft card ------------------------------------------------------

function AiDraftCard(props: {
  content: string;
  onAccept: () => void;
  onEdit: () => void;
  onRelease: () => void;
}) {
  return (
    <section className="m6-draft-card" aria-label={COPY.draftCardTitle}>
      <div className="m6-draft-card__title">{COPY.draftCardTitle}</div>
      <p className="m6-draft-card__content">«{props.content}»</p>
      <div className="m6-draft-card__actions">
        <button
          type="button"
          className="m6-btn-primary"
          onClick={props.onAccept}
        >
          {COPY.draftSendAsMe}
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={props.onEdit}
        >
          {COPY.draftEdit}
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={props.onRelease}
        >
          {COPY.draftReleaseToAi}
        </button>
      </div>
    </section>
  );
}

// --- compose bar --------------------------------------------------------

function ComposeBar(props: {
  composeRef: React.RefObject<HTMLTextAreaElement>;
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onPromote: () => void;
  canCompose: boolean;
  canPromote: boolean;
}) {
  const trimmed = props.value.trim();
  const overLimit = props.value.length > MASTER_COMPOSE_MAX_LENGTH;
  const sendDisabled = !props.canCompose || trimmed.length === 0 || overLimit;
  const remaining = MASTER_COMPOSE_MAX_LENGTH - props.value.length;
  const showCounter =
    props.value.length >= MASTER_COMPOSE_COUNTER_THRESHOLD;

  return (
    <div className="m6-compose">
      <div className="m6-compose__row">
        <textarea
          ref={props.composeRef}
          className="m6-compose__textarea"
          value={props.value}
          onChange={(e) => props.onChange(e.currentTarget.value)}
          placeholder={COPY.composePlaceholder}
          rows={2}
          aria-label={COPY.composePlaceholder}
          maxLength={MASTER_COMPOSE_MAX_LENGTH + 50}
          disabled={!props.canCompose}
        />
        <button
          type="button"
          className="m6-compose__send"
          aria-label={COPY.sendAria}
          onClick={props.onSend}
          disabled={sendDisabled}
        >
          {/* Inline paper-plane SVG to avoid adding lucide-react. */}
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
          >
            <path
              d="M3 11L21 3L13 21L11 13L3 11Z"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
      {showCounter ? (
        <div
          className={`m6-compose__counter${overLimit ? " m6-compose__counter--over" : ""}`}
          aria-live="polite"
        >
          {COPY.counterFmt(Math.max(0, remaining))}
        </div>
      ) : null}
      {props.canPromote ? (
        <button
          type="button"
          className="m6-compose__promote"
          onClick={props.onPromote}
        >
          {COPY.promoteButton}
        </button>
      ) : null}
    </div>
  );
}

// --- promote sheet ------------------------------------------------------

function PromoteSheet(props: {
  onClose: () => void;
  onConfirm: (reason: PromoteReasonClass) => void | Promise<void>;
  submitting: boolean;
}) {
  const [reason, setReason] = useState<PromoteReasonClass>("complaint");
  return (
    <div
      className="m6-sheet-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={COPY.promoteTitle}
      onClick={(e) => {
        // Tap on the backdrop dismisses — the sheet itself stops the
        // event so taps inside don't close.
        if (e.target === e.currentTarget && !props.submitting) {
          props.onClose();
        }
      }}
    >
      <div className="m6-sheet">
        <h2 className="m6-sheet__title">{COPY.promoteTitle}</h2>
        <p className="m6-sheet__blurb">{COPY.promoteBlurb1}</p>
        <p className="m6-sheet__blurb">{COPY.promoteBlurb2}</p>
        <ul className="m6-sheet__hints">
          <li>{COPY.promoteHint1}</li>
          <li>{COPY.promoteHint2}</li>
          <li>{COPY.promoteHint3}</li>
          <li>{COPY.promoteHint4}</li>
        </ul>
        <p className="m6-sheet__reason-label">{COPY.promoteReasonLabel}</p>
        <div className="m6-sheet__options" role="radiogroup">
          {REASON_OPTIONS.map((opt) => (
            <label key={opt.value} className="m6-sheet__option">
              <input
                type="radio"
                name="reason_class"
                value={opt.value}
                checked={reason === opt.value}
                onChange={() => setReason(opt.value)}
                disabled={props.submitting}
              />
              <span>{opt.label}</span>
            </label>
          ))}
        </div>
        <div className="m6-sheet__actions">
          <button
            type="button"
            className="m6-btn-primary"
            onClick={() => void props.onConfirm(reason)}
            disabled={props.submitting}
          >
            {COPY.promoteConfirm}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={props.onClose}
            disabled={props.submitting}
          >
            {COPY.promoteCancel}
          </button>
        </div>
      </div>
    </div>
  );
}

// --- loading / error states --------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="m6-skeleton" aria-busy="true">
      <p className="master-dashboard__loading-label">{COPY.loading}</p>
      <div className="skeleton" style={{ width: "55%", height: "1.4em" }} />
      <div
        className="skeleton"
        style={{ width: "30%", height: "0.9em", marginTop: 8 }}
      />
      <div className="m6-skeleton__bubbles">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className={`m6-skeleton__bubble m6-skeleton__bubble--${
              i % 2 === 0 ? "left" : "right"
            }`}
          >
            <div
              className="skeleton"
              style={{ width: "70%", height: "1em" }}
            />
            <div
              className="skeleton"
              style={{ width: "40%", height: "1em", marginTop: 6 }}
            />
          </div>
        ))}
      </div>
      <div className="m6-skeleton__compose">
        <div
          className="skeleton"
          style={{ width: "100%", height: "44px", borderRadius: 12 }}
        />
      </div>
    </div>
  );
}

function ErrorInitial(props: { err: unknown; onRetry: () => void }) {
  const err = props.err;
  const isOffline = !(err instanceof ApiError);
  const body = isOffline
    ? COPY.errorOffline
    : err instanceof ApiError && err.status >= 500
      ? "Что-то у нас не получается прямо сейчас."
      : "Не получилось загрузить. Попробуйте снова.";
  return (
    <div className="master-dashboard__section">
      <h2 className="master-dashboard__section-title">{COPY.errorTitle}</h2>
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>{body}</p>
        <div style={{ marginTop: "var(--s-3)" }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={props.onRetry}
          >
            {COPY.retry}
          </button>
        </div>
      </div>
    </div>
  );
}

// --- defence-in-depth PII gate ------------------------------------------

const FORBIDDEN_DETAIL_KEYS = [
  "phone",
  "phone_number",
  "phone_masked",
  "email",
  "ltv",
  "ltv_rub",
  "client_last_name",
  "client_full_name",
];

function runDetailPiiCheck(data: ConversationDetailResponse): void {
  const observed = FORBIDDEN_DETAIL_KEYS.filter(
    (k) => k in (data as unknown as Record<string, unknown>),
  );
  if (observed.length > 0) {
    console.warn(
      "[m6] PII safety check — backend leaked",
      observed,
      "in",
      data.conversation_id,
    );
  }
}
