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
