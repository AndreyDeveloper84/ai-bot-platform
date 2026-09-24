import { defaultExclude, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

import { NODE_ENVIRONMENT_TESTS } from "./src/test/node-tests";

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
  // DRF-2389 — два набора тестов, два окружения.
  //
  // Замер 24.09.2026 (209 файлов, 2081 тест): полный прогон тратит на
  // ПОСТРОЕНИЕ окружения 1167 с рабочего времени против 332 с на самих
  // тестах — 60 % против 19 %. При этом 47 файлов не касаются DOM вовсе:
  // на них окружение стоило 261 с, а под node — 20 мс.
  //
  // Изоляция НЕ МЕНЯЕТСЯ: каждый файл по-прежнему получает своё свежее
  // окружение, просто дешёвое. Варианты, которые её меняют, замерены и
  // отвергнуты — см. `src/test/node-tests.ts`.
  test: {
    css: false,
    projects: [
      {
        extends: true,
        test: {
          name: "node",
          environment: "node",
          setupFiles: "./src/test/setup.node.ts",
          // Список ЯВНЫЙ и конечный, как реестр долга у линтера:
          // пополняется только после прогона файла под node.
          include: [...NODE_ENVIRONMENT_TESTS],
        },
      },
      {
        extends: true,
        test: {
          name: "dom",
          environment: "jsdom",
          setupFiles: "./src/test/setup.ts",
          include: ["src/**/*.test.{ts,tsx}"],
          // `defaultExclude` обязателен: своё `exclude` затирает умолчания
          // vitest (node_modules, dist), и прогон полез бы в зависимости.
          exclude: [...defaultExclude, ...NODE_ENVIRONMENT_TESTS],
        },
      },
    ],
  },
});
