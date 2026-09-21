/**
 * C03 в живой путь — экран по макету DRF-1178 (DRF-2177, К-2 эпика DRF-2175).
 *
 * Документ теперь строит каталог (beautygo_backend #517): у человека с
 * целью — первый сужающий вопрос сразу, семь целей скрыты, `next` молчит
 * при вопросах и говорит `return_to_chat`, когда контекст собран. Экран
 * остаётся тупым рендерером; что здесь его собственное:
 *
 * 1. «Уже учла: цель · Изменить» с ПЕРВОГО кадра — не «Текущая цель»;
 * 2. кадр C03.5: ✓ + «Спасибо! Этого достаточно, чтобы подобрать тебе
 *    подходящий шаг.» — и авто-переход: в MAX — `closeApp()` (в чат),
 *    вне MAX — на корень H01; кнопки «Вернуться в чат» нет;
 * 3. на кадрах с целью заголовка нет (макет: только «Ayla»); на шаге цели —
 *    «Какая у тебя цель?» как прежде;
 * 4. «Найти услугу»/«Посмотреть услуги» с экрана уходят (§60): при
 *    вопросе выхода в каталог нет, пол — «назад» на H01 + свободный ввод;
 * 5. «Сменить режим» — только на корне (там нет «назад», DRF-1469);
 *    не на корне — уходит;
 * 6. прежний документ (`browse_catalog`) экран рисует как раньше —
 *    выкладка каталога и бота не одновременная.
 *
 * Чат → C03 — целевой UX-контракт, не runtime (ruling §61). Здесь не строится.
 */
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return { ...original, fetchDecisionContext: vi.fn(), postGoalSelect: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, maxBridge: vi.fn(() => null), returnToChat: vi.fn(() => "closed") };
});

import { SurfaceModeContext } from "../components/SurfaceSwitch";
import { fetchDecisionContext, postGoalSelect, type DecisionContext } from "../lib/customer-goals";
import { maxBridge, returnToChat } from "../lib/max-sdk";
import { COMPLETION_AUTO_MS, COMPLETION_TEXT, GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);
const mockedBridge = vi.mocked(maxBridge);
const mockedClose = vi.mocked(returnToChat);

const INTENTS: DecisionContext["intents"] = [
  { id: "choose_suggested", label: "Выбрать из предложенного" },
  { id: "formulate_own", label: "Рассказать своими словами" },
  { id: "need_guidance", label: "Не понимаю, чего хочу" },
  { id: "start_anketa", label: "Пройти анкету заново" },
];

const GOAL = {
  goal_key: "self_care",
  goal_text: null,
  label: "Привести себя в порядок",
  selected_at: "2026-09-20T09:00:00Z",
  source_channel: "miniapp" as const,
};

/** Снимок владельца после каталога #517: цель есть, первый вопрос сразу. */
const FROM_GOAL: DecisionContext = {
  version: 2,
  known: { goal: GOAL, anketa: [] },
  missing: [
    {
      kind: "goal_anketa",
      prompt: "Что хочется наладить в первую очередь?",
      step: "area",
      options: [
        { key: "face", label: "Лицо и кожа" },
        { key: "unknown", label: "Не знаю", role: "escape" },
      ],
      allow_free_text: false,
      mode: "single",
      progress: { index: 2, total: 3, is_last: false },
    },
  ],
  suggestions: [],
  intents: INTENTS,
  next: null,
};

/** Контекст собран — C03.5. */
const COLLECTED: DecisionContext = {
  ...FROM_GOAL,
  missing: [],
  next: { id: "return_to_chat", label: "Вернуться в чат" },
};

/** Прежний документ каталога — до выкладки #517. */
const LEGACY: DecisionContext = {
  ...FROM_GOAL,
  missing: [],
  suggestions: [{ key: "relax", label: "Расслабиться" }],
  next: { id: "browse_catalog", label: "Найти услугу" },
};

/** Шаг цели — корень для того, у кого цели нет. */
const GOAL_STEP: DecisionContext = {
  version: 2,
  known: { goal: null, anketa: [] },
  missing: [
    {
      kind: "goal_anketa",
      prompt: "Выбери цель — или напиши своими словами, чего хочешь.",
      step: "goal",
      options: [{ key: "relax", label: "Расслабиться" }],
      allow_free_text: true,
      mode: "single",
      progress: { index: 1, total: 3, is_last: false },
    },
  ],
  suggestions: [],
  intents: INTENTS.slice(0, 3),
  next: null,
};

function renderAt(path: string, canSwitch = false) {
  render(
    <SurfaceModeContext.Provider value={{ canSwitch, requestChooser: () => {} }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={<GoalSelectScreen />} />
          <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
          <Route path="/customer/main" element={<div>ГЛАВНЫЙ H01</div>} />
          <Route path="/customer/catalog" element={<div>ЭКРАН ПОДБОРА</div>} />
        </Routes>
      </MemoryRouter>
    </SurfaceModeContext.Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedClose.mockReturnValue("closed");
  mockedBridge.mockReturnValue(null);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("первый кадр с целью — «Уже учла» и вопрос (C03.2)", () => {
  it("«Уже учла: цель · Изменить» стоит над первым вопросом, «Текущей цели» нет", async () => {
    mockedFetch.mockResolvedValue(FROM_GOAL);
    renderAt("/customer/goal-select");

    expect(await screen.findByText("Что хочется наладить в первую очередь?")).toBeInTheDocument();
    const noted = screen.getByRole("region", { name: "Уже учла" });
    expect(within(noted).getByText("Привести себя в порядок")).toBeInTheDocument();
    expect(within(noted).getByRole("button", { name: /Изменить/ })).toBeInTheDocument();
    expect(screen.queryByText("Текущая цель")).toBeNull();
  });

  it("«Изменить» у цели — повторный проход (семь целей только за ним)", async () => {
    mockedFetch.mockResolvedValue(FROM_GOAL);
    mockedPost.mockResolvedValue(GOAL_STEP);
    renderAt("/customer/goal-select");

    const noted = await screen.findByRole("region", { name: "Уже учла" });
    await userEvent.click(within(noted).getByRole("button", { name: /Изменить/ }));
    expect(mockedPost).toHaveBeenCalledWith({ intent: "start_anketa", source_channel: "miniapp" });
    // Ряда целей на кадре вопроса не было — он пришёл шагом цели.
    expect(await screen.findByRole("button", { name: "Расслабиться" })).toBeInTheDocument();
  });

  it("на кадре с целью заголовка «Какая у тебя цель?» нет; на шаге цели — есть", async () => {
    mockedFetch.mockResolvedValue(FROM_GOAL);
    renderAt("/customer/goal-select");
    await screen.findByText("Что хочется наладить в первую очередь?");
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });

  it("шаг цели (нет цели) — заголовок как прежде", async () => {
    mockedFetch.mockResolvedValue(GOAL_STEP);
    renderAt("/customer/goal-select");
    await screen.findByText("Выбери цель — или напиши своими словами, чего хочешь.");
    expect(screen.getByRole("heading", { level: 1, name: "Какая у тебя цель?" })).toBeInTheDocument();
  });

  it("выхода в каталог на кадре вопроса нет — пол: «назад» на H01 и свободный ввод", async () => {
    mockedFetch.mockResolvedValue(FROM_GOAL);
    renderAt("/customer/goal-select");
    await screen.findByText("Что хочется наладить в первую очередь?");

    expect(screen.getByRole("textbox", { name: "Рассказать своими словами" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Найти услугу" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Посмотреть услуги" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Назад" }));
    expect(screen.getByText("ГЛАВНЫЙ H01")).toBeInTheDocument();
  });
});

describe("кадр C03.5 — контекст собран", () => {
  it("✓ + текст макета, кнопки «Вернуться в чат» нет", async () => {
    mockedFetch.mockResolvedValue(COLLECTED);
    renderAt("/customer/goal-select");

    expect(await screen.findByText(COMPLETION_TEXT)).toBeInTheDocument();
    expect(COMPLETION_TEXT).toBe("Спасибо! Этого достаточно, чтобы подобрать тебе подходящий шаг.");
    expect(screen.queryByRole("button", { name: "Вернуться в чат" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Найти услугу" })).toBeNull();
    // Ничего кликабельного (макет C03.5): «Изменить» на этом кадре нет.
    expect(screen.queryByRole("button", { name: /Изменить/ })).toBeNull();
  });

  it("внутри MAX через паузу закрывает мини-апп — человек возвращается в чат", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedBridge.mockReturnValue({ close: () => {} } as never);
    mockedFetch.mockResolvedValue(COLLECTED);
    renderAt("/customer/goal-select");

    expect(await screen.findByText(COMPLETION_TEXT)).toBeInTheDocument();
    expect(mockedClose).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(COMPLETION_AUTO_MS + 10);
    });
    expect(mockedClose).toHaveBeenCalledTimes(1);
  });

  it("вернуться в чат нечем (DRF-2268: returnToChat → stuck) — не виснем, уходим на главный", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedBridge.mockReturnValue({} as never);
    mockedClose.mockReturnValue("stuck");
    mockedFetch.mockResolvedValue(COLLECTED);
    renderAt("/customer/goal-select");
    await screen.findByText(COMPLETION_TEXT);
    await act(async () => {
      vi.advanceTimersByTime(COMPLETION_AUTO_MS + 10);
    });
    expect(mockedClose).toHaveBeenCalledTimes(1);
    expect(screen.getByText("ГЛАВНЫЙ H01")).toBeInTheDocument();
  });

  it("диалог открыт ссылкой (opened_chat) — на главный не уходим", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedClose.mockReturnValue("opened_chat");
    mockedFetch.mockResolvedValue(COLLECTED);
    renderAt("/customer/goal-select");
    await screen.findByText(COMPLETION_TEXT);
    await act(async () => {
      vi.advanceTimersByTime(COMPLETION_AUTO_MS + 10);
    });
    expect(mockedClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("ГЛАВНЫЙ H01")).toBeNull();
  });

  it("вне MAX через паузу уходит на главный H01", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedClose.mockReturnValue("stuck");
    mockedFetch.mockResolvedValue(COLLECTED);
    renderAt("/customer/goal-select");

    expect(await screen.findByText(COMPLETION_TEXT)).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(COMPLETION_AUTO_MS + 10);
    });
    expect(screen.getByText("ГЛАВНЫЙ H01")).toBeInTheDocument();
  });
});

describe("совместимость и режимы", () => {
  it("прежний документ каталога рисуется как раньше — «Найти услугу» ведёт в каталог", async () => {
    mockedFetch.mockResolvedValue(LEGACY);
    renderAt("/customer/goal-select");
    await userEvent.click(await screen.findByRole("button", { name: "Найти услугу" }));
    expect(screen.getByText("ЭКРАН ПОДБОРА")).toBeInTheDocument();
  });

  it("«Сменить режим» на корне у многоролевого есть — там нет «назад»", async () => {
    mockedFetch.mockResolvedValue(GOAL_STEP);
    renderAt("/", true);
    await screen.findByText("Выбери цель — или напиши своими словами, чего хочешь.");
    expect(screen.getByRole("button", { name: "Сменить режим" })).toBeInTheDocument();
  });

  it("«Сменить режим» на корне остаётся, даже если контекст не загрузился (DRF-2198)", async () => {
    // Правило класса после инцидента 20.09: состояние ошибки не убирает
    // навигацию. У многоролевого это единственный выход обратно к мастеру.
    mockedFetch.mockRejectedValue(new Error("boom"));
    renderAt("/", true);
    expect(
      await screen.findByRole("button", { name: "Сменить режим" }, { timeout: 4000 }),
    ).toBeInTheDocument();
  });

  it("«Сменить режим» не на корне уходит с экрана (§60)", async () => {
    mockedFetch.mockResolvedValue(FROM_GOAL);
    renderAt("/customer/goal-select", true);
    await screen.findByText("Что хочется наладить в первую очередь?");
    expect(screen.getByRole("button", { name: "Назад" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сменить режим" })).toBeNull();
  });
});
