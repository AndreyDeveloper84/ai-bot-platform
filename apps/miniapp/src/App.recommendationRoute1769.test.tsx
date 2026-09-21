/**
 * Адрес карточки и вход по ссылке из чата (DRF-1769, К-3 N3).
 *
 * У карточки появляется СВОЙ адрес, и `reco_<uuid>` ведёт на него, а не
 * в каталог. До этого среза ссылка вела в каталог (N7): id ехал ради
 * провенанса, а показать карточку на экране было нечем. Теперь есть —
 * и ссылка с именем карточки открывает карточку.
 *
 * Провенанс от этого не страдает: `entry_point` собирается из
 * start-payload, а не из маршрута, — узел ниже это и держит.
 */
import { describe, expect, it } from "vitest";

import { RECO_PAYLOAD_PREFIX, parseStartRoute } from "./lib/max-sdk";
import { resolveEntryPoint } from "./lib/pending-booking-intent";

const ID = "11111111-1111-1111-1111-111111111111";

describe("ссылка на карточку", () => {
  it("ведёт на адрес карточки, а не в каталог", () => {
    expect(parseStartRoute(`${RECO_PAYLOAD_PREFIX}${ID}`)).toBe(`/customer/recommendation/${ID}`);
  });

  it("объявила себя ссылкой и не является — не маршрут, а отказ", () => {
    expect(parseStartRoute(`${RECO_PAYLOAD_PREFIX}not-a-uuid`)).toBeNull();
  });

  it("провенанс остаётся прежним: он из payload, а не из маршрута", () => {
    expect(resolveEntryPoint(null, `${RECO_PAYLOAD_PREFIX}${ID}`)).toBe(
      `deep_link:${RECO_PAYLOAD_PREFIX}${ID}`,
    );
  });
});
