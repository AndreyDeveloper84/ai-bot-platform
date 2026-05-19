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
