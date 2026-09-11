/**
 * Согласие на сканирование еды спрашивается у СЕРВЕРА (DRF-1564).
 *
 * # Что этот файл сторожит
 *
 * До 08.09.2026 согласие жило в `localStorage`. Колонка
 * `BotUser.food_scanner_consent_at` существовала с миграции `0013`, её
 * читал гейт навыка — а писателей у неё не было ни одного. Человек
 * давал согласие, экран его принимал и пропускал дальше, а бот на то же
 * согласие отвечал «открой Mini App и дай согласие». Каждый раз. На
 * новом устройстве всё начиналось заново.
 *
 * # Три состояния, и свести любые два нельзя
 *
 * `undefined` — ещё не спросили; ошибка — **не смогли** спросить;
 * `null` — согласия нет. Разница между вторым и третьим стоит дорого:
 * если их свести, сетевой сбой начнёт переспрашивать согласие у того,
 * кто его уже дал.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return {
    ...original,
    fetchConsentAt: vi.fn(),
    grantConsent: vi.fn(),
  };
});

import { fetchConsentAt, grantConsent } from "../lib/food-scanner";
import { FoodScannerCaptureScreen } from "./FoodScannerCaptureScreen";

const mockedFetch = vi.mocked(fetchConsentAt);
const mockedGrant = vi.mocked(grantConsent);

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/food-scanner/capture"]}>
      <Routes>
        <Route
          path="/customer/food-scanner/capture"
          element={<FoodScannerCaptureScreen />}
        />
        <Route path="/customer/main" element={<div>ДОМ</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("источник правды — сервер, а не браузер", () => {
  it("согласие есть на сервере — гейт не показывается", async () => {
    mockedFetch.mockResolvedValue("2026-09-08T10:00:00+00:00");
    renderScreen();

    // Присутствие: экран съёмки открылся…
    expect(await screen.findByRole("heading", { name: "Что ешь сейчас?" })).toBeInTheDocument();
    // …отсутствие: согласие не переспрашивают у того, кто его дал.
    expect(screen.queryByRole("button", { name: /разреш/i })).not.toBeInTheDocument();
  });

  it("пустой localStorage согласия НЕ отменяет", async () => {
    // Тот самый случай, ради которого источник сменили: человек дал
    // согласие с другого устройства. Браузер о нём не знает — и это
    // больше не имеет значения.
    window.localStorage.clear();
    mockedFetch.mockResolvedValue("2026-09-08T10:00:00+00:00");
    renderScreen();

    expect(await screen.findByRole("heading", { name: "Что ешь сейчас?" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /разреш/i })).not.toBeInTheDocument();
  });

  it("согласия нет — гейт показывается, и согласие уходит НА СЕРВЕР", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue(null);
    mockedGrant.mockResolvedValue("2026-09-08T12:00:00+00:00");
    renderScreen();

    const accept = await screen.findByRole("button", { name: /разреш/i });
    await user.click(accept);

    // Согласие ушло ручкой — а не осело в браузере.
    await waitFor(() => expect(mockedGrant).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("heading", { name: "Что ешь сейчас?" })).toBeInTheDocument();
    // Отсутствие: в localStorage ничего не записалось — авторитета там
    // больше нет, и «оптимистичной отметки», переживающей перезагрузку,
    // мы не завели.
    expect(window.localStorage.getItem("ayla.food_scanner_consent_at")).toBeNull();
  });
});

describe("«не смогли спросить» ≠ «согласия нет»", () => {
  it("отказ чтения не показывает гейт и не переспрашивает согласие", async () => {
    mockedFetch.mockRejectedValue(new Error("[502] upstream"));
    renderScreen();

    // Присутствие: экран сказал про неудачу и дал повтор…
    expect(await screen.findByRole("button", { name: /Повторить|снова/i })).toBeInTheDocument();
    // …отсутствие: гейта согласия нет. Сетевой сбой не имеет права
    // требовать согласие у того, кто, возможно, его уже дал.
    expect(screen.queryByRole("button", { name: /разреш/i })).not.toBeInTheDocument();
    // И камеры тоже нет: неизвестное состояние не пропускает дальше.
    expect(screen.queryByRole("heading", { name: "Что ешь сейчас?" })).not.toBeInTheDocument();
  });

  it("пока ответ не пришёл — ни гейта, ни камеры", async () => {
    let release: (v: string | null) => void = () => {};
    mockedFetch.mockReturnValue(
      new Promise<string | null>((resolve) => {
        release = resolve;
      }),
    );
    renderScreen();

    // Ожидание — своё состояние: показать гейт заранее значило бы
    // спросить согласие у того, у кого оно, возможно, есть.
    expect(screen.queryByRole("button", { name: /разреш/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Что ешь сейчас?" })).not.toBeInTheDocument();

    release("2026-09-08T10:00:00+00:00");
    expect(await screen.findByRole("heading", { name: "Что ешь сейчас?" })).toBeInTheDocument();
  });

  it("сбой ЗАПИСИ не выдаёт согласие за данное", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue(null);
    mockedGrant.mockRejectedValue(new Error("[500] boom"));
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /разреш/i }));

    // Присутствие: экран сообщил о неудаче…
    expect(await screen.findByRole("button", { name: /Повторить|снова/i })).toBeInTheDocument();
    // …отсутствие: камеру не открыли. Пропустить человека дальше на
    // незаписанном согласии значило бы получить ровно ту петлю, которую
    // эта задача закрывает, — только с обратной стороны.
    expect(screen.queryByRole("heading", { name: "Что ешь сейчас?" })).not.toBeInTheDocument();
  });
});
