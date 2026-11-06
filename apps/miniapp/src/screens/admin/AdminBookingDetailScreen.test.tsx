import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelSalonBooking,
  getBookingSlots,
  getAdminBookingDetail,
  rescheduleSalonBooking,
} from "../../lib/admin-api";
import { AdminBookingDetailScreen } from "./AdminBookingDetailScreen";

vi.mock("../../lib/admin-api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/admin-api")>("../../lib/admin-api");
  return {
    ...actual,
    getAdminBookingDetail: vi.fn(),
    getBookingSlots: vi.fn(),
    cancelSalonBooking: vi.fn(),
    rescheduleSalonBooking: vi.fn(),
  };
});

const mockedDetail = vi.mocked(getAdminBookingDetail);
const mockedSlots = vi.mocked(getBookingSlots);
const mockedCancel = vi.mocked(cancelSalonBooking);
const mockedReschedule = vi.mocked(rescheduleSalonBooking);

const DETAIL = {
  id: "11111111-1111-4111-8111-111111111111",
  version: 7,
  status: "confirmed",
  start_datetime: "2026-10-21T10:00:00+03:00",
  end_datetime: "2026-10-21T11:00:00+03:00",
  duration_min: 60,
  service_id: "svc-1",
  service_name: "Классический массаж",
  client_first_name: "Анна",
  client_last_initial: "П.",
  master_id: "master-1",
  master_name: "Мария",
  is_in_progress: false,
};

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={[`/admin/bookings/${DETAIL.id}`]}>
      <Routes>
        <Route path="/admin/bookings/:id" element={<AdminBookingDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedDetail.mockResolvedValue(DETAIL);
  mockedSlots.mockResolvedValue({
    date: "2026-10-22",
    timezone: "Europe/Moscow",
    master_id: "master-1",
    service_id: "svc-1",
    duration_min: 60,
    slots: [{ time: "12:30", start_at: "2026-10-22T12:30:00+03:00", duration_min: 60 }],
  });
  mockedCancel.mockResolvedValue({ outcome: "committed", detail: "", appointment_id: DETAIL.id });
  mockedReschedule.mockResolvedValue({ outcome: "committed", detail: "", appointment_id: DETAIL.id });
});

describe("Salon Appointment Detail v2 — DRF-2935", () => {
  it("shows only frozen P0 facts/actions and no phone/complete/no-show", async () => {
    renderScreen();

    expect(await screen.findByText("Анна П.")).toBeInTheDocument();
    expect(screen.getByText("Классический массаж")).toBeInTheDocument();
    expect(screen.getByText("Мария")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Перенести" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeInTheDocument();
    expect(screen.queryByText(/телефон/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /заверш/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /не приш/i })).toBeNull();
  });

  it("reschedules only to an authoritative slot with the displayed version", async () => {
    renderScreen();
    await screen.findByText("Анна П.");

    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
    const date = screen.getByLabelText("Дата");
    await userEvent.clear(date);
    await userEvent.type(date, "2026-10-22");
    await userEvent.click(screen.getByRole("button", { name: "Показать свободное время" }));

    expect(mockedSlots).toHaveBeenCalledWith({
      masterId: "master-1",
      serviceId: "svc-1",
      date: "2026-10-22",
    });
    await userEvent.click(await screen.findByRole("button", { name: "12:30" }));
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить перенос" }));

    await waitFor(() =>
      expect(mockedReschedule).toHaveBeenCalledWith(
        DETAIL.id,
        7,
        "2026-10-22T12:30:00+03:00",
      ),
    );
  });
});
