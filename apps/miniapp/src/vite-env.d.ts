/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Pre-signed initData string for browser-dev round-trip without MAX.
   * Sign with the same `MAX_BOT_TOKEN` the Django backend verifies
   * against — see `apps/miniapp/README.md`.
   */
  readonly VITE_DEV_INIT_DATA?: string;
  /**
   * Real MAX support channel URL for Profile support-entry sheets
   * (152-ФЗ export/delete + R5 notification prefs). REQUIRED for
   * production builds — see the guard in `vite.config.ts` (#949).
   */
  readonly VITE_SUPPORT_DEEPLINK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
