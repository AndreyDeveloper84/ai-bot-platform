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
 * Здесь же — вход в пилот с моста и выход обратно (решение владельца
 * 07.09.2026): посадка осталась мостовой, а в пилот с «Настроек» ведёт
 * явная кнопка. Вход без выхода — ловушка, поэтому оба конца проверены
 * в паре.
 *
 * Тесты умеют падать: снимите `canOpenSalonPilot` с маршрута
 * `/admin/today` — покраснеют права ресепшн; поменяйте состав
 * `SALON_PILOT_TAB_SPECS` — покраснеет панель; верните пилоту
 * `AdminTabBar` — покраснеет разделение поверхностей; верните «Услуги»
 * в `ADMIN_TABS_RECEPTION` — покраснеет пин моста; снимите
 * `setBackButton`/`onBackButton` с `SalonPilotFrame` — покраснеет выход.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
import { AdminSettingsPlaceholderScreen } from "./screens/admin/AdminSettingsPlaceholderScreen";

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
  // День с одной идущей записью. До DRF-1236 здесь стояло
  // `total: 5` при пустом списке мастеров: экран показывал только
  // число из `summary`, и расхождение ничему не мешало. Теперь экран
  // рисует сами записи, и несуществующие пять записей были бы фикстурой,
  // описывающей невозможный ответ сервера.
  mockedDay.mockResolvedValue({
    date: "2026-08-22",
    timezone: "Europe/Moscow",
    summary: { total: 1, upcoming: 0, completed: 0, released: 0 },
    masters: [
      {
        master_id: "m-1",
        name: "Денис",
        is_active: true,
        visits: [
          {
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
          },
        ],
      },
    ],
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
  it("дата и записи дня приходят из ответа ручки", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/today");
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    expect(await screen.findByText("Суббота, 22 августа")).toBeInTheDocument();
    // Содержимое дня — DRF-1236; здесь проверяется только то, что ответ
    // ручки доезжает до экрана внутри каркаса. Разбор блоков «Сейчас»,
    // «Дальше» и «Мастера сегодня» живёт в
    // `screens/admin/SalonPilotTodayScreen.test.tsx`.
    expect(await screen.findByText("Анна П.")).toBeInTheDocument();
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

/**
 * Заглушка системной кнопки MAX.
 *
 * В jsdom `window.WebApp` нет вовсе, поэтому `setBackButton` и
 * `onBackButton` — пустышки, и без этой заглушки проверка выхода
 * зеленела бы, ничего не проверив.
 */
function stubMaxBackButton() {
  const handlers: Array<() => void> = [];
  const backButton = {
    show: vi.fn(),
    hide: vi.fn(),
    onClick: vi.fn((h: () => void) => {
      handlers.push(h);
    }),
    offClick: vi.fn((h: () => void) => {
      const i = handlers.indexOf(h);
      if (i >= 0) handlers.splice(i, 1);
    }),
  };
  (window as unknown as { WebApp?: unknown }).WebApp = { BackButton: backButton };
  return {
    backButton,
    /** Нажатие системной «назад» — зовём то, на что экран подписался. */
    press: () => handlers.forEach((h) => h()),
  };
}

describe("вход в пилот с моста (решение владельца 07.09.2026)", () => {
  it("владелец видит вход на «Настройках» и попадает по нему в «Сегодня»", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/settings");
    const enter = await screen.findByRole("button", {
      name: "Открыть пилотную админку",
    });
    await userEvent.click(enter);
    expect(
      await screen.findByRole("heading", { name: "Сегодня" }),
    ).toBeInTheDocument();
    // И это именно пилот, а не «День» моста.
    expect(tabLabels()).toEqual(["Сегодня", "Расписание", "Ayla"]);
  });

  it("администратор видит тот же вход", async () => {
    mockedGetMe.mockResolvedValue(ADMIN_ME);
    renderAppAt("/admin/settings");
    expect(
      await screen.findByRole("button", { name: "Открыть пилотную админку" }),
    ).toBeInTheDocument();
  });

  it("надпись не обещает содержимого пустых разделов", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/settings");
    // Присутствие: вход на месте и назвал оба пустых раздела пустыми.
    expect(
      await screen.findByText(
        /«Расписание» и «Ayla» пока пустые/,
      ),
    ).toBeInTheDocument();
  });

  it("ресепшн до «Настроек» не доходит — на адресе стоит отказ", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/admin/settings");
    // Присутствие: отказ отрисован, экран не пустой.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Раздел «Настройки» открыт владельцу и администратору/,
    );
    // И только теперь отрицание: входа в пилот на нём нет.
    expect(
      screen.queryByRole("button", { name: "Открыть пилотную админку" }),
    ).not.toBeInTheDocument();
  });

  /**
   * Проверка выше пинует СТРАЖ МАРШРУТА, а не проверку роли на экране:
   * ресепшн до placeholder-а не доходит вовсе, поэтому убери
   * `canOpenSalonPilot` из экрана — и она осталась бы зелёной. Проверено
   * саботажем: заменил условие на `true`, все 29 остались зелёными.
   *
   * Поэтому экран рендерится напрямую, в обход маршрута. Правила
   * независимы: сюда пускает `isAdminTabAllowed(me, "settings")`, в пилот
   * — `canOpenSalonPilot`. Сегодня оба закрыты для ресепшн; если
   * «Настройки» ей когда-нибудь откроют, вход в пилот уехать следом не
   * должен — он привёл бы её на экран отказа.
   */
  it("на самом экране вход скрыт от ресепшн, а не только на маршруте", () => {
    render(
      <MemoryRouter>
        <AdminSettingsPlaceholderScreen me={RECEPTION_ME} />
      </MemoryRouter>,
    );
    // Присутствие: экран отрисовался целиком, а не упал в пустоту.
    expect(screen.getByText(/Скоро здесь будут настройки/)).toBeInTheDocument();
    // Отсутствие: и входа в пилот на нём нет.
    expect(
      screen.queryByRole("button", { name: "Открыть пилотную админку" }),
    ).not.toBeInTheDocument();
  });

  it("тому же экрану с владельцем вход показывается", () => {
    render(
      <MemoryRouter>
        <AdminSettingsPlaceholderScreen me={OWNER_ME} />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("button", { name: "Открыть пилотную админку" }),
    ).toBeInTheDocument();
  });

  it("«Настройки» не потеряли своего прежнего содержимого", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/settings");
    expect(
      await screen.findByText(/Скоро здесь будут настройки/),
    ).toBeInTheDocument();
    // Панель моста под ними прежняя — вход в пилот её не тронул.
    expect(tabLabels()).toEqual([
      "День",
      "Команда",
      "Услуги",
      "Чаты",
      "Настройки",
    ]);
  });
});

describe("выход с пилота обратно на мост (не ловушка)", () => {
  afterEach(() => {
    delete (window as unknown as { WebApp?: unknown }).WebApp;
  });

  it("на пилоте показана системная «назад», и она возвращает в «Настройки»", async () => {
    const max = stubMaxBackButton();
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/today");
    await screen.findByRole("heading", { name: "Сегодня" });
    // Присутствие: кнопка показана и экран на неё подписался.
    expect(max.backButton.show).toHaveBeenCalled();
    expect(max.backButton.onClick).toHaveBeenCalled();

    max.press();

    expect(
      await screen.findByText(/Скоро здесь будут настройки/),
    ).toBeInTheDocument();
    // Ушли именно на мост: под «Настройками» его пятивкладочная панель.
    expect(tabLabels()).toEqual([
      "День",
      "Команда",
      "Услуги",
      "Чаты",
      "Настройки",
    ]);
  });

  it("выход есть на каждом из трёх разделов, а не только на «Сегодня»", async () => {
    const max = stubMaxBackButton();
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/ayla");
    await screen.findByRole("heading", { name: "Ayla" });
    expect(max.backButton.show).toHaveBeenCalled();

    max.press();

    expect(
      await screen.findByText(/Скоро здесь будут настройки/),
    ).toBeInTheDocument();
  });
});
