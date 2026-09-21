/**
 * Кадры 5–6 макета DRF-1320: «Создаю запись…» → «Запись подтверждена»
 * (DRF-2178, Э-3).
 *
 * Этап 3 из 4, радиус ограничен намеренно.
 *
 * Кадр 5 сегодня — подпись на кнопке («Записываю…»), и человек видит
 * прежний экран с полями, которые уже ничего не решают. Макет даёт свой
 * кадр: что происходит, сколько это обычно длится и где ещё появится
 * результат (ПРАВКА 4: «Результат также появится в чате Ayla» — это
 * снижает тревогу и, с ней, риск повторной записи).
 *
 * Что заперто:
 *
 * 1. во время создания виден кадр 5, а не прежняя форма; действие
 *    заблокировано — повторный submit невозможен;
 * 2. ПРАВКА 4 сказана дословно;
 * 3. кадр 6 показывает идентификатор записи, который печатался и до
 *    этого этапа, — но НЕ выдаёт его за короткий номер макета «№ 48217»:
 *    такого номера у нас нет, и подставить под «№» UUID значило бы выдать
 *    ключ за номер (предел назван в теле PR);
 * 4. адрес на кадре 6 показывается, когда источник его дал, и честной
 *    фразой, когда промолчал (`visitAddressText`, DRF-1952).
 */
import { render, screen } from "@testing-library/react";
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
  return { ...original, getBookingQuote: vi.fn(), createCustomerBooking: vi.fn() };
});

import userEvent from "@testing-library/user-event";

import { authVerify } from "../lib/api";
import { CREATING_HEAD, CREATING_HINT, CREATING_ALSO_IN_CHAT } from "../lib/booking-outcome";
import { createCustomerBooking, getBookingQuote } from "../lib/customer-booking";
import { resetBooking, setMaster, setService, setVisitAt } from "../state/booking";
import { CustomerBookingConfirmScreen } from "./CustomerBookingConfirmScreen";
import { CustomerBookingSuccessScreen } from "./CustomerBookingSuccessScreen";

const mockedAuthVerify = vi.mocked(authVerify);
const mockedQuote = vi.mocked(getBookingQuote);
const mockedCreate = vi.mocked(createCustomerBooking);

const FUTURE_VISIT = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString();

const CREATED = {
  booking: {
    id: "11111111-1111-1111-1111-111111111111",
    service_name: "Лимфодренажный массаж",
    master_name: "Екатерина С.",
    visit_at: FUTURE_VISIT,
    duration_min: 60,
    status: "confirmed",
    address: "ул. Лесная, 20, Москва",
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedAuthVerify.mockResolvedValue({
    user: { id: "u-1", channel_user_id: "cu-1", display_name: "Ольга", client_name: "" },
    tenant: { slug: "demo", name: "Demo", timezone: "Europe/Moscow" },
    pending_booking_intent: null,
  });
  mockedQuote.mockResolvedValue({ price_rub: "3200.00", duration_minutes: 60 } as never);
  resetBooking();
  setService("svc-1", "Лимфодренажный массаж");
  setMaster("mst-1", "Екатерина С.");
  setVisitAt(FUTURE_VISIT);
});

describe("кадр 5 «Создаю запись…»", () => {
  it("виден во время создания и говорит правку 4 дословно", async () => {
    // Создание «зависает» — ровно то состояние, которое рисует кадр 5.
    mockedCreate.mockImplementation(() => new Promise(() => {}) as never);
    render(
      <MemoryRouter initialEntries={["/customer/booking/confirm"]}>
        <Routes>
          <Route path="/customer/booking/confirm" element={<CustomerBookingConfirmScreen />} />
        </Routes>
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Записаться|Подтвердить/ }));

    expect(await screen.findByText(CREATING_HEAD)).toBeInTheDocument();
    expect(screen.getByText(CREATING_HINT)).toBeInTheDocument();
    expect(screen.getByText(CREATING_ALSO_IN_CHAT)).toBeInTheDocument();
  });

  it("повторный submit невозможен: действия на кадре нет", async () => {
    mockedCreate.mockImplementation(() => new Promise(() => {}) as never);
    render(
      <MemoryRouter initialEntries={["/customer/booking/confirm"]}>
        <Routes>
          <Route path="/customer/booking/confirm" element={<CustomerBookingConfirmScreen />} />
        </Routes>
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Записаться|Подтвердить/ }));
    await screen.findByText(CREATING_HEAD);

    const submit = screen.queryByRole("button", { name: /Записаться|Подтвердить/ });
    expect(submit === null || (submit as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("кадр 6 «Запись подтверждена»", () => {
  function renderSuccess(state: unknown) {
    render(
      <MemoryRouter
        initialEntries={[{ pathname: `/customer/booking/success/${CREATED.booking.id}`, state }]}
      >
        <Routes>
          <Route
            path="/customer/booking/success/:bookingId"
            element={<CustomerBookingSuccessScreen />}
          />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("адрес показан, когда источник его дал", () => {
    renderSuccess(CREATED.booking);
    expect(screen.getByText(/Лесная, 20/)).toBeInTheDocument();
  });

  it("идентификатор показан как есть и НЕ выдаётся за короткий номер макета", () => {
    // Экран печатает «Номер записи» с идентификатором ещё до этого
    // этапа — стирать не станем: человеку есть что назвать в салоне.
    // Макет просит «№ 48217» — короткий человеческий номер, которого у
    // нас нет. Значит нельзя подставлять под «№» UUID: это выдало бы
    // ключ за номер. Предел назван в теле PR.
    renderSuccess(CREATED.booking);
    const text = document.body.textContent ?? "";
    expect(text).toContain("Номер записи");
    expect(text).toContain(CREATED.booking.id);
    expect(text).not.toMatch(/№/);
  });
});
