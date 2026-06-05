/**
 * Admin internal-chat — list screen («Чаты с мастерами»).
 *
 * Route: /admin/internal-chat (replaces /admin/chats placeholder, which
 * still routes to the AdminTabBar «Чаты» entry — wired in App.tsx).
 *
 * Spec source: docs/design/handoffs/2026-05-19-master-admin-internal-chat-handoff.md
 *
 * Spec quote (§4.1 «Чаты с мастерами» tab in admin Mini App, lines 287-316):
 *
 *     «💬 Чаты с мастерами (5) … ── Требуют ответа ── … Анна 🛌 —
 *      обсудить выходные / Прислала: 2 часа назад / SLA: 22ч из 24 /
 *      [Открыть] … ── Идёт обсуждение ── … ── Эскалированные ── /
 *      ⚠ Олеся 🚪 — эскалировала к основателю / Эскалирована 3 дня
 *      назад / [Посмотреть]»
 *
 * Spec quote (§4.2 «Thread view (admin)», lines 318-324):
 *
 *     «Same as master's view §3.4 but: Admin sees master's full identity;
 *      Admin sees linked artifact (booking, dispute, leave-request);
 *      Action buttons depending on topic: «закрыть спор», «согласовать
 *      отпуск», etc. that resolve linked artifact AND close thread»
 *
 * Differences vs MasterInternalChatListScreen:
 *   - Header «Чаты с мастерами» (not «Со студией») — admin queue.
 *   - Each row shows MASTER NAME prominently (admin sees full identity
 *     per §2.7 / §4.2 — privacy rule is one-way, admin can see master).
 *   - Filter chips: Активные / Решённые / Архив (mirrors master side
 *     wording for visual consistency).
 *   - Sections regroup into «Требуют ответа / Идёт обсуждение /
 *     Эскалированные» per §4.1.
 *   - NO «Написать студии» CTA — admins respond, they don't open new
 *     threads to themselves. (Admin-initiated threads — Q-IAC3 — are
 *     out of scope for this PR.)
 *
 * Receptionist role: the backend gates /admin/threads/ on
 * require_admin_role which admits owner/admin but rejects receptionist
 * (per test_receptionist_blocked_by_require_admin_role). When the API
 * returns 403 we render the «только для администраторов» banner.
 *
 * Bridge API:
 *   - BackButton hidden — this is a root tab (AdminTabBar is on-screen).
 *   - HapticFeedback.selection() on chip taps, thread taps.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import { Snackbar } from "../../components/Snackbar";
import { ApiError } from "../../lib/api";
import { type MeResponse } from "../../lib/admin-api";
import {
  TOPIC_LABELS_RU,
  type InternalChatThreadSummary,
  type StatusFilter,
  type ThreadStatus,
  type AdminThreadBucket,
  adminBucketFor,
  isActiveStatus,
  isArchivedStatus,
  isResolvedStatus,
  listAdminThreads,
} from "../../lib/internal-chat-api";
import {
  hapticImpact,
  hapticSelection,
  setBackButton,
} from "../../lib/max-sdk";

// --- Russian copy (verbatim from §4.1) -----------------------------------

const COPY = {
  header: "Чаты с мастерами",
  headerEmoji: "💬",
  filters: {
    all: "Все",
    active: "Активные",
    resolved: "Решённые",
    archived: "Архив",
  },
  sectionAwaiting: "Требуют ответа",
  sectionDiscussion: "Идёт обсуждение",
  sectionEscalated: "Эскалированные",
  sectionResolved: "Решённые",
  sectionArchived: "Архив",
  emptyAll:
    "Пока нет ни одного обсуждения. Когда мастер откроет тему — она появится здесь.",
  emptyActive: "Сейчас активных обсуждений нет.",
  emptyResolved: "Решённых обсуждений пока нет.",
  emptyArchived: "В архиве пусто.",
  loading: "Загружаем чаты с мастерами…",
  loadError: "Не получилось загрузить чаты. Попробуйте снова.",
  retry: "Попробовать снова",
  permissionDenied: "Эта страница только для администраторов.",
  threadOpen: "Открыть",
  threadView: "Посмотреть",
  agoNow: "только что",
  agoMinutes: (n: number) => `${n} мин назад`,
  agoHours: (n: number) => `${n} ч назад`,
  agoDays: (n: number) => `${n} дн назад`,
  sensitiveBadge: "Конфиденциально",
  escalatedBadge: "Эскалировано",
  noSubject: "Без темы",
  noMasterName: "Мастер",
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

function passesStatusFilter(
  status: ThreadStatus,
  filter: StatusFilter,
): boolean {
  if (filter === "all") return true;
  if (filter === "active") return isActiveStatus(status);
  if (filter === "resolved") return isResolvedStatus(status);
  if (filter === "archived") return isArchivedStatus(status);
  return true;
}

// --- state-machine types --------------------------------------------------

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; rows: InternalChatThreadSummary[] }
  | { kind: "error"; err: unknown };

interface Props {
  me: MeResponse;
}

// --- component ------------------------------------------------------------

export function AdminInternalChatListScreen({ me }: Props) {
  const navigate = useNavigate();

  // Owner + Admin pass the role gate; Receptionist gets a banner.
  // The backend re-checks at every endpoint (defence in depth).
  const isAdmin = me.is_owner || me.is_admin;

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [filter, setFilter] = useState<StatusFilter>("active");
  const [toast, setToast] = useState("");

  useEffect(() => {
    // Root tab — AdminTabBar handles navigation, MAX BackButton hidden.
    setBackButton(false);
  }, []);

  const reload = useCallback(async () => {
    if (!isAdmin) {
      setPhase({ kind: "ready", rows: [] });
      return;
    }
    setPhase({ kind: "loading" });
    try {
      // Fetch a generous page — backend caps at 50. Pagination beyond
      // the first page is deferred (most tenants have ≤ 20 active
      // threads at a time; M3-admin / earnings disputes mostly resolve
      // within 48h).
      const res = await listAdminThreads({ limit: 50 });
      setPhase({ kind: "ready", rows: res.items });
    } catch (err) {
      setPhase({ kind: "error", err });
    }
  }, [isAdmin]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const onFilterChange = useCallback((next: StatusFilter) => {
    hapticSelection();
    setFilter(next);
  }, []);

  const openThread = useCallback(
    (id: string) => {
      hapticImpact("light");
      navigate(`/admin/internal-chat/threads/${id}`);
    },
    [navigate],
  );

  // --- Render guards ---
  if (!isAdmin) {
    return (
      <div className="screen">
        <h1 className="screen__title">{COPY.header}</h1>
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>{COPY.permissionDenied}</p>
        </div>
        <AdminTabBar />
      </div>
    );
  }

  return (
    <div className="screen internal-chat-list">
      <header className="internal-chat-list__header">
        <h1 className="screen__title">
          <span aria-hidden="true">{COPY.headerEmoji} </span>
          {COPY.header}
        </h1>
      </header>

      <div
        className="internal-chat-list__filters"
        role="tablist"
        aria-label="Фильтр обсуждений"
      >
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
        <AdminThreadList
          rows={phase.rows.filter((r) => passesStatusFilter(r.status, filter))}
          onOpen={openThread}
          filter={filter}
        />
      )}

      <Snackbar
        visible={toast.length > 0}
        message={toast}
        durationMs={2200}
        onTimeout={() => setToast("")}
        onDismiss={() => setToast("")}
      />

      <AdminTabBar />
    </div>
  );
}

// --- subcomponents --------------------------------------------------------

function AdminThreadList({
  rows,
  onOpen,
  filter,
}: {
  rows: InternalChatThreadSummary[];
  onOpen: (id: string) => void;
  filter: StatusFilter;
}) {
  const now = Date.now();

  // Group by admin bucket per §4.1.
  const buckets = useMemo(() => {
    const acc: Record<AdminThreadBucket, InternalChatThreadSummary[]> = {
      awaiting: [],
      discussion: [],
      escalated: [],
      resolved: [],
      archived: [],
    };
    for (const r of rows) acc[adminBucketFor(r)].push(r);
    return acc;
  }, [rows]);

  if (rows.length === 0) {
    let emptyCopy = COPY.emptyAll;
    if (filter === "active") emptyCopy = COPY.emptyActive;
    else if (filter === "resolved") emptyCopy = COPY.emptyResolved;
    else if (filter === "archived") emptyCopy = COPY.emptyArchived;
    return (
      <div className="callout internal-chat-list__empty" role="status">
        <p style={{ margin: 0 }}>{emptyCopy}</p>
      </div>
    );
  }

  return (
    <div className="internal-chat-list__rows">
      {buckets.awaiting.length > 0 ? (
        <AdminThreadSection
          title={COPY.sectionAwaiting}
          rows={buckets.awaiting}
          onOpen={onOpen}
          now={now}
        />
      ) : null}
      {buckets.discussion.length > 0 ? (
        <AdminThreadSection
          title={COPY.sectionDiscussion}
          rows={buckets.discussion}
          onOpen={onOpen}
          now={now}
        />
      ) : null}
      {buckets.escalated.length > 0 ? (
        <AdminThreadSection
          title={COPY.sectionEscalated}
          rows={buckets.escalated}
          onOpen={onOpen}
          now={now}
        />
      ) : null}
      {buckets.resolved.length > 0 ? (
        <AdminThreadSection
          title={COPY.sectionResolved}
          rows={buckets.resolved}
          onOpen={onOpen}
          now={now}
        />
      ) : null}
      {buckets.archived.length > 0 ? (
        <AdminThreadSection
          title={COPY.sectionArchived}
          rows={buckets.archived}
          onOpen={onOpen}
          now={now}
        />
      ) : null}
    </div>
  );
}

function AdminThreadSection({
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
            <AdminThreadCard row={row} onOpen={onOpen} now={now} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function AdminThreadCard({
  row,
  onOpen,
  now,
}: {
  row: InternalChatThreadSummary;
  onOpen: (id: string) => void;
  now: number;
}) {
  const masterName = row.master?.name?.trim() || COPY.noMasterName;
  const subject = row.subject || COPY.noSubject;
  const ageLabel = formatRelative(row.last_activity_at, now);
  const topicLabel = TOPIC_LABELS_RU[row.topic] ?? row.topic_display;
  const escalated = !!row.founder_added_at;
  return (
    <button
      type="button"
      className="m-card m-card--tappable internal-chat-list__card"
      onClick={() => onOpen(row.id)}
      aria-label={`${masterName}, ${topicLabel}, ${subject}, ${ageLabel}`}
    >
      <div className="internal-chat-list__card-top">
        <span className="internal-chat-list__card-master">{masterName}</span>
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
  err,
  onRetry,
}: {
  err: unknown;
  onRetry: () => void;
}) {
  // 403 from the backend means receptionist / customer hit the
  // endpoint despite the front-end gate (e.g. an unusual role mapping).
  // Render the same «только для администраторов» banner.
  const forbidden = err instanceof ApiError && err.status === 403;
  return (
    <div className="callout callout--danger" role="alert">
      <p style={{ margin: 0 }}>
        {forbidden ? COPY.permissionDenied : COPY.loadError}
      </p>
      {!forbidden ? (
        <div style={{ marginTop: "var(--s-3)" }}>
          <button type="button" className="btn-secondary" onClick={onRetry}>
            {COPY.retry}
          </button>
        </div>
      ) : null}
    </div>
  );
}
