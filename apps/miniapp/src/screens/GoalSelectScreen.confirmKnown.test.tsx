/**
 * Подтверждение известного (DRF-1745, макет C03.3).
 *
 * Шаг `mode: "confirm"` с `known_value`: «Да, всё так» шлёт `{confirm: true}`
 * без выбора варианта (значение экран не пересылает); «Изменилось»
 * раскрывает обычный вопрос шага (`question`) компонентом `answer_mode`;
 * «Не знаю» — общая escape-ссылка. Положительная стража: шаг без
 * `known_value` рисуется как раньше, кнопок подтверждения нет.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return {
    ...original,
    fetchDecisionContext: vi.fn(),
    postGoalSelect: vi.fn(),
  };
});

import {
  CONFIRM_CHANGED_LABEL,
  CONFIRM_YES_LABEL,
  MULTI_CONTINUE_LABEL,
} from "../components/AnketaStepInput";
import {
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
  type MissingItem,
} from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);

const CONFIRM_PROMPT = "Раньше ты выбирала «Лицо и кожа». Всё ещё так?";
const QUESTION = "Что сейчас хочется привести в порядок?";

function docWith(item: Partial<MissingItem>): DecisionContext {
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
        prompt: CONFIRM_PROMPT,
        step: "area",
        options: [
          { key: "face", label: "Лицо и кожа" },
          { key: "hands", label: "Руки и ногти" },
          { key: "unknown", label: "Не знаю", role: "escape" },
        ],
        allow_free_text: false,
        mode: "confirm",
        answer_mode: "single",
        question: QUESTION,
        known_value: { option_key: "face", option_keys: [], text: null, label: "Лицо и кожа" },
        ...item,
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

beforeEach(() => {
  vi.clearAllMocks();
  mockedPost.mockImplementation(async () => docWith({ mode: "single", known_value: undefined }));
});

describe("подтверждение известного", () => {
  it("«Да, всё так» шлёт confirm без выбора варианта; вариантов на экране нет", async () => {
    mockedFetch.mockResolvedValue(docWith({}));
    renderScreen();
    expect(await screen.findByText(CONFIRM_PROMPT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Лицо и кожа" })).toBeNull();
    expect(screen.queryByText(QUESTION)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: CONFIRM_YES_LABEL }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "area", confirm: true },
      source_channel: "miniapp",
    });
  });

  it("«Изменилось» раскрывает обычный вопрос с вариантами; тап — обычный ответ", async () => {
    mockedFetch.mockResolvedValue(docWith({}));
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: CONFIRM_CHANGED_LABEL }));
    expect(screen.getByTestId("anketa-confirm-question")).toHaveTextContent(QUESTION);
    expect(screen.queryByRole("button", { name: CONFIRM_YES_LABEL })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Руки и ногти" }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "area", option_key: "hands" },
      source_channel: "miniapp",
    });
  });

  it("«Изменилось» на multi-шаге — компонент по answer_mode", async () => {
    mockedFetch.mockResolvedValue(
      docWith({
        answer_mode: "multi",
        known_value: { option_key: null, option_keys: ["face"], text: null, label: "Лицо и кожа" },
      }),
    );
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: CONFIRM_CHANGED_LABEL }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Руки и ногти" }));
    fireEvent.click(screen.getByRole("button", { name: MULTI_CONTINUE_LABEL }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "area", option_keys: ["hands"] },
      source_channel: "miniapp",
    });
  });

  it("«Не знаю» стоит рядом с подтверждением и отвечает сразу", async () => {
    mockedFetch.mockResolvedValue(docWith({}));
    renderScreen();
    await screen.findByRole("button", { name: CONFIRM_YES_LABEL });
    fireEvent.click(screen.getByTestId("anketa-escape"));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "area", option_key: "unknown" },
      source_channel: "miniapp",
    });
  });

  it("шаг без known_value рисуется как раньше — кнопок подтверждения нет", async () => {
    mockedFetch.mockResolvedValue(
      docWith({ mode: "single", prompt: QUESTION, known_value: undefined, question: undefined }),
    );
    renderScreen();
    expect(await screen.findByRole("button", { name: "Лицо и кожа" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: CONFIRM_YES_LABEL })).toBeNull();
    expect(screen.queryByTestId("anketa-confirm")).toBeNull();
  });
});
