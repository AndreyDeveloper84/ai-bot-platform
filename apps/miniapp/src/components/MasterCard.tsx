import type { Master } from "../lib/api";
import { publicRating } from "../lib/rating";

interface Props {
  master: Master;
  selected?: boolean;
  onSelect: () => void;
}

function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

export function MasterCard({ master, selected, onSelect }: Props) {
  // DRF-1224 — «0.00» is a truthy string and not a rating; see publicRating.
  const rating = publicRating(master.rating);
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`master-card ${selected ? "master-card--selected" : ""}`}
      aria-pressed={selected}
      aria-label={`Мастер ${master.name}${master.specialization ? `, ${master.specialization}` : ""}`}
    >
      <div className="master-card__avatar" aria-hidden="true">
        {master.photo_url ? <img src={master.photo_url} alt="" /> : <span>{initials(master.name)}</span>}
      </div>
      <div className="master-card__body">
        <div className="master-card__name">{master.name}</div>
        {master.specialization && <div className="master-card__spec">{master.specialization}</div>}
        {rating !== null && (
          <div className="master-card__rating" aria-label={`Рейтинг ${rating.toFixed(1)}`}>
            ★ {rating.toFixed(1)}
          </div>
        )}
      </div>
    </button>
  );
}
