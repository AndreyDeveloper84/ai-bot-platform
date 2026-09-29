/**
 * Избранные блюда — клиент Mini App (DRF-2092, F12).
 *
 * Предмет — провод: куда идёт запрос, с чем, и как 201/200 каталога
 * («сохранила» / «уже в избранном») доезжают до экрана разными словами.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./max-sdk", () => ({
  getInitData: () => "init-data-2092",
}));

import { deleteSavedMeal, listSavedMeals, saveMealFromEntry } from "./saved-meals";

const fetchMock = vi.fn();

const ROW = {
  id: "sm-1",
  dish_name: "Борщ",
  portion_g: 250,
  calories: 125,
  protein_g: 5,
  fat_g: 7.5,
  carbs_g: 10,
  source_food_log_id: null,
  created_at: "2026-09-18T12:00:00+00:00",
};

function respond(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

describe("saved-meals: провод к customer/saved-meals", () => {
  it("список — GET /saved-meals, строки как отдал сервер", async () => {
    fetchMock.mockResolvedValueOnce(respond(200, { items: [ROW] }));

    const rows = await listSavedMeals();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/customer\/saved-meals$/);
    expect(init.method ?? "GET").toBe("GET");
    expect(rows).toEqual([ROW]);
  });

  it("из записи — POST {food_log_id}; 201 → created, 200 → уже была", async () => {
    fetchMock.mockResolvedValueOnce(respond(201, { ...ROW, source_food_log_id: "fl-1" }));
    const first = await saveMealFromEntry("fl-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/customer\/saved-meals$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ food_log_id: "fl-1" });
    expect(first).toEqual({ created: true, meal: { ...ROW, source_food_log_id: "fl-1" } });

    fetchMock.mockResolvedValueOnce(respond(200, { ...ROW, source_food_log_id: "fl-1" }));
    const second = await saveMealFromEntry("fl-1");
    expect(second.created).toBe(false);
  });

  it("удалить — DELETE /saved-meals/{id}", async () => {
    fetchMock.mockResolvedValueOnce(respond(200, { id: "sm-1", deleted: true }));

    await deleteSavedMeal("sm-1");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/customer\/saved-meals\/sm-1$/);
    expect(init.method).toBe("DELETE");
  });
});
