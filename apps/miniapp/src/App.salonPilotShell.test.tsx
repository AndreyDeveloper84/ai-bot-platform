/**
 * DRF-1235 — каркас пилотной салонной админки.
 *
 * Проверяется ровно то, что построено: три адреса существуют, панель
 * под ними одна и та же и состоит из трёх вкладок, поверхность закрыта
 * всем, кроме владельца и администратора, а мост не сдвинулся —
 * «День · Команда · Услуги · Чаты · Настройки» у владельца и
 * «День · Команда» у ресепшн (DRF-1552).
 *
 * Права здесь НЕ «повторяют бэкенд»: `GET /api/v1/admin/day/` с
 * DRF-1552 ресепшн открыт (`require_admin_or_reception_read`). Пилот
 * закрыт решением о поверхности, и тест проверяет именно его.
 *
 * # Почему каждое отрицание идёт в паре с утверждением
 *
 * Соглашение DRF-1411: «панели нет» и «экран не открылся» зеленеют
 * одинаково и когда поверхность закрыта правильно, и когда она
 * сломалась целиком. Поэтому рядом с каждой проверкой отсутствия стоит
 * проверка присутствия на тех же данных: сначала «вот поверхность», и
 * только потом «вот чего в ней нет».
 *
 * Тесты умеют падать: снимите `canOpenSalonPilot` с маршрута
 * `/admin/today` — покраснеют права ресепшн; поменяйте состав
 * `SALON_PILOT_TAB_SPECS` — покраснеет панель; верните пилоту
 * `AdminTabBar` — покраснеет разделение поверхностей; верните «Услуги»
 * в `ADMIN_TABS_RECEPTION` — покраснеет пин моста.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return {
    ...original,
    getMe: vi.fn(),
    getSalonDay: vi.fn(),
    listMasters: vi.fn(),
    getAvailabilityRequests: vi.fn(),
  };
});

import {
  getAvailabilityRequests,
  getMe,
  getSalonDay,
  listMasters,
  type MeResponse,
} from "./lib/admin-api";
import { SALON_PILOT_PATHS, SALON_PILOT_TABS } from "./lib/salon-pilot";
import { SALON_PILOT_TAB_SPECS } from "./components/SalonPilotTabBar";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDay = vi.mocked(getSalonDay);
const mockedMasters = vi.mocked(listMasters);
const mockedRequests = vi.mocked(getAvailabilityRequests);

const BASE_ME: MeResponse = {
  user: { id: "u-1", name: "Ирина", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula" },
  role: "receptionist",
  capabilities: [],
  is_customer: false,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: false,
  master_id: null,
  landing_path: "/admin/team",
};

const OWNER_ME: MeResponse = { ...BASE_ME, role: "owner", is_owner: true };
const ADMIN_ME: MeResponse = { ...BASE_ME, role: "admin", is_admin: true };
const RECEPTION_ME: MeResponse = { ...BASE_ME, is_receptionist: true };
/** Владелец, которому заодно проставили приёмную роль — он остаётся владельцем. */
const OWNER_ALSO_RECEPTION: MeResponse = { ...OWNER_ME, is_receptionist: true };

function renderAppAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function tabLabels(): string[] {
  const bar = screen.getByRole("navigation", { name: "Основная навигация" });
  return Array.from(bar.querySelectorAll("button")).map(
    (b) => b.getAttribute("aria-label") ?? "",
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedDay.mockResolvedValue({
    date: "2026-08-22",
    timezone: "Europe/Moscow",
    summary: { total: 5, upcoming: 3, completed: 2, released: 0 },
    masters: [],
    orphan_visits: [],
  });
  mockedMasters.mockResolvedValue({
    items: [],
    next_cursor: null,
    total_count: 0,
  });
  mockedRequests.mockResolvedValue({ items: [], next_cursor: null });
});

describe("три маршрута пилота существуют (DRF-1235)", () => {
  it("«Сегодня» открывается по своему адресу", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/today");
    expect(
      await screen.findByRole("heading", { name: "Сегодня" }),
    ).toBeInTheDocument();
  });

  it("«Расписание» открывается по своему адресу", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/schedule");
    expect(
      await screen.findByRole("heading", { name: "Расписание" }),
    ).toBeInTheDocument();
  });

  it("«Ayla» открывается по своему адресу", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/ayla");
    expect(
      await screen.findByRole("heading", { name: "Ayla" }),
    ).toBeInTheDocument();
  });

  it("адреса панели совпадают с адресами маршрутов", () => {
    // Панель выписывает адреса строками, чтобы их видел текстовый линт
    // числа колонок. Здесь эта копия сверяется с источником маршрутов.
    expect(SALON_PILOT_TAB_SPECS.map((t) => t.key)).toEqual([
      ...SALON_PILOT_TABS,
    ]);
    expect(SALON_PILOT_TAB_SPECS.map((t) => t.to)).toEqual(
      SALON_PILOT_TABS.map((key) => SALON_PILOT_PATHS[key]),
    );
  });
});

describe("нижняя навигация пилота (DRF-1235)", () => {
  it("три вкладки в порядке «Сегодня · Расписание · Ayla»", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/today");
    await screen.findByRole("heading", { name: "Сегодня" });
    expect(tabLabels()).toEqual(["Сегодня", "Расписание", "Ayla"]);
  });

  it("панель растянута на свои три колонки", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/schedule");
    await screen.findByRole("heading", { name: "Расписание" });
    const bar = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(bar.getAttribute("style")).toContain("repeat(3, 1fr)");
  });

  it("панель отмечает открытый раздел, и только его", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/ayla");
    await screen.findByRole("heading", { name: "Ayla" });
    const bar = screen.getByRole("navigation", { name: "Основная навигация" });
    const current = Array.from(bar.querySelectorAll("[aria-current]"));
    // Сначала — что отмеченная вкладка вообще есть.
    expect(current).toHaveLength(1);
    expect(current[0]?.getAttribute("aria-label")).toBe("Ayla");
  });

  it("панель пилота — не панель моста: разделов моста в ней нет", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/today");
    await screen.findByRole("heading", { name: "Сегодня" });
    // Присутствие: панель пилота на месте и полна.
    expect(tabLabels()).toEqual(["Сегодня", "Расписание", "Ayla"]);
    // Отсутствие: значит, «Команды» и «Услуг» здесь действительно нет.
    expect(
      screen.queryByRole("button", { name: "Команда" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Услуги" }),
    ).not.toBeInTheDocument();
  });
});

describe("права на пилот повторяют бэкенд (DRF-1235)", () => {
  it("администратор входит так же, как владелец", async () => {
    mockedGetMe.mockResolvedValue(ADMIN_ME);
    renderAppAt("/admin/today");
    expect(
      await screen.findByRole("heading", { name: "Сегодня" }),
    ).toBeInTheDocument();
  });

  it("ресепшн получает честный отказ, а не пустой экран", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/admin/today");
    // Отказ рисует общий `AdminSectionDeniedScreen`, тот же, что на
    // «Чатах», «Настройках» и «Услугах», и называет раздел по имени.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Раздел «Сегодня» открыт владельцу и администратору/,
    );
    // Выход есть, и он ведёт на мост.
    expect(
      screen.getByRole("button", { name: "Вернуться в «День»" }),
    ).toBeInTheDocument();
    // И только теперь отрицание — и оно НЕ про 403.
    //
    // С DRF-1552 `GET /api/v1/admin/day/` ресепшн пускает
    // (`require_admin_or_reception_read`), то есть ручка ответила бы ей
    // данными. Закрывает её решение о поверхности, и проверяется именно
    // оно: запроса не случилось вовсе.
    expect(mockedDay).not.toHaveBeenCalled();
  });

  it("ресепшн закрыты все три адреса, а не только первый", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/admin/schedule");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Раздел «Расписание» открыт владельцу и администратору/,
    );
    // Заголовком экраны не различить: общий отказ ставит в `h1` имя
    // раздела, то есть тоже «Расписание». Различает содержимое и
    // панель — их у отказа нет, а у экрана пилота есть.
    expect(
      screen.queryByText(/Расписание салона сюда пока не приходит/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Ayla" }),
    ).not.toBeInTheDocument();
    // Под отказом — панель моста, уже урезанная до двух вкладок.
    expect(tabLabels()).toEqual(["День", "Команда"]);
  });

  it("и «Ayla» тоже — отказ называет её своим именем", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/admin/ayla");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Раздел «Ayla» открыт владельцу и администратору/,
    );
  });

  it("владелец с приёмной ролью сверху остаётся владельцем", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ALSO_RECEPTION);
    renderAppAt("/admin/today");
    expect(
      await screen.findByRole("heading", { name: "Сегодня" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("«Сегодня» показывает то, что вернул сервер (DRF-1235)", () => {
  it("дата и число записей приходят из ответа ручки дня", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/today");
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    expect(await screen.findByText("Суббота, 22 августа")).toBeInTheDocument();
    expect(
      await screen.findByText(/Записей на сегодня: 5/),
    ).toBeInTheDocument();
  });

  it("пустой день назван пустым, а не спрятан", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    mockedDay.mockResolvedValue({
      date: "2026-08-22",
      timezone: "Europe/Moscow",
      summary: { total: 0, upcoming: 0, completed: 0, released: 0 },
      masters: [],
      orphan_visits: [],
    });
    renderAppAt("/admin/today");
    expect(
      await screen.findByText("Записей на сегодня нет."),
    ).toBeInTheDocument();
  });

  it("отказ ручки показан общим StateError с повтором", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    mockedDay.mockRejectedValue(new Error("boom"));
    renderAppAt("/admin/today");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Не получилось загрузить/,
    );
    expect(
      screen.getByRole("button", { name: "Попробовать снова" }),
    ).toBeInTheDocument();
  });

  it("название салона в шапке — из ответа /me, а не зашито", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/today");
    expect(await screen.findByText("Формула тела")).toBeInTheDocument();
  });
});

describe("«Расписание» и «Ayla» не обещают несуществующего (DRF-1235)", () => {
  it("«Расписание» говорит, что данных нет, и никуда не ходит", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/schedule");
    expect(
      await screen.findByText(/Расписание салона сюда пока не приходит/),
    ).toBeInTheDocument();
    // Присутствие проверено строкой выше — теперь отрицание осмысленно.
    expect(mockedDay).not.toHaveBeenCalled();
    expect(mockedRequests).not.toHaveBeenCalled();
  });

  it("«Ayla» говорит то же и не зовёт мастерского ассистента", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/ayla");
    expect(
      await screen.findByText(/Разговор салона с Ayla сюда пока не приходит/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Скоро/)).not.toBeInTheDocument();
  });
});

describe("мост из пяти вкладок не сдвинулся (DRF-1235)", () => {
  it("владелец на «Дне» видит прежние пять вкладок", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/day");
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    expect(tabLabels()).toEqual([
      "День",
      "Команда",
      "Услуги",
      "Чаты",
      "Настройки",
    ]);
  });

  it("у ресепшн на мосту прежние две вкладки: «День» и «Команда»", async () => {
    // Пин по именам, а не по числу. DRF-1552 убрал у ресепшн «Услуги»,
    // и счётчик «три» здесь уже однажды устарел за сутки; список имён
    // покраснеет и на возврате «Услуг», и на подмешивании пилота.
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/admin/day");
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    expect(tabLabels()).toEqual(["День", "Команда"]);
  });

  it("вход без адреса по-прежнему ведёт владельца на «Команду»", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/");
    // Присутствие: посадка сработала и сходила за ростером.
    await waitFor(() => expect(mockedMasters).toHaveBeenCalled());
    // Отсутствие: значит, на пилот его не унесло.
    expect(
      screen.queryByRole("button", { name: "Расписание" }),
    ).not.toBeInTheDocument();
  });
});
