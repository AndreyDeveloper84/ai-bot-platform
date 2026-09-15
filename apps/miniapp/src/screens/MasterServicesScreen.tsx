/**
 * Master Mini App «Услуги» tab — read-only catalog view + «Свои услуги».
 *
 * Route: /solo/services (rendered inside `UnifiedSoloSurface`).
 *
 * Spec source: docs/screens/master-solo-surface.md §4.4 (Tau verdict
 * 2026-05-26 r1). Variant B navigation per §3 — 4th of 5 bottom tabs.
 * Phase 1 Tier 2 read-only — no edit / add / archive of catalog offers.
 * Catalog mutation lives in Ayla per ADR-0009 §Hard rule #4 (no new
 * transactional domains in bot-platform).
 *
 * Backend contract: GET /api/v1/master/catalog
 * (apps/master_api/views.py::catalog_list →
 *  apps/master_api/services/catalog.py::list_master_services).
 *
 * «Свои услуги» (DRF-1896, M18a; G6 / D6 владельца): услуга, которой нет в
 * каноне, — не новая строка каталога, а ЗАЯВКА о разрыве канона к владельцу
 * (`/api/v1/master/canon-gap-requests`, DRF-1802 → каталог DRF-1801).
 *   - Секция «Свои услуги · N»: N и статусы — только из ответа сервера.
 *   - Заявки НИКОГДА не смешиваются со списком каталога: pending-заявка не
 *     услуга и не входит ни в группы, ни в счёт каталога (фриз §11.2).
 *   - Форма «Добавить мою» сначала спрашивает «похожую услугу» у каталога;
 *     подсказка ничего не связывает. «Выбрать эту услугу» — выбор канона
 *     (M8), которого ещё нет: кнопка показана выключенной с объяснением, а
 *     не притворяется действием.
 *   - Решение по заявке принимает владелец — экран его только показывает.
 *
 * Layout per Tau §4.4:
 *   - Header «Услуги и цены».
 *   - Note «Каталог Студии Ольги · видят клиенты при записи».
 *   - Services grouped by category, sorted (category, name).
 *   - Read-only edit-disabled note at footer.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "../lib/api";
import {
  createCanonGapRequest,
  getMasterCatalog,
  getServiceSelection,
  getSimilarCanonTemplates,
  listCanonGapRequests,
  selectServices,
  type CanonGapRequest,
  type CanonGapSimilar,
  type MasterServiceItem,
} from "../lib/master-api";

// --- Russian copy (VERBATIM from Tau §4.4) -------------------------------

const COPY = {
  title: "Услуги и цены",
  catalogNote: "Каталог Студии Ольги · видят клиенты при записи",
  // Round-1 amendment (adversarial Code Reviewer): the original copy
  // hardcoded an individual operator name («Карина настроит вместе с
  // тобой») which leaks per-tenant identity into shared frontend
  // copy. Generic phrasing keeps the Tau §5.4 «promise, not broken»
  // voice without naming a specific person. We deliberately chose the
  // generic form (option B) over a salon-name parameter (option A)
  // because some salon names (e.g. «Студия Карины») would still leak
  // an individual identity.
  empty:
    "Мы настроим каталог вместе с тобой. Открой обсуждение, если нужна помощь.",
  errorTitle: "Не получилось загрузить услуги",
  retry: "Попробовать снова",
  lockedEditNote:
    "Редактирование услуг — в следующих обновлениях. Сейчас можно посмотреть, как видят клиенты.",
  priceOnRequest: "по запросу",
  durationUnit: "мин",
  priceUnit: "₽",
};

// --- «Свои услуги» copy (DRF-1896) ----------------------------------------

export const OWN_TITLE = "Свои услуги";
export const OWN_NOTE =
  "Услуги, которых нет в каталоге. Их проверяет владелец; клиенты увидят услугу только после подтверждения.";
export const OWN_EMPTY = "Своих услуг пока нет.";
export const ADD_OWN_LABEL = "Добавить мою";
export const ADD_ANYWAY_LABEL = "Всё равно добавить мою";
export const PICK_CANON_LABEL = "Выбрать эту услугу";
// DRF-1895: кнопка активна ⇔ выбор канона доступен этому мастеру (состояние
// выбора загрузилось с сервера). Салон, чей каталог ведёт владелец, выбрать не
// может — экран говорит это словами; любой другой отказ — кнопка выключена.
export const PICK_CANON_UNAVAILABLE = "Выбор услуги из каталога сейчас недоступен.";
export const SALON_MANAGED_MESSAGE =
  "Каталог салона ведёт владелец — выбрать услугу из каталога здесь нельзя.";
/** Счётчик — из ответа сервера, экран его не считает. */
export const pickedMessage = (selected: number) =>
  `Добавили в ваши услуги. Выбрано услуг: ${selected}.`;
export const SIMILAR_TITLE = "В каталоге есть похожая услуга:";
export const SENT_MESSAGE = "Отправили на проверку.";
export const NOT_LINKED_MESSAGE =
  "Профиль ещё не связан с каталогом — заявку пока некуда отправить.";
export const OWN_LOAD_ERROR = "Не получилось загрузить свои услуги";
export const FIELD_NAME = "Название";
export const FIELD_DESCRIPTION = "Описание";
export const FIELD_DURATION = "Длительность, мин";
export const FIELD_PRICE = "Цена, ₽";
export const ERR_NAME = "Укажите название.";
export const ERR_DURATION = "Длительность — целое число минут, не меньше 1.";
export const ERR_PRICE = "Укажите цену — число, не меньше 0.";

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

// --- Skeleton / ErrorBanner ------------------------------------------------

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

// --- «Свои услуги»: validation (pure, exported for tests) --------------------

export interface OwnServiceDraft {
  name: string;
  description: string;
  duration: string;
  price: string;
}

export type OwnServiceErrors = Partial<Record<"name" | "duration" | "price", string>>;

export function validateOwnService(draft: OwnServiceDraft): OwnServiceErrors {
  const errors: OwnServiceErrors = {};
  if (!draft.name.trim()) errors.name = ERR_NAME;
  const duration = Number(draft.duration);
  if (!/^\d+$/.test(draft.duration.trim()) || !Number.isInteger(duration) || duration < 1) {
    errors.duration = ERR_DURATION;
  }
  const price = Number(draft.price.replace(",", "."));
  if (!draft.price.trim() || !Number.isFinite(price) || price < 0) errors.price = ERR_PRICE;
  return errors;
}

function OwnRequestCard({ item }: { item: CanonGapRequest }) {
  return (
    <div className="service-card own-service-card" data-testid="own-service">
      <div className="service-card__name">{item.name}</div>
      <div className="service-card__meta">
        {`${item.duration_minutes} ${COPY.durationUnit} · ${item.price} ${COPY.priceUnit}`}
      </div>
      {/* Статус — словом сервера, не вычисляется на экране. */}
      <div className="own-service-card__status">{item.status_label}</div>
      {item.clarification_question && (
        <p className="own-service-card__note">{item.clarification_question}</p>
      )}
      {item.rejection_reason && (
        <p className="own-service-card__note">{item.rejection_reason}</p>
      )}
    </div>
  );
}

const EMPTY_DRAFT: OwnServiceDraft = { name: "", description: "", duration: "", price: "" };

function OwnServicesSection() {
  const [items, setItems] = useState<CanonGapRequest[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notLinked, setNotLinked] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState<OwnServiceDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<OwnServiceErrors>({});
  const [similar, setSimilar] = useState<CanonGapSimilar[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [selection, setSelection] = useState<
    "loading" | "available" | "salon_managed" | "unavailable"
  >("loading");

  const load = useCallback(() => {
    setLoadError(null);
    return listCanonGapRequests()
      .then((res) => {
        setItems(res.requests);
        setNotLinked(false);
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.slug === "not_linked") {
          setNotLinked(true);
          setItems([]);
          return;
        }
        setLoadError(e instanceof ApiError ? e.detail || e.slug : "Сеть недоступна");
      });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let alive = true;
    getServiceSelection()
      .then(() => {
        if (alive) setSelection("available");
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setSelection(
          e instanceof ApiError && e.slug === "salon_catalog_owner_managed"
            ? "salon_managed"
            : "unavailable",
        );
      });
    return () => {
      alive = false;
    };
  }, []);

  const refusal = (e: unknown) => {
    if (e instanceof ApiError && e.slug === "not_linked") {
      setNotLinked(true);
      return;
    }
    setSubmitError(e instanceof ApiError ? e.detail || e.slug : "Сеть недоступна");
  };

  const create = async () => {
    setBusy(true);
    setSubmitError(null);
    try {
      await createCanonGapRequest({
        name: draft.name.trim(),
        description: draft.description.trim(),
        duration_minutes: Number(draft.duration),
        price: draft.price.trim().replace(",", "."),
      });
      setFormOpen(false);
      setDraft(EMPTY_DRAFT);
      setSimilar(null);
      setMessage(SENT_MESSAGE);
      // Список и счётчик — заново с сервера, а не дописанной локально строкой.
      await load();
    } catch (e: unknown) {
      refusal(e);
    } finally {
      setBusy(false);
    }
  };

  const pick = async (templateId: string) => {
    setBusy(true);
    setSubmitError(null);
    try {
      const res = await selectServices([templateId]);
      setFormOpen(false);
      setDraft(EMPTY_DRAFT);
      setSimilar(null);
      setMessage(pickedMessage(res.selected));
    } catch (e: unknown) {
      if (e instanceof ApiError && e.slug === "salon_catalog_owner_managed") {
        setSelection("salon_managed");
        return;
      }
      refusal(e);
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    const found = validateOwnService(draft);
    setErrors(found);
    setMessage(null);
    if (Object.keys(found).length > 0) return;
    setBusy(true);
    setSubmitError(null);
    try {
      const res = await getSimilarCanonTemplates(draft.name.trim());
      if (res.similar.length > 0) {
        // Подсказка — и только: связь не создаётся, заявка не отправлена.
        setSimilar(res.similar);
        setBusy(false);
        return;
      }
    } catch (e: unknown) {
      if (e instanceof ApiError && e.slug === "not_linked") {
        setNotLinked(true);
        setBusy(false);
        return;
      }
      // Подсказка недоступна — это не повод не принять заявку.
    }
    setBusy(false);
    await create();
  };

  const field = (key: keyof OwnServiceDraft) => ({
    value: draft[key],
    onChange: (e: { target: { value: string } }) =>
      setDraft((d) => ({ ...d, [key]: e.target.value })),
  });

  return (
    <section className="master-services__section master-services__own" aria-label={OWN_TITLE}>
      <h2 className="master-services__section-title">
        {items === null ? OWN_TITLE : `${OWN_TITLE} · ${items.length}`}
      </h2>
      <p className="master-services__own-note">{OWN_NOTE}</p>
      {notLinked && (
        <p className="callout" role="status">
          {NOT_LINKED_MESSAGE}
        </p>
      )}
      {loadError && (
        <div className="callout callout--danger" role="alert">
          <p>{OWN_LOAD_ERROR}</p>
          <p style={{ fontSize: "var(--font-size-100)", opacity: 0.7 }}>{loadError}</p>
          <button type="button" className="btn-secondary" onClick={() => void load()}>
            {COPY.retry}
          </button>
        </div>
      )}
      {message && <p role="status">{message}</p>}
      {items !== null && items.length === 0 && !notLinked && <p>{OWN_EMPTY}</p>}
      {items?.map((item) => <OwnRequestCard key={item.id} item={item} />)}

      {!notLinked && !formOpen && (
        <button type="button" className="btn-secondary" onClick={() => setFormOpen(true)}>
          {ADD_OWN_LABEL}
        </button>
      )}

      {!notLinked && formOpen && (
        <form
          className="master-services__own-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label>
            {FIELD_NAME}
            <input type="text" {...field("name")} />
          </label>
          {errors.name && <p className="master-services__field-error">{errors.name}</p>}
          <label>
            {FIELD_DESCRIPTION}
            <textarea {...field("description")} />
          </label>
          <label>
            {FIELD_DURATION}
            <input type="text" inputMode="numeric" {...field("duration")} />
          </label>
          {errors.duration && <p className="master-services__field-error">{errors.duration}</p>}
          <label>
            {FIELD_PRICE}
            <input type="text" inputMode="decimal" {...field("price")} />
          </label>
          {errors.price && <p className="master-services__field-error">{errors.price}</p>}
          {submitError && (
            <p className="master-services__field-error" role="alert">
              {submitError}
            </p>
          )}

          {similar && similar.length > 0 ? (
            <div className="master-services__similar">
              <p>{SIMILAR_TITLE}</p>
              {similar.map((s) => (
                <div key={s.template_id} className="master-services__similar-item">
                  <span>{s.name}</span>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busy || selection !== "available"}
                    title={selection === "available" ? undefined : PICK_CANON_UNAVAILABLE}
                    onClick={() => void pick(s.template_id)}
                  >
                    {PICK_CANON_LABEL}
                  </button>
                </div>
              ))}
              {selection === "salon_managed" ? (
                <p className="master-services__similar-later">{SALON_MANAGED_MESSAGE}</p>
              ) : selection === "unavailable" ? (
                <p className="master-services__similar-later">{PICK_CANON_UNAVAILABLE}</p>
              ) : null}
              <button type="button" className="btn-primary" disabled={busy} onClick={() => void create()}>
                {ADD_ANYWAY_LABEL}
              </button>
            </div>
          ) : (
            <button type="submit" className="btn-primary" disabled={busy}>
              {ADD_OWN_LABEL}
            </button>
          )}
        </form>
      )}
    </section>
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

  // Tau §4.4: "Single service: skip category grouping (group too small)".
  const showGroups = grouped.size > 1;

  return (
    <div className="screen master-services">
      <header className="master-services__header">
        <h1>{COPY.title}</h1>
        {services.length > 0 && (
          <p className="master-services__catalog-note">{COPY.catalogNote}</p>
        )}
      </header>
      <div className="master-services__catalog" data-testid="catalog-services">
        {services.length === 0 ? (
          <div className="master-services__empty">
            <p>{COPY.empty}</p>
          </div>
        ) : showGroups ? (
          Array.from(grouped.entries()).map(([category, items]) => (
            <section key={category} className="master-services__section">
              <h2 className="master-services__section-title">{category}</h2>
              {items.map((s) => (
                <ServiceCard key={s.service_id} service={s} />
              ))}
            </section>
          ))
        ) : (
          services.map((s) => <ServiceCard key={s.service_id} service={s} />)
        )}
      </div>
      {/* Заявки о разрыве канона — отдельно от каталога и его счёта. */}
      <OwnServicesSection />
      {services.length > 0 && (
        <p className="master-services__locked-edit-note">
          {COPY.lockedEditNote}
        </p>
      )}
    </div>
  );
}
