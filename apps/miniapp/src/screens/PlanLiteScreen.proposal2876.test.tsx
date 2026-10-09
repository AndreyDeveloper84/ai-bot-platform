/**
 * «Мой план» — предложение рядом с действующим планом и замена (DRF-2876).
 *
 * Решение владельца: сохранённый новый план не вытесняет действующий без
 * подтверждения замены. Слова вопроса и кнопок — лист решений 07.10, п.15;
 * нажатие кнопки — само подтверждение, второго «Ты уверен?» нет.
 *
 * Что сторожится:
 *   - предложение показано рядом с действующим планом: его шаги, вопрос
 *     владельца дословно и две кнопки; действующий план при этом на месте;
 *   - без предложения ни вопроса, ни кнопок нет;
 *   - «Заменить план» шлёт замену именно этого предложения и названного
 *     плана, затем экран перечитывает сохранённое;
 *   - «Оставить текущий» шлёт отказ и замену не шлёт;
 *   - отказ сервера показан именем с пометкой «тест», предложение остаётся;
 *   - пока запрос идёт, кнопки недоступны — двойное нажатие не шлёт дважды.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { primeDisplayName } from "../components/CustomerAvatarEntry";

vi.mock("../lib/plan-engine", () => ({
  getSavedPlanState: vi.fn(),
  replacePlan: vi.fn(),
  keepCurrentPlan: vi.fn(),
}));
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
vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return { ...original, fetchDiaryConsentGate: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { ApiError } from "../lib/api";
import { fetchDecisionContext, type DecisionContext } from "../lib/customer-goals";
import {
  getSavedPlanState,
  keepCurrentPlan,
  replacePlan,
  type PlanProposal,
  type SavedPlan,
} from "../lib/plan-engine";
import { PLAN_LITE_ROUTE, PlanLiteScreen } from "./PlanLiteScreen";

const mockedState = vi.mocked(getSavedPlanState);
const mockedReplace = vi.mocked(replacePlan);
const mockedKeep = vi.mocked(keepCurrentPlan);
const mockedDoc = vi.mocked(fetchDecisionContext);

const QUESTION = "Заменить текущий план новым? Прежний останется в истории";

const DOC: DecisionContext = {
  version: 1,
  known: { goal: { goal_key: "tone_up", goal_text: null, selected_at: "2026-09-10T10:00:00Z", source_channel: "miniapp" } },
  missing: [],
  suggestions: [{ key: "tone_up", label: "Подтянуть фигуру" }],
  intents: [],
};

const ACTIVE: SavedPlan = {
  plan_id: "5a5a5a5a-1111-4222-8333-999999999999",
  steps: [{ step_id: "a-0", label: "Режим сна" }],
};

const PROPOSAL: PlanProposal = {
  plan_id: "7c1d2e3f-aaaa-4bbb-8ccc-ddddeeeeffff",
  replaces_plan_id: ACTIVE.plan_id,
  steps: [{ step_id: "p-0", label: "Вечерняя прогулка" }],
};

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={[PLAN_LITE_ROUTE]}>
      <Routes>
        <Route path={PLAN_LITE_ROUTE} element={<PlanLiteScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  primeDisplayName("Тест Тестов");
  mockedDoc.mockResolvedValue(DOC);
  mockedState.mockResolvedValue({ plan: ACTIVE, proposal: PROPOSAL });
  mockedReplace.mockResolvedValue(true);
  mockedKeep.mockResolvedValue(undefined);
});

describe("«Мой план»: предложение рядом с действующим планом", () => {
  it("показаны шаги предложения, вопрос владельца и две кнопки; действующий план на месте", async () => {
    renderScreen();

    const proposal = await screen.findByTestId("plan-proposal-card");
    expect(within(proposal).getByText("Вечерняя прогулка")).toBeTruthy();
    expect(within(proposal).getByText(QUESTION)).toBeTruthy();
    expect(within(proposal).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Заменить план",
      "Оставить текущий",
    ]);
    expect(within(screen.getByTestId("plan-saved-card")).getByText("Режим сна")).toBeTruthy();
    expect(mockedReplace).not.toHaveBeenCalled();
  });

  it("без предложения нет ни вопроса, ни кнопок", async () => {
    mockedState.mockResolvedValue({ plan: ACTIVE, proposal: null });
    renderScreen();

    // Положительный контроль: действующий план показан.
    expect(await screen.findByTestId("plan-saved-card")).toBeTruthy();
    expect(screen.queryByTestId("plan-proposal-card")).toBeNull();
    expect(screen.queryByText(QUESTION)).toBeNull();
  });

  it("«Заменить план» шлёт замену этого предложения и перечитывает сохранённое", async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Заменить план" }));

    await waitFor(() => expect(mockedState).toHaveBeenCalledTimes(2));
    expect(mockedReplace).toHaveBeenCalledTimes(1);
    expect(mockedReplace).toHaveBeenCalledWith(PROPOSAL);
    expect(mockedKeep).not.toHaveBeenCalled();
  });

  it("«Оставить текущий» шлёт отказ и замену не шлёт", async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Оставить текущий" }));

    await waitFor(() => expect(mockedState).toHaveBeenCalledTimes(2));
    expect(mockedKeep).toHaveBeenCalledWith(PROPOSAL);
    expect(mockedReplace).not.toHaveBeenCalled();
  });

  it("отказ сервера показан именем с пометкой «тест», предложение остаётся", async () => {
    mockedReplace.mockRejectedValue(new ApiError(409, "plan_safety_unavailable", "no verdict"));
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Заменить план" }));

    expect(await screen.findByText("PLAN_SAFETY_UNAVAILABLE · тест")).toBeTruthy();
    expect(screen.getByTestId("plan-proposal-card")).toBeTruthy();
  });

  it("пока запрос идёт, кнопки недоступны — второе нажатие не шлёт вторую замену", async () => {
    let release: (value: boolean) => void = () => undefined;
    mockedReplace.mockReturnValue(
      new Promise<boolean>((resolve) => {
        release = resolve;
      }),
    );
    renderScreen();
    const yes = await screen.findByRole("button", { name: "Заменить план" });

    fireEvent.click(yes);
    await waitFor(() => expect(yes).toBeDisabled());
    fireEvent.click(yes);
    expect(mockedReplace).toHaveBeenCalledTimes(1);

    release(true);
    await waitFor(() => expect(mockedState).toHaveBeenCalledTimes(2));
  });
});
