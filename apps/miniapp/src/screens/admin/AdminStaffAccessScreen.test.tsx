/**
 * DRF-1061 block 2.4 — the screen that issues staff access codes.
 *
 * What is pinned here is chosen by what would actually hurt:
 *
 *   - the code is shown once, so the screen must SAY so, and must not be
 *     leaveable by a back-swipe that silently destroys a credential;
 *   - `role=owner` is a privilege escalation if an admin can reach it, and
 *     the server's 403 must not be the only thing standing in the way;
 *   - `role=master` LINKS an existing catalog row and never creates one —
 *     if that ever starts sending a name instead of a `master_id`, this
 *     screen has quietly become the other one;
 *   - the ready-to-forward invitation text is an OPEN OWNER DECISION, and
 *     the last test exists to fail if somebody fills it in with invented
 *     copy rather than waiting for the ruling.
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
  return { ...original, issueStaffInvite: vi.fn(), listMasters: vi.fn() };
});

import { ApiError } from "../../lib/api";
import {
  issueStaffInvite,
  listMasters,
  type MeResponse,
  type StaffInviteResponse,
} from "../../lib/admin-api";
import { setClosingConfirmation } from "../../lib/max-sdk";
import {
  AdminStaffAccessScreen,
  INVITE_MESSAGE_TEMPLATE,
  ROLE_OPTIONS,
} from "./AdminStaffAccessScreen";

const mockedIssue = vi.mocked(issueStaffInvite);
const mockedListMasters = vi.mocked(listMasters);
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
};

function renderScreen(me: MeResponse = BASE_ME) {
  return render(
    <MemoryRouter initialEntries={["/admin/team/access"]}>
      <AdminStaffAccessScreen me={me} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
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
// The one-shot code.
// --------------------------------------------------------------------------

describe("the code is shown once", () => {
  it("renders the code together with the warning that it cannot be recovered", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    renderScreen();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    expect(await screen.findByText("AYLA-7K3M")).toBeInTheDocument();
    expect(screen.getByText("Код показывается один раз.")).toBeInTheDocument();
    // Not «you can find it later» — the hash is all that is stored.
    expect(screen.getByText(/восстановить код нельзя/i)).toBeInTheDocument();
  });

  it("warns BEFORE the code, not after it", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    const { container } = renderScreen();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));
    await screen.findByText("AYLA-7K3M");

    // A warning underneath is read after the reader has already decided
    // what to do with the screen, which is too late for a credential.
    const text = container.textContent ?? "";
    expect(text.indexOf("Код показывается один раз.")).toBeLessThan(
      text.indexOf("AYLA-7K3M"),
    );
  });

  it("turns MAX's closing confirmation ON while the code is on screen", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    renderScreen();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));
    await screen.findByText("AYLA-7K3M");

    // Swiping the Mini App away here destroys the code, so MAX must ask.
    await waitFor(() =>
      expect(mockedClosingConfirmation).toHaveBeenCalledWith(true),
    );
  });
});

// --------------------------------------------------------------------------
// Roles.
// --------------------------------------------------------------------------

describe("roles", () => {
  it("hides «Владелец» from an admin", () => {
    renderScreen(ADMIN_ME);

    expect(screen.queryByLabelText("Владелец")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Администратор")).toBeInTheDocument();
  });

  it("offers «Владелец» to the owner", () => {
    renderScreen(BASE_ME);

    expect(screen.getByLabelText("Владелец")).toBeInTheDocument();
  });

  it("lists roles in increasing privilege so the most powerful is not first", () => {
    // Mirrors TenantStaff.Role's own docstring. A tired person taps the
    // first option; that option should not be «full access».
    expect(ROLE_OPTIONS.map((o) => o.value)).toEqual([
      "master",
      "receptionist",
      "admin",
      "owner",
    ]);
  });

  it("sends role only — no master_id — for a staff role", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    renderScreen();

    await user.click(screen.getByLabelText("Администратор"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    await waitFor(() =>
      expect(mockedIssue).toHaveBeenCalledWith({ role: "admin" }),
    );
  });
});

// --------------------------------------------------------------------------
// role=master links an existing row; it never creates one.
// --------------------------------------------------------------------------

describe("role=master", () => {
  it("sends the chosen master_id, never a name", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue({ ...ISSUED, role: "master" });
    renderScreen();

    // `master` is the default role, so the roster loads on mount.
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

  it("refuses to submit without a master and says which field is missing", async () => {
    const user = userEvent.setup();
    renderScreen();

    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    expect(
      await screen.findByText("Выберите мастера, которого нужно связать."),
    ).toBeInTheDocument();
    expect(mockedIssue).not.toHaveBeenCalled();
  });

  it("says the code links an existing master rather than creating one", async () => {
    renderScreen();

    expect(
      await screen.findByText(/Новая карточка не создаётся/),
    ).toBeInTheDocument();
  });

  it("degrades to a readable notice when the roster cannot be loaded", async () => {
    mockedListMasters.mockRejectedValue(new Error("network"));
    renderScreen();

    expect(
      await screen.findByText(/Не получилось загрузить список мастеров/),
    ).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Failures.
// --------------------------------------------------------------------------

describe("failures", () => {
  it("translates the owner-only 403 instead of showing the server's English", async () => {
    const user = userEvent.setup();
    mockedIssue.mockRejectedValue(
      new ApiError(403, "forbidden", "only the salon owner can issue an owner invite"),
    );
    renderScreen();

    await user.click(screen.getByLabelText("Владелец"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    expect(
      await screen.findByText("Код владельца может выдать только владелец салона."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/only the salon owner/i),
    ).not.toBeInTheDocument();
  });

  it("never leaves a failed attempt looking like a success", async () => {
    const user = userEvent.setup();
    mockedIssue.mockRejectedValue(new ApiError(500, "server_error", "boom"));
    renderScreen();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));

    await screen.findByText(/Не получилось выдать код/);
    expect(screen.queryByText(/Код показывается один раз/)).not.toBeInTheDocument();
  });

  it("issues exactly once when the button is tapped twice", async () => {
    const user = userEvent.setup();
    let resolve: ((v: StaffInviteResponse) => void) | undefined;
    mockedIssue.mockImplementation(
      () => new Promise((r) => { resolve = r; }),
    );
    renderScreen();

    await user.click(screen.getByLabelText("Ресепшен"));
    const cta = screen.getByRole("button", { name: "Выдать код" });
    await user.click(cta);
    await user.click(cta);

    expect(mockedIssue).toHaveBeenCalledTimes(1);
    resolve!(ISSUED);
  });
});

// --------------------------------------------------------------------------
// The open owner decision.
// --------------------------------------------------------------------------

describe("the ready-to-forward invitation text", () => {
  it("is not written yet and the screen shows nothing in its place", async () => {
    const user = userEvent.setup();
    mockedIssue.mockResolvedValue(ISSUED);
    const { container } = renderScreen();

    await user.click(screen.getByLabelText("Ресепшен"));
    await user.click(screen.getByRole("button", { name: "Выдать код" }));
    await screen.findByText("AYLA-7K3M");

    // The wording is product voice and belongs to the owner, who has not
    // ruled. If this test fails because the seam was filled, check that the
    // copy came from the owner and not from whoever was writing the code.
    expect(INVITE_MESSAGE_TEMPLATE).toBeNull();
    expect(container.querySelector(".staff-access__message")).toBeNull();
  });
});
