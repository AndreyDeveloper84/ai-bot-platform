/**
 * DRF-1989 — карточка услуги не рисует цену ниже 1 ₽.
 *
 * 0.40 ₽ раньше округлялось в «0 ₽». Только показ: `price_from` не меняется.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchService: vi.fn() };
});

import { fetchService, type Service } from "../lib/api";
import { resetBooking } from "../state/booking";
import { ServiceDetailScreen } from "./ServiceDetailScreen";

const mockedFetchService = vi.mocked(fetchService);

/** «0 ₽», перед которым не цифра: «9 000 ₽» сюда не попадает. */
const ZERO_PRICE = /(^|[^\d\s])\s?0 ₽/;

function service(overrides: Partial<Service> = {}): Service {
  return {
    id: "svc-1989",
    slug: "piling",
    name: "Пилинг 1989",
    short_description: "",
    description: "",
    price_from: "9000.00",
    duration_min: 60,
    is_popular: false,
    contraindications: "",
    is_bookable: true,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
});

describe("ServiceDetailScreen — цена ниже 1 ₽ (DRF-1989)", () => {
  it("0.40 не рисуется как «0 ₽»", async () => {
    mockedFetchService.mockResolvedValue({ service: service({ price_from: "0.40" }) });
    render(
      <MemoryRouter initialEntries={["/customer/catalog/svc-1989"]}>
        <Routes>
          <Route path="/customer/catalog/:serviceId" element={<ServiceDetailScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: /Пилинг 1989/ })).toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toMatch(ZERO_PRICE);
  });
});
