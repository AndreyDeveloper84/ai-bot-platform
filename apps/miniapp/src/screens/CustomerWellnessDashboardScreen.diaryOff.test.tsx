/**
 * DRF-2071 — контур питания выключен (`NUTRITION_ENABLED=false`): сервер
 * отвечает 404 `nutrition_disabled` на ЧТЕНИЕ `wellness/today`, как и на
 * запись. До этого листа дашборд получал 200 с дневником при выключенном
 * контуре; после — 404, и экран обязан сказать «недоступен», а не
 * «попробуй через минуту» (повтор ничего не даст) и не пустой день.
 *
 * Второй узел — входы в запись не рисуются: рядом с «недоступен» не должно
 * стоять «+ стакан» и «Дневник питания». Остальные быстрые действия (цель,
 * каталог) — не питание и остаются.
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
  it("404 nutrition_disabled → «недоступен» без повтора, не «через минуту»", async () => {
    serveToday(() => refused(404, "nutrition_disabled"));
    await renderScreen();

    expect(await screen.findByText(DIARY_OFF_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(/через минуту/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Обновить" })).not.toBeInTheDocument();
    // Не пустой день: карточка первого шага требует ЗНАНИЯ о пустом дне.
    expect(screen.queryByText("Начнём с малого?")).not.toBeInTheDocument();
  });

  it("входы в запись не рисуются: ни «+ стакан», ни «Дневник питания»; цель и каталог — на месте", async () => {
    serveToday(() => refused(404, "nutrition_disabled"));
    await renderScreen();

    await screen.findByText(DIARY_OFF_TEXT);
    expect(
      screen.queryByRole("button", { name: "Добавить стакан воды 250 мл" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Дневник питания" })).not.toBeInTheDocument();
    // «Найди услугу» есть и в быстрых действиях, и под пустой записью.
    expect(screen.getAllByRole("button", { name: "Найди услугу" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Цель|Выбери цель|Моя цель/ })).toBeInTheDocument();
  });

  it("положительная стража: прочий 4xx — по-прежнему «через минуту» с повтором и кнопками записи", async () => {
    serveToday(() => refused(422, "validation_error"));
    await renderScreen();

    expect(await screen.findByText(/через минуту/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Обновить" })).toBeInTheDocument();
    expect(screen.queryByText(DIARY_OFF_TEXT)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Добавить стакан воды 250 мл" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Дневник питания" })).toBeInTheDocument();
  });

  it("положительная стража: контур включён — дневник и кнопки записи как раньше", async () => {
    serveToday(live);
    await renderScreen();

    expect(await screen.findByRole("button", { name: "Дневник питания" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Добавить стакан воды 250 мл" })).toBeInTheDocument();
    expect(screen.queryByText(DIARY_OFF_TEXT)).not.toBeInTheDocument();
  });
});
