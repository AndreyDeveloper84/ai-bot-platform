/**
 * Главная не ходит за данными полки, пока полка обездвижена (DRF-2348).
 *
 * Решение владельца — `docs/OPEN_DECISIONS.md` §172, ответ 40 (23.09):
 * код полки «Ayla подобрала тебе» ОСТАВИТЬ обездвиженным, не удалять.
 * Оно снимает полку с экрана и НЕ снимает сетевое обращение: оно уходило
 * при каждом открытии Главной, хотя показывать нечего.
 *
 * Сторож нужен в ОБЕ стороны, и вторая сторона здесь не формальность:
 *
 * * полка тёмная → обращения нет. Иначе человек платит временем загрузки
 *   и трафиком, сервер — обращением, и ни один не получает ничего;
 * * полка зажжена → обращение ЕСТЬ и его данные доезжают до экрана. Без
 *   этого узла однажды полку включат, а данных не будет — и дефект будет
 *   выглядеть как «полка сломалась», хотя сломали её здесь и сейчас.
 *
 * Поэтому выключатель один на оба места (`lib/ayla-picks-shelf`), а не
 * два рядом стоящих условия: два рано или поздно разъезжаются.
 *
 * Третьего узла — «выключатель один и тот же» — здесь НЕТ намеренно. Он
 * был написан и снят: два независимых `false` прошли бы его точно так же,
 * то есть именно того, ради чего он назван, он показать не мог, а всё
 * остальное в нём уже доказано двумя узлами ниже (найдено ревью).
 *
 * И то, чего этот файл НЕ проверяет: условие `shelfOn` в самой вёрстке.
 * При тёмной полке данных в срезе нет вовсе, поэтому снятие того условия
 * ничего не меняет — цена решения, названная вслух.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCatalogBrowse: vi.fn() };
});
vi.mock("../lib/ayla-picks-shelf", async (importOriginal) => {
  // Подменяется ТОЛЬКО выключатель: замена модуля целиком отдала бы
  // `AYLA_PICKS_SHELF_ON` как `undefined`, и если экран когда-нибудь
  // прочитает константу напрямую, оба «тёмных» узла пройдут по неверной
  // причине (найдено ревью).
  const original =
    await importOriginal<typeof import("../lib/ayla-picks-shelf")>();
  return { ...original, aylaPicksShelfOn: vi.fn(() => false) };
});
vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "",
  setBackButton: vi.fn(),
  signalReady: vi.fn(),
  applyTheme: vi.fn(),
  hapticImpact: vi.fn(),
  closeApp: vi.fn(),
  returnToChat: vi.fn(() => "closed"),
  rememberChatLink: vi.fn(),
}));

import { getCatalogBrowse } from "../lib/customer-booking";
import type { CatalogBrowseData } from "../lib/customer-booking";
import { aylaPicksShelfOn } from "../lib/ayla-picks-shelf";
import { CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";

const mockedBrowse = vi.mocked(getCatalogBrowse);
const mockedShelfOn = vi.mocked(aylaPicksShelfOn);

/** Полка, которой ЕСТЬ что показать: иначе «включена — рисуется» прошёл
 *  бы на пустом ответе, ничего не доказав. */
const SHELF_ANSWER = {
  services: [
    {
      id: "s1",
      name: "Массаж",
      price_from: "2000",
      duration_min: 60,
      is_active: true,
    },
  ],
  masters: [],
  picks: [{ serviceId: "s1", tier: 1, rank: 1, reasonCodes: [], reasons: ["ты искала массаж"] }],
  picksOutcome: "OK",
} as unknown as CatalogBrowseData;

const TODAY: Record<string, unknown> = {
  calories_eaten: 1240,
  calories_target: 2100,
  water_glasses_eaten: 4,
  water_glasses_target: 8,
  active_goals: [{ title: "Подтянуть фигуру", week_num: 2 }],
  display_name: "Мария",
  diary_consent: true,
};

function ok(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

function serve(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const u = String(url);
      if (u.includes("/wellness/today")) return ok(TODAY);
      if (u.includes("/recent-activity")) return ok({ this_week_booking_count: 0 });
      if (u.includes("/plan-lite")) return ok({ plan_lite: null });
      if (u.includes("/last-topic")) return ok({ last_topic: null });
      throw new Error(`unexpected fetch: ${u}`);
    }),
  );
}

function renderHome() {
  return render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <Routes>
        <Route
          path="/customer/main"
          element={<CustomerWellnessDashboardScreen />}
        />
        <Route path="*" element={<div />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  // `restoreAllMocks` возвращает реализации шпионам, но историю вызовов
  // `vi.fn()` не чистит — и узел «обращения нет» видел вызов из соседнего
  // теста, где полка зажжена. Сам же узел это и поймал.
  vi.clearAllMocks();
  mockedShelfOn.mockReturnValue(false);
  // Под типом, а не `as never`: это единственная фикстура листа, которая
  // обязана доехать до DOM, и переименование поля в контракте должно
  // ронять сборку, а не превращаться в непонятный промах `getByText`.
  mockedBrowse.mockResolvedValue(SHELF_ANSWER);
  serve();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Запрос за полкой ходит только при зажжённой полке (DRF-2348)", () => {
  it("полка обездвижена — за её данными не ходим вовсе", async () => {
    renderHome();

    // Утверждение о НАЛИЧИИ идёт первым: экран должен успеть сходить за
    // остальным и отрисовать привезённое, иначе «обращения нет» окажется
    // правдой просто потому, что ничего ещё не начиналось.
    expect(await screen.findByText("Подтянуть фигуру")).toBeInTheDocument();
    expect(mockedBrowse).not.toHaveBeenCalled();
  });

  it("полка зажжена — обращение есть, и его данные видны на экране", async () => {
    mockedShelfOn.mockReturnValue(true);
    renderHome();

    await waitFor(() => expect(mockedBrowse).toHaveBeenCalled());
    // Данные доезжают до экрана, а не тонут по дороге: включение полки
    // обязано вернуть и обращение, и картинку.
    await waitFor(() =>
      expect(screen.getByText("ты искала массаж")).toBeInTheDocument(),
    );
  });
});
