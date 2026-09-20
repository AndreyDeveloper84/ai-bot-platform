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
  });
  mockedAsk.mockResolvedValue(reply());
});

describe("1 · стартовый экран", () => {
  it("контекст дня одной строкой и четыре чипа", async () => {
    renderAt();
    expect(
      await screen.findByText(
        "Сегодня 2 записи · Следующая — Анна П. в 10:30 · Классический массаж · 60 мин",
      ),
    ).toBeInTheDocument();
    for (const chip of CHIPS) {
      expect(screen.getByRole("button", { name: chip })).toBeInTheDocument();
    }
    // Не длинный текст от помощника: прежнее приглашение-абзац не рисуется.
    expect(screen.queryByText(/Спросите про день, загрузку/)).toBeNull();
  });

  it("без записей — «Сегодня записей нет»", async () => {
    mockedContext.mockResolvedValue({
      today: { date: "2026-09-20", count: 0, next: null },
      chips: CHIPS,
    });
    renderAt();
    expect(await screen.findByText("Сегодня записей нет")).toBeInTheDocument();
  });

  it("чип отправляет фразу как вопрос", async () => {
    renderAt();
    (
      await screen.findByRole("button", { name: "Когда я свободен завтра?" })
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
      await screen.findByRole("button", { name: "Когда я свободен завтра?" })
    ).click();
    expect(await screen.findByText("Завтра свободно:")).toBeInTheDocument();
    expect(screen.getByText("10:30–12:00")).toBeInTheDocument();
    expect(screen.getByText("15:00–17:30")).toBeInTheDocument();
    screen.getAllByRole("link", { name: "Создать запись" })[1]!.click();
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
      await screen.findByRole("button", { name: "Когда я свободен завтра?" })
    ).click();
    expect(
      await screen.findByText(
        "Не удалось проверить актуальное расписание. Последние известные данные",
      ),
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
            date: "21 сентября",
            time: "12:30",
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
    });
    renderAt();
    (await screen.findByRole("button", { name: "Добавить запись" })).click();
    const card = await screen.findByRole("region", { name: /Подтвердите/ });
    for (const [label, value] of [
      ["Клиент", "Анна П."],
      ["Услуга", "Классический массаж"],
      ["Длительность", "60 мин"],
      ["Дата", "21 сентября"],
      ["Время", "12:30"],
    ]) {
      expect(card).toHaveTextContent(`${label}${value}`);
    }
    expect(screen.getByRole("button", { name: "Отмена" })).toBeInTheDocument();
    expect(mockedConfirm).not.toHaveBeenCalled();
    screen.getByRole("button", { name: "Подтвердить" }).click();
    expect(await screen.findByText("Запись создана")).toBeInTheDocument();
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
        answer: "Кого вы имеете в виду?",
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
    (await screen.findByRole("button", { name: "Добавить запись" })).click();
    expect(
      await screen.findByText("Кого вы имеете в виду?"),
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
            date: "21 сентября",
            time: "12:30",
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
          alternatives: [
            { time: "14:00", start_at: "2026-09-21T14:00:00+03:00" },
            { time: "15:00", start_at: "2026-09-21T15:00:00+03:00" },
          ],
        },
      ],
    });
    renderAt();
    (await screen.findByRole("button", { name: "Добавить запись" })).click();
    (await screen.findByRole("button", { name: "Подтвердить" })).click();
    expect(await screen.findByText("Это время занято")).toBeInTheDocument();
    expect(screen.queryByText("Запись создана")).toBeNull();
    screen.getByRole("button", { name: "15:00" }).click();
    await waitFor(() => expect(mockedAsk).toHaveBeenCalledTimes(2));
    expect(mockedAsk.mock.calls[1]).toEqual([
      "Запиши на 15:00",
      { start_at: "2026-09-21T15:00:00+03:00" },
    ]);
    expect(mockedConfirm).toHaveBeenCalledTimes(1);
  });
});
