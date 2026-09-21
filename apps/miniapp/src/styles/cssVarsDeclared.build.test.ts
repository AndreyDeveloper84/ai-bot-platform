/**
 * Инцидент 21.09 (DRF-2150, скрин владельца): в чате «Ayla» мастера последняя
 * реплика обрезана нижней панелью, поля ввода не видно вовсе.
 *
 * Механизм — не z-index, а незаявленная переменная. `--tabbar-height` жила
 * только внутри `.master-dashboard`; `.ayla-screen` не лежит внутри него,
 * поэтому `calc(var(--tabbar-height) + …)` недействителен в момент
 * вычисления: `padding-bottom` сбрасывается в 0, у sticky-композера пропадает
 * `bottom`, и он уезжает под фиксированную панель. Браузер молчит, jsdom
 * раскладку не считает — поэтому сторож читает CSS с диска (как #1919).
 *
 * Класс: переменная без запасного значения, не заявленная на `:root`, — это
 * отступ/цвет, который работает только там, где случайно оказался предок.
 */
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const STYLES = resolve(__dirname);
const CSS = readdirSync(STYLES)
  .filter((f) => f.endsWith(".css"))
  .map((f) => readFileSync(resolve(STYLES, f), "utf-8"))
  .join("\n");
const AYLA_CHAT = readFileSync(
  resolve(__dirname, "../components/AylaChat.tsx"),
  "utf-8",
);

/** Незаявленные переменные, найденные этим сторожем 21.09, — вне рамок микро-PR.
 *  Красный в обе стороны: заявили переменную или убрали её употребление —
 *  строку отсюда снять. */
const KNOWN_UNDECLARED: Record<string, string> = {
  "--c-surface":
    "2 места; фон кнопок сейчас прозрачный — перекраска = решение по макету",
  "--leading-normal":
    "14 мест; межстрочный наследуется — замена меняет вёрстку",
};

function rootDeclared(css: string): Set<string> {
  const blocks = [...css.matchAll(/:root[^{]*\{([^}]*)\}/g)].map(
    (m) => m[1] ?? "",
  );
  return new Set(
    blocks.flatMap((b) =>
      [...b.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1] ?? ""),
    ),
  );
}

function usedWithoutFallback(css: string): Set<string> {
  return new Set(
    [...css.matchAll(/var\(\s*(--[\w-]+)\s*\)/g)].map((m) => m[1] ?? ""),
  );
}

function block(selector: string): string {
  return CSS.split(`${selector} {`)[1]?.split("}")[0] ?? "";
}

describe("переменная без запасного значения заявлена на :root", () => {
  const root = rootDeclared(CSS);
  const used = usedWithoutFallback(CSS);

  it("новых незаявленных нет", () => {
    const undeclared = [...used]
      .filter((v) => !root.has(v) && !(v in KNOWN_UNDECLARED))
      .sort();
    expect(
      undeclared,
      "var(--x) без :root — работает только под случайным предком",
    ).toEqual([]);
  });

  it("список известных не устарел", () => {
    for (const name of Object.keys(KNOWN_UNDECLARED)) {
      expect(
        used.has(name),
        `${name} больше не используется — снять из списка`,
      ).toBe(true);
      expect(
        root.has(name),
        `${name} заявлена на :root — снять из списка`,
      ).toBe(false);
    }
  });

  it("положительная пара: сторож видит заявленные токены", () => {
    expect(root.has("--safe-bottom")).toBe(true);
    expect(used.has("--safe-bottom")).toBe(true);
  });
});

describe("«Ayla»: композер и последняя реплика над нижней панелью", () => {
  it("высота панели — токен :root, ненулевой", () => {
    const m = /:root[^{]*\{[^}]*--tabbar-height:\s*(\d+)px/.exec(CSS);
    expect(m, "--tabbar-height не на :root").not.toBeNull();
    expect(Number(m?.[1])).toBeGreaterThan(0);
  });

  it("экран держит отступ под панель и safe-area", () => {
    const screen = block(".ayla-screen");
    expect(screen).toMatch(
      /padding-bottom:[^;]*var\(--tabbar-height\)[^;]*var\(--safe-bottom\)/,
    );
  });

  it("композер закреплён над панелью", () => {
    const compose = block(".ayla-compose");
    expect(compose).toContain("position: sticky");
    expect(compose).toMatch(
      /bottom:[^;]*var\(--tabbar-height\)[^;]*var\(--safe-bottom\)/,
    );
  });

  it("прокрутка к последней реплике останавливается над композером и панелью", () => {
    const end = block(".ayla-list__end");
    expect(end).toMatch(/scroll-margin-bottom:[\s\S]*var\(--tabbar-height\)/);
    expect(end).toContain("var(--ayla-compose-height)");
    expect(/:root[^{]*\{[^}]*--ayla-compose-height:\s*\d+px/.test(CSS)).toBe(
      true,
    );
    expect(AYLA_CHAT).toMatch(
      /ref=\{listEndRef\}\s+className="ayla-list__end"/,
    );
  });
});
