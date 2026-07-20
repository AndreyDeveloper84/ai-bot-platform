/**
 * Master billing screen — subscription status + card binding (D7).
 *
 * Route: /master/billing (entry: MasterSettingsScreen «Биллинг и карта»).
 *
 * Money path: without a bound card there is no subscription charge →
 * dunning → ``past_due`` → C1 blocks the master's NEW bookings (client
 * sees the neutral «unavailable»). So this screen is the pilot-critical
 * binding funnel:
 *
 *   consent checkbox (gates the button) → cardSetup → payment webview
 *   (openPaymentConfirmation) → return → status refetch → card shown.
 *
 * Data: verbatim W3 proxies — GET /api/v1/master/billing/status (C2),
 * POST /api/v1/master/billing/card-setup. Error mapping: 403 forbidden
 * (identity mismatch), 503 specialist_mapping_unavailable (master has
 * no Ayla link yet), 502 billing_upstream_unavailable, 400
 * validation_error.
 *
 * Consent wording: placeholder pending legal — AUTOPAY_CONSENT_VERSION
 * travels with the acceptance so the backend can re-prompt on a bump.
 *
 * Tariff choice: solo/salon radio, persisted to localStorage (asked
 * once, remembered) per the follow-up brief.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { MasterTabBar } from "../components/MasterTabBar";
import { Skeleton } from "../components/Skeleton";
import { Snackbar } from "../components/Snackbar";
import { ApiError } from "../lib/api";
import {
  cardSetup,
  getBillingStatus,
  type BillingStatus,
  type BillingTariff,
} from "../lib/master-api";
import {
  hapticNotify,
  hapticSelection,
  onBackButton,
  openPaymentConfirmation,
  setBackButton,
} from "../lib/max-sdk";

// --- Copy -----------------------------------------------------------------

export const AUTOPAY_CONSENT_VERSION = "billing-autopay-v1";

const TARIFF_STORAGE_KEY = "master_billing_tariff";

const COPY = {
  header: "Биллинг и карта",
  subscriptionTitle: "Подписка",
  cardTitle: "Карта для автосписаний",
  cardBoundPrefix: "Карта привязана",
  awaitingFirstCharge: "Карта привязана · ожидает первого списания",
  cardNone:
    "Карта не привязана. Без неё подписка не спишется — запись клиентов остановится.",
  consent:
    "Соглашаюсь на автоматическое списание абонементской платы с привязанной карты по выбранному тарифу. Текст оферты уточняется юристом.",
  // TODO(legal #947): replace with the approved offer wording.
  bindButton: "Привязать карту",
  bindingButton: "Открываем оплату…",
  tariffLegend: "Тариф",
  tariffs: {
    solo: "Соло — 690 ₽/мес",
    salon: "Салон — 990 ₽/мес",
  } as Record<BillingTariff, string>,
  status: {
    trial: "Пробный период",
    active: "Подписка активна",
    past_due: "Есть задолженность — оплатите, чтобы принимать записи",
    canceled: "Подписка отменена",
    none: "Подписки нет",
  } as Record<string, string>,
  errors: {
    forbidden: "Действие недоступно: сессия не совпадает с аккаунтом мастера.",
    mapping: "Связка с Ayla ещё не настроена. Напишите в поддержку студии.",
    upstream: "Сервис биллинга временно недоступен. Попробуйте позже.",
    generic: "Не удалось начать привязку. Попробуйте ещё раз.",
    statusLoad: "Не удалось загрузить статус биллинга.",
  },
};

function tariffFromStorage(): BillingTariff {
  if (typeof window === "undefined" || !window.localStorage) return "solo";
  return window.localStorage.getItem(TARIFF_STORAGE_KEY) === "salon"
    ? "salon"
    : "solo";
}

function mapSetupError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return COPY.errors.forbidden;
    if (err.status === 503) return COPY.errors.mapping;
    if (err.status === 502) return COPY.errors.upstream;
    if (err.status === 400 && err.detail) return err.detail;
  }
  return COPY.errors.generic;
}

// --- Component --------------------------------------------------------------

export function MasterBillingScreen() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [tariff, setTariff] = useState<BillingTariff>(tariffFromStorage);
  const [busy, setBusy] = useState(false);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [justBound, setJustBound] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await getBillingStatus();
      setStatus(data);
      setLoadError(null);
    } catch {
      setLoadError(COPY.errors.statusLoad);
    }
  }, []);

  // --- Bridge: BackButton wiring ---
  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => {
      hapticSelection();
      navigate("/master/settings");
    });
    return () => {
      off();
      setBackButton(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const pickTariff = useCallback((next: BillingTariff) => {
    hapticSelection();
    setTariff(next);
    try {
      window.localStorage.setItem(TARIFF_STORAGE_KEY, next);
    } catch {
      /* storage full / private mode — choice just isn't persisted */
    }
  }, []);

  const bind = useCallback(async () => {
    if (!consent || busy) return;
    setBusy(true);
    setSetupError(null);
    try {
      const resp = await cardSetup({
        tariff,
        return_url: window.location.href,
      });
      hapticNotify("success");
      setJustBound(true);
      openPaymentConfirmation(resp.confirmation_url);
      // The user lands back on this screen after the webview — refresh
      // so the bound card + status appear.
      await refresh();
    } catch (err) {
      hapticNotify("error");
      setSetupError(mapSetupError(err));
    } finally {
      setBusy(false);
    }
  }, [consent, busy, tariff, refresh]);

  const card = status?.card ?? null;
  const subStatus = status?.subscription.status ?? "none";
  const showAwaiting =
    justBound && card === null && (subStatus === "trial" || subStatus === "none");

  return (
    <div className="master-billing-screen">
      <header className="master-billing-screen__header">
        <h1>{COPY.header}</h1>
      </header>

      {loadError && <Snackbar visible message={loadError} />}

      <section aria-labelledby="billing-subscription">
        <h2 id="billing-subscription">{COPY.subscriptionTitle}</h2>
        {status === null && !loadError ? (
          <Skeleton />
        ) : (
          status !== null && (
            <div>
              <p>{COPY.status[subStatus] ?? subStatus}</p>
              {status.subscription.current_period_end && (
                <p>
                  Действует до {status.subscription.current_period_end}
                </p>
              )}
              {showAwaiting && <p>{COPY.awaitingFirstCharge}</p>}
            </div>
          )
        )}
      </section>

      <section aria-labelledby="billing-card">
        <h2 id="billing-card">{COPY.cardTitle}</h2>
        {card !== null ? (
          <p>
            {COPY.cardBoundPrefix}: {card.brand} •• {card.last4}
          </p>
        ) : (
          <>
            <p>{COPY.cardNone}</p>
            <fieldset>
              <legend>{COPY.tariffLegend}</legend>
              {(Object.keys(COPY.tariffs) as BillingTariff[]).map((code) => (
                <label key={code}>
                  <input
                    type="radio"
                    name="billing-tariff"
                    value={code}
                    checked={tariff === code}
                    onChange={() => pickTariff(code)}
                  />
                  {COPY.tariffs[code]}
                </label>
              ))}
            </fieldset>
            <label>
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />
              {COPY.consent} <small>({AUTOPAY_CONSENT_VERSION})</small>
            </label>
            <div>
              <button
                type="button"
                disabled={!consent || busy}
                onClick={() => void bind()}
              >
                {busy ? COPY.bindingButton : COPY.bindButton}
              </button>
            </div>
            {setupError && <p role="alert">{setupError}</p>}
          </>
        )}
      </section>

      <MasterTabBar
        unreadCount={0}
        scheduleHasPendingChange={false}
        profileHasOwnerPendingChange={false}
      />
    </div>
  );
}
