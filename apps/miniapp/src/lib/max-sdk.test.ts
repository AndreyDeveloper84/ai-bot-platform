import { describe, expect, it } from "vitest";

import { parseStartRoute } from "./max-sdk";

describe("parseStartRoute", () => {
  it("maps pre-existing slugs", () => {
    expect(parseStartRoute("open_catalog")).toBe("/catalog");
    expect(parseStartRoute("open_visits")).toBe("/my-visits");
    expect(parseStartRoute("open_profile")).toBe("/me");
    expect(parseStartRoute("catalog")).toBe("/catalog");
  });

  // DRF-1167: S5 first-action slugs emitted by the welcome skill must
  // resolve — before the fix they returned null (dead deeplink).
  it("maps S5 first-action slugs (DRF-1167)", () => {
    expect(parseStartRoute("open_food_scan")).toBe(
      "/customer/food-scanner/capture",
    );
    expect(parseStartRoute("open_water_add_250")).toBe("/customer/wellness");
    expect(parseStartRoute("open_goal_select")).toBe("/customer/goal-select");
    expect(parseStartRoute("open_home")).toBe("/customer/main");
  });

  it("returns null for empty / unknown / malformed payloads", () => {
    expect(parseStartRoute("")).toBeNull();
    expect(parseStartRoute("open_unknown")).toBeNull();
    expect(parseStartRoute("garbage")).toBeNull();
  });

  it("still accepts the legacy querystring form", () => {
    expect(parseStartRoute("route=visits&ref=ig")).toBe("/my-visits");
  });
});
