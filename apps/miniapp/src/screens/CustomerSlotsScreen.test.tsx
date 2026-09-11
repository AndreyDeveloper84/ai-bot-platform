/**
 * F3 — заголовок блока подсказок не обещает персонализации, которой нет.
 *
 * До правки `detectCustomerMode()` возвращал «registered» любому, у кого
 * есть `initData` от MAX, и экран рисовал «✨ Похоже подойдёт» над
 * ПЕРВЫМИ ДВУМЯ СЛОТАМИ ПО ВРЕМЕНИ. Сохранённого предпочтения по времени
 * бэкенд не отдаёт вовсе (`is_suggested` не приходит), то есть заголовок
 * утверждал «мы посмотрели на тебя» там, где никто не смотрел.
 *
 * Рационал спеки `docs/screens/customer-booking-flow.md` §5.1 сказан
 * ровно про это: «не имитировать персонализацию там где её нет».
 *
 * Стража парная (`negative_assert_guard`, DRF-1411): к отрицательной
 * проверке «персонализирующего заголовка нет» приложены положительные на
 * тех же данных — блок ближайших слотов на месте, «Все слоты» на месте,
 * и по слоту по-прежнему можно кликнуть. Правка, которая снесла бы блок
 * целиком, прошла бы отрицательную проверку и упала на положительных.
 *
 * Тест умеет падать: верните `suggestionsHeader(mode)` без параметра
 * `personalised` — покраснеет первый случай; уберите `personalised` из
 * ветки с серверной пометкой — покраснеет третий.
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
  mockedSlots.mockResolvedValue({ slots: SLOTS });
});

describe("подсказки слотов без сигнала персонализации", () => {
  it("не обещает «твоё обычное время» / «похоже подойдёт»", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: /Ближайшие свободные/ });
    expect(screen.queryByText(/обычное время/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Похоже подойдёт/)).not.toBeInTheDocument();
  });

  it("положительная стража: блок и остальной экран на месте", async () => {
    renderScreen();
    // Сам блок ближайших слотов остался — сняли обещание, не блок.
    expect(
      await screen.findByRole("heading", { name: /Ближайшие свободные/ }),
    ).toBeInTheDocument();
    // «Все слоты» и сетка дня на месте.
    expect(
      screen.getByRole("heading", { name: "Все слоты" }),
    ).toBeInTheDocument();
    // Слот по-прежнему выбирается, и CTA разблокируется.
    const user = userEvent.setup();
    expect(screen.getByRole("button", { name: "Выбери слот" })).toBeDisabled();
    const [slot] = screen.getAllByRole("button", { name: /в 15:00/ });
    await user.click(slot!);
    expect(screen.getByRole("button", { name: "Дальше" })).toBeEnabled();
  });
});

describe("подсказки слотов КОГДА бэкенд действительно пометил слоты", () => {
  it("персонализирующий заголовок возвращается по серверной пометке", async () => {
    mockedSlots.mockResolvedValue({
      slots: [
        { ...SLOTS[0], is_suggested: true },
        SLOTS[1],
        SLOTS[2],
      ] as typeof SLOTS,
    });
    renderScreen();
    expect(
      await screen.findByRole("heading", { name: /Похоже подойдёт/ }),
    ).toBeInTheDocument();
    // И в блок попадает именно помеченный слот, а не первые два подряд.
    expect(screen.getByLabelText(/в 10:00, обычное время/)).toBeInTheDocument();
  });
});
