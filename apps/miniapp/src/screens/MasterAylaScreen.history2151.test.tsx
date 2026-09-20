/**
 * «Ayla» мастера — на экране только ходы ассистента (DRF-2151, М-0).
 *
 * Второй слой поверх фильтра бэкенда: даже если история пришла со старого
 * бэкенда (или из кэша), команда «/start …» и токены приглашений
 * (`master_invite_…`, `inv_…`) не рисуются никогда. Ложный вход — подсадка
 * «/start inv_X» → 0 вхождений на экране; пустой остаток → приглашение.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getAylaHistory: vi.fn(),
    askAyla: vi.fn(),
    confirmAylaAction: vi.fn(),
  };
});

import { isHiddenTurn, visibleMessages } from "../components/AylaChat";
import { getAylaHistory } from "../lib/master-api";
import { MasterAylaScreen } from "./MasterAylaScreen";

const mockedHistory = vi.mocked(getAylaHistory);

function row(id: string, role: string, content: string) {
  return { id, role, content, tool: "", created_at: "2026-09-20T09:44:00+03:00" };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/master/ayla"]}>
      <MasterAylaScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("MasterAylaScreen · только ходы ассистента (DRF-2151)", () => {
  it("не рисует команды и токены приглашений, но рисует настоящий ход", async () => {
    mockedHistory.mockResolvedValue({
      messages: [
        row("1", "user", "/start master_invite_2d8bbc4f-1111-4222-8333-444444444444"),
        row("2", "user", "/start inv_AYLAUUA6"),
        row("3", "user", "что у меня в четверг"),
        row("4", "assistant", "В четверг две записи."),
      ],
    });

    renderScreen();

    // Присутствие впереди отсутствия: настоящий ход на месте.
    expect(await screen.findByText("что у меня в четверг")).toBeInTheDocument();
    expect(screen.getByText("В четверг две записи.")).toBeInTheDocument();
    expect(screen.queryByText(/\/start/)).not.toBeInTheDocument();
    expect(screen.queryByText(/master_invite/)).not.toBeInTheDocument();
    expect(screen.queryByText(/inv_/)).not.toBeInTheDocument();
  });

  it("ложный вход: одна подсадка «/start inv_X» → 0 вхождений и приглашение", async () => {
    mockedHistory.mockResolvedValue({ messages: [row("1", "user", "/start inv_X")] });

    renderScreen();

    expect(await screen.findByText(/Спросите про день, загрузку/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/inv_X/)).not.toBeInTheDocument());
    expect(screen.queryByText(/\/start/)).not.toBeInTheDocument();
  });
});

describe("visibleMessages / isHiddenTurn", () => {
  it("прячет команды и токены, оставляет обычные реплики", () => {
    expect(isHiddenTurn("/start")).toBe(true);
    expect(isHiddenTurn("/start inv_AYLAUUA6")).toBe(true);
    expect(isHiddenTurn("код AYLA-7K3M")).toBe(true);
    expect(isHiddenTurn("ayla-7k3m")).toBe(true);
    expect(isHiddenTurn("AYLA Beauty открыт?")).toBe(false);
    expect(isHiddenTurn("Ayla, что у меня завтра?")).toBe(false);
    expect(isHiddenTurn("master_invite_2d8bbc4f-1111-4222-8333-444444444444")).toBe(true);
    expect(isHiddenTurn("что у меня завтра")).toBe(false);
    expect(isHiddenTurn("клиент написал: инвойс готов")).toBe(false);
    const kept = visibleMessages([
      { content: "/start inv_X" },
      { content: "вопрос" },
      { content: "ответ" },
    ]);
    expect(kept.map((m) => m.content)).toEqual(["вопрос", "ответ"]);
  });
});
