/**
 * Tests for `customer-booking.ts` — the customer booking-flow client.
 *
 * After pilot phase 3(1) the catalog reads are REAL: the 3-layer Tau
 * stub (layer_1/2/3 with reasoning_text) is gone — no backend ever
 * produced it. The lib now composes the bot-mirror endpoints
 * (`GET /services`, `GET /masters`) with the Ayla scorer proxy
 * (`POST /recommendations` → service_id+score). HTTP layer mocked here;
 * fixtures use the verbatim contract shapes from
 * `apps/miniapp_api/views.py::_service_to_dict/_master_to_dict`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    fetchMaster: vi.fn(),
    fetchSlots: vi.fn(),
    createBooking: vi.fn(),
    fetchServices: vi.fn(),
    fetchMasters: vi.fn(),
    fetchRecommendations: vi.fn(),
  };
});

import {
  createBooking,
  fetchMasters,
  fetchRecommendations,
  fetchServices,
  fetchSlots,
  type Master,
  type Service,
} from "./api";
import {
  createCustomerBooking,
  getCatalogBrowse,
  getCustomerSlots,
} from "./customer-booking";

const mockedFetchSlots = vi.mocked(fetchSlots);
const mockedCreateBooking = vi.mocked(createBooking);
const mockedFetchServices = vi.mocked(fetchServices);
const mockedFetchMasters = vi.mocked(fetchMasters);
const mockedFetchRecommendations = vi.mocked(fetchRecommendations);

function service(partial: Partial<Service> & Pick<Service, "id" | "name">): Service {
  return {
    slug: partial.id,
    short_description: "",
    description: "",
    price_from: "1800.00",
    duration_min: 60,
    is_popular: false,
    contraindications: "",
    ...partial,
  };
}

const MASTER: Master = {
  id: "mst-1",
  name: "Анна Соколова",
  specialization: "nail-мастер",
  bio: "",
  experience: "5 лет",
  rating: "4.9",
  photo_url: "",
};

beforeEach(() => {
  vi.clearAllMocks();
});

// --- booking flow (slots window / create passthrough) ----------------------

describe("getCustomerSlots", () => {
  it("requests a 14-day window by default with YYYY-MM-DD dates", async () => {
    mockedFetchSlots.mockResolvedValue({ slots: [] });
    await getCustomerSlots({ masterId: "mst-1", serviceId: "svc-1" });
    expect(mockedFetchSlots).toHaveBeenCalledTimes(1);
    const arg = mockedFetchSlots.mock.calls[0]![0];
    expect(arg.masterId).toBe("mst-1");
    expect(arg.serviceId).toBe("svc-1");
    expect(arg.dateFrom).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(arg.dateTo).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const from = new Date(`${arg.dateFrom}T00:00:00`);
    const to = new Date(`${arg.dateTo}T00:00:00`);
    expect(Math.round((to.getTime() - from.getTime()) / 86_400_000)).toBe(14);
  });

  it("honours an explicit days override", async () => {
    mockedFetchSlots.mockResolvedValue({ slots: [] });
    await getCustomerSlots({ masterId: "mst-1", serviceId: "svc-1", days: 7 });
    const arg = mockedFetchSlots.mock.calls[0]![0];
    const from = new Date(`${arg.dateFrom}T00:00:00`);
    const to = new Date(`${arg.dateTo}T00:00:00`);
    expect(Math.round((to.getTime() - from.getTime()) / 86_400_000)).toBe(7);
  });
});

describe("createCustomerBooking", () => {
  it("passes the payload through to the API layer verbatim", async () => {
    const created = {
      booking: {
        id: "b-1",
        service_name: "Маникюр",
        master_name: "Анна",
        visit_at: "2026-08-01T16:00:00+03:00",
        duration_min: 60,
        status: "confirmed",
      },
    };
    mockedCreateBooking.mockResolvedValue(created);
    const payload = {
      service_id: "svc-1",
      master_id: "mst-1",
      visit_at: "2026-08-01T16:00:00+03:00",
    };
    const result = await createCustomerBooking(payload);
    expect(mockedCreateBooking).toHaveBeenCalledWith(payload);
    expect(result).toBe(created);
  });

  it("propagates API errors to the caller (screen renders the error state)", async () => {
    mockedCreateBooking.mockRejectedValue(new Error("[409] unavailable: slot taken"));
    await expect(
      createCustomerBooking({
        service_id: "svc-1",
        master_id: "mst-1",
        visit_at: "2026-08-01T16:00:00+03:00",
      }),
    ).rejects.toThrow("[409]");
  });
});

// --- catalog browse (real mirror + Ayla scorer) ----------------------------

describe("getCatalogBrowse", () => {
  it("composes mirror services/masters with Ayla-ranked pick ids", async () => {
    mockedFetchServices.mockResolvedValue({
      services: [
        service({ id: "svc-1", name: "Маникюр" }),
        service({ id: "svc-2", name: "Педикюр" }),
        service({ id: "svc-3", name: "Массаж" }),
      ],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
    mockedFetchRecommendations.mockResolvedValue({
      recommendations: [
        { service_id: "svc-ghost", score: 0.99 },
        { service_id: "svc-2", score: 0.9 },
        { service_id: "svc-1", score: 0.8 },
      ],
    });
    const data = await getCatalogBrowse();
    expect(data.services.map((s) => s.name)).toEqual([
      "Маникюр",
      "Педикюр",
      "Массаж",
    ]);
    expect(data.masters).toEqual([MASTER]);
    // Score-desc order; ids missing from the mirror are dropped.
    expect(data.pickServiceIds).toEqual(["svc-2", "svc-1"]);
  });

  it("returns empty picks when the Ayla scorer is unavailable", async () => {
    mockedFetchServices.mockResolvedValue({
      services: [service({ id: "svc-1", name: "Маникюр" })],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
    mockedFetchRecommendations.mockRejectedValue(new Error("[502] ayla_unavailable"));
    const data = await getCatalogBrowse();
    expect(data.services).toHaveLength(1);
    expect(data.masters).toHaveLength(1);
    expect(data.pickServiceIds).toEqual([]);
  });

  it("rejects when the mirror itself fails (screen shows the error state)", async () => {
    mockedFetchServices.mockRejectedValue(new Error("[500] http_error"));
    mockedFetchMasters.mockResolvedValue({ masters: [] });
    await expect(getCatalogBrowse()).rejects.toThrow("[500]");
  });
});
