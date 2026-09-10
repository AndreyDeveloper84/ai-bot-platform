/**
 * «Расписание» салона, режим одного мастера (DRF-1237, срез A1).
 *
 * Три вещи, которые эти тесты держат помимо отрисовки:
 *
 * * **выбор мастера берётся из дня салона, где есть ВСЕ неархивные мастера.**
 *   Свободный мастер обязан быть в списке — иначе именно его расписание и
 *   нельзя открыть, а это тот случай, ради которого экран существует;
 * * **недоступный источник называется, а не рисуется пустым днём.** Пустой
 *   день читается как «мастер свободен весь день», и администратор предложит
 *   клиенту время, которого нет;
 * * **свободные окна подписаны как диапазон доступности, а не как слот.**
 *   Это не вежливость: обеденный перерыв в них сегодня протекает (DRF-1638),
 *   и запись такое время отклонит. Подпись — то немногое, что экран вправе
 *   сделать, не заводя четвёртого вычислителя доступности.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    getSalonDay: vi.fn(),
    getMasterDaySchedule: vi.fn(),
    getSalonDayFrame: vi.fn(),
  };
});

import {
  getMasterDaySchedule,
  getSalonDay,
  getSalonDayFrame,
  type MasterDay,
  type MeResponse,
  type SalonDayFrame,
  type SalonDayResponse,
  type SalonDayVisit,
} from "../../lib/admin-api";
import { SalonPilotScheduleScreen } from "./SalonPilotScheduleScreen";

const mockedDay = vi.mocked(getSalonDay);
const mockedSchedule = vi.mocked(getMasterDaySchedule);
const mockedFrame = vi.mocked(getSalonDayFrame);

const ME: MeResponse = {
  user: { id: "u-1", name: "Карина", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula-tela" },
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

function salonDay(): SalonDayResponse {
  return {
    date: "2026-09-10",
    timezone: "Europe/Moscow",
    summary: { total: 0, upcoming: 0, completed: 0, released: 0 },
    masters: [
      { master_id: "m-1", name: "Ольга", is_active: true, visits: [] },
      // Мастер без визитов — он и есть проверяемый случай: расписание
      // свободного человека открыть нужно чаще, чем занятого.
      { master_id: "m-2", name: "Денис", is_active: true, visits: [] },
    ],
    orphan_visits: [],
  };
}

function masterDay(patch: Partial<MasterDay> = {}): MasterDay {
  return {
    date: "2026-09-10",
    is_off_day: false,
    working_hours: { start: "10:00", end: "19:00" },
    bookings: [],
    blocks: [],
    free_windows: [{ start: "10:00", end: "19:00", duration_min: 540 }],
    conflicts: [],
    ...patch,
  };
}

function renderScreen() {
  return render(
    <MemoryRouter>
      <SalonPilotScheduleScreen me={ME} />
    </MemoryRouter>,
  );
}

describe("Расписание салона — режим одного мастера", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedDay.mockResolvedValue(salonDay());
    mockedSchedule.mockResolvedValue({
      tenant_tz: "Europe/Moscow",
      from: "2026-09-10",
      to: "2026-09-10",
      days: [masterDay()],
    });
  });

  it("в выборе есть и тот мастер, у которого нет записей", async () => {
    renderScreen();

    expect(await screen.findByRole("option", { name: "Ольга" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Денис" })).toBeTruthy();
  });

  it("показывает смену и свободное время посчитанными сервером", async () => {
    renderScreen();

    expect(await screen.findByText("Смена 10:00–19:00")).toBeTruthy();
    expect(screen.getByText(/10:00–19:00 · 540 мин/)).toBeTruthy();
  });

  it("свободное время подписано как диапазон, а не как гарантия", async () => {
    // Несущая подпись: перерыв в окна протекает (DRF-1638), и запись такое
    // время отклонит. Экран не чинит это своими руками — он не обещает.
    renderScreen();

    expect(
      await screen.findByText(/диапазон доступности, а не готовый слот/),
    ).toBeTruthy();
  });

  it("нерабочий день назван словами, а не пустым списком", async () => {
    mockedSchedule.mockResolvedValue({
      tenant_tz: "Europe/Moscow",
      from: "2026-09-10",
      to: "2026-09-10",
      days: [masterDay({ is_off_day: true, working_hours: null, free_windows: [] })],
    });

    renderScreen();

    expect(await screen.findByText("Сегодня не работает")).toBeTruthy();
  });

  it("устаревший день не переживает переключение на упавший запрос", async () => {
    // Две прежние версии этого теста были украшениями, и обе поймала подмена.
    //
    // Первая мокала отказ СРАЗУ: «Смена» отсутствовала потому, что дня не
    // было вовсе. Вторая перемонтировала экран новым ключом — состояние
    // обнулялось само, без участия проверяемого кода.
    //
    // Настоящий случай — ТОТ ЖЕ экран: день показан, администратор
    // переключил мастера, запрос упал. Если показанное останется, он увидит
    // расписание Ольги под именем Дениса.
    //
    // Инвариант держат ДВА стража сразу: `setDay(null)` в `catch` и
    // `err == null` на отрисовке. Снимешь один — второй удержит, и подмена
    // промолчит на верном тесте. Доказательство снимает оба; повторяя его,
    // снимайте оба, иначе прочитаете «тест бесполезен» там, где он рабочий.
    renderScreen();
    expect(await screen.findByText("Смена 10:00–19:00")).toBeTruthy();

    mockedSchedule.mockRejectedValue(new Error("boom"));
    fireEvent.change(screen.getByLabelText("Мастер"), { target: { value: "m-2" } });

    await waitFor(() => {
      expect(screen.queryByText("Смена 10:00–19:00")).toBeNull();
    });
    expect(screen.queryByText(/диапазон доступности/)).toBeNull();
  });

  it("конфликт показывается, а не прячется", async () => {
    // Запись вне рабочих часов существует законно — салонная и уличная
    // создаются вне рамки намеренно. Спрятать её значит заставить
    // администратора гадать, почему день выглядит странно.
    mockedSchedule.mockResolvedValue({
      tenant_tz: "Europe/Moscow",
      from: "2026-09-10",
      to: "2026-09-10",
      days: [
        masterDay({
          conflicts: [
            {
              type: "outside_hours",
              booking_id: "b-1",
              description: "Запись в 20:30 вне рабочих часов",
            },
          ],
        }),
      ],
    });

    renderScreen();

    expect(await screen.findByText("Запись в 20:30 вне рабочих часов")).toBeTruthy();
  });
});

/**
 * Режим «Все» — срез A2.
 *
 * Он складывает ДВА ответа: визиты из дня салона (зеркало) и кадр из Ayla.
 * Тесты держат три вещи, каждая из которых иначе стоила бы дефекта:
 *
 * * параллельные записи группируются по времени — ради этого режим и нужен;
 * * неразобранный список назван СЛОВАМИ. Молчаливая пустота прочиталась бы
 *   как «перерывов нет», и первый салон с обедом получил бы его показанным
 *   рабочим временем;
 * * свободного времени здесь нет вовсе — вычесть занятое из смены значило бы
 *   завести четвёртый вычислитель доступности (§17, DRF-1637).
 */
function visit(patch: Partial<SalonDayVisit> = {}): SalonDayVisit {
  return {
    id: "v-1",
    service_id: "s-1",
    start_at: "2026-09-10T14:00:00+03:00",
    end_at: "2026-09-10T15:00:00+03:00",
    duration_min: 60,
    status: "confirmed",
    service_name: "Стрижка",
    client_first_name: "Мария",
    client_last_initial: "К.",
    is_in_progress: false,
    ...patch,
  };
}

function frameMaster(patch: Partial<SalonDayFrame["masters"][number]> = {}) {
  return {
    specialist_id: "m-1",
    display_name: "Ольга",
    is_working_day: true,
    schedule_note: null,
    schedule_source: "ayla",
    working_intervals: {
      state: "parsed" as const,
      rows: [{ start: "10:00", end: "19:00" }],
      seen_fields: ["end_local", "start_local"],
    },
    breaks: { state: "none" as const, rows: [], seen_fields: [] },
    absences: { state: "none" as const, rows: [], seen_fields: [] },
    ...patch,
  };
}

function frame(patch: Partial<SalonDayFrame> = {}): SalonDayFrame {
  return {
    date: "2026-09-10",
    source: "ayla",
    masters: [frameMaster()],
    unreadable_lists: [],
    ...patch,
  };
}

async function switchToAll() {
  fireEvent.click(await screen.findByRole("tab", { name: "Все" }));
}

describe("Расписание салона — режим «Все»", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedDay.mockResolvedValue(salonDay());
    mockedSchedule.mockResolvedValue({
      tenant_tz: "Europe/Moscow",
      from: "2026-09-10",
      to: "2026-09-10",
      days: [masterDay()],
    });
    mockedFrame.mockResolvedValue(frame());
  });

  it("параллельные записи стоят под одним временем, а не в четырёх колонках", async () => {
    const base = salonDay();
    mockedDay.mockResolvedValue({
      ...base,
      masters: [
        { ...base.masters[0]!, visits: [visit({ id: "v-1" })] },
        {
          ...base.masters[1]!,
          visits: [visit({ id: "v-2", client_first_name: "Пётр", service_name: "Бритьё" })],
        },
      ],
    });

    renderScreen();
    await switchToAll();

    expect(await screen.findByText("14:00")).toBeTruthy();
    expect(screen.getByText(/Ольга · Стрижка · Мария К\./)).toBeTruthy();
    expect(screen.getByText(/Денис · Бритьё · Пётр К\./)).toBeTruthy();
    // Одно время на обе записи, а не два одинаковых заголовка.
    expect(screen.getAllByText("14:00")).toHaveLength(1);
  });

  it("неразобранный список назван словами, а не показан пустотой", async () => {
    // Форма непустой строки breaks не проверена ничем: перерывов не завёл
    // никто. Пока это так, «не разобрал» обязано звучать иначе, чем «нет».
    mockedFrame.mockResolvedValue(
      frame({
        masters: [
          frameMaster({
            breaks: { state: "unreadable", rows: [], seen_fields: ["from_minute"] },
          }),
        ],
        unreadable_lists: ["breaks"],
      }),
    );

    renderScreen();
    await switchToAll();

    expect(await screen.findByText(/не удалось разобрать перерывы/)).toBeTruthy();
  });

  it("часы, которых сервер не разобрал, не выдаются за смену", async () => {
    mockedFrame.mockResolvedValue(
      frame({
        masters: [
          frameMaster({
            working_intervals: { state: "unreadable", rows: [], seen_fields: ["shift"] },
          }),
        ],
        unreadable_lists: ["working_intervals"],
      }),
    );

    renderScreen();
    await switchToAll();

    expect(await screen.findByText(/Ольга · часы неизвестны/)).toBeTruthy();
  });

  it("свободного времени в режиме «Все» нет вовсе", async () => {
    // Вычесть занятое из смены — это и есть четвёртый вычислитель
    // доступности. Здесь его нет, и подписи про диапазон тоже нет.
    renderScreen();
    await switchToAll();

    await screen.findByText("Хронология");
    expect(screen.queryByText("Свободное время")).toBeNull();
    expect(screen.queryByText(/диапазон доступности/)).toBeNull();
  });

  it("устаревший кадр не переживает отказ при возврате в режим", async () => {
    // Инвариант держат ДВА стража: `setFrame(null)` в `catch` и
    // `frameErr == null` на отрисовке. Снимешь один — второй удержит, и
    // подмена промолчит на верном тесте. Доказательство снимает оба;
    // повторяя его, снимайте оба.
    renderScreen();
    await switchToAll();
    expect(await screen.findByText(/Ольга · 10:00–19:00/)).toBeTruthy();

    mockedFrame.mockRejectedValue(new Error("boom"));
    fireEvent.click(screen.getByRole("tab", { name: "Один мастер" }));
    await switchToAll();

    await waitFor(() => {
      expect(screen.queryByText(/Ольга · 10:00–19:00/)).toBeNull();
    });
    expect(screen.queryByText("Хронология")).toBeNull();
  });
});
