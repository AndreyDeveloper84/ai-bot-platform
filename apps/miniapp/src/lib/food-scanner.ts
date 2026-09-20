/**
 * Customer food scanner lib — Tier 1 Priority 7 Phase B; scan/log — боевые с DRF-2098.
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` (Variant A — Wizard,
 * 4 screens with MAX BackButton navigation) + memory
 * `project_variant_b_wellness_mvp` (founder pivot 2026-05-25 — food
 * scanner is P0 BLOCKER for pilot 2026-07-15, без рабочего scanner
 * dashboard quick action «📸 Сфотографируй еду» нечем оперировать).
 *
 * # Contracts (canonical Ayla nutrition shape per TL handoff verbatim)
 *
 *   POST /api/v1/customer/food/scan      → ScanResponse (multipart)
 *   POST /api/v1/customer/food/log       → LogMealResponse
 *   GET  /api/v1/customer/food/daily     → DailySummaryResponse
 *
 * Mini App calls bot-platform `miniapp_api` proxy; the proxy talks to
 * Ayla `nutrition_client.{scan_photo,log_meal}`. Everything here is a
 * real request since DRF-2098 / DRF-2106 — the last stub
 * (`fetchHealthFlags`, behind `guardProd`) threw in the production
 * build and hid the numbers on the result card for everyone.
 *
 * # ED-mode rendering — critical voice rule
 *
 * The ED flag is the same `nutrition_numbers_hidden` the diary reads
 * (`wellness/today`, `customer-wellness.ts::getWellnessToday`),
 * fail-closed: an absent key HIDES the numbers (spec §10 Appendix ED
 * Mode). UI MUST hide all numeric nutrition values (calories, macros)
 * while it is not explicitly `false`. Render text only: «Примерно ·
 * 150 г · записала».
 *
 * # 152-ФЗ consent gate
 *
 * Per spec §2 — first scan requires explicit consent (accept/decline
 * sheet). Stored server-side in the consent registry as
 * `food_diary_processing` (DRF-1963) via `me/food-scanner-consent/`.
 *
 */

// ---------------------------------------------------------------------------
// Contract types — TL handoff verbatim. `beauty_insights` MAY be null
// — frontend must null-safe; UI never crashes.
// ---------------------------------------------------------------------------

import { ApiError, request } from "./api";

export interface NutritionFacts {
  calories: number;
  protein_g: number;
  fat_g: number;
  carbs_g: number;
  vitamins?: Record<string, number | string>;
}

export interface BeautyInsights {
  vitamin_deficits: string[];
  beauty_impact: string;
  recommendation: string;
}

export interface ScanResponse {
  scan_id: string;
  dish_name: string;
  /** 0–1; <0.6 → «Похоже на», ≥0.6 → «Узнала». */
  confidence: number;
  portion_g: number | null;
  nutrition: NutritionFacts | null;
  beauty_insights: BeautyInsights | null;
}

export type MealType = "breakfast" | "lunch" | "dinner" | "snack";

export interface LogMealResponse {
  log_id: string;
  dish_name: string;
  meal_type: MealType;
  calories: number;
}

export interface DailySummaryEntry {
  log_id: string;
  meal_type: MealType;
  dish_name: string;
  calories: number;
  portion_g?: number;
  logged_at_iso: string;
}

export interface DailySummaryResponse {
  date: string; // YYYY-MM-DD
  calories_total: number;
  /**
   * `calories_goal` СНЯТО. Ayla ключ больше не присылает: плоскую норму
   * 2000 ккал для всех владелец удалил 09.09.2026 (§82), а
   * версионированный расчёт (§85) — отдельный срез. Обязательное поле
   * здесь заставляло бы выдумать значение при любой попытке собрать
   * этот объект — что стаб ниже и делал, подставляя 2100.
   *
   * Когда ориентир появится, он придёт НЕОБЯЗАТЕЛЬНЫМ (`?:`), как
   * `calories_target` в `customer-wellness.ts`: экран обязан уметь
   * его отсутствие, а не полагаться на то, что число всегда есть.
   */
  protein_g: number;
  fat_g: number;
  carbs_g: number;
  entries: DailySummaryEntry[];
  ai_comment?: string;
}

// ---------------------------------------------------------------------------
// Error taxonomy — UI maps each to one of the §7 state screens.
// ---------------------------------------------------------------------------

export class FoodNotRecognizedError extends Error {
  constructor() {
    super("Фото немного сложное — не разобралась.");
    this.name = "FoodNotRecognizedError";
  }
}

export class NutritionUnavailableError extends Error {
  constructor() {
    super("Сервис распознавания временно недоступен.");
    this.name = "NutritionUnavailableError";
  }
}

export class PhotoBytesMissingError extends Error {
  constructor() {
    super("Фото пришло, но скачать не получилось.");
    this.name = "PhotoBytesMissingError";
  }
}

/** 413 `photo_too_large` — лимит один на бота (тот же, что у фото из чата). */
export class PhotoTooLargeError extends Error {
  constructor() {
    super("Фото слишком большое — попробуй сжать или снять ещё раз.");
    this.name = "PhotoTooLargeError";
  }
}

/**
 * DRF-2195 — штатные отказы каталога по бюджету распознавания. Ни один из
 * них не «временно недоступен»: каталог работает и отвечает осознанно.
 * Отдельные классы нужны ровно затем, чтобы экран мог сказать человеку
 * «сегодня» и увести писать словами вместо «попробуй через минуту».
 */
export class ScanDailyLimitError extends Error {
  constructor() {
    super("Сегодня фото больше не распознаю.");
    this.name = "ScanDailyLimitError";
  }
}

/** 503 `food_scan_budget_exhausted` — общий дневной бюджет распознавания. */
export class ScanBudgetExhaustedError extends Error {
  constructor() {
    super("Распознавание фото сейчас недоступно.");
    this.name = "ScanBudgetExhaustedError";
  }
}

/**
 * Legacy (DRF-2106): nothing in this module throws it any more — the last
 * stub is gone. The class stays exported because the Processing screen
 * still maps it to its «пока не подключено» state; that branch is dead
 * and can go with the screen's next edit.
 */
export class StubNotWiredError extends Error {
  constructor() {
    super("Распознавание еды по фото ещё не подключено.");
    this.name = "StubNotWiredError";
  }
}

// ---------------------------------------------------------------------------
// Фото-половина F8 (DRF-2098) — настоящий провод. Решение владельца 18.09
// (§48 п.4): «food-diary-v1 покрывает фото из Mini App» — отдельного
// согласия на фото нет, ворота у `POST /food/scan` те же, что у текста
// (`fetchDiaryConsentGate` спрашивают до снимка; 403 в полёте — тот же
// экран согласия). Stub-сторожа `guardProd` в модуле больше нет (DRF-2098/2106).
// ---------------------------------------------------------------------------

export interface ScanPhotoOptions {
  caption?: string;
  /**
   * AbortSignal from F2's `AbortController` — passed straight into
   * `fetch`, so «Отменить» aborts the upload in flight, not a timer.
   */
  signal?: AbortSignal;
}

/** Ответ прокси `POST /food/scan` (бот отдаёт подмножество каталога). */
interface ScanWire {
  scan_id: string;
  dish_name: string;
  confidence: number;
  portion_g: number | null;
  nutrition: NutritionFacts | null;
}

/**
 * Multipart `POST /food/scan`: поле `image` — сам файл. Ошибки бота
 * приводятся к таксономии §7: `food_not_recognized` → FoodNotRecognizedError,
 * `food_scan_daily_limit` (429) → ScanDailyLimitError,
 * `food_scan_budget_exhausted` (503) → ScanBudgetExhaustedError,
 * `nutrition_unavailable` (503) → NutritionUnavailableError, `photo_too_large`
 * (413) → PhotoTooLargeError. Отказы гейта (403/404) пробрасываются как
 * `ApiError` — Capture-экран ведёт на согласие по слагу.
 */
export async function scanPhoto(
  photo: File,
  opts?: ScanPhotoOptions,
): Promise<ScanResponse> {
  const form = new FormData();
  form.append("image", photo, photo.name || "meal.jpg");
  let wire: ScanWire;
  try {
    wire = await request<ScanWire>("/food/scan", {
      method: "POST",
      body: form,
      signal: opts?.signal,
    });
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.slug === "food_not_recognized")
        throw new FoodNotRecognizedError();
      // DRF-2195 — бюджет читается ДО `nutrition_unavailable`, как и на
      // стороне бота: у обоих отказов свои слаги, и 503 бюджета не должен
      // попасть в «сервис лёг».
      if (err.slug === "food_scan_daily_limit") throw new ScanDailyLimitError();
      if (err.slug === "food_scan_budget_exhausted")
        throw new ScanBudgetExhaustedError();
      if (err.slug === "nutrition_unavailable")
        throw new NutritionUnavailableError();
      if (err.slug === "photo_too_large") throw new PhotoTooLargeError();
    }
    throw err;
  }
  return {
    scan_id: wire.scan_id,
    dish_name: wire.dish_name,
    confidence: wire.confidence,
    portion_g: wire.portion_g,
    nutrition: wire.nutrition,
    // Каталог не отдаёт beauty-инсайты на этой ручке; экран рисует
    // блок только когда он есть.
    beauty_insights: null,
  };
}

/** Тело записи по скану — ветка `scan_id` в `POST /food/log` (DRF-2098). */
export interface LogMealRequest {
  /** Провенанс фото (§136 `photo_*`) — остаётся и при переименовании. */
  scan_id: string;
  /** Только когда человек переименовал блюдо на карточке. */
  dish_name?: string;
  meal_type: MealType;
  /** 1.0 default; portion ± buttons multiply this. */
  portion_multiplier: number;
  /** Ключ идемпотентности — от экрана; повтор после потерянного ответа не пишет вторую запись. */
  idempotency_key: string;
  /**
   * Заметка карточки. НЕ пересылается: у `log_meal` каталога нет такого
   * поля, чат её тоже не шлёт (предел, назван в DRF-2098).
   */
  note?: string;
}

interface LogMealWire {
  log_id: string;
  dish_name: string;
  meal_type: string;
  calories: number;
  entry_origin: string | null;
}

/**
 * `POST /food/log` с `scan_id`. Возвращает запись, как её записал каталог;
 * `meal_type` в ответе — как каталог её назвал (unnamed, если экран не
 * назвал приём).
 */
export async function logMeal(req: LogMealRequest): Promise<LogMealResponse> {
  const body: Record<string, unknown> = {
    scan_id: req.scan_id,
    meal_type: req.meal_type,
    portion_multiplier: req.portion_multiplier,
    idempotency_key: req.idempotency_key,
  };
  if (req.dish_name !== undefined) body.dish_name = req.dish_name;
  const wire = await request<LogMealWire>("/food/log", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return {
    log_id: wire.log_id,
    dish_name: wire.dish_name,
    meal_type: (wire.meal_type as MealType) ?? req.meal_type,
    calories: wire.calories,
  };
}

/*
 * `fetchDailySummary` УДАЛЕНА 08.09.2026 вместе с выдуманным числом.
 *
 * Она читала `localStorage` и вычисляла БЖУ из калорий постоянными
 * коэффициентами:
 *
 *     pTotal += Math.round(e.calories * 0.075);
 *     fTotal += Math.round(e.calories * 0.018);
 *     cTotal += Math.round(e.calories * 0.105);
 *
 * — и показывала это человеку как его белки, жиры и углеводы за день.
 *
 * До сих пор в этом контуре вычищали выдуманные НОРМЫ (§65): плоские
 * 2000 ккал, восемь стаканов. Норма — выдуманная мишень, она врёт про
 * то, к чему идти, и её можно оспорить. Здесь был выдуманный ФАКТ О
 * ЧЕЛОВЕКЕ — про то, что он уже съел; свой факт о себе человек
 * оспаривать не станет.
 *
 * Приближение было ещё и не нужно: настоящие `protein_g / fat_g /
 * carbs_g` приходят НА КАЖДУЮ ЗАПИСЬ от источника
 * (`nutrition/serializers.py::FoodLogEntrySerializer`).
 *
 * Снято тем же коммитом, которым подключены настоящие записи: до него
 * выдумку закрывал `guardProd`, и одно лишь подключение данных само
 * открыло бы ей дорогу к человеку.
 *
 * Настоящее чтение — `customer-wellness.ts::loadDiaryToday`.
 */

// ---------------------------------------------------------------------------
// Согласие на сканирование еды (152-ФЗ) — источник правды СЕРВЕР.
// ---------------------------------------------------------------------------
//
// Здесь стоял `localStorage`, и это был не «MVP-компромисс», а петля.
//
// Здесь когда-то стоял `localStorage`, потом — колонка
// `BotUser.food_scanner_consent_at`. С DRF-1963 (M1, владелец 15.09) согласие
// — строка единого реестра согласий `food_diary_processing`: с версией
// текста, источником и отзывом, который не стирает факт выдачи. Экран и гейт
// бота читают одну и ту же строку через `me/food-scanner-consent/`.

/**
 * Версия текста согласия, который показывает экран. КОПИЯ константы
 * `FOOD_DIARY_CONSENT_DOCUMENT_VERSION` из `apps/consent/nutrition.py` —
 * источник там, паритет держит тест `test_food_diary_disclosure.py`.
 * Сервер отвергает выдачу под версией, которой не знает (409).
 *
 * `food-diary-v1` — решение владельца 17.09: текст v1 = раскрытие Z9
 * (`food-diary-disclosure.ts`), с этого момента неизменяем; содержательное
 * изменение текста = `food-diary-v2` новой константой, без перезаписи v1.
 * `food-diary-v0` — черновой контракт, не используется.
 */
export const FOOD_DIARY_CONSENT_DOCUMENT_VERSION = "food-diary-v1";

/**
 * Прочитать согласие у СЕРВЕРА.
 *
 * `null` — согласия нет, и экран обязан спросить. Отсутствие ключа
 * читается так же: fail-closed, отсутствие доезжает отсутствием.
 */
// ---------------------------------------------------------------------------
// Запись еды ТЕКСТОМ — F8, текстовая половина (DRF-2091).
//
// Настоящий провод, не stub: `POST /food/estimate` (оценка без записи) и
// `POST /food/log` (запись по подтверждению) — те же ручки бота, что ведут
// в ту же тропу каталога, что и текст в чате (F2). Это боевые ручки, как и
// фото-половина (`scanPhoto`/`logMeal` выше) с DRF-2098: решение владельца
// D26 = «food-diary-v1 покрывает фото из Mini App».
// ---------------------------------------------------------------------------

export interface FoodTextEstimate {
  matched_dish: string;
  portion_g: number;
  /** true — граммов в тексте не было, порция — оценка; экран обязан сказать это словами. */
  portion_estimated: boolean;
  kcal: number;
  protein_g: number | null;
  fat_g: number | null;
  carbs_g: number | null;
}

export interface FoodTextLogResult {
  log_id: string;
  dish_name: string;
  calories: number;
  entry_origin: "text_estimated_confirmed" | "text_user_corrected" | string;
}

/** Оценка без записи. `portionG` — поправка граммов с карточки, сильнее числа в тексте. */
export async function estimateFoodText(
  text: string,
  portionG?: number,
): Promise<FoodTextEstimate> {
  const body: { text: string; portion_g?: number } = { text };
  if (portionG !== undefined) body.portion_g = portionG;
  return request<FoodTextEstimate>("/food/estimate", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * Запись — только по подтверждению показанной оценки. `corrected` решается
 * на карточке («Поправить граммы» → true), не задним числом: от него
 * зависит код происхождения записи (§136). Ключ идемпотентности минтится
 * экраном один раз на карточку и переживает повтор запроса.
 */
export async function logFoodText(req: {
  dish_name: string;
  portion_g: number;
  corrected: boolean;
  idempotency_key: string;
}): Promise<FoodTextLogResult> {
  return request<FoodTextLogResult>("/food/log", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function fetchConsentAt(): Promise<string | null> {
  const res = await request<{ granted?: boolean; granted_at?: string | null }>(
    "/me/food-scanner-consent/",
    { method: "GET" },
  );
  return res.granted ? (res.granted_at ?? null) : null;
}

/**
 * Дать согласие. Возвращает момент выдачи, записанный СЕРВЕРОМ.
 *
 * Момент берётся из ответа, а не из часов браузера: у гейта и у экрана
 * должно быть одно значение, а часы на устройстве человека могут
 * показывать что угодно. Версия текста уезжает в теле — без неё сервер
 * согласие не запишет.
 */
export async function grantConsent(): Promise<string | null> {
  const res = await request<{ granted_at?: string | null }>(
    "/me/food-scanner-consent/",
    {
      method: "POST",
      body: JSON.stringify({
        document_version: FOOD_DIARY_CONSENT_DOCUMENT_VERSION,
      }),
    },
  );
  return res.granted_at ?? null;
}

/**
 * Отозвать согласие. Отзыв доступен тем же способом, что и выдача, —
 * иначе это была бы новая строка «право на отзыв недостижимо из
 * приложения» (DRF-1520) в день закрытия старой.
 */
export async function withdrawConsent(): Promise<null> {
  await request("/me/food-scanner-consent/", { method: "DELETE" });
  return null;
}

// ---------------------------------------------------------------------------
// Каноническое раскрытие дневника питания — F10 / Z9 (DRF-2038).
//
// Согласие одно и ручка одна — `me/food-scanner-consent/` выше: строка
// реестра `food_diary_processing` покрывает дневник и текстом, и фотографией.
// Здесь НЕ второй путь согласия, а ответ на один вопрос экрана: КАКОЙ текст
// показывать — нынешний короткий или каноническое раскрытие Z9. Пока текст
// раскрытия имеет статус WORKING PRODUCT COPY, сервер объявляет канон только
// под флагом `FOOD_DIARY_CANONICAL_CONSENT`, и экран узнаёт об этом из `/me`.
// ---------------------------------------------------------------------------

/**
 * Что экрану нужно знать про согласие — одним вызовом.
 *
 * `canonical` — КАКОЙ текст показывать, а не «дано ли согласие». Отсутствие
 * поля в `/me` читается как старый путь: сборка, не знающая про канон, и
 * ответ без поля обязаны вести себя одинаково.
 */
export type DiaryConsentGate = {
  canonical: boolean;
  grantedAt: string | null;
  /** Версия текста, под которой согласие выдаётся, — та же для обоих текстов. */
  currentDocumentVersion: string;
};

/**
 * Прочитать, какой текст показывать и стоит ли согласие.
 *
 * Развилка живёт ЗДЕСЬ, а не в компоненте, ровно по одной причине: правило
 * «нет поля — старый путь» должно существовать в единственном месте.
 * Размазанное по экрану, оно разошлось бы с собой при первой же правке.
 *
 * Момент выдачи — из той же ручки, что и у гейта (`fetchConsentAt`): у
 * экрана и у гейта одно значение, а не два.
 */
export async function fetchDiaryConsentGate(): Promise<DiaryConsentGate> {
  const me = await request<{ food_diary_consent_canonical?: boolean }>("/me", {
    method: "GET",
  });
  const grantedAt = await fetchConsentAt();
  return {
    canonical: me.food_diary_consent_canonical === true,
    grantedAt,
    currentDocumentVersion: FOOD_DIARY_CONSENT_DOCUMENT_VERSION,
  };
}

// ---------------------------------------------------------------------------
// Pure helpers (also exported for smoke tests).
// ---------------------------------------------------------------------------

/**
 * Default meal type by local time bucket per spec §2 (Implementation
 * notes):
 *   04–11 → breakfast
 *   11–16 → lunch
 *   16–22 → dinner
 *   22–04 → snack
 */
export function defaultMealTypeForHour(hour: number): MealType {
  if (hour >= 4 && hour < 11) return "breakfast";
  if (hour >= 11 && hour < 16) return "lunch";
  if (hour >= 16 && hour < 22) return "dinner";
  return "snack";
}

export const MEAL_TYPE_LABEL: Record<MealType, string> = {
  breakfast: "Завтрак",
  lunch: "Обед",
  dinner: "Ужин",
  snack: "Перекус",
};

export const MEAL_TYPE_ICON: Record<MealType, string> = {
  breakfast: "🌅",
  lunch: "🥗",
  dinner: "🍽",
  snack: "🍎",
};

/**
 * Portion multiplier ladder — spec §4 (steps 0.25× range 0.5×–2.0×).
 * Display as percentage; underlying grams + calories recalc locally.
 */
export const PORTION_STEPS: ReadonlyArray<number> = [
  0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0,
];

export function nextPortion(current: number, direction: "up" | "down"): number {
  const idx = PORTION_STEPS.findIndex((s) => Math.abs(s - current) < 0.001);
  if (idx < 0) return 1.0;
  if (direction === "up") {
    const next = PORTION_STEPS[idx + 1];
    return next ?? current;
  }
  const prev = PORTION_STEPS[idx - 1];
  return prev ?? current;
}

/**
 * Strip EXIF metadata (incl. GPS) from a customer photo via canvas
 * re-encode. Defence-in-depth layer 1 for spec §2 privacy promise
 * («Фото нужно только чтобы узнать блюдо — удаляю сразу») — follow-up
 * #957 / adversarial CR A12. Without this, geo-tagged JPEGs ship GPS
 * coordinates to the backend BEFORE the delete-after-recognition
 * runs, leaking the customer's meal location to anyone reading the
 * in-flight payload.
 *
 * # Contract — fail-CLOSED, not fail-OPEN
 *
 * Throws `ImageStripUnsupportedError` if the file cannot be safely
 * stripped (HEIC on Safari, decode failure, encode failure, missing
 * canvas API). Callers MUST treat this as «refuse to upload» and
 * surface an error to the customer — NEVER silent-fallback to the
 * original file. iPhone shoots HEIC by default and Safari's
 * `createImageBitmap` rejects it; silently passing the HEIC through
 * would leak GPS exactly for the customers we care most about
 * (pilot 2026-07-15 Пенза = real iOS users).
 *
 * # Implementation
 *
 * `createImageBitmap(file, { imageOrientation: "from-image" })` honours
 * EXIF Orientation (per spec, otherwise portrait photos arrive rotated
 * 90° at the backend and break recognition — adversarial CR P2).
 * Draw to a 2D canvas + re-encode as JPEG at quality 0.92 (verified
 * acceptable by Ayla recognition, per pre-flight Q tolerance ≥0.85).
 * The canvas pipeline never preserves EXIF (HTML5 Canvas spec —
 * `toBlob` writes a fresh container), so output is metadata-clean
 * regardless of input.
 *
 * Server-side strip is a separate W4 hardening (true defence-in-depth);
 * this layer being mandatory removes the «defence-in-hope» smell the
 * adversarial CR flagged.
 */
export class ImageStripUnsupportedError extends Error {
  readonly reason:
    "no_browser_api" | "decode_failed" | "no_canvas_context" | "encode_failed";
  constructor(
    reason:
      | "no_browser_api"
      | "decode_failed"
      | "no_canvas_context"
      | "encode_failed",
  ) {
    super(`stripImageMetadata failed: ${reason}`);
    this.name = "ImageStripUnsupportedError";
    this.reason = reason;
  }
}

export async function stripImageMetadata(file: File): Promise<File> {
  if (typeof createImageBitmap === "undefined") {
    throw new ImageStripUnsupportedError("no_browser_api");
  }
  if (typeof document === "undefined") {
    throw new ImageStripUnsupportedError("no_browser_api");
  }
  let bitmap: ImageBitmap;
  try {
    // imageOrientation: "from-image" applies EXIF Orientation tag so
    // portrait iPhone photos arrive upright at the backend. Default
    // value is "none" which would mis-rotate. Supported Chromium ≥79,
    // Safari ≥15 — older Safari throws InvalidStateError which lands
    // in the catch below as "decode_failed" (correct fail-closed).
    bitmap = await createImageBitmap(file, {
      imageOrientation: "from-image",
    });
  } catch {
    // HEIC on Safari, corrupt JPEG, 0×0 image, SecurityError on exotic
    // camera intents — all land here. Refuse the upload.
    throw new ImageStripUnsupportedError("decode_failed");
  }
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    bitmap.close?.();
    throw new ImageStripUnsupportedError("no_canvas_context");
  }
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close?.();
  const blob = await new Promise<Blob | null>((resolve) => {
    // Quality 0.92 — backend Ayla recognition tolerance verified ≥0.85;
    // 0.92 leaves a safety margin without inflating bytes 2×.
    canvas.toBlob((b) => resolve(b), "image/jpeg", 0.92);
  });
  if (!blob) {
    // iOS Safari can return null under memory pressure on huge images.
    // Fail-closed: lose the photo, keep the privacy.
    throw new ImageStripUnsupportedError("encode_failed");
  }
  // Preserve filename so backend logs read the same; type forced to
  // image/jpeg (we just encoded it). lastModified intentionally NOT
  // preserved (soft time-of-meal leak per CR F4): use `Date.now()` so
  // the timestamp reflects upload moment, not original photo capture.
  return new File([blob], file.name, {
    type: "image/jpeg",
    lastModified: Date.now(),
  });
}

/**
 * Russian-pluralised «N приёмов» for the diary footer.
 */
export function entriesLabel(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  let word: string;
  if (mod10 === 1 && mod100 !== 11) word = "приём";
  else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14))
    word = "приёма";
  else word = "приёмов";
  return `${count} ${word}`;
}

/**
 * Format ISO timestamp into the friendly «14:32» time-only label used
 * in diary entry rows.
 */
export function formatTimeShort(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
