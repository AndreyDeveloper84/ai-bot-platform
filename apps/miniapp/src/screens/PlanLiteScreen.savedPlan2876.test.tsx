/**
 * «Мой план» — сохранённый план нового механизма (DRF-2876).
 *
 * Решение владельца (лист 07.10, п.9–10): раздел один; сохранённый новый
 * план — основной, прежний показывается, пока нового нет; у шага — подпись
 * каталога, технических ключей человеку не показываем.
 *
 * Что сторожится:
 *   - сохранённый план есть → показаны подписи его шагов по порядку, прежний
 *     план не запрашивается и не рисуется;
 *   - сохранённого нет → прежний путь без изменений;
 *   - в карточке нет счётчиков «N из M» и номеров шагов;
 *   - план не прочитался → «не получилось» с повтором, а не прежний план;
 *   - «Повторить» перечитывает и показывает сохранённый план.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { primeDisplayName } from "../components/CustomerAvatarEntry";

vi.mock("../lib/plan-engine", () => {
  // Экран читает план и предложение вместе; узлы этого файла — о плане,
  // предложения в них нет (его сторожит PlanLiteScreen.proposal2876.test.tsx).
  const getSavedPlan = vi.fn();
  return {
    getSavedPlan,
    getSavedPlanState: async () => ({ plan: await getSavedPlan(), proposal: null, draft: null }),
  };
});
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
import { getSavedPlan, type SavedPlan } from "../lib/plan-engine";
import { getPlanLite, getPlanLiteProposal, type PlanLite } from "../lib/plan-lite";
import { PLAN_LITE_COPY, PLAN_LITE_ROUTE, PlanLiteScreen } from "./PlanLiteScreen";

const mockedSaved = vi.mocked(getSavedPlan);
const mockedLite = vi.mocked(getPlanLite);
const mockedProposal = vi.mocked(getPlanLiteProposal);
const mockedDoc = vi.mocked(fetchDecisionContext);

const DOC: DecisionContext = {
  version: 1,
  known: { goal: { goal_key: "tone_up", goal_text: null, selected_at: "2026-09-10T10:00:00Z", source_channel: "miniapp" } },
  missing: [],
  suggestions: [{ key: "tone_up", label: "Подтянуть фигуру" }],
  intents: [],
};

const SAVED: SavedPlan = {
  plan_id: "plan-2876",
  steps: [
    { step_id: "s-0", label: "Режим сна" },
    { step_id: "s-1", label: "Вечерняя прогулка" },
  ],
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
  mockedProposal.mockRejectedValue(new ApiError(404, "no_template", "none"));
});

describe("«Мой план»: сохранённый план нового механизма", () => {
  it("сохранённый план — основной: подписи шагов по порядку, прежний план не спрашивают", async () => {
    mockedSaved.mockResolvedValue(SAVED);
    renderScreen();

    const card = await screen.findByTestId("plan-saved-card");
    const items = within(card).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual(["Режим сна", "Вечерняя прогулка"]);
    expect(mockedLite).not.toHaveBeenCalled();
    expect(screen.queryByTestId("plan-lite-card")).toBeNull();
  });

  it("сохранённого плана нет — прежний план, как раньше", async () => {
    mockedSaved.mockResolvedValue(null);
    renderScreen();

    expect(await screen.findByTestId("plan-lite-card")).toBeTruthy();
    expect(screen.queryByTestId("plan-saved-card")).toBeNull();
  });

  it("в карточке нового плана нет счётчиков и номеров", async () => {
    mockedSaved.mockResolvedValue(SAVED);
    renderScreen();

    const card = await screen.findByTestId("plan-saved-card");
    // Положительный контроль: карточка с шагами, а не пустая.
    expect(within(card).getAllByRole("listitem")).toHaveLength(2);
    expect(card.textContent).not.toMatch(/\d/);
    // У шагов без текста «зачем» нет ни одной кнопки.
    expect(within(card).queryByRole("button")).toBeNull();
  });

  it("план не прочитался — «не получилось», а не прежний план", async () => {
    mockedSaved.mockRejectedValue(new ApiError(502, "plan_step_unlabelled", "no label"));
    renderScreen();

    expect(await screen.findByText(PLAN_LITE_COPY.transient)).toBeTruthy();
    expect(mockedLite).not.toHaveBeenCalled();
    expect(screen.queryByTestId("plan-lite-card")).toBeNull();
  });

  it("«Повторить» перечитывает и показывает сохранённый план", async () => {
    mockedSaved.mockRejectedValueOnce(new ApiError(502, "ayla_unavailable", "down"));
    mockedSaved.mockResolvedValue(SAVED);
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: PLAN_LITE_COPY.retry }));

    expect(await screen.findByTestId("plan-saved-card")).toBeTruthy();
    expect(mockedSaved).toHaveBeenCalledTimes(2);
  });

  it("«Почему этот шаг?» раскрывает слова каталога — и только у шага, где они есть", async () => {
    const why = "Помогает ложиться и вставать в одно время.";
    mockedSaved.mockResolvedValue({
      plan_id: "plan-2876",
      steps: [
        { step_id: "s-0", label: "Режим сна", why },
        { step_id: "s-1", label: "Вечерняя прогулка", why: null },
      ],
    });
    renderScreen();

    const card = await screen.findByTestId("plan-saved-card");
    const [first, second] = within(card).getAllByRole("listitem");
    if (!first || !second) throw new Error("в карточке должно быть два шага");
    const link = within(first).getByRole("button", { name: PLAN_LITE_COPY.whyThisStep });
    expect(link.getAttribute("aria-expanded")).toBe("false");
    expect(within(first).queryByText(why)).toBeNull();

    fireEvent.click(link);
    expect(within(first).getByText(why)).toBeTruthy();
    expect(link.getAttribute("aria-expanded")).toBe("true");
    // У шага без текста ссылки нет вовсе.
    expect(within(second).queryByRole("button")).toBeNull();

    fireEvent.click(link);
    expect(within(first).queryByText(why)).toBeNull();
  });

  it("пустой текст «зачем» — как его отсутствие: ссылки нет", async () => {
    mockedSaved.mockResolvedValue({
      plan_id: "plan-2876",
      steps: [
        { step_id: "s-0", label: "Режим сна", why: "   " },
        { step_id: "s-1", label: "Вечерняя прогулка" },
      ],
    });
    renderScreen();

    const card = await screen.findByTestId("plan-saved-card");
    // Положительный контроль: шаги на месте.
    expect(within(card).getAllByRole("listitem")).toHaveLength(2);
    expect(within(card).queryByRole("button")).toBeNull();
  });
});
