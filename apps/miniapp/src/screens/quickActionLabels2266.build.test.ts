/**
 * DRF-2266 — подписи быстрых действий Главной не налезают друг на друга.
 *
 * Скрин владельца 21.09: «Новая запись» и «Скорректировать план» вылезали из
 * плиток. Плитки ряда — `flex: 1 1 0; min-width: 56px`: пять в ряд на узком
 * экране — около 60–90 px, а «Скорректировать» — одно слово длиннее плитки.
 * Без правила переноса слово не рвётся и рисуется поверх соседа. jsdom
 * раскладку не считает — узел читает CSS с диска (как #1919 / #1947).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "../styles/globals.css"), "utf-8");

function block(selector: string): string {
  return CSS.split(`${selector} {`)[1]?.split("}")[0] ?? "";
}

describe("подпись плитки быстрого действия переносится внутри плитки", () => {
  it("положительная пара: плитка ряда правда узкая (min-width 56px)", () => {
    expect(block(".wellness-dash__qa-btn--compact")).toMatch(/min-width:\s*56px/);
  });

  it("длинное слово рвётся, а не вылезает к соседу", () => {
    const label = block(".wellness-dash__qa-btn--compact .wellness-dash__qa-label");
    expect(label).toMatch(/overflow-wrap:\s*anywhere/);
    expect(label).toMatch(/hyphens:\s*auto/);
    expect(label).toMatch(/max-width:\s*100%/);
  });
});
