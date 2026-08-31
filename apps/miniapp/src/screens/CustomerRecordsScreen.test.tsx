/**
 * Tests for `CustomerRecordsScreen` after pilot phase 3.2 — real
 * `GET /bookings/list` data instead of the invented stub bookings.
 * HTTP layer (`../lib/api`) mocked; fixtures are verbatim `BookingItem`
 * rows. Home = records: the screen is a tab root (no back button) and
 * renders identically in DEV and prod builds.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Домашний экран теперь спрашивает decision-context (приглашение в
// анкету цели). Мокаем, чтобы юнит-тест не ходил в сеть; отсутствие
// missing = приглашение не рисуется и на эти проверки не влияет.
vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return {
    ...original,
    fetchDecisionContext: vi.fn().mockResolvedValue({
      version: 1,
      known: { goal: null },
      missing: [],
      suggestions: [],
      intents: [],
    }),
  };
});

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    fetchMyBookings: vi.fn(),
  };
});

import { fetchMyBookings, type BookingItem } from "../lib/api";
import { CustomerRecordsScreen } from "./CustomerRecordsScreen";

const mockedList = vi.mocked(fetchMyBookings);

function isoInHours(hours: number): string {
  return new Date(Date.now() + hours * 3_600_000).toISOString();
}

function booking(partial: Partial<BookingItem> & Pick<BookingItem, "id">): BookingItem {
  return {
    status: "confirmed",
    service_id: "svc-1",
    service_name: "Маникюр",
    master_id: "mst-1",
    master_name: "Анна Соколова",
    visit_at: isoInHours(48),
    duration_min: 90,
    cancel_requested_at: null,
    undo_window_seconds: 300,
    cancellable: true,
    reschedulable: true,
    rating: null,
    can_rate: false,
    ...partial,
  };
}

const UPCOMING: BookingItem[] = [
  booking({ id: "b-1", visit_at: isoInHours(20) }),
  booking({ id: "b-2", service_name: "Массаж", visit_at: isoInHours(70) }),
];

const HISTORY: BookingItem[] = [
  booking({
    id: "b-h1",
    visit_at: isoInHours(-30),
    cancellable: false,
    reschedulable: false,
    can_rate: true,
  }),
  booking({
    id: "b-h2",
    status: "cancelled",
    service_name: "Брови",
    visit_at: isoInHours(-80),
    cancellable: false,
    reschedulable: false,
  }),
  booking({
    id: "b-h3",
    status: "rescheduled",
    service_name: "Педикюр",
    visit_at: isoInHours(-100),
    cancellable: false,
    reschedulable: false,
  }),
];

function mockLists(upcoming: BookingItem[] = UPCOMING, history: BookingItem[] = HISTORY) {
  mockedList.mockImplementation((params) =>
    Promise.resolve({
      items: params?.past ? history : upcoming,
      next_cursor: null,
    }),
  );
}

function BookingProbe() {
  const { bookingId } = useParams();
  return <div>BOOKING-{bookingId}</div>;
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/main"]}>
      <Routes>
        <Route path="/customer/main" element={<CustomerRecordsScreen />} />
        <Route path="/customer/records/:bookingId" element={<BookingProbe />} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
        <Route path="/feedback/:bookingId" element={<BookingProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

describe("CustomerRecordsScreen (real data)", () => {
  it("renders real upcoming bookings with tab counts and status badges", async () => {
    mockLists();
    renderScreen();
    expect(await screen.findByRole("tab", { name: "Ближайшие (2)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "История (3)" })).toBeInTheDocument();
    expect(screen.getByText("Маникюр")).toBeInTheDocument();
    expect(screen.getByText("Массаж")).toBeInTheDocument();
    expect(screen.getAllByText("Подтверждена").length).toBeGreaterThan(0);
    // Nearest card (≤24h) carries manage actions; the future one doesn't.
    expect(screen.getByRole("button", { name: "Перенести" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeInTheDocument();
  });

  it("history tab shows derived statuses and history actions", async () => {
    const user = userEvent.setup();
    mockLists();
    renderScreen();
    await screen.findByRole("tab", { name: "Ближайшие (2)" });
    await user.click(screen.getByRole("tab", { name: "История (3)" }));
    expect(await screen.findByText("Прошла")).toBeInTheDocument();
    expect(screen.getByText("Отменена")).toBeInTheDocument();
    expect(screen.getByText("Перенесена")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оставить отзыв" })).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "Записаться ещё" }).length,
    ).toBeGreaterThan(0);
  });

  it("contains no stub-era artefacts and no message action", async () => {
    mockLists();
    renderScreen();
    await screen.findByRole("tab", { name: "Ближайшие (2)" });
    expect(screen.queryByText("Beauty Place")).not.toBeInTheDocument();
    expect(screen.queryByText("Casa Bella")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сообщить по записи" })).not.toBeInTheDocument();
    expect(screen.queryByText(/ул\. Тверская/)).not.toBeInTheDocument();
  });

  it("shows the empty state when there are no bookings at all", async () => {
    const user = userEvent.setup();
    mockLists([], []);
    renderScreen();
    expect(await screen.findByText(/Пока записей нет/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Найти услугу" }));
    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
  });

  it("shows the error state with a working retry", async () => {
    const user = userEvent.setup();
    mockedList.mockRejectedValueOnce(new Error("network down"));
    mockLists();
    renderScreen();
    expect(await screen.findByText(/Не получилось загрузить/)).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Обновить" })[0]!);
    expect(await screen.findByRole("tab", { name: "Ближайшие (2)" })).toBeInTheDocument();
  });

  it("opens the booking detail from a card", async () => {
    const user = userEvent.setup();
    mockLists();
    renderScreen();
    await screen.findByText("Маникюр");
    await user.click(screen.getAllByRole("button", { name: /Открыть/ })[0]!);
    expect(await screen.findByText("BOOKING-b-1")).toBeInTheDocument();
  });

  it("review CTA leads to the real feedback screen", async () => {
    const user = userEvent.setup();
    mockLists();
    renderScreen();
    await screen.findByRole("tab", { name: "Ближайшие (2)" });
    await user.click(screen.getByRole("tab", { name: "История (3)" }));
    await user.click(await screen.findByRole("button", { name: "Оставить отзыв" }));
    expect(await screen.findByText("BOOKING-b-h1")).toBeInTheDocument();
  });

  it("repeat CTA leads to the real catalog", async () => {
    const user = userEvent.setup();
    mockLists();
    renderScreen();
    await screen.findByRole("tab", { name: "Ближайшие (2)" });
    await user.click(screen.getByRole("tab", { name: "История (3)" }));
    await user.click((await screen.findAllByRole("button", { name: "Записаться ещё" }))[0]!);
    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
  });

  it("is a tab root — no back button (home = records)", async () => {
    mockLists();
    renderScreen();
    await screen.findByRole("tab", { name: "Ближайшие (2)" });
    expect(screen.queryByRole("button", { name: "Назад" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Записи" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
