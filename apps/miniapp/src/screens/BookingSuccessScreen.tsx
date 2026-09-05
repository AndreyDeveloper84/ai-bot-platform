/** F5 — Success. Spec §8 F5 + conversational-ux-framework §5.1.5. */

import { useEffect } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { formatVisitFull } from "../lib/format";
import { hapticNotify, maxBridge } from "../lib/max-sdk";
import { backTo } from "../lib/screen-back";

/**
 * Возврат (DRF-1493): к списку записей, а НЕ на подтверждение.
 * Запись уже создана — шаг назад в оформление вернул бы человека в
 * завершённый сценарий.
 */
const BACK = backTo("/my-visits");

interface SuccessState {
  service_name?: string;
  master_name?: string;
  visit_at?: string;
}

export function BookingSuccessScreen() {
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

  // Spec-locked copy: "Готово — {{date}} в {{time}}, у {{master}}.
  // Напомню за день и за час до визита."
  const headline = s.visit_at
    ? `Готово — ${formatVisitFull(s.visit_at)}${s.master_name ? `, у ${s.master_name}` : ""}.`
    : "Готово.";

  return (
    <ScreenLayout
      back={BACK}
      title=""
      cta={
        <StickyCta onClick={() => navigate("/catalog", { replace: true })}>
          На главную
        </StickyCta>
      }
    >
      <h1 className="success-headline">{headline}</h1>
      <p className="success-sub">Напомню за день и за час до визита.</p>

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
              <dd style={{ fontFamily: "monospace", fontSize: "var(--font-size-100)" }}>
                {bookingId}
              </dd>
            </>
          )}
        </dl>
      </div>
    </ScreenLayout>
  );
}
