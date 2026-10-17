/**
 * DRF-2752 — шов потока записи C05: «способ исполнения → специалист → время».
 *
 * Дефект (аудит 02.10, переподтверждён на `af83f6fe`): услуга ехала по
 * адресу (`?service=`), а экран времени и запасной список мастеров читали её
 * из черновика записи — и на этом пути черновик не писал никто. Пустой
 * черновик → человека выбрасывало в каталог; черновик от прошлого выбора →
 * время и подтверждение шли по ЧУЖОЙ услуге и чужому мастеру.
 *
 * Почему прежние узлы этого не ловили: узлы экранов «вариант» и «специалист»
 * подменяют `useNavigate` и проверяют только строку адреса, а узел экрана
 * времени сам заполняет черновик в `beforeEach`. Шов между экранами не
 * исполнялся ни одним.
 *
 * Поэтому здесь экраны стоят в ОДНОМ настоящем роутере, с настоящим
 * черновиком; подменена только сеть. Переход делается нажатием, а не
 * сборкой адреса руками. **`beforeEach` черновик только очищает** — услугу
 * и мастера в него кладут сами экраны, иначе узел проверял бы себя.
 *
 * Настоящая запись не создаётся: ручка создания подменена, и узел смотрит,
 * с чем экран подтверждения её позвал бы.
 *
 * Сценарии (P0-01…08 листа и входы сверх них):
 *  01. пустой черновик, весь путь → окна нужного мастера по нужной услуге;
 *  02. в черновике старая услуга A, выбрана B → только B, прежнее время снято;
 *  03. «другие варианты» → окна и черновик второго мастера, услуга та же;
 *  04. полный список мастеров (кнопкой и без кандидатов) → услуга пути цела;
 *  05. прямая ссылка / перезагрузка → окна по адресу, имена — с сервера;
 *  06. назад и повторный выбор → прежнего мастера и его времени нет;
 *  07. подтверждение показывает и отправляет выбранные услугу и мастера;
 *  08. окна не загрузились → идти дальше нечем, запись не создаётся;
 *  09. без `?service=` прежние правила целы;
 *  10. карточка мастера: адрес главнее черновика; без услуги — в каталог.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, getInitData: () => "test-init-data", openPaymentConfirmation: vi.fn() };
});

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return {
    ...original,
    getCatalogBrowse: vi.fn(),
    getCustomerSlots: vi.fn(),
    getCustomerMaster: vi.fn(),
    getBookingQuote: vi.fn(),
    createCustomerBooking: vi.fn(),
  };
});

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchMasters: vi.fn(), fetchService: vi.fn(), authVerify: vi.fn() };
});

vi.mock("../lib/payments", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/payments")>();
  return { ...original, createPayment: vi.fn() };
});

import { authVerify, fetchMasters, fetchService } from "../lib/api";
import { OPTION_CTA, PROVIDER_CTA, PROVIDER_OTHER_LINK } from "../lib/booking-flow";
import { CHANGE_PROVIDER } from "../lib/booking-outcome";
import {
  createCustomerBooking,
  getBookingQuote,
  getCatalogBrowse,
  getCustomerMaster,
  getCustomerSlots,
  type CatalogBrowseData,
} from "../lib/customer-booking";
import {
  getBookingDraft,
  resetBooking,
  setEntryPoint,
  setMaster,
  setService,
  setVisitAt,
  useBookingDraft,
} from "../state/booking";
import { CustomerBookingConfirmScreen } from "./CustomerBookingConfirmScreen";
import { CustomerMasterDetailScreen, OTHER_MASTERS_LABEL } from "./CustomerMasterDetailScreen";
import { CustomerSlotsScreen } from "./CustomerSlotsScreen";
import { ExecutionOptionScreen } from "./ExecutionOptionScreen";
import { MasterPickerScreen } from "./MasterPickerScreen";
import { ProviderChoiceScreen } from "./ProviderChoiceScreen";

const mockedBrowse = vi.mocked(getCatalogBrowse);
const mockedSlots = vi.mocked(getCustomerSlots);
const mockedMasters = vi.mocked(fetchMasters);
const mockedMaster = vi.mocked(getCustomerMaster);
const mockedService = vi.mocked(fetchService);
const mockedQuote = vi.mocked(getBookingQuote);
const mockedCreate = vi.mocked(createCustomerBooking);
const mockedAuthVerify = vi.mocked(authVerify);

/** Время — в будущем относительно часов теста: подтверждение прошедшее время не записывает. */
const DAY = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString().slice(0, 10);
const SLOT = `${DAY}T10:00:00+03:00`;
const STALE_TIME = `${DAY}T19:00:00+03:00`;

const SERVICE = {
  id: "svc-1",
  slug: "lymph",
  name: "Лимфодренажный массаж",
  short_description: "",
  description: "",
  price_from: "3200.00",
  duration_min: 60,
  is_popular: false,
  contraindications: "",
  is_bookable: true,
} as unknown as CatalogBrowseData["services"][number];

function master(id: string, name: string) {
  return {
    id,
    name,
    specialization: "Массаж",
    bio: "",
    experience: "",
    rating: "4.8",
    photo_url: "",
    review_count: 74,
  } as unknown as CatalogBrowseData["masters"][number];
}

function providerPick(masterId: string, rank: number) {
  return {
    masterId,
    tier: 1,
    rank,
    reasonCodes: ["ELIG_CAPABILITY_VERIFIED"],
    reasons: ["работает с выбранной услугой"],
  };
}

function browse(over: Partial<CatalogBrowseData> = {}): CatalogBrowseData {
  return {
    services: [SERVICE],
    masters: [master("m-1", "Екатерина С."), master("m-2", "Анна П.")],
    picks: [
      {
        serviceId: "svc-1",
        tier: 1,
        rank: 1,
        reasonCodes: ["MATCH_GOAL_CATEGORY", "ELIG_CAPABILITY_VERIFIED"],
        reasons: ["Подходит под твою цель"],
      },
    ],
    providerPicks: [providerPick("m-1", 1), providerPick("m-2", 2)],
    picksOutcome: "OK",
    emptyReason: null,
    ...over,
  } as CatalogBrowseData;
}

/** Черновик глазами следующего экрана — на месте шага, которого в узле нет. */
function DraftProbe() {
  const draft = useBookingDraft();
  const navigate = useNavigate();
  return (
    <div>
      <pre data-testid="draft">{JSON.stringify(draft)}</pre>
      <button type="button" onClick={() => navigate(-1)}>
        НАЗАД
      </button>
    </div>
  );
}

function shownDraft(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("draft").textContent ?? "{}") as Record<string, unknown>;
}

/** С чем экраны звали ручку окон — по вызовам, по порядку. */
function slotRequests(): Array<{ masterId: string; serviceId: string }> {
  return mockedSlots.mock.calls.map(([args]) => ({
    masterId: args.masterId,
    serviceId: args.serviceId,
  }));
}

function masterListRequests(): Array<string | undefined> {
  return mockedMasters.mock.calls.map(([args]) => args?.serviceId);
}

function renderFlow(entry: string, { realConfirm = false } = {}) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/customer/booking/option" element={<ExecutionOptionScreen />} />
        <Route path="/customer/booking/provider" element={<ProviderChoiceScreen />} />
        <Route path="/customer/masters/:masterId/slots" element={<CustomerSlotsScreen />} />
        <Route path="/customer/masters/:masterId" element={<CustomerMasterDetailScreen />} />
        <Route path="/customer/book/master" element={<MasterPickerScreen />} />
        <Route path="/customer/book/when" element={<DraftProbe />} />
        <Route
          path="/customer/booking/confirm"
          element={realConfirm ? <CustomerBookingConfirmScreen /> : <DraftProbe />}
        />
        <Route path="/customer/booking/success/:bookingId" element={<div>SUCCESS-PROBE</div>} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Черновик, оставшийся от другого, прежнего выбора (услуга A). */
function leaveForeignDraft() {
  setEntryPoint("catalog");
  setService("svc-OLD", "Маникюр");
  setMaster("m-OLD", "Ольга В.");
  setVisitAt(STALE_TIME);
}

async function walkToProvider() {
  await userEvent.click(await screen.findByRole("button", { name: OPTION_CTA }));
  // Экран «специалист» отрисован: кнопка называет лучшего по имени.
  return screen.findByRole("button", { name: PROVIDER_CTA("Екатерина С.") });
}

async function pickTheTenOClockSlot() {
  await userEvent.click(await screen.findByRole("button", { name: /10:00/ }));
  await userEvent.click(screen.getByRole("button", { name: "Дальше" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  // ТОЛЬКО очистка. Услугу и мастера в черновик кладут экраны.
  resetBooking();
  mockedBrowse.mockResolvedValue(browse());
  mockedSlots.mockResolvedValue({
    slots: [{ date: DAY, start: SLOT }],
    dateFrom: DAY,
    dateTo: DAY,
  });
  mockedMasters.mockResolvedValue({
    masters: [{ id: "m-9", name: "Мария К." }],
  } as unknown as Awaited<ReturnType<typeof fetchMasters>>);
  mockedMaster.mockResolvedValue({
    master: {
      id: "m-1",
      name: "Екатерина С.",
      specialization: "массаж",
      bio: "",
      experience: "7 лет",
      rating: "4.9",
      photo_url: "",
      service_ids: ["svc-1"],
    },
  } as unknown as Awaited<ReturnType<typeof getCustomerMaster>>);
  mockedService.mockResolvedValue({
    service: SERVICE,
  } as unknown as Awaited<ReturnType<typeof fetchService>>);
  mockedQuote.mockResolvedValue({ price: "3200.00", duration_minutes: 60, source: "service" });
  mockedAuthVerify.mockResolvedValue({
    user: { id: "u-1", channel_user_id: "cu-1", display_name: "Ольга", client_name: "" },
    tenant: { slug: "demo", name: "Demo", timezone: "Europe/Moscow" },
    pending_booking_intent: null,
  } as unknown as Awaited<ReturnType<typeof authVerify>>);
  mockedCreate.mockResolvedValue({
    booking: { id: "b-1", status: "confirmed" },
  } as unknown as Awaited<ReturnType<typeof createCustomerBooking>>);
});

describe("весь путь: вариант → специалист → время", () => {
  it("P0-01. пустой черновик — окна выбранного мастера по услуге пути, а не каталог", async () => {
    renderFlow("/customer/booking/option");
    await userEvent.click(await walkToProvider());

    await pickTheTenOClockSlot();

    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-1" }]);
    expect(shownDraft()).toMatchObject({
      serviceId: "svc-1",
      serviceName: "Лимфодренажный массаж",
      masterId: "m-1",
      masterName: "Екатерина С.",
      visitAt: SLOT,
    });
    expect(screen.queryByText("CATALOG-PROBE")).not.toBeInTheDocument();
  });

  it("P0-02. в черновике старая услуга A, выбрана B — используется только B", async () => {
    leaveForeignDraft();
    renderFlow("/customer/booking/option");
    await userEvent.click(await walkToProvider());

    // Время, выбранное когда-то под услугу A у другого мастера, к этому пути
    // не относится: пока человек не выбрал новое, идти дальше нечем.
    expect(await screen.findByRole("button", { name: /10:00/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Выбрать время" })).toBeDisabled();

    await pickTheTenOClockSlot();

    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-1" }]);
    expect(shownDraft()).toEqual({
      serviceId: "svc-1",
      serviceName: "Лимфодренажный массаж",
      masterId: "m-1",
      masterName: "Екатерина С.",
      visitAt: SLOT,
      // Чужой путь не оставил следов: ни переноса чужой записи, ни своего
      // источника входа.
      rescheduleOf: null,
      entryPoint: null,
    });
  });

  it("P0-03. «другие варианты» — окна и черновик второго мастера, услуга та же", async () => {
    renderFlow("/customer/booking/option");
    await walkToProvider();
    await userEvent.click(screen.getByRole("button", { name: /Анна П\./ }));

    await pickTheTenOClockSlot();

    expect(slotRequests()).toEqual([{ masterId: "m-2", serviceId: "svc-1" }]);
    expect(shownDraft()).toMatchObject({
      serviceId: "svc-1",
      serviceName: "Лимфодренажный массаж",
      masterId: "m-2",
      masterName: "Анна П.",
    });
  });

  it("P0-06. назад к выбору мастера и повторный выбор — прежнего мастера и его времени нет", async () => {
    renderFlow("/customer/booking/option", { realConfirm: true });
    await userEvent.click(await walkToProvider());
    await pickTheTenOClockSlot();
    await screen.findByRole("button", { name: "Записаться" });
    expect(getBookingDraft()).toMatchObject({ masterId: "m-1", visitAt: SLOT });

    // С подтверждения — к выбору мастера, его же кнопкой, и другой мастер.
    await userEvent.click(screen.getByRole("button", { name: CHANGE_PROVIDER }));
    await userEvent.click(await screen.findByRole("button", { name: /Анна П\./ }));

    // Время выбиралось у Екатерины — у Анны оно не выбрано.
    await screen.findByRole("button", { name: /10:00/ });
    expect(screen.getByRole("button", { name: "Выбрать время" })).toBeDisabled();
    expect(getBookingDraft()).toMatchObject({
      serviceId: "svc-1",
      serviceName: "Лимфодренажный массаж",
      masterId: "m-2",
      masterName: "Анна П.",
      visitAt: null,
    });
    expect(slotRequests().at(-1)).toEqual({ masterId: "m-2", serviceId: "svc-1" });
    expect(mockedCreate).toHaveBeenCalledTimes(0);
  });
});

describe("запасной путь: полный список мастеров", () => {
  it("P0-04. пустой черновик — список по услуге пути, выбранный мастер попадает в черновик", async () => {
    renderFlow("/customer/booking/option");
    await walkToProvider();
    await userEvent.click(screen.getByRole("button", { name: PROVIDER_OTHER_LINK }));

    await userEvent.click(await screen.findByText("Мария К."));

    expect(masterListRequests()).toEqual(["svc-1"]);
    expect(shownDraft()).toMatchObject({
      serviceId: "svc-1",
      serviceName: "Лимфодренажный массаж",
      masterId: "m-9",
      masterName: "Мария К.",
    });
    expect(screen.queryByText("CATALOG-PROBE")).not.toBeInTheDocument();
  });

  it("P0-04. чужой черновик — список по услуге пути, не по чужой", async () => {
    leaveForeignDraft();
    renderFlow("/customer/booking/option");
    await walkToProvider();
    await userEvent.click(screen.getByRole("button", { name: PROVIDER_OTHER_LINK }));

    await userEvent.click(await screen.findByText("Мария К."));

    expect(masterListRequests()).toEqual(["svc-1"]);
    expect(shownDraft()).toMatchObject({ serviceId: "svc-1", masterId: "m-9", visitAt: null });
  });

  it("P0-04. кандидатов-специалистов нет — тот же список, по услуге пути", async () => {
    mockedBrowse.mockResolvedValue(browse({ providerPicks: [] }));
    renderFlow("/customer/booking/option");
    await userEvent.click(await screen.findByRole("button", { name: OPTION_CTA }));

    expect(await screen.findByText("Мария К.")).toBeInTheDocument();
    expect(masterListRequests()).toEqual(["svc-1"]);
    expect(screen.queryByText("CATALOG-PROBE")).not.toBeInTheDocument();
  });
});

describe("прямой вход на экран времени (перезагрузка, ссылка)", () => {
  it("P0-05. пустой черновик и `?service=` — окна по адресу, имена спрошены у сервера", async () => {
    renderFlow("/customer/masters/m-1/slots?service=svc-1");

    await pickTheTenOClockSlot();

    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-1" }]);
    expect(mockedMaster).toHaveBeenCalledWith("m-1");
    expect(mockedService).toHaveBeenCalledWith("svc-1");
    expect(shownDraft()).toMatchObject({
      serviceId: "svc-1",
      serviceName: "Лимфодренажный массаж",
      masterId: "m-1",
      masterName: "Екатерина С.",
      visitAt: SLOT,
    });
  });

  it("P0-05. сервер имён не отдал — окна есть, имя не выдумано", async () => {
    mockedMaster.mockRejectedValue(new Error("offline"));
    mockedService.mockRejectedValue(new Error("offline"));
    renderFlow("/customer/masters/m-1/slots?service=svc-1");

    await pickTheTenOClockSlot();

    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-1" }]);
    expect(shownDraft()).toMatchObject({
      serviceId: "svc-1",
      serviceName: "",
      masterId: "m-1",
      masterName: "",
      visitAt: SLOT,
    });
  });

  it("P0-05. чужой черновик и `?service=` — адрес главнее черновика", async () => {
    leaveForeignDraft();
    renderFlow("/customer/masters/m-1/slots?service=svc-1");

    await pickTheTenOClockSlot();

    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-1" }]);
    expect(shownDraft()).toMatchObject({ serviceId: "svc-1", masterId: "m-1", visitAt: SLOT });
  });

  it("имя, уже известное черновику, пустым не затирается", async () => {
    // Услуга та же, что в адресе, и её имя известно — прямой вход его не трогает
    // и сервер о нём не спрашивает.
    setService("svc-1", "Лимфодренажный массаж");
    setMaster("m-1", "Екатерина С.");
    renderFlow("/customer/masters/m-1/slots?service=svc-1");

    await pickTheTenOClockSlot();

    expect(shownDraft()).toMatchObject({
      serviceName: "Лимфодренажный массаж",
      masterName: "Екатерина С.",
    });
    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-1" }]);
    expect(mockedService).toHaveBeenCalledTimes(0);
    expect(mockedMaster).toHaveBeenCalledTimes(0);
  });
});

describe("прямой вход на промежуточные шаги", () => {
  it("«специалист» по ссылке при чужом черновике — имя выбранного мастера не теряется, даже если сервер имён не отдал", async () => {
    // Так сюда приходит и «Изменить специалиста» с подтверждения. Услугу
    // пути называет адрес; мастер и его имя — этот экран. Если бы услугу
    // выравнивал только экран времени, смена услуги стёрла бы уже
    // записанное имя мастера, и восстановить его было бы неоткуда.
    mockedMaster.mockRejectedValue(new Error("offline"));
    mockedService.mockRejectedValue(new Error("offline"));
    leaveForeignDraft();
    renderFlow("/customer/booking/provider?service=svc-1");
    await userEvent.click(
      await screen.findByRole("button", { name: PROVIDER_CTA("Екатерина С.") }),
    );

    await pickTheTenOClockSlot();

    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-1" }]);
    expect(shownDraft()).toMatchObject({
      serviceId: "svc-1",
      masterId: "m-1",
      masterName: "Екатерина С.",
      visitAt: SLOT,
    });
  });

  it("полный список мастеров по ссылке, пустой черновик — список по услуге из адреса, она же в черновике", async () => {
    renderFlow("/customer/book/master?service=svc-1");

    await userEvent.click(await screen.findByText("Мария К."));

    expect(masterListRequests()).toEqual(["svc-1"]);
    expect(shownDraft()).toMatchObject({ serviceId: "svc-1", masterId: "m-9", masterName: "Мария К." });
  });

  it("полный список мастеров по ссылке, чужой черновик — услуга из адреса, не чужая", async () => {
    leaveForeignDraft();
    renderFlow("/customer/book/master?service=svc-1");

    await userEvent.click(await screen.findByText("Мария К."));

    expect(masterListRequests()).toEqual(["svc-1"]);
    expect(shownDraft()).toMatchObject({ serviceId: "svc-1", masterId: "m-9", visitAt: null });
  });
});

describe("подтверждение — настоящий экран, подменена только ручка создания", () => {
  it("P0-07. показывает и отправляет выбранные услугу и мастера, а не чужие", async () => {
    leaveForeignDraft();
    renderFlow("/customer/booking/option", { realConfirm: true });
    await userEvent.click(await walkToProvider());
    await pickTheTenOClockSlot();

    expect(await screen.findByText(/Лимфодренажный массаж/)).toBeInTheDocument();
    expect(screen.getByText(/Екатерина С\./)).toBeInTheDocument();
    expect(mockedQuote).toHaveBeenCalledWith("m-1", "svc-1");
    // До нажатия ничего не создано.
    expect(mockedCreate).toHaveBeenCalledTimes(0);

    await userEvent.click(await screen.findByRole("button", { name: "Записаться" }));

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    expect(mockedCreate.mock.calls[0]?.[0]).toMatchObject({
      service_id: "svc-1",
      master_id: "m-1",
      visit_at: SLOT,
    });
  });
});

describe("сбой окон не ведёт к записи", () => {
  it("P0-08. окна не загрузились — времени нет, подтверждение недостижимо, ручка создания не тронута", async () => {
    mockedSlots.mockRejectedValue(new Error("slots down"));
    leaveForeignDraft();
    renderFlow("/customer/booking/option", { realConfirm: true });
    await userEvent.click(await walkToProvider());

    await waitFor(() =>
      expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-1" }]),
    );
    await waitFor(() =>
      expect(getBookingDraft()).toMatchObject({ serviceId: "svc-1", masterId: "m-1", visitAt: null }),
    );
    // Кнопки «Дальше» с чужим временем нет; подтверждения на экране нет.
    expect(screen.queryByRole("button", { name: "Дальше" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Записаться" })).not.toBeInTheDocument();
    expect(mockedCreate).toHaveBeenCalledTimes(0);
  });
});

describe("без `?service=` прежние правила целы", () => {
  it("09а. пустой черновик — как и раньше, в каталог: выбрать услугу заново", async () => {
    renderFlow("/customer/masters/m-1/slots");

    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
    expect(mockedSlots).toHaveBeenCalledTimes(0);
  });

  it("09б. вход из карточки мастера с заполненным черновиком — окна по нему, имена и время не тронуты", async () => {
    // Здесь черновик заполнен нарочно: это прежний вход (карточка мастера
    // сама пишет мастера), и узел держит, что правка его не сломала.
    setService("svc-7", "Маникюр");
    setMaster("m-1", "Екатерина С.");
    setVisitAt(SLOT);
    renderFlow("/customer/masters/m-1/slots");

    await userEvent.click(await screen.findByRole("button", { name: "Дальше" }));

    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-7" }]);
    expect(shownDraft()).toMatchObject({
      serviceId: "svc-7",
      serviceName: "Маникюр",
      masterId: "m-1",
      masterName: "Екатерина С.",
      visitAt: SLOT,
    });
  });

  it("09в. адрес другого мастера при черновике прежнего — время прежнего снято", async () => {
    setService("svc-7", "Маникюр");
    setMaster("m-OLD", "Ольга В.");
    setVisitAt(STALE_TIME);
    renderFlow("/customer/masters/m-1/slots");

    await screen.findByRole("button", { name: /10:00/ });

    expect(screen.getByRole("button", { name: "Выбрать время" })).toBeDisabled();
    expect(getBookingDraft()).toMatchObject({ serviceId: "svc-7", masterId: "m-1", visitAt: null });
    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-7" }]);
  });
});

describe("карточка мастера — тот же шов", () => {
  it("10а. `?service=` при чужом черновике — окна по услуге из адреса, время прежнего пути снято", async () => {
    leaveForeignDraft();
    renderFlow("/customer/masters/m-1?service=svc-1");

    await userEvent.click(await screen.findByRole("button", { name: "Выбрать время" }));

    await screen.findByRole("button", { name: /10:00/ });
    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-1" }]);
    expect(getBookingDraft()).toMatchObject({
      serviceId: "svc-1",
      masterId: "m-1",
      masterName: "Екатерина С.",
      visitAt: null,
      // Источник входа — этого пути, а не оставшийся от чужого («catalog»).
      entryPoint: "master",
    });
  });

  it("10б. «Другие специалисты» с `?service=` при чужом черновике — список по услуге из адреса", async () => {
    leaveForeignDraft();
    renderFlow("/customer/masters/m-1?service=svc-1");

    await userEvent.click(await screen.findByRole("button", { name: OTHER_MASTERS_LABEL }));

    expect(await screen.findByText("Мария К.")).toBeInTheDocument();
    expect(masterListRequests()).toEqual(["svc-1"]);
  });

  it("10в. услуга не названа ни адресом, ни черновиком — «Выбрать время» ведёт выбрать услугу", async () => {
    // Названный предел, а не починка: с карточки мастера, открытой из
    // каталога без выбранной услуги, время выбрать нельзя — список услуг
    // мастера на карточке не реализован (вопрос владельцу, P1).
    renderFlow("/customer/masters/m-1");

    await userEvent.click(await screen.findByRole("button", { name: "Выбрать время" }));

    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
    expect(mockedSlots).toHaveBeenCalledTimes(0);
  });
});
