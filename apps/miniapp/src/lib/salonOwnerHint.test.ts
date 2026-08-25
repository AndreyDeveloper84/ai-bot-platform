import { describe, expect, it } from "vitest";

import { DEFAULT_SALON_OWNER_HINT, salonOwnerHint } from "./salonOwnerHint";

describe("salonOwnerHint", () => {
  it("uses the salon name when there is one", () => {
    expect(salonOwnerHint("Студия Карина")).toBe("Студия Карина");
  });

  it("trims surrounding whitespace", () => {
    expect(salonOwnerHint("  GoBeauty  ")).toBe("GoBeauty");
  });

  it("falls back when the name is missing, empty or blank", () => {
    for (const value of [null, undefined, "", "   "]) {
      expect(salonOwnerHint(value)).toBe(DEFAULT_SALON_OWNER_HINT);
    }
  });

  it("never returns an empty string", () => {
    expect(salonOwnerHint("")).not.toBe("");
  });
});
