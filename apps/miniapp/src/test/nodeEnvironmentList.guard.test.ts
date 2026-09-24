/**
 * Список node-окружения — явный, конечный и проверяемый (DRF-2389).
 *
 * Полный прогон тратил на построение окружения 60 % рабочего времени
 * против 19 % на сами тесты (замер 24.09.2026: 1167 с против 332 с).
 * Файлы из `node-tests.ts` не касаются DOM вовсе; перевод их на
 * node-окружение **не меняет изоляции** — каждый файл по-прежнему
 * получает своё свежее окружение, просто дешёвое.
 *
 * ### Почему центральный список, а не построчный `@vitest-environment`
 *
 * У vitest есть встроенный приём — докблок `// @vitest-environment node`
 * в начале файла, и один такой в дереве уже живёт
 * (`lib/dev-init-data.build.test.ts`). Он проще, но рассыпает решение по
 * сорока семи файлам: ни посмотреть список целиком, ни потребовать
 * прогона перед пополнением. Здесь решение принято в пользу реестра —
 * как у долга линтера: видно, что внутри, и видно, что добавили.
 *
 * ### Что стережётся
 *
 * **1. Список соответствует дереву.** Переименовали тест — список скажет
 * об этом, а не перестанет тихо его покрывать.
 *
 * **2. Отсортирован, без повторов, только тесты.**
 *
 * **3. Тяжёлый `setup` остаётся тяжёлым у DOM-файлов.** Разделяя `setup`
 * на два, легко получить то, ради чего его никто не делил, — пустой для
 * всех. Узел требует, чтобы в DOM-варианте жили матчеры `jest-dom` и
 * `cleanup` из RTL: без `cleanup` экранные тесты начнут накапливать DOM
 * друг друга, и это будет не медленно, а неверно.
 *
 * ### Чего сторож НЕ проверяет
 *
 * Что каждый файл списка зелен под node. Это проверяется прогоном (при
 * заведении — 47/47, 455 тестов), а не чтением. Файл, попавший сюда
 * ошибочно, краснеет сразу: в node нет ни `document`, ни `window`.
 *
 * Читается дерево через `import.meta.glob`, как в
 * `no-person-names.guard.test.ts`: `node:fs` в этом пакете не типизирован
 * (`tsconfig.json` даже исключает `*.build.test.ts` из-за этого), а Vite
 * отдаёт и пути, и содержимое на сборке теста.
 */
import { describe, expect, it } from "vitest";

import { NODE_ENVIRONMENT_TESTS } from "./node-tests";

/** Все тестовые файлы дерева — ключи вида `../lib/foo.test.ts`. */
const ALL_TESTS = import.meta.glob("../**/*.test.{ts,tsx}");

/** Оба setup-файла текстом. */
const SETUPS = import.meta.glob("./setup*.ts", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Этот файл — в списке он есть, в выдаче glob'а его нет (см. ниже). */
const SELF = "src/test/nodeEnvironmentList.guard.test.ts";

/** `../lib/foo.test.ts` → `src/lib/foo.test.ts`, как записано в списке. */
function asListed(globKey: string): string {
  return globKey.replace(/^\.\.\//, "src/");
}

describe("Список node-окружения (DRF-2389)", () => {
  const tree = new Set(Object.keys(ALL_TESTS).map(asListed));

  it("непуст, и каждый путь есть в дереве", () => {
    // Присутствие первым: пустой список или пустое дерево сделали бы всё
    // остальное зелёным и бессмысленным.
    expect(NODE_ENVIRONMENT_TESTS.length).toBeGreaterThan(40);
    expect(tree.size).toBeGreaterThan(200);
    // Себя `import.meta.glob` не возвращает ПО ПОСТРОЕНИЮ — Vite исключает
    // вызывающий модуль. Значит этот файл в дереве есть всегда (мы в нём
    // и находимся), и сверять его с деревом нечем; зато проверяемо, что
    // он в списке — иначе сторож платил бы за jsdom, сторожа экономию.
    expect(NODE_ENVIRONMENT_TESTS).toContain(SELF);
    const missing = NODE_ENVIRONMENT_TESTS.filter(
      (rel) => rel !== SELF && !tree.has(rel),
    );
    expect(missing).toEqual([]);
  });

  it("отсортирован и без повторов", () => {
    const sorted = [...NODE_ENVIRONMENT_TESTS].sort((a, b) => a.localeCompare(b));
    expect(NODE_ENVIRONMENT_TESTS).toEqual(sorted);
    expect(new Set(NODE_ENVIRONMENT_TESTS).size).toBe(NODE_ENVIRONMENT_TESTS.length);
  });

  it("содержит только тесты", () => {
    const strays = NODE_ENVIRONMENT_TESTS.filter((rel) => !/\.test\.tsx?$/.test(rel));
    expect(strays).toEqual([]);
  });

  it("тяжёлый setup остаётся у DOM-файлов", () => {
    const dom = SETUPS["./setup.ts"];
    // Присутствие первым: файл найден и прочитан.
    expect(dom).toBeTruthy();
    expect(dom).toContain("@testing-library/jest-dom/vitest");
    expect(dom).toContain("cleanup");
  });

  it("лёгкий setup не тянет DOM обратно", () => {
    const node = SETUPS["./setup.node.ts"];
    expect(node).toBeTruthy();
    // Смысл разделения: в node-окружении матчеров DOM нет и быть не может.
    expect(node).not.toContain("@testing-library/jest-dom");
    expect(node).not.toContain("@testing-library/react");
  });
});
