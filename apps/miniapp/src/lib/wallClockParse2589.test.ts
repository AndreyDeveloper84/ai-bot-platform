/**
 * Сторож «часы из строки» (DRF-2589).
 *
 * Корень DRF-2589 — не UTC на проводе сам по себе, а то, что экраны берут
 * ЧАСЫ ПРЯМО ИЗ СТРОКИ ISO (регуляркой или срезом), отбрасывая смещение.
 * Провод отдавал `06:00+00:00` при визите в 09:00 по салону — человек видел
 * «в 06:00». Сервер теперь отдаёт время визита в поясе салона записи, и
 * разбор строки в `lib/format.ts` на это опирается.
 *
 * Правило: разбирать часы из строки ISO можно только в `lib/format.ts` — это
 * дом, у которого названа опора (пояс задаёт сервер). Новое место с таким
 * разбором — красное с `файл:строка`: следующее поле приедет с той же бедой,
 * если провод у него не в поясе салона.
 *
 * Названные исключения — места ЧУЖИХ проводов (администратор, мастер),
 * вне DRF-2589: у каждого причина. Сузить список, когда провод переведён.
 */
import { describe, expect, it } from "vitest";

const SOURCES = Object.fromEntries(
  Object.entries(
    import.meta.glob("../**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<
      string,
      string
    >,
  ).map(([path, text]) => [path.replace(/^\.\//, "../lib/"), text]),
);

/**
 * Часы из строки ISO. Шире литерала (ревью 28.09): `T(\d{2}`, `T(\d\d`,
 * срез с 11-го символа любой длины, `substring(11`, `split("T")`.
 */
const WALL_CLOCK = [
  /T\(\\d\{2\}/,
  /T\(\\d\\d/,
  /\.slice\(\s*11\s*,/,
  /\.substring\(\s*11/,
  /\.split\(\s*["'`]T["'`]\s*\)/,
];

const HOME = "../lib/format.ts";

/**
 * Исключение — по ПРИЗНАКУ, а не по имени файла: строка разбора (или строка
 * над ней) несёт пометку `wall-clock-ok: <причина>`. Признак разрешения —
 * «читает время суток из расписания или время уже в поясе салона, а не
 * метку времени визита». Следующее такое место попадает в исключения по
 * правилу и с причиной, видимой на ревью; место без пометки — красное.
 */
const MARK = /wall-clock-ok:\s*\S.{9,}/;

function hits(): string[] {
  const out: string[] = [];
  for (const [path, text] of Object.entries(SOURCES)) {
    if (/\.test\.(ts|tsx)$/.test(path) || path === HOME) continue;
    const lines = text.split("\n");
    lines.forEach((line, i) => {
      if (!WALL_CLOCK.some((re) => re.test(line))) return;
      if (MARK.test(line) || MARK.test(lines[i - 1] ?? "")) return;
      out.push(`${path}:${i + 1}`);
    });
  }
  return out;
}

describe("часы из строки ISO — только в lib/format.ts (DRF-2589)", () => {
  it("обход видит исходники и дом правила", () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100);
    const home = SOURCES[HOME] ?? "";
    // Положительный контроль: в доме разбор есть, сторож его узнаёт.
    expect(WALL_CLOCK.some((re) => home.split("\n").some((l) => re.test(l)))).toBe(true);
  });

  it("вне дома — только места с пометкой причины", () => {
    expect(hits()).toEqual([]);
  });

  it("пометки есть и несут причину (иначе ноль исключений — не вердикт)", () => {
    const marked = Object.entries(SOURCES).filter(
      ([p, t]) => !/\.test\.(ts|tsx)$/.test(p) && MARK.test(t),
    );
    expect(marked.length).toBeGreaterThanOrEqual(4);
  });
});
