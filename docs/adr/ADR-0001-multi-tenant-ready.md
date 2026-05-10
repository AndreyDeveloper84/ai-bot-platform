# ADR-0001: Multi-tenant-ready architecture from day one

**Status:** Accepted — 2026-05-07

## Context

We have one paying customer (`formula_tela`) and a pipeline of prospects. Phase 0 must serve `formula_tela` perfectly, but rebuilding the data model to add a tenant column later is a 6-month rewrite. Industry consensus (Salesforce, Stripe, Notion) is that *tenant_id everywhere* is cheap up front and ruinous to retrofit.

## Decision

Every domain model carries `tenant_id` from the first migration. `TenantContextMiddleware` (set in `apps.tenancy`) is mandatory on every request and Celery task. `STRICT_TENANT_SCOPE` runs in `audit` mode in prod from Sprint 2 and flips to `strict` in Sprint 9 after a 14-day soak. Test fixtures default to a tenant-scoped context; cross-tenant tests must opt in via `pytest.mark.cross_tenant`.

SaaS infrastructure — billing, self-service onboarding, region routing — is **not** built. Those are Phase 2+.

## Consequences

- **Easier:** adding tenant N+1 in Phase 1 is a CLI command + content load, not a migration.
- **Easier:** auditors / regulators can ask "show me all data for client X across our service" and we have a single column to scope by.
- **Easier:** replay fixtures (Sprint 5) record `tenant_id` so cross-tenant leak regressions are visible in diff.
- **Harder:** every contributor must remember the `tenant_scope` context manager. Mitigated by `STRICT_TENANT_SCOPE=strict` in tests + ruff lint rule (Sprint 2).
- **Acceptable:** ~8 bytes of FK + index overhead per row. Negligible at our scale (1–50 tenants for the foreseeable future).

## Alternatives considered

- **Single-tenant first, tenant column added later.** Rejected. `mysite/` hit 8 architectural cycles by avoiding clear tenant boundaries; we would not earn back the 6 months of retrofit pain.
- **Schema-per-tenant.** Rejected. Operational complexity (migrations × N tenants), worse query optimisation, harder backups, harder cross-tenant analytics. Single-DB shared-schema with `tenant_id` is the chosen Postgres pattern at our scale.
- **Database-per-tenant.** Rejected for the same reasons as schema-per-tenant, multiplied by N pgbouncers.
