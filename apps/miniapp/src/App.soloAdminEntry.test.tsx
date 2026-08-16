/**
 * DRF-1149 safety net — the «Салон» entry in the solo «Ещё» sheet.
 *
 * The solo surface mounts every `/admin/*` route but surfaced none of
 * them, so the only way into the admin screens was typing a URL. When
 * the pilot salon was mis-counted as solo, that left an owner with four
 * masters stranded on a surface with no way out. The counting bug is
 * fixed in `is_solo_provider`; this item is the second lock on the same
 * door, and these tests are what keep it there.
 *
 * The sheet is opened by deep-linking to `/solo/more`, which
 * `UnifiedSoloSurface` reads on mount.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return { ...original, getMe: vi.fn() };
});

vi.mock("./lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/master-api")>();
  return { ...original, getDashboard: vi.fn() };
});

vi.mock("./lib/master-billing", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/master-billing")>();
  return { ...original, getPayoutPreview: vi.fn() };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import { getDashboard } from "./lib/master-api";
import { getPayoutPreview } from "./lib/master-billing";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDashboard = vi.mocked(getDashboard);
const mockedPayout = vi.mocked(getPayoutPreview);

/** Solo owner who is also her own master — the shape that renders the solo surface. */
const SOLO_OWNER_ME: MeResponse = {
  user: { id: "u-1", name: "Ольга", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Demo", slug: "demo" },
  role: "owner",
  capabilities: [],
  is_customer: true,
  is_master: true,
  is_receptionist: false,
  is_admin: false,
  is_owner: true,
  master_id: "m-1",
  landing_path: "/admin/team",
  is_solo_provider: true,
};

/** Same surface, no admin-side role — «Салон» must not appear. */
const SOLO_MASTER_ONLY_ME: MeResponse = {
  ...SOLO_OWNER_ME,
  role: "master",
  is_owner: false,
  is_admin: false,
  is_receptionist: false,
};

function renderAppAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedGetMe.mockResolvedValue(SOLO_OWNER_ME);
  // The dashboard behind the sheet is irrelevant here — reject so the
  // screen settles into its error state instead of hanging on a pending
  // promise. The sheet renders regardless.
  mockedDashboard.mockRejectedValue(new Error("not under test"));
  mockedPayout.mockRejectedValue(new Error("not under test"));
});

describe("solo surface — admin escape hatch (DRF-1149)", () => {
  it("shows «Салон» in the «Ещё» sheet for an owner", async () => {
    renderAppAt("/solo/more");
    expect(
      await screen.findByRole("button", { name: "Управление салоном" }),
    ).toBeInTheDocument();
  });

  it("keeps the Tau §3 base items alongside it", async () => {
    renderAppAt("/solo/more");
    await screen.findByRole("button", { name: "Управление салоном" });
    for (const label of ["Расписание", "Доходы", "Отзывы", "AI-помощник", "Профиль", "Настройки"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("hides «Салон» from a master-only caller", async () => {
    mockedGetMe.mockResolvedValue(SOLO_MASTER_ONLY_ME);
    renderAppAt("/solo/more");
    // Wait for the sheet to exist via a base item, then assert absence.
    await screen.findByRole("button", { name: "Расписание" });
    expect(
      screen.queryByRole("button", { name: "Управление салоном" }),
    ).not.toBeInTheDocument();
  });
});
