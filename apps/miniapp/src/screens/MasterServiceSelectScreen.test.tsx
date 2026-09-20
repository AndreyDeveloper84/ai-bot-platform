/**
 * Экран 03 — выбор услуг по направлению (DRF-1809, M17).
 *
 * Заперто:
 * D1 — направления ровно из ответа прокси, в его порядке, при любом N (1, 4, 11);
 *      ни числа, ни кодов направлений в экране нет (оговорка #454 / G7);
 * D2 — «Выбрано: N» и счёт в строке направления — из ответа выбора сервера;
 * T1 — шаблоны направления сгруппированы по подкатегории; цен и минут на экране нет;
 * T2 — поиск по имени сужает список; ничего не нашлось — своя строка;
 * T3 — уже выбранные отмечены и заблокированы; «Сохранить выбор» шлёт ТОЛЬКО новые id;
 * T4 — после «Сохранить» 3.4 «Готово с «…»!» и итог — из ответа сервера, не из отметок;
 * T5 — «Следующее направление» открывает следующее из ответа; после последнего — «Перейти к ценам» → /solo/services;
 * E1 — отказы загрузки каждым своим текстом; ошибка шаблонов — не пустое направление;
 * O1 — «+ Добавить свою услугу» открывает форму M18a; выбор похожей обновляет счётчик из ответа.
 *
 * Сторож от зависимости от времени — как в тесте экрана 04: малый asyncUtilTimeout
 * и явный settle(); новая гонка краснеет детерминированно.
 */
import { act, configure, fireEvent, getConfig, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getServiceDirections: vi.fn(),
    getServiceTemplates: vi.fn(),
    getServiceSelection: vi.fn(),
    selectServices: vi.fn(),
    getSimilarCanonTemplates: vi.fn(),
    createCanonGapRequest: vi.fn(),
  };
});

import { ADD_OWN_LABEL, FIELD_DURATION, FIELD_NAME, FIELD_PRICE, PICK_CANON_LABEL, pickedMessage } from "../components/OwnServiceForm";
import { ApiError } from "../lib/api";
import {
  getServiceDirections,
  getServiceSelection,
  getServiceTemplates,
  getSimilarCanonTemplates,
  selectServices,
  type SelectedService,
  type ServiceDirection,
  type ServiceSelectionState,
  type ServiceTemplate,
} from "../lib/master-api";
import { MasterServiceSelectScreen, SELECT_COPY } from "./MasterServiceSelectScreen";

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
const mockedTemplates = vi.mocked(getServiceTemplates);
const mockedSelection = vi.mocked(getServiceSelection);
const mockedSelect = vi.mocked(selectServices);
const mockedSimilar = vi.mocked(getSimilarCanonTemplates);

function directions(n: number): ServiceDirection[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `dir-${i}`,
    name: `Направление ${i}`,
    slug: `dir-${i}`,
    icon: "",
    sort_order: i,
  }));
}

function template(id: string, name: string, category: string): ServiceTemplate {
  return { id, name, name_short: name, is_popular: false, category_id: `cat-${category}`, category_name: category };
}

function selected(templateId: string, directionId: string): SelectedService {
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

function selection(services: SelectedService[], selectedCount = services.length): ServiceSelectionState {
  return { specialist_id: "m1", tenant_id: "tn1", selected: selectedCount, configured: 0, services };
}

const TEMPLATES = [
  template("t-classic", "Классический маникюр", "Маникюр"),
  template("t-hardware", "Аппаратный маникюр", "Маникюр"),
  template("t-gel", "Покрытие гель-лак", "Покрытие"),
];

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

async function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/solo/services/select"]}>
      <Routes>
        <Route path="/solo/services/select" element={<MasterServiceSelectScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
  await settle();
}

const directionsRegion = () => screen.getByRole("region", { name: SELECT_COPY.directionsTitle });

async function openDirection(name: string) {
  fireEvent.click(within(directionsRegion()).getByRole("button", { name: new RegExp(name) }));
  await settle();
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedDirections.mockResolvedValue({ directions: directions(3) });
  mockedTemplates.mockResolvedValue({ direction_id: "dir-0", templates: TEMPLATES });
  mockedSelection.mockResolvedValue(selection([]));
  mockedSelect.mockResolvedValue({ ...selection([selected("t-classic", "dir-0")]), created: 1 });
  mockedSimilar.mockResolvedValue({ similar: [] });
});

describe("MasterServiceSelectScreen — направления", () => {
  it.each([1, 4, 11])("D1: exactly the proxy's directions, in its order (N=%i)", async (n) => {
    const rows = directions(n);
    mockedDirections.mockResolvedValue({ directions: rows });
    await renderScreen();

    const buttons = within(directionsRegion()).getAllByRole("button", { name: /Направление/ });
    expect(buttons.map((b) => b.textContent)).toEqual(rows.map((d) => expect.stringContaining(d.name)));
  });

  it("D2: counters come from the server's selection", async () => {
    mockedSelection.mockResolvedValue(
      selection([selected("a", "dir-1"), selected("b", "dir-1"), selected("c", "dir-2")], 7),
    );
    await renderScreen();

    expect(screen.getByText(SELECT_COPY.selected(7))).toBeInTheDocument();
    const row1 = within(directionsRegion()).getByRole("button", { name: /Направление 1/ });
    expect(row1).toHaveTextContent(SELECT_COPY.inDirection(2));
  });
});

describe("MasterServiceSelectScreen — шаблоны направления", () => {
  it("T1: grouped by subcategory, no prices or minutes on the screen", async () => {
    await renderScreen();
    await openDirection("Направление 0");

    expect(mockedTemplates).toHaveBeenCalledWith("dir-0");
    const region = screen.getByRole("region", { name: "Направление 0" });
    expect(within(region).getByText("Маникюр")).toBeInTheDocument();
    expect(within(region).getByText("Покрытие")).toBeInTheDocument();
    expect(within(region).getByLabelText(/Аппаратный маникюр/)).toBeInTheDocument();
    expect(region.textContent ?? "").not.toMatch(/₽|мин\b/);
  });

  it("T2: search narrows the list; nothing found has its own line", async () => {
    await renderScreen();
    await openDirection("Направление 0");
    const region = screen.getByRole("region", { name: "Направление 0" });

    fireEvent.change(within(region).getByLabelText(SELECT_COPY.search), { target: { value: "аппарат" } });
    expect(within(region).getByLabelText(/Аппаратный маникюр/)).toBeInTheDocument();
    expect(within(region).queryByLabelText(/Классический маникюр/)).not.toBeInTheDocument();

    fireEvent.change(within(region).getByLabelText(SELECT_COPY.search), { target: { value: "педикюр" } });
    expect(within(region).getByText(SELECT_COPY.nothingFound)).toBeInTheDocument();
  });

  it("T3: already selected are checked and locked; save sends only the new ids", async () => {
    mockedSelection.mockResolvedValue(selection([selected("t-gel", "dir-0")]));
    await renderScreen();
    await openDirection("Направление 0");
    const region = screen.getByRole("region", { name: "Направление 0" });

    const gel = within(region).getByLabelText(/Покрытие гель-лак/);
    expect(gel).toBeChecked();
    expect(gel).toBeDisabled();

    fireEvent.click(within(region).getByLabelText(/Классический маникюр/));
    fireEvent.click(within(region).getByRole("button", { name: SELECT_COPY.save }));
    await settle();

    expect(mockedSelect).toHaveBeenCalledTimes(1);
    expect(mockedSelect).toHaveBeenCalledWith(["t-classic"]);
  });

  it("T4: «Готово с «…»!» and its total come from the server's answer", async () => {
    mockedSelect.mockResolvedValue({
      ...selection([selected("t-classic", "dir-0"), selected("x", "dir-0"), selected("y", "dir-2")], 3),
      created: 1,
    });
    await renderScreen();
    await openDirection("Направление 0");
    fireEvent.click(screen.getByLabelText(/Классический маникюр/));
    fireEvent.click(screen.getByRole("button", { name: SELECT_COPY.save }));
    await settle();

    const done = screen.getByRole("region", { name: SELECT_COPY.done("Направление 0") });
    expect(within(done).getByText(SELECT_COPY.doneSummary(2))).toBeInTheDocument();
    expect(screen.getByText(SELECT_COPY.selected(3))).toBeInTheDocument();
  });

  it("T5: next direction, and after the last one — to prices", async () => {
    mockedDirections.mockResolvedValue({ directions: directions(2) });
    await renderScreen();
    await openDirection("Направление 0");
    fireEvent.click(screen.getByRole("button", { name: SELECT_COPY.save }));
    await settle();

    fireEvent.click(screen.getByRole("button", { name: SELECT_COPY.nextDirection }));
    await settle();
    expect(mockedTemplates).toHaveBeenLastCalledWith("dir-1");
    fireEvent.click(screen.getByRole("button", { name: SELECT_COPY.save }));
    await settle();

    expect(screen.queryByRole("button", { name: SELECT_COPY.nextDirection })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: SELECT_COPY.toPrices }));
    await settle();
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/services");
  });
});

describe("MasterServiceSelectScreen — вход с экрана 02 (DRF-1808)", () => {
  it("D1: state.directionIds сужает список и порядок «Следующее направление»; без state — все корни", async () => {
    mockedDirections.mockResolvedValue({ directions: directions(4) });
    // Со state: выбраны 3-е и 1-е (порядок state не важен — идёт порядок каталога).
    render(
      <MemoryRouter initialEntries={[{ pathname: "/solo/services/select", state: { directionIds: ["dir-3", "dir-1"] } }]}>
        <Routes>
          <Route path="/solo/services/select" element={<MasterServiceSelectScreen />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );
    await settle();

    // ПРИСУТСТВИЕ: ровно два выбранных направления.
    const rows = within(directionsRegion()).getAllByRole("button", { name: /Направление \d/ });
    expect(rows.map((b) => b.textContent)).toEqual([
      expect.stringContaining("Направление 1"),
      expect.stringContaining("Направление 3"),
    ]);
    // ОТСУТСТВИЕ: невыбранные корни не показываются.
    expect(within(directionsRegion()).queryByText("Направление 0")).toBeNull();
    expect(within(directionsRegion()).queryByText("Направление 2")).toBeNull();

    // «Следующее направление» после 1-го ведёт на 3-е, минуя 2-е.
    await openDirection("Направление 1");
    fireEvent.click(screen.getByRole("button", { name: SELECT_COPY.save }));
    await settle();
    fireEvent.click(screen.getByRole("button", { name: SELECT_COPY.nextDirection }));
    await settle();
    expect(mockedTemplates).toHaveBeenLastCalledWith("dir-3");
  });

  it("D2: без state — все корни, как до экрана 02", async () => {
    mockedDirections.mockResolvedValue({ directions: directions(4) });
    await renderScreen();
    expect(within(directionsRegion()).getAllByRole("button", { name: /Направление \d/ })).toHaveLength(4);
  });
});

describe("MasterServiceSelectScreen — отказы", () => {
  it.each([
    [new ApiError(409, "salon_catalog_owner_managed", "…", { reason: "salon_catalog_owner_managed" }), SELECT_COPY.salonManaged],
    [new ApiError(403, "not_linked", "…"), SELECT_COPY.notLinked],
    // М-6b: ошибка загрузки — общий SystemState «Не удалось загрузить каталог услуг».
    [new ApiError(503, "catalog_unavailable", "…"), "Не удалось загрузить каталог услуг"],
  ])("E1: %s → its own text, no directions", async (error, text) => {
    mockedSelection.mockRejectedValue(error);
    await renderScreen();

    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: SELECT_COPY.directionsTitle })).not.toBeInTheDocument();
  });

  it("E1: templates did not load → an error with retry, not an empty direction", async () => {
    mockedTemplates.mockRejectedValueOnce(new ApiError(503, "catalog_unavailable", "…"));
    await renderScreen();
    await openDirection("Направление 0");

    // М-6b: «Не удалось загрузить услуги направления» + «Попробовать снова» (ruling §61 е).
    expect(screen.getByText("Не удалось загрузить услуги направления")).toBeInTheDocument();
    expect(screen.queryByText(SELECT_COPY.noTemplates)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    await settle();
    expect(screen.getByLabelText(/Классический маникюр/)).toBeInTheDocument();
  });
});

describe("MasterServiceSelectScreen — своя услуга", () => {
  it("O1: «+ Добавить свою услугу» opens the M18a form; picking the similar one updates the counter from the server", async () => {
    mockedSimilar.mockResolvedValue({ similar: [{ template_id: "t9", name: "Похожая", matched_by: "synonym" }] });
    mockedSelect.mockResolvedValue({ ...selection([selected("t9", "dir-0")], 5), created: 1 });
    await renderScreen();

    fireEvent.click(screen.getByRole("button", { name: SELECT_COPY.addOwn }));
    fireEvent.change(screen.getByLabelText(FIELD_NAME), { target: { value: "Своя" } });
    fireEvent.change(screen.getByLabelText(FIELD_DURATION), { target: { value: "60" } });
    fireEvent.change(screen.getByLabelText(FIELD_PRICE), { target: { value: "1000" } });
    fireEvent.click(screen.getByRole("button", { name: ADD_OWN_LABEL }));
    await settle();

    fireEvent.click(screen.getByRole("button", { name: PICK_CANON_LABEL }));
    await settle();

    expect(mockedSelect).toHaveBeenCalledWith(["t9"]);
    expect(screen.getByText(pickedMessage(5))).toBeInTheDocument();
    expect(screen.getByText(SELECT_COPY.selected(5))).toBeInTheDocument();
  });
});
