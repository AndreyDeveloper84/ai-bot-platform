/**
 * DRF-2247 — у мастера салона двери не ведут на `/solo/*`.
 *
 * «Рабочие часы» и «Место работы» в Настройках и «Настроить рабочие часы» на
 * пустой неделе «Расписания» вели на `/solo/*`: у салонного мастера этих
 * маршрутов нет, и он молча оказывался на «Сегодня». На `/solo/*` те же экраны
 * ведут в соло-адреса — положительная пара.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return { ...original, getMasterSchedule: vi.fn(), getPendingAvailability: vi.fn() };
});

import {
  getMasterSchedule,
  getPendingAvailability,
  type MasterScheduleResponse,
} from "../lib/master-api";
import { addDays, formatYmdLocal } from "../lib/masterDateFormat";
import { MasterScheduleScreen } from "./MasterScheduleScreen";
import { MasterSettingsScreen } from "./MasterSettingsScreen";

const mockedSchedule = vi.mocked(getMasterSchedule);
const mockedPending = vi.mocked(getPendingAvailability);

function Probe() {
  const location = useLocation();
  return <p data-testid="landed">{location.pathname}</p>;
}

function renderAt(path: string, element: React.ReactElement) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={path} element={element} />
        <Route path="*" element={<Probe />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Неделя без рабочих часов — состояние «Настроить рабочие часы». */
function emptyWeek(): MasterScheduleResponse {
  const start = new Date();
  const days = Array.from({ length: 7 }, (_, i) => ({
    date: formatYmdLocal(addDays(start, i - 3)),
    is_off_day: true,
    working_hours: null,
    bookings: [],
    blocks: [],
    free_windows: [],
    conflicts: [],
  }));
  return {
    tenant_tz: "Europe/Moscow",
    from: days[0]?.date ?? "",
    to: days[days.length - 1]?.date ?? "",
    days,
  } as unknown as MasterScheduleResponse;
}

/** Дверь «Настроить рабочие часы» живёт на виде «Неделя». */
async function openWeek() {
  await userEvent.click(await screen.findByRole("tab", { name: "Неделя" }));
}

beforeEach(() => {
  vi.resetAllMocks();
  mockedPending.mockResolvedValue({ items: [] });
});

describe("Настройки: двери своей поверхности", () => {
  it("салонный мастер: «Рабочие часы» → /master/working-hours", async () => {
    renderAt("/master/settings", <MasterSettingsScreen />);
    await userEvent.click(screen.getByRole("button", { name: "Рабочие часы" }));
    expect(screen.getByTestId("landed")).toHaveTextContent("/master/working-hours");
  });

  it("салонный мастер: «Место работы» нет — место задаёт салон, экрана у мастера нет", () => {
    renderAt("/master/settings", <MasterSettingsScreen />);
    // Положительная пара: экран отрисован, соседние двери на месте.
    expect(screen.getByRole("button", { name: "Рабочие часы" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Место работы" })).toBeNull();
  });

  it("соло: «Рабочие часы» и «Место работы» — соло-адреса", async () => {
    const { unmount } = renderAt("/solo/settings", <MasterSettingsScreen />);
    await userEvent.click(screen.getByRole("button", { name: "Рабочие часы" }));
    expect(screen.getByTestId("landed")).toHaveTextContent("/solo/working-hours");
    unmount();
    renderAt("/solo/settings", <MasterSettingsScreen />);
    await userEvent.click(screen.getByRole("button", { name: "Место работы" }));
    expect(screen.getByTestId("landed")).toHaveTextContent("/solo/place");
  });
});

describe("Расписание: пустая неделя ведёт в часы своей поверхности", () => {
  it("салонный мастер: «Настроить рабочие часы» → /master/working-hours", async () => {
    mockedSchedule.mockResolvedValue(emptyWeek());
    renderAt("/master/schedule", <MasterScheduleScreen />);
    await openWeek();
    const link = await screen.findByRole("link", { name: "Настроить рабочие часы" });
    expect(link).toHaveAttribute("href", "/master/working-hours");
  });

  it("соло: → /solo/working-hours", async () => {
    mockedSchedule.mockResolvedValue(emptyWeek());
    renderAt("/solo/schedule", <MasterScheduleScreen />);
    await openWeek();
    const link = await screen.findByRole("link", { name: "Настроить рабочие часы" });
    expect(link).toHaveAttribute("href", "/solo/working-hours");
  });
});
