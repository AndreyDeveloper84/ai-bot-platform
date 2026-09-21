/**
 * F3 — экран времени не обещает персонализации, которой нет (DRF-1319).
 *
 * Гарантия та же, форма новая. До DRF-2178 обещание жило в заголовке
 * блока «ближайших» («Похоже подойдёт» над первыми двумя слотами по
 * времени — утверждение «мы посмотрели на тебя», которого никто не
 * делал). Кадр 3 макета DRF-1320 блока не знает: есть полоса дней,
 * части суток и ОДНА пометка у самого времени.
 *
 * Обещание никуда не переехало — оно по-прежнему зависит только от
 * серверного признака `is_suggested`, которого бэкенд не шлёт. Поэтому
 * пометки не видно ни разу, и рационал спеки §5.1 «не имитировать
 * персонализацию там где её нет» держится теперь на ней.
 *
 * Стража парная (`negative_assert_guard`, DRF-1411): к отрицательным
 * проверкам приложены положительные на тех же данных — время на месте,
 * выбирается, CTA разблокируется.
 *
 * Тест умеет падать: нарисуйте пометку без серверного признака —
 * покраснеет первый случай; перестаньте её рисовать по признаку —
 * покраснеет третий.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, getInitData: () => "test-init-data" };
});

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCustomerSlots: vi.fn() };
});

import { getCustomerSlots } from "../lib/customer-booking";
import { resetBooking, setMaster, setService } from "../state/booking";
import { SUGGESTED_NOTE } from "../lib/booking-time";
import { CustomerSlotsScreen } from "./CustomerSlotsScreen";

const mockedSlots = vi.mocked(getCustomerSlots);

const DAY = "2026-09-10";
const SLOTS = [
  { date: DAY, start: `${DAY}T10:00:00+03:00` },
  { date: DAY, start: `${DAY}T11:30:00+03:00` },
  { date: DAY, start: `${DAY}T15:00:00+03:00` },
];

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/masters/mst-1/slots"]}>
      <Routes>
        <Route
          path="/customer/masters/:masterId/slots"
          element={<CustomerSlotsScreen />}
        />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
  setService("svc-1", "Маникюр");
  setMaster("mst-1", "Анна Соколова");
  mockedSlots.mockResolvedValue({ slots: SLOTS, dateFrom: DAY, dateTo: DAY });
});

describe("без сигнала персонализации экран ничего не обещает", () => {
  it("ни пометки «обычное время», ни прежних персонализирующих заголовков", async () => {
    renderScreen();
    await screen.findByText("День");
    expect(screen.queryByText(/обычное время/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Похоже подойдёт/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Подходит под тво/)).not.toBeInTheDocument();
  });

  it("положительная стража: время на месте, выбирается, CTA оживает", async () => {
    renderScreen();
    // Части суток и полоса дней — на месте, экран не опустел.
    expect(await screen.findByText("День")).toBeInTheDocument();
    expect(screen.getByText(/3 окна/)).toBeInTheDocument();
    const user = userEvent.setup();
    expect(screen.getByRole("button", { name: "Выбери слот" })).toBeDisabled();
    const [slot] = screen.getAllByRole("button", { name: /в 15:00/ });
    await user.click(slot!);
    expect(screen.getByRole("button", { name: "Дальше" })).toBeEnabled();
  });
});

describe("КОГДА бэкенд действительно пометил слот", () => {
  it("пометка макета появляется — у самого времени и строкой под группой", async () => {
    mockedSlots.mockResolvedValue({
      slots: [{ ...SLOTS[0], is_suggested: true }, SLOTS[1], SLOTS[2]] as typeof SLOTS,
      dateFrom: DAY,
      dateTo: DAY,
    });
    renderScreen();
    // Строка макета — дословно, и ровно одна.
    expect(await screen.findByText(SUGGESTED_NOTE)).toBeInTheDocument();
    // Помечено именно то время, которое пометил сервер, а не первое подряд.
    expect(screen.getByLabelText(/в 10:00, обычное время/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/в 11:30, обычное время/)).not.toBeInTheDocument();
  });
});
