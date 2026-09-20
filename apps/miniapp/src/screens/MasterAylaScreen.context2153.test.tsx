/**
 * Ayla для мастера — четыре состояния макета DRF-1187 (DRF-2153, М-5).
 *
 * 1. Стартовый экран: контекст дня («Сегодня 2 записи · Следующая — Анна П.
 *    в 10:30 · Классический массаж · 60 мин» / «Сегодня записей нет») и
 *    четыре чипа; чип = отправка фразы.
 * 2. Ответ о свободном времени — карточкой «Завтра свободно:» с окнами и
 *    «Создать запись» → М-3 с выбранным окном; «последние известные
 *    данные» + «Проверить снова», когда бэкенд не подтвердил расписание.
 * 3. Подготовленное действие — карточка клиент/услуга/длительность/дата/
 *    время + «Подтвердить»/«Отмена»; после — «Запись создана» + «Открыть
 *    запись» (обычный экран деталей, не карточка внутри Ayla).
 * 4. Уточнение: «Кого вы имеете в виду?» с вариантами без телефона;
 *    «Это время занято» + варианты — выбор варианта = новая фраза, без тихого
 *    переноса.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getAylaHistory: vi.fn(),
    askAyla: vi.fn(),
    confirmAylaAction: vi.fn(),
    getAylaContext: vi.fn(),
    getMasterMe: vi.fn(),
  };
});

import {
  askAyla,
  confirmAylaAction,
  getAylaContext,
  getAylaHistory,
  getMasterMe,
  type AylaAskResponse,
} from "../lib/master-api";
import { MasterAylaScreen } from "./MasterAylaScreen";

const mockedHistory = vi.mocked(getAylaHistory);
const mockedAsk = vi.mocked(askAyla);
const mockedConfirm = vi.mocked(confirmAylaAction);
const mockedContext = vi.mocked(getAylaContext);
const mockedMe = vi.mocked(getMasterMe);

const CHIPS = [
  "Что у меня сегодня?",
  "Когда я свободен завтра?",
  "Добавить запись",
  "Изменить рабочий день",
];
const CHIP_HINTS: Record<string, string> = {
  "Что у меня сегодня?": "Покажите расписание дня",
  "Когда я свободен завтра?": "Свободные окна на завтра",
  "Добавить запись": "Создать новую запись в расписании",
  "Изменить рабочий день": "Изменить график или недоступность",
};

function renderAt(path = "/master/ayla") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/master/ayla" element={<MasterAylaScreen />} />
        <Route
          path="/master/booking/new"
          element={<p>Экран «Новая запись»</p>}
        />
        <Route
          path="/master/bookings/:id"
          element={<p>Экран «Детали записи»</p>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function reply(over: Partial<AylaAskResponse> = {}): AylaAskResponse {
  return {
    answer: "ответ",
    tool: "",
    pending_action: null,
    message_id: "m-1",
    cards: [],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedHistory.mockResolvedValue({ messages: [] });
  mockedMe.mockRejectedValue(new Error("not needed"));
  mockedContext.mockResolvedValue({
    today: {
      date: "2026-09-20",
      count: 2,
      next: {
        client_name_initial: "Анна П.",
        time: "10:30",
        service_name: "Классический массаж",
        duration_min: 60,
      },
    },
    chips: CHIPS,
    chip_hints: CHIP_HINTS,
  });
  mockedAsk.mockResolvedValue(reply());
});

describe("1 · стартовый экран", () => {
  it("карточка дня в три строки макета, «Что можно сделать» и четыре чипа с подписями", async () => {
    renderAt();
    expect(await screen.findByText("Сегодня 2 записи")).toBeInTheDocument();
    expect(screen.getByText("Следующая — Анна П. в 10:30")).toBeInTheDocument();
    expect(
      screen.getByText("Классический массаж · 60 мин"),
    ).toBeInTheDocument();
    expect(screen.getByText("Что можно сделать")).toBeInTheDocument();
    for (const chip of CHIPS) {
      const button = screen.getByRole("button", {
        name: new RegExp(`^${chip}`),
      });
      expect(button).toHaveTextContent(CHIP_HINTS[chip]!);
    }
    expect(screen.getByPlaceholderText("Спросите Ayla…")).toBeInTheDocument();
    // Не длинный текст от помощника: прежнее приглашение-абзац не рисуется.
    expect(screen.queryByText(/Спросите про день, загрузку/)).toBeNull();
  });

  it("без записей — «Сегодня записей нет»", async () => {
    mockedContext.mockResolvedValue({
      today: { date: "2026-09-20", count: 0, next: null },
      chips: CHIPS,
      chip_hints: CHIP_HINTS,
    });
    renderAt();
    expect(await screen.findByText("Сегодня записей нет")).toBeInTheDocument();
  });

  it("чип отправляет фразу как вопрос", async () => {
    renderAt();
    (
      await screen.findByRole("button", { name: /^Когда я свободен завтра\?/ })
    ).click();
    await waitFor(() =>
      expect(mockedAsk).toHaveBeenCalledWith(
        "Когда я свободен завтра?",
        undefined,
      ),
    );
    expect(
      await screen.findByText("Когда я свободен завтра?", {
        selector: ".ayla-bubble__content",
      }),
    ).toBeInTheDocument();
  });
});

describe("2 · ответ о свободном времени", () => {
  it("окна карточкой и «Создать запись» → М-3 с выбранным окном", async () => {
    mockedAsk.mockResolvedValue(
      reply({
        answer: "Завтра свободно: 10:30–12:00 · 15:00–17:30",
        cards: [
          {
            kind: "free_windows",
            date: "2026-09-21",
            stale: false,
            notice: null,
            recheck: null,
            book_url: "/master/booking/new?date=2026-09-21",
            windows: [
              {
                start: "10:30",
                end: "12:00",
                book_url:
                  "/master/booking/new?date=2026-09-21&from=10%3A30&to=12%3A00",
              },
              {
                start: "15:00",
                end: "17:30",
                book_url:
                  "/master/booking/new?date=2026-09-21&from=15%3A00&to=17%3A30",
              },
            ],
          },
        ],
      }),
    );
    renderAt();
    (
      await screen.findByRole("button", { name: /^Когда я свободен завтра\?/ })
    ).click();
    // Макет: «Завтра, 21 августа» / «Свободно:» / строки окон с длительностью
    // (ruling (ж): «90 мин», не «1 ч 30 мин») / «Создать запись» / ⓘ подсказка.
    expect(await screen.findByText("Свободно:")).toBeInTheDocument();
    expect(screen.getByText(/21 сентября/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /10:30–12:00.*90 мин/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /15:00–17:30.*150 мин/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Нажмите на интервал, чтобы создать запись с этим окном. Или нажмите «Создать запись», чтобы выбрать время позже.",
      ),
    ).toBeInTheDocument();
    screen.getByRole("link", { name: "Создать запись" }).click();
    expect(await screen.findByText("Экран «Новая запись»")).toBeInTheDocument();
  });

  it("данные не подтверждены — «последние известные данные» и «Проверить снова» повторяет вопрос", async () => {
    mockedAsk.mockResolvedValue(
      reply({
        answer: "Завтра свободно: 10:00–18:00",
        cards: [
          {
            kind: "free_windows",
            date: "2026-09-21",
            stale: true,
            notice:
              "Не удалось проверить актуальное расписание. Последние известные данные",
            footer: "Расписание могло измениться.",
            recheck: "Проверить снова",
            windows: [
              {
                start: "10:00",
                end: "18:00",
                book_url:
                  "/master/booking/new?date=2026-09-21&from=10%3A00&to=18%3A00",
              },
            ],
          },
        ],
      }),
    );
    renderAt();
    (
      await screen.findByRole("button", { name: /^Когда я свободен завтра\?/ })
    ).click();
    // Тексты макета дословно: заголовок / «Последние известные данные:» /
    // строки / «Расписание могло измениться.» / «Проверить снова».
    expect(
      await screen.findByText("Не удалось проверить актуальное расписание."),
    ).toBeInTheDocument();
    expect(screen.getByText("Последние известные данные:")).toBeInTheDocument();
    expect(
      screen.getByText("Расписание могло измениться."),
    ).toBeInTheDocument();
    screen.getByRole("button", { name: "Проверить снова" }).click();
    await waitFor(() => expect(mockedAsk).toHaveBeenCalledTimes(2));
    expect(mockedAsk.mock.calls[1]?.[0]).toBe("Когда я свободен завтра?");
  });
});

describe("3 · подготовленное действие", () => {
  it("карточка с пятью полями, «Подтвердить» / «Отмена»; после — «Запись создана» + «Открыть запись»", async () => {
    mockedAsk.mockResolvedValue(
      reply({
        answer: "Записать Анну П. на Классический массаж 21 сентября в 12:30?",
        pending_action: {
          action: "prepare_booking",
          summary:
            "Анна П. · Классический массаж · 60 мин · 21 сентября · 12:30",
          confirm_label: "Подтвердить",
          token: "t-1",
          expires_in_sec: 900,
          details: {
            client: "Анна П.",
            service: "Классический массаж",
            duration_min: 60,
            date: "21 сентября, четверг",
            time: "12:30",
            time_range: "12:30–13:30",
          },
        },
      }),
    );
    mockedConfirm.mockResolvedValue({
      answer: "Запись создана",
      action: "prepare_booking",
      executed: true,
      message_id: "m-2",
      open: { url: "/master/bookings/a-1", label: "Открыть запись" },
      cards: [],
      details: {
        client: "Анна П.",
        service: "Классический массаж",
        duration_min: 60,
        date: "21 сентября, четверг",
        time: "12:30",
        time_range: "12:30–13:30",
      },
    });
    renderAt();
    (await screen.findByRole("button", { name: /^Добавить запись/ })).click();
    // Макет 3A: «Проверьте запись» / Анна П. / Классический массаж · 60 мин /
    // 21 сентября, четверг / 12:30–13:30 / Подтвердить · Отмена / ⓘ.
    const card = await screen.findByRole("region", {
      name: "Проверьте запись",
    });
    expect(card).toHaveTextContent("Анна П.");
    expect(card).toHaveTextContent("Классический массаж · 60 мин");
    expect(card).toHaveTextContent("21 сентября, четверг");
    expect(card).toHaveTextContent("12:30–13:30");
    expect(
      screen.getByText(
        "После подтверждения запись будет создана в расписании.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отмена" })).toBeInTheDocument();
    expect(mockedConfirm).not.toHaveBeenCalled();
    screen.getByRole("button", { name: "Подтвердить" }).click();
    // Макет 3B: ✓ «Запись создана» / три строки / «Открыть запись» / ⓘ.
    expect(await screen.findByText("Запись создана")).toBeInTheDocument();
    expect(
      screen.getByText("Сервер подтвердил результат."),
    ).toBeInTheDocument();
    expect(screen.getByText("21 сентября · 12:30–13:30")).toBeInTheDocument();
    expect(mockedConfirm).toHaveBeenCalledWith("t-1");
    screen.getByRole("link", { name: "Открыть запись" }).click();
    expect(
      await screen.findByText("Экран «Детали записи»"),
    ).toBeInTheDocument();
  });
});

describe("4 · уточнение и конфликт", () => {
  it("«Кого вы имеете в виду?» — варианты без телефона; выбор уходит с client_id", async () => {
    mockedAsk.mockResolvedValueOnce(
      reply({
        answer: "Нашла двух клиентов с таким именем. Кого вы имеете в виду?",
        cards: [
          {
            kind: "clarify_client",
            options: [
              { client_id: "c-1", label: "Анна П. · была 12.05" },
              { client_id: "c-2", label: "Анна С. · новый клиент" },
            ],
          },
        ],
      }),
    );
    renderAt();
    (await screen.findByRole("button", { name: /^Добавить запись/ })).click();
    expect(
      await screen.findByText(
        /Нашла двух клиентов с таким именем\. Кого вы имеете в виду\?/,
      ),
    ).toBeInTheDocument();
    screen.getByRole("button", { name: "Анна П. · была 12.05" }).click();
    await waitFor(() => expect(mockedAsk).toHaveBeenCalledTimes(2));
    expect(mockedAsk.mock.calls[1]).toEqual([
      "Анна П. · была 12.05",
      { client_id: "c-1" },
    ]);
    expect(document.body.textContent).not.toMatch(/\+7|\d{3}-\d{2}-\d{2}/);
  });

  it("«Это время занято» + варианты; выбор варианта — новая фраза с start_at, без тихого переноса", async () => {
    mockedAsk.mockResolvedValue(
      reply({
        pending_action: {
          action: "prepare_booking",
          summary: "…",
          confirm_label: "Подтвердить",
          token: "t-1",
          expires_in_sec: 900,
          details: {
            client: "Анна П.",
            service: "Массаж",
            duration_min: 60,
            date: "21 сентября, четверг",
            time: "12:30",
            time_range: "12:30–13:30",
          },
        },
      }),
    );
    mockedConfirm.mockResolvedValue({
      answer: "Это время занято",
      action: "prepare_booking",
      executed: false,
      message_id: "m-3",
      cards: [
        {
          kind: "slot_taken",
          range: "12:30–13:30",
          book_url: "/master/booking/new?date=2026-09-21",
          alternatives: [
            { time: "14:00", start_at: "2026-09-21T14:00:00+03:00" },
            { time: "15:00", start_at: "2026-09-21T15:00:00+03:00" },
          ],
        },
      ],
    });
    renderAt();
    (await screen.findByRole("button", { name: /^Добавить запись/ })).click();
    (await screen.findByRole("button", { name: "Подтвердить" })).click();
    // Макет 4: ⚠ «Это время занято» / «12:30–13:30 больше недоступно.» /
    // «Свободно рядом:» / варианты / «Выбрать другое время».
    expect(await screen.findByText("Это время занято")).toBeInTheDocument();
    expect(
      screen.getByText("12:30–13:30 больше недоступно."),
    ).toBeInTheDocument();
    expect(screen.getByText("Свободно рядом:")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Выбрать другое время" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Запись создана")).toBeNull();
    screen.getByRole("button", { name: /15:00/ }).click();
    await waitFor(() => expect(mockedAsk).toHaveBeenCalledTimes(2));
    expect(mockedAsk.mock.calls[1]).toEqual([
      "Запиши на 15:00",
      { start_at: "2026-09-21T15:00:00+03:00" },
    ]);
    expect(mockedConfirm).toHaveBeenCalledTimes(1);
  });
});
