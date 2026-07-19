/**
 * Unit tests for `booking-status.ts` — the single backend→UI status map
 * used by customer records. Locked vocabulary: 8 customer-visible
 * buckets; unknown backend values must fail safe to «В обработке».
 */
import { describe, expect, it } from "vitest";

import { mapBookingStatus, sectionFor } from "./booking-status";

describe("mapBookingStatus", () => {
  it("maps each of the 8 canonical buckets to its locked rendering", () => {
    const cases: Array<[string, string, string, string]> = [
      // backend, status, label, tint
      ["confirmed", "confirmed", "Подтверждена", "sage"],
      ["rescheduled", "rescheduled", "Перенесена", "muted"],
      ["cancelled", "cancelled_customer", "Отменена", "muted"],
      ["provider_cancelled", "provider_cancelled", "Отменена салоном", "warning"],
      ["completed", "completed", "Прошла", "sage"],
      ["no_show", "no_show", "Не пришла", "muted"],
      ["refund_pending", "refund_pending", "Возврат в обработке", "muted"],
      ["refund_completed", "refund_completed", "Возврат завершён", "sage"],
    ];
    for (const [backend, status, label, tint] of cases) {
      const mapped = mapBookingStatus(backend);
      expect(mapped.status).toBe(status);
      expect(mapped.rendering.label).toBe(label);
      expect(mapped.rendering.tint).toBe(tint);
    }
  });

  it("collapses chargeback / dispute aliases into refund_pending", () => {
    for (const alias of ["chargeback", "chargeback_initiated", "dispute", "payment_dispute"]) {
      expect(mapBookingStatus(alias).status).toBe("refund_pending");
    }
  });

  it("maps cancellation-request flow states onto customer-visible buckets", () => {
    expect(mapBookingStatus("cancel_requested").status).toBe("cancelled_customer");
    expect(mapBookingStatus("reschedule_requested").status).toBe("rescheduled");
    expect(mapBookingStatus("in_progress").status).toBe("confirmed");
  });

  it("normalises case and whitespace before lookup", () => {
    expect(mapBookingStatus("  Confirmed ").status).toBe("confirmed");
  });

  it("fails safe to a generic badge for unknown values", () => {
    const mapped = mapBookingStatus("some_future_status");
    expect(mapped.status).toBe("unknown");
    expect(mapped.rendering.label).toBe("В обработке");
    expect(mapped.rendering.tint).toBe("muted");
  });

  it("fails safe for empty / non-string input", () => {
    for (const bad of ["", "   ", null, undefined]) {
      expect(mapBookingStatus(bad).status).toBe("unknown");
    }
  });
});

describe("sectionFor", () => {
  it("keeps active bookings in the upcoming section", () => {
    expect(sectionFor("confirmed")).toBe("upcoming");
    expect(sectionFor("rescheduled")).toBe("upcoming");
  });

  it("sends terminal and unknown states to history", () => {
    for (const s of [
      "cancelled_customer",
      "provider_cancelled",
      "completed",
      "no_show",
      "refund_pending",
      "refund_completed",
      "unknown",
    ] as const) {
      expect(sectionFor(s)).toBe("history");
    }
  });
});
