/**
 * «В ориентире N» в карточке «Мой план» (DRF-2124, План-B).
 *
 * Каталог (#515) кладёт к `done_count` дневника второй факт —
 * `within_target_count`: дни ведра с суммой ≤ подтверждённого ориентира,
 * `null` без ориентира. Экран показывает его ТОЛЬКО когда он число; это
 * факт, не оценка — ни «отлично», ни ✓, ни процента (В-5).
 *
 *   - число → «1 из 3» и рядом «в ориентире 1»;
 *   - null / ключа нет → карточка как прежде, слова «ориентир» нет;
 *   - 0 — тоже факт, печатается;
 *   - у воды и брони «в ориентире» не бывает.
 */
import { act, configure, getConfig, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/plan-lite", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/plan-lite")>();
  return {
    ...original,
    getPlanLite: vi.fn(),
    getPlanLiteProposal: vi.fn(),
    createPlanLite: vi.fn(),
    closePlanLite: vi.fn(),
  };
});
vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return { ...original, fetchDecisionContext: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { ApiError } from "../lib/api";
import { fetchDecisionContext, type DecisionContext } from "../lib/customer-goals";
import { getPlanLite, getPlanLiteProposal, type PlanLite, type PlanLiteAction } from "../lib/plan-lite";
import { PLAN_LITE_ROUTE, PlanLiteScreen } from "./PlanLiteScreen";

const GUARD_ASYNC_TIMEOUT_MS = 20;
let previousAsyncUtilTimeout = 1000;
beforeAll(() => {
  previousAsyncUtilTimeout = getConfig().asyncUtilTimeout;
  configure({ asyncUtilTimeout: GUARD_ASYNC_TIMEOUT_MS });
});
afterAll(() => {
  configure({ asyncUtilTimeout: previousAsyncUtilTimeout });
  vi.unstubAllEnvs();
});

const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

const DOC: DecisionContext = {
  version: 1,
  known: { goal: { goal_key: "tone_up", goal_text: null, selected_at: "2026-09-10T10:00:00Z", source_channel: "miniapp" } },
  missing: [],
  suggestions: [{ key: "tone_up", label: "Подтянуть фигуру" }],
  intents: [],
};

const WEEK = { start: "2026-09-14", end: "2026-09-21" };
const DAY = { start: "2026-09-18", end: "2026-09-19" };

function food(within: number | null | undefined): PlanLiteAction {
  const base: PlanLiteAction = { action_type: "log_food", cadence: "per_week", target_count: 3, done_count: 1, bucket: WEEK };
  return within === undefined ? base : { ...base, within_target_count: within };
}

function plan(...actions: PlanLiteAction[]): PlanLite {
  return { plan_id: "p-2124", goal_key: "tone_up", actions };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={[PLAN_LITE_ROUTE]}>
      <Routes>
        <Route path={PLAN_LITE_ROUTE} element={<PlanLiteScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function card(p: PlanLite): Promise<HTMLElement> {
  vi.mocked(getPlanLite).mockResolvedValue(p);
  renderScreen();
  await settle();
  return screen.getByTestId("plan-lite-card");
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("VITE_PLAN_LITE", "1");
  vi.mocked(fetchDecisionContext).mockResolvedValue(DOC);
  vi.mocked(getPlanLiteProposal).mockRejectedValue(new ApiError(404, "no_template", "none"));
});

describe("в ориентире", () => {
  it("число → рядом с «N из M» стоит «в ориентире N»", async () => {
    const el = await card(plan(food(1)));
    expect(el).toHaveTextContent("1 из 3");
    expect(el).toHaveTextContent("в ориентире 1");
  });

  it("null → карточка как прежде, слова «ориентир» нет", async () => {
    const el = await card(plan(food(null)));
    expect(el).toHaveTextContent("1 из 3");
    expect(el.textContent?.toLowerCase()).not.toContain("ориентир");
  });

  it("ключа нет (старый каталог) → слова «ориентир» нет", async () => {
    const el = await card(plan(food(undefined)));
    expect(el).toHaveTextContent("1 из 3");
    expect(el.textContent?.toLowerCase()).not.toContain("ориентир");
  });

  it("0 — факт, печатается", async () => {
    const el = await card(plan(food(0)));
    expect(el).toHaveTextContent("в ориентире 0");
  });

  it("слов достижения нет даже при полном совпадении", async () => {
    const el = await card(plan({ ...food(3), done_count: 3 }));
    const low = el.textContent?.toLowerCase() ?? "";
    expect(low).toContain("3 из 3");
    expect(low).toContain("в ориентире 3");
    expect(low).not.toContain("%");
    expect(low).not.toMatch(/достиг|отлично|молодец|прогресс|пропуст|✓/);
  });

  it("у воды и брони «в ориентире» не бывает", async () => {
    const el = await card(
      plan(
        { action_type: "log_water", cadence: "per_day", target_count: 7, done_count: 4, bucket: DAY, within_target_count: 2 },
        { action_type: "book_service", cadence: "per_week", target_count: 1, done_count: 1, bucket: WEEK, within_target_count: 1 },
      ),
    );
    expect(el).toHaveTextContent("4 из 7");
    expect(el.textContent?.toLowerCase()).not.toContain("ориентир");
  });
});
