/**
 * DRF-1989 — подборка на главной не пишет «от 0 ₽».
 *
 * Строка цены стояла под условием `service.price_from ?`, а строка "0.00"
 * истинна. Только показ: `price_from` не меняется.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return {
    ...original,
    getCatalogBrowse: vi.fn(),
  };
});

vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "test-init-data",
  setBackButton: () => undefined,
  onBackButton: () => () => undefined,
}));

import { getCatalogBrowse } from "../lib/customer-booking";

const mockedBrowse = vi.mocked(getCatalogBrowse);

const PILING = {
  id: "svc-1989",
  slug: "piling",
  name: "Пилинг 1989",
  short_description: "",
  description: "",
  price_from: "0.00",
  duration_min: 30,
  is_popular: false,
  contraindications: "",
  is_bookable: true,
};

describe("CustomerWellnessDashboardScreen — цена ниже 1 ₽ (DRF-1989)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    // DEV-сборка на заглушке данных: сеть здесь не нужна и запрещена.
    window.history.replaceState({}, "", "/customer/main?stub=default");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network reached: ?stub= selection is broken");
      }),
    );
  });

  it("подборка с ценой 0.00 не пишет «от 0 ₽»", async () => {
    mockedBrowse.mockResolvedValue({
      services: [PILING],
      masters: [],
      picks: [
        {
          serviceId: "svc-1989",
          tier: 1,
          rank: 1,
          reasonCodes: ["EXEC_SLOT_CONFIRMED_IN_WINDOW"],
          reasons: ["Есть свободное время в нужном окне"],
        },
      ],
      picksOutcome: "OK",
    });
    vi.resetModules();
    const { CustomerWellnessDashboardScreen } = await import("./CustomerWellnessDashboardScreen");
    render(
      <MemoryRouter initialEntries={["/customer/main"]}>
        <CustomerWellnessDashboardScreen />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Пилинг 1989")).toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toMatch(/от 0 ₽/);
  });
});
