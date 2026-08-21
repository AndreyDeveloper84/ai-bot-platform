/** DRF-1224 — the 1..5 domain rule for a displayable master rating. */
import { describe, expect, it } from "vitest";

import { publicRating } from "./rating";

describe("publicRating", () => {
  it("hides the pilot's zero rating", () => {
    // The exact wire value behind «★ 0.00» on the pilot.
    expect(publicRating("0.00")).toBeNull();
  });

  it("hides an absent rating", () => {
    expect(publicRating(null)).toBeNull();
    expect(publicRating(undefined)).toBeNull();
    expect(publicRating("")).toBeNull();
  });

  it("hides anything below the 1..5 domain", () => {
    expect(publicRating("0")).toBeNull();
    expect(publicRating(0)).toBeNull();
    expect(publicRating("0.99")).toBeNull();
  });

  it("hides an unparseable rating instead of rendering NaN", () => {
    expect(publicRating("—")).toBeNull();
  });

  it("keeps a real rating", () => {
    expect(publicRating("4.90")).toBe(4.9);
    expect(publicRating("1.00")).toBe(1);
    expect(publicRating(5)).toBe(5);
  });
});
