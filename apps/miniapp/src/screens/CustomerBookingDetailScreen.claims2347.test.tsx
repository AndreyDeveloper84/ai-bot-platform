/**
 * Настоящий случай для механизма утверждений (DRF-2347 × DRF-2346).
 *
 * Механизм сам по себе доказан в `lib/claims.test.ts`. Этот узел проверяет,
 * что он ловит **живой дефект**: экран запрашивает отмену, источник отвечает
 * `cancel_requested`, экран читает этот статус — и говорит «Запись отменена».
 *
 * ## Почему `it.fails`
 *
 * DRF-2346 ещё не починен, и узел обязан краснеть на нём **сегодня**. Зелёный
 * с самого начала узел ничего не доказывал бы. `it.fails` — та же форма, что
 * strict-xfail в реестрах бота: сейчас он зелёный **потому что внутри
 * красное**, а когда DRF-2346 починят, узел упадёт и потребует снять метку.
 * Тогда `it.fails` меняется на `it`, и проверка становится обычным сторожем.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    fetchBooking: vi.fn(),
    cancelBookingRequest: vi.fn(),
    cancelBookingConfirm: vi.fn(),
    cancelBookingUndo: vi.fn(),
  };
});

import { cancelBookingRequest, fetchBooking, type BookingItem } from "../lib/api";
import { markRead, verifyClaim, type Read } from "../lib/claims";
import { CustomerBookingDetailScreen } from "./CustomerBookingDetailScreen";

const mockedFetch = vi.mocked(fetchBooking);
const mockedRequest = vi.mocked(cancelBookingRequest);

function booking(status: BookingItem["status"]): BookingItem {
  return {
    id: "b-2346",
    status,
    service_id: "svc-1",
    service_name: "Маникюр",
    master_id: "mst-1",
    master_name: "Анна Соколова",
    visit_at: new Date(Date.now() + 48 * 3_600_000).toISOString(),
    duration_min: 90,
    cancel_requested_at: null,
    undo_window_seconds: 300,
    cancellable: true,
    reschedulable: true,
    rating: null,
    can_rate: false,
  } as BookingItem;
}

/** Ответ источника, как его отдаёт клиент, — с меткой прочитанного. */
function read(status: BookingItem["status"]): Read<BookingItem> {
  return markRead(booking(status), { source: "/bookings/b-2346/cancel/", status: 200 });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mockedFetch.mockResolvedValue({ booking: booking("confirmed") });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

async function cancelAndReadMessage(): Promise<string> {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  mockedRequest.mockResolvedValue({ booking: read("cancel_requested") });

  render(
    <MemoryRouter initialEntries={["/customer/bookings/b-2346"]}>
      <Routes>
        <Route path="/customer/bookings/:id" element={<CustomerBookingDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );

  await user.click(await screen.findByRole("button", { name: "Отменить" }));
  await user.click(await screen.findByRole("button", { name: "Отменить запись" }));
  return (await screen.findByRole("status")).textContent ?? "";
}

describe("утверждение экрана при cancel_requested", () => {
  it("источник говорит «запрошена отмена» — и это ЕГО слово, а не наше", () => {
    // Наличие: ответ прочитан, статус в нём именно такой. Дефект не в том,
    // что чтения не было, — чтение было.
    expect(verifyClaim({ outcome: "booking_cancel_requested", from: read("cancel_requested") }).ok).toBe(
      true,
    );
  });

  it.fails(
    "лист следом (DRF-2346): экран не вправе говорить «отменена» до завершения отмены",
    async () => {
      const message = await cancelAndReadMessage();

      // Сегодня здесь «Запись отменена» — при статусе `cancel_requested`.
      // Когда DRF-2346 починят, эта проверка станет верной, узел упадёт
      // из-за `it.fails`, и метку нужно будет снять.
      expect(message).not.toContain("отменена");
    },
  );
});
