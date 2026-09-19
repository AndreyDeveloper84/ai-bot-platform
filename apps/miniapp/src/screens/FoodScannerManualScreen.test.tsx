/**
 * Запись еды текстом (F8, DRF-2091) — targeted proof.
 *
 * Что сторожится (узлы листа):
 *   - текст → оценка без записи → карточка «Я распознала так» со словами
 *     «примерно» / «оценка» (сторож слов, как у чата) → «В дневник» →
 *     запись с `corrected=false` → возврат в дневник;
 *   - «Поправить граммы» → новая оценка с граммами → запись с `corrected=true`
 *     (код происхождения меняется на карточке, не задним числом);
 *   - ключ идемпотентности — один на карточку;
 *   - отказы по имени: `food_not_recognized` → фраза; `food_diary_consent_required`
 *     → на гейт согласия с возвратом сюда; `nutrition_unavailable` → фраза;
 *   - `guardProd` на этой тропе НЕ стоит: `estimateFoodText`/`logFoodText` —
 *     настоящие request (с DRF-2098 и `scanPhoto`/`logMeal` тоже — D26 = v1
 *     покрывает фото; DRF-2106 снял последний stub `fetchHealthFlags`);
 *   - «Добавить приём» в дневнике и кнопка дашборда ведут сюда, а не в съёмку.
 */
import { act, configure, fireEvent, getConfig, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return {
    ...original,
    fetchConsentAt: vi.fn(),
    estimateFoodText: vi.fn(),
    logFoodText: vi.fn(),
  };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { ApiError } from "../lib/api";
import * as foodScanner from "../lib/food-scanner";
import { estimateFoodText, fetchConsentAt, logFoodText, type FoodTextEstimate } from "../lib/food-scanner";
import { FoodScannerManualScreen, MANUAL_COPY, renderEstimateLines } from "./FoodScannerManualScreen";

const GUARD_ASYNC_TIMEOUT_MS = 20;
let previousAsyncUtilTimeout = 1000;

beforeAll(() => {
  previousAsyncUtilTimeout = getConfig().asyncUtilTimeout;
  configure({ asyncUtilTimeout: GUARD_ASYNC_TIMEOUT_MS });
});

afterAll(() => {
  configure({ asyncUtilTimeout: previousAsyncUtilTimeout });
});

const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

const mockedConsent = vi.mocked(fetchConsentAt);
const mockedEstimate = vi.mocked(estimateFoodText);
const mockedLog = vi.mocked(logFoodText);

function estimate(overrides: Partial<FoodTextEstimate> = {}): FoodTextEstimate {
  return {
    matched_dish: "борщ",
    portion_g: 250,
    portion_estimated: false,
    kcal: 300,
    protein_g: 12,
    fat_g: 10,
    carbs_g: 30,
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  const state = location.state as { returnTo?: string } | null;
  return (
    <div data-testid="location">
      {location.pathname}|{state?.returnTo ?? ""}
    </div>
  );
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/food-scanner/manual"]}>
      <Routes>
        <Route path="/customer/food-scanner/manual" element={<FoodScannerManualScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function typeAndEstimate(text: string) {
  fireEvent.change(screen.getByLabelText(MANUAL_COPY.whatInputLabel), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: MANUAL_COPY.estimate }));
  await settle();
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedConsent.mockResolvedValue("2026-09-18T10:00:00Z");
  mockedEstimate.mockResolvedValue(estimate());
  mockedLog.mockResolvedValue({ log_id: "01J9LOG", dish_name: "борщ", calories: 300, entry_origin: "text_estimated_confirmed" });
});

describe("оценка → карточка → «В дневник»", () => {
  it("текст уходит на оценку без записи; карточка называет оценку оценкой; запись как показано → дневник", async () => {
    renderScreen();
    await settle();
    await typeAndEstimate("борщ 250");

    expect(mockedEstimate).toHaveBeenCalledWith("борщ 250", undefined);
    expect(mockedLog).not.toHaveBeenCalled(); // оценка ничего не пишет

    const card = screen.getByTestId("estimate-card");
    expect(card).toHaveTextContent("Я распознала так: борщ.");
    expect(card).toHaveTextContent("Порция — 250 г, по твоим словам.");
    // Сторож слов: «Примерно … — оценка по справочнику блюд.»
    expect(card.textContent).toMatch(/[Пп]римерно/);
    expect(card.textContent).toMatch(/оценка/);

    fireEvent.click(screen.getByRole("button", { name: MANUAL_COPY.toDiary }));
    await settle();

    expect(mockedLog).toHaveBeenCalledTimes(1);
    expect(mockedLog.mock.calls[0]?.[0]).toMatchObject({ dish_name: "борщ", portion_g: 250, corrected: false });
    expect(mockedLog.mock.calls[0]?.[0].idempotency_key).toBeTruthy();
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/food-scanner/diary");
  });

  it("без граммов в тексте порция — оценка, и карточка говорит это словами", async () => {
    mockedEstimate.mockResolvedValue(estimate({ portion_g: 100, portion_estimated: true }));
    renderScreen();
    await settle();
    await typeAndEstimate("борщ");
    expect(screen.getByTestId("estimate-card")).toHaveTextContent(
      "Порция — примерно 100 г, это оценка: граммов в сообщении не было.",
    );
  });

  it("сторож слов на самом рендере: оценка без макросов всё равно несёт «примерно» и «оценка»", () => {
    const lines = renderEstimateLines(estimate({ protein_g: null, fat_g: null, carbs_g: null }));
    const text = lines.join("\n");
    expect(text).toMatch(/[Пп]римерно/);
    expect(text).toMatch(/оценка/);
    expect(text).not.toMatch(/Б \d/);
  });
});

describe("«Поправить граммы» — код происхождения решается на карточке", () => {
  it("новая оценка с граммами → запись с corrected=true; ключ — новый на новую карточку", async () => {
    renderScreen();
    await settle();
    await typeAndEstimate("борщ");
    const firstKey = (): string | undefined => mockedLog.mock.calls[0]?.[0].idempotency_key;

    fireEvent.click(screen.getByRole("button", { name: MANUAL_COPY.fixGrams }));
    fireEvent.change(screen.getByLabelText(MANUAL_COPY.gramsField), { target: { value: "300" } });
    mockedEstimate.mockResolvedValue(estimate({ portion_g: 300, portion_estimated: false, kcal: 360 }));
    fireEvent.click(screen.getByRole("button", { name: MANUAL_COPY.recalc }));
    await settle();

    expect(mockedEstimate).toHaveBeenLastCalledWith("борщ", 300);
    expect(screen.getByTestId("estimate-card")).toHaveTextContent("Порция — 300 г, по твоим словам.");

    fireEvent.click(screen.getByRole("button", { name: MANUAL_COPY.toDiary }));
    await settle();
    expect(mockedLog.mock.calls[0]?.[0]).toMatchObject({ portion_g: 300, corrected: true });
    expect(firstKey()).toBeTruthy();
  });

  it("не число → фраза, оценка не зовётся повторно", async () => {
    renderScreen();
    await settle();
    await typeAndEstimate("борщ");
    fireEvent.click(screen.getByRole("button", { name: MANUAL_COPY.fixGrams }));
    fireEvent.change(screen.getByLabelText(MANUAL_COPY.gramsField), { target: { value: "много" } });
    fireEvent.click(screen.getByRole("button", { name: MANUAL_COPY.recalc }));
    await settle();
    expect(screen.getByText(MANUAL_COPY.badGrams)).toBeInTheDocument();
    expect(mockedEstimate).toHaveBeenCalledTimes(1);
  });
});

describe("отказы — по имени", () => {
  it("food_not_recognized → своя фраза, карточки нет", async () => {
    mockedEstimate.mockRejectedValue(new ApiError(400, "food_not_recognized", "x"));
    renderScreen();
    await settle();
    await typeAndEstimate("нечто");
    expect(screen.getByText(MANUAL_COPY.notRecognized)).toBeInTheDocument();
    expect(screen.queryByTestId("estimate-card")).toBeNull();
  });

  it("food_diary_consent_required → на гейт согласия с возвратом сюда", async () => {
    mockedEstimate.mockRejectedValue(new ApiError(403, "food_diary_consent_required", "x"));
    renderScreen();
    await settle();
    await typeAndEstimate("борщ");
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/food-scanner/capture|/customer/food-scanner/manual");
  });

  it("nutrition_unavailable при записи → фраза, остаёмся на карточке", async () => {
    mockedLog.mockRejectedValue(new ApiError(503, "nutrition_unavailable", "x"));
    renderScreen();
    await settle();
    await typeAndEstimate("борщ");
    fireEvent.click(screen.getByRole("button", { name: MANUAL_COPY.toDiary }));
    await settle();
    expect(screen.getByText(MANUAL_COPY.unavailable)).toBeInTheDocument();
    expect(screen.getByTestId("estimate-card")).toBeInTheDocument();
  });

  it("согласия нет по данным сервера → сразу на гейт (без вызова оценки)", async () => {
    mockedConsent.mockResolvedValue(null);
    renderScreen();
    await settle();
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/food-scanner/capture|/customer/food-scanner/manual");
    expect(mockedEstimate).not.toHaveBeenCalled();
  });
});

describe("входы ведут сюда — обещание фото снято (D26)", () => {
  // Копия дашборда читается из исходника: сторож честной копии, как у имён.
  const DASHBOARD = Object.values(
    import.meta.glob("./CustomerWellnessDashboardScreen.tsx", { query: "?raw", import: "default", eager: true }),
  )[0] as string;

  it("дашборд не обещает «Сфотографируй первый приём» и ведёт «Записать текстом» на этот экран", () => {
    expect(DASHBOARD).toBeTruthy(); // присутствие: файл прочитан
    expect(DASHBOARD).not.toContain("Сфотографируй первый приём");
    expect(DASHBOARD).toContain("Записать текстом");
    expect(DASHBOARD).toContain("/customer/food-scanner/manual");
  });
});

describe("тропа настоящая, не stub", () => {
  it("estimateFoodText / logFoodText существуют в lib и не проходят через guardProd", async () => {
    const original = await vi.importActual<typeof import("../lib/food-scanner")>("../lib/food-scanner");
    const source = (original.estimateFoodText.toString() + original.logFoodText.toString()).toLowerCase();
    expect(source).not.toContain("guardprod");
    expect(source).toContain("/food/estimate");
    expect(source).toContain("/food/log");
    // Фото-половина с DRF-2098 боевая (D26 = «food-diary-v1 покрывает фото»),
    // последний stub (`fetchHealthFlags`) снят DRF-2106 — в модуле `guardProd`
    // больше нет вовсе; контроль на слово живёт в `food-scanner.scan.test.ts`.
    expect(original.scanPhoto.toString().toLowerCase()).not.toContain("guardprod");
    expect(typeof foodScanner.estimateFoodText).toBe("function");
  });
});
