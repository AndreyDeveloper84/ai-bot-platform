/**
 * Tests for `PayoutPreviewCard` — the compact «К выплате» card on the
 * master dashboard (C3, phase 2b). Self-loading; renders only with
 * real pending data (a zero state and errors belong to the full
 * billing screen — the dashboard card hides both, dev-warned).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-billing", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-billing")>();
  return { ...original, getPayoutPreview: vi.fn() };
});

import { ApiError } from "../lib/api";
import { getPayoutPreview } from "../lib/master-billing";
import { PayoutPreviewCard } from "./PayoutPreviewCard";

const mockedPayout = vi.mocked(getPayoutPreview);

function renderCard() {
  render(
    <MemoryRouter initialEntries={["/master/dashboard"]}>
      <Routes>
        <Route path="/master/dashboard" element={<PayoutPreviewCard />} />
        <Route path="/master/billing" element={<div>BILLING-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PayoutPreviewCard", () => {
  it("renders pending amount + «ожидается» hint + link to the billing screen", async () => {
    const user = userEvent.setup();
    mockedPayout.mockResolvedValue({
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
      ],
    });
    renderCard();
    expect(await screen.findByText(/5 730 ₽/)).toBeInTheDocument();
    expect(screen.getByText(/Ожидается:/)).toBeInTheDocument();
    expect(screen.queryByText(/гарантирован/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Подробнее/ }));
    expect(await screen.findByText("BILLING-PROBE")).toBeInTheDocument();
  });

  it("hides on zero pending (the zero state lives on the billing screen)", async () => {
    mockedPayout.mockResolvedValue({
      pending_amount: "0.00",
      currency: "RUB",
      expected_settlement_hint: null,
      items: [],
    });
    const { container } = render(
      <MemoryRouter>
        <PayoutPreviewCard />
      </MemoryRouter>,
    );
    // Give the effect a tick to resolve, then the card must be absent.
    await new Promise((r) => setTimeout(r, 50));
    expect(container).toBeEmptyDOMElement();
  });

  it("hides on error — never fake numbers on the dashboard", async () => {
    mockedPayout.mockRejectedValue(
      new ApiError(503, "specialist_mapping_unavailable", "not synced"),
    );
    const { container } = render(
      <MemoryRouter>
        <PayoutPreviewCard />
      </MemoryRouter>,
    );
    await new Promise((r) => setTimeout(r, 50));
    expect(container).toBeEmptyDOMElement();
  });
});
