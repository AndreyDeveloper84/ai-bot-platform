/**
 * Перенос записи клиентом — три шага по макету «Записи.png» (DRF-2952),
 * кадры 5, 6, 7. Слой HTTP (`../lib/api`) подменён.
 *
 * Что здесь держится:
 *
 *   - два ключа услуги: свободное время — по ключу каталога бота, перенос —
 *     по `service_id` записи (на пути Ayla они разные, и экран слал в `/slots`
 *     не тот — перенос там не открывался);
 *   - подтверждение несёт выбранное время (DRF-2561);
 *   - слова результата — владельца (28.09, п.8): «Перенесла запись», «Было»,
 *     «Стало»; показываются только по ответу сервера, и «Стало» — из ответа;
 *   - отказы — клиентский регистр владельца (28.09, п.10), без «слота»;
 *   - выходы ведут в `/customer/*` (DRF-1480), старый адрес — алиас (DRF-1481).
 */
import { render, screen, within } from "@testing-library/react";
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
  ApiError,
  fetchBooking,
  fetchSlots,
  rescheduleBookingConfirm,
  rescheduleBookingRequest,
  type BookingItem,
} from "../lib/api";
import { markRead } from "../lib/claims";
import { CLIENT_RESCHEDULE_REFUSAL } from "../lib/refusal-canon";
import { RESCHEDULE_COPY, RescheduleScreen } from "./RescheduleScreen";

const mockedBooking = vi.mocked(fetchBooking);
const mockedSlots = vi.mocked(fetchSlots);
const mockedRequest = vi.mocked(rescheduleBookingRequest);
const mockedConfirm = vi.mocked(rescheduleBookingConfirm);

const OLD_ID = "b-old";
const NEW_ID = "b-new";
const PICKED = "2026-09-10T12:00:00+03:00";
/** Время, которое назначил сервер, — нарочно не то, что выбрано на экране. */
const SERVER_TIME = "2026-09-10T12:30:00+03:00";

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
    address: null,
    ...partial,
  };
}

function Probe({ label }: { label: string }) {
  const { bookingId } = useParams();
  return <div>{`${label}:${bookingId ?? "-"}`}</div>;
}

function renderReschedule(at = `/customer/records/${OLD_ID}/reschedule`) {
  render(
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        {/* Канонический адрес (DRF-1481) и его алиас. */}
        <Route path="/customer/records/:bookingId/reschedule" element={<RescheduleScreen />} />
        <Route path="/my-visits/:bookingId/reschedule" element={<RescheduleScreen />} />
        <Route path="/customer/records/:bookingId" element={<Probe label="НОВАЯ КАРТОЧКА" />} />
        <Route path="/my-visits/:bookingId" element={<Probe label="СТАРАЯ КАРТОЧКА" />} />
        <Route path="/customer/catalog" element={<div>НОВЫЙ КАТАЛОГ</div>} />
        <Route path="/catalog" element={<div>СТАРЫЙ КАТАЛОГ</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Ответ подтверждения с меткой прочитанного на новой записи — как у клиента. */
function confirmedWith(newBooking: BookingItem) {
  return async () => ({
    old_booking: booking({ id: OLD_ID }),
    new_booking: markRead({ ...newBooking }, { source: "/bookings/b-old/reschedule/confirm", status: 200 }),
  });
}

/** Шаг 1 → шаг 2: выбрать время и «Продолжить». */
async function pickAndContinue() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("radio", { name: "12:00" }));
  await user.click(screen.getByRole("button", { name: RESCHEDULE_COPY.next }));
  return user;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedBooking.mockImplementation((id: string) => Promise.resolve({ booking: booking({ id }) }));
  mockedSlots.mockResolvedValue({ slots: [{ date: "2026-09-10", start: PICKED }] });
  mockedRequest.mockResolvedValue({ booking: booking({ id: OLD_ID }) });
  mockedConfirm.mockImplementation(confirmedWith(booking({ id: NEW_ID, visit_at: SERVER_TIME })));
});

describe("шаг 1, кадр 5 — выбор дня и времени", () => {
  it("заголовок, какая запись переносится, неделя и время", async () => {
    renderReschedule();

    expect(await screen.findByRole("heading", { name: RESCHEDULE_COPY.title })).toBeInTheDocument();
    // Строка «что переносим» — услуга и нынешнее время записи.
    expect(screen.getByText("Маникюр · 8 сентября, 15:00")).toBeInTheDocument();
    expect(screen.getByText("Сентябрь 2026")).toBeInTheDocument();
    const days = within(screen.getByRole("radiogroup", { name: "Дата визита" })).getAllByRole("radio");
    expect(days).toHaveLength(7);
    expect(screen.getByRole("heading", { name: RESCHEDULE_COPY.slotsHeading })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "12:00" })).toBeInTheDocument();
  });

  it("день без свободного времени выбрать нельзя; день со временем — выбран", async () => {
    renderReschedule();
    await screen.findByRole("radio", { name: "12:00" });
    const days = within(screen.getByRole("radiogroup", { name: "Дата визита" })).getAllByRole("radio");
    const enabled = days.filter((d) => !(d as HTMLButtonElement).disabled);
    expect(enabled).toHaveLength(1);
    expect(enabled[0]).toHaveAttribute("aria-checked", "true");
    expect(enabled[0]).toHaveTextContent("10");
  });

  it("смена недели: выбран её первый свободный день, время прошлой недели снято", async () => {
    mockedSlots.mockResolvedValue({
      slots: [
        { date: "2026-09-10", start: PICKED },
        { date: "2026-09-17", start: "2026-09-17T16:00:00+03:00" },
      ],
    });
    renderReschedule();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "12:00" }));
    await user.click(screen.getByRole("button", { name: "Следующая неделя" }));

    // Времени прошлой недели на экране нет, и «Продолжить» с ним не нажать.
    expect(screen.queryByRole("radio", { name: "12:00" })).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "16:00" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("button", { name: RESCHEDULE_COPY.next })).toBeDisabled();
    // Листать дальше некуда — стрелки вперёд нет, назад есть.
    expect(screen.queryByRole("button", { name: "Следующая неделя" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Предыдущая неделя" })).toBeInTheDocument();
  });

  it("поиск времени ответил 404 — это не «запись не загрузилась», а «перенести нельзя»", async () => {
    mockedSlots.mockRejectedValue(new ApiError(404, "not_found", "master not found or not bookable"));
    renderReschedule();

    expect(await screen.findByText(RESCHEDULE_COPY.orphan)).toBeInTheDocument();
    expect(screen.queryByText(RESCHEDULE_COPY.loadFailed)).not.toBeInTheDocument();
    expect(screen.queryByText(/not bookable/)).not.toBeInTheDocument();
  });

  it("«Продолжить» выключена, пока время не выбрано", async () => {
    renderReschedule();
    const next = await screen.findByRole("button", { name: RESCHEDULE_COPY.next });
    expect(next).toBeDisabled();
    await userEvent.setup().click(screen.getByRole("radio", { name: "12:00" }));
    expect(next).toBeEnabled();
  });

  it("свободного времени нет — сказано прямо, перейти дальше нельзя", async () => {
    mockedSlots.mockResolvedValue({ slots: [] });
    renderReschedule();
    expect(await screen.findByText(RESCHEDULE_COPY.noSlots)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: RESCHEDULE_COPY.next })).toBeDisabled();
  });

  it("«Вернуться к записи» и стрелка ведут в каноническую карточку, а не в старую", async () => {
    renderReschedule();
    await screen.findByRole("radio", { name: "12:00" });
    await userEvent.setup().click(screen.getByRole("button", { name: RESCHEDULE_COPY.backToRecord }));
    expect(await screen.findByText(`НОВАЯ КАРТОЧКА:${OLD_ID}`)).toBeInTheDocument();
    expect(screen.queryByText(/СТАРАЯ КАРТОЧКА/)).not.toBeInTheDocument();
  });
});

describe("два ключа услуги (DRF-2952)", () => {
  it("путь Ayla: время ищется по ключу каталога, перенос уходит с `service_id` записи", async () => {
    mockedBooking.mockResolvedValue({
      booking: booking({ id: OLD_ID, service_id: "ayla-svc", catalog_service_id: "catalog-svc" }),
    });
    renderReschedule();
    const user = await pickAndContinue();

    expect(mockedSlots).toHaveBeenCalledWith(
      expect.objectContaining({ serviceId: "catalog-svc", masterId: "mst-1" }),
    );
    await user.click(screen.getByRole("button", { name: RESCHEDULE_COPY.confirm }));
    await screen.findByRole("heading", { name: RESCHEDULE_COPY.doneTitle });
    expect(mockedConfirm).toHaveBeenCalledWith(
      OLD_ID,
      expect.objectContaining({ new_service_id: "ayla-svc" }),
    );
  });

  it("сервер старше и ключа каталога не несёт — годится `service_id`", async () => {
    renderReschedule();
    await screen.findByRole("radio", { name: "12:00" });
    expect(mockedSlots).toHaveBeenCalledWith(expect.objectContaining({ serviceId: "svc-1" }));
  });

  it("услуги в каталоге больше нет — «сирота»: «Каталог» ведёт в новый каталог", async () => {
    mockedBooking.mockResolvedValue({
      booking: booking({ id: OLD_ID, service_id: "ayla-svc", catalog_service_id: null }),
    });
    renderReschedule();

    expect(await screen.findByText(RESCHEDULE_COPY.orphan)).toBeInTheDocument();
    expect(mockedSlots).not.toHaveBeenCalled();
    await userEvent.setup().click(screen.getByRole("button", { name: RESCHEDULE_COPY.catalog }));
    expect(await screen.findByText("НОВЫЙ КАТАЛОГ")).toBeInTheDocument();
  });
});

describe("шаг 2, кадр 6 — подтверждение", () => {
  it("«Сейчас» и «Новое» — прежние слова; запрос не уходит до подтверждения", async () => {
    renderReschedule();
    await pickAndContinue();

    expect(screen.getByText(RESCHEDULE_COPY.now)).toBeInTheDocument();
    expect(screen.getByText("8 сентября, 15:00")).toBeInTheDocument();
    expect(screen.getByText(RESCHEDULE_COPY.fresh)).toBeInTheDocument();
    expect(screen.getByText("10 сентября, 12:00")).toBeInTheDocument();
    expect(mockedRequest).not.toHaveBeenCalled();
    expect(mockedConfirm).not.toHaveBeenCalled();
  });

  it("DRF-2561: подтверждение получает то же тело, что заявка, — мастер, услуга и время", async () => {
    renderReschedule();
    const user = await pickAndContinue();
    await user.click(screen.getByRole("button", { name: RESCHEDULE_COPY.confirm }));
    await screen.findByRole("heading", { name: RESCHEDULE_COPY.doneTitle });

    const candidate = { new_master_id: "mst-1", new_service_id: "svc-1", new_visit_at: PICKED };
    expect(mockedRequest).toHaveBeenCalledWith(OLD_ID, candidate);
    expect(mockedConfirm).toHaveBeenCalledWith(OLD_ID, candidate);
  });

  it("«Отмена» возвращает к выбору, выбранное время остаётся", async () => {
    renderReschedule();
    const user = await pickAndContinue();
    await user.click(screen.getByRole("button", { name: RESCHEDULE_COPY.cancel }));

    expect(await screen.findByRole("radio", { name: "12:00" })).toHaveAttribute("aria-checked", "true");
    expect(mockedRequest).not.toHaveBeenCalled();
  });
});

describe("шаг 3, кадр 7 — результат (слова владельца 28.09, п.8)", () => {
  it("«Перенесла запись», «Было» и «Стало» — и «Стало» из ответа сервера", async () => {
    renderReschedule();
    const user = await pickAndContinue();
    await user.click(screen.getByRole("button", { name: RESCHEDULE_COPY.confirm }));

    expect(await screen.findByRole("heading", { name: RESCHEDULE_COPY.doneTitle })).toBeInTheDocument();
    expect(screen.getByText("Было:").parentElement).toHaveTextContent("Было: 8 сентября в 15:00");
    // Выбрано 12:00, сервер назначил 12:30 — показано время записи, не экрана.
    expect(screen.getByText("Стало:").parentElement).toHaveTextContent("Стало: 10 сентября в 12:30");
  });

  it("DRF-1480: «Перейти к записи» открывает НОВУЮ карточку с id новой записи", async () => {
    renderReschedule();
    const user = await pickAndContinue();
    await user.click(screen.getByRole("button", { name: RESCHEDULE_COPY.confirm }));
    await user.click(await screen.findByRole("button", { name: RESCHEDULE_COPY.toRecord }));

    expect(await screen.findByText(`НОВАЯ КАРТОЧКА:${NEW_ID}`)).toBeInTheDocument();
    expect(screen.queryByText(/СТАРАЯ КАРТОЧКА/)).not.toBeInTheDocument();
  });

  it("ответ без доказательства слов не получает: открывается запись, она скажет сама", async () => {
    // Непомеченный ответ — значение, которого клиент API не читал.
    mockedConfirm.mockResolvedValue({
      old_booking: booking({ id: OLD_ID }),
      new_booking: booking({ id: NEW_ID }),
    });
    renderReschedule();
    const user = await pickAndContinue();
    await user.click(screen.getByRole("button", { name: RESCHEDULE_COPY.confirm }));

    expect(await screen.findByText(`НОВАЯ КАРТОЧКА:${NEW_ID}`)).toBeInTheDocument();
    expect(screen.queryByText(RESCHEDULE_COPY.doneTitle)).not.toBeInTheDocument();
  });
});

describe("слова отказа — клиентский регистр владельца (28.09, п.10)", () => {
  it("время заняли — фраза владельца, возврат к выбору со свежим списком, выбор сброшен", async () => {
    mockedRequest.mockRejectedValueOnce(new ApiError(409, "slot_unavailable", "slot taken"));
    renderReschedule();
    const user = await pickAndContinue();
    await user.click(screen.getByRole("button", { name: RESCHEDULE_COPY.confirm }));

    expect(await screen.findByText(CLIENT_RESCHEDULE_REFUSAL.slotTaken)).toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: "12:00" })).toHaveAttribute("aria-checked", "false");
    expect(mockedSlots).toHaveBeenCalledTimes(2);
    expect(CLIENT_RESCHEDULE_REFUSAL.slotTaken).not.toMatch(/слот/i);
    expect(screen.queryByText("slot taken")).not.toBeInTheDocument();
  });

  it("прочий сбой — другая фраза владельца; человек остаётся на подтверждении", async () => {
    mockedRequest.mockRejectedValueOnce(new ApiError(500, "http_error", "boom"));
    renderReschedule();
    const user = await pickAndContinue();
    await user.click(screen.getByRole("button", { name: RESCHEDULE_COPY.confirm }));

    expect(await screen.findByText(CLIENT_RESCHEDULE_REFUSAL.failed)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: RESCHEDULE_COPY.confirm })).toBeEnabled();
    expect(screen.queryByText(RESCHEDULE_COPY.doneTitle)).not.toBeInTheDocument();
  });
});

describe("запись не загрузилась и старый адрес", () => {
  it("сбой загрузки — кадр 15 с рабочим повтором", async () => {
    mockedBooking.mockRejectedValueOnce(new ApiError(500, "http_error", ""));
    renderReschedule();
    expect(
      await screen.findByRole("heading", { name: RESCHEDULE_COPY.loadFailed }),
    ).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(await screen.findByRole("radio", { name: "12:00" })).toBeInTheDocument();
  });

  it("DRF-1481: старый адрес монтирует тот же экран с тем же id", async () => {
    renderReschedule(`/my-visits/${OLD_ID}/reschedule`);
    expect(await screen.findByRole("heading", { name: RESCHEDULE_COPY.title })).toBeInTheDocument();
    expect(mockedBooking).toHaveBeenCalledWith(OLD_ID);
  });
});
