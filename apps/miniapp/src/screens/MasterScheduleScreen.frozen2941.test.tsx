import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getMasterSchedule: vi.fn(),
    getPendingAvailability: vi.fn(),
  };
});

import {
  getMasterSchedule,
  getPendingAvailability,
  type MasterScheduleResponse,
} from "../lib/master-api";
import { formatYmdLocal } from "../lib/masterDateFormat";
import { MasterScheduleScreen } from "./MasterScheduleScreen";

const mockedSchedule = vi.mocked(getMasterSchedule);
const mockedPending = vi.mocked(getPendingAvailability);

function LocationProbe() {
  const location = useLocation();
  return <p data-testid="location">{location.pathname}{location.search}</p>;
}

function renderSchedule() {
  return render(
    <MemoryRouter initialEntries={["/master/schedule"]}>
      <Routes>
        <Route path="/master/schedule" element={<MasterScheduleScreen />} />
        <Route path="/master/booking/new" element={<LocationProbe />} />
        <Route path="/master/bookings/:id" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function freeDay(): MasterScheduleResponse {
  const date = formatYmdLocal(new Date());
  return {
    tenant_tz: "Europe/Moscow",
    from: date,
    to: date,
    days: [
      {
        date,
        is_off_day: false,
        working_hours: { start: "09:00", end: "18:00" },
        bookings: [],
        blocks: [],
        free_windows: [{ start: "09:00", end: "18:00", duration_min: 540 }],
        conflicts: [],
      },
    ],
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  mockedPending.mockResolvedValue({ items: [] });
});

describe("DRF-2941 · frozen Schedule contract", () => {
  it("fully free workday keeps the authoritative working window visible and clickable", async () => {
    mockedSchedule.mockResolvedValue(freeDay());
    renderSchedule();

    const free = await screen.findByRole("button", { name: "Записать на 09:00–18:00" });
    expect(free).toBeInTheDocument();
    expect(screen.getByText("09:00–18:00 · свободно · 540 мин")).toBeInTheDocument();
    expect(screen.queryByText("Свободный день. Отдыхайте.")).toBeNull();
  });

  it("free-window tap passes only date/from/to context to Manual Booking", async () => {
    const payload = freeDay();
    mockedSchedule.mockResolvedValue(payload);
    renderSchedule();

    await userEvent.click(
      await screen.findByRole("button", { name: "Записать на 09:00–18:00" }),
    );

    const location = screen.getByTestId("location").textContent ?? "";
    expect(location).toContain("/master/booking/new?");
    expect(location).toContain(`date=${payload.days[0]!.date}`);
    expect(location).toContain("from=09%3A00");
    expect(location).toContain("to=18%3A00");
    expect(location).not.toMatch(/[?&]start=/);
    expect(location).not.toMatch(/[?&]start_at=/);
  });

  it("booking tap stays on the appointment-detail route", async () => {
    const payload = freeDay();
    payload.days[0]!.free_windows = [];
    payload.days[0]!.bookings = [
      {
        booking_id: "b-42",
        visit_at: `${payload.days[0]!.date}T10:00:00`,
        duration_min: 60,
        service_name: "Массаж",
        client_first_name: "Анна",
        client_last_initial: "П.",
        is_in_progress: false,
        is_returning_customer: false,
      },
    ];
    mockedSchedule.mockResolvedValue(payload);
    renderSchedule();

    await userEvent.click(await screen.findByRole("link", { name: /Анна/ }));
    expect(screen.getByTestId("location")).toHaveTextContent("/master/bookings/b-42");
  });

  it("day/week/month views remain available", async () => {
    mockedSchedule.mockResolvedValue(freeDay());
    renderSchedule();

    expect(await screen.findByRole("tab", { name: "День" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Неделя" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Месяц" })).toBeInTheDocument();
  });
});
