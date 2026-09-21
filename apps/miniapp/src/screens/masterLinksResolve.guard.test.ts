/**
 * DRF-2247 — ссылка на экране мастера ведёт в маршрут его поверхности.
 *
 * У мастера салона «Рабочие часы» и «Место работы» в Настройках и «Настроить
 * рабочие часы» на пустой неделе «Расписания» вели на `/solo/*`. Маршрутов
 * соло у салонного мастера нет — `CatchAllRedirect` молча выбрасывал его на
 * «Сегодня». Ни ошибки, ни экрана: кнопка «работала», просто не туда.
 *
 * Класс, а не три строки:
 * 1. каждая полная цель `/master/…` и `/solo/…` в экранах, смонтированных на
 *    `/master/*`, резолвится в маршрут `App.tsx`;
 * 2. цель `/solo/…` на таком экране допустима, только если выбрана по
 *    поверхности: рядом (±1 строка) стоит пара `/master/…` (тернарник
 *    `isSolo ? … : …`) или это проверка `startsWith("/solo/")`. Остальное —
 *    мёртвая дверь, если экран не держит её внутри соло-ветки явно
 *    (`SOLO_ONLY_SITES`, красный в обе стороны).
 */
import { describe, expect, it } from "vitest";

const APP = Object.values(
  import.meta.glob("../App.tsx", { query: "?raw", import: "default", eager: true }) as Record<
    string,
    string
  >,
)[0] as string;

const SOURCES = import.meta.glob("./**/Master*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Цели `/solo/…`, которые экран держит внутри соло-ветки (`isSolo ? (…)`). */
const SOLO_ONLY_SITES: Record<string, string> = {
  "MasterWorkingHoursScreen:/solo/setup":
    "«Продолжить позже» — внутри ветки isSolo: дверь по чек-листу соло-настройки (DRF-1807)",
};

function screenName(path: string): string {
  return path.replace(/^.*\//, "").replace(/\.tsx$/, "");
}

/** Код без комментариев; строки сохраняют номера (комментарий → пустые строки). */
function stripComments(src: string): string {
  const noBlock = src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
  return noBlock.replace(/(^|[^:])\/\/.*$/gm, "$1");
}

const ROUTES = [...APP.matchAll(/path="(\/(?:master|solo)\/[^"]*)"/g)].map((m) => m[1] ?? "");
const ROUTE_RX = ROUTES.map((p) => new RegExp(`^${p.replace(/:[^/]+/g, "[^/]+")}$`));

const MASTER_MOUNTED = new Set(
  [...APP.matchAll(/path="\/master\/[^"]*"\s*element=\{<(\w+)/g)].map((m) => m[1] ?? ""),
);

const screens = Object.entries(SOURCES)
  .map(([path, src]) => ({ name: screenName(path), code: stripComments(src) }))
  .filter((s) => MASTER_MOUNTED.has(s.name));

function resolves(target: string): boolean {
  const t = target.split(/[?#]/)[0]?.replace(/\$\{[^}]*\}/g, "x").replace(/\/$/, "") ?? "";
  return ROUTE_RX.some((rx) => rx.test(t));
}

interface Site {
  screen: string;
  line: number;
  target: string;
  text: string;
}

function sites(): Site[] {
  const out: Site[] = [];
  for (const s of screens) {
    const lines = s.code.split("\n");
    lines.forEach((text, i) => {
      for (const m of text.matchAll(/["'`](\/(?:master|solo)\/[^"'`]*)/g)) {
        out.push({ screen: s.name, line: i + 1, target: m[1] ?? "", text: text.trim() });
      }
    });
  }
  return out;
}

describe("ссылки экранов мастера — в маршруты своей поверхности (DRF-2247)", () => {
  const all = sites();

  it("положительная пара: сторож видит экраны и цели", () => {
    expect(screens.length).toBeGreaterThanOrEqual(8);
    expect(ROUTES).toContain("/master/working-hours");
    expect(all.some((s) => s.target === "/master/working-hours")).toBe(true);
  });

  it("каждая полная цель резолвится в маршрут App.tsx", () => {
    const dead = all
      .filter((s) => !/^\/(master|solo)\/$/.test(s.target))
      .filter((s) => !resolves(s.target))
      .map((s) => `${s.screen}:${s.line} ${s.target}`);
    expect(dead, "цель, которой нет в App.tsx, — выброс на «Сегодня»").toEqual([]);
  });

  it("цель /solo/… на экране /master/* выбрана по поверхности", () => {
    const codeOf = new Map(screens.map((s) => [s.name, s.code.split("\n")]));
    const unguarded = all
      .filter((s) => s.target.startsWith("/solo/") && s.target !== "/solo/")
      .filter((s) => {
        const lines = codeOf.get(s.screen) ?? [];
        const around = lines.slice(Math.max(0, s.line - 2), s.line + 1).join("\n");
        const paired = /["'`]\/master\//.test(around);
        return !paired && !(`${s.screen}:${s.target}` in SOLO_ONLY_SITES);
      })
      .map((s) => `${s.screen}:${s.line} ${s.target}`);
    expect(unguarded, "салонного мастера такая ссылка выбросит на «Сегодня»").toEqual([]);
  });

  it("список соло-веток не устарел", () => {
    for (const key of Object.keys(SOLO_ONLY_SITES)) {
      const [screen, target] = key.split(":");
      expect(
        all.some((s) => s.screen === screen && s.target === target),
        `${key} больше нет — снять из списка`,
      ).toBe(true);
    }
  });
});
