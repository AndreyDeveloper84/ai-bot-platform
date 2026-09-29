/**
 * DRF-1708 — цена и длительность на подтверждении: показанное и есть
 * отправленное; расхождение — кадр «было → стало», новое подтверждение.
 *
 * Решение владельца (пакет 2, D4): авторитетна ровно та execution option,
 * которую клиент видел и подтвердил; расхождение → MATERIAL_CHANGE →
 * показать → новое подтверждение; не silent normalization.
 *
 * Стражи:
 * 1. котировка рисуется в карточке и уезжает как `quoted_*` — те же
 *    значения, что на экране;
 * 2. без котировки (ответа нет) — строк нет и полей в запросе нет
 *    (положительная стража прежнего контракта); `null` не рисуется;
 * 3. `quote_changed` → кадр с обеими парами, записи нет, error-хаптики
 *    нет; «Подтвердить с новыми условиями» переписывает карточку и
 *    ТОЛЬКО следующее «Записаться» шлёт новое значение;
 * 4. «Выбрать другое время» уводит к слотам.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return {
    ...original,
    getInitData: () => "test-init-data",
    openPaymentConfirmation: vi.fn(),
    hapticNotify: vi.fn(),
  };
});

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, authVerify: vi.fn() };
});

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return {
    ...original,
    createCustomerBooking: vi.fn(),
    getBookingQuote: vi.fn(),
  };
});

import { ApiError, authVerify } from "../lib/api";
import { createCustomerBooking, getBookingQuote } from "../lib/customer-booking";
import { hapticNotify } from "../lib/max-sdk";
import { resetBooking, setMaster, setService, setVisitAt } from "../state/booking";
import { CustomerBookingConfirmScreen } from "./CustomerBookingConfirmScreen";

const mockedAuthVerify = vi.mocked(authVerify);
const mockedCreate = vi.mocked(createCustomerBooking);
const mockedQuote = vi.mocked(getBookingQuote);
const mockedHaptic = vi.mocked(hapticNotify);

const CREATED = {
  booking: {
    id: "b-1",
    service_name: "Маникюр",
    master_name: "Анна Соколова",
    visit_at: "2026-08-01T16:00:00+03:00",
    duration_min: 60,
    status: "confirmed",
  },
};

/** В будущем относительно часов теста (DRF-1776): прошедшее время экран
 * считает устаревшим подтверждением и прячет «Записаться». */
const FUTURE_VISIT = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString();

function seedDraft() {
  resetBooking();
  setService("svc-1", "Маникюр");
  setMaster("mst-1", "Анна Соколова");
  setVisitAt(FUTURE_VISIT);
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/booking/confirm"]}>
      <Routes>
        <Route path="/customer/booking/confirm" element={<CustomerBookingConfirmScreen />} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
        <Route path="/customer/booking/success/:bookingId" element={<div>SUCCESS-PROBE</div>} />
        <Route path="/customer/masters/:masterId/slots" element={<div>SLOTS-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function quoteChanged(field: "price" | "duration_minutes", quoted: string | number, applied: string | number) {
  return new ApiError(409, "quote_changed", "price or duration changed since it was shown", {
    field,
    quoted,
    applied,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  seedDraft();
  mockedAuthVerify.mockResolvedValue({
    user: { id: "u", channel_user_id: "c", display_name: "", client_name: "" },
    tenant: { slug: "t", name: "T", timezone: "Europe/Moscow" },
    pending_booking_intent: null,
  } as never);
});

describe("котировка на карточке = отправленное (DRF-1708)", () => {
  it("цена и длительность из котировки рисуются и уезжают как quoted_*", async () => {
    mockedQuote.mockResolvedValue({ price: "1500.00", duration_minutes: 60, source: "edge" });
    mockedCreate.mockResolvedValue(CREATED as never);
    renderScreen();

    expect(await screen.findByTestId("confirm-price")).toHaveTextContent("1 500 ₽");
    expect(screen.getByTestId("confirm-duration")).toHaveTextContent("1 ч");

    await userEvent.click(screen.getByRole("button", { name: "Записаться" }));

    expect(mockedCreate).toHaveBeenCalledWith(
      expect.objectContaining({ quoted_price: "1500.00", quoted_duration_minutes: 60 }),
    );
    expect(await screen.findByText("SUCCESS-PROBE")).toBeInTheDocument();
  });

  it("без котировки — ни строк, ни полей: прежний контракт", async () => {
    mockedQuote.mockRejectedValue(new Error("down"));
    mockedCreate.mockResolvedValue(CREATED as never);
    renderScreen();

    await screen.findByRole("button", { name: "Записаться" });
    expect(screen.queryByTestId("confirm-price")).toBeNull();
    expect(screen.queryByTestId("confirm-duration")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Записаться" }));
    const body = mockedCreate.mock.calls[0]?.[0] as unknown as Record<string, unknown>;
    expect(body).not.toHaveProperty("quoted_price");
    expect(body).not.toHaveProperty("quoted_duration_minutes");
  });

  it("null в котировке не рисуется и не шлётся — только известное", async () => {
    mockedQuote.mockResolvedValue({ price: null, duration_minutes: 45, source: "service" });
    mockedCreate.mockResolvedValue(CREATED as never);
    renderScreen();

    expect(await screen.findByTestId("confirm-duration")).toHaveTextContent("45 мин");
    expect(screen.queryByTestId("confirm-price")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Записаться" }));
    const body = mockedCreate.mock.calls[0]?.[0] as unknown as Record<string, unknown>;
    expect(body).not.toHaveProperty("quoted_price");
    expect(body).toHaveProperty("quoted_duration_minutes", 45);
  });
});

describe("MATERIAL_CHANGE — было → стало, новое подтверждение (D4)", () => {
  it("цена изменилась: кадр с обеими суммами, записи нет, без error-хаптики", async () => {
    mockedQuote.mockResolvedValue({ price: "1500.00", duration_minutes: 60, source: "edge" });
    mockedCreate.mockRejectedValueOnce(quoteChanged("price", "1500.00", "1700.00"));
    renderScreen();
    await screen.findByTestId("confirm-price");

    await userEvent.click(screen.getByRole("button", { name: "Записаться" }));

    const frame = await screen.findByTestId("quote-changed");
    expect(frame).toHaveTextContent("было 1 500 ₽, стало 1 700 ₽");
    expect(frame).toHaveTextContent("Запись не создана");
    expect(screen.queryByText("SUCCESS-PROBE")).toBeNull();
    expect(mockedHaptic).not.toHaveBeenCalledWith("error");
    // Карточка всё ещё показывает то, что человек видел, — до его решения.
    expect(screen.getByTestId("confirm-price")).toHaveTextContent("1 500 ₽");
  });

  it("«Подтвердить с новыми условиями» переписывает карточку; запись — только следующим «Записаться» с новым значением", async () => {
    mockedQuote.mockResolvedValue({ price: "1500.00", duration_minutes: 60, source: "edge" });
    mockedCreate
      .mockRejectedValueOnce(quoteChanged("price", "1500.00", "1700.00"))
      .mockResolvedValueOnce(CREATED as never);
    renderScreen();
    await screen.findByTestId("confirm-price");

    await userEvent.click(screen.getByRole("button", { name: "Записаться" }));
    const frame = await screen.findByTestId("quote-changed");
    await userEvent.click(within(frame).getByRole("button", { name: "Подтвердить с новыми условиями" }));

    // Кадр снят, карточка показывает применяемое, запись ещё НЕ создана.
    expect(screen.queryByTestId("quote-changed")).toBeNull();
    expect(screen.getByTestId("confirm-price")).toHaveTextContent("1 700 ₽");
    expect(mockedCreate).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: "Записаться" }));
    expect(mockedCreate).toHaveBeenCalledTimes(2);
    expect(mockedCreate.mock.calls[1]?.[0]).toEqual(
      expect.objectContaining({ quoted_price: "1700.00", quoted_duration_minutes: 60 }),
    );
    expect(await screen.findByText("SUCCESS-PROBE")).toBeInTheDocument();
  });

  it("длительность изменилась: своё поле, своя фраза", async () => {
    mockedQuote.mockResolvedValue({ price: "1500.00", duration_minutes: 60, source: "edge" });
    mockedCreate.mockRejectedValueOnce(quoteChanged("duration_minutes", 60, 45));
    renderScreen();
    await screen.findByTestId("confirm-price");

    await userEvent.click(screen.getByRole("button", { name: "Записаться" }));
    const frame = await screen.findByTestId("quote-changed");
    expect(frame).toHaveTextContent("длительность изменилась: было 1 ч, стало 45 мин");

    await userEvent.click(within(frame).getByRole("button", { name: "Подтвердить с новыми условиями" }));
    expect(screen.getByTestId("confirm-duration")).toHaveTextContent("45 мин");
    expect(screen.getByTestId("confirm-price")).toHaveTextContent("1 500 ₽");
  });

  it("«Выбрать другое время» уводит к слотам", async () => {
    mockedQuote.mockResolvedValue({ price: "1500.00", duration_minutes: 60, source: "edge" });
    mockedCreate.mockRejectedValueOnce(quoteChanged("price", "1500.00", "1700.00"));
    renderScreen();
    await screen.findByTestId("confirm-price");
    await userEvent.click(screen.getByRole("button", { name: "Записаться" }));
    const frame = await screen.findByTestId("quote-changed");
    await userEvent.click(within(frame).getByRole("button", { name: "Выбрать другое время" }));
    expect(await screen.findByText("SLOTS-PROBE")).toBeInTheDocument();
  });

  it("положительная стража: занятый слот — прежний кадр, не quote_changed", async () => {
    mockedQuote.mockResolvedValue({ price: "1500.00", duration_minutes: 60, source: "edge" });
    mockedCreate.mockRejectedValueOnce(new ApiError(409, "slot_unavailable", "taken"));
    renderScreen();
    await screen.findByTestId("confirm-price");
    await userEvent.click(screen.getByRole("button", { name: "Записаться" }));
    expect(await screen.findByText("Это время только что заняли.")).toBeInTheDocument();
    expect(screen.queryByTestId("quote-changed")).toBeNull();
  });
});
