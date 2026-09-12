/**
 * «Показать рядом со мной» (DRF-1707) — одноразовая геолокация по кнопке,
 * расстояние с провода, имя «Рядом с вами» только при наличии поля.
 *
 * Решение владельца (пакет 2, D3): явное contextual consent на
 * одноразовую геолокацию; координаты не сохраняются; перед вызовом ОС
 * понятно, зачем нужна location. OD-PILOT-9: `< 1000` → метры, иначе
 * «1.8 км»; «Рядом с вами» — только у списка с расстоянием (#1653).
 *
 * Стражи:
 * 1. пояснение стоит ДО тапа — ОС ещё не спрошена;
 * 2. тап → ОС спрошена один раз (`maximumAge: 0`) → мастера перечитаны
 *    с координатами → заголовок «Рядом с вами», подписи «850 м» / «1.8 км»,
 *    `null` без подписи, порядок — серверный;
 * 3. координаты не попадают ни в одно хранилище браузера;
 * 4. отказ ОС → честный текст, заголовок остаётся «Мастера», запроса с
 *    координатами нет;
 * 5. положительная стража: без поля в данных — «Мастера», кнопка есть.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    fetchServices: vi.fn(),
    fetchMasters: vi.fn(),
    fetchRecommendations: vi.fn(),
  };
});

import { fetchMasters, fetchRecommendations, fetchServices, type Master } from "../lib/api";
import {
  NEARBY_BUTTON,
  NEARBY_DENIED,
  NEARBY_EXPLANATION,
  formatDistance,
  hasKnownDistance,
} from "../lib/nearby";
import { SURFACE_NEARBY } from "../lib/recommendation-absence";
import { CustomerCatalogScreen } from "./CustomerCatalogScreen";

const mockedServices = vi.mocked(fetchServices);
const mockedMasters = vi.mocked(fetchMasters);
const mockedRecs = vi.mocked(fetchRecommendations);

const SERVICES = [
  {
    id: "svc-1",
    slug: "manicure",
    name: "Маникюр",
    short_description: "",
    description: "",
    price_from: null,
    duration_min: 60,
    is_popular: false,
    contraindications: "",
    is_bookable: true,
  },
];
const base = (name: string, id: string) => ({
  id,
  name,
  specialization: "мастер",
  bio: "",
  experience: "5 лет",
  rating: "4.9",
  photo_url: "",
});
const MASTERS_PLAIN: Master[] = [base("Анна", "m-1"), base("Борис", "m-2"), base("Вера", "m-3")];
const MASTERS_NEAR: Master[] = [
  { ...base("Борис", "m-2"), distance_meters: 850 },
  { ...base("Анна", "m-1"), distance_meters: 1800 },
  { ...base("Вера", "m-3"), distance_meters: null },
];

function decisionOk(): unknown {
  return {
    data: {
      decision_id: "d",
      request_id: "r",
      resolver_spec_version: "1.0",
      policy_versions: { resolver_spec_version: "1.0" },
      ordered: [],
    },
  };
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/catalog"]}>
      <Routes>
        <Route path="/customer/catalog" element={<CustomerCatalogScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

const getCurrentPosition = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  mockedServices.mockResolvedValue({ services: SERVICES as never });
  mockedMasters.mockResolvedValue({ masters: MASTERS_PLAIN as never });
  mockedRecs.mockResolvedValue(decisionOk() as never);
  Element.prototype.scrollIntoView = vi.fn();
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: { getCurrentPosition },
  });
  sessionStorage.clear();
  localStorage.clear();
});

function grantPosition(lat: number, lon: number) {
  getCurrentPosition.mockImplementation((ok: (p: unknown) => void) =>
    ok({ coords: { latitude: lat, longitude: lon } }),
  );
}

function denyPosition() {
  getCurrentPosition.mockImplementation((_ok: unknown, err: (e: unknown) => void) =>
    err({ code: 1 }),
  );
}

describe("формат и имя списка", () => {
  it("метры до километра, километры с одним знаком, неизвестное — пусто", () => {
    expect(formatDistance(850)).toBe("850 м");
    expect(formatDistance(999)).toBe("999 м");
    expect(formatDistance(1000)).toBe("1.0 км");
    expect(formatDistance(1800)).toBe("1.8 км");
    expect(formatDistance(null)).toBe("");
    expect(formatDistance(undefined)).toBe("");
    expect(formatDistance(-5)).toBe("");
  });

  it("«Рядом с вами» — только при хотя бы одном известном расстоянии", () => {
    expect(hasKnownDistance(MASTERS_PLAIN)).toBe(false);
    expect(hasKnownDistance([{ distance_meters: null }])).toBe(false);
    expect(hasKnownDistance(MASTERS_NEAR)).toBe(true);
  });
});

describe("кнопка «Показать рядом со мной» (D3)", () => {
  it("пояснение стоит до тапа; ОС не спрошена; заголовок «Мастера»", async () => {
    renderScreen();
    await screen.findByRole("button", { name: NEARBY_BUTTON });
    expect(screen.getByText(NEARBY_EXPLANATION)).toBeInTheDocument();
    expect(getCurrentPosition).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "Мастера" })).toBeInTheDocument();
    expect(screen.queryByTestId("master-distance")).toBeNull();
  });

  it("тап → ОС один раз без кэша → мастера с координатами → «Рядом с вами» и подписи", async () => {
    grantPosition(55.751244, 37.618423);
    mockedMasters.mockResolvedValueOnce({ masters: MASTERS_PLAIN as never });
    mockedMasters.mockResolvedValueOnce({ masters: MASTERS_NEAR as never });
    renderScreen();
    await userEvent.click(await screen.findByRole("button", { name: NEARBY_BUTTON }));

    expect(await screen.findByRole("heading", { name: SURFACE_NEARBY })).toBeInTheDocument();
    expect(getCurrentPosition).toHaveBeenCalledTimes(1);
    expect(getCurrentPosition.mock.calls[0]?.[2]).toEqual(
      expect.objectContaining({ maximumAge: 0 }),
    );
    expect(mockedMasters).toHaveBeenLastCalledWith({ coords: { lat: 55.751244, lon: 37.618423 } });

    const section = screen.getByRole("region", { name: SURFACE_NEARBY });
    const names = within(section)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"))
      .filter((l) => l?.startsWith("Мастер "));
    expect(names).toEqual(["Мастер Борис, мастер", "Мастер Анна, мастер", "Мастер Вера, мастер"]);
    const distances = within(section).getAllByTestId("master-distance").map((d) => d.textContent);
    expect(distances).toEqual(["850 м", "1.8 км"]);
    // Кнопка и пояснение больше не нужны — список уже «рядом».
    expect(screen.queryByRole("button", { name: NEARBY_BUTTON })).toBeNull();
  });

  it("координаты не попадают ни в одно хранилище браузера", async () => {
    grantPosition(55.751244, 37.618423);
    mockedMasters.mockResolvedValueOnce({ masters: MASTERS_PLAIN as never });
    mockedMasters.mockResolvedValueOnce({ masters: MASTERS_NEAR as never });
    renderScreen();
    await userEvent.click(await screen.findByRole("button", { name: NEARBY_BUTTON }));
    await screen.findByRole("heading", { name: SURFACE_NEARBY });

    const dump = [
      ...Array.from({ length: sessionStorage.length }, (_, i) => sessionStorage.getItem(sessionStorage.key(i) ?? "")),
      ...Array.from({ length: localStorage.length }, (_, i) => localStorage.getItem(localStorage.key(i) ?? "")),
    ].join("\n");
    expect(dump).not.toMatch(/55\.75|37\.61/);
  });

  it("отказ ОС → честный текст, «Мастера» остаются, запроса с координатами нет", async () => {
    denyPosition();
    renderScreen();
    await userEvent.click(await screen.findByRole("button", { name: NEARBY_BUTTON }));

    expect(await screen.findByText(NEARBY_DENIED)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Мастера" })).toBeInTheDocument();
    expect(mockedMasters).toHaveBeenCalledTimes(1);
    expect(mockedMasters.mock.calls[0]?.[0]).toBeUndefined();
  });

  it("положительная стража: ответ с координатами без поля — по-прежнему «Мастера»", async () => {
    grantPosition(55.75, 37.62);
    mockedMasters.mockResolvedValueOnce({ masters: MASTERS_PLAIN as never });
    mockedMasters.mockResolvedValueOnce({ masters: MASTERS_PLAIN as never });
    renderScreen();
    await userEvent.click(await screen.findByRole("button", { name: NEARBY_BUTTON }));
    await screen.findByRole("button", { name: NEARBY_BUTTON });
    expect(screen.getByRole("heading", { name: "Мастера" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: SURFACE_NEARBY })).toBeNull();
  });
});
