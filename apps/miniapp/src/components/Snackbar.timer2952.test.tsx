/**
 * Таймер сообщения не перезапускается от перерисовки (DRF-2952).
 *
 * Экраны передают `onTimeout` свежей стрелкой на каждом рендере. Пока
 * обработчик был зависимостью эффекта, любая перерисовка (пришла сеть,
 * сменилось состояние) начинала отсчёт заново — и окно возврата отмены
 * подтверждалось позже серверного.
 */
import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Snackbar } from "./Snackbar";

afterEach(() => {
  vi.useRealTimers();
});

describe("Snackbar — отсчёт идёт от показа, а не от последней перерисовки", () => {
  it("перерисовка с новой стрелкой на 4-й секунде не сдвигает срабатывание с 5-й", () => {
    vi.useFakeTimers();
    const first = vi.fn();
    const second = vi.fn();
    const view = render(<Snackbar visible message="ход дела" durationMs={5000} onTimeout={first} />);

    act(() => {
      vi.advanceTimersByTime(4000);
    });
    view.rerender(<Snackbar visible message="ход дела" durationMs={5000} onTimeout={second} />);
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    // Сработал вовремя — и позван ПОСЛЕДНИЙ объявленный обработчик.
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
  });

  it("новое сообщение получает свой полный срок, а не остаток прежнего (ревью #2458)", () => {
    vi.useFakeTimers();
    const onTimeout = vi.fn();
    const view = render(<Snackbar visible message="первое" durationMs={5000} onTimeout={onTimeout} />);

    act(() => {
      vi.advanceTimersByTime(4000);
    });
    view.rerender(<Snackbar visible message="второе" durationMs={5000} onTimeout={onTimeout} />);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    // Остаток первого срока (1 с) прошёл — второе сообщение ещё живо.
    expect(onTimeout).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(3500);
    });
    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it("скрытое сообщение не срабатывает вовсе", () => {
    vi.useFakeTimers();
    const onTimeout = vi.fn();
    const view = render(<Snackbar visible message="ход дела" durationMs={5000} onTimeout={onTimeout} />);
    view.rerender(<Snackbar visible={false} message="" durationMs={5000} onTimeout={onTimeout} />);
    act(() => {
      vi.advanceTimersByTime(6000);
    });
    expect(onTimeout).not.toHaveBeenCalled();
  });
});
