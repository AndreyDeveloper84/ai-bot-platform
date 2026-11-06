/**
 * DRF-2755 — расстояние до мастера на его карточке, за флагом
 * `VITE_MASTER_CARD_P1`. Расстояние считает каталог (`GET /masters/{id}`
 * с `?lat&lon`, узлы ручки — `test_master_detail_distance_2755.py`); экран
 * только спрашивает координаты по нажатию и показывает ответ.
 *
 * Что заперто:
 *  - флаг выключен → блока нет, ОС не спрашивается;
 *  - пояснение стоит ДО вызова ОС; без нажатия координаты не запрашиваются;
 *  - есть / ноль / метры / километры → подпись формата списка;
 *  - `null`, поля нет, сервер не ответил → блока нет (не «0 м»);
 *  - отказ ОС → подпись отказа и повтор; повтор берёт новое место;
 *  - другой мастер → прежний исход не его, поздний ответ отброшен;
 *  - координаты не остаются ни в хранилищах, ни в адресе;
 *  - порядок: имя и рейтинг → расстояние → описание.
 *
 * Данные синтетические.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, getInitData: () => "test-init-data" };
});

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCustomerMaster: vi.fn() };
});

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchServices: vi.fn() };
});

vi.mock("../lib/nearby", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/nearby")>();
  return { ...original, locateOnce: vi.fn() };
});

import { fetchServices } from "../lib/api";
import { getCustomerMaster } from "../lib/customer-booking";
import { locateOnce, NEARBY_LOCATING } from "../lib/nearby";
import { resetBooking } from "../state/booking";
import {
  CustomerMasterDetailScreen,
  MASTER_DISTANCE_BUTTON,
  MASTER_DISTANCE_DENIED,
  MASTER_DISTANCE_EXPLANATION,
} from "./CustomerMasterDetailScreen";

const mockedMaster = vi.mocked(getCustomerMaster);
const mockedCatalog = vi.mocked(fetchServices);
const mockedLocate = vi.mocked(locateOnce);

const HERE = { lat: 53.2, lon: 45.0 };
const THERE = { lat: 53.25, lon: 45.05 };

function masterAnswer(over: Record<string, unknown> = {}) {
  return {
    master: {
      id: "m-1",
      name: "Мария Петрова",
      specialization: "массаж",
      bio: "Работает с осанкой",
      experience: "7 лет",
      rating: "4.9",
      photo_url: "",
      service_ids: [],
      ...over,
    },
  } as unknown as Awaited<ReturnType<typeof getCustomerMaster>>;
}

/** Ответ карточки: без координат — без поля; с координатами — `byCoords`. */
function answerWith(byCoords: (coords: { lat: number; lon: number }) => Record<string, unknown>) {
  mockedMaster.mockImplementation(async (id, coords) =>
    masterAnswer({ id, ...(coords ? byCoords(coords) : {}) }),
  );
}

let where = "";
function Probe() {
  const location = useLocation();
  where = `${location.pathname}${location.search}`;
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate("/customer/masters/m-2")}>
      К ДРУГОМУ МАСТЕРУ
    </button>
  );
}

function renderCard() {
  return render(
    <MemoryRouter initialEntries={["/customer/masters/m-1"]}>
      <Probe />
      <Routes>
        <Route path="/customer/masters/:masterId" element={<CustomerMasterDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

function distanceText(): string | null {
  return screen.queryByTestId("master-distance")?.textContent ?? null;
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
  localStorage.clear();
  sessionStorage.clear();
  Object.defineProperty(window.navigator, "onLine", { value: true, configurable: true });
  vi.stubEnv("VITE_MASTER_CARD_P1", "1");
  mockedCatalog.mockResolvedValue({ services: [] } as unknown as Awaited<
    ReturnType<typeof fetchServices>
  >);
  mockedLocate.mockResolvedValue(HERE);
  answerWith(() => ({ distance_meters: 1303 }));
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("флаг выключен — карточка прежняя", () => {
  it("блока расстояния нет, ОС не спрашивается", async () => {
    vi.stubEnv("VITE_MASTER_CARD_P1", "");
    renderCard();

    expect(await screen.findByRole("button", { name: "Выбрать время" })).toBeEnabled();
    expect(screen.queryByTestId("master-distance-block")).toBeNull();
    expect(screen.queryByText(MASTER_DISTANCE_EXPLANATION)).toBeNull();
    expect(mockedLocate).not.toHaveBeenCalled();
  });
});

describe("согласие до вызова ОС", () => {
  it("пояснение и кнопка видны; без нажатия координаты не запрашиваются", async () => {
    renderCard();

    expect(await screen.findByText(MASTER_DISTANCE_EXPLANATION)).toBeVisible();
    expect(screen.getByRole("button", { name: MASTER_DISTANCE_BUTTON })).toBeEnabled();
    expect(mockedLocate).not.toHaveBeenCalled();
    expect(mockedMaster).toHaveBeenCalledTimes(1);
    expect(mockedMaster).toHaveBeenCalledWith("m-1");
  });

  it("без сети кнопка закрыта", async () => {
    Object.defineProperty(window.navigator, "onLine", { value: false, configurable: true });
    renderCard();

    expect(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON })).toBeDisabled();
  });
});

describe("расстояние есть", () => {
  it.each([
    [1303, "1.3 км"],
    [850, "850 м"],
    [0, "0 м"],
  ])("каталог ответил %s → «%s»", async (meters, label) => {
    answerWith(() => ({ distance_meters: meters }));
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON }));

    await waitFor(() => expect(distanceText()).toBe(label));
    expect(mockedMaster).toHaveBeenLastCalledWith("m-1", HERE);
    expect(screen.queryByRole("button", { name: MASTER_DISTANCE_BUTTON })).toBeNull();
    expect(screen.queryByText(MASTER_DISTANCE_EXPLANATION)).toBeNull();
  });

  it("пока ОС отвечает — «Определяю местоположение…»", async () => {
    let release: (c: typeof HERE) => void = () => undefined;
    mockedLocate.mockReturnValue(new Promise((resolve) => (release = resolve)));
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON }));

    expect(screen.getByText(NEARBY_LOCATING)).toBeVisible();
    await act(async () => release(HERE));
    await waitFor(() => expect(distanceText()).toBe("1.3 км"));
  });
});

describe("расстояния нет — блока нет, не «0 м»", () => {
  it.each([
    ["каталог ответил null", () => ({ distance_meters: null })],
    ["поля нет (флаг каталога, источник лежит)", () => ({})],
    ["в поле не число", () => ({ distance_meters: "1303" })],
    ["отрицательное", () => ({ distance_meters: -5 })],
  ])("%s", async (_name, byCoords) => {
    answerWith(byCoords);
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON }));

    await waitFor(() => expect(screen.queryByTestId("master-distance-block")).toBeNull());
    expect(distanceText()).toBeNull();
    expect(screen.queryByText("0 м")).toBeNull();
    // Положительная пара: карточка цела.
    expect(screen.getByText("Мария Петрова", { selector: ".customer-master__name" })).toBeVisible();
  });

  it("сервер не ответил на запрос с координатами — блока нет, карточка цела", async () => {
    mockedMaster.mockImplementation(async (id, coords) => {
      if (coords) throw new Error("boom");
      return masterAnswer({ id });
    });
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON }));

    await waitFor(() => expect(screen.queryByTestId("master-distance-block")).toBeNull());
    expect(screen.getByRole("button", { name: "Выбрать время" })).toBeEnabled();
  });
});

describe("отказ ОС и повтор", () => {
  it("отказ — подпись отказа, сервер с координатами не зовётся, кнопка для повтора", async () => {
    mockedLocate.mockResolvedValue(null);
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON }));

    expect(await screen.findByText(MASTER_DISTANCE_DENIED)).toBeVisible();
    expect(screen.getByRole("button", { name: MASTER_DISTANCE_BUTTON })).toBeEnabled();
    expect(mockedMaster).toHaveBeenCalledTimes(1);
    expect(distanceText()).toBeNull();
  });

  it("повтор после отказа берёт новое место и новое расстояние", async () => {
    mockedLocate.mockResolvedValueOnce(null).mockResolvedValueOnce(THERE);
    answerWith((c) => ({ distance_meters: c.lat === THERE.lat ? 420 : 1303 }));
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON }));
    await screen.findByText(MASTER_DISTANCE_DENIED);
    await user.click(screen.getByRole("button", { name: MASTER_DISTANCE_BUTTON }));

    await waitFor(() => expect(distanceText()).toBe("420 м"));
    expect(mockedMaster).toHaveBeenLastCalledWith("m-1", THERE);
    expect(mockedLocate).toHaveBeenCalledTimes(2);
  });
});

describe("другой мастер", () => {
  it("прежнее расстояние не переезжает на другого мастера", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON }));
    await waitFor(() => expect(distanceText()).toBe("1.3 км"));

    await user.click(screen.getByRole("button", { name: "К ДРУГОМУ МАСТЕРУ" }));

    expect(await screen.findByText(MASTER_DISTANCE_EXPLANATION)).toBeVisible();
    expect(distanceText()).toBeNull();
  });

  it("поздний ответ по прежнему мастеру отброшен", async () => {
    let release: () => void = () => undefined;
    mockedMaster.mockImplementation(async (id, coords) => {
      if (coords) {
        await new Promise<void>((resolve) => (release = resolve));
        return masterAnswer({ id, distance_meters: 1303 });
      }
      return masterAnswer({ id });
    });
    const user = userEvent.setup();
    renderCard();
    await user.click(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON }));
    await waitFor(() => expect(mockedMaster).toHaveBeenCalledWith("m-1", HERE));

    await user.click(screen.getByRole("button", { name: "К ДРУГОМУ МАСТЕРУ" }));
    await screen.findByText(MASTER_DISTANCE_EXPLANATION);
    await act(async () => release());

    expect(distanceText()).toBeNull();
    expect(screen.getByText(MASTER_DISTANCE_EXPLANATION)).toBeVisible();
  });
});

describe("координаты не хранятся (D3)", () => {
  it("ни в хранилищах, ни в адресе экрана", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON }));
    await waitFor(() => expect(distanceText()).toBe("1.3 км"));

    const stored = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage });
    expect(stored).not.toContain("53.2");
    expect(where).toBe("/customer/masters/m-1");
  });
});

describe("порядок блоков", () => {
  it("имя и рейтинг → расстояние → описание", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(await screen.findByRole("button", { name: MASTER_DISTANCE_BUTTON }));
    await waitFor(() => expect(distanceText()).toBe("1.3 км"));

    const name = screen.getByText("Мария Петрова", { selector: ".customer-master__name" });
    const block = screen.getByTestId("master-distance-block");
    const bio = screen.getByText("Работает с осанкой");
    expect(name.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(block.compareDocumentPosition(bio) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
