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
  MasterDay,
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
    /** Список не разобран — «изменений нет» сказать нельзя. */
    unreadable: "Показано не всё: часть графика не удалось разобрать.",
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
    /** Список даты не разобран — запись по такому снимку стёрла бы невидимое. */
    unreadable: "Часть графика на эту дату не удалось разобрать — изменять её пока нельзя.",
    /** Черновик пережил переоткрытие (окно записи ~10 минут). */
    restored: "Изменения этой даты, которые вы не сохранили, восстановлены.",
    reset: "Сбросить",
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

/** Настоящая дата: «2026-13-45» формой похожа, но такого дня нет. */
export function isYmd(value: string): boolean {
  const m = YMD.exec(value);
  if (!m) return false;
  return fromUtc(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))) === value;
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

function weekdayRow(schedule: MasterSchedule, ymd: string) {
  return schedule.days.find((d) => d.day_of_week === weekdayOf(ymd));
}

/** День недели из шаблона. */
export function templateLayer(schedule: MasterSchedule, ymd: string): DayLayer {
  const row = weekdayRow(schedule, ymd);
  if (!row || !row.is_working_day) return OFF;
  return { working: true, start: hm(row.start_time), end: hm(row.end_time) };
}

/** Перерыв дня недели — только для сверки (отдельной сущности «перерыв» нет). */
export function templateBreak(schedule: MasterSchedule, ymd: string): [string, string] | null {
  const row = weekdayRow(schedule, ymd);
  const from = hm(row?.break_start);
  const to = hm(row?.break_end);
  return row?.is_working_day && from && to ? [from, to] : null;
}

export function exceptionOn(exceptions: MasterExceptions, ymd: string): MasterExceptionRow | null {
  return exceptions.exceptions.rows.find((r) => r.date === ymd) ?? null;
}

export function exceptionLayer(row: MasterExceptionRow): DayLayer {
  return row.is_working_day ? { working: true, start: hm(row.start), end: hm(row.end) } : OFF;
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
  /** Начинается и кончается в этой дате — можно править и снимать. */
  editable: boolean;
  startYmd: string;
  endYmd: string;
}

/**
 * Недоступность, задевающая дату. Многодневная и переходящая через полночь
 * — только чтение: корзина на ней сняла бы весь отпуск, а не одну дату.
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
    out.push({
      id: row.id,
      from: start.ymd < ymd ? "00:00" : start.hm,
      to: end.ymd > ymd ? END_OF_DAY : end.hm,
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

/**
 * Недоступность закрывает весь рабочий день — в том числе несколькими
 * смежными строками (13–15 и 15–19 вместе закрывают 13–19).
 */
export function coversWholeDay(
  hours: { start: string; end: string } | null,
  offs: ReadonlyArray<{ from: string; to: string }>,
): boolean {
  let cursor = hours ? toMinutes(hours.start) : 0;
  const end = hours ? toMinutes(hours.end) : 24 * 60;
  const sorted = [...offs].sort((a, b) => toMinutes(a.from) - toMinutes(b.from));
  for (const off of sorted) {
    if (toMinutes(off.from) > cursor) break;
    cursor = Math.max(cursor, toMinutes(off.to));
    if (cursor >= end) return true;
  }
  return cursor >= end;
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
  /** Салон закрыт часть дня: «10:00–14:00». */
  closedPart: string | null;
}

/**
 * Неделя — ЧАСАМИ СЕРВЕРА: `working_hours` дня из `day-schedule` (их считает
 * сервер по шаблону и изменениям на даты, по ним и продают). Сверху — только
 * пометки из списков того же сервера: изменение на дату, недоступность,
 * закрытие салона. Свободного времени экран не считает.
 */
export function weekRows(
  monday: string,
  days: readonly MasterDay[],
  exceptions: MasterExceptions,
  today: string,
): WeekRow[] {
  return weekDates(monday).map((ymd) => {
    const hours = days.find((d) => d.date === ymd)?.working_hours ?? null;
    const offs = timeOffOn(exceptions.time_off.rows, ymd);
    const closure = closureOn(exceptions.closures.rows, ymd);
    const base = {
      ymd,
      past: ymd < today,
      override: exceptionOn(exceptions, ymd) !== null,
      partial: [] as string[],
      closedPart: null as string | null,
    };
    if (closure && !(closure.start && closure.end)) return { ...base, status: "closed", hours: null };
    if (!hours) return { ...base, status: "off", hours: null };
    if (coversWholeDay(hours, offs)) return { ...base, status: "unavailable", hours: null };
    return {
      ...base,
      status: "working",
      hours: `${hours.start} – ${hours.end}`,
      partial: offs.map((o) => `${o.from}–${o.to}`),
      closedPart: closure?.start && closure.end ? `${closure.start}–${closure.end}` : null,
    };
  });
}

/** Ни одного рабочего дня в шаблоне и ни одного изменения на неделе. */
export function scheduleNotSet(schedule: MasterSchedule, exceptions: MasterExceptions): boolean {
  return !schedule.has_working_day && exceptions.exceptions.rows.length === 0;
}

/** Списки, по которым пишется дата, разобраны не целиком. */
export function listsUnreadable(exceptions: MasterExceptions): boolean {
  return (
    exceptions.exceptions.state === "unreadable" ||
    exceptions.time_off.state === "unreadable" ||
    exceptions.unreadable_lists.some((k) => k === "exceptions" || k === "time_off")
  );
}

// ---------------------------------------------------------------------------
// Снимок даты и черновик-намерение
// ---------------------------------------------------------------------------

export interface DaySnapshot {
  ymd: string;
  template: DayLayer;
  templateBreak: [string, string] | null;
  exception: MasterExceptionRow | null;
  timeOff: DayTimeOff[];
}

export function snapshotOf(
  ymd: string,
  schedule: MasterSchedule,
  exceptions: MasterExceptions,
): DaySnapshot {
  return {
    ymd,
    template: templateLayer(schedule, ymd),
    templateBreak: templateBreak(schedule, ymd),
    exception: exceptionOn(exceptions, ymd),
    timeOff: timeOffOn(exceptions.time_off.rows, ymd),
  };
}

export function snapshotLayer(snap: DaySnapshot): DayLayer {
  return snap.exception ? exceptionLayer(snap.exception) : snap.template;
}

/**
 * Отпечаток снимка — «на чём человек правил». Черновик несёт его с собой:
 * разошёлся с живым — значит, дату кто-то менял, и писать по черновику
 * вслепую нельзя.
 */
export function snapshotKey(snap: DaySnapshot): string {
  const layer = (l: DayLayer) => (l.working ? `${l.start}-${l.end}` : "off");
  const ex = snap.exception
    ? `${layer(exceptionLayer(snap.exception))}|${snap.exception.break_start ?? ""}-${snap.exception.break_end ?? ""}|${snap.exception.note ?? ""}`
    : "none";
  const offs = snap.timeOff
    .map((o) => `${o.id}:${o.from}-${o.to}:${o.reason}:${o.editable ? "e" : "r"}`)
    .sort()
    .join(",");
  return [layer(snap.template), snap.templateBreak?.join("-") ?? "", ex, offs].join("#");
}

export function snapshotsDiffer(a: DaySnapshot, b: DaySnapshot): boolean {
  return snapshotKey(a) !== snapshotKey(b);
}

export interface AddedTimeOff {
  /** Ключ строки на экране. */
  key: string;
  from: string;
  to: string;
  reason: string;
}

/**
 * Черновик — НАМЕРЕНИЯ, а не «желаемое состояние целиком»: что добавить,
 * какие существующие строки снять, трогали ли часы. Снимается только то, что
 * человек снял сам: чужая недоступность, появившаяся после открытия, правкой
 * не задевается.
 */
export interface DayDraft {
  /** Отпечаток снимка, на котором правили (`snapshotKey`). */
  basis: string;
  /**
   * Часы даты на том же снимке. Разошлись с живыми — часы менял кто-то
   * другой, и правка часов человека писалась бы поверх невидимой ему чужой.
   */
  basisLayer: DayLayer;
  /** Часы или «рабочий день» меняли. Нет — о дне черновик ничего не говорит. */
  hoursTouched: boolean;
  working: boolean;
  /** Часы хранятся и у выходного: тумблер назад их возвращает. */
  start: string;
  end: string;
  added: AddedTimeOff[];
  /** Сохранённые строки, которые человек снял (правка = снятие + новая). */
  removedIds: string[];
}

export function freshDraft(snap: DaySnapshot, fallback: [string, string]): DayDraft {
  const layer = snapshotLayer(snap);
  return {
    basis: snapshotKey(snap),
    basisLayer: layer,
    hoursTouched: false,
    working: layer.working,
    start: layer.start ?? fallback[0],
    end: layer.end ?? fallback[1],
    added: [],
    removedIds: [],
  };
}

export function draftLayer(draft: DayDraft): DayLayer {
  return draft.working ? { working: true, start: draft.start, end: draft.end } : OFF;
}

export function hasIntent(draft: DayDraft): boolean {
  return draft.hoursTouched || draft.added.length > 0 || draft.removedIds.length > 0;
}

function sameOff(
  a: { from: string; to: string; reason: string },
  b: { from: string; to: string; reason: string },
): boolean {
  return a.from === b.from && a.to === b.to && a.reason.trim() === b.reason.trim();
}

export interface VisibleTimeOff {
  key: string;
  /** Сохранённая строка — её номер; новая — `null`. */
  id: string | null;
  from: string;
  to: string;
  reason: string;
}

/** Что на экране: сохранённые правимые строки минус снятые, плюс новые. */
export function visibleTimeOff(snap: DaySnapshot, draft: DayDraft): VisibleTimeOff[] {
  const kept = snap.timeOff
    .filter((o) => o.editable && !draft.removedIds.includes(o.id))
    .map((o) => ({ key: o.id, id: o.id, from: o.from, to: o.to, reason: o.reason }));
  const fresh = draft.added.map((a) => ({ key: a.key, id: null, from: a.from, to: a.to, reason: a.reason }));
  return [...kept, ...fresh].sort((a, b) => toMinutes(a.from) - toMinutes(b.from));
}

/**
 * Перенести намерения на снимок — после «Расписание могло измениться», после
 * частичной записи, при восстановлении черновика:
 *
 * * снятие строки, которой больше нет, — уже сделано;
 * * новая строка, которая уже есть, — уже сделана;
 * * новая строка, равная снятой, — пара ничего не меняет;
 * * часы, совпавшие с живыми, — больше не изменение; не тронутые — живые;
 * * часы, которые после начала правки изменил кто-то другой, — живые: правка
 *   человека легла бы поверх чужой, которой он не видел. Экран показывает
 *   новые часы под «Расписание могло измениться», решает человек.
 */
export function rebaseDraft(snap: DaySnapshot, draft: DayDraft): DayDraft {
  const live = snap.timeOff.filter((o) => o.editable);
  let removedIds = draft.removedIds.filter((id) => live.some((o) => o.id === id));
  const added: AddedTimeOff[] = [];
  for (const item of draft.added) {
    const pair = live.find((o) => removedIds.includes(o.id) && sameOff(o, item));
    if (pair) {
      removedIds = removedIds.filter((id) => id !== pair.id);
      continue;
    }
    if (live.some((o) => !removedIds.includes(o.id) && sameOff(o, item))) continue;
    added.push(item);
  }
  const layer = snapshotLayer(snap);
  const foreignHours = !sameLayer(draft.basisLayer, layer);
  const hoursTouched = draft.hoursTouched && !foreignHours && !sameLayer(draftLayer(draft), layer);
  return {
    basis: snapshotKey(snap),
    basisLayer: layer,
    hoursTouched,
    working: hoursTouched ? draft.working : layer.working,
    start: hoursTouched ? draft.start : (layer.start ?? draft.start),
    end: hoursTouched ? draft.end : (layer.end ?? draft.end),
    added,
    removedIds,
  };
}

export type DateAction =
  | { kind: "none" }
  | { kind: "put"; layer: DayLayer }
  | { kind: "delete" };

/**
 * Изменение на дату несёт что-то, кроме часов: свою заметку или свой
 * перерыв. Перерыв читается как на сервере (`_prepare_date_exception`):
 *
 * * у выходного перерыва нет по построению, и к выходному шаблона перерыв
 *   ничего не значит — несёт только заметка;
 * * свой перерыв, не равный обеду шаблона, — несёт;
 * * своего перерыва нет, а обед шаблона в часы изменения помещался — «без
 *   обеда» поставлено намеренно, несёт; не помещался — обед выпал из-за
 *   часов (дату сужали), и снятие изменения вернёт его;
 * * прежний сервер этих полей не отдаёт — неизвестно, значит несёт: снять
 *   вслепую — потерять заметку.
 */
function exceptionCarriesMore(snap: DaySnapshot): boolean {
  const ex = snap.exception;
  if (!ex) return false;
  if (ex.note === undefined || ex.note.trim() !== "") return true;
  if (!ex.is_working_day || !snap.template.working) return false;
  if (ex.break_start === undefined || ex.break_end === undefined) return true;
  const weekday = snap.templateBreak;
  const ownFrom = hm(ex.break_start);
  const ownTo = hm(ex.break_end);
  if (ownFrom && ownTo) return !weekday || ownFrom !== weekday[0] || ownTo !== weekday[1];
  if (!weekday) return false;
  const from = hm(ex.start);
  const to = hm(ex.end);
  return Boolean(from && to && from <= weekday[0] && weekday[1] <= to);
}

/**
 * Что сделать с изменением на дату. Часы не трогали — ничего (даже если
 * изменение совпадает с шаблоном: снимать его молча — терять его перерыв и
 * заметку). Вернули к шаблону — снять изменение, если оно ничего, кроме
 * часов, не несёт; несёт — переписать часы, сохранив остальное.
 */
export function dateActionFor(snap: DaySnapshot, draft: DayDraft): DateAction {
  if (!draft.hoursTouched) return { kind: "none" };
  const desired = draftLayer(draft);
  if (sameLayer(desired, snapshotLayer(snap))) return { kind: "none" };
  if (snap.exception && sameLayer(desired, snap.template) && !exceptionCarriesMore(snap)) {
    return { kind: "delete" };
  }
  return { kind: "put", layer: desired };
}

export interface TimeOffPlan {
  create: AddedTimeOff[];
  remove: DayTimeOff[];
}

export function timeOffPlanFor(snap: DaySnapshot, draft: DayDraft): TimeOffPlan {
  if (!draft.working) return { create: [], remove: [] };
  const rebased = rebaseDraft(snap, draft);
  return {
    create: rebased.added,
    remove: snap.timeOff.filter((o) => o.editable && rebased.removedIds.includes(o.id)),
  };
}

export interface Window {
  from: string;
  to: string;
}

function layerAfter(snap: DaySnapshot, action: DateAction): DayLayer {
  if (action.kind === "put") return action.layer;
  if (action.kind === "delete") return snap.template;
  return snapshotLayer(snap);
}

/**
 * Окна, которые правка ОТНИМАЕТ у записи, — их проверяет предпросмотр до
 * записи. Выходной из рабочего — сутки целиком; сокращение — отрезанные края;
 * новая недоступность — её окно. Открытие времени ничего не отнимает.
 */
export function closingWindows(snap: DaySnapshot, draft: DayDraft): Window[] {
  const before = snapshotLayer(snap);
  const after = layerAfter(snap, dateActionFor(snap, draft));
  const out: Window[] = [];
  if (before.working && before.start && before.end) {
    if (!after.working) {
      out.push({ from: "00:00", to: END_OF_DAY });
    } else if (after.start && after.end) {
      if (toMinutes(after.start) > toMinutes(before.start)) out.push({ from: before.start, to: after.start });
      if (toMinutes(after.end) < toMinutes(before.end)) out.push({ from: after.end, to: before.end });
    }
  }
  for (const item of timeOffPlanFor(snap, draft).create) out.push({ from: item.from, to: item.to });
  return out;
}

export type Step =
  | { kind: "time_off_create"; item: AddedTimeOff }
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
  const dateStep: Step | null =
    action.kind === "put"
      ? { kind: "date_put", layer: action.layer }
      : action.kind === "delete"
        ? { kind: "date_delete" }
        : null;
  const dateCloses = dateStep !== null && narrows(snapshotLayer(snap), layerAfter(snap, action));
  const closing: Step[] = plan.create.map((item) => ({ kind: "time_off_create", item }));
  const opening: Step[] = plan.remove.map((item) => ({ kind: "time_off_delete", item }));
  if (dateStep && dateCloses) closing.push(dateStep);
  if (dateStep && !dateCloses) opening.push(dateStep);
  return [...closing, ...opening];
}

/** Живое состояние даты исполнило намерения — правка применена. */
export function draftApplied(snap: DaySnapshot, draft: DayDraft): boolean {
  if (draft.hoursTouched && !sameLayer(snapshotLayer(snap), draftLayer(draft))) return false;
  if (!draft.working) return true;
  const live = snap.timeOff.filter((o) => o.editable);
  if (draft.removedIds.some((id) => live.some((o) => o.id === id))) return false;
  return draft.added.every((item) => live.some((o) => sameOff(o, item)));
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
  /** Снимать уже нечего — сделано (404 на снятии). */
  | { kind: "gone" }
  | { kind: "break_crosses"; from: string; to: string }
  | { kind: "failed" };

/**
 * `step` — что делали: 404 на СНЯТИИ значит «уже снято», а на записи —
 * «мастер не найден», ничего не записано.
 */
export function classifyWriteError(err: unknown, step: Step["kind"] = "date_put"): WriteFailure {
  if (err instanceof ApiError) {
    if (err.status === 409 && err.slug === "has_active_appointments") return { kind: "conflict" };
    if (err.status === 401) return { kind: "stale" };
    if (err.status === 403) return { kind: "forbidden" };
    if (err.status === 503 && err.slug === "salon_write_outcome_unknown") return { kind: "unknown" };
    // 502/504 без тела — ответ потерян по дороге.
    if ((err.status === 502 || err.status === 504) && err.slug === "http_error") return { kind: "unknown" };
    if (err.status === 404) {
      return step === "time_off_delete" || step === "date_delete" ? { kind: "gone" } : { kind: "failed" };
    }
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

/**
 * Сколько секунд назад открыто Mini App — по часам ТОГО ЖЕ устройства, что и
 * «сейчас» (`startedAtMs` — момент загрузки приложения). Не по `auth_date`:
 * спешащие часы телефона дали бы вечное «откройте заново», и переоткрытие бы
 * не помогло. Последнее слово — за сервером (`401 stale`).
 */
export function launchAgeSeconds(startedAtMs: number, nowMs: number = Date.now()): number {
  return Math.floor((nowMs - startedAtMs) / 1000);
}

export function writeWindowClosed(startedAtMs: number, nowMs: number = Date.now()): boolean {
  return launchAgeSeconds(startedAtMs, nowMs) > WRITE_WINDOW_S - WRITE_MARGIN_S;
}

const DRAFT_PREFIX = "max:wt-draft:";
/** Черновик переживает переоткрытие Mini App — но не дольше часа с последней правки. */
export const DRAFT_TTL_MS = 60 * 60 * 1000;

function draftKey(masterId: string, ymd: string): string {
  return `${DRAFT_PREFIX}${masterId}:${ymd}`;
}

/** Пишется только по правке человека: открытие даты срок черновика не продлевает. */
export function saveDraft(masterId: string, ymd: string, draft: DayDraft, nowMs: number = Date.now()): void {
  try {
    window.localStorage.setItem(draftKey(masterId, ymd), JSON.stringify({ savedAt: nowMs, draft }));
  } catch {
    /* частный режим / квота — черновик просто не переживёт переоткрытие */
  }
}

function isText(v: unknown): v is string {
  return typeof v === "string";
}

function isLayer(v: unknown): v is DayLayer {
  if (!v || typeof v !== "object") return false;
  const l = v as Record<string, unknown>;
  const time = (t: unknown) => t === null || isText(t);
  return typeof l.working === "boolean" && time(l.start) && time(l.end);
}

function isDraft(v: unknown): v is DayDraft {
  if (!v || typeof v !== "object") return false;
  const d = v as Record<string, unknown>;
  return (
    isText(d.basis) &&
    isLayer(d.basisLayer) &&
    typeof d.hoursTouched === "boolean" &&
    typeof d.working === "boolean" &&
    isText(d.start) &&
    isText(d.end) &&
    Array.isArray(d.removedIds) &&
    d.removedIds.every(isText) &&
    Array.isArray(d.added) &&
    d.added.every((a) => {
      const r = a as Record<string, unknown> | null;
      return !!r && isText(r.key) && isText(r.from) && isText(r.to) && isText(r.reason);
    })
  );
}

export function loadDraft(masterId: string, ymd: string, nowMs: number = Date.now()): DayDraft | null {
  try {
    const raw = window.localStorage.getItem(draftKey(masterId, ymd));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { savedAt?: unknown; draft?: unknown };
    if (typeof parsed.savedAt !== "number" || nowMs - parsed.savedAt > DRAFT_TTL_MS || !isDraft(parsed.draft)) {
      window.localStorage.removeItem(draftKey(masterId, ymd));
      return null;
    }
    return parsed.draft;
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
