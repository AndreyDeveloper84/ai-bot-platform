/**
 * Tests for `PilotComingSoonScreen` — the brand-level honest placeholder
 * shown in prod builds where a stub surface is gated off (orchestrator
 * decision, pilot commit 4): no fake data, working navigation to the
 * real sections (profile with C5 152-ФЗ actions).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PilotComingSoonScreen } from "./PilotComingSoonScreen";

function renderWithRoutes(surface: "home" | "catalog") {
  render(
    <MemoryRouter initialEntries={["/start"]}>
      <Routes>
        <Route
          path="/start"
          element={<PilotComingSoonScreen surface={surface} />}
        />
        <Route path="/customer/profile" element={<div>PROFILE-PROBE</div>} />
        <Route path="/customer/main" element={<div>HOME-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("PilotComingSoonScreen", () => {
  it("home surface: honest copy, no fake data, CTA leads to the real profile", async () => {
    const user = userEvent.setup();
    renderWithRoutes("home");
    expect(
      screen.getByRole("heading", { name: "Главная" }),
    ).toBeInTheDocument();
    // Honest about being a placeholder — explicitly no made-up data.
    expect(screen.getByText(/выдуманных данных/)).toBeInTheDocument();
    // Active nav tab is «Главная».
    expect(screen.getByRole("button", { name: "Главная" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await user.click(screen.getByRole("button", { name: "Открыть профиль" }));
    expect(await screen.findByText("PROFILE-PROBE")).toBeInTheDocument();
  });

  it("catalog surface: заголовок «Услуги», но вкладки «Услуги» в панели нет (DRF-2191)", () => {
    // Панель одна на всех клиентских экранах и «Услуги» из неё ушли
    // (§55 б, макет DRF-1321): подсвечивать на заглушке каталога нечего,
    // и врать «ты в Записях» нельзя — активной вкладки просто нет.
    renderWithRoutes("catalog");
    expect(screen.getByRole("heading", { name: "Услуги" })).toBeInTheDocument();
    const nav = within(screen.getByRole("navigation", { name: "Основная навигация" }));
    expect(nav.queryByRole("button", { name: "Услуги" })).toBeNull();
    expect(nav.queryByRole("button", { current: "page" })).toBeNull();
    expect(screen.getByText(/выдуманных/)).toBeInTheDocument();
  });

  it("nav «Профиль» ведёт в настоящий профиль (C5 actions live there)", async () => {
    const user = userEvent.setup();
    renderWithRoutes("home");
    await user.click(screen.getByRole("button", { name: "Профиль" }));
    expect(await screen.findByText("PROFILE-PROBE")).toBeInTheDocument();
  });

  it("home surface: активна «Главная», панель — пять вкладок макета (DRF-2191)", () => {
    renderWithRoutes("home");
    const nav = within(screen.getByRole("navigation", { name: "Основная навигация" }));
    expect(nav.getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
      "Главная", "План", "Дневник", "Записи", "Профиль",
    ]);
    expect(nav.getByRole("button", { name: "Главная" })).toHaveAttribute("aria-current", "page");
  });

  it("nav «Главная» tab leads to the home screen, and «День» is not offered", async () => {
    // DRF-1546: поверхности «День» не существует — её роль исполнял
    // домашний экран, а он теперь «Главная». Стража парная: вкладки
    // «День» нет, но «Главная» ведёт куда обещает.
    const user = userEvent.setup();
    renderWithRoutes("catalog");
    await user.click(screen.getByRole("button", { name: "Главная" }));
    expect(await screen.findByText("HOME-PROBE")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "День" }),
    ).not.toBeInTheDocument();
  });
});
