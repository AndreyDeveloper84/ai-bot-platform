/**
 * Ошибка привязывается к полю по машинному признаку, а не по прозе (DRF-2452).
 *
 * Раскладка держалась на разборе английского текста отказа:
 * `e.detail.toLowerCase().includes("contact")`. Признак, которого не видно
 * никому: сервер поправит формулировку — и подпись под полем исчезнет, а
 * человек будет смотреть на верную форму и не понимать, что не так.
 *
 * **Узлов на эту раскладку не было вовсе** — потому она и могла сгнить
 * молча. Теперь поле называет сервер (`details.field`, `views_invite.py`),
 * а слова у поля — те же, что уже показывает собственная проверка формы:
 * новых текстов лист не заводит.
 */
import { render, screen } from "@testing-library/react";
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
  listMasters,
  type MeResponse,
} from "../../lib/admin-api";
import { AdminAddPersonScreen } from "./AdminAddPersonScreen";

const mockedInvite = vi.mocked(inviteMaster);
const mockedListMasters = vi.mocked(listMasters);
const mockedServices = vi.mocked(getCatalogServicesForAdmin);

const OWNER: MeResponse = {
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

/** Английская проза сервера — ровно та, что он отдаёт на деле. */
const SERVER_PROSE = "contact_value exceeds 120 chars";

beforeEach(() => {
  vi.clearAllMocks();
  mockedListMasters.mockResolvedValue({ items: [], next_cursor: null, total_count: 0 });
  mockedServices.mockResolvedValue([]);
});

async function submit() {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/admin/team/add"]}>
      <AdminAddPersonScreen me={OWNER} />
    </MemoryRouter>,
  );
  await user.type(screen.getByLabelText(/Имя и фамилия/), "Анна Петрова");
  await user.type(screen.getByPlaceholderText(/@anna_styl/), "@anna_styl");
  await user.click(screen.getByRole("button", { name: "Пригласить" }));
}

describe("поле называет сервер, а не наш разбор текста", () => {
  it("details.field=contact_value → подпись у поля контакта", async () => {
    mockedInvite.mockRejectedValue(
      new ApiError(400, "bad_request", SERVER_PROSE, { field: "contact_value" }),
    );

    await submit();

    // Наличие раньше отсутствия: подпись есть и стоит у нужного поля.
    const contact = screen.getByPlaceholderText(/@anna_styl/);
    expect(await screen.findByText("Укажите MAX-аккаунт или телефон")).toBeInTheDocument();
    expect(contact).toHaveAttribute("aria-invalid", "true");
    // И у чужого поля её нет.
    expect(screen.getByLabelText(/Имя и фамилия/)).not.toHaveAttribute("aria-invalid", "true");
  });

  it("details.field=name → подпись у поля имени", async () => {
    mockedInvite.mockRejectedValue(
      new ApiError(400, "bad_request", "name is required", { field: "name" }),
    );

    await submit();

    expect(await screen.findByText("Укажите имя и фамилию")).toBeInTheDocument();
    expect(screen.getByLabelText(/Имя и фамилия/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByPlaceholderText(/@anna_styl/)).not.toHaveAttribute("aria-invalid", "true");
  });

  it("поле не названо → общая плашка, и ни одно поле не помечено чужим отказом", async () => {
    // Раньше сюда попадал любой текст без слов «contact» и «name».
    mockedInvite.mockRejectedValue(new ApiError(400, "bad_request", "services must be a list"));

    await submit();

    expect(await screen.findByText("Не получилось отправить")).toBeInTheDocument();
    expect(screen.getByLabelText(/Имя и фамилия/)).not.toHaveAttribute("aria-invalid", "true");
    expect(screen.getByPlaceholderText(/@anna_styl/)).not.toHaveAttribute("aria-invalid", "true");
  });

  it("прозу сервера человек не видит нигде", async () => {
    mockedInvite.mockRejectedValue(
      new ApiError(400, "bad_request", SERVER_PROSE, { field: "contact_value" }),
    );

    await submit();

    await screen.findByText("Укажите MAX-аккаунт или телефон");
    expect(screen.queryByText(new RegExp(SERVER_PROSE))).toBeNull();
    expect(screen.queryByText(/exceeds/)).toBeNull();
  });

  it("сервер переписал формулировку — раскладка держится", async () => {
    // Тот самый отказ, ради которого лист заведён: слова «contact» в
    // тексте больше нет, а поле обязано быть прежним.
    mockedInvite.mockRejectedValue(
      new ApiError(400, "bad_request", "the handle is too long", { field: "contact_value" }),
    );

    await submit();

    expect(await screen.findByText("Укажите MAX-аккаунт или телефон")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/@anna_styl/)).toHaveAttribute("aria-invalid", "true");
  });
});
