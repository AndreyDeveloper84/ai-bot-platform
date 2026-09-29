/**
 * DRF-1893 — без initData Mini App не открывается ни на одном входе.
 *
 * Решение владельца (раздел U, канон 1319-D): пустой initData — невалидный
 * транспортный вход, а не анонимный клиент. Mini App работает только из MAX с
 * валидным initData; без него — экран «Открой Ayla из MAX» с возвратом в MAX.
 *
 * До этого листа человек без initData видел семь разных исходов (замер
 * 15.09): баннер «Не получилось загрузить ваш профиль» + «Не получилось
 * войти» с повтором, гейт регистрации на подтверждении записи, «доступ
 * мастера ещё не подтверждён» на /master/*, и т.д. — и ни на одном не было
 * возврата в MAX.
 *
 * Здесь закрепляется: на каждом входе — один экран, ни одного запроса к
 * серверу (ни /me, ни данных), кнопка возвращает в MAX. Положительная стража:
 * с initData приложение идёт обычным путём загрузки.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return { ...original, getMe: vi.fn() };
});

import { App } from "./App";
import { getMe } from "./lib/admin-api";
import * as identity from "./lib/identity";
import * as maxSdk from "./lib/max-sdk";

const TITLE = "Открой Ayla из MAX";
const RETURN = "Вернуться в MAX";

const mockedGetMe = vi.mocked(getMe);
let fetchMock: ReturnType<typeof vi.fn>;

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  mockedGetMe.mockReset();
  // /me никогда не отвечает: если App дойдёт до загрузки, он зависнет на
  // заставке, а не упадёт — утверждения ниже различают эти исходы сами.
  mockedGetMe.mockReturnValue(new Promise(() => {}));
  fetchMock = vi.fn(() => new Promise(() => {}));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const ENTRIES = [
  "/",
  "/customer/catalog",
  "/customer/masters/m-1",
  "/customer/masters/m-1/slots",
  "/customer/booking/confirm",
  "/customer/records",
  "/master/dashboard",
  "/admin/team",
];

describe("без initData — экран «Открой Ayla из MAX» на каждом входе", () => {
  it.each(ENTRIES)("%s → экран отказа, ни одного запроса", (path) => {
    vi.spyOn(identity, "channelIdentity").mockReturnValue("no_init_data");

    renderAt(path);

    expect(screen.getByText(TITLE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: RETURN })).toBeInTheDocument();
    expect(screen.queryByText("Попробовать снова")).not.toBeInTheDocument();
    expect(mockedGetMe).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("«Вернуться в MAX» закрывает Mini App", () => {
    vi.spyOn(identity, "channelIdentity").mockReturnValue("no_init_data");
    const close = vi.spyOn(maxSdk, "returnToChat").mockReturnValue("closed");

    renderAt("/");
    fireEvent.click(screen.getByRole("button", { name: RETURN }));

    expect(close).toHaveBeenCalledTimes(1);
  });

  it("по умолчанию, без мока: реальный channelIdentity, моста MAX нет → тот же экран", () => {
    // Без этой проверки явный мок `identified` в App.*-тестах мог бы спрятать
    // поведение по умолчанию. Здесь ничего не подменено: моста `window.WebApp`
    // нет, dev-initData пуст — и App сам приходит к экрану отказа.
    delete (window as unknown as { WebApp?: unknown }).WebApp;
    vi.stubEnv("VITE_DEV_INIT_DATA", "");

    renderAt("/");

    expect(identity.channelIdentity()).toBe("no_init_data");
    expect(screen.getByText(TITLE)).toBeInTheDocument();
    expect(mockedGetMe).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    vi.unstubAllEnvs();
  });
});

describe("с initData — обычный путь (положительная стража)", () => {
  it("App грузит роль через /me и экран отказа не показывает", () => {
    vi.spyOn(identity, "channelIdentity").mockReturnValue("identified");

    renderAt("/");

    expect(mockedGetMe).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(TITLE)).not.toBeInTheDocument();
  });
});
