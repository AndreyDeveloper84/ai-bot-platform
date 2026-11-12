/**
 * DRF-2954 — карточка записи дневника (доска владельца «Дневник», решение
 * 12.11 п.1). Замещает `diaryThumb2455.build.test.ts`: тот морозил вид строки
 * по решению 28.09 п.10 («рефакторинг ≠ редизайн»), и эту заморозку для
 * экранов дневника владелец снял решением 12.11 п.1.
 *
 * Что держится здесь — то, чего jsdom не видит (раскладку он не считает,
 * правила читаются с диска, как у соседей):
 *  * место под картинку занято ВСЕГДА и одной величины — карточка не
 *    прыгает, когда снимок догрузился;
 *  * снимок обрезается по месту (`object-fit: cover`), не растягивается;
 *  * длинное название блюда переносится, а не режется многоточием;
 *  * действия и форма граммов — на всю ширину карточки и переносятся (на
 *    320 px три кнопки не помещаются справа от картинки).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "globals.css"), "utf-8");
const PLAIN = CSS.replace(/\/\*[\s\S]*?\*\//g, "");

function block(selector: string): string {
  return PLAIN.split(`\n${selector} {`)[1]?.split("}")[0] ?? "";
}

describe("карточка записи дневника", () => {
  it("положительная пара: файл стилей прочитан, карточка на месте", () => {
    expect(block(".diary-entry")).toMatch(/display:\s*grid/);
  });

  it("левая колонка — ровно под картинку 56 px; правая сжимается, а не распирает", () => {
    expect(block(".diary-entry")).toMatch(/grid-template-columns:\s*56px minmax\(0, 1fr\)/);
    const tile = block(".cr-thumb--tile");
    expect(tile).toMatch(/width:\s*56px/);
    expect(tile).toMatch(/height:\s*56px/);
    expect(block(".cr-thumb__img")).toMatch(/object-fit:\s*cover/);
  });

  it("длинное название переносится, многоточия нет", () => {
    const dish = block(".diary-entry__dish");
    expect(dish).toMatch(/overflow-wrap:\s*anywhere/);
    expect(dish).not.toMatch(/text-overflow|white-space:\s*nowrap/);
    expect(block(".diary-entry__body")).toMatch(/min-width:\s*0/);
  });

  it("действия и форма граммов — на всю ширину карточки; действия переносятся", () => {
    const actions = block(".diary-entry__actions");
    expect(actions).toMatch(/grid-column:\s*1 \/ -1/);
    expect(actions).toMatch(/flex-wrap:\s*wrap/);
    expect(block(".diary-grams")).toMatch(/grid-column:\s*1 \/ -1/);
  });

  it("цель касания действий — не меньше 44 px (WCAG 2.5.8)", () => {
    expect(block(".diary-entry__action")).toMatch(/min-height:\s*44px/);
    expect(block(".diary-notice__action")).toMatch(/min-height:\s*44px/);
  });

  it("два входа переносятся на узком экране, а не сжимаются в нечитаемое", () => {
    expect(block(".diary-ways")).toMatch(/flex-wrap:\s*wrap/);
    expect(block(".diary-way")).toMatch(/flex:\s*1 1 140px/);
  });

  it("живая область уведомлений остаётся в дереве доступности", () => {
    expect(block(".diary-notice-slot")).not.toMatch(/display:\s*contents/);
  });
});
