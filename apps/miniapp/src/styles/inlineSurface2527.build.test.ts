/**
 * DRF-2527 — элемент, собранный инлайном ЦЕЛИКОМ: класса нет вовсе, а
 * `style` несёт вид поверхности. Такой элемент не видит ни
 * `miniapp_style_contract` (он спрашивает про имена классов), ни словарь:
 * вид нельзя ни переиспользовать, ни поправить в одном месте.
 *
 * Перепись 26.09 (DRF-2465 п.3): 132 не-тестовых `.tsx`, 3967 открывающих
 * тегов, 7 кандидатов. Шесть — неодетые элементы; седьмой (`fieldset` в
 * `AdminSalonDayScreen`) — сброс `border: 0`, а не вид. Ни одному из шести
 * точного вида в словаре нет — вопрос владельца (§6-фи), поэтому они здесь
 * заморожены ПОФАЙЛОВО СО СЧЁТОМ: одели элемент — счёт уменьшится ровно на
 * него, запись поправить; появился седьмой — красный, с названной строкой.
 *
 * Критерий — тот же, что у переписи, чтобы числа сходились:
 * встроенный тег (строчная буква), `className` нет, в `style` есть
 * `background` / `backgroundColor` / `border` / `borderRadius` / `boxShadow`.
 *
 * Пределы (долг, а не оговорка):
 *  - вид только текстом (цвет, размер шрифта) критерий не ловит;
 *  - `style={переменная}` не ловится: объект вычисляется, текст слеп;
 *  - элемент С классом, у которого правила нет, а вид весь инлайном
 *    (`className="snackbar"`), здесь не виден — это вопрос к стилевому
 *    контракту, а не к этому узлу;
 *  - вычисляемый класс (`className={cx(…)}`, шаблон) считается «классом
 *    есть» — разрешается ли он в правило, этот узел не проверяет;
 *  - шире критерия ~445 элементов со `style` без класса — в основном
 *    раскладка и текст.
 */
import { readFileSync, readdirSync } from "node:fs";
import { relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "..");

const SURFACE = /\b(background|backgroundColor|border|borderRadius|boxShadow)\s*:/;

/** Известные носители по файлам. Счёт — точный. */
const KNOWN: Record<string, { count: number; what: string }> = {
  "App.tsx": {
    count: 1,
    what: "SurfaceCard — карточка выбора режима (DRF-2465, §6-фи)",
  },
  "components/Snackbar.tsx": {
    count: 1,
    what: "кнопка действия снекбара (DRF-2527)",
  },
  "screens/admin/AdminServicesMatrixScreen.tsx": {
    count: 3,
    what: "точка «несохранено», панель услуг мастера, полоса сохранения (DRF-2527)",
  },
  "screens/MasterSettingsScreen.tsx": {
    count: 1,
    what: "разделитель hr (DRF-2527)",
  },
  "screens/admin/AdminSalonDayScreen.tsx": {
    count: 1,
    what: "fieldset со сбросом border: 0 — не вид, а сброс",
  },
};

type Hit = { file: string; line: number; tag: string };

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const path = resolve(dir, e.name);
    if (e.isDirectory()) return e.name === "__tests__" ? [] : sourceFiles(path);
    return e.name.endsWith(".tsx") && !/\.test\.tsx$/.test(e.name) ? [path] : [];
  });
}

/** Открывающие теги встроенных элементов: от `<tag` до `>` вне скобок и строк. */
function openingTags(src: string): { tag: string; text: string; line: number }[] {
  const out: { tag: string; text: string; line: number }[] = [];
  for (const m of src.matchAll(/<([a-z][a-zA-Z0-9]*)\b/g)) {
    const start = m.index ?? 0;
    let i = start + m[0].length;
    let depth = 0;
    let quote = "";
    for (; i < src.length; i++) {
      const c = src[i] ?? "";
      if (quote) {
        if (c === quote) quote = "";
      } else if (c === '"' || c === "'" || c === "`") {
        quote = c;
      } else if (c === "{") {
        depth++;
      } else if (c === "}") {
        depth--;
      } else if (c === ">" && depth === 0) {
        break;
      }
    }
    out.push({
      tag: m[1] ?? "",
      text: src.slice(start, i + 1),
      line: src.slice(0, start).split("\n").length,
    });
  }
  return out;
}

function inlineSurfaces(src: string, file: string): Hit[] {
  return openingTags(src)
    .filter((t) => /\bstyle=\{/.test(t.text) && !/\bclassName=/.test(t.text))
    .filter((t) => SURFACE.test(t.text.slice(t.text.indexOf("style={"))))
    .map((t) => ({ file, line: t.line, tag: t.tag }));
}

const FILES = sourceFiles(SRC);
let TAGS = 0;
const HITS = FILES.flatMap((path) => {
  const src = readFileSync(path, "utf-8");
  TAGS += openingTags(src).length;
  return inlineSurfaces(src, relative(SRC, path).replace(/\\/g, "/"));
});

describe("элементов без класса с видом поверхности не прибавляется", () => {
  it("положительная пара: охват непустой", () => {
    // «0 новых» и «ничего не прочитано» иначе неразличимы. Перепись 26.09:
    // 132 файла, 3967 тегов.
    expect(FILES.length, `файлов: ${FILES.length}`).toBeGreaterThan(120);
    expect(TAGS, `тегов: ${TAGS}`).toBeGreaterThan(3500);
  });

  it("счёт по файлам совпадает с известным — ни больше, ни меньше", () => {
    const byFile = new Map<string, Hit[]>();
    for (const h of HITS) byFile.set(h.file, [...(byFile.get(h.file) ?? []), h]);
    const problems: string[] = [];
    for (const [file, hits] of byFile) {
      const known = KNOWN[file]?.count ?? 0;
      if (hits.length !== known) {
        problems.push(
          `${file}: ждали ${known}, нашли ${hits.length} — ` +
            hits.map((h) => `${file}:${h.line} <${h.tag}>`).join(", "),
        );
      }
    }
    for (const [file, { count }] of Object.entries(KNOWN)) {
      if (!byFile.has(file)) {
        problems.push(`${file}: ждали ${count}, нашли 0 — одет? снять запись`);
      }
    }
    expect(problems, "неодетый элемент прибавился или одет без правки списка").toEqual([]);
    expect(HITS.length).toBe(7);
  });

  it("узел умеет покраснеть: ловит неодетый и не ловит одетый", () => {
    const src = [
      '<div style={{ background: "var(--c-surface-1)", padding: 8 }} />',
      '<div className="x" style={{ background: "red" }} />',
      "<span style={{ flex: 1 }} />",
      '<button type="button" style={{ border: "none", color: "var(--c-accent)" }}>',
      "<Foo style={{ background: 1 }} />",
    ].join("\n");
    expect(inlineSurfaces(src, "x.tsx")).toEqual([
      { file: "x.tsx", line: 1, tag: "div" },
      { file: "x.tsx", line: 4, tag: "button" },
    ]);
  });
});
