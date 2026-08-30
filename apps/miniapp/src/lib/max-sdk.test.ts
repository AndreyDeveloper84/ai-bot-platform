import { describe, expect, it } from "vitest";

import {
  MASTER_INVITE_PAYLOAD_PREFIX,
  MASTER_ONBOARDING_PATH,
  parseStartRoute,
} from "./max-sdk";

describe("parseStartRoute", () => {
  it("maps pre-existing slugs", () => {
    expect(parseStartRoute("open_catalog")).toBe("/customer/catalog");
    expect(parseStartRoute("open_visits")).toBe("/customer/records");
    expect(parseStartRoute("open_profile")).toBe("/customer/profile");
    expect(parseStartRoute("catalog")).toBe("/customer/catalog");
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
    expect(parseStartRoute("route=visits&ref=ig")).toBe("/customer/records");
  });

  // DRF-1349: the master-invite payload. Unlike every slug above it is a
  // PREFIX plus a parameter, not an exact key — the first such case in
  // this file. Which is exactly why it is matched strictly: "anything
  // starting with master_invite_" would forward arbitrary attacker-chosen
  // text into a query string.
  describe("master invite payload (DRF-1349)", () => {
    const uuid = "3f2a1b4c-5d6e-4f70-8912-a3b4c5d6e7f8";

    it("resolves a well-formed invite payload to the onboarding route", () => {
      expect(parseStartRoute(`${MASTER_INVITE_PAYLOAD_PREFIX}${uuid}`)).toBe(
        `${MASTER_ONBOARDING_PATH}?token=${uuid}`,
      );
    });

    it("rejects anything that is not a UUID after the prefix", () => {
      // The positive case above is what makes these meaningful: without
      // it, every one of these would also pass on a parser that always
      // returns null.
      expect(parseStartRoute(`${MASTER_INVITE_PAYLOAD_PREFIX}`)).toBeNull();
      expect(parseStartRoute(`${MASTER_INVITE_PAYLOAD_PREFIX}nope`)).toBeNull();
      expect(
        parseStartRoute(`${MASTER_INVITE_PAYLOAD_PREFIX}${uuid}extra`),
      ).toBeNull();
      expect(
        parseStartRoute(`${MASTER_INVITE_PAYLOAD_PREFIX}${uuid}&x=1`),
      ).toBeNull();
      // A path traversal / open-redirect attempt must not survive either.
      expect(
        parseStartRoute(`${MASTER_INVITE_PAYLOAD_PREFIX}../../admin/team`),
      ).toBeNull();
    });

    it("does not swallow the legacy querystring form", () => {
      // The invite branch runs before the `=` fallback, so guard that it
      // did not shadow it.
      expect(parseStartRoute("route=profile")).toBe("/customer/profile");
    });
  });
});
