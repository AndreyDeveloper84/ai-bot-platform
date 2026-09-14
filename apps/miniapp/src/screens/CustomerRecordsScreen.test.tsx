/**
 * Tests for `CustomerRecordsScreen` after pilot phase 3.2 — real
 * `GET /bookings/list` data instead of the invented stub bookings.
 * HTTP layer (`../lib/api`) mocked; fixtures are verbatim `BookingItem`
 * rows. Home = records: the screen is a tab root (no back button) and
 * renders identically in DEV and prod builds.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Домашний экран теперь спрашивает decision-context (приглашение в
// анкету цели). Мокаем, чтобы юнит-тест не ходил в сеть; отсутствие
// missing = приглашение не рисуется и на эти проверки не влияет.
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
import { authErrorCopy } from "../lib/auth-error-copy";
import { CustomerRecordsScreen } from "./CustomerRecordsScreen";

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
    // DRF-1652 — умолчание `null`, то есть «источник промолчал».
    // НЕ `""`: это сказало бы, что салон ответил «адреса нет», и
    // фикстура утверждала бы за салон то, чего он не говорил.
    address: null,
    ...partial,
  };
}

const UPCOMING: BookingItem[] = [
  booking({ id: "b-1", visit_at: isoInHours(20) }),
  booking({ id: "b-2", service_name: "Массаж", visit_at: isoInHours(70) }),
];

const HISTORY: BookingItem[] = [
  booking({
    id: "b-h1",
    visit_at: isoInHours(-30),
    cancellable: false,
    reschedulable: false,
    can_rate: true,
  }),
  booking({
    id: "b-h2",
    status: "cancelled",
    service_name: "Брови",
    visit_at: isoInHours(-80),
    cancellable: false,
    reschedulable: false,
  }),
  booking({
    id: "b-h3",
    status: "rescheduled",
    service_name: "Педикюр",
    visit_at: isoInHours(-100),
    cancellable: false,
    reschedulable: false,
  }),
];

function mockLists(upcoming: BookingItem[] = UPCOMING, history: BookingItem[] = HISTORY) {
  mockedList.mockImplementation((params) =>
    Promise.resolve({
      items: params?.past ? history : upcoming,
      next_cursor: null,
    }),
  );
}

function BookingProbe() {
  const { bookingId } = useParams();
  return <div>BOOKING-{bookingId}</div>;
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <Routes>
        <Route path="/customer/main" element={<CustomerRecordsScreen />} />
        <Route path="/customer/records/:bookingId" element={<BookingProbe />} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
        <Route path="/feedback/:bookingId" element={<BookingProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Переключить `navigator.onLine`, вернув прежнее значение обратно. */
function setOnLine(value: boolean) {
  Object.defineProperty(window.navigator, "onLine", {
    value,
    configurable: true,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
  setOnLine(true);
});

describe("CustomerRecordsScreen (real data)", () => {
  it("bottom nav: «День» is not offered, the four real tabs are", async () => {
    // DRF-1546 — поверхности «День» не существует: её роль исполнял
    // домашний экран, а он теперь «Главная». Вкладка вела бы на
    // страницу с подсвеченной «Главной», то есть врала бы о том, куда
    // ведёт. Стража парная: снята одна вкладка, а не навигация.
    mockLists();
    renderScreen();
    const nav = within(
      await screen.findByRole("navigation", { name: "Основная навигация" }),
    );
    expect(nav.queryByRole("button", { name: "День" })).not.toBeInTheDocument();
    for (const tab of ["Главная", "Записи", "Услуги", "Я"]) {
      expect(nav.getByRole("button", { name: tab })).toBeInTheDocument();
    }
  });

  it("renders real upcoming bookings with tab counts and status badges", async () => {
    mockLists();
    renderScreen();
    expect(await screen.findByRole("tab", { name: "Ближайшие (2)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "История (3)" })).toBeInTheDocument();
    expect(screen.getByText("Маникюр")).toBeInTheDocument();
    expect(screen.getByText("Массаж")).toBeInTheDocument();
    expect(screen.getAllByText("Подтверждена").length).toBeGreaterThan(0);
    // Nearest card (≤24h) carries manage actions; the future one doesn't.
    expect(screen.getByRole("button", { name: "Перенести" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeInTheDocument();
  });

  it("history tab shows derived statuses and history actions", async () => {
    const user = userEvent.setup();
    mockLists();
    renderScreen();
    await screen.findByRole("tab", { name: "Ближайшие (2)" });
    await user.click(screen.getByRole("tab", { name: "История (3)" }));
    expect(await screen.findByText("Прошла")).toBeInTheDocument();
    expect(screen.getByText("Отменена")).toBeInTheDocument();
    expect(screen.getByText("Перенесена")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оставить отзыв" })).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "Записаться ещё" }).length,
    ).toBeGreaterThan(0);
  });

  it("contains no stub-era artefacts and no message action", async () => {
    mockLists();
    renderScreen();
    await screen.findByRole("tab", { name: "Ближайшие (2)" });
    expect(screen.queryByText("Beauty Place")).not.toBeInTheDocument();
    expect(screen.queryByText("Casa Bella")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сообщить по записи" })).not.toBeInTheDocument();
    expect(screen.queryByText(/ул\. Тверская/)).not.toBeInTheDocument();
  });

  it("shows the empty state when there are no bookings at all", async () => {
    const user = userEvent.setup();
    mockLists([], []);
    renderScreen();
    expect(await screen.findByText(/Пока записей нет/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Найти услугу" }));
    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
  });

  it("shows the error state with a working retry", async () => {
    const user = userEvent.setup();
    mockedList.mockRejectedValueOnce(new Error("network down"));
    mockLists();
    renderScreen();
    expect(await screen.findByText(/Не получилось загрузить/)).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Обновить" })[0]!);
    expect(await screen.findByRole("tab", { name: "Ближайшие (2)" })).toBeInTheDocument();
  });

  it("DRF-1319 D-1: отказ входа (400 malformed) назван своим именем, не «через минуту»", async () => {
    mockedList.mockRejectedValueOnce(new ApiError(400, "malformed", "missing Authorization header"));
    renderScreen();
    expect(await screen.findByText(authErrorCopy("malformed").title)).toBeInTheDocument();
    expect(screen.getByText(authErrorCopy("malformed").body)).toBeInTheDocument();
    expect(screen.queryByText(/через минуту/)).not.toBeInTheDocument();
    expect(screen.queryByText(/missing Authorization header/)).not.toBeInTheDocument();
  });

  it("DRF-1319 D-1: прочий 4xx — по-прежнему «попробуй через минуту» (положительная стража)", async () => {
    mockedList.mockRejectedValueOnce(new ApiError(422, "validation_error", "bad section"));
    renderScreen();
    expect(await screen.findByText(/через минуту/)).toBeInTheDocument();
    expect(screen.queryByText(authErrorCopy("malformed").title)).not.toBeInTheDocument();
  });

  it("opens the booking detail from a card", async () => {
    const user = userEvent.setup();
    mockLists();
    renderScreen();
    await screen.findByText("Маникюр");
    await user.click(screen.getAllByRole("button", { name: /Открыть/ })[0]!);
    expect(await screen.findByText("BOOKING-b-1")).toBeInTheDocument();
  });

  it("review CTA leads to the real feedback screen", async () => {
    const user = userEvent.setup();
    mockLists();
    renderScreen();
    await screen.findByRole("tab", { name: "Ближайшие (2)" });
    await user.click(screen.getByRole("tab", { name: "История (3)" }));
    await user.click(await screen.findByRole("button", { name: "Оставить отзыв" }));
    expect(await screen.findByText("BOOKING-b-h1")).toBeInTheDocument();
  });

  it("repeat CTA leads to the real catalog", async () => {
    const user = userEvent.setup();
    mockLists();
    renderScreen();
    await screen.findByRole("tab", { name: "Ближайшие (2)" });
    await user.click(screen.getByRole("tab", { name: "История (3)" }));
    await user.click((await screen.findAllByRole("button", { name: "Записаться ещё" }))[0]!);
    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
  });

  it("is a tab root — no back button (home = records)", async () => {
    mockLists();
    renderScreen();
    await screen.findByRole("tab", { name: "Ближайшие (2)" });
    expect(screen.queryByRole("button", { name: "Назад" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Записи" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});

/**
 * Офлайн: полоса и кнопки говорят одно и то же.
 *
 * До правки экран рисовал честную полосу «Записи могут быть
 * устаревшими — нет сети», а кнопки под ней оставались живыми:
 * «Перенести» и «Отменить» уводили на экраны, которые без сети ничего
 * не загрузят и ничего не отправят, «Записаться ещё» — в каталог,
 * который не придёт. Предупреждение, которое приложение само же
 * опровергает следующим касанием, хуже отсутствия предупреждения.
 *
 * Стража парная (`negative_assert_guard`, DRF-1411): к «действия
 * выключены» приложены положительные проверки на тех же данных — сами
 * записи, их время и мастер на месте, «Открыть запись» работает
 * (чтение уже показанного, у экрана детали своё состояние ошибки), и
 * при живой сети ВСЕ кнопки снова активны. Правка, которая выключила бы
 * карточку целиком или выключила бы её навсегда, упала бы на них.
 *
 * Тест умеет падать: снимите `disabled={offline}` с кнопок
 * `BookingCard` — покраснеет первый случай; перестаньте передавать
 * `offline: !online` в `renderTimeBuckets` — покраснеет он же.
 */
describe("офлайн: действия выключены вместе с предупреждением", () => {
  it("перенос, отмена и повтор недоступны, пока нет сети", async () => {
    setOnLine(false);
    mockLists();
    renderScreen();
    await screen.findByText("Маникюр");
    expect(screen.getByRole("button", { name: "Перенести" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeDisabled();
    // Полоса объясняет, почему.
    expect(
      screen.getByText(/Перенос, отмена и\s+новая запись сейчас недоступны/),
    ).toBeInTheDocument();
  });

  it("история: «Записаться ещё» и «Оставить отзыв» тоже выключены", async () => {
    setOnLine(false);
    mockLists();
    renderScreen();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: /История/ }));
    const repeats = await screen.findAllByRole("button", {
      name: "Записаться ещё",
    });
    for (const b of repeats) expect(b).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Оставить отзыв" }),
    ).toBeDisabled();
  });

  it("положительная стража: записи видны и «Открыть запись» работает", async () => {
    setOnLine(false);
    mockLists();
    renderScreen();
    const user = userEvent.setup();
    // Сами записи никуда не делись — офлайн выключает действия, не показ.
    expect(await screen.findByText("Маникюр")).toBeInTheDocument();
    expect(screen.getByText("Массаж")).toBeInTheDocument();
    expect(screen.getAllByText(/у Анна Соколова/).length).toBeGreaterThan(0);
    // Чтение уже показанной записи остаётся доступным.
    const open = screen.getAllByRole("button", { name: "Открыть запись" })[0];
    expect(open).toBeEnabled();
    await user.click(open!);
    expect(await screen.findByText("BOOKING-b-1")).toBeInTheDocument();
  });

  it("положительная стража: с сетью все действия снова активны", async () => {
    setOnLine(true);
    mockLists();
    renderScreen();
    await screen.findByText("Маникюр");
    expect(screen.getByRole("button", { name: "Перенести" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeEnabled();
    expect(screen.queryByText(/нет сети/i)).not.toBeInTheDocument();
  });
});
