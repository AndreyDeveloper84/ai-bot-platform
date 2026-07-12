import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// MAX Mini App is served as a static SPA. We embed the same shell at
// every route — the Mini App platform doesn't do server-side routing.
// Backend (Django) runs on :8000 in dev; Vite proxies /api/v1/customer/*
// so the frontend can use relative URLs identical to prod.
export default defineConfig(({ command, mode }) => {
  // #949: pilot 152-ФЗ export/delete + R5 notification requests route
  // through the Profile support deeplink. An unset URL ships a silent
  // 404 to the customer, so any build must fail fast instead. Keyed on
  // `command`, not `mode`: every `vite build` produces a deployable
  // dist/ regardless of --mode (so a custom mode must not bypass the
  // guard), while dev server and `vite preview` (command "serve",
  // preview's default mode is also "production") stay unaffected.
  const env = loadEnv(mode, __dirname, "");
  if (command === "build" && !env.VITE_SUPPORT_DEEPLINK) {
    throw new Error(
      "VITE_SUPPORT_DEEPLINK is not set. Production builds require the real " +
        "MAX support channel URL (see docs/runbooks/server-deployment.md §2.6 " +
        "and issue #949).",
    );
  }
  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: 5173,
      proxy: {
        "/api/v1/customer": {
          target: "http://localhost:8000",
          changeOrigin: true,
        },
      },
    },
    build: {
      target: "es2022",
      sourcemap: true,
      // MAX Mini App container is a recent Chromium — no legacy polyfills needed.
      cssCodeSplit: true,
    },
  };
});
