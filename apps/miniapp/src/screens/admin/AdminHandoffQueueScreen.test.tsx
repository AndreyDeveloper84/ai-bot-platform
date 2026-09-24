/**
 * «Диалоги» — очередь ждущих человека (DRF-2115), маршрут `/admin/handoff`.
 *
 * До DRF-2367 маршрут не был упомянут ни одним узлом. Закрепляется **путь
 * человека**, а не отрисовка: администратор открывает очередь, чтобы понять,
 * ждёт ли кто-то ответа и как давно, — и ошибиться здесь дороже всего,
 * потому что по ту сторону ждёт живой человек.
 *
 * Узлы держат:
 *
 * * очередь читается **построчно** — возраст, взятость и эскалация стоят у
 *   той строки, к которой относятся (проверка по документу целиком пропускала
 *   перепутанные строки: строка есть, а у кого — неизвестно);
 * * **пусто отличимо от ошибки**: «никто не ждёт» и «не смогли спросить» на
 *   экране выглядят одинаково безмятежно, а значат противоположное;
 * * из ошибки есть возврат — повтор перезапрашивает и показывает список;
 * * предел экрана назван человеку: брать и закрывать — не здесь;
 * * `formatAge` — по границам, а не по одному примеру.
 *
 * Чего узлы НЕ держат — по границам DRF-2367 (поведение не менять): вёрстку,
 * порядок и сортировку строк, состояние загрузки (экран наблюдается только в
 * конечных состояниях), возврат системной кнопкой MAX (замокан).
 *
 * Заголовок сверяется с `waitingLabel(WAITING.waiting)`, а не с готовой
 * строкой: подпись живёт в `SalonTodayCards` и принадлежит карточке
 * «Сегодня», а **сколько именно считать ждущими** — вопрос к владельцу ручки
 * `GET admin/handoff-queue/`, и узел его не решает.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/max-sdk")>();
  return {
    ...original,
    getInitData: () => "test-init-data",
    setBackButton: vi.fn(),
    onBackButton: vi.fn(() => () => undefined),
  };
});

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return { ...original, getHandoffQueue: vi.fn() };
});

import { getHandoffQueue, type HandoffQueueResponse } from "../../lib/admin-api";
import { ApiError } from "../../lib/api";
import { AdminHandoffQueueScreen, formatAge, HANDOFF_COPY } from "./AdminHandoffQueueScreen";
import { waitingLabel } from "./SalonTodayCards";

const mockedQueue = vi.mocked(getHandoffQueue);

const WAITING: HandoffQueueResponse = {
  waiting: 2,
  rows: [
    {
      task_id: "t-1",
      status: "open",
      age_minutes: 84,
      created_at: "2026-09-23T14:00:00Z",
      claimed: false,
      addressee: "",
      escalated: true,
    },
    {
      task_id: "t-2",
      status: "in_progress",
      age_minutes: 3,
      created_at: "2026-09-23T15:20:00Z",
      claimed: true,
      addressee: "Карина",
      escalated: false,
    },
  ],
};

const EMPTY: HandoffQueueResponse = { waiting: 0, rows: [] };

function open() {
  return render(
    <MemoryRouter initialEntries={["/admin/handoff"]}>
      <AdminHandoffQueueScreen />
    </MemoryRouter>,
  );
}

/** Единственная кнопка внутри тревоги — повтор; подпись живёт в `StateError`. */
function retryButton() {
  return within(screen.getByRole("alert")).getByRole("button");
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("очередь приходит и читается", () => {
  it("возраст, взятость и эскалация стоят у своих строк", async () => {
    mockedQueue.mockResolvedValue(WAITING);

    open();

    expect(await screen.findByText(`${waitingLabel(WAITING.waiting)}.`)).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(WAITING.rows.length);
    const [first, second] = rows as [HTMLElement, HTMLElement];

    expect(within(first).getByText("1 ч 24 мин")).toBeInTheDocument();
    expect(within(first).getByText(HANDOFF_COPY.unclaimed)).toBeInTheDocument();
    expect(within(first).getByText(HANDOFF_COPY.escalated)).toBeInTheDocument();

    expect(within(second).getByText("3 мин")).toBeInTheDocument();
    expect(within(second).getByText(HANDOFF_COPY.claimed("Карина"))).toBeInTheDocument();
    // Эскалация — признак строки, а не экрана: без этой проверки перепутанные
    // строки проходили, потому что метка есть где-то на странице.
    expect(within(second).queryByText(HANDOFF_COPY.escalated)).not.toBeInTheDocument();
  });

  it("называет предел экрана: брать и закрывать — не здесь", async () => {
    mockedQueue.mockResolvedValue(WAITING);

    open();

    expect(await screen.findByText(HANDOFF_COPY.readOnly)).toBeInTheDocument();
  });
});

describe("возраст читается человеком", () => {
  it.each([
    [0, "только что"],
    [3, "3 мин"],
    [60, "1 ч"],
    [84, "1 ч 24 мин"],
  ])("%i мин → «%s»", (minutes, expected) => {
    expect(formatAge(minutes)).toBe(expected);
  });
});

describe("пусто отличимо от ошибки", () => {
  it("пустая очередь говорит «никто не ждёт» и не поднимает тревоги", async () => {
    mockedQueue.mockResolvedValue(EMPTY);

    open();

    expect(await screen.findByText(HANDOFF_COPY.empty)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("отказ источника не выдаёт себя за пустую очередь", async () => {
    mockedQueue.mockRejectedValue(new ApiError(503, "unavailable", "очередь недоступна"));

    open();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // Ровно та подмена, ради которой узел и написан: «никто не ждёт» при
    // недоступном источнике означало бы «можно не смотреть».
    expect(screen.queryByText(HANDOFF_COPY.empty)).not.toBeInTheDocument();
  });
});

describe("из ошибки есть возврат", () => {
  it("повтор перезапрашивает очередь и показывает её", async () => {
    mockedQueue
      .mockRejectedValueOnce(new ApiError(503, "unavailable", "очередь недоступна"))
      .mockResolvedValueOnce(WAITING);

    open();
    await screen.findByRole("alert");
    await userEvent.click(retryButton());

    expect(await screen.findByText(`${waitingLabel(WAITING.waiting)}.`)).toBeInTheDocument();
    await waitFor(() => expect(mockedQueue).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
