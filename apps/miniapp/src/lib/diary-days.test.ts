/**
 * Дневник за неделю — клиент Mini App (DRF-2099).
 *
 * Предмет — провод (куда идёт запрос и с чем) и календарная арифметика
 * стрелок: предел «раньше» — 28 дней по пределу ручки, «позже» — не
 * дальше сегодня; даты — строки из ответа, без пояса устройства.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./max-sdk", () => ({
  getInitData: () => "init-data-2099",
}));

import {
  canGoEarlier,
  canGoLater,
  dayLabel,
  earlierWeek,
  earliestFrom,
  getDiaryDay,
  getDiaryDays,
  laterWeek,
  periodLabel,
  shiftDays,
  weekEndingAt,
} from "./diary-days";

const fetchMock = vi.fn();

function respond(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

const WEEK = {
  timezone: "Europe/Moscow",
  from: "2026-09-13",
  to: "2026-09-19",
  days: [{ date: "2026-09-13", meals_count: 0, kcal: null, has_entries: false }],
  nutrition_numbers_hidden: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

describe("diary-days: провод", () => {
  it("без периода — GET /diary/days без параметров; ответ как отдал сервер", async () => {
    fetchMock.mockResolvedValueOnce(respond(200, WEEK));

    const res = await getDiaryDays();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/customer\/diary\/days$/);
    expect(init.method ?? "GET").toBe("GET");
    expect(res).toEqual(WEEK);
  });

  it("с периодом — from/to в строке запроса", async () => {
    fetchMock.mockResolvedValueOnce(respond(200, WEEK));

    await getDiaryDays("2026-09-06", "2026-09-12");

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toMatch(/\/diary\/days\?from=2026-09-06&to=2026-09-12$/);
  });

  it("день — GET /diary/day?date=", async () => {
    const day = { date: "2026-09-14", calories_total: 640, entries: [] };
    fetchMock.mockResolvedValueOnce(respond(200, day));

    const res = await getDiaryDay("2026-09-14");

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toMatch(/\/diary\/day\?date=2026-09-14$/);
    expect(res).toEqual(day);
  });
});

describe("diary-days: календарь стрелок", () => {
  it("сдвиг по календарю через границу месяца и года", () => {
    expect(shiftDays("2026-09-19", -7)).toBe("2026-09-12");
    expect(shiftDays("2026-03-02", -7)).toBe("2026-02-23");
    expect(shiftDays("2027-01-03", -7)).toBe("2026-12-27");
    expect(weekEndingAt("2026-09-19")).toEqual({ from: "2026-09-13", to: "2026-09-19" });
  });

  it("«раньше» — ровно четыре недели (28 дней) от сегодня, дальше кнопка неактивна", () => {
    const today = "2026-09-19";
    expect(earliestFrom(today)).toBe("2026-08-23");
    // текущая → 2-я → 3-я → 4-я: три шага назад разрешены
    expect(canGoEarlier("2026-09-13", today)).toBe(true);
    expect(canGoEarlier("2026-09-06", today)).toBe(true);
    expect(canGoEarlier("2026-08-30", today)).toBe(true);
    // четвёртая неделя начинается ровно на пределе — дальше нельзя
    expect(canGoEarlier("2026-08-23", today)).toBe(false);
    expect(earlierWeek("2026-09-13")).toEqual({ from: "2026-09-06", to: "2026-09-12" });
  });

  it("«позже» — не дальше сегодня", () => {
    const today = "2026-09-19";
    expect(canGoLater("2026-09-19", today)).toBe(false);
    expect(canGoLater("2026-09-12", today)).toBe(true);
    expect(laterWeek("2026-09-12", today)).toEqual({ from: "2026-09-13", to: "2026-09-19" });
    // неполный шаг упирается в сегодня, а не перескакивает его
    expect(laterWeek("2026-09-15", today)).toEqual({ from: "2026-09-13", to: "2026-09-19" });
  });

  it("подписи — день недели и число из строки даты, не из часов устройства", () => {
    expect(dayLabel("2026-09-14")).toBe("Пн, 14.09");
    expect(dayLabel("2026-09-20")).toBe("Вс, 20.09");
    expect(periodLabel("2026-09-13", "2026-09-19")).toBe("13.09 – 19.09");
  });
});
