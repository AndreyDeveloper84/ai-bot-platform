/**
 * Серверный `detail` пишется в журнал один раз на клиент (DRF-2451).
 *
 * DRF-2446 убрал `detail` с общего хвоста ошибки, DRF-2451 — с двадцати
 * пяти экранов, у которых уже была согласованная фраза. Диагностика при
 * этом обязана остаться: иначе через неделю `detail` вернут на экран,
 * «чтобы было видно».
 *
 * Журнал стоит там, где `ApiError` рождается, а не на каждом экране, —
 * поэтому узел здесь, а не двадцать пять узлов по экранам.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./max-sdk", () => ({ getInitData: () => "init-data-2451" }));

import { ApiError, request } from "./api";

const fetchMock = vi.fn();

function respond(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

describe("журнал вместо экрана", () => {
  it("отказ с detail — строка в журнале со статусом и слагом", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchMock.mockResolvedValueOnce(
      respond(404, { error: "not_found", detail: "booking not found" }),
    );

    await expect(request("/customer/bookings/b-1")).rejects.toBeInstanceOf(ApiError);

    expect(warn).toHaveBeenCalledTimes(1);
    const [line] = warn.mock.calls[0] ?? [];
    expect(String(line)).toContain("booking not found");
    expect(String(line)).toContain("not_found");
    expect(String(line)).toContain("404");
    warn.mockRestore();
  });

  it("успех не пишет в журнал ничего", async () => {
    // Положительная половина: иначе «пишется при отказе» было бы правдой и
    // о клиенте, который пишет на каждый ответ подряд.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchMock.mockResolvedValueOnce(respond(200, { ok: true }));

    await request("/customer/bookings");

    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("отказ без detail не пишет пустую строку", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchMock.mockResolvedValueOnce(respond(500, { error: "http_error", detail: "" }));

    await expect(request("/customer/bookings")).rejects.toBeInstanceOf(ApiError);

    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });
});
