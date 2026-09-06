/**
 * «Добавить человека» — один экран, две ветки (DRF-1061 block 2.4 +
 * DRF-1424 + DRF-1505).
 *
 * Что закреплено — выбрано по тому, что реально стоит дорого:
 *
 *   - **ссылка-приглашение видна и её можно взять.** Без неё владелец
 *     салона физически не может пригласить мастера: личное сообщение
 *     уходит клиентским ботом и достигает только уже существующий чат.
 *     На 06.09 из 34 мастеров в кабинет могут войти 4;
 *   - **отказ доставки назван причиной, а не молчанием.** «Не удалось»
 *     без причины отправляет владельца перепроверять правильный
 *     аккаунт до бесконечности;
 *   - код показывается один раз, поэтому экран обязан это СКАЗАТЬ и не
 *     давать уйти назад свайпом, уничтожив учётные данные;
 *   - `role=owner` — это повышение привилегий, и 403 сервера не должен
 *     быть единственным препятствием;
 *   - `role=master` СВЯЗЫВАЕТ существующую карточку и никогда её не
 *     создаёт: если оттуда начнёт уходить имя вместо `master_id`, этот
 *     экран незаметно стал другим;
 *   - готовый пересылаемый текст — ОТКРЫТОЕ РЕШЕНИЕ ВЛАДЕЛЬЦА, и
 *     последний тест падает, если кто-то заполнил шов выдуманной
 *     формулировкой вместо того, чтобы дождаться ответа.
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
    hapticNotify: vi.fn(),
    hapticSelection: vi.fn(),
    setClosingConfirmation: vi.fn(),
    setBackButton: vi.fn(),
    onBackButton: vi.fn(() => () => undefined),
  };
});

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    issueStaffInvite: vi.fn(),
    listMasters: vi.fn(),
    inviteMaster: vi.fn(),
    getCatalogServicesForAdmin: vi.fn(),
  };
});

import { ApiError } from "../../lib/api";
import {
  getCatalogServicesForAdmin,
  inviteMaster,
  issueStaffInvite,
  listMasters,
  type InviteMasterResponse,
  type MeResponse,
  type StaffInviteResponse,
} from "../../lib/admin-api";
import { setClosingConfirmation } from "../../lib/max-sdk";
import { AdminAddPersonScreen } from "./AdminAddPersonScreen";
import {
  INVITE_MESSAGE_TEMPLATE,
  ROLE_OPTIONS,
} from "./AddPersonAccessCodeSection";
import { deliveryNotice } from "./AddPersonNewMasterSection";

const mockedIssue = vi.mocked(issueStaffInvite);
const mockedListMasters = vi.mocked(listMasters);
const mockedInvite = vi.mocked(inviteMaster);
const mockedServices = vi.mocked(getCatalogServicesForAdmin);
const mockedClosingConfirmation = vi.mocked(setClosingConfirmation);

const BASE_ME: MeResponse = {
  user: { id: "u-1", name: "Андрей", phone_masked: "+• ••• ••• ••12" },
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

const ADMIN_ME: MeResponse = {
  ...BASE_ME,
  role: "admin",
  is_owner: false,
  is_admin: true,
};

const ISSUED: StaffInviteResponse = {
  invite_id: "i-1",
  role: "receptionist",
  code: "AYLA-7K3M",
  expires_at: "2026-08-31T09:00:00+00:00",
  code_is_shown_once: true,
  invite_link: "https://max.ru/id583403546770_3_bot?start=inv_AYLA7K3M",
};

const INVITED: InviteMasterResponse = {
  master_id: "m-9",
  invite_token: "3f6c1e7a-0000-4000-8000-0000000000aa",
  invite_expires_at: "2026-09-13T09:00:00+00:00",
  max_dm_delivery: "queued",
  max_dm_error: "",
  fallback_link: "https://miniapp-dev.gobeauty.site/onboarding/master?token=3f6c1e7a",
  invite_link:
    "https://max.ru/id583403546770_3_bot?start=master_invite_3f6c1e7a-0000-4000-8000-0000000000aa",
  was_idempotent: false,
};

/**
 * Подменить буфер обмена.
 *
 * `navigator.clipboard` в jsdom — свойство только для чтения, поэтому
 * `Object.assign` бросает `TypeError`, а не подменяет. Через
 * `defineProperty` с `configurable: true` подмена и ставится, и
 * снимается между тестами.
 */
function stubClipboard(writeText: ReturnType<typeof vi.fn>) {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
}

/** Открыть экран на ветке кода доступа — прежний `/admin/team/access`. */
function renderAccess(me: MeResponse = BASE_ME) {
  return render(
    <MemoryRouter initialEntries={["/admin/team/access"]}>
      <AdminAddPersonScreen me={me} initialTrack="access-code" />
    </MemoryRouter>,
  );
}

/** Открыть экран на ветке нового мастера — прежний `/admin/team/invite`. */
function renderNewMaster(me: MeResponse = BASE_ME) {
  return render(
    <MemoryRouter initialEntries={["/admin/team/add"]}>
      <AdminAddPersonScreen me={me} />
    </MemoryRouter>,
  );
}

/** Заполнить форму приглашения и отправить. */
async function submitInvite(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/Имя и фамилия/), "Анна Петрова");
  await user.type(screen.getByPlaceholderText(/@anna_styl/), "@anna_styl");
  await user.click(screen.getByRole("button", { name: "Пригласить" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedServices.mockResolvedValue([]);
  mockedListMasters.mockResolvedValue({
    items: [
      {
        id: "m-1",
        name: "Тихонова Ольга",
        specialization: "",
        photo_url: "",
        is_active: true,
        invite_status: "accepted",
        last_seen_at: null,
        services_count: 3,
      },
    ],
    next_cursor: null,
    total_count: 1,
  });
});

// --------------------------------------------------------------------------
// Один экран, две ветки.
// --------------------------------------------------------------------------

describe("одна задача, один экран", () => {
  it("спрашивает, кого добавляют, прямо на экране", () => {
    renderNewMaster();

    expect(screen.getByText("Кого добавляете?")).toBeInTheDocument();
    expect(screen.getByLabelText("Новый мастер")).toBeInTheDocument();
    expect(screen.getByLabelText("Уже работает у нас")).toBeInTheDocument();
  });

  it("переключает ветку без ухода с экрана и без потери возможности вернуться", async () => {
    const user = userEvent.setup();
    renderNewMaster();

    // Ветка мастера: поле имени.
    expect(screen.getByLabelText(/Имя и фамилия/)).toBeInTheDocument();

    await user.click(screen.getByLabelText("Уже работает у нас"));

    // Ветка кода: роли. И вопрос всё ещё на экране — передумать можно.
    expect(await screen.findByLabelText("Ресепшен")).toBeInTheDocument();
    expect(screen.getByText("Кого добавляете?")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Имя и фамилия/)).not.toBeInTheDocument();
  });

  it("открывается на нужной ветке по старому адресу", () => {
    renderAccess();

    expect(screen.getByLabelText("Ресепшен")).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Ссылка-приглашение мастера — то, ради чего задача существует.
// --------------------------------------------------------------------------

describe("ссылка-приглашение", () => {
  it("показывается после создания и её можно скопировать одним нажатием", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);
    mockedInvite.mockResolvedValue(INVITED);
    renderNewMaster();

    await submitInvite(user);

    expect(await screen.findByText(INVITED.invite_link)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Скопировать ссылку" }));

    expect(writeText).toHaveBeenCalledWith(INVITED.invite_link);
    expect(await screen.findByText("Скопировано")).toBeInTheDocument();
  });

  it("говорит вслух, если скопировать не вышло", async () => {
    const user = userEvent.setup();
    stubClipboard(vi.fn().mockRejectedValue(new Error("denied")));
    mockedInvite.mockResolvedValue(INVITED);
    renderNewMaster();

    await submitInvite(user);
    await user.click(
      await screen.findByRole("button", { name: "Скопировать ссылку" }),
    );

    // Кнопка, которая молча ничего не сделала, хуже отсутствующей:
    // читатель уверен, что ссылка у него в буфере.
    expect(
      await screen.findByText(/выделите ссылку и скопируйте вручную/i),
    ).toBeInTheDocument();
  });

  it("не обещает ссылку, которой нет", async () => {
    const user = userEvent.setup();
    mockedInvite.mockResolvedValue({
      ...INVITED,
      invite_link: "",
      max_dm_delivery: "failed",
      max_dm_error: "no_entry_configured",
    });
    renderNewMaster();

    await submitInvite(user);

    expect(await screen.findByText(/Ссылки нет/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Скопировать ссылку" }),
    ).not.toBeInTheDocument();
  });

  it("держит подтверждение выхода, пока ссылка на экране", async () => {
    const user = userEvent.setup();
    mockedInvite.mockResolvedValue(INVITED);
    renderNewMaster();

    await submitInvite(user);
    await screen.findByText(INVITED.invite_link);

    // Свайп-закрытие уносит ссылку: достать её потом можно только
    // запросом к базе — так и пришлось делать на пилоте 30.08.
    await waitFor(() =>
      expect(mockedClosingConfirmation).toHaveBeenCalledWith(true),
    );
  });
});

// --------------------------------------------------------------------------
// Честность про доставку.
// --------------------------------------------------------------------------

describe("отказ доставки", () => {
  it("не выдаёт принятую отправку за доставленную", () => {
    const notice = deliveryNotice(INVITED, "Анна");

    expect(notice.tone).toBe("ok");
    expect(notice.text).toMatch(/Подтверждения доставки/);
    expect(notice.text).not.toMatch(/в течение минуты/);
  });

  it("различает опечатку в аккаунте и ненастроенный контур", () => {
    const typo = deliveryNotice(
      { ...INVITED, max_dm_delivery: "failed", max_dm_error: "max_status_404" },
      "Анна",
    );
    const notConfigured = deliveryNotice(
      {
        ...INVITED,
        max_dm_delivery: "failed",
        max_dm_error: "no_entry_configured",
      },
      "Анна",
    );

    // Опечатку правит владелец — прямо в поле выше.
    expect(typo.text).toMatch(/проверьте написание/i);
    // А это он не исправит ничем, и посылать его перепроверять
    // правильный аккаунт — злее, чем молчать.
    expect(notConfigured.text).toMatch(/настройка на стороне платформы/i);
    expect(notConfigured.text).not.toMatch(/проверьте написание/i);
  });

  it("не называет поломкой то, что просто не построено", () => {
    const skipped = deliveryNotice(
      {
        ...INVITED,
        max_dm_delivery: "skipped",
        max_dm_error: "max_phone_lookup_deferred",
      },
      "Анна",
    );

    expect(skipped.text).toMatch(/пока не умеет/i);
    expect(skipped.text).not.toMatch(/не удалось/i);
  });

  it("показывает причину на экране, а не только в логе", async () => {
    const user = userEvent.setup();
    mockedInvite.mockResolvedValue({
      ...INVITED,
      max_dm_delivery: "failed",
      max_dm_error: "no_entry_configured",
    });
    renderNewMaster();

    await submitInvite(user);

    expect(
      await screen.findByText(/настройка на стороне платформы/i),
    ).toBeInTheDocument();
    // Положительная стража: ссылка при этом на месте, и владельцу есть
    // что передать — иначе экран сообщал бы об отказе и обрывался.
    expect(screen.getByText(INVITED.invite_link)).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Код доступа: показывается один раз.
// --------------------------------------------------------------------------

describe("код показывается один раз", () => {
  it("рисует код вместе с предупреждением, что его не восстановить", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    renderAccess();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    expect(await screen.findByText("AYLA-7K3M")).toBeInTheDocument();
    expect(
      screen.getByText("Код и ссылка показываются один раз."),
    ).toBeInTheDocument();
    expect(screen.getByText(/восстановить его нельзя/i)).toBeInTheDocument();
  });

  it("предупреждает ДО кода, а не после", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    const { container } = renderAccess();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));
    await screen.findByText("AYLA-7K3M");

    const text = container.textContent ?? "";
    expect(text.indexOf("Код и ссылка показываются один раз.")).toBeLessThan(
      text.indexOf("AYLA-7K3M"),
    );
  });

  it("включает подтверждение выхода MAX, пока код на экране", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    renderAccess();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));
    await screen.findByText("AYLA-7K3M");

    await waitFor(() =>
      expect(mockedClosingConfirmation).toHaveBeenCalledWith(true),
    );
  });

  it("даёт ссылку, чтобы четыре символа не диктовали голосом", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);
    mockedIssue.mockResolvedValue(ISSUED);
    renderAccess();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    const link = await screen.findByText(ISSUED.invite_link);
    expect(link).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Скопировать ссылку" }));
    expect(writeText).toHaveBeenCalledWith(ISSUED.invite_link);
  });

  it("говорит, что ссылка и код — одно и то же", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    renderAccess();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));
    await screen.findByText(ISSUED.invite_link);

    // Кто перейдёт по ссылке — тот и потратит код. Читатель должен это
    // знать до того, как отправит её в общий чат.
    expect(screen.getByText(/тот его и\s+потратит/)).toBeInTheDocument();
  });

  it("без салонного бота отдаёт код без ссылки и не врёт про неё", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue({ ...ISSUED, invite_link: "" });
    renderAccess();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    expect(await screen.findByText("AYLA-7K3M")).toBeInTheDocument();
    expect(screen.getByText(/Ссылки нет/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Скопировать ссылку" }),
    ).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Роли.
// --------------------------------------------------------------------------

describe("роли", () => {
  it("прячет «Владелец» от администратора", () => {
    renderAccess(ADMIN_ME);

    expect(screen.queryByLabelText("Владелец")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Администратор")).toBeInTheDocument();
  });

  it("предлагает «Владелец» владельцу", () => {
    renderAccess(BASE_ME);

    expect(screen.getByLabelText("Владелец")).toBeInTheDocument();
  });

  it("перечисляет роли по возрастанию прав", () => {
    expect(ROLE_OPTIONS.map((o) => o.value)).toEqual([
      "master",
      "receptionist",
      "admin",
      "owner",
    ]);
  });

  it("шлёт только роль — без master_id — для штатной роли", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    renderAccess();

    await user.click(screen.getByLabelText("Администратор"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    await waitFor(() =>
      expect(mockedIssue).toHaveBeenCalledWith({ role: "admin" }),
    );
  });
});

// --------------------------------------------------------------------------
// role=master связывает существующую строку и никогда её не создаёт.
// --------------------------------------------------------------------------

describe("role=master", () => {
  it("шлёт выбранный master_id, никогда имя", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue({ ...ISSUED, role: "master" });
    renderAccess();

    await screen.findByRole("option", { name: "Тихонова Ольга" });
    await user.selectOptions(screen.getByRole("combobox"), "m-1");
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    await waitFor(() =>
      expect(mockedIssue).toHaveBeenCalledWith({
        role: "master",
        master_id: "m-1",
      }),
    );
  });

  it("отказывается отправлять без мастера и называет поле", async () => {
    const user = userEvent.setup();
    renderAccess();

    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    expect(
      await screen.findByText("Выберите мастера, которого нужно связать."),
    ).toBeInTheDocument();
    expect(mockedIssue).not.toHaveBeenCalled();
  });

  it("говорит, что код связывает существующего мастера, а не создаёт нового", async () => {
    renderAccess();

    expect(
      await screen.findByText(/Новая карточка не создаётся/),
    ).toBeInTheDocument();
  });

  it("вырождается в читаемое сообщение, когда список мастеров не загрузился", async () => {
    mockedListMasters.mockRejectedValue(new Error("network"));
    renderAccess();

    expect(
      await screen.findByText(/Не получилось загрузить список мастеров/),
    ).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Отказы.
// --------------------------------------------------------------------------

describe("отказы", () => {
  it("переводит 403 вместо английского текста сервера", async () => {
    const user = userEvent.setup();
    mockedIssue.mockRejectedValue(
      new ApiError(403, "forbidden", "only the salon owner can issue an owner invite"),
    );
    renderAccess();

    await user.click(screen.getByLabelText("Владелец"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    expect(
      await screen.findByText("Код владельца может выдать только владелец салона."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/only the salon owner/i)).not.toBeInTheDocument();
  });

  it("никогда не оставляет неудачную попытку похожей на успех", async () => {
    const user = userEvent.setup();
    mockedIssue.mockRejectedValue(new ApiError(500, "server_error", "boom"));
    renderAccess();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    await screen.findByText(/Не получилось выдать код/);
    expect(
      screen.queryByText(/показываются один раз/),
    ).not.toBeInTheDocument();
  });

  it("выдаёт ровно один код при двойном нажатии", async () => {
    const user = userEvent.setup();
    let resolve: ((v: StaffInviteResponse) => void) | undefined;
    mockedIssue.mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    renderAccess();

    await user.click(screen.getByLabelText("Ресепшен"));
    const cta = screen.getByRole("button", { name: "Выдать код" });
    await user.click(cta);
    await user.click(cta);

    expect(mockedIssue).toHaveBeenCalledTimes(1);
    resolve!(ISSUED);
  });

  it("не отправляет приглашение с пустым именем", async () => {
    const user = userEvent.setup();
    renderNewMaster();

    await user.click(screen.getByRole("button", { name: "Пригласить" }));

    expect(await screen.findByText("Укажите имя и фамилию")).toBeInTheDocument();
    expect(mockedInvite).not.toHaveBeenCalled();
  });
});

// --------------------------------------------------------------------------
// Повторное приглашение — идемпотентный ответ.
// --------------------------------------------------------------------------

describe("повторное приглашение", () => {
  it("говорит, что новое не создавалось, и всё равно отдаёт ссылку", async () => {
    const user = userEvent.setup();
    mockedInvite.mockResolvedValue({ ...INVITED, was_idempotent: true });
    renderNewMaster();

    await submitInvite(user);

    expect(
      await screen.findByText(/новое не создавалось/),
    ).toBeInTheDocument();
    // Ссылка обязана быть и здесь: повторное нажатие — обычная реакция
    // владельца на «не доставлено», и остаться без ссылки на нём значит
    // остаться без неё насовсем.
    expect(screen.getByText(INVITED.invite_link)).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Открытое решение владельца.
// --------------------------------------------------------------------------

describe("готовый пересылаемый текст", () => {
  it("не написан, и экран не рисует ничего вместо него", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    const { container } = renderAccess();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));
    await screen.findByText("AYLA-7K3M");

    // Формулировка — голос продукта и принадлежит владельцу, который не
    // высказался. Черновик для DRF-1505 ушёл ему В ОТЧЁТЕ, а не в эту
    // константу. Если тест упал — проверьте, что текст пришёл от
    // владельца, а не от того, кто писал код.
    expect(INVITE_MESSAGE_TEMPLATE).toBeNull();
    expect(container.querySelector(".staff-access__message")).toBeNull();
  });
});
