/**
 * Клиентская библиотека воронки записи — реальные данные зеркала.
 *
 * Спека экранов: `docs/screens/customer-booking-flow.md` §1–§10.
 * Спека подбора: `docs/specs/RECOMMENDATION_RESOLVER_CONTRACT_v1.0.md`.
 *
 * # Что этот файл перестал делать 08.09.2026 (DRF-1568, T7)
 *
 * Он перестал быть третьим авторитетом ранжирования.
 *
 * Прежняя версия получала `{service_id, score}`, **сортировала сама**
 * по убыванию балла и склеивала WHY из строк, которые присылал
 * источник. Каждое из трёх — политика: порядок, отбор и формулировка
 * причины. Контракт (§2) отнимает политику у потребителя целиком, и
 * взамен даёт `ordered[]` — уже упорядоченный, с `tier`, кодами причин
 * и свидетельствами. Сырых баллов в ответе больше нет вовсе (§4.3),
 * так что собрать свой порядок здесь физически не из чего — это
 * сделано намеренно, а не по забывчивости источника.
 *
 * Прежний докстринг этого файла утверждал: «NO backend ever produced
 * that shape … endpoint returns `{service_id, score}` rows only».
 * **С 07.09.2026 это неверно**, и неверность обошлась дорого: источник
 * отдавал `{data:{layer_1_your_places, layer_2_ayla_picks,
 * layer_3_explore}}`, потребитель звал `.slice()` на `undefined`,
 * `TypeError` улетал в пустой `catch {}`, и блок «Ayla подобрала» не
 * рендерился **вовсе** — при том, что эндпоинт отвечал 200 и месяц
 * считался работающим. Замер эндпоинта не является замером экрана.
 *
 * # Три исхода, и делить ветку им нельзя (§9.4, DRF-1556)
 *
 * `OK` / `UNAVAILABLE` / `CONTRACT_VIOLATION` разводятся не эвристикой,
 * а устройством: `try` накрывает **ровно транспорт**, проверка формы
 * стоит после него и по определению видит только доставленные ответы.
 * Ложного `CONTRACT_VIOLATION` при обычном отказе не может быть не
 * потому, что мы стараемся, а потому что состояние туда не доходит.
 *
 * У молчания в этих двух состояниях противоположная цена:
 *
 * * `UNAVAILABLE` — подбор есть необязательное украшение, молчание
 *   законно, а детектор, кричащий на каждый мёртвый источник, глушат
 *   за неделю, и он молчит ровно тогда, когда нужен;
 * * `CONTRACT_VIOLATION` — «мы и они разошлись в том, о чём
 *   договорились», и это обязано быть громким.
 *
 * `picks` остаётся пустым в обоих: рекомендация не подделывается.
 *
 * # WHY собирается из утверждённых кодов, а не из присланной фразы
 *
 * Граница отдаёт `reason_codes` (закрытый реестр §7.2) и `evidence`;
 * строки для показа человеку в ответе нет и быть не должно — её
 * наличие само по себе нарушение формы (§8.4 E1). Фразу собирает
 * представление, и собирает **только** по коду, который источник
 * утвердил.
 *
 * Гейт владельца 25.08 сохраняется дословно: «Нет displayable WHY →
 * нет блока „Ayla подобрала"». Разница в том, откуда берётся WHY:
 * раньше — из строки источника, теперь — из кода. Общая фраза
 * («подходит тебе», «Ayla рекомендует») не подставляется никогда:
 * §73 — имя производной не доказывает происхождение, и объяснение
 * допускается только за утверждённым кодом.
 *
 * # Ярусы: равные кандидаты остаются равными
 *
 * `tier` доезжает до экрана нетронутым. Равный `tier` означает
 * НЕРАЗЛИЧЁННЫХ кандидатов, и поверхность не вправе называть первого
 * из яруса лучшим (§4.3, решение владельца §29.3). Эта библиотека не
 * переставляет и не помечает никого: порядок — как прислали, признака
 * «лучший» в {@link ServicePick} нет вовсе, и завести его нечем.
 */

import {
  fetchMaster,
  fetchMasters,
  fetchRecommendations,
  fetchServices,
  fetchSlots,
  createBooking,
  decisionContractViolation,
  NOT_CAPABLE_CODE,
  NOT_RECOMMENDABLE_CODE,
  SAFETY_EXCLUDED_CODE,
} from "./api";
import type {
  Master,
  MasterDetail,
  FreeSlot,
  CreatedBooking,
  RankedCandidate,
  RecommendationDecision,
  Service,
} from "./api";

// ---------------------------------------------------------------------------
// Catalog browse — зеркало каталога + решение резолвера.
// ---------------------------------------------------------------------------

/**
 * Словарь отрисовки: утверждённый код → фраза человеку.
 *
 * Это та самая «заглушка, возвращающая шаблон по коду», которой §7.3
 * проверяет, что отрисовка не порождает утверждений о мире: подмена
 * модели на этот словарь не меняет ни одного семантического
 * утверждения на экране, потому что все утверждения уже выбраны
 * детерминированно и переданы закрытым перечислением.
 *
 * `null` означает «код есть, но человеку он не причина». Таких два
 * рода, и оба намеренные:
 *
 * * **механика и внутренние состояния** — `TIE_*`, `*_UNKNOWN`,
 *   `*_UNCONFIRMED`, `MATCH_UNDETERMINED`, `CONTEXT_NOT_APPLICABLE`:
 *   превратить их во фразу значило бы выдать отсутствие сведений за
 *   довод;
 * * **коды исключения** — `ELIG_EXCLUDED_*`, `SCOPE_EXCLUDED_*`: их
 *   место в `excluded[]` (§4.4), и в `ordered[]` они не появляются.
 *   Перечислены здесь ради полноты: словарь обязан покрывать реестр
 *   целиком, иначе «нет фразы» перестанет отличаться от «нет кода».
 *
 * Отдельно `QUALITY_RATING_SUBSTANTIATED`: фразы у него нет не по
 * недосмотру. Порог `N_substantiated` владельцем не назван, поэтому
 * `CONFIRMED` не выдаётся вовсе и стадия качества молчит для всех
 * (§8.3). Придумывать формулировку до решения по DRF-1527 значило бы
 * снова сделать рейтинг основанием — то самое, чего в реестре нет и
 * не будет (§7.2).
 *
 * Код вне реестра фразы не даёт и нарушением формы **не** считается:
 * реестр версионируется, и «мы отстали от источника» — не повод
 * подставить человеку выдуманную причину.
 */
const REASON_PHRASES: Readonly<Record<string, string | null>> = {
  // — область поиска
  SCOPE_WITHIN_CITY: "В твоём городе",
  SCOPE_WITHIN_REQUESTED_AREA: "В районе, который ты назвала",
  SCOPE_WITHIN_TENANT: null,
  SCOPE_CROSS_TENANT_ALLOWED: null,
  SCOPE_GEO_UNKNOWN_EXCLUDED: null,
  SCOPE_EXCLUDED_OUT_OF_CITY: null,
  SCOPE_EXCLUDED_OUT_OF_TENANT: null,
  SCOPE_EXCLUDED_OUT_OF_AREA: null,

  // — допустимость
  ELIG_CAPABILITY_VERIFIED: "Делает именно это",
  ELIG_ACTIVE_OFFER: "Услуга сейчас доступна",
  ELIG_WITHIN_STATED_BUDGET: "Укладывается в названный бюджет",
  ELIG_SAFETY_CLEARED: null,
  ELIG_EXCLUDED_INACTIVE: null,
  ELIG_EXCLUDED_NOT_CAPABLE: null,
  ELIG_EXCLUDED_SAFETY: null,
  ELIG_EXCLUDED_BUDGET: null,
  ELIG_EXCLUDED_NOT_RECOMMENDABLE: null,

  // — соответствие нужде
  MATCH_SERVICE_EXACT: "Это та услуга, которую ты искала",
  MATCH_SERVICE_PARTIAL: "Близко к тому, что ты искала",
  MATCH_CAPABILITY_ONLY: "Делает такие услуги",
  MATCH_GOAL_CATEGORY: "Подходит под твою цель",
  MATCH_UNDETERMINED: null,

  // — исполнимость
  EXEC_BOOKABLE: "Можно записаться",
  EXEC_SLOT_CONFIRMED_IN_WINDOW: "Есть свободное время в нужном окне",
  EXEC_PRICE_INTENT_APPLIED: "Цена учтена по твоему запросу",
  EXEC_SCHEDULE_UNCONFIRMED: null,
  EXEC_PRICE_UNKNOWN: null,

  // — личный контекст
  CONTEXT_PRIOR_COMPLETED_VISIT: "Ты уже здесь была",
  CONTEXT_PRIOR_SAME_CATEGORY: "Ты уже брала такие услуги",
  CONTEXT_NOT_APPLICABLE: null,

  // — качество
  QUALITY_RATING_SUBSTANTIATED: null,
  QUALITY_RATING_UNSUBSTANTIATED_IGNORED: null,
  QUALITY_NO_EVIDENCE: null,

  // — ничьи
  TIE_ROTATION_APPLIED: null,
  TIE_TIER_SHARED: null,
};

/** Решение владельца 25.08: WHY — «2–3 коротких», не стена текста. */
const MAX_REASONS = 3;

/**
 * Одна рекомендация, доведённая до экрана.
 *
 * Признака «лучший» здесь нет и не будет: превосходство выражается
 * ярусом, а не пометкой, и решает его резолвер, а не эта библиотека.
 */
export interface ServicePick {
  /** Идентификатор услуги в зеркале — экраны склеивают по нему. */
  serviceId: string;
  /**
   * Ярус §4.3. Равный `tier` — НЕРАЗЛИЧЁННЫЕ кандидаты, которых
   * поверхность обязана показать равноправными.
   */
  tier: number;
  /** Позиция в `ordered[]`, начиная с 1. Порядок, а не превосходство. */
  rank: number;
  /** Коды как прислал источник — по ним ключуется аналитика, не по фразам. */
  reasonCodes: string[];
  /** Фразы, собранные из кодов. Непусто: без WHY кандидат сюда не доходит. */
  reasons: string[];
}

export interface CatalogBrowseData {
  /** Активные услуги из зеркала бота (дословно). */
  services: Service[];
  /** Мастера из зеркала бота (дословно). */
  masters: Master[];
  /**
   * Рекомендации в порядке резолвера, суженные до услуг, которые есть
   * в зеркале и которым нашлась хоть одна фраза WHY. Пусто, когда
   * источник недоступен, разошёлся с контрактом или объяснить ему
   * нечего — во всех трёх случаях блок скрывается.
   */
  picks: ServicePick[];
  /**
   * Почему `picks` именно такой — ШЕСТЬ различимых исходов, и свести
   * любые два значило бы вернуть то самое смешение отсутствия с нулём.
   *
   * | исход | что произошло | повтор | что чинить |
   * |---|---|---|---|
   * | `OK` | источник ответил, полка построена | — | — |
   * | `UNAVAILABLE` | источник не ответил | осмыслен | ждать |
   * | `CONTRACT_VIOLATION` | ответил не в той форме | бессмыслен | источник |
   * | `NO_VERIFIED_CANDIDATES` | связи не подтверждены (§76) | бессмыслен | разметку каталога |
   * | `SAFETY_BLOCKED` | гейт безопасности закрыл выдачу | бессмыслен | **ничего** |
   * | `NO_CAPABLE_CANDIDATES` | нужда названа, никто не совпал | бессмыслен | запрос |
   * | `UNRENDERABLE_CANDIDATES` | кандидаты есть, полка их не умеет | бессмыслен | **нас** |
   *
   * Последний — единственный, где виноват потребитель, а не источник и
   * не данные. Он существует потому, что замер 08.09 показал: источник
   * отдаёт `kind=PROVIDER` с ключами Ayla
   * (`users/recommendation_source.py:209`), а полка умеет `kind=SERVICE`
   * с ключами зеркала (`apps/miniapp_api/views.py:695`). Без имени это
   * состояние выглядело бы как `OK` с пустой полкой — и всплыло бы не
   * сейчас, а через недели, когда кто-то разметит связи и будет ждать,
   * что полка загорится. Разбор — `bus/CLIENT-SURFACE-note-resolver-
   * candidate-kind-mismatch.md`.
   *
   * Последние три — пустая полка, и снаружи они **неразличимы**: 200 и
   * пустой `ordered[]` у всех трёх. Различает их только код исключения,
   * а цена путаницы разная: `SAFETY_BLOCKED`, названный «нет
   * подтверждённых связей», отправил бы человека чинить разметку там,
   * где гейт сработал верно.
   *
   * Все четыре пустоты — **штатные результаты, а не ошибки** (решение
   * владельца §76). Человеку во всех показывается предусмотренное
   * НЕперсонализированное состояние: каталог и запись по прямому
   * выбору, без слова «подходит» (§10.2). Экран вправе развести их
   * по-разному; сводить обратно — нельзя.
   */
  picksOutcome: PicksOutcome;
  /**
   * DRF-1482 — `empty_reason` ровно как прислал `GET /services`
   * (`null`, когда каталогу есть что предложить или бэкенд старее
   * поля). Проходит НЕПРОВЕРЕННЫМ: сопоставление значений состояниям —
   * дело `lib/customer-catalog-empty.ts`, поэтому новая причина от
   * сервера доезжает до экрана нетронутой.
   */
  emptyReason?: string | null;
}

/**
 * Собрать фразы WHY одного кандидата из его утверждённых кодов.
 *
 * Ничего не сочиняет и ничего не переводит вольно: код без фразы
 * просто не даёт строки. Пустой результат означает «показать нечего»,
 * и гейт владельца 25.08 отсекает такого кандидата — это тот же гейт,
 * что стоял здесь до контракта, а не новый фильтр.
 */
function displayableReasons(candidate: RankedCandidate): string[] {
  const phrases: string[] = [];
  for (const code of candidate.reason_codes) {
    const phrase = REASON_PHRASES[code];
    if (typeof phrase === "string" && !phrases.includes(phrase)) {
      phrases.push(phrase);
    }
    if (phrases.length === MAX_REASONS) break;
  }
  return phrases;
}

/**
 * Исходы границы §9.4 — именами контракта, а не своими.
 *
 * Три первых объявлены §9.4; четвёртый пришёл решением владельца §76 и
 * лежит НЕ рядом с ними, а внутри `OK`: на проводе это конформный
 * ответ, а не отказ. Именно поэтому он появляется после проверки формы,
 * а не вместо неё.
 */
type RecommendationsOutcome =
  | { state: "UNAVAILABLE" }
  | { state: "CONTRACT_VIOLATION"; violation: string }
  | { state: "OK"; decision: RecommendationDecision };

export type PicksOutcome =
  | "OK"
  | "UNAVAILABLE"
  | "CONTRACT_VIOLATION"
  | "NO_VERIFIED_CANDIDATES"
  | "SAFETY_BLOCKED"
  | "NO_CAPABLE_CANDIDATES"
  | "UNRENDERABLE_CANDIDATES";

/**
 * Три пустоты, неразличимые снаружи, и почему их всё же три.
 *
 * `200` и пустой `ordered[]` выглядят одинаково во всех трёх случаях.
 * Различает их только код — и цена путаницы разная у каждой пары:
 *
 * | код | причина | что чинить |
 * |---|---|---|
 * | `ELIG_EXCLUDED_NOT_RECOMMENDABLE` | связь не подтверждена (§76) | разметку каталога |
 * | `ELIG_EXCLUDED_SAFETY` | заявление `NOT_APPLICABLE` отвергнуто содержанием | **ничего: гейт сработал** |
 * | `ELIG_EXCLUDED_NOT_CAPABLE` | нужда названа, никто не совпал | запрос, не систему |
 *
 * Назвать безопасность «нет подтверждённых связей» значило бы послать
 * человека чинить разметку там, где сработал медицинский гейт, — одно
 * имя на два состояния, то самое, против чего эти исходы и заведены.
 *
 * **Читаются ДВА места, и это не перестраховка.** Реализация резолвера
 * (`recommendation/_pipeline.py::_decision_codes`) поднимает на уровень
 * решения только первые два кода; `ELIG_EXCLUDED_NOT_CAPABLE` живёт
 * исключительно в `excluded[]`. Потребитель, читающий одни лишь коды
 * решения, превратил бы третью пустоту обратно в `OK` с пустой полкой.
 *
 * **Старшинство при нескольких кодах сразу** (решение отвергло часть
 * кандидатов по безопасности, а часть — по неподтверждённой связи):
 * безопасность старше. Она — единственная из трёх, про которую верно
 * «чинить нечего», и потерять её за более громким соседом опаснее, чем
 * наоборот. Это выбор потребителя, а не буква контракта: контракт
 * допускает оба кода одновременно и старшинства не назначает.
 */
function classifyEmptiness(decision: RecommendationDecision): PicksOutcome {
  if (decision.ordered.length > 0) return "OK";
  const decisionCodes = decision.reason_codes ?? [];
  const excludedCodes = (decision.excluded ?? []).map((e) => e.reason_code);
  if (decisionCodes.includes(SAFETY_EXCLUDED_CODE)) return "SAFETY_BLOCKED";
  if (decisionCodes.includes(NOT_RECOMMENDABLE_CODE)) return "NO_VERIFIED_CANDIDATES";
  if (excludedCodes.includes(SAFETY_EXCLUDED_CODE)) return "SAFETY_BLOCKED";
  if (excludedCodes.includes(NOT_RECOMMENDABLE_CODE)) return "NO_VERIFIED_CANDIDATES";
  if (excludedCodes.includes(NOT_CAPABLE_CODE)) return "NO_CAPABLE_CANDIDATES";
  // Пустота без единого кода — «никто не подошёл» без объяснения.
  // Приписать ей чужое имя значило бы выдумать причину.
  return "OK";
}

/**
 * Сходить за решением и классифицировать результат.
 *
 * Различие структурно, а не эвристично: `try` накрывает ТРАНСПОРТ и
 * ничего кроме, поэтому всё, что отвергается, — по построению «источник
 * не ответил», а проверка формы видит только доставленные ответы.
 * Внутри `catch` тоже не живёт ничего лишнего, так что спрятать в той
 * же ветке уже наш собственный дефект — как прятался `TypeError` от
 * `undefined.slice()` — здесь больше нечем.
 */
async function loadRecommendations(): Promise<RecommendationsOutcome> {
  let payload: unknown;
  try {
    payload = await fetchRecommendations();
  } catch {
    /* Источник недоступен — необязательное украшение; не подделываем и
       не шумим: это состояние, чьё молчание законно. */
    return { state: "UNAVAILABLE" };
  }
  const violation = decisionContractViolation(payload);
  if (violation !== null) return { state: "CONTRACT_VIOLATION", violation };
  return {
    state: "OK",
    decision: (payload as { data: RecommendationDecision }).data,
  };
}

/**
 * Собрать всё, что нужно экрану каталога.
 *
 * Отказ зеркала отвергается (экран рисует своё состояние ошибки);
 * недоступный источник подбора уходит в `picks: []` молча, расхождение
 * контракта — в `picks: []` громко.
 */
export async function getCatalogBrowse(): Promise<CatalogBrowseData> {
  const [servicesRes, mastersRes] = await Promise.all([
    fetchServices(),
    fetchMasters(),
  ]);
  const { picks, picksOutcome } = await resolveCatalogPicks(servicesRes.services);
  return {
    services: servicesRes.services,
    masters: mastersRes.masters,
    picks,
    picksOutcome,
    emptyReason: servicesRes.empty_reason ?? null,
  };
}

/**
 * Только подбор — без повторного чтения услуг и мастеров (DRF-1768).
 *
 * «Попробовать снова» на отказе источника обязано повторять именно
 * запрос рекомендаций, а не перезагружать экран: услуги и мастера
 * пришли из зеркала и ни в чём не виноваты. Вынесено из
 * :func:`getCatalogBrowse`, чтобы у экрана не было второго способа
 * посчитать picks — один код, два вызывающих.
 */
export async function resolveCatalogPicks(
  services: Service[],
): Promise<{ picks: ServicePick[]; picksOutcome: PicksOutcome }> {
  let picks: ServicePick[] = [];
  const recs = await loadRecommendations();
  let picksOutcome: PicksOutcome = recs.state;
  if (recs.state === "CONTRACT_VIOLATION") {
    // Единственный канал, который есть у `apps/miniapp/src` (ни Sentry,
    // ни трекера — заводить их под эту задачу запрещено DRF-1556).
    // Одна строка, одно состояние: что объявляли и что пришло.
    // eslint-disable-next-line no-console
    console.error(
      "[recommendations] расхождение контракта — источник ответил, но не в " +
        "форме, которую объявляет apps/miniapp/src/lib/api.ts::" +
        "RecommendationDecision: " +
        recs.violation +
        ". Подбор остаётся пустым (никогда не подделывается); см. docs/specs/" +
        "RECOMMENDATION_RESOLVER_CONTRACT_v1.0.md §9.4 (DRF-1568).",
    );
  } else if (recs.state === "OK" && recs.decision.ordered.length === 0) {
    // Штатные состояния с именами, а не дефект и не пустота по ошибке.
    // Ни строчки в журнал: шум здесь обесценил бы детектор расхождения,
    // стоящий рядом.
    picksOutcome = classifyEmptiness(recs.decision);
  } else if (recs.state === "OK") {
    const known = new Set(services.map((s) => s.id));
    // Кандидаты, которые эта поверхность вообще способна показать:
    // услуга (не мастер, не слот) и притом известная зеркалу.
    const renderable = recs.decision.ordered.filter(
      (c) => c.candidate.kind === "SERVICE" && known.has(c.candidate.id),
    );
    if (renderable.length === 0) {
      // Источник ответил, кандидаты есть — и ни одного из них полка
      // отрисовать не может. Это НЕ «нечего показать»: это «нам
      // прислали то, чего мы не умеем», и молчать об этом нельзя.
      // eslint-disable-next-line no-console
      console.error(
        "[recommendations] решение содержит " +
          `${recs.decision.ordered.length} кандидат(ов), и ни один не ` +
          "отрисуем этой полкой: она умеет kind=SERVICE и ключи зеркала " +
          `(kinds: ${[...new Set(recs.decision.ordered.map((c) => c.candidate.kind))].join(",")}). ` +
          "Подбор остаётся пустым; см. bus/CLIENT-SURFACE-note-resolver-" +
          "candidate-kind-mismatch.md.",
      );
      picksOutcome = "UNRENDERABLE_CANDIDATES";
    }
    picks = renderable
      // Порядок НЕ трогается: он пришёл готовым, и пересобрать его не
      // из чего — баллов в ответе нет (§4.3).
      .map((c) => ({
        serviceId: c.candidate.id,
        tier: c.tier,
        rank: c.rank,
        reasonCodes: c.reason_codes,
        reasons: displayableReasons(c),
      }))
      // Гейт владельца 25.08 — кандидат, которого нечем объяснить, не
      // является рекомендацией Ayla. Одна строка, весь гейт.
      .filter((p) => p.reasons.length > 0);
  }
  return { picks, picksOutcome };
}

// ---------------------------------------------------------------------------
// Customer master / slot / booking — wraps existing /customer/* endpoints.
// ---------------------------------------------------------------------------

export type CustomerMaster = MasterDetail;

export interface SlotsResponse {
  slots: FreeSlot[];
  /**
   * `is_suggested` per slot — Tau §10 W2 tier 1. NOT yet wired by
   * backend; frontend currently treats absence as false. When backend
   * ships per-slot `is_suggested`, plumb here (do NOT add the field
   * client-side; this is a backend-driven UX cue).
   */
}

export interface BookingCreatePayload {
  service_id: string;
  master_id: string;
  visit_at: string;
  /** AMD-002 / C7.4 — user's payment choice from the summary screen. */
  payment_required?: boolean;
}
export interface BookingCreateResponse {
  booking: CreatedBooking;
}

export const getCustomerMaster = (masterId: string): Promise<{ master: CustomerMaster }> =>
  fetchMaster(masterId);

/**
 * Fetch slots for a master across the next `days` window.
 *
 * NOTE: existing backend endpoint requires `service_id`. Caller must
 * pass both `masterId` AND `serviceId` (we accept a unified `params`
 * object). `days` defaults to 14 per Tau §10.1.
 */
export const getCustomerSlots = (params: {
  masterId: string;
  serviceId: string;
  days?: number;
}): Promise<SlotsResponse> => {
  const days = params.days ?? 14;
  const today = new Date();
  const future = new Date(today);
  future.setDate(today.getDate() + days);
  const isoDate = (d: Date): string =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return fetchSlots({
    masterId: params.masterId,
    serviceId: params.serviceId,
    dateFrom: isoDate(today),
    dateTo: isoDate(future),
  });
};

export const createCustomerBooking = (
  payload: BookingCreatePayload,
): Promise<BookingCreateResponse> => createBooking(payload);
