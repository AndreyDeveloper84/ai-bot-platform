/**
 * DRF-2455 — миниатюра снимка в строке дневника, на ОБОИХ экранах, одинаково.
 *
 * Записи рисуют два экрана: день (`FoodScannerDayScreen`, `/diary/day`) и
 * главная дневника (`FoodScannerDiaryScreen`, `/wellness/today`). Поле,
 * поднятое в одном клиенте и потерянное в другом, у нас уже было — поэтому
 * каждый узел гоняется по обоим экранам одним и тем же набором.
 *
 * Три состояния строки (решения владельца 28.09 п.14 и главного окна 26.09):
 *  * снимок есть → миниатюра, источник — только прокси бота;
 *  * снимка нет (`has_photo` не `true`, или прокси ответил 404 — удалён по
 *    сроку) → обычная текстовая строка: ни картинки, ни заглушки, ни текста;
 *    при `has_photo` не `true` запроса к прокси НЕТ;
 *  * чисел нет (`calories: null`) → миниатюра на месте, калорий нет — два
 *    пустых состояния независимы.
 */
import { act, render, waitFor } from "@testing-library/react";
import { StrictMode, type ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { primeDisplayName } from "../components/CustomerAvatarEntry";

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

const PHOTO_PATH = (id: string) => `/api/v1/customer/diary/entry/${id}/photo`;
const WITH_THUMB = "food-scanner-diary__entry--with-thumb";

/** Строки дневника с модификатором «со снимком». */
function thumbRows(container: HTMLElement): Element[] {
  return Array.from(container.querySelectorAll("li")).filter((li) => li.classList.contains(WITH_THUMB));
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

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe.each(SCREENS)("$name", (s) => {
  it("снимок есть → миниатюра из прокси, один запрос, в строке первой", async () => {
    const { container } = s.render([WITH_PHOTO]);

    await waitFor(() => expect(container.querySelectorAll("img")).toHaveLength(1));
    const img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toMatch(/^blob:/);
    // Класс по макету — от окна mini (DRF-2455, #2118).
    expect(img.className).toBe("food-scanner-diary__entry-thumb");
    // Модификатор строки — по факту картинки: без него данные уходят под снимок.
    expect(img.closest("li")?.classList.contains(WITH_THUMB)).toBe(true);
    // Слева: первый ребёнок строки.
    expect(img.closest("li")?.firstElementChild).toBe(img);
    expect(photoCalls()).toEqual([PHOTO_PATH("fl-photo")]);
    const init = fetchMock.mock.calls.find((c) => String(c[0]).includes("/photo"))![1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe("MaxInitData test-init-data");
  });

  it("снимка нет → обычная текстовая строка, запроса к прокси НЕТ", async () => {
    const { container, findByText } = s.render([WITHOUT_PHOTO]);

    await findByText("Борщ");
    await flush();
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(photoCalls()).toEqual([]);
    // Строка без снимка — прежняя: ни картинки, ни модификатора.
    expect(thumbRows(container)).toHaveLength(0);
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
    await flush();
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(created).toEqual([]);
    // has_photo=true, но снимка нет: модификатор ставится по факту, не по сводке —
    // иначе пустое место слева и сдвинутый текст без причины.
    expect(thumbRows(container)).toHaveLength(0);
  });

  it("смешанный день: картинка ровно у записи со снимком; уход освобождает всё", async () => {
    const { container, unmount } = s.render([WITH_PHOTO, WITHOUT_PHOTO, NO_NUMBERS_WITH_PHOTO]);

    await waitFor(() => expect(container.querySelectorAll("img")).toHaveLength(2));
    expect(thumbRows(container)).toHaveLength(2);
    expect(photoCalls().sort()).toEqual([PHOTO_PATH("fl-photo"), PHOTO_PATH("fl-risotto")].sort());

    unmount();
    await flush();
    expect(created.length).toBe(2);
    expect([...revoked].sort()).toEqual([...created].sort());
  });
});

/**
 * Строка БЕЗ фото — прежняя целиком, а не «без двух признаков».
 *
 * `<li>` теперь рождает `DiaryEntryItem`, а не экран: это рефакторинг, и
 * решение владельца п.10 требует, чтобы принятый вид им не менялся. Эталон
 * снят РЕНДЕРОМ `dev` `ebfe80ce` (экраны до DRF-2455) и заморожен литералом;
 * сравнивается `outerHTML` строки целиком — классы, атрибуты, порядок и
 * вложенность детей. Текст времени заменён меткой: он зависит от часового
 * пояса машины (09:05Z → «12:05» при UTC+3, «09:05» в CI).
 */
const ROW_WITHOUT_PHOTO_AT_DEV_EBFE80CE: Record<string, string> = {
  "день (FoodScannerDayScreen)": "<li class=\"food-scanner-diary__entry\"><div class=\"food-scanner-diary__entry-main\"><span class=\"food-scanner-diary__entry-time\"><TIME></span><span class=\"food-scanner-diary__entry-dish\">Борщ</span></div></li>",
  "главная дневника (FoodScannerDiaryScreen)": "<li class=\"food-scanner-diary__entry\"><div class=\"food-scanner-diary__entry-main\"><span class=\"food-scanner-diary__entry-time\"><TIME></span><span class=\"food-scanner-diary__entry-dish\">Борщ</span></div><span class=\"food-scanner-diary__entry-cal\">~320 ккал</span><div class=\"food-scanner-diary__entry-actions\"><button type=\"button\" class=\"food-scanner-diary__entry-action\" aria-label=\"В избранное: Борщ\">В избранное</button><button type=\"button\" class=\"food-scanner-diary__entry-action\" aria-label=\"Удалить: Борщ\">Удалить</button></div></li>"
};

function normalizedRow(li: Element): string {
  return li.outerHTML.replace(/(food-scanner-diary__entry-time">)[^<]*/, "$1<TIME>");
}

describe.each(SCREENS)("$name — строка без фото прежняя целиком", (s) => {
  it("outerHTML строки без снимка равен рендеру dev ebfe80ce", async () => {
    const { findByText } = s.render([WITHOUT_PHOTO]);
    const dish = await findByText("Борщ");
    await flush();
    expect(normalizedRow(dish.closest("li")!)).toBe(ROW_WITHOUT_PHOTO_AT_DEV_EBFE80CE[s.name]);
  });
});
