/**
 * Unit tests for `customer-profile.ts` — настоящие ручки согласий
 * (DRF-1475 §24, DRF-1520) плюс чистые хелперы (склонение, дата
 * согласия, инициалы).
 *
 * Имя по-прежнему читается из `/customer/me` (`fetchProfile`), а все
 * согласия — из `me/consents/`, поэтому мокируется и `fetchProfile`, и
 * сам `request`: `fetchProfile` держит ссылку на исходный `request`
 * внутри модуля, и подмена одного другого не перехватывает.
 * `ApiError` остаётся настоящим — по нему различаются 409 и 502.
 *
 * Booking-flow lib tests live in `customer-booking.test.ts`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  additionalSalonsLabel,
  avatarInitials,
  DATA_STORAGE_PARTIAL_PROCESSING_NOTE,
  DATA_STORAGE_REVOCATION_DISCLOSURE_TEXT,
  DataStorageRevocationFailedError,
  fetchConsents,
  fetchMe,
  fetchProactivePrefs,
  formatConsentDate,
  revokeDataStorage,
  setMarketingConsent,
  setProactiveOptOut,
  StaleDisclosureError,
} from "./customer-profile";
import { ApiError, fetchProfile, request, type Profile } from "./api";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    fetchProfile: vi.fn(),
    updateProfile: vi.fn(),
    request: vi.fn(),
  };
});

const fetchProfileMock = vi.mocked(fetchProfile);
const requestMock = vi.mocked(request);

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

interface DocOptions {
  marketing?: boolean;
  storageGranted?: boolean;
  storageAt?: string | null;
  hintsEnabled?: boolean;
  disclosureVersion?: string;
  revocation?: {
    status: string;
    failed_steps?: string[];
    failed_details?: Record<string, string>;
  };
}

/** Форма ответа `apps/consent/customer.py::read_consents`, снята с кода. */
function consentsDoc(o: DocOptions = {}): Record<string, unknown> {
  const marketing = o.marketing ?? false;
  const storageGranted = o.storageGranted ?? true;
  const storageAt =
    o.storageAt === undefined ? "2026-05-14T10:30:00+03:00" : o.storageAt;
  const doc: Record<string, unknown> = {
    consents: {
      personal_data: {
        granted: storageGranted,
        granted_at: storageGranted ? storageAt : null,
        document_version: "welcome-v1",
      },
      marketing: {
        granted: marketing,
        granted_at: marketing ? "2026-06-01T09:00:00+03:00" : null,
        document_version: marketing ? "marketing-v1" : "",
      },
      photo_biometric: { granted: false, granted_at: null, document_version: "" },
      health: { granted: false, granted_at: null, document_version: "" },
      memory_green: { granted: false, granted_at: null, document_version: "" },
      memory_yellow: { granted: false, granted_at: null, document_version: "" },
      memory_red: { granted: false, granted_at: null, document_version: "" },
    },
    proactive_hints: { enabled: o.hintsEnabled ?? true },
    data_storage: {
      granted: storageGranted,
      granted_at: storageGranted ? storageAt : null,
      document_version: "welcome-v1",
      revocation: {
        disclosure_version:
          o.disclosureVersion ?? "data-storage-revocation-v1",
        consequences: [
          "ayla_delete",
          "memory_delete",
          "consent_withdraw",
          "profile_pii_erase",
          "staff_assistant_erase",
          "dialogue_anonymize",
        ],
        retained: ["bookings", "payments"],
      },
    },
  };
  if (o.revocation) doc.revocation = o.revocation;
  return doc;
}

// --- утверждённый владельцем текст --------------------------------------

describe("текст последствий отзыва (§35 п.7)", () => {
  it("совпадает с утверждённым дословно", () => {
    // Стража против «причёсывания» под голос экрана. Правка этого теста
    // допустима только вместе с новым решением владельца.
    expect(DATA_STORAGE_REVOCATION_DISCLOSURE_TEXT).toBe(
      "После подтверждения Ayla перестанет сохранять и использовать ваши " +
        "данные, основанные на этом согласии. Доступные для удаления данные " +
        "будут удалены. Сведения, которые мы обязаны хранить по закону или " +
        "для исполнения ваших действующих записей, могут сохраниться на " +
        "необходимый срок. Сам аккаунт и доступ к записям останутся. " +
        "Вернуть удалённые данные будет нельзя.",
    );
  });

  it("формулировки частичного исхода нет — она у владельца (Q-CLIENT-03)", () => {
    // Положительная стража рядом: утверждённый текст при этом на месте,
    // то есть пустое значение — это отсутствие ОДНОЙ формулировки, а не
    // пустой модуль (DRF-1411).
    expect(DATA_STORAGE_REVOCATION_DISCLOSURE_TEXT.length).toBeGreaterThan(0);
    expect(DATA_STORAGE_PARTIAL_PROCESSING_NOTE).toBeNull();
  });
});

// --- fetchMe / согласия → настоящие ручки --------------------------------

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

describe("fetchConsents (реальный GET me/consents/)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("читает документ согласий одним запросом и раскладывает его", async () => {
    requestMock.mockResolvedValue(consentsDoc({ marketing: true }));
    const consents = await fetchConsents();
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(requestMock).toHaveBeenCalledWith("/me/consents/");
    expect(consents.marketing_consent).toBe(true);
    expect(consents.data_storage_granted).toBe(true);
    expect(consents.data_storage_consent_at).toBe("2026-05-14T10:30:00+03:00");
    expect(consents.data_storage_disclosure_version).toBe(
      "data-storage-revocation-v1",
    );
    expect(consents.proactive_hints_enabled).toBe(true);
    expect(consents.is_booking_pii_locked).toBe(true);
    expect(consents.is_master_data_locked).toBe(true);
  });

  it("отозванное согласие: даты нет, и она не выдумывается", async () => {
    requestMock.mockResolvedValue(consentsDoc({ storageGranted: false }));
    const consents = await fetchConsents();
    expect(consents.data_storage_granted).toBe(false);
    expect(consents.data_storage_consent_at).toBe("");
  });

  it("телефон по этому пути не приходит и не пробрасывается (DRF-1039)", async () => {
    requestMock.mockResolvedValue(consentsDoc({ marketing: true }));
    const consents = await fetchConsents();
    // Положительная стража ПЕРЕД отрицанием: нужное доехало, значит
    // отсутствие телефона — факт, а не пустой ответ (DRF-1411).
    expect(consents.marketing_consent).toBe(true);
    expect(consents.data_storage_disclosure_version).toBe(
      "data-storage-revocation-v1",
    );
    const serialised = JSON.stringify(consents);
    expect(serialised).not.toMatch(/phone/i);
    expect(serialised).not.toMatch(/\+7/);
  });
});

describe("setMarketingConsent (реестр, а не зеркало notify_promo)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("выдача: POST me/consents/marketing/ и факт из ответа", async () => {
    requestMock.mockResolvedValue(consentsDoc({ marketing: true }));
    const consents = await setMarketingConsent(true);
    expect(requestMock).toHaveBeenCalledWith("/me/consents/marketing/", {
      method: "POST",
    });
    expect(consents.marketing_consent).toBe(true);
  });

  it("отзыв: DELETE me/consents/marketing/", async () => {
    requestMock.mockResolvedValue(consentsDoc({ marketing: false }));
    const consents = await setMarketingConsent(false);
    expect(requestMock).toHaveBeenCalledWith("/me/consents/marketing/", {
      method: "DELETE",
    });
    expect(consents.marketing_consent).toBe(false);
  });

  it("PATCH /me с notify_promo больше не используется", async () => {
    requestMock.mockResolvedValue(consentsDoc({ marketing: true }));
    // Положительная стража: запись состоялась именно по пути согласий.
    await setMarketingConsent(true);
    expect(requestMock).toHaveBeenCalledWith("/me/consents/marketing/", {
      method: "POST",
    });
    const paths = requestMock.mock.calls.map((c) => c[0]);
    expect(paths).not.toContain("/me");
  });
});

describe("подсказки Ayla (me/consents/proactive-hints/)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("чтение: enabled документа → отрицательный контракт клиента", async () => {
    requestMock.mockResolvedValue(consentsDoc({ hintsEnabled: false }));
    const prefs = await fetchProactivePrefs();
    expect(requestMock).toHaveBeenCalledWith("/me/consents/");
    expect(prefs.proactive_messages_opt_out).toBe(true);
  });

  it("запись: opt-out превращается в enabled=false и обратно", async () => {
    requestMock.mockResolvedValue(consentsDoc({ hintsEnabled: false }));
    const off = await setProactiveOptOut(true);
    expect(requestMock).toHaveBeenCalledWith(
      "/me/consents/proactive-hints/",
      { method: "POST", body: JSON.stringify({ enabled: false }) },
    );
    expect(off.proactive_messages_opt_out).toBe(true);

    requestMock.mockResolvedValue(consentsDoc({ hintsEnabled: true }));
    const on = await setProactiveOptOut(false);
    expect(requestMock).toHaveBeenLastCalledWith(
      "/me/consents/proactive-hints/",
      { method: "POST", body: JSON.stringify({ enabled: true }) },
    );
    expect(on.proactive_messages_opt_out).toBe(false);
  });
});

describe("revokeDataStorage (DELETE me/consents/data-storage/)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("шлёт обе половины подтверждения и читает исход из ответа", async () => {
    requestMock.mockResolvedValue(
      consentsDoc({
        storageGranted: false,
        revocation: { status: "revoked", failed_steps: [] },
      }),
    );
    const result = await revokeDataStorage("УДАЛИТЬ", "data-storage-revocation-v1");
    expect(requestMock).toHaveBeenCalledWith("/me/consents/data-storage/", {
      method: "DELETE",
      body: JSON.stringify({
        confirmation: "УДАЛИТЬ",
        disclosure_version: "data-storage-revocation-v1",
      }),
    });
    expect(result.status).toBe("revoked");
    expect(result.consents.data_storage_granted).toBe(false);
  });

  it("частичный исход называется частичным, а не успехом", async () => {
    requestMock.mockResolvedValue(
      consentsDoc({
        storageGranted: false,
        revocation: {
          status: "revoked_partial_processing",
          failed_steps: ["ayla_delete"],
          failed_details: { ayla_delete: "not_linked" },
        },
      }),
    );
    const result = await revokeDataStorage("УДАЛИТЬ", "data-storage-revocation-v1");
    expect(result.status).toBe("revoked_partial_processing");
    // Положительная стража: состояние при этом перечитано и согласия нет.
    expect(result.consents.data_storage_granted).toBe(false);
  });

  it("409 stale_disclosure — отдельный тип, а не общий сбой", async () => {
    requestMock.mockRejectedValue(
      new ApiError(409, "stale_disclosure", "disclosure changed"),
    );
    await expect(
      revokeDataStorage("УДАЛИТЬ", "data-storage-revocation-v0"),
    ).rejects.toBeInstanceOf(StaleDisclosureError);
  });

  it("502 — не состоялся сам отзыв", async () => {
    requestMock.mockRejectedValue(new ApiError(502, "", ""));
    await expect(
      revokeDataStorage("УДАЛИТЬ", "data-storage-revocation-v1"),
    ).rejects.toBeInstanceOf(DataStorageRevocationFailedError);
  });

  it("обрыв сети пробрасывается как есть — исход неизвестен", async () => {
    const boom = new TypeError("Failed to fetch");
    requestMock.mockRejectedValue(boom);
    await expect(
      revokeDataStorage("УДАЛИТЬ", "data-storage-revocation-v1"),
    ).rejects.toBe(boom);
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
