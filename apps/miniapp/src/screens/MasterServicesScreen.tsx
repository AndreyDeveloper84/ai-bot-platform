/**
 * Master Mini App «Услуги» tab — read-only catalog view.
 *
 * Route: /solo/services (rendered inside `UnifiedSoloSurface`).
 *
 * Spec source: docs/screens/master-solo-surface.md §4.4 (Tau verdict
 * 2026-05-26 r1). Variant B navigation per §3 — 4th of 5 bottom tabs.
 * Phase 1 Tier 2 read-only — no edit / add / archive. Catalog mutation
 * lives in Ayla per ADR-0009 §Hard rule #4 (no new transactional
 * domains in bot-platform).
 *
 * Backend contract: GET /api/v1/master/catalog
 * (apps/master_api/views.py::catalog_list →
 *  apps/master_api/services/catalog.py::list_master_services).
 *
 * Layout per Tau §4.4:
 *   - Header «Услуги и цены».
 *   - Note «Каталог Студии Ольги · видят клиенты при записи».
 *   - Services grouped by category, sorted (category, name).
 *   - Read-only edit-disabled note at footer.
 */

import { useEffect, useMemo, useState } from "react";

import { ApiError } from "../lib/api";
import {
  getMasterCatalog,
  type MasterServiceItem,
} from "../lib/master-api";

// --- Russian copy (VERBATIM from Tau §4.4) -------------------------------

const COPY = {
  title: "Услуги и цены",
  catalogNote: "Каталог Студии Ольги · видят клиенты при записи",
  empty: "Здесь будет твой каталог услуг. Карина настроит вместе с тобой.",
  errorTitle: "Не получилось загрузить услуги",
  retry: "Попробовать снова",
  lockedEditNote:
    "Редактирование услуг — в следующих обновлениях. Сейчас можно посмотреть, как видят клиенты.",
  priceOnRequest: "по запросу",
  durationUnit: "мин",
  priceUnit: "₽",
};

// --- Card sub-component --------------------------------------------------

function ServiceCard({ service }: { service: MasterServiceItem }) {
  const priceText =
    service.price_rub != null
      ? `${service.price_rub} ${COPY.priceUnit}`
      : COPY.priceOnRequest;
  const metaLine =
    service.duration_min > 0
      ? `${service.duration_min} ${COPY.durationUnit} · ${priceText}`
      : priceText;

  return (
    <div className="service-card">
      <div className="service-card__name">{service.name}</div>
      <div className="service-card__meta">{metaLine}</div>
      {service.description && (
        <p className="service-card__description">{service.description}</p>
      )}
    </div>
  );
}

// --- Skeleton / EmptyState ----------------------------------------------

function Skeleton() {
  return (
    <div className="screen master-services" aria-busy="true">
      <header className="master-services__header">
        <h1>{COPY.title}</h1>
      </header>
      <div className="master-services__body">
        {[1, 2, 3].map((i) => (
          <div key={i} className="skeleton service-card service-card--skel" />
        ))}
      </div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="screen master-services">
      <header className="master-services__header">
        <h1>{COPY.title}</h1>
      </header>
      <div className="master-services__empty">
        <p>{text}</p>
      </div>
    </div>
  );
}

function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="screen master-services">
      <header className="master-services__header">
        <h1>{COPY.title}</h1>
      </header>
      <div className="callout callout--danger" role="alert">
        <p>{COPY.errorTitle}</p>
        <p style={{ fontSize: "var(--font-size-100)", opacity: 0.7 }}>
          {message}
        </p>
        <button type="button" className="btn-secondary" onClick={onRetry}>
          {COPY.retry}
        </button>
      </div>
    </div>
  );
}

// --- Main screen ---------------------------------------------------------

export function MasterServicesScreen() {
  const [services, setServices] = useState<MasterServiceItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setServices(null);
    setError(null);
    getMasterCatalog()
      .then((rows) => {
        if (cancelled) return;
        setServices(rows);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const msg =
          e instanceof ApiError
            ? e.detail || e.slug
            : "Сеть недоступна";
        setError(msg);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  // Group by category preserving the backend-provided (category, name)
  // ordering. We use an ordered map: a Map preserves insertion order in
  // modern JS engines (ES2015+), so we can walk services in order and
  // bucket on the fly without re-sorting.
  const grouped = useMemo(() => {
    const groups = new Map<string, MasterServiceItem[]>();
    if (!services) return groups;
    for (const s of services) {
      const bucket = groups.get(s.category);
      if (bucket) bucket.push(s);
      else groups.set(s.category, [s]);
    }
    return groups;
  }, [services]);

  if (error) {
    return (
      <ErrorBanner
        message={error}
        onRetry={() => setReloadKey((k) => k + 1)}
      />
    );
  }
  if (services === null) return <Skeleton />;
  if (services.length === 0) return <EmptyState text={COPY.empty} />;

  // Tau §4.4: "Single service: skip category grouping (group too small)".
  const showGroups = grouped.size > 1;

  return (
    <div className="screen master-services">
      <header className="master-services__header">
        <h1>{COPY.title}</h1>
        <p className="master-services__catalog-note">{COPY.catalogNote}</p>
      </header>
      {showGroups
        ? Array.from(grouped.entries()).map(([category, items]) => (
            <section key={category} className="master-services__section">
              <h2 className="master-services__section-title">{category}</h2>
              {items.map((s) => (
                <ServiceCard key={s.service_id} service={s} />
              ))}
            </section>
          ))
        : services.map((s) => (
            <ServiceCard key={s.service_id} service={s} />
          ))}
      <p className="master-services__locked-edit-note">
        {COPY.lockedEditNote}
      </p>
    </div>
  );
}
