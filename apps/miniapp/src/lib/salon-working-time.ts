/**
 * «Рабочее время мастеров» салона — слова и расчёты экрана (DRF-2936).
 *
 * Макет: DRF-2865 Salon v2 · Working Time · VISUAL FROZEN v1.0. Канон
 * поведения — freeze-контракт из DRF-2865 (07.10): при расхождении картинки
 * с контрактом прав контракт. Отсюда три поправки к картинке, и все они
 * названы, а не спрятаны:
 *
 * * изменение дня помечено «только на эту дату» — контракт (MUST FIX 3)
 *   требует, чтобы override даты не выглядел правкой недели;
 * * системные состояния (ошибка загрузки, «проверяем результат», нет прав,
 *   нет связи, данные могли измениться) — общим словарём DRF-2938
 *   (`OperationalSystemState`), а не словами постера: контракт объявил их
 *   переиспользуемыми компонентами дизайн-системы;
 * * недельный шаблон только читается: его запись — DRF-2605. Правки — по
 *   ДАТАМ: изменение на дату и недоступность внутри даты (DRF-2607).
 *
 * # Слои одного дня
 *
 * Каталог считает день так же, и экран повторяет ровно это, ничего не
 * вычисляя сверху: изменение на дату заменяет день недели из шаблона;
 * недоступность вычитается; закрытие салона закрывает. Свободных окон экран
 * не считает вовсе — это делает сервер при записи клиента.
 */

import type {
  MasterDayBooking,
  MasterExceptionRow,
  MasterExceptions,
  MasterSchedule,
  MasterTimeOffRow,
  SalonClosureRow,
  ScheduleImpact,
} from "./admin-api";
import { ApiError } from "./api";
import { parseSalonWallClock } from "./format";

export const WORKING_TIME_ROOT = "/admin/working-time";

export function workingTimeWeekPath(masterId: string): string {
  return `${WORKING_TIME_ROOT}/${encodeURIComponent(masterId)}`;
}

export function workingTimeDayPath(masterId: string, ymd: string): string {
  return `${workingTimeWeekPath(masterId)}/${ymd}`;
}

/** Слова макета DRF-2865 — дословно; добавления по контракту помечены. */
export const WT_COPY = {
  title: "Рабочее время",
  search: "Поиск мастера",
  week: {
    /** Добавление по контракту (DRF-2936, Андрей 20.10): неделя только читается. */
    note: "Обычная неделя задаётся в каталоге салона. Здесь меняются отдельные даты.",
    dayOff: "Выходной",
    closed: "Салон закрыт",
    /** Существительное, не «недоступен/а»: мастер любого пола. */
    unavailable: "Недоступность",
    /** Добавление по контракту: «просмотр существующих исключений». */
    override: "только эта дата",
    prev: "Предыдущая неделя",
    next: "Следующая неделя",
  },
  day: {
    working: "Рабочий день",
    hours: "Рабочие часы",
    start: "Начало",
    end: "Окончание",
    duration: "Длительность",
    /** Добавление по контракту (MUST FIX 3). */
    scope: "Изменение только на эту дату",
    unavailability: "Недоступность в течение дня",
    addUnavailability: "Добавить недоступность",
    save: "Сохранить",
    cancel: "Отмена",
    dayOffTitle: "Выходной день",
    dayOffBody: "Мастер не будет отображаться в расписании на эту дату.",
    past: "Прошедшую дату изменить нельзя.",
    closed: "В этот день салон закрыт",
    readOnly: "Изменить можно в Pro App",
    noHours: "Не задано",
    nothingToSave: "Этот день остаётся как в графике — сохранять нечего.",
    invalidInterval: "Начало должно быть раньше конца.",
    breakCrosses: (from: string, to: string) =>
      `В этот день у мастера перерыв ${from}–${to}. Выберите часы, которые его не разрезают.`,
    saveFailed: "Не удалось сохранить изменения.",
    /** Предпросмотр не прочитан — «записей нет» сказать нельзя, запись не идёт. */
    impactFailed: "Не удалось проверить записи на это время — изменения пока не внесены.",
    edit: "Изменить",
    remove: "Удалить",
  },
  unavailable: {
    title: "Недоступность",
    from: "С",
    to: "До",
    reason: "Причина",
    reasonPlaceholder: "Например, личные дела",
    reasonRequired: "Укажите причину.",
    add: "Добавить",
    save: "Сохранить",
  },
  conflict: {
    title: "Конфликт с записями",
    body: "В выбранное время есть записи клиентов. Сначала перенесите или отмените их.",
    open: "Перейти к записям",
    cancel: "Отмена",
  },
  success: {
    title: "График обновлён",
    body: "Изменения сохранены и применены к расписанию.",
    hours: "Рабочие часы",
    unavailability: "Недоступность",
    none: "нет",
    dayOff: "Выходной",
    toWeek: "К неделе",
    anotherDay: "Изменить ещё день",
  },
  pending: {
    /** Карточка макета (кадр 9) под общим «Проверяем результат». */
    note: "Если изменения не отобразятся автоматически, мы проверим их и сообщим статус.",
    leave: "Отмена",
  },
  /** Добавление по контракту (MUST FIX 7: частичный исход не выдаётся за успех). */
  partial: {
    title: "Сохранено не всё",
    body: "Часть изменений сохранилась. Сохраните оставшееся или вернитесь к неделе.",
    retry: "Сохранить оставшееся",
    toWeek: "К неделе",
  },
  /**
   * Запись живёт ~10 минут от открытия Mini App (каталог выдаёт токен по
   * initData не старше 600 с). Черновик: слова — на утверждение владельцу.
   */
  reopen: {
    title: "Откройте приложение заново",
    body: "Изменения можно сохранить в течение 10 минут после открытия. Закройте приложение и откройте его снова из салонного бота — изменения дня останутся.",
  },
  empty: {
    noMasters: {
      title: "Нет мастеров",
      body: "В салоне пока нет мастеров. Добавьте мастеров, чтобы настраивать их график.",
      cta: "Добавить мастера",
    },
    notSet: {
      title: "График не настроен",
      body: "У мастера ещё не настроен рабочий график.",
      cta: "Настроить график",
    },
    noMatches: "Никого не нашли.",
  },
} as const;

// ---------------------------------------------------------------------------
// Даты — календарные, без пояса: «2031-01-10» это день салона, а не момент.
// ---------------------------------------------------------------------------

const WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"] as const;
const WEEKDAYS_FULL = [
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
const MONTHS_SHORT = [
  "янв.",
  "февр.",
  "мар.",
  "апр.",
  "мая",
  "июн.",
  "июл.",
  "авг.",
  "сент.",
  "окт.",
  "нояб.",
  "дек.",
] as const;

const YMD = /^(\d{4})-(\d{2})-(\d{2})$/;

export function isYmd(value: string): boolean {
  return YMD.test(value);
}

function parts(ymd: string): [number, number, number] {
  const m = YMD.exec(ymd);
  if (!m) throw new Error(`not a date: ${ymd}`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

function fromUtc(ms: number): string {
  const d = new Date(ms);
  const mm = `${d.getUTCMonth() + 1}`.padStart(2, "0");
  const dd = `${d.getUTCDate()}`.padStart(2, "0");
  return `${d.getUTCFullYear()}-${mm}-${dd}`;
}

export function addDays(ymd: string, days: number): string {
  const [y, m, d] = parts(ymd);
  return fromUtc(Date.UTC(y, m - 1, d) + days * 86_400_000);
}

/** Пн=0 … Вс=6 — как `day_of_week` каталога. */
export function weekdayOf(ymd: string): number {
  const [y, m, d] = parts(ymd);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}

/** Понедельник недели, в которой лежит дата. */
export function weekStart(ymd: string): string {
  return addDays(ymd, -weekdayOf(ymd));
}

export function weekDates(monday: string): string[] {
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i));
}

/** «4 – 10 авг. 2025»; на стыке месяцев и лет — оба названия. */
export function weekRangeLabel(monday: string): string {
  const sunday = addDays(monday, 6);
  const [y1, m1, d1] = parts(monday);
  const [y2, m2, d2] = parts(sunday);
  if (y1 !== y2) {
    return `${d1} ${MONTHS_SHORT[m1 - 1]} ${y1} – ${d2} ${MONTHS_SHORT[m2 - 1]} ${y2}`;
  }
  if (m1 !== m2) {
    return `${d1} ${MONTHS_SHORT[m1 - 1]} – ${d2} ${MONTHS_SHORT[m2 - 1]} ${y2}`;
  }
  return `${d1} – ${d2} ${MONTHS_SHORT[m2 - 1]} ${y2}`;
}

/** «Вторник, 5 августа». */
export function dayTitle(ymd: string): string {
  const [, m, d] = parts(ymd);
  return `${WEEKDAYS_FULL[weekdayOf(ymd)]}, ${d} ${MONTHS_GENITIVE[m - 1]}`;
}

/** Строка недели: «Пн» и «4 авг.». */
export function rowLabel(ymd: string): { weekday: string; date: string } {
  const [, m, d] = parts(ymd);
  return { weekday: WEEKDAYS_SHORT[weekdayOf(ymd)] ?? "", date: `${d} ${MONTHS_SHORT[m - 1]}` };
}

/** «5 августа». */
export function dayMonth(ymd: string): string {
  const [, m, d] = parts(ymd);
  return `${d} ${MONTHS_GENITIVE[m - 1]}`;
}

/**
 * Сегодня по часам САЛОНА. Пояс — строка IANA с сервера (`tenant_tz`);
 * не пришла — браузерная дата, та же оговорка, что у «Расписания».
 */
export function salonToday(tz: string | null | undefined, now: Date = new Date()): string {
  if (tz) {
    try {
      // en-CA печатает ровно YYYY-MM-DD.
      return new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(now);
    } catch {
      /* неизвестный пояс — браузерная дата */
    }
  }
  const mm = `${now.getMonth() + 1}`.padStart(2, "0");
  const dd = `${now.getDate()}`.padStart(2, "0");
  return `${now.getFullYear()}-${mm}-${dd}`;
}

// ---------------------------------------------------------------------------
// Время суток — «HH:MM», «24:00» — конец суток.
// ---------------------------------------------------------------------------

export const END_OF_DAY = "24:00";
/** Сетка записи каталога — 30 минут. */
export const STEP_MIN = 30;

export function toMinutes(value: string): number {
  const [h = 0, m = 0] = value.split(":").map(Number);
  return h * 60 + m;
}

export function fromMinutes(total: number): string {
  if (total >= 24 * 60) return END_OF_DAY;
  const h = `${Math.floor(total / 60)}`.padStart(2, "0");
  const m = `${total % 60}`.padStart(2, "0");
  return `${h}:${m}`;
}

/** «HH:MM:SS» с провода → «HH:MM». */
export function hm(value: string | null | undefined): string | null {
  if (!value) return null;
  const match = /^(\d{2}:\d{2})/.exec(value);
  return match?.[1] ?? null;
}

/** «9 ч 0 мин» — как на макете, ноль минут пишется. */
export function durationLabel(start: string, end: string): string | null {
  if (!start || !end) return null;
  const minutes = toMinutes(end) - toMinutes(start);
  if (minutes <= 0) return null;
  return `${Math.floor(minutes / 60)} ч ${minutes % 60} мин`;
}

/**
 * Варианты выбора времени — сеткой 30 минут. Текущее значение вне сетки
 * (09:45) остаётся среди вариантов: выбор не «прыгает» сам.
 */
export function timeOptions(current: string, opts: { allowEndOfDay?: boolean } = {}): string[] {
  const values = new Set<string>();
  for (let t = 0; t < 24 * 60; t += STEP_MIN) values.add(fromMinutes(t));
  if (opts.allowEndOfDay) values.add(END_OF_DAY);
  if (current) values.add(current);
  return [...values].sort((a, b) => toMinutes(a) - toMinutes(b));
}

// ---------------------------------------------------------------------------
// Слои дня
// ---------------------------------------------------------------------------

export interface DayLayer {
  working: boolean;
  start: string | null;
  end: string | null;
}

const OFF: DayLayer = { working: false, start: null, end: null };

/** День недели из шаблона. */
export function templateLayer(schedule: MasterSchedule, ymd: string): DayLayer {
  const row = schedule.days.find((d) => d.day_of_week === weekdayOf(ymd));
  if (!row || !row.is_working_day) return OFF;
  return { working: true, start: hm(row.start_time), end: hm(row.end_time) };
}

/** Перерыв дня недели — только показ (отдельной сущности «перерыв» нет). */
export function templateBreak(schedule: MasterSchedule, ymd: string): [string, string] | null {
  const row = schedule.days.find((d) => d.day_of_week === weekdayOf(ymd));
  const from = hm(row?.break_start);
  const to = hm(row?.break_end);
  return row?.is_working_day && from && to ? [from, to] : null;
}

export function exceptionOn(exceptions: MasterExceptions, ymd: string): MasterExceptionRow | null {
  return exceptions.exceptions.rows.find((r) => r.date === ymd) ?? null;
}

export function exceptionLayer(row: MasterExceptionRow): DayLayer {
  return row.is_working_day
    ? { working: true, start: hm(row.start), end: hm(row.end) }
    : OFF;
}

/** Действующий слой: изменение на дату заменяет день шаблона целиком. */
export function liveLayer(schedule: MasterSchedule, exception: MasterExceptionRow | null, ymd: string): DayLayer {
  return exception ? exceptionLayer(exception) : templateLayer(schedule, ymd);
}

export function sameLayer(a: DayLayer, b: DayLayer): boolean {
  if (a.working !== b.working) return false;
  if (!a.working) return true;
  return a.start === b.start && a.end === b.end;
}

/** Часы по умолчанию — как у мастера (`defaultInterval`): первый рабочий день шаблона. */
export function fallbackInterval(schedule: MasterSchedule): [string, string] {
  for (const day of schedule.days) {
    const from = hm(day.start_time);
    const to = hm(day.end_time);
    if (day.is_working_day && from && to) return [from, to];
  }
  return ["", ""];
}

export interface DayTimeOff {
  id: string;
  /** Часы внутри ЭТОЙ даты: начало раньше даты — «00:00», конец позже — «24:00». */
  from: string;
  to: string;
  reason: string;
  /** Начинается и кончается в этой дате — можно править и удалять. */
  editable: boolean;
  startYmd: string;
  endYmd: string;
}

/**
 * Недоступность, задевающая дату. Многодневная и переходящая через полночь
 * — только чтение: корзина на ней удалила бы весь отпуск, а не одну дату.
 */
export function timeOffOn(rows: readonly MasterTimeOffRow[], ymd: string): DayTimeOff[] {
  const out: DayTimeOff[] = [];
  const next = addDays(ymd, 1);
  for (const row of rows) {
    const start = parseSalonWallClock(row.start_at);
    const end = parseSalonWallClock(row.end_at);
    if (!start || !end) continue;
    const endsAtNextMidnight = end.ymd === next && end.hm === "00:00";
    if (start.ymd > ymd) continue;
    if (end.ymd < ymd || (end.ymd === ymd && end.hm === "00:00" && start.ymd < ymd)) continue;
    const from = start.ymd < ymd ? "00:00" : start.hm;
    const to = end.ymd > ymd ? END_OF_DAY : end.hm;
    out.push({
      id: row.id,
      from,
      to,
      reason: row.reason,
      editable: start.ymd === ymd && (end.ymd === ymd || endsAtNextMidnight),
      startYmd: start.ymd,
      endYmd: endsAtNextMidnight ? ymd : end.ymd,
    });
  }
  return out.sort((a, b) => toMinutes(a.from) - toMinutes(b.from));
}

export function closureOn(rows: readonly SalonClosureRow[], ymd: string): SalonClosureRow | null {
  return rows.find((r) => r.date === ymd) ?? null;
}

/** Недоступность закрывает весь рабочий день (или все сутки у выходного). */
export function coversWholeDay(layer: DayLayer, offs: readonly DayTimeOff[]): boolean {
  const from = layer.working && layer.start ? toMinutes(layer.start) : 0;
  const to = layer.working && layer.end ? toMinutes(layer.end) : 24 * 60;
  return offs.some((o) => toMinutes(o.from) <= from && toMinutes(o.to) >= to);
}

// ---------------------------------------------------------------------------
// Строка недели
// ---------------------------------------------------------------------------

export type WeekRowStatus = "working" | "off" | "closed" | "unavailable";

export interface WeekRow {
  ymd: string;
  past: boolean;
  status: WeekRowStatus;
  /** «10:00 – 19:00» у рабочего дня. */
  hours: string | null;
  /** На дату стоит изменение — эта дата не по шаблону. */
  override: boolean;
  /** Недоступность внутри рабочего дня: «13:00–15:00». */
  partial: string[];
}

export function weekRows(
  monday: string,
  schedule: MasterSchedule,
  exceptions: MasterExceptions,
  today: string,
): WeekRow[] {
  return weekDates(monday).map((ymd) => {
    const exception = exceptionOn(exceptions, ymd);
    const layer = liveLayer(schedule, exception, ymd);
    const offs = timeOffOn(exceptions.time_off.rows, ymd);
    const closure = closureOn(exceptions.closures.rows, ymd);
    const base = { ymd, past: ymd < today, override: exception !== null };
    if (closure && !closure.start && !closure.end) {
      return { ...base, status: "closed", hours: null, partial: [] };
    }
    if (!layer.working) return { ...base, status: "off", hours: null, partial: [] };
    if (coversWholeDay(layer, offs)) {
      return { ...base, status: "unavailable", hours: null, partial: [] };
    }
    return {
      ...base,
      status: "working",
      hours: layer.start && layer.end ? `${layer.start} – ${layer.end}` : null,
      partial: offs.map((o) => `${o.from}–${o.to}`),
    };
  });
}

/** Ни одного рабочего дня в шаблоне и ни одного изменения на неделе. */
export function scheduleNotSet(schedule: MasterSchedule, exceptions: MasterExceptions): boolean {
  return !schedule.has_working_day && exceptions.exceptions.rows.length === 0;
}

// ---------------------------------------------------------------------------
// Черновик дня и план записи
// ---------------------------------------------------------------------------

export interface DraftTimeOff {
  /** Ключ строки на экране. */
  key: string;
  /** Есть у сохранённой недоступности; у новой — `null`. */
  id: string | null;
  from: string;
  to: string;
  reason: string;
}

export interface DayDraft {
  working: boolean;
  /** Часы хранятся и у выходного: тумблер назад их возвращает. */
  start: string;
  end: string;
  timeOff: DraftTimeOff[];
}

export interface DaySnapshot {
  ymd: string;
  template: DayLayer;
  exception: MasterExceptionRow | null;
  timeOff: DayTimeOff[];
}

export function snapshotOf(
  ymd: string,
  schedule: MasterSchedule,
  exceptions: MasterExceptions,
): DaySnapshot {
  const exception = exceptionOn(exceptions, ymd);
  return {
    ymd,
    template: templateLayer(schedule, ymd),
    exception,
    timeOff: timeOffOn(exceptions.time_off.rows, ymd),
  };
}

export function snapshotLayer(snap: DaySnapshot): DayLayer {
  return snap.exception ? exceptionLayer(snap.exception) : snap.template;
}

export function initialDraft(snap: DaySnapshot, fallback: [string, string]): DayDraft {
  const layer = snapshotLayer(snap);
  return {
    working: layer.working,
    start: layer.start ?? fallback[0],
    end: layer.end ?? fallback[1],
    timeOff: snap.timeOff
      .filter((o) => o.editable)
      .map((o) => ({ key: o.id, id: o.id, from: o.from, to: o.to, reason: o.reason })),
  };
}

export function draftLayer(draft: DayDraft): DayLayer {
  return draft.working ? { working: true, start: draft.start, end: draft.end } : OFF;
}

export type DateAction =
  | { kind: "none" }
  | { kind: "put"; layer: DayLayer }
  | { kind: "delete" };

/**
 * Что сделать с изменением на дату — против ШАБЛОНА, а не против текущих
 * часов: черновик, равный шаблону, — это «вернуть к шаблону» (если изменение
 * есть) или «ничего» (если его нет). Лишний override привязал бы дату к
 * себе: будущая правка недели её бы не тронула.
 */
export function dateActionFor(snap: DaySnapshot, draft: DayDraft): DateAction {
  const desired = draftLayer(draft);
  if (sameLayer(desired, snap.template)) {
    return snap.exception ? { kind: "delete" } : { kind: "none" };
  }
  if (snap.exception && sameLayer(desired, exceptionLayer(snap.exception))) {
    return { kind: "none" };
  }
  return { kind: "put", layer: desired };
}

export interface TimeOffPlan {
  create: DraftTimeOff[];
  remove: DayTimeOff[];
}

function sameWindow(a: { from: string; to: string; reason: string }, b: { from: string; to: string; reason: string }): boolean {
  return a.from === b.from && a.to === b.to && a.reason.trim() === b.reason.trim();
}

/** Правка недоступности — новая строка и снятие старой (PATCH нет). */
export function timeOffPlanFor(snap: DaySnapshot, draft: DayDraft): TimeOffPlan {
  if (!draft.working) return { create: [], remove: [] };
  const live = snap.timeOff.filter((o) => o.editable);
  const create: DraftTimeOff[] = [];
  const remove: DayTimeOff[] = [];
  const kept = new Set<string>();
  for (const item of draft.timeOff) {
    const old = item.id ? live.find((o) => o.id === item.id) : undefined;
    if (old && sameWindow(old, item)) {
      kept.add(old.id);
      continue;
    }
    create.push(item);
  }
  for (const old of live) {
    if (!kept.has(old.id)) remove.push(old);
  }
  return { create, remove };
}

export interface Window {
  from: string;
  to: string;
}

/**
 * Окна, которые правка ОТНИМАЕТ у записи, — их проверяет предпросмотр до
 * записи. Выходной из рабочего — сутки целиком; сокращение — отрезанные края;
 * новая недоступность — её окно. Открытие времени ничего не отнимает.
 */
export function closingWindows(snap: DaySnapshot, draft: DayDraft): Window[] {
  const before = snapshotLayer(snap);
  const after = draftLayer(draft);
  const out: Window[] = [];
  if (before.working && before.start && before.end) {
    if (!after.working) {
      out.push({ from: "00:00", to: END_OF_DAY });
    } else if (after.start && after.end) {
      if (toMinutes(after.start) > toMinutes(before.start)) {
        out.push({ from: before.start, to: after.start });
      }
      if (toMinutes(after.end) < toMinutes(before.end)) {
        out.push({ from: after.end, to: before.end });
      }
    }
  }
  for (const item of timeOffPlanFor(snap, draft).create) {
    out.push({ from: item.from, to: item.to });
  }
  return out;
}

export type Step =
  | { kind: "time_off_create"; item: DraftTimeOff }
  | { kind: "time_off_delete"; item: DayTimeOff }
  | { kind: "date_put"; layer: DayLayer }
  | { kind: "date_delete" };

function narrows(before: DayLayer, after: DayLayer): boolean {
  if (!before.working) return false;
  if (!after.working) return true;
  if (!before.start || !before.end || !after.start || !after.end) return false;
  return toMinutes(after.start) > toMinutes(before.start) || toMinutes(after.end) < toMinutes(before.end);
}

/**
 * Порядок записи: сначала то, что ЗАКРЫВАЕТ время, потом то, что открывает.
 * Сбой посередине тогда оставляет мастера скорее занятым, чем отданным под
 * запись поверх недоступности. Правка недоступности — новая строка раньше
 * снятия старой: сбой создания не оставит время открытым.
 */
export function stepsFor(snap: DaySnapshot, draft: DayDraft): Step[] {
  const action = dateActionFor(snap, draft);
  const plan = timeOffPlanFor(snap, draft);
  const before = snapshotLayer(snap);
  const dateStep: Step | null =
    action.kind === "put"
      ? { kind: "date_put", layer: action.layer }
      : action.kind === "delete"
        ? { kind: "date_delete" }
        : null;
  const after = action.kind === "delete" ? snap.template : draftLayer(draft);
  const dateCloses = dateStep !== null && narrows(before, after);
  const closing: Step[] = plan.create.map((item) => ({ kind: "time_off_create", item }));
  const opening: Step[] = plan.remove.map((item) => ({ kind: "time_off_delete", item }));
  if (dateStep && dateCloses) closing.push(dateStep);
  if (dateStep && !dateCloses) opening.push(dateStep);
  return [...closing, ...opening];
}

/** Живое состояние даты совпало с черновиком — правка применена. */
export function draftApplied(snap: DaySnapshot, draft: DayDraft): boolean {
  if (!sameLayer(snapshotLayer(snap), draftLayer(draft))) return false;
  if (!draft.working) return true;
  const live = snap.timeOff.filter((o) => o.editable);
  if (live.length !== draft.timeOff.length) return false;
  return draft.timeOff.every((item) => live.some((o) => sameWindow(o, item)));
}

/** Живое состояние даты разошлось с тем, на котором человек правил. */
export function snapshotsDiffer(a: DaySnapshot, b: DaySnapshot): boolean {
  if (!sameLayer(a.template, b.template)) return true;
  if ((a.exception === null) !== (b.exception === null)) return true;
  if (a.exception && b.exception && !sameLayer(exceptionLayer(a.exception), exceptionLayer(b.exception))) {
    return true;
  }
  const key = (o: DayTimeOff) => `${o.id}|${o.from}|${o.to}|${o.reason}`;
  const left = a.timeOff.map(key).sort().join(";");
  const right = b.timeOff.map(key).sort().join(";");
  return left !== right;
}

export function draftsEqual(a: DayDraft, b: DayDraft): boolean {
  if (a.working !== b.working || a.start !== b.start || a.end !== b.end) return false;
  if (a.timeOff.length !== b.timeOff.length) return false;
  return a.timeOff.every((item, i) => {
    const other = b.timeOff[i];
    return other !== undefined && item.id === other.id && sameWindow(item, other);
  });
}

// ---------------------------------------------------------------------------
// Конфликт
// ---------------------------------------------------------------------------

export interface ConflictRow {
  appointmentId: string;
  from: string;
  to: string;
  service: string | null;
  /** «Анна К.» из дня мастера — у предпросмотра клиента нет по построению. */
  client: string | null;
  /** Запись есть в зеркале — её карточку можно открыть. */
  linkable: boolean;
}

/**
 * Записи из предпросмотров — без повторов, по времени. Имя клиента и право
 * открыть карточку — из дня мастера, по ТОМУ ЖЕ номеру: у обоих это номер
 * записи в каталоге (`appointment_id`). Предпросмотр не разобран целиком —
 * `null`: «записей нет» сказать нельзя.
 */
export function conflictRows(
  impacts: readonly ScheduleImpact[],
  bookings: readonly MasterDayBooking[],
): ConflictRow[] | null {
  const seen = new Map<string, ConflictRow>();
  for (const impact of impacts) {
    if (impact.bookings.state === "unreadable" || impact.bookings.state === "absent") return null;
    if (impact.bookings.unreadable_rows > 0) return null;
    for (const row of impact.bookings.rows) {
      if (seen.has(row.appointment_id)) continue;
      const start = parseSalonWallClock(row.start_local);
      const end = parseSalonWallClock(row.end_local);
      const booking = bookings.find((b) => b.booking_id === row.appointment_id);
      const client = booking
        ? [booking.client_first_name.trim(), booking.client_last_initial.trim()].filter(Boolean).join(" ") || null
        : null;
      seen.set(row.appointment_id, {
        appointmentId: row.appointment_id,
        from: start?.hm ?? "",
        to: end?.hm ?? "",
        service: row.service_name,
        client,
        linkable: booking !== undefined,
      });
    }
  }
  return [...seen.values()].sort((a, b) => a.from.localeCompare(b.from));
}

// ---------------------------------------------------------------------------
// Отказы записи
// ---------------------------------------------------------------------------

export type WriteFailure =
  | { kind: "conflict" }
  | { kind: "stale" }
  | { kind: "forbidden" }
  /** Ушло, ответ потерян: могло записаться. Только перечитывание скажет. */
  | { kind: "unknown" }
  | { kind: "break_crosses"; from: string; to: string }
  | { kind: "failed" };

export function classifyWriteError(err: unknown): WriteFailure {
  if (err instanceof ApiError) {
    if (err.status === 409 && err.slug === "has_active_appointments") return { kind: "conflict" };
    if (err.status === 401) return { kind: "stale" };
    if (err.status === 403) return { kind: "forbidden" };
    if (err.status === 503 && err.slug === "salon_write_outcome_unknown") return { kind: "unknown" };
    // 502/504 без тела — ответ потерян по дороге; 404 на снятии — уже снято
    // или не было: решает перечитывание, а не догадка.
    if ((err.status === 502 || err.status === 504) && err.slug === "http_error") return { kind: "unknown" };
    if (err.status === 404) return { kind: "unknown" };
    if (err.status === 400 && err.details?.reason === "template_break_crosses_hours") {
      const from = typeof err.details.break_start === "string" ? err.details.break_start : "";
      const to = typeof err.details.break_end === "string" ? err.details.break_end : "";
      return { kind: "break_crosses", from, to };
    }
    return { kind: "failed" };
  }
  // fetch бросает TypeError, когда запрос ушёл, а ответ не пришёл.
  if (err instanceof TypeError) return { kind: "unknown" };
  return { kind: "failed" };
}

// ---------------------------------------------------------------------------
// Окно записи и черновик
// ---------------------------------------------------------------------------

/** Каталог принимает initData не старше 600 с; запас — минута на сами шаги. */
export const WRITE_WINDOW_S = 600;
export const WRITE_MARGIN_S = 60;

/** Сколько секунд назад открыто Mini App; `authDate` — `launchAuthDate()` из max-sdk. */
export function launchAgeSeconds(authDate: number | null, nowMs: number = Date.now()): number | null {
  if (authDate === null) return null;
  return Math.floor(nowMs / 1000) - authDate;
}

export function writeWindowClosed(authDate: number | null, nowMs: number = Date.now()): boolean {
  const age = launchAgeSeconds(authDate, nowMs);
  return age !== null && age > WRITE_WINDOW_S - WRITE_MARGIN_S;
}

const DRAFT_PREFIX = "max:wt-draft:";
/** Черновик переживает переоткрытие Mini App — но не дольше часа. */
export const DRAFT_TTL_MS = 60 * 60 * 1000;

function draftKey(masterId: string, ymd: string): string {
  return `${DRAFT_PREFIX}${masterId}:${ymd}`;
}

export function saveDraft(masterId: string, ymd: string, draft: DayDraft, nowMs: number = Date.now()): void {
  try {
    window.localStorage.setItem(draftKey(masterId, ymd), JSON.stringify({ savedAt: nowMs, draft }));
  } catch {
    /* частный режим / квота — черновик просто не переживёт переоткрытие */
  }
}

export function loadDraft(masterId: string, ymd: string, nowMs: number = Date.now()): DayDraft | null {
  try {
    const raw = window.localStorage.getItem(draftKey(masterId, ymd));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { savedAt?: unknown; draft?: unknown };
    if (typeof parsed.savedAt !== "number" || nowMs - parsed.savedAt > DRAFT_TTL_MS) {
      window.localStorage.removeItem(draftKey(masterId, ymd));
      return null;
    }
    const draft = parsed.draft as DayDraft | undefined;
    if (!draft || typeof draft.working !== "boolean" || !Array.isArray(draft.timeOff)) return null;
    return draft;
  } catch {
    return null;
  }
}

export function clearDraft(masterId: string, ymd: string): void {
  try {
    window.localStorage.removeItem(draftKey(masterId, ymd));
  } catch {
    /* нечего снимать */
  }
}
