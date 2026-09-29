/**
 * Кадр 1 макета DRF-1320 «Подходящий вариант для этого шага» (DRF-2178, Э-1).
 *
 * Этап 1 из 4, радиус ограничен намеренно: здесь только кадры 1–2 —
 * способ исполнения и выбор специалиста. Время (кадр 3), «Проверь
 * запись» (4) и снятие чатового пошагового выбора — следующие этапы.
 *
 * Откуда данные — и чего здесь не сочиняется:
 *
 * * **услуга** — первая SERVICE-рекомендация резолвера (`picks[0]`), тот
 *   же источник, что у полки. Таблицу «направление → услуга» в каталоге
 *   НЕ заводим: по ADR-0009 это транзакционный словарь Ayla, и завести
 *   его молча нельзя (вынесено владельцу);
 * * **длительность и цена** — из услуги зеркала, тем же форматом, что у
 *   карточки каталога (`lib/format.ts`). Нет значения — строки нет:
 *   «0 ₽» и «— мин» это не «неизвестно». Цена — прайс витрины, а не
 *   снимок: снимок фиксируется на «Проверь запись» (DRF-1708/2172), и
 *   до создания записи его не существует;
 * * **«Почему этот вариант»** — проверяемые причины из кодов решения
 *   (ПРАВКА 1 макета: абстрактный пункт «соответствует текущему
 *   контексту» убран). Причин нет — кадра нет, а не пустой заголовок.
 *
 * §60 в силе: OD-PILOT-9 снят только в части C04. «Ayla рекомендует
 * услугу X» здесь не звучит — сторож на это стоит ниже.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCatalogBrowse: vi.fn() };
});

const navigateSpy = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const original = await importOriginal<typeof import("react-router-dom")>();
  return { ...original, useNavigate: () => navigateSpy };
});

import { getCatalogBrowse, type CatalogBrowseData } from "../lib/customer-booking";
import {
  OPTION_CTA,
  OPTION_HEAD,
  OPTION_OTHER,
  OPTION_WHY_HEAD,
  PROVIDER_ROUTE,
} from "../lib/booking-flow";
import { ExecutionOptionScreen } from "./ExecutionOptionScreen";

const mockedBrowse = vi.mocked(getCatalogBrowse);

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

function browse(over: Partial<CatalogBrowseData> = {}): CatalogBrowseData {
  return {
    services: [SERVICE],
    masters: [],
    picks: [
      {
        serviceId: "svc-1",
        tier: 1,
        rank: 1,
        reasonCodes: ["MATCH_GOAL_CATEGORY", "ELIG_CAPABILITY_VERIFIED"],
        reasons: ["Подходит под твою цель", "Делает именно это"],
      },
    ],
    providerPicks: [],
    picksOutcome: "OK",
    emptyReason: null,
    ...over,
  } as CatalogBrowseData;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/booking/option"]}>
      <ExecutionOptionScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedBrowse.mockResolvedValue(browse());
});

describe("кадр «Подходящий вариант для этого шага»", () => {
  it("рисует услугу, её длительность и цену — по макету", async () => {
    renderScreen();
    expect(await screen.findByText(OPTION_HEAD)).toBeInTheDocument();
    expect(screen.getByText("Лимфодренажный массаж")).toBeInTheDocument();
    expect(screen.getByText(/3 200/)).toBeInTheDocument();
  });

  it("показывает только проверяемые причины (ПРАВКА 1)", async () => {
    renderScreen();
    await screen.findByText(OPTION_HEAD);
    expect(screen.getByText(OPTION_WHY_HEAD)).toBeInTheDocument();
    expect(screen.getByText("Подходит под твою цель")).toBeInTheDocument();
    expect(screen.queryByText(/соответствует текущему контексту/i)).not.toBeInTheDocument();
  });

  it("«Выбрать специалиста» ведёт на кадр выбора специалиста", async () => {
    renderScreen();
    await screen.findByText(OPTION_HEAD);
    await userEvent.click(screen.getByRole("button", { name: OPTION_CTA }));
    expect(navigateSpy).toHaveBeenCalledWith(expect.stringContaining(PROVIDER_ROUTE));
  });

  it("«Другой вариант» уводит в каталог — там способы исполнения целиком", async () => {
    renderScreen();
    await screen.findByText(OPTION_HEAD);
    await userEvent.click(screen.getByRole("button", { name: OPTION_OTHER }));
    expect(navigateSpy).toHaveBeenCalledWith("/customer/catalog");
  });

  it("длительность и цена — тем же форматом, что витрина каталога", async () => {
    // Свой формат здесь был бы вторым на ту же работу и однажды разошёлся
    // бы с карточкой услуги. Узел держит именно ОБЩИЙ формат — и поэтому
    // закрепляет отступление от макета: макет пишет «60 минут», а
    // `formatDuration` во всём продукте говорит «1 ч». Показать здесь
    // иначе значило бы, что одна и та же услуга в каталоге и в потоке
    // записи названа по-разному.
    renderScreen();
    await screen.findByText(OPTION_HEAD);
    expect(screen.getByText("1 ч · 3 200 ₽")).toBeInTheDocument();
  });

  it("причина не называет цену — цена живёт в строке услуги, не в «почему»", async () => {
    mockedBrowse.mockResolvedValue(
      browse({
        picks: [
          {
            serviceId: "svc-1",
            tier: 1,
            rank: 1,
            reasonCodes: ["EXEC_PRICE_INTENT_APPLIED"],
            reasons: ["Цена учтена по твоему запросу"],
          },
        ],
      } as Partial<CatalogBrowseData>),
    );
    renderScreen();
    await screen.findByText(OPTION_HEAD);
    const why = screen.getByText("Цена учтена по твоему запросу");
    // Речь о том, что запрос учли, — без суммы. Число с валютой в причине
    // означало бы, что цену называют дважды и, возможно, по-разному.
    expect(why.textContent ?? "").not.toMatch(/\d\s*₽/);
  });

  it("«Ayla рекомендует услугу X» не звучит — §60 снят только для C04", async () => {
    const { container } = renderScreen();
    await screen.findByText(OPTION_HEAD);
    expect(container.textContent ?? "").not.toMatch(/рекоменд/i);
  });
});

describe("когда показывать нечего", () => {
  it("без проверяемых причин кадра нет — человек идёт в каталог", async () => {
    mockedBrowse.mockResolvedValue(browse({ picks: [] }));
    renderScreen();
    await vi.waitFor(() => {
      expect(navigateSpy).toHaveBeenCalledWith("/customer/catalog");
    });
    expect(screen.queryByText(OPTION_HEAD)).not.toBeInTheDocument();
  });

  it("нет цены или длительности — строки нет, а не «0 ₽»", async () => {
    mockedBrowse.mockResolvedValue(
      browse({
        services: [{ ...SERVICE, price_from: null, duration_min: null }],
      } as Partial<CatalogBrowseData>),
    );
    const { container } = renderScreen();
    await screen.findByText(OPTION_HEAD);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/0\s*₽/);
    expect(text).not.toMatch(/—\s*мин/);
  });
});
