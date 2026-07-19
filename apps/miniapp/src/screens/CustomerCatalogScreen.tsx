/**
 * F1 — Customer catalog browse, 3-layer Ayla hierarchy.
 *
 * Spec: `docs/screens/customer-booking-flow.md` §3 (§3.1 standard /
 * §3.2 loyalty / §3.3 anonymous / §3.4 states).
 *
 * Founder cut #1: Layer 2 max 3 items (already capped in
 * `customer-booking.ts`, defence-in-depth slice here too).
 *
 * Voice rules (Tau §8 F1):
 *   - Title: «Найди мастера» (action verb)
 *   - Search placeholder: «Что хочешь?»
 *   - Layer 1: «Твои места»
 *   - Layer 2: «✨ Ayla подобрала»
 *   - Layer 3: «Исследовать новое»
 *   - reasoning_text rendered VERBATIM from backend (Tau §10.3 + memory
 *     `project_ayla_ranking_philosophy`).
 *
 * WCAG 2.2 AA inline (Tau §11):
 *   - 2.5.8 — cards ≥ 48dp tap target (matches `.service-card`)
 *   - 1.3.1 — `<section>` per layer with `<h2>`; cards `<article>`
 *   - Reasoning text aria-describedby links to card title for SR.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import {
  getCatalogRecommendations,
  type CatalogRecommendations,
  type CatalogLayer2Item,
} from "../lib/customer-booking";
import { STUB_SURFACES_ENABLED } from "../lib/feature-flags";
import { formatMoney } from "../lib/format";
import { PilotComingSoonScreen } from "./PilotComingSoonScreen";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: CatalogRecommendations }
  | { kind: "error"; err: unknown };

export function CustomerCatalogScreen() {
  // Pilot commit 4 (orchestrator): hardcoded fake salons are
  // unacceptable in prod for the pilot — the stub surface is gated off;
  // real customer endpoints land as phase 3 item 1. Build-time constant
  // flag, so the early return is hooks-safe.
  if (!STUB_SURFACES_ENABLED) {
    return <PilotComingSoonScreen surface="catalog" />;
  }
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [search, setSearch] = useState("");

  const load = useCallback(() => {
    setState({ kind: "loading" });
    let cancelled = false;
    getCatalogRecommendations()
      .then((data) => {
        if (!cancelled) setState({ kind: "ok", data });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => load(), [load]);

  if (state.kind === "loading") {
    return (
      <ScreenLayout title="Найди мастера">
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout title="Найди мастера">
        <StateError err={state.err} onRetry={load} screenId="customer-catalog" />
      </ScreenLayout>
    );
  }

  const { layer_1_your_places, layer_2_ayla_picks, layer_3_explore } =
    state.data;

  // Defence-in-depth cap per founder cut #1. Lib already caps; second
  // line is so a future direct fetch wiring can't accidentally re-leak.
  const layer2 = layer_2_ayla_picks.slice(0, 3);

  // Empty-everywhere edge: backend trust filter caught all results +
  // no Layer 1 + no Layer 3. Voice per Tau §3.4 row 4.
  const allEmpty =
    layer_1_your_places.length === 0 &&
    layer2.length === 0 &&
    layer_3_explore.length === 0;

  return (
    <ScreenLayout title="Найди мастера">
      <div className="customer-catalog__search">
        <input
          type="search"
          aria-label="Поиск по услугам"
          placeholder="Что хочешь?"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="customer-catalog__search-input"
        />
      </div>

      {allEmpty && (
        <div className="callout">
          <p style={{ margin: 0 }}>
            Сегодня ничего безопасного не нашла. Загляни позже —
            покажу варианты.
          </p>
        </div>
      )}

      {layer_1_your_places.length > 0 && (
        <section aria-labelledby="catalog-layer-1">
          <h2 id="catalog-layer-1" className="customer-catalog__section-title">
            Твои места
          </h2>
          {layer_1_your_places.map((item) => (
            <article key={item.id} className="customer-catalog__card-l1">
              <button
                type="button"
                className="customer-catalog__card-btn"
                aria-label={`${item.title}, ${item.services_summary}`}
                onClick={() =>
                  navigate(`/customer/masters/${item.id}`)
                }
              >
                <div className="customer-catalog__card-title">{item.title}</div>
                <div className="customer-catalog__card-meta">
                  {item.services_summary}
                </div>
              </button>
            </article>
          ))}
        </section>
      )}

      {layer2.length > 0 && (
        <section aria-labelledby="catalog-layer-2">
          <h2 id="catalog-layer-2" className="customer-catalog__section-title">
            <span aria-hidden="true">✨ </span>Ayla подобрала
          </h2>
          {layer2.map((item) => (
            <Layer2Card
              key={item.id}
              item={item}
              onOpen={() => navigate(`/customer/masters/${item.id}`)}
            />
          ))}
        </section>
      )}

      {layer_3_explore.length > 0 && (
        <section aria-labelledby="catalog-layer-3">
          <h2 id="catalog-layer-3" className="customer-catalog__section-title">
            Исследовать новое
          </h2>
          <ul className="customer-catalog__categories" role="list">
            {layer_3_explore.map((c) => (
              <li key={c.category}>
                <button
                  type="button"
                  className="customer-catalog__category-btn"
                  aria-label={`${c.category}, ${c.count} мастеров`}
                  onClick={() =>
                    navigate(
                      `/customer/catalog?category=${encodeURIComponent(c.category)}`,
                    )
                  }
                >
                  {c.category} ({c.count})
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </ScreenLayout>
  );
}

interface Layer2CardProps {
  item: CatalogLayer2Item;
  onOpen: () => void;
}

/**
 * Layer 2 card with reasoning_text rendered VERBATIM per Tau §10.3.
 * `aria-describedby` links the reasoning to the title for SR.
 */
function Layer2Card({ item, onOpen }: Layer2CardProps) {
  const titleId = `l2-title-${item.id}`;
  const reasoningId = `l2-reason-${item.id}`;
  return (
    <article className="customer-catalog__card-l2">
      <button
        type="button"
        className="customer-catalog__card-btn"
        onClick={onOpen}
        aria-labelledby={titleId}
        aria-describedby={reasoningId}
      >
        <div id={titleId} className="customer-catalog__card-title">
          {item.title}
        </div>
        <div
          id={reasoningId}
          className="customer-catalog__card-reasoning"
        >
          {item.reasoning_text}
        </div>
        <div className="customer-catalog__card-meta">
          от {formatMoney(item.starting_price)} ·{" "}
          {item.next_available_slot}
        </div>
      </button>
    </article>
  );
}
