/**
 * Вход в анкету цели из приложения (решение владельца 30.08).
 *
 * До этой правки единственный вход в `/customer/goal-select` жил в
 * стартовой сетке БОТА. Человек, открывший мини-апп, попадал на
 * «Мои записи» и анкету не встречал никогда.
 *
 * Новизна определяется НЕ эвристикой «нет записей»: сервер уже отвечает
 * на этот вопрос документом decision-context, поле `missing`. Экран
 * ничего не решает сам — если `missing` непуст, приглашение есть; если
 * пуст, приглашения нет. Поэтому каждая пара тестов ниже гоняет ОБЕ
 * ветки на ОДНИХ И ТЕХ ЖЕ данных записей: отрицательное утверждение
 * («человеку с целью не предлагаем») без положительной стражи на тех же
 * данных зелено даже на пустом экране.
 *
 * Никаких литеральных дат — только смещения от `now`.
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchMyBookings: vi.fn() };
});

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return {
    ...original,
    fetchDecisionContext: vi.fn(),
    postGoalSelect: vi.fn(),
  };
});

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), onBackButton: vi.fn() };
});

import { fetchMyBookings, type BookingItem } from "../lib/api";
import {
  fetchDecisionContext,
  type DecisionContext,
} from "../lib/customer-goals";
import { onBackButton, setBackButton } from "../lib/max-sdk";
import { CustomerRecordsScreen } from "./CustomerRecordsScreen";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedList = vi.mocked(fetchMyBookings);
const mockedContext = vi.mocked(fetchDecisionContext);
const mockedSetBackButton = vi.mocked(setBackButton);
const mockedOnBackButton = vi.mocked(onBackButton);

// ---------------------------------------------------------------------------
// Фикстуры. Тексты подсказок — дословно серверный контракт
// (`goals/decision_context.py`, PROMPT_GOAL_MISSING / _CLARIFICATION).
// ---------------------------------------------------------------------------

const PROMPT_GOAL_MISSING =
  "Что хочешь изменить или как хочешь себя чувствовать?";
const PROMPT_GOAL_CLARIFICATION =
  "Записала: «хочу выглядеть отдохнувшей». Расскажи чуть подробнее — что для тебя важнее всего?";

const SUGGESTIONS = [
  { key: "relax", label: "Расслабиться и восстановиться" },
  { key: "beauty", label: "Ухоженный вид" },
];

const INTENTS: DecisionContext["intents"] = [
  { id: "choose_suggested", label: "Выбрать из предложенного" },
  { id: "formulate_own", label: "Сформулирую своими словами" },
  { id: "need_guidance", label: "Не понимаю, чего хочу" },
];

function isoInHours(hours: number): string {
  return new Date(Date.now() + hours * 3_600_000).toISOString();
}

/** Сервер: цели нет вовсе — `missing` содержит kind=goal. */
const DOC_GOAL_MISSING: DecisionContext = {
  version: 1,
  known: { goal: null },
  missing: [{ kind: "goal", prompt: PROMPT_GOAL_MISSING }],
  suggestions: SUGGESTIONS,
  intents: INTENTS,
};

/** Сервер: цель выбрана и уточнять нечего — `missing` пуст. */
const DOC_GOAL_KNOWN: DecisionContext = {
  version: 1,
  known: {
    goal: {
      goal_key: "relax",
      goal_text: null,
      selected_at: isoInHours(-72),
      source_channel: "bot",
    },
  },
  missing: [],
  suggestions: SUGGESTIONS,
  intents: INTENTS,
};

/** Сервер: цель есть, но свободным текстом — просит уточнить. */
const DOC_GOAL_CLARIFICATION: DecisionContext = {
  version: 1,
  known: {
    goal: {
      goal_key: null,
      goal_text: "хочу выглядеть отдохнувшей",
      selected_at: isoInHours(-5),
      source_channel: "miniapp",
    },
  },
  missing: [
    { kind: "goal_clarification", prompt: PROMPT_GOAL_CLARIFICATION },
  ],
  suggestions: SUGGESTIONS,
  intents: INTENTS,
};

function booking(
  partial: Partial<BookingItem> & Pick<BookingItem, "id">,
): BookingItem {
  return {
    status: "confirmed",
    service_id: "svc-1",
    service_name: "Маникюр",
    master_id: "mst-1",
    master_name: "Анна Соколова",
    visit_at: isoInHours(48),
    duration_min: 90,
    cancel_requested_at: null,
    undo_window_seconds: 300,
    cancellable: true,
    reschedulable: true,
    rating: null,
    can_rate: false,
    ...partial,
  };
}

const UPCOMING: BookingItem[] = [
  booking({ id: "b-1", visit_at: isoInHours(20) }),
  booking({ id: "b-2", service_name: "Массаж", visit_at: isoInHours(70) }),
];

function mockLists(upcoming: BookingItem[], history: BookingItem[]) {
  mockedList.mockImplementation((params) =>
    Promise.resolve({
      items: params?.past ? history : upcoming,
      next_cursor: null,
    }),
  );
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <Routes>
        <Route path="/customer/main" element={<CustomerRecordsScreen />} />
        <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
        <Route path="/customer/records/:bookingId" element={<div>BOOKING-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const GOAL_CTA = "Выбрать цель";

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Пара 1 — новый человек: записей нет. Обе ветки на ОДНИХ данных.
// ---------------------------------------------------------------------------

describe("пустые записи — приглашение зависит от сервера, не от пустоты", () => {
  it("без цели: приглашение с серверным вопросом есть", async () => {
    mockLists([], []);
    mockedContext.mockResolvedValue(DOC_GOAL_MISSING);
    renderScreen();

    expect(await screen.findByText(PROMPT_GOAL_MISSING)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: GOAL_CTA }),
    ).toBeInTheDocument();
  });

  it("с целью: приглашения нет — при том, что экран отрисован (те же данные)", async () => {
    mockLists([], []);
    mockedContext.mockResolvedValue(DOC_GOAL_KNOWN);
    renderScreen();

    // Положительная стража: экран действительно отрисовался.
    expect(await screen.findByText(/Пока записей нет/)).toBeInTheDocument();

    expect(
      screen.queryByRole("button", { name: GOAL_CTA }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(PROMPT_GOAL_MISSING)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Пара 2 — «нет записей» не равно «нет цели»: человек С записями, но без
// цели, тоже должен встретить анкету.
// ---------------------------------------------------------------------------

describe("записи есть — новизну определяет missing, а не число записей", () => {
  it("без цели: приглашение есть даже поверх непустого списка", async () => {
    mockLists(UPCOMING, []);
    mockedContext.mockResolvedValue(DOC_GOAL_MISSING);
    renderScreen();

    // Положительная стража: список записей на месте.
    expect(
      await screen.findByRole("tab", { name: "Ближайшие (2)" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Маникюр")).toBeInTheDocument();

    expect(screen.getByText(PROMPT_GOAL_MISSING)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: GOAL_CTA })).toBeInTheDocument();
  });

  it("с целью: приглашения нет — на тех же записях", async () => {
    mockLists(UPCOMING, []);
    mockedContext.mockResolvedValue(DOC_GOAL_KNOWN);
    renderScreen();

    expect(
      await screen.findByRole("tab", { name: "Ближайшие (2)" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Маникюр")).toBeInTheDocument();

    expect(
      screen.queryByRole("button", { name: GOAL_CTA }),
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Сервер просит уточнить — приглашение показывает серверный вопрос as-is.
// ---------------------------------------------------------------------------

describe("уточнение цели — экран не интерпретирует kind", () => {
  it("goal_clarification: показан серверный вопрос, а не выдуманный", async () => {
    mockLists([], []);
    mockedContext.mockResolvedValue(DOC_GOAL_CLARIFICATION);
    renderScreen();

    expect(
      await screen.findByText(PROMPT_GOAL_CLARIFICATION),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: GOAL_CTA })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Текст ничего не обещает. GOAL_RESOLUTION_ENABLED на пилоте выключен —
// цель пока ни на что не влияет, значит приглашение не имеет права
// обещать подбор/рекомендации.
// ---------------------------------------------------------------------------

describe("приглашение не обещает того, чего код не делает", () => {
  it("в приглашении нет обещаний подбора и рекомендаций", async () => {
    mockLists([], []);
    mockedContext.mockResolvedValue(DOC_GOAL_MISSING);
    renderScreen();

    const cta = await screen.findByRole("button", { name: GOAL_CTA });
    const invite = cta.closest("section");
    expect(invite).not.toBeNull();
    const text = invite!.textContent ?? "";

    expect(text).toMatch(PROMPT_GOAL_MISSING);
    for (const promise of [
      /подбер/i,
      /порекоменд/i,
      /рекомендац/i,
      /персонализ/i,
      /подойдут/i,
      /под твою цель/i,
    ]) {
      expect(text).not.toMatch(promise);
    }
  });
});

// ---------------------------------------------------------------------------
// Дорога к записи не перекрыта.
// ---------------------------------------------------------------------------

describe("дорога к записи открыта при показанном приглашении", () => {
  it("«Найти услугу» с пустого экрана по-прежнему ведёт в каталог", async () => {
    const user = userEvent.setup();
    mockLists([], []);
    mockedContext.mockResolvedValue(DOC_GOAL_MISSING);
    renderScreen();

    // Приглашение на экране — и не перехватывает поток записи.
    expect(await screen.findByRole("button", { name: GOAL_CTA })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Найти услугу" }));
    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
  });

  it("нижняя навигация «Услуги» работает при показанном приглашении", async () => {
    const user = userEvent.setup();
    mockLists(UPCOMING, []);
    mockedContext.mockResolvedValue(DOC_GOAL_MISSING);
    renderScreen();

    expect(await screen.findByRole("button", { name: GOAL_CTA })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Услуги" }));
    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
  });

  it("приглашение идёт ПОСЛЕ содержимого экрана, а не до него", async () => {
    // BOT-001 §13: First Contact не начинается со standalone-анкеты, а
    // Mini App entry — в области действия BOT-001 (§2.1). Порядок в DOM
    // здесь нормативный, поэтому и заперт тестом.
    mockLists([], []);
    mockedContext.mockResolvedValue(DOC_GOAL_MISSING);
    renderScreen();

    const cta = await screen.findByRole("button", { name: GOAL_CTA });
    const findService = screen.getByRole("button", { name: "Найти услугу" });
    expect(
      findService.compareDocumentPosition(cta) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("анкета — не тупик: из неё есть выход назад к записям", async () => {
    const user = userEvent.setup();
    const back = { handler: null as (() => void) | null };
    mockedOnBackButton.mockImplementation((h: () => void) => {
      back.handler = h;
      return () => {
        back.handler = null;
      };
    });
    mockLists([], []);
    mockedContext.mockResolvedValue(DOC_GOAL_MISSING);
    renderScreen();

    await user.click(await screen.findByRole("button", { name: GOAL_CTA }));

    // Мы на анкете.
    expect(
      await screen.findByRole("heading", { name: "Какая у тебя цель?" }),
    ).toBeInTheDocument();

    // Кнопка «назад» платформы показана и подписана.
    expect(mockedSetBackButton).toHaveBeenCalledWith(true);
    expect(back.handler).not.toBeNull();

    // И она реально возвращает к записям.
    await act(async () => {
      back.handler!();
    });
    expect(await screen.findByText(/Пока записей нет/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Сбой decision-context не имеет права ломать домашний экран.
// ---------------------------------------------------------------------------

describe("сбой decision-context", () => {
  it("приглашения нет, записи отрисованы", async () => {
    mockLists(UPCOMING, []);
    mockedContext.mockRejectedValue(new Error("ayla_unavailable"));
    renderScreen();

    expect(
      await screen.findByRole("tab", { name: "Ближайшие (2)" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: GOAL_CTA }),
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Каждый класс приглашения обязан иметь правило.
//
// DRF-1066: имя класса без правила молчит — React его отрисует, браузер
// проигнорирует, все тесты пройдут, и дефект увидит только человек на
// экране. Ровно это и случилось здесь: `goal-invite__cta` уехал в CI
// ненайденным, потому что проверки выше смотрели на присутствие узла и
// его порядок — и обе прошли бы при невидимой кнопке.
//
// vitest гоняется с `css: false`, поэтому стиль в jsdom не доедет:
// таблицу приходится читать текстом, как это делает
// `tools/lint/miniapp_style_contract.py`. Здесь проверка уже,
// чем линт, и потому строже: линт читает статические литералы
// `className="..."` в исходнике, а этот тест собирает классы с реально
// отрисованного поддерева — включая базовые (`btn-secondary`), которые
// тоже обязаны существовать.
// ---------------------------------------------------------------------------

function stylesheetText(): string {
  const stylesDir = join(dirname(fileURLToPath(import.meta.url)), "..", "styles");
  return readdirSync(stylesDir)
    .filter((f) => f.endsWith(".css"))
    .map((f) => readFileSync(join(stylesDir, f), "utf-8"))
    .join("\n");
}

describe("контракт стилей приглашения", () => {
  it("каждый класс отрисованного приглашения имеет правило в src/styles/", async () => {
    mockLists([], []);
    mockedContext.mockResolvedValue(DOC_GOAL_MISSING);
    renderScreen();

    const cta = await screen.findByRole("button", { name: GOAL_CTA });
    const invite = cta.closest("section");
    expect(invite).not.toBeNull();

    const classes = new Set<string>();
    for (const el of [invite!, ...invite!.querySelectorAll("*")]) {
      for (const c of el.classList) classes.add(c);
    }
    // Положительная стража: поддерево вообще несёт классы.
    expect(classes.size).toBeGreaterThan(0);
    expect(classes.has("goal-invite")).toBe(true);

    // Селекторы классов, объявленные в таблицах. Регулярка — литерал,
    // а не собранная строка: так в ней нечему сломаться при переносе.
    const declared = new Set(
      (stylesheetText().match(/\.[A-Za-z0-9_-]+/g) ?? []).map((m) => m.slice(1)),
    );
    const unstyled = [...classes].filter((c) => !declared.has(c));
    expect(unstyled).toEqual([]);
  });
});
