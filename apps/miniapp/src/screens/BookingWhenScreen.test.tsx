/**
 * Tests for `BookingWhenScreen` — the legacy slot picker now hands the
 * draft to the payment-capable confirmation screen
 * (`/customer/booking/confirm`), so BOTH acceptance payment paths work
 * on the service-first chain too (Wave 0 booking GO).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchSlots: vi.fn() };
});

import { fetchSlots } from "../lib/api";
import {
  resetBooking,
  setMaster,
  setService,
  setVisitAt,
} from "../state/booking";
import { BookingWhenScreen } from "./BookingWhenScreen";

const mockedSlots = vi.mocked(fetchSlots);

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/book/when"]}>
      <Routes>
        <Route path="/book/when" element={<BookingWhenScreen />} />
        <Route
          path="/customer/booking/confirm"
          element={<div>CONFIRM-PROBE</div>}
        />
        <Route path="/book/confirm" element={<div>LEGACY-CONFIRM</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
  setService("svc-1", "Маникюр");
  setMaster("mst-1", "Анна Соколова");
  mockedSlots.mockResolvedValue({
    slots: [{ date: "2026-08-01", start: "2026-08-01T16:00:00+03:00" }],
  });
});

describe("BookingWhenScreen (Wave 0 flow unification)", () => {
  it("continues to the payment-capable confirm screen, not the legacy one", async () => {
    const user = userEvent.setup();
    renderScreen();
    setVisitAt("2026-08-01T16:00:00+03:00");
    await user.click(await screen.findByRole("button", { name: "Дальше" }));
    expect(await screen.findByText("CONFIRM-PROBE")).toBeInTheDocument();
    expect(screen.queryByText("LEGACY-CONFIRM")).not.toBeInTheDocument();
  });
});
