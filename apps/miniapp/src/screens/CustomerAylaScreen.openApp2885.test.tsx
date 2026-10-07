/**
 * DRF-2885 (часть Б) — кнопка бота «открыть Mini App» в чате Mini App.
 *
 * В чате MAX такая кнопка (`open_app`) открывает Mini App на нужном экране.
 * В чате Mini App человек уже внутри, и до этой правки сервер такие кнопки
 * отбрасывал: «Мой план» под карточкой плана в MAX был, а здесь — нет.
 * Теперь это переход внутри приложения; куда ведёт слаг, решает одна карта
 * маршрутов (`parseStartRoute`), и незнакомый слаг кнопку не рисует.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/max-sdk")>();
  return {
    ...actual,
    getInitData: () => "",
    setBackButton: vi.fn(),
    onBackButton: vi.fn(() => () => undefined),
    signalReady: vi.fn(),
    hapticImpact: vi.fn(),
    hapticSelection: vi.fn(),
    openExternalLink: vi.fn(),
  };
});

import { AylaChat, quickReplyRoute, type AylaChatApi } from "../components/AylaChat";
import { openExternalLink } from "../lib/max-sdk";
import { CustomerAylaScreen } from "./CustomerAylaScreen";

function ok(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>;
}

type Button = { label: string; payload?: string; url?: string; open_app?: string };

function stubTurn(buttons: Button[]): Record<string, unknown>[] {
  const asked: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: RequestInit) => {
      const u = String(url);
      if (u.includes("/assistant/history")) return ok({ messages: [] });
      if (u.includes("/assistant/ask")) {
        asked.push(JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>);
        return ok({ answer: "Вот твой план.", buttons, pending_action: null, cards: [] });
      }
      throw new Error(`unexpected fetch: ${u}`);
    }),
  );
  return asked;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/customer/ayla"]}>
      <Routes>
        <Route path="/customer/ayla" element={<CustomerAylaScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function ask(text: string) {
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: text } });
  fireEvent.submit(screen.getByRole("textbox").closest("form") as HTMLFormElement);
  await screen.findByText("Вот твой план.");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(openExternalLink).mockClear();
});

describe("кнопка open_app в чате Mini App", () => {
  it("«Мой план» ведёт на экран плана внутри Mini App — без нового хода и без внешней ссылки", async () => {
    const asked = stubTurn([{ label: "Мой план", open_app: "open_plan" }]);
    renderScreen();
    await ask("покажи план");

    fireEvent.click(screen.getByRole("button", { name: "Мой план" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/customer/plan"));
    expect(asked).toHaveLength(1);
    expect(openExternalLink).not.toHaveBeenCalled();
  });

  it("незнакомый слаг кнопку не рисует, остальные кнопки ответа на месте", async () => {
    stubTurn([
      { label: "Куда-то", open_app: "open_nowhere_such" },
      { label: "📅 Записаться", payload: "cb:welcome:book" },
    ]);
    renderScreen();
    await ask("привет");

    expect(screen.getByRole("button", { name: "📅 Записаться" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Куда-то" })).toBeNull();
  });

  it("единственная кнопка с незнакомым слагом — ряда кнопок нет вовсе", async () => {
    stubTurn([{ label: "Куда-то", open_app: "open_nowhere_such" }]);
    renderScreen();
    await ask("привет");

    // Положительная пара: ответ на экране, а пустого списка под ним нет.
    expect(screen.getByText("Вот твой план.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Варианты ответа" })).toBeNull();
  });
});

describe("quickReplyRoute — одна карта маршрутов с кнопками чата бота", () => {
  it("знакомые слаги ведут туда же, куда кнопка в MAX", () => {
    expect(quickReplyRoute({ label: "x", open_app: "open_plan" })).toBe("/customer/plan");
    expect(quickReplyRoute({ label: "x", open_app: "open_catalog" })).toBe("/customer/catalog");
  });

  it("незнакомый слаг и кнопка другого вида — некуда", () => {
    expect(quickReplyRoute({ label: "x", open_app: "open_nowhere_such" })).toBeNull();
    expect(quickReplyRoute({ label: "x", payload: "cb:welcome:book" })).toBeNull();
  });
});

describe("экран без двери `onOpen` кнопку перехода не рисует", () => {
  it("стафф-чат: open_app без обработчика не появляется, callback остаётся", async () => {
    const api: AylaChatApi = {
      history: async () => ({ messages: [] }),
      ask: async () => ({
        answer: "Вот твой план.",
        buttons: [
          { label: "Мой план", open_app: "open_plan" },
          { label: "Да", payload: "cb:yes" },
        ],
        pending_action: null,
      }),
      confirm: async () => {
        throw new Error("not used");
      },
    } as unknown as AylaChatApi;
    render(
      <MemoryRouter>
        <AylaChat api={api} greeting="Привет" />
      </MemoryRouter>,
    );
    await ask("привет");

    expect(screen.getByRole("button", { name: "Да" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Мой план" })).toBeNull();
  });
});
