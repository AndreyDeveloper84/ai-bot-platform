/**
 * Красная линия текущего времени на шкале «Расписания» — макет DRF-1183
 * («Текущее время: тонкая красная линия показывает текущее время»;
 * «Текущее время всегда видно, но не отвлекает») — DRF-2194 (М-6c).
 *
 * Шкала экрана — список карточек по времени; линия — элемент списка между
 * карточками по HH:MM, с подписью времени, только на сегодняшнем дне. Не
 * тикает (setInterval на мастерских экранах не заводим): положение — на
 * момент рендера/перезагрузки. Источник «сейчас» — часы устройства: ответ
 * расписания серверного now не несёт (отступление, владельцу).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return { ...original, getMasterSchedule: vi.fn(), getPendingAvailability: vi.fn() };
});

import {
  getMasterSchedule,
  getPendingAvailability,
  type MasterScheduleResponse,
  type ScheduleDay,
} from "../lib/master-api";
import { addDays, formatYmdLocal } from "../lib/masterDateFormat";
import { MasterScheduleScreen } from "./MasterScheduleScreen";

const SCHEDULE_SOURCE = import.meta.glob("./MasterScheduleScreen.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const mockedSchedule = vi.mocked(getMasterSchedule);
const mockedPending = vi.mocked(getPendingAvailability);

/** Сегодня 13:37 по часам устройства — как на картинке DRF-1183. */
function freezeNow(hm = "13:37") {
  const d = new Date();
  const [h, m] = hm.split(":").map(Number);
  d.setHours(h ?? 13, m ?? 37, 0, 0);
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(d);
  return d;
}

function day(ymd: string, hours: string[]): ScheduleDay {
  return {
    date: ymd,
    is_off_day: false,
    working_hours: { start: "09:00", end: "18:00" },
    bookings: hours.map((hm, i) => ({
      booking_id: `b-${i}`,
      visit_at: `${ymd}T${hm}:00`,
      duration_min: 60,
      service_name: "Массаж",
      client_first_name: ["Мария", "Анна", "Ольга"][i] ?? "Клиент",
      client_last_initial: "К.",
      is_in_progress: false,
      is_returning_customer: false,
    })),
    blocks: [],
    free_windows: [],
    conflicts: [],
  };
}

function scheduleFor(days: ScheduleDay[]): MasterScheduleResponse {
  return {
    tenant_tz: "Europe/Moscow",
    from: days[0]?.date ?? "",
    to: days[days.length - 1]?.date ?? "",
    days,
  };
}

function renderAt(path = "/master/schedule") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/master/schedule" element={<MasterScheduleScreen />} />
        <Route path="/solo/schedule" element={<MasterScheduleScreen />} />
        <Route path="*" element={<p>другой экран</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  mockedPending.mockResolvedValue({ items: [] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("линия «сейчас» на дне (DRF-1183, DRF-2194)", () => {
  it("сегодня 13:37: линия между записью 12:00 и записью 15:00, с подписью времени", async () => {
    const now = freezeNow("13:37");
    const today = formatYmdLocal(now);
    mockedSchedule.mockResolvedValue(scheduleFor([day(today, ["12:00", "15:00"])]));
    renderAt();

    const list = await screen.findByRole("list", { name: /расписание дня/i });
    const now_ = within(list).getByRole("separator", { name: /сейчас/i });
    expect(now_).toHaveTextContent("13:37");
    const items = Array.from(list.querySelectorAll(":scope > li"));
    const idxFirst = items.findIndex((li) => li.textContent?.includes("Мария К."));
    const idxNow = items.findIndex((li) => li.contains(now_));
    const idxSecond = items.findIndex((li) => li.textContent?.includes("Анна К."));
    expect(idxFirst).toBeGreaterThanOrEqual(0);
    expect(idxNow).toBeGreaterThan(idxFirst);
    expect(idxSecond).toBeGreaterThan(idxNow);
  });

  it("линия не тикает: экран не заводит setInterval (по исходнику)", () => {
    const src = Object.values(SCHEDULE_SOURCE)[0] ?? "";
    expect(src.length).toBeGreaterThan(0);
    expect(src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "")).not.toContain("setInterval");
  });

  it("сегодня 08:00, до всех записей — линия первой; 20:00, после всех — последней", async () => {
    const now = freezeNow("08:00");
    const today = formatYmdLocal(now);
    mockedSchedule.mockResolvedValue(scheduleFor([day(today, ["12:00"])]));
    const { unmount } = renderAt();
    let list = await screen.findByRole("list", { name: /расписание дня/i });
    let items = Array.from(list.querySelectorAll(":scope > li"));
    expect(items[0]?.querySelector('[role="separator"]')).not.toBeNull();
    unmount();

    vi.setSystemTime(new Date(now.setHours(20, 0, 0, 0)));
    renderAt();
    list = await screen.findByRole("list", { name: /расписание дня/i });
    items = Array.from(list.querySelectorAll(":scope > li"));
    expect(items[items.length - 1]?.querySelector('[role="separator"]')).not.toBeNull();
  });

  it("на другом дне линии нет", async () => {
    const now = freezeNow("13:37");
    const today = formatYmdLocal(now);
    const tomorrow = formatYmdLocal(addDays(now, 1));
    mockedSchedule.mockResolvedValue(
      scheduleFor([day(today, ["12:00"]), day(tomorrow, ["12:00"])]),
    );
    renderAt();
    await screen.findByRole("separator", { name: /сейчас/i });
    const user = userEvent.setup({ delay: null });
    await user.click(screen.getByRole("button", { name: "Вперёд" }));
    // Экран перезапрашивает диапазон и показывает завтрашний день.
    const list = await screen.findByRole("list", { name: /расписание дня/i });
    expect(within(list).queryByRole("separator", { name: /сейчас/i })).toBeNull();
  });

  it("пустой сегодняшний день — линия всё равно есть (время видно всегда)", async () => {
    const now = freezeNow("13:37");
    const today = formatYmdLocal(now);
    mockedSchedule.mockResolvedValue(scheduleFor([day(today, [])]));
    renderAt();
    expect(await screen.findByRole("separator", { name: /сейчас/i })).toHaveTextContent("13:37");
  });
});
