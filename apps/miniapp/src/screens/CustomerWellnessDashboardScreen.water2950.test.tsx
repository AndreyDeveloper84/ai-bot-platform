/**
 * DRF-2950 — две неточности воды на Главной (решение владельца 12.11, п.11).
 *
 * 1. После принятого стакана сводка не перечитывалась: строка «Вода»,
 *    «Ещё N стаканов до нормы» и счёт воды в плане показывали старое число.
 *    Теперь — тихое перечитывание: без скелетов, сбой не роняет показанное.
 * 2. Стаканы из офлайн-очереди уходили только по событию `online` или со
 *    следующим нажатием. Теперь — и при открытии экрана с сетью.
 *
 * Гонки, которые тихое чтение не должно проиграть: тихое обогнало полную и
 * упало (срез не зависает в скелете); полная ответила позже тихой (на экране
 * свежее число); частые тапы склеиваются в два чтения, а не в N.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

import { CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";
import { settleScenario } from "../test/settleScenario";

const QUEUE_KEY = "max:wellness_water_offline_queue";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function day(glasses: number): Record<string, unknown> {
  return {
    display_name: "Анна",
    calories_eaten: 0,
    water_glasses_eaten: glasses,
    water_glasses_target: 8,
    active_goals: [],
  };
}

const ACCEPTED = {
  entry_id: "entry-1",
  ml: 250,
  water_ml: 250,
  today_total_ml: 250,
  today_norm_ml: 0,
  water_glasses_eaten: 1,
};

type TodayAnswer = () => Response | Promise<Response>;

/**
 * Сервер: `/wellness/today` отвечает по очереди из `todays` (последний ответ
 * повторяется), POST воды — `post`. Возвращает журнал запросов.
 */
function serve(todays: TodayAnswer[], post: () => Response | Promise<Response> = () => json(ACCEPTED)) {
  const calls: string[] = [];
  let todayN = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: RequestInit) => {
      const u = String(url);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push(`${method} ${u}`);
      if (method === "POST" && u.includes("/wellness/water")) return post();
      if (u.includes("/wellness/today")) {
        const answer = todays[Math.min(todayN, todays.length - 1)]!;
        todayN += 1;
        return answer();
      }
      if (u.includes("/recent-activity")) return json({ this_week_booking_count: 0 });
      if (u.includes("/plan/current")) return json({ plan: null });
      if (u.includes("/plan-lite")) return json({ plan_lite: null });
      if (u.includes("/last-topic")) return json({ last_topic: null });
      throw new Error(`unexpected fetch: ${method} ${u}`);
    }),
  );
  const todayGets = () => calls.filter((c) => c === "GET /api/v1/customer/wellness/today").length;
  const posts = () => calls.filter((c) => c.startsWith("POST")).length;
  return { calls, todayGets, posts };
}

/** Ответ, который отпускается вручную. */
function held(): { answer: TodayAnswer; release: (r: Response) => void } {
  let release: (r: Response) => void = () => {};
  const p = new Promise<Response>((resolve) => {
    release = resolve;
  });
  return { answer: () => p, release };
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

function tapGlass() {
  const qa = within(screen.getByRole("region", { name: "Быстрые действия" }));
  fireEvent.click(qa.getByRole("button", { name: "Добавить стакан воды" }));
}

const skeletons = () => document.querySelectorAll(".skeleton").length;

beforeEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("п.11а — после принятого стакана числа перечитываются тихо", () => {
  it("пока перечитывание в пути — старое число и ни одного скелета; потом — новое", async () => {
    const second = held();
    const srv = serve([() => json(day(0)), second.answer]);
    renderHome();
    expect(await screen.findByText("0 / 8 стаканов")).toBeInTheDocument();

    tapGlass();
    await waitFor(() => expect(srv.todayGets()).toBe(2));
    // Перечитывание висит: показано прежнее, экран не ушёл в загрузку.
    expect(screen.getByText("0 / 8 стаканов")).toBeInTheDocument();
    expect(skeletons()).toBe(0);

    second.release(json(day(1)));
    expect(await screen.findByText("1 / 8 стаканов")).toBeInTheDocument();
    expect(screen.queryByText("0 / 8 стаканов")).not.toBeInTheDocument();
  });

  it("перечитывание упало — показанное остаётся, ошибки вместо чисел нет", async () => {
    const srv = serve([() => json(day(0)), () => json({ error: "ayla_unavailable" }, 502)]);
    renderHome();
    expect(await screen.findByText("0 / 8 стаканов")).toBeInTheDocument();

    tapGlass();
    await waitFor(() => expect(srv.todayGets()).toBe(2));
    expect(await screen.findByText("+1 стакан зачтён")).toBeInTheDocument();
    expect(screen.getByText("0 / 8 стаканов")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Обновить" })).not.toBeInTheDocument();
  });

  it("стакан остался в очереди (сеть не ответила) — перечитывать нечего, запроса нет", async () => {
    const srv = serve([() => json(day(0))], () => {
      throw new TypeError("Failed to fetch");
    });
    renderHome();
    expect(await screen.findByText("0 / 8 стаканов")).toBeInTheDocument();

    tapGlass();
    expect(await screen.findByText("+1 стакан · 1 стакан ждёт синхронизации")).toBeInTheDocument();
    expect(srv.posts()).toBe(1); // присутствие: стакан правда пытался уйти
    expect(srv.todayGets()).toBe(1);
  });

  it("частые тапы склеиваются: три стакана — два перечитывания, а не три", async () => {
    const first = held();
    const srv = serve([() => json(day(0)), first.answer, () => json(day(3))]);
    renderHome();
    expect(await screen.findByText("0 / 8 стаканов")).toBeInTheDocument();

    tapGlass();
    await waitFor(() => expect(srv.todayGets()).toBe(2));
    tapGlass();
    tapGlass();
    await waitFor(() => expect(srv.posts()).toBe(3));
    first.release(json(day(1)));

    expect(await screen.findByText("3 / 8 стаканов")).toBeInTheDocument();
    // Сценарий улёгся: лишнее чтение пришло бы следом.
    await settleScenario();
    expect(srv.todayGets()).toBe(3);
  });
});

describe("стакан добрал норму — карточка шага уходит, фокус не теряется", () => {
  it("кнопка ушла вместе с карточкой — фокус на «Отменить» тоста", async () => {
    serve([() => json(day(7)), () => json(day(8))]);
    renderHome();

    const card = (await screen.findByRole("heading", { name: "Шаги на сегодня" })).closest(
      "section",
    ) as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "Добавить стакан воды" }));

    const undo = await screen.findByRole("button", { name: "Отменить стакан" });
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Шаги на сегодня" })).toBeNull(),
    );
    expect(screen.getByText("8 / 8 стаканов")).toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(undo));
  });

  it("стакан из быстрых действий — фокус не трогается (его кнопка на месте)", async () => {
    serve([() => json(day(7)), () => json(day(8))]);
    renderHome();
    expect(await screen.findByRole("heading", { name: "Шаги на сегодня" })).toBeInTheDocument();

    tapGlass();
    await screen.findByRole("button", { name: "Отменить стакан" });
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Шаги на сегодня" })).toBeNull(),
    );
    await settleScenario();
    expect(document.activeElement).toBe(document.body);
  });
});

describe("п.11а — гонки тихого и полного чтения", () => {
  it("тихое обогнало полную и упало — срез не завис в скелете, а потом получил ответ полной", async () => {
    const firstLoad = held();
    const srv = serve([firstLoad.answer, () => json({ error: "ayla_unavailable" }, 502)]);
    renderHome();
    await screen.findByRole("region", { name: "Быстрые действия" });
    expect(skeletons()).toBeGreaterThan(0); // присутствие: полная правда грузится

    tapGlass();
    await waitFor(() => expect(srv.todayGets()).toBe(2));
    // Тихое упало, пока сводка ещё грузилась, — ошибка, а не вечный скелет.
    expect(await screen.findByRole("button", { name: "Обновить" })).toBeInTheDocument();

    firstLoad.release(json(day(1)));
    expect(await screen.findByText("1 / 8 стаканов")).toBeInTheDocument();
  });

  it("полная при открытии ответила позже тихой — на экране свежее число", async () => {
    window.localStorage.setItem(
      QUEUE_KEY,
      JSON.stringify([{ ts: Date.now() - 60_000, volume_ml: 250, key: "water-old" }]),
    );
    const firstLoad = held();
    const srv = serve([firstLoad.answer, () => json(day(1))]);
    renderHome();

    // Очередь ушла при открытии, тихое чтение после неё вернуло свежее.
    expect(await screen.findByText("1 / 8 стаканов")).toBeInTheDocument();
    expect(srv.posts()).toBe(1);

    // Опоздавший ответ полной загрузки — старше, и экран его не берёт.
    firstLoad.release(json(day(0)));
    await settleScenario();
    expect(screen.getByText("1 / 8 стаканов")).toBeInTheDocument();
    expect(screen.queryByText("0 / 8 стаканов")).not.toBeInTheDocument();
  });
});

describe("п.11б — очередь уходит при открытии экрана", () => {
  it("с сетью: стакан из очереди ушёл без нажатий, индикатор погас, сводка перечитана", async () => {
    window.localStorage.setItem(
      QUEUE_KEY,
      JSON.stringify([{ ts: Date.now() - 60_000, volume_ml: 250, key: "water-old" }]),
    );
    const srv = serve([() => json(day(0)), () => json(day(1))]);
    renderHome();

    await waitFor(() => expect(srv.posts()).toBe(1));
    expect(await screen.findByText("1 / 8 стаканов")).toBeInTheDocument();
    expect(screen.queryByText(/ждёт синхронизации/)).not.toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(QUEUE_KEY) ?? "null")).toEqual([]);
  });

  it("без сети: очередь не трогается, индикатор на месте", async () => {
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    window.localStorage.setItem(
      QUEUE_KEY,
      JSON.stringify([{ ts: Date.now() - 60_000, volume_ml: 250, key: "water-old" }]),
    );
    const srv = serve([() => json(day(0))]);
    renderHome();

    expect(await screen.findByText("0 / 8 стаканов")).toBeInTheDocument();
    expect(screen.getByText("+1 стакан ждёт синхронизации")).toBeInTheDocument();
    expect(srv.posts()).toBe(0);
  });

  it("сеть вернулась — очередь ушла, и сводка перечитана", async () => {
    const onLine = vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    window.localStorage.setItem(
      QUEUE_KEY,
      JSON.stringify([{ ts: Date.now() - 60_000, volume_ml: 250, key: "water-old" }]),
    );
    const srv = serve([() => json(day(0)), () => json(day(1))]);
    renderHome();
    expect(await screen.findByText("0 / 8 стаканов")).toBeInTheDocument();
    expect(srv.posts()).toBe(0);

    onLine.mockReturnValue(true);
    window.dispatchEvent(new Event("online"));

    expect(await screen.findByText("1 / 8 стаканов")).toBeInTheDocument();
    expect(srv.posts()).toBe(1);
  });
});
