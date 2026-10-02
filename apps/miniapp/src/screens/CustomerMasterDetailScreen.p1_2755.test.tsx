/**
 * DRF-2755 — карточка мастера, этап P1 (решение владельца 02.10): большое
 * фото вверху и услуги мастера с длительностью и ценой. За флагом
 * `VITE_MASTER_CARD_P1`. Расстояния здесь нет: оно ждёт правки каталога.
 *
 * Что заперто:
 *  - флаг выключен → карточка прежняя, каталог услуг не запрашивается;
 *  - фото: есть / нет / прокси отказал (404, 403, 500) / сеть упала / адрес
 *    не нашего прокси → инициалы, без `<img>`; карточка при этом работает;
 *  - услуги: только те, что мастер оказывает И каталог отдал И на них можно
 *    записаться; порядок — как в каталоге; нет цены — цены нет в строке;
 *  - запись с карточки: услуга и мастер уезжают на экран времени настоящим
 *    переходом, чужой черновик не побеждает; возврат на карточку цел;
 *  - услугу, которую мастер не оказывает, «Выбрать время» в запись не несёт.
 *
 * Сама загрузка байтов (подпись запроса, кэш, освобождение blob-адреса)
 * заперта в `lib/master-photo.test.ts` и `components/MasterPhoto.test.tsx` —
 * здесь она не повторяется, проверяется только карточка.
 *
 * Данные синтетические; настоящая запись не создаётся.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, getInitData: () => "test-init-data" };
});

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCustomerMaster: vi.fn(), getCustomerSlots: vi.fn() };
});

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchServices: vi.fn(), fetchService: vi.fn() };
});

import { fetchService, fetchServices, type Service } from "../lib/api";
import { getCustomerMaster, getCustomerSlots } from "../lib/customer-booking";
import { resetMasterPhotoCacheForTests } from "../lib/master-photo";
import {
  getBookingDraft,
  resetBooking,
  setEntryPoint,
  setMaster,
  setService,
  setVisitAt,
} from "../state/booking";
import { CustomerMasterDetailScreen, MASTER_SERVICES_HEAD } from "./CustomerMasterDetailScreen";
import { CustomerSlotsScreen } from "./CustomerSlotsScreen";

const mockedMaster = vi.mocked(getCustomerMaster);
const mockedSlots = vi.mocked(getCustomerSlots);
const mockedCatalog = vi.mocked(fetchServices);
const mockedService = vi.mocked(fetchService);
const fetchMock = vi.fn();

const PHOTO = "/api/v1/customer/media/masters/7b0c3f7e-1f7a-4a53-9a55-0d4d1f5d6a11/photo?v=3f2a9c1b7d4e";
const STORAGE_URL =
  "http://minio:9000/beautygo-media/specialists/avatars/a.jpg?AWSAccessKeyId=KEY&Signature=SIG&Expires=1790000000";
const DAY = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString().slice(0, 10);
const SLOT = `${DAY}T10:00:00+03:00`;

function service(id: string, name: string, over: Partial<Service> = {}): Service {
  return {
    id,
    slug: id,
    name,
    short_description: "",
    description: "",
    price_from: "3200.00",
    duration_min: 60,
    is_popular: false,
    contraindications: "",
    is_bookable: true,
    ...over,
  } as Service;
}

function masterAnswer(over: Record<string, unknown> = {}) {
  return {
    master: {
      id: "m-1",
      name: "Мария Петрова",
      specialization: "массаж",
      bio: "",
      experience: "7 лет",
      rating: "4.9",
      photo_url: "",
      service_ids: ["svc-1", "svc-2"],
      ...over,
    },
  } as unknown as Awaited<ReturnType<typeof getCustomerMaster>>;
}

function catalogAnswer(services: Service[]) {
  return { services } as unknown as Awaited<ReturnType<typeof fetchServices>>;
}

function photoResponse(): Response {
  return new Response(new Uint8Array([0xff, 0xd8, 0xff]), {
    status: 200,
    headers: { "Content-Type": "image/jpeg" },
  });
}

function images(): string[] {
  return [...document.querySelectorAll("img")].map((img) => img.getAttribute("src") ?? "");
}

/** Строки блока услуг — текстом, по порядку. */
function serviceRows(): string[] {
  const list = document.querySelector(".customer-master__services");
  return list ? [...list.querySelectorAll("button")].map((b) => b.textContent ?? "") : [];
}

function slotRequests(): Array<{ masterId: string; serviceId: string }> {
  return mockedSlots.mock.calls.map(([args]) => ({
    masterId: args.masterId,
    serviceId: args.serviceId,
  }));
}

function renderCard(entry = "/customer/masters/m-1") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/customer/masters/:masterId" element={<CustomerMasterDetailScreen />} />
        <Route path="/customer/masters/:masterId/slots" element={<CustomerSlotsScreen />} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function setOnLine(value: boolean) {
  Object.defineProperty(window.navigator, "onLine", { value, configurable: true });
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
  setOnLine(true);
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => "blob:ayla/master-1");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  vi.stubEnv("VITE_MASTER_CARD_P1", "1");
  mockedMaster.mockResolvedValue(masterAnswer());
  mockedCatalog.mockResolvedValue(
    catalogAnswer([service("svc-1", "Классический массаж"), service("svc-2", "Лимфодренаж")]),
  );
  mockedSlots.mockResolvedValue({ slots: [{ date: DAY, start: SLOT }], dateFrom: DAY, dateTo: DAY });
  mockedService.mockRejectedValue(new Error("not needed"));
});

afterEach(() => {
  resetMasterPhotoCacheForTests();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("флаг выключен — карточка прежняя", () => {
  it("ни фото, ни блока услуг; каталог услуг не запрашивается", async () => {
    vi.stubEnv("VITE_MASTER_CARD_P1", "");
    mockedMaster.mockResolvedValue(masterAnswer({ photo_url: PHOTO }));
    renderCard();

    // Положительная пара: карточка отрисована, мастер тот.
    expect(await screen.findByRole("button", { name: "Выбрать время" })).toBeEnabled();
    expect(screen.getAllByText("Мария Петрова").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("master-photo")).not.toBeInTheDocument();
    expect(screen.queryByText(MASTER_SERVICES_HEAD)).not.toBeInTheDocument();
    expect(mockedCatalog).toHaveBeenCalledTimes(0);
    expect(fetchMock).toHaveBeenCalledTimes(0);
  });
});

describe("фото", () => {
  it("есть — большое фото с именем мастера в подписи, байты через прокси с подписью запроса", async () => {
    fetchMock.mockResolvedValueOnce(photoResponse());
    mockedMaster.mockResolvedValue(masterAnswer({ photo_url: PHOTO }));
    renderCard();

    await waitFor(() => expect(images()).toEqual(["blob:ayla/master-1"]));
    expect(screen.getByRole("img", { name: "Мария Петрова" })).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(PHOTO);
    expect(new Headers(init.headers).get("Authorization")).toBe("MaxInitData test-init-data");
  });

  it("нет — инициалы, без запроса и без <img>", async () => {
    renderCard();

    const block = await screen.findByTestId("master-photo");
    expect(block).toHaveTextContent("МП");
    expect(images()).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(0);
  });

  it.each([404, 403, 500])("прокси ответил %i — инициалы, карточка работает", async (status) => {
    fetchMock.mockResolvedValueOnce(new Response("{}", { status }));
    mockedMaster.mockResolvedValue(masterAnswer({ photo_url: PHOTO }));
    renderCard();

    const block = await screen.findByTestId("master-photo");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(block).toHaveTextContent("МП");
    expect(images()).toEqual([]);
    expect(screen.getByRole("button", { name: "Выбрать время" })).toBeEnabled();
  });

  it("сеть упала — инициалы, карточка работает", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    mockedMaster.mockResolvedValue(masterAnswer({ photo_url: PHOTO }));
    renderCard();

    const block = await screen.findByTestId("master-photo");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(block).toHaveTextContent("МП");
    expect(images()).toEqual([]);
    expect(screen.getByRole("button", { name: "Выбрать время" })).toBeEnabled();
  });

  it("адрес не нашего прокси (хранилище с подписью) — инициалы, запроса по нему нет", async () => {
    mockedMaster.mockResolvedValue(masterAnswer({ photo_url: STORAGE_URL }));
    renderCard();

    const block = await screen.findByTestId("master-photo");
    expect(block).toHaveTextContent("МП");
    expect(images()).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(0);
  });

  it("другой мастер без фото после мастера с фото — чужого лица нет", async () => {
    fetchMock.mockResolvedValueOnce(photoResponse());
    mockedMaster.mockResolvedValue(masterAnswer({ photo_url: PHOTO }));
    const first = renderCard();
    await waitFor(() => expect(images()).toEqual(["blob:ayla/master-1"]));
    first.unmount();

    mockedMaster.mockResolvedValue(masterAnswer({ id: "m-2", name: "Анна Кузнецова" }));
    renderCard("/customer/masters/m-2");

    const block = await screen.findByTestId("master-photo");
    expect(block).toHaveTextContent("АК");
    expect(images()).toEqual([]);
  });
});

describe("услуги мастера", () => {
  it("несколько — название, длительность и цена «от», в порядке каталога", async () => {
    mockedCatalog.mockResolvedValue(
      catalogAnswer([
        service("svc-2", "Лимфодренаж", { duration_min: 90, price_from: "4500.00" }),
        service("svc-1", "Классический массаж"),
      ]),
    );
    renderCard();

    expect(await screen.findByText(MASTER_SERVICES_HEAD)).toBeInTheDocument();
    expect(serviceRows()).toEqual([
      "Лимфодренаж · 1 ч 30 мин · от 4 500 ₽",
      "Классический массаж · 1 ч · от 3 200 ₽",
    ]);
  });

  it("одна", async () => {
    mockedMaster.mockResolvedValue(masterAnswer({ service_ids: ["svc-1"] }));
    renderCard();

    expect(await screen.findByText(MASTER_SERVICES_HEAD)).toBeInTheDocument();
    expect(serviceRows()).toEqual(["Классический массаж · 1 ч · от 3 200 ₽"]);
  });

  it("мастер не оказывает услугу каталога — её в списке нет", async () => {
    mockedCatalog.mockResolvedValue(
      catalogAnswer([
        service("svc-1", "Классический массаж"),
        service("svc-9", "Маникюр"),
        service("svc-2", "Лимфодренаж"),
      ]),
    );
    renderCard();

    await screen.findByText(MASTER_SERVICES_HEAD);
    expect(serviceRows()).toEqual([
      "Классический массаж · 1 ч · от 3 200 ₽",
      "Лимфодренаж · 1 ч · от 3 200 ₽",
    ]);
  });

  it("услуга мастера, которую каталог не отдал (снята, чужой салон), — её в списке нет", async () => {
    mockedCatalog.mockResolvedValue(catalogAnswer([service("svc-1", "Классический массаж")]));
    renderCard();

    await screen.findByText(MASTER_SERVICES_HEAD);
    expect(serviceRows()).toEqual(["Классический массаж · 1 ч · от 3 200 ₽"]);
  });

  it("на услугу нельзя записаться — её в списке нет", async () => {
    mockedCatalog.mockResolvedValue(
      catalogAnswer([
        service("svc-1", "Классический массаж"),
        service("svc-2", "Лимфодренаж", { is_bookable: false }),
      ]),
    );
    renderCard();

    await screen.findByText(MASTER_SERVICES_HEAD);
    expect(serviceRows()).toEqual(["Классический массаж · 1 ч · от 3 200 ₽"]);
  });

  it("нет цены в данных — цены нет в строке: ни прочерка, ни «0 ₽»", async () => {
    mockedCatalog.mockResolvedValue(
      catalogAnswer([
        service("svc-1", "Классический массаж", { price_from: null }),
        service("svc-2", "Лимфодренаж", { price_from: "0.00" }),
      ]),
    );
    renderCard();

    await screen.findByText(MASTER_SERVICES_HEAD);
    expect(serviceRows()).toEqual(["Классический массаж · 1 ч", "Лимфодренаж · 1 ч"]);
  });

  it("нет длительности — только цена; нет ни того ни другого — только название", async () => {
    mockedCatalog.mockResolvedValue(
      catalogAnswer([
        service("svc-1", "Классический массаж", { duration_min: null }),
        service("svc-2", "Лимфодренаж", { duration_min: null, price_from: null }),
      ]),
    );
    renderCard();

    await screen.findByText(MASTER_SERVICES_HEAD);
    expect(serviceRows()).toEqual(["Классический массаж · от 3 200 ₽", "Лимфодренаж"]);
  });

  it("услуг нет — блока нет вовсе; близнец: с услугой блок есть", async () => {
    mockedMaster.mockResolvedValue(masterAnswer({ service_ids: [] }));
    const empty = renderCard();
    await screen.findByRole("button", { name: "Выбрать время" });
    await waitFor(() => expect(mockedCatalog).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(MASTER_SERVICES_HEAD)).not.toBeInTheDocument();
    expect(serviceRows()).toEqual([]);
    empty.unmount();

    mockedMaster.mockResolvedValue(masterAnswer({ service_ids: ["svc-1"] }));
    renderCard();
    expect(await screen.findByText(MASTER_SERVICES_HEAD)).toBeInTheDocument();
  });

  it("каталог услуг не ответил — блока нет, карточка работает", async () => {
    mockedCatalog.mockRejectedValue(new Error("catalog down"));
    renderCard();

    expect(await screen.findByRole("button", { name: "Выбрать время" })).toBeEnabled();
    await waitFor(() => expect(mockedCatalog).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(MASTER_SERVICES_HEAD)).not.toBeInTheDocument();
  });

  it("нет сети — запись с карточки выключена", async () => {
    setOnLine(false);
    renderCard();

    const row = await screen.findByRole("button", { name: /Классический массаж/ });
    expect(row).toBeDisabled();
    await userEvent.click(row);
    expect(mockedSlots).toHaveBeenCalledTimes(0);
  });
});

describe("запись с карточки", () => {
  it("услуга из списка → экран времени этого мастера по этой услуге, с именами", async () => {
    renderCard();
    await userEvent.click(await screen.findByRole("button", { name: /Лимфодренаж/ }));

    await screen.findByRole("button", { name: /10:00/ });
    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-2" }]);
    expect(getBookingDraft()).toMatchObject({
      serviceId: "svc-2",
      serviceName: "Лимфодренаж",
      masterId: "m-1",
      masterName: "Мария Петрова",
      visitAt: null,
      entryPoint: "master",
    });
    // Имена известны с карточки — сервер о них не спрашивается.
    expect(mockedService).toHaveBeenCalledTimes(0);
  });

  it("чужой черновик не побеждает выбранную услугу; время прежнего пути снято", async () => {
    setEntryPoint("catalog");
    setService("svc-OLD", "Маникюр");
    setMaster("m-OLD", "Ольга В.");
    setVisitAt(`${DAY}T19:00:00+03:00`);
    renderCard();
    await userEvent.click(await screen.findByRole("button", { name: /Классический массаж/ }));

    await screen.findByRole("button", { name: /10:00/ });
    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-1" }]);
    expect(screen.getByRole("button", { name: "Выбрать время" })).toBeDisabled();
    expect(getBookingDraft()).toMatchObject({
      serviceId: "svc-1",
      serviceName: "Классический массаж",
      masterId: "m-1",
      visitAt: null,
      entryPoint: "master",
    });
  });

  it("возврат с экрана времени — карточка и её услуги на месте", async () => {
    renderCard();
    await userEvent.click(await screen.findByRole("button", { name: /Лимфодренаж/ }));
    await screen.findByRole("button", { name: /10:00/ });

    await userEvent.click(screen.getByRole("button", { name: "Назад" }));

    expect(await screen.findByText(MASTER_SERVICES_HEAD)).toBeInTheDocument();
    expect(serviceRows()).toEqual([
      "Классический массаж · 1 ч · от 3 200 ₽",
      "Лимфодренаж · 1 ч · от 3 200 ₽",
    ]);
  });

  it("«Выбрать время» с услугой, которую мастер не оказывает, — время под неё не открывается", async () => {
    setService("svc-OLD", "Маникюр");
    renderCard();
    await screen.findByText(MASTER_SERVICES_HEAD);

    await userEvent.click(screen.getByRole("button", { name: "Выбрать время" }));

    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
    expect(mockedSlots).toHaveBeenCalledTimes(0);
  });

  it("близнец: «Выбрать время» с услугой, которую мастер оказывает, — время открывается по ней", async () => {
    setService("svc-2", "Лимфодренаж");
    renderCard();
    await screen.findByText(MASTER_SERVICES_HEAD);

    await userEvent.click(screen.getByRole("button", { name: "Выбрать время" }));

    await screen.findByRole("button", { name: /10:00/ });
    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-2" }]);
  });

  it("каталог не ответил — судить не о чем: «Выбрать время» работает по прежнему правилу", async () => {
    mockedCatalog.mockRejectedValue(new Error("catalog down"));
    setService("svc-OLD", "Маникюр");
    renderCard();
    await userEvent.click(await screen.findByRole("button", { name: "Выбрать время" }));

    await screen.findByRole("button", { name: /10:00/ });
    expect(slotRequests()).toEqual([{ masterId: "m-1", serviceId: "svc-OLD" }]);
  });
});
