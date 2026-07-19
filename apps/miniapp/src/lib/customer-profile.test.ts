/**
 * Unit tests for the pure helpers of `customer-profile.ts`
 * (pluralisation, consent-date formatting, avatar initials) plus the
 * booking-flow client wrappers of `customer-booking.ts` with the HTTP
 * layer (`./api`) mocked — booking-flow "on mocks" per pilot phase 1.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  additionalSalonsLabel,
  avatarInitials,
  formatConsentDate,
} from "./customer-profile";

// --- customer-profile pure helpers -----------------------------------------

describe("additionalSalonsLabel (Russian plural rules)", () => {
  it("returns empty string for zero / negative counts", () => {
    expect(additionalSalonsLabel(0)).toBe("");
    expect(additionalSalonsLabel(-3)).toBe("");
  });

  it("picks салон / салона / салонов by Slavic plural categories", () => {
    expect(additionalSalonsLabel(1)).toBe("+1 салон");
    expect(additionalSalonsLabel(2)).toBe("+2 салона");
    expect(additionalSalonsLabel(4)).toBe("+4 салона");
    expect(additionalSalonsLabel(5)).toBe("+5 салонов");
    expect(additionalSalonsLabel(11)).toBe("+11 салонов");
    expect(additionalSalonsLabel(14)).toBe("+14 салонов");
    expect(additionalSalonsLabel(21)).toBe("+21 салон");
    expect(additionalSalonsLabel(22)).toBe("+22 салона");
    expect(additionalSalonsLabel(111)).toBe("+111 салонов");
  });
});

describe("avatarInitials", () => {
  it("takes initials from a two-word name, uppercased", () => {
    expect(avatarInitials("Анна Петрова")).toBe("АП");
  });

  it("falls back to a single uppercased character for one-word names", () => {
    expect(avatarInitials("мария")).toBe("М");
  });

  it("returns the calm placeholder for empty / blank names", () => {
    expect(avatarInitials("")).toBe("·");
    expect(avatarInitials("   ")).toBe("·");
  });
});

describe("formatConsentDate", () => {
  it("renders an ISO timestamp as a calm Russian calendar date", () => {
    const out = formatConsentDate("2026-05-14T10:30:00+03:00");
    expect(out).toContain("мая");
    expect(out).toContain("2026");
  });

  it("passes through unparseable input unchanged", () => {
    expect(formatConsentDate("not-a-date")).toBe("not-a-date");
  });
});

// --- customer-booking with mocked HTTP layer --------------------------------

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    fetchMaster: vi.fn(),
    fetchSlots: vi.fn(),
    createBooking: vi.fn(),
  };
});

import { createBooking, fetchSlots } from "./api";
import { createCustomerBooking, getCustomerSlots } from "./customer-booking";

const mockedFetchSlots = vi.mocked(fetchSlots);
const mockedCreateBooking = vi.mocked(createBooking);

beforeEach(() => {
  vi.clearAllMocks();
});

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
    const diffDays = Math.round((to.getTime() - from.getTime()) / 86_400_000);
    expect(diffDays).toBe(14);
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
