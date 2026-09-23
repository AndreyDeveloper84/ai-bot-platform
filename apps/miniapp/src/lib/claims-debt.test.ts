/**
 * Слепое пятно механизма утверждений — измеренное, а не названное (DRF-2347).
 *
 * Сторож держит число точным в обе стороны: новое утверждение мимо примитива
 * краснит (пятно растёт молча — этого и боимся), исправленное тоже (долг,
 * который больше не соответствует коду, врёт о размере).
 *
 * Исходники берутся через `import.meta.glob` сборщика, а не через файловую
 * систему: тот же обход, что у сборки, и никакой зависимости от узла.
 */

import { describe, expect, it } from "vitest";

import { CLAIM_WORDS, CLAIMS_DEBT_FILES, CLAIMS_OUTSIDE_PRIMITIVE_DEBT } from "./claims-debt";

const SOURCES = import.meta.glob("../**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;

const CLAIM_LINE = new RegExp(`"[^"\\n]*(${CLAIM_WORDS.join("|")})[^"\\n]*"`, "gi");

function claimStrings(): { total: number; files: string[] } {
  const files: string[] = [];
  let total = 0;
  for (const [path, text] of Object.entries(SOURCES)) {
    if (/\.test\.(ts|tsx)$/.test(path)) continue;
    if (path.includes("__fixtures__")) continue;
    const hits = text.match(CLAIM_LINE);
    if (!hits) continue;
    total += hits.length;
    files.push(path);
  }
  return { total, files };
}

describe("размер слепого пятна", () => {
  it("перепись видит исходники и утверждения в них", () => {
    const { total, files } = claimStrings();

    // Наличие прежде равенства: пустой обход сделал бы проверку ниже
    // вакуумной, и долг «ноль» читался бы как «чисто».
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100);
    expect(total).toBeGreaterThan(0);
    expect(files.length).toBeGreaterThan(0);
  });

  it("долг точен в обе стороны", () => {
    const { total, files } = claimStrings();

    expect({ строк: total, файлов: files.length }).toEqual({
      строк: CLAIMS_OUTSIDE_PRIMITIVE_DEBT,
      файлов: CLAIMS_DEBT_FILES,
    });
  });
});
