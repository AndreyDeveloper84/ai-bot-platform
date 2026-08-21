/**
 * «Новая запись» — the three promises the UX contract makes (§12–17).
 *
 * 1. Nothing shifts silently: an invalidated start is announced.
 * 2. The slot query does not happen until it can mean something.
 * 3. «Could not ask the schedule» never renders as «nothing free».
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    getBookingSlots: vi.fn(),
    getCatalogServicesForAdmin: vi.fn(),
    listMasters: vi.fn(),
    searchSalonCustomers: vi.fn(),
    createSalonBooking: vi.fn(),
  };
});

import { ApiError } from "../../lib/api";
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

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/admin/booking/new"]}>
      <AdminNewBookingScreen />
    </MemoryRouter>,
  );
}

/** Walk the draft to «service + master chosen», the state slots need. */
async function chooseServiceAndMaster() {
  screen.getByLabelText(/Услуга/).click();
  (await screen.findByText(/Маникюр/)).click();
  screen.getByLabelText(/Мастер/).click();
  (await screen.findByText("Анна")).click();
}

beforeEach(() => {
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
  mockedCreate.mockResolvedValue({ outcome: "committed", detail: "ok", appointment_id: "a-1" });
  mockedSlots.mockResolvedValue({
    date: "2026-08-21",
    timezone: "Europe/Moscow",
    master_id: "m-1",
    service_id: "s-1",
    duration_min: 60,
    slots: [
      { time: "15:00", start_at: "2026-08-21T15:00:00+03:00", duration_min: 60 },
      { time: "15:30", start_at: null, duration_min: 60 },
    ],
  });
});

describe("primary screen shape (§12)", () => {
  it("shows every row at once — a draft, not a wizard", async () => {
    renderScreen();
    for (const label of [/Клиент/, /Услуга/, /Мастер/, /Дата и время/]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });

  it("names what is still missing rather than blocking the screen", async () => {
    renderScreen();
    expect(await screen.findByText(/Осталось выбрать/)).toBeInTheDocument();
  });

  it("keeps «Создать запись» disabled until the draft is reviewable", () => {
    renderScreen();
    expect(screen.getByRole("button", { name: "Создать запись" })).toBeDisabled();
  });
});

describe("availability is not asked before it can mean anything (§12, §17)", () => {
  it("refuses to query slots without service and master", async () => {
    renderScreen();
    screen.getByLabelText(/Дата и время/).click();

    expect(
      await screen.findByText(/Сначала выберите услугу и мастера/),
    ).toBeInTheDocument();
    expect(mockedSlots).not.toHaveBeenCalled();
  });

  it("queries once both are known", async () => {
    renderScreen();
    await chooseServiceAndMaster();
    screen.getByLabelText(/Дата и время/).click();

    await waitFor(() => expect(mockedSlots).toHaveBeenCalled());
    expect(mockedSlots.mock.calls[0]?.[0]).toMatchObject({
      masterId: "m-1",
      serviceId: "s-1",
    });
  });
});

describe("nothing shifts silently (§12)", () => {
  it("announces which change dropped the chosen start", async () => {
    renderScreen();
    await chooseServiceAndMaster();

    screen.getByLabelText(/Дата и время/).click();
    (await screen.findByRole("button", { name: "15:00" })).click();
    await waitFor(() =>
      expect(screen.getByLabelText(/Дата и время/).textContent).toMatch(/15:00/),
    );

    // Change the service — the start was sized for the old duration.
    screen.getByLabelText(/Услуга/).click();
    (await screen.findByText(/Окрашивание/)).click();

    expect(
      await screen.findByText(/Время сброшено — у новой услуги другая длительность/),
    ).toBeInTheDocument();
  });
});

describe("an unreachable schedule is not «nothing free» (§16)", () => {
  it("says the schedule is unavailable on 503", async () => {
    mockedSlots.mockRejectedValue(
      new ApiError(503, "schedule_unavailable", "unreachable"),
    );
    renderScreen();
    await chooseServiceAndMaster();
    screen.getByLabelText(/Дата и время/).click();

    expect(await screen.findByText(/Расписание сейчас недоступно/)).toBeInTheDocument();
    expect(screen.queryByText(/свободного времени нет/)).not.toBeInTheDocument();
  });

  it("distinguishes a genuinely full day from a failure", async () => {
    mockedSlots.mockResolvedValue({
      date: "2026-08-21",
      timezone: "Europe/Moscow",
      master_id: "m-1",
      service_id: "s-1",
      duration_min: 60,
      slots: [],
    });
    renderScreen();
    await chooseServiceAndMaster();
    screen.getByLabelText(/Дата и время/).click();

    expect(await screen.findByText(/свободного времени нет/)).toBeInTheDocument();
  });
});


describe("commit (§18)", () => {
  async function fillWholeDraft() {
    mockedSearch.mockResolvedValue([
      { id: "c-1", name: "Мария", phone_masked: "+• ••67" },
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

  it("sends exactly one identification path for an existing customer", async () => {
    renderScreen();
    await fillWholeDraft();
    screen.getByRole("button", { name: "Создать запись" }).click();

    await waitFor(() => expect(mockedCreate).toHaveBeenCalled());
    const body = mockedCreate.mock.calls[0]?.[0];
    expect(body?.client_id).toBe("c-1");
    expect(body?.client_name).toBeUndefined();
    expect(body?.idempotency_key).toBeTruthy();
  });

  it("reuses the same idempotency key when a pending submit is retried", async () => {
    // Ayla invents a key when the header is absent, so a retry with a fresh
    // one books the customer twice — the exact trap «pending» sets.
    mockedCreate.mockResolvedValue({
      outcome: "pending",
      detail: "нет ответа",
      idempotency_key: "k-1",
    });
    renderScreen();
    await fillWholeDraft();

    const btn = screen.getByRole("button", { name: "Создать запись" });
    btn.click();
    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    await screen.findByText(/могла быть создана/);

    btn.click();
    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(2));
    expect(mockedCreate.mock.calls[1]?.[0].idempotency_key).toBe(
      mockedCreate.mock.calls[0]?.[0].idempotency_key,
    );
  });

  it("never renders pending as created", async () => {
    mockedCreate.mockResolvedValue({ outcome: "pending", detail: "" });
    renderScreen();
    await fillWholeDraft();
    screen.getByRole("button", { name: "Создать запись" }).click();

    expect(await screen.findByText(/могла быть создана/)).toBeInTheDocument();
    expect(screen.queryByText("Запись создана.")).not.toBeInTheDocument();
    expect(screen.getByText(/Введённые данные сохранены/)).toBeInTheDocument();
  });

  it("keeps the draft on conflict so the user can pick another time", async () => {
    mockedCreate.mockResolvedValue({ outcome: "conflict", detail: "занято" });
    renderScreen();
    await fillWholeDraft();
    screen.getByRole("button", { name: "Создать запись" }).click();

    await screen.findByText(/это время уже занято/i);
    expect(screen.getByLabelText(/Клиент/).textContent).toMatch(/Мария/);
  });

  it("clears the draft only when the booking is actually committed", async () => {
    renderScreen();
    await fillWholeDraft();
    screen.getByRole("button", { name: "Создать запись" }).click();

    expect(await screen.findByText("Запись создана.")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText(/Клиент/).textContent).toMatch(/выбрать/),
    );
  });
});

describe("review (§18)", () => {
  it("states the salon's timezone, not the device's", async () => {
    mockedSearch.mockResolvedValue([
      { id: "c-1", name: "Мария", phone_masked: "+• ••67" },
    ]);
    renderScreen();

    // customer
    screen.getByLabelText(/Клиент/).click();
    fireEvent.change(await screen.findByLabelText("Поиск клиента"), {
      target: { value: "Мария" },
    });
    (await screen.findByText(/Мария · /)).click();

    await chooseServiceAndMaster();
    screen.getByLabelText(/Дата и время/).click();
    (await screen.findByRole("button", { name: "15:00" })).click();

    const review = await screen.findByText(/Когда:/);
    expect(review.textContent).toMatch(/Europe\/Moscow/);
  });

  it("does not invent a price the catalog never gave", async () => {
    // §12 step 5 — «do not invent missing domain fields».
    mockedSearch.mockResolvedValue([
      { id: "c-1", name: "Мария", phone_masked: "+• ••67" },
    ]);
    renderScreen();
    screen.getByLabelText(/Клиент/).click();
    fireEvent.change(await screen.findByLabelText("Поиск клиента"), {
      target: { value: "Мария" },
    });
    (await screen.findByText(/Мария · /)).click();
    await chooseServiceAndMaster();
    screen.getByLabelText(/Дата и время/).click();
    (await screen.findByRole("button", { name: "15:00" })).click();

    await screen.findByText(/Проверьте запись/);
    expect(screen.queryByText(/Цена|₽/)).not.toBeInTheDocument();
  });
});

describe("customer selection (§13, §14)", () => {
  async function openSearchAndType(text: string) {
    screen.getByLabelText(/Клиент/).click();
    const input = await screen.findByLabelText("Поиск клиента");
    fireEvent.change(input, { target: { value: text } });
  }

  it("waits for two characters before searching", async () => {
    renderScreen();
    await openSearchAndType("М");

    expect(await screen.findByText(/Введите хотя бы два символа/)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 400));
    expect(mockedSearch).not.toHaveBeenCalled();
  });

  it("renders an unreachable search as «недоступен», never as «not found»", async () => {
    renderScreen();
    await openSearchAndType("Мария");

    expect(await screen.findByText(/Поиск по клиентам пока недоступен/)).toBeInTheDocument();
    // The distinction §13 insists on: this is not evidence of absence.
    expect(screen.queryByText(/Совпадений нет/)).not.toBeInTheDocument();
  });

  it("distinguishes «found nothing» from «could not look»", async () => {
    mockedSearch.mockResolvedValue([]);
    renderScreen();
    await openSearchAndType("Мария");

    expect(await screen.findByText(/Совпадений нет/)).toBeInTheDocument();
    expect(screen.queryByText(/недоступен/)).not.toBeInTheDocument();
  });

  it("shows a match as name plus masked phone and nothing else", async () => {
    mockedSearch.mockResolvedValue([
      { id: "c-1", name: "Мария Иванова", phone_masked: "+• ••• ••• ••67" },
    ]);
    renderScreen();
    await openSearchAndType("Мария");

    const hit = await screen.findByText(/Мария Иванова · \+• ••• ••• ••67/);
    expect(hit).toBeInTheDocument();
    hit.click();

    await waitFor(() =>
      expect(screen.getByLabelText(/Клиент/).textContent).toMatch(/Мария Иванова/),
    );
  });

  it("accepts a new client from name and phone alone", async () => {
    renderScreen();
    screen.getByLabelText(/Клиент/).click();

    const name = await screen.findByLabelText("Имя клиента");
    const phone = screen.getByLabelText("Телефон клиента");
    const save = screen.getByRole("button", { name: "Сохранить клиента" });
    expect(save).toBeDisabled();

    fireEvent.change(name, { target: { value: "Мария" } });
    fireEvent.change(phone, { target: { value: "+79990000000" } });

    await waitFor(() => expect(save).toBeEnabled());
    save.click();

    await waitFor(() =>
      expect(screen.getByLabelText(/Клиент/).textContent).toMatch(/Мария/),
    );
  });
});
