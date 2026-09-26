/**
 * DRF-2528 — содержимое `.sheet` лежит в `.sheet__panel`, у каждого носителя.
 *
 * `.sheet` — только затемнение: `position: fixed; inset: 0`, фон
 * `rgba(0,0,0,.45)`, `display: flex` **без** `flex-direction`, то есть ряд.
 * Колонку и светлую подложку даёт `.sheet__panel`. Три шторки дня салона
 * («Отмена», «Закрытие», «Перенос визита») писались 23.08, когда у `.sheet`
 * не было ни одного правила, и клали содержимое прямо в него. 25.09 класс
 * получил правило ради листа новой записи (DRF-2448, #2077) — и шторки дня
 * молча стали рядом узких столбцов у нижнего края на затемнении, с кнопками
 * за правой границей экрана (замерено в Chromium, см. PR DRF-2528).
 *
 * Общий класс одел одного носителя и сломал других. Этот сторож держит
 * контракт класса у ВСЕХ носителей, а не у одного экрана.
 *
 * Что считается нарушением: элемент с классом `sheet`, у которого первый
 * дочерний элемент (пробелы и JSX-комментарии пропускаются) — не
 * `.sheet__panel`.
 *
 * Чего сторож НЕ видит: класс, собранный выражением (`className={…}`), и
 * шторку, отрисованную другим компонентом внутри `.sheet`. Сегодня таких нет.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "..");

/** Открывающий тег с классом `sheet` (отдельным словом, не `avatar-sheet`). */
const SHEET_OPEN = /<([A-Za-z][\w.]*)\s[^>]*?className="(?:[^"]*\s)?sheet(?:\s[^"]*)?"[^>]*>/g;
const PANEL_OPEN = /^<[A-Za-z][\w.]*\s[^>]*?className="(?:[^"]*\s)?sheet__panel(?:\s[^"]*)?"/;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return name.endsWith(".tsx") && !name.includes(".test.") ? [path] : [];
  });
}

/** Для каждого `.sheet` в тексте: номер строки и есть ли панель первым ребёнком. */
function sheets(source: string): { line: number; hasPanel: boolean }[] {
  return [...source.matchAll(SHEET_OPEN)].map((m) => {
    const rest = source
      .slice((m.index ?? 0) + m[0].length)
      .replace(/^(?:\s|\{\/\*[\s\S]*?\*\/\})*/, "");
    return {
      line: source.slice(0, m.index).split("\n").length,
      hasPanel: PANEL_OPEN.test(rest),
    };
  });
}

describe("содержимое .sheet — на .sheet__panel", () => {
  it("у каждого носителя класса первым ребёнком стоит панель", () => {
    const found = sources(SRC).flatMap((path) =>
      sheets(readFileSync(path, "utf-8")).map((s) => ({
        where: `${relative(SRC, path).replaceAll("\\", "/")}:${s.line}`,
        hasPanel: s.hasPanel,
      })),
    );

    // Присутствие — первым и на тех же данных: обход обязан найти
    // носителей, среди них заведомо правильный лист новой записи. Иначе
    // «нарушений нет» прошло бы и при ослепшем обходе.
    expect(found.length).toBeGreaterThanOrEqual(4);
    expect(found.some((s) => s.where.startsWith("components/booking/NewBookingForm.tsx"))).toBe(
      true,
    );

    const withoutPanel = found.filter((s) => !s.hasPanel).map((s) => s.where);
    expect(withoutPanel).toEqual([]);
  });

  it("сторож различает панель и её отсутствие и называет место", () => {
    const good = `<div className="sheet" role="dialog">\n  {/* пояснение */}\n  <div className="sheet__panel">\n    <h3>…</h3>\n  </div>\n</div>`;
    expect(sheets(good)).toEqual([{ line: 1, hasPanel: true }]);

    const bare = `\n\n<div className="sheet" role="dialog" aria-label="Отмена">\n  <h3 className="section__title">…</h3>\n</div>`;
    expect(sheets(bare)).toEqual([{ line: 3, hasPanel: false }]);

    // Соседние семейства — не этот контракт: у них своя пара классов.
    const other = `<div className="avatar-sheet"><div className="schedule-sheet">x</div></div>`;
    expect(sheets(other)).toEqual([]);
  });
});
