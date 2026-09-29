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
  return {
    ...original,
    getStaffRoster: vi.fn(),
    revokeStaffAccess: vi.fn(),
    changeStaffRole: vi.fn(),
    restoreStaffAccess: vi.fn(),
  };
});

import { ApiError } from "../../lib/api";
import {
  changeStaffRole,
  getStaffRoster,
  restoreStaffAccess,
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
const mockedChangeRole = vi.mocked(changeStaffRole);
const mockedRestore = vi.mocked(restoreStaffAccess);

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
  restorable_roles: [],
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
  restorable_roles: [],
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

  it("says the profile is unfinished, and does not call it a revoke", async () => {
    // DRF-1521. `is_active=false` on an accepted master answers two
    // different questions and they need two different next moves: the
    // owner took her off the storefront herself (nothing to fix), or the
    // master accepted the invite and stopped halfway (write to her).
    // `доступ отозван` on the second one sends the owner hunting for
    // whoever revoked the access — the same lie DRF-1506 removed for
    // `pending`, one status later.
    //
    // Unreachable on live data until DRF-1521 пп. 4-6 land; the wording
    // exists first so this screen is not the last place to learn about it.
    mockedRoster.mockResolvedValue(
      rosterOf({
        ...base,
        roles: [grant("master", "profile_incomplete", "master_invite", daysAgo(3))],
      }),
    );
    renderScreen();

    await waitFor(() => {
      expect(screen.getByText(/профиль не заполнен/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/доступ отозван/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/приглашение не принято/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/не удалось связать профиль мастера с Ayla/),
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
  restorable_roles: [],
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
    // And where the way back is — since DRF-2274 it is on this screen.
    expect(
      screen.getByText(/Вернуть доступ можно будет здесь же/),
    ).toBeInTheDocument();
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
  it("keeps the dialog open and says so in our own words, not the server's", async () => {
    // DRF-2446. Узел раньше подставлял русский `detail` и проверял, что
    // экран печатает «слова сервера». Сервер здесь говорит по-английски:
    // `staff_revoke.py:149` поднимает «the salon owner's access cannot be
    // revoked here», и владелец увидел бы эту строку. То есть узел
    // закреплял ровно тот дефект, ради которого заведён лист.
    //
    // Отказ по-прежнему НЕ проглочен — он назван собственной строкой
    // экрана; внутренний текст уходит в журнал.
    mockedRevoke.mockRejectedValue(
      new ApiError(409, "owner_revoke_refused", "the salon owner's access cannot be revoked here"),
    );
    await openConfirm(ANYA);
    fireEvent.click(confirmButton());

    // The outcome, in one line, before any diagnosis.
    expect(await screen.findByText("Доступ не отозван.")).toBeInTheDocument();
    // И ни слова внутреннего английского на экране.
    expect(screen.queryByText(/salon owner's access/)).toBeNull();
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

/**
 * DRF-2273 — «Сменить роль». Owner only (her ruling, `views_staff_roster`).
 *
 * Pinned: the button appears only where the server would accept; the
 * sheet names who and what they hold now; nothing is sent before a new
 * role is picked; the current role and «Владелец» are never offered; the
 * catalog's refusal (admin needs the catalog half, DRF-2085) is said in
 * words with its own hint, not as an English slug.
 */
const LENA_ADMIN: StaffRosterPerson = {
  id: "bot:u-8",
  bot_user_id: "u-8",
  master_id: null,
  name: "Лена",
  has_account: true,
  is_active: true,
  restorable_roles: [],
  roles: [grant("admin", "active", "access_code", daysAgo(40))],
};

const roleButton = (name: string) =>
  screen.getByRole("button", { name: `Сменить роль: ${name}` });

const queryRoleButton = (name: string) =>
  screen.queryByRole("button", { name: `Сменить роль: ${name}` });

async function openRoleSheet(person: StaffRosterPerson) {
  mockedRoster.mockResolvedValue(rosterOf(person));
  renderScreen();
  await waitFor(() => {
    expect(roleButton(person.name)).toBeInTheDocument();
  });
  fireEvent.click(roleButton(person.name));
  await screen.findByRole("dialog");
}

describe("DRF-2273 — the role sheet", () => {
  beforeEach(() => {
    mockedChangeRole.mockResolvedValue({
      role: "receptionist",
      previous_roles: ["admin"],
    });
  });

  it("names the person and the role held now, and offers only the other one", async () => {
    await openRoleSheet(LENA_ADMIN);

    expect(screen.getByRole("dialog")).toHaveAccessibleName(
      "Сменить роль: Лена",
    );
    expect(screen.getByText(/Сейчас: Администратор\./)).toBeInTheDocument();
    const options = screen.getAllByRole("radio");
    expect(options.map((o) => o.textContent)).toEqual(["Ресепшен"]);
    expect(
      screen.queryByRole("radio", { name: "Владелец" }),
    ).not.toBeInTheDocument();
  });

  it("offers both roles to a person who holds both — never an empty sheet", async () => {
    await openRoleSheet({
      ...LENA_ADMIN,
      roles: [
        grant("admin", "active", "access_code", daysAgo(40)),
        grant("receptionist", "active", "access_code", daysAgo(20)),
      ],
    });

    expect(
      screen.getByText(/Сейчас: Администратор и Ресепшен\./),
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole("radio").map((o) => o.textContent),
    ).toEqual(["Администратор", "Ресепшен"]);
  });

  it("names a catalog refusal even without a hint", async () => {
    mockedChangeRole.mockRejectedValue(
      new ApiError(409, "catalog_admin_link_refused", "transport_error"),
    );
    await openRoleSheet({
      ...LENA_ADMIN,
      roles: [grant("receptionist", "active", "access_code", daysAgo(40))],
    });

    fireEvent.click(screen.getByRole("radio", { name: "Администратор" }));
    fireEvent.click(screen.getByRole("button", { name: "Сменить роль" }));

    expect(
      await screen.findByText("Каталог не подтвердил администратора."),
    ).toBeInTheDocument();
  });

  it("says a 404 in words and offers no pointless retry", async () => {
    mockedChangeRole.mockRejectedValue(
      new ApiError(404, "not_found", "no such person in this salon"),
    );
    await openRoleSheet(LENA_ADMIN);

    fireEvent.click(screen.getByRole("radio", { name: "Ресепшен" }));
    fireEvent.click(screen.getByRole("button", { name: "Сменить роль" }));

    expect(
      await screen.findByText(
        "Этого человека больше нет в салоне — обновите список.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Попробовать снова" }),
    ).not.toBeInTheDocument();
  });

  it("sends nothing until a new role is picked", async () => {
    await openRoleSheet(LENA_ADMIN);

    const confirm = screen.getByRole("button", { name: "Сменить роль" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(mockedChangeRole).not.toHaveBeenCalled();
  });

  it("sends the person and the picked role, then re-reads the roster", async () => {
    await openRoleSheet(LENA_ADMIN);
    const readsBefore = mockedRoster.mock.calls.length;

    fireEvent.click(screen.getByRole("radio", { name: "Ресепшен" }));
    fireEvent.click(screen.getByRole("button", { name: "Сменить роль" }));

    await waitFor(() => {
      expect(mockedChangeRole).toHaveBeenCalledTimes(1);
    });
    expect(mockedChangeRole.mock.calls.at(0)?.[0]).toEqual({
      bot_user_id: "u-8",
      role: "receptionist",
    });
    await waitFor(() => {
      expect(mockedRoster.mock.calls.length).toBe(readsBefore + 1);
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("says a master role stays as it is", async () => {
    await openRoleSheet({
      ...LENA_ADMIN,
      master_id: "m-8",
      roles: [
        grant("admin", "active", "access_code", daysAgo(40)),
        grant("master", "active", "master_invite", daysAgo(90)),
      ],
    });

    expect(
      screen.getByText(/Роль мастера останется как есть\./),
    ).toBeInTheDocument();
  });

  it("says the catalog's refusal in words, with its hint, and keeps the sheet", async () => {
    mockedChangeRole.mockRejectedValue(
      new ApiError(409, "catalog_admin_link_refused", "transport_error", {
        hint: "каталог недоступен — повторить позже",
      }),
    );
    await openRoleSheet({
      ...LENA_ADMIN,
      roles: [grant("receptionist", "active", "access_code", daysAgo(40))],
    });

    fireEvent.click(screen.getByRole("radio", { name: "Администратор" }));
    fireEvent.click(screen.getByRole("button", { name: "Сменить роль" }));

    expect(await screen.findByText("Роль не изменена.")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Каталог не подтвердил администратора: каталог недоступен — повторить позже.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("DRF-2273 — «Сменить роль» only where the server would accept", () => {
  it("is offered on an admin — the guards below are not always on", async () => {
    mockedRoster.mockResolvedValue(rosterOf(LENA_ADMIN));
    renderScreen();

    await waitFor(() => {
      expect(roleButton("Лена")).toBeInTheDocument();
    });
  });

  it("never on the owner, on yourself, on a master-only row or a revoked role", async () => {
    mockedRoster.mockResolvedValue(
      rosterOf(
        { ...LENA_ADMIN, id: "bot:u-1", bot_user_id: "u-1", name: "Сама" },
        {
          ...LENA_ADMIN,
          id: "bot:u-9",
          bot_user_id: "u-9",
          name: "Владелица",
          roles: [grant("owner", "active", "direct", daysAgo(300))],
        },
        { ...ANYA },
        {
          ...LENA_ADMIN,
          id: "bot:u-10",
          bot_user_id: "u-10",
          name: "Ушедшая",
          roles: [grant("admin", "revoked", "access_code", daysAgo(60))],
        },
      ),
    );
    renderScreen();

    // Presence first: the rows rendered, so the absences below mean
    // «no button», not «no screen».
    expect(await screen.findByText("Ушедшая")).toBeInTheDocument();
    expect(screen.getByText("Аня Ковалёва")).toBeInTheDocument();
    for (const name of ["Сама", "Владелица", "Аня Ковалёва", "Ушедшая"]) {
      expect(queryRoleButton(name)).not.toBeInTheDocument();
    }
  });
});

/**
 * DRF-2274 — «Вернуть доступ». Owner only; gives back the role the row
 * shows as revoked, never a new one.
 *
 * Pinned: the button appears only where the server has something to give
 * back (a revoked staff chip on a row with an account, or a master card
 * flagged `restorable_master`); the master card is named by `master_id`
 * alone — after the revoke it has no account; a single role is
 * preselected, several make the owner choose; refusals are words.
 */
const LENA_REVOKED: StaffRosterPerson = {
  ...LENA_ADMIN,
  is_active: false,
  roles: [grant("admin", "revoked", "access_code", daysAgo(40))],
  restorable_roles: ["admin"],
};

const MASTER_CARD_REVOKED: StaffRosterPerson = {
  id: "master:m-5",
  bot_user_id: null,
  master_id: "m-5",
  name: "Вера Лис",
  has_account: false,
  is_active: true,
  restorable_roles: ["master"],
  roles: [grant("master", "active", "master_invite", daysAgo(90))],
};

const restoreButton = (name: string) =>
  screen.getByRole("button", { name: `Вернуть доступ: ${name}` });

const queryRestoreButton = (name: string) =>
  screen.queryByRole("button", { name: `Вернуть доступ: ${name}` });

async function openRestore(person: StaffRosterPerson) {
  mockedRoster.mockResolvedValue(rosterOf(person));
  renderScreen();
  await waitFor(() => {
    expect(restoreButton(person.name)).toBeInTheDocument();
  });
  fireEvent.click(restoreButton(person.name));
  await screen.findByRole("dialog");
}

describe("DRF-2274 — the restore sheet", () => {
  beforeEach(() => {
    mockedRestore.mockResolvedValue({ changed: true, role: "admin" });
  });

  it("names the person and the one role coming back, and sends it by bot_user_id", async () => {
    await openRestore(LENA_REVOKED);

    expect(screen.getByRole("dialog")).toHaveAccessibleName(
      "Вернуть доступ: Лена",
    );
    expect(screen.getByText(/Вернём роль: Администратор\./)).toBeInTheDocument();
    expect(screen.queryAllByRole("radio")).toHaveLength(0);

    const readsBefore = mockedRoster.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Вернуть доступ" }));
    await waitFor(() => {
      expect(mockedRestore).toHaveBeenCalledTimes(1);
    });
    expect(mockedRestore.mock.calls.at(0)?.[0]).toEqual({
      role: "admin",
      bot_user_id: "u-8",
    });
    await waitFor(() => {
      expect(mockedRoster.mock.calls.length).toBe(readsBefore + 1);
    });
  });

  it("names a revoked master card by master_id alone", async () => {
    mockedRestore.mockResolvedValue({ changed: true, role: "master" });
    await openRestore(MASTER_CARD_REVOKED);

    expect(screen.getByText(/Вернём роль: Мастер\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Вернуть доступ" }));
    await waitFor(() => {
      expect(mockedRestore).toHaveBeenCalledTimes(1);
    });
    expect(mockedRestore.mock.calls.at(0)?.[0]).toEqual({
      role: "master",
      master_id: "m-5",
    });
  });

  it("makes the owner choose when two roles were revoked", async () => {
    await openRestore({
      ...LENA_REVOKED,
      roles: [
        grant("admin", "revoked", "access_code", daysAgo(40)),
        grant("receptionist", "revoked", "access_code", daysAgo(20)),
      ],
      restorable_roles: ["admin", "receptionist"],
    });

    const confirm = screen.getByRole("button", { name: "Вернуть доступ" });
    expect(confirm).toBeDisabled();
    expect(screen.getAllByRole("radio").map((o) => o.textContent)).toEqual([
      "Администратор",
      "Ресепшен",
    ]);
    fireEvent.click(screen.getByRole("radio", { name: "Ресепшен" }));
    fireEvent.click(confirm);
    await waitFor(() => {
      expect(mockedRestore).toHaveBeenCalledTimes(1);
    });
    expect(mockedRestore.mock.calls.at(0)?.[0]).toEqual({
      role: "receptionist",
      bot_user_id: "u-8",
    });
  });

  it("says a person who is gone in words, and keeps the sheet", async () => {
    mockedRestore.mockRejectedValue(
      new ApiError(409, "person_gone", "no account"),
    );
    await openRestore(MASTER_CARD_REVOKED);

    fireEvent.click(screen.getByRole("button", { name: "Вернуть доступ" }));

    expect(await screen.findByText("Доступ не возвращён.")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Аккаунта этого человека больше нет — пригласите его заново на экране «Команда».",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("DRF-2274 — «Вернуть доступ» only where there is something to give back", () => {
  it("is offered on a revoked admin and on a flagged master card", async () => {
    mockedRoster.mockResolvedValue(rosterOf(LENA_REVOKED, MASTER_CARD_REVOKED));
    renderScreen();

    await waitFor(() => {
      expect(restoreButton("Лена")).toBeInTheDocument();
    });
    expect(restoreButton("Вера Лис")).toBeInTheDocument();
  });

  it("never on a live role, an unflagged card, yourself or a revoked owner", async () => {
    mockedRoster.mockResolvedValue(
      rosterOf(
        LENA_ADMIN,
        { ...MASTER_CARD_REVOKED, id: "master:m-6", master_id: "m-6", name: "Нина", restorable_roles: [] },
        { ...LENA_REVOKED, id: "bot:u-1", bot_user_id: "u-1", name: "Сама" },
        {
          ...LENA_REVOKED,
          id: "bot:u-9",
          bot_user_id: "u-9",
          name: "Бывшая владелица",
          roles: [grant("owner", "revoked", "direct", daysAgo(300))],
          restorable_roles: [],
        },
        {
          // A role change closes the old row and it reads as «revoked» —
          // but no revoke took it, and the server says so.
          ...LENA_ADMIN,
          id: "bot:u-12",
          bot_user_id: "u-12",
          name: "Сменила роль",
          roles: [
            grant("admin", "revoked", "access_code", daysAgo(40)),
            grant("receptionist", "active", "direct", daysAgo(1)),
          ],
          restorable_roles: [],
        },
      ),
    );
    renderScreen();

    // Presence first: the rows rendered, so the absences mean «no button».
    expect(await screen.findByText("Бывшая владелица")).toBeInTheDocument();
    expect(screen.getByText("Нина")).toBeInTheDocument();
    for (const name of ["Лена", "Нина", "Сама", "Бывшая владелица", "Сменила роль"]) {
      expect(queryRestoreButton(name)).not.toBeInTheDocument();
    }
  });
});
