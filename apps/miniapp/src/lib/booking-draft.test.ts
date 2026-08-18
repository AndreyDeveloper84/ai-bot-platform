/**
 * Manual booking draft rules — UX contract §12–18.
 *
 * The weight is on invalidation. A draft that keeps «15:00» after the
 * service changed from 30 to 90 minutes is offering a start nobody
 * validated for the new duration, and it will look completely normal
 * right up until the salon double-books.
 */
import { describe, expect, it } from "vitest";

import {
  applyDraftAction,
  canQueryAvailability,
  canReview,
  EMPTY_DRAFT,
  missingSteps,
  outcomeKeepsDraft,
  SUBMIT_OUTCOME_COPY,
  type BookingDraft,
  type DraftService,
} from "./booking-draft";

const MANICURE: DraftService = {
  id: "s-1",
  name: "Маникюр",
  duration_min: 60,
};
const COLORING: DraftService = {
  id: "s-2",
  name: "Окрашивание",
  duration_min: 180,
};
const ANNA = { id: "m-1", name: "Анна" };
const INNA = { id: "m-2", name: "Инна" };
const SLOT = { start_at: "2026-08-21T12:00:00Z", end_at: "2026-08-21T13:00:00Z" };
const WINDOW = { start_at: "2026-08-21T12:00:00Z", end_at: "2026-08-21T15:00:00Z" };

function draftWithSlot(): BookingDraft {
  return {
    customer: { kind: "existing", id: "c-1", name: "Мария", phone_masked: "+• ••• ••67" },
    service: MANICURE,
    master: ANNA,
    window: WINDOW,
    slot: SLOT,
  };
}

describe("draft invalidation (§16)", () => {
  it("drops the chosen start when the service changes", () => {
    const { draft, slotInvalidatedBy } = applyDraftAction(draftWithSlot(), {
      type: "service/set",
      service: COLORING,
    });
    expect(draft.slot).toBeNull();
    expect(slotInvalidatedBy).toBe("service_changed");
  });

  it("drops the chosen start when the master changes", () => {
    const { draft, slotInvalidatedBy } = applyDraftAction(draftWithSlot(), {
      type: "master/set",
      master: INNA,
    });
    expect(draft.slot).toBeNull();
    expect(slotInvalidatedBy).toBe("master_changed");
  });

  it("drops the chosen start when the window changes", () => {
    const { draft, slotInvalidatedBy } = applyDraftAction(draftWithSlot(), {
      type: "window/set",
      window: { start_at: "2026-08-22T09:00:00Z", end_at: "2026-08-22T12:00:00Z" },
    });
    expect(draft.slot).toBeNull();
    expect(slotInvalidatedBy).toBe("window_changed");
  });

  it("reports the reason so the UI can say it out loud (§12)", () => {
    // Rule 3 is «never silently». A cleared start with no explanation is
    // the same broken promise as a shifted one.
    const { slotInvalidatedBy } = applyDraftAction(draftWithSlot(), {
      type: "service/set",
      service: COLORING,
    });
    expect(slotInvalidatedBy).toBeDefined();
  });

  it("re-selecting the same service is not a change", () => {
    const { draft, slotInvalidatedBy } = applyDraftAction(draftWithSlot(), {
      type: "service/set",
      service: { ...MANICURE },
    });
    expect(draft.slot).toEqual(SLOT);
    expect(slotInvalidatedBy).toBeUndefined();
  });

  it("re-selecting the same master is not a change", () => {
    const { draft, slotInvalidatedBy } = applyDraftAction(draftWithSlot(), {
      type: "master/set",
      master: { ...ANNA },
    });
    expect(draft.slot).toEqual(SLOT);
    expect(slotInvalidatedBy).toBeUndefined();
  });

  it("treats a same-id service with a new duration as a change", () => {
    // A catalog edit mid-draft. The id matches but the slot was sized for
    // the old duration, so it is no longer a start anyone validated.
    const { draft, slotInvalidatedBy } = applyDraftAction(draftWithSlot(), {
      type: "service/set",
      service: { ...MANICURE, duration_min: 90 },
    });
    expect(draft.slot).toBeNull();
    expect(slotInvalidatedBy).toBe("service_changed");
  });

  it("reports nothing when there was no start to lose", () => {
    const base: BookingDraft = { ...EMPTY_DRAFT, master: ANNA };
    const { slotInvalidatedBy } = applyDraftAction(base, {
      type: "service/set",
      service: COLORING,
    });
    expect(slotInvalidatedBy).toBeUndefined();
  });

  it("keeps the start when only the customer changes", () => {
    // §16 lists date, service and assignment. Going back to fix a
    // customer must not cost the user their time slot.
    const { draft, slotInvalidatedBy } = applyDraftAction(draftWithSlot(), {
      type: "customer/set",
      customer: { kind: "new", name: "Ольга", phone: "+79990000000" },
    });
    expect(draft.slot).toEqual(SLOT);
    expect(slotInvalidatedBy).toBeUndefined();
  });
});

describe("flow order (§12, §17)", () => {
  it("will not query availability without a service", () => {
    expect(canQueryAvailability({ ...EMPTY_DRAFT, master: ANNA })).toBe(false);
  });

  it("will not query availability without an assignment", () => {
    expect(canQueryAvailability({ ...EMPTY_DRAFT, service: MANICURE })).toBe(false);
  });

  it("queries availability once service and master are known", () => {
    expect(
      canQueryAvailability({ ...EMPTY_DRAFT, service: MANICURE, master: ANNA }),
    ).toBe(true);
  });

  it("does not require a customer to ask for intervals", () => {
    // The business order is logical, not a locked stepper (§12): the
    // screen is a progressive draft, and a receptionist holding a phone
    // often knows the time before the name.
    expect(
      canQueryAvailability({ ...EMPTY_DRAFT, service: MANICURE, master: ANNA }),
    ).toBe(true);
  });
});

describe("review readiness (§18)", () => {
  it("is ready when customer, service, master and start are set", () => {
    expect(canReview(draftWithSlot())).toBe(true);
  });

  it("is not ready without a start", () => {
    expect(canReview({ ...draftWithSlot(), slot: null })).toBe(false);
  });

  it("is not ready without a customer", () => {
    expect(canReview({ ...draftWithSlot(), customer: null })).toBe(false);
  });

  it("names what is missing in business order, not field order", () => {
    expect(missingSteps(EMPTY_DRAFT)).toEqual([
      "клиента",
      "услугу",
      "мастера",
      "время",
    ]);
  });
});

describe("submit outcomes (§18)", () => {
  it("keeps the entered data for every non-committed outcome", () => {
    for (const outcome of ["conflict", "blocked", "pending", "failed"] as const) {
      expect(outcomeKeepsDraft(outcome)).toBe(true);
    }
    expect(outcomeKeepsDraft("committed")).toBe(false);
  });

  it("never claims creation on an unknown result", () => {
    // The single most important line in §18: pending is not success.
    expect(SUBMIT_OUTCOME_COPY.pending).not.toMatch(/создана\./);
    expect(SUBMIT_OUTCOME_COPY.pending).toMatch(/могла быть создана/);
  });

  it("has copy for every outcome", () => {
    for (const outcome of [
      "committed",
      "conflict",
      "blocked",
      "pending",
      "failed",
    ] as const) {
      expect(SUBMIT_OUTCOME_COPY[outcome]).toBeTruthy();
    }
  });
});

describe("window semantics (§12)", () => {
  it("keeps the window separate from the chosen start", () => {
    // «Выбранное окно» is the range the draft started from, not the
    // appointment's duration — a three-hour window with a one-hour
    // service must not imply a three-hour booking.
    const d = draftWithSlot();
    expect(d.window).toEqual(WINDOW);
    expect(d.slot).toEqual(SLOT);
    expect(d.window?.end_at).not.toEqual(d.slot?.end_at);
  });

  it("resets everything on reset", () => {
    const { draft } = applyDraftAction(draftWithSlot(), { type: "reset" });
    expect(draft).toEqual(EMPTY_DRAFT);
  });
});
