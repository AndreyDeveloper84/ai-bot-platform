/**
 * Инцидент 20.09 — три двери мастерского кабинета (DRF-2150, скрины владельца).
 *
 * 1. «Назад» в «Расписании» (и на других экранах мастера) показывал стрелку
 *    MAX без обработчика — тап в пустоту. Теперь — `useScreenBack`; сторож:
 *    ни один экран не зовёт `setBackButton(true)` без объявления возврата.
 * 2. «Открыть чек-лист» на «Сегодня» салонного мастера вёл на `/solo/setup`,
 *    которого в мастерском дереве нет. Карточка — только на соло-поверхности.
 * 3. Лист аватара лежал под нижней панелью (z-index 40 < 100) — из трёх
 *    пунктов виден был один. Лист — поверх панели; CSS-узел — в
 *    `masterDoors2150.build.test.ts` (читает globals.css с диска).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return {
    ...original,
    setBackButton: vi.fn(),
    onBackButton: vi.fn(() => () => {}),
  };
});
vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getMasterSchedule: vi.fn(),
    getPendingAvailability: vi.fn(),
    getMasterMe: vi.fn(),
    getOnboardingReadiness: vi.fn(),
    getDashboard: vi.fn(),
    getMasterConversations: vi.fn(),
  };
});

import { SetupProgressCard } from "../components/SetupProgressCard";
import {
  getMasterMe,
  getMasterSchedule,
  getOnboardingReadiness,
  getPendingAvailability,
  type OnboardingReadiness,
} from "../lib/master-api";
import { onBackButton, setBackButton } from "../lib/max-sdk";
import { MasterScheduleScreen } from "./MasterScheduleScreen";

const SCREEN_SOURCES = import.meta.glob(["./*.tsx", "./admin/*.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const NOT_READY: OnboardingReadiness = {
  ready: false,
  blocking: ["hours:missing"],
  items: [
    {
      key: "hours",
      state: "missing",
      detail: {},
      reason: null,
      deep_link: "/solo/working-hours",
    },
    {
      key: "profile",
      state: "done",
      detail: {},
      reason: null,
      deep_link: "/solo/profile",
    },
  ],
  identity: { state: "pending", link_status: "PENDING" },
  setup_state: "SETUP_PENDING",
  sale_block: "SETUP_PENDING",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getMasterSchedule).mockResolvedValue({
    tenant_tz: "Europe/Moscow",
    from: "2026-09-20",
    to: "2026-09-20",
    days: [],
  } as never);
  vi.mocked(getPendingAvailability).mockResolvedValue({ items: [] } as never);
  vi.mocked(getOnboardingReadiness).mockResolvedValue(NOT_READY);
  vi.mocked(getMasterMe).mockRejectedValue(new Error("not needed"));
});

describe("1 · «Назад» ведёт куда-то", () => {
  it("«Расписание»: стрелка MAX подключена — к «Сегодня» своей поверхности", async () => {
    render(
      <MemoryRouter initialEntries={["/master/schedule"]}>
        <Routes>
          <Route path="/master/schedule" element={<MasterScheduleScreen />} />
          <Route path="/master/dashboard" element={<p>Экран «Сегодня»</p>} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(setBackButton).toHaveBeenCalledWith(true));
    const handler = vi.mocked(onBackButton).mock.calls.at(-1)?.[0];
    expect(typeof handler).toBe("function");
    handler!();
    expect(await screen.findByText("Экран «Сегодня»")).toBeInTheDocument();
  });

  it("сторож: экран, который показывает стрелку, обязан объявить возврат", () => {
    const offenders: string[] = [];
    for (const [file, src] of Object.entries(SCREEN_SOURCES)) {
      if (file.endsWith(".test.tsx")) continue;
      const shows = /setBackButton\(true\)/.test(src);
      const declares = /useScreenBack\(|useBackButton\(|onBackButton\(/.test(
        src,
      );
      if (shows && !declares) offenders.push(file);
    }
    expect(Object.keys(SCREEN_SOURCES).length).toBeGreaterThan(20);
    expect(offenders).toEqual([]); // empty-assert-ok: выборка выше не пуста
  });
});

describe("2 · чек-лист — только на соло-поверхности", () => {
  function renderCardAt(path: string) {
    render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/solo/my-day" element={<SetupProgressCard />} />
          <Route path="/master/dashboard" element={<SetupProgressCard />} />
          <Route path="/solo/setup" element={<p>Экран чек-листа</p>} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("на /solo/my-day карточка есть и «Открыть чек-лист» открывает экран", async () => {
    renderCardAt("/solo/my-day");
    (await screen.findByRole("button", { name: "Открыть чек-лист" })).click();
    expect(await screen.findByText("Экран чек-листа")).toBeInTheDocument();
  });

  it("на /master/dashboard карточки нет — и readiness не спрашивается", async () => {
    renderCardAt("/master/dashboard");
    await new Promise((r) => setTimeout(r, 20));
    expect(
      screen.queryByRole("button", { name: "Открыть чек-лист" }),
    ).toBeNull();
    expect(getOnboardingReadiness).not.toHaveBeenCalled();
  });
});
