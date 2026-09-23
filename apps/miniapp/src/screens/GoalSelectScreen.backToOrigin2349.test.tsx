/**
 * Экран цели возвращает туда, откуда его открыли (DRF-2349, §61 вопрос 42).
 *
 * «Изменить цель» открывают и с Главной, и с «Плана». `PlanLiteScreen` давно
 * передаёт `returnTo`, но экран его не читал и уводил на Главную всех —
 * человек, менявший цель своего плана, оказывался не там, откуда пришёл.
 *
 * Узлы идут парами: с происхождением и без. Второй в каждой паре — не
 * формальность: без него правка просто переставила бы дефект на вход
 * с Главной.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return { ...original, fetchDecisionContext: vi.fn(), postGoalSelect: vi.fn() };
});

import { fetchDecisionContext, type DecisionContext } from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);

const NEEDS_GOAL: DecisionContext = {
  version: 1,
  known: { goal: null },
  missing: [{ kind: "goal", prompt: "Что хочешь получить от визита?" }],
  suggestions: [{ key: "tone_up", label: "Подтянуть фигуру" }],
  intents: [{ id: "choose_suggested", label: "Выбери из вариантов" }],
};

function BackProbe() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname}</div>;
}

function renderFrom(state: unknown) {
  render(
    <MemoryRouter initialEntries={[{ pathname: "/customer/goal-select", state }]}>
      <Routes>
        <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
        <Route path="*" element={<BackProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Возврат с экрана цели", () => {
  beforeEach(() => {
    mockedFetch.mockResolvedValue(NEEDS_GOAL);
  });

  it("с «Плана» ведёт в план", async () => {
    renderFrom({ returnTo: "/customer/plan" });

    const back = await screen.findByRole("button", { name: /назад/i });
    back.click();

    expect(await screen.findByTestId("where")).toHaveTextContent("/customer/plan");
  });

  it("без происхождения ведёт на Главную, как и было", async () => {
    renderFrom(null);

    const back = await screen.findByRole("button", { name: /назад/i });
    back.click();

    expect(await screen.findByTestId("where")).toHaveTextContent("/customer/main");
  });
});
