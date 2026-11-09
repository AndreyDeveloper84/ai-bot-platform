/**
 * «Рабочее время» — неделя мастера (DRF-2936, макет DRF-2865, кадр 2).
 *
 * Адрес `/admin/working-time/:masterId?week=YYYY-MM-DD` (понедельник). Неделя
 * — ДАТАМИ, а не днями недели: правка на макете всегда «Вторник, 5 августа».
 *
 * Часы даты — СЕРВЕРА: `working_hours` дня из `day-schedule`, по ним и
 * продают (`lib/salon-working-time.weekRows`). Сверху — только пометки из
 * списков того же сервера: полнодневная недоступность и закрытие салона
 * закрывают дату; «только эта дата» у даты с изменением (контракт:
 * «просмотр существующих исключений») и часы недоступности внутри рабочего
 * дня — добавления по контракту, а не по картинке.
 *
 * Обычная неделя здесь только читается — её запись ждёт DRF-2605 (Андрей,
 * 20.10: «оставить только для чтения с явным объяснением»). Объяснение —
 * строкой над неделей.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { SystemState } from "../../components/OperationalSystemState";
import { SalonPilotTabBar } from "../../components/SalonPilotTabBar";
import { useOnline } from "../../hooks/useOnline";
import { useScreenBack } from "../../hooks/useScreenBack";
import {
  getMasterDaySchedule,
  getMasterDetail,
  getMasterExceptions,
  getMasterSchedule,
  type MasterDay,
  type MasterDetail,
  type MasterExceptions,
  type MasterSchedule,
} from "../../lib/admin-api";
import { signalReady } from "../../lib/max-sdk";
import {
  WORKING_TIME_ROOT,
  WT_COPY,
  addDays,
  isYmd,
  listsUnreadable,
  rowLabel,
  salonToday,
  scheduleNotSet,
  weekRangeLabel,
  weekRows,
  weekStart,
  workingTimeDayPath,
  type WeekRow,
} from "../../lib/salon-working-time";
import { backByAction } from "../../lib/screen-back";
import {
  IconCalendar,
  IconChevron,
  WtHeader,
  WtLoadError,
  WtStateCard,
} from "./WorkingTimeParts";

type Phase =
  | { kind: "loading" }
  | {
      kind: "ready";
      monday: string;
      detail: MasterDetail;
      schedule: MasterSchedule;
      exceptions: MasterExceptions;
      days: MasterDay[];
      today: string;
    }
  | { kind: "error"; err: unknown };

function requestedMonday(raw: string | null): string {
  return weekStart(raw && isYmd(raw) ? raw : salonToday(null));
}

export function SalonPilotWorkingTimeWeekScreen() {
  const { masterId = "" } = useParams<{ masterId: string }>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const online = useOnline();
  const monday = requestedMonday(params.get("week"));
  // «Изменить ещё день» (кадр 8) — фокус на следующей дате.
  const focus = (location.state as { focus?: unknown } | null)?.focus;

  const toList = useCallback(
    () => navigate(WORKING_TIME_ROOT, { state: { masterId } }),
    [navigate, masterId],
  );
  const back = useScreenBack(backByAction(toList));

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [reload, setReload] = useState(0);
  const [openWeekAnyway, setOpenWeekAnyway] = useState(false);
  const ready = useRef(false);
  const weekGiven = params.has("week");

  const load = useCallback(
    async (signal: AbortSignal) => {
      try {
        // Сутки по краям недели: отгул у полуночи салона каталог отдаёт по
        // датам в СВОЁМ поясе и иначе мог бы выпасть из ответа.
        const [detail, schedule, exceptions, frame] = await Promise.all([
          getMasterDetail(masterId, { signal }),
          getMasterSchedule(masterId, { signal }),
          getMasterExceptions(
            masterId,
            { from: addDays(monday, -1), to: addDays(monday, 7) },
            { signal },
          ),
          // Часы дат — серверные; заодно пояс салона: «сегодня» — по его часам.
          getMasterDaySchedule(masterId, { from: monday, to: addDays(monday, 6) }, { signal }),
        ]);
        if (signal.aborted) return;
        const today = salonToday(frame.tenant_tz);
        // Без явной недели — неделя САЛОНА: у браузера в другом поясе
        // «сегодня» могло оказаться вчерашним понедельником.
        if (!weekGiven && weekStart(today) !== monday) {
          setParams({ week: weekStart(today) }, { replace: true });
          return;
        }
        setPhase({
          kind: "ready",
          monday,
          detail,
          schedule,
          exceptions,
          days: frame.days,
          today,
        });
      } catch (err) {
        if (signal.aborted) return;
        setPhase({ kind: "error", err });
      } finally {
        if (!ready.current && !signal.aborted) {
          ready.current = true;
          signalReady();
        }
      }
    },
    [masterId, monday, weekGiven, setParams],
  );

  useEffect(() => {
    const controller = new AbortController();
    setPhase({ kind: "loading" });
    void load(controller.signal);
    return () => controller.abort();
  }, [load, reload]);

  // Повтор — через тот же эффект: поздний ответ прежней недели отменён им.
  const retry = () => setReload((n) => n + 1);

  const shiftWeek = (days: number) => setParams({ week: addDays(monday, days) });

  const detail = phase.kind === "ready" ? phase.detail : null;

  return (
    <div className="screen wt-screen">
      <WtHeader
        title={WT_COPY.title}
        onBack={back}
        master={
          detail
            ? { name: detail.name, specialization: detail.specialization, photoUrl: detail.photo_url }
            : null
        }
      />
      <div className="wt-body">
        {!online ? <SystemState kind="offline" /> : null}
        {phase.kind === "loading" ? <SystemState kind="loading" lines={7} /> : null}
        {phase.kind === "error" ? <WtLoadError page err={phase.err} onRetry={retry} /> : null}
        {phase.kind === "ready" &&
        scheduleNotSet(phase.schedule, phase.exceptions) &&
        !openWeekAnyway ? (
          <WtStateCard
            page
            tone="accent"
            icon={<IconCalendar />}
            title={WT_COPY.empty.notSet.title}
            body={WT_COPY.empty.notSet.body}
          >
            {/* Решение 09.11 (вариант «а»): обычную неделю здесь не задать
                (DRF-2605), а отдельные даты — можно; кнопка открывает их. */}
            <button
              type="button"
              className="btn-secondary wt-btn"
              onClick={() => setOpenWeekAnyway(true)}
            >
              {WT_COPY.empty.notSet.cta}
            </button>
          </WtStateCard>
        ) : null}
        {phase.kind === "ready" &&
        (!scheduleNotSet(phase.schedule, phase.exceptions) || openWeekAnyway) ? (
          <>
            <p className="wt-note">{WT_COPY.week.note}</p>
            {listsUnreadable(phase.exceptions) ? (
              <p className="wt-note wt-note--warn">{WT_COPY.week.unreadable}</p>
            ) : null}
            <div className="wt-weeknav">
              <button
                type="button"
                className="wt-weeknav__btn"
                aria-label={WT_COPY.week.prev}
                onClick={() => shiftWeek(-7)}
              >
                <span aria-hidden="true" className="wt-weeknav__arrow wt-weeknav__arrow--prev">
                  <IconChevron />
                </span>
              </button>
              <span className="wt-weeknav__label">{weekRangeLabel(phase.monday)}</span>
              <button
                type="button"
                className="wt-weeknav__btn"
                aria-label={WT_COPY.week.next}
                onClick={() => shiftWeek(7)}
              >
                <span aria-hidden="true" className="wt-weeknav__arrow">
                  <IconChevron />
                </span>
              </button>
            </div>
            <ul className="wt-week" aria-label={weekRangeLabel(phase.monday)}>
              {weekRows(phase.monday, phase.days, phase.exceptions, phase.today).map((row) => (
                <li key={row.ymd}>
                  <WeekDayRow
                    row={row}
                    focused={row.ymd === focus}
                    onOpen={() => navigate(workingTimeDayPath(masterId, row.ymd))}
                  />
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </div>
      <SalonPilotTabBar active="schedule" />
    </div>
  );
}

function WeekDayRow({
  row,
  focused,
  onOpen,
}: {
  row: WeekRow;
  focused: boolean;
  onOpen: () => void;
}) {
  const label = rowLabel(row.ymd);
  const ref = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (focused) ref.current?.focus();
  }, [focused]);
  return (
    <button
      type="button"
      ref={ref}
      className={`wt-day${row.past ? " wt-day--past" : ""}`}
      disabled={row.past}
      onClick={onOpen}
    >
      <span className="wt-day__label">
        <span className="wt-day__weekday">{label.weekday}</span>
        <span className="wt-day__date">{label.date}</span>
      </span>
      <span className="wt-day__value">
        {row.status === "working" ? (
          <span className="wt-day__hours">
            <span className="wt-pill">{row.hours ?? WT_COPY.day.noHours}</span>
            <span className="wt-dot" aria-hidden="true" />
          </span>
        ) : null}
        {row.status === "off" ? (
          <span className="wt-pill wt-pill--off">{WT_COPY.week.dayOff}</span>
        ) : null}
        {row.status === "closed" ? (
          <span className="wt-pill wt-pill--off">{WT_COPY.week.closed}</span>
        ) : null}
        {row.status === "unavailable" ? (
          <span className="wt-pill wt-pill--warn">{WT_COPY.week.unavailable}</span>
        ) : null}
        {row.partial.length > 0 ? (
          <span className="wt-day__note">{`${WT_COPY.week.unavailable} ${row.partial.join(", ")}`}</span>
        ) : null}
        {row.closedPart ? (
          <span className="wt-day__note">{`${WT_COPY.week.closed} ${row.closedPart}`}</span>
        ) : null}
        {row.override ? <span className="wt-day__note">{WT_COPY.week.override}</span> : null}
      </span>
      <span className="wt-day__chevron" aria-hidden="true">
        {row.past ? null : <IconChevron />}
      </span>
    </button>
  );
}
