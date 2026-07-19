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
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
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
  { id: "svc-1", slug: "manikyur", name: "Маникюр", short_description: "Классический", description: "", price_from: "1800.00", duration_min: 60, is_popular: true, contraindications: "" },
  { id: "svc-2", slug: "pedikyur", name: "Педикюр", short_description: "", description: "", price_from: "2200.00", duration_min: 90, is_popular: false, contraindications: "" },
  { id: "svc-3", slug: "massazh", name: "Массаж", short_description: "", description: "", price_from: "3000.00", duration_min: 60, is_popular: false, contraindications: "" },
  { id: "svc-4", slug: "brovi", name: "Брови", short_description: "", description: "", price_from: "1200.00", duration_min: 30, is_popular: false, contraindications: "" },
];

const MASTERS: Master[] = [
  { id: "mst-1", name: "Анна Соколова", specialization: "nail-мастер", bio: "", experience: "5 лет", rating: "4.9", photo_url: "" },
  { id: "mst-2", name: "Карина Ли", specialization: "бровист", bio: "", experience: "3 года", rating: null, photo_url: "" },
];

const RECS = {
  recommendations: [
    { service_id: "svc-2", score: 0.95 },
    { service_id: "svc-1", score: 0.9 },
    { service_id: "svc-4", score: 0.85 },
    { service_id: "svc-3", score: 0.8 },
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

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/catalog"]}>
      <Routes>
        <Route path="/customer/catalog" element={<CustomerCatalogScreen />} />
        <Route path="/catalog/:serviceId" element={<ServiceProbe />} />
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

  it("prod build: gate removed — real data renders, no coming-soon placeholder", async () => {
    vi.stubEnv("DEV", false);
    mockHappyPath();
    renderScreen();
    expect(await screen.findByRole("region", { name: "Услуги" })).toBeInTheDocument();
    expect(screen.queryByText(/выдуманных/)).not.toBeInTheDocument();
    expect(screen.queryByText("Beauty Place")).not.toBeInTheDocument();
  });
});
