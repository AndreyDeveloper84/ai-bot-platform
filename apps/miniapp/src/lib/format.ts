/** Russian-locale formatting per assistant-persona.md voice rules. */

const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const MONTHS_GEN = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

export function formatMoney(p: string | number | null): string {
  if (p === null || p === undefined) return "—";
  const n = Math.round(Number(p));
  if (!Number.isFinite(n)) return "—";
  return `${n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ")} ₽`;
}

export function formatDuration(min: number | null): string {
  if (!min) return "";
  if (min < 60) return `${min} мин`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m === 0 ? `${h} ч` : `${h} ч ${m} мин`;
}

export function formatDateLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  const date = new Date(Date.UTC(y, m - 1, d));
  const today = new Date();
  const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  if (iso === todayIso) return "сегодня";
  return `${d} ${MONTHS_GEN[m - 1]}, ${WEEKDAYS[date.getUTCDay()]}`;
}

export function formatDayStrip(iso: string): { name: string; num: string } {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return { name: "", num: iso };
  const date = new Date(Date.UTC(y, m - 1, d));
  return { name: WEEKDAYS[date.getUTCDay()] ?? "", num: String(d) };
}

export function formatSlotTime(isoWithOffset: string): string {
  const match = isoWithOffset.match(/T(\d{2}:\d{2})/);
  return match?.[1] ?? isoWithOffset;
}

export function formatVisitFull(isoWithOffset: string): string {
  const match = isoWithOffset.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  if (!match) return isoWithOffset;
  return `${formatDateLabel(match[1] ?? "")} в ${match[2]}`;
}
