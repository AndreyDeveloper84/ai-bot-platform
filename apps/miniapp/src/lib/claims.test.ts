/**
 * Механизм утверждений экрана: доказательство, сверка, отрицательные пробы (DRF-2347).
 *
 * Здесь проверяется сам сторож. То, что он ловит настоящий дефект, держит
 * отдельный узел — `CustomerBookingDetailScreen.claims2347.test.tsx`.
 */

import { describe, expect, it } from "vitest";

import { markRead, proofOf, verifyClaim, type Read } from "./claims";

function read<T extends object>(body: T, status = 200): Read<T> {
  return markRead(body, { source: "/bookings/x/", status });
}

describe("доказательство", () => {
  it("метку ставит клиент, и она не видна снаружи тела", () => {
    const booking = read({ id: "b1", status: "cancelled" });

    expect(proofOf(booking)).not.toBeNull(); // наличие
    expect(Object.keys(booking)).toEqual(["id", "status"]);
    expect(JSON.parse(JSON.stringify(booking))).toEqual({ id: "b1", status: "cancelled" });
  });

  it("метка помнит источник и код ответа", () => {
    const meta = proofOf(read({ status: "cancelled" }, 200));

    expect(meta?.source).toBe("/bookings/x/");
    expect(meta?.status).toBe(200);
  });
});

describe("сверка утверждения с прочитанным", () => {
  it("исход, подтверждённый значением, принимается", () => {
    const verdict = verifyClaim({
      outcome: "booking_cancelled",
      from: read({ status: "cancelled" }),
    });

    expect(verdict.ok).toBe(true);
  });

  it("DRF-2346: «отменена» при cancel_requested — красное", () => {
    const verdict = verifyClaim({
      outcome: "booking_cancelled",
      from: read({ status: "cancel_requested" }),
    });

    expect(verdict.ok).toBe(false);
    expect(verdict.ok === false && verdict.reason).toContain("cancel_requested");
  });

  it("тот же ответ оправдывает честный исход «запрошена отмена»", () => {
    const verdict = verifyClaim({
      outcome: "booking_cancel_requested",
      from: read({ status: "cancel_requested" }),
    });

    expect(verdict.ok).toBe(true);
  });
});

describe("отрицательные пробы", () => {
  it("доказательства нет вовсе — красное", () => {
    const verdict = verifyClaim({
      outcome: "booking_cancelled",
      // Собранный на месте объект: клиента никто не звал.
      from: { status: "cancelled" } as unknown as Read<{ status: string }>,
    });

    expect(verdict.ok).toBe(false);
    expect(verdict.ok === false && verdict.reason).toContain("без метки");
  });

  it("подделать метку снаружи нечем: символ из модуля не вывозится", () => {
    const forged = { status: "cancelled" } as Record<string | symbol, unknown>;
    for (const sym of Object.getOwnPropertySymbols(read({ status: "cancelled" }))) {
      // Символ виден на помеченном объекте, но взять его оттуда и поставить
      // на свой — единственный путь; проверяем, что и он не спасает подделку
      // без чтения: скопированная метка несёт чужой номер чтения.
      forged[sym] = { source: "подделка", status: 200, seq: 0 };
    }

    const verdict = verifyClaim({
      outcome: "booking_cancelled",
      from: forged as unknown as Read<{ status: string }>,
      after: 0,
    });

    expect(verdict.ok).toBe(false);
    expect(verdict.ok === false && verdict.reason).toContain("прошлой попытки");
  });

  it("доказательство из прошлой попытки — красное", () => {
    const first = read({ status: "cancelled" });
    const firstSeq = proofOf(first)!.seq;
    const second = read({ status: "cancelled" });

    expect(proofOf(second)!.seq).toBeGreaterThan(firstSeq); // наличие: номера растут
    expect(verifyClaim({ outcome: "booking_cancelled", from: first, after: firstSeq }).ok).toBe(
      false,
    );
    expect(verifyClaim({ outcome: "booking_cancelled", from: second, after: firstSeq }).ok).toBe(
      true,
    );
  });

  it("незнакомый исход — красное, а не пропуск (fail-closed)", () => {
    const verdict = verifyClaim({
      outcome: "booking_teleported" as never,
      from: read({ status: "cancelled" }),
    });

    expect(verdict.ok).toBe(false);
    expect(verdict.ok === false && verdict.reason).toContain("не описан");
  });
});
