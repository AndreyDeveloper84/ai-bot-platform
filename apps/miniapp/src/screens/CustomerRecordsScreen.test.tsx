/**
 * «Записи» клиента — список по макету «Записи.png» (DRF-2952), кадры 1, 2,
 * 12, 14–16. Данные — настоящий `GET /bookings/list`: слой HTTP (`../lib/api`)
 * подменён, фикстуры — дословные строки `BookingItem`.
 *
 * Здесь закреплено и то, что перенесено с макета, и то, что осталось вопреки
 * ему (правило DRF-2948 — работающее не убирают молча): салон в строке
 * мастера, цена без «0 ₽», «Оценить визит», офлайн, постраничная загрузка,
 * фильтр прошедших, панель при любом исходе загрузки.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { primeDisplayName } from "../components/CustomerAvatarEntry";

// Экран спрашивает decision-context (приглашение к цели). Подмена — чтобы
// тест не ходил в сеть; без `missing` приглашения нет и на проверки оно не
// влияет (его собственные тесты — `CustomerRecordsScreen.goalEntry`).
vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return {
    ...original,
    fetchDecisionContext: vi.fn().mockResolvedValue({
      version: 1,
      known: { goal: null },
      missing: [],
      suggestions: [],
      intents: [],
    }),
  };
});

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    fetchMyBookings: vi.fn(),
  };
});

import { ApiError, fetchMyBookings, type BookingItem } from "../lib/api";
import { OPEN_FROM_MAX_COPY } from "../lib/auth-error-copy";
import { SUPPORT_DEEPLINK } from "../lib/customer-profile";
import { formatRecordWhen } from "../lib/format";
import { CustomerRecordsScreen, RECORDS_COPY } from "./CustomerRecordsScreen";

// Дверь в профиль без пропа спрашивает имя у `/me`; набор ручку не подменяет,
// поэтому имя засевается явно (DRF-2523).
beforeEach(() => {
  primeDisplayName("Тест Тестов");
});

const mockedList = vi.mocked(fetchMyBookings);

function isoInHours(hours: number): string {
  return new Date(Date.now() + hours * 3_600_000).toISOString();
}

function booking(partial: Partial<BookingItem> & Pick<BookingItem, "id">): BookingItem {
  return {
    status: "confirmed",
    service_id: "svc-1",
    service_name: "Маникюр",
    master_id: "mst-1",
    master_name: "Анна Соколова",
    visit_at: isoInHours(48),
    duration_min: 90,
    cancel_requested_at: null,
    undo_window_seconds: 300,
    cancellable: true,
    reschedulable: true,
    rating: null,
    can_rate: false,
    address: null,
    ...partial,
  };
}

const UPCOMING: BookingItem[] = [
  booking({ id: "b-1", visit_at: isoInHours(20), address: "Пенза, Московская, 12" }),
  booking({ id: "b-2", service_name: "Массаж", visit_at: isoInHours(70) }),
];

const past = (id: string, hours: number, more: Partial<BookingItem> = {}) =>
  booking({ id, visit_at: isoInHours(hours), cancellable: false, reschedulable: false, ...more });

const HISTORY: BookingItem[] = [
  past("b-h1", -30, { can_rate: true, service_name: "Окрашивание" }),
  past("b-h2", -80, { status: "cancelled", service_name: "Брови" }),
  past("b-h3", -100, { status: "rescheduled", service_name: "Педикюр" }),
];

function mockLists(upcoming: BookingItem[] = UPCOMING, history: BookingItem[] = HISTORY) {
  mockedList.mockImplementation((params) =>
    Promise.resolve({ items: params?.past ? history : upcoming, next_cursor: null }),
  );
}

function BookingProbe() {
  const { bookingId } = useParams();
  return <div>BOOKING-{bookingId}</div>;
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/records"]}>
      <Routes>
        <Route path="/customer/records" element={<CustomerRecordsScreen />} />
        <Route path="/customer/records/:bookingId" element={<BookingProbe />} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
        <Route path="/customer/ayla" element={<div>AYLA-PROBE</div>} />
        <Route path="/feedback/:bookingId" element={<BookingProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function setOnLine(value: boolean) {
  Object.defineProperty(window.navigator, "onLine", { value, configurable: true });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
  setOnLine(true);
});

const tab = (name: string) => screen.findByRole("tab", { name });
/** Карточка записи — кнопка, названная услугой. */
const card = (service: string) => screen.findByRole("button", { name: service });

describe("кадр 1 — вкладка «Предстоящие»", () => {
  it("сегмент без счётчиков, раздел предстоящих и под ним прошедшие", async () => {
    mockLists();
    renderScreen();

    expect(await tab(RECORDS_COPY.tabUpcoming)).toHaveAttribute("aria-selected", "true");
    expect(await tab(RECORDS_COPY.tabHistory)).toHaveAttribute("aria-selected", "false");

    const upcoming = await screen.findByRole("region", { name: RECORDS_COPY.upcomingHeading });
    expect(within(upcoming).getAllByRole("listitem")).toHaveLength(2);
    const history = screen.getByRole("region", { name: RECORDS_COPY.historyHeading });
    expect(within(history).getAllByRole("listitem")).toHaveLength(3);
  });

  it("на карточке — услуга, время, адрес; обычный статус не назван", async () => {
    mockLists();
    renderScreen();

    const first = await card("Маникюр");
    expect(first).toHaveTextContent(formatRecordWhen(UPCOMING[0]!.visit_at));
    expect(first).toHaveTextContent("Пенза, Московская, 12");
    // Положительная пара: карточка отрисована целиком, а слова статуса нет.
    expect(first).not.toHaveTextContent("Подтверждена");
    // У второй адрес источник не сообщил — сказано, у кого спросить.
    expect(await card("Массаж")).toHaveTextContent("Уточни адрес в салоне");
  });

  it("прошедших больше трёх — показаны три и «Все прошедшие»", async () => {
    mockLists(UPCOMING, [...HISTORY, past("b-h4", -200, { service_name: "Стрижка" })]);
    renderScreen();
    const user = userEvent.setup();

    const history = await screen.findByRole("region", { name: RECORDS_COPY.historyHeading });
    expect(within(history).getAllByRole("listitem")).toHaveLength(3);
    expect(within(history).queryByText("Стрижка")).not.toBeInTheDocument();

    await user.click(within(history).getByRole("button", { name: RECORDS_COPY.allHistory }));
    expect(await tab(RECORDS_COPY.tabHistory)).toHaveAttribute("aria-selected", "true");
    expect(await card("Стрижка")).toBeInTheDocument();
  });

  it("статус «в пути» — словами о ходе: «Перенос не завершён», а не «Перенесена»", async () => {
    mockLists([booking({ id: "b-1", status: "reschedule_requested", reschedulable: false })], []);
    renderScreen();

    const row = await card("Маникюр");
    expect(row).toHaveTextContent("Перенос не завершён");
    expect(row).not.toHaveTextContent("Перенесена");
  });

  it("у карточки есть описание: две записи на одну услугу различимы на слух", async () => {
    mockLists(
      [
        booking({ id: "b-1", master_name: "Анна Соколова" }),
        booking({ id: "b-2", master_name: "Ольга Тихонова" }),
      ],
      [],
    );
    renderScreen();

    const rows = await screen.findAllByRole("button", { name: "Маникюр" });
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAccessibleDescription(/Анна Соколова/);
    expect(rows[1]).toHaveAccessibleDescription(/Ольга Тихонова/);
  });

  it("предстоящих нет, прошедшие есть — строка и прошедшие, а не кадр «записей нет»", async () => {
    mockLists([], HISTORY);
    renderScreen();

    expect(await screen.findByText(RECORDS_COPY.noUpcoming)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: RECORDS_COPY.historyHeading })).toBeInTheDocument();
    expect(screen.queryByText(RECORDS_COPY.emptyTitle)).not.toBeInTheDocument();
  });

  it("DRF-2436 B / п.15: в строке мастер и салон; два салона различимы", async () => {
    mockLists(
      [
        booking({ id: "b-1", salon_name: "Салон «Ромашка»" }),
        booking({ id: "b-2", service_name: "Массаж", salon_name: "Студия «Лотос»" }),
      ],
      [],
    );
    renderScreen();

    expect(await card("Маникюр")).toHaveTextContent("Анна Соколова · Салон «Ромашка»");
    expect(await card("Массаж")).toHaveTextContent("Анна Соколова · Студия «Лотос»");
  });

  it("DRF-2172: цена «3 200 ₽» на карточке; без цены строки нет, не «0 ₽»", async () => {
    mockLists(
      [booking({ id: "b-1", price: "3200.00" }), booking({ id: "b-2", service_name: "Массаж" })],
      [],
    );
    renderScreen();

    expect(await card("Маникюр")).toHaveTextContent("3 200 ₽");
    const noPrice = await card("Массаж");
    expect(noPrice).toHaveTextContent("Массаж");
    expect(noPrice).not.toHaveTextContent("₽");
  });

  it("карточка открывает запись", async () => {
    mockLists();
    renderScreen();
    await userEvent.setup().click(await card("Массаж"));
    expect(await screen.findByText("BOOKING-b-2")).toBeInTheDocument();
  });
});

describe("кадр 12 — вкладка «Прошедшие»", () => {
  async function openHistory() {
    const user = userEvent.setup();
    await user.click(await tab(RECORDS_COPY.tabHistory));
    return user;
  }

  it("необычный статус назван, обычный — нет", async () => {
    mockLists();
    renderScreen();
    await openHistory();

    expect(await card("Брови")).toHaveTextContent("Отменена");
    expect(await card("Педикюр")).toHaveTextContent("Перенесена");
    const ordinary = await card("Окрашивание");
    expect(ordinary).toHaveTextContent("Анна Соколова");
    expect(ordinary).not.toHaveTextContent("Прошла");
  });

  it("действия — только у самой свежей записи", async () => {
    mockLists();
    renderScreen();
    await openHistory();

    expect(await screen.findAllByRole("button", { name: RECORDS_COPY.repeat })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: RECORDS_COPY.details })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: RECORDS_COPY.rate })).toHaveLength(1);
    // Остальные записи на месте — действия не выключили список.
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("«Посмотреть детали» открывает эту запись", async () => {
    mockLists();
    renderScreen();
    const user = await openHistory();
    await user.click(await screen.findByRole("button", { name: RECORDS_COPY.details }));
    expect(await screen.findByText("BOOKING-b-h1")).toBeInTheDocument();
  });

  it("«Повторить запись» ведёт в каталог", async () => {
    mockLists();
    renderScreen();
    const user = await openHistory();
    await user.click(await screen.findByRole("button", { name: RECORDS_COPY.repeat }));
    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
  });

  it("«Оценить визит» ведёт на настоящий экран отзыва", async () => {
    mockLists();
    renderScreen();
    const user = await openHistory();
    await user.click(await screen.findByRole("button", { name: RECORDS_COPY.rate }));
    expect(await screen.findByText("BOOKING-b-h1")).toBeInTheDocument();
  });

  it("оценить можно не только свежую: у остальных — строка на карточке", async () => {
    mockLists(UPCOMING, [past("b-h1", -30), past("b-h2", -80, { service_name: "Брови", can_rate: true })]);
    renderScreen();
    await openHistory();

    expect(await card("Брови")).toHaveTextContent("Можно оставить отзыв");
    expect(screen.queryByRole("button", { name: RECORDS_COPY.rate })).not.toBeInTheDocument();
  });

  it("прошедших нет, предстоящие есть — «история накопится»", async () => {
    mockLists(UPCOMING, []);
    renderScreen();
    await openHistory();
    expect(await screen.findByText(RECORDS_COPY.noHistory)).toBeInTheDocument();
  });

  it("фильтр по мастеру — от пяти записей", async () => {
    mockLists(UPCOMING, [
      past("h1", -30),
      past("h2", -40),
      past("h3", -50),
      past("h4", -60),
      past("h5", -70, { master_name: "Ольга Тихонова", service_name: "Брови" }),
    ]);
    renderScreen();
    const user = await openHistory();

    await user.click(await screen.findByRole("button", { name: "Ольга Тихонова" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(await card("Брови")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Все" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
  });

  it("«Показать ещё» догружает страницу по курсору сервера", async () => {
    mockedList.mockImplementation((params) => {
      if (!params?.past) return Promise.resolve({ items: UPCOMING, next_cursor: null });
      return params.before
        ? Promise.resolve({ items: [past("b-old", -500, { service_name: "Стрижка" })], next_cursor: null })
        : Promise.resolve({ items: HISTORY, next_cursor: "cursor-1" });
    });
    renderScreen();
    const user = await openHistory();

    await user.click(await screen.findByRole("button", { name: RECORDS_COPY.more }));
    expect(await card("Стрижка")).toBeInTheDocument();
    expect(mockedList).toHaveBeenCalledWith({ past: true, before: "cursor-1" });
    expect(screen.queryByRole("button", { name: RECORDS_COPY.more })).not.toBeInTheDocument();
  });

  it("сервер вернул уже показанные записи — дубликатов нет, и листать дальше нечего", async () => {
    mockedList.mockImplementation((params) =>
      Promise.resolve({
        items: params?.past ? HISTORY : UPCOMING,
        next_cursor: params?.past ? "cursor-1" : null,
      }),
    );
    renderScreen();
    const user = await openHistory();

    await user.click(await screen.findByRole("button", { name: RECORDS_COPY.more }));
    await vi.waitFor(() =>
      expect(screen.queryByRole("button", { name: RECORDS_COPY.more })).not.toBeInTheDocument(),
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });
});

describe("кадр 2 — записей нет вовсе", () => {
  it("текст макета, без сегмента; «Найти мастера» ведёт в каталог", async () => {
    mockLists([], []);
    renderScreen();

    expect(await screen.findByRole("heading", { name: RECORDS_COPY.emptyTitle })).toBeInTheDocument();
    expect(screen.getByText(/запишись на первую процедуру/)).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: RECORDS_COPY.findMaster }));
    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
  });

  it("«Спросить Ayla» открывает чат, а не главную", async () => {
    mockLists([], []);
    renderScreen();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Спросить Ayla" }));
    expect(await screen.findByText("AYLA-PROBE")).toBeInTheDocument();
  });

  it("пока прошедшие не прочитаны, «записей нет» не говорится", async () => {
    mockedList.mockImplementation((params) =>
      params?.past
        ? Promise.reject(new ApiError(500, "http_error", ""))
        : Promise.resolve({ items: [], next_cursor: null }),
    );
    renderScreen();

    expect(await screen.findByText(RECORDS_COPY.noUpcoming)).toBeInTheDocument();
    expect(screen.queryByText(RECORDS_COPY.emptyTitle)).not.toBeInTheDocument();
  });
});

describe("кадры 14–16 — список не загрузился", () => {
  it("кадр 15: «Не удалось загрузить записи», повтор работает, есть дорога в поддержку", async () => {
    mockedList.mockRejectedValueOnce(new ApiError(500, "http_error", ""));
    mockedList.mockRejectedValueOnce(new ApiError(500, "http_error", ""));
    renderScreen();

    expect(await screen.findByRole("heading", { name: RECORDS_COPY.loadFailed })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Написать в поддержку" })).toHaveAttribute(
      "href",
      SUPPORT_DEEPLINK,
    );

    mockLists();
    await userEvent.setup().click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(await card("Маникюр")).toBeInTheDocument();
  });

  it("кадр 14: устройство без сети — «Нет подключения к интернету»", async () => {
    setOnLine(false);
    mockedList.mockRejectedValue(new TypeError("Failed to fetch"));
    renderScreen();
    expect(
      await screen.findByRole("heading", { name: "Нет подключения к интернету" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
    // Кадр — на весь экран: переключаться не между чем, и «записи могли
    // устареть» говорить не о чем — показанных записей нет.
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByText(RECORDS_COPY.offline)).not.toBeInTheDocument();
  });

  it("не загрузился один раздел — второй остаётся доступным через сегмент", async () => {
    mockedList.mockImplementation((params) =>
      params?.past
        ? Promise.resolve({ items: HISTORY, next_cursor: null })
        : Promise.reject(new ApiError(500, "http_error", "")),
    );
    renderScreen();

    expect(await screen.findByRole("heading", { name: RECORDS_COPY.loadFailed })).toBeInTheDocument();
    await userEvent.setup().click(await tab(RECORDS_COPY.tabHistory));
    expect(await card("Брови")).toBeInTheDocument();
  });

  it("сбой запроса при живой сети отсутствием интернета не называется", async () => {
    mockedList.mockRejectedValue(new TypeError("Failed to fetch"));
    renderScreen();
    expect(await screen.findByRole("heading", { name: RECORDS_COPY.loadFailed })).toBeInTheDocument();
    expect(screen.queryByText("Нет подключения к интернету")).not.toBeInTheDocument();
  });

  it("DRF-1319 D-1: отказ входа назван своим именем и ведёт обратно в MAX", async () => {
    mockedList.mockRejectedValue(new ApiError(400, "malformed", "bad init data"));
    renderScreen();
    expect(await screen.findByText(OPEN_FROM_MAX_COPY.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: OPEN_FROM_MAX_COPY.action })).toBeInTheDocument();
    // Серверный `detail` человеку не показан (DRF-2446).
    expect(screen.queryByText("bad init data")).not.toBeInTheDocument();
  });

  it("кадр 16: удалённый аккаунт — слова словаря отказов, без выдуманных кнопок", async () => {
    mockedList.mockRejectedValue(new ApiError(403, "user_deleted", ""));
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Аккаунт удалён" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Открыть настройки" })).not.toBeInTheDocument();
  });
});

describe("корень вкладки", () => {
  it("та же панель, что на Главной: пять вкладок, «Записи» активна; стрелки «назад» нет", async () => {
    mockLists();
    renderScreen();
    await card("Маникюр");

    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(
      within(nav)
        .getAllByRole("button")
        .map((b) => b.textContent?.replace(/^\P{L}+/u, "")),
    ).toEqual(["Главная", "План", "Дневник", "Записи", "Профиль"]);
    expect(within(nav).getByRole("button", { name: "Записи" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.queryByRole("button", { name: "Назад" })).not.toBeInTheDocument();
    // Раздел назван для читающего с экрана, хотя на макете заголовка нет.
    expect(screen.getByRole("heading", { level: 1, name: "Записи" })).toBeInTheDocument();
  });

  it("DRF-2191: при отказе ручки панель остаётся — выход есть в любом состоянии", async () => {
    mockedList.mockRejectedValue(new ApiError(500, "http_error", ""));
    renderScreen();
    await screen.findByRole("heading", { name: RECORDS_COPY.loadFailed });
    expect(screen.getByRole("navigation", { name: "Основная навигация" })).toBeInTheDocument();
  });
});

/**
 * Офлайн: полоса и кнопки говорят одно и то же. Полоса «нет сети» без
 * выключенных действий — предупреждение, которое приложение само же
 * опровергает следующим касанием.
 *
 * Стража парная (`negative_assert_guard`, DRF-1411): к «действия выключены»
 * приложены положительные проверки — записи видны, запись открывается, а с
 * сетью те же кнопки активны.
 */
describe("офлайн: действия выключены вместе с предупреждением", () => {
  async function historyOffline() {
    mockLists();
    renderScreen();
    await userEvent.setup().click(await tab(RECORDS_COPY.tabHistory));
  }

  it("«Повторить запись» и «Оценить визит» недоступны, пока нет сети", async () => {
    setOnLine(false);
    await historyOffline();

    expect(await screen.findByText(RECORDS_COPY.offline)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: RECORDS_COPY.repeat })).toBeDisabled();
    expect(screen.getByRole("button", { name: RECORDS_COPY.rate })).toBeDisabled();
  });

  it("положительная стража: записи видны, запись открывается и без сети", async () => {
    setOnLine(false);
    await historyOffline();

    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    await userEvent.setup().click(screen.getByRole("button", { name: RECORDS_COPY.details }));
    expect(await screen.findByText("BOOKING-b-h1")).toBeInTheDocument();
  });

  it("положительная стража: с сетью те же действия активны", async () => {
    await historyOffline();
    expect(await screen.findByRole("button", { name: RECORDS_COPY.repeat })).toBeEnabled();
    expect(screen.getByRole("button", { name: RECORDS_COPY.rate })).toBeEnabled();
    expect(screen.queryByText(RECORDS_COPY.offline)).not.toBeInTheDocument();
  });
});
