# ADR-0008: Role detection foundation — TenantStaff + CatalogMaster split

**Status:** Accepted — 2026-05-19
**Related:** PR 1.5 (role-detection foundation), [`conversation-ownership-policy.md §4`](../design/policies/conversation-ownership-policy.md) (locked role + capability matrix), [`2026-05-18-master-management-handoff.md`](../design/handoffs/2026-05-18-master-management-handoff.md) MM2 (invite contract), ADR-0001 (multi-tenant), ADR-0003 (tenant context), PR #203 (`CatalogMaster.linked_bot_user`).

## Context

The platform has five canonical roles per [`conversation-ownership-policy.md §4`](../design/policies/conversation-ownership-policy.md) (the §4 permissions matrix is retained as the authoritative reference even though the surrounding tier-ownership doc was deprecated 2026-05-19):

- **Customer** — implicit, any BotUser without a staff or master link.
- **Master** — locked by PR #203 via `CatalogMaster.linked_bot_user` (OneToOne → `BotUser`).
- **Receptionist / Admin / Owner** — designed in §4 of the policy but with **no models or detection code in the platform today**.

Result before this ADR:

- The bot DM handler has no way to distinguish a customer from a salon owner writing to the same bot.
- The Mini App has no way to route the launch landing screen based on role.
- The MM2 invite contract references `role: "master" | "admin" | "receptionist"` but the backend cannot honour the latter two — there is nothing to write.
- The three admin-side roles in §4 are unenforceable: there is no row in any table that says "BotUser X is the Owner of Tenant Y".

PR 1.5 closes this gap by locking the role model in code so all downstream master/admin/owner backend work (intent router, /api/me, invite endpoints, role-change audit slug, Mini App routing) has a single source of truth to read from.

## Decision

1. **Roles enumerated** — in increasing privilege: `CUSTOMER` (implicit) < `MASTER` < `RECEPTIONIST` < `ADMIN` < `OWNER`. Every other code path that mentions a "role" name must match one of these slugs exactly.

2. **Storage choice — Option A: split staff from master.** A new `TenantStaff` table holds the three admin-side roles (`OWNER`, `ADMIN`, `RECEPTIONIST`). Master detection stays on `CatalogMaster.linked_bot_user` (PR #203). Customer is the absence of any staff link AND any master link — never a row in either table.

   Rationale: separates "person who delivers services" (the Master — has bio, photo, schedule, services, an external YClients staff id) from "person with admin chrome" (TenantStaff — has permissions only). A master can ALSO be an admin by holding both a `CatalogMaster.linked_bot_user` link AND a `TenantStaff` row; an admin without a `CatalogMaster` row simply doesn't deliver services.

3. **Multi-role per BotUser is additive.** A single BotUser can simultaneously be `{customer always, master if CatalogMaster.linked, staff if TenantStaff row}`. Highest-privilege wins for the **landing screen** and for the **role chip** on the frontend, but every capability the user qualifies for under any of their roles is available.

   *Example.* Anna is a master at Studia Karina (CatalogMaster.linked_bot_user → her BotUser) AND was promoted to admin (TenantStaff row with `role=ADMIN`). In the MAX bot DM her primary role is `admin` (highest of admin/master). The Mini App lands on `/admin/dashboard`. She can ALSO call customer endpoints to book a service at her own salon — her customer access is unaffected by her staff status.

4. **Dual-role disambiguation: server-side capability checks are authoritative.** The frontend role chip and landing screen are convenience only. Every privileged endpoint computes `has_capability(role_ctx, "<slug>")` server-side; the answer never depends on what role the frontend thinks the user has. The `/api/v1/me` endpoint returns the EFFECTIVE primary role (highest) PLUS the full capability set — the frontend uses the chip for display and the set for menu visibility, but every action that mutates state re-checks the capability server-side.

5. **Owner uniqueness — one Owner per tenant.** `TenantStaff` has a partial unique constraint: `unique_together(tenant) WHERE role='owner' AND deactivated_at IS NULL`. The unique row is the operator of record for that tenant — referenced by audit events, billing escalations, and §4's "Owner-only" capabilities (export data, manage tenant roles, tune assistant persona). Owner handover is a deactivate-and-reactivate flow (deactivate the old Owner row first, then create the new one) — not built in this PR.

6. **Customer access is never gated by staff role.** Every BotUser can call customer endpoints regardless of their staff status — customer is the implicit baseline, not a competing role. The capability matrix in §4 explicitly carves the "own only" rows for master so a master booking at their own salon has the customer view of their own bookings; for staff with no master link, the customer view simply shows their own customer-bookings (likely empty at most tenants but allowed).

7. **Cross-tenant: per-tenant rows, no cross-tenant role inference.** A person who works at two salons has two `BotUser` rows (one per tenant per channel, per `BotUser` uniqueness `(tenant, channel, channel_user_id)`) and two `TenantStaff` rows. There is no "global" role concept — a TenantStaff row in tenant A says nothing about that person's standing in tenant B.

## Consequences

**Positive:**

- The 5-role model in `conversation-ownership-policy.md §4` is now backed by code. Every reader of "role" goes through `resolve_role(bot_user)` and gets a single typed `RoleContext` dataclass.
- The Master / Admin split prevents a class of design mistakes: extending a master's profile fields (bio, photo, services) doesn't bloat staff rows for non-masters; extending staff permissions doesn't bloat the master mirror.
- Multi-role additive semantics let one row (the BotUser) carry as many capabilities as the person legitimately holds, without "switch role" UI for masters who happen to be admins.
- Owner uniqueness constraint is enforced at the DB level — admin tools cannot accidentally create a second Owner via a UI bug.
- Cross-tenant isolation already exists in the platform (`TenantScopedManager`); `TenantStaff` plugs into the same machinery and inherits the same audit guarantees.

**Negative:**

- Two tables to consult for every role resolution (`CatalogMaster.linked_bot_user` + `TenantStaff`). The resolver hides this behind a single function but the indirection is now part of the platform's mental model.
- The capability matrix is duplicated: §4 of the policy doc is the human-readable source of truth, and `role_resolver.CAPABILITIES` is the machine-readable copy. Both must move together — drift is caught by tests that assert the matrix shape, but not by the type system.
- "Receptionist with audit on phone view" is encoded as `True` in the matrix today with a TODO comment. The audit-on-access requirement lands in a separate PR; until then, a receptionist with `view_customer_phone_audited` capability technically passes the gate without writing an audit row.

**Acceptable:**

- No backfill of Owner rows. Each tenant starts with zero Owner rows after this migration; the operator who onboards a tenant promotes the first BotUser to Owner manually (Phase 4c onboarding flow). This is intentional — automatically electing a "first BotUser" as Owner has wrong-recipient failure modes, and Owner is a privileged-enough role that explicit operator action is the right safety bar.
- Tests that need an Owner fabricate the `TenantStaff` row directly. This is the same pattern existing tests use for `CatalogMaster` rows (no UI flow yet, fabricate directly).

## Alternatives considered

**Option B: enum field on BotUser.** Add `BotUser.role` as a `TextChoices` field with the five values. Rejected:

- A BotUser can be multi-role (Anna the master-and-admin) — a single enum field forces an artificial "primary role" choice at write time, and the additive-capabilities semantics in Decision 3 become impossible without either denormalising into another field or losing the master link.
- The customer role is the **absence** of staff data, not a positive value. Encoding it as `BotUser.role='customer'` means every code path must remember to backfill that string when creating a BotUser, and a NULL is impossible to distinguish from a customer.
- Tenant uniqueness of Owner cannot be expressed with a `BotUser.role` field — the uniqueness scope is `(tenant, role)`, not `(bot_user_id,)`, so the constraint lives on the assignment row, not the identity row.
- Demoting an admin back to a customer is a no-op via "delete the TenantStaff row" — but with an enum on BotUser you have to remember to flip the value back AND audit-log it. Higher cognitive load.

**Option C: unified Roles table** (one table for all five roles including a row per customer). Rejected:

- Customers vastly outnumber staff (likely 100×–1000× per tenant). A per-customer row in a Roles table is a write per customer registration, with zero useful payload (no permissions, no scopes — they're already a BotUser).
- Master rows would live in two places — both `CatalogMaster` (which has the bio / photo / services this Roles table can't carry) and a Roles row. Synchronisation drift is just a matter of time.
- The matrix in §4 already treats "customer" as a different shape (no UI chrome, no admin endpoints, just bot+miniapp). Pretending it's a peer of Owner/Admin in storage shape doesn't simplify any code path.

**Option D: implicit role from CatalogMaster.linked_bot_user only.** Rejected — has no path to express Admin/Receptionist/Owner without inventing a "fake master row" hack.

## Migration / rollout impact

- One forward migration in `apps/tenancy/migrations/000X_tenant_staff.py` — creates the `tenancy_tenantstaff` table + indexes + the partial unique Owner constraint. Reverse migration drops the table. No data migration; existing tenants get zero `TenantStaff` rows on apply.
- Existing `CatalogMaster.linked_bot_user` rows are untouched.
- No backfill: existing tenants have zero Owner rows after migrate. Each tenant's first Owner is created by an operator running a one-off management command (Phase 4c will add a `promote_to_owner` command); for tests, fabricate the row directly. This is documented in the runbook section that lands with the Phase 4c onboarding PR.
- The `/api/v1/me` endpoint added in this PR is purely read-only with no audit slug — read-once-on-launch is a normal request log entry, not a privileged action.
- Downstream PRs in the master-management track (invite Admin / Receptionist, role-change audit slug, bot intent router) consume this model — they cannot proceed without it.

## Linkages

- Capability matrix source of truth: [`docs/design/policies/conversation-ownership-policy.md §4`](../design/policies/conversation-ownership-policy.md).
- Machine-readable mirror: `apps/identity/services/role_resolver.py::CAPABILITIES`.
- Resolver entry-point: `apps/identity/services/role_resolver.py::resolve_role`.
- Storage: `apps/tenancy/models.py::TenantStaff` + `apps/catalog/models.py::CatalogMaster.linked_bot_user`.
- HTTP surface: `apps/identity/views.py::me_view`, mounted at `/api/v1/me`.
