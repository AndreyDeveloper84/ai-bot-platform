/**
 * Tests for the water write path of `customer-wellness.ts` (DRF-1402).
 *
 *   POST   /api/v1/customer/wellness/water              → log a glass
 *   DELETE /api/v1/customer/wellness/water/{entry_id}    → undo it
 *
 * The offline queue (Tau §11.8) is the durable buffer in front of that
 * POST. Its ONE invariant: an entry leaves the queue only after Ayla
 * accepted it. Every «did not clear» assertion below is paired with a
 * «did clear on success» assertion on the same data through the same
 * call, so neither can pass on a fixture where flush never runs.
 *
 * No calendar constants anywhere — water is day-bound and a pinned date
 * is a timebomb. Timestamps are always derived from `Date.now()`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./max-sdk", () => ({
  getInitData: () => "test-init-data",
}));

import {
  enqueueWaterLog,
  flushWaterQueue,
  getRecentActivity,
  getWellnessToday,
  readWaterQueue,
  undoWaterLog,
} from "./customer-wellness";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function okEntry(entryId = "entry-1"): Response {
  return jsonResponse({
    entry_id: entryId,
    ml: 250,
    water_ml: 250,
    water_glasses_eaten: 5,
    water_glasses_target: 8,
  });
}

/** The nth recorded fetch call, as [url, init]. Throws if it never happened. */
function callAt(n: number): [string, RequestInit] {
  const call = fetchMock.mock.calls[n];
  if (!call) throw new Error(`fetch was not called ${n + 1} time(s)`);
  return call as [string, RequestInit];
}

function bodyOf(n: number): Record<string, unknown> {
  const [, init] = callAt(n);
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  window.localStorage.clear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("flushWaterQueue — the queue reaches Ayla", () => {
  it("POSTs every queued glass to the water endpoint", async () => {
    enqueueWaterLog(250);
    enqueueWaterLog(500);
    // Fresh Response per call — a Response body can only be read once.
    fetchMock.mockImplementation(async () => okEntry());

    const synced = await flushWaterQueue();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [url, init] = callAt(0);
    expect(url).toBe("/api/v1/customer/wellness/water");
    expect((init.method ?? "GET").toUpperCase()).toBe("POST");
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("MaxInitData test-init-data");
    expect(bodyOf(0).ml).toBe(250);
    expect(bodyOf(1).ml).toBe(500);
    expect(synced).toBe(2);
  });

  it("sends the tap time, not the flush time, so a late flush lands on the right day", async () => {
    const before = Date.now();
    enqueueWaterLog(250);
    const after = Date.now();
    fetchMock.mockResolvedValue(okEntry());

    await flushWaterQueue();

    const sentMs = Date.parse(String(bodyOf(0).ts));
    expect(Number.isNaN(sentMs)).toBe(false);
    expect(sentMs).toBeGreaterThanOrEqual(before - 1000);
    expect(sentMs).toBeLessThanOrEqual(after + 1000);
  });

  // ── the paired invariant: cleared on success, kept on failure ────────
  it("clears the queue when Ayla accepted the write", async () => {
    enqueueWaterLog(250);
    fetchMock.mockResolvedValue(okEntry());

    const synced = await flushWaterQueue();

    expect(synced).toBe(1);
    expect(readWaterQueue()).toHaveLength(0);
  });

  it("keeps the queue intact when the write failed — same data, same call", async () => {
    enqueueWaterLog(250);
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const synced = await flushWaterQueue();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(synced).toBe(0);
    expect(readWaterQueue().map((e) => e.volume_ml)).toEqual([250]);
  });

  it("keeps the queue on a 5xx — an Ayla outage is retryable", async () => {
    enqueueWaterLog(250);
    fetchMock.mockResolvedValue(
      jsonResponse({ error: "ayla_unavailable", detail: "down" }, 502),
    );

    expect(await flushWaterQueue()).toBe(0);
    expect(readWaterQueue()).toHaveLength(1);
  });

  it("on partial success drops only what went through", async () => {
    enqueueWaterLog(250);
    enqueueWaterLog(500);
    enqueueWaterLog(750);
    fetchMock
      .mockResolvedValueOnce(okEntry("entry-1"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const synced = await flushWaterQueue();

    expect(synced).toBe(1);
    const left = readWaterQueue();
    expect(left.map((e) => e.volume_ml)).toEqual([500, 750]);
  });

  it("re-flushing after a failure retries the survivors with the SAME idempotency key", async () => {
    enqueueWaterLog(250);
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await flushWaterQueue();
    const firstKey = bodyOf(0).idempotency_key;

    fetchMock.mockResolvedValueOnce(okEntry());
    expect(await flushWaterQueue()).toBe(1);

    const secondKey = bodyOf(1).idempotency_key;
    expect(typeof firstKey).toBe("string");
    expect(secondKey).toBe(firstKey);
    expect(readWaterQueue()).toHaveLength(0);
  });

  it("drops an entry the server permanently rejects so it cannot poison the queue", async () => {
    enqueueWaterLog(250);
    enqueueWaterLog(500);
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ error: "malformed", detail: "bad" }, 400))
      .mockResolvedValueOnce(okEntry());

    const synced = await flushWaterQueue();

    expect(synced).toBe(1);
    expect(readWaterQueue()).toHaveLength(0);
  });

  it("does nothing and posts nothing on an empty queue", async () => {
    expect(await flushWaterQueue()).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not double-post when two flushes overlap", async () => {
    enqueueWaterLog(250);
    fetchMock.mockResolvedValue(okEntry());

    // The second caller joins the in-flight flush instead of starting a
    // competing one — both see the same result, the glass is sent once.
    const [a, b] = await Promise.all([flushWaterQueue(), flushWaterQueue()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(a).toBe(1);
    expect(b).toBe(1);
    expect(readWaterQueue()).toHaveLength(0);
  });
});

describe("undoWaterLog", () => {
  it("DELETEs the entry and reports success", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await expect(undoWaterLog("entry-42")).resolves.toBe(true);

    const [url, init] = callAt(0);
    expect(url).toBe("/api/v1/customer/wellness/water/entry-42");
    expect((init.method ?? "GET").toUpperCase()).toBe("DELETE");
  });

  it("reports false when the undo window has closed (404)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: "not_undoable", detail: "window closed" }, 404),
    );

    await expect(undoWaterLog("entry-42")).resolves.toBe(false);
  });

  it("propagates a real outage instead of pretending the glass is gone", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: "ayla_unavailable", detail: "down" }, 502),
    );

    await expect(undoWaterLog("entry-42")).rejects.toThrow();
  });

  it("percent-encodes the entry id", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await undoWaterLog("a/b c");
    expect(callAt(0)[0]).toBe("/api/v1/customer/wellness/water/a%2Fb%20c");
  });
});

/**
 * These three replace the old `guardProd` tests. Their intent is
 * unchanged — production must never invent a person's day — but the
 * mechanism is: the reads now reach the backend, so «did not invent»
 * is proven by «asked the server», not by «threw».
 *
 * The stub payloads below are deliberately unlike the hardcoded ones
 * (`TODAY_STUB` says 1240 kcal): a test that asserted only «truthy»
 * would pass on invented data too.
 */
describe("the reads reach the backend instead of inventing a day", () => {
  const LIVE_TODAY = {
    calories_eaten: 777,
    calories_target: 1900,
    water_glasses_eaten: 3,
    water_glasses_target: 8,
    active_goals: [],
    display_name: "",
  };
  const LIVE_ACTIVITY = {
    this_week_booking_count: 2,
    weekly_progress: { water_days_logged: 1, food_days_logged: 1, active_days_count: 1 },
  };

  function okJson(body: unknown) {
    return { ok: true, status: 200, json: async () => body } as unknown as Response;
  }

  it("getWellnessToday asks the endpoint and returns what it answered", async () => {
    vi.stubEnv("DEV", false);
    fetchMock.mockResolvedValue(okJson(LIVE_TODAY));

    const today = await getWellnessToday();

    const [url] = callAt(0);
    expect(String(url)).toContain("/wellness/today");
    // Positive guard: the value came from the server, not the stub.
    expect(today.calories_eaten).toBe(777);
    expect(today.calories_eaten).not.toBe(1240);
  });

  it("getRecentActivity asks the endpoint and returns what it answered", async () => {
    vi.stubEnv("DEV", false);
    fetchMock.mockResolvedValue(okJson(LIVE_ACTIVITY));

    const activity = await getRecentActivity();

    const [url] = callAt(0);
    expect(String(url)).toContain("/recent-activity");
    expect(activity.this_week_booking_count).toBe(2);
    // The stub always carries a next_booking; a live empty week must not.
    expect(activity.next_booking).toBeUndefined();
  });

  it("a failing backend surfaces the failure — it never falls back to a stub", async () => {
    vi.stubEnv("DEV", false);
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(getWellnessToday()).rejects.toThrow();
    await expect(getRecentActivity()).rejects.toThrow();
  });
});
