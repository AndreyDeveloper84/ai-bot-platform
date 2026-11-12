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
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  // DRF-2266: двери в чат идут одним путём — мост close → ссылка на диалог → подсказка.
  returnToChat: vi.fn(() => "closed"),
  rememberChatLink: vi.fn(),
}));

import { getCatalogBrowse } from "../lib/customer-booking";
import { returnToChat } from "../lib/max-sdk";
import { bookingWhoText, CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";
import { settleScenario } from "../test/settleScenario";

const mockedBrowse = vi.mocked(getCatalogBrowse);
/** «Ушёл в чат» — теперь `returnToChat` (DRF-2266), а не голый `closeApp`. */
const mockedClose = vi.mocked(returnToChat);

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
  /**
   * DRF-2876 — сохранённый план нового механизма: объект — есть; по умолчанию
   * `null` — нет; `"error"` — узнать не удалось; `"disabled"` — механизм
   * выключен на сервере (404 plan_engine_disabled).
   */
  savedPlan?: Json | "error" | "disabled";
  /** DRF-2230 — ответ ручки «позвать в чат за согласием». */
  consentPrompt?: Response;
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
      // Стакан воды (POST): принят. GET по этому пути экран не делает.
      if (u.includes("/wellness/water")) {
        return ok({ entry_id: "e-1", ml: 250, water_ml: 250, today_total_ml: 250, today_norm_ml: 0, water_glasses_eaten: 5 });
      }
      if (u.includes("/recent-activity")) return ok(activity);
      if (u.includes("/plan/current")) {
        if (s.savedPlan === "error") return refused(502, "ayla_unavailable");
        if (s.savedPlan === "disabled") return refused(404, "plan_engine_disabled");
        return ok({ plan: s.savedPlan === undefined ? null : s.savedPlan });
      }
      if (u.includes("/plan-lite")) {
        return plan === "disabled" ? refused(404, "plan_lite_disabled") : ok(plan);
      }
      if (u.includes("/last-topic")) return ok(lastTopic);
      if (u.includes("/wellness/consent-prompt")) return s.consentPrompt ?? ok({ sent: true });
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
  mockedClose.mockReturnValue("closed");
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
  // Решение 12.11, п.4 (DRF-2950): кнопки плана — в шапке блока «План на
  // сегодня»; «План ещё не составлен» переехала туда же из карточки цели.
  it("строка цели — метка и название; «План ещё не составлен» и «Составить план» — в блоке плана → /customer/plan", async () => {
    serve();
    renderHome();

    const card = (await screen.findByText("Активная цель")).closest("section") as HTMLElement;
    expect(within(card).getByText("Подтянуть фигуру")).toBeInTheDocument();
    expect(within(card).queryByText("План ещё не составлен")).toBeNull();
    expect(within(card).queryByRole("button", { name: "Составить план" })).toBeNull();

    const plan = screen.getByRole("heading", { name: "План на сегодня" }).closest("section") as HTMLElement;
    expect(within(plan).getByText("План ещё не составлен")).toBeInTheDocument();
    expect(within(plan).queryByRole("list")).toBeNull();
    expect(screen.queryByRole("button", { name: "Продолжить сегодняшний план" })).toBeNull();

    fireEvent.click(within(plan).getByRole("button", { name: "Составить план" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/plan");
  });

  it("DRF-2173: срок цели «До 1 ноября 2026» под названием; без срока строки нет", async () => {
    serve({ today: { ...TODAY_WITH_GOAL, active_goals: [{ title: "Подтянуть фигуру", week_num: 2, target_date: "2026-11-01", target_date_passed: false }] } });
    renderHome();
    const card = (await screen.findByText("Активная цель")).closest("section") as HTMLElement;
    expect(within(card).getByText("До 1 ноября 2026")).toBeInTheDocument();
  });

  it("DRF-2173: без срока — строки «До …» нет", async () => {
    serve();
    renderHome();
    const card = (await screen.findByText("Активная цель")).closest("section") as HTMLElement;
    expect(within(card).queryByText(/^До \d/)).toBeNull();
  });

  // Решение 12.11, п.2: вход на экран цели — сама строка «Моя цель ›» доски.
  it("строка цели целиком — вход на экран цели; «Посмотреть детали цели» нет", async () => {
    serve();
    renderHome();

    const row = await screen.findByRole("button", { name: /Подтянуть фигуру/ });
    expect(screen.queryByRole("button", { name: "Посмотреть детали цели" })).toBeNull();
    fireEvent.click(row);
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
  // Решения 12.11, п.2 и п.4 (DRF-2950): «Продолжить сегодняшний план» — в
  // шапке блока плана; счёта действий нет нигде — ни «выполнено N из M
  // действий» под целью, ни «N из 3» в шапке.
  it("«Продолжить сегодняшний план» — в шапке блока плана → /customer/plan; счёта действий нет", async () => {
    serve({ plan: PLAN });
    renderHome();

    const cta = await screen.findByRole("button", { name: "Продолжить сегодняшний план" });
    const block = screen.getByRole("heading", { name: "План на сегодня" }).closest("section") as HTMLElement;
    expect(block).toContainElement(cta);
    // Присутствие: строка цели со счётчиком недель на месте.
    expect(screen.getByText("Неделя 2")).toBeInTheDocument();
    expect(screen.queryByText(/действий/)).toBeNull();
    expect(screen.queryByText(/из 10/)).toBeNull();

    fireEvent.click(cta);
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/plan");
  });

  it("строки доски: подпись, период, «N из M»; сделанное — галочкой; «›» ведёт на экран плана", async () => {
    serve({ plan: PLAN });
    renderHome();

    const heading = await screen.findByRole("heading", { name: "План на сегодня" });
    const block = heading.closest("section") as HTMLElement;
    expect(within(block).getByText("4 из 7")).toBeInTheDocument();
    // Чип «из твоего плана» старого макета снят: строки и так в блоке плана.
    expect(within(block).queryByText("из твоего плана")).toBeNull();
    // Выполненное — галочкой, не «3 из 3»; словом — для скринридера.
    expect(within(block).queryByText("3 из 3")).toBeNull();
    const food = within(block).getByRole("button", { name: /Дневник/ });
    expect(food).toHaveAccessibleName(/выполнено/);

    fireEvent.click(food);
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/plan");
  });

  it("«+» у воды — то же действие, что «Стакан воды»: стакан уходит, экран остаётся", async () => {
    serve({ plan: PLAN });
    renderHome();

    const block = (await screen.findByRole("heading", { name: "План на сегодня" })).closest(
      "section",
    ) as HTMLElement;
    const water = within(block).getByText("Вода").closest("li") as HTMLElement;
    fireEvent.click(within(water).getByRole("button", { name: "Добавить стакан воды" }));

    expect(await screen.findByText("+1 стакан зачтён")).toBeInTheDocument();
    // Экран не ушёл: щуп адреса рисуется только на чужом маршруте.
    expect(screen.queryByTestId("location")).toBeNull();
  });

  it("без контура питания у воды нет «+»: строка ведёт на экран плана, кнопок стакана нет нигде", async () => {
    serve({
      plan: PLAN,
      today: { display_name: "Анна", active_goals: TODAY_WITH_GOAL.active_goals, nutrition_disabled: true },
    });
    renderHome();

    const block = (await screen.findByRole("heading", { name: "План на сегодня" })).closest(
      "section",
    ) as HTMLElement;
    const water = within(block).getByRole("button", { name: /Вода/ });
    expect(screen.queryByRole("button", { name: "Добавить стакан воды" })).toBeNull();
    fireEvent.click(water);
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/plan");
  });

  // DRF-2950 (решение владельца 12.11, п.10): как «Продолжить разговор» с
  // DRF-2799 — разговор в приложении, а не закрытие в чат MAX.
  it("attention-строка: «Сегодня не получается по плану?» → «Скорректировать с Ayla» открывает разговор в приложении", async () => {
    serve({ plan: PLAN });
    renderHome();

    await screen.findByText("Сегодня не получается по плану?");
    fireEvent.click(screen.getByRole("button", { name: "Скорректировать с Ayla" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/ayla");
    await settleScenario();
    expect(mockedClose).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 4/5. Запись
// ---------------------------------------------------------------------------

describe("H01 · ближайшая запись", () => {
  // DRF-2172 — «3 200 ₽» справа в карточке (макет DRF-1321): цена — снимок
  // на момент записи из `next_booking.price`; null / ниже 1 ₽ → строки нет
  // (§103, DRF-1989), «0 ₽» не рисуется.
  it("DRF-2172: с ценой — «3 200 ₽» в карточке, формат как у услуг каталога", async () => {
    const withPrice = { ...(NEXT_BOOKING.next_booking as Record<string, unknown>), price: "3200.00" };
    serve({ activity: { ...NEXT_BOOKING, next_booking: withPrice } });
    renderHome();

    const heading = await screen.findByRole("heading", { name: "Ближайшая запись" });
    const block = heading.closest("section") as HTMLElement;
    expect(within(block).getByText("3 200 ₽")).toBeInTheDocument();
  });

  it.each([
    ["null", null],
    ["ключа нет", undefined],
    ["«0.00»", "0.00"],
  ])("DRF-2172: цена %s — строки с ₽ в карточке нет, не «0 ₽»", async (_label, price) => {
    const nb: Record<string, unknown> = { ...(NEXT_BOOKING.next_booking as Record<string, unknown>) };
    if (price === undefined) delete nb.price;
    else nb.price = price;
    serve({ activity: { ...NEXT_BOOKING, next_booking: nb } });
    renderHome();

    const heading = await screen.findByRole("heading", { name: "Ближайшая запись" });
    const block = heading.closest("section") as HTMLElement;
    expect(within(block).getByText(/Лимфодренажный массаж/)).toBeInTheDocument();
    expect(block.textContent ?? "").not.toMatch(/₽/);
  });

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
    // Решение владельца 28.09 (слова, п.2) — ровно «мастер {Имя} · {салон}»,
    // сравнение строки целиком: «у Екатерина С. · …» этот узел не пройдёт.
    expect(within(block).getByText("мастер Екатерина С. · Ayla Beauty")).toBeInTheDocument();
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
    // DRF-2230: сперва приглашение уходит в чат, потом экран закрывается.
    await waitFor(() => expect(mockedClose).toHaveBeenCalledTimes(1));
  });

  // ── DRF-2230 — кнопка зовёт в чат, а не просто закрывает приложение ──────

  const CONSENT_TODAY = {
    consent_required: true,
    active_goals: [{ title: "Подтянуть фигуру", week_num: 2 }],
    display_name: "Анна",
  };

  function promptCalls(): unknown[][] {
    return vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).includes("/wellness/consent-prompt"));
  }

  it("нажатие шлёт приглашение в чат (POST) и только потом закрывает приложение", async () => {
    serve({ today: CONSENT_TODAY });
    renderHome();
    fireEvent.click(await screen.findByRole("button", { name: "Дать согласие в чате" }));
    await waitFor(() => expect(mockedClose).toHaveBeenCalledTimes(1));
    const calls = promptCalls();
    expect(calls).toHaveLength(1);
    expect((calls[0]?.[1] as RequestInit | undefined)?.method).toBe("POST");
  });

  it("приглашение уже в чате (повтор) — не проваливаемся молча: подсказка и «Открыть чат»", async () => {
    // Живой проход владельца 21.09: повтор в окне дубля закрывал приложение, и
    // человек «просто проваливался в чат», не понимая, что там уже ждёт.
    serve({ today: CONSENT_TODAY, consentPrompt: ok({ sent: false, reason: "recently_sent" }) });
    renderHome();
    fireEvent.click(await screen.findByRole("button", { name: "Дать согласие в чате" }));
    expect(await screen.findByText(/Приглашение уже в чате/)).toBeInTheDocument();
    // DRF-2597: мини-апп не закрыт до «Открыть чат» — замер после того, как повтор приглашения улёгся.
    await settleScenario();
    expect(mockedClose).not.toHaveBeenCalled();
    // Положительная пара: выход в чат — по явной кнопке.
    fireEvent.click(screen.getByRole("button", { name: "Открыть чат" }));
    expect(mockedClose).toHaveBeenCalledTimes(1);
  });

  it("«Открыть чат» идёт тем же путём: застрял — подсказка вернуться в чат", async () => {
    mockedClose.mockReturnValue("stuck");
    serve({ today: CONSENT_TODAY, consentPrompt: ok({ sent: false, reason: "recently_sent" }) });
    renderHome();
    fireEvent.click(await screen.findByRole("button", { name: "Дать согласие в чате" }));
    fireEvent.click(await screen.findByRole("button", { name: "Открыть чат" }));
    expect(mockedClose).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Вернись в чат с Ayla/)).toBeInTheDocument();
  });

  it("сбой отправки — приложение НЕ закрывается молча, сказано, что делать", async () => {
    serve({ today: CONSENT_TODAY, consentPrompt: refused(502, "consent_prompt_not_sent") });
    renderHome();
    fireEvent.click(await screen.findByRole("button", { name: "Дать согласие в чате" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/не получилось/i);
    // Положительная пара: запрос был, а закрытия — нет.
    expect(promptCalls()).toHaveLength(1);
    expect(mockedClose).not.toHaveBeenCalled();
  });

  it("согласие уже есть (ответ already_granted) — не закрываем, блок уходит после перечитывания", async () => {
    serve({ today: CONSENT_TODAY, consentPrompt: ok({ sent: false, reason: "already_granted" }) });
    renderHome();
    fireEvent.click(await screen.findByRole("button", { name: "Дать согласие в чате" }));
    await waitFor(() => expect(promptCalls()).toHaveLength(1));
    expect(mockedClose).not.toHaveBeenCalled();
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
  // «Профиль» снят решением владельца 12.11 (DRF-2950, п.9): третий вход в
  // профиль рядом с аватаром в шапке и вкладкой панели.
  it("ровно четыре: записать питание / стакан воды / новая запись / скорректировать план", async () => {
    serve({ plan: PLAN });
    renderHome();

    const heading = await screen.findByRole("heading", { name: "Быстрые действия" });
    const block = heading.closest("section") as HTMLElement;
    const labels = within(block)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"));
    // Имя стакана для скринридера — с глаголом (тап пишет 250 мл сразу); видимая подпись — «Стакан воды».
    expect(labels).toEqual(["Записать питание", "Добавить стакан воды", "Новая запись", "Скорректировать план"]);
    expect(within(block).getByText("Стакан воды")).toBeInTheDocument();
    // Профиль не потерян: вход остался в шапке и во вкладке панели.
    expect(within(block).queryByRole("button", { name: "Профиль" })).toBeNull();
    expect(within(screen.getByRole("banner")).getByRole("button", { name: /профил/i })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation")).getByText("Профиль")).toBeInTheDocument();
    // Ушедшие из быстрых действий: цель — в карточке, услуги — в каталоге.
    expect(within(block).queryByRole("button", { name: "Найди услугу" })).toBeNull();
    expect(within(block).queryByRole("button", { name: "Моя цель" })).toBeNull();
    // «Добавить замер» из макета не переносится (§49).
    expect(screen.queryByText(/замер/i)).toBeNull();
  });

  it("«Скорректировать план» открывает разговор в приложении, а не закрывает его (DRF-2950, п.10)", async () => {
    serve({ plan: PLAN });
    renderHome();

    const qa = within(await screen.findByRole("region", { name: "Быстрые действия" }));
    fireEvent.click(qa.getByRole("button", { name: "Скорректировать план" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/ayla");
    await settleScenario();
    expect(mockedClose).not.toHaveBeenCalled();
  });

  it("«Записать питание» → съёмка фото (DRF-2289; текстом — ссылкой оттуда)", async () => {
    serve();
    renderHome();

    fireEvent.click(await screen.findByRole("button", { name: "Записать питание" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/food-scanner/capture");
  });

  it("карточка «Начнём с малого?» обещает текст — её «Записать текстом» ведёт в ввод текстом (DRF-2289)", async () => {
    // Пустой день — условие карточки первого шага.
    serve({ today: { ...TODAY_WITH_GOAL, calories_eaten: 0, water_glasses_eaten: 0 } });
    renderHome();

    expect(await screen.findByText("Начнём с малого?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Записать текстом" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/food-scanner/manual");
  });
});

// ---------------------------------------------------------------------------
// «Шаги на сегодня» — карточка «Твой следующий шаг» доски (решение 12.11, п.3)
// ---------------------------------------------------------------------------

describe("H01 · шаги на сегодня (DRF-2950, п.3)", () => {
  it("норма известна и не набрана: «Ещё N стаканов до нормы» и кнопка стакана, стакан уходит", async () => {
    serve();
    renderHome();

    const card = (await screen.findByRole("heading", { name: "Шаги на сегодня" })).closest(
      "section",
    ) as HTMLElement;
    expect(within(card).getByText("Ещё 4 стакана до нормы")).toBeInTheDocument();
    const add = within(card).getByRole("button", { name: "Добавить стакан воды" });
    // Видимая подпись — та же, что у быстрого действия.
    expect(add).toHaveTextContent("Стакан воды");
    fireEvent.click(add);
    expect(await screen.findByText("+1 стакан зачтён")).toBeInTheDocument();
  });

  it.each([
    ["норма набрана", { ...TODAY_WITH_GOAL, water_glasses_eaten: 8 }],
    ["нормы нет (анкета питания не пройдена)", { ...TODAY_WITH_GOAL, water_glasses_target: undefined }],
  ])("%s — карточки нет", async (_case, today) => {
    serve({ today });
    renderHome();

    // Присутствие: экран отрисован, строка цели на месте.
    expect(await screen.findByText("Активная цель")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Шаги на сегодня" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// «Продолжить разговор с Ayla»
// ---------------------------------------------------------------------------

describe("H01 · продолжить разговор с Ayla", () => {
  // DRF-2799 (решение владельца 06.10, замещает Д2 §172 / DRF-2266 для этого
  // блока): «диалог продолжается там, где начат». Обе кнопки ведут в разговор
  // внутри Mini App (`/customer/ayla`) и приложение НЕ закрывают.
  it.each(["Продолжить разговор", "Задать новый вопрос"])(
    "с последней темой — «Последняя тема» и текст; «%s» ведёт в разговор в приложении",
    async (label) => {
      serve({
        lastTopic: {
          last_topic: { text: "Обсудили план питания и подобрали запись", at: "2026-09-20T08:30:00Z" },
        },
      });
      renderHome();

      const heading = await screen.findByRole("heading", { name: /Продолжить разговор с Ayla/ });
      const block = heading.closest("section") as HTMLElement;
      expect(within(block).getByText("Последняя тема")).toBeInTheDocument();
      expect(within(block).getByText("Обсудили план питания и подобрали запись")).toBeInTheDocument();

      fireEvent.click(within(block).getByRole("button", { name: label }));

      expect(await screen.findByTestId("location")).toHaveTextContent("/customer/ayla");
      expect(mockedClose).not.toHaveBeenCalled();
    },
  );

  // ── DRF-2266 — ссылка на диалог бота по-прежнему запоминается: ею пользуются
  //    прочие двери в чат (согласие, план). Блок Ayla её больше не открывает.

  it("ссылка на чат из last-topic запоминается, но блок Ayla в чат не уводит", async () => {
    const { rememberChatLink } = await import("../lib/max-sdk");
    serve({ lastTopic: { last_topic: null, chat_link: "https://max.ru/ayla_client_bot" } });
    renderHome();
    const heading = await screen.findByRole("heading", { name: /Продолжить разговор с Ayla/ });
    const block = heading.closest("section") as HTMLElement;
    await waitFor(() =>
      expect(vi.mocked(rememberChatLink)).toHaveBeenCalledWith("https://max.ru/ayla_client_bot"),
    );

    fireEvent.click(within(block).getByRole("button", { name: "Продолжить разговор" }));

    expect(await screen.findByTestId("location")).toHaveTextContent("/customer/ayla");
    expect(mockedClose).not.toHaveBeenCalled();
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
  it("в DOM нет «кг» / «вес»; «%» — только в строке калорий дневника (§85 §8); «Неделя» есть — сторож не пуст", async () => {
    serve({ plan: PLAN, activity: NEXT_BOOKING });
    const { container } = renderHome();
    await screen.findByRole("button", { name: "Продолжить сегодняшний план" });
    await screen.findByRole("heading", { name: "Ближайшая запись" });

    const text = container.textContent ?? "";
    expect(text).toMatch(/Неделя/);
    // Граница ASCII-слова (backslash-b) с кириллицей не работает: она не
    // поймала бы « кг » никогда. Граница здесь — «не буква» по Unicode.
    const word = (w: string) => new RegExp(`(^|[^\\p{L}])${w}([^\\p{L}]|$)`, "iu");
    expect(text).not.toMatch(word("кг"));
    expect(text).not.toMatch(word("вес"));
    expect("−2,4 кг из 10").toMatch(word("кг")); // положительная пара сторожа

    // Процент калорий «1240 / 2100 ккал · 59 %» — строка дневника под
    // ориентиром, разрешённая §85 §8 (DRF-1844); она не про цель. Всё
    // остальное на экране — карточка цели, план, запись, панель — без «%».
    const diary = container.querySelector(".wellness-dash__pulse-card");
    expect(diary).not.toBeNull();
    expect(diary?.textContent ?? "").toMatch(/%/); // положительная пара: исключение не пустое
    const outsideDiary = Array.from(container.querySelectorAll("section"))
      .filter((el) => !el.querySelector(".wellness-dash__pulse-card"))
      .map((el) => el.textContent ?? "")
      .join(" ");
    expect(outsideDiary).toMatch(/Активная цель/);
    expect(outsideDiary).not.toMatch(/%/);
  });
});

describe("строка «кто» ближайшей записи (п.2 решений владельца 28.09)", () => {
  it.each([
    ["Марина", "Формула тела", "мастер Марина · Формула тела"],
    ["", "Формула тела", "Формула тела"],
    ["Марина", "", "мастер Марина"],
    ["  ", null, ""],
  ])("%j + %j → %j", (name, salon, text) => {
    expect(bookingWhoText(name, salon)).toBe(text);
  });
});


// ---------------------------------------------------------------------------
// 3а. Сохранён план нового механизма (DRF-2876)
// ---------------------------------------------------------------------------

const SAVED_PLAN = {
  plan_id: "plan-2876",
  steps: [
    { step_id: "s-0", label: "Режим сна", why: null },
    { step_id: "s-1", label: "Вечерняя прогулка", why: null },
  ],
};

describe("H01 · сохранён новый план (решение владельца 09.10, вариант «а»)", () => {
  // Решение 12.11, п.4: «Мой план» — в шапке блока «План на сегодня». Строк
  // и счёта прежнего плана нет (09.10, вариант «а»): блок — одна шапка.
  // Вопрос владельцу (DRF-2950, 12.11): оставить так или вернуть «Мой план»
  // под строку цели.
  it("шапка блока плана с «Мой план»; строк и счёта прежнего плана нет, хотя он есть", async () => {
    serve({ plan: PLAN, savedPlan: SAVED_PLAN });
    renderHome();

    const mine = await screen.findByRole("button", { name: "Мой план" });
    const block = screen.getByRole("heading", { name: "План на сегодня" }).closest("section") as HTMLElement;
    expect(block).toContainElement(mine);
    // Положительный контроль: строка цели отрисована, счётчик недель на месте.
    expect(screen.getByText("Неделя 2")).toBeInTheDocument();
    expect(within(block).queryByRole("list")).not.toBeInTheDocument();
    expect(screen.queryByText("4 из 7")).not.toBeInTheDocument();
    expect(screen.queryByText(/выполнено/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Продолжить сегодняшний план" })).not.toBeInTheDocument();
  });

  it("вход в план — «Мой план», существующей подписью, → /customer/plan", async () => {
    serve({ plan: PLAN, savedPlan: SAVED_PLAN });
    renderHome();

    fireEvent.click(await screen.findByRole("button", { name: "Мой план" }));

    expect(screen.getByTestId("location")).toHaveTextContent("/customer/plan");
  });

  it("шаги нового плана на Главной не дублируются и «Составить план» не предлагается", async () => {
    serve({ plan: null, savedPlan: SAVED_PLAN });
    renderHome();

    expect(await screen.findByRole("button", { name: "Мой план" })).toBeInTheDocument();
    expect(screen.queryByText("Режим сна")).not.toBeInTheDocument();
    expect(screen.queryByText("План ещё не составлен")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Составить план" })).not.toBeInTheDocument();
  });

  it("не удалось узнать о новом плане — о плане молчим, счётчики прежнего не показываем", async () => {
    serve({ plan: PLAN, savedPlan: "error" });
    renderHome();

    expect(await screen.findByText("Неделя 2")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "План на сегодня" })).not.toBeInTheDocument();
    expect(screen.queryByText(/выполнено/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Мой план" })).not.toBeInTheDocument();
  });

  it("механизм выключен на сервере — Главная как раньше, с прежним планом", async () => {
    serve({ plan: PLAN, savedPlan: "disabled" });
    renderHome();

    expect(await screen.findByRole("heading", { name: "План на сегодня" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Продолжить сегодняшний план" })).toBeInTheDocument();
    expect(screen.getByText("4 из 7")).toBeInTheDocument();
  });
});
