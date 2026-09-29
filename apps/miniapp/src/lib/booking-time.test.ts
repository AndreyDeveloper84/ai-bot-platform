/**
 * Кадр 3 макета DRF-1320 — время: полоса дней и части суток (DRF-2178, Э-2).
 *
 * Этап 2 из 4, радиус ограничен намеренно.
 *
 * **Границы суток не выбираются здесь.** Они уже выбраны контрактом
 * памяти (`apps/persona/memory_extract.py::_hour_to_slot`): `morning`
 * 9–12, `afternoon` 12–17, `evening` 17–21, `late_evening` от 21,
 * `early_morning` до 9. Этим словарём кодируется то, что человек сказал
 * о себе («мне удобно утром»), и разойтись с ним значило бы подсветить
 * ему 07:00 как «обычное время» после слов «удобно утром».
 *
 * Три группы макета — склейка пяти слотов контракта: **Утро** до 12,
 * **День** 12–17, **Вечер** от 17. Свои числа не изобретаются.
 *
 * Второй вычислитель свободного времени не заводится (DRF-1637): дни,
 * счётчики окон и группы считаются ГРУППИРОВКОЙ того, что прислал
 * `/customer/slots`.
 */
import { describe, expect, it } from "vitest";

import {
  DAY_PART_ORDER,
  NO_WINDOWS_LABEL,
  dayPartOf,
  daysWithCounts,
  groupByDayPart,
  windowWord,
  type Slot,
} from "./booking-time";

function slot(start: string): Slot {
  return { date: start.slice(0, 10), start };
}

describe("части суток — из контракта памяти, а не свои", () => {
  it.each([
    ["2026-09-28T07:00:00+03:00", "morning"],
    ["2026-09-28T09:00:00+03:00", "morning"],
    ["2026-09-28T10:30:00+03:00", "morning"],
    ["2026-09-28T11:59:00+03:00", "morning"],
    ["2026-09-28T12:00:00+03:00", "afternoon"],
    ["2026-09-28T15:00:00+03:00", "afternoon"],
    ["2026-09-28T16:59:00+03:00", "afternoon"],
    ["2026-09-28T17:00:00+03:00", "evening"],
    ["2026-09-28T19:30:00+03:00", "evening"],
    ["2026-09-28T22:00:00+03:00", "evening"],
  ])("%s → %s", (start, part) => {
    expect(dayPartOf(start)).toBe(part);
  });

  it("порядок групп — как в макете: Утро, День, Вечер", () => {
    expect(DAY_PART_ORDER.map((p) => p.key)).toEqual(["morning", "afternoon", "evening"]);
    expect(DAY_PART_ORDER.map((p) => p.label)).toEqual(["Утро", "День", "Вечер"]);
  });

  it("пустая группа не рисуется — заголовка без окон не бывает", () => {
    const groups = groupByDayPart([
      slot("2026-09-28T10:00:00+03:00"),
      slot("2026-09-28T10:30:00+03:00"),
    ]);
    expect(groups.map((g) => g.key)).toEqual(["morning"]);
    expect(groups[0]?.slots).toHaveLength(2);
  });
});

describe("полоса дней со счётчиком окон", () => {
  const SLOTS = [
    slot("2026-09-27T10:00:00+03:00"),
    slot("2026-09-27T12:00:00+03:00"),
    slot("2026-09-28T18:00:00+03:00"),
  ];

  it("день без окон остаётся в полосе и говорит «нет мест»", () => {
    const days = daysWithCounts(SLOTS, "2026-09-26", "2026-09-28");
    expect(days.map((d) => d.date)).toEqual(["2026-09-26", "2026-09-27", "2026-09-28"]);
    expect(days[0]?.count).toBe(0);
    expect(days[0]?.label).toBe(NO_WINDOWS_LABEL);
  });

  it("счётчик — число окон этого дня, словом по-русски", () => {
    const days = daysWithCounts(SLOTS, "2026-09-26", "2026-09-28");
    expect(days[1]?.count).toBe(2);
    expect(days[1]?.label).toBe("2 окна");
    expect(days[2]?.label).toBe("1 окно");
  });

  it.each([
    [1, "1 окно"],
    [2, "2 окна"],
    [4, "4 окна"],
    [5, "5 окон"],
    [11, "11 окон"],
    [21, "21 окно"],
  ])("%i → «%s»", (count, expected) => {
    expect(windowWord(count)).toBe(expected);
  });

  it("день из окна, но уже без окон, говорит «нет мест» — не исчезает", () => {
    // Сегодня после последнего слота: сервер по этому дню не вернёт
    // ничего, и день мог бы молча выпасть из полосы. Тогда человек не
    // увидел бы, что сегодня уже поздно, — он увидел бы, что сегодня
    // не бывает. Часы у нас уже четырежды за сутки оказывались
    // молчаливым параметром; здесь они параметр названный.
    const days = daysWithCounts([slot("2026-09-28T18:00:00+03:00")], "2026-09-27", "2026-09-28");
    expect(days.map((d) => d.date)).toEqual(["2026-09-27", "2026-09-28"]);
    expect(days[0]?.count).toBe(0);
    expect(days[0]?.label).toBe(NO_WINDOWS_LABEL);
    // И положительная половина пары: день с окнами по-прежнему считается.
    expect(days[1]?.count).toBe(1);
  });

  it("день вне запрошенного окна в полосу не попадает", () => {
    const days = daysWithCounts(SLOTS, "2026-09-27", "2026-09-27");
    expect(days.map((d) => d.date)).toEqual(["2026-09-27"]);
  });
});
