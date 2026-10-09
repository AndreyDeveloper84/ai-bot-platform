/**
 * «Мой план» — от шага сохранённого плана к услуге, времени и записи
 * (DRF-2876, часть 3).
 *
 * Тот же путь, что кнопками в чате, — без сообщений в чат. Подпись кнопки у
 * шага — слова владельца (лист решений 07.10, п.12: «Нужно выбрать услугу»),
 * взятые кнопкой временно; всё остальное на этом пути — слова каталога.
 *
 * Что сторожится:
 *   - у каждого шага действующего плана кнопка; у шагов предложения её нет;
 *   - нажатие спрашивает услуги ЭТОГО шага и показывает их словами каталога;
 *   - ничего не выбирается за человека: кнопка на каждой паре «услуга × мастер»;
 *   - выбор показывает свободное время; нажатие времени записывает;
 *   - после записи экран перечитывает план — время записи приходит с сервера;
 *   - у шага с записью кнопки нет, показано время, часы — из строки сервера;
 *   - отказ сервера показан именем с пометкой «тест», путь закрывается;
 *   - пока запрос идёт, кнопки недоступны.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { primeDisplayName } from "../components/CustomerAvatarEntry";

vi.mock("../lib/plan-engine", () => ({
  getSavedPlanState: vi.fn(),
  replacePlan: vi.fn(),
  keepCurrentPlan: vi.fn(),
  saveDraft: vi.fn(),
  stepOffers: vi.fn(),
  chooseStepOption: vi.fn(),
  chooseStepDay: vi.fn(),
  bookStepSlot: vi.fn(),
}));
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
vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return { ...original, fetchDiaryConsentGate: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { ApiError } from "../lib/api";
import { fetchDecisionContext, type DecisionContext } from "../lib/customer-goals";
import {
  bookStepSlot,
  chooseStepDay,
  chooseStepOption,
  getSavedPlanState,
  stepOffers,
  type PlanProposal,
  type SavedPlan,
  type SavedPlanStep,
  type StepOption,
} from "../lib/plan-engine";
import { getPlanLite, getPlanLiteProposal } from "../lib/plan-lite";
import { PLAN_LITE_ROUTE, PlanLiteScreen } from "./PlanLiteScreen";

const mockedState = vi.mocked(getSavedPlanState);
const mockedOffers = vi.mocked(stepOffers);
const mockedChoose = vi.mocked(chooseStepOption);
const mockedBook = vi.mocked(bookStepSlot);
const mockedDay = vi.mocked(chooseStepDay);
const mockedLite = vi.mocked(getPlanLite);
const mockedLiteProposal = vi.mocked(getPlanLiteProposal);
const mockedDoc = vi.mocked(fetchDecisionContext);

const CHOOSE = "Нужно выбрать услугу";

const DOC: DecisionContext = {
  version: 1,
  known: { goal: { goal_key: "tone_up", goal_text: null, selected_at: "2026-09-10T10:00:00Z", source_channel: "miniapp" } },
  missing: [],
  suggestions: [{ key: "tone_up", label: "Подтянуть фигуру" }],
  intents: [],
};

const SLEEP: SavedPlanStep = { step_id: "s-sleep", label: "Режим сна", why: null, booked_at: null };
const WALK: SavedPlanStep = { step_id: "s-walk", label: "Вечерняя прогулка", why: null, booked_at: null };
const PLAN: SavedPlan = { plan_id: "5a5a5a5a-1111-4222-8333-999999999999", steps: [SLEEP, WALK] };

const ANNA: StepOption = {
  service_name: "Консультация по режиму",
  salon_name: "Медный ковш",
  salon_city: "Пенза",
  master_name: "Анна",
  price: "2000.00",
  duration_minutes: 60,
  place_address: null,
  synthetic: true,
};
const OLGA: StepOption = {
  ...ANNA,
  master_name: "Ольга",
  price: "2500.00",
  duration_minutes: 90,
  place_address: "ул. Московская, 1",
  synthetic: false,
};

const SEARCH = "c0ffee00";
const SLOT_A = "2026-10-12T10:00:00+03:00";
const SLOT_B = "2026-10-12T11:30:00+03:00";
const SLOT_C = "2026-10-17T12:00:00+03:00";

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={[PLAN_LITE_ROUTE]}>
      <Routes>
        <Route path={PLAN_LITE_ROUTE} element={<PlanLiteScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function openSecondStep() {
  renderScreen();
  const card = await screen.findByTestId("plan-saved-card");
  fireEvent.click(within(card).getByRole("button", { name: `${CHOOSE}: Вечерняя прогулка` }));
  return screen.findByTestId("plan-step-offers");
}

beforeEach(() => {
  // Подпись дня зависит от «сегодня» (сегодняшний день подписан словом), а
  // дни в узлах названы датами — часы закреплены. Подменяется только Date:
  // таймеры настоящие, ожидания экрана работают как обычно.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-09T12:00:00+03:00"));
  vi.clearAllMocks();
  primeDisplayName("Тест Тестов");
  mockedDoc.mockResolvedValue(DOC);
  mockedLite.mockRejectedValue(new ApiError(404, "not_found", "none"));
  mockedLiteProposal.mockRejectedValue(new ApiError(404, "no_template", "none"));
  mockedState.mockResolvedValue({ plan: PLAN, proposal: null, draft: null });
  mockedOffers.mockResolvedValue({ token: SEARCH, options: [ANNA, OLGA] });
  mockedChoose.mockResolvedValue({
    token: SEARCH,
    option: OLGA,
    days: ["2026-10-12", "2026-10-14", "2026-10-17"],
    day: "2026-10-12",
    slots: [SLOT_A, SLOT_B],
  });
  mockedDay.mockResolvedValue({
    token: SEARCH,
    option: OLGA,
    days: ["2026-10-12", "2026-10-14", "2026-10-17"],
    day: "2026-10-17",
    slots: [SLOT_C],
  });
  mockedBook.mockResolvedValue(SLOT_B);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("«Мой план»: от шага к услуге, времени и записи", () => {
  it("у каждого шага действующего плана — кнопка словами владельца", async () => {
    renderScreen();

    const card = await screen.findByTestId("plan-saved-card");
    expect(within(card).getAllByRole("button").map((b) => b.textContent)).toEqual([CHOOSE, CHOOSE]);
    expect(mockedOffers).not.toHaveBeenCalled();
  });

  it("у шагов предложения кнопки выбора услуги нет — только у действующего плана", async () => {
    const proposal: PlanProposal = {
      plan_id: "7c1d2e3f-aaaa-4bbb-8ccc-ddddeeeeffff",
      replaces_plan_id: PLAN.plan_id,
      steps: [{ step_id: "n-1", label: "Растяжка", why: null }],
    };
    mockedState.mockResolvedValue({ plan: PLAN, proposal, draft: null });
    renderScreen();

    const card = await screen.findByTestId("plan-proposal-card");
    // Положительный контроль: шаг предложения показан, кнопки замены есть.
    expect(within(card).getByText("Растяжка")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Заменить план" })).toBeTruthy();
    expect(within(card).queryByRole("button", { name: /Нужно выбрать услугу/ })).toBeNull();
  });

  it("нажатие спрашивает услуги ЭТОГО шага и показывает их словами каталога", async () => {
    const offers = await openSecondStep();

    expect(mockedOffers).toHaveBeenCalledTimes(1);
    expect(mockedOffers).toHaveBeenCalledWith(PLAN, 1);
    // Панель — под тем шагом, который нажали.
    const step = offers.closest("li");
    expect(step?.textContent?.startsWith("Вечерняя прогулка")).toBe(true);
    expect(within(offers).getAllByText("Консультация по режиму — Медный ковш, Пенза")).toHaveLength(2);
    expect(within(offers).getByText("Анна · 2 000 ₽ · 1 ч")).toBeTruthy();
    expect(within(offers).getByText("Ольга · 2 500 ₽ · 1 ч 30 мин")).toBeTruthy();
    expect(within(offers).getByText("ул. Московская, 1")).toBeTruthy();
    // Пометка синтетики — только у услуги, которую каталог так пометил.
    expect(within(offers).getAllByText("Допущено для теста · синтетические данные")).toHaveLength(1);
  });

  it("кнопка — на каждой паре «услуга × мастер»; до нажатия ничего не выбрано", async () => {
    const offers = await openSecondStep();

    expect(within(offers).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Консультация по режиму · Анна",
      "Консультация по режиму · Ольга",
    ]);
    expect(mockedChoose).not.toHaveBeenCalled();
    expect(mockedBook).not.toHaveBeenCalled();
  });

  it("выбор услуги шлёт номер нажатого варианта и показывает свободное время", async () => {
    const offers = await openSecondStep();
    fireEvent.click(within(offers).getByRole("button", { name: "Консультация по режиму · Ольга" }));

    const slots = await screen.findByTestId("plan-step-slots");
    expect(mockedChoose).toHaveBeenCalledWith(SEARCH, 1);
    expect(within(slots).getByText("Ольга · 2 500 ₽ · 1 ч 30 мин")).toBeTruthy();
    expect(within(slots).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "12 октября в 10:00",
      "12 октября в 11:30",
      // Остальные дни со свободным временем — датами; показанного дня среди них нет.
      "14 октября, ср",
      "17 октября, сб",
    ]);
    expect(mockedBook).not.toHaveBeenCalled();
  });

  it("другой день: шлёт номер нажатого дня и показывает его время; запись — на него", async () => {
    const offers = await openSecondStep();
    fireEvent.click(within(offers).getByRole("button", { name: "Консультация по режиму · Ольга" }));
    const slots = await screen.findByTestId("plan-step-slots");

    fireEvent.click(within(slots).getByRole("button", { name: "17 октября, сб" }));

    const time = await screen.findByRole("button", { name: "17 октября в 12:00" });
    expect(mockedDay).toHaveBeenCalledTimes(1);
    expect(mockedDay).toHaveBeenCalledWith(SEARCH, 2);
    expect(mockedChoose).toHaveBeenCalledTimes(1);
    // Теперь на выбор — два других дня, показанный не дублируется.
    expect(within(screen.getByTestId("plan-step-days")).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "12 октября, пн",
      "14 октября, ср",
    ]);
    expect(screen.queryByRole("button", { name: "12 октября в 10:00" })).toBeNull();

    fireEvent.click(time);
    await waitFor(() => expect(mockedBook).toHaveBeenCalledWith(SEARCH, 0));
  });

  it("в выбранном дне времени уже нет — остальные дни остаются на выбор", async () => {
    mockedDay.mockResolvedValue({
      token: SEARCH,
      option: OLGA,
      days: ["2026-10-12", "2026-10-14", "2026-10-17"],
      day: "2026-10-17",
      slots: [],
    });
    const offers = await openSecondStep();
    fireEvent.click(within(offers).getByRole("button", { name: "Консультация по режиму · Ольга" }));
    const slots = await screen.findByTestId("plan-step-slots");

    fireEvent.click(within(slots).getByRole("button", { name: "17 октября, сб" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "12 октября в 10:00" })).toBeNull());
    expect(within(screen.getByTestId("plan-step-days")).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "12 октября, пн",
      "14 октября, ср",
    ]);
  });

  it("свободный день один — кнопок других дней нет", async () => {
    mockedChoose.mockResolvedValue({
      token: SEARCH,
      option: OLGA,
      days: ["2026-10-12"],
      day: "2026-10-12",
      slots: [SLOT_A],
    });
    const offers = await openSecondStep();
    fireEvent.click(within(offers).getByRole("button", { name: "Консультация по режиму · Ольга" }));

    // Положительный контроль: время показано.
    expect(await screen.findByRole("button", { name: "12 октября в 10:00" })).toBeTruthy();
    expect(screen.queryByTestId("plan-step-days")).toBeNull();
  });

  it("нажатие времени записывает и перечитывает план — время записи приходит с сервера", async () => {
    const offers = await openSecondStep();
    fireEvent.click(within(offers).getByRole("button", { name: "Консультация по режиму · Ольга" }));
    const slots = await screen.findByTestId("plan-step-slots");
    mockedState.mockResolvedValue({
      plan: { ...PLAN, steps: [SLEEP, { ...WALK, booked_at: SLOT_B }] },
      proposal: null,
      draft: null,
    });

    fireEvent.click(within(slots).getByRole("button", { name: "12 октября в 11:30" }));

    const booked = await screen.findByTestId("plan-step-booked-at");
    expect(mockedBook).toHaveBeenCalledTimes(1);
    expect(mockedBook).toHaveBeenCalledWith(SEARCH, 1);
    expect(mockedState).toHaveBeenCalledTimes(2);
    expect(booked.textContent).toBe("12 октября в 11:30");
    expect(screen.queryByTestId("plan-step-slots")).toBeNull();
  });

  it("у шага с записью кнопки нет; часы — из строки сервера, не из часов устройства", async () => {
    mockedState.mockResolvedValue({
      // Смещение +05:00 нарочно: часы устройства дали бы другое время.
      plan: { ...PLAN, steps: [{ ...SLEEP, booked_at: "2026-10-12T10:00:00+05:00" }, WALK] },
      proposal: null,
      draft: null,
    });
    renderScreen();

    const card = await screen.findByTestId("plan-saved-card");
    expect(within(card).getByTestId("plan-step-booked-at").textContent).toBe("12 октября в 10:00");
    // Кнопка осталась только у шага без записи.
    expect(within(card).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
      `${CHOOSE}: Вечерняя прогулка`,
    ]);
  });

  it("отказ на подборе показан именем с пометкой «тест», панель не открыта", async () => {
    mockedOffers.mockRejectedValue(new ApiError(409, "plan_safety_unavailable", "no verdict"));
    renderScreen();
    const card = await screen.findByTestId("plan-saved-card");
    fireEvent.click(within(card).getByRole("button", { name: `${CHOOSE}: Режим сна` }));

    expect(await screen.findByText("PLAN_SAFETY_UNAVAILABLE · тест")).toBeTruthy();
    expect(screen.queryByTestId("plan-step-offers")).toBeNull();
    // План на экране остался, его кнопки снова доступны.
    expect(within(card).getAllByRole("button").every((b) => !(b as HTMLButtonElement).disabled)).toBe(true);
  });

  it("причина каталога показана его именем", async () => {
    mockedOffers.mockRejectedValue(new ApiError(409, "no_offer", "nothing"));
    renderScreen();
    const card = await screen.findByTestId("plan-saved-card");
    fireEvent.click(within(card).getByRole("button", { name: `${CHOOSE}: Режим сна` }));

    expect(await screen.findByText("NO_OFFER · тест")).toBeTruthy();
  });

  it("отказ на записи закрывает путь: устаревшие кнопки времени не остаются", async () => {
    mockedBook.mockRejectedValue(new ApiError(409, "plan_step_expired", "stale"));
    const offers = await openSecondStep();
    fireEvent.click(within(offers).getByRole("button", { name: "Консультация по режиму · Анна" }));
    const slots = await screen.findByTestId("plan-step-slots");

    fireEvent.click(within(slots).getByRole("button", { name: "12 октября в 10:00" }));

    expect(await screen.findByText("PLAN_STEP_EXPIRED · тест")).toBeTruthy();
    expect(screen.queryByTestId("plan-step-slots")).toBeNull();
    expect(screen.queryByTestId("plan-step-booked-at")).toBeNull();
  });

  it("пока запрос идёт, кнопки недоступны — второе нажатие не шлёт второй раз", async () => {
    let release: (value: { token: string; options: StepOption[] }) => void = () => undefined;
    mockedOffers.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    renderScreen();
    const card = await screen.findByTestId("plan-saved-card");
    const first = within(card).getByRole("button", { name: `${CHOOSE}: Режим сна` });
    fireEvent.click(first);

    await waitFor(() => expect((first as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(within(card).getByRole("button", { name: `${CHOOSE}: Вечерняя прогулка` }));
    expect(mockedOffers).toHaveBeenCalledTimes(1);

    release({ token: SEARCH, options: [ANNA] });
    expect(await screen.findByTestId("plan-step-offers")).toBeTruthy();
  });
});
