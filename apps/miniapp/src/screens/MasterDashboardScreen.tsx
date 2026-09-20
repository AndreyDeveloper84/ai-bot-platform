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
 * DRF-2152 (М-1, макет DRF-1182 — решение владельца 20.09): «Сегодня» — это
 * СОСТОЯНИЕ ДНЯ, и оно стоит ПЕРВЫМ. Порядок: шапка → блок дня → карточка
 * настройки (пока не готов) → «Принимаю записи» → «Спросить Ayla» → панель.
 *
 * Блок дня — одно из состояний (`DayBlock`):
 *   - ближайшая запись: имя, услуга, начало–конец, «До визита N мин»;
 *     остальные записи дня ниже, спокойнее (`upcoming_today`);
 *   - «Сейчас по расписанию»: текущая запись теми же полями. Флаг сервера
 *     ставится ПО ЧАСАМ (dashboard.py), поэтому не «идёт визит» и без «До
 *     конца ≈» — макет прямо запрещает состояние «визит идёт» и таймер;
 *   - «На сегодня записей нет» — только текст: кнопка «Добавить запись»
 *     появится с М-3 (DRF-2155). Сегодня тап по свободному окну открывает
 *     «недоступно», а не создание — кнопка сюда была бы ложью (DRF-1181);
 *   - «Сегодня выходной» + «Рабочие часы →» (соло → экран часов, салонный →
 *     «Расписание», где заявка владельцу);
 *   - рамка дня не прочитана (`states.day_off === null`) — «Не удалось
 *     проверить расписание» + «Проверить снова»: молчание источника — не
 *     пустой день и не выходной (DRF-1111);
 *   - день прошёл — «Вы провели N клиентов. Хороший день.» (без действий).
 *
 * Снято с экрана (макет + §50 п.5 «прямой переписки мастера с клиентом нет»):
 * «ТРЕБУЮТ ВНИМАНИЯ» (переписки), значок 💬 в шапке, «Открыть диалог ›», тап
 * по записи → переписка, «ЭТА НЕДЕЛЯ» с рейтингом, `PayoutPreviewCard`, мёртвая
 * «Заметка к визиту ›», «Сказала: «…»», «⚠ Постоянный клиент». Компоненты
 * `PayoutPreviewCard` / `IconMessage` живут дальше — с экрана сняты, не удалены.
 * Карточка записи — имя, услуга, время; сторож на набор полей — в тестах.
 * Тап по карточке → «Детали записи» `/master|solo/bookings/:id` (DRF-2156,
 * М-4); «До визита …» — общим форматтером DRF-1185 («1 ч 20 мин», §61);
 * дата в шапке — тем же стилем, что в деталях («20 сентября · воскресенье»,
 * DRF-2179).
 *
 * State branches:
 *   - loading            → 3 skeleton cards
 *   - offline / 5xx      → stale data banner + retry
 *   - permission denied  → «Этот диалог не для вас» (cross-master deep-link)
 *
 * Bridge API (§M1 lines 359–363):
 *   - WebApp.BackButton.hide() on mount (root screen)
 *   - WebApp.HapticFeedback.selectionChanged() on card tap
 *   - Pull-to-refresh → HapticFeedback.impactOccurred('soft')
 *   - No enableClosingConfirmation (no dirty state)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../lib/api";
import { salonOwnerHint } from "../lib/salonOwnerHint";
import {
  getDashboard,
  type DashboardActiveVisit,
  type DashboardNextVisit,
  type DashboardResponse,
  type DashboardUpcomingVisit,
} from "../lib/master-api";
import {
  hapticImpact,
  hapticSelection,
  setBackButton,
  signalReady,
} from "../lib/max-sdk";
import { AvatarSheet } from "../components/AvatarSheet";
import { MasterBookingCard } from "../components/master/MasterBookingCard";
import { SystemState } from "../components/master/SystemState";
import { MasterTabBar } from "../components/MasterTabBar";
import { useOnline } from "../hooks/useOnline";
import { useMasterAvatarItems } from "../hooks/useMasterAvatarItems";
import { AcceptingBookingsToggle } from "../components/AcceptingBookingsToggle";
import { SetupProgressCard } from "../components/SetupProgressCard";
import {
  formatDateDotWeekdayRu,
  formatDurationRu,
  formatTimeHM,
  joinClientName,
} from "../lib/masterDateFormat";

// --- Russian copy (VERBATIM from §M1) ------------------------------------

const COPY = {
  // DRF-2152 — тексты блока дня, по макету DRF-1182 дословно где он их даёт.
  day: {
    region: "Сегодня",
    scheduledNow: "Сейчас по расписанию",
    next: "Ближайшая запись",
    later: "Дальше сегодня",
    // §61: формат DRF-1185 («1 ч 20 мин») общим helper'ом с «Деталями записи».
    untilVisit: (min: number) =>
      min > 0 ? `До визита ${formatDurationRu(min)}` : "Уже сейчас",
    noVisits: "На сегодня записей нет",
    // DRF-2155 (М-3) — дверь в «Новую запись» (макет DRF-1182/1184).
    addBooking: "Добавить запись",
    dayOff: "Сегодня выходной",
    hoursCta: "Рабочие часы →",
    frameUnknown: "Не удалось проверить расписание",
    recheck: "Проверить снова",
  },
  empty: {
    // The admin is named by the salon, never by a hardcoded first name — see
    // lib/salonOwnerHint.ts. Nominative + non-past verb («настраивает») so the
    // sentence works for any tenant string.
    noServices: (ownerHint: string) =>
      `Вам ещё не назначили услуги. Их настраивает ${ownerHint} — напишите в MAX, если думаете, что это ошибка.`,
    noServicesCta: "Написать администратору салона",
  },
  dayDone: {
    body: (n: number, time: string | null, firstName: string | null) =>
      n > 0
        ? firstName && time
          ? `Вы провели ${n} ${pluralRu(n, "клиента", "клиентов", "клиентов")}. Хороший день. Завтра первая запись ${time} — ${firstName}.`
          : `Вы провели ${n} ${pluralRu(n, "клиента", "клиентов", "клиентов")}. Хороший день.`
        : "Хороший день. Завтра увидимся.",
  },
  // Системные состояния (загрузка / ошибка / нет прав / нет сети / устарело)
  // — только через SystemState и его словарь (DRF-2157, макет DRF-1181 п.10).
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
  // Реактивно: сеть вернулась — полоса сама сменится на «Не удалось обновить» с повтором.
  const online = useOnline();

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

  // DRF-2152: соло и салонный мастер делят экран; поверхность — по адресу.
  const location = useLocation();
  const isSolo = location.pathname.startsWith("/solo/");

  // DRF-2156 (М-4): тап по записи → «Детали записи» своей поверхности.
  const bookingHref = useCallback(
    (bookingId: string) =>
      `${isSolo ? "/solo" : "/master"}/bookings/${encodeURIComponent(bookingId)}`,
    [isSolo],
  );

  // «Рабочие часы →» на выходном: соло правит часы сам, салонный — подаёт
  // заявку владельцу на экране «Расписание».
  const onHoursCta = useCallback(() => {
    hapticSelection();
    navigate(isSolo ? "/solo/working-hours" : "/master/schedule");
  }, [navigate, isSolo]);

  // Вход в раздел «Ayla» (DRF-1180). Временно карточкой, а не вкладкой:
  // нижняя навигация станет трёхразделной вместе с DRF-1255.
  const onAylaOpen = useCallback(() => {
    hapticSelection();
    navigate("/master/ayla");
  }, [navigate]);

  // --- Resolve current data + flags --------------------------------------

  const data: DashboardResponse | null =
    phase.kind === "ready" || phase.kind === "stale_with_data"
      ? phase.data
      : null;
  const isStale = phase.kind === "stale_with_data";

  // --- Branch rendering --------------------------------------------------

  // Системные состояния — один компонент на все мастерские экраны (DRF-2157).
  if (phase.kind === "error_permission") {
    return (
      <div className="master-dashboard">
        <SystemState kind="forbidden" />
      </div>
    );
  }

  if (phase.kind === "loading" || phase.kind === "error_initial") {
    return (
      <DashboardFrame
        scrollRef={scrollContainerRef}
        onTouchStart={onTouchStart}
        onTouchEnd={onTouchEnd}
      >
        {phase.kind === "loading" ? (
          <SystemState kind="loading" />
        ) : (
          <SystemState
            kind="load_error"
            what="today"
            err={phase.err}
            busy={refreshing}
            onRetry={() => load(false)}
          />
        )}
        <TabBarBlank />
      </DashboardFrame>
    );
  }

  if (data === null) {
    // Defensive — every other phase should have data by now.
    return null;
  }

  // States.
  const {
    active_visit,
    next_visit,
    upcoming_today,
    inbox_preview,
    today_summary,
    tab_badges,
    states,
  } = data;
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
        profileHasOwnerPendingChange={
          tab_badges.profile_has_owner_pending_change
        }
      />

      {/* Данные есть, обновить не вышло: без сети — «Нет подключения», иначе «Не удалось обновить». */}
      {isStale ? (
        !online ? (
          <SystemState kind="offline" />
        ) : (
          <SystemState
            kind="load_error"
            err={phase.err}
            hasData
            busy={refreshing}
            onRetry={() => load(true)}
          />
        )
      ) : null}

      {/* DRF-2152 — состояние дня ПЕРВЫМ (макет DRF-1182). */}
      <DayBlock
        activeVisit={active_visit}
        nextVisit={next_visit}
        upcoming={upcoming_today ?? []}
        isDayDone={isDayDone}
        isEmptyToday={isEmptyToday}
        noServices={noServices}
        salonName={data.salon.name}
        dayOff={states.day_off}
        completedCount={today_summary.completed_count}
        totalClients={today_summary.total_clients_today}
        onHours={onHoursCta}
        onRecheck={() => load(true)}
        onAddBooking={() =>
          navigate(isSolo ? "/solo/booking/new" : "/master/booking/new")
        }
        bookingHref={bookingHref}
      />

      {/* DRF-1807 — карточка «Продолжить настройку», пока readiness не закрыт. */}
      <SetupProgressCard />

      {/* DRF-1845 — «Принимаю записи»: сам грузится, прячется при отказе. */}
      <AcceptingBookingsToggle />

      <AylaEntrySection onOpen={onAylaOpen} />

      <MasterTabBar
        scheduleHasPendingChange={tab_badges.schedule_has_pending_change}
      />
    </DashboardFrame>
  );
}

function AylaEntrySection({ onOpen }: { onOpen: () => void }) {
  return (
    <section className="master-dashboard__section">
      <button type="button" className="ayla-entry" onClick={onOpen}>
        <span>
          <span className="ayla-entry__title">Спросить Ayla</span>
          <span className="ayla-entry__sub">
            День, загрузка, свободные окна
          </span>
        </span>
        <span className="ayla-entry__chevron" aria-hidden="true">
          ›
        </span>
      </button>
    </section>
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

/**
 * Шапка дашборда (§M1 «Студия Карина [Анна ●]»).
 *
 * DRF-1848 (карта кабинета D01, D02): имя мастера — видимым текстом, а не
 * только подписью аватара; значок диалогов с числом непрочитанных. Число
 * берётся из того же `tab_badges.conversations_unread`, что и у вкладки
 * «Диалоги», и пишется тем же `unreadBadgeText` — второго источника нет.
 * Тап ведёт туда же, куда карточки входящих (`onInboxCardTap`).
 */
export function DashboardHeader({
  salonName,
  masterName,
  photoUrl,
  nowIso,
  profileHasOwnerPendingChange = false,
}: {
  salonName: string;
  masterName: string;
  photoUrl: string;
  nowIso: string;
  /** Точка на аватаре: владелец ждёт правок профиля (DRF-2121 — переехала с панели). */
  profileHasOwnerPendingChange?: boolean;
}) {
  // Spec §M1 lines 289-291: «Студия Карина [Анна ●] / Среда, 21 мая 14:42».
  // DRF-2121 (§28 п.3): аватар — кнопка, открывающая лист «Профиль · Со
  // студией · Настройки»; «Профиль» из нижней панели снят. Кнопка «Диалоги»
  // (💬 с бейджем) снята DRF-2152: прямой переписки мастера с клиентом нет
  // (§50 п.5, макет DRF-1182); /master/conversations живёт по прямой ссылке.
  const firstName = (masterName || "").split(/\s+/)[0] ?? "";
  // DRF-2127: адреса пунктов — по поверхности (/master/* или /solo/*).
  const avatarItems = useMasterAvatarItems();
  return (
    <header className="master-dashboard__header">
      <div className="master-dashboard__header-left">
        <div className="master-dashboard__salon">{salonName}</div>
        {/* DRF-2179 (§61 п.6): один стиль даты с «Деталями записи» — «20 сентября · воскресенье». */}
        <div className="master-dashboard__date">
          {formatDateDotWeekdayRu(nowIso)}
        </div>
      </div>
      <div className="master-dashboard__header-right">
        <div className="master-dashboard__who">
          {firstName ? (
            <div className="master-dashboard__name">{firstName}</div>
          ) : null}
          <AvatarSheet
            name={masterName}
            photoUrl={photoUrl}
            dot={profileHasOwnerPendingChange}
            items={avatarItems}
          />
        </div>
        <div className="master-dashboard__time">{formatTimeHM(nowIso)}</div>
      </div>
    </header>
  );
}

// ----------------------------------------------------------------------------
// Блок дня (DRF-2152, макет DRF-1182)
// ----------------------------------------------------------------------------
function DayBlock({
  activeVisit,
  nextVisit,
  upcoming,
  isDayDone,
  isEmptyToday,
  noServices,
  salonName,
  dayOff,
  completedCount,
  totalClients,
  onHours,
  onRecheck,
  onAddBooking,
  bookingHref,
}: {
  activeVisit: DashboardActiveVisit | null;
  nextVisit: DashboardNextVisit | null;
  upcoming: DashboardUpcomingVisit[];
  isDayDone: boolean;
  isEmptyToday: boolean;
  noServices: boolean;
  salonName: string | null;
  dayOff: boolean | null | undefined;
  completedCount: number;
  totalClients: number;
  onHours: () => void;
  onRecheck: () => void;
  onAddBooking: () => void;
  bookingHref: (bookingId: string) => string;
}) {
  let body: React.ReactNode;
  if (isDayDone) {
    body = (
      <DayDoneLine
        completedCount={completedCount}
        totalClients={totalClients}
      />
    );
  } else if (isEmptyToday) {
    if (dayOff === true) {
      body = (
        <>
          <p className="master-dashboard__empty-line">{COPY.day.dayOff}</p>
          <button
            type="button"
            className="btn-secondary master-dashboard__inline-cta"
            onClick={onHours}
          >
            {COPY.day.hoursCta}
          </button>
        </>
      );
    } else if (dayOff === null || dayOff === undefined) {
      // Каталог не ответил: «не знаю» — не «свободный день» (DRF-1111).
      body = (
        <>
          <p className="master-dashboard__empty-line">
            {COPY.day.frameUnknown}
          </p>
          <button
            type="button"
            className="btn-secondary master-dashboard__inline-cta"
            onClick={onRecheck}
          >
            {COPY.day.recheck}
          </button>
        </>
      );
    } else if (noServices) {
      body = <NoServicesLine salonName={salonName} />;
    } else {
      body = (
        <>
          <p className="master-dashboard__empty-line">{COPY.day.noVisits}</p>
          <button
            type="button"
            className="btn-secondary master-dashboard__inline-cta"
            onClick={onAddBooking}
          >
            {COPY.day.addBooking}
          </button>
        </>
      );
    }
  } else {
    body = (
      <>
        {activeVisit ? (
          <ScheduledNowCard visit={activeVisit} href={bookingHref} />
        ) : null}
        {nextVisit ? (
          <NextVisitCard visit={nextVisit} href={bookingHref} />
        ) : null}
        {upcoming.length > 0 ? (
          <LaterTodayList visits={upcoming} href={bookingHref} />
        ) : null}
      </>
    );
  }
  return (
    <section
      className="master-dashboard__section master-dashboard__day"
      aria-labelledby="m1-day"
    >
      <h2 className="master-dashboard__section-title" id="m1-day">
        {COPY.day.region}
      </h2>
      {body}
    </section>
  );
}

/**
 * Карточка записи — общая MasterBookingCard (DRF-1181 п.5, DRF-2157): время,
 * имя, услуга, длительность; ссылка на «Детали записи» (DRF-2156).
 */
function VisitRow({
  first,
  lastInitial,
  service,
  startIso,
  endIso,
  durationMin,
  to,
  quiet = false,
}: {
  first: string;
  lastInitial: string;
  service: string;
  startIso: string;
  endIso: string;
  durationMin: number;
  to: string;
  quiet?: boolean;
}) {
  return (
    <MasterBookingCard
      variant="today"
      clientName={joinClientName(first, lastInitial)}
      serviceName={service}
      startIso={startIso}
      endIso={endIso}
      durationMin={durationMin}
      to={to}
      quiet={quiet}
    />
  );
}

function ScheduledNowCard({
  visit,
  href,
}: {
  visit: DashboardActiveVisit;
  href: (bookingId: string) => string;
}) {
  // Конец — по часам: начало + длительность; «До конца ≈» не рисуется.
  const end = new Date(
    new Date(visit.started_at).getTime() + visit.duration_min * 60_000,
  );
  return (
    <div className="master-dashboard__day-part">
      <p className="master-dashboard__day-label">{COPY.day.scheduledNow}</p>
      <VisitRow
        first={visit.client_first_name}
        lastInitial={visit.client_last_initial}
        service={visit.service_name}
        startIso={visit.started_at}
        endIso={end.toISOString()}
        durationMin={visit.duration_min}
        to={href(visit.booking_id)}
      />
    </div>
  );
}

function NextVisitCard({
  visit,
  href,
}: {
  visit: DashboardNextVisit;
  href: (bookingId: string) => string;
}) {
  const endIso =
    visit.end_at ||
    new Date(
      new Date(visit.visit_at).getTime() + visit.duration_min * 60_000,
    ).toISOString();
  return (
    <div className="master-dashboard__day-part">
      <p className="master-dashboard__day-label">{COPY.day.next}</p>
      <VisitRow
        first={visit.client_first_name}
        lastInitial={visit.client_last_initial}
        service={visit.service_name}
        startIso={visit.visit_at}
        endIso={endIso}
        durationMin={visit.duration_min}
        to={href(visit.booking_id)}
      />
      <p className="master-dashboard__day-until">
        {COPY.day.untilVisit(visit.minutes_until ?? 0)}
      </p>
    </div>
  );
}

function LaterTodayList({
  visits,
  href,
}: {
  visits: DashboardUpcomingVisit[];
  href: (bookingId: string) => string;
}) {
  return (
    <div className="master-dashboard__day-part">
      <p className="master-dashboard__day-label">{COPY.day.later}</p>
      <ul className="master-dashboard__day-list" aria-label={COPY.day.later}>
        {visits.map((v) => (
          <li key={v.booking_id}>
            <VisitRow
              first={v.client_first_name}
              lastInitial={v.client_last_initial}
              service={v.service_name}
              startIso={v.visit_at}
              endIso={v.end_at}
              durationMin={Math.round(
                (new Date(v.end_at).getTime() -
                  new Date(v.visit_at).getTime()) /
                  60_000,
              )}
              to={href(v.booking_id)}
              quiet
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

function NoServicesLine({ salonName }: { salonName: string | null }) {
  return (
    <div className="callout" role="status">
      <p style={{ margin: 0 }}>
        {COPY.empty.noServices(salonOwnerHint(salonName))}
      </p>
    </div>
  );
}

function DayDoneLine({
  completedCount,
  totalClients,
}: {
  completedCount: number;
  totalClients: number;
}) {
  const n = Math.max(completedCount, totalClients);
  return (
    <p className="master-dashboard__empty-line">
      {COPY.dayDone.body(n, null, null)}
    </p>
  );
}

/** Empty tab bar shown while data loads — keeps layout stable. */
function TabBarBlank() {
  return <MasterTabBar scheduleHasPendingChange={false} />;
}
