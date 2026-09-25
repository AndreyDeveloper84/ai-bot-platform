/**
 * DRF-2464 — у словаря кнопок появляются опасный и компактный виды.
 *
 * После DRF-2448 на экране дня салона семь элементов `<button>` из
 * четырнадцати рисуются обычным текстом: администратор не видит их как
 * кнопки. Причина названа там же — в семье `ayla-btn` нет ни опасного
 * вида, ни компактного, и каждый экран выдумывал размер инлайном.
 *
 * Решения владельца (канон §77 п.51): опасное действие — ОБВОДКОЙ, не
 * заливкой (в строке рядом стоят ещё две кнопки, заливка перетянула бы
 * глаз на самое редкое действие); компактный вид — ЗАВЕСТИ модификатор,
 * а не снимать инлайн (без него три кнопки строки визита станут по 44px,
 * и день салона на телефоне вырастет вдвое).
 *
 * Кнопки отказа одеваются СУЩЕСТВУЮЩИМ `.ayla-btn--secondary`
 * (`globals.css:9737`, заведён в 2ad7cb3c) — там ничего заводить не надо.
 *
 * jsdom раскладку не считает, поэтому вид проверяется чтением CSS с диска
 * — тот же приём, что в `quickActionLabels2266.build.test.ts`.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "../styles/globals.css"), "utf-8");
const SALON_DAY = readFileSync(
  resolve(__dirname, "./admin/AdminSalonDayScreen.tsx"),
  "utf-8",
);

function block(selector: string): string {
  return CSS.split(`${selector} {`)[1]?.split("}")[0] ?? "";
}

/** Строки тега `<button …>` вместе с их содержимым до закрывающего тега. */
function buttons(source: string): string[] {
  return source.split("<button").slice(1).map((chunk) => chunk.split("</button>")[0]);
}

describe("опасный вид — обводка, а не заливка", () => {
  it("положительная пара: обычный вид кнопки правда существует", () => {
    // Иначе следующие утверждения проверяли бы пустую строку и зеленели
    // на отсутствии файла, а не на наличии правила.
    expect(block(".ayla-btn")).toMatch(/min-height:\s*44px/);
  });

  it("правило заведено", () => {
    expect(block(".ayla-btn--danger")).not.toBe("");
  });

  it("рисуется рамкой и цветом текста", () => {
    const rule = block(".ayla-btn--danger");
    expect(rule).toMatch(/border:\s*1px solid/);
    expect(rule).toMatch(/color:/);
  });

  it("не заливается: фона у опасного вида нет", () => {
    // Решение владельца именно о весе. Заливка вернула бы вес, ради
    // снятия которого вид и выбран.
    const rule = block(".ayla-btn--danger");
    // Утверждение о наличии раньше утверждения об отсутствии: без него
    // узел зеленел бы на ПУСТОМ правиле, то есть на отсутствии вида.
    expect(rule).toMatch(/border:/);
    expect(rule).not.toMatch(/background:\s*var\(--c-(warning|danger|accent)/);
  });
});

describe("компактный вид — модификатор, а не выдумка на каждом экране", () => {
  it("правило заведено", () => {
    expect(block(".ayla-btn--compact")).not.toBe("");
  });

  it("перебивает и отступ, и кегль базы", () => {
    const rule = block(".ayla-btn--compact");
    expect(rule).toMatch(/padding:/);
    expect(rule).toMatch(/font-size:/);
  });

  it("кегль берётся токеном, а не сырым числом", () => {
    // `0.85em` на трёх кнопках строки визита — ровно то, что модификатор
    // и заменяет; повторить сырое число значит перенести долг, а не снять.
    expect(block(".ayla-btn--compact")).toMatch(/font-size:\s*var\(--font-size-/);
  });
});

describe("экран дня салона больше не выдумывает размер инлайном", () => {
  it("положительная пара: кнопки на экране вообще есть", () => {
    expect(buttons(SALON_DAY).length).toBeGreaterThan(10);
  });

  it("ни одна кнопка не задаёт себе padding или fontSize", () => {
    const guilty = buttons(SALON_DAY).filter((b) =>
      /style=\{\{[^}]*\b(padding|fontSize)\b/.test(b),
    );
    expect(guilty).toEqual([]);
  });
});

describe("кнопки экрана дня салона написаны живым словарём", () => {
  it("мёртвых имён не осталось ни одного", () => {
    const dead = buttons(SALON_DAY).filter((b) =>
      /className="[^"]*(^|\s)(btn|btn--danger|btn--ghost|btn--primary)(\s|")/.test(b),
    );
    expect(dead).toEqual([]);
  });

  it("«Отменить визит» одет опасным видом", () => {
    const cancel = buttons(SALON_DAY).find((b) => b.includes("Отменить визит"));
    expect(cancel).toBeDefined();
    expect(cancel).toMatch(/className="[^"]*ayla-btn--danger/);
  });

  it("кнопки отказа одеты существующим второстепенным видом", () => {
    for (const label of ["Не отменять", "Не сейчас", "Не переносить"]) {
      const b = buttons(SALON_DAY).find((chunk) => chunk.includes(label));
      expect(b, label).toBeDefined();
      expect(b, label).toMatch(/className="[^"]*ayla-btn--secondary/);
    }
  });

  it("«Не пришёл» — самостоятельное действие, и оно тоже видно", () => {
    // Не отказ, а третий исход диалога (onNoShow, DRF-1851). В DRF-2448
    // оно пряталось под словом «отказы» и поэтому едва не осталось текстом.
    const b = buttons(SALON_DAY).find((chunk) => chunk.includes("Не пришёл"));
    expect(b).toBeDefined();
    expect(b).toMatch(/className="[^"]*ayla-btn--/);
  });
});
