/**
 * Time-bucket header — «Сегодня» / «Завтра» / «На этой неделе» /
 * «Через неделю» / «Позже» / «{Month} {Year}».
 *
 * Spec: `docs/screens/customer-records-flow.md` §3.2 (upcoming) + §3.4
 * (history month grouping). WCAG 1.3.1 — semantic `<h3>` nested
 * under the parent tenant `<h2>` (or just `<h2>` when there's no
 * tenant header in single-tenant case — but for simplicity we always
 * use `<h3>` and bump if needed).
 */

interface Props {
  label: string;
  /**
   * `level=2` when there's no parent tenant header (single-tenant
   * case — TenantGroupHeader isn't rendered), `level=3` otherwise.
   * Defaults to 3.
   */
  level?: 2 | 3;
}

export function TimeGroupHeader({ label, level = 3 }: Props) {
  const Tag = level === 2 ? "h2" : "h3";
  return <Tag className="records-time-header">{label}</Tag>;
}
