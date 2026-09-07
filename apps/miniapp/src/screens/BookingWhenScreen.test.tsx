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
    <MemoryRouter initialEntries={["/customer/book/when"]}>
      <Routes>
        <Route path="/customer/book/when" element={<BookingWhenScreen />} />
        <Route
          path="/customer/booking/confirm"
          element={<div>CONFIRM-PROBE</div>}
        />
        <Route path="/book/confirm" element={<div>LEGACY-CONFIRM</div>} />
        <Route path="/customer/catalog" element={<div>CATALOG-LIVE</div>} />
        <Route path="/catalog" element={<div>CATALOG-LEGACY</div>} />
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

describe("BookingWhenScreen — запасной выход (DRF-1485 §3)", () => {
  it("потерянный черновик уводит в /customer/catalog, не в /catalog", async () => {
    // §31 оставил этот экран жить навсегда, значит и его аварийный
    // выход обязан вести в живое поколение. До правки он вёл в
    // `/catalog` — экран прежнего, оставленный лишь алиасом.
    resetBooking();
    renderScreen();

    expect(await screen.findByText("CATALOG-LIVE")).toBeInTheDocument();
    expect(screen.queryByText("CATALOG-LEGACY")).not.toBeInTheDocument();
    // Редирект случился до загрузки слотов — экрана не было вовсе.
    expect(mockedSlots).not.toHaveBeenCalled();
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
