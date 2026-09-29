/**
 * DRF-1989 — карточка услуги не рисует цену ниже 1 ₽.
 *
 * 0.40 ₽ раньше округлялось в «0 ₽». Только показ: `price_from` не меняется.
 *
 * ### Почему не `findByRole` (DRF-2622)
 *
 * Узел мигал: «`findByRole` заголовка не дождался», зелёный в двух прогонах из
 * трёх. Ждать тут нечего по часам: мок `fetchService` разрешается микрозадачей,
 * дальше `setState` и отрисовка React — обороты очереди событий, не время.
 * По часам ждёт сам `findByRole`: это `waitFor` с таймаутом 1000 мс НАСТОЯЩИХ
 * часов, и когда таймаут срабатывает, последней проверки он не делает. Цикл
 * событий воркера, остановленный соседями или сборкой мусора дольше таймаута,
 * отдаёт просроченный таймер таймаута раньше, чем React успеет отрисовать, —
 * узел падает без дефекта.
 *
 * Поэтому ждём событие, а не время: `settleScenario` — обороты очереди без
 * предела по часам (DRF-2616/2617), после него заголовок проверяется сразу.
 * Второй узел держит это свойство: он останавливает цикл событий на
 * {@link STALL_MS} мс ровно между ответом загрузки и отрисовкой. Прежний
 * `findByRole` на нём краснеет каждый раз, этот — зелёный.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, fetchService: vi.fn() };
});

import { fetchService, type Service } from "../lib/api";
import { resetBooking } from "../state/booking";
import { settleScenario } from "../test/settleScenario";
import { ServiceDetailScreen } from "./ServiceDetailScreen";

const mockedFetchService = vi.mocked(fetchService);

/** «0 ₽», перед которым не цифра: «9 000 ₽» сюда не попадает. */
const ZERO_PRICE = /(^|[^\d\s])\s?0 ₽/;

/** Дольше таймаута `waitFor` по умолчанию (1000 мс) — остановка, которую он не переживает. */
const STALL_MS = 1100;

function service(overrides: Partial<Service> = {}): Service {
  return {
    id: "svc-1989",
    slug: "piling",
    name: "Пилинг 1989",
    short_description: "",
    description: "",
    price_from: "9000.00",
    duration_min: 60,
    is_popular: false,
    contraindications: "",
    is_bookable: true,
    ...overrides,
  };
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/catalog/svc-1989"]}>
      <Routes>
        <Route path="/customer/catalog/:serviceId" element={<ServiceDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Занять поток, не отдавая цикл событий, — как занятый соседями воркер. */
function stallEventLoop(ms: number): void {
  const end = performance.now() + ms;
  while (performance.now() < end) {
    // намеренно пусто: ни один таймер и ни один оборот очереди не пройдут
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  resetBooking();
});

describe("ServiceDetailScreen — цена ниже 1 ₽ (DRF-1989)", () => {
  it("0.40 не рисуется как «0 ₽»", async () => {
    mockedFetchService.mockResolvedValue({ service: service({ price_from: "0.40" }) });
    renderScreen();

    await settleScenario();

    expect(screen.getByRole("heading", { name: /Пилинг 1989/ })).toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toMatch(ZERO_PRICE);
  });

  it("остановка цикла событий между загрузкой и отрисовкой не роняет узел (DRF-2622)", async () => {
    mockedFetchService.mockImplementation(() =>
      Promise.resolve().then(() => {
        stallEventLoop(STALL_MS);
        return { service: service({ price_from: "0.40" }) };
      }),
    );
    renderScreen();

    await settleScenario();

    expect(screen.getByRole("heading", { name: /Пилинг 1989/ })).toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toMatch(ZERO_PRICE);
  });
});
