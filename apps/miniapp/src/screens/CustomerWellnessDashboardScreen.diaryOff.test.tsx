/**
 * DRF-2071 — контур питания выключен (`NUTRITION_ENABLED=false`): на ЧТЕНИЕ
 * `wellness/today` сервер отвечает 200 с `nutrition_disabled: true`, именем и
 * целью — и без единого ключа дневника/воды (та же форма, что у
 * `consent_required`, DRF-1927). До этого листа дашборд получал дневник при
 * выключенном контуре; теперь экран обязан сказать «недоступен», а не
 * «попробуй через минуту» (повтор ничего не даст) и не пустой день — и при
 * этом НЕ потерять имя в приветствии и тройку кнопки цели (DRF-1476): они к
 * дневнику не относятся.
 *
 * Второй узел — входы в запись не рисуются: рядом с «недоступен» не должно
 * стоять «Стакан воды» и «Записать питание» (H01, DRF-2144). Остальные
 * быстрые действия (новая запись, профиль) — не питание и остаются.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, getCatalogBrowse: vi.fn() };
});
vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "",
  setBackButton: vi.fn(),
  signalReady: vi.fn(),
  applyTheme: vi.fn(),
  hapticImpact: vi.fn(),
}));

import { getCatalogBrowse } from "../lib/customer-booking";
import { DIARY_OFF_TEXT } from "../lib/customer-wellness";

const mockedBrowse = vi.mocked(getCatalogBrowse);

/** Ручка today отвечает так, как скажут; activity — обычно. */
function serveToday(today: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const u = String(url);
      if (u.includes("/wellness/today")) return today();
      if (u.includes("/recent-activity")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ this_week_booking_count: 0 }),
        } as unknown as Response;
      }
      throw new Error(`unexpected fetch: ${u}`);
    }),
  );
}

function refused(status: number, slug: string): Response {
  return {
    ok: false,
    status,
    statusText: "refused",
    json: async () => ({ error: slug, detail: "food diary is not enabled" }),
  } as unknown as Response;
}

function live(): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      calories_eaten: 1240,
      water_glasses_eaten: 4,
      active_goals: [],
      display_name: "Анна",
    }),
  } as unknown as Response;
}

/** Ответ сервера при OFF: маркер, имя, цель — и ни одного ключа дневника. */
function off(goals: unknown[] | undefined = [{ title: "Высыпаться", week_num: 1 }]): Response {
  const body: Record<string, unknown> = { nutrition_disabled: true, display_name: "Анна" };
  if (goals !== undefined) body.active_goals = goals;
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

async function renderScreen() {
  vi.resetModules();
  vi.stubEnv("DEV", false);
  const { CustomerWellnessDashboardScreen } = await import("./CustomerWellnessDashboardScreen");
  render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <CustomerWellnessDashboardScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [], picksOutcome: "OK" } as never);
  window.history.replaceState({}, "", "/customer/main");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("CustomerWellnessDashboardScreen — контур питания выключен (DRF-2071)", () => {
  it("сводка с nutrition_disabled → «недоступен» без повтора, не «через минуту» и не пустой день", async () => {
    serveToday(() => off());
    await renderScreen();

    expect(await screen.findByText(DIARY_OFF_TEXT)).toBeInTheDocument();
    // Живая область: экранный диктор услышит фразу, а не пустоту на месте чисел.
    expect(screen.getByRole("status", { name: "" }).textContent).toContain(DIARY_OFF_TEXT);
    expect(screen.queryByText(/через минуту/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Обновить" })).not.toBeInTheDocument();
    // Не пустой день: карточка первого шага требует ЗНАНИЯ о пустом дне.
    expect(screen.queryByText("Начнём с малого?")).not.toBeInTheDocument();
    // И не дневник: ни калорий, ни стаканов на экране.
    expect(screen.queryByText(/ккал/)).not.toBeInTheDocument();
  });

  it("имя и цель не теряются: приветствие с именем, карточка «Активная цель», не нейтральная «Цель»", async () => {
    serveToday(() => off());
    await renderScreen();

    await screen.findByText(DIARY_OFF_TEXT);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Анна");
    expect(screen.getByText("Активная цель")).toBeInTheDocument();
    expect(screen.queryByText("Выбери цель")).not.toBeInTheDocument();
  });

  it("цели нет — «Выбери цель», как при включённом контуре", async () => {
    serveToday(() => off([]));
    await renderScreen();

    await screen.findByText(DIARY_OFF_TEXT);
    expect(screen.getByRole("button", { name: "Выбери цель" })).toBeInTheDocument();
  });

  it("входы в запись не рисуются: ни «Стакан воды», ни «Записать питание»; каталог — на месте", async () => {
    serveToday(() => off());
    await renderScreen();

    await screen.findByText(DIARY_OFF_TEXT);
    expect(screen.queryByRole("button", { name: "Добавить стакан воды" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Записать питание" })).not.toBeInTheDocument();
    // Вход в каталог — «Новая запись» в быстрых действиях и «Записаться» под пустой записью.
    expect(screen.getByRole("button", { name: "Новая запись" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Записаться" })).toBeInTheDocument();
  });

  it("положительная стража: отказ ручки — по-прежнему «через минуту» с повтором и кнопками записи", async () => {
    serveToday(() => refused(422, "validation_error"));
    await renderScreen();

    expect(await screen.findByText(/через минуту/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Обновить" })).toBeInTheDocument();
    expect(screen.queryByText(DIARY_OFF_TEXT)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Добавить стакан воды" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Записать питание" })).toBeInTheDocument();
  });

  it("положительная стража: контур включён — дневник и кнопки записи как раньше", async () => {
    serveToday(live);
    await renderScreen();

    expect(await screen.findByRole("button", { name: "Записать питание" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Добавить стакан воды" })).toBeInTheDocument();
    expect(screen.queryByText(DIARY_OFF_TEXT)).not.toBeInTheDocument();
  });
});
