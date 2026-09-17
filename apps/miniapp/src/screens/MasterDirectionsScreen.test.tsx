/**
 * Экран 02 «Чем вы занимаетесь?» (DRF-1808, M16) — targeted proof.
 *
 * Что сторожится (столбец «Как проверим», карта разрывов §2.2):
 *   - карточки = ровно ответ `/services/directions` — ни макетного списка,
 *     ни лишних, ни недостающих;
 *   - «Выбрано: N» — от состояния: меняется с кликами и предвыбирается из
 *     уже выбранных услуг (производное), а не константа и не поле ответа;
 *   - «Другое направление» → заявка о разрыве канона с названным
 *     направлением; ввод «татуаж» НЕ создаёт направления: список тот же,
 *     единственный POST — заявка, направления читались ровно один раз;
 *   - «Продолжить» передаёт выбранное экрану 03 навигацией, а не записью.
 */

import { act, configure, fireEvent, getConfig, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getServiceDirections: vi.fn(),
    getServiceSelection: vi.fn(),
    getSimilarCanonTemplates: vi.fn(),
    createCanonGapRequest: vi.fn(),
    selectServices: vi.fn(),
  };
});

import { FIELD_DESCRIPTION, FIELD_DURATION, FIELD_NAME, FIELD_PRICE, SENT_MESSAGE } from "../components/OwnServiceForm";
import {
  createCanonGapRequest,
  getServiceDirections,
  getServiceSelection,
  getSimilarCanonTemplates,
  selectServices,
  type SelectedService,
  type ServiceDirection,
  type ServiceSelectionState,
} from "../lib/master-api";
import { DIRECTIONS_COPY, MasterDirectionsScreen, derivedDirectionIds } from "./MasterDirectionsScreen";

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

const mockedDirections = vi.mocked(getServiceDirections);
const mockedSelection = vi.mocked(getServiceSelection);
const mockedSimilar = vi.mocked(getSimilarCanonTemplates);
const mockedCreate = vi.mocked(createCanonGapRequest);
const mockedSelect = vi.mocked(selectServices);

/** Три корня с НЕмакетными именами: карточки обязаны прийти отсюда, не из макета. */
const ROOTS: ServiceDirection[] = [
  { id: "dir-nails", name: "Маникюр и ногтевой сервис", slug: "nails", icon: "💅", sort_order: 1 },
  { id: "dir-brows", name: "Брови", slug: "brows", icon: "", sort_order: 2 },
  { id: "dir-lashes", name: "Ресницы", slug: "lashes", icon: "", sort_order: 3 },
];

function selected(templateId: string, directionId: string | null): SelectedService {
  return {
    salon_service_id: `ss-${templateId}`,
    template_id: templateId,
    name: templateId,
    category_id: null,
    is_active: true,
    mapping_status: "review_required",
    offer: null,
    configured: false,
    category_name: null,
    direction_id: directionId,
    direction_name: null,
    direction_sort_order: null,
  };
}

function selection(services: SelectedService[]): ServiceSelectionState {
  return { specialist_id: "m1", tenant_id: "tn1", selected: services.length, configured: 0, services };
}

function LocationProbe() {
  const location = useLocation();
  const state = location.state as { directionIds?: string[] } | null;
  return (
    <div data-testid="location">
      {location.pathname}|{(state?.directionIds ?? []).join(",")}
    </div>
  );
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/solo/directions"]}>
      <Routes>
        <Route path="/solo/directions" element={<MasterDirectionsScreen />} />
        <Route path="/solo/services/select" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function checkbox(directionId: string): HTMLInputElement {
  return within(screen.getByTestId(`direction-${directionId}`)).getByRole("checkbox");
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedDirections.mockResolvedValue({ directions: ROOTS });
  mockedSelection.mockResolvedValue(selection([]));
  mockedSimilar.mockResolvedValue({ similar: [] });
});

describe("карточки — корни каталога, не список макета", () => {
  it("ровно то, что вернул сервер: ни лишних, ни недостающих", async () => {
    renderScreen();
    await settle();

    // ПРИСУТСТВИЕ: все три корня — по имени сервера.
    for (const root of ROOTS) {
      expect(screen.getByText(root.name)).toBeInTheDocument();
    }
    // Ни одной карточки сверх ответа: чекбоксов ровно столько, сколько корней.
    expect(screen.getAllByRole("checkbox")).toHaveLength(ROOTS.length);
    // ОТСУТСТВИЕ макетного списка: шесть названий макета 2 сюда не попадают.
    for (const mock of ["Педикюр", "Массаж", "Косметология", "Брови и ресницы", "Парикмахерские услуги"]) {
      expect(screen.queryByText(mock)).toBeNull();
    }
    expect(mockedDirections).toHaveBeenCalledTimes(1);
  });
});

describe("«Выбрано: N» — от состояния", () => {
  it("растёт и падает с кликами, а не стоит константой", async () => {
    renderScreen();
    await settle();
    const counter = screen.getByTestId("directions-counter");
    expect(counter).toHaveTextContent(DIRECTIONS_COPY.selected(0));

    fireEvent.click(checkbox("dir-nails"));
    fireEvent.click(checkbox("dir-brows"));
    expect(counter).toHaveTextContent(DIRECTIONS_COPY.selected(2));

    fireEvent.click(checkbox("dir-brows"));
    expect(counter).toHaveTextContent(DIRECTIONS_COPY.selected(1));
    expect(checkbox("dir-nails").checked).toBe(true);
    expect(checkbox("dir-brows").checked).toBe(false);
  });

  it("предвыбор — производное от уже выбранных услуг, без клика", async () => {
    // Услуга направления «Брови» уже есть; ещё одна — без направления
    // (`direction_id: null`) и не должна дать ни предвыбора, ни счёта.
    mockedSelection.mockResolvedValue(selection([selected("t-brow", "dir-brows"), selected("t-orphan", null)]));
    renderScreen();
    await settle();

    expect(checkbox("dir-brows").checked).toBe(true);
    expect(checkbox("dir-nails").checked).toBe(false);
    expect(screen.getByTestId("directions-counter")).toHaveTextContent(DIRECTIONS_COPY.selected(1));
    // Подпись «Услуг выбрано: 1» — у направления с услугой, и только у него.
    expect(within(screen.getByTestId("direction-dir-brows")).getByText(DIRECTIONS_COPY.hasServices(1))).toBeInTheDocument();
    expect(within(screen.getByTestId("direction-dir-nails")).queryByText(/Услуг выбрано/)).toBeNull();
    expect(derivedDirectionIds(selection([selected("t-brow", "dir-brows"), selected("t-orphan", null)]))).toEqual(
      new Set(["dir-brows"]),
    );
  });
});

describe("«Другое направление» — заявка о разрыве канона, не новая категория", () => {
  it("ввод «татуаж» не создаёт направления: список тот же, единственный POST — заявка", async () => {
    mockedCreate.mockResolvedValue({
      request: {
        id: "cgr-1",
        name: "Перманентный макияж бровей",
        description: "Направление: татуаж",
        duration_minutes: 90,
        price: "5000",
        status: "pending",
        decision_note: null,
        decided_at: null,
        created_at: "2026-09-17T16:00:00Z",
      } as never,
      similar: [],
    });
    renderScreen();
    await settle();

    fireEvent.click(screen.getByRole("button", { name: `+ ${DIRECTIONS_COPY.other}` }));
    fireEvent.change(screen.getByLabelText(DIRECTIONS_COPY.otherField), { target: { value: "татуаж" } });
    fireEvent.click(screen.getByRole("button", { name: DIRECTIONS_COPY.otherContinue }));

    // Форма своей услуги открылась с направлением в описании — редактируемым.
    const description = screen.getByLabelText(FIELD_DESCRIPTION) as HTMLTextAreaElement | HTMLInputElement;
    expect(description.value).toBe(`${DIRECTIONS_COPY.directionPrefix}татуаж`);

    fireEvent.change(screen.getByLabelText(FIELD_NAME), { target: { value: "Перманентный макияж бровей" } });
    fireEvent.change(screen.getByLabelText(FIELD_DURATION), { target: { value: "90" } });
    fireEvent.change(screen.getByLabelText(FIELD_PRICE), { target: { value: "5000" } });
    fireEvent.submit(screen.getByLabelText(FIELD_NAME).closest("form") as HTMLFormElement);
    await settle();

    // ПРИСУТСТВИЕ: заявка ушла, и в ней названо направление.
    expect(mockedCreate).toHaveBeenCalledTimes(1);
    expect(mockedCreate.mock.calls[0]?.[0]).toMatchObject({
      name: "Перманентный макияж бровей",
      description: "Направление: татуаж",
      duration_minutes: 90,
    });
    expect(screen.getByText(SENT_MESSAGE)).toBeInTheDocument();

    // ОТСУТСТВИЕ на тех же данных: направления не появилось — карточек ровно
    // столько же, «татуаж» среди них нет, направления не перечитывались и
    // никакой другой записи (выбор услуг) не было.
    expect(screen.getAllByRole("checkbox")).toHaveLength(ROOTS.length);
    expect(screen.queryByText(/татуаж/i)).toBeNull();
    expect(mockedDirections).toHaveBeenCalledTimes(1);
    expect(mockedSelect).not.toHaveBeenCalled();
  });
});

describe("«Продолжить» — навигация, не запись", () => {
  it("передаёт выбранные направления экрану 03 в порядке каталога", async () => {
    renderScreen();
    await settle();
    const next = screen.getByRole("button", { name: DIRECTIONS_COPY.next });
    expect(next).toBeDisabled(); // ноль выбранных — идти некуда

    fireEvent.click(checkbox("dir-lashes"));
    fireEvent.click(checkbox("dir-nails")); // кликнули позже — в state раньше: порядок каталога
    fireEvent.click(next);
    await settle();

    expect(screen.getByTestId("location")).toHaveTextContent("/solo/services/select|dir-nails,dir-lashes");
    // Ничего не записано: ни выбора услуг, ни заявки.
    expect(mockedSelect).not.toHaveBeenCalled();
    expect(mockedCreate).not.toHaveBeenCalled();
  });
});
