/**
 * «Расписание» салона v2 (DRF-2933, макет DRF-2854 FROZEN v1.0) — расчёты экрана.
 *
 * Здесь нет ни одного запроса и ни одной разметки: только то, что экран
 * выводит из ответов сервера. Три правила держатся этим файлом.
 *
 * # Экран не считает доступность
 *
 * Свободное окно — это строка `free_windows` из `day-schedule`, и ничто
 * другое: её считает сервер по смене, записям, блокам и перерыву. Вычесть
 * записи из смены здесь значило бы завести ещё один вычислитель свободного
 * времени (§17, DRF-1637). Сетка и лента поэтому свободных окон не знают
 * вовсе, пока их не запросили у сервера на конкретного мастера.
 *
 * # Раскладка выбирается числом мастеров, а не вкусом
 *
 * Заморозка DRF-2854 (MUST FIX 1 и 6): три и более колонок мастеров — не
 * обязательная мобильная раскладка, а компактный обзор, пока он читается;
 * большая команда — выбор и фильтр, не горизонтальная сетка. Отсюда
 * {@link layoutFor}: один мастер — его день списком, от двух до четырёх —
 * сетка кадра 1, больше — лента по времени (DRF-1237, «Режим Все»).
 *
 * # Устаревшее окно — не действие
 *
 * Заморозка, MUST FIX 7: «не выдавать stale availability за доступный слот».
 * Окно годится для перехода в запись, пока его ответ свежий
 * ({@link windowsFresh}); создание записи всё равно проверяет время ещё раз.
 *
 * # Где окно сервера не авторитетно — и экран его не показывает
 *
 * `build_schedule` не знает «сейчас» и не читает закрытия салона: на
 * прошедшей дате и в уже прошедшие часы сегодня он отдаёт «свободно» поверх
 * состоявшихся визитов, а в день закрытого салона — обычные окна, которые
 * каталог при записи отклонит. {@link liveWindows} их отсекает. Это не
 * расчёт доступности — ни одно окно здесь не создаётся и не двигается, —
 * а отказ показывать окно, про которое известно, что оно не продаётся.
 *
 * # Записи — всегда из одного источника
 *
 * Записи любого вида берутся из дня салона (зеркало): там все статусы и
 * `service_id`. `day-schedule` отдаёт только забронированные и без услуги;
 * возьми список мастера оттуда — один и тот же день в сетке и в списке
 * выглядел бы по-разному. Из `day-schedule` экран берёт часы, блоки, окна и
 * конфликты.
 */
import {
  RELEASED_VISIT_STATUSES,
  type CatalogServiceLite,
  type FrameInterval,
  type MasterDay,
  type MasterDayConflict,
  type MasterListItem,
  type SalonDayFrame,
  type SalonDayResponse,
  type SalonDayVisit,
  type SalonFrameMaster,
} from "./admin-api";
import { parseSalonWallClock } from "./format";
import {
  END_OF_DAY,
  addDays,
  fromMinutes,
  hm,
  isYmd,
  salonToday,
  toMinutes,
  weekStart,
  weekdayOf,
} from "./salon-working-time";

export const SCHEDULE_ROOT = "/admin/schedule";

/** Сколько мастеров ещё читается колонками на телефоне — столько их на кадре 1. */
export const GRID_MAX_MASTERS = 4;

/** С какого числа мастеров в шторке появляется поиск (MUST FIX 6). */
export const MASTER_SEARCH_FROM = 8;

/** Сколько живёт ответ со свободными окнами как основание для перехода в запись. */
export const FREE_WINDOWS_FRESH_MS = 5 * 60 * 1000;

export const SS_COPY = {
  title: "Расписание",
  calendar: "Выбрать дату",
  filtersButton: "Фильтры",
  allMasters: "Все мастера",
  allServices: "Все услуги",
  mastersCount: (n: number) => `${n} ${plural(n, "мастер", "мастера", "мастеров")}`,
  servicesCount: (n: number) => `${n} ${plural(n, "услуга", "услуги", "услуг")}`,
  sheet: {
    title: "Фильтры",
    range: "Диапазон дат",
    day: "День",
    week: "Неделя",
    month: "Месяц",
    prevWeek: "Предыдущая неделя",
    nextWeek: "Следующая неделя",
    prevMonth: "Предыдущий месяц",
    nextMonth: "Следующий месяц",
    masters: "Мастера",
    services: "Услуги",
    search: "Поиск мастера",
    noMatches: "Никого не нашли.",
    noServices: "Список услуг не загрузился.",
    reset: "Сбросить",
    apply: "Применить",
  },
  free: "Свободно",
  book: "Записать",
  block: "Блок",
  breakLabel: "Перерыв",
  dayOff: "Выходной",
  hoursUnknown: "Часы неизвестны",
  /**
   * Утверждённая семантика DRF-1237: плановый интервал накрывает текущее
   * время. Не статус и не «клиент пришёл» — поэтому «по плану».
   */
  nowMarker: "сейчас по плану",
  masterUnknown: "Мастер не определён",
  noTime: "Без времени",
  newBooking: "Новая запись",
  freeWindowsFor: (name: string) => `Свободные окна: ${name}`,
  openMaster: (name: string) => `Расписание мастера: ${name}`,
  noFreeWindows: "Свободных промежутков нет.",
  noFreeWindowsFor: (name: string) => `${name}: свободных промежутков нет.`,
  freeLoading: "Загружаем свободные окна…",
  freeFailed: "Свободные окна не загрузились. Нажмите на колонку мастера ещё раз.",
  dayPartsFailed: "Часы, блоки и свободные окна мастера не загрузились — показаны только записи.",
  /** Канон DRF-1237: интервал — диапазон доступности, а не готовый слот. */
  freeNote:
    "Это диапазон доступности, а не готовый слот: свободное время проверяется ещё раз при создании записи.",
  /** Окно не свежее — заморозка, MUST FIX 7. */
  refresh: "Обновить",
  staleWindows: "Свободные окна могли измениться. Обновите расписание, чтобы записать.",
  /** «Явная пометка актуальности» (MUST FIX 7) — всегда, не только после сбоя. */
  asOf: (time: string) => `Актуально на ${time}`,
  closedFull: "В этот день салон закрыт",
  closedPart: (from: string, to: string) => `Салон закрыт ${from}–${to}`,
  closedNoWindows: "Свободные окна на этот день не показываются.",
  conflict: {
    double_booking: "Записи пересекаются по времени",
    outside_hours: "Запись вне рабочих часов",
    overlapping_exception: "Запись во время недоступности мастера",
    other: "Запись требует проверки",
  },
  /** Состояние подтверждения не прочитано — это третье состояние, не «не подтверждено». */
  confirmationUnread: "Состояние подтверждения не прочитано — это не значит «не подтверждено».",
  toWorkingTime: "Открыть в «Рабочем времени»",
  wholeDay: "весь день",
  noMastersTitle: "Нет мастеров",
  noMasters: "В салоне нет мастеров — расписание показывать некому.",
  noVisits: "Записей на этот день нет.",
  noVisitsForServices: "По выбранным услугам записей на этот день нет.",
  noRows: "На этот день у мастера ничего нет.",
  attention: "Требует внимания",
  /** Кадр не прочитан, день прочитан: смен нет, записи есть. */
  frameMissing: "Смены и недоступность не загрузились — показаны только записи.",
  frameUnreadable: (lists: string) =>
    `День показан не полностью: не удалось разобрать ${lists}. Показанное верно, но не всё.`,
  feedHint: "Мастеров больше четырёх — записи идут лентой по времени. Сетка по мастерам доступна при выборе до четырёх.",
  shifts: (n: number) => `Мастера и смены · ${n}`,
  timeline: "Записи дня",
  orphans: "Мастер не определён",
  master: {
    title: "Мастер",
    close: "Закрыть",
    schedule: "Расписание",
    about: "О мастере",
    specialization: "Специализация",
    experience: "Стаж",
    bio: "О себе",
    services: "Услуги",
    card: "Открыть карточку мастера",
    cardNote: "Недельный график и его подтверждение — в карточке мастера.",
    empty: "Сведений о мастере пока нет.",
  },
} as const;

const FRAME_LIST_TITLE: Record<string, string> = {
  working_intervals: "смены",
  breaks: "перерывы",
  absences: "отсутствия",
  closures: "закрытия салона",
};

export function frameListTitles(keys: readonly string[]): string {
  return keys.map((k) => FRAME_LIST_TITLE[k] ?? k).join(", ");
}

function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

// ---------------------------------------------------------------------------
// Даты
// ---------------------------------------------------------------------------

const WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"] as const;
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
const MONTHS_NOMINATIVE = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
] as const;

function ymdParts(ymd: string): [number, number, number] {
  const [y = 0, m = 1, d = 1] = ymd.split("-").map(Number);
  return [y, m, d];
}

export function weekdayShort(ymd: string): string {
  return WEEKDAYS_SHORT[weekdayOf(ymd)] ?? "";
}

export function dayNumber(ymd: string): number {
  return ymdParts(ymd)[2];
}

/** «Пн, 21 августа» — строка над заголовком, как на кадре 1. */
export function dateLine(ymd: string): string {
  const [, m, d] = ymdParts(ymd);
  return `${weekdayShort(ymd)}, ${d} ${MONTHS_GENITIVE[m - 1] ?? ""}`;
}

/** «21 августа, понедельник» — подпись дня для читалки. */
export function dayAria(ymd: string): string {
  const [, m, d] = ymdParts(ymd);
  return `${d} ${MONTHS_GENITIVE[m - 1] ?? ""}, ${weekdayShort(ymd)}`;
}

/** Первое число месяца, в котором лежит дата. */
export function monthStart(ymd: string): string {
  return `${ymd.slice(0, 7)}-01`;
}

export function addMonths(firstOfMonth: string, delta: number): string {
  const [y, m] = ymdParts(firstOfMonth);
  const total = y * 12 + (m - 1) + delta;
  const year = Math.floor(total / 12);
  const month = `${(total % 12) + 1}`.padStart(2, "0");
  return `${year}-${month}-01`;
}

/** «Август 2026». */
export function monthTitle(firstOfMonth: string): string {
  const [y, m] = ymdParts(firstOfMonth);
  return `${MONTHS_NOMINATIVE[m - 1] ?? ""} ${y}`;
}

/**
 * Месяц неделями, с понедельника: дни чужих месяцев — `null`, календарь их
 * не рисует (выбор «30 июля» на листе августа читается как ошибка).
 */
export function monthGrid(firstOfMonth: string): Array<Array<string | null>> {
  const month = firstOfMonth.slice(0, 7);
  const weeks: Array<Array<string | null>> = [];
  let cursor = weekStart(firstOfMonth);
  for (;;) {
    const week = Array.from({ length: 7 }, (_, i) => {
      const day = addDays(cursor, i);
      return day.startsWith(month) ? day : null;
    });
    if (week.every((d) => d === null)) break;
    weeks.push(week);
    cursor = addDays(cursor, 7);
  }
  return weeks;
}

/** Дата из адреса: только настоящая, иначе «нет» — и экран берёт сегодня салона. */
export function dateParam(raw: string | null): string | null {
  return raw && isYmd(raw) ? raw : null;
}

// ---------------------------------------------------------------------------
// Адреса
// ---------------------------------------------------------------------------

export function schedulePath(ymd: string | null): string {
  return ymd ? `${SCHEDULE_ROOT}?date=${ymd}` : SCHEDULE_ROOT;
}

export function scheduleMasterPath(masterId: string, ymd: string): string {
  return `${SCHEDULE_ROOT}/master/${encodeURIComponent(masterId)}?date=${ymd}`;
}

/**
 * Ручная запись с контекстом (заморозка, MUST FIX 2 и 5): мастер, дата и
 * окно, с которого начали. Услугу, клиента и окончательное время человек
 * выбирает там; запись отсюда не создаётся.
 */
export function newBookingPath(context: {
  date: string | null;
  masterId?: string | null;
  from?: string;
  to?: string;
  /** Откуда пришли: с «Расписания» или с дня мастера (кадр 12) — туда и возврат. */
  returnTo?: "schedule" | "schedule-master";
}): string {
  const q = new URLSearchParams({ return: context.returnTo ?? "schedule" });
  if (context.date) q.set("date", context.date);
  if (context.masterId) q.set("master_id", context.masterId);
  if (context.from && context.to) {
    q.set("from", context.from);
    q.set("to", context.to);
  }
  return `/admin/booking/new?${q.toString()}`;
}

// ---------------------------------------------------------------------------
// День салона колонками: записи из зеркала, смены из кадра
// ---------------------------------------------------------------------------

export interface RosterMaster {
  id: string;
  name: string;
  specialization: string;
  photoUrl: string;
  /** Карточка включена. К выключенному мастеру экран записать не предлагает. */
  isActive: boolean;
}

/**
 * Смена мастера. `unknown` — не «свободен» и не «выходной»: кадр не
 * прочитан, строка мастера в нём не нашлась или часы не разобраны. §29.5
 * запрещает подставлять неизвестное значением в любую сторону.
 */
export type Shift =
  | { kind: "working"; intervals: FrameInterval[] }
  | { kind: "off" }
  | { kind: "unknown" };

export interface ColumnBlock {
  kind: "block" | "break";
  start: string;
  end: string;
}

export interface ColumnVisit {
  id: string;
  /** Время суток салона; `null` — у записи нет времени, по оси её не поставить. */
  start: string | null;
  end: string | null;
  /** «Мария И.» */
  client: string;
  firstName: string;
  service: string;
  serviceId: string;
  /** Слот освобождён (отмена, неявка) — показывается приглушённо. */
  released: boolean;
  now: boolean;
}

export interface MasterColumn {
  master: RosterMaster;
  shift: Shift;
  blocks: ColumnBlock[];
  visits: ColumnVisit[];
}

export function clientLabel(first: string, initial: string): string {
  const name = first.trim();
  const letter = initial.trim();
  if (!name) return letter ? `${letter}.` : "Клиент";
  return letter ? `${name} ${letter.replace(/\.$/, "")}.` : name;
}

function endOf(visit: SalonDayVisit, start: string | null, day: string): string | null {
  if (!start) return null;
  if (visit.end_at) {
    const end = parseSalonWallClock(visit.end_at);
    if (end) return end.ymd > day ? END_OF_DAY : end.hm;
  }
  if (visit.duration_min > 0) return fromMinutes(Math.min(toMinutes(start) + visit.duration_min, 24 * 60));
  return null;
}

export function columnVisit(visit: SalonDayVisit, day: string): ColumnVisit {
  const at = visit.start_at ? parseSalonWallClock(visit.start_at) : null;
  const start = at ? at.hm : null;
  return {
    id: visit.id,
    start,
    end: endOf(visit, start, day),
    client: clientLabel(visit.client_first_name, visit.client_last_initial),
    firstName: visit.client_first_name.trim() || clientLabel(visit.client_first_name, visit.client_last_initial),
    service: visit.service_name,
    serviceId: visit.service_id,
    released: RELEASED_VISIT_STATUSES.has(visit.status),
    now: visit.is_in_progress,
  };
}

function parsedRows(list: SalonFrameMaster["breaks"]): FrameInterval[] {
  return list.state === "parsed" ? list.rows : [];
}

function shiftOf(row: SalonFrameMaster | undefined): Shift {
  if (!row) return { kind: "unknown" };
  if (!row.is_working_day) return { kind: "off" };
  const intervals = parsedRows(row.working_intervals);
  return intervals.length > 0 ? { kind: "working", intervals } : { kind: "unknown" };
}

function blocksOf(row: SalonFrameMaster | undefined): ColumnBlock[] {
  if (!row || !row.is_working_day) return [];
  const blocks: ColumnBlock[] = [
    ...parsedRows(row.absences).map((r) => ({ kind: "block" as const, start: r.start, end: r.end })),
    ...parsedRows(row.breaks).map((r) => ({ kind: "break" as const, start: r.start, end: r.end })),
  ];
  return blocks.sort((a, b) => toMinutes(a.start) - toMinutes(b.start));
}

/**
 * День салона колонками мастеров.
 *
 * Мастера — из дня салона (зеркало: все неархивные). Выключенная карточка
 * без записей не показывается, с записями — показывается: спрятать строку,
 * за которой стоят живые записи, значило бы спрятать день (то же решение,
 * что на «Сегодня»).
 *
 * Смена и блоки — из кадра, по `master_id` строки. Строка кадра без пары
 * ничья: приложить её к мастеру по имени значило бы угадать.
 */
export function buildColumns(
  day: SalonDayResponse,
  frame: SalonDayFrame | null,
  roster: readonly MasterListItem[],
): MasterColumn[] {
  const cards = new Map(roster.map((m) => [m.id, m]));
  const frames = new Map<string, SalonFrameMaster>();
  for (const row of frame?.masters ?? []) {
    if (row.master_id) frames.set(row.master_id, row);
  }
  return day.masters
    .filter((m) => m.is_active || m.visits.length > 0)
    .map((m) => {
      const card = cards.get(m.master_id);
      const row = frames.get(m.master_id);
      return {
        master: {
          id: m.master_id,
          name: m.name,
          specialization: card?.specialization ?? "",
          photoUrl: card?.photo_url ?? "",
          isActive: m.is_active,
        },
        shift: shiftOf(row),
        blocks: blocksOf(row),
        visits: m.visits.map((v) => columnVisit(v, day.date)),
      };
    });
}

/** «10:00–14:00, 15:00–19:00», «Выходной» или «Часы неизвестны». */
export function shiftLabel(shift: Shift): string {
  if (shift.kind === "off") return SS_COPY.dayOff;
  if (shift.kind === "unknown") return SS_COPY.hoursUnknown;
  return shift.intervals.map((r) => `${r.start}–${r.end}`).join(", ");
}

// ---------------------------------------------------------------------------
// Фильтры
// ---------------------------------------------------------------------------

export interface ScheduleFilters {
  /** Пусто — все мастера. */
  masterIds: string[];
  /** Пусто — все услуги. */
  serviceIds: string[];
}

export const NO_FILTERS: ScheduleFilters = { masterIds: [], serviceIds: [] };

/**
 * Фильтры, которые действуют на ЭТОТ день: только мастера, которые в нём
 * есть, и только услуги из загруженного списка. Сохранённый id, которого
 * больше нет, молча скрыл бы все записи под чипом «2 услуги» — такой фильтр
 * не считается выбранным. Список услуг не загрузился — фильтра услуг нет:
 * показать, что именно выбрано, нечем.
 */
export function effectiveFilters(
  filters: ScheduleFilters,
  columns: readonly MasterColumn[],
  services: readonly CatalogServiceLite[],
): ScheduleFilters {
  return {
    masterIds: filters.masterIds.filter((id) => columns.some((c) => c.master.id === id)),
    serviceIds: filters.serviceIds.filter((id) => services.some((svc) => svc.id === id)),
  };
}

/** Мастера, которых показывает экран. */
export function shownColumns(columns: readonly MasterColumn[], filters: ScheduleFilters): MasterColumn[] {
  if (filters.masterIds.length === 0) return [...columns];
  const picked = columns.filter((c) => filters.masterIds.includes(c.master.id));
  return picked.length > 0 ? picked : [...columns];
}

export function matchesService(serviceId: string, serviceIds: readonly string[]): boolean {
  return serviceIds.length === 0 || serviceIds.includes(serviceId);
}

/** Фильтр услуг сужает список записей (лента). Мастера и окна от него не зависят. */
export function visitsByService<T extends { serviceId: string }>(visits: readonly T[], serviceIds: readonly string[]): T[] {
  return visits.filter((v) => matchesService(v.serviceId, serviceIds));
}

export function mastersChip(columns: readonly MasterColumn[], filters: ScheduleFilters): string {
  const picked = columns.filter((c) => filters.masterIds.includes(c.master.id));
  if (picked.length === 0) return SS_COPY.allMasters;
  if (picked.length === 1) return picked[0]?.master.name ?? SS_COPY.allMasters;
  return SS_COPY.mastersCount(picked.length);
}

export function servicesChip(services: readonly CatalogServiceLite[], filters: ScheduleFilters): string {
  const picked = services.filter((svc) => filters.serviceIds.includes(svc.id));
  if (picked.length === 0) return SS_COPY.allServices;
  if (picked.length === 1) return picked[0]?.name ?? SS_COPY.allServices;
  return SS_COPY.servicesCount(picked.length);
}

function filtersKey(tenantId: string): string {
  return `max:salon-schedule:filters:${tenantId}`;
}

function isIdList(v: unknown): v is string[] {
  return Array.isArray(v) && v.every((x) => typeof x === "string");
}

/**
 * Фильтры переживают уход в карточку записи и обратно — на время сеанса и в
 * пределах салона. Вход без даты в адресе («Расписание» с панели, ссылка
 * бота) — свежий: экран их не читает и стирает, см. `SalonPilotScheduleScreen`.
 */
export function loadFilters(tenantId: string): ScheduleFilters {
  try {
    const raw = window.sessionStorage.getItem(filtersKey(tenantId));
    if (!raw) return NO_FILTERS;
    const parsed = JSON.parse(raw) as { masterIds?: unknown; serviceIds?: unknown };
    if (!isIdList(parsed.masterIds) || !isIdList(parsed.serviceIds)) return NO_FILTERS;
    return { masterIds: parsed.masterIds, serviceIds: parsed.serviceIds };
  } catch {
    return NO_FILTERS;
  }
}

export function saveFilters(tenantId: string, filters: ScheduleFilters): void {
  try {
    if (filters.masterIds.length === 0 && filters.serviceIds.length === 0) {
      window.sessionStorage.removeItem(filtersKey(tenantId));
    } else {
      window.sessionStorage.setItem(filtersKey(tenantId), JSON.stringify(filters));
    }
  } catch {
    /* хранилище недоступно — фильтры живут до ухода с экрана */
  }
}

// ---------------------------------------------------------------------------
// Раскладка
// ---------------------------------------------------------------------------

export type Layout = "none" | "single" | "grid" | "feed";

export function layoutFor(masters: number): Layout {
  if (masters <= 0) return "none";
  if (masters === 1) return "single";
  return masters <= GRID_MAX_MASTERS ? "grid" : "feed";
}

// ---------------------------------------------------------------------------
// Сетка: ось и место блока
// ---------------------------------------------------------------------------

export interface Axis {
  /** Минуты от полуночи, по целым часам. */
  start: number;
  end: number;
}

const DEFAULT_AXIS: Axis = { start: 9 * 60, end: 19 * 60 };

/**
 * Ось времени сетки: от самого раннего до самого позднего из показанного —
 * смен, записей, блоков, — по целым часам. Ничего не известно — 09–19.
 */
export function axisFor(columns: readonly MasterColumn[]): Axis {
  let start = Number.POSITIVE_INFINITY;
  let end = Number.NEGATIVE_INFINITY;
  const take = (from: string | null, to: string | null) => {
    if (!from) return;
    const a = toMinutes(from);
    const b = to ? toMinutes(to) : a + 30;
    start = Math.min(start, a);
    end = Math.max(end, b);
  };
  for (const col of columns) {
    if (col.shift.kind === "working") col.shift.intervals.forEach((r) => take(r.start, r.end));
    col.blocks.forEach((b) => take(b.start, b.end));
    col.visits.forEach((v) => take(v.start, v.end));
  }
  if (!Number.isFinite(start) || !Number.isFinite(end)) return DEFAULT_AXIS;
  const from = Math.max(0, Math.floor(start / 60) * 60);
  const to = Math.min(24 * 60, Math.ceil(end / 60) * 60);
  return to > from ? { start: from, end: to } : DEFAULT_AXIS;
}

export function axisHours(axis: Axis): string[] {
  const out: string[] = [];
  for (let m = axis.start; m <= axis.end; m += 60) out.push(fromMinutes(m));
  return out;
}

export interface Span {
  /** Доли высоты оси, 0…1. */
  top: number;
  height: number;
}

/** Место интервала на оси; обрезается её краями. `null` — целиком вне оси. */
export function spanOf(axis: Axis, from: string, to: string): Span | null {
  const total = axis.end - axis.start;
  const a = Math.max(axis.start, toMinutes(from));
  const b = Math.min(axis.end, toMinutes(to));
  if (total <= 0 || b <= a) return null;
  return { top: (a - axis.start) / total, height: (b - a) / total };
}

export interface PlacedVisit {
  visit: ColumnVisit;
  span: Span;
  /** Дорожка внутри колонки и число дорожек в её группе наложений. */
  lane: number;
  lanes: number;
}

/**
 * Записи колонки на оси. Пересекающиеся делят ширину колонки дорожками —
 * двойная запись видна двумя блоками рядом, а не одним поверх другого.
 * Записи без времени сюда не попадают — см. {@link untimedVisits}.
 */
export function placeVisits(axis: Axis, visits: readonly ColumnVisit[]): PlacedVisit[] {
  const timed = visits
    .filter((v): v is ColumnVisit & { start: string } => v.start !== null)
    .map((v) => {
      const from = toMinutes(v.start);
      const to = v.end ? Math.max(toMinutes(v.end), from + 15) : from + 30;
      return { visit: v, from, to };
    })
    .sort((a, b) => a.from - b.from || a.to - b.to);

  const out: PlacedVisit[] = [];
  let group: Array<{ visit: ColumnVisit; from: number; to: number; lane: number }> = [];
  let groupEnd = -1;
  const flush = () => {
    const lanes = group.reduce((n, g) => Math.max(n, g.lane + 1), 0);
    for (const g of group) {
      const span = spanOf(axis, fromMinutes(g.from), fromMinutes(g.to));
      if (span) out.push({ visit: g.visit, span, lane: g.lane, lanes });
    }
    group = [];
    groupEnd = -1;
  };
  for (const item of timed) {
    if (group.length > 0 && item.from >= groupEnd) flush();
    const taken = new Set(group.filter((g) => g.to > item.from).map((g) => g.lane));
    let lane = 0;
    while (taken.has(lane)) lane += 1;
    group.push({ ...item, lane });
    groupEnd = Math.max(groupEnd, item.to);
  }
  if (group.length > 0) flush();
  return out;
}

export function untimedVisits(visits: readonly ColumnVisit[]): ColumnVisit[] {
  return visits.filter((v) => v.start === null);
}

/** Нерабочее время колонки на оси — то, что экран приглушает. */
export function offHours(axis: Axis, shift: Shift): Span[] {
  if (shift.kind !== "working") return [];
  const out: Span[] = [];
  let cursor = axis.start;
  const sorted = [...shift.intervals].sort((a, b) => toMinutes(a.start) - toMinutes(b.start));
  for (const r of sorted) {
    const from = toMinutes(r.start);
    if (from > cursor) {
      const span = spanOf(axis, fromMinutes(cursor), fromMinutes(from));
      if (span) out.push(span);
    }
    cursor = Math.max(cursor, toMinutes(r.end));
  }
  if (cursor < axis.end) {
    const span = spanOf(axis, fromMinutes(cursor), fromMinutes(axis.end));
    if (span) out.push(span);
  }
  return out;
}

/** Минута дня по доле высоты оси — куда человек нажал в колонке. */
export function minuteAt(axis: Axis, fraction: number): number {
  const clamped = Math.min(1, Math.max(0, fraction));
  return Math.round(axis.start + clamped * (axis.end - axis.start));
}

// ---------------------------------------------------------------------------
// Свободные окна — только с сервера
// ---------------------------------------------------------------------------

export interface FreeWindow {
  start: string;
  end: string;
}

export function freeWindowsOf(day: MasterDay | null): FreeWindow[] {
  return (day?.free_windows ?? []).map((w) => ({ start: w.start, end: w.end }));
}

/** Сейчас по часам САЛОНА: дата и время суток. Пояс не пришёл — часы устройства. */
export function salonNow(tz: string | null | undefined, now: Date = new Date()): { ymd: string; hm: string } {
  const ymd = salonToday(tz, now);
  if (tz) {
    try {
      const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: tz,
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).formatToParts(now);
      const hour = parts.find((p) => p.type === "hour")?.value;
      const minute = parts.find((p) => p.type === "minute")?.value;
      if (hour && minute) return { ymd, hm: `${hour}:${minute}` };
    } catch {
      /* неизвестный пояс — часы устройства */
    }
  }
  const hh = `${now.getHours()}`.padStart(2, "0");
  const mm = `${now.getMinutes()}`.padStart(2, "0");
  return { ymd, hm: `${hh}:${mm}` };
}

export interface ClosureNotice {
  text: string;
  reason: string;
}

/**
 * Закрытия салона на дату — словами. Список не пришёл или не разобран —
 * `[]`: про неразобранный список экран говорит отдельно (`unreadable_lists`).
 */
export function closureNotices(frame: SalonDayFrame | null): ClosureNotice[] {
  const closures = frame?.closures;
  if (!closures || closures.state !== "parsed") return [];
  return closures.rows.map((row) => ({
    text:
      row.is_full_day || (!row.start && !row.end)
        ? SS_COPY.closedFull
        : SS_COPY.closedPart(row.start ?? "00:00", row.end ?? END_OF_DAY),
    reason: row.reason.trim(),
  }));
}

/**
 * Окна, которые экран вправе показать: строки сервера минус те, про которые
 * известно, что они не продаются. Прошедшая дата — окон нет; сегодня — нет
 * окон, кончившихся к «сейчас» салона; салон в этот день закрыт (хоть на
 * часть дня) — окон нет: `build_schedule` закрытий не знает, а каталог при
 * записи их учитывает. Ни одно окно здесь не создаётся и не двигается.
 */
export function liveWindows(
  windows: readonly FreeWindow[],
  date: string,
  now: { ymd: string; hm: string },
  salonClosed: boolean,
): FreeWindow[] {
  if (salonClosed || date < now.ymd) return [];
  if (date > now.ymd) return [...windows];
  return windows.filter((w) => toMinutes(w.end) > toMinutes(now.hm));
}

/** Окно сервера, в которое попала минута. Сам экран окон не выводит. */
export function windowAt(windows: readonly FreeWindow[], minute: number): FreeWindow | null {
  return windows.find((w) => toMinutes(w.start) <= minute && minute < toMinutes(w.end)) ?? null;
}

export function windowsFresh(fetchedAtMs: number, nowMs: number = Date.now()): boolean {
  return nowMs - fetchedAtMs <= FREE_WINDOWS_FRESH_MS;
}

// ---------------------------------------------------------------------------
// Лента по времени (DRF-1237, «Режим Все»)
// ---------------------------------------------------------------------------

export type FeedEntry =
  | { kind: "visit"; master: RosterMaster | null; visit: ColumnVisit }
  | { kind: "block"; master: RosterMaster; block: ColumnBlock };

export interface FeedGroup {
  /** «10:00»; `null` — записи без времени, идут последними. */
  time: string | null;
  entries: FeedEntry[];
}

/**
 * Записи всех показанных мастеров под временем начала — перегруппировка
 * показанного, а не расчёт. Отсутствие мастера стоит событием под своим
 * началом (DRF-1237: «значимая недоступность… как отдельное schedule-event»);
 * перерывы в ленту не идут — это шум, а не событие дня салона.
 */
export function feedGroups(
  columns: readonly MasterColumn[],
  orphans: readonly ColumnVisit[],
  serviceIds: readonly string[],
): FeedGroup[] {
  const buckets = new Map<string, FeedEntry[]>();
  const untimed: FeedEntry[] = [];
  const put = (time: string | null, entry: FeedEntry) => {
    if (time === null) {
      untimed.push(entry);
      return;
    }
    const list = buckets.get(time) ?? [];
    list.push(entry);
    buckets.set(time, list);
  };
  for (const col of columns) {
    for (const visit of visitsByService(col.visits, serviceIds)) {
      put(visit.start, { kind: "visit", master: col.master, visit });
    }
    for (const block of col.blocks) {
      if (block.kind === "block") put(block.start, { kind: "block", master: col.master, block });
    }
  }
  for (const visit of visitsByService(orphans, serviceIds)) put(visit.start, { kind: "visit", master: null, visit });
  const groups: FeedGroup[] = [...buckets.entries()]
    .sort(([a], [b]) => toMinutes(a) - toMinutes(b))
    .map(([time, entries]) => ({ time, entries }));
  if (untimed.length > 0) groups.push({ time: null, entries: untimed });
  return groups;
}

export function feedHasVisits(groups: readonly FeedGroup[]): boolean {
  return groups.some((g) => g.entries.some((e) => e.kind === "visit"));
}

// ---------------------------------------------------------------------------
// День одного мастера списком (кадр 12)
// ---------------------------------------------------------------------------

export type MasterDayRow =
  | { kind: "booking"; visit: ColumnVisit; dimmed: boolean }
  | { kind: "free"; start: string; end: string }
  | { kind: "block"; id: string; start: string; end: string; label: string };

function clipToDay(iso: string, day: string, edge: "start" | "end"): string | null {
  const at = parseSalonWallClock(iso);
  if (!at) return hm(iso);
  if (at.ymd < day) return "00:00";
  if (at.ymd > day) return END_OF_DAY;
  // Конец суток сервер пишет полуночью следующего дня или 23:59 — оба «до конца дня».
  if (edge === "end" && at.hm === "23:59") return END_OF_DAY;
  return at.hm;
}

/**
 * Строки дня мастера по времени.
 *
 * Записи — из дня салона (`visits`), как в сетке и ленте: все статусы, с
 * услугой. Окна — уже отсечённые {@link liveWindows}. Блоки — из
 * `day-schedule`: по ним же сервер считал окна. `day` не пришёл — остаются
 * записи; про окна и блоки экран тогда говорит словами, а не пустотой.
 *
 * Причина блока не печатается: отпуск и больничный — сведения о человеке,
 * экрану расписания довольно того, что время закрыто. Перерыв назван — он
 * часть графика, а не обстоятельство.
 *
 * Фильтр услуг записи здесь не убирает, а приглушает: занятое время,
 * исчезнувшее из дня мастера, выглядело бы свободным.
 */
export function masterDayRows(
  visits: readonly ColumnVisit[],
  day: MasterDay | null,
  windows: readonly FreeWindow[],
  serviceIds: readonly string[] = [],
): MasterDayRow[] {
  const rows: MasterDayRow[] = visits
    .filter((v) => v.start !== null)
    .map((visit) => ({ kind: "booking", visit, dimmed: !matchesService(visit.serviceId, serviceIds) }));
  for (const w of windows) rows.push({ kind: "free", start: w.start, end: w.end });
  for (const bl of day?.blocks ?? []) {
    const start = clipToDay(bl.start, day?.date ?? "", "start");
    const end = clipToDay(bl.end, day?.date ?? "", "end");
    if (!start || !end) continue;
    rows.push({
      kind: "block",
      id: bl.exception_id,
      start,
      end,
      label: bl.reason === "lunch" ? SS_COPY.breakLabel : SS_COPY.block,
    });
  }
  const startOf = (row: MasterDayRow) => toMinutes(row.kind === "booking" ? (row.visit.start ?? "00:00") : row.start);
  const order = { block: 0, booking: 1, free: 2 } as const;
  return rows.sort((x, y) => startOf(x) - startOf(y) || order[x.kind] - order[y.kind]);
}

/** День без смены: «выходной» говорит сервер (`working_hours` нет), экран его не выводит. */
export function masterDayOff(day: MasterDay): boolean {
  return day.is_off_day || !day.working_hours;
}

/**
 * Конфликт дня — словами по его виду. Сервер пишет `description`
 * по-английски и с номерами записей; человеку это не показывается.
 */
export function conflictLabel(conflict: MasterDayConflict): string {
  const known = SS_COPY.conflict as Record<string, string>;
  return known[conflict.type] ?? SS_COPY.conflict.other;
}

export interface ConflictItem {
  bookingId: string;
  label: string;
  /** «10:30 · Мария И.» — если запись есть в дне салона. */
  detail: string | null;
}

/** Конфликты дня без повторов; строка ведёт в запись (заморозка, MUST FIX 8). */
export function conflictItems(day: MasterDay | null, visits: readonly ColumnVisit[]): ConflictItem[] {
  const seen = new Set<string>();
  const out: ConflictItem[] = [];
  for (const conflict of day?.conflicts ?? []) {
    const key = `${conflict.type}:${conflict.booking_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const visit = visits.find((v) => v.id === conflict.booking_id);
    out.push({
      bookingId: conflict.booking_id,
      label: conflictLabel(conflict),
      detail: visit ? [visit.start, visit.client].filter(Boolean).join(" · ") : null,
    });
  }
  return out;
}
