/** F-reschedule — pick a new slot for an existing booking.
 *
 * Spec §5.3 reschedule modal + confirmation. Reuses the slot picker
 * pattern from BookingWhenScreen — same date strip + slot grid +
 * selection model.
 *
 * Flow:
 *   1. Customer arrives with draft.serviceId/masterId pre-filled from
 *      MyVisitDetailScreen (the existing booking's service + master).
 *   2. They pick a new slot → "Подобрать слоты" / direct tap.
 *   3. Confirmation modal shows old → new diff.
 *   4. On confirm we POST /reschedule + /reschedule/confirm
 *      sequentially. Spec §5.3 final screen copy: "Перенесена…".
 *
 * Куда ведут выходы (DRF-1480) — в каноническое `/customer/*`, а не в
 * старое поколение. После успешного переноса это новая карточка
 * `/customer/records/:id` (`CustomerBookingDetailScreen`). Обе карточки
 * читают один `GET /bookings/<id>`, поэтому id переносится как есть, но
 * старая `MyVisitDetailScreen` не рисует статус оплаты, сумму и
 * выставленную оценку и не имеет кнопки «Оценить визит» — человек после
 * переноса терял возможность оценить визит. «Каталог» из состояния
 * «сирота» — тоже новый `/customer/catalog`. Образец перевода —
 * `CustomerBookingSuccessScreen`. Старые маршруты остаются смонтированы
 * для внешних ссылок; уборка поколений — отдельная задача (DRF-1481).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { Snackbar } from "../components/Snackbar";
import { StickyCta } from "../components/StickyCta";
import { DelayedSkeleton, Skeleton, SlotGridSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { useHaptics } from "../hooks/useHaptics";
import {
  ApiError,
  fetchBooking,
  fetchSlots,
  rescheduleBookingConfirm,
  rescheduleBookingRequest,
  type BookingItem,
  type FreeSlot,
} from "../lib/api";
import { formatDateLabel, formatDayStrip, formatSlotTime, formatVisitFull } from "../lib/format";
import { resetBooking } from "../state/booking";
import { backTo } from "../lib/screen-back";

function isoDateNDaysAhead(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; booking: BookingItem; slots: FreeSlot[] }
  | { kind: "orphan"; booking: BookingItem }
  | { kind: "error"; err: unknown };

export function RescheduleScreen() {
  const navigate = useNavigate();
  const haptics = useHaptics();
  const { bookingId } = useParams<{ bookingId: string }>();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [pickedSlot, setPickedSlot] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Возврат (DRF-1493) — к самой записи, которую переносят. Адрес несёт
  // её id, поэтому родитель известен и при входе по ссылке из бота.
  const back = backTo(
    bookingId ? `/my-visits/${bookingId}` : "/my-visits",
  );

  const load = useCallback(() => {
    if (!bookingId) return;
    setState({ kind: "loading" });
    let cancelled = false;
    fetchBooking(bookingId)
      .then(({ booking }) => {
        // Orphan path: booking's service_id or master_id is null. Happens
        // when the catalog row was deleted (e.g. dev catalog re-seed)
        // after the booking was made — FK was set to NULL by on_delete.
        // We can't fetch slots without those IDs; show a graceful
        // "запишитесь заново" panel instead of a wall-of-red error.
        if (!booking.service_id || !booking.master_id) {
          if (!cancelled) setState({ kind: "orphan", booking });
          return null;
        }
        return Promise.all([
          Promise.resolve(booking),
          fetchSlots({
            serviceId: booking.service_id,
            masterId: booking.master_id,
            dateFrom: isoDateNDaysAhead(0),
            dateTo: isoDateNDaysAhead(14),
          }),
        ]);
      })
      .then((pair) => {
        if (cancelled || pair === null) return;
        const [booking, { slots }] = pair;
        setState({ kind: "ok", booking, slots });
        if (slots.length > 0 && slots[0]) setSelectedDate(slots[0].date);
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, [bookingId]);

  useEffect(() => load(), [load]);

  const slotsByDate = useMemo(() => {
    if (state.kind !== "ok") return new Map<string, FreeSlot[]>();
    const m = new Map<string, FreeSlot[]>();
    for (const s of state.slots) {
      const arr = m.get(s.date) ?? [];
      arr.push(s);
      m.set(s.date, arr);
    }
    return m;
  }, [state]);

  const availableDates = useMemo(() => Array.from(slotsByDate.keys()), [slotsByDate]);
  const slotsForDay = selectedDate ? slotsByDate.get(selectedDate) ?? [] : [];

  async function onConfirm() {
    if (!bookingId || !pickedSlot || state.kind !== "ok") return;
    const b = state.booking;
    if (!b.service_id || !b.master_id) return;
    setConfirming(true);
    setError(null);
    try {
      // request → stash candidate.
      await rescheduleBookingRequest(bookingId, {
        new_master_id: b.master_id,
        new_service_id: b.service_id,
        new_visit_at: pickedSlot,
      });
      // confirm → commit (creates new booking).
      const { new_booking } = await rescheduleBookingConfirm(bookingId);
      resetBooking();
      // Spec §5.3 confirmation: "Перенесена — было … стало …".
      navigate(`/customer/records/${new_booking.id}`, {
        state: { justRescheduled: true, oldVisit: b.visit_at },
        replace: true,
      });
    } catch (err) {
      if (err instanceof ApiError && err.slug === "slot_unavailable") {
        setError("Этот слот только что заняли. Выберите другое время.");
        // Reload slots — the picked one is now gone.
        load();
      } else if (err instanceof ApiError) {
        setError(err.detail || "Не получилось перенести.");
      } else {
        setError("Не получилось перенести. Проверьте интернет.");
      }
      setConfirming(false);
    }
  }

  if (state.kind === "loading") {
    return (
      <ScreenLayout back={back} title="Перенести">
        <DelayedSkeleton loading>
          <div className="date-strip">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} width="64px" height="60px" radius="var(--r-md)" />
            ))}
          </div>
          <SlotGridSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout back={back} title="Перенести">
        <StateError err={state.err} onRetry={load} screenId="reschedule" />
      </ScreenLayout>
    );
  }

  if (state.kind === "orphan") {
    return (
      <ScreenLayout back={back} title="Перенести">
        <div className="callout">
          <p style={{ margin: 0 }}>
            Эту услугу нельзя перенести — она больше не предлагается в текущем
            каталоге. Запишитесь заново.
          </p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-3)" }}
            onClick={() => navigate("/customer/catalog")}
          >
            Каталог
          </button>
        </div>
      </ScreenLayout>
    );
  }

  const b = state.booking;

  return (
    <ScreenLayout
      back={back}
      title="Перенести"
      cta={
        <StickyCta onClick={onConfirm} disabled={!pickedSlot || confirming}>
          {confirming ? "Переношу…" : pickedSlot ? "Подтвердить перенос" : "Выберите слот"}
        </StickyCta>
      }
    >
      {/* Old → new diff per spec §5.3 confirmation card. */}
      <div className="confirm-card">
        <dl>
          <dt>Сейчас</dt>
          <dd>{formatVisitFull(b.visit_at)}</dd>
          {b.service_name && (
            <>
              <dt>Услуга</dt>
              <dd>{b.service_name}</dd>
            </>
          )}
          {b.master_name && (
            <>
              <dt>Мастер</dt>
              <dd>{b.master_name}</dd>
            </>
          )}
          {pickedSlot && (
            <>
              <dt>Новое</dt>
              <dd>{formatVisitFull(pickedSlot)}</dd>
            </>
          )}
        </dl>
      </div>

      {state.slots.length === 0 ? (
        <div className="callout" style={{ marginTop: "var(--s-3)" }}>
          <p style={{ margin: 0 }}>
            На ближайшие две недели свободного времени нет.
          </p>
        </div>
      ) : (
        <>
          <div
            className="date-strip"
            role="tablist"
            aria-label="Дата визита"
            style={{ marginTop: "var(--s-3)" }}
          >
            {availableDates.map((d) => {
              const ds = formatDayStrip(d);
              const active = selectedDate === d;
              return (
                <button
                  type="button"
                  key={d}
                  role="tab"
                  aria-selected={active}
                  className={`date-strip__day ${active ? "date-strip__day--active" : ""}`}
                  onClick={() => {
                    haptics.selection();
                    setSelectedDate(d);
                  }}
                >
                  <div className="date-strip__day-name">{ds.name}</div>
                  <div className="date-strip__day-num">{ds.num}</div>
                </button>
              );
            })}
          </div>

          <div className="slot-grid" role="radiogroup" aria-label="Свободные слоты">
            {slotsForDay.length === 0 ? (
              <div className="slot-grid__empty">
                {selectedDate
                  ? `На ${formatDateLabel(selectedDate)} свободного времени нет.`
                  : "Выберите дату."}
              </div>
            ) : (
              slotsForDay.map((s) => {
                const active = pickedSlot === s.start;
                return (
                  <button
                    type="button"
                    key={s.start}
                    role="radio"
                    aria-checked={active}
                    className={`slot-grid__cell ${active ? "slot-grid__cell--active" : ""}`}
                    onClick={() => {
                      haptics.selection();
                      setPickedSlot(s.start);
                    }}
                  >
                    {formatSlotTime(s.start)}
                  </button>
                );
              })
            )}
          </div>
        </>
      )}

      <Snackbar
        visible={error !== null}
        message={error ?? ""}
        durationMs={4000}
        onTimeout={() => setError(null)}
        onDismiss={() => setError(null)}
      />
    </ScreenLayout>
  );
}
