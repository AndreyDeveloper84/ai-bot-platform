/** MAX Mini App Bridge adapter — typed thin wrapper around window.WebApp. */

interface MaxWebAppGlobal {
  readonly initData?: string;
  readonly initDataUnsafe?: Record<string, unknown>;
  ready?: () => void;
  expand?: () => void;
  close?: () => void;
  HapticFeedback?: {
    impactOccurred: (style: "light" | "medium" | "heavy") => void;
    notificationOccurred: (type: "success" | "warning" | "error") => void;
    selectionChanged: () => void;
  };
  BackButton?: {
    show: () => void;
    hide: () => void;
    onClick: (handler: () => void) => void;
    offClick: (handler: () => void) => void;
  };
  DeviceStorage?: {
    setItem: (
      key: string,
      value: string,
      cb?: (err: string | null, ok?: boolean) => void,
    ) => void;
    getItem?: (
      key: string,
      cb: (err: string | null, value?: string) => void,
    ) => void;
    removeItem?: (
      key: string,
      cb?: (err: string | null, ok?: boolean) => void,
    ) => void;
  };
  enableClosingConfirmation?: () => void;
  disableClosingConfirmation?: () => void;
  requestScreenMaxBrightness?: () => void;
  releaseScreenMaxBrightness?: () => void;
  shareMaxContent?: (content: { url?: string; text?: string }) => void;
  openLink?: (url: string) => void;
}

declare global {
  interface Window {
    WebApp?: MaxWebAppGlobal;
  }
}

export function maxBridge(): MaxWebAppGlobal | null {
  if (typeof window === "undefined") return null;
  return window.WebApp ?? null;
}

export function getInitData(): string {
  const fromBridge = maxBridge()?.initData ?? "";
  if (fromBridge) return fromBridge;
  const fromEnv = (import.meta.env.VITE_DEV_INIT_DATA as string | undefined) ?? "";
  return fromEnv;
}

/**
 * Deeplink payload passed by MAX when the Mini App is opened via an
 * ``open_app`` inline-keyboard button. Welcome skill emits flat-slug
 * payloads (e.g. ``open_catalog``); ``parseStartRoute`` maps them to
 * the matching React Router path.
 *
 * MAX delivers the button's ``payload`` field as
 * ``initDataUnsafe.start_param`` (mirroring Telegram WebApp's
 * convention). Returns empty string when:
 * - the Mini App wasn't opened via an open_app button (no payload),
 * - the WebApp bridge is unavailable (dev browser without
 *   ``VITE_DEV_INIT_DATA``),
 * - ``start_param`` is missing / not a string.
 *
 * Dev override: ``VITE_DEV_START_PARAM`` env var simulates a payload
 * when running ``npm run dev`` outside MAX.
 */
export function getStartPayload(): string {
  const fromBridge = maxBridge()?.initDataUnsafe;
  if (fromBridge && typeof fromBridge["start_param"] === "string") {
    return fromBridge["start_param"];
  }
  const fromEnv = (import.meta.env.VITE_DEV_START_PARAM as string | undefined) ?? "";
  return fromEnv;
}

/**
 * Welcome-skill deeplink → in-app path. Returns null for unknown / empty.
 *
 * CONTRACT MIRROR: this map is the consumer side of the payload values
 * emitted by ``apps/skills/welcome/skill.py::_welcome_buttons``. Adding
 * a new route here requires adding the matching button there in the
 * same PR, or the welcome menu will ship a dead deeplink.
 *
 * Two key formats accepted:
 *
 * * **Flat slug** (``open_catalog``, ``open_visits``, ``open_profile``)
 *   — current emit path. MAX validates an open_app button payload
 *   against ``^[A-Za-z0-9_-]{0,512}$`` (measured against the live API
 *   2026-08-30 — see ``OPEN_APP_PAYLOAD_RE`` in
 *   ``apps/channels/max/outbound.py``), so the flat-slug shape passes
 *   and anything with ``=``, ``&``, ``?``, ``.`` or ``:`` in it does not.
 * * **Legacy querystring inner-value** (``catalog``, ``visits``,
 *   ``profile``) — accepted via the ``route=<value>`` fallback below,
 *   kept for cold-start back-compat with stale message bodies in users'
 *   MAX history during the F2 rollout window.
 */
/**
 * Payload prefix for a master invitation (DRF-1349).
 *
 * The bot builds `${MASTER_INVITE_PAYLOAD_PREFIX}${token}` in
 * `apps/admin_api/views_invite.py` (constant of the same name there).
 * `apps/admin_api/tests/test_invite_entry.py` reads both sources and
 * fails when they drift, so this is a contract and not a coincidence.
 *
 * Exported because the pinning test needs to see it under a stable name
 * — and because a reader looking at the bot's constant should be able to
 * find this one by searching for the same identifier.
 */
export const MASTER_INVITE_PAYLOAD_PREFIX = "master_invite_";

/** Where a valid invite payload lands. Mounted in `App.tsx`. */
export const MASTER_ONBOARDING_PATH = "/onboarding/master";

/**
 * Strict shape of an invite payload: the prefix and a canonical UUID,
 * anchored at both ends.
 *
 * This is the first prefix-plus-parameter payload in this file, and the
 * looseness that would be natural here — `payload.startsWith(prefix)`
 * and forward the rest — is a hole, not a shortcut: the tail is
 * concatenated into a query string, so "anything after the prefix" means
 * whatever text a third party can get into a `start_param` ends up in
 * the app's own URL. A UUID is the only tail the backend can ever act
 * on (`validate_invite_token` looks the token up by UUID), so anything
 * else is refused here rather than forwarded and refused later.
 */
/**
 * Payload prefix for «перенести ЭТУ запись» (DRF-1547).
 *
 * The bot builds `${RESCHEDULE_PAYLOAD_PREFIX}${bookingId}` in
 * `apps/orchestrator/visits.py` (constant of the same name there), and
 * `apps/skills/welcome/skill.py::reschedule_route` builds the same path
 * for the link-button fallback. `test_miniapp_routes.py` reads both
 * sources and fails when they drift.
 *
 * Owner decision §37: the schedule is the one thing a chat cannot do
 * well, so it — and only it — opens the app, and only after the bot has
 * said so. A person who has already picked WHICH booking must not be
 * asked to pick it again here, so the payload carries the id.
 */
export const RESCHEDULE_PAYLOAD_PREFIX = "reschedule_";

/** Canonical address of the reschedule screen (DRF-1481). */
export const RESCHEDULE_PATH_PREFIX = "/customer/records";

const _MASTER_INVITE_RE = new RegExp(
  `^${MASTER_INVITE_PAYLOAD_PREFIX}` +
    "([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" +
    "[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$",
);

/**
 * Strict shape of a reschedule payload — the prefix and a canonical UUID,
 * anchored at both ends. Same rule and same reason as
 * `_MASTER_INVITE_RE` above: the tail becomes part of the app's own URL,
 * so "anything after the prefix" is a hole, not a shortcut.
 */
const _RESCHEDULE_RE = new RegExp(
  `^${RESCHEDULE_PAYLOAD_PREFIX}` +
    "([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" +
    "[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$",
);

const _ROUTE_MAP: Record<string, string> = {
  // Flat slug — current. Every target is the canonical /customer/* screen
  // and matches MINIAPP_ROUTES in apps/skills/welcome/skill.py exactly
  // (DRF-1326): the producer's link-button fallback builds these same
  // paths directly, so a slug and its link must not name different
  // screens. tests/test_miniapp_routes.py fails the build if they drift.
  //
  // Before DRF-1326 these three pointed at the legacy pre-reskin screens
  // (/catalog, /my-visits, /me) while the link fallback pointed at the
  // /customer/* ones — same button, two destinations depending on config.
  // /catalog and /my-visits stay mounted in App.tsx as compatibility
  // aliases; /me and its screen were removed with DRF-1485. None of the
  // three is what the welcome menu opens any more.
  open_catalog: "/customer/catalog",
  open_visits: "/customer/records",
  open_profile: "/customer/profile",
  // S5 first-action grid (welcome skill) — DRF-1167 fix. Each slug mirrors
  // the payload emitted by apps/skills/welcome/skill.py::_s5_first_action_buttons.
  open_food_scan: "/customer/food-scanner/capture",
  open_water_add_250: "/customer/wellness",
  open_goal_select: "/customer/goal-select",
  // Home = «Мои записи» (pilot phase 3.2 orchestrator decision, App.tsx
  // comment at the /customer/main route) — newer than the onboarding spec's
  // "Dashboard empty state".
  open_home: "/customer/main",
  // DRF-1491 — главное меню витрины (apps/skills/menu/marketplace.py).
  // Дневник питания и панель самочувствия: пункты меню есть, а слагов
  // не было. open_wellness ведёт туда же, куда open_water_add_250, —
  // старое имя оставлено ради клавиатур в истории чата.
  open_food_diary: "/customer/food-scanner/diary",
  open_wellness: "/customer/wellness",
  // Legacy querystring inner-values — kept for cold-start back-compat.
  // Same destinations as the flat slugs above: a stale `route=visits`
  // payload should land on today's records screen, not on a screen the
  // menu no longer opens.
  catalog: "/customer/catalog",
  visits: "/customer/records",
  profile: "/customer/profile",
};

/**
 * Parse a MAX start_param payload and return the in-app path, or ``null``
 * for empty / unknown / malformed input.
 *
 * Examples (destinations are whatever `_ROUTE_MAP` above says — keep these
 * in sync with it, they were left naming the pre-DRF-1326 screens):
 *   parseStartRoute("open_catalog")         → "/customer/catalog"
 *                                             (flat slug, `open_catalog:`)
 *   parseStartRoute("route=visits&ref=ig")  → "/customer/records"  (legacy
 *                                             form, inner value `visits:`)
 *   parseStartRoute("master_invite_<uuid>") → "/onboarding/master?token=<uuid>"
 *   parseStartRoute("open_unknown")         → null
 *   parseStartRoute("")                     → null
 *   parseStartRoute("garbage")              → null
 */
export function parseStartRoute(payload: string): string | null {
  if (!payload) return null;
  // Try flat-slug direct lookup first (current emit format).
  //
  // `Object.hasOwn` rather than a bare `_ROUTE_MAP[payload]`: the object
  // literal inherits from Object.prototype, so `"constructor"` and
  // `"toString"` resolve to functions. They are typed `string` here, and
  // `navigate(aFunction)` is not something React Router has an opinion
  // about. `start_param` is attacker-influenceable, so the lookup is
  // restricted to keys the table actually declares.
  const direct = Object.hasOwn(_ROUTE_MAP, payload) ? _ROUTE_MAP[payload] : undefined;
  if (direct) return direct;
  // Master invitation — the one payload that carries a parameter
  // (DRF-1349). Claimed by prefix and resolved by the strict form: a
  // payload that announces itself as an invitation and then isn't one is
  // refused outright rather than left to the querystring fallback below,
  // which would happily read `master_invite_x=1&route=catalog` as
  // `route=catalog`. Claiming the prefix is what makes the strictness
  // above mean something — matching order alone would not, since a
  // failed `exec` simply falls through.
  if (payload.startsWith(MASTER_INVITE_PAYLOAD_PREFIX)) {
    const invite = _MASTER_INVITE_RE.exec(payload);
    return invite ? `${MASTER_ONBOARDING_PATH}?token=${invite[1]}` : null;
  }
  // Перенос конкретной записи (DRF-1547) — второй и последний payload с
  // параметром. Claimed by prefix, resolved by the strict form, for the
  // same reason the invitation is: a payload that announces itself as a
  // reschedule and then isn't one is refused outright rather than left to
  // the querystring fallback below.
  if (payload.startsWith(RESCHEDULE_PAYLOAD_PREFIX)) {
    const move = _RESCHEDULE_RE.exec(payload);
    return move ? `${RESCHEDULE_PATH_PREFIX}/${move[1]}/reschedule` : null;
  }
  // Fall back to legacy querystring shape (``route=<value>``).
  if (payload.includes("=")) {
    const params = new URLSearchParams(payload);
    const route = params.get("route");
    if (route && Object.hasOwn(_ROUTE_MAP, route)) return _ROUTE_MAP[route] ?? null;
  }
  return null;
}

export function signalReady(): void {
  maxBridge()?.ready?.();
}

export function hapticSelection(): void {
  maxBridge()?.HapticFeedback?.selectionChanged?.();
}

export function hapticNotify(type: "success" | "warning" | "error"): void {
  maxBridge()?.HapticFeedback?.notificationOccurred?.(type);
}

export function hapticImpact(style: "light" | "medium" | "heavy" = "light"): void {
  maxBridge()?.HapticFeedback?.impactOccurred?.(style);
}

export function setClosingConfirmation(enabled: boolean): void {
  const b = maxBridge();
  if (!b) return;
  if (enabled) b.enableClosingConfirmation?.();
  else b.disableClosingConfirmation?.();
}

export function setBackButton(shown: boolean): void {
  const b = maxBridge()?.BackButton;
  if (!b) return;
  if (shown) b.show();
  else b.hide();
}

export function onBackButton(handler: () => void): () => void {
  const b = maxBridge()?.BackButton;
  if (!b) return () => undefined;
  b.onClick(handler);
  return () => b.offClick?.(handler);
}

/**
 * Persist a value in MAX's per-app device storage. Used for the master
 * session token after Step 3 (§M0 line 268). Degrades to localStorage
 * when the bridge is absent (dev browser).
 */
export function setDeviceStorage(key: string, value: string): void {
  const ds = maxBridge()?.DeviceStorage;
  if (ds?.setItem) {
    try {
      ds.setItem(key, value);
      return;
    } catch (err) {
      console.warn("[max-sdk] DeviceStorage.setItem failed, falling back", err);
    }
  }
  if (typeof window !== "undefined" && window.localStorage) {
    try {
      window.localStorage.setItem(`max:${key}`, value);
    } catch {
      /* private mode / quota — best effort */
    }
  }
}

/**
 * Remove a value from MAX's per-app device storage. Used by M8 logout
 * to clear the master session token. Mirrors ``setDeviceStorage``
 * shape: best-effort on the bridge, fall back to localStorage with
 * the ``max:`` prefix when the bridge is absent (dev browser / SSR).
 */
export function removeDeviceStorage(key: string): void {
  const ds = maxBridge()?.DeviceStorage;
  if (ds?.removeItem) {
    try {
      ds.removeItem(key);
      return;
    } catch (err) {
      console.warn("[max-sdk] DeviceStorage.removeItem failed, falling back", err);
    }
  }
  if (typeof window !== "undefined" && window.localStorage) {
    try {
      window.localStorage.removeItem(`max:${key}`);
    } catch {
      /* private mode / SSR — best effort */
    }
  }
}

/**
 * Открыть внешний адрес: в MAX — через оболочку (webview overlay), в
 * обычном браузере — новой вкладкой.
 *
 * Заведена как отдельная функция, потому что тем же механизмом
 * пользуется не только оплата (DRF-1319). Звать `openPaymentConfirmation`
 * ради OAuth значило бы назвать вход оплатой — имя, обвиняющее не тот
 * предмет, дороже лишней функции: по нему потом ищут не там.
 */
export function openExternalLink(url: string): void {
  const b = maxBridge();
  if (b?.openLink) {
    try {
      b.openLink(url);
      return;
    } catch (err) {
      console.warn("[max-sdk] openLink failed, falling back to window.open", err);
    }
  }
  if (typeof window !== "undefined") {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

/**
 * Open the payment confirmation page (C7.4) — YooKassa checkout URL
 * returned by the payment-create passthrough. In MAX we hand the URL
 * to the wrapper's openLink (webview overlay); in a plain browser we
 * open a new tab. Inert until the W3 passthrough returns the field.
 */
export function openPaymentConfirmation(url: string): void {
  const b = maxBridge();
  if (b?.openLink) {
    try {
      b.openLink(url);
      return;
    } catch (err) {
      console.warn("[max-sdk] openLink failed, falling back to window.open", err);
    }
  }
  if (typeof window !== "undefined") {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

/**
 * Ask MAX to close the Mini App. Falls back to ``history.back()`` when
 * the bridge isn't available — at least navigates the dev browser away.
 */
export function closeApp(): void {
  const b = maxBridge();
  if (b?.close) {
    try {
      b.close();
      return;
    } catch (err) {
      console.warn("[max-sdk] close() failed", err);
    }
  }
  if (typeof window !== "undefined" && window.history?.length > 1) {
    window.history.back();
  }
}
