/**
 * DRF-2549 — сторож: подпись человека собирается в одном месте.
 *
 * До листа строку `MaxInitData ${…}` собирали 16 мест в 6 файлах, и запрет,
 * записанный в одном (не класть в `<img src>`, не класть в query), не
 * действовал в остальных. Сторож читает ВСЕ исходники приложения как текст
 * и краснеет на любой сборке вне `lib/auth-headers.ts`, называя файл и строку.
 *
 * Прозу (комментарии «Auth: MaxInitData …») он не трогает: ищет именно
 * сборку — литерал `MaxInitData ` в кавычках или с подстановкой `${`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { applyIdentityHeaders } from "./auth-headers";

const THE_PLACE = "/src/lib/auth-headers.ts";

/** Все исходники приложения, кроме узлов, — как текст. */
const SOURCES = import.meta.glob(["/src/**/*.{ts,tsx}", "!/src/**/*.test.{ts,tsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Сборка подписи: литерал `MaxInitData ` в кавычках, либо с подстановкой. */
const ASSEMBLY = /["'`]MaxInitData\s|MaxInitData\s*\$\{/;

export function findCarriers(sources: Record<string, string>, allowed: string): string[] {
  const hits: string[] = [];
  for (const [file, text] of Object.entries(sources)) {
    if (file === allowed) continue;
    text.split(/\r?\n/).forEach((line, i) => {
      if (ASSEMBLY.test(line)) hits.push(`${file}:${i + 1}`);
    });
  }
  return hits.sort();
}

describe("подпись собирается в одном месте", () => {
  it("охват не пуст: сторож видит всё приложение, включая само место сборки", () => {
    const files = Object.keys(SOURCES);
    // Пустой охват прошёл бы «ноль носителей» молча. На 26.09 исходников
    // без узлов — 211; порог — нижняя граница «glob нашёл приложение».
    expect(files.length).toBeGreaterThan(150);
    expect(files).toContain(THE_PLACE);
    expect(files).toContain("/src/lib/api.ts");
    expect(ASSEMBLY.test(SOURCES[THE_PLACE] ?? "")).toBe(true);
  });

  it("вне lib/auth-headers.ts сборок подписи — ноль", () => {
    expect(findCarriers(SOURCES, THE_PLACE)).toEqual([]);
  });

  it("подмена: копия в любом файле называется файлом и строкой", () => {
    const planted = {
      ...SOURCES,
      "/src/lib/rogue-client.ts": [
        "const headers = new Headers();",
        "headers.set(\"Authorization\", `MaxInitData ${getInitData()}`);",
      ].join("\n"),
      "/src/screens/RogueScreen.tsx": "const url = `/photo?auth=MaxInitData ${x}`;",
    };

    expect(findCarriers(planted, THE_PLACE)).toEqual([
      "/src/lib/rogue-client.ts:2",
      "/src/screens/RogueScreen.tsx:1",
    ]);
  });

  it("проза не носитель: упоминание в комментарии не краснеет", () => {
    const prose = { "/src/lib/doc.ts": " * Auth: same header (`Authorization: MaxInitData <raw>`)." };
    // «`Authorization: MaxInitData <raw>`» — кавычка перед Authorization, не перед MaxInitData.
    expect(findCarriers(prose, THE_PLACE)).toEqual([]);
  });
});

describe("конверт ведёт себя как прежние шестнадцать копий", () => {
  afterEach(() => {
    vi.doUnmock("./max-sdk");
    vi.resetModules();
  });

  it("есть initData — подпись в заголовке", async () => {
    vi.doMock("./max-sdk", () => ({ getInitData: () => "signed-launch" }));
    vi.resetModules();
    const { applyIdentityHeaders: apply } = await import("./auth-headers");

    const headers = apply(new Headers());

    expect(headers.get("Authorization")).toBe("MaxInitData signed-launch");
  });

  it("нет initData — подписи нет (не пустая, не «undefined»)", async () => {
    vi.doMock("./max-sdk", () => ({ getInitData: () => "" }));
    vi.resetModules();
    const { applyIdentityHeaders: apply } = await import("./auth-headers");

    expect(apply(new Headers()).has("Authorization")).toBe(false);
  });

  it("возвращает тот же объект заголовков — вызов на месте прежних трёх строк", () => {
    const headers = new Headers({ "Content-Type": "application/json" });
    expect(applyIdentityHeaders(headers)).toBe(headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });
});
