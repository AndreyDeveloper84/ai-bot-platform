/**
 * `lib/customer-records` — что экран «Записи» выводит из настоящих строк
 * `BookingItem` (DRF-2952). Слой HTTP подменён.
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
  displayStatusFor,
  getBookingDetail,
  getMyBookings,
  initialsOf,
  isOrdinaryStatus,
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
    // DRF-1652 — умолчание `null`, «источник промолчал». НЕ `""`: это сказало
    // бы, что салон ответил «адреса нет», чего он не говорил.
    address: null,
    ...partial,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("getMyBookings", () => {
  it("предстоящие — без `status=past`, прошедшие — с ним; курсор уходит как `before`", async () => {
    mockedList.mockResolvedValue({ items: [], next_cursor: "c-1" });

    const upcoming = await getMyBookings("upcoming");
    expect(mockedList).toHaveBeenLastCalledWith({ past: false });
    expect(upcoming.nextCursor).toBe("c-1");

    await getMyBookings("history", "c-1");
    expect(mockedList).toHaveBeenLastCalledWith({ past: true, before: "c-1" });
  });

  it("строка несёт то, что рисует карточка, — и ничего выдуманного", async () => {
    mockedList.mockResolvedValue({
      items: [
        booking({
          id: "b-1",
          salon_name: "Салон «Ромашка»",
          master_photo_url: "/api/v1/customer/media/masters/mst-1/photo?v=1",
          address: "Пенза, Московская, 12",
          price: "2500.00",
          payment: { capture_state: "authorized", amount: "2500.00" },
        }),
      ],
      next_cursor: null,
    });

    const [item] = (await getMyBookings("upcoming")).items;
    expect(item).toMatchObject({
      bookingId: "b-1",
      status: "confirmed",
      serviceName: "Маникюр",
      masterName: "Анна Соколова",
      salonName: "Салон «Ромашка»",
      masterPhotoUrl: "/api/v1/customer/media/masters/mst-1/photo?v=1",
      address: "Пенза, Московская, 12",
      price: "2500.00",
      paymentState: "authorized",
      canRate: false,
    });
  });

  it("чего провод не несёт — пусто, а не догадка", async () => {
    mockedList.mockResolvedValue({ items: [booking({ id: "b-1" })], next_cursor: null });
    const [item] = (await getMyBookings("upcoming")).items;
    expect(item).toMatchObject({
      salonName: "",
      masterPhotoUrl: "",
      address: null,
      price: null,
      paymentState: null,
    });
  });
});

describe("статус для показа", () => {
  it("подтверждённая запись в прошлом — «прошла»; в будущем — как есть", () => {
    expect(displayStatusFor(booking({ id: "a", visit_at: isoInHours(-3) }))).toBe("completed");
    expect(displayStatusFor(booking({ id: "b", visit_at: isoInHours(3) }))).toBe("confirmed");
    expect(displayStatusFor(booking({ id: "c", status: "cancelled", visit_at: isoInHours(-3) }))).toBe(
      "cancelled",
    );
  });

  it("обычный статус своего раздела карточка не называет, любой другой — называет", () => {
    expect(isOrdinaryStatus("confirmed", "upcoming")).toBe(true);
    expect(isOrdinaryStatus("completed", "history")).toBe(true);
    expect(isOrdinaryStatus("cancelled", "history")).toBe(false);
    expect(isOrdinaryStatus("reschedule_requested", "upcoming")).toBe(false);
    // Незнакомый статус обычным не бывает — его обязаны увидеть.
    expect(isOrdinaryStatus("something_new", "upcoming")).toBe(false);
    expect(renderStatus("cancelled").rendering.label).toBe("Отменена");
  });
});

describe("getBookingDetail и мелочи", () => {
  it("одна запись — настоящий GET /bookings/<id>", async () => {
    const row = booking({ id: "b-9" });
    mockedDetail.mockResolvedValue({ booking: row });
    expect(await getBookingDetail("b-9")).toBe(row);
    expect(mockedDetail).toHaveBeenCalledWith("b-9");
  });

  it("инициалы — плитка вместо фото", () => {
    expect(initialsOf("Анна Соколова")).toBe("АС");
    expect(initialsOf("  ольга ")).toBe("О");
    expect(initialsOf("")).toBe("");
  });
});
