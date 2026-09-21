/**
 * «Рабочий график» мастера — макет DRF-1186 (DRF-2200, М-7).
 *
 * Контракт часов прежний (DRF-1817, M24): `GET/PUT /working-hours` бота →
 * каталог. Второго хранилища часов в боте нет — экран пишет туда же,
 * откуда движок слотов читает, и рисует readback каталога, не эхо запроса.
 *
 * Четыре экрана макета:
 *
 * 1. «Рабочий график» — неделя списком: день и его интервал или «Выходной».
 *    Снято по макету: чекбоксы «Выберите рабочие дни» и «Применить ко всем
 *    выбранным дням» — обе делали неделю единым полем ввода, а макет
 *    правит ОДИН день за раз.
 * 2. «Изменить конкретный день» — три именованных варианта («По обычному
 *    графику» / «Другие часы» / «Не работаю»). Меняется только этот день
 *    недели: остальные шесть уходят в PUT такими, какими пришли.
 * 3. «Недоступно (часть дня)» — окно недоступности на ближайшую такую дату
 *    (дата названа в шапке листа, не угадывается молча). Это НЕ график:
 *    уходит заявкой `POST /availability` — у соло владелец он сам, и заявка
 *    применяется сразу; у салонного её решает владелец (§83).
 * 4. «Конфликт с записью» — каталог отказал 409, и экран показывает саму
 *    запись (кто/что/когда) из `details.conflicts`, а не слово «конфликт».
 *    Черновик дня цел: «Вернуться» возвращает в экран 2 с тем же выбором.
 *
 * Поверхности (DRF-2152): адрес решает, кто перед нами.
 *   * `/solo/working-hours` — соло правит часы сам;
 *   * `/master/working-hours` — мастер салона видит ТО ЖЕ, но только
 *     читает: часы салонного мастера ведёт салон (§83). Его действие —
 *     «Запросить изменение», заявка, которую администратор видит в
 *     «Заявках на изменение графика».
 *
 * Предел (назван, не спрятан): записи часов салонным мастером здесь нет и
 * не будет — она принадлежит владельцу. Пока у администратора нет своего
 * экрана часов мастера, салон ставит их в каталоге/бэк-офисе.
 *
 * Семантика (§13.2): часы — не слоты, которые видит клиент. Единственная
 * верная фраза — «По этим часам Ayla будет рассчитывать доступное время
 * для записи»; «Клиенты увидят ваше расписание» на экране не звучит.
 *
 * Проверка на месте (§13.3): начало раньше конца, перерыв внутри смены —
 * та же, что на сервере; сервер остаётся последним словом (400/409).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useScreenBack } from "../hooks/useScreenBack";
import { backTo } from "../lib/screen-back";

import { SheetChrome } from "../components/PersonalDataSheets";
import { Snackbar } from "../components/Snackbar";
// Загрузка / ошибка загрузки — мастерский SystemState (DRF-2194), не клиентский StateError.
import { SystemState } from "../components/master/SystemState";
import { ApiError } from "../lib/api";
import {
  getWorkingHours,
  putWorkingHours,
  requestAvailability,
  type WorkingHoursDay,
  type WorkingHoursResponse,
} from "../lib/master-api";
import { signalReady } from "../lib/max-sdk";

export const DAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"] as const;
export const DAY_NAMES = [
  "Понедельник",
  "Вторник",
  "Среда",
  "Четверг",
  "Пятница",
  "Суббота",
  "Воскресенье",
] as const;

const MONTHS_GENITIVE = [
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
] as const;

/** Слова макета DRF-1186 — дословно, одним словарём на экран и на «Сегодня». */
export const HOURS_COPY = {
  title: "Рабочий график",
  lead: "Ваш обычный график.",
  leadRepeat: "Повторяется каждую неделю.",
  weekNote:
    "Здесь задаётся ваш обычный график на неделю. Конкретные даты можно изменить отдельно.",
  dayOff: "Выходной",
  day: {
    scope: "Изменение только на этот день",
    usual: "По обычному графику",
    custom: "Другие часы",
    off: "Не работаю",
    usualHintOff: "По графику: выходной",
    usualHint: (interval: string) => `По графику: ${interval}`,
    customHint: "Укажите другой интервал на этот день",
    offHint: "Выходной только на этот день",
    note: "Это изменение не повлияет на другие дни и не изменит существующие записи клиентов.",
    save: "Сохранить",
    from: "Начало",
    to: "Окончание",
    /** «По обычному графику» — день остаётся как был. */
    nothingToSave: "Этот день остаётся как в графике — сохранять нечего.",
  },
  unavailable: {
    open: "Недоступно (часть дня)",
    lead: "Укажите период, в который вы недоступны.",
    from: "С",
    to: "До",
    reason: "Причина (необязательно)",
    note: "Новые записи на это время не смогут быть созданы.",
    save: "Сохранить",
    sent: "Заявка отправлена.",
    // Соло сам себе владелец: заявка применяется сразу, и говорить
    // «отправлено» было бы неправдой — время уже закрыто.
    applied: "Время закрыто — новые записи на него не создадутся.",
  },
  conflict: {
    title: "На это время уже есть запись.",
    lead: "Сначала решите, что делать с существующей записью.",
    note: "Изменение графика остановлено. Запись клиента не меняется автоматически.",
    open: "Открыть запись",
    back: "Вернуться",
    // Ayla отказывает по всему будущему, а список собран на горизонт —
    // молчать об этом значит обещать, что показано всё.
    horizon: (days: number) => `Показаны записи на ближайшие ${days} дн.`,
  },
  salon: {
    note: "Часы мастера салона ведёт салон. Заявку на изменение увидит администратор.",
    request: "Запросить изменение",
    sent: "Заявка отправлена администратору.",
    pickDay: "Или выберите день в списке — заявка уйдёт на него.",
    /** Нечего просить: день остаётся как был. */
    nothingToAsk: "Выберите, что изменить, — и заявка уйдёт администратору.",
    // Заявка умеет сказать ровно две вещи: «не работаю в этот день» и
    // «недоступен в это окно». «Другие часы» она выразить не может, и
    // предлагать её мастеру салона значило бы обещать несказанное.
    onlyOff: "Заявкой можно попросить выходной или окно недоступности. Другие часы ставит салон.",
  },
  /**
   * «Часы не заданы» ≠ «выходной» (экран «Сегодня»). Дверь разная по
   * поверхности: салонный просит часы у салона, соло ставит их сам.
   */
  notSet: {
    title: "Рабочие часы ещё не заданы",
    cta: "Запросить часы →",
    ctaSolo: "Задать часы →",
  },
} as const;

export const SEMANTIC_NOTE =
  "По этим часам Ayla будет рассчитывать доступное время для записи.";
/**
 * Экран больше не копит правки недели: каждый день сохраняется своим листом
 * сразу (макет DRF-1186). Поэтому здесь не «Сохранить и продолжить позже», а
 * просто «Продолжить позже» — сохранять к этому моменту уже нечего, и кнопка
 * «Сохранить расписание», перезаписывавшая ответ сервера им же, снята.
 */
export const CONTINUE_LATER_LABEL = "Продолжить позже";
export const SAVED_MESSAGE = "Часы сохранены.";
export const INVALID_INTERVAL = "Начало должно быть раньше конца.";
export const INVALID_BREAK = "Перерыв должен быть внутри рабочего времени.";
export const CONFLICT_MESSAGE =
  "В это время уже есть записи. Сначала разберитесь с ними.";
export const NOT_LINKED_MESSAGE =
  "Профиль ещё не связан с каталогом — сохранить часы пока некуда.";
export const REQUEST_FAILED = "Не удалось отправить заявку. Попробуйте ещё раз.";

type Phase =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  /** Профиль ещё не связан с каталогом — этап жизни, не отказ прав (DRF-2194). */
  | { kind: "not_linked" }
  | { kind: "ready"; timezone: string | null };

export type WeekDraft = WorkingHoursDay[];

/** Что человек выбрал для одного дня недели (экран 2 макета). */
type DayMode = "usual" | "custom" | "off";

type DayDraft = { weekday: number; mode: DayMode; start: string; end: string };

/** Запись, мешающая новому графику — из `details.conflicts` отказа 409. */
export interface HoursConflict {
  booking_id: string;
  date?: string;
  client_name: string;
  service_name: string;
  duration_min: number;
  start_at: string;
  end_at: string;
}

type Sheet =
  | { kind: "day" }
  | { kind: "unavailable" }
  /** `from` — лист, из которого пришёл отказ: «Вернуться» ведёт туда же. */
  | {
      kind: "conflict";
      rows: HoursConflict[];
      horizonDays: number | null;
      from: "day" | "unavailable";
    }
  | null;

function emptyWeek(): WeekDraft {
  return Array.from({ length: 7 }, (_, d) => ({
    day_of_week: d,
    is_working_day: false,
    start_time: null,
    end_time: null,
    break_start: null,
    break_end: null,
  }));
}

/** Неделя из ответа сервера, всегда 7 дней в порядке Пн…Вс. */
export function weekFrom(schedule: WorkingHoursDay[]): WeekDraft {
  const week = emptyWeek();
  for (const row of schedule) {
    const d = Number(row.day_of_week);
    if (d >= 0 && d < 7) {
      week[d] = {
        day_of_week: d,
        is_working_day: Boolean(row.is_working_day),
        start_time: row.start_time ? row.start_time.slice(0, 5) : null,
        end_time: row.end_time ? row.end_time.slice(0, 5) : null,
        break_start: row.break_start ? row.break_start.slice(0, 5) : null,
        break_end: row.break_end ? row.break_end.slice(0, 5) : null,
      };
    }
  }
  return week;
}

/** Ошибка дня по правилам §13.3 или null. Та же проверка, что на сервере. */
export function dayError(day: WorkingHoursDay): string | null {
  if (!day.is_working_day) return null;
  if (!day.start_time || !day.end_time || day.start_time >= day.end_time)
    return INVALID_INTERVAL;
  if (day.break_start || day.break_end) {
    if (!day.break_start || !day.break_end) return INVALID_BREAK;
    if (day.break_start >= day.break_end) return INVALID_BREAK;
    if (day.break_start < day.start_time || day.break_end > day.end_time)
      return INVALID_BREAK;
  }
  return null;
}

/** Строка дня в неделе: интервал (с перерывом) или «Выходной». */
export function summary(day: WorkingHoursDay): string {
  if (!day.is_working_day || !day.start_time || !day.end_time)
    return HOURS_COPY.dayOff;
  const brk =
    day.break_start && day.break_end
      ? ` · перерыв ${day.break_start}–${day.break_end}`
      : "";
  return `${day.start_time}–${day.end_time}${brk}`;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/**
 * С чего открывается «Другие часы» у дня без часов: интервал, который
 * мастер уже работает в другие дни. Это его собственный график, а не
 * «10:00–19:00 из воздуха» — на пустом шаблоне поля остаются пустыми, и
 * сохранение честно упирается в §13.3.
 */
export function defaultInterval(week: WeekDraft): [string, string] {
  for (const day of week) {
    if (day.is_working_day && day.start_time && day.end_time)
      return [day.start_time, day.end_time];
  }
  return ["", ""];
}

/**
 * Ближайшая дата этого дня недели, считая сегодня (Пн=0…Вс=6).
 *
 * Экран 3 просит окно на конкретную дату, а открыт он из дня НЕДЕЛИ —
 * поэтому дата называется в шапке листа, а не подставляется молча.
 */
export function nextDateFor(weekday: number, today: Date = new Date()): Date {
  const mondayFirst = (today.getDay() + 6) % 7;
  const shift = (weekday - mondayFirst + 7) % 7;
  const date = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  date.setDate(date.getDate() + shift);
  return date;
}

/** Сегодняшний день недели в счёте Пн=0…Вс=6. */
export function todayWeekday(today: Date = new Date()): number {
  return (today.getDay() + 6) % 7;
}

export function isoDate(date: Date): string {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

export function humanDate(date: Date): string {
  return `${date.getDate()} ${MONTHS_GENITIVE[date.getMonth()]}`;
}

/** «2026-08-26T14:30:00+03:00» → «14:30». Срез, а не `Date`: пояс уже в строке. */
export function hhmm(iso: string): string {
  return typeof iso === "string" && iso.length >= 16 ? iso.slice(11, 16) : "";
}

/** `details.conflicts` отказа — то, что можно показать, без догадок. */
export function conflictsFrom(err: unknown): HoursConflict[] {
  if (!(err instanceof ApiError)) return [];
  const raw = (err.details as { conflicts?: unknown } | undefined)?.conflicts;
  if (!Array.isArray(raw)) return [];
  // Карточка обещает «кто, что, когда»: строка без этих полей нарисовала бы
  // «undefined мин», поэтому неполную не показываем вовсе.
  return raw.filter(
    (row): row is HoursConflict =>
      typeof row === "object" &&
      row !== null &&
      typeof (row as HoursConflict).booking_id === "string" &&
      typeof (row as HoursConflict).start_at === "string" &&
      typeof (row as HoursConflict).end_at === "string" &&
      typeof (row as HoursConflict).duration_min === "number",
  );
}

/** Горизонт поиска конфликтов, если сервер его назвал. */
export function horizonFrom(err: unknown): number | null {
  if (!(err instanceof ApiError)) return null;
  const raw = (err.details as { horizon_days?: unknown } | undefined)
    ?.horizon_days;
  return typeof raw === "number" ? raw : null;
}

export function MasterWorkingHoursScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  // DRF-2152: соло и салонный мастер делят экран; поверхность — по адресу.
  const isSolo = location.pathname.startsWith("/solo/");

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [week, setWeek] = useState<WeekDraft>(emptyWeek());
  const [draft, setDraft] = useState<DayDraft | null>(null);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [snack, setSnack] = useState<string | null>(null);
  const sheetTriggerRef = useRef<HTMLElement | null>(null);

  // Возврат — «Сегодня» своей поверхности (DRF-2150).
  useScreenBack(backTo(isSolo ? "/solo/my-day" : "/master/dashboard"));
  useEffect(() => {
    signalReady();
  }, []);

  const load = useCallback(async () => {
    setPhase({ kind: "loading" });
    try {
      const data = await getWorkingHours();
      setWeek(weekFrom(data.schedule));
      setPhase({ kind: "ready", timezone: data.timezone });
    } catch (err) {
      // 403 not_linked на загрузке — свой текст экрана, как на save и как у
      // Place/Directions; иначе общий SystemState прочитал бы его как «Недостаточно прав».
      if (err instanceof ApiError && err.status === 403) {
        setPhase({ kind: "not_linked" });
        return;
      }
      setPhase({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openDay = (weekday: number, trigger: HTMLElement | null) => {
    const row = week[weekday];
    sheetTriggerRef.current = trigger;
    setSaveError(null);
    setDraft({
      weekday,
      mode: "usual",
      start: row?.start_time ?? "",
      end: row?.end_time ?? "",
    });
    setSheet({ kind: "day" });
  };

  const closeSheet = () => {
    setSheet(null);
    setDraft(null);
    setFrom("");
    setTo("");
    setReason("");
    // Ошибка принадлежала листу: оставить её на экране недели значило бы
    // показать «alert» без того, к чему он относится.
    setSaveError(null);
  };

  /**
   * Экран 3: открыть окно недоступности на ближайшую такую дату. Поля
   * заполнены сменой этого дня (а на выходном — сутками), чтобы «Сохранить»
   * отправил ровно то, что человек видит, а не пустоту, молча ставшую днём.
   */
  const openUnavailable = (weekday: number, trigger: HTMLElement | null) => {
    const row = week[weekday];
    const working = row?.is_working_day && row.start_time && row.end_time;
    sheetTriggerRef.current = trigger ?? sheetTriggerRef.current;
    setDraft({
      weekday,
      mode: "off",
      start: row?.start_time ?? "",
      end: row?.end_time ?? "",
    });
    setFrom(working ? (row.start_time as string) : "00:00");
    setTo(working ? (row.end_time as string) : "23:59");
    setReason("");
    setSaveError(null);
    setSheet({ kind: "unavailable" });
  };

  /** Неделя с применённым черновиком: меняется ОДИН день, шесть идут как есть. */
  const weekWithDraft = useCallback(
    (current: WeekDraft, d: DayDraft): WeekDraft =>
      current.map((day) => {
        if (day.day_of_week !== d.weekday) return day;
        if (d.mode === "usual") return day;
        if (d.mode === "off")
          return {
            ...day,
            is_working_day: false,
            start_time: null,
            end_time: null,
            break_start: null,
            break_end: null,
          };
        return {
          ...day,
          is_working_day: true,
          start_time: d.start || null,
          end_time: d.end || null,
        };
      }),
    [],
  );

  const saveDay = async () => {
    if (!draft || busy) return;
    const next = weekWithDraft(week, draft);
    const err = dayError(next[draft.weekday] as WorkingHoursDay);
    if (err) {
      setSaveError(err);
      return;
    }
    setBusy(true);
    setSaveError(null);
    try {
      const saved: WorkingHoursResponse = await putWorkingHours(next);
      setWeek(weekFrom(saved.schedule));
      setSnack(SAVED_MESSAGE);
      closeSheet();
    } catch (e) {
      const rows = conflictsFrom(e);
      if (rows.length > 0) {
        // Экран 4 макета: показываем записи, черновик дня не трогаем.
        setSheet({
          kind: "conflict",
          rows,
          horizonDays: horizonFrom(e),
          from: "day",
        });
        return;
      }
      setSaveError(saveErrorText(e));
    } finally {
      setBusy(false);
    }
  };

  /**
   * Заявка: окно недоступности (экран 3) или выходной, о котором просит
   * мастер салона (экран 2 его поверхности). Время в теле — локально-наивное,
   * как на «Расписании»: пояс задаёт салон, а не телефон (иначе перевод
   * часов сдвинул бы хранимое).
   *
   * Отправляется ровно то, что человек видит: «Не работаю» — сутки целиком,
   * окно — введённые С и До. Варианта «по обычному графику» тут нет: просить
   * нечего, и кнопка в этом случае недоступна.
   */
  const sendRequest = async (origin: "day" | "unavailable") => {
    if (!draft || busy) return;
    const wholeDay = origin === "day";
    const date = isoDate(nextDateFor(draft.weekday));
    const startHm = wholeDay ? "00:00" : from;
    const endHm = wholeDay ? "23:59" : to;
    if (!startHm || !endHm || startHm >= endHm) {
      setSaveError(INVALID_INTERVAL);
      return;
    }
    setBusy(true);
    setSaveError(null);
    try {
      const res = await requestAvailability({
        start: `${date}T${startHm}:00`,
        end: `${date}T${endHm}:00`,
        reason_class: "other",
        reason_text: wholeDay
          ? `${HOURS_COPY.day.off}: ${DAY_NAMES[draft.weekday]}, ${humanDate(nextDateFor(draft.weekday))}`
          : reason.trim(),
      });
      if (!isSolo) {
        setSnack(HOURS_COPY.salon.sent);
      } else {
        // Соло сам себе владелец: заявка применяется сразу (§83) — и тогда
        // «отправлено» было бы неправдой.
        setSnack(
          res?.status === "approved"
            ? HOURS_COPY.unavailable.applied
            : HOURS_COPY.unavailable.sent,
        );
      }
      closeSheet();
    } catch (e) {
      const rows = conflictsFrom(e);
      if (rows.length > 0) {
        setSheet({
          kind: "conflict",
          rows,
          horizonDays: horizonFrom(e),
          from: origin,
        });
        return;
      }
      setSaveError(requestErrorText(e));
    } finally {
      setBusy(false);
    }
  };

  const bookingHref = (bookingId: string) =>
    `${isSolo ? "/solo" : "/master"}/bookings/${encodeURIComponent(bookingId)}`;


  if (phase.kind === "loading") {
    return (
      <main className="screen working-hours">
        <SystemState kind="loading" lines={2} />
      </main>
    );
  }
  if (phase.kind === "not_linked") {
    return (
      <main className="screen working-hours">
        <p className="callout" role="status">
          {NOT_LINKED_MESSAGE}
        </p>
      </main>
    );
  }
  if (phase.kind === "error") {
    return (
      <main className="screen working-hours">
        <SystemState
          kind="load_error"
          what="workingHours"
          err={phase.err}
          onRetry={() => void load()}
        />
      </main>
    );
  }

  return (
    <main
      className="screen working-hours"
      aria-labelledby="working-hours-title"
    >
      <h1 id="working-hours-title" className="working-hours__title">
        {HOURS_COPY.title}
      </h1>
      <p className="working-hours__lead">{HOURS_COPY.lead}</p>
      <p className="working-hours__lead">{HOURS_COPY.leadRepeat}</p>
      {phase.timezone && (
        <p className="working-hours__tz" data-testid="working-hours-tz">
          Часовой пояс: {phase.timezone}
        </p>
      )}

      {/* Экран 1 — неделя списком; тап по дню открывает экран 2. */}
      <ul className="working-hours__list" aria-label="Неделя">
        {week.map((day) => {
          const err = dayError(day);
          return (
            <li key={day.day_of_week} className="working-hours__item">
              <button
                type="button"
                className="working-hours__row"
                data-testid={`day-${day.day_of_week}`}
                onClick={(e) => openDay(day.day_of_week, e.currentTarget)}
              >
                <span className="working-hours__day">
                  {DAY_NAMES[day.day_of_week]}
                </span>
                <span
                  className={`working-hours__summary${err ? " working-hours__summary--error" : ""}`}
                >
                  {err ?? summary(day)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      <p className="working-hours__note">{HOURS_COPY.weekNote}</p>
      <p className="working-hours__note">{SEMANTIC_NOTE}</p>
      {!isSolo && <p className="working-hours__note">{HOURS_COPY.salon.note}</p>}

      {saveError && sheet === null && (
        <p className="working-hours__error" role="alert">
          {saveError}
        </p>
      )}

      {/* Действия экрана прячутся, пока открыт лист: у листа свои. */}
      {sheet === null &&
        (isSolo ? (
          <div className="working-hours__actions">
            {/* Каждый день сохранён своим листом — здесь сохранять нечего,
                осталась дверь дальше по чек-листу настройки (DRF-1807). */}
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate("/solo/setup")}
            >
              {CONTINUE_LATER_LABEL}
            </button>
          </div>
        ) : (
          <div className="working-hours__actions">
            <button
              type="button"
              className="btn-primary"
              onClick={(e) =>
                openUnavailable(todayWeekday(), e.currentTarget as HTMLElement)
              }
            >
              {HOURS_COPY.salon.request}
            </button>
            <p className="working-hours__note">{HOURS_COPY.salon.pickDay}</p>
          </div>
        ))}

      {sheet?.kind === "day" && draft && (
        <DaySheet
          draft={draft}
          row={week[draft.weekday] as WorkingHoursDay}
          fallback={defaultInterval(week)}
          isSolo={isSolo}
          busy={busy}
          error={saveError}
          triggerRef={sheetTriggerRef}
          onChange={setDraft}
          onSave={() => void saveDay()}
          onRequest={() => void sendRequest("day")}
          onUnavailable={() => openUnavailable(draft.weekday, null)}
          onClose={closeSheet}
        />
      )}

      {sheet?.kind === "unavailable" && draft && (
        <UnavailableSheet
          weekday={draft.weekday}
          from={from}
          to={to}
          reason={reason}
          busy={busy}
          error={saveError}
          triggerRef={sheetTriggerRef}
          onFrom={setFrom}
          onTo={setTo}
          onReason={setReason}
          onSave={() => void sendRequest("unavailable")}
          onClose={closeSheet}
        />
      )}

      {sheet?.kind === "conflict" && (
        <ConflictSheet
          rows={sheet.rows}
          horizonDays={sheet.horizonDays}
          triggerRef={sheetTriggerRef}
          href={bookingHref}
          onBack={() => setSheet({ kind: sheet.from })}
          onClose={closeSheet}
        />
      )}

      <Snackbar
        visible={snack !== null}
        message={snack ?? ""}
        durationMs={4000}
        onTimeout={() => setSnack(null)}
      />
    </main>
  );
}

/**
 * Отказ заявки словами сервера. «Попробуйте ещё раз» на 400 — обещание
 * лекарства, которого нет: окно в прошлом или пересечение с уже одобренным
 * повтор не вылечит.
 */
function requestErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return NOT_LINKED_MESSAGE;
    if (err.status === 400) return err.detail || REQUEST_FAILED;
    if (err.status === 409) return CONFLICT_MESSAGE;
  }
  return REQUEST_FAILED;
}

function saveErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return CONFLICT_MESSAGE;
    if (err.status === 403) return NOT_LINKED_MESSAGE;
    if (err.status === 400) return err.detail || INVALID_INTERVAL;
  }
  return "Не удалось сохранить. Попробуйте ещё раз.";
}

/** Смена варианта: «Другие часы» открываются с уже работающим интервалом. */
function withMode(
  draft: DayDraft,
  mode: DayMode,
  fallback: [string, string],
): DayDraft {
  if (mode !== "custom") return { ...draft, mode };
  return {
    ...draft,
    mode,
    start: draft.start || fallback[0],
    end: draft.end || fallback[1],
  };
}

/** Экран 2 макета — «Изменить конкретный день». */
function DaySheet({
  draft,
  row,
  fallback,
  isSolo,
  busy,
  error,
  triggerRef,
  onChange,
  onSave,
  onRequest,
  onUnavailable,
  onClose,
}: {
  draft: DayDraft;
  row: WorkingHoursDay;
  /** Интервал по умолчанию для «Других часов» — см. `defaultInterval`. */
  fallback: [string, string];
  isSolo: boolean;
  busy: boolean;
  error: string | null;
  triggerRef: React.RefObject<HTMLElement | null>;
  onChange: (next: DayDraft) => void;
  onSave: () => void;
  onRequest: () => void;
  onUnavailable: () => void;
  onClose: () => void;
}) {
  const usualHint = row?.is_working_day
    ? HOURS_COPY.day.usualHint(summary(row))
    : HOURS_COPY.day.usualHintOff;
  const options: Array<[DayMode, string, string]> = [
    ["usual", HOURS_COPY.day.usual, usualHint],
    ["custom", HOURS_COPY.day.custom, HOURS_COPY.day.customHint],
    ["off", HOURS_COPY.day.off, HOURS_COPY.day.offHint],
  ].filter(
    // Мастеру салона «Другие часы» не показываем: заявка их не выражает
    // (см. `salon.onlyOff`), а кнопка, которая отправит не то, о чём
    // просили, хуже отсутствующей.
    ([mode]) => isSolo || mode !== "custom",
  ) as Array<[DayMode, string, string]>;
  return (
    <SheetChrome
      headlineId="working-hours-day-headline"
      headline={
        isSolo
          ? (DAY_NAMES[draft.weekday] ?? "")
          : // Салонная заявка ложится на КОНКРЕТНУЮ дату, а не на все такие
            // дни недели — дата названа, а не угадана молча.
            `${DAY_NAMES[draft.weekday]}, ${humanDate(nextDateFor(draft.weekday))}`
      }
      closeDisabled={busy}
      triggerRef={triggerRef as React.RefObject<HTMLElement>}
      onClose={onClose}
    >
      <p className="working-hours__scope">{HOURS_COPY.day.scope}</p>
      <div className="working-hours__options">
        {options.map(([mode, name, hint]) => (
          <label key={mode} className="working-hours__option">
            <input
              type="radio"
              name="working-hours-day-mode"
              className="working-hours__option-input"
              checked={draft.mode === mode}
              onChange={() => onChange(withMode(draft, mode, fallback))}
            />
            <span className="working-hours__option-name">{name}</span>
            <span className="working-hours__option-hint">{hint}</span>
          </label>
        ))}
      </div>

      {draft.mode === "custom" && (
        <div className="working-hours__times">
          <div className="working-hours__time">
            <label htmlFor="working-hours-day-start">
              {HOURS_COPY.day.from}
            </label>
            <input
              id="working-hours-day-start"
              type="time"
              className="working-hours__input"
              value={draft.start}
              onChange={(e) => onChange({ ...draft, start: e.target.value })}
            />
          </div>
          <div className="working-hours__time">
            <label htmlFor="working-hours-day-end">{HOURS_COPY.day.to}</label>
            <input
              id="working-hours-day-end"
              type="time"
              className="working-hours__input"
              value={draft.end}
              onChange={(e) => onChange({ ...draft, end: e.target.value })}
            />
          </div>
        </div>
      )}

      <p className="working-hours__hint">{HOURS_COPY.day.note}</p>
      {!isSolo && (
        <p className="working-hours__hint">{HOURS_COPY.salon.onlyOff}</p>
      )}
      {draft.mode === "usual" && (
        <p className="working-hours__hint">
          {isSolo ? HOURS_COPY.day.nothingToSave : HOURS_COPY.salon.nothingToAsk}
        </p>
      )}
      {error && (
        <p className="working-hours__error" role="alert">
          {error}
        </p>
      )}

      <div className="working-hours__actions">
        <button
          type="button"
          className="btn-primary"
          // «По обычному графику» — это «ничего не меняем»: соло сохранил бы
          // ответ сервера им же, салонный попросил бы у администратора
          // пустоту. Просить и сохранять нечего.
          disabled={busy || draft.mode === "usual"}
          onClick={isSolo ? onSave : onRequest}
        >
          {isSolo ? HOURS_COPY.day.save : HOURS_COPY.salon.request}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={onUnavailable}
        >
          {HOURS_COPY.unavailable.open}
        </button>
      </div>
    </SheetChrome>
  );
}

/** Экран 3 макета — «Недоступно (часть дня)». */
function UnavailableSheet({
  weekday,
  from,
  to,
  reason,
  busy,
  error,
  triggerRef,
  onFrom,
  onTo,
  onReason,
  onSave,
  onClose,
}: {
  weekday: number;
  from: string;
  to: string;
  reason: string;
  busy: boolean;
  error: string | null;
  triggerRef: React.RefObject<HTMLElement | null>;
  onFrom: (v: string) => void;
  onTo: (v: string) => void;
  onReason: (v: string) => void;
  onSave: () => void;
  onClose: () => void;
}) {
  const date = nextDateFor(weekday);
  return (
    <SheetChrome
      headlineId="working-hours-unavailable-headline"
      headline={`${HOURS_COPY.unavailable.open} · ${DAY_NAMES[weekday]}, ${humanDate(date)}`}
      closeDisabled={busy}
      triggerRef={triggerRef as React.RefObject<HTMLElement>}
      onClose={onClose}
    >
      <p className="working-hours__scope">{HOURS_COPY.unavailable.lead}</p>
      <div className="working-hours__times">
        <div className="working-hours__time">
          <label htmlFor="working-hours-unavailable-from">
            {HOURS_COPY.unavailable.from}
          </label>
          <input
            id="working-hours-unavailable-from"
            type="time"
            className="working-hours__input"
            value={from}
            onChange={(e) => onFrom(e.target.value)}
          />
        </div>
        <div className="working-hours__time">
          <label htmlFor="working-hours-unavailable-to">
            {HOURS_COPY.unavailable.to}
          </label>
          <input
            id="working-hours-unavailable-to"
            type="time"
            className="working-hours__input"
            value={to}
            onChange={(e) => onTo(e.target.value)}
          />
        </div>
      </div>
      <div className="working-hours__field">
        <label
          className="working-hours__field-label"
          htmlFor="working-hours-unavailable-reason"
        >
          {HOURS_COPY.unavailable.reason}
        </label>
        <input
          id="working-hours-unavailable-reason"
          type="text"
          className="working-hours__reason"
          value={reason}
          onChange={(e) => onReason(e.target.value)}
        />
      </div>
      <p className="working-hours__hint">{HOURS_COPY.unavailable.note}</p>
      {error && (
        <p className="working-hours__error" role="alert">
          {error}
        </p>
      )}
      <div className="working-hours__actions">
        <button
          type="button"
          className="btn-primary"
          disabled={busy}
          onClick={onSave}
        >
          {HOURS_COPY.unavailable.save}
        </button>
      </div>
    </SheetChrome>
  );
}

/** Экран 4 макета — «Конфликт с записью»: сама запись, а не слово. */
function ConflictSheet({
  rows,
  horizonDays,
  triggerRef,
  href,
  onBack,
  onClose,
}: {
  rows: HoursConflict[];
  /** Сколько дней вперёд сервер смотрел; null — не сказал. */
  horizonDays: number | null;
  triggerRef: React.RefObject<HTMLElement | null>;
  href: (bookingId: string) => string;
  onBack: () => void;
  onClose: () => void;
}) {
  return (
    <SheetChrome
      headlineId="working-hours-conflict-headline"
      headline={HOURS_COPY.conflict.title}
      closeDisabled={false}
      triggerRef={triggerRef as React.RefObject<HTMLElement>}
      onClose={onClose}
    >
      {/* Заголовок листа и есть эта строка — второй раз её не говорим. */}
      <p className="working-hours__scope">{HOURS_COPY.conflict.lead}</p>
      {rows.map((row) => (
        <div key={row.booking_id} className="working-hours__conflict-card">
          <p className="working-hours__conflict-who">{row.client_name}</p>
          <p className="working-hours__conflict-when">
            {`${hhmm(row.start_at)}–${hhmm(row.end_at)}`}
          </p>
          <p className="working-hours__conflict-what">
            {`${row.service_name} · ${row.duration_min} мин`}
          </p>
          <Link className="working-hours__conflict-link" to={href(row.booking_id)}>
            {HOURS_COPY.conflict.open}
          </Link>
        </div>
      ))}
      <p className="working-hours__hint">{HOURS_COPY.conflict.note}</p>
      {horizonDays !== null && (
        <p className="working-hours__hint">
          {HOURS_COPY.conflict.horizon(horizonDays)}
        </p>
      )}
      <div className="working-hours__actions">
        <button type="button" className="btn-secondary" onClick={onBack}>
          {HOURS_COPY.conflict.back}
        </button>
      </div>
    </SheetChrome>
  );
}
