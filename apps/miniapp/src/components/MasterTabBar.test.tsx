/**
 * `MasterTabBar` — нижняя навигация мастера (master-mobile §M1). До DRF-1848
 * у компонента не было ни одного теста (карта кабинета D13).
 *
 * Заперто: четыре вкладки и активная по адресу; число непрочитанных на
 * «Диалогах» (0 — значка нет, >99 — «99+»); точки изменений на «Расписании»
 * и «Профиле»; на соло-поверхности (`/solo/*`) бар не рисуется — там свой;
 * тап ведёт на адрес вкладки.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { MasterTabBar } from "./MasterTabBar";

type Props = Parameters<typeof MasterTabBar>[0];

const NONE: Props = {
  unreadCount: 0,
  scheduleHasPendingChange: false,
  profileHasOwnerPendingChange: false,
};

function renderAt(path: string, props: Partial<Props> = {}) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<MasterTabBar {...NONE} {...props} />} />
      </Routes>
      <Routes>
        <Route path="/master/conversations" element={<div>CONVERSATIONS-PROBE</div>} />
        <Route path="*" element={null} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("MasterTabBar", () => {
  it("четыре вкладки, активная — по адресу", () => {
    renderAt("/master/schedule");
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    const tabs = within(nav).getAllByRole("button");
    expect(tabs.map((t) => t.getAttribute("aria-label"))).toEqual([
      "Дом",
      "Расписание",
      "Диалоги",
      "Профиль",
    ]);
    expect(within(nav).getByRole("button", { name: "Расписание" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("button", { name: "Дом" })).not.toHaveAttribute("aria-current");
  });

  it("число непрочитанных на «Диалогах»", () => {
    renderAt("/master/dashboard", { unreadCount: 3 });
    const badge = screen.getByLabelText("непрочитанных: 3");
    expect(badge).toHaveTextContent("3");
    expect(
      within(screen.getByRole("button", { name: "Диалоги" })).getByLabelText("непрочитанных: 3"),
    ).toBe(badge);
  });

  it("ноль — значка нет", () => {
    renderAt("/master/dashboard", { unreadCount: 0 });
    expect(screen.queryByLabelText(/непрочитанных/)).not.toBeInTheDocument();
  });

  it("больше 99 — «99+»", () => {
    renderAt("/master/dashboard", { unreadCount: 120 });
    expect(screen.getByLabelText("непрочитанных: 120")).toHaveTextContent("99+");
  });

  it("точки изменений — на «Расписании» и «Профиле»", () => {
    renderAt("/master/dashboard", {
      scheduleHasPendingChange: true,
      profileHasOwnerPendingChange: true,
    });
    for (const name of ["Расписание", "Профиль"]) {
      expect(
        within(screen.getByRole("button", { name })).getByLabelText("есть изменения"),
      ).toBeInTheDocument();
    }
    expect(
      within(screen.getByRole("button", { name: "Дом" })).queryByLabelText("есть изменения"),
    ).not.toBeInTheDocument();
  });

  it("без изменений — точек нет", () => {
    renderAt("/master/dashboard");
    expect(screen.queryByLabelText("есть изменения")).not.toBeInTheDocument();
  });

  it("на соло-поверхности бар не рисуется — там свой", () => {
    renderAt("/solo/my-day", { unreadCount: 5 });
    expect(screen.queryByRole("navigation", { name: "Основная навигация" })).not.toBeInTheDocument();
  });

  it("тап ведёт на адрес вкладки", async () => {
    const user = userEvent.setup();
    renderAt("/master/dashboard");
    await user.click(screen.getByRole("button", { name: "Диалоги" }));
    expect(await screen.findByText("CONVERSATIONS-PROBE")).toBeInTheDocument();
  });
});
