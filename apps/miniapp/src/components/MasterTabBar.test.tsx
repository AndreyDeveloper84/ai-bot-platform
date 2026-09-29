/**
 * Нижняя панель мастера — ровно три (DRF-2121, §28 п.2 / §50).
 *
 * «Сегодня | Расписание | Ayla». «Профиль» и «Диалоги» из панели сняты:
 * профиль — через аватар (§28 п.3), переписка мастер↔клиент подлежит
 * снятию (DRF-1039/1255), маршрут живёт по прямой ссылке. Точка «есть
 * изменения» на «Расписании» остаётся; счётчик непрочитанных в панели
 * больше не живёт (он на кнопке «Диалоги» в шапке «Сегодня»).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { MASTER_TAB_LABELS, MasterTabBar } from "./MasterTabBar";

type Props = Parameters<typeof MasterTabBar>[0];

const NONE: Props = { scheduleHasPendingChange: false };

function renderAt(path: string, props: Partial<Props> = {}) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<MasterTabBar {...NONE} {...props} />} />
      </Routes>
      <Routes>
        <Route path="/master/ayla" element={<div>AYLA-PROBE</div>} />
        <Route path="*" element={null} />
      </Routes>
    </MemoryRouter>,
  );
}

function labels(): string[] {
  const nav = screen.getByRole("navigation", { name: "Основная навигация" });
  return within(nav)
    .getAllByRole("button")
    .map((t) => t.getAttribute("aria-label") ?? "");
}

describe("MasterTabBar", () => {
  it("ровно три: «Сегодня · Расписание · Ayla», активная — по адресу", () => {
    renderAt("/master/schedule");
    expect(labels()).toEqual(["Сегодня", "Расписание", "Ayla"]);
    expect(labels()).toEqual([...MASTER_TAB_LABELS]);
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(within(nav).getByRole("button", { name: "Расписание" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("button", { name: "Сегодня" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("«Профиль», «Диалоги», «Ещё», «Дом» в панели не живут", () => {
    renderAt("/master/dashboard");
    expect(labels()).toHaveLength(3);
    for (const gone of ["Профиль", "Диалоги", "Ещё", "Дом"]) {
      expect(labels()).not.toContain(gone);
    }
  });

  it("точка изменений — на «Расписании»", () => {
    renderAt("/master/dashboard", { scheduleHasPendingChange: true });
    expect(
      within(screen.getByRole("button", { name: "Расписание" })).getByLabelText("есть изменения"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("button", { name: "Сегодня" })).queryByLabelText("есть изменения"),
    ).not.toBeInTheDocument();
  });

  it("без изменений — точек нет", () => {
    renderAt("/master/dashboard");
    expect(screen.getAllByRole("button")).toHaveLength(3);
    expect(screen.queryByLabelText("есть изменения")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/непрочитанных/)).not.toBeInTheDocument();
  });

  it("на соло-поверхности бар не рисуется — там свой", () => {
    renderAt("/solo/my-day");
    expect(screen.queryByRole("navigation", { name: "Основная навигация" })).not.toBeInTheDocument();
  });

  it("тап ведёт на адрес вкладки", async () => {
    const user = userEvent.setup();
    renderAt("/master/dashboard");
    await user.click(screen.getByRole("button", { name: "Ayla" }));
    expect(await screen.findByText("AYLA-PROBE")).toBeInTheDocument();
  });
});
