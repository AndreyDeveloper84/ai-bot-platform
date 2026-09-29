/**
 * DRF-1989 — непродаваемое предложение на экране подтверждения.
 *
 * Стражи:
 * 1. котировка с ценой ниже 1 ₽ (цена услуги из зеркала, ребро без ключа
 *    `sellable`) — строки «Цена» нет, и `quoted_price` не уезжает: показанное
 *    равно отправленному (DRF-1708);
 * 2. отказ создания `offer_not_sellable` — текст сервера спокойной плашкой
 *    `role="status"`, без error-хаптики и без плашки ошибки;
 * 3. отказ котировки `offer_not_sellable` — та же плашка до нажатия
 *    «Записаться», запись не создаётся.
 */
import { render, screen, waitFor } from "@testing-library/react";
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

/** Текст сервера — рисуется дословно; своих слов у экрана нет. */
const SERVER_TEXT =
  "Сейчас записаться на эту услугу онлайн нельзя: у мастера не указана цена. " +
  "Напишите администратору салона.";

const CREATED = {
  booking: {
    id: "b-1989",
    service_name: "Маникюр",
    master_name: "Анна Соколова",
    visit_at: "2026-08-01T16:00:00+03:00",
    duration_min: 60,
    status: "confirmed",
  },
};

const FUTURE_VISIT = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString();

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/booking/confirm"]}>
      <Routes>
        <Route path="/customer/booking/confirm" element={<CustomerBookingConfirmScreen />} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
        <Route path="/customer/booking/success/:bookingId" element={<div>SUCCESS-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function inStatus(text: string): boolean {
  return Boolean(screen.queryByText(text)?.closest('[role="status"]'));
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
  setService("svc-1", "Маникюр");
  setMaster("mst-1", "Анна Соколова");
  setVisitAt(FUTURE_VISIT);
  mockedAuthVerify.mockResolvedValue({
    user: { id: "u", channel_user_id: "c", display_name: "", client_name: "" },
    tenant: { slug: "t", name: "T", timezone: "Europe/Moscow" },
    pending_booking_intent: null,
  } as never);
});

describe("непродаваемое предложение на подтверждении (DRF-1989)", () => {
  it("цена ниже 1 ₽ из котировки не рисуется и не уезжает как quoted_price", async () => {
    mockedQuote.mockResolvedValue({ price: "0.00", duration_minutes: 60, source: "service" });
    mockedCreate.mockResolvedValue(CREATED as never);
    renderScreen();

    expect(await screen.findByTestId("confirm-duration")).toHaveTextContent("1 ч");
    expect(screen.queryByTestId("confirm-price")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Записаться" }));
    const body = mockedCreate.mock.calls[0]?.[0] as unknown as Record<string, unknown>;
    expect(body).not.toHaveProperty("quoted_price");
    expect(body).toHaveProperty("quoted_duration_minutes", 60);
  });

  it("отказ создания offer_not_sellable — плашка status с текстом сервера", async () => {
    mockedQuote.mockRejectedValue(new Error("down"));
    mockedCreate.mockRejectedValue(new ApiError(409, "offer_not_sellable", SERVER_TEXT));
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: "Записаться" }));

    await waitFor(() => expect(inStatus(SERVER_TEXT)).toBe(true));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(mockedHaptic).not.toHaveBeenCalledWith("error");
  });

  it("отказ котировки offer_not_sellable — причина видна до нажатия", async () => {
    mockedQuote.mockRejectedValue(new ApiError(409, "offer_not_sellable", SERVER_TEXT));
    renderScreen();

    await waitFor(() => expect(inStatus(SERVER_TEXT)).toBe(true));
    expect(mockedCreate).not.toHaveBeenCalled();
  });
});
