/**
 * C05.7 «Вернуться в чат» (DRF-1777) — P0 actions DONE / RETURN_TO_CHAT.
 *
 * Внутри MAX — вторая кнопка закрывает мини-приложение и возвращает в
 * диалог; вне MAX кнопки нет (возвращаться некуда). «Открыть запись»
 * на месте в обоих случаях — положительная стража.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, hapticNotify: vi.fn(), maxBridge: vi.fn(() => null), closeApp: vi.fn() };
});

import { closeApp, maxBridge } from "../lib/max-sdk";
import { CustomerBookingSuccessScreen, RETURN_TO_CHAT_LABEL } from "./CustomerBookingSuccessScreen";

const mockedBridge = vi.mocked(maxBridge);
const mockedClose = vi.mocked(closeApp);

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/booking/success/b-1"]}>
      <Routes>
        <Route path="/customer/booking/success/:bookingId" element={<CustomerBookingSuccessScreen />} />
        <Route path="/customer/records/:bookingId" element={<div>RECORD-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("«Вернуться в чат» (DRF-1777)", () => {
  it("внутри MAX — кнопка есть и закрывает мини-приложение; «Открыть запись» на месте", async () => {
    mockedBridge.mockReturnValue({} as never);
    renderScreen();
    expect(screen.getByRole("button", { name: "Открыть запись" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: RETURN_TO_CHAT_LABEL }));
    expect(mockedClose).toHaveBeenCalledTimes(1);
  });

  it("вне MAX — кнопки нет, «Открыть запись» работает как прежде", async () => {
    mockedBridge.mockReturnValue(null);
    renderScreen();
    expect(screen.queryByRole("button", { name: RETURN_TO_CHAT_LABEL })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Открыть запись" }));
    expect(await screen.findByText("RECORD-PROBE")).toBeInTheDocument();
    expect(mockedClose).not.toHaveBeenCalled();
  });
});
