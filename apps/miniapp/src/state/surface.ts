/**
 * Which top-level surface a multi-role user is currently working in.
 *
 * Extracted from `App.tsx` (was: `UNIFIED_LAST_SURFACE_KEY` +
 * `readLastSurface` / `writeLastSurface` local to that module) for two
 * reasons:
 *
 *   1. The «Сменить режим» action lives inside leaf screens
 *      (`AdminSettingsPlaceholderScreen`, `MasterSettingsScreen`,
 *      `CustomerProfileScreen`). Those are imported BY `App.tsx`, so a
 *      shared helper had to move out of it to avoid an import cycle.
 *   2. The choice now drives the top-level routing cascade, not just a
 *      landing redirect — so a plain `localStorage` read at render time
 *      is no longer enough. Writing the key has to re-render `App`.
 *      Hence the tiny subscribe/emit store below (same shape as
 *      `state/booking.ts`) consumed through `useSyncExternalStore`.
 *
 * Persistence is best-effort `localStorage` with the `max:` prefix the
 * rest of the app uses for the DeviceStorage fallback (see
 * `setDeviceStorage` in lib/max-sdk.ts). Synchronous access is required
 * because the value is read during render to pick a surface; the real
 * MAX DeviceStorage bridge is async (callback-based), so we stick to
 * localStorage and degrade gracefully when it is unavailable (private
 * mode / SSR).
 */

import { useSyncExternalStore } from "react";

/**
 * `customer` joined `admin` / `master` in DRF surface-switch: an owner
 * who is also a master had NO way to reach the customer surface — the
 * routing cascade in `App` only fell through to `CustomerRoutes` for a
 * user with no roles at all, so the person who owns the salon could
 * never see what her clients see.
 */
export type UnifiedSurface = "admin" | "master" | "customer";

export const UNIFIED_LAST_SURFACE_KEY = "max:unified_last_surface";

const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

/**
 * Transient «show me the chooser» flag — deliberately NOT persisted.
 *
 * «Сменить режим» clears the stored choice and raises this flag. For a
 * dual-role team member the cleared key alone would already land them
 * on the chooser, but a solo provider bypasses the chooser by design
 * (Tau §5.1 — she lands on /solo/my-day), so an explicit request is the
 * only thing that can bring the chooser up for her. Being transient is
 * the point: a reload while the chooser is open restores each user's
 * normal default instead of trapping them in a chooser loop.
 */
let chooserRequested = false;

export function readLastSurface(): UnifiedSurface | null {
  if (typeof window === "undefined" || !window.localStorage) return null;
  try {
    const v = window.localStorage.getItem(UNIFIED_LAST_SURFACE_KEY);
    return v === "admin" || v === "master" || v === "customer" ? v : null;
  } catch {
    return null;
  }
}

export function writeLastSurface(s: UnifiedSurface): void {
  chooserRequested = false;
  if (typeof window !== "undefined" && window.localStorage) {
    try {
      window.localStorage.setItem(UNIFIED_LAST_SURFACE_KEY, s);
    } catch {
      /* private mode / quota — best effort */
    }
  }
  emit();
}

/** Drops the persisted choice (role revocation cleanup + «Сменить режим»). */
export function clearLastSurface(): void {
  if (typeof window !== "undefined" && window.localStorage) {
    try {
      window.localStorage.removeItem(UNIFIED_LAST_SURFACE_KEY);
    } catch {
      /* SSR / private mode / quota — best effort */
    }
  }
  emit();
}

/** Raises the transient chooser flag — see `chooserRequested`. */
export function requestSurfaceChooser(): void {
  chooserRequested = true;
  emit();
}

export function isSurfaceChooserRequested(): boolean {
  return chooserRequested;
}

/** Full reset — used by tests to keep module-global state order-independent. */
export function resetSurfaceState(): void {
  chooserRequested = false;
  clearLastSurface();
}

/** Persisted surface choice, re-rendering the caller when it changes. */
export function useLastSurface(): UnifiedSurface | null {
  return useSyncExternalStore(subscribe, readLastSurface, () => null);
}

/** Transient chooser flag, re-rendering the caller when it changes. */
export function useSurfaceChooserRequested(): boolean {
  return useSyncExternalStore(
    subscribe,
    isSurfaceChooserRequested,
    () => false,
  );
}
