/**
 * F1 — Customer catalog browse, REAL mirror data (pilot phase 3.1).
 *
 * Spec: `docs/screens/customer-booking-flow.md` §3. The Tau 3-layer
 * stub (reasoning_text / next_available_slot / last_visit / category
 * counts) was removed in phase 3.1 — no backend produces those fields
 * (see `lib/customer-booking.ts` header for the full rationale). This
 * screen renders only what the bot mirror + Ayla scorer actually
 * provide:
 *
 *   «✨ Ayla подобрала» — top-3 services by scorer rank (founder cut
 *     #1 cap; hidden silently when the scorer is unavailable);
 *   «Услуги» — all active services (mirror) → service detail
 *     (`/catalog/:serviceId`, real screen continuing the booking flow);
 *   «Мастера» — bookable masters (mirror) → master detail
 *     (`/customer/masters/:masterId`, real F2 screen).
 *
 * Voice rules (Tau §8 F1) unchanged: title «Найди мастера», search
 * placeholder «Что хочешь?». Search filters the services list
 * client-side (name + short description, case-insensitive).
 *
 * WCAG 2.2 AA (Tau §11): cards wrapped in `<article>`, one `<section>`
 * per block with labelled headings.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MasterCard } from "../components/MasterCard";
import { ScreenLayout } from "../components/ScreenLayout";
import { ServiceCard } from "../components/ServiceCard";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { getCatalogBrowse, type CatalogBrowseData } from "../lib/customer-booking";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: CatalogBrowseData }
  | { kind: "error"; err: unknown };

/** Founder cut #1: never more than 3 picks, whatever the scorer sends. */
const PICKS_CAP = 3;

export function CustomerCatalogScreen() {
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [search, setSearch] = useState("");

  const load = useCallback(() => {
    setState({ kind: "loading" });
    let cancelled = false;
    getCatalogBrowse()
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

  const query = search.trim().toLowerCase();

  const visibleServices = useMemo(() => {
    if (state.kind !== "ok") return [];
    if (!query) return state.data.services;
    return state.data.services.filter((s) =>
      `${s.name} ${s.short_description}`.toLowerCase().includes(query),
    );
  }, [state, query]);

  const picks = useMemo(() => {
    if (state.kind !== "ok") return [];
    const byId = new Map(state.data.services.map((s) => [s.id, s]));
    return state.data.pickServiceIds
      .map((id) => byId.get(id))
      .filter((s): s is NonNullable<typeof s> => s != null)
      .slice(0, PICKS_CAP);
  }, [state]);

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

  const { masters } = state.data;
  const allEmpty = visibleServices.length === 0 && masters.length === 0;

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
            Пока здесь пусто. Загляни позже — покажу варианты.
          </p>
        </div>
      )}

      {picks.length > 0 && (
        <section aria-labelledby="catalog-picks">
          <h2 id="catalog-picks" className="customer-catalog__section-title">
            <span aria-hidden="true">✨ </span>Ayla подобрала
          </h2>
          {picks.map((service) => (
            <article key={service.id} className="customer-catalog__card-l2">
              <ServiceCard
                service={service}
                onSelect={() => navigate(`/catalog/${service.id}`)}
              />
            </article>
          ))}
        </section>
      )}

      {visibleServices.length > 0 && (
        <section aria-labelledby="catalog-services">
          <h2 id="catalog-services" className="customer-catalog__section-title">
            Услуги
          </h2>
          {visibleServices.map((service) => (
            <article key={service.id}>
              <ServiceCard
                service={service}
                onSelect={() => navigate(`/catalog/${service.id}`)}
              />
            </article>
          ))}
        </section>
      )}

      {masters.length > 0 && (
        <section aria-labelledby="catalog-masters">
          <h2 id="catalog-masters" className="customer-catalog__section-title">
            Мастера
          </h2>
          {masters.map((master) => (
            <article key={master.id}>
              <MasterCard
                master={master}
                onSelect={() => navigate(`/customer/masters/${master.id}`)}
              />
            </article>
          ))}
        </section>
      )}
    </ScreenLayout>
  );
}
