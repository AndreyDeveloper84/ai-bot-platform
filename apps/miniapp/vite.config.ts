import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// MAX Mini App is served as a static SPA. We embed the same shell at
// every route — the Mini App platform doesn't do server-side routing.
// Backend (Django) runs on :8000 in dev; Vite proxies /api/v1/customer/*
// so the frontend can use relative URLs identical to prod.
export default defineConfig({
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
});
