/**
 * DRF-2988 B3b — сквозные отказы подписки уводят туда, где их снимают.
 *
 * Сигнал шлёт настоящий `admin-api.ts` на ответ сервера: предмет — путь
 * «отказ двери → экран», а не только сам слушатель.
 */
import { act, render, screen } from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  RouterProvider,
  Routes,
  createMemoryRouter,
  useLocation,
} from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getSalonDay } from "../lib/admin-api";
import { emitSalonBillingSignal } from "../lib/salon-billing-signal";
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

  it("два отказа в одном тике — один переход: «назад» ведёт на форму", async () => {
    // Рендер видит только последний адрес, дубль в истории — нет; его видно
    // шагом назад: с дублем он вернул бы на тот же экран перехода.
    const router = createMemoryRouter(
      [
        {
          path: "*",
          element: (
            <>
              <SalonBillingRedirect />
              <Where />
            </>
          ),
        },
      ],
      { initialEntries: ["/admin/team/invite"] },
    );
    render(<RouterProvider router={router} />);

    // Синхронно, до перерисовки: адрес в замыкании слушателя ещё прежний,
    // и держит один переход только флаг ухода.
    act(() => {
      emitSalonBillingSignal({ kind: "plan_change", salonPrice: "990.00" });
      emitSalonBillingSignal({ kind: "plan_change", salonPrice: "990.00" });
    });
    expect(await screen.findByText("at /admin/billing/salon · 990.00")).toBeInTheDocument();

    await act(() => router.navigate(-1));

    expect(await screen.findByText("at /admin/team/invite")).toBeInTheDocument();
  });

  it("другой отказ — остаётся на месте", async () => {
    answer(403, { error: "role_not_allowed", detail: "x" });
    mount();

    await expect(getSalonDay()).rejects.toThrow();

    expect(screen.getByText("at /admin/today")).toBeInTheDocument();
  });
});
