/**
 * Master M3 schedule — day / week / month views + mark-unavailable flow.
 *
 * Route: /master/schedule
 *
 * Spec source: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M3
 * (lines 380-477). State-matrix discipline per
 * docs/design/policies/customer-first-touch-and-mini-app-states.md §7.
 *
 * Spec quote (§M3, line 391-394):
 *
 *     «[День] [Неделя] [Месяц]  Segmented control
 *      ◀  Среда, 21 мая  ▶      Date stepper»
 *
 * Spec quote (§M3, line 458):
 *
 *     «Mark unavailable / off-day: master taps empty slot → sheet
 *      `Помечу как недоступно` → confirmation → server marks slot blocked
 *      → owner notified (audit + bot DM)»
 *
 * DRF-2155 (М-3): the MAIN tap on a free window now opens «Новая запись»
 * (mockup DRF-1183/1184 — «Выбранное окно»); the unavailability request
 * above stays as the secondary «Недоступно» action on the window card.
 *
 * Backend contracts:
 *   GET  /api/v1/master/schedule?from&to
 *   POST /api/v1/master/availability
 *   GET  /api/v1/master/availability/pending
 *
 * Bridge API (§M3 lines 468-471):
 *   - BackButton.show() (not root)
 *   - HapticFeedback.selectionChanged() on date step
 *   - HapticFeedback.impactOccurred('heavy') on availability-request confirm
 *
 * Booking tap → «Детали записи» `/master|solo/bookings/:id` (DRF-2156, М-4;
 * макет DRF-1183 «нажатие на запись → экран деталей»). Раньше вело в список
 * переписок — снято: прямой переписки мастера с клиентом нет (§50 п.5).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { DEFAULT_SALON_OWNER_HINT } from "../lib/salonOwnerHint";
import {
  getMasterSchedule,
  getPendingAvailability,
  requestAvailability,
  type AvailabilityReasonClass,
  type MasterScheduleResponse,
  type PendingAvailabilityItem,
  type ScheduleBooking,
  type ScheduleDay,
  type ScheduleFreeWindow,
} from "../lib/master-api";
import {
  hapticImpact,
  hapticSelection,
  setBackButton,
  signalReady,
} from "../lib/max-sdk";
import { MasterAvatar } from "../components/MasterAvatar";
import { MasterBookingCard } from "../components/master/MasterBookingCard";
import { SystemState } from "../components/master/SystemState";
import { MasterTabBar } from "../components/MasterTabBar";
import { Snackbar } from "../components/Snackbar";
import {
  addDays,
  formatMonthHeaderRu,
  formatWeekHeaderRu,
  formatWeekRangeRu,
  formatTimeHM,
  formatYmdLocal,
  joinClientName,
  localHmFromIso,
  parseLocalYmd,
  startOfWeekMonday,
  weekdayShortRu,
} from "../lib/masterDateFormat";

function pluralRu(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

// --- Russian copy (VERBATIM from §M3) -------------------------------------

const COPY = {
  title: "Расписание",
  today: "Сегодня",
  segments: {
    day: "День",
    week: "Неделя",
    month: "Месяц",
  },
  freeWindow: (min: number) => `свободно · ${min} мин`,
  // DRF-2155 (М-3): вторичное действие в карточке окна — заявка о недоступности.
  unavailable: "Недоступно",
  outsideHours: "вне рабочего времени",
  offDayLabel: "выходной",
  conflictTapHint: "уточнить у админа",
  conflictBanner: "⚠ Конфликт расписания — посмотрите",
  emptyDay: "Свободный день. Отдыхайте.",
  // DRF-2194 — шкала дня и линия текущего времени (DRF-1183).
  dayListLabel: "Расписание дня",
  nowLabel: (hm: string) => `Сейчас ${hm}`,
  // The admin is named by the salon, never by a hardcoded first name — see
  // lib/salonOwnerHint.ts. `MasterScheduleResponse` carries no salon, so both
  // call sites pass the neutral default; the parameter is here so that wiring a
  // real name later is a one-line change at each call site.
  emptyWeek: (ownerHint: string) =>
    `Расписание ещё не составлено. ${ownerHint} добавит вас на смены.`,
  pendingBanner: (start: string, end: string) =>
    `У вас запрос на выходной ${start}—${end}, ждёт одобрения`,
  unavailableSheet: {
    title: (date: string, start: string, end: string) =>
      `Помечу ${date} ${start}—${end} как недоступно`,
    reasonLabel: "Причина",
    reasons: {
      vacation: "Отпуск",
      sick: "Болезнь",
      personal: "Личное",
      other: "Другое",
    } as const,
    commentLabel: "Комментарий (необязательно)",
    cancel: "Отменить",
    submit: "Запросить",
    success: (ownerHint: string) =>
      `Запрос отправлен. ${ownerHint} увидит и подтвердит.`,
    error: "Не получилось отправить запрос. Попробуйте снова.",
  },
  weekClientsLabel: (n: number) =>
    n === 0 ? "—" : `${n} ${pluralRu(n, "клиент", "клиента", "клиентов")}`,
  weekFreeLabel: (n: number) =>
    n === 0 ? "0 окон" : `${n} ${pluralRu(n, "окно", "окна", "окон")}`,
  // Системные состояния — через SystemState (DRF-2157, макет DRF-1181 п.10).
  weekDayLink: "Открыть день ›",
  weekDayActiveSuffix: "(сегодня)",
};

const REASON_VALUES: AvailabilityReasonClass[] = [
  "vacation",
  "sick",
  "personal",
  "other",
];

// --- View state -----------------------------------------------------------

type SegmentView = "day" | "week" | "month";

type Phase =
  | { kind: "loading" }
  | {
      kind: "ready";
      data: MasterScheduleResponse;
      pending: PendingAvailabilityItem[];
    }
  | { kind: "error_initial"; err: unknown };

interface UnavailableSheetState {
  open: boolean;
  date: string; // YYYY-MM-DD
  startHm: string; // HH:MM
  endHm: string; // HH:MM
  reason: AvailabilityReasonClass;
  comment: string;
  submitting: boolean;
}

const EMPTY_SHEET: UnavailableSheetState = {
  open: false,
  date: "",
  startHm: "",
  endHm: "",
  reason: "personal",
  comment: "",
  submitting: false,
};

// --- Component ------------------------------------------------------------

export function MasterScheduleScreen() {
  const location = useLocation();
  const navigate = useNavigate();

  const [view, setView] = useState<SegmentView>("day");
  // Currently-focused local date. Day view = that day; Week view = anchor
  // inside that week; Month view = anchor inside that month.
  const [anchor, setAnchor] = useState<Date>(() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  });

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [sheet, setSheet] = useState<UnavailableSheetState>(EMPTY_SHEET);
  const [snackbar, setSnackbar] = useState<{
    visible: boolean;
    message: string;
    isError?: boolean;
  }>({ visible: false, message: "" });

  // BackButton: NOT root, so show.
  useEffect(() => {
    setBackButton(true);
    signalReady();
    return () => setBackButton(false);
  }, []);

  // Compute the requested range based on the current view.
  const range = useMemo(() => computeRange(view, anchor), [view, anchor]);

  // --- Data load --------------------------------------------------------

  const load = useCallback(async () => {
    setPhase({ kind: "loading" });
    try {
      const [data, pending] = await Promise.all([
        getMasterSchedule({ from: range.from, to: range.to }),
        getPendingAvailability().catch((err) => {
          // Pending list is auxiliary; degrade silently rather than block
          // the calendar render. Banner just won't show.
          console.warn("[schedule] pending fetch failed", err);
          return { items: [] };
        }),
      ]);
      setPhase({ kind: "ready", data, pending: pending.items });
    } catch (err) {
      setPhase({ kind: "error_initial", err });
    }
  }, [range.from, range.to]);

  useEffect(() => {
    void load();
  }, [load]);

  // --- Date stepping ----------------------------------------------------

  const stepBy = useCallback((deltaDays: number) => {
    hapticSelection();
    setAnchor((prev) => addDays(prev, deltaDays));
  }, []);

  const onPrev = useCallback(() => {
    if (view === "day") stepBy(-1);
    else if (view === "week") stepBy(-7);
    else stepBy(-30);
  }, [view, stepBy]);

  const onNext = useCallback(() => {
    if (view === "day") stepBy(1);
    else if (view === "week") stepBy(7);
    else stepBy(30);
  }, [view, stepBy]);

  const onJumpToday = useCallback(() => {
    hapticSelection();
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    setAnchor(d);
  }, []);

  const onSegmentChange = useCallback((v: SegmentView) => {
    hapticSelection();
    setView(v);
  }, []);

  // --- Booking → «Детали записи» (DRF-2156, М-4) -----------------------

  // Макет DRF-1183 «нажатие на запись → экран деталей записи». Раньше вело в
  // список переписок — прямой переписки мастера с клиентом нет (§50 п.5).
  // Соло и салонный мастер делят экран; поверхность — по адресу.
  const isSolo = location.pathname.startsWith("/solo/");
  const bookingHref = useCallback(
    (booking: ScheduleBooking) =>
      `${isSolo ? "/solo" : "/master"}/bookings/${encodeURIComponent(booking.booking_id)}`,
    [isSolo],
  );

  // --- Free-window tap → «Новая запись» (DRF-2155, М-3) -------------------

  // Макет DRF-1183/1184: тап по свободному окну — создание записи с
  // подписью «Выбранное окно». До М-3 главный тап открывал лист «Помечу как
  // недоступно» — противоположно макету; заявка о недоступности осталась,
  // но вторичным действием в карточке окна (`onFreeSlotUnavailable`).
  const onFreeSlotTap = useCallback(
    (day: ScheduleDay, window: ScheduleFreeWindow) => {
      hapticSelection();
      const qs = new URLSearchParams({
        date: day.date,
        from: window.start,
        to: window.end,
      });
      navigate(`${isSolo ? "/solo" : "/master"}/booking/new?${qs.toString()}`);
    },
    [navigate, isSolo],
  );

  // --- Free-window «Недоступно» → mark-unavailable sheet ------------------

  const onFreeSlotUnavailable = useCallback(
    (day: ScheduleDay, window: ScheduleFreeWindow) => {
      hapticSelection();
      setSheet({
        ...EMPTY_SHEET,
        open: true,
        date: day.date,
        startHm: window.start,
        endHm: window.end,
        reason: "personal",
      });
    },
    [],
  );

  const onMarkOffDay = useCallback((day: ScheduleDay) => {
    hapticSelection();
    setSheet({
      ...EMPTY_SHEET,
      open: true,
      date: day.date,
      startHm: day.working_hours?.start ?? "10:00",
      endHm: day.working_hours?.end ?? "19:00",
      reason: "vacation",
    });
  }, []);

  const closeSheet = useCallback(
    () => setSheet((prev) => ({ ...prev, open: false })),
    [],
  );

  const submitUnavailable = useCallback(async () => {
    if (!sheet.open || sheet.submitting) return;
    // Build tenant-local ISO with no TZ designator; backend parses naive
    // datetimes as UTC, which works because the master sees local times
    // here and the backend re-projects on read. We deliberately do NOT
    // attach a TZ offset because the schedule endpoint returns HH:MM
    // strings without offset, and the user's intent is "this calendar
    // slot". If we attached the user's TZ, the backend storage would
    // shift on DST changes — by passing local-naive we let the salon's
    // tenant TZ govern.
    const localIso = (date: string, hm: string): string => {
      const parts = hm.split(":");
      const h = Number(parts[0] ?? "0");
      const m = Number(parts[1] ?? "0");
      return `${date}T${pad2(h)}:${pad2(m)}:00`;
    };
    setSheet((prev) => ({ ...prev, submitting: true }));
    try {
      await requestAvailability({
        start: localIso(sheet.date, sheet.startHm),
        end: localIso(sheet.date, sheet.endHm),
        reason_class: sheet.reason,
        reason_text: sheet.comment.trim() || undefined,
      });
      hapticImpact("heavy");
      setSnackbar({
        visible: true,
        message: COPY.unavailableSheet.success(DEFAULT_SALON_OWNER_HINT),
      });
      setSheet(EMPTY_SHEET);
      // Refresh pending list + schedule (in case backend already created
      // a blocking exception for an approved request — current backend
      // returns a pending row, so the calendar is unchanged but the
      // banner appears).
      void load();
    } catch (err) {
      console.warn("[schedule] availability request failed", err);
      setSnackbar({
        visible: true,
        message: COPY.unavailableSheet.error,
        isError: true,
      });
      setSheet((prev) => ({ ...prev, submitting: false }));
    }
  }, [sheet, load]);

  // --- Rendering --------------------------------------------------------

  return (
    <>
      <div className="master-dashboard">
        <ScheduleHeader
          view={view}
          anchor={anchor}
          onSegmentChange={onSegmentChange}
          onPrev={onPrev}
          onNext={onNext}
          onJumpToday={onJumpToday}
        />
        {phase.kind === "loading" ? (
          <SystemState kind="loading" />
        ) : phase.kind === "error_initial" ? (
          <SystemState
            kind="load_error"
            what="schedule"
            err={phase.err}
            onRetry={() => void load()}
          />
        ) : (
          <ScheduleBody
            view={view}
            anchor={anchor}
            data={phase.data}
            pending={phase.pending}
            bookingHref={bookingHref}
            onFreeSlotTap={onFreeSlotTap}
            onFreeSlotUnavailable={onFreeSlotUnavailable}
            onMarkOffDay={onMarkOffDay}
            onSwitchToDay={(d) => {
              hapticSelection();
              setAnchor(d);
              setView("day");
            }}
          />
        )}
      </div>
      {sheet.open ? (
        <UnavailableSheet
          state={sheet}
          onChange={setSheet}
          onClose={closeSheet}
          onSubmit={submitUnavailable}
        />
      ) : null}
      <Snackbar
        visible={snackbar.visible}
        message={snackbar.message}
        durationMs={4500}
        onTimeout={() => setSnackbar({ visible: false, message: "" })}
      />
      <MasterTabBar
        scheduleHasPendingChange={
          phase.kind === "ready" && phase.pending.length > 0
        }
      />
    </>
  );
}

// --- Helpers --------------------------------------------------------------

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

function computeRange(
  view: SegmentView,
  anchor: Date,
): { from: string; to: string } {
  if (view === "day") {
    const ymd = formatYmdLocal(anchor);
    return { from: ymd, to: ymd };
  }
  if (view === "week") {
    const start = startOfWeekMonday(anchor);
    const end = addDays(start, 6);
    return { from: formatYmdLocal(start), to: formatYmdLocal(end) };
  }
  // Month: clamp to ≤ 31 days (backend limit). We anchor on month start
  // and request 30 days forward (inclusive 31).
  const start = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  // Backend MAX_RANGE_DAYS = 31, exclusive in the check (`>=`), so we
  // pass at most 30 day-diff (31 days inclusive).
  const lastDay = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
  const safeEnd =
    (lastDay.getTime() - start.getTime()) / 86400000 < 31
      ? lastDay
      : addDays(start, 30);
  return { from: formatYmdLocal(start), to: formatYmdLocal(safeEnd) };
}

// --- Header ---------------------------------------------------------------

function ScheduleHeader({
  view,
  anchor,
  onSegmentChange,
  onPrev,
  onNext,
  onJumpToday,
}: {
  view: SegmentView;
  anchor: Date;
  onSegmentChange: (v: SegmentView) => void;
  onPrev: () => void;
  onNext: () => void;
  onJumpToday: () => void;
}) {
  return (
    <header className="schedule-header">
      <div className="schedule-header__top">
        <h1 className="screen__title schedule-header__title">{COPY.title}</h1>
        <button
          type="button"
          className="btn-secondary schedule-header__today"
          onClick={onJumpToday}
        >
          {COPY.today}
        </button>
        {/* DRF-2121 (§28 п.3): вход в профиль — аватар на каждом из трёх разделов. */}
        <MasterAvatar />
      </div>
      <div
        className="schedule-segments"
        role="tablist"
        aria-label="Вид расписания"
      >
        {(["day", "week", "month"] as const).map((v) => (
          <button
            key={v}
            type="button"
            role="tab"
            aria-selected={view === v}
            className={`schedule-segments__tab${view === v ? " schedule-segments__tab--active" : ""}`}
            onClick={() => onSegmentChange(v)}
          >
            {COPY.segments[v]}
          </button>
        ))}
      </div>
      <div className="schedule-stepper">
        <button
          type="button"
          className="schedule-stepper__btn"
          onClick={onPrev}
          aria-label="Назад"
        >
          ◀
        </button>
        <span className="schedule-stepper__label">
          {renderStepperLabel(view, anchor)}
        </span>
        <button
          type="button"
          className="schedule-stepper__btn"
          onClick={onNext}
          aria-label="Вперёд"
        >
          ▶
        </button>
      </div>
    </header>
  );
}

function renderStepperLabel(view: SegmentView, anchor: Date): string {
  if (view === "day") {
    // Spec line 393: «Среда, 21 мая»
    const weekdaysFull = [
      "Воскресенье",
      "Понедельник",
      "Вторник",
      "Среда",
      "Четверг",
      "Пятница",
      "Суббота",
    ];
    const monthsGen = [
      "января",
      "февраля",
      "марта",
      "апреля",
      "мая",
      "июня",
      "июля",
      "августа",
      "сентября",
      "октября",
      "ноября",
      "декабря",
    ];
    return `${weekdaysFull[anchor.getDay()] ?? ""}, ${anchor.getDate()} ${monthsGen[anchor.getMonth()] ?? ""}`;
  }
  if (view === "week") {
    const start = startOfWeekMonday(anchor);
    const end = addDays(start, 6);
    return formatWeekRangeRu(start, end);
  }
  return formatMonthHeaderRu(anchor);
}

// --- Body — dispatches by view -------------------------------------------

function ScheduleBody({
  view,
  anchor,
  data,
  pending,
  bookingHref,
  onFreeSlotTap,
  onFreeSlotUnavailable,
  onMarkOffDay,
  onSwitchToDay,
}: {
  view: SegmentView;
  anchor: Date;
  data: MasterScheduleResponse;
  pending: PendingAvailabilityItem[];
  bookingHref: (b: ScheduleBooking) => string;
  onFreeSlotTap: (day: ScheduleDay, window: ScheduleFreeWindow) => void;
  onFreeSlotUnavailable: (day: ScheduleDay, window: ScheduleFreeWindow) => void;
  onMarkOffDay: (day: ScheduleDay) => void;
  onSwitchToDay: (date: Date) => void;
}) {
  return (
    <>
      <PendingBanner pending={pending} />
      {view === "day" ? (
        <DayView
          day={pickDay(data, anchor)}
          bookingHref={bookingHref}
          onFreeSlotTap={onFreeSlotTap}
          onFreeSlotUnavailable={onFreeSlotUnavailable}
          onMarkOffDay={onMarkOffDay}
        />
      ) : view === "week" ? (
        <WeekView data={data} anchor={anchor} onSwitchToDay={onSwitchToDay} />
      ) : (
        <MonthView data={data} anchor={anchor} onSwitchToDay={onSwitchToDay} />
      )}
    </>
  );
}

function pickDay(
  data: MasterScheduleResponse,
  anchor: Date,
): ScheduleDay | null {
  const ymd = formatYmdLocal(anchor);
  return data.days.find((d) => d.date === ymd) ?? null;
}

// --- Pending availability banner -----------------------------------------

function PendingBanner({ pending }: { pending: PendingAvailabilityItem[] }) {
  const visible = pending.filter((p) => p.status === "pending");
  if (visible.length === 0) return null;
  const first = visible[0]!;
  const startText = first.requested_start
    ? formatBannerDate(first.requested_start)
    : "?";
  const endText = first.requested_end
    ? formatBannerDate(first.requested_end)
    : "?";
  return (
    <div
      className="master-dashboard__stale-banner"
      role="status"
      style={{ background: "var(--c-surface-2)" }}
    >
      <span>{COPY.pendingBanner(startText, endText)}</span>
    </div>
  );
}

function formatBannerDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const months = [
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
  ];
  return `${d.getDate()} ${months[d.getMonth()] ?? ""}`;
}

// --- Day view -------------------------------------------------------------

function DayView({
  day,
  bookingHref,
  onFreeSlotTap,
  onFreeSlotUnavailable,
  onMarkOffDay,
}: {
  day: ScheduleDay | null;
  bookingHref: (b: ScheduleBooking) => string;
  onFreeSlotTap: (day: ScheduleDay, window: ScheduleFreeWindow) => void;
  onFreeSlotUnavailable: (day: ScheduleDay, window: ScheduleFreeWindow) => void;
  onMarkOffDay: (day: ScheduleDay) => void;
}) {
  if (day === null) {
    // Server returned no day-row for this date (out of any operational
    // window). Render the same «free day» empty.
    return (
      <section className="master-dashboard__section">
        <p className="master-dashboard__empty-line">{COPY.emptyDay}</p>
      </section>
    );
  }
  if (day.is_off_day) {
    return (
      <section className="master-dashboard__section">
        <div className="callout" role="status">
          <p style={{ margin: 0 }}>
            <strong>{COPY.offDayLabel}</strong>
          </p>
        </div>
      </section>
    );
  }
  const noContent =
    day.bookings.length === 0 &&
    day.blocks.length === 0 &&
    day.free_windows.length === 0;
  // Always render a stable timeline based on working_hours. Bookings,
  // blocks, free-windows are merged in chronological order; the
  // working-window bounds drive the gray-outside-hours surface.
  // DRF-2194 (DRF-1183): на сегодняшнем дне — линия «сейчас» между карточками
  // по HH:MM. Часы устройства, на момент рендера; не тикает.
  const items = buildDayItems(day, nowMarkerFor(day));
  return (
    <section className="master-dashboard__section">
      {day.conflicts.length > 0 ? <ConflictBanner /> : null}
      {noContent ? (
        <p className="master-dashboard__empty-line">{COPY.emptyDay}</p>
      ) : null}
      <ul className="schedule-day" aria-label={COPY.dayListLabel}>
        {items.map((item, idx) => (
          <li key={idx}>
            {item.kind === "now" ? (
              <NowLine hm={item.startHm} />
            ) : item.kind === "booking" ? (
              <MasterBookingCard
                variant="schedule"
                clientName={joinClientName(
                  item.booking.client_first_name,
                  item.booking.client_last_initial,
                )}
                serviceName={item.booking.service_name}
                startIso={item.booking.visit_at}
                durationMin={item.booking.duration_min}
                to={bookingHref(item.booking)}
              />
            ) : item.kind === "free" ? (
              <FreeWindowCard
                window={item.window}
                onTap={() => onFreeSlotTap(day, item.window)}
                onUnavailable={() => onFreeSlotUnavailable(day, item.window)}
              />
            ) : (
              <BlockCard block={item.block} />
            )}
          </li>
        ))}
      </ul>
      {day.working_hours ? (
        <button
          type="button"
          className="btn-secondary master-dashboard__inline-cta"
          onClick={() => onMarkOffDay(day)}
        >
          Запросить выходной на этот день
        </button>
      ) : null}
    </section>
  );
}

interface DayItemBooking {
  kind: "booking";
  booking: ScheduleBooking;
  startHm: string;
}
interface DayItemFree {
  kind: "free";
  window: ScheduleFreeWindow;
  startHm: string;
}
interface DayItemBlock {
  kind: "block";
  block: ScheduleDay["blocks"][number];
  startHm: string;
}
/** Линия текущего времени (DRF-1183) — элемент шкалы, не карточка. */
interface DayItemNow {
  kind: "now";
  startHm: string;
}

type DayItem = DayItemBooking | DayItemFree | DayItemBlock | DayItemNow;

/**
 * «Сейчас» для дня: HH:MM по часам устройства, если день — сегодня; иначе
 * null. Источник — устройство: ответ расписания серверного now не несёт
 * (отступление (т), у владельца). Линия — про часы, не про состояние записи.
 */
function nowMarkerFor(day: ScheduleDay, now: Date = new Date()): string | null {
  return day.date === formatYmdLocal(now) ? formatTimeHM(now.toISOString()) : null;
}

function buildDayItems(day: ScheduleDay, nowHm: string | null = null): DayItem[] {
  const items: DayItem[] = [];
  if (nowHm !== null) items.push({ kind: "now", startHm: nowHm });
  for (const b of day.bookings) {
    items.push({
      kind: "booking",
      booking: b,
      startHm: localHmFromIso(b.visit_at),
    });
  }
  for (const f of day.free_windows) {
    items.push({ kind: "free", window: f, startHm: f.start });
  }
  for (const bl of day.blocks) {
    items.push({ kind: "block", block: bl, startHm: localHmFromIso(bl.start) });
  }
  // Стабильная сортировка: при равном времени линия «сейчас» стоит перед
  // карточкой, начинающейся в эту минуту.
  items.sort(
    (a, b) => a.startHm.localeCompare(b.startHm) || Number(b.kind === "now") - Number(a.kind === "now"),
  );
  return items;
}

/** Тонкая красная линия с подписью времени — DRF-1183 «Текущее время». */
function NowLine({ hm }: { hm: string }) {
  return (
    <div className="schedule-now" role="separator" aria-label={COPY.nowLabel(hm)}>
      <span className="schedule-now__time">{hm}</span>
      <span className="schedule-now__dot" aria-hidden="true" />
      <span className="schedule-now__line" aria-hidden="true" />
    </div>
  );
}

function ConflictBanner() {
  return (
    <div
      className="callout callout--danger"
      role="alert"
      style={{ marginBottom: "var(--s-3)" }}
    >
      <p style={{ margin: 0 }}>{COPY.conflictBanner}</p>
      <p style={{ margin: "var(--s-1) 0 0", fontSize: "var(--font-size-200)" }}>
        {COPY.conflictTapHint}
      </p>
    </div>
  );
}

function FreeWindowCard({
  window,
  onTap,
  onUnavailable,
}: {
  window: ScheduleFreeWindow;
  onTap: () => void;
  onUnavailable: () => void;
}) {
  // Главный тап — запись (макет); «Недоступно» — вторичное действие, не
  // главный тап (DRF-2155, М-3). Две соседние кнопки, не кнопка в кнопке.
  return (
    <div
      className="m-card schedule-free"
      style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}
    >
      <button
        type="button"
        className="m-card--tappable"
        onClick={onTap}
        aria-label={`Записать на ${window.start}–${window.end}`}
        style={{
          flex: 1,
          minHeight: 44,
          background: "none",
          border: "none",
          textAlign: "start",
          padding: 0,
        }}
      >
        <div
          className="m-card__title"
          style={{ color: "var(--c-text-secondary)" }}
        >
          {window.start} · {COPY.freeWindow(window.duration_min)}
        </div>
      </button>
      <button
        type="button"
        className="btn-secondary"
        onClick={onUnavailable}
        aria-label={`${COPY.unavailable}: ${window.start}–${window.end}`}
      >
        {COPY.unavailable}
      </button>
    </div>
  );
}

function BlockCard({ block }: { block: ScheduleDay["blocks"][number] }) {
  const startHm = localHmFromIso(block.start);
  const endHm = localHmFromIso(block.end);
  return (
    <div className="m-card" aria-label="блок">
      <div
        className="m-card__title"
        style={{ color: "var(--c-text-secondary)" }}
      >
        {startHm}–{endHm} · {translateBlockReason(block.reason)}
      </div>
      {!block.approved ? (
        <div className="m-card__meta">на рассмотрении</div>
      ) : null}
    </div>
  );
}

function translateBlockReason(reason: string): string {
  switch (reason) {
    case "lunch":
      return "обед";
    case "vacation":
      return "отпуск";
    case "sick":
      return "болезнь";
    case "personal":
      return "личное";
    default:
      return "недоступно";
  }
}

// --- Week view ------------------------------------------------------------

function WeekView({
  data,
  anchor,
  onSwitchToDay,
}: {
  data: MasterScheduleResponse;
  anchor: Date;
  onSwitchToDay: (d: Date) => void;
}) {
  const start = startOfWeekMonday(anchor);
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  const todayYmd = formatYmdLocal(new Date());
  const byDate = new Map<string, ScheduleDay>(
    data.days.map((d) => [d.date, d]),
  );

  const anyWorking = days.some((d) => {
    const row = byDate.get(formatYmdLocal(d));
    return row && !row.is_off_day && row.working_hours !== null;
  });
  if (!anyWorking) {
    return (
      <section className="master-dashboard__section">
        <p className="master-dashboard__empty-line">
          {COPY.emptyWeek(DEFAULT_SALON_OWNER_HINT)}
        </p>
        {/* DRF-1817 — часы можно задать самому (экран 06); для соло владелец
            и есть мастер. */}
        <Link
          to="/solo/working-hours"
          className="btn-secondary schedule-week__setup"
        >
          Настроить рабочие часы
        </Link>
      </section>
    );
  }

  return (
    <section className="master-dashboard__section">
      <div className="schedule-week-grid">
        {days.map((d) => {
          const ymd = formatYmdLocal(d);
          const row = byDate.get(ymd);
          const isOff = row?.is_off_day ?? true;
          const clients = row?.bookings.length ?? 0;
          const freeCount = row?.free_windows.length ?? 0;
          const isToday = ymd === todayYmd;
          return (
            <button
              key={ymd}
              type="button"
              className={`schedule-week-cell${isToday ? " schedule-week-cell--today" : ""}${isOff ? " schedule-week-cell--off" : ""}`}
              onClick={() => onSwitchToDay(d)}
              aria-label={`${weekdayShortRu(d)} ${d.getDate()}`}
            >
              <span className="schedule-week-cell__wd">
                {weekdayShortRu(d)}
              </span>
              <span className="schedule-week-cell__date">{d.getDate()}</span>
              <span className="schedule-week-cell__meta">
                {isOff ? "—" : clients}
              </span>
              <span className="schedule-week-cell__meta schedule-week-cell__meta--sub">
                {isOff ? "" : `${freeCount}`}
              </span>
            </button>
          );
        })}
      </div>
      <ul className="schedule-week-list">
        {days.map((d) => {
          const ymd = formatYmdLocal(d);
          const row = byDate.get(ymd);
          if (row === undefined || row.is_off_day) return null;
          const isToday = ymd === todayYmd;
          return (
            <li key={ymd}>
              <div className="schedule-week-section">
                <div className="schedule-week-section__title">
                  {formatWeekHeaderRu(d)}{" "}
                  {isToday ? (
                    <span className="schedule-week-section__today">
                      {COPY.weekDayActiveSuffix}
                    </span>
                  ) : null}
                </div>
                <div className="schedule-week-section__line">
                  {COPY.weekClientsLabel(row.bookings.length)},{" "}
                  {COPY.weekFreeLabel(row.free_windows.length)}
                </div>
                <button
                  type="button"
                  className="btn-secondary master-dashboard__inline-cta"
                  onClick={() => onSwitchToDay(d)}
                >
                  {COPY.weekDayLink}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// --- Month view -----------------------------------------------------------

function MonthView({
  data,
  anchor,
  onSwitchToDay,
}: {
  data: MasterScheduleResponse;
  anchor: Date;
  onSwitchToDay: (d: Date) => void;
}) {
  const monthStart = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const monthEnd = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
  // Build a Monday-aligned grid covering the visible month.
  const gridStart = startOfWeekMonday(monthStart);
  const totalCells =
    Math.ceil((monthEnd.getTime() - gridStart.getTime()) / 86400000) + 1;
  const cellCount = Math.ceil(totalCells / 7) * 7;
  const cells = Array.from({ length: cellCount }, (_, i) =>
    addDays(gridStart, i),
  );
  const todayYmd = formatYmdLocal(new Date());
  const byDate = new Map<string, ScheduleDay>(
    data.days.map((d) => [d.date, d]),
  );

  return (
    <section className="master-dashboard__section">
      <div className="schedule-month-weekheader">
        {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((w) => (
          <span key={w}>{w}</span>
        ))}
      </div>
      <div className="schedule-month-grid">
        {cells.map((d, idx) => {
          const inMonth = d.getMonth() === anchor.getMonth();
          const ymd = formatYmdLocal(d);
          const row = byDate.get(ymd);
          const clientCount = row?.bookings.length ?? 0;
          const isToday = ymd === todayYmd;
          return (
            <button
              key={idx}
              type="button"
              className={`schedule-month-cell${inMonth ? "" : " schedule-month-cell--out"}${isToday ? " schedule-month-cell--today" : ""}`}
              onClick={() => onSwitchToDay(d)}
              aria-label={`${d.getDate()}, ${clientCount} клиентов`}
            >
              <span className="schedule-month-cell__date">{d.getDate()}</span>
              <MonthDot count={clientCount} />
            </button>
          );
        })}
      </div>
    </section>
  );
}

function MonthDot({ count }: { count: number }): ReactNode {
  // Per spec line 454: 0 = empty, 1 small dot = 1-3, 2 medium dots = 4-6,
  // full circle = 7+
  if (count === 0)
    return <span className="schedule-month-dots" aria-hidden="true" />;
  if (count <= 3)
    return (
      <span className="schedule-month-dots" aria-hidden="true">
        <span className="schedule-month-dot schedule-month-dot--sm" />
      </span>
    );
  if (count <= 6)
    return (
      <span className="schedule-month-dots" aria-hidden="true">
        <span className="schedule-month-dot schedule-month-dot--md" />
        <span className="schedule-month-dot schedule-month-dot--md" />
      </span>
    );
  return (
    <span className="schedule-month-dots" aria-hidden="true">
      <span className="schedule-month-dot schedule-month-dot--full" />
    </span>
  );
}

// --- Mark-unavailable bottom sheet ---------------------------------------

function UnavailableSheet({
  state,
  onChange,
  onClose,
  onSubmit,
}: {
  state: UnavailableSheetState;
  onChange: (
    next:
      | UnavailableSheetState
      | ((prev: UnavailableSheetState) => UnavailableSheetState),
  ) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  // Render the human-readable date label inside the sheet header.
  const parsed = parseLocalYmd(state.date);
  const dateLabel = Number.isNaN(parsed.getTime())
    ? state.date
    : `${parsed.getDate()} ${
        [
          "января",
          "февраля",
          "марта",
          "апреля",
          "мая",
          "июня",
          "июля",
          "августа",
          "сентября",
          "октября",
          "ноября",
          "декабря",
        ][parsed.getMonth()] ?? ""
      }`;
  return (
    <div
      className="schedule-sheet-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Помечу как недоступно"
      onClick={onClose}
    >
      <div className="schedule-sheet" onClick={(e) => e.stopPropagation()}>
        <h2 className="schedule-sheet__title">
          {COPY.unavailableSheet.title(dateLabel, state.startHm, state.endHm)}
        </h2>
        <fieldset className="schedule-sheet__field">
          <legend>{COPY.unavailableSheet.reasonLabel}</legend>
          {REASON_VALUES.map((r) => (
            <label key={r} className="schedule-sheet__radio">
              <input
                type="radio"
                name="reason"
                value={r}
                checked={state.reason === r}
                onChange={() => onChange((prev) => ({ ...prev, reason: r }))}
              />
              <span>{COPY.unavailableSheet.reasons[r]}</span>
            </label>
          ))}
        </fieldset>
        <label className="schedule-sheet__field">
          <span>{COPY.unavailableSheet.commentLabel}</span>
          <textarea
            value={state.comment}
            onChange={(e) =>
              onChange((prev) => ({
                ...prev,
                comment: e.currentTarget.value,
              }))
            }
            maxLength={200}
            rows={3}
            className="schedule-sheet__textarea"
          />
        </label>
        <div className="schedule-sheet__actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onClose}
            disabled={state.submitting}
          >
            {COPY.unavailableSheet.cancel}
          </button>
          <button
            type="button"
            className="schedule-sheet__primary"
            onClick={onSubmit}
            disabled={state.submitting}
          >
            {COPY.unavailableSheet.submit}
          </button>
        </div>
      </div>
    </div>
  );
}
