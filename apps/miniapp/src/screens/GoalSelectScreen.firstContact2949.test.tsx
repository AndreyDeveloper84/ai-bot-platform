/**
 * Вид шага цели по макету «Первый контакт» (DRF-2949, кадры 1, 2, 8, 11, 15, 16).
 *
 * Решение владельца 11.11.2026: путь первого контакта остаётся в переписке, в
 * приложении меняется только ВИД этого экрана. Поэтому главный предмет узлов —
 * не «нарисовано красиво», а «поведение то же»:
 *
 *   - настоящий документ новичка — шаг цели анкеты (`step: "goal"`), а не ряд
 *     `suggestions`: нажатие по-прежнему шлёт `answer`, а не `goal_key`;
 *   - «Пропустить» серверу ничего не шлёт;
 *   - приветствие — только на корне и только до первого действия человека;
 *     «Пропустить» — на корне, пока цели нет и у документа нет своей дороги.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return { ...original, fetchDecisionContext: vi.fn(), postGoalSelect: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, maxBridge: vi.fn(() => null), returnToChat: vi.fn(() => "closed") };
});

import { SurfaceModeContext } from "../components/SurfaceSwitch";
import { ApiError } from "../lib/api";
import { OPEN_FROM_MAX_COPY } from "../lib/auth-error-copy";
import { fetchDecisionContext, postGoalSelect, type DecisionContext } from "../lib/customer-goals";
import {
  GoalSelectScreen,
  HELLO_TEXT,
  HELLO_TITLE,
  LATER_HOME,
  LATER_TEXT,
  LATER_TITLE,
  SKIP_LABEL,
} from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);

const INTENTS: DecisionContext["intents"] = [
  { id: "choose_suggested", label: "Выбрать из предложенного" },
  { id: "formulate_own", label: "Рассказать своими словами" },
  { id: "need_guidance", label: "Не понимаю, чего хочу" },
];

const PROMPT = "Выбери цель — или напиши своими словами, чего хочешь.";

/** Настоящий документ новичка: шаг цели анкеты, ряд `suggestions` пуст. */
const GOAL_STEP_DOC: DecisionContext = {
  version: 2,
  known: { goal: null, anketa: [] },
  missing: [
    {
      kind: "goal_anketa",
      prompt: PROMPT,
      step: "goal",
      options: [
        { key: "relax", label: "Расслабиться и снять стресс" },
        { key: "face", label: "Позаботиться о коже лица" },
        { key: "energy", label: "Восстановить силы" },
      ],
      allow_free_text: true,
      mode: "single",
      progress: { index: 1, total: 3, is_last: false },
    },
  ],
  suggestions: [],
  intents: INTENTS,
  next: null,
};

/**
 * После «Не понимаю, чего хочу» — как отвечает каталог: наводящий вопрос, шага
 * цели нет, а ряд `suggestions` заполнен (он обнуляется только на шаге цели).
 */
const GUIDANCE_DOC: DecisionContext = {
  ...GOAL_STEP_DOC,
  missing: [{ kind: "goal_guidance", prompt: "Что сейчас хочется привести в порядок?" }],
  suggestions: [
    { key: "relax", label: "Расслабиться и снять стресс" },
    { key: "face", label: "Позаботиться о коже лица" },
  ],
};

/** Документ каталога до шага цели: цели рядом `suggestions`. */
const LEGACY_DOC: DecisionContext = {
  version: 1,
  known: { goal: null },
  missing: [],
  suggestions: [
    { key: "relax", label: "Расслабиться и снять стресс" },
    { key: "face", label: "Позаботиться о коже лица" },
  ],
  intents: INTENTS,
};

function renderAt(path: string, canSwitch = false) {
  render(
    <SurfaceModeContext.Provider value={{ canSwitch, requestChooser: () => {} }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={<GoalSelectScreen />} />
          <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
          <Route path="/customer/main" element={<div>ГЛАВНЫЙ</div>} />
        </Routes>
      </MemoryRouter>
    </SurfaceModeContext.Provider>,
  );
}

/** Идёт ли `a` в документе раньше `b`. */
function precedes(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedFetch.mockResolvedValue(GOAL_STEP_DOC);
  mockedPost.mockResolvedValue(GOAL_STEP_DOC);
});

describe("кадр 1 — новичок на корне", () => {
  it("приветствие словами бота, под ним заголовок, вопрос, поле и цели — в этом порядке", async () => {
    renderAt("/");

    const hello = await screen.findByText(HELLO_TITLE, { exact: false });
    expect(screen.getByText(HELLO_TEXT)).toBeInTheDocument();
    const title = screen.getByRole("heading", { level: 1, name: "Какая у тебя цель?" });
    const prompt = screen.getByText(PROMPT);
    const field = screen.getByRole("textbox", { name: "Рассказать своими словами" });
    const goals = screen.getByRole("group", { name: PROMPT });

    expect(precedes(hello, title)).toBe(true);
    expect(precedes(title, prompt)).toBe(true);
    // Кадры 1–2: «своими словами» стоит НАД вариантами.
    expect(precedes(prompt, field)).toBe(true);
    expect(precedes(field, goals)).toBe(true);
  });

  it("цели — карточками, все до одной; подпись кнопки — ровно подпись сервера", async () => {
    renderAt("/");

    const group = await screen.findByRole("group", { name: PROMPT });
    // Над целями — серверная подпись ряда (на макете там «или выбери вариант»).
    const label = screen.getByRole("heading", { level: 2, name: "Выбрать из предложенного" });
    expect(precedes(label, group)).toBe(true);
    const goals = within(group).getAllByRole("button");
    expect(goals.map((b) => b.textContent)).toEqual([
      "Расслабиться и снять стресс",
      "Позаботиться о коже лица",
      "Восстановить силы",
    ]);
    for (const goal of goals) expect(goal).toHaveClass("goal-select__option");
  });

  it("поведение прежнее: нажатие на цель сразу шлёт ответ шага, а не `goal_key`", async () => {
    renderAt("/");
    await userEvent.setup().click(
      await screen.findByRole("button", { name: "Позаботиться о коже лица" }),
    );

    expect(mockedPost).toHaveBeenCalledTimes(1);
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "goal", option_key: "face" },
      source_channel: "miniapp",
    });
    // Кнопки «Продолжить» с макета нет: выбор не откладывается.
    expect(screen.queryByRole("button", { name: "Продолжить" })).not.toBeInTheDocument();
  });

  it("свой текст уходит ответом шага липкой «Отправить»; «Пропустить» на это время уступает место", async () => {
    const user = userEvent.setup();
    renderAt("/");
    const field = await screen.findByRole("textbox", { name: "Рассказать своими словами" });
    expect(screen.getByRole("button", { name: SKIP_LABEL })).toBeInTheDocument();

    await user.type(field, "Хочу высыпаться");
    expect(screen.queryByRole("button", { name: SKIP_LABEL })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Отправить" }));

    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "goal", text: "Хочу высыпаться" },
      source_channel: "miniapp",
    });
  });
});

describe("«Пропустить» и кадр 11", () => {
  it("кнопка стоит в липкой панели — её не надо доскролливать", async () => {
    renderAt("/");
    const skip = await screen.findByRole("button", { name: SKIP_LABEL });
    expect(skip.closest(".cta-bar")).not.toBeNull();
  });

  it("пропуск ничего не шлёт серверу и показывает «Можно настроить план позже»", async () => {
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await screen.findByRole("button", { name: SKIP_LABEL }));

    expect(screen.getByRole("heading", { level: 1, name: LATER_TITLE })).toBeInTheDocument();
    expect(screen.getByText(LATER_TEXT)).toBeInTheDocument();
    expect(mockedPost).not.toHaveBeenCalled();
    // «Напомнить позже» и ссылки в профиль нет (решение владельца п.7).
    expect(screen.queryByText(/Напомнить/)).not.toBeInTheDocument();
    expect(screen.queryByText(/в профиле/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: LATER_HOME }));
    expect(await screen.findByText("ГЛАВНЫЙ")).toBeInTheDocument();
  });

  it("с кадра «настроить позже» можно вернуться к шагу цели; фокус не теряется", async () => {
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await screen.findByRole("button", { name: SKIP_LABEL }));
    // Кадр заменил экран целиком — фокус на его заголовке, а не в `body`.
    expect(screen.getByRole("heading", { level: 1, name: LATER_TITLE })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Назад" }));

    expect(await screen.findByRole("group", { name: PROMPT })).toBeInTheDocument();
    expect(screen.queryByText(LATER_TITLE)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: SKIP_LABEL })).toHaveFocus();
  });

  it("пока ответ отправляется, «Пропустить» выключена", async () => {
    mockedPost.mockReturnValue(new Promise(() => {}));
    renderAt("/");
    await userEvent.setup().click(
      await screen.findByRole("button", { name: "Восстановить силы" }),
    );
    expect(screen.getByRole("button", { name: SKIP_LABEL })).toBeDisabled();
  });

  it("многоролевой: «Сменить режим» остаётся рядом с «Пропустить» и на кадре «настроить позже»", async () => {
    const user = userEvent.setup();
    renderAt("/", true);
    await screen.findByRole("button", { name: SKIP_LABEL });
    expect(screen.getByRole("button", { name: "Сменить режим" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: SKIP_LABEL }));
    expect(screen.getByRole("button", { name: "Сменить режим" })).toBeInTheDocument();
  });
});

describe("где приветствия и «Пропустить» нет", () => {
  it("не корень (кадр 2): заголовок, поле над целями, карточки — без приветствия и без «Пропустить»", async () => {
    renderAt("/customer/goal-select");

    const field = await screen.findByRole("textbox", { name: "Рассказать своими словами" });
    const goals = screen.getByRole("group", { name: PROMPT });
    expect(screen.getByRole("heading", { level: 1, name: "Какая у тебя цель?" })).toBeInTheDocument();
    expect(precedes(field, goals)).toBe(true);
    expect(within(goals).getAllByRole("button")[0]).toHaveClass("goal-select__option");

    expect(screen.queryByText(HELLO_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: SKIP_LABEL })).not.toBeInTheDocument();
    // Там выход есть и так — «назад».
    expect(screen.getByRole("button", { name: "Назад" })).toBeInTheDocument();
  });

  it("на корне после «Не понимаю, чего хочу» здороваться поздно, а выход остаётся", async () => {
    const user = userEvent.setup();
    mockedPost.mockResolvedValue(GUIDANCE_DOC);
    renderAt("/");
    // Положительная пара: до нажатия приветствие есть.
    expect(await screen.findByText(HELLO_TEXT)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Не понимаю, чего хочу" }));

    expect(await screen.findByText("Что сейчас хочется привести в порядок?")).toBeInTheDocument();
    // Цели в документе по-прежнему есть — но человек уже действовал.
    expect(screen.getAllByRole("radio")).toHaveLength(2);
    expect(screen.queryByText(HELLO_TEXT)).not.toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    // Выход с первого экрана никуда не делся.
    expect(screen.getByRole("button", { name: SKIP_LABEL })).toBeInTheDocument();
  });

  it("на корне после выбора цели приветствие и «Пропустить» уходят", async () => {
    const user = userEvent.setup();
    mockedPost.mockResolvedValue({
      ...GOAL_STEP_DOC,
      known: {
        goal: {
          goal_key: "face",
          goal_text: null,
          label: "Позаботиться о коже лица",
          selected_at: "2026-09-20T09:00:00Z",
          source_channel: "miniapp",
        },
        anketa: [],
      },
      missing: [
        {
          kind: "goal_anketa",
          prompt: "Что хочется наладить в первую очередь?",
          step: "area",
          options: [{ key: "face", label: "Лицо и кожа" }],
          allow_free_text: false,
          mode: "single",
        },
      ],
    });
    renderAt("/");
    await user.click(await screen.findByRole("button", { name: "Позаботиться о коже лица" }));

    expect(await screen.findByRole("button", { name: "Лицо и кожа" })).toBeInTheDocument();
    expect(screen.queryByText(HELLO_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: SKIP_LABEL })).not.toBeInTheDocument();
  });

  it("у документа есть своя дорога дальше — «Пропустить» не рисуется (третий ряд панели не нужен)", async () => {
    mockedFetch.mockResolvedValue({
      ...LEGACY_DOC,
      next: { id: "browse_catalog", label: "Найти услугу" },
    });
    renderAt("/", true);

    expect(await screen.findByRole("button", { name: "Найти услугу" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сменить режим" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: SKIP_LABEL })).not.toBeInTheDocument();
  });

  it("«Изменить цель»: шаг цели при известной цели — тоже карточками, без приветствия", async () => {
    mockedFetch.mockResolvedValue({
      ...GOAL_STEP_DOC,
      known: {
        goal: {
          goal_key: "relax",
          goal_text: null,
          label: "Расслабиться и снять стресс",
          selected_at: "2026-09-20T09:00:00Z",
          source_channel: "miniapp",
        },
        anketa: [],
      },
    });
    renderAt("/customer/goal-select");

    const goals = await screen.findByRole("group", { name: PROMPT });
    expect(within(goals).getAllByRole("button")[0]).toHaveClass("goal-select__option");
    expect(screen.queryByText(HELLO_TEXT)).not.toBeInTheDocument();
  });

  it("другой шаг анкеты рисуется как раньше — фишками", async () => {
    mockedFetch.mockResolvedValue({
      ...GOAL_STEP_DOC,
      missing: [
        {
          kind: "goal_anketa",
          prompt: "Что хочется наладить в первую очередь?",
          step: "area",
          options: [{ key: "face", label: "Лицо и кожа" }],
          allow_free_text: false,
          mode: "single",
        },
      ],
    });
    renderAt("/customer/goal-select");

    const option = await screen.findByRole("button", { name: "Лицо и кожа" });
    expect(option).toHaveClass("chip");
    expect(option).not.toHaveClass("goal-select__option");
  });
});

describe("прежний документ: цели рядом `suggestions`", () => {
  it("тот же вид и то же тело: карточки-радио, поле над ними, нажатие шлёт `goal_key`", async () => {
    mockedFetch.mockResolvedValue(LEGACY_DOC);
    renderAt("/");

    const radios = await screen.findAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(radios[0]).toHaveClass("goal-select__option");
    expect(
      precedes(screen.getByRole("textbox", { name: "Рассказать своими словами" }), radios[0]!),
    ).toBe(true);
    expect(screen.getByText(HELLO_TEXT)).toBeInTheDocument();

    await userEvent.setup().click(radios[1]!);
    expect(mockedPost).toHaveBeenCalledWith({ goal_key: "face", source_channel: "miniapp" });
  });
});

describe("кадр 8 — отказ гейта здоровья карточкой, слова сервера", () => {
  it("заголовок и текст сервера — внутри одной карточки; «Понятно» возвращает к экрану, цель не создана", async () => {
    const user = userEvent.setup();
    mockedPost.mockResolvedValue({
      safety: { kind: "health_red_flag", text: "По описанию это может требовать помощи врача." },
    });
    renderAt("/");
    await user.click(await screen.findByRole("button", { name: "Восстановить силы" }));

    const frame = await screen.findByTestId("goal-safety-frame");
    const title = within(frame).getByRole("heading", { name: "Здесь я не подскажу" });
    const text = within(frame).getByText("По описанию это может требовать помощи врача.");
    const card = title.closest(".goal-select__stop");
    expect(card).not.toBeNull();
    expect(card).toContainElement(text);
    // Кнопки макета «Посмотреть другие варианты» нет — такого действия нет.
    expect(screen.queryByText(/другие варианты/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Цель сохранена.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Понятно" }));
    expect(await screen.findByRole("group", { name: PROMPT })).toBeInTheDocument();
  });
});

describe("кадры 15–16 — сбой загрузки, слова прежние", () => {
  it("ответа нет вовсе: прежняя фраза про интернет, объявлена как ошибка, повтор работает", async () => {
    mockedFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    renderAt("/customer/goal-select");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Не получилось загрузить. Проверь интернет и попробуй снова.");

    mockedFetch.mockResolvedValue(GOAL_STEP_DOC);
    await userEvent.setup().click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(await screen.findByRole("group", { name: PROMPT })).toBeInTheDocument();
  });

  it("сбой сервера: прежняя фраза, серверный `detail` человеку не показан", async () => {
    mockedFetch.mockRejectedValue(new ApiError(502, "ayla_unavailable", "upstream down"));
    renderAt("/customer/goal-select");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Что-то у нас не получается прямо сейчас.",
    );
    expect(screen.queryByText("upstream down")).not.toBeInTheDocument();
  });

  it("отказ входа называется своим именем: заголовок и пояснение из словаря отказов", async () => {
    mockedFetch.mockRejectedValue(new ApiError(403, "user_deleted", "gone"));
    renderAt("/customer/goal-select");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading", { name: "Аккаунт удалён" })).toBeInTheDocument();
    expect(alert).toHaveTextContent(/напиши боту студии/);
  });

  it("отказ транспорта — не повтор, а возврат в MAX (DRF-1893)", async () => {
    mockedFetch.mockRejectedValue(new ApiError(400, "malformed", "bad init data"));
    renderAt("/customer/goal-select");

    expect(await screen.findByText(OPEN_FROM_MAX_COPY.title)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Попробовать снова" })).not.toBeInTheDocument();
  });
});
