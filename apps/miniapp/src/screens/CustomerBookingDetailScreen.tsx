/**
 * Customer booking detail — REAL booking row (pilot phase 3.2).
 *
 * Route: `/customer/records/:bookingId`
 *
 * Spec: `docs/screens/customer-records-flow.md` §5 (R3). Phase 3.2
 * (stub removal): the stub detail rendered address / route deeplinks /
 * cancellation-policy text / refund amounts — NONE of those fields
 * exist in the real `GET /bookings/<id>` payload, so the screen shows
 * only what the backend serves (service, master, visit time, duration,
 * status, rating) plus actions wired to REAL endpoints:
 *
 *   - «Перенести» → `/my-visits/:id/reschedule` (real RescheduleScreen;
 *     gated by `reschedulable`);
 *   - «Отменить» → 2-step cancel with a 5s undo window — the proven
 *     flow mirrored from `MyVisitDetailScreen` (cancel request →
 *     snackbar undo → server confirm on timeout), gated by `cancellable`;
 *   - «Оценить визит» → `/feedback/:id` (real FeedbackScreen; gated by
 *     `can_rate` — past + confirmed + unrated);
 *   - «Записаться ещё» → `/customer/catalog` (history rows; no prefill
 *     endpoint exists in the pilot).
 *
 * 404 → honest «Этой записи больше нет» state (deleted booking race,
 * Tau §9.2). Voice: «ты», no exclamation marks.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Snackbar } from "../components/Snackbar";
import { StateError } from "../components/StateError";
import { PaymentStatusBadge } from "../components/PaymentStatusBadge";
import { StatusBadge } from "../components/StatusBadge";
import {
  ApiError,
  cancelBookingConfirm,
  cancelBookingRequest,
  cancelBookingUndo,
  type BookingItem,
  type CancelReasonClass,
} from "../lib/api";
import { displayStatusFor, getBookingDetail, renderStatus } from "../lib/customer-records";
import { formatDuration, formatMoney, formatVisitFull } from "../lib/format";

type State =
  | { kind: "loading" }
  | { kind: "ok"; booking: BookingItem }
  | { kind: "error"; err: unknown };

const REASON_CHIPS: { value: CancelReasonClass; label: string }[] = [
  // Verbatim from spec §3.2 step 3 (same as MyVisitDetailScreen).
  { value: "timing", label: "Не успеваю" },
  { value: "plans_changed", label: "Изменились планы" },
  { value: "not_needed", label: "Не нужна услуга сейчас" },
  { value: "other", label: "Другое" },
];

export function CustomerBookingDetailScreen() {
  const navigate = useNavigate();
  const { bookingId } = useParams<{ bookingId: string }>();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [modalOpen, setModalOpen] = useState(false);
  const [reasonClass, setReasonClass] = useState<CancelReasonClass | null>(null);
  const [snack, setSnack] = useState<{
    visible: boolean;
    message: string;
    showUndo: boolean;
  }>({ visible: false, message: "", showUndo: false });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    if (!bookingId) return;
    setState({ kind: "loading" });
    getBookingDetail(bookingId)
      .then((booking) => setState({ kind: "ok", booking }))
      .catch((err: unknown) => setState({ kind: "error", err }));
  }, [bookingId]);

  useEffect(() => load(), [load]);

  // --- 2-step cancel + undo (mirrors MyVisitDetailScreen verbatim) ----

  async function onConfirmCancel() {
    if (!bookingId) return;
    setBusy(true);
    try {
      const { booking } = await cancelBookingRequest(bookingId, {
        reason_class: reasonClass ?? undefined,
      });
      setState({ kind: "ok", booking });
      setModalOpen(false);
      setReasonClass(null);
      setSnack({ visible: true, message: "Запись отменена", showUndo: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setSnack({
          visible: true,
          message: err.detail || "Не получилось отменить.",
          showUndo: false,
        });
      }
    } finally {
      setBusy(false);
    }
  }

  async function onSnackbarTimeout() {
    if (!bookingId) return;
    // 5s elapsed — confirm the cancel server-side.
    setSnack({ visible: false, message: "", showUndo: false });
    try {
      const { booking } = await cancelBookingConfirm(bookingId);
      setState({ kind: "ok", booking });
    } catch {
      // Already committed by another tab — reload for the truth.
      load();
    }
  }

  async function onUndo() {
    if (!bookingId) return;
    try {
      const { booking } = await cancelBookingUndo(bookingId);
      setState({ kind: "ok", booking });
    } catch (err) {
      if (err instanceof ApiError && err.slug === "undo_window_elapsed") {
        setSnack({
          visible: true,
          message: "Окно отмены истекло.",
          showUndo: false,
        });
        load();
        return;
      }
      load();
    }
  }

  // --- states ----------------------------------------------------------

  if (state.kind === "loading") {
    return (
      <div className="records-screen">
        <header className="records-screen__header">
          <h1 className="records-screen__title">Запись</h1>
        </header>
        <div className="callout">Загружаю…</div>
      </div>
    );
  }

  if (state.kind === "error") {
    const gone =
      state.err instanceof ApiError && state.err.status === 404;
    return (
      <div className="records-screen">
        <header className="records-screen__header">
          <button
            type="button"
            className="records-screen__back"
            aria-label="Назад"
            onClick={() => navigate(-1)}
          >
            <span aria-hidden="true">←</span>
          </button>
          <h1 className="records-screen__title">Запись</h1>
        </header>
        {gone ? (
          <div className="callout" role="status">
            <p style={{ margin: 0 }}>
              Этой записи больше нет. Если отменял её не ты — напиши в
              поддержку из профиля.
            </p>
          </div>
        ) : (
          <StateError err={state.err} onRetry={load} screenId="customer-booking-detail" />
        )}
      </div>
    );
  }

  const b = state.booking;
  const { rendering } = renderStatus(displayStatusFor(b));
  const isHistoryRow =
    rendering.label !== "Подтверждена" || new Date(b.visit_at).getTime() < Date.now();

  return (
    <div className="records-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={() => navigate(-1)}
        >
          <span aria-hidden="true">←</span>
        </button>
        <h1 className="records-screen__title">{b.service_name || "Запись"}</h1>
      </header>

      <main className="records-screen__main">
        <div className="records-card__status-row">
          <StatusBadge rendering={rendering} />
          {/* C7.3 — payment status when the passthrough ships it. */}
          <PaymentStatusBadge state={b.payment?.capture_state} />
        </div>

        <div className="confirm-card">
          <dl>
            <dt>Время</dt>
            <dd>{formatVisitFull(b.visit_at)}</dd>
            {b.master_name && (
              <>
                <dt>Мастер</dt>
                <dd>{b.master_name}</dd>
              </>
            )}
            {b.duration_min != null && (
              <>
                <dt>Длительность</dt>
                <dd>{formatDuration(b.duration_min)}</dd>
              </>
            )}
            {b.payment?.amount && (
              <>
                <dt>Сумма</dt>
                <dd>{formatMoney(b.payment.amount)}</dd>
              </>
            )}
            {b.rating != null && (
              <>
                <dt>Оценка</dt>
                <dd>Оценка: {b.rating} из 5</dd>
              </>
            )}
          </dl>
        </div>

        {(b.cancellable || b.reschedulable) && (
          <div
            style={{
              display: "flex",
              gap: "var(--s-2)",
              marginTop: "var(--s-4)",
            }}
          >
            {b.reschedulable && (
              <button
                type="button"
                className="btn-secondary"
                style={{ flex: 1 }}
                onClick={() => navigate(`/my-visits/${b.id}/reschedule`)}
              >
                Перенести
              </button>
            )}
            {b.cancellable && (
              <button
                type="button"
                className="btn-secondary"
                style={{ flex: 1 }}
                onClick={() => setModalOpen(true)}
              >
                Отменить
              </button>
            )}
          </div>
        )}

        {b.can_rate && (
          <div style={{ marginTop: "var(--s-4)" }}>
            <button
              type="button"
              className="btn-primary"
              onClick={() => navigate(`/feedback/${b.id}`)}
            >
              Оценить визит
            </button>
          </div>
        )}

        {isHistoryRow && (
          <div style={{ marginTop: "var(--s-3)" }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate("/customer/catalog")}
            >
              Записаться ещё
            </button>
          </div>
        )}
      </main>

      {/* Cancel confirmation modal — spec §3.4 flow + reason chips. */}
      {modalOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Отменить запись"
          className="modal"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.4)",
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "center",
            zIndex: 50,
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              setModalOpen(false);
              setReasonClass(null);
            }
          }}
        >
          <div
            className="modal__sheet"
            style={{
              background: "var(--surface-1, #fff)",
              padding: "var(--s-4)",
              borderRadius: "var(--r-lg) var(--r-lg) 0 0",
              width: "100%",
              maxWidth: 480,
            }}
          >
            <h2 style={{ margin: 0 }}>Отменить запись?</h2>
            <p style={{ marginTop: "var(--s-2)", marginBottom: "var(--s-2)" }}>
              {formatVisitFull(b.visit_at)}
            </p>
            <p style={{ color: "var(--text-muted, #888)" }}>
              {b.service_name}
              {b.master_name ? ` · ${b.master_name}` : ""}
            </p>
            <p style={{ marginTop: "var(--s-3)" }}>Что повлияло? (опционально)</p>
            <div className="chip-row" style={{ flexWrap: "wrap" }}>
              {REASON_CHIPS.map((r) => (
                <button
                  key={r.value}
                  type="button"
                  className={`chip ${reasonClass === r.value ? "chip--active" : ""}`}
                  onClick={() =>
                    setReasonClass((cur) => (cur === r.value ? null : r.value))
                  }
                >
                  {r.label}
                </button>
              ))}
            </div>
            <div
              style={{
                display: "flex",
                gap: "var(--s-2)",
                marginTop: "var(--s-4)",
              }}
            >
              <button
                type="button"
                className="btn-secondary"
                style={{ flex: 1 }}
                onClick={() => {
                  setModalOpen(false);
                  setReasonClass(null);
                }}
              >
                Назад
              </button>
              <button
                type="button"
                className="cta-bar__button"
                style={{ flex: 1 }}
                disabled={busy}
                onClick={onConfirmCancel}
              >
                {busy ? "Отменяю…" : "Отменить запись"}
              </button>
            </div>
          </div>
        </div>
      )}

      <Snackbar
        visible={snack.visible}
        message={snack.message}
        actionLabel={snack.showUndo ? "Отменить" : undefined}
        onAction={snack.showUndo ? onUndo : undefined}
        durationMs={5000}
        onTimeout={
          snack.showUndo
            ? onSnackbarTimeout
            : () => setSnack({ visible: false, message: "", showUndo: false })
        }
        onDismiss={() => setSnack({ visible: false, message: "", showUndo: false })}
      />
    </div>
  );
}
