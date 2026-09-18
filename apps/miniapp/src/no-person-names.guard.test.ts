/**
 * Сторож класса: в пользовательских строках Mini App нет имён людей (DRF-1961; расширение DRF-1814 C).
 *
 * Макет 6 и §3 карты разрывов: «имена операторов из UI снять». Имя оператора
 * («Карина») в копии — это имя одного человека одного салона, зашитое во все
 * салоны: мастеру второго салона бот предлагает «написать Карине», которой там
 * нет. Роль — «администратор салона», «мастер», «клиент» — есть у каждого,
 * поэтому копия называет роль. #1821 закрыл `screens/Master*.tsx`; этот файл
 * стережёт весь `src` — класс, а не сегодняшний список файлов.
 *
 * Что стережётся: каждый `src/**\/*.ts|tsx` (не тесты, не `.d.ts`); из файла
 * берутся только строковые литералы кода — `"…"`, `'…'` и шаблонные `` `…` ``;
 * комментарии (`//`, блочные) и `*.test.*` — вне охвата: спека цитирует макет
 * по имени, и это не UI. Совпадение — слово из словаря имён в любом падеже, с
 * заглавной, как отдельное слово. Заглушки dev-сборки (`?stub=`) — тоже в
 * охвате: исключение по файлу сделало бы сторожа класса сторожем списка.
 *
 * Словарь — закрытый и пополняемый: сторож не угадывает «имя ли это», он
 * знает конкретные имена, которые в макетах и спеке стоят как примеры
 * (Карина, Анна, Ольга, Ирина, Мария, Елена, Наталья, Светлана). Новое имя в
 * копии, которого тут нет, сторож не увидит — это предел, названный, а не
 * спрятанный; он закрывается добавлением имени в словарь, а не расширением
 * шаблона до «любое слово с заглавной».
 *
 * Пределы, названные: JSX-текст вне кавычек (`<p>Напишите Карине</p>`) сторож
 * не читает — сегодня таких строк в дереве нет (проверено grep по словарю при
 * заведении, 18.09.2026), и это второй класс, если появится.
 *
 * Проба на самого сторожа: вернуть «Карина» в любую строку любого файла →
 * красный (см. узел «подмена вне экранов мастера»).
 */
import { describe, expect, it } from "vitest";

// Исходники — через `import.meta.glob`, как в `backContract.test.ts`: `node:fs`
// в этом пакете не типизирован, а Vite отдаёт файлы строками на сборке теста.
const SOURCES = import.meta.glob("./**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Основы имён; окончания — любой русский падеж. Заглавная обязательна. */
const NAME_STEMS = ["Карин", "Анн", "Ольг", "Ирин", "Мари", "Елен", "Наталь", "Светлан"];
const NAME_RE = new RegExp(
  `(?<![А-Яа-яЁё])(?:${NAME_STEMS.join("|")})(?:а|е|у|ы|ой|ою|ей|ею|и|я|ю|ии|ье|ья|ью|ей)?(?![А-Яа-яЁё])`,
  "u",
);

/** Строковые литералы кода без комментариев — по одному на строку вывода. */
export function stringLiteralsOf(source: string): string[] {
  // `\r` снимается заранее: в JS `.` не матчит перевод строки, и строка
  // комментария с CRLF-хвостом пережила бы `^\s*\/\/.*$` (нашлось пробой).
  const noBlockComments = source.replace(/\r/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
  const literals: string[] = [];
  for (const rawLine of noBlockComments.split("\n")) {
    const line = rawLine.replace(/^\s*\/\/.*$/, "");
    for (const m of line.matchAll(/"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`/g)) {
      const text = m[1] ?? m[2] ?? m[3] ?? "";
      if (text.trim()) literals.push(text);
    }
  }
  return literals;
}

export function personNameHits(source: string): string[] {
  return stringLiteralsOf(source).filter((text) => NAME_RE.test(text));
}

/** [путь, исходник] для каждого файла кода; тесты, сторожа и .d.ts отброшены. */
const sourceFiles = (): Array<[string, string]> =>
  Object.entries(SOURCES)
    .filter(([p]) => !/\.test\.tsx?$/.test(p) && !p.endsWith(".d.ts"))
    .map(([p, src]): [string, string] => [p.replace(/^\.\//, ""), src])
    .sort(([a], [b]) => a.localeCompare(b));

describe("Mini App — без имён людей в строках UI (весь src)", () => {
  it("перепись не пуста и покрывает все три области: экраны мастера, админку, lib", () => {
    const files = sourceFiles().map(([p]) => p);
    // Нижняя граница — от сегодняшнего дерева (159 файлов на 18.09.2026):
    // glob, промахнувшийся мимо папки, отдал бы «0 нарушений» честно и пусто.
    expect(files.length).toBeGreaterThanOrEqual(150);
    expect(files).toContain("screens/MasterProfileScreen.tsx");
    expect(files).toContain("screens/admin/AddPersonNewMasterSection.tsx");
    expect(files).toContain("lib/customer-profile.ts");
    expect(files.some((p) => p.endsWith(".test.ts") || p.endsWith(".test.tsx"))).toBe(false);
  });

  it("словарь ловит каждый падеж и не ловит роль (положительная стража на самого сторожа)", () => {
    for (const bad of ["Написать Карине", "Карина указала", "Попросите Карину", "Анна Петрова", "у Карины"]) {
      expect(personNameHits(`const s = "${bad}";`), bad).toHaveLength(1);
    }
    for (const ok of [
      "Написать администратору салона",
      "администратор салона указал",
      "Имя и фамилия мастера",
      "Клиент",
      "Салон",
      "Каринка-тест",
    ]) {
      expect(personNameHits(`const s = "${ok}";`), ok).toHaveLength(0);
    }
    // Комментарии — вне охвата: спека цитирует макет по имени.
    expect(personNameHits(`// «Карина назначила»\n/* Анна */ const s = "роль";`)).toHaveLength(0);
  });

  it("подмена вне экранов мастера: «Карина» в строке admin-файла → красно", () => {
    // Тот же матчер на том же исходнике, что и в переписи ниже, плюс одна
    // подставленная строка: сторож обязан увидеть её вне `Master*.tsx`.
    const [file, source] = sourceFiles().find(([p]) => p === "screens/admin/AddPersonNewMasterSection.tsx") ?? [];
    expect(file).toBeDefined();
    const planted = `${source ?? ""}\nconst planted = "Напишите Карине";\n`;
    expect(personNameHits(planted)).toEqual(["Напишите Карине"]);
  });

  it("ни в одном файле src нет имени человека в строке кода", () => {
    const findings: string[] = [];
    for (const [file, source] of sourceFiles()) {
      for (const hit of personNameHits(source)) findings.push(`${file}: «${hit}»`);
    }
    expect(findings).toEqual([]);
  });
});
