/**
 * Вход в «Мой план» с экрана цели (DRF-2101) — только под флагом сборки.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return { ...original, fetchDecisionContext: vi.fn(), postGoalSelect: vi.fn() };
});

import { fetchDecisionContext, type DecisionContext } from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";
import { PLAN_LITE_COPY, PLAN_LITE_ROUTE } from "./PlanLiteScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);

const WITH_GOAL: DecisionContext = {
  version: 1,
  known: {
    goal: { goal_key: "tone_up", goal_text: null, selected_at: "2026-09-10T10:00:00Z", source_channel: "miniapp" },
  },
  missing: [],
  suggestions: [{ key: "tone_up", label: "Подтянуть фигуру" }],
  intents: [{ id: "choose_suggested", label: "Выбери из вариантов" }],
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/goal-select"]}>
      <Routes>
        <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedFetch.mockResolvedValue(WITH_GOAL);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("экран цели → «Мой план»", () => {
  it("с флагом и известной целью — кнопка ведёт на экран плана", async () => {
    vi.stubEnv("VITE_PLAN_LITE", "1");
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: PLAN_LITE_COPY.entryFromGoal }));

    expect(screen.getByTestId("location")).toHaveTextContent(PLAN_LITE_ROUTE);
  });

  it("без флага кнопки нет — состав экрана прежний", async () => {
    vi.stubEnv("VITE_PLAN_LITE", "");
    renderScreen();
    await screen.findByText("Текущая цель");

    expect(screen.queryByRole("button", { name: PLAN_LITE_COPY.entryFromGoal })).toBeNull();
  });
});
