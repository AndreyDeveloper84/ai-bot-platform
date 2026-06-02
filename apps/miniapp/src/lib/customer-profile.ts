/**
 * Customer profile stub lib — Tier 1 Priority 6 Phase B (deferred Variant 3).
 *
 * Spec: `docs/screens/customer-profile-flow.md` (current dev: deferred
 * version post commit `376784e`, NOT Tau #843 original).
 *
 * # Pilot scope (per §0 tech-lead recon 2026-06-01)
 *
 * ## IN scope (built):
 *   - R1 header read: display_name + max_handle + tenant_names
 *   - R2 consent toggles read/write (marketing only — 3 other rows
 *     are locked info rows, not toggles per spec §4.1)
 *   - R4 proactive on/off read/write
 *
 * ## DEFERRED for pilot (NOT wired):
 *   - R2 export endpoint (`/me/export`) — bot-platform data only, no
 *     Ayla data, no unified cross-service export → routes to support
 *   - R2 delete endpoint (`/me/delete`) — bot-platform hard-delete vs
 *     beautygo soft-delete + Appointments/Payments FK PROTECT, no
 *     cross-service hook (ADR-0015 not ratified) → routes to support
 *   - R3 memory summary + clear — backend layer not built
 *     (no `UserPersonalContext`/`MemoryEntry` tables) → coming-soon
 *     card per spec §5
 *
 * # Contracts (W4 follow-ups per spec §12.2 / §12.3 P-1)
 *
 *   GET  /api/v1/me                       → MeProfileResponse
 *   GET  /api/v1/me/consents              → ConsentsResponse
 *   POST /api/v1/me/consents/marketing    → ConsentsResponse (toggle)
 *   GET  /api/v1/me/proactive_opt_out     → ProactivePrefsResponse
 *   POST /api/v1/me/proactive_opt_out     → ProactivePrefsResponse (toggle)
 *
 * WIRING NOTE: this module ships STUBS only. W4 owns the proxy
 * endpoints; swap stub function bodies to `request(...)` calls when
 * W4 ships. Function signatures DO NOT change. See FOLLOW_UP P-1.
 *
 * # Stub variants for dev QA (Records / Wellness pattern reuse)
 *
 *   ?stub=default — multi-tenant happy path (Анна Петрова, 3 салона)
 *   ?stub=new_user — first-time, single tenant, all consents at default
 *   ?stub=multi   — same as default (alias kept for explicit naming)
 *
 * Production bundle aliases all variants to default via `import.meta
 * .env.DEV` guard (Wellness PR #886 lesson — the `?stub=` query was a
 * phishing vector when read in prod).
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
 */

// ---------------------------------------------------------------------------
// Contract types — verbatim per spec §12. Marketing toggle is the ONLY
// editable consent in R2; the other 3 rows render as locked info rows.
// ---------------------------------------------------------------------------

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
  /** OFF default per spec §4.1 — opt-in only. The single editable toggle. */
  marketing_consent: boolean;
  /** ISO timestamp of overall 152-ФЗ consent (from onboarding). */
  data_storage_consent_at: string;
}

export interface ProactivePrefsResponse {
  /**
   * True = proactive AI off. Default false per spec §6.1 (R4 toggle
   * default ON = opt_out false). Engagement-class B11 messages gate
   * on this; transactional B5/B6 bypass (see spec §6.1).
   */
  proactive_messages_opt_out: boolean;
}

/**
 * Support deeplink placeholder (Q5 ambiguity per Phase A recon).
 *
 * FOLLOW_UP issue P-7 (pilot): file in deployment runbook + Phase J
 * follow-up to wire real support channel URL from
 * `apps/channels/max/` config. For pilot we render `https://max.me/aylasupport`
 * placeholder; both R2 deferred sheets (export + delete) and R5
 * notification-prefs entry route to the same deeplink to keep the
 * pilot truthful — Profile NEVER promises an in-app action it cannot
 * complete.
 */
export const SUPPORT_DEEPLINK = "https://max.me/aylasupport";

// ---------------------------------------------------------------------------
// Stub variant picker — dev-only QA hook (mirror customer-records.ts).
// ---------------------------------------------------------------------------

type StubVariant = "default" | "new_user" | "multi";

function pickStubVariant(): StubVariant {
  if (!import.meta.env.DEV) return "default";
  if (typeof window === "undefined") return "default";
  try {
    const sp = new URLSearchParams(window.location.search);
    const v = sp.get("stub");
    if (v === "new_user" || v === "multi") return v;
  } catch {
    /* SSR / parse failure */
  }
  return "default";
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

// In-memory mutable consent state (per-session). Production swap will
// persist server-side; for dev QA the toggle change must be visibly
// reflected on subsequent reads.
const CONSENTS_STATE: Record<StubVariant, ConsentsResponse> = {
  default: {
    is_booking_pii_locked: true,
    is_master_data_locked: true,
    marketing_consent: false,
    data_storage_consent_at: "2026-05-14T10:30:00+03:00",
  },
  new_user: {
    is_booking_pii_locked: true,
    is_master_data_locked: true,
    marketing_consent: false,
    data_storage_consent_at: "2026-05-30T12:00:00+03:00",
  },
  multi: {
    is_booking_pii_locked: true,
    is_master_data_locked: true,
    marketing_consent: false,
    data_storage_consent_at: "2026-05-14T10:30:00+03:00",
  },
};

const PROACTIVE_STATE: Record<StubVariant, ProactivePrefsResponse> = {
  default: { proactive_messages_opt_out: false },
  new_user: { proactive_messages_opt_out: false },
  multi: { proactive_messages_opt_out: false },
};

// ---------------------------------------------------------------------------
// Fetch wrappers — stubs in DEV; production must swap to real endpoints
// once W4 ships. Function signatures DO NOT change.
// ---------------------------------------------------------------------------

function devWarn(msg: string): void {
  if (import.meta.env.DEV && typeof console !== "undefined") {
    // eslint-disable-next-line no-console
    console.warn(`[customer-profile stub] ${msg}`);
  }
}

/**
 * Production guard — if this module ships to prod BEFORE W4 wires the
 * real endpoints (per spec §12.2 + follow-up P-1), every customer
 * would see «Анна Петрова» as their own profile, plus a hardcoded
 * `data_storage_consent_at` they never gave, plus a marketing toggle
 * that persists fake «saved» without a server record. That's a
 * 152-ФЗ truthfulness violation against the founder-locked Variant 3
 * rule («Profile NEVER promises an in-app action it cannot complete»).
 *
 * Until W4 swaps stub bodies to real `request(...)` calls, prod-mode
 * fetch must surface an explicit error → `StateError` renders, NOT
 * fake identity. Adversarial CR caught this as «M1 stub leak in
 * production» (ship gate, addressed inline).
 */
class StubNotWiredError extends Error {
  constructor() {
    super(
      "Профиль ещё не подключён. Загрузка временно недоступна. Попробуй позже.",
    );
    this.name = "StubNotWiredError";
  }
}

function guardProd(endpoint: string): void {
  if (!import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.error(
      `[customer-profile] ${endpoint} called in production with no W4 wire-up. ` +
        "See docs/screens/customer-profile-flow.md §12.2 (P-1).",
    );
    throw new StubNotWiredError();
  }
}

export async function fetchMe(): Promise<MeProfileResponse> {
  guardProd("GET /api/v1/me");
  devWarn(
    "GET /api/v1/me served from stub — swap when W4 ships canonical proxy",
  );
  const v = pickStubVariant();
  return ME_STUB[v];
}

export async function fetchConsents(): Promise<ConsentsResponse> {
  guardProd("GET /api/v1/me/consents");
  devWarn(
    "GET /api/v1/me/consents served from stub — W4 follow-up P-1",
  );
  const v = pickStubVariant();
  // Return a copy so callers cannot mutate stub state directly.
  return { ...CONSENTS_STATE[v] };
}

export async function setMarketingConsent(
  next: boolean,
): Promise<ConsentsResponse> {
  guardProd("POST /api/v1/me/consents/marketing");
  devWarn(
    "POST /api/v1/me/consents/marketing served from stub — W4 follow-up P-1",
  );
  const v = pickStubVariant();
  CONSENTS_STATE[v] = { ...CONSENTS_STATE[v], marketing_consent: next };
  return { ...CONSENTS_STATE[v] };
}

export async function fetchProactivePrefs(): Promise<ProactivePrefsResponse> {
  guardProd("GET /api/v1/me/proactive_opt_out");
  devWarn(
    "GET /api/v1/me/proactive_opt_out served from stub — W4 follow-up P-1",
  );
  const v = pickStubVariant();
  return { ...PROACTIVE_STATE[v] };
}

export async function setProactiveOptOut(
  optOut: boolean,
): Promise<ProactivePrefsResponse> {
  guardProd("POST /api/v1/me/proactive_opt_out");
  devWarn(
    "POST /api/v1/me/proactive_opt_out served from stub — W4 follow-up P-1",
  );
  const v = pickStubVariant();
  PROACTIVE_STATE[v] = { proactive_messages_opt_out: optOut };
  return { ...PROACTIVE_STATE[v] };
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
 */
export function formatConsentDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString("ru-RU", {
      day: "numeric",
      month: "long",
      year: "numeric",
    });
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
