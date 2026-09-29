/**
 * DRF-1482 — контекстные состояния отсутствия рекомендаций в каталоге.
 *
 * Spec: `docs/screens/customer-catalog-empty-states-spec.md` §1. An
 * empty catalog is not a system error but the next step of the dialog:
 * each state answers «Что Ayla предлагает сделать дальше?» with its
 * OWN message and its OWN recovery action — the screen is never blank
 * without an explanation (the pilot defect: search → 0 services with
 * masters present rendered nothing at all).
 *
 * Voice (spec §0.2 / §4): факт для объективных причин, никакой
 * внутренней логики наружу, никакого «ничего безопасного не нашла».
 *
 * region_empty CTA: the spec's «Сообщить, когда появятся» needs a
 * lead receiver that does not exist yet (spec §6.3 — separate
 * backend/lead task). The pilot forbids fake controls, so until the
 * receiver ships this state offers the one honest recovery the client
 * owns: «Проверить снова» (salons may have connected since the load).
 * The spec CTA is a one-line addition to `COPY.region_empty.ctas` once
 * the endpoint exists.
 */

import type { ResolvedCatalogEmpty } from "../lib/customer-catalog-empty";

interface Cta {
  label: string;
  action: "showAllServices" | "retry";
  primary?: boolean;
}

const COPY: Record<ResolvedCatalogEmpty, { text: string; ctas: Cta[] }> = {
  search_no_match: {
    text: "Не нашла ничего по такому запросу",
    ctas: [{ label: "Посмотреть все услуги", action: "showAllServices", primary: true }],
  },
  region_empty: {
    text: "Пока здесь нет подключённых салонов",
    ctas: [{ label: "Проверить снова", action: "retry", primary: true }],
  },
  booking_unavailable: {
    text: "Подходящие услуги есть, но сейчас нет свободных мест для записи. Могу подобрать другое время или похожие варианты.",
    ctas: [
      { label: "Подобрать ещё раз", action: "retry", primary: true },
      { label: "Смотреть все услуги", action: "showAllServices" },
    ],
  },
  // Spec §1 default row: a reason this build does not know renders the
  // booking_unavailable copy — never a blank screen.
  unknown: {
    text: "Подходящие услуги есть, но сейчас нет свободных мест для записи. Могу подобрать другое время или похожие варианты.",
    ctas: [
      { label: "Подобрать ещё раз", action: "retry", primary: true },
      { label: "Смотреть все услуги", action: "showAllServices" },
    ],
  },
};

interface Props {
  reason: ResolvedCatalogEmpty;
  /** «Посмотреть/Смотреть все услуги» — показать полный каталог. */
  onShowAllServices: () => void;
  /** «Подобрать ещё раз» / «Проверить снова» — перезагрузить каталог. */
  onRetry: () => void;
}

export function CatalogEmptyState({ reason, onShowAllServices, onRetry }: Props) {
  const { text, ctas } = COPY[reason];
  return (
    <div className="callout catalog-empty" role="status" aria-live="polite">
      <p style={{ margin: 0 }}>{text}</p>
      <div className="catalog-empty__ctas">
        {ctas.map((cta) => (
          <button
            key={cta.label}
            type="button"
            className={
              cta.primary
                ? "btn-secondary catalog-empty__cta-primary"
                : "btn-secondary"
            }
            onClick={cta.action === "showAllServices" ? onShowAllServices : onRetry}
          >
            {cta.label}
          </button>
        ))}
      </div>
    </div>
  );
}
