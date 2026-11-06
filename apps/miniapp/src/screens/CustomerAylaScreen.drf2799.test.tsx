/**
 * DRF-2799 — разговор с Ayla внутри Mini App (решение владельца 06.10.2026:
 * «диалог продолжается там, где начат»; замещает Д2 §172 / DRF-2266 для клиента).
 *
 * * обе кнопки блока «Продолжить разговор с Ayla» на Главной ведут в
 *   `/customer/ayla` и НЕ закрывают Mini App (`returnToChat` не зовётся);
 * * экран берёт историю и задаёт вопрос клиентскими ручками;
 * * быстрые ответы: кнопка под ответом шлёт свой payload тем же `ask`, а на
 *   экране остаётся подписью; кнопки — только под последним ответом.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "",
  setBackButton: vi.fn(),
  onBackButton: vi.fn(() => () => undefined),
  signalReady: vi.fn(),
  applyTheme: vi.fn(),
  hapticImpact: vi.fn(),
  hapticSelection: vi.fn(),
  openExternalLink: vi.fn(),
  closeApp: vi.fn(),
  returnToChat: vi.fn(() => "closed"),
  rememberChatLink: vi.fn(),
}));

import { returnToChat } from "../lib/max-sdk";
import { CustomerAylaScreen } from "./CustomerAylaScreen";
import { CustomerWellnessDashboardScreen } from "./CustomerWellnessDashboardScreen";

function ok(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Главная · «Продолжить разговор с Ayla»", () => {
  beforeEach(() => {
    vi.mocked(returnToChat).mockClear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        if (u.includes("/wellness/today")) return ok({ active_goals: [] });
        if (u.includes("/recent-activity")) return ok({ this_week_booking_count: 0 });
        if (u.includes("/plan-lite")) return ok({ plan_lite: null });
        if (u.includes("/last-topic")) return ok({ last_topic: null });
        if (u.includes("/catalog")) return ok({ services: [], masters: [], picks: [] });
        return ok({});
      }),
    );
  });

  it.each(["Продолжить разговор", "Задать новый вопрос"])(
    "«%s» открывает разговор в приложении и не закрывает его",
    async (label) => {
      render(
        <MemoryRouter initialEntries={["/customer/main"]}>
          <Routes>
            <Route path="/customer/main" element={<CustomerWellnessDashboardScreen />} />
            <Route path="*" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>,
      );

      fireEvent.click(await screen.findByRole("button", { name: label }));

      expect(await screen.findByTestId("location")).toHaveTextContent("/customer/ayla");
      expect(returnToChat).not.toHaveBeenCalled();
    },
  );
});

describe("Экран разговора с Ayla", () => {
  let asked: Array<Record<string, unknown>>;

  beforeEach(() => {
    asked = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown, init?: RequestInit) => {
        const u = String(url);
        if (u.includes("/assistant/history")) {
          return ok({
            messages: [
              { id: "m1", role: "user", content: "привет", tool: "", created_at: "" },
              { id: "m2", role: "assistant", content: "Привет! Чем помочь?", tool: "", created_at: "" },
            ],
          });
        }
        if (u.includes("/assistant/ask")) {
          const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
          asked.push(body);
          if (asked.length === 1) {
            return ok({
              answer: "Записать тебя?",
              buttons: [
                { label: "📅 Записаться", payload: "cb:welcome:book" },
                { label: "Сайт салона", url: "https://example.org" },
              ],
              pending_action: null,
              cards: [],
            });
          }
          return ok({ answer: "Выбери время.", buttons: [], pending_action: null, cards: [] });
        }
        throw new Error(`unexpected fetch: ${u}`);
      }),
    );
  });

  function renderScreen() {
    return render(
      <MemoryRouter initialEntries={["/customer/ayla"]}>
        <Routes>
          <Route path="/customer/ayla" element={<CustomerAylaScreen />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("история — с сервера, общая нить с чатом бота", async () => {
    renderScreen();
    expect(await screen.findByText("Привет! Чем помочь?")).toBeInTheDocument();
    expect(screen.getByText("привет")).toBeInTheDocument();
  });

  it("быстрый ответ уходит payload'ом, а на экране — подписью; кнопки только под последним", async () => {
    renderScreen();
    await screen.findByText("Привет! Чем помочь?");

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "хочу на маникюр" } });
    fireEvent.submit(screen.getByRole("textbox").closest("form") as HTMLFormElement);
    const book = await screen.findByRole("button", { name: "📅 Записаться" });
    expect(asked[0]).toMatchObject({ text: "хочу на маникюр" });
    expect(typeof asked[0]?.request_id).toBe("string");

    fireEvent.click(book);

    await waitFor(() => expect(asked).toHaveLength(2));
    expect(asked[1]).toMatchObject({ text: "cb:welcome:book" });
    expect(await screen.findByText("Выбери время.")).toBeInTheDocument();
    // На экране — подпись кнопки, не payload.
    expect(screen.queryByText("cb:welcome:book")).not.toBeInTheDocument();
    expect(screen.getAllByText("📅 Записаться").length).toBeGreaterThan(0);
    // Кнопки прошлого ответа больше не жмутся.
    expect(screen.queryByRole("button", { name: "Сайт салона" })).not.toBeInTheDocument();
  });
});
