/**
 * DRF-2936 — расчёты экрана «Рабочее время мастеров».
 *
 * Держится то, на чём стоит запись: правка сверяется с ШАБЛОНОМ (лишний
 * override привязал бы дату к себе), окна предпросмотра — ровно то, что правка
 * отнимает, шаги — сначала закрывающие время, и записи из предпросмотра
 * получают имя клиента по ТОМУ ЖЕ номеру, а не по времени.
 */
import { describe, expect, it } from "vitest";

import type {
  MasterDayBooking,
  MasterExceptions,
  MasterSchedule,
  MasterTimeOffRow,
  ScheduleImpact,
} from "./admin-api";
import { ApiError } from "./api";
import {
  classifyWriteError,
  closingWindows,
  conflictRows,
  dateActionFor,
  dayTitle,
  draftApplied,
  initialDraft,
  launchAgeSeconds,
  snapshotOf,
  stepsFor,
  timeOffOn,
  timeOffPlanFor,
  weekRangeLabel,
  weekRows,
  weekStart,
  writeWindowClosed,
  type DayDraft,
} from "./salon-working-time";

/** Пятница. */
const DAY = "2031-01-10";

function schedule(patch: Partial<MasterSchedule> = {}): MasterSchedule {
  return {
    source: "ayla",
    has_working_day: true,
    days: Array.from({ length: 7 }, (_, i) => ({
      day_of_week: i,
      is_working_day: i < 6,
      start_time: i < 6 ? "10:00:00" : null,
      end_time: i < 6 ? "19:00:00" : null,
      break_start: null,
      break_end: null,
    })),
    confirmation: {
      confirmed_at: null,
      confirmed_by: null,
      is_current: false,
      fingerprint: "f",
      block: null,
    },
    ...patch,
  };
}

function exceptions(patch: Partial<MasterExceptions> = {}): MasterExceptions {
  const empty = { state: "none" as const, rows: [], seen_fields: [] };
  return {
    from: "2031-01-09",
    to: "2031-01-11",
    exceptions: empty,
    time_off: empty,
    closures: empty,
    unreadable_lists: [],
    writable: true,
    ...patch,
  };
}

function offRow(start: string, end: string, id = "off-1", reason = "Личные дела"): MasterTimeOffRow {
  return { id, start_at: start, end_at: end, reason };
}

function withTimeOff(rows: MasterTimeOffRow[]): MasterExceptions {
  return exceptions({ time_off: { state: "parsed", rows, seen_fields: [] } });
}

function withException(isWorking: boolean, start: string | null, end: string | null): MasterExceptions {
  return exceptions({
    exceptions: {
      state: "parsed",
      rows: [{ id: "exc-1", date: DAY, is_working_day: isWorking, start, end }],
      seen_fields: [],
    },
  });
}

describe("даты макета", () => {
  it("неделя — с понедельника; подпись как на макете", () => {
    expect(weekStart(DAY)).toBe("2031-01-06");
    expect(weekRangeLabel("2031-01-06")).toBe("6 – 12 янв. 2031");
    expect(weekRangeLabel("2031-01-27")).toBe("27 янв. – 2 февр. 2031");
    expect(weekRangeLabel("2030-12-30")).toBe("30 дек. 2030 – 5 янв. 2031");
  });

  it("заголовок дня — «Пятница, 10 января»", () => {
    expect(dayTitle(DAY)).toBe("Пятница, 10 января");
  });
});

describe("недоступность внутри даты", () => {
  it("однодневная правится, многодневная — только читается с периодом", () => {
    const rows = timeOffOn(
      [
        offRow("2031-01-10T13:00:00+03:00", "2031-01-10T15:00:00+03:00", "one"),
        offRow("2031-01-05T00:00:00+03:00", "2031-01-14T00:00:00+03:00", "vacation", "Отпуск"),
      ],
      DAY,
    );
    expect(rows.map((r) => [r.id, r.from, r.to, r.editable])).toEqual([
      ["vacation", "00:00", "24:00", false],
      ["one", "13:00", "15:00", true],
    ]);
  });

  it("конец в полночь следующих суток — это «до 24:00» этой даты, и она правится", () => {
    const [row] = timeOffOn([offRow("2031-01-10T20:00:00+03:00", "2031-01-11T00:00:00+03:00")], DAY);
    expect(row).toMatchObject({ from: "20:00", to: "24:00", editable: true });
  });
});

describe("правка сверяется с шаблоном, а не с текущими часами", () => {
  const snap = (ex = exceptions()) => snapshotOf(DAY, schedule(), ex);

  it("равна шаблону и изменения нет — ничего", () => {
    const s = snap();
    expect(dateActionFor(s, initialDraft(s, ["", ""]))).toEqual({ kind: "none" });
  });

  it("равна шаблону, а изменение есть — вернуть к шаблону, не писать override", () => {
    const s = snap(withException(true, "12:00", "16:00"));
    const draft: DayDraft = { working: true, start: "10:00", end: "19:00", timeOff: [] };
    expect(dateActionFor(s, draft)).toEqual({ kind: "delete" });
  });

  it("отличается от шаблона — изменение на дату", () => {
    const s = snap();
    const draft: DayDraft = { working: false, start: "10:00", end: "19:00", timeOff: [] };
    expect(dateActionFor(s, draft)).toEqual({
      kind: "put",
      layer: { working: false, start: null, end: null },
    });
  });
});

describe("окна предпросмотра — ровно то, что правка отнимает", () => {
  it("выходной из рабочего — сутки целиком", () => {
    const s = snapshotOf(DAY, schedule(), exceptions());
    const draft: DayDraft = { working: false, start: "10:00", end: "19:00", timeOff: [] };
    expect(closingWindows(s, draft)).toEqual([{ from: "00:00", to: "24:00" }]);
  });

  it("сокращение — отрезанные края; расширение — ничего", () => {
    const s = snapshotOf(DAY, schedule(), exceptions());
    expect(
      closingWindows(s, { working: true, start: "11:00", end: "18:00", timeOff: [] }),
    ).toEqual([
      { from: "10:00", to: "11:00" },
      { from: "18:00", to: "19:00" },
    ]);
    expect(closingWindows(s, { working: true, start: "09:00", end: "20:00", timeOff: [] })).toEqual([]);
  });

  it("новая недоступность — её окно", () => {
    const s = snapshotOf(DAY, schedule(), exceptions());
    const draft: DayDraft = {
      working: true,
      start: "10:00",
      end: "19:00",
      timeOff: [{ key: "n", id: null, from: "13:00", to: "15:00", reason: "Личные дела" }],
    };
    expect(closingWindows(s, draft)).toEqual([{ from: "13:00", to: "15:00" }]);
  });
});

describe("порядок шагов: сначала закрыть время, потом открыть", () => {
  it("правка недоступности — новая строка раньше снятия старой", () => {
    const ex = withTimeOff([offRow("2031-01-10T13:00:00+03:00", "2031-01-10T15:00:00+03:00", "old")]);
    const s = snapshotOf(DAY, schedule(), ex);
    const draft: DayDraft = {
      working: true,
      start: "10:00",
      end: "19:00",
      timeOff: [{ key: "old", id: "old", from: "14:00", to: "16:00", reason: "Личные дела" }],
    };
    expect(timeOffPlanFor(s, draft).create.map((c) => c.from)).toEqual(["14:00"]);
    expect(stepsFor(s, draft).map((st) => st.kind)).toEqual(["time_off_create", "time_off_delete"]);
  });

  it("выходной (закрывает) идёт раньше снятия недоступности (открывает)", () => {
    const ex = withTimeOff([offRow("2031-01-10T13:00:00+03:00", "2031-01-10T15:00:00+03:00", "old")]);
    const s = snapshotOf(DAY, schedule(), ex);
    const draft: DayDraft = { working: true, start: "11:00", end: "19:00", timeOff: [] };
    expect(stepsFor(s, draft).map((st) => st.kind)).toEqual(["date_put", "time_off_delete"]);
  });

  it("расширение часов открывает — идёт после", () => {
    const ex = withTimeOff([offRow("2031-01-10T13:00:00+03:00", "2031-01-10T15:00:00+03:00", "old")]);
    const s = snapshotOf(DAY, schedule(), ex);
    const draft: DayDraft = {
      working: true,
      start: "09:00",
      end: "19:00",
      timeOff: [{ key: "n", id: null, from: "16:00", to: "17:00", reason: "Личные дела" }],
    };
    expect(stepsFor(s, draft).map((st) => st.kind)).toEqual([
      "time_off_create",
      "time_off_delete",
      "date_put",
    ]);
  });

  it("перечитанное состояние совпало с черновиком — правка применена", () => {
    const after = snapshotOf(
      DAY,
      schedule(),
      withTimeOff([offRow("2031-01-10T13:00:00+03:00", "2031-01-10T15:00:00+03:00", "new")]),
    );
    const draft: DayDraft = {
      working: true,
      start: "10:00",
      end: "19:00",
      timeOff: [{ key: "n", id: null, from: "13:00", to: "15:00", reason: "Личные дела" }],
    };
    expect(draftApplied(after, draft)).toBe(true);
    expect(draftApplied(after, { ...draft, timeOff: [] })).toBe(false);
  });
});

describe("строки недели", () => {
  it("выходной, закрытие, полнодневная недоступность, изменение даты", () => {
    const ex = exceptions({
      exceptions: {
        state: "parsed",
        rows: [{ id: "e", date: "2031-01-07", is_working_day: true, start: "12:00", end: "16:00" }],
        seen_fields: [],
      },
      time_off: {
        state: "parsed",
        rows: [
          offRow("2031-01-08T00:00:00+03:00", "2031-01-09T00:00:00+03:00", "all-day"),
          offRow("2031-01-09T13:00:00+03:00", "2031-01-09T15:00:00+03:00", "part"),
        ],
        seen_fields: [],
      },
      closures: {
        state: "parsed",
        rows: [{ id: "c", date: "2031-01-10", start: null, end: null, reason: "" }],
        seen_fields: [],
      },
    });
    const rows = weekRows("2031-01-06", schedule(), ex, "2031-01-07");
    expect(rows.map((r) => [r.ymd, r.status, r.hours, r.override, r.past])).toEqual([
      ["2031-01-06", "working", "10:00 – 19:00", false, true],
      ["2031-01-07", "working", "12:00 – 16:00", true, false],
      ["2031-01-08", "unavailable", null, false, false],
      ["2031-01-09", "working", "10:00 – 19:00", false, false],
      ["2031-01-10", "closed", null, false, false],
      ["2031-01-11", "working", "10:00 – 19:00", false, false],
      ["2031-01-12", "off", null, false, false],
    ]);
    expect(rows[3]?.partial).toEqual(["13:00–15:00"]);
  });
});

describe("записи из предпросмотра", () => {
  function impact(rows: Array<{ id: string; start: string; end: string }>, patch = {}): ScheduleImpact {
    return {
      start_at: "",
      end_at: "",
      timezone: "Europe/Moscow",
      bookings: {
        state: rows.length ? "parsed" : "none",
        rows: rows.map((r) => ({
          appointment_id: r.id,
          start_local: r.start,
          end_local: r.end,
          service_name: "Маникюр",
          status: "confirmed",
          payment_status: null,
          refund_percent_if_cancelled: null,
        })),
        seen_fields: [],
        unreadable_rows: 0,
        ...patch,
      },
      writable: true,
      next_step: "pro_app",
    };
  }
  const booking: MasterDayBooking = {
    booking_id: "a-1",
    visit_at: "2031-01-10T13:00:00+03:00",
    duration_min: 60,
    service_name: "Маникюр",
    client_first_name: "Анна",
    client_last_initial: "К.",
    is_in_progress: false,
    is_returning_customer: false,
  };

  it("без повторов; имя — по номеру записи, а не по времени", () => {
    const one = { id: "a-1", start: "2031-01-10T13:00:00+03:00", end: "2031-01-10T14:00:00+03:00" };
    const other = { id: "a-2", start: "2031-01-10T14:30:00+03:00", end: "2031-01-10T15:30:00+03:00" };
    const rows = conflictRows([impact([one]), impact([one, other])], [booking]);
    expect(rows).toEqual([
      { appointmentId: "a-1", from: "13:00", to: "14:00", service: "Маникюр", client: "Анна К.", linkable: true },
      { appointmentId: "a-2", from: "14:30", to: "15:30", service: "Маникюр", client: null, linkable: false },
    ]);
  });

  it("неразобранная строка — не «записей нет», а отказ", () => {
    expect(conflictRows([impact([], { unreadable_rows: 1 })], [])).toBeNull();
  });
});

describe("отказы записи", () => {
  it("каждый код — своё состояние", () => {
    expect(classifyWriteError(new ApiError(409, "has_active_appointments", ""))).toEqual({ kind: "conflict" });
    expect(classifyWriteError(new ApiError(401, "stale", ""))).toEqual({ kind: "stale" });
    expect(classifyWriteError(new ApiError(403, "salon_write_refused", ""))).toEqual({ kind: "forbidden" });
    expect(classifyWriteError(new ApiError(503, "salon_write_outcome_unknown", ""))).toEqual({ kind: "unknown" });
    expect(classifyWriteError(new ApiError(504, "http_error", ""))).toEqual({ kind: "unknown" });
    expect(classifyWriteError(new TypeError("Failed to fetch"))).toEqual({ kind: "unknown" });
    expect(
      classifyWriteError(
        new ApiError(400, "validation", "", {
          reason: "template_break_crosses_hours",
          break_start: "13:00",
          break_end: "14:00",
        }),
      ),
    ).toEqual({ kind: "break_crosses", from: "13:00", to: "14:00" });
    expect(classifyWriteError(new ApiError(503, "salon_unavailable", ""))).toEqual({ kind: "failed" });
  });
});

describe("окно записи — 10 минут от открытия Mini App", () => {
  const now = Date.UTC(2031, 0, 10, 12, 0, 0);
  const launched = (secondsAgo: number) => now / 1000 - secondsAgo;

  it("возраст запуска — по auth_date", () => {
    expect(launchAgeSeconds(launched(120), now)).toBe(120);
    expect(launchAgeSeconds(null, now)).toBeNull();
  });

  it("за минуту до края окно считается закрытым", () => {
    expect(writeWindowClosed(launched(500), now)).toBe(false);
    expect(writeWindowClosed(launched(560), now)).toBe(true);
  });
});
