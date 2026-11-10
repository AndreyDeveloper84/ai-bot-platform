/**
 * DRF-2933 — «Расписание» салона v2 по макету DRF-2854 FROZEN v1.0.
 *
 * Держится то, что обещает замороженный контракт:
 *
 * * раскладка — числом показанных мастеров: сетка при двух–четырёх, лента по
 *   времени при большем числе, день списком при одном (MUST FIX 1 и 6);
 * * экран ничего не пишет: запись ведёт в карточку, свободное окно и «+» —
 *   в ручную запись с контекстом, блок — в «Рабочее время» (MUST FIX 2–5);
 * * свободное окно — только строка сервера; на прошедшей дате, в день
 *   закрытого салона, без сети и после пяти минут оно не действие (MUST FIX 7);
 * * день читается из двух ответов, и «смены не загрузились» — не «все
 *   свободны».
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/max-sdk")>();
  return { ...original, signalReady: () => undefined };
});

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    getSalonDay: vi.fn(),
    getSalonDayFrame: vi.fn(),
    listMasters: vi.fn(),
    getCatalogServicesForAdmin: vi.fn(),
    getMasterDaySchedule: vi.fn(),
    getMasterSchedule: vi.fn(),
    getMasterDetail: vi.fn(),
    // Двери записи: «Расписание» не зовёт ни одну.
    createSalonBooking: vi.fn(),
    putDateException: vi.fn(),
    deleteDateException: vi.fn(),
    createTimeOff: vi.fn(),
    deleteTimeOff: vi.fn(),
  };
});

import {
  createSalonBooking,
  createTimeOff,
  deleteDateException,
  deleteTimeOff,
  getCatalogServicesForAdmin,
  getMasterDaySchedule,
  getMasterDetail,
  getMasterSchedule,
  getSalonDay,
  getSalonDayFrame,
  listMasters,
  putDateException,
  type MasterDay,
  type MasterDetail,
  type MasterSchedule,
  type MeResponse,
  type SalonDayFrame,
  type SalonDayResponse,
  type SalonDayVisit,
  type SalonFrameMaster,
} from "../../lib/admin-api";
import { ApiError } from "../../lib/api";
import { settleScenario } from "../../test/settleScenario";
import { SalonPilotScheduleMasterScreen } from "./SalonPilotScheduleMasterScreen";
import { SalonPilotScheduleScreen } from "./SalonPilotScheduleScreen";

const mocked = {
  day: vi.mocked(getSalonDay),
  frame: vi.mocked(getSalonDayFrame),
  roster: vi.mocked(listMasters),
  services: vi.mocked(getCatalogServicesForAdmin),
  masterDay: vi.mocked(getMasterDaySchedule),
  weekly: vi.mocked(getMasterSchedule),
  detail: vi.mocked(getMasterDetail),
};

const WRITES = [createSalonBooking, putDateException, deleteDateException, createTimeOff, deleteTimeOff].map((fn) =>
  vi.mocked(fn),
);

/** Ни одной записи на сервер — «Расписание» только читает. */
function expectNoWrites() {
  for (const write of WRITES) expect(write).not.toHaveBeenCalled();
}

/** Понедельник далёкого года: дата будущая — свободные окна показываются. */
const DAY = "2034-08-21";
const FILTERS_KEY = "max:salon-schedule:filters:t-1";

const ME: MeResponse = {
  user: { id: "u-1", name: "Администратор", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Салон", slug: "salon" },
  role: "owner",
  capabilities: [],
  is_customer: false,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: true,
  master_id: null,
  landing_path: "/admin/today",
};

const NAMES = ["Мастер Один", "Мастер Два", "Мастер Три", "Мастер Четыре", "Мастер Пять", "Мастер Шесть"];

function visit(
  id: string,
  from: string,
  to: string,
  patch: Partial<SalonDayVisit> = {},
  date: string = DAY,
): SalonDayVisit {
  return {
    id,
    service_id: "svc-1",
    start_at: `${date}T${from}:00+03:00`,
    end_at: `${date}T${to}:00+03:00`,
    duration_min: 60,
    status: "confirmed",
    service_name: "Маникюр",
    client_first_name: "Клиент",
    client_last_initial: "А",
    is_in_progress: false,
    ...patch,
  };
}

/** День салона: `count` мастеров; у первого две записи, у второго одна. */
function salonDay(count = 4, date: string = DAY, patch: Partial<SalonDayResponse> = {}): SalonDayResponse {
  const visits: Record<number, SalonDayVisit[]> = {
    0: [visit("v-1", "09:00", "10:00", {}, date), visit("v-2", "10:30", "11:30", { service_id: "svc-2", service_name: "Стрижка" }, date)],
    1: [visit("v-3", "10:00", "11:00", { client_last_initial: "Б" }, date)],
  };
  return {
    date,
    timezone: "Europe/Moscow",
    summary: { total: 3, upcoming: 3, completed: 0, released: 0 },
    masters: NAMES.slice(0, count).map((name, i) => ({
      master_id: `m-${i + 1}`,
      name,
      is_active: true,
      visits: visits[i] ?? [],
    })),
    orphan_visits: [],
    ...patch,
  };
}

const NONE = { state: "none" as const, rows: [], seen_fields: [] };
const rows = (...pairs: Array<[string, string]>) => ({
  state: "parsed" as const,
  rows: pairs.map(([start, end]) => ({ start, end })),
  seen_fields: [],
});

function frameRow(index: number, patch: Partial<SalonFrameMaster> = {}): SalonFrameMaster {
  return {
    specialist_id: `cat-${index}`,
    master_id: `m-${index}`,
    display_name: NAMES[index - 1] ?? "",
    is_working_day: true,
    schedule_note: null,
    schedule_source: null,
    working_intervals: rows(["09:00", "16:00"]),
    breaks: NONE,
    absences: NONE,
    ...patch,
  };
}

/**
 * Кадр: первый мастер с блоком 14–16, третий — выходной, четвёртого в кадре
 * нет (часы неизвестны), остальные работают 09–16.
 */
function salonFrame(count = 4, date: string = DAY, patch: Partial<SalonDayFrame> = {}): SalonDayFrame {
  const masters: SalonFrameMaster[] = [];
  for (let i = 1; i <= count; i += 1) {
    if (i === 4) continue;
    masters.push(
      frameRow(i, {
        ...(i === 1 ? { absences: rows(["14:00", "16:00"]) } : {}),
        ...(i === 3 ? { is_working_day: false, working_intervals: NONE } : {}),
      }),
    );
  }
  return { date, source: "ayla", closures: NONE, masters, unreadable_lists: [], ...patch };
}

function masterDay(patch: Partial<MasterDay> = {}): MasterDay {
  return {
    date: DAY,
    is_off_day: false,
    working_hours: { start: "09:00", end: "16:00" },
    bookings: [],
    blocks: [
      { exception_id: "x-1", start: `${DAY}T14:00:00+03:00`, end: `${DAY}T15:00:00+03:00`, reason: "sick", approved: true },
    ],
    free_windows: [{ start: "12:00", end: "13:00", duration_min: 60 }],
    conflicts: [],
    ...patch,
  };
}

function weekly(confirmed = false): MasterSchedule {
  return {
    source: "ayla",
    has_working_day: true,
    days: [],
    confirmation: confirmed
      ? { confirmed_at: "2034-08-01T10:00:00+03:00", confirmed_by: { id: "u-1", name: "Владелец" }, is_current: true, fingerprint: "f", block: null }
      : { confirmed_at: null, confirmed_by: null, is_current: false, fingerprint: "f", block: null },
  };
}

const SERVICES = [
  { id: "svc-1", name: "Маникюр", duration_min: 60 },
  { id: "svc-2", name: "Стрижка", duration_min: 30 },
];

const DETAIL = {
  id: "m-1",
  name: "Мастер Один",
  specialization: "Парикмахер",
  bio: "Стригу с 2015 года.",
  experience: "10 лет",
  photo_url: "",
  is_active: true,
  services: [{ id: "svc-2", name: "Стрижка" }],
} as unknown as MasterDetail;

function Where() {
  const location = useLocation();
  return (
    <output data-testid="where">
      {`${location.pathname}${location.search}|${JSON.stringify(location.state ?? null)}`}
    </output>
  );
}
const where = () => screen.getByTestId("where").textContent ?? "";

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/admin/schedule" element={<SalonPilotScheduleScreen me={ME} />} />
        <Route path="/admin/schedule/master/:masterId" element={<SalonPilotScheduleMasterScreen me={ME} />} />
        <Route path="*" element={<p>другой экран</p>} />
      </Routes>
      <Where />
    </MemoryRouter>,
  );
}

/** Сколько мастеров отдают день и кадр по умолчанию. */
let team = 4;

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  team = 4;
  mocked.day.mockImplementation(async (date) => salonDay(team, date ?? DAY));
  mocked.frame.mockImplementation(async (date) => salonFrame(team, date ?? DAY));
  mocked.roster.mockImplementation(async () => ({
    items: NAMES.map((name, i) => ({
      id: `m-${i + 1}`,
      name,
      specialization: "Парикмахер",
      photo_url: "",
      is_active: true,
      invite_status: "accepted",
      last_seen_at: null,
      services_count: 1,
    })),
    next_cursor: null,
    total_count: NAMES.length,
  }));
  mocked.services.mockImplementation(async () => SERVICES);
  mocked.masterDay.mockImplementation(async (_id, params) => ({
    tenant_tz: "Europe/Moscow",
    from: params?.from ?? DAY,
    to: params?.to ?? DAY,
    days: [masterDay({ date: params?.from ?? DAY })],
  }));
  mocked.weekly.mockImplementation(async () => weekly());
  mocked.detail.mockImplementation(async () => DETAIL);
});

afterEach(() => {
  window.sessionStorage.clear();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

/** «Сейчас» салона (Москва) — подменой одних часов; таймеры остаются настоящими. */
function salonClockIs(isoMoscow: string) {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(`${isoMoscow}:00+03:00`));
}

function masterDayWith(patch: Partial<MasterDay>) {
  mocked.masterDay.mockImplementation(async (_id, params) => ({
    tenant_tz: "Europe/Moscow",
    from: params?.from ?? DAY,
    to: params?.to ?? DAY,
    days: [masterDay({ date: params?.from ?? DAY, ...patch })],
  }));
}

function offline() {
  Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => false });
  return () => {
    delete (window.navigator as { onLine?: boolean }).onLine;
  };
}

async function openGrid(entry = `/admin/schedule?date=${DAY}`) {
  renderAt(entry);
  return screen.findByRole("region", { name: "Расписание по мастерам" });
}

/** Нажатие в колонку на высоте `fraction` от её начала — как пальцем по времени. */
function tapColumn(master: string, fraction: number) {
  const tap = screen.getByRole("button", { name: `Свободные окна: ${master}` });
  vi.spyOn(tap, "getBoundingClientRect").mockReturnValue({
    top: 0,
    left: 0,
    width: 80,
    height: 700,
    bottom: 700,
    right: 80,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  fireEvent.click(tap, { clientY: 700 * fraction, detail: 1 });
}

function selectOnly(masterIds: string[], serviceIds: string[] = []) {
  window.sessionStorage.setItem(FILTERS_KEY, JSON.stringify({ masterIds, serviceIds }));
}

describe("кадр 1 — сетка: от двух до четырёх мастеров", () => {
  it("дата над заголовком, колонки мастеров, записи, блок, выходной и неизвестные часы", async () => {
    const grid = await openGrid();

    expect(screen.getByText("Пн, 21 августа")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Расписание" })).toBeInTheDocument();
    for (const name of NAMES.slice(0, 4)) {
      expect(within(grid).getByRole("button", { name: `Расписание мастера: ${name}` })).toBeInTheDocument();
    }
    expect(
      within(grid).getByRole("button", { name: "Запись 09:00–10:00, Клиент А., Маникюр, мастер Мастер Один" }),
    ).toBeInTheDocument();
    expect(
      within(grid).getByRole("button", { name: /^Блок 14:00–16:00, Мастер Один/ }),
    ).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Мастер Три" })).getByText("Выходной")).toBeInTheDocument();
    // Строки мастера в кадре нет — «неизвестно», а не «свободен» и не «выходной».
    expect(within(screen.getByRole("group", { name: "Мастер Четыре" })).getByText("Часы неизвестны")).toBeInTheDocument();
    expect(screen.getByText(/^Актуально на \d\d:\d\d$/)).toBeInTheDocument();
  });

  it("запись ведёт в карточку и уносит дату для возврата", async () => {
    const grid = await openGrid();

    fireEvent.click(within(grid).getByRole("button", { name: /^Запись 10:30–11:30/ }));

    expect(where()).toBe(`/admin/bookings/v-2|${JSON.stringify({ from: `/admin/schedule?date=${DAY}` })}`);
  });

  it("мастер ведёт в его день", async () => {
    const grid = await openGrid();
    fireEvent.click(within(grid).getByRole("button", { name: "Расписание мастера: Мастер Два" }));
    expect(where()).toBe(`/admin/schedule/master/m-2?date=${DAY}|null`);
  });

  it("блок ведёт в «Рабочее время» этой даты, а не правится здесь", async () => {
    const grid = await openGrid();
    fireEvent.click(within(grid).getByRole("button", { name: /^Блок 14:00–16:00, Мастер Один/ }));
    expect(where()).toBe(`/admin/working-time/m-1/${DAY}|null`);
    expectNoWrites();
  });

  it("«сейчас по плану» стоит у записи, чей интервал накрывает сейчас, и только у неё", async () => {
    mocked.day.mockImplementation(async () => {
      const day = salonDay();
      day.masters[0]!.visits[0]!.is_in_progress = true;
      return day;
    });
    const grid = await openGrid();

    const now = within(grid).getByRole("button", { name: /^Запись 09:00–10:00.*сейчас по плану$/ });
    expect(now).toHaveClass("ss-block--now");
    expect(within(grid).getByRole("button", { name: /^Запись 10:30–11:30/ })).not.toHaveClass("ss-block--now");
  });

  it("освобождённая запись не скрывается, а приглушена", async () => {
    mocked.day.mockImplementation(async () => {
      const day = salonDay();
      day.masters[0]!.visits[0]!.status = "cancelled";
      return day;
    });
    const grid = await openGrid();

    expect(within(grid).getByRole("button", { name: /^Запись 09:00–10:00/ })).toHaveClass("ss-block--released");
  });

  it("ничья запись в сетке не теряется: колонки для неё нет, а строка есть", async () => {
    mocked.day.mockImplementation(async () =>
      salonDay(4, DAY, { orphan_visits: [visit("v-9", "15:00", "16:00", { client_last_initial: "Я" })] }),
    );
    await openGrid();

    const orphans = screen.getByRole("region", { name: "Мастер не определён" });
    fireEvent.click(within(orphans).getByRole("button", { name: /^Запись 15:00–16:00, Клиент Я\./ }));
    expect(where()).toBe(`/admin/bookings/v-9|${JSON.stringify({ from: `/admin/schedule?date=${DAY}` })}`);
  });

  it("«+» ведёт в ручную запись с датой; меню «⋯» — один пункт, «Рабочее время»", async () => {
    await openGrid();

    fireEvent.click(screen.getByRole("button", { name: "Ещё" }));
    const items = screen.getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual(["Рабочее время"]);

    fireEvent.click(screen.getByRole("button", { name: "Новая запись" }));
    expect(where()).toBe(`/admin/booking/new?return=schedule&date=${DAY}|null`);
    expectNoWrites();
  });
});

describe("кадр 3 — свободное окно в сетке", () => {
  it("нажатие на пустое время колонки читает окна мастера и раскрывает окно под пальцем", async () => {
    await openGrid();
    await settleScenario();
    expect(mocked.masterDay).not.toHaveBeenCalled();

    // Ось 09–16: половина высоты — 12:30.
    tapColumn("Мастер Один", 0.5);

    const card = await screen.findByRole("group", { name: "Свободно 12:00–13:00" });
    expect(mocked.masterDay).toHaveBeenCalledWith("m-1", { from: DAY, to: DAY });
    expect(within(card).getByText("12:00 – 13:00")).toBeInTheDocument();
    expect(screen.getByText(/диапазон доступности, а не готовый слот/)).toBeInTheDocument();

    fireEvent.click(within(card).getByRole("button", { name: /Записать/ }));

    expect(where()).toBe(
      `/admin/booking/new?return=schedule&date=${DAY}&master_id=m-1&from=12%3A00&to=13%3A00|null`,
    );
    expectNoWrites();
  });

  it("нажатие мимо окна окно не выдумывает: окна сервера видны, карточки нет", async () => {
    await openGrid();

    // 09:30 — занято записью; свободного окна сервера там нет.
    tapColumn("Мастер Один", 0.07);

    const window1 = await screen.findByRole("button", { name: "Свободно 12:00–13:00" });
    expect(screen.queryByRole("group", { name: /^Свободно/ })).toBeNull();

    fireEvent.click(window1);
    expect(await screen.findByRole("group", { name: "Свободно 12:00–13:00" })).toBeInTheDocument();
  });

  it("с клавиатуры колонка открывает окна без выбора — они обычные кнопки", async () => {
    await openGrid();

    fireEvent.click(screen.getByRole("button", { name: "Свободные окна: Мастер Один" }));

    expect(await screen.findByRole("button", { name: "Свободно 12:00–13:00" })).toBeInTheDocument();
  });

  it("окон у мастера нет — сказано словами и по имени", async () => {
    mocked.masterDay.mockImplementation(async () => ({
      tenant_tz: "Europe/Moscow",
      from: DAY,
      to: DAY,
      days: [masterDay({ free_windows: [] })],
    }));
    await openGrid();

    tapColumn("Мастер Два", 0.5);

    expect(await screen.findByText("Мастер Два: свободных промежутков нет.")).toBeInTheDocument();
  });

  it("окна не загрузились — «не загрузились», а не «окон нет»", async () => {
    mocked.masterDay.mockImplementation(async () => {
      throw new ApiError(503, "schedule_unavailable", "");
    });
    await openGrid();

    tapColumn("Мастер Один", 0.5);

    expect(await screen.findByText(/Свободные окна не загрузились/)).toBeInTheDocument();
    expect(screen.queryByText(/свободных промежутков нет/)).toBeNull();
  });

  it("окно старше пяти минут — не действие: в запись не уходим, просим обновить", async () => {
    const clock = vi.spyOn(Date, "now");
    const loaded = 1_800_000_000_000;
    clock.mockReturnValue(loaded);
    await openGrid();
    tapColumn("Мастер Один", 0.5);
    const card = await screen.findByRole("group", { name: "Свободно 12:00–13:00" });

    clock.mockReturnValue(loaded + 5 * 60_000 + 1);
    fireEvent.click(within(card).getByRole("button", { name: /Записать/ }));

    expect(await screen.findByText("Расписание могло измениться")).toBeInTheDocument();
    expect(screen.getByText(/Свободные окна могли измениться/)).toBeInTheDocument();
    expect(where()).toBe(`/admin/schedule?date=${DAY}|null`);
    // Устаревшее окно снято с экрана, а не оставлено «на всякий случай».
    expect(screen.queryByRole("group", { name: /^Свободно/ })).toBeNull();
  });

  it("без сети окно видно, но «Записать» не нажимается", async () => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => false });
    try {
      await openGrid();
      expect(screen.getByText("Нет подключения")).toBeInTheDocument();

      tapColumn("Мастер Один", 0.5);

      const card = await screen.findByRole("group", { name: "Свободно 12:00–13:00" });
      expect(within(card).getByRole("button", { name: /Записать/ })).toBeDisabled();
    } finally {
      delete (window.navigator as { onLine?: boolean }).onLine;
    }
  });

  it("выключенному мастеру «Записать» не предлагается", async () => {
    mocked.day.mockImplementation(async () => {
      const day = salonDay();
      day.masters[0]!.is_active = false;
      return day;
    });
    await openGrid();

    tapColumn("Мастер Один", 0.5);

    const card = await screen.findByRole("group", { name: "Свободно 12:00–13:00" });
    expect(within(card).getByRole("button", { name: /Записать/ })).toBeDisabled();
  });

  it("прошедшая дата — колонки не нажимаются: сервер не знает «сейчас» и отдал бы окна в прошлом", async () => {
    await openGrid("/admin/schedule?date=2020-01-06");

    expect(screen.getByRole("button", { name: "Свободные окна: Мастер Один" })).toBeDisabled();
    await settleScenario();
    expect(mocked.masterDay).not.toHaveBeenCalled();
  });

  it("салон в этот день закрыт — сказано словами, окна не показываются", async () => {
    mocked.frame.mockImplementation(async () =>
      salonFrame(4, DAY, {
        closures: {
          state: "parsed",
          rows: [{ start: null, end: null, is_full_day: true, reason: "Санитарный день" }],
          seen_fields: [],
        },
      }),
    );
    await openGrid();

    expect(screen.getByText("В этот день салон закрыт · Санитарный день")).toBeInTheDocument();
    expect(screen.getByText("Свободные окна на этот день не показываются.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свободные окна: Мастер Один" })).toBeDisabled();
  });

  it("закрытия салона не прочитаны — окна не продаются: «открыт» сказать нельзя", async () => {
    mocked.frame.mockImplementation(async () =>
      salonFrame(4, DAY, { closures: { state: "unreadable", rows: [], seen_fields: ["x"] }, unreadable_lists: ["closures"] }),
    );
    await openGrid();

    expect(screen.getByText(/Не удалось проверить закрытия салона/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свободные окна: Мастер Один" })).toBeDisabled();
    await settleScenario();
    expect(mocked.masterDay).not.toHaveBeenCalled();
  });

  it("кадр не прочитан — закрытия неизвестны, окна не продаются", async () => {
    mocked.frame.mockImplementation(async () => {
      throw new ApiError(503, "schedule_unavailable", "");
    });
    await openGrid();

    expect(screen.getByText(/Не удалось проверить закрытия салона/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свободные окна: Мастер Один" })).toBeDisabled();
  });

  it("салон на локальных таблицах: кадра каталога у него нет по устройству — окна остаются", async () => {
    mocked.frame.mockImplementation(async () => {
      throw new ApiError(503, "frame_source_local_unsupported", "");
    });
    await openGrid();

    expect(screen.queryByText(/Не удалось проверить закрытия салона/)).toBeNull();
    tapColumn("Мастер Один", 0.5);
    expect(await screen.findByRole("button", { name: /^Свободно/ })).toBeInTheDocument();
  });

  it("окно сервера вне оси не теряется: ось расширяется до него", async () => {
    // У четвёртого мастера часы неизвестны; ось дня — 09–16, окно — вечером.
    masterDayWith({ free_windows: [{ start: "17:00", end: "21:00", duration_min: 240 }] });
    await openGrid();

    fireEvent.click(screen.getByRole("button", { name: "Свободные окна: Мастер Четыре" }));

    expect(await screen.findByRole("button", { name: "Свободно 17:00–21:00" })).toBeInTheDocument();
  });

  it("сегодня: прошедшее начало окна не показывается свободным, кончившееся окно — тоже", async () => {
    salonClockIs(`${DAY}T12:40`);
    masterDayWith({
      free_windows: [
        { start: "09:00", end: "10:00", duration_min: 60 },
        { start: "11:30", end: "16:00", duration_min: 270 },
      ],
    });
    await openGrid();

    fireEvent.click(screen.getByRole("button", { name: "Свободные окна: Мастер Один" }));

    expect(await screen.findByRole("button", { name: "Свободно 12:40–16:00" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Свободно 09:00/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Свободно 11:30/ })).toBeNull();
  });

  it("ответ на прежнюю колонку не ложится поверх открытой из кэша", async () => {
    await openGrid();
    tapColumn("Мастер Один", 0.5);
    await screen.findByRole("group", { name: "Свободно 12:00–13:00" });

    // Вторая колонка: ответ задержан.
    let release: (() => void) | undefined;
    mocked.masterDay.mockImplementationOnce(
      (_id, params) =>
        new Promise((resolve) => {
          release = () =>
            resolve({
              tenant_tz: "Europe/Moscow",
              from: params?.from ?? DAY,
              to: params?.to ?? DAY,
              days: [masterDay({ free_windows: [{ start: "09:00", end: "10:00", duration_min: 60 }] })],
            });
        }),
    );
    tapColumn("Мастер Два", 0.5);
    await waitFor(() => expect(release).toBeDefined());
    tapColumn("Мастер Один", 0.5);
    release?.();
    await settleScenario();

    const first = screen.getByRole("group", { name: "Мастер Один" });
    expect(within(first).getByRole("group", { name: "Свободно 12:00–13:00" })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Мастер Два" })).queryByRole("button", { name: /^Свободно/ })).toBeNull();
  });

  it("после «могли измениться» новое чтение окон снимает просьбу обновить", async () => {
    const clock = vi.spyOn(Date, "now");
    const loaded = 1_800_000_000_000;
    clock.mockReturnValue(loaded);
    await openGrid();
    tapColumn("Мастер Один", 0.5);
    const card = await screen.findByRole("group", { name: "Свободно 12:00–13:00" });
    clock.mockReturnValue(loaded + 5 * 60_000 + 1);
    fireEvent.click(within(card).getByRole("button", { name: /Записать/ }));
    await screen.findByText("Расписание могло измениться");

    tapColumn("Мастер Один", 0.5);

    await screen.findByRole("group", { name: "Свободно 12:00–13:00" });
    expect(screen.queryByText("Расписание могло измениться")).toBeNull();
    expect(mocked.masterDay).toHaveBeenCalledTimes(2);
  });

  it("окно, выбранное кнопкой, отдаёт фокус «Записать», а не теряет его", async () => {
    await openGrid();
    fireEvent.click(screen.getByRole("button", { name: "Свободные окна: Мастер Один" }));

    fireEvent.click(await screen.findByRole("button", { name: "Свободно 12:00–13:00" }));

    const card = await screen.findByRole("group", { name: "Свободно 12:00–13:00" });
    expect(within(card).getByRole("button", { name: /Записать/ })).toHaveFocus();
  });

  it("окна одной колонки не переживают перечитывание дня", async () => {
    await openGrid();
    tapColumn("Мастер Один", 0.5);
    await screen.findByRole("group", { name: "Свободно 12:00–13:00" });

    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));

    await waitFor(() => expect(mocked.day).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("group", { name: /^Свободно/ })).toBeNull());

    // И из памяти они тоже ушли: новое нажатие читает окна заново.
    tapColumn("Мастер Один", 0.5);
    await screen.findByRole("group", { name: "Свободно 12:00–13:00" });
    expect(mocked.masterDay).toHaveBeenCalledTimes(2);
  });
});

describe("лента по времени — больше четырёх мастеров (MUST FIX 1 и 6)", () => {
  beforeEach(() => {
    team = 6;
  });

  async function openFeed() {
    renderAt(`/admin/schedule?date=${DAY}`);
    await screen.findByRole("heading", { name: "Записи дня" });
  }

  it("колонок нет: записи стоят под временем, мастер назван в карточке", async () => {
    await openFeed();

    expect(screen.getByText(/Мастеров больше четырёх/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Расписание по мастерам" })).toBeNull();
    const at10 = screen.getByText("10:00").closest("li")!;
    expect(
      within(at10).getByRole("button", { name: "Запись 10:00–11:00, Клиент Б., Маникюр, мастер Мастер Два" }),
    ).toBeInTheDocument();
  });

  it("отсутствие мастера стоит событием дня", async () => {
    await openFeed();

    const at14 = screen.getByText("14:00").closest("li")!;
    expect(within(at14).getByText("Блок")).toBeInTheDocument();
    expect(within(at14).getByText("14:00 – 16:00")).toBeInTheDocument();
    expect(within(at14).getByText("Мастер Один")).toBeInTheDocument();
  });

  it("свободных окон в ленте нет вовсе — день мастера за ними не читается", async () => {
    await openFeed();
    expect(screen.getAllByRole("button", { name: /^Запись/ }).length).toBeGreaterThan(0);

    await settleScenario();
    expect(screen.queryByText("Свободно")).toBeNull();
    expect(mocked.masterDay).not.toHaveBeenCalled();
  });

  it("«Мастера и смены» раскрывается списком со сменами и ведёт в день мастера", async () => {
    await openFeed();

    const toggle = screen.getByRole("button", { name: "Мастера и смены · 6" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);

    expect(screen.getByRole("button", { name: "Расписание мастера: Мастер Три, Выходной" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Расписание мастера: Мастер Четыре, Часы неизвестны" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Расписание мастера: Мастер Два, 09:00–16:00" }));
    expect(where()).toBe(`/admin/schedule/master/m-2?date=${DAY}|null`);
  });

  it("ничья запись показывается, а не прячется", async () => {
    mocked.day.mockImplementation(async () =>
      salonDay(6, DAY, { orphan_visits: [visit("v-9", "15:00", "16:00", { client_last_initial: "Я" })] }),
    );
    await openFeed();

    const orphan = screen.getByRole("button", { name: /^Запись 15:00–16:00, Клиент Я\./ });
    expect(within(orphan).getByText("Мастер не определён")).toBeInTheDocument();
  });

  it("фильтр услуг сужает ленту; пустой итог назван", async () => {
    selectOnly([], ["svc-2"]);
    await openFeed();

    expect(screen.getByRole("button", { name: /^Запись 10:30–11:30.*Стрижка/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Запись 09:00–10:00/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Стрижка" })).toBeInTheDocument();
  });

  it("по выбранной услуге записей нет — сказано словами", async () => {
    mocked.services.mockImplementation(async () => [...SERVICES, { id: "svc-9", name: "Окрашивание", duration_min: 90 }]);
    selectOnly([], ["svc-9"]);
    await openFeed();

    expect(await screen.findByText("По выбранным услугам записей на этот день нет.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Запись/ })).toBeNull();
  });
});

describe("один мастер — день списком (кадр 12 на экране расписания)", () => {
  async function openSingle() {
    selectOnly(["m-1"]);
    renderAt(`/admin/schedule?date=${DAY}`);
    await screen.findByRole("button", { name: /^Свободно 12:00–13:00/ });
  }

  it("записи — из дня салона, окно и блок — из дня мастера; подтверждение графика названо", async () => {
    mocked.day.mockImplementation(async () => {
      const day = salonDay();
      day.masters[0]!.visits.push(visit("v-7", "15:00", "16:00", { status: "cancelled" }));
      return day;
    });
    await openSingle();

    expect(mocked.masterDay).toHaveBeenCalledWith("m-1", { from: DAY, to: DAY }, expect.anything());
    expect(screen.getByRole("button", { name: "Запись 09:00–10:00, Клиент А., Маникюр" })).toBeInTheDocument();
    // Отменённая есть в дне салона и пропала бы из day-schedule.
    expect(screen.getByRole("button", { name: /^Запись 15:00–16:00/ })).toHaveClass("ss-row--released");
    expect(screen.getByText("Расписание не подтверждено — салон его не заверял")).toBeInTheDocument();
    expect(screen.getByText(/диапазон доступности, а не готовый слот/)).toBeInTheDocument();
    // Причина блока («sick») человеку не показывается.
    expect(screen.getByRole("button", { name: /^Блок 14:00–15:00/ })).toBeInTheDocument();
  });

  it("окно ведёт в ручную запись с мастером, датой и окном", async () => {
    await openSingle();

    fireEvent.click(screen.getByRole("button", { name: /^Свободно 12:00–13:00/ }));

    expect(where()).toBe(
      `/admin/booking/new?return=schedule&date=${DAY}&master_id=m-1&from=12%3A00&to=13%3A00|null`,
    );
    expectNoWrites();
  });

  it("«⋯» уносит мастера в «Рабочее время» отмеченным", async () => {
    await openSingle();

    fireEvent.click(screen.getByRole("button", { name: "Ещё" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Рабочее время" }));

    expect(where()).toBe(`/admin/working-time|${JSON.stringify({ masterId: "m-1" })}`);
  });

  it("блок в дне мастера ведёт в «Рабочее время» этой даты", async () => {
    await openSingle();

    fireEvent.click(screen.getByRole("button", { name: /^Блок 14:00–15:00/ }));

    expect(where()).toBe(`/admin/working-time/m-1/${DAY}|null`);
    expectNoWrites();
  });

  it("конфликт назван по-русски по его виду и ведёт в запись", async () => {
    mocked.masterDay.mockImplementation(async () => ({
      tenant_tz: "Europe/Moscow",
      from: DAY,
      to: DAY,
      days: [
        masterDay({
          conflicts: [{ type: "double_booking", booking_id: "v-2", description: "Overlaps booking 7f3c (09:00-10:00)" }],
        }),
      ],
    }));
    await openSingle();

    const item = screen.getByRole("button", { name: /Записи пересекаются по времени/ });
    expect(screen.queryByText(/Overlaps/)).toBeNull();
    fireEvent.click(item);
    expect(where()).toBe(`/admin/bookings/v-2|${JSON.stringify({ from: `/admin/schedule?date=${DAY}` })}`);
  });

  it("непрочитанное подтверждение не выдаётся за неподтверждённое", async () => {
    mocked.weekly.mockImplementation(async () => {
      throw new ApiError(503, "schedule_unavailable", "");
    });
    await openSingle();

    expect(screen.getByText(/Состояние подтверждения не прочитано/)).toBeInTheDocument();
    expect(screen.queryByText(/Расписание не подтверждено/)).toBeNull();
  });

  it("день мастера не загрузился — записи показаны, про остальное сказано", async () => {
    mocked.masterDay.mockImplementation(async () => {
      throw new ApiError(503, "schedule_unavailable", "");
    });
    selectOnly(["m-1"]);
    renderAt(`/admin/schedule?date=${DAY}`);

    expect(await screen.findByText(/Часы, блоки и свободные окна мастера не загрузились/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Запись 09:00–10:00/ })).toBeInTheDocument();
    expect(screen.queryByText("Свободных промежутков нет.")).toBeNull();
  });

  it("без сети окно в списке видно, но не нажимается", async () => {
    const restore = offline();
    try {
      await openSingle();
      expect(screen.getByRole("button", { name: /^Свободно 12:00–13:00/ })).toBeDisabled();
    } finally {
      restore();
    }
  });

  it("после сбоя обновления окно в списке не действие", async () => {
    await openSingle();
    mocked.day.mockImplementationOnce(async () => {
      throw new ApiError(503, "schedule_unavailable", "");
    });

    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));

    await screen.findByText("Не удалось обновить. Показаны последние данные");
    expect(screen.getByRole("button", { name: /^Свободно 12:00–13:00/ })).toBeDisabled();
  });

  it("выключенному мастеру окно не предлагается ни строкой, ни «+»", async () => {
    mocked.day.mockImplementation(async () => {
      const day = salonDay();
      day.masters[0]!.is_active = false;
      return day;
    });
    await openSingle();

    expect(screen.getByRole("button", { name: /^Свободно 12:00–13:00/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Новая запись" }));
    expect(where()).toBe(`/admin/booking/new?return=schedule&date=${DAY}|null`);
  });

  it("салон закрыт — в дне мастера окон нет, и сказано почему", async () => {
    mocked.frame.mockImplementation(async () =>
      salonFrame(4, DAY, {
        closures: { state: "parsed", rows: [{ start: "10:00", end: "14:00", is_full_day: false, reason: "" }], seen_fields: [] },
      }),
    );
    selectOnly(["m-1"]);
    renderAt(`/admin/schedule?date=${DAY}`);

    expect(await screen.findByText("Свободные окна на этот день не показываются.")).toBeInTheDocument();
    expect(screen.getByText("Салон закрыт 10:00–14:00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Запись 09:00–10:00/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Свободно/ })).toBeNull();
  });

  it("закрытия не прочитаны — в дне мастера окон нет", async () => {
    mocked.frame.mockImplementation(async () => {
      throw new ApiError(503, "schedule_unavailable", "");
    });
    selectOnly(["m-1"]);
    renderAt(`/admin/schedule?date=${DAY}`);

    expect(await screen.findByText(/Не удалось проверить закрытия салона/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Свободно/ })).toBeNull();
  });

  it("сегодня: «сейчас» — по часам салона; идущее окно начинается с него", async () => {
    salonClockIs(`${DAY}T12:40`);
    masterDayWith({
      blocks: [],
      free_windows: [
        { start: "09:00", end: "10:00", duration_min: 60 },
        { start: "11:30", end: "16:00", duration_min: 270 },
      ],
    });
    selectOnly(["m-1"]);
    renderAt(`/admin/schedule?date=${DAY}`);

    fireEvent.click(await screen.findByRole("button", { name: /^Свободно 12:40–16:00/ }));

    expect(screen.queryByRole("button", { name: /^Свободно 09:00/ })).toBeNull();
    expect(where()).toBe(
      `/admin/booking/new?return=schedule&date=${DAY}&master_id=m-1&from=12%3A40&to=16%3A00|null`,
    );
  });

  it("вчера по часам салона — прошедшая дата: окон нет, «+» дату не несёт", async () => {
    // 23:30 UTC 21-го — в Москве уже 22-е: 21-е для салона прошло.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(`${DAY}T23:30:00Z`));
    selectOnly(["m-1"]);
    renderAt(`/admin/schedule?date=${DAY}`);

    expect(await screen.findByText("Расписание не подтверждено — салон его не заверял")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Свободно/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Новая запись" }));
    expect(where()).toBe("/admin/booking/new?return=schedule&master_id=m-1|null");
  });

  it("окно старше пяти минут в списке — не действие", async () => {
    const clock = vi.spyOn(Date, "now");
    const loaded = 1_800_000_000_000;
    clock.mockReturnValue(loaded);
    await openSingle();

    clock.mockReturnValue(loaded + 5 * 60_000 + 1);
    fireEvent.click(screen.getByRole("button", { name: /^Свободно 12:00–13:00/ }));

    expect(await screen.findByText("Расписание могло измениться")).toBeInTheDocument();
    expect(where()).toBe(`/admin/schedule?date=${DAY}|null`);
  });
});

describe("дата", () => {
  it("вход без даты: день называет сервер, а кадр спрашивается с ЕГО датой", async () => {
    await openGrid("/admin/schedule");

    expect(mocked.day.mock.calls[0]?.[0]).toBeUndefined();
    expect(mocked.frame.mock.calls[0]?.[0]).toBe(DAY);
    expect(screen.getByText("Пн, 21 августа")).toBeInTheDocument();
  });

  it("дата из адреса уходит в оба запроса", async () => {
    await openGrid(`/admin/schedule?date=${DAY}`);
    expect(mocked.day.mock.calls[0]?.[0]).toBe(DAY);
    expect(mocked.frame.mock.calls[0]?.[0]).toBe(DAY);
  });

  it("испорченная дата в адресе — как её отсутствие", async () => {
    await openGrid("/admin/schedule?date=2034-02-30");
    expect(mocked.day.mock.calls[0]?.[0]).toBeUndefined();
  });

  it("день недели в полосе выбирает дату и кладёт её в адрес", async () => {
    await openGrid();

    fireEvent.click(screen.getByRole("button", { name: "22 августа, Вт" }));

    await waitFor(() => expect(mocked.day.mock.calls.at(-1)?.[0]).toBe("2034-08-22"));
    expect(where()).toBe("/admin/schedule?date=2034-08-22|null");
    expect(await screen.findByText("Вт, 22 августа")).toBeInTheDocument();
  });

  it("ответ на прежнюю дату, пришедший позже, не ложится поверх новой", async () => {
    let releaseOld: (() => void) | undefined;
    mocked.day.mockImplementation((date) =>
      date === DAY
        ? new Promise((resolve) => {
            releaseOld = () => resolve(salonDay(4, DAY));
          })
        : Promise.resolve(salonDay(4, date ?? DAY)),
    );
    renderAt(`/admin/schedule?date=${DAY}`);
    await waitFor(() => expect(releaseOld).toBeDefined());

    fireEvent.click(screen.getByRole("button", { name: "22 августа, Вт" }));
    expect(await screen.findByText("Вт, 22 августа")).toBeInTheDocument();
    await screen.findByRole("region", { name: "Расписание по мастерам" });
    releaseOld?.();
    await settleScenario();

    expect(screen.getByText("Вт, 22 августа")).toBeInTheDocument();
    expect(where()).toBe("/admin/schedule?date=2034-08-22|null");
  });

  it("кадр, ответивший про другой день, отбрасывается: смены вторника под записями понедельника не рисуются", async () => {
    mocked.frame.mockImplementation(async () => salonFrame(4, "2034-08-22"));
    await openGrid();

    expect(screen.getByText(/Смены и недоступность не загрузились/)).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Мастер Один" })).getByText("Часы неизвестны")).toBeInTheDocument();
  });
});

describe("кадр 2 — шторка «Фильтры»", () => {
  async function openSheet(trigger: RegExp | string = "Фильтры") {
    fireEvent.click(screen.getByRole("button", { name: trigger }));
    return screen.findByRole("dialog", { name: "Фильтры" });
  }

  it("выбранные мастера применяются, чип говорит сколько; «Сбросить» возвращает всех", async () => {
    team = 6;
    renderAt(`/admin/schedule?date=${DAY}`);
    await screen.findByRole("heading", { name: "Записи дня" });

    let sheet = await openSheet(/Все мастера/);
    fireEvent.click(within(sheet).getByRole("checkbox", { name: /Мастер Один/ }));
    fireEvent.click(within(sheet).getByRole("checkbox", { name: /Мастер Два/ }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Применить" }));

    // Двое — уже сетка, а не лента.
    const grid = await screen.findByRole("region", { name: "Расписание по мастерам" });
    expect(within(grid).getAllByRole("button", { name: /^Расписание мастера:/ })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "2 мастера" })).toBeInTheDocument();
    expect(JSON.parse(window.sessionStorage.getItem(FILTERS_KEY) ?? "{}")).toEqual({
      masterIds: ["m-1", "m-2"],
      serviceIds: [],
    });

    sheet = await openSheet("2 мастера");
    fireEvent.click(within(sheet).getByRole("button", { name: "Сбросить" }));
    expect(within(sheet).getByRole("checkbox", { name: "Все мастера" })).toBeChecked();
    fireEvent.click(within(sheet).getByRole("button", { name: "Применить" }));

    expect(await screen.findByRole("heading", { name: "Записи дня" })).toBeInTheDocument();
    expect(window.sessionStorage.getItem(FILTERS_KEY)).toBeNull();
  });

  it("фильтр услуг в сетке приглушает чужие записи, а не убирает: занятое время не выглядит свободным", async () => {
    const grid = await openGrid();

    const sheet = await openSheet(/Все услуги/);
    fireEvent.click(within(sheet).getByRole("checkbox", { name: "Стрижка" }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Применить" }));

    expect(await screen.findByRole("button", { name: "Стрижка" })).toBeInTheDocument();
    expect(within(grid).getByRole("button", { name: /^Запись 09:00–10:00/ })).toHaveClass("ss-block--dim");
    expect(within(grid).getByRole("button", { name: /^Запись 10:30–11:30/ })).not.toHaveClass("ss-block--dim");
  });

  it("«Месяц» — способ выбрать день: дата применяется вместе с фильтрами", async () => {
    await openGrid();

    const sheet = await openSheet("Выбрать дату");
    fireEvent.click(within(sheet).getByRole("button", { name: "Месяц" }));
    expect(within(sheet).getByText("Август 2034")).toBeInTheDocument();
    fireEvent.click(within(sheet).getByRole("button", { name: "Следующий месяц" }));
    fireEvent.click(within(sheet).getByRole("button", { name: "5 сентября, Вт" }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Применить" }));

    await waitFor(() => expect(mocked.day.mock.calls.at(-1)?.[0]).toBe("2034-09-05"));
    expect(where()).toBe("/admin/schedule?date=2034-09-05|null");
  });

  it("«Неделя» листает недели, выбор — по-прежнему один день", async () => {
    await openGrid();

    const sheet = await openSheet("Выбрать дату");
    fireEvent.click(within(sheet).getByRole("button", { name: "Неделя" }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Следующая неделя" }));
    expect(within(sheet).getByText("28 авг. – 3 сент. 2034")).toBeInTheDocument();
    fireEvent.click(within(sheet).getByRole("button", { name: "30 августа, Ср" }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Применить" }));

    await waitFor(() => expect(mocked.day.mock.calls.at(-1)?.[0]).toBe("2034-08-30"));
  });

  it("закрытие без «Применить» ничего не меняет", async () => {
    await openGrid();

    const sheet = await openSheet(/Все мастера/);
    fireEvent.click(within(sheet).getByRole("checkbox", { name: /Мастер Один/ }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Закрыть" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: /Все мастера/ })).toBeInTheDocument();
    expect(window.sessionStorage.getItem(FILTERS_KEY)).toBeNull();
  });

  it("большая команда — поиск по имени (MUST FIX 6)", async () => {
    mocked.day.mockImplementation(async () => {
      const day = salonDay(6);
      for (let i = 7; i <= 9; i += 1) {
        day.masters.push({ master_id: `m-${i}`, name: `Специалист ${i}`, is_active: true, visits: [] });
      }
      return day;
    });
    renderAt(`/admin/schedule?date=${DAY}`);
    await screen.findByRole("heading", { name: "Записи дня" });

    const sheet = await openSheet(/Все мастера/);
    const search = within(sheet).getByRole("searchbox", { name: "Поиск мастера" });
    fireEvent.change(search, { target: { value: "специалист 8" } });

    expect(within(sheet).getByRole("checkbox", { name: /Специалист 8/ })).toBeInTheDocument();
    expect(within(sheet).queryByRole("checkbox", { name: /Мастер Один/ })).toBeNull();

    fireEvent.change(search, { target: { value: "никто" } });
    expect(within(sheet).getByText("Никого не нашли.")).toBeInTheDocument();
  });

  it("маленькой команде поиск не показывается", async () => {
    await openGrid();
    const sheet = await openSheet(/Все мастера/);
    expect(within(sheet).getByRole("checkbox", { name: /Мастер Один/ })).toBeInTheDocument();
    expect(within(sheet).queryByRole("searchbox")).toBeNull();
  });
});

describe("фильтры между заходами", () => {
  it("вход без даты — свежий: фильтры прошлого захода не поднимаются и стираются", async () => {
    selectOnly(["m-1"]);
    await openGrid("/admin/schedule");

    expect(screen.getByRole("button", { name: /Все мастера/ })).toBeInTheDocument();
    expect(window.sessionStorage.getItem(FILTERS_KEY)).toBeNull();
  });

  it("возврат с датой поднимает фильтры", async () => {
    selectOnly(["m-1", "m-2"]);
    await openGrid();
    expect(screen.getByRole("button", { name: "2 мастера" })).toBeInTheDocument();
  });

  it("сохранённый id, которого больше нет, фильтром не считается: записи не прячутся", async () => {
    selectOnly(["архив"], ["удалена"]);
    const grid = await openGrid();

    expect(screen.getByRole("button", { name: /Все мастера/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Все услуги/ })).toBeInTheDocument();
    expect(within(grid).getByRole("button", { name: /^Запись 09:00–10:00/ })).not.toHaveClass("ss-block--dim");
  });
});

describe("кадр 13 — состояния", () => {
  it("день не загрузился — названо «расписание», повтор загружает", async () => {
    mocked.day.mockImplementationOnce(async () => {
      throw new ApiError(500, "server_error", "");
    });
    renderAt(`/admin/schedule?date=${DAY}`);

    expect(await screen.findByRole("heading", { name: "Не удалось загрузить расписание" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));

    expect(await screen.findByRole("region", { name: "Расписание по мастерам" })).toBeInTheDocument();
  });

  it("обновление не удалось — последнее известное остаётся на экране с пометкой", async () => {
    const grid = await openGrid();
    mocked.day.mockImplementationOnce(async () => {
      throw new ApiError(503, "schedule_unavailable", "");
    });

    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));

    expect(await screen.findByText("Не удалось обновить. Показаны последние данные")).toBeInTheDocument();
    expect(within(grid).getByRole("button", { name: /^Запись 09:00–10:00/ })).toBeInTheDocument();
  });

  it("после сбоя обновления окно не действие", async () => {
    await openGrid();
    mocked.day.mockImplementationOnce(async () => {
      throw new ApiError(503, "schedule_unavailable", "");
    });
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    await screen.findByText("Не удалось обновить. Показаны последние данные");

    tapColumn("Мастер Один", 0.5);

    const card = await screen.findByRole("group", { name: "Свободно 12:00–13:00" });
    expect(within(card).getByRole("button", { name: /Записать/ })).toBeDisabled();
  });

  it("возврат на экран перечитывает день, уход с него — нет", async () => {
    await openGrid();
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    fireEvent(document, new Event("visibilitychange"));
    await settleScenario();
    expect(mocked.day).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    fireEvent(document, new Event("visibilitychange"));
    await waitFor(() => expect(mocked.day).toHaveBeenCalledTimes(2));
  });

  it("кадр не прочитан, день прочитан — записи показаны, про смены сказано", async () => {
    mocked.frame.mockImplementation(async () => {
      throw new ApiError(503, "schedule_unavailable", "");
    });
    const grid = await openGrid();

    expect(screen.getByText(/Смены и недоступность не загрузились/)).toBeInTheDocument();
    expect(within(grid).getByRole("button", { name: /^Запись 09:00–10:00/ })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Мастер Три" })).getByText("Часы неизвестны")).toBeInTheDocument();
  });

  it("неразобранный список кадра назван словами", async () => {
    mocked.frame.mockImplementation(async () => salonFrame(4, DAY, { unreadable_lists: ["breaks", "closures"] }));
    await openGrid();

    expect(
      screen.getByText("День показан не полностью: не удалось разобрать перерывы, закрытия салона. Показанное верно, но не всё."),
    ).toBeInTheDocument();
  });

  it("в салоне нет мастеров — карточка, а не пустая сетка", async () => {
    team = 0;
    renderAt(`/admin/schedule?date=${DAY}`);

    expect(await screen.findByRole("heading", { name: "Нет мастеров" })).toBeInTheDocument();
    expect(screen.getByText("В салоне нет мастеров — расписание показывать некому.")).toBeInTheDocument();
  });
});

describe("кадр 12 — экран «Мастер»", () => {
  async function openMaster(entry = `/admin/schedule/master/m-1?date=${DAY}`) {
    renderAt(entry);
    await screen.findByRole("button", { name: /^Свободно 12:00–13:00/ });
  }

  it("шапка, вкладки и день: записи дня салона, окно, блок", async () => {
    await openMaster();

    expect(screen.getByRole("heading", { name: "Мастер" })).toBeInTheDocument();
    expect(screen.getByText("Мастер Один")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Расписание" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "Пн, 21 августа" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Запись 09:00–10:00, Клиент А., Маникюр" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Блок 14:00–15:00/ })).toBeInTheDocument();
    // Чужие записи на экране мастера не появляются.
    expect(screen.queryByRole("button", { name: /Клиент Б\./ })).toBeNull();
    expect(mocked.masterDay).toHaveBeenCalledTimes(1);
  });

  it("окно ведёт в ручную запись и просит вернуть к мастеру", async () => {
    await openMaster();

    fireEvent.click(screen.getByRole("button", { name: /^Свободно 12:00–13:00/ }));

    expect(where()).toBe(
      `/admin/booking/new?return=schedule-master&date=${DAY}&master_id=m-1&from=12%3A00&to=13%3A00|null`,
    );
    expectNoWrites();
  });

  it("запись ведёт в карточку и уносит адрес мастера", async () => {
    await openMaster();

    fireEvent.click(screen.getByRole("button", { name: /^Запись 09:00–10:00/ }));
    expect(where()).toBe(
      `/admin/bookings/v-1|${JSON.stringify({ from: `/admin/schedule/master/m-1?date=${DAY}` })}`,
    );
  });

  it("«назад» и «✕» возвращают в расписание той же даты", async () => {
    await openMaster();
    fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(where()).toBe(`/admin/schedule?date=${DAY}|null`);
  });

  it("«О мастере» — только чтение и ссылка на карточку с недельным графиком", async () => {
    await openMaster();

    fireEvent.click(screen.getByRole("tab", { name: "О мастере" }));

    expect(await screen.findByText("Стригу с 2015 года.")).toBeInTheDocument();
    expect(screen.getByText("10 лет")).toBeInTheDocument();
    expect(screen.getByText(/Недельный график и его подтверждение — в карточке мастера/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Открыть карточку мастера" }));
    expect(where()).toBe("/admin/team/m-1|null");
  });

  it("прошедшая дата — окон нет, и про «свободных промежутков нет» экран молчит", async () => {
    renderAt("/admin/schedule/master/m-1?date=2020-01-06");
    mocked.masterDay.mockImplementation(async () => ({
      tenant_tz: "Europe/Moscow",
      from: "2020-01-06",
      to: "2020-01-06",
      days: [masterDay({ date: "2020-01-06", blocks: [] })],
    }));

    expect(await screen.findByText("Расписание не подтверждено — салон его не заверял")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Свободно/ })).toBeNull();
    expect(screen.queryByText("Свободных промежутков нет.")).toBeNull();
  });

  it("адрес с негодным id мастера не уходит в запросы — возврат в расписание", async () => {
    renderAt(`/admin/schedule/master/..%2F..%2Fsettings?date=${DAY}`);

    await waitFor(() => expect(where()).toBe(`/admin/schedule?date=${DAY}|null`));
    expect(mocked.detail).not.toHaveBeenCalled();
  });

  it("выключенному мастеру окно на экране мастера не действие", async () => {
    mocked.day.mockImplementation(async () => {
      const day = salonDay();
      day.masters[0]!.is_active = false;
      return day;
    });
    await openMaster();
    expect(screen.getByRole("button", { name: /^Свободно 12:00–13:00/ })).toBeDisabled();
  });

  it("без сети окно на экране мастера не нажимается", async () => {
    const restore = offline();
    try {
      await openMaster();
      expect(screen.getByRole("button", { name: /^Свободно 12:00–13:00/ })).toBeDisabled();
    } finally {
      restore();
    }
  });

  it("салон закрыт — на экране мастера окон нет", async () => {
    mocked.frame.mockImplementation(async () =>
      salonFrame(4, DAY, {
        closures: { state: "parsed", rows: [{ start: null, end: null, is_full_day: true, reason: "" }], seen_fields: [] },
      }),
    );
    renderAt(`/admin/schedule/master/m-1?date=${DAY}`);

    expect(await screen.findByText("Свободные окна на этот день не показываются.")).toBeInTheDocument();
    expect(screen.getByText("В этот день салон закрыт")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Свободно/ })).toBeNull();
  });

  it("вкладки ходят стрелками", async () => {
    await openMaster();
    const schedule = screen.getByRole("tab", { name: "Расписание" });
    schedule.focus();

    fireEvent.keyDown(schedule, { key: "ArrowRight" });

    const about = screen.getByRole("tab", { name: "О мастере" });
    expect(about).toHaveAttribute("aria-selected", "true");
    expect(about).toHaveFocus();
    expect(schedule).toHaveAttribute("tabindex", "-1");
  });

  it("выходной назван словом сервера", async () => {
    mocked.masterDay.mockImplementation(async () => ({
      tenant_tz: "Europe/Moscow",
      from: DAY,
      to: DAY,
      days: [masterDay({ is_off_day: true, working_hours: null, blocks: [], free_windows: [] })],
    }));
    renderAt(`/admin/schedule/master/m-1?date=${DAY}`);

    expect(await screen.findByText("Выходной")).toBeInTheDocument();
    // Салонная запись законно живёт и в выходной — она остаётся видна.
    expect(screen.getByRole("button", { name: /^Запись 09:00–10:00/ })).toBeInTheDocument();
  });
});
