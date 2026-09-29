/**
 * DRF-2654 (часть B) — production-сборка без ссылки поддержки отказывает.
 *
 * На пилоте кнопка «поддержка» вела на `https://max.me/aylasupport` — умолчание
 * из кода: сборка выкладки не задавала `VITE_SUPPORT_DEEPLINK`. Узел «ссылка
 * непустая» прошёл бы при живом дефекте — умолчание непустое. Поэтому тройка:
 * не задана → отказ; пустая → тот же отказ; заполнена → сборка. Под node:
 * esbuild (часть Vite) под jsdom не импортируется.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { missingInProduction, REQUIRED_IN_PRODUCTION } from "../../build-env";

const REAL = "https://max.me/ayla_support_real";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("the rule", () => {
  it("names the variable the owner has to set", () => {
    expect(REQUIRED_IN_PRODUCTION).toContain("VITE_SUPPORT_DEEPLINK");
  });

  it("unset → refused", () => {
    expect(missingInProduction("production", {})).toEqual(["VITE_SUPPORT_DEEPLINK"]);
  });

  it("empty or blank → refused the same way", () => {
    expect(missingInProduction("production", { VITE_SUPPORT_DEEPLINK: "" })).toEqual([
      "VITE_SUPPORT_DEEPLINK",
    ]);
    expect(missingInProduction("production", { VITE_SUPPORT_DEEPLINK: "   " })).toEqual([
      "VITE_SUPPORT_DEEPLINK",
    ]);
  });

  it("filled → nothing is missing", () => {
    expect(missingInProduction("production", { VITE_SUPPORT_DEEPLINK: REAL })).toEqual([]);
  });

  it("dev server and tests are not touched — an empty value is lawful there", () => {
    expect(missingInProduction("development", {})).toEqual([]);
    expect(missingInProduction("test", {})).toEqual([]);
  });
});

describe("the Vite config applies it", () => {
  it("production without the link throws by name; with it, builds; dev serves", async () => {
    vi.stubEnv("VITE_SUPPORT_DEEPLINK", "");
    const { default: config } = await import("../../vite.config");
    const build = config as unknown as (env: { mode: string; command: string }) => unknown;
    expect(() => build({ mode: "production", command: "build" })).toThrow(
      /VITE_SUPPORT_DEEPLINK/,
    );

    vi.stubEnv("VITE_SUPPORT_DEEPLINK", REAL);
    expect(() => build({ mode: "production", command: "build" })).not.toThrow();

    vi.stubEnv("VITE_SUPPORT_DEEPLINK", "");
    expect(() => build({ mode: "development", command: "serve" })).not.toThrow();
  });
});
