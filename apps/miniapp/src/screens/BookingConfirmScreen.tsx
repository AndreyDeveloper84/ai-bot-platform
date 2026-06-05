/** F4 — Confirmation. Spec §8 F4. */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, createBooking } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { useBackButton } from "../hooks/useBackButton";
import { useClosingConfirmation } from "../hooks/useClosingConfirmation";
import { useHaptics } from "../hooks/useHaptics";
import { formatVisitFull } from "../lib/format";
import { resetBooking, useBookingDraft } from "../state/booking";

type ErrState =
  | { kind: "slot_unavailable" }
  | { kind: "server" }
  | { kind: "network" }
  | { kind: "other"; detail: string };

export function BookingConfirmScreen() {
  const navigate = useNavigate();
  const draft = useBookingDraft();
  const haptics = useHaptics();
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<ErrState | null>(null);

  useBackButton({ onBack: () => navigate(-1) });
  useClosingConfirmation(true);

  if (!draft.serviceId || !draft.masterId || !draft.visitAt) {
    navigate("/catalog", { replace: true });
    return null;
  }

  async function onConfirm() {
    if (!draft.serviceId || !draft.masterId || !draft.visitAt) return;
    setSubmitting(true);
    setErr(null);
    try {
      // Reschedule flow now lives in dev's dedicated RescheduleScreen
      // (route /my-visits/:bookingId/reschedule); this confirm screen is
      // create-only post-merge.
      const { booking } = await createBooking({
        service_id: draft.serviceId,
        master_id: draft.masterId,
        visit_at: draft.visitAt,
      });
      haptics.notify("success");
      resetBooking();
      navigate(`/book/success/${booking.id}`, {
        state: {
          service_name: booking.service_name,
          master_name: booking.master_name,
          visit_at: booking.visit_at,
        },
        replace: true,
      });
    } catch (e: unknown) {
      haptics.notify("error");
      if (e instanceof ApiError && e.slug === "slot_unavailable") {
        setErr({ kind: "slot_unavailable" });
      } else if (e instanceof ApiError && e.status >= 500) {
        setErr({ kind: "server" });
      } else if (e instanceof ApiError) {
        setErr({ kind: "other", detail: e.detail });
      } else {
        setErr({ kind: "network" });
      }
      setSubmitting(false);
    }
  }

  return (
    <ScreenLayout
      title={false ? "Перенос записи" : "Подтверждение"}
      cta={
        <StickyCta onClick={onConfirm} disabled={submitting}>
          {submitting
            ? false
              ? "Переношу…"
              : "Записываю…"
            : false
              ? "Подтвердить перенос"
              : "Подтвердить запись"}
        </StickyCta>
      }
    >
      <div className="confirm-card">
        <dl>
          <dt>Услуга</dt>
          <dd>{draft.serviceName}</dd>
          <dt>Мастер</dt>
          <dd>{draft.masterName}</dd>
          <dt>Время</dt>
          <dd>{formatVisitFull(draft.visitAt)}</dd>
        </dl>
      </div>

      {err?.kind === "slot_unavailable" && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>Этот слот только что заняли.</p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-3)" }}
            onClick={() => navigate("/book/when")}
          >
            Выбрать другое время
          </button>
        </div>
      )}
      {err?.kind === "server" && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>Не получилось. Попробуем ещё раз?</p>
        </div>
      )}
      {err?.kind === "network" && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>Не получилось загрузить. Проверьте интернет и попробуйте снова.</p>
        </div>
      )}
      {err?.kind === "other" && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>{err.detail}</p>
        </div>
      )}
    </ScreenLayout>
  );
}
