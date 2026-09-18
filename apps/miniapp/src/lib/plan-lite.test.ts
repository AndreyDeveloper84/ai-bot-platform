/**
 * Plan Lite — клиент Mini App (DRF-2101, §49).
 *
 * Предмет — провод к `customer/plan-lite`: GET → документ или null; POST —
 * ТОЛЬКО actions (goal_id не шлётся — активную цель знает каталог, PR-1b);
 * DELETE — закрыть.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./max-sdk", () => ({ getInitData: () => "init-data-2101" }));

import { closePlanLite, createPlanLite, getPlanLite, type PlanLite, type PlanLiteActionSpec } from "./plan-lite";

const fetchMock = vi.fn();

const PLAN: PlanLite = {
  plan_id: "p-1",
  goal_key: "tone_up",
  actions: [
    {
      action_type: "log_food",
      cadence: "per_week",
      target_count: 3,
      done_count: 1,
      bucket: { start: "2026-09-14", end: "2026-09-21" },
    },
  ],
};

function respond(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

describe("plan-lite: провод к customer/plan-lite", () => {
  it("get — GET /plan-lite, документ как отдал сервер; null — плана нет", async () => {
    fetchMock.mockResolvedValueOnce(respond(200, { plan_lite: PLAN }));
    expect(await getPlanLite()).toEqual(PLAN);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/customer\/plan-lite$/);
    expect(init.method ?? "GET").toBe("GET");

    fetchMock.mockResolvedValueOnce(respond(200, { plan_lite: null }));
    expect(await getPlanLite()).toBeNull();
  });

  it("create — POST только с actions, без goal_id", async () => {
    fetchMock.mockResolvedValueOnce(respond(201, { plan_lite: PLAN }));
    const actions: PlanLiteActionSpec[] = [{ action_type: "log_food", cadence: "per_week", target_count: 3 }];

    const plan = await createPlanLite(actions);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/customer\/plan-lite$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ actions });
    expect(plan).toEqual(PLAN);
  });

  it("close — DELETE /plan-lite", async () => {
    fetchMock.mockResolvedValueOnce(respond(200, { closed: true }));
    await closePlanLite();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/customer\/plan-lite$/);
    expect(init.method).toBe("DELETE");
  });
});
