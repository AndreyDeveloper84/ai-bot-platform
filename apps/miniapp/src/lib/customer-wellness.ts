/**
 * Customer wellness dashboard stub lib — Tier 1 Priority 2 Phase B.
 *
 * Spec: `docs/screens/customer-main-wellness-dashboard.md` §3 Variant B
 * (Compact Hero v2) + §6 (Backend data needs) + memory
 * `project_variant_b_wellness_mvp` (founder pivot 2026-05-25 — wellness
 * dashboard becomes Главная for ACTIVE_REGULAR + no AI insight cards in
 * MVP per Q-BACK-4 / `project_cross_domain_insight_safety_gap`).
 *
 * # Contracts
 *
 *   - `GET    /api/v1/customer/wellness/today`  → `WellnessToday`   STUB
 *   - `GET    /api/v1/customer/recent-activity` → `RecentActivity`  STUB
 *   - `POST   /api/v1/customer/wellness/water`  → log a glass       WIRED
 *   - `DELETE /api/v1/customer/wellness/water/{entry_id}`           WIRED
 *
 * The two READS are still served from stubs here even though their
 * backends exist (`apps/miniapp_api/views.py::customer_wellness_today`
 * / `::customer_recent_activity`). Wiring them is a follow-up, not this
 * change. Until then they are fenced by `guardProd` — see below.
 *
 * The WRITE path is real (DRF-1402): `flushWaterQueue` posts to Ayla
 * through `apps/miniapp_api` and the offline queue drains ONLY on
 * accepted writes. It used to `writeWaterQueue([])` unconditionally —
 * the customer saw «+1 ждёт синхронизации», then the record vanished
 * and nothing had ever been sent.
 *
 * Stub variants for dev QA (matching the Phase B catalog stub pattern
 * in `customer-booking.ts::pickStubVariant`):
 *   - `?stub=default`  — happy path with pfc + booking + goal (default)
 *   - `?stub=empty`    — first-time user, anketa not done, no booking
 *   - `?stub=partial`  — pfc missing, no goal, no booking (graceful
 *                        degradation across Tier 2 §11.1 / §11.2 / §11.5)
 *
 * Variants are dev-only (gated on `import.meta.env.DEV`). Production
 * bundle ships only the `default` variant; the variant blobs for
 * `empty` + `partial` tree-shake out via Vite's static define
 * replacement of `import.meta.env.DEV` to `false`.
 *
 * # Voice + factual-only rule
 *
 * All copy strings rendered from this module's data MUST stay factual.
 * No «рекомендуем» / «лучший» / «спонсировано» / «топ» / «премиум»;
 * no medical content; no pressure messaging («ты пьёшь мало воды!» —
 * forbidden per memory `project_cross_domain_insight_safety_gap`).
 *
 * The `day_pattern_hint` field is consumed by the screen ONLY to drive
 * static template selection (see §11.10 implementation note in spec),
 * NEVER concatenated into LLM prompts.
 */

import { ApiError, request } from "./api";

// ---------------------------------------------------------------------------
// Contract types — verbatim per Tau §6 (Backend data needs).
// ---------------------------------------------------------------------------

/**
 * Pulse data + today's targets + greeting context. Shape matches the
 * future `GET /api/v1/customer/wellness/today` per W4.
 */
export interface WellnessToday {
  /** Eaten today (kcal). 0 when no logs. */
  calories_eaten: number;
  /** User's target (kcal). Pulled from Layer 2 Goals or anketa. */
  calories_target: number;
  /**
   * Macros breakdown. **MAY BE undefined / null** when the customer
   * has not completed the nutrition anketa (Tau §11.1). In that case
   * the БЖУ row is hidden entirely — NEVER rendered as «Б — · Ж — · У —».
   */
  pfc?: {
    protein_g: number;
    fat_g: number;
    carbs_g: number;
    protein_target_g?: number;
  };
  /** Glasses logged today. */
  water_glasses_eaten: number;
  /** Daily target. Defaults to 8 if anketa skipped. */
  water_glasses_target: number;
  /**
   * Active goals (cap=1 for MVP — multi-goal post-pilot).
   * Empty array when no goal chosen → quick action shows
   * «Выбери цель» per Tau §11.2.
   */
  active_goals: Array<{
    title: string;
    progress_pct: number;
    week_num: number;
  }>;
  /**
   * Optional preferred display name (Layer 1 Identity). Falls back to
   * `me.user.client_name` from `/auth/verify` when undefined.
   */
  display_name?: string;
  /**
   * Optional day-pattern hint from Layer 5 Behavioral. Drives static
   * template selection for the human one-liner per §11.10. NEVER fed
   * to an LLM — purely a template-key selector.
   *
   * Allowed values (Tier 2 — frontend treats unknown as fallback):
   *   - "morning_good_progress"
   *   - "morning_no_logs"
   *   - "midday_ahead"
   *   - "evening_close"
   *   - "evening_dropoff"
   *   - "fallback"
   */
  day_pattern_hint?: string;
}

/**
 * Recent activity surface — bookings + 7-day aggregate. Shape matches
 * the future `GET /api/v1/customer/recent-activity` per W4.
 */
export interface RecentActivity {
  /**
   * Next confirmed booking (status=CONFIRMED, visit_at >= now).
   * Omitted when no upcoming visit → Block 5 renders empty-state CTA
   * «Подобрать услугу под твою цель» per Tau §5 / §6.
   */
  next_booking?: {
    /** Pre-formatted human date («Завтра · пт · 16:00»). Server-rendered
     *  in the customer's TZ — frontend renders verbatim. */
    date_human: string;
    service_name: string;
    duration_min: number;
    master_name: string;
    salon_name: string;
    address: string;
    booking_id: string;
  };
  /**
   * Count of CONFIRMED bookings in the current week. Drives the
   * «Ещё N записи на этой неделе» indicator per Tau §11.3 — only
   * shown when > 1.
   */
  this_week_booking_count: number;
  /**
   * 7-day rollup. The screen's Block 6 (Прогресс недели) is gated on
   * `weekly_progress.active_days_count >= 3` per Tau §11.4 cold-start.
   */
  weekly_progress: {
    water_days_logged: number;
    food_days_logged: number;
    active_days_count: number;
  };
}

// ---------------------------------------------------------------------------
// Production honesty guard — same precedent as `customer-profile.ts:215`
// and `food-scanner.ts:180`.
//
// The pilot rule (`lib/feature-flags.ts:9-12`): NOTHING fake in prod. A
// stub that ships to a real customer would show them «Анна», 1240 kcal
// they never ate and a massage they never booked. Until the reads are
// wired, a prod-mode call must fail loudly → `StateError` renders.
//
// NOTE: the dashboard is ALSO hidden in prod by `STUB_SURFACES_ENABLED`
// (`CustomerWellnessDashboardScreen.tsx:109`). That gate is a screen-
// level decision that can be lifted at any time; this guard is a
// module-level one that must not depend on it. Two locks, one door.
// ---------------------------------------------------------------------------

class StubNotWiredError extends Error {
  constructor() {
    super(
      "Дашборд ещё не подключён. Загрузка временно недоступна. Попробуй позже.",
    );
    this.name = "StubNotWiredError";
  }
}

function guardProd(endpoint: string): void {
  if (!import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.error(
      `[customer-wellness] ${endpoint} called in production with no wire-up. ` +
        "See docs/screens/customer-main-wellness-dashboard.md §6.",
    );
    throw new StubNotWiredError();
  }
}

// ---------------------------------------------------------------------------
// Stub variant picker — dev-only QA hook mirroring customer-booking.ts.
// ---------------------------------------------------------------------------

type StubVariant = "default" | "empty" | "partial";

function pickStubVariant(): StubVariant {
  // Production: always `default`. Reading the `?stub=` query in prod
  // was a phishing vector in the booking flow (see customer-booking.ts
  // PRE_MERGE blocker #2) — apply the same gate here.
  if (!import.meta.env.DEV) return "default";
  if (typeof window === "undefined") return "default";
  try {
    const sp = new URLSearchParams(window.location.search);
    const v = sp.get("stub");
    if (v === "empty" || v === "partial") return v;
  } catch {
    /* SSR / parse failure — fall through */
  }
  return "default";
}

// ---------------------------------------------------------------------------
// Stub data — matches Tau §6 verbatim. Anti-pattern audited (no
// «рекомендуем» / «лучший» / «спонсировано» / «топ» / medical content).
// ---------------------------------------------------------------------------

const DEFAULT_TODAY: WellnessToday = {
  calories_eaten: 1240,
  calories_target: 2100,
  pfc: {
    protein_g: 65,
    fat_g: 40,
    carbs_g: 120,
    protein_target_g: 100,
  },
  water_glasses_eaten: 4,
  water_glasses_target: 8,
  active_goals: [
    { title: "Меньше стресса", progress_pct: 78, week_num: 3 },
  ],
  display_name: "Анна",
  day_pattern_hint: "morning_good_progress",
};

const DEFAULT_ACTIVITY: RecentActivity = {
  next_booking: {
    date_human: "Завтра · пт · 16:00",
    service_name: "Массаж лимфодренаж",
    duration_min: 60,
    master_name: "Ирина",
    salon_name: "Формула тела",
    address: "ул. Тверская 12",
    booking_id: "booking-stub-001",
  },
  this_week_booking_count: 3,
  weekly_progress: {
    water_days_logged: 4,
    food_days_logged: 5,
    active_days_count: 5,
  },
};

const EMPTY_TODAY: WellnessToday = {
  calories_eaten: 0,
  calories_target: 2000,
  // pfc undefined — anketa not done, БЖУ row hidden per §11.1
  water_glasses_eaten: 0,
  water_glasses_target: 8,
  active_goals: [],
  display_name: "Анна",
  day_pattern_hint: "morning_no_logs",
};

const EMPTY_ACTIVITY: RecentActivity = {
  // next_booking omitted → empty-state CTA per Tau §5 State 2
  this_week_booking_count: 0,
  weekly_progress: {
    water_days_logged: 0,
    food_days_logged: 0,
    active_days_count: 0,
  },
};

const PARTIAL_TODAY: WellnessToday = {
  calories_eaten: 800,
  calories_target: 2000,
  // pfc undefined — partial state exercises the conditional render path
  water_glasses_eaten: 2,
  water_glasses_target: 8,
  active_goals: [], // no goal — quick action shows «Выбери цель»
  display_name: "Анна",
  // No day_pattern_hint → falls back to «fallback» template
};

const PARTIAL_ACTIVITY: RecentActivity = {
  // next_booking omitted
  this_week_booking_count: 0,
  weekly_progress: {
    water_days_logged: 2, // < 3 → Block 6 hidden per §11.4
    food_days_logged: 1,
    active_days_count: 2,
  },
};

// Production bundle ships only the default stub. Empty + partial blobs
// alias to default — Vite tree-shakes the unused string literals.
const TODAY_STUB: Record<StubVariant, WellnessToday> = import.meta.env.DEV
  ? {
      default: DEFAULT_TODAY,
      empty: EMPTY_TODAY,
      partial: PARTIAL_TODAY,
    }
  : {
      default: DEFAULT_TODAY,
      empty: DEFAULT_TODAY,
      partial: DEFAULT_TODAY,
    };

const ACTIVITY_STUB: Record<StubVariant, RecentActivity> = import.meta.env.DEV
  ? {
      default: DEFAULT_ACTIVITY,
      empty: EMPTY_ACTIVITY,
      partial: PARTIAL_ACTIVITY,
    }
  : {
      default: DEFAULT_ACTIVITY,
      empty: DEFAULT_ACTIVITY,
      partial: DEFAULT_ACTIVITY,
    };

// ---------------------------------------------------------------------------
// Public functions
// ---------------------------------------------------------------------------

/**
 * Fetch today's pulse + targets + greeting context.
 *
 * STUB MODE — dev only. The backend
 * (`GET /api/v1/customer/wellness/today`) already composes this from
 * Ayla; swapping this body for `request("/wellness/today")` is the
 * follow-up. Signature does NOT change.
 */
export async function getWellnessToday(): Promise<WellnessToday> {
  guardProd("GET /api/v1/customer/wellness/today");
  const variant = pickStubVariant();
  // Simulate realistic network latency for skeleton testing (~300ms).
  await new Promise<void>((resolve) => setTimeout(resolve, 300));
  return TODAY_STUB[variant];
}

/**
 * Fetch next booking + this-week count + weekly progress aggregate.
 *
 * STUB MODE — dev only, same pattern as getWellnessToday. Backend
 * `GET /api/v1/customer/recent-activity` exists; wiring is the
 * follow-up.
 */
export async function getRecentActivity(): Promise<RecentActivity> {
  guardProd("GET /api/v1/customer/recent-activity");
  const variant = pickStubVariant();
  await new Promise<void>((resolve) => setTimeout(resolve, 350));
  return ACTIVITY_STUB[variant];
}

// ---------------------------------------------------------------------------
// Offline water log queue — localStorage 24h TTL per Tau §11.8.
// ---------------------------------------------------------------------------

const WATER_QUEUE_KEY = "max:wellness_water_offline_queue";
const WATER_QUEUE_TTL_MS = 24 * 60 * 60 * 1000; // 24h per spec

export interface QueuedWaterLog {
  /** ms epoch — the TAP time. Drives TTL pruning, ordering, AND the
   *  `ts` sent to Ayla so a late flush lands on the right day. */
  ts: number;
  /** ml — defaults to 250 per quick action spec. */
  volume_ml: number;
  /**
   * Idempotency key, minted once at enqueue and reused on every retry.
   * A retry after an ambiguous failure (request delivered, response
   * lost) must not double-count the glass.
   *
   * Optional for backward compatibility: entries already sitting in a
   * customer's localStorage from a previous build have no key. Those
   * fall back to a deterministic key derived from ts + volume.
   */
  key?: string;
}

/** Stable key for an entry, including pre-DRF-1402 queued ones. */
function idempotencyKeyFor(entry: QueuedWaterLog): string {
  return entry.key ?? `water-${entry.ts}-${entry.volume_ml}`;
}

function mintQueueKey(ts: number): string {
  // crypto.randomUUID is present in the MAX webview (recent Chromium)
  // but not in every test/SSR environment — degrade, never throw.
  const rnd =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2, 12);
  return `water-${ts}-${rnd}`;
}

/**
 * Append a water log to the localStorage queue. Used when offline (the
 * Block 3 «+ стакан 250 мл» quick action is allowed offline per spec).
 *
 * Returns the post-append queue size (for the «+N ждёт синхронизации»
 * indicator).
 */
export function enqueueWaterLog(volume_ml = 250): number {
  const queue = readWaterQueue();
  const ts = Date.now();
  queue.push({ ts, volume_ml, key: mintQueueKey(ts) });
  writeWaterQueue(queue);
  return queue.length;
}

/**
 * Read + auto-prune expired entries (> 24h old). Returns the pruned
 * queue — caller must NOT rely on the storage being identical to the
 * returned array since prune side-effects re-persist.
 */
export function readWaterQueue(): QueuedWaterLog[] {
  if (typeof window === "undefined" || !window.localStorage) return [];
  try {
    const raw = window.localStorage.getItem(WATER_QUEUE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      // Corrupt — clear + start fresh
      window.localStorage.removeItem(WATER_QUEUE_KEY);
      return [];
    }
    const now = Date.now();
    const fresh = parsed.filter(
      (entry: unknown): entry is QueuedWaterLog =>
        !!entry &&
        typeof entry === "object" &&
        typeof (entry as QueuedWaterLog).ts === "number" &&
        typeof (entry as QueuedWaterLog).volume_ml === "number" &&
        now - (entry as QueuedWaterLog).ts < WATER_QUEUE_TTL_MS,
    );
    // Persist the prune so the next read is fast.
    if (fresh.length !== parsed.length) {
      writeWaterQueue(fresh);
    }
    return fresh;
  } catch {
    return [];
  }
}

function writeWaterQueue(queue: QueuedWaterLog[]): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.setItem(WATER_QUEUE_KEY, JSON.stringify(queue));
  } catch {
    /* private mode / quota — best effort */
  }
}

// ---------------------------------------------------------------------------
// Water write path — POST / DELETE against Ayla via apps/miniapp_api.
// ---------------------------------------------------------------------------

/** Response of `POST /wellness/water` (see `views.py::customer_wellness_water`). */
export interface WaterLogResult {
  entry_id: string;
  ml: number;
  water_ml: number;
  today_total_ml: number;
  today_norm_ml: number;
  water_glasses_eaten: number;
  water_glasses_target: number;
}

/**
 * Post one water entry. `ts` is the TAP time (not now) — a queue
 * flushed after midnight must not move yesterday's glass into today.
 */
export async function postWaterLog(entry: QueuedWaterLog): Promise<WaterLogResult> {
  return request<WaterLogResult>("/wellness/water", {
    method: "POST",
    body: JSON.stringify({
      ml: entry.volume_ml,
      ts: new Date(entry.ts).toISOString(),
      idempotency_key: idempotencyKeyFor(entry),
    }),
  });
}

/**
 * Undo a logged glass — the way back when the customer mis-tapped.
 *
 * Returns `true` when Ayla removed it, `false` when the restore window
 * has closed (404) and the glass therefore STAYS counted. Anything else
 * (outage, 5xx) throws: a caller must never be told «removed» on the
 * strength of a failed request. That confusion is exactly the bug this
 * whole change exists to remove.
 */
export async function undoWaterLog(entryId: string): Promise<boolean> {
  try {
    await request<void>(`/wellness/water/${encodeURIComponent(entryId)}`, {
      method: "DELETE",
    });
    return true;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return false;
    throw err;
  }
}

/**
 * Is this failure worth retrying, or will the server refuse forever?
 *
 * A 4xx (bar 408/429) means the entry itself is unacceptable — keeping
 * it would poison the queue: every later flush would stop on it and the
 * glasses queued behind it would never reach Ayla. Anything else
 * (offline, timeout, 5xx, 502 from our own proxy) is transient.
 */
function isPermanentRejection(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false; // network / parse — retry
  if (err.status === 408 || err.status === 429) return false;
  return err.status >= 400 && err.status < 500;
}

/** Guards against two overlapping flushes double-posting the same entry. */
let flushInFlight: Promise<number> | null = null;

/**
 * Flush the offline queue to Ayla. Called on reconnect (online event
 * listener) and right after an online tap.
 *
 * Returns the number of entries Ayla accepted.
 *
 * ## The invariant
 *
 * An entry leaves the queue ONLY when the server accepted it (or
 * permanently refused it). This function used to clear the queue
 * unconditionally without sending anything — the customer's glass was
 * silently destroyed while the UI said «синхронизация».
 *
 * ## Partial success
 *
 * Entries are sent oldest-first. On the first RETRYABLE failure we stop
 * and keep that entry plus everything after it: if the network just
 * dropped, the remaining posts would fail anyway, and stopping
 * preserves chronological order for the next attempt. Entries already
 * accepted stay accepted — they are not re-sent. A PERMANENT rejection
 * drops just that one entry and the loop continues.
 */
export async function flushWaterQueue(): Promise<number> {
  if (flushInFlight) return flushInFlight;
  flushInFlight = (async () => {
    const queue = readWaterQueue();
    if (queue.length === 0) return 0;

    const remaining: QueuedWaterLog[] = [];
    let synced = 0;

    for (const [i, entry] of queue.entries()) {
      try {
        await postWaterLog(entry);
        synced += 1;
      } catch (err) {
        if (isPermanentRejection(err)) {
          // eslint-disable-next-line no-console
          console.warn("[customer-wellness] water entry refused, dropping", err);
          continue;
        }
        // Retryable — this entry and every later one stay queued.
        remaining.push(...queue.slice(i));
        break;
      }
    }

    // Re-read before writing: a tap during the awaits appended to the
    // SAME storage key, and blind-writing `remaining` would erase it —
    // the very class of bug this function is being fixed for.
    const appended = readWaterQueue().filter(
      (e) => !queue.some((sent) => sent.ts === e.ts && sent.key === e.key),
    );
    writeWaterQueue([...remaining, ...appended]);
    return synced;
  })();
  try {
    return await flushInFlight;
  } finally {
    flushInFlight = null;
  }
}

// ---------------------------------------------------------------------------
// Onboarding card dismiss state (localStorage flag) — §11.6.
// ---------------------------------------------------------------------------

const ONBOARDING_DISMISSED_KEY = "max:wellness_onboarding_dismissed_at";

export function isOnboardingDismissed(): boolean {
  if (typeof window === "undefined" || !window.localStorage) return false;
  try {
    return !!window.localStorage.getItem(ONBOARDING_DISMISSED_KEY);
  } catch {
    return false;
  }
}

export function markOnboardingDismissed(): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.setItem(
      ONBOARDING_DISMISSED_KEY,
      String(Date.now()),
    );
  } catch {
    /* best effort */
  }
}

// ---------------------------------------------------------------------------
// Static greeting + one-liner templates — Tau §11.9 + §11.10.
// All templates are STATIC, NEVER LLM-generated (per Q-TAU-4 + memory
// `project_cross_domain_insight_safety_gap`).
// ---------------------------------------------------------------------------

/**
 * Time-of-day-sensitive greeting per customer TZ. Falls back to local
 * device hour when no explicit TZ argument is passed.
 *
 * Buckets per Tau §11.9 verbatim:
 *   04:00–11:59 → «Доброе утро»
 *   12:00–17:59 → «Добрый день»
 *   18:00–21:59 → «Добрый вечер»
 *   22:00–03:59 → «Спокойной ночи»
 */
export function pickGreeting(now: Date = new Date()): string {
  const hour = now.getHours();
  if (hour >= 4 && hour < 12) return "Доброе утро";
  if (hour >= 12 && hour < 18) return "Добрый день";
  if (hour >= 18 && hour < 22) return "Добрый вечер";
  return "Спокойной ночи";
}

/**
 * Human one-liner — static template selection per Tau §11.10 + §7.
 *
 * Returned strings are VERBATIM Russian copy from the spec. The
 * selection is deterministic: same input → same output (no
 * randomness, no LLM). The screen renders the returned string as-is.
 *
 * Templates audited for anti-patterns: no pressure messaging, no
 * medical, no «рекомендуем» / «лучший» / etc.
 */
export function pickOneLiner(args: {
  hour: number;
  hint?: string;
  waterRatio: number; // 0..1
  hasAnyLogs: boolean;
  hasNextBooking: boolean;
}): string {
  const { hour, hint, waterRatio, hasAnyLogs, hasNextBooking } = args;
  // Explicit hint takes precedence (Layer 5 Behavioral pattern).
  if (hint === "morning_good_progress")
    return "Хороший старт дня. Давай мягко доберём воду.";
  if (hint === "morning_no_logs") return "Доброе утро. Начнём день?";
  if (hint === "midday_ahead")
    return hasNextBooking
      ? "Хорошо идёшь. Запись завтра — всё на месте."
      : "Хорошо идёшь. Продолжаем.";
  if (hint === "evening_close")
    return "Почти всё что хотели. Допей воду перед сном.";
  if (hint === "evening_dropoff")
    return "Тихий день. Если что-то нужно — расскажи.";
  if (hint === "fallback") return "Что нужно сегодня?";

  // Heuristic fallback when no hint provided.
  if (hour >= 4 && hour < 12) {
    if (!hasAnyLogs) return "Доброе утро. Начнём день?";
    if (waterRatio < 0.5) return "Хороший старт дня. Давай мягко доберём воду.";
    return "Хороший старт дня.";
  }
  if (hour >= 12 && hour < 18) {
    return hasNextBooking
      ? "Хорошо идёшь. Запись скоро — всё на месте."
      : "Хорошо идёшь. Продолжаем.";
  }
  if (hour >= 18 && hour < 22) {
    if (waterRatio >= 0.75) return "Почти всё что хотели. Допей воду перед сном.";
    if (!hasAnyLogs) return "Тихий день. Если что-то нужно — расскажи.";
    return "Что нужно сегодня?";
  }
  return "Что нужно сегодня?";
}
