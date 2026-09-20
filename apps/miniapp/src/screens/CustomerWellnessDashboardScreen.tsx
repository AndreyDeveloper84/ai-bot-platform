/**
 * Customer wellness dashboard — Tier 1 Priority 2 Phase B.
 *
 * Spec: `docs/screens/customer-main-wellness-dashboard.md` §3 Variant B
 * (Compact Hero v2) + §5 (5 states) + §6 (Backend data needs) + §7
 * (Brand) + §8 (WCAG 2.2 AA) + §11 (10 explicit impl notes).
 *
 * Memory refs:
 *   - `project_variant_b_wellness_mvp` — founder pivot 2026-05-25
 *   - `project_cross_domain_insight_safety_gap` — factual-only widgets,
 *     NO insight cards (Block 6 removed per Q-BACK-4)
 *   - `project_pilot_scope_discipline` — pilot-scope cuts
 *   - `project_ayla_personal_ai` — first-person Ayla voice, «ты»,
 *     lowercase «ayla» wordmark
 *
 * # Layout — H01 по макету DRF-1321 v1.2 (UX FREEZE 25.08), DRF-2144
 *
 * Решение владельца 20.09 (§55): строить по макету в рамках §49/§82 — без
 * веса, «−2,4 кг», процентов, графиков прогресса, «Добавить замер» и
 * «Самочувствие». Порядок сверху вниз:
 *
 *   Block 1 — Greeting + human one-liner (Tau §3 / §11.9 + §11.10)
 *   Block G — карточка активной цели: «Активная цель», название,
 *     «Неделя N · выполнено N из M действий» (adherence Plan Lite),
 *     primary «Продолжить сегодняшний план» / «Составить план»,
 *     вторичное «Посмотреть детали цели»; без цели — «Выбери цель»
 *   Block C — ОДИН блок согласия дневника (вместо двух абзацев)
 *   Block P — «План на сегодня» из Plan Lite (тот же источник, что у
 *     PlanLiteScreen); без плана блока нет
 *   Block 2 — Pulse strip (Питание + Вода) — Tau §3 + §11.1
 *   Block 4 — Шаги на сегодня (норма воды из анкеты; иной источник, чем план)
 *   Block 5 — Ближайшая запись: карточка (услуга, мастер, когда, адрес,
 *     статус, «Открыть запись», «Все мои записи»); нет — «Записей нет» +
 *     «Записаться»
 *   Block 3 — Быстрые действия по фризу: записать питание / стакан воды /
 *     новая запись / скорректировать план / профиль
 *   Block A — «Продолжить разговор с Ayla» с последней темой
 *     (`customer/last-topic/`); без темы — нейтрально
 *   Block 6 — Прогресс недели (cold-start ≥3 days) — Tau §3 + §11.4
 *   Block 7 — Recommendations embed (TL extension)
 *   Bottom nav — Главная · План · Дневник · Записи · Профиль (§55 б)
 *
 * Места, оставленные под чужие листы (условный рендер, ключа пока нет):
 * срок цели — DRF-2173 (заполнено), цена записи — DRF-2172 (заполнено).
 *
 * # Что снято с этого экрана и почему (DRF-1546)
 *
 * Правило владельца (§33 / DRF-1543): блок, за которым нет ручки, на
 * главную не выходит — не заглушкой и не «скоро», его просто нет.
 *
 *   - **Quick action «📸 Сфотографируй еду»** — ручек
 *     `/api/v1/customer/food/{scan,log,daily}` не существует (404 на
 *     боевом контуре), `food-scanner.ts::guardProd` вне DEV бросает.
 *     Кнопка вела на падающий экран. Маршруты `/customer/food-scanner/*`
 *     оставлены (§33: снимается вход, а не маршрут).
 *   - **Строка «🍽 Добрать белок · ещё N г»** (Block 4) — висела на
 *     `pfc.protein_target_g`, которого бэкенд не шлёт и источника под
 *     него не имеет. В бою не рендерилась никогда.
 *   - **Кнопка «Подробнее в Дне»** (Block 6) — поверхности «День» нет,
 *     вела на `/`, экран входа.
 *   - **Вкладка «День»** в нижней навигации — та же причина: её роль
 *     исполнял этот экран, а он теперь Главная.
 *
 * AI insight cards не строятся (spec §2: deferred post-pilot), прогресс
 * цели и мультицели — тоже (в модели Ayla их нет; борды обещают, канон
 * нет — §34).
 *
 * # Anti-pattern enforcement
 *
 * Anti-patterns FORBIDDEN in any rendered string from this screen:
 *   - «рекомендуем» / «лучший» / «спонсировано» / «премиум» / «топ»
 *   - «Ayla заметила…» — insight cards REMOVED per Q-BACK-4
 *   - «ты пьёшь мало воды!» — pressure messaging
 *   - Medical content of any kind
 *
 * All copy is verbatim-from-spec OR factual data labels. The human
 * one-liner is selected via STATIC template — see `pickOneLiner` in
 * `lib/customer-wellness.ts`. NEVER LLM-generated.
 *
 * # WCAG 2.2 AA (Tau §8 — 6 BLOCKERS addressed inline)
 *
 *   1. Target Size — every tap target ≥44×44dp via padding (CSS).
 *   2. Contrast Minimum — token colors used; brand sage NOT used for
 *      body text (which would fail 4.5:1).
 *   3. Non-text content — water dots / progress bars wrapped in
 *      `role="progressbar"` + aria-valuenow/max/label. Visual glyphs
 *      `aria-hidden="true"`.
 *   4. Info & Relationships — Pulse rows composite-labelled.
 *   5. Language of page + parts — Mini App ships `<html lang="ru">`;
 *      «ayla» wordmark wrapped `<span lang="en">Ayla</span>` per §8.5.
 *   6. Non-text Contrast — progress-bar track + borders use
 *      explicit verified tokens (handled in globals.css).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, type Service } from "../lib/api";
import { authErrorCopy, loadErrorReason, type LoadErrorReason } from "../lib/auth-error-copy";
import { mapBookingStatus } from "../lib/booking-status";
import { formatTopicWhen, getLastTopic, type LastTopic } from "../lib/customer-last-topic";
import { formatDuration, priceFromLabel } from "../lib/format";
import { closeApp } from "../lib/max-sdk";
import {
  getPlanLite,
  type PlanLite,
  type PlanLiteActionType,
  type PlanLiteCadence,
} from "../lib/plan-lite";
import { formatGoalDue } from "../lib/goal-deadline";
import { visitAddressText } from "../lib/visit-address";
import {
  enqueueWaterLog,
  enqueueWaterLogEntry,
  flushWaterQueue,
  onWaterQueueRefused,
  syncWaterEntry,
  WATER_SESSION_EXPIRED_TEXT,
  getRecentActivity,
  getWellnessToday,
  isOnboardingDismissed,
  markOnboardingDismissed,
  pickGreeting,
  pickOneLiner,
  readWaterQueue,
  undoWaterLog,
  waterRefusalText,
  type RecentActivity,
  type WellnessToday,
  DIARY_CONSENT_REQUIRED_TEXT,
  DIARY_OFF_TEXT,
  diaryIsOff,
} from "../lib/customer-wellness";
import {
  getCatalogBrowse,
  type CatalogBrowseData,
} from "../lib/customer-booking";
import { StatusBadge } from "../components/StatusBadge";
import { UnbookableBadge } from "../components/UnbookableNote";
import { useScreenBack } from "../hooks/useScreenBack";
import { PLAN_LITE_COPY, PLAN_LITE_ROUTE } from "./PlanLiteScreen";
import { screenRoot } from "../lib/screen-back";

/** Одна цель из `wellness/today.active_goals` (cap=1, решение №13). */
type ActiveGoal = NonNullable<WellnessToday["active_goals"]>[number];

/**
 * Согласие дневника — ОДИН блок на экране (DRF-2144 п.6). Формулировка из
 * листа; кнопка закрывает Mini App в чат MAX — согласие даётся там.
 */
export const DIARY_CONSENT_CARD_TEXT = "Чтобы вести дневник, нужно согласие — дай его в чате с Ayla";
export const DIARY_CONSENT_CARD_CTA = "Дать согласие в чате";

/**
 * Нижняя панель — ровно пять вкладок по макету H01 (решение владельца §55 б).
 * `route: null` — эта вкладка и есть текущий экран. Порядок и подписи —
 * договор со сторожем (h01-тест) и с макетом; менять их — новое решение.
 */
export const HOME_TABS: ReadonlyArray<{ label: string; icon: string; route: string | null }> = [
  { label: "Главная", icon: "🏠", route: null },
  { label: "План", icon: "📋", route: PLAN_LITE_ROUTE },
  { label: "Дневник", icon: "📔", route: "/customer/food-scanner/diary" },
  { label: "Записи", icon: "📅", route: "/customer/records" },
  { label: "Профиль", icon: "👤", route: "/customer/profile" },
];

// ---------------------------------------------------------------------------
// Loading + error state model — per-block isolation for «Partial» state
// (Tau §5 State 5). Each remote slice carries its own status so a
// failed bookings call doesn't blank the pulse strip.
// ---------------------------------------------------------------------------

type Slice<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T }
  | { kind: "error"; reason: LoadErrorReason };

/**
 * План — три состояния: читаем; прочитан (`null` = плана нет); недоступен
 * (сервер выключил Plan Lite — 404 `plan_lite_disabled` — или не ответил).
 * «Недоступен» и «плана нет» — разные факты: во втором случае карточка цели
 * зовёт составить план, в первом о плане молчит.
 */
type PlanSlice = { kind: "loading" } | { kind: "ok"; data: PlanLite | null } | { kind: "unavailable" };

function isOnline(): boolean {
  if (typeof navigator === "undefined") return true;
  return navigator.onLine !== false;
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

export function CustomerWellnessDashboardScreen() {
  // Гейт снят (DRF-1546, решение владельца §24.2 + §34). Оба условия
  // владельца выполнены: настоящие цели подключены (DRF-1476),
  // `weekly_progress` бэкенд опускает, а не шлёт нулями. Экран стоял
  // закрытым только потому, что его никто не открыл.
  const navigate = useNavigate();

  // Вид экрана (DRF-1493): корень. «Главная» — точка входа клиентской
  // поверхности: у экрана своя нижняя навигация, и стрелка «назад»
  // здесь означала бы родителя, которого нет.
  useScreenBack(
    screenRoot(
      "«Главная» — корневая вкладка клиентской поверхности со своей " +
        "нижней навигацией; выше неё ничего нет.",
    ),
  );

  const [today, setToday] = useState<Slice<WellnessToday>>({ kind: "loading" });
  const [activity, setActivity] = useState<Slice<RecentActivity>>({
    kind: "loading",
  });
  const [recs, setRecs] = useState<Slice<CatalogBrowseData>>({
    kind: "loading",
  });
  const [planSlice, setPlanSlice] = useState<PlanSlice>({ kind: "loading" });
  // Тема — `null` и «ручка упала» читаются одинаково: блок нейтральный.
  const [lastTopic, setLastTopic] = useState<LastTopic | null>(null);

  const [online, setOnline] = useState<boolean>(isOnline());
  const [waterQueueLen, setWaterQueueLen] = useState<number>(
    () => readWaterQueue().length,
  );
  const [waterToast, setWaterToast] = useState<string | null>(null);
  // DRF-1919: отказ стаканов из очереди, которых этот экран не ждал, — не молча.
  // Тап, пришедший следом, допишет эту фразу к своей, а не затрёт её.
  const queueRefusalNote = useRef<string | null>(null);
  useEffect(
    () =>
      onWaterQueueRefused((refused) => {
        const note = waterRefusalText(refused, { fromQueue: true });
        queueRefusalNote.current = note;
        setWaterToast(note);
      }),
    [],
  );
  // DRF-1842 — id последнего принятого стакана, пока его можно отменить.
  // Ручка отмены (`DELETE /wellness/water/{id}`, `undoWaterLog`) была, а
  // кнопки не было ни одной: ошибочный тап оставался в дневнике навсегда.
  const [undoEntryId, setUndoEntryId] = useState<string | null>(null);
  const [onboardingDismissed, setOnboardingDismissed] = useState<boolean>(
    () => isOnboardingDismissed(),
  );

  // ── data fetch ────────────────────────────────────────────────────────
  const fetchAll = useCallback(async () => {
    setToday({ kind: "loading" });
    setActivity({ kind: "loading" });
    setRecs({ kind: "loading" });
    setPlanSlice({ kind: "loading" });

    // Per-slice isolation — Promise.allSettled so one failure doesn't
    // blank the other blocks. (Tau §5 State 5 partial render.)
    const [todayRes, activityRes, recsRes, planRes, topicRes] = await Promise.allSettled([
      getWellnessToday(),
      getRecentActivity(),
      getCatalogBrowse(),
      getPlanLite(),
      getLastTopic(),
    ]);

    if (todayRes.status === "fulfilled") {
      setToday({ kind: "ok", data: todayRes.value });
    } else {
      setToday({ kind: "error", reason: loadErrorReason(todayRes.reason) });
    }

    if (activityRes.status === "fulfilled") {
      setActivity({ kind: "ok", data: activityRes.value });
    } else {
      setActivity({ kind: "error", reason: loadErrorReason(activityRes.reason) });
    }

    if (recsRes.status === "fulfilled") {
      setRecs({ kind: "ok", data: recsRes.value });
    } else {
      // Recommendations errors hide the whole block silently per spec.
      setRecs({ kind: "error", reason: loadErrorReason(recsRes.reason) });
    }

    // План: выключен на сервере (`plan_lite_disabled`) или не ответил —
    // «недоступен», без ошибки на экране: Главная от плана не зависит.
    setPlanSlice(
      planRes.status === "fulfilled" ? { kind: "ok", data: planRes.value } : { kind: "unavailable" },
    );
    setLastTopic(topicRes.status === "fulfilled" ? topicRes.value : null);
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  // ── online / offline transitions ─────────────────────────────────────
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onOnline = () => {
      setOnline(true);
      // Auto-flush water queue on reconnect (Tau §11.8).
      // Отказы стаканов из очереди скажет подписка `onWaterQueueRefused` (DRF-1919).
      void flushWaterQueue().then(() => {
        setWaterQueueLen(readWaterQueue().length);
      });
    };
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  // Auto-clear transient toasts after 3s — 8s while «Отменить» is on offer:
  // за три секунды кнопку не успеть нажать.
  useEffect(() => {
    if (!waterToast) return;
    const t = setTimeout(
      () => {
        setWaterToast(null);
        setUndoEntryId(null);
        queueRefusalNote.current = null;
      },
      undoEntryId ? 8000 : 3000,
    );
    return () => clearTimeout(t);
  }, [waterToast, undoEntryId]);
  // ── quick-action handlers ────────────────────────────────────────────
  //
  // Быстрого действия «📸 Сфотографируй еду» здесь БОЛЬШЕ НЕТ
  // (DRF-1546, тот же признак, что §33 / DRF-1543). За ним не было
  // ручки: `/api/v1/customer/food/{scan,log,daily}` отвечают 404 на
  // боевом контуре, а `food-scanner.ts::guardProd` вне DEV бросает
  // `StubNotWiredError` — то есть кнопка вела на падающий экран.
  // Маршруты `/customer/food-scanner/*` намеренно оставлены (§33:
  // снимается вход, а не маршрут); вернуть кнопку — одна строка, когда
  // ручки появятся. Настоящий дневник питания живёт в боте.

  const onWaterTap = useCallback(() => {
    // §11.8 — offline: queue to localStorage 24h TTL. Real POST is
    // wired by W4; STUB MODE simulates instant accept when online.
    setUndoEntryId(null);
    if (!online) {
      queueRefusalNote.current = null;
      const len = enqueueWaterLog(250);
      setWaterQueueLen(len);
      setWaterToast(
        `+1 стакан · ${len} ${ruPluralWater(len)} ${ruPluralWaterWaits(len)} синхронизации`,
      );
      return;
    }
    // Online — also enqueue + immediately flush. This keeps the queue
    // as a single durable code path while STUB doesn't have a real
    // endpoint. Once W4 ships, the online branch will skip enqueue.
    // DRF-1919: «зачтён» и «Отменить» — только про СВОЙ стакан этого тапа
    // (сверка по key): в одну синхронизацию уходят и стаканы, ждавшие в
    // очереди. Отказ в них не затирается принятием нового; свой стакан, не
    // дошедший по сети, — «ждёт синхронизации».
    const { entry: own } = enqueueWaterLogEntry(250);
    void syncWaterEntry(own).then((outcome) => {
      const len = readWaterQueue().length;
      setWaterQueueLen(len);
      let ownText: string;
      if (outcome.kind === "accepted") {
        setUndoEntryId(outcome.result.entry_id);
        ownText = "+1 стакан зачтён";
      } else {
        // «Отменить» могла остаться от стакана другого тапа — рядом с этим
        // тостом она была бы про чужой стакан.
        setUndoEntryId(null);
        if (outcome.kind === "rejected") {
          ownText = waterRefusalText([outcome.err]);
        } else if (outcome.err instanceof ApiError && outcome.err.status === 401) {
          ownText = WATER_SESSION_EXPIRED_TEXT;
        } else if (!readWaterQueue().some((e) => e.key === own.key)) {
          // Стакана нет ни в очереди, ни в исходе: хранилище его не сохранило.
          // «Ждёт синхронизации» было бы обещанием, которое нечем выполнить.
          ownText = "Стакан не сохранён — попробуй ещё раз.";
        } else {
          ownText = `+1 стакан · ${len} ${ruPluralWater(len)} ${ruPluralWaterWaits(len)} синхронизации`;
        }
      }
      const note = queueRefusalNote.current;
      queueRefusalNote.current = null;
      setWaterToast(joinSentences(note ? [ownText, note] : [ownText]));
    });
  }, [online]);

  const onUndoWater = useCallback(() => {
    // Фраза об отказах из очереди уже показана — к следующему тосту не приклеивать.
    queueRefusalNote.current = null;
    const id = undoEntryId;
    if (!id) return;
    setUndoEntryId(null);
    void undoWaterLog(id).then(
      (removed) => {
        // `false` — окно отмены закрылось: стакан ОСТАЛСЯ, и сказать
        // «убран» было бы той самой ложью, ради которой `undoWaterLog`
        // различает 404 и сбой.
        setWaterToast(
          removed
            ? "Стакан убран"
            : "Уже не убрать: окно отмены закрылось, стакан остался в дневнике",
        );
        if (removed) void fetchAll();
      },
      (err: unknown) => {
        // DRF-1919: дневник выключен — повтор не поможет, кнопку не возвращаем.
        if (err instanceof ApiError && err.slug === "nutrition_disabled") {
          setWaterToast("Дневник воды сейчас выключен — убрать стакан не получилось.");
          return;
        }
        // Сбой сети/сервера — ничего не удалено; кнопку возвращаем.
        setUndoEntryId(id);
        setWaterToast("Не получилось убрать — попробуй ещё раз");
      },
    );
  }, [undoEntryId, fetchAll]);

  const onGoalTap = useCallback(() => {
    // §11.2 — context-aware label; the destination is the real goal
    // surface (`GoalSelectScreen`, DRF-1190), which reads
    // `/customer/decision-context` and writes `/customer/goals/select`.
    // It used to `navigate("/")`, i.e. the entry screen: someone who
    // already had a goal tapped «Моя цель» and landed on a greeting.
    navigate("/customer/goal-select");
  }, [navigate]);

  const onCatalogTap = useCallback(() => {
    navigate("/customer/catalog");
  }, [navigate]);

  // DRF-2101/2144 — Plan Lite: экран плана. Флага сборки больше нет —
  // включён ли план, скажет сервер (карточка цели уже прочитала ответ).
  const onPlanTap = useCallback(() => {
    navigate(PLAN_LITE_ROUTE);
  }, [navigate]);

  // Вход в дневник живёт во вкладке «Дневник» (DRF-1839 → DRF-2144);
  // быстрое действие «Записать питание» ведёт сразу к вводу текстом.
  const onFoodTap = useCallback(() => {
    navigate("/customer/food-scanner/manual");
  }, [navigate]);

  const onProfileTap = useCallback(() => {
    navigate("/customer/profile");
  }, [navigate]);

  // В чат MAX — закрыть Mini App: приложение открыто из диалога с Ayla, и
  // закрытие возвращает туда. Фразу («скорректировать план», согласие,
  // новый вопрос) человек пишет сам: deep link с текстом в MAX не
  // существует (см. историю в StateError.tsx), и никакого контекста экран
  // в чат не передаёт — обещать это было бы ложью.
  const onChatTap = useCallback(() => {
    closeApp();
  }, []);

  const onDismissOnboarding = useCallback(() => {
    markOnboardingDismissed();
    setOnboardingDismissed(true);
  }, []);

  // ── derived state ────────────────────────────────────────────────────
  const todayData = today.kind === "ok" ? today.data : null;
  // DRF-2071 — контур питания выключен: сводка пришла с маркером и без
  // ключей дневника (как `consent_required`), имя и цель — на месте. Не
  // сбой (повтор ничего не даст) и не пустой день; входы в дневник (стакан,
  // «Дневник питания») не рисуются — иначе рядом с «недоступен» стояло бы
  // «Добавить».
  const diaryOff = diaryIsOff(todayData);
  const activityData = activity.kind === "ok" ? activity.data : null;
  const recsData = recs.kind === "ok" ? recs.data : null;
  // Block 7 picks — top-3 services by Ayla scorer rank, joined onto the
  // mirror services. Owner ruling 25.08: «Нет displayable WHY → нет
  // блока „Ayla подобрала"» — `getCatalogBrowse` already dropped every
  // pick the source did not explain, so this list is empty until the
  // scorer sends WHY, and the branded section below hides itself.
  const picksWithWhy: { service: Service; reasons: string[] }[] = (() => {
    if (!recsData) return [];
    const byId = new Map(recsData.services.map((s) => [s.id, s]));
    return recsData.picks
      .map((pick) => ({ service: byId.get(pick.serviceId), reasons: pick.reasons }))
      .filter(
        (p): p is { service: Service; reasons: string[] } => p.service != null,
      )
      .slice(0, 3);
  })();

  const now = new Date();
  const greeting = pickGreeting(now);
  const displayName = todayData?.display_name ?? "";
  // Absent ≠ zero (DRF-1546). A failed nutrition or hydration read omits
  // its keys, and every derived number below has to treat that as
  // «unknown» rather than folding it into «nothing logged».
  const caloriesEaten = todayData?.calories_eaten;
  // Съеденное и цель — РАЗНЫЕ факты, ровно как у воды ниже. Цель Ayla
  // отдаёт только тем, кто прошёл анкету питания, и «цели нет» обязано
  // читаться как «цели нет», а не как «не удалось загрузить»: иначе
  // человек вместо своих калорий видит НЕДОСТУПНО.
  //
  // Здесь это ещё и меняло поведение карточки первого шага ниже
  // (``showOnboarding``): она требует ЗНАНИЯ о пустом дне, а знание
  // подменялось наличием цели — то есть карточка «Начнём с малого?»
  // не показалась бы ровно тому, для кого она написана: человеку без
  // анкеты.
  const caloriesKnown = caloriesEaten !== undefined;
  const waterEaten = todayData?.water_glasses_eaten;
  const waterTarget = todayData?.water_glasses_target;
  // Выпитое и цель — РАЗНЫЕ факты, и знать их можно порознь. Норму воды
  // Ayla отдаёт только тем, кто прошёл анкету питания; раньше на её
  // месте стояла константа «8 стаканов», и человек видел чужое число
  // как свою дневную цель. Теперь цели просто нет — а выпитое остаётся.
  const waterKnown = waterEaten !== undefined;
  const waterTargetKnown = waterTarget !== undefined;
  const waterRatio =
    waterTargetKnown && waterTarget > 0 && waterEaten !== undefined
      ? waterEaten / waterTarget
      : undefined;
  const hasAnyLogs =
    !!todayData && ((caloriesEaten ?? 0) > 0 || (waterEaten ?? 0) > 0);
  const oneLiner = todayData
    ? pickOneLiner({
        hour: now.getHours(),
        hint: todayData.day_pattern_hint,
        waterRatio,
        hasAnyLogs,
        hasNextBooking: !!activityData?.next_booking,
      })
    : "";

  // Onboarding card visible iff: data loaded, no logs anywhere, no
  // bookings, no dismiss flag (§11.6). Auto-hide after first action is
  // handled implicitly: enqueueWaterLog drives waterQueueLen > 0 →
  // hasAnyLogs becomes true on next data refresh, OR user taps dismiss.
  const showOnboarding =
    !onboardingDismissed &&
    todayData !== null &&
    // Only when we actually KNOW the day is empty — an outage is not a
    // first-time user, and «Начнём с малого?» is wrong for both.
    caloriesKnown &&
    waterKnown &&
    !hasAnyLogs &&
    !activityData?.next_booking;

  // Block 6 gate — PRESENCE first, then the cold-start threshold
  // (§11.4). The backend omits `weekly_progress` while it has no real
  // source for it, so «absent» must hide the block on its own and not
  // lean on `>= 3` to do it (DRF-1476).
  const weeklyProgress = activityData?.weekly_progress;
  const showWeekly = !!weeklyProgress && weeklyProgress.active_days_count >= 3;

  // Goal CTA is TRI-state (DRF-1476). `active_goals` absent means the
  // backend could not reach the goal layer — that is not «no goal», and
  // saying «Выбери цель» to someone who has one is the defect this
  // fixes. `todayData === null` (still loading) is likewise unknown.
  const goalsSlice = todayData?.active_goals;
  const goalsKnown = goalsSlice !== undefined;
  // Одна цель, не несколько (решение владельца №13, 06.09).
  const goal: ActiveGoal | undefined = goalsSlice?.[0];
  // DRF-1927 — ключей дневника нет, потому что нет согласия, а не потому,
  // что чтение упало: один блок согласия вместо строк «Питание»/«Вода».
  const consentRequired = todayData?.consent_required === true;
  const topicWhen = useMemo(() => (lastTopic ? formatTopicWhen(lastTopic.at) : ""), [lastTopic]);

  // Block 4 actionable targets visibility.
  //
  // Строка «🍽 Добрать белок · ещё N г» СНЯТА (DRF-1546): она висела на
  // `pfc.protein_target_g`, которого тогда бэкенд не слал. С DRF-1844 ключ
  // приходит (под признаком происхождения), но строка НЕ возвращается:
  // «добрать» — оценка, §85 §8 разрешает при ориентире шкалу и процент, а
  // не призыв; ориентир по белку виден в самой строке БЖУ («Б 108 / 130 г»).
  // «Ещё N стаканов до нормы» бывает только когда норма есть.
  //
  // «До нормы», не «до цели»: решение владельца 11.09.2026 §5.2 — Goal это
  // желаемый результат, Habit — повторяющееся действие внутри плана,
  // PlanStep — конкретное действие; привычка или действие не создают
  // отдельную Goal. Стакан воды — шаг, норма воды — его мера. Назвать её
  // целью значило бы завести человеку вторую цель, которой он не ставил.
  const waterRemaining =
    waterKnown && waterTargetKnown ? Math.max(0, waterTarget - waterEaten) : 0;
  const showTodayGoals = waterRemaining > 0;

  // ── render ────────────────────────────────────────────────────────────
  return (
    <div className="wellness-dash" lang="ru">
      {/* Skip link — WCAG 2.4.1 (Tau §8 IMPORTANT #7). */}
      <a className="wellness-dash__skip-link" href="#wellness-main">
        К основному содержимому
      </a>

      {/* Header — 56dp. Иконки «Профиль»/«Настройки» сняты (DRF-2144): обе
          вели в профиль, а профиль теперь — вкладка панели. Вордмарк и
          «спросить» — прежние; на макете H01 в шапке имя и колокольчик
          (Д1/Д2 в списке отступлений PR). */}
      <header className="wellness-dash__header" role="banner">
        <div className="wellness-dash__brand">
          {/* «ayla» = English wordmark per Tau §7 — wrap in lang="en"
              to keep RU TTS from pronouncing it «Айла» (WCAG 3.1.2). */}
          <span className="wellness-dash__wordmark" lang="en">
            ayla
          </span>
          <span aria-hidden="true"> ✨</span>
        </div>
        <button
          type="button"
          className="wellness-dash__ask"
          aria-label="Спросить у ayla"
          onClick={() => navigate("/")}
        >
          спросить
        </button>
      </header>

      {/* Offline banner — §5 State 4. */}
      {!online && (
        <div
          className="wellness-dash__offline-banner"
          role="status"
          aria-live="polite"
        >
          <span aria-hidden="true">⚡</span> Без сети — показываю последние
          данные
        </div>
      )}

      <main id="wellness-main" className="wellness-dash__main">
        {/* Block 1 — Greeting + human one-liner (§11.9 + §11.10). */}
        <section className="wellness-dash__greeting" aria-label="Приветствие">
          {today.kind === "loading" ? (
            <>
              <div
                className="skeleton wellness-dash__skel-line"
                style={{ width: "60%", height: 24 }}
                aria-hidden="true"
              />
              <div
                className="skeleton wellness-dash__skel-line"
                style={{
                  width: "85%",
                  height: 16,
                  marginTop: "var(--s-2)",
                }}
                aria-hidden="true"
              />
            </>
          ) : (
            <>
              <h1 className="wellness-dash__greeting-title">
                {greeting}
                {displayName ? `, ${displayName}` : ""}{" "}
                <span aria-hidden="true">🌿</span>
              </h1>
              {oneLiner && (
                <p className="wellness-dash__one-liner">{oneLiner}</p>
              )}
            </>
          )}
        </section>

        {/* Onboarding card — first-time empty state (§5 State 2 + §11.6). */}
        {showOnboarding && (
          <section
            className="wellness-dash__onboarding"
            aria-label="Подсказка для начала"
          >
            <div className="wellness-dash__onboarding-body">
              <p className="wellness-dash__onboarding-title">Начнём с малого?</p>
              {/* DRF-2091: копия честная — путь текстом есть, фото (D26) —
                  ещё нет; обещать «сфотографируй» без ручки нельзя. */}
              <p className="wellness-dash__onboarding-text">
                Запиши первый приём текстом, добавь воду или выбери цель.
              </p>
              <button
                type="button"
                className="btn-secondary wellness-dash__onboarding-cta"
                onClick={onFoodTap}
              >
                Записать текстом
              </button>
              <button
                type="button"
                className="wellness-dash__onboarding-dismiss"
                aria-label="Скрыть подсказку"
                onClick={onDismissOnboarding}
              >
                <span aria-hidden="true">×</span>
              </button>
            </div>
          </section>
        )}

        {/* Block G — карточка активной цели (макет H01, верх). Цель — из
            `wellness/today.active_goals` (три состояния, DRF-1476), план —
            из `plan-lite` (тот же источник, что у PlanLiteScreen). */}
        {today.kind === "ok" && (
          <GoalCard
            goal={goal}
            goalsKnown={goalsKnown}
            plan={planSlice}
            onPlan={onPlanTap}
            onGoal={onGoalTap}
          />
        )}

        {/* Block C — согласие дневника: ОДИН блок (DRF-2144 п.6) вместо двух
            одинаковых абзацев в строках «Питание» и «Вода». */}
        {today.kind === "ok" && consentRequired && (
          <section
            className="wellness-dash__consent"
            aria-label="Согласие на дневник"
          >
            <p className="wellness-dash__consent-text">{DIARY_CONSENT_CARD_TEXT}</p>
            <button type="button" className="btn-secondary" onClick={onChatTap}>
              {DIARY_CONSENT_CARD_CTA}
            </button>
          </section>
        )}

        {/* Block P — «План на сегодня»: действия активного плана; без плана
            блока нет (DRF-2144 п.2). */}
        {planSlice.kind === "ok" && planSlice.data && (
          <PlanToday plan={planSlice.data} onAll={onPlanTap} onAdjust={onChatTap} />
        )}

        {/* Block 2 — Pulse strip (§11.1 — conditional БЖУ). Без согласия
            строк нет — их место занимает одна карточка согласия выше. */}
        {!(today.kind === "ok" && consentRequired) && (
          <section className="wellness-dash__pulse" aria-label="Сегодня">
            {today.kind === "loading" && <PulseSkeleton />}
            {today.kind === "error" && (
              <BlockError
                reason={today.reason}
                onRetry={() => void fetchAll()}
              />
            )}
            {today.kind === "ok" && diaryOff && (
              <div className="wellness-dash__block-error" role="status" aria-live="polite">
                <p>{DIARY_OFF_TEXT}</p>
              </div>
            )}
            {today.kind === "ok" && !diaryOff && (
              <PulseStrip data={today.data} />
            )}
          </section>
        )}

        {/* Block 4 — Шаги на сегодня (text actions). */}
        {showTodayGoals && today.kind === "ok" && (
          <section
            className="wellness-dash__today-goals"
            aria-labelledby="tg-header"
          >
            <h2 id="tg-header" className="wellness-dash__section-header">
              Шаги на сегодня
            </h2>
            <ul className="wellness-dash__goal-list">
              {waterRemaining > 0 && (
                <li className="wellness-dash__goal-item">
                  <span aria-hidden="true">💧</span>{" "}
                  <span>
                    Ещё {waterRemaining}{" "}
                    {ruPluralWater(waterRemaining)} до нормы
                  </span>
                </li>
              )}
            </ul>
          </section>
        )}

        {/* Block 5 — Ближайшая запись (карточка по макету H01, DRF-2144 п.3;
            источник — `recent-activity`, как и раньше). */}
        <section
          className="wellness-dash__booking"
          aria-labelledby="booking-header"
        >
          <h2 id="booking-header" className="wellness-dash__section-header">
            Ближайшая запись
          </h2>
          {activity.kind === "loading" && <BookingSkeleton />}
          {activity.kind === "error" && (
            <BlockError
              reason={activity.reason}
              onRetry={() => void fetchAll()}
            />
          )}
          {activity.kind === "ok" && !activity.data.next_booking && (
            <BookingEmpty onBook={onCatalogTap} />
          )}
          {activity.kind === "ok" && activity.data.next_booking && (
            <BookingCard
              data={activity.data}
              onOpen={() =>
                activity.data.next_booking &&
                navigate(
                  `/customer/records/${activity.data.next_booking.booking_id}`,
                )
              }
              onAll={() => navigate("/customer/records")}
            />
          )}
        </section>

        {/* Block 3 — Быстрые действия по фризу 25.08 (DRF-2144 п.4):
            записать питание / стакан воды / новая запись / скорректировать
            план / профиль. «Добавить замер» из макета → «стакан воды» (§49:
            без веса). «Найди услугу» и «Моя цель» ушли: цель — в карточке,
            услуги — по «Записаться» в каталог. Без контура питания (DRF-2071)
            дневниковые кнопки не рисуются. */}
        <section
          className="wellness-dash__quick-actions"
          aria-labelledby="qa-header"
        >
          <h2 id="qa-header" className="wellness-dash__section-header">
            Быстрые действия
          </h2>
          <div className="wellness-dash__qa-grid wellness-dash__qa-grid--row">
            {!diaryOff && (
              <button
                type="button"
                className="wellness-dash__qa-btn wellness-dash__qa-btn--compact"
                aria-label="Записать питание"
                onClick={onFoodTap}
              >
                <span className="wellness-dash__qa-icon" aria-hidden="true">
                  🍽
                </span>
                <span className="wellness-dash__qa-label">Записать питание</span>
              </button>
            )}
            {!diaryOff && (
              <button
                type="button"
                className="wellness-dash__qa-btn wellness-dash__qa-btn--compact"
                // Видимая подпись — по макету «Стакан воды»; имя для скринридера
                // несёт глагол: тап сразу пишет 250 мл без подтверждения.
                aria-label="Добавить стакан воды"
                onClick={onWaterTap}
              >
                <span className="wellness-dash__qa-icon" aria-hidden="true">
                  💧
                </span>
                <span className="wellness-dash__qa-label">Стакан воды</span>
              </button>
            )}
            <button
              type="button"
              className="wellness-dash__qa-btn wellness-dash__qa-btn--compact"
              aria-label="Новая запись"
              onClick={onCatalogTap}
            >
              <span className="wellness-dash__qa-icon" aria-hidden="true">
                📅
              </span>
              <span className="wellness-dash__qa-label">Новая запись</span>
            </button>
            <button
              type="button"
              className="wellness-dash__qa-btn wellness-dash__qa-btn--compact"
              aria-label="Скорректировать план"
              onClick={onChatTap}
            >
              <span className="wellness-dash__qa-icon" aria-hidden="true">
                📝
              </span>
              <span className="wellness-dash__qa-label">Скорректировать план</span>
            </button>
            <button
              type="button"
              className="wellness-dash__qa-btn wellness-dash__qa-btn--compact"
              aria-label="Профиль"
              onClick={onProfileTap}
            >
              <span className="wellness-dash__qa-icon" aria-hidden="true">
                👤
              </span>
              <span className="wellness-dash__qa-label">Профиль</span>
            </button>
          </div>

          {/* Sync indicator for offline water queue (§11.8). */}
          {waterQueueLen > 0 && (
            <p
              className="wellness-dash__queue-indicator"
              role="status"
              aria-live="polite"
            >
              +{waterQueueLen} {ruPluralWater(waterQueueLen)}{" "}
              {ruPluralWaterWaits(waterQueueLen)} синхронизации
            </p>
          )}
          {/* Transient toasts. */}
          {waterToast && (
            <div
              className="wellness-dash__toast"
              role="status"
              aria-live="polite"
            >
              {waterToast}
              {undoEntryId && (
                <button
                  type="button"
                  className="wellness-dash__toast-action"
                  aria-label="Отменить стакан"
                  onClick={onUndoWater}
                >
                  Отменить
                </button>
              )}
            </div>
          )}
        </section>

        {/* Block A — «Продолжить разговор с Ayla» (DRF-2144 п.5; фриз п.4:
            превью — только реальный последний контекст, иначе нейтрально).
            Обе кнопки закрывают Mini App — человек возвращается в чат MAX,
            откуда приложение открыто; фразу он пишет сам (deep link с
            текстом в MAX не существует — см. StateError.tsx). */}
        <section className="wellness-dash__ayla" aria-labelledby="ayla-header">
          <h2 id="ayla-header" className="wellness-dash__section-header">
            Продолжить разговор с <span lang="en">Ayla</span>
          </h2>
          <div className="wellness-dash__ayla-card">
            {lastTopic && (
              <>
                <div className="wellness-dash__ayla-topic-head">
                  <span>Последняя тема</span>
                  {topicWhen && (
                    <span className="wellness-dash__ayla-when">{topicWhen}</span>
                  )}
                </div>
                <p className="wellness-dash__ayla-topic">{lastTopic.text}</p>
              </>
            )}
            <button type="button" className="wellness-dash__cta" onClick={onChatTap}>
              Продолжить разговор
            </button>
            <button type="button" className="wellness-dash__link-btn" onClick={onChatTap}>
              Задать новый вопрос
            </button>
          </div>
        </section>

        {/* Block 6 — Прогресс недели (cold-start gate §11.4). */}
        {showWeekly && weeklyProgress && activity.kind === "ok" && (
          <section
            className="wellness-dash__weekly"
            aria-labelledby="weekly-header"
          >
            <h2 id="weekly-header" className="wellness-dash__section-header">
              Прогресс недели
            </h2>
            <ul className="wellness-dash__weekly-list">
              <li>
                <span aria-hidden="true">💧</span> Вода:{" "}
                {weeklyProgress.water_days_logged} из 7 дней
              </li>
              <li>
                <span aria-hidden="true">🍽</span> Питание:{" "}
                {weeklyProgress.food_days_logged} из 7 дней
              </li>
              <li>
                <span aria-hidden="true">📅</span> Активность:{" "}
                {weeklyProgress.active_days_count} дней
              </li>
            </ul>
            {/* Кнопка «Подробнее в Дне» снята (DRF-1546): поверхности
                «День» не существует, а вела она на `/` — экран входа.
                Вернуть вместе с самой вкладкой «День». */}
          </section>
        )}

        {/* Block 7 — Recommendations embed (TL extension: real scorer
            picks onto mirror services). Hide silently on error / empty
            (spec: no error UI). Owner ruling 25.08 — the branded
            signature «Ayla подобрала тебе» is gated on the WHY the
            SOURCE sent, not on a flag: the block reappears on its own
            once `POST /recommendations` returns reasons. */}
        {picksWithWhy.length > 0 && (
            <section
              className="wellness-dash__recos"
              aria-labelledby="recos-header"
            >
              <h2 id="recos-header" className="wellness-dash__section-header">
                <span aria-hidden="true">✨ </span>
                <span lang="en">Ayla</span> подобрала тебе
              </h2>
              <ul className="wellness-dash__reco-list">
                {picksWithWhy.map(({ service, reasons }) => (
                  <li key={service.id}>
                    <RecoCard
                      service={service}
                      reasons={reasons}
                      onOpen={() =>
                        navigate(`/customer/catalog/${service.id}`)
                      }
                    />
                  </li>
                ))}
              </ul>
            </section>
          )}
      </main>

      {/* Нижняя панель — ровно пять вкладок по макету H01 (решение владельца
          §55 б, DRF-2144): Главная · План · Дневник · Записи · Профиль.
          «Услуги» ушли из панели в каталог (по «Записаться» / карточке),
          «Я» стало «Профиль». Этот экран — «Главная», поэтому активна она.
          Сетка панели подстраивается под число вкладок, см.
          `.wellness-dash__nav` в globals.css. */}
      <nav className="wellness-dash__nav" aria-label="Основная навигация">
        {HOME_TABS.map((tab) => (
          <button
            key={tab.label}
            type="button"
            className={
              tab.route === null
                ? "wellness-dash__nav-tab wellness-dash__nav-tab--active"
                : "wellness-dash__nav-tab"
            }
            aria-current={tab.route === null ? "page" : undefined}
            aria-label={tab.label}
            onClick={tab.route === null ? undefined : () => navigate(tab.route as string)}
          >
            <span className="wellness-dash__nav-icon" aria-hidden="true">
              {tab.icon}
            </span>
            <span className="wellness-dash__nav-label">{tab.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

function PulseSkeleton() {
  return (
    <div className="wellness-dash__pulse-card" aria-hidden="true">
      <div className="skeleton wellness-dash__skel-line" style={{ height: 18 }} />
      <div
        className="skeleton wellness-dash__skel-line"
        style={{ height: 14, width: "80%", marginTop: "var(--s-2)" }}
      />
      <div
        className="skeleton wellness-dash__skel-line"
        style={{ height: 14, width: "60%", marginTop: "var(--s-2)" }}
      />
    </div>
  );
}

/** Copy for a slice the backend could not read. One string, one place —
 *  the goal row has said this since DRF-1476 and the nutrition rows say
 *  it since DRF-1546. */
const UNAVAILABLE = "Не удалось загрузить";

function PulseStrip({ data }: { data: WellnessToday }) {
  // Absent is not zero (DRF-1546). `0` is «nothing logged yet» and draws
  // normally; an ABSENT key means the read failed and must say so.
  const caloriesEaten = data.calories_eaten;
  const caloriesTarget = data.calories_target;
  // По образцу воды парой строк ниже: съеденное и цель — раздельно.
  const caloriesKnown = caloriesEaten !== undefined;
  const caloriesTargetKnown = caloriesTarget !== undefined;
  const waterEaten = data.water_glasses_eaten;
  const waterTarget = data.water_glasses_target;
  // Знать выпитое и не знать нормы — обычное состояние, а не сбой.
  const waterKnown = waterEaten !== undefined;
  const waterTargetKnown = waterTarget !== undefined;
  // «Ещё ничего не залогировано» — состояние всего дня, а не одной
  // строки, поэтому считается один раз и решает и текст, и шкалу.
  const dayIsEmpty = caloriesEaten === 0 && waterEaten === 0;
  const caloriesPct =
    caloriesKnown && caloriesTargetKnown && caloriesTarget > 0
      ? Math.round((caloriesEaten / caloriesTarget) * 100)
      : 0;
  const waterPct =
    waterKnown && waterTargetKnown && waterTarget > 0
      ? Math.round((waterEaten / waterTarget) * 100)
      : 0;
  // Цель здесь больше не рисуется — она в карточке цели над этим блоком
  // (H01, DRF-2144); строка была бы вторым местом для того же факта.
  // DRF-1927 — ключей дневника нет, потому что нет согласия, а не потому,
  // что чтение упало: говорим, что нужно, а не «Не удалось загрузить».
  const sliceClosedCopy = data.consent_required ? DIARY_CONSENT_REQUIRED_TEXT : UNAVAILABLE;

  return (
    <div className="wellness-dash__pulse-card">
      {/* Питание row */}
      <div
        className="wellness-dash__pulse-row"
        aria-label={
          !caloriesKnown
            ? `Питание: ${sliceClosedCopy}`
            : caloriesTargetKnown
              ? `Питание: ${caloriesEaten} из ${caloriesTarget} килокалорий, ${caloriesPct} процентов${
                  data.pfc
                    ? `. Белки ${data.pfc.protein_g}, жиры ${data.pfc.fat_g}, углеводы ${data.pfc.carbs_g} граммов`
                    : ""
                }`
              : `Питание: ${caloriesEaten} килокалорий сегодня`
        }
      >
        <div className="wellness-dash__pulse-head">
          <span aria-hidden="true">🍽 </span>Питание
        </div>
        {caloriesKnown && caloriesTargetKnown ? (
          <>
            <div className="wellness-dash__pulse-numbers" aria-hidden="true">
              {dayIsEmpty
                ? "Ещё ничего не залогировано"
                : `${caloriesEaten} / ${caloriesTarget} ккал · ${caloriesPct} %`}
            </div>
            {/* §11.1 — БЖУ row hidden when pfc absent. DRF-1844: белок
                «108 / 130 г», когда ориентир по белку приехал; без него —
                факт без второго числа (§85 §8: процент и «из» только при
                ориентире). */}
            {data.pfc && (
              <div className="wellness-dash__pulse-pfc" aria-hidden="true">
                Б {data.pfc.protein_g}
                {data.pfc.protein_target_g !== undefined
                  ? ` / ${data.pfc.protein_target_g}`
                  : ""}{" "}
                · Ж {data.pfc.fat_g} · У {data.pfc.carbs_g} г
              </div>
            )}
            {/* Пустой день — без шкалы. Полоса при нуле не видна глазом,
                но `role="progressbar"` озвучивает «0 из 2000», то есть
                противоречит строке над ней ровно для того человека,
                который не может проверить глазами. */}
            {!dayIsEmpty && (
              <div
                className="wellness-dash__progress"
                role="progressbar"
                aria-valuenow={caloriesEaten}
                aria-valuemin={0}
                aria-valuemax={caloriesTarget}
                aria-label={`Калории: ${caloriesEaten} из ${caloriesTarget}`}
              >
                <div
                  className="wellness-dash__progress-fill"
                  style={{ width: `${Math.min(100, caloriesPct)}%` }}
                  aria-hidden="true"
                />
              </div>
            )}
          </>
        ) : caloriesKnown ? (
          /* Цель не известна — показываем ровно то, что знаем: сколько
             съедено. Ни шкалы, ни процентов: и то и другое считается ОТ
             цели, а цели нет. Та же форма, что у воды ниже. */
          <div className="wellness-dash__pulse-numbers">
            {dayIsEmpty
              ? "Ещё ничего не залогировано"
              : `${caloriesEaten} ккал сегодня`}
          </div>
        ) : (
          /* Read failed - no numbers, no bar. «0 / 0 ккал · 0 %» would
             read as a logged-nothing day, which is a different fact. */
          <div className="wellness-dash__pulse-numbers">{sliceClosedCopy}</div>
        )}
      </div>

      <hr className="wellness-dash__pulse-divider" aria-hidden="true" />

      {/* Вода row */}
      <div
        className="wellness-dash__pulse-row"
        aria-label={
          !waterKnown
            ? `Вода: ${sliceClosedCopy}`
            : waterTargetKnown
              ? `Вода: ${waterEaten} из ${waterTarget} стаканов`
              : `Вода: ${waterEaten} ${ruPluralWater(waterEaten)} сегодня`
        }
      >
        <div className="wellness-dash__pulse-head">
          <span aria-hidden="true">💧 </span>Вода
        </div>
        {waterKnown && waterTargetKnown ? (
          <>
            <div className="wellness-dash__pulse-numbers" aria-hidden="true">
              {waterEaten} / {waterTarget} стаканов
            </div>
            <div
              className="wellness-dash__water-dots"
              role="progressbar"
              aria-valuenow={waterEaten}
              aria-valuemin={0}
              aria-valuemax={waterTarget}
              aria-label={`Вода: ${waterEaten} из ${waterTarget} стаканов (${waterPct} процентов)`}
            >
              <span aria-hidden="true">
                {Array.from({ length: waterTarget }).map((_, i) => (
                  <span key={i}>{i < waterEaten ? "●" : "○"}</span>
                ))}
              </span>
            </div>
          </>
        ) : waterKnown ? (
          /* Норма не известна — показываем ровно то, что знаем: сколько
             выпито. Ни шкалы, ни процентов, ни точек «до нормы»: всё это
             считается ОТ цели, а цели нет. Формулировка — из макета
             §Cold-start: «0 стаканов сегодня». */
          <div className="wellness-dash__pulse-numbers">
            {waterEaten} {ruPluralWater(waterEaten)} сегодня
          </div>
        ) : (
          /* Read failed. The «+ стакан» quick action stays live - it is
             a separate handle (POST /wellness/water) and still works. */
          <div className="wellness-dash__pulse-numbers">{sliceClosedCopy}</div>
        )}
      </div>

    </div>
  );
}

// ---------------------------------------------------------------------------
// H01 (DRF-2144): карточка цели, «План на сегодня», карточка записи.
// ---------------------------------------------------------------------------

/** Ярлыки действий Plan Lite — те же, что на экране плана (одно имя на проводе). */
const PLAN_ACTION_LABELS: Record<PlanLiteActionType, string> = {
  book_service: PLAN_LITE_COPY.labelBook,
  log_food: PLAN_LITE_COPY.labelFood,
  log_water: PLAN_LITE_COPY.labelWater,
};

const PLAN_ACTION_ICONS: Record<PlanLiteActionType, string> = {
  book_service: "📅",
  log_food: "🍽",
  log_water: "💧",
};

/** Период ведра — словами экрана плана: «Сегодня» / «На этой неделе» / «Эти 2 недели». */
const PLAN_CADENCE_PERIOD: Record<PlanLiteCadence, string> = {
  per_day: PLAN_LITE_COPY.today,
  per_week: PLAN_LITE_COPY.thisWeek,
  per_2_weeks: PLAN_LITE_COPY.twoWeeks,
};

/**
 * Adherence плана — сколько действий сделано из скольких за текущие вёдра.
 * Это счёт ДЕЙСТВИЙ, не оценка результата (§49: без веса, §82: без
 * процентов) — поэтому строка «выполнено N из M действий», а не шкала.
 */
function planAdherence(plan: PlanLite): { done: number; total: number } {
  return plan.actions.reduce(
    (acc, a) => ({
      done: acc.done + Math.min(a.done_count, a.target_count),
      total: acc.total + a.target_count,
    }),
    { done: 0, total: 0 },
  );
}

function GoalCard({
  goal,
  goalsKnown,
  plan,
  onPlan,
  onGoal,
}: {
  goal: ActiveGoal | undefined;
  goalsKnown: boolean;
  plan: PlanSlice;
  onPlan: () => void;
  onGoal: () => void;
}) {
  if (!goal) {
    // Нет цели / слой цели не ответил — та же форма карточки, чтобы экран
    // не «прыгал» между состояниями. Три состояния — DRF-1476: «Выбери цель»
    // говорится ТОЛЬКО когда известно, что цели нет.
    return (
      <section className="wellness-dash__goal-card" aria-labelledby="goal-header">
        <p id="goal-header" className="wellness-dash__goal-eyebrow">
          Цель
        </p>
        {goalsKnown ? (
          <>
            <p className="wellness-dash__goal-hint">Расскажи о себе — точнее советую</p>
            <button type="button" className="wellness-dash__cta" onClick={onGoal}>
              Выбери цель
            </button>
          </>
        ) : (
          <p className="wellness-dash__goal-hint">{UNAVAILABLE}</p>
        )}
      </section>
    );
  }

  const planKnown = plan.kind === "ok";
  const activePlan = plan.kind === "ok" ? plan.data : null;
  const due = formatGoalDue(goal.target_date);
  const weekPrefix = goal.week_num ? `Неделя ${goal.week_num} · ` : "";
  let statusLine: string | null = null;
  if (activePlan) {
    const { done, total } = planAdherence(activePlan);
    statusLine = weekPrefix
      ? `${weekPrefix}выполнено ${done} из ${total} действий`
      : `Выполнено ${done} из ${total} действий`;
  } else if (planKnown) {
    statusLine = "План ещё не составлен";
  } else if (goal.week_num) {
    // План недоступен (сервер выключил или не ответил) — о плане молчим,
    // счётчик недель остаётся: это не оценка выполнения (решение №13).
    statusLine = `Неделя ${goal.week_num}`;
  }

  return (
    <section className="wellness-dash__goal-card" aria-labelledby="goal-header">
      <p id="goal-header" className="wellness-dash__goal-eyebrow">
        Активная цель
      </p>
      <h2 className="wellness-dash__goal-title">{goal.title}</h2>
      {/* DRF-2173 — срок цели «До 1 ноября 2026» (макет DRF-1321); без срока строки нет. */}
      {due && <p className="wellness-dash__goal-due">{due}</p>}
      {/* Ни шкалы, ни процентов, ни веса под целью (§49/§82, решение №13):
          только счёт действий из плана. */}
      {statusLine && <p className="wellness-dash__goal-status">{statusLine}</p>}
      {activePlan && (
        <button type="button" className="wellness-dash__cta" onClick={onPlan}>
          Продолжить сегодняшний план
        </button>
      )}
      {!activePlan && planKnown && (
        <button type="button" className="wellness-dash__cta" onClick={onPlan}>
          Составить план
        </button>
      )}
      <button type="button" className="wellness-dash__link-btn" onClick={onGoal}>
        Посмотреть детали цели
      </button>
    </section>
  );
}

function PlanToday({
  plan,
  onAll,
  onAdjust,
}: {
  plan: PlanLite;
  onAll: () => void;
  onAdjust: () => void;
}) {
  return (
    <section className="wellness-dash__plan-today" aria-labelledby="plan-today-header">
      <div className="wellness-dash__plan-head">
        <h2 id="plan-today-header" className="wellness-dash__section-header">
          План на сегодня
        </h2>
        <button type="button" className="wellness-dash__link-btn" onClick={onAll}>
          Смотреть весь план
        </button>
      </div>
      <ul className="wellness-dash__plan-list">
        {plan.actions.map((a) => {
          const label = PLAN_ACTION_LABELS[a.action_type];
          const done = a.done_count >= a.target_count;
          return (
            <li key={a.action_type} className="wellness-dash__plan-item">
              <span className="wellness-dash__plan-icon" aria-hidden="true">
                {PLAN_ACTION_ICONS[a.action_type]}
              </span>
              <div className="wellness-dash__plan-main">
                <div className="wellness-dash__plan-label">{label}</div>
                <div className="wellness-dash__plan-period">{PLAN_CADENCE_PERIOD[a.cadence]}</div>
              </div>
              <div className="wellness-dash__plan-state">
                {done ? (
                  <span
                    className="wellness-dash__plan-done"
                    role="img"
                    aria-label={`${label}: выполнено`}
                  >
                    ✓
                  </span>
                ) : (
                  PLAN_LITE_COPY.ofTotal(a.done_count, a.target_count)
                )}
              </div>
              <span className="wellness-dash__plan-chip">из вашего плана</span>
            </li>
          );
        })}
      </ul>
      {/* Attention-состояние по фризу 25.08 п.2 — человеческим языком. Кнопка
          закрывает Mini App в чат MAX; что именно поправить, человек пишет
          сам — контекст в чат не передаётся. */}
      <div className="wellness-dash__plan-attention" role="note">
        <p className="wellness-dash__plan-attention-text">Сегодня не получается по плану?</p>
        <button type="button" className="btn-secondary" onClick={onAdjust}>
          Скорректировать с Ayla
        </button>
      </div>
    </section>
  );
}

function BookingSkeleton() {
  return (
    <div className="wellness-dash__booking-card" aria-hidden="true">
      <div className="skeleton wellness-dash__skel-line" style={{ height: 18 }} />
      <div
        className="skeleton wellness-dash__skel-line"
        style={{ height: 14, width: "70%", marginTop: "var(--s-2)" }}
      />
      <div
        className="skeleton wellness-dash__skel-line"
        style={{ height: 14, width: "50%", marginTop: "var(--s-2)" }}
      />
    </div>
  );
}

function BookingEmpty({ onBook }: { onBook: () => void }) {
  // По макету H01: «Записей нет» + «Записаться» → каталог. Прежняя заглушка
  // «Подобрать услугу под твою цель…» обещала подбор, которого на Главной
  // нет (C01: Home — не второй каталог).
  return (
    <div className="wellness-dash__booking-card">
      <p className="wellness-dash__booking-empty">Записей нет</p>
      <button type="button" className="btn-secondary" onClick={onBook}>
        Записаться
      </button>
    </div>
  );
}

function BookingCard({
  data,
  onOpen,
  onAll,
}: {
  data: RecentActivity;
  onOpen: () => void;
  onAll: () => void;
}) {
  const b = data.next_booking;
  if (!b) return null;
  const moreThisWeek = data.this_week_booking_count > 1;
  // Бейдж — тем же словарём, что список записей; ключа нет (старый сервер)
  // — бейджа нет, а не «неизвестно».
  const status = b.status ? mapBookingStatus(b.status) : null;
  // Цена — место оставлено под DRF-2172: пока ключа нет, строки нет (§33).
  const price = b.price ? priceFromLabel(b.price) : "";
  return (
    <div className="wellness-dash__booking-card">
      <div className="wellness-dash__booking-top">
        <div className="wellness-dash__booking-what">
          {b.service_name}
          {b.duration_min ? ` · ${b.duration_min} мин` : ""}
        </div>
        {status && <StatusBadge rendering={status.rendering} />}
      </div>
      <div className="wellness-dash__booking-who">
        у {b.master_name} · {b.salon_name}
      </div>
      <div className="wellness-dash__booking-row">
        <div className="wellness-dash__booking-when">{b.date_human}</div>
        {price && <div className="wellness-dash__booking-price">{price}</div>}
      </div>
      {/* Адрес — три состояния, и на экране их два разных текста
          (DRF-1611). Строка есть ВСЕГДА: человек идёт на визит, и
          нужда у него одна и та же независимо от того, чей это
          пробел — салона или наш. */}
      <div className="wellness-dash__booking-where">
        {visitAddressText(b.address)}
      </div>

      {/* §11.3 — multi-record indicator. */}
      {moreThisWeek && (
        <div className="wellness-dash__booking-more">
          Ещё {data.this_week_booking_count - 1}{" "}
          {ruPluralBooking(data.this_week_booking_count - 1)} на этой неделе
        </div>
      )}

      {/* По макету: «Открыть запись» и «Все мои записи». «Перенести» с
          Главной снято — перенос живёт в карточке записи. */}
      <div className="wellness-dash__booking-actions">
        <button
          type="button"
          className="btn-secondary wellness-dash__booking-primary"
          onClick={onOpen}
          aria-label="Открыть запись"
        >
          Открыть запись
        </button>
      </div>
      <button
        type="button"
        className="wellness-dash__booking-all"
        onClick={onAll}
        aria-label="Все мои записи"
      >
        Все мои записи →
      </button>
    </div>
  );
}

/**
 * One branded pick: WHAT (service + factual chips) + WHY (the lines the
 * source sent, verbatim). `reasons` is always non-empty — the screen
 * never builds a RecoCard for a pick it cannot explain.
 */
function RecoCard({
  service,
  reasons,
  onOpen,
}: {
  service: Service;
  reasons: string[];
  onOpen: () => void;
}) {
  const titleId = `wd-reco-title-${service.id}`;
  const metaId = `wd-reco-meta-${service.id}`;
  const whyId = `wd-reco-why-${service.id}`;
  return (
    <article className="wellness-dash__reco-card">
      <button
        type="button"
        className="wellness-dash__reco-btn"
        onClick={onOpen}
        aria-labelledby={titleId}
        aria-describedby={`${metaId} ${whyId}`}
      >
        <div id={titleId} className="wellness-dash__reco-title">
          {service.name}
        </div>
        {!service.is_bookable && (
          // DRF-1164 — the scorer ranks by fit, not by staffing, so a pick
          // can be a service nobody performs. Same label as the catalog
          // card; the tap lands on /customer/catalog/:id, which withholds
          // the CTA.
          <UnbookableBadge />
        )}
        <div id={metaId} className="wellness-dash__reco-meta">
          {/* Factual mirror fields only — never a synthesised WHY. */}
          {formatDuration(service.duration_min)}
          {/* DRF-1989 — цена ниже 1 ₽ не рисуется: «от 0 ₽» читалось как «бесплатно». */}
          {service.duration_min && service.price_from && priceFromLabel(service.price_from)
            ? " · "
            : ""}
          {service.price_from && priceFromLabel(service.price_from)
            ? `от ${priceFromLabel(service.price_from)}`
            : ""}
        </div>
      </button>
      {/* WHY — verbatim from the source. Kept OUTSIDE the button (a
          <ul> is not phrasing content) but wired to it via
          aria-describedby, so screen readers announce the reason with
          the pick. */}
      <ul id={whyId} className="wellness-dash__reco-why">
        {reasons.map((reason) => (
          <li key={reason} className="wellness-dash__reco-why-item">
            {reason}
          </li>
        ))}
      </ul>
    </article>
  );
}

function BlockError({
  reason,
  onRetry,
}: {
  reason: LoadErrorReason;
  onRetry: () => void;
}) {
  // Warm copy per Tau §5 State 3 + §7 brand voice. DRF-1319 D-1: отказ
  // входа — своим именем, как на Hello и в StateError, а не «через минуту».
  const text =
    reason.kind === "auth"
      ? authErrorCopy(reason.slug).body
      : reason.kind === "network"
        ? "Не получилось загрузить. Проверь интернет и попробуй ещё раз."
        : "Что-то пошло не так. Попробуй ещё раз через минуту.";
  return (
    <div className="wellness-dash__block-error" role="status" aria-live="polite">
      {reason.kind === "auth" && (
        <p style={{ fontWeight: 600 }}>{authErrorCopy(reason.slug).title}</p>
      )}
      <p>{text}</p>
      <button type="button" className="btn-secondary" onClick={onRetry}>
        Обновить
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Russian plural helpers — keep copy natural.
// ---------------------------------------------------------------------------

function ruPluralWater(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "стаканов";
  if (mod10 === 1) return "стакан";
  if (mod10 >= 2 && mod10 <= 4) return "стакана";
  return "стаканов";
}

// Verb agreement for "стакан(а/ов) ждёт/ждут синхронизации".
// Singular forms (1, 21, 31, ...) take "ждёт"; rest take "ждут".
// Excludes the 11-14 teen-irregular range.
/** Предложения тоста через пробел; точка ставится там, где её нет. */
function joinSentences(parts: string[]): string {
  return parts.map((p, i) => (i < parts.length - 1 && !/[.!?]$/.test(p) ? `${p}.` : p)).join(" ");
}

function ruPluralWaterWaits(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "ждут";
  if (mod10 === 1) return "ждёт";
  return "ждут";
}

function ruPluralBooking(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "записей";
  if (mod10 === 1) return "запись";
  if (mod10 >= 2 && mod10 <= 4) return "записи";
  return "записей";
}
