/**
 * Экран «Рабочий график» — контракт часов (DRF-1817, M25; макет DRF-1186).
 *
 * Экранные сторожа макета М-7 (неделя, три варианта дня, «недоступно»,
 * конфликт, салонная поверхность) живут в `MasterWorkingHours2200.test.tsx`.
 * Здесь — то, что экран обязан соблюдать НЕЗАВИСИМО от макета:
 *
 * - на пустом каталоге ничего не предвыбрано: «10:00–19:00» из воздуха нет;
 * - PUT уходит ровно семью днями, и меняется только тот день, который
 *   правили: шесть остальных идут такими, какими пришли из каталога;
 * - экран рисует readback каталога, не эхо запроса;
 * - валидация §13.3 не пускает сохранение и называет причину;
 * - отказы сервера переводятся честно (409 → «есть записи», 403 → «не
 *   связан»), системные состояния — через SystemState (DRF-2194);
 * - семантика §13.2: «По этим часам Ayla будет рассчитывать доступное
 *   время для записи» есть, «Клиенты увидят ваше расписание» — нет.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getWorkingHours: vi.fn(),
    putWorkingHours: vi.fn(),
    requestAvailability: vi.fn(),
  };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { ApiError } from "../lib/api";
import {
  getWorkingHours,
  putWorkingHours,
  type WorkingHoursDay,
  type WorkingHoursResponse,
} from "../lib/master-api";
import {
  CONFLICT_MESSAGE,
  HOURS_COPY,
  INVALID_BREAK,
  INVALID_INTERVAL,
  MasterWorkingHoursScreen,
  NOT_LINKED_MESSAGE,
  CONTINUE_LATER_LABEL,
  SAVED_MESSAGE,
  SEMANTIC_NOTE,
  conflictsFrom,
  dayError,
  defaultInterval,
  hhmm,
  horizonFrom,
  nextDateFor,
  summary,
  weekFrom,
} from "./MasterWorkingHoursScreen";

const mockedGet = vi.mocked(getWorkingHours);
const mockedPut = vi.mocked(putWorkingHours);

function day(d: number, extra: Partial<WorkingHoursDay> = {}): WorkingHoursDay {
  return {
    day_of_week: d,
    is_working_day: false,
    start_time: null,
    end_time: null,
    break_start: null,
    break_end: null,
    ...extra,
  };
}

const EMPTY: WorkingHoursResponse = {
  specialist_id: "m1",
  timezone: "Europe/Moscow",
  schedule: Array.from({ length: 7 }, (_, d) => day(d)),
};

/** Пн–Пт 10:00–19:00, выходные пустые — обычный шаблон. */
const FILLED: WorkingHoursResponse = {
  ...EMPTY,
  schedule: Array.from({ length: 7 }, (_, d) =>
    day(d, {
      is_working_day: d < 5,
      start_time: d < 5 ? "10:00" : null,
      end_time: d < 5 ? "19:00" : null,
    }),
  ),
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/solo/working-hours"]}>
      <Routes>
        <Route path="/solo/working-hours" element={<MasterWorkingHoursScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Открыть лист дня и вернуть его. */
async function openDay(index: number) {
  fireEvent.click(screen.getByTestId(`day-${index}`));
  return await screen.findByRole("dialog");
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedGet.mockResolvedValue(EMPTY);
  mockedPut.mockImplementation(async (schedule) => ({ ...EMPTY, schedule, schedule_confirmed: true }));
});

describe("неделя", () => {
  it("на пустом каталоге ничего не предвыбрано, «10:00–19:00» из воздуха нет", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: HOURS_COPY.title });
    const rows = screen.getAllByRole("button", { name: /^(Понедельник|Вторник|Среда|Четверг|Пятница|Суббота|Воскресенье)/ });
    expect(rows).toHaveLength(7);
    expect(rows.every((r) => r.textContent?.includes(HOURS_COPY.dayOff))).toBe(true);
    expect(document.body.textContent).not.toMatch(/10:00|19:00/);
    // Семантика §13.2: верная фраза есть, запрещённой нет.
    expect(screen.getByText(SEMANTIC_NOTE)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Клиенты увидят/i);
    expect(screen.getByTestId("working-hours-tz")).toHaveTextContent("Europe/Moscow");
  });

  it("сохранённые часы приходят из каталога", async () => {
    mockedGet.mockResolvedValue({
      ...EMPTY,
      schedule: [day(0, { is_working_day: true, start_time: "09:00:00", end_time: "18:00:00" })],
    });
    renderScreen();
    await screen.findByRole("heading", { name: HOURS_COPY.title });
    expect(screen.getByTestId("day-0")).toHaveTextContent("09:00–18:00");
    expect(screen.getByTestId("day-1")).toHaveTextContent(HOURS_COPY.dayOff);
  });
});

describe("правка одного дня", () => {
  it("«Не работаю» шлёт ровно 7 дней и трогает только этот день", async () => {
    mockedGet.mockResolvedValue(FILLED);
    renderScreen();
    await screen.findByRole("heading", { name: HOURS_COPY.title });
    const sheet = await openDay(2);
    fireEvent.click(within(sheet).getByRole("radio", { name: /^Не работаю/ }));
    fireEvent.click(within(sheet).getByRole("button", { name: HOURS_COPY.day.save }));
    await waitFor(() => expect(mockedPut).toHaveBeenCalledTimes(1));
    const sent = mockedPut.mock.calls[0]?.[0] ?? [];
    expect(sent).toHaveLength(7);
    expect(sent[2]).toMatchObject({ day_of_week: 2, is_working_day: false, start_time: null });
    // Соседние дни ушли такими, какими пришли.
    expect(sent[1]).toMatchObject({ is_working_day: true, start_time: "10:00", end_time: "19:00" });
    expect(await screen.findByText(SAVED_MESSAGE)).toBeInTheDocument();
  });

  it("«Другие часы» открываются рабочим интервалом мастера и пишут его", async () => {
    mockedGet.mockResolvedValue(FILLED);
    mockedPut.mockResolvedValue({
      ...FILLED,
      schedule: [day(5, { is_working_day: true, start_time: "12:30", end_time: "16:00" })],
    });
    renderScreen();
    await screen.findByRole("heading", { name: HOURS_COPY.title });
    const sheet = await openDay(5);
    fireEvent.click(within(sheet).getByRole("radio", { name: /^Другие часы/ }));
    expect(within(sheet).getByLabelText(HOURS_COPY.day.from)).toHaveValue("10:00");
    fireEvent.change(within(sheet).getByLabelText(HOURS_COPY.day.to), {
      target: { value: "16:00" },
    });
    fireEvent.click(within(sheet).getByRole("button", { name: HOURS_COPY.day.save }));
    await waitFor(() => expect(mockedPut).toHaveBeenCalledTimes(1));
    expect(mockedPut.mock.calls[0]?.[0]?.[5]).toMatchObject({
      is_working_day: true,
      start_time: "10:00",
      end_time: "16:00",
    });
    // Экран показывает то, что каталог прочёл (12:30), а не то, что послали.
    expect(await screen.findByTestId("day-5")).toHaveTextContent("12:30–16:00");
  });

  it("на пустом шаблоне «Другие часы» пусты, сохранение названо невозможным", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: HOURS_COPY.title });
    const sheet = await openDay(1);
    fireEvent.click(within(sheet).getByRole("radio", { name: /^Другие часы/ }));
    expect(within(sheet).getByLabelText(HOURS_COPY.day.from)).toHaveValue("");
    fireEvent.click(within(sheet).getByRole("button", { name: HOURS_COPY.day.save }));
    expect(await within(sheet).findByRole("alert")).toHaveTextContent(INVALID_INTERVAL);
    expect(mockedPut).not.toHaveBeenCalled();
  });
});

describe("онбординг", () => {
  it("«Продолжить позже» уводит на экран 01 и ничего не пишет", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: HOURS_COPY.title });
    fireEvent.click(screen.getByRole("button", { name: CONTINUE_LATER_LABEL }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/solo/setup");
    // Каждый день сохранён своим листом — «сохранить всё» здесь нечего, и
    // кнопки «Сохранить расписание», писавшей ответ сервера им же, больше нет.
    expect(mockedPut).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Сохранить расписание/ })).toBeNull();
  });

  it.each([
    [new ApiError(409, "has_active_appointments", "…"), CONFLICT_MESSAGE],
    [new ApiError(403, "not_linked", "…"), NOT_LINKED_MESSAGE],
  ])("отказ сервера %s переводится честно", async (err, text) => {
    mockedGet.mockResolvedValue(FILLED);
    mockedPut.mockRejectedValue(err);
    renderScreen();
    await screen.findByRole("heading", { name: HOURS_COPY.title });
    const sheet = await openDay(2);
    fireEvent.click(within(sheet).getByRole("radio", { name: /^Не работаю/ }));
    fireEvent.click(within(sheet).getByRole("button", { name: HOURS_COPY.day.save }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });
});

describe("правила §13.3 и чистые помощники", () => {
  it("dayError повторяет серверную проверку", () => {
    expect(dayError(day(0))).toBeNull();
    expect(dayError(day(0, { is_working_day: true }))).toBe(INVALID_INTERVAL);
    expect(dayError(day(0, { is_working_day: true, start_time: "10:00", end_time: "10:00" }))).toBe(
      INVALID_INTERVAL,
    );
    expect(
      dayError(day(0, { is_working_day: true, start_time: "10:00", end_time: "18:00", break_start: "09:00", break_end: "09:30" })),
    ).toBe(INVALID_BREAK);
    expect(
      dayError(day(0, { is_working_day: true, start_time: "10:00", end_time: "18:00", break_start: "13:00", break_end: "14:00" })),
    ).toBeNull();
  });

  it("weekFrom всегда даёт 7 дней в порядке Пн…Вс и режет секунды", () => {
    const week = weekFrom([day(6, { is_working_day: true, start_time: "10:00:00", end_time: "19:00:00" })]);
    expect(week.map((d) => d.day_of_week)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(week[6]).toMatchObject({ start_time: "10:00", end_time: "19:00" });
    expect(week[0]?.is_working_day).toBe(false);
  });

  it("summary называет интервал с перерывом, иначе «Выходной»", () => {
    expect(summary(day(0, { is_working_day: true, start_time: "10:00", end_time: "19:00" }))).toBe(
      "10:00–19:00",
    );
    expect(
      summary(day(0, { is_working_day: true, start_time: "10:00", end_time: "19:00", break_start: "13:00", break_end: "14:00" })),
    ).toBe("10:00–19:00 · перерыв 13:00–14:00");
    expect(summary(day(0))).toBe(HOURS_COPY.dayOff);
  });

  it("defaultInterval берёт собственный рабочий интервал, на пустом шаблоне — пусто", () => {
    expect(defaultInterval(weekFrom(FILLED.schedule))).toEqual(["10:00", "19:00"]);
    expect(defaultInterval(weekFrom(EMPTY.schedule))).toEqual(["", ""]);
  });

  it("nextDateFor — ближайшая такая дата, считая сегодня", () => {
    // Среда, 23 сентября 2026.
    const wednesday = new Date(2026, 8, 23);
    expect(nextDateFor(2, wednesday).getDate()).toBe(23);
    expect(nextDateFor(4, wednesday).getDate()).toBe(25);
    expect(nextDateFor(1, wednesday).getDate()).toBe(29);
  });

  it("hhmm режет время из ISO, не пересчитывая пояс", () => {
    expect(hhmm("2026-08-26T14:30:00+03:00")).toBe("14:30");
    expect(hhmm("")).toBe("");
  });

  it("conflictsFrom берёт только полные строки — «undefined мин» не рисуем", () => {
    const full = {
      booking_id: "b-1",
      client_name: "Анна П.",
      service_name: "Массаж",
      duration_min: 60,
      start_at: "2026-08-26T14:30:00+03:00",
      end_at: "2026-08-26T15:30:00+03:00",
    };
    // Присутствие: полная строка доходит.
    expect(conflictsFrom(new ApiError(409, "x", "y", { conflicts: [full] }))).toHaveLength(1);
    expect(conflictsFrom(new Error("boom"))).toEqual([]);
    expect(conflictsFrom(new ApiError(409, "x", "y"))).toEqual([]);
    expect(
      conflictsFrom(new ApiError(409, "x", "y", { conflicts: [{ booking_id: "b-1" }] })),
    ).toEqual([]);
    const { duration_min: _skip, ...noDuration } = full;
    expect(
      conflictsFrom(new ApiError(409, "x", "y", { conflicts: [noDuration] })),
    ).toEqual([]);
  });

  it("horizonFrom называет горизонт, когда сервер его прислал", () => {
    expect(
      horizonFrom(new ApiError(409, "x", "y", { horizon_days: 14 })),
    ).toBe(14);
    expect(horizonFrom(new ApiError(409, "x", "y"))).toBeNull();
    expect(horizonFrom(new Error("boom"))).toBeNull();
  });
});

describe("системные состояния через SystemState (DRF-2194)", () => {
  it("загрузка — общий скелет без слов", () => {
    mockedGet.mockReturnValue(new Promise(() => {}));
    renderScreen();
    expect(screen.getByRole("status", { busy: true })).toBeInTheDocument();
  });

  it("403 not_linked на загрузке — свой текст экрана, не «Недостаточно прав»", async () => {
    mockedGet.mockRejectedValueOnce(new ApiError(403, "not_linked", "…"));
    renderScreen();
    // Скелет тоже role=status (busy) — ждём текст, а не роль.
    expect(await screen.findByText(NOT_LINKED_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByText(/Недостаточно прав/)).toBeNull();
  });

  it("инцидент 21.09 (DRF-2150): не связан — панель на месте и сказано, кто привяжет", async () => {
    // Салонный мастер: на /master/* панель рисуется (на /solo/* её несёт соло-каркас).
    mockedGet.mockRejectedValueOnce(new ApiError(403, "not_linked", "…"));
    render(
      <MemoryRouter initialEntries={["/master/working-hours"]}>
        <Routes>
          <Route path="/master/working-hours" element={<MasterWorkingHoursScreen />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(NOT_LINKED_MESSAGE)).toBeInTheDocument();
    expect(NOT_LINKED_MESSAGE).toContain("Привязку выполнит оператор.");
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(within(nav).getByRole("button", { name: "Сегодня" })).toBeInTheDocument();
  });

  it("ошибка — «Не удалось загрузить рабочие часы» + «Попробовать снова»", async () => {
    mockedGet.mockRejectedValueOnce(new Error("boom"));
    renderScreen();
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить рабочие часы");
    expect(screen.queryByText(/Не получилось загрузить/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
  });
});
