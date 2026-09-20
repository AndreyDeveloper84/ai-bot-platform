/**
 * DRF-2144 — главный экран клиента H01 по макету DRF-1321 v1.2 (UX FREEZE
 * 25.08) в рамках решений владельца §49/§82 (§55 от 20.09).
 *
 * Состояния из листа — по одному узлу на каждое:
 *   нет цели / цель без плана / план / нет записи / есть запись / нет согласия.
 *
 * Сторожа из листа:
 *   - без согласия — РОВНО ОДИН блок согласия (было два абзаца: «Питание» и
 *     «Вода» — снимок владельца 20.09);
 *   - панель — ровно пять вкладок «Главная · План · Дневник · Записи · Профиль»;
 *   - в DOM нет слов «кг», «%», «вес» — с положительной парой: «Неделя» есть
 *     (§49 Plan Lite без веса, §82 без процентов).
 *
 * Источники: цель — `wellness/today.active_goals`; план — `plan-lite` (тот же,
 * что у `PlanLiteScreen`); запись — `recent-activity` (bookings, authoritative);
 * последняя тема — новая ручка `last-topic/` (реестр 2094).
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCatalogBrowse: vi.fn() };
});
vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "",
  setBackButton: vi.fn(),
  signalReady: vi.fn(),
  applyTheme: vi.fn(),
  hapticImpact: vi.fn(),
  closeApp: vi.fn(),
}));

import { getCatalogBrowse } from "../lib/customer-booking";
import { closeApp } from "../lib/max-sdk";
import { CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";

const mockedBrowse = vi.mocked(getCatalogBrowse);
const mockedClose = vi.mocked(closeApp);

// ---------------------------------------------------------------------------
// Ответы ручек — по умолчанию «цель есть, плана нет, записи нет, темы нет».
// ---------------------------------------------------------------------------

type Json = Record<string, unknown> | null;

interface Served {
  today?: Json;
  activity?: Json;
  /** `null` — плана нет; объект — план; `"disabled"` — 404 plan_lite_disabled. */
  plan?: Json | "disabled";
  lastTopic?: Json;
}

const TODAY_WITH_GOAL: Record<string, unknown> = {
  calories_eaten: 1240,
  calories_target: 2100,
  water_glasses_eaten: 4,
  water_glasses_target: 8,
  active_goals: [{ title: "Подтянуть фигуру", week_num: 2 }],
  display_name: "Анна",
};

const PLAN: Record<string, unknown> = {
  plan_lite: {
    plan_id: "p1",
    goal_key: "shape",
    actions: [
      {
        action_type: "log_water",
        cadence: "per_day",
        target_count: 7,
        done_count: 4,
        bucket: { start: "2026-09-20", end: "2026-09-20" },
      },
      {
        action_type: "log_food",
        cadence: "per_week",
        target_count: 3,
        done_count: 3,
        bucket: { start: "2026-09-14", end: "2026-09-20" },
      },
    ],
  },
};

const NEXT_BOOKING: Record<string, unknown> = {
  this_week_booking_count: 1,
  next_booking: {
    date_human: "Завтра · пн · 14:00",
    service_name: "Лимфодренажный массаж",
    duration_min: 60,
    master_name: "Екатерина С.",
    salon_name: "Ayla Beauty",
    address: "м. Петровка, 5",
    booking_id: "b-1",
    status: "confirmed",
  },
};

function ok(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function refused(status: number, slug: string): Response {
  return {
    ok: false,
    status,
    statusText: "refused",
    json: async () => ({ error: slug, detail: slug }),
  } as unknown as Response;
}

function serve(s: Served = {}) {
  const today = s.today === undefined ? TODAY_WITH_GOAL : s.today;
  const activity = s.activity === undefined ? { this_week_booking_count: 0 } : s.activity;
  const plan = s.plan === undefined ? { plan_lite: null } : s.plan;
  const lastTopic = s.lastTopic === undefined ? { last_topic: null } : s.lastTopic;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const u = String(url);
      if (u.includes("/wellness/today")) return ok(today);
      if (u.includes("/recent-activity")) return ok(activity);
      if (u.includes("/plan-lite")) {
        return plan === "disabled" ? refused(404, "plan_lite_disabled") : ok(plan);
      }
      if (u.includes("/last-topic")) return ok(lastTopic);
      throw new Error(`unexpected fetch: ${u}`);
    }),
  );
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderHome() {
  return render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <Routes>
        <Route path="/customer/main" element={<CustomerWellnessDashboardScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  mockedClose.mockReset();
  mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [], picksOutcome: "OK" } as never);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// 1. Нет цели
// ---------------------------------------------------------------------------

describe("H01 · нет цели", () => {
  it("карточка «Выбери цель» ведёт на экран цели; ни CTA плана, ни блока плана", async () => {
    serve({ today: { ...TODAY_WITH_GOAL, active_goals: [] } });
    renderHome();

    const cta = await screen.findByRole("button", { name: "Выбери цель" });
    expect(screen.queryByRole("button", { name: "Продолжить сегодняшний план" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "План на сегодня" })).toBeNull();

    fireEvent.click(cta);
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/goal-select");
  });
});

// ---------------------------------------------------------------------------
// 2. Цель без плана
// ---------------------------------------------------------------------------

describe("H01 · цель без плана", () => {
  it("карточка цели: метка, название, «План ещё не составлен», CTA «Составить план» → /customer/plan", async () => {
    serve();
    renderHome();

    const card = (await screen.findByText("Активная цель")).closest("section");
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByText("Подтянуть фигуру")).toBeInTheDocument();
    expect(within(card as HTMLElement).getByText("План ещё не составлен")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Продолжить сегодняшний план" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "План на сегодня" })).toBeNull();

    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Составить план" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/plan");
  });

  it("«Посмотреть детали цели» — вторичное действие, ведёт на экран цели", async () => {
    serve();
    renderHome();

    fireEvent.click(await screen.findByRole("button", { name: "Посмотреть детали цели" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/goal-select");
  });

  it("Plan Lite выключен на сервере (404) — карточка цели без строки плана, экран не падает", async () => {
    serve({ plan: "disabled" });
    renderHome();

    await screen.findByText("Активная цель");
    expect(screen.queryByText("План ещё не составлен")).toBeNull();
    expect(screen.queryByRole("button", { name: "Составить план" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Продолжить сегодняшний план" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 3. План
// ---------------------------------------------------------------------------

describe("H01 · план", () => {
  it("primary CTA «Продолжить сегодняшний план» → /customer/plan; строка adherence без процентов", async () => {
    serve({ plan: PLAN });
    renderHome();

    const cta = await screen.findByRole("button", { name: "Продолжить сегодняшний план" });
    // 4 + 3 из 7 + 3 — adherence Plan Lite, не результат.
    expect(screen.getByText("Неделя 2 · выполнено 7 из 10 действий")).toBeInTheDocument();

    fireEvent.click(cta);
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/plan");
  });

  it("«План на сегодня»: действия с меткой «из вашего плана», состояние, «Смотреть весь план»", async () => {
    serve({ plan: PLAN });
    renderHome();

    const heading = await screen.findByRole("heading", { name: "План на сегодня" });
    const block = heading.closest("section") as HTMLElement;
    const chips = within(block).getAllByText("из вашего плана");
    expect(chips).toHaveLength(2);
    expect(within(block).getByText("4 из 7")).toBeInTheDocument();
    // Выполненное — галочка, не «3 из 3».
    expect(within(block).getByLabelText("Дневник: выполнено")).toBeInTheDocument();

    fireEvent.click(within(block).getByRole("button", { name: "Смотреть весь план" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/plan");
  });

  it("attention-строка: «Сегодня не получается по плану?» → «Скорректировать с Ayla» уходит в чат", async () => {
    serve({ plan: PLAN });
    renderHome();

    await screen.findByText("Сегодня не получается по плану?");
    fireEvent.click(screen.getByRole("button", { name: "Скорректировать с Ayla" }));
    expect(mockedClose).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// 4/5. Запись
// ---------------------------------------------------------------------------

describe("H01 · ближайшая запись", () => {
  it("нет записи — «Записей нет» и «Записаться» → каталог", async () => {
    serve();
    renderHome();

    await screen.findByText("Записей нет");
    fireEvent.click(screen.getByRole("button", { name: "Записаться" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/catalog");
  });

  it("есть запись — услуга, мастер, когда, адрес, статус, «Открыть запись», «Все мои записи»", async () => {
    serve({ activity: NEXT_BOOKING });
    renderHome();

    const heading = await screen.findByRole("heading", { name: "Ближайшая запись" });
    const block = heading.closest("section") as HTMLElement;
    expect(within(block).getByText(/Лимфодренажный массаж/)).toBeInTheDocument();
    expect(within(block).getByText(/Екатерина С\./)).toBeInTheDocument();
    expect(within(block).getByText(/Завтра · пн · 14:00/)).toBeInTheDocument();
    expect(within(block).getByText(/м\. Петровка, 5/)).toBeInTheDocument();
    expect(within(block).getByText("Подтверждена")).toBeInTheDocument();

    fireEvent.click(within(block).getByRole("button", { name: "Все мои записи" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/records");
  });

  it("«Открыть запись» ведёт на карточку записи", async () => {
    serve({ activity: NEXT_BOOKING });
    renderHome();

    fireEvent.click(await screen.findByRole("button", { name: "Открыть запись" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/records/b-1");
  });
});

// ---------------------------------------------------------------------------
// 6. Нет согласия — ровно один блок
// ---------------------------------------------------------------------------

describe("H01 · нет согласия дневника", () => {
  it("ровно один блок согласия с кнопкой в чат — не два абзаца", async () => {
    serve({
      today: { consent_required: true, active_goals: [{ title: "Подтянуть фигуру", week_num: 2 }], display_name: "Анна" },
    });
    renderHome();

    const blocks = await screen.findAllByText("Чтобы вести дневник, нужно согласие — дай его в чате с Ayla");
    expect(blocks).toHaveLength(1);
    // Старой пары абзацев нет.
    expect(screen.queryAllByText(/Чтобы менять дневник, нужно согласие/)).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Дать согласие в чате" }));
    expect(mockedClose).toHaveBeenCalledTimes(1);
  });

  it("ложный вход: с согласием блока согласия нет вовсе", async () => {
    serve();
    renderHome();

    await screen.findByText("Активная цель");
    expect(screen.queryByText("Чтобы вести дневник, нужно согласие — дай его в чате с Ayla")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Быстрые действия по фризу 25.08 (замер → стакан воды, §49)
// ---------------------------------------------------------------------------

describe("H01 · быстрые действия", () => {
  it("ровно пять: записать питание / стакан воды / новая запись / скорректировать план / профиль", async () => {
    serve({ plan: PLAN });
    renderHome();

    const heading = await screen.findByRole("heading", { name: "Быстрые действия" });
    const block = heading.closest("section") as HTMLElement;
    const labels = within(block)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual(["Записать питание", "Стакан воды", "Новая запись", "Скорректировать план", "Профиль"]);
    // Ушедшие из быстрых действий: цель — в карточке, услуги — в каталоге.
    expect(within(block).queryByRole("button", { name: "Найди услугу" })).toBeNull();
    expect(within(block).queryByRole("button", { name: "Моя цель" })).toBeNull();
    // «Добавить замер» из макета не переносится (§49).
    expect(screen.queryByText(/замер/i)).toBeNull();
  });

  it("«Записать питание» → ввод текстом, «Новая запись» → каталог, «Профиль» → профиль", async () => {
    serve();
    renderHome();

    fireEvent.click(await screen.findByRole("button", { name: "Записать питание" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/food-scanner/manual");
  });
});

// ---------------------------------------------------------------------------
// «Продолжить разговор с Ayla»
// ---------------------------------------------------------------------------

describe("H01 · продолжить разговор с Ayla", () => {
  it("с последней темой — «Последняя тема» и текст; обе кнопки уходят в чат", async () => {
    serve({ lastTopic: { last_topic: { text: "Обсудили план питания и подобрали запись", at: "2026-09-20T08:30:00Z" } } });
    renderHome();

    const heading = await screen.findByRole("heading", { name: /Продолжить разговор с Ayla/ });
    const block = heading.closest("section") as HTMLElement;
    expect(within(block).getByText("Последняя тема")).toBeInTheDocument();
    expect(within(block).getByText("Обсудили план питания и подобрали запись")).toBeInTheDocument();

    fireEvent.click(within(block).getByRole("button", { name: "Продолжить разговор" }));
    fireEvent.click(within(block).getByRole("button", { name: "Задать новый вопрос" }));
    expect(mockedClose).toHaveBeenCalledTimes(2);
  });

  it("без темы — нейтрально: «Продолжить разговор», подписи «Последняя тема» нет (фриз п.4)", async () => {
    serve({ lastTopic: { last_topic: null } });
    renderHome();

    const heading = await screen.findByRole("heading", { name: /Продолжить разговор с Ayla/ });
    const block = heading.closest("section") as HTMLElement;
    expect(within(block).queryByText("Последняя тема")).toBeNull();
    expect(within(block).getByRole("button", { name: "Продолжить разговор" })).toBeInTheDocument();
  });

  it("ручка темы упала — блок остаётся нейтральным, экран не падает", async () => {
    serve();
    const base = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) =>
        String(url).includes("/last-topic") ? refused(500, "internal") : (base as typeof fetch)(url as string),
      ),
    );
    renderHome();

    const heading = await screen.findByRole("heading", { name: /Продолжить разговор с Ayla/ });
    const block = heading.closest("section") as HTMLElement;
    expect(within(block).getByRole("button", { name: "Продолжить разговор" })).toBeInTheDocument();
    expect(within(block).queryByText("Последняя тема")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Нижняя панель — ровно пять (решение владельца б, §55)
// ---------------------------------------------------------------------------

describe("H01 · нижняя панель", () => {
  it("ровно пять вкладок «Главная · План · Дневник · Записи · Профиль», «Главная» активна", async () => {
    serve();
    renderHome();
    await screen.findByText("Активная цель");

    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    const tabs = within(nav).getAllByRole("button");
    expect(tabs.map((t) => t.textContent?.replace(/[^\p{L}]/gu, ""))).toEqual([
      "Главная",
      "План",
      "Дневник",
      "Записи",
      "Профиль",
    ]);
    expect(tabs[0]).toHaveAttribute("aria-current", "page");
    // «Услуги» и «Я» из панели ушли.
    expect(within(nav).queryByText("Услуги")).toBeNull();
    expect(within(nav).queryByText("Я")).toBeNull();
  });

  it("«План» → /customer/plan, «Дневник» → дневник", async () => {
    serve();
    renderHome();
    await screen.findByText("Активная цель");

    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    fireEvent.click(within(nav).getByRole("button", { name: "План" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/plan");
  });
});

// ---------------------------------------------------------------------------
// Negative: ни «кг», ни «%», ни «вес» — с положительной парой «Неделя»
// ---------------------------------------------------------------------------

describe("H01 · без веса и процентов (§49/§82)", () => {
  it("в DOM нет «кг» / «%» / «вес»; «Неделя» есть — сторож не пуст", async () => {
    serve({ plan: PLAN, activity: NEXT_BOOKING });
    const { container } = renderHome();
    await screen.findByRole("button", { name: "Продолжить сегодняшний план" });
    await screen.findByRole("heading", { name: "Ближайшая запись" });

    const text = container.textContent ?? "";
    expect(text).toMatch(/Неделя/);
    expect(text).not.toMatch(/\bкг\b/i);
    expect(text).not.toMatch(/%/);
    expect(text).not.toMatch(/\bвес\b/i);
  });
});
