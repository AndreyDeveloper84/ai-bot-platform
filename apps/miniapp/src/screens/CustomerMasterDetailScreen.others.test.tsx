/**
 * C05.3 ProviderCard (DRF-1778): «Другие специалисты» с карточки и число
 * отзывов только из данных.
 *
 * Тело C05: ProviderCard несёт «существующие trust signals … CTA;
 * «Другие специалисты»»; всё — «только из фактических данных». Макет
 * кадра 6 «★4.9 (108 отзывов)» — скобки рисуются ровно когда число
 * известно и больше нуля.
 *
 * Стражи:
 * 1. с известной услугой «Другие специалисты» ведёт к выбору мастера под
 *    неё; без услуги — в каталог;
 * 2. отзывы: 108 → «(108 отзывов)», 1 → «отзыв», 3 → «отзыва»;
 *    0 / отсутствие → скобок нет (положительная стража — рейтинг на месте).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCustomerMaster: vi.fn() };
});

import { getCustomerMaster } from "../lib/customer-booking";
import { reviewCountLabel } from "../lib/rating";
import { resetBooking, setService } from "../state/booking";
import { CustomerMasterDetailScreen, OTHER_MASTERS_LABEL } from "./CustomerMasterDetailScreen";

const mockedMaster = vi.mocked(getCustomerMaster);

function master(extra: Record<string, unknown> = {}) {
  return {
    master: {
      id: "m-1",
      name: "Мария П.",
      specialization: "массаж",
      bio: "",
      experience: "7 лет",
      rating: "4.9",
      photo_url: "",
      service_ids: ["svc-1"],
      ...extra,
    },
  };
}

function renderScreen(search = "") {
  render(
    <MemoryRouter initialEntries={[`/customer/masters/m-1${search}`]}>
      <Routes>
        <Route path="/customer/masters/:masterId" element={<CustomerMasterDetailScreen />} />
        <Route path="/customer/book/master" element={<div>MASTER-PICKER-PROBE</div>} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
});

describe("число отзывов — только из данных", () => {
  it("склонение", () => {
    expect(reviewCountLabel(108)).toBe("108 отзывов");
    expect(reviewCountLabel(1)).toBe("1 отзыв");
    expect(reviewCountLabel(3)).toBe("3 отзыва");
    expect(reviewCountLabel(11)).toBe("11 отзывов");
    expect(reviewCountLabel(21)).toBe("21 отзыв");
    expect(reviewCountLabel(0)).toBe("");
    expect(reviewCountLabel(null)).toBe("");
    expect(reviewCountLabel(undefined)).toBe("");
  });

  it("108 отзывов рядом с рейтингом", async () => {
    mockedMaster.mockResolvedValue(master({ review_count: 108 }) as never);
    renderScreen();
    expect(await screen.findByTestId("master-reviews")).toHaveTextContent("(108 отзывов)");
    expect(screen.getByLabelText("Рейтинг 4.9")).toBeInTheDocument();
  });

  it("0 или отсутствие — скобок нет, рейтинг на месте", async () => {
    mockedMaster.mockResolvedValue(master({ review_count: 0 }) as never);
    renderScreen();
    expect(await screen.findByLabelText("Рейтинг 4.9")).toBeInTheDocument();
    expect(screen.queryByTestId("master-reviews")).toBeNull();
  });
});

describe("«Другие специалисты» с карточки", () => {
  it("с известной услугой — к выбору мастера под неё", async () => {
    mockedMaster.mockResolvedValue(master() as never);
    setService("svc-1", "Массаж");
    renderScreen();
    await userEvent.click(await screen.findByRole("button", { name: OTHER_MASTERS_LABEL }));
    expect(await screen.findByText("MASTER-PICKER-PROBE")).toBeInTheDocument();
  });

  it("услуга из адреса — тоже к выбору мастера", async () => {
    mockedMaster.mockResolvedValue(master() as never);
    renderScreen("?service=svc-1");
    await userEvent.click(await screen.findByRole("button", { name: OTHER_MASTERS_LABEL }));
    expect(await screen.findByText("MASTER-PICKER-PROBE")).toBeInTheDocument();
  });

  it("без услуги — в каталог", async () => {
    mockedMaster.mockResolvedValue(master() as never);
    renderScreen();
    await userEvent.click(await screen.findByRole("button", { name: OTHER_MASTERS_LABEL }));
    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
  });
});
