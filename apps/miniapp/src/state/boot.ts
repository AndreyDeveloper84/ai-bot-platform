/**
 * Re-run the `/api/v1/me` boot from a leaf screen (DRF-1434).
 *
 * `App` fetches `/me` once and caches the answer for the life of the
 * webview. That is right for a role that cannot change mid-session —
 * and wrong for the one flow where it does: accepting a master
 * invitation. `resolve_role` reports `is_master` only for a
 * `CatalogMaster` row that is ACCEPTED **and** linked to the calling
 * BotUser (`apps/identity/services/role_resolver.py`), so an invitee
 * boots with `is_master: false`, lands in `CustomerRoutes`, and stays
 * there after `POST /onboarding/accept` flips both fields server-side.
 *
 * `MasterOnboardingScreen` then navigated to `/master/dashboard`, which
 * `CustomerRoutes` does not mount, and its `<Route path="*">` rendered
 * `HelloScreen` — the client greeting, inside the master bot. No error
 * anywhere. This context is how the screen that changed the role tells
 * the shell to go and read it again.
 *
 * The default is a no-op so a screen rendered outside the provider
 * (unit tests of the screen alone) does not have to care.
 */

import { createContext, useContext } from "react";

/** Kick off a fresh `/api/v1/me`. Fire-and-forget — `App` owns the state. */
export type ReloadMe = () => void;

export const BootReloadContext = createContext<ReloadMe>(() => {});

export function useReloadMe(): ReloadMe {
  return useContext(BootReloadContext);
}
