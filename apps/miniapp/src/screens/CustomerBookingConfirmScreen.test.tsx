/**
 * Tests for `CustomerBookingConfirmScreen` — payment choice (C7.4 /
 * AMD-002) and the C1 neutral-unavailable branch.
 *
 * The screen reads the booking draft store (`state/booking`) — tests
 * seed it directly. MAX initData is mocked so the registered branch
 * renders (no OAuth gate). `createCustomerBooking` is mocked at the
 * lib seam; payloads are asserted verbatim (payment_required from the
 * user's choice — «Оплатить онлайн» / «Оплатить на месте»).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return {
    ...original,
    getInitData: () => "test-init-data",
    openPaymentConfirmation: vi.fn(),
  };
});

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    authVerify: vi.fn(),
  };
});

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return {
    ...original,
    createCustomerBooking: vi.fn(),
  };
});

vi.mock("../lib/payments", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/payments")>();
  return {
    ...original,
    createPayment: vi.fn(),
  };
});

import { ApiError, authVerify } from "../lib/api";
import { createCustomerBooking } from "../lib/customer-booking";
import { openPaymentConfirmation } from "../lib/max-sdk";
import { createPayment } from "../lib/payments";
import {
  resetBooking,
  setMaster,
  setService,
  setVisitAt,
} from "../state/booking";
import { CustomerBookingConfirmScreen } from "./CustomerBookingConfirmScreen";
import { CustomerBookingSuccessScreen } from "./CustomerBookingSuccessScreen";

const mockedAuthVerify = vi.mocked(authVerify);
const mockedCreate = vi.mocked(createCustomerBooking);
const mockedOpenPayment = vi.mocked(openPaymentConfirmation);
const mockedCreatePayment = vi.mocked(createPayment);

const CREATED = {
  booking: {
    id: "b-1",
    service_name: "Маникюр",
    master_name: "Анна Соколова",
    visit_at: "2026-08-01T16:00:00+03:00",
    duration_min: 60,
    status: "confirmed",
  },
};

function seedDraft() {
  resetBooking();
  setService("svc-1", "Маникюр");
  setMaster("mst-1", "Анна Соколова");
  setVisitAt("2026-08-01T16:00:00+03:00");
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/booking/confirm"]}>
      <Routes>
        <Route
          path="/customer/booking/confirm"
          element={<CustomerBookingConfirmScreen />}
        />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
        <Route
          path="/customer/booking/success/:bookingId"
          element={<CustomerBookingSuccessScreen />}
        />
        <Route
          path="/customer/masters/:masterId/slots"
          element={<div>SLOTS-PROBE</div>}
        />
        <Route path="/customer/records" element={<div>RECORDS-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedAuthVerify.mockResolvedValue({
    user: { id: "u-1", channel_user_id: "cu-1", display_name: "Ольга", client_name: "" },
    tenant: { slug: "demo", name: "Demo", timezone: "Europe/Moscow" },
    pending_booking_intent: null,
  });
  mockedCreate.mockResolvedValue(CREATED);
  seedDraft();
});

describe("payment choice (C7.4 / AMD-002)", () => {
  it("offers both options with «Оплатить на месте» preselected", () => {
    renderScreen();
    const onsite = screen.getByRole("radio", { name: /Оплатить на месте/ });
    const online = screen.getByRole("radio", { name: /Оплатить онлайн/ });
    expect(onsite).toBeChecked();
    expect(online).not.toBeChecked();
  });

  it("onsite choice creates the booking with payment_required=false", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(screen.getByRole("button", { name: "Записаться" }));
    expect(mockedCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        service_id: "svc-1",
        master_id: "mst-1",
        visit_at: "2026-08-01T16:00:00+03:00",
        payment_required: false,
      }),
    );
  });

  it("online choice creates the booking with payment_required=true", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(screen.getByRole("radio", { name: /Оплатить онлайн/ }));
    await user.click(screen.getByRole("button", { name: "Записаться" }));
    expect(mockedCreate).toHaveBeenCalledWith(
      expect.objectContaining({ payment_required: true }),
    );
  });

  it("online copy states the reserve honestly (no «24h to complain» promises)", () => {
    renderScreen();
    expect(screen.getByText(/зарезервирована/i)).toBeInTheDocument();
    expect(screen.queryByText(/24 часа/)).not.toBeInTheDocument();
  });

  it("online: creates the payment for the booking and opens the webview (C7.4)", async () => {
    const user = userEvent.setup();
    mockedCreatePayment.mockResolvedValue({
      payment_id: "pay-1",
      confirmation_url: "https://yoomoney.ru/checkout/pay/123",
      amount: "2000.00",
      capture_state: "authorized",
      currency: "RUB",
    });
    renderScreen();
    await user.click(screen.getByRole("radio", { name: /Оплатить онлайн/ }));
    await user.click(screen.getByRole("button", { name: "Записаться" }));
    expect(mockedCreatePayment).toHaveBeenCalledWith("b-1");
    expect(mockedOpenPayment).toHaveBeenCalledWith(
      "https://yoomoney.ru/checkout/pay/123",
    );
  });

  it("onsite: never calls the payment endpoint", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(screen.getByRole("button", { name: "Записаться" }));
    expect(mockedCreatePayment).not.toHaveBeenCalled();
  });

  it("payment create failure after booking: booking preserved, honest note on success", async () => {
    const user = userEvent.setup();
    mockedCreatePayment.mockRejectedValue(
      new ApiError(502, "upstream_unavailable", "payments upstream is temporarily unavailable"),
    );
    renderScreen();
    await user.click(screen.getByRole("radio", { name: /Оплатить онлайн/ }));
    await user.click(screen.getByRole("button", { name: "Записаться" }));
    // The booking EXISTS — the flow must land on success, not on an
    // error screen (a duplicate-create retry is the real risk here).
    expect(
      await screen.findByText(/Записала тебя/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Начать онлайн-оплату не получилось/),
    ).toBeInTheDocument();
    expect(mockedOpenPayment).not.toHaveBeenCalled();
  });
});

describe("C1 neutral unavailable message (contract §2)", () => {
  it("shows the neutral text + alternatives, never the debt reason", async () => {
    const user = userEvent.setup();
    mockedCreate.mockRejectedValue(
      new ApiError(409, "unavailable", "SUBSCRIPTION_PAST_DUE: specialist has debt"),
    );
    renderScreen();
    await user.click(screen.getByRole("button", { name: "Записаться" }));
    expect(
      await screen.findByText(/Сейчас запись к этому специалисту недоступна/),
    ).toBeInTheDocument();
    // Contract §2 privacy: the debt reason NEVER reaches the customer UI.
    expect(screen.queryByText(/долг|задолжен|past_due/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/SUBSCRIPTION_PAST_DUE/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Выбрать другое время" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Посмотреть других мастеров" }),
    ).toBeInTheDocument();
  });

  it("keeps the slot-race 409 copy distinct from the C1 neutral message", async () => {
    const user = userEvent.setup();
    mockedCreate.mockRejectedValue(
      new ApiError(409, "slot_unavailable", "slot already taken"),
    );
    renderScreen();
    await user.click(screen.getByRole("button", { name: "Записаться" }));
    expect(
      await screen.findByText(/Это время только что заняли/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Сейчас запись к этому специалисту недоступна/),
    ).not.toBeInTheDocument();
  });
});

describe("error matrix + idempotency (Wave 0 booking GO)", () => {
  it("not-bookable 404 → neutral message + catalog alternative", async () => {
    const user = userEvent.setup();
    mockedCreate.mockRejectedValue(
      new ApiError(404, "master_not_bookable", "master not found or not bookable"),
    );
    renderScreen();
    await user.click(screen.getByRole("button", { name: "Записаться" }));
    expect(
      await screen.findByText(/Эта услуга или специалист сейчас недоступны/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Посмотреть других мастеров" }),
    ).toBeInTheDocument();
    // Raw backend slug never renders.
    expect(screen.queryByText(/not_bookable/)).not.toBeInTheDocument();
  });

  it("double-tap on «Записаться» creates the booking exactly once", async () => {
    const user = userEvent.setup();
    let resolveCreate: ((v: typeof CREATED) => void) | undefined;
    mockedCreate.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );
    renderScreen();
    const cta = screen.getByRole("button", { name: "Записаться" });
    await user.click(cta);
    await user.click(cta);
    expect(mockedCreate).toHaveBeenCalledTimes(1);
    resolveCreate!(CREATED);
  });
});

describe("DRF-1066 — the success screen must actually appear", () => {
  it("lands on the success screen after a successful create", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(screen.getByRole("button", { name: "Записаться" }));
    expect(await screen.findByText(/Записала тебя/)).toBeInTheDocument();
    expect(screen.queryByText("CATALOG-PROBE")).not.toBeInTheDocument();
  });
});


/**
 * DRF-1066 — what the screen says when it does not know the outcome.
 *
 * The 14.08 double booking was not a double tap. The customer tapped
 * once, the answer never came back, and nothing told her the booking
 * had happened — so she walked the funnel again and picked a different
 * service and a different master. Two confirmed appointments, two
 * masters, one person who did not know what she was booked for.
 *
 * A 5xx and a dropped connection are the only two branches where the
 * booking may exist anyway: the create can commit and the response
 * still die on the way back. Copy that asserts failure there is not
 * merely inaccurate, it prescribes the second booking.
 *
 * The negative claim below («never tells her to retry») is worthless
 * on its own — it would also pass if the screen rendered nothing at
 * all, or if every retry affordance in the file were deleted. So each
 * ambiguous case asserts the replacement copy is actually present, and
 * `keeps the retry affordance where the refusal is definite` stands
 * guard on the other side: a slot race provably did not book, and must
 * still offer another time.
 */
describe("DRF-1066 — ambiguous outcome never invites a second booking", () => {
  it.each([
    ["network drop", new TypeError("Failed to fetch")],
    ["server 5xx", new ApiError(503, "server_error", "boom")],
  ])("%s: says the outcome is unknown, not that it failed", async (_label, thrown) => {
    const user = userEvent.setup();
    mockedCreate.mockRejectedValueOnce(thrown);
    renderScreen();
    await user.click(screen.getByRole("button", { name: "Записаться" }));

    const alert = await screen.findByRole("alert");
    // Positive: the honest copy is on screen — outcome unknown, and
    // the chat confirmation is what will settle it.
    expect(alert).toHaveTextContent(/не знаю, прошла ли запись/i);
    expect(alert).toHaveTextContent(/подтверждение в чат/i);
    // Negative: no instruction to book again.
    expect(alert).not.toHaveTextContent(/попробу/i);
    expect(alert).not.toHaveTextContent(/снова/i);
  });

  it("offers the record list so she can check instead of rebooking", async () => {
    const user = userEvent.setup();
    mockedCreate.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    renderScreen();
    await user.click(screen.getByRole("button", { name: "Записаться" }));

    await user.click(await screen.findByRole("button", { name: "Мои записи" }));
    expect(await screen.findByText("RECORDS-PROBE")).toBeInTheDocument();
  });

  it("keeps the retry affordance where the refusal is definite", async () => {
    // The positive guard. A slot race is a real refusal — nothing was
    // booked — so this branch must keep sending her to pick another
    // time. Without this, deleting every retry path in the screen
    // would make the assertions above pass.
    const user = userEvent.setup();
    mockedCreate.mockRejectedValueOnce(
      new ApiError(409, "slot_unavailable", "taken"),
    );
    renderScreen();
    await user.click(screen.getByRole("button", { name: "Записаться" }));

    expect(await screen.findByText(/Это время только что заняли/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Выбрать другое время" }));
    expect(await screen.findByText("SLOTS-PROBE")).toBeInTheDocument();
  });
});
