/**
 * Tests for `PilotComingSoonScreen` — the brand-level honest placeholder
 * shown in prod builds where a stub surface is gated off (orchestrator
 * decision, pilot commit 4): no fake data, working navigation to the
 * real sections (profile with C5 152-ФЗ actions).
 */
import { render, screen } from "@testing-library/react";
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

  it("catalog surface: «Услуги» title and active «Услуги» tab", () => {
    renderWithRoutes("catalog");
    expect(
      screen.getByRole("heading", { name: "Услуги" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Услуги" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByText(/выдуманных/)).toBeInTheDocument();
  });

  it("nav «Я» tab leads to the real profile (C5 actions live there)", async () => {
    const user = userEvent.setup();
    renderWithRoutes("home");
    await user.click(screen.getByRole("button", { name: "Я" }));
    expect(await screen.findByText("PROFILE-PROBE")).toBeInTheDocument();
  });
});
