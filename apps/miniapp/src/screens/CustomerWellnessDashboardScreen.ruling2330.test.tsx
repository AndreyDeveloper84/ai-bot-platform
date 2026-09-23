/**
 * H01 «Главная клиента» — решение владельца 22.09 (DRF-2330).
 *
 * Источник: `docs/CURRENT_DECISIONS_2026-09-16.md`, блок «H01 «Главная
 * клиента» (макет DRF-1321 v1.2, PR #1894) — OWNER RULING 22.09»; вопросы —
 * `docs/OWNER_QUESTIONS_H01_DEVIATIONS_2026-09-20.md`, раздел 1.
 *
 * Что закрепляется:
 *
 * * **Д2 — СНЯТЬ** кнопку «спросить» из шапки: один вход в чат вместо двух
 *   (второй — блок «Продолжить разговор с Ayla», он остаётся);
 * * **Д32 — ПО МАКЕТУ** порядок блоков: цель → план → запись → быстрые
 *   действия → Ayla → дневник;
 * * **Д6** — стрелка «→» у «Продолжить сегодняшний план» (украшение; имя
 *   кнопки для скринридера не меняется);
 * * **Д11** — «Все мои записи» справа от заголовка, а не внизу карточки;
 * * **Д31 (в) и (г) — СНЯТЬ** «Прогресс недели» и полку «Ayla подобрала
 *   тебе».
 *
 * Что владелец ОСТАВИЛ, и это его решение, а не недосмотр:
 *
 * * **Д31 (а) «Начнём с малого?»** — единственная подсказка первого шага
 *   человеку с пустым днём;
 * * **Д31 (б) «Шаги на сегодня»** — единственное место, где видно «Ещё N
 *   стаканов до нормы» (норма из анкеты питания, не из плана).
 *
 * Полка «Ayla подобрала тебе» уходит С ЭКРАНА, но КОД НЕ УДАЛЯЕТСЯ: вопрос
 * 40 (снимать совсем или оставить обездвиженной) у владельца. Поэтому узел
 * ниже проверяет отсутствие на экране, а не отсутствие в файле.
 *
 * Вопросы 39, 41–43 — не трогаются. Д8/Д9 — правится макет, не код.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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
  returnToChat: vi.fn(() => "closed"),
  rememberChatLink: vi.fn(),
}));

import { getCatalogBrowse } from "../lib/customer-booking";
import { CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";

const mockedBrowse = vi.mocked(getCatalogBrowse);

const TODAY: Record<string, unknown> = {
  calories_eaten: 1240,
  calories_target: 2100,
  water_glasses_eaten: 4,
  water_glasses_target: 8,
  active_goals: [{ title: "Подтянуть фигуру", week_num: 2 }],
  display_name: "Анна",
  diary_consent: true,
  food_days_logged: 5,
  active_days_count: 4,
  water_days_logged: 6,
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
        done_count: 2,
      },
    ],
  },
};

const BOOKING: Record<string, unknown> = {
  this_week_booking_count: 1,
  // Гейт «Прогресса недели» (§11.4): вложенный объект и ≥3 активных дня —
  // иначе блок не рендерится и узел ниже был бы зелёным впустую.
  weekly_progress: {
    water_days_logged: 6,
    food_days_logged: 5,
    active_days_count: 4,
  },
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

/** Как в `h01.test.tsx`: ручки отдают тело как есть, без конверта. */
function ok(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function serve(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const u = String(url);
      if (u.includes("/wellness/today")) return ok(TODAY);
      if (u.includes("/recent-activity")) return ok(BOOKING);
      if (u.includes("/plan-lite")) return ok(PLAN);
      if (u.includes("/last-topic")) return ok({ last_topic: null });
      if (u.includes("/wellness/consent-prompt")) return ok({ sent: true });
      throw new Error(`unexpected fetch: ${u}`);
    }),
  );
}

function renderHome() {
  return render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <Routes>
        <Route path="/customer/main" element={<CustomerWellnessDashboardScreen />} />
        <Route path="*" element={<div />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  mockedBrowse.mockResolvedValue({
    services: [{ id: "s1", name: "Массаж", price_amount: 2000 }],
    masters: [],
    // `serviceId`, как в разборе клиента: иначе полка не отрисуется и узел
    // «снята» прошёл бы, ничего не доказав.
    picks: [{ serviceId: "s1", reasons: ["ты искала массаж"] }],
    picksOutcome: "OK",
  } as never);
  serve();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Порядок разделов на экране — по тому, как они идут в DOM. */
function sectionOrder(): string[] {
  const main = document.querySelector("main") as HTMLElement;
  return Array.from(main.querySelectorAll("section"))
    .map((el) => el.className)
    .filter(Boolean);
}

function indexOfClass(order: string[], needle: string): number {
  return order.findIndex((cls) => cls.includes(needle));
}

describe("Д2 — один вход в чат, не два", () => {
  it("в шапке нет кнопки «спросить»", async () => {
    renderHome();
    // Присутствие: шапка на месте и это она.
    expect(await screen.findByRole("banner")).toBeInTheDocument();
    expect(within(screen.getByRole("banner")).queryByText("спросить")).toBeNull();
  });

  it("вход в чат остаётся один — блок «Продолжить разговор с Ayla»", async () => {
    renderHome();
    expect(await screen.findByRole("heading", { name: /Продолжить разговор/ })).toBeInTheDocument();
  });
});

describe("Д32 — порядок блоков по макету", () => {
  it("цель → план → запись → быстрые действия → Ayla → дневник", async () => {
    renderHome();
    await screen.findByText("Активная цель");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "План на сегодня" })).toBeInTheDocument();
    });

    const order = sectionOrder();
    const goal = indexOfClass(order, "goal-card");
    const plan = indexOfClass(order, "plan-today");
    const booking = indexOfClass(order, "booking");
    const quick = indexOfClass(order, "quick");
    const ayla = indexOfClass(order, "ayla");
    const diary = indexOfClass(order, "pulse");

    // Присутствие: все шесть разделов на экране.
    for (const [name, idx] of Object.entries({ goal, plan, booking, quick, ayla, diary })) {
      expect(idx, `раздел «${name}» не найден`).toBeGreaterThanOrEqual(0);
    }
    expect(goal).toBeLessThan(plan);
    expect(plan).toBeLessThan(booking);
    expect(booking).toBeLessThan(quick);
    expect(quick).toBeLessThan(ayla);
    expect(ayla).toBeLessThan(diary);
  });
});

describe("Д6 и Д11 — две мелочи макета", () => {
  it("у «Продолжить сегодняшний план» есть стрелка, имя кнопки прежнее", async () => {
    renderHome();
    const cta = await screen.findByRole("button", { name: "Продолжить сегодняшний план" });
    // Стрелка — украшение: в доступном имени её нет, в тексте есть.
    expect(cta.textContent).toContain("→");
  });

  it("«Все мои записи» стоит рядом с заголовком записи, а не в конце карточки", async () => {
    renderHome();
    const link = await screen.findByRole("button", { name: "Все мои записи" });
    const header = screen.getByRole("heading", { name: /Ближайшая запись/ });
    const open = screen.getByRole("button", { name: "Открыть запись" });
    // Присутствие: заголовок, ссылка и тело карточки — все на экране.
    expect(header).toBeInTheDocument();
    expect(link).toBeInTheDocument();
    // Справа от заголовка значит: в DOM ссылка идёт ДО тела карточки
    // (сейчас она в самом низу, под «Открыть запись»).
    const linkBeforeBody =
      link.compareDocumentPosition(open) & Node.DOCUMENT_POSITION_FOLLOWING;
    expect(Boolean(linkBeforeBody)).toBe(true);
  });
});

describe("Д31 — сняты два блока из четырёх", () => {
  it("«Прогресс недели» с экрана снят", async () => {
    renderHome();
    await screen.findByText("Активная цель");  // присутствие: экран отрисован
    expect(screen.queryByRole("heading", { name: "Прогресс недели" })).toBeNull();
  });

  it("полка «Ayla подобрала тебе» с экрана снята, хотя подборка пришла", async () => {
    renderHome();
    await screen.findByText("Активная цель");
    await waitFor(() => {
      expect(mockedBrowse).toHaveBeenCalled();  // присутствие: подборка запрошена
    });
    expect(screen.queryByRole("heading", { name: /подобрала тебе/ })).toBeNull();
  });
});

describe("Д31 — два блока ОСТАЮТСЯ: это решение владельца", () => {
  it("«Шаги на сегодня» на месте — единственное место с нормой воды", async () => {
    renderHome();
    expect(
      await screen.findByRole("heading", { name: "Шаги на сегодня" }),
    ).toBeInTheDocument();
  });

  it("«Начнём с малого?» показывается человеку с пустым днём", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        if (u.includes("/wellness/today")) {
          return ok({
            ...TODAY,
            calories_eaten: 0,
            water_glasses_eaten: 0,
            active_goals: [],
          });
        }
        if (u.includes("/recent-activity")) return ok({ this_week_booking_count: 0 });
        if (u.includes("/plan-lite")) return ok({ plan_lite: null });
        if (u.includes("/last-topic")) return ok({ last_topic: null });
        if (u.includes("/wellness/consent-prompt")) return ok({ sent: true });
        throw new Error(`unexpected fetch: ${u}`);
      }),
    );
    renderHome();
    expect(await screen.findByText("Начнём с малого?")).toBeInTheDocument();
  });
});
