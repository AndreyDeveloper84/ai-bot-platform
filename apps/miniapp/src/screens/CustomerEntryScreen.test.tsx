/**
 * Первый вход клиента (DRF-1451).
 *
 * Решение владельца 03.09.2026: человек, впервые открывший
 * мини-приложение, попадает на анкету цели, а не на приветствие.
 *
 * Каждая пара «положительная / отрицательная» гоняется на ОДНИХ И ТЕХ
 * ЖЕ данных, различается только серверный документ: новизну определяет
 * сервер (`missing`), а не эвристика экрана. Отрицательный тест здесь
 * несёт вес наравне с положительным — без него «всегда показывать
 * анкету» тоже был бы зелёным.
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

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    authVerify: vi.fn(),
  };
});

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return {
    ...original,
    signalReady: vi.fn(),
    setBackButton: vi.fn(),
    onBackButton: vi.fn(() => () => undefined),
  };
});

import { authVerify } from "../lib/api";
import {
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
} from "../lib/customer-goals";
import { setBackButton, signalReady } from "../lib/max-sdk";
import { CustomerEntryScreen } from "./CustomerEntryScreen";
import { CustomerRoutes } from "../App";
import { SurfaceModeContext } from "../components/SurfaceSwitch";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedAuth = vi.mocked(authVerify);
const mockedSetBackButton = vi.mocked(setBackButton);

const INTENTS: DecisionContext["intents"] = [
  { id: "choose_suggested", label: "Выбери из вариантов" },
  { id: "formulate_own", label: "Опиши своими словами" },
  { id: "need_guidance", label: "Не понимаю, чего хочу" },
];

/** Сервер спрашивает — человек новый. */
const ASKING: DecisionContext = {
  version: 2,
  known: { goal: null },
  missing: [
    {
      kind: "goal_anketa",
      prompt: "Что сейчас хочется привести в порядок?",
      step: "area",
      options: [{ key: "face", label: "Лицо и кожа" }],
      allow_free_text: false,
      progress: { index: 1, total: 3 },
    },
  ],
  suggestions: [{ key: "relax", label: "Расслабиться" }],
  intents: INTENTS,
  next: null,
};

/** Спрашивать нечего — человек уже с целью. */
const SETTLED: DecisionContext = {
  version: 2,
  known: {
    goal: {
      goal_key: "relax",
      goal_text: null,
      selected_at: "2026-09-03T10:00:00Z",
      source_channel: "bot",
    },
  },
  missing: [],
  suggestions: [{ key: "relax", label: "Расслабиться" }],
  intents: INTENTS,
  next: { id: "browse_catalog", label: "Найти услугу" },
};

function renderEntry() {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<CustomerEntryScreen />} />
        <Route path="/customer/main" element={<div>ДОМАШНИЙ ЭКРАН</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // HelloScreen — запасной путь; пусть его вызов не падает.
  mockedAuth.mockResolvedValue({
    user: { client_name: "Аня", display_name: "Аня" },
    tenant: { name: "Студия" },
  } as unknown as Awaited<ReturnType<typeof authVerify>>);
});

describe("первый вход клиента", () => {
  it("сервер спрашивает — человек попадает на анкету, а не на приветствие", async () => {
    mockedFetch.mockResolvedValue(ASKING);
    renderEntry();

    expect(
      await screen.findByText("Что сейчас хочется привести в порядок?"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Лицо и кожа" })).toBeInTheDocument();
    // Приветствия на первом экране больше нет.
    expect(screen.queryByText(/Здравствуйте/)).toBeNull();
    expect(screen.queryByText("ДОМАШНИЙ ЭКРАН")).toBeNull();
  });

  it("спрашивать нечего — человек идёт на домашний экран, анкеты нет", async () => {
    // Тот же экран, тот же вызов, отличается только документ.
    mockedFetch.mockResolvedValue(SETTLED);
    renderEntry();

    expect(await screen.findByText("ДОМАШНИЙ ЭКРАН")).toBeInTheDocument();
    expect(screen.queryByText("Что сейчас хочется привести в порядок?")).toBeNull();
  });

  it("decision-context упал — вход в приложение остаётся рабочим", async () => {
    // Анкета необязательна; вход — нет. Падать домашним экраном на
    // сбое необязательной ручки было бы хуже, чем не спросить.
    mockedFetch.mockRejectedValue(new Error("502"));
    renderEntry();

    expect(await screen.findByText(/Здравствуйте/)).toBeInTheDocument();
  });

  it("MAX получает ready на первом экране в любой ветке", async () => {
    mockedFetch.mockResolvedValue(ASKING);
    renderEntry();
    await screen.findByText("Что сейчас хочется привести в порядок?");
    expect(vi.mocked(signalReady)).toHaveBeenCalled();
  });

  it("первый экран ходит за документом ОДИН раз, а не два", async () => {
    // Поверхность цели монтируется прямо здесь и получает документ
    // пропом. Без этого она сходила бы за тем же decision-context
    // второй раз — на первом экране, в самом дорогом месте.
    mockedFetch.mockResolvedValue(ASKING);
    renderEntry();
    await screen.findByText("Что сейчас хочется привести в порядок?");
    expect(mockedFetch).toHaveBeenCalledTimes(1);
  });

  it("на корне кнопки «назад» нет — вести ей некуда", async () => {
    // Канон useBackButton: показывать везде, кроме корня. Поверхность
    // цели теперь бывает и корнем, и без этой проверки на первом экране
    // висела бы кнопка, которая ничего не делает.
    mockedFetch.mockResolvedValue(ASKING);
    renderEntry();
    await screen.findByText("Что сейчас хочется привести в порядок?");
    expect(mockedSetBackButton).toHaveBeenCalledWith(false);
    expect(mockedSetBackButton).not.toHaveBeenCalledWith(true);
  });

  it("та же поверхность НЕ на корне кнопку «назад» показывает", async () => {
    // Обратная сторона: страховка от «спрятали везде».
    mockedFetch.mockResolvedValue(ASKING);
    const { GoalSelectScreen } = await import("./GoalSelectScreen");
    render(
      <MemoryRouter initialEntries={["/customer/goal-select"]}>
        <Routes>
          <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByText("Что сейчас хочется привести в порядок?");
    expect(mockedSetBackButton).toHaveBeenCalledWith(true);
  });
});

describe("анкета на корне — одна для всех (DRF-1469)", () => {
  /**
   * `canSwitch` — единственное, чем многоролевой отличается от
   * одноролевого на этой поверхности. Значение приходит из `App` тем
   * же контекстом, что питает «Сменить режим» на экранах настроек.
   */
  function renderRoot(canSwitch: boolean, requestChooser = () => {}) {
    render(
      <SurfaceModeContext.Provider value={{ canSwitch, requestChooser }}>
        <MemoryRouter initialEntries={["/"]}>
          <CustomerRoutes />
        </MemoryRouter>
      </SurfaceModeContext.Provider>,
    );
  }

  it("одноролевой клиент — анкета, и ничего лишнего", async () => {
    // Отрицательная половина: правка не меняет экран обычного клиента.
    // Выход с поверхности ему предлагать нечего и незачем.
    mockedFetch.mockResolvedValue(ASKING);
    renderRoot(false);

    expect(
      await screen.findByText("Что сейчас хочется привести в порядок?"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сменить режим" })).toBeNull();
  });

  it("многоролевой — ту же анкету, а не приветствие", async () => {
    // Положительная половина. Раньше здесь стоял `goalEntry={false}` и
    // проверялось ОБРАТНОЕ: админ-мастер получал `HelloScreen`. Из-за
    // этого владелец и вся команда не видели анкету вовсе.
    mockedFetch.mockResolvedValue(ASKING);
    renderRoot(true);

    expect(
      await screen.findByText("Что сейчас хочется привести в порядок?"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Здравствуйте/)).toBeNull();
  });

  it("многоролевой может уйти с анкеты, не создавая цели", async () => {
    // Причина прежнего запрета: с корня до «Сменить режим» было не
    // дойти. Замер прямой — выход нажат, чужой обработчик вызван, и
    // ни один ответ на вопрос анкеты для этого не понадобился.
    const requestChooser = vi.fn();
    mockedFetch.mockResolvedValue(ASKING);
    renderRoot(true, requestChooser);

    await screen.findByText("Что сейчас хочется привести в порядок?");
    await userEvent.click(screen.getByRole("button", { name: "Сменить режим" }));
    expect(requestChooser).toHaveBeenCalledTimes(1);
    expect(vi.mocked(postGoalSelect)).not.toHaveBeenCalled();
  });

  it("выход закреплён в панели, а не дописан в хвост документа", async () => {
    // Выход, который надо доскроллить, — выход, которого на экране
    // нет (тот же довод, что в DRF-1458 про кнопку `next`).
    mockedFetch.mockResolvedValue(ASKING);
    renderRoot(true);

    await screen.findByText("Что сейчас хочется привести в порядок?");
    const bar = screen.getByRole("region", { name: "Действие" });
    expect(
      within(bar).getByRole("button", { name: "Сменить режим" }),
    ).toBeInTheDocument();
  });

  it("выход и дорога дальше стоят рядом, а не вместо друг друга", async () => {
    // `ASKING` приходит без `next` — там в панели один выход. Здесь
    // сервер прислал и `next`: условие C-2 (DRF-1451) требует, чтобы
    // кнопка рисовалась всегда, когда она есть в документе, и правка
    // DRF-1469 не имеет права её вытеснить.
    mockedFetch.mockResolvedValue({
      ...ASKING,
      next: { id: "browse_catalog", label: "Найти услугу" },
    });
    renderRoot(true);

    await screen.findByText("Что сейчас хочется привести в порядок?");
    const bar = screen.getByRole("region", { name: "Действие" });
    expect(within(bar).getByRole("button", { name: "Найти услугу" })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Сменить режим" })).toBeInTheDocument();
    // Панель стала в два ряда — под ней надо освободить на ряд больше,
    // иначе она накроет хвост документа.
    expect(document.querySelector("main.screen--tall-cta")).not.toBeNull();
  });

  it("decision-context уходит С КОРНЯ, а не только с /customer/main", async () => {
    // Признак, по которому дефект и нашли в журнале nginx: с `/`
    // запрос не уходил ни разу, все обращения шли с `/customer/main`,
    // то есть от карточки на экране записей. Многоролевой — тот самый
    // случай, где этого запроса не было.
    mockedFetch.mockResolvedValue(ASKING);
    renderRoot(true);

    await screen.findByText("Что сейчас хочется привести в порядок?");
    expect(mockedFetch).toHaveBeenCalledTimes(1);
  });
});
