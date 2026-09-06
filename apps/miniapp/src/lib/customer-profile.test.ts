/**
 * Unit tests for the pure helpers of `customer-profile.ts`
 * (pluralisation, consent-date formatting, avatar initials) and for
 * the real-API fetch wrappers (DRF-1475): имя и маркетинговое
 * согласие ходят в настоящий `/customer/me` через lib/api.ts, поэтому
 * request-слой мокируется на границе модуля.
 * Booking-flow lib tests live in `customer-booking.test.ts`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  additionalSalonsLabel,
  avatarInitials,
  fetchConsents,
  fetchMe,
  formatConsentDate,
  setMarketingConsent,
} from "./customer-profile";
import { fetchProfile, updateProfile, type Profile } from "./api";

vi.mock("./api", () => ({
  fetchProfile: vi.fn(),
  updateProfile: vi.fn(),
}));

const fetchProfileMock = vi.mocked(fetchProfile);
const updateProfileMock = vi.mocked(updateProfile);

function profileFixture(overrides: Partial<Profile> = {}): Profile {
  return {
    bot_user_id: "u-1",
    display_name: "Анна Петрова",
    client_name: "Аня",
    phone_masked: "+7 ••• ••• 45 67",
    timezone: "Europe/Moscow",
    joined_at: "2026-05-14T10:30:00+03:00",
    preferences: {
      notify_reminders: true,
      notify_retention: true,
      notify_promo: false,
      notify_birthday: false,
      birthday_date: null,
    },
    favorites: { master_name: null, service_name: null },
    ...overrides,
  };
}

// --- customer-profile fetch wrappers → реальный /customer/me (DRF-1475) ---

describe("fetchMe (реальный GET /customer/me)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("maps the real profile, preferring the self-given client_name", async () => {
    fetchProfileMock.mockResolvedValue(profileFixture());
    const me = await fetchMe();
    expect(fetchProfileMock).toHaveBeenCalledTimes(1);
    expect(me.display_name).toBe("Аня");
    // Ручка не отдаёт handle/tenant_names — честные пустые значения,
    // экран эти строки скрывает.
    expect(me.max_handle).toBe("");
    expect(me.tenant_names).toEqual([]);
  });

  it("falls back to the channel display_name when client_name is empty", async () => {
    fetchProfileMock.mockResolvedValue(profileFixture({ client_name: "" }));
    const me = await fetchMe();
    expect(me.display_name).toBe("Анна Петрова");
  });
});

describe("fetchConsents (реальный GET /customer/me → notify_promo)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("maps preferences.notify_promo onto marketing_consent", async () => {
    fetchProfileMock.mockResolvedValue(
      profileFixture({
        preferences: {
          notify_reminders: true,
          notify_retention: true,
          notify_promo: true,
          notify_birthday: false,
          birthday_date: null,
        },
      }),
    );
    const consents = await fetchConsents();
    expect(consents.marketing_consent).toBe(true);
    expect(consents.is_booking_pii_locked).toBe(true);
    expect(consents.is_master_data_locked).toBe(true);
  });
});

describe("setMarketingConsent (реальный PATCH /customer/me)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("выдача: шлёт notify_promo=true и перечитывает факт с сервера", async () => {
    updateProfileMock.mockResolvedValue(
      profileFixture({
        preferences: {
          notify_reminders: true,
          notify_retention: true,
          notify_promo: true,
          notify_birthday: false,
          birthday_date: null,
        },
      }),
    );
    const consents = await setMarketingConsent(true);
    expect(updateProfileMock).toHaveBeenCalledWith({ notify_promo: true });
    expect(consents.marketing_consent).toBe(true);
  });

  it("отзыв: шлёт notify_promo=false", async () => {
    updateProfileMock.mockResolvedValue(profileFixture());
    const consents = await setMarketingConsent(false);
    expect(updateProfileMock).toHaveBeenCalledWith({ notify_promo: false });
    expect(consents.marketing_consent).toBe(false);
  });
});


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
