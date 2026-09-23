/**
 * Механизм утверждений на настоящем экране (DRF-2347 × DRF-2346).
 *
 * Сам механизм доказан в `lib/claims.test.ts`. Здесь — экран, на котором
 * дефект этого класса жил: запросили отмену, источник ответил
 * `cancel_requested`, экран прочитал статус и говорил «Запись отменена».
 *
 * **DRF-2346 починен (#2024), поэтому узел здесь — сторож, а не измерение.**
 * Он держит починку: вернётся утверждение о факте при «запрошена отмена» —
 * покраснеет. Красное, доказывающее сам механизм, — в
 * `components/Snackbar.claims2347.test.tsx`: там утверждение, противоречащее
 * прочитанному, останавливает сборку.
 *
 * История одного промаха, чтобы он не повторился: первая версия узла была
 * помечена `it.fails` («лист следом»), и метка показывала зелёное — но не
 * потому, что дефект жив, а потому что узел падал на разборе адреса
 * (`:id` вместо `:bookingId`) и до кнопки не доходил. Метка «ожидаемо
 * красный» скрывает ПРИЧИНУ красноты; проверять её обязательно.
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
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("утверждение экрана при «запрошена отмена»", () => {
  it("источник говорит «запрошена отмена» — и это его слово, а не наше", () => {
    // Наличие: ответ прочитан, статус в нём именно такой. Дефект класса был
    // не в отсутствии чтения — чтение было.
    expect(
      verifyClaim({ outcome: "booking_cancel_requested", from: read("cancel_requested") }).ok,
    ).toBe(true);
  });

  it("экран не говорит «отменена», пока отмена не завершена", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue({ booking: booking("confirmed") });
    mockedRequest.mockResolvedValue({ booking: read("cancel_requested") });

    render(
      <MemoryRouter initialEntries={["/customer/records/b-2346"]}>
        <Routes>
          <Route path="/customer/records/:bookingId" element={<CustomerBookingDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", { name: "Отменить" }));
    await user.click(await screen.findByRole("button", { name: "Отменить запись" }));

    const message = (await screen.findByRole("status")).textContent ?? "";
    expect(message).toContain("Отменяю"); // наличие: сообщение о ходе дела есть
    expect(message).not.toContain("отменена");
  });
});
