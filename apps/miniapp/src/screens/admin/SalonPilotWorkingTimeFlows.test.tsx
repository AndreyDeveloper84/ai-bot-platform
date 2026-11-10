/**
 * DRF-2936 — сценарии записи «Рабочего времени» на каталоге в памяти.
 *
 * Цепочки `mockResolvedValueOnce` привязывают тест к числу чтений. Здесь
 * каталог хранит состояние, а запись меняет его так же, как сервер: перерыв
 * даты считается как в `_prepare_date_exception`, повтор той же
 * недоступности возвращает существующую строку. Поэтому проверяется исход —
 * что лежит в каталоге, — а не порядок вызовов. Ревью №2:
 *
 * * обед, выпавший из-за суженных часов, не теряется при возврате к шаблону;
 * * выходной на дату → рабочий по шаблону снимает изменение;
 * * пара «снял и вернул ту же» не читается как «Сохранено не всё»;
 * * чужие часы после «Расписание могло измениться» не перезаписываются;
 * * «Сохранить оставшееся», «Отмена» в «Проверяем результат», 401/403
 *   посреди шагов — без дублей и без снятия чужого.
 */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const clock = vi.hoisted(() => ({ startedAt: 0 }));

vi.mock("../../lib/app-clock", () => ({ appStartedAtMs: () => clock.startedAt }));

vi.mock("../../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/max-sdk")>();
  return { ...original, signalReady: () => undefined };
});

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    getMasterDetail: vi.fn(),
    getMasterSchedule: vi.fn(),
    getMasterExceptions: vi.fn(),
    getMasterDaySchedule: vi.fn(),
    getScheduleImpact: vi.fn(),
    putDateException: vi.fn(),
    deleteDateException: vi.fn(),
    createTimeOff: vi.fn(),
    deleteTimeOff: vi.fn(),
  };
});

import {
  createTimeOff,
  deleteDateException,
  deleteTimeOff,
  getMasterDaySchedule,
  getMasterDetail,
  getMasterExceptions,
  getMasterSchedule,
  getScheduleImpact,
  putDateException,
  type MasterDetail,
  type MasterExceptions,
  type MasterSchedule,
  type ScheduleImpact,
} from "../../lib/admin-api";
import { ApiError } from "../../lib/api";
import { settleScenario } from "../../test/settleScenario";
import { SalonPilotWorkingTimeDayScreen } from "./SalonPilotWorkingTimeDayScreen";

const mocked = {
  detail: vi.mocked(getMasterDetail),
  schedule: vi.mocked(getMasterSchedule),
  exceptions: vi.mocked(getMasterExceptions),
  day: vi.mocked(getMasterDaySchedule),
  impact: vi.mocked(getScheduleImpact),
  put: vi.mocked(putDateException),
  remove: vi.mocked(deleteDateException),
  createOff: vi.mocked(createTimeOff),
  removeOff: vi.mocked(deleteTimeOff),
};

/** Пятница; будущая дата — правка открыта. */
const DAY = "2031-01-10";
const DRAFT_KEY = `max:wt-draft:m-1:${DAY}`;
const NONE = { state: "none" as const, rows: [], seen_fields: [] };

interface Off {
  id: string;
  from: string;
  to: string;
  reason: string;
}

interface DateRow {
  is_working_day: boolean;
  start: string | null;
  end: string | null;
  break_start: string | null;
  break_end: string | null;
  note: string;
}

type Door = "create" | "removeOff" | "put" | "removeExc";

/**
 * Сбой записи: ошибка — запись НЕ легла; `"lost"` — легла, а ответ потерян.
 * `n` — номер вызова этой двери с начала теста.
 */
type Failure = (n: number) => ApiError | "lost" | undefined;

const catalog = {
  date: null as DateRow | null,
  offs: [] as Off[],
  seq: 1,
  /** Обед дня недели в шаблоне (пн–сб); `null` — без обеда. */
  lunch: null as [string, string] | null,
  fail: {} as Partial<Record<Door, Failure>>,
  calls: { create: 0, removeOff: 0, put: 0, removeExc: 0 } as Record<Door, number>,
};

function resetCatalog() {
  catalog.date = null;
  catalog.offs = [];
  catalog.seq = 1;
  catalog.lunch = null;
  catalog.fail = {};
  catalog.calls = { create: 0, removeOff: 0, put: 0, removeExc: 0 };
}

function scheduleNow(): MasterSchedule {
  const lunch = catalog.lunch;
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

function exceptionsNow(): MasterExceptions {
  const date = catalog.date;
  return {
    from: "2031-01-09",
    to: "2031-01-11",
    exceptions: date
      ? {
          state: "parsed",
          rows: [
            {
              id: "exc-1",
              date: DAY,
              is_working_day: date.is_working_day,
              start: date.is_working_day ? date.start : null,
              end: date.is_working_day ? date.end : null,
              break_start: date.is_working_day ? date.break_start : null,
              break_end: date.is_working_day ? date.break_end : null,
              has_note: date.note.trim() !== "",
            },
          ],
          seen_fields: [],
        }
      : NONE,
    time_off: catalog.offs.length
      ? {
          state: "parsed",
          rows: catalog.offs.map((o) => ({
            id: o.id,
            start_at: `${DAY}T${o.from}:00+03:00`,
            end_at: `${DAY}T${o.to}:00+03:00`,
            reason: o.reason,
          })),
          seen_fields: [],
        }
      : NONE,
    closures: NONE,
    unreadable_lists: [],
    writable: true,
  };
}

function noBookings(): ScheduleImpact {
  return {
    start_at: "",
    end_at: "",
    timezone: "Europe/Moscow",
    bookings: { state: "none", rows: [], seen_fields: [], unreadable_rows: 0 },
    writable: true,
    next_step: "pro_app",
  };
}

/** Сбой этой двери на этом вызове: бросить до записи или пометить «ответ потерян». */
function failureFor(door: Door): "lost" | null {
  catalog.calls[door] += 1;
  const outcome = catalog.fail[door]?.(catalog.calls[door]);
  if (outcome instanceof ApiError) throw outcome;
  return outcome === "lost" ? "lost" : null;
}

const LOST = () => new ApiError(503, "salon_write_outcome_unknown", "");

/**
 * Перерыв, который сервер запишет с новыми часами (`_prepare_date_exception`):
 * свой перерыв рабочей даты; нет своего — обед шаблона, если «без обеда» не
 * намеренное (обед шаблона не помещался в прежние часы даты).
 */
function breakOnPut(hours: { start: string; end: string }): [string, string] | null {
  const current = catalog.date;
  let source: [string, string] | null = null;
  if (current?.is_working_day && current.break_start && current.break_end) {
    source = [current.break_start, current.break_end];
  } else if (catalog.lunch) {
    const lunch = catalog.lunch;
    const deliberate =
      current?.is_working_day &&
      current.start !== null &&
      current.end !== null &&
      current.start <= lunch[0] &&
      lunch[1] <= current.end;
    source = deliberate ? null : lunch;
  }
  return source && hours.start <= source[0] && source[1] <= hours.end ? source : null;
}

function installCatalog() {
  mocked.detail.mockResolvedValue({ id: "m-1", name: "Мастер Один", photo_url: "" } as unknown as MasterDetail);
  mocked.schedule.mockImplementation(async () => scheduleNow());
  mocked.exceptions.mockImplementation(async () => exceptionsNow());
  mocked.day.mockImplementation(async () => ({
    tenant_tz: "Europe/Moscow",
    from: DAY,
    to: DAY,
    days: [
      {
        date: DAY,
        is_off_day: false,
        working_hours: { start: "10:00", end: "19:00" },
        bookings: [],
        blocks: [],
        free_windows: [],
        conflicts: [],
      },
    ],
  }));
  mocked.impact.mockImplementation(async () => noBookings());

  mocked.createOff.mockImplementation(async (_master, body) => {
    const lost = failureFor("create");
    let row = catalog.offs.find(
      (o) => o.from === body.from && o.to === body.to && o.reason.trim() === body.reason.trim(),
    );
    const existing = row !== undefined;
    if (!row) {
      row = { id: `off-${catalog.seq++}`, from: body.from, to: body.to, reason: body.reason };
      catalog.offs.push(row);
    }
    if (lost) throw LOST();
    return {
      row: {
        id: row.id,
        start_at: `${DAY}T${row.from}:00+03:00`,
        end_at: `${DAY}T${row.to}:00+03:00`,
        reason: row.reason,
      },
      existing,
    };
  });
  mocked.removeOff.mockImplementation(async (_master, id) => {
    const lost = failureFor("removeOff");
    const at = catalog.offs.findIndex((o) => o.id === id);
    if (at < 0) throw new ApiError(404, "not_found", "");
    catalog.offs.splice(at, 1);
    if (lost) throw LOST();
  });
  mocked.put.mockImplementation(async (_master, body) => {
    const lost = failureFor("put");
    const working = body.is_working_day;
    const start = working ? (body.start_time ?? null) : null;
    const end = working ? (body.end_time ?? null) : null;
    const brk = working && start && end ? breakOnPut({ start, end }) : null;
    catalog.date = {
      is_working_day: working,
      start,
      end,
      break_start: brk?.[0] ?? null,
      break_end: brk?.[1] ?? null,
      note: catalog.date?.note ?? "",
    };
    if (lost) throw LOST();
    return {
      id: "exc-1",
      date: DAY,
      is_working_day: working,
      start_time: start,
      end_time: end,
      break_start: catalog.date.break_start,
      break_end: catalog.date.break_end,
      note: catalog.date.note,
    };
  });
  mocked.remove.mockImplementation(async () => {
    const lost = failureFor("removeExc");
    if (!catalog.date) throw new ApiError(404, "not_found", "");
    catalog.date = null;
    if (lost) throw LOST();
  });
}

function renderDay() {
  return render(
    <MemoryRouter initialEntries={[`/admin/working-time/m-1/${DAY}`]}>
      <Routes>
        <Route path="/admin/working-time/:masterId" element={<p>неделя мастера</p>} />
        <Route path="/admin/working-time/:masterId/:date" element={<SalonPilotWorkingTimeDayScreen />} />
        <Route path="*" element={<p>другой экран</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function openDay() {
  renderDay();
  await screen.findByRole("switch", { name: "Рабочий день" });
}

/** Закрыть Mini App и открыть дату заново: черновик — только на устройстве. */
async function reopenDay() {
  cleanup();
  await openDay();
}

function pressSave() {
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
}

async function addOff(from: string, to: string, reason: string) {
  fireEvent.click(screen.getByRole("button", { name: "Добавить недоступность" }));
  const sheet = await screen.findByRole("dialog");
  fireEvent.change(within(sheet).getByLabelText("С"), { target: { value: from } });
  fireEvent.change(within(sheet).getByLabelText("До"), { target: { value: to } });
  fireEvent.change(within(sheet).getByLabelText("Причина"), { target: { value: reason } });
  fireEvent.click(within(sheet).getByRole("button", { name: "Добавить" }));
}

async function editOff(label: RegExp, patch: { to?: string; reason?: string }) {
  fireEvent.click(screen.getByRole("button", { name: label }));
  const sheet = await screen.findByRole("dialog");
  if (patch.to) fireEvent.change(within(sheet).getByLabelText("До"), { target: { value: patch.to } });
  if (patch.reason !== undefined) {
    fireEvent.change(within(sheet).getByLabelText("Причина"), { target: { value: patch.reason } });
  }
  fireEvent.click(within(sheet).getByRole("button", { name: "Сохранить" }));
}

const intervals = () => catalog.offs.map((o) => `${o.from}-${o.to} ${o.reason}`).sort();

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  clock.startedAt = Date.now() - 5_000;
  resetCatalog();
  installCatalog();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("изменение даты и обед шаблона", () => {
  it("сузил до 10–13, потом вернул 10–19 — изменение снято, обед шаблона снова действует", async () => {
    catalog.lunch = ["13:00", "14:00"];
    await openDay();

    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "13:00" } });
    pressSave();
    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(catalog.date).toMatchObject({ start: "10:00", end: "13:00", break_start: null });

    fireEvent.click(screen.getByRole("button", { name: /Рабочие часы/ }));
    fireEvent.change(await screen.findByLabelText("Окончание"), { target: { value: "19:00" } });
    pressSave();

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(mocked.remove).toHaveBeenCalledTimes(1);
    expect(catalog.date).toBeNull();
    expect(mocked.put).toHaveBeenCalledTimes(1);
  });

  it("выходной на дату → рабочий день по шаблону — изменение снято, а не прибито заново", async () => {
    catalog.lunch = ["13:00", "14:00"];
    catalog.date = { is_working_day: false, start: null, end: null, break_start: null, break_end: null, note: "" };
    await openDay();

    fireEvent.click(screen.getByRole("switch", { name: "Рабочий день" }));
    expect(screen.getByLabelText("Начало")).toHaveValue("10:00");
    expect(screen.getByLabelText("Окончание")).toHaveValue("19:00");
    pressSave();

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(mocked.remove).toHaveBeenCalledTimes(1);
    expect(catalog.date).toBeNull();
    await settleScenario();
    expect(mocked.put).not.toHaveBeenCalled();
  });
});

describe("исход записи судится по тому, что писали", () => {
  it("пара «снял и вернул ту же» и правка часов — «График обновлён», а не «Сохранено не всё»", async () => {
    catalog.offs = [{ id: "c", from: "13:00", to: "15:00", reason: "Личные дела" }];
    await openDay();

    await editOff(/Изменить: 13:00/, { reason: "Врач" });
    await editOff(/Изменить: 13:00/, { reason: "Личные дела" });
    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "18:00" } });
    pressSave();

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(catalog.date).toMatchObject({ start: "10:00", end: "18:00" });
    expect(intervals()).toEqual(["13:00-15:00 Личные дела"]);
    await settleScenario();
    expect(mocked.createOff).not.toHaveBeenCalled();
    expect(mocked.removeOff).not.toHaveBeenCalled();
  });
});

describe("чужая правка после открытия", () => {
  it("часы, изменённые в Pro App, показаны живыми и не перезаписываются вторым нажатием", async () => {
    await openDay();
    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "18:00" } });
    // Пока правили, дату изменили в Pro App.
    catalog.date = { is_working_day: true, start: "12:00", end: "16:00", break_start: null, break_end: null, note: "" };

    pressSave();
    expect(await screen.findByText("Расписание могло измениться")).toBeInTheDocument();
    expect(screen.getByLabelText("Начало")).toHaveValue("12:00");
    expect(screen.getByLabelText("Окончание")).toHaveValue("16:00");

    pressSave();
    expect(await screen.findByText(/сохранять нечего/)).toBeInTheDocument();
    expect(catalog.date).toMatchObject({ start: "12:00", end: "16:00" });
    await settleScenario();
    expect(mocked.put).not.toHaveBeenCalled();
  });
});

describe("неполный исход", () => {
  it("«Сохранить оставшееся» дописывает остаток, ничего не повторяя", async () => {
    catalog.offs = [{ id: "c", from: "16:00", to: "17:00", reason: "Личные дела" }];
    catalog.fail.removeOff = (n) => (n === 1 ? new ApiError(500, "salon_unavailable", "") : undefined);
    await openDay();

    await addOff("13:00", "15:00", "Врач");
    fireEvent.click(screen.getByRole("button", { name: /Удалить: 16:00/ }));
    pressSave();
    expect(await screen.findByRole("heading", { name: "Сохранено не всё" })).toBeInTheDocument();
    expect(intervals()).toEqual(["13:00-15:00 Врач", "16:00-17:00 Личные дела"]);

    fireEvent.click(screen.getByRole("button", { name: "Сохранить оставшееся" }));

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(intervals()).toEqual(["13:00-15:00 Врач"]);
    expect(mocked.createOff).toHaveBeenCalledTimes(1);
  });

  it("«Проверяем результат» → «Отмена» → открыть заново: всё легло — сохранять нечего, без дублей", async () => {
    catalog.fail.put = () => "lost";
    await openDay();

    await addOff("13:00", "15:00", "Личные дела");
    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "18:00" } });
    pressSave();
    expect(await screen.findByRole("heading", { name: "Проверяем результат" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(await screen.findByText("неделя мастера")).toBeInTheDocument();

    catalog.fail = {};
    await reopenDay();
    pressSave();

    expect(await screen.findByText(/сохранять нечего/)).toBeInTheDocument();
    expect(intervals()).toEqual(["13:00-15:00 Личные дела"]);
    expect(catalog.date).toMatchObject({ start: "10:00", end: "18:00" });
    expect(mocked.createOff).toHaveBeenCalledTimes(1);
    expect(mocked.put).toHaveBeenCalledTimes(1);
  });

  it("«Проверяем результат» → «Отмена» → открыть заново: не легло — повтор создаёт ровно одну", async () => {
    catalog.fail.create = (n) => (n === 1 ? LOST() : undefined);
    await openDay();

    await addOff("13:00", "15:00", "Личные дела");
    pressSave();
    expect(await screen.findByRole("heading", { name: "Проверяем результат" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(await screen.findByText("неделя мастера")).toBeInTheDocument();

    await reopenDay();
    expect(screen.getByText(/которые вы не сохранили, восстановлены/)).toBeInTheDocument();
    pressSave();

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(intervals()).toEqual(["13:00-15:00 Личные дела"]);
  });
});

describe("401/403 посреди шагов", () => {
  it("401 после созданной строки → открыть заново: дописано только оставшееся, чужое цело", async () => {
    catalog.offs = [{ id: "c", from: "13:00", to: "15:00", reason: "Личные дела" }];
    catalog.fail.removeOff = (n) => (n === 1 ? new ApiError(401, "stale", "") : undefined);
    await openDay();

    await editOff(/Изменить: 13:00/, { to: "16:00" });
    pressSave();
    expect(await screen.findByRole("heading", { name: "Откройте приложение заново" })).toBeInTheDocument();
    expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();

    // Пока приложение открывали заново, в Pro App поставили другую строку.
    catalog.offs.push({ id: "z", from: "17:00", to: "18:00", reason: "Больничный" });
    await reopenDay();
    expect(screen.getByText(/которые вы не сохранили, восстановлены/)).toBeInTheDocument();
    pressSave();

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(intervals()).toEqual(["13:00-16:00 Личные дела", "17:00-18:00 Больничный"]);
    expect(mocked.createOff).toHaveBeenCalledTimes(1);
  });

  it("403 после созданной строки → «Отмена» → «Сохранить»: дубля нет, часы дописаны", async () => {
    catalog.fail.put = (n) => (n === 1 ? new ApiError(403, "salon_write_refused", "") : undefined);
    await openDay();

    await addOff("13:00", "15:00", "Личные дела");
    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "18:00" } });
    pressSave();
    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));

    // Строка уже легла — первое нажатие показывает это, второе пишет часы.
    pressSave();
    expect(await screen.findByText("Расписание могло измениться")).toBeInTheDocument();
    pressSave();

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(intervals()).toEqual(["13:00-15:00 Личные дела"]);
    expect(catalog.date).toMatchObject({ start: "10:00", end: "18:00" });
    expect(mocked.createOff).toHaveBeenCalledTimes(1);
  });
});
