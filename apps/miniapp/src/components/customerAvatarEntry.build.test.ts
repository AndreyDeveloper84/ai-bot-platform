/**
 * Цель нажатия у двери в профиль — 44×44 при кружке 36 (WCAG 2.5.8).
 *
 * Решение владельца §77 п.60 делает аватарку входом в профиль, и после
 * замены пункта панели — **единственным**. Маленькая цель нажатия у
 * единственной двери — это не косметика: человек с неточным касанием
 * теряет вход в свои данные.
 *
 * Почему сторож читает CSS С ДИСКА, а не измеряет и не импортирует:
 *   - jsdom раскладку не считает, измерять нечего;
 *   - при `css: false` (так настроен пакет) импортированная таблица
 *     приходит **пустой**, и утверждение о ней прошло бы вакуумно — об
 *     этом прямо сказано в докстринге `tools/lint/miniapp_style_contract.py`.
 *
 * Первая редакция этого сторожа жила в DOM-тесте и брала стиль через
 * `import.meta.glob(?raw)` — получила пустоту; поймала её проверка «правило
 * вообще найдено», которая осталась здесь именно поэтому. Затем `node:fs`
 * уронил `tsc`: в этом пакете типов node нет, и `tsconfig.json` исключает
 * `*.build.test.ts` ровно за этим. Отсюда имя файла и запись в
 * `src/test/node-tests.ts` — приём взят у `styles/cssVarsDeclared.build.test.ts`,
 * который читает ту же таблицу и по той же причине.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "../styles/globals.css"), "utf-8");

/**
 * Якорь — начало строки, а не подстрока: `.customer-avatar-entry {`
 * встречается и внутри правила-потомка
 * `.records-screen__header .customer-avatar-entry {`, и тогда какое из двух
 * правил проверяется, решал бы порядок строк в файле. Это удача, а не
 * конструкция, и она тихо сломалась бы при переносе правила.
 */
function ruleBody(selector: string): string {
  const at = CSS.indexOf(`\n${selector} {`);
  expect(at, `правила ${selector} на своей строке в globals.css нет`).toBeGreaterThan(-1);
  return CSS.slice(at, CSS.indexOf("}", at));
}

describe("дверь в профиль — цель нажатия 44 при кружке 36", () => {
  it("кнопка не меньше 44 по обеим сторонам", () => {
    const body = ruleBody(".customer-avatar-entry");
    expect(body).toContain("min-width: 44px");
    expect(body).toContain("min-height: 44px");
  });

  it("кружок остаётся 36 — увеличена цель, а не рисунок", () => {
    const body = ruleBody(".customer-avatar-entry__circle");
    expect(body).toContain("width: 36px");
    expect(body).toContain("height: 36px");
  });
});
