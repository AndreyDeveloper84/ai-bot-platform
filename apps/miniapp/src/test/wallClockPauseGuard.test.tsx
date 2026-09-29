/**
 * DRF-2624 — сторож паузы по настоящим часам в теле теста (`timerLedger`).
 *
 * Обе стороны на одном механизме: пауза по настоящим часам в тесте падает с
 * адресом; прыжок по фейковым часам — образец ожидания ВРЕМЕНИ (DRF-2616,
 * `AdminNewBookingScreen`) — зелёный; короткая задержка и пауза кода
 * приложения — не предмет сторожа.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { getRecentActivity } from "../lib/customer-wellness";

describe("сторож паузы по часам — положительный контроль", () => {
  it("пауза по настоящим часам в тесте падает с адресом строки", async () => {
    await expect(new Promise((r) => setTimeout(r, 50))).rejects.toThrow(
      /DRF-2624: пауза по настоящим часам в тесте — test\/wallClockPauseGuard\.test\.tsx:\d+, 50 мс/,
    );
  });
});

describe("сторож паузы по часам — законное ожидание не задевается", () => {
  afterEach(() => {
    vi.useRealTimers();
    window.history.replaceState(null, "", "/");
  });

  it("та же пауза на фейковых часах с прыжком — зелёная (образец DRF-2616)", async () => {
    vi.useFakeTimers();
    const pause = new Promise<void>((r) => setTimeout(r, 1_000));
    await vi.advanceTimersByTimeAsync(1_000);
    await expect(pause).resolves.toBeUndefined();
  });

  it("короткая задержка — не пауза по часам", async () => {
    await expect(new Promise<void>((r) => setTimeout(r, 0))).resolves.toBeUndefined();
  });

  it("пауза в коде приложения (заглушка customer-wellness, 350 мс) — не предмет сторожа", async () => {
    window.history.replaceState(null, "", "/?stub=default");
    const activity = await getRecentActivity();
    expect(activity).toBeTruthy();
  });
});
