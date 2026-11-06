/**
 * DRF-2654 — чтение ссылки поддержки: пустое значение — то же, что незаданное.
 *
 * На пилоте кнопка «поддержка» вела на `https://max.me/aylasupport` — умолчание
 * из кода: сборка выкладки не задавала `VITE_SUPPORT_DEEPLINK`. Этот узел — о
 * чтении (`||`, а не `??`); отказ production-сборки без переменной — отдельная
 * правка. Пара: заполнена → ровно она; пустая → заглушка, а не пустой `href`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const REAL = "https://max.me/ayla_support_real";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("the link the person sees", () => {
  it("filled → exactly that link", async () => {
    vi.stubEnv("VITE_SUPPORT_DEEPLINK", REAL);
    const { SUPPORT_DEEPLINK } = await import("./customer-profile");
    expect(SUPPORT_DEEPLINK).toBe(REAL);
  });

  it("empty (dev, .env.local.example) → the placeholder, not an empty href", async () => {
    vi.stubEnv("VITE_SUPPORT_DEEPLINK", "");
    const { SUPPORT_DEEPLINK } = await import("./customer-profile");
    expect(SUPPORT_DEEPLINK).toBe("https://max.me/aylasupport");
  });
});
