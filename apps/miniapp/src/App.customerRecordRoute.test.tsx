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
 * DRF-1625: легаси-`/my-visits/:bookingId` снят вместе со своим
 * экраном — продюсеров этого адреса не нашлось ни в коде, ни в истории
 * (замер в комментарии у маршрутов в `App.tsx`). Псевдоним экрана
 * переноса `/my-visits/:bookingId/reschedule` остался: за ним стоит тот
 * же `RescheduleScreen`, что и на каноническом адресе, — второй кейс
 * ниже это и проверяет.
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
  return { ...original, fetchBooking: vi.fn(), fetchSlots: vi.fn() };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import { fetchBooking, fetchSlots, type BookingItem } from "./lib/api";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedFetchBooking = vi.mocked(fetchBooking);
const mockedFetchSlots = vi.mocked(fetchSlots);

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
  mockedFetchSlots.mockResolvedValue({ slots: [] });
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
});

describe("canonical reschedule route registration (DRF-1481)", () => {
  it("/customer/records/:bookingId/reschedule resolves to the real reschedule screen", async () => {
    renderAppAt("/customer/records/bk-77/reschedule");
    // Real reschedule screen (not a 404 / not the booking detail).
    expect(
      await screen.findByRole("heading", { name: "Перенести" }),
    ).toBeInTheDocument();
    // The id from the URL is the id the screen actually loads.
    expect(mockedFetchBooking).toHaveBeenCalledWith("bk-77");
  });

  it("legacy /my-visits/:bookingId/reschedule alias mounts the same screen", async () => {
    // Compatibility alias — страховка для старых внешних ссылок,
    // ушедших наружу ранее. Тот же компонент, тот же id.
    renderAppAt("/my-visits/bk-77/reschedule");
    expect(
      await screen.findByRole("heading", { name: "Перенести" }),
    ).toBeInTheDocument();
    expect(mockedFetchBooking).toHaveBeenCalledWith("bk-77");
  });
});
