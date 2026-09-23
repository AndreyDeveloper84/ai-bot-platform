/**
 * «Диалоги» — очередь ждущих человека (DRF-2115), маршрут `/admin/handoff`.
 *
 * До DRF-2367 маршрут не был упомянут ни одним узлом. Закрепляется **путь
 * человека**, а не отрисовка: администратор открывает очередь, чтобы понять,
 * ждёт ли кто-то ответа и как давно, — и ошибиться здесь дороже всего,
 * потому что по ту сторону ждёт живой человек.
 *
 * Узлы держат ровно четыре вещи:
 *
 * * очередь приходит и читается — сколько ждут, как давно, взял ли кто-то;
 * * **пусто отличимо от ошибки**: «никто не ждёт» и «не смогли спросить» на
 *   экране выглядят одинаково безмятежно, а значат противоположное;
 * * из ошибки есть возврат — повтор перезапрашивает и показывает список;
 * * предел экрана назван человеку: брать и закрывать — не здесь.
 *
 * Чего узлы НЕ держат — по границам DRF-2367 (поведение не менять):
 * вёрстку, порядок строк, сортировку и подписи возраста сверх одного
 * примера; `formatAge` и `waitingLabel` — числовая арифметика, у неё своё
 * место.
 */
import { render, screen, waitFor } from "@testing-library/react";
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
import { AdminHandoffQueueScreen, HANDOFF_COPY } from "./AdminHandoffQueueScreen";

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

beforeEach(() => {
  vi.clearAllMocks();
});

describe("очередь приходит и читается", () => {
  it("показывает, сколько ждут, как давно и взял ли кто-то", async () => {
    mockedQueue.mockResolvedValue(WAITING);

    open();

    expect(await screen.findByText("2 ждут ответа.")).toBeInTheDocument();
    expect(screen.getByText("1 ч 24 мин")).toBeInTheDocument();
    expect(screen.getByText(HANDOFF_COPY.unclaimed)).toBeInTheDocument();
    expect(screen.getByText(HANDOFF_COPY.claimed("Карина"))).toBeInTheDocument();
    expect(screen.getByText(HANDOFF_COPY.escalated)).toBeInTheDocument();
  });

  it("называет предел экрана: брать и закрывать — не здесь", async () => {
    mockedQueue.mockResolvedValue(WAITING);

    open();

    expect(await screen.findByText(HANDOFF_COPY.readOnly)).toBeInTheDocument();
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
    await userEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));

    expect(await screen.findByText("2 ждут ответа.")).toBeInTheDocument();
    await waitFor(() => expect(mockedQueue).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
