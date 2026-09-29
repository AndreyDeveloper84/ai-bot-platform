/**
 * OD-PILOT-9 (DRF-1706) — честное отсутствие рекомендации и полка за флагом.
 *
 * Первый Controlled Pilot — без полки. Когда резолвер отверг кандидатов
 * как неподтверждённые (`ELIG_EXCLUDED_NOT_RECOMMENDABLE`, §76), человек
 * читает текст владельца и получает два действия, которые работают уже
 * сегодня. Остальные пустоты остаются молчаливыми — одно имя на два
 * состояния запрещено.
 *
 * Стражи:
 * 1. текст — дословно константа, оба действия на месте, действия ведут
 *    куда обещают;
 * 2. положительная половина: на `OK` (есть кандидаты) и на
 *    `SAFETY_BLOCKED` блока нет — иначе тест зеленел бы на экране,
 *    который говорит «недостаточно данных» всегда;
 * 3. полка не рендерится без флага даже с picks + WHY, с флагом —
 *    называется «Ayla рекомендует», и старого имени в исходниках нет.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
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

import { fetchMasters, fetchRecommendations, fetchServices } from "../lib/api";
import {
  ACTION_CLARIFY_REQUEST,
  ACTION_SHOW_SERVICES,
  CANONICAL_SHELF_TITLE,
  NO_VERIFIED_EVIDENCE_TEXT,
  SURFACE_AVAILABLE_SERVICES,
} from "../lib/recommendation-absence";
import { CustomerCatalogScreen } from "./CustomerCatalogScreen";

const mockedServices = vi.mocked(fetchServices);
const mockedMasters = vi.mocked(fetchMasters);
const mockedRecs = vi.mocked(fetchRecommendations);

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

function PathProbe() {
  const location = useLocation();
  return <div data-testid="path">{location.pathname}</div>;
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/catalog"]}>
      <Routes>
        <Route
          path="/customer/catalog"
          element={
            <>
              <CustomerCatalogScreen />
              <PathProbe />
            </>
          }
        />
        <Route path="/customer/goal-select" element={<div>GOAL-SELECT-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
  mockedServices.mockResolvedValue({ services: SERVICES as never });
  mockedMasters.mockResolvedValue({ masters: MASTERS as never });
  // jsdom не реализует scrollIntoView — в браузере это есть.
  Element.prototype.scrollIntoView = vi.fn();
});

describe("честное отсутствие — NO_VERIFIED_CANDIDATES", () => {
  it("текст владельца дословно и два действия", async () => {
    mockedRecs.mockResolvedValue(
      decision([], { reason_codes: ["ELIG_EXCLUDED_NOT_RECOMMENDABLE"] }) as never,
    );
    renderScreen();

    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent(NO_VERIFIED_EVIDENCE_TEXT);
    expect(within(notice).getByRole("button", { name: ACTION_SHOW_SERVICES })).toBeInTheDocument();
    expect(within(notice).getByRole("button", { name: ACTION_CLARIFY_REQUEST })).toBeInTheDocument();
    // Каталог под блоком на месте и называется по решению.
    expect(screen.getByRole("region", { name: SURFACE_AVAILABLE_SERVICES })).toBeInTheDocument();
  });

  it("«Уточнить запрос» ведёт к уточнению цели; «Посмотреть услуги» — к секции", async () => {
    const user = userEvent.setup();
    mockedRecs.mockResolvedValue(
      decision([], { reason_codes: ["ELIG_EXCLUDED_NOT_RECOMMENDABLE"] }) as never,
    );
    renderScreen();
    const notice = await screen.findByRole("status");

    await user.click(within(notice).getByRole("button", { name: ACTION_SHOW_SERVICES }));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    expect(screen.getByTestId("path")).toHaveTextContent("/customer/catalog");

    await user.click(within(notice).getByRole("button", { name: ACTION_CLARIFY_REQUEST }));
    expect(await screen.findByText("GOAL-SELECT-PROBE")).toBeInTheDocument();
  });

  it("положительная стража: с кандидатами блока нет", async () => {
    mockedRecs.mockResolvedValue(
      decision([
        { candidate: { kind: "SERVICE", id: "svc-1" }, rank: 1, tier: 1, reason_codes: ["MATCH_SERVICE_EXACT"] },
      ]) as never,
    );
    renderScreen();
    await screen.findByRole("region", { name: SURFACE_AVAILABLE_SERVICES });
    expect(screen.queryByText(NO_VERIFIED_EVIDENCE_TEXT)).not.toBeInTheDocument();
  });

  it("безопасность — не «недостаточно данных»: на SAFETY_BLOCKED блока нет", async () => {
    mockedRecs.mockResolvedValue(
      decision([], { reason_codes: ["ELIG_EXCLUDED_SAFETY", "ELIG_EXCLUDED_NOT_RECOMMENDABLE"] }) as never,
    );
    renderScreen();
    await screen.findByRole("region", { name: SURFACE_AVAILABLE_SERVICES });
    expect(screen.queryByText(NO_VERIFIED_EVIDENCE_TEXT)).not.toBeInTheDocument();
  });

  it("резолвер недоступен — молчим, а не выдумываем причину", async () => {
    mockedRecs.mockRejectedValue(new Error("down"));
    renderScreen();
    await screen.findByRole("region", { name: SURFACE_AVAILABLE_SERVICES });
    expect(screen.queryByText(NO_VERIFIED_EVIDENCE_TEXT)).not.toBeInTheDocument();
  });
});

describe("полка за флагом (OD-PILOT-9 Stage 2)", () => {
  const PICKS_WITH_WHY = decision([
    { candidate: { kind: "SERVICE", id: "svc-1" }, rank: 1, tier: 1, reason_codes: ["MATCH_SERVICE_EXACT"] },
  ]) as never;

  it("без флага полка не рендерится даже с picks и WHY", async () => {
    mockedRecs.mockResolvedValue(PICKS_WITH_WHY);
    renderScreen();
    await screen.findByRole("region", { name: SURFACE_AVAILABLE_SERVICES });
    expect(screen.queryByRole("region", { name: new RegExp(CANONICAL_SHELF_TITLE) })).not.toBeInTheDocument();
  });

  it("с флагом полка есть и называется «Ayla рекомендует»", async () => {
    vi.stubEnv("VITE_RECOMMENDATION_SHELF", "1");
    mockedRecs.mockResolvedValue(PICKS_WITH_WHY);
    renderScreen();
    expect(await screen.findByRole("region", { name: new RegExp(CANONICAL_SHELF_TITLE) })).toBeInTheDocument();
  });
});

// ─── сторож на исходники: старого имени полки нет ──────────────────────────

const SCREEN_SOURCES = import.meta.glob(["./**/*.tsx", "../components/**/*.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

describe("имя полки", () => {
  it("«Ayla подобрала» больше не рендерится ни одним экраном", () => {
    const offenders = Object.entries(SCREEN_SOURCES)
      .filter(([p]) => !p.endsWith(".test.tsx"))
      .filter(([, src]) => /Ayla подобрала/.test(src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1")))
      .map(([p]) => p);
    expect(offenders).toEqual([]);
    expect(Object.keys(SCREEN_SOURCES).some((p) => p.endsWith("CustomerCatalogScreen.tsx"))).toBe(true);
  });
});
