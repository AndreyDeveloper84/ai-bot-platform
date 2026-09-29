/**
 * DRF-2609 — сторож против новых локальных помощников «дождаться» в тестах.
 *
 * До DRF-2609 восемнадцать файлов держали свои копии: 16× `settle` (N× `act`
 * без оборота очереди, раунды 4/5/6 «на глаз»), `flush` и `flushSweep`
 * (`act` + ОДИН оборот `setTimeout 0`). Замер: к гонке DRF-2596 они не
 * уязвимы (внутри `act` React сливает эффекты своей очередью), а настоящая
 * слабость — `act` без оборота очереди не видит работу на `setTimeout 0`,
 * а один оборот не видит вложенный таймер. Все 18 ждали событие, не время,
 * и сведены к `settleScenario`.
 *
 * Сканер ищет МЕХАНИЗМ, а не имя: локальная функция в файле теста, чьё тело
 * ждёт — `act` в цикле или с таймерным промисом, промис с таймером,
 * `advanceTimers*`, `waitFor` в цикле. Имя (`settle`, `flush`, …) не
 * признак: перепись по имени уже однажды пропустила две живые гонки.
 *
 * Исключение — только с причиной в строке. Помощник, которому нужна
 * настоящая задержка, живёт на фейковых таймерах узла, а не здесь.
 */
import { describe, expect, it } from "vitest";

/** Все файлы тестов Mini App как текст — приёмом дерева (`?raw`), без node:fs. */
const TEST_SOURCES = import.meta.glob("../**/*.test.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Разрешённые локальные помощники: `файл:имя` → причина. Пусто намеренно. */
const ALLOWED: Record<string, string> = {};

const DECL =
  /^[ \t]*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\([^)]*\)[^{]*\{|^[ \t]*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::[^=]+)?=>\s*\{/gm;

const MECH = {
  act: /\bact\s*\(/,
  loop: /\bfor\s*\(|\bwhile\s*\(/,
  timer: /setTimeout\s*\(|setImmediate\s*\(|queueMicrotask\s*\(/,
  promise: /new\s+Promise\b/,
  advance: /advanceTimers|runAllTimers|runOnlyPendingTimers/,
  waitFor: /\bwaitFor\s*\(/,
};

function bodyFrom(src: string, start: number): string {
  let depth = 1;
  let i = start;
  while (i < src.length && depth > 0) {
    if (src[i] === "{") depth += 1;
    else if (src[i] === "}") depth -= 1;
    i += 1;
  }
  return src.slice(start, i);
}

/** Локальные помощники ожидания в тексте файла — по механизму, не по имени. */
export function localWaitHelpers(source: string): string[] {
  const found: string[] = [];
  for (const m of source.matchAll(DECL)) {
    const name = m[1] ?? m[2]!;
    const body = bodyFrom(source, m.index! + m[0].length);
    const has = (k: keyof typeof MECH) => MECH[k].test(body);
    const acts = (body.match(/\bact\s*\(/g) ?? []).length;
    const waits =
      (has("act") && (has("loop") || has("timer") || has("promise") || acts >= 2)) ||
      (has("promise") && has("timer")) ||
      has("advance") ||
      (has("waitFor") && has("loop"));
    if (waits && !/\brender\s*\(/.test(body)) found.push(name);
  }
  return found;
}


describe("сторож локальных «дождаться» — положительный контроль сканера", () => {
  it("видит все три прежние формы и форму с advanceTimers", () => {
    const fixture = [
      "const settle = async (rounds = 4) => {",
      "  for (let i = 0; i < rounds; i += 1) {",
      "    await act(async () => {});",
      "  }",
      "};",
      "async function flush() {",
      "  await act(async () => {",
      "    await new Promise((r) => setTimeout(r, 0));",
      "  });",
      "}",
      "const tick = async () => {",
      "  await vi.advanceTimersByTimeAsync(10);",
      "};",
    ].join("\n");
    expect(localWaitHelpers(fixture)).toEqual(["settle", "flush", "tick"]);
  });

  it("не видит вызов общего помощника и обычные локальные функции", () => {
    const fixture = [
      "const renderScreen = async () => {",
      "  render(<Screen />);",
      "  await settleScenario();",
      "};",
      "function row(name: string) {",
      "  return screen.getByText(name).closest('li');",
      "}",
    ].join("\n");
    expect(localWaitHelpers("const x = 1;")).toEqual([]); // empty-assert-ok: пара к узлу выше
    expect(localWaitHelpers(fixture)).toEqual([]); // empty-assert-ok: пара к узлу выше
  });
});

describe("сторож локальных «дождаться» — дерево", () => {
  it("охватывает дерево тестов, а не пустоту", () => {
    expect(Object.keys(TEST_SOURCES).length).toBeGreaterThan(200);
  });

  it("в тестах Mini App нет локальных помощников ожидания вне списка исключений", () => {
    const offenders = Object.entries(TEST_SOURCES).flatMap(([file, source]) => {
      const rel = file.replace(/^\.\.\//, "");
      return localWaitHelpers(source)
        .map((name) => `${rel}:${name}`)
        .filter((key) => !(key in ALLOWED));
    });
    // Каждое имя — адрес: «файл:функция». Замените на `settleScenario`
    // (событие) или фейковые таймеры узла (время); исключение — с причиной.
    expect(offenders).toEqual([]); // empty-assert-ok: охват доказан узлом выше
  });
});
