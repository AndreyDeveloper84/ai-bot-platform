/**
 * Анкета цели на поверхности выбора цели (DRF-1451).
 *
 * Два обещания решения владельца 03.09.2026 запираются здесь:
 *
 *   1. Вопросы приходят с сервера, и экран НЕ принимает решений —
 *      `TestScreenDecidesNothing`. Проверяется не отсутствием кода, а
 *      подменой документа: другой документ рисует другие вопросы и
 *      другие варианты БЕЗ единой правки экрана.
 *   2. **Анкета не ворота** — `describe("не ворота")`. Путь «назвал
 *      услугу → попал к подбору» проходится, НЕ ответив ни на один
 *      вопрос. Замер прямой: ни один `postGoalSelect` не получил
 *      `answer` (поправка A-1 к BOT-001, §24, условие C-2).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
  type GoalSelectBody,
} from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);

const INTENTS: DecisionContext["intents"] = [
  { id: "choose_suggested", label: "Выбери из вариантов" },
  { id: "formulate_own", label: "Опиши своими словами" },
  { id: "need_guidance", label: "Не понимаю, чего хочу" },
];

/** Первый шаг анкеты — ровно та форма, что приходит с сервера. */
const STEP_ONE: DecisionContext = {
  version: 2,
  known: { goal: null },
  missing: [
    {
      kind: "goal_anketa",
      prompt: "Что сейчас хочется привести в порядок?",
      step: "area",
      options: [
        { key: "face", label: "Лицо и кожа" },
        { key: "hands", label: "Руки и ногти" },
      ],
      allow_free_text: false,
      progress: { index: 1, total: 3 },
    },
  ],
  suggestions: [{ key: "relax", label: "Расслабиться" }],
  intents: INTENTS,
  next: null,
};

/** Второй шаг — ДРУГИЕ вопрос, варианты и номер. Тот же экран. */
const STEP_TWO: DecisionContext = {
  ...STEP_ONE,
  missing: [
    {
      kind: "goal_anketa",
      prompt: "Как хочешь себя чувствовать после?",
      step: "feeling",
      options: [
        { key: "rested", label: "Отдохнувшей" },
        { key: "confident", label: "Увереннее" },
      ],
      allow_free_text: false,
      progress: { index: 2, total: 3 },
    },
  ],
};

/** Финальный шаг — свободный ввод открыт сервером. */
const STEP_FINAL: DecisionContext = {
  ...STEP_ONE,
  missing: [
    {
      kind: "goal_anketa",
      prompt: "Выбери цель — или напиши своими словами.",
      step: "goal",
      options: [{ key: "relax", label: "Расслабиться" }],
      allow_free_text: true,
      progress: { index: 3, total: 3 },
    },
  ],
};

/** Спрашивать нечего: цель есть, сервер назвал, куда вести дальше. */
const DONE: DecisionContext = {
  version: 2,
  known: {
    goal: {
      goal_key: null,
      goal_text: "хочу маникюр",
      selected_at: "2026-09-03T10:00:00Z",
      source_channel: "miniapp",
    },
  },
  missing: [],
  suggestions: [{ key: "relax", label: "Расслабиться" }],
  intents: INTENTS,
  next: { id: "browse_catalog", label: "Найти услугу" },
};

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/goal-select"]}>
      <Routes>
        <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
        <Route path="/customer/catalog" element={<div>ЭКРАН ПОДБОРА</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Все тела, ушедшие на сервер за тест. */
function sentBodies(): GoalSelectBody[] {
  return mockedPost.mock.calls.map(([body]) => body);
}

function answersSent(): GoalSelectBody[] {
  return sentBodies().filter((b) => "answer" in b);
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------

describe("шаг анкеты рисуется тем, что прислал сервер", () => {
  it("вопрос, номер и варианты — из документа", async () => {
    mockedFetch.mockResolvedValue(STEP_ONE);
    renderScreen();

    expect(
      await screen.findByText("Что сейчас хочется привести в порядок?"),
    ).toBeInTheDocument();
    expect(screen.getByText("Вопрос 1 из 3")).toBeInTheDocument();

    const group = screen.getByRole("group", {
      name: "Что сейчас хочется привести в порядок?",
    });
    const options = within(group).getAllByRole("button");
    expect(options.map((b) => b.textContent)).toEqual([
      "Лицо и кожа",
      "Руки и ногти",
    ]);
  });

  it("нажатие варианта уходит как ответ на ЭТОТ шаг", async () => {
    mockedFetch.mockResolvedValue(STEP_ONE);
    mockedPost.mockResolvedValue(STEP_TWO);
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: "Лицо и кожа" }));

    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "area", option_key: "face" },
      source_channel: "miniapp",
    });
  });

  it("экран не решает ничего: другой документ — другой вопрос, без правки кода", async () => {
    // Контроль каркаса. Тот же компонент, те же нажатия — но сервер
    // прислал второй шаг, и на экране второй шаг. Ни списка вопросов,
    // ни порядка, ни «какой следующий» на клиенте нет.
    mockedFetch.mockResolvedValue(STEP_ONE);
    mockedPost.mockResolvedValue(STEP_TWO);
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: "Лицо и кожа" }));

    expect(
      await screen.findByText("Как хочешь себя чувствовать после?"),
    ).toBeInTheDocument();
    expect(screen.getByText("Вопрос 2 из 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отдохнувшей" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Лицо и кожа" })).toBeNull();
  });

  it("на шаге со свободным вводом текст уходит ответом на шаг, а не прямой целью", async () => {
    // Куда уедет текст — решает сервер через allow_free_text.
    mockedFetch.mockResolvedValue(STEP_FINAL);
    mockedPost.mockResolvedValue(DONE);
    renderScreen();

    const box = await screen.findByRole("textbox", { name: "Опиши своими словами" });
    await userEvent.type(box, "своя формулировка");
    await userEvent.click(screen.getByRole("button", { name: "Отправить" }));

    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "goal", text: "своя формулировка" },
      source_channel: "miniapp",
    });
  });

  it("сервер прислал `next` — кнопка ведёт туда, куда он назвал", async () => {
    mockedFetch.mockResolvedValue(DONE);
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: "Найти услугу" }));
    expect(screen.getByText("ЭКРАН ПОДБОРА")).toBeInTheDocument();
  });

  it("`next` пуст — кнопки нет", async () => {
    mockedFetch.mockResolvedValue(STEP_ONE);
    renderScreen();
    await screen.findByText("Что сейчас хочется привести в порядок?");
    expect(screen.queryByRole("button", { name: "Найти услугу" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------

describe("анкета — НЕ ворота (условие C-2)", () => {
  it("назвал услугу на первом вопросе → попал к подбору, не ответив ни на один", async () => {
    mockedFetch.mockResolvedValue(STEP_ONE);
    mockedPost.mockResolvedValue(DONE);
    renderScreen();

    // Человек видит первый вопрос анкеты…
    expect(
      await screen.findByText("Что сейчас хочется привести в порядок?"),
    ).toBeInTheDocument();

    // …но знает, чего хочет, и пишет это в поле, стоящее ЗДЕСЬ ЖЕ.
    const box = screen.getByRole("textbox", { name: "Опиши своими словами" });
    await userEvent.type(box, "хочу маникюр");
    await userEvent.click(screen.getByRole("button", { name: "Отправить" }));

    // Ушло прямой целью, а не ответом на вопрос.
    expect(mockedPost).toHaveBeenCalledWith({
      goal_text: "хочу маникюр",
      source_channel: "miniapp",
    });

    // Вопросов на экране больше нет, есть дорога дальше.
    await userEvent.click(await screen.findByRole("button", { name: "Найти услугу" }));
    expect(screen.getByText("ЭКРАН ПОДБОРА")).toBeInTheDocument();

    // ЗАМЕР: ни одного ответа на вопрос анкеты за весь путь.
    expect(answersSent()).toEqual([]);
  });

  it("выбрал подсказку на первом вопросе → к подбору, ноль ответов", async () => {
    mockedFetch.mockResolvedValue(STEP_ONE);
    mockedPost.mockResolvedValue(DONE);
    renderScreen();

    const group = await screen.findByRole("radiogroup");
    await userEvent.click(within(group).getByRole("radio", { name: "Расслабиться" }));

    expect(mockedPost).toHaveBeenCalledWith({
      goal_key: "relax",
      source_channel: "miniapp",
    });

    await userEvent.click(await screen.findByRole("button", { name: "Найти услугу" }));
    expect(screen.getByText("ЭКРАН ПОДБОРА")).toBeInTheDocument();
    expect(answersSent()).toEqual([]);
  });

  it("свободный ввод и подсказки стоят рядом с вопросом, а не вместо него", async () => {
    // Если бы они появлялись только после анкеты, выхода с первого
    // вопроса не было бы — анкета стала бы воротами.
    mockedFetch.mockResolvedValue(STEP_ONE);
    renderScreen();

    expect(
      await screen.findByText("Что сейчас хочется привести в порядок?"),
    ).toBeInTheDocument();
    expect(screen.getByRole("radiogroup")).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Опиши своими словами" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Не понимаю, чего хочу" }),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------

describe("повторный проход (DRF-1225 / C-4)", () => {
  it("сервер прислал start_anketa — кнопка есть и уходит намерением", async () => {
    const withRestart: DecisionContext = {
      ...DONE,
      intents: [...INTENTS, { id: "start_anketa", label: "Пройти анкету заново" }],
    };
    mockedFetch.mockResolvedValue(withRestart);
    mockedPost.mockResolvedValue(STEP_ONE);
    renderScreen();

    await userEvent.click(
      await screen.findByRole("button", { name: "Пройти анкету заново" }),
    );
    expect(mockedPost).toHaveBeenCalledWith({
      intent: "start_anketa",
      source_channel: "miniapp",
    });
    expect(
      await screen.findByText("Что сейчас хочется привести в порядок?"),
    ).toBeInTheDocument();
  });

  it("сервер намерения не прислал — кнопки нет", async () => {
    mockedFetch.mockResolvedValue(DONE);
    renderScreen();
    await screen.findByRole("button", { name: "Найти услугу" });
    expect(screen.queryByRole("button", { name: "Пройти анкету заново" })).toBeNull();
  });
});
