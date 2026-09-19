/**
 * Plan Lite — клиент Mini App (DRF-2101, §49).
 *
 * Предмет — провод к `customer/plan-lite`: GET → документ или null; POST —
 * ТОЛЬКО actions (goal_id не шлётся — активную цель знает каталог, PR-1b);
 * DELETE — закрыть.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./max-sdk", () => ({ getInitData: () => "init-data-2101" }));

import {
  closePlanLite,
  createPlanLite,
  getPlanLite,
  getPlanLiteProposal,
  type PlanLite,
  type PlanLiteActionSpec,
  type PlanLiteProposal,
} from "./plan-lite";

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

describe("plan-lite: предложение из шаблона (DRF-2123, План-A)", () => {
  const PROPOSAL: PlanLiteProposal = {
    goal_key: "self_care",
    why: "Забота о себе — это регулярность, а не подвиг.",
    template_version: 2,
    actions: [
      { action_type: "book_service", cadence: "per_2_weeks", target_count: 1 },
      { action_type: "log_water", cadence: "per_day", target_count: 6 },
    ],
  };

  it("proposal — GET /plan-lite/proposal, документ как отдал сервер", async () => {
    fetchMock.mockResolvedValueOnce(respond(200, { proposal: PROPOSAL }));
    expect(await getPlanLiteProposal()).toEqual(PROPOSAL);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/customer\/plan-lite\/proposal$/);
    expect(init.method ?? "GET").toBe("GET");
  });

  it("proposal — 404 no_template / no_active_goal доезжают слагами", async () => {
    fetchMock.mockResolvedValueOnce(respond(404, { error: "no_template", detail: "none" }));
    await expect(getPlanLiteProposal()).rejects.toMatchObject({ status: 404, slug: "no_template" });
    fetchMock.mockResolvedValueOnce(respond(404, { error: "no_active_goal", detail: "none" }));
    await expect(getPlanLiteProposal()).rejects.toMatchObject({ status: 404, slug: "no_active_goal" });
  });

  it("create с template_version — ключ в теле рядом с actions", async () => {
    fetchMock.mockResolvedValueOnce(respond(201, { plan_lite: PLAN }));

    await createPlanLite(PROPOSAL.actions, 2);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body).toEqual({ actions: PROPOSAL.actions, template_version: 2 });
  });

  it("create без template_version — ключа в теле нет (не null)", async () => {
    fetchMock.mockResolvedValueOnce(respond(201, { plan_lite: PLAN }));

    await createPlanLite(PROPOSAL.actions);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.actions).toEqual(PROPOSAL.actions);
    expect(Object.keys(body)).toEqual(["actions"]);
  });
});
