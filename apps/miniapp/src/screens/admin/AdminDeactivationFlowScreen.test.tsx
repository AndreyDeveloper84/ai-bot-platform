/**
 * DRF-1139 — Step 1 must not present a blind spot as an all-clear.
 *
 * The cascade can only reassign or cancel bookings it can see. On the
 * pilot it could see none of them (every BookingRequest row has
 * master_id NULL), so the screen said «нет будущих записей ✓ можно
 * деактивировать» while the salon had a live visit booked with that
 * master. These tests pin the three states apart: nothing anywhere,
 * something actionable, and something real the screen cannot touch.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../../lib/admin-api")>();
  return { ...original, previewDeactivation: vi.fn(), executeDeactivation: vi.fn() };
});

import {
  previewDeactivation,
  type DeactivationPreview,
  type DeactivationSummary,
  type MeResponse,
} from "../../lib/admin-api";
import { AdminDeactivationFlowScreen } from "./AdminDeactivationFlowScreen";

const mockedPreview = vi.mocked(previewDeactivation);

const OWNER_ME: MeResponse = {
  user: { id: "u-1", name: "Андрей", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula-tela" },
  role: "owner",
  capabilities: [],
  is_customer: true,
  is_master: true,
  is_receptionist: false,
  is_admin: false,
  is_owner: true,
  master_id: "m-1",
  landing_path: "/admin/team",
};

function makePreview(summary: Partial<DeactivationSummary>): DeactivationPreview {
  return {
    master: {
      id: "m-2",
      name: "Тихонова Ольга",
      is_active: true,
      archived_at: null,
    },
    future_bookings: [],
    summary: {
      total_future_bookings: 0,
      bookings_with_fallback: 0,
      bookings_without_fallback: 0,
      ...summary,
    },
  };
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/admin/team/m-2/deactivate"]}>
      <Routes>
        <Route
          path="/admin/team/:masterId/deactivate"
          element={<AdminDeactivationFlowScreen me={OWNER_ME} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("deactivation Step 1 — inventory integrity (DRF-1139)", () => {
  it("blocks the action when the mirror reports visits this screen cannot move", async () => {
    mockedPreview.mockResolvedValue(
      makePreview({ mirror_future_bookings: 1, inventory_complete: false }),
    );
    renderScreen();

    expect(await screen.findByText(/перенести нельзя/)).toBeInTheDocument();
    // The reassuring copy must be gone, not merely accompanied by a warning.
    expect(
      screen.queryByText(/Можно деактивировать без переноса/),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Продолжить/ })).toBeDisabled();
  });

  it("keeps the all-clear when nothing is booked anywhere", async () => {
    mockedPreview.mockResolvedValue(
      makePreview({ mirror_future_bookings: 0, inventory_complete: true }),
    );
    renderScreen();

    expect(
      await screen.findByText(/Можно деактивировать без переноса/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Продолжить/ })).toBeEnabled();
  });

  it("does not block when an older backend omits the field", async () => {
    // Absence means «unknown», not «incomplete» — degrading to a hard
    // block would break every deactivation against an older ship.
    mockedPreview.mockResolvedValue(makePreview({}));
    renderScreen();

    expect(
      await screen.findByText(/Можно деактивировать без переноса/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Продолжить/ })).toBeEnabled();
  });
});
