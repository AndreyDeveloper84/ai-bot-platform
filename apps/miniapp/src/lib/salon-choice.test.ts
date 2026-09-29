import { beforeEach, describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  SALON_CHOICE_HEADER,
  applySalonChoiceHeader,
  getSalonChoice,
  salonChoiceTenantsFrom,
  setSalonChoice,
} from "./salon-choice";

describe("salon choice (DRF-1766)", () => {
  beforeEach(() => {
    setSalonChoice(null);
    try {
      sessionStorage.clear();
    } catch {
      /* jsdom always has it */
    }
  });

  it("reads the tenants only from a 409 salon_choice_required", () => {
    const tenants = salonChoiceTenantsFrom(
      new ApiError(409, "salon_choice_required", "choose", {
        tenants: [
          { slug: "a", name: "Салон А" },
          { slug: "b", name: "Салон Б" },
        ],
      }),
    );
    expect(tenants).toEqual([
      { slug: "a", name: "Салон А" },
      { slug: "b", name: "Салон Б" },
    ]);
  });

  it("is not fooled by another 409 or by a 409 without a list", () => {
    expect(salonChoiceTenantsFrom(new ApiError(409, "quote_changed", "x", {}))).toBeNull();
    expect(salonChoiceTenantsFrom(new ApiError(409, "salon_choice_required", "x", {}))).toBeNull();
    expect(salonChoiceTenantsFrom(new ApiError(404, "user_not_registered", "x"))).toBeNull();
    expect(salonChoiceTenantsFrom(new Error("network"))).toBeNull();
  });

  it("puts the stored choice on the wire and nothing when there is none", () => {
    const none = new Headers();
    applySalonChoiceHeader(none);
    expect(none.has(SALON_CHOICE_HEADER)).toBe(false);

    setSalonChoice("b");
    const some = new Headers();
    applySalonChoiceHeader(some);
    expect(some.get(SALON_CHOICE_HEADER)).toBe("b");
    expect(getSalonChoice()).toBe("b");
  });

  it("forgets the choice on null", () => {
    setSalonChoice("b");
    setSalonChoice(null);
    expect(getSalonChoice()).toBeNull();
  });
});
