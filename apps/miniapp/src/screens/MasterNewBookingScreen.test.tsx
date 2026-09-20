/**
 * «Новая запись» мастера по макету DRF-1184 (DRF-2155, М-3).
 *
 * Красное листа: у мастера/соло экрана нет; тап по свободному окну ведёт в
 * «недоступно». Здесь — узлы макета и границы:
 *
 * * один экран Клиент / Услуга / Дата и время — БЕЗ строки «Мастер» (мастер —
 *   субъект initData), без пошаговой полосы, без экрана подтверждения;
 * * выбор клиента: поиск по имени, список «Анна П. · была 12.05» /
 *   «Анна С. · новый клиент» — без телефона; «Новый клиент» — имя + телефон
 *   + пояснение макета; телефон уходит в каталог и НЕ возвращается ни в
 *   строку «Клиент», ни в «Проверьте запись», ни после создания (DRF-1039);
 * * услуга до времени; слоты — из `booking-slots` мастера по услуге;
 * * `?date&from&to` → «Выбранное окно: 14:00–17:00» (сторож на «Исходный
 *   интервал»);
 * * «Это время занято» + варианты, клиент/услуга/дата остаются;
 *   `result_pending` → «Проверяем результат» + «Проверить снова» с тем же
 *   ключом; тексты — из словаря `SystemState` (М-6), не свои;
 * * `/solo/booking/new` — тот же экран, назад — в `/solo/schedule`.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getMasterCatalog: vi.fn(),
    searchMasterCustomers: vi.fn(),
    getMasterBookingSlots: vi.fn(),
    createMasterBooking: vi.fn(),
  };
});

import { SYSTEM_STATE_COPY } from "../components/master/SystemState";
import {
  createMasterBooking,
  getMasterBookingSlots,
  getMasterCatalog,
  searchMasterCustomers,
  type MasterServiceItem,
} from "../lib/master-api";
import { MasterNewBookingScreen } from "./MasterNewBookingScreen";

const mockedCatalog = vi.mocked(getMasterCatalog);
const mockedSearch = vi.mocked(searchMasterCustomers);
const mockedSlots = vi.mocked(getMasterBookingSlots);
const mockedCreate = vi.mocked(createMasterBooking);

const CUSTOMER_PHONE = "+79997775544";

function service(over: Partial<MasterServiceItem> = {}): MasterServiceItem {
  return {
    service_id: "s-1",
    name: "Маникюр",
    price_rub: 2500,
    duration_min: 60,
    description: "",
    category: "nails",
    is_active: true,
    ...over,
  };
}

function renderAt(path = "/master/booking/new") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/master/booking/new"
          element={<MasterNewBookingScreen />}
        />
        <Route path="/solo/booking/new" element={<MasterNewBookingScreen />} />
        <Route path="/master/schedule" element={<p>Экран «Расписание»</p>} />
        <Route path="/solo/schedule" element={<p>Экран «Расписание соло»</p>} />
        <Route
          path="/master/bookings/:id"
          element={<p>Экран «Детали записи»</p>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

async function chooseService() {
  screen.getByLabelText(/Услуга/).click();
  (await screen.findByText(/Маникюр/)).click();
}

async function chooseExistingCustomer() {
  screen.getByLabelText(/Клиент/).click();
  fireEvent.change(await screen.findByLabelText("Поиск клиента"), {
    target: { value: "Анна" },
  });
  (await screen.findByText(/Анна П\./)).click();
}

async function chooseSlot() {
  screen.getByLabelText(/Дата и время/).click();
  (await screen.findByRole("button", { name: "15:00" })).click();
}

async function fillWholeDraft() {
  await chooseExistingCustomer();
  await chooseService();
  await chooseSlot();
  await screen.findByText(/Проверьте запись/);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedCatalog.mockResolvedValue([
    service(),
    service({ service_id: "s-2", name: "Окрашивание", duration_min: 180 }),
    service({ service_id: "s-off", name: "Снятая", is_active: false }),
  ]);
  mockedSearch.mockResolvedValue([
    { id: "c-1", name: "Анна П.", named: true, last_visit_date: "2026-05-12" },
    { id: "c-2", name: "Анна С.", named: true, last_visit_date: null },
  ]);
  mockedSlots.mockResolvedValue({
    date: "2026-10-21",
    timezone: "Europe/Moscow",
    service_id: "s-1",
    duration_min: 60,
    slots: [
      {
        time: "15:00",
        start_at: "2026-10-21T15:00:00+03:00",
        duration_min: 60,
      },
      {
        time: "16:00",
        start_at: "2026-10-21T16:00:00+03:00",
        duration_min: 60,
      },
    ],
  });
  mockedCreate.mockResolvedValue({
    outcome: "committed",
    detail: "appointment created",
    appointment_id: "a-1",
  });
});

describe("один экран по макету DRF-1184", () => {
  it("рисует Клиент / Услуга / Дата и время и НЕ рисует строку «Мастер»", async () => {
    renderAt();
    await waitFor(() => expect(mockedCatalog).toHaveBeenCalled());
    for (const label of [/^Клиент/, /^Услуга/, /^Дата и время/]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    expect(screen.queryByLabelText(/^Мастер/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Создать запись" }),
    ).toBeDisabled();
    expect(await screen.findByText(/Осталось выбрать/)).not.toHaveTextContent(
      /мастер/i,
    );
  });

  it("услуги — свои, только активные", async () => {
    renderAt();
    screen.getByLabelText(/Услуга/).click();
    expect(await screen.findByText(/Маникюр/)).toBeInTheDocument();
    expect(screen.getByText(/Окрашивание/)).toBeInTheDocument();
    expect(screen.queryByText(/Снятая/)).toBeNull();
  });

  it("слоты спрашиваются по услуге и дате — без master_id", async () => {
    renderAt("/master/booking/new?date=2026-10-21");
    await chooseService();
    screen.getByLabelText(/Дата и время/).click();
    await screen.findByRole("button", { name: "15:00" });
    expect(mockedSlots).toHaveBeenCalledTimes(1);
    expect(mockedSlots.mock.calls[0]?.[0]).toEqual({
      serviceId: "s-1",
      date: "2026-10-21",
    });
  });

  it("без услуги слоты не спрашиваются — «Сначала выберите услугу»", async () => {
    renderAt();
    screen.getByLabelText(/Дата и время/).click();
    expect(
      await screen.findByText(/Сначала выберите услугу/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/и мастера/)).toBeNull();
    expect(mockedSlots).not.toHaveBeenCalled();
  });
});

describe("выбор клиента — без телефона (DRF-1039, владелец 20.09)", () => {
  it("список: «Анна П. · была 12.05» и «Анна С. · новый клиент», поиск по имени", async () => {
    renderAt();
    screen.getByLabelText(/Клиент/).click();
    const input = await screen.findByLabelText("Поиск клиента");
    expect(input).toHaveAttribute("placeholder", "Имя");
    fireEvent.change(input, { target: { value: "Анна" } });
    expect(await screen.findByText("Анна П. · была 12.05")).toBeInTheDocument();
    expect(screen.getByText("Анна С. · новый клиент")).toBeInTheDocument();
    expect(mockedSearch.mock.calls[0]?.[0]).toBe("Анна");
  });

  it("«Новый клиент»: имя + телефон + пояснение; телефон не возвращается на экран", async () => {
    renderAt();
    screen.getByLabelText(/Клиент/).click();
    // «Новый клиент» — тот же блок, что у стойки: имя + телефон; у мастера — с
    // пояснением макета.
    expect(await screen.findByText("Новый клиент")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Имя и телефон нужны для создания записи и связи по ней.",
      ),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Имя клиента"), {
      target: { value: "Мария" },
    });
    fireEvent.change(screen.getByLabelText("Телефон клиента"), {
      target: { value: CUSTOMER_PHONE },
    });
    screen.getByRole("button", { name: "Сохранить клиента" }).click();

    // Присутствие: клиент выбран — его имя в строке, телефона в ней нет.
    const row = await screen.findByLabelText(/^Клиент: Мария/);
    expect(row.textContent).not.toContain("5544");
    await chooseService();
    await chooseSlot();
    await screen.findByText(/Проверьте запись/);
    screen.getByRole("button", { name: "Создать запись" }).click();
    expect(await screen.findByText("Запись создана.")).toBeInTheDocument();

    // Телефон ушёл в каталог…
    expect(mockedCreate.mock.calls[0]?.[0]).toMatchObject({
      client_name: "Мария",
      client_phone: CUSTOMER_PHONE,
    });
    // …и нигде не вернулся: ни в строке, ни в проверке, ни после создания.
    expect(document.body.textContent).not.toContain(CUSTOMER_PHONE);
    expect(document.body.textContent).not.toContain("5544");
  });
});

describe("«Выбранное окно» из расписания (DRF-1183 состояние 4)", () => {
  it("?date&from&to → подпись «Выбранное окно» и день слотов", async () => {
    renderAt("/master/booking/new?date=2026-10-21&from=14:00&to=17:00");
    expect(await screen.findByText("Выбранное окно")).toBeInTheDocument();
    expect(screen.getByText(/14:00–17:00/)).toBeInTheDocument();
    await chooseService();
    screen.getByLabelText(/Дата и время/).click();
    await screen.findByRole("button", { name: "15:00" });
    expect(mockedSlots.mock.calls[0]?.[0]).toMatchObject({
      date: "2026-10-21",
    });
  });

  it("сторож: «Исходный интервал» на мастерском экране не звучит", () => {
    const sources = import.meta.glob(
      ["./MasterNewBookingScreen.tsx", "../components/booking/*.tsx"],
      { query: "?raw", import: "default", eager: true },
    ) as Record<string, string>;
    expect(Object.keys(sources).length).toBeGreaterThanOrEqual(2);
    for (const [file, src] of Object.entries(sources)) {
      expect(src, file).not.toContain("Исходный интервал");
    }
  });
});

describe("исходы — словами SystemState (М-6)", () => {
  it("«Это время занято»: варианты из ответа, клиент и услуга остаются", async () => {
    mockedCreate.mockResolvedValue({
      outcome: "conflict",
      detail: "занято",
      reason_code: "slot_taken",
      alternatives: [
        {
          time: "16:00",
          start_at: "2026-10-21T16:00:00+03:00",
          duration_min: 60,
        },
        {
          time: "17:00",
          start_at: "2026-10-21T17:00:00+03:00",
          duration_min: 60,
        },
      ],
      alternatives_unavailable: false,
    });
    renderAt("/master/booking/new?date=2026-10-21");
    await fillWholeDraft();
    screen.getByRole("button", { name: "Создать запись" }).click();

    expect(
      await screen.findByText(SYSTEM_STATE_COPY.conflict.title),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^Клиент/).textContent).toMatch(/Анна П\./);
    expect(screen.getByLabelText(/^Услуга/).textContent).toMatch(/Маникюр/);
    // Варианты — кнопки; выбор варианта ставит слот и убирает конфликт.
    screen.getByRole("button", { name: "17:00" }).click();
    await waitFor(() =>
      expect(screen.getByLabelText(/^Дата и время/).textContent).toMatch(
        /17:00/,
      ),
    );
    expect(screen.queryByText(SYSTEM_STATE_COPY.conflict.title)).toBeNull();
  });

  it("«Это время занято» без вариантов — CTA «Выбрать другое время» открывает лист времени", async () => {
    mockedCreate.mockResolvedValue({
      outcome: "conflict",
      detail: "занято",
      reason_code: "slot_taken",
      alternatives: null,
      alternatives_unavailable: true,
    });
    renderAt("/master/booking/new?date=2026-10-21");
    await fillWholeDraft();
    screen.getByRole("button", { name: "Создать запись" }).click();
    (
      await screen.findByRole("button", {
        name: SYSTEM_STATE_COPY.conflict.cta,
      })
    ).click();
    expect(
      await screen.findByRole("button", { name: "16:00" }),
    ).toBeInTheDocument();
  });

  it("result_pending: «Проверяем результат», без второй записи, «Проверить снова» с тем же ключом", async () => {
    mockedCreate.mockResolvedValue({
      outcome: "pending",
      detail: "the schedule did not answer",
      reason_code: "result_pending",
      idempotency_key: "k-1",
    });
    renderAt("/master/booking/new?date=2026-10-21");
    await fillWholeDraft();
    screen.getByRole("button", { name: "Создать запись" }).click();

    expect(
      await screen.findByText(SYSTEM_STATE_COPY.pending.title),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Не создавайте запись повторно, пока проверка не закончена.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Запись создана.")).toBeNull();

    mockedCreate.mockResolvedValue({
      outcome: "committed",
      detail: "ok",
      appointment_id: "a-2",
    });
    screen
      .getByRole("button", { name: SYSTEM_STATE_COPY.pending.recheck })
      .click();
    await screen.findByText("Запись создана.");
    expect(mockedCreate).toHaveBeenCalledTimes(2);
    expect(mockedCreate.mock.calls[1]?.[0].idempotency_key).toBe(
      mockedCreate.mock.calls[0]?.[0].idempotency_key,
    );
  });

  it("создано: «Запись создана.» и дверь «Открыть запись» в детали", async () => {
    renderAt("/master/booking/new?date=2026-10-21");
    await fillWholeDraft();
    screen.getByRole("button", { name: "Создать запись" }).click();
    expect(await screen.findByText("Запись создана.")).toBeInTheDocument();
    screen.getByRole("link", { name: "Открыть запись" }).click();
    expect(
      await screen.findByText("Экран «Детали записи»"),
    ).toBeInTheDocument();
  });
});

describe("соло-мастер", () => {
  it("/solo/booking/new — тот же экран, назад — в расписание соло", async () => {
    renderAt("/solo/booking/new");
    await waitFor(() => expect(mockedCatalog).toHaveBeenCalled());
    expect(screen.queryByLabelText(/^Мастер/)).toBeNull();
    screen.getByRole("button", { name: /Расписание/ }).click();
    expect(
      await screen.findByText("Экран «Расписание соло»"),
    ).toBeInTheDocument();
  });
});
