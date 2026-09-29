/**
 * DRF-2465 — карточка выбора режима одета классом, а не инлайном.
 *
 * `SurfaceCard` в `App.tsx` — кнопка-карточка на посадочной странице
 * выбора режима («Салон» / «Мастер» / «Клиент»). Весь её вид был собран
 * инлайновым стилем: фон, рамка, скругление, высота. Класса не было
 * вовсе, поэтому сторож `miniapp_style_contract` её не видел, а вид
 * нельзя было ни переиспользовать, ни поправить в одном месте.
 *
 * ВИД ЗДЕСЬ НЕ ВЫБИРАЕТСЯ, А НАЗЫВАЕТСЯ. Объявления переносятся в
 * правило дословно, один в один, поэтому ни один пиксель не меняется.
 * Подобрать «похожее» из словаря было нельзя и не предлагается:
 * `.ayla-card__option` (`globals.css:9761`) — СТРОКА (`align-items:
 * center`, `min-height: 44px`, отступ `--s-2/--s-3`), а эта карточка —
 * СТОЛБЕЦ 88px с отступом `--s-4`. Разный вид, а не разное написание.
 *
 * jsdom раскладку не считает, поэтому правило читается с диска — приём
 * из `quickActionLabels2266.build.test.ts`.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "../styles/globals.css"), "utf-8");
const APP = readFileSync(resolve(__dirname, "../App.tsx"), "utf-8");

function block(selector: string): string {
  return CSS.split(`${selector} {`)[1]?.split("}")[0] ?? "";
}

/** Тело компонента `SurfaceCard` — от его объявления до следующего. */
function surfaceCardSource(): string {
  const start = APP.indexOf("function SurfaceCard(");
  expect(start, "компонент SurfaceCard не найден").toBeGreaterThan(-1);
  const rest = APP.slice(start + 1);
  const end = rest.indexOf("\nfunction ");
  return end === -1 ? rest : rest.slice(0, end);
}

describe("вид карточки выбора режима живёт в правиле", () => {
  it("положительная пара: файл стилей правда прочитан", () => {
    // Без неё любое утверждение ниже зеленело бы на пустой строке.
    expect(block(".screen")).toMatch(/padding/);
  });

  it("правило заведено", () => {
    expect(block(".surface-card")).not.toBe("");
  });

  it("перенесено дословно — все объявления, что были инлайном", () => {
    const rule = block(".surface-card");
    expect(rule).toMatch(/flex-direction:\s*column/);
    expect(rule).toMatch(/align-items:\s*flex-start/);
    expect(rule).toMatch(/gap:\s*var\(--s-1\)/);
    expect(rule).toMatch(/padding:\s*var\(--s-4\)/);
    expect(rule).toMatch(/min-height:\s*88px/);
    expect(rule).toMatch(/background:\s*var\(--c-surface-1\)/);
    expect(rule).toMatch(/border:\s*1px solid var\(--c-divider\)/);
    expect(rule).toMatch(/border-radius:\s*var\(--r-md\)/);
    expect(rule).toMatch(/text-align:\s*left/);
  });
});

describe("компонент больше не собирает себя инлайном", () => {
  it("положительная пара: компонент на месте и это кнопка", () => {
    expect(surfaceCardSource()).toMatch(/<button/);
  });

  it("кнопка названа классом словаря", () => {
    expect(surfaceCardSource()).toMatch(/className="surface-card"/);
  });

  it("у кнопки не осталось собственного style", () => {
    // Ищем `style={{` именно в открывающем теге кнопки, а не где-то
    // ниже: у подписей внутри карточки свои инлайны, и они не предмет
    // этого листа — их вид это текст, а не поверхность.
    const src = surfaceCardSource();
    const tag = src.slice(src.indexOf("<button"), src.indexOf(">", src.indexOf("aria-label")));
    expect(tag).not.toMatch(/style=\{\{/);
    // Утверждение о наличии раньше утверждения об отсутствии: иначе узел
    // зеленел бы на пустом срезе, то есть на пропавшей кнопке.
    expect(tag).toMatch(/type="button"/);
  });
});
