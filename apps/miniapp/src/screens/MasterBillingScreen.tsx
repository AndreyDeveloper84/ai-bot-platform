/**
 * Master billing screen — C2 subscription status + C3 payout
 * breakdown (pilot phase 2b).
 *
 * Route: `/master/billing`
 *
 * Data: real master_api proxies via `lib/master-billing.ts` (frozen
 * contract §3/§4). Two isolated slices — a payout failure never
 * blanks the subscription card and vice versa.
 *
 * Locked UX:
 *   - Statuses: trial «Пробный период», active «Активна», past_due
 *     «Задолженность», canceled «Отменена», none «Подписки нет».
 *   - AMD-013: next_charge renders as «Следующее списание {date}»
 *     with the total and the subscription+fees breakdown; canceled →
 *     no next charge.
 *   - past_due: neutral block (the debt reason is visible ONLY to the
 *     master, C1 §2). No «Оплатить долг» button — the payoff endpoint
 *     does not exist (retry is automatic server-side, W2 dunning);
 *     inventing a dead CTA would be the same lie class as fake data.
 *   - C3: two item states explicitly — «Ожидает подтверждения после
 *     визита» (scheduled) / «Подтверждено, ожидает перечисления»
 *     (captured_pending_settlement). Settlement wording is always
 *     «ожидается», never «гарантированно».
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../lib/api";
import { formatMoney } from "../lib/format";
import { formatConsentDate } from "../lib/customer-profile";
import {
  getBillingStatus,
  getPayoutPreview,
  payDebt,
  setupMasterCard,
  type BillingStatus,
  type PayoutCaptureState,
  type PayoutPreview,
  type SubscriptionStatus,
  type TariffCode,
} from "../lib/master-billing";
import { openPaymentConfirmation } from "../lib/max-sdk";

type Slice<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T }
  | { kind: "error"; err: unknown };

const SUBSCRIPTION_LABELS: Record<SubscriptionStatus, string> = {
  trial: "Пробный период",
  active: "Активна",
  past_due: "Задолженность",
  canceled: "Отменена",
  none: "Подписки нет",
};

const TARIFF_LABELS: Record<string, string> = {
  solo: "Соло",
  salon: "Салон",
};

/** C3 item states with explicit customer-safe wording (§4 UI rule). */
const PAYOUT_STATE_LABELS: Partial<Record<PayoutCaptureState, string>> = {
  scheduled: "Ожидает подтверждения после визита",
  captured_pending_settlement: "Подтверждено, ожидает перечисления",
};

function payoutStateLabel(state: PayoutCaptureState): string {
  return PAYOUT_STATE_LABELS[state] ?? "В обработке";
}

export function MasterBillingScreen() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<Slice<BillingStatus>>({ kind: "loading" });
  const [payout, setPayout] = useState<Slice<PayoutPreview>>({ kind: "loading" });

  const loadStatus = useCallback(async (opts?: { silent?: boolean }) => {
    // Silent refresh keeps the current card (and its action notes)
    // mounted while fresh data arrives — used after debt actions.
    if (!opts?.silent) setStatus({ kind: "loading" });
    try {
      setStatus({ kind: "ok", data: await getBillingStatus() });
    } catch (err) {
      setStatus({ kind: "error", err });
    }
  }, []);

  const loadPayout = useCallback(async () => {
    setPayout({ kind: "loading" });
    try {
      setPayout({ kind: "ok", data: await getPayoutPreview() });
    } catch (err) {
      setPayout({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    void loadPayout();
  }, [loadStatus, loadPayout]);

  return (
    <div className="profile-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={() => navigate(-1)}
        >
          <span aria-hidden="true">←</span>
        </button>
        <h1 className="records-screen__title">Оплата и выплаты</h1>
      </header>

      <main className="profile-screen__main">
        {/* ── Подписка (C2) ─────────────────────────────────────────── */}
        <section className="profile-section" aria-labelledby="billing-sub-h2">
          <h2 id="billing-sub-h2" className="profile-section__heading">
            Подписка
          </h2>
          {status.kind === "loading" && (
            <p className="profile-section__caption">Загружаю…</p>
          )}
          {status.kind === "error" && (
            <BillingError err={status.err} onRetry={loadStatus} />
          )}
          {status.kind === "ok" && (
            <SubscriptionCard
              status={status.data}
              onChanged={() => void loadStatus({ silent: true })}
            />
          )}
        </section>

        {/* ── Карта для автосписаний (D7) ─────────────────────────── */}
        {status.kind === "ok" && (
          <CardBindingSection
            tariff={status.data.subscription.tariff}
            card={status.data.subscription.card}
          />
        )}

        {/* ── К выплате (C3) ────────────────────────────────────────── */}
        <section className="profile-section" aria-labelledby="billing-payout-h2">
          <h2 id="billing-payout-h2" className="profile-section__heading">
            К выплате
          </h2>
          {payout.kind === "loading" && (
            <p className="profile-section__caption">Загружаю…</p>
          )}
          {payout.kind === "error" && (
            <BillingError err={payout.err} onRetry={loadPayout} />
          )}
          {payout.kind === "ok" && <PayoutBreakdown preview={payout.data} />}
        </section>
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subscription card (C2)
// ---------------------------------------------------------------------------

function SubscriptionCard({
  status,
  onChanged,
}: {
  status: BillingStatus;
  /** Refetch C2 after a debt action (charge / no_debt) — the server
      holds the truth, the card re-renders from it. */
  onChanged: () => void;
}) {
  const sub = status.subscription;
  return (
    <div>
      <p className="profile-billing__status-line">
        <strong>{SUBSCRIPTION_LABELS[sub.status]}</strong>
        {sub.tariff && <> · тариф {TARIFF_LABELS[sub.tariff] ?? sub.tariff}</>}
      </p>

      {sub.status === "past_due" && <PayDebtBlock onChanged={onChanged} />}

      {sub.current_period_end && (
        <p className="profile-section__caption">
          Текущий период — до {formatConsentDate(sub.current_period_end)}.
        </p>
      )}

      {sub.next_charge && (
        <p className="profile-section__caption">
          Следующее списание {formatConsentDate(sub.next_charge.date)}:{" "}
          {formatMoney(sub.next_charge.total_amount)} (подписка{" "}
          {formatMoney(sub.next_charge.subscription_amount)} + комиссии{" "}
          {formatMoney(sub.next_charge.fees_amount)}).
        </p>
      )}

      {status.fees.pending_count > 0 && (
        <p className="profile-section__caption">
          Комиссии за записи: {formatMoney(status.fees.pending_total)} (
          {status.fees.pending_count}).
        </p>
      )}

      {status.last_invoice && (
        <p className="profile-section__caption">
          Последний инвойс: {formatMoney(status.last_invoice.amount)} —{" "}
          {status.last_invoice.status === "paid" ? "оплачен" : status.last_invoice.status}{" "}
          {formatConsentDate(status.last_invoice.paid_at)}.
        </p>
      )}

      {sub.status === "none" && (
        <p className="profile-section__caption">
          Подписки нет. Когда появится — здесь будут статус и списания.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card binding (D7) — consent-gated setup + webview.
// ---------------------------------------------------------------------------

function CardBindingSection({
  tariff,
  card,
}: {
  tariff: TariffCode | null;
  /** AMD-017 read-model: {last4, brand} once bound, null until then. */
  card: { last4: string; brand: string } | null;
}) {
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // After a successful setup-open the card exists only once the
  // webhook lands — show an explicit placeholder until C2 carries it.
  const [setupPending, setSetupPending] = useState(false);

  async function onBind() {
    // Consent gate is the disabled button; guard here too (money path).
    if (!consent || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { confirmation_url } = await setupMasterCard({
        // Tariff decides the bound account (solo=personal /
        // salon=tenant). C2 tariff wins; «solo» is the pilot default
        // when there is no subscription yet.
        tariff: tariff ?? "solo",
        returnUrl: `${window.location.origin}/master/billing`,
      });
      openPaymentConfirmation(confirmation_url);
      setSetupPending(true);
    } catch {
      setError("Не получилось начать привязку. Попробуй ещё раз.");
    } finally {
      setBusy(false);
    }
  }

  const BRAND_LABELS: Record<string, string> = {
    mir: "Мир",
    visa: "Visa",
    mastercard: "Mastercard",
  };

  return (
    <section className="profile-section" aria-labelledby="billing-card-h2">
      <h2 id="billing-card-h2" className="profile-section__heading">
        Карта для автосписаний
      </h2>

      {card ? (
        <p className="profile-cards__item">
          <span className="profile-cards__brand">
            {BRAND_LABELS[card.brand.toLowerCase()] ?? card.brand}
          </span>{" "}
          <span className="profile-cards__last4">·· {card.last4}</span>
        </p>
      ) : setupPending ? (
        <p className="profile-section__caption" role="status">
          Карта привязывается — появится здесь после подтверждения.
        </p>
      ) : (
        <>
          <p className="profile-section__caption">
            С привязанной карты раз в месяц будут списываться подписка и
            комиссии за записи — автоматически.
          </p>
          <label className="profile-cards__consent">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
            />
            <span>
              Соглашаюсь на автоматические списания по подписке с
              привязанной карты.
            </span>
          </label>
          <div className="profile-section__cta-row">
            <button
              type="button"
              className="btn-secondary"
              disabled={!consent || busy}
              onClick={() => void onBind()}
            >
              {busy ? "Подключаю…" : "Привязать карту"}
            </button>
          </div>
        </>
      )}

      {error && (
        <p className="profile-section__caption" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Pay-debt block (past_due) — real one-shot charge via the W3 proxy.
// ---------------------------------------------------------------------------

function PayDebtBlock({ onChanged }: { onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onPay() {
    // In-flight idempotency — the button is disabled, guard anyway.
    if (busy) return;
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const result = await payDebt(`${window.location.origin}/master/billing`);
      if (result.confirmation_url) {
        // No saved method — finish the payment in the webview.
        openPaymentConfirmation(result.confirmation_url);
        setNote("Открыла страницу оплаты. После списания статус обновится.");
      } else {
        setNote(`Списано ${formatMoney(result.amount)}. Задолженность погашена.`);
      }
      // The server holds the truth — refetch C2 regardless.
      onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.slug === "no_debt") {
        setNote("Задолженности уже нет — статус обновится.");
        onChanged();
      } else {
        setError("Не получилось списать долг. Попробуй ещё раз.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="callout" role="status">
      <p style={{ margin: 0 }}>
        По подписке есть задолженность. Списание повторится
        автоматически — или можно погасить сейчас одной кнопкой.
      </p>
      <div style={{ marginTop: "var(--s-3)" }}>
        <button
          type="button"
          className="btn-primary"
          disabled={busy}
          onClick={() => void onPay()}
        >
          {busy ? "Списываю…" : "Оплатить долг"}
        </button>
      </div>
      {note && <p style={{ marginBottom: 0, marginTop: "var(--s-2)" }}>{note}</p>}
      {error && (
        <p role="alert" style={{ marginBottom: 0, marginTop: "var(--s-2)" }}>
          {error}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Payout breakdown (C3)
// ---------------------------------------------------------------------------

function PayoutBreakdown({ preview }: { preview: PayoutPreview }) {
  const isZero = preview.items.length === 0;
  return (
    <div>
      <p className="profile-billing__payout-sum">
        <strong>{formatMoney(preview.pending_amount)}</strong>
      </p>
      {preview.expected_settlement_hint && (
        <p className="profile-section__caption">
          Ожидается: {preview.expected_settlement_hint}.
        </p>
      )}
      {isZero ? (
        <p className="profile-section__caption">
          Пока начислений нет — они появятся после первых визитов.
        </p>
      ) : (
        <ul className="profile-payout__list" role="list">
          {preview.items.map((item) => (
            <li key={item.appointment_id} className="profile-payout__item">
              <span className="profile-payout__item-state">
                {payoutStateLabel(item.capture_state)}
              </span>
              <span className="profile-payout__item-amount">
                {formatMoney(item.specialist_income)}
              </span>
              <span className="profile-payout__item-meta">
                {formatConsentDate(item.completed_at)} · визит{" "}
                {formatMoney(item.amount)} − комиссия{" "}
                {formatMoney(item.platform_fee)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Honest error states
// ---------------------------------------------------------------------------

function BillingError({ err, onRetry }: { err: unknown; onRetry: () => void }) {
  const mapping =
    err instanceof ApiError && err.slug === "specialist_mapping_unavailable";
  return (
    <div className="callout" role="alert">
      <p style={{ margin: 0 }}>
        {mapping
          ? "Данные биллинга ещё синхронизируются — загляни чуть позже."
          : "Не получилось загрузить. Попробуй ещё раз."}
      </p>
      <button
        type="button"
        className="btn-secondary"
        style={{ marginTop: "var(--s-3)" }}
        onClick={onRetry}
      >
        Обновить
      </button>
    </div>
  );
}
