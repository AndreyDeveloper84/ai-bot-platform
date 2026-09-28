/**
 * DRF-2554 — отказ записи в дневник назван классом, а не свален в один.
 *
 * До листа `logMeal` не разбирал ошибок, и экран отвечал одной фразой на
 * тринадцать причин. Здесь проверяется провод: каждый отказ бота приходит
 * своим классом; 2xx с нечитаемым телом — НЕ отказ (запись сделана);
 * ответа дольше `LOG_MEAL_TIMEOUT_MS` не ждём, и это тоже свой класс.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./max-sdk", () => ({
  getInitData: () => "test-init-data",
}));

import {
  FoodLogAnswerUnreadableError,
  FoodLogRefusedError,
  LOG_MEAL_TIMEOUT_MS,
  logMeal,
  type FoodLogRefusalKind,
} from "./food-scanner";

const fetchMock = vi.fn();

const REQ = {
  scan_id: "scan-2554-1",
  meal_type: "snack" as const,
  portion_multiplier: 1,
  idempotency_key: "scan-2554-1:k",
};

function reply(body: string, status: number): Response {
  return new Response(body, { status, headers: { "Content-Type": "application/json" } });
}

async function refusalOf(): Promise<unknown> {
  try {
    await logMeal(REQ);
  } catch (err) {
    return err;
  }
  throw new Error("logMeal resolved — expected a refusal");
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("logMeal — каждый отказ бота своим классом", () => {
  it.each<[number, string, FoodLogRefusalKind]>([
    [403, "consent_required", "consent"],
    [403, "food_diary_consent_required", "consent"],
    [404, "nutrition_disabled", "nutrition_disabled"],
    [400, "malformed", "malformed"],
    [400, "food_not_recognized", "food_not_recognized"],
    [400, "ayla_bad_request", "catalog_rejected"],
    [503, "nutrition_unavailable", "nutrition_unavailable"],
    [401, "no_init_data", "auth"],
    [500, "http_error", "server_error"],
    [418, "something_new", "unknown"],
    [400, "constructor", "unknown"],
  ])("%i %s → %s", async (status, slug, kind) => {
    fetchMock.mockResolvedValue(reply(JSON.stringify({ error: slug, detail: "x" }), status));
    const err = await refusalOf();
    expect(err).toBeInstanceOf(FoodLogRefusedError);
    expect((err as FoodLogRefusedError).kind).toBe(kind);
    expect((err as FoodLogRefusedError).status).toBe(status);
  });

  it("нет ответа вовсе (fetch отказал TypeError) — network", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await refusalOf();
    expect(err).toBeInstanceOf(FoodLogRefusedError);
    expect((err as FoodLogRefusedError).kind).toBe("network");
  });

  it("5xx с не-JSON телом — server_error, а не «успех»", async () => {
    fetchMock.mockResolvedValue(new Response("<html>502</html>", { status: 502 }));
    const err = await refusalOf();
    expect((err as FoodLogRefusedError).kind).toBe("server_error");
  });
});

describe("logMeal — 2xx с нечитаемым телом это НЕ отказ", () => {
  it("201 с не-JSON телом — FoodLogAnswerUnreadableError, не FoodLogRefusedError", async () => {
    fetchMock.mockResolvedValue(new Response("<html>ok</html>", { status: 201 }));
    const err = await refusalOf();
    expect(err).toBeInstanceOf(FoodLogAnswerUnreadableError);
    expect(err).not.toBeInstanceOf(FoodLogRefusedError);
  });

  it("положительная пара: 201 с телом — запись, без исключения", async () => {
    fetchMock.mockResolvedValue(
      reply(
        JSON.stringify({ log_id: "l1", dish_name: "помидор", meal_type: "snack", calories: null }),
        201,
      ),
    );
    await expect(logMeal(REQ)).resolves.toMatchObject({ log_id: "l1", calories: null });
  });
});

describe("logMeal — ответа не ждём бесконечно", () => {
  it("таймаут прерывает запрос и назван классом timeout", async () => {
    vi.useFakeTimers();
    let seen: AbortSignal | undefined;
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          seen = init.signal ?? undefined;
          init.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const pending = refusalOf();
    await vi.advanceTimersByTimeAsync(LOG_MEAL_TIMEOUT_MS - 1);
    expect(seen?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const err = await pending;
    expect(seen?.aborted).toBe(true);
    expect(err).toBeInstanceOf(FoodLogRefusedError);
    expect((err as FoodLogRefusedError).kind).toBe("timeout");
  });

  it("таймаут длиннее ожидания каталога ботом (10 с)", () => {
    expect(LOG_MEAL_TIMEOUT_MS).toBeGreaterThan(10_000);
  });
});
