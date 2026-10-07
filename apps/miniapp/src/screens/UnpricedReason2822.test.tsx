/**
 * DRF-2822 — вопрос под карточкой текстового ввода называет причину.
 *
 * Решение владельца 06.10.2026: «Сейчас не удалось…» — для сбоя,
 * «…выключена» — когда ИИ-оценка выключена; остальное — прежний вопрос.
 * Причину ставит каталог (`kcal_ai_status`), слова те же, что в чате.
 * Статусы здесь синтетические: каталог поле ещё не выложил.
 */
import { describe, expect, it } from "vitest";

import type { FoodTextEstimate } from "../lib/food-scanner";
import { confirmQuestionFor, renderEstimateLines } from "./FoodScannerManualScreen";

const PLAIN = "Записать в дневник?";
const FAILED = "Сейчас не удалось рассчитать калорийность. Записать без расчёта?";
const SWITCHED_OFF = "ИИ-оценка калорийности выключена. Записать без расчёта?";

function estimate(over: Partial<FoodTextEstimate> = {}): FoodTextEstimate {
  return {
    matched_dish: "зыбзик",
    portion_g: 300,
    portion_estimated: false,
    kcal: null,
    portion_source: "provider",
    protein_g: null,
    fat_g: null,
    carbs_g: null,
    kcal_ai_estimate: null,
    ...over,
  };
}

describe("DRF-2822 — причина «числа нет» в вопросе карточки", () => {
  it("сбой и «выключена» — слова владельца", () => {
    expect(confirmQuestionFor(estimate({ kcal_ai_status: "unavailable" }))).toBe(FAILED);
    expect(confirmQuestionFor(estimate({ kcal_ai_status: "disabled" }))).toBe(SWITCHED_OFF);
  });

  it.each(["not_attempted", "not_permitted", "not_applicable", "declined", "estimated"])(
    "статус %s — прежний вопрос",
    (status) => {
      expect(confirmQuestionFor(estimate({ kcal_ai_status: status }))).toBe(PLAIN);
    },
  );

  it.each([undefined, null, "", "unparsed", "UNAVAILABLE"])(
    "поля нет или значение незнакомо (%s) — прежний вопрос",
    (status) => {
      expect(confirmQuestionFor(estimate({ kcal_ai_status: status }))).toBe(PLAIN);
    },
  );

  it("число есть — причина не называется, каким бы ни был статус", () => {
    for (const status of ["unavailable", "disabled"]) {
      expect(confirmQuestionFor(estimate({ kcal_ai_estimate: 750, kcal_ai_status: status }))).toBe(PLAIN);
      expect(confirmQuestionFor(estimate({ kcal: 150, kcal_ai_status: status }))).toBe(PLAIN);
    }
  });

  it("строки карточки от статуса не зависят", () => {
    expect(renderEstimateLines(estimate({ kcal_ai_status: "unavailable" }))).toEqual(
      renderEstimateLines(estimate()),
    );
  });
});
