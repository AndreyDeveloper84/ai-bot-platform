/**
 * §31 — канонизация адресов подбора мастера и времени.
 *
 * Решение владельца 06.09.2026 (`docs/OPEN_DECISIONS.md` §31):
 * `MasterPickerScreen` и `BookingWhenScreen` исключаются из удаления
 * DRF-1485 и канонизируются как `/customer/book/master` и
 * `/customer/book/when`, **старые адреса остаются алиасами**.
 *
 * Стража парная (`negative_assert_guard`, DRF-1411): каждой проверке
 * «канонический адрес открывает настоящий экран» отвечает проверка
 * «старый адрес по-прежнему открывает тот же экран». Правка, которая
 * канонизировала бы адреса ЦЕНОЙ алиасов, прошла бы первую половину и
 * упала на второй — а это ровно то, что решение запрещает.
 *
 * Тесты умеют падать: снимите `/customer/book/master` или
 * `/customer/book/when` из `App.tsx` — покраснеет каноническая пара;
 * снимите `/book/master` / `/book/when` — покраснеют алиасы; верните
 * `navigate("/book/master")` в `ServiceDetailScreen` — покраснеет
 * проверка внутреннего перехода.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return { ...original, getMe: vi.fn() };
});

vi.mock("./lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/api")>();
  return {
    ...original,
    fetchMasters: vi.fn(),
    fetchSlots: vi.fn(),
    fetchService: vi.fn(),
  };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import { fetchMasters, fetchSlots, type Master } from "./lib/api";
import { resetBooking, setMaster, setService } from "./state/booking";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedFetchMasters = vi.mocked(fetchMasters);
const mockedFetchSlots = vi.mocked(fetchSlots);

const CUSTOMER_ME: MeResponse = {
  user: { id: "u-1", name: "Ольга", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Demo", slug: "demo" },
  role: "customer",
  capabilities: [],
  is_customer: true,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: false,
  master_id: null,
  landing_path: "/customer/main",
  is_solo_provider: false,
};

const MASTER: Master = {
  id: "mst-1",
  name: "Анна Соколова",
  specialization: "Маникюр",
  bio: "",
  experience: "",
  photo_url: "",
  rating: null,
};

function renderAppAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
  mockedGetMe.mockResolvedValue(CUSTOMER_ME);
  mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
  mockedFetchSlots.mockResolvedValue({ slots: [] });
});

describe("§31 — канонические адреса", () => {
  it("/customer/book/master открывает подбор мастера", async () => {
    setService("svc-1", "Маникюр");
    renderAppAt("/customer/book/master");
    expect(
      await screen.findByRole("heading", { name: "Кто сделает" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Анна Соколова")).toBeInTheDocument();
  });

  it("/customer/book/when открывает подбор времени", async () => {
    setService("svc-1", "Маникюр");
    setMaster("mst-1", "Анна Соколова");
    renderAppAt("/customer/book/when");
    expect(
      await screen.findByRole("heading", { name: "Выберите время" }),
    ).toBeInTheDocument();
  });
});

describe("§31 — старые адреса остаются алиасами", () => {
  it("/book/master открывает тот же подбор мастера", async () => {
    setService("svc-1", "Маникюр");
    renderAppAt("/book/master");
    expect(
      await screen.findByRole("heading", { name: "Кто сделает" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Анна Соколова")).toBeInTheDocument();
  });

  it("/book/when открывает тот же подбор времени", async () => {
    setService("svc-1", "Маникюр");
    setMaster("mst-1", "Анна Соколова");
    renderAppAt("/book/when");
    expect(
      await screen.findByRole("heading", { name: "Выберите время" }),
    ).toBeInTheDocument();
  });
});
