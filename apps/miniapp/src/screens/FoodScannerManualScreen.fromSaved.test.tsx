/**
 * «Записать из избранного» (DRF-2092, F12) — та же тропа F8, вход с
 * сохранённой порцией.
 *
 * Экран текста получает `location.state.fromSaved = {dish_name, portion_g}`
 * и сразу зовёт оценку с этой порцией: карточка «Я распознала так» — и
 * дальше всё как у F8 («В дневник» → `logFoodText`, `corrected=false`:
 * порцию человек назвал, сохранив блюдо; это не поправка на карточке).
 * Сторож слов F8 («примерно» / «оценка») обязан пройти и на этом входе.
 */
import { act, configure, fireEvent, getConfig, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return { ...original, fetchConsentAt: vi.fn(), estimateFoodText: vi.fn(), logFoodText: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { estimateFoodText, fetchConsentAt, logFoodText, type FoodTextEstimate } from "../lib/food-scanner";
import { FoodScannerManualScreen, MANUAL_COPY, MANUAL_ROUTE } from "./FoodScannerManualScreen";

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

const ESTIMATE: FoodTextEstimate = {
  matched_dish: "борщ",
  portion_g: 250,
  portion_estimated: false,
  kcal: 300,
  protein_g: 12,
  fat_g: 10,
  carbs_g: 30,
};

function renderFromSaved() {
  return render(
    <MemoryRouter
      initialEntries={[{ pathname: MANUAL_ROUTE, state: { fromSaved: { dish_name: "Борщ", portion_g: 250 } } }]}
    >
      <Routes>
        <Route path={MANUAL_ROUTE} element={<FoodScannerManualScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedConsent.mockResolvedValue("2026-09-18T10:00:00Z");
  mockedEstimate.mockResolvedValue(ESTIMATE);
  mockedLog.mockResolvedValue({ log_id: "01J9LOG", dish_name: "борщ", calories: 300, entry_origin: "text_estimated_confirmed" });
});

describe("из избранного — тот же путь F8", () => {
  it("оценка зовётся сразу с сохранённой порцией; карточка несёт «примерно»/«оценка»", async () => {
    renderFromSaved();
    await settle();

    expect(mockedEstimate).toHaveBeenCalledTimes(1);
    expect(mockedEstimate).toHaveBeenCalledWith("Борщ", 250);
    expect(mockedLog).not.toHaveBeenCalled();

    const card = screen.getByTestId("estimate-card");
    expect(card).toHaveTextContent("Я распознала так: борщ.");
    expect(card).toHaveTextContent("Порция — 250 г, по твоим словам.");
    expect(card.textContent).toMatch(/[Пп]римерно/);
    expect(card.textContent).toMatch(/оценка/);
    // Поле заполнено блюдом — человек видит, что оценивается.
    expect(screen.getByLabelText(MANUAL_COPY.whatInputLabel)).toHaveValue("Борщ");
  });

  it("«В дневник» с карточки из избранного пишет как показано, corrected=false", async () => {
    renderFromSaved();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: MANUAL_COPY.toDiary }));
    await settle();

    expect(mockedLog).toHaveBeenCalledTimes(1);
    expect(mockedLog.mock.calls[0]?.[0]).toMatchObject({ dish_name: "борщ", portion_g: 250, corrected: false });
    expect(mockedLog.mock.calls[0]?.[0].idempotency_key).toBeTruthy();
  });
});
