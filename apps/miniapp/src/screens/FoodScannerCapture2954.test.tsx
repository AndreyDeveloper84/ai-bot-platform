/**
 * DRF-2954 — съёмка в виде доски «Дневник» (кадр 3): то, что вид не вправе
 * потерять.
 *
 * * без сети обе плитки выключены — снимок без сети не распознать;
 * * выбор приёма — радиогруппа с одним выбранным, и выбранный на съёмке
 *   приём уезжает дальше, в распознавание (а оттуда — в запись).
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return {
    ...original,
    fetchDiaryConsentGate: vi.fn(),
    grantConsent: vi.fn(),
    // jsdom не умеет canvas: очистка метаданных отдаёт файл как есть.
    stripImageMetadata: vi.fn(async (file: File) => file),
  };
});

import { fetchDiaryConsentGate } from "../lib/food-scanner";
import { FoodScannerCaptureScreen } from "./FoodScannerCaptureScreen";

const mockedGate = vi.mocked(fetchDiaryConsentGate);

function ProcessingProbe() {
  const state = (useLocation().state ?? {}) as { mealType?: string };
  return <div data-testid="processing">{state.mealType ?? "—"}</div>;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/food-scanner/capture"]}>
      <Routes>
        <Route path="/customer/food-scanner/capture" element={<FoodScannerCaptureScreen />} />
        <Route path="/customer/food-scanner/processing" element={<ProcessingProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function setOnline(value: boolean) {
  Object.defineProperty(window.navigator, "onLine", { configurable: true, value });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedGate.mockResolvedValue({
    canonical: false,
    grantedAt: "2026-09-08T10:00:00+00:00",
    currentDocumentVersion: "",
  });
});

afterEach(() => {
  setOnline(true);
});

describe("съёмка (DRF-2954)", () => {
  it("с сетью плитки работают; без сети обе выключены", async () => {
    const online = renderScreen();
    expect(await screen.findByRole("button", { name: "Сделать фото" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Из галереи" })).toBeEnabled();
    online.unmount();

    setOnline(false);
    renderScreen();
    expect(await screen.findByRole("button", { name: "Сделать фото" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Из галереи" })).toBeDisabled();
  });

  it("приём — один выбранный; выбранный на съёмке уезжает в распознавание", async () => {
    const { container } = renderScreen();
    const group = await screen.findByRole("radiogroup", { name: "Когда" });
    expect(group.querySelectorAll('[aria-checked="true"]')).toHaveLength(1);

    fireEvent.click(screen.getByRole("radio", { name: "Перекус" }));
    expect(screen.getByRole("radio", { name: "Перекус" })).toHaveAttribute("aria-checked", "true");
    expect(group.querySelectorAll('[aria-checked="true"]')).toHaveLength(1);

    const input = container.querySelector('input[type="file"][capture]') as HTMLInputElement;
    const photo = new File(["x"], "meal.jpg", { type: "image/jpeg" });
    fireEvent.change(input, { target: { files: [photo] } });

    await waitFor(() => expect(screen.getByTestId("processing")).toHaveTextContent("snack"));
  });
});
