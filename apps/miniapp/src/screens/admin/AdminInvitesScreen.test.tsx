/**
 * «Коды доступа» — issued codes (DRF-2275).
 *
 * Pinned, by what would hurt:
 *   - four statuses, four words — «ждёт» asks for a move, the others do not;
 *   - buttons only where the server acts: «Отменить» on a waiting code,
 *     «Отправить заново» on anything not yet used, nothing on an accepted
 *     code, and nothing for an admin on an OWNER code (the server's 403);
 *   - a resent code is shown once, through the same block as a new one,
 *     and the list is re-read afterwards;
 *   - the front desk gets a sentence, not a request that can only fail.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/max-sdk")>();
  return {
    ...original,
    getInitData: () => "test-init-data",
    setBackButton: vi.fn(),
    onBackButton: vi.fn(() => () => undefined),
    hapticNotify: vi.fn(),
    hapticSelection: vi.fn(),
  };
});

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    listStaffInvites: vi.fn(),
    cancelStaffInvite: vi.fn(),
    resendStaffInvite: vi.fn(),
  };
});

import { ApiError } from "../../lib/api";
import {
  cancelStaffInvite,
  listStaffInvites,
  resendStaffInvite,
  type MeResponse,
  type StaffInviteRow,
} from "../../lib/admin-api";
import { AdminInvitesScreen } from "./AdminInvitesScreen";

const mockedList = vi.mocked(listStaffInvites);
const mockedCancel = vi.mocked(cancelStaffInvite);
const mockedResend = vi.mocked(resendStaffInvite);

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

const ADMIN_ME: MeResponse = { ...OWNER_ME, role: "admin", is_owner: false, is_admin: true };
const DESK_ME: MeResponse = {
  ...OWNER_ME,
  role: "receptionist",
  is_owner: false,
  is_receptionist: true,
};

const daysFromNow = (n: number): string =>
  new Date(Date.now() + n * 24 * 60 * 60 * 1000).toISOString();

function inv(over: Partial<StaffInviteRow> & Pick<StaffInviteRow, "id">): StaffInviteRow {
  return {
    role: "admin",
    status: "pending",
    note: "",
    master_name: null,
    created_at: daysFromNow(-2),
    expires_at: daysFromNow(5),
    used_at: null,
    revoked_at: null,
    ...over,
  };
}

const PENDING = inv({ id: "i-1", role: "receptionist", note: "Лена" });
const ACCEPTED = inv({ id: "i-2", status: "accepted", used_at: daysFromNow(-1) });
const EXPIRED = inv({ id: "i-3", status: "expired", expires_at: daysFromNow(-1) });
const CANCELLED = inv({ id: "i-4", status: "cancelled", revoked_at: daysFromNow(-1) });
const OWNER_CODE = inv({ id: "i-5", role: "owner" });

function listOf(...items: StaffInviteRow[]) {
  return { items, total_count: items.length, truncated: false };
}

function renderScreen(me: MeResponse = OWNER_ME) {
  return render(
    <MemoryRouter initialEntries={["/admin/team/invites"]}>
      <AdminInvitesScreen me={me} />
    </MemoryRouter>,
  );
}

const row = (text: string) => screen.getByText(text).closest("li") as HTMLElement;

beforeEach(() => {
  vi.clearAllMocks();
  mockedList.mockResolvedValue(listOf(PENDING, ACCEPTED, EXPIRED, CANCELLED));
  mockedCancel.mockResolvedValue({ changed: true, status: "cancelled" });
  mockedResend.mockResolvedValue({
    invite_id: "i-9",
    role: "receptionist",
    code: "AYLA-7Q2K",
    expires_at: daysFromNow(7),
    code_is_shown_once: true,
    invite_link: "",
    resent_from: "i-1",
  } as Awaited<ReturnType<typeof resendStaffInvite>>);
});

describe("four statuses, four words, and the buttons that fit each", () => {
  it("names each status and offers only what the server would do", async () => {
    renderScreen();
    expect(await screen.findByText("Заметка: Лена")).toBeInTheDocument();

    const pending = row("Заметка: Лена");
    expect(within(pending).getByText("ждёт")).toBeInTheDocument();
    expect(within(pending).getByRole("button", { name: /Отменить код/ })).toBeInTheDocument();
    expect(
      within(pending).getByRole("button", { name: /Отправить заново/ }),
    ).toBeInTheDocument();

    const statuses = screen.getAllByText(/^(принят|истёк|отменён)$/).map((n) => n.textContent);
    expect(statuses).toEqual(["принят", "истёк", "отменён"]);

    const accepted = screen.getByText("принят").closest("li") as HTMLElement;
    expect(within(accepted).queryAllByRole("button")).toHaveLength(0);
    for (const word of ["истёк", "отменён"]) {
      const li = screen.getByText(word).closest("li") as HTMLElement;
      expect(within(li).getByRole("button", { name: /Отправить заново/ })).toBeInTheDocument();
      expect(within(li).queryByRole("button", { name: /Отменить код/ })).not.toBeInTheDocument();
    }
  });

  it("gives an admin no buttons on an owner code — and keeps the rest", async () => {
    mockedList.mockResolvedValue(listOf(PENDING, OWNER_CODE));
    renderScreen(ADMIN_ME);
    expect(await screen.findByText("Владелец")).toBeInTheDocument();

    const owner = screen.getByText("Владелец").closest("li") as HTMLElement;
    expect(within(owner).queryAllByRole("button")).toHaveLength(0);
    expect(
      within(row("Заметка: Лена")).getByRole("button", { name: /Отменить код/ }),
    ).toBeInTheDocument();
  });

  it("tells the front desk it is not theirs, and asks nothing", () => {
    renderScreen(DESK_ME);
    expect(
      screen.getByText("Коды доступа видят владелец и администраторы салона."),
    ).toBeInTheDocument();
    expect(mockedList).not.toHaveBeenCalled();
  });
});

describe("cancel", () => {
  it("asks first, then cancels this code and re-reads the list", async () => {
    renderScreen();
    await screen.findByText("Заметка: Лена");
    fireEvent.click(within(row("Заметка: Лена")).getByRole("button", { name: /Отменить код/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Код: Ресепшен · Лена.")).toBeInTheDocument();
    expect(mockedCancel).not.toHaveBeenCalled();

    const reads = mockedList.mock.calls.length;
    fireEvent.click(within(dialog).getByRole("button", { name: "Отменить код" }));
    await waitFor(() => {
      expect(mockedCancel).toHaveBeenCalledWith("i-1");
    });
    await waitFor(() => {
      expect(mockedList.mock.calls.length).toBe(reads + 1);
    });
  });

  it("says a refusal in words and keeps the sheet", async () => {
    mockedCancel.mockRejectedValue(new ApiError(409, "invite_already_used", "used"));
    renderScreen();
    await screen.findByText("Заметка: Лена");
    fireEvent.click(within(row("Заметка: Лена")).getByRole("button", { name: /Отменить код/ }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Отменить код" }),
    );

    expect(
      await screen.findByText("Этим кодом уже воспользовались — доступ выдан. Обновите список."),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("resend", () => {
  it("shows the new code once, with the warning, and returns to a fresh list", async () => {
    renderScreen();
    await screen.findByText("Заметка: Лена");
    fireEvent.click(
      within(row("Заметка: Лена")).getByRole("button", { name: /Отправить заново/ }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("Выдадим новый код на ту же роль, а прежний перестанет работать."),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Выдать новый код" }));

    expect(await screen.findByText("AYLA-7Q2K")).toBeInTheDocument();
    expect(screen.getByText("Код и ссылка показываются один раз.")).toBeInTheDocument();
    expect(mockedResend).toHaveBeenCalledWith("i-1");

    const reads = mockedList.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Готово" }));
    await waitFor(() => {
      expect(mockedList.mock.calls.length).toBe(reads + 1);
    });
    expect(screen.queryByText("AYLA-7Q2K")).not.toBeInTheDocument();
  });

  it("does not claim an old code stops working when it already had", async () => {
    renderScreen();
    await screen.findByText("истёк");
    const expired = screen.getByText("истёк").closest("li") as HTMLElement;
    fireEvent.click(within(expired).getByRole("button", { name: /Отправить заново/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Выдадим новый код на ту же роль.")).toBeInTheDocument();
  });
});
