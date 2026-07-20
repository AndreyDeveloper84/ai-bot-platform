/**
 * Tests for `MasterBillingScreen` — the D7 card-binding funnel.
 *
 * API seam (`../lib/master-api`) and the payment-webview bridge helper
 * (`../lib/max-sdk.openPaymentConfirmation`) are mocked. Covers:
 * consent-gate, setup → webview, error surfaces (403/503/502), tariff
 * persistence, bound-card display after return.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getBillingStatus: vi.fn(),
    cardSetup: vi.fn(),
  };
});

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return {
    ...original,
    openPaymentConfirmation: vi.fn(),
    hapticNotify: vi.fn(),
    hapticSelection: vi.fn(),
    onBackButton: vi.fn(() => () => {}),
    setBackButton: vi.fn(),
  };
});

import { ApiError } from "../lib/api";
import { cardSetup, getBillingStatus } from "../lib/master-api";
import { openPaymentConfirmation } from "../lib/max-sdk";
import { MasterBillingScreen } from "./MasterBillingScreen";

const mockedStatus = vi.mocked(getBillingStatus);
const mockedSetup = vi.mocked(cardSetup);
const mockedOpen = vi.mocked(openPaymentConfirmation);

const STATUS_NO_CARD = {
  specialist_id: "s-1",
  subscription: {
    status: "none" as const,
    tariff: null,
    current_period_end: null,
    next_charge: null,
  },
  fees: { pending_total: "0.00", pending_count: 0 },
  last_invoice: null,
};

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/master/billing"]}>
      <MasterBillingScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("MasterBillingScreen", () => {
  it("gates the bind button behind the consent checkbox", async () => {
    mockedStatus.mockResolvedValue(STATUS_NO_CARD);
    const user = userEvent.setup();
    renderScreen();

    const button = await screen.findByRole("button", {
      name: "Привязать карту",
    });
    expect(button).toBeDisabled();

    await user.click(screen.getByRole("checkbox"));
    expect(button).toBeEnabled();
    expect(mockedSetup).not.toHaveBeenCalled();
  });

  it("calls cardSetup with the chosen tariff and opens the webview", async () => {
    mockedStatus.mockResolvedValue(STATUS_NO_CARD);
    mockedSetup.mockResolvedValue({
      confirmation_url: "https://yoomoney.ru/checkout/bind/1",
    });
    const user = userEvent.setup();
    renderScreen();

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("radio", { name: /Салон/ }));
    await user.click(screen.getByRole("button", { name: "Привязать карту" }));

    await waitFor(() => expect(mockedSetup).toHaveBeenCalledTimes(1));
    expect(mockedSetup).toHaveBeenCalledWith({
      tariff: "salon",
      // window.location.href in the test env (jsdom root).
      return_url: expect.stringContaining("http"),
    });
    await waitFor(() =>
      expect(mockedOpen).toHaveBeenCalledWith(
        "https://yoomoney.ru/checkout/bind/1",
      ),
    );
    // Tariff choice persists (asked once, remembered).
    expect(window.localStorage.getItem("master_billing_tariff")).toBe("salon");
  });

  it("defaults to the persisted tariff choice", async () => {
    window.localStorage.setItem("master_billing_tariff", "salon");
    mockedStatus.mockResolvedValue(STATUS_NO_CARD);
    renderScreen();

    const salon = await screen.findByRole("radio", { name: /Салон/ });
    expect(salon).toBeChecked();
  });

  it("surfaces 403 as an identity error", async () => {
    mockedStatus.mockResolvedValue(STATUS_NO_CARD);
    mockedSetup.mockRejectedValue(new ApiError(403, "forbidden", "nope"));
    const user = userEvent.setup();
    renderScreen();

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Привязать карту" }));

    expect(
      await screen.findByText(/сессия не совпадает с аккаунтом мастера/),
    ).toBeInTheDocument();
    expect(mockedOpen).not.toHaveBeenCalled();
  });

  it("surfaces 503 mapping gap as a support hint", async () => {
    mockedStatus.mockResolvedValue(STATUS_NO_CARD);
    mockedSetup.mockRejectedValue(
      new ApiError(503, "specialist_mapping_unavailable", "not synced"),
    );
    const user = userEvent.setup();
    renderScreen();

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Привязать карту" }));

    expect(
      await screen.findByText(/Связка с Ayla ещё не настроена/),
    ).toBeInTheDocument();
  });

  it("surfaces 502 as a retry-later message", async () => {
    mockedStatus.mockResolvedValue(STATUS_NO_CARD);
    mockedSetup.mockRejectedValue(
      new ApiError(502, "billing_upstream_unavailable", "http_503"),
    );
    const user = userEvent.setup();
    renderScreen();

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Привязать карту" }));

    expect(
      await screen.findByText(/временно недоступен/),
    ).toBeInTheDocument();
  });

  it("shows brand + last4 when the status carries a bound card", async () => {
    mockedStatus.mockResolvedValue({
      ...STATUS_NO_CARD,
      subscription: { ...STATUS_NO_CARD.subscription, status: "active" },
      card: { brand: "mir", last4: "4321" },
    });
    renderScreen();

    expect(await screen.findByText(/Карта привязана: mir •• 4321/))
      .toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Привязать карту" }),
    ).not.toBeInTheDocument();
  });

  it("shows «ожидает первого списания» right after binding on trial/none", async () => {
    mockedStatus.mockResolvedValue(STATUS_NO_CARD);
    mockedSetup.mockResolvedValue({
      confirmation_url: "https://yoomoney.ru/checkout/bind/2",
    });
    const user = userEvent.setup();
    renderScreen();

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Привязать карту" }));

    expect(
      await screen.findByText(/ожидает первого списания/),
    ).toBeInTheDocument();
  });
});
