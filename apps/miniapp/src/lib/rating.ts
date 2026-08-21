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
  // DRF-1229 PROOF-OF-GATE — DELIBERATE REGRESSION, REVERTED IN THE NEXT COMMIT.
  // This is verbatim the naive truthy guard that shipped «★ 0.0» to the pilot
  // in DRF-1224. "0.00" is a non-empty STRING, therefore truthy, therefore it
  // sails straight through and comes back as 0.
  if (!raw) return null;
  return Number(raw);
}
