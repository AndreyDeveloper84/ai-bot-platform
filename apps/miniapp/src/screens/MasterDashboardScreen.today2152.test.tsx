/**
 * «Сегодня» мастера по макету DRF-1182 (DRF-2152, М-1).
 *
 * Состояние дня — ПЕРВЫМ, одно из четырёх: ближайшая запись (имя, услуга,
 * начало–конец, «до визита N мин») + следующие спокойнее · «Сейчас по
 * расписанию» (без «Сейчас идёт визит» и «До конца ≈») · «На сегодня записей
 * нет» (кнопка «Добавить запись» появится с М-3 / DRF-2155 — мёртвых и лживых
 * кнопок не рисуем, DRF-1181) · «Сегодня выходной» + «Рабочие часы →» ·
 * рамка дня не прочитана → «Не удалось проверить расписание» + «Проверить снова».
 *
 * Убрано: «ТРЕБУЮТ ВНИМАНИЯ» (переписки), 💬 в шапке, «Открыть диалог ›», тап
 * по записи → переписка, «ЭТА НЕДЕЛЯ», PayoutPreviewCard, мёртвая «Заметка к
 * визиту ›», «Сказала: …», «Постоянный клиент». Карточка = имя, услуга, время
 * — сторож на набор полей: лишнее из ответа сервера на экран не выходит.
 *
 * Мокируется только `../lib/master-api` (как в App.masterTrio2121): экран
 * настоящий, дочерние карточки грузятся сами и тихо отказывают.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getDashboard: vi.fn(),
    getMasterMe: vi.fn(),
  };
});

import { ApiError } from "../lib/api";
import { getDashboard, type DashboardResponse } from "../lib/master-api";
import { MasterDashboardScreen } from "./MasterDashboardScreen";

const mockedDashboard = vi.mocked(getDashboard);

// Метки времени — без смещения: JS читает их как локальные, а экран рендерит
// локальное время (formatTimeHM). Так ожидания «12:00–13:30» верны и на
// ноутбуке в МСК, и на CI-раннере в UTC.
const NOW = "2026-09-20T11:15:00";

function doc(over: Partial<DashboardResponse> = {}): DashboardResponse {
  return {
    master: {
      id: "m-1",
      name: "Архипкин",
      specialization: "Массаж",
      photo_url: "",
    },
    salon: { id: "t-1", name: "Формула тела" },
    now_iso: NOW,
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
      conversations_unread: 2,
      schedule_has_pending_change: false,
      profile_has_owner_pending_change: false,
    },
    states: {
      is_day_done: false,
      is_offline_safe_response: false,
      day_off: false,
    },
    week_summary: {
      week_start: "2026-09-14",
      week_end: "2026-09-20",
      bookings: 3,
      completed: 2,
      rating: { value: 4.9, review_count: 12 },
    },
    ...over,
  };
}

const NEXT = {
  booking_id: "b-1",
  client_first_name: "Анна",
  client_last_initial: "П.",
  visit_at: "2026-09-20T12:00:00",
  end_at: "2026-09-20T13:30:00",
  minutes_until: 45,
  service_name: "Массаж спины",
  duration_min: 90,
  is_returning_customer: true,
  customer_intent_hint: "Сказала: болит поясница",
};

function renderAt(path = "/master/dashboard") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/master/dashboard" element={<MasterDashboardScreen />} />
        <Route path="/solo/my-day" element={<MasterDashboardScreen />} />
        <Route path="/master/schedule" element={<p>Экран «Расписание»</p>} />
        <Route
          path="/solo/working-hours"
          element={<p>Экран «Рабочие часы»</p>}
        />
        <Route path="/master/conversations" element={<p>Экран переписок</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockedDashboard.mockReset();
});

describe("шапка — дата в стиле деталей записи (DRF-2179, §61 п.6)", () => {
  it("«20 сентября · воскресенье», а не «Воскресенье, 20 сентября»", async () => {
    mockedDashboard.mockResolvedValue(doc());
    renderAt();
    await screen.findByRole("region", { name: /сегодня/i });
    expect(screen.getByText("20 сентября · воскресенье")).toBeInTheDocument();
    expect(screen.queryByText(/Воскресенье, 20 сентября/)).toBeNull();
  });
});

describe("порядок: состояние дня — первым", () => {
  it("блок дня стоит выше карточки настройки и «Спросить Ayla»", async () => {
    mockedDashboard.mockResolvedValue(doc({ next_visit: NEXT }));
    renderAt();
    const day = await screen.findByRole("region", { name: /сегодня/i });
    const ayla = screen.getByRole("button", { name: /Спросить Ayla/ });
    // DOM order: day block precedes the Ayla entry.
    expect(
      day.compareDocumentPosition(ayla) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("состояние 1 — ближайшая запись", () => {
  it("имя, услуга, начало–конец, «до визита N мин»; ничего лишнего", async () => {
    mockedDashboard.mockResolvedValue(
      doc({
        next_visit: NEXT,
        upcoming_today: [
          {
            booking_id: "b-2",
            client_first_name: "Борис",
            client_last_initial: "К.",
            service_name: "Массаж лица",
            visit_at: "2026-09-20T14:00:00",
            end_at: "2026-09-20T15:00:00",
          },
        ],
        today_summary: {
          total_clients_today: 2,
          completed_count: 0,
          next_free_window: null,
        },
      }),
    );
    renderAt();

    const day = await screen.findByRole("region", { name: /сегодня/i });
    expect(within(day).getByText(/Анна П\./)).toBeInTheDocument();
    expect(within(day).getByText("Массаж спины")).toBeInTheDocument();
    expect(within(day).getByText(/12:00–13:30/)).toBeInTheDocument();
    expect(within(day).getByText("До визита 45 мин")).toBeInTheDocument();
    // Следующие записи — спокойнее, ниже, теми же тремя полями.
    expect(within(day).getByText(/Борис К\./)).toBeInTheDocument();
    expect(within(day).getByText(/14:00–15:00/)).toBeInTheDocument();

    // Сторож набора полей карточки: лишнее из ответа сервера на экран не выходит.
    expect(screen.queryByText(/Сказала/)).toBeNull();
    expect(screen.queryByText(/Постоянный клиент/)).toBeNull();
    expect(screen.queryByText(/Открыть диалог/)).toBeNull();
    // Длительность — поле карточки по DRF-1181 п.5 («⏱ 60 мин»), минутами (М-6, DRF-2157).
    expect(within(day).getByText("90 мин")).toBeInTheDocument();
    expect(screen.queryByText(/1 ч 30 мин/)).toBeNull();
    // Тап по записи → «Детали записи» (М-4, DRF-2156), не переписки.
    expect(within(day).queryByRole("button", { name: /Анна/ })).toBeNull();
    expect(within(day).getByRole("link", { name: /Анна/ })).toHaveAttribute(
      "href",
      "/master/bookings/b-1",
    );
    expect(within(day).getByRole("link", { name: /Борис/ })).toHaveAttribute(
      "href",
      "/master/bookings/b-2",
    );
  });

  it("«До визита» — общим форматтером DRF-1185: 80 → «1 ч 20 мин» (§61)", async () => {
    mockedDashboard.mockResolvedValue(
      doc({ next_visit: { ...NEXT, minutes_until: 80 } }),
    );
    renderAt();
    const day = await screen.findByRole("region", { name: /сегодня/i });
    expect(within(day).getByText("До визита 1 ч 20 мин")).toBeInTheDocument();
    expect(screen.queryByText(/80 мин/)).toBeNull();
  });

  it("на /solo/my-day ссылка карточки ведёт на /solo/bookings/:id", async () => {
    mockedDashboard.mockResolvedValue(doc({ next_visit: NEXT }));
    renderAt("/solo/my-day");
    const day = await screen.findByRole("region", { name: /сегодня/i });
    expect(within(day).getByRole("link", { name: /Анна/ })).toHaveAttribute(
      "href",
      "/solo/bookings/b-1",
    );
  });
});

describe("состояние 3 — «Сейчас по расписанию»", () => {
  it("текущая запись подписана «по расписанию», без «идёт визит» и «До конца»", async () => {
    mockedDashboard.mockResolvedValue(
      doc({
        active_visit: {
          booking_id: "b-0",
          client_first_name: "Мария",
          client_last_initial: "И.",
          service_name: "Массаж спины",
          started_at: "2026-09-20T11:00:00",
          duration_min: 60,
          minutes_remaining: 45,
          is_in_progress: true,
          note: "",
        },
        today_summary: {
          total_clients_today: 1,
          completed_count: 0,
          next_free_window: null,
        },
      }),
    );
    renderAt();

    const day = await screen.findByRole("region", { name: /сегодня/i });
    expect(within(day).getByText("Сейчас по расписанию")).toBeInTheDocument();
    expect(within(day).getByText(/Мария И\./)).toBeInTheDocument();
    expect(within(day).getByText(/11:00–12:00/)).toBeInTheDocument();
    expect(screen.queryByText(/Сейчас идёт визит/)).toBeNull();
    expect(screen.queryByText(/До конца/)).toBeNull();
    expect(screen.queryByText(/Заметка к визиту/)).toBeNull();
  });
});

describe("состояние 4 — записей нет", () => {
  it("рабочий день без записей: текст макета и кнопка «Добавить запись» (М-3)", async () => {
    mockedDashboard.mockResolvedValue(doc());
    renderAt();

    const day = await screen.findByRole("region", { name: /сегодня/i });
    expect(within(day).getByText("На сегодня записей нет")).toBeInTheDocument();
    // С М-3 (DRF-2155) кнопка — дверь в «Новую запись» (тап по свободному
    // окну в «Расписании» тоже ведёт в создание, не в «недоступно»).
    expect(
      within(day).getByRole("button", { name: "Добавить запись" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/свободного окна/)).toBeNull();
    // И прежнего «Свободный день — отдохните» тоже нет: текст макета дословно.
    expect(screen.queryByText(/отдохните/)).toBeNull();
  });

  it("выходной: «Сегодня выходной» + «Рабочие часы →»; для соло — экран часов", async () => {
    mockedDashboard.mockResolvedValue(
      doc({
        states: {
          is_day_done: false,
          is_offline_safe_response: false,
          day_off: true,
        },
      }),
    );
    renderAt("/solo/my-day");

    const day = await screen.findByRole("region", { name: /сегодня/i });
    expect(within(day).getByText("Сегодня выходной")).toBeInTheDocument();
    expect(screen.queryByText("На сегодня записей нет")).toBeNull();
    await userEvent.click(
      within(day).getByRole("button", { name: /Рабочие часы/ }),
    );
    expect(await screen.findByText("Экран «Рабочие часы»")).toBeInTheDocument();
  });

  it("выходной у салонного мастера — «Рабочие часы →» ведёт в «Расписание» (заявка)", async () => {
    mockedDashboard.mockResolvedValue(
      doc({
        states: {
          is_day_done: false,
          is_offline_safe_response: false,
          day_off: true,
        },
      }),
    );
    renderAt("/master/dashboard");
    const day = await screen.findByRole("region", { name: /сегодня/i });
    await userEvent.click(
      within(day).getByRole("button", { name: /Рабочие часы/ }),
    );
    expect(await screen.findByText("Экран «Расписание»")).toBeInTheDocument();
  });

  it("рамка дня не прочитана (day_off: null) — «Не удалось проверить расписание» + «Проверить снова»", async () => {
    mockedDashboard
      .mockResolvedValueOnce(
        doc({
          states: {
            is_day_done: false,
            is_offline_safe_response: false,
            day_off: null,
          },
        }),
      )
      .mockResolvedValueOnce(
        doc({
          states: {
            is_day_done: false,
            is_offline_safe_response: false,
            day_off: true,
          },
        }),
      );
    renderAt();
    const day = await screen.findByRole("region", { name: /сегодня/i });
    // Молчание источника — не пустой день и не выходной (DRF-1111).
    expect(
      within(day).getByText("Не удалось проверить расписание"),
    ).toBeInTheDocument();
    expect(screen.queryByText("На сегодня записей нет")).toBeNull();
    expect(screen.queryByText("Сегодня выходной")).toBeNull();
    await userEvent.click(
      within(day).getByRole("button", { name: "Проверить снова" }),
    );
    expect(await screen.findByText("Сегодня выходной")).toBeInTheDocument();
    expect(mockedDashboard).toHaveBeenCalledTimes(2);
  });
});

describe("убрано по макету и §50 п.5", () => {
  it("нет переписок, недели, выплат, значка 💬 в шапке", async () => {
    mockedDashboard.mockResolvedValue(
      doc({
        next_visit: NEXT,
        inbox_preview: [
          {
            conversation_id: "c-1",
            client_first_name: "Ольга",
            client_last_initial: "Р.",
            last_message_excerpt: "Можно перенести?",
            last_message_at: NOW,
            sla_tier: "red",
            ai_drafted_reply_available: true,
          },
        ],
        today_summary: {
          total_clients_today: 1,
          completed_count: 0,
          next_free_window: null,
        },
      }),
    );
    renderAt();

    await screen.findByRole("region", { name: /сегодня/i });
    expect(screen.queryByText(/ТРЕБУЮТ ВНИМАНИЯ/)).toBeNull();
    expect(screen.queryByText(/Ольга/)).toBeNull();
    expect(screen.queryByText(/Все диалоги/)).toBeNull();
    expect(screen.queryByText(/ЭТА НЕДЕЛЯ/)).toBeNull();
    expect(screen.queryByText(/На этой неделе/)).toBeNull();
    expect(screen.queryByText(/★/)).toBeNull();
    expect(screen.queryByText(/[Вв]ыплат/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Диалоги/ })).toBeNull();
    // Положительный сторож той же отрисовки: шапка и «Спросить Ayla» на месте.
    expect(screen.getByText("Архипкин")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Спросить Ayla/ }),
    ).toBeInTheDocument();
  });
});

describe("системные состояния — через SystemState по DRF-1181 (М-6, DRF-2157)", () => {
  it("первичная ошибка — «Не удалось загрузить» + «Попробовать снова»; старого текста нет", async () => {
    mockedDashboard
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(doc());
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось загрузить сегодняшний день",
    );
    expect(screen.queryByText(/Не получилось загрузить/)).toBeNull();
    expect(screen.queryByText(/Проверьте интернет/)).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Попробовать снова" }),
    );
    expect(
      await screen.findByRole("region", { name: /сегодня/i }),
    ).toBeInTheDocument();
  });

  it("403 — «Недостаточно прав» · «Это действие недоступно», не «Этот диалог не для вас»", async () => {
    mockedDashboard.mockRejectedValue(new ApiError(403, "not_linked", "nope"));
    renderAt();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Недостаточно прав");
    expect(alert).toHaveTextContent("Это действие недоступно");
    expect(screen.queryByText(/Этот диалог не для вас/)).toBeNull();
  });

  it("обновление не удалось при данных — «Не удалось обновить» · «Показаны последние данные», данные на месте", async () => {
    mockedDashboard
      .mockResolvedValueOnce(doc({ next_visit: NEXT }))
      .mockRejectedValueOnce(new Error("boom"));
    renderAt();
    const day = await screen.findByRole("region", { name: /сегодня/i });
    expect(within(day).getByText(/Анна П\./)).toBeInTheDocument();
    // Pull-to-refresh: жест вниз ≥ 60px от верха.
    const frame = day.closest(".master-dashboard") as HTMLElement;
    fireEvent.touchStart(frame, { touches: [{ clientY: 10 }] });
    fireEvent.touchEnd(frame, { changedTouches: [{ clientY: 100 }] });
    const status = await screen.findByText(
      "Не удалось обновить. Показаны последние данные",
    );
    expect(status.closest("[role=status]")).not.toBeNull();
    expect(screen.queryByText(/Данные могут быть неактуальны/)).toBeNull();
    // Данные не сброшены.
    expect(
      within(screen.getByRole("region", { name: /сегодня/i })).getByText(
        /Анна П\./,
      ),
    ).toBeInTheDocument();
  });

  it("загрузка — скелет без слов", () => {
    mockedDashboard.mockReturnValue(new Promise(() => {}));
    renderAt();
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText(/Загружаем/)).toBeNull();
  });
});

describe("переписок на «Сегодня» нет (DRF-1255)", () => {
  it("inbox_preview с сервера не влияет на состояние дня: без записей — «На сегодня записей нет»", async () => {
    mockedDashboard.mockResolvedValue(
      doc({
        inbox_preview: [
          {
            conversation_id: "c-1",
            client_first_name: "Ксения",
            client_last_initial: "Л.",
            last_message_excerpt: "Здравствуйте!",
            last_message_at: "2026-09-20T10:00:00",
            sla_tier: "red",
            ai_drafted_reply_available: false,
          },
        ],
      }),
    );
    renderAt();
    const day = await screen.findByRole("region", { name: /сегодня/i });
    expect(within(day).getByText("На сегодня записей нет")).toBeInTheDocument();
    expect(screen.queryByText(/Ксения/)).toBeNull();
    expect(screen.queryByText(/Здравствуйте/)).toBeNull();
  });
});
