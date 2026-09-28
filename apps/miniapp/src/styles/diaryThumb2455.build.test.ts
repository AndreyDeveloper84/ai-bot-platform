/**
 * DRF-2455 — миниатюра блюда в строке дневника (решение владельца 28.09, п.14).
 *
 * С макета (экран 2 «Ближайшая запись» презентации клиента) взяты пропорция
 * снимка 116:68 и зазор до текста 16; высота — по списку, 82×48 (решение
 * главного окна 28.09: «небольшая миниатюра… компактный список»).
 *
 * Строка дневника — принятый вид. По п.10 («рефакторинг ≠ редизайн») строка
 * БЕЗ снимка обязана остаться прежней пиксель в пиксель: её объявления
 * заморожены дословно, и ни одно новое правило не вправе попасть на неё —
 * раскладка со снимком живёт только под модификатором.
 *
 * jsdom раскладку не считает — правило читается с диска, как в соседях.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "globals.css"), "utf-8");
const PLAIN = CSS.replace(/\/\*[\s\S]*?\*\//g, "");

function block(selector: string): string {
  return PLAIN.split(`\n${selector} {`)[1]?.split("}")[0] ?? "";
}

function declarations(selector: string): string[] {
  return block(selector)
    .split(";")
    .map((d) => d.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

/** Все селекторы правил, которые попадают на саму строку `__entry`. */
function selectorsHittingTheRow(): string[] {
  const out: string[] = [];
  for (const m of PLAIN.matchAll(/([^{}]+)\{/g)) {
    for (const sel of (m[1] ?? "").split(",").map((s) => s.trim())) {
      // Последний составной селектор — тот, на кого правило ложится.
      const subject = sel.split(/\s*[>+~\s]\s*/).pop() ?? "";
      if (/\.food-scanner-diary__entry(?![\w-])/.test(subject)) out.push(sel);
    }
  }
  return out.sort();
}

describe("миниатюра блюда в строке дневника", () => {
  it("положительная пара: файл стилей прочитан, строка дневника на месте", () => {
    expect(block(".food-scanner-diary__entry")).toMatch(/display:\s*flex/);
  });

  it("снимок 82×48 — пропорция макета 116:68", () => {
    const thumb = block(".food-scanner-diary__entry-thumb");
    expect(thumb).toMatch(/width:\s*82px/);
    expect(thumb).toMatch(/height:\s*48px/);
    expect(thumb).toMatch(/object-fit:\s*cover/);
    expect(Math.abs(82 / 48 - 116 / 68)).toBeLessThan(0.01);
    // Вне модификатора снимок не вынимается из потока: иначе он привязался
    // бы к чужому предку.
    expect(thumb).not.toMatch(/position:/);
  });

  it("раскладка — только под модификатором: снимок слева, зазор 16, данные справа", () => {
    const row = block(".food-scanner-diary__entry--with-thumb");
    expect(row).toMatch(/position:\s*relative/);
    expect(row).toMatch(/padding-inline-start:\s*calc\(82px \+ var\(--s-4\)\)/);
    expect(row).toMatch(/min-height:\s*calc\(48px \+ 2 \* var\(--s-2\)\)/);
    const placed = block(
      ".food-scanner-diary__entry--with-thumb > .food-scanner-diary__entry-thumb",
    );
    expect(placed).toMatch(/position:\s*absolute/);
    expect(placed).toMatch(/inset-inline-start:\s*0/);
  });

  it("кнопки под модификатором переносятся и не шире места справа от снимка", () => {
    // Замер в Chrome: без этого блок «Граммы / В избранное / Убрать» (227 px)
    // вылезал за строку на 31 px (экран 360) и на 1 px (390); одного
    // переноса мало — блок с `flex: 0 0 auto` держит ширину содержимого.
    const actions = block(
      ".food-scanner-diary__entry--with-thumb .food-scanner-diary__entry-actions",
    );
    expect(actions).toMatch(/flex-wrap:\s*wrap/);
    expect(actions).toMatch(/max-width:\s*100%/);
  });
});

describe("строка без снимка — прежняя пиксель в пиксель (п.10)", () => {
  it("на строку без модификатора ложатся только два прежних правила", () => {
    const plain = selectorsHittingTheRow().filter((s) => !s.includes("--with-thumb"));
    expect(plain).toEqual([".food-scanner-diary__entry", ".food-scanner-diary__entry:last-child"]);
  });

  it("пара обязана различаться: у строки со снимком отступ под него есть, у прежней — нет", () => {
    expect(block(".food-scanner-diary__entry--with-thumb")).toMatch(/padding-inline-start/);
    expect(block(".food-scanner-diary__entry")).not.toMatch(/padding-inline-start|min-height|position/);
  });

  it("объявления строки дневника не изменились", () => {
    expect(declarations(".food-scanner-diary__entry")).toEqual([
      "display: flex",
      "flex-wrap: wrap",
      "align-items: center",
      "justify-content: space-between",
      "gap: var(--s-2)",
      "padding: var(--s-2) 0",
      "border-bottom: 1px solid var(--c-divider)",
    ]);
  });

  it("объявления основной части строки не изменились", () => {
    expect(declarations(".food-scanner-diary__entry-main")).toEqual([
      "display: flex",
      "align-items: baseline",
      "gap: var(--s-2)",
      "flex: 1 1 auto",
      "min-width: 0",
    ]);
  });
});
