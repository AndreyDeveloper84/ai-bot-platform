/**
 * Граница C03 на экране (DRF-1751, макет P23).
 *
 * Отрицательная стража: документ с шагом из запрещённого списка
 * («district», «budget»…) не рисуется как вопрос и называется в консоли.
 * Положительная стража: настоящий шаг («area») рисуется как раньше, и
 * консоль молчит — иначе стража зеленела бы и на пустом экране.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return {
    ...original,
    fetchDecisionContext: vi.fn(),
    postGoalSelect: vi.fn(),
  };
});

import {
  C03_FORBIDDEN_STEP_KEYS,
  fetchDecisionContext,
  type DecisionContext,
} from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);

function docWithStep(step: string, prompt: string): DecisionContext {
  return {
    version: 2,
    known: {
      goal: {
        goal_key: "relax",
        goal_text: null,
        selected_at: "2026-09-12T10:00:00Z",
        source_channel: "miniapp",
      },
    },
    missing: [
      {
        kind: "goal_anketa",
        prompt,
        step,
        options: [{ key: "a", label: "Вариант" }],
        allow_free_text: false,
      },
    ],
    suggestions: [],
    intents: [],
    next: null,
  };
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/goal-select"]}>
      <Routes>
        <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

const warned: string[] = [];
let warn: { mockRestore: () => void };

const boundaryWarnings = (): string[] => warned.filter((m) => m.startsWith("[c03-boundary]"));

beforeEach(() => {
  vi.clearAllMocks();
  warned.length = 0;
  warn = vi.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
    warned.push(String(args[0]));
  });
});

afterEach(() => {
  warn.mockRestore();
});

describe("граница C03", () => {
  it("настоящий шаг рисуется, консоль молчит", async () => {
    mockedFetch.mockResolvedValue(docWithStep("area", "Что сейчас хочется привести в порядок?"));
    renderScreen();
    expect(await screen.findByText("Что сейчас хочется привести в порядок?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Вариант" })).toBeInTheDocument();
    // Чужие предупреждения (React/router) не считаются — только наше.
    expect(boundaryWarnings()).toHaveLength(0);
  });

  it.each(["district", "budget", "master", "salon"])(
    "шаг «%s» вопросом не рисуется и называется в консоли",
    async (step) => {
      expect(C03_FORBIDDEN_STEP_KEYS.has(step)).toBe(true);
      mockedFetch.mockResolvedValue(docWithStep(step, "В каком районе удобнее?"));
      renderScreen();
      // Экран дорисовался (цель на месте) — а вопроса нет.
      expect(await screen.findByText(/Расслабиться|Твоя цель|цель/i)).toBeInTheDocument();
      expect(screen.queryByText("В каком районе удобнее?")).toBeNull();
      expect(screen.queryByRole("button", { name: "Вариант" })).toBeNull();
      const ours = boundaryWarnings();
      expect(ours).toHaveLength(1);
      expect(ours[0]).toContain(step);
    },
  );
});
