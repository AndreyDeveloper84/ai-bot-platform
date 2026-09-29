/**
 * `customer-memory.ts` (DRF-2133) — три вызова и три подписи.
 *
 *   GET    /api/v1/customer/memory/            → форма ответа как у сервера
 *   DELETE /api/v1/customer/memory/{id}/       → метод и путь
 *   POST   /api/v1/customer/memory/forget-all/ → 202 → "deletion_pending"
 *
 * Подписи: «ты сказал(а) 19.09» только у сказанного; предположение —
 * «мы предположили» без даты; текст факта — подпись чата, иначе значение.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./max-sdk", () => ({
  getInitData: () => "test-init-data",
}));

import {
  factText,
  fetchMemory,
  forgetAll,
  forgetEntry,
  provenanceLabel,
  shortDate,
} from "./customer-memory";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function callAt(n: number): [string, RequestInit] {
  const call = fetchMock.mock.calls[n];
  if (!call) throw new Error(`fetch was not called ${n + 1} time(s)`);
  return call as [string, RequestInit];
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("fetchMemory", () => {
  it("читает GET /memory/ и отдаёт форму сервера как есть", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        green: [
          {
            id: "g1",
            key: "diet",
            label: "придерживается веганского питания",
            value: "vegan",
            said_at: "2026-09-19T20:58:47+00:00",
            provenance: "said",
          },
        ],
        health: [],
        status: "active",
      }),
    );

    const doc = await fetchMemory();

    const [url, init] = callAt(0);
    expect(url).toMatch(/\/api\/v1\/customer\/memory\/$/);
    expect(init.method ?? "GET").toBe("GET");
    expect(doc.green).toHaveLength(1);
    expect(doc.green[0]?.provenance).toBe("said");
    expect(doc.health).toEqual([]);
    expect(doc.status).toBe("active");
  });

  it("незнакомый статус читается как active, отсутствующие списки — как пустые", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "weird" }));
    const doc = await fetchMemory();
    expect(doc).toEqual({ green: [], health: [], status: "active" });
  });

  it("deletion_pending доходит до экрана", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ green: [], health: [], status: "deletion_pending" }),
    );
    expect((await fetchMemory()).status).toBe("deletion_pending");
  });
});

describe("forgetEntry / forgetAll", () => {
  it("DELETE идёт на /memory/{id}/", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "g1", deleted: true }));
    await forgetEntry("g1");
    const [url, init] = callAt(0);
    expect(url).toMatch(/\/api\/v1\/customer\/memory\/g1\/$/);
    expect(init.method).toBe("DELETE");
  });

  it("DELETE 404 — записи уже нет: не ошибка", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "not_found", detail: "нет" }, 404));
    await expect(forgetEntry("gone")).resolves.toBeUndefined();
  });

  it("DELETE 500 — ошибка доходит до карточки", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "server", detail: "x" }, 500));
    await expect(forgetEntry("g1")).rejects.toBeInstanceOf(Error);
  });

  it("POST /memory/forget-all/ → deletion_pending", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "deletion_pending" }, 202));
    expect(await forgetAll()).toBe("deletion_pending");
    const [url, init] = callAt(0);
    expect(url).toMatch(/\/api\/v1\/customer\/memory\/forget-all\/$/);
    expect(init.method).toBe("POST");
  });
});

describe("подписи", () => {
  it("сказанное — с датой, предположение — без", () => {
    expect(provenanceLabel({ provenance: "said", said_at: "2026-09-19T20:58:47+00:00" })).toBe(
      "ты сказал(а) 19.09",
    );
    expect(provenanceLabel({ provenance: "inferred", said_at: "2026-09-19T20:58:47+00:00" })).toBe(
      "мы предположили",
    );
  });

  it("shortDate: ISO → дд.мм, мусор → пустая строка", () => {
    expect(shortDate("2026-01-05T00:00:00Z")).toBe("05.01");
    expect(shortDate("not-a-date")).toBe("");
  });

  it("factText: подпись чата, иначе значение, иначе ключ", () => {
    expect(factText({ label: "ест халяль", value: "halal", key: "diet" })).toBe("ест халяль");
    expect(factText({ label: null, value: "halal", key: "diet" })).toBe("halal");
    expect(factText({ label: null, value: null, key: "diet" })).toBe("diet");
  });
});
