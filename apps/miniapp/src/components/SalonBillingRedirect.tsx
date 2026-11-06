/**
 * Слушатель сквозных отказов подписки салона (DRF-2988, PR-B3b).
 *
 * `admin-api.ts` шлёт сигнал на `403 subscription_frozen` и
 * `409 plan_change_required`; здесь — роутер, который уводит туда, где отказ
 * снимают. Уже на месте — не уводим: экран биллинга сам ходит в открытые при
 * заморозке ручки, и повторный переход сбил бы его состояние.
 */
import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  SALON_BILLING_PATH,
  SALON_PLAN_CHANGE_PATH,
  onSalonBillingSignal,
} from "../lib/salon-billing-signal";

export function SalonBillingRedirect() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // Несколько отказов в одном тике (параллельные запросы экрана) — один
  // переход: флаг снимается, когда адрес действительно сменился.
  const leaving = useRef(false);
  useEffect(() => {
    leaving.current = false;
  }, [pathname]);

  useEffect(
    () =>
      onSalonBillingSignal((signal) => {
        if (leaving.current || pathname.startsWith(SALON_BILLING_PATH)) return;
        leaving.current = true;
        if (signal.kind === "frozen") {
          navigate(SALON_BILLING_PATH, { replace: true });
        } else {
          navigate(SALON_PLAN_CHANGE_PATH, { state: { salonPrice: signal.salonPrice } });
        }
      }),
    [navigate, pathname],
  );

  return null;
}
