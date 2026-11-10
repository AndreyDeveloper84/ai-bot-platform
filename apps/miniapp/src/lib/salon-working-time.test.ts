/**
 * DRF-2936 — расчёты экрана «Рабочее время мастеров».
 *
 * Черновик — НАМЕРЕНИЯ: снимается только то, что человек снял сам. Здесь
 * держатся три потери данных, найденные ревью, и то, что их закрывает:
 *
 * * добавленная недоступность не снимается следующим сохранением;
 * * правка одной причины — новая строка и снятие старой, а не «снять всё»;
 * * чужая недоступность, появившаяся после открытия, не снимается вовсе.
 */
import { describe, expect, it } from "vitest";

import type {
  MasterDay,
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
  coversWholeDay,
  dateActionFor,
  dayTitle,
  draftApplied,
  freshDraft,
  isYmd,
  launchAgeSeconds,
  rebaseDraft,
  snapshotKey,
  snapshotOf,
  stepsFor,
  timeOffOn,
  visibleTimeOff,
  weekRangeLabel,
  weekRows,
  weekStart,
  writeWindowClosed,
  type DayDraft,
} from "./salon-working-time";

/** Пятница. */
const DAY = "2031-01-10";

/** Шаблон 10–19 с понедельника по субботу; `lunch` — обед рабочих дней. */
function schedule(lunch: [string, string] | null = null): MasterSchedule {
  return {
    source: "ayla",
    has_working_day: true,
    days: Array.from({ length: 7 }, (_, i) => ({
      day_of_week: i,
      is_working_day: i < 6,
      start_time: i < 6 ? "10:00:00" : null,
      end_time: i < 6 ? "19:00:00" : null,
      break_start: i < 6 && lunch ? `${lunch[0]}:00` : null,
      break_end: i < 6 && lunch ? `${lunch[1]}:00` : null,
    })),
    confirmation: { confirmed_at: null, confirmed_by: null, is_current: false, fingerprint: "f", block: null },
  };
}

const NONE = { state: "none" as const, rows: [], seen_fields: [] };

function exceptions(patch: Partial<MasterExceptions> = {}): MasterExceptions {
  return {
    from: "2031-01-09",
    to: "2031-01-11",
    exceptions: NONE,
    time_off: NONE,
    closures: NONE,
    unreadable_lists: [],
    writable: true,
    ...patch,
  };
}

function off(from: string, to: string, id: string, reason = "Личные дела"): MasterTimeOffRow {
  return { id, start_at: `${DAY}T${from}:00+03:00`, end_at: `${DAY}T${to}:00+03:00`, reason };
}

function withOffs(rows: MasterTimeOffRow[], patch: Partial<MasterExceptions> = {}): MasterExceptions {
  return exceptions({ time_off: { state: "parsed", rows, seen_fields: [] }, ...patch });
}

/** Строка изменения в форме нынешнего сервера: перерыв и заметка всегда есть. */
function withException(
  start: string,
  end: string,
  extra: { break_start?: string | null; break_end?: string | null; has_note?: boolean } = {},
): MasterExceptions {
  return exceptions({
    exceptions: {
      state: "parsed",
      rows: [
        {
          id: "exc-1",
          date: DAY,
          is_working_day: true,
          start,
          end,
          break_start: null,
          break_end: null,
          has_note: false,
          ...extra,
        },
      ],
      seen_fields: [],
    },
  });
}

const snap = (ex: MasterExceptions = exceptions()) => snapshotOf(DAY, schedule(), ex);
const fresh = (ex?: MasterExceptions) => freshDraft(snap(ex), ["", ""]);

describe("даты макета", () => {
  it("неделя — с понедельника; подпись как на макете", () => {
    expect(weekStart(DAY)).toBe("2031-01-06");
    expect(weekRangeLabel("2031-01-06")).toBe("6 – 12 янв. 2031");
    expect(weekRangeLabel("2031-01-27")).toBe("27 янв. – 2 февр. 2031");
    expect(weekRangeLabel("2030-12-30")).toBe("30 дек. 2030 – 5 янв. 2031");
  });

  it("заголовок дня — «Пятница, 10 января»; несуществующая дата — не дата", () => {
    expect(dayTitle(DAY)).toBe("Пятница, 10 января");
    expect(isYmd(DAY)).toBe(true);
    expect(isYmd("2026-13-45")).toBe(false);
    expect(isYmd("2031-02-30")).toBe(false);
  });
});

describe("недоступность внутри даты", () => {
  it("однодневная правится, многодневная — только читается с периодом", () => {
    const rows = timeOffOn(
      [
        off("13:00", "15:00", "one"),
        { id: "vacation", start_at: "2031-01-05T00:00:00+03:00", end_at: "2031-01-14T00:00:00+03:00", reason: "Отпуск" },
      ],
      DAY,
    );
    expect(rows.map((r) => [r.id, r.from, r.to, r.editable])).toEqual([
      ["vacation", "00:00", "24:00", false],
      ["one", "13:00", "15:00", true],
    ]);
  });

  it("смежные строки вместе закрывают весь день", () => {
    const hours = { start: "10:00", end: "19:00" };
    expect(coversWholeDay(hours, [{ from: "10:00", to: "14:00" }, { from: "14:00", to: "19:00" }])).toBe(true);
    expect(coversWholeDay(hours, [{ from: "10:00", to: "13:00" }, { from: "14:00", to: "19:00" }])).toBe(false);
  });
});

describe("снимается только снятое человеком", () => {
  it("правка одной причины — новая строка и снятие ровно старой", () => {
    const ex = withOffs([off("13:00", "15:00", "old")]);
    const draft: DayDraft = {
      ...fresh(ex),
      removedIds: ["old"],
      added: [{ key: "n", from: "13:00", to: "15:00", reason: "Врач" }],
    };
    expect(stepsFor(snap(ex), draft)).toEqual([
      { kind: "time_off_create", item: { key: "n", from: "13:00", to: "15:00", reason: "Врач" } },
      expect.objectContaining({ kind: "time_off_delete", item: expect.objectContaining({ id: "old" }) }),
    ]);
  });

  it("чужая строка, появившаяся после открытия, не снимается и видна на экране", () => {
    const opened = withOffs([off("13:00", "15:00", "mine")]);
    const draft: DayDraft = { ...fresh(opened), hoursTouched: true, start: "11:00" };
    const now = withOffs([off("13:00", "15:00", "mine"), off("16:00", "17:00", "foreign", "Больничный")]);

    expect(snapshotKey(snap(now))).not.toBe(draft.basis);
    const rebased = rebaseDraft(snap(now), draft);
    expect(rebased.basis).toBe(snapshotKey(snap(now)));
    expect(visibleTimeOff(snap(now), rebased).map((v) => v.key)).toEqual(["mine", "foreign"]);
    expect(stepsFor(snap(now), rebased).map((s) => s.kind)).toEqual(["date_put"]);
  });

  it("часы, изменённые кем-то другим после начала правки, — живые: правка не ляжет поверх чужой", () => {
    // Ревью №2, №4: человек правил 10–19 → 10–18, а в Pro App дату сделали
    // 12–16. Второе нажатие не должно молча записать 10–18 поверх 12–16.
    const draft: DayDraft = { ...fresh(), hoursTouched: true, end: "18:00" };
    const now = snap(withException("12:00", "16:00"));

    const rebased = rebaseDraft(now, draft);
    expect(rebased).toMatchObject({ hoursTouched: false, working: true, start: "12:00", end: "16:00" });
    expect(rebased.basisLayer).toEqual({ working: true, start: "12:00", end: "16:00" });
    expect(stepsFor(now, rebased)).toEqual([]);
  });

  it("часы не менял никто, кроме человека, — его правка переносится", () => {
    const draft: DayDraft = { ...fresh(), hoursTouched: true, end: "18:00" };
    const now = snap(withOffs([off("16:00", "17:00", "foreign", "Больничный")]));

    const rebased = rebaseDraft(now, draft);
    expect(rebased).toMatchObject({ hoursTouched: true, start: "10:00", end: "18:00" });
    expect(stepsFor(now, rebased).map((s) => s.kind)).toEqual(["date_put"]);
  });

  it("после записи исполненное выпадает: повторное сохранение ничего не снимает", () => {
    const before = exceptions();
    const draft: DayDraft = {
      ...fresh(before),
      added: [{ key: "n", from: "13:00", to: "15:00", reason: "Личные дела" }],
    };
    const after = snap(withOffs([off("13:00", "15:00", "created")]));

    expect(draftApplied(after, draft)).toBe(true);
    const rebased = rebaseDraft(after, draft);
    expect(rebased.added).toEqual([]);
    expect(stepsFor(after, rebased)).toEqual([]);
  });

  it("снятая и заново добавленная та же строка — пара ничего не меняет", () => {
    const ex = withOffs([off("13:00", "15:00", "old")]);
    const draft: DayDraft = {
      ...fresh(ex),
      removedIds: ["old"],
      added: [{ key: "n", from: "13:00", to: "15:00", reason: " Личные дела " }],
    };
    expect(stepsFor(snap(ex), draft)).toEqual([]);
  });

  it("снятие строки, которой уже нет, — сделано", () => {
    const draft: DayDraft = { ...fresh(withOffs([off("13:00", "15:00", "gone")])), removedIds: ["gone"] };
    expect(rebaseDraft(snap(), draft).removedIds).toEqual([]);
  });
});

describe("изменение на дату — по намерению, с сохранением чужого", () => {
  it("часы не трогали — изменения нет, даже если оно равно шаблону", () => {
    const ex = withException("10:00", "19:00", { has_note: true });
    const draft: DayDraft = {
      ...fresh(ex),
      added: [{ key: "n", from: "13:00", to: "15:00", reason: "Личные дела" }],
    };
    expect(dateActionFor(snap(ex), draft)).toEqual({ kind: "none" });
    expect(stepsFor(snap(ex), draft).map((s) => s.kind)).toEqual(["time_off_create"]);
  });

  it("вернули к шаблону — снять изменение, если оно ничего, кроме часов, не несёт", () => {
    const ex = withException("12:00", "16:00");
    const draft: DayDraft = { ...fresh(ex), hoursTouched: true, start: "10:00", end: "19:00" };
    expect(dateActionFor(snap(ex), draft)).toEqual({ kind: "delete" });
  });

  it("вернули к шаблону, а у даты своя заметка или перерыв — переписать часы, не снимая", () => {
    for (const extra of [{ has_note: true }, { break_start: "15:00", break_end: "16:00" }]) {
      const ex = withException("12:00", "16:00", extra);
      const draft: DayDraft = { ...fresh(ex), hoursTouched: true, start: "10:00", end: "19:00" };
      expect(dateActionFor(snap(ex), draft)).toEqual({
        kind: "put",
        layer: { working: true, start: "10:00", end: "19:00" },
      });
    }
  });

  it("обед шаблона выпал из-за суженных часов — возврат к шаблону снимает изменение, обед вернётся", () => {
    // Ревью №2, блокер: дату сужали до 10–13, обед 13–14 оказался снаружи и
    // не записан. «Без обеда» здесь не решение салона, а следствие часов.
    const s = snapshotOf(DAY, schedule(["13:00", "14:00"]), withException("10:00", "13:00"));
    const draft: DayDraft = { ...freshDraft(s, ["", ""]), hoursTouched: true, start: "10:00", end: "19:00" };
    expect(dateActionFor(s, draft)).toEqual({ kind: "delete" });
  });

  it("«без обеда» поставлено намеренно — обед шаблона помещался в часы даты: не снимать", () => {
    const s = snapshotOf(DAY, schedule(["13:00", "14:00"]), withException("10:00", "18:00"));
    const draft: DayDraft = { ...freshDraft(s, ["", ""]), hoursTouched: true, start: "10:00", end: "19:00" };
    expect(dateActionFor(s, draft)).toEqual({
      kind: "put",
      layer: { working: true, start: "10:00", end: "19:00" },
    });
  });

  it("свой перерыв, равный обеду шаблона, — ничего сверх часов, снимается", () => {
    const ex = withException("10:00", "18:00", { break_start: "13:00", break_end: "14:00" });
    const s = snapshotOf(DAY, schedule(["13:00", "14:00"]), ex);
    const draft: DayDraft = { ...freshDraft(s, ["", ""]), hoursTouched: true, start: "10:00", end: "19:00" };
    expect(dateActionFor(s, draft)).toEqual({ kind: "delete" });
  });

  it("выходной на дату → рабочий по шаблону: снять изменение — у выходного перерыва нет", () => {
    const ex = exceptions({
      exceptions: {
        state: "parsed",
        rows: [
          {
            id: "exc-1",
            date: DAY,
            is_working_day: false,
            start: null,
            end: null,
            break_start: null,
            break_end: null,
            has_note: false,
          },
        ],
        seen_fields: [],
      },
    });
    const s = snapshotOf(DAY, schedule(["13:00", "14:00"]), ex);
    const draft: DayDraft = {
      ...freshDraft(s, ["10:00", "19:00"]),
      hoursTouched: true,
      working: true,
      start: "10:00",
      end: "19:00",
    };
    expect(dateActionFor(s, draft)).toEqual({ kind: "delete" });
  });

  it("прежний сервер без полей перерыва и заметки — изменение не снимается вслепую", () => {
    const ex = exceptions({
      exceptions: {
        state: "parsed",
        rows: [{ id: "exc-1", date: DAY, is_working_day: true, start: "12:00", end: "16:00" }],
        seen_fields: [],
      },
    });
    const draft: DayDraft = { ...fresh(ex), hoursTouched: true, start: "10:00", end: "19:00" };
    expect(dateActionFor(snap(ex), draft)).toEqual({
      kind: "put",
      layer: { working: true, start: "10:00", end: "19:00" },
    });
  });

  it("выходной — изменение на дату", () => {
    const draft: DayDraft = { ...fresh(), hoursTouched: true, working: false };
    expect(dateActionFor(snap(), draft)).toEqual({
      kind: "put",
      layer: { working: false, start: null, end: null },
    });
  });
});

describe("окна предпросмотра — ровно то, что правка отнимает", () => {
  it("выходной из рабочего — сутки целиком", () => {
    const draft: DayDraft = { ...fresh(), hoursTouched: true, working: false };
    expect(closingWindows(snap(), draft)).toEqual([{ from: "00:00", to: "24:00" }]);
  });

  it("сокращение — отрезанные края; расширение — ничего", () => {
    expect(closingWindows(snap(), { ...fresh(), hoursTouched: true, start: "11:00", end: "18:00" })).toEqual([
      { from: "10:00", to: "11:00" },
      { from: "18:00", to: "19:00" },
    ]);
    expect(closingWindows(snap(), { ...fresh(), hoursTouched: true, start: "09:00", end: "20:00" })).toEqual([]);
  });

  it("новая недоступность — её окно", () => {
    const draft: DayDraft = { ...fresh(), added: [{ key: "n", from: "13:00", to: "15:00", reason: "Личные дела" }] };
    expect(closingWindows(snap(), draft)).toEqual([{ from: "13:00", to: "15:00" }]);
  });
});

describe("порядок шагов: сначала закрыть время, потом открыть", () => {
  it("сокращение (закрывает) раньше снятия недоступности (открывает)", () => {
    const ex = withOffs([off("13:00", "15:00", "old")]);
    const draft: DayDraft = { ...fresh(ex), hoursTouched: true, start: "11:00", removedIds: ["old"] };
    expect(stepsFor(snap(ex), draft).map((s) => s.kind)).toEqual(["date_put", "time_off_delete"]);
  });

  it("расширение часов открывает — идёт последним", () => {
    const ex = withOffs([off("13:00", "15:00", "old")]);
    const draft: DayDraft = {
      ...fresh(ex),
      hoursTouched: true,
      start: "09:00",
      removedIds: ["old"],
      added: [{ key: "n", from: "16:00", to: "17:00", reason: "Личные дела" }],
    };
    expect(stepsFor(snap(ex), draft).map((s) => s.kind)).toEqual([
      "time_off_create",
      "time_off_delete",
      "date_put",
    ]);
  });
});

describe("строки недели — часами сервера", () => {
  function days(hours: Record<string, [string, string] | null>): MasterDay[] {
    return Object.entries(hours).map(([date, h]) => ({
      date,
      is_off_day: h === null,
      working_hours: h ? { start: h[0], end: h[1] } : null,
      bookings: [],
      blocks: [],
      free_windows: [],
      conflicts: [],
    }));
  }

  it("выходной, закрытие, полнодневная недоступность, изменение даты, частичное закрытие", () => {
    const ex = exceptions({
      exceptions: {
        state: "parsed",
        rows: [{ id: "e", date: "2031-01-07", is_working_day: true, start: "12:00", end: "16:00" }],
        seen_fields: [],
      },
      time_off: {
        state: "parsed",
        rows: [
          { id: "all", start_at: "2031-01-08T00:00:00+03:00", end_at: "2031-01-09T00:00:00+03:00", reason: "" },
          { id: "part", start_at: "2031-01-09T13:00:00+03:00", end_at: "2031-01-09T15:00:00+03:00", reason: "" },
        ],
        seen_fields: [],
      },
      closures: {
        state: "parsed",
        rows: [
          { id: "c", date: "2031-01-10", start: null, end: null, reason: "" },
          { id: "c2", date: "2031-01-11", start: "10:00", end: "14:00", reason: "" },
        ],
        seen_fields: [],
      },
    });
    const rows = weekRows(
      "2031-01-06",
      days({
        "2031-01-06": ["10:00", "19:00"],
        "2031-01-07": ["12:00", "16:00"],
        "2031-01-08": ["10:00", "19:00"],
        "2031-01-09": ["10:00", "19:00"],
        "2031-01-10": ["10:00", "19:00"],
        "2031-01-11": ["10:00", "18:00"],
        "2031-01-12": null,
      }),
      ex,
      "2031-01-07",
    );
    expect(rows.map((r) => [r.ymd, r.status, r.hours, r.override, r.past])).toEqual([
      ["2031-01-06", "working", "10:00 – 19:00", false, true],
      ["2031-01-07", "working", "12:00 – 16:00", true, false],
      ["2031-01-08", "unavailable", null, false, false],
      ["2031-01-09", "working", "10:00 – 19:00", false, false],
      ["2031-01-10", "closed", null, false, false],
      ["2031-01-11", "working", "10:00 – 18:00", false, false],
      ["2031-01-12", "off", null, false, false],
    ]);
    expect(rows[3]?.partial).toEqual(["13:00–15:00"]);
    expect(rows[5]?.closedPart).toBe("10:00–14:00");
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
    expect(conflictRows([impact([one]), impact([one, other])], [booking])).toEqual([
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
    expect(classifyWriteError(new ApiError(503, "salon_unavailable", ""))).toEqual({ kind: "failed" });
    expect(
      classifyWriteError(
        new ApiError(400, "validation", "", {
          reason: "template_break_crosses_hours",
          break_start: "13:00",
          break_end: "14:00",
        }),
      ),
    ).toEqual({ kind: "break_crosses", from: "13:00", to: "14:00" });
  });

  it("404 на снятии — уже снято; на записи — отказ, ничего не записано", () => {
    const notFound = new ApiError(404, "not_found", "");
    expect(classifyWriteError(notFound, "time_off_delete")).toEqual({ kind: "gone" });
    expect(classifyWriteError(notFound, "date_delete")).toEqual({ kind: "gone" });
    expect(classifyWriteError(notFound, "time_off_create")).toEqual({ kind: "failed" });
    expect(classifyWriteError(notFound, "date_put")).toEqual({ kind: "failed" });
  });
});

describe("окно записи — 10 минут от открытия, по часам устройства", () => {
  const started = Date.UTC(2031, 0, 10, 12, 0, 0);

  it("возраст — разность двух отметок одних часов", () => {
    expect(launchAgeSeconds(started, started + 120_000)).toBe(120);
  });

  it("за минуту до края окно считается закрытым", () => {
    expect(writeWindowClosed(started, started + 500_000)).toBe(false);
    expect(writeWindowClosed(started, started + 560_000)).toBe(true);
  });
});
