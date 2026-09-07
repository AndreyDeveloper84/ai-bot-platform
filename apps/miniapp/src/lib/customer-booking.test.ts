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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  recommendationsContractViolation,
  ApiError,
  type Master,
  type RecommendationScore,
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
    is_bookable: true,
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

/**
 * DRF-1556 — the loud channel is `console.error` (the only one
 * `apps/miniapp/src` has). Spied on in every test so «is it silent?» is
 * an assertion rather than an assumption, and so a real mismatch never
 * prints into the test log.
 */
function silenceConsoleError() {
  return vi.spyOn(console, "error").mockImplementation(() => {});
}
let consoleErrorSpy: ReturnType<typeof silenceConsoleError>;

beforeEach(() => {
  vi.clearAllMocks();
  consoleErrorSpy = silenceConsoleError();
});

afterEach(() => {
  consoleErrorSpy.mockRestore();
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
        { service_id: "svc-ghost", score: 0.99, reasons: ["Свободно раньше"] },
        { service_id: "svc-2", score: 0.9, reasons: ["Свободно раньше"] },
        { service_id: "svc-1", score: 0.8, reasons: ["20 минут от тебя"] },
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
    expect(data.picks).toEqual([
      { serviceId: "svc-2", reasons: ["Свободно раньше"] },
      { serviceId: "svc-1", reasons: ["20 минут от тебя"] },
    ]);
  });

  // ── Owner ruling 25.08: a pick without displayable WHY is not a
  //    branded Ayla pick, so it never reaches the screens.
  it("drops picks the scorer sent without any displayable WHY", async () => {
    mockedFetchServices.mockResolvedValue({
      services: [
        service({ id: "svc-1", name: "Маникюр" }),
        service({ id: "svc-2", name: "Педикюр" }),
      ],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
    // Today's runtime shape: `{service_id, score}` and nothing else.
    mockedFetchRecommendations.mockResolvedValue({
      recommendations: [
        { service_id: "svc-1", score: 0.9 },
        { service_id: "svc-2", score: 0.8 },
      ],
    });
    const data = await getCatalogBrowse();
    // Catalog itself is untouched — only the branded picks disappear.
    expect(data.services).toHaveLength(2);
    expect(data.picks).toEqual([]);
  });

  it("keeps the May-spec single `reasoning_text` shape too", async () => {
    mockedFetchServices.mockResolvedValue({
      services: [service({ id: "svc-1", name: "Маникюр" })],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
    mockedFetchRecommendations.mockResolvedValue({
      recommendations: [
        { service_id: "svc-1", score: 0.9, reasoning_text: "20 минут от тебя, рейтинг 4.9" },
      ],
    });
    const data = await getCatalogBrowse();
    expect(data.picks).toEqual([
      { serviceId: "svc-1", reasons: ["20 минут от тебя, рейтинг 4.9"] },
    ]);
  });

  it("trims blanks and caps WHY at 3 lines", async () => {
    mockedFetchServices.mockResolvedValue({
      services: [
        service({ id: "svc-1", name: "Маникюр" }),
        service({ id: "svc-2", name: "Педикюр" }),
      ],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
    mockedFetchRecommendations.mockResolvedValue({
      recommendations: [
        { service_id: "svc-1", score: 0.9, reasons: ["  раз  ", "", "два", "три", "четыре"] },
        { service_id: "svc-2", score: 0.8, reasons: ["   ", ""] },
      ],
    });
    const data = await getCatalogBrowse();
    expect(data.picks).toEqual([
      { serviceId: "svc-1", reasons: ["раз", "два", "три"] },
    ]);
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
    expect(data.picks).toEqual([]);
  });

  it("rejects when the mirror itself fails (screen shows the error state)", async () => {
    mockedFetchServices.mockRejectedValue(new Error("[500] http_error"));
    mockedFetchMasters.mockResolvedValue({ masters: [] });
    await expect(getCatalogBrowse()).rejects.toThrow("[500]");
  });
});

// --- DRF-1556: contract divergence ≠ unavailable scorer --------------------
//
// The two states used to share one `catch`, so a divergence was
// indistinguishable from a dead scorer and undetectable by design
// (`docs/OPEN_DECISIONS.md` §52). These tests hold them apart from both
// sides: the loud one must be loud, and — the pairing that matters more
// (DRF-1411) — the quiet one must stay quiet, or the detector gets muted
// within a week and is silent exactly when it is needed.

describe("getCatalogBrowse — contract divergence is loud", () => {
  /** The shape the source actually sends today: layers of MASTERS. */
  const LAYERED_PAYLOAD = {
    data: {
      layer_1_your_places: [],
      layer_2_ayla_picks: [
        { master_id: "mst-1", reasoning_text: "20 минут от тебя" },
      ],
      layer_3_explore: [],
    },
  };

  function mirrorReady(): void {
    mockedFetchServices.mockResolvedValue({
      services: [service({ id: "svc-1", name: "Маникюр" })],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
  }

  /** The mock is typed to the DECLARED contract — divergence is, by
   *  definition, a payload that type does not describe. */
  function respondWith(payload: unknown): void {
    mockedFetchRecommendations.mockResolvedValue(
      payload as { recommendations: RecommendationScore[] },
    );
  }

  it("says so out loud when the source answers in the layered shape", async () => {
    mirrorReady();
    respondWith(LAYERED_PAYLOAD);
    const data = await getCatalogBrowse();

    expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
    const message = String(consoleErrorSpy.mock.calls[0]![0]);
    // What we expected, and what actually arrived — both named.
    expect(message).toContain("contract mismatch");
    expect(message).toContain("`recommendations` to be an array");
    expect(message).toContain("object{data}");

    // Never fake a pick: the block stays hidden either way, and the
    // catalog around it is untouched.
    expect(data.picks).toEqual([]);
    expect(data.services).toHaveLength(1);
    expect(data.masters).toHaveLength(1);
  });

  it("says so out loud on a PARTIAL divergence (items lose `score`)", async () => {
    mirrorReady();
    respondWith({
      recommendations: [
        { service_id: "svc-1", reasoning_text: "20 минут от тебя" },
      ],
    });
    const data = await getCatalogBrowse();

    expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
    const message = String(consoleErrorSpy.mock.calls[0]![0]);
    expect(message).toContain("recommendations[0].score");
    expect(data.picks).toEqual([]);
  });

  it("never leaks payload VALUES into the log — only keys and types", async () => {
    mirrorReady();
    respondWith({
      recommendations: [{ service_id: "svc-1", score: "0.9", secret: "+79990000000" }],
    });
    await getCatalogBrowse();

    expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
    const message = String(consoleErrorSpy.mock.calls[0]![0]);
    // Presence: the log names the offending field and its key set…
    expect(message).toContain("recommendations[0].score");
    expect(message).toContain("object{service_id,score,secret}");
    // …absence: and carries none of the values behind those keys.
    expect(message).not.toContain("+79990000000");
    expect(message).not.toContain("svc-1");
  });
});

describe("getCatalogBrowse — an unavailable scorer stays SILENT (DRF-1411 pairing)", () => {
  beforeEach(() => {
    mockedFetchServices.mockResolvedValue({
      services: [service({ id: "svc-1", name: "Маникюр" })],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
  });

  const outages: ReadonlyArray<[string, unknown]> = [
    // `fetch` itself failing — no response at all.
    ["network down", new TypeError("Failed to fetch")],
    // Non-2xx, exactly as `api.ts::request` raises it.
    ["502 from the proxy", new ApiError(502, "ayla_unavailable", "upstream down")],
    ["500 from the proxy", new ApiError(500, "http_error", "Internal Server Error")],
    // Request aborted on timeout.
    ["timeout", Object.assign(new Error("The operation was aborted."), { name: "AbortError" })],
    // 2xx with a body that is not JSON — `res.json()` rejects.
    ["unparseable body", new SyntaxError("Unexpected token < in JSON at position 0")],
  ];

  for (const [label, failure] of outages) {
    it(`stays quiet and empty when the scorer is unavailable: ${label}`, async () => {
      mockedFetchRecommendations.mockRejectedValue(failure);
      const data = await getCatalogBrowse();
      // Presence — the screen still gets its catalog…
      expect(data.services).toHaveLength(1);
      expect(data.masters).toHaveLength(1);
      // …absence — no picks invented, and NOT ONE WORD logged.
      expect(data.picks).toEqual([]);
      expect(consoleErrorSpy).not.toHaveBeenCalled();
    });
  }

  it("a conforming answer is silent too — behaviour unchanged", async () => {
    mockedFetchRecommendations.mockResolvedValue({
      recommendations: [
        { service_id: "svc-1", score: 0.9, reasons: ["20 минут от тебя"] },
      ],
    });
    const data = await getCatalogBrowse();
    expect(data.picks).toEqual([
      { serviceId: "svc-1", reasons: ["20 минут от тебя"] },
    ]);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("a conforming answer WITHOUT the optional WHY fields is silent too", async () => {
    // Today's real 2xx payload from the proxy. Empty picks here is the
    // owner's WHY gate, not a divergence — it must not make noise.
    mockedFetchRecommendations.mockResolvedValue({
      recommendations: [{ service_id: "svc-1", score: 0.9 }],
    });
    const data = await getCatalogBrowse();
    expect(data.picks).toEqual([]);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("an empty list is a valid answer, not a divergence", async () => {
    mockedFetchRecommendations.mockResolvedValue({ recommendations: [] });
    const data = await getCatalogBrowse();
    expect(data.picks).toEqual([]);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });
});

// --- DRF-1556: the runtime shape check itself ------------------------------

describe("recommendationsContractViolation", () => {
  it("passes every shape the declared contract allows", () => {
    const conforming: unknown[] = [
      { recommendations: [] },
      { recommendations: [{ service_id: "svc-1", score: 0 }] },
      { recommendations: [{ service_id: "svc-1", score: -1.5, reasons: null }] },
      { recommendations: [{ service_id: "svc-1", score: 1, reasons: ["раз", "два"] }] },
      { recommendations: [{ service_id: "svc-1", score: 1, reasoning_text: "почему" }] },
      { recommendations: [{ service_id: "svc-1", score: 1, reasoning_text: null }] },
      // Forward-compat: unknown EXTRA fields are not a divergence —
      // `api.ts` says any field Ayla starts sending arrives untouched.
      { recommendations: [{ service_id: "svc-1", score: 1, tier: "gold" }] },
      { recommendations: [{ service_id: "svc-1", score: 1 }], meta: { v: 2 } },
    ];
    for (const payload of conforming) {
      expect(recommendationsContractViolation(payload)).toBeNull();
    }
  });

  it("names the first violation for every way the contract can break", () => {
    const cases: ReadonlyArray<[unknown, string]> = [
      [null, "expected an object"],
      [[], "expected an object"],
      ["", "expected an object"],
      [{}, "`recommendations` to be an array"],
      [{ data: {} }, "object{data}"],
      [{ recommendations: {} }, "`recommendations` to be an array"],
      [{ recommendations: [null] }, "recommendations[0]: expected an object"],
      [{ recommendations: [{ score: 1 }] }, "recommendations[0].service_id"],
      [{ recommendations: [{ service_id: 7, score: 1 }] }, "recommendations[0].service_id"],
      [{ recommendations: [{ service_id: "s", score: "1" }] }, "recommendations[0].score"],
      [{ recommendations: [{ service_id: "s", score: Number.NaN }] }, "recommendations[0].score"],
      [{ recommendations: [{ service_id: "s", score: 1, reasons: "одна" }] }, "reasons"],
      [{ recommendations: [{ service_id: "s", score: 1, reasons: [1] }] }, "reasons"],
      [{ recommendations: [{ service_id: "s", score: 1, reasoning_text: 5 }] }, "reasoning_text"],
      // Second item diverges — the check must not stop at the first.
      [
        {
          recommendations: [
            { service_id: "s", score: 1 },
            { service_id: "s2" },
          ],
        },
        "recommendations[1].score",
      ],
    ];
    for (const [payload, expected] of cases) {
      const violation = recommendationsContractViolation(payload);
      expect(violation, `payload: ${JSON.stringify(payload)}`).not.toBeNull();
      expect(violation).toContain(expected);
    }
  });
});
