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
 *     новая запись / скорректировать план («Профиль» снят — DRF-2950)
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
import {
  approxPrefix,
  formatDuration,
  KCAL_TOTAL_AI_NOTE,
  KCAL_TOTAL_INCOMPLETE,
  KCAL_TOTAL_UNKNOWN,
  kcalTotalMarks,
  priceFromLabel,
} from "../lib/format";
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
import { ACTION_MARKS, PlanMark, PlanMarkIcon } from "../components/plan/PlanMark";
import { RecordIcon } from "../components/records/RecordIcons";
import { RecordButton } from "../components/records/RecordsState";
import { StatusBadge } from "../components/StatusBadge";
import { CustomerTabBar } from "../components/CustomerTabBar";
import { UnbookableBadge } from "../components/UnbookableNote";
import { useScreenBack } from "../hooks/useScreenBack";
import { getSavedPlan } from "../lib/plan-engine";
import { PLAN_LITE_COPY, PLAN_LITE_ROUTE } from "./PlanLiteScreen";
import { screenRoot } from "../lib/screen-back";

/** Одна цель из `wellness/today.active_goals` (cap=1, решение №13). */
type ActiveGoal = NonNullable<WellnessToday["active_goals"]>[number];

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
type PlanSlice =
  | { kind: "loading" }
  | { kind: "ok"; data: PlanLite | null }
  /**
   * DRF-2876 — у человека сохранён план нового механизма. Решение владельца
   * 09.10 (вариант «а»): блок «План на сегодня» и счёт действий на Главной
   * не показываются — они построены вокруг счётчиков прежнего плана, а у
   * нового плана счёта нет. Вход в план — карточка цели.
   */
  | { kind: "saved" }
  | { kind: "unavailable" };

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
  const [chatStuckAt, setChatStuckAt] = useState<"consent" | null>(null);

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
  //
  // Загрузок две (DRF-2950, решение владельца 12.11, п.11):
  //  * полная (`fetchAll`) — открытие экрана, «Обновить», отмена стакана:
  //    скелеты и все ручки, как было всегда;
  //  * тихая (`refreshQuietly`) — после принятого стакана: только то, что
  //    стакан меняет (сводка дня и план), без скелетов. Раньше после стакана
  //    не перечитывалось ничего, и «Вода» показывала старое число. Отказ
  //    тихого чтения показанное НЕ роняет: стакан принят, и не суметь его
  //    перечитать — не повод показать ошибку вместо чисел.
  //
  // Гонки. Сводку и план читают обе загрузки, ответы приходят в любом
  // порядке. Ответ применяется, только если он НОВЕЕ последнего применённого
  // для этого среза (`applied`); отказ поколение не продвигает — иначе
  // упавшее свежее чтение закрыло бы дорогу верному, но опоздавшему ответу.
  // Срезы, которые читает только полная загрузка (запись, тема, полка),
  // принимают ответ лишь последней полной: два «Обновить» подряд не дадут
  // старой записи затереть новую.
  //
  // Загрузчик не читает состояние срезов — только ref и функциональный
  // setState: иначе `fetchAll` менялся бы с каждым ответом, и эффект
  // `[fetchAll]` ниже перезапускал бы загрузку по кругу.
  const dayGen = useRef(0);
  const applied = useRef({ today: 0, plan: 0 });
  const fullGen = useRef(0);

  const load = useCallback(
    async (mode: "full" | "quiet") => {
      const quiet = mode === "quiet";
      const gen = ++dayGen.current;
      const full = quiet ? 0 : ++fullGen.current;
      if (!quiet) {
        setToday({ kind: "loading" });
        setActivity({ kind: "loading" });
        // Тёмная полка не «грузится» — её просто не спрашивают (DRF-2348).
        setRecs(shelfOn ? { kind: "loading" } : { kind: "not_requested" });
        setPlanSlice({ kind: "loading" });
      }

      // Per-slice isolation — Promise.allSettled so one failure doesn't
      // blank the other blocks. (Tau §5 State 5 partial render.)
      // `getCatalogBrowse` зовётся ТОЛЬКО при зажжённой полке: решение
      // владельца гасит полку, а обращение уходило всё равно — при каждом
      // открытии экрана, хотя показывать нечего (DRF-2348, §172 ответ 40).
      // Ветка `null` держит форму `allSettled` и порядок распаковки: так
      // включение полки возвращает запрос одной строкой в `aylaPicksShelfOn`,
      // а не раскопками.
      const todayP = getWellnessToday();
      const restP = quiet
        ? null
        : Promise.allSettled([
            getRecentActivity(),
            shelfOn ? getCatalogBrowse() : Promise.resolve(null),
          ]);
      const planP = getPlanLite();
      const topicP = quiet ? null : Promise.allSettled([getLastTopicAndChatLink()]);
      // DRF-2876 — есть ли сохранённый план нового механизма (`null` — нет
      // или механизм выключен на сервере).
      const savedP = getSavedPlan();
      const [[todayRes, planRes, savedPlanRes], rest, topic] = await Promise.all([
        Promise.allSettled([todayP, planP, savedP]),
        restP,
        topicP,
      ]);

      // ── сводка дня ──
      if (todayRes.status === "fulfilled") {
        if (gen > applied.current.today) {
          applied.current.today = gen;
          setToday({ kind: "ok", data: todayRes.value });
        }
      } else if (quiet) {
        const reason = loadErrorReason(todayRes.reason);
        setToday((prev) => (prev.kind === "loading" ? { kind: "error", reason } : prev));
      } else if (gen > applied.current.today) {
        setToday({ kind: "error", reason: loadErrorReason(todayRes.reason) });
      }

      // ── план ──
      // Выключен на сервере (`plan_lite_disabled`) или не ответил —
      // «недоступен», без ошибки на экране: Главная от плана не зависит.
      //
      // DRF-2876 — сохранённый новый план важнее прежнего: с ним счётчики
      // прежнего плана не показываются. Не удалось узнать, есть ли он, — о
      // плане молчим («недоступен»): показать счётчики прежнего плана рядом с
      // новым в «Моём плане» было бы противоречием.
      const plan: PlanSlice | null =
        savedPlanRes.status === "rejected"
          ? null
          : savedPlanRes.value
            ? { kind: "saved" }
            : planRes.status === "fulfilled"
              ? { kind: "ok", data: planRes.value }
              : null;
      if (plan) {
        if (gen > applied.current.plan) {
          applied.current.plan = gen;
          setPlanSlice(plan);
        }
      } else if (quiet) {
        setPlanSlice((prev) => (prev.kind === "loading" ? { kind: "unavailable" } : prev));
      } else if (gen > applied.current.plan) {
        setPlanSlice({ kind: "unavailable" });
      }

      // ── только полная загрузка ──
      if (!rest || !topic || full !== fullGen.current) return;
      const [activityRes, recsRes] = rest;
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

      const [topicRes] = topic;
      setLastTopic(topicRes.status === "fulfilled" ? topicRes.value.topic : null);
      setChatLink(topicRes.status === "fulfilled" ? topicRes.value.chatLink : null);
    },
    [shelfOn],
  );

  const fetchAll = useCallback(() => load("full"), [load]);

  // Тихие чтения склеиваются: одно в пути и, если за это время пришёл ещё
  // повод, ровно одно после него. Пять быстрых стаканов — два чтения, а не
  // пять: каждое — это три запроса к Ayla.
  const quietBusy = useRef(false);
  const quietAgain = useRef(false);
  const refreshQuietly = useCallback(async () => {
    if (quietBusy.current) {
      quietAgain.current = true;
      return;
    }
    quietBusy.current = true;
    try {
      do {
        quietAgain.current = false;
        await load("quiet");
      } while (quietAgain.current);
    } finally {
      quietBusy.current = false;
    }
  }, [load]);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  // ── online / offline transitions ─────────────────────────────────────
  //
  // Очередь стаканов уходит в двух случаях: сеть вернулась (Tau §11.8) и
  // экран открыт с сетью, а в очереди что-то лежит (DRF-2950, решение
  // владельца 12.11, п.11): сеть могла вернуться, пока приложение было
  // закрыто, и события `online` тогда не будет — стаканы ждали бы
  // следующего нажатия. Двойной отправки нет: проход очереди один на всех
  // (`flushInFlight`), ключ идемпотентности у стакана постоянный.
  // Отказы стаканов из очереди скажет подписка `onWaterQueueRefused`
  // (DRF-1919). Принятые стаканы меняют числа — их перечитываем тихо.
  const flushQueue = useCallback(() => {
    void flushWaterQueue()
      .then((accepted) => {
        setWaterQueueLen(readWaterQueue().length);
        if (accepted > 0) void refreshQuietly();
      })
      .catch(() => {
        // Очередь цела (стакан уходит из неё только принятым) — уйдёт в
        // следующий раз; индикатор продолжает говорить «ждёт».
      });
  }, [refreshQuietly]);

  useEffect(() => {
    if (isOnline() && readWaterQueue().length > 0) flushQueue();
  }, [flushQueue]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onOnline = () => {
      setOnline(true);
      flushQueue();
    };
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, [flushQueue]);

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
      // DRF-2950 (п.11) — числа воды и плана после стакана. «Отказан» тоже:
      // в том же проходе могли уйти стаканы очереди. «Остался в очереди» —
      // нет: сеть не ответила, и чтение не ответит тоже.
      if (outcome.kind === "accepted" || outcome.kind === "rejected") {
        void refreshQuietly();
      }
    });
  }, [online, refreshQuietly]);

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

  // В чат MAX — закрыть Mini App: приложение открыто из диалога с Ayla, и
  // закрытие возвращает туда. Так уходит ОДНА дверь — согласие на дневник:
  // приглашение к согласию бот присылает в диалог (DRF-2230), и дать его
  // можно только там. Фразу человек пишет сам: deep link с текстом в MAX не
  // существует (см. историю в StateError.tsx).
  // DRF-2266 — мост close() → ссылка на диалог бота → подсказка рядом с
  // нажатой кнопкой (web.max.ru: раньше `closeApp()` там молча ничего не
  // делал — «кнопка не работает»).
  const goToChat = useCallback(
    (where: "consent") => {
      const outcome = returnToChat(chatLink);
      setChatStuckAt(outcome === "stuck" ? where : null);
    },
    [chatLink],
  );
  // Разговор с Ayla продолжается здесь, в Mini App, — приложение не
  // закрывается: «Продолжить разговор» и «Задать новый вопрос» с DRF-2799
  // (решение владельца 06.10), «Скорректировать с Ayla» и «Скорректировать
  // план» — с DRF-2950 (решение владельца 12.11, п.10).
  const onChatTap = useCallback(() => navigate("/customer/ayla"), [navigate]);

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
  // DRF-2766 — записи без калорий — тоже записи: день не пустой.
  const hasAnyLogs =
    !!todayData &&
    ((caloriesEaten ?? 0) > 0 || (waterEaten ?? 0) > 0 || kcalTotalMarks(todayData).incomplete);
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

  // ── «План на сегодня»: что стоит в шапке блока (DRF-2950, п.4) ─────────
  //
  // Кнопки плана переехали из карточки цели в шапку блока плана (решение
  // владельца 12.11). Условия — прежние условия этих кнопок: «Продолжить
  // сегодняшний план» — когда план есть; «Составить план» и «Мой план»
  // (DRF-2876) — только при цели, как и раньше в её карточке. Без кнопки
  // блока нет: план недоступен или ещё грузится — о плане молчим.
  //
  // «Мой план» при сохранённом новом плане — шапка без строк: шаги нового
  // плана на Главной не дублируются (решение 09.10, вариант «а»). Вопрос
  // владельцу в DRF-2950 (12.11) — оставить так или вернуть «Мой план» под
  // строку цели.
  const planData = planSlice.kind === "ok" ? planSlice.data : null;
  const planHead: PlanHead | null = planData
    ? "continue"
    : planSlice.kind === "ok" && goal
      ? "compose"
      : planSlice.kind === "saved" && goal
        ? "mine"
        : null;

  // Стакан, добравший норму, снимает карточку шага вместе с нажатой кнопкой:
  // фокус ушёл бы в никуда, а блок плана поднялся бы под палец. Фокус —
  // на «Отменить» тоста: это следующее, что человеку может понадобиться.
  const stepTap = useRef(false);
  const undoRef = useRef<HTMLButtonElement>(null);
  const onStepWaterTap = useCallback(() => {
    stepTap.current = true;
    onWaterTap();
  }, [onWaterTap]);
  useEffect(() => {
    if (!undoEntryId || !stepTap.current || showTodayGoals) return;
    stepTap.current = false;
    undoRef.current?.focus();
  }, [undoEntryId, showTodayGoals]);

  // ── render ────────────────────────────────────────────────────────────
  //
  // DRF-2950 — вид по доскам владельца «Презентация UI Ayla…» (кадры 1–2, 5–7)
  // и «Мобильное приложение Ayla…» (кадры 1–3), решения 12.11. Раскладка —
  // с доски, слова и поведение — прежние. Порядок: цель → шаг на сегодня →
  // план → запись → быстрые действия → Ayla → согласие → сводка дня.
  return (
    <div className="wellness-dash" lang="ru">
      {/* Skip link — WCAG 2.4.1 (Tau §8 IMPORTANT #7). */}
      <a className="wellness-dash__skip-link" href="#wellness-main">
        К основному содержимому
      </a>

      {/* Шапка — решение владельца 12.11, п.1 (DRF-2950; заменяет Д1 /
          DRF-2331): фирменный знак слева, вход в профиль справа. Имя и «Рада
          тебя видеть!» ушли — экран здоровается один раз, заголовком ниже.

          Колокольчика доски НЕТ: ленты уведомлений у клиента не существует —
          ни ручки на сервере, ни экрана (есть только настройки уведомлений).
          Мёртвый control запрещён решением владельца (§61, М-4 п. 1) и
          DRF-1181. Фото-фона нет — изображений нет.

          Знак — строчное «ayla», как на всех экранах бренда
          (`ayla-identity-and-brand.md`, «lowercase «ayla» wordmark»;
          `docs/screens/customer-main-wellness-dashboard.md` §7). Доска
          пишет «Ayla» — это вопрос бренда, а не вёрстки. */}
      <header className="wellness-dash__header" role="banner">
        <div className="wellness-dash__brand">
          {/* «ayla» = English wordmark per Tau §7 — wrap in lang="en"
              to keep RU TTS from pronouncing it «Айла» (WCAG 3.1.2). */}
          <span className="wellness-dash__wordmark" lang="en">
            ayla
          </span>
          <span className="wellness-dash__brand-star">
            <RecordIcon name="sparkle" size={20} />
          </span>
        </div>
        {/* Фотографии клиента нет ни в одном контракте — кружок с
            инициалами (§77 п.60, общий для всех экранов панели). Имя —
            своё, с `wellness/today`: второй источник того же имени дал бы
            расхождение в момент загрузки. */}
        <CustomerAvatarEntry displayName={displayName} />
      </header>

      {/* «Без сети» — §5 State 4. Данные на экране есть, поэтому это полоса,
          а не состояние на весь экран (кадр 7 доски — для экранов без данных). */}
      {!online && (
        <div
          className="wellness-dash__offline-banner"
          role="status"
          aria-live="polite"
        >
          <RecordIcon name="offline" size={18} />
          <span>Без сети — показываю последние данные</span>
        </div>
      )}

      <main id="wellness-main" className="wellness-dash__main">
        {/* Приветствие + фраза дня (§11.9 + §11.10) — единственное. */}
        <section className="wellness-dash__greeting" aria-label="Приветствие">
          {today.kind === "loading" ? (
            <>
              <div
                className="skeleton wellness-dash__skel-line"
                style={{ width: "60%", height: 26 }}
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
                <span aria-hidden="true">👋</span>
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
              <RecordButton variant="soft" icon="text" onClick={onFoodTextTap}>
                Записать текстом
              </RecordButton>
            </div>
            <button
              type="button"
              className="wellness-dash__onboarding-dismiss"
              aria-label="Скрыть подсказку"
              onClick={onDismissOnboarding}
            >
              <span aria-hidden="true">×</span>
            </button>
          </section>
        )}

        {/* Цель — строка доски «Моя цель» (решение 12.11, п.2): слова прежние,
            нажатие — экран цели. Цель — из `wellness/today.active_goals`
            (три состояния, DRF-1476). Кнопок плана здесь больше нет — они в
            шапке блока «План на сегодня» (п.4). */}
        {today.kind === "loading" && <GoalSkeleton />}
        {today.kind === "ok" && (
          <GoalCard goal={goal} goalsKnown={goalsKnown} onGoal={onGoalTap} />
        )}

        {/* «Шаги на сегодня» — карточка «Твой следующий шаг» доски (решение
            12.11, п.3): своей логики «следующего шага» нет, это прежний блок
            «Ещё N стаканов до нормы» с кнопкой стакана. Место — второе, как на
            доске (прежде блок стоял после сводки дня: отклонение PR #1894,
            а не решение владельца; Д31 (б) решал только, что блок остаётся).
            Виден, только когда норма воды известна и не набрана; на пилоте
            нормы нет ни у кого — блок появится с ней. */}
        {showTodayGoals && today.kind === "ok" && (
          <section
            className="wellness-dash__today-goals"
            aria-labelledby="tg-header"
          >
            <h2 id="tg-header" className="wellness-dash__step-eyebrow">
              <RecordIcon name="sparkle" size={18} />
              Шаги на сегодня
            </h2>
            <div className="wellness-dash__step">
              <span className="wellness-dash__tile">
                <PlanMarkIcon kind="water" size={26} />
              </span>
              <p className="wellness-dash__step-title">
                Ещё {waterRemaining} {ruPluralWater(waterRemaining)} до нормы
              </p>
            </div>
            {/* Видимая подпись — «Стакан воды», как у быстрого действия; имя
                для скринридера несёт глагол: тап сразу пишет 250 мл. Одно
                действие — одно имя у всех трёх кнопок стакана (WCAG 3.2.4). */}
            <button
              type="button"
              className="cr-btn cr-btn--primary"
              aria-label="Добавить стакан воды"
              onClick={onStepWaterTap}
            >
              <RecordIcon name="plus" />
              Стакан воды
            </button>
          </section>
        )}

        {/* «План на сегодня» — строки доски (решение 12.11, п.4). */}
        {planHead && (
          <PlanToday
            head={planHead}
            plan={planData}
            diaryOff={diaryOff}
            onPlan={onPlanTap}
            onWater={onWaterTap}
            onAdjust={onChatTap}
          />
        )}

        {/* Ближайшая запись — раскладка доски (решение 12.11, п.7): значок
            вместо фото (фото процедуры на проводе нет), бейдж, адрес, «Открыть
            запись»; источник — `recent-activity`. */}
        <section
          className="wellness-dash__booking"
          aria-labelledby="booking-header"
        >
          {/* Д11 (решение владельца 22.09): «Все мои записи» — справа от
              заголовка. Только когда запись есть: у человека без записей
              кнопка вела бы в пустой список, а на загрузке и на ошибке —
              предлагала бы переход рядом со строкой «не удалось прочитать». */}
          <div className="wellness-dash__head">
            <h2 id="booking-header" className="wellness-dash__section-header">
              Ближайшая запись
            </h2>
            {activity.kind === "ok" && activity.data.next_booking && (
              <button
                type="button"
                className="wellness-dash__head-link"
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

        {/* Быстрые действия по фризу 25.08 (DRF-2144 п.4) — компактный ряд
            (решение 12.11, п.9): записать питание / стакан воды / новая
            запись / скорректировать план. «Профиль» снят — третий вход в
            профиль рядом с аватаром и вкладкой. «Добавить замер» из макета →
            «стакан воды» (§49: без веса). Без контура питания (DRF-2071)
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
                <span className="wellness-dash__qa-icon">
                  <PlanMarkIcon kind="food" />
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
                <span className="wellness-dash__qa-icon">
                  <PlanMarkIcon kind="water" />
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
              <span className="wellness-dash__qa-icon">
                <PlanMarkIcon kind="book" />
              </span>
              <span className="wellness-dash__qa-label">Новая запись</span>
            </button>
            <button
              type="button"
              className="wellness-dash__qa-btn wellness-dash__qa-btn--compact"
              aria-label="Скорректировать план"
              onClick={onChatTap}
            >
              <span className="wellness-dash__qa-icon">
                <RecordIcon name="ayla" size={22} />
              </span>
              <span className="wellness-dash__qa-label">Скорректировать план</span>
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
        </section>

        {/* Block A — «Продолжить разговор с Ayla» (DRF-2144 п.5; фриз п.4:
            превью — только реальный последний контекст, иначе нейтрально).
            DRF-2799 (решение владельца 06.10): обе кнопки ведут в разговор
            внутри Mini App (`/customer/ayla`). Нить та же, что в чате бота.
            Вечерний кадр доски («всё выполнено», фото Ayla) не строится —
            решение 12.11, п.8. */}
        <section className="wellness-dash__ayla" aria-labelledby="ayla-header">
          <h2 id="ayla-header" className="wellness-dash__section-header">
            Продолжить разговор с <span lang="en">Ayla</span>
          </h2>
          <div className="wellness-dash__ayla-card">
            {lastTopic && (
              <div className="wellness-dash__ayla-row">
                <span className="wellness-dash__mark">
                  <RecordIcon name="ayla" size={22} />
                </span>
                <div className="wellness-dash__ayla-text">
                  <div className="wellness-dash__ayla-topic-head">
                    <span>Последняя тема</span>
                    {topicWhen && (
                      <span className="wellness-dash__ayla-when">{topicWhen}</span>
                    )}
                  </div>
                  <p className="wellness-dash__ayla-topic">{lastTopic.text}</p>
                </div>
              </div>
            )}
            <RecordButton variant="primary" onClick={onChatTap}>
              Продолжить разговор
            </RecordButton>
            <RecordButton variant="text" onClick={onChatTap}>
              Задать новый вопрос
            </RecordButton>
          </div>
        </section>

        {/* ── Дневник и то, что при нём (Д32, решение владельца 22.09) ──

            Дневник идёт последним. Согласие стоит НАД сводкой дня, потому что
            оно её и закрывает (``consentRequired`` — единственный ключ, который
            гасит сводку). Ворота не могут стоять ниже двери, которую запирают. */}

        {/* Block C — согласие дневника: ОДИН блок (DRF-2144 п.6) вместо двух
            одинаковых абзацев в строках «Питание» и «Вода». */}
        {today.kind === "ok" && consentRequired && (
          <section
            className="wellness-dash__consent"
            aria-label="Согласие на дневник"
          >
            <div className="wellness-dash__consent-row">
              <span className="wellness-dash__mark">
                <RecordIcon name="lock" size={22} />
              </span>
              <p className="wellness-dash__consent-text">{DIARY_CONSENT_CARD_TEXT}</p>
            </div>
            <RecordButton
              variant="soft"
              onClick={() => void onConsentTap()}
              disabled={consentPromptBusy}
            >
              {DIARY_CONSENT_CARD_CTA}
            </RecordButton>
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
                <RecordButton variant="primary" onClick={() => goToChat("consent")}>
                  {CONSENT_PROMPT_OPEN_CHAT_CTA}
                </RecordButton>
              </>
            ) : null}
            {chatStuckAt === "consent" && <ChatStuckHint />}
          </section>
        )}

        {/* Block 2 — сводка дня (§11.1 — conditional БЖУ; решение 12.11, п.6:
            карточкой, без кольца, полоса калорий только при ориентире; п.5:
            вода в стаканах). Без согласия строк нет — их место занимает одна
            карточка согласия выше. */}
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
                <span className="wellness-dash__mark">
                  <RecordIcon name="info" size={22} />
                </span>
                <p>{DIARY_OFF_TEXT}</p>
              </div>
            )}
            {today.kind === "ok" && !diaryOff && (
              <PulseStrip data={today.data} />
            )}
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

      {/* Тост воды — над панелью, а не в потоке (DRF-2950): кнопок стакана
          три, две из них высоко на экране, и тост с «Отменить» у быстрых
          действий оказывался за краем. */}
      {waterToast && (
        <div
          className="wellness-dash__toast"
          role="status"
          aria-live="polite"
        >
          <span className="wellness-dash__toast-text">{waterToast}</span>
          {undoEntryId && (
            <button
              ref={undoRef}
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
  // DRF-2766 фаза 2 — итог включает оценки ИИ («≈ … включая оценки ИИ»),
  // записи без калорий делают его неполным; день прочитан, а итога нет —
  // «пока не посчитаны», а не «не удалось загрузить».
  const marks = kcalTotalMarks(data);
  const approx = approxPrefix(marks);
  const totalUnknown = !caloriesKnown && marks.incomplete;
  // DRF-2873 — «Без чисел» (решение владельца 04.10: на всех экранах).
  // Человек сам выбрал не видеть калории, БЖУ и числовые цели: строка
  // «Питание» тогда не рисует ни чисел, ни шкалы и не произносит их.
  // Только явное `true`: режим добровольный, и «не знаем выбора» не значит
  // «спрятать за человека» — то же правило, что у `numbers_hidden_for`.
  const hideNumbers = data.nutrition_numbers_hidden === true;
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
          hideNumbers
            ? dayIsEmpty
              ? `Питание: ${EMPTY_DAY_TEXT}`
              : !caloriesKnown && !totalUnknown
                ? `Питание: ${sliceClosedCopy}`
                : "Питание"
            : totalUnknown
            ? `Питание: ${KCAL_TOTAL_UNKNOWN}`
            : !caloriesKnown
            ? `Питание: ${sliceClosedCopy}`
            : dayIsEmpty
              ? // DRF-2288: скринридер слышит то же, что видно глазу, — не «0 из 2100».
                `Питание: ${EMPTY_DAY_TEXT}`
              : caloriesTargetKnown
                ? `Питание: ${marks.approx ? "примерно " : ""}${caloriesEaten} из ${caloriesTarget} килокалорий, ${caloriesPct} процентов${marks.approx ? `, ${KCAL_TOTAL_AI_NOTE}` : ""}${pfcSpoken}`
                : `Питание: ${marks.approx ? "примерно " : ""}${caloriesEaten} килокалорий сегодня${marks.approx ? `, ${KCAL_TOTAL_AI_NOTE}` : ""}`
        }
      >
        <div className="wellness-dash__pulse-head">
          <span className="wellness-dash__pulse-mark">
            <PlanMarkIcon kind="food" size={18} />
          </span>
          Питание
        </div>
        {hideNumbers ? (
          /* «Без чисел»: ни калорий, ни цели, ни процента, ни БЖУ, ни шкалы.
             Слова без чисел остаются — пустой день и закрытый дневник. */
          dayIsEmpty ? (
            <div className="wellness-dash__pulse-numbers">{EMPTY_DAY_TEXT}</div>
          ) : !caloriesKnown && !totalUnknown ? (
            <div className="wellness-dash__pulse-numbers">{sliceClosedCopy}</div>
          ) : null
        ) : caloriesKnown && caloriesTargetKnown ? (
          <>
            <div className="wellness-dash__pulse-numbers" aria-hidden="true">
              {dayIsEmpty
                ? EMPTY_DAY_TEXT
                : `${approx}${caloriesEaten} / ${caloriesTarget} ккал · ${caloriesPct} %`}
            </div>
            {!dayIsEmpty && (marks.approx || marks.incomplete) && (
              <div className="wellness-dash__pulse-pfc" aria-hidden="true" data-testid="pulse-kcal-note">
                {[marks.approx ? KCAL_TOTAL_AI_NOTE : null, marks.incomplete ? KCAL_TOTAL_INCOMPLETE : null]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            )}
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
          <>
            <div className="wellness-dash__pulse-numbers">
              {dayIsEmpty ? EMPTY_DAY_TEXT : `${approx}${caloriesEaten} ккал сегодня`}
            </div>
            {!dayIsEmpty && (marks.approx || marks.incomplete) && (
              <div className="wellness-dash__pulse-pfc" aria-hidden="true" data-testid="pulse-kcal-note">
                {[marks.approx ? KCAL_TOTAL_AI_NOTE : null, marks.incomplete ? KCAL_TOTAL_INCOMPLETE : null]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            )}
          </>
        ) : (
          /* Read failed - no numbers, no bar. «0 / 0 ккал · 0 %» would
             read as a logged-nothing day, which is a different fact.
             DRF-2766 — day read but no total: «пока не посчитаны». */
          <div className="wellness-dash__pulse-numbers">
            {totalUnknown ? KCAL_TOTAL_UNKNOWN : sliceClosedCopy}
          </div>
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
          <span className="wellness-dash__pulse-mark">
            <PlanMarkIcon kind="water" size={18} />
          </span>
          Вода
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

/** Период ведра — словами экрана плана: «Сегодня» / «На этой неделе» / «Эти 2 недели». */
const PLAN_CADENCE_PERIOD: Record<PlanLiteCadence, string> = {
  per_day: PLAN_LITE_COPY.today,
  per_week: PLAN_LITE_COPY.thisWeek,
  per_2_weeks: PLAN_LITE_COPY.twoWeeks,
};

/** Что стоит в шапке блока «План на сегодня» (DRF-2950, п.4). */
type PlanHead = "continue" | "compose" | "mine";

function GoalSkeleton() {
  return (
    <div className="wellness-dash__goal-card" aria-hidden="true">
      <span className="skeleton wellness-dash__skel-mark" />
      <div className="wellness-dash__goal-text">
        <div className="skeleton wellness-dash__skel-line" style={{ height: 12, width: "40%" }} />
        <div
          className="skeleton wellness-dash__skel-line"
          style={{ height: 16, width: "85%", marginTop: "var(--s-1)" }}
        />
      </div>
    </div>
  );
}

/**
 * Цель — строка доски «Моя цель» (решение владельца 12.11, п.2): значок,
 * название блока, цель словами человека, срок и «Неделя N», шеврон; нажатие —
 * экран цели. Слова прежние: «Активная цель» (решение 28.09, слова п.3), без
 * «Твоя цель:». Ни шкалы, ни процентов, ни счёта действий (§49/§82, решение
 * №13; п.2 и п.4 решений 12.11).
 *
 * Заголовок блока (`h2`) стоит ВНЕ кнопки — заголовок внутри кнопки
 * недопустим, а навигация по заголовкам не должна терять цель. Кнопка
 * растянута на всю карточку (`::after`), поэтому нажимается вся строка.
 */
function GoalCard({
  goal,
  goalsKnown,
  onGoal,
}: {
  goal: ActiveGoal | undefined;
  goalsKnown: boolean;
  onGoal: () => void;
}) {
  if (!goal) {
    // Нет цели / слой цели не ответил — та же строка, чтобы экран не «прыгал»
    // между состояниями. Три состояния — DRF-1476: «Выбери цель» говорится
    // ТОЛЬКО когда известно, что цели нет; стрелки у неё нет (§172, ответ 39).
    return (
      <section className="wellness-dash__goal-card" aria-labelledby="goal-header">
        <span className="wellness-dash__mark">
          <PlanMarkIcon kind="step" />
        </span>
        <div className="wellness-dash__goal-text">
          <h2 id="goal-header" className="wellness-dash__goal-eyebrow">
            Цель
          </h2>
          <p className="wellness-dash__goal-hint">
            {goalsKnown ? "Расскажи о себе — точнее советую" : UNAVAILABLE}
          </p>
        </div>
        {goalsKnown && (
          <button
            type="button"
            className="cr-btn cr-btn--soft wellness-dash__goal-pick"
            onClick={onGoal}
          >
            Выбери цель
          </button>
        )}
      </section>
    );
  }

  const due = formatGoalDue(goal.target_date);
  const week = goal.week_num ? `Неделя ${goal.week_num}` : "";
  return (
    <section
      className="wellness-dash__goal-card wellness-dash__goal-card--link"
      aria-labelledby="goal-header"
    >
      <span className="wellness-dash__mark">
        <PlanMarkIcon kind="step" />
      </span>
      <div className="wellness-dash__goal-text">
        <h2 id="goal-header" className="wellness-dash__goal-eyebrow">
          Активная цель
        </h2>
        <button type="button" className="wellness-dash__goal-row" onClick={onGoal}>
          <span className="wellness-dash__goal-title">{goal.title}</span>
          {/* DRF-2173 — срок «До 1 ноября 2026»; без срока его нет. Части —
              отдельными строками текста, разделитель — украшение. */}
          {(due || week) && (
            <span className="wellness-dash__goal-meta">
              {due && <span>{due}</span>}
              {due && week && <span aria-hidden="true"> · </span>}
              {week && <span>{week}</span>}
            </span>
          )}
        </button>
      </div>
      <span className="wellness-dash__chevron">
        <RecordIcon name="chevron" />
      </span>
    </section>
  );
}

/**
 * «План на сегодня» — строки доски (решение владельца 12.11, п.4).
 *
 * * Кнопка плана — в шапке блока: «Продолжить сегодняшний план →» (Д6:
 *   стрелка — украшение) / «Составить план» / «Мой план» (без стрелки, §172
 *   ответ 39). Счёта «N из 3» в шапке нет.
 * * Строка: значок действия в круге, подпись, период и «N из M». Сделано не
 *   меньше нужного — круг с галочкой («✓» у всех действий, как раньше).
 * * Вода — «+» справа: то же действие, что «Стакан воды». Полосы нет:
 *   на доске она — литры против нормы, а здесь счёт плана; нормы воды на
 *   пилоте нет ни у кого, и §82 запрещает шкалу без ориентира. Экран «План»
 *   её тоже не рисует.
 * * Остальные строки — целиком кнопка на экран плана, шеврон справа.
 */
function PlanToday({
  head,
  plan,
  diaryOff,
  onPlan,
  onWater,
  onAdjust,
}: {
  head: PlanHead;
  plan: PlanLite | null;
  diaryOff: boolean;
  onPlan: () => void;
  onWater: () => void;
  onAdjust: () => void;
}) {
  return (
    <section className="wellness-dash__plan-today" aria-labelledby="plan-today-header">
      <div className="wellness-dash__head">
        <h2 id="plan-today-header" className="wellness-dash__section-header">
          План на сегодня
        </h2>
        <button type="button" className="wellness-dash__head-link" onClick={onPlan}>
          {head === "continue" ? (
            <>
              Продолжить сегодняшний план
              <span aria-hidden="true"> →</span>
            </>
          ) : head === "compose" ? (
            "Составить план"
          ) : (
            // DRF-2876 — сохранён новый план: вход в него; подпись — та же,
            // что у раздела.
            PLAN_LITE_COPY.entryFromDashboard
          )}
        </button>
      </div>
      {head === "compose" && <p className="wellness-dash__plan-empty">План ещё не составлен</p>}
      {plan && (
        <ul className="wellness-dash__plan-list">
          {plan.actions.map((a) => {
            const label = PLAN_ACTION_LABELS[a.action_type];
            const done = a.done_count >= a.target_count;
            const text = (
              <span className="wellness-dash__plan-text">
                <span className="wellness-dash__plan-label">{label}</span>
                <span className="wellness-dash__plan-period">
                  {PLAN_CADENCE_PERIOD[a.cadence]}
                  {done ? (
                    // «✓» словом — для скринридера; глазу его говорит круг.
                    <span className="visually-hidden">, выполнено</span>
                  ) : (
                    <>
                      <span aria-hidden="true"> · </span>
                      <span className="wellness-dash__plan-count">
                        {PLAN_LITE_COPY.ofTotal(a.done_count, a.target_count)}
                      </span>
                    </>
                  )}
                </span>
              </span>
            );
            const mark = done ? (
              <span className="wellness-dash__mark wellness-dash__mark--done">
                <RecordIcon name="check" size={22} />
              </span>
            ) : (
              <PlanMark kind={ACTION_MARKS[a.action_type]} />
            );
            if (a.action_type === "log_water" && !diaryOff) {
              return (
                <li key={a.action_type} className="wellness-dash__plan-item">
                  {mark}
                  {text}
                  {/* Повторяемое действие: «+» остаётся и после нормы. */}
                  <button
                    type="button"
                    className="wellness-dash__plan-add"
                    aria-label="Добавить стакан воды"
                    onClick={onWater}
                  >
                    <RecordIcon name="plus" size={22} />
                  </button>
                </li>
              );
            }
            return (
              <li key={a.action_type}>
                <button
                  type="button"
                  className="wellness-dash__plan-item wellness-dash__plan-item--go"
                  onClick={onPlan}
                >
                  {mark}
                  {text}
                  <span className="wellness-dash__chevron">
                    <RecordIcon name="chevron" />
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {/* Attention-состояние по фризу 25.08 п.2 — человеческим языком. Кнопка
          ведёт в разговор с Ayla внутри Mini App (DRF-2950, решение владельца
          12.11, п.10); что именно поправить, человек пишет сам — контекст в
          разговор не передаётся. */}
      {plan && (
        <div className="wellness-dash__plan-attention" role="note">
          <p className="wellness-dash__plan-attention-text">Сегодня не получается по плану?</p>
          <RecordButton variant="soft" icon="ayla" onClick={onAdjust}>
            Скорректировать с Ayla
          </RecordButton>
        </div>
      )}
    </section>
  );
}

function BookingSkeleton() {
  return (
    <div className="wellness-dash__booking-card" aria-hidden="true">
      <div className="wellness-dash__booking-main">
        <span className="skeleton wellness-dash__skel-tile" />
        <div className="wellness-dash__booking-text">
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
      </div>
    </div>
  );
}

function BookingEmpty({ onBook }: { onBook: () => void }) {
  // По макету H01 и кадру 5 доски: значок, «Записей нет», «Записаться» →
  // каталог. Прежняя заглушка «Подобрать услугу под твою цель…» обещала
  // подбор, которого на Главной нет (C01: Home — не второй каталог).
  return (
    <div className="wellness-dash__booking-card wellness-dash__booking-card--empty">
      <span className="wellness-dash__tile">
        <RecordIcon name="calendar" size={26} />
      </span>
      <p className="wellness-dash__booking-empty">Записей нет</p>
      <RecordButton variant="primary" onClick={onBook}>
        Записаться
      </RecordButton>
    </div>
  );
}

/** «мастер {Имя} · {салон}» — п.2 решений владельца 28.09, дословная форма. */
export function bookingWhoText(
  masterName: string | null | undefined,
  salonName: string | null | undefined,
): string {
  const name = masterName?.trim() ?? "";
  const salon = salonName?.trim() ?? "";
  return [name ? `мастер ${name}` : "", salon].filter(Boolean).join(" · ");
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
      {/* Раскладка доски: плитка слева (значок вместо фото процедуры — поля
          картинки у услуги нет ни в контракте Ayla, ни в зеркале), справа
          услуга, мастер и салон, время, адрес и цена. */}
      <div className="wellness-dash__booking-main">
        <span className="wellness-dash__tile">
          <RecordIcon name="calendar" size={26} />
        </span>
        <div className="wellness-dash__booking-text">
          <div className="wellness-dash__booking-what">
            {b.service_name}
            {b.duration_min ? ` · ${b.duration_min} мин` : ""}
          </div>
          {status && (
            <div className="wellness-dash__booking-badges">
              <StatusBadge rendering={status.rendering} />
            </div>
          )}
          {/* Решение владельца 28.09 (слова, п.2): «мастер {Имя}», имя не
              склоняется; форма одна на чат, «Мои записи» и эту строку. Нет
              имени или салона — без висящего «мастер » / « · ». */}
          <div className="wellness-dash__booking-who">
            {bookingWhoText(b.master_name, b.salon_name)}
          </div>
          <div className="wellness-dash__booking-when">{b.date_human}</div>
          {/* Адрес — три состояния, и на экране их два разных текста
              (DRF-1611). Строка есть ВСЕГДА: человек идёт на визит, и нужда
              у него одна и та же независимо от того, чей это пробел. */}
          <div className="wellness-dash__booking-where">
            <span>{visitAddressText(b.address)}</span>
            {price && (
              <>
                <span aria-hidden="true"> · </span>
                <span className="wellness-dash__booking-price">{price}</span>
              </>
            )}
          </div>
          {/* §11.3 — multi-record indicator. */}
          {moreThisWeek && (
            <div className="wellness-dash__booking-more">
              Ещё {data.this_week_booking_count - 1}{" "}
              {ruPluralBooking(data.this_week_booking_count - 1)} на этой неделе
            </div>
          )}
        </div>
      </div>

      {/* «Открыть запись» остаётся (решение 12.11, п.7). Шеврона доски нет:
          строка-кнопка рядом с этой кнопкой дала бы два одинаковых перехода.
          «Все мои записи» — в строке заголовка блока (Д11), «Перенести» с
          Главной снято — перенос живёт в карточке записи. */}
      <RecordButton variant="soft" onClick={onOpen}>
        Открыть запись
      </RecordButton>
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
  // Вид — состояние раздела клиента в размер блока: значок в круге, текст,
  // кнопка. Заголовка нет и фокус не переводится: ошибок на экране может
  // быть две, и тянуть фокус друг у друга им нельзя.
  return (
    <div className="wellness-dash__block-error" role="status" aria-live="polite">
      <span className="wellness-dash__mark wellness-dash__mark--danger">
        <RecordIcon name={reason.kind === "network" ? "offline" : "alert"} size={22} />
      </span>
      {reason.kind === "auth" && (
        <p className="wellness-dash__block-error-title">{authErrorCopy(reason.slug).title}</p>
      )}
      <p>{text}</p>
      <RecordButton variant="soft" onClick={onRetry}>
        Обновить
      </RecordButton>
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
