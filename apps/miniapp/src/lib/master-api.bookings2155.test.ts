/**
 * Клиент ручек М-2 для формы записи мастера (DRF-2155):
 * `searchMasterCustomers` / `getMasterBookingSlots` / `createMasterBooking`.
 *
 * Создание — не через `request` (он бросает на не-2xx): 409 «занято» и
 * 202 «проверяем результат» — исходы, не ошибки; сеть упала — `pending`
 * с тем же ключом, а не «ошибка» (иначе мастер нажмёт ещё раз и запишет
 * клиента дважды).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createMasterBooking,
  getMasterBookingSlots,
  searchMasterCustomers,
} from "./master-api";

const fetchMock = vi.fn();

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("searchMasterCustomers", () => {
  it("GET /customers?q= → results как есть (имя+инициал, дата визита, без телефона)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        results: [
          {
            id: "c-1",
            name: "Анна П.",
            named: true,
            last_visit_date: "2026-05-12",
          },
        ],
      }),
    );
    const rows = await searchMasterCustomers("Анна");
    expect(rows).toEqual([
      {
        id: "c-1",
        name: "Анна П.",
        named: true,
        last_visit_date: "2026-05-12",
      },
    ]);
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toMatch(
      /\/api\/v1\/master\/customers\?q=%D0%90%D0%BD%D0%BD%D0%B0$/,
    );
  });
});

describe("getMasterBookingSlots", () => {
  it("GET /booking-slots?date&service_id — без master_id", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        date: "2026-10-21",
        timezone: "Europe/Moscow",
        service_id: "s-1",
        duration_min: 60,
        slots: [],
      }),
    );
    const res = await getMasterBookingSlots({
      serviceId: "s-1",
      date: "2026-10-21",
    });
    expect(res.timezone).toBe("Europe/Moscow");
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("/api/v1/master/booking-slots?");
    expect(url).toContain("date=2026-10-21");
    expect(url).toContain("service_id=s-1");
    expect(url).not.toContain("master_id");
  });
});

describe("createMasterBooking — исход §18, не исключение", () => {
  const body = {
    service_id: "s-1",
    start_at: "2026-10-21T15:00:00+03:00",
    idempotency_key: "k-1",
    client_id: "c-1",
  };

  it("201 → committed с appointment_id; POST /bookings без master_id", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(201, {
        outcome: "committed",
        detail: "appointment created",
        appointment_id: "a-1",
      }),
    );
    const res = await createMasterBooking(body);
    expect(res).toEqual({
      outcome: "committed",
      detail: "appointment created",
      appointment_id: "a-1",
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/master\/bookings$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).not.toHaveProperty("master_id");
  });

  it("409 slot_taken → conflict с alternatives", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, {
        outcome: "conflict",
        detail: "занято",
        reason_code: "slot_taken",
        alternatives: [
          {
            time: "16:00",
            start_at: "2026-10-21T16:00:00+03:00",
            duration_min: 60,
          },
        ],
        alternatives_unavailable: false,
      }),
    );
    const res = await createMasterBooking(body);
    expect(res.outcome).toBe("conflict");
    expect(res.reason_code).toBe("slot_taken");
    expect(res.alternatives).toHaveLength(1);
  });

  it("202 result_pending → pending с ключом", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(202, {
        outcome: "pending",
        reason_code: "result_pending",
        detail: "…",
        idempotency_key: "k-1",
      }),
    );
    const res = await createMasterBooking(body);
    expect(res.outcome).toBe("pending");
    expect(res.idempotency_key).toBe("k-1");
  });

  it("сеть не ответила → pending с тем же ключом, не failed", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const res = await createMasterBooking(body);
    expect(res.outcome).toBe("pending");
    expect(res.idempotency_key).toBe("k-1");
  });

  it("отказ без outcome ({error, detail}) → failed с detail", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(404, { error: "not_found", detail: "service not found" }),
    );
    const res = await createMasterBooking(body);
    expect(res).toEqual({ outcome: "failed", detail: "service not found" });
  });
});
