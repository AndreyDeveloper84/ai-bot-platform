/**
 * DRF-2549 — сторож: подпись человека собирается и достаётся в одном месте.
 *
 * До листа строку `MaxInitData ${…}` собирали 16 мест в 6 файлах, и запрет,
 * записанный в одном (не класть в `<img src>`, не класть в query), не
 * действовал в остальных. Сторож читает ВСЕ исходники приложения как текст
 * и держит два правила, называя нарушение файлом и строкой:
 *
 * 1. **строковый литерал со словом `MaxInitData`** — только в
 *    `lib/auth-headers.ts`. Ловит и шаблон `MaxInitData ${x}`, и
 *    `"MaxInitData" + …`, и константу-схему, и `join`;
 * 2. **имя `getInitData`** (вызов, импорт под другим именем, доступ через
 *    пространство имён) — только в `lib/auth-headers.ts` и трёх файлах,
 *    которым сырой пакет нужен не для заголовка. Это правило и охраняет оба
 *    запрета: подписи негде попасть в адрес картинки или в query, если её
 *    нельзя достать мимо места сборки — слово `MaxInitData` для утечки
 *    писать не обязательно;
 * 3. **прямое чтение пакета** (`.initData` у моста MAX, `VITE_DEV_INIT_DATA`) —
 *    только в `lib/max-sdk.ts`: иначе правило 2 обходится мимо `getInitData`.
 *
 * Строки-комментарии (`//`, `*`, `/*`) не проверяются: проза не носитель.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { applyIdentityHeaders } from "./auth-headers";

const THE_PLACE = "/src/lib/auth-headers.ts";

/** Кому сырой `getInitData()` законно нужен — и зачем. */
const INIT_DATA_READERS = [
  THE_PLACE, // сборка заголовка
  "/src/lib/max-sdk.ts", // определение
  "/src/lib/dev-bypass.ts", // обход для разработки — только когда подписи нет
  "/src/lib/identity.ts", // «есть ли личность»: пустая строка или нет
];

/** Все исходники приложения, кроме узлов, — как текст. */
const SOURCES = import.meta.glob(["/src/**/*.{ts,tsx}", "!/src/**/*.test.{ts,tsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Строковый литерал, в котором есть слово MaxInitData. */
const SIGNATURE_LITERAL = /["'`][^"'`\n]*MaxInitData/;
/** Имя доступа к сырому пакету — вызов, импорт, алиас, пространство имён. */
const RAW_INIT_DATA = /\bgetInitData\b/;
/** Чтение пакета прямо из моста MAX или из сборочной переменной. */
const BRIDGE_READ = /\.initData\b|VITE_DEV_INIT_DATA/;
const THE_BRIDGE = "/src/lib/max-sdk.ts";

function isComment(line: string): boolean {
  const t = line.trimStart();
  return t.startsWith("//") || t.startsWith("*") || t.startsWith("/*");
}

function findHits(sources: Record<string, string>, pattern: RegExp, allowed: string[]): string[] {
  const hits: string[] = [];
  for (const [file, text] of Object.entries(sources)) {
    if (allowed.includes(file)) continue;
    text.split(/\r?\n/).forEach((line, i) => {
      if (!isComment(line) && pattern.test(line)) hits.push(`${file}:${i + 1}`);
    });
  }
  return hits.sort();
}

const carriers = (s: Record<string, string>) => findHits(s, SIGNATURE_LITERAL, [THE_PLACE]);
const rawReaders = (s: Record<string, string>) => findHits(s, RAW_INIT_DATA, INIT_DATA_READERS);
/** Где пакет законно назван: мост читает его; объявление типа только описывает. */
const BRIDGE_ALLOWED = [THE_BRIDGE, "/src/vite-env.d.ts"];
const bridgeReaders = (s: Record<string, string>) => findHits(s, BRIDGE_READ, BRIDGE_ALLOWED);

describe("охват", () => {
  it("не пуст: сторож видит всё приложение, место сборки и всех законных читателей", () => {
    const files = Object.keys(SOURCES);
    // Пустой охват прошёл бы «ноль нарушений» молча. На 26.09 исходников
    // без узлов — 212; порог — нижняя граница «glob нашёл приложение».
    expect(files.length).toBeGreaterThan(150);
    for (const f of INIT_DATA_READERS) expect(files).toContain(f);
    expect(SIGNATURE_LITERAL.test(SOURCES[THE_PLACE] ?? "")).toBe(true);
    expect(RAW_INIT_DATA.test(SOURCES[THE_PLACE] ?? "")).toBe(true);
    expect(BRIDGE_READ.test(SOURCES[THE_BRIDGE] ?? "")).toBe(true);
  });
});

describe("правило 1: литерал подписи — только в месте сборки", () => {
  it("вне lib/auth-headers.ts — ноль", () => {
    expect(carriers(SOURCES)).toEqual([]);
  });

  it("подмена: любая форма сборки называется файлом и строкой", () => {
    const planted = {
      "/src/lib/a.ts": "h.set(\"Authorization\", `MaxInitData ${d}`);",
      "/src/lib/b.ts": 'const v = "MaxInitData" + " " + d;',
      "/src/lib/c.ts": 'const SCHEME = "MaxInitData";',
      "/src/lib/d.ts": '["MaxInitData", d].join(" ");',
    };
    expect(carriers(planted)).toEqual(["/src/lib/a.ts:1", "/src/lib/b.ts:1", "/src/lib/c.ts:1", "/src/lib/d.ts:1"]);
  });

  it("проза не носитель — ни в какой кавычке", () => {
    const prose = {
      "/src/lib/doc.ts": [" * Auth: `Authorization: MaxInitData <raw>`.", "// `MaxInitData <raw>`"].join("\n"),
    };
    expect(carriers(prose)).toEqual([]);
  });
});

describe("правило 2: сырой пакет запуска — только законным читателям", () => {
  it("вне четырёх файлов — ноль", () => {
    expect(rawReaders(SOURCES)).toEqual([]);
  });

  it("подмена: подпись в адрес картинки или в query без слова MaxInitData — поймана", () => {
    const planted = {
      "/src/screens/Rogue.tsx": "<img src={`/photo?init=${getInitData()}`} />",
      "/src/lib/rogue.ts": "const url = `/api/x?sig=${encodeURIComponent(getInitData())}`;",
    };
    expect(rawReaders(planted)).toEqual(["/src/lib/rogue.ts:1", "/src/screens/Rogue.tsx:1"]);
  });

  it("подмена: импорт под другим именем и доступ через пространство имён — пойманы", () => {
    const planted = {
      "/src/lib/alias.ts": 'import { getInitData as g } from "./max-sdk";',
      "/src/lib/ns.ts": "const sig = sdk.getInitData;",
    };
    expect(rawReaders(planted)).toEqual(["/src/lib/alias.ts:1", "/src/lib/ns.ts:1"]);
  });
});

describe("правило 3: пакет читается из моста только в max-sdk.ts", () => {
  it("вне lib/max-sdk.ts (и объявления типа в vite-env.d.ts) — ноль", () => {
    expect(bridgeReaders(SOURCES)).toEqual([]);
  });

  it("подмена: мимо getInitData прямо из моста или из сборочной переменной — поймано", () => {
    const planted = {
      "/src/screens/Direct.tsx": "const sig = window.WebApp?.initData ?? '';",
      "/src/lib/env.ts": "const sig = import.meta.env.VITE_DEV_INIT_DATA;",
    };
    expect(bridgeReaders(planted)).toEqual(["/src/lib/env.ts:1", "/src/screens/Direct.tsx:1"]);
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
