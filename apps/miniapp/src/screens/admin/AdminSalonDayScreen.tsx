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
  getSalonDay,
  RELEASED_VISIT_STATUSES,
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
}: {
  visit: SalonDayVisit;
  timeZone: string;
  onCancel: (visit: SalonDayVisit) => void;
}) {
  const released = RELEASED_VISIT_STATUSES.has(visit.status);
  // Offered only where it can succeed. A cancelled or finished visit is
  // settled — Ayla answers 422 whoever asks — and a button that always
  // fails teaches the front desk to distrust the screen.
  const cancellable = !released && !visit.is_in_progress;
  return (
    <li
      className="salon-day__visit"
      style={{
        display: "flex",
        gap: "var(--s-2)",
        padding: "var(--s-2) 0",
        opacity: released ? 0.55 : 1,
        textDecoration: released ? "line-through" : "none",
      }}
    >
      <span style={{ fontVariantNumeric: "tabular-nums", minWidth: "3.5em" }}>
        {formatTime(visit.start_at, timeZone)}
      </span>
      <span style={{ flex: 1 }}>
        <span style={{ fontWeight: 600 }}>{clientLabel(visit)}</span>
        {visit.service_name ? ` · ${visit.service_name}` : ""}
        {visit.is_in_progress && (
          <span
            className="badge"
            style={{ marginInlineStart: "var(--s-2)" }}
            aria-label="Визит идёт сейчас"
          >
            идёт
          </span>
        )}
      </span>
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

export function AdminSalonDayScreen() {
  const navigate = useNavigate();
  const today = useMemo(() => toIsoDate(new Date()), []);
  const [date, setDate] = useState<string>(today);
  const [day, setDay] = useState<SalonDayResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown>(null);
  const [cancelling, setCancelling] = useState<SalonDayVisit | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
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
                  <VisitRow key={v.id} visit={v} timeZone={tz} onCancel={setCancelling} />
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
                  <VisitRow key={v.id} visit={v} timeZone={tz} onCancel={setCancelling} />
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
