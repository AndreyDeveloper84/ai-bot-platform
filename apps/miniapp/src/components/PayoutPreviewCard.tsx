/**
 * Compact «К выплате» card for the master dashboard (C3, phase 2b).
 *
 * Self-loading slice — independent of the dashboard's day state, so a
 * billing failure never blanks the day view. Renders ONLY with real
 * pending data:
 *   - zero pending → hidden (the honest zero state lives on the full
 *     billing screen `/master/billing`);
 *   - error (503 mapping / 502 upstream) → hidden + DEV warn (fake
 *     numbers on the dashboard are worse than no card).
 *
 * Wording locked per §4: «Ожидается», never «гарантированно».
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { formatMoney } from "../lib/format";
import { getPayoutPreview, type PayoutPreview } from "../lib/master-billing";

export function PayoutPreviewCard() {
  const navigate = useNavigate();
  const [preview, setPreview] = useState<PayoutPreview | null>(null);

  useEffect(() => {
    let cancelled = false;
    getPayoutPreview()
      .then((data) => {
        if (!cancelled) setPreview(data);
      })
      .catch((err: unknown) => {
        if (import.meta.env.DEV) {
          // eslint-disable-next-line no-console
          console.warn("[payout-card] hidden — preview failed", err);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!preview) return null;
  const isZero = preview.items.length === 0 && preview.pending_amount === "0.00";
  if (isZero) return null;

  return (
    <section
      className="master-dashboard__section"
      aria-labelledby="m1-payout"
    >
      <h2 className="master-dashboard__section-title" id="m1-payout">
        К выплате
      </h2>
      <p className="master-dashboard__today-line">
        <strong>{formatMoney(preview.pending_amount)}</strong>
      </p>
      {preview.expected_settlement_hint && (
        <p className="master-dashboard__today-line">
          Ожидается: {preview.expected_settlement_hint}.
        </p>
      )}
      <button
        type="button"
        className="btn-secondary master-dashboard__inline-cta"
        onClick={() => navigate("/master/billing")}
      >
        Подробнее
      </button>
    </section>
  );
}
