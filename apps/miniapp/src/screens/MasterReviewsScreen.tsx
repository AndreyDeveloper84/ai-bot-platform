/**
 * DRF-1857 (карта кабинета K14) — «Мои отзывы» мастера вместо заглушки.
 *
 * Данные — `GET /master/reviews`, прокси в каталог под субъектом мастера:
 * видимые отзывы о его профиле, клиент как «Имя Ф.» / «Клиент» / `null` для
 * анонимного, оценка только при отзыве.
 *
 * Честные состояния: пока нет ни одного отзыва — «Отзывов пока нет», а не
 * «0.0 · 0 отзывов»; не загрузилось — своя строка, а не пустота (пустота
 * читалась бы как «отзывов нет»). Строка сводки собирается теми же
 * `publicRating` / `reviewCountLabel`, что и карточка мастера у клиента.
 */
import { useEffect, useState } from "react";

import {
  getMasterReviews,
  type MasterReview,
  type MasterReviewsResponse,
} from "../lib/master-api";
import { publicRating, reviewCountLabel } from "../lib/rating";

export const REVIEWS_COPY = {
  title: "Отзывы",
  loading: "Загружаю отзывы…",
  error: "Отзывы сейчас не загрузились. Попробуйте открыть раздел позже.",
  empty: "Отзывов пока нет",
  emptyHint: "Когда клиент оставит отзыв после визита, он появится здесь.",
  anonymous: "Анонимный отзыв",
} as const;

/** «4.5 · 2 отзыва»; без оценки — только число; без отзывов — `null`. */
export function reviewsSummary(data: MasterReviewsResponse): string | null {
  const count = reviewCountLabel(data.review_count);
  if (!count) return null;
  const rating = publicRating(data.rating);
  return rating === null ? count : `${rating.toFixed(1)} · ${count}`;
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

function Stars({ rating }: { rating: number }) {
  const filled = Math.max(0, Math.min(5, Math.round(rating)));
  return (
    <span className="master-reviews__stars" aria-label={`Оценка ${filled} из 5`}>
      {"★".repeat(filled)}
      {"☆".repeat(5 - filled)}
    </span>
  );
}

function ReviewItem({ review }: { review: MasterReview }) {
  const meta = [review.service_name, formatDate(review.created_at)].filter(Boolean).join(" · ");
  return (
    <li className="master-reviews__item">
      <div className="master-reviews__head">
        <span className="master-reviews__client">
          {review.client_name ?? REVIEWS_COPY.anonymous}
        </span>
        <Stars rating={review.rating} />
      </div>
      {meta ? <p className="master-reviews__meta">{meta}</p> : null}
      {review.text ? <p className="master-reviews__text">{review.text}</p> : null}
    </li>
  );
}

type State =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; data: MasterReviewsResponse };

export function MasterReviewsScreen() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let alive = true;
    getMasterReviews()
      .then((data) => {
        if (alive) setState({ kind: "ready", data });
      })
      .catch(() => {
        if (alive) setState({ kind: "error" });
      });
    return () => {
      alive = false;
    };
  }, []);

  const summary = state.kind === "ready" ? reviewsSummary(state.data) : null;

  return (
    <div className="screen master-reviews">
      <h1 className="master-reviews__title">{REVIEWS_COPY.title}</h1>
      {state.kind === "loading" ? (
        <p className="master-reviews__status" role="status">
          {REVIEWS_COPY.loading}
        </p>
      ) : null}
      {state.kind === "error" ? (
        <p className="master-reviews__status" role="alert">
          {REVIEWS_COPY.error}
        </p>
      ) : null}
      {state.kind === "ready" && summary === null ? (
        <div className="master-reviews__empty">
          <p className="master-reviews__empty-title">{REVIEWS_COPY.empty}</p>
          <p className="master-reviews__empty-hint">{REVIEWS_COPY.emptyHint}</p>
        </div>
      ) : null}
      {state.kind === "ready" && summary !== null ? (
        <>
          <p className="master-reviews__summary">{summary}</p>
          <ul className="master-reviews__list">
            {state.data.reviews.map((review) => (
              <ReviewItem key={review.id} review={review} />
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}
