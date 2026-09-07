/**
 * Customer profile lib — Tier 1 Priority 6 Phase B.
 *
 * Spec: `docs/screens/customer-profile-flow.md` (current dev: deferred
 * version post commit `376784e`, NOT Tau #843 original).
 *
 * # Что здесь подключено
 *
 *   - R1 header read: имя из реального `/customer/me`
 *   - R2 согласия: чтение всех, маркетинг на запись, отзыв согласия на
 *     хранение данных (не тумблер — сценарий с подтверждением)
 *   - R4 «Подсказки от Ayla» на чтение и запись
 *
 * Отложено по-прежнему: R3 «что Ayla помнит» — backend-слоя нет
 * (`UserPersonalContext`/`MemoryEntry` не существует), на экране
 * coming-soon карточка по spec §5. Экспорт и удаление данных живут в
 * `lib/personal-data.ts` и подключены с C5.
 *
 * # Contracts (DRF-1520 — ручки живые, снято с кода, а не с тикета)
 *
 *   GET    /api/v1/customer/me                          → Profile (lib/api.ts)
 *   GET    /api/v1/customer/me/consents/                → документ согласий
 *   POST   /api/v1/customer/me/consents/proactive-hints/
 *          тело `{"enabled": bool}`                     → документ согласий
 *   POST   /api/v1/customer/me/consents/marketing/      → документ (выдача)
 *   DELETE /api/v1/customer/me/consents/marketing/      → документ (отзыв)
 *   DELETE /api/v1/customer/me/consents/data-storage/
 *          тело `{"confirmation", "disclosure_version"}` → документ + `revocation`
 *
 * WIRING NOTE (DRF-1475, §24, решение владельца 05.09 + DRF-1520):
 * заглушек здесь больше нет. Все четыре функции ходят в настоящие
 * ручки `apps/miniapp_api/views.py` (`customer_consents`,
 * `customer_proactive_hints`, `customer_marketing_consent`,
 * `customer_data_storage_consent`), логика — `apps/consent/customer.py`.
 *
 * Главный источник правды для маркетингового согласия — реестр
 * `ConsentRecord(MARKETING)`, а не `UserPreferences.notify_promo`:
 * колонка осталась зеркалом с единственным пишущим путём. Поэтому и
 * чтение, и запись маркетинга идут через `me/consents/`, а не через
 * `PATCH /me` — иначе экран показывал бы зеркало вместо факта.
 *
 * Дата согласия берётся из `granted_at` реестра. `BotUser.consent_at`
 * сервер наружу не отдаёт сознательно: приветственный поток её ставит,
 * а отзыв никогда не снимает, и на пилоте четыре строки из пяти с
 * непустой `consent_at` уже отозвали согласие.
 *
 * Function signatures DO NOT change; `ConsentsResponse` дополнен полями,
 * которых у зеркала не было (состояние хранения данных, версия
 * раскрытия, состояние подсказок).
 *
 * # Stub variants for dev QA (Records / Wellness pattern reuse)
 *
 *   ?stub=default — multi-tenant happy path (Анна Петрова, 3 салона)
 *   ?stub=new_user — first-time, single tenant, all consents at default
 *   ?stub=multi   — same as default (alias kept for explicit naming)
 *
 * Заглушки отдаются ТОЛЬКО при явном `?stub=<variant>` в DEV-сборке —
 * без параметра и в проде всегда ходим в настоящие ручки. Они дают QA
 * варианты и в проде не читаются. Production bundle aliases all
 * variants to default via `import.meta.env.DEV` guard (Wellness
 * PR #886 lesson — the `?stub=` query was a phishing vector when
 * read in prod).
 *
 * # Voice + factual-only rule
 *
 * All copy + stub data MUST obey memory `project_records_voice_principles`
 * + spec §10 + §14 anti-patterns:
 *   - No exclamation marks anywhere
 *   - No selling tone, no marketing copy
 *   - No English jargon («PII» / «opt-out» / «GDPR» / «cross-border»
 *     forbidden in customer-facing strings per spec §14)
 *   - «ты» canonical (per memory `project_ayla_personal_ai`)
 *   - No fake grace promises (NO «через 30 дней можно отменить»)
 *   - No promises which backend cannot deliver
 *
 * Единственное исключение из голоса экрана — утверждённый владельцем
 * юридический текст последствий отзыва (§35 п.7), см.
 * {@link DATA_STORAGE_REVOCATION_DISCLOSURE_TEXT}.
 */

// ---------------------------------------------------------------------------
// Contract types — verbatim per spec §12. Управляемых строк в R2 две:
// маркетинговый тумблер и отзыв согласия на хранение (не тумблер —
// действие с подтверждением); остальные строки — locked info rows.
// ---------------------------------------------------------------------------

import { ApiError, fetchProfile, request, type Profile } from "./api";

export interface MeProfileResponse {
  display_name: string;
  max_handle: string;
  /**
   * Variant C multi-tenant scope hint (per spec §3.1 + memory
   * `project_cross_tenant_invisible_relationship`). Nearest tenant is
   * the first element; «+N салонов» derived from `length - 1`.
   */
  tenant_names: string[];
}

export interface ConsentsResponse {
  /** Always true for pilot — info row, not toggle. */
  is_booking_pii_locked: boolean;
  /** Always true for pilot — info row, not toggle. */
  is_master_data_locked: boolean;
  /**
   * OFF default per spec §4.1 — opt-in only. Правда живёт в реестре
   * `ConsentRecord(MARKETING)`, а не в зеркале `notify_promo`.
   */
  marketing_consent: boolean;
  /**
   * ISO-момент ДЕЙСТВУЮЩЕГО согласия на хранение данных (`granted_at`
   * реестра). Пустая строка, когда действующего согласия нет — это
   * правда, а не пробел: у отозвавшего даты выдачи не существует.
   */
  data_storage_consent_at: string;
  /** Действует ли согласие на хранение данных прямо сейчас. */
  data_storage_granted: boolean;
  /**
   * Версия раскрытия последствий отзыва — ТОЛЬКО из ответа сервера.
   * Смысл проверки в том, что человек нажал под тем текстом, который
   * сервер считает актуальным; клиентская константа этого не докажет.
   */
  data_storage_disclosure_version: string;
  /** «Подсказки от Ayla» — включены ли (не opt-out, а прямое «да»). */
  proactive_hints_enabled: boolean;
}

export interface ProactivePrefsResponse {
  /**
   * True = proactive AI off. Default false per spec §6.1 (R4 toggle
   * default ON = opt_out false). Engagement-class B11 messages gate
   * on this; transactional B5/B6 bypass (see spec §6.1).
   */
  proactive_messages_opt_out: boolean;
}

// ---------------------------------------------------------------------------
// Документ согласий — форма ответа сервера, снята с
// `apps/consent/customer.py::read_consents` и
// `apps/miniapp_api/views.py::customer_data_storage_consent`.
//
// Типы согласий сервер строит обходом `ConsentRecord.ConsentType`, то
// есть набор ключей растёт сам. Клиент поэтому читает его как словарь и
// НЕ перечисляет типы: список, продублированный здесь, разошёлся бы с
// сервером молча.
// ---------------------------------------------------------------------------

interface ConsentStateDoc {
  granted: boolean;
  granted_at: string | null;
  document_version: string;
}

interface ConsentsDocument {
  consents: Record<string, ConsentStateDoc | undefined>;
  proactive_hints: { enabled: boolean };
  data_storage: ConsentStateDoc & {
    revocation: {
      disclosure_version: string;
      consequences: string[];
      retained: string[];
    };
  };
  /**
   * Есть только в ответе на отзыв (`DELETE me/consents/data-storage/`).
   * Лежит в КОРНЕ документа, а не внутри `data_storage`: внутри —
   * раскрытие, которое показывают ДО нажатия, снаружи — исход того
   * нажатия. Две разные вещи с одинаковым именем на разных уровнях.
   */
  revocation?: {
    status: string;
    failed_steps?: string[];
    failed_details?: Record<string, string>;
  };
}

const CONSENTS_PATH = "/me/consents/";
const PROACTIVE_HINTS_PATH = "/me/consents/proactive-hints/";
const MARKETING_PATH = "/me/consents/marketing/";
const DATA_STORAGE_PATH = "/me/consents/data-storage/";

/**
 * Последствия отзыва согласия на хранение данных.
 *
 * УТВЕРЖДЕНО ВЛАДЕЛЬЦЕМ §35 п.7 ДОСЛОВНО. Правка только с новым
 * решением владельца: ни сокращений, ни «вы» → «ты» ради голоса
 * экрана, ни перестановки предложений. Это юридический текст, он
 * проходит согласование перед выпуском, и «причесать» его под тон
 * приложения — значит выпустить не то, что согласовано.
 *
 * Показывается ДО подтверждения; кнопки под ним — «Не отзывать» и
 * «Отозвать согласие», ровно эти подписи.
 */
export const DATA_STORAGE_REVOCATION_DISCLOSURE_TEXT =
  "После подтверждения Ayla перестанет сохранять и использовать ваши " +
  "данные, основанные на этом согласии. Доступные для удаления данные " +
  "будут удалены. Сведения, которые мы обязаны хранить по закону или " +
  "для исполнения ваших действующих записей, могут сохраниться на " +
  "необходимый срок. Сам аккаунт и доступ к записям останутся. Вернуть " +
  "удалённые данные будет нельзя.";

/**
 * Что экран говорит после исхода `revoked_partial_processing`.
 *
 * TODO(Q-CLIENT-03): формулировка частичного исхода — юридическая и
 * решается владельцем. Пока ответа нет, значение `null`, и экран не
 * делает НИ ОДНОГО утверждения о полноте удаления: ни «всё удалено»,
 * ни «часть данных осталась» — второе тоже формулировка, которой у нас
 * нет. Умолчание, а не выдумка: про возможное сохранение части
 * сведений человек уже прочитал в утверждённом тексте выше, до
 * нажатия. Ответ владельца вставляется сюда одной правкой.
 *
 * На пилоте это ОСНОВНОЙ исход: замер 07.09 — 13 из 26 человек не
 * связаны с Ayla, у них шаг `ayla_delete` вернёт `not_linked`.
 */
export const DATA_STORAGE_PARTIAL_PROCESSING_NOTE: string | null = null;

/** Исход отзыва — ровно два значения, которые отдаёт сервер на 200. */
export type DataStorageRevocationStatus =
  | "revoked"
  | "revoked_partial_processing";

export interface DataStorageRevocationResult {
  status: DataStorageRevocationStatus;
  /** Перечитанное из ответа состояние — не то, каким оно было до нажатия. */
  consents: ConsentsResponse;
}

/**
 * Сервер не знает версию раскрытия, под которой человек нажал (409).
 * Не ошибка ввода и не повод «дожать» отзыв тем же телом: текст
 * последствий обновился, и его надо прочитать заново.
 */
export class StaleDisclosureError extends Error {
  constructor() {
    super("data-storage revocation disclosure version is stale");
    this.name = "StaleDisclosureError";
  }
}

/**
 * Сам отзыв не состоялся — сервер ответил 502 и в перечитанном
 * документе согласие всё ещё действует. Отдельный тип, потому что это
 * единственный исход, про который экран вправе сказать «согласие
 * осталось действующим».
 */
export class DataStorageRevocationFailedError extends Error {
  constructor() {
    super("data-storage revocation did not happen");
    this.name = "DataStorageRevocationFailedError";
  }
}

/**
 * Support deeplink (issue #949).
 *
 * Ops-configurable via `VITE_SUPPORT_DEEPLINK` (deploy-time env, no
 * code change needed); falls back to the pilot placeholder. When the
 * real support channel handle is decided (ops/W3), set the env var —
 * do NOT hardcode another URL here.
 */
export const SUPPORT_DEEPLINK =
  (import.meta.env.VITE_SUPPORT_DEEPLINK as string | undefined) ??
  "https://max.me/aylasupport";

// ---------------------------------------------------------------------------
// Stub variant picker — dev-only QA hook (mirror customer-records.ts).
// ---------------------------------------------------------------------------

type StubVariant = "default" | "new_user" | "multi";

/**
 * Явный dev-override для ПОДКЛЮЧЁННЫХ функций (DRF-1475): заглушка
 * отдаётся только когда разработчик сам попросил `?stub=<variant>` в
 * DEV-сборке. Без параметра — реальный `/customer/me`, иначе dev и
 * прод расходились бы поведением, а экран снова показывал бы выдумку.
 */
function explicitStubVariant(): StubVariant | null {
  if (!import.meta.env.DEV) return null;
  if (typeof window === "undefined") return null;
  try {
    const sp = new URLSearchParams(window.location.search);
    const v = sp.get("stub");
    if (v === "default" || v === "new_user" || v === "multi") return v;
  } catch {
    /* SSR / parse failure */
  }
  return null;
}

// ---------------------------------------------------------------------------
// Маппинг реального `GET /customer/me` (lib/api.ts::Profile) на контракты
// экрана. Ручка не отдаёт `max_handle` и `tenant_names` — соответствующие
// строки экран скрывает, поэтому здесь честные пустые значения, а не
// выдуманные. Имя — то, которое человек назвал сам (`client_name`), с
// отступлением на канальное `display_name`.
// ---------------------------------------------------------------------------

function toMeProfile(p: Profile): MeProfileResponse {
  return {
    display_name: p.client_name || p.display_name,
    max_handle: "",
    tenant_names: [],
  };
}

/**
 * Документ согласий → контракт экрана. Ничего не достраивает: чего в
 * ответе нет, того нет и здесь.
 */
function toConsents(doc: ConsentsDocument): ConsentsResponse {
  const marketing = doc.consents?.marketing;
  const storage = doc.data_storage;
  return {
    is_booking_pii_locked: true,
    is_master_data_locked: true,
    marketing_consent: Boolean(marketing?.granted),
    data_storage_consent_at: storage?.granted_at ?? "",
    data_storage_granted: Boolean(storage?.granted),
    data_storage_disclosure_version:
      storage?.revocation?.disclosure_version ?? "",
    proactive_hints_enabled: Boolean(doc.proactive_hints?.enabled),
  };
}

function toProactivePrefs(doc: ConsentsDocument): ProactivePrefsResponse {
  return { proactive_messages_opt_out: !doc.proactive_hints?.enabled };
}

// ---------------------------------------------------------------------------
// Stub data — voice-audited per spec §10. Anna Petrova default mirrors
// the spec §3 illustration verbatim.
// ---------------------------------------------------------------------------

const DEFAULT_ME: MeProfileResponse = {
  display_name: "Анна Петрова",
  max_handle: "@anna_petrova",
  tenant_names: ["Beauty Place", "Casa Bella", "Студия Натали"],
};

const NEW_USER_ME: MeProfileResponse = {
  display_name: "Мария",
  max_handle: "@maria_k",
  tenant_names: ["Beauty Place"],
};

const MULTI_ME: MeProfileResponse = DEFAULT_ME;

const ME_STUB: Record<StubVariant, MeProfileResponse> = import.meta.env.DEV
  ? { default: DEFAULT_ME, new_user: NEW_USER_ME, multi: MULTI_ME }
  : { default: DEFAULT_ME, new_user: DEFAULT_ME, multi: DEFAULT_ME };

// In-memory mutable consent state (per-session), только для явного
// `?stub=` в DEV: переключение тумблера должно быть видно при повторном
// чтении. Без `?stub=` состояние живёт на сервере (`notify_promo`).
//
// `data_storage_disclosure_version` здесь — это ИМИТАЦИЯ ответа сервера
// для QA, а не источник правды: боевой отзыв уходит с той версией,
// которую вернул сервер в этой же сессии.
const CONSENTS_STATE: Record<StubVariant, ConsentsResponse> = {
  default: {
    is_booking_pii_locked: true,
    is_master_data_locked: true,
    marketing_consent: false,
    data_storage_consent_at: "2026-05-14T10:30:00+03:00",
    data_storage_granted: true,
    data_storage_disclosure_version: "data-storage-revocation-v1",
    proactive_hints_enabled: true,
  },
  new_user: {
    is_booking_pii_locked: true,
    is_master_data_locked: true,
    marketing_consent: false,
    data_storage_consent_at: "2026-05-30T12:00:00+03:00",
    data_storage_granted: true,
    data_storage_disclosure_version: "data-storage-revocation-v1",
    proactive_hints_enabled: true,
  },
  multi: {
    is_booking_pii_locked: true,
    is_master_data_locked: true,
    marketing_consent: false,
    data_storage_consent_at: "2026-05-14T10:30:00+03:00",
    data_storage_granted: true,
    data_storage_disclosure_version: "data-storage-revocation-v1",
    proactive_hints_enabled: true,
  },
};

const PROACTIVE_STATE: Record<StubVariant, ProactivePrefsResponse> = {
  default: { proactive_messages_opt_out: false },
  new_user: { proactive_messages_opt_out: false },
  multi: { proactive_messages_opt_out: false },
};

// ---------------------------------------------------------------------------
// Fetch wrappers — все четыре ходят в настоящие ручки (DRF-1520).
// Prod-гарды `guardProd` / `StubNotWiredError` сняты: они существовали
// ровно про «ручек нет», и держать их дальше значило бы ронять экран на
// работающем эндпоинте. DEV-заглушки `?stub=` остались — они дают QA
// варианты и в проде не читаются.
//
// Function signatures DO NOT change.
// ---------------------------------------------------------------------------

function devWarn(msg: string): void {
  if (import.meta.env.DEV && typeof console !== "undefined") {
    // eslint-disable-next-line no-console
    console.warn(`[customer-profile stub] ${msg}`);
  }
}

export async function fetchMe(): Promise<MeProfileResponse> {
  const stub = explicitStubVariant();
  if (stub) {
    devWarn("GET /customer/me served from explicit ?stub= override");
    return ME_STUB[stub];
  }
  return toMeProfile(await fetchProfile());
}

/**
 * Всё состояние согласий одним запросом. Экран читает его целиком, а не
 * по кусочкам: сервер отдаёт один документ, и три запроса за одним и
 * тем же документом отличались бы только моментом съёмки.
 */
export async function fetchConsents(): Promise<ConsentsResponse> {
  const stub = explicitStubVariant();
  if (stub) {
    devWarn("consents served from explicit ?stub= override");
    // Return a copy so callers cannot mutate stub state directly.
    return { ...CONSENTS_STATE[stub] };
  }
  return toConsents(await request<ConsentsDocument>(CONSENTS_PATH));
}

/**
 * Маркетинговое согласие: `POST` — выдать, `DELETE` — отозвать.
 *
 * Не `PATCH /me` с `notify_promo`: колонка — зеркало, правду держит
 * реестр. Сервер отвечает пересчитанным документом, из которого и
 * перечитывается факт, а не то, что просили.
 */
export async function setMarketingConsent(
  next: boolean,
): Promise<ConsentsResponse> {
  const stub = explicitStubVariant();
  if (stub) {
    devWarn("marketing consent served from explicit ?stub= override");
    CONSENTS_STATE[stub] = { ...CONSENTS_STATE[stub], marketing_consent: next };
    return { ...CONSENTS_STATE[stub] };
  }
  const doc = await request<ConsentsDocument>(MARKETING_PATH, {
    method: next ? "POST" : "DELETE",
  });
  return toConsents(doc);
}

/**
 * Узкое чтение «Подсказок Ayla» из того же документа согласий.
 * Сигнатура прежняя (`proactive_messages_opt_out`), потому что в этих
 * терминах о колонке говорит и сервер, и сторож рассылок.
 */
export async function fetchProactivePrefs(): Promise<ProactivePrefsResponse> {
  const stub = explicitStubVariant();
  if (stub) {
    devWarn("proactive hints served from explicit ?stub= override");
    return { ...PROACTIVE_STATE[stub] };
  }
  return toProactivePrefs(await request<ConsentsDocument>(CONSENTS_PATH));
}

export async function setProactiveOptOut(
  optOut: boolean,
): Promise<ProactivePrefsResponse> {
  const stub = explicitStubVariant();
  if (stub) {
    devWarn("proactive hints served from explicit ?stub= override");
    PROACTIVE_STATE[stub] = { proactive_messages_opt_out: optOut };
    return { ...PROACTIVE_STATE[stub] };
  }
  // Ручка думает в положительных терминах («включено»), клиентский
  // контракт — в отрицательных («opt out»). Инверсия ровно здесь, в
  // одном месте, чтобы не разъехаться по экранам.
  const doc = await request<ConsentsDocument>(PROACTIVE_HINTS_PATH, {
    method: "POST",
    body: JSON.stringify({ enabled: !optOut }),
  });
  return toProactivePrefs(doc);
}

/**
 * Отзыв согласия на хранение данных — двойное подтверждение.
 *
 * `confirmation` — тот же токен, что у соседнего удаления данных
 * ({@link DELETE_CONFIRMATION_TOKEN} в `lib/personal-data.ts`; второй
 * копии здесь заводить нельзя). `disclosureVersion` — версия ИЗ ОТВЕТА
 * СЕРВЕРА: проверяется, что человек нажал под тем текстом последствий,
 * который сервер считает актуальным.
 *
 * Исходы:
 *   - 200 `revoked` / `revoked_partial_processing` — отзыв состоялся,
 *     состояние перечитывается из этого же ответа (§35 п.9);
 *   - 409 `stale_disclosure` → {@link StaleDisclosureError}: тело чинить
 *     нечего, надо перечитать `me/consents/` и показать раскрытие заново;
 *   - 502 → {@link DataStorageRevocationFailedError}: не состоялся сам
 *     отзыв, согласие осталось действующим;
 *   - остальное — общий {@link ApiError}.
 */
export async function revokeDataStorage(
  confirmation: string,
  disclosureVersion: string,
): Promise<DataStorageRevocationResult> {
  try {
    const doc = await request<ConsentsDocument>(DATA_STORAGE_PATH, {
      method: "DELETE",
      body: JSON.stringify({
        confirmation,
        disclosure_version: disclosureVersion,
      }),
    });
    // Сервер называет исход сам. Выводить его из `failed_steps` значило
    // бы решать за сервер, что считать полным успехом.
    const status: DataStorageRevocationStatus =
      doc.revocation?.status === "revoked" ? "revoked" : "revoked_partial_processing";
    return { status, consents: toConsents(doc) };
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.status === 409) throw new StaleDisclosureError();
      if (err.status === 502) throw new DataStorageRevocationFailedError();
    }
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Pure helpers — also exported for unit-style smoke tests.
// ---------------------------------------------------------------------------

/**
 * Russian-pluralised «+N салонов» — used in R1 header (per spec §3.1
 * + memory `project_solo_provider_universal_ui`). Plural forms picked
 * by standard one-few-many rules (Slavic plural categories).
 */
export function additionalSalonsLabel(extraCount: number): string {
  if (extraCount <= 0) return "";
  const mod10 = extraCount % 10;
  const mod100 = extraCount % 100;
  let word: string;
  if (mod10 === 1 && mod100 !== 11) {
    word = "салон";
  } else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
    word = "салона";
  } else {
    word = "салонов";
  }
  return `+${extraCount} ${word}`;
}

/**
 * 152-ФЗ row date format — «14 мая 2026». Same render as records
 * `datetime_relative_label` (server side normally pre-formats), but
 * for the consent timestamp the server returns ISO and the row needs
 * a calm calendar date.
 *
 * Built from a hardcoded month table (same discipline as
 * `lib/format.ts`) — NOT `toLocaleDateString("ru-RU")`, whose output
 * depends on the runtime's ICU data (small-icu builds render English
 * and made this an env-dependent test failure, 114-vs-115).
 */
const RU_MONTHS_GENITIVE = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

export function formatConsentDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const month = RU_MONTHS_GENITIVE[d.getMonth()] ?? "";
    return `${d.getDate()} ${month} ${d.getFullYear()}`;
  } catch {
    return iso;
  }
}

/**
 * Initials from a display name. Falls back to a single character; if
 * empty, returns «·» (calm placeholder per spec §3.1 — NO «??» / «n/a»
 * anti-patterns).
 */
export function avatarInitials(displayName: string): string {
  const cleaned = (displayName ?? "").trim();
  if (!cleaned) return "·";
  const parts = cleaned.split(/\s+/).filter(Boolean);
  const first = parts[0];
  if (!first) return "·";
  const second = parts[1];
  if (!second) return first.slice(0, 1).toUpperCase();
  const a = first.charAt(0);
  const b = second.charAt(0);
  return (a + b).toUpperCase();
}
