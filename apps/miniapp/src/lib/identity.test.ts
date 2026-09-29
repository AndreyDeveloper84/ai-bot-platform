/**
 * DRF-1319 B — «аноним» определён ровно один раз, и это не «аноним».
 *
 * Две проверки:
 *
 * 1. Поведение `channelIdentity()` при пустом и непустом `initData`, и
 *    что `subjectIdentity()` только читает серверный блок, а не выводит.
 * 2. Сторож на исходники: ни один экран, хук или компонент не проверяет
 *    `getInitData()` на пустоту сам. Второе определение — то, с чего всё
 *    началось (два независимых `getInitData() === ""`, замер #1539 §4.2),
 *    и ничто, кроме этого теста, не мешает ему вернуться.
 *
 * Исходники читаются через `import.meta.glob(..., ?raw)` — тот же приём,
 * что в `screens/backContract.test.ts`, и по той же причине (`node:fs`
 * в пакете не типизирован).
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import * as maxSdk from "./max-sdk";
import { channelIdentity, hasChannelIdentity, subjectIdentity } from "./identity";
import type { AuthVerifyResponse } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("channelIdentity", () => {
  it("initData есть → identified", () => {
    vi.spyOn(maxSdk, "getInitData").mockReturnValue("query_id=1&user=%7B%7D&hash=abc");
    expect(channelIdentity()).toBe("identified");
    expect(hasChannelIdentity()).toBe(true);
  });

  it("initData пуст → no_init_data, не «anonymous»", () => {
    vi.spyOn(maxSdk, "getInitData").mockReturnValue("");
    expect(channelIdentity()).toBe("no_init_data");
    expect(hasChannelIdentity()).toBe(false);
  });
});

describe("subjectIdentity", () => {
  const base: AuthVerifyResponse = {
    user: { id: "u1", channel_user_id: "1", display_name: "Ира", client_name: "" },
    tenant: { slug: "t", name: "T", timezone: "Europe/Moscow" },
    pending_booking_intent: null,
  };

  it("возвращает серверный блок как есть", () => {
    const identity = { channel: "identified" as const, subject: "linked" as const, ayla_user_id: "a-1" };
    expect(subjectIdentity({ ...base, identity })).toEqual(identity);
  });

  it("сервер без блока → null («неизвестно»), а не «не привязан»", () => {
    expect(subjectIdentity(base)).toBeNull();
  });
});

// ─── сторож: одно определение ─────────────────────────────────────────────

const SOURCES = import.meta.glob(
  ["../screens/**/*.tsx", "../components/**/*.tsx", "../hooks/**/*.ts", "../lib/**/*.ts", "../App.tsx"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

/** Проверка initData на пустоту в любом написании. */
const EMPTY_INIT_DATA_CHECK = /getInitData\(\)\s*(===|!==|==|!=)\s*(""|''|``)|!\s*getInitData\(\)|getInitData\(\)\s*\?/;

/**
 * Комментарии — не код. Без этого сторож ловил бы собственное объяснение
 * («здесь стояло `getInitData() === ""`») и краснел бы на памятке о том,
 * чего больше нет. Снимаются `//`-строки и блоки `/* … *\/`.
 */
function withoutComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

describe("одно определение пустого initData", () => {
  it("вне lib/identity.ts никто не проверяет getInitData() на пустоту", () => {
    // Пути из glob — относительно этого файла: `./identity.ts`, `../screens/…`.
    const offenders = Object.entries(SOURCES)
      .filter(([path]) => !path.endsWith(".test.ts") && !path.endsWith(".test.tsx"))
      .filter(([path]) => path !== "./identity.ts")
      // dev-bypass переключает режим разработки, а не опознаёт человека —
      // единственное разрешённое исключение, названное здесь, а не спрятанное.
      .filter(([path]) => path !== "./dev-bypass.ts")
      .filter(([, src]) => EMPTY_INIT_DATA_CHECK.test(withoutComments(src)))
      .map(([path]) => path);
    expect(offenders, "второе определение «анонима» вернулось").toEqual([]);
  });

  it("сторож видит исходники (не ослеп)", () => {
    const paths = Object.keys(SOURCES);
    expect(paths.some((p) => p.endsWith("CustomerBookingConfirmScreen.tsx"))).toBe(true);
    expect(paths.some((p) => p.endsWith("CustomerSlotsScreen.tsx"))).toBe(true);
    expect(paths).toContain("./identity.ts");
    expect(paths).toContain("./dev-bypass.ts");
  });

  it("сторож умеет краснеть: старое определение он бы поймал", () => {
    expect(EMPTY_INIT_DATA_CHECK.test('function isAnonymous() { return getInitData() === ""; }')).toBe(true);
    expect(EMPTY_INIT_DATA_CHECK.test("if (!getInitData()) return;")).toBe(true);
  });

  it("сторож не краснеет на упоминании в комментарии", () => {
    const src = [
      '// раньше: getInitData() === ""',
      "const x = channelIdentity();",
      "/* и !getInitData() */",
    ].join("\n");
    expect(EMPTY_INIT_DATA_CHECK.test(withoutComments(src))).toBe(false);
    // …но код под комментарием видит.
    const withCode = src + "\n" + 'if (getInitData() === "") {}';
    expect(EMPTY_INIT_DATA_CHECK.test(withoutComments(withCode))).toBe(true);
  });
});
