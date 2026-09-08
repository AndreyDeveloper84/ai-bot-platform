import { getInitData } from "./max-sdk";
import { applyDevBypassHeaders } from "./dev-bypass";

const API_BASE = "/api/v1/customer";

export class ApiError extends Error {
  constructor(readonly status: number, readonly slug: string, readonly detail: string) {
    super(`[${status}] ${slug}: ${detail}`);
    this.name = "ApiError";
  }
}

interface ErrorBody {
  error: string;
  detail: string;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const initData = getInitData();
  const headers = new Headers(init.headers);
  if (initData) headers.set("Authorization", `MaxInitData ${initData}`);
  applyDevBypassHeaders(headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let body: ErrorBody = { error: "http_error", detail: res.statusText };
    try {
      body = (await res.json()) as ErrorBody;
    } catch {
      /* non-JSON 5xx */
    }
    throw new ApiError(res.status, body.error, body.detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- auth ---

/**
 * Server-cached pending booking intent — W4 #844 extension to
 * `/auth/verify`. Backend field names per
 * `apps/miniapp_api/pending_intent.py::_ALLOWED_FIELDS`:
 *
 *   - `master_id`     : str
 *   - `service_id`    : str
 *   - `slot_iso`      : str  (ISO 8601 with offset)
 *   - `price_quoted`  : int | float  (NOTE: not `price_rub`)
 *   - `note`          : str  (truncated to 500 chars server-side)
 *   - `loyalty_apply` : bool (NOTE: not `loyalty_choice`)
 *   - `entry_point`   : str  (provenance, DRF-1484 §24.5; truncated to
 *                             64 chars server-side)
 *
 * Field-name mismatch with the frontend sessionStorage shape
 * (`PendingBookingIntent` in `pending-booking-intent.ts`) is by
 * design — the backend cache schema is the source of truth and the
 * sessionStorage fallback was specced earlier; the booking-confirm
 * screen normalises both shapes when restoring.
 *
 * `tenant_id` is deliberately NOT part of this contract (§24.5 owner
 * decision): tenant belongs to the execution/request context and is
 * server-resolved, never a property of the durable intent snapshot.
 */
export interface ServerPendingBookingIntent {
  master_id: string;
  service_id: string;
  slot_iso: string;
  price_quoted?: number;
  note?: string;
  loyalty_apply?: boolean;
  entry_point?: string;
}

export interface AuthVerifyResponse {
  user: { id: string; channel_user_id: string; display_name: string; client_name: string };
  tenant: { slug: string; name: string; timezone: string };
  /**
   * W4 #844 — server-side cached `pending_booking_intent`. Present
   * (or `null`) on every `/auth/verify` response per backend
   * `views.py::auth_verify`. Used by CustomerBookingConfirmScreen to
   * restore draft across the OAuth round-trip (defence-in-depth
   * multi-device — sessionStorage stays the PRIMARY restore path).
   */
  pending_booking_intent?: ServerPendingBookingIntent | null;
}

/**
 * POST /auth/verify with an optional body. Per the backend contract
 * (`pending_intent.py` docstring), passing `pending_booking_intent`
 * caches it server-side keyed by `BotUser.id` (10min TTL); passing
 * `null` or omitting the body returns the current cached value
 * without mutation.
 *
 * Frontend callers:
 *   - HelloScreen / boot — `authVerify()` (no body) just reads.
 *   - CustomerBookingConfirmScreen (anonymous-gate restore) —
 *     `authVerify({ readCached: true })` triggers the same read path
 *     but the helper makes intent explicit at the call site.
 */
export function authVerify(
  opts?: { intent?: ServerPendingBookingIntent | null; readCached?: boolean },
): Promise<AuthVerifyResponse> {
  if (opts && (opts.intent !== undefined || opts.readCached)) {
    // Send a JSON body. The backend only reads it when content-type
    // is application/json — request() sets that header automatically
    // when `body` is non-empty (api.ts §line ~23).
    const body: Record<string, unknown> = {};
    if (opts.intent !== undefined) {
      body.pending_booking_intent = opts.intent;
    }
    return request("/auth/verify", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  return request("/auth/verify", { method: "POST" });
}

// --- catalog: services ---
export interface Service {
  id: string;
  slug: string;
  name: string;
  short_description: string;
  description: string;
  price_from: string | null;
  duration_min: number | null;
  is_popular: boolean;
  contraindications: string;
  /**
   * DRF-1164 — can the customer book this service at all? False when no
   * bookable master performs it. Backend-computed (`apps/miniapp_api`
   * annotates the catalog query); NEVER derived client-side from a
   * master list, or the answer would differ per screen.
   */
  is_bookable: boolean;
}
export const fetchServices = (): Promise<{
  services: Service[];
  /**
   * DRF-1482 — WHY the catalog has nothing to offer, server-computed
   * (`apps/miniapp_api/views.py::services_list`), per
   * `docs/screens/customer-catalog-empty-states-spec.md` §2:
   * `empty_reason ∈ {search_no_match, region_empty, booking_unavailable}`.
   * The server never sends `search_no_match` (free-text search never
   * leaves the Mini App — the client derives that one). Optional while
   * older backends roll out; `null`/absent means "no empty state".
   */
  empty_reason?: string | null;
}> =>
  request("/services", { method: "GET" });
export const fetchService = (id: string): Promise<{ service: Service }> =>
  request(`/services/${id}`, { method: "GET" });

// --- catalog: masters ---
export interface Master {
  id: string;
  name: string;
  specialization: string;
  bio: string;
  experience: string;
  rating: string | null;
  photo_url: string;
}
export interface MasterDetail extends Master {
  service_ids: string[];
}
export const fetchMasters = (params?: {
  serviceId?: string;
}): Promise<{ masters: Master[] }> => {
  const q = new URLSearchParams();
  if (params?.serviceId) q.set("service_id", params.serviceId);
  const qs = q.toString();
  return request(`/masters${qs ? `?${qs}` : ""}`, { method: "GET" });
};
export const fetchMaster = (id: string): Promise<{ master: MasterDetail }> =>
  request(`/masters/${id}`, { method: "GET" });

// --- catalog: recommendations (граница резолвера, §9.4) ---
/**
 * `POST /recommendations` — проекция границы резолвера рекомендаций
 * на клиентскую поверхность.
 *
 * # Что здесь изменилось и почему (DRF-1568, T7)
 *
 * До 07.09.2026 этот модуль объявлял `{recommendations: [{service_id,
 * score}]}` и утверждал в докстринге, что «NO backend ever produced»
 * трёхслойную форму. Второе перестало быть правдой в тот же день:
 * источник отдаёт трёхслойный ответ с мастерами и `reasoning_text`,
 * а комментарий продолжал описывать состояние, которого больше нет —
 * ровно тот класс дефекта, из-за которого замер эндпоинта месяц
 * подменял собой замер экрана.
 *
 * Форму ответа теперь описывает не эта поверхность и не источник, а
 * договор: `docs/specs/RECOMMENDATION_RESOLVER_CONTRACT_v1.0.md`.
 * Клиент написан **против документа**, а не против чужой реализации:
 * расхождение реализации с контрактом — находка, которую несут
 * владельцу контракта, а не подгоняют молча под факт.
 *
 * # Что запрещено этому файлу
 *
 * * **Не адаптировать.** Переименование, доклейка умолчаний и починка
 *   «почти правильного» ответа означали бы, что форму держит
 *   потребитель (§2.1 C3). Здесь только ответ на один вопрос: та ли
 *   это форма, о которой договорились.
 * * **Не решать за человека.** `ordered[]` приходит уже упорядоченным;
 *   сырых баллов в ответе нет вовсе (§4.3), и собрать свой порядок
 *   не из чего — это сделано намеренно.
 * * **Не сочинять WHY.** Наружу идут `reason_codes` и `evidence`;
 *   фразу собирает представление (§7.1), и строки для показа в ответе
 *   быть не должно — её наличие само по себе нарушение.
 */

/**
 * Мажорная версия контракта, которую этот клиент умеет разбирать.
 *
 * Ответ другой мажорной версии — `CONTRACT_VIOLATION`, а не «попробуем
 * разобрать»: попытка разобрать неизвестное и есть тот способ, которым
 * расхождение доезжает до человека молча. Та же константа стоит на
 * второй половине границы — `apps/integrations/ayla/
 * recommendation_resolver_client.py::SUPPORTED_SPEC_MAJOR`.
 */
export const SUPPORTED_RESOLVER_SPEC_MAJOR = 1;

/**
 * §4.3 — что именно рекомендовано. `kind` нормативен: без него
 * поверхность не знает, услуга это или мастер, и «молча подставить
 * другое» становится делом одной строки.
 */
export type CandidateKind = "SERVICE" | "OFFER" | "PROVIDER" | "SLOT";
export const CANDIDATE_KINDS: readonly CandidateKind[] = [
  "SERVICE",
  "OFFER",
  "PROVIDER",
  "SLOT",
];

export interface CandidateRef {
  kind: CandidateKind;
  id: string;
}

/**
 * §8.2 — свидетельство. Передаётся вместе с оценкой: `rating` без
 * `review_count` не приходит никогда, чтобы потребитель физически не
 * мог повторить ошибку «Рейтинг 4.9» как причину.
 *
 * `strength` и `origin` читаются, но не пересчитываются: смягчение или
 * усиление силы свидетельства при отрисовке — `LLM_FORBIDDEN`, и
 * человеку оно запрещено тем же пунктом (§7.3).
 */
export interface EvidenceItem {
  kind: string;
  value: unknown;
  strength: "CONFIRMED" | "WEAK" | "UNSUBSTANTIATED" | "UNKNOWN";
  origin: "DOMAIN_FACT" | "USER_EXPLICIT" | "USER_CLICK" | "CURATED_KNOWLEDGE";
  observed_at?: string;
  source_ref?: string;
}

/**
 * §4.3 — один упорядоченный кандидат.
 *
 * `tier` **нормативен**: равный `tier` означает НЕРАЗЛИЧЁННЫХ
 * кандидатов, и поверхность не вправе называть первого из яруса лучшим
 * (решение владельца §29.3). `rank` — позиция, а не превосходство.
 */
export interface RankedCandidate {
  candidate: CandidateRef;
  rank: number;
  tier: number;
  /** Закрытый реестр §7.2, лексикографически. Минимум один. */
  reason_codes: string[];
  evidence?: EvidenceItem[];
  stage_verdicts?: Record<string, string>;
}

/** §4.4 — почему кандидата нет. Только S0/S1: стадии допустимости. */
export interface ExcludedCandidate {
  candidate: CandidateRef;
  stage: string;
  reason_code: string;
}

/** §6.3 — без версий решение невоспроизводимо задним числом. */
export interface PolicyVersions {
  resolver_spec_version?: string;
  stage_policy_version?: string;
  reason_code_registry_version?: string;
  catalog_mapping_version?: string;
  safety_policy_version?: string;
  tie_break_policy_version?: string;
}

/**
 * §4.2 — неизменяемое решение резолвера.
 *
 * `resolver_spec_version` лежит здесь **дважды** — отдельным полем и
 * внутри `policy_versions`. Отдельное поле существует затем, чтобы
 * потребитель мог отвергнуть неизвестную мажорную версию, не разбирая
 * остального.
 */
export interface RecommendationDecision {
  decision_id: string;
  request_id: string;
  resolver_spec_version: string;
  ordered: RankedCandidate[];
  excluded?: ExcludedCandidate[];
  policy_versions: PolicyVersions;
  reason_codes?: string[];
  stage_activity?: Record<string, string>;
  context_snapshot_ref?: string;
  computed_at?: string;
}

/**
 * Конверт репозитория. Проверяется наравне с остальным: именно его
 * неразворачивание было половиной DEFECT-C-02 (§9.4).
 */
export interface RecommendationDecisionEnvelope {
  data: RecommendationDecision;
}

/**
 * Возвращает `unknown`, и это не небрежность.
 *
 * Типы TypeScript стираются в рантайме: типизированный промис
 * доказывает лишь то, что мы **намеревались** получить, и никогда —
 * что пришло. Прошлая подпись обещала `{recommendations: […]}`,
 * поэтому потребитель звал `.slice()` на `undefined`, `TypeError`
 * улетал в `catch`, написанный про «скорер недоступен», и блок
 * исчезал молча (`docs/OPEN_DECISIONS.md` §52). Единственный вход в
 * типизированный мир — {@link decisionContractViolation}.
 */
export const fetchRecommendations = (): Promise<unknown> =>
  request("/recommendations", { method: "POST" });

/**
 * Вернуть описание нарушения формы или `null`, если ответ конформен.
 *
 * # Конформность — целиком (§9.4.1, OD §53.1)
 *
 * > Ответ конформен целиком или не конформен. Один битый элемент из
 * > двадцати делает невалидным **ответ**, а не элемент.
 *
 * «Пропустить годные» запрещено прямо: это означало бы, что потребитель
 * решает, какие из присланных рекомендаций увидит человек, — то есть
 * ведёт отбор, то есть держит политику, которую §2 у него отнимает.
 * Так возвращается четвёртый авторитет ранжирования, самый незаметный:
 * он живёт в фильтре и никогда не назовёт себя ранжированием.
 *
 * Указание на конкретный элемент в тексте — **диагностика**, а не
 * исключение из правила: чинить по этому сигналу будут источник, а не
 * экран, и ему нужно знать, где именно он нарушил.
 *
 * # Чего эта функция не делает
 *
 * Она не применяется к отказу транспорта: сеть, не-2xx и неразбираемое
 * тело отвергаются до того, как появится тело, поэтому недоступный
 * источник сюда не доходит и шуметь не может. Устройство, а не
 * старательность: см. `customer-booking.ts::loadRecommendations`.
 *
 * Порядок проверок повторяет вторую половину границы
 * (`recommendation_resolver_client.py::decision_contract_violation`),
 * чтобы на одном и том же ответе оба конца называли **одно и то же**
 * первое нарушение.
 */
export function decisionContractViolation(payload: unknown): string | null {
  if (!isPlainObject(payload)) {
    return `ожидался объект, получено ${describeShape(payload)}`;
  }
  const data = payload.data;
  if (!isPlainObject(data)) {
    return `ожидался конверт {data: {…}}, получено data=${describeShape(data)}`;
  }
  const version = data.resolver_spec_version;
  if (typeof version !== "string") {
    return `resolver_spec_version отсутствует или не строка: ${describeShape(version)}`;
  }
  const major = version.split(".", 1)[0] ?? "";
  if (!/^\d+$/.test(major) || Number(major) !== SUPPORTED_RESOLVER_SPEC_MAJOR) {
    return (
      `неизвестная мажорная версия контракта ${describeShape(version)}; клиент ` +
      `умеет ${SUPPORTED_RESOLVER_SPEC_MAJOR}.x. Разбирать неизвестное запрещено (§9.4)`
    );
  }
  const ordered = data.ordered;
  if (!Array.isArray(ordered)) {
    return `ordered отсутствует или не список: ${describeShape(ordered)}`;
  }
  for (let i = 0; i < ordered.length; i += 1) {
    const problem = candidateViolation(ordered[i], i);
    if (problem !== null) {
      return `ответ невалиден целиком; первое нарушение — ${problem}`;
    }
  }
  for (const field of ["decision_id", "request_id", "policy_versions"] as const) {
    if (!(field in data)) return `обязательное поле ${field} отсутствует`;
  }
  if (hasDisplayString(data)) {
    return (
      "ответ несёт строку для показа человеку — граница отдаёт reason_codes " +
      "и evidence, фразу собирает представление (§7)"
    );
  }
  return null;
}

function candidateViolation(item: unknown, index: number): string | null {
  if (!isPlainObject(item)) {
    return `ordered[${index}]: ожидался объект, получено ${describeShape(item)}`;
  }
  const candidate = item.candidate;
  if (!isPlainObject(candidate) || typeof candidate.id !== "string") {
    return `ordered[${index}].candidate: нет идентификатора кандидата`;
  }
  // `kind` проверяется здесь, а не только на второй половине границы:
  // без него «услуга» и «мастер» неразличимы, а полка услуг, молча
  // принявшая мастера, и есть подстановка другого предмета (§14.4).
  if (
    typeof candidate.kind !== "string" ||
    !CANDIDATE_KINDS.includes(candidate.kind as CandidateKind)
  ) {
    return (
      `ordered[${index}].candidate.kind: ожидалось одно из ` +
      `${CANDIDATE_KINDS.join("|")}, получено ${describeShape(candidate.kind)}`
    );
  }
  for (const field of ["rank", "tier"] as const) {
    if (!Number.isInteger(item[field])) {
      return (
        `ordered[${index}].${field}: ожидалось целое, получено ` +
        `${describeShape(item[field])}`
      );
    }
  }
  const codes = item.reason_codes;
  if (
    !Array.isArray(codes) ||
    codes.length === 0 ||
    !codes.every((c) => typeof c === "string")
  ) {
    // Кандидат без кодов — строка, про которую нельзя сказать, почему
    // она здесь. Гейт WHY владельца (25.08) не пропустил бы её дальше,
    // но здесь она уже нарушение формы, а не «нечего показать».
    return (
      `ordered[${index}].reason_codes: ожидался непустой список строк, ` +
      `получено ${describeShape(codes)}`
    );
  }
  if (item.evidence !== undefined && !Array.isArray(item.evidence)) {
    return (
      `ordered[${index}].evidence: ожидался список, получено ` +
      `${describeShape(item.evidence)}`
    );
  }
  return null;
}

/**
 * Поля, наличие которых означает, что источник снова собрал фразу за
 * потребителя (§8.4 E1). Проверяется рекурсивно: «Рейтинг 4.9» пришёл
 * человеку именно такой строкой, и пришла она вложенной.
 */
const DISPLAY_FIELDS = ["reasoning_text", "reason_text", "why_text"];

function hasDisplayString(node: unknown): boolean {
  if (Array.isArray(node)) return node.some(hasDisplayString);
  if (isPlainObject(node)) {
    if (DISPLAY_FIELDS.some((f) => f in node)) return true;
    return Object.values(node).some(hasDisplayString);
  }
  return false;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Описывает значение по ФОРМЕ и никогда по содержимому — имена ключей и
 * типы опознают разошедшийся контракт, и они не могут унести данные
 * человека в строку журнала. Вторая половина границы описывает форму
 * подробнее (там журнал серверный); здесь журнал — консоль браузера
 * того самого человека, поэтому значений в ней нет вовсе.
 */
function describeShape(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return `array(${value.length})`;
  if (typeof value === "object") {
    return `object{${Object.keys(value as object).join(",")}}`;
  }
  return typeof value;
}

// --- slots ---
export interface FreeSlot {
  date: string;
  start: string;
}
export const fetchSlots = (params: {
  masterId: string;
  serviceId: string;
  dateFrom: string;
  dateTo: string;
}): Promise<{ slots: FreeSlot[] }> => {
  const q = new URLSearchParams({
    master_id: params.masterId,
    service_id: params.serviceId,
    date_from: params.dateFrom,
    date_to: params.dateTo,
  });
  return request(`/slots?${q.toString()}`, { method: "GET" });
};

// --- bookings ---
export interface CreatedBooking {
  id: string;
  service_name: string;
  master_name: string;
  visit_at: string;
  duration_min: number;
  status: string;
}
export const createBooking = (body: {
  service_id: string;
  master_id: string;
  visit_at: string;
  /** AMD-002: user-chosen online payment (C7). */
  payment_required?: boolean;
}): Promise<{ booking: CreatedBooking }> =>
  request("/bookings", { method: "POST", body: JSON.stringify(body) });

// --- bookings: list / detail / cancel / reschedule ---
// Mirrors `apps/miniapp_api/views.py` per
// customer-cancellation-reschedule-spec §3-§5.
export type BookingStatus =
  | "confirmed"
  | "cancel_requested"
  | "reschedule_requested"
  | "cancelled"
  | "rescheduled";

export interface BookingItem {
  id: string;
  status: BookingStatus;
  service_id: string | null;
  service_name: string;
  master_id: string | null;
  master_name: string;
  visit_at: string;
  duration_min: number | null;
  cancel_requested_at: string | null;
  undo_window_seconds: number;
  cancellable: boolean;
  reschedulable: boolean;
  // Phase 4 — post-visit feedback. NULL until customer rates.
  rating: number | null;
  can_rate: boolean;
  /**
   * C7.3 payment read model — present only when the event stream
   * produced a mirror row (hold signal or a payment.* event).
   * `amount` is a Decimal string per §1 (e.g. "2000.00").
   */
  payment?: { capture_state?: string | null; amount?: string | null } | null;
}

export const fetchMyBookings = (params?: {
  past?: boolean;
  limit?: number;
  before?: string;
}): Promise<{ items: BookingItem[]; next_cursor: string | null }> => {
  const q = new URLSearchParams();
  if (params?.past) q.append("status", "past");
  if (params?.limit) q.set("limit", String(params.limit));
  if (params?.before) q.set("before", params.before);
  const qs = q.toString();
  return request(`/bookings/list${qs ? `?${qs}` : ""}`, { method: "GET" });
};

export const fetchBooking = (id: string): Promise<{ booking: BookingItem }> =>
  request(`/bookings/${id}`, { method: "GET" });

export type CancelReasonClass = "timing" | "plans_changed" | "not_needed" | "other";

export const cancelBookingRequest = (
  id: string,
  body?: { reason_class?: CancelReasonClass; reason_text?: string },
): Promise<{ booking: BookingItem }> =>
  request(`/bookings/${id}/cancel`, {
    method: "POST",
    body: JSON.stringify(body ?? {}),
  });

export const cancelBookingConfirm = (id: string): Promise<{ booking: BookingItem }> =>
  request(`/bookings/${id}/cancel/confirm`, { method: "POST" });

export const cancelBookingUndo = (id: string): Promise<{ booking: BookingItem }> =>
  request(`/bookings/${id}/cancel/undo`, { method: "POST" });

export const rescheduleBookingRequest = (
  id: string,
  body: { new_master_id: string; new_service_id: string; new_visit_at: string },
): Promise<{ booking: BookingItem }> =>
  request(`/bookings/${id}/reschedule`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const rescheduleBookingConfirm = (
  id: string,
): Promise<{ old_booking: BookingItem; new_booking: BookingItem }> =>
  request(`/bookings/${id}/reschedule/confirm`, { method: "POST" });

// --- profile (Phase 3 / F4) ---
export interface Preferences {
  notify_reminders: boolean;
  notify_retention: boolean;
  notify_promo: boolean;
  notify_birthday: boolean;
  birthday_date: string | null; // ISO 8601 yyyy-mm-dd
  // DRF-1371 removed the free-text contraindications member. The column is
  // gone, GET /me no longer sends it, and PATCH /me now answers 400 for it —
  // re-adding it here would only let a screen write a field the API rejects.
}

export interface Profile {
  bot_user_id: string;
  display_name: string;
  client_name: string;
  phone_masked: string;
  timezone: string;
  joined_at: string; // ISO 8601 datetime
  preferences: Preferences;
  favorites: {
    master_name: string | null;
    service_name: string | null;
  };
}

export const fetchProfile = (): Promise<Profile> => request("/me", { method: "GET" });

export const updateProfile = (
  patch: Partial<Pick<Profile, "client_name" | "timezone">> & Partial<Preferences>,
): Promise<Profile> =>
  request("/me", { method: "PATCH", body: JSON.stringify(patch) });

export const deleteAccount = (): Promise<{ deleted: true }> =>
  request("/me/delete", {
    method: "POST",
    body: JSON.stringify({ confirmation: "УДАЛИТЬ" }),
  });

// --- feedback (Phase 4 / F5) ---
export interface FeedbackResult {
  booking_id: string;
  rating: number;
  comment: string;
  feedback_at: string;
  handoff_created: boolean;
  task_id: string | null;
}

export const submitFeedback = (
  bookingId: string,
  body: { rating: number; comment?: string },
): Promise<FeedbackResult> =>
  request(`/bookings/${bookingId}/feedback`, {
    method: "POST",
    body: JSON.stringify(body),
  });
