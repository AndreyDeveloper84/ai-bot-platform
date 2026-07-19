/**
 * Unit tests for the booking-flow draft store (`state/booking.ts`).
 * The store is module-global — every test resets it to keep order
 * independence.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  getBookingDraft,
  resetBooking,
  setMaster,
  setRescheduleContext,
  setService,
  setVisitAt,
} from "./booking";

beforeEach(() => {
  resetBooking();
});

describe("booking draft store", () => {
  it("starts empty", () => {
    expect(getBookingDraft()).toEqual({
      serviceId: null,
      serviceName: null,
      masterId: null,
      masterName: null,
      visitAt: null,
      rescheduleOf: null,
    });
  });

  it("accumulates service → master → slot selections", () => {
    setService("svc-1", "Маникюр");
    setMaster("mst-1", "Анна");
    setVisitAt("2026-08-01T16:00:00+03:00");
    expect(getBookingDraft()).toEqual({
      serviceId: "svc-1",
      serviceName: "Маникюр",
      masterId: "mst-1",
      masterName: "Анна",
      visitAt: "2026-08-01T16:00:00+03:00",
      rescheduleOf: null,
    });
  });

  it("setRescheduleContext replaces any prior draft", () => {
    setService("svc-old", "Старая услуга");
    setVisitAt("2026-07-01T10:00:00+03:00");
    setRescheduleContext("booking-42", "svc-1", "Маникюр", "mst-1", "Анна");
    expect(getBookingDraft()).toEqual({
      serviceId: "svc-1",
      serviceName: "Маникюр",
      masterId: "mst-1",
      masterName: "Анна",
      visitAt: null,
      rescheduleOf: "booking-42",
    });
  });

  it("resetBooking clears everything including reschedule context", () => {
    setRescheduleContext("booking-42", "svc-1", "Маникюр", "mst-1", "Анна");
    resetBooking();
    expect(getBookingDraft().rescheduleOf).toBeNull();
    expect(getBookingDraft().serviceId).toBeNull();
  });

  it("produces a new state object on each update (useSyncExternalStore contract)", () => {
    const before = getBookingDraft();
    setService("svc-1", "Маникюр");
    const after = getBookingDraft();
    expect(after).not.toBe(before);
    expect(before.serviceId).toBeNull();
    expect(after.serviceId).toBe("svc-1");
  });
});
