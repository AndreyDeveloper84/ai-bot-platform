/**
 * DRF-1763 — safety-стоп на тексте цели (C02-M7).
 *
 * Сервер на «болит спина после работы» отвечает не документом, а конвертом
 * `{safety}`: цель не создана, документ не менялся. Экран обязан показать
 * признание и вопросы, НЕ говорить «Цель сохранена», и единственным выходом
 * держать ответ человека — который уходит РЯДОМ с исходным телом. Отказы
 * (red flag / crisis / block) показываются как текст без кнопки «продолжить».
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

import {
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
  type SafetyEnvelope,
} from "../lib/customer-goals";
import {
  HEALTH_ACKNOWLEDGEMENT_COPY,
  SAFETY_ANSWER_FIELD,
  SAFETY_KIND_BLOCK,
  SAFETY_KIND_CLARIFY,
  SAFETY_KIND_CRISIS,
  SAFETY_KIND_RED_FLAG,
} from "../lib/health-gate-copy";
import { GoalSelectScreen } from "./GoalSelectScreen";

/** [OD-BOT §163] — the server's medical S1 text (`apps/orchestrator/safety/medical_emergency.py`), verbatim. */
const MEDICAL_EMERGENCY_TEXT_V2 =
  "По описанию это может требовать срочной медицинской помощи. " +
  "Я не буду сейчас подбирать процедуру или оформлять запись. " +
  "Если это происходит сейчас, произошло только что, повторяется, усиливается " +
  "или тебе резко плохо — позвони 103 или 112. " +
  "Не добирайся за рулём самостоятельно. " +
  "Если можешь, попроси человека рядом помочь тебе вызвать помощь и остаться с тобой.";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);

const PAIN_GOAL = "болит спина после работы";
const CLEAN_ANSWER = "спина, после сидячей работы";
const QUESTIONS: [string, string] = [
  "Где именно болит — конкретное место?",
  "Это после нагрузки / сидячей работы или с утра после сна?",
];

const EMPTY_DOC: DecisionContext = {
  version: 2,
  known: { goal: null },
  missing: [{ kind: "goal", prompt: "Что хочешь изменить?" }],
  suggestions: [],
  intents: [{ id: "formulate_own", label: "Сформулирую своими словами" }],
  next: null,
};

const SAVED_DOC: DecisionContext = {
  ...EMPTY_DOC,
  known: {
    goal: { goal_key: null, goal_text: PAIN_GOAL, source_channel: "miniapp", selected_at: "2026-09-18T19:00:00Z" },
  },
  missing: [],
};

const CLARIFY: SafetyEnvelope = {
  safety: {
    kind: SAFETY_KIND_CLARIFY,
    acknowledgement: HEALTH_ACKNOWLEDGEMENT_COPY,
    questions: QUESTIONS,
  },
};

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/goal"]}>
      <Routes>
        <Route path="/customer/goal" element={<GoalSelectScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function typeGoalAndSend(text: string) {
  const textarea = await screen.findByRole("textbox", { name: "Сформулирую своими словами" });
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedFetch.mockResolvedValue(EMPTY_DOC);
});

describe("GoalSelectScreen — safety-стоп на тексте цели (DRF-1763)", () => {
  it("на health_clarify показывает признание и вопросы и НЕ объявляет «Цель сохранена»", async () => {
    mockedPost.mockResolvedValueOnce(CLARIFY);
    renderScreen();
    await typeGoalAndSend(PAIN_GOAL);

    const frame = await screen.findByTestId("goal-safety-frame");
    expect(frame).toHaveAttribute("data-safety-kind", SAFETY_KIND_CLARIFY);
    expect(within(frame).getByText(HEALTH_ACKNOWLEDGEMENT_COPY)).toBeInTheDocument();
    for (const q of QUESTIONS) expect(within(frame).getByText(q)).toBeInTheDocument();
    expect(screen.queryByText("Цель сохранена.")).not.toBeInTheDocument();
    // Документ остался на месте — ни «Текущая цель», ни новых вопросов анкеты.
    expect(screen.queryByText("Текущая цель")).not.toBeInTheDocument();
  });

  it("ответ уходит РЯДОМ с исходным телом как safety_answer, и документ обновляется как обычно", async () => {
    mockedPost.mockResolvedValueOnce(CLARIFY).mockResolvedValueOnce(SAVED_DOC);
    renderScreen();
    await typeGoalAndSend(PAIN_GOAL);
    await screen.findByTestId("goal-safety-frame");

    // Пока ответа нет — отправлять нечего.
    expect(screen.queryByRole("button", { name: "Отправить ответ" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Твой ответ" }), {
      target: { value: CLEAN_ANSWER },
    });
    fireEvent.click(screen.getByRole("button", { name: "Отправить ответ" }));

    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(2));
    expect(mockedPost.mock.calls[1]?.[0]).toEqual({
      goal_text: PAIN_GOAL,
      [SAFETY_ANSWER_FIELD]: CLEAN_ANSWER,
      source_channel: "miniapp",
    });
    await waitFor(() =>
      expect(screen.queryByTestId("goal-safety-frame")).not.toBeInTheDocument(),
    );
    expect(await screen.findByText("Цель сохранена.")).toBeInTheDocument();
    expect(screen.getByText(PAIN_GOAL)).toBeInTheDocument();
  });

  it.each([
    [SAFETY_KIND_RED_FLAG, MEDICAL_EMERGENCY_TEXT_V2],
    [SAFETY_KIND_CRISIS, "Спасибо, что написал(а) мне это.\n\nРядом есть те, кто может поддержать."],
    [SAFETY_KIND_BLOCK, "Здесь я не помощник — это вопрос к специалисту."],
  ])("на %s показывает текст сервера, без поля ответа и без «продолжить»", async (kind, text) => {
    mockedPost.mockResolvedValueOnce({ safety: { kind, text } });
    renderScreen();
    await typeGoalAndSend(PAIN_GOAL);

    const frame = await screen.findByTestId("goal-safety-frame");
    expect(frame).toHaveAttribute("data-safety-kind", kind);
    expect(within(frame).getByText((_, el) => el?.tagName === "P" && el.textContent === text)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Твой ответ" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отправить ответ" })).not.toBeInTheDocument();
    expect(screen.queryByText("Цель сохранена.")).not.toBeInTheDocument();
    // «Понятно» закрывает кадр и возвращает документ, каким он был — цели нет.
    fireEvent.click(screen.getByRole("button", { name: "Понятно" }));
    await waitFor(() =>
      expect(screen.queryByTestId("goal-safety-frame")).not.toBeInTheDocument(),
    );
    expect(mockedPost).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Текущая цель")).not.toBeInTheDocument();
  });

  it("экран не имеет своей строки о самочувствии: без acknowledgement в конверте признания нет", async () => {
    mockedPost.mockResolvedValueOnce({ safety: { kind: SAFETY_KIND_CLARIFY, questions: QUESTIONS } });
    renderScreen();
    await typeGoalAndSend(PAIN_GOAL);
    const frame = await screen.findByTestId("goal-safety-frame");
    expect(within(frame).queryByText(HEALTH_ACKNOWLEDGEMENT_COPY)).not.toBeInTheDocument();
    expect(within(frame).getByText(QUESTIONS[0])).toBeInTheDocument();
  });

  it("[OD-BOT §170] G7: три структурированных ответа — кнопки, без поля ответа; значение уходит как safety_answer", async () => {
    const G7_QUESTION =
      "Сейчас есть хотя бы один из признаков: кажется, что вы вот-вот потеряете сознание; " +
      "трудно самостоятельно стоять, говорить или дышать; появилась спутанность; " +
      "состояние быстро ухудшается?";
    const OPTIONS = [
      { label: "Да, есть хотя бы один признак", value: "cb:s1g7:yes:0123456789ab" },
      { label: "Нет — этих признаков не было и сейчас нет, состояние не ухудшается", value: "cb:s1g7:no:0123456789ab" },
      { label: "Не уверен(а) или не могу ответить", value: "cb:s1g7:unsure:0123456789ab" },
    ];
    mockedPost
      .mockResolvedValueOnce({
        safety: {
          kind: SAFETY_KIND_CLARIFY,
          questions: [G7_QUESTION],
          options: OPTIONS,
          question_id: "health_screening.g7",
        },
      })
      .mockResolvedValueOnce(SAVED_DOC);
    renderScreen();
    await typeGoalAndSend("Мне резко стало очень плохо");

    const frame = await screen.findByTestId("goal-safety-frame");
    expect(within(frame).getByText(G7_QUESTION)).toBeInTheDocument();
    const options = within(frame).getByTestId("goal-safety-options");
    for (const o of OPTIONS) expect(within(options).getByRole("button", { name: o.label })).toBeInTheDocument();
    // свободный текст — не ответ на этот вопрос
    expect(screen.queryByRole("textbox", { name: "Твой ответ" })).not.toBeInTheDocument();

    fireEvent.click(within(options).getByRole("button", { name: OPTIONS[1]!.label }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(2));
    expect(mockedPost.mock.calls[1]?.[0]).toEqual({
      goal_text: "Мне резко стало очень плохо",
      [SAFETY_ANSWER_FIELD]: OPTIONS[1]!.value,
      source_channel: "miniapp",
    });
  });

  it("negative-guard: копия экрана и константы не называют диагнозов", () => {
    const words = ["грыж", "остеохондроз", "протруз", "невралг", "артрит", "артроз", "сколиоз", "защемлен", "диагноз"];
    const own = ["Сначала уточню", "Здесь я не подскажу", "Твой ответ", "Ответь своими словами", HEALTH_ACKNOWLEDGEMENT_COPY]
      .join(" ")
      .toLowerCase();
    expect(words.filter((w) => own.includes(w))).toEqual([]);
  });
});
