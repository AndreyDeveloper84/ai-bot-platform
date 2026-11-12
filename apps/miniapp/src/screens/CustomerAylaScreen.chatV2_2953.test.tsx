/**
 * DRF-2953 — чат клиента с Ayla в виде макета владельца «Чат с Ayla.png».
 *
 * Раскладка с макета, слова и поведение — действующие. Узел закрепляет то,
 * что в раскладке легко потерять или дорисовать лишнее:
 *
 * * приветствие — пузырём Ayla и остаётся первой репликой, а не исчезает;
 *   кнопок-подсказок пустого чата нет (слов и поведения для них нет);
 * * время под репликой человека появляется только после ответа сервера;
 * * пустой ответ (бот молчит: вопрос у сотрудника) — не пузырь;
 * * «нет сети»: отправка выключена, набранное цело;
 * * повтор — ТЕМ ЖЕ ключом запроса; новый ключ только когда сервер сам
 *   сказал, что ход упал; 409 на повтор — не ошибка, а исход первого хода;
 * * серверный `detail` «для нас» (английский) человеку не показывается;
 * * микрофона на экране нет (DRF-2427), нижней панели тоже.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  // Разбор кнопки «открыть Mini App» (`parseStartRoute`) — настоящий.
  const actual = await importOriginal<typeof import("../lib/max-sdk")>();
  return {
    ...actual,
    getInitData: () => "",
    setBackButton: vi.fn(),
    onBackButton: vi.fn(() => () => undefined),
    signalReady: vi.fn(),
    applyTheme: vi.fn(),
    hapticImpact: vi.fn(),
    hapticSelection: vi.fn(),
    openExternalLink: vi.fn(),
    closeApp: vi.fn(),
    returnToChat: vi.fn(() => "closed"),
    rememberChatLink: vi.fn(),
  };
});

import { primeDisplayName } from "../components/CustomerAvatarEntry";
import { CLIENT_CHAT_COPY, CustomerAylaChat } from "../components/CustomerAylaChat";
import { RECORDS_ERROR_COPY } from "../components/records/RecordsLoadError";
import { authErrorCopy, OPEN_FROM_MAX_COPY } from "../lib/auth-error-copy";
import { formatTopicWhen, formatTurnWhen } from "../lib/customer-last-topic";
import { CustomerAylaScreen } from "./CustomerAylaScreen";

const GREETING = "Напиши, чем помочь, — продолжим здесь.";

function ok(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function refusal(status: number, error: string, detail: string, details?: unknown): Response {
  return {
    ok: false,
    status,
    statusText: "",
    json: async () => ({ error, detail, ...(details ? { details } : {}) }),
  } as unknown as Response;
}

function answer(text: string, buttons: unknown[] = []): Response {
  return ok({ answer: text, buttons, pending_action: null, cards: [] });
}

type Reply = Response | Error;

/** Провод экрана: история — по очереди из `histories`, ответы — из `replies`. */
function wire(histories: unknown[][], replies: Reply[]) {
  const asked: Array<{ text: string; request_id: string }> = [];
  let historyCalls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: RequestInit) => {
      const u = String(url);
      if (u.includes("/assistant/history")) {
        const messages = histories[Math.min(historyCalls, histories.length - 1)] ?? [];
        historyCalls += 1;
        return ok({ messages });
      }
      if (u.includes("/assistant/ask")) {
        asked.push(JSON.parse(String(init?.body ?? "{}")) as { text: string; request_id: string });
        const reply = replies[asked.length - 1];
        if (reply === undefined) throw new Error("ответов больше, чем заготовлено");
        if (reply instanceof Error) throw reply;
        return reply;
      }
      throw new Error(`unexpected fetch: ${u}`);
    }),
  );
  return { asked, historyCalls: () => historyCalls };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/ayla"]}>
      <Routes>
        <Route path="/customer/ayla" element={<CustomerAylaScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Лента без экрана — там, где нужна короткая пауза переспроса. */
function renderChat() {
  return render(
    <MemoryRouter>
      <CustomerAylaChat greeting={GREETING} onOpen={() => undefined} runningPollMs={1} />
    </MemoryRouter>,
  );
}

const DUPLICATE = "Этот вопрос уже обработан.";

function duplicate(turn?: string): Response {
  return refusal(409, "duplicate_request", DUPLICATE, turn ? { turn } : undefined);
}

function ask(text: string) {
  fireEvent.change(screen.getByRole("textbox", { name: "Вопрос к Ayla" }), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
}

function setOnline(value: boolean) {
  Object.defineProperty(window.navigator, "onLine", { configurable: true, value });
  window.dispatchEvent(new Event(value ? "online" : "offline"));
}

const HISTORY = [
  { id: "m1", role: "user", content: "привет", tool: "", created_at: "2026-09-20T08:30:00+00:00" },
  {
    id: "m2",
    role: "assistant",
    content: "Привет! Чем помочь?",
    tool: "",
    created_at: "2026-09-20T08:30:05+00:00",
  },
];

beforeEach(() => {
  primeDisplayName("Тест Тестов");
});

afterEach(() => {
  setOnline(true);
  primeDisplayName(null);
  vi.unstubAllGlobals();
});

describe("Шапка и состав экрана", () => {
  it("стрелка возврата, имя, дверь в профиль — и ни микрофона, ни нижней панели", async () => {
    wire([[]], []);
    renderScreen();
    await screen.findByText(GREETING);

    expect(screen.getByRole("heading", { level: 1, name: "Ayla" })).toBeInTheDocument();
    expect(screen.getByText("Запись, уход и питание")).toBeInTheDocument();
    // Весь набор кнопок пустого чата: ничего нарисованного «на вырост».
    const names = screen.getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent);
    expect(names).toHaveLength(3);
    expect(names).toEqual(expect.arrayContaining(["Назад", "Отправить"]));
    expect(names.join(" ")).not.toMatch(/микрофон|голос/i);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
});

describe("Слова владельца 12.11 (DRF-2953, п.2–3)", () => {
  it("поле ввода и сбой — на «ты»; у сбоя заголовок «Не удалось получить ответ от Ayla»", async () => {
    wire([[]], [new TypeError("Failed to fetch")]);
    renderScreen();
    await screen.findByText(GREETING);
    expect(screen.getByRole("textbox", { name: "Вопрос к Ayla" })).toHaveAttribute(
      "placeholder",
      "Напиши сообщение…",
    );

    ask("хочу на массаж");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading", { name: "Не удалось получить ответ от Ayla" })).toBeInTheDocument();
    expect(within(alert).getByText("Не получилось отправить. Попробуй ещё раз.")).toBeInTheDocument();
    expect(screen.queryByText(/Попробуйте|Спросите/)).not.toBeInTheDocument();
  });

  it("отказ 400 — словами сервера и без заголовка «не удалось получить ответ»: сервер ответил", async () => {
    wire([[]], [refusal(400, "question_too_long", "Вопрос слишком длинный.")]);
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Вопрос слишком длинный.")).toBeInTheDocument();
    expect(within(alert).queryByRole("heading")).not.toBeInTheDocument();
  });
});

describe("Пустой разговор", () => {
  it("приветствие — пузырём Ayla, без кнопок-подсказок", async () => {
    wire([[]], []);
    const { container } = renderScreen();

    const greeting = await screen.findByText(GREETING);
    expect(greeting.closest(".cchat-bubble--ayla")).not.toBeNull();
    expect(screen.queryByRole("list", { name: "Варианты ответа" })).not.toBeInTheDocument();
    expect(container.querySelectorAll(".cchat-bubble")).toHaveLength(1);
  });

  it("приветствие остаётся первой репликой и после вопроса", async () => {
    wire([[]], [answer("Подскажу.")]);
    const { container } = renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    await screen.findByText("Подскажу.");
    const bubbles = [...container.querySelectorAll(".cchat-bubble")].map((b) => b.textContent);
    expect(bubbles).toEqual([GREETING, "хочу на массаж", "Подскажу."]);
  });

  it("у начатого разговора приветствия нет", async () => {
    wire([HISTORY], []);
    renderScreen();

    await screen.findByText("Привет! Чем помочь?");
    expect(screen.queryByText(GREETING)).not.toBeInTheDocument();
  });

  it("история не загрузилась — приглашение, а не ошибка (решение DRF-2799)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    renderScreen();

    expect(await screen.findByText(GREETING)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("Реплики", () => {
  it("время из истории — под каждой репликой, в поясе устройства", async () => {
    wire([HISTORY], []);
    const { container } = renderScreen();
    await screen.findByText("Привет! Чем помочь?");

    const times = [...container.querySelectorAll("time")];
    expect(times.map((t) => t.getAttribute("datetime"))).toEqual(HISTORY.map((m) => m.created_at));
    expect(times[0]).toHaveTextContent(formatTurnWhen(HISTORY[0]!.created_at));
  });

  it("время под репликой человека появляется только после ответа сервера", async () => {
    let release: (r: Response) => void = () => undefined;
    const slow = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        if (u.includes("/assistant/history")) return ok({ messages: [] });
        if (u.includes("/assistant/ask")) return slow;
        throw new Error(`unexpected fetch: ${u}`);
      }),
    );
    const { container } = renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    expect(await screen.findByText("Ayla думает…")).toBeInTheDocument();
    expect(container.querySelectorAll("time")).toHaveLength(0);

    release(answer("Подскажу."));

    await screen.findByText("Подскажу.");
    expect(container.querySelectorAll("time")).toHaveLength(2);
    expect(screen.queryByText("Ayla думает…")).not.toBeInTheDocument();
  });

  it("длинный ответ — абзацами, а не одной простынёй", async () => {
    wire([[]], [answer("Классический массаж расслабляет.\n\nВот что важно знать:\n— не на голодный желудок")]);
    renderScreen();
    await screen.findByText(GREETING);

    ask("расскажи про массаж");

    const first = await screen.findByText("Классический массаж расслабляет.");
    const bubble = first.closest(".cchat-bubble")!;
    expect(bubble.querySelectorAll("p")).toHaveLength(2);
    // Одиночный перенос внутри абзаца остаётся переносом, а не новым абзацем.
    expect(bubble.querySelectorAll("p")[1]!.textContent).toBe(
      "Вот что важно знать:\n— не на голодный желудок",
    );
  });

  it("бот молчит (вопрос у сотрудника) — пустого пузыря нет, реплика человека принята", async () => {
    wire([HISTORY], [answer("")]);
    const { container } = renderScreen();
    await screen.findByText("Привет! Чем помочь?");

    ask("ау");

    await waitFor(() => expect(container.querySelectorAll("time")).toHaveLength(3));
    const bubbles = [...container.querySelectorAll(".cchat-bubble")].map((b) => b.textContent);
    expect(bubbles).toEqual(["привет", "Привет! Чем помочь?", "ау"]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ответ без текста, но с кнопками — кнопки есть, пузыря нет", async () => {
    wire([HISTORY], [answer("", [{ label: "Мой план", open_app: "open_plan" }])]);
    const { container } = renderScreen();
    await screen.findByText("Привет! Чем помочь?");

    ask("план");

    const list = await screen.findByRole("list", { name: "Варианты ответа" });
    expect(within(list).getByRole("button", { name: "Мой план" })).toBeInTheDocument();
    expect(container.querySelectorAll(".cchat-bubble--ayla")).toHaveLength(1);
  });
});

describe("Лента: порядок и состав", () => {
  it("знак Ayla — один на подряд идущие её реплики", async () => {
    wire(
      [
        [
          ...HISTORY,
          { id: "m3", role: "assistant", content: "И ещё.", tool: "", created_at: "2026-09-20T08:30:09+00:00" },
        ],
      ],
      [],
    );
    const { container } = renderScreen();
    await screen.findByText("И ещё.");

    expect(container.querySelectorAll(".cchat-mark")).toHaveLength(2);
    expect(container.querySelectorAll(".cchat-mark--blank")).toHaveLength(1);
  });

  it("команда и токен в ленте не рисуются, но уходят (DRF-2151)", async () => {
    const { asked } = wire([HISTORY], [answer("Привет ещё раз.")]);
    const { container } = renderScreen();
    await screen.findByText("Привет! Чем помочь?");

    ask("/start");

    await screen.findByText("Привет ещё раз.");
    expect(asked[0]!.text).toBe("/start");
    expect(screen.queryByText("/start")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".cchat-bubble--mine")).toHaveLength(1);
  });

  it("пока история не пришла, вопрос не уходит: она заменила бы ленту вместе с ним", async () => {
    let release: (r: Response) => void = () => undefined;
    const slow = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const fetchMock = vi.fn(async (url: unknown) => {
      if (String(url).includes("/assistant/history")) return slow;
      return answer("не должен понадобиться");
    });
    vi.stubGlobal("fetch", fetchMock);
    renderScreen();
    await screen.findByText("Загружаю диалог…");

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "хочу на массаж" } });
    expect(screen.getByRole("button", { name: "Отправить" })).toBeDisabled();
    fireEvent.submit(screen.getByRole("textbox").closest("form")!);
    expect(fetchMock.mock.calls.filter(([u]) => String(u).includes("/assistant/ask"))).toHaveLength(0);

    release(ok({ messages: HISTORY }));

    await screen.findByText("Привет! Чем помочь?");
    expect(screen.getByRole("button", { name: "Отправить" })).toBeEnabled();
    expect(screen.getByRole("textbox")).toHaveValue("хочу на массаж");
  });

  it("карточка сбоя стоит ДО метки конца ленты — прокрутка ставит её над полем ввода", async () => {
    wire([HISTORY], [new TypeError("Failed to fetch")]);
    const { container } = renderScreen();
    await screen.findByText("Привет! Чем помочь?");

    ask("хочу на массаж");

    const alert = await screen.findByRole("alert");
    const end = container.querySelector(".cchat-list__end")!;
    const form = container.querySelector("form")!;
    expect(alert.compareDocumentPosition(end) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(end.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Живая область карточки — снаружи ленты: `log` читает реплики сам.
    expect(screen.getByRole("log").contains(alert)).toBe(false);
  });

  it("нажатая кнопка исчезает — фокус остаётся на ленте, а не теряется", async () => {
    wire([[]], [new TypeError("Failed to fetch"), answer("Подскажу.")]);
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");
    fireEvent.click(await screen.findByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    await screen.findByText("Подскажу.");
    expect(screen.getByRole("log")).toHaveFocus();
  });
});

describe("Нет сети", () => {
  it("кнопки ответа без сети выключены", async () => {
    wire([HISTORY], [answer("Записать тебя?", [{ label: "📅 Записаться", payload: "cb:menu:book" }])]);
    renderScreen();
    await screen.findByText("Привет! Чем помочь?");
    ask("хочу на массаж");
    const reply = await screen.findByRole("button", { name: "📅 Записаться" });

    setOnline(false);

    await waitFor(() => expect(reply).toBeDisabled());
  });


  it("состояние словами раздела, отправка выключена, набранное цело; сеть вернулась — можно слать", async () => {
    const { asked } = wire([HISTORY], [answer("Подскажу.")]);
    renderScreen();
    await screen.findByText("Привет! Чем помочь?");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "хочу на массаж" } });

    setOnline(false);

    const state = await screen.findByRole("status");
    expect(within(state).getByText(RECORDS_ERROR_COPY.offlineTitle)).toBeInTheDocument();
    expect(within(state).getByText(RECORDS_ERROR_COPY.offlineBody)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отправить" })).toBeDisabled();
    expect(screen.getByRole("textbox")).toHaveValue("хочу на массаж");
    // Разговор при этом виден: человеку нужно перечитать ответ.
    expect(screen.getByText("Привет! Чем помочь?")).toBeInTheDocument();
    // Не только кнопка: сама отправка формы тоже никуда не уходит.
    fireEvent.submit(screen.getByRole("textbox").closest("form")!);
    expect(asked).toHaveLength(0);

    setOnline(true);

    await waitFor(() => expect(screen.getByRole("button", { name: "Отправить" })).toBeEnabled());
    expect(screen.queryByText(RECORDS_ERROR_COPY.offlineTitle)).not.toBeInTheDocument();
  });
});

describe("Сбой и повтор", () => {
  it("ответа не было: прежние слова, повтор — ТЕМ ЖЕ ключом, реплика человека одна", async () => {
    const { asked } = wire([[]], [new TypeError("Failed to fetch"), answer("Подскажу.")]);
    const { container } = renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(CLIENT_CHAT_COPY.failed)).toBeInTheDocument();
    expect(within(alert).getByRole("link", { name: RECORDS_ERROR_COPY.support })).toBeInTheDocument();

    fireEvent.click(within(alert).getByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    await screen.findByText("Подскажу.");
    expect(asked).toHaveLength(2);
    expect(asked[1]!.request_id).toBe(asked[0]!.request_id);
    expect(asked[1]!.text).toBe("хочу на массаж");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".cchat-bubble--mine")).toHaveLength(1);
  });

  it("сервер сказал «ход упал»: его слова, повтор — НОВЫМ ключом (прежний занят)", async () => {
    const said = "Не получилось ответить. Попробуй ещё раз.";
    const { asked } = wire([[]], [refusal(502, "turn_failed", said), answer("Подскажу.")]);
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(said)).toBeInTheDocument();
    fireEvent.click(within(alert).getByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    await screen.findByText("Подскажу.");
    expect(asked[1]!.request_id).not.toBe(asked[0]!.request_id);
  });

  it("повтор, а первый ход уже прошёл: ответ дочитывается из истории, ошибки нет", async () => {
    const after = [
      { id: "s1", role: "user", content: "хочу на массаж", tool: "", created_at: "2026-09-20T09:00:00+00:00" },
      { id: "s2", role: "assistant", content: "Подскажу.", tool: "", created_at: "2026-09-20T09:00:04+00:00" },
    ];
    const { asked, historyCalls } = wire(
      [[], after],
      [
        new TypeError("Failed to fetch"),
        refusal(409, "duplicate_request", "Этот вопрос уже обработан.", { turn: "done" }),
      ],
    );
    const { container } = renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");
    fireEvent.click(await screen.findByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    expect(await screen.findByText("Подскажу.")).toBeInTheDocument();
    expect(asked[1]!.request_id).toBe(asked[0]!.request_id);
    expect(historyCalls()).toBe(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Этот вопрос уже обработан.")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".cchat-bubble--mine")).toHaveLength(1);
  });

  it("повтор, а первый ход ещё идёт: экран ждёт и переспрашивает тем же ключом; дождался — ответ из истории", async () => {
    const after = [
      { id: "s1", role: "user", content: "хочу на массаж", tool: "", created_at: "2026-09-20T09:00:00+00:00" },
      { id: "s2", role: "assistant", content: "Подскажу.", tool: "", created_at: "2026-09-20T09:00:04+00:00" },
    ];
    const { asked } = wire(
      [[], after],
      [new TypeError("Failed to fetch"), duplicate("running"), duplicate("running"), duplicate("done")],
    );
    renderChat();
    await screen.findByText(GREETING);

    ask("хочу на массаж");
    fireEvent.click(await screen.findByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    expect(await screen.findByText("Подскажу.")).toBeInTheDocument();
    expect(asked).toHaveLength(4);
    expect(new Set(asked.map((a) => a.request_id)).size).toBe(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ход так и идёт: «не получилось отправить» не говорится — сервер вопрос принял", async () => {
    const { asked, historyCalls } = wire(
      [[]],
      [
        new TypeError("Failed to fetch"),
        duplicate("running"),
        duplicate("running"),
        duplicate("running"),
        duplicate("running"),
        answer("не должен понадобиться"),
      ],
    );
    renderChat();
    await screen.findByText(GREETING);

    ask("хочу на массаж");
    fireEvent.click(await screen.findByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    // Тап и три переспроса — и только потом карточка.
    await waitFor(() => expect(asked).toHaveLength(5));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(RECORDS_ERROR_COPY.failedBody)).toBeInTheDocument();
    // Заголовок кадра 15 — и у «исход неизвестен».
    expect(within(alert).getByRole("heading", { name: CLIENT_CHAT_COPY.failedTitle })).toBeInTheDocument();
    expect(screen.queryByText(CLIENT_CHAT_COPY.failed)).not.toBeInTheDocument();
    expect(screen.queryByText(DUPLICATE)).not.toBeInTheDocument();
    expect(new Set(asked.map((a) => a.request_id)).size).toBe(1);
    expect(historyCalls()).toBe(1);
  });

  it("повтор, а первый ход упал без нас: вопрос сразу уходит заново новым ключом", async () => {
    const { asked } = wire(
      [[]],
      [new TypeError("Failed to fetch"), duplicate("failed"), answer("Подскажу.")],
    );
    const { container } = renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");
    fireEvent.click(await screen.findByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    // Один тап: человек не видит ту же карточку второй раз.
    await screen.findByText("Подскажу.");
    expect(asked).toHaveLength(3);
    expect(asked[1]!.request_id).toBe(asked[0]!.request_id);
    expect(asked[2]!.request_id).not.toBe(asked[0]!.request_id);
    expect(container.querySelectorAll(".cchat-bubble--mine")).toHaveLength(1);
  });

  it("ход прошёл, а история не прочиталась: нейтральные слова, ключ прежний, повтор дочитывает", async () => {
    const after = [
      { id: "s1", role: "user", content: "хочу на массаж", tool: "", created_at: "2026-09-20T09:00:00+00:00" },
      { id: "s2", role: "assistant", content: "Подскажу.", tool: "", created_at: "2026-09-20T09:00:04+00:00" },
    ];
    const asked: Array<{ request_id: string }> = [];
    let historyCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown, init?: RequestInit) => {
        const u = String(url);
        if (u.includes("/assistant/history")) {
          historyCalls += 1;
          if (historyCalls === 1) return ok({ messages: [] });
          if (historyCalls === 2) throw new TypeError("Failed to fetch");
          return ok({ messages: after });
        }
        asked.push(JSON.parse(String(init?.body)) as { request_id: string });
        if (asked.length === 1) throw new TypeError("Failed to fetch");
        return duplicate("done");
      }),
    );
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");
    fireEvent.click(await screen.findByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    const alert = await screen.findByText(RECORDS_ERROR_COPY.failedBody);
    expect(screen.queryByText(CLIENT_CHAT_COPY.failed)).not.toBeInTheDocument();
    fireEvent.click(within(alert.closest("section")!).getByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    expect(await screen.findByText("Подскажу.")).toBeInTheDocument();
    expect(new Set(asked.map((a) => a.request_id)).size).toBe(1);
  });

  it("сервер до этой правки (409 без отметки): показывается то, что сервер знает, — история", async () => {
    const after = [
      { id: "s1", role: "user", content: "хочу на массаж", tool: "", created_at: "2026-09-20T09:00:00+00:00" },
      { id: "s2", role: "assistant", content: "Подскажу.", tool: "", created_at: "2026-09-20T09:00:04+00:00" },
    ];
    wire([[], after], [new TypeError("Failed to fetch"), duplicate()]);
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");
    fireEvent.click(await screen.findByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    expect(await screen.findByText("Подскажу.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("обрыв на прокси (ответ не JSON): ход мог пройти — ключ прежний", async () => {
    const gateway = {
      ok: false,
      status: 504,
      statusText: "Gateway Time-out",
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    } as unknown as Response;
    const { asked } = wire([[]], [gateway, answer("Подскажу.")]);
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(CLIENT_CHAT_COPY.failed)).toBeInTheDocument();
    expect(screen.queryByText(/Gateway/)).not.toBeInTheDocument();
    fireEvent.click(within(alert).getByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    await screen.findByText("Подскажу.");
    expect(asked[1]!.request_id).toBe(asked[0]!.request_id);
  });

  it("сбой быстрого ответа: повтор шлёт тот же payload тем же ключом, подпись в ленте одна", async () => {
    const { asked } = wire(
      [HISTORY],
      [
        answer("Записать тебя?", [{ label: "📅 Записаться", payload: "cb:menu:book" }]),
        new TypeError("Failed to fetch"),
        answer("Выбери услугу."),
      ],
    );
    renderScreen();
    await screen.findByText("Привет! Чем помочь?");

    ask("хочу на массаж");
    fireEvent.click(await screen.findByRole("button", { name: "📅 Записаться" }));
    fireEvent.click(await screen.findByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    await screen.findByText("Выбери услугу.");
    expect(asked[2]).toEqual(asked[1]);
    expect(asked[2]!.text).toBe("cb:menu:book");
    expect(screen.getAllByText("📅 Записаться")).toHaveLength(1);
    expect(screen.queryByText("cb:menu:book")).not.toBeInTheDocument();
  });

  it("второй «ход упал» подряд — карточка сбоя, а не круг перевыпусков ключа", async () => {
    const { asked } = wire(
      [[]],
      [new TypeError("Failed to fetch"), duplicate("failed"), duplicate("failed"), answer("не должен понадобиться")],
    );
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");
    fireEvent.click(await screen.findByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(CLIENT_CHAT_COPY.failed)).toBeInTheDocument();
    expect(asked).toHaveLength(3);
  });

  it("лимит вопросов: английский detail человеку не показывается, ключ прежний", async () => {
    const { asked } = wire(
      [[]],
      [refusal(429, "ask_rate_limited", "too many questions, retry in a minute"), answer("Подскажу.")],
    );
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(CLIENT_CHAT_COPY.failed)).toBeInTheDocument();
    expect(screen.queryByText(/too many/)).not.toBeInTheDocument();

    fireEvent.click(within(alert).getByRole("button", { name: RECORDS_ERROR_COPY.retry }));
    await screen.findByText("Подскажу.");
    expect(asked[1]!.request_id).toBe(asked[0]!.request_id);
  });

  it("отказ, который повтором не лечится: слова сервера, кнопки повтора нет", async () => {
    const said = "Разговор в приложении доступен из MAX.";
    wire([[]], [refusal(400, "not_supported", said)]);
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(said)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: RECORDS_ERROR_COPY.retry })).not.toBeInTheDocument();
  });

  it("вход не из MAX — возврат в мессенджер, а не повтор", async () => {
    wire([[]], [refusal(401, "no_init_data", "Mini App opens only from MAX with valid initData")]);
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    expect(await screen.findByText(OPEN_FROM_MAX_COPY.title)).toBeInTheDocument();
    expect(screen.queryByText(/initData/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: RECORDS_ERROR_COPY.retry })).not.toBeInTheDocument();
  });

  it("400 со слагом входа — это разбор тела, а не отказ входа: в MAX не отправляем", async () => {
    wire([[]], [refusal(400, "malformed", "request_id must be a UUID")]);
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(CLIENT_CHAT_COPY.failed)).toBeInTheDocument();
    expect(screen.queryByText(OPEN_FROM_MAX_COPY.title)).not.toBeInTheDocument();
    expect(screen.queryByText(/UUID/)).not.toBeInTheDocument();
  });

  it("временный сбой входа на сервере: слова отказа входа и повтор", async () => {
    const copy = authErrorCopy("server_misconfigured");
    const { asked } = wire(
      [[]],
      [refusal(500, "server_misconfigured", "bot token is not configured"), answer("Подскажу.")],
    );
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(copy.title)).toBeInTheDocument();
    expect(screen.queryByText(/bot token/)).not.toBeInTheDocument();
    fireEvent.click(within(alert).getByRole("button", { name: RECORDS_ERROR_COPY.retry }));

    await screen.findByText("Подскажу.");
    expect(asked[1]!.request_id).toBe(asked[0]!.request_id);
  });

  it("брошенный вопрос не теряет свой ответ: после нового вопроса лента дочитывается, кнопки на месте", async () => {
    const after = [
      { id: "s1", role: "user", content: "мне плохо", tool: "", created_at: "2026-09-20T09:00:00+00:00" },
      { id: "s2", role: "assistant", content: "Это вопрос к специалисту.", tool: "", created_at: "2026-09-20T09:00:04+00:00" },
      { id: "s3", role: "user", content: "а про питание?", tool: "", created_at: "2026-09-20T09:01:00+00:00" },
      { id: "s4", role: "assistant", content: "Про питание.", tool: "", created_at: "2026-09-20T09:01:04+00:00" },
    ];
    wire(
      [[], after],
      [new TypeError("Failed to fetch"), answer("Про питание.", [{ label: "Мой план", open_app: "open_plan" }])],
    );
    const { container } = renderScreen();
    await screen.findByText(GREETING);

    ask("мне плохо");
    await screen.findByRole("alert");
    ask("а про питание?");

    // Ход брошенного вопроса на сервере прошёл — его ответ обязан появиться.
    expect(await screen.findByText("Это вопрос к специалисту.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Мой план" })).toBeInTheDocument();
    expect(container.querySelectorAll(".cchat-bubble--mine")).toHaveLength(2);
  });

  it("брошенный вопрос, а ответ нового — из двух реплик: лента из истории, кнопки под последней (ревью #2463)", async () => {
    const after = [
      { id: "s1", role: "user", content: "мне плохо", tool: "", created_at: "2026-09-20T09:00:00+00:00" },
      { id: "s2", role: "assistant", content: "Это вопрос к специалисту.", tool: "", created_at: "2026-09-20T09:00:04+00:00" },
      { id: "s3", role: "user", content: "а про питание?", tool: "", created_at: "2026-09-20T09:01:00+00:00" },
      { id: "s4", role: "assistant", content: "Сначала коротко.", tool: "", created_at: "2026-09-20T09:01:04+00:00" },
      { id: "s5", role: "assistant", content: "Про питание.", tool: "", created_at: "2026-09-20T09:01:05+00:00" },
    ];
    wire(
      [[], after],
      [
        new TypeError("Failed to fetch"),
        answer("Сначала коротко.\n\nПро питание.", [{ label: "Мой план", open_app: "open_plan" }]),
      ],
    );
    renderScreen();
    await screen.findByText(GREETING);

    ask("мне плохо");
    await screen.findByRole("alert");
    ask("а про питание?");

    expect(await screen.findByText("Это вопрос к специалисту.")).toBeInTheDocument();
    expect(screen.getByText("Сначала коротко.")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Варианты ответа" });
    expect(within(list).getByRole("button", { name: "Мой план" })).toBeInTheDocument();
    // Склеенный ответ хода не дублирует реплики истории.
    expect(screen.getAllByText("Про питание.")).toHaveLength(1);
  });

  it("брошенный вопрос, а ответа нового хода в истории нет: лента из истории, ответ дописан — дубль лучше потери", async () => {
    const after = [
      { id: "s1", role: "user", content: "а про питание?", tool: "", created_at: "2026-09-20T09:01:00+00:00" },
      { id: "s2", role: "assistant", content: "Что-то другое.", tool: "", created_at: "2026-09-20T09:01:04+00:00" },
    ];
    wire(
      [[], after],
      [new TypeError("Failed to fetch"), answer("Про питание.", [{ label: "Мой план", open_app: "open_plan" }])],
    );
    renderScreen();
    await screen.findByText(GREETING);

    ask("мне плохо");
    await screen.findByRole("alert");
    ask("а про питание?");

    expect(await screen.findByText("Про питание.")).toBeInTheDocument();
    expect(screen.getByText("Что-то другое.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Мой план" })).toBeInTheDocument();
  });

  it("брошенный вопрос, а ответ консьержа в истории без строки памяти: кнопки находят свою реплику", async () => {
    // Консьерж пишет строку сам, а «Запомнила: …» дописывается к ответу
    // позже и в историю не идёт (`memory_announce`): строка — НАЧАЛО ответа.
    const after = [
      { id: "s1", role: "user", content: "а про питание?", tool: "", created_at: "2026-09-20T09:01:00+00:00" },
      { id: "s2", role: "assistant", content: "Про питание.", tool: "", created_at: "2026-09-20T09:01:04+00:00" },
    ];
    wire(
      [[], after],
      [
        new TypeError("Failed to fetch"),
        answer("Про питание.\n\nЗапомнила: без молочного.", [{ label: "Мой план", open_app: "open_plan" }]),
      ],
    );
    renderScreen();
    await screen.findByText(GREETING);

    ask("мне плохо");
    await screen.findByRole("alert");
    ask("а про питание?");

    const list = await screen.findByRole("list", { name: "Варианты ответа" });
    expect(within(list).getByRole("button", { name: "Мой план" })).toBeInTheDocument();
    expect(screen.getAllByText("Про питание.")).toHaveLength(1);
    // Строки памяти в истории нет — она не теряется: дописана к своей реплике,
    // ровно один раз (анонс говорится один раз на факт, §52 В3).
    expect(screen.getAllByText("Запомнила: без молочного.")).toHaveLength(1);
  });

  it("брошенный вопрос: короткая чужая реплика «Хорошо.» не становится якорем ответа", async () => {
    // Новый вопрос — тап кнопки: в историю он не пишется, и последней
    // реплике человека в истории предшествует чужой ответ.
    const after = [
      { id: "s1", role: "user", content: "мне плохо", tool: "", created_at: "2026-09-20T09:00:00+00:00" },
      { id: "s2", role: "assistant", content: "Хорошо.", tool: "", created_at: "2026-09-20T09:00:04+00:00" },
    ];
    wire(
      [[], after],
      [
        new TypeError("Failed to fetch"),
        answer("Хорошо, подберу.\n\nВыбери день.", [{ label: "Мой план", open_app: "open_plan" }]),
      ],
    );
    renderScreen();
    await screen.findByText(GREETING);

    ask("мне плохо");
    await screen.findByRole("alert");
    ask("а когда можно?");

    // Ответ нового хода дописан целиком и с кнопками; вопрос — тоже.
    expect(await screen.findByText("Хорошо, подберу.")).toBeInTheDocument();
    expect(screen.getByText("Выбери день.")).toBeInTheDocument();
    expect(screen.getByText("а когда можно?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Мой план" })).toBeInTheDocument();
  });

  it("брошенный вопрос: после нашего ответа в истории легла ещё реплика — кнопки не пропадают", async () => {
    const after = [
      { id: "s1", role: "user", content: "а про питание?", tool: "", created_at: "2026-09-20T09:01:00+00:00" },
      { id: "s2", role: "assistant", content: "Про питание.", tool: "", created_at: "2026-09-20T09:01:04+00:00" },
      { id: "s3", role: "assistant", content: "Ответ на прежний вопрос.", tool: "", created_at: "2026-09-20T09:01:06+00:00" },
    ];
    wire(
      [[], after],
      [new TypeError("Failed to fetch"), answer("Про питание.", [{ label: "Мой план", open_app: "open_plan" }])],
    );
    renderScreen();
    await screen.findByText(GREETING);

    ask("мне плохо");
    await screen.findByRole("alert");
    ask("а про питание?");

    expect(await screen.findByText("Ответ на прежний вопрос.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Мой план" })).toBeInTheDocument();
  });

  it("новый вопрос поверх сбоя — новый ход своим ключом, карточка сбоя снята", async () => {
    // Брошенный вопрос не дошёл: в истории только новый вопрос и ответ.
    const after = [
      { id: "s1", role: "user", content: "а про питание?", tool: "", created_at: "2026-09-20T09:01:00+00:00" },
      { id: "s2", role: "assistant", content: "Про питание.", tool: "", created_at: "2026-09-20T09:01:04+00:00" },
    ];
    const { asked } = wire([[], after], [new TypeError("Failed to fetch"), answer("Про питание.")]);
    renderScreen();
    await screen.findByText(GREETING);

    ask("хочу на массаж");
    await screen.findByRole("alert");
    ask("а про питание?");

    await screen.findByText("Про питание.");
    expect(asked[1]!.text).toBe("а про питание?");
    expect(asked[1]!.request_id).not.toBe(asked[0]!.request_id);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("formatTurnWhen", () => {
  const now = new Date(2026, 8, 20, 18, 0, 0);

  it("сегодняшняя реплика — только время", () => {
    const at = new Date(2026, 8, 20, 11, 30, 0);
    expect(formatTurnWhen(at.toISOString(), now)).toBe(
      at.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }),
    );
  });

  it("прежняя — с датой, как у «Последней темы» той же нити", () => {
    const at = new Date(2026, 8, 18, 11, 30, 0).toISOString();
    expect(formatTurnWhen(at, now)).toBe(formatTopicWhen(at, now));
  });

  it("времени нет или оно нечитаемо — пусто", () => {
    expect(formatTurnWhen("", now)).toBe("");
    expect(formatTurnWhen("не дата", now)).toBe("");
  });
});
