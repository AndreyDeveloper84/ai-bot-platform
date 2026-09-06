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
 * Scorer response WITH displayable WHY — the shape the owner ruling
 * 25.08 requires before the branded «Ayla подобрала» block may render.
 */
const RECS = {
  recommendations: [
    { service_id: "svc-2", score: 0.95, reasons: ["Свободно раньше всех остальных"] },
    { service_id: "svc-1", score: 0.9, reasons: ["20 минут от тебя, рейтинг 4.9", "Ты уже была на этой услуге"] },
    { service_id: "svc-4", score: 0.85, reasons: ["Подходит под твою цель — снижение стресса"] },
    { service_id: "svc-3", score: 0.8, reasons: ["У мастера 7 лет опыта в твоей категории"] },
  ],
};

/**
 * TODAY'S RUNTIME shape: `POST /recommendations` returns `{service_id,
 * score}` and nothing else — no WHY exists anywhere in the response.
 */
const RECS_NO_WHY = {
  recommendations: [
    { service_id: "svc-2", score: 0.95 },
    { service_id: "svc-1", score: 0.9 },
    { service_id: "svc-4", score: 0.85 },
  ],
};

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
  it("renders real services and masters; picks capped at 3 in score order", async () => {
    mockHappyPath();
    renderScreen();
    const picks = await screen.findByRole("region", { name: /Ayla подобрала/ });
    const pickCards = within(picks).getAllByRole("article");
    expect(pickCards).toHaveLength(3);
    // Score order: Педикюр (0.95), Маникюр (0.9), Брови (0.85) — Массаж out.
    expect(pickCards[0]).toHaveTextContent("Педикюр");
    expect(pickCards[1]).toHaveTextContent("Маникюр");
    expect(pickCards[2]).toHaveTextContent("Брови");
    // WHAT + WHY: every branded pick renders the reasons the SOURCE
    // sent, verbatim — nothing is synthesised client-side.
    expect(pickCards[0]).toHaveTextContent("Свободно раньше всех остальных");
    expect(pickCards[1]).toHaveTextContent("20 минут от тебя, рейтинг 4.9");
    expect(pickCards[1]).toHaveTextContent("Ты уже была на этой услуге");
    expect(pickCards[2]).toHaveTextContent("Подходит под твою цель — снижение стресса");

    const servicesSection = screen.getByRole("region", { name: "Услуги" });
    expect(within(servicesSection).getAllByRole("article")).toHaveLength(4);

    const mastersSection = screen.getByRole("region", { name: "Мастера" });
    expect(within(mastersSection).getByText("Анна Соколова")).toBeInTheDocument();
    expect(within(mastersSection).getByText(/4\.9/)).toBeInTheDocument();
    expect(within(mastersSection).getByText("Карина Ли")).toBeInTheDocument();
  });

  it("never renders the old fake stub salons", async () => {
    mockHappyPath();
    renderScreen();
    await screen.findByRole("region", { name: "Услуги" });
    for (const fake of ["Beauty Place", "Формула тела", "Студия Лотос", "Casa Bella"]) {
      expect(screen.queryByText(fake)).not.toBeInTheDocument();
    }
  });

  it("hides picks silently when the Ayla scorer is unavailable", async () => {
    mockedFetchServices.mockResolvedValue({ services: SERVICES });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    mockedFetchRecommendations.mockRejectedValue(new Error("[502] ayla_unavailable"));
    renderScreen();
    await screen.findByRole("region", { name: "Услуги" });
    expect(screen.queryByRole("region", { name: /Ayla подобрала/ })).not.toBeInTheDocument();
    expect(screen.getByText("Анна Соколова")).toBeInTheDocument();
  });

  // ── Owner ruling 25.08: «Нет displayable WHY → нет блока „Ayla
  //    подобрала"». The section is gated on the reasons the source
  //    actually sends, never on a flag and never on fabricated copy.
  it("score-only response: no branded block, catalog and masters stay", async () => {
    mockedFetchServices.mockResolvedValue({ services: SERVICES });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    mockedFetchRecommendations.mockResolvedValue(RECS_NO_WHY);
    renderScreen();
    // The branded signature is gone…
    expect(await screen.findByRole("region", { name: "Услуги" })).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: /Ayla подобрала/ }),
    ).not.toBeInTheDocument();
    // …but the plain catalog underneath is untouched.
    expect(within(screen.getByRole("region", { name: "Услуги" })).getAllByRole("article")).toHaveLength(4);
    expect(screen.getByRole("region", { name: "Мастера" })).toBeInTheDocument();
    // And no generic stand-in WHY was invented in its place.
    for (const fake of [/подходит тебе/i, /выбрано по твоей цели/i, /Ayla рекомендует/i, /подобрано для вас/i]) {
      expect(screen.queryByText(fake)).not.toBeInTheDocument();
    }
  });

  it("drops picks whose WHY is blank; hides the block when none is left", async () => {
    mockedFetchServices.mockResolvedValue({ services: SERVICES });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    mockedFetchRecommendations.mockResolvedValue({
      recommendations: [
        { service_id: "svc-2", score: 0.95, reasons: [] },
        { service_id: "svc-1", score: 0.9, reasons: ["   "] },
        { service_id: "svc-4", score: 0.85, reasons: null },
      ],
    });
    renderScreen();
    await screen.findByRole("region", { name: "Услуги" });
    expect(
      screen.queryByRole("region", { name: /Ayla подобрала/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps only the picks that carry WHY when the source is partial", async () => {
    mockedFetchServices.mockResolvedValue({ services: SERVICES });
    mockedFetchMasters.mockResolvedValue({ masters: MASTERS });
    mockedFetchRecommendations.mockResolvedValue({
      recommendations: [
        { service_id: "svc-2", score: 0.95 },
        { service_id: "svc-1", score: 0.9, reasons: ["20 минут от тебя, рейтинг 4.9"] },
      ],
    });
    renderScreen();
    const picks = await screen.findByRole("region", { name: /Ayla подобрала/ });
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
    expect(await screen.findByRole("region", { name: "Услуги" })).toBeInTheDocument();
  });

  it("filters the services list by the search query", async () => {
    const user = userEvent.setup();
    mockHappyPath();
    renderScreen();
    const servicesSection = await screen.findByRole("region", { name: "Услуги" });
    await user.type(screen.getByRole("searchbox", { name: "Поиск по услугам" }), "ман");
    expect(within(servicesSection).getByText("Маникюр")).toBeInTheDocument();
    expect(within(servicesSection).queryByText("Педикюр")).not.toBeInTheDocument();
  });

  it("service card navigates to the real service detail screen", async () => {
    const user = userEvent.setup();
    mockHappyPath();
    renderScreen();
    const servicesSection = await screen.findByRole("region", { name: "Услуги" });
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

    const servicesSection = await screen.findByRole("region", { name: "Услуги" });
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
    expect(await screen.findByRole("region", { name: "Услуги" })).toBeInTheDocument();
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
    await screen.findByRole("region", { name: "Услуги" });

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
    const servicesSection = await screen.findByRole("region", { name: "Услуги" });
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
    expect(await screen.findByRole("region", { name: "Услуги" })).toBeInTheDocument();
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
    expect(screen.getByRole("region", { name: "Услуги" })).toBeInTheDocument();

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
    await screen.findByRole("region", { name: "Услуги" });
    expect(screen.queryByText(SEARCH_NO_MATCH_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByText(REGION_EMPTY_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByText(BOOKING_UNAVAILABLE_TEXT)).not.toBeInTheDocument();
  });
});
