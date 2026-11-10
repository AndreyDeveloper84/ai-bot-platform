/**
 * DRF-2933 — расчёты «Расписания» салона v2.
 *
 * Держится то, на чём стоит экран: раскладка выбирается числом мастеров
 * (заморозка DRF-2854, MUST FIX 1 и 6); смена прикладывается к мастеру
 * только по `master_id`, а не по имени; свободное окно — только строка
 * сервера и только пока свежая; время берётся из строки салона, не из
 * пояса устройства.
 */
import { afterEach, describe, expect, it } from "vitest";

import type {
  MasterDay,
  MasterListItem,
  SalonDayFrame,
  SalonDayResponse,
  SalonDayVisit,
  SalonFrameMaster,
} from "./admin-api";
import {
  FREE_WINDOWS_FRESH_MS,
  NO_FILTERS,
  SS_COPY,
  addMonths,
  axisFor,
  axisHours,
  buildColumns,
  cardTop,
  clientLabel,
  closureNotices,
  conflictItems,
  dateLine,
  dateParam,
  effectiveFilters,
  feedGroups,
  feedHasVisits,
  layoutFor,
  liveWindows,
  loadFilters,
  masterDayOff,
  masterDayRows,
  mastersChip,
  minuteAt,
  monthGrid,
  monthStart,
  monthTitle,
  newBookingPath,
  offHours,
  placeVisits,
  salonClosure,
  salonNow,
  saveFilters,
  scheduleMasterPath,
  schedulePath,
  servicesChip,
  shiftLabel,
  shownColumns,
  spanOf,
  untimedVisits,
  visitsByService,
  windowAt,
  windowsFresh,
  type ColumnVisit,
} from "./salon-schedule";

/** Четверг. */
const DAY = "2031-01-09";

function visit(id: string, from: string | null, to: string | null, patch: Partial<SalonDayVisit> = {}): SalonDayVisit {
  return {
    id,
    service_id: "svc-1",
    start_at: from ? `${DAY}T${from}:00+03:00` : null,
    end_at: to ? `${DAY}T${to}:00+03:00` : null,
    duration_min: 60,
    status: "confirmed",
    service_name: "Маникюр",
    client_first_name: "Клиент",
    client_last_initial: "К",
    is_in_progress: false,
    ...patch,
  };
}

function salonDay(masters: Array<{ id: string; name?: string; active?: boolean; visits?: SalonDayVisit[] }>): SalonDayResponse {
  return {
    date: DAY,
    timezone: "Europe/Moscow",
    summary: { total: 0, upcoming: 0, completed: 0, released: 0 },
    masters: masters.map((m) => ({
      master_id: m.id,
      name: m.name ?? `Мастер ${m.id}`,
      is_active: m.active ?? true,
      visits: m.visits ?? [],
    })),
    orphan_visits: [],
  };
}

const NONE = { state: "none" as const, rows: [], seen_fields: [] };
const rows = (...pairs: Array<[string, string]>) => ({
  state: "parsed" as const,
  rows: pairs.map(([start, end]) => ({ start, end })),
  seen_fields: [],
});

function frameRow(masterId: string | null, patch: Partial<SalonFrameMaster> = {}): SalonFrameMaster {
  return {
    specialist_id: `cat-${masterId ?? "x"}`,
    master_id: masterId,
    display_name: "как в каталоге",
    is_working_day: true,
    schedule_note: null,
    schedule_source: null,
    working_intervals: rows(["10:00", "19:00"]),
    breaks: NONE,
    absences: NONE,
    ...patch,
  };
}

function frame(masters: SalonFrameMaster[]): SalonDayFrame {
  return { date: DAY, source: "ayla", masters, unreadable_lists: [] };
}

function card(id: string, patch: Partial<MasterListItem> = {}): MasterListItem {
  return {
    id,
    name: `Мастер ${id}`,
    specialization: "Парикмахер",
    photo_url: `https://img.example/${id}.jpg`,
    is_active: true,
    invite_status: "accepted",
    last_seen_at: null,
    services_count: 1,
    ...patch,
  };
}

const cv = (id: string, start: string | null, end: string | null, patch: Partial<ColumnVisit> = {}): ColumnVisit => ({
  id,
  start,
  end,
  client: "Клиент К.",
  firstName: "Клиент",
  service: "Маникюр",
  serviceId: "svc-1",
  released: false,
  now: false,
  ...patch,
});

afterEach(() => {
  window.sessionStorage.clear();
});

describe("раскладка — числом мастеров (заморозка DRF-2854, MUST FIX 1 и 6)", () => {
  it("один — день списком; от двух до четырёх — сетка; больше — лента", () => {
    expect([0, 1, 2, 4, 5, 31].map(layoutFor)).toEqual(["none", "single", "grid", "grid", "feed", "feed"]);
  });
});

describe("даты кадра", () => {
  it("строка над заголовком — «Чт, 9 января»", () => {
    expect(dateLine(DAY)).toBe("Чт, 9 января");
  });

  it("дата из адреса — только настоящая", () => {
    expect(dateParam(DAY)).toBe(DAY);
    expect(dateParam("2031-02-30")).toBeNull();
    expect(dateParam("завтра")).toBeNull();
    expect(dateParam(null)).toBeNull();
  });

  it("месяц неделями с понедельника, чужие дни не рисуются", () => {
    const grid = monthGrid(monthStart(DAY));
    expect(grid[0]).toEqual([null, null, "2031-01-01", "2031-01-02", "2031-01-03", "2031-01-04", "2031-01-05"]);
    expect(grid.at(-1)).toEqual(["2031-01-27", "2031-01-28", "2031-01-29", "2031-01-30", "2031-01-31", null, null]);
    expect(grid.flat().filter(Boolean)).toHaveLength(31);
  });

  it("листание месяцев переходит через год", () => {
    expect(addMonths("2031-01-01", -1)).toBe("2030-12-01");
    expect(addMonths("2031-12-01", 1)).toBe("2032-01-01");
    expect(monthTitle("2031-01-01")).toBe("Январь 2031");
  });
});

describe("адреса переходов", () => {
  it("расписание и мастер несут дату", () => {
    expect(schedulePath(DAY)).toBe(`/admin/schedule?date=${DAY}`);
    expect(schedulePath(null)).toBe("/admin/schedule");
    expect(scheduleMasterPath("m 1", DAY)).toBe(`/admin/schedule/master/m%201?date=${DAY}`);
  });

  it("ручная запись получает мастера, дату и окно — и ничего не создаёт сама", () => {
    expect(newBookingPath({ date: DAY, masterId: "m-1", from: "12:00", to: "13:00" })).toBe(
      `/admin/booking/new?return=schedule&date=${DAY}&master_id=m-1&from=12%3A00&to=13%3A00`,
    );
    expect(newBookingPath({ date: DAY })).toBe(`/admin/booking/new?return=schedule&date=${DAY}`);
    // Половина окна — не окно.
    expect(newBookingPath({ date: DAY, from: "12:00" })).toBe(`/admin/booking/new?return=schedule&date=${DAY}`);
  });
});

describe("колонки дня: записи из зеркала, смены из кадра", () => {
  it("смена и блоки прикладываются по master_id; фото и специализация — из списка мастеров", () => {
    const day = salonDay([{ id: "m-1", name: "Мастер Один", visits: [visit("v-1", "10:30", "11:30")] }]);
    const cols = buildColumns(
      day,
      frame([frameRow("m-1", { absences: rows(["14:00", "16:00"]), breaks: rows(["13:00", "13:30"]) })]),
      [card("m-1")],
    );

    expect(cols).toHaveLength(1);
    expect(cols[0]?.master).toEqual({
      id: "m-1",
      name: "Мастер Один",
      specialization: "Парикмахер",
      photoUrl: "https://img.example/m-1.jpg",
      isActive: true,
    });
    expect(cols[0]?.shift).toEqual({ kind: "working", intervals: [{ start: "10:00", end: "19:00" }] });
    expect(cols[0]?.blocks).toEqual([
      { kind: "break", start: "13:00", end: "13:30" },
      { kind: "block", start: "14:00", end: "16:00" },
    ]);
    expect(cols[0]?.visits[0]).toMatchObject({ id: "v-1", start: "10:30", end: "11:30", client: "Клиент К." });
  });

  it("строка кадра без пары ничья: по имени к мастеру не прикладывается, смена — «неизвестно»", () => {
    const day = salonDay([{ id: "m-1", name: "как в каталоге" }]);
    const cols = buildColumns(day, frame([frameRow(null)]), []);

    expect(cols[0]?.shift).toEqual({ kind: "unknown" });
    expect(shiftLabel(cols[0]!.shift)).toBe("Часы неизвестны");
  });

  it("кадра нет — смена неизвестна, а не «выходной» и не «свободен»", () => {
    const cols = buildColumns(salonDay([{ id: "m-1" }]), null, []);
    expect(cols[0]?.shift).toEqual({ kind: "unknown" });
    expect(cols[0]?.blocks).toEqual([]);
  });

  it("часы, которых сервер не разобрал, не выдаются за смену", () => {
    const unreadable = { state: "unreadable" as const, rows: [], seen_fields: ["from"] };
    const cols = buildColumns(salonDay([{ id: "m-1" }]), frame([frameRow("m-1", { working_intervals: unreadable })]), []);
    expect(cols[0]?.shift).toEqual({ kind: "unknown" });
  });

  it("нерабочий день — «Выходной», блоки не рисуются", () => {
    const cols = buildColumns(
      salonDay([{ id: "m-1" }]),
      frame([frameRow("m-1", { is_working_day: false, absences: rows(["10:00", "12:00"]) })]),
      [],
    );
    expect(shiftLabel(cols[0]!.shift)).toBe("Выходной");
    expect(cols[0]?.blocks).toEqual([]);
  });

  it("выключенная карточка без записей не показывается, с записями — показывается", () => {
    const day = salonDay([
      { id: "off-empty", active: false },
      { id: "off-busy", active: false, visits: [visit("v-1", "10:00", "11:00")] },
      { id: "on", active: true },
    ]);
    expect(buildColumns(day, null, []).map((c) => c.master.id)).toEqual(["off-busy", "on"]);
  });

  it("время записи — из строки салона, а не из пояса устройства", () => {
    const day = salonDay([
      { id: "m-1", visits: [visit("v-1", null, null, { start_at: `${DAY}T23:30:00+03:00`, end_at: "2031-01-10T00:30:00+03:00" })] },
    ]);
    const [col] = buildColumns(day, null, []);
    expect(col?.visits[0]).toMatchObject({ start: "23:30", end: "24:00" });
  });

  it("конца нет — берётся длительность; нет и её — конец неизвестен", () => {
    const day = salonDay([
      {
        id: "m-1",
        visits: [visit("a", "10:00", null, { duration_min: 90 }), visit("b", "12:00", null, { duration_min: 0 })],
      },
    ]);
    const [col] = buildColumns(day, null, []);
    expect(col?.visits.map((v) => v.end)).toEqual(["11:30", null]);
  });

  it("освобождённый визит помечен, идущий по плану — тоже", () => {
    const day = salonDay([
      {
        id: "m-1",
        visits: [visit("a", "10:00", "11:00", { status: "cancelled" }), visit("b", "12:00", "13:00", { is_in_progress: true })],
      },
    ]);
    const [col] = buildColumns(day, null, []);
    expect(col?.visits.map((v) => [v.released, v.now])).toEqual([
      [true, false],
      [false, true],
    ]);
  });

  it("подпись клиента — имя и инициал, без выдуманной фамилии", () => {
    expect(clientLabel("Мария", "И")).toBe("Мария И.");
    expect(clientLabel("Мария", "И.")).toBe("Мария И.");
    expect(clientLabel("Мария", "")).toBe("Мария");
    expect(clientLabel("", "")).toBe("Клиент");
  });
});

describe("фильтры", () => {
  const cols = buildColumns(salonDay([{ id: "a", name: "Мастер А" }, { id: "b", name: "Мастер Б" }, { id: "c" }]), null, []);

  it("пусто — все мастера; выбранные — только они", () => {
    expect(shownColumns(cols, NO_FILTERS)).toHaveLength(3);
    expect(shownColumns(cols, { masterIds: ["b"], serviceIds: [] }).map((c) => c.master.id)).toEqual(["b"]);
  });

  it("выбранного мастера в дне уже нет — показываются все, а не пустой экран", () => {
    expect(shownColumns(cols, { masterIds: ["архив"], serviceIds: [] })).toHaveLength(3);
    expect(mastersChip(cols, { masterIds: ["архив"], serviceIds: [] })).toBe("Все мастера");
  });

  it("чип мастеров: все, имя одного, число нескольких", () => {
    expect(mastersChip(cols, NO_FILTERS)).toBe("Все мастера");
    expect(mastersChip(cols, { masterIds: ["b"], serviceIds: [] })).toBe("Мастер Б");
    expect(mastersChip(cols, { masterIds: ["a", "b"], serviceIds: [] })).toBe("2 мастера");
    expect(SS_COPY.mastersCount(5)).toBe("5 мастеров");
    expect(SS_COPY.mastersCount(21)).toBe("21 мастер");
  });

  const services = [
    { id: "s1", name: "Маникюр", duration_min: 60 },
    { id: "s2", name: "Стрижка", duration_min: 30 },
  ];

  it("чип услуг: все, название одной, число нескольких", () => {
    expect(servicesChip(services, NO_FILTERS)).toBe("Все услуги");
    expect(servicesChip(services, { masterIds: [], serviceIds: ["s2"] })).toBe("Стрижка");
    expect(servicesChip(services, { masterIds: [], serviceIds: ["s1", "s2"] })).toBe("2 услуги");
  });

  it("сохранённый id, которого больше нет, фильтром не считается — записи не прячутся под «2 услуги»", () => {
    const stale = { masterIds: ["архив", "b"], serviceIds: ["s1", "удалена"] };
    expect(effectiveFilters(stale, cols, services)).toEqual({ masterIds: ["b"], serviceIds: ["s1"] });
  });

  it("список услуг не загрузился — фильтра услуг нет: показать, что выбрано, нечем", () => {
    expect(effectiveFilters({ masterIds: [], serviceIds: ["s1"] }, cols, [])).toEqual(NO_FILTERS);
  });

  it("фильтр услуг сужает список записей", () => {
    const visits = [cv("a", "10:00", "11:00", { serviceId: "s1" }), cv("b", "12:00", "13:00", { serviceId: "s2" })];
    expect(visitsByService(visits, []).map((v) => v.id)).toEqual(["a", "b"]);
    expect(visitsByService(visits, ["s2"]).map((v) => v.id)).toEqual(["b"]);
  });

  it("фильтры живут в пределах сеанса и салона; мусор в хранилище — «без фильтров»", () => {
    saveFilters("t-1", { masterIds: ["a"], serviceIds: ["s1"] });
    expect(loadFilters("t-1")).toEqual({ masterIds: ["a"], serviceIds: ["s1"] });
    expect(loadFilters("t-2")).toEqual(NO_FILTERS);

    saveFilters("t-1", NO_FILTERS);
    expect(window.sessionStorage.length).toBe(0);

    window.sessionStorage.setItem("max:salon-schedule:filters:t-1", JSON.stringify({ masterIds: "a" }));
    expect(loadFilters("t-1")).toEqual(NO_FILTERS);
    window.sessionStorage.setItem("max:salon-schedule:filters:t-1", "{не json");
    expect(loadFilters("t-1")).toEqual(NO_FILTERS);
  });
});

describe("сетка: ось и место блока", () => {
  it("ось — от самого раннего до самого позднего из показанного, по целым часам", () => {
    const cols = buildColumns(
      salonDay([
        { id: "a", visits: [visit("v", "08:30", "09:15")] },
        { id: "b", visits: [visit("w", "19:30", "20:10")] },
      ]),
      frame([frameRow("a"), frameRow("b")]),
      [],
    );
    const axis = axisFor(cols);
    expect(axis).toEqual({ start: 8 * 60, end: 21 * 60 });
    expect(axisHours(axis)[0]).toBe("08:00");
    expect(axisHours(axis).at(-1)).toBe("21:00");
  });

  it("отсутствие ось не двигает: отпуск «00:00–24:00» не растягивает сетку на сутки", () => {
    const cols = buildColumns(
      salonDay([{ id: "a" }, { id: "b" }]),
      frame([frameRow("a", { absences: rows(["00:00", "24:00"]) }), frameRow("b", { absences: rows(["16:00", "24:00"]) })]),
      [],
    );
    expect(axisFor(cols)).toEqual({ start: 10 * 60, end: 19 * 60 });
  });

  it("окна открытой колонки ось расширяют: окно сервера вне оси иначе не нарисовать", () => {
    const cols = buildColumns(salonDay([{ id: "a", visits: [visit("v", "09:00", "10:00")] }]), null, []);
    expect(axisFor(cols)).toEqual({ start: 9 * 60, end: 10 * 60 });
    expect(axisFor(cols, [{ start: "17:00", end: "21:00" }])).toEqual({ start: 9 * 60, end: 21 * 60 });
  });

  it("карточка окна встаёт под палец, но не дальше часа до конца окна", () => {
    const axis = { start: 540, end: 1140 };
    const long = { start: "09:00", end: "18:00" };
    expect(cardTop(axis, long, null)).toBe(0);
    expect(cardTop(axis, long, 14 * 60)).toBe(0.5);
    expect(cardTop(axis, long, 17 * 60 + 50)).toBe((17 * 60 - 540) / 600);
    // Нажали выше окна — карточка в его начале.
    expect(cardTop(axis, { start: "12:00", end: "13:00" }, 10 * 60)).toBe(0.3);
  });

  it("ничего не известно — 09–19, а не пустая ось", () => {
    expect(axisFor(buildColumns(salonDay([{ id: "a" }]), null, []))).toEqual({ start: 540, end: 1140 });
  });

  it("место интервала — доли оси; за краями обрезается, целиком вне — нет места", () => {
    const axis = { start: 600, end: 1200 };
    expect(spanOf(axis, "10:00", "11:00")).toEqual({ top: 0, height: 0.1 });
    expect(spanOf(axis, "09:00", "11:00")).toEqual({ top: 0, height: 0.1 });
    expect(spanOf(axis, "07:00", "09:00")).toBeNull();
  });

  it("пересекающиеся записи делят колонку дорожками; непересекающиеся — нет", () => {
    const axis = { start: 600, end: 1200 };
    const placed = placeVisits(axis, [
      cv("a", "10:00", "11:00"),
      cv("b", "10:30", "11:30"),
      cv("c", "12:00", "13:00"),
    ]);
    expect(placed.map((p) => [p.visit.id, p.lane, p.lanes])).toEqual([
      ["a", 0, 2],
      ["b", 1, 2],
      ["c", 0, 1],
    ]);
  });

  it("короткие соседние записи встают дорожками, а не наезжают друг на друга", () => {
    // 15 минут на оси — 14 px, блок ниже 22 px не читается: он рисуется на
    // 25 минут, и по этой же длине считается наложение.
    const placed = placeVisits({ start: 600, end: 1200 }, [cv("a", "10:00", "10:15"), cv("b", "10:15", "10:30")]);
    expect(placed.map((p) => [p.lane, p.lanes])).toEqual([
      [0, 2],
      [1, 2],
    ]);
  });

  it("запись встык не делит колонку", () => {
    const placed = placeVisits({ start: 600, end: 1200 }, [cv("a", "10:00", "11:00"), cv("b", "11:00", "12:00")]);
    expect(placed.map((p) => p.lanes)).toEqual([1, 1]);
  });

  it("запись без времени на ось не ставится и не теряется", () => {
    const visits = [cv("a", null, null), cv("b", "10:00", "11:00")];
    expect(placeVisits({ start: 600, end: 1200 }, visits).map((p) => p.visit.id)).toEqual(["b"]);
    expect(untimedVisits(visits).map((v) => v.id)).toEqual(["a"]);
  });

  it("нерабочее время колонки — всё, что вне смен; у неизвестной смены его нет", () => {
    const axis = { start: 540, end: 1260 };
    const spans = offHours(axis, {
      kind: "working",
      intervals: [
        { start: "10:00", end: "14:00" },
        { start: "15:00", end: "19:00" },
      ],
    });
    expect(spans).toHaveLength(3);
    expect(spans[0]).toEqual(spanOf(axis, "09:00", "10:00"));
    expect(spans[1]).toEqual(spanOf(axis, "14:00", "15:00"));
    expect(spans[2]).toEqual(spanOf(axis, "19:00", "21:00"));
    expect(offHours(axis, { kind: "unknown" })).toEqual([]);
  });

  it("минута по месту нажатия", () => {
    const axis = { start: 600, end: 1200 };
    expect(minuteAt(axis, 0)).toBe(600);
    expect(minuteAt(axis, 0.25)).toBe(750);
    expect(minuteAt(axis, 7)).toBe(1200);
  });
});

describe("свободное окно — строка сервера, только свежая и только там, где сервер авторитетен", () => {
  const windows = [
    { start: "10:00", end: "11:00" },
    { start: "12:00", end: "13:30" },
  ];

  it("окно под минутой: начало входит, конец — нет; между окнами окна нет", () => {
    expect(windowAt(windows, 12 * 60)).toEqual(windows[1]);
    expect(windowAt(windows, 13 * 60 + 29)).toEqual(windows[1]);
    expect(windowAt(windows, 13 * 60 + 30)).toBeNull();
    expect(windowAt(windows, 11 * 60 + 30)).toBeNull();
  });

  it("свежесть — пять минут от ответа", () => {
    expect(windowsFresh(1_000, 1_000 + FREE_WINDOWS_FRESH_MS)).toBe(true);
    expect(windowsFresh(1_000, 1_001 + FREE_WINDOWS_FRESH_MS)).toBe(false);
  });

  it("прошедшая дата — окон нет: сервер не знает «сейчас» и отдаёт их поверх состоявшихся визитов", () => {
    expect(liveWindows(windows, DAY, { ymd: "2031-01-10", hm: "09:00" }, "open")).toEqual([]);
  });

  it("сегодня — нет окон, кончившихся к «сейчас» салона; идущее окно начинается с «сейчас»", () => {
    expect(liveWindows(windows, DAY, { ymd: DAY, hm: "11:00" }, "open")).toEqual([windows[1]]);
    // 12:40: «свободно с 12:00» — уже неправда; с 12:40 — правда.
    expect(liveWindows(windows, DAY, { ymd: DAY, hm: "12:40" }, "open")).toEqual([{ start: "12:40", end: "13:30" }]);
    expect(liveWindows(windows, DAY, { ymd: DAY, hm: "13:30" }, "open")).toEqual([]);
    // Окно на весь день за десять минут до конца смены — десять минут, а не день.
    expect(liveWindows([{ start: "09:00", end: "18:00" }], DAY, { ymd: DAY, hm: "17:50" }, "open")).toEqual([
      { start: "17:50", end: "18:00" },
    ]);
  });

  it("будущая дата — все окна сервера", () => {
    expect(liveWindows(windows, DAY, { ymd: "2031-01-08", hm: "23:00" }, "open")).toEqual(windows);
  });

  it("салон закрыт или про закрытия ничего не известно — окон нет", () => {
    const future = { ymd: "2031-01-08", hm: "09:00" };
    expect(liveWindows(windows, DAY, future, "closed")).toEqual([]);
    expect(liveWindows(windows, DAY, future, "unknown")).toEqual([]);
  });

  it("закрытия салона: прочитаны и пусты — открыт; не прочитаны — неизвестно, а не «открыт»", () => {
    const closures = (state: "none" | "parsed" | "unreadable" | "absent", n = 0) => ({
      ...frame([]),
      closures: {
        state,
        rows: Array.from({ length: n }, () => ({ start: null, end: null, is_full_day: true, reason: "" })),
        seen_fields: [],
      },
    });
    expect(salonClosure(closures("none"))).toBe("open");
    expect(salonClosure(closures("parsed", 1))).toBe("closed");
    expect(salonClosure(closures("unreadable"))).toBe("unknown");
    expect(salonClosure(closures("absent"))).toBe("unknown");
    // Кадр не прочитан; прежний сервер поля не отдаёт.
    expect(salonClosure(null)).toBe("unknown");
    expect(salonClosure(frame([]))).toBe("unknown");
    // Салон на локальных таблицах: закрытий каталога у него нет вовсе.
    expect(salonClosure(null, true)).toBe("open");
  });

  it("«сейчас» — по часам салона, а не устройства", () => {
    // 21:30 UTC 9 января: во Владивостоке уже 10 января, 07:30.
    const moment = new Date("2031-01-09T21:30:00Z");
    expect(salonNow("Asia/Vladivostok", moment)).toEqual({ ymd: "2031-01-10", hm: "07:30" });
    expect(salonNow("Europe/Moscow", moment)).toEqual({ ymd: "2031-01-10", hm: "00:30" });
  });

  it("закрытия салона — словами; неразобранный и отсутствующий список — не «закрытий нет»", () => {
    const withClosures = (rowsOf: Array<{ start: string | null; end: string | null; is_full_day: boolean; reason: string }>) => ({
      ...frame([]),
      closures: { state: "parsed" as const, rows: rowsOf, seen_fields: [] },
    });
    expect(
      closureNotices(
        withClosures([
          { start: null, end: null, is_full_day: true, reason: "Санитарный день" },
          { start: "10:00", end: "14:00", is_full_day: false, reason: "" },
          { start: "18:00", end: null, is_full_day: false, reason: "" },
        ]),
      ),
    ).toEqual([
      { text: "В этот день салон закрыт", reason: "Санитарный день" },
      { text: "Салон закрыт 10:00–14:00", reason: "" },
      { text: "Салон закрыт 18:00–24:00", reason: "" },
    ]);
    expect(closureNotices(frame([]))).toEqual([]);
    expect(closureNotices(null)).toEqual([]);
    expect(closureNotices({ ...frame([]), closures: { state: "unreadable", rows: [], seen_fields: ["x"] } })).toEqual([]);
  });
});

describe("лента по времени (DRF-1237, «Режим Все»)", () => {
  const day = salonDay([
    { id: "a", name: "Мастер А", visits: [visit("a1", "14:00", "15:00"), visit("a2", "10:00", "11:00")] },
    { id: "b", name: "Мастер Б", visits: [visit("b1", "14:00", "15:00", { service_id: "svc-2" })] },
  ]);
  const cols = buildColumns(
    day,
    frame([frameRow("a", { absences: rows(["16:00", "18:00"]), breaks: rows(["13:00", "13:30"]) }), frameRow("b")]),
    [],
  );

  it("параллельные записи стоят под одним временем, а не в колонках", () => {
    const groups = feedGroups(cols, [], []);
    expect(groups.map((g) => g.time)).toEqual(["10:00", "14:00", "16:00"]);
    const at14 = groups[1]?.entries ?? [];
    expect(at14.map((e) => (e.kind === "visit" ? e.master?.name : null))).toEqual(["Мастер А", "Мастер Б"]);
  });

  it("отсутствие мастера — событие дня, перерыв — нет", () => {
    const groups = feedGroups(cols, [], []);
    expect(groups[2]?.entries).toEqual([
      { kind: "block", master: cols[0]!.master, block: { kind: "block", start: "16:00", end: "18:00" } },
    ]);
    expect(groups.flatMap((g) => g.entries).filter((e) => e.kind === "block")).toHaveLength(1);
  });

  it("фильтр услуг сужает записи; «записей нет» видно по ленте", () => {
    const groups = feedGroups(cols, [], ["svc-2"]);
    expect(groups.flatMap((g) => g.entries).filter((e) => e.kind === "visit")).toHaveLength(1);
    expect(feedHasVisits(groups)).toBe(true);
    expect(feedHasVisits(feedGroups(cols, [], ["нет такой"]))).toBe(false);
  });

  it("ничья запись и запись без времени не теряются", () => {
    const groups = feedGroups(cols, [cv("o1", "09:00", "10:00"), cv("o2", null, null)], []);
    expect(groups[0]).toEqual({ time: "09:00", entries: [{ kind: "visit", master: null, visit: cv("o1", "09:00", "10:00") }] });
    expect(groups.at(-1)?.time).toBeNull();
  });
});

describe("день одного мастера списком (кадр 12)", () => {
  function masterDay(patch: Partial<MasterDay> = {}): MasterDay {
    return {
      date: DAY,
      is_off_day: false,
      working_hours: { start: "09:00", end: "19:00" },
      // Записи `day-schedule` экран не читает: они без статуса и без услуги.
      bookings: [],
      blocks: [
        { exception_id: "x-1", start: `${DAY}T14:00:00+03:00`, end: `${DAY}T15:00:00+03:00`, reason: "sick", approved: true },
        { exception_id: "lunch", start: `${DAY}T13:00:00+03:00`, end: `${DAY}T13:30:00+03:00`, reason: "lunch", approved: true },
      ],
      free_windows: [{ start: "12:00", end: "13:00", duration_min: 60 }],
      conflicts: [],
      ...patch,
    };
  }

  const visits = [
    cv("b-2", "10:30", "11:30", { serviceId: "s2", now: true }),
    cv("b-1", "09:00", "10:00", { serviceId: "s1" }),
    cv("b-3", "16:00", "17:00", { serviceId: "s1", released: true }),
  ];
  const windows = [{ start: "12:00", end: "13:00" }];
  const shape = (rowsOf: ReturnType<typeof masterDayRows>) =>
    rowsOf.map((r) => (r.kind === "booking" ? [r.kind, r.visit.start, r.visit.end] : [r.kind, r.start, r.end]));

  it("строки идут по времени: записи дня салона, окна сервера, блоки", () => {
    expect(shape(masterDayRows(visits, masterDay(), windows))).toEqual([
      ["booking", "09:00", "10:00"],
      ["booking", "10:30", "11:30"],
      ["free", "12:00", "13:00"],
      ["block", "13:00", "13:30"],
      ["block", "14:00", "15:00"],
      ["booking", "16:00", "17:00"],
    ]);
  });

  it("записи — из дня салона: отменённая не пропадает, как пропала бы из day-schedule", () => {
    const booking = masterDayRows(visits, masterDay(), windows).flatMap((r) => (r.kind === "booking" ? [r.visit] : []));
    expect(booking.map((v) => [v.id, v.released])).toContainEqual(["b-3", true]);
  });

  it("фильтр услуг в дне мастера приглушает, а не убирает: занятое время не выглядит свободным", () => {
    const rowsOfDay = masterDayRows(visits, masterDay(), windows, ["s1"]);
    const dimmed = rowsOfDay.flatMap((r) => (r.kind === "booking" ? [[r.visit.id, r.dimmed]] : []));
    expect(dimmed).toEqual([
      ["b-1", false],
      ["b-2", true],
      ["b-3", false],
    ]);
  });

  it("причина блока не печатается; перерыв назван перерывом", () => {
    const labels = masterDayRows([], masterDay(), []).flatMap((r) => (r.kind === "block" ? [r.label] : []));
    expect(labels).toEqual(["Перерыв", "Блок"]);
  });

  it("блок на весь день и многодневный — обрезаются сутками", () => {
    const whole = masterDay({
      free_windows: [],
      blocks: [
        { exception_id: "v", start: "2031-01-05T00:00:00+03:00", end: "2031-01-14T00:00:00+03:00", reason: "vacation", approved: true },
      ],
    });
    expect(masterDayRows([], whole, [])).toEqual([{ kind: "block", id: "v", start: "00:00", end: "24:00", label: "Блок" }]);
  });

  it("окна экран не выводит: нет строки — нет окна, даже в пустой смене", () => {
    expect(masterDayRows([], masterDay({ blocks: [] }), [])).toEqual([]);
  });

  it("day-schedule не пришёл — записи остаются", () => {
    expect(shape(masterDayRows(visits, null, []))).toHaveLength(3);
  });

  it("выходной говорит сервер", () => {
    expect(masterDayOff(masterDay())).toBe(false);
    expect(masterDayOff(masterDay({ working_hours: null }))).toBe(true);
    expect(masterDayOff(masterDay({ is_off_day: true }))).toBe(true);
  });

  it("конфликт назван словами по виду, без английского текста сервера и номеров записей", () => {
    const day = masterDay({
      conflicts: [
        { type: "double_booking", booking_id: "b-2", description: "Overlaps booking 7f3c (09:00-10:00)" },
        { type: "outside_hours", booking_id: "b-3", description: "Booking 16:00-17:00 outside hours 09:00-15:00" },
        { type: "outside_hours", booking_id: "b-3", description: "Booking 16:00-17:00 outside hours 09:00-15:00" },
        { type: "новый_вид", booking_id: "нет-в-дне", description: "whatever" },
      ],
    });
    expect(conflictItems(day, visits)).toEqual([
      { bookingId: "b-2", label: "Записи пересекаются по времени", detail: "10:30 · Клиент К." },
      { bookingId: "b-3", label: "Запись вне рабочих часов", detail: "16:00 · Клиент К." },
      { bookingId: "нет-в-дне", label: "Запись требует проверки", detail: null },
    ]);
    expect(conflictItems(null, visits)).toEqual([]);
  });
});
