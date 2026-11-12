/**
 * Шапка Главной — решение владельца 12.11, п.1 (DRF-2950). Заменяет Д1
 * (DRF-2331, `docs/OPEN_DECISIONS.md` §172 → §175).
 *
 * Было (Д1): аватар, имя с 👋 и «Рада тебя видеть!» слева, знак справа — и
 * второе приветствие «Доброе утро, Анна» ниже: экран здоровался дважды.
 *
 * Стало, как на доске: знак «ayla» слева, вход в профиль (кружок с
 * инициалами) справа, ОДНО приветствие — заголовок `h1` в `main` с прежними
 * словами и фразой дня. «Рада тебя видеть!» уходит.
 *
 * Чего нет, и это решение, а не забывчивость:
 *
 * * **колокольчика.** Ленты уведомлений у клиента не существует — ни ручки
 *   на сервере, ни экрана; есть только настройки уведомлений. Счётчик «2»
 *   взять неоткуда. Правило владельца §61 (М-4, п. 1) и DRF-1181;
 * * **фото-фона** — изображений нет.
 *
 * Знак — строчное «ayla» (`docs/screens/customer-main-wellness-dashboard.md`
 * §7, `ayla-identity-and-brand.md` — «lowercase «ayla» wordmark»). Доска
 * пишет «Ayla»: это вопрос бренда, а не вёрстки.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../lib/customer-booking")>();
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

/** Имя из двух слов: инициалы «МП» ни с чем на экране не совпадают. */
const TODAY: Record<string, unknown> = {
  calories_eaten: 1240,
  calories_target: 2100,
  water_glasses_eaten: 4,
  water_glasses_target: 8,
  active_goals: [{ title: "Подтянуть фигуру", week_num: 2 }],
  display_name: "Мария Петрова",
  diary_consent: true,
};

function ok(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

function serve(today: Record<string, unknown> = TODAY): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const u = String(url);
      if (u.includes("/wellness/today")) return ok(today);
      if (u.includes("/recent-activity")) return ok({ this_week_booking_count: 0 });
      if (u.includes("/plan-lite")) return ok({ plan_lite: null });
      if (u.includes("/last-topic")) return ok({ last_topic: null });
      throw new Error(`unexpected fetch: ${u}`);
    }),
  );
}

function renderHome() {
  return render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <Routes>
        <Route
          path="/customer/main"
          element={<CustomerWellnessDashboardScreen />}
        />
        <Route path="*" element={<div />} />
      </Routes>
    </MemoryRouter>,
  );
}

function header(): HTMLElement {
  return screen.getByRole("banner");
}

beforeEach(() => {
  vi.restoreAllMocks();
  mockedBrowse.mockResolvedValue({
    services: [],
    masters: [],
    picks: [],
    picksOutcome: "OK",
  } as never);
  serve();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function greetingHeading(): HTMLElement {
  return screen.getByRole("heading", { level: 1 });
}

describe("Шапка Главной — решение владельца 12.11, п.1 (DRF-2950)", () => {
  it("знак «ayla» слева, вход в профиль справа — в этом порядке", async () => {
    renderHome();
    const avatar = await within(header()).findByRole("button", { name: "Мой профиль" });
    const mark = within(header()).getByText("ayla");
    expect(mark).toHaveAttribute("lang", "en");
    // Слева — значит раньше в DOM: шапка — строка флекса без `order`.
    expect(mark.compareDocumentPosition(avatar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("аватар — инициалы тем же образцом, что у профиля и карточки мастера", async () => {
    renderHome();
    // Фотографии клиента нет нигде в контракте: ни у `wellness/today`, ни
    // у `/me`. Кружок — `avatarInitials`, как на профиле клиента.
    await waitFor(() =>
      expect(within(header()).getByText("МП")).toBeInTheDocument(),
    );
  });

  it("экран здоровается один раз: «Рада тебя видеть!» нет, имя — в заголовке, а не в шапке", async () => {
    renderHome();
    // Присутствие первым: приветствие отрисовано и несёт имя.
    await waitFor(() => expect(greetingHeading()).toHaveTextContent(/, Мария Петрова/));
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(greetingHeading().closest("main")).not.toBeNull();
    expect(screen.queryByText(/Рада тебя видеть/)).toBeNull();
    expect(within(header()).queryByText(/Мария Петрова/)).toBeNull();
  });

  // Два РАЗНЫХ отсутствия имени, и оба реальны.
  //
  // `""` — то, что шлёт этот сервер: имя он собирает сам
  // (`client_name or display_name or ""`), то есть ключ приходит всегда, а
  // пустым бывает у человека, который не назвался и чей канал имени не дал.
  //
  // Ключа нет вовсе — контракт это допускает (`display_name?: string`), и
  // так ответит сервер постарше. Сходятся они только на `?? ""`, а это
  // ровно та строка, которую узел и стережёт.
  const NO_NAME_CASES: Array<[string, Record<string, unknown>]> = [
    ["пустая строка от сервера", { ...TODAY, display_name: "" }],
    [
      "ключа нет вовсе",
      Object.fromEntries(
        Object.entries(TODAY).filter(([k]) => k !== "display_name"),
      ),
    ],
  ];

  it.each(NO_NAME_CASES)(
    "без имени (%s): кружок не пустой, приветствие без висящей запятой",
    async (_case, today) => {
      serve(today);
      renderHome();

      // Присутствие первым: без него проверки ниже были бы зелёными и на
      // не отрисовавшемся экране.
      await waitFor(() => expect(greetingHeading()).toBeInTheDocument());
      // «·» — ответ приложения на «имени нет», тот же, что на профиле.
      expect(within(header()).getByText("·")).toBeInTheDocument();
      expect(greetingHeading().textContent).not.toContain(",");
      expect(screen.queryByText(/Мария Петрова/)).toBeNull();
    },
  );

  it("пока день грузится, шапка на месте и не врёт про имя, приветствие — скелетом", async () => {
    let release: (v: unknown) => void = () => undefined;
    const held = new Promise((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        if (u.includes("/wellness/today")) {
          await held;
          return ok(TODAY);
        }
        if (u.includes("/recent-activity")) return ok({ this_week_booking_count: 0 });
        if (u.includes("/plan-lite")) return ok({ plan_lite: null });
        if (u.includes("/plan/current")) return ok({ plan: null });
        if (u.includes("/last-topic")) return ok({ last_topic: null });
        throw new Error(`unexpected fetch: ${u}`);
      }),
    );
    renderHome();

    // Шапка от дня не зависит: знак и вход в профиль — сразу.
    expect(await within(header()).findByText("ayla")).toBeInTheDocument();
    expect(within(header()).getByRole("button", { name: "Мой профиль" })).toBeInTheDocument();
    const greeting = screen.getByRole("region", { name: "Приветствие" });
    expect(greeting.querySelector(".skeleton")).not.toBeNull();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();

    release(null);
    await waitFor(() => expect(greetingHeading()).toHaveTextContent(/Мария Петрова/));
    expect(greeting.querySelector(".skeleton")).toBeNull();
  });

  it("колокольчика нет: ленты уведомлений не существует", async () => {
    renderHome();
    // Присутствие первым: шапка отрисована и несёт своё.
    expect(await within(header()).findByText("ayla")).toBeInTheDocument();
    expect(
      within(header()).queryByRole("button", { name: /уведомл/i }),
    ).toBeNull();
    // Счётчик «2» с доски — обещание, за которым нет источника.
    expect(within(header()).queryByText("2")).toBeNull();
  });
});
