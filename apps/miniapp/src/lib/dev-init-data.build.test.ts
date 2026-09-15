// @vitest-environment node
/**
 * DRF-1893 — в прод-бандле нет подписанного initData.
 *
 * `getInitData()` читал `VITE_DEV_INIT_DATA` без проверки режима: если
 * переменная задана при сборке, прод-бандл вёз заранее подписанный initData и
 * Mini App «входил» мимо MAX. Здесь собирается настоящий прод-бандл с
 * маркером в переменной и проверяется, что маркера в выходе нет.
 *
 * Положительная стража: в том же бандле есть префикс заголовка входа
 * `MaxInitData` — скан не пуст и смотрит на код клиента.
 */
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { build } from "vite";
import { afterAll, describe, expect, it } from "vitest";

const MARKER = "hash=dev-signed-initdata-marker-1893";
const ROOT = resolve(__dirname, "../..");
const OUT = mkdtempSync(join(tmpdir(), "miniapp-1893-"));

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

afterAll(() => {
  rmSync(OUT, { recursive: true, force: true });
});

describe("прод-сборка", () => {
  it(
    "не содержит VITE_DEV_INIT_DATA",
    async () => {
      const previous = process.env.VITE_DEV_INIT_DATA;
      // vitest выставляет NODE_ENV=test, и Vite собрал бы бандл с
      // import.meta.env.DEV=true — это не прод. CI зовёт `npx vite build`
      // без NODE_ENV, то есть production; повторяем именно его.
      const previousNodeEnv = process.env.NODE_ENV;
      process.env.NODE_ENV = "production";
      process.env.VITE_DEV_INIT_DATA = `user=%7B%7D&${MARKER}`;
      try {
        await build({
          root: ROOT,
          mode: "production",
          logLevel: "silent",
          build: { outDir: OUT, emptyOutDir: true, sourcemap: false },
        });
      } finally {
        if (previous === undefined) delete process.env.VITE_DEV_INIT_DATA;
        else process.env.VITE_DEV_INIT_DATA = previous;
        process.env.NODE_ENV = previousNodeEnv;
      }
      const bundle = files(OUT)
        .filter((f) => f.endsWith(".js"))
        .map((f) => readFileSync(f, "utf8"))
        .join("\n");

      expect(bundle).toContain("MaxInitData");
      expect(bundle).not.toContain(MARKER);
    },
    240_000,
  );
});
