/**
 * Booking card — 3 variants per Tau §4.
 *
 *   §4.1 «nearest» (is_nearest=true)        — full actions (5 buttons:
 *                                              open / message / route /
 *                                              reschedule / cancel)
 *   §4.2 «future»  (upcoming non-nearest)   — limited actions
 *                                              (open / message)
 *   §4.3 «past»    (history)                — repeat + optional review
 *
 * Spec: `docs/screens/customer-records-flow.md` §4.
 *
 * WCAG 2.5.8 — every action button ≥44dp via padding (handled in CSS).
 * WCAG 1.4.1 — status icon paired with text (StatusBadge).
 * WCAG 1.3.1 — card uses semantic `<article>` and a label-y header.
 *
 * Voice rules (memory `project_records_voice_principles`):
 *   - «Сообщить по записи» (NOT «Написать» per founder F1 lock)
 *   - «ты» canonical (F2 lock)
 *   - no exclamation marks
 *   - no selling tone
 */

import type { BookingListItem } from "../lib/customer-records";
import { renderStatus } from "../lib/customer-records";
import { StatusBadge, tintColourVar } from "./StatusBadge";

export type BookingCardVariant = "nearest" | "future" | "past";

interface Props {
  item: BookingListItem;
  variant: BookingCardVariant;
  onOpen: () => void;
  onMessage?: () => void;
  onRoute?: () => void;
  onReschedule?: () => void;
  onCancel?: () => void;
  onRepeat?: () => void;
  onReview?: () => void;
  /** Show «Можно оставить отзыв» badge — past variant only. */
  reviewPending?: boolean;
}

export function BookingCard({
  item,
  variant,
  onOpen,
  onMessage,
  onRoute,
  onReschedule,
  onCancel,
  onRepeat,
  onReview,
  reviewPending,
}: Props) {
  const { rendering } = renderStatus(item.status);
  const accent = tintColourVar(rendering.tint);
  const formattedPrice =
    typeof item.price_approx === "number"
      ? `${item.price_approx.toLocaleString("ru-RU")} ₽`
      : null;
  const actions = new Set(item.actions_available);

  return (
    <article
      className={`records-card records-card--${variant}`}
      style={{ borderLeftColor: accent }}
      aria-labelledby={`rc-${item.booking_id}-title`}
    >
      <div className="records-card__status-row">
        <StatusBadge rendering={rendering} />
      </div>

      <div className="records-card__when">{item.datetime_relative_label}</div>

      <div
        id={`rc-${item.booking_id}-title`}
        className="records-card__what"
      >
        {item.service_name}
        {variant !== "past" && (
          <span className="records-card__duration">
            {" · "}
            {item.duration_min} мин
          </span>
        )}
      </div>

      <div className="records-card__who">
        у {item.master_first_name}
        {" · "}
        {item.tenant_name}
      </div>

      {variant !== "future" && formattedPrice && (
        <div className="records-card__price">
          {variant === "past" ? formattedPrice : `~${formattedPrice}`}
        </div>
      )}

      {reviewPending && (
        <p className="records-card__review-hint" aria-live="polite">
          <span aria-hidden="true">★ </span>Можно оставить отзыв
        </p>
      )}

      <div className="records-card__actions">
        {/* Primary CTA — «Открыть запись» / «Открыть» per variant. */}
        <button
          type="button"
          className="btn-secondary records-card__action records-card__action--primary"
          onClick={onOpen}
          aria-label={
            variant === "nearest" ? "Открыть запись" : "Открыть запись"
          }
        >
          {variant === "nearest" ? "Открыть запись" : "Открыть"}
        </button>

        {/* Message — upcoming variants only (past bookings excluded per
            AMM §11.1). */}
        {(variant === "nearest" || variant === "future") &&
          actions.has("message") &&
          onMessage && (
            <button
              type="button"
              className="btn-secondary records-card__action"
              onClick={onMessage}
            >
              Сообщить по записи
            </button>
          )}

        {/* Nearest-only — route / reschedule / cancel. */}
        {variant === "nearest" && actions.has("route") && onRoute && (
          <button
            type="button"
            className="btn-secondary records-card__action"
            onClick={onRoute}
            aria-label="Маршрут до салона"
          >
            Маршрут
          </button>
        )}
        {variant === "nearest" &&
          actions.has("reschedule") &&
          onReschedule && (
            <button
              type="button"
              className="btn-secondary records-card__action"
              onClick={onReschedule}
            >
              Перенести
            </button>
          )}
        {variant === "nearest" && actions.has("cancel") && onCancel && (
          <button
            type="button"
            className="btn-secondary records-card__action records-card__action--danger"
            onClick={onCancel}
          >
            Отменить
          </button>
        )}

        {/* Past — repeat + optional review. */}
        {variant === "past" && actions.has("repeat") && onRepeat && (
          <button
            type="button"
            className="btn-secondary records-card__action"
            onClick={onRepeat}
          >
            Записаться ещё
          </button>
        )}
        {variant === "past" && actions.has("review") && onReview && (
          <button
            type="button"
            className="btn-secondary records-card__action"
            onClick={onReview}
          >
            Оставить отзыв
          </button>
        )}
      </div>
    </article>
  );
}

/**
 * Skeleton placeholder matching the card shape — used by the
 * Records screen's loading state. Aria-hidden so SR ignores it.
 */
export function BookingCardSkeleton() {
  return (
    <div className="records-card records-card--skeleton" aria-hidden="true">
      <div className="skeleton" style={{ width: "40%", height: 14 }} />
      <div
        className="skeleton"
        style={{ width: "55%", height: 18, marginTop: "var(--s-2)" }}
      />
      <div
        className="skeleton"
        style={{ width: "70%", height: 14, marginTop: "var(--s-1)" }}
      />
      <div
        className="skeleton"
        style={{ width: "50%", height: 14, marginTop: "var(--s-1)" }}
      />
    </div>
  );
}
