/**
 * Tests for `CustomerCardsScreen` — the C7.2 cards skeleton. The seam
 * (`lib/cards.ts`) is mocked: empty → honest empty state + disabled
 * bind button; with cards → brand + last4 list.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/cards", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/cards")>();
  return { ...original, getSavedCards: vi.fn() };
});

import { getSavedCards } from "../lib/cards";
import { CustomerCardsScreen } from "./CustomerCardsScreen";

const mockedCards = vi.mocked(getSavedCards);

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/cards"]}>
      <CustomerCardsScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CustomerCardsScreen", () => {
  it("shows the honest empty state and a disabled bind action", async () => {
    mockedCards.mockResolvedValue([]);
    renderScreen();
    expect(await screen.findByText(/Пока карт нет/)).toBeInTheDocument();
    const bind = screen.getByRole("button", { name: "Привязать карту" });
    expect(bind).toBeDisabled();
    expect(
      screen.getByText(/Привязка появится, когда подключим оплату онлайн/),
    ).toBeInTheDocument();
  });

  it("renders saved cards as brand + last4", async () => {
    mockedCards.mockResolvedValue([
      { id: "c-1", brand: "mir", last4: "4321" },
      { id: "c-2", brand: "visa", last4: "0005" },
    ]);
    renderScreen();
    expect(await screen.findByText("Мир")).toBeInTheDocument();
    expect(screen.getByText("·· 4321")).toBeInTheDocument();
    expect(screen.getByText("Visa")).toBeInTheDocument();
    expect(screen.getByText("·· 0005")).toBeInTheDocument();
  });
});
