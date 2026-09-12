/**
 * Карточка «Продолжить настройку» и вход по готовности (DRF-1807, M15).
 *
 * - карточка есть, пока readiness не закрыт: перечисляет незакрытые пункты
 *   (unavailable не в счёт) и ведёт на экран 01; закрыт — карточки нет;
 *   ручка упала — карточки нет (кабинет важнее);
 * - корень `/`: не готово → экран 01, готово → «Мой день», сеть упала —
 *   «Мой день».
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return { ...original, getOnboardingReadiness: vi.fn() };
});

import { getOnboardingReadiness, type OnboardingReadiness } from "../lib/master-api";
import { SETUP_CARD_CTA, SETUP_CARD_TITLE, SetupProgressCard } from "./SetupProgressCard";
import { SoloSetupGate } from "./SoloSetupGate";

const mocked = vi.mocked(getOnboardingReadiness);

const NOT_READY: OnboardingReadiness = {
  ready: false,
  blocking: ["services:missing", "location:unavailable", "hours:missing"],
  items: [
    { key: "services", state: "missing", detail: {}, reason: null, deep_link: "/solo/services" },
    {
      key: "location",
      state: "unavailable",
      detail: {},
      reason: "capability_not_built",
      deep_link: "/solo/settings",
    },
    { key: "hours", state: "missing", detail: {}, reason: null, deep_link: "/solo/schedule" },
    { key: "profile", state: "done", detail: {}, reason: null, deep_link: "/solo/profile" },
  ],
  identity: { state: "pending", link_status: "PENDING" },
  setup_state: "SETUP_PENDING",
  sale_block: "SETUP_PENDING",
};

const READY: OnboardingReadiness = {
  ...NOT_READY,
  ready: true,
  blocking: [],
  items: NOT_READY.items.map((i) => ({ ...i, state: "done" })),
  setup_state: "READY",
  sale_block: null,
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SetupProgressCard", () => {
  function renderCard() {
    render(
      <MemoryRouter initialEntries={["/solo/my-day"]}>
        <Routes>
          <Route path="/solo/my-day" element={<SetupProgressCard />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("пока не готово — перечисляет незакрытые пункты и ведёт на экран 01", async () => {
    mocked.mockResolvedValue(NOT_READY);
    renderCard();
    const card = await screen.findByTestId("setup-card");
    expect(card).toHaveTextContent(SETUP_CARD_TITLE);
    expect(card).toHaveTextContent("Услуги и цены");
    expect(card).toHaveTextContent("Расписание");
    // unavailable и done — не в списке; ни процентов, ни «из N».
    expect(card).not.toHaveTextContent("Место работы");
    expect(card).not.toHaveTextContent("Профиль для клиентов");
    expect(card.textContent).not.toMatch(/%|(^|\s)из \d/);
    fireEvent.click(screen.getByRole("button", { name: SETUP_CARD_CTA }));
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/setup");
  });

  it("готово — карточки нет", async () => {
    mocked.mockResolvedValue(READY);
    renderCard();
    await waitFor(() => expect(mocked).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId("setup-card")).toBeNull());
  });

  it("ручка упала — карточки нет", async () => {
    mocked.mockRejectedValue(new Error("down"));
    renderCard();
    await waitFor(() => expect(mocked).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("setup-card")).toBeNull();
  });
});

describe("SoloSetupGate", () => {
  function renderGate() {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<SoloSetupGate />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("не готово → экран 01", async () => {
    mocked.mockResolvedValue(NOT_READY);
    renderGate();
    expect(await screen.findByTestId("location")).toHaveTextContent("/solo/setup");
  });

  it("готово → «Мой день»", async () => {
    mocked.mockResolvedValue(READY);
    renderGate();
    expect(await screen.findByTestId("location")).toHaveTextContent("/solo/my-day");
  });

  it("сеть упала → «Мой день»", async () => {
    mocked.mockRejectedValue(new Error("down"));
    renderGate();
    expect(await screen.findByTestId("location")).toHaveTextContent("/solo/my-day");
  });
});
