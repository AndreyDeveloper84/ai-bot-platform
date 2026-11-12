/**
 * DRF-2455 — миниатюра снимка в строке дневника, на ОБОИХ экранах, одинаково.
 *
 * Записи рисуют два экрана: день (`FoodScannerDayScreen`, `/diary/day`) и
 * главная дневника (`FoodScannerDiaryScreen`, `/wellness/today`). Поле,
 * поднятое в одном клиенте и потерянное в другом, у нас уже было — поэтому
 * каждый узел гоняется по обоим экранам одним и тем же набором.
 *
 * Три состояния карточки (решения владельца 28.09 п.14, главного окна 26.09
 * и 12.11 п.1 по DRF-2954):
 *  * снимок есть → миниатюра, источник — только прокси бота;
 *  * снимка нет (`has_photo` не `true`, или прокси ответил 404 — удалён по
 *    сроку) → значок на том же месте; при `has_photo` не `true` запроса к
 *    прокси НЕТ;
 *  * чисел нет (`calories: null`) → миниатюра на месте, калорий нет — два
 *    пустых состояния независимы.
 *
 * Место под картинку у карточки есть ВСЕГДА (DRF-2954): заморозка вида строки
 * по решению 28.09 п.10 снята решением владельца 12.11 п.1, и прежний предел
 * «строка прыгает, когда снимок догрузился» этим снят.
 */
import { render, waitFor } from "@testing-library/react";
import { StrictMode, type ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { primeDisplayName } from "../components/CustomerAvatarEntry";
import {
  DIARY_ENTRY_CLASS,
  DIARY_ENTRY_ICON_CLASS,
  DIARY_ENTRY_PHOTO_CLASS,
} from "../components/diary/DiaryEntryCard";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, getInitData: () => "test-init-data" };
});
vi.mock("../lib/diary-days", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/diary-days")>();
  return { ...original, getDiaryDay: vi.fn() };
});
vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, loadDiaryToday: vi.fn() };
});

import { loadDiaryToday, type FoodDiaryEntry, type WellnessToday } from "../lib/customer-wellness";
import { getDiaryDay } from "../lib/diary-days";
import { resetDiaryPhotoCacheForTests } from "../lib/diary-photo";
import { DAY_ROUTE_PATTERN, FoodScannerDayScreen, dayRoute } from "./FoodScannerDayScreen";
import { FoodScannerDiaryScreen } from "./FoodScannerDiaryScreen";
import { settleScenario } from "../test/settleScenario";

const PHOTO_PATH = (id: string) => `/api/v1/customer/diary/entry/${id}/photo`;

/** Карточки, у которых на месте картинки — значок (снимка нет). */
function iconCards(container: HTMLElement): Element[] {
  return Array.from(container.querySelectorAll(`li.${DIARY_ENTRY_CLASS}`)).filter((li) =>
    li.querySelector(`.${DIARY_ENTRY_ICON_CLASS}`),
  );
}

const WITH_PHOTO: FoodDiaryEntry = {
  id: "fl-photo",
  dish_name: "Овсянка с ягодами",
  calories: 320,
  protein_g: 11,
  fat_g: 6,
  carbs_g: 54,
  meal_type: "breakfast",
  logged_at: "2026-09-14T05:31:00Z",
  has_photo: true,
};
const WITHOUT_PHOTO: FoodDiaryEntry = {
  ...WITH_PHOTO,
  id: "fl-old",
  dish_name: "Борщ",
  meal_type: "lunch",
  logged_at: "2026-09-14T09:05:00Z",
  has_photo: false,
};
const NO_NUMBERS_WITH_PHOTO: FoodDiaryEntry = {
  ...WITH_PHOTO,
  id: "fl-risotto",
  dish_name: "Ризотто с трюфелем",
  calories: null,
  protein_g: null,
  fat_g: null,
  carbs_g: null,
  meal_type: "dinner",
  logged_at: "2026-09-14T17:00:00Z",
};

type Screen = {
  name: string;
  render: (entries: FoodDiaryEntry[]) => ReturnType<typeof render>;
};

function inRouter(path: string, pattern: string, element: ReactElement) {
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={pattern} element={element} />
        </Routes>
      </MemoryRouter>
    </StrictMode>,
  );
}

const SCREENS: Screen[] = [
  {
    name: "день (FoodScannerDayScreen)",
    render: (entries) => {
      vi.mocked(getDiaryDay).mockResolvedValue({
        date: "2026-09-14",
        calories_total: 320,
        entries,
      } as Awaited<ReturnType<typeof getDiaryDay>>);
      return inRouter(dayRoute("2026-09-14"), DAY_ROUTE_PATTERN, <FoodScannerDayScreen />);
    },
  },
  {
    name: "главная дневника (FoodScannerDiaryScreen)",
    render: (entries) => {
      vi.mocked(loadDiaryToday).mockResolvedValue({
        state: "entries",
        entries,
        hideNumbers: false,
        today: {
          display_name: "Анна",
          calories_eaten: 320,
          calories_target: 2100,
          pfc: { protein_g: 11, fat_g: 6, carbs_g: 54 },
        } as WellnessToday,
      });
      return inRouter("/customer/food-scanner/diary", "/customer/food-scanner/diary", <FoodScannerDiaryScreen />);
    },
  },
];

const fetchMock = vi.fn();
let created: string[];
let revoked: string[];

function photoCalls(): string[] {
  return fetchMock.mock.calls.map((c) => String(c[0])).filter((u) => u.includes("/diary/entry/"));
}

beforeEach(() => {
  vi.clearAllMocks();
  primeDisplayName("Тест Тестов");
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    if (url.includes("/diary/entry/") && url.endsWith("/photo")) {
      return new Response(new Uint8Array([0xff, 0xd8, 0xff]), {
        status: 200,
        headers: { "Content-Type": "image/jpeg" },
      });
    }
    return new Response(JSON.stringify({ error: "not_found", detail: "" }), { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  created = [];
  revoked = [];
  let n = 0;
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => {
    const u = `blob:ayla/${++n}`;
    created.push(u);
    return u;
  });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation((u: string) => {
    revoked.push(u);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetDiaryPhotoCacheForTests();
});


describe.each(SCREENS)("$name", (s) => {
  it("снимок есть → миниатюра из прокси, один запрос, слева в карточке", async () => {
    const { container } = s.render([WITH_PHOTO]);

    await waitFor(() => expect(container.querySelectorAll("img")).toHaveLength(1));
    const img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toMatch(/^blob:/);
    expect(img.classList.contains(DIARY_ENTRY_PHOTO_CLASS)).toBe(true);
    // Декоративная: блюдо названо текстом рядом.
    expect(img.getAttribute("alt")).toBe("");
    // Слева: место картинки — первый ребёнок карточки; значка там больше нет.
    const card = img.closest("li")!;
    expect(card.firstElementChild?.contains(img)).toBe(true);
    expect(iconCards(container)).toHaveLength(0);
    expect(photoCalls()).toEqual([PHOTO_PATH("fl-photo")]);
    const init = fetchMock.mock.calls.find((c) => String(c[0]).includes("/photo"))![1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe("MaxInitData test-init-data");
  });

  it("снимка нет → значок на месте картинки, запроса к прокси НЕТ", async () => {
    const { container, findByText } = s.render([WITHOUT_PHOTO]);

    await findByText("Борщ");
    await settleScenario();
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(photoCalls()).toEqual([]);
    // Решение владельца 12.11 п.1: у записи без снимка — значок, место занято.
    expect(iconCards(container)).toHaveLength(1);
  });

  it("чисел нет → миниатюра на месте, калорий нет (два пустых состояния независимы)", async () => {
    const { container, findByText } = s.render([NO_NUMBERS_WITH_PHOTO]);

    const dish = await findByText("Ризотто с трюфелем");
    await waitFor(() => expect(container.querySelectorAll("img")).toHaveLength(1));
    expect(dish.closest("li")?.textContent ?? "").not.toMatch(/ккал/);
  });

  it("прокси ответил 404 (снимок удалён по сроку) → строка без картинки", async () => {
    fetchMock.mockImplementation(
      async () => new Response(JSON.stringify({ error: "not_found", detail: "" }), { status: 404 }),
    );
    const { container, findByText } = s.render([WITH_PHOTO]);

    await findByText("Овсянка с ягодами");
    await waitFor(() => expect(photoCalls()).toHaveLength(1));
    await settleScenario();
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(created).toEqual([]);
    // has_photo=true, но снимка нет: картинка ставится по факту, не по сводке —
    // на её месте значок, а не пустая рамка.
    expect(iconCards(container)).toHaveLength(1);
  });

  it("смешанный день: картинка ровно у записи со снимком; уход освобождает всё", async () => {
    const { container, unmount } = s.render([WITH_PHOTO, WITHOUT_PHOTO, NO_NUMBERS_WITH_PHOTO]);

    await waitFor(() => expect(container.querySelectorAll("img")).toHaveLength(2));
    expect(iconCards(container)).toHaveLength(1);
    expect(photoCalls().sort()).toEqual([PHOTO_PATH("fl-photo"), PHOTO_PATH("fl-risotto")].sort());

    unmount();
    await settleScenario();
    expect(created.length).toBe(2);
    expect([...revoked].sort()).toEqual([...created].sort());
  });
});

/**
 * Карточка без снимка: место под картинку занято значком ДО и ПОСЛЕ загрузки
 * — карточка не прыгает. Прежний эталон `outerHTML` строки (решение 28.09
 * п.10) снят решением владельца 12.11 п.1 (DRF-2954): вид строки меняется
 * по доске «Дневник». Смысл, который эталон держал, — состав строки —
 * держат узлы выше и тесты экранов.
 */
describe.each(SCREENS)("$name — карточка без снимка", (s) => {
  it("место картинки — первый ребёнок, значок; название и время — текстом", async () => {
    const { findByText } = s.render([WITHOUT_PHOTO]);
    const dish = await findByText("Борщ");
    await settleScenario();
    const card = dish.closest("li")!;
    expect(card.classList.contains(DIARY_ENTRY_CLASS)).toBe(true);
    expect(card.firstElementChild?.classList.contains(DIARY_ENTRY_ICON_CLASS)).toBe(true);
    expect(card.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });
});
