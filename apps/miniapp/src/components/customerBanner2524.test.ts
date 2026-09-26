/**
 * Шапка каждого экрана клиентской панели — ориентир `banner` (DRF-2524).
 *
 * Лист считал «2 из 6»: `role="banner"` написан у Главной и Записей. Замер
 * настоящим движком (Chrome 153, дерево доступности через CDP) показал 6 из
 * 6: у Дневника, Плана, Профиля и заглушки `<header>` стоит прямым потомком
 * `div` экрана, и по HTML-AAM такой `<header>` — banner без атрибута.
 * Дефекта нет; есть хрупкость. У четырёх ориентир держится на неявной роли и
 * исчезнет, если шапку завернут в `main`/`section`/`article`/`aside`/`nav`
 * (калибровка на том же движке: в `section` и `main` — `sectionheader`).
 *
 * Почему правило проверяется здесь, а не через `getByRole("banner")`: jsdom
 * этого не различает. `dom-accessibility-api` мапит `header → banner`
 * безусловно, ограничение «scoped to the body element» знает только
 * `aria-query`, и его никто не применяет, — `<header>` внутри `<section>`
 * testing-library тоже назовёт баннером. Поэтому узел читает JSX экрана и
 * применяет правило сам, а в конце файла проверяет, что правило различает.
 *
 * Пределы, названные заранее:
 * - доказано поведение Chromium, а не объявление ориентира TalkBack или
 *   VoiceOver; iOS WKWebView не проверялся вовсе;
 * - смотрится JSX самого экрана: обёртка-компонент, рисующая `<section>`
 *   вокруг детей, здесь не видна (сегодня таких нет);
 * - `PilotComingSoonScreen` боевым кодом не монтируется нигде
 *   (`lib/feature-flags.ts`: «Кто гейтится сегодня: НИКТО») — зелень по нему
 *   ничего не говорит о продукте.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SOURCES = import.meta.glob(["../screens/**/*.tsx", "../components/**/*.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const baseName = (path: string) => path.split("/").pop() as string;
const isTest = (path: string) => path.includes(".test.");

/**
 * Экраны панели — списком имён, а не «все, кроме»: отрицательный признак
 * пропустил бы экран, с которого ориентир однажды снимут молча.
 */
const PANEL_SCREENS: Record<string, { h1InHeader: boolean }> = {
  // У Главной `<h1>` — приветствие в `<main>`, в шапке аватар и имя. Узел
  // `h1` не требует ни у кого; пометка — чтобы разница не считалась ошибкой.
  "CustomerWellnessDashboardScreen.tsx": { h1InHeader: false },
  "CustomerRecordsScreen.tsx": { h1InHeader: true },
  "FoodScannerDiaryScreen.tsx": { h1InHeader: true },
  "PlanLiteScreen.tsx": { h1InHeader: true },
  "CustomerProfileScreen.tsx": { h1InHeader: true },
  // Боевым кодом не монтируется — см. шапку файла.
  "PilotComingSoonScreen.tsx": { h1InHeader: true },
};

/** Предки, внутри которых `<header>` по HTML-AAM уже не banner. */
const SECTIONING = new Set(["main", "section", "article", "aside", "nav"]);

type HeaderVerdict = { line: number; banner: boolean; why: string };

function tagOf(node: ts.Node): string | null {
  if (ts.isJsxElement(node)) return node.openingElement.tagName.getText();
  if (ts.isJsxSelfClosingElement(node)) return node.tagName.getText();
  return null;
}

function attrs(node: ts.Node): ts.JsxAttributes | null {
  if (ts.isJsxElement(node)) return node.openingElement.attributes;
  if (ts.isJsxSelfClosingElement(node)) return node.attributes;
  return null;
}

function explicitRole(node: ts.Node): string | null {
  for (const prop of attrs(node)?.properties ?? []) {
    if (ts.isJsxAttribute(prop) && prop.name.getText() === "role") {
      const init = prop.initializer;
      return init && ts.isStringLiteral(init) ? init.text : "<выражение>";
    }
  }
  return null;
}

/** Каждый `<header>` в JSX исходника — и banner ли он по правилу HTML-AAM. */
function headerVerdicts(source: string): HeaderVerdict[] {
  const file = ts.createSourceFile("x.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: HeaderVerdict[] = [];
  const visit = (node: ts.Node) => {
    if (tagOf(node) === "header") {
      const line = file.getLineAndCharacterOfPosition(node.getStart()).line + 1;
      const role = explicitRole(node);
      let wrapper: string | null = null;
      for (let p = node.parent; p; p = p.parent) {
        const tag = tagOf(p);
        if (tag && SECTIONING.has(tag)) {
          wrapper = tag;
          break;
        }
      }
      if (role === "banner") out.push({ line, banner: true, why: 'явный role="banner"' });
      else if (role !== null) out.push({ line, banner: false, why: `role="${role}" перекрывает banner` });
      else if (wrapper) out.push({ line, banner: false, why: `внутри <${wrapper}> — sectionheader` });
      else out.push({ line, banner: true, why: "неявный: вне main/section/article/aside/nav" });
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return out;
}

const screens = Object.entries(SOURCES).filter(([path]) => !isTest(path));

describe("перепись: список экранов панели сверен с теми, кто монтирует <CustomerTabBar>", () => {
  it("седьмой экран с панелью обязан попасть в список — и ни один из списка не выпал", () => {
    const mounting = screens
      .filter(([, src]) => src.includes("<CustomerTabBar"))
      .map(([path]) => baseName(path))
      .sort();
    expect(mounting.length, "панель вообще кто-то монтирует").toBeGreaterThan(0);
    expect(mounting).toEqual(Object.keys(PANEL_SCREENS).sort());
  });
});

describe("шапка каждого экрана панели — ориентир banner", () => {
  it.each(Object.keys(PANEL_SCREENS))("%s", (name) => {
    const found = screens.filter(([path]) => baseName(path) === name);
    expect(found, `исходник ${name} не найден`).toHaveLength(1);
    const verdicts = headerVerdicts(found[0]![1]);
    expect(verdicts.length, `${name}: <header> в JSX экрана`).toBe(1);
    const v = verdicts[0]!;
    expect(v.banner, `${name}:${v.line} — шапка не ориентир: ${v.why}`).toBe(true);
  });
});

describe("правило различает: калибровка по тем же случаям, что в Chrome 153", () => {
  const one = (jsx: string) => headerVerdicts(`const X = () => (${jsx});`);

  it("header в div — banner (неявно)", () => {
    expect(one("<div><header><h1>t</h1></header><main /></div>")).toEqual([
      expect.objectContaining({ banner: true }),
    ]);
  });

  it.each(["section", "main", "article", "aside", "nav"])("header внутри <%s> — НЕ banner", (tag) => {
    expect(one(`<div><${tag}><header><h1>t</h1></header></${tag}></div>`)).toEqual([
      expect.objectContaining({ banner: false }),
    ]);
  });

  it('role="banner" внутри section — banner (явная роль переживает обёртку)', () => {
    expect(one('<section><header role="banner"><h1>t</h1></header></section>')).toEqual([
      expect.objectContaining({ banner: true }),
    ]);
  });

  it("чужая явная роль перекрывает неявную", () => {
    expect(one('<div><header role="presentation" /></div>')).toEqual([
      expect.objectContaining({ banner: false }),
    ]);
  });
});
