/**
 * Экран 06 «Рабочие часы» (DRF-1817, M25; макет §13).
 *
 * Сторожа: A — ничего не предвыбрано на пустом каталоге; B — одно время
 * применяется ко всем выбранным; C — редактор дня с одним переключателем
 * «Рабочий день», без второго «сделать выходным»; D — сохранение шлёт
 * ровно 7 дней и рисует readback; валидация §13.3 блокирует сохранение;
 * семантика §13.2 — фраза «По этим часам Ayla будет рассчитывать…» есть,
 * «Клиенты увидят ваше расписание» — нет; отказы сервера переводятся.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return { ...original, getWorkingHours: vi.fn(), putWorkingHours: vi.fn() };
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
  APPLY_ALL_LABEL,
  CONFLICT_MESSAGE,
  INVALID_BREAK,
  INVALID_INTERVAL,
  MasterWorkingHoursScreen,
  NOT_LINKED_MESSAGE,
  SAVE_LABEL,
  SAVE_LATER_LABEL,
  SAVED_MESSAGE,
  SEMANTIC_NOTE,
  WORKING_DAY_SWITCH,
  dayError,
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

const dayBox = (name: string) => screen.getByRole("checkbox", { name });

beforeEach(() => {
  vi.clearAllMocks();
  mockedGet.mockResolvedValue(EMPTY);
  mockedPut.mockImplementation(async (schedule) => ({ ...EMPTY, schedule, schedule_confirmed: true }));
});

describe("A — дни", () => {
  it("на пустом каталоге ничего не предвыбрано, «10:00–19:00» из воздуха нет", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: "Рабочие часы" });
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(7);
    expect(boxes.every((b) => b.getAttribute("aria-checked") === "false")).toBe(true);
    expect(document.body.textContent).not.toMatch(/10:00|19:00/);
    // Семантика §13.2: верная фраза есть, запрещённой нет.
    expect(screen.getByText(SEMANTIC_NOTE)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Клиенты увидят/i);
    expect(screen.getByTestId("working-hours-tz")).toHaveTextContent("Europe/Moscow");
  });

  it("сохранённые часы приходят из каталога и отмечают дни", async () => {
    mockedGet.mockResolvedValue({
      ...EMPTY,
      schedule: [day(0, { is_working_day: true, start_time: "09:00:00", end_time: "18:00:00" })],
    });
    renderScreen();
    await screen.findByRole("heading", { name: "Рабочие часы" });
    expect(dayBox("Понедельник")).toHaveAttribute("aria-checked", "true");
    expect(dayBox("Вторник")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByTestId("day-0")).toHaveTextContent("09:00–18:00");
  });
});

describe("B — одно время на все выбранные", () => {
  it("«Применить ко всем выбранным дням» ставит время только отмеченным", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: "Рабочие часы" });
    fireEvent.click(dayBox("Понедельник"));
    fireEvent.click(dayBox("Среда"));
    fireEvent.change(screen.getByLabelText("Начало"), { target: { value: "10:00" } });
    fireEvent.change(screen.getByLabelText("Конец"), { target: { value: "18:00" } });
    fireEvent.click(screen.getByRole("button", { name: APPLY_ALL_LABEL }));
    expect(screen.getByTestId("day-0")).toHaveTextContent("10:00–18:00");
    expect(screen.getByTestId("day-2")).toHaveTextContent("10:00–18:00");
    expect(screen.getByTestId("day-1")).toHaveTextContent("Выходной");
  });
});

describe("C — редактор дня", () => {
  it("один переключатель «Рабочий день», без отдельного «сделать выходным»", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: "Рабочие часы" });
    fireEvent.click(screen.getByTestId("day-4"));
    const sheet = await screen.findByRole("dialog");
    const switches = within(sheet).getAllByRole("switch");
    expect(switches).toHaveLength(1);
    expect(switches[0]).toHaveAccessibleName(WORKING_DAY_SWITCH);
    expect(within(sheet).queryByText(/выходн/i)).toBeNull();
    fireEvent.click(switches[0] as HTMLElement);
    fireEvent.change(within(sheet).getByLabelText("Начало дня"), { target: { value: "12:00" } });
    fireEvent.change(within(sheet).getByLabelText("Конец дня"), { target: { value: "20:00" } });
    fireEvent.change(within(sheet).getByLabelText("Перерыв с"), { target: { value: "15:00" } });
    fireEvent.change(within(sheet).getByLabelText("Перерыв до"), { target: { value: "16:00" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Готово" }));
    expect(screen.getByTestId("day-4")).toHaveTextContent("12:00–20:00 · перерыв 15:00–16:00");
    expect(dayBox("Пятница")).toHaveAttribute("aria-checked", "true");
  });
});

describe("D — сохранение", () => {
  it("шлёт ровно 7 дней и рисует readback каталога, не эхо", async () => {
    mockedPut.mockResolvedValue({
      ...EMPTY,
      schedule: [day(0, { is_working_day: true, start_time: "10:30", end_time: "18:00" })],
      schedule_confirmed: true,
    });
    renderScreen();
    await screen.findByRole("heading", { name: "Рабочие часы" });
    fireEvent.click(dayBox("Понедельник"));
    fireEvent.change(screen.getByLabelText("Начало"), { target: { value: "10:00" } });
    fireEvent.change(screen.getByLabelText("Конец"), { target: { value: "18:00" } });
    fireEvent.click(screen.getByRole("button", { name: APPLY_ALL_LABEL }));
    fireEvent.click(screen.getByRole("button", { name: SAVE_LABEL }));
    await waitFor(() => expect(mockedPut).toHaveBeenCalledTimes(1));
    const sent = mockedPut.mock.calls[0]?.[0] ?? [];
    expect(sent).toHaveLength(7);
    expect(sent[0]).toMatchObject({ day_of_week: 0, is_working_day: true, start_time: "10:00" });
    expect(await screen.findByText(SAVED_MESSAGE)).toBeInTheDocument();
    // Экран показывает то, что каталог прочёл (10:30), а не то, что послали.
    expect(screen.getByTestId("day-0")).toHaveTextContent("10:30–18:00");
  });

  it("«Сохранить и продолжить позже» сохраняет и уводит на экран 01", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: "Рабочие часы" });
    fireEvent.click(screen.getByRole("button", { name: SAVE_LATER_LABEL }));
    await waitFor(() => expect(mockedPut).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId("location")).toHaveTextContent("/solo/setup");
  });

  it("невалидный день блокирует сохранение и называет причину", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: "Рабочие часы" });
    fireEvent.click(dayBox("Вторник"));
    fireEvent.change(screen.getByLabelText("Начало"), { target: { value: "19:00" } });
    fireEvent.change(screen.getByLabelText("Конец"), { target: { value: "10:00" } });
    fireEvent.click(screen.getByRole("button", { name: APPLY_ALL_LABEL }));
    expect(screen.getByTestId("day-1")).toHaveTextContent(INVALID_INTERVAL);
    expect(screen.getByRole("button", { name: SAVE_LABEL })).toBeDisabled();
    expect(mockedPut).not.toHaveBeenCalled();
  });

  it.each([
    [new ApiError(409, "has_active_appointments", "…"), CONFLICT_MESSAGE],
    [new ApiError(403, "not_linked", "…"), NOT_LINKED_MESSAGE],
  ])("отказ сервера %s переводится честно", async (err, text) => {
    mockedPut.mockRejectedValue(err);
    renderScreen();
    await screen.findByRole("heading", { name: "Рабочие часы" });
    fireEvent.click(screen.getByRole("button", { name: SAVE_LABEL }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });
});

describe("правила §13.3 (чистые)", () => {
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
});
