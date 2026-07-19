/**
 * Tests for `payment-status.ts` — the C7.3 client payment status read
 * model (frozen contract PILOT_CONTRACTS §7.5). Single place mapping
 * internal capture states to the customer-visible labels; render code
 * must go through `mapPaymentStatus` so a taxonomy change is one edit.
 *
 * Locked UX table (C7.3 / ADR payments-capture-strategy):
 *   authorized        → «Зарезервировано»
 *   capture_scheduled → «Оплата будет подтверждена после визита»
 *   captured          → «Оплата завершена»
 *   released/canceled → «Резерв отменён, деньги разблокированы»
 *   failed            → «Оплата не прошла»
 *   refunded          → «Оплата возвращена»
 * `waiting_for_capture` is NEVER shown to customers (ADR).
 */
import { describe, expect, it } from "vitest";

import { mapPaymentStatus } from "./payment-status";

describe("mapPaymentStatus", () => {
  it("maps every contract state to its locked label", () => {
    const cases: Array<[string, string]> = [
      ["authorized", "Зарезервировано"],
      ["capture_scheduled", "Оплата будет подтверждена после визита"],
      ["captured", "Оплата завершена"],
      ["released", "Резерв отменён, деньги разблокированы"],
      ["canceled", "Резерв отменён, деньги разблокированы"],
      ["failed", "Оплата не прошла"],
      ["refunded", "Оплата возвращена"],
    ];
    for (const [state, label] of cases) {
      const mapped = mapPaymentStatus(state);
      expect(mapped.visible).toBe(true);
      expect(mapped.rendering?.label).toBe(label);
    }
  });

  it("hides waiting_for_capture from customers (ADR)", () => {
    expect(mapPaymentStatus("waiting_for_capture").visible).toBe(false);
  });

  it("fails safe to hidden for unknown / empty values", () => {
    for (const bad of ["", "   ", "some_future_state", null, undefined]) {
      expect(mapPaymentStatus(bad).visible).toBe(false);
    }
  });

  it("marks failed as warning tint, captured/refunded as calm-positive", () => {
    expect(mapPaymentStatus("failed").rendering?.tint).toBe("warning");
    expect(mapPaymentStatus("captured").rendering?.tint).toBe("sage");
    expect(mapPaymentStatus("refunded").rendering?.tint).toBe("sage");
    expect(mapPaymentStatus("authorized").rendering?.tint).toBe("muted");
  });
});
