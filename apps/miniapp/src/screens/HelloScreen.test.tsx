/**
 * DRF-1481 — fallback-экран не раздаёт старое поколение.
 *
 * `HelloScreen` стоит на двух входах: отказ `/decision-context` на
 * корне и catch-all `*`. До уборки его кнопки были единственной дверью
 * в прошлое поколение: «Записаться» вела в старый `/catalog`, «Мои
 * записи» — в старое `/my-visits`. Теперь обе ведут в каноническое
 * `/customer/*`, и этот файл — тому замер: маршрут назначения
 * проверяется по тому, ЧТО смонтировалось, а отрицательная половина
 * стоит рядом с положительной на тех же данных.
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
    signalReady: vi.fn(),
    setBackButton: vi.fn(),
    onBackButton: vi.fn(() => () => undefined),
  };
});

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, authVerify: vi.fn() };
});

import { authVerify, type AuthVerifyResponse } from "../lib/api";
import { HelloScreen } from "./HelloScreen";

const mockedVerify = vi.mocked(authVerify);

const OK: AuthVerifyResponse = {
  user: {
    id: "u-1",
    channel_user_id: "c-1",
    display_name: "Ольга",
    client_name: "Ольга",
  },
  tenant: { slug: "demo", name: "Демо-салон", timezone: "Europe/Moscow" },
};

function renderHello() {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<HelloScreen />} />
        <Route path="/customer/catalog" element={<div>НОВЫЙ КАТАЛОГ</div>} />
        <Route path="/catalog" element={<div>СТАРЫЙ КАТАЛОГ</div>} />
        <Route path="/customer/records" element={<div>НОВЫЕ ЗАПИСИ</div>} />
        <Route path="/my-visits" element={<div>СТАРЫЕ ЗАПИСИ</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedVerify.mockResolvedValue(OK);
});

describe("HelloScreen — кнопки ведут только в /customer/* (DRF-1481)", () => {
  it("«Записаться» — в канонический каталог", async () => {
    renderHello();
    // Присутствие: приветствие загрузилось, кнопка на экране.
    await userEvent.click(
      await screen.findByRole("button", { name: "Записаться" }),
    );

    expect(await screen.findByText("НОВЫЙ КАТАЛОГ")).toBeInTheDocument();
    expect(screen.queryByText("СТАРЫЙ КАТАЛОГ")).toBeNull();
  });

  it("«Мои записи» — в канонический список записей", async () => {
    renderHello();
    await userEvent.click(
      await screen.findByRole("button", { name: "Мои записи" }),
    );

    expect(await screen.findByText("НОВЫЕ ЗАПИСИ")).toBeInTheDocument();
    expect(screen.queryByText("СТАРЫЕ ЗАПИСИ")).toBeNull();
  });
});
