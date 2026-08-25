/**
 * Master Mini App «Клиенты» tab — read-only customer roster.
 *
 * Route: /solo/customers (rendered inside `UnifiedSoloSurface`).
 *
 * Spec source: docs/screens/master-solo-surface.md §4.3 (Tau verdict
 * 2026-05-26 r1). Variant B navigation per §3 — this is the 3rd of the
 * 5 bottom tabs. Phase 1 Tier 2 read-only — no notes CRUD, no customer
 * detail screen. Those land post-pilot.
 *
 * **No customer phone, in any form** — not full, not masked, not the last
 * N digits (owner decision DRF-1039 / OD-W2-2, DRF-1360). Phone reveal is
 * out of scope, NOT deferred: it must not be built without a new separate
 * owner decision on PII.
 *
 * Backend contract: GET /api/v1/master/customers
 * (apps/master_api/views.py::customers_list →
 *  apps/master_api/services/customers.py::list_master_customers).
 *
 * Sections rendered (when data permits):
 *   1. Header — «Клиенты» title.
 *   2. Disabled search + sort stubs (Tier 2 deferred; visible to
 *      signal future capability per Tau §4.3 mock).
 *   3. «Активные» — customers whose last visit is within `AT_RISK_DAYS`.
 *   4. «Давно не были» — at_risk=true rows (last visit > 60d ago AND
 *      total_visits >= 3) per Tau §4.3 "⚠ Возможно, ушла" group.
 *
 * Empty / error states verbatim from Tau §4.3.
 */

import { useEffect, useMemo, useState } from "react";

import { ApiError } from "../lib/api";
import { getMasterCustomers, type MasterCustomer } from "../lib/master-api";
import { formatRelativePast } from "../lib/masterDateFormat";

// --- Russian copy (VERBATIM from Tau §4.3) -------------------------------

const COPY = {
  title: "Клиенты",
  searchPlaceholder: "Поиск клиента...",
  sortLabel: "По последнему визиту",
  sectionActive: (n: number) => `Активные (${n})`,
  sectionAtRisk: (n: number) => `Давно не были (${n})`,
  empty: "Здесь будет твой пул клиентов. Накопится после первых записей.",
  errorTitle: "Не получилось загрузить клиентов",
  retry: "Попробовать снова",
  lostHint: "⚠ Возможно, ушла",
  returningCustomer: "постоянный клиент",
  visitsSuffix: (n: number) => {
    // Russian pluralisation.
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return `${n} визит`;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
      return `${n} визита`;
    }
    return `${n} визитов`;
  },
  lastVisitPrefix: "Последний визит:",
};

// --- Card sub-component --------------------------------------------------

function CustomerCard({
  customer,
  variant,
}: {
  customer: MasterCustomer;
  variant?: "active" | "at-risk";
}) {
  const visitText =
    customer.last_visit_at != null
      ? `${COPY.lastVisitPrefix} ${formatRelativePast(customer.last_visit_at)}`
      : COPY.lastVisitPrefix;
  const serviceSuffix = customer.last_visit_service_name
    ? ` · ${customer.last_visit_service_name}`
    : "";

  const visitCountChip = customer.is_returning
    ? `${COPY.visitsSuffix(customer.total_visits)} · ${COPY.returningCustomer}`
    : COPY.visitsSuffix(customer.total_visits);

  return (
    <div
      className={`customer-card${
        variant === "at-risk" ? " customer-card--at-risk" : ""
      }`}
    >
      <div className="customer-card__name">{customer.first_name}</div>
      {/*
        No phone line — not full, not masked, not the last N digits.
        Owner decision DRF-1039 / OD-W2-2 (DRF-1360): «телефон клиента
        исполнителю не передаётся ни в каком виде». The backend no longer
        sends the field; do not add a client-side substitute.
      */}
      <div className="customer-card__last-visit">
        {visitText}
        {serviceSuffix}
      </div>
      {variant === "at-risk" && (
        <div className="customer-card__lost-hint">{COPY.lostHint}</div>
      )}
      <div className="customer-card__visit-count">{visitCountChip}</div>
    </div>
  );
}

// --- Skeleton + EmptyState helpers --------------------------------------

function Skeleton() {
  return (
    <div className="screen master-customers" aria-busy="true">
      <header className="master-customers__header">
        <h1>{COPY.title}</h1>
      </header>
      <div className="master-customers__body">
        {[1, 2, 3].map((i) => (
          <div key={i} className="skeleton customer-card customer-card--skel" />
        ))}
      </div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="screen master-customers">
      <header className="master-customers__header">
        <h1>{COPY.title}</h1>
      </header>
      <div className="master-customers__empty">
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
    <div className="screen master-customers">
      <header className="master-customers__header">
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

export function MasterCustomersScreen() {
  const [customers, setCustomers] = useState<MasterCustomer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setCustomers(null);
    setError(null);
    getMasterCustomers()
      .then((rows) => {
        if (cancelled) return;
        setCustomers(rows);
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

  // Partition into active / at_risk groups. Both groups are already in
  // last_visit_at DESC order from the backend.
  const { active, atRisk } = useMemo(() => {
    if (!customers) return { active: [], atRisk: [] };
    const a: MasterCustomer[] = [];
    const r: MasterCustomer[] = [];
    for (const c of customers) {
      if (c.at_risk) r.push(c);
      else a.push(c);
    }
    return { active: a, atRisk: r };
  }, [customers]);

  if (error) {
    return (
      <ErrorBanner
        message={error}
        onRetry={() => setReloadKey((k) => k + 1)}
      />
    );
  }
  if (customers === null) return <Skeleton />;
  if (customers.length === 0) return <EmptyState text={COPY.empty} />;

  return (
    <div className="screen master-customers">
      <header className="master-customers__header">
        <h1>{COPY.title}</h1>
      </header>
      <div className="master-customers__controls">
        {/* Search + sort are visible-but-disabled stubs per Tier 2 cut.
            Tau §4.3 reserves the UI; functionality is post-pilot. */}
        <input
          type="search"
          className="master-customers__search"
          placeholder={COPY.searchPlaceholder}
          disabled
          aria-disabled="true"
        />
        <button
          type="button"
          className="master-customers__sort"
          disabled
          aria-disabled="true"
        >
          {COPY.sortLabel}
        </button>
      </div>
      {active.length > 0 && (
        <section className="master-customers__section">
          <h2 className="master-customers__section-title">
            {COPY.sectionActive(active.length)}
          </h2>
          {active.map((c) => (
            <CustomerCard key={c.bot_user_id} customer={c} variant="active" />
          ))}
        </section>
      )}
      {atRisk.length > 0 && (
        <section className="master-customers__section">
          <h2 className="master-customers__section-title">
            {COPY.sectionAtRisk(atRisk.length)}
          </h2>
          {atRisk.map((c) => (
            <CustomerCard key={c.bot_user_id} customer={c} variant="at-risk" />
          ))}
        </section>
      )}
    </div>
  );
}
