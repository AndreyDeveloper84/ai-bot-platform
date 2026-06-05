/**
 * Master M1 dashboard — home screen for the master Mini App.
 *
 * Route: /master/dashboard
 *
 * Spec source: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M1
 * (lines 280–373). State-matrix discipline (loading / empty / day-done /
 * offline / stale) per
 * docs/design/policies/customer-first-touch-and-mini-app-states.md §7.
 *
 * Spec quote (§M1, lines 284–337):
 *
 *     «Layout (mobile, ~360–430px width) … СЕЙЧАС … СЛЕДУЮЩИЙ КЛИЕНТ …
 *     ТРЕБУЮТ ВНИМАНИЯ (2) … СЕГОДНЯ … [🏠] [📅] [💬 2] [👤]»
 *
 * Backend contract: GET /api/v1/master/dashboard (apps/master_api/views.py
 * → apps/master_api/services/dashboard.py::build_dashboard).
 *
 * Sections rendered:
 *   1. Header — salon name + master avatar + date + time
 *   2. СЕЙЧАС  — active visit card (red dot if in progress)
 *   3. СЛЕДУЮЩИЙ КЛИЕНТ — next visit card
 *   4. ТРЕБУЮТ ВНИМАНИЯ — inbox preview cards (max 3)
 *   5. СЕГОДНЯ — counters + next free window
 *   6. Sticky bottom tab bar (4 tabs)
 *
 * State branches:
 *   - loading            → 3 skeleton cards
 *   - empty (no clients) → «Сегодня нет записей.» variant
 *   - is_day_done        → «Вы провели N клиентов. Хороший день.»
 *   - active visit       → СЕЙЧАС card with red dot
 *   - offline / 5xx      → stale data banner + retry
 *   - permission denied  → «Этот диалог не для вас» (cross-master deep-link)
 *
 * Bridge API (§M1 lines 359–363):
 *   - WebApp.BackButton.hide() on mount (root screen)
 *   - WebApp.HapticFeedback.selectionChanged() on card tap
 *   - Pull-to-refresh → HapticFeedback.impactOccurred('soft')
 *   - No enableClosingConfirmation (no dirty state)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../lib/api";
import {
  getDashboard,
  type DashboardActiveVisit,
  type DashboardInboxItem,
  type DashboardNextVisit,
  type DashboardResponse,
  type DashboardTodaySummary,
  type SlaTier,
} from "../lib/master-api";
import {
  hapticImpact,
  hapticSelection,
  setBackButton,
  signalReady,
} from "../lib/max-sdk";
import { MasterTabBar } from "../components/MasterTabBar";
import {
  formatDateLong,
  formatRelativePast,
  formatTimeHM,
  joinClientName,
} from "../lib/masterDateFormat";

// --- Russian copy (VERBATIM from §M1) ------------------------------------

const COPY = {
  sections: {
    now: "СЕЙЧАС",
    next: "СЛЕДУЮЩИЙ КЛИЕНТ",
    needsAttention: (n: number) => `ТРЕБУЮТ ВНИМАНИЯ (${n})`,
    today: "СЕГОДНЯ",
  },
  active: {
    inProgress: "Сейчас идёт визит",
    startedAt: (time: string) => `Началось ${time}`,
    durationSuffix: (min: number) => `${min} мин`,
    remaining: (min: number) => `До конца ≈ ${min} мин`,
    noteCta: "Заметка к визиту ›",
  },
  next: {
    timePrefix: (time: string) => `в ${time}`,
    returning: "⚠ Постоянный клиент",
    saidPrefix: "Сказала: ",
    openDialogCta: "Открыть диалог ›",
  },
  inbox: {
    suggestedReply: "Помощник предложил ответ — посмотреть?",
    allDialogsCta: "Все диалоги ›",
  },
  todaySummary: {
    clientsAndCompleted: (total: number, completed: number) =>
      `${total} ${pluralRu(total, "клиент", "клиента", "клиентов")} · ${completed} завершено`,
    nextWindow: (start: string, end: string) =>
      `Следующее окно: ${start}–${end}`,
    scheduleWeekCta: "Расписание на неделю ›",
  },
  empty: {
    noClientsToday: (firstName: string | null, time: string | null) =>
      firstName && time
        ? `Сегодня нет записей. Свободный день — отдохните. Ближайшая запись завтра в ${time} — ${firstName}.`
        : "Сегодня нет записей. Свободный день — отдохните.",
    noClientsCta: "Расписание ›",
    noServices:
      "Карина ещё не назначила вас на услуги. Если думаете, что это ошибка — напишите ей в MAX.",
    noServicesCta: "Написать Карине",
  },
  dayDone: {
    body: (n: number, time: string | null, firstName: string | null) =>
      n > 0
        ? firstName && time
          ? `Вы провели ${n} ${pluralRu(n, "клиента", "клиентов", "клиентов")}. Хороший день. Завтра первая запись ${time} — ${firstName}.`
          : `Вы провели ${n} ${pluralRu(n, "клиента", "клиентов", "клиентов")}. Хороший день.`
        : "Хороший день. Завтра увидимся.",
  },
  offline: {
    banner: "Данные могут быть неактуальны",
    retry: "Обновить",
  },
  permissionDenied: {
    title: "Этот диалог не для вас",
    body: "Если думаете, что должны видеть — напишите Карине.",
  },
  loading: "Загружаем рабочий стол…",
  errorTitle: "Не получилось загрузить",
  retry: "Попробовать снова",
};

/** Russian plural — picks (one / few / many) based on Slavic rules. */
function pluralRu(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

// --- State model ----------------------------------------------------------

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; data: DashboardResponse; stale?: boolean }
  | { kind: "stale_with_data"; data: DashboardResponse; err: unknown }
  | { kind: "error_initial"; err: unknown }
  | { kind: "error_permission" };

const REFRESH_HAPTIC_STYLE = "light" as const;

// --- Component ------------------------------------------------------------

export function MasterDashboardScreen() {
  const navigate = useNavigate();

  // Cache the last successful payload separately so a fetch failure can
  // downgrade to «stale_with_data» banner instead of nuking the UI.
  const lastGoodRef = useRef<DashboardResponse | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);

  // Pull-to-refresh state.
  const touchStartY = useRef<number | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);

  // BackButton: hide on dashboard root (§M1 line 361 «BackButton.hide()»).
  useEffect(() => {
    setBackButton(false);
    signalReady();
  }, []);

  // --- Fetch --------------------------------------------------------------

  const load = useCallback(async (isRefresh: boolean) => {
    if (!isRefresh) setPhase({ kind: "loading" });
    setRefreshing(true);
    try {
      const data = await getDashboard();
      lastGoodRef.current = data;
      setPhase({ kind: "ready", data });
    } catch (err: unknown) {
      // Permission cliff — spec §M1 line 357. We treat any 403 from the
      // dashboard endpoint as cross-master / not-yet-linked.
      if (err instanceof ApiError && err.status === 403) {
        setPhase({ kind: "error_permission" });
        return;
      }
      // Network / 5xx — degrade to stale data if we have any.
      if (lastGoodRef.current) {
        setPhase({
          kind: "stale_with_data",
          data: lastGoodRef.current,
          err,
        });
      } else {
        setPhase({ kind: "error_initial", err });
      }
    } finally {
      setRefreshing(false);
    }
  }, []);

  // Initial mount.
  useEffect(() => {
    void load(false);
  }, [load]);

  // --- Pull-to-refresh ----------------------------------------------------
  //
  // Lightweight touch-based PTR. Only fires when the user starts a drag
  // at scrollTop === 0 and the gesture is downward by ≥ 60px. Triggers
  // haptic impact + reload. Spec §M1 line 363: «Pull-to-refresh →
  // HapticFeedback.impactOccurred('soft')». We use 'light' since the
  // MAX bridge has no 'soft' style — closest available impact level.

  const onTouchStart = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    const el = scrollContainerRef.current;
    if (!el || el.scrollTop > 0) {
      touchStartY.current = null;
      return;
    }
    touchStartY.current = e.touches[0]?.clientY ?? null;
  }, []);

  const onTouchEnd = useCallback(
    (e: React.TouchEvent<HTMLDivElement>) => {
      const start = touchStartY.current;
      touchStartY.current = null;
      if (start === null) return;
      const endY = e.changedTouches[0]?.clientY ?? start;
      if (endY - start >= 60 && !refreshing) {
        hapticImpact(REFRESH_HAPTIC_STYLE);
        void load(true);
      }
    },
    [load, refreshing],
  );

  // --- Card-tap helpers ---------------------------------------------------

  const onInboxCardTap = useCallback(() => {
    hapticSelection();
    navigate("/master/conversations");
  }, [navigate]);

  const onNextVisitTap = useCallback(() => {
    hapticSelection();
    navigate("/master/conversations");
  }, [navigate]);

  const onScheduleCta = useCallback(() => {
    hapticSelection();
    navigate("/master/schedule");
  }, [navigate]);

  // --- Resolve current data + flags --------------------------------------

  const data: DashboardResponse | null =
    phase.kind === "ready" || phase.kind === "stale_with_data"
      ? phase.data
      : null;
  const isStale = phase.kind === "stale_with_data";

  // --- Branch rendering --------------------------------------------------

  if (phase.kind === "error_permission") {
    return (
      <PermissionDeniedScreen
        onRetry={() => navigate("/master/dashboard", { replace: true })}
      />
    );
  }

  if (phase.kind === "loading") {
    return (
      <DashboardFrame
        scrollRef={scrollContainerRef}
        onTouchStart={onTouchStart}
        onTouchEnd={onTouchEnd}
      >
        <LoadingSkeleton />
        <TabBarBlank />
      </DashboardFrame>
    );
  }

  if (phase.kind === "error_initial") {
    return (
      <DashboardFrame
        scrollRef={scrollContainerRef}
        onTouchStart={onTouchStart}
        onTouchEnd={onTouchEnd}
      >
        <ErrorInitial err={phase.err} onRetry={() => load(false)} />
        <TabBarBlank />
      </DashboardFrame>
    );
  }

  if (data === null) {
    // Defensive — every other phase should have data by now.
    return null;
  }

  // States.
  const { active_visit, next_visit, inbox_preview, today_summary, tab_badges, states } = data;
  const isEmptyToday =
    active_visit === null &&
    next_visit === null &&
    inbox_preview.length === 0 &&
    today_summary.total_clients_today === 0;
  const isDayDone = states.is_day_done && !active_visit && !next_visit;
  const noServices =
    !data.master.specialization &&
    today_summary.total_clients_today === 0 &&
    inbox_preview.length === 0;

  return (
    <DashboardFrame
      scrollRef={scrollContainerRef}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      <DashboardHeader
        salonName={data.salon.name}
        masterName={data.master.name}
        photoUrl={data.master.photo_url}
        nowIso={data.now_iso}
      />

      {isStale ? (
        <StaleBanner onRetry={() => load(true)} refreshing={refreshing} />
      ) : null}

      {isDayDone ? (
        <DayDoneSection
          completedCount={today_summary.completed_count}
          totalClients={today_summary.total_clients_today}
        />
      ) : isEmptyToday ? (
        noServices ? (
          <NoServicesSection />
        ) : (
          <EmptyTodaySection onSchedule={onScheduleCta} />
        )
      ) : (
        <>
          <ActiveVisitSection visit={active_visit} />
          <NextVisitSection visit={next_visit} onTap={onNextVisitTap} />
          <InboxSection
            items={inbox_preview}
            onCardTap={onInboxCardTap}
            onAllDialogs={() => {
              hapticSelection();
              navigate("/master/conversations");
            }}
          />
          <TodaySection
            summary={today_summary}
            onScheduleWeek={onScheduleCta}
          />
        </>
      )}

      <MasterTabBar
        unreadCount={tab_badges.conversations_unread}
        scheduleHasPendingChange={tab_badges.schedule_has_pending_change}
        profileHasOwnerPendingChange={tab_badges.profile_has_owner_pending_change}
      />
    </DashboardFrame>
  );
}

// ----------------------------------------------------------------------------
// Frame
// ----------------------------------------------------------------------------

function DashboardFrame({
  scrollRef,
  onTouchStart,
  onTouchEnd,
  children,
}: {
  scrollRef: React.RefObject<HTMLDivElement>;
  onTouchStart: (e: React.TouchEvent<HTMLDivElement>) => void;
  onTouchEnd: (e: React.TouchEvent<HTMLDivElement>) => void;
  children: React.ReactNode;
}) {
  return (
    <div
      ref={scrollRef}
      className="master-dashboard"
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      {children}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Header
// ----------------------------------------------------------------------------

function DashboardHeader({
  salonName,
  masterName,
  photoUrl,
  nowIso,
}: {
  salonName: string;
  masterName: string;
  photoUrl: string;
  nowIso: string;
}) {
  // We render the master's first name + initial-circle when the photo is
  // missing. Spec §M1 lines 289-291: «Студия Карина [Анна ●] / Среда, 21
  // мая 14:42».
  const firstName = (masterName || "").split(/\s+/)[0] ?? "";
  const initials = useMemo(() => {
    const parts = (masterName || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "—";
    return parts.slice(0, 2).map((p) => p[0]?.toUpperCase() ?? "").join("");
  }, [masterName]);
  return (
    <header className="master-dashboard__header">
      <div className="master-dashboard__header-left">
        <div className="master-dashboard__salon">{salonName}</div>
        <div className="master-dashboard__date">{formatDateLong(nowIso)}</div>
      </div>
      <div className="master-dashboard__header-right">
        <div className="master-dashboard__avatar" aria-label={firstName}>
          {photoUrl ? (
            <img src={photoUrl} alt="" />
          ) : (
            <span>{initials}</span>
          )}
        </div>
        <div className="master-dashboard__time">{formatTimeHM(nowIso)}</div>
      </div>
    </header>
  );
}

// ----------------------------------------------------------------------------
// СЕЙЧАС — active visit
// ----------------------------------------------------------------------------

function ActiveVisitSection({ visit }: { visit: DashboardActiveVisit | null }) {
  return (
    <section className="master-dashboard__section" aria-labelledby="m1-now">
      <h2 className="master-dashboard__section-title" id="m1-now">
        {COPY.sections.now}
      </h2>
      {visit === null ? (
        <p className="master-dashboard__section-empty">
          Сейчас визитов нет.
        </p>
      ) : (
        <ActiveVisitCard visit={visit} />
      )}
    </section>
  );
}

function ActiveVisitCard({ visit }: { visit: DashboardActiveVisit }) {
  const clientName = joinClientName(
    visit.client_first_name,
    visit.client_last_initial,
  );
  return (
    <article className="m-card m-card--active" aria-label={COPY.active.inProgress}>
      <div className="m-card__title">
        {visit.is_in_progress ? (
          <span className="m-card__dot m-card__dot--red" aria-hidden="true" />
        ) : null}
        <span>
          {clientName} — {visit.service_name}
        </span>
      </div>
      <div className="m-card__meta">
        {COPY.active.startedAt(formatTimeHM(visit.started_at))} ·{" "}
        {COPY.active.durationSuffix(visit.duration_min)}
      </div>
      <div className="m-card__meta">
        {COPY.active.remaining(visit.minutes_remaining)}
      </div>
      {/* «Заметка к визиту» — placeholder, full implementation deferred. */}
      <button
        type="button"
        className="m-card__cta"
        aria-disabled="true"
        onClick={(e) => e.preventDefault()}
      >
        {COPY.active.noteCta}
      </button>
    </article>
  );
}

// ----------------------------------------------------------------------------
// СЛЕДУЮЩИЙ КЛИЕНТ
// ----------------------------------------------------------------------------

function NextVisitSection({
  visit,
  onTap,
}: {
  visit: DashboardNextVisit | null;
  onTap: () => void;
}) {
  return (
    <section className="master-dashboard__section" aria-labelledby="m1-next">
      <h2 className="master-dashboard__section-title" id="m1-next">
        {COPY.sections.next}
      </h2>
      {visit === null ? (
        <p className="master-dashboard__section-empty">
          Больше визитов сегодня нет.
        </p>
      ) : (
        <NextVisitCard visit={visit} onTap={onTap} />
      )}
    </section>
  );
}

function NextVisitCard({
  visit,
  onTap,
}: {
  visit: DashboardNextVisit;
  onTap: () => void;
}) {
  const clientName = joinClientName(
    visit.client_first_name,
    visit.client_last_initial,
  );
  return (
    <button type="button" className="m-card m-card--tappable" onClick={onTap}>
      <div className="m-card__title">
        {clientName} {COPY.next.timePrefix(formatTimeHM(visit.visit_at))}
      </div>
      <div className="m-card__meta">
        {visit.service_name} · {visit.duration_min} мин
      </div>
      {visit.is_returning_customer ? (
        <div className="m-card__chip m-card__chip--warning">
          {COPY.next.returning}
        </div>
      ) : null}
      {visit.customer_intent_hint ? (
        <div className="m-card__hint">
          {COPY.next.saidPrefix}«{visit.customer_intent_hint}»
        </div>
      ) : null}
      <div className="m-card__cta">{COPY.next.openDialogCta}</div>
    </button>
  );
}

// ----------------------------------------------------------------------------
// ТРЕБУЮТ ВНИМАНИЯ
// ----------------------------------------------------------------------------

function InboxSection({
  items,
  onCardTap,
  onAllDialogs,
}: {
  items: DashboardInboxItem[];
  onCardTap: () => void;
  onAllDialogs: () => void;
}) {
  if (items.length === 0) return null;
  return (
    <section className="master-dashboard__section" aria-labelledby="m1-inbox">
      <h2 className="master-dashboard__section-title" id="m1-inbox">
        {COPY.sections.needsAttention(items.length)}
      </h2>
      {items.map((item) => (
        <InboxCard key={item.conversation_id} item={item} onTap={onCardTap} />
      ))}
      <button
        type="button"
        className="btn-secondary master-dashboard__inline-cta"
        onClick={onAllDialogs}
      >
        {COPY.inbox.allDialogsCta}
      </button>
    </section>
  );
}

function InboxCard({
  item,
  onTap,
}: {
  item: DashboardInboxItem;
  onTap: () => void;
}) {
  const clientName = joinClientName(
    item.client_first_name,
    item.client_last_initial,
  );
  const tier: SlaTier = item.sla_tier;
  return (
    <button
      type="button"
      className="m-card m-card--tappable m-card--inbox"
      onClick={onTap}
    >
      <div className="m-card__title">
        <span
          className={`m-card__dot m-card__dot--${tier}`}
          aria-label={`SLA: ${tier}`}
        />
        <span>{clientName}</span>
        <span className="m-card__meta-inline">
          · {formatRelativePast(item.last_message_at)}
        </span>
      </div>
      <div className="m-card__excerpt">
        {item.ai_drafted_reply_available
          ? COPY.inbox.suggestedReply
          : `«${item.last_message_excerpt}»`}
      </div>
    </button>
  );
}

// ----------------------------------------------------------------------------
// СЕГОДНЯ
// ----------------------------------------------------------------------------

function TodaySection({
  summary,
  onScheduleWeek,
}: {
  summary: DashboardTodaySummary;
  onScheduleWeek: () => void;
}) {
  return (
    <section className="master-dashboard__section" aria-labelledby="m1-today">
      <h2 className="master-dashboard__section-title" id="m1-today">
        {COPY.sections.today}
      </h2>
      <p className="master-dashboard__today-line">
        {COPY.todaySummary.clientsAndCompleted(
          summary.total_clients_today,
          summary.completed_count,
        )}
      </p>
      {summary.next_free_window ? (
        <p className="master-dashboard__today-line">
          {COPY.todaySummary.nextWindow(
            summary.next_free_window.start,
            summary.next_free_window.end,
          )}
        </p>
      ) : null}
      <button
        type="button"
        className="btn-secondary master-dashboard__inline-cta"
        onClick={onScheduleWeek}
      >
        {COPY.todaySummary.scheduleWeekCta}
      </button>
    </section>
  );
}

// ----------------------------------------------------------------------------
// Empty states
// ----------------------------------------------------------------------------

function EmptyTodaySection({ onSchedule }: { onSchedule: () => void }) {
  return (
    <section className="master-dashboard__section">
      <p className="master-dashboard__empty-line">
        {COPY.empty.noClientsToday(null, null)}
      </p>
      <button
        type="button"
        className="btn-secondary master-dashboard__inline-cta"
        onClick={onSchedule}
      >
        {COPY.empty.noClientsCta}
      </button>
    </section>
  );
}

function NoServicesSection() {
  return (
    <section className="master-dashboard__section">
      <div className="callout" role="status">
        <p style={{ margin: 0 }}>{COPY.empty.noServices}</p>
      </div>
    </section>
  );
}

function DayDoneSection({
  completedCount,
  totalClients,
}: {
  completedCount: number;
  totalClients: number;
}) {
  const n = Math.max(completedCount, totalClients);
  return (
    <section className="master-dashboard__section">
      <p className="master-dashboard__empty-line">
        {COPY.dayDone.body(n, null, null)}
      </p>
    </section>
  );
}

// ----------------------------------------------------------------------------
// Loading / error / stale
// ----------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="master-dashboard__skeleton-wrap" aria-busy="true">
      <p className="master-dashboard__loading-label">{COPY.loading}</p>
      <div className="m-card m-card--skel">
        <div className="skeleton" style={{ width: "60%", height: "1.1em" }} />
        <div className="skeleton" style={{ width: "40%", height: "0.9em", marginTop: 8 }} />
      </div>
      <div className="m-card m-card--skel">
        <div className="skeleton" style={{ width: "70%", height: "1.1em" }} />
        <div className="skeleton" style={{ width: "50%", height: "0.9em", marginTop: 8 }} />
      </div>
      <div className="m-card m-card--skel">
        <div className="skeleton" style={{ width: "55%", height: "1.1em" }} />
        <div className="skeleton" style={{ width: "65%", height: "0.9em", marginTop: 8 }} />
      </div>
    </div>
  );
}

function ErrorInitial({
  err,
  onRetry,
}: {
  err: unknown;
  onRetry: () => void;
}) {
  const isServer = err instanceof ApiError && err.status >= 500;
  const body = isServer
    ? "Что-то у нас не получается прямо сейчас."
    : "Не получилось загрузить. Проверьте интернет и попробуйте снова.";
  return (
    <div className="master-dashboard__section">
      <h2 className="master-dashboard__section-title">{COPY.errorTitle}</h2>
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>{body}</p>
        <div style={{ marginTop: "var(--s-3)" }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={onRetry}
          >
            {COPY.retry}
          </button>
        </div>
      </div>
    </div>
  );
}

function StaleBanner({
  onRetry,
  refreshing,
}: {
  onRetry: () => void;
  refreshing: boolean;
}) {
  return (
    <div className="master-dashboard__stale-banner" role="status">
      <span>{COPY.offline.banner}</span>
      <button
        type="button"
        className="master-dashboard__stale-retry"
        onClick={onRetry}
        disabled={refreshing}
      >
        {COPY.offline.retry}
      </button>
    </div>
  );
}

function PermissionDeniedScreen({ onRetry: _onRetry }: { onRetry: () => void }) {
  return (
    <div className="master-dashboard">
      <section className="master-dashboard__section">
        <h2 className="master-dashboard__section-title">
          {COPY.permissionDenied.title}
        </h2>
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>{COPY.permissionDenied.body}</p>
        </div>
      </section>
    </div>
  );
}

/** Empty tab bar shown while data loads — keeps layout stable. */
function TabBarBlank() {
  return (
    <MasterTabBar
      unreadCount={0}
      scheduleHasPendingChange={false}
      profileHasOwnerPendingChange={false}
    />
  );
}
