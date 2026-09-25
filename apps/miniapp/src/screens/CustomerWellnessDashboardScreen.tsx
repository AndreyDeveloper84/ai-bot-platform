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
 * Порядок по макету — решение владельца 22.09 (Д32, DRF-2330):
 * цель → план → запись → быстрые действия → Ayla → дневник.
 *
 *   Block 1 — Greeting + human one-liner (Tau §3 / §11.9 + §11.10);
 *     внутри — карточка «Начнём с малого?» (Д31 а): подсказка первого шага
 *     человеку с пустым днём, поэтому наверху, а не в конце экрана
 *   Block G — карточка активной цели: «Активная цель», название,
 *     «Неделя N · выполнено N из M действий» (adherence Plan Lite),
 *     primary «Продолжить сегодняшний план →» / «Составить план»,
 *     вторичное «Посмотреть детали цели»; без цели — «Выбери цель»
 *   Block P — «План на сегодня» из Plan Lite (тот же источник, что у
 *     PlanLiteScreen); без плана блока нет
 *   Block 5 — Ближайшая запись: карточка (услуга, мастер, когда, адрес,
 *     статус, «Открыть запись»); «Все мои записи» — справа от заголовка
 *     (Д11); нет записи — «Записей нет» + «Записаться»
 *   Block 3 — Быстрые действия по фризу: записать питание / стакан воды /
 *     новая запись / скорректировать план / профиль
 *   Block A — «Продолжить разговор с Ayla» с последней темой
 *     (`customer/last-topic/`); без темы — нейтрально. ЕДИНСТВЕННЫЙ вход в
 *     чат с этого экрана (Д2)
 *   Block C — ОДИН блок согласия дневника (вместо двух абзацев); стоит над
 *     полосой дневника, которую и закрывает
 *   Block 2 — Pulse strip (Питание + Вода) — Tau §3 + §11.1
 *   Block 4 — Шаги на сегодня (норма воды из анкеты; иной источник, чем
 *     план) — сразу после дневника: это его числа (Д31 б)
 *   Block 7 — Recommendations embed: СКРЫТ (Д31 г), см.
 *     `lib/ayla-picks-shelf` и ответ 40 (§172). Тот же выключатель
 *     снимает и запрос за данными полки (DRF-2348)
 *   Bottom nav — Главная · План · Дневник · Записи · Профиль (§55 б)
 *
 * Снято решением владельца 22.09: кнопка «спросить» из шапки (Д2) и блок
 * «Прогресс недели» (Д31 в — сервер этих данных и так не слал).
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
import {
  formatTopicWhen,
  getLastTopicAndChatLink,
  type LastTopic,
} from "../lib/customer-last-topic";
import { formatDuration, priceFromLabel } from "../lib/format";
import { returnToChat } from "../lib/max-sdk";
import { ReturnToChatHint } from "../components/ReturnToChatHint";
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
  CONSENT_PROMPT_FAILED_TEXT,
  CONSENT_PROMPT_ALREADY_SENT_TEXT,
  CONSENT_PROMPT_OPEN_CHAT_CTA,
  requestDiaryConsentPrompt,
  pfcLine,
} from "../lib/customer-wellness";
import {
  getCatalogBrowse,
  type CatalogBrowseData,
} from "../lib/customer-booking";
import { aylaPicksShelfOn } from "../lib/ayla-picks-shelf";
import { CustomerAvatarEntry } from "../components/CustomerAvatarEntry";
import { StatusBadge } from "../components/StatusBadge";
import { CustomerTabBar } from "../components/CustomerTabBar";
import { UnbookableBadge } from "../components/UnbookableNote";
import { useScreenBack } from "../hooks/useScreenBack";
import { PLAN_LITE_COPY, PLAN_LITE_ROUTE } from "./PlanLiteScreen";
import { screenRoot } from "../lib/screen-back";

/** Одна цель из `wellness/today.active_goals` (cap=1, решение №13). */
type ActiveGoal = NonNullable<WellnessToday["active_goals"]>[number];

/**
 * Шапка H01 здоровается словами макета — Д1, дословно (DRF-2331).
 *
 * Строка вынесена сюда, потому что PR обещает «с макета дословно»: сверять
 * обещание надо с одним местом, а не с копией в тесте.
 *
 * ⚠ С этой строкой экран здоровается ДВАЖДЫ: ниже стоит блок приветствия
 * «Доброе утро, Анна 🌿» (`docs/screens/customer-main-wellness-dashboard.md`
 * §7), а макет здоровается один раз — только в шапке. Вопрос у владельца
 * тремя вариантами (шапка вместо блока / шапка без этой строки / оставить
 * оба). До его слова не трогаем ни то, ни другое: любой выбор здесь
 * выдал бы догадку за решение.
 */
export const HEADER_WELCOME_LINE = "Рада вас видеть!";

/**
 * Согласие дневника — ОДИН блок на экране (DRF-2144 п.6). Формулировка из
 * листа. Кнопка сперва просит сервер прислать в чат MAX приглашение с
 * кнопкой «Дать согласие» (DRF-2230) и только потом закрывает Mini App:
 * раньше она просто закрывала приложение, и человек попадал в чат, где о
 * согласии не было ни слова (скрин владельца 21.09).
 */
export const DIARY_CONSENT_CARD_TEXT =
  "Чтобы вести дневник, нужно согласие — дай его в чате с Ayla";
export const DIARY_CONSENT_CARD_CTA = "Дать согласие в чате";

// DRF-2268: строка и компонент подсказки — общие, `components/ReturnToChatHint`.
export { CHAT_STUCK_HINT } from "../components/ReturnToChatHint";

function ChatStuckHint() {
  return <ReturnToChatHint className="wellness-dash__chat-hint" />;
}

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
 * Подбор для полки «Ayla подобрала тебе» — четыре состояния (DRF-2348).
 *
 * `not_requested` — полка обездвижена, за данными не ходили. Отдельное
 * состояние, потому что остальные три на этот вопрос отвечают неправду:
 * «грузится», «не ответил» и «ответил, полка построена» — всё это про
 * источник, которого никто не спрашивал.
 */
type RecsSlice = Slice<CatalogBrowseData> | { kind: "not_requested" };

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
  // Четвёртое состояние, и оно не роскошь: ни одно из трёх не говорит
  // «мы не спрашивали». `loading` соврал бы «грузится», `error` — «не
  // ответил», а `ok` с пустыми списками соврал бы дважды: и на экране
  // («полка построена»), и в шести исходах `picksOutcome`, которые
  // заведены ровно для того, чтобы не смешивать отсутствие с нулём
  // (`customer-booking.ts`, таблица исходов). Полка тёмная — вопроса не
  // было, и так и записано.
  const [recs, setRecs] = useState<RecsSlice>(() =>
    // Ленивое начальное значение, потому что «не спрашивали» — ровно то
    // утверждение, ради которого это состояние и заведено: при зажжённой
    // полке первый кадр говорил бы неправду до первого `fetchAll`.
    aylaPicksShelfOn() ? { kind: "loading" } : { kind: "not_requested" },
  );
  const [planSlice, setPlanSlice] = useState<PlanSlice>({ kind: "loading" });
  // Тема — `null` и «ручка упала» читаются одинаково: блок нейтральный.
  const [lastTopic, setLastTopic] = useState<LastTopic | null>(null);
  // DRF-2266 — куда ведут двери в чат, и где они «застряли» (web.max.ru).
  const [chatLink, setChatLink] = useState<string | null>(null);
  const [chatStuckAt, setChatStuckAt] = useState<"ayla" | "consent" | "plan" | null>(null);

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

  // Один выключатель на вёрстку полки и на запрос за её данными
  // (`lib/ayla-picks-shelf`, DRF-2348). Читается при отрисовке, а не на
  // уровне модуля: так сторож может проверить обе стороны.
  const shelfOn = aylaPicksShelfOn();

  // ── data fetch ────────────────────────────────────────────────────────
  const fetchAll = useCallback(async () => {
    setToday({ kind: "loading" });
    setActivity({ kind: "loading" });
    // Тёмная полка не «грузится» — её просто не спрашивают (DRF-2348).
    setRecs(shelfOn ? { kind: "loading" } : { kind: "not_requested" });
    setPlanSlice({ kind: "loading" });

    // Per-slice isolation — Promise.allSettled so one failure doesn't
    // blank the other blocks. (Tau §5 State 5 partial render.)
    // `getCatalogBrowse` зовётся ТОЛЬКО при зажжённой полке: решение
    // владельца гасит полку, а обращение уходило всё равно — при каждом
    // открытии экрана, хотя показывать нечего (DRF-2348, §172 ответ 40).
    // Ветка `null` держит форму `allSettled` и порядок распаковки: так
    // включение полки возвращает запрос одной строкой в `aylaPicksShelfOn`,
    // а не раскопками.
    const [todayRes, activityRes, recsRes, planRes, topicRes] = await Promise.allSettled([
      getWellnessToday(),
      getRecentActivity(),
      shelfOn ? getCatalogBrowse() : Promise.resolve(null),
      getPlanLite(),
      getLastTopicAndChatLink(),
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

    if (!shelfOn) {
      // Вопроса не было — и в срезе стоит именно это, а не пустой ответ.
      setRecs({ kind: "not_requested" });
    } else if (recsRes.status === "rejected") {
      // Recommendations errors hide the whole block silently per spec.
      setRecs({ kind: "error", reason: loadErrorReason(recsRes.reason) });
    } else {
      // Всё остальное при зажжённой полке — ответ. Ветки «а если `null`»
      // здесь намеренно нет: `null` кладёт только выключенная полка, её
      // забрал первый случай. Отдельное условие на `null` выглядело бы
      // аккуратнее, а на деле оставляло бы срез в «грузится» навсегда,
      // если `getCatalogBrowse` однажды станет возвращать `null` — то
      // есть меняло бы тип ошибки на самую тихую (найдено ревью).
      setRecs({ kind: "ok", data: recsRes.value as CatalogBrowseData });
    }

    // План: выключен на сервере (`plan_lite_disabled`) или не ответил —
    // «недоступен», без ошибки на экране: Главная от плана не зависит.
    setPlanSlice(
      planRes.status === "fulfilled" ? { kind: "ok", data: planRes.value } : { kind: "unavailable" },
    );
    setLastTopic(topicRes.status === "fulfilled" ? topicRes.value.topic : null);
    setChatLink(topicRes.status === "fulfilled" ? topicRes.value.chatLink : null);
  }, [shelfOn]);

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
  // Отдельного «📸 Сфотографируй еду» нет (снято в DRF-1546, пока у
  // съёмки не было ручки). Ручки подключены (DRF-2098/2106), и фото
  // теперь — вход «Записать питание» (DRF-2289, ниже).

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
  // быстрое действие «Записать питание» ведёт к съёмке фото (DRF-2289),
  // ввод текстом — ссылкой «Записать текстом» на том же экране. Гейт
  // согласия на сканер съёмка проверяет сама (DRF-1564).
  const onFoodTap = useCallback(() => {
    navigate("/customer/food-scanner/capture");
  }, [navigate]);

  // Карточка «Начнём с малого?» обещает именно текст — её кнопка ведёт
  // прямо в ввод текстом, а не на съёмку (DRF-2289).
  const onFoodTextTap = useCallback(() => {
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
  // DRF-2266 — все двери Главной в чат идут одним путём: мост close() →
  // ссылка на диалог бота → подсказка рядом с нажатой кнопкой (web.max.ru:
  // раньше `closeApp()` там молча ничего не делал — «кнопка не работает»).
  const goToChat = useCallback(
    (where: "ayla" | "consent" | "plan") => {
      const outcome = returnToChat(chatLink);
      setChatStuckAt(outcome === "stuck" ? where : null);
    },
    [chatLink],
  );
  const onChatTap = useCallback(() => goToChat("ayla"), [goToChat]);
  const onPlanChatTap = useCallback(() => goToChat("plan"), [goToChat]);

  // DRF-2230 — приглашение к согласию уходит в чат, и лишь потом экран
  // закрывается. Сбой отправки — вслух и без закрытия: закрыться молча значило
  // бы снова отправить человека в чат, где ему нечего нажать.
  const [consentPromptError, setConsentPromptError] = useState<string | null>(null);
  const [consentPromptBusy, setConsentPromptBusy] = useState(false);
  const [consentPromptAlreadySent, setConsentPromptAlreadySent] = useState(false);
  const onConsentTap = useCallback(async () => {
    setConsentPromptError(null);
    setConsentPromptAlreadySent(false);
    setConsentPromptBusy(true);
    try {
      const res = await requestDiaryConsentPrompt();
      if (res.reason === "already_granted") {
        // Согласие уже есть — блок уйдёт после перечитывания; в чат незачем.
        void fetchAll();
        return;
      }
      if (res.reason === "recently_sent") {
        // Живой проход 21.09: повтор закрывал приложение молча — человек
        // «проваливался в чат». Теперь говорим, что ждёт в чате, и уходим
        // туда по явной кнопке.
        setConsentPromptAlreadySent(true);
        return;
      }
      goToChat("consent");
    } catch {
      setConsentPromptError(CONSENT_PROMPT_FAILED_TEXT);
    } finally {
      setConsentPromptBusy(false);
    }
  }, [fetchAll, goToChat]);

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
  // «Прогресс недели» снят с экрана решением владельца 22.09 (Д31 в): он
  // дублировал план, а сервер этих данных и так не слал. Вместе с блоком
  // ушли и его ключи — держать вычисление ради снятого блока значило бы
  // оставить мёртвый код, который читается как живой.

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
          вели в профиль, а профиль теперь — вкладка панели.

          Д2 (решение владельца 22.09, §172): кнопка «спросить» снята —
          «один вход в чат вместо двух». Второй и единственный остаётся
          блоком «Продолжить разговор с Ayla» ниже: он несёт последнюю тему,
          а шапочная кнопка вела в тот же чат без неё.

          Д1 (DRF-2331): человек слева — аватар, имя, «Рада вас видеть!».

          Колокольчика макета НЕТ, и это решение: ленты уведомлений у
          клиента не существует — ни ручки на сервере, ни экрана на
          клиенте (есть только настройки уведомлений, а это не они).
          Счётчик «2» брать неоткуда вовсе. Мёртвый control запрещён
          решением владельца (§61, М-4 п. 1) и DRF-1181.

          Вордмарк макет в шапке не рисует, но он ОСТАЁТСЯ и занимает
          освободившееся справа место: его держат
          `docs/screens/customer-main-wellness-dashboard.md` §7 и
          `docs/design/policies/ayla-identity-and-brand.md` §7.1. Снять
          фирменный знак — решение о бренде, а не правка вёрстки. */}
      <header className="wellness-dash__header" role="banner">
        <div className="wellness-dash__person">
          {/* Фотографии клиента нет ни в одном контракте — ни у
              `wellness/today`, ни у `/me`. Новой сущности под Д1 не
              заводим: `avatarInitials` уже рисует кружок на профиле
              клиента, и «·» — его же ответ на «имени нет».
              §77 п.60 — кружок стал ВХОДОМ в профиль и уехал в общий
              компонент: он должен быть одинаков на всех экранах панели,
              а не только здесь. Имя передаём своё — оно уже пришло с
              `wellness/today`, и второй источник того же имени дал бы
              расхождение в момент загрузки. */}
          <CustomerAvatarEntry displayName={displayName} />
          <span className="wellness-dash__person-text">
            {/* Пока день грузится, имени ещё нет — и раньше строка просто
                отсутствовала, отчего «Рада вас видеть!» прыгала внутри
                56 px при каждом обновлении (в том числе при «Отменить» у
                стакана воды, DRF-2331, по ревью). Скелет держит высоту и
                говорит «грузится», а не «имени нет» — тем же приёмом, что
                блок приветствия ниже. */}
            {today.kind === "loading" ? (
              <span
                className="skeleton wellness-dash__person-skel"
                aria-hidden="true"
              />
            ) : (
              displayName && (
                <span className="wellness-dash__person-name">
                  {/* Многоточие живёт на ВНУТРЕННЕЙ строке, а 👋 стоит
                      рядом с ней: внутри он съедался первым и длинное имя
                      оставалось без жеста макета, а сразу за пределами
                      строки имени — отлетал к вордмарку и читался как его
                      часть (видно на снимке приёмки). */}
                  <span className="wellness-dash__person-name-text">
                    {displayName}
                  </span>
                  <span aria-hidden="true">👋</span>
                </span>
              )
            )}
            <span className="wellness-dash__person-hi">
              {HEADER_WELCOME_LINE}
            </span>
          </span>
        </div>
        <div className="wellness-dash__brand">
          {/* «ayla» = English wordmark per Tau §7 — wrap in lang="en"
              to keep RU TTS from pronouncing it «Айла» (WCAG 3.1.2). */}
          <span className="wellness-dash__wordmark" lang="en">
            ayla
          </span>
          <span aria-hidden="true"> ✨</span>
        </div>
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
                onClick={onFoodTextTap}
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

        {/* Block P — «План на сегодня»: действия активного плана; без плана
            блока нет (DRF-2144 п.2). */}
        {planSlice.kind === "ok" && planSlice.data && (
          <PlanToday plan={planSlice.data} onAll={onPlanTap} onAdjust={onPlanChatTap} />
        )}

        {/* Block 5 — Ближайшая запись (карточка по макету H01, DRF-2144 п.3;
            источник — `recent-activity`, как и раньше). */}
        <section
          className="wellness-dash__booking"
          aria-labelledby="booking-header"
        >
          {/* Д11 (решение владельца 22.09): «Все мои записи» — СПРАВА ОТ
              ЗАГОЛОВКА, как в макете. Раньше кнопка стояла в самом низу
              карточки, под «Открыть запись»: человек находил её последней,
              хотя это выход ко всем записям, а не действие над этой.

              Раскладка — существующий `wellness-dash__plan-head` (та же
              строка заголовка у блока плана), а не новый класс: у нового
              не было бы правила в стилях, и сторож стиля (DRF-1066) прав,
              что такой класс мёртв. */}
          <div className="wellness-dash__plan-head">
            <h2 id="booking-header" className="wellness-dash__section-header">
              Ближайшая запись
            </h2>
            {/* Только когда запись действительно есть. Д11 — про МЕСТО
                кнопки, а не про новое приглашение: у человека без записей
                «Все мои записи» вело бы в пустой список, а на загрузке и
                на ошибке — предлагало бы переход рядом со строкой «не
                удалось прочитать». Прежде кнопка жила внутри карточки и
                этого условия не теряла. */}
            {activity.kind === "ok" && activity.data.next_booking && (
              <button
                type="button"
                className="wellness-dash__booking-all"
                onClick={() => navigate("/customer/records")}
                aria-label="Все мои записи"
              >
                Все мои записи
                <span aria-hidden="true"> →</span>
              </button>
            )}
          </div>
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
              onClick={onPlanChatTap}
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
          {/* DRF-2266 — «Скорректировать план» тоже дверь в чат. */}
          {chatStuckAt === "plan" && <ChatStuckHint />}

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
            {chatStuckAt === "ayla" && <ChatStuckHint />}
          </div>
        </section>

        {/* ── Дневник, и то, что при нём (Д32, решение владельца 22.09) ──

            По макету дневник идёт последним: цель → план → запись → быстрые
            действия → Ayla → дневник.

            Рядом с ним, НАЗВАННЫМИ отклонениями (в таблице PR):

            * согласие стоит НАД полосой дневника, потому что оно её и
              закрывает (``consentRequired`` — единственный ключ, который
              гасит эту полосу, и ничего выше он не стережёт; замерено).
              Ворота не могут стоять ниже двери, которую запирают;
            * «Шаги на сегодня» — сразу ПОСЛЕ дневника: предмет блока это
              числа дневника («Ещё N стаканов до нормы», норма из анкеты
              питания). Прежнее место между дневником и записью — случайность
              истории. Владелец оставил блок ради этой строки (Д31 б). */}

        {/* Block C — согласие дневника: ОДИН блок (DRF-2144 п.6) вместо двух
            одинаковых абзацев в строках «Питание» и «Вода». */}
        {today.kind === "ok" && consentRequired && (
          <section
            className="wellness-dash__consent"
            aria-label="Согласие на дневник"
          >
            <p className="wellness-dash__consent-text">{DIARY_CONSENT_CARD_TEXT}</p>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void onConsentTap()}
              disabled={consentPromptBusy}
            >
              {DIARY_CONSENT_CARD_CTA}
            </button>
            {consentPromptError ? (
              <p className="wellness-dash__consent-error" role="alert">
                {consentPromptError}
              </p>
            ) : null}
            {consentPromptAlreadySent ? (
              <>
                <p className="wellness-dash__consent-text" role="status">
                  {CONSENT_PROMPT_ALREADY_SENT_TEXT}
                </p>
                <button type="button" className="btn-primary" onClick={() => goToChat("consent")}>
                  {CONSENT_PROMPT_OPEN_CHAT_CTA}
                </button>
              </>
            ) : null}
            {chatStuckAt === "consent" && <ChatStuckHint />}
          </section>
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

        {/* Block 7 — Recommendations embed (TL extension: real scorer
            picks onto mirror services). Hide silently on error / empty
            (spec: no error UI). Owner ruling 25.08 — the branded
            signature «Ayla подобрала тебе» is gated on the WHY the
            SOURCE sent, not on a flag: the block reappears on its own
            once `POST /recommendations` returns reasons. */}
        {/* `shelfOn` здесь — пояс поверх подтяжек, и проверить его узлом
            НЕЛЬЗЯ: при тёмной полке за данными не ходят, `picksWithWhy`
            всегда пуст, и снятие этого условия ничего не меняет (проверено
            мутацией на ревью). Условие оставлено на случай, если срез
            когда-нибудь наполнится из другого места — из кэша, из общего
            состояния. Настоящие ворота — в `fetchAll`. */}
        {shelfOn && picksWithWhy.length > 0 && (
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

      {/* Панель — общая для клиентских экранов (DRF-2191, состав и правила —
          в `components/CustomerTabBar.tsx`); этот экран «Главная». */}
      <CustomerTabBar active="home" />
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

/** DRF-2288 (№41, CD §76) — текст пустого дня, слово владельца вместо «залогировано». */
export const EMPTY_DAY_TEXT = "Сегодня ещё ничего не записано";

/** То же для скринридера: « из 61». */
function spokenTarget(target: number | undefined): string {
  return target !== undefined ? ` из ${target}` : "";
}

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
  // «Сегодня ещё ничего не записано» — состояние всего дня, а не одной
  // строки, поэтому считается один раз и решает и текст, и шкалу.
  const dayIsEmpty = caloriesEaten === 0 && waterEaten === 0;
  // DRF-2288 (№41): строка БЖУ из нулей не рисуется, как только еды нет, —
  // даже если вода уже записана (решение владельца: «строку нулей не рисовать»).
  const foodIsEmpty = caloriesEaten === 0;
  const pfcSpoken =
    data.pfc && !foodIsEmpty
      ? `. Белки ${data.pfc.protein_g}${spokenTarget(data.pfc.protein_target_g)}, жиры ${
          data.pfc.fat_g
        }${spokenTarget(data.pfc.fat_target_g)}, углеводы ${data.pfc.carbs_g}${spokenTarget(
          data.pfc.carbs_target_g,
        )} граммов`
      : "";
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
            : dayIsEmpty
              ? // DRF-2288: скринридер слышит то же, что видно глазу, — не «0 из 2100».
                `Питание: ${EMPTY_DAY_TEXT}`
              : caloriesTargetKnown
                ? `Питание: ${caloriesEaten} из ${caloriesTarget} килокалорий, ${caloriesPct} процентов${pfcSpoken}`
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
                ? EMPTY_DAY_TEXT
                : `${caloriesEaten} / ${caloriesTarget} ккал · ${caloriesPct} %`}
            </div>
            {/* §11.1 — БЖУ row hidden when pfc absent. DRF-1844: белок
                «108 / 130 г», когда ориентир по белку приехал; без него —
                факт без второго числа (§85 §8: процент и «из» только при
                ориентире). */}
            {/* DRF-2288 (№41): ориентиры Ж и У — тем же признаком, что белок;
                в пустой день строки нулей нет — рядом уже сказано, что
                ничего не записано. */}
            {data.pfc && !foodIsEmpty && (
              <div className="wellness-dash__pulse-pfc" aria-hidden="true">
                {pfcLine(data.pfc, caloriesEaten)}
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
              ? EMPTY_DAY_TEXT
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
          <span aria-hidden="true"> →</span>
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
}: {
  data: RecentActivity;
  onOpen: () => void;
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

      {/* По макету: «Открыть запись». «Все мои записи» уехала в строку
          заголовка блока (Д11, решение владельца 22.09), «Перенести» с
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
