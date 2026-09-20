/**
 * Инцидент 20.09 (DRF-2150), п.3 — лист аватара поверх нижней панели.
 *
 * `.avatar-sheet` лежал под `.master-tabbar` (z-index 40 < 100): на iPhone
 * из трёх пунктов («Профиль», «Со студией», «Настройки») виден был один,
 * а «Настройки» — второй путь к выходу. jsdom стек не считает, поэтому
 * узел читает CSS с диска (как `dev-init-data.build.test.ts`).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "../styles/globals.css"), "utf-8");

function zIndexOf(selector: string): number {
  const block = CSS.split(`${selector} {`)[1]?.split("}")[0] ?? "";
  const m = /z-index:\s*(\d+)/.exec(block);
  return m ? Number(m[1]) : -1;
}

describe("3 · лист аватара — поверх нижней панели", () => {
  it("z-index листа выше z-index панели, низ — с safe-area", () => {
    const z = zIndexOf;
    const css = CSS;
    expect(z(".master-tabbar")).toBeGreaterThan(0);
    expect(z(".avatar-sheet")).toBeGreaterThan(z(".master-tabbar"));
    const panel = css.split(".avatar-sheet__panel {")[1]?.split("}")[0] ?? "";
    expect(panel).toContain("var(--safe-bottom)");
  });
});
