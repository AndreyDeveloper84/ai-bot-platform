/**
 * Booking card — 3 variants per Tau §4 (real data, phase 3.2).
 *
 *   §4.1 «nearest» (isNearest=true)         — full actions (open /
 *                                              reschedule / cancel)
 *   §4.2 «future»  (upcoming non-nearest)   — limited actions (open)
 *   §4.3 «past»    (history)                — repeat + optional review
 *
 * Spec: `docs/screens/customer-records-flow.md` §4.
 *
 * Phase 3.2 (stub removal): the card renders `RecordItem` — real
 * `BookingItem` rows with derivations from `lib/customer-records.ts`.
 * Gone with the stub: «Сообщить по записи» (AMM endpoint does not
 * exist), «Маршрут» and tenant name (no source), price (no source).
 * Every remaining action maps to a real endpoint/screen.
 *
 * WCAG 2.5.8 — every action button ≥44dp via padding (handled in CSS).
 * WCAG 1.4.1 — status icon paired with text (StatusBadge).
 * WCAG 1.3.1 — card uses semantic `<article>` and a label-y header.
 *
 * Voice rules (memory `project_records_voice_principles`): «ты»
 * canonical, no exclamation marks, no selling tone.
 */

import type { RecordItem } from "../lib/customer-records";
import { renderStatus } from "../lib/customer-records";
import { PaymentStatusBadge } from "./PaymentStatusBadge";
import { StatusBadge, tintColourVar } from "./StatusBadge";

export type BookingCardVariant = "nearest" | "future" | "past";

interface Props {
  item: RecordItem;
  variant: BookingCardVariant;
  onOpen: () => void;
  onReschedule?: () => void;
  onCancel?: () => void;
  onRepeat?: () => void;
  onReview?: () => void;
}

export function BookingCard({
  item,
  variant,
  onOpen,
  onReschedule,
  onCancel,
  onRepeat,
  onReview,
}: Props) {
  const { rendering } = renderStatus(item.status);
  const accent = tintColourVar(rendering.tint);
  const actions = new Set(item.actions);
  const reviewPending = variant === "past" && actions.has("review");

  return (
    <article
      className={`records-card records-card--${variant}`}
      style={{ borderLeftColor: accent }}
      aria-labelledby={`rc-${item.bookingId}-title`}
    >
      <div className="records-card__status-row">
        <StatusBadge rendering={rendering} />
        {/* C7.3 — payment badge only when the passthrough ships a
            capture_state; hidden/unknown states render nothing. */}
        <PaymentStatusBadge state={item.paymentState} />
      </div>

      <div className="records-card__when">{item.datetimeLabel}</div>

      <div
        id={`rc-${item.bookingId}-title`}
        className="records-card__what"
      >
        {item.serviceName}
        {variant !== "past" && item.durationMin != null && (
          <span className="records-card__duration">
            {" · "}
            {item.durationMin} мин
          </span>
        )}
      </div>

      <div className="records-card__who">у {item.masterName}</div>

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
          aria-label="Открыть запись"
        >
          {variant === "nearest" ? "Открыть запись" : "Открыть"}
        </button>

        {/* Nearest-only — reschedule / cancel (real endpoints). */}
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

        {/* Past — repeat (real catalog) + optional review (real feedback). */}
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
