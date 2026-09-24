/**
 * «Перейти» в карточке плана: куда зовём и когда молчим (DRF-2441).
 *
 * Лист заведён по живому заходу владельца: «кнопка у воды ведёт в
 * никуда». Замер это НЕ подтвердил: маршрут `log_water` →
 * `/customer/main` верен, вода отмечается именно там (очередь, тост,
 * отмена стакана — `CustomerWellnessDashboardScreen`). Владелец видел
 * симптом, а не механизм.
 *
 * Два правила, оба общие, оба названы в коде (`shouldOfferGo`):
 *
 *   1. не зовём с того экрана, на котором человек уже стоит. СЕГОДНЯ это
 *      правило не срабатывает ни разу: карточка живёт только на
 *      `/customer/plan`, а туда не ведёт ни одно из трёх действий. Узел
 *      «правило пока сторож» это фиксирует — и покраснеет в тот день,
 *      когда карточку покажут на экране, куда она же и зовёт;
 *   2. выполненное РАЗОВОЕ обязательство не зовёт сделать его ещё раз;
 *      выполненное повторяемое — зовёт. «Повторяемое» — названное
 *      свойство `ACTION_REPEATABLE`, не список слагов: четвёртое
 *      действие обяжет автора ответить на вопрос, а не угадать по имени.
 */
import { act, configure, getConfig, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/plan-lite", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/plan-lite")>();
  return {
    ...original,
    getPlanLite: vi.fn(),
    getPlanLiteProposal: vi.fn(),
    createPlanLite: vi.fn(),
    closePlanLite: vi.fn(),
  };
});
vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return { ...original, fetchDecisionContext: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { ApiError } from "../lib/api";
import { fetchDecisionContext, type DecisionContext } from "../lib/customer-goals";
import { getPlanLite, getPlanLiteProposal, type PlanLite, type PlanLiteAction } from "../lib/plan-lite";
import { PLAN_LITE_ROUTE, PlanLiteScreen, shouldOfferGo } from "./PlanLiteScreen";

const GUARD_ASYNC_TIMEOUT_MS = 20;
let previousAsyncUtilTimeout = 1000;
beforeAll(() => {
  previousAsyncUtilTimeout = getConfig().asyncUtilTimeout;
  configure({ asyncUtilTimeout: GUARD_ASYNC_TIMEOUT_MS });
});
afterAll(() => {
  configure({ asyncUtilTimeout: previousAsyncUtilTimeout });
  vi.unstubAllEnvs();
});

const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

const DOC: DecisionContext = {
  version: 1,
  known: {
    goal: {
      goal_key: "tone_up",
      goal_text: null,
      selected_at: "2026-09-10T10:00:00Z",
      source_channel: "miniapp",
    },
  },
  missing: [],
  suggestions: [{ key: "tone_up", label: "Подтянуть фигуру" }],
  intents: [],
};

const WEEK = { start: "2026-09-14", end: "2026-09-21" };
const DAY = { start: "2026-09-18", end: "2026-09-19" };

function book(done: number): PlanLiteAction {
  return { action_type: "book_service", cadence: "per_week", target_count: 1, done_count: done, bucket: WEEK };
}
function water(done: number): PlanLiteAction {
  return { action_type: "log_water", cadence: "per_day", target_count: 7, done_count: done, bucket: DAY };
}
function food(done: number): PlanLiteAction {
  return { action_type: "log_food", cadence: "per_week", target_count: 3, done_count: done, bucket: WEEK };
}

function plan(...actions: PlanLiteAction[]): PlanLite {
  return { plan_id: "p-2441", goal_key: "tone_up", actions };
}

async function card(p: PlanLite): Promise<HTMLElement> {
  vi.mocked(getPlanLite).mockResolvedValue(p);
  render(
    <MemoryRouter initialEntries={[PLAN_LITE_ROUTE]}>
      <Routes>
        <Route path={PLAN_LITE_ROUTE} element={<PlanLiteScreen />} />
      </Routes>
    </MemoryRouter>,
  );
  await settle();
  return screen.getByTestId("plan-lite-card");
}

/** Подписи кнопок «Перейти», по которым видно, у КОГО она есть. */
function goLabels(): string[] {
  return screen
    .queryAllByRole("button", { name: /^Перейти: / })
    .map((b) => b.getAttribute("aria-label") ?? "");
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("VITE_PLAN_LITE", "1");
  vi.mocked(fetchDecisionContext).mockResolvedValue(DOC);
  vi.mocked(getPlanLiteProposal).mockRejectedValue(new ApiError(404, "no_template", "none"));
});

describe("кнопка «Перейти» у обязательств плана", () => {
  it("невыполненное зовёт — у всех трёх", async () => {
    // Утверждение о наличии раньше всех утверждений об отсутствии: иначе
    // «у брони кнопки нет» было бы правдой и о карточке без кнопок вовсе.
    await card(plan(book(0), food(1), water(4)));

    expect(goLabels()).toEqual([
      "Перейти: Записаться на услугу",
      "Перейти: Дневник",
      "Перейти: Вода",
    ]);
  });

  it("выполненное РАЗОВОЕ не зовёт записаться ещё раз", async () => {
    await card(plan(book(1), food(1), water(4)));

    const labels = goLabels();
    expect(labels).toContain("Перейти: Дневник"); // соседи на месте
    expect(labels).toContain("Перейти: Вода");
    expect(labels).not.toContain("Перейти: Записаться на услугу");
  });

  it("выполненное ПОВТОРЯЕМОЕ зовёт по-прежнему", async () => {
    // Лишний стакан — не ошибка, и «7 из 7» не повод убирать вход.
    await card(plan(water(7), food(3)));

    expect(goLabels()).toEqual(["Перейти: Вода", "Перейти: Дневник"]);
  });

  it("выполненная норма видна и без кнопки", async () => {
    // Скрываем вход, а не факт: человек должен видеть, что норма набрана.
    const el = await card(plan(book(1)));

    expect(el).toHaveTextContent("1 из 1");
    expect(goLabels()).toEqual([]);
  });
});

describe("правило «не зовём туда, где человек уже стоит»", () => {
  it("целевой маршрут равен текущему → кнопки нет даже у невыполненного", () => {
    expect(shouldOfferGo(water(0), "/customer/main")).toBe(false);
    expect(shouldOfferGo(food(0), "/customer/food-scanner/diary")).toBe(false);
    expect(shouldOfferGo(book(0), "/customer/catalog")).toBe(false);
  });

  it("на другом экране то же обязательство зовёт", () => {
    expect(shouldOfferGo(water(0), PLAN_LITE_ROUTE)).toBe(true);
    expect(shouldOfferGo(food(0), "/customer/main")).toBe(true);
  });

  it("сегодня правило — сторож: с экрана плана никуда не ведёт само в себя", () => {
    // Замер, а не догадка: ни одно из трёх действий не ведёт на
    // `/customer/plan`, значит правило 1 на этом экране НЕ СРАБАТЫВАЕТ
    // ни разу. Узел покраснеет, когда карточку покажут на экране, куда
    // она же и зовёт, — и тогда правило станет живым, а не сторожем.
    for (const action of [book(0), food(0), water(0)]) {
      expect(shouldOfferGo(action, PLAN_LITE_ROUTE)).toBe(true);
    }
  });
});
