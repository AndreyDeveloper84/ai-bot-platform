/**
 * Home routing test (pilot phase 3.2, orchestrator decision):
 * `/customer/main` = «Мои записи» (real records) — records matter more
 * than wellness for the pilot. The stub wellness dashboard moved to
 * `/customer/wellness` (still gated in prod by STUB_SURFACES_ENABLED).
 *
 * App boots through `getMe()` (role resolution) — mocked to a plain
 * customer. The bookings + catalog endpoints are mocked at the HTTP
 * layer (`../lib/api`).
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return { ...original, getMe: vi.fn() };
});

vi.mock("./lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/api")>();
  return {
    ...original,
    fetchMyBookings: vi.fn(),
    fetchServices: vi.fn(),
    fetchMasters: vi.fn(),
    fetchRecommendations: vi.fn(),
  };
});

import { getMe, type MeResponse } from "./lib/admin-api";
import {
  fetchMasters,
  fetchMyBookings,
  fetchRecommendations,
  fetchServices,
} from "./lib/api";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedList = vi.mocked(fetchMyBookings);
const mockedServices = vi.mocked(fetchServices);
const mockedMasters = vi.mocked(fetchMasters);
const mockedRecs = vi.mocked(fetchRecommendations);

const CUSTOMER_ME: MeResponse = {
  user: { id: "u-1", name: "Ольга", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Demo", slug: "demo" },
  role: "customer",
  capabilities: [],
  is_customer: true,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: false,
  master_id: null,
  landing_path: "/customer/main",
  is_solo_provider: false,
};

function renderAppAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedGetMe.mockResolvedValue(CUSTOMER_ME);
  mockedList.mockResolvedValue({ items: [], next_cursor: null });
  mockedServices.mockResolvedValue({ services: [] });
  mockedMasters.mockResolvedValue({ masters: [] });
  mockedRecs.mockResolvedValue({ recommendations: [] });
});

describe("home routing (phase 3.2)", () => {
  it("/customer/main renders the real records screen, not the wellness stub", async () => {
    renderAppAt("/customer/main");
    // Records empty state (both sections empty per mocks) — real screen.
    expect(await screen.findByText(/Пока записей нет/)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Записи" }),
    ).toBeInTheDocument();
    // Wellness stub content must NOT be here.
    expect(screen.queryByText(/Вода:/)).not.toBeInTheDocument();
  });

  it("/customer/wellness keeps the (DEV) wellness dashboard reachable", async () => {
    // The dashboard reads are wired now, so a dev build without `?stub=`
    // goes to the network. This test is about ROUTING — that the path
    // still resolves to the dashboard — so it asks for the stub data
    // explicitly rather than standing up a backend.
    window.history.replaceState({}, "", "/customer/wellness?stub=default");
    renderAppAt("/customer/wellness");
    expect(await screen.findByText(/Вода:/)).toBeInTheDocument();
  });
});
