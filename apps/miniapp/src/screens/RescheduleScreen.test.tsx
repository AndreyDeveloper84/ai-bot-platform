/**
 * Куда ведут выходы экрана переноса (DRF-1480).
 *
 * Три перехода клиентского пути уводили человека в СТАРОЕ поколение
 * экранов. Самый дорогой из них — успешный перенос: он приземлял на
 * старую карточку `/my-visits/:id`. Обе карточки читают один
 * `GET /bookings/<id>`, но старая `MyVisitDetailScreen` не рисует ни
 * статус оплаты, ни сумму, ни выставленную оценку и не имеет кнопки
 * «Оценить визит». То есть человек после переноса записи терял
 * возможность оценить визит.
 *
 * Замер здесь прямой: маршрут назначения проверяется по тому, ЧТО
 * смонтировалось, а способность карточки — на настоящих экранах, а не
 * на заглушках. Старые маршруты остаются смонтированы для внешних
 * ссылок — уборка поколений отдельная задача (DRF-1481), поэтому тест
 * проверяет выбор назначения, а не отсутствие старого маршрута.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    fetchBooking: vi.fn(),
    fetchSlots: vi.fn(),
    rescheduleBookingRequest: vi.fn(),
    rescheduleBookingConfirm: vi.fn(),
  };
});

import {
  fetchBooking,
  fetchSlots,
  rescheduleBookingConfirm,
  rescheduleBookingRequest,
  type BookingItem,
} from "../lib/api";
import { CustomerBookingDetailScreen } from "./CustomerBookingDetailScreen";
import { MyVisitDetailScreen } from "./MyVisitDetailScreen";
import { RescheduleScreen } from "./RescheduleScreen";

const mockedBooking = vi.mocked(fetchBooking);
const mockedSlots = vi.mocked(fetchSlots);
const mockedRequest = vi.mocked(rescheduleBookingRequest);
const mockedConfirm = vi.mocked(rescheduleBookingConfirm);

const OLD_ID = "b-old";
const NEW_ID = "b-new";

function booking(partial: Partial<BookingItem> & Pick<BookingItem, "id">): BookingItem {
  return {
    status: "confirmed",
    service_id: "svc-1",
    service_name: "Маникюр",
    master_id: "mst-1",
    master_name: "Анна Соколова",
    visit_at: "2026-09-08T15:00:00+03:00",
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

/** Показывает, КАКОЙ маршрут смонтирован и с каким id. */
function Probe({ label }: { label: string }) {
  const { bookingId } = useParams();
  return <div>{`${label}:${bookingId ?? "-"}`}</div>;
}

function renderReschedule() {
  render(
    <MemoryRouter initialEntries={[`/my-visits/${OLD_ID}/reschedule`]}>
      <Routes>
        <Route path="/my-visits/:bookingId/reschedule" element={<RescheduleScreen />} />
        <Route path="/customer/records/:bookingId" element={<Probe label="НОВАЯ КАРТОЧКА" />} />
        <Route path="/my-visits/:bookingId" element={<Probe label="СТАРАЯ КАРТОЧКА" />} />
        <Route path="/customer/catalog" element={<div>НОВЫЙ КАТАЛОГ</div>} />
        <Route path="/catalog" element={<div>СТАРЫЙ КАТАЛОГ</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Доводит перенос до конца: выбрать слот → подтвердить. */
async function rescheduleThrough() {
  const slot = await screen.findByRole("radio", { name: "12:00" });
  await userEvent.click(slot);
  await userEvent.click(screen.getByRole("button", { name: "Подтвердить перенос" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedBooking.mockImplementation((id: string) =>
    Promise.resolve({ booking: booking({ id }) }),
  );
  mockedSlots.mockResolvedValue({
    slots: [{ date: "2026-09-10", start: "2026-09-10T12:00:00+03:00" }],
  });
  mockedRequest.mockResolvedValue({ booking: booking({ id: OLD_ID }) });
  mockedConfirm.mockResolvedValue({
    old_booking: booking({ id: OLD_ID }),
    new_booking: booking({ id: NEW_ID }),
  });
});

describe("успешный перенос приземляет в новое поколение (DRF-1480)", () => {
  it("после переноса открыта НОВАЯ карточка с id новой записи", async () => {
    renderReschedule();
    await rescheduleThrough();

    // Присутствие: мы действительно куда-то приехали, и это новая
    // карточка новой записи — id перенесён как есть.
    expect(await screen.findByText(`НОВАЯ КАРТОЧКА:${NEW_ID}`)).toBeInTheDocument();
    // И только тогда осмысленно: старое поколение не смонтировано.
    expect(screen.queryByText(`СТАРАЯ КАРТОЧКА:${NEW_ID}`)).toBeNull();
  });

  it("«Каталог» из состояния «сирота» ведёт в новый каталог", async () => {
    // Услуга удалена из каталога — переносить нечего, экран предлагает
    // записаться заново. Эта дверь тоже вела в старое поколение.
    mockedBooking.mockResolvedValue({
      booking: booking({ id: OLD_ID, service_id: null, master_id: null }),
    });
    renderReschedule();

    await userEvent.click(await screen.findByRole("button", { name: "Каталог" }));

    expect(screen.getByText("НОВЫЙ КАТАЛОГ")).toBeInTheDocument();
    expect(screen.queryByText("СТАРЫЙ КАТАЛОГ")).toBeNull();
  });
});

describe("почему это не косметика: расхождение карточек", () => {
  // Оценить визит можно только с той карточки, где есть кнопка. Пока
  // перенос приземлял на старую, человек её не видел.
  const RATEABLE = booking({
    id: NEW_ID,
    visit_at: "2026-08-20T15:00:00+03:00",
    cancellable: false,
    reschedulable: false,
    can_rate: true,
  });

  function renderCard(element: React.ReactElement) {
    render(
      <MemoryRouter initialEntries={[`/card/${NEW_ID}`]}>
        <Routes>
          <Route path="/card/:bookingId" element={element} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("новая карточка даёт «Оценить визит» на той же записи", async () => {
    mockedBooking.mockResolvedValue({ booking: RATEABLE });
    renderCard(<CustomerBookingDetailScreen />);

    // Присутствие: запись отрисована.
    expect(await screen.findByText("Маникюр")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оценить визит" })).toBeInTheDocument();
  });

  it("старая карточка на той же записи кнопки не даёт", async () => {
    mockedBooking.mockResolvedValue({ booking: RATEABLE });
    renderCard(<MyVisitDetailScreen />);

    // Присутствие сначала: экран загрузился и показывает ту же запись.
    expect(await screen.findByText("Маникюр")).toBeInTheDocument();
    // Только теперь отсутствие кнопки — измерение, а не совпадение.
    expect(screen.queryByRole("button", { name: "Оценить визит" })).toBeNull();
  });
});
