/**
 * Tests for `MasterBillingScreen` — C2 subscription status + C3 payout
 * breakdown (phase 2b). Data seam (`lib/master-billing.ts`) mocked;
 * fixtures are verbatim contract shapes (§3/§4).
 *
 * Locked UX:
 *   - statuses trial/active/past_due/canceled/none render distinctly;
 *   - AMD-013: next_charge shows as «Следующее списание {date}»;
 *     canceled → no next charge;
 *   - past_due: neutral debt block (the reason is visible ONLY to the
 *     master, C1 §2) — no shaming, no fake payoff button (the payoff
 *     endpoint does not exist; retry is automatic server-side);
 *   - C3: two item states explicitly — «Ожидает подтверждения после
 *     визита» (scheduled) / «Подтверждено, ожидает перечисления»
 *     (captured_pending_settlement); wording «ожидается», never
 *     «гарантированно».
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-billing", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-billing")>();
  return {
    ...original,
    getBillingStatus: vi.fn(),
    getPayoutPreview: vi.fn(),
    setupMasterCard: vi.fn(),
  };
});

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, openPaymentConfirmation: vi.fn() };
});

import { ApiError } from "../lib/api";
import {
  getBillingStatus,
  getPayoutPreview,
  setupMasterCard,
  type BillingStatus,
  type PayoutPreview,
} from "../lib/master-billing";
import { openPaymentConfirmation } from "../lib/max-sdk";
import { MasterBillingScreen } from "./MasterBillingScreen";

const mockedStatus = vi.mocked(getBillingStatus);
const mockedPayout = vi.mocked(getPayoutPreview);
const mockedSetup = vi.mocked(setupMasterCard);
const mockedOpen = vi.mocked(openPaymentConfirmation);

const STATUS_ACTIVE: BillingStatus = {
  specialist_id: "spec-1",
  subscription: {
    status: "active",
    tariff: "solo",
    current_period_end: "2026-08-31",
    next_charge: {
      subscription_amount: "690.00",
      fees_amount: "270.00",
      total_amount: "960.00",
      date: "2026-09-01",
    },
  },
  fees: { pending_total: "270.00", pending_count: 3 },
  last_invoice: {
    id: "inv-1",
    amount: "960.00",
    status: "paid",
    paid_at: "2026-07-01T10:00:00Z",
  },
};

const PAYOUT: PayoutPreview = {
  pending_amount: "5730.00",
  currency: "RUB",
  expected_settlement_hint: "~следующий рабочий день по расписанию ЮKassa",
  items: [
    {
      appointment_id: "a-1",
      completed_at: "2026-07-18T16:00:00Z",
      amount: "2000.00",
      platform_fee: "90.00",
      specialist_income: "1910.00",
      capture_state: "scheduled",
    },
    {
      appointment_id: "a-2",
      completed_at: "2026-07-17T12:00:00Z",
      amount: "3000.00",
      platform_fee: "90.00",
      specialist_income: "2910.00",
      capture_state: "captured_pending_settlement",
    },
  ],
};

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/master/billing"]}>
      <MasterBillingScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedStatus.mockResolvedValue(STATUS_ACTIVE);
  mockedPayout.mockResolvedValue(PAYOUT);
});

describe("MasterBillingScreen — subscription (C2)", () => {
  it("active: status, tariff, next charge with AMD-013 date + breakdown", async () => {
    renderScreen();
    expect(await screen.findByText("Активна")).toBeInTheDocument();
    expect(screen.getByText(/Соло/)).toBeInTheDocument();
    expect(screen.getByText(/Следующее списание/)).toBeInTheDocument();
    // 960 ₽ appears twice (next-charge total AND last invoice) — both real.
    expect(screen.getAllByText(/960 ₽/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/690 ₽/)).toBeInTheDocument();
    // 270 ₽ appears twice (next-charge fees AND pending fees) — both real.
    expect(screen.getAllByText(/270 ₽/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Последний инвойс/)).toBeInTheDocument();
  });

  it("none: honest no-subscription state, nulls never rendered raw", async () => {
    mockedStatus.mockResolvedValue({
      specialist_id: "spec-1",
      subscription: {
        status: "none",
        tariff: null,
        current_period_end: null,
        next_charge: null,
      },
      fees: { pending_total: "0.00", pending_count: 0 },
      last_invoice: null,
    });
    renderScreen();
    // Status label + explanatory caption both carry «Подписки нет».
    expect(
      (await screen.findAllByText(/Подписки нет/)).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("null")).not.toBeInTheDocument();
    expect(screen.queryByText(/Следующее списание/)).not.toBeInTheDocument();
  });

  it("past_due: neutral debt block — reason only for the master, no shame", async () => {
    mockedStatus.mockResolvedValue({
      ...STATUS_ACTIVE,
      subscription: { ...STATUS_ACTIVE.subscription, status: "past_due" },
    });
    renderScreen();
    expect(
      await screen.findByText(/По подписке есть задолженность/),
    ).toBeInTheDocument();
    expect(screen.getByText(/повтор/)).toBeInTheDocument();
    // No fake payoff button — the payoff endpoint does not exist.
    expect(
      screen.queryByRole("button", { name: "Оплатить долг" }),
    ).not.toBeInTheDocument();
  });

  it("canceled: status shown, no next charge (AMD-013)", async () => {
    mockedStatus.mockResolvedValue({
      ...STATUS_ACTIVE,
      subscription: {
        ...STATUS_ACTIVE.subscription,
        status: "canceled",
        next_charge: null,
      },
    });
    renderScreen();
    expect(await screen.findByText("Отменена")).toBeInTheDocument();
    expect(screen.queryByText(/Следующее списание/)).not.toBeInTheDocument();
  });

  it("trial renders as «Пробный период»", async () => {
    mockedStatus.mockResolvedValue({
      ...STATUS_ACTIVE,
      subscription: { ...STATUS_ACTIVE.subscription, status: "trial" },
    });
    renderScreen();
    expect(await screen.findByText("Пробный период")).toBeInTheDocument();
  });
});

describe("MasterBillingScreen — payout (C3)", () => {
  it("pending amount + «ожидается» hint, never «гарантированно»", async () => {
    renderScreen();
    expect(await screen.findByText(/5 730 ₽/)).toBeInTheDocument();
    expect(screen.getByText(/Ожидается:/)).toBeInTheDocument();
    expect(screen.queryByText(/гарантирован/i)).not.toBeInTheDocument();
  });

  it("breakdown shows both states explicitly", async () => {
    renderScreen();
    expect(
      await screen.findByText("Ожидает подтверждения после визита"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Подтверждено, ожидает перечисления"),
    ).toBeInTheDocument();
    expect(screen.getByText(/1 910 ₽/)).toBeInTheDocument();
    expect(screen.getByText(/2 910 ₽/)).toBeInTheDocument();
  });

  it("empty payout renders an honest zero state", async () => {
    mockedPayout.mockResolvedValue({
      pending_amount: "0.00",
      currency: "RUB",
      expected_settlement_hint: null,
      items: [],
    });
    renderScreen();
    expect(await screen.findByText(/Пока начислений нет/)).toBeInTheDocument();
  });
});

describe("MasterBillingScreen — honest errors", () => {
  it("503 specialist_mapping_unavailable → sync-pending state with retry", async () => {
    const user = userEvent.setup();
    mockedStatus.mockRejectedValue(
      new ApiError(503, "specialist_mapping_unavailable", "not synced yet"),
    );
    renderScreen();
    expect(
      await screen.findByText(/ещё синхронизируются/),
    ).toBeInTheDocument();
    mockedStatus.mockResolvedValue(STATUS_ACTIVE);
    await user.click(screen.getAllByRole("button", { name: "Обновить" })[0]!);
    expect(await screen.findByText("Активна")).toBeInTheDocument();
  });
});

describe("MasterBillingScreen — card binding (D7)", () => {
  it("consent checkbox gates the bind button", async () => {
    const user = userEvent.setup();
    renderScreen();
    await screen.findByText("Активна");
    const bind = screen.getByRole("button", { name: "Привязать карту" });
    expect(bind).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    expect(bind).toBeEnabled();
  });

  it("setup posts the C2 tariff and opens the binding webview", async () => {
    const user = userEvent.setup();
    mockedSetup.mockResolvedValue({
      confirmation_url: "https://pay.test/bind/1",
    });
    renderScreen();
    await screen.findByText("Активна");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Привязать карту" }));
    expect(mockedSetup).toHaveBeenCalledWith(
      expect.objectContaining({ tariff: "solo" }),
    );
    expect(mockedOpen).toHaveBeenCalledWith("https://pay.test/bind/1");
    expect(
      await screen.findByText(/Открыла страницу привязки/),
    ).toBeInTheDocument();
  });

  it("setup failure shows an honest error, no fake binding", async () => {
    const user = userEvent.setup();
    mockedSetup.mockRejectedValue(
      new ApiError(502, "billing_upstream_unavailable", "upstream down"),
    );
    renderScreen();
    await screen.findByText("Активна");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Привязать карту" }));
    expect(
      await screen.findByText(/Не получилось начать привязку/),
    ).toBeInTheDocument();
    expect(mockedOpen).not.toHaveBeenCalled();
  });
});
