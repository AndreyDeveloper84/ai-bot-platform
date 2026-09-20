/**
 * «Мой план» — Plan Lite без веса (DRF-2101, §49).
 *
 * Что сторожится:
 *   - плана нет → конструктор из трёх обязательств; выбранные 1–3 уходят
 *     POST'ом ТОЛЬКО как actions (goal_id не шлётся — PR-1b);
 *   - карточка — «N из M» по каждому обязательству и ничего о результате
 *     (В-5: ни процента, ни «достигнута», ни «пропустил»);
 *   - «Изменить план» — DELETE, затем конструктор;
 *   - отказы по слагам: not_found на POST (нет активной цели) → экран
 *     цели; already_active → перечитать карточку; plan_lite_disabled →
 *     «недоступно»; ayla_unavailable → фраза + «Повторить»;
 *   - флага сборки нет (DRF-2144): «недоступно» говорит только сервер.
 */
import { act, configure, fireEvent, getConfig, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
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
import { closePlanLite, createPlanLite, getPlanLite, getPlanLiteProposal, type PlanLite } from "../lib/plan-lite";
import { PLAN_LITE_COPY, PLAN_LITE_ROUTE, PlanLiteScreen } from "./PlanLiteScreen";

const GUARD_ASYNC_TIMEOUT_MS = 20;
let previousAsyncUtilTimeout = 1000;
beforeAll(() => {
  previousAsyncUtilTimeout = getConfig().asyncUtilTimeout;
  configure({ asyncUtilTimeout: GUARD_ASYNC_TIMEOUT_MS });
});
afterAll(() => {
  configure({ asyncUtilTimeout: previousAsyncUtilTimeout });
});

const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

const mockedGet = vi.mocked(getPlanLite);
const mockedProposal = vi.mocked(getPlanLiteProposal);
const mockedCreate = vi.mocked(createPlanLite);
const mockedClose = vi.mocked(closePlanLite);
const mockedDoc = vi.mocked(fetchDecisionContext);

const DOC: DecisionContext = {
  version: 1,
  known: { goal: { goal_key: "tone_up", goal_text: null, selected_at: "2026-09-10T10:00:00Z", source_channel: "miniapp" } },
  missing: [],
  suggestions: [{ key: "tone_up", label: "Подтянуть фигуру" }],
  intents: [],
};

const PLAN: PlanLite = {
  plan_id: "p-1",
  goal_key: "tone_up",
  actions: [
    { action_type: "book_service", cadence: "per_week", target_count: 1, done_count: 1, bucket: { start: "2026-09-14", end: "2026-09-21" } },
    { action_type: "log_food", cadence: "per_week", target_count: 3, done_count: 1, bucket: { start: "2026-09-14", end: "2026-09-21" } },
    { action_type: "log_water", cadence: "per_day", target_count: 7, done_count: 4, bucket: { start: "2026-09-18", end: "2026-09-19" } },
  ],
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={[PLAN_LITE_ROUTE]}>
      <Routes>
        <Route path={PLAN_LITE_ROUTE} element={<PlanLiteScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedDoc.mockResolvedValue(DOC);
  mockedGet.mockResolvedValue(null);
  // Здесь — прежний путь без шаблона (DRF-2123: no_template → конструктор);
  // предложение из шаблона сторожит PlanLiteScreen.proposal.test.tsx.
  mockedProposal.mockRejectedValue(new ApiError(404, "no_template", "none"));
  mockedCreate.mockResolvedValue(PLAN);
  mockedClose.mockResolvedValue(undefined);
});


describe("конструктор", () => {
  it("плана нет → три обязательства, ничего не выбрано, «Составить» не активна", async () => {
    renderScreen();
    await settle();

    const chips = screen.getAllByRole("checkbox");
    expect(chips).toHaveLength(3);
    expect(screen.getByText(PLAN_LITE_COPY.chipBook)).toBeInTheDocument();
    expect(screen.getByText(PLAN_LITE_COPY.chipFood(3))).toBeInTheDocument();
    expect(screen.getByText(PLAN_LITE_COPY.chipWater(7))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: PLAN_LITE_COPY.compose })).toBeDisabled();
  });

  it("выбранные уходят POST'ом только как actions (goal_id не шлётся)", async () => {
    renderScreen();
    await settle();

    fireEvent.click(screen.getByLabelText(PLAN_LITE_COPY.chipBook));
    fireEvent.click(screen.getByLabelText(PLAN_LITE_COPY.chipFood(3)));
    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.compose }));
    await settle();

    expect(mockedCreate).toHaveBeenCalledTimes(1);
    expect(mockedCreate.mock.calls[0]?.[0]).toEqual([
      { action_type: "book_service", cadence: "per_week", target_count: 1 },
      { action_type: "log_food", cadence: "per_week", target_count: 3 },
    ]);
    // После POST — карточка из ответа.
    expect(screen.getByTestId("plan-lite-card")).toBeInTheDocument();
  });

  it("нет активной цели (404 на POST) → «сначала выбери цель» → экран цели", async () => {
    mockedCreate.mockRejectedValue(new ApiError(404, "not_found", "no active goal"));
    renderScreen();
    await settle();

    fireEvent.click(screen.getByLabelText(PLAN_LITE_COPY.chipBook));
    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.compose }));
    await settle();

    expect(screen.getByTestId("location")).toHaveTextContent("/customer/goal-select");
  });

  it("already_active на POST → перечитать: карточка с сервера", async () => {
    mockedCreate.mockRejectedValue(new ApiError(409, "already_active", "exists"));
    mockedGet.mockResolvedValueOnce(null).mockResolvedValueOnce(PLAN);
    renderScreen();
    await settle();

    fireEvent.click(screen.getByLabelText(PLAN_LITE_COPY.chipBook));
    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.compose }));
    await settle();

    expect(mockedGet).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("plan-lite-card")).toBeInTheDocument();
  });
});

describe("карточка", () => {
  it("«N из M» по каждому обязательству, метка цели из decision-context, без слов результата", async () => {
    mockedGet.mockResolvedValue(PLAN);
    renderScreen();
    await settle();

    const card = screen.getByTestId("plan-lite-card");
    expect(card).toHaveTextContent("Подтянуть фигуру");
    expect(card).toHaveTextContent("Записаться на услугу");
    expect(card).toHaveTextContent("1 из 1");
    expect(card).toHaveTextContent("Дневник");
    expect(card).toHaveTextContent("1 из 3");
    expect(card).toHaveTextContent("Вода");
    expect(card).toHaveTextContent("4 из 7");
    const low = card.textContent?.toLowerCase() ?? "";
    expect(low).toContain("из");
    expect(low).not.toContain("%");
    expect(low).not.toMatch(/достиг|пропуст|прогресс/);
  });

  it("каждое обязательство ведёт туда, где оно делается", async () => {
    mockedGet.mockResolvedValue(PLAN);
    renderScreen();
    await settle();

    const card = screen.getByTestId("plan-lite-card");
    fireEvent.click(within(card).getByRole("button", { name: `${PLAN_LITE_COPY.go}: Дневник` }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/food-scanner/diary");
  });

  it("«Изменить план» — DELETE, затем конструктор", async () => {
    mockedGet.mockResolvedValue(PLAN);
    renderScreen();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.change }));
    await settle();

    expect(mockedClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("plan-lite-card")).toBeNull();
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
  });
});

describe("отказы и флаг", () => {
  it("plan_lite_disabled от сервера → «недоступно», конструктора нет", async () => {
    mockedGet.mockRejectedValue(new ApiError(404, "plan_lite_disabled", "off"));
    renderScreen();
    await settle();

    expect(screen.getByText(PLAN_LITE_COPY.unavailable)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("ayla_unavailable → фраза и «Повторить», который повторяет запрос", async () => {
    mockedGet.mockRejectedValueOnce(new ApiError(502, "ayla_unavailable", "down"));
    renderScreen();
    await settle();

    expect(screen.getByText(PLAN_LITE_COPY.transient)).toBeInTheDocument();
    mockedGet.mockResolvedValueOnce(PLAN);
    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.retry }));
    await settle();

    expect(mockedGet).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("plan-lite-card")).toBeInTheDocument();
  });

  it("флаг сборки VITE_PLAN_LITE ничего не решает — план читается с сервера (DRF-2144)", async () => {
    vi.stubEnv("VITE_PLAN_LITE", "");
    renderScreen();
    await settle();

    expect(mockedGet).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(PLAN_LITE_COPY.unavailable)).toBeNull();
    vi.unstubAllEnvs();
  });
});
