/**
 * DRF-2988 B3b — сквозные отказы подписки уводят туда, где их снимают.
 *
 * Сигнал шлёт настоящий `admin-api.ts` на ответ сервера: предмет — путь
 * «отказ двери → экран», а не только сам слушатель.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getSalonDay } from "../lib/admin-api";
import { SalonBillingRedirect } from "./SalonBillingRedirect";

function Where() {
  const location = useLocation();
  const price = (location.state as { salonPrice?: string } | null)?.salonPrice;
  return <p>{`at ${location.pathname}${price ? ` · ${price}` : ""}`}</p>;
}

function mount(path = "/admin/today") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <SalonBillingRedirect />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

function answer(status: number, body: unknown) {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("отказ двери → экран", () => {
  it("заморозка с любой двери — на экран подписки", async () => {
    answer(403, { error: "subscription_frozen", detail: "x" });
    mount();
    expect(screen.getByText("at /admin/today")).toBeInTheDocument();

    await expect(getSalonDay()).rejects.toThrow();

    expect(await screen.findByText("at /admin/billing")).toBeInTheDocument();
  });

  it("«нужен тариф Салон» — на подтверждение с ценой из отказа", async () => {
    answer(409, {
      error: "plan_change_required",
      detail: "x",
      details: { salon_price: "990.00" },
    });
    mount("/admin/team/invite");

    await expect(getSalonDay()).rejects.toThrow();

    expect(await screen.findByText("at /admin/billing/salon · 990.00")).toBeInTheDocument();
  });

  it("другой отказ — остаётся на месте", async () => {
    answer(403, { error: "role_not_allowed", detail: "x" });
    mount();

    await expect(getSalonDay()).rejects.toThrow();

    expect(screen.getByText("at /admin/today")).toBeInTheDocument();
  });
});
