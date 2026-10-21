/**
 * Переход «Соло» → «Салон» с подтверждением цены (DRF-2988, PR-B3b; В-2).
 *
 * Route: `/admin/billing/salon`. Сюда уводит `409 plan_change_required` —
 * соло пытается получить второго мастера: это не запрет, а предложение
 * перейти на «Салон». Цена — из отказа гейта (`details.salon_price`) или из
 * экрана биллинга; канон цены — каталог: он сверяет подтверждённую и
 * отвечает `price_mismatch` со своей, и экран просит подтвердить её заново.
 *
 * **Без утверждённого макета** — как `SalonBillingScreen` (решение владельца
 * 21.10). Только владелец: сервер отказывает остальным (`owner_only`).
 */

import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import { convertToSalon, type MeResponse } from "../../lib/admin-api";
import { ApiError } from "../../lib/api";
import { formatConsentDate } from "../../lib/customer-profile";
import { formatMoney } from "../../lib/format";
import { onBackButton, setBackButton } from "../../lib/max-sdk";
import { SALON_BILLING_PATH, SALON_PRICE_FALLBACK } from "../../lib/salon-billing-signal";
import { billingRefusalText } from "./SalonBillingScreen";

export interface PlanChangeState {
  salonPrice?: string;
}

export function SalonPlanChangeScreen({ me }: { me: MeResponse }) {
  const navigate = useNavigate();
  const location = useLocation();
  const initial = (location.state as PlanChangeState | null)?.salonPrice || SALON_PRICE_FALLBACK;
  const [price, setPrice] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [key] = useState(() => crypto.randomUUID());

  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => navigate(SALON_BILLING_PATH));
    return () => {
      off();
      setBackButton(false);
    };
  }, [navigate]);

  async function onConfirm() {
    if (busy) return;
    setBusy(true);
    setNote(null);
    try {
      const result = await convertToSalon(price, key);
      setDone(result.effective_from);
    } catch (err) {
      if (err instanceof ApiError && err.slug === "price_mismatch") {
        const current = err.details?.current_price;
        if (typeof current === "string" && current) setPrice(current);
        setNote("Цена изменилась — проверьте новую и подтвердите ещё раз.");
      } else if (err instanceof ApiError && err.slug === "already_salon") {
        setDone("");
      } else {
        setNote(billingRefusalText(err) ?? "Не получилось сменить тариф. Попробуйте ещё раз.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="screen">
      <h1 className="screen__title">Тариф «Салон»</h1>

      {done !== null ? (
        <div className="callout" role="status">
          <p style={{ margin: 0 }}>
            {done
              ? `Готово: можно добавлять мастеров. Новая цена — с ${formatConsentDate(done)}.`
              : "Салон уже на тарифе «Салон» — можно добавлять мастеров."}
          </p>
          <div style={{ marginTop: "var(--s-3)" }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate(SALON_BILLING_PATH)}
            >
              К подписке
            </button>
          </div>
        </div>
      ) : (
        <section className="profile-section" aria-labelledby="salon-plan-h2">
          <h2 id="salon-plan-h2" className="profile-section__heading">
            Второй мастер — это тариф «Салон»
          </h2>
          <p>
            <strong>{formatMoney(price)} в месяц</strong> · до трёх мастеров
          </p>
          <p className="profile-section__caption">
            Мастера можно добавить сразу. Новая цена начнёт действовать со следующего периода.
          </p>
          {me.is_owner ? (
            <div className="profile-section__cta-row">
              <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={() => void onConfirm()}
              >
                {busy ? "Переводим…" : `Перейти за ${formatMoney(price)}`}
              </button>
            </div>
          ) : (
            <p className="profile-section__caption">
              Сменить тариф может только владелец салона.
            </p>
          )}
          {note && (
            <p className="profile-section__caption" role="alert">
              {note}
            </p>
          )}
        </section>
      )}

      <AdminTabBar me={me} />
    </div>
  );
}
