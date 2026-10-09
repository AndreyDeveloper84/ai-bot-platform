/**
 * «Мой план» — несохранённое предложение из чата и «Сохранить» с экрана
 * (DRF-2876, часть 2).
 *
 * Задание владельца (§9): «Mini App должен уметь сохранять без искусственного
 * сообщения „сохрани“ в чат». Вопрос и кнопка — слова владельца, лист решений
 * 07.10, п.15: «Сохранить выбранные шаги в мой план?» · «Сохранить»; нажатие
 * кнопки — само подтверждение.
 *
 * Что сторожится:
 *   - предложение показано шагами каталога, с вопросом владельца и кнопкой;
 *   - оно не заслоняет то, что уже на экране: прежний план остаётся;
 *   - без предложения ни вопроса, ни кнопки нет;
 *   - «Сохранить» шлёт именно показанную карточку и перечитывает сохранённое;
 *   - отказ сервера показан именем с пометкой «тест», предложение остаётся;
 *   - пока запрос идёт, кнопка недоступна — второе нажатие не шлёт второй раз;
 *   - экран не прочитался — сохранять нечего: предложения на нём нет.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { primeDisplayName } from "../components/CustomerAvatarEntry";

vi.mock("../lib/plan-engine", () => ({
  getSavedPlanState: vi.fn(),
  replacePlan: vi.fn(),
  keepCurrentPlan: vi.fn(),
  saveDraft: vi.fn(),
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
import { getSavedPlanState, saveDraft, type PlanDraft } from "../lib/plan-engine";
import { getPlanLite, getPlanLiteProposal, type PlanLite } from "../lib/plan-lite";
import { PLAN_LITE_ROUTE, PlanLiteScreen } from "./PlanLiteScreen";

const mockedState = vi.mocked(getSavedPlanState);
const mockedSave = vi.mocked(saveDraft);
const mockedLite = vi.mocked(getPlanLite);
const mockedLiteProposal = vi.mocked(getPlanLiteProposal);
const mockedDoc = vi.mocked(fetchDecisionContext);

const QUESTION = "Сохранить выбранные шаги в мой план?";

const DOC: DecisionContext = {
  version: 1,
  known: { goal: { goal_key: "tone_up", goal_text: null, selected_at: "2026-09-10T10:00:00Z", source_channel: "miniapp" } },
  missing: [],
  suggestions: [{ key: "tone_up", label: "Подтянуть фигуру" }],
  intents: [],
};

const DRAFT: PlanDraft = {
  token: "0f3a9c2e",
  steps: [{ label: "Режим сна" }, { label: "Вечерняя прогулка", why: "Даёт спокойный вечер." }],
};

const LITE: PlanLite = {
  plan_id: "p-1",
  goal_key: "tone_up",
  actions: [
    { action_type: "log_water", cadence: "per_day", target_count: 7, done_count: 4, bucket: { start: "2026-09-18", end: "2026-09-19" } },
  ],
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
  mockedLite.mockResolvedValue(LITE);
  mockedLiteProposal.mockRejectedValue(new ApiError(404, "no_template", "none"));
  mockedState.mockResolvedValue({ plan: null, proposal: null, draft: DRAFT });
  mockedSave.mockResolvedValue(undefined);
});

describe("«Мой план»: несохранённое предложение из чата", () => {
  it("показаны шаги каталога, вопрос владельца и кнопка «Сохранить»", async () => {
    renderScreen();

    const card = await screen.findByTestId("plan-draft-card");
    expect(within(card).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Режим сна",
      "Вечерняя прогулкаПочему этот шаг?",
    ]);
    expect(within(card).getByText(QUESTION)).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Сохранить" })).toBeTruthy();
    expect(mockedSave).not.toHaveBeenCalled();
  });

  it("предложение не заслоняет прежний план — он остаётся на экране", async () => {
    renderScreen();

    expect(await screen.findByTestId("plan-draft-card")).toBeTruthy();
    expect(screen.getByTestId("plan-lite-card")).toBeTruthy();
  });

  it("без предложения нет ни вопроса, ни кнопки", async () => {
    mockedState.mockResolvedValue({ plan: null, proposal: null, draft: null });
    renderScreen();

    // Положительный контроль: экран отрисован, прежний план показан.
    expect(await screen.findByTestId("plan-lite-card")).toBeTruthy();
    expect(screen.queryByTestId("plan-draft-card")).toBeNull();
    expect(screen.queryByText(QUESTION)).toBeNull();
  });

  it("«Сохранить» шлёт именно показанную карточку и перечитывает сохранённое", async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(mockedState).toHaveBeenCalledTimes(2));
    expect(mockedSave).toHaveBeenCalledTimes(1);
    expect(mockedSave).toHaveBeenCalledWith(DRAFT);
  });

  it("отказ сервера показан именем с пометкой «тест», предложение остаётся", async () => {
    mockedSave.mockRejectedValue(new ApiError(409, "plan_safety_unavailable", "no verdict"));
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText("PLAN_SAFETY_UNAVAILABLE · тест")).toBeTruthy();
    expect(screen.getByTestId("plan-draft-card")).toBeTruthy();
  });

  it("пока запрос идёт, кнопка недоступна — второе нажатие не шлёт второй раз", async () => {
    let release: () => void = () => undefined;
    mockedSave.mockReturnValue(
      new Promise<void>((resolve) => {
        release = resolve;
      }),
    );
    renderScreen();
    const button = await screen.findByRole("button", { name: "Сохранить" });

    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    fireEvent.click(button);
    expect(mockedSave).toHaveBeenCalledTimes(1);

    release();
    await waitFor(() => expect(mockedState).toHaveBeenCalledTimes(2));
  });

  it("экран не прочитался — предложения на нём нет", async () => {
    mockedState.mockRejectedValue(new ApiError(502, "ayla_unavailable", "down"));
    renderScreen();

    // Положительный контроль: экран в состоянии «не получилось», а не пустой.
    expect(await screen.findByRole("button", { name: "Повторить" })).toBeTruthy();
    expect(screen.queryByTestId("plan-draft-card")).toBeNull();
  });
});
