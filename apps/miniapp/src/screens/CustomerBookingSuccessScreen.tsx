/**
 * F5 — Booking success.
 *
 * Spec: `docs/screens/customer-booking-flow.md` §7 (§7.1 layout /
 * §7.2 voice / §7.3 states).
 *
 * Voice rules (founder cut #5 + §8 F5):
 *   - Soft universal reminder, verbatim:
 *       «Я напомню перед визитом, чтобы ты не пропустила.»
 *     NEVER promise a specific schedule («за день и за час» et al.)
 *     because user notification preferences MAY differ post-pilot.
 *   - Primary CTA «Открыть запись» → `/customer/records/{id}` — the
 *     CANONICAL record detail (`CustomerBookingDetailScreen`, mounted
 *     in `App.tsx`). It used to point at the legacy `/my-visits/{id}`
 *     namespace, dropping a customer who had just booked onto the old
 *     surface; both routes read the same `GET /bookings/<id>`, so the
 *     id carries over unchanged. Legacy `/my-visits/:id` stays mounted
 *     for deep links that live outside this repo — see
 *     `App.customerRecordRoute.test.tsx`, which proves both.
 *   - Secondary CTA «Сообщить по записи» — Ayla-mediated messaging
 *     per `docs/design/policies/ayla-mediated-messaging.md`.
 *
 * Anti-patterns: «Готово ✅» / «УРА» — exclamation-mark celebration
 * forbidden per founder F2 (мягкий уважительный тон).
 *
 * Route shape: `/customer/booking/success/:bookingId` (path param).
 * State payload (service_name / master_name / visit_at) is passed
 * via `navigate(..., { state })` from F4. When the user deep-links
 * back into this screen (e.g. browser back), state is null and we
 * render a minimal «Готово» card with just the booking id.
 */

import { useEffect } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { PaymentStatusBadge } from "../components/PaymentStatusBadge";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { formatVisitFull } from "../lib/format";
import { hapticNotify, maxBridge } from "../lib/max-sdk";
import { backTo } from "../lib/screen-back";

/**
 * Возврат (DRF-1493): к списку записей, а НЕ на подтверждение.
 * Запись уже создана: шаг назад по истории вернул бы человека в
 * оформление с оплатой, которое он только что завершил.
 */
const BACK = backTo("/customer/records");

interface SuccessState {
  service_name?: string;
  master_name?: string;
  visit_at?: string;
  /** C7.4 — booking created but the payment create failed right after. */
  payment_start_failed?: boolean;
  /** C7.3 — capture_state right after payment create (online path). */
  payment_capture_state?: string | null;
}

export function CustomerBookingSuccessScreen() {
  const { bookingId } = useParams<{ bookingId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const s = (location.state as SuccessState | null) ?? {};

  useEffect(() => {
    hapticNotify("success");
    maxBridge()?.requestScreenMaxBrightness?.();
    return () => {
      maxBridge()?.releaseScreenMaxBrightness?.();
    };
  }, []);

  // Founder cut #5 verbatim. NEVER expand this copy with «за день и
  // за час» — the soft promise is the entire contract until the
  // reminder system surfaces per-user schedule preferences.
  const reminderCopy = "Я напомню перед визитом, чтобы ты не пропустила.";

  const headline = s.visit_at
    ? `Записала тебя — ${formatVisitFull(s.visit_at)}${
        s.master_name ? `, у ${s.master_name}` : ""
      }.`
    : "Записала.";

  return (
    <ScreenLayout back={BACK}
      title=""
      cta={
        <StickyCta
          onClick={() =>
            bookingId
              ? navigate(`/customer/records/${bookingId}`, { replace: true })
              : navigate("/customer/catalog", { replace: true })
          }
        >
          Открыть запись
        </StickyCta>
      }
    >
      <h1 className="customer-success__headline">{headline}</h1>
      <p className="customer-success__sub">{reminderCopy}</p>

      {/* C7.3 — online path: payment status right on the success
          screen («Зарезервировано» after create). Hidden otherwise. */}
      <PaymentStatusBadge state={s.payment_capture_state} />

      {/* C7.4 — the booking exists; only the payment start failed.
          Never imply the booking failed (duplicate-create risk). */}
      {s.payment_start_failed && (
        <p className="customer-success__payment-note" role="status">
          Запись подтверждена. Начать онлайн-оплату не получилось —
          можно оплатить на месте или позже из записи.
        </p>
      )}

      <div className="confirm-card">
        <dl>
          {s.service_name && (
            <>
              <dt>Услуга</dt>
              <dd>{s.service_name}</dd>
            </>
          )}
          {bookingId && (
            <>
              <dt>Номер записи</dt>
              <dd
                style={{
                  fontFamily: "monospace",
                  fontSize: "var(--font-size-100)",
                }}
              >
                {bookingId}
              </dd>
            </>
          )}
        </dl>
      </div>

      {/* Secondary CTA «Сообщить по записи» — hidden in round-1 until
          the messaging route ships. The prior implementation routed to
          the record detail, i.e. the same destination as the primary
          CTA (two CTAs, one place, no messaging surface) — a UX
          dead-end.
          Will route to /customer/masters/{masterId}/message per
          ayla-mediated-messaging.md when messaging UI lands. */}
    </ScreenLayout>
  );
}
