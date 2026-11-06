/** DRF-1224 — the 1..5 domain rule for a displayable master rating. */
import { describe, expect, it } from "vitest";

import { NO_REVIEWS_LABEL, publicRating } from "./rating";

describe("publicRating", () => {
  it("hides the pilot's zero rating", () => {
    // The exact wire value behind «★ 0.00» on the pilot.
    expect(publicRating("0.00", 12)).toBeNull();
  });

  it("hides an absent rating", () => {
    expect(publicRating(null, 12)).toBeNull();
    expect(publicRating(undefined, 12)).toBeNull();
    expect(publicRating("", 12)).toBeNull();
  });

  it("hides anything below the 1..5 domain", () => {
    expect(publicRating("0", 12)).toBeNull();
    expect(publicRating(0, 12)).toBeNull();
    expect(publicRating("0.99", 12)).toBeNull();
  });

  it("hides an unparseable rating instead of rendering NaN", () => {
    expect(publicRating("—", 12)).toBeNull();
  });

  it("keeps a real rating", () => {
    expect(publicRating("4.90", 12)).toBe(4.9);
    expect(publicRating("1.00", 12)).toBe(1);
    expect(publicRating(5, 12)).toBe(5);
  });
});

describe("publicRating — оценка только вместе с отзывами (DRF-2875)", () => {
  // Решение владельца 07.10, п.20. На пилоте: импортированная «4.9» при нуле отзывов.
  it("прячет оценку без отзывов", () => {
    expect(publicRating("4.90", 0)).toBeNull();
    expect(publicRating("4.90", null)).toBeNull();
    expect(publicRating("4.90", undefined)).toBeNull();
    expect(publicRating(5, Number.NaN)).toBeNull();
  });

  it("показывает оценку, когда за ней есть хотя бы один отзыв", () => {
    expect(publicRating("4.90", 1)).toBe(4.9);
  });

  it("слова владельца — дословно", () => {
    expect(NO_REVIEWS_LABEL).toBe("Пока нет отзывов");
  });
});
