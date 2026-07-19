/**
 * Customer cards screen — C7.2 skeleton (pilot phase 3, step 3).
 *
 * Route: `/customer/cards`
 *
 * Renders saved cards (brand + last4) from the `lib/cards.ts` seam.
 * The passthrough endpoint is W3-pending (C7.2), so today the seam
 * returns empty and the screen shows its honest empty state. The
 * «Привязать карту» action stays disabled with an explicit caption —
 * a button that pretends to work would violate the same truthfulness
 * rule as fake data. Card binding activates on the orchestrator's
 * signal (after the C7 passthrough + webview confirmation flow).
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getSavedCards, type SavedCard } from "../lib/cards";

type State =
  | { kind: "loading" }
  | { kind: "ok"; cards: SavedCard[] };

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

  useEffect(() => {
    let cancelled = false;
    getSavedCards()
      .then((cards) => {
        if (!cancelled) setState({ kind: "ok", cards });
      })
      .catch(() => {
        // Pre-passthrough seam never rejects; defensive — empty state
        // is the honest fallback (no fake cards, no scary error).
        if (!cancelled) setState({ kind: "ok", cards: [] });
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
                  <span className="profile-cards__brand">
                    {brandLabel(card.brand)}
                  </span>
                  <span className="profile-cards__last4">
                    ·· {card.last4}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <div className="profile-section__cta-row">
            <button
              type="button"
              className="btn-secondary"
              disabled
              aria-disabled="true"
            >
              Привязать карту
            </button>
          </div>
          <p className="profile-section__caption">
            Привязка появится, когда подключим оплату онлайн.
          </p>
        </section>
      </main>
    </div>
  );
}
