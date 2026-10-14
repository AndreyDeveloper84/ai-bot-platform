/** DRF-2875 — подпись мастера на экране выбора: оценка только вместе с отзывами. */
import { describe, expect, it } from "vitest";

import type { Master } from "./api";
import { providerMeta } from "./booking-flow";

function master(over: Partial<Master>): Master {
  return {
    id: "m-1",
    name: "Анна Соколова",
    specialization: "",
    bio: "",
    experience: "",
    rating: null,
    photo_url: "",
    ...over,
  } as Master;
}

describe("providerMeta (решение владельца 07.10, п.20)", () => {
  it("есть отзывы — оценка и их число", () => {
    expect(providerMeta(master({ rating: "4.80", review_count: 74 }))).toBe("4.8 · 74 отзыва");
  });

  it("оценка без отзывов — «Пока нет отзывов», числа нет", () => {
    const meta = providerMeta(master({ rating: "4.90", review_count: 0 }));
    expect(meta).toBe("Пока нет отзывов");
    expect(meta).not.toContain("4.9");
  });

  it("ни оценки, ни отзывов — «Пока нет отзывов»", () => {
    expect(providerMeta(master({}))).toBe("Пока нет отзывов");
  });
});
