/**
 * Срок цели на экране цели (DRF-2173, H01-2).
 *
 * Каталог (#518) прислал последним шагом анкеты `deadline` — «К какому сроку
 * хочешь? Можно пропустить» с чипами «через месяц / к лету / без срока» (+
 * «Не знаю» ролью escape), со свободным текстом; и `known.goal.target_date`
 * / `target_date_passed`.
 *
 * Здесь:
 *   - шаг рисуется штатным рендером single-шага: чипы + поле текста с
 *     плейсхолдером «например, до 1 ноября» (чипа «своя дата» нет — сторож
 *     C03 DRF-1751);
 *   - текст уходит как ответ на шаг; отказ каталога 400 словами
 *     («Этот срок уже прошёл…») показывается словами, а не «Не получилось
 *     отправить»;
 *   - под «Уже учла» — строка «До 1 ноября 2026»; без срока — строки нет;
 *   - срок прошёл — «Срок прошёл — обновить?» → повторный проход
 *     (`start_anketa`); без push и без процентов.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return { ...original, fetchDecisionContext: vi.fn(), postGoalSelect: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, maxBridge: vi.fn(), returnToChat: vi.fn(() => "closed") };
});

import { SurfaceModeContext } from "../components/SurfaceSwitch";
import { ApiError } from "../lib/api";
import { fetchDecisionContext, postGoalSelect, type DecisionContext } from "../lib/customer-goals";
import { maxBridge } from "../lib/max-sdk";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);
const mockedBridge = vi.mocked(maxBridge);

const INTENTS: DecisionContext["intents"] = [
  { id: "formulate_own", label: "Рассказать своими словами" },
  { id: "start_anketa", label: "Пройти анкету заново" },
];

const GOAL = {
  goal_key: "self_care",
  goal_text: null,
  label: "Привести себя в порядок",
  selected_at: "2026-09-20T09:00:00Z",
  source_channel: "miniapp" as const,
};

const DEADLINE_STEP: DecisionContext["missing"][number] = {
  kind: "goal_anketa",
  prompt: "К какому сроку хочешь? Можно пропустить Ответ сохраню, на подбор он пока не влияет.",
  step: "deadline",
  options: [
    { key: "in_month", label: "через месяц" },
    { key: "by_summer", label: "к лету" },
    { key: "no_deadline", label: "без срока" },
    { key: "unknown", label: "Не знаю", role: "escape" },
  ],
  allow_free_text: true,
  mode: "single",
  progress: { index: 4, total: 4, is_last: true },
};

const ASKS_DEADLINE: DecisionContext = {
  version: 2,
  known: { goal: GOAL, anketa: [] },
  missing: [DEADLINE_STEP],
  suggestions: [],
  intents: INTENTS,
  next: null,
};

const WITH_DEADLINE: DecisionContext = {
  ...ASKS_DEADLINE,
  known: { goal: { ...GOAL, target_date: "2026-11-01", target_date_passed: false }, anketa: [] },
  missing: [],
  next: { id: "return_to_chat", label: "Вернуться в чат" },
};

/** Срок прошёл; документ НЕ на кадре C03.5 (`next` не return_to_chat) —
 *  там ничего кликабельного, и кнопка обновления жить не может (К-2). */
const DEADLINE_PASSED: DecisionContext = {
  ...WITH_DEADLINE,
  known: { goal: { ...GOAL, target_date: "2026-01-01", target_date_passed: true }, anketa: [] },
  next: null,
};

const DEADLINE_PASSED_ON_C035: DecisionContext = {
  ...DEADLINE_PASSED,
  next: { id: "return_to_chat", label: "Вернуться в чат" },
};

const NO_DEADLINE: DecisionContext = {
  ...WITH_DEADLINE,
  known: { goal: { ...GOAL, target_date: null, target_date_passed: false }, anketa: [] },
};

function renderScreen() {
  render(
    <SurfaceModeContext.Provider value={{ canSwitch: false, requestChooser: () => {} }}>
      <MemoryRouter initialEntries={["/customer/goal-select"]}>
        <Routes>
          <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
          <Route path="/customer/main" element={<div>ГЛАВНЫЙ H01</div>} />
        </Routes>
      </MemoryRouter>
    </SurfaceModeContext.Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedBridge.mockReturnValue(null);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("шаг «К какому сроку хочешь?»", () => {
  it("чипы из документа + поле текста с плейсхолдером «например, до 1 ноября»; чипа «своя дата» нет", async () => {
    mockedFetch.mockResolvedValue(ASKS_DEADLINE);
    renderScreen();

    await screen.findByText(/К какому сроку хочешь\? Можно пропустить/);
    for (const label of ["через месяц", "к лету", "без срока", "Не знаю"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: /своя дата/i })).toBeNull();
    expect(screen.getByPlaceholderText("например, до 1 ноября")).toBeInTheDocument();
  });

  it("«без срока» уходит ответом на шаг; текст «до 1 ноября» — тоже ответом на шаг, не целью", async () => {
    mockedFetch.mockResolvedValue(ASKS_DEADLINE);
    mockedPost.mockResolvedValue(WITH_DEADLINE);
    renderScreen();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "без срока" }));
    expect(mockedPost).toHaveBeenLastCalledWith({
      answer: { step: "deadline", option_key: "no_deadline" },
      source_channel: "miniapp",
    });

    mockedFetch.mockResolvedValue(ASKS_DEADLINE);
    renderScreen();
    const field = (await screen.findAllByPlaceholderText("например, до 1 ноября")).at(-1)!;
    await user.type(field, "до 1 ноября");
    await user.click(screen.getAllByRole("button", { name: "Отправить" }).at(-1)!);
    expect(mockedPost).toHaveBeenLastCalledWith({
      answer: { step: "deadline", text: "до 1 ноября" },
      source_channel: "miniapp",
    });
  });

  it("отказ каталога 400 — словами человека, а не «Не получилось отправить»", async () => {
    mockedFetch.mockResolvedValue(ASKS_DEADLINE);
    mockedPost.mockRejectedValue(
      new ApiError(400, "ayla_bad_request", "ayla returned HTTP 400", {
        ayla_error: {
          error: {
            code: "VALIDATION_ERROR",
            message: "Validation failed",
            details: { answer: { text: ["Этот срок уже прошёл — назови дату не раньше сегодняшней."] } },
          },
        },
      }),
    );
    renderScreen();
    const user = userEvent.setup();

    await user.type(await screen.findByPlaceholderText("например, до 1 ноября"), "вчера");
    await user.click(screen.getByRole("button", { name: "Отправить" }));

    expect(
      await screen.findByText("Этот срок уже прошёл — назови дату не раньше сегодняшней."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Не получилось отправить. Попробуй снова.")).toBeNull();
    // Документ не менялся — не перечитываем, и набранное остаётся в поле.
    expect(mockedFetch).toHaveBeenCalledTimes(1);
    expect(screen.getByPlaceholderText("например, до 1 ноября")).toHaveValue("вчера");
  });

  it("протухший документ (каталог 409 за общим 400 прокси) — по-прежнему общий текст и перечитывание", async () => {
    mockedFetch.mockResolvedValue(ASKS_DEADLINE);
    mockedPost.mockRejectedValue(
      new ApiError(400, "ayla_bad_request", "ayla returned HTTP 409", {
        ayla_status: 409,
        ayla_error: {
          error: {
            code: "ANKETA_STEP_MISMATCH",
            message: "Answer does not match the expected step.",
            details: { expected_step: "goal" },
          },
        },
      }),
    );
    renderScreen();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "без срока" }));

    expect(await screen.findByText("Не получилось отправить. Попробуй снова.")).toBeInTheDocument();
    await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(2));
  });
});

describe("строка срока под «Уже учла»", () => {
  it("«До 1 ноября 2026» при сроке", async () => {
    mockedFetch.mockResolvedValue(WITH_DEADLINE);
    renderScreen();
    expect(await screen.findByText("До 1 ноября 2026")).toBeInTheDocument();
    expect(screen.queryByText(/Срок прошёл/)).toBeNull();
  });

  it("без срока — строки нет (§103), экран как раньше", async () => {
    mockedFetch.mockResolvedValue(NO_DEADLINE);
    renderScreen();
    expect(await screen.findByText("Привести себя в порядок")).toBeInTheDocument();
    await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/^До \d/)).toBeNull();
    expect(screen.queryByText(/Срок прошёл/)).toBeNull();
  });

  it("срок прошёл — «Срок прошёл — обновить?» ведёт в повторный проход, без процентов и push", async () => {
    mockedFetch.mockResolvedValue(DEADLINE_PASSED);
    mockedPost.mockResolvedValue(ASKS_DEADLINE);
    renderScreen();
    const user = userEvent.setup();

    const cta = await screen.findByRole("button", { name: "Срок прошёл — обновить?" });
    expect(screen.getByText("До 1 января 2026")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/%/);
    await user.click(cta);
    expect(mockedPost).toHaveBeenLastCalledWith({ intent: "start_anketa", source_channel: "miniapp" });
  });

  it("на кадре C03.5 (контекст собран) кнопки нет — там ничего кликабельного (К-2); дата остаётся", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedFetch.mockResolvedValue(DEADLINE_PASSED_ON_C035);
    renderScreen();
    expect(await screen.findByText("До 1 января 2026")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Срок прошёл — обновить?" })).toBeNull();
  });
});
