/**
 * Сторож класса: в пользовательских строках экранов мастера нет имён людей (DRF-1814, часть C).
 *
 * Макет 6 и §3 карты разрывов: «имена операторов из UI снять». Имя оператора
 * («Карина») в копии кабинета — это имя одного человека одного салона,
 * зашитое во все салоны: мастеру второго салона бот предлагает «написать
 * Карине», которой там нет. Роль — «администратор салона» — есть у каждого
 * (`TenantStaff.Role`), поэтому копия называет роль.
 *
 * Что стережётся: каждый `src/screens/Master*.tsx` (не тесты); из файла берутся
 * только строковые литералы кода — `"…"`, `'…'` и шаблонные `` `…` ``;
 * комментарии (`//`, блочные `/* … *​/`) и `*.test.tsx` — вне охвата: спека
 * цитирует макет по имени, и это не UI. Совпадение — слово из словаря имён в
 * любом падеже, с заглавной, как отдельное слово.
 *
 * Словарь — закрытый и пополняемый: сторож не угадывает «имя ли это», он
 * знает конкретные имена, которые в макетах и спеке стоят как примеры
 * (Карина, Анна, Ольга, Ирина, Мария, Елена, Наталья, Светлана). Новое имя в
 * копии, которого тут нет, сторож не увидит — это предел, названный, а не
 * спрятанный; он закрывается добавлением имени в словарь, а не расширением
 * шаблона до «любое слово с заглавной».
 *
 * Проба на самого сторожа: вернуть «Карина» в любую строку любого
 * `Master*.tsx` → красный (см. узел «словарь ловит каждый падеж» — он тот же
 * матчер на заведомо именной строке).
 */
import { describe, expect, it } from "vitest";

// Исходники — через `import.meta.glob`, как в `backContract.test.ts`: `node:fs`
// в этом пакете не типизирован, а Vite отдаёт файлы строками на сборке теста.
const MASTER_SOURCES = import.meta.glob("./Master*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Основы имён; окончания — любой русский падеж. Заглавная обязательна. */
const NAME_STEMS = ["Карин", "Анн", "Ольг", "Ирин", "Мари", "Елен", "Наталь", "Светлан"];
const NAME_RE = new RegExp(`(?<![А-Яа-яЁё])(?:${NAME_STEMS.join("|")})(?:а|е|у|ы|ой|ою|ей|ею|и|я|ю|ии|ье|ья|ью|ей)?(?![А-Яа-яЁё])`, "u");

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

/** [имя файла, исходник] для каждого экрана мастера; тесты отброшены. */
const masterScreens = (): Array<[string, string]> =>
  Object.entries(MASTER_SOURCES)
    .map(([p, src]): [string, string] => [p.split("/").pop() ?? p, src])
    .filter(([name]) => !name.endsWith(".test.tsx"))
    .sort(([a], [b]) => a.localeCompare(b));

describe("экраны мастера — без имён людей в строках UI", () => {
  it("перепись не пуста: экраны мастера найдены", () => {
    const files = masterScreens().map(([name]) => name);
    expect(files.length).toBeGreaterThanOrEqual(10);
    expect(files).toContain("MasterProfileScreen.tsx");
    expect(files).toContain("MasterOnboardingScreen.tsx");
    expect(files).toContain("MasterDashboardScreen.tsx");
  });

  it("словарь ловит каждый падеж и не ловит роль (положительная стража на самого сторожа)", () => {
    for (const bad of ["Написать Карине", "Карина указала", "Попросите Карину", "Анна Петрова", "у Карины"]) {
      expect(personNameHits(`const s = "${bad}";`), bad).toHaveLength(1);
    }
    for (const ok of ["Написать администратору салона", "администратор салона указал", "Салон", "Каринка-тест"]) {
      expect(personNameHits(`const s = "${ok}";`), ok).toHaveLength(0);
    }
    // Комментарии — вне охвата: спека цитирует макет по имени.
    expect(personNameHits(`// «Карина назначила»\n/* Анна */ const s = "роль";`)).toHaveLength(0);
  });

  it("ни в одном Master*.tsx нет имени человека в строке кода", () => {
    const findings: string[] = [];
    for (const [file, source] of masterScreens()) {
      for (const hit of personNameHits(source)) findings.push(`${file}: «${hit}»`);
    }
    expect(findings).toEqual([]);
  });
});
