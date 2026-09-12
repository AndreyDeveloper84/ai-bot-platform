/**
 * Блок «Уже учла» и «Изменить» (DRF-1744).
 *
 * Макет C03 (DRF-1178): человек видит, что его услышали; любой
 * показанный факт исправляем; блок сворачивается по мере роста; никаких
 * технических формулировок. Экран ничего не решает: строки — из
 * `known.anketa`, «Изменить» — только где сервер сказал `revisable`,
 * варианты для пересмотра — те, что сервер прислал в строке.
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
  ALREADY_NOTED_TITLE,
  COMPACT_ROWS_SHOWN,
  FULL_ROWS_MAX,
  SUMMARY_ROWS_MIN,
} from "../components/AlreadyNoted";
import {
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
  type KnownAnketaAnswer,
} from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);

const AREA: KnownAnketaAnswer = {
  step: "area",
  prompt: "Что сейчас хочется привести в порядок?",
  option_key: "face",
  label: "Лицо и кожа",
  options: [
    { key: "face", label: "Лицо и кожа" },
    { key: "hands", label: "Руки и ногти" },
  ],
  revisable: true,
};

const FEELING: KnownAnketaAnswer = {
  step: "feeling",
  prompt: "Как хочешь себя чувствовать после?",
  option_key: "rested",
  label: "Отдохнувшей",
  options: [
    { key: "rested", label: "Отдохнувшей" },
    { key: "calmer", label: "Спокойнее" },
  ],
  revisable: true,
};

const FINAL_PROMPT = "Выбери цель — или напиши своими словами.";

function docWith(answers: KnownAnketaAnswer[], goalText: string | null = null): DecisionContext {
  return {
    version: 2,
    known: {
      goal: goalText
        ? {
            goal_key: null,
            goal_text: goalText,
            selected_at: "2026-09-03T10:00:00Z",
            source_channel: "miniapp",
          }
        : null,
      anketa: answers,
    },
    missing: [
      {
        kind: "goal_anketa",
        prompt: FINAL_PROMPT,
        step: "goal",
        options: [{ key: "relax", label: "Расслабиться" }],
        allow_free_text: true,
        progress: { is_last: true },
      },
    ],
    suggestions: [],
    intents: [{ id: "formulate_own", label: "Опиши своими словами" }],
    next: null,
  };
}

/** N синтетических строк для состояний компактности. */
function manyAnswers(n: number): KnownAnketaAnswer[] {
  return Array.from({ length: n }, (_, i) => ({
    step: `step${i + 1}`,
    prompt: `Вопрос ${i + 1}?`,
    option_key: `opt${i + 1}`,
    label: `Ответ номер ${i + 1}`,
    options: [{ key: `opt${i + 1}`, label: `Ответ номер ${i + 1}` }],
    revisable: true,
  }));
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

function block(): HTMLElement {
  return screen.getByRole("region", { name: ALREADY_NOTED_TITLE });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("«Уже учла» рисуется из документа", () => {
  it("ответы прохода — строками, каждая с «Изменить»", async () => {
    mockedFetch.mockResolvedValue(docWith([AREA, FEELING]));
    renderScreen();

    expect(await screen.findByText(FINAL_PROMPT)).toBeInTheDocument();
    const b = block();
    expect(within(b).getByText("Лицо и кожа")).toBeInTheDocument();
    expect(within(b).getByText("Отдохнувшей")).toBeInTheDocument();
    expect(within(b).getByRole("button", { name: "Изменить: Лицо и кожа" })).toBeInTheDocument();
    expect(within(b).getByRole("button", { name: "Изменить: Отдохнувшей" })).toBeInTheDocument();
    // Вопрос при этом на месте: память не вместо вопроса, а рядом.
    expect(screen.getByRole("group", { name: FINAL_PROMPT })).toBeInTheDocument();
  });

  it("цель — первой строкой, без «Изменить» (её меняют выбором цели)", async () => {
    mockedFetch.mockResolvedValue(docWith([AREA], "хочу маникюр"));
    renderScreen();

    expect(await screen.findByText(FINAL_PROMPT)).toBeInTheDocument();
    const items = within(block()).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("хочу маникюр");
    expect(within(items[0]).queryByRole("button")).toBeNull();
    expect(items[1]).toHaveTextContent("Лицо и кожа");
    // Прежняя секция «Текущая цель» не дублирует цель второй раз.
    expect(screen.queryByText("Текущая цель")).toBeNull();
  });

  it("без ответов блока нет — документ до DRF-1744 рисуется как раньше", async () => {
    mockedFetch.mockResolvedValue(docWith([], "хочу маникюр"));
    renderScreen();

    expect(await screen.findByText(FINAL_PROMPT)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: ALREADY_NOTED_TITLE })).toBeNull();
    expect(screen.getByText("Текущая цель")).toBeInTheDocument();
  });

  it("«Изменить» только там, где сервер сказал revisable", async () => {
    mockedFetch.mockResolvedValue(docWith([AREA, { ...FEELING, revisable: false }]));
    renderScreen();

    expect(await screen.findByText(FINAL_PROMPT)).toBeInTheDocument();
    const b = block();
    expect(within(b).getByRole("button", { name: "Изменить: Лицо и кожа" })).toBeInTheDocument();
    expect(within(b).queryByRole("button", { name: "Изменить: Отдохнувшей" })).toBeNull();
  });
});

describe("«Изменить» — пересмотр ответа с revise", () => {
  it("открывает варианты ЭТОГО шага вместо вопроса, тап уходит с revise", async () => {
    mockedFetch.mockResolvedValue(docWith([AREA, FEELING]));
    mockedPost.mockResolvedValue(docWith([{ ...AREA, option_key: "hands", label: "Руки и ногти" }, FEELING]));
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: "Изменить: Лицо и кожа" }));

    // Один экран — одна задача: текущий вопрос уступил место пересмотру.
    expect(screen.queryByRole("group", { name: FINAL_PROMPT })).toBeNull();
    const group = screen.getByRole("group", { name: AREA.prompt });
    expect(within(group).getByRole("button", { name: "Лицо и кожа" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(within(group).getByRole("button", { name: "Руки и ногти" }));

    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "area", option_key: "hands", revise: true },
      source_channel: "miniapp",
    });
    // Ответ сервера заменил документ: строка обновлена, вопрос вернулся.
    expect(await screen.findByText("Ответ изменён.")).toBeInTheDocument();
    expect(within(block()).getByText("Руки и ногти")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: FINAL_PROMPT })).toBeInTheDocument();
  });

  it("«Оставить как есть» возвращает вопрос, ничего не отправляя", async () => {
    mockedFetch.mockResolvedValue(docWith([AREA]));
    renderScreen();

    await userEvent.click(await screen.findByRole("button", { name: "Изменить: Лицо и кожа" }));
    await userEvent.click(screen.getByRole("button", { name: "Оставить как есть" }));

    expect(mockedPost).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: FINAL_PROMPT })).toBeInTheDocument();
  });
});

describe("блок сворачивается по мере роста", () => {
  it(`до ${FULL_ROWS_MAX} строк — подробно, без раскрытия`, async () => {
    mockedFetch.mockResolvedValue(docWith(manyAnswers(FULL_ROWS_MAX)));
    renderScreen();
    expect(await screen.findByText(FINAL_PROMPT)).toBeInTheDocument();
    const b = block();
    expect(within(b).getAllByRole("listitem")).toHaveLength(FULL_ROWS_MAX);
    expect(within(b).queryByRole("button", { expanded: false })).toBeNull();
  });

  it(`${FULL_ROWS_MAX + 1} строк — первые ${COMPACT_ROWS_SHOWN} и «+N», раскрывается`, async () => {
    const n = FULL_ROWS_MAX + 1;
    mockedFetch.mockResolvedValue(docWith(manyAnswers(n)));
    renderScreen();
    expect(await screen.findByText(FINAL_PROMPT)).toBeInTheDocument();
    const b = block();
    expect(within(b).getAllByRole("listitem")).toHaveLength(COMPACT_ROWS_SHOWN);
    const more = within(b).getByRole("button", { name: `+${n - COMPACT_ROWS_SHOWN}` });

    await userEvent.click(more);
    expect(within(b).getAllByRole("listitem")).toHaveLength(n);
    expect(within(b).getByRole("button", { name: "Свернуть" })).toBeInTheDocument();
  });

  it(`от ${SUMMARY_ROWS_MIN} строк — одна сводка «N ответов · Посмотреть»`, async () => {
    mockedFetch.mockResolvedValue(docWith(manyAnswers(SUMMARY_ROWS_MIN)));
    renderScreen();
    expect(await screen.findByText(FINAL_PROMPT)).toBeInTheDocument();
    const b = block();
    expect(within(b).queryAllByRole("listitem")).toHaveLength(0);
    const open = within(b).getByRole("button", { name: `${SUMMARY_ROWS_MIN} ответов · Посмотреть` });

    await userEvent.click(open);
    expect(within(b).getAllByRole("listitem")).toHaveLength(SUMMARY_ROWS_MIN);
    expect(within(b).getByRole("button", { name: "Изменить: Ответ номер 7" })).toBeInTheDocument();
  });
});

describe("в блоке нет технических формулировок", () => {
  const TECHNICAL = /confidence|score|label|\bid\b|факт(ов|а)?\b|в памяти|%/i;

  it("ни в подробном, ни в свёрнутом состоянии", async () => {
    mockedFetch.mockResolvedValue(docWith(manyAnswers(SUMMARY_ROWS_MIN), "хочу маникюр"));
    renderScreen();
    expect(await screen.findByText(FINAL_PROMPT)).toBeInTheDocument();
    const b = block();
    expect(b.textContent ?? "").not.toMatch(TECHNICAL);
    await userEvent.click(within(b).getByRole("button", { expanded: false }));
    expect(b.textContent ?? "").not.toMatch(TECHNICAL);
    // Положительная стража самого сторожа.
    expect(TECHNICAL.test("в памяти 17 фактов")).toBe(true);
    expect(TECHNICAL.test("confidence 0.8")).toBe(true);
  });
});
