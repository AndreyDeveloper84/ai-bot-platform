/**
 * Customer cards screen — C7.2 live (saved cards for online payment).
 *
 * Route: `/customer/cards`
 *
 * Consent boundary (C7.2, locked): binding a card is a SEPARATE
 * voluntary action — the «Привязать карту» button stays disabled until
 * the user explicitly checks the consent box; the lib sends
 * `consent_version` + `consented_at` with the setup call. The saved
 * method is used only for user-initiated payments — no autocharges
 * (AYLA-DEC-0001); after a revoke the method is never charged again.
 *
 * Flow: list (brand + last4) → consent → setup → webview
 * (`openPaymentConfirmation`) → card appears after the webhook →
 * revoke behind an explicit two-step confirmation (server-side
 * idempotent, repeat → 204).
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  deleteCard,
  getSavedCards,
  setupCard,
  type SavedCard,
} from "../lib/cards";
import { openPaymentConfirmation } from "../lib/max-sdk";

type State =
  | { kind: "loading" }
  | { kind: "ok"; cards: SavedCard[] }
  | { kind: "error" };

const BRAND_LABELS: Record<string, string> = {
  mir: "Мир",
  visa: "Visa",
  mastercard: "Mastercard",
};

function brandLabel(brand: string): string {
  return BRAND_LABELS[brand.toLowerCase()] ?? brand;
}

export function CustomerCardsScreen() {
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [consentChecked, setConsentChecked] = useState(false);
  const [setupBusy, setSetupBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null);
  const [revokeBusy, setRevokeBusy] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const cards = await getSavedCards();
      setState({ kind: "ok", cards });
    } catch {
      setState({ kind: "error" });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onSetup() {
    // Consent gate is the disabled button; guard here too (C7.2).
    if (!consentChecked || setupBusy) return;
    setSetupBusy(true);
    setError(null);
    setNote(null);
    try {
      const { confirmation_url } = await setupCard();
      openPaymentConfirmation(confirmation_url);
      setNote(
        "Открыла страницу привязки. После подтверждения карта появится в списке.",
      );
      // Refresh — the webhook may already have saved the method.
      void load();
    } catch {
      setError("Не получилось начать привязку. Попробуй ещё раз.");
    } finally {
      setSetupBusy(false);
    }
  }

  async function onRevoke(cardId: string) {
    if (revokeBusy) return;
    setRevokeBusy(true);
    try {
      await deleteCard(cardId);
      setConfirmRevokeId(null);
      setState((s) =>
        s.kind === "ok"
          ? { kind: "ok", cards: s.cards.filter((c) => c.id !== cardId) }
          : s,
      );
    } catch {
      setError("Не получилось отвязать карту. Попробуй ещё раз.");
    } finally {
      setRevokeBusy(false);
    }
  }

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
        <h1 className="records-screen__title">Мои карты</h1>
      </header>

      <main className="profile-screen__main">
        <section className="profile-section" aria-labelledby="cards-h2">
          <h2 id="cards-h2" className="profile-section__heading">
            Карты для оплаты онлайн
          </h2>

          {state.kind === "loading" && (
            <p className="profile-section__caption">Загружаю…</p>
          )}

          {state.kind === "error" && (
            <div className="callout" role="alert">
              <p style={{ margin: 0 }}>
                Не получилось загрузить карты. Попробуй ещё раз.
              </p>
              <button
                type="button"
                className="btn-secondary"
                style={{ marginTop: "var(--s-3)" }}
                onClick={() => void load()}
              >
                Обновить
              </button>
            </div>
          )}

          {state.kind === "ok" && state.cards.length === 0 && (
            <p className="profile-section__caption">
              Пока карт нет. Привязанная карта понадобится, если захочешь
              оплачивать запись онлайн — она появится здесь после первой
              привязки.
            </p>
          )}

          {state.kind === "ok" && state.cards.length > 0 && (
            <ul className="profile-cards__list" role="list">
              {state.cards.map((card) => (
                <li key={card.id} className="profile-cards__item">
                  {confirmRevokeId === card.id ? (
                    <span className="profile-cards__revoke-confirm">
                      <span>Отвязать ·· {card.last4}?</span>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={revokeBusy}
                        onClick={() => void onRevoke(card.id)}
                      >
                        {revokeBusy ? "Отвязываю…" : "Да, отвязать"}
                      </button>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={revokeBusy}
                        onClick={() => setConfirmRevokeId(null)}
                      >
                        Оставить
                      </button>
                    </span>
                  ) : (
                    <>
                      <span className="profile-cards__brand">
                        {brandLabel(card.brand)}
                      </span>
                      <span className="profile-cards__last4">
                        ·· {card.last4}
                      </span>
                      <button
                        type="button"
                        className="btn-secondary"
                        aria-label={`Отвязать карту ${brandLabel(card.brand)} ·· ${card.last4}`}
                        onClick={() => setConfirmRevokeId(card.id)}
                      >
                        Отвязать
                      </button>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}

          {/* Consent gate (C7.2) — binding starts only after the user
              explicitly agrees. Saved method = user-initiated payments
              only, no autocharges. */}
          <label className="profile-cards__consent">
            <input
              type="checkbox"
              checked={consentChecked}
              onChange={(e) => setConsentChecked(e.target.checked)}
            />
            <span>
              Соглашаюсь сохранить карту для оплаты записей онлайн.
              Списания — только когда я сам оплачиваю запись.
            </span>
          </label>

          <div className="profile-section__cta-row">
            <button
              type="button"
              className="btn-secondary"
              disabled={!consentChecked || setupBusy}
              onClick={() => void onSetup()}
            >
              {setupBusy ? "Подключаю…" : "Привязать карту"}
            </button>
          </div>

          {note && (
            <p className="profile-section__caption" role="status">
              {note}
            </p>
          )}
          {error && (
            <p className="profile-section__caption" role="alert">
              {error}
            </p>
          )}
        </section>
      </main>
    </div>
  );
}
