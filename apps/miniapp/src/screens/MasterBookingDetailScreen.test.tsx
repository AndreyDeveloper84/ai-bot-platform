/**
 * «Детали записи» мастера по макету DRF-1185 (DRF-2156, М-4).
 *
 * Один экран, меняется только контекстный блок по `temporal_state` с сервера:
 * upcoming → «Следующая запись» · «Сегодня в 15:30» · «До визита 1 ч 20 мин»;
 * now → «Сейчас по расписанию» · «15:30–16:30»; after → «Запись закончилась по
 * расписанию» · «Если всё прошло как запланировано, ничего делать не нужно.»;
 * completed → «Завершено» (ТОЛЬКО по серверу); unknown → «Проверяем результат»
 * + «Проверить снова» (троттл 10 с, блок на время запроса).
 *
 * Отменённая/no_show — status проверяется ДО temporal_state: временного блока
 * нет, одна строка «Запись отменена» (отступление №2, у владельца).
 *
 * Негативные узлы с положительной парой: телефон, оплата, история, заметки,
 * «Начать визит», «Завершить», «Клиент не пришёл», «В процессе», таймер —
 * на экран не выходят даже из «жирного» ответа сервера.
 *
 * Метки времени в фикстурах — без смещения (локальные), чтобы ожидания были
 * верны и в МСК, и на UTC-раннере (урок #1892).
 */
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return { ...original, getMasterBooking: vi.fn() };
});

import { ApiError } from "../lib/api";
import { getMasterBooking, type MasterBookingDetail } from "../lib/master-api";
import {
  MasterBookingDetailScreen,
  RECHECK_MIN_INTERVAL_MS,
} from "./MasterBookingDetailScreen";

const mocked = vi.mocked(getMasterBooking);

/** Ответ сервера с ЛИШНИМИ полями — сторож набора: экран их не рендерит. */
const FAT_EXTRAS = {
  client_phone: "+7 999 123-45-67",
  price: 3500,
  payment_status: "paid",
  history: [{ date: "2026-05-12", service: "Массаж" }],
  note: "Заметка: болит поясница",
  comment: "Комментарий администратора",
};

function detail(over: Partial<MasterBookingDetail> = {}): MasterBookingDetail {
  return {
    id: "b-1",
    client: { name_initial: "Анна П.", last_visit_date: "2026-05-12" },
    service: { id: "s-1", name: "Классический массаж" },
    start_at: "2026-08-20T15:30:00",
    end_at: "2026-08-20T16:30:00",
    duration_min: 60,
    status: "confirmed",
    temporal_state: "upcoming",
    minutes_until: 80,
    checked_at: "2026-08-20T14:10:00",
    ...over,
    ...(FAT_EXTRAS as object),
  } as MasterBookingDetail;
}

function renderAt(path = "/master/bookings/b-1") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/master/bookings/:id" element={<MasterBookingDetailScreen />} />
        <Route path="/solo/bookings/:id" element={<MasterBookingDetailScreen />} />
        <Route path="/master/schedule" element={<p>Экран «Расписание»</p>} />
        <Route path="/solo/schedule" element={<p>Соло «Расписание»</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function findScreen() {
  return screen.findByRole("main", { name: /Анна П\./ });
}

function stateBlock() {
  return screen.getByRole("region", { name: "Состояние записи" });
}

/** Постоянная часть — одна и та же во всех состояниях (макет: «одна основа»). */
function expectPermanentPart(main: HTMLElement) {
  expect(within(main).getByRole("heading", { name: "Анна П." })).toBeInTheDocument();
  expect(within(main).getByText("20 августа · четверг")).toBeInTheDocument();
  expect(within(main).getByText("15:30–16:30")).toBeInTheDocument();
  expect(within(main).getByText("Классический массаж")).toBeInTheDocument();
  // Длительность — подстрокой и под временем, и под услугой (как в макете).
  expect(within(main).getAllByText("1 ч")).toHaveLength(2);
}

/** Что не должно появиться ни в одном состоянии. */
function expectNothingForbidden() {
  for (const re of [
    /\+7 999/,
    /3500/,
    /paid|Оплат/i,
    /История/,
    /Заметка/,
    /Комментарий/,
    /Начать визит/,
    /Завершить/,
    /не пришёл/,
    /В процессе/,
    /Визит идёт/,
    /До конца/,
    /была 12\.05|последний визит/i,
  ]) {
    expect(screen.queryByText(re)).toBeNull();
  }
  expect(screen.queryByRole("timer")).toBeNull();
  expect(screen.queryByRole("progressbar")).toBeNull();
}

beforeEach(() => {
  mocked.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("постоянная часть + контекстный блок по temporal_state", () => {
  it("upcoming: «Следующая запись» · «Сегодня в 15:30» · «До визита 1 ч 20 мин»", async () => {
    mocked.mockResolvedValue(detail());
    renderAt();
    const main = await findScreen();
    expectPermanentPart(main);
    const block = stateBlock();
    expect(within(block).getByText("Следующая запись")).toBeInTheDocument();
    expect(within(block).getByText("Сегодня в 15:30")).toBeInTheDocument();
    expect(within(block).getByText("До визита 1 ч 20 мин")).toBeInTheDocument();
    expect(screen.queryByText("Сейчас по расписанию")).toBeNull();
    expect(screen.queryByText("Завершено")).toBeNull();
    expectNothingForbidden();
  });

  it("upcoming завтра — «Завтра в 09:00»; 45 минут — «До визита 45 мин»", async () => {
    mocked.mockResolvedValue(
      detail({
        start_at: "2026-08-21T09:00:00",
        end_at: "2026-08-21T10:00:00",
        minutes_until: 45,
      }),
    );
    renderAt();
    await findScreen();
    expect(within(stateBlock()).getByText("Завтра в 09:00")).toBeInTheDocument();
    expect(within(stateBlock()).getByText("До визита 45 мин")).toBeInTheDocument();
  });

  it("now: «Сейчас по расписанию» · «15:30–16:30»; без «идёт визит» и таймера", async () => {
    mocked.mockResolvedValue(
      detail({ temporal_state: "now", minutes_until: null, checked_at: "2026-08-20T15:45:00" }),
    );
    renderAt();
    const main = await findScreen();
    expectPermanentPart(main);
    const block = stateBlock();
    expect(within(block).getByText("Сейчас по расписанию")).toBeInTheDocument();
    expect(within(block).getByText("15:30–16:30")).toBeInTheDocument();
    expect(screen.queryByText(/До визита/)).toBeNull();
    expectNothingForbidden();
  });

  it("after: «Запись закончилась по расписанию» + «ничего делать не нужно», без кнопок", async () => {
    mocked.mockResolvedValue(
      detail({ temporal_state: "after", minutes_until: null, checked_at: "2026-08-20T17:00:00" }),
    );
    renderAt();
    const main = await findScreen();
    expectPermanentPart(main);
    const block = stateBlock();
    expect(within(block).getByText("Запись закончилась по расписанию")).toBeInTheDocument();
    expect(
      within(block).getByText("Если всё прошло как запланировано, ничего делать не нужно."),
    ).toBeInTheDocument();
    expect(within(block).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByText("Завершено")).toBeNull();
    expectNothingForbidden();
  });

  it("completed: «Завершено» — только когда так сказал сервер", async () => {
    mocked.mockResolvedValue(
      detail({
        status: "completed",
        temporal_state: "completed",
        minutes_until: null,
        checked_at: "2026-08-20T19:00:00",
      }),
    );
    renderAt();
    const main = await findScreen();
    expectPermanentPart(main);
    expect(within(stateBlock()).getByText("Завершено")).toBeInTheDocument();
    expect(within(stateBlock()).queryAllByRole("button")).toHaveLength(0);
    expectNothingForbidden();
  });

  it("часы устройства не переводят запись в «Завершено»: after остаётся after при start далеко в прошлом", async () => {
    // Сервер сказал after; устройство — что угодно. Экран не считает по своим часам.
    mocked.mockResolvedValue(
      detail({
        start_at: "2020-01-01T10:00:00",
        end_at: "2020-01-01T11:00:00",
        temporal_state: "after",
        minutes_until: null,
        checked_at: "2020-01-01T12:00:00",
      }),
    );
    renderAt();
    await findScreen();
    expect(screen.queryByText("Завершено")).toBeNull();
    expect(within(stateBlock()).getByText("Запись закончилась по расписанию")).toBeInTheDocument();
  });
});

describe("unknown — «Проверяем результат» + «Проверить снова» (троттл 10 с)", () => {
  it("текст макета дословно, кнопка перечитывает ручку; повтор — не чаще раза в 10 с", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocked.mockResolvedValue(
      detail({ temporal_state: "unknown", minutes_until: null, checked_at: "2026-08-20T20:00:00" }),
    );
    renderAt();
    const main = await findScreen();
    expectPermanentPart(main);
    const block = stateBlock();
    expect(within(block).getByText("Проверяем результат")).toBeInTheDocument();
    expect(
      within(block).getByText("Не удалось получить актуальное состояние записи."),
    ).toBeInTheDocument();
    expect(mocked).toHaveBeenCalledTimes(1);

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const btn = within(block).getByRole("button", { name: "Проверить снова" });
    await user.click(btn);
    expect(mocked).toHaveBeenCalledTimes(2);
    // Сразу ещё раз — троттл: запроса нет, кнопка заблокирована.
    expect(btn).toBeDisabled();
    await user.click(btn);
    expect(mocked).toHaveBeenCalledTimes(2);

    await act(async () => {
      vi.advanceTimersByTime(RECHECK_MIN_INTERVAL_MS + 50);
    });
    expect(within(stateBlock()).getByRole("button", { name: "Проверить снова" })).toBeEnabled();
    await user.click(within(stateBlock()).getByRole("button", { name: "Проверить снова" }));
    expect(mocked).toHaveBeenCalledTimes(3);
    expectNothingForbidden();
  });

  it("кнопка заблокирована, пока запрос в полёте; ответ completed заменяет блок", async () => {
    let resolveSecond: (v: MasterBookingDetail) => void = () => {};
    mocked
      .mockResolvedValueOnce(
        detail({ temporal_state: "unknown", minutes_until: null, checked_at: "2026-08-20T20:00:00" }),
      )
      .mockImplementationOnce(
        () =>
          new Promise<MasterBookingDetail>((res) => {
            resolveSecond = res;
          }),
      );
    renderAt();
    await findScreen();
    const btn = within(stateBlock()).getByRole("button", { name: "Проверить снова" });
    await userEvent.click(btn);
    expect(btn).toBeDisabled();
    await act(async () => {
      resolveSecond(
        detail({
          status: "completed",
          temporal_state: "completed",
          minutes_until: null,
          checked_at: "2026-08-20T20:00:30",
        }),
      );
    });
    expect(await within(stateBlock()).findByText("Завершено")).toBeInTheDocument();
    expect(screen.queryByText("Проверяем результат")).toBeNull();
  });
});

describe("отменённая запись — status раньше temporal_state", () => {
  it.each(["cancelled", "no_show"] as const)(
    "status=%s: постоянная часть + «Запись отменена», без временного блока и кнопок",
    async (status) => {
      // temporal_state по часам — «now»; экран обязан его проигнорировать.
      mocked.mockResolvedValue(
        detail({
          status,
          temporal_state: "now",
          minutes_until: null,
          checked_at: "2026-08-20T15:45:00",
        }),
      );
      renderAt();
      const main = await findScreen();
      expectPermanentPart(main);
      expect(within(main).getByText("Запись отменена")).toBeInTheDocument();
      expect(screen.queryByText("Сейчас по расписанию")).toBeNull();
      expect(screen.queryByText(/До визита/)).toBeNull();
      expect(screen.queryByText("Завершено")).toBeNull();
      expect(screen.queryByRole("region", { name: "Состояние записи" })).toBeNull();
      expect(within(main).queryAllByRole("button")).toHaveLength(0);
      expectNothingForbidden();
    },
  );
});

describe("ошибки загрузки", () => {
  it("404 not_found → «Не удалось загрузить запись» + «Проверить снова»; без «Завершено» и без имени", async () => {
    mocked.mockRejectedValueOnce(new ApiError(404, "not_found", "booking not found"));
    mocked.mockResolvedValueOnce(detail());
    renderAt();
    expect(await screen.findByText("Не удалось загрузить запись")).toBeInTheDocument();
    expect(screen.queryByText("Завершено")).toBeNull();
    expect(screen.queryByText("Анна П.")).toBeNull();
    // Повтор после ошибки — без троттла (беречь нечего), и он работает.
    await userEvent.click(screen.getByRole("button", { name: "Проверить снова" }));
    expect(await findScreen()).toBeInTheDocument();
    expect(mocked).toHaveBeenCalledTimes(2);
  });

  it("5xx → тот же общий элемент", async () => {
    mocked.mockRejectedValue(new ApiError(503, "unavailable", "mirror down"));
    renderAt();
    expect(await screen.findByText("Не удалось загрузить запись")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Проверить снова" })).toBeInTheDocument();
  });
});

describe("панель и возврат", () => {
  it("/master/bookings/:id — панель Сегодня | Расписание | Ayla, активна «Расписание»", async () => {
    mocked.mockResolvedValue(detail());
    renderAt();
    await findScreen();
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    const labels = Array.from(nav.querySelectorAll("button, a")).map((b) =>
      b.getAttribute("aria-label"),
    );
    expect(labels).toEqual(["Сегодня", "Расписание", "Ayla"]);
    expect(within(nav).getByRole("button", { name: "Расписание" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("/solo/bookings/:id — экран тот же, мастерской панели нет (соло рисует свою в App)", async () => {
    mocked.mockResolvedValue(detail());
    renderAt("/solo/bookings/b-1");
    await findScreen();
    expect(screen.queryByRole("navigation", { name: "Основная навигация" })).toBeNull();
  });

  it("запрашивает ровно ту запись, что в адресе", async () => {
    mocked.mockResolvedValue(detail({ id: "b-77" }));
    renderAt("/master/bookings/b-77");
    await findScreen();
    expect(mocked).toHaveBeenCalledWith("b-77", expect.anything());
  });
});
