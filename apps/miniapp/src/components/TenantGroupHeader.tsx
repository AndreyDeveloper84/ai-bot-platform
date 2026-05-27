/**
 * Tenant group header for the multi-tenant Variant C grouping.
 *
 * Spec: `docs/screens/customer-records-flow.md` §3.3 — «── Beauty Place ──»
 * dashes for multi-tenant section. Single-tenant case skips this header
 * (Tau §3.2).
 *
 * WCAG 1.3.1: rendered as `<h2>` so screen readers expose the document
 * structure. The decorative dashes are aria-hidden — the accessible
 * name is just the tenant_name.
 */

interface Props {
  tenantName: string;
}

export function TenantGroupHeader({ tenantName }: Props) {
  return (
    <h2 className="records-tenant-header">
      <span className="records-tenant-header__dash" aria-hidden="true">
        ──
      </span>
      <span className="records-tenant-header__name">{tenantName}</span>
      <span className="records-tenant-header__dash" aria-hidden="true">
        ──
      </span>
    </h2>
  );
}
