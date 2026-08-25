import type { Service } from "../lib/api";
import { formatDuration, formatMoney } from "../lib/format";
import { UNBOOKABLE_NOTE, UnbookableBadge } from "./UnbookableNote";

interface Props {
  service: Service;
  onSelect: () => void;
}

export function ServiceCard({ service, onSelect }: Props) {
  // DRF-1164 — a service with no bookable performer keeps its place in the
  // catalog (owner's decision: show it, mark it) but the card says so up
  // front, so the customer learns it here instead of on an empty master
  // list. The aria-label carries the same fact — a screen-reader user must
  // not have to render the badge visually to know.
  const unbookable = !service.is_bookable;
  const meta = `${formatDuration(service.duration_min)}, ${formatMoney(service.price_from)}`;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`service-card ${unbookable ? "service-card--unbookable" : ""}`}
      aria-label={
        unbookable
          ? `${service.name}, ${meta}. ${UNBOOKABLE_NOTE}`
          : `${service.name}, ${meta}`
      }
    >
      <div className="service-card__name">{service.name}</div>
      <div className="service-card__meta">
        {formatDuration(service.duration_min)}
        {service.duration_min && service.price_from ? " • " : ""}
        {formatMoney(service.price_from)}
      </div>
      {unbookable && <UnbookableBadge />}
    </button>
  );
}
