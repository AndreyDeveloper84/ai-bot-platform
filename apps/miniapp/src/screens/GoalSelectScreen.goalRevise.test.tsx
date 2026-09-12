/**
 * «Твоя цель: … · Изменить» (DRF-1758, макет C02.1).
 *
 * Кнопка у цели есть ровно когда сервер прислал намерение `start_anketa`
 * (экран решения не принимает), и уходит этим намерением. Без намерения
 * — цели без действия (положительная стража), а «Изменить» у ответов
 * анкеты не затронуто.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return { ...original, fetchDecisionContext: vi.fn(), postGoalSelect: vi.fn() };
});

import { ALREADY_NOTED_TITLE } from "../components/AlreadyNoted";
import {
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
  type KnownAnketaAnswer,
} from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);

const GOAL = {
  goal_key: "relax",
  goal_text: null,
  selected_at: "2026-09-03T10:00:00Z",
  source_channel: "miniapp" as const,
};
const AREA: KnownAnketaAnswer = {
  step: "area",
  prompt: "Что сейчас хочется привести в порядок?",
  option_key: "face",
  label: "Лицо и кожа",
  options: [{ key: "face", label: "Лицо и кожа" }],
  revisable: true,
};

function doc(opts: { startAnketa: boolean; answers: KnownAnketaAnswer[] }): DecisionContext {
  return {
    version: 2,
    known: { goal: GOAL, anketa: opts.answers },
    missing: opts.answers.length
      ? [{ kind: "goal_anketa", prompt: "Как хочешь себя чувствовать после?", step: "feeling",
           options: [{ key: "rested", label: "Отдохнувшей" }], allow_free_text: false, progress: { is_last: true } }]
      : [],
    suggestions: [{ key: "relax", label: "Расслабиться" }],
    intents: [
      { id: "formulate_own", label: "Опиши своими словами" },
      ...(opts.startAnketa ? [{ id: "start_anketa" as const, label: "Пройти анкету заново" }] : []),
    ],
    next: { id: "browse_catalog", label: "Найти услугу" },
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

beforeEach(() => {
  vi.clearAllMocks();
});

describe("«Изменить» у цели (DRF-1758)", () => {
  it("завершённый проход: у «Текущей цели» есть «Изменить», тап уходит намерением start_anketa", async () => {
    mockedFetch.mockResolvedValue(doc({ startAnketa: true, answers: [] }));
    mockedPost.mockResolvedValue(doc({ startAnketa: false, answers: [] }));
    renderScreen();
    await screen.findByText("Текущая цель");
    await userEvent.click(screen.getByRole("button", { name: "Изменить: Расслабиться" }));
    expect(mockedPost).toHaveBeenCalledWith({ intent: "start_anketa", source_channel: "miniapp" });
  });

  it("в блоке «Уже учла» строка цели тоже получает «Изменить», когда намерение прислано", async () => {
    mockedFetch.mockResolvedValue(doc({ startAnketa: true, answers: [AREA] }));
    renderScreen();
    const block = await screen.findByRole("region", { name: ALREADY_NOTED_TITLE });
    expect(within(block).getByRole("button", { name: "Изменить: Расслабиться" })).toBeInTheDocument();
    expect(within(block).getByRole("button", { name: "Изменить: Лицо и кожа" })).toBeInTheDocument();
  });

  it("положительная стража: без намерения у цели действия нет, у ответа — есть", async () => {
    mockedFetch.mockResolvedValue(doc({ startAnketa: false, answers: [AREA] }));
    renderScreen();
    const block = await screen.findByRole("region", { name: ALREADY_NOTED_TITLE });
    expect(within(block).queryByRole("button", { name: "Изменить: Расслабиться" })).toBeNull();
    expect(within(block).getByRole("button", { name: "Изменить: Лицо и кожа" })).toBeInTheDocument();
  });
});
