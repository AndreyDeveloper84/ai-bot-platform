/**
 * Экран 05 «Место работы» (DRF-1811, M19) — targeted proof.
 *
 * Что сторожится (карта §2.4, столбец «Как проверим»):
 *   - два формата = две записи: кабинет → POST место, выезд → POST зона;
 *   - бейдж «Будет виден клиентам» — только со слов каталога (CONFIRMED);
 *     при REVIEW_REQUIRED его нет;
 *   - на экране нет ни одного `<img>` при любом ответе (фасада нет —
 *     источника нет); превью карты — ссылка по координатам, и только при них;
 *   - подсказки: 503 (геокодер не настроен) → ручной ввод без ошибки на
 *     экране; 200 → подсказки, выбор подставляет адрес;
 *   - «Как клиенту вас найти?» ≤ 200 со счётчиком от длины;
 *   - радио «Только в некоторых районах» отсутствует по построению; «Настрою
 *     позже» уходит как `later`, не как `whole_city`;
 *   - отказ каталога `place_already_set` показан по имени.
 */

import { act, configure, fireEvent, getConfig, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getServiceLocations: vi.fn(),
    createServiceLocation: vi.fn(),
    patchServiceLocation: vi.fn(),
    suggestAddress: vi.fn(),
  };
});

import { ApiError } from "../lib/api";
import {
  createServiceLocation,
  getServiceLocations,
  patchServiceLocation,
  suggestAddress,
  type ServiceArea,
  type ServiceLocationsState,
  type ServicePlace,
} from "../lib/master-api";
import { MasterPlaceScreen, NOTE_MAX, PLACE_COPY, mapLink } from "./MasterPlaceScreen";

const GUARD_ASYNC_TIMEOUT_MS = 20;
let previousAsyncUtilTimeout = 1000;

beforeAll(() => {
  previousAsyncUtilTimeout = getConfig().asyncUtilTimeout;
  configure({ asyncUtilTimeout: GUARD_ASYNC_TIMEOUT_MS });
});

afterAll(() => {
  configure({ asyncUtilTimeout: previousAsyncUtilTimeout });
});

const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

const mockedGet = vi.mocked(getServiceLocations);
const mockedCreate = vi.mocked(createServiceLocation);
const mockedPatch = vi.mocked(patchServiceLocation);
const mockedSuggest = vi.mocked(suggestAddress);

function place(status: string, opts: Partial<ServicePlace> = {}): ServicePlace {
  return {
    id: "pl-1",
    kind: "private_studio",
    label: "Студия у метро",
    address: "Москва, Тверская, 1",
    city: "Москва",
    note_for_client: "Вход со двора",
    status,
    geocode_status: "pending",
    latitude: null,
    longitude: null,
    shown_to_clients_after_publication: status === "confirmed",
    ...opts,
  };
}

function area(coverage: "whole_city" | "later"): ServiceArea {
  return { id: "ar-1", kind: "mobile", city: "Москва", coverage, configured: coverage === "whole_city" };
}

function state(places: ServicePlace[] = [], areas: ServiceArea[] = []): ServiceLocationsState {
  return { specialist_id: "m1", city: "Москва", places, areas };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/solo/place"]}>
      <Routes>
        <Route path="/solo/place" element={<MasterPlaceScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

const check = (testId: string): HTMLInputElement =>
  within(screen.getByTestId(testId)).getByRole("checkbox") as HTMLInputElement;

beforeEach(() => {
  vi.clearAllMocks();
  mockedGet.mockResolvedValue(state());
  mockedSuggest.mockResolvedValue({ available: true, suggestions: [] });
});

describe("два формата = две записи", () => {
  it("кабинет → POST место, выезд → POST зона; сводка показывает обе", async () => {
    mockedCreate
      .mockResolvedValueOnce(state([place("review_required")]))
      .mockResolvedValueOnce(state([place("review_required")], [area("whole_city")]));
    renderScreen();
    await settle();

    fireEvent.click(check("format-private_studio"));
    fireEvent.click(check("format-mobile"));
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.next }));

    fireEvent.change(screen.getByLabelText(PLACE_COPY.addressField), { target: { value: "Москва, Тверская, 1" } });
    fireEvent.change(screen.getByLabelText(PLACE_COPY.noteField), { target: { value: "Вход со двора" } });
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.saveAddress }));
    await settle();

    // Кадр 5.3 — выезд, город из ответа.
    expect(screen.getByText(PLACE_COPY.areaCity("Москва"))).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId("coverage-whole_city")).getByRole("radio"));
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.saveArea }));
    await settle();

    expect(mockedCreate).toHaveBeenCalledTimes(2);
    expect(mockedCreate.mock.calls[0]?.[0]).toEqual({
      kind: "private_studio",
      address: "Москва, Тверская, 1",
      label: "",
      note_for_client: "Вход со двора",
    });
    expect(mockedCreate.mock.calls[1]?.[0]).toEqual({ kind: "mobile", coverage: "whole_city" });
    expect(screen.getByTestId("place-pl-1")).toBeInTheDocument();
    expect(screen.getByTestId("area-ar-1")).toBeInTheDocument();
    expect(screen.getByTestId("badge-ar-1")).toHaveTextContent(PLACE_COPY.badgeWholeCity);
  });
});

describe("бейдж «Будет виден клиентам» — только при CONFIRMED", () => {
  it("CONFIRMED → бейдж есть (положительный контроль)", async () => {
    mockedGet.mockResolvedValue(state([place("confirmed")]));
    renderScreen();
    await settle();
    expect(screen.getByTestId("badge-pl-1")).toHaveTextContent(PLACE_COPY.badgeVisible);
  });

  it("REVIEW_REQUIRED → бейджа нет, вместо него «На проверке»", async () => {
    mockedGet.mockResolvedValue(state([place("review_required")]));
    renderScreen();
    await settle();
    expect(screen.getByTestId("badge-pl-1")).toHaveTextContent(PLACE_COPY.badgeReview);
    expect(screen.queryByText(PLACE_COPY.badgeVisible)).toBeNull();
  });

  it("слово каталога сильнее статуса: shown=false при status=confirmed → бейджа нет", async () => {
    // Экран не выводит право из status сам — только из поля каталога.
    mockedGet.mockResolvedValue(state([place("confirmed", { shown_to_clients_after_publication: false })]));
    renderScreen();
    await settle();
    expect(screen.queryByText(PLACE_COPY.badgeVisible)).toBeNull();
  });
});

describe("нет фото фасада; карта — только ссылка и только при координатах", () => {
  it("ни одного <img> ни в одном кадре; без координат — нет и ссылки", async () => {
    mockedGet.mockResolvedValue(state([place("confirmed")], [area("later")]));
    const { container } = renderScreen();
    await settle();

    // ПРИСУТСТВИЕ: сводка отрисована.
    expect(screen.getByTestId("place-pl-1")).toBeInTheDocument();
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.querySelectorAll("iframe")).toHaveLength(0);
    expect(screen.queryByText(new RegExp(PLACE_COPY.openMap))).toBeNull();
    expect(screen.getByText(PLACE_COPY.noCoords)).toBeInTheDocument();

    // И в кадрах ввода — тоже без картинок.
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.changeKind }));
    expect(container.querySelectorAll("img")).toHaveLength(0);
  });

  it("с координатами из ответа — ссылка «Открыть на карте» с координатами, без встраивания", async () => {
    mockedGet.mockResolvedValue(state([place("confirmed", { latitude: "55.7600", longitude: "37.6100" })]));
    const { container } = renderScreen();
    await settle();

    const link = screen.getByRole("link", { name: new RegExp(PLACE_COPY.openMap) });
    expect(link).toHaveAttribute("href", mapLink("55.7600", "37.6100"));
    expect(link).toHaveTextContent("55.7600, 37.6100");
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.querySelectorAll("iframe")).toHaveLength(0);
  });
});

describe("подсказки адреса", () => {
  it("503 (геокодер не настроен) → ручной ввод, без ошибки на экране, сохранение идёт", async () => {
    mockedSuggest.mockRejectedValue(new ApiError(503, "suggest_unavailable", "unavailable", { reason: "misconfigured" }));
    mockedCreate.mockResolvedValue(state([place("review_required")]));
    renderScreen();
    await settle();

    fireEvent.click(check("format-private_studio"));
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.next }));
    fireEvent.change(screen.getByLabelText(PLACE_COPY.addressField), { target: { value: "Москва, Тверская, 1" } });
    await settle();

    expect(mockedSuggest).toHaveBeenCalledWith("Москва, Тверская, 1");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.saveAddress }));
    await settle();
    expect(mockedCreate).toHaveBeenCalledTimes(1);
  });

  it("200 → подсказки; выбор подставляет адрес и убирает список", async () => {
    mockedSuggest.mockResolvedValue({
      available: true,
      suggestions: [{ value: "Тверская ул, 1", unrestricted_value: "г Москва, Тверская ул, д 1" }],
    });
    renderScreen();
    await settle();
    fireEvent.click(check("format-salon_or_studio"));
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.next }));
    fireEvent.change(screen.getByLabelText(PLACE_COPY.addressField), { target: { value: "Тверск" } });
    await settle();

    const listbox = screen.getByRole("listbox");
    fireEvent.click(within(listbox).getByRole("button", { name: "Тверская ул, 1" }));
    expect((screen.getByLabelText(PLACE_COPY.addressField) as HTMLInputElement).value).toBe("Тверская ул, 1");
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});

describe("«Как клиенту вас найти?» ≤ 200 — счётчик от длины", () => {
  it("счётчик растёт с вводом; поле не принимает 201-й символ", async () => {
    renderScreen();
    await settle();
    fireEvent.click(check("format-private_studio"));
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.next }));

    const counter = screen.getByTestId("note-counter");
    expect(counter).toHaveTextContent(PLACE_COPY.noteCounter(0));
    fireEvent.change(screen.getByLabelText(PLACE_COPY.noteField), { target: { value: "Вход со двора" } });
    expect(counter).toHaveTextContent(PLACE_COPY.noteCounter("Вход со двора".length));
    expect(screen.getByLabelText(PLACE_COPY.noteField)).toHaveAttribute("maxlength", String(NOTE_MAX));
  });
});

describe("зона выезда", () => {
  it("радио «районы» отсутствует по построению; «Настрою позже» уходит как later", async () => {
    mockedCreate.mockResolvedValue(state([], [area("later")]));
    renderScreen();
    await settle();
    fireEvent.click(check("format-mobile"));
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.next }));

    // ПРИСУТСТВИЕ: два радио есть.
    expect(screen.getAllByRole("radio")).toHaveLength(2);
    // ОТСУТСТВИЕ: третьего значения нет — ни радио, ни текста.
    expect(screen.queryByText(/район/i)).toBeNull();

    fireEvent.click(within(screen.getByTestId("coverage-later")).getByRole("radio"));
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.saveArea }));
    await settle();
    expect(mockedCreate).toHaveBeenCalledWith({ kind: "mobile", coverage: "later" });
    expect(screen.getByTestId("badge-ar-1")).toHaveTextContent(PLACE_COPY.badgeLater);
  });
});

describe("отказы каталога — по имени; изменение — PATCH", () => {
  it("place_already_set показан своим текстом, форма остаётся", async () => {
    mockedCreate.mockRejectedValue(new ApiError(409, "place_already_set", "x"));
    renderScreen();
    await settle();
    fireEvent.click(check("format-private_studio"));
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.next }));
    fireEvent.change(screen.getByLabelText(PLACE_COPY.addressField), { target: { value: "Москва, Тверская, 1" } });
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.saveAddress }));
    await settle();

    expect(screen.getByRole("alert")).toHaveTextContent("Место уже указано — измените его, а не добавляйте второе.");
    expect(screen.getByLabelText(PLACE_COPY.addressField)).toBeInTheDocument();
  });

  it("существующее место правится PATCH по его id, не вторым POST", async () => {
    mockedGet.mockResolvedValue(state([place("review_required")]));
    mockedPatch.mockResolvedValue(state([place("review_required", { note_for_client: "Второй этаж" })]));
    renderScreen();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.changeKind }));
    expect(check("format-private_studio").checked).toBe(true); // предвыбор — из readback
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.next }));
    fireEvent.change(screen.getByLabelText(PLACE_COPY.noteField), { target: { value: "Второй этаж" } });
    fireEvent.click(screen.getByRole("button", { name: PLACE_COPY.saveAddress }));
    await settle();

    expect(mockedCreate).not.toHaveBeenCalled();
    expect(mockedPatch).toHaveBeenCalledWith("pl-1", expect.objectContaining({ note_for_client: "Второй этаж" }));
  });
});

describe("системные состояния через SystemState (М-6b)", () => {
  it("загрузка — скелет без слов", () => {
    mockedGet.mockReturnValue(new Promise(() => {}));
    renderScreen();
    expect(screen.getByRole("status", { busy: true })).toBeInTheDocument();
  });

  it("ошибка — «Не удалось загрузить место работы» + «Попробовать снова», повтор зовёт ручку", async () => {
    mockedGet.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(state());
    renderScreen();
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить место работы");
    expect(screen.queryByRole("button", { name: "Повторить" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
  });
});
