/**
 * «День салона» — the front desk's home screen (Phase 2).
 *
 * The salon has had the operational backend since 15.08 and no button to
 * reach it. This is the screen the administrator opens in the morning:
 * every master's day in one column each, in the salon's own timezone.
 *
 * Deliberate choices worth knowing before editing:
 *
 * - **Cancelled visits stay on the board, struck through.** An absent row
 *   and a cancelled row look identical otherwise, and telling them apart
 *   is most of what the front desk is asked in the morning.
 * - **A master with nothing booked keeps their column.** «Инна сегодня
 *   свободна» is an answer; a missing column is a question.
 * - **Orphan visits get their own block.** A booking whose specialist
 *   matches no master must never be silently dropped — the whole reason
 *   this surface exists is that invisible bookings cost the salon money.
 * - No phone number is rendered, because none is sent (DRF-1039).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import { StateError } from "../../components/StateError";
import {
  CANCEL_REASONS,
  cancelSalonBooking,
  completeSalonBooking,
  getBookingSlots,
  getBookingVersion,
  getSalonDay,
  rescheduleSalonBooking,
  RELEASED_VISIT_STATUSES,
  type BookingSlot,
  type CancelReasonCode,
  type SalonDayResponse,
  type SalonDayVisit,
} from "../../lib/admin-api";
import { setBackButton } from "../../lib/max-sdk";

/** `YYYY-MM-DD` for a Date, in that Date's own local fields. */
function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function shiftIsoDate(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  // Noon avoids the DST edge where midnight ± 1 day lands on the same date.
  const dt = new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1, 12);
  dt.setDate(dt.getDate() + days);
  return toIsoDate(dt);
}

/** Render the visit's clock time in the salon's timezone, not the device's. */
function formatTime(iso: string | null, timeZone: string): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone,
    }).format(new Date(iso));
  } catch {
    return "—";
  }
}

function formatDayTitle(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1, 12);
  const formatted = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    weekday: "long",
  }).format(dt);
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
}

function clientLabel(v: SalonDayVisit): string {
  const initial = v.client_last_initial ? ` ${v.client_last_initial}` : "";
  return `${v.client_first_name}${initial}`.trim() || "Гость";
}

function VisitRow({
  visit,
  timeZone,
  onCancel,
  onComplete,
  onMove,
  masterId,
}: {
  visit: SalonDayVisit;
  timeZone: string;
  onCancel: (visit: SalonDayVisit) => void;
  onComplete: (visit: SalonDayVisit) => void;
  onMove: (visit: SalonDayVisit, masterId: string) => void;
  /** Absent for orphan visits — see `movable`. */
  masterId?: string;
}) {
  const released = RELEASED_VISIT_STATUSES.has(visit.status);
  // Offered only where it can succeed. A cancelled or finished visit is
  // settled — Ayla answers 422 whoever asks — and a button that always
  // fails teaches the front desk to distrust the screen.
  const cancellable = !released && !visit.is_in_progress;
  // Closing is what everything downstream hangs on — commission, payment
  // capture, the review request, RFM — and until this button existed none
  // of it had ever run. Offered on anything still open, including a visit
  // in progress: the front desk closes it as the customer leaves.
  const closable = !released && visit.status !== "completed";
  // A closed visit HAPPENED — it must not be struck through, which on this
  // board means «the slot was freed». Same pixels for «состоялся» and
  // «отменён» is precisely the confusion the screen exists to prevent, and
  // it only became visible once there was a button to close one.
  const closed = visit.status === "completed";
  // Moving needs a service to ask the schedule about. Without it there is
  // no way to know which starts are bookable, and offering arbitrary
  // times would be the client inventing availability (§17).
  // Also needs a master: an orphan visit is one whose specialist matches
  // no master here, and there is nobody to ask for free time.
  const movable = cancellable && Boolean(visit.service_id) && Boolean(masterId);
  return (
    <li
      className="salon-day__visit"
      style={{
        display: "flex",
        gap: "var(--s-2)",
        padding: "var(--s-2) 0",
        opacity: released || closed ? 0.55 : 1,
        textDecoration: released ? "line-through" : "none",
      }}
    >
      {/* Start time, and under it the length of the visit — the mockup's
       * time column («09:00» / «60 мин»). `duration_min` has always been
       * in the /salon/day payload (admin-api.ts, `SalonDayVisit`); until
       * now the screen dropped it, so the front desk could see when a
       * visit starts but not how long the master is busy. Guarded on
       * `> 0` so a zero-length row renders the time alone rather than
       * «0 мин». */}
      <span
        style={{
          display: "flex",
          flexDirection: "column",
          fontVariantNumeric: "tabular-nums",
          minWidth: "3.5em",
        }}
      >
        <span>{formatTime(visit.start_at, timeZone)}</span>
        {visit.duration_min > 0 && (
          <span
            style={{
              fontSize: "var(--font-size-100)",
              color: "var(--c-text-secondary)",
            }}
          >
            {`${visit.duration_min} мин`}
          </span>
        )}
      </span>
      <span style={{ flex: 1 }}>
        <span style={{ fontWeight: 600 }}>{clientLabel(visit)}</span>
        {visit.service_name ? ` · ${visit.service_name}` : ""}
        {visit.is_in_progress && !closed && (
          <span
            className="badge"
            style={{ marginInlineStart: "var(--s-2)" }}
            aria-label="Визит идёт сейчас"
          >
            идёт
          </span>
        )}
        {closed && (
          <span
            className="badge"
            style={{
              marginInlineStart: "var(--s-2)",
              color: "var(--c-text-secondary)",
            }}
            aria-label="Визит закрыт"
          >
            закрыт
          </span>
        )}
      </span>
      {closable && (
        <button
          type="button"
          className="btn btn--ghost"
          style={{ padding: "0 var(--s-2)", fontSize: "0.85em" }}
          onClick={() => onComplete(visit)}
          aria-label={`Визит состоялся: ${clientLabel(visit)}, ${formatTime(
            visit.start_at,
            timeZone,
          )}`}
        >
          Состоялся
        </button>
      )}
      {movable && (
        <button
          type="button"
          className="btn btn--ghost"
          style={{ padding: "0 var(--s-2)", fontSize: "0.85em" }}
          onClick={() => onMove(visit, masterId as string)}
          aria-label={`Перенести визит: ${clientLabel(visit)}, ${formatTime(
            visit.start_at,
            timeZone,
          )}`}
        >
          Перенести
        </button>
      )}
      {cancellable && (
        <button
          type="button"
          className="btn btn--ghost"
          style={{ padding: "0 var(--s-2)", fontSize: "0.85em" }}
          onClick={() => onCancel(visit)}
          aria-label={`Отменить визит: ${clientLabel(visit)}, ${formatTime(
            visit.start_at,
            timeZone,
          )}`}
        >
          Отменить
        </button>
      )}
    </li>
  );
}

/**
 * Confirmation before a cancellation.
 *
 * Deliberately two steps, not one. The customer is told their
 * appointment is off, so a mis-tap is not recoverable by pressing again
 * — and the reason is asked for here because Ayla records it as the
 * salon's own claim about why.
 */
function CancelDialog({
  visit,
  timeZone,
  busy,
  onConfirm,
  onDismiss,
}: {
  visit: SalonDayVisit;
  timeZone: string;
  busy: boolean;
  onConfirm: (code: CancelReasonCode) => void;
  onDismiss: () => void;
}) {
  const [code, setCode] = useState<CancelReasonCode>("master_unavailable");
  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label="Отмена визита">
      <h3 className="section__title">Отменить визит?</h3>
      <p>
        {clientLabel(visit)} · {formatTime(visit.start_at, timeZone)}
        {visit.service_name ? ` · ${visit.service_name}` : ""}
      </p>
      <p className="muted">Клиент получит уведомление об отмене.</p>

      <fieldset style={{ border: 0, padding: 0, margin: "var(--s-3) 0" }}>
        <legend className="muted">Причина</legend>
        {CANCEL_REASONS.map((r) => (
          <label key={r.code} style={{ display: "block", padding: "var(--s-1) 0" }}>
            <input
              type="radio"
              name="cancel-reason"
              value={r.code}
              checked={code === r.code}
              onChange={() => setCode(r.code)}
            />{" "}
            {r.label}
          </label>
        ))}
      </fieldset>

      <div style={{ display: "flex", gap: "var(--s-2)" }}>
        <button type="button" className="btn" onClick={onDismiss} disabled={busy}>
          Не отменять
        </button>
        <button
          type="button"
          className="btn btn--danger"
          onClick={() => onConfirm(code)}
          disabled={busy}
        >
          {busy ? "Отменяем…" : "Отменить визит"}
        </button>
      </div>
    </div>
  );
}

/**
 * Confirmation before closing a visit.
 *
 * The version shown here is the one that will be sent. That is the whole
 * design: `expected_version` guards against acting on a booking that
 * changed since the operator looked, and a version fetched by the write
 * itself would always match and guard nothing. The pause between reading
 * and confirming is the window it covers.
 */
function CompleteDialog({
  visit,
  timeZone,
  version,
  busy,
  onConfirm,
  onDismiss,
}: {
  visit: SalonDayVisit;
  timeZone: string;
  version: { version: number; status: string } | null;
  busy: boolean;
  onConfirm: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label="Закрытие визита">
      <h3 className="section__title">Визит состоялся?</h3>
      <p>
        {clientLabel(visit)} · {formatTime(visit.start_at, timeZone)}
        {visit.service_name ? ` · ${visit.service_name}` : ""}
      </p>

      {version === null ? (
        <p className="muted">Читаем запись в расписании…</p>
      ) : (
        <p className="muted">
          {version.status === "confirmed"
            ? "После закрытия визит уйдёт в историю, а клиенту придёт запрос отзыва."
            : `Расписание считает эту запись «${version.status}». Проверьте, прежде чем закрывать.`}
        </p>
      )}

      <div style={{ display: "flex", gap: "var(--s-2)" }}>
        <button type="button" className="btn" onClick={onDismiss} disabled={busy}>
          Не сейчас
        </button>
        <button
          type="button"
          className="btn btn--primary"
          onClick={onConfirm}
          // Nothing to send until the canonical version has arrived —
          // and it is never invented locally.
          disabled={busy || version === null}
        >
          {busy ? "Закрываем…" : "Да, состоялся"}
        </button>
      </div>
    </div>
  );
}

/**
 * Choosing a new time for an existing booking.
 *
 * The times come from the schedule, never from the client: §17 forbids
 * offering a start nobody said was bookable, and Ayla re-checks at commit
 * regardless. An unreachable slot list is shown as «не смогли спросить» —
 * an empty list would read as «мастер занят весь день», which is the
 * opposite claim.
 */
function MoveDialog({
  visit,
  timeZone,
  date,
  version,
  slots,
  slotsState,
  busy,
  onPick,
  onDismiss,
}: {
  visit: SalonDayVisit;
  timeZone: string;
  date: string;
  version: { version: number; status: string } | null;
  slots: BookingSlot[];
  slotsState: "loading" | "ready" | "unavailable";
  busy: boolean;
  onPick: (slot: BookingSlot) => void;
  onDismiss: () => void;
}) {
  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label="Перенос визита">
      <h3 className="section__title">Перенести визит</h3>
      <p>
        {clientLabel(visit)} · сейчас {formatTime(visit.start_at, timeZone)}
        {visit.service_name ? ` · ${visit.service_name}` : ""}
      </p>
      <p className="muted">{formatDayTitle(date)} — свободное время того же мастера</p>

      {slotsState === "loading" && <p className="muted">Спрашиваем расписание…</p>}

      {slotsState === "unavailable" && (
        <p className="muted">
          Не смогли спросить расписание. Это не значит, что времени нет —
          попробуйте ещё раз.
        </p>
      )}

      {slotsState === "ready" && slots.length === 0 && (
        <p className="muted">В этот день у мастера нет свободного времени.</p>
      )}

      {slotsState === "ready" && slots.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: "var(--s-2) 0" }}>
          {slots.map((slot) => (
            <li key={slot.time}>
              <button
                type="button"
                className="sheet__item"
                // Nothing to send until the canonical version arrives.
                disabled={busy || version === null}
                onClick={() => onPick(slot)}
              >
                {slot.time}
              </button>
            </li>
          ))}
        </ul>
      )}

      <button type="button" className="btn" onClick={onDismiss} disabled={busy}>
        Не переносить
      </button>
    </div>
  );
}

export function AdminSalonDayScreen() {
  const navigate = useNavigate();
  const today = useMemo(() => toIsoDate(new Date()), []);
  const [date, setDate] = useState<string>(today);
  const [day, setDay] = useState<SalonDayResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown>(null);
  const [cancelling, setCancelling] = useState<SalonDayVisit | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [closing, setClosing] = useState<SalonDayVisit | null>(null);
  const [closingVersion, setClosingVersion] = useState<
    { version: number; status: string } | null
  >(null);
  const [closeBusy, setCloseBusy] = useState(false);
  const [moving, setMoving] = useState<SalonDayVisit | null>(null);
  const [moveVersion, setMoveVersion] = useState<
    { version: number; status: string } | null
  >(null);
  const [moveSlots, setMoveSlots] = useState<BookingSlot[]>([]);
  const [moveSlotsState, setMoveSlotsState] = useState<
    "loading" | "ready" | "unavailable"
  >("loading");
  const [moveBusy, setMoveBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setBackButton(false);
  }, []);

  const load = useCallback(
    async (target: string, signal?: AbortSignal) => {
      setLoading(true);
      setErr(null);
      try {
        const res = await getSalonDay(target, { signal });
        if (signal?.aborted) return;
        setDay(res);
      } catch (e) {
        if ((e as DOMException | undefined)?.name === "AbortError") return;
        setErr(e);
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(date, controller.signal);
    return () => controller.abort();
  }, [date, load]);

  const beginComplete = useCallback((visit: SalonDayVisit) => {
    // Open the dialog first, then read: the operator sees the visit they
    // tapped immediately, and the canonical version arrives into it.
    setClosing(visit);
    setClosingVersion(null);
    setNotice(null);
    void (async () => {
      try {
        const v = await getBookingVersion(visit.id);
        setClosingVersion({ version: v.version, status: v.status });
      } catch {
        // No version means no action. Closing the dialog rather than
        // offering a button we would have to aim blind.
        setClosing(null);
        setNotice("Не удалось прочитать запись в расписании. Попробуйте ещё раз.");
      }
    })();
  }, []);

  const confirmComplete = useCallback(async () => {
    const visit = closing;
    const version = closingVersion;
    if (!visit || version === null || closeBusy) return;
    setCloseBusy(true);
    try {
      const res = await completeSalonBooking(visit.id, version.version);
      setClosing(null);
      switch (res.outcome) {
        case "committed":
          setNotice("Визит закрыт.");
          break;
        case "conflict":
          setNotice("Запись изменилась — день обновлён, посмотрите ещё раз.");
          break;
        case "pending":
          setNotice(
            "Расписание не ответило. Возможно, визит закрыт — проверьте день, прежде чем повторять.",
          );
          break;
        case "blocked":
          setNotice(res.detail || "Этот визит нельзя закрыть.");
          break;
        default:
          setNotice(res.detail || "Не удалось закрыть визит.");
      }
      if (res.outcome !== "blocked") await load(date);
    } finally {
      setCloseBusy(false);
    }
  }, [closing, closingVersion, closeBusy, date, load]);

  const beginMove = useCallback(
    (visit: SalonDayVisit, masterId: string) => {
      setMoving(visit);
      setMoveVersion(null);
      setMoveSlots([]);
      setMoveSlotsState("loading");
      setNotice(null);
      // Two independent asks: the canonical version (what we will send
      // back) and the bookable starts (what we may offer). Neither is
      // guessed if the other fails.
      void (async () => {
        try {
          const v = await getBookingVersion(visit.id);
          setMoveVersion({ version: v.version, status: v.status });
        } catch {
          setMoving(null);
          setNotice("Не удалось прочитать запись в расписании. Попробуйте ещё раз.");
          return;
        }
        try {
          const res = await getBookingSlots({
            masterId,
            serviceId: visit.service_id,
            date,
          });
          setMoveSlots(res.slots);
          setMoveSlotsState("ready");
        } catch {
          // «Could not ask» — never rendered as «no free time» (§16).
          setMoveSlotsState("unavailable");
        }
      })();
    },
    [date],
  );

  const pickNewSlot = useCallback(
    async (slot: BookingSlot) => {
      const visit = moving;
      const version = moveVersion;
      if (!visit || version === null || moveBusy) return;
      // The schedule's own ISO start, never one rebuilt from «HH:MM»:
      // choosing a timezone here would be the client computing an
      // authoritative moment (§17).
      if (!slot.start_at) {
        setNotice("Расписание не назвало точное время этого слота — обновите день.");
        return;
      }
      setMoveBusy(true);
      try {
        const res = await rescheduleSalonBooking(
          visit.id,
          version.version,
          slot.start_at,
        );
        setMoving(null);
        switch (res.outcome) {
          case "committed":
            setNotice(`Визит перенесён на ${slot.time}.`);
            break;
          case "conflict":
            setNotice(res.detail);
            break;
          case "pending":
            setNotice(
              "Расписание не ответило. Возможно, перенос прошёл — проверьте день, прежде чем повторять.",
            );
            break;
          case "blocked":
            setNotice(res.detail || "Этот визит нельзя перенести.");
            break;
          default:
            setNotice(res.detail || "Не удалось перенести визит.");
        }
        if (res.outcome !== "blocked") await load(date);
      } finally {
        setMoveBusy(false);
      }
    },
    [moving, moveVersion, moveBusy, date, load],
  );

  const confirmCancel = useCallback(
    async (code: CancelReasonCode) => {
      const visit = cancelling;
      if (!visit || cancelBusy) return;
      setCancelBusy(true);
      try {
        const res = await cancelSalonBooking(visit.id, { reason_code: code });
        setCancelling(null);

        // Every outcome except «blocked» ends with a reload, because in
        // each of them the day on screen may no longer be the day that
        // exists — including `pending`, where the cancellation may well
        // have landed. Refreshing is how the receptionist finds out,
        // and it is safer than any message we could invent.
        switch (res.outcome) {
          case "committed":
            setNotice("Визит отменён. Клиент получит уведомление.");
            break;
          case "conflict":
            setNotice("Запись успели изменить — день обновлён.");
            break;
          case "pending":
            setNotice(
              "Расписание не ответило. Возможно, отмена прошла — проверьте день, прежде чем повторять.",
            );
            break;
          case "blocked":
            setNotice(res.detail || "Этот визит нельзя отменить.");
            break;
          default:
            setNotice(res.detail || "Не удалось отменить.");
        }
        if (res.outcome !== "blocked") await load(date);
      } finally {
        setCancelBusy(false);
      }
    },
    [cancelling, cancelBusy, date, load],
  );

  const tz = day?.timezone ?? "Europe/Moscow";
  const busyMasters = day?.masters.filter((m) => m.visits.length > 0) ?? [];
  const freeMasters = day?.masters.filter((m) => m.visits.length === 0) ?? [];

  return (
    <div className="screen admin-flow-screen">
      <header
        className="screen__header"
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}
      >
        <h1 className="screen__title">День салона</h1>
        {/* UX contract §12 — manual booking opens «from Schedule or an
            allowed free interval». The day carries the date forward so
            the picker starts where the receptionist was looking. */}
        <button
          type="button"
          className="btn-secondary"
          onClick={() => navigate(`/admin/booking/new?date=${date}`)}
        >
          Записать
        </button>
      </header>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--s-2)",
        }}
      >
        <button
          type="button"
          className="btn-secondary"
          aria-label="Предыдущий день"
          onClick={() => setDate((d) => shiftIsoDate(d, -1))}
        >
          ←
        </button>
        <div style={{ textAlign: "center", flex: 1 }}>
          <div style={{ fontWeight: 600 }}>{formatDayTitle(date)}</div>
          {date !== today && (
            <button
              type="button"
              className="btn-link"
              onClick={() => setDate(today)}
              style={{ fontSize: "var(--font-size-100)" }}
            >
              Вернуться к сегодня
            </button>
          )}
        </div>
        <button
          type="button"
          className="btn-secondary"
          aria-label="Следующий день"
          onClick={() => setDate((d) => shiftIsoDate(d, 1))}
        >
          →
        </button>
      </div>

      {loading && (
        <div className="callout" role="status" style={{ marginTop: "var(--s-3)" }}>
          Собираю день…
        </div>
      )}

      {!loading && err != null && (
        <StateError err={err} onRetry={() => void load(date)} />
      )}

      {!loading && err == null && day && (
        <>
          <div
            className="callout"
            aria-live="polite"
            style={{ marginTop: "var(--s-3)" }}
          >
            {day.summary.total === 0
              ? "На этот день записей нет."
              : `Записей: ${day.summary.total} · впереди ${day.summary.upcoming} · завершено ${day.summary.completed}${
                  day.summary.released > 0 ? ` · отменено ${day.summary.released}` : ""
                }`}
          </div>

          {day.orphan_visits.length > 0 && (
            <div
              className="callout callout--warning"
              role="status"
              style={{ marginTop: "var(--s-3)" }}
            >
              <div style={{ fontWeight: 600, marginBottom: "var(--s-1)" }}>
                Записи без мастера
              </div>
              <p style={{ margin: 0 }}>
                Эти записи есть в расписании, но мастер по ним не определён.
                Проверьте карточки мастеров.
              </p>
              <ul style={{ listStyle: "none", padding: 0, margin: "var(--s-2) 0 0" }}>
                {day.orphan_visits.map((v) => (
                  <VisitRow
                    key={v.id}
                    visit={v}
                    timeZone={tz}
                    onCancel={setCancelling}
                    onComplete={beginComplete}
                    onMove={beginMove}
                  />
                ))}
              </ul>
            </div>
          )}

          {busyMasters.map((m) => (
            <section key={m.master_id} style={{ marginTop: "var(--s-4)" }}>
              <h2 className="section__title" style={{ marginBottom: "var(--s-1)" }}>
                {m.name}
              </h2>
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {m.visits.map((v) => (
                  <VisitRow
                    key={v.id}
                    visit={v}
                    timeZone={tz}
                    masterId={m.master_id}
                    onCancel={setCancelling}
                    onComplete={beginComplete}
                    onMove={beginMove}
                  />
                ))}
              </ul>
            </section>
          ))}

          {freeMasters.length > 0 && (
            <section style={{ marginTop: "var(--s-4)" }}>
              <h2 className="section__title" style={{ marginBottom: "var(--s-1)" }}>
                Свободны весь день
              </h2>
              <p style={{ margin: 0, color: "var(--c-text-secondary)" }}>
                {freeMasters.map((m) => m.name).join(", ")}
              </p>
            </section>
          )}
        </>
      )}

      {notice && (
        <p
          role="status"
          style={{ marginTop: "var(--s-3)", color: "var(--c-text-secondary)" }}
        >
          {notice}
        </p>
      )}

      {moving && (
        <MoveDialog
          visit={moving}
          timeZone={tz}
          date={date}
          version={moveVersion}
          slots={moveSlots}
          slotsState={moveSlotsState}
          busy={moveBusy}
          onPick={(slot) => void pickNewSlot(slot)}
          onDismiss={() => setMoving(null)}
        />
      )}

      {closing && (
        <CompleteDialog
          visit={closing}
          timeZone={tz}
          version={closingVersion}
          busy={closeBusy}
          onConfirm={() => void confirmComplete()}
          onDismiss={() => setClosing(null)}
        />
      )}

      {cancelling && (
        <CancelDialog
          visit={cancelling}
          timeZone={tz}
          busy={cancelBusy}
          onConfirm={(code) => void confirmCancel(code)}
          onDismiss={() => setCancelling(null)}
        />
      )}

      <AdminTabBar />
    </div>
  );
}
