/**
 * DRF-2936 — «Рабочее время мастеров» салона по макету DRF-2865 FROZEN v1.0.
 *
 * Держится то, что обещает замороженный контракт:
 *
 * * список мастеров, неделя датами, правка ОДНОЙ даты;
 * * изменение не сохраняется, если в отнятом времени есть записи, — кадр 7
 *   показывает их, а записи на сервер нет;
 * * «График обновлён» — только после перечитывания; потерянный ответ —
 *   «Проверяем результат», а не успех и не повтор вслепую;
 * * совпавшее с шаблоном — снятие изменения, а не новый override.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
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
    listMasters: vi.fn(),
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
  listMasters,
  putDateException,
  type MasterDetail,
  type MasterExceptions,
  type MasterSchedule,
  type ScheduleImpact,
} from "../../lib/admin-api";
import { ApiError } from "../../lib/api";
import { settleScenario } from "../../test/settleScenario";
import { SalonPilotWorkingTimeDayScreen } from "./SalonPilotWorkingTimeDayScreen";
import { SalonPilotWorkingTimeScreen } from "./SalonPilotWorkingTimeScreen";
import { SalonPilotWorkingTimeWeekScreen } from "./SalonPilotWorkingTimeWeekScreen";

const mocked = {
  listMasters: vi.mocked(listMasters),
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

/** Ни одной записи на сервер — все четыре двери записи молчат. */
function expectNoWrites() {
  expect(mocked.put).not.toHaveBeenCalled();
  expect(mocked.remove).not.toHaveBeenCalled();
  expect(mocked.createOff).not.toHaveBeenCalled();
  expect(mocked.removeOff).not.toHaveBeenCalled();
}

/** Пятница; будущая дата — правка открыта. */
const DAY = "2031-01-10";
const DAY_PATH = `/admin/working-time/m-1/${DAY}`;

function startedAgo(seconds = 5): number {
  return Date.now() - seconds * 1000;
}

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
    confirmation: { confirmed_at: null, confirmed_by: null, is_current: false, fingerprint: "f", block: null },
    ...patch,
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

/** Строка изменения в форме нынешнего сервера: перерыв и заметка всегда есть. */
function exceptionOn(isWorking: boolean, start: string | null, end: string | null): MasterExceptions {
  return exceptions({
    exceptions: {
      state: "parsed",
      rows: [
        {
          id: "exc-1",
          date: DAY,
          is_working_day: isWorking,
          start,
          end,
          break_start: null,
          break_end: null,
          note: "",
        },
      ],
      seen_fields: [],
    },
  });
}

const DETAIL = {
  id: "m-1",
  name: "Мастер Один",
  specialization: "Мастер ногтевого сервиса",
  photo_url: "",
} as unknown as MasterDetail;

function dayFrame(bookings: Array<{ id: string; first: string; initial: string }> = []) {
  return {
    tenant_tz: "Europe/Moscow",
    from: DAY,
    to: DAY,
    days: [
      {
        date: DAY,
        is_off_day: false,
        working_hours: { start: "10:00", end: "19:00" },
        bookings: bookings.map((b) => ({
          booking_id: b.id,
          visit_at: `${DAY}T13:00:00+03:00`,
          duration_min: 60,
          service_name: "Маникюр",
          client_first_name: b.first,
          client_last_initial: b.initial,
          is_in_progress: false,
          is_returning_customer: false,
        })),
        blocks: [],
        free_windows: [],
        conflicts: [],
      },
    ],
  };
}

/** Неделя часами сервера: по дню на дату, «Вс» — выходной. */
function weekFrame(monday: string, allOff = false) {
  const dates = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(`${monday}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() + i);
    return d.toISOString().slice(0, 10);
  });
  return {
    tenant_tz: "Europe/Moscow",
    from: dates[0]!,
    to: dates[6]!,
    days: dates.map((date, i) => ({
      date,
      is_off_day: allOff || i === 6,
      working_hours: allOff || i === 6 ? null : { start: "10:00", end: "19:00" },
      bookings: [],
      blocks: [],
      free_windows: [],
      conflicts: [],
    })),
  };
}

function offsOn(rows: Array<{ id: string; from: string; to: string; reason: string }>): MasterExceptions {
  return exceptions({
    time_off: {
      state: "parsed",
      rows: rows.map((r) => ({
        id: r.id,
        start_at: `${DAY}T${r.from}:00+03:00`,
        end_at: `${DAY}T${r.to}:00+03:00`,
        reason: r.reason,
      })),
      seen_fields: [],
    },
  });
}

function impact(rows: Array<{ id: string; from: string; to: string }> = []): ScheduleImpact {
  return {
    start_at: "",
    end_at: "",
    timezone: "Europe/Moscow",
    bookings: {
      state: rows.length ? "parsed" : "none",
      rows: rows.map((r) => ({
        appointment_id: r.id,
        start_local: `${DAY}T${r.from}:00+03:00`,
        end_local: `${DAY}T${r.to}:00+03:00`,
        service_name: "Маникюр",
        status: "confirmed",
        payment_status: null,
        refund_percent_if_cancelled: null,
      })),
      seen_fields: [],
      unreadable_rows: 0,
    },
    writable: true,
    next_step: "pro_app",
  };
}

function Where() {
  const location = useLocation();
  return (
    <output data-testid="where">
      {`${location.pathname}${location.search}|${JSON.stringify(location.state ?? null)}`}
    </output>
  );
}

function renderAt(entry: string | { pathname: string; state?: unknown }) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/admin/working-time" element={<SalonPilotWorkingTimeScreen />} />
        <Route path="/admin/working-time/:masterId" element={<SalonPilotWorkingTimeWeekScreen />} />
        <Route path="/admin/working-time/:masterId/:date" element={<SalonPilotWorkingTimeDayScreen />} />
        <Route path="*" element={<p>другой экран</p>} />
      </Routes>
      <Where />
    </MemoryRouter>,
  );
}

const where = () => screen.getByTestId("where").textContent ?? "";

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  clock.startedAt = startedAgo();
  mocked.detail.mockResolvedValue(DETAIL);
  mocked.schedule.mockResolvedValue(schedule());
  mocked.exceptions.mockResolvedValue(exceptions());
  mocked.day.mockResolvedValue(dayFrame());
  mocked.impact.mockResolvedValue(impact());
});

afterEach(() => {
  window.localStorage.clear();
});

describe("кадр 1 — список мастеров", () => {
  it("мастер со специализацией; пришедший из «Расписания» отмечен; вкладка — «Расписание»", async () => {
    mocked.listMasters.mockResolvedValue({
      items: [
        { id: "m-1", name: "Мастер Один", specialization: "Парикмахер", photo_url: "", is_active: true, invite_status: "accepted", last_seen_at: null, services_count: 1 },
        { id: "m-2", name: "Мастер Два", specialization: "Косметолог", photo_url: "", is_active: true, invite_status: "accepted", last_seen_at: null, services_count: 1 },
      ],
      next_cursor: null,
      total_count: 2,
    });
    renderAt({ pathname: "/admin/working-time", state: { masterId: "m-2" } });

    const second = await screen.findByRole("button", { name: /Мастер Два/ });
    expect(second).toHaveAttribute("aria-current", "true");
    expect(screen.getByText("Косметолог")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Мастер Один/ })).not.toHaveAttribute("aria-current");
    const bar = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(within(bar).getByRole("button", { name: "Расписание" })).toHaveAttribute("aria-current", "page");

    fireEvent.click(second);
    expect(where()).toMatch(/^\/admin\/working-time\/m-2\|/);
  });

  it("пустой салон — «Нет мастеров», и кнопка ведёт добавить мастера", async () => {
    mocked.listMasters.mockResolvedValue({ items: [], next_cursor: null, total_count: 0 });
    renderAt("/admin/working-time");

    expect(await screen.findByRole("heading", { name: "Нет мастеров" })).toBeInTheDocument();
    // Кадр 10: искать не в чем — карточка одна.
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Добавить мастера" }));
    expect(where()).toMatch(/^\/admin\/team\/add\|/);
  });

  it("поиск уходит на сервер", async () => {
    mocked.listMasters.mockResolvedValue({
      items: [
        { id: "m-1", name: "Мастер Один", specialization: "", photo_url: "", is_active: true, invite_status: "accepted", last_seen_at: null, services_count: 1 },
      ],
      next_cursor: null,
      total_count: 1,
    });
    renderAt("/admin/working-time");
    await screen.findByRole("button", { name: /Мастер Один/ });

    fireEvent.change(screen.getByRole("searchbox", { name: "Поиск мастера" }), { target: { value: "Од" } });

    await waitFor(() =>
      expect(mocked.listMasters).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: "Од", is_active: true }),
        expect.anything(),
      ),
    );
  });
});

describe("кадр 2 — неделя мастера", () => {
  beforeEach(() => {
    mocked.day.mockImplementation(async (_id, params) => weekFrame(params?.from ?? "2031-01-06"));
  });

  it("даты недели часами сервера, «Выходной», пояснение про обычную неделю, переход к дате", async () => {
    renderAt("/admin/working-time/m-1?week=2031-01-06");

    expect(await screen.findByText("6 – 12 янв. 2031")).toBeInTheDocument();
    expect(mocked.day).toHaveBeenCalledWith("m-1", { from: "2031-01-06", to: "2031-01-12" }, expect.anything());
    expect(screen.getByText(/Обычная неделя задаётся в каталоге салона/)).toBeInTheDocument();
    const week = screen.getByRole("list", { name: "6 – 12 янв. 2031" });
    expect(within(week).getAllByRole("button")).toHaveLength(7);
    expect(within(week).getAllByText("10:00 – 19:00")).toHaveLength(6);
    expect(within(week).getByText("Выходной")).toBeInTheDocument();

    fireEvent.click(within(week).getAllByRole("button")[0]!);
    expect(where()).toMatch(/^\/admin\/working-time\/m-1\/2031-01-06\|/);
  });

  it("стрелка — следующая неделя", async () => {
    renderAt("/admin/working-time/m-1?week=2031-01-06");
    await screen.findByText("6 – 12 янв. 2031");

    fireEvent.click(screen.getByRole("button", { name: "Следующая неделя" }));

    expect(await screen.findByText("13 – 19 янв. 2031")).toBeInTheDocument();
  });

  it("«График не настроен»; кнопка открывает правку отдельных дат", async () => {
    mocked.day.mockImplementation(async (_id, params) => weekFrame(params?.from ?? "2031-01-06", true));
    mocked.schedule.mockResolvedValue(
      schedule({
        has_working_day: false,
        days: Array.from({ length: 7 }, (_, i) => ({
          day_of_week: i,
          is_working_day: false,
          start_time: null,
          end_time: null,
          break_start: null,
          break_end: null,
        })),
      }),
    );
    renderAt("/admin/working-time/m-1?week=2031-01-06");

    expect(await screen.findByRole("heading", { name: "График не настроен" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Настроить график" }));

    expect(screen.getByText(/Здесь меняются отдельные даты/)).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "6 – 12 янв. 2031" })).getAllByText("Выходной")).toHaveLength(7);
  });

  it("список не разобран — сказано, что показано не всё", async () => {
    mocked.exceptions.mockResolvedValue(exceptions({ unreadable_lists: ["time_off"] }));
    renderAt("/admin/working-time/m-1?week=2031-01-06");

    expect(await screen.findByText(/Показано не всё/)).toBeInTheDocument();
  });
});

describe("кадры 3–9 — одна дата", () => {
  async function openDay(path = DAY_PATH) {
    renderAt(path);
    await screen.findByRole("switch", { name: "Рабочий день" });
  }

  it("изменение помечено «только на эту дату»; сокращение без записей пишется и подтверждается", async () => {
    mocked.exceptions
      .mockResolvedValueOnce(exceptions())
      .mockResolvedValueOnce(exceptions())
      .mockResolvedValue(exceptionOn(true, "10:00", "18:00"));
    mocked.put.mockResolvedValue({
      id: "exc-1",
      date: DAY,
      is_working_day: true,
      start_time: "10:00",
      end_time: "18:00",
      break_start: null,
      break_end: null,
      note: "",
    });
    await openDay();
    expect(screen.getByRole("heading", { name: "Пятница, 10 января" })).toBeInTheDocument();
    expect(screen.getByText("Изменение только на эту дату")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "18:00" } });
    expect(screen.getByText("8 ч 0 мин")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(mocked.impact).toHaveBeenCalledWith("m-1", { date: DAY, from: "18:00", to: "19:00" });
    expect(mocked.put).toHaveBeenCalledWith("m-1", {
      date: DAY,
      is_working_day: true,
      start_time: "10:00",
      end_time: "18:00",
    });
  });

  it("конец рабочего дня — не «24:00»: его не сохранить", async () => {
    await openDay();
    const end = screen.getByLabelText("Окончание");

    // Присутствие впереди отсутствия: варианты на месте.
    expect(within(end).getByRole("option", { name: "23:30" })).toBeInTheDocument();
    expect(within(end).queryByRole("option", { name: "24:00" })).not.toBeInTheDocument();
  });

  it("выходной поверх записи — кадр 7: запись видна по имени, на сервер ничего не ушло", async () => {
    mocked.impact.mockResolvedValue(impact([{ id: "a-1", from: "13:00", to: "14:00" }]));
    mocked.day.mockResolvedValue(dayFrame([{ id: "a-1", first: "Анна", initial: "К." }]));
    await openDay();

    fireEvent.click(screen.getByRole("switch", { name: "Рабочий день" }));
    expect(screen.getByRole("heading", { name: "Выходной день" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("heading", { name: "Конфликт с записями" })).toBeInTheDocument();
    expect(screen.getByText("Анна К.")).toBeInTheDocument();
    expect(mocked.impact).toHaveBeenCalledWith("m-1", { date: DAY, from: "00:00", to: "24:00" });
    await settleScenario();
    expectNoWrites();

    fireEvent.click(screen.getByRole("button", { name: "Перейти к записям" }));
    expect(where()).toBe(`/admin/bookings/a-1|${JSON.stringify({ from: DAY_PATH })}`);
  });

  it("день вернули к шаблону — изменение снимается, нового не пишется", async () => {
    mocked.exceptions
      .mockResolvedValueOnce(exceptionOn(true, "12:00", "16:00"))
      .mockResolvedValueOnce(exceptionOn(true, "12:00", "16:00"))
      .mockResolvedValue(exceptions());
    mocked.remove.mockResolvedValue(undefined);
    await openDay();

    fireEvent.change(screen.getByLabelText("Начало"), { target: { value: "10:00" } });
    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "19:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(mocked.remove).toHaveBeenCalledWith("m-1", DAY);
    await settleScenario();
    expect(mocked.put).not.toHaveBeenCalled();
  });

  it("ничего не менял — «сохранять нечего», ни одной записи", async () => {
    // Изменение на дату, равное шаблону: без правки часов оно не снимается.
    mocked.exceptions.mockResolvedValue(exceptionOn(true, "10:00", "19:00"));
    await openDay();

    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText(/сохранять нечего/)).toBeInTheDocument();
    await settleScenario();
    expectNoWrites();
  });

  it("недоступность из шторки уходит датой и часами салона; после успеха повтор ничего не пишет", async () => {
    const saved = offsOn([{ id: "off-1", from: "13:00", to: "15:00", reason: "Личные дела" }]);
    mocked.exceptions
      .mockResolvedValueOnce(exceptions())
      .mockResolvedValueOnce(exceptions())
      .mockResolvedValue(saved);
    mocked.createOff.mockResolvedValue({
      row: { id: "off-1", start_at: `${DAY}T13:00:00+03:00`, end_at: `${DAY}T15:00:00+03:00`, reason: "Личные дела" },
      existing: false,
    });
    await openDay();

    fireEvent.click(screen.getByRole("button", { name: "Добавить недоступность" }));
    const sheet = await screen.findByRole("dialog");
    fireEvent.change(within(sheet).getByLabelText("С"), { target: { value: "13:00" } });
    fireEvent.change(within(sheet).getByLabelText("До"), { target: { value: "15:00" } });
    fireEvent.change(within(sheet).getByLabelText("Причина"), { target: { value: "Личные дела" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Добавить" }));
    expect(screen.getByText("13:00 – 15:00")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(mocked.impact).toHaveBeenCalledWith("m-1", { date: DAY, from: "13:00", to: "15:00" });
    expect(mocked.createOff).toHaveBeenCalledWith("m-1", {
      date: DAY,
      from: "13:00",
      to: "15:00",
      reason: "Личные дела",
    });
    expect(window.localStorage.getItem(`max:wt-draft:m-1:${DAY}`)).toBeNull();

    // «Рабочие часы ›» — назад к правке: черновик начат заново, повтор пуст.
    fireEvent.click(screen.getByRole("button", { name: /Рабочие часы/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText(/сохранять нечего/)).toBeInTheDocument();
    await settleScenario();
    expect(mocked.createOff).toHaveBeenCalledTimes(1);
    expect(mocked.removeOff).not.toHaveBeenCalled();
  });

  it("правка одной причины — новая строка и снятие ровно старой", async () => {
    mocked.exceptions
      .mockResolvedValueOnce(offsOn([{ id: "off-1", from: "13:00", to: "15:00", reason: "Личные дела" }]))
      .mockResolvedValueOnce(offsOn([{ id: "off-1", from: "13:00", to: "15:00", reason: "Личные дела" }]))
      .mockResolvedValue(offsOn([{ id: "off-2", from: "13:00", to: "15:00", reason: "Врач" }]));
    mocked.createOff.mockResolvedValue({
      row: { id: "off-2", start_at: `${DAY}T13:00:00+03:00`, end_at: `${DAY}T15:00:00+03:00`, reason: "Врач" },
      existing: false,
    });
    mocked.removeOff.mockResolvedValue(undefined);
    await openDay();

    fireEvent.click(screen.getByRole("button", { name: "Изменить: 13:00 – 15:00" }));
    const sheet = await screen.findByRole("dialog");
    fireEvent.change(within(sheet).getByLabelText("Причина"), { target: { value: "Врач" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Сохранить" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("heading", { name: "График обновлён" })).toBeInTheDocument();
    expect(mocked.createOff).toHaveBeenCalledWith("m-1", { date: DAY, from: "13:00", to: "15:00", reason: "Врач" });
    expect(mocked.removeOff).toHaveBeenCalledTimes(1);
    expect(mocked.removeOff).toHaveBeenCalledWith("m-1", "off-1");
  });

  it("без причины недоступность не добавляется", async () => {
    await openDay();
    fireEvent.click(screen.getByRole("button", { name: "Добавить недоступность" }));
    const sheet = await screen.findByRole("dialog");
    fireEvent.change(within(sheet).getByLabelText("С"), { target: { value: "13:00" } });
    fireEvent.change(within(sheet).getByLabelText("До"), { target: { value: "15:00" } });

    fireEvent.click(within(sheet).getByRole("button", { name: "Добавить" }));

    expect(within(sheet).getByRole("alert")).toHaveTextContent("Укажите причину.");
  });

  it("ответ потерян — «Проверяем результат», затем проверка сама показывает итог", async () => {
    mocked.exceptions
      .mockResolvedValueOnce(exceptions())
      .mockResolvedValueOnce(exceptions())
      .mockResolvedValue(exceptionOn(true, "10:00", "18:00"));
    mocked.put.mockRejectedValue(new ApiError(503, "salon_write_outcome_unknown", ""));
    await openDay();

    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "18:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("heading", { name: "Проверяем результат" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "График обновлён" }, { timeout: 4000 })).toBeInTheDocument();
    expect(mocked.put).toHaveBeenCalledTimes(1);
  });

  it("окно записи закрыто — просит открыть заново, черновик остаётся на устройстве", async () => {
    clock.startedAt = startedAgo(20 * 60);
    await openDay();

    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "18:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("heading", { name: "Откройте приложение заново" })).toBeInTheDocument();
    expect(window.localStorage.getItem(`max:wt-draft:m-1:${DAY}`)).toContain("18:00");
    await settleScenario();
    expect(mocked.impact).not.toHaveBeenCalled();
    expectNoWrites();
  });

  it("дату изменили, пока правили, — чужая недоступность видна и не снимается", async () => {
    const foreign = offsOn([{ id: "z", from: "16:00", to: "17:00", reason: "Больничный" }]);
    mocked.exceptions.mockResolvedValueOnce(exceptions()).mockResolvedValue(foreign);
    mocked.put.mockResolvedValue({} as never);
    await openDay();

    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "18:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText("Расписание могло измениться")).toBeInTheDocument();
    expect(screen.getByText("16:00 – 17:00")).toBeInTheDocument();
    await settleScenario();
    expectNoWrites();

    // Второе нажатие — по живому снимку: пишутся только свои намерения.
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(mocked.put).toHaveBeenCalledTimes(1));
    await settleScenario();
    expect(mocked.removeOff).not.toHaveBeenCalled();
  });

  it("черновик с другого снимка — перенесён на живое, сказано об этом, чужое не снимается", async () => {
    window.localStorage.setItem(
      `max:wt-draft:m-1:${DAY}`,
      JSON.stringify({
        savedAt: Date.now() - 30 * 60 * 1000,
        draft: {
          basis: "old",
          basisLayer: { working: true, start: "10:00", end: "19:00" },
          hoursTouched: true,
          working: true,
          start: "10:00",
          end: "18:00",
          added: [],
          removedIds: [],
        },
      }),
    );
    mocked.exceptions.mockResolvedValue(offsOn([{ id: "z", from: "16:00", to: "17:00", reason: "Больничный" }]));
    mocked.put.mockResolvedValue({} as never);
    await openDay();

    expect(screen.getByText(/которые вы не сохранили, восстановлены/)).toBeInTheDocument();
    expect(screen.getByText("Расписание могло измениться")).toBeInTheDocument();
    expect(screen.getByText("16:00 – 17:00")).toBeInTheDocument();
    expect(screen.getByLabelText("Окончание")).toHaveValue("18:00");

    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(mocked.put).toHaveBeenCalledTimes(1));
    await settleScenario();
    expect(mocked.removeOff).not.toHaveBeenCalled();
  });

  it("черновик прежнего формата (без часов снимка) выбрасывается, а не применяется", async () => {
    window.localStorage.setItem(
      `max:wt-draft:m-1:${DAY}`,
      JSON.stringify({
        savedAt: Date.now(),
        draft: { basis: "old", hoursTouched: true, working: true, start: "10:00", end: "18:00", added: [], removedIds: [] },
      }),
    );
    await openDay();

    expect(screen.getByLabelText("Окончание")).toHaveValue("19:00");
    expect(screen.queryByText(/которые вы не сохранили, восстановлены/)).toBeNull();
    expect(window.localStorage.getItem(`max:wt-draft:m-1:${DAY}`)).toBeNull();
  });

  it("«Сбросить» возвращает живое и выбрасывает черновик", async () => {
    window.localStorage.setItem(
      `max:wt-draft:m-1:${DAY}`,
      JSON.stringify({
        savedAt: Date.now(),
        draft: {
          basis: "old",
          basisLayer: { working: true, start: "10:00", end: "19:00" },
          hoursTouched: true,
          working: true,
          start: "10:00",
          end: "18:00",
          added: [],
          removedIds: [],
        },
      }),
    );
    await openDay();
    expect(screen.getByLabelText("Окончание")).toHaveValue("18:00");

    fireEvent.click(screen.getByRole("button", { name: "Сбросить" }));

    expect(screen.getByLabelText("Окончание")).toHaveValue("19:00");
    expect(window.localStorage.getItem(`max:wt-draft:m-1:${DAY}`)).toBeNull();
  });

  it("«назад» посреди записи не уводит с экрана", async () => {
    mocked.impact.mockReturnValue(new Promise(() => undefined));
    await openDay();
    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "18:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(mocked.impact).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Назад" }));

    expect(where()).toMatch(new RegExp(`^${DAY_PATH}\\|`));
  });

  it("предпросмотр не прочитан — отказ, записи нет", async () => {
    mocked.impact.mockRejectedValue(new ApiError(503, "schedule_unavailable", ""));
    await openDay();

    fireEvent.change(screen.getByLabelText("Окончание"), { target: { value: "18:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText(/Не удалось проверить записи на это время/)).toBeInTheDocument();
    await settleScenario();
    expectNoWrites();
  });

  it("список даты не разобран — изменять нельзя, и сказано почему", async () => {
    mocked.exceptions.mockResolvedValue(exceptions({ unreadable_lists: ["time_off"] }));
    await openDay();

    expect(screen.getByText(/не удалось разобрать — изменять её пока нельзя/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  });

  it("прошедшая дата — только чтение", async () => {
    renderAt("/admin/working-time/m-1/2020-01-10");

    expect(await screen.findByText("Прошедшую дату изменить нельзя.")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Рабочий день" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  });

  it("такой даты нет — неделя мастера, а не вечная ошибка", async () => {
    mocked.day.mockImplementation(async (_id, params) => weekFrame(params?.from ?? "2031-01-06"));
    renderAt("/admin/working-time/m-1/2026-13-45");

    await waitFor(() => expect(where()).toMatch(/^\/admin\/working-time\/m-1(\?|\|)/));
  });
});
