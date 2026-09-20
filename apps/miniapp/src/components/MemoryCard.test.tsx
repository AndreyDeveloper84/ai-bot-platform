/**
 * `MemoryCard` (DRF-2133) — экран «Что Ayla помнит».
 *
 * Мокируется `../lib/customer-memory`: карточка — про состояния и
 * действия, а форма запросов проверена в `customer-memory.test.ts`.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-memory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/customer-memory")>();
  return {
    ...actual,
    fetchMemory: vi.fn(),
    forgetEntry: vi.fn(),
    forgetAll: vi.fn(),
  };
});

import { fetchMemory, forgetAll, forgetEntry, type MemoryResponse } from "../lib/customer-memory";
import { MEMORY_EMPTY_TEXT, MEMORY_PENDING_TEXT, MemoryCard } from "./MemoryCard";

const fetchMemoryMock = vi.mocked(fetchMemory);
const forgetEntryMock = vi.mocked(forgetEntry);
const forgetAllMock = vi.mocked(forgetAll);

const SAID_AT = "2026-09-19T20:58:47+00:00";

function doc(overrides: Partial<MemoryResponse> = {}): MemoryResponse {
  return {
    green: [
      {
        id: "g1",
        key: "diet",
        label: "придерживается веганского питания",
        value: "vegan",
        said_at: SAID_AT,
        provenance: "said",
      },
      {
        id: "g2",
        key: "preferred_time_slots",
        label: "предпочитает по вечерам",
        value: "evening",
        said_at: SAID_AT,
        provenance: "inferred",
      },
    ],
    health: [],
    status: "active",
    ...overrides,
  };
}

beforeEach(() => {
  fetchMemoryMock.mockReset();
  forgetEntryMock.mockReset();
  forgetAllMock.mockReset();
});

describe("MemoryCard — состояния", () => {
  it("пустая память — приглашение сказать факт в чате, без кнопок", async () => {
    fetchMemoryMock.mockResolvedValue(doc({ green: [], health: [] }));
    render(<MemoryCard />);
    expect(await screen.findByText(MEMORY_EMPTY_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /забыть/i })).toBeNull();
  });

  it("deletion_pending — одна честная строка вместо списка", async () => {
    fetchMemoryMock.mockResolvedValue(doc({ green: [], status: "deletion_pending" }));
    render(<MemoryCard />);
    expect(await screen.findByText(MEMORY_PENDING_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("ошибка загрузки — «Повторить», и повтор перечитывает", async () => {
    fetchMemoryMock.mockRejectedValueOnce(new Error("down"));
    fetchMemoryMock.mockResolvedValueOnce(doc());
    render(<MemoryCard />);
    const retry = await screen.findByRole("button", { name: "Повторить" });
    await userEvent.click(retry);
    expect(await screen.findByText("придерживается веганского питания")).toBeInTheDocument();
    expect(fetchMemoryMock).toHaveBeenCalledTimes(2);
  });

  it("факты — подпись чата и происхождение; предположение помечено, не скрыто", async () => {
    fetchMemoryMock.mockResolvedValue(doc());
    render(<MemoryCard />);
    const list = await screen.findByRole("list", { name: "Что Ayla помнит" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("придерживается веганского питания")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("ты сказал(а) 19.09")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("предпочитает по вечерам")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("мы предположили")).toBeInTheDocument();
    // Никаких тумблеров «разрешить запоминать» (ADR-0011).
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("раздел «Здоровье» показывается отдельно, когда сервер его прислал", async () => {
    fetchMemoryMock.mockResolvedValue(
      doc({
        health: [{ id: "h1", kind: "health", value: "аллергия на орехи", said_at: SAID_AT }],
      }),
    );
    render(<MemoryCard />);
    const health = await screen.findByRole("list", { name: "Здоровье" });
    expect(within(health).getByText("аллергия на орехи")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Здоровье" })).toBeInTheDocument();
  });
});

describe("MemoryCard — действия", () => {
  it("«Забыть» у факта — DELETE, затем список перечитывается с сервера", async () => {
    const after = doc();
    after.green = after.green.filter((f) => f.id !== "g1");
    fetchMemoryMock.mockResolvedValueOnce(doc()).mockResolvedValueOnce(after);
    forgetEntryMock.mockResolvedValue(undefined);
    render(<MemoryCard />);
    await screen.findByText("придерживается веганского питания");

    await userEvent.click(
      screen.getByRole("button", { name: "Забыть: придерживается веганского питания" }),
    );

    expect(forgetEntryMock).toHaveBeenCalledWith("g1");
    await waitFor(() =>
      expect(screen.queryByText("придерживается веганского питания")).toBeNull(),
    );
    // Правду об остатке знает сервер: второй GET, а не локальный фильтр.
    expect(fetchMemoryMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("предпочитает по вечерам")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Забыла: придерживается веганского питания",
    );
  });

  it("«Забыть» не получилось — строка остаётся, ошибка названа", async () => {
    fetchMemoryMock.mockResolvedValue(doc());
    forgetEntryMock.mockRejectedValue(new Error("down"));
    render(<MemoryCard />);
    await screen.findByText("придерживается веганского питания");

    await userEvent.click(
      screen.getByRole("button", { name: "Забыть: придерживается веганского питания" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Не получилось забыть");
    expect(screen.getByText("придерживается веганского питания")).toBeInTheDocument();
  });

  it("«Забыть всё» — только через подтверждение; отмена ничего не шлёт", async () => {
    fetchMemoryMock.mockResolvedValue(doc());
    render(<MemoryCard />);
    await screen.findByText("придерживается веганского питания");

    await userEvent.click(screen.getByRole("button", { name: "Забыть всё" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/переписка обезличится/i)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));

    expect(forgetAllMock).not.toHaveBeenCalled();
    expect(screen.getByText("придерживается веганского питания")).toBeInTheDocument();
  });

  it("«Забыть всё» подтверждено — POST, список сменяется строкой deletion_pending", async () => {
    fetchMemoryMock.mockResolvedValue(doc());
    forgetAllMock.mockResolvedValue("deletion_pending");
    render(<MemoryCard />);
    await screen.findByText("придерживается веганского питания");

    await userEvent.click(screen.getByRole("button", { name: "Забыть всё" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Забыть всё" }));

    expect(forgetAllMock).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(MEMORY_PENDING_TEXT)).toBeInTheDocument();
    expect(screen.queryByText("придерживается веганского питания")).toBeNull();
  });
});
