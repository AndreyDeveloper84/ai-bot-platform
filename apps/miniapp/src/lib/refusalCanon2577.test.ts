/**
 * Сторож шести фраз отказа (§6-кси, решение владельца 28.09 п.1; DRF-2577).
 *
 * Краснеет, когда человеку показывается фраза одного из шести случаев,
 * не совпадающая с канонической:
 *
 * 1. `REFUSAL_CANON` равен словам владельца дословно — второй свидетель
 *    рядом с модулем, чтобы правка одной буквы в модуле не прошла молча.
 * 2. Вариант фразы (тот же корень, другие знаки или хвост) в любом
 *    исходнике, кроме `refusal-canon.ts`, — красный с `файл:строка`.
 *    Дом у шести фраз один, копия — будущее расхождение.
 * 3. Шесть мест, где эти случаи показываются, берут фразу из
 *    `REFUSAL_CANON`, а своих литералов «Не удалось сохранить…» и т.п. не
 *    держат: общая «Не удалось сохранить.» на экране профиля — это тоже
 *    не-каноническая фраза для случая «профиль», хотя корня «профиль» в ней нет.
 *
 * Что именно видит человек на каждом месте, держат узлы поведения в тестах
 * самих экранов (поиск по «DRF-2577»); этот файл держит словарь и его дом.
 */
import { describe, expect, it } from "vitest";

import { REFUSAL_CANON } from "./refusal-canon";

// Соседей по каталогу сборщик отдаёт как «./x.ts», а не «../lib/x.ts»
// (см. claims-debt.test.ts) — ключи приводятся к одному виду, иначе место
// в lib/ молча не находится.
const SOURCES = Object.fromEntries(
  Object.entries(
    import.meta.glob("../**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<
      string,
      string
    >,
  ).map(([path, text]) => [path.replace(/^\.\//, "../lib/"), text]),
);

/** Слова владельца — дословно из docs/OWNER_DECISIONS_2026-09-28.md, п.1. */
const OWNER_WORDS = [
  "Не удалось сохранить услугу.",
  "Не удалось создать запись.",
  "Это время уже занято. Выберите другое.",
  "Не удалось сохранить профиль.",
  "Это время пересекается с существующим расписанием. Выберите другое время.",
  "Не удалось перенести визит.",
];

/** Корни шести фраз: литерал с корнем и не равный канону — вариант. */
const STEMS = [
  /не удалось сохранить услуг/i,
  /не удалось создать запис/i,
  /это время уже занят/i,
  /не удалось сохранить профил/i,
  /пересекается с существующим расписани/i,
  /не удалось перенести визит/i,
];

/** Места шести случаев (адреса — PR #2064, раздел «e»). */
const SITES = [
  "components/OwnServiceForm.tsx",
  "screens/CustomerBookingConfirmScreen.tsx",
  "lib/booking-draft.ts",
  "screens/MasterProfileScreen.tsx",
  "screens/admin/AdminAvailabilityRequestsScreen.tsx",
  "screens/admin/AdminSalonDayScreen.tsx",
];

/** Своя фраза отказа того же рода на месте одного из шести случаев. */
const LOCAL_REFUSAL = /не удалось (сохранить|создать|перенести)|уже занят|пересека/i;

const LITERAL = /"([^"\\\n]*)"|'([^'\\\n]*)'|`([^`\\]*)`/g;

interface Hit {
  where: string;
  text: string;
}

function literals(path: string, source: string): Hit[] {
  const hits: Hit[] = [];
  source.split("\n").forEach((line, i) => {
    for (const m of line.matchAll(LITERAL)) {
      hits.push({ where: `${path}:${i + 1}`, text: m[1] ?? m[2] ?? m[3] ?? "" });
    }
  });
  return hits;
}

function productSources(): [string, string][] {
  return Object.entries(SOURCES).filter(
    ([path]) => !/\.test\.(ts|tsx)$/.test(path) && !path.includes("__fixtures__"),
  );
}

function variants(entries: [string, string][]): string[] {
  const canon = new Set<string>(OWNER_WORDS);
  return entries
    .filter(([path]) => !path.endsWith("/refusal-canon.ts"))
    .flatMap(([path, text]) => literals(path, text))
    .filter((h) => STEMS.some((s) => s.test(h.text)) && !canon.has(h.text))
    .map((h) => `${h.where} «${h.text}»`);
}

function sitePath(site: string): string | undefined {
  return Object.keys(SOURCES).find((p) => p.endsWith(`/${site}`));
}

describe("шесть фраз отказа — канон (§6-кси, DRF-2577)", () => {
  it("REFUSAL_CANON — слова владельца дословно, все шесть", () => {
    expect(Object.values(REFUSAL_CANON)).toEqual(OWNER_WORDS);
  });

  it("обход видит исходники (иначе ноль вариантов — не вердикт)", () => {
    expect(productSources().length).toBeGreaterThan(100);
    for (const site of SITES) expect(sitePath(site), site).toBeDefined();
  });

  it("положительный контроль: вариант фразы сторож называет с адресом", () => {
    const planted: [string, string][] = [
      ["../screens/Planted.tsx", 'const a = "ok";\nconst b = "Это время уже занято — выберите другое.";\n'],
    ];
    expect(variants(planted)).toEqual(["../screens/Planted.tsx:2 «Это время уже занято — выберите другое.»"]);
  });

  it("вариантов шести фраз вне refusal-canon.ts нет", () => {
    expect(variants(productSources())).toEqual([]);
  });

  it.each(SITES)("%s — фраза из REFUSAL_CANON, своей фразы отказа того же рода нет", (site) => {
    const path = sitePath(site) as string;
    const source = SOURCES[path] ?? "";
    // Сначала — своя фраза с адресом: подмена должна назвать строку, а не
    // только сказать «импорта нет».
    const own = literals(path, source)
      .filter((h) => LOCAL_REFUSAL.test(h.text))
      .map((h) => `${h.where} «${h.text}»`);
    expect(own).toEqual([]);
    expect(source.includes("REFUSAL_CANON."), `${site} не берёт фразу из REFUSAL_CANON`).toBe(true);
  });
});
