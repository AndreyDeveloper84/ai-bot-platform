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
 * * список приходит и читается (имя, причина), и экран спрашивает **тот**
 *   статус, который выбран, — иначе «Ожидают» молча показывало бы всё;
 * * **пусто отличимо от ошибки** — «все рассмотрены» и «не смогли спросить»
 *   выглядят одинаково спокойно, а значат противоположное;
 * * «Одобрить» доезжает до сервера с id **нажатой** строки (в списке их две,
 *   иначе «тем самым id» доказывалось бы совпадением с единственным);
 * * до подтверждения на сервер не уходит ничего;
 * * отказ сервера остаётся отказом, и человеку сказано **то, что ответил
 *   сервер**, а не заготовка экрана;
 * * ресепшен списка не получает — запрос вообще не уходит.
 *
 * Список отвечает изменяемым состоянием, а не одной готовой страницей:
 * сегодня экран убирает решённую строку без перезапроса, но перезапрос был бы
 * не хуже, и узел не должен краснеть на **улучшение**.
 *
 * Чего узлы НЕ держат (границы DRF-2367 — поведение не менять):
 *
 * * **числа на фильтрах** (`Ожидают (N)` / `Решены (N)`): их предмет прямо
 *   сейчас меняется в DRF-2366 — «не знаю» против нуля, — и узел на
 *   сегодняшнее число закрепил бы то, что решено переделать;
 * * ветки 409 (`already_decided`, `overlap_conflict`), отклонение с причиной,
 *   «показать ещё», разворачивание длинной причины, формат дат, состояние
 *   загрузки (экран наблюдается только в конечных состояниях).
 *
 * Подписи здесь сверяются со строками, а не с общей таблицей: у этого экрана,
 * в отличие от очереди передач, нет экспортированного `COPY` — заводить его
 * значило бы менять экран, а это вне границ листа.
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

const MARINA: AvailabilityRequestItem = {
  ...OLGA,
  request_id: "r-2",
  master_id: "m-2",
  master_name: "Марина",
  reason_class: "sick",
};

/**
 * Список и счётчики читаются одной ручкой, поэтому она отвечает состоянием:
 * решённая заявка исчезает из «Ожидают» и у источника, а не только на экране.
 * Так узел не краснеет, если экран однажды станет перечитывать список.
 */
function salonHas(...pending: AvailabilityRequestItem[]) {
  let rows = [...pending];
  mockedList.mockImplementation(async (params) => ({
    items: params?.status === "decided" ? [] : rows,
    next_cursor: null,
  }));
  mockedApprove.mockImplementation(async (id) => {
    const decided = rows.find((r) => r.request_id === id);
    // Падать, а не подставлять первую строку: подстановка сделала бы узел
    // «уходит id нажатой строки» неотличимым от «уходит хоть что-нибудь».
    if (!decided) throw new Error(`решают заявку ${id}, которой в салоне нет`);
    rows = rows.filter((r) => r.request_id !== id);
    return { ...decided, status: "approved" };
  });
}

/** Карточка мастера целиком — чтобы кнопка бралась из нужной строки. */
function rowOf(name: string): HTMLElement {
  return screen.getByText(name).closest("li") as HTMLElement;
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
    salonHas(OLGA);

    open();

    expect(await screen.findByText("Ольга")).toBeInTheDocument();
    expect(screen.getByText("Отпуск")).toBeInTheDocument();
  });

  it("экран спрашивает тот статус, который выбран", async () => {
    salonHas(OLGA);

    open();
    await screen.findByText("Ольга");

    // Без этого «Ожидают» могло бы молча запрашивать «все» — и решённые
    // заявки выглядели бы ждущими ответа.
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ status: "pending" }),
      expect.anything(),
    );
    // Одного утверждения выше мало: счётчики зовут ту же ручку с тем же
    // «pending», и проверка проходила бы, даже если список спрашивает «все».
    // Поэтому — ни одного захода за «всеми», пока открыта вкладка «Ожидают».
    expect(mockedList).not.toHaveBeenCalledWith(
      expect.objectContaining({ status: "all" }),
      expect.anything(),
    );
  });
});

describe("пусто отличимо от ошибки", () => {
  it("пустой список говорит, что рассматривать нечего", async () => {
    salonHas();

    open();

    // Подпись принадлежит вкладке «Ожидают» — она открыта по умолчанию.
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
  it("«Одобрить» уходит с id нажатой строки, и она покидает «Ожидают»", async () => {
    salonHas(OLGA, MARINA);

    open();
    await screen.findByText("Марина");
    await userEvent.click(within(rowOf("Марина")).getByRole("button", { name: /Одобрить/ }));

    const sheet = await screen.findByRole("dialog", { name: "Подтвердить одобрение" });
    await userEvent.click(within(sheet).getByRole("button", { name: "Одобрить" }));

    await waitFor(() => expect(mockedApprove).toHaveBeenCalledWith("r-2"));
    await waitFor(() => expect(screen.queryByText("Марина")).not.toBeInTheDocument());
    expect(screen.getByText("Ольга")).toBeInTheDocument();
  });

  it("до подтверждения на сервер не уходит ничего", async () => {
    salonHas(OLGA);

    open();
    await screen.findByText("Ольга");
    await userEvent.click(within(rowOf("Ольга")).getByRole("button", { name: /Одобрить/ }));
    await screen.findByRole("dialog", { name: "Подтвердить одобрение" });

    // Положительная пара к узлу выше: кнопка открывает вопрос, а не решает.
    expect(mockedApprove).not.toHaveBeenCalled();
  });

  it("отказ сервера остаётся отказом: строка на месте, внутреннего текста нет", async () => {
    salonHas(OLGA);
    // DRF-2451. Раньше здесь стояла выдуманная русская причина («Мастер уже
    // в отпуске в эти дни»), и узел требовал напечатать её. Сервер таких слов
    // не производит: отказы решения приходят по-английски —
    // `admin_api/services/availability.py:206` говорит «There are active
    // bookings in this period…». То есть узел охранял строку, которой нет, и
    // разрешал ту, которую человек не должен видеть.
    //
    // Забота автора узла верна и сохранена: «заготовка прошла бы проверку и
    // при полностью проглоченном ответе». Поэтому отказ отличается от
    // молчания двумя фактами сразу — плашка появилась И заявка осталась
    // неразобранной (при успехе она уходит из списка).
    mockedApprove.mockRejectedValue(
      new ApiError(409, "conflict", "There are active bookings in this period."),
    );

    open();
    await screen.findByText("Ольга");
    await userEvent.click(within(rowOf("Ольга")).getByRole("button", { name: /Одобрить/ }));
    const sheet = await screen.findByRole("dialog", { name: "Подтвердить одобрение" });
    await userEvent.click(within(sheet).getByRole("button", { name: "Одобрить" }));

    expect(await screen.findByText("Не получилось одобрить")).toBeInTheDocument();
    expect(screen.queryByText(/active bookings/)).toBeNull();
    // Заявка на месте — значит ответ не проглочен и не принят молча.
    expect(screen.getByText("Ольга")).toBeInTheDocument();
  });
});

describe("ресепшен сюда не ходит", () => {
  it("видит отказ и не отправляет запрос вовсе", async () => {
    salonHas(OLGA);

    open(DESK_ME);

    expect(
      await screen.findByText("Эта страница только для администраторов."),
    ).toBeInTheDocument();
    expect(mockedList).not.toHaveBeenCalled();
  });
});
