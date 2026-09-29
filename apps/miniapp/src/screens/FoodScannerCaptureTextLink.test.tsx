/**
 * DRF-2289 — «Записать питание» с Главной открывает съёмку фото, а ввод
 * текстом — ссылка на самом экране съёмки.
 *
 * Экран съёмки сам спрашивает согласие у сервера (DRF-1564) — ту же
 * колонку, что читает гейт навыка в боте. Поэтому без согласия человек
 * попадает на гейт, а не в тупик; ссылка «Записать текстом» живёт на
 * экране съёмки, а гейт её не показывает (ввод текстом без согласия
 * сам вернул бы на тот же гейт).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return {
    ...original,
    fetchDiaryConsentGate: vi.fn(),
    grantConsent: vi.fn(),
  };
});

import { fetchDiaryConsentGate } from "../lib/food-scanner";
import { FoodScannerCaptureScreen } from "./FoodScannerCaptureScreen";

const mockedFetch = vi.mocked(fetchDiaryConsentGate);

const gate = (grantedAt: string | null) => ({
  canonical: false,
  grantedAt,
  currentDocumentVersion: "",
});

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/food-scanner/capture"]}>
      <Routes>
        <Route path="/customer/food-scanner/capture" element={<FoodScannerCaptureScreen />} />
        <Route path="/customer/food-scanner/manual" element={<div>ВВОД ТЕКСТОМ</div>} />
        <Route path="/customer/main" element={<div>ДОМ</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("DRF-2289 — съёмка фото с выходом в ввод текстом", () => {
  it("на экране съёмки есть «Записать текстом», и она ведёт в ввод текстом", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue(gate("2026-09-08T10:00:00+00:00"));
    renderScreen();

    expect(await screen.findByRole("button", { name: "Сделать фото" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Записать текстом" }));
    expect(await screen.findByText("ВВОД ТЕКСТОМ")).toBeInTheDocument();
  });

  it("без согласия на сканер — экран согласия, а не тупик", async () => {
    mockedFetch.mockResolvedValue(gate(null));
    renderScreen();

    // Присутствие: гейт с кнопкой согласия…
    expect(await screen.findByRole("button", { name: /разреш/i })).toBeInTheDocument();
    // …и отсутствие: съёмки до согласия нет.
    expect(screen.queryByRole("button", { name: "Сделать фото" })).not.toBeInTheDocument();
  });
});
