/**
 * C05.4 — состояния выбора времени с именем и локальным выходом (DRF-1776).
 *
 * Тело C05 (DRF-1271): no-slots → «следующие даты / другой специалист»;
 * unavailable — своё имя, не «ошибка»; expired pending — понятное
 * восстановление; «ошибка выбора слота не должна возвращать в C01».
 *
 * Стражи:
 * 1. нет окон → «Другие даты» сдвигает окно на 14 дней (запрос с
 *    offsetDays), «Другой специалист» ведёт к выбору мастера под ту же
 *    услугу; после потолка «Другие даты» исчезает, текст честный;
 * 2. окно позже обычного названо, «Ближайшие» возвращает;
 * 3. возврат с подтверждения по 409 называет занятый слот;
 * 4. подтверждение с прошедшим временем — «устарело», «Записаться»
 *    нет, есть «Выбрать время заново»; положительная стража — будущее
 *    время рисует прежнюю кнопку.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, getInitData: () => "test-init-data" };
});

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, authVerify: vi.fn() };
});

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCustomerSlots: vi.fn(), createCustomerBooking: vi.fn() };
});

import { authVerify } from "../lib/api";
import { getCustomerSlots } from "../lib/customer-booking";
import { resetBooking, setMaster, setService, setVisitAt } from "../state/booking";
import { CustomerBookingConfirmScreen } from "./CustomerBookingConfirmScreen";
import { CustomerSlotsScreen, OTHER_DATES_LABEL, OTHER_MASTER_LABEL } from "./CustomerSlotsScreen";

const mockedSlots = vi.mocked(getCustomerSlots);
const mockedAuthVerify = vi.mocked(authVerify);

const DAY = "2026-10-10";
const SLOTS = [
  { date: DAY, start: `${DAY}T10:00:00+03:00` },
  { date: DAY, start: `${DAY}T11:30:00+03:00` },
];

function renderSlots(state?: Record<string, unknown>) {
  render(
    <MemoryRouter initialEntries={[{ pathname: "/customer/masters/mst-1/slots", state }]}>
      <Routes>
        <Route path="/customer/masters/:masterId/slots" element={<CustomerSlotsScreen />} />
        <Route path="/customer/book/master" element={<div>MASTER-PICKER-PROBE</div>} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderConfirm() {
  render(
    <MemoryRouter initialEntries={["/customer/booking/confirm"]}>
      <Routes>
        <Route path="/customer/booking/confirm" element={<CustomerBookingConfirmScreen />} />
        <Route path="/customer/masters/:masterId/slots" element={<div>SLOTS-PROBE</div>} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
  setService("svc-1", "Маникюр");
  setMaster("mst-1", "Анна Соколова");
  mockedAuthVerify.mockResolvedValue({
    user: { id: "u", channel_user_id: "c", display_name: "", client_name: "" },
    tenant: { slug: "t", name: "T", timezone: "Europe/Moscow" },
    pending_booking_intent: null,
  } as never);
});

describe("нет окон — локальные выходы", () => {
  it("«Другие даты» сдвигает окно на 14 дней; «Другой специалист» — к выбору мастера под ту же услугу", async () => {
    mockedSlots.mockResolvedValue({ slots: [] });
    renderSlots();

    const callout = await screen.findByRole("status");
    expect(callout).toHaveTextContent("Анна Соколова занята на 2 недели вперёд.");
    expect(mockedSlots).toHaveBeenLastCalledWith(expect.objectContaining({ offsetDays: 0 }));

    await userEvent.click(within(callout).getByRole("button", { name: OTHER_DATES_LABEL }));
    expect(mockedSlots).toHaveBeenLastCalledWith(expect.objectContaining({ offsetDays: 14 }));
    expect(await screen.findByText("Анна Соколова занята и на следующие 2 недели.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: OTHER_MASTER_LABEL }));
    expect(await screen.findByText("MASTER-PICKER-PROBE")).toBeInTheDocument();
  });

  it("после потолка «Другие даты» исчезает, текст говорит почему", async () => {
    mockedSlots.mockResolvedValue({ slots: [] });
    renderSlots();
    await screen.findByRole("status");
    // 0 → 14 → 28 → 42: на 42 следующий шаг вышел бы за 56 дней.
    for (let i = 0; i < 3; i += 1) {
      await userEvent.click(await screen.findByRole("button", { name: OTHER_DATES_LABEL }));
      await screen.findByText(/занята и на следующие/);
    }
    expect(mockedSlots).toHaveBeenLastCalledWith(expect.objectContaining({ offsetDays: 42 }));
    expect(screen.queryByRole("button", { name: OTHER_DATES_LABEL })).toBeNull();
    expect(screen.getByText(/Дальше расписание ещё не открыто/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: OTHER_MASTER_LABEL })).toBeInTheDocument();
  });

  it("окно позже обычного названо над списком, «Ближайшие» возвращает", async () => {
    mockedSlots.mockResolvedValueOnce({ slots: [] }).mockResolvedValueOnce({ slots: SLOTS });
    renderSlots();
    await userEvent.click(await screen.findByRole("button", { name: OTHER_DATES_LABEL }));

    expect(await screen.findByText(/Окна на две недели позже обычного/)).toBeInTheDocument();
    mockedSlots.mockResolvedValueOnce({ slots: SLOTS });
    await userEvent.click(screen.getByRole("button", { name: "Ближайшие" }));
    expect(mockedSlots).toHaveBeenLastCalledWith(expect.objectContaining({ offsetDays: 0 }));
  });
});

describe("слот заняли между выбором и подтверждением", () => {
  it("возврат по 409 называет занятое время", async () => {
    mockedSlots.mockResolvedValue({ slots: SLOTS });
    renderSlots({ unavailableSlot: `${DAY}T10:00:00+03:00` });
    const note = await screen.findByTestId("slot-unavailable-note");
    expect(note).toHaveTextContent(/уже заняли — выбери другое время/);
    // Список на месте — человек остался здесь, не в C01.
    expect(screen.getAllByRole("button", { name: /10:00|11:30/ }).length).toBeGreaterThan(0);
  });

  it("положительная стража: без state заметки нет", async () => {
    mockedSlots.mockResolvedValue({ slots: SLOTS });
    renderSlots();
    await screen.findAllByRole("button", { name: /11:30/ });
    expect(screen.queryByTestId("slot-unavailable-note")).toBeNull();
  });
});

describe("подтверждение устарело", () => {
  it("прошедшее время → «устарело», «Записаться» нет, «Выбрать время заново» ведёт к слотам", async () => {
    setVisitAt(new Date(Date.now() - 3600 * 1000).toISOString());
    renderConfirm();
    expect(await screen.findByTestId("confirm-stale")).toHaveTextContent("Подтверждение устарело");
    expect(screen.queryByRole("button", { name: "Записаться" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Выбрать время заново" }));
    expect(await screen.findByText("SLOTS-PROBE")).toBeInTheDocument();
  });

  it("положительная стража: будущее время — прежняя «Записаться», без заметки", async () => {
    setVisitAt(new Date(Date.now() + 24 * 3600 * 1000).toISOString());
    renderConfirm();
    expect(await screen.findByRole("button", { name: "Записаться" })).toBeInTheDocument();
    expect(screen.queryByTestId("confirm-stale")).toBeNull();
  });
});
