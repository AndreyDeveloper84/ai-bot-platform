/**
 * Клиент сохранённого плана (DRF-2876): что значит «плана нет».
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return { ...original, request: vi.fn() };
});

import { ApiError, request } from "./api";
import { getSavedPlan } from "./plan-engine";

const mockedRequest = vi.mocked(request);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("getSavedPlan", () => {
  it("читает план с ручки бота", async () => {
    const plan = { plan_id: "p", steps: [{ step_id: "s-0", label: "Режим сна" }] };
    mockedRequest.mockResolvedValue({ plan });

    expect(await getSavedPlan()).toEqual(plan);
    expect(mockedRequest).toHaveBeenCalledWith("/plan/current");
  });

  it("плана нет — null", async () => {
    mockedRequest.mockResolvedValue({ plan: null });

    expect(await getSavedPlan()).toBeNull();
  });

  it("механизм выключен на сервере — null: экран показывает прежний план", async () => {
    mockedRequest.mockRejectedValue(new ApiError(404, "plan_engine_disabled", "off"));

    expect(await getSavedPlan()).toBeNull();
  });

  it("любой другой отказ пробрасывается — это не «плана нет»", async () => {
    mockedRequest.mockRejectedValue(new ApiError(502, "ayla_unavailable", "down"));

    await expect(getSavedPlan()).rejects.toMatchObject({ slug: "ayla_unavailable" });
  });
});
