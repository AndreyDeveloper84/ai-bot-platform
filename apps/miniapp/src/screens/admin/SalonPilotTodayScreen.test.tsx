/**
 * «Сегодня» — содержимое главного экрана пилота (DRF-1236).
 *
 * Проверяется ровно то, что построено с макета, и — отдельно — то, что
 * с него НЕ построено, потому что бэкенд этого не отдаёт: кабинет,
 * рабочие часы мастера, лента изменений Ayla, «Требует внимания».
 *
 * # Каждое отрицание идёт в паре с утверждением
 *
 * Соглашение DRF-1411: «кабинета нет» зеленеет одинаково и когда
 * кабинет правильно не нарисован, и когда экран не отрисовался вовсе.
 * Поэтому рядом с каждой проверкой отсутствия стоит проверка
 * присутствия на тех же данных: сначала «вот запись Анны», и только
 * потом «кабинета у неё нет».
 *
 * Тесты умеют падать: покажите кнопку без проверки роли — покраснеет
 * ресепшн; отрисуйте «Кабинет 1» — покраснеет пара про кабинет; уберите
 * фильтр `is_in_progress` — «Сейчас» наберёт лишнее; поставьте пояс
 * устройства вместо `day.timezone` — покраснеет время.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../../lib/admin-api")>();
  return { ...original, getSalonDay: vi.fn() };
});

import {
  getSalonDay,
  type MeResponse,
  type SalonDayResponse,
  type SalonDayVisit,
} from "../../lib/admin-api";
import { SalonPilotTodayScreen } from "./SalonPilotTodayScreen";

const mockedDay = vi.mocked(getSalonDay);

const OWNER: MeResponse = {
  user: { id: "u-1", name: "Ирина", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula" },
  role: "owner",
  capabilities: [],
  is_customer: false,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: true,
  master_id: null,
  landing_path: "/admin/team",
};

/**
 * Ресепшн. На пилот её не пускает маршрут (`canOpenSalonPilot`), но
 * экран не имеет права полагаться на чужой сторож: решение о кнопке
 * принимает он сам.
 */
const RECEPTION: MeResponse = {
  ...OWNER,
  role: "receptionist",
  is_owner: false,
  is_receptionist: true,
};

function visit(over: Partial<SalonDayVisit> = {}): SalonDayVisit {
  return {
    id: "v-1",
    service_id: "svc-1",
    start_at: "2026-08-22T06:00:00Z",
    end_at: "2026-08-22T07:00:00Z",
    duration_min: 60,
    status: "confirmed",
    service_name: "Классический массаж",
    client_first_name: "Анна",
    client_last_initial: "П.",
    is_in_progress: true,
    ...over,
  };
}

/** День с одной идущей записью у Дениса и одной будущей у Ольги. */
function busyDay(): SalonDayResponse {
  return {
    date: "2026-08-22",
    timezone: "Europe/Moscow",
    summary: { total: 2, upcoming: 1, completed: 0, released: 0 },
    masters: [
      {
        master_id: "m-1",
        name: "Денис",
        is_active: true,
        visits: [visit()],
      },
      {
        master_id: "m-2",
        name: "Ольга",
        is_active: true,
        visits: [
          visit({
            id: "v-2",
            // Далеко в будущем от любых часов машины, гоняющей тест.
            start_at: "2099-08-22T08:00:00Z",
            end_at: "2099-08-22T09:15:00Z",
            duration_min: 75,
            service_name: "Массаж лица",
            client_first_name: "Мария",
            client_last_initial: "К.",
            is_in_progress: false,
          }),
        ],
      },
    ],
    orphan_visits: [],
  };
}

function renderToday(me: MeResponse = OWNER) {
  render(
    <MemoryRouter initialEntries={["/admin/today"]}>
      <Routes>
        <Route path="/admin/today" element={<SalonPilotTodayScreen me={me} />} />
        <Route path="/admin/booking/new" element={<p>Экран новой записи</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockedDay.mockReset();
});

describe("что построено с макета", () => {
  it("«Сейчас» показывает идущую запись с интервалом, клиентом и мастером", async () => {
    mockedDay.mockResolvedValue(busyDay());
    renderToday();

    // Блок ищется по имени, а не по тексту: имя мастера стоит и в
    // строке записи, и в «Мастера сегодня» — на макете так же.
    const now = await screen.findByRole("region", { name: /Сейчас/ });
    expect(within(now).getByText("Анна П.")).toBeInTheDocument();
    expect(within(now).getByText("Классический массаж")).toBeInTheDocument();
    expect(within(now).getByText("Денис")).toBeInTheDocument();
    // 06:00 UTC — 09:00 в Москве, как на макете.
    expect(within(now).getByText("09:00 – 10:00")).toBeInTheDocument();
    expect(within(now).getByText("60 мин")).toBeInTheDocument();
  });

  it("«Дальше» показывает ближайшую запись отдельным блоком", async () => {
    mockedDay.mockResolvedValue(busyDay());
    renderToday();

    const next = await screen.findByRole("region", { name: /Дальше/ });
    expect(within(next).getByText("Мария К.")).toBeInTheDocument();
    expect(within(next).getByText("Массаж лица")).toBeInTheDocument();
    expect(within(next).getByText("Ольга")).toBeInTheDocument();
  });

  it("«Мастера сегодня» перечисляет команду и число записей", async () => {
    mockedDay.mockResolvedValue(busyDay());
    renderToday();

    expect(await screen.findByText("Мастера сегодня")).toBeInTheDocument();
    expect(screen.getByText("В команде: 2")).toBeInTheDocument();
    expect(screen.getAllByText("1 запись")).toHaveLength(2);
  });

  it("пустой день сказан словами, а не пустыми блоками", async () => {
    mockedDay.mockResolvedValue({
      date: "2026-08-22",
      timezone: "Europe/Moscow",
      summary: { total: 0, upcoming: 0, completed: 0, released: 0 },
      masters: [{ master_id: "m-1", name: "Денис", is_active: true, visits: [] }],
      orphan_visits: [],
    });
    renderToday();

    expect(await screen.findByText("Записей на сегодня нет.")).toBeInTheDocument();
    // Присутствие рядом с отсутствием: блок мастеров на месте, а блоков
    // записей нет, потому что записей нет.
    expect(screen.getByText("Мастера сегодня")).toBeInTheDocument();
    expect(screen.queryByText("Сейчас")).toBeNull();
    expect(screen.queryByText("Дальше")).toBeNull();
  });

  it("не отвечающая ручка даёт общий StateError с повтором", async () => {
    mockedDay.mockRejectedValue(new Error("boom"));
    renderToday();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Попробовать снова" });
    expect(retry).toBeInTheDocument();

    mockedDay.mockResolvedValue(busyDay());
    await userEvent.click(retry);
    expect(await screen.findByText("Анна П.")).toBeInTheDocument();
  });
});

describe("кнопка «Новая запись» решается по роли зрителя", () => {
  it("владелец её видит и попадает по ней на создание записи", async () => {
    mockedDay.mockResolvedValue(busyDay());
    renderToday(OWNER);

    const cta = await screen.findByRole("button", { name: "+ Новая запись" });
    await userEvent.click(cta);
    expect(await screen.findByText("Экран новой записи")).toBeInTheDocument();
  });

  it("ресепшн её не видит: сервер откажет в создании (require_admin_role)", async () => {
    mockedDay.mockResolvedValue(busyDay());
    renderToday(RECEPTION);

    // Присутствие сначала: экран отрисован и данные пришли...
    expect(await screen.findByText("Анна П.")).toBeInTheDocument();
    // ...и только потом — что кнопки на нём нет.
    const cta = screen.queryByRole("button", { name: "+ Новая запись" });
    expect(cta).toBeNull();
  });
});

describe("чего экран не рисует, потому что бэкенд этого не отдаёт", () => {
  it("кабинета нет ни у записи, ни у мастера", async () => {
    mockedDay.mockResolvedValue(busyDay());
    renderToday();

    // Запись на экране есть — значит, отсутствие кабинета что-то значит.
    expect(await screen.findByText("Анна П.")).toBeInTheDocument();
    const cabinet = screen.queryByText(/Кабинет/i);
    expect(cabinet).toBeNull();
  });

  it("рабочих часов и недоступности у мастера нет", async () => {
    mockedDay.mockResolvedValue(busyDay());
    renderToday();

    // Мастер на экране есть...
    const masters = await screen.findByRole("region", {
      name: /Мастера сегодня/,
    });
    expect(within(masters).getByText("Денис")).toBeInTheDocument();
    // ...а часов смены и недоступности в ответе дня нет, и экран их не
    // достраивает.
    const shift = screen.queryByText(/Недоступн|Работают/i);
    expect(shift).toBeNull();
  });

  it("ленты изменений Ayla и «Требует внимания» нет: event source не существует", async () => {
    mockedDay.mockResolvedValue(busyDay());
    renderToday();

    // Экран построен целиком...
    expect(await screen.findByText("Сейчас")).toBeInTheDocument();
    expect(screen.getByText("Мастера сегодня")).toBeInTheDocument();
    // ...и обоих backend-blocked блоков в нём нет.
    const changes = screen.queryByText(/Изменения/i);
    expect(changes).toBeNull();
    const attention = screen.queryByText(/Требует внимания/i);
    expect(attention).toBeNull();
  });

  it("телефона клиента нет нигде (DRF-1039)", async () => {
    mockedDay.mockResolvedValue(busyDay());
    const { container } = render(
      <MemoryRouter initialEntries={["/admin/today"]}>
        <SalonPilotTodayScreen me={OWNER} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(container.textContent).toContain("Анна П."));
    const phone = container.textContent?.match(/\+?\d[\d\s()-]{8,}/);
    expect(phone).toBeNull();
  });
});

describe("остаток дня раскрывается на месте", () => {
  it("«Дальше» показывает три записи, остальные — по кнопке", async () => {
    const many = busyDay();
    many.masters[1]!.visits = [1, 2, 3, 4, 5].map((n) =>
      visit({
        id: `v-next-${n}`,
        start_at: `2099-08-22T0${n}:00:00Z`,
        end_at: `2099-08-22T0${n}:30:00Z`,
        client_first_name: `Клиент${n}`,
        client_last_initial: "",
        is_in_progress: false,
      }),
    );
    mockedDay.mockResolvedValue(many);
    renderToday();

    expect(await screen.findByText("Клиент1")).toBeInTheDocument();
    expect(screen.getByText("Клиент3")).toBeInTheDocument();
    const hidden = screen.queryByText("Клиент4");
    expect(hidden).toBeNull();

    await userEvent.click(
      screen.getByRole("button", { name: "Показать ещё 2 записи" }),
    );
    expect(await screen.findByText("Клиент4")).toBeInTheDocument();
    expect(screen.getByText("Клиент5")).toBeInTheDocument();
  });
});
