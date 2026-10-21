/**
 * Подписка салона — экран владельца и администратора (DRF-2988, PR-B3b).
 *
 * Route: `/admin/billing` — пункт «Подписка» листа аватара; сюда же уводит
 * `403 subscription_frozen` с любой админской двери (`salon-billing-signal`).
 *
 * **Без утверждённого макета.** Решение владельца 21.10: строить сейчас из
 * системы Salon v2 (рамка и классы соседних экранов листа аватара), состав —
 * строго по листу DRF-2988 п.4; доска владельца потом — корректирующий
 * проход. Новых классов и цветов здесь нет.
 *
 * Данные — ответ каталога как есть (`GET /api/v1/admin/billing/status/`):
 * бот подписку не считает. Подписку открывает каталог на первой публикации
 * (В-10) — до того `subscription: null`.
 *
 * Кто что видит: статус, карта, оплата долга — владелец и администратор;
 * «Отменить подписку» и переход на «Салон» — только владелец (сервер
 * отказывает `owner_only` и сам). Ресепшн получает 403 от сервера — экран
 * говорит, к кому идти.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import { SystemState } from "../../components/master/SystemState";
import { useSalonSectionBack } from "../../hooks/useSalonSectionBack";
import {
  cancelSalonSubscription,
  getSalonBillingStatus,
  paySalonDebt,
  setupSalonCard,
  type MeResponse,
  type SalonBillingStatus,
  type SalonSubscription,
} from "../../lib/admin-api";
import { ApiError } from "../../lib/api";
import { formatConsentDate } from "../../lib/customer-profile";
import { formatMoney } from "../../lib/format";
import { openPaymentConfirmation } from "../../lib/max-sdk";
import { SALON_BILLING_PATH, SALON_PLAN_CHANGE_PATH } from "../../lib/salon-billing-signal";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; data: SalonBillingStatus }
  | { kind: "error"; err: unknown };

const STATUS_LABELS: Record<string, string> = {
  trial: "Пробный период",
  active: "Активна",
  past_due: "Задолженность",
  frozen: "Приостановлена",
  canceled: "Отменена",
};

const TARIFF_LABELS: Record<string, string> = { solo: "Соло", salon: "Салон" };

const BRAND_LABELS: Record<string, string> = { mir: "Мир", visa: "Visa", mastercard: "Mastercard" };

/** Отказы, у которых есть свой текст; остальное — общая ошибка загрузки. */
export const BILLING_REFUSAL_TEXT: Record<string, string> = {
  billing_disabled: "Оплата подписки пока не подключена.",
  billing_charges_disabled: "Оплата пока не подключена — списаний сейчас нет.",
  billing_subject_not_admin:
    "В каталоге ваша учётная запись не отмечена администратором салона — обратитесь в поддержку Ayla.",
  owner_only: "Это может сделать только владелец салона.",
  forbidden: "Подпиской управляют владелец и администратор салона.",
  no_tenant_admin: "У салона в каталоге нет администратора-плательщика — обратитесь в поддержку Ayla.",
};

export function billingRefusalText(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  if (err.status === 403 && !BILLING_REFUSAL_TEXT[err.slug]) {
    return BILLING_REFUSAL_TEXT.forbidden ?? null;
  }
  return BILLING_REFUSAL_TEXT[err.slug] ?? null;
}

const returnUrl = () => `${window.location.origin}${SALON_BILLING_PATH}`;

export function SalonBillingScreen({ me }: { me: MeResponse }) {
  useSalonSectionBack(me);
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  const reload = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoad({ kind: "loading" });
    try {
      setLoad({ kind: "ok", data: await getSalonBillingStatus() });
    } catch (err) {
      setLoad({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="screen">
      <h1 className="screen__title">Подписка</h1>

      {load.kind === "loading" && <SystemState kind="loading" lines={2} />}
      {load.kind === "error" &&
        (billingRefusalText(load.err) ? (
          <div className="callout" role="status">
            <p style={{ margin: 0 }}>{billingRefusalText(load.err)}</p>
          </div>
        ) : (
          <SystemState
            kind="load_error"
            what="subscription"
            err={load.err}
            onRetry={() => void reload()}
          />
        ))}
      {load.kind === "ok" &&
        (load.data.subscription ? (
          <Subscription
            sub={load.data.subscription}
            isOwner={me.is_owner}
            onChanged={() => void reload({ silent: true })}
          />
        ) : (
          <div className="callout" role="status">
            <p style={{ margin: 0 }}>
              Подписка откроется, когда салон появится в каталоге: первый месяц — бесплатно.
            </p>
          </div>
        ))}

      <AdminTabBar me={me} />
    </div>
  );
}

function Subscription({
  sub,
  isOwner,
  onChanged,
}: {
  sub: SalonSubscription;
  isOwner: boolean;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const extra = Number(sub.extra_masters_amount);
  const owesNow = sub.status === "past_due" || sub.status === "frozen";

  return (
    <>
      <section className="profile-section" aria-labelledby="salon-billing-h2">
        <h2 id="salon-billing-h2" className="profile-section__heading">
          Тариф
        </h2>
        <p>
          <strong>{STATUS_LABELS[sub.status] ?? sub.status}</strong>
          {sub.tariff && <> · тариф «{TARIFF_LABELS[sub.tariff] ?? sub.tariff}»</>}
        </p>
        {sub.status === "trial" && sub.trial_ends_at && (
          <p className="profile-section__caption">
            Пробный период — до {formatConsentDate(sub.trial_ends_at)}.
          </p>
        )}
        {sub.status !== "canceled" && sub.current_period_end && (
          <p className="profile-section__caption">
            Текущий период — до {formatConsentDate(sub.current_period_end)}.
          </p>
        )}
        <p className="profile-section__caption">Активных мастеров: {sub.masters_count}.</p>
        {extra > 0 && (
          <p className="profile-section__caption">
            Доплата за мастеров сверх трёх: {formatMoney(sub.extra_masters_amount)} в месяц.
          </p>
        )}
        <p className="profile-section__caption">
          К оплате за период: {formatMoney(sub.price_preview)}.
        </p>
        {sub.pending_tariff && sub.pending_tariff_from && (
          <p className="profile-section__caption">
            С {formatConsentDate(sub.pending_tariff_from)} — тариф «
            {TARIFF_LABELS[sub.pending_tariff] ?? sub.pending_tariff}».
          </p>
        )}
        {sub.status === "canceled" && sub.cancel_effective_at && (
          <p className="profile-section__caption">
            Подписка действует до {formatConsentDate(sub.cancel_effective_at)}.
          </p>
        )}
        {isOwner && sub.tariff === "solo" && !sub.pending_tariff && sub.status !== "canceled" && (
          <div className="profile-section__cta-row">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate(SALON_PLAN_CHANGE_PATH)}
            >
              Перейти на «Салон»
            </button>
          </div>
        )}
      </section>

      {owesNow && <PayDebt frozen={sub.status === "frozen"} onChanged={onChanged} />}

      <Card card={sub.card} />

      {isOwner && sub.status !== "canceled" && <Cancel onChanged={onChanged} />}
    </>
  );
}

function PayDebt({ frozen, onChanged }: { frozen: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onPay() {
    if (busy) return;
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const result = await paySalonDebt(returnUrl());
      if (result.confirmation_url) {
        openPaymentConfirmation(result.confirmation_url);
        setNote("Открыли страницу оплаты. После списания статус обновится.");
      } else {
        setNote(`Списано ${formatMoney(result.amount)}. Задолженность погашена.`);
      }
      onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.slug === "no_debt") {
        setNote("Задолженности уже нет — статус обновится.");
        onChanged();
      } else {
        setError(billingRefusalText(err) ?? "Не получилось списать долг. Попробуйте ещё раз.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="callout" role="status">
      <p style={{ margin: 0 }}>
        {frozen
          ? "Подписка приостановлена: остальные разделы откроются после оплаты."
          : "По подписке есть задолженность. Списание повторится автоматически — или можно погасить сейчас."}
      </p>
      <div style={{ marginTop: "var(--s-3)" }}>
        <button type="button" className="btn-primary" disabled={busy} onClick={() => void onPay()}>
          {busy ? "Списываем…" : "Оплатить долг"}
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

function Card({ card }: { card: SalonSubscription["card"] }) {
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onBind() {
    if (!consent || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { confirmation_url } = await setupSalonCard(returnUrl());
      openPaymentConfirmation(confirmation_url);
      setPending(true);
    } catch (err) {
      setError(billingRefusalText(err) ?? "Не получилось начать привязку. Попробуйте ещё раз.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="profile-section" aria-labelledby="salon-billing-card-h2">
      <h2 id="salon-billing-card-h2" className="profile-section__heading">
        Карта для автосписаний
      </h2>
      {card ? (
        <p className="profile-section__caption">
          <span>
            {BRAND_LABELS[card.brand.toLowerCase()] ?? card.brand}
          </span>{" "}
          <span>·· {card.last4}</span>
        </p>
      ) : pending ? (
        <p className="profile-section__caption" role="status">
          Карта привязывается — появится здесь после подтверждения.
        </p>
      ) : (
        <>
          <p className="profile-section__caption">
            С привязанной карты раз в месяц будет списываться подписка салона — автоматически.
          </p>
          <label style={{ display: "flex", gap: "var(--s-2)", alignItems: "flex-start" }}>
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
            />
            <span>Согласен на автоматические списания по подписке с привязанной карты.</span>
          </label>
          <div className="profile-section__cta-row">
            <button
              type="button"
              className="btn-secondary"
              disabled={!consent || busy}
              onClick={() => void onBind()}
            >
              {busy ? "Подключаем…" : "Привязать карту"}
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

function Cancel({ onChanged }: { onChanged: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Один ключ на одно решение: повтор нажатия — та же отмена.
  const [key] = useState(() => crypto.randomUUID());

  async function onConfirm() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await cancelSalonSubscription(key);
      setAsking(false);
      onChanged();
    } catch (err) {
      setError(billingRefusalText(err) ?? "Не получилось отменить подписку. Попробуйте ещё раз.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="profile-section" aria-labelledby="salon-billing-cancel-h2">
      <h2 id="salon-billing-cancel-h2" className="profile-section__heading">
        Отмена подписки
      </h2>
      {asking ? (
        <>
          <p className="profile-section__caption">
            Подписка будет действовать до конца оплаченного периода, потом салон закроется для
            новых записей. Отменить?
          </p>
          <div className="profile-section__cta-row">
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={() => void onConfirm()}
            >
              {busy ? "Отменяем…" : "Да, отменить"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => setAsking(false)}
            >
              Не отменять
            </button>
          </div>
        </>
      ) : (
        <div className="profile-section__cta-row">
          <button type="button" className="btn-secondary" onClick={() => setAsking(true)}>
            Отменить подписку
          </button>
        </div>
      )}
      {error && (
        <p className="profile-section__caption" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
