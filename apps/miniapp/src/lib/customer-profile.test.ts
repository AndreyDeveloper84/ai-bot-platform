/**
 * Unit tests for the pure helpers of `customer-profile.ts`
 * (pluralisation, consent-date formatting, avatar initials).
 * Booking-flow lib tests live in `customer-booking.test.ts`.
 */
import { describe, expect, it } from "vitest";

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
    // Local-constructor input pins the calendar day in ANY test-env TZ.
    const iso = new Date(2026, 4, 14, 12, 30).toISOString();
    expect(formatConsentDate(iso)).toBe("14 мая 2026");
  });

  it("passes through unparseable input unchanged", () => {
    expect(formatConsentDate("not-a-date")).toBe("not-a-date");
  });
});
