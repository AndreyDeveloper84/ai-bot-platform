/**
 * Tests for `CustomerCatalogScreen` after pilot phase 3(1): the stub
 * 3-layer recommendations lib is gone — the screen renders REAL mirror
 * data (`GET /services`, `GET /masters`) plus Ayla scorer picks
 * (`POST /recommendations`). HTTP layer (`../lib/api`) mocked with the
 * verbatim contract shapes from `apps/miniapp_api/views.py`.
 *
 * The prod gate is removed in the same change: the honest-placeholder
 * test asserts the OLD behaviour is gone (no «выдуманных» placeholder),
 * and every case double-checks the fake stub salons never render.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router-dom";
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

import {
  fetchMasters,
  fetchRecommendations,
  fetchServices,
  type Master,
  type Service,
} from "../lib/api";
import { CustomerCatalogScreen } from "./CustomerCatalogScreen";

const mockedFetchServices = vi.mocked(fetchServices);
const mockedFetchMasters = vi.mocked(fetchMasters);
const mockedFetchRecommendations = vi.mocked(fetchRecommendations);

const SERVICES: Service[] = [
  { id: "svc-1", slug: "manikyur", name: "Маникюр", short_description: "Классический", description: "", price_from: "1800.00", duration_min: 60, is_popular: true, contraindications: "", is_bookable: true },
  { id: "svc-2", slug: "pedikyur", name: "Педикюр", short_description: "", description: "", price_from: "2200.00", duration_min: 90, is_popular: false, contraindications: "", is_bookable: true },
  { id: "svc-3", slug: "massazh", name: "Массаж", short_description: "", description: "", price_from: "3000.00", duration_min: 60, is_popular: false, contraindications: "", is_bookable: true },
  { id: "svc-4", slug: "brovi", name: "Брови", short_description: "", description: "", price_from: "1200.00", duration_min: 30, is_popular: false, contraindications: "", is_bookable: true },
];

const MASTERS: Master[] = [
  { id: "mst-1", name: "Анна Соколова", specialization: "nail-мастер", bio: "", experience: "5 лет", rating: "4.9", photo_url: "" },
  { id: "mst-2", name: "Карина Ли", specialization: "бровист", bio: "", experience: "3 года", rating: null, photo_url: "" },
];

/**
 * Решение резолвера (§4.2) — форма, которую полка читает после T7
 * (DRF-1568). Порядок задан источником: пересобрать его нечем, баллов
 * в ответе нет вовсе.
 */
function decision(ordered: unknown[]): unknown {
  return {
    data: {
      decision_id: "dec-1",
      request_id: "req-1",
      resolver_spec_version: "1.0",
      policy_versions: { resolver_spec_version: "1.0" },
      ordered,
    },
  };
}

function ranked(id: string, rank: number, tier: number, codes: string[]): unknown {
  return { candidate: { kind: "SERVICE", id }, rank, tier, reason_codes: codes };
}

/** Решение с кодами, которым словарь отрисовки даёт фразу. */
const RECS = decision([
  ranked("svc-2", 1, 1, ["EXEC_SLOT_CONFIRMED_IN_WINDOW"]),
  ranked("svc-1", 2, 2, ["CONTEXT_PRIOR_COMPLETED_VISIT", "SCOPE_WITHIN_CITY"]),
  ranked("svc-4", 3, 3, ["MATCH_GOAL_CATEGORY"]),
  ranked("svc-3", 4, 4, ["ELIG_CAPABILITY_VERIFIED"]),
]);

/**
 * Коды есть — фразы нет: ярусная механика и «соответствие не
 * определилось» человеку причиной не являются (§7.2, словарь
 * отрисовки). Это гейт владельца, а не расхождение контракта.
 */
const RECS_NO_WHY = decision([
  ranked("svc-2", 1, 1, ["TIE_TIER_SHARED"]),
  ranked("svc-1", 2, 1, ["MATCH_UNDETERMINED"]),
  ranked("svc-4", 3, 1, ["QUALITY_RATING_UNSUBSTANTIATED_IGNORED"]),
]);

function ServiceProbe() {
  const { serviceId } = useParams();
  return <div>SERVICE-{serviceId}</div>;
}

function MasterProbe() {
  const { masterId } = useParams();
  return <div>MASTER-{masterId}</div>;
}

/** Where the router currently is — CTA navigation assertions. */
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
        <Route path="/customer/catalog/:serviceId" element={<ServiceProbe />} />
        <Route path="/customer/masters/:masterId" element={<MasterProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockHappyPath() {
  mockedFetchServices.mockResolvedValue({ services: SERVICES });
  mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
  mockedFetchRecommendations.mockResolvedValue(RECS);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

describe("CustomerCatalogScreen (real mirror data)", () => {
  it("renders real services and masters; picks capped at 3 in resolver order", async () => {
    vi.stubEnv("VITE_RECOMMENDATION_SHELF", "1"); // полка за флагом (OD-PILOT-9)
    mockHappyPath();
    renderScreen();
    const picks = await screen.findByRole("region", { name: /Ayla рекомендует/ });
    const pickCards = within(picks).getAllByRole("article");
    expect(pickCards).toHaveLength(3);
    // Порядок резолвера: Педикюр, Маникюр, Брови — Массаж за срезом k.
    expect(pickCards[0]).toHaveTextContent("Педикюр");
    expect(pickCards[1]).toHaveTextContent("Маникюр");
    expect(pickCards[2]).toHaveTextContent("Брови");
    // WHAT + WHY: фраза собрана из УТВЕРЖДЁННОГО кода и ниоткуда больше.
    expect(pickCards[0]).toHaveTextContent("Есть свободное время в нужном окне");
    expect(pickCards[1]).toHaveTextContent("Ты уже здесь была");
    expect(pickCards[1]).toHaveTextContent("В твоём городе");
    expect(pickCards[2]).toHaveTextContent("Подходит под твою цель");

    const servicesSection = screen.getByRole("region", { name: "Доступные услуги" });
    expect(within(servicesSection).getAllByRole("article")).toHaveLength(4);

    const mastersSection = screen.getByRole("region", { name: "Мастера" });
    expect(within(mastersSection).getByText("Анна Соколова")).toBeInTheDocument();
    expect(within(mastersSection).getByText(/4\.9/)).toBeInTheDocument();
    expect(within(mastersSection).getByText("Карина Ли")).toBeInTheDocument();
  });

  it("never renders the old fake stub salons", async () => {
    mockHappyPath();
    renderScreen();
    await screen.findByRole("region", { name: "Доступные услуги" });
    for (const fake of ["Beauty Place", "Формула тела", "Студия Лотос", "Casa Bella"]) {
      expect(screen.queryByText(fake)).not.toBeInTheDocument();
    }
  });

  it("hides picks silently when the Ayla scorer is unavailable", async () => {
    mockedFetchServices.mockResolvedValue({ services: SERVICES });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    mockedFetchRecommendations.mockRejectedValue(new Error("[502] ayla_unavailable"));
    renderScreen();
    await screen.findByRole("region", { name: "Доступные услуги" });
    expect(screen.queryByRole("region", { name: /Ayla рекомендует/ })).not.toBeInTheDocument();
    expect(screen.getByText("Анна Соколова")).toBeInTheDocument();
  });

  // ── Owner ruling 25.08: «Нет displayable WHY → нет блока „Ayla
  //    подобрала"». The section is gated on the reasons the source
  //    actually sends, never on a flag and never on fabricated copy.
  it("коды без фразы: блока нет, каталог и мастера на месте", async () => {
    mockedFetchServices.mockResolvedValue({ services: SERVICES });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    mockedFetchRecommendations.mockResolvedValue(RECS_NO_WHY);
    renderScreen();
    // The branded signature is gone…
    expect(await screen.findByRole("region", { name: "Доступные услуги" })).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: /Ayla рекомендует/ }),
    ).not.toBeInTheDocument();
    // …but the plain catalog underneath is untouched.
    expect(within(screen.getByRole("region", { name: "Доступные услуги" })).getAllByRole("article")).toHaveLength(4);
    expect(screen.getByRole("region", { name: "Мастера" })).toBeInTheDocument();
    // And no generic stand-in WHY was invented in its place.
    for (const fake of [/подходит тебе/i, /выбрано по твоей цели/i, /Ayla рекомендует/i, /подобрано для вас/i]) {
      expect(screen.queryByText(fake)).not.toBeInTheDocument();
    }
  });

  it("незнакомый код фразы не даёт — блок прячется, а не выдумывает копию", async () => {
    mockedFetchServices.mockResolvedValue({ services: SERVICES });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    // Реестр версионируется: источник вправе уехать вперёд. «Мы отстали»
    // не повод подставить человеку выдуманную причину.
    mockedFetchRecommendations.mockResolvedValue(
      decision([
        ranked("svc-2", 1, 1, ["REASON_FROM_THE_FUTURE"]),
        ranked("svc-1", 2, 1, ["ANOTHER_UNKNOWN_CODE"]),
      ]),
    );
    renderScreen();
    await screen.findByRole("region", { name: "Доступные услуги" });
    expect(
      screen.queryByRole("region", { name: /Ayla рекомендует/ }),
    ).not.toBeInTheDocument();
  });

  it("показывает только тех, кого есть чем объяснить", async () => {
    vi.stubEnv("VITE_RECOMMENDATION_SHELF", "1"); // полка за флагом (OD-PILOT-9)
    mockedFetchServices.mockResolvedValue({ services: SERVICES });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    mockedFetchRecommendations.mockResolvedValue(
      decision([
        ranked("svc-2", 1, 1, ["MATCH_UNDETERMINED"]),
        ranked("svc-1", 2, 1, ["MATCH_SERVICE_EXACT"]),
      ]),
    );
    renderScreen();
    const picks = await screen.findByRole("region", { name: /Ayla рекомендует/ });
    const cards = within(picks).getAllByRole("article");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toHaveTextContent("Маникюр");
    expect(within(picks).queryByText("Педикюр")).not.toBeInTheDocument();
  });

  it("shows the error state with retry when the mirror fails", async () => {
    const user = userEvent.setup();
    mockedFetchServices.mockRejectedValueOnce(new Error("[500] http_error"));
    mockedFetchMasters.mockResolvedValue({ masters: [] });
    renderScreen();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    mockHappyPath();
    await user.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(await screen.findByRole("region", { name: "Доступные услуги" })).toBeInTheDocument();
  });

  it("filters the services list by the search query", async () => {
    const user = userEvent.setup();
    mockHappyPath();
    renderScreen();
    const servicesSection = await screen.findByRole("region", { name: "Доступные услуги" });
    await user.type(screen.getByRole("searchbox", { name: "Поиск по услугам" }), "ман");
    expect(within(servicesSection).getByText("Маникюр")).toBeInTheDocument();
    expect(within(servicesSection).queryByText("Педикюр")).not.toBeInTheDocument();
  });

  it("service card navigates to the real service detail screen", async () => {
    const user = userEvent.setup();
    mockHappyPath();
    renderScreen();
    const servicesSection = await screen.findByRole("region", { name: "Доступные услуги" });
    await user.click(within(servicesSection).getByRole("button", { name: /Маникюр/ }));
    expect(await screen.findByText("SERVICE-svc-1")).toBeInTheDocument();
  });

  it("master card navigates to the real master detail screen", async () => {
    const user = userEvent.setup();
    mockHappyPath();
    renderScreen();
    const mastersSection = await screen.findByRole("region", { name: "Мастера" });
    await user.click(within(mastersSection).getByRole("button", { name: /Анна Соколова/ }));
    expect(await screen.findByText("MASTER-mst-1")).toBeInTheDocument();
  });

  // --- DRF-1164 ---------------------------------------------------------

  it("marks a service nobody performs and keeps it in the list", async () => {
    // The pilot defect: this row used to look exactly like the others and
    // led to an empty master list. It stays in the catalog (owner's call)
    // but says so on the card.
    mockedFetchServices.mockResolvedValue({
      services: [
        ...SERVICES,
        {
          id: "svc-1164",
          slug: "gladkaya-kozha",
          name: "Гладкая кожа (комплекс)",
          short_description: "",
          description: "",
          price_from: "9000.00",
          duration_min: 105,
          is_popular: false,
          contraindications: "",
          is_bookable: false,
        },
      ],
    });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    mockedFetchRecommendations.mockResolvedValue(RECS);
    renderScreen();

    const servicesSection = await screen.findByRole("region", { name: "Доступные услуги" });
    expect(within(servicesSection).getByText(/Гладкая кожа/)).toBeInTheDocument();
    expect(
      within(servicesSection).getByText("Сейчас нет свободных мастеров"),
    ).toBeInTheDocument();
    // The label rides in the accessible name too — a screen-reader user
    // must not have to see the badge to learn it.
    expect(
      within(servicesSection).getByRole("button", {
        name: /Гладкая кожа.*нет свободных мастеров/i,
      }),
    ).toBeInTheDocument();
    // Bookable neighbours stay clean.
    expect(
      within(servicesSection)
        .getByRole("button", { name: /Маникюр/ })
        .textContent,
    ).not.toMatch(/нет свободных мастеров/);
  });

  it("prod build: gate removed — real data renders, no coming-soon placeholder", async () => {
    vi.stubEnv("DEV", false);
    mockHappyPath();
    renderScreen();
    expect(await screen.findByRole("region", { name: "Доступные услуги" })).toBeInTheDocument();
    expect(screen.queryByText(/выдуманных/)).not.toBeInTheDocument();
    expect(screen.queryByText("Beauty Place")).not.toBeInTheDocument();
  });
});

// --- DRF-1482: контракт пустого каталога (empty_reason) -----------------
// Spec: docs/screens/customer-catalog-empty-states-spec.md §1–§2.
// Every empty situation gets its OWN message and its OWN recovery CTA —
// the screen is never blank without an explanation.

const SEARCH_NO_MATCH_TEXT = "Не нашла ничего по такому запросу";
const REGION_EMPTY_TEXT = "Пока здесь нет подключённых салонов";
const BOOKING_UNAVAILABLE_TEXT =
  /Подходящие услуги есть, но сейчас нет свободных мест для записи/;

describe("CustomerCatalogScreen — empty states (DRF-1482)", () => {
  it("search_no_match: zero services with masters present explains itself (the defect)", async () => {
    const user = userEvent.setup();
    mockHappyPath();
    renderScreen();
    await screen.findByRole("region", { name: "Доступные услуги" });

    // The audit defect: free-text search filters services to zero while
    // masters stay — the old gate rendered NOTHING here.
    await user.type(
      screen.getByRole("searchbox", { name: "Поиск по услугам" }),
      "несуществующая",
    );

    // Own message + own CTA…
    expect(await screen.findByText(SEARCH_NO_MATCH_TEXT)).toBeInTheDocument();
    // …masters are NOT hidden — they were never the problem…
    expect(screen.getByText("Анна Соколова")).toBeInTheDocument();
    // …and no other state's copy leaks in (positive guard, DRF-1411).
    expect(screen.queryByText(REGION_EMPTY_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByText(BOOKING_UNAVAILABLE_TEXT)).not.toBeInTheDocument();

    // CTA «Посмотреть все услуги» — recovery: полный каталог по
    // каноническому адресу (DRF-1481).
    await user.click(screen.getByRole("button", { name: "Посмотреть все услуги" }));
    expect(screen.getByTestId("path")).toHaveTextContent("/customer/catalog");
    const servicesSection = await screen.findByRole("region", { name: "Доступные услуги" });
    expect(within(servicesSection).getAllByRole("article")).toHaveLength(4);
    expect(screen.queryByText(SEARCH_NO_MATCH_TEXT)).not.toBeInTheDocument();
  });

  it("region_empty: no salons connected — own text and retry CTA", async () => {
    const user = userEvent.setup();
    // Backend predating the field: no empty_reason — the client derives
    // the state from the same signals the server would use.
    mockedFetchServices.mockResolvedValue({ services: [] });
    mockedFetchMasters.mockResolvedValue({ masters: [] });
    mockedFetchRecommendations.mockRejectedValue(new Error("[502] ayla_unavailable"));
    renderScreen();

    expect(await screen.findByText(REGION_EMPTY_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(SEARCH_NO_MATCH_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByText(BOOKING_UNAVAILABLE_TEXT)).not.toBeInTheDocument();
    // Recovery exists and works: salons may have connected since.
    mockedFetchServices.mockResolvedValue({ services: SERVICES });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    await user.click(screen.getByRole("button", { name: "Проверить снова" }));
    expect(await screen.findByRole("region", { name: "Доступные услуги" })).toBeInTheDocument();
    expect(screen.queryByText(REGION_EMPTY_TEXT)).not.toBeInTheDocument();
  });

  it("region_empty: server-provided empty_reason is honoured verbatim", async () => {
    mockedFetchServices.mockResolvedValue({ services: [], empty_reason: "region_empty" });
    mockedFetchMasters.mockResolvedValue({ masters: [] });
    mockedFetchRecommendations.mockRejectedValue(new Error("[502] ayla_unavailable"));
    renderScreen();
    expect(await screen.findByText(REGION_EMPTY_TEXT)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Проверить снова" }),
    ).toBeInTheDocument();
  });

  it("booking_unavailable: services exist but none is bookable — own text and both CTAs", async () => {
    const user = userEvent.setup();
    const unbookable = SERVICES.map((s) => ({ ...s, is_bookable: false }));
    mockedFetchServices.mockResolvedValue({ services: unbookable });
    mockedFetchMasters.mockResolvedValue({ masters: [] });
    mockedFetchRecommendations.mockRejectedValue(new Error("[502] ayla_unavailable"));
    renderScreen();

    expect(await screen.findByText(BOOKING_UNAVAILABLE_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(SEARCH_NO_MATCH_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByText(REGION_EMPTY_TEXT)).not.toBeInTheDocument();
    // The shop window stays (DRF-1164): услуги видны, с честной пометкой.
    expect(screen.getByRole("region", { name: "Доступные услуги" })).toBeInTheDocument();

    // «Смотреть все услуги» ведёт на канонический /customer/catalog.
    await user.click(screen.getByRole("button", { name: "Смотреть все услуги" }));
    expect(screen.getByTestId("path")).toHaveTextContent("/customer/catalog");

    // «Подобрать ещё раз» — recovery: перезагрузить, вдруг места появились.
    mockHappyPath();
    await user.click(screen.getByRole("button", { name: "Подобрать ещё раз" }));
    expect(await screen.findByRole("region", { name: "Мастера" })).toBeInTheDocument();
    expect(screen.queryByText(BOOKING_UNAVAILABLE_TEXT)).not.toBeInTheDocument();
  });

  it("unknown server reason: spec default copy, never a blank screen", async () => {
    // Forward-compat (spec §1 default row): a reason this build does not
    // know renders the booking_unavailable copy — the API can grow new
    // reasons without breaking the client.
    mockedFetchServices.mockResolvedValue({
      services: SERVICES,
      empty_reason: "premium_only",
    });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    mockedFetchRecommendations.mockRejectedValue(new Error("[502] ayla_unavailable"));
    renderScreen();

    // Server value wins over client derivation (services ARE bookable —
    // derivation alone would say "no empty state").
    expect(await screen.findByText(BOOKING_UNAVAILABLE_TEXT)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Подобрать ещё раз" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("premium_only")).not.toBeInTheDocument();
  });

  it("non-empty bookable catalog: no empty state at all (positive guard)", async () => {
    mockHappyPath();
    renderScreen();
    await screen.findByRole("region", { name: "Доступные услуги" });
    expect(screen.queryByText(SEARCH_NO_MATCH_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByText(REGION_EMPTY_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByText(BOOKING_UNAVAILABLE_TEXT)).not.toBeInTheDocument();
  });
});
