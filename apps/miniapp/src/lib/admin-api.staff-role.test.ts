/**
 * DRF-2273 — `changeStaffRole` against a stubbed `fetch`, not a mocked
 * module: what is pinned is the wire, which the screen tests mock away.
 *
 *   - the request goes to `staff/role/` as a POST with exactly the body
 *     the view reads;
 *   - a refusal keeps `details` — the catalog's «что сделать» rides in
 *     `details.hint`, and the admin wrapper used to drop it, which only a
 *     screenshot of the real bundle showed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("./max-sdk")>();
  return { ...original, getInitData: () => "test-init-data" };
});

import { changeStaffRole } from "./admin-api";
import { ApiError } from "./api";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("changeStaffRole", () => {
  it("posts the person and the role to staff/role/", async () => {
    const fetchMock = vi.fn(async () =>
      json(200, { role: "receptionist", previous_roles: ["admin"] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await changeStaffRole({ bot_user_id: "u-8", role: "receptionist" });

    expect(res.previous_roles).toEqual(["admin"]);
    const [url, init] = fetchMock.mock.calls.at(0) as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/admin/staff/role/");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      bot_user_id: "u-8",
      role: "receptionist",
    });
  });

  it("keeps the refusal's details, so the hint reaches the screen", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json(409, {
          error: "catalog_admin_link_refused",
          detail: "transport_error",
          details: { hint: "каталог недоступен — повторить позже" },
        }),
      ),
    );

    const err = await changeStaffRole({ bot_user_id: "u-8", role: "admin" }).catch(
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).slug).toBe("catalog_admin_link_refused");
    expect((err as ApiError).details?.hint).toBe("каталог недоступен — повторить позже");
  });
});
