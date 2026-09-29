/**
 * DRF-1857 — «Мои отзывы» мастера.
 *
 * Заперто: без отзывов — «Отзывов пока нет», никаких «0.0» и «0 отзывов»;
 * сводка «оценка · число» из ответа; анонимный отзыв без имени; оценка без
 * отзывов (0.00 из старого сида) не рисуется; не загрузилось — строка ошибки,
 * а не пустота.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return { ...original, getMasterReviews: vi.fn() };
});

import { ApiError } from "../lib/api";
import { getMasterReviews } from "../lib/master-api";
import { MasterReviewsScreen, REVIEWS_COPY, reviewsSummary } from "./MasterReviewsScreen";

const mockedGet = vi.mocked(getMasterReviews);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("MasterReviewsScreen", () => {
  it("без отзывов — честная пустота, без нулей", async () => {
    mockedGet.mockResolvedValue({ review_count: 0, rating: null, reviews: [] });
    render(<MasterReviewsScreen />);
    expect(await screen.findByText(REVIEWS_COPY.empty)).toBeInTheDocument();
    expect(screen.queryByText(/0\.0/)).toBeNull();
    expect(screen.queryByText(/0 отзыв/)).toBeNull();
  });

  it("сводка и список из ответа", async () => {
    mockedGet.mockResolvedValue({
      review_count: 2,
      rating: 4.5,
      reviews: [
        {
          id: "r1",
          rating: 5,
          text: "Спасибо, всё аккуратно",
          client_name: "Ксения Л.",
          service_name: "Маникюр",
          created_at: "2026-09-10T10:00:00Z",
        },
        {
          id: "r2",
          rating: 4,
          text: "",
          client_name: null,
          service_name: null,
          created_at: "2026-09-01T10:00:00Z",
        },
      ],
    });
    render(<MasterReviewsScreen />);
    expect(await screen.findByText("4.5 · 2 отзыва")).toBeInTheDocument();
    expect(screen.getByText("Ксения Л.")).toBeInTheDocument();
    expect(screen.getByText(REVIEWS_COPY.anonymous)).toBeInTheDocument();
    expect(screen.getByText("Спасибо, всё аккуратно")).toBeInTheDocument();
    expect(screen.getByLabelText("Оценка 5 из 5")).toBeInTheDocument();
    expect(screen.queryByText(REVIEWS_COPY.empty)).toBeNull();
  });

  it("не загрузилось — строка ошибки, а не «отзывов нет»", async () => {
    mockedGet.mockRejectedValue(new ApiError(503, "reviews_unavailable", "…"));
    render(<MasterReviewsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent(REVIEWS_COPY.error);
    expect(screen.queryByText(REVIEWS_COPY.empty)).toBeNull();
  });
});

describe("reviewsSummary", () => {
  it("оценка без отзывов не рисуется; «0.00» — не оценка", () => {
    expect(reviewsSummary({ review_count: 0, rating: 4.8, reviews: [] })).toBeNull();
    expect(reviewsSummary({ review_count: 1, rating: "0.00" as unknown as number, reviews: [] })).toBe(
      "1 отзыв",
    );
    expect(reviewsSummary({ review_count: 12, rating: 4.83, reviews: [] })).toBe("4.8 · 12 отзывов");
  });
});
