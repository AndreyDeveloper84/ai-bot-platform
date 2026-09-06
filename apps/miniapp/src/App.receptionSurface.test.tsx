/**
 * DRF-1522 — салонная поверхность глазами ресепшн.
 *
 * Решение владельца 05.09.2026: «убрать две вкладки и сажать на День».
 * Пять разделов при этом СОХРАНЯЮТСЯ для владельца и администратора —
 * поэтому здесь каждая отрицательная проверка идёт в паре с
 * положительной. Без пары «починка» легко превратилась бы в исчезновение
 * вкладок у всех (`negative_assert_guard`, DRF-1411).
 *
 * Тесты умеют падать: верните `to="/admin/team"` в catch-all `AdminRoutes`
 * — покраснеет посадка; верните полный список вместо `adminTabsFor(me)` в
 * `AdminTabBar` — покраснеет состав панели; снимите стража с
 * `/admin/internal-chat` — покраснеет прямая ссылка.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return {
    ...original,
    getMe: vi.fn(),
    getSalonDay: vi.fn(),
    listMasters: vi.fn(),
    getAvailabilityRequests: vi.fn(),
  };
});

vi.mock("./lib/internal-chat-api", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("./lib/internal-chat-api")>();
  return { ...original, listAdminThreads: vi.fn() };
});

import {
  getAvailabilityRequests,
  getMe,
  getSalonDay,
  listMasters,
  type MeResponse,
} from "./lib/admin-api";
import { listAdminThreads } from "./lib/internal-chat-api";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDay = vi.mocked(getSalonDay);
const mockedMasters = vi.mocked(listMasters);
const mockedRequests = vi.mocked(getAvailabilityRequests);
const mockedThreads = vi.mocked(listAdminThreads);

const BASE_ME: MeResponse = {
  user: { id: "u-1", name: "Ирина", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Demo", slug: "demo" },
  role: "receptionist",
  capabilities: [],
  is_customer: false,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: false,
  master_id: null,
  landing_path: "/admin/team",
};

/** Ресепшн — и только ресепшн. */
const RECEPTION_ME: MeResponse = { ...BASE_ME, is_receptionist: true };
const OWNER_ME: MeResponse = { ...BASE_ME, role: "owner", is_owner: true };
const ADMIN_ME: MeResponse = { ...BASE_ME, role: "admin", is_admin: true };

function renderAppAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

/** Подписи вкладок в нижней панели, в порядке отрисовки. */
function tabLabels(): string[] {
  const bar = screen.getByRole("navigation", { name: "Основная навигация" });
  return Array.from(bar.querySelectorAll("button")).map(
    (b) => b.getAttribute("aria-label") ?? "",
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedDay.mockResolvedValue({
    date: "2026-09-05",
    timezone: "Europe/Moscow",
    summary: { total: 0, upcoming: 0, completed: 0, released: 0 },
    masters: [],
    orphan_visits: [],
  });
  mockedMasters.mockResolvedValue({ items: [], next_cursor: null, total_count: 0 });
  mockedRequests.mockResolvedValue({ items: [], next_cursor: null });
  mockedThreads.mockResolvedValue({
    items: [],
    total_count: 0,
    offset: 0,
    limit: 50,
  });
});

describe("ресепшн садится на «День» (DRF-1522)", () => {
  it("вход без адреса приводит ресепшн на «День», а не на «Команду»", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/");
    // «День» открылся: экран сходил за днём салона.
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    const bar = await screen.findByRole("navigation", {
      name: "Основная навигация",
    });
    const current = bar.querySelector("[aria-current]");
    expect(current?.getAttribute("aria-label")).toBe("День");
    // Ростер мастеров не запрашивался — на «Команду» её не заносило.
    expect(mockedMasters).not.toHaveBeenCalled();
  });

  it("владелец по-прежнему садится на «Команду»", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/");
    await waitFor(() => expect(mockedMasters).toHaveBeenCalled());
    expect(mockedDay).not.toHaveBeenCalled();
  });

  it("администратор по-прежнему садится на «Команду»", async () => {
    mockedGetMe.mockResolvedValue(ADMIN_ME);
    renderAppAt("/");
    await waitFor(() => expect(mockedMasters).toHaveBeenCalled());
    expect(mockedDay).not.toHaveBeenCalled();
  });
});

describe("состав нижней панели (DRF-1522)", () => {
  it("у ресепшн три вкладки, «Чатов» и «Настроек» нет", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/admin/day");
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    expect(tabLabels()).toEqual(["День", "Команда", "Услуги"]);
    expect(
      screen.queryByRole("button", { name: "Чаты" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Настройки" }),
    ).not.toBeInTheDocument();
  });

  it("у владельца пять вкладок — состав не изменился", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/day");
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    expect(tabLabels()).toEqual([
      "День",
      "Команда",
      "Услуги",
      "Чаты",
      "Настройки",
    ]);
  });

  it("у администратора пять вкладок — состав не изменился", async () => {
    mockedGetMe.mockResolvedValue(ADMIN_ME);
    renderAppAt("/admin/day");
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    expect(tabLabels()).toEqual([
      "День",
      "Команда",
      "Услуги",
      "Чаты",
      "Настройки",
    ]);
  });

  it("панель ресепшн растянута на свои три колонки, а не сжата в пять", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/admin/day");
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    const bar = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(bar.getAttribute("style")).toContain("repeat(3, 1fr)");
  });
});

describe("прямая ссылка на закрытый раздел (DRF-1522)", () => {
  it("ресепшн получает честный отказ на «Чатах», а не пустой экран", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/admin/internal-chat");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Раздел «Чаты» открыт владельцу и администратору/,
    );
    // До бэкенда дело не дошло: 403 остаётся правильным ответом, но
    // человеку показывают отказ, а не ошибку загрузки.
    expect(mockedThreads).not.toHaveBeenCalled();
    // Выход есть — и он ведёт на «День».
    expect(
      screen.getByRole("button", { name: "Вернуться в «День»" }),
    ).toBeInTheDocument();
  });

  it("ресепшн получает честный отказ на «Настройках»", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/admin/settings");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Раздел «Настройки» открыт владельцу и администратору/,
    );
    expect(screen.queryByText(/Скоро здесь будут настройки/)).toBeNull();
  });

  it("владелец те же адреса открывает как раньше", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/internal-chat");
    await waitFor(() => expect(mockedThreads).toHaveBeenCalled());
    expect(
      screen.queryByRole("button", { name: "Вернуться в «День»" }),
    ).not.toBeInTheDocument();
  });

  it("администратору «Настройки» открываются заглушкой, а не отказом", async () => {
    mockedGetMe.mockResolvedValue(ADMIN_ME);
    renderAppAt("/admin/settings");
    expect(
      await screen.findByText(/Скоро здесь будут настройки/),
    ).toBeInTheDocument();
  });
});
