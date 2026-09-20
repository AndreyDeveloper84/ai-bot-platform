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
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { CUSTOMER_TABS, CustomerTabBar } from "./CustomerTabBar";

const SRC = join(__dirname, "..");

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

describe("перепись: наборов вкладок в клиентских исходниках = 1", () => {
  const screensDir = join(SRC, "screens");
  const customerScreens = readdirSync(screensDir).filter(
    (f) => f.endsWith(".tsx") && !f.includes(".test.") && /^(Customer|PilotComingSoon|PlanLite|FoodScanner)/.test(f),
  );

  it("ни один клиентский экран не рисует «Основная навигация» сам", () => {
    const offenders = customerScreens.filter((f) =>
      readFileSync(join(screensDir, f), "utf8").includes('aria-label="Основная навигация"'),
    );
    expect(offenders).toEqual([]);
    // Положительная пара: экраны с панелью её всё-таки рисуют — через компонент.
    const users = customerScreens.filter((f) =>
      readFileSync(join(screensDir, f), "utf8").includes("<CustomerTabBar"),
    );
    expect(users).toEqual(
      expect.arrayContaining([
        "CustomerWellnessDashboardScreen.tsx",
        "CustomerRecordsScreen.tsx",
        "CustomerProfileScreen.tsx",
      ]),
    );
  });

  it("каждый маршрут вкладки смонтирован в App.tsx — мёртвых вкладок нет", () => {
    const app = readFileSync(join(SRC, "App.tsx"), "utf8");
    for (const tab of CUSTOMER_TABS) {
      expect(app, `${tab.label} → ${tab.route}`).toContain(`path="${tab.route}"`);
    }
  });
});
