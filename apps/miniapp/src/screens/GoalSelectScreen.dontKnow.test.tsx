/**
 * «Не знаю» — полноценный ответ (DRF-1747, макет C03 P12).
 *
 * Опция с ролью `escape` рисуется тихой ссылкой ОТДЕЛЬНО от вариантов и
 * в любом режиме шлёт `{option_key}` сразу — на multi она заменяет
 * отмеченное, «Продолжить» ей не нужен. Положительная стража: шаг без
 * такой опции рисуется ровно как раньше — ссылки нет.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

import { MULTI_CONTINUE_LABEL } from "../components/AnketaStepInput";
import {
  fetchDecisionContext,
  postGoalSelect,
  type AnketaOption,
  type DecisionContext,
  type MissingItem,
} from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);

const PROMPT = "Как хочешь себя чувствовать после?";
const REGULAR: AnketaOption[] = [
  { key: "rested", label: "Отдохнувшей" },
  { key: "calmer", label: "Спокойнее" },
];
const ESCAPE: AnketaOption = { key: "unknown", label: "Не знаю", role: "escape" };

function docWith(item: Partial<MissingItem>, known: DecisionContext["known"] = {
  goal: null,
}): DecisionContext {
  return {
    version: 2,
    known,
    missing: [
      {
        kind: "goal_anketa",
        prompt: PROMPT,
        step: "feeling",
        options: [...REGULAR, ESCAPE],
        allow_free_text: false,
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
  mockedPost.mockImplementation(async () => docWith({}));
});

describe("«Не знаю» отдельно от вариантов", () => {
  it("single: ссылка вне ряда фишек, тап шлёт option_key сразу", async () => {
    mockedFetch.mockResolvedValue(docWith({}));
    renderScreen();
    const link = await screen.findByTestId("anketa-escape");
    expect(link).toHaveTextContent("Не знаю");
    // В ряду вариантов «Не знаю» нет — только настоящие варианты.
    const row = screen.getByRole("group", { name: PROMPT });
    expect(within(row).queryByText("Не знаю")).toBeNull();
    expect(within(row).getAllByRole("button")).toHaveLength(REGULAR.length);
    fireEvent.click(link);
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "feeling", option_key: "unknown" },
      source_channel: "miniapp",
    });
  });

  it("multi: «Не знаю» заменяет отмеченное и не ждёт «Продолжить»", async () => {
    mockedFetch.mockResolvedValue(docWith({ mode: "multi" }));
    renderScreen();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Отдохнувшей" }));
    expect(screen.queryByRole("checkbox", { name: "Не знаю" })).toBeNull();
    // «Продолжить» на месте — но «Не знаю» его не ждёт.
    expect(screen.getByRole("button", { name: MULTI_CONTINUE_LABEL })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("anketa-escape"));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "feeling", option_key: "unknown" },
      source_channel: "miniapp",
    });
  });

  it("шаг без escape-опции рисуется как раньше — ссылки нет", async () => {
    mockedFetch.mockResolvedValue(docWith({ options: REGULAR }));
    renderScreen();
    await screen.findByRole("button", { name: "Отдохнувшей" });
    expect(screen.queryByTestId("anketa-escape")).toBeNull();
    expect(screen.queryByText("Не знаю")).toBeNull();
  });

  it("при пересмотре «Не знаю» тоже стоит отдельно и уходит с revise", async () => {
    mockedFetch.mockResolvedValue(
      docWith(
        { step: "area", prompt: "Что сейчас хочется привести в порядок?", options: [] },
        {
          goal: {
            goal_key: "relax",
            goal_text: null,
            selected_at: "2026-09-12T10:00:00Z",
            source_channel: "miniapp",
          },
          anketa: [
            {
              step: "feeling",
              prompt: PROMPT,
              option_key: "rested",
              label: "Отдохнувшей",
              options: [...REGULAR, ESCAPE],
              revisable: true,
              unknown: false,
              origin: "anketa",
            },
          ],
        },
      ),
    );
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: /Изменить/ }));
    const section = await screen.findByRole("region", { name: "Изменить ответ" });
    const row = within(section).getByRole("group", { name: PROMPT });
    expect(within(row).queryByText("Не знаю")).toBeNull();
    fireEvent.click(within(section).getByTestId("anketa-escape"));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "feeling", option_key: "unknown", revise: true },
      source_channel: "miniapp",
    });
  });
});
