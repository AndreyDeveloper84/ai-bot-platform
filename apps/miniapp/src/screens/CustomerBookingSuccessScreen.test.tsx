/**
 * Tests for `CustomerBookingSuccessScreen` — payment-aware success
 * (Wave 0 booking GO): the online path surfaces the C7.3 payment
 * status badge right on the success screen (reserved after create),
 * the no-prepayment path stays the plain confirmed success.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, hapticNotify: vi.fn(), maxBridge: () => null };
});

import { CustomerBookingSuccessScreen } from "./CustomerBookingSuccessScreen";

/** Probes that report WHICH namespace the CTA landed in, and with what id. */
function CanonicalProbe() {
  const { bookingId } = useParams();
  return <div>CANONICAL-{bookingId}</div>;
}
function LegacyProbe() {
  const { bookingId } = useParams();
  return <div>LEGACY-{bookingId}</div>;
}

function renderWithState(state: Record<string, unknown> | null) {
  render(
    <MemoryRouter
      initialEntries={[
        { pathname: "/customer/booking/success/b-1", state: state ?? undefined },
      ]}
    >
      <Routes>
        <Route
          path="/customer/booking/success/:bookingId"
          element={<CustomerBookingSuccessScreen />}
        />
        <Route
          path="/customer/records/:bookingId"
          element={<CanonicalProbe />}
        />
        <Route path="/my-visits/:bookingId" element={<LegacyProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CustomerBookingSuccessScreen (payment-aware)", () => {
  it("no-prepayment path: plain confirmed success, no payment badge", () => {
    renderWithState({
      service_name: "Маникюр",
      master_name: "Анна",
      visit_at: "2026-08-01T16:00:00+03:00",
    });
    expect(screen.getByText(/Записала тебя/)).toBeInTheDocument();
    expect(screen.queryByText("Зарезервировано")).not.toBeInTheDocument();
  });

  it("online path: payment status badge visible (C7.3 mapping)", () => {
    renderWithState({
      service_name: "Маникюр",
      master_name: "Анна",
      visit_at: "2026-08-01T16:00:00+03:00",
      payment_capture_state: "authorized",
    });
    expect(screen.getByText(/Записала тебя/)).toBeInTheDocument();
    expect(screen.getByText("Зарезервировано")).toBeInTheDocument();
  });
});

describe("CustomerBookingSuccessScreen — «Открыть запись» destination", () => {
  it("opens the CANONICAL record route with the same booking id", async () => {
    const user = userEvent.setup();
    renderWithState({ service_name: "Маникюр" });
    await user.click(screen.getByRole("button", { name: "Открыть запись" }));
    // Canonical namespace (`/customer/records/:id`), same id as the
    // booking just created — never the legacy `/my-visits` namespace.
    expect(await screen.findByText("CANONICAL-b-1")).toBeInTheDocument();
    expect(screen.queryByText("LEGACY-b-1")).not.toBeInTheDocument();
  });
});
