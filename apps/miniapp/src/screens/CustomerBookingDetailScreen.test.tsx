/**
 * Карточка записи клиента по макету «Записи.png» (DRF-2952), кадры 3, 4,
 * 8–10, 13. Слой HTTP (`../lib/api`) подменён; фикстуры — строки `BookingItem`.
 *
 * Главное здесь — отмена: экран не говорит «Запись отменена», пока это не
 * прочитано у сервера. Подмены поэтому метят ответ В МОМЕНТ ответа
 * (`markRead` внутри реализации), как настоящий клиент: метка, поставленная
 * заранее, считалась бы чтением «до нажатия» и исход законно не доказала бы.
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { primeDisplayName } from "../components/CustomerAvatarEntry";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    fetchBooking: vi.fn(),
    cancelBookingRequest: vi.fn(),
    cancelBookingConfirm: vi.fn(),
    cancelBookingUndo: vi.fn(),
  };
});

import {
  ApiError,
  cancelBookingConfirm,
  cancelBookingRequest,
  cancelBookingUndo,
  fetchBooking,
  type BookingItem,
} from "../lib/api";
import { markRead } from "../lib/claims";
import { formatDayWeekday } from "../lib/format";
import {
  CANCEL_STARTED_COPY,
  CustomerBookingDetailScreen,
  DETAIL_COPY,
} from "./CustomerBookingDetailScreen";

const mockedFetch = vi.mocked(fetchBooking);
const mockedRequest = vi.mocked(cancelBookingRequest);
const mockedConfirm = vi.mocked(cancelBookingConfirm);
const mockedUndo = vi.mocked(cancelBookingUndo);

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
    address: null,
    ...partial,
  };
}

const FUTURE = booking({ id: "b-1" });
/** Запись пути Ayla: окна возврата нет. */
const AYLA = booking({ id: "b-1", undo_window_seconds: 0 });
const PAST = booking({
  id: "b-2",
  visit_at: isoInHours(-30),
  cancellable: false,
  reschedulable: false,
  can_rate: true,
});

/** Ответ источника с меткой прочитанного — как его отдаёт настоящий клиент. */
function answered(row: BookingItem) {
  return async () => ({ booking: markRead({ ...row }, { source: "/bookings/b-1", status: 200 }) });
}

function Probe() {
  const params = useParams();
  return <div>PROBE-{JSON.stringify(params)}</div>;
}

function renderScreen(bookingId: string) {
  render(
    <MemoryRouter initialEntries={[`/customer/records/${bookingId}`]}>
      <Routes>
        <Route path="/customer/records" element={<div>RECORDS-PROBE</div>} />
        <Route path="/customer/records/:bookingId" element={<CustomerBookingDetailScreen />} />
        <Route path="/customer/records/:bookingId/reschedule" element={<Probe />} />
        <Route path="/feedback/:bookingId" element={<Probe />} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const cancelButton = () => screen.findByRole("button", { name: DETAIL_COPY.cancel });
const confirmYes = () => screen.getByRole("button", { name: DETAIL_COPY.confirmYes });

beforeEach(() => {
  vi.clearAllMocks();
  primeDisplayName("Тест Тестов");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("кадр 3 — карточка записи", () => {
  it("услуга, мастер со специализацией, день, время и цена — только с провода", async () => {
    mockedFetch.mockResolvedValue({
      booking: {
        ...FUTURE,
        master_specialization: "Мастер маникюра",
        salon_name: "Салон «Ромашка»",
        price: "2500.00",
      },
    });
    renderScreen("b-1");

    expect(await screen.findByRole("heading", { level: 1, name: "Маникюр" })).toBeInTheDocument();
    expect(screen.getByText("Анна Соколова")).toBeInTheDocument();
    expect(screen.getByText("Мастер маникюра")).toBeInTheDocument();
    expect(screen.getByText("Салон «Ромашка»")).toBeInTheDocument();
    expect(screen.getByText(formatDayWeekday(FUTURE.visit_at))).toBeInTheDocument();
    // Время визита на макете потеряно — на карточке оно обязано быть.
    expect(screen.getByText(/^\d{2}:\d{2} · 1 ч 30 мин$/)).toBeInTheDocument();
    expect(screen.getByText("2 500 ₽")).toBeInTheDocument();
    // Обычный статус не назван; пара — карточка отрисована (заголовок выше).
    expect(screen.queryByText("Подтверждена")).not.toBeInTheDocument();
  });

  it("чего провод не несёт, того на карточке нет: специализации, цены, расстояния, подготовки", async () => {
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");

    await screen.findByRole("heading", { level: 1, name: "Маникюр" });
    expect(screen.queryByText(/₽/)).not.toBeInTheDocument();
    expect(screen.queryByText(/км/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Подготовка/)).not.toBeInTheDocument();
    expect(screen.queryByText(/политика отмены/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/возврат/i)).not.toBeInTheDocument();
  });

  it("адрес известен — показан дословно", async () => {
    mockedFetch.mockResolvedValue({ booking: { ...FUTURE, address: "Пенза, Московская, 12" } });
    renderScreen("b-1");
    expect(await screen.findByText("Пенза, Московская, 12")).toBeInTheDocument();
  });

  it("салон сказал «адреса нет» — это ОТВЕТ, а не молчание", async () => {
    mockedFetch.mockResolvedValue({ booking: { ...FUTURE, address: "" } });
    renderScreen("b-1");
    expect(await screen.findByText("Адрес не указан")).toBeInTheDocument();
  });

  it("источник промолчал — человеку сказано, у кого спросить; строка адреса безусловна", async () => {
    mockedFetch.mockResolvedValue({ booking: { ...FUTURE, address: null, master_name: "" } });
    renderScreen("b-1");
    expect(await screen.findByText("Уточни адрес в салоне")).toBeInTheDocument();
    expect(screen.getByText("Адрес")).toBeInTheDocument();
  });

  it("«Перенести запись» ведёт на настоящий экран переноса", async () => {
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");
    await userEvent.setup().click(await screen.findByRole("button", { name: DETAIL_COPY.reschedule }));
    expect(await screen.findByText('PROBE-{"bookingId":"b-1"}')).toBeInTheDocument();
  });

  it("оплата: значок и сумма, когда провод их несёт; waiting_for_capture человеку не показывается", async () => {
    mockedFetch.mockResolvedValue({
      booking: { ...FUTURE, payment: { capture_state: "authorized", amount: "2000.00" } },
    });
    renderScreen("b-1");
    expect(await screen.findByText("Зарезервировано")).toBeInTheDocument();
    expect(screen.getByText(/2 000 ₽/)).toBeInTheDocument();
  });

  it("waiting_for_capture человеку не показывается (ADR)", async () => {
    mockedFetch.mockResolvedValue({
      booking: { ...FUTURE, payment: { capture_state: "waiting_for_capture" } },
    });
    renderScreen("b-1");
    await screen.findByRole("heading", { level: 1, name: "Маникюр" });
    expect(screen.queryByText(/capture/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Зарезервировано")).not.toBeInTheDocument();
  });

  it("возврат — к списку записей", async () => {
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");
    await screen.findByRole("heading", { level: 1, name: "Маникюр" });
    await userEvent.setup().click(screen.getByRole("button", { name: "Назад" }));
    expect(await screen.findByText("RECORDS-PROBE")).toBeInTheDocument();
  });
});

describe("кадр 4 — перенос недоступен", () => {
  it("пояснение на «ты» и без слова «слот»; «Перенести запись» нет, «Отменить запись» есть", async () => {
    mockedFetch.mockResolvedValue({ booking: { ...FUTURE, reschedulable: false } });
    renderScreen("b-1");

    expect(await screen.findByText(DETAIL_COPY.noReschedule)).toBeInTheDocument();
    expect(DETAIL_COPY.noReschedule).not.toMatch(/слот|отмените|выберите/i);
    expect(screen.queryByRole("button", { name: DETAIL_COPY.reschedule })).not.toBeInTheDocument();
    expect(await cancelButton()).toBeInTheDocument();
  });

  it("пояснения нет, когда перенос доступен, и нет у прошедшей записи", async () => {
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");
    expect(await screen.findByRole("button", { name: DETAIL_COPY.reschedule })).toBeInTheDocument();
    expect(screen.queryByText(DETAIL_COPY.noReschedule)).not.toBeInTheDocument();
  });

  it("прошедшая запись пояснения про перенос не получает", async () => {
    mockedFetch.mockResolvedValue({ booking: PAST });
    renderScreen("b-2");
    expect(await screen.findByText(DETAIL_COPY.finished)).toBeInTheDocument();
    expect(screen.queryByText(DETAIL_COPY.noReschedule)).not.toBeInTheDocument();
  });
});

describe("кадр 13 — прошедшая запись", () => {
  it("«Запись завершена», «Повторить запись» ведёт в каталог", async () => {
    mockedFetch.mockResolvedValue({ booking: PAST });
    renderScreen("b-2");

    expect(await screen.findByText(DETAIL_COPY.finished)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: DETAIL_COPY.cancel })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: DETAIL_COPY.repeat }));
    expect(await screen.findByText("CATALOG-PROBE")).toBeInTheDocument();
  });

  it("«Оценить визит» — на настоящий экран отзыва, когда сервер разрешил", async () => {
    mockedFetch.mockResolvedValue({ booking: PAST });
    renderScreen("b-2");
    await userEvent.setup().click(await screen.findByRole("button", { name: DETAIL_COPY.rate }));
    expect(await screen.findByText('PROBE-{"bookingId":"b-2"}')).toBeInTheDocument();
  });

  it("оценённая запись показывает оценку, кнопки оценки нет", async () => {
    mockedFetch.mockResolvedValue({ booking: { ...PAST, can_rate: false, rating: 5 } });
    renderScreen("b-2");
    expect(await screen.findByText("Оценка: 5 из 5")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: DETAIL_COPY.rate })).not.toBeInTheDocument();
  });

  it("флаги сервера времени не учитывают: у прошедшей записи переноса и отмены нет", async () => {
    // Так выглядит каждый прошедший визит локального пути: `confirmed`,
    // время позади, а `cancellable` и `reschedulable` по-прежнему `true`.
    mockedFetch.mockResolvedValue({
      booking: { ...PAST, cancellable: true, reschedulable: true },
    });
    renderScreen("b-2");

    expect(await screen.findByText(DETAIL_COPY.finished)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: DETAIL_COPY.repeat })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: DETAIL_COPY.reschedule })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: DETAIL_COPY.cancel })).not.toBeInTheDocument();
  });

  it("давно отменённая запись — карточка со статусом, а не кадр «Запись отменена»", async () => {
    // Чтение есть, статус `cancelled`, но это не исход нажатия на этом экране.
    mockedFetch.mockImplementation(
      answered({ ...PAST, status: "cancelled", can_rate: false }),
    );
    renderScreen("b-2");

    expect(await screen.findByText("Отменена")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: DETAIL_COPY.repeat })).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: DETAIL_COPY.cancelledTitle }),
    ).not.toBeInTheDocument();
  });
});

describe("кадр 8 — подтверждение отмены", () => {
  it("диалог с записью, о которой речь; Escape закрывает, запрос не уходит (#953)", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");

    await user.click(await cancelButton());
    const dialog = await screen.findByRole("dialog", { name: DETAIL_COPY.cancel });
    expect(within(dialog).getByRole("heading", { name: DETAIL_COPY.confirmTitle })).toBeInTheDocument();
    expect(within(dialog).getByText("Маникюр")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it("«Не отменять» возвращает к карточке", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");
    await user.click(await cancelButton());
    await user.click(screen.getByRole("button", { name: DETAIL_COPY.confirmNo }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // Фокус вернулся на кнопку, открывшую диалог.
    await vi.waitFor(async () => expect(await cancelButton()).toHaveFocus());
  });

  it("«без возможности восстановления» — только там, где окна возврата нет", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue({ booking: AYLA });
    renderScreen("b-1");
    await user.click(await cancelButton());

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(new RegExp(DETAIL_COPY.confirmFinal))).toBeInTheDocument();
    // Причину отмены на этом пути сервер не читает — и экран её не спрашивает.
    expect(within(dialog).queryByText(DETAIL_COPY.reasons)).not.toBeInTheDocument();
  });

  it("локальный путь: окно возврата есть — фразы «без возможности» нет, причина уходит на сервер", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    mockedRequest.mockImplementation(answered({ ...FUTURE, status: "cancel_requested" }));
    renderScreen("b-1");
    await user.click(await cancelButton());

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(DETAIL_COPY.confirmAgain)).toBeInTheDocument();
    expect(within(dialog).queryByText(new RegExp(DETAIL_COPY.confirmFinal))).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Не успеваю" }));
    await user.click(confirmYes());
    expect(mockedRequest).toHaveBeenCalledWith("b-1", { reason_class: "timing" });
  });
});

describe("отмена, локальный путь — окно возврата (DRF-2346)", () => {
  it("сервер сказал «запрошена» — сообщение о ходе дела и возврат, а не «отменена»", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    mockedRequest.mockImplementation(
      answered({ ...FUTURE, status: "cancel_requested", cancellable: false, reschedulable: false }),
    );
    mockedUndo.mockResolvedValue({ booking: FUTURE });
    renderScreen("b-1");

    await user.click(await cancelButton());
    await user.click(confirmYes());

    expect(await screen.findByText(CANCEL_STARTED_COPY)).toBeInTheDocument();
    expect(screen.queryByText(DETAIL_COPY.cancelledTitle)).not.toBeInTheDocument();
    // Возврат — запись снова живая, окончательное подтверждение не уходит.
    await user.click(screen.getByRole("button", { name: "Отменить" }));
    expect(mockedUndo).toHaveBeenCalledTimes(1);
    expect(mockedConfirm).not.toHaveBeenCalled();
  });

  it("окно истекло — подтверждение у сервера, и только после `cancelled` кадр 9", async () => {
    vi.useFakeTimers();
    mockedFetch.mockResolvedValue({ booking: FUTURE });
    mockedRequest.mockImplementation(
      answered({ ...FUTURE, status: "cancel_requested", cancellable: false, reschedulable: false }),
    );
    mockedConfirm.mockImplementation(answered({ ...FUTURE, status: "cancelled" }));
    renderScreen("b-1");
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: DETAIL_COPY.cancel }));
    await act(async () => {});
    fireEvent.click(confirmYes());
    await act(async () => {});
    expect(screen.getByText(CANCEL_STARTED_COPY)).toBeInTheDocument();
    expect(screen.queryByText(DETAIL_COPY.cancelledTitle)).not.toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    await act(async () => {});
    expect(mockedConfirm).toHaveBeenCalledTimes(1);
    expect(mockedUndo).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: DETAIL_COPY.cancelledTitle })).toBeInTheDocument();
  });
});

describe("отмена, путь Ayla — запись переводит событие, а не ответ на запрос", () => {
  /** Запрос отмены принят: 200 и запись в прежнем статусе. */
  function acceptedButUnchanged() {
    mockedRequest.mockImplementation(answered({ ...AYLA }));
  }

  it("ответ «принято» при прежнем статусе — «Отменяю запись…», а не «Запись отменена»", async () => {
    vi.useFakeTimers();
    mockedFetch.mockResolvedValue({ booking: AYLA });
    acceptedButUnchanged();
    renderScreen("b-1");
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: DETAIL_COPY.cancel }));
    await act(async () => {});
    fireEvent.click(confirmYes());
    await act(async () => {});

    expect(screen.getByRole("heading", { name: DETAIL_COPY.cancellingTitle })).toBeInTheDocument();
    expect(screen.queryByText(DETAIL_COPY.cancelledTitle)).not.toBeInTheDocument();
    expect(mockedConfirm).not.toHaveBeenCalled();
  });

  it("запись перечитывается; кадр 9 — когда прочитано `cancelled`", async () => {
    vi.useFakeTimers();
    mockedFetch.mockResolvedValue({ booking: AYLA });
    acceptedButUnchanged();
    renderScreen("b-1");
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: DETAIL_COPY.cancel }));
    await act(async () => {});
    fireEvent.click(confirmYes());
    await act(async () => {});

    mockedFetch.mockImplementation(answered({ ...AYLA, status: "cancelled", cancellable: false }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });

    expect(screen.getByRole("heading", { name: DETAIL_COPY.cancelledTitle })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: DETAIL_COPY.findMaster })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: DETAIL_COPY.toRecords })).toBeInTheDocument();
    // Отмену шлём один раз: дальше только чтение.
    expect(mockedRequest).toHaveBeenCalledTimes(1);
  });

  it("запись так и не перевели — «ещё обрабатывается», а не «отменена»", async () => {
    vi.useFakeTimers();
    mockedFetch.mockImplementation(answered({ ...AYLA }));
    acceptedButUnchanged();
    renderScreen("b-1");
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: DETAIL_COPY.cancel }));
    await act(async () => {});
    fireEvent.click(confirmYes());
    await act(async () => {});

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6 * 1500);
    });

    expect(screen.getByRole("heading", { name: DETAIL_COPY.pendingTitle })).toBeInTheDocument();
    expect(screen.queryByText(DETAIL_COPY.cancelledTitle)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: DETAIL_COPY.pendingRefresh })).toBeInTheDocument();
    expect(mockedRequest).toHaveBeenCalledTimes(1);
  });
});

describe("кадр 10 — отмена не удалась", () => {
  it("сервер отказал определённо — «Не удалось отменить запись»; повтор человек шлёт сам", async () => {
    const user = userEvent.setup();
    mockedFetch.mockImplementation(answered(FUTURE));
    mockedRequest.mockRejectedValue(new ApiError(409, "invalid_state", ""));
    renderScreen("b-1");

    await user.click(await cancelButton());
    await user.click(confirmYes());

    expect(await screen.findByRole("heading", { name: DETAIL_COPY.failedTitle })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Написать в поддержку" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(await screen.findByRole("dialog", { name: DETAIL_COPY.cancel })).toBeInTheDocument();
    expect(mockedRequest).toHaveBeenCalledTimes(1);
  });

  it("сбой, но запись уже отменена — это кадр 9, а не «не удалось»", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValueOnce({ booking: FUTURE });
    mockedRequest.mockImplementation(async () => {
      // Запрос дошёл, ответ потерялся: следующее чтение скажет правду.
      mockedFetch.mockImplementation(answered({ ...FUTURE, status: "cancelled" }));
      throw new TypeError("Failed to fetch");
    });
    renderScreen("b-1");

    await user.click(await cancelButton());
    await user.click(confirmYes());

    expect(await screen.findByRole("heading", { name: DETAIL_COPY.cancelledTitle })).toBeInTheDocument();
    expect(screen.queryByText(DETAIL_COPY.failedTitle)).not.toBeInTheDocument();
  });
});

describe("исход отмены неизвестен — экран не решает за сервер", () => {
  it("ответ потерян, а запись уже «отмена запрошена» — отмена идёт, «не удалось» не говорится", async () => {
    vi.useFakeTimers();
    mockedFetch.mockResolvedValueOnce({ booking: FUTURE });
    mockedRequest.mockImplementation(async () => {
      mockedFetch.mockImplementation(answered({ ...FUTURE, status: "cancel_requested" }));
      throw new TypeError("Failed to fetch");
    });
    renderScreen("b-1");
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: DETAIL_COPY.cancel }));
    await act(async () => {});
    fireEvent.click(confirmYes());
    await act(async () => {});

    expect(screen.getByRole("heading", { name: DETAIL_COPY.cancellingTitle })).toBeInTheDocument();
    expect(screen.queryByText(DETAIL_COPY.failedTitle)).not.toBeInTheDocument();

    // Сервер добил отмену — следующее чтение это покажет.
    mockedFetch.mockImplementation(answered({ ...FUTURE, status: "cancelled" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(screen.getByRole("heading", { name: DETAIL_COPY.cancelledTitle })).toBeInTheDocument();
  });

  it("сбой сервера (5xx): запись перечитывается, и только потом — «не удалось»", async () => {
    vi.useFakeTimers();
    mockedFetch.mockImplementation(answered(AYLA));
    mockedRequest.mockRejectedValue(new ApiError(502, "upstream_unavailable", ""));
    renderScreen("b-1");
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: DETAIL_COPY.cancel }));
    await act(async () => {});
    fireEvent.click(confirmYes());
    await act(async () => {});

    // Сразу «не удалось» не говорится: отмена могла дойти.
    expect(screen.queryByText(DETAIL_COPY.failedTitle)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: DETAIL_COPY.cancellingTitle })).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6 * 1500);
    });
    expect(screen.getByRole("heading", { name: DETAIL_COPY.failedTitle })).toBeInTheDocument();
    // «Принято» мы не знаем — и не говорим.
    expect(screen.queryByText(DETAIL_COPY.pendingTitle)).not.toBeInTheDocument();
    expect(mockedRequest).toHaveBeenCalledTimes(1);
  });

  it("окно возврата истекло, подтверждение упало, запись «отмена запрошена» — ждём, а не молчим", async () => {
    vi.useFakeTimers();
    mockedFetch.mockResolvedValueOnce({ booking: FUTURE });
    mockedRequest.mockImplementation(answered({ ...FUTURE, status: "cancel_requested" }));
    mockedConfirm.mockRejectedValue(new TypeError("Failed to fetch"));
    renderScreen("b-1");
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: DETAIL_COPY.cancel }));
    await act(async () => {});
    fireEvent.click(confirmYes());
    await act(async () => {});

    mockedFetch.mockImplementation(answered({ ...FUTURE, status: "cancel_requested" }));
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    await act(async () => {});
    expect(screen.getByRole("heading", { name: DETAIL_COPY.cancellingTitle })).toBeInTheDocument();
  });
});

describe("статус «в пути» — словами о ходе, не о сделанном (DRF-2346)", () => {
  it("`cancel_requested`: «Отмена обрабатывается», а не «Отменена»; «Повторить запись» нет", async () => {
    mockedFetch.mockResolvedValue({
      booking: { ...FUTURE, status: "cancel_requested", cancellable: false, reschedulable: false },
    });
    renderScreen("b-1");

    expect(await screen.findByText("Отмена обрабатывается")).toBeInTheDocument();
    expect(screen.queryByText("Отменена")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: DETAIL_COPY.repeat })).not.toBeInTheDocument();
  });

  it("`reschedule_requested`: «Перенос не завершён», а не «Перенесена»; отменить можно", async () => {
    mockedFetch.mockResolvedValue({
      booking: { ...FUTURE, status: "reschedule_requested", reschedulable: false },
    });
    renderScreen("b-1");

    expect(await screen.findByText("Перенос не завершён")).toBeInTheDocument();
    expect(screen.queryByText("Перенесена")).not.toBeInTheDocument();
    expect(await cancelButton()).toBeInTheDocument();
  });

  it("ожидает оплаты — запись впереди: «Повторить запись» нет, отмена есть", async () => {
    mockedFetch.mockResolvedValue({
      booking: { ...FUTURE, status: "pending_payment" as BookingItem["status"], reschedulable: false },
    });
    renderScreen("b-1");

    expect(await screen.findByText("Ожидает оплаты")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: DETAIL_COPY.repeat })).not.toBeInTheDocument();
    expect(await cancelButton()).toBeInTheDocument();
  });
});

describe("запись не открылась", () => {
  it("404 — «Этой записи больше нет», с дорогой назад к записям", async () => {
    mockedFetch.mockRejectedValue(new ApiError(404, "not_found", "booking not found"));
    renderScreen("b-gone");

    expect(await screen.findByRole("heading", { name: DETAIL_COPY.goneTitle })).toBeInTheDocument();
    expect(screen.queryByText("booking not found")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: DETAIL_COPY.toRecords }));
    expect(await screen.findByText("RECORDS-PROBE")).toBeInTheDocument();
  });

  it("сбой загрузки — кадр 15 с рабочим повтором", async () => {
    mockedFetch.mockRejectedValueOnce(new ApiError(500, "http_error", ""));
    renderScreen("b-1");
    expect(await screen.findByRole("heading", { name: DETAIL_COPY.loadFailed })).toBeInTheDocument();

    mockedFetch.mockResolvedValue({ booking: FUTURE });
    await userEvent.setup().click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Маникюр" })).toBeInTheDocument();
  });
});
