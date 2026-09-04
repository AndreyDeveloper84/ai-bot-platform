/**
 * Куда уходит человек сразу после оценки (DRF-1480).
 *
 * «К моим записям» на экране благодарности вело в старое `/my-visits`.
 * Человек, только что поставивший оценку, оказывался в старом поколении
 * экранов — там, где его же оценка и не показывается. Канон один:
 * `/customer/records`.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, submitFeedback: vi.fn() };
});

import { submitFeedback } from "../lib/api";
import { FeedbackScreen } from "./FeedbackScreen";

const mockedSubmit = vi.mocked(submitFeedback);

// FeedbackScreen проверяет форму id и на кривой ссылке не рисует форму.
const BOOKING_ID = "3f1c2a44-5b6d-4e7f-8a9b-0c1d2e3f4a5b";

function renderScreen() {
  render(
    <MemoryRouter initialEntries={[`/feedback/${BOOKING_ID}`]}>
      <Routes>
        <Route path="/feedback/:bookingId" element={<FeedbackScreen />} />
        <Route path="/customer/records" element={<div>НОВЫЕ ЗАПИСИ</div>} />
        <Route path="/my-visits" element={<div>СТАРЫЕ ЗАПИСИ</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedSubmit.mockResolvedValue({
    booking_id: BOOKING_ID,
    rating: 5,
    comment: "",
    feedback_at: "2026-09-04T10:00:00Z",
    handoff_created: false,
    task_id: null,
  });
});

describe("закрытие после оценки остаётся в новом поколении (DRF-1480)", () => {
  it("«К моим записям» ведёт в /customer/records, а не в /my-visits", async () => {
    renderScreen();

    await userEvent.click(screen.getByRole("radio", { name: "5 из 5" }));
    await userEvent.click(screen.getByRole("button", { name: "Отправить" }));

    // Присутствие: оценка ушла и экран благодарности показан.
    expect(await screen.findByText("Спасибо за оценку!")).toBeInTheDocument();
    expect(mockedSubmit).toHaveBeenCalledWith(BOOKING_ID, { rating: 5, comment: "" });

    await userEvent.click(screen.getByRole("button", { name: "К моим записям" }));

    expect(screen.getByText("НОВЫЕ ЗАПИСИ")).toBeInTheDocument();
    expect(screen.queryByText("СТАРЫЕ ЗАПИСИ")).toBeNull();
  });
});
