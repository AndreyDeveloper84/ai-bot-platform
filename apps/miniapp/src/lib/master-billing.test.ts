/**
 * Tests for `master-billing.ts` — the C2 (billing status) + C3 (payout
 * preview) clients of the master Mini App (frozen contract
 * PILOT_CONTRACTS §3/§4 via the master_api proxies; proxies return the
 * contract `data` envelope verbatim).
 *
 *   GET /api/v1/master/billing/status   → {data: C2}
 *   GET /api/v1/master/payout-preview   → {data: C3}
 *
 * Money fields are Decimal strings with exactly two places (§1).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";

vi.mock("./max-sdk", () => ({
  getInitData: () => "test-init-data",
}));

import { getBillingStatus, getPayoutPreview } from "./master-billing";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const C2_ACTIVE = {
  data: {
    specialist_id: "spec-1",
    subscription: {
      status: "active",
      tariff: "solo",
      current_period_end: "2026-08-31",
      next_charge: {
        subscription_amount: "690.00",
        fees_amount: "270.00",
        total_amount: "960.00",
        date: "2026-09-01",
      },
    },
    fees: { pending_total: "270.00", pending_count: 3 },
    last_invoice: {
      id: "inv-1",
      amount: "960.00",
      status: "paid",
      paid_at: "2026-07-01T10:00:00Z",
    },
  },
};

const C3_WITH_ITEMS = {
  data: {
    pending_amount: "5730.00",
    currency: "RUB",
    expected_settlement_hint: "~следующий рабочий день по расписанию ЮKassa",
    items: [
      {
        appointment_id: "a-1",
        completed_at: "2026-07-18T16:00:00Z",
        amount: "2000.00",
        platform_fee: "90.00",
        specialist_income: "1910.00",
        capture_state: "scheduled",
      },
      {
        appointment_id: "a-2",
        completed_at: "2026-07-17T12:00:00Z",
        amount: "3000.00",
        platform_fee: "90.00",
        specialist_income: "2910.00",
        capture_state: "captured_pending_settlement",
      },
    ],
  },
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("getBillingStatus (C2)", () => {
  it("unwraps the data envelope and returns the C2 payload", async () => {
    fetchMock.mockResolvedValue(jsonResponse(C2_ACTIVE));
    const status = await getBillingStatus();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/master/billing/status");
    expect((init.method ?? "GET").toUpperCase()).toBe("GET");
    expect(new Headers(init.headers).get("Authorization")).toBe(
      "MaxInitData test-init-data",
    );
    expect(status).toEqual(C2_ACTIVE.data);
  });

  it("none-subscription shape: null fields, zero fees (C2)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        data: {
          specialist_id: "spec-1",
          subscription: {
            status: "none",
            tariff: null,
            current_period_end: null,
            next_charge: null,
          },
          fees: { pending_total: "0.00", pending_count: 0 },
          last_invoice: null,
        },
      }),
    );
    const status = await getBillingStatus();
    expect(status.subscription.status).toBe("none");
    expect(status.subscription.next_charge).toBeNull();
    expect(status.last_invoice).toBeNull();
  });

  it("canceled shape: next_charge is null (AMD-013)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        data: {
          ...C2_ACTIVE.data,
          subscription: {
            ...C2_ACTIVE.data.subscription,
            status: "canceled",
            next_charge: null,
          },
        },
      }),
    );
    const status = await getBillingStatus();
    expect(status.subscription.status).toBe("canceled");
    expect(status.subscription.next_charge).toBeNull();
  });

  it("surfaces the proxy error slugs (503 specialist_mapping_unavailable)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          error: "specialist_mapping_unavailable",
          detail: "specialist id mapping is not synced yet",
        },
        503,
      ),
    );
    const err = await getBillingStatus().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(503);
    expect((err as ApiError).slug).toBe("specialist_mapping_unavailable");
  });
});

describe("getPayoutPreview (C3)", () => {
  it("unwraps the data envelope with items and hint", async () => {
    fetchMock.mockResolvedValue(jsonResponse(C3_WITH_ITEMS));
    const preview = await getPayoutPreview();
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/api/v1/master/payout-preview");
    expect(preview).toEqual(C3_WITH_ITEMS.data);
  });

  it("empty shape: 0.00 and no items (C3)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        data: {
          pending_amount: "0.00",
          currency: "RUB",
          expected_settlement_hint: null,
          items: [],
        },
      }),
    );
    const preview = await getPayoutPreview();
    expect(preview.pending_amount).toBe("0.00");
    expect(preview.items).toEqual([]);
  });
});
