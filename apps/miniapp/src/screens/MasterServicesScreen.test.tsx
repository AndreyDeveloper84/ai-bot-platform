/**
 * «Услуги и цены» мастера-соло — экран 04 (DRF-1810, M18) и «Свои услуги» (DRF-1896).
 *
 * Экран 04 (на выборе каталога M8a/M8b, группы — направление DRF-1912):
 * S1 — аккордеоны по направлениям в порядке direction_sort_order;
 * S2 — прогресс «Настроено N из M · Осталось K» — числа сервера (selected/configured),
 *      даже если они не совпадают со строками: экран не считает сам;
 * S3 — «Не настроено» у строки без предложения; настроенная — цена · мин · ✓;
 * S4 — «Продолжить» неактивна при 3/6, активна при 6/6 и при 6/6 + 1 pending-заявка
 *      (заявки не в знаменателе);
 * S5 — «Продолжить» ведёт на deep_link первого missing-пункта готовности (M2);
 *      нет missing / готовность не загрузилась → /solo/setup;
 * S6 — «Сохранить и продолжить позже» → /solo/setup всегда;
 * S7 — шторка: ровно два поля (цена и длительность 15/30/45/60/75/90/120 или
 *      «Другое время» 5..480) + «Убрать из моих услуг»; сохранение — PUT и состояние
 *      из ответа;
 * S8 — неверная цена / длительность → запроса нет;
 * S9 — «Убрать»: состояние из ответа; 409 has_future_appointments → причина с числом;
 * S10 — отказы загрузки выбора, каждый своим текстом, ни одного молчащего;
 * S11 — зеркало каталога бота больше не источник: getMasterCatalog не зовётся,
 *       подписи «редактирование позже» нет.
 *
 * Плавающее «A+B» (падало под параллельной нагрузкой, DRF-1810): тест ждал
 * цепочку загрузок (каталог → секция → заявки) в окне findBy 1000 мс. Теперь каждая загрузка досчитывается явным
 * `settle()` (act), и файл идёт со СТОРОЖЕМ — малым asyncUtilTimeout только
 * здесь: новая зависимость от времени краснеет детерминированно, а не раз в тысячу.
 */
import { act, configure, fireEvent, getConfig, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getMasterCatalog: vi.fn(),
    listCanonGapRequests: vi.fn(),
    createCanonGapRequest: vi.fn(),
    getSimilarCanonTemplates: vi.fn(),
    getServiceSelection: vi.fn(),
    selectServices: vi.fn(),
    putServiceOffer: vi.fn(),
    removeService: vi.fn(),
    getOnboardingReadiness: vi.fn(),
  };
});

import { ApiError } from "../lib/api";
import { SUPPORT_DEEPLINK } from "../lib/customer-profile";
import {
  createCanonGapRequest,
  getMasterCatalog,
  getOnboardingReadiness,
  getServiceSelection,
  getSimilarCanonTemplates,
  listCanonGapRequests,
  putServiceOffer,
  removeService,
  selectServices,
  type CanonGapRequest,
  type OnboardingReadiness,
  type SelectedService,
  type ServiceSelectionState,
} from "../lib/master-api";
import {
  ADD_ANYWAY_LABEL,
  ADD_OWN_LABEL,
  ERR_DURATION,
  ERR_NAME,
  ERR_PRICE,
  FIELD_DURATION,
  FIELD_NAME,
  FIELD_PRICE,
  MasterServicesScreen,
  NOT_LINKED_MESSAGE,
  OWN_TITLE,
  PICK_CANON_LABEL,
  PICK_CANON_UNAVAILABLE,
  SALON_MANAGED_MESSAGE,
  SENT_MESSAGE,
  pickedMessage,
  validateOwnService,
} from "./MasterServicesScreen";

/**
 * Сторож от зависимости от времени (DRF-1810): только в этом файле.
 * Значение подобрано прогоном файла под нагрузкой — см. тело PR.
 */
const GUARD_ASYNC_TIMEOUT_MS = 20;
let previousAsyncUtilTimeout = 1000;

beforeAll(() => {
  previousAsyncUtilTimeout = getConfig().asyncUtilTimeout;
  configure({ asyncUtilTimeout: GUARD_ASYNC_TIMEOUT_MS });
});

afterAll(() => {
  configure({ asyncUtilTimeout: previousAsyncUtilTimeout });
});

/** Досчитать все уже разрешённые промисы и эффекты — без ожидания по времени. */
const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

const mockedCatalog = vi.mocked(getMasterCatalog);
const mockedList = vi.mocked(listCanonGapRequests);
const mockedCreate = vi.mocked(createCanonGapRequest);
const mockedSimilar = vi.mocked(getSimilarCanonTemplates);
const mockedSelection = vi.mocked(getServiceSelection);
const mockedSelect = vi.mocked(selectServices);
const mockedPut = vi.mocked(putServiceOffer);
const mockedRemove = vi.mocked(removeService);
const mockedReadiness = vi.mocked(getOnboardingReadiness);

function row(id: string, name: string, extra: Partial<SelectedService> = {}): SelectedService {
  return {
    salon_service_id: id,
    template_id: `tpl-${id}`,
    name,
    category_id: "cat",
    is_active: true,
    mapping_status: "review_required",
    offer: null,
    configured: false,
    direction_id: "dir-brows",
    direction_name: "Брови",
    direction_sort_order: 2,
    category_name: "Коррекция",
    ...extra,
  };
}

function configured(id: string, name: string, price: string, minutes: number, extra: Partial<SelectedService> = {}) {
  return row(id, name, {
    offer: { id: `offer-${id}`, price, duration_minutes: minutes, is_active: true },
    configured: true,
    ...extra,
  });
}

function state(services: SelectedService[], selected: number, configuredCount: number): ServiceSelectionState {
  return { specialist_id: "m1", tenant_id: "tn1", selected, configured: configuredCount, services };
}

const EMPTY_SELECTION = state([], 0, 0);
const SIMILAR = { template_id: "t1", name: "Перманентный макияж бровей", matched_by: "synonym" };

function readiness(items: Array<{ key: string; state: string; deep_link: string }>): OnboardingReadiness {
  return {
    ready: false,
    blocking: [],
    items: items.map((i) => ({ ...i, detail: {}, reason: null })),
    identity: { state: "linked", link_status: null },
    setup_state: "SETUP_PENDING",
    sale_block: null,
  };
}

function req(id: string, name: string, extra: Partial<CanonGapRequest> = {}): CanonGapRequest {
  return {
    id,
    specialist_id: "m1",
    name,
    description: "",
    duration_minutes: 120,
    price: "4500.00",
    status: "pending",
    status_label: "На проверке",
    resolved_template_id: null,
    clarification_question: null,
    rejection_reason: null,
    decided_at: null,
    created_at: "2026-09-15T10:00:00+00:00",
    ...extra,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

async function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/solo/services"]}>
      <Routes>
        <Route path="/solo/services" element={<MasterServicesScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
  await settle();
}

const ownSection = () => screen.getByRole("region", { name: OWN_TITLE });
const prices = () => screen.getByRole("region", { name: "Цены и длительность" });

function fill(label: string, value: string) {
  fireEvent.change(within(ownSection()).getByLabelText(label), { target: { value } });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedCatalog.mockResolvedValue([]);
  mockedList.mockResolvedValue({ requests: [req("r1", "Татуаж бровей пудровый")] });
  mockedSimilar.mockResolvedValue({ similar: [] });
  mockedCreate.mockResolvedValue({ request: req("r2", "Ламинирование"), similar: [] });
  mockedSelection.mockResolvedValue(EMPTY_SELECTION);
  mockedSelect.mockResolvedValue({ ...EMPTY_SELECTION, selected: 3, created: 1 });
  mockedReadiness.mockResolvedValue(readiness([]));
});

async function openSimilarHint() {
  fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL }));
  fill(FIELD_NAME, "Татуаж бровей");
  fill(FIELD_DURATION, "90");
  fill(FIELD_PRICE, "3000");
  fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL }));
  await settle();
}

// ---------------------------------------------------------------------------
// Экран 04
// ---------------------------------------------------------------------------

describe("MasterServicesScreen — экран 04 «Цены и длительность» (DRF-1810)", () => {
  it("S1: accordions by direction follow direction_sort_order", async () => {
    mockedSelection.mockResolvedValue(
      state(
        [
          row("a", "Маникюр классический", { direction_id: "dir-nails", direction_name: "Ногти", direction_sort_order: 5 }),
          row("b", "Коррекция бровей", { direction_id: "dir-brows", direction_name: "Брови", direction_sort_order: 2 }),
        ],
        2,
        0,
      ),
    );
    await renderScreen();

    const groups = within(prices()).getAllByRole("region").map((g) => g.getAttribute("aria-label"));
    expect(groups).toEqual(["Брови", "Ногти"]);
    expect(within(within(prices()).getByRole("region", { name: "Ногти" })).getByText("Маникюр классический")).toBeInTheDocument();
  });

  it("S2: progress numbers are the server's, not a count of the rows", async () => {
    // Строк две, а сервер говорит 6 выбрано / 3 настроено — экран показывает сервер.
    mockedSelection.mockResolvedValue(state([row("a", "А"), configured("b", "Б", "1500.00", 60)], 6, 3));
    await renderScreen();

    expect(within(prices()).getByText("Настроено 3 из 6")).toBeInTheDocument();
    expect(within(prices()).getByText("Осталось 3")).toBeInTheDocument();
  });

  it("S3: an unconfigured row says «Не настроено»; a configured one shows price · minutes · ✓", async () => {
    mockedSelection.mockResolvedValue(state([row("a", "Коррекция бровей"), configured("b", "Окрашивание бровей", "1500.00", 45)], 2, 1));
    await renderScreen();

    const pending = within(prices()).getByRole("button", { name: /Коррекция бровей/ });
    expect(within(pending).getByText("Не настроено")).toBeInTheDocument();
    const done = within(prices()).getByRole("button", { name: /Окрашивание бровей/ });
    expect(within(done).getByText("1500 ₽ · 45 мин ✓")).toBeInTheDocument();
  });

  it("S4: «Продолжить» is off at 3/6, on at 6/6 and at 6/6 with a pending request", async () => {
    mockedSelection.mockResolvedValue(state([row("a", "А")], 6, 3));
    await renderScreen();
    expect(screen.getByRole("button", { name: "Продолжить" })).toBeDisabled();
  });

  it("S4: nothing selected → «Продолжить» is off with a hint; «Позже» stays on", async () => {
    mockedSelection.mockResolvedValue(EMPTY_SELECTION);
    await renderScreen();

    expect(screen.getByRole("button", { name: "Продолжить" })).toBeDisabled();
    expect(screen.getByText("Выбери хотя бы одну услугу")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить и продолжить позже" })).toBeEnabled();
  });

  it("S4: one selected and not configured → «Продолжить» is off, no hint", async () => {
    mockedSelection.mockResolvedValue(state([row("a", "Коррекция бровей")], 1, 0));
    await renderScreen();

    expect(screen.getByRole("button", { name: "Продолжить" })).toBeDisabled();
    expect(screen.queryByText("Выбери хотя бы одну услугу")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить и продолжить позже" })).toBeEnabled();
  });

  it("S4: 6/6 enables «Продолжить»", async () => {
    mockedSelection.mockResolvedValue(state([configured("a", "А", "1000.00", 60)], 6, 6));
    await renderScreen();
    expect(screen.getByRole("button", { name: "Продолжить" })).toBeEnabled();
  });

  it("S4: a pending own request does not enter the denominator", async () => {
    mockedSelection.mockResolvedValue(state([configured("a", "А", "1000.00", 60)], 6, 6));
    mockedList.mockResolvedValue({ requests: [req("r9", "Своя на проверке")] });
    await renderScreen();

    expect(within(prices()).getByText("Настроено 6 из 6")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Продолжить" })).toBeEnabled();
    expect(screen.getByRole("heading", { name: `${OWN_TITLE} · 1` })).toBeInTheDocument();
  });

  it("S5: «Продолжить» goes to the first missing readiness item", async () => {
    mockedSelection.mockResolvedValue(state([configured("a", "А", "1000.00", 60)], 1, 1));
    mockedReadiness.mockResolvedValue(
      readiness([
        { key: "services", state: "done", deep_link: "/solo/services" },
        { key: "location", state: "unknown", deep_link: "/solo/settings" },
        { key: "hours", state: "missing", deep_link: "/solo/working-hours" },
        { key: "profile", state: "missing", deep_link: "/solo/profile" },
      ]),
    );
    await renderScreen();

    fireEvent.click(screen.getByRole("button", { name: "Продолжить" }));
    await settle();
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/working-hours");
  });

  it("S5: a stale «services: missing» is this very screen and is skipped", async () => {
    mockedSelection.mockResolvedValue(state([configured("a", "А", "1000.00", 60)], 1, 1));
    mockedReadiness.mockResolvedValue(
      readiness([
        { key: "services", state: "missing", deep_link: "/solo/services" },
        { key: "profile", state: "missing", deep_link: "/solo/profile" },
      ]),
    );
    await renderScreen();

    fireEvent.click(screen.getByRole("button", { name: "Продолжить" }));
    await settle();
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/profile");
  });

  it("S5: nothing missing → /solo/setup", async () => {
    mockedSelection.mockResolvedValue(state([configured("a", "А", "1000.00", 60)], 1, 1));
    mockedReadiness.mockResolvedValue(readiness([{ key: "services", state: "done", deep_link: "/solo/services" }]));
    await renderScreen();

    fireEvent.click(screen.getByRole("button", { name: "Продолжить" }));
    await settle();
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/setup");
  });

  it("S5: readiness did not load → /solo/setup", async () => {
    mockedSelection.mockResolvedValue(state([configured("a", "А", "1000.00", 60)], 1, 1));
    mockedReadiness.mockRejectedValue(new ApiError(503, "catalog_unavailable", "…"));
    await renderScreen();

    fireEvent.click(screen.getByRole("button", { name: "Продолжить" }));
    await settle();
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/setup");
  });

  it("S6: «Сохранить и продолжить позже» always goes to /solo/setup", async () => {
    mockedSelection.mockResolvedValue(state([row("a", "А")], 6, 3));
    await renderScreen();

    fireEvent.click(screen.getByRole("button", { name: "Сохранить и продолжить позже" }));
    await settle();
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/setup");
  });

  it("M17: nothing selected → «Выбрать из каталога» leads to screen 03", async () => {
    mockedSelection.mockResolvedValue(EMPTY_SELECTION);
    await renderScreen();

    expect(screen.getByText("Выбери хотя бы одну услугу")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Выбрать из каталога" }));
    await settle();
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/services/select");
  });

  it("M17: something selected → no «Выбрать из каталога» next to the hint", async () => {
    mockedSelection.mockResolvedValue(state([row("a", "Коррекция бровей")], 1, 0));
    await renderScreen();

    expect(screen.getByRole("button", { name: "Продолжить" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Выбрать из каталога" })).not.toBeInTheDocument();
  });

  it.each([
    [
      "salon_managed",
      new ApiError(409, "salon_catalog_owner_managed", "…", { reason: "salon_catalog_owner_managed" }),
      "Услуги салона ведёт владелец салона.",
    ],
    ["not_linked", new ApiError(403, "not_linked", "…"), "Доступ не настроен"],
  ])("M17: %s → no way into the selection", async (_name, error, witness) => {
    mockedSelection.mockRejectedValue(error);
    await renderScreen();

    expect(screen.getByText(witness)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Выбрать из каталога" })).not.toBeInTheDocument();
  });

  it("M17: readiness «services» pointing at screen 03 is still this step and is skipped — no loop", async () => {
    mockedSelection.mockResolvedValue(state([configured("a", "А", "1000.00", 60)], 1, 1));
    mockedReadiness.mockResolvedValue(
      readiness([
        { key: "services", state: "missing", deep_link: "/solo/services/select" },
        { key: "profile", state: "missing", deep_link: "/solo/profile" },
      ]),
    );
    await renderScreen();

    fireEvent.click(screen.getByRole("button", { name: "Продолжить" }));
    await settle();
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/profile");
  });

  it("S7: the sheet has exactly two fields and saves through PUT, state from the response", async () => {
    mockedSelection.mockResolvedValue(state([row("svc-1", "Коррекция бровей")], 1, 0));
    mockedPut.mockResolvedValue({
      ...state([configured("svc-1", "Коррекция бровей", "1800.00", 45)], 1, 1),
      offer_id: "offer-svc-1",
    });
    await renderScreen();

    fireEvent.click(within(prices()).getByRole("button", { name: /Коррекция бровей/ }));
    await settle();
    const sheet = screen.getByRole("dialog", { name: "Коррекция бровей" });
    // Ровно два поля: цена и длительность. Ни названия, ни описания.
    expect(within(sheet).getByLabelText("Цена, ₽")).toBeInTheDocument();
    expect(within(sheet).getByRole("radiogroup", { name: "Длительность" })).toBeInTheDocument();
    expect(within(sheet).getAllByRole("textbox")).toHaveLength(1);
    expect(within(sheet).queryByLabelText(FIELD_NAME)).not.toBeInTheDocument();
    for (const minutes of [15, 30, 45, 60, 75, 90, 120]) {
      expect(within(sheet).getByRole("radio", { name: `${minutes} мин` })).toBeInTheDocument();
    }
    expect(within(sheet).getByRole("radio", { name: "Другое время" })).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Убрать из моих услуг" })).toBeInTheDocument();

    fireEvent.change(within(sheet).getByLabelText("Цена, ₽"), { target: { value: "1800" } });
    fireEvent.click(within(sheet).getByRole("radio", { name: "45 мин" }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Сохранить" }));
    await settle();

    expect(mockedPut).toHaveBeenCalledWith("svc-1", { price: "1800", duration_minutes: 45 });
    expect(within(prices()).getByText("Настроено 1 из 1")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("S7: «Другое время» takes 5..480 minutes", async () => {
    mockedSelection.mockResolvedValue(state([row("svc-1", "Коррекция бровей")], 1, 0));
    mockedPut.mockResolvedValue({ ...state([configured("svc-1", "Коррекция бровей", "900.00", 200)], 1, 1), offer_id: "o" });
    await renderScreen();

    fireEvent.click(within(prices()).getByRole("button", { name: /Коррекция бровей/ }));
    await settle();
    const sheet = screen.getByRole("dialog", { name: "Коррекция бровей" });
    fireEvent.change(within(sheet).getByLabelText("Цена, ₽"), { target: { value: "900" } });
    fireEvent.click(within(sheet).getByRole("radio", { name: "Другое время" }));
    fireEvent.change(within(sheet).getByLabelText("Минут"), { target: { value: "200" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Сохранить" }));
    await settle();

    expect(mockedPut).toHaveBeenCalledWith("svc-1", { price: "900", duration_minutes: 200 });
  });

  it.each([
    ["0.99", "60", "Цена — от 1 ₽, не больше двух знаков после запятой."],
    ["1500.555", "60", "Цена — от 1 ₽, не больше двух знаков после запятой."],
    ["1500", "4", "Длительность — от 5 до 480 минут."],
    ["1500", "481", "Длительность — от 5 до 480 минут."],
  ])("S8: price %s / minutes %s is refused before any request", async (price, minutes, message) => {
    mockedSelection.mockResolvedValue(state([row("svc-1", "Коррекция бровей")], 1, 0));
    await renderScreen();

    fireEvent.click(within(prices()).getByRole("button", { name: /Коррекция бровей/ }));
    await settle();
    const sheet = screen.getByRole("dialog", { name: "Коррекция бровей" });
    fireEvent.change(within(sheet).getByLabelText("Цена, ₽"), { target: { value: price } });
    fireEvent.click(within(sheet).getByRole("radio", { name: "Другое время" }));
    fireEvent.change(within(sheet).getByLabelText("Минут"), { target: { value: minutes } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Сохранить" }));
    await settle();

    expect(within(sheet).getByText(message)).toBeInTheDocument();
    expect(mockedPut).not.toHaveBeenCalled();
  });

  it("S9: «Убрать из моих услуг» removes and takes the state from the response", async () => {
    mockedSelection.mockResolvedValue(state([row("svc-1", "Коррекция бровей"), row("svc-2", "Окрашивание")], 2, 0));
    mockedRemove.mockResolvedValue({ ...state([row("svc-2", "Окрашивание")], 1, 0), removal: "deleted" });
    await renderScreen();

    fireEvent.click(within(prices()).getByRole("button", { name: /Коррекция бровей/ }));
    await settle();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Убрать из моих услуг" }));
    await settle();

    expect(mockedRemove).toHaveBeenCalledWith("svc-1");
    expect(within(prices()).queryByRole("button", { name: /Коррекция бровей/ })).not.toBeInTheDocument();
    expect(within(prices()).getByText("Настроено 0 из 1")).toBeInTheDocument();
  });

  it("S9: future appointments block removal and the reason carries the count", async () => {
    mockedSelection.mockResolvedValue(state([configured("svc-1", "Коррекция бровей", "1500.00", 60)], 1, 1));
    mockedRemove.mockRejectedValue(
      new ApiError(409, "has_future_appointments", "…", { reason: "has_future_appointments", count: 2 }),
    );
    await renderScreen();

    fireEvent.click(within(prices()).getByRole("button", { name: /Коррекция бровей/ }));
    await settle();
    const sheet = screen.getByRole("dialog");
    fireEvent.click(within(sheet).getByRole("button", { name: "Убрать из моих услуг" }));
    await settle();

    expect(within(sheet).getByText("Нельзя убрать: есть будущие записи (2).")).toBeInTheDocument();
    expect(within(prices()).getByRole("button", { name: /Коррекция бровей/ })).toBeInTheDocument();
  });

  it.each([
    ["service_removed", "Услуга убрана — выберите её в каталоге снова."],
    ["service_not_selected", "Эта услуга больше не выбрана."],
  ])("S9: PUT refusal %s is named, not silent", async (slug, message) => {
    mockedSelection.mockResolvedValue(state([row("svc-1", "Коррекция бровей")], 1, 0));
    mockedPut.mockRejectedValue(new ApiError(slug === "service_removed" ? 409 : 404, slug, "…", { reason: slug }));
    await renderScreen();

    fireEvent.click(within(prices()).getByRole("button", { name: /Коррекция бровей/ }));
    await settle();
    const sheet = screen.getByRole("dialog");
    fireEvent.change(within(sheet).getByLabelText("Цена, ₽"), { target: { value: "1500" } });
    fireEvent.click(within(sheet).getByRole("radio", { name: "60 мин" }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Сохранить" }));
    await settle();

    expect(within(sheet).getByText(message)).toBeInTheDocument();
  });

  it("S10: salon catalog is owner-managed → explanation, no editing", async () => {
    mockedSelection.mockRejectedValue(
      new ApiError(409, "salon_catalog_owner_managed", "…", { reason: "salon_catalog_owner_managed" }),
    );
    await renderScreen();

    expect(screen.getByText("Услуги салона ведёт владелец салона.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Цены и длительность" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Продолжить" })).not.toBeInTheDocument();
  });

  it("S10: not linked → «Доступ не настроен», operator links, support contact, no editing", async () => {
    mockedSelection.mockRejectedValue(new ApiError(403, "not_linked", "…"));
    await renderScreen();

    expect(screen.getByRole("heading", { name: "Доступ не настроен" })).toBeInTheDocument();
    expect(screen.getByText("Профиль ещё не привязан — привязку выполнит оператор.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Написать в поддержку" })).toHaveAttribute("href", SUPPORT_DEEPLINK);
    expect(screen.queryByRole("region", { name: "Цены и длительность" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Продолжить" })).not.toBeInTheDocument();
  });

  it.each([
    ["no_workspace_tenant", 409],
    ["catalog_unavailable", 503],
  ])("S10: %s → «Не удалось загрузить услуги» and «Повторить» reloads", async (slug, status) => {
    mockedSelection.mockRejectedValueOnce(new ApiError(status, slug, "…", { reason: slug }));
    mockedSelection.mockResolvedValueOnce(state([row("a", "Коррекция бровей")], 1, 0));
    await renderScreen();

    expect(screen.getByText("Не удалось загрузить услуги.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await settle();

    expect(mockedSelection).toHaveBeenCalledTimes(2);
    expect(within(prices()).getByRole("button", { name: /Коррекция бровей/ })).toBeInTheDocument();
  });

  it("S11: the bot's catalog mirror is no longer read, and «editing later» is gone", async () => {
    mockedSelection.mockResolvedValue(state([row("a", "Коррекция бровей")], 1, 0));
    await renderScreen();

    expect(mockedCatalog).not.toHaveBeenCalled();
    expect(screen.queryByText(/Редактирование услуг — в следующих обновлениях/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// «Свои услуги» (DRF-1896) и «Выбрать эту услугу» (DRF-1895) — на явном settle
// ---------------------------------------------------------------------------

describe("MasterServicesScreen — «Свои услуги»", () => {
  it("A+B: counter and status come from the server; requests stay out of the prices progress", async () => {
    mockedSelection.mockResolvedValue(state([configured("a", "Коррекция бровей", "1500.00", 60)], 1, 1));
    mockedList.mockResolvedValue({
      requests: [
        req("r1", "Татуаж бровей пудровый"),
        req("r3", "Микроблейдинг", {
          status: "needs_clarification",
          status_label: "Нужно уточнение",
          clarification_question: "Это брови или губы?",
        }),
      ],
    });
    await renderScreen();

    expect(screen.getByRole("heading", { name: `${OWN_TITLE} · 2` })).toBeInTheDocument();
    const own = ownSection();
    expect(within(own).getAllByTestId("own-service")).toHaveLength(2);
    expect(within(own).getByText("На проверке")).toBeInTheDocument();
    expect(within(own).getByText("Нужно уточнение")).toBeInTheDocument();
    expect(within(own).getByText("Это брови или губы?")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Цены и длительность" })).queryByText("Татуаж бровей пудровый")).not.toBeInTheDocument();
  });

  it("C: no own requests still shows the section and the add button", async () => {
    mockedList.mockResolvedValue({ requests: [] });
    await renderScreen();
    expect(screen.getByRole("heading", { name: `${OWN_TITLE} · 0` })).toBeInTheDocument();
    expect(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL })).toBeInTheDocument();
  });

  it("D: invalid form sends nothing and names each field", async () => {
    await renderScreen();
    fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL }));
    fill(FIELD_DURATION, "0");
    fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL }));
    await settle();

    expect(within(ownSection()).getByText(ERR_NAME)).toBeInTheDocument();
    expect(within(ownSection()).getByText(ERR_DURATION)).toBeInTheDocument();
    expect(within(ownSection()).getByText(ERR_PRICE)).toBeInTheDocument();
    expect(mockedSimilar).not.toHaveBeenCalled();
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("E: a similar service is a hint — nothing sent until «Всё равно добавить мою»", async () => {
    mockedSimilar.mockResolvedValue({ similar: [SIMILAR] });
    await renderScreen();
    await openSimilarHint();

    expect(within(ownSection()).getByText("Перманентный макияж бровей")).toBeInTheDocument();
    expect(mockedSimilar).toHaveBeenCalledWith("Татуаж бровей");
    expect(mockedCreate).not.toHaveBeenCalled();
    expect(within(ownSection()).getByRole("button", { name: PICK_CANON_LABEL })).toBeEnabled();
    expect(mockedSelect).not.toHaveBeenCalled();

    mockedList.mockResolvedValue({ requests: [req("r1", "Татуаж бровей пудровый"), req("r2", "Татуаж бровей")] });
    fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_ANYWAY_LABEL }));
    await settle();

    expect(mockedCreate).toHaveBeenCalledTimes(1);
    expect(mockedCreate).toHaveBeenCalledWith({ name: "Татуаж бровей", description: "", duration_minutes: 90, price: "3000" });
    expect(within(ownSection()).getByText(SENT_MESSAGE)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: `${OWN_TITLE} · 2` })).toBeInTheDocument();
    expect(mockedList).toHaveBeenCalledTimes(2);
  });

  it("F: no similar service → the request goes straight away", async () => {
    await renderScreen();
    fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL }));
    fill(FIELD_NAME, "Ламинирование");
    fill(FIELD_DURATION, "60");
    fill(FIELD_PRICE, "2000,50");
    fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL }));
    await settle();

    expect(mockedCreate).toHaveBeenCalledTimes(1);
    expect(mockedCreate).toHaveBeenCalledWith(expect.objectContaining({ name: "Ламинирование", duration_minutes: 60, price: "2000.50" }));
  });

  it("G: own requests not linked → explanation, no form", async () => {
    mockedList.mockRejectedValue(new ApiError(403, "not_linked", "…"));
    await renderScreen();
    expect(within(ownSection()).getByText(NOT_LINKED_MESSAGE)).toBeInTheDocument();
    expect(within(ownSection()).queryByRole("button", { name: ADD_OWN_LABEL })).not.toBeInTheDocument();
  });
});

describe("MasterServicesScreen — «Выбрать эту услугу» (DRF-1895)", () => {
  it("H: salon catalog is owner-managed → the button stays disabled and says why", async () => {
    mockedSelection.mockRejectedValue(
      new ApiError(409, "salon_catalog_owner_managed", "…", { reason: "salon_catalog_owner_managed" }),
    );
    mockedSimilar.mockResolvedValue({ similar: [SIMILAR] });
    await renderScreen();
    await openSimilarHint();

    expect(within(ownSection()).getByRole("button", { name: PICK_CANON_LABEL })).toBeDisabled();
    expect(within(ownSection()).getByText(SALON_MANAGED_MESSAGE)).toBeInTheDocument();
    expect(mockedSelect).not.toHaveBeenCalled();
  });

  it("I: picking the similar canon service selects it and shows the server's counter", async () => {
    mockedSimilar.mockResolvedValue({ similar: [SIMILAR] });
    await renderScreen();
    await openSimilarHint();

    fireEvent.click(within(ownSection()).getByRole("button", { name: PICK_CANON_LABEL }));
    await settle();

    expect(mockedSelect).toHaveBeenCalledWith(["t1"]);
    expect(within(ownSection()).getByText(pickedMessage(3))).toBeInTheDocument();
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("J: selection state unavailable → the button is not offered (fail-closed)", async () => {
    mockedSelection.mockRejectedValue(new ApiError(503, "catalog_unavailable", "…"));
    mockedSimilar.mockResolvedValue({ similar: [SIMILAR] });
    await renderScreen();
    await openSimilarHint();

    const button = within(ownSection()).getByRole("button", { name: PICK_CANON_LABEL });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", PICK_CANON_UNAVAILABLE);
    expect(mockedSelect).not.toHaveBeenCalled();
  });
});

describe("validateOwnService", () => {
  it("accepts a complete draft and refuses each missing field", () => {
    expect(validateOwnService({ name: "X", description: "", duration: "60", price: "0" })).toEqual({});
    expect(validateOwnService({ name: " ", description: "", duration: "1.5", price: "-1" })).toEqual({
      name: ERR_NAME,
      duration: ERR_DURATION,
      price: ERR_PRICE,
    });
  });
});
