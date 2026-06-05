/**
 * Russian-locale date / time / relative formatters for the master dashboard
 * (master-mobile §M1).
 *
 * No external date library — hand-rolled to keep bundle small. All
 * outputs are verbatim per spec («12 мин», «Среда, 21 мая», «14:42»).
 */

const WEEKDAYS_FULL = [
  "Воскресенье",
  "Понедельник",
  "Вторник",
  "Среда",
  "Четверг",
  "Пятница",
  "Суббота",
];
const MONTHS_GEN = [
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

/** Two-digit pad. */
function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/**
 * «14:42» — local time of an ISO timestamp.
 *
 * Defensive: returns the raw string on parse failure.
 */
export function formatTimeHM(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/**
 * «Среда, 21 мая» — full weekday + day-of-month + month-name-genitive.
 *
 * Uses local time so the date matches what the master sees on their
 * own clock. Defensive on parse failure.
 */
export function formatDateLong(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const weekday = WEEKDAYS_FULL[d.getDay()] ?? "";
  const month = MONTHS_GEN[d.getMonth()] ?? "";
  return `${weekday}, ${d.getDate()} ${month}`;
}

/**
 * «12 мин» / «1 ч» / «3 ч» / «вчера» / «21 мая» — relative time for
 * inbox cards. Spec §M1: «🟡 Ксения Л. · 12 мин».
 *
 * Rules:
 *   <1 min       → «только что»
 *   <60 min      → «N мин»
 *   <24 h        → «N ч»
 *   yesterday    → «вчера»
 *   <7 days      → «N дн»
 *   older        → «21 мая» (no year)
 */
export function formatRelativePast(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffMs = now.getTime() - d.getTime();
  if (diffMs < 0) {
    // Future — degrade gracefully; shouldn't happen in inbox preview but
    // an out-of-sync clock could trip this.
    return formatTimeHM(iso);
  }
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "только что";
  if (diffMin < 60) return `${diffMin} мин`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH} ч`;
  // Yesterday detection: walk the local calendar.
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (
    d.getFullYear() === yesterday.getFullYear() &&
    d.getMonth() === yesterday.getMonth() &&
    d.getDate() === yesterday.getDate()
  ) {
    return "вчера";
  }
  const diffDays = Math.floor(diffH / 24);
  if (diffDays < 7) return `${diffDays} дн`;
  const month = MONTHS_GEN[d.getMonth()] ?? "";
  return `${d.getDate()} ${month}`;
}

/**
 * «Анна П.» — client name + last-initial helper.
 *
 * Returns the joined form expected by spec card layouts. Empty
 * last-initial → first name only (e.g. server only resolved one part).
 */
export function joinClientName(
  firstName: string,
  lastInitial: string,
): string {
  const first = (firstName || "").trim();
  const last = (lastInitial || "").trim();
  if (!first && !last) return "";
  if (!last) return first;
  if (!first) return last;
  return `${first} ${last}`;
}

// --- Schedule helpers (M3) ------------------------------------------------

const WEEKDAYS_SHORT_RU = [
  "Вс",
  "Пн",
  "Вт",
  "Ср",
  "Чт",
  "Пт",
  "Сб",
];

const WEEKDAY_NAMES_FULL_UPPER = [
  "ВОСКРЕСЕНЬЕ",
  "ПОНЕДЕЛЬНИК",
  "ВТОРНИК",
  "СРЕДА",
  "ЧЕТВЕРГ",
  "ПЯТНИЦА",
  "СУББОТА",
];

/** «Пн» / «Вт» / … for week-view column headers. */
export function weekdayShortRu(date: Date): string {
  return WEEKDAYS_SHORT_RU[date.getDay()] ?? "";
}

/** «СРЕДА, 21 МАЯ» — week-view section header (uppercase verbatim per spec). */
export function formatWeekHeaderRu(date: Date): string {
  const weekday = WEEKDAY_NAMES_FULL_UPPER[date.getDay()] ?? "";
  const month = (MONTHS_GEN[date.getMonth()] ?? "").toUpperCase();
  return `${weekday}, ${date.getDate()} ${month}`;
}

/**
 * Parse a YYYY-MM-DD string as a local-date (no TZ shift).
 *
 * `new Date("2026-05-21")` would land at UTC midnight which can flip to the
 * previous day in negative-offset locales. We split + construct to keep the
 * calendar date stable regardless of the user's TZ.
 */
export function parseLocalYmd(ymd: string): Date {
  const parts = ymd.split("-").map((s) => Number(s));
  const y = parts[0];
  const m = parts[1];
  const d = parts[2];
  if (
    y === undefined ||
    m === undefined ||
    d === undefined ||
    !Number.isFinite(y) ||
    !Number.isFinite(m) ||
    !Number.isFinite(d)
  ) {
    return new Date(NaN);
  }
  return new Date(y, m - 1, d);
}

/** Format a local Date as YYYY-MM-DD (no TZ shift). */
export function formatYmdLocal(date: Date): string {
  const y = date.getFullYear();
  const m = date.getMonth() + 1;
  const d = date.getDate();
  return `${y}-${pad2(m)}-${pad2(d)}`;
}

/** Add days to a Date (returns a new instance, local-cal preserving). */
export function addDays(date: Date, n: number): Date {
  const out = new Date(date);
  out.setDate(out.getDate() + n);
  return out;
}

/** Start-of-week (Monday) for the given date, in local time. */
export function startOfWeekMonday(date: Date): Date {
  const out = new Date(date);
  const day = out.getDay(); // 0=Sun..6=Sat
  const back = day === 0 ? 6 : day - 1;
  out.setDate(out.getDate() - back);
  out.setHours(0, 0, 0, 0);
  return out;
}

/** «19—25 мая» — week range header for the week view. */
export function formatWeekRangeRu(start: Date, end: Date): string {
  const startDay = start.getDate();
  const endDay = end.getDate();
  const startMonth = MONTHS_GEN[start.getMonth()] ?? "";
  const endMonth = MONTHS_GEN[end.getMonth()] ?? "";
  if (start.getMonth() === end.getMonth()) {
    return `${startDay}—${endDay} ${endMonth}`;
  }
  return `${startDay} ${startMonth} — ${endDay} ${endMonth}`;
}

/** «Май 2026» — month-view header. */
export function formatMonthHeaderRu(date: Date): string {
  const m = MONTHS_GEN[date.getMonth()] ?? "";
  const capitalised = m.charAt(0).toUpperCase() + m.slice(1);
  return `${capitalised} ${date.getFullYear()}`;
}

/** Pull HH:MM from an ISO UTC datetime, converted to local. */
export function localHmFromIso(iso: string): string {
  return formatTimeHM(iso);
}

// --- M6 conversation detail helpers --------------------------------------

/**
 * Day-group key in local time (YYYY-MM-DD). Used to bucket messages into
 * date separators per spec §M6 lines 647-657 («Вчера, 18:42» / «Сегодня,
 * 14:32»). Returns the raw iso on parse failure (degrades to one bucket
 * per malformed value rather than crashing the screen).
 */
export function localDayKey(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return formatYmdLocal(d);
}

/**
 * «Сегодня, 14:32» / «Вчера, 18:42» / «4 мая 2026, 11:05» — date-separator
 * label for M6 message groups. Spec §M6 lines 647-657 shows the relative
 * forms first; older messages fall back to full date.
 *
 * The label embeds the *first* message's time so the user sees an
 * anchor — full per-message time chips live on the bubbles themselves.
 */
export function formatDaySeparator(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const time = formatTimeHM(iso);
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const dDay = new Date(d);
  dDay.setHours(0, 0, 0, 0);
  const diffDays = Math.round(
    (today.getTime() - dDay.getTime()) / (24 * 60 * 60 * 1000),
  );
  if (diffDays === 0) return `Сегодня, ${time}`;
  if (diffDays === 1) return `Вчера, ${time}`;
  const month = MONTHS_GEN[d.getMonth()] ?? "";
  // Older — include year only when not the current year.
  if (d.getFullYear() !== now.getFullYear()) {
    return `${d.getDate()} ${month} ${d.getFullYear()}, ${time}`;
  }
  return `${d.getDate()} ${month}, ${time}`;
}

/** Add minutes to an HH:MM string. Returns HH:MM (24h). */
export function addMinutesHm(hm: string, minutes: number): string {
  const [h, m] = hm.split(":").map((s) => Number(s));
  if (!Number.isFinite(h) || !Number.isFinite(m)) return hm;
  let total = (h as number) * 60 + (m as number) + minutes;
  total = Math.max(0, Math.min(total, 24 * 60 - 1));
  const hh = Math.floor(total / 60);
  const mm = total % 60;
  return `${pad2(hh)}:${pad2(mm)}`;
}
