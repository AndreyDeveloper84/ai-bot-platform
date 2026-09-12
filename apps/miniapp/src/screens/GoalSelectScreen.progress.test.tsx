/**
 * Место в проходе анкеты (DRF-1743, доктрина 12.09).
 *
 * До этого среза экран рисовал «Вопрос 2 из 3» из `progress.index/total`.
 * Счётчик честен только пока порядок вопросов фиксирован; с движком
 * вопросов общее число неизвестно заранее — число стало бы выдумкой.
 * Макет C03: «формулировку „Ещё один короткий вопрос“ используем только
 * если действительно уверены, что вопрос последний». Уверен сервер:
 * `progress.is_last`. Экран числа не рисует и не вычисляет.
 *
 * Два сторожа: по рендеру (ни одного «из N» ни при каких числах) и по
 * исходнику (`progress.index`/`progress.total` не читаются нигде вне
 * типа) — второй нужен, потому что первый зеленеет и на пустом экране.
 */
import { render, screen } from "@testing-library/react";
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
  type AnketaProgress,
  type DecisionContext,
} from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);

const PROMPT = "Что сейчас хочется привести в порядок?";
const LAST_NOTE = "Ещё один короткий вопрос";
const COUNTER = /\bиз \d/;

function docWith(progress: AnketaProgress | undefined): DecisionContext {
  return {
    version: 2,
    known: { goal: null },
    missing: [
      {
        kind: "goal_anketa",
        prompt: PROMPT,
        step: "area",
        options: [{ key: "face", label: "Лицо и кожа" }],
        allow_free_text: false,
        ...(progress ? { progress } : {}),
      },
    ],
    suggestions: [],
    intents: [{ id: "formulate_own", label: "Опиши своими словами" }],
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
});

describe("«Ещё один короткий вопрос» — только когда сервер сказал is_last", () => {
  it("is_last: true → фраза есть, числа нет", async () => {
    mockedFetch.mockResolvedValue(docWith({ index: 3, total: 3, is_last: true }));
    renderScreen();

    expect(await screen.findByText(PROMPT)).toBeInTheDocument();
    expect(screen.getByText(LAST_NOTE)).toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toMatch(COUNTER);
  });

  it("is_last: false → ни фразы, ни числа", async () => {
    mockedFetch.mockResolvedValue(docWith({ index: 1, total: 3, is_last: false }));
    renderScreen();

    expect(await screen.findByText(PROMPT)).toBeInTheDocument();
    expect(screen.queryByText(LAST_NOTE)).toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(COUNTER);
  });

  it("сервер старой сборки без is_last → ничего, даже когда index == total", async () => {
    // «Последний» — не арифметика экрана. Равенство чисел — совпадение.
    mockedFetch.mockResolvedValue(docWith({ index: 3, total: 3 }));
    renderScreen();

    expect(await screen.findByText(PROMPT)).toBeInTheDocument();
    expect(screen.queryByText(LAST_NOTE)).toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(COUNTER);
  });

  it("без progress вовсе → вопрос рисуется, места в проходе нет", async () => {
    mockedFetch.mockResolvedValue(docWith(undefined));
    renderScreen();

    expect(await screen.findByText(PROMPT)).toBeInTheDocument();
    expect(screen.queryByText(LAST_NOTE)).toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(COUNTER);
  });
});

// ---------------------------------------------------------------------------
// Сторож по исходнику: числа прохода никто не читает.
// ---------------------------------------------------------------------------

const SOURCES = import.meta.glob(
  ["./**/*.tsx", "../components/**/*.tsx", "../lib/**/*.ts", "../App.tsx"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

/** Комментарии — не код: сторож не должен ловить памятку о том, чего нет. */
function withoutComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

const PROGRESS_NUMBER_READ = /progress\??\.(index|total)\b/;

describe("progress.index / progress.total не читаются нигде", () => {
  it("вне типа AnketaProgress нет ни одного чтения чисел прохода", () => {
    const offenders = Object.entries(SOURCES)
      .filter(([path]) => !path.endsWith(".test.ts") && !path.endsWith(".test.tsx"))
      .filter(([, src]) => PROGRESS_NUMBER_READ.test(withoutComments(src)))
      .map(([path]) => path);
    expect(offenders, "счётчик «Вопрос N из M» вернулся").toEqual([]);
  });

  it("сторож видит чтение, когда оно есть", () => {
    // Положительная стража самого сторожа: регулярка ловит оба написания.
    expect(PROGRESS_NUMBER_READ.test("Вопрос {item.progress.index} из {item.progress.total}")).toBe(true);
    expect(PROGRESS_NUMBER_READ.test("const n = p.progress?.total;")).toBe(true);
    expect(PROGRESS_NUMBER_READ.test("item.progress?.is_last === true")).toBe(false);
  });
});
