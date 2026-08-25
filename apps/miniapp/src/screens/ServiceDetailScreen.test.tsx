/**
 * DRF-1164 — a service nobody can perform must be readable but not
 * bookable.
 *
 * `/catalog/:serviceId` is the single door into the booking flow from the
 * service side: both catalog surfaces (`/catalog`, `/customer/catalog`),
 * the wellness picks, and bot deep-links by service id all land here. So
 * this screen is where the path is closed, and this file is what proves it
 * stays closed — including the case where the customer arrives by URL
 * without ever seeing the marked card.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchService: vi.fn() };
});

import { fetchService, type Service } from "../lib/api";
import { ServiceDetailScreen } from "./ServiceDetailScreen";
import { getBookingDraft, resetBooking } from "../state/booking";

const mockedFetchService = vi.mocked(fetchService);

function service(overrides: Partial<Service> = {}): Service {
  return {
    id: "svc-1164",
    slug: "gladkaya-kozha",
    name: "Гладкая кожа (комплекс)",
    short_description: "Антицеллюлитный массаж + VelaShape",
    description: "",
    price_from: "9000.00",
    duration_min: 105,
    is_popular: false,
    contraindications: "Беременность",
    is_bookable: true,
    ...overrides,
  };
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/catalog/svc-1164"]}>
      <Routes>
        <Route path="/catalog/:serviceId" element={<ServiceDetailScreen />} />
        <Route path="/catalog" element={<div>CATALOG</div>} />
        <Route path="/book/master" element={<div>MASTER-PICKER</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
});

describe("ServiceDetailScreen — DRF-1164 unbookable service", () => {
  it("keeps the CTA when the service has a bookable master", async () => {
    mockedFetchService.mockResolvedValue({ service: service() });
    renderScreen();
    expect(
      await screen.findByRole("button", { name: "Подобрать время" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/нет свободных мастеров/i)).not.toBeInTheDocument();
  });

  it("shows the notice and withholds the CTA when nobody performs it", async () => {
    mockedFetchService.mockResolvedValue({
      service: service({ is_bookable: false }),
    });
    renderScreen();

    // The service is still on screen — the shop window is not lost.
    expect(
      await screen.findByRole("heading", { name: /Гладкая кожа/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Беременность")).toBeInTheDocument();

    // …but there is no way to start booking from here.
    expect(screen.getByText(/нет свободных мастеров/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Подобрать время" }),
    ).not.toBeInTheDocument();
  });

  it("never seeds the booking draft for an unbookable service", async () => {
    // The direct-URL bypass: the customer never saw the marked card, so
    // the ONLY thing standing between them and the empty master list is
    // this screen. Nothing here may write `serviceId` into the draft —
    // that is what /book/master and /book/when read.
    mockedFetchService.mockResolvedValue({
      service: service({ is_bookable: false }),
    });
    renderScreen();
    await screen.findByText(/нет свободных мастеров/i);

    expect(
      screen.queryByRole("button", { name: "Подобрать время" }),
    ).not.toBeInTheDocument();

    // The one control the screen does offer leads to the catalog, never
    // into the flow.
    await userEvent.click(screen.getByRole("button", { name: "Другие услуги" }));
    expect(await screen.findByText("CATALOG")).toBeInTheDocument();
    expect(screen.queryByText("MASTER-PICKER")).not.toBeInTheDocument();
    expect(getBookingDraft().serviceId).toBeNull();
  });
});
