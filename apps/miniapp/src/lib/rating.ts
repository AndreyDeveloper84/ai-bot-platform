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
// DRF-1229 PROOF-OF-GATE — DELIBERATE TYPE ERROR, REVERTED IN THE NEXT COMMIT.
// The vitest step is already proven red. This proves the OTHER step: that
// `npm run typecheck` actually propagates tsc's non-zero exit to the job.
export const PROOF_OF_GATE: number = "0.00";

export function publicRating(raw: string | number | null | undefined): number | null {
  if (raw === null || raw === undefined || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 1 ? value : null;
}
