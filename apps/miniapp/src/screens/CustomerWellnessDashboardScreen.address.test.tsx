/**
 * Адрес визита: три состояния данных — два текста на экране (DRF-1611).
 *
 * Ручка отдавала жёсткий `""`, и экран рисовал **пустой блок со
 * стилями** — пустое место, которое выглядит как «здесь что-то должно
 * быть». Поле `Tenant.address` завела DRF-1587 сразу трёхзначным.
 *
 * | данные | что значит | что на экране |
 * |---|---|---|
 * | строка | адрес известен | адрес |
 * | `""` | **салон сказал**, адреса нет | «Адрес не указан» |
 * | `null` | источник промолчал — наш пробел | «Уточните адрес в салоне» |
 *
 * Строка есть в обоих пустых случаях, и это решение владельца контура,
 * а не упрощение: различие «чей пробел» — наше, а у человека нужда
 * одна и та же, он идёт на визит и не знает куда. Спрятать строку у
 * `null` значило бы дать меньше тому, кому нужнее: при `""` спрашивать
 * некого, при `null` адрес скорее всего есть.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCatalogBrowse: vi.fn() };
});

vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return {
    ...original,
    getWellnessToday: vi.fn(),
    getRecentActivity: vi.fn(),
  };
});

vi.mock("../lib/max-sdk", () => ({
  setBackButton: vi.fn(),
  onBackButton: vi.fn(),
  getInitData: () => "",
  openApp: vi.fn(),
}));

import { getCatalogBrowse } from "../lib/customer-booking";
import {
  getRecentActivity,
  getWellnessToday,
  type RecentActivity,
} from "../lib/customer-wellness";
import { CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";

const mockedBrowse = vi.mocked(getCatalogBrowse);
const mockedToday = vi.mocked(getWellnessToday);
const mockedActivity = vi.mocked(getRecentActivity);

function activityWithAddress(address: string | null): RecentActivity {
  return {
    next_booking: {
      date_human: "Завтра · пт · 16:00",
      service_name: "Массаж",
      duration_min: 60,
      master_name: "Ирина",
      salon_name: "Формула тела",
      address,
      booking_id: "bk-1",
    },
    this_week_booking_count: 1,
  } as RecentActivity;
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <CustomerWellnessDashboardScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedBrowse.mockResolvedValue({
    services: [],
    masters: [],
    picks: [],
    picksOutcome: "OK",
  });
  mockedToday.mockResolvedValue({ display_name: "Аня" } as never);
});

describe("адрес визита: три состояния, два текста", () => {
  it("известный адрес показывается как есть", async () => {
    mockedActivity.mockResolvedValue(activityWithAddress("Москва, Тверская 12"));
    renderScreen();

    expect(await screen.findByText("Москва, Тверская 12")).toBeInTheDocument();
    // Отсутствие: подсказок про неизвестность нет — адрес известен.
    expect(screen.queryByText(/Уточните адрес/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Адрес не указан/)).not.toBeInTheDocument();
  });

  it("салон сказал, что адреса нет — так и написано", async () => {
    mockedActivity.mockResolvedValue(activityWithAddress(""));
    renderScreen();

    // Присутствие: карточка визита на экране…
    expect(await screen.findByText("Формула тела", { exact: false })).toBeInTheDocument();
    // …и вместо пустого блока — фраза. Спрашивать некого: салон ответил.
    expect(screen.getByText("Адрес не указан")).toBeInTheDocument();
    expect(screen.queryByText(/Уточните адрес/)).not.toBeInTheDocument();
  });

  it("источник промолчал — человека отправляют спросить, а не оставляют ни с чем", async () => {
    mockedActivity.mockResolvedValue(activityWithAddress(null));
    renderScreen();

    expect(await screen.findByText("Уточните адрес в салоне")).toBeInTheDocument();
    // Отсутствие: «адреса нет» тут было бы ложью — салон ничего не говорил.
    expect(screen.queryByText("Адрес не указан")).not.toBeInTheDocument();
  });

  it("два пустых состояния различимы на экране, а не схлопнуты", async () => {
    const seen: string[] = [];
    for (const value of ["", null] as const) {
      mockedActivity.mockResolvedValue(activityWithAddress(value));
      const { unmount } = render(
        <MemoryRouter initialEntries={["/customer/main"]}>
          <CustomerWellnessDashboardScreen />
        </MemoryRouter>,
      );
      seen.push(
        (await screen.findByText(/Адрес не указан|Уточните адрес в салоне/)).textContent ?? "",
      );
      unmount();
    }

    expect(seen).toEqual(["Адрес не указан", "Уточните адрес в салоне"]);
    expect(new Set(seen).size).toBe(2);
  });
});
