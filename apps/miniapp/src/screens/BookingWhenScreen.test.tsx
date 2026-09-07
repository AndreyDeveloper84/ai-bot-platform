/**
 * Tests for `BookingWhenScreen` — the slot picker hands the draft to the
 * payment-capable confirmation screen (`/customer/booking/confirm`), so
 * BOTH acceptance payment paths work on the service-first chain too
 * (Wave 0 booking GO).
 *
 * Экран из удаления исключён решением владельца §31 (Q-CLIENT-01,
 * вариант B) и канонизирован по `/customer/book/when`.
 *
 * Проба на `/book/confirm` — это утверждение про АДРЕС, а не про экран.
 * Экрана `BookingConfirmScreen` больше нет (DRF-1485), маршрут снят
 * вместе с ним, и монтировать сюда нечего. Проба остаётся, потому что
 * снятие маршрута само по себе не мешает написать сюда `navigate` снова:
 * без неё «ведём на канонический» зеленело бы и на переходе в
 * несуществующий адрес, где человек увидел бы catch-all `HelloScreen`.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchSlots: vi.fn() };
});

import { fetchSlots } from "../lib/api";
import {
  resetBooking,
  setMaster,
  setService,
  setVisitAt,
} from "../state/booking";
import { BookingWhenScreen } from "./BookingWhenScreen";

const mockedSlots = vi.mocked(fetchSlots);

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/book/when"]}>
      <Routes>
        <Route path="/customer/book/when" element={<BookingWhenScreen />} />
        <Route
          path="/customer/booking/confirm"
          element={<div>CONFIRM-PROBE</div>}
        />
        {/* Адрес прежнего поколения — снят из App.tsx вместе с экраном
            (DRF-1485). Здесь он смонтирован НАРОЧНО: только так «сюда не
            ведём» можно отличить от «ведём, но роутер промолчал». */}
        <Route
          path="/book/confirm"
          element={<div>OLD-ADDRESS-BOOK-CONFIRM</div>}
        />
        <Route path="/customer/catalog" element={<div>CATALOG-LIVE</div>} />
        <Route path="/catalog" element={<div>CATALOG-LEGACY</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
  setService("svc-1", "Маникюр");
  setMaster("mst-1", "Анна Соколова");
  mockedSlots.mockResolvedValue({
    slots: [{ date: "2026-08-01", start: "2026-08-01T16:00:00+03:00" }],
  });
});

describe("BookingWhenScreen — запасной выход (DRF-1485 §3)", () => {
  it("потерянный черновик уводит в /customer/catalog, не в /catalog", async () => {
    // §31 оставил этот экран жить навсегда, значит и его аварийный
    // выход обязан вести в живое поколение. До правки он вёл в
    // `/catalog` — экран прежнего, оставленный лишь алиасом.
    resetBooking();
    renderScreen();

    expect(await screen.findByText("CATALOG-LIVE")).toBeInTheDocument();
    expect(screen.queryByText("CATALOG-LEGACY")).not.toBeInTheDocument();
    // Редирект случился до загрузки слотов — экрана не было вовсе.
    expect(mockedSlots).not.toHaveBeenCalled();
  });
});

describe("BookingWhenScreen (Wave 0 flow unification)", () => {
  it("continues to the payment-capable confirm screen, not the old address", async () => {
    const user = userEvent.setup();
    renderScreen();
    setVisitAt("2026-08-01T16:00:00+03:00");
    await user.click(await screen.findByRole("button", { name: "Дальше" }));
    // Положительно: канонический адрес.
    expect(await screen.findByText("CONFIRM-PROBE")).toBeInTheDocument();
    // Отрицательно, на том же переходе: старый адрес не задет.
    expect(
      screen.queryByText("OLD-ADDRESS-BOOK-CONFIRM"),
    ).not.toBeInTheDocument();
  });
});
