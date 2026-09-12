/**
 * Честные пустоты подбора — каждому исходу свой кадр (DRF-1767, DRF-1768).
 *
 * До этого среза только `NO_VERIFIED_CANDIDATES` имел слова (#1653);
 * `SAFETY_BLOCKED`, `NO_CAPABLE_CANDIDATES`, `UNAVAILABLE`,
 * `CONTRACT_VIOLATION`, `UNRENDERABLE_CANDIDATES` молчали намеренно, чтобы
 * не получить одно имя на два состояния — и получили пустой экран.
 *
 * Стражи:
 * 1. C04.5: safety boundary — слова гейта, ни одного слова-диагноза, ни
 *    одной кнопки записи; «Написать Ayla» только внутри MAX;
 * 2. no capable — «уточнить запрос», без слов про подтверждённые данные;
 * 3. отказ источника — «попробовать снова» повторяет ТОЛЬКО запрос
 *    подбора (услуги/мастера не перечитываются), второй тап не плодит
 *    второй запрос; удачный повтор снимает кадр;
 * 4. положительная стража: на `OK` ни одного из кадров;
 * 5. кадры не показываются поверх поиска.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    fetchServices: vi.fn(),
    fetchMasters: vi.fn(),
    fetchRecommendations: vi.fn(),
  };
});

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return {
    ...original,
    maxBridge: vi.fn(() => null),
    closeApp: vi.fn(),
  };
});

import { fetchMasters, fetchRecommendations, fetchServices } from "../lib/api";
import { closeApp, maxBridge } from "../lib/max-sdk";
import {
  ACTION_CLARIFY_REQUEST,
  ACTION_RETRY,
  ACTION_SHOW_SERVICES,
  ACTION_WRITE_AYLA,
  DIAGNOSIS_WORDS,
  NO_CAPABLE_TEXT,
  NO_VERIFIED_EVIDENCE_TEXT,
  SAFETY_BOUNDARY_TEXT,
  SOURCE_FAILURE_TEXT,
  SURFACE_AVAILABLE_SERVICES,
  absenceFrame,
} from "../lib/recommendation-absence";
import { CustomerCatalogScreen } from "./CustomerCatalogScreen";

const mockedServices = vi.mocked(fetchServices);
const mockedMasters = vi.mocked(fetchMasters);
const mockedRecs = vi.mocked(fetchRecommendations);
const mockedBridge = vi.mocked(maxBridge);
const mockedClose = vi.mocked(closeApp);

const SERVICES = [
  {
    id: "svc-1",
    slug: "manicure",
    name: "Маникюр",
    short_description: "",
    description: "",
    price_from: null,
    duration_min: 60,
    is_popular: false,
    contraindications: "",
    is_bookable: true,
  },
];
const MASTERS = [
  { id: "m-1", name: "Анна", specialization: "мастер", bio: "", experience: "5 лет", rating: "4.9", photo_url: "" },
];

function decision(ordered: unknown[], extra: Record<string, unknown> = {}): unknown {
  return {
    data: {
      decision_id: "dec-1",
      request_id: "req-1",
      resolver_spec_version: "1.0",
      policy_versions: { resolver_spec_version: "1.0" },
      ordered,
      ...extra,
    },
  };
}

const ALL_TEXTS = [NO_VERIFIED_EVIDENCE_TEXT, SAFETY_BOUNDARY_TEXT, NO_CAPABLE_TEXT, SOURCE_FAILURE_TEXT];

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/catalog"]}>
      <Routes>
        <Route path="/customer/catalog" element={<CustomerCatalogScreen />} />
        <Route path="/customer/goal-select" element={<div>GOAL-SELECT-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function catalogReady() {
  return screen.findByRole("region", { name: SURFACE_AVAILABLE_SERVICES });
}

function onlyThisText(text: string) {
  expect(screen.getByText(text)).toBeInTheDocument();
  for (const other of ALL_TEXTS) {
    if (other !== text) expect(screen.queryByText(other)).toBeNull();
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
  mockedBridge.mockReturnValue(null);
  mockedServices.mockResolvedValue({ services: SERVICES as never });
  mockedMasters.mockResolvedValue({ masters: MASTERS as never });
  Element.prototype.scrollIntoView = vi.fn();
});

describe("классификация исходов — каждому своё имя", () => {
  it("absenceFrame покрывает все исходы и молчит только на OK", () => {
    expect(absenceFrame("OK")).toBeNull();
    expect(absenceFrame("NO_VERIFIED_CANDIDATES")).toBe("no_verified");
    expect(absenceFrame("SAFETY_BLOCKED")).toBe("safety_boundary");
    expect(absenceFrame("NO_CAPABLE_CANDIDATES")).toBe("no_capable");
    expect(absenceFrame("UNAVAILABLE")).toBe("source_failure");
    expect(absenceFrame("CONTRACT_VIOLATION")).toBe("source_failure");
    expect(absenceFrame("UNRENDERABLE_CANDIDATES")).toBe("source_failure");
  });
});

describe("C04.5 — safety boundary (DRF-1767)", () => {
  it("слова гейта, разрешённое действие, ни одной кнопки записи, ни слова-диагноза", async () => {
    mockedRecs.mockResolvedValue(
      decision([], { reason_codes: ["ELIG_EXCLUDED_SAFETY", "ELIG_EXCLUDED_NOT_RECOMMENDABLE"] }) as never,
    );
    renderScreen();
    await catalogReady();

    onlyThisText(SAFETY_BOUNDARY_TEXT);
    const frame = screen.getByRole("status");
    expect(within(frame).getByRole("button", { name: ACTION_SHOW_SERVICES })).toBeInTheDocument();
    expect(within(frame).queryByRole("button", { name: /запис/i })).toBeNull();
    expect(frame.textContent ?? "").not.toMatch(DIAGNOSIS_WORDS);
    // Вне MAX «Написать Ayla» некуда — кнопки нет.
    expect(within(frame).queryByRole("button", { name: ACTION_WRITE_AYLA })).toBeNull();
  });

  it("внутри MAX — «Написать Ayla» закрывает мини-приложение", async () => {
    mockedBridge.mockReturnValue({} as never);
    mockedRecs.mockResolvedValue(decision([], { reason_codes: ["ELIG_EXCLUDED_SAFETY"] }) as never);
    renderScreen();
    await catalogReady();

    await userEvent.click(screen.getByRole("button", { name: ACTION_WRITE_AYLA }));
    expect(mockedClose).toHaveBeenCalledTimes(1);
  });

  it("сторож диагнозов видит диагноз", () => {
    expect(DIAGNOSIS_WORDS.test("похоже на заболевание кожи")).toBe(true);
    expect(DIAGNOSIS_WORDS.test("Здесь я не помощник")).toBe(false);
  });
});

describe("нужда без совпадений (DRF-1768)", () => {
  it("NO_CAPABLE_CANDIDATES → «уточнить запрос», без слов про подтверждённые данные", async () => {
    mockedRecs.mockResolvedValue(
      decision([], { excluded: [{ candidate: { kind: "SERVICE", id: "x" }, stage: "S1", reason_code: "ELIG_EXCLUDED_NOT_CAPABLE" }] }) as never,
    );
    renderScreen();
    await catalogReady();

    onlyThisText(NO_CAPABLE_TEXT);
    await userEvent.click(screen.getByRole("button", { name: ACTION_CLARIFY_REQUEST }));
    expect(await screen.findByText("GOAL-SELECT-PROBE")).toBeInTheDocument();
  });
});

describe("отказ источника (DRF-1768)", () => {
  it("UNAVAILABLE → «попробовать снова»; повтор — только подбор; удача снимает кадр", async () => {
    mockedRecs.mockRejectedValueOnce(new Error("down"));
    renderScreen();
    await catalogReady();
    onlyThisText(SOURCE_FAILURE_TEXT);
    expect(mockedServices).toHaveBeenCalledTimes(1);
    expect(mockedMasters).toHaveBeenCalledTimes(1);

    mockedRecs.mockResolvedValueOnce(decision([]) as never);
    await userEvent.click(screen.getByRole("button", { name: ACTION_RETRY }));

    // Повторился ТОЛЬКО запрос подбора.
    expect(mockedRecs).toHaveBeenCalledTimes(2);
    expect(mockedServices).toHaveBeenCalledTimes(1);
    expect(mockedMasters).toHaveBeenCalledTimes(1);
    // Пустой OK — кадров нет вовсе.
    for (const text of ALL_TEXTS) expect(screen.queryByText(text)).toBeNull();
  });

  it("второй тап во время повтора не плодит второй запрос", async () => {
    mockedRecs.mockRejectedValueOnce(new Error("down"));
    renderScreen();
    await catalogReady();

    let release: (() => void) | null = null;
    mockedRecs.mockImplementationOnce(
      () => new Promise((resolve) => {
        release = () => resolve(decision([]) as never);
      }),
    );
    const retry = screen.getByRole("button", { name: ACTION_RETRY });
    await userEvent.click(retry);
    expect(retry).toBeDisabled();
    await userEvent.click(retry);
    expect(mockedRecs).toHaveBeenCalledTimes(2);
    release?.();
  });

  it("CONTRACT_VIOLATION и UNRENDERABLE — тот же кадр отказа источника", async () => {
    // Не по контракту: нет `ordered`.
    mockedRecs.mockResolvedValueOnce({ data: { decision_id: "d" } } as never);
    renderScreen();
    await catalogReady();
    onlyThisText(SOURCE_FAILURE_TEXT);
  });
});

describe("положительная стража и поиск", () => {
  it("на OK с кандидатами ни одного кадра", async () => {
    mockedRecs.mockResolvedValue(
      decision([
        { candidate: { kind: "SERVICE", id: "svc-1" }, tier: 1, rank: 1, reason_codes: ["MATCH_SERVICE_EXACT"] },
      ]) as never,
    );
    renderScreen();
    await catalogReady();
    for (const text of ALL_TEXTS) expect(screen.queryByText(text)).toBeNull();
  });

  it("поверх поиска кадры не рисуются", async () => {
    mockedRecs.mockResolvedValue(decision([], { reason_codes: ["ELIG_EXCLUDED_SAFETY"] }) as never);
    renderScreen();
    await catalogReady();
    expect(screen.getByText(SAFETY_BOUNDARY_TEXT)).toBeInTheDocument();
    await userEvent.type(screen.getByRole("searchbox"), "мани");
    expect(screen.queryByText(SAFETY_BOUNDARY_TEXT)).toBeNull();
  });
});
