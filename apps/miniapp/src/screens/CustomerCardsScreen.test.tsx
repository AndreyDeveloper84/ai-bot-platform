/**
 * Tests for `CustomerCardsScreen` — live C7.2 flow: list, opt-in
 * consent gate before setup, webview bind, revoke with confirmation.
 * The passthrough seam (`lib/cards.ts`) and the webview opener are
 * mocked; fixtures match the bot passthrough shapes.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/cards", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/cards")>();
  return {
    ...original,
    getSavedCards: vi.fn(),
    setupCard: vi.fn(),
    deleteCard: vi.fn(),
  };
});

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, openPaymentConfirmation: vi.fn() };
});

import { deleteCard, getSavedCards, setupCard } from "../lib/cards";
import { openPaymentConfirmation } from "../lib/max-sdk";
import { CustomerCardsScreen } from "./CustomerCardsScreen";

const mockedList = vi.mocked(getSavedCards);
const mockedSetup = vi.mocked(setupCard);
const mockedDelete = vi.mocked(deleteCard);
const mockedOpen = vi.mocked(openPaymentConfirmation);

const TWO_CARDS = [
  { id: "c-1", brand: "mir", last4: "4321" },
  { id: "c-2", brand: "visa", last4: "0005" },
];

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

describe("CustomerCardsScreen (live C7.2)", () => {
  it("empty state: consent checkbox present, bind stays disabled until checked", async () => {
    const user = userEvent.setup();
    mockedList.mockResolvedValue([]);
    renderScreen();
    expect(await screen.findByText(/Пока карт нет/)).toBeInTheDocument();
    const bind = screen.getByRole("button", { name: "Привязать карту" });
    expect(bind).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    expect(bind).toBeEnabled();
  });

  it("consent-gated setup opens the binding webview (C7.2)", async () => {
    const user = userEvent.setup();
    mockedList.mockResolvedValue([]);
    mockedSetup.mockResolvedValue({
      confirmation_url: "https://yoomoney.ru/checkout/bind/xyz",
    });
    renderScreen();
    await screen.findByText(/Пока карт нет/);
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Привязать карту" }));
    expect(mockedSetup).toHaveBeenCalledTimes(1);
    expect(mockedOpen).toHaveBeenCalledWith(
      "https://yoomoney.ru/checkout/bind/xyz",
    );
    expect(
      await screen.findByText(/Открыла страницу привязки/),
    ).toBeInTheDocument();
  });

  it("no consent, no setup call — the button never fires unchecked", async () => {
    const user = userEvent.setup();
    mockedList.mockResolvedValue([]);
    renderScreen();
    await screen.findByText(/Пока карт нет/);
    await user.click(screen.getByRole("button", { name: "Привязать карту" }));
    expect(mockedSetup).not.toHaveBeenCalled();
  });

  it("setup failure shows an honest error with the list intact", async () => {
    const user = userEvent.setup();
    mockedList.mockResolvedValue(TWO_CARDS);
    mockedSetup.mockRejectedValue(new Error("[502] upstream_unavailable"));
    renderScreen();
    await screen.findByText("Мир");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Привязать карту" }));
    expect(
      await screen.findByText(/Не получилось начать привязку/),
    ).toBeInTheDocument();
    expect(screen.getByText("Мир")).toBeInTheDocument();
  });

  it("revoke flows through an explicit confirmation, then removes the card", async () => {
    const user = userEvent.setup();
    mockedList.mockResolvedValue(TWO_CARDS);
    mockedDelete.mockResolvedValue(undefined);
    renderScreen();
    const mir = await screen.findByText("Мир");
    const mirItem = mir.closest("li")!;
    await user.click(
      within(mirItem).getByRole("button", { name: /Отвязать/ }),
    );
    // Confirmation step — no delete call yet.
    expect(mockedDelete).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Да, отвязать" }),
    );
    expect(mockedDelete).toHaveBeenCalledWith("c-1");
    expect(await screen.findByText("Visa")).toBeInTheDocument();
    expect(screen.queryByText("Мир")).not.toBeInTheDocument();
    // The other card stays.
    expect(screen.getByText("·· 0005")).toBeInTheDocument();
  });

  it("revoke can be abandoned at the confirmation step", async () => {
    const user = userEvent.setup();
    mockedList.mockResolvedValue(TWO_CARDS);
    renderScreen();
    const mir = await screen.findByText("Мир");
    await user.click(
      within(mir.closest("li")!).getByRole("button", { name: /Отвязать/ }),
    );
    await user.click(screen.getByRole("button", { name: "Оставить" }));
    expect(mockedDelete).not.toHaveBeenCalled();
    expect(screen.getByText("Мир")).toBeInTheDocument();
  });
});
