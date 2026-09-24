/**
 * H01, шапка — Д1 (DRF-2331).
 *
 * Источник в этом репозитории: `docs/OPEN_DECISIONS.md` §172 (решение
 * владельца 22.09 по H01, пункт «Вопрос 2 — ассеты»). Первоисточник —
 * `CURRENT_DECISIONS_2026-09-16.md` §61, пункт 7, в дереве `Ayla/docs`:
 * другой репозиторий, ссылка оттуда отсюда не разрешается.
 *
 * Макет рисует в шапке: аватар, «Мария 👋», «Рада вас видеть!» и
 * колокольчик со счётчиком «2».
 *
 * ЧТО ДЕЛАЕТСЯ ЗДЕСЬ: аватар, имя, «Рада вас видеть!».
 *
 * ЧЕГО ЗДЕСЬ НЕТ, И ЭТО РЕШЕНИЕ, А НЕ ЗАБЫВЧИВОСТЬ:
 *
 * * **колокольчик не рисуется.** Ленты уведомлений у клиента не
 *   существует — ни ручки на сервере, ни экрана на клиенте; есть только
 *   `CustomerNotificationSettingsScreen` («настройки уведомлений»), а это
 *   не уведомления. Счётчик «2» взять неоткуда вовсе. Действует правило
 *   самого владельца из того же §61 (М-4, п. 1: «если реальных действий
 *   нет — мёртвый control не нужен») и DRF-1181;
 * * **вордмарк «ayla» остаётся,** хотя макет его в шапке не рисует.
 *   Его держат два внутренних документа: `docs/screens/
 *   customer-main-wellness-dashboard.md` §7 («Header wordmark lowercase
 *   «ayla» preserved») и `ayla-identity-and-brand.md` §7.1. Снять
 *   фирменный знак — решение о бренде, а не правка вёрстки, поэтому он
 *   сохранён и занимает свободное место справа, где макет держал
 *   колокольчик. Строкой в отклонения PR;
 * * **задвоенное приветствие не трогается.** Макет здоровается один раз —
 *   в шапке; у экрана есть свой блок приветствия («Доброе утро, Анна 🌿»)
 *   из `docs/screens/customer-main-wellness-dashboard.md` §7. С шапкой
 *   экран здоровается дважды. Вопрос у владельца; узел на это появится
 *   после его слова, а не раньше: сейчас он закрепил бы одну из трёх
 *   версий как решённую.
 *
 * Д12 (фото у ближайшей записи) в этот лист НЕ входит: на макете это фото
 * процедуры, а поля картинки у услуги нет ни в контракте Ayla
 * (`CatalogSalonServiceDTO`), ни в зеркале. Ждёт выбора владельца.
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
import {
  CustomerWellnessDashboardScreen,
  HEADER_WELCOME_LINE,
} from "./CustomerWellnessDashboardScreen";

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

describe("Д1 — шапка H01 (DRF-2331)", () => {
  it("несёт имя человека из контракта, а не выдуманное", async () => {
    renderHome();
    await waitFor(() =>
      expect(within(header()).getByText(/Мария Петрова/)).toBeInTheDocument(),
    );
  });

  it("здоровается словами макета — дословно", async () => {
    renderHome();
    // Строка вынесена константой: PR обещает «с макета дословно», и
    // сверять обещание надо с одним местом, а не с копией в тесте.
    expect(HEADER_WELCOME_LINE).toBe("Рада вас видеть!");
    await waitFor(() =>
      expect(
        within(header()).getByText(HEADER_WELCOME_LINE),
      ).toBeInTheDocument(),
    );
  });

  it("аватар — инициалы тем же образцом, что у профиля и карточки мастера", async () => {
    renderHome();
    // Фотографии клиента нет нигде в контракте: ни у `wellness/today`, ни
    // у `/me`. Новой сущности под Д1 не заводим — берём `avatarInitials`,
    // который уже рисует кружок на профиле клиента.
    await waitFor(() =>
      expect(within(header()).getByText("МП")).toBeInTheDocument(),
    );
  });

  // Два РАЗНЫХ отсутствия имени, и оба реальны.
  //
  // `""` — то, что шлёт этот сервер: имя он собирает сам
  // (`client_name or display_name or ""`, `views.py` 3760/3778/3994), то
  // есть ключ приходит всегда, а пустым бывает у человека, который не
  // назвался и чей канал имени не дал.
  //
  // Ключа нет вовсе — контракт это допускает (`display_name?: string`), и
  // так ответит сервер постарше. Проверяются оба, потому что сходятся они
  // только на `?? ""`, а это ровно та строка, которую узел и стережёт.
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
    "без имени (%s): кружок не пустой, приветствие на месте, строки имени нет",
    async (_case, today) => {
      serve(today);
      renderHome();

      // Сперва утверждение о НАЛИЧИИ: без него проверки ниже были бы
      // зелёными и на не отрисовавшейся шапке.
      await waitFor(() =>
        expect(
          within(header()).getByText(HEADER_WELCOME_LINE),
        ).toBeInTheDocument(),
      );
      // «·» — ответ приложения на «имени нет», тот же, что на профиле.
      expect(within(header()).getByText("·")).toBeInTheDocument();
      expect(within(header()).queryByText("Мария Петрова")).toBeNull();
      // Строки имени нет КАК УЗЛА, а не «в ней пусто»: без этого сторож
      // пропустил бы жирную пустую строку с одиноким 👋 — ровно то, что
      // осталось бы, потеряйся условие в вёрстке (найдено ревью).
      expect(header().querySelector(".wellness-dash__person-name")).toBeNull();
      expect(within(header()).queryByText("👋")).toBeNull();
    },
  );

  it("пока день грузится, шапка не роняет высоту и не врёт про имя", async () => {
    // Обновление дня (в том числе «Отменить» у стакана воды) возвращает
    // экран в `loading`. Раньше строка имени в этот момент исчезала, и
    // «Рада вас видеть!» прыгала внутри 56 px; теперь место держит скелет.
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
        if (u.includes("/last-topic")) return ok({ last_topic: null });
        throw new Error(`unexpected fetch: ${u}`);
      }),
    );
    renderHome();

    // Наличие — первым: шапка отрисована и здоровается уже на загрузке.
    await waitFor(() =>
      expect(
        within(header()).getByText(HEADER_WELCOME_LINE),
      ).toBeInTheDocument(),
    );
    expect(header().querySelector(".wellness-dash__person-skel")).not.toBeNull();

    release(null);
    await waitFor(() =>
      expect(within(header()).getByText(/Мария Петрова/)).toBeInTheDocument(),
    );
    // Скелет ушёл, как только имя пришло.
    expect(header().querySelector(".wellness-dash__person-skel")).toBeNull();
  });

  it("колокольчика нет: ленты уведомлений не существует", async () => {
    renderHome();
    // Утверждение о наличии — первым: шапка отрисована и несёт своё.
    await waitFor(() =>
      expect(
        within(header()).getByText(HEADER_WELCOME_LINE),
      ).toBeInTheDocument(),
    );
    expect(
      within(header()).queryByRole("button", { name: /уведомл/i }),
    ).toBeNull();
    // Счётчик «2» с макета — обещание, за которым нет источника.
    expect(within(header()).queryByText("2")).toBeNull();
  });

  it("вордмарк остаётся в шапке — названное отклонение от макета", async () => {
    renderHome();
    await waitFor(() =>
      expect(
        within(header()).getByText(HEADER_WELCOME_LINE),
      ).toBeInTheDocument(),
    );
    // `docs/screens/customer-main-wellness-dashboard.md` §7 и
    // `ayla-identity-and-brand.md` §7.1 держат его в шапке. Макет его не
    // рисует; снятие фирменного знака — не правка вёрстки.
    expect(within(header()).getByText("ayla")).toBeInTheDocument();
  });
});
