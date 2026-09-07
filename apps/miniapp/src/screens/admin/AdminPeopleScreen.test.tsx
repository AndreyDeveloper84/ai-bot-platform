/**
 * «Люди салона» — the roster screen.
 *
 * What is pinned here is chosen by what would actually hurt:
 *
 *   - the pilot's own shape is an owner who is ALSO a master. She must
 *     render as ONE row carrying BOTH roles. Collapsing `roles` to
 *     `roles[0]` anywhere in this component restores exactly the blindness
 *     the endpoint was built to remove — and it would still look fine;
 *   - `pending` and `revoked` must never share wording. One person has not
 *     arrived yet and needs the invite resent; the other had access taken
 *     away. The backend went to the trouble of returning three states
 *     rather than a boolean, and a component that renders two of them
 *     identically throws that away silently;
 *   - the screen is owner-only. The server's 403 must not be the only
 *     thing standing in the way.
 *
 * What is NOT pinned here, deliberately: whether a class has a CSS rule.
 * jsdom loads no stylesheets, so every element is equally «visible» to
 * these tests — an unstyled class is invisible to them by construction.
 * That is `tools/lint/miniapp_style_contract.py`'s job (DRF-1066); it runs
 * repo-wide and it is what caught the dead `screen__header` on this file.
 * Re-implementing it here would be a test that cannot fail.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  return { ...original, getStaffRoster: vi.fn(), revokeStaffAccess: vi.fn() };
});

import { ApiError } from "../../lib/api";
import {
  getStaffRoster,
  revokeStaffAccess,
  type MeResponse,
  type RoleSource,
  type RoleState,
  type StaffRoleGrant,
  type StaffRosterPerson,
  type StaffRosterResponse,
} from "../../lib/admin-api";
import { AdminPeopleScreen } from "./AdminPeopleScreen";

const mockedRoster = vi.mocked(getStaffRoster);
const mockedRevoke = vi.mocked(revokeStaffAccess);

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

const ADMIN_ME: MeResponse = {
  ...OWNER_ME,
  role: "admin",
  is_owner: false,
  is_admin: true,
};

/** Offsets from now — never a literal date. */
const daysAgo = (n: number): string =>
  new Date(Date.now() - n * 24 * 60 * 60 * 1000).toISOString();

/**
 * Build a grant the way the server does — `active` DERIVED from `state`,
 * never written by hand.
 *
 * Spelling both out at each fixture would let a test encode a payload the
 * backend cannot produce (`state: "pending", active: true`), and a test
 * standing on an impossible response proves nothing about the real one.
 */
function grant(
  role: StaffRoleGrant["role"],
  state: RoleState,
  source: RoleSource,
  since: string | null,
): StaffRoleGrant {
  return { role, state, source, since, active: state === "active" };
}

const OWNER_MASTER: StaffRosterPerson = {
  id: "bot:u-1",
  bot_user_id: "u-1",
  master_id: "m-1",
  name: "Карина",
  has_account: true,
  is_active: true,
  roles: [
    grant("owner", "active", "direct", daysAgo(300)),
    grant("master", "active", "master_invite", daysAgo(120)),
  ],
};

function rosterOf(...items: StaffRosterPerson[]): StaffRosterResponse {
  return { items, total_count: items.length, truncated: false };
}

function renderScreen(me: MeResponse = OWNER_ME) {
  return render(
    <MemoryRouter initialEntries={["/admin/team/people"]}>
      <AdminPeopleScreen me={me} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedRoster.mockResolvedValue(rosterOf(OWNER_MASTER));
  mockedRevoke.mockResolvedValue({
    changed: true,
    roles_revoked: ["master"],
    master_unlinked: true,
  });
});

describe("the additive-role trap, at the UI layer", () => {
  it("renders a two-role person once, carrying both roles", async () => {
    renderScreen();

    // Once, not twice — the name is the identity the reader sees.
    await waitFor(() => {
      expect(screen.getAllByText("Карина")).toHaveLength(1);
    });
    // Both roles present. A component rendering roles[0] would show
    // «Владелец» alone and look entirely correct.
    expect(screen.getByText("Владелец")).toBeInTheDocument();
    expect(screen.getByText("Мастер")).toBeInTheDocument();
  });

  it("shows a per-role source and date line for each role", async () => {
    renderScreen();

    await waitFor(() => {
      expect(
        screen.getByText(/Владелец · добавлен\(а\) напрямую · /),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByText(/Мастер · по приглашению мастера · /),
    ).toBeInTheDocument();
  });

  it("omits the date when the catalog sync produced the row", async () => {
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...OWNER_MASTER,
        roles: [grant("master", "active", "direct", null)],
      }),
    );
    renderScreen();

    // «Unknown» is said by omission, not by an invented timestamp.
    await waitFor(() => {
      expect(
        screen.getByText("Мастер · добавлен(а) напрямую"),
      ).toBeInTheDocument();
    });
  });
});

describe("pending, revoked and ayla_unlinked never share wording", () => {
  const base: StaffRosterPerson = {
    id: "master:m-9",
    bot_user_id: null,
    master_id: "m-9",
    name: "Наталья Прохорова",
    has_account: false,
    is_active: false,
    roles: [],
  };

  it("says the invite was not accepted for a pending master", async () => {
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...base,
        roles: [grant("master", "pending", "master_invite", daysAgo(2))],
      }),
    );
    renderScreen();

    await waitFor(() => {
      expect(screen.getByText(/приглашение не принято/)).toBeInTheDocument();
    });
    // The opposite word must be absent — this is the whole point.
    expect(screen.queryByText(/доступ отозван/)).not.toBeInTheDocument();
  });

  it("says access was revoked for a revoked master", async () => {
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...base,
        roles: [grant("master", "revoked", "master_invite", daysAgo(40))],
      }),
    );
    renderScreen();

    await waitFor(() => {
      expect(screen.getByText(/доступ отозван/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/приглашение не принято/)).not.toBeInTheDocument();
  });

  it("names the Ayla link failure, and blames neither the master nor the salon", async () => {
    // DRF-1540. This master looks entirely fine on every other column —
    // accepted, active, in the roster — and is off the storefront because
    // we could not link her row to a canonical Ayla id. Before the fix she
    // was sold to clients and received no booking notification at all.
    //
    // The owner's next move is to come to us. `доступ отозван` would send
    // her hunting for whoever revoked it (nobody did) and
    // `приглашение не принято` would send her back to the master (she
    // accepted). One text for three causes is the defect, not the copy.
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...base,
        roles: [grant("master", "ayla_unlinked", "master_invite", daysAgo(1))],
      }),
    );
    renderScreen();

    await waitFor(() => {
      expect(
        screen.getByText(/не удалось связать профиль мастера с Ayla/),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/доступ отозван/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/приглашение не принято/),
    ).not.toBeInTheDocument();
  });

  it("keeps a revoked role visible rather than dropping it", async () => {
    // Disappearance proves nothing: the owner has to READ that the revoke
    // landed. A row that simply vanished would look identical to a person
    // who was never there.
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...OWNER_MASTER,
        name: "Аня",
        roles: [grant("admin", "revoked", "access_code", daysAgo(10))],
      }),
    );
    renderScreen();

    await waitFor(() => {
      expect(screen.getByText("Аня")).toBeInTheDocument();
    });
    // Twice on purpose: once on the chip, once on the per-role line.
    // Both must survive — the chip alone would lose «как попал и когда».
    expect(screen.getAllByText(/Администратор/)).toHaveLength(2);
    expect(screen.getByText(/доступ отозван/)).toBeInTheDocument();
  });
});

describe("who may look", () => {
  it("tells an admin the list is owner-only and never calls the API", () => {
    renderScreen(ADMIN_ME);

    expect(
      screen.getByText(/Список ролей видит только владелец салона/),
    ).toBeInTheDocument();
    expect(mockedRoster).not.toHaveBeenCalled();
  });

  it("fetches for an owner — the guard above is not simply always on", async () => {
    renderScreen();

    await waitFor(() => {
      expect(mockedRoster).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByText("Люди салона")).toBeInTheDocument();
  });
});

describe("the truncation banner", () => {
  it("names both numbers when the cap dropped people", async () => {
    mockedRoster.mockResolvedValue({
      items: [OWNER_MASTER],
      total_count: 217,
      truncated: true,
    });
    renderScreen();

    await waitFor(() => {
      expect(screen.getByText(/Показаны первые/)).toBeInTheDocument();
    });
    // The count chip in the heading and the banner both name it.
    expect(screen.getAllByText(/217/).length).toBeGreaterThanOrEqual(1);
  });

  it("stays silent on an ordinary roster", async () => {
    renderScreen();

    await waitFor(() => {
      expect(screen.getByText("Карина")).toBeInTheDocument();
    });
    expect(screen.queryByText(/Показаны первые/)).not.toBeInTheDocument();
  });
});

/**
 * Revoking access — the caller `staff/revoke/` never had.
 *
 * What is pinned is what would actually hurt:
 *
 *   - the endpoint is real and takes EXACTLY ONE of master_id /
 *     bot_user_id. Sending both, neither, or the wrong one is a 400 that
 *     a mocked-away client would never show;
 *   - the button must not appear where the server would refuse. Owner
 *     (409) and self (403) are refusals, not warnings, and a button that
 *     always fails teaches the owner to distrust the screen;
 *   - the confirmation must name WHO and WHAT. Over a list of similar
 *     rows a bare «Вы уверены?» is how the wrong person loses access,
 *     and nothing on this screen can undo it;
 *   - a refused revoke must be shown. DRF-1556 was an empty `catch`
 *     hiding a contract mismatch; silence here would leave the owner
 *     believing access is gone when it is not.
 */

/** Somebody else, with real access — the ordinary case for the button. */
const ANYA: StaffRosterPerson = {
  id: "bot:u-7",
  bot_user_id: "u-7",
  master_id: "m-7",
  name: "Аня Ковалёва",
  has_account: true,
  is_active: true,
  roles: [grant("master", "active", "master_invite", daysAgo(30))],
};

const revokeButton = (name: string) =>
  screen.getByRole("button", { name: `Отозвать доступ у ${name}` });

const queryRevokeButton = (name: string) =>
  screen.queryByRole("button", { name: `Отозвать доступ у ${name}` });

/** The confirm button inside the sheet — not one of the row buttons. */
const confirmButton = () =>
  screen.getByRole("button", { name: "Отозвать доступ" });

async function openConfirm(person: StaffRosterPerson) {
  mockedRoster.mockResolvedValue(rosterOf(person));
  renderScreen();
  await waitFor(() => {
    expect(revokeButton(person.name)).toBeInTheDocument();
  });
  fireEvent.click(revokeButton(person.name));
  await screen.findByRole("dialog");
}

describe("the confirmation names who and what", () => {
  it("asks about this person by name, and lists the role going away", async () => {
    await openConfirm(ANYA);

    const dialog = screen.getByRole("dialog");
    // The name, so the owner can see she picked the row she meant.
    expect(dialog).toHaveAccessibleName("Отозвать доступ у Аня Ковалёва?");
    // The role, so «доступ» is not an abstraction. A confirmation that
    // says neither is a confirmation of nothing.
    expect(screen.getByText(/Снимаем роль: Мастер\./)).toBeInTheDocument();
    // And that this is a one-way door — the whole reason to confirm.
    expect(screen.getByText(/придётся выдать новое/)).toBeInTheDocument();
  });

  it("lists every role a two-role person is about to lose", async () => {
    // The pilot's own shape. Revoking takes ALL of them — a dialog that
    // named one would understate what the owner is agreeing to.
    await openConfirm({
      ...ANYA,
      roles: [
        grant("master", "active", "master_invite", daysAgo(30)),
        grant("admin", "active", "access_code", daysAgo(10)),
      ],
    });

    expect(
      screen.getByText(/Снимаем роль: Мастер и Администратор\./),
    ).toBeInTheDocument();
  });

  it("does nothing at all until the owner confirms", async () => {
    await openConfirm(ANYA);

    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(mockedRevoke).not.toHaveBeenCalled();
    // Backing out must not consume the row's button.
    expect(revokeButton("Аня Ковалёва")).toBeInTheDocument();
  });
});

describe("what actually reaches the server", () => {
  it("names the person with bot_user_id, and with nothing else", async () => {
    await openConfirm(ANYA);
    fireEvent.click(confirmButton());

    await waitFor(() => {
      expect(mockedRevoke).toHaveBeenCalledTimes(1);
    });
    const payload = mockedRevoke.mock.calls.at(0)?.[0];
    // Presence first, and it is not `toBeDefined()`: an absent call makes
    // this read `undefined !== "u-7"` and fail by name, so the absence
    // assertion below can never run over a payload that was never sent.
    expect(payload?.bot_user_id).toBe("u-7");
    // EXACTLY ONE identifier — the server answers 400 to both or neither,
    // and a payload carrying master_id as well would never be sent by a
    // client that had actually been run against it.
    expect(payload).not.toHaveProperty("master_id");
  });

  it("re-reads the roster so the row can say «доступ отозван»", async () => {
    // Disappearing or staying put both look like nothing happened. The
    // state has to come back from the server — the same source every
    // other chip on this screen reads.
    mockedRoster.mockResolvedValueOnce(rosterOf(ANYA)).mockResolvedValueOnce(
      rosterOf({
        ...ANYA,
        is_active: false,
        roles: [grant("master", "revoked", "master_invite", daysAgo(30))],
      }),
    );
    renderScreen();
    await waitFor(() => {
      expect(revokeButton("Аня Ковалёва")).toBeInTheDocument();
    });
    fireEvent.click(revokeButton("Аня Ковалёва"));
    fireEvent.click(
      await screen.findByRole("button", { name: "Отозвать доступ" }),
    );

    // Presence first: the reload happened and brought a body back.
    const revokedChip = await screen.findByText(/доступ отозван/);
    expect(revokedChip).toBeInTheDocument();
    expect(mockedRoster).toHaveBeenCalledTimes(2);
    // Reusing the existing `revoked` state, not inventing a fourth
    // wording — and the offer is gone, because there is nothing left.
    expect(queryRevokeButton("Аня Ковалёва")).not.toBeInTheDocument();
  });
});

describe("a refusal is shown, never swallowed", () => {
  it("keeps the dialog open and prints what the server said", async () => {
    mockedRevoke.mockRejectedValue(
      new ApiError(409, "owner_revoke_refused", "нельзя отозвать этот доступ"),
    );
    await openConfirm(ANYA);
    fireEvent.click(confirmButton());

    // The outcome, in one line, before any diagnosis.
    expect(await screen.findByText("Доступ не отозван.")).toBeInTheDocument();
    // And the server's own words, via the shared StateError.
    expect(screen.getByText("нельзя отозвать этот доступ")).toBeInTheDocument();
    // Still open: closing on failure would leave the owner unsure.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    // And the list was NOT reloaded — nothing changed to re-read.
    expect(mockedRoster).toHaveBeenCalledTimes(1);
  });

  it("retries the revoke itself, not the page", async () => {
    mockedRevoke.mockRejectedValueOnce(
      new ApiError(500, "server_error", "боль"),
    );
    await openConfirm(ANYA);
    fireEvent.click(confirmButton());
    await screen.findByText("Доступ не отозван.");

    fireEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));

    await waitFor(() => {
      expect(mockedRevoke).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});

describe("the button is never offered where the server would refuse", () => {
  it("offers it on an ordinary master — the guards below are not always on", async () => {
    mockedRoster.mockResolvedValue(rosterOf(ANYA));
    renderScreen();

    await waitFor(() => {
      expect(revokeButton("Аня Ковалёва")).toBeInTheDocument();
    });
  });

  it("never on the salon owner — the service answers 409", async () => {
    // A tenant has one active owner and only an owner may issue an owner
    // code. Revoking it leaves a salon nobody can ever re-enter.
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...ANYA,
        roles: [grant("owner", "active", "direct", daysAgo(300))],
      }),
    );
    renderScreen();

    const name = await screen.findByText("Аня Ковалёва");
    expect(name).toBeInTheDocument();
    expect(queryRevokeButton("Аня Ковалёва")).not.toBeInTheDocument();
  });

  it("never on yourself — the view answers 403", async () => {
    // OWNER_ME is `u-1`; an admin who revokes their own access cannot
    // restore it and would need the owner to issue a fresh code.
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...ANYA,
        bot_user_id: "u-1",
        name: "Карина",
        roles: [grant("admin", "active", "access_code", daysAgo(50))],
      }),
    );
    renderScreen();

    const name = await screen.findByText("Карина");
    expect(name).toBeInTheDocument();
    expect(queryRevokeButton("Карина")).not.toBeInTheDocument();
  });

  it("never where no account exists — nothing to take away", async () => {
    // `has_account` is defined as `bot_user_id is not None`, and a revoke
    // by master_id here would answer 200 with `changed: false`.
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...ANYA,
        bot_user_id: null,
        has_account: false,
        roles: [grant("master", "active", "direct", daysAgo(5))],
      }),
    );
    renderScreen();

    const name = await screen.findByText("Аня Ковалёва");
    expect(name).toBeInTheDocument();
    expect(queryRevokeButton("Аня Ковалёва")).not.toBeInTheDocument();
  });

  it("never on an invite nobody has accepted — resend it, do not revoke it", async () => {
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...ANYA,
        roles: [grant("master", "pending", "master_invite", daysAgo(2))],
      }),
    );
    renderScreen();

    const chip = await screen.findByText(/приглашение не принято/);
    expect(chip).toBeInTheDocument();
    expect(queryRevokeButton("Аня Ковалёва")).not.toBeInTheDocument();
  });

  it("never twice on the same role — «доступ отозван» must stay true", async () => {
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...ANYA,
        is_active: false,
        roles: [grant("master", "revoked", "master_invite", daysAgo(40))],
      }),
    );
    renderScreen();

    const chip = await screen.findByText(/доступ отозван/);
    expect(chip).toBeInTheDocument();
    expect(queryRevokeButton("Аня Ковалёва")).not.toBeInTheDocument();
  });

  it("offers it on an ayla_unlinked master — that access is real", async () => {
    // DRF-1540 is ours to fix, and the chip says so. It does not follow
    // that the owner may not remove a master who is leaving anyway: she
    // accepted, she is linked, she can open the Mini App.
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...ANYA,
        is_active: false,
        roles: [grant("master", "ayla_unlinked", "master_invite", daysAgo(1))],
      }),
    );
    renderScreen();

    await waitFor(() => {
      expect(revokeButton("Аня Ковалёва")).toBeInTheDocument();
    });
  });

  it("shows no revoke button to anyone who is not the owner", async () => {
    // `mayRevoke` mirrors `require_admin_role` — owner and admin, never
    // the front desk — and reads the VIEWER'S ROLE, never the row's
    // state, which is `AdminSalonDayScreen`'s mistake.
    //
    // Today the roster endpoint is narrower than the revoke endpoint, so
    // an admin gets the owner-only sentence and no list at all, and that
    // is what this pins. The role gate is what keeps the button correct
    // on the day the roster widens; it cannot be observed through the DOM
    // until then, and inventing a way to would be testing the test.
    renderScreen(ADMIN_ME);

    expect(
      screen.getByText(/Список ролей видит только владелец салона/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Отозвать доступ/ }),
    ).not.toBeInTheDocument();
  });
});
