/**
 * «Рабочее время» мастера по макету DRF-1186 (DRF-2200, М-7).
 *
 * Лист начинался красным: экран был смонтирован только на
 * `/solo/working-hours`, у салонного мастера «Рабочие часы →»
 * перебрасывало в «Расписание», а «Сегодня» при отсутствии шаблона
 * говорило «Сегодня выходной» — часов нет, а не выходной.
 *
 * * h1 — экран 1 «Рабочий график»: заголовок и подпись макета, семь дней с
 *   интервалами / «Выходной», тап по дню открывает экран дня;
 * * h2 — экран 2 «Изменить конкретный день»: три варианта макета («По
 *   обычному графику» / «Другие часы» / «Не работаю»), метка «Изменение
 *   только на этот день», ⓘ «Это изменение не повлияет…»;
 * * h3 — экран 3 «Недоступно (часть дня)»: С / До / «Причина
 *   (необязательно)» / ⓘ «Новые записи на это время не смогут быть
 *   созданы.» / «Сохранить»; причина не обязательна;
 * * h4 — экран 4 «Конфликт с записью»: ⚠ «На это время уже есть запись.»,
 *   карточка конфликтующей записи, «Открыть запись» и «Вернуться»;
 *   изменение НЕ применено, черновик сохранён;
 * * h5 — салонная поверхность: тот же экран только на чтение + «Запросить
 *   изменение» (заявка владельцу, §83), правки нет; заявка уходит ТЕМ, что
 *   выбрано, и на названную дату, а «по обычному графику» просить нечего;
 * * h6 — «часы не заданы» ≠ «выходной» на «Сегодня», и дверь ведёт по
 *   поверхности: соло — в редактор, салонный — в заявку.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getWorkingHours: vi.fn(),
    putWorkingHours: vi.fn(),
    requestAvailability: vi.fn(),
    getDashboard: vi.fn(),
    getMasterMe: vi.fn(),
    getMasterConversations: vi.fn(),
  };
});

import {
  getDashboard,
  getMasterMe,
  getWorkingHours,
  putWorkingHours,
  requestAvailability,
  type DashboardResponse,
  type WorkingHoursResponse,
} from "../lib/master-api";
import { MasterDashboardScreen } from "./MasterDashboardScreen";
import {
  HOURS_COPY,
  MasterWorkingHoursScreen,
} from "./MasterWorkingHoursScreen";

const mockedHours = vi.mocked(getWorkingHours);
const mockedPut = vi.mocked(putWorkingHours);
const mockedRequest = vi.mocked(requestAvailability);
const mockedDashboard = vi.mocked(getDashboard);

const WEEK: WorkingHoursResponse = {
  timezone: "Europe/Moscow",
  schedule: [
    {
      day_of_week: 0,
      is_working_day: true,
      start_time: "10:00",
      end_time: "19:00",
      break_start: null,
      break_end: null,
    },
    {
      day_of_week: 1,
      is_working_day: true,
      start_time: "10:00",
      end_time: "19:00",
      break_start: null,
      break_end: null,
    },
    {
      day_of_week: 2,
      is_working_day: false,
      start_time: null,
      end_time: null,
      break_start: null,
      break_end: null,
    },
    {
      day_of_week: 3,
      is_working_day: true,
      start_time: "12:00",
      end_time: "20:00",
      break_start: null,
      break_end: null,
    },
    {
      day_of_week: 4,
      is_working_day: true,
      start_time: "10:00",
      end_time: "19:00",
      break_start: null,
      break_end: null,
    },
    {
      day_of_week: 5,
      is_working_day: true,
      start_time: "10:00",
      end_time: "16:00",
      break_start: null,
      break_end: null,
    },
    {
      day_of_week: 6,
      is_working_day: false,
      start_time: null,
      end_time: null,
      break_start: null,
      break_end: null,
    },
  ],
} as WorkingHoursResponse;

function dashboard(
  over: Partial<DashboardResponse["states"]> = {},
): DashboardResponse {
  return {
    master: {
      id: "m-1",
      name: "Анна",
      specialization: "Массаж",
      photo_url: "",
    },
    salon: { id: "t-1", name: "Формула тела" },
    now_iso: "2026-09-21T09:00:00",
    active_visit: null,
    next_visit: null,
    upcoming_today: [],
    inbox_preview: [],
    today_summary: {
      total_clients_today: 0,
      completed_count: 0,
      next_free_window: null,
    },
    tab_badges: {
      conversations_unread: 0,
      schedule_has_pending_change: false,
      profile_has_owner_pending_change: false,
    },
    states: {
      is_day_done: false,
      is_offline_safe_response: false,
      day_off: true,
      hours_set: false,
      ...over,
    },
    week_summary: {
      week_start: "2026-09-21",
      week_end: "2026-09-27",
      bookings: 0,
      completed: 0,
      rating: null,
    },
  } as DashboardResponse;
}

/** Ближайшая среда в счёте экрана (Пн=0…Вс=6), как `nextDateFor`. */
function nextWednesday(): string {
  const today = new Date();
  const mondayFirst = (today.getDay() + 6) % 7;
  const d = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  d.setDate(d.getDate() + ((2 - mondayFirst + 7) % 7));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/solo/working-hours"
          element={<MasterWorkingHoursScreen />}
        />
        <Route
          path="/master/working-hours"
          element={<MasterWorkingHoursScreen />}
        />
        <Route path="/master/dashboard" element={<MasterDashboardScreen />} />
        <Route path="/solo/my-day" element={<MasterDashboardScreen />} />
        <Route
          path="/master/bookings/:id"
          element={<p>Экран «Детали записи»</p>}
        />
        <Route
          path="/solo/bookings/:id"
          element={<p>Экран «Детали записи»</p>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedHours.mockResolvedValue(WEEK);
  mockedPut.mockResolvedValue(WEEK);
  mockedRequest.mockResolvedValue({ id: "r-1", status: "pending" } as never);
  mockedDashboard.mockResolvedValue(dashboard());
  vi.mocked(getMasterMe).mockRejectedValue(new Error("not needed"));
});

describe("1 · «Рабочий график» — неделя по макету", () => {
  it("заголовок, подпись и семь дней с интервалами", async () => {
    renderAt("/solo/working-hours");
    expect(await screen.findByText(HOURS_COPY.title)).toBeInTheDocument();
    expect(screen.getByText("Ваш обычный график.")).toBeInTheDocument();
    expect(screen.getByText("Повторяется каждую неделю.")).toBeInTheDocument();
    for (const [day, value] of [
      ["Понедельник", "10:00–19:00"],
      ["Среда", "Выходной"],
      ["Четверг", "12:00–20:00"],
      ["Суббота", "10:00–16:00"],
      ["Воскресенье", "Выходной"],
    ]) {
      expect(
        screen.getByRole("button", { name: new RegExp(`^${day}`) }),
      ).toHaveTextContent(value!);
    }
    expect(
      screen.getByText(
        "Здесь задаётся ваш обычный график на неделю. Конкретные даты можно изменить отдельно.",
      ),
    ).toBeInTheDocument();
  });
});

describe("2 · «Изменить конкретный день» — три варианта макета", () => {
  it("метка «Изменение только на этот день» и три именованных варианта", async () => {
    renderAt("/solo/working-hours");
    fireEvent.click(await screen.findByRole("button", { name: /^Среда/ }));
    expect(
      await screen.findByText("Изменение только на этот день"),
    ).toBeInTheDocument();
    for (const [name, hint] of [
      ["По обычному графику", "По графику: выходной"],
      ["Другие часы", "Укажите другой интервал на этот день"],
      ["Не работаю", "Выходной только на этот день"],
    ]) {
      const option = screen.getByRole("radio", {
        name: new RegExp(`^${name}`),
      });
      expect(option).toBeInTheDocument();
      expect(screen.getByText(hint!)).toBeInTheDocument();
    }
    expect(
      screen.getByText(
        "Это изменение не повлияет на другие дни и не изменит существующие записи клиентов.",
      ),
    ).toBeInTheDocument();
  });

  it("«Другие часы» — начало и окончание, «Сохранить» пишет только этот день", async () => {
    renderAt("/solo/working-hours");
    fireEvent.click(await screen.findByRole("button", { name: /^Среда/ }));
    fireEvent.click(screen.getByRole("radio", { name: /^Другие часы/ }));
    expect(await screen.findByLabelText("Начало")).toBeInTheDocument();
    expect(screen.getByLabelText("Окончание")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(mockedPut).toHaveBeenCalledTimes(1));
    const saved = mockedPut.mock.calls[0]?.[0] ?? [];
    expect(saved.filter((d) => d.day_of_week === 2)).toHaveLength(1);
  });
});

describe("3 · «Недоступно (часть дня)»", () => {
  it("С / До / причина необязательна / ⓘ о новых записях", async () => {
    renderAt("/solo/working-hours");
    fireEvent.click(await screen.findByRole("button", { name: /^Среда/ }));
    fireEvent.click(screen.getByRole("button", { name: HOURS_COPY.unavailable.open }));
    expect(
      await screen.findByText("Укажите период, в который вы недоступны."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("С")).toBeInTheDocument();
    expect(screen.getByLabelText("До")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Причина (необязательно)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Новые записи на это время не смогут быть созданы."),
    ).toBeInTheDocument();
    // Причина пуста — «Сохранить» всё равно работает (макет: не требуем причину).
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(mockedRequest).toHaveBeenCalledTimes(1));
    expect(mockedRequest.mock.calls[0]?.[0]).toMatchObject({ reason_text: "" });
  });
});

describe("4 · «Конфликт с записью»", () => {
  it("показывает запись, «Открыть запись» и «Вернуться»; изменение не применено", async () => {
    const { ApiError } = await import("../lib/api");
    mockedPut.mockRejectedValue(
      new ApiError(
        409,
        "has_active_appointments",
        "В это время уже есть записи.",
        {
          conflicts: [
            {
              booking_id: "b-1",
              client_name: "Анна П.",
              service_name: "Классический массаж",
              duration_min: 60,
              start_at: "2026-08-26T14:30:00+03:00",
              end_at: "2026-08-26T15:30:00+03:00",
            },
          ],
        } as never,
      ),
    );
    renderAt("/solo/working-hours");
    fireEvent.click(await screen.findByRole("button", { name: /^Среда/ }));
    fireEvent.click(screen.getByRole("radio", { name: /^Не работаю/ }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(
      await screen.findByText("На это время уже есть запись."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Сначала решите, что делать с существующей записью."),
    ).toBeInTheDocument();
    expect(screen.getByText("Анна П.")).toBeInTheDocument();
    expect(screen.getByText(/14:30–15:30/)).toBeInTheDocument();
    expect(
      screen.getByText("Классический массаж · 60 мин"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Изменение графика остановлено. Запись клиента не меняется автоматически.",
      ),
    ).toBeInTheDocument();
    // «Вернуться» — к изменению графика, черновик цел.
    fireEvent.click(screen.getByRole("button", { name: "Вернуться" }));
    expect(
      await screen.findByText("Изменение только на этот день"),
    ).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /^Не работаю/ })).toBeChecked();
  });

  it("«Открыть запись» ведёт на обычный экран записи", async () => {
    const { ApiError } = await import("../lib/api");
    mockedPut.mockRejectedValue(
      new ApiError(
        409,
        "has_active_appointments",
        "В это время уже есть записи.",
        {
          conflicts: [
            {
              booking_id: "b-1",
              client_name: "Анна П.",
              service_name: "Массаж",
              duration_min: 60,
              start_at: "2026-08-26T14:30:00+03:00",
              end_at: "2026-08-26T15:30:00+03:00",
            },
          ],
        } as never,
      ),
    );
    // Конфликт — ответ на ЗАПИСЬ часов, а её делает только соло: у
    // салонного мастера часы пишет салон, заявка ничего не меняет (5·b).
    renderAt("/solo/working-hours");
    fireEvent.click(await screen.findByRole("button", { name: /^Среда/ }));
    fireEvent.click(screen.getByRole("radio", { name: /^Не работаю/ }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    fireEvent.click(await screen.findByRole("link", { name: "Открыть запись" }));
    expect(
      await screen.findByText("Экран «Детали записи»"),
    ).toBeInTheDocument();
  });
});

describe("5 · салонная поверхность — чтение и заявка (§83)", () => {
  it("те же дни, но без правки: «Запросить изменение» вместо «Сохранить»", async () => {
    renderAt("/master/working-hours");
    expect(await screen.findByText(HOURS_COPY.title)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^Понедельник/ }),
    ).toHaveTextContent("10:00–19:00");
    expect(screen.getByText(HOURS_COPY.salon.note)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Сохранить расписание" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: HOURS_COPY.salon.request }),
    ).toBeInTheDocument();
  });

  it("заявка уходит администратору тем, что выбрано, и часы не пишутся", async () => {
    renderAt("/master/working-hours");
    fireEvent.click(await screen.findByRole("button", { name: /^Среда/ }));
    fireEvent.click(screen.getByRole("radio", { name: /^Не работаю/ }));
    fireEvent.click(screen.getByRole("button", { name: HOURS_COPY.salon.request }));
    await waitFor(() => expect(mockedRequest).toHaveBeenCalledTimes(1));
    expect(mockedPut).not.toHaveBeenCalled();
    // Тело заявки — сутки НАЗВАННОЙ даты (ближайшая среда), и администратор
    // читает словами, о чём просили: пустая заявка «на весь день» без повода
    // была бы не тем, что человек выбрал.
    const sent = mockedRequest.mock.calls[0]?.[0];
    const date = nextWednesday();
    expect(sent).toMatchObject({
      start: `${date}T00:00:00`,
      end: `${date}T23:59:00`,
    });
    expect(sent?.reason_text).toContain("Не работаю");
    expect(await screen.findByText(HOURS_COPY.salon.sent)).toBeInTheDocument();
  });

  it("«По обычному графику» просить нечего — кнопка недоступна", async () => {
    renderAt("/master/working-hours");
    fireEvent.click(await screen.findByRole("button", { name: /^Среда/ }));
    // Вариант по умолчанию — «ничего не меняем»: один тап по главной кнопке
    // не должен просить у салона целый день.
    expect(screen.getByRole("radio", { name: /^По обычному графику/ })).toBeChecked();
    expect(
      screen.getByRole("button", { name: HOURS_COPY.salon.request }),
    ).toBeDisabled();
    expect(screen.getByText(HOURS_COPY.salon.nothingToAsk)).toBeInTheDocument();
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it("дата заявки названа в шапке листа, а не угадана молча", async () => {
    renderAt("/master/working-hours");
    fireEvent.click(await screen.findByRole("button", { name: /^Среда/ }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleName(/^Среда, \d{1,2} /);
  });
});

describe("6 · «часы не заданы» ≠ «выходной»", () => {
  it("на «Сегодня» при пустом шаблоне — своё состояние и дверь в часы", async () => {
    renderAt("/master/dashboard");
    expect(
      await screen.findByText(HOURS_COPY.notSet.title),
    ).toBeInTheDocument();
    expect(screen.queryByText("Сегодня выходной")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: HOURS_COPY.notSet.cta }));
    expect(await screen.findByText(HOURS_COPY.title)).toBeInTheDocument();
  });

  it("у соло — своя дверь: «Задать часы →» ведёт в редактор", async () => {
    renderAt("/solo/my-day");
    expect(
      await screen.findByText(HOURS_COPY.notSet.title),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: HOURS_COPY.notSet.ctaSolo }),
    );
    expect(await screen.findByText(HOURS_COPY.title)).toBeInTheDocument();
  });

  it("шаблон есть и сегодня выходной — прежний текст", async () => {
    mockedDashboard.mockResolvedValue(dashboard({ hours_set: true }));
    renderAt("/master/dashboard");
    expect(await screen.findByText("Сегодня выходной")).toBeInTheDocument();
  });
});
