/**
 * Метка прочитанного доходит до ВЛОЖЕННОЙ записи (DRF-2952).
 *
 * Клиент API метит тело ответа целиком, а экраны утверждают исход по записи
 * внутри него — `{ booking }`, `{ new_booking }`. До этой правки у вложенной
 * записи метки не было: `verifyClaim` на ней отвечал «не прочитано» всегда, и
 * экран, ветвящийся по вердикту, в бою не показал бы исход никогда. В тестах
 * экранов это было невидимо — они метят запись руками.
 *
 * Поэтому здесь НАСТОЯЩИЙ клиент: подменён только `fetch`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelBookingConfirm,
  cancelBookingRequest,
  fetchBooking,
  rescheduleBookingConfirm,
} from "./api";
import { carryProof, lastReadSeq, markRead, proofOf, readOf, verifyClaim } from "./claims";

function answer(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("запись из ответа клиента несёт метку", () => {
  it("отмена: `booking` — доказательство; исход сверяется со статусом", async () => {
    answer({ booking: { id: "b-1", status: "cancelled" } });
    const { booking } = await cancelBookingConfirm("b-1");

    const from = readOf(booking);
    expect(from).not.toBeNull();
    expect(verifyClaim({ outcome: "booking_cancelled", from: from! }).ok).toBe(true);
  });

  it("запрос отмены на пути Ayla: статус прежний — «отменена» не доказана, хотя ответ прочитан", async () => {
    answer({ booking: { id: "b-1", status: "confirmed" } });
    const { booking } = await cancelBookingRequest("b-1");

    const from = readOf(booking);
    // Наличие: чтение было. Дефект класса DRF-2346 — не в отсутствии чтения.
    expect(from).not.toBeNull();
    expect(verifyClaim({ outcome: "booking_cancelled", from: from! }).ok).toBe(false);
  });

  it("чтение записи и перенос: метку несут `booking` и `new_booking`", async () => {
    answer({ booking: { id: "b-1", status: "confirmed" } });
    expect(proofOf((await fetchBooking("b-1")).booking)?.source).toBe("/bookings/b-1");

    answer({
      old_booking: { id: "b-1", status: "rescheduled" },
      new_booking: { id: "b-2", status: "confirmed" },
    });
    const { new_booking } = await rescheduleBookingConfirm("b-1", {
      new_master_id: "m",
      new_service_id: "s",
      new_visit_at: "2026-09-10T12:00:00+03:00",
    });
    const from = readOf(new_booking);
    expect(from && verifyClaim({ outcome: "booking_rescheduled", from }).ok).toBe(true);
  });

  it("чтение до нажатия исход нажатия не доказывает", async () => {
    answer({ booking: { id: "b-1", status: "cancelled" } });
    const { booking } = await fetchBooking("b-1");
    const pressedAt = lastReadSeq();

    const from = readOf(booking);
    expect(verifyClaim({ outcome: "booking_cancelled", from: from!, after: pressedAt }).ok).toBe(false);
    // Пара: то же значение без границы «после нажатия» исход подтверждает.
    expect(verifyClaim({ outcome: "booking_cancelled", from: from! }).ok).toBe(true);
  });
});

describe("carryProof нельзя использовать как подделку", () => {
  it("конверт без метки вложенному ничего не даёт", () => {
    const nested = { status: "cancelled" };
    carryProof({ booking: nested }, nested);
    expect(readOf(nested)).toBeNull();
  });

  it("метка конверта переходит на вложенный объект с тем же номером чтения", () => {
    const nested = { status: "cancelled" };
    const envelope = markRead({ booking: nested }, { source: "/x", status: 200 });
    carryProof(envelope, nested);
    expect(proofOf(nested)).toEqual(proofOf(envelope));
  });
});
