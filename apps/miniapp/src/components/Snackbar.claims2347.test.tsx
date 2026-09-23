/**
 * Красное, которое доказывает механизм (DRF-2347).
 *
 * DRF-2346 починен (#2024), и настоящего живого случая на экране больше нет —
 * значит нужен искусственный: сообщение утверждает исход, которому прочитанное
 * значение противоречит. Сторож обязан остановить это **там, где сообщение
 * рождается**, а не позже и не в отчёте.
 *
 * Узел, родившийся зелёным, ничего не доказывает: здесь красное — предмет
 * проверки, поэтому оно вызвано нарочно и поймано `toThrow`.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Snackbar } from "./Snackbar";
import { markRead, type Read } from "../lib/claims";

function read<T extends object>(body: T): Read<T> {
  return markRead(body, { source: "/bookings/b-1/cancel/", status: 200 });
}

describe("примитив вывода и утверждение", () => {
  it("утверждение, подтверждённое прочитанным, выводится", () => {
    expect(() =>
      render(
        <Snackbar
          visible
          message="Запись отменена"
          claim={{ outcome: "booking_cancelled", from: read({ status: "cancelled" }) }}
        />,
      ),
    ).not.toThrow();
  });

  it("утверждение, которому прочитанное противоречит, останавливает", () => {
    expect(() =>
      render(
        <Snackbar
          visible
          message="Запись отменена"
          claim={{ outcome: "booking_cancelled", from: read({ status: "cancel_requested" }) }}
        />,
      ),
    ).toThrow(/cancel_requested/);
  });

  it("утверждение без доказательства останавливает", () => {
    expect(() =>
      render(
        <Snackbar
          visible
          message="Запись отменена"
          claim={{
            outcome: "booking_cancelled",
            // Собрано на месте: клиента никто не звал.
            from: { status: "cancelled" } as unknown as Read<{ status: string }>,
          }}
        />,
      ),
    ).toThrow(/без метки/);
  });

  it("сообщение без утверждения выводится как прежде", () => {
    // Положительная пара к трём выше: сообщения о ходе дела, ошибке и
    // запросе признака не несут — доказывать им нечего.
    expect(() => render(<Snackbar visible message="Отменяю запись…" />)).not.toThrow();
  });
});
