/**
 * DRF-2268 — кнопки «вернуться в чат» на остальных экранах не молчат.
 *
 * #1961 перевёл Главную на `returnToChat`; здесь — прочие двери в чат:
 * «Вернуться в MAX» (отказ транспорта), «Вернуться в чат» после записи и
 * «Закрыть» на «Не тот получатель» в онбординге мастера. «Застрял» (нет ни
 * `close()`, ни ссылки на диалог) — подсказка рядом с кнопкой вместо тишины.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return {
    ...original,
    hapticNotify: vi.fn(),
    maxBridge: vi.fn(() => ({})),
    returnToChat: vi.fn(() => "closed"),
  };
});

import { CHAT_STUCK_HINT } from "../components/ReturnToChatHint";
import { OpenFromMaxScreen } from "../components/OpenFromMaxScreen";
import { OPEN_FROM_MAX_COPY } from "../lib/auth-error-copy";
import { returnToChat } from "../lib/max-sdk";
import { CustomerBookingSuccessScreen, RETURN_TO_CHAT_LABEL } from "./CustomerBookingSuccessScreen";
import { WrongRecipientScreen } from "./MasterOnboardingScreen";

const mockedReturn = vi.mocked(returnToChat);

beforeEach(() => {
  vi.clearAllMocks();
  mockedReturn.mockReturnValue("closed");
});

function inRouter(node: React.ReactElement, path = "/x") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={path} element={node} />
        <Route path="*" element={<p>другой экран</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("«Вернуться в MAX» на отказе транспорта", () => {
  it("застрял — подсказка", async () => {
    mockedReturn.mockReturnValue("stuck");
    inRouter(<OpenFromMaxScreen />);
    await userEvent.click(screen.getByRole("button", { name: OPEN_FROM_MAX_COPY.action }));
    expect(mockedReturn).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(CHAT_STUCK_HINT)).toBeInTheDocument();
  });

  it("закрылось — подсказки нет (положительная пара)", async () => {
    inRouter(<OpenFromMaxScreen />);
    await userEvent.click(screen.getByRole("button", { name: OPEN_FROM_MAX_COPY.action }));
    expect(mockedReturn).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(CHAT_STUCK_HINT)).toBeNull();
  });
});

describe("«Вернуться в чат» после записи", () => {
  it("застрял — подсказка", async () => {
    mockedReturn.mockReturnValue("stuck");
    inRouter(<CustomerBookingSuccessScreen />, "/customer/booking/success/:bookingId".replace(":bookingId", "b-1"));
    await userEvent.click(screen.getByRole("button", { name: RETURN_TO_CHAT_LABEL }));
    expect(mockedReturn).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(CHAT_STUCK_HINT)).toBeInTheDocument();
  });
});

describe("«Закрыть» на «Не тот получатель» (онбординг мастера)", () => {
  it("застрял — подсказка, а не тишина", async () => {
    mockedReturn.mockReturnValue("stuck");
    inRouter(<WrongRecipientScreen />);
    await userEvent.click(screen.getAllByRole("button").find((b) => /Закрыть/.test(b.textContent ?? ""))!);
    expect(mockedReturn).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(CHAT_STUCK_HINT)).toBeInTheDocument();
  });
});
