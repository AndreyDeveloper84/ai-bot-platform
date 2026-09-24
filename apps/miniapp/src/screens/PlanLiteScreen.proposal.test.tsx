/**
 * «Мой план» — предложение из шаблона цели (DRF-2123, План-A).
 *
 * Что сторожится:
 *   - плана нет → блок «Ayla предлагает для цели «{метка}»» + «почему» (why)
 *     + строки действий с числами из предложения; ничего о результате
 *     (В-5: ни «%», ни «процент», ни «достигнут»);
 *   - снятый пункт в POST не уходит; «Подтвердить план» шлёт actions +
 *     template_version; после POST — карточка;
 *   - «Собрать самому» → прежний конструктор (три обязательства);
 *   - `no_template` → прежний конструктор без блока и без ошибки;
 *   - `no_active_goal` → «сначала выбери цель» и кнопка на экран цели;
 *   - строка «дневник» без согласия дневника → пометка «нужно согласие», в
 *     POST не уходит; пометка ведёт на тот же гейт, что у сканера;
 *   - каденс per_2_weeks: подпись «раз в 2 недели» в предложении, ведро
 *     «2 недели» в карточке.
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
import { fetchDiaryConsentGate } from "../lib/food-scanner";
import {
  createPlanLite,
  getPlanLite,
  getPlanLiteProposal,
  type PlanLite,
  type PlanLiteProposal,
} from "../lib/plan-lite";
import { PLAN_LITE_COPY, PLAN_LITE_ROUTE, PlanLiteScreen } from "./PlanLiteScreen";

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

const settle = async (rounds = 5) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

const mockedGet = vi.mocked(getPlanLite);
const mockedProposal = vi.mocked(getPlanLiteProposal);
const mockedCreate = vi.mocked(createPlanLite);
const mockedDoc = vi.mocked(fetchDecisionContext);
const mockedConsent = vi.mocked(fetchDiaryConsentGate);

const DOC: DecisionContext = {
  version: 1,
  known: { goal: { goal_key: "self_care", goal_text: null, selected_at: "2026-09-10T10:00:00Z", source_channel: "miniapp" } },
  missing: [],
  suggestions: [{ key: "self_care", label: "Забота о себе" }],
  intents: [],
};

const WHY = "Забота о себе — это регулярность, а не подвиг.";

const PROPOSAL: PlanLiteProposal = {
  goal_key: "self_care",
  why: WHY,
  template_version: 2,
  actions: [
    { action_type: "book_service", cadence: "per_2_weeks", target_count: 1 },
    { action_type: "log_food", cadence: "per_week", target_count: 3 },
    { action_type: "log_water", cadence: "per_day", target_count: 6 },
  ],
};

const PLAN: PlanLite = {
  plan_id: "p-2123",
  goal_key: "self_care",
  actions: [
    { action_type: "book_service", cadence: "per_2_weeks", target_count: 1, done_count: 0, bucket: { start: "2026-09-19", end: "2026-10-03" } },
    { action_type: "log_water", cadence: "per_day", target_count: 6, done_count: 2, bucket: { start: "2026-09-19", end: "2026-09-20" } },
  ],
};

function LocationProbe() {
  const location = useLocation();
  const state = location.state as { returnTo?: string } | null;
  return (
    <div>
      <div data-testid="location">{location.pathname}</div>
      <div data-testid="return-to">{state?.returnTo ?? ""}</div>
    </div>
  );
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

const proposalBlock = () => screen.getByTestId("plan-lite-proposal");

beforeEach(() => {
  vi.clearAllMocks();
  mockedDoc.mockResolvedValue(DOC);
  mockedGet.mockResolvedValue(null);
  mockedProposal.mockResolvedValue(PROPOSAL);
  mockedCreate.mockResolvedValue(PLAN);
  mockedConsent.mockResolvedValue({ canonical: false, grantedAt: "2026-09-01T10:00:00Z", currentDocumentVersion: "v1" });
});

describe("предложение", () => {
  it("плана нет → блок с меткой цели, «почему» и три строки с числами из предложения; без слов результата", async () => {
    renderScreen();
    await settle();

    const block = proposalBlock();
    expect(block).toHaveTextContent(PLAN_LITE_COPY.proposalTitle("Забота о себе"));
    expect(block).toHaveTextContent(WHY);
    expect(block).toHaveTextContent(PLAN_LITE_COPY.labelBook);
    expect(block).toHaveTextContent(PLAN_LITE_COPY.labelFood);
    expect(block).toHaveTextContent(PLAN_LITE_COPY.labelWater);
    // Числа — из предложения, а не из умолчаний конструктора (3 дн. / 7 раз).
    expect(within(block).getByRole("group", { name: PLAN_LITE_COPY.labelBook })).toHaveTextContent("1");
    expect(within(block).getByRole("group", { name: PLAN_LITE_COPY.labelFood })).toHaveTextContent("3");
    expect(within(block).getByRole("group", { name: PLAN_LITE_COPY.labelWater })).toHaveTextContent("6");
    // Каденс per_2_weeks подписан словами.
    expect(block).toHaveTextContent("раз в 2 недели");
    // Конструктора рядом нет: чекбоксы — только строки предложения.
    expect(screen.queryByText(PLAN_LITE_COPY.builderTitle)).toBeNull();
    // В-5: текст есть (проверено выше), и в нём нет результата.
    const low = block.textContent?.toLowerCase() ?? "";
    expect(low).toContain("ayla предлагает");
    expect(low).not.toContain("%");
    expect(low).not.toMatch(/процент|достигнут|прогресс|пропуст/);
  });

  it("«Подтвердить план» шлёт actions из предложения + template_version; после — карточка", async () => {
    renderScreen();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.confirm }));
    await settle();

    expect(mockedCreate).toHaveBeenCalledTimes(1);
    expect(mockedCreate.mock.calls[0]).toEqual([
      [
        { action_type: "book_service", cadence: "per_2_weeks", target_count: 1 },
        { action_type: "log_food", cadence: "per_week", target_count: 3 },
        { action_type: "log_water", cadence: "per_day", target_count: 6 },
      ],
      2,
    ]);
    expect(screen.getByTestId("plan-lite-card")).toBeInTheDocument();
  });

  it("снятый пункт в POST не уходит; степпер меняет число в POST", async () => {
    renderScreen();
    await settle();

    const block = proposalBlock();
    fireEvent.click(within(block).getByRole("checkbox", { name: PLAN_LITE_COPY.labelWater }));
    fireEvent.click(within(block).getByRole("button", { name: `${PLAN_LITE_COPY.labelFood}: ${PLAN_LITE_COPY.more}` }));
    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.confirm }));
    await settle();

    expect(mockedCreate).toHaveBeenCalledTimes(1);
    const [actions, version] = mockedCreate.mock.calls[0] ?? [];
    expect(actions).toEqual([
      { action_type: "book_service", cadence: "per_2_weeks", target_count: 1 },
      { action_type: "log_food", cadence: "per_week", target_count: 4 },
    ]);
    expect(actions?.map((a) => a.action_type)).not.toContain("log_water");
    expect(version).toBe(2);
  });

  it("все пункты сняты → «Подтвердить» не активна и POST нет", async () => {
    renderScreen();
    await settle();

    const block = proposalBlock();
    fireEvent.click(within(block).getByRole("checkbox", { name: PLAN_LITE_COPY.labelBook }));
    fireEvent.click(within(block).getByRole("checkbox", { name: PLAN_LITE_COPY.labelFood }));
    fireEvent.click(within(block).getByRole("checkbox", { name: PLAN_LITE_COPY.labelWater }));

    expect(screen.getByRole("button", { name: PLAN_LITE_COPY.confirm })).toBeDisabled();
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("«Собрать самому» → прежний конструктор из трёх обязательств", async () => {
    renderScreen();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.byHand }));

    expect(screen.queryByTestId("plan-lite-proposal")).toBeNull();
    expect(screen.getByText(PLAN_LITE_COPY.builderTitle)).toBeInTheDocument();
    expect(screen.getByText(PLAN_LITE_COPY.chipBook)).toBeInTheDocument();
    expect(screen.getByText(PLAN_LITE_COPY.chipFood(3))).toBeInTheDocument();
    expect(screen.getByText(PLAN_LITE_COPY.chipWater(7))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: PLAN_LITE_COPY.compose })).toBeDisabled();
  });

  it("no_template → прежний конструктор без блока и без ошибки", async () => {
    mockedProposal.mockRejectedValue(new ApiError(404, "no_template", "none"));
    renderScreen();
    await settle();

    expect(mockedProposal).toHaveBeenCalledTimes(1);
    expect(screen.getByText(PLAN_LITE_COPY.builderTitle)).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
    expect(screen.queryByTestId("plan-lite-proposal")).toBeNull();
    expect(screen.queryByText(PLAN_LITE_COPY.transient)).toBeNull();
  });

  it("no_active_goal → «сначала выбери цель», кнопка ведёт на экран цели", async () => {
    mockedProposal.mockRejectedValue(new ApiError(404, "no_active_goal", "none"));
    renderScreen();
    await settle();

    expect(screen.getByText(PLAN_LITE_COPY.needGoal)).toBeInTheDocument();
    expect(screen.queryByTestId("plan-lite-proposal")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.chooseGoal }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/goal-select");
  });

  it("план есть → карточка, предложение не спрашивается", async () => {
    mockedGet.mockResolvedValue(PLAN);
    renderScreen();
    await settle();

    expect(screen.getByTestId("plan-lite-card")).toBeInTheDocument();
    expect(mockedProposal).not.toHaveBeenCalled();
  });
});

describe("дневник без согласия", () => {
  beforeEach(() => {
    mockedConsent.mockResolvedValue({ canonical: false, grantedAt: null, currentDocumentVersion: "v1" });
  });

  it("строка «дневник» помечена «нужно согласие», снята и в POST не уходит", async () => {
    renderScreen();
    await settle();

    const block = proposalBlock();
    expect(block).toHaveTextContent(PLAN_LITE_COPY.labelFood);
    expect(block).toHaveTextContent(PLAN_LITE_COPY.needConsent);
    expect(within(block).getByRole("checkbox", { name: PLAN_LITE_COPY.labelFood })).not.toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.confirm }));
    await settle();

    expect(mockedCreate).toHaveBeenCalledTimes(1);
    const [actions, version] = mockedCreate.mock.calls[0] ?? [];
    expect(actions).toEqual([
      { action_type: "book_service", cadence: "per_2_weeks", target_count: 1 },
      { action_type: "log_water", cadence: "per_day", target_count: 6 },
    ]);
    expect(actions?.map((a) => a.action_type)).not.toContain("log_food");
    expect(version).toBe(2);
  });

  it("пометка ведёт на гейт согласия сканера с возвратом сюда", async () => {
    renderScreen();
    await settle();

    fireEvent.click(within(proposalBlock()).getByRole("button", { name: PLAN_LITE_COPY.needConsent }));

    expect(screen.getByTestId("location")).toHaveTextContent("/customer/food-scanner/capture");
    expect(screen.getByTestId("return-to")).toHaveTextContent(PLAN_LITE_ROUTE);
  });

  it("гейт согласия не ответил → строка остаётся у человека (DRF-2354)", async () => {
    // Этот узел держал прежнее поведение: сбой гейта приравнивался к «нет»,
    // и строка дневника молча выпадала из плана. Решение §77 (23.09) его
    // меняет: «не знаю» — не «нет». Человек видит строку включённой и
    // решает сам; если согласия действительно нет, откажет каталог — это
    // его ответ, а не наша догадка за человека.
    mockedConsent.mockRejectedValue(new ApiError(502, "ayla_unavailable", "down"));
    renderScreen();
    await settle();

    // Подсказка «Нужно согласие» — только на явное «нет», а его не было.
    expect(proposalBlock()).not.toHaveTextContent(PLAN_LITE_COPY.needConsent);
    fireEvent.click(screen.getByRole("button", { name: PLAN_LITE_COPY.confirm }));
    await settle();

    expect(mockedCreate).toHaveBeenCalledTimes(1);
    const actions = mockedCreate.mock.calls[0]?.[0] ?? [];
    expect(actions.map((a) => a.action_type)).toContain("log_food");
  });

  it("предложение без строки «дневник» → гейт согласия не спрашивается", async () => {
    mockedProposal.mockResolvedValue({ ...PROPOSAL, actions: PROPOSAL.actions.filter((a) => a.action_type !== "log_food") });
    renderScreen();
    await settle();

    expect(proposalBlock()).toHaveTextContent(PLAN_LITE_COPY.labelWater);
    expect(mockedConsent).not.toHaveBeenCalled();
  });
});

describe("карточка с per_2_weeks", () => {
  it("ведро подписано «2 недели», «N из M» как прежде, без слов результата", async () => {
    mockedGet.mockResolvedValue(PLAN);
    renderScreen();
    await settle();

    const card = screen.getByTestId("plan-lite-card");
    expect(card).toHaveTextContent("0 из 1");
    expect(card).toHaveTextContent("2 недели");
    expect(card).toHaveTextContent("2 из 6");
    const low = card.textContent?.toLowerCase() ?? "";
    expect(low).toContain("из");
    expect(low).not.toContain("%");
    expect(low).not.toMatch(/процент|достигнут|прогресс|пропуст/);
  });
});
