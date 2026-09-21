/**
 * Кадр 2 макета DRF-1320 «Лучше всего подходит» (DRF-2178, Э-1; DRF-1775).
 *
 * Этап 1 из 4, радиус ограничен намеренно.
 *
 * **Лист DRF-1775 писался 12.09, до DRF-2174.** Он объявлен
 * заблокированным D11 — «причин решения для кандидата PROVIDER нет».
 * Они есть: транзит `ProviderPick.reasonCodes` / `reasons` сделан в К-1
 * ради полки, и гейт «без displayable-кода кандидат не доходит»
 * встроен в него же. Строка «Почему она» строится из готовых данных.
 *
 * Что заперто:
 *
 * 1. один лучший специалист, его имя, рейтинг и число отзывов — из
 *    зеркала; нет рейтинга или отзывов — скобок нет (DRF-1778);
 * 2. «Почему она:» — причины кандидата дословно, не сочинённые экраном;
 * 3. «Другие варианты» — не больше двух, тем же видом;
 * 4. кнопка называет выбранного по имени («Выбрать Екатерину»);
 * 5. нет кандидатов-специалистов → кадра нет, прежний путь через выбор
 *    мастера; это названный предел, а не пустой экран;
 * 6. «наиболее подходящий» без кода не звучит, и «Ayla рекомендует» —
 *    тоже (§60 снят только в части C04).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCatalogBrowse: vi.fn() };
});

const navigateSpy = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const original = await importOriginal<typeof import("react-router-dom")>();
  return { ...original, useNavigate: () => navigateSpy };
});

import { getCatalogBrowse, type CatalogBrowseData } from "../lib/customer-booking";
import {
  MAX_OTHER_PROVIDERS,
  PROVIDER_CTA,
  PROVIDER_HEAD,
  PROVIDER_OTHERS_HEAD,
  PROVIDER_OTHER_LINK,
  PROVIDER_WHY_HEAD,
} from "../lib/booking-flow";
import { ProviderChoiceScreen } from "./ProviderChoiceScreen";

const mockedBrowse = vi.mocked(getCatalogBrowse);

function master(id: string, name: string, over: Record<string, unknown> = {}) {
  return {
    id,
    name,
    specialization: "Массаж",
    bio: "",
    experience: "",
    rating: "4.8",
    photo_url: "",
    review_count: 74,
    ...over,
  } as unknown as CatalogBrowseData["masters"][number];
}

function pick(masterId: string, rank: number, reasons: string[]) {
  return {
    masterId,
    tier: 1,
    rank,
    reasonCodes: ["ELIG_CAPABILITY_VERIFIED"],
    reasons,
  };
}

function browse(over: Partial<CatalogBrowseData> = {}): CatalogBrowseData {
  return {
    services: [],
    masters: [master("m-1", "Екатерина С."), master("m-2", "Анна П.", { rating: "4.7", review_count: 52 })],
    picks: [],
    providerPicks: [
      pick("m-1", 1, ["работает с выбранной услугой", "есть подходящее время"]),
      pick("m-2", 2, ["работает с выбранной услугой"]),
    ],
    picksOutcome: "OK",
    emptyReason: null,
    ...over,
  } as CatalogBrowseData;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/booking/provider?service=svc-1"]}>
      <ProviderChoiceScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedBrowse.mockResolvedValue(browse());
});

describe("кадр «Лучше всего подходит»", () => {
  it("показывает одного лучшего с именем, рейтингом и отзывами", async () => {
    renderScreen();
    expect(await screen.findByText(PROVIDER_HEAD)).toBeInTheDocument();
    expect(screen.getByText("Екатерина С.")).toBeInTheDocument();
    expect(screen.getByText(/4\.8/)).toBeInTheDocument();
    expect(screen.getByText(/74/)).toBeInTheDocument();
  });

  it("«Почему она» — причины кандидата дословно", async () => {
    renderScreen();
    await screen.findByText(PROVIDER_HEAD);
    expect(screen.getByText(PROVIDER_WHY_HEAD)).toBeInTheDocument();
    expect(screen.getByText("работает с выбранной услугой")).toBeInTheDocument();
    expect(screen.getByText("есть подходящее время")).toBeInTheDocument();
  });

  it("кнопка называет выбранного по имени", async () => {
    renderScreen();
    await screen.findByText(PROVIDER_HEAD);
    expect(
      screen.getByRole("button", { name: PROVIDER_CTA("Екатерина С.") }),
    ).toBeInTheDocument();
  });

  it("«Другие варианты» — не больше двух", async () => {
    mockedBrowse.mockResolvedValue(
      browse({
        masters: [
          master("m-1", "Екатерина С."),
          master("m-2", "Анна П."),
          master("m-3", "Мария К."),
          master("m-4", "Ольга Д."),
        ],
        providerPicks: [
          pick("m-1", 1, ["работает с выбранной услугой"]),
          pick("m-2", 2, ["работает с выбранной услугой"]),
          pick("m-3", 3, ["работает с выбранной услугой"]),
          pick("m-4", 4, ["работает с выбранной услугой"]),
        ],
      } as Partial<CatalogBrowseData>),
    );
    renderScreen();
    await screen.findByText(PROVIDER_HEAD);
    expect(screen.getByText(PROVIDER_OTHERS_HEAD)).toBeInTheDocument();
    expect(screen.queryByText("Ольга Д.")).not.toBeInTheDocument();
    expect(MAX_OTHER_PROVIDERS).toBe(2);
  });

  it("без рейтинга и отзывов скобок нет, а не «null»", async () => {
    mockedBrowse.mockResolvedValue(
      browse({
        masters: [master("m-1", "Екатерина С.", { rating: null, review_count: 0 })],
        providerPicks: [pick("m-1", 1, ["работает с выбранной услугой"])],
      } as Partial<CatalogBrowseData>),
    );
    const { container } = renderScreen();
    await screen.findByText(PROVIDER_HEAD);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/null|undefined|\(\s*\)/);
  });

  it("ни «наиболее подходящий» без кода, ни «Ayla рекомендует»", async () => {
    const { container } = renderScreen();
    await screen.findByText(PROVIDER_HEAD);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/рекоменд/i);
    expect(text).not.toMatch(/наиболее подходящ/i);
  });
});

describe("когда выбирать не из кого", () => {
  it("нет кандидатов — кадра нет, остаётся прежний выбор мастера", async () => {
    mockedBrowse.mockResolvedValue(browse({ providerPicks: [] }));
    renderScreen();
    await vi.waitFor(() => {
      expect(navigateSpy).toHaveBeenCalledWith(expect.stringContaining("/customer/book/master"));
    });
    expect(screen.queryByText(PROVIDER_HEAD)).not.toBeInTheDocument();
  });

  it("«Другой специалист» ведёт туда же — к полному списку", async () => {
    renderScreen();
    await screen.findByText(PROVIDER_HEAD);
    await userEvent.click(screen.getByRole("button", { name: PROVIDER_OTHER_LINK }));
    expect(navigateSpy).toHaveBeenCalledWith(expect.stringContaining("/customer/book/master"));
  });
});
