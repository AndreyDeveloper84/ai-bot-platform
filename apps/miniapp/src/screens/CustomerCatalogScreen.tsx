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
 *     #1 cap), each with the WHY the source sent. Owner ruling 25.08:
 *     «Нет displayable WHY → нет блока „Ayla подобрала"» — the section
 *     renders only while `data.picks` is non-empty, and the lib puts a
 *     pick there only when the SOURCE explained it. Today the scorer
 *     sends `{service_id, score}` only, so the branded block is
 *     silently absent; «Услуги» and «Мастера» below are untouched;
 *   «Услуги» — all active services (mirror) → service detail
 *     (`/customer/catalog/:serviceId` — canonical address of the shared
 *     ServiceDetailScreen (DRF-1481), real screen continuing the
 *     booking flow);
 *   «Мастера» — bookable masters (mirror) → master detail
 *     (`/customer/masters/:masterId`, real F2 screen).
 *
 * Voice rules (Tau §8 F1) unchanged: title «Найди мастера», search
 * placeholder «Что хочешь?». Search filters the services list
 * client-side (name + short description, case-insensitive).
 *
 * WCAG 2.2 AA (Tau §11): cards wrapped in `<article>`, one `<section>`
 * per block with labelled headings. Each branded pick's WHY is a `<ul>`
 * inside its `<article>`, next to the card — `ServiceCard` itself is
 * shared with the plain catalog and stays WHY-free by design.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MasterCard } from "../components/MasterCard";
import { ScreenLayout } from "../components/ScreenLayout";
import { ServiceCard } from "../components/ServiceCard";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import type { Service } from "../lib/api";
import { getCatalogBrowse, type CatalogBrowseData } from "../lib/customer-booking";
import { backTo } from "../lib/screen-back";

/**
 * Возврат (DRF-1493) — экран со скриншота владельца («Нету кнопки
 * назад»).
 *
 * Каталог НЕ корень, хотя в него и ведёт deep link из бота
 * (`open_catalog` → `/customer/catalog`). Deep link делает
 * `history.back()` бесполезным, но родителя не отменяет: внутри
 * приложения сюда приходят только с дома, из профиля, со «Дня» и из
 * тупиков сценария записи. Дом клиентской поверхности — «Записи»
 * (`/customer/main`), туда и ведёт возврат при любом входе.
 *
 * Нижней навигации этот экран не рисует (её рисуют только `Записи` и
 * `День`), поэтому без стрелки он был настоящим тупиком.
 */
const BACK = backTo("/customer/main");

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

  /**
   * Branded picks, each carrying the WHY the source sent. Entries
   * without WHY never reach here — `getCatalogBrowse` drops them (owner
   * ruling 25.08). So an empty list means exactly one thing: Ayla has
   * nothing it can explain right now.
   */
  const picksWithWhy = useMemo(() => {
    if (state.kind !== "ok") return [];
    const byId = new Map(state.data.services.map((s) => [s.id, s]));
    return state.data.picks
      .map((pick) => ({ service: byId.get(pick.serviceId), reasons: pick.reasons }))
      .filter(
        (p): p is { service: Service; reasons: string[] } => p.service != null,
      )
      .slice(0, PICKS_CAP);
  }, [state]);

  if (state.kind === "loading") {
    return (
      <ScreenLayout back={BACK} title="Найди мастера">
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
      <ScreenLayout back={BACK} title="Найди мастера">
        <StateError err={state.err} onRetry={load} screenId="customer-catalog" />
      </ScreenLayout>
    );
  }

  const { masters } = state.data;
  const allEmpty = visibleServices.length === 0 && masters.length === 0;

  return (
    <ScreenLayout back={BACK} title="Найди мастера">
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

      {/* Owner ruling 25.08 — «Нет displayable WHY → нет блока „Ayla
          подобрала"». The gate is the DATA, not a feature flag: the
          section shows exactly while the source sent picks it can
          explain, so it revives by itself when `POST /recommendations`
          starts returning reasons. Never render a stand-in WHY here. */}
      {picksWithWhy.length > 0 && (
        <section aria-labelledby="catalog-picks">
          <h2 id="catalog-picks" className="customer-catalog__section-title">
            <span aria-hidden="true">✨ </span>Ayla подобрала
          </h2>
          {picksWithWhy.map(({ service, reasons }) => (
            <article key={service.id} className="customer-catalog__card-l2">
              <ServiceCard
                service={service}
                onSelect={() => navigate(`/customer/catalog/${service.id}`)}
              />
              {/* WHY — verbatim from the source, never composed here. */}
              <ul className="customer-catalog__why">
                {reasons.map((reason) => (
                  <li key={reason} className="customer-catalog__why-item">
                    {reason}
                  </li>
                ))}
              </ul>
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
                onSelect={() => navigate(`/customer/catalog/${service.id}`)}
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
