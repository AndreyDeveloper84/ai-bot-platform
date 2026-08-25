/**
 * DRF-1164 — the honest label on a service nobody can currently perform.
 *
 * The owner's call out of three options: keep the service in the catalog,
 * mark it, and give it no path to booking. So this is a *label*, never a
 * control: it carries no click target and it never sits next to a CTA.
 * The card it decorates stays tappable (browsing is not booking — the
 * description and contraindications are still worth reading); what goes
 * away is the «Подобрать время» CTA on the detail screen.
 *
 * The copy is a single constant on purpose: the badge on the card and the
 * callout on the detail screen must say the same thing, or a customer who
 * taps through gets two different stories about the same service.
 *
 * The wording says *now* («сейчас») because the state is temporary — the
 * salon maps a master in the admin matrix and the service becomes
 * bookable on the next load. It never says "нет мастера" flatly, which
 * would read as "this salon cannot do this at all".
 */

export const UNBOOKABLE_NOTE = "Сейчас нет свободных мастеров";

export const UNBOOKABLE_DETAIL =
  "Сейчас нет свободных мастеров на эту услугу — записаться не получится. Загляни позже или выбери другую.";

/** Inline badge for a service row in a list. */
export function UnbookableBadge() {
  return <span className="unbookable-badge">{UNBOOKABLE_NOTE}</span>;
}
