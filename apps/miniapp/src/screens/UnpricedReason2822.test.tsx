/**
 * DRF-2822 — вопрос под карточкой текстового ввода называет причину.
 *
 * Решение владельца 06.10.2026: «Сейчас не удалось…» — для сбоя,
 * «…выключена» — когда ИИ-оценка выключена; остальное — прежний вопрос.
 * Причину ставит каталог (`kcal_ai_status`), слова те же, что в чате.
 * Статусы здесь синтетические: каталог поле ещё не выложил.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return { ...original, fetchConsentAt: vi.fn(), estimateFoodText: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { estimateFoodText, fetchConsentAt, type FoodTextEstimate } from "../lib/food-scanner";
import { settleScenario } from "../test/settleScenario";
import {
  FoodScannerManualScreen,
  MANUAL_COPY,
  confirmQuestionFor,
  renderEstimateLines,
} from "./FoodScannerManualScreen";

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

describe("DRF-2822 — экран: вопрос под карточкой берётся из ответа оценки", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchConsentAt).mockResolvedValue("2026-09-18T10:00:00Z");
  });

  async function cardFor(over: Partial<FoodTextEstimate>): Promise<HTMLElement> {
    vi.mocked(estimateFoodText).mockResolvedValue(estimate(over));
    render(
      <MemoryRouter initialEntries={["/customer/food-scanner/manual"]}>
        <Routes>
          <Route path="/customer/food-scanner/manual" element={<FoodScannerManualScreen />} />
        </Routes>
      </MemoryRouter>,
    );
    await settleScenario();
    fireEvent.change(screen.getByLabelText(MANUAL_COPY.whatInputLabel), { target: { value: "зыбзик 300" } });
    fireEvent.click(screen.getByRole("button", { name: MANUAL_COPY.estimate }));
    await settleScenario();
    return screen.getByTestId("estimate-card");
  }

  it("сбой: карточка спрашивает словами владельца, кнопка записи на месте", async () => {
    const card = await cardFor({ kcal_ai_status: "unavailable" });

    expect(card).toHaveTextContent(FAILED);
    expect(card).not.toHaveTextContent(PLAIN);
    expect(screen.getByRole("button", { name: MANUAL_COPY.toDiary })).toBeInTheDocument();
  });

  it("выключена: карточка говорит «выключена»", async () => {
    const card = await cardFor({ kcal_ai_status: "disabled" });

    expect(card).toHaveTextContent(SWITCHED_OFF);
    expect(card).not.toHaveTextContent(PLAIN);
  });

  it("статуса нет: прежний вопрос", async () => {
    const card = await cardFor({});

    expect(card).toHaveTextContent(PLAIN);
    expect(card).not.toHaveTextContent("Записать без расчёта?");
  });
});
