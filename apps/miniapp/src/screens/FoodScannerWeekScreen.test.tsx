/**
 * Дневник за неделю — экран (DRF-2099).
 *
 * Предмет: «N из 7 дней с записями» как факт, строка на каждый день,
 * числа под ED-признаком, стрелки «раньше/позже» в пределах 28 дней с
 * НЕАКТИВНОЙ кнопкой на пределе (не ошибкой), нажатие дня ведёт на экран
 * дня, отказы — по слагу. Слов «напоминание», «серия», «пропустил» на
 * экране нет — сторож словами (В-5, DRF-1332).
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/diary-days", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/diary-days")>();
  return { ...original, getDiaryDays: vi.fn() };
});

import { ApiError } from "../lib/api";
import { getDiaryDays, type DiaryDays } from "../lib/diary-days";
import { FoodScannerWeekScreen, WEEK_COPY, WEEK_ROUTE } from "./FoodScannerWeekScreen";

const mockedDays = vi.mocked(getDiaryDays);

function week(from: string, to: string, overrides: Partial<DiaryDays> = {}): DiaryDays {
  const days: DiaryDays["days"] = [];
  const start = Date.UTC(
    Number(from.slice(0, 4)),
    Number(from.slice(5, 7)) - 1,
    Number(from.slice(8, 10)),
  );
  for (let i = 0; i < 7; i += 1) {
    const date = new Date(start + i * 86_400_000).toISOString().slice(0, 10);
    days.push({ date, meals_count: 0, kcal: null, has_entries: false });
  }
  return { timezone: "Europe/Moscow", from, to, days, nutrition_numbers_hidden: false, ...overrides };
}

const TODAY_WEEK = week("2026-09-13", "2026-09-19", {
  days: [
    { date: "2026-09-13", meals_count: 0, kcal: null, has_entries: false },
    { date: "2026-09-14", meals_count: 2, kcal: 640, has_entries: true },
    { date: "2026-09-15", meals_count: 0, kcal: null, has_entries: false },
    { date: "2026-09-16", meals_count: 1, kcal: 320, has_entries: true },
    { date: "2026-09-17", meals_count: 0, kcal: null, has_entries: false },
    { date: "2026-09-18", meals_count: 3, kcal: 1500.4, has_entries: true },
    { date: "2026-09-19", meals_count: 0, kcal: null, has_entries: false },
  ],
});

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={[WEEK_ROUTE]}>
      <Routes>
        <Route path={WEEK_ROUTE} element={<FoodScannerWeekScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedDays.mockResolvedValue(TODAY_WEEK);
});

describe("неделя: факт, не оценка", () => {
  it("первая загрузка — без периода; «3 из 7 дней с записями» и строка на каждый день", async () => {
    renderScreen();
    await screen.findByText(WEEK_COPY.summary(3));

    expect(mockedDays).toHaveBeenCalledWith(undefined, undefined);
    const rows = screen.getAllByRole("button", { name: /^(Пн|Вт|Ср|Чт|Пт|Сб|Вс), \d\d\.\d\d/ });
    expect(rows).toHaveLength(7);
    expect(screen.getByText(WEEK_COPY.meals(2, 640))).toBeInTheDocument();
    expect(screen.getByText(WEEK_COPY.meals(3, 1500.4))).toBeInTheDocument();
    // пустые дни присутствуют строкой с прочерком, а не пропадают
    expect(screen.getAllByText(WEEK_COPY.none)).toHaveLength(4);
  });

  it("«0 из 7» — без напоминаний, серий и упрёков: сторож словами", async () => {
    mockedDays.mockResolvedValue(week("2026-09-13", "2026-09-19"));
    renderScreen();
    await screen.findByText(WEEK_COPY.summary(0));

    const text = document.body.textContent ?? "";
    expect(text).toContain(WEEK_COPY.summary(0));
    for (const word of ["напомин", "серия", "серии", "streak", "пропуст", "молодец", "стыд"]) {
      expect(text.toLowerCase()).not.toContain(word);
    }
  });

  it("под ED-признаком калорий нет, число приёмов остаётся", async () => {
    mockedDays.mockResolvedValue({ ...TODAY_WEEK, nutrition_numbers_hidden: true });
    renderScreen();
    await screen.findByText(WEEK_COPY.summary(3));

    expect(screen.getByText(WEEK_COPY.mealsOnly(2))).toBeInTheDocument();
    expect(screen.queryByText(/ккал/)).toBeNull();
  });

  it("признака нет в ответе — числа спрятаны (fail-closed)", async () => {
    const { nutrition_numbers_hidden: _omit, ...withoutFlag } = TODAY_WEEK;
    mockedDays.mockResolvedValue(withoutFlag);
    renderScreen();
    await screen.findByText(WEEK_COPY.summary(3));

    expect(screen.getByText(WEEK_COPY.mealsOnly(2))).toBeInTheDocument();
    expect(screen.queryByText(/ккал/)).toBeNull();
  });

  it("нажатие дня ведёт на экран дня с этой датой", async () => {
    renderScreen();
    await screen.findByText(WEEK_COPY.summary(3));

    fireEvent.click(screen.getByRole("button", { name: /^Пн, 14\.09/ }));

    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/customer/food-scanner/day/2026-09-14",
    );
  });
});

describe("неделя: стрелки", () => {
  it("«раньше» просит предыдущую неделю; «позже» на текущей неактивна", async () => {
    renderScreen();
    await screen.findByText(WEEK_COPY.summary(3));

    const later = screen.getByRole("button", { name: WEEK_COPY.later });
    expect(later).toBeDisabled();

    mockedDays.mockResolvedValueOnce(week("2026-09-06", "2026-09-12"));
    fireEvent.click(screen.getByRole("button", { name: WEEK_COPY.earlier }));
    await settle();

    expect(mockedDays).toHaveBeenLastCalledWith("2026-09-06", "2026-09-12");
    expect(screen.getByRole("button", { name: WEEK_COPY.later })).toBeEnabled();
  });

  it("на четвёртой неделе назад «раньше» неактивна — предел, не ошибка", async () => {
    renderScreen();
    await screen.findByText(WEEK_COPY.summary(3));

    const steps: Array<[string, string]> = [
      ["2026-09-06", "2026-09-12"],
      ["2026-08-30", "2026-09-05"],
      ["2026-08-23", "2026-08-29"],
    ];
    for (const [from, to] of steps) {
      mockedDays.mockResolvedValueOnce(week(from, to));
      const earlier = screen.getByRole("button", { name: WEEK_COPY.earlier });
      expect(earlier).toBeEnabled();
      fireEvent.click(earlier);
      await settle();
    }

    expect(mockedDays).toHaveBeenLastCalledWith("2026-08-23", "2026-08-29");
    expect(screen.getByRole("button", { name: WEEK_COPY.earlier })).toBeDisabled();
    expect(screen.queryByRole("status", { name: /ошиб/i })).toBeNull();
    // ровно четыре запроса: первая неделя + три шага назад, лишнего не ушло
    expect(mockedDays).toHaveBeenCalledTimes(4);
  });
});

describe("неделя: отказы", () => {
  it("consent_required — на ворота согласия с возвратом сюда", async () => {
    mockedDays.mockRejectedValue(new ApiError(403, "consent_required", "consent"));
    renderScreen();

    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/customer/food-scanner/capture",
    );
  });

  it("nutrition_disabled — «дневник недоступен» без повтора", async () => {
    mockedDays.mockRejectedValue(new ApiError(404, "nutrition_disabled", "off"));
    renderScreen();

    expect(await screen.findByText(WEEK_COPY.diaryOff)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: WEEK_COPY.retry })).toBeNull();
  });

  it("ayla_unavailable — фраза и «Повторить»", async () => {
    mockedDays.mockRejectedValueOnce(new ApiError(502, "ayla_unavailable", "down"));
    renderScreen();

    expect(await screen.findByText(WEEK_COPY.unavailable)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: WEEK_COPY.retry }));
    await screen.findByText(WEEK_COPY.summary(3));
    expect(mockedDays).toHaveBeenCalledTimes(2);
  });
});
