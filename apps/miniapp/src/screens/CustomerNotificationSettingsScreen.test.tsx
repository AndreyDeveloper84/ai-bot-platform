/**
 * Tests for `CustomerNotificationSettingsScreen` — customer-facing
 * notification preferences (issue #948 / P-8). Replaces the deferred
 * support-route sheet: real `GET /me` + `PATCH /me` preference fields
 * (notify_reminders / notify_retention / notify_promo / notify_birthday)
 * via `lib/api.ts`. Transactional booking messages always arrive and
 * are stated as such.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    fetchProfile: vi.fn(),
    updateProfile: vi.fn(),
  };
});

import {
  fetchProfile,
  updateProfile,
  type Profile,
} from "../lib/api";
import { CustomerNotificationSettingsScreen } from "./CustomerNotificationSettingsScreen";

const mockedFetch = vi.mocked(fetchProfile);
const mockedUpdate = vi.mocked(updateProfile);

const PROFILE: Profile = {
  bot_user_id: "u-1",
  display_name: "Ольга",
  client_name: "Ольга",
  phone_masked: "+7 *** **12",
  timezone: "Europe/Moscow",
  joined_at: "2026-05-01T10:00:00Z",
  preferences: {
    notify_reminders: true,
    notify_retention: false,
    notify_promo: false,
    notify_birthday: true,
    birthday_date: null,
    allergies: "",
  },
  favorites: { master_name: null, service_name: null },
};

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/notification-settings"]}>
      <CustomerNotificationSettingsScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedFetch.mockResolvedValue(PROFILE);
  mockedUpdate.mockImplementation(async (patch) => ({
    ...PROFILE,
    preferences: { ...PROFILE.preferences, ...patch },
  }));
});

describe("CustomerNotificationSettingsScreen (#948)", () => {
  it("renders the four preference toggles with real values", async () => {
    renderScreen();
    expect(
      await screen.findByRole("switch", { name: /Напоминания о записях/ }),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByRole("switch", { name: /Возвращение к заботе/ }),
    ).toHaveAttribute("aria-checked", "false");
    expect(
      screen.getByRole("switch", { name: /Акции и предложения/ }),
    ).toHaveAttribute("aria-checked", "false");
    expect(
      screen.getByRole("switch", { name: /Поздравление с днём рождения/ }),
    ).toHaveAttribute("aria-checked", "true");
  });

  it("toggle PATCHes the preference and flips the switch", async () => {
    const user = userEvent.setup();
    renderScreen();
    const promo = await screen.findByRole("switch", {
      name: /Акции и предложения/,
    });
    await user.click(promo);
    expect(mockedUpdate).toHaveBeenCalledWith({ notify_promo: true });
    expect(promo).toHaveAttribute("aria-checked", "true");
  });

  it("save failure reverts the switch and shows an honest snackbar", async () => {
    const user = userEvent.setup();
    mockedUpdate.mockRejectedValueOnce(new Error("network down"));
    renderScreen();
    const promo = await screen.findByRole("switch", {
      name: /Акции и предложения/,
    });
    await user.click(promo);
    expect(await screen.findByText(/Не получилось сохранить/)).toBeInTheDocument();
    expect(promo).toHaveAttribute("aria-checked", "false");
  });

  it("states that transactional booking messages always arrive", async () => {
    renderScreen();
    expect(
      await screen.findByText(/подтверждения, переносы, отмены/),
    ).toBeInTheDocument();
    expect(screen.getByText(/приходят всегда/)).toBeInTheDocument();
  });

  it("shows an honest error with retry when the profile fails to load", async () => {
    const user = userEvent.setup();
    mockedFetch.mockRejectedValueOnce(new Error("network down"));
    renderScreen();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(
      await screen.findByRole("switch", { name: /Напоминания о записях/ }),
    ).toBeInTheDocument();
  });
});
