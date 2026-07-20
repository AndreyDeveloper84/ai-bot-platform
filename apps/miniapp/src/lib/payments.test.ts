/**
 * Tests for the C7 live passthrough clients — `lib/payments.ts`
 * (payment create) and `lib/cards.ts` (saved cards), pinned to the
 * verbatim bot passthrough shapes (apps/miniapp_api/views.py):
 *
 *   POST   /api/v1/customer/me/payments/      {appointment_id}
 *        → {payment_id, confirmation_url, amount, capture_state, currency}
 *   GET    /api/v1/customer/me/cards/         → {cards: [{id, last4, brand}]}
 *   POST   /api/v1/customer/me/cards/setup/   {consent_version, consented_at}
 *        → {confirmation_url}
 *   DELETE /api/v1/customer/me/cards/{id}/    → 204 (idempotent)
 *
 * HTTP mocked at global fetch; auth header asserted per session initData.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";

vi.mock("./max-sdk", () => ({
  getInitData: () => "test-init-data",
}));

import {
  CLIENT_CARDS_CONSENT_VERSION,
  deleteCard,
  getSavedCards,
  setupCard,
} from "./cards";
import { createPayment } from "./payments";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("createPayment (C7.1 passthrough)", () => {
  it("POSTs the appointment id and returns the verbatim payment data", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        payment_id: "pay-1",
        confirmation_url: "https://yoomoney.ru/checkout/pay/abc",
        amount: "2000.00",
        capture_state: "authorized",
        currency: "RUB",
      }),
    );
    const payment = await createPayment("appt-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/customer/me/payments/");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("Authorization")).toBe(
      "MaxInitData test-init-data",
    );
    expect(JSON.parse(String(init.body))).toEqual({ appointment_id: "appt-1" });
    expect(payment).toEqual({
      payment_id: "pay-1",
      confirmation_url: "https://yoomoney.ru/checkout/pay/abc",
      amount: "2000.00",
      capture_state: "authorized",
      currency: "RUB",
    });
  });

  it("tolerates a null confirmation_url (saved-method charge, C7.1)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        payment_id: "pay-2",
        confirmation_url: null,
        amount: "2000.00",
        capture_state: "authorized",
        currency: "RUB",
      }),
    );
    const payment = await createPayment("appt-1");
    expect(payment.confirmation_url).toBeNull();
  });

  it("throws ApiError with the backend slug on C1-neutral 409", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          error: "unavailable",
          detail: "Запись к этому специалисту сейчас недоступна",
        },
        409,
      ),
    );
    const err = await createPayment("appt-1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).slug).toBe("unavailable");
  });
});

describe("cards live (C7.2 passthrough)", () => {
  it("getSavedCards maps the {cards: [...]} envelope", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ cards: [{ id: "c-1", last4: "4242", brand: "visa" }] }),
    );
    const cards = await getSavedCards();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/customer/me/cards/");
    expect((init.method ?? "GET").toUpperCase()).toBe("GET");
    expect(cards).toEqual([{ id: "c-1", last4: "4242", brand: "visa" }]);
  });

  it("setupCard sends the consent version + timestamp (C7.2 consent boundary)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ confirmation_url: "https://yoomoney.ru/checkout/bind/xyz" }),
    );
    const res = await setupCard();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/customer/me/cards/setup/");
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body.consent_version).toBe(CLIENT_CARDS_CONSENT_VERSION);
    expect(typeof body.consented_at).toBe("string");
    expect(res.confirmation_url).toBe("https://yoomoney.ru/checkout/bind/xyz");
  });

  it("deleteCard issues DELETE and resolves on 204", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(deleteCard("c-1")).resolves.toBeUndefined();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/customer/me/cards/c-1/");
    expect(init.method).toBe("DELETE");
  });

  it("deleteCard surfaces upstream errors for the honest retry path", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: "upstream_unavailable", detail: "try later" }, 502),
    );
    const err = await deleteCard("c-1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
  });
});
