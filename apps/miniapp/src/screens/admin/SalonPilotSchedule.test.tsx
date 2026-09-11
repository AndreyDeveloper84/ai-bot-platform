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
    getMasterSchedule: vi.fn(),
    getMasterExceptions: vi.fn(),
    getScheduleImpact: vi.fn(),
  };
});

import {
  getMasterDaySchedule,
  getMasterExceptions,
  getMasterSchedule,
  getSalonDay,
  getSalonDayFrame,
  getScheduleImpact,
  type MasterDay,
  type MasterExceptions,
  type MasterSchedule,
  type MeResponse,
  type SalonDayFrame,
  type SalonDayResponse,
  type SalonDayVisit,
  type ScheduleImpact,
} from "../../lib/admin-api";
import { SalonPilotScheduleScreen } from "./SalonPilotScheduleScreen";

const mockedDay = vi.mocked(getSalonDay);
const mockedSchedule = vi.mocked(getMasterDaySchedule);
const mockedFrame = vi.mocked(getSalonDayFrame);
const mockedWeek = vi.mocked(getMasterSchedule);
const mockedAssigned = vi.mocked(getMasterExceptions);
const mockedImpact = vi.mocked(getScheduleImpact);

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

/**
 * Недельный график в форме ответа `GET masters/<id>/schedule/`.
 *
 * По умолчанию — подтверждённый для ЭТИХ часов: положительный контроль, без
 * которого проверки «не подтверждено» зеленели бы на экране, где подтверждения
 * не показывают вовсе.
 */
function weekSchedule(patch: Partial<MasterSchedule> = {}): MasterSchedule {
  return {
    source: "ayla",
    has_working_day: true,
    days: [0, 1, 2, 3, 4, 5, 6].map((i) => ({
      day_of_week: i,
      is_working_day: i !== 6,
      start_time: i !== 6 ? "10:00" : null,
      end_time: i !== 6 ? "19:00" : null,
      break_start: null,
      break_end: null,
    })),
    confirmation: {
      confirmed_at: "2026-09-09T12:00:00+03:00",
      confirmed_by: { id: "u-1", name: "Карина" },
      is_current: true,
      fingerprint: "abc",
      block: null,
    },
    ...patch,
  };
}

/**
 * Назначенное в форме ответа `GET masters/<id>/exceptions/`.
 *
 * По умолчанию — всё разобрано и пусто: положительный контроль для проверок
 * «показано не всё», которые иначе зеленели бы на экране, где раздела нет.
 */
function assignedNothing(
  patch: Partial<MasterExceptions> = {},
): MasterExceptions {
  const empty = { state: "none" as const, rows: [], seen_fields: [] };
  return {
    from: "2026-09-10",
    to: "2026-09-16",
    exceptions: empty,
    time_off: empty,
    closures: empty,
    unreadable_lists: [],
    writable: false,
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
    mockedWeek.mockResolvedValue(weekSchedule());
    mockedAssigned.mockResolvedValue(assignedNothing());
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
      days: [
        masterDay({ is_off_day: true, working_hours: null, free_windows: [] }),
      ],
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
    fireEvent.change(screen.getByLabelText("Мастер"), {
      target: { value: "m-2" },
    });

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

    expect(
      await screen.findByText("Запись в 20:30 вне рабочих часов"),
    ).toBeTruthy();
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
    mockedWeek.mockResolvedValue(weekSchedule());
    mockedAssigned.mockResolvedValue(assignedNothing());
  });

  it("параллельные записи стоят под одним временем, а не в четырёх колонках", async () => {
    const base = salonDay();
    mockedDay.mockResolvedValue({
      ...base,
      masters: [
        { ...base.masters[0]!, visits: [visit({ id: "v-1" })] },
        {
          ...base.masters[1]!,
          visits: [
            visit({
              id: "v-2",
              client_first_name: "Пётр",
              service_name: "Бритьё",
            }),
          ],
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

  it("часы берутся со смещением салона, а не пересчитываются в пояс браузера", async () => {
    // Проверка НЕ ЗАВИСИТ от пояса, в котором идёт прогон, и в этом весь
    // смысл. Прежний код печатал часы через `new Date`, то есть в поясе
    // браузера; местный прогон в MSK и провод с «+03:00» совпадали, и тест
    // был зелёным на сломанном. Красноту нашёл CI — он идёт в UTC.
    //
    // Здесь два визита названы на ОДНО стенное время салона, но с разными
    // смещениями. Пересчёт в любой единый пояс разведёт их на шесть часов и
    // даст два заголовка; чтение времени салона даст один.
    const base = salonDay();
    mockedDay.mockResolvedValue({
      ...base,
      masters: [
        {
          ...base.masters[0]!,
          visits: [visit({ id: "v-1", start_at: "2026-09-10T14:00:00+03:00" })],
        },
        {
          ...base.masters[1]!,
          visits: [
            visit({
              id: "v-2",
              start_at: "2026-09-10T14:00:00+09:00",
              client_first_name: "Пётр",
              service_name: "Бритьё",
            }),
          ],
        },
      ],
    });

    renderScreen();
    await switchToAll();

    // Присутствие впереди отсутствия: сперва убеждаемся, что обе записи
    // вообще отрисованы, иначе «одна группа» значило бы «ничего не показано».
    expect(await screen.findByText(/Ольга · Стрижка · Мария К\./)).toBeTruthy();
    expect(screen.getByText(/Денис · Бритьё · Пётр К\./)).toBeTruthy();

    // Считаем ЗАГОЛОВКИ групп, а не одно значение. Первая редакция проверяла
    // `getAllByText("14:00")).toHaveLength(1)` — и была зелёной при обеих
    // реализациях: пересчёт уводил второй визит в «08:00», заголовков
    // становилось два, но «14:00» по-прежнему один. Подмена молчала, и
    // молчание это было дефектом теста, а не кода.
    expect(screen.getAllByText(/^\d{2}:\d{2}$/)).toHaveLength(1);
  });

  it("неразобранный список назван словами, а не показан пустотой", async () => {
    // Форма непустой строки breaks не проверена ничем: перерывов не завёл
    // никто. Пока это так, «не разобрал» обязано звучать иначе, чем «нет».
    mockedFrame.mockResolvedValue(
      frame({
        masters: [
          frameMaster({
            breaks: {
              state: "unreadable",
              rows: [],
              seen_fields: ["from_minute"],
            },
          }),
        ],
        unreadable_lists: ["breaks"],
      }),
    );

    renderScreen();
    await switchToAll();

    expect(
      await screen.findByText(/не удалось разобрать перерывы/),
    ).toBeTruthy();
  });

  it("часы, которых сервер не разобрал, не выдаются за смену", async () => {
    mockedFrame.mockResolvedValue(
      frame({
        masters: [
          frameMaster({
            working_intervals: {
              state: "unreadable",
              rows: [],
              seen_fields: ["shift"],
            },
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

describe("Отметка «сейчас по плану» — утверждённая семантика DRF-1237", () => {
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
    mockedWeek.mockResolvedValue(weekSchedule());
    mockedAssigned.mockResolvedValue(assignedNothing());
  });

  it("стоит у записи, чей интервал накрывает сейчас, и только у неё", async () => {
    // Заморозка: `scheduled_start <= now < scheduled_end`. Считает это
    // сервер (`is_in_progress`), клиент не заводит своё «сейчас».
    //
    // Отрицательный контроль в этом же тесте обязателен: проверка «отметка
    // есть» без «у соседа её нет» зеленела бы и на отметке у всех подряд.
    const base = salonDay();
    mockedDay.mockResolvedValue({
      ...base,
      masters: [
        {
          ...base.masters[0]!,
          visits: [visit({ id: "v-now", is_in_progress: true })],
        },
        {
          ...base.masters[1]!,
          visits: [
            visit({
              id: "v-later",
              client_first_name: "Пётр",
              service_name: "Бритьё",
              is_in_progress: false,
            }),
          ],
        },
      ],
    });

    renderScreen();
    await switchToAll();

    expect(
      await screen.findByText(/Ольга · Стрижка · Мария К\. · сейчас по плану/),
    ).toBeTruthy();
    expect(screen.getByText(/Денис · Бритьё · Пётр К\.$/)).toBeTruthy();
  });

  it("не обещает, что клиент пришёл", async () => {
    // Заморозка прямо запрещает читать индикатор как приход клиента, оплату
    // или статус in_progress. Слово «идёт» обещало бы ровно это, поэтому
    // формулировка говорит про план, а не про факт.
    const base = salonDay();
    mockedDay.mockResolvedValue({
      ...base,
      masters: [
        {
          ...base.masters[0]!,
          visits: [visit({ id: "v-now", is_in_progress: true })],
        },
        { ...base.masters[1]!, visits: [] },
      ],
    });

    renderScreen();
    await switchToAll();

    await screen.findByText(/сейчас по плану/);
    expect(screen.queryByText(/· идёт$/)).toBeNull();
  });
});

/**
 * График и поручительство за него — срез B1.
 *
 * §29.5: «Неизвестное расписание нельзя считать ни свободным, ни занятым.
 * Третье состояние, а не подстановка значения по умолчанию», и там же —
 * «заглушка 10:00–19:00 подтверждением не является».
 *
 * Отсюда четыре состояния, и ни одно не сворачивается в остальные. Тесты
 * держат именно неразличимость соседей: «устарело» рядом с «никогда» и
 * «не прочитано» рядом с «не подтверждено» — те две пары, которые проще
 * всего склеить в булево и потерять действие, которым они отличаются.
 */
describe("Расписание салона — график мастера и его подтверждение", () => {
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
    mockedWeek.mockResolvedValue(weekSchedule());
    mockedAssigned.mockResolvedValue(assignedNothing());
  });

  it("показывает неделю целиком, включая выходной", async () => {
    renderScreen();

    expect(await screen.findByText("График на неделю")).toBeTruthy();
    expect(screen.getByText("Вс")).toBeTruthy();
    expect(screen.getByText("выходной")).toBeTruthy();
  });

  it("подтверждённое расписание названо подтверждённым", async () => {
    // Положительный контроль: без него три следующие проверки зеленели бы и
    // на экране, где подтверждение не показывают вовсе.
    renderScreen();

    expect(
      await screen.findByText(/Расписание подтверждено 9 сентября · Карина/),
    ).toBeTruthy();
  });

  it("неподтверждённое названо словами, а не показано пустотой", async () => {
    mockedWeek.mockResolvedValue(
      weekSchedule({
        confirmation: {
          confirmed_at: null,
          confirmed_by: null,
          is_current: false,
          fingerprint: "abc",
          block: null,
        },
      }),
    );

    renderScreen();

    expect(await screen.findByText(/Расписание не подтверждено/)).toBeTruthy();
  });

  it("устаревшее подтверждение — не то же, что неподтверждённое", async () => {
    // Разница не косметическая: «никогда не заверяли» лечится первым
    // подтверждением, «часы изменились после» — повторным, и склеенные в
    // булево они потеряли бы ровно то действие, которым отличаются.
    mockedWeek.mockResolvedValue(
      weekSchedule({
        confirmation: {
          confirmed_at: "2026-09-08T12:00:00+03:00",
          confirmed_by: { id: "u-1", name: "Карина" },
          is_current: false,
          fingerprint: "def",
          block: null,
        },
      }),
    );

    renderScreen();

    expect(
      await screen.findByText(/Часы изменились после подтверждения/),
    ).toBeTruthy();
    expect(screen.queryByText(/Расписание не подтверждено/)).toBeNull();
  });

  it("непрочитанное состояние не выдаётся за неподтверждённое", async () => {
    // Самая соблазнительная склейка: запрос не дошёл — значит «не
    // подтверждено». Нет: неизвестное нельзя подставлять значением по
    // умолчанию НИ В ОДНУ сторону, иначе салон пойдёт заверять расписание,
    // которое, возможно, уже заверено.
    mockedWeek.mockRejectedValue(new Error("boom"));

    renderScreen();

    expect(
      await screen.findByText(/Состояние подтверждения не прочитано/),
    ).toBeTruthy();
    expect(screen.queryByText(/Расписание не подтверждено/)).toBeNull();
  });
});

/**
 * Показ назначенного — DRF-1240, читаемая половина.
 *
 * Держит три вещи:
 *
 * * назначенное видно вообще — сегодня его не показывает ни один экран;
 * * «ничего не назначено» — утверждение, и произносить его можно ТОЛЬКО
 *   когда разобрано всё. Сказать его поверх неразобранного списка значит
 *   поручиться за то, чего не читал;
 * * действий нет, и это говорит сервер полем `writable`, а не экран.
 */
describe("Расписание салона — что уже назначено мастеру", () => {
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
    mockedWeek.mockResolvedValue(weekSchedule());
    mockedAssigned.mockResolvedValue(assignedNothing());
  });

  it("показывает исключение, недоступность и закрытие салона", async () => {
    mockedAssigned.mockResolvedValue(
      assignedNothing({
        exceptions: {
          state: "parsed",
          rows: [
            {
              id: "e1",
              date: "2026-09-12",
              is_working_day: true,
              start: "12:00",
              end: "16:00",
            },
          ],
          seen_fields: [],
        },
        time_off: {
          state: "parsed",
          rows: [
            {
              id: "o1",
              start_at: "2026-09-13T10:00:00+03:00",
              end_at: "2026-09-13T14:00:00+03:00",
              reason: "учёба",
            },
          ],
          seen_fields: [],
        },
        closures: {
          state: "parsed",
          rows: [
            {
              id: "c1",
              date: "2026-09-14",
              start: null,
              end: null,
              reason: "санитарный день",
            },
          ],
          seen_fields: [],
        },
      }),
    );

    renderScreen();

    expect(await screen.findByText("12 сентября · 12:00–16:00")).toBeTruthy();
    expect(
      screen.getByText(/13 сентября · 10:00–14:00 · недоступна · учёба/),
    ).toBeTruthy();
    expect(
      screen.getByText(/14 сентября · салон закрыт · санитарный день/),
    ).toBeTruthy();
  });

  it("время недоступности показано как прислал салон, а не в поясе браузера", async () => {
    // Провод несёт смещение САЛОНА. Пересчёт через Date показал бы
    // администратору в другом поясе сдвинутое время назначения.
    mockedAssigned.mockResolvedValue(
      assignedNothing({
        time_off: {
          state: "parsed",
          rows: [
            {
              id: "o1",
              start_at: "2026-09-13T09:30:00+03:00",
              end_at: "2026-09-13T11:00:00+03:00",
              reason: "",
            },
          ],
          seen_fields: [],
        },
      }),
    );

    renderScreen();

    expect(
      await screen.findByText(/13 сентября · 09:30–11:00 · недоступна/),
    ).toBeTruthy();
  });

  it("нерабочий день не показывает часы, даже если они пришли", async () => {
    // Первая версия этого теста была украшением, и поймала её подмена:
    // фикстура приходила с `start: null`, поэтому снятие фронтового условия
    // ничего не меняло — предмет держал сервер, который часы уже обнулил.
    //
    // Инвариант держат ДВОЕ: `_exception_row` на сервере и это условие на
    // экране. Чтобы проверялось именно второе, данные здесь ВРАЖДЕБНЫЕ —
    // нерабочий день с часами. Так выглядел бы ответ, если серверную
    // половину однажды снимут, и экран обязан не поверить часам, которые
    // тот же ответ объявил нерабочими.
    mockedAssigned.mockResolvedValue(
      assignedNothing({
        exceptions: {
          state: "parsed",
          rows: [
            {
              id: "e1",
              date: "2026-09-12",
              is_working_day: false,
              start: "12:00",
              end: "16:00",
            },
          ],
          seen_fields: [],
        },
      }),
    );

    renderScreen();

    expect(await screen.findByText("12 сентября · не работает")).toBeTruthy();
    expect(screen.queryByText(/12:00–16:00/)).toBeNull();
  });

  it("«ничего не назначено» говорится только когда разобрано всё", async () => {
    // Положительный контроль стоит рядом: сперва убеждаемся, что фраза
    // вообще появляется на пустом разобранном ответе.
    renderScreen();
    expect(
      await screen.findByText("На ближайшие дни ничего не назначено."),
    ).toBeTruthy();
  });

  it("поверх неразобранного списка «ничего не назначено» не произносится", async () => {
    // Это и есть предмет: пустой список и непрочитанный список выглядят
    // одинаково, и утверждение о пустоте поверх второго — обещание за то,
    // чего мы не читали.
    mockedAssigned.mockResolvedValue(
      assignedNothing({
        time_off: {
          state: "unreadable",
          rows: [],
          seen_fields: ["from", "to"],
        },
        unreadable_lists: ["time_off"],
      }),
    );

    renderScreen();

    expect(
      await screen.findByText(/не удалось разобрать недоступность/),
    ).toBeTruthy();
    expect(
      screen.queryByText("На ближайшие дни ничего не назначено."),
    ).toBeNull();
  });
});

/**
 * §142, срез В — «показать, не применять».
 *
 * Держится три вещи: салон видит, кого затронет закрытие; окно уходит на
 * сервер датой и часами салона, а не ISO, посчитанным в браузере; и на
 * экране нет кнопки «Закрыть» — есть подсказка, куда идти, потому что сервер
 * сказал writable: false.
 */
function impactOf(patch: Partial<ScheduleImpact> = {}): ScheduleImpact {
  return {
    start_at: "2026-09-15T10:00:00+03:00",
    end_at: "2026-09-15T14:00:00+03:00",
    timezone: "Europe/Moscow",
    bookings: {
      state: "parsed",
      rows: [
        {
          appointment_id: "a-1",
          start_local: "2026-09-15T11:00:00+03:00",
          end_local: "2026-09-15T12:00:00+03:00",
          service_name: "Стрижка",
          status: "confirmed",
          payment_status: "paid",
          refund_percent_if_cancelled: 100,
        },
        {
          appointment_id: "a-2",
          start_local: "2026-09-15T12:30:00+09:00",
          end_local: "2026-09-15T13:30:00+09:00",
          service_name: null,
          status: "confirmed",
          payment_status: "unpaid",
          refund_percent_if_cancelled: null,
        },
      ],
      seen_fields: [],
      unreadable_rows: 0,
    },
    writable: false,
    next_step: "pro_app",
    ...patch,
  };
}

async function askForImpact(date = "2026-09-15", from = "10:00", to = "14:00") {
  fireEvent.change(await screen.findByLabelText("Дата закрытия"), {
    target: { value: date },
  });
  fireEvent.change(screen.getByLabelText("Закрыть с"), {
    target: { value: from },
  });
  fireEvent.change(screen.getByLabelText("Закрыть до"), {
    target: { value: to },
  });
  fireEvent.click(screen.getByRole("button", { name: "Показать записи" }));
}

describe("Закрыть время — кого затронет (§142, срез В)", () => {
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
    mockedWeek.mockResolvedValue(weekSchedule());
    mockedAssigned.mockResolvedValue(assignedNothing());
    mockedImpact.mockResolvedValue(impactOf());
  });

  it("окно уходит датой и часами салона, а не ISO из браузера", async () => {
    renderScreen();
    await askForImpact("2026-09-15", "10:00", "14:00");

    await waitFor(() => expect(mockedImpact).toHaveBeenCalledTimes(1));
    const [masterId, params] = mockedImpact.mock.calls[0]!;
    expect(masterId).toBe("m-1");
    expect(params).toEqual({ date: "2026-09-15", from: "10:00", to: "14:00" });
  });

  it("показывает затронутые записи со временем салона, не браузера", async () => {
    renderScreen();
    await askForImpact();

    expect(await screen.findByText(/затронет 2 записи/)).toBeTruthy();
    expect(
      screen.getByText(/11:00–12:00 · Стрижка · при отмене вернётся 100%/),
    ).toBeTruthy();
    // Вторая запись пришла со смещением +09:00. Пересчёт в любой единый пояс
    // сдвинул бы её; чтение времени салона оставляет 12:30.
    expect(screen.getByText(/12:30–13:30 · услуга не названа/)).toBeTruthy();
  });

  it("кнопки «Закрыть» нет — есть подсказка, куда идти", async () => {
    renderScreen();
    await askForImpact();

    // Присутствие впереди отсутствия: ответ показан, значит и подсказка при нём.
    expect(await screen.findByText(/Pro App под своим именем/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Закрыть/ })).toBeNull();
  });

  it("никого не затронет — сказано словами, а не пустотой", async () => {
    mockedImpact.mockResolvedValue(
      impactOf({
        bookings: {
          state: "none",
          rows: [],
          seen_fields: [],
          unreadable_rows: 0,
        },
      }),
    );
    renderScreen();
    await askForImpact();

    expect(
      await screen.findByText(/записей нет, закрытие никого не затронет/),
    ).toBeTruthy();
  });

  it("выброшенная строка названа рядом с показанными", async () => {
    const one = impactOf();
    one.bookings = {
      ...one.bookings,
      rows: one.bookings.rows.slice(0, 1),
      unreadable_rows: 1,
    };
    mockedImpact.mockResolvedValue(one);
    renderScreen();
    await askForImpact();

    expect(await screen.findByText(/затронет 1 запись/)).toBeTruthy();
    expect(
      screen.getByText(/Показано не всё: 1 запись не удалось разобрать/),
    ).toBeTruthy();
  });

  it("смена мастера снимает чужой предпросмотр", async () => {
    renderScreen();
    await askForImpact();
    expect(await screen.findByText(/затронет 2 записи/)).toBeTruthy();

    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "m-2" },
    });

    await waitFor(() =>
      expect(screen.queryByText(/затронет 2 записи/)).toBeNull(),
    );
  });
});
