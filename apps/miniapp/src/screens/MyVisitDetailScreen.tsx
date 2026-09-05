/** F-myvisits-detail — single booking detail with [Отменить] [Перенести]
 * action buttons (spec §3.4 + §5.3).
 *
 * Includes the cancel modal (with optional reason chips per spec §3.2
 * step 3) and the 5-second undo Snackbar.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { Snackbar } from "../components/Snackbar";
import { StateError } from "../components/StateError";
import {
  ApiError,
  cancelBookingConfirm,
  cancelBookingRequest,
  cancelBookingUndo,
  fetchBooking,
  type BookingItem,
  type CancelReasonClass,
} from "../lib/api";
import { formatVisitFull } from "../lib/format";
import { setMaster, setService, setVisitAt } from "../state/booking";
import { backTo } from "../lib/screen-back";

/** Возврат (DRF-1493): к легаси-списку записей. */
const BACK = backTo("/my-visits");

type State =
  | { kind: "loading" }
  | { kind: "ok"; booking: BookingItem }
  | { kind: "error"; err: unknown };

interface CancelModalState {
  open: boolean;
  reasonClass: CancelReasonClass | null;
}

const REASON_CHIPS: { value: CancelReasonClass; label: string }[] = [
  // Verbatim from spec §3.2 step 3.
  { value: "timing", label: "Не успеваю" },
  { value: "plans_changed", label: "Изменились планы" },
  { value: "not_needed", label: "Не нужна услуга сейчас" },
  { value: "other", label: "Другое" },
];

export function MyVisitDetailScreen() {
  const navigate = useNavigate();
  const { bookingId } = useParams<{ bookingId: string }>();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [modal, setModal] = useState<CancelModalState>({
    open: false,
    reasonClass: null,
  });
  const [snack, setSnack] = useState<{
    visible: boolean;
    message: string;
    showUndo: boolean;
  }>({ visible: false, message: "", showUndo: false });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    if (!bookingId) return;
    setState({ kind: "loading" });
    fetchBooking(bookingId)
      .then(({ booking }) => setState({ kind: "ok", booking }))
      .catch((err) => setState({ kind: "error", err }));
  }, [bookingId]);

  useEffect(() => load(), [load]);

  async function onConfirmCancel() {
    if (!bookingId) return;
    setBusy(true);
    try {
      const { booking } = await cancelBookingRequest(bookingId, {
        reason_class: modal.reasonClass ?? undefined,
      });
      setState({ kind: "ok", booking });
      setModal({ open: false, reasonClass: null });
      // Verbatim copy from spec §3.4: "Запись отменена · Отменить".
      setSnack({
        visible: true,
        message: "Запись отменена",
        showUndo: true,
      });
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
    // 5s elapsed — confirm cancel server-side.
    setSnack({ visible: false, message: "", showUndo: false });
    try {
      const { booking } = await cancelBookingConfirm(bookingId);
      setState({ kind: "ok", booking });
    } catch (err) {
      // Server says it can't commit — most likely already done by another tab.
      // Reload to get the truth.
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
        // Server's authoritative — window already closed.
        setSnack({
          visible: true,
          message: "Окно отмены истекло.",
          showUndo: false,
        });
        // Reload to reflect the final cancelled state.
        load();
        return;
      }
      // Optimistic UI fallback — reload.
      load();
    }
  }

  function onReschedule() {
    if (state.kind !== "ok") return;
    const b = state.booking;
    if (!b.service_id || !b.master_id) return;
    // Seed the booking draft with the existing service + master and
    // navigate into the slot picker. The reschedule flow piggybacks on
    // the booking-creation F2 + F3 screens — spec "Frontend §F-reschedule
    // flow — reuses F2 (master picker) + F3 (slot picker)".
    setService(b.service_id, b.service_name);
    setMaster(b.master_id, b.master_name);
    setVisitAt(null);
    navigate(`/my-visits/${b.id}/reschedule`);
  }

  if (state.kind === "loading") {
    return (
      <ScreenLayout back={BACK} title="Запись">
        <div className="callout">Загружаю…</div>
      </ScreenLayout>
    );
  }
  if (state.kind === "error") {
    return (
      <ScreenLayout back={BACK} title="Запись">
        <StateError err={state.err} onRetry={load} screenId="my-visit-detail" />
      </ScreenLayout>
    );
  }

  const b = state.booking;

  return (
    <ScreenLayout back={BACK} title={b.service_name || "Запись"}>
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
          <dt>Статус</dt>
          <dd>{statusLabel(b.status)}</dd>
        </dl>
      </div>

      {(b.cancellable || b.reschedulable) && (
        <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
          {b.reschedulable && (
            <button
              type="button"
              className="btn-secondary"
              onClick={onReschedule}
              data-testid="reschedule-button"
              style={{ flex: 1 }}
            >
              Перенести
            </button>
          )}
          {b.cancellable && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setModal({ open: true, reasonClass: null })}
              data-testid="cancel-button"
              style={{ flex: 1 }}
            >
              Отменить
            </button>
          )}
        </div>
      )}

      {/* Cancel confirmation modal — spec §3.4 verbatim copy. */}
      {modal.open && (
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
            if (e.target === e.currentTarget) setModal({ open: false, reasonClass: null });
          }}
        >
          <div
            className="modal__sheet"
            style={{
              background: "var(--c-surface-1)",
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
            <p style={{ color: "var(--c-text-secondary)" }}>
              {b.service_name}
              {b.master_name ? ` · ${b.master_name}` : ""}
            </p>
            <p style={{ marginTop: "var(--s-3)" }}>Что повлияло? (опционально)</p>
            <div className="chip-row">
              {REASON_CHIPS.map((r) => (
                <button
                  key={r.value}
                  type="button"
                  className={`chip ${modal.reasonClass === r.value ? "chip--active" : ""}`}
                  onClick={() =>
                    setModal((m) => ({
                      ...m,
                      reasonClass: m.reasonClass === r.value ? null : r.value,
                    }))
                  }
                >
                  {r.label}
                </button>
              ))}
            </div>
            <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setModal({ open: false, reasonClass: null })}
                style={{ flex: 1 }}
              >
                Назад
              </button>
              <button
                type="button"
                className="cta-bar__button"
                disabled={busy}
                onClick={onConfirmCancel}
                style={{ flex: 1 }}
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
        onTimeout={snack.showUndo ? onSnackbarTimeout : () => setSnack({ ...snack, visible: false })}
        onDismiss={() => setSnack({ ...snack, visible: false })}
      />
    </ScreenLayout>
  );
}

function statusLabel(s: BookingItem["status"]): string {
  switch (s) {
    case "confirmed":
      return "Подтверждена";
    case "cancel_requested":
      return "Отменяется";
    case "reschedule_requested":
      return "Переносится";
    case "cancelled":
      return "Отменена";
    case "rescheduled":
      return "Перенесена";
  }
}
