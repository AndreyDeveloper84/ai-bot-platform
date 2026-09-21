/**
 * Кадр 3 макета DRF-1320 — полоса дней и части суток (DRF-2178, Э-2).
 *
 * Этап 2 из 4. Здесь только ГРУППИРОВКА того, что прислал
 * `/customer/slots`: второго вычислителя свободного времени не заводится
 * (DRF-1637 — три расходящихся ответа у нас уже были). Чистые функции,
 * экран их только рисует.
 *
 * ## Границы суток выбраны не здесь
 *
 * Они уже выбраны контрактом памяти
 * (`apps/persona/memory_extract.py::_hour_to_slot`): `early_morning`
 * до 9, `morning` 9–12, `afternoon` 12–17, `evening` 17–21,
 * `late_evening` от 21. Этим словарём кодируется то, что человек
 * СКАЗАЛ о себе («мне удобно утром», «после 18:00»).
 *
 * Три группы макета — склейка пяти слотов контракта: **Утро** до 12,
 * **День** 12–17, **Вечер** от 17. Свои числа здесь не изобретаются, и
 * это не педантизм: разойдись экран с контрактом — человек, сказавший
 * «удобно утром», увидел бы 07:00 помеченным как его обычное время.
 *
 * ## Час берётся из строки, а не из часов браузера
 *
 * Слот приходит с собственным смещением (`…T18:00:00+03:00`) — это
 * время салона. `new Date(...).getHours()` вернул бы час ТОГО, кто
 * смотрит: у человека в другом поясе вечер стал бы днём. Читаем час из
 * строки, как это делает `formatSlotTime`, — тем же приёмом и по той
 * же причине.
 */

export interface Slot {
  readonly date: string;
  readonly start: string;
}

export type DayPartKey = "morning" | "afternoon" | "evening";

export interface DayPart {
  readonly key: DayPartKey;
  readonly label: string;
}

/** Порядок и подписи — дословно с макета. */
export const DAY_PART_ORDER: readonly DayPart[] = [
  { key: "morning", label: "Утро" },
  { key: "afternoon", label: "День" },
  { key: "evening", label: "Вечер" },
];

/** День без свободного времени — по макету. */
export const NO_WINDOWS_LABEL = "нет мест";

/**
 * Пометка «это время тебе обычно подходит» — дословно с макета.
 *
 * Показывается ТОЛЬКО по серверному признаку `is_suggested` (ПРАВКА 2
 * макета: «пометка появляется только если есть сохранённое предпочтение
 * пользователя по времени, иначе не показываем»).
 *
 * Названный предел: сервер этот признак сегодня НЕ ШЛЁТ — пустота
 * найдена и заперта тестом ещё при DRF-1319. Значит пометка не
 * показывается ни разу, и это честно: обещать «твоё обычное время» без
 * предпочтения значило бы имитировать персонализацию. Источник
 * предпочтения при этом существует — `preferred_time_slots` в памяти;
 * связка «память → слоты» вынесена владельцу отдельным вопросом и здесь
 * НЕ строится.
 */
export const SUGGESTED_NOTE = "Подходит под твоё обычное время (на основе твоего предпочтения)";
/** Значок у самого времени — текст рядом обязателен (WCAG: не цветом). */
export const SUGGESTED_MARK = "★";

/** Граница «День» по контракту памяти (`afternoon` с 12). */
const AFTERNOON_FROM = 12;
/** Граница «Вечер» по контракту памяти (`evening` с 17). */
const EVENING_FROM = 17;

function hourOf(startIso: string): number {
  const match = startIso.match(/T(\d{2}):/);
  return match ? Number(match[1]) : 0;
}

/**
 * Часть суток слота.
 *
 * `early_morning` контракта (до 9) живёт в «Утре», а `late_evening`
 * (от 21) — в «Вечере»: макет знает три группы, контракт пять, и
 * склейка идёт по границам контракта, а не по новым.
 */
export function dayPartOf(startIso: string): DayPartKey {
  const hour = hourOf(startIso);
  if (hour < AFTERNOON_FROM) return "morning";
  if (hour < EVENING_FROM) return "afternoon";
  return "evening";
}

export interface DayPartGroup extends DayPart {
  readonly slots: readonly Slot[];
}

/**
 * Слоты по частям суток, в порядке макета.
 *
 * Пустая группа не возвращается вовсе: заголовок без окон под ним
 * обещал бы время, которого нет.
 */
export function groupByDayPart(slots: readonly Slot[]): DayPartGroup[] {
  return DAY_PART_ORDER.map((part) => ({
    ...part,
    slots: slots.filter((slot) => dayPartOf(slot.start) === part.key),
  })).filter((group) => group.slots.length > 0);
}

export interface DayWithCount {
  readonly date: string;
  readonly count: number;
  /** «3 окна» или «нет мест» — то, что стоит под датой в полосе. */
  readonly label: string;
}

/**
 * Полоса дней запрошенного окна со счётчиком свободного времени.
 *
 * Дни берутся из ЗАПРОШЕННОГО диапазона, а не из ответа: сервер
 * возвращает только свободное, и день без окон в ответе просто
 * отсутствует. Выброси мы его из полосы — человек не увидел бы, что
 * сегодня уже поздно; он увидел бы, что сегодня не бывает.
 */
export function daysWithCounts(
  slots: readonly Slot[],
  dateFrom: string,
  dateTo: string,
): DayWithCount[] {
  const counts = new Map<string, number>();
  for (const slot of slots) {
    counts.set(slot.date, (counts.get(slot.date) ?? 0) + 1);
  }
  const days: DayWithCount[] = [];
  for (const date of datesBetween(dateFrom, dateTo)) {
    const count = counts.get(date) ?? 0;
    days.push({ date, count, label: count === 0 ? NO_WINDOWS_LABEL : windowWord(count) });
  }
  return days;
}

function datesBetween(from: string, to: string): string[] {
  const out: string[] = [];
  const start = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return out;
  for (let d = start; d <= end; d = new Date(d.getTime() + 86_400_000)) {
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

/** «1 окно» / «2 окна» / «5 окон» — число словом, а не «5 окно». */
export function windowWord(count: number): string {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return `${count} окон`;
  switch (count % 10) {
    case 1:
      return `${count} окно`;
    case 2:
    case 3:
    case 4:
      return `${count} окна`;
    default:
      return `${count} окон`;
  }
}
