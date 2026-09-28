/**
 * DRF-2455 — миниатюра блюда в строке дневника (решение владельца 28.09, п.14).
 *
 * Геометрия снята с макета (экран 2 «Ближайшая запись» презентации клиента):
 * 116×68 pt, между снимком и текстом 16 pt. Миниатюра ДОБАВЛЯЕТСЯ в строку;
 * сама строка — принятый вид, и по п.10 («рефакторинг ≠ редизайн») её
 * объявления заморожены здесь дословно: строка без фото (записи старше
 * 30 суток) обязана остаться прежней пиксель в пиксель.
 *
 * jsdom раскладку не считает — правило читается с диска, как в соседях.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "globals.css"), "utf-8");

function block(selector: string): string {
  return CSS.split(`\n${selector} {`)[1]?.split("}")[0] ?? "";
}

function declarations(selector: string): string[] {
  return block(selector)
    .split(";")
    .map((d) => d.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

describe("миниатюра блюда в строке дневника", () => {
  it("положительная пара: файл стилей прочитан, строка дневника на месте", () => {
    expect(block(".food-scanner-diary__entry")).toMatch(/display:\s*flex/);
  });

  it("геометрия с макета: 116×68, зазор до текста 8 + 8 = 16", () => {
    const thumb = block(".food-scanner-diary__entry-thumb");
    expect(thumb).toMatch(/flex:\s*0 0 116px/);
    expect(thumb).toMatch(/width:\s*116px/);
    expect(thumb).toMatch(/height:\s*68px/);
    expect(thumb).toMatch(/margin-inline-end:\s*var\(--s-2\)/);
    expect(block(".food-scanner-diary__entry")).toMatch(/gap:\s*var\(--s-2\)/);
    expect(thumb).toMatch(/object-fit:\s*cover/);
  });
});

describe("строка без фото — прежняя пиксель в пиксель (п.10)", () => {
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
