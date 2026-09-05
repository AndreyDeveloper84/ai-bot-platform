/**
 * Tests for the `pending_booking_intent` snapshot (DRF-1484 / §24.5).
 *
 * The decision under test:
 *   - `entry_point` IS part of the snapshot — provenance of the flow
 *     that produced the intent (catalog / master / deep link / direct);
 *   - `tenant_id` is NOT — tenant belongs to the execution/request
 *     context and is server-resolved, so the durable intent never
 *     carries it.
 *
 * The negative assertion («no tenant_id») is guarded by a positive
 * presence assertion on the same restored object (CI guard
 * `negative_assert_guard`, DRF-1411): a test that restored nothing
 * must fail loudly instead of passing vacuously.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  _PENDING_INTENT_STORAGE_KEY,
  MAX_ENTRY_POINT_LEN,
  peekPendingIntent,
  resolveEntryPoint,
  restorePendingIntent,
  savePendingIntent,
  type PendingBookingIntent,
} from "./pending-booking-intent";

const BASE: PendingBookingIntent = {
  master_id: "mst-1",
  service_id: "svc-1",
  slot_iso: "2026-09-10T14:00:00+03:00",
};

beforeEach(() => {
  window.sessionStorage.clear();
});

describe("save/restore round-trip", () => {
  it("preserves entry_point alongside the identifying triplet", () => {
    savePendingIntent({ ...BASE, entry_point: "catalog" });
    const restored = restorePendingIntent();
    expect(restored).toEqual({ ...BASE, entry_point: "catalog" });
  });

  it("accepts an intent without entry_point (optional provenance)", () => {
    savePendingIntent(BASE);
    const restored = restorePendingIntent();
    expect(restored).toEqual(BASE);
    expect(restored?.entry_point).toBeUndefined();
  });

  it("rejects a corrupt (non-string) entry_point", () => {
    window.sessionStorage.setItem(
      _PENDING_INTENT_STORAGE_KEY,
      JSON.stringify({ ...BASE, entry_point: 42 }),
    );
    expect(peekPendingIntent()).toBeNull();
    expect(restorePendingIntent()).toBeNull();
  });
});

describe("tenant_id stays out of the snapshot (§24.5)", () => {
  it("restored intent carries provenance but no tenant_id", () => {
    savePendingIntent({ ...BASE, entry_point: "master" });
    const restored = restorePendingIntent();
    // Positive guard on the same data: the restore actually happened
    // and the provenance round-tripped. Without this, «no tenant_id»
    // would also pass on a null restore.
    expect(restored).not.toBeNull();
    expect(restored?.entry_point).toBe("master");
    expect(restored?.master_id).toBe("mst-1");
    // The negative assertion the guard exists for:
    expect(restored).not.toHaveProperty("tenant_id");
  });

  it("the interface contract lists provenance, not tenant", () => {
    // Compile-time shape check, exercised at runtime: the fields the
    // screen actually snapshots.
    const intent: PendingBookingIntent = {
      ...BASE,
      entry_point: resolveEntryPoint(null, ""),
    };
    expect(intent.entry_point).toBe("direct");
    expect(Object.keys(intent).sort()).toEqual(
      ["entry_point", "master_id", "service_id", "slot_iso"].sort(),
    );
  });
});

describe("resolveEntryPoint", () => {
  it("prefers the explicit upstream value", () => {
    expect(resolveEntryPoint("master", "open_catalog")).toBe("master");
    expect(resolveEntryPoint("catalog", "")).toBe("catalog");
  });

  it("composes deep_link provenance from the start payload", () => {
    expect(resolveEntryPoint(null, "open_catalog")).toBe(
      "deep_link:open_catalog",
    );
  });

  it("truncates an over-long payload to the bound", () => {
    const payload = "x".repeat(512);
    const resolved = resolveEntryPoint(null, payload);
    expect(resolved).toHaveLength(MAX_ENTRY_POINT_LEN);
    expect(resolved.startsWith("deep_link:")).toBe(true);
  });

  it("falls back to direct when nothing is known", () => {
    expect(resolveEntryPoint(null, "")).toBe("direct");
  });
});
