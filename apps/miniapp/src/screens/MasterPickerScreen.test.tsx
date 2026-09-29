/**
 * DRF-1485 §3 — запасной выход подбора мастера ведёт в ЖИВОЙ каталог.
 *
 * `MasterPickerScreen` исключён из удаления решением владельца §31
 * (Q-CLIENT-01, вариант B) и канонизирован по адресу
 * `/customer/book/master`. Раз экран остаётся жить, все три его выхода
 * обязаны вести в поколение, которое остаётся жить вместе с ним:
 *
 *   1. потерянный черновик (`draft.serviceId` пуст) — редирект;
 *   2. «Другие услуги» на пустом списке мастеров;
 *   3. кнопка «Назад» каркаса (`ScreenLayout`).
 *
 * До правки все три вели в `/catalog` — экран прежнего поколения,
 * оставленный только compatibility-алиасом для ушедших наружу ссылок.
 * Человек, потерявший черновик, попадал из канонического поколения в
 * прежнее и оставался там.
 *
 * Каждый случай проверяется парой: КУДА привело (положительно) и куда
 * НЕ привело (отрицательно) — на одном и том же рендере.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchMasters: vi.fn() };
});

import { fetchMasters, type Master } from "../lib/api";
import { MasterPickerScreen } from "./MasterPickerScreen";
import { resetBooking, setService } from "../state/booking";

const mockedFetchMasters = vi.mocked(fetchMasters);

function master(overrides: Partial<Master> = {}): Master {
  return {
    id: "mst-1",
    name: "Анна Соколова",
    specialization: "Маникюр",
    bio: "",
    experience: "5 лет",
    rating: "4.9",
    photo_url: "",
    ...overrides,
  };
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/book/master"]}>
      <Routes>
        <Route path="/customer/book/master" element={<MasterPickerScreen />} />
        <Route path="/customer/catalog" element={<div>CATALOG-LIVE</div>} />
        {/* Прежнее поколение. Маршрут остаётся алиасом в App.tsx, но
            внутренний переход сюда вести не должен — вот проба. */}
        <Route path="/catalog" element={<div>CATALOG-LEGACY</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
  mockedFetchMasters.mockResolvedValue({ masters: [master()] });
});

describe("MasterPickerScreen — выходы ведут в живой каталог (DRF-1485)", () => {
  it("потерянный черновик уводит в /customer/catalog, не в /catalog", async () => {
    // Услуга не выбрана — экрану нечего показывать, он обязан вернуть
    // человека к выбору услуги.
    renderScreen();

    expect(await screen.findByText("CATALOG-LIVE")).toBeInTheDocument();
    expect(screen.queryByText("CATALOG-LEGACY")).not.toBeInTheDocument();
    // И к сети он при этом не ходил — редирект случился до загрузки.
    expect(mockedFetchMasters).not.toHaveBeenCalled();
  });

  it("«Другие услуги» на пустом списке ведёт в /customer/catalog", async () => {
    setService("svc-1", "Маникюр");
    mockedFetchMasters.mockResolvedValue({ masters: [] });
    renderScreen();

    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Другие услуги" }),
    );

    expect(await screen.findByText("CATALOG-LIVE")).toBeInTheDocument();
    expect(screen.queryByText("CATALOG-LEGACY")).not.toBeInTheDocument();
  });

  it("«Назад» ведёт в /customer/catalog", async () => {
    setService("svc-1", "Маникюр");
    renderScreen();
    // Положительно: экран действительно отрисовался со списком —
    // иначе «нажали Назад» проверяло бы пустоту.
    expect(await screen.findByText("Анна Соколова")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Назад" }));

    expect(await screen.findByText("CATALOG-LIVE")).toBeInTheDocument();
    expect(screen.queryByText("CATALOG-LEGACY")).not.toBeInTheDocument();
  });
});
