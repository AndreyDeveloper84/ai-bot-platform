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
 *   - `GET    /api/v1/customer/wellness/today`  → `WellnessToday`   WIRED
 *   - `GET    /api/v1/customer/recent-activity` → `RecentActivity`  WIRED
 *   - `POST   /api/v1/customer/wellness/water`  → log a glass       WIRED
 *   - `DELETE /api/v1/customer/wellness/water/{entry_id}`           WIRED
 *
 * All four are live against `apps/miniapp_api`. The stub blobs below
 * are a dev-only `?stub=` QA hook and are unreachable in a production
 * build (`pickStubOrLive` returns `null` there unconditionally).
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
 *   - `?stub=empty`    — first-time user, anketa not done, no goal, no
 *                        booking, no weekly rollup
 *   - `?stub=partial`  — pfc missing, goal layer UNREACHABLE, no booking
 *                        (graceful degradation across Tier 2 §11.1 /
 *                        §11.2 / §11.5)
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
/**
 * Одна запись дневника питания, дословно как её отдаёт источник
 * (`nutrition/serializers.py::FoodLogEntrySerializer`).
 *
 * БЖУ приходит НА ЗАПИСЬ и настоящее. Клиент до 08.09.2026 считал его
 * сам — умножал калории на постоянные коэффициенты (0.075 / 0.018 /
 * 0.105) — и показывал человеку как факт о том, что тот съел. Это было
 * выдуманное число о человеке, а не выдуманная цель, и снято вместе с
 * подключением настоящих записей.
 *
 * `logged_at` — UTC. Расхождение суток разбирает DRF-1582; здесь оно не
 * решается и не воспроизводится.
 */
export interface FoodDiaryEntry {
  id: string;
  dish_name: string;
  calories: number;
  protein_g: number;
  fat_g: number;
  carbs_g: number;
  meal_type: string;
  logged_at: string;
  /**
   * §136 — чем получено число записи (`text_*` / `photo_*`), `null` для
   * старых. Экран дневника по нему решает, можно ли править граммы:
   * `граммы ÷ 100` верно только для записи текстом (DRF-1838).
   */
  entry_origin?: string | null;
}

export interface WellnessToday {
  /**
   * DRF-1927 — `true`, когда у человека нет согласия на обработку личных
   * данных: сервер дневник НЕ читал, и ключей дневника (калории, БЖУ,
   * записи, вода) в ответе нет не из-за сбоя. Экран вместо «Не удалось
   * загрузить» говорит {@link DIARY_CONSENT_REQUIRED_TEXT}. Цель
   * (`active_goals`) приходит как обычно.
   */
  consent_required?: boolean;
  /**
   * DRF-2071 — `true`, когда контур питания выключен (`NUTRITION_ENABLED=false`):
   * сервер дневник и воду НЕ читал, ключей дневника нет; имя и цель — есть,
   * они к дневнику не относятся. Та же форма, что у `consent_required`.
   */
  nutrition_disabled?: boolean;
  /**
   * Eaten today (kcal), and the target. `0` is a real value — «nothing
   * logged yet». **Both keys are ABSENT when the nutrition read failed**
   * (DRF-1546), which is a different thing entirely: the screen must
   * then say «Не удалось загрузить», not «0 / 0 ккал · 0 %». Same
   * contract as `active_goals` below — absence cannot be misread the
   * way a zero can.
   */
  calories_eaten?: number;
  /** User's target (kcal). Pulled from Layer 2 Goals or anketa. */
  calories_target?: number;
  /**
   * Macros breakdown. **MAY BE undefined / null** when the customer
   * has not completed the nutrition anketa (Tau §11.1). In that case
   * the БЖУ row is hidden entirely — NEVER rendered as «Б — · Ж — · У —».
   *
   * `protein_target_g` — DRF-1844 (F1): the profile's protein target,
   * derived from the §85 calories target; the backend sends it ONLY
   * under the same provenance flag as `calories_target` (confirmed
   * `ayla_calculated` / `user_entered`). Absent = no target — the БЖУ row
   * then shows the fact alone («Б 65 г»), never «Б 65 / 0 г». The
   * «Добрать белок» line removed in DRF-1546 is NOT brought back here.
   */
  pfc?: {
    protein_g: number;
    fat_g: number;
    carbs_g: number;
    protein_target_g?: number;
    /** DRF-2288 (№41): ориентиры жиров и углеводов — тем же признаком, каждый сам по себе. */
    fat_target_g?: number;
    carbs_target_g?: number;
  };
  /**
   * Стаканы за сегодня и дневная норма.
   *
   * `water_glasses_eaten` отсутствует, когда чтение воды упало (см.
   * `calories_eaten`). `water_glasses_target` отсутствует ЕЩЁ И тогда,
   * когда нормы у человека просто нет: анкету питания он не проходил, и
   * Ayla отвечает `norm_ml=0`. Раньше на это место бэкенд подставлял
   * константу «8», и человек видел чужое число как свою цель — теперь
   * ключа нет, и экран рисует выпитое без цели и без шкалы.
   */
  water_glasses_eaten?: number;
  water_glasses_target?: number;
  /**
   * Записи дневника за сегодня — ТРИ различимых состояния, и различие
   * несёт КЛЮЧ, а не длина списка:
   *
   * * ключа нет            → «прочитать не удалось». Экран говорит это
   *   словами, а не показывает пустой день;
   * * `[]`                 → «спросили, за день ничего не записано»;
   * * непустой список      → записи.
   *
   * Свести первые два — та же ложь, что «0 из 0 ккал» при отказе
   * чтения: человеку сообщают «ты сегодня ничего не ел» там, где
   * правда — «мы не смогли спросить».
   *
   * Поля приходят ДОСЛОВНО от источника
   * (`nutrition/serializers.py::FoodLogEntrySerializer`) и здесь не
   * переименовываются: одно поле — одно имя на всём проводе.
   */
  entries?: FoodDiaryEntry[];
  /**
   * Прятать ли числа (калории, БЖУ) — производный признак, а не
   * диагноз.
   *
   * Наружу приходит следствие, потому что клиенту нужно знать
   * «прятать ли цифру», а не «что с человеком»: здоровье — специальная
   * категория 152-ФЗ, и границу она пересекать не обязана.
   *
   * **Отсутствие ключа = fail-closed, числа ПРЯЧУТСЯ.** «Не смогли
   * спросить» не превращается в разрешение показать калории тому, кому
   * спека их показывать запрещает (§10 Appendix ED Mode). Цена названа
   * и принята: пока чтение профиля не работает, числа спрятаны у всех.
   */
  nutrition_numbers_hidden?: boolean;
  /**
   * Строка диетолога (DRF-1897) — тот же текст, что в дневнике в чате.
   *
   * Приходит ТОЛЬКО на `?surface=diary` (`loadDiaryToday`): сервер
   * решает её и пишет в журнал наблюдений лишь для открытого дневника.
   * Главная этот признак не ставит и строки не получает — иначе её
   * открытие тратило бы суточный слот наблюдения. Ключа нет — строки нет.
   */
  coach_observation?: string;
  /**
   * Active goals (cap=1 for MVP — multi-goal post-pilot). Read from
   * Ayla's goal layer — the same `known.goal` the goal screen renders
   * (`customer-goals.ts`), so the two surfaces cannot disagree.
   *
   * THREE states, and the difference between the last two matters
   * (DRF-1476):
   *
   *   - `[{...}]` — a goal is active → «Моя цель».
   *   - `[]`      — no goal chosen → «Выбери цель» per Tau §11.2.
   *   - `undefined` — the backend could NOT reach the goal layer. Not
   *     the same as «no goal»: rendering «Выбери цель» here is what
   *     told a customer who had just picked «Позаботиться о коже лица»
   *     to go pick one. Render a neutral label instead.
   *
   * There is NO progress field, by owner decision (решение №13,
   * 06.09): на пилоте разрешён простой показ «Моя цель» — без
   * процентов, шкал и оценок выполнения. Ayla и не хранит прогресс
   * (`ClientGoal` = key / text / selected_at / source_channel), так что
   * поле было бы нечем наполнить; теперь его нет и в контракте, и
   * нарисовать полосу не из чего.
   *
   * Одна цель, не несколько — тем же решением.
   *
   * `week_num` — производная от `selected_at` на сервере, отсутствует
   * только когда та отметка непригодна. Это счётчик недель, а не оценка
   * выполнения, и под запрет №13 не попадает.
   */
  active_goals?: Array<{
    title: string;
    week_num?: number;
    /**
     * DRF-2173 — срок цели: ISO-дата из `known.goal.target_date` каталога;
     * ключ опускается, когда срока нет (§103 — строки на карточке нет).
     * `target_date_passed` — факт сервера «срок прошёл» (на Главной строка
     * «До …» остаётся; «Срок прошёл — обновить?» живёт на экране цели).
     */
    target_date?: string;
    target_date_passed?: boolean;
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
    /**
     * Адрес салона — ТРИ состояния, и схлопывать их нельзя (DRF-1611):
     *
     * * строка   — адрес известен;
     * * `""`     — **салон сказал**, что адреса нет. Это ответ;
     * * `null`   — источник об адресе не сказал ничего. Это НАШ пробел.
     *
     * Ни `?? ""`, ни `|| ""` по дороге: они превратили бы молчание
     * источника в ответ салона, а разница видна человеку — при `""`
     * спрашивать некого, при `null` адрес скорее всего есть и его
     * стоит уточнить.
     */
    address: string | null;
    booking_id: string;
    /**
     * DRF-2144 — wire-статус той же строки (mirror: `confirmed` /
     * `awaiting_payment` / `pending_payment`; local: `confirmed`). Карточка
     * на Главной переводит его через `mapBookingStatus` — тем же словарём,
     * что список записей. Ключа нет (старый сервер) — бейджа нет.
     */
    status?: string;
    /**
     * Цена записи — МЕСТО ОСТАВЛЕНО, ждёт DRF-2172: каталог поля ещё не
     * отдаёт, сервер ключ не шлёт. Строка «3 200 ₽» — как на макете
     * DRF-1321 v1.2; отсутствие ключа = строки нет (§33: без источника не
     * рисуем). Формат — как `priceFromLabel` в каталоге.
     */
    price?: string | null;
  };
  /**
   * Count of CONFIRMED bookings in the current week. Drives the
   * «Ещё N записи на этой неделе» indicator per Tau §11.3 — only
   * shown when > 1.
   */
  this_week_booking_count: number;
  /**
   * 7-day rollup. **Optional** — the backend omits it entirely while
   * Ayla has no meals-list endpoint to build it from (DRF-1476). It
   * previously arrived as three hardcoded zeros, and only Block 6's
   * `>= 3` threshold kept that fiction off the screen.
   *
   * So Block 6 is gated on PRESENCE first, then on
   * `active_days_count >= 3` per Tau §11.4 cold-start. Absence cannot
   * be misread the way a zero can.
   */
  weekly_progress?: {
    water_days_logged: number;
    food_days_logged: number;
    active_days_count: number;
  };
}

// ---------------------------------------------------------------------------
// Production honesty rule (`lib/feature-flags.ts`): NOTHING fake in
// prod. A stub that shipped to a real customer would show them «Клиент»,
// 1240 kcal they never ate and a massage they never booked — so the
// stub blobs below are reachable ONLY from a dev build, and only when
// `?stub=` names a variant. The screen-level gate that used to hide
// this surface entirely came off with DRF-1546; this module never
// depended on it.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Stub variant picker — dev-only QA hook mirroring customer-booking.ts.
// ---------------------------------------------------------------------------

type StubVariant = "default" | "empty" | "partial";

/**
 * Which source a read should use: an explicit dev stub, or the live
 * endpoint. Returns `null` — «go to the backend» — in production ALWAYS,
 * and in dev unless `?stub=` names a variant.
 *
 * The production branch never reads the query string: doing so was a
 * phishing vector in the booking flow (customer-booking.ts PRE_MERGE
 * blocker #2), and that gate holds here too.
 */
function pickStubOrLive(): StubVariant | null {
  if (!import.meta.env.DEV) return null;
  if (typeof window === "undefined") return null;
  try {
    const v = new URLSearchParams(window.location.search).get("stub");
    if (v === "empty" || v === "partial" || v === "default") return v;
  } catch {
    /* SSR / parse failure — fall through to the live endpoint */
  }
  return null;
}

// ---------------------------------------------------------------------------
// Stub data — matches Tau §6 verbatim. Anti-pattern audited (no
// «рекомендуем» / «лучший» / «спонсировано» / «топ» / medical content).
// ---------------------------------------------------------------------------

// Стаб состояния «ориентир ЕСТЬ». Число здесь оставлено намеренно:
// §85 вернул шкалу и процент для режимов «Рассчитано Ayla» и
// «Установлено клиентом», и вёрстку этого состояния надо на чём-то
// развивать. Оно DEV-only (`pickStubOrLive` возвращает null вне DEV) и
// в боевой ответ не попадает. Холодный старт — `EMPTY_TODAY` ниже, и
// там ориентира нет, потому что сегодня его нет ни у кого.
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
  active_goals: [{ title: "Меньше стресса", week_num: 3 }],
  display_name: "Клиент",
  day_pattern_hint: "morning_good_progress",
};

const DEFAULT_ACTIVITY: RecentActivity = {
  next_booking: {
    date_human: "Завтра · пт · 16:00",
    service_name: "Массаж лимфодренаж",
    duration_min: 60,
    master_name: "Мастер",
    salon_name: "Формула тела",
    address: "ул. Тверская 12",
    booking_id: "booking-stub-001",
  },
  this_week_booking_count: 3,
  // Kept so Block 6 stays developable in dev; the live endpoint omits
  // this key until the meals layer ships (DRF-1476).
  weekly_progress: {
    water_days_logged: 4,
    food_days_logged: 5,
    active_days_count: 5,
  },
};

const EMPTY_TODAY: WellnessToday = {
  calories_eaten: 0,
  // `calories_target` ОПУЩЕН — по той же причине, по которой ниже
  // опущен `water_glasses_target`. Здесь стояло `2000`: та самая плоская
  // норма для всех, которую владелец удалил (§82), воспроизведённая в
  // стабе холодного старта. Стаб «подтверждал» константу вместо того,
  // чтобы её ловить, — и человек без анкеты в dev выглядел как человек
  // с целью 2000 ккал.
  // pfc undefined — anketa not done, БЖУ row hidden per §11.1
  water_glasses_eaten: 0,
  // water_glasses_target omitted — анкету не проходили, нормы нет.
  // Это и есть боевое состояние холодного старта: раньше здесь стояла
  // та же выдуманная восьмёрка, что и на бэкенде, и стаб «подтверждал»
  // константу вместо того, чтобы её ловить.
  active_goals: [],
  display_name: "Клиент",
  day_pattern_hint: "morning_no_logs",
};

const EMPTY_ACTIVITY: RecentActivity = {
  // next_booking omitted → empty-state CTA per Tau §5 State 2
  this_week_booking_count: 0,
  // weekly_progress omitted — mirrors the live endpoint, and exercises
  // the presence gate on Block 6 (DRF-1476).
};

const PARTIAL_TODAY: WellnessToday = {
  // calories_* omitted — the nutrition read failed. Exercises the
  // «Не удалось загрузить» row instead of «0 / 0 ккал» (DRF-1546).
  // pfc undefined — partial state exercises the conditional render path
  water_glasses_eaten: 2,
  // water_glasses_target omitted — тот же холодный старт.
  // active_goals omitted — the goal layer was unreachable. Exercises
  // the third state: neutral label, never «Выбери цель» (DRF-1476).
  display_name: "Клиент",
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
/**
 * Дневник за сегодня — три различимых состояния.
 *
 * Живёт здесь, а не в `food-scanner.ts`, потому что источник у него
 * тот же, что у дашборда: одна композитная ручка на обе поверхности.
 * Второе хранилище не заводится — его надо не «не заводить
 * специально», а просто не завести.
 *
 * * `unreachable` — ручка не ответила: наружу уходит исключение, экран
 *   рисует состояние ошибки с повтором;
 * * `unreadable` — ручка ответила, но БЕЗ ключа `entries`: питательная
 *   половина у сервера не прочиталась. Повтор осмыслен, сообщение
 *   другое, и «пустой день» показывать нельзя;
 * * `empty` / `entries` — ключ есть; пустой список означает ровно
 *   «за сегодня ничего не записано».
 *
 * `hideNumbers` читается ОДИНАКОВО в обеих непустых ветках, и
 * отсутствие ключа прячет числа (fail-closed).
 */
export type DiaryToday =
  | { state: "unreadable" }
  // DRF-1927 — нет согласия на обработку личных данных: сервер дневник не
  // читал. Не сбой (повтор ничего не даст) и не пустой день.
  | { state: "consent_required" }
  // DRF-2071 — контур питания выключен: сервер дневник не читал. Тоже не
  // сбой и не пустой день; входов в запись при этом не рисуется.
  | { state: "diary_off" }
  | { state: "empty"; hideNumbers: boolean; today: WellnessToday }
  | {
      state: "entries";
      entries: FoodDiaryEntry[];
      hideNumbers: boolean;
      today: WellnessToday;
    };

export async function loadDiaryToday(): Promise<DiaryToday> {
  // Явный признак «открыт именно дневник» (DRF-1897): по нему и только по
  // нему сервер решает строку диетолога и пишет журнал.
  const today = await getWellnessToday({ surface: "diary" });
  // Выключено важнее «согласия нет» — так отвечает и сервер.
  if (diaryIsOff(today)) return { state: "diary_off" };
  if (today.consent_required === true) return { state: "consent_required" };
  if (!Array.isArray(today.entries)) return { state: "unreadable" };
  const hideNumbers = today.nutrition_numbers_hidden !== false;
  // `today` едет целиком, а не разобранным на итоги: у его ключей уже
  // объявлены правила отсутствия (цели нет → ключа нет), и пересобрать
  // их здесь значило бы завести второй набор тех же правил.
  return today.entries.length === 0
    ? { state: "empty", hideNumbers, today }
    : { state: "entries", entries: today.entries, hideNumbers, today };
}

export async function getWellnessToday(
  /** `diary` — зовёт экран дневника; главная признак не передаёт. */
  options: { surface?: "diary" } = {},
): Promise<WellnessToday> {
  const variant = pickStubOrLive();
  if (variant === null) {
    return request<WellnessToday>(
      options.surface === "diary" ? "/wellness/today?surface=diary" : "/wellness/today",
    );
  }
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
  const variant = pickStubOrLive();
  if (variant === null) return request<RecentActivity>("/recent-activity");
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
  return enqueueWaterLogEntry(volume_ml).length;
}

/**
 * DRF-1919 — то же, но возвращает и сам стакан: вызывающий узнаёт СВОЙ стакан
 * в колбэках синхронизации по `key`, а не по «что-то приняли».
 */
export function enqueueWaterLogEntry(volume_ml = 250): { entry: QueuedWaterLog; length: number } {
  const queue = readWaterQueue();
  const ts = Date.now();
  const entry: QueuedWaterLog = { ts, volume_ml, key: mintQueueKey(ts) };
  queue.push(entry);
  writeWaterQueue(queue);
  return { entry, length: queue.length };
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
  /** Отсутствует, когда нормы у человека нет (Ayla шлёт norm_ml=0). */
  water_glasses_target?: number;
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
 * DRF-2230 — «Дать согласие в чате»: сервер шлёт в чат MAX приглашение с
 * кнопкой «Дать согласие». `sent: false` — либо приглашение уже отправлено
 * недавно (`recently_sent`, дубля нет — можно закрываться), либо согласие
 * уже есть (`already_granted` — закрываться незачем, данные перечитываются).
 */
export interface ConsentPromptResult {
  sent: boolean;
  reason?: "recently_sent" | "already_granted";
}

export function requestDiaryConsentPrompt(): Promise<ConsentPromptResult> {
  return request<ConsentPromptResult>("/wellness/consent-prompt", { method: "POST" });
}

/**
 * DRF-2230 (живой проход владельца 21.09) — ЧЕРНОВИКИ: повтор в окне дубля.
 * Приглашение уже лежит в чате; закрыть приложение молча значило «провалиться
 * в чат», не понимая, что там ждёт. Выход в чат — по явной кнопке.
 */
export const CONSENT_PROMPT_ALREADY_SENT_TEXT =
  "Приглашение уже в чате с Ayla — открой его и нажми «Дать согласие».";
export const CONSENT_PROMPT_OPEN_CHAT_CTA = "Открыть чат";

/** DRF-2230 — ЧЕРНОВИК: приглашение в чат не ушло; приложение не закрывается. */
export const CONSENT_PROMPT_FAILED_TEXT =
  "Не получилось отправить приглашение в чат. Попробуй ещё раз.";

/** DRF-1919 — одна фраза на «нет согласия» для дневника еды и воды. */
export const DIARY_CONSENT_REQUIRED_TEXT =
  "Чтобы менять дневник, нужно согласие на обработку личных данных — дай его в чате с Ayla.";

/**
 * DRF-2071 — контур питания выключен (`NUTRITION_ENABLED=false`): на ЧТЕНИЕ
 * `wellness/today` сервер отвечает 200 с `nutrition_disabled: true` и без
 * ключей дневника/воды (запись — 404 с тем же слагом). Это не сбой (повтор
 * ничего не даст) и не пустой день — экраны, читающие сводку, показывают эту
 * фразу и не рисуют кнопок записи. Литерал тот же, что у экранов
 * дня/недели/избранного (`*_COPY.diaryOff`).
 */
export const DIARY_OFF_TEXT = "Дневник питания пока недоступен.";

/** DRF-2071 — «контур выключен» узнаётся по маркеру сводки, не по статусу. */
export function diaryIsOff(today: WellnessToday | null | undefined): boolean {
  return today?.nutrition_disabled === true;
}

/**
 * DRF-1919 — что сказать, когда сервер навсегда отказал в стаканах. Они
 * выброшены из очереди, то есть НЕ записаны: фраза называет, сколько, откуда
 * (`fromQueue` — не только что нажатый, а ждавший в очереди) и почему.
 */
export function waterRefusalText(
  refused: readonly ApiError[],
  { fromQueue = false }: { fromQueue?: boolean } = {},
): string {
  const n = refused.length;
  const one = n === 1;
  const where = fromQueue ? " из очереди" : "";
  const head = one ? `Стакан${where} не записан` : `${n} ${glassesWord(n)}${where} не записаны`;
  const slugs = new Set(refused.map((e) => e.slug));
  if (slugs.has("consent_required")) return `${head}. ${DIARY_CONSENT_REQUIRED_TEXT}`;
  if (slugs.has("nutrition_disabled")) return `${head}: дневник воды сейчас выключен.`;
  return `${head} — дневник ${one ? "его" : "их"} не принял.`;
}

function glassesWord(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "стакан";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "стакана";
  return "стаканов";
}

/**
 * Undo a logged glass — the way back when the customer mis-tapped.
 *
 * Returns `true` when Ayla removed it, `false` when the restore window
 * has closed (404 `not_undoable`) and the glass therefore STAYS counted.
 * DRF-1919: a 404 `nutrition_disabled` (diary off) is not that — it throws. Anything else
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
    if (err instanceof ApiError && err.status === 404 && err.slug === "not_undoable") return false;
    throw err;
  }
}

/** DRF-1838 — ответ на удаление записи еды: окно, в котором её можно вернуть. */
export interface FoodEntryDeletion {
  entry_id: string;
  restore_window_expires_at: string | null;
}

/**
 * Убрать запись еды из дневника. Обратимо в окне восстановления каталога.
 * Любой отказ бросает `ApiError` — вызывающий называет его своей фразой.
 */
export async function deleteFoodEntry(entryId: string): Promise<FoodEntryDeletion> {
  return request<FoodEntryDeletion>(`/wellness/food/${encodeURIComponent(entryId)}`, {
    method: "DELETE",
  });
}

/**
 * Исход возврата удалённой записи — три РАЗНЫХ ответа, и сбой ни одним из них
 * не является: `expired` — окно закрылось, удаление окончательно; `gone` —
 * такой удалённой записи нет. Всё остальное бросает.
 */
export type FoodEntryRestore = "restored" | "expired" | "gone";

export async function restoreFoodEntry(entryId: string): Promise<FoodEntryRestore> {
  try {
    await request<unknown>(`/wellness/food/${encodeURIComponent(entryId)}/restore`, {
      method: "POST",
    });
    return "restored";
  } catch (err) {
    if (err instanceof ApiError && err.status === 410) return "expired";
    // «Записи нет» — только если так сказал сам сервер. 404 от прокси или от
    // сервера без этого маршрута (Mini App выложен раньше) — сбой, не исход.
    if (err instanceof ApiError && err.status === 404 && err.slug === "not_found") {
      return "gone";
    }
    throw err;
  }
}

/** Исправить граммы записи, сделанной текстом (`portion = граммы / 100` на сервере). */
export async function correctFoodEntryGrams(entryId: string, grams: number): Promise<void> {
  await request<unknown>(`/wellness/food/${encodeURIComponent(entryId)}`, {
    method: "PATCH",
    body: JSON.stringify({ grams }),
  });
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
  // DRF-1919: 401 — истекла сессия Mini App, а не отказ дневника: стакан
  // уйдёт после перезахода. 404 без slug сервера (прокси, сервер без этого
  // маршрута — `request` подставляет `http_error`) — тоже не отказ.
  if (err.status === 401) return false;
  if (err.status === 404 && err.slug === "http_error") return false;
  return err.status >= 400 && err.status < 500;
}

/**
 * DRF-1919 — исход ОДНОГО стакана: принят, отказан навсегда или остался в
 * очереди (с причиной — сеть, 5xx, истёкшая сессия).
 */
export type WaterEntryOutcome =
  | { kind: "accepted"; result: WaterLogResult }
  | { kind: "rejected"; err: ApiError }
  | { kind: "queued"; err: unknown };

/** Кто ждёт исхода какого стакана — по ключу стакана, а не по проходу. */
const entryWatchers = new Map<string, (outcome: WaterEntryOutcome) => void>();
/** Кому сказать об отказе стаканов, исхода которых никто не ждёт. */
const queueRefusalListeners = new Set<(refused: ApiError[]) => void>();

function notifyEntry(entry: QueuedWaterLog, outcome: WaterEntryOutcome): boolean {
  const key = idempotencyKeyFor(entry);
  const watcher = entryWatchers.get(key);
  if (!watcher) return false;
  entryWatchers.delete(key);
  try {
    watcher(outcome);
  } catch {
    /* исход для экрана — не часть синхронизации */
  }
  return true;
}

/**
 * DRF-1919 — подписка на отказы стаканов из очереди, которых никто не ждёт
 * (офлайн-стаканы, ушедшие при возврате сети или вместе с новым тапом): отказ
 * не должен пропадать молча. Возвращает отписку.
 */
export function onWaterQueueRefused(listener: (refused: ApiError[]) => void): () => void {
  queueRefusalListeners.add(listener);
  return () => {
    queueRefusalListeners.delete(listener);
  };
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
export async function flushWaterQueue(
  /** DRF-1842: id принятой записи — чтобы вызывающий мог предложить её отменить. */
  onAccepted?: (result: WaterLogResult, entry: QueuedWaterLog) => void,
  /** DRF-1919: постоянный отказ — стакан выброшен из очереди, экран обязан это сказать. */
  onRejected?: (err: ApiError, entry: QueuedWaterLog) => void,
): Promise<number> {
  // Исход СВОЕГО стакана вызывающий узнаёт через `syncWaterEntry`, а не через
  // колбэки прохода: присоединившийся к идущему проходу своих колбэков не имеет.
  if (flushInFlight) return flushInFlight;
  flushInFlight = (async () => {
    const queue = readWaterQueue();
    if (queue.length === 0) return 0;

    const remaining: QueuedWaterLog[] = [];
    const unwatchedRefusals: ApiError[] = [];
    let synced = 0;

    for (const [i, entry] of queue.entries()) {
      let accepted: WaterLogResult | null = null;
      try {
        accepted = await postWaterLog(entry);
        synced += 1;
      } catch (err) {
        if (isPermanentRejection(err)) {
          // eslint-disable-next-line no-console
          console.warn("[customer-wellness] water entry refused, dropping", err);
          if (err instanceof ApiError) {
            if (onRejected) {
              try {
                onRejected(err, entry);
              } catch {
                /* фраза об отказе — не часть синхронизации */
              }
            }
            if (!notifyEntry(entry, { kind: "rejected", err })) unwatchedRefusals.push(err);
          }
          continue;
        }
        // Retryable — this entry and every later one stay queued.
        for (const left of queue.slice(i)) notifyEntry(left, { kind: "queued", err });
        remaining.push(...queue.slice(i));
        break;
      }
      // Вне try: сбой подсказки в интерфейсе не должен превращать уже
      // принятый стакан в «повторить отправку» — это был бы дубль.
      if (accepted) notifyEntry(entry, { kind: "accepted", result: accepted });
      if (accepted && onAccepted) {
        try {
          onAccepted(accepted, entry);
        } catch {
          /* подсказка «отменить» — не часть синхронизации */
        }
      }
    }

    // Re-read before writing: a tap during the awaits appended to the
    // SAME storage key, and blind-writing `remaining` would erase it —
    // the very class of bug this function is being fixed for.
    const appended = readWaterQueue().filter(
      (e) => !queue.some((sent) => sent.ts === e.ts && sent.key === e.key),
    );
    writeWaterQueue([...remaining, ...appended]);
    if (unwatchedRefusals.length > 0) {
      for (const listener of queueRefusalListeners) {
        try {
          listener(unwatchedRefusals);
        } catch {
          /* фраза об отказе — не часть синхронизации */
        }
      }
    }
    return synced;
  })();
  try {
    return await flushInFlight;
  } finally {
    flushInFlight = null;
  }
}

/**
 * DRF-1919 — отправить стакан и узнать ЕГО исход, какой бы проход его ни взял.
 *
 * Идущий проход снял снимок очереди до этого стакана — дождаться его и
 * запустить проход, в котором стакан есть. Любой проход, взявший стакан,
 * называет его исход (принят / отказан / остался в очереди с причиной).
 */
export async function syncWaterEntry(entry: QueuedWaterLog): Promise<WaterEntryOutcome> {
  const key = idempotencyKeyFor(entry);
  let resolveOutcome: (outcome: WaterEntryOutcome) => void = () => {};
  const outcome = new Promise<WaterEntryOutcome>((resolve) => {
    resolveOutcome = resolve;
  });
  // Второй ожидающий того же стакана цепляется к первому — исход получают оба.
  const previous = entryWatchers.get(key);
  entryWatchers.set(
    key,
    previous
      ? (o) => {
          previous(o);
          resolveOutcome(o);
        }
      : resolveOutcome,
  );
  for (let attempt = 0; attempt < 3 && entryWatchers.has(key); attempt += 1) {
    await flushWaterQueue();
  }
  const pending = entryWatchers.get(key);
  if (pending) {
    // Стакан не попал ни в один проход (не сохранился в хранилище, очередь
    // очищена, TTL) — исход не известен; вызывающий проверит очередь сам.
    entryWatchers.delete(key);
    pending({ kind: "queued", err: null });
  }
  return outcome;
}

/** DRF-1919 — 401: стакан сохранён, но сам не уйдёт, пока приложение не открыть заново. */
export const WATER_SESSION_EXPIRED_TEXT =
  "Стакан сохранён, но не отправлен: сессия истекла — открой приложение заново.";

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
  /**
   * Доля выпитого от нормы, 0..1 — или `undefined`, когда НОРМЫ НЕТ.
   *
   * Раньше её место занимал ноль, и ноль означал сразу две разные вещи:
   * «сегодня ещё не пил» и «нормы у человека нет». Из второго нельзя
   * делать вывод «мало воды» — сравнивать не с чем.
   */
  waterRatio?: number;
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
    if (waterRatio !== undefined && waterRatio < 0.5)
      return "Хороший старт дня. Давай мягко доберём воду.";
    return "Хороший старт дня.";
  }
  if (hour >= 12 && hour < 18) {
    return hasNextBooking
      ? "Хорошо идёшь. Запись скоро — всё на месте."
      : "Хорошо идёшь. Продолжаем.";
  }
  if (hour >= 18 && hour < 22) {
    if (waterRatio !== undefined && waterRatio >= 0.75)
      return "Почти всё что хотели. Допей воду перед сном.";
    if (!hasAnyLogs) return "Тихий день. Если что-то нужно — расскажи.";
    return "Что нужно сегодня?";
  }
  return "Что нужно сегодня?";
}
