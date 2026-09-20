/**
 * `salon-readiness.ts` (DRF-2117, карточка «Готовность — N проблем») — правило
 * карточки по контракту ayla-22 (#1878) и строка сводки «Сегодня».
 *
 * Правило: `source_problem != null` → «не удалось проверить»; иначе `ready`
 * → «салон готов»; иначе N = problems.length; при N == 0 и `unknown` —
 * «не удалось проверить». Число — только из ответа.
 */
import { describe, expect, it } from "vitest";

import type { SalonReadinessResponse } from "./admin-api";
import {
  attentionLine,
  checkedAtLabel,
  problemsLabel,
  readinessCardText,
  readinessState,
} from "./salon-readiness";

function doc(over: Partial<SalonReadinessResponse> = {}): SalonReadinessResponse {
  return {
    ready: true,
    unknown: false,
    source_problem: null,
    checked_at: "2026-09-20T09:00:00+00:00",
    masters_total: 3,
    problems: [],
    limits: [],
    ...over,
  };
}

const anna = {
  master: { id: "m-1", name: "Анна" },
  code: "schedule_missing",
  text: "Анна — не настроен график",
  origin: "catalog" as const,
};
const ivan = {
  master: { id: "m-2", name: "Иван" },
  code: "services_missing",
  text: "Иван — не назначены услуги",
  origin: "catalog" as const,
};

describe("readinessState", () => {
  it("готов — только при ready", () => {
    expect(readinessState(doc())).toEqual({ kind: "ready" });
  });

  it("N проблем — длина problems", () => {
    expect(readinessState(doc({ ready: false, problems: [anna, ivan] }))).toEqual({
      kind: "problems",
      n: 2,
    });
  });

  it("отказ источника — «не удалось проверить», даже если в problems одна строка о салоне", () => {
    const state = readinessState(
      doc({
        ready: false,
        unknown: true,
        source_problem: "source_unavailable",
        problems: [
          {
            master: { id: null, name: "" },
            code: "source_unavailable",
            text: "Не удалось проверить готовность: каталог не ответил. Попробуйте ещё раз.",
            origin: "source",
          },
        ],
      }),
    );
    expect(state).toEqual({ kind: "unknown" });
  });

  it("unknown без строк (форма, которую каталог не производит) — «не удалось проверить», не «готов»", () => {
    expect(readinessState(doc({ ready: false, unknown: true, problems: [] }))).toEqual({
      kind: "unknown",
    });
  });

  it("unknown с именованной строкой (slots_unknown) — это проблема, считается", () => {
    const slots = { ...anna, code: "slots_unknown", text: "Анна — не удалось проверить свободные окна" };
    expect(readinessState(doc({ ready: false, unknown: true, problems: [slots] }))).toEqual({
      kind: "problems",
      n: 1,
    });
  });
});

describe("тексты", () => {
  it("склонение «проблема»", () => {
    expect(problemsLabel(1)).toBe("1 проблема");
    expect(problemsLabel(2)).toBe("2 проблемы");
    expect(problemsLabel(5)).toBe("5 проблем");
    expect(problemsLabel(11)).toBe("11 проблем");
    expect(problemsLabel(21)).toBe("21 проблема");
  });

  it("карточка по состоянию", () => {
    expect(readinessCardText({ kind: "loading" })).toBe("Готовность — проверяем…");
    expect(readinessCardText({ kind: "failed" })).toBe("Готовность — не удалось проверить");
    expect(readinessCardText({ kind: "unknown" })).toBe("Готовность — не удалось проверить");
    expect(readinessCardText({ kind: "ready" })).toBe("Готовность — салон готов");
    expect(readinessCardText({ kind: "problems", n: 3 })).toBe("Готовность — 3 проблемы");
  });

  it("checkedAtLabel: ISO → ЧЧ:ММ, мусор → пустая строка (не «NaN:NaN»)", () => {
    expect(checkedAtLabel("2026-09-20T09:00:00+00:00")).toMatch(/^\d{2}:\d{2}$/);
    expect(checkedAtLabel("not-a-date")).toBe("");
    expect(checkedAtLabel("")).toBe("");
  });

  it("строка сводки: одна ситуация / N ситуаций; ноль — пусто", () => {
    expect(attentionLine(0)).toBe("");
    expect(attentionLine(1)).toBe("Одна ситуация требует внимания.");
    expect(attentionLine(2)).toBe("2 ситуации требуют внимания.");
    expect(attentionLine(5)).toBe("5 ситуаций требуют внимания.");
    expect(attentionLine(21)).toBe("21 ситуация требует внимания.");
  });
});
