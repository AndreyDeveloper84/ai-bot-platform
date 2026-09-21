/**
 * Сторож класса: фикстура расписания идёт за часами устройства, а не за
 * календарём автора теста.
 *
 * Экраны «Сегодня» и «Расписание» выбирают день по `new Date()` устройства.
 * Тест, где день ответа сервера записан литералом («2026-09-20»), зелёный
 * ровно до ближайшей полуночи в часовом поясе раннера — после неё день
 * ответа и день экрана расходятся, а падает совсем другое утверждение
 * («кнопка окна не найдена»), и время уходит на поиск не там.
 *
 * Это уже третий случай за сутки: #1892 (метки со смещением +03:00 против
 * UTC-раннера), DRF-2194 (линия «сейчас») и MasterEntryPoints2155 (день
 * фикстуры против «сегодня» экрана, красный на dev 21.09).
 *
 * Правило: если мастерский тест мокает `getMasterSchedule`, дата дня в его
 * фикстуре берётся из `formatYmdLocal(new Date())` / `addDays(...)`, а не
 * литералом. Литерал допустим, если тест сам замораживает часы
 * (`setSystemTime`) или строка помечена `clock-intentional` с причиной —
 * как в дымовом тесте, где тенантская дата НАМЕРЕННО не равна дате
 * устройства.
 */
import { describe, expect, it } from "vitest";

/**
 * Только мастерская поверхность: там экран сам выбирает «сегодня»
 * (`anchor = new Date()`), поэтому день фикстуры обязан совпасть. Салонный
 * пилот днём управляет сам (свой выбор даты) — проверено в обоих поясах,
 * его литералы безопасны.
 */
const TEST_SOURCES = {
  ...(import.meta.glob("./Master*.test.tsx", { query: "?raw", import: "default", eager: true }) as Record<
    string,
    string
  >),
  ...(import.meta.glob("../App.*.test.tsx", { query: "?raw", import: "default", eager: true }) as Record<
    string,
    string
  >),
};

const ISO_DATE = /"\d{4}-\d{2}-\d{2}"/;

function strip(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

describe("фикстуры расписания следуют часам устройства", () => {
  it("тесты найдены", () => {
    expect(Object.keys(TEST_SOURCES).length).toBeGreaterThan(5);
  });

  it("день фикстуры расписания не записан литералом (или часы заморожены)", () => {
    const offenders: string[] = [];
    for (const [path, raw] of Object.entries(TEST_SOURCES)) {
      const src = strip(raw);
      if (!src.includes("getMasterSchedule")) continue;
      // Тест сам держит часы — литерал безопасен.
      if (src.includes("setSystemTime") || src.includes("useFakeTimers")) continue;
      // Опасен ровно день строки расписания: он обязан совпасть с «сегодня»
      // экрана. Диапазон `from`/`to`, `week_summary` и пустой `days: []`
      // безопасны — с днём экрана они не сравниваются.
      //
      // Две формы: `date: "2026-09-20"` и `const TODAY = "2026-09-20"` с
      // последующим `date: TODAY` — вторая и сломала MasterEntryPoints2155.
      const litConsts = new Set<string>();
      for (const m of src.matchAll(/const\s+([A-Z_a-z][\w$]*)\s*(?::\s*\w+\s*)?=\s*"(\d{4}-\d{2}-\d{2})"/g)) {
        if (m[1]) litConsts.add(m[1]);
      }
      for (const line of src.split("\n")) {
        const m = /^\s*date:\s*(.+?),?\s*$/.exec(line);
        if (!m) continue;
        const value = (m[1] ?? "").trim();
        const literal = ISO_DATE.test(value);
        const viaConst = litConsts.has(value);
        if (!literal && !viaConst) continue;
        // Строчный комментарий в конце строки `strip` не трогает: пометка
        // `clock-intentional` с причиной остаётся видимой.
        if (line.includes("clock-intentional")) continue;
        const how = viaConst ? ` (через константу ${value})` : "";
        offenders.push(`${path.replace(/^.*\//, "")}: ${line.trim()}${how}`);
      }
    }
    expect(
      offenders,
      "день фикстуры записан литералом — возьми formatYmdLocal(new Date()) или заморозь часы",
    ).toEqual([]);
  });
});
