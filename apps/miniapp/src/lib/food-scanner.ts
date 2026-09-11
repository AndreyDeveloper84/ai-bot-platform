/**
 * Customer food scanner stub lib — Tier 1 Priority 7 Phase B.
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
 * Mini App calls bot-platform `miniapp_api` proxy (W4 ownership);
 * proxy talks to Ayla `nutrition_client.{scan_photo,log_meal,
 * daily_summary}`. Exact paths TBD by W4. Until W4 ships, frontend
 * stubs serve dev — production calls throw `StubNotWiredError`
 * (Profile precedent PR #954 M1 inline fix).
 *
 * # ED-mode rendering — critical voice rule
 *
 * If `MeResponse.health_flags.eating_disorder === true`, Ayla does
 * NOT return calorie numbers (spec §10 Appendix ED Mode). UI MUST
 * hide all numeric nutrition values (calories, macros). Render text
 * only: «Примерно · 150 г · записала». Portion ± buttons still
 * function locally but display no updated numbers.
 *
 * # 152-ФЗ consent gate
 *
 * Per spec §2 — first scan requires explicit consent (accept/decline
 * sheet). Persisted via DeviceStorage key
 * `food_scanner_consent_at` (ISO timestamp). Real backend persist
 * via `/me` field deferred to W4 follow-up.
 *
 * # Stub variants for dev QA
 *
 *   ?stub=default        — happy path (гречка с курицей, conf 0.85)
 *   ?stub=low_confidence — F3 low-conf branch (conf 0.42 → «Похоже на»)
 *   ?stub=not_recognized — FoodNotRecognizedError
 *   ?stub=api_down       — NutritionUnavailableError
 *   ?stub=photo_failed   — PhotoBytesMissingError
 *   ?stub=ed_mode        — eating_disorder=true (hides numbers)
 *
 * # Voice + factual-only rule (per spec §10)
 *
 *   - «примерно» / «похоже на» / «можно уточнить» / «записала» — OK
 *   - «вредно» / «много» / «слишком» — FORBIDDEN (medical/judgmental)
 *   - «~» literal as visual approximate signal
 *   - No gamification / streaks / badges (founder anti-pattern)
 */

// ---------------------------------------------------------------------------
// Contract types — TL handoff verbatim. `beauty_insights` MAY be null
// — frontend must null-safe; UI never crashes.
// ---------------------------------------------------------------------------

import { request } from "./api";

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

export interface LogMealRequest {
  scan_id?: string;
  dish_name?: string;
  meal_type: MealType;
  /** 1.0 default; portion ± buttons multiply this. */
  portion_multiplier: number;
  /** Optional free-text customer note. */
  note?: string;
}

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

/**
 * Stub MeResponse extension — health_flags exposure (Q2 blocker per
 * Phase A recon). Real shape lives in `apps/miniapp/src/lib/admin-api.ts
 * ::MeResponse`; this typed subset is what food scanner consumes.
 * Until W4 wires `health_flags` into the canonical /me payload, the
 * stub here drives the ED-mode toggle.
 */
export interface FoodHealthFlags {
  eating_disorder?: boolean;
  pregnancy?: boolean;
  breastfeeding?: boolean;
  diabetes?: boolean;
  hypertension?: boolean;
}

export interface MeHealthFlagsResponse {
  health_flags: FoodHealthFlags;
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

/**
 * Production guard — Profile PR #954 M1 precedent. If real W4 endpoint
 * is not wired, prod-mode calls throw → `StateError` renders. NEVER
 * ship fake recognition results / fake daily totals to a real customer.
 */
export class StubNotWiredError extends Error {
  constructor() {
    super("Распознавание еды по фото ещё не подключено.");
    this.name = "StubNotWiredError";
  }
}

function guardProd(endpoint: string): void {
  if (!import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.error(
      `[food-scanner] ${endpoint} called in production with no W4 wire-up. ` +
        "See docs/screens/customer-food-scanner-flow.md §13 + W4 follow-up issue.",
    );
    throw new StubNotWiredError();
  }
}

// ---------------------------------------------------------------------------
// Stub variant picker.
// ---------------------------------------------------------------------------

type StubVariant =
  | "default"
  | "low_confidence"
  | "not_recognized"
  | "api_down"
  | "photo_failed"
  | "ed_mode";

function pickStubVariant(): StubVariant {
  if (!import.meta.env.DEV) return "default";
  if (typeof window === "undefined") return "default";
  try {
    const sp = new URLSearchParams(window.location.search);
    const v = sp.get("stub");
    if (
      v === "low_confidence" ||
      v === "not_recognized" ||
      v === "api_down" ||
      v === "photo_failed" ||
      v === "ed_mode"
    ) {
      return v;
    }
  } catch {
    /* SSR / parse failure */
  }
  return "default";
}

// ---------------------------------------------------------------------------
// In-memory dev state (mirrors Profile pattern). Diary entries accumulate
// across `log_meal` calls in dev so QA can see them in the diary screen.
// ---------------------------------------------------------------------------

const SCAN_STUB: Record<StubVariant, ScanResponse> = {
  default: {
    scan_id: "scan-stub-001",
    dish_name: "Гречка с курицей",
    confidence: 0.85,
    portion_g: 150,
    nutrition: {
      calories: 480,
      protein_g: 35,
      fat_g: 8,
      carbs_g: 50,
      vitamins: { B6: 0.4, Fe: 3.2 },
    },
    beauty_insights: null,
  },
  low_confidence: {
    scan_id: "scan-stub-002",
    dish_name: "Гречка с курицей",
    confidence: 0.42,
    portion_g: 150,
    nutrition: {
      calories: 480,
      protein_g: 35,
      fat_g: 8,
      carbs_g: 50,
    },
    beauty_insights: null,
  },
  not_recognized: {
    scan_id: "",
    dish_name: "",
    confidence: 0,
    portion_g: null,
    nutrition: null,
    beauty_insights: null,
  },
  api_down: {
    scan_id: "",
    dish_name: "",
    confidence: 0,
    portion_g: null,
    nutrition: null,
    beauty_insights: null,
  },
  photo_failed: {
    scan_id: "",
    dish_name: "",
    confidence: 0,
    portion_g: null,
    nutrition: null,
    beauty_insights: null,
  },
  ed_mode: {
    scan_id: "scan-stub-ed",
    dish_name: "Гречка с курицей",
    confidence: 0.85,
    portion_g: 150,
    nutrition: null, // ED mode: Ayla returns no numbers
    beauty_insights: null,
  },
};

interface DiaryState {
  entries: DailySummaryEntry[];
}

const DIARY_STATE: { byDate: Map<string, DiaryState> } = {
  byDate: new Map(),
};

function todayKey(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function ensureDiaryDay(date: string): DiaryState {
  let state = DIARY_STATE.byDate.get(date);
  if (!state) {
    // Ориентира у стаба нет — ровно как у источника. Стояло
    // `calories_goal: 2100`: выдуманное число, «подтверждавшее»
    // константу вместо того, чтобы её ловить. Ровно так же здесь уже
    // стояла выдуманная восьмёрка стаканов.
    state = { entries: [] };
    DIARY_STATE.byDate.set(date, state);
  }
  return state;
}

function devWarn(msg: string): void {
  if (import.meta.env.DEV && typeof console !== "undefined") {
    // eslint-disable-next-line no-console
    console.warn(`[food-scanner stub] ${msg}`);
  }
}

// ---------------------------------------------------------------------------
// Fetch wrappers — stubs in DEV; throw in prod until W4 wires.
// ---------------------------------------------------------------------------

export interface ScanPhotoOptions {
  caption?: string;
  /**
   * AbortSignal plumbed from `AbortController` on F2 — friendly CR
   * follow-up. On stub this controls only the simulated-latency
   * setTimeout so QA can verify cancel UX; on swap-day W4 must wire
   * the signal into the real `fetch`/`httpx` request so an inflight
   * upload is cancelled when the customer taps «Отменить» or
   * navigates away.
   */
  signal?: AbortSignal;
}

export async function scanPhoto(
  _photo: File,
  opts?: ScanPhotoOptions,
): Promise<ScanResponse> {
  guardProd("POST /api/v1/customer/food/scan");
  devWarn("scanPhoto served from stub — W4 follow-up");
  const v = pickStubVariant();
  // Simulate network latency so the F2 loading card actually shows.
  // The signal aborts the wait early to mirror prod cancel behaviour.
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, 1200);
    if (opts?.signal) {
      const onAbort = () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      };
      if (opts.signal.aborted) {
        onAbort();
      } else {
        opts.signal.addEventListener("abort", onAbort, { once: true });
      }
    }
  });
  if (v === "not_recognized") throw new FoodNotRecognizedError();
  if (v === "api_down") throw new NutritionUnavailableError();
  if (v === "photo_failed") throw new PhotoBytesMissingError();
  return SCAN_STUB[v];
}

export async function logMeal(
  req: LogMealRequest,
): Promise<LogMealResponse> {
  guardProd("POST /api/v1/customer/food/log");
  devWarn("logMeal served from stub — W4 follow-up");
  const dishName = req.dish_name ?? "Запись";
  // Resolve calories from the most recent scan stub of the active
  // variant (so the diary reflects what the user just saw on F3).
  const v = pickStubVariant();
  const baseCalories =
    SCAN_STUB[v].nutrition?.calories ?? 0;
  const calories = Math.round(baseCalories * (req.portion_multiplier ?? 1));
  const logId = `log-${Date.now()}`;
  const day = ensureDiaryDay(todayKey());
  day.entries.push({
    log_id: logId,
    meal_type: req.meal_type,
    dish_name: dishName,
    calories,
    portion_g:
      SCAN_STUB[v].portion_g != null
        ? Math.round((SCAN_STUB[v].portion_g as number) * (req.portion_multiplier ?? 1))
        : undefined,
    logged_at_iso: new Date().toISOString(),
  });
  return { log_id: logId, dish_name: dishName, meal_type: req.meal_type, calories };
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

/**
 * Read the customer's health_flags. Production swap: read from
 * canonical `/api/v1/me` response once W4 adds the `health_flags`
 * field (P2 follow-up).
 */
export async function fetchHealthFlags(): Promise<MeHealthFlagsResponse> {
  guardProd("GET /api/v1/me (health_flags)");
  devWarn("health_flags served from stub — W4 follow-up");
  const v = pickStubVariant();
  return {
    health_flags: { eating_disorder: v === "ed_mode" },
  };
}

// ---------------------------------------------------------------------------
// Согласие на сканирование еды (152-ФЗ) — источник правды СЕРВЕР.
// ---------------------------------------------------------------------------
//
// Здесь стоял `localStorage`, и это был не «MVP-компромисс», а петля.
//
// Колонка `BotUser.food_scanner_consent_at` существует с миграции `0013`,
// и её читает гейт навыка (`apps/skills/food_scanner/skill.py:463`).
// Писателей у неё не было ни одного. Человек давал согласие в
// мини-приложении, экран его принимал и пропускал дальше — а бот на то же
// самое согласие отвечал «открой Mini App и дай согласие». Каждый раз. На
// новом устройстве всё начиналось заново, потому что согласие лежало в
// браузере предыдущего.
//
// Теперь согласие пишется ручкой `me/food-scanner-consent/` и читается
// вместе с профилем. `localStorage` авторитетом быть перестал и здесь не
// живёт вовсе: браузер на новом устройстве сказал бы «согласия нет» там,
// где база говорит «есть», и разошлись бы они молча.

/**
 * Прочитать согласие у СЕРВЕРА (приезжает вместе с профилем).
 *
 * `null` — согласия нет, и экран обязан спросить. Отсутствие ключа
 * читается так же: fail-closed, отсутствие доезжает отсутствием.
 */
export async function fetchConsentAt(): Promise<string | null> {
  const me = await request<{ food_scanner_consent_at?: string | null }>("/me", {
    method: "GET",
  });
  return me.food_scanner_consent_at ?? null;
}

/**
 * Дать согласие. Возвращает момент выдачи, записанный СЕРВЕРОМ.
 *
 * Момент берётся из ответа, а не из часов браузера: у гейта и у экрана
 * должно быть одно значение, а часы на устройстве человека могут
 * показывать что угодно.
 */
export async function grantConsent(): Promise<string | null> {
  const res = await request<{ granted_at?: string | null }>(
    "/me/food-scanner-consent/",
    { method: "POST" },
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

export function nextPortion(
  current: number,
  direction: "up" | "down",
): number {
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
    | "no_browser_api"
    | "decode_failed"
    | "no_canvas_context"
    | "encode_failed";
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
