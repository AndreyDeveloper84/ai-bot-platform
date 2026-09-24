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
 * устроены иначе, и их экраны этого объявления пока не несут.
 * Расширять проверку на них здесь значило бы поменять заодно и их
 * навигацию — отдельная работа с отдельным решением владельца
 * (см. тело PR). Граница проведена явно и здесь названа, чтобы её не
 * приняли за недосмотр.
 *
 * # Довод про нижнюю навигацию верен НЕ ДЛЯ ВСЕХ экранов кабинета
 *
 * Здесь стояло: «у них есть постоянная нижняя навигация (`MasterTabBar` /
 * `AdminTabBar`)» — то есть выход есть всегда, и объявление не нужно.
 * Замер карты кабинета (DRF-2365, 23.09) показал, что это верно не
 * везде: **шесть экранов админа нижней навигации не несут** —
 * деактивация мастера, очередь передач, ветка внутреннего чата,
 * карточка мастера, новая запись, готовность. Выход у них есть, но
 * держится он на том, что автор не забыл нарисовать свою кнопку, —
 * ровно то состояние, из-за которого этот сторож и появился.
 *
 * Сегодняшней поломки за этим нет: все шесть возвращают ПО АДРЕСУ, а
 * `navigate(-1)` в кабинете не встречается ни разу. Не хватает
 * объявления, то есть защиты от следующего автора. Расширение сторожа
 * на эти экраны — DRF-2368, отдельным PR: оно требует перевести их на
 * `useScreenBack`, а это меняет поведение (появляется аппаратная
 * кнопка MAX), и такое не делают попутной правкой докстроки.
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
  "BookingWhenScreen",
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
  "ExecutionOptionScreen",
  "FeedbackScreen",
  "FoodScannerCaptureScreen",
  "FoodScannerDayScreen",
  "FoodScannerDiaryScreen",
  "FoodScannerFavoritesScreen",
  "FoodScannerManualScreen",
  "FoodScannerProcessingScreen",
  "FoodScannerResultScreen",
  "FoodScannerSavedScreen",
  "FoodScannerWeekScreen",
  "GoalSelectScreen",
  "HelloScreen",
  "MasterPickerScreen",
  "PlanLiteScreen",
  "ProviderChoiceScreen",
  "RecommendationCardScreen",
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

/**
 * DRF-2368 — вторая рука сторожа: экраны кабинета БЕЗ нижней навигации.
 *
 * Прежний довод («у админа есть постоянная нижняя навигация, поэтому
 * объявление ему не нужно») верен не для всех: шесть экранов кабинета её не
 * несут, и выход у них держался на том, что автор не забыл нарисовать свою
 * кнопку. Сегодняшней поломки за этим не было — все шесть возвращали по
 * адресу, — но не было и защиты от следующего автора, а она и есть предмет
 * этого сторожа.
 *
 * Выборка НЕ ручной список: она вычисляется — экран кабинета, в теле
 * которого нет `AdminTabBar`. Седьмой такой экран попадёт сюда сам, и
 * именно этого прежняя граница не умела.
 *
 * Экраны С нижней навигацией остаются вне проверки намеренно: у них выход
 * есть всегда, и требовать от них объявления — отдельное решение с отдельной
 * ценой (у каждого появится аппаратная кнопка MAX).
 *
 * `useBackButton` объявлением НЕ считается: это старый хук аппаратной
 * кнопки, он не связывает её с видимой стрелкой и не несёт вида экрана.
 * Признать его значило бы принять два приёма за один — ровно та слабость,
 * которой сторож избегает.
 */
const ADMIN_TAB_BAR = /<AdminTabBar|<SalonPilotFrame/;

/**
 * Два исключения, и оба названы, а не подразумеваются.
 *
 * `SalonPilotFrame` — сам каркас с навигацией: в его теле нет `<SalonPilotFrame`
 * по той же причине, по которой дверь не содержит саму себя. Требовать от
 * него объявления бессмысленно.
 *
 * `AdminAddPersonScreen` — хозяин на 150 строк, который выбирает, какую из
 * двух секций показать; человек видит СЕКЦИЮ, и объявление живёт там
 * (`AddPersonAccessCodeSection`, `AddPersonNewMasterSection` — обе в выборке
 * ниже). Второе объявление у хозяина нарушило бы «ровно один вызов на
 * смонтированное дерево» — это привело бы к двум переходам на одно нажатие,
 * то есть ровно к дефекту, который `useScreenBack` и закрывает.
 */
const NOT_A_SCREEN_ITSELF = new Set(["SalonPilotFrame", "AdminAddPersonScreen"]);

/** Экраны кабинета без постоянной нижней навигации — выборка, а не список. */
function adminScreensWithoutTabBar(): string[] {
  const names: string[] = [];
  for (const [path, src] of Object.entries(SCREEN_SOURCES)) {
    if (path.endsWith(".test.tsx")) continue;
    if (!path.includes("/admin/")) continue;
    const name = path.split("/").pop()!.replace(/\.tsx$/, "");
    const isScreen = /^(Admin|SalonPilot)/.test(name) || /Section$/.test(name);
    if (!isScreen || NOT_A_SCREEN_ITSELF.has(name)) continue;
    if (!/export function /.test(src)) continue;
    if (ADMIN_TAB_BAR.test(src)) continue;
    names.push(name);
  }
  return names.sort();
}

/**
 * Точный ожидаемый состав — по тому же доводу, что у клиентского дерева:
 * порог «сколько-то» пережил бы молчаливую потерю экрана из выборки.
 */
const EXPECTED_ADMIN_SCREENS = [
  "AddPersonAccessCodeSection",
  "AddPersonNewMasterSection",
  "AdminDeactivationFlowScreen",
  "AdminHandoffQueueScreen",
  "AdminInternalChatThreadScreen",
  "AdminInvitesScreen",
  "AdminMasterDetailScreen",
  "AdminNewBookingScreen",
  "AdminReadinessScreen",
];

describe("DRF-2368 · экран кабинета без нижней навигации объявляет возврат", () => {
  it("выборка ровно та, что ожидается", () => {
    expect(adminScreensWithoutTabBar()).toEqual([...EXPECTED_ADMIN_SCREENS]);
  });

  it.each(EXPECTED_ADMIN_SCREENS)(
    "%s объявляет, куда ведёт возврат",
    (name) => {
      const body = componentBody(name);
      expect(
        body,
        `Не нашёл тело компонента ${name} — проверка по нему ослепла`,
      ).not.toBeNull();
      expect(
        DECLARED.test(body!),
        `Экран ${name} живёт в кабинете без нижней навигации, то есть выход ` +
          "у него только свой, — и в теле нигде не объявил, куда он ведёт. " +
          'Позовите `useScreenBack(backTo("/адрес"))`, а для потока из ' +
          "нескольких шагов — `useScreenBack(backByAction(…))`, чтобы " +
          "аппаратная кнопка не выносила человека из потока с середины. " +
          "`useBackButton` объявлением не считается.",
      ).toBe(true);
    },
  );
});
