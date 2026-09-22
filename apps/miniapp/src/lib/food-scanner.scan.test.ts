/**
 * Фото-половина F8 — настоящий провод `scanPhoto` / `logMeal` (DRF-2098).
 *
 * Решение владельца 18.09 (§48 п.4): «food-diary-v1 покрывает фото из Mini
 * App». Здесь проверяется провод, не экран: multipart без ручного JSON
 * Content-Type, AbortSignal доходит до fetch, три отказа бота приводятся к
 * таксономии §7, запись шлёт `scan_id` (и при переименовании — рядом с
 * `dish_name`), а `note` не уезжает. И negative-guard: stub-ветка мертва —
 * `guardProd` на scan/log не зовётся ни в DEV, ни в prod.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./max-sdk", () => ({
  getInitData: () => "test-init-data",
}));

import { ApiError } from "./api";
import {
  FoodNotRecognizedError,
  NutritionUnavailableError,
  ScanBudgetExhaustedError,
  ScanDailyLimitError,
  PhotoTooLargeError,
  logMeal,
  scanPhoto,
} from "./food-scanner";

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

const PHOTO = new File(
  [new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3])],
  "plate.jpg",
  {
    type: "image/jpeg",
  },
);

const SCAN_WIRE = {
  scan_id: "scan-2098-1",
  dish_name: "борщ",
  confidence: 0.83,
  portion_g: 300,
  nutrition: { calories: 250, protein_g: 12, fat_g: 8, carbs_g: 32 },
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("scanPhoto — multipart к POST /food/scan", () => {
  it("шлёт файл полем image через FormData, без ручного JSON Content-Type", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(SCAN_WIRE));

    const result = await scanPhoto(PHOTO);

    const [url, init] = callAt(0);
    expect(url).toMatch(/\/food\/scan$/);
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const image = (init.body as FormData).get("image");
    expect(image).toBeInstanceOf(File);
    expect((image as File).name).toBe("plate.jpg");
    const headers = new Headers(init.headers);
    expect(headers.get("Content-Type")).toBeNull();
    expect(headers.get("Authorization")).toBe("MaxInitData test-init-data");
    expect(result).toEqual({ ...SCAN_WIRE, beauty_insights: null });
  });

  it("AbortSignal с экрана доходит до fetch", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(SCAN_WIRE));
    const controller = new AbortController();

    await scanPhoto(PHOTO, { signal: controller.signal });

    const [, init] = callAt(0);
    expect(init.signal).toBe(controller.signal);
  });

  it.each([
    ["food_not_recognized", 400, FoodNotRecognizedError],
    // DRF-2195 — слаги бюджета читаются ДО `nutrition_unavailable`. Опечатка
    // с любой стороны провода (`views.py::_food_text_catalog_refusal` ↔ этот
    // модуль) уронила бы оба отказа в общую ветку экрана «Сервис недоступен,
    // попробуй через минуту» — ровно тот дефект, который лист чинит.
    ["food_scan_daily_limit", 429, ScanDailyLimitError],
    ["food_scan_budget_exhausted", 503, ScanBudgetExhaustedError],
    // DRF-2318 — стойкий отказ распознавателя: тот же честный экран без «через минуту».
    ["food_scan_provider_down", 503, ScanBudgetExhaustedError],
    ["nutrition_unavailable", 503, NutritionUnavailableError],
    ["photo_too_large", 413, PhotoTooLargeError],
  ])("отказ бота %s → своя ошибка §7", async (slug, status, cls) => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: slug, detail: "x" }, status),
    );
    await expect(scanPhoto(PHOTO)).rejects.toBeInstanceOf(cls);
  });

  it("отказ ворот дневника пробрасывается как ApiError со своим слагом", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: "food_diary_consent_required", detail: "x" }, 403),
    );
    const err = await scanPhoto(PHOTO).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).slug).toBe("food_diary_consent_required");
  });

  it("не подменяет ответ stub-данными в DEV: то, что вернул бот, то и отдано", async () => {
    vi.stubEnv("DEV", true);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ ...SCAN_WIRE, dish_name: "свекольник" }),
    );
    const result = await scanPhoto(PHOTO);
    expect(result.dish_name).toBe("свекольник");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("logMeal — запись по scan_id через POST /food/log", () => {
  const WIRE = {
    log_id: "01J9FOODPHOTO0000000000AA",
    dish_name: "борщ",
    meal_type: "lunch",
    calories: 250,
    entry_origin: null,
  };

  it("шлёт scan_id, множитель, тип приёма и ключ — и НЕ шлёт note", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(WIRE, 201));

    const res = await logMeal({
      scan_id: "scan-2098-1",
      meal_type: "lunch",
      portion_multiplier: 1.5,
      idempotency_key: "k-1",
      note: "без сметаны",
    });

    const [url, init] = callAt(0);
    expect(url).toMatch(/\/food\/log$/);
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body).toEqual({
      scan_id: "scan-2098-1",
      meal_type: "lunch",
      portion_multiplier: 1.5,
      idempotency_key: "k-1",
    });
    expect("note" in body).toBe(false);
    expect("dish_name" in body).toBe(false);
    expect(res).toEqual({
      log_id: WIRE.log_id,
      dish_name: "борщ",
      meal_type: "lunch",
      calories: 250,
    });
  });

  it("переименование шлёт dish_name РЯДОМ со scan_id — провенанс фото не теряется", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ ...WIRE, dish_name: "свекольник" }, 201),
    );

    await logMeal({
      scan_id: "scan-2098-1",
      dish_name: "свекольник",
      meal_type: "dinner",
      portion_multiplier: 1,
      idempotency_key: "k-2",
    });

    const body = JSON.parse(String(callAt(0)[1].body)) as Record<
      string,
      unknown
    >;
    expect(body.scan_id).toBe("scan-2098-1");
    expect(body.dish_name).toBe("свекольник");
  });
});

describe("stub-ветка мертва", () => {
  it("в модуле нет ни guardProd, ни fetchHealthFlags — только настоящие запросы (DRF-2106)", async () => {
    const source = (await import("./food-scanner.ts?raw")).default as string;
    // Положительно: это тот самый модуль — обе боевые ручки в нём названы.
    expect(source).toContain("/food/scan");
    expect(source).toContain("/food/log");
    expect(source).not.toContain("guardProd(");
    expect(source).not.toContain("function fetchHealthFlags");
    expect(source).not.toContain("function pickStubVariant");
  });
});
