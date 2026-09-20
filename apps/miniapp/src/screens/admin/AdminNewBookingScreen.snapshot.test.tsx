/**
 * Сторож «байт в байт» на рендер салонной формы «Новая запись» (DRF-2155, М-3).
 *
 * М-3 выносит из этого экрана общий компонент формы для мастера. Тесты
 * поведения (`AdminNewBookingScreen.test.tsx`) доказывают обещания §12–18,
 * но не то, что 852 строки после вынесения рисуют ТО ЖЕ САМОЕ. Здесь —
 * текстовый снимок DOM в трёх состояниях, снятый на чистом dev ДО
 * рефакторинга (4cd78913): пусто после загрузки списков; всё заполнено до
 * «Проверьте запись»; «время занято» после отправки. Любое расхождение —
 * красное, и это правильно: сдвиг админки при вынесении должен быть
 * назван, а не замечен через неделю.
 *
 * Снимок нормализован только там, где детерминизма нет по построению:
 * ``id``/``for``/``aria-*`` от ``useId`` (порядковые, но зависят от дерева)
 * и дата дня (часы заморожены). Обновлять — ``vitest -u`` с причиной в PR.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    getBookingSlots: vi.fn(),
    getCatalogServicesForAdmin: vi.fn(),
    listMasters: vi.fn(),
    searchSalonCustomers: vi.fn(),
    createSalonBooking: vi.fn(),
  };
});

import {
  createSalonBooking,
  CustomerSearchUnavailable,
  getBookingSlots,
  getCatalogServicesForAdmin,
  listMasters,
  searchSalonCustomers,
} from "../../lib/admin-api";
import { AdminNewBookingScreen } from "./AdminNewBookingScreen";

const mockedSlots = vi.mocked(getBookingSlots);
const mockedServices = vi.mocked(getCatalogServicesForAdmin);
const mockedMasters = vi.mocked(listMasters);
const mockedSearch = vi.mocked(searchSalonCustomers);
const mockedCreate = vi.mocked(createSalonBooking);

const FROZEN_NOW = new Date("2026-08-21T10:00:00+03:00");

function normalize(html: string): string {
  return (
    html
      // useId — «:r1:»/««r1»» — порядковые, но зависят от формы дерева.
      .replace(
        /(id|for|aria-controls|aria-labelledby|aria-describedby)="[^"]*"/g,
        '$1="…"',
      )
      .replace(/></g, ">\n<")
      .trim() + "\n"
  );
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/admin/booking/new"]}>
      <AdminNewBookingScreen />
    </MemoryRouter>,
  );
}

async function chooseServiceAndMaster() {
  screen.getByLabelText(/Услуга/).click();
  (await screen.findByText(/Маникюр/)).click();
  screen.getByLabelText(/Мастер/).click();
  (await screen.findByText("Анна")).click();
}

async function fillWholeDraft() {
  mockedSearch.mockResolvedValue([
    { id: "c-1", name: "Мария", named: true, phone_masked: "+• ••67" },
  ]);
  screen.getByLabelText(/Клиент/).click();
  fireEvent.change(await screen.findByLabelText("Поиск клиента"), {
    target: { value: "Мария" },
  });
  (await screen.findByText(/Мария · /)).click();
  await chooseServiceAndMaster();
  screen.getByLabelText(/Дата и время/).click();
  (await screen.findByRole("button", { name: "15:00" })).click();
  await screen.findByText(/Проверьте запись/);
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(FROZEN_NOW);
  vi.clearAllMocks();
  mockedServices.mockResolvedValue([
    { id: "s-1", name: "Маникюр", duration_min: 60 },
    { id: "s-2", name: "Окрашивание", duration_min: 180 },
  ]);
  mockedMasters.mockResolvedValue({
    items: [
      {
        id: "m-1",
        name: "Анна",
        specialization: "",
        photo_url: "",
        is_active: true,
        invite_status: "accepted",
        last_seen_at: null,
        services_count: 1,
      },
    ],
    next_cursor: null,
    total_count: 1,
  });
  mockedSearch.mockRejectedValue(new CustomerSearchUnavailable());
  mockedCreate.mockResolvedValue({
    outcome: "committed",
    detail: "ok",
    appointment_id: "a-1",
  });
  mockedSlots.mockResolvedValue({
    date: "2026-08-21",
    timezone: "Europe/Moscow",
    master_id: "m-1",
    service_id: "s-1",
    duration_min: 60,
    slots: [
      {
        time: "15:00",
        start_at: "2026-08-21T15:00:00+03:00",
        duration_min: 60,
      },
      { time: "15:30", start_at: null, duration_min: 60 },
    ],
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("салонная форма рисует то же самое (сторож вынесения М-3)", () => {
  it("пусто — после загрузки списков", async () => {
    const { container } = renderScreen();
    await screen.findByText(/Осталось выбрать/);
    await expect(normalize(container.innerHTML)).toMatchFileSnapshot(
      "./__snapshots__/AdminNewBookingScreen.empty.html",
    );
  });

  it("заполнено — до «Проверьте запись»", async () => {
    const { container } = renderScreen();
    await fillWholeDraft();
    await expect(normalize(container.innerHTML)).toMatchFileSnapshot(
      "./__snapshots__/AdminNewBookingScreen.filled.html",
    );
  });

  it("«время занято» — черновик цел", async () => {
    mockedCreate.mockResolvedValue({ outcome: "conflict", detail: "занято" });
    const { container } = renderScreen();
    await fillWholeDraft();
    screen.getByRole("button", { name: "Создать запись" }).click();
    await screen.findByText(/это время уже занято/i);
    await expect(normalize(container.innerHTML)).toMatchFileSnapshot(
      "./__snapshots__/AdminNewBookingScreen.conflict.html",
    );
  });
});
