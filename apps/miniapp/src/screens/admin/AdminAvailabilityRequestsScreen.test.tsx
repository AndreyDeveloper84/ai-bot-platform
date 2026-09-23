/**
 * «Запросы на смену графика» — маршрут `/admin/availability-requests`.
 *
 * До DRF-2367 маршрут не был упомянут ни одним узлом. С обеих сторон здесь
 * живые люди: мастер попросил нерабочие дни и ждёт ответа, владелец решает.
 * Поэтому закрепляется **путь человека** — список приходит, решение доезжает
 * до сервера, отказ сервера не выглядит успехом, — а не отрисовка карточек.
 *
 * Узлы держат:
 *
 * * список приходит и читается (имя, причина);
 * * **пусто отличимо от ошибки** — «все рассмотрены» и «не смогли спросить»
 *   выглядят одинаково спокойно, а значат противоположное;
 * * «Одобрить» **доезжает до сервера** тем самым `request_id`, и строка
 *   уходит из «Ожидают»;
 * * отказ сервера остаётся отказом: строка на месте, человеку сказано;
 * * ресепшен списка не получает — запрос вообще не уходит.
 *
 * Чего узлы НЕ держат (границы DRF-2367 — поведение не менять):
 *
 * * **числа на фильтрах** (`Ожидают (N)` / `Решены (N)`): их предмет прямо
 *   сейчас меняется в DRF-2366 — «не знаю» против нуля, — и узел на
 *   сегодняшнее число закрепил бы то, что решено переделать;
 * * ветки 409 (`already_decided`, `overlap_conflict`), отклонение с
 *   причиной, «показать ещё», разворачивание длинной причины, формат дат.
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
    setClosingConfirmation: vi.fn(),
    hapticImpact: vi.fn(),
    hapticNotify: vi.fn(),
    hapticSelection: vi.fn(),
  };
});

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    getAvailabilityRequests: vi.fn(),
    approveAvailabilityRequest: vi.fn(),
    rejectAvailabilityRequest: vi.fn(),
  };
});

import {
  approveAvailabilityRequest,
  getAvailabilityRequests,
  type AvailabilityRequestItem,
  type MeResponse,
} from "../../lib/admin-api";
import { ApiError } from "../../lib/api";
import { AdminAvailabilityRequestsScreen } from "./AdminAvailabilityRequestsScreen";

const mockedList = vi.mocked(getAvailabilityRequests);
const mockedApprove = vi.mocked(approveAvailabilityRequest);

const OWNER_ME: MeResponse = {
  user: { id: "u-1", name: "Карина", phone_masked: "+• ••• ••• ••12" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula-tela" },
  role: "owner",
  capabilities: [],
  is_customer: true,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: true,
  master_id: null,
  landing_path: "/admin/team",
};

const DESK_ME: MeResponse = {
  ...OWNER_ME,
  role: "receptionist",
  is_owner: false,
  is_receptionist: true,
};

const OLGA: AvailabilityRequestItem = {
  request_id: "r-1",
  master_id: "m-1",
  master_name: "Ольга",
  requested_start: "2026-10-05T00:00:00Z",
  requested_end: "2026-10-07T00:00:00Z",
  reason_class: "vacation",
  reason_text: "",
  status: "pending",
  created_at: "2026-09-23T10:00:00Z",
  decided_at: null,
  decided_by_name: "",
  resolution_note: "",
};

/** Список и счётчики читаются одной и той же ручкой — отвечаем на все заходы. */
function listReturns(items: AvailabilityRequestItem[]) {
  mockedList.mockResolvedValue({ items, next_cursor: null });
}

function open(me: MeResponse = OWNER_ME) {
  return render(
    <MemoryRouter initialEntries={["/admin/availability-requests"]}>
      <AdminAvailabilityRequestsScreen me={me} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("список приходит", () => {
  it("владелец видит, кто и почему просит", async () => {
    listReturns([OLGA]);

    open();

    expect(await screen.findByText("Ольга")).toBeInTheDocument();
    expect(screen.getByText("Отпуск")).toBeInTheDocument();
  });
});

describe("пусто отличимо от ошибки", () => {
  it("пустой список говорит, что рассматривать нечего", async () => {
    listReturns([]);

    open();

    expect(await screen.findByText("Все запросы рассмотрены 👍")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("отказ источника не выдаёт себя за пустой список", async () => {
    mockedList.mockRejectedValue(new ApiError(503, "unavailable", "источник недоступен"));

    open();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // Подмена, ради которой узел написан: «все рассмотрены» при недоступном
    // источнике означало бы, что мастера можно не ждать.
    expect(screen.queryByText("Все запросы рассмотрены 👍")).not.toBeInTheDocument();
  });
});

describe("решение доезжает до сервера", () => {
  it("«Одобрить» уходит тем самым request_id, и строка покидает «Ожидают»", async () => {
    listReturns([OLGA]);
    mockedApprove.mockResolvedValue({ ...OLGA, status: "approved" });

    open();
    await screen.findByText("Ольга");
    await userEvent.click(screen.getByRole("button", { name: "✓ Одобрить" }));

    const sheet = await screen.findByRole("dialog", { name: "Подтвердить одобрение" });
    await userEvent.click(within(sheet).getByRole("button", { name: "Одобрить" }));

    await waitFor(() => expect(mockedApprove).toHaveBeenCalledWith("r-1"));
    await waitFor(() => expect(screen.queryByText("Ольга")).not.toBeInTheDocument());
  });

  it("до подтверждения на сервер не уходит ничего", async () => {
    listReturns([OLGA]);

    open();
    await screen.findByText("Ольга");
    await userEvent.click(screen.getByRole("button", { name: "✓ Одобрить" }));
    await screen.findByRole("dialog", { name: "Подтвердить одобрение" });

    // Положительная пара к узлу выше: кнопка открывает вопрос, а не решает.
    expect(mockedApprove).not.toHaveBeenCalled();
  });

  it("отказ сервера остаётся отказом: строка на месте, человеку сказано", async () => {
    listReturns([OLGA]);
    mockedApprove.mockRejectedValue(new ApiError(500, "server_error", "Не получилось одобрить"));

    open();
    await screen.findByText("Ольга");
    await userEvent.click(screen.getByRole("button", { name: "✓ Одобрить" }));
    const sheet = await screen.findByRole("dialog", { name: "Подтвердить одобрение" });
    await userEvent.click(within(sheet).getByRole("button", { name: "Одобрить" }));

    expect(await screen.findByText("Не получилось одобрить")).toBeInTheDocument();
    expect(screen.getByText("Ольга")).toBeInTheDocument();
  });
});

describe("ресепшен сюда не ходит", () => {
  it("видит отказ и не отправляет запрос вовсе", async () => {
    listReturns([OLGA]);

    open(DESK_ME);

    expect(
      await screen.findByText("Эта страница только для администраторов."),
    ).toBeInTheDocument();
    expect(mockedList).not.toHaveBeenCalled();
  });
});
