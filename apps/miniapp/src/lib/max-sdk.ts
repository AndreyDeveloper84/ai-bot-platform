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
 *   — current emit path. MAX requires open_app button payload to match
 *   a restricted regex (no ``=``, no ``&``); the flat-slug shape passes.
 * * **Legacy querystring inner-value** (``catalog``, ``visits``,
 *   ``profile``) — accepted via the ``route=<value>`` fallback below,
 *   kept for cold-start back-compat with stale message bodies in users'
 *   MAX history during the F2 rollout window.
 */
const _ROUTE_MAP: Record<string, string> = {
  // Flat slug — current.
  open_catalog: "/catalog",
  open_visits: "/my-visits",
  open_profile: "/me",
  // Legacy querystring inner-values — kept for cold-start back-compat.
  catalog: "/catalog",
  visits: "/my-visits",
  profile: "/me",
};

/**
 * Parse a MAX start_param payload and return the in-app path, or ``null``
 * for empty / unknown / malformed input.
 *
 * Examples:
 *   parseStartRoute("open_catalog")         → "/catalog"
 *   parseStartRoute("route=visits&ref=ig")  → "/my-visits"  (legacy form)
 *   parseStartRoute("open_unknown")         → null
 *   parseStartRoute("")                     → null
 *   parseStartRoute("garbage")              → null
 */
export function parseStartRoute(payload: string): string | null {
  if (!payload) return null;
  // Try flat-slug direct lookup first (current emit format).
  const direct = _ROUTE_MAP[payload];
  if (direct) return direct;
  // Fall back to legacy querystring shape (``route=<value>``).
  if (payload.includes("=")) {
    const params = new URLSearchParams(payload);
    const route = params.get("route");
    if (route && _ROUTE_MAP[route]) return _ROUTE_MAP[route];
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
