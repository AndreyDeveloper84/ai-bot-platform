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
 * # Почему проверяется тело компонента, а не файл
 *
 * Файл нередко содержит несколько компонентов: `FoodScannerCaptureScreen`
 * и `ConsentGate` внутри него, `FoodScannerProcessingScreen` и
 * `ScanErrorScreen`. Проверка по всему тексту файла зеленела бы от
 * объявления ЛЮБОГО из них — то есть ровно на той группе экранов, ради
 * которой тест и написан (там, где есть `ScreenLayout`, гарантию даёт
 * TypeScript). Поэтому вырезается тело именно того компонента, чьим
 * именем экран смонтирован. Комментарии из него убираются: слова
 * «позвать `useScreenBack`» в шапке — это не вызов.
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

/** Исходник файла экрана по имени компонента (тестовые файлы отброшены). */
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
 * проверку не попадают. `MasterOnboardingScreen` приходит не отсюда, а
 * из хелпера `inviteOnboardingRouteElements()` — он добавлен вручную в
 * ожидаемый список ниже, чтобы не выпасть молча.
 */
function mountedScreens(): string[] {
  const body = customerRoutesBody();
  const names = new Set<string>();
  for (const m of body.matchAll(/element=\{<([A-Z][A-Za-z0-9_]*)/g)) {
    names.add(m[1]!);
  }
  return [...names].filter((n) => SCREEN_FILES.has(n)).sort();
}

/**
 * Точный ожидаемый состав выборки.
 *
 * Порог вида «больше десяти» пережил бы молчаливую потерю доброго
 * десятка экранов: элемент, смонтированный через хелпер или через
 * обёртку `element={<Guard><X/></Guard>}`, из регулярки выпадает и
 * проверку больше не проходит — при этом всё зелено. Точный список
 * делает и появление экрана, и его исчезновение видимыми в диффе.
 */
const EXPECTED_SCREENS = [
  "BookingConfirmScreen",
  "BookingSuccessScreen",
  "BookingWhenScreen",
  "CatalogScreen",
  "CustomerBookingConfirmScreen",
  "CustomerBookingDetailScreen",
  "CustomerBookingSuccessScreen",
  "CustomerCardsScreen",
  "CustomerCatalogScreen",
  "CustomerEntryScreen",
  "CustomerMasterDetailScreen",
  "CustomerNotificationSettingsScreen",
  "CustomerProfileScreen",
  "CustomerRecordsScreen",
  "CustomerSlotsScreen",
  "CustomerWellnessDashboardScreen",
  "FeedbackScreen",
  "FoodScannerCaptureScreen",
  "FoodScannerDiaryScreen",
  "FoodScannerManualScreen",
  "FoodScannerProcessingScreen",
  "FoodScannerResultScreen",
  "FoodScannerSavedScreen",
  "GoalSelectScreen",
  "HelloScreen",
  "MasterPickerScreen",
  "MyVisitDetailScreen",
  "MyVisitsScreen",
  "ProfileScreen",
  "RescheduleScreen",
  "RoleNotReadyScreen",
  "ServiceDetailScreen",
] as const;

/**
 * Экраны клиентского дерева, попадающие в него не через `element={<X/>}`.
 * Проверяются наравне с остальными.
 */
const MOUNTED_VIA_HELPER = ["MasterOnboardingScreen"] as const;

/** Тело компонента `name` без комментариев, или `null` если его нет. */
function componentBody(name: string): string | null {
  const src = SCREEN_FILES.get(name);
  if (src === undefined) return null;
  const start = src.search(
    new RegExp(`^(export )?function ${name}\\s*\\(`, "m"),
  );
  if (start < 0) return null;
  const rest = src.slice(start);
  // Конец компонента — закрывающая скобка в первой колонке.
  const end = rest.search(/^\}/m);
  const body = end < 0 ? rest : rest.slice(0, end);
  return body
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

const DECLARED = /<ScreenLayout[\s\S]{0,400}?back=\{|useScreenBack\s*\(/;

const UNDER_TEST = [...EXPECTED_SCREENS, ...MOUNTED_VIA_HELPER].sort();

describe("DRF-1493 · каждый клиентский экран объявляет свой вид", () => {
  it("выборка ровно та, что ожидается", () => {
    expect(mountedScreens()).toEqual([...EXPECTED_SCREENS]);
  });

  it.each(UNDER_TEST)(
    "%s объявляет: родителя (`back={…}`) или корень (`useScreenBack`)",
    (name) => {
      const body = componentBody(name);
      expect(
        body,
        `Не нашёл тело компонента ${name} — проверка по нему ослепла`,
      ).not.toBeNull();
      expect(
        DECLARED.test(body!),
        `Экран ${name} смонтирован в клиентском дереве, но в своём теле ` +
          "нигде не объявил, куда ведёт возврат. Передайте " +
          '`back={backTo("/адрес")}` в `ScreenLayout` или вызовите ' +
          '`useScreenBack(screenRoot("почему выше некуда"))`. ' +
          "Молчание — то, чем DRF-1493 и был.",
      ).toBe(true);
    },
  );
});
