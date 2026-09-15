/**
 * DRF-1989 — цена ниже 1 ₽ не рисуется на карточке услуги.
 *
 * Каталог не продаёт предложение дешевле 1 ₽ (DRF-1962): такая цена — не
 * цена, а незаполненное поле, и «0 ₽» читается как «бесплатно». Только
 * показ: `price_from` в ответе не меняется.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Service } from "../lib/api";
import { ServiceCard } from "./ServiceCard";

/** «0 ₽», перед которым не цифра: «9 000 ₽» сюда не попадает. */
const ZERO_PRICE = /(^|[^\d\s])\s?0 ₽/;

function service(overrides: Partial<Service> = {}): Service {
  return {
    id: "svc-1989",
    slug: "piling",
    name: "Пилинг",
    short_description: "",
    description: "",
    price_from: "1500.00",
    duration_min: 60,
    is_popular: false,
    contraindications: "",
    is_bookable: true,
    ...overrides,
  };
}

describe("ServiceCard — цена ниже 1 ₽ (DRF-1989)", () => {
  it("0.00 не рисуется ни на карточке, ни в aria-label", () => {
    render(<ServiceCard service={service({ price_from: "0.00" })} onSelect={() => {}} />);
    const card = screen.getByRole("button");

    expect(card.textContent ?? "").not.toMatch(ZERO_PRICE);
    expect(card.getAttribute("aria-label") ?? "").not.toMatch(ZERO_PRICE);
  });

  it("настоящая цена рисуется как раньше", () => {
    render(<ServiceCard service={service()} onSelect={() => {}} />);

    expect(screen.getByRole("button")).toHaveTextContent("1 500 ₽");
  });
});
