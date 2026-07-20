/**
 * Tests for `CustomerBookingDetailScreen` after pilot phase 3.2 — real
 * `GET /bookings/<id>` payload + the proven 2-step cancel with undo
 * (mirrored from `MyVisitDetailScreen`), instead of the stub detail
 * (address / cancellation policy / refund fields that have no backend).
 * HTTP layer (`../lib/api`) mocked.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
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

import {
  ApiError,
  cancelBookingConfirm,
  cancelBookingRequest,
  cancelBookingUndo,
  fetchBooking,
  type BookingItem,
} from "../lib/api";
import { CustomerBookingDetailScreen } from "./CustomerBookingDetailScreen";

const mockedFetch = vi.mocked(fetchBooking);
const mockedRequest = vi.mocked(cancelBookingRequest);
const mockedConfirm = vi.mocked(cancelBookingConfirm);
const mockedUndo = vi.mocked(cancelBookingUndo);

function isoInHours(hours: number): string {
  return new Date(Date.now() + hours * 3_600_000).toISOString();
}

function booking(partial: Partial<BookingItem> & Pick<BookingItem, "id">): BookingItem {
  return {
    status: "confirmed",
    service_id: "svc-1",
    service_name: "Маникюр",
    master_id: "mst-1",
    master_name: "Анна Соколова",
    visit_at: isoInHours(48),
    duration_min: 90,
    cancel_requested_at: null,
    undo_window_seconds: 300,
    cancellable: true,
    reschedulable: true,
    rating: null,
    can_rate: false,
    ...partial,
  };
}

const FUTURE = booking({ id: "b-1" });
const PAST = booking({
  id: "b-2",
  visit_at: isoInHours(-30),
  cancellable: false,
  reschedulable: false,
  can_rate: true,
});
const RATED = booking({
  id: "b-3",
  visit_at: isoInHours(-50),
  cancellable: false,
  reschedulable: false,
  can_rate: false,
  rating: 5,
});

function Probe() {
  const params = useParams();
  return <div>PROBE-{JSON.stringify(params)}</div>;
}

function renderScreen(bookingId: string) {
  render(
    <MemoryRouter initialEntries={[`/customer/records/${bookingId}`]}>
      <Routes>
        <Route path="/customer/records/:bookingId" element={<CustomerBookingDetailScreen />} />
        <Route path="/my-visits/:bookingId/reschedule" element={<Probe />} />
        <Route path="/feedback/:bookingId" element={<Probe />} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("CustomerBookingDetailScreen (real data)", () => {
  it("renders the real booking fields", async () => {
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");
    expect(await screen.findByText("Маникюр")).toBeInTheDocument();
    expect(screen.getByText(/Анна Соколова/)).toBeInTheDocument();
    expect(screen.getByText("Подтверждена")).toBeInTheDocument();
    expect(screen.getByText(/1 ч 30 мин/)).toBeInTheDocument();
  });

  it("runs the 2-step cancel with an undo window", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    mockedRequest.mockResolvedValue({
      booking: { ...FUTURE, status: "cancel_requested", cancellable: false, reschedulable: false },
    });
    mockedUndo.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");
    await user.click(await screen.findByRole("button", { name: "Отменить" }));
    await user.click(await screen.findByRole("button", { name: "Отменить запись" }));
    expect(mockedRequest).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Запись отменена")).toBeInTheDocument();
    // Undo — the booking comes back, the final confirm never fires.
    await user.click(screen.getByRole("button", { name: "Отменить" }));
    expect(mockedUndo).toHaveBeenCalledTimes(1);
    expect(mockedConfirm).not.toHaveBeenCalled();
  });

  it("confirms the cancel server-side once the undo window elapses", async () => {
    vi.useFakeTimers();
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    mockedRequest.mockResolvedValue({
      booking: { ...FUTURE, status: "cancel_requested", cancellable: false, reschedulable: false },
    });
    mockedConfirm.mockResolvedValue({ booking: { ...FUTURE, status: "cancelled" } });
    renderScreen("b-1");
    // fireEvent — synchronous, no userEvent timer machinery under a
    // fake clock. act() flushes promise-driven state updates.
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Отменить запись" }));
    await act(async () => {});
    expect(screen.getByText("Запись отменена")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(mockedConfirm).toHaveBeenCalledTimes(1);
    expect(mockedUndo).not.toHaveBeenCalled();
  });

  it("cancel modal closes on Escape without calling the endpoint (#953)", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");
    await user.click(await screen.findByRole("button", { name: "Отменить" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it("reschedule CTA leads to the real reschedule screen", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");
    await user.click(await screen.findByRole("button", { name: "Перенести" }));
    expect(await screen.findByText(/PROBE-\{"bookingId":"b-1"\}/)).toBeInTheDocument();
  });

  it("past booking: «Прошла» + rate CTA to the real feedback screen", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue({ booking: PAST });
    renderScreen("b-2");
    expect(await screen.findByText("Прошла")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Оценить визит" }));
    expect(await screen.findByText(/PROBE-\{"bookingId":"b-2"\}/)).toBeInTheDocument();
  });

  it("rated booking shows the given rating, no rate CTA", async () => {
    mockedFetch.mockResolvedValue({ booking: RATED });
    renderScreen("b-3");
    expect(await screen.findByText(/Оценка: 5/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Оценить визит" })).not.toBeInTheDocument();
  });

  it("shows the C7.3 payment badge when the booking carries a capture_state", async () => {
    mockedFetch.mockResolvedValue({
      booking: { ...FUTURE, payment: { capture_state: "authorized", amount: "2000.00" } },
    });
    renderScreen("b-1");
    expect(await screen.findByText("Зарезервировано")).toBeInTheDocument();
    expect(screen.getByText(/2 000 ₽/)).toBeInTheDocument();
  });

  it("never shows waiting_for_capture to the customer (ADR)", async () => {
    mockedFetch.mockResolvedValue({
      booking: { ...FUTURE, payment: { capture_state: "waiting_for_capture" } },
    });
    renderScreen("b-1");
    await screen.findByText("Маникюр");
    expect(screen.queryByText(/capture/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Зарезервировано")).not.toBeInTheDocument();
  });

  it("404 renders an honest «booking is gone» state", async () => {
    mockedFetch.mockRejectedValue(new ApiError(404, "not_found", "booking not found"));
    renderScreen("b-gone");
    expect(await screen.findByText(/Этой записи больше нет/)).toBeInTheDocument();
  });

  it("never renders stub-era fields (address / policy / refund)", async () => {
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");
    await screen.findByText("Маникюр");
    expect(screen.queryByText(/Тверская/)).not.toBeInTheDocument();
    expect(screen.queryByText(/политика отмены/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/возврат/i)).not.toBeInTheDocument();
  });
});
