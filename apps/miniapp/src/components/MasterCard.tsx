import type { Master } from "../lib/api";
import { formatDistance } from "../lib/nearby";
import { publicRating, reviewCountLabel } from "../lib/rating";

interface Props {
  master: Master;
  selected?: boolean;
  onSelect: () => void;
  /**
   * DRF-1814 (экран 07, превью 6.4). Бейдж рисуется ТОЛЬКО при `true` —
   * значение приходит с сервера из реального расчёта слотов на сегодня
   * (`GET /master/profile/card`), никогда не вычисляется на экране.
   * Клиентская витрина проп не передаёт — там бейджа нет, как и раньше.
   */
  acceptsToday?: boolean;
  /** Категории выбранных шаблонов — чипы вместо одной подписи-специализации. */
  categories?: string[];
}

/** Слово бейджа — одно на превью мастера и на любой будущий экран клиента. */
export const ACCEPTS_TODAY_LABEL = "Принимает сегодня";

function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

export function MasterCard({ master, selected, onSelect, acceptsToday, categories }: Props) {
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
        {acceptsToday === true && (
          <div className="master-card__badge" data-testid="accepts-today">
            {ACCEPTS_TODAY_LABEL}
          </div>
        )}
        {categories && categories.length > 0 && (
          <ul className="master-card__chips" aria-label="Категории">
            {categories.map((c) => (
              <li key={c} className="master-card__chip">
                {c}
              </li>
            ))}
          </ul>
        )}
        {rating !== null && (
          <div className="master-card__rating" aria-label={`Рейтинг ${rating.toFixed(1)}`}>
            ★ {rating.toFixed(1)}
            {reviewCountLabel(master.review_count) && (
              <span className="master-card__reviews"> ({reviewCountLabel(master.review_count)})</span>
            )}
          </div>
        )}
        {/* DRF-1707 — расстояние с провода каталога; неизвестное не рисуется. */}
        {formatDistance(master.distance_meters) && (
          <div className="master-card__distance" data-testid="master-distance">
            {formatDistance(master.distance_meters)}
          </div>
        )}
      </div>
    </button>
  );
}
