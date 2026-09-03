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
import { fetchDecisionContext, type DecisionContext } from "../lib/customer-goals";
import { setBackButton, signalReady } from "../lib/max-sdk";
import { CustomerEntryScreen } from "./CustomerEntryScreen";
import { CustomerRoutes } from "../App";

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

describe("кто получает анкету на корне, а кто нет", () => {
  function renderRoutes(props: { goalEntry?: boolean }) {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <CustomerRoutes {...props} />
      </MemoryRouter>,
    );
  }

  it("обычный клиент — получает", async () => {
    mockedFetch.mockResolvedValue(ASKING);
    renderRoutes({});
    expect(
      await screen.findByText("Что сейчас хочется привести в порядок?"),
    ).toBeInTheDocument();
  });

  it("админ-мастер на клиентской поверхности — НЕ получает", async () => {
    // `goalEntry={false}` — ровно то, что App.tsx передаёт на ветке
    // `multiRole && surfacePref === "customer"`.
    //
    // Он не человек, впервые встречающий Ayla, а владелец салона,
    // зашедший посмотреть. Своего `ClientGoal` у него нет, так что
    // анкету он получал бы первым же экраном; а `SurfaceSwitchButton`
    // смонтирована только на профиле и настройках, до которых с корня
    // без нижней навигации он уже не дойдёт. `useLastSurface` держит
    // выбор в localStorage — повторялось бы при каждом запуске.
    //
    // Тот же класс дефекта, что DRF-1349 и DRF-1434: роль есть, экран
    // показан не тот, и ошибки нигде не видно.
    mockedFetch.mockResolvedValue(ASKING);
    renderRoutes({ goalEntry: false });

    expect(await screen.findByText(/Здравствуйте/)).toBeInTheDocument();
    expect(screen.queryByText("Что сейчас хочется привести в порядок?")).toBeNull();
  });
});
