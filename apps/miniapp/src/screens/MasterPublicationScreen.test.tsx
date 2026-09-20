/**
 * Экран 08 — отправка профиля на проверку (DRF-1818, M26).
 *
 * Заперто:
 * S1 — черновик + READY: 8.1, пункты ✓ со сводкой от сервера (услуги из выбора, каждый рабочий день),
 *      услуги на проверке отдельным блоком;
 * S2 — NOT_READY: 8.2, текст по коду каталога, переход — по deep_link readiness бота;
 *      место без редактора — без кнопки и с честной строкой;
 * S3 — ACTIVE: «Профиль опубликован!», «Услуги» из выбора, кабинет и «Добавить ещё услуги»;
 * S4 — PENDING (D2 → модератор): «Профиль отправлен на проверку», слова «опубликован» нет;
 * P1 — два тапа → один POST, кнопка заблокирована; P2 — после POST состояние читается из status;
 * P3 — обрыв → «Проверить статус» = GET, не второй POST; P4 — повтор после проверки — тем же command_id;
 * P5 — not_ready на POST → 8.2 из status;
 * E1 — отказы по `error`, каждый своим текстом, на загрузке и на POST; E2 — 5xx загрузки → общий текст;
 * G1 — ни в одном состоянии нет «популярн», «несколько секунд», «немного больше времени», процентов.
 *
 * Сторож от зависимости от времени — как в тестах экранов 03/04: малый asyncUtilTimeout и явный settle().
 */
import { act, configure, fireEvent, getConfig, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getPublicationStatus: vi.fn(),
    publishProfile: vi.fn(),
    getOnboardingReadiness: vi.fn(),
    getServiceSelection: vi.fn(),
    getWorkingHours: vi.fn(),
    listCanonGapRequests: vi.fn(),
    getMasterMe: vi.fn(),
  };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { ApiError } from "../lib/api";
import {
  getMasterMe,
  getOnboardingReadiness,
  getPublicationStatus,
  getServiceSelection,
  getWorkingHours,
  listCanonGapRequests,
  publishProfile,
  type CanonGapRequest,
  type OnboardingReadiness,
  type PublicationStatus,
  type PublishResponse,
  type ReadinessItem,
  type SelectedService,
  type ServiceSelectionState,
  type WorkingHoursDay,
} from "../lib/master-api";
import {
  MasterPublicationScreen,
  PUBLICATION_COPY,
  PUBLICATION_MISSING_TEXT,
  PUBLICATION_REFUSAL_TEXT,
} from "./MasterPublicationScreen";

const GUARD_ASYNC_TIMEOUT_MS = 20;
let previousAsyncUtilTimeout = 1000;

beforeAll(() => {
  previousAsyncUtilTimeout = getConfig().asyncUtilTimeout;
  configure({ asyncUtilTimeout: GUARD_ASYNC_TIMEOUT_MS });
});

afterAll(() => {
  configure({ asyncUtilTimeout: previousAsyncUtilTimeout });
});

const settle = async (rounds = 6) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

const mockedStatus = vi.mocked(getPublicationStatus);
const mockedPublish = vi.mocked(publishProfile);
const mockedReadiness = vi.mocked(getOnboardingReadiness);
const mockedSelection = vi.mocked(getServiceSelection);
const mockedHours = vi.mocked(getWorkingHours);
const mockedRequests = vi.mocked(listCanonGapRequests);
const mockedMe = vi.mocked(getMasterMe);

/** Слова, которых нет ни в одном состоянии: спрос не измерен, время не обещано, процентов нет. */
const FORBIDDEN_COPY = /популярн|несколько секунд|немного больше времени|%/i;
const missing = (code: string): string => PUBLICATION_MISSING_TEXT[code] ?? `<нет текста для ${code}>`;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const DRAFT_READY: PublicationStatus = {
  specialist_id: "spec",
  profile_status: "draft",
  readiness: { status: "READY", missing: [] },
  last_request: null,
};

const NOT_READY: PublicationStatus = {
  ...DRAFT_READY,
  readiness: {
    status: "NOT_READY",
    missing: [
      { code: "photo_missing", section: "profile", detail: {} },
      { code: "location_not_assigned", section: "location", detail: { area_option: "location_area_unavailable" } },
      { code: "no_working_day", section: "hours", detail: {} },
    ],
  },
};

const withStatus = (profileStatus: string): PublicationStatus => ({ ...DRAFT_READY, profile_status: profileStatus });

const PUBLISHED: PublishResponse = {
  specialist_id: "spec",
  profile_status: "pending",
  replayed: false,
  request: {
    id: "req-1",
    command_id: "cmd",
    outcome: "submitted",
    from_status: "draft",
    to_status: "pending",
    created_at: "2026-09-15T10:00:00Z",
  },
};

function readinessItem(key: string, state: string, deepLink: string): ReadinessItem {
  return {
    key,
    state,
    detail: {},
    reason: state === "unavailable" ? "capability_not_built" : null,
    deep_link: deepLink,
  };
}

const READINESS: OnboardingReadiness = {
  ready: false,
  blocking: ["hours:missing", "profile:missing"],
  items: [
    readinessItem("services", "done", "/solo/services"),
    readinessItem("location", "unavailable", "/solo/settings"),
    readinessItem("hours", "missing", "/solo/working-hours"),
    readinessItem("profile", "missing", "/solo/profile"),
  ],
  identity: { state: "linked", link_status: null },
  setup_state: "SETUP_PENDING",
  sale_block: null,
};

function service(name: string, price: string, minutes: number): SelectedService {
  return {
    salon_service_id: `ss-${name}`,
    template_id: `tpl-${name}`,
    name,
    category_id: null,
    is_active: true,
    mapping_status: "mapped",
    offer: { id: `offer-${name}`, price, duration_minutes: minutes, is_active: true },
    configured: true,
    category_name: null,
    direction_id: null,
    direction_name: null,
    direction_sort_order: null,
  };
}

const SELECTION: ServiceSelectionState = {
  specialist_id: "spec",
  tenant_id: "t1",
  selected: 2,
  configured: 2,
  services: [service("Аппаратный маникюр", "1500.00", 60), service("Маникюр + гель-лак", "2200.00", 90)],
};

function day(dayOfWeek: number, start: string | null, end: string | null): WorkingHoursDay {
  return {
    day_of_week: dayOfWeek,
    is_working_day: start !== null,
    start_time: start,
    end_time: end,
    break_start: null,
    break_end: null,
  };
}

/** Разные часы в разные дни — сводка «Вт–Сб 10:00–19:00» их бы спрятала. */
const HOURS: WorkingHoursDay[] = [
  day(0, null, null),
  day(1, "10:00", "19:00"),
  day(2, null, null),
  day(3, "12:00", "20:00"),
  day(4, null, null),
  day(5, "10:00", "16:00"),
  day(6, null, null),
];

const PENDING_REQUEST: CanonGapRequest = {
  id: "gap-1",
  specialist_id: "spec",
  name: "SPA-маникюр",
  description: "",
  duration_minutes: 90,
  price: "1800.00",
  status: "pending",
  status_label: "На проверке",
  resolved_template_id: null,
  clarification_question: null,
  rejection_reason: null,
  decided_at: null,
  created_at: "2026-09-15T09:00:00Z",
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

async function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/solo/publication"]}>
      <Routes>
        <Route path="/solo/publication" element={<MasterPublicationScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
  await settle();
}

const title = () => screen.getByRole("heading", { level: 1 });
const submitButton = () => screen.getByRole("button", { name: PUBLICATION_COPY.submit });

beforeEach(() => {
  vi.clearAllMocks();
  mockedReadiness.mockResolvedValue(READINESS);
  mockedSelection.mockResolvedValue(SELECTION);
  mockedHours.mockResolvedValue({ specialist_id: "spec", timezone: "Europe/Moscow", schedule: HOURS });
  mockedRequests.mockResolvedValue({ requests: [PENDING_REQUEST] });
  mockedMe.mockResolvedValue({
    master: {
      id: "m1",
      name: "Анна",
      specialization: "Мастер маникюра",
      bio: "",
      photo_url: "",
      services: [],
    },
    salon: { tenant_id: "t1", name: "" },
    permissions: { can_edit_schedule: true, can_edit_services: true, can_message_customers: true },
  });
});

describe("экран 08 — состояния из ответа каталога", () => {
  it("S1: черновик и READY — 8.1: пункты ✓ со сводкой от сервера, услуги на проверке отдельно", async () => {
    mockedStatus.mockResolvedValue(DRAFT_READY);
    await renderScreen();

    expect(title()).toHaveTextContent(PUBLICATION_COPY.readyTitle);
    expect(screen.getByTestId("publication-section-services")).toHaveTextContent(
      "2 услуги с ценой и длительностью",
    );
    expect(screen.getByTestId("publication-section-hours")).toHaveTextContent(
      "Вт 10:00–19:00, Чт 12:00–20:00, Сб 10:00–16:00",
    );
    const review = screen.getByRole("region", { name: PUBLICATION_COPY.reviewTitle });
    expect(within(review).getByText("SPA-маникюр · 1 ч 30 мин · 1 800 ₽")).toBeInTheDocument();
    expect(within(review).getByText("На проверке")).toBeInTheDocument();
    expect(submitButton()).toBeEnabled();
  });

  it("S2: NOT_READY — 8.2: текст по коду, профиль ведёт по deep_link, место без редактора — без кнопки", async () => {
    mockedStatus.mockResolvedValue(NOT_READY);
    await renderScreen();

    expect(title()).toHaveTextContent(PUBLICATION_COPY.notReadyTitle);
    const place = screen.getByTestId("publication-section-location");
    expect(place).toHaveTextContent(missing("location_not_assigned"));
    expect(place).toHaveTextContent(PUBLICATION_COPY.locationUnavailable);
    expect(within(place).queryByRole("button")).toBeNull();
    expect(screen.queryByRole("button", { name: PUBLICATION_COPY.submit })).toBeNull();

    const profile = screen.getByTestId("publication-section-profile");
    expect(profile).toHaveTextContent(missing("photo_missing"));
    fireEvent.click(within(profile).getByRole("button"));
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/profile");
  });

  it("S2c: NOT_READY — недействительное место названо своим текстом, а не запасным", async () => {
    mockedStatus.mockResolvedValue({
      ...DRAFT_READY,
      readiness: {
        status: "NOT_READY",
        missing: [{ code: "location_inactive", section: "location", detail: { area_option: "location_area_unavailable" } }],
      },
    });
    await renderScreen();

    expect(title()).toHaveTextContent(PUBLICATION_COPY.notReadyTitle);
    const place = screen.getByTestId("publication-section-location");
    expect(place).toHaveTextContent(missing("location_inactive"));
    expect(place).not.toHaveTextContent("Пункт не заполнен.");
  });

  it("S2d: кодов, которых каталог больше не шлёт, в словаре экрана нет", () => {
    expect(Object.keys(PUBLICATION_MISSING_TEXT)).toContain("location_inactive");
    expect(Object.keys(PUBLICATION_MISSING_TEXT)).not.toContain("location_not_participating");
  });

  it("S2b: NOT_READY — «Настроить расписание» ведёт по deep_link пункта hours", async () => {
    mockedStatus.mockResolvedValue(NOT_READY);
    await renderScreen();

    const hours = screen.getByTestId("publication-section-hours");
    expect(hours).toHaveTextContent(missing("no_working_day"));
    fireEvent.click(within(hours).getByRole("button"));
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/working-hours");
  });

  it("S3: ACTIVE — «Профиль опубликован!», «Услуги» из выбора, каждый день, «Добавить ещё услуги» → 03", async () => {
    mockedStatus.mockResolvedValue(withStatus("active"));
    await renderScreen();

    expect(title()).toHaveTextContent(PUBLICATION_COPY.activeTitle);
    const services = screen.getByRole("region", { name: PUBLICATION_COPY.servicesTitle });
    expect(within(services).getByText("Аппаратный маникюр · 1 ч · 1 500 ₽")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: PUBLICATION_COPY.daysLabel })).toHaveTextContent("Чт 12:00–20:00");
    expect(screen.getByText(PUBLICATION_COPY.reviewAfter)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: PUBLICATION_COPY.addServices }));
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/services/select");
  });

  it("S3b: ACTIVE — «Перейти в кабинет» ведёт в «Мой день»", async () => {
    mockedStatus.mockResolvedValue(withStatus("active"));
    await renderScreen();

    fireEvent.click(screen.getByRole("button", { name: PUBLICATION_COPY.toCabinet }));
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/my-day");
  });

  it("S4: PENDING — «Профиль отправлен на проверку», слова «опубликован» и кнопки отправки нет", async () => {
    mockedStatus.mockResolvedValue(withStatus("pending"));
    await renderScreen();

    expect(title()).toHaveTextContent(PUBLICATION_COPY.pendingTitle);
    expect(document.body.textContent).not.toMatch(/опубликован/i);
    expect(screen.queryByRole("button", { name: PUBLICATION_COPY.submit })).toBeNull();
  });
});

describe("экран 08 — отправка", () => {
  it("P1/P2: два тапа → один POST с UUID-ключом; после ответа состояние читается из status", async () => {
    mockedStatus.mockResolvedValue(DRAFT_READY);
    let release: (value: PublishResponse) => void = () => {};
    mockedPublish.mockReturnValue(
      new Promise<PublishResponse>((resolve) => {
        release = resolve;
      }),
    );
    await renderScreen();

    const button = submitButton();
    fireEvent.click(button);
    fireEvent.click(button);
    await settle();

    expect(mockedPublish).toHaveBeenCalledTimes(1);
    expect(mockedPublish.mock.calls[0]?.[0]).toMatch(UUID_V4);
    const sending = screen.getByRole("button", { name: PUBLICATION_COPY.sending });
    expect(sending).toBeDisabled();
    expect(document.body.textContent).not.toMatch(FORBIDDEN_COPY);

    mockedStatus.mockResolvedValue(withStatus("pending"));
    release(PUBLISHED);
    await settle();

    expect(mockedStatus).toHaveBeenCalledTimes(2);
    expect(title()).toHaveTextContent(PUBLICATION_COPY.pendingTitle);
  });

  it("P3: обрыв — «Проверить статус» читает status и не шлёт второй POST", async () => {
    mockedStatus.mockResolvedValue(DRAFT_READY);
    mockedPublish.mockRejectedValue(new TypeError("Failed to fetch"));
    await renderScreen();

    fireEvent.click(submitButton());
    await settle();

    expect(screen.getByText(PUBLICATION_COPY.uncertainTitle)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: PUBLICATION_COPY.submit })).toBeNull();

    mockedStatus.mockResolvedValue(withStatus("pending"));
    fireEvent.click(screen.getByRole("button", { name: PUBLICATION_COPY.checkStatus }));
    await settle();

    expect(mockedStatus).toHaveBeenCalledTimes(2);
    expect(mockedPublish).toHaveBeenCalledTimes(1);
    expect(title()).toHaveTextContent(PUBLICATION_COPY.pendingTitle);
  });

  it("P4: 5xx, проверка показала черновик — повтор идёт с тем же command_id", async () => {
    mockedStatus.mockResolvedValue(DRAFT_READY);
    mockedPublish
      .mockRejectedValueOnce(new ApiError(503, "catalog_unavailable", "Каталог сейчас недоступен."))
      .mockResolvedValueOnce(PUBLISHED);
    await renderScreen();

    fireEvent.click(submitButton());
    await settle();
    fireEvent.click(screen.getByRole("button", { name: PUBLICATION_COPY.checkStatus }));
    await settle();

    mockedStatus.mockResolvedValue(withStatus("pending"));
    fireEvent.click(submitButton());
    await settle();

    expect(mockedPublish).toHaveBeenCalledTimes(2);
    expect(mockedPublish.mock.calls[1]?.[0]).toBe(mockedPublish.mock.calls[0]?.[0]);
    expect(title()).toHaveTextContent(PUBLICATION_COPY.pendingTitle);
  });

  it("P5: not_ready на POST — 8.2 со списком из status", async () => {
    mockedStatus.mockResolvedValueOnce(DRAFT_READY).mockResolvedValueOnce(NOT_READY);
    mockedPublish.mockRejectedValue(new ApiError(409, "not_ready", "Профиль ещё не готов к публикации."));
    await renderScreen();

    fireEvent.click(submitButton());
    await settle();

    expect(title()).toHaveTextContent(PUBLICATION_COPY.notReadyTitle);
    expect(screen.getByTestId("publication-section-profile")).toHaveTextContent(
      missing("photo_missing"),
    );
  });
});

describe("экран 08 — отказы", () => {
  it.each([
    [409, "catalog_profile_unresolved"],
    [403, "not_linked"],
    [404, "specialist_not_found"],
    [409, "salon_publication_owner_managed"],
    [409, "no_workspace_tenant"],
    [409, "publication_refused"],
  ])("E1: %s %s на загрузке — свой текст", async (code, slug) => {
    mockedStatus.mockRejectedValue(new ApiError(code, slug, "detail"));
    await renderScreen();

    expect(title()).toHaveTextContent(PUBLICATION_COPY.refusedTitle);
    expect(screen.getByTestId("publication-refusal")).toHaveTextContent(PUBLICATION_REFUSAL_TEXT[slug] ?? "—");
  });

  it("E1: у каждого отказа свой текст", () => {
    const texts = Object.values(PUBLICATION_REFUSAL_TEXT);
    expect(new Set(texts).size).toBe(texts.length);
  });

  it("E1b: catalog_profile_unresolved на POST — отказ по имени, а не «исход неизвестен»", async () => {
    mockedStatus.mockResolvedValue(DRAFT_READY);
    mockedPublish.mockRejectedValue(
      new ApiError(409, "catalog_profile_unresolved", "Профиль мастера ещё не заведён в каталоге."),
    );
    await renderScreen();

    fireEvent.click(submitButton());
    await settle();

    expect(screen.getByTestId("publication-refusal")).toHaveTextContent(
      PUBLICATION_REFUSAL_TEXT.catalog_profile_unresolved ?? "—",
    );
    expect(screen.queryByText(PUBLICATION_COPY.uncertainTitle)).toBeNull();
  });

  it("E2: 5xx на загрузке — общий текст ошибки, не отказ", async () => {
    mockedStatus.mockRejectedValue(new ApiError(503, "catalog_unavailable", "Каталог сейчас недоступен."));
    await renderScreen();

    expect(screen.getByText("Что-то у нас не получается прямо сейчас.")).toBeInTheDocument();
    expect(screen.queryByTestId("publication-refusal")).toBeNull();
  });
});

describe("экран 08 — слова", () => {
  it.each([
    ["8.1", DRAFT_READY, PUBLICATION_COPY.readyTitle],
    ["8.2", NOT_READY, PUBLICATION_COPY.notReadyTitle],
    ["8.4 на проверке", withStatus("pending"), PUBLICATION_COPY.pendingTitle],
    ["8.4 опубликован", withStatus("active"), PUBLICATION_COPY.activeTitle],
  ])("G1: %s — без «популярн», обещаний времени и процентов", async (_frame, statusValue, heading) => {
    mockedStatus.mockResolvedValue(statusValue);
    await renderScreen();

    expect(title()).toHaveTextContent(heading);
    expect(document.body.textContent).not.toMatch(FORBIDDEN_COPY);
  });
});

describe("системные состояния через SystemState (DRF-2194)", () => {
  it("загрузка — общий скелет без слов", () => {
    mockedStatus.mockReturnValue(new Promise(() => {}));
    void renderScreen();
    expect(screen.getByRole("status", { busy: true })).toBeInTheDocument();
  });

  it("ошибка — «Не удалось загрузить статус публикации» + «Попробовать снова», без клиентского словаря", async () => {
    mockedStatus.mockRejectedValueOnce(new Error("boom"));
    await renderScreen();
    expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить статус публикации");
    expect(screen.queryByText(/Не получилось загрузить/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    await settle();
    expect(mockedStatus).toHaveBeenCalledTimes(2);
  });
});
