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
 *
 * DRF-2468 (26.09) расширил охват. Прежний сторож читал только
 * `styles/*.css` и только `var(--x)` без запасного значения, поэтому не видел:
 *  - инлайн-стилей в `.tsx` (`style={{ fontSize: "var(--text-h3-size, …)" }}`);
 *  - имени, спрятанного за запасным значением: `var(--нет, 18px)` рисуется
 *    запасным, и опечатка в имени не видна никому;
 *  - нового употребления уже известного имени — список держал имя, а не счёт;
 *  - токена, заявленного только в тёмной ветке: в светлой его нет.
 * Теперь каждый `var(--…)` в `apps/miniapp/src` обязан разрешаться в
 * заявление на `:root` светлой ветки, а нарушение называет файл и строку.
 *
 * Пределы: файлы тестов не читаются (в них нарочно живут выдуманные имена);
 * имя, собранное во время исполнения (`var(--s-${n})`), не видно тексту;
 * переменная, заявленная инлайном на элементе (`style={{ "--x": … }}`),
 * считается незаявленной — ровно то, от чего предостерегает DRF-2150.
 */
import { readFileSync, readdirSync } from "node:fs";
import { relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const STYLES = resolve(__dirname);
const SRC = resolve(__dirname, "..");
const CSS = readdirSync(STYLES)
  .filter((f) => f.endsWith(".css"))
  .map((f) => readFileSync(resolve(STYLES, f), "utf-8"))
  .join("\n");
const AYLA_CHAT = readFileSync(
  resolve(__dirname, "../components/AylaChat.tsx"),
  "utf-8",
);

/** Незаявленные имена, известные поимённо и ПОСЧИТАННЫЕ. Счёт заморожен:
 *  новое употребление красное и названо строкой; заявили имя или убрали
 *  употребления — счёт не сойдётся, запись отсюда снять. */
const KNOWN_UNDECLARED: Record<string, { uses: number; why: string }> = {
  "--c-surface": {
    uses: 2,
    why:
      "globals.css .ayla-card__option и .ayla-btn--secondary, 14 элементов; " +
      "сегодня фон прозрачный, а --c-surface-1 перекрасил бы их поверх " +
      "--c-surface-2 карточки — решение владельца (DRF-2468, DRF-2469)",
  },
  "--leading-normal": {
    uses: 14,
    why: "межстрочный наследуется — замена меняет вёрстку (DRF-2150)",
  },
  "--text-body-size": {
    uses: 9,
    why: "за запасным значением; рисуется запасным (DRF-2468)",
  },
  "--text-h3-size": {
    uses: 7,
    why: "за запасным значением; рисуется запасным (DRF-2468)",
  },
  "--text-caption-size": {
    uses: 1,
    why: "за запасным значением; рисуется запасным (DRF-2468)",
  },
  "--fs-h2": {
    uses: 1,
    why: "за запасным значением; рисуется запасным (DRF-2468)",
  },
};

type Use = { name: string; where: string };

/** Комментарии гасятся с сохранением переводов строк — номера строк верны. */
function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, " "));
}

const DARK = /prefers-color-scheme:\s*dark|data-theme=["']dark["']/;

/** Заявления на `:root` по веткам: `light` — вне тёмного условия,
 *  `dark` — внутри `@media (prefers-color-scheme: dark)` или
 *  `[data-theme="dark"]`. Тёмная ветка наследует светлую, но не наоборот. */
function rootDeclarations(css: string): {
  light: Set<string>;
  dark: Set<string>;
} {
  const light = new Set<string>();
  const dark = new Set<string>();
  const stack: string[] = [];
  let buf = "";
  for (const ch of stripComments(css)) {
    if (ch === "{") {
      stack.push(buf.trim());
      buf = "";
    } else if (ch === "}") {
      stack.pop();
      buf = "";
    } else if (ch === ";") {
      const m = /^(--[\w-]+)\s*:/.exec(buf.trim());
      const selector = stack[stack.length - 1] ?? "";
      if (m?.[1] && selector.startsWith(":root")) {
        (stack.some((p) => DARK.test(p)) ? dark : light).add(m[1]);
      }
      buf = "";
    } else {
      buf += ch;
    }
  }
  return { light, dark };
}

/** Каждое `var(--имя` в тексте, с запасным значением или без.
 *  `(?![\w$-])` отсекает имя, достраиваемое шаблоном: `var(--s-${n})`. */
function varUses(text: string, file: string): Use[] {
  return [...text.matchAll(/var\(\s*(--[\w-]+)(?![\w$-])/g)].map((m) => ({
    name: m[1] ?? "",
    where: `${file}:${text.slice(0, m.index).split("\n").length}`,
  }));
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const path = resolve(dir, e.name);
    if (e.isDirectory()) return e.name === "__tests__" ? [] : sourceFiles(path);
    if (!/\.(css|tsx?)$/.test(e.name) || /\.test\.tsx?$/.test(e.name)) return [];
    return [path];
  });
}

const FILES = sourceFiles(SRC);
const USES = FILES.flatMap((path) => {
  const raw = readFileSync(path, "utf-8");
  const text = path.endsWith(".css") ? stripComments(raw) : raw;
  return varUses(text, relative(SRC, path).replace(/\\/g, "/"));
});
const ROOT = rootDeclarations(CSS);

/** Нарушения в виде «файл:строка var(--имя) — почему». */
function unresolved(
  uses: Use[],
  root: { light: Set<string>; dark: Set<string> },
  known: Record<string, unknown>,
): string[] {
  return uses
    .filter((u) => !root.light.has(u.name) && !(u.name in known))
    .map((u) =>
      root.dark.has(u.name)
        ? `${u.where} var(${u.name}) — заявлена только в тёмной ветке`
        : `${u.where} var(${u.name}) — не заявлена на :root`,
    );
}

describe("каждый var(--…) в apps/miniapp/src разрешается в :root", () => {
  it("положительная пара: охват непустой и токены видны", () => {
    // «0 нарушений» и «ничего не прочитано» иначе неразличимы.
    expect(FILES.length).toBeGreaterThan(150);
    expect(USES.length).toBeGreaterThan(3000);
    expect(FILES.some((f) => f.endsWith(".tsx"))).toBe(true);
    expect(ROOT.light.has("--safe-bottom")).toBe(true);
    expect(USES.some((u) => u.name === "--safe-bottom")).toBe(true);
    // Тёмная ветка распознана: поверхности переопределены в ней.
    expect(ROOT.dark.has("--c-surface-1")).toBe(true);
  });

  it("новых незаявленных нет", () => {
    expect(
      unresolved(USES, ROOT, KNOWN_UNDECLARED),
      "var(--x) без :root — работает только под случайным предком или никогда",
    ).toEqual([]);
  });

  it("известные посчитаны: не разрослись и не устарели", () => {
    for (const [name, { uses }] of Object.entries(KNOWN_UNDECLARED)) {
      const here = USES.filter((u) => u.name === name).map((u) => u.where);
      expect(
        here.length,
        `${name}: счёт заморожен на ${uses}, сейчас — ${here.join(", ")}`,
      ).toBe(uses);
      expect(
        ROOT.light.has(name) || ROOT.dark.has(name),
        `${name} заявлена на :root — снять из списка`,
      ).toBe(false);
    }
  });

  it("сторож умеет покраснеть: выдуманное имя, тёмная ветка, запасное значение", () => {
    const css = [
      ":root { --only-light: 1px; }",
      "@media (prefers-color-scheme: dark) { :root { --only-dark: 1px; } }",
      ':root[data-theme="dark"] { --themed-dark: 1px; }',
      ".a { margin: var(--only-light); }",
      ".b { color: var(--c-surface-nonexistent); }",
      ".c { color: var(--only-dark); }",
      ".d { color: var(--themed-dark); }",
      ".e { font-size: var(--typo-name, 16px); }",
      "/* var(--in-comment) */",
    ].join("\n");
    const root = rootDeclarations(css);
    expect(unresolved(varUses(stripComments(css), "x.css"), root, {})).toEqual([
      "x.css:5 var(--c-surface-nonexistent) — не заявлена на :root",
      "x.css:6 var(--only-dark) — заявлена только в тёмной ветке",
      "x.css:7 var(--themed-dark) — заявлена только в тёмной ветке",
      "x.css:8 var(--typo-name) — не заявлена на :root",
    ]);
    expect(varUses('style={{ gap: `var(--s-${n})` }}', "x.tsx")).toEqual([]);
  });
});

function block(selector: string): string {
  return CSS.split(`${selector} {`)[1]?.split("}")[0] ?? "";
}

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
