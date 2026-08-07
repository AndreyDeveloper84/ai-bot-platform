/**
 * Tests for `personal-data.ts` — the C5 (152-ФЗ) client of the frozen
 * pilot contract (PILOT_CONTRACTS_2026-08-15 §6):
 *
 *   GET    /api/v1/customer/me/personal-data/export/  → JSON attachment
 *   DELETE /api/v1/customer/me/personal-data/         → {status:"deleted"}
 *        200 on success (idempotent — repeat deletes also return 200),
 *        502 {status:"partial", failed_steps:[...]} on partial failure.
 *
 * `failed_steps` values are backend cascade slugs: "ayla_delete" |
 * "memory_delete" | "consent_withdraw" (apps/identity/services/privacy.py).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";

vi.mock("./max-sdk", () => ({
  getInitData: () => "test-init-data",
}));

import {
  DELETE_CONFIRMATION_TOKEN,
  deletePersonalData,
  exportPersonalData,
  PersonalDataPartialDeleteError,
  triggerDownload,
} from "./personal-data";

const fetchMock = vi.fn();

function jsonResponse(
  body: unknown,
  init: { status?: number; headers?: Record<string, string> } = {},
): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("exportPersonalData", () => {
  it("GETs the C5 export endpoint with MAX auth and returns blob + filename", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { profile: { display_name: "Анна" } },
        {
          headers: {
            "Content-Disposition": 'attachment; filename="personal-data-export.json"',
          },
        },
      ),
    );
    const result = await exportPersonalData();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/customer/me/personal-data/export/");
    expect((init.method ?? "GET").toUpperCase()).toBe("GET");
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("MaxInitData test-init-data");
    expect(result.filename).toBe("personal-data-export.json");
    expect(await result.blob.text()).toContain("Анна");
  });

  it("falls back to the contract filename when Content-Disposition is absent", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    const result = await exportPersonalData();
    expect(result.filename).toBe("personal-data-export.json");
  });

  it("throws ApiError with the backend slug on upstream failure (502)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          error: "upstream_unavailable",
          detail: "personal-data export is temporarily unavailable, try again later",
        },
        { status: 502 },
      ),
    );
    const err = await exportPersonalData().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).slug).toBe("upstream_unavailable");
  });

  it("throws ApiError on non-JSON error responses", async () => {
    fetchMock.mockResolvedValue(new Response("oops", { status: 500 }));
    const err = await exportPersonalData().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
  });
});

describe("deletePersonalData", () => {
  it("DELETEs the C5 endpoint with MAX auth and resolves on {status: deleted}", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "deleted" }));
    const result = await deletePersonalData(DELETE_CONFIRMATION_TOKEN);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/customer/me/personal-data/");
    expect(init.method).toBe("DELETE");
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("MaxInitData test-init-data");
    expect(result.status).toBe("deleted");
  });

  it("sends the confirmation token in the body (server verifies it)", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "deleted" }));
    await deletePersonalData(DELETE_CONFIRMATION_TOKEN);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      confirmation: DELETE_CONFIRMATION_TOKEN,
    });
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  });

  it("relays a wrong token verbatim so the server can reject it", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: "confirmation_mismatch", detail: "nope" },
        { status: 400 },
      ),
    );
    const err = await deletePersonalData("удалить").catch((e: unknown) => e);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ confirmation: "удалить" });
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(400);
  });

  it("stays successful on a repeat call (backend idempotency, C5.2)", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse({ status: "deleted" })),
    );
    await expect(deletePersonalData(DELETE_CONFIRMATION_TOKEN)).resolves.toEqual({
      status: "deleted",
    });
    await expect(deletePersonalData(DELETE_CONFIRMATION_TOKEN)).resolves.toEqual({
      status: "deleted",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("throws PersonalDataPartialDeleteError with failed_steps on 502 partial", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { status: "partial", failed_steps: ["memory_delete", "consent_withdraw"] },
        { status: 502 },
      ),
    );
    const err = await deletePersonalData(DELETE_CONFIRMATION_TOKEN).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(PersonalDataPartialDeleteError);
    expect((err as PersonalDataPartialDeleteError).failedSteps).toEqual([
      "memory_delete",
      "consent_withdraw",
    ]);
  });

  it("throws ApiError on other failures", async () => {
    fetchMock.mockResolvedValue(new Response("boom", { status: 503 }));
    const err = await deletePersonalData(DELETE_CONFIRMATION_TOKEN).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(503);
  });
});

describe("triggerDownload", () => {
  it("clicks a temporary anchor with the download attribute and revokes the URL", () => {
    vi.useFakeTimers();
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    const createUrl = vi.fn(() => "blob:mock-url");
    const revokeUrl = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, {
      createObjectURL: createUrl,
      revokeObjectURL: revokeUrl,
    }));

    triggerDownload(new Blob(["{}"], { type: "application/json" }), "data.json");

    expect(createUrl).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeUrl).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(revokeUrl).toHaveBeenCalledWith("blob:mock-url");
    vi.useRealTimers();
  });
});
