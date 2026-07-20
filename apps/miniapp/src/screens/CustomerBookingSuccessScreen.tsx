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
 *   - Primary CTA «Открыть запись» → /my-visits/{id}.
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
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { formatVisitFull } from "../lib/format";
import { hapticNotify, maxBridge } from "../lib/max-sdk";

interface SuccessState {
  service_name?: string;
  master_name?: string;
  visit_at?: string;
  /** C7.4 — booking created but the payment create failed right after. */
  payment_start_failed?: boolean;
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
    <ScreenLayout
      title=""
      cta={
        <StickyCta
          onClick={() =>
            bookingId
              ? navigate(`/my-visits/${bookingId}`, { replace: true })
              : navigate("/customer/catalog", { replace: true })
          }
        >
          Открыть запись
        </StickyCta>
      }
    >
      <h1 className="customer-success__headline">{headline}</h1>
      <p className="customer-success__sub">{reminderCopy}</p>

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
          the messaging route ships. The prior implementation routed
          to the same `/my-visits/{id}` as the primary CTA (two CTAs,
          same destination, no messaging surface) — a UX dead-end.
          Will route to /customer/masters/{masterId}/message per
          ayla-mediated-messaging.md when messaging UI lands. */}
    </ScreenLayout>
  );
}
