/**
 * Tests for `customer-records.ts` after pilot phase 3.2 — the stub lib
 * (890 lines of invented bookings, tenant groupings, AMM messages,
 * repeat-intent prefill) is replaced by the real
 * `GET /bookings/list` + `GET /bookings/<id>` endpoints. HTTP layer
 * (`./api`) mocked; fixtures are verbatim `BookingItem` rows per
 * `apps/miniapp_api/views.py::_booking_to_dict`.
 *
 * Derivation rules pinned here:
 *   - display status: confirmed + visit in the past → "completed"
 *     (the backend has no completed status; can_rate is computed the
 *     same way server-side);
 *   - is_nearest: FIRST upcoming item with visit within ≤24h;
 *   - actions come only from real flags (cancellable / reschedulable /
 *     can_rate) — "message" (AMM) and route deeplinks have no backend
 *     and are gone for the pilot.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    fetchMyBookings: vi.fn(),
    fetchBooking: vi.fn(),
  };
});

import { fetchBooking, fetchMyBookings, type BookingItem } from "./api";
import {
  getBookingDetail,
  getMyBookings,
  renderStatus,
} from "./customer-records";

const mockedList = vi.mocked(fetchMyBookings);
const mockedDetail = vi.mocked(fetchBooking);

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

beforeEach(() => {
  vi.clearAllMocks();
});

describe("getMyBookings", () => {
  it("upcoming section queries without the past flag", async () => {
    mockedList.mockResolvedValue({ items: [], next_cursor: null });
    await getMyBookings("upcoming");
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ past: false }),
    );
  });

  it("history section queries with the past flag", async () => {
    mockedList.mockResolvedValue({ items: [], next_cursor: null });
    await getMyBookings("history");
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ past: true }),
    );
  });

  it("maps real BookingItems with derived actions for an upcoming booking", async () => {
    mockedList.mockResolvedValue({
      items: [booking({ id: "b-1" })],
      next_cursor: null,
    });
    const page = await getMyBookings("upcoming");
    expect(page.totalCount).toBe(1);
    const item = page.items[0]!;
    expect(item.bookingId).toBe("b-1");
    expect(item.serviceName).toBe("Маникюр");
    expect(item.masterName).toBe("Анна Соколова");
    expect(item.status).toBe("confirmed");
    expect(item.actions).toEqual(["open", "reschedule", "cancel"]);
  });

  it("derives «Прошла» for confirmed bookings whose visit is in the past", async () => {
    mockedList.mockResolvedValue({
      items: [
        booking({
          id: "b-h1",
          visit_at: isoInHours(-30),
          cancellable: false,
          reschedulable: false,
          can_rate: true,
        }),
      ],
      next_cursor: null,
    });
    const page = await getMyBookings("history");
    const item = page.items[0]!;
    expect(item.status).toBe("completed");
    expect(renderStatus(item.status).rendering.label).toBe("Прошла");
    expect(item.actions).toEqual(["open", "repeat", "review"]);
  });

  it("keeps terminal statuses as-is in history (cancelled → «Отменена»)", async () => {
    mockedList.mockResolvedValue({
      items: [
        booking({
          id: "b-h2",
          status: "cancelled",
          visit_at: isoInHours(-80),
          cancellable: false,
          reschedulable: false,
        }),
      ],
      next_cursor: null,
    });
    const page = await getMyBookings("history");
    const item = page.items[0]!;
    expect(item.status).toBe("cancelled");
    expect(renderStatus(item.status).rendering.label).toBe("Отменена");
    expect(item.actions).toEqual(["open", "repeat"]);
  });

  it("marks only the first upcoming booking within 24h as nearest", async () => {
    mockedList.mockResolvedValue({
      items: [
        booking({ id: "b-1", visit_at: isoInHours(20) }),
        booking({ id: "b-2", visit_at: isoInHours(22) }),
        booking({ id: "b-3", visit_at: isoInHours(70) }),
      ],
      next_cursor: null,
    });
    const page = await getMyBookings("upcoming");
    expect(page.items.map((i) => [i.bookingId, i.isNearest])).toEqual([
      ["b-1", true],
      ["b-2", false],
      ["b-3", false],
    ]);
  });

  it("never emits message/route actions (no such backend in the pilot)", async () => {
    mockedList.mockResolvedValue({
      items: [booking({ id: "b-1" })],
      next_cursor: null,
    });
    const page = await getMyBookings("upcoming");
    for (const item of page.items) {
      expect(item.actions).not.toContain("message");
      expect(item.actions).not.toContain("route");
    }
  });
});

describe("getBookingDetail", () => {
  it("passes through to the real detail endpoint", async () => {
    const row = booking({ id: "b-1" });
    mockedDetail.mockResolvedValue({ booking: row });
    const detail = await getBookingDetail("b-1");
    expect(mockedDetail).toHaveBeenCalledWith("b-1");
    expect(detail).toEqual(row);
  });
});

describe("payment read model passthrough (C7.3)", () => {
  it("carries capture_state onto the list item when present", async () => {
    mockedList.mockResolvedValue({
      items: [
        booking({ id: "b-1", payment: { capture_state: "authorized" } }),
        booking({ id: "b-2" }),
      ],
      next_cursor: null,
    });
    const page = await getMyBookings("upcoming");
    expect(page.items[0]!.paymentState).toBe("authorized");
    expect(page.items[1]!.paymentState).toBeNull();
  });
});
