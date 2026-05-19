/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Pre-signed initData string for browser-dev round-trip without MAX.
   * Sign with the same `MAX_BOT_TOKEN` the Django backend verifies
   * against — see `apps/miniapp/README.md`.
   */
  readonly VITE_DEV_INIT_DATA?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
