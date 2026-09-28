/**
 * Карточка «Продолжить настройку» и вход по готовности (DRF-1807, M15).
 *
 * - карточка есть, пока readiness не закрыт: перечисляет незакрытые пункты
 *   (unavailable не в счёт) и ведёт на экран 01; закрыт — карточки нет;
 *   ручка упала — карточки нет (кабинет важнее);
 * - корень `/`: не готово → экран 01, готово → «Мой день», сеть упала —
 *   «Мой день».
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return { ...original, getOnboardingReadiness: vi.fn(), getPublicationStatus: vi.fn() };
});

import {
  getOnboardingReadiness,
  getPublicationStatus,
  type OnboardingReadiness,
  type PublicationStatus,
} from "../lib/master-api";
import { PUBLICATION_COPY } from "../screens/MasterPublicationScreen";
import { SETUP_CARD_CTA, SETUP_CARD_TITLE, SetupProgressCard } from "./SetupProgressCard";
import { SoloSetupGate } from "./SoloSetupGate";

const mocked = vi.mocked(getOnboardingReadiness);
const mockedPublication = vi.mocked(getPublicationStatus);

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

describe("вход на отправку на «Моём дне» (§6-квартер, вопрос 1)", () => {
  // Экран 01 при «готов» не открывается, и карточка настройки пряталась —
  // вход на отправку был недостижим. Теперь он здесь, но только для профиля,
  // который ещё не отправлен, и только связанному мастеру.
  const READY_LINKED: OnboardingReadiness = {
    ...READY,
    identity: { state: "linked", link_status: "LINKED" },
  };
  const status = (profile_status: string): PublicationStatus => ({
    specialist_id: "s-1",
    profile_status,
    readiness: { status: "READY", missing: [] },
    last_request: null,
  });

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

  it("готово, связан, профиль draft — зовёт отправить и ведёт на экран отправки", async () => {
    mocked.mockResolvedValue(READY_LINKED);
    mockedPublication.mockResolvedValue(status("draft"));
    renderCard();

    const card = await screen.findByTestId("submit-card");
    expect(within(card).getByRole("heading", { name: PUBLICATION_COPY.readyTitle })).toBeInTheDocument();
    expect(screen.queryByTestId("setup-card")).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: PUBLICATION_COPY.submit }));
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/publication");
  });

  it.each(["pending", "active"])("профиль %s — уже отправлен, не зовём", async (profileStatus) => {
    mocked.mockResolvedValue(READY_LINKED);
    mockedPublication.mockResolvedValue(status(profileStatus));
    renderCard();
    await waitFor(() => expect(mockedPublication).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("submit-card")).toBeNull();
  });

  it("статус не прочитан — не знаем, отправлен ли, карточки нет", async () => {
    mocked.mockResolvedValue(READY_LINKED);
    mockedPublication.mockRejectedValue(new Error("down"));
    renderCard();
    await waitFor(() => expect(mockedPublication).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("submit-card")).toBeNull();
  });

  it("готово, но личность не связана — отправить нельзя, статус не спрашиваем", async () => {
    mocked.mockResolvedValue(READY);
    renderCard();
    await waitFor(() => expect(mocked).toHaveBeenCalledTimes(1));
    expect(mockedPublication).not.toHaveBeenCalled();
    expect(screen.queryByTestId("submit-card")).toBeNull();
  });

  it("не готово — прежняя карточка настройки, входа на отправку нет", async () => {
    mocked.mockResolvedValue(NOT_READY);
    renderCard();
    await screen.findByTestId("setup-card");
    expect(screen.queryByTestId("submit-card")).toBeNull();
    expect(mockedPublication).not.toHaveBeenCalled();
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
