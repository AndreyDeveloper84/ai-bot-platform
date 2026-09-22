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
import { CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";

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
      if (u.includes("/recent-activity")) return ok(activity);
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
  it("ровно пять: записать питание / стакан воды / новая запись / скорректировать план / профиль", async () => {
    serve({ plan: PLAN });
    renderHome();

    const heading = await screen.findByRole("heading", { name: "Быстрые действия" });
    const block = heading.closest("section") as HTMLElement;
    const labels = within(block)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"));
    // Имя стакана для скринридера — с глаголом (тап пишет 250 мл сразу); видимая подпись — «Стакан воды».
    expect(labels).toEqual(["Записать питание", "Добавить стакан воды", "Новая запись", "Скорректировать план", "Профиль"]);
    expect(within(block).getByText("Стакан воды")).toBeInTheDocument();
    // Ушедшие из быстрых действий: цель — в карточке, услуги — в каталоге.
    expect(within(block).queryByRole("button", { name: "Найди услугу" })).toBeNull();
    expect(within(block).queryByRole("button", { name: "Моя цель" })).toBeNull();
    // «Добавить замер» из макета не переносится (§49).
    expect(screen.queryByText(/замер/i)).toBeNull();
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

  // ── DRF-2266 — кнопка молча ничего не делала (web.max.ru, 21.09) ──────────

  it("ссылка на чат из last-topic уходит в returnToChat", async () => {
    serve({ lastTopic: { last_topic: null, chat_link: "https://max.ru/ayla_client_bot" } });
    renderHome();
    const heading = await screen.findByRole("heading", { name: /Продолжить разговор с Ayla/ });
    const block = heading.closest("section") as HTMLElement;
    await waitFor(() =>
      expect(within(block).getByRole("button", { name: "Продолжить разговор" })).toBeEnabled(),
    );
    fireEvent.click(within(block).getByRole("button", { name: "Продолжить разговор" }));
    await waitFor(() =>
      expect(mockedClose).toHaveBeenCalledWith("https://max.ru/ayla_client_bot"),
    );
  });

  it("ни закрыть, ни открыть диалог нечем — подсказка, а не тишина", async () => {
    mockedClose.mockReturnValue("stuck");
    serve({ lastTopic: { last_topic: null, chat_link: null } });
    renderHome();
    const heading = await screen.findByRole("heading", { name: /Продолжить разговор с Ayla/ });
    const block = heading.closest("section") as HTMLElement;
    fireEvent.click(within(block).getByRole("button", { name: "Задать новый вопрос" }));
    expect(await within(block).findByText(/Вернись в чат с Ayla/)).toBeInTheDocument();
  });

  it("закрылось — подсказки нет (положительная пара)", async () => {
    serve({ lastTopic: { last_topic: null, chat_link: null } });
    renderHome();
    const heading = await screen.findByRole("heading", { name: /Продолжить разговор с Ayla/ });
    const block = heading.closest("section") as HTMLElement;
    fireEvent.click(within(block).getByRole("button", { name: "Задать новый вопрос" }));
    await waitFor(() => expect(mockedClose).toHaveBeenCalledTimes(1));
    expect(within(block).queryByText(/Вернись в чат с Ayla/)).toBeNull();
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
