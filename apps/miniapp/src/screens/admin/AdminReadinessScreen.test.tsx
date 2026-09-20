/**
 * `AdminReadinessScreen` (DRF-2117) — список проблем готовности поимённо.
 *
 * Тексты строк — `problems[].text` дословно: формулировки живут на сервере
 * (`salon_readiness.TEXTS`), экран их не переписывает. Отказ источника —
 * одна строка о салоне, не пустой список и не «готов».
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return { ...original, getSalonReadiness: vi.fn() };
});

import { getSalonReadiness, type SalonReadinessResponse } from "../../lib/admin-api";
import { ApiError } from "../../lib/api";
import { AdminReadinessScreen, RECHECK_MIN_INTERVAL_MS } from "./AdminReadinessScreen";

const mocked = vi.mocked(getSalonReadiness);

function doc(over: Partial<SalonReadinessResponse> = {}): SalonReadinessResponse {
  return {
    ready: false,
    unknown: false,
    source_problem: null,
    checked_at: "2026-09-20T09:00:00+00:00",
    masters_total: 3,
    problems: [
      {
        master: { id: "m-1", name: "Анна" },
        code: "schedule_missing",
        text: "Анна — не настроен график",
        origin: "catalog",
      },
      {
        master: { id: "m-2", name: "Иван" },
        code: "catalog_unlinked",
        text: "Иван — не связана с каталогом",
        origin: "mirror",
      },
      {
        master: { id: null, name: "" },
        code: "no_masters",
        text: "В салоне нет ни одного мастера",
        origin: "catalog",
      },
    ],
    limits: ["Мастер из приглашения и из синхронизации считаются отдельно"],
    ...over,
  };
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/admin/readiness"]}>
      <AdminReadinessScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // Блоком, не выражением: `() => mocked.mockReset()` вернул бы сам мок, а
  // функцию, возвращённую из beforeEach, vitest зовёт как cleanup после теста —
  // и мок с отклонённым промисом «срабатывал» вне экрана.
  mocked.mockReset();
});

describe("AdminReadinessScreen", () => {
  it("список проблем — тексты сервера дословно, заголовок «пока не готов», сноска пределов", async () => {
    mocked.mockResolvedValue(doc());
    renderScreen();

    expect(await screen.findByText("Салон пока не готов:")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: /готовност/i });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      "Анна — не настроен график",
      "Иван — не связана с каталогом",
      "В салоне нет ни одного мастера",
    ]);
    expect(
      screen.getByText("Мастер из приглашения и из синхронизации считаются отдельно"),
    ).toBeInTheDocument();
  });

  it("готов — одна строка «Салон готов принимать записи.», списка нет", async () => {
    mocked.mockResolvedValue(doc({ ready: true, problems: [], limits: [] }));
    renderScreen();

    expect(await screen.findByText("Салон готов принимать записи.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: /готовност/i })).toBeNull();
  });

  it("отказ источника — строка о салоне, не «готов» и не пустой список", async () => {
    mocked.mockResolvedValue(
      doc({
        unknown: true,
        source_problem: "source_unavailable",
        problems: [
          {
            master: { id: null, name: "" },
            code: "source_unavailable",
            text: "Не удалось проверить готовность: каталог не ответил. Попробуйте ещё раз.",
            origin: "source",
          },
        ],
        limits: [],
      }),
    );
    renderScreen();

    expect(
      await screen.findByText(
        "Не удалось проверить готовность: каталог не ответил. Попробуйте ещё раз.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Салон готов принимать записи.")).toBeNull();
    expect(screen.queryByText("Салон пока не готов:")).toBeNull();
  });

  it("«Проверить снова» — не чаще раза в 10 с, с подписью «обновлено HH:MM»", async () => {
    mocked.mockResolvedValueOnce(doc()).mockResolvedValueOnce(doc({ ready: true, problems: [] }));
    const realNow = Date.now;
    const t0 = realNow();
    const nowSpy = vi.spyOn(Date, "now").mockImplementation(() => t0);
    try {
      renderScreen();
      await screen.findByText("Салон пока не готов:");
      // checked_at 09:00Z → в часах зрителя; проверяем форму «обновлено ЧЧ:ММ».
      expect(screen.getByText(/^обновлено \d{2}:\d{2}$/)).toBeInTheDocument();

      // Сразу после загрузки — повтор в кулдауне, ручка не зовётся.
      await userEvent.click(screen.getByRole("button", { name: "Проверить снова" }));
      expect(mocked).toHaveBeenCalledTimes(1);

      // Через 10 с — перечитывает.
      nowSpy.mockImplementation(() => t0 + RECHECK_MIN_INTERVAL_MS + 1);
      await userEvent.click(screen.getByRole("button", { name: "Проверить снова" }));
      expect(await screen.findByText("Салон готов принимать записи.")).toBeInTheDocument();
      expect(mocked).toHaveBeenCalledTimes(2);
    } finally {
      nowSpy.mockRestore();
    }
  });

  it("сбой ручки — StateError с повтором, не «готов»", async () => {
    mocked.mockRejectedValue(new ApiError(500, "server_error", "x"));
    renderScreen();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Салон готов принимать записи.")).toBeNull();
  });
});
