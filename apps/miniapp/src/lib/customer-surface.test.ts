/**
 * DRF-2687 — граница клиентской поверхности и сторож кнопок чата.
 *
 * Каскад ролей в `App.tsx` отдаёт сотруднику клиентское дерево по
 * `isCustomerSurfacePath`. Если в `_ROUTE_MAP` появится цель кнопки вне
 * `/customer/*`, `/master/*`, `/admin/*`, сотрудник по такой кнопке снова
 * молча окажется в своём кабинете — второй узел краснеет раньше.
 */
import { describe, expect, it } from "vitest";

import { isCustomerSurfacePath } from "./customer-surface";
import { parseStartRoute } from "./max-sdk";

const MAX_SDK = import.meta.glob("./max-sdk.ts", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Слаги из тела `_ROUTE_MAP` — ключи вида `open_catalog:` и `catalog:`. */
function routeMapSlugs(): string[] {
  const source = Object.values(MAX_SDK)[0] ?? "";
  const start = source.indexOf("const _ROUTE_MAP");
  const end = source.indexOf("\n};", start);
  const body = source.slice(start, end);
  return [...body.matchAll(/^\s{2}([a-z_0-9]+):\s*"/gm)].flatMap((m) => (m[1] ? [m[1]] : []));
}

describe("isCustomerSurfacePath", () => {
  it.each(["/customer/food-scanner/capture", "/customer/main", "/feedback/abc"])(
    "%s — клиентская поверхность",
    (path) => {
      expect(isCustomerSurfacePath(path)).toBe(true);
    },
  );

  it.each(["/", "/customer", "/customerX/main", "/master/dashboard", "/admin/team", "/catalog/1"])(
    "%s — не клиентская поверхность",
    (path) => {
      expect(isCustomerSurfacePath(path)).toBe(false);
    },
  );
});

describe("цели кнопок чата", () => {
  it("каждая ведёт в кабинет по своему префиксу или на клиентскую поверхность", () => {
    const slugs = routeMapSlugs();
    // Узел не пустой: разбор исходника действительно нашёл карту.
    expect(slugs).toContain("open_food_scan");
    expect(slugs.length).toBeGreaterThan(10);

    const stray = slugs
      .map((slug) => [slug, parseStartRoute(slug)] as const)
      .filter(
        ([, target]) =>
          target === null ||
          !(
            target.startsWith("/admin/") ||
            target.startsWith("/master/") ||
            isCustomerSurfacePath(target)
          ),
      );
    expect(stray).toEqual([]);
  });

  it("цели с идентификатором тоже клиентские", () => {
    const id = "123e4567-e89b-12d3-a456-426614174000";
    const targets = [parseStartRoute(`reco_${id}`), parseStartRoute(`reschedule_${id}`)];
    expect(targets.every((t) => t !== null && isCustomerSurfacePath(t))).toBe(true);
  });
});
