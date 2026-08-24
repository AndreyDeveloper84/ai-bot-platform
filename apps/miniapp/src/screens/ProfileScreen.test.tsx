/**
 * DRF-1371 — the F4 profile screen must not collect contraindications.
 *
 * The screen used to carry a free-text field for contraindications under a
 * caption promising the value would be passed to the master. Nothing on the
 * master side ever read it, so the caption was untrue, and free-text health
 * data is a special category under 152-ФЗ ст. 10. Owner decision 2026-08-25:
 * the master must not see contraindications at all, so the input is gone.
 *
 * These tests assert the rendered DOM, not the source: no textarea, no
 * caption, and a Save that never sends the key. The source-level guard —
 * `apps/identity/tests/test_drf1371_allergies_removed.py` — covers the
 * strings not existing in the repo at all.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    fetchProfile: vi.fn(),
    updateProfile: vi.fn(),
    deleteAccount: vi.fn(),
  };
});

import { fetchProfile, updateProfile, type Profile } from "../lib/api";
import { ProfileScreen } from "./ProfileScreen";

const mockedFetch = vi.mocked(fetchProfile);
const mockedUpdate = vi.mocked(updateProfile);

const PROFILE: Profile = {
  bot_user_id: "u-1",
  display_name: "Мария",
  client_name: "Мария Иванова",
  phone_masked: "+7 *** **67",
  timezone: "Europe/Moscow",
  joined_at: "2026-05-01T10:00:00Z",
  preferences: {
    notify_reminders: true,
    notify_retention: false,
    notify_promo: false,
    notify_birthday: true,
    birthday_date: null,
  },
  favorites: { master_name: null, service_name: null },
};

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/me"]}>
      <ProfileScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedFetch.mockResolvedValue(PROFILE);
  mockedUpdate.mockResolvedValue(PROFILE);
});

describe("ProfileScreen — no contraindications collection (DRF-1371)", () => {
  it("renders no free-text contraindications field", async () => {
    renderScreen();
    await screen.findByLabelText("Имя");

    expect(screen.queryByText(/Аллергии/i)).toBeNull();
    expect(screen.queryByText(/противопоказани/i)).toBeNull();
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("does not promise anything is passed to the master", async () => {
    renderScreen();
    await screen.findByLabelText("Имя");

    expect(screen.queryByText(/Передадим мастеру/i)).toBeNull();
    expect(screen.queryByText(/для вашей безопасности/i)).toBeNull();
  });

  it("keeps the rest of F4 — birthday is still editable", async () => {
    renderScreen();
    await screen.findByLabelText("Имя");

    expect(screen.getByText("День рождения")).toBeTruthy();
  });

  it("never sends an allergies key on save", async () => {
    const user = userEvent.setup();
    renderScreen();
    await screen.findByLabelText("Имя");

    const birthday = document.querySelector<HTMLInputElement>('input[type="date"]');
    expect(birthday).not.toBeNull();
    await user.type(birthday!, "1990-05-17");

    await user.click(screen.getByRole("button", { name: /Сохранить/i }));

    await waitFor(() => expect(mockedUpdate).toHaveBeenCalled());
    for (const call of mockedUpdate.mock.calls) {
      const body = JSON.stringify(call[0]);
      expect(body).not.toContain("allergies");
    }
  });
});
