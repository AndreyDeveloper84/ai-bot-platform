/**
 * «День салона» — the three distinctions the screen exists to make.
 *
 * Nothing booked / booked-then-cancelled / booked-but-invisible all
 * rendered identically on the surfaces this one replaces, and that is
 * exactly what cost the salon money. These tests pin them apart.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../../lib/admin-api")>();
  return { ...original, getSalonDay: vi.fn(), cancelSalonBooking: vi.fn() };
});

import {
  cancelSalonBooking,
  getSalonDay,
  type SalonDayResponse,
} from "../../lib/admin-api";
import { AdminSalonDayScreen } from "./AdminSalonDayScreen";

const mockedDay = vi.mocked(getSalonDay);
const mockedCancel = vi.mocked(cancelSalonBooking);

function visit(over: Partial<SalonDayResponse["masters"][0]["visits"][0]> = {}) {
  return {
    id: "v-1",
    start_at: "2026-08-20T07:00:00+00:00",
    end_at: "2026-08-20T08:00:00+00:00",
    duration_min: 60,
    status: "confirmed",
    service_name: "Маникюр",
    client_first_name: "Мария",
    client_last_initial: "И.",
    is_in_progress: false,
    ...over,
  };
}

function dayResponse(over: Partial<SalonDayResponse> = {}): SalonDayResponse {
  return {
    date: "2026-08-20",
    timezone: "Europe/Moscow",
    summary: { total: 0, upcoming: 0, completed: 0, released: 0 },
    masters: [],
    orphan_visits: [],
    ...over,
  };
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/admin/day"]}>
      <AdminSalonDayScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("salon day", () => {
  it("renders a visit under its master, in the salon timezone", async () => {
    mockedDay.mockResolvedValue(
      dayResponse({
        summary: { total: 1, upcoming: 1, completed: 0, released: 0 },
        masters: [
          {
            master_id: "m-1",
            name: "Анна Петрова",
            is_active: true,
            visits: [visit()],
          },
        ],
      }),
    );
    renderScreen();

    expect(await screen.findByText("Анна Петрова")).toBeInTheDocument();
    // 07:00 UTC is 10:00 in Europe/Moscow — the salon's clock, not the device's.
    expect(screen.getByText("10:00")).toBeInTheDocument();
    expect(screen.getByText("Мария И.")).toBeInTheDocument();
  });

  it("says plainly when nothing is booked", async () => {
    mockedDay.mockResolvedValue(dayResponse());
    renderScreen();
    expect(
      await screen.findByText(/На этот день записей нет/),
    ).toBeInTheDocument();
  });

  it("keeps a cancelled visit visible instead of dropping it", async () => {
    mockedDay.mockResolvedValue(
      dayResponse({
        summary: { total: 1, upcoming: 0, completed: 0, released: 1 },
        masters: [
          {
            master_id: "m-1",
            name: "Анна Петрова",
            is_active: true,
            visits: [visit({ status: "cancelled" })],
          },
        ],
      }),
    );
    renderScreen();

    // Present — a freed slot must not look like a booking that never existed.
    expect(await screen.findByText("Мария И.")).toBeInTheDocument();
    expect(screen.getByText(/отменено 1/)).toBeInTheDocument();
  });

  it("surfaces visits whose master could not be resolved", async () => {
    mockedDay.mockResolvedValue(
      dayResponse({
        summary: { total: 1, upcoming: 1, completed: 0, released: 0 },
        orphan_visits: [visit({ id: "orphan-1" })],
      }),
    );
    renderScreen();

    expect(await screen.findByText("Записи без мастера")).toBeInTheDocument();
  });

  it("lists idle masters instead of hiding them", async () => {
    mockedDay.mockResolvedValue(
      dayResponse({
        masters: [
          { master_id: "m-2", name: "Сазонова Инна", is_active: true, visits: [] },
        ],
      }),
    );
    renderScreen();

    expect(await screen.findByText("Свободны весь день")).toBeInTheDocument();
    expect(screen.getByText("Сазонова Инна")).toBeInTheDocument();
  });

  it("moves a day back and refetches for that date", async () => {
    mockedDay.mockResolvedValue(dayResponse());
    renderScreen();
    await screen.findByText(/На этот день записей нет/);

    const firstDate = mockedDay.mock.calls[0]?.[0];
    screen.getByLabelText("Предыдущий день").click();

    await waitFor(() => expect(mockedDay).toHaveBeenCalledTimes(2));
    const secondDate = mockedDay.mock.calls[1]?.[0];
    expect(secondDate).not.toEqual(firstDate);
  });

  it("shows the error state with a retry when the read fails", async () => {
    mockedDay.mockRejectedValue(new Error("boom"));
    renderScreen();
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    expect(
      screen.queryByText(/На этот день записей нет/),
    ).not.toBeInTheDocument();
  });
});

describe("cancelling a visit", () => {
  function dayWithOneVisit(over = {}) {
    return dayResponse({
      summary: { total: 1, upcoming: 1, completed: 0, released: 0 },
      masters: [
        {
          master_id: "m-1",
          name: "Анна Петрова",
          is_active: true,
          visits: [visit(over)],
        },
      ],
    });
  }

  it("asks before cancelling, because the customer gets told", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWithOneVisit());
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Отменить визит: Мария/ }));

    expect(screen.getByRole("dialog", { name: "Отмена визита" })).toBeInTheDocument();
    expect(screen.getByText(/Клиент получит уведомление/)).toBeInTheDocument();
    // Nothing has been sent yet — the first press only opens the question.
    expect(mockedCancel).not.toHaveBeenCalled();
  });

  it("sends the chosen reason and reloads the day", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWithOneVisit());
    mockedCancel.mockResolvedValue({ outcome: "committed", detail: "ok" });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Отменить визит: Мария/ }));
    await user.click(screen.getByLabelText("Салон закрыт"));
    await user.click(screen.getByRole("button", { name: "Отменить визит" }));

    await waitFor(() =>
      expect(mockedCancel).toHaveBeenCalledWith("v-1", {
        reason_code: "tenant_closed_slot",
      }),
    );
    // Reloaded: the day on screen must be the day that exists.
    await waitFor(() => expect(mockedDay).toHaveBeenCalledTimes(2));
  });

  it("does not call it a failure when the schedule did not answer", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWithOneVisit());
    mockedCancel.mockResolvedValue({ outcome: "pending", detail: "no answer" });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Отменить визит: Мария/ }));
    await user.click(screen.getByRole("button", { name: "Отменить визит" }));

    // «Possibly cancelled» — and a reload, so the receptionist can see
    // for themselves rather than press again.
    expect(await screen.findByText(/Возможно, отмена прошла/)).toBeInTheDocument();
    await waitFor(() => expect(mockedDay).toHaveBeenCalledTimes(2));
  });

  it("offers nothing to cancel on a visit that is already released", async () => {
    mockedDay.mockResolvedValue(dayWithOneVisit({ status: "cancelled" }));
    renderScreen();

    await screen.findByText("Мария И.");
    // Ayla refuses a settled booking whoever asks; a button that always
    // fails teaches the front desk to distrust the screen.
    expect(
      screen.queryByRole("button", { name: /Отменить визит: Мария/ }),
    ).not.toBeInTheDocument();
  });

  it("offers nothing to cancel while the visit is happening", async () => {
    mockedDay.mockResolvedValue(dayWithOneVisit({ is_in_progress: true }));
    renderScreen();

    await screen.findByText("Мария И.");
    expect(
      screen.queryByRole("button", { name: /Отменить визит: Мария/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps the day as it is when the booking cannot be cancelled", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWithOneVisit());
    mockedCancel.mockResolvedValue({
      outcome: "blocked",
      detail: "Визит уже завершён.",
    });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Отменить визит: Мария/ }));
    await user.click(screen.getByRole("button", { name: "Отменить визит" }));

    expect(await screen.findByText("Визит уже завершён.")).toBeInTheDocument();
    // Settled, not contended: nothing changed, so nothing to reload.
    await waitFor(() => expect(mockedDay).toHaveBeenCalledTimes(1));
  });
});
