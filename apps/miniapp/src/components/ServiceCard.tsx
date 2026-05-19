import type { Service } from "../lib/api";
import { formatDuration, formatMoney } from "../lib/format";

interface Props {
  service: Service;
  onSelect: () => void;
}

export function ServiceCard({ service, onSelect }: Props) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className="service-card"
      aria-label={`${service.name}, ${formatDuration(service.duration_min)}, ${formatMoney(service.price_from)}`}
    >
      <div className="service-card__name">{service.name}</div>
      <div className="service-card__meta">
        {formatDuration(service.duration_min)}
        {service.duration_min && service.price_from ? " • " : ""}
        {formatMoney(service.price_from)}
      </div>
    </button>
  );
}
