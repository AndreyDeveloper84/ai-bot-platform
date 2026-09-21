/**
 * Сторож класса (DRF-2198): экран, у которого есть панель, обязан рисовать её
 * во ВСЕХ ветках — иначе состояние ошибки оставляет человека на поверхности
 * без выхода. Дважды стоило выхода: «Сегодня» при 403 и профиль при 403
 * (инцидент 20.09, #1918).
 *
 * Правильных форм две: панель в КАРКАСЕ экрана (компонент с `children`, через
 * который проходят все ветки — `NotifFrame`, `ProfileFrame`) либо панель в
 * каждой ветке состояния плюс в рабочей. Сторож принимает обе и ловит третью
 * — панель, нарисованную один раз в `ready`.
 *
 * Экраны без панели вовсе — подэкраны: возврат у них аппаратной кнопкой MAX
 * (`setBackButton` / `useScreenBack` в эффекте, который отрабатывает при
 * любой ветке). Перечислены явно, с причиной; список краснеет, если у экрана
 * появилась панель или экран исчез.
 */
import { describe, expect, it } from "vitest";

const SOURCES = import.meta.glob("./Master*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Подэкраны: панели нет ни в одном состоянии, возврат — аппаратной кнопкой. */
const NO_TABBAR_BY_DESIGN: ReadonlySet<string> = new Set([
  "MasterBillingScreen",
  "MasterCustomersScreen",
  "MasterDirectionsScreen",
  "MasterInternalChatListScreen",
  "MasterInternalChatThreadScreen",
  "MasterNewBookingScreen",
  "MasterOnboardingScreen",
  "MasterPickerScreen",
  "MasterPlaceScreen",
  "MasterPublicationScreen",
  "MasterReviewsScreen",
  "MasterServiceSelectScreen",
  "MasterServicesScreen",
  "MasterSettingsScreen",
  "MasterSetupLandingScreen",
]);

const STATE_BRANCH =
  /if \(\s*(phase|state|load)\.kind === "(loading|error|error_initial|error_permission|refused|not_linked)"/g;

function screenName(path: string): string {
  return path.replace(/^.*\//, "").replace(/\.tsx$/, "");
}

function strip(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const screens = Object.entries(SOURCES)
  .filter(([p]) => !/\.test\.tsx$/.test(p))
  .map(([p, src]) => ({ name: screenName(p), src: strip(src) }));

describe("панель не исчезает в ветках состояния (DRF-2198)", () => {
  it("исходники найдены", () => {
    expect(screens.length).toBeGreaterThan(10);
  });

  it("панель — в каркасе экрана либо в каждой ветке состояния", () => {
    const offenders: { name: string; tabbars: number; branches: number }[] = [];
    for (const s of screens) {
      if (NO_TABBAR_BY_DESIGN.has(s.name)) continue;
      // Локальная обёртка вроде `TabBarBlank` — та же панель под другим именем.
      const tabbars = (s.src.match(/<MasterTabBar|<TabBarBlank/g) ?? []).length;
      if (tabbars === 0) continue;
      // Каркас: компонент принимает `children` и рисует панель — её видят все ветки.
      const framed =
        /\{children\}[\s\S]{0,400}<(MasterTabBar|TabBarBlank)/.test(s.src) ||
        /<(MasterTabBar|TabBarBlank)[\s\S]{0,400}\{children\}/.test(s.src);
      if (framed) continue;
      const branches = (s.src.match(STATE_BRANCH) ?? []).length;
      // Ветки состояния + рабочая ветка.
      if (tabbars < branches + 1) offenders.push({ name: s.name, tabbars, branches });
    }
    expect(
      offenders,
      "панель нарисована не во всех ветках и не в каркасе — человек останется без выхода",
    ).toEqual([]);
  });

  it("список исключений не переживает свою причину", () => {
    const stale = [...NO_TABBAR_BY_DESIGN].filter((n) => {
      const s = screens.find((x) => x.name === n);
      return s !== undefined && /<MasterTabBar/.test(s.src);
    });
    expect(stale, "у экрана появилась панель — убери его из NO_TABBAR_BY_DESIGN").toEqual([]);
    const missing = [...NO_TABBAR_BY_DESIGN].filter((n) => !screens.some((x) => x.name === n));
    expect(missing, "экрана больше нет — убери его из NO_TABBAR_BY_DESIGN").toEqual([]);
  });
});
