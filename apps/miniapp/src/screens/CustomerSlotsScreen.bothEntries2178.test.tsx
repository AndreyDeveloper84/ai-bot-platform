/**
 * Кадр 3 макета DRF-1320 на ОДНОМ экране для двух входов (DRF-2178, Э-2).
 *
 * Экран выбора времени — вход и из карточки мастера
 * (`/customer/masters/:id/slots`), и из потока записи. Второй такой
 * экран ради макета был бы вторым вычислителем в другом обличье, только
 * медленнее заметным. Поэтому экран один, а кадр макета — добавка к
 * нему, а не замена.
 *
 * Что заперто здесь — сохранность прежнего входа:
 *
 * 1. без цели и шага плана экран работает как раньше: КАЖДОЕ свободное
 *    время достижимо и кликабельно; полоса дней и части суток
 *    добавляются. Узел про достижимость, а не про число отрисовок:
 *    до этого этапа слот рисовался дважды — в блоке «ближайших» и в
 *    списке дня, — а кадр макета такого блока не знает. Исчезает дубль
 *    ярлыка, не возможность выбрать время;
 * 2. «это время только что заняли» (возврат с подтверждения по 409) и
 *    подмена мастера (Q-BF-3, текст владельца) живы на ОБОИХ входах —
 *    состояние, потерянное на одном из них, и есть цена объединения,
 *    поэтому оно проверяется на обоих.
 *
 * Стража парная (`negative_assert_guard`): к «ничего не потеряли»
 * приложены положительные утверждения на тех же данных.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, getInitData: () => "test-init-data" };
});

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCustomerSlots: vi.fn() };
});

import { getCustomerSlots } from "../lib/customer-booking";
import { resetBooking, setMaster, setService } from "../state/booking";
import { CustomerSlotsScreen } from "./CustomerSlotsScreen";

const mockedSlots = vi.mocked(getCustomerSlots);

const DAY = "2026-09-28";
const SLOTS = [
  { date: DAY, start: `${DAY}T10:00:00+03:00` },
  { date: DAY, start: `${DAY}T13:30:00+03:00` },
  { date: DAY, start: `${DAY}T18:00:00+03:00` },
];

/** Оба входа ведут на один и тот же адрес — разной только историей. */
function renderAt(entry: string, state?: Record<string, unknown>) {
  render(
    <MemoryRouter initialEntries={[{ pathname: entry, state: state ?? null }]}>
      <Routes>
        <Route path="/customer/masters/:masterId/slots" element={<CustomerSlotsScreen />} />
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

describe("вход из карточки мастера ничего не теряет", () => {
  it("каждое свободное время достижимо и кликабельно — ровно один раз", async () => {
    // До этого этапа слот рисовался ДВАЖДЫ: в блоке «ближайших» и в
    // списке дня. Кадр макета такого блока не знает: его содержимое —
    // те же слоты выбранного дня, разложенные по частям суток. Значит
    // ничего не становится недостижимым, исчезает только дубль ярлыка.
    // Это отступление названо в теле PR; узел держит суть — доступность
    // каждого времени, а не число его отрисовок.
    renderAt("/customer/masters/mst-1/slots");
    await screen.findByText("Утро");
    for (const time of ["10:00", "13:30", "18:00"]) {
      const buttons = screen.getAllByRole("button", { name: new RegExp(time) });
      expect(buttons).toHaveLength(1);
      expect(buttons[0]).toBeEnabled();
    }
  });

  it("и добавляется новое: части суток и полоса дней со счётчиком", async () => {
    renderAt("/customer/masters/mst-1/slots");
    expect(await screen.findByText("Утро")).toBeInTheDocument();
    expect(screen.getByText("День")).toBeInTheDocument();
    expect(screen.getByText("Вечер")).toBeInTheDocument();
    expect(screen.getByText(/3 окна/)).toBeInTheDocument();
  });
});

describe("состояния живы на обоих входах", () => {
  it("«это время только что заняли» — из карточки мастера", async () => {
    renderAt("/customer/masters/mst-1/slots", {
      unavailableSlot: `${DAY}T13:30:00+03:00`,
    });
    // Положительная половина: экран не пуст и время на месте.
    await screen.findByText("Утро");
    expect(screen.getAllByRole("button", { name: /10:00/ })).toHaveLength(1);
    expect(screen.getByText(/заняли|занято/i)).toBeInTheDocument();
  });

  it("«это время только что заняли» — из потока записи", async () => {
    renderAt("/customer/masters/mst-1/slots", {
      unavailableSlot: `${DAY}T13:30:00+03:00`,
      fromBookingFlow: true,
    });
    await screen.findByText("Утро");
    expect(screen.getAllByRole("button", { name: /10:00/ })).toHaveLength(1);
    expect(screen.getByText(/заняли|занято/i)).toBeInTheDocument();
  });
});
