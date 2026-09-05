/**
 * DRF-1493 — возврат на клиентских экранах.
 *
 * Задача найдена владельцем на пилоте: экран со списком услуг, подпись
 * «Нету кнопки назад». Экран оказался не один.
 *
 * Здесь проверяются ОБЕ половины исправления:
 *
 * 1. Вложенные экраны — возврат есть, видимый и аппаратный, и ведёт в
 *    ЗАЯВЛЕННОЕ место. Каждый такой экран монтируется ОДНОЙ записью в
 *    истории — ровно так, как человек попадает в мини-приложение по
 *    deep link из бота (`open_app` с payload). При такой истории
 *    `navigate(-1)` не делает ничего: если бы возврат остался на ней,
 *    проба родителя не отрисовалась бы и тест бы покраснел. То есть
 *    «место задано, а не взято из истории» — не комментарий, а
 *    условие прохождения.
 *
 * 2. Корневые экраны — лишней кнопки НЕ появилось. Парная
 *    положительная проверка: правка не могла пройти, просто раздав
 *    стрелку всем подряд.
 *
 * Дыра «новый экран молча без возврата» закрыта отдельно:
 * `backContract.test.tsx` (объявление обязательно) плюс обязательное
 * поле `back` у `ScreenLayout` (не собирается без него).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return {
    ...original,
    maxBridge: () => null,
    hapticNotify: vi.fn(),
    setBackButton: vi.fn(),
    onBackButton: vi.fn(() => () => undefined),
  };
});

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchServices: vi.fn(), fetchMyBookings: vi.fn() };
});

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<
    typeof import("../lib/customer-booking")
  >();
  return { ...original, getCatalogBrowse: vi.fn() };
});

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return {
    ...original,
    fetchDecisionContext: vi.fn().mockResolvedValue({
      version: 1,
      known: { goal: null },
      missing: [],
      suggestions: [],
      intents: [],
    }),
  };
});

import { fetchMyBookings, fetchServices } from "../lib/api";
import { getCatalogBrowse } from "../lib/customer-booking";
import { onBackButton, setBackButton } from "../lib/max-sdk";
import { CatalogScreen } from "./CatalogScreen";
import { CustomerBookingSuccessScreen } from "./CustomerBookingSuccessScreen";
import { CustomerCatalogScreen } from "./CustomerCatalogScreen";
import { CustomerRecordsScreen } from "./CustomerRecordsScreen";
import { CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";

const mockedBrowse = vi.mocked(getCatalogBrowse);
const mockedServices = vi.mocked(fetchServices);
const mockedBookings = vi.mocked(fetchMyBookings);
const mockedOnBack = vi.mocked(onBackButton);
const mockedSetBack = vi.mocked(setBackButton);

/** Проба: печатает адрес, на который увёл возврат. */
function Probe({ name }: { name: string }) {
  return <div>ПРИЕХАЛИ:{name}</div>;
}

/**
 * Монтирует экран РОВНО ОДНОЙ записью в истории — вход по deep link из
 * бота, где предыдущей страницы не существует.
 */
function renderDeepLink(at: string, element: React.ReactNode, path: string) {
  render(
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        <Route path={path} element={element} />
        <Route path="/" element={<Probe name="/" />} />
        <Route path="/catalog" element={<Probe name="/catalog" />} />
        <Route path="/customer/main" element={<Probe name="/customer/main" />} />
        <Route
          path="/customer/records"
          element={<Probe name="/customer/records" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedOnBack.mockImplementation(() => () => undefined);
  mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [] });
  mockedServices.mockResolvedValue({ services: [] });
  mockedBookings.mockResolvedValue({
    items: [],
    next_cursor: null,
  });
});

describe("DRF-1493 · вложенный экран: возврат есть и ведёт в заявленное место", () => {
  it("CustomerCatalogScreen (экран со скриншота владельца) → /customer/main", async () => {
    const user = userEvent.setup();
    renderDeepLink(
      "/customer/catalog",
      <CustomerCatalogScreen />,
      "/customer/catalog",
    );
    await screen.findByRole("heading", { name: "Найди мастера" });
    await user.click(screen.getByRole("button", { name: "Назад" }));
    expect(await screen.findByText("ПРИЕХАЛИ:/customer/main")).toBeInTheDocument();
  });

  it("CustomerBookingSuccessScreen → /customer/records", async () => {
    const user = userEvent.setup();
    renderDeepLink(
      "/customer/booking/success/b-1",
      <CustomerBookingSuccessScreen />,
      "/customer/booking/success/:bookingId",
    );
    await user.click(await screen.findByRole("button", { name: "Назад" }));
    expect(
      await screen.findByText("ПРИЕХАЛИ:/customer/records"),
    ).toBeInTheDocument();
  });

  it("CatalogScreen (легаси) → /", async () => {
    const user = userEvent.setup();
    renderDeepLink("/catalog", <CatalogScreen />, "/catalog");
    await screen.findByRole("heading", { name: "Услуги студии" });
    await user.click(screen.getByRole("button", { name: "Назад" }));
    expect(await screen.findByText("ПРИЕХАЛИ:/")).toBeInTheDocument();
  });

  it("аппаратная кнопка MAX заведена туда же, куда видимая", async () => {
    renderDeepLink(
      "/customer/catalog",
      <CustomerCatalogScreen />,
      "/customer/catalog",
    );
    await screen.findByRole("heading", { name: "Найди мастера" });

    expect(mockedSetBack).toHaveBeenCalledWith(true);
    const handler = mockedOnBack.mock.calls.at(-1)?.[0];
    expect(handler).toBeTypeOf("function");
    handler!();

    expect(await screen.findByText("ПРИЕХАЛИ:/customer/main")).toBeInTheDocument();
  });
});

describe("DRF-1493 · корневой экран: лишней кнопки не появилось", () => {
  it("CustomerRecordsScreen — дом клиентской поверхности", async () => {
    renderDeepLink(
      "/customer/main",
      <CustomerRecordsScreen />,
      "/customer/main",
    );
    await screen.findByRole("tab", { name: /Ближайшие/ });
    expect(
      screen.queryByRole("button", { name: "Назад" }),
    ).not.toBeInTheDocument();
    // Объявление корня — не молчание: аппаратная кнопка спрятана явно.
    expect(mockedSetBack).toHaveBeenCalledWith(false);
    expect(mockedSetBack).not.toHaveBeenCalledWith(true);
  });

  it("CustomerWellnessDashboardScreen — вкладка «День»", async () => {
    window.history.replaceState({}, "", "/customer/wellness?stub=default");
    renderDeepLink(
      "/customer/wellness",
      <CustomerWellnessDashboardScreen />,
      "/customer/wellness",
    );
    await screen.findByRole("button", { name: "День" });
    expect(
      screen.queryByRole("button", { name: "Назад" }),
    ).not.toBeInTheDocument();
    expect(mockedSetBack).toHaveBeenCalledWith(false);
    expect(mockedSetBack).not.toHaveBeenCalledWith(true);
  });
});
