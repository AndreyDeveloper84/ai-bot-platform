/**
 * Нижняя панель клиентского Mini App — одна на всех экранах (DRF-2191, H01-b).
 *
 * Макет DRF-1321 v1.2 и решение владельца §55 б: «Главная · План · Дневник ·
 * Записи · Профиль». DRF-2144 переименовал панель только на Главной; «Мои
 * записи», «Профиль» и заглушка пилота рисовали старую «Главная · Записи ·
 * Услуги · Я» каждая своей разметкой — четыре набора вкладок в коде.
 *
 * Сторожа:
 *   - ровно один компонент `CustomerTabBar` с ровно пятью вкладками в этом
 *     порядке и с этими подписями; «Услуги» и «Я» из панели ушли;
 *   - переписьный сторож: ни один клиентский экран не рисует
 *     `aria-label="Основная навигация"` сам — только через компонент
 *     (наборов вкладок в исходниках = 1);
 *   - активная вкладка — `aria-current="page"` и не кликается; остальные
 *     ведут на свои маршруты, и каждый маршрут смонтирован в App.tsx
 *     (мёртвых вкладок нет).
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { CUSTOMER_TABS, CustomerTabBar } from "./CustomerTabBar";

/**
 * Исходники — тем же способом, что `backContract.test.ts` и словарь мастера.
 * Берутся ВСЕ экраны и компоненты, а не выборка по имени файла: панель может
 * появиться и в экране, которого нет в клиентском именовании
 * (`GoalSelectScreen`, `ServiceDetailScreen`…), и в обёртке-компоненте.
 * Список панелей — белый, по одному файлу на поверхность.
 */
const SOURCES = import.meta.glob(["../screens/**/*.tsx", "../components/**/*.tsx", "../App.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Панель каждой поверхности — ровно один файл; всё остальное её не рисует. */
const TAB_BAR_FILES = [
  "CustomerTabBar.tsx",
  "MasterTabBar.tsx",
  "AdminTabBar.tsx",
  "SalonPilotTabBar.tsx",
  // Соло-панель владельца живёт в App.tsx (DRF-2127) — там же её разметка.
  "App.tsx",
];

const isTest = (path: string) => path.includes(".test.");
const baseName = (path: string) => path.split("/").pop() as string;
const APP_SOURCE = Object.fromEntries(
  Object.entries(SOURCES).filter(([path]) => baseName(path) === "App.tsx"),
);

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderBar(active: Parameters<typeof CustomerTabBar>[0]["active"]) {
  return render(
    <MemoryRouter initialEntries={["/customer/x"]}>
      <Routes>
        <Route path="/customer/x" element={<CustomerTabBar active={active} />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CustomerTabBar — пять вкладок по макету", () => {
  it("ровно «Главная · План · Дневник · Записи · Профиль», в этом порядке; «Услуги» и «Я» ушли", () => {
    renderBar("home");
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    const tabs = within(nav).getAllByRole("button");
    expect(tabs.map((t) => t.getAttribute("aria-label"))).toEqual([
      "Главная",
      "План",
      "Дневник",
      "Записи",
      "Профиль",
    ]);
    expect(within(nav).queryByText("Услуги")).toBeNull();
    expect(within(nav).queryByText("Я")).toBeNull();
    expect(CUSTOMER_TABS).toHaveLength(5);
  });

  it.each([
    ["home", "Главная", "/customer/main"],
    ["plan", "План", "/customer/plan"],
    ["diary", "Дневник", "/customer/food-scanner/diary"],
    ["records", "Записи", "/customer/records"],
    ["profile", "Профиль", "/customer/profile"],
  ] as const)("активная %s — aria-current и без перехода; соседняя ведёт на свой маршрут", (active, label, route) => {
    renderBar(active);
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    const current = within(nav).getByRole("button", { name: label });
    expect(current).toHaveAttribute("aria-current", "page");
    fireEvent.click(current);
    expect(screen.queryByTestId("location")).toBeNull(); // остались на месте

    const other = CUSTOMER_TABS.find((t) => t.key !== active)!;
    fireEvent.click(within(nav).getByRole("button", { name: other.label }));
    expect(screen.getByTestId("location")).toHaveTextContent(other.route);
    expect(CUSTOMER_TABS.find((t) => t.key === active)?.route).toBe(route);
  });
});

describe("перепись: наборов вкладок в исходниках — по одному на поверхность", () => {
  const sources = Object.entries(SOURCES).filter(
    ([path]) => !isTest(path) && !TAB_BAR_FILES.includes(baseName(path)),
  );

  it("ни один экран и ни один компонент не рисует панель сам", () => {
    // Две приметы разом: подпись панели и её класс — копипаста прежнего блока
    // (самый вероятный возврат) попадается по обеим, а переименованная
    // подпись — по классу. Сторож ловит копию, а не всякую навигацию вообще:
    // это названо здесь, чтобы «зелёный» не читался шире, чем он есть.
    const offenders = sources
      .filter(
        ([, src]) =>
          src.includes('aria-label="Основная навигация"') || src.includes('"wellness-dash__nav'),
      )
      .map(([path]) => baseName(path));
    expect(offenders).toEqual([]);
    // Положительная пара: экраны с панелью её всё-таки рисуют — через компонент.
    const users = sources
      .filter(([, src]) => src.includes("<CustomerTabBar"))
      .map(([path]) => baseName(path))
      .sort();
    expect(users).toEqual(
      expect.arrayContaining([
        "CustomerWellnessDashboardScreen.tsx",
        "CustomerRecordsScreen.tsx",
        "CustomerProfileScreen.tsx",
        "PilotComingSoonScreen.tsx",
        // DRF-2201 — «План» и «Дневник» стали вкладками-корнями.
        "PlanLiteScreen.tsx",
        "FoodScannerDiaryScreen.tsx",
      ]),
    );
  });

  it("кто рисует панель — тот объявил себя корнем (DRF-2201)", () => {
    // Панель есть только у корней: вкладка — верх поверхности, стрелке
    // «назад» там взяться неоткуда (DRF-1493: адрес, не история). Перепись
    // держит это фактом файла, а не памятью: экран с панелью и с `backTo`
    // — противоречие, и оно должно быть красным.
    const withBar = Object.entries(SOURCES).filter(
      ([path, src]) => !isTest(path) && src.includes("<CustomerTabBar"),
    );
    expect(withBar.length, "панель вообще кто-то рисует").toBeGreaterThanOrEqual(5);
    const notRoots = withBar
      .filter(([, src]) => !/useScreenBack\(\s*screenRoot\(/.test(src))
      .map(([path]) => baseName(path));
    expect(notRoots).toEqual([]);
    const withParent = withBar
      .filter(([, src]) => /useScreenBack\(\s*backTo\(/.test(src))
      .map(([path]) => baseName(path));
    expect(withParent).toEqual([]);
  });

  it("каждый маршрут вкладки смонтирован в App.tsx — мёртвых вкладок нет", () => {
    const app = Object.values(APP_SOURCE)[0];
    expect(app).toBeTruthy();
    for (const tab of CUSTOMER_TABS) {
      expect(app, `${tab.label} → ${tab.route}`).toContain(`path="${tab.route}"`);
    }
  });
});
