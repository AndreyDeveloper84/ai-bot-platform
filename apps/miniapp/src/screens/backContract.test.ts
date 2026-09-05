/**
 * DRF-1493 — экран не может молча не объявить свой вид.
 *
 * # Почему одного типа мало
 *
 * `ScreenLayout` требует `back`, и экран, который его не передаст, не
 * соберётся. Но два из пяти экранов, у которых возврата не оказалось
 * (`CustomerRecordsScreen`, `CustomerWellnessDashboardScreen`), общий
 * каркас не используют вовсе — они рисуют свою разметку. Для них
 * компилятору сказать нечего, и именно так дыра открывается заново:
 * следующий автор напишет ещё один экран без `ScreenLayout`, и никто
 * не заметит.
 *
 * Поэтому проверка идёт от РОУТЕРА, а не от каркаса: берётся всё, что
 * `CustomerRoutes` монтирует как экран, и от каждого требуется
 * объявление — либо `back={…}` у `ScreenLayout`, либо прямой вызов
 * `useScreenBack(…)`. Молчание валидным состоянием не является.
 *
 * # Почему только клиентское дерево
 *
 * DRF-1493 — про клиентскую поверхность: это в ней человек оказывался
 * в тупике после deep link из бота. Поверхности мастера и админа
 * устроены иначе — у них есть постоянная нижняя навигация
 * (`MasterTabBar` / `AdminTabBar`), и их экраны этого объявления пока
 * не несут. Расширять проверку на них здесь значило бы поменять
 * заодно и их навигацию — отдельная работа с отдельным решением
 * владельца (см. тело PR). Граница проведена явно и здесь названа,
 * чтобы её не приняли за недосмотр.
 *
 * # Почему исходники читаются через `import.meta.glob`
 *
 * `node:fs` в этом пакете не типизирован (`@types/node` не стоит), а
 * тащить его ради одного теста — плата больше пользы. Vite отдаёт
 * содержимое файлов как строки на этапе сборки теста, чего проверке и
 * достаточно.
 *
 * Тест умеет падать: снимите объявление у любого экрана — и он назовёт
 * этот экран поимённо.
 */
import { describe, expect, it } from "vitest";

const APP = import.meta.glob("../App.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const SCREEN_SOURCES = import.meta.glob("./**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Исходник экрана по имени компонента (тестовые файлы отброшены). */
const SCREEN_FILES = new Map<string, string>(
  Object.entries(SCREEN_SOURCES)
    .filter(([p]) => !p.endsWith(".test.tsx"))
    .map(([p, src]) => [p.split("/").pop()!.replace(/\.tsx$/, ""), src]),
);

/** Тело `CustomerRoutes` — клиентское дерево адресов целиком. */
function customerRoutesBody(): string {
  const app = Object.values(APP)[0];
  expect(app, "App.tsx не прочитан — проверка ослепла").toBeTypeOf("string");
  const start = app!.indexOf("export function CustomerRoutes()");
  expect(
    start,
    "CustomerRoutes переименован — проверка ослепла",
  ).toBeGreaterThan(0);
  // Конец функции — закрывающая скобка в первой колонке.
  const rest = app!.slice(start);
  const end = start + rest.search(/^\}/m);
  expect(end, "не нашёл конец CustomerRoutes").toBeGreaterThan(start);
  return app!.slice(start, end);
}

/**
 * Всё, что клиентское дерево монтирует как экран.
 *
 * Часть элементов — служебные обёртки, объявленные прямо в `App.tsx`.
 * Своего файла в `screens/` у них нет, экранами они не являются и в
 * проверку не попадают.
 */
function mountedScreens(): string[] {
  const body = customerRoutesBody();
  const names = new Set<string>();
  for (const m of body.matchAll(/element=\{<([A-Z][A-Za-z0-9_]*)/g)) {
    names.add(m[1]!);
  }
  return [...names].filter((n) => SCREEN_FILES.has(n)).sort();
}

const DECLARED = /<ScreenLayout[\s\S]{0,400}?back=\{|useScreenBack\s*\(/;

describe("DRF-1493 · каждый клиентский экран объявляет свой вид", () => {
  it("выборка непуста и содержит экраны из задачи", () => {
    const screens = mountedScreens();
    expect(screens.length).toBeGreaterThan(10);
    // Пять экранов, с которых началась задача.
    expect(screens).toContain("CustomerCatalogScreen");
    expect(screens).toContain("CustomerRecordsScreen");
    expect(screens).toContain("CustomerWellnessDashboardScreen");
    expect(screens).toContain("CustomerBookingSuccessScreen");
    expect(screens).toContain("CatalogScreen");
  });

  it.each(mountedScreens())(
    "%s объявляет: родителя (`back={…}`) или корень (`useScreenBack`)",
    (name) => {
      const src = SCREEN_FILES.get(name)!;
      expect(
        DECLARED.test(src),
        `Экран ${name} смонтирован в CustomerRoutes, но нигде не объявил, ` +
          'куда ведёт возврат. Передайте `back={backTo("/адрес")}` в ' +
          "`ScreenLayout` или вызовите " +
          '`useScreenBack(screenRoot("почему выше некуда"))`. ' +
          "Молчание — то, чем DRF-1493 и был.",
      ).toBe(true);
    },
  );
});
