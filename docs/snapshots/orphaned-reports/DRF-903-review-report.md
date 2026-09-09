# DRF-903 — Review Report: PR #1128 CatalogMaster + MasterService bookable mirror

## Verdict

**BLOCKED**

PR #1128 cannot merge into the current `origin/dev` because its master-identity model conflicts with the masters-mirror implementation that `dev` already merged via commit `4f32a25` (pilot/bot-backend). Resolving the conflict requires an architectural decision on whether `CatalogMaster` keys on `SpecialistProfile.id` (current dev) or on `ayla_user_id` (PR #1128), plus a coordinated rollout of the Ayla S3B catalog schema that the PR depends on.

## PR state

- Repository: AndreyDeveloper84/ai-bot-platform
- PR: #1128
- State: OPEN
- Draft: false
- Base: dev
- Base SHA: f5a1fd05ecff6d6c4c26fc3d09a07d753a256a0b
- Current origin/dev SHA: ce7beb69894e795128899a1dca89d58bc297bbd7
- Head: feat/s3b-catalog-master-bookable
- Head SHA: 74ce4f4fc716a9e8417fd9d30bcad56f7c994aaa
- Head drift: none (matches expected SHA)
- Mergeable: CONFLICTING
- Merge state: DIRTY
- Reason for mergeable=false: branch is behind current `dev` and has file-level conflicts in `apps/catalog/services/{http_client,sync,upserter}.py`
- Review decision: (empty)
- Checks: pytest + ruff + mypy SUCCESS; replay fixtures SUCCESS
- Changed files: 10 (1 migration added, 9 modified)
- Reviews: none
- Unresolved threads: none

## Worktree safety

- Main worktree: C:/Users/user/PycharmProjects/ai-bot-platform (branch fix/wave1-rb1-d01-d02; left untouched)
- Review worktree: C:/Users/user/PycharmProjects/ai-bot-platform-drf-903 (detached HEAD 74ce4f4, clean)
- Baseline worktree: C:/Users/user/PycharmProjects/ai-bot-platform-drf-903-baseline (detached HEAD ce7beb6, clean)
- Shared WIP touched: no
- Files changed: no (review-only)
- Commit: no
- Push: no
- Review submitted: no
- Merge performed: no

## Diff summary

| File | Change | Risk |
|---|---|---|
| `apps/catalog/migrations/0011_masterservice_ayla_specialist_service_id_and_more.py` | added | migration defaults, partial unique on already-populated `ayla_user_id` |
| `apps/catalog/models.py` | +71/-6 | new MasterService bookable fields; partial unique on CatalogMaster.ayla_user_id |
| `apps/catalog/services/http_client.py` | +130/-4 | new `CatalogSpecialistServiceDTO`, `CatalogSpecialistDTO` reshaped, `CatalogNotFoundError` |
| `apps/catalog/services/sync.py` | +46/-4 | 3-endpoint orchestration; drops dev's per-mirror fetch isolation; drops global_bot skip |
| `apps/catalog/services/upserter.py` | +143/-4 | `upsert_masters` keyed by `ayla_user_id`; new `upsert_master_services` |
| `apps/catalog/services/tests/test_http_client.py` | +115/-0 | DTO/error parsing tests |
| `apps/catalog/services/tests/test_sync.py` | +60/-4 | 3-endpoint sync tests |
| `apps/catalog/services/tests/test_upserter.py` | +198/-0 | master/master-service upsert tests |
| `apps/catalog/tasks.py` | +4/-2 | accumulates `master_services` counters; **removed global_bot sentinel skip** |
| `apps/catalog/tests/test_models.py` | +105/-0 | model constraint tests |

## Identity matrix

| Upstream entity | Upstream ID | Local model | Local field | Meaning | Result |
|---|---|---|---|---|---|
| Ayla SalonService | `id` (UUID) | CatalogService | `ayla_service_id` | bookable service offer | ✅ matches PR #1125 / Ayla contract |
| Ayla SpecialistProfile | `id` (UUID) | CatalogMaster | `id` (in dev) | booking create `specialist_id` | ⚠️ PR sets `CatalogMaster.id` to bot-generated UUID; breaks dev consumers |
| Ayla User | `id` (UUID) | CatalogMaster | `ayla_user_id` | billing key (AMD-005) | ✅ field exists in both dev and PR |
| Ayla SpecialistService | `id` (UUID) | MasterService | `ayla_specialist_service_id` | future S2 bookable key | ⚠️ endpoint/schema not in canonical Ayla `djangoproject` |

PR #1128 keys `CatalogMaster` lookups on `ayla_user_id`, while `origin/dev` already uses `CatalogMaster.id = SpecialistProfile.id` for booking identity (commit `d5e416a` / #1027). The two models cannot coexist.

## Tenant isolation

| Path | Manager | Tenant filter | Cross-tenant risk | Result |
|---|---|---|---|---|
| `CatalogService.objects.update_or_create` | `.objects` under `tenant_scope` | `tenant=tenant`, `ayla_service_id` | none | ✅ |
| `CatalogMaster.objects.update_or_create` | `.objects` under `tenant_scope` | `tenant=tenant`, `ayla_user_id` | none if scope active | ✅ |
| `MasterService.objects.update_or_create` | `.objects` under `tenant_scope` | `tenant=tenant`, `master`, `service` | none if scope active | ✅ |
| `MasterService` legacy unique | model meta | `(master, service)` only | latent cross-tenant hole | ⚠️ not tenant-scoped |
| `_audit_and_emit` | unscoped | reads `current_tenant()` | writes `tenant=None` event/audit rows | ❌ P0/P1 |
| `CatalogSyncService._run_locked` | orchestrator | relies on upserters to set scope | future scoped access fail-closed | ⚠️ P1 |

## Model and constraints

### CatalogMaster

- legacy identity: `(tenant, external_id)` integer
- Ayla identity (PR): `(tenant, ayla_user_id)` partial unique
- unique constraints: `unique_together (tenant, external_id)` + partial `(tenant, ayla_user_id)` WHERE NOT NULL
- adoption/dedupe: PR dedupes edges by `ayla_user_id`; dev uses `id = SpecialistProfile.id`
- result: conflict with dev's existing identity model; partial unique may fail if `ayla_user_id` duplicates exist

### MasterService

- legacy identity: `(tenant, master, service)` — **not tenant-scoped**
- Ayla bookable identity: `(tenant, ayla_specialist_service_id)` partial unique
- `(master, service)` constraint: allows cross-tenant collisions in theory; in practice `tenant_scope` and explicit `tenant=` on writes prevent it
- partial unique constraint: correct WHERE NOT NULL syntax
- result: model additions are sound in isolation; defaults need gating

## Migration 0011

- dependencies: `catalog.0010`, `identity.0014`, `tenancy.0010`, auth.User
- additive: yes (only `AddField` + `AddConstraint`)
- table rewrite risk: none
- lock risk: `AddConstraint` acquires `ACCESS EXCLUSIVE`; may be long on `CatalogMaster` if `ayla_user_id` already populated
- default health: `resolved_requires_health_check = False` → fail-open for legacy rows
- default active: `is_active = True` → legacy/admin rows become bookable
- partial unique preflight: `CatalogMaster.ayla_user_id` added in `0007`; may already have duplicates → migration can fail
- reverse: auto-generated drop columns/constraints; data loss if rolled back after sync
- migration drift: `makemigrations --check --dry-run` → "No changes detected"
- result: structurally additive and safe on empty columns, but defaults create fail-open/fail-bookable risks for legacy rows

## Adopt-and-update

- lookup key: `(tenant, master, service)` via `update_or_create`
- adopted fields: `ayla_specialist_service_id`, `resolved_duration`, `resolved_requires_health_check`, `price`, `is_active`
- preserved fields: platform-owned fields untouched
- collision behavior: per-row savepoint rolls back single-row `IntegrityError`; batch continues
- race behavior: DB partial unique constraint is the backstop
- idempotency: stable UUID keys; re-run updates same rows
- result: ✅ correct adopt-and-update semantics

## Health semantics

- upstream field: `resolved_requires_health_check` from `SpecialistService.resolved_requires_health_check()`
- DTO behavior: `bool(row.get("resolved_requires_health_check", False))` — missing → False is explicit
- persisted value: stored as-is in `MasterService.resolved_requires_health_check`
- missing-field behavior: defaults to False
- legacy default risk: existing rows become `False` → booking gate fail-open if no Ayla edge
- reader exists: no reader in PR; PR #1127 has a fail-closed stub in `apps/skills/booking/skill.py`
- fail-open risk: high until reader is wired and backfill is complete
- result: ⚠️ P1 — field exists but is not consumed; legacy default unsafe

## Bookability

- upstream `is_active`: from `SpecialistService.is_active`
- persisted: stored in `MasterService.is_active`
- reader filter: none in PR; comments state S2 reader must filter `is_active=True`
- legacy default risk: all existing rows default to `True` → may become bookable
- stale edge: upsert-only with no tombstone/reconciliation; deleted upstream edges stay mirrored
- tombstone: none (full snapshot marker, seen-id set, last_seen_at, sync generation all absent)
- result: ⚠️ P1 — defaults unsafe; no tombstone strategy

## Sync orchestration

```text
salon-services (tenant-filtered list)
→ specialist-services (tenant-filtered list)
→ /internal/specialists/{id}/ for each edge.specialist (per-id enrichment)
→ CatalogService upsert
→ CatalogMaster upsert (identity from edges, name/bio from enrichment)
→ MasterService upsert
```

- transaction scope: per-upsert `transaction.atomic()`; no outer transaction across mirrors
- tenant lock: Redis advisory lock per tenant
- endpoint order: correct for FK availability
- partial failure: ❌ all three fetches inside one `try/except`; any transport/auth failure aborts whole cycle and writes nothing (regression from dev)
- retry: 3 attempts exponential backoff on 5xx/network only
- counters: services, masters, master_services
- duplicate HTTP calls: specialist ids deduped
- N+1: one detail GET per specialist
- timeout: per-request 30s; no total deadline
- result: ⚠️ P1 — loss of per-mirror isolation; N+1 enrichment; no total deadline

## Failure-mode matrix

| Scenario | Expected | Actual | Severity |
|---|---|---|---|
| salon-services 400/500 | abort cycle or log | aborts whole cycle, no writes | P1 |
| specialist-services 400/500 | should not abort salon-services | aborts whole cycle | P1 |
| detail 404 | skip enrichment, keep edge | ✅ skipped | P3 |
| detail 400/500 | propagate | propagates and aborts | ✅ |
| missing master/service for edge | skip + log | ✅ skipped | P3 |
| duplicate `ayla_specialist_service_id` | IntegrityError → row error | ✅ caught per-row | P3 |
| DB IntegrityError (race) | row-level rollback | ✅ per-row savepoint | P3 |
| global_bot tenant | skip | ❌ attempts sync every cycle | P0 |

## Upstream contract

- specialist identity: `SpecialistProfile.id` (UUID) — returned by `/internal/specialists/` and used by Ayla create/slots
- user identity: `User.id` (UUID) — returned as `user_id`; used by AMD-005 billing
- salon service identity: `SalonService.id` (UUID) — S3B schema; current canonical Ayla uses `Service.id`
- specialist-service identity: `SpecialistService.id` (UUID) — exists only in `djangoproject-catalog` worktree, not canonical `djangoproject`
- slots parameter identity: `service_id` = salon/service UUID (not `SpecialistService.id`)
- booking create identity: `specialist_id` = `SpecialistProfile.id`; `service_id` = resolved service UUID
- evidence: Ayla `appointments/tests/test_internal_booking_rest_1016.py:114-115`; `users/specialists_api.py:56,64`; `services/serializers.py:207-230` (catalog worktree)
- result: PR #1128 is built against the future S3B catalog schema (`djangoproject-catalog`), which is not yet in the canonical Ayla repo. Current Ayla expects `SpecialistProfile.id` as the booking specialist key.

## Cross-PR compatibility

### PR #1125

- service ID contract: `service_id` = `CatalogService.ayla_service_id` / salon-service UUID
- dependency: none blocking
- conflict: none
- merge order: first
- result: ✅ compatible with PR #1128, but callers must not pass `MasterService.ayla_specialist_service_id` to slots

### PR #1127

- resolved health dependency: adds fail-closed stub returning `True` when `BOOKING_VIA_AYLA_REST` is ON
- reader gap: PR #1128 adds `resolved_requires_health_check` column but does NOT update `apps/skills/booking/skill.py` to read it
- merge order: do not merge standalone; supersede with wiring commit after #1128
- result: ⚠️ merging #1127 + #1128 without a wiring commit still fails closed on every Ayla booking

### PR #1041

- feature flag: flips `BOOKING_VIA_AYLA_REST` ON in staging/production
- rollout preconditions: Ayla S2 endpoints live, real booking client shipped, `link_ayla_service_ids` coverage high
- blocker: must be LAST; flipping flag before resolved health-check reader is wired routes all Ayla bookings to human handoff
- result: ⚠️ high risk if merged before #1128 + wiring

## Rollout order recommendation

```text
1. PR #1125 — service_id for slots (clean, flag-OFF-inert)
2. Redesigned PR #1128 — merge only after resolving identity conflict with dev's 4f32a25
3. Wiring commit — read MasterService.resolved_requires_health_check in apps/skills/booking/skill.py (supersedes #1127)
4. PR #1041 — flip BOOKING_VIA_AYLA_REST ON (last)
```

## Validation

- dependency sync: ✅ `uv sync --extra dev --extra ai-core --frozen` succeeded
- catalog tests: ✅ 105 passed
- migration check: ✅ `makemigrations --check --dry-run` → "No changes detected"
- migration plan: ✅ `migrate --plan` includes 0011
- mypy: ✅ Success, no issues found in 1053 source files
- ruff check: ✅ All checks passed
- format: ✅ 40 files already formatted
- import boundaries: ✅ passed on `apps/catalog`
- red-zone guard: ✅ passed on `apps/catalog`
- broader suite: ⚠️ PR worktree fails at collection with circular import in `apps/bookings` (pre-existing in PR base f5a1fd0; fixed in current dev ce7beb6)
- baseline comparison: current dev `ce7beb6` collects successfully; PR base `f5a1fd0` has the circular import; full dev suite also shows pre-existing failures

## Findings

### F1 — Identity model conflicts with dev's already-merged master mirror

- Severity: **P0**
- Path: `apps/catalog/services/upserter.py:164`, `apps/catalog/models.py:347-357`, `apps/miniapp_api/views.py:754`, `apps/miniapp_api/views.py:1121`
- Evidence: `origin/dev` commit `4f32a25` sets `CatalogMaster.id = SpecialistProfile.id` and `d5e416a` sends `specialist_id=str(master.id)` to Ayla create. PR #1128 creates `CatalogMaster` with bot-generated UUID and keys mirror on `ayla_user_id`, so `master.id` would no longer be Ayla's specialist ID.
- Impact: booking create would send the wrong specialist ID (bot UUID instead of Ayla SpecialistProfile.id) → Ayla 422; personal booking lookups by `proxy.specialist_id` would fail.
- Required action: decide master identity model; if keeping dev's model, redesign PR to use `CatalogMaster.id = SpecialistProfile.id` and store `ayla_user_id` only as billing bridge.
- Merge-blocking: yes

### F2 — global_bot sentinel skip removed from catalog beat

- Severity: **P0**
- Path: `apps/catalog/tasks.py:68-70`
- Evidence: dev has `if tenant.slug == GLOBAL_BOT_TENANT_SLUG: continue`; PR removed it.
- Impact: beat will attempt to sync the non-salon global_bot tenant every cycle, producing perpetual failures/noise.
- Required action: restore the global_bot skip.
- Merge-blocking: yes

### F3 — Audit/event emission outside tenant_scope

- Severity: **P0/P1**
- Path: `apps/catalog/services/sync.py:174-192`, `apps/catalog/services/sync.py:235-257`
- Evidence: `_audit_and_emit` is called outside `tenant_scope(tenant)`; `write_audit` and `emit` read `current_tenant()`.
- Impact: audit/event rows lose tenant attribution.
- Required action: wrap `_run_locked` (or at least writes + audit) in `with tenant_scope(tenant):`.
- Merge-blocking: yes

### F4 — All fetches in single try/except aborts whole cycle

- Severity: **P1**
- Path: `apps/catalog/services/sync.py:145-154`
- Evidence: `salon-services`, `specialist-services`, and specialist detail fetches share one `except Exception`; dev isolated the specialists fetch.
- Impact: a transient `specialist-services` failure prevents salon-services mirror from landing.
- Required action: isolate fetches so partial failure does not abort already-safe work.
- Merge-blocking: no (operational degradation, not data loss)

### F5 — MasterService legacy unique_together not tenant-scoped

- Severity: **P1/P2**
- Path: `apps/catalog/models.py:457`
- Evidence: `unique_together = (("master", "service"),)` omits `tenant`.
- Impact: latent cross-tenant integrity hole if FKs are ever reused.
- Required action: change to `(("tenant", "master", "service"),)`.
- Merge-blocking: no

### F6 — `resolved_requires_health_check default=False` is fail-open

- Severity: **P1**
- Path: `apps/catalog/models.py:426-433`, migration `0011:54-61`
- Evidence: help text says booking gate reads this field; legacy rows get `False`.
- Impact: legacy/admin matrix rows that should require health check become bookable without one.
- Required action: make field nullable until backfilled, or gate booking on `ayla_specialist_service_id IS NOT NULL` + reader fail-closed.
- Merge-blocking: yes (until gating decision made)

### F7 — `is_active default=True` makes legacy rows bookable

- Severity: **P1**
- Path: `apps/catalog/models.py:441-448`, migration `0011:26-33`
- Evidence: existing `MasterService` rows default to `True`; S2 reader must filter on this flag.
- Impact: admin-created `(master, service)` rows not intended for Ayla booking may become bookable.
- Required action: default to `False` and set `True` only from Ayla edge evidence, or require `ayla_specialist_service_id IS NOT NULL` in reader.
- Merge-blocking: yes (until gating decision made)

### F8 — Migration 0011 may fail on existing `CatalogMaster.ayla_user_id` duplicates

- Severity: **P1**
- Path: migration `0011:62-69`
- Evidence: `ayla_user_id` was added in `0007`; sync/events may have populated it; partial unique validates immediately.
- Impact: migration acquires `ACCESS EXCLUSIVE` and then fails if duplicates exist.
- Required action: run preflight query on production; consider `NOT VALID` + separate validation migration.
- Merge-blocking: yes (until preflight proven clean)

### F9 — No tombstone/reconciliation for deleted upstream rows

- Severity: **P1/P2**
- Path: `apps/catalog/services/upserter.py:140-141`, `apps/catalog/services/sync.py`
- Evidence: upsert-only; no seen-id set, no reconciliation, no `last_seen_at`.
- Impact: deleted upstream services/specialists/edges remain mirrored and may be booked.
- Required action: document pilot limitation and schedule follow-up for snapshot/reconciliation.
- Merge-blocking: no (for pilot scope)

### F10 — PR depends on Ayla S3B schema not in canonical repo

- Severity: **P1**
- Path: `apps/catalog/services/http_client.py:223-243`
- Evidence: `GET /api/v1/internal/catalog/specialist-services/` and `SpecialistService` model exist only in `djangoproject-catalog` worktree, not in canonical `djangoproject`.
- Impact: PR cannot be exercised against current Ayla; merging before Ayla S3B lands breaks the sync.
- Required action: coordinate Ayla S3B schema rollout; do not merge until the endpoint is live in the target environment.
- Merge-blocking: yes

## Required actions before merge

1. Decide the master identity architecture:
   - Option A: keep dev's `CatalogMaster.id = SpecialistProfile.id` and adapt PR's MasterService edge onto it;
   - Option B: revert dev's `4f32a25` and adopt PR's `ayla_user_id`-keyed model (requires updating `apps/miniapp_api/views.py:754`, `:1121` and any other consumers).
2. Restore `global_bot` sentinel skip in `apps/catalog/tasks.py`.
3. Wrap `CatalogSyncService._run_locked` and `_audit_and_emit` in `tenant_scope(tenant)`.
4. Isolate `specialist-services` and specialist-detail fetch failures so salon-services can still land.
5. Make `MasterService.unique_together` tenant-scoped.
6. Decide legacy defaults for `is_active` and `resolved_requires_health_check` (recommend nullable + reader fail-closed until backfilled).
7. Run production preflight for duplicate `(tenant, ayla_user_id)` on `CatalogMaster` before applying migration 0011.
8. Wire `MasterService.resolved_requires_health_check` into `apps/skills/booking/skill.py` (supersedes PR #1127 stub).
9. Confirm Ayla S3B catalog schema (`SpecialistService`, `/internal/catalog/specialist-services/`) is live in target environment.
10. Rebase PR onto current `origin/dev` (ce7beb6) and resolve file conflicts.

## Remaining risks

- No tombstone/reconciliation strategy for upstream deletions.
- N+1 specialist detail enrichment could be slow for salons with many specialists.
- No total deadline/lock-TTL budget across paginated fetches + detail calls.
- PR #1041 flag flip can accidentally go live before the health reader is wired.

## Final reasoning

- **Identity correctness:** PR #1128's `ayla_user_id`-keyed master model is internally consistent with the future Ayla S3B contract, but it directly conflicts with `origin/dev`'s already-merged `CatalogMaster.id = SpecialistProfile.id` model that booking create depends on. This is the decisive P0.
- **Data/migration safety:** migration is additive, but defaults on `is_active` and `resolved_requires_health_check` are unsafe for legacy rows, and the partial unique on `CatalogMaster.ayla_user_id` can fail on existing duplicates.
- **Health/bookability safety:** fields exist but no reader consumes them; default values create fail-open and accidental-bookability risks.
- **Sync correctness:** 3-endpoint sequence is correct for FK availability, but loss of per-mirror failure isolation and missing tenant scope in audit are regressions from dev.
- **Rollout readiness:** PR depends on Ayla S3B schema that is not yet in the canonical Ayla repo, and it conflicts with dev. It cannot merge until the identity model is aligned and the dependency is live.

## Linear-ready update

- Verdict: **BLOCKED**
- Head SHA: 74ce4f4fc716a9e8417fd9d30bcad56f7c994aaa
- Drift: none
- mergeable=false reason: branch behind current dev (base f5a1fd0 vs dev ce7beb6) + file conflicts in catalog sync/client/upserter with dev's already-merged masters mirror (`4f32a25`)
- identity: conflict — PR keys master by `ayla_user_id`; dev keys `CatalogMaster.id` by `SpecialistProfile.id` and uses it as `specialist_id` for booking create
- tenancy: mostly correct inside upserters; audit/event emission outside `tenant_scope` and missing global_bot skip are blockers
- migration: additive but unsafe defaults; partial unique may fail on existing `ayla_user_id` duplicates
- health default: `False` is fail-open for legacy rows
- active default: `True` makes legacy/admin rows bookable
- sync: correct 3-endpoint order, but regression in failure isolation and tenant scoping
- upstream contract: built against future Ayla S3B schema (`djangoproject-catalog`); canonical Ayla does not have `specialist-services` endpoint yet
- cross-PR order: #1125 → redesigned #1128 → health-reader wiring commit (supersedes #1127) → #1041 last
- validation: catalog 105 pass; mypy/ruff/format/guards clean; broader suite blocked by pre-existing circular import in PR base
- blockers: P0 identity conflict; P0 global_bot skip removal; P0/P1 tenant scope in audit; P1 unsafe defaults; P1 Ayla S3B dependency not live; P1 migration duplicate risk
- recommended Linear status: **Blocked**
