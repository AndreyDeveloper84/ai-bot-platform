/**
 * Route-registration proof for the canonical customer record detail.
 *
 * `CustomerBookingSuccessScreen`'s «Открыть запись» CTA points at
 * `/customer/records/:bookingId`. Replacing the legacy string is not
 * enough — this contour has already shipped a duplicated route and a
 * docstring that lied about where a CTA led. So the route is proven at
 * the App level: mount the real `<App />` router at the canonical path
 * and assert that (a) it resolves at all, (b) it resolves to the real
 * booking-detail screen, and (c) it hands that screen the SAME id from
 * the URL (the id `GET /bookings/<id>` is then called with).
 *
 * The legacy `/my-visits/:bookingId` route stays mounted on purpose
 * (bot-DM deep links live outside this repo) — a second case asserts it
 * still resolves, so nobody reads this change as a legacy removal.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return { ...original, getMe: vi.fn() };
});

vi.mock("./lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/api")>();
  return { ...original, fetchBooking: vi.fn() };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import { fetchBooking, type BookingItem } from "./lib/api";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedFetchBooking = vi.mocked(fetchBooking);

const CUSTOMER_ME: MeResponse = {
  user: { id: "u-1", name: "Ольга", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Demo", slug: "demo" },
  role: "customer",
  capabilities: [],
  is_customer: true,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: false,
  master_id: null,
  landing_path: "/customer/main",
  is_solo_provider: false,
};

const BOOKING: BookingItem = {
  id: "bk-77",
  status: "confirmed",
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
};

function renderAppAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedGetMe.mockResolvedValue(CUSTOMER_ME);
  mockedFetchBooking.mockResolvedValue({ booking: BOOKING });
});

describe("canonical record route registration", () => {
  it("/customer/records/:bookingId resolves to the real detail screen with that id", async () => {
    renderAppAt("/customer/records/bk-77");
    // Real booking-detail content (not a 404 / not the records list).
    expect(await screen.findByText("Маникюр")).toBeInTheDocument();
    expect(screen.getByText(/Анна Соколова/)).toBeInTheDocument();
    // The id from the URL is the id the detail screen actually loads.
    expect(mockedFetchBooking).toHaveBeenCalledWith("bk-77");
  });

  it("legacy /my-visits/:bookingId stays mounted (external deep links)", async () => {
    renderAppAt("/my-visits/bk-77");
    expect(await screen.findByText("Маникюр")).toBeInTheDocument();
  });
});
