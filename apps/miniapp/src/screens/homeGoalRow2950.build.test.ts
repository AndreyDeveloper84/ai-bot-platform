/**
 * DRF-2950 — строка цели на Главной и подвал под ней (уточнение владельца
 * 12.11, вариант Б, `docs/OPEN_DECISIONS.md` §175).
 *
 * Строка цели — кнопка, растянутая псевдоэлементом `::after` с
 * `position: absolute`. Под строкой, в той же карточке, — подвал с
 * «Составить план» / «Мой план». Если растянутый слой отсчитывается от всей
 * карточки, он ложится поверх подвала, и нажатие на «Составить план»
 * уходит в строку цели — на экран цели вместо плана. jsdom раскладку не
 * считает (vitest идёт с `css: false`), поэтому узел читает CSS с диска,
 * как `quickActionLabels2266`: точка отсчёта слоя — обёртка строки
 * `.wellness-dash__goal-main`, а не карточка. То, что подвал стоит ВНЕ
 * обёртки, держит узел в `CustomerWellnessDashboardScreen.h01.test.tsx`.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "../styles/globals.css"), "utf-8");

function block(selector: string): string {
  return CSS.split(`\n${selector} {`)[1]?.split("}")[0] ?? "";
}

describe("растянутая кнопка строки цели не накрывает подвал с планом", () => {
  it("слой кнопки растянут абсолютно — положительная пара", () => {
    const after = block(".wellness-dash__goal-row::after");
    expect(after).toMatch(/position:\s*absolute/);
    expect(after).toMatch(/inset:/);
  });

  it("точка отсчёта слоя — обёртка строки, а не карточка", () => {
    expect(block(".wellness-dash__goal-main")).toMatch(/position:\s*relative/);
    const card = block(".wellness-dash__goal-card");
    expect(card).toMatch(/display:\s*flex/); // присутствие: правило карточки найдено
    expect(card).not.toMatch(/position:\s*(relative|absolute|fixed|sticky)/);
  });
});
