/**
 * Master internal-chat — list screen («Со студией»).
 *
 * Route: /master/internal-chat
 *
 * Spec source: docs/design/handoffs/2026-05-19-master-admin-internal-chat-handoff.md
 *
 * Spec quote (§3.2 «Tab home», lines 144-169):
 *
 *     «🏛 Со студией … ── Открытые ── … 💰 Спор по доходу за маникюр 17 мая
 *      / Студия ответила 3 часа назад / [Открыть] … ── Решённые (за месяц) ──
 *      … 🛌 Отпуск 10-24 июня — согласовано / Завершено 5 дней назад
 *      / [Открыть] … ── Что-то новое? ── / [✏ Написать студии]»
 *
 * Spec quote (§3.3 «Написать студии», lines 174-189):
 *
 *     «← Что обсудим? … Выберите тему — это поможет быстрее ответить:
 *      ⦿ 💰 Что-то по доходу или комиссии … ◯ ❓ Что-то другое / [Дальше]»
 *
 * Surface decision (documented in PR body): the master surface uses a
 * SEPARATE channel from customer conversations (which live under
 * /master/conversations and render in the bottom-nav «💬 Диалоги»
 * tab). «Со студией» is accessed via a section row in the M4 profile
 * («Со студией ›») rather than a 5th bottom-nav tab — bottom-nav real
 * estate is already at the 4-tab MAX recommendation cap. The M4
 * profile «Написать Карине» CTA is wired to deep-link
 * /master/internal-chat/new?topic=general.
 *
 * Backend contract: GET/POST /api/v1/internal-chat/master/threads/
 *   (apps/internal_chat/views.py::master_threads_collection)
 *
 * State matrix:
 *   - loading      → 3 skeleton cards
 *   - ready        → grouped «Активные / Решённые / Архив» sections per filter
 *   - empty (filter-aware copy)
 *   - error        → callout + retry
 *   - newThread    → topic-picker sheet + first-message editor
 *   - escalation   → handled in detail screen
 *
 * Bridge API:
 *   - BackButton.show() → /master/profile (entry point)
 *   - enableClosingConfirmation() while new-thread editor has text
 *   - HapticFeedback.selection() on chip taps, topic picks, thread taps
 *   - HapticFeedback.notification('success') on thread-creation success
 *   - HapticFeedback.notification('error') on thread-creation error
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { Snackbar } from "../components/Snackbar";
import { ApiError } from "../lib/api";
import {
  INTERNAL_CHAT_MESSAGE_MAX,
  INTERNAL_CHAT_SUBJECT_MAX,
  TOPIC_CODES,
  TOPIC_LABELS_RU,
  type InternalChatThreadSummary,
  type StatusFilter,
  type ThreadStatus,
  type TopicCode,
  createMasterThread,
  isActiveStatus,
  isArchivedStatus,
  isResolvedStatus,
  listMasterThreads,
} from "../lib/internal-chat-api";
import {
  hapticImpact,
  hapticNotify,
  hapticSelection,
  onBackButton,
  setBackButton,
  setClosingConfirmation,
} from "../lib/max-sdk";

// --- Russian copy (verbatim from §3.2 / §3.3) ----------------------------

const COPY = {
  header: "Со студией",
  headerEmoji: "🏛",
  filters: {
    all: "Все",
    active: "Активные",
    resolved: "Решённые",
    archived: "Архив",
  },
  sectionActive: "Открытые",
  sectionResolved: "Решённые",
  sectionArchived: "Архив",
  newThreadCta: "✏ Написать студии",
  newThreadSectionHelper: "Что-то новое?",
  newThreadSheetTitle: "Что обсудим?",
  newThreadSheetHelper:
    "Выберите тему — это поможет быстрее ответить:",
  newThreadSheetNext: "Дальше",
  newThreadSheetCancel: "Отмена",
  composerTitlePrefix: "Опишите вопрос",
  composerPlaceholder: "Напишите подробнее, что случилось…",
  composerSubjectPlaceholder: "Тема (необязательно)",
  composerSubmit: "Открыть обсуждение",
  composerSubmitting: "Открываем…",
  composerBack: "Назад",
  emptyAll:
    "У вас пока нет обсуждений со студией. Открыть новое — кнопка ниже.",
  emptyActive: "Сейчас активных обсуждений со студией нет.",
  emptyResolved: "В этом месяце решённых обсуждений нет.",
  emptyArchived: "В архиве пусто.",
  loading: "Загружаем обсуждения со студией…",
  loadError: "Не получилось загрузить обсуждения. Попробуйте снова.",
  retry: "Попробовать снова",
  threadOpen: "Открыть",
  agoNow: "только что",
  agoMinutes: (n: number) => `${n} мин назад`,
  agoHours: (n: number) => `${n} ч назад`,
  agoDays: (n: number) => `${n} дн назад`,
  toastCreated: "Обсуждение открыто",
  toastCreateError: "Не получилось открыть обсуждение. Попробуйте ещё раз.",
  sensitiveBadge: "Конфиденциально",
  escalatedBadge: "Эскалировано",
  noSubject: "Без темы",
  composerTooLongHint: `Длиннее ${INTERNAL_CHAT_MESSAGE_MAX} символов нельзя.`,
  subjectTooLongHint: `Тема длиннее ${INTERNAL_CHAT_SUBJECT_MAX} символов нельзя.`,
};

// --- helpers --------------------------------------------------------------

function formatRelative(iso: string, now: number): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const deltaMs = Math.max(0, now - t);
  const minutes = Math.floor(deltaMs / 60_000);
  if (minutes < 1) return COPY.agoNow;
  if (minutes < 60) return COPY.agoMinutes(minutes);
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return COPY.agoHours(hours);
  const days = Math.floor(hours / 24);
  return COPY.agoDays(days);
}

function statusBucket(status: ThreadStatus): "active" | "resolved" | "archived" {
  if (isResolvedStatus(status)) return "resolved";
  if (isArchivedStatus(status)) return "archived";
  // Treat everything else (including ESCALATED_TO_FOUNDER) as active —
  // master is still waiting on a reply or in mid-conversation.
  if (isActiveStatus(status)) return "active";
  return "active";
}

function applyFilter(
  rows: InternalChatThreadSummary[],
  filter: StatusFilter,
): InternalChatThreadSummary[] {
  if (filter === "all") return rows;
  return rows.filter((r) => statusBucket(r.status) === filter);
}

function emptyCopyForFilter(filter: StatusFilter): string {
  switch (filter) {
    case "active":
      return COPY.emptyActive;
    case "resolved":
      return COPY.emptyResolved;
    case "archived":
      return COPY.emptyArchived;
    default:
      return COPY.emptyAll;
  }
}

// --- state-machine types --------------------------------------------------

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; rows: InternalChatThreadSummary[] }
  | { kind: "error"; err: unknown };

type NewThreadSheet =
  | { kind: "closed" }
  | { kind: "picker"; topic: TopicCode | null }
  | {
      kind: "compose";
      topic: TopicCode;
      subject: string;
      body: string;
      submitting: boolean;
      err: string;
    };

// --- component ------------------------------------------------------------

export function MasterInternalChatListScreen() {
  const navigate = useNavigate();
  const location = useLocation();

  // Deep-link «open new thread with topic=X» — used by M4
  // «Написать Карине» CTA + future cross-doc «открыть обсуждение»
  // buttons (handoff §5.3).
  const initialOpenTopic = useMemo<TopicCode | null>(() => {
    const search = new URLSearchParams(location.search);
    if (search.get("new") !== "1") return null;
    const raw = search.get("topic") || "general";
    return (TOPIC_CODES as readonly string[]).includes(raw)
      ? (raw as TopicCode)
      : "general";
  }, [location.search]);

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [filter, setFilter] = useState<StatusFilter>("active");
  const [sheet, setSheet] = useState<NewThreadSheet>({ kind: "closed" });
  const [toast, setToast] = useState("");

  // --- Bridge: BackButton wiring ---
  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => {
      hapticSelection();
      navigate("/master/profile");
    });
    return () => {
      off();
      setBackButton(false);
      setClosingConfirmation(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Closing confirmation while compose dirty ---
  useEffect(() => {
    if (sheet.kind === "compose") {
      const dirty = sheet.body.trim().length > 0 || sheet.subject.trim().length > 0;
      setClosingConfirmation(dirty);
    } else {
      setClosingConfirmation(false);
    }
  }, [sheet]);

  // --- Initial load + reload ---
  const reload = useCallback(async () => {
    setPhase({ kind: "loading" });
    try {
      const res = await listMasterThreads({ limit: 50 });
      setPhase({ kind: "ready", rows: res.items });
    } catch (err) {
      setPhase({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // --- Deep-link open-new-thread (?new=1&topic=X) ---
  useEffect(() => {
    if (!initialOpenTopic) return;
    if (sheet.kind !== "closed") return;
    setSheet({
      kind: "compose",
      topic: initialOpenTopic,
      subject: "",
      body: "",
      submitting: false,
      err: "",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialOpenTopic]);

  // --- Sheet handlers ---
  const openPicker = useCallback(() => {
    hapticSelection();
    setSheet({ kind: "picker", topic: null });
  }, []);

  const cancelSheet = useCallback(() => {
    hapticSelection();
    setSheet({ kind: "closed" });
  }, []);

  const pickTopic = useCallback((topic: TopicCode) => {
    hapticSelection();
    setSheet((curr) => {
      if (curr.kind !== "picker") return curr;
      return { ...curr, topic };
    });
  }, []);

  const advanceToCompose = useCallback(() => {
    setSheet((curr) => {
      if (curr.kind !== "picker" || curr.topic == null) return curr;
      hapticSelection();
      return {
        kind: "compose",
        topic: curr.topic,
        subject: "",
        body: "",
        submitting: false,
        err: "",
      };
    });
  }, []);

  const backToPicker = useCallback(() => {
    setSheet((curr) => {
      if (curr.kind !== "compose") return curr;
      hapticSelection();
      return { kind: "picker", topic: curr.topic };
    });
  }, []);

  const updateComposeBody = useCallback((value: string) => {
    setSheet((curr) => {
      if (curr.kind !== "compose") return curr;
      return { ...curr, body: value, err: "" };
    });
  }, []);

  const updateComposeSubject = useCallback((value: string) => {
    setSheet((curr) => {
      if (curr.kind !== "compose") return curr;
      return { ...curr, subject: value, err: "" };
    });
  }, []);

  const submitNewThread = useCallback(async () => {
    if (sheet.kind !== "compose") return;
    const body = sheet.body.trim();
    if (!body) {
      setSheet({ ...sheet, err: "Напишите сообщение." });
      hapticNotify("error");
      return;
    }
    if (body.length > INTERNAL_CHAT_MESSAGE_MAX) {
      setSheet({ ...sheet, err: COPY.composerTooLongHint });
      hapticNotify("error");
      return;
    }
    const subject = sheet.subject.trim();
    if (subject.length > INTERNAL_CHAT_SUBJECT_MAX) {
      setSheet({ ...sheet, err: COPY.subjectTooLongHint });
      hapticNotify("error");
      return;
    }
    setSheet({ ...sheet, submitting: true, err: "" });
    try {
      const res = await createMasterThread({
        topic: sheet.topic,
        first_message_body: body,
        subject: subject || undefined,
      });
      hapticNotify("success");
      setToast(COPY.toastCreated);
      setSheet({ kind: "closed" });
      // Land on the freshly created thread detail.
      navigate(`/master/internal-chat/threads/${res.thread.id}`);
    } catch (e) {
      hapticNotify("error");
      const msg =
        e instanceof ApiError
          ? e.detail || COPY.toastCreateError
          : COPY.toastCreateError;
      setSheet({ ...sheet, submitting: false, err: msg });
    }
  }, [navigate, sheet]);

  // --- Filter chips ---
  const onFilterChange = useCallback((next: StatusFilter) => {
    hapticSelection();
    setFilter(next);
  }, []);

  // --- Open thread ---
  const openThread = useCallback(
    (id: string) => {
      hapticImpact("light");
      navigate(`/master/internal-chat/threads/${id}`);
    },
    [navigate],
  );

  // --- Render ---
  return (
    <div className="screen internal-chat-list">
      <header className="internal-chat-list__header">
        <h1 className="screen__title">
          <span aria-hidden="true">{COPY.headerEmoji} </span>
          {COPY.header}
        </h1>
      </header>

      <div className="internal-chat-list__filters" role="tablist" aria-label="Фильтр обсуждений">
        {(
          [
            { key: "active", label: COPY.filters.active },
            { key: "resolved", label: COPY.filters.resolved },
            { key: "archived", label: COPY.filters.archived },
            { key: "all", label: COPY.filters.all },
          ] as { key: StatusFilter; label: string }[]
        ).map((f) => (
          <button
            type="button"
            key={f.key}
            role="tab"
            aria-selected={filter === f.key}
            className={
              filter === f.key
                ? "internal-chat-list__filter internal-chat-list__filter--active"
                : "internal-chat-list__filter"
            }
            onClick={() => onFilterChange(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {phase.kind === "loading" ? (
        <ListSkeleton />
      ) : phase.kind === "error" ? (
        <ErrorBlock err={phase.err} onRetry={() => void reload()} />
      ) : (
        <ThreadList
          rows={applyFilter(phase.rows, filter)}
          onOpen={openThread}
          emptyCopy={emptyCopyForFilter(filter)}
        />
      )}

      <section className="internal-chat-list__new-thread">
        <p className="internal-chat-list__new-thread-helper">
          {COPY.newThreadSectionHelper}
        </p>
        <button
          type="button"
          className="btn-primary internal-chat-list__new-thread-cta"
          onClick={openPicker}
        >
          {COPY.newThreadCta}
        </button>
      </section>

      {sheet.kind === "picker" ? (
        <TopicPickerSheet
          selected={sheet.topic}
          onPick={pickTopic}
          onCancel={cancelSheet}
          onNext={advanceToCompose}
        />
      ) : null}

      {sheet.kind === "compose" ? (
        <ComposeFirstMessageSheet
          topic={sheet.topic}
          subject={sheet.subject}
          body={sheet.body}
          submitting={sheet.submitting}
          err={sheet.err}
          onChangeBody={updateComposeBody}
          onChangeSubject={updateComposeSubject}
          onBack={backToPicker}
          onCancel={cancelSheet}
          onSubmit={() => void submitNewThread()}
        />
      ) : null}

      <Snackbar
        visible={toast.length > 0}
        message={toast}
        durationMs={2200}
        onTimeout={() => setToast("")}
        onDismiss={() => setToast("")}
      />
    </div>
  );
}

// --- subcomponents --------------------------------------------------------

function ThreadList({
  rows,
  onOpen,
  emptyCopy,
}: {
  rows: InternalChatThreadSummary[];
  onOpen: (id: string) => void;
  emptyCopy: string;
}) {
  if (rows.length === 0) {
    return (
      <div className="callout internal-chat-list__empty" role="status">
        <p style={{ margin: 0 }}>{emptyCopy}</p>
      </div>
    );
  }
  // Group by bucket so the same grouping the spec shows still holds when
  // filter=all.
  const buckets: Record<
    "active" | "resolved" | "archived",
    InternalChatThreadSummary[]
  > = { active: [], resolved: [], archived: [] };
  for (const row of rows) buckets[statusBucket(row.status)].push(row);
  const now = Date.now();
  return (
    <div className="internal-chat-list__rows">
      {buckets.active.length > 0 ? (
        <ThreadSection
          title={COPY.sectionActive}
          rows={buckets.active}
          onOpen={onOpen}
          now={now}
        />
      ) : null}
      {buckets.resolved.length > 0 ? (
        <ThreadSection
          title={COPY.sectionResolved}
          rows={buckets.resolved}
          onOpen={onOpen}
          now={now}
        />
      ) : null}
      {buckets.archived.length > 0 ? (
        <ThreadSection
          title={COPY.sectionArchived}
          rows={buckets.archived}
          onOpen={onOpen}
          now={now}
        />
      ) : null}
    </div>
  );
}

function ThreadSection({
  title,
  rows,
  onOpen,
  now,
}: {
  title: string;
  rows: InternalChatThreadSummary[];
  onOpen: (id: string) => void;
  now: number;
}) {
  return (
    <section className="internal-chat-list__group">
      <h2 className="internal-chat-list__group-title">── {title} ──</h2>
      <ul className="internal-chat-list__list">
        {rows.map((row) => (
          <li key={row.id}>
            <ThreadCard row={row} onOpen={onOpen} now={now} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function ThreadCard({
  row,
  onOpen,
  now,
}: {
  row: InternalChatThreadSummary;
  onOpen: (id: string) => void;
  now: number;
}) {
  const subject = row.subject || COPY.noSubject;
  const ageLabel = formatRelative(row.last_activity_at, now);
  const topicLabel = TOPIC_LABELS_RU[row.topic] ?? row.topic_display;
  const escalated = !!row.founder_added_at;
  return (
    <button
      type="button"
      className="m-card m-card--tappable internal-chat-list__card"
      onClick={() => onOpen(row.id)}
      aria-label={`${subject}, ${topicLabel}, ${ageLabel}`}
    >
      <div className="internal-chat-list__card-top">
        <span className="internal-chat-list__card-topic">{topicLabel}</span>
        {row.is_sensitive ? (
          <span
            className="m-card__chip m-card__chip--warning"
            aria-label={COPY.sensitiveBadge}
          >
            {COPY.sensitiveBadge}
          </span>
        ) : null}
        {escalated ? (
          <span
            className="m-card__chip m-card__chip--warning"
            aria-label={COPY.escalatedBadge}
          >
            {COPY.escalatedBadge}
          </span>
        ) : null}
      </div>
      <div className="m-card__title internal-chat-list__card-subject">
        {subject}
      </div>
      <div className="m-card__meta internal-chat-list__card-meta">
        {ageLabel}
      </div>
    </button>
  );
}

function TopicPickerSheet({
  selected,
  onPick,
  onCancel,
  onNext,
}: {
  selected: TopicCode | null;
  onPick: (t: TopicCode) => void;
  onCancel: () => void;
  onNext: () => void;
}) {
  return (
    <div
      className="internal-chat-sheet"
      role="dialog"
      aria-modal="true"
      aria-label={COPY.newThreadSheetTitle}
    >
      <div className="internal-chat-sheet__card">
        <h2 className="internal-chat-sheet__title">
          ← {COPY.newThreadSheetTitle}
        </h2>
        <p className="internal-chat-sheet__helper">
          {COPY.newThreadSheetHelper}
        </p>
        <ul className="internal-chat-sheet__topics" role="radiogroup">
          {TOPIC_CODES.map((code) => (
            <li key={code}>
              <button
                type="button"
                role="radio"
                aria-checked={selected === code}
                className={
                  selected === code
                    ? "internal-chat-sheet__topic internal-chat-sheet__topic--selected"
                    : "internal-chat-sheet__topic"
                }
                onClick={() => onPick(code)}
              >
                <span className="internal-chat-sheet__topic-radio" aria-hidden="true">
                  {selected === code ? "⦿" : "◯"}
                </span>
                <span>{TOPIC_LABELS_RU[code]}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="internal-chat-sheet__actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
          >
            {COPY.newThreadSheetCancel}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={onNext}
            disabled={selected == null}
          >
            {COPY.newThreadSheetNext}
          </button>
        </div>
      </div>
    </div>
  );
}

function ComposeFirstMessageSheet({
  topic,
  subject,
  body,
  submitting,
  err,
  onChangeBody,
  onChangeSubject,
  onBack,
  onCancel,
  onSubmit,
}: {
  topic: TopicCode;
  subject: string;
  body: string;
  submitting: boolean;
  err: string;
  onChangeBody: (v: string) => void;
  onChangeSubject: (v: string) => void;
  onBack: () => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const remaining = INTERNAL_CHAT_MESSAGE_MAX - body.length;
  const overLimit = remaining < 0;
  const subjectOverLimit = subject.length > INTERNAL_CHAT_SUBJECT_MAX;
  return (
    <div
      className="internal-chat-sheet"
      role="dialog"
      aria-modal="true"
      aria-label={`${COPY.composerTitlePrefix}: ${TOPIC_LABELS_RU[topic]}`}
    >
      <div className="internal-chat-sheet__card">
        <h2 className="internal-chat-sheet__title">
          ← {TOPIC_LABELS_RU[topic]}
        </h2>
        <input
          type="text"
          className="internal-chat-sheet__subject"
          placeholder={COPY.composerSubjectPlaceholder}
          value={subject}
          onChange={(e) => onChangeSubject(e.target.value)}
          disabled={submitting}
          maxLength={INTERNAL_CHAT_SUBJECT_MAX + 50}
          aria-label={COPY.composerSubjectPlaceholder}
        />
        <textarea
          className="internal-chat-sheet__textarea"
          placeholder={COPY.composerPlaceholder}
          value={body}
          onChange={(e) => onChangeBody(e.target.value)}
          rows={6}
          maxLength={INTERNAL_CHAT_MESSAGE_MAX + 50}
          disabled={submitting}
          aria-label={COPY.composerPlaceholder}
        />
        <div
          className={
            overLimit
              ? "internal-chat-sheet__counter internal-chat-sheet__counter--bad"
              : "internal-chat-sheet__counter"
          }
          aria-live="polite"
        >
          {body.length} / {INTERNAL_CHAT_MESSAGE_MAX}
        </div>
        {err ? (
          <p className="internal-chat-sheet__error" role="alert">
            {err}
          </p>
        ) : null}
        <div className="internal-chat-sheet__actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onBack}
            disabled={submitting}
          >
            {COPY.composerBack}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
            disabled={submitting}
          >
            {COPY.newThreadSheetCancel}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={onSubmit}
            disabled={submitting || overLimit || subjectOverLimit || body.trim().length === 0}
          >
            {submitting ? COPY.composerSubmitting : COPY.composerSubmit}
          </button>
        </div>
      </div>
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="internal-chat-list__skeleton" aria-busy="true">
      <p className="internal-chat-list__loading">{COPY.loading}</p>
      {[0, 1, 2].map((i) => (
        <div key={i} className="m-card m-card--skel">
          <div className="skeleton" style={{ width: "60%", height: "1.1em" }} />
          <div
            className="skeleton"
            style={{ width: "45%", height: "0.9em", marginTop: 8 }}
          />
          <div
            className="skeleton"
            style={{ width: "30%", height: "0.8em", marginTop: 6 }}
          />
        </div>
      ))}
    </div>
  );
}

function ErrorBlock({
  err: _err,
  onRetry,
}: {
  err: unknown;
  onRetry: () => void;
}) {
  return (
    <div className="callout callout--danger" role="alert">
      <p style={{ margin: 0 }}>{COPY.loadError}</p>
      <div style={{ marginTop: "var(--s-3)" }}>
        <button type="button" className="btn-secondary" onClick={onRetry}>
          {COPY.retry}
        </button>
      </div>
    </div>
  );
}
