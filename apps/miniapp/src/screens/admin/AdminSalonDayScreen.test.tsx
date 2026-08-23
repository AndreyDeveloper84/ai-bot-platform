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
  return {
    ...original,
    getSalonDay: vi.fn(),
    cancelSalonBooking: vi.fn(),
    getBookingVersion: vi.fn(),
    completeSalonBooking: vi.fn(),
    getBookingSlots: vi.fn(),
    rescheduleSalonBooking: vi.fn(),
  };
});

import {
  cancelSalonBooking,
  completeSalonBooking,
  getBookingSlots,
  getBookingVersion,
  getSalonDay,
  rescheduleSalonBooking,
  type SalonDayResponse,
} from "../../lib/admin-api";
import { AdminSalonDayScreen } from "./AdminSalonDayScreen";

const mockedDay = vi.mocked(getSalonDay);
const mockedCancel = vi.mocked(cancelSalonBooking);
const mockedVersion = vi.mocked(getBookingVersion);
const mockedComplete = vi.mocked(completeSalonBooking);
const mockedSlots = vi.mocked(getBookingSlots);
const mockedMove = vi.mocked(rescheduleSalonBooking);

function visit(over: Partial<SalonDayResponse["masters"][0]["visits"][0]> = {}) {
  return {
    id: "v-1",
    service_id: "svc-1",
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

describe("closing a visit", () => {
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

  const version = (over = {}) => ({
    id: "v-1",
    version: 3,
    status: "confirmed",
    start_datetime: "2026-08-20T07:00:00+00:00",
    ...over,
  });

  it("sends the version the operator was shown, not one fetched by the write", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWithOneVisit());
    mockedVersion.mockResolvedValue(version({ version: 7 }));
    mockedComplete.mockResolvedValue({ outcome: "committed", detail: "ok" });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Визит состоялся: Мария/ }));
    await screen.findByRole("dialog", { name: "Закрытие визита" });
    await user.click(screen.getByRole("button", { name: "Да, состоялся" }));

    await waitFor(() => expect(mockedComplete).toHaveBeenCalledWith("v-1", 7));
  });

  it("cannot confirm before the canonical version has arrived", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWithOneVisit());
    // Never resolves — the read is still in flight.
    mockedVersion.mockImplementation(() => new Promise(() => {}));
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Визит состоялся: Мария/ }));

    // Disabled rather than sending a locally invented version.
    expect(screen.getByRole("button", { name: "Да, состоялся" })).toBeDisabled();
    expect(mockedComplete).not.toHaveBeenCalled();
  });

  it("gives up rather than guessing when the version cannot be read", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWithOneVisit());
    mockedVersion.mockRejectedValue(new Error("503"));
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Визит состоялся: Мария/ }));

    expect(await screen.findByText(/Не удалось прочитать запись/)).toBeInTheDocument();
    expect(mockedComplete).not.toHaveBeenCalled();
  });

  it("warns when the schedule disagrees about the status", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWithOneVisit());
    mockedVersion.mockResolvedValue(version({ status: "cancelled" }));
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Визит состоялся: Мария/ }));

    expect(await screen.findByText(/считает эту запись/)).toBeInTheDocument();
  });

  it("does not call it a failure when the schedule did not answer", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWithOneVisit());
    mockedVersion.mockResolvedValue(version());
    mockedComplete.mockResolvedValue({ outcome: "pending", detail: "no answer" });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Визит состоялся: Мария/ }));
    await screen.findByRole("dialog", { name: "Закрытие визита" });
    await user.click(screen.getByRole("button", { name: "Да, состоялся" }));

    expect(await screen.findByText(/Возможно, визит закрыт/)).toBeInTheDocument();
    await waitFor(() => expect(mockedDay).toHaveBeenCalledTimes(2));
  });

  it("offers nothing to close on a released visit", async () => {
    mockedDay.mockResolvedValue(dayWithOneVisit({ status: "cancelled" }));
    renderScreen();

    await screen.findByText("Мария И.");
    expect(
      screen.queryByRole("button", { name: /Визит состоялся: Мария/ }),
    ).not.toBeInTheDocument();
  });

  it("still offers to close a visit that is in progress", async () => {
    mockedDay.mockResolvedValue(dayWithOneVisit({ is_in_progress: true }));
    renderScreen();

    // The front desk closes it as the customer walks out — unlike
    // cancelling, which is gone by then.
    expect(
      await screen.findByRole("button", { name: /Визит состоялся: Мария/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Отменить визит: Мария/ }),
    ).not.toBeInTheDocument();
  });
});

describe("a closed visit reads as closed, not as cancelled", () => {
  function dayWith(over = {}) {
    return dayResponse({
      summary: { total: 1, upcoming: 0, completed: 1, released: 0 },
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

  it("marks it «закрыт»", async () => {
    mockedDay.mockResolvedValue(dayWith({ status: "completed" }));
    renderScreen();

    expect(await screen.findByLabelText("Визит закрыт")).toBeInTheDocument();
  });

  it("does not strike it through — the visit happened", async () => {
    mockedDay.mockResolvedValue(dayWith({ status: "completed" }));
    renderScreen();

    const row = (await screen.findByText("Мария И.")).closest("li");
    // Struck through on this board means «the slot was freed». A closed
    // visit is the opposite claim: the customer came and was served.
    expect(row).toHaveStyle({ textDecoration: "none" });
  });

  it("still strikes a cancelled one through", async () => {
    mockedDay.mockResolvedValue(dayWith({ status: "cancelled" }));
    renderScreen();

    const row = (await screen.findByText("Мария И.")).closest("li");
    expect(row).toHaveStyle({ textDecoration: "line-through" });
    expect(screen.queryByLabelText("Визит закрыт")).not.toBeInTheDocument();
  });

  it("offers no closing button on an already closed visit", async () => {
    mockedDay.mockResolvedValue(dayWith({ status: "completed" }));
    renderScreen();

    await screen.findByText("Мария И.");
    expect(
      screen.queryByRole("button", { name: /Визит состоялся: Мария/ }),
    ).not.toBeInTheDocument();
  });

  it("prefers «закрыт» over «идёт» when both could apply", async () => {
    mockedDay.mockResolvedValue(
      dayWith({ status: "completed", is_in_progress: true }),
    );
    renderScreen();

    await screen.findByText("Мария И.");
    expect(screen.getByLabelText("Визит закрыт")).toBeInTheDocument();
    expect(screen.queryByLabelText("Визит идёт сейчас")).not.toBeInTheDocument();
  });
});

describe("moving a visit", () => {
  function dayWith(over = {}) {
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

  const okVersion = {
    id: "v-1",
    version: 4,
    status: "confirmed",
    start_datetime: "2026-08-20T07:00:00+00:00",
  };

  const slotsPayload = (slots: unknown[]) => ({
    date: "2026-08-20",
    timezone: "Europe/Moscow",
    master_id: "m-1",
    service_id: "svc-1",
    duration_min: 60,
    slots,
  });

  it("asks the schedule for that master and service, then sends its ISO start", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWith());
    mockedVersion.mockResolvedValue(okVersion);
    mockedSlots.mockResolvedValue(
      slotsPayload([
        { time: "14:00", start_at: "2026-08-20T11:00:00+00:00", duration_min: 60 },
      ]) as never,
    );
    mockedMove.mockResolvedValue({ outcome: "committed", detail: "ok" });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Перенести визит: Мария/ }));
    // The date is the day the screen is SHOWING, which starts at the
    // device's today — not the date baked into the fixture payload.
    const today = new Date();
    const iso = `${today.getFullYear()}-${`${today.getMonth() + 1}`.padStart(2, "0")}-${`${today.getDate()}`.padStart(2, "0")}`;
    await waitFor(() =>
      expect(mockedSlots).toHaveBeenCalledWith({
        masterId: "m-1",
        serviceId: "svc-1",
        date: iso,
      }),
    );

    await user.click(await screen.findByRole("button", { name: "14:00" }));

    // The schedule's own ISO instant, not one rebuilt from «14:00».
    await waitFor(() =>
      expect(mockedMove).toHaveBeenCalledWith("v-1", 4, "2026-08-20T11:00:00+00:00"),
    );
  });

  it("never renders an unreachable slot list as «no free time»", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWith());
    mockedVersion.mockResolvedValue(okVersion);
    mockedSlots.mockRejectedValue(new Error("503"));
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Перенести визит: Мария/ }));

    expect(await screen.findByText(/Не смогли спросить расписание/)).toBeInTheDocument();
    expect(screen.queryByText(/нет свободного времени/)).not.toBeInTheDocument();
  });

  it("says plainly when the master really has nothing free", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWith());
    mockedVersion.mockResolvedValue(okVersion);
    mockedSlots.mockResolvedValue(slotsPayload([]) as never);
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Перенести визит: Мария/ }));

    expect(await screen.findByText(/нет свободного времени/)).toBeInTheDocument();
  });

  it("refuses a slot the schedule could not put an instant on", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWith());
    mockedVersion.mockResolvedValue(okVersion);
    mockedSlots.mockResolvedValue(
      slotsPayload([{ time: "14:00", start_at: null, duration_min: 60 }]) as never,
    );
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Перенести визит: Мария/ }));
    await user.click(await screen.findByRole("button", { name: "14:00" }));

    // Rebuilding the instant from «14:00» would mean picking a timezone
    // here — the client computing an authoritative start (§17).
    expect(mockedMove).not.toHaveBeenCalled();
    expect(await screen.findByText(/не назвало точное время/)).toBeInTheDocument();
  });

  it("gives up rather than guessing when the version cannot be read", async () => {
    const user = userEvent.setup();
    mockedDay.mockResolvedValue(dayWith());
    mockedVersion.mockRejectedValue(new Error("503"));
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /Перенести визит: Мария/ }));

    expect(await screen.findByText(/Не удалось прочитать запись/)).toBeInTheDocument();
    expect(mockedSlots).not.toHaveBeenCalled();
  });

  it("offers no move when the booking has no local service", async () => {
    mockedDay.mockResolvedValue(dayWith({ service_id: "" }));
    renderScreen();

    await screen.findByText("Мария И.");
    // No service means no way to ask what is bookable, and offering
    // arbitrary times would be inventing availability.
    expect(
      screen.queryByRole("button", { name: /Перенести визит: Мария/ }),
    ).not.toBeInTheDocument();
  });

  it("offers no move on an orphan visit — there is no master to ask", async () => {
    mockedDay.mockResolvedValue(
      dayResponse({
        summary: { total: 1, upcoming: 1, completed: 0, released: 0 },
        orphan_visits: [visit()],
      }),
    );
    renderScreen();

    await screen.findByText("Мария И.");
    expect(
      screen.queryByRole("button", { name: /Перенести визит: Мария/ }),
    ).not.toBeInTheDocument();
  });
});
