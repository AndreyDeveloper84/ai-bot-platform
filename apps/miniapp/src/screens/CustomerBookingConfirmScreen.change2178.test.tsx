/**
 * Кадр 4 макета DRF-1320 «Проверь запись» — строки «Изменить …» (DRF-2178, Э-3).
 *
 * Этап 3 из 4, радиус ограничен намеренно.
 *
 * Макет даёт по действию на каждую строку: «Изменить услугу», «Изменить
 * специалиста», «Изменить время». Сегодня строки есть, а поправить их
 * можно только кнопкой «назад», и человек не знает заранее, что именно
 * вернётся.
 *
 * Что заперто:
 *
 * 1. три действия названы дословно и ведут на СВОЙ шаг — услуга на выбор
 *    способа, специалист на выбор специалиста, время на выбор времени;
 * 2. уход «изменить» не стирает выбранное: черновик переживает переход
 *    (правило М-3 «ничего не сдвигается молча»), и человек возвращается
 *    к остальным строкам, а не начинает заново.
 *
 * Стража парная: к проверкам действий приложены положительные на тех же
 * данных — сами строки и кнопка подтверждения на месте.
 */
import { render, screen } from "@testing-library/react";
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
  return { ...original, getBookingQuote: vi.fn(), createCustomerBooking: vi.fn() };
});

const navigateSpy = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const original = await importOriginal<typeof import("react-router-dom")>();
  return { ...original, useNavigate: () => navigateSpy };
});

import { authVerify } from "../lib/api";
import { CHANGE_PROVIDER, CHANGE_SERVICE, CHANGE_TIME } from "../lib/booking-outcome";
import { getBookingQuote } from "../lib/customer-booking";
import { resetBooking, setMaster, setService, setVisitAt } from "../state/booking";
import { CustomerBookingConfirmScreen } from "./CustomerBookingConfirmScreen";

const mockedAuthVerify = vi.mocked(authVerify);
const mockedQuote = vi.mocked(getBookingQuote);

/**
 * Время визита — в будущем ОТНОСИТЕЛЬНО часов теста, а не прибитой
 * датой. Урок соседнего файла (DRF-1776): «2026-08-01» стало прошлым
 * 02.08 и уронило бы весь файл в один день, никого не спросив. Экран
 * считает прошедшее время устаревшим подтверждением и прячет действие.
 */
const FUTURE_VISIT = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString();

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/booking/confirm"]}>
      <Routes>
        <Route path="/customer/booking/confirm" element={<CustomerBookingConfirmScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

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

describe("«Изменить …» по каждой строке", () => {
  it("три действия названы дословно по макету", async () => {
    renderScreen();
    for (const label of [CHANGE_SERVICE, CHANGE_PROVIDER, CHANGE_TIME]) {
      expect(await screen.findByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("каждое ведёт на свой шаг, а не «назад»", async () => {
    renderScreen();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: CHANGE_SERVICE }));
    expect(navigateSpy).toHaveBeenLastCalledWith("/customer/booking/option");

    await user.click(screen.getByRole("button", { name: CHANGE_PROVIDER }));
    expect(navigateSpy).toHaveBeenLastCalledWith(
      expect.stringContaining("/customer/booking/provider"),
    );

    await user.click(screen.getByRole("button", { name: CHANGE_TIME }));
    expect(navigateSpy).toHaveBeenLastCalledWith(
      expect.stringContaining("/customer/masters/mst-1/slots"),
    );
  });

  it("положительная стража: строки и подтверждение на месте", async () => {
    renderScreen();
    expect(await screen.findByText("Лимфодренажный массаж")).toBeInTheDocument();
    expect(screen.getByText("Екатерина С.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Записаться|Подтвердить/ })).toBeInTheDocument();
  });

  it("уход «изменить» не стирает выбранное — черновик переживает переход", async () => {
    renderScreen();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: CHANGE_TIME }));
    expect(navigateSpy).toHaveBeenCalled();
    // Услуга и специалист остались: экран их всё ещё знает.
    expect(screen.getByText("Лимфодренажный массаж")).toBeInTheDocument();
    expect(screen.getByText("Екатерина С.")).toBeInTheDocument();
  });
});
