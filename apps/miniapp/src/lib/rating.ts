/** Public master rating — the one place that decides whether there IS one.
 *
 * DRF-1224. The mirror stores the rating as a 1..5 decimal, serialised over
 * the wire as a string ("4.90"), and `null` when the column is NULL. But a
 * stored `0.00` is NOT a rating: it is what a master with no reviews behind
 * them carries, and on the pilot that is every master (reviews are blocked
 * upstream). Rendering it produced «★ 0.00» on every card — a number no
 * master can legitimately have, read by users as «bad master».
 *
 * The naive guard `master.rating ? …` does not catch it: "0.00" is a
 * non-empty STRING, i.e. truthy. Hence the explicit domain check here.
 */
export function publicRating(raw: string | number | null | undefined): number | null {
  if (raw === null || raw === undefined || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 1 ? value : null;
}

/**
 * «(108 отзывов)» — только когда число известно и больше нуля (DRF-1778).
 * Ноль и отсутствие поля — пустая строка: скобок с выдуманным числом не
 * бывает. Склонение — по русским правилам.
 */
export function reviewCountLabel(count: number | null | undefined): string {
  if (!count || !Number.isFinite(count) || count < 1) return "";
  const n = Math.floor(count);
  const mod10 = n % 10;
  const mod100 = n % 100;
  const word =
    mod10 === 1 && mod100 !== 11
      ? "отзыв"
      : mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)
        ? "отзыва"
        : "отзывов";
  return `${n} ${word}`;
}
