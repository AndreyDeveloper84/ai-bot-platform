/**
 * DRF-2469 — пара «опасное × обычное» различима не только тоном.
 *
 * Замер 25.09: рамки опасной и обычной контурных кнопок различались ТОЛЬКО
 * цветом, и контраст между ними — 1.09 : 1 (светлая), 1.19 : 1 (тёмная).
 * Человек, не различающий красный и синий, видел две одинаковые кнопки.
 * Решение владельца 28.09 (§6-упсилон): опасная остаётся контурной, но
 * получает БОЛЕЕ ВЫРАЖЕННУЮ рамку — второй признак, не заливка.
 *
 * Сторож мерит ПАРУ, которая обязана различаться, а не «текст к фону»
 * (та проверка проходила при неразличимых кнопках). Пара различима, если
 * рамки разной толщины ИЛИ цвета рамок контрастны не меньше 3 : 1 между
 * собой. Красный — только когда неразличимо по обоим признакам.
 *
 * jsdom стили не считает — правила и токены читаются с диска.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "globals.css"), "utf-8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);
const TOKENS = readFileSync(resolve(__dirname, "tokens.css"), "utf-8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

function block(selector: string): string {
  return CSS.split(`\n${selector} {`)[1]?.split("}")[0] ?? "";
}

/** Толщина и цвет рамки из правил в порядке каскада (позднее перекрывает). */
function border(...selectors: string[]): { width: number; color: string } {
  let width = 0;
  let color = "";
  for (const sel of selectors) {
    const b = block(sel);
    expect(b, `правило ${sel} не найдено`).not.toBe("");
    const short = /(?:^|;)\s*border:\s*(\d+)px\s+solid\s+([^;]+);/.exec(b);
    if (short) {
      width = Number(short[1]);
      color = (short[2] ?? "").trim();
    }
    const w = /border-width:\s*(\d+)px/.exec(b);
    if (w) width = Number(w[1]);
    const c = /border-color:\s*([^;]+);/.exec(b);
    if (c) color = (c[1] ?? "").trim();
  }
  return { width, color };
}

function tokenHex(theme: "light" | "dark", name: string): string {
  const [light, dark] = TOKENS.split("@media (prefers-color-scheme: dark)");
  const src = theme === "dark" ? `${dark ?? ""}\n${light ?? ""}` : (light ?? "");
  const m = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(src);
  expect(m, `${name} не найден в ${theme}`).not.toBeNull();
  return m?.[1] ?? "";
}

function luminance(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0);
}

function contrast(theme: "light" | "dark", a: string, b: string): number {
  const hex = (v: string) => tokenHex(theme, /var\((--[\w-]+)\)/.exec(v)?.[1] ?? v);
  const [x, y] = [luminance(hex(a)), luminance(hex(b))].sort((p, q) => q - p);
  return ((x ?? 0) + 0.05) / ((y ?? 0) + 0.05);
}

/** Пары «опасное × обычное» — каскад каждой стороны целиком. */
const PAIRS: Array<{ name: string; danger: string[]; ordinary: string[] }> = [
  {
    name: "ayla: «Отменить визит» × «Не отменять» (AdminSalonDayScreen)",
    danger: [".ayla-btn", ".ayla-btn--danger"],
    ordinary: [".ayla-btn", ".ayla-btn--secondary"],
  },
  {
    name: "карточка записи: «Отменить» × соседние действия (BookingCard)",
    danger: [".btn-secondary", ".records-card__action--danger"],
    ordinary: [".btn-secondary"],
  },
  {
    name: "модификатор .btn-secondary--danger (носителей 0)",
    danger: [".btn-secondary", ".btn-secondary--danger"],
    ordinary: [".btn-secondary"],
  },
];

function distinguishable(pair: (typeof PAIRS)[number], theme: "light" | "dark") {
  const d = border(...pair.danger);
  const o = border(...pair.ordinary);
  return { d, o, byWidth: d.width !== o.width, byColor: contrast(theme, d.color, o.color) >= 3 };
}

describe("опасное различимо с обычным не только тоном (§6-упсилон)", () => {
  it("калибровка: прибор читает токены обеих тем и различает контраст", () => {
    // Без неё «контраст мал» мог бы оказаться ошибкой разбора токенов, а
    // «контраст велик» — чтением не той темы. Значения токенов здесь не
    // прибиты: смена палитры не должна краснеть этим узлом.
    for (const theme of ["light", "dark"] as const) {
      expect(contrast(theme, "var(--c-accent)", "var(--c-accent)")).toBeCloseTo(1, 5);
      expect(contrast(theme, "var(--c-text-primary)", "var(--c-surface-1)")).toBeGreaterThan(4.5);
    }
    expect(tokenHex("dark", "--c-danger")).not.toBe(tokenHex("light", "--c-danger"));
  });

  for (const pair of PAIRS) {
    for (const theme of ["light", "dark"] as const) {
      it(`${pair.name} — ${theme}`, () => {
        const r = distinguishable(pair, theme);
        expect(
          r.byWidth || r.byColor,
          `рамки ${r.d.width}px ${r.d.color} × ${r.o.width}px ${r.o.color}: одна толщина и цвета < 3:1`,
        ).toBe(true);
      });
    }
  }

  it("второй признак — толщина: опасная рамка толще обычной во всех парах", () => {
    for (const pair of PAIRS) {
      expect(border(...pair.danger).width, pair.name).toBeGreaterThan(
        border(...pair.ordinary).width,
      );
    }
  });

  it("заливки у опасной ayla-кнопки нет — контурная, как решил владелец", () => {
    expect(block(".ayla-btn--danger")).toMatch(/background:\s*transparent/);
  });
});
