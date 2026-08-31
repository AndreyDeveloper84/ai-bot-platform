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
import { render, screen, waitFor } from "@testing-library/react";
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
  return { ...original, getStaffRoster: vi.fn() };
});

import {
  getStaffRoster,
  type MeResponse,
  type RoleSource,
  type RoleState,
  type StaffRoleGrant,
  type StaffRosterPerson,
  type StaffRosterResponse,
} from "../../lib/admin-api";
import { AdminPeopleScreen } from "./AdminPeopleScreen";

const mockedRoster = vi.mocked(getStaffRoster);

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

describe("pending and revoked never share wording", () => {
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
