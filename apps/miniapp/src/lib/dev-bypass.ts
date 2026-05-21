/**
 * Dev-mode init-data bypass — frontend header injector.
 *
 * Works ONLY when ALL of:
 *   - Vite is in dev mode (`import.meta.env.DEV === true`)
 *   - `VITE_DEV_BYPASS_USER_ID` is set in `.env.local`
 *   - `VITE_DEV_BYPASS_TENANT_SLUG` is set in `.env.local`
 *   - No real MAX initData is available (i.e. running in a plain browser)
 *
 * Pairs with `apps/miniapp_api/dev_bypass.py` on the backend, which only
 * honors these headers when `settings.DEBUG=True`. In production builds
 * (`import.meta.env.DEV === false`) the dead-code elimination removes
 * the inject branch entirely — the headers are not shipped to clients.
 *
 * Precedence: when real initData is available (MAX wrapper present), it
 * wins — we DO NOT inject dev headers, so the real HMAC path runs. This
 * means flipping between the MAX app and dev browser "just works"
 * without flipping .env.local toggles.
 */

import { getInitData } from "./max-sdk";

/**
 * Mutates the given `Headers` object in-place to add dev-bypass headers
 * when applicable. No-op in production builds and when MAX initData is
 * present.
 */
export function applyDevBypassHeaders(headers: Headers): void {
  // Dead-code-eliminated in production builds: `import.meta.env.DEV` is
  // a compile-time constant; the whole branch is dropped.
  if (!import.meta.env.DEV) return;

  // Real MAX initData wins — never overlay dev headers when a real
  // MAX session is active in the browser (e.g. opened inside MAX).
  if (getInitData()) return;

  const userId = import.meta.env.VITE_DEV_BYPASS_USER_ID as string | undefined;
  const tenantSlug = import.meta.env.VITE_DEV_BYPASS_TENANT_SLUG as
    | string
    | undefined;
  if (!userId || !tenantSlug) return;

  headers.set("X-Dev-Bypass", "1");
  headers.set("X-Dev-User-Id", userId);
  headers.set("X-Dev-Tenant-Slug", tenantSlug);
}
