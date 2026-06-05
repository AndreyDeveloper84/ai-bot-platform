# Memory schema specification — tabular companion to ADR-0011

> **Status:** v1 — extracted from ADR-0011 round-3 amendments, 2026-05-22
> **Companion doc:** [`docs/adr/ADR-0011-user-personal-context-privacy.md`](../adr/ADR-0011-user-personal-context-privacy.md) — the «why» prose. This document is the «what» — pure data tables.
> **Authority:** ADR-0009 §Memory model + §Hard rule #6.
> **Refactor rationale:** 3 amendment rounds on ADR-0011 prose produced 7+4+12 = 23 cumulative adversarial blockers, escalating not converging. Tabular extraction breaks the recursive-blocker pattern — data tables enumerate fields explicitly where dense privacy prose hides interpretation gaps. Implementers read this doc; ADR holds the rationale for «why».
> **Blocks:** #228 (UserPersonalContext model) · #229 (MemoryEntry model) · #230 (RedZoneAccessLog model). Same hard gate as ADR-0011.

---

## 1. `UserPersonalContext` columns

One row per user, ever. Cross-tenant. Soft-delete only.

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| `user_id` | UUID, PK, FK → User | no | — | Canonical Ayla `User.id`. 1-to-1 with UPC. |
| `created_at` | timestamptz | no | `now()` | Row insertion timestamp. |
| `updated_at` | timestamptz | no | `now()` | Last modification (managed by Django auto_now). |
| `soft_deleted_at` | timestamptz | yes | NULL | Set when user invoked forget-all OR account-closure. Hard-delete forbidden (152-ФЗ tombstone). |
| `display_name_preferred` | text | yes | NULL | Optional. Ayla's preferred display name. |
| `language_preferred` | text (ISO-639-1) | yes | NULL | e.g. `"ru"`. |
| `summary` | text | yes | NULL | Ayla's running summary, capped 8 KB application-side. |
| `forget_all_requested_at` | timestamptz | yes | NULL | Set when user invokes POST `/api/v1/users/me/memory/forget-all`. Records user-intent moment; async sweep then soft-deletes all entries. |
| `minor_lock` | boolean | no | `false` | Per ADR-0011 §10.2 + round-3 A9: set `true` when reconciliation job detects post-fact that the user is a minor; blocks future yellow/red writes. |

## 2. `MemoryEntry` columns

Many rows per UPC. Per-fact granularity.

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| `id` | UUID, PK | no | `uuid_generate_v4()` | |
| `user_id` | UUID, FK → User, indexed | no | — | Convenience denormalisation; matches `personal_context.user_id`. |
| `personal_context_id` | UUID, FK → UserPersonalContext, indexed | no | — | Owning UPC. |
| `sensitivity_zone` | enum `green`/`yellow`/`red`, indexed | no | — | Per-fact zone. See §5 for semantics. |
| `source` | enum `explicit`/`inferred`/`signal`, indexed | no | — | How the fact entered. See §6. |
| `last_inferred_at` | timestamptz | yes | NULL | Updated on every re-inference pass. **MUST be NULL when source=`explicit`. MUST be NOT NULL when source IN (`inferred`,`signal`).** See §4 Constraint 1. |
| `source_tenant_id` | UUID, FK → Tenant | yes | NULL | Tenant the fact originated at. NULL if origin is cross-tenant or platform-level. |
| `kind` | enum `preference`/`contraindication`/`symptom`/`lifestyle`/`relationship`/`financial`/`other` | no | `'other'` | Categorical for UX. |
| `content` | `EncryptedJSONField` (per ADR-0006) | no | `{}` | The fact payload. Encrypted at rest with the Fernet key. Red entries additionally hash-pepper'd before encryption (per ADR-0011 §6). |
| `created_at` | timestamptz | no | `now()` | |
| `last_used_at` | timestamptz | no | `now()` | Updated by the LLM access path (event-sourced batch, not synchronous — see ADR-0011 §6 + §13.2). |
| `last_used_count` | int | no | `0` | Rolled up by §13.2 yellow-zone access rollup job. |
| `ttl_days` | int | yes | NULL (green) / 365 (yellow) / 90 (red) | Per-zone defaults; NULL means no auto-TTL. See §5. |
| `consent_at` | timestamptz | yes | NULL | When user explicitly consented (yellow/red writes require it). See §4 Constraint 2. |
| `delete_requested_at` | timestamptz | yes | NULL | User-intent moment for entry deletion. |
| `soft_deleted_at` | timestamptz | yes | NULL | Set by the soft-delete job (or in same tx as withdrawal). |
| `deletion_reason` | enum `user_delete`/`withdrawal`/`forget_all`/`ttl_purge`/`minor_protection`/`unknown_legacy` | yes | NULL | Set in same UPDATE as `delete_requested_at`. NULL only on live rows. **`unknown_legacy` reserved for backfill** of any pre-existing soft-deleted rows at migration time (S1 fix). See §4 Constraint 3 + §7. |

**Indices** (besides PK + FK + the inline indexes above):

- `idx_memory_entry_zone_user` on `(user_id, sensitivity_zone)` — primary read path.
- `idx_memory_entry_ttl_red` on `(sensitivity_zone, last_used_at)` WHERE `sensitivity_zone='red' AND soft_deleted_at IS NULL` — supports nightly red-zone TTL sweep (#234).
- `idx_memory_entry_ttl_yellow` on `(sensitivity_zone, last_used_at)` WHERE `sensitivity_zone='yellow' AND soft_deleted_at IS NULL` — supports yellow-zone TTL sweep (§13.2).
- `idx_memory_entry_delete_request` on `(delete_requested_at)` WHERE `delete_requested_at IS NOT NULL AND soft_deleted_at IS NULL` — supports the delete-request worker.

## 3. `RedZoneAccessLog` columns

Append-only audit. INSERT-only DB role. 7-year retention. No FK CASCADE (audit history survives entry purge — see §4 fix for the v1.1 §7 vs §11.1 internal contradiction).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | UUID, PK | no | `uuid_generate_v4()` |
| `memory_entry_id` | UUID, FK → MemoryEntry (NO CASCADE) | no | The entry being accessed. **FK with `ON DELETE NO ACTION`** so log rows persist after entry purge. |
| `user_id` | UUID, FK → User (NO CASCADE) | no | Subject the entry belongs to. |
| `accessor_role` | enum `ayla_llm`/`system_job`/`ops_admin` | no | Coarse category. Round-2 AS2 fix: this alone is INSUFFICIENT for 152-ФЗ Chapter 3 «who accessed» auditor query — a `ops_admin` row doesn't say WHICH admin. The next column closes that gap. |
| `accessor_principal` | text | no | **Round-2 AS2 fix — concrete identity of the accessor.** Per `accessor_role`: <ul><li>`ayla_llm` → Celery worker hostname + queue name (e.g. `"ayla-celery-worker-3@ai-bot-platform-prod / queue:llm_inference"`)</li><li>`system_job` → cron job name + worker hostname (e.g. `"red_zone_ttl_sweep@ayla-celery-worker-1"`)</li><li>`ops_admin` → staff `User.id` as UUID string (the actual human; for 4-eyes break-glass, the row records the PRIMARY operator; the SECONDARY operator who co-authorized appears as a `companion_operator_id` field in the operator-audit-trail referenced by `request_id`)</li><li>`service_to_service` (future, when s2s reads land) → caller service-account name (e.g. `"service-account/memory-writer@ai-bot-platform"`)</li></ul> Max 256 chars. Indexed for «show me every access by staff X» queries. |
| `access_type` | enum `read`/`write`/`purge`/`withdrawal`/`write_rejected_dob_lookup` | no | What kind of access. Two new values (round-3 A6 + §11.3 ADR-0011): `withdrawal` for explicit consent withdrawal, `write_rejected_dob_lookup` for §10.2 fail-closed events. |
| `ts` | timestamptz, indexed | no | Access time. |
| `request_id` | text | no | UUID (validated by trigger §9 + accessor §10). FK semantic into operator audit (Celery task id / HTTP request id / ops ticket id). Validated at write time. |
| `purpose` | text | no | Human-readable purpose for the access (round-3 A5): `"contraindication_check"`, `"subject_access_request"`, `"incident_debug"`, `"ttl_sweep"`, ... |

**Indices on `RedZoneAccessLog` (round-2 AS2 supporting):**

- `idx_red_zone_log_user_ts` on `(user_id, ts)` — primary auditor query «every access to user X's red-zone data in date range».
- `idx_red_zone_log_principal_ts` on `(accessor_principal, ts)` — secondary auditor query «every access by staff X in date range» (round-2 AS2 enables this query — was impossible with `accessor_role` enum alone).
- `idx_red_zone_log_access_type_ts` on `(access_type, ts)` — operational «show me all withdrawals in Q3» / «show me all DOB-lookup failures last week».

## 4. CHECK constraints

Three DB-level CHECKs. All added with `NOT VALID` + `VALIDATE CONSTRAINT` pattern (§12). Constraint 3 includes backfill clause for any pre-existing soft-deleted rows in dev/staging (S1 fix from round-3).

| # | Name | Predicate | Semantics | Backfill |
|---|---|---|---|---|
| 1 | `memory_entry_inferred_nullness` | `(source='explicit' AND last_inferred_at IS NULL) OR (source IN ('inferred','signal') AND last_inferred_at IS NOT NULL)` | Explicit facts never inferred; inferred/signal facts always carry their inference timestamp. | N/A — greenfield table. |
| 2 | `memory_entry_yellow_red_requires_consent` | `sensitivity_zone='green' OR (sensitivity_zone IN ('yellow','red') AND (consent_at IS NOT NULL OR soft_deleted_at IS NOT NULL))` | Yellow/red entries need explicit consent OR soft-delete tombstone. Withdrawal flow uses the soft-delete exemption (see ADR-0011 §11.3). | N/A — greenfield. |
| 3 (S1) | `memory_entry_deletion_reason_nullness` | `(delete_requested_at IS NULL AND soft_deleted_at IS NULL AND deletion_reason IS NULL) OR (deletion_reason IS NOT NULL AND (delete_requested_at IS NOT NULL OR soft_deleted_at IS NOT NULL))` | Live rows have `deletion_reason IS NULL`; deleted rows have non-null reason. | **`UPDATE memory_entry SET deletion_reason='unknown_legacy' WHERE soft_deleted_at IS NOT NULL AND deletion_reason IS NULL` before `VALIDATE CONSTRAINT` step.** |

## 5. Sensitivity zones — semantics

| Zone | Default `ttl_days` | TTL trigger expression | Encryption | Audit log | `consent_at` required at write? | Withdrawal flow | Cross-tenant reuse |
|---|---|---|---|---|---|---|---|
| green | NULL (no auto-TTL) | N/A | EncryptedJSONField (homogeneous) | Not in RedZoneAccessLog | No (service-contract basis) | DELETE entry endpoint → `delete_requested_at` + `soft_deleted_at` + `deletion_reason='user_delete'` | Reusable freely |
| yellow | 365 | `GREATEST(last_used_at, consent_at) < now() - INTERVAL '365 days'` | EncryptedJSONField | Not in RedZoneAccessLog (rolled up via `last_used_count`) | **YES** (CHECK 2 enforces) | Same as red — soft-delete + `deletion_reason='withdrawal'`, app-layer read gate immediate | Reusable within session; voice-modulator filters from provider strings |
| red | 90 | `GREATEST(last_used_at, consent_at) < now() - INTERVAL '90 days'` | EncryptedJSONField + hash-pepper (ADR-0006 amendment §13.5) | **Every read + write + purge logs** (§3 access_type) | **YES** (CHECK 2 enforces) | Soft-delete + `deletion_reason='withdrawal'` + RedZoneAccessLog row with `access_type='withdrawal'` | USE-only; never speak back to user in mixed-company; never expose to provider |

**ADR canonical retention:** the values above supersede #229's `ttl_days` defaults and any «yellow indefinite» wording in `ayla-memory-and-personalization.md`. Amendment inline-applied in PRs that close #228 / #229 / #230.

## 6. `source` enum — provenance semantics

| Value | `last_inferred_at` | Set by | Audit meaning |
|---|---|---|---|
| `explicit` | MUST be NULL | User-typed input via Ayla chat, or explicit form submission | User stated this fact directly. Default consent basis. |
| `inferred` | MUST be NOT NULL | Ayla LLM inference from conversation context (sentiment, repeated patterns, behavioural classifiers) | Ayla derived this fact. Lower confidence; subject to user-side rebuttal. |
| `signal` | MUST be NOT NULL | Booking/payment/event-derived fact (e.g. «user paid for cancellation in last 30 days» → `late_canceller` signal) | Platform-derived from observable events. Different audit category from `inferred` because verifiability is higher. |

## 7. `deletion_reason` enum

| Value | Set by | Used for audit query | Notes |
|---|---|---|---|
| `user_delete` | DELETE `/api/v1/users/me/memory/{entry_id}` endpoint (#232) | «User-initiated deletions in Q3» | Most common; per-entry user action. |
| `withdrawal` | Yellow/red consent-withdrawal flow (ADR-0011 §11.3) | «Q3 consent withdrawals» (regulator audit) | Distinct from `user_delete` to enable §8 (152-ФЗ) per-right reporting. |
| `forget_all` | POST `/api/v1/users/me/memory/forget-all` (#233) async sweep | «Full-account forgets in Q3» | One UPC's `forget_all_requested_at` fires N entries with this reason. |
| `ttl_purge` | Nightly red-zone TTL sweep (#234) + yellow-zone TTL sweep (§13.2) | «Auto-purged stale entries in Q3» | Hygiene. |
| `minor_protection` | Reconciliation job (§13.4) when post-fact minor detected (round-3 A9) | «Minor-protection auto-purges in Q3» | Audit-defensible for regulatory review of minor-data handling. |
| `unknown_legacy` | **Backfill only** at migration time | «Pre-spec-era soft-deletes» | Reserved for dev/staging rows that existed before the schema-3 spec landed. Greenfield prod will never have this value. |

## 8. Production DB roles

Five roles. Two routine + one DDL + one backup + one break-glass.

| Role | DML on `memory_entry` | DML on `memory_entry.content` (red rows) | DDL | SELECT on `memory_entry_safe` view | Break-glass | Audit |
|---|---|---|---|---|---|---|
| `ayla_app` | Yes (read green/yellow always; read red ONLY with `ayla.red_zone_access_context` GUC set + UUID-valid via §9 trigger) | Same — gated by §9 trigger | NO | Yes (via view) | No | Per-request via RedZoneAccessLog (red); not logged (green/yellow) |
| `ayla_ops` | NO direct table SELECT for red (view-only) | NO | NO | Yes (red filtered out) | No | Routine debugging; OS-level SSH session log |
| `ayla_migrator` | NO DML on `memory_entry.content` (column-level GRANT denied even for red); other DDL allowed | NO | Yes (CREATE/ALTER/DROP table, column, constraint, trigger, view, role) | Yes (no PII access) | No | **Round-3 A3 fix:** CI pipeline assumes role for migration step only. **2-reviewer GH approval gate + `db-sensitive` label + CI gates migration apply on label presence.** |
| `ayla_backup` | Bypasses logical roles by design (REPLICATION privilege for `pg_basebackup`) | — | — | — | No | **Round-3 S7 + accepted residual risk per ADR-0011 §11.1.** Physical backups contain encrypted bytes; mitigation is the encryption + pepper layered defence + 30/90/quarterly retention rotation. |
| `ayla_ops_redzone_break_glass` | Time-limited app-role escalation (1h grant) | Yes (with mandatory GUC + audit) | NO | — | YES — 4-eyes (two named admins each enter passphrase via secret manager) | RedZoneAccessLog row with `accessor_role='ops_admin'` + OS-level SSH session audit + ticket-reference required in `purpose` |

**S6 fix (round-3 ayla_migrator DDL backdoor):** `ayla_migrator` could `ALTER TABLE memory_entry ADD COLUMN content_v2 jsonb` — new column has no trigger guard. Compromised CI pipeline = silent backdoor. Defence:

1. Every PR touching `memory_entry` schema MUST carry the `db-sensitive` GitHub label.
2. The CI pipeline's «apply migration as `ayla_migrator`» step gates on label presence; without label, migration step is skipped + PR is blocked from merge.
3. 2-reviewer GH approval required on labeled PRs (configured in `.github/CODEOWNERS` + branch protection).
4. CODEOWNERS for `apps/identity/migrations/*` includes founder + security steward.

## 9. Red-zone SELECT guard on `memory_entry`

> **Implementation note (added 2026-05-23 / veha 2):** §9 originally specified «`BEFORE SELECT` trigger». PostgreSQL does not support row-level BEFORE SELECT triggers. Implemented as **Row-Level Security (RLS) policy** with equivalent semantics — policy checks `current_setting(...)` against UUID regex, **denying** SELECT visibility on red rows unless valid context is set. This matches the «implementer's choice between RLS-policy expression OR equivalent mechanism» clause in the original §9 wording.
>
> Behavioural difference vs the original spec: a forbidden read no longer **raises** — RLS-filtered rows are simply invisible to the SELECT. The Python accessor (§10) ensures every red read goes through the audit-logging path which sets the GUC explicitly; any code path that bypasses the accessor sees empty results, NOT plaintext. Defence-in-depth holds (RLS at DB layer + accessor + AST lint at code layer).

| Mechanism | Fires on | Condition | Action |
|---|---|---|---|
| RLS policy `memory_entry_non_red_visible` | Every SELECT against `identity_memoryentry` | Row's `sensitivity_zone = 'red'` AND current session GUC `ayla.red_zone_access_context` is NOT a valid UUID | Row is invisible (filtered out by Postgres before result returns). NO exception raised — empty result instead. |

**Operational implication for ops/dev:** if you run `SELECT * FROM identity_memoryentry WHERE sensitivity_zone='red'` directly via `psql` as `ayla_app` without setting the GUC, you get **zero rows back** (not an error). This is intentional defence-in-depth — bypass attempts return nothing, not «access denied» (which leaks the existence of red rows). For legitimate ops access, use the break-glass procedure (§8).

```sql
-- RLS policy (round-3 A8 — empty-string-safe, UUID-validated;
--             round-5 Cat-4 fix — AS RESTRICTIVE so the filter actually applies)
ALTER TABLE identity_memoryentry ENABLE ROW LEVEL SECURITY;

-- AS RESTRICTIVE: Postgres combines permissive policies via OR, then
-- AND's each restrictive into the result. Without RESTRICTIVE, a
-- companion `FOR ALL ... USING (true)` permissive write-authorisation
-- policy would OR-collapse the SELECT filter to `true` and leak red
-- rows. RESTRICTIVE forces the filter to intersect, not union.
CREATE POLICY memory_entry_non_red_visible
    ON identity_memoryentry
    AS RESTRICTIVE
    FOR SELECT
    USING (
        sensitivity_zone != 'red'
        OR (
            current_setting('ayla.red_zone_access_context', true) ~
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        )
    );

-- FORCE means the policy applies even to the table owner (otherwise owner
-- bypasses RLS). Required because Django migrations + admin might run as
-- owner-equivalent role.
ALTER TABLE identity_memoryentry FORCE ROW LEVEL SECURITY;

-- ayla_migrator needs full table access for schema changes; ALL includes
-- BYPASSRLS-equivalent via grants (RLS still applies but migrator can DDL).
GRANT ALL ON identity_memoryentry TO ayla_migrator;
```

**Why RLS, not trigger:** Postgres has no row-level `BEFORE SELECT` trigger. Statement-level triggers don't see individual rows, and the «trigger raises exception» semantics from the original §9 spec cannot be implemented row-by-row on SELECT. RLS gives equivalent protection (red rows invisible without GUC) with native Postgres mechanism + zero accessor pattern code changes (`MemoryEntry.objects.get()` just naturally returns `DoesNotExist` when RLS filters the row out — which is the correct behaviour for the Django accessor pattern).

**pgbouncer caveat (S3):** with pgbouncer transaction-pooling, the session GUC is reset between transactions. The accessor (§10) MUST set the GUC INSIDE the transaction (`SET LOCAL ayla.red_zone_access_context = ...`) so it's bound to the transaction. With session-pooling, behaviour is also safe because pgbouncer ties a session to one transaction lifetime. Pseudo-code in §10 uses `SET LOCAL` semantically.

## 10. `RedZoneReader` accessor

Sole entry point for red-zone reads from `ayla_app`. Lives at `apps/identity/services/red_zone_reader.py`.

| Attribute | Value |
|---|---|
| Signature | `read(entry_id: UUID, user_id: UUID, accessor_role: str, request_id: UUID, purpose: str) -> bytes` (decrypted content). **`user_id` MUST be passed by caller — round-2 AS1 fix.** Caller already has it from JWT verifier middleware (the verifier already looked up `user_id` from `jwt['sub']` per jwt-contract.md §8.2). Reading it FROM the entry would require a SELECT-first ordering that defeats the audit-before-read invariant. |
| Transaction | Opens an `atomic()`; sets GUC; writes log row FIRST; reads entry SECOND; commits |
| GUC management | `SET LOCAL ayla.red_zone_access_context = <request_id>` inside the transaction; cleared by `SET LOCAL` semantics on commit/rollback. Explicit `try/finally` reset retained as defence-in-depth (S3) |

```python
class RedZoneReader:
    @classmethod
    def read(
        cls,
        entry_id: UUID,
        user_id: UUID,          # Round-2 AS1 fix — caller-supplied (from JWT verifier)
        accessor_role: str,
        request_id: UUID,
        purpose: str,
        accessor_principal: str,  # Round-2 AS2 fix — see §3 RedZoneAccessLog
    ) -> bytes:
        # Round-2 AS1 — Audit-before-read invariant + no orphan-log:
        # The transaction either commits BOTH the audit row AND the read result,
        # or rolls back BOTH. Two failure modes covered:
        #   (a) SELECT fails (entry missing / soft-deleted) → DoesNotExist raised
        #       INSIDE the atomic block → audit row is rolled back automatically.
        #       No orphan «successful read» log for a read that did not happen.
        #   (b) Audit INSERT fails (DB role missing, constraint violation) →
        #       SELECT never executes → no plaintext exposure.
        #
        # `user_id` is REQUIRED in signature (was literal ellipsis in v1 spec —
        # AS1 fix). Caller obtains it from JWT verifier middleware (jwt-contract.md
        # §8.2). Reading user_id FROM the entry would require SELECT-first ordering
        # that defeats audit-before-read.
        with transaction.atomic():
            # SET LOCAL — bound to this transaction; auto-cleared on commit/rollback.
            # Works correctly under pgbouncer transaction-pooling AND session-pooling.
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT set_config('ayla.red_zone_access_context', %s, true)",
                    [str(request_id)],  # MUST be UUID — trigger regex-checks
                )
            try:
                # Step 1: write audit row FIRST. If this fails (DB role missing,
                # FK violation, etc.) the SELECT below never runs.
                RedZoneAccessLog.objects.create(
                    memory_entry_id=entry_id,
                    user_id=user_id,          # AS1 — caller-supplied, not ellipsis
                    accessor_role=accessor_role,
                    accessor_principal=accessor_principal,  # AS2 — concrete identity
                    access_type='read',
                    request_id=request_id,
                    purpose=purpose,
                )
                # Step 2: SELECT. If it fails (DoesNotExist), the atomic block
                # rolls back EVERYTHING including the audit row. The
                # «no-orphan-log» invariant.
                entry = MemoryEntry.objects.select_for_update().get(
                    id=entry_id,
                    user_id=user_id,          # AS1 — assert ownership at query time
                    sensitivity_zone='red',
                    soft_deleted_at__isnull=True,
                    delete_requested_at__isnull=True,
                )
                return entry.content  # decryption via EncryptedJSONField
            finally:
                # Defence-in-depth GUC reset (round-3 S3 retain). SET LOCAL alone is
                # correct under transactional pooling; explicit reset adds belt-and-
                # braces if a future refactor accidentally moves code outside
                # `transaction.atomic()`.
                with connection.cursor() as cur:
                    cur.execute("SELECT set_config('ayla.red_zone_access_context', '', false)")
```

**Trade-off named explicitly (round-2 AS1):**

Two implementation choices were considered:
1. **Audit-FIRST then SELECT** (chosen, above): audit row commits + SELECT inside same atomic. If SELECT fails, audit row rolls back. Pro: no orphan logs. Con: requires `user_id` in signature.
2. **SELECT-FIRST then audit** (rejected): read entry, then write audit row. Pro: simpler signature. Con: window between SELECT and audit-INSERT where decrypted plaintext exists in process memory without a log row yet. If process crashes between steps, plaintext was exposed to memory without audit — auditor cannot account for it.

Choice 1 wins because the «caller has user_id from JWT» path is cheap (one extra argument), and the no-orphan-log invariant is structurally stronger than «we promise to always reach the audit-write step».


## 11. Lint rule — ALLOWLIST (S4 inversion)

Round-2 v1.1 specified a blocklist of 6 bypass patterns. Round-3 adversarial review showed blocklists are brittle (new Django ORM features have no coverage). v1.2 inverts:

**Rule:** ANY reference to `MemoryEntry` outside `apps/identity/services/red_zone_reader.py` is **DENIED** by the lint. The lint is AST-based (using `libcst` or `ast.NodeVisitor`), runs in pre-commit + CI, and fails on:

- `from apps.identity.models import MemoryEntry` (any import) — outside the accessor module.
- `apps.identity.models.MemoryEntry` (qualified reference) — outside the accessor module.
- Any string `"MemoryEntry"` in `ContentType.objects.get_for_model(...)` patterns — covers Django introspection bypass.

**Allowed sites:**
- `apps/identity/services/red_zone_reader.py` (the accessor itself)
- `apps/identity/services/memory_writer.py` (the writer — separate but in the same module group)
- `apps/identity/migrations/*.py` (migrations need the model reference)
- `apps/identity/admin.py` (Django admin registration — relies on the BEFORE SELECT trigger + role separation)
- `apps/identity/tests/**.py` (test code)

CI test `tests/test_red_zone_guard.py` exercises:

- A positive case (import in `red_zone_reader.py`) → lint passes.
- A negative case (import in any other module) → lint fails.
- Each of the original 6 bypass patterns reframed as «code that mentions MemoryEntry where it shouldn't» → lint fails.

Rule lives in `tools/lint/red_zone_guard.py`. Ships in the PR that creates `RedZoneAccessLog` (#230).

## 12. Migration ordering (NOT VALID + VALIDATE pattern)

Each schema migration that adds a tightening CHECK to `memory_entry` follows this sequence (one migration file per step):

| Step | Migration | What it does |
|---|---|---|
| 1 | Add column | `ALTER TABLE memory_entry ADD COLUMN ...` — fast, no data scan |
| 2 | Backfill | `UPDATE memory_entry SET column = default_value WHERE column IS NULL` — chunked, off-peak |
| 3 | Backfill validate | Assert no row violates the soon-to-be-CHECK predicate via SELECT |
| 4 | Add CHECK NOT VALID | `ALTER TABLE memory_entry ADD CONSTRAINT ... CHECK (...) NOT VALID` — only validates NEW rows |
| 5 | Validate | `ALTER TABLE memory_entry VALIDATE CONSTRAINT ...` — scans existing rows; off-peak |
| 6 | Drop NOT VALID flag (implicit on successful VALIDATE) | — |

For the greenfield initial migration in #229, steps 1–3 collapse into a single migration with the column at default; step 4-5 still split per Postgres recommendation to avoid table-lock storms even on greenfield (greenfield assumption sometimes proven false in dev/staging).

## 13. §14 verification checklist (per-ticket attribution — S11 fix)

| # | Check | Owner ticket | Acceptance test location |
|---|---|---|---|
| 14.1 | `MemoryEntry` migration adds: `sensitivity_zone`, `source`, `last_inferred_at`, `delete_requested_at`, `consent_at`, `deletion_reason` columns + 3 CHECK constraints (§4) | #229 | `apps/identity/tests/test_migrations.py::test_memory_entry_schema` |
| 14.2 | Reverse migration drops constraints + columns cleanly | #229 | `apps/identity/tests/test_migrations.py::test_memory_entry_reverse` |
| 14.3 | `UserPersonalContext` migration adds: `forget_all_requested_at`, `minor_lock` columns | #228 | `apps/identity/tests/test_migrations.py::test_upc_schema` |
| 14.4 | `MemoryEntry.content` uses `EncryptedJSONField` | #229 | `apps/identity/tests/test_encryption.py::test_content_field_encrypted` |
| 14.5 | `RedZoneAccessLog` migration: append-only role + no FK CASCADE + new enum values (`withdrawal`, `write_rejected_dob_lookup`) | #230 | `apps/identity/tests/test_audit_log.py::test_red_zone_access_log_constraints` |
| 14.6 | DB roles + view + trigger per §8 + §9: `ayla_app`, `ayla_ops`, `ayla_migrator`, `ayla_backup` + `memory_entry_safe` view + BEFORE SELECT (or RLS) red-zone trigger | #230 | `apps/identity/tests/test_db_roles.py` |
| 14.7 | `RedZoneReader` accessor exists with signature `(entry_id, accessor_role, request_id, purpose)` + AST-based allowlist lint passes | #229 | `apps/identity/tests/test_red_zone_reader.py` + `tests/test_red_zone_guard.py` |
| 14.8 | Memory writer enforces minor protections + fail-CLOSED on REST outage (ADR-0011 §10.2) + writes audit log on red read | #229 | `apps/identity/tests/test_memory_writer.py` |
| 14.9 | Test: insert `(source='explicit', last_inferred_at NOT NULL)` → CHECK 1 raises | #229 | Same as 14.1 |
| 14.10 | Test: insert `(source='inferred', last_inferred_at NULL)` → CHECK 1 raises | #229 | Same as 14.1 |
| 14.11 | Test: insert yellow row `(consent_at NULL, soft_deleted_at NULL)` → CHECK 2 raises | #229 | Same as 14.1 |
| 14.12 | Test: insert yellow row `(consent_at NULL, soft_deleted_at NOT NULL)` → CHECK 2 allows | #229 | Same as 14.1 |
| 14.13 | Test: live row with `(deletion_reason NOT NULL)` → CHECK 3 raises | #229 | Same as 14.1 |
| 14.14 | Test: soft-deleted row with `(deletion_reason NULL)` → CHECK 3 raises | #229 | Same as 14.1 |
| 14.15 | Test: pre-existing soft-deleted row backfilled with `deletion_reason='unknown_legacy'` before VALIDATE — VALIDATE succeeds | #229 (migration script) | `apps/identity/migrations/0NNN_memory_entry_initial.py` self-test |
| 14.16 | Test: red read without `RedZoneReader` accessor → writer raises before DB | #229 | `apps/identity/tests/test_red_zone_reader.py::test_direct_access_denied` |
| 14.17 | Test: direct `psql -U ayla_app SELECT WHERE sensitivity_zone='red'` without GUC → trigger raises | #230 | `apps/identity/tests/test_db_trigger.py` |
| 14.18 | Test: GUC with empty string OR non-UUID → trigger raises; UUID → trigger allows | #230 | Same as 14.17 |
| 14.19 | Test: zone-promotion `green → yellow` without `consent_token` → writer raises `ZonePromotionRequiresConsent` (ADR-0011 §11.2) | #229 | `apps/identity/tests/test_zone_promotion.py` |
| 14.20 | Test: minor-age reconciliation (§13.4) sweeps freshly-18 users + clears minor_lock | #229 + reconciliation ticket (filed before #229) | `apps/identity/tests/test_minor_reconciliation.py` |
| 14.21 | Test: detect-minor-post-fact (A9) → minor_lock=true + `memory.minor_detected_postfact` event + queue yellow/red for soft-delete with `deletion_reason='minor_protection'` + Ayla Pro queue ticket created | Reconciliation ticket | Same as 14.20 |
| 14.22 | Test: cross-tenant red SELECT raises `TenantScopeViolation` at app layer BEFORE DB query, INDEPENDENT of `STRICT_TENANT_REFUSE` flag (ADR-0011 §9.1) | #229 | `apps/identity/tests/test_tenant_scope.py::test_red_zone_carve_out` |
| 14.23 | Test: writer fail-closed path writes one RedZoneAccessLog row with `access_type='write_rejected_dob_lookup'` | #229 | `apps/identity/tests/test_memory_writer.py::test_dob_lookup_failed_audit` |
| 14.24 | Test: backup-restore — entries with `delete_requested_at NOT NULL` in live log stay unreadable after restore (per ADR-0011 §11.1) | #230 + restore runbook (§13.11) | `apps/identity/tests/test_backup_restore.py` |
| 14.27 (AS1) | Test: `RedZoneReader.read()` with non-existent `entry_id` raises `DoesNotExist` AND **no `RedZoneAccessLog` row is committed** (atomic rollback). Verifies no-orphan-log invariant. | #229 | `apps/identity/tests/test_red_zone_reader.py::test_no_orphan_log_on_select_fail` |
| 14.28 (AS1) | Test: `RedZoneReader.read()` signature rejects calls missing `user_id` argument (TypeError at call site). | #229 | Same |
| 14.29 (AS1) | Test: `RedZoneReader.read()` with `entry.user_id ≠ caller-supplied user_id` raises `DoesNotExist` (ownership-check at query time). | #229 | Same |
| 14.30 (AS2) | Test: `RedZoneAccessLog.accessor_principal` is non-null + matches the expected concrete identity per `accessor_role` (Celery hostname / cron name / staff UUID / service-account name). | #230 | `apps/identity/tests/test_audit_log.py::test_accessor_principal_concrete` |
| 14.31 (AS2) | Test: query «show me every access by staff X in date range» runs against `idx_red_zone_log_principal_ts` index (EXPLAIN ANALYZE shows index scan). | #230 | Same |
| 14.25 | Test: `db-sensitive` label CI gate — PR without label cannot apply migrations to `memory_entry` (round-3 S6 fix) | #229 | `.github/workflows/migrations.yml` + lint test |
| 14.26 | Test: minor `memory.minor_detected_postfact` event has hashed user_id + tenant_id=null + only internal subscribers (§15) (round-3 S14 fix) | Reconciliation ticket | `apps/identity/tests/test_minor_event_schema.py` |

## 14. Export semantics (S2 fix)

§8 (152-ФЗ portability) export MUST follow these rules to avoid leaking internal taxonomy to the data subject:

| Field | Exposed to data subject in export? | Rationale |
|---|---|---|
| `id`, `created_at`, `kind`, `summary` (rendered) | YES | Standard subject-access data. |
| `sensitivity_zone` | YES (as user-facing label, e.g. «личные» / «чувствительные») | The user already sees zone labels in the Bonuses → Memory UI; consistent here. |
| `source` | YES | The user has the right to know about automated processing (ADR-0011 §8 right-to-know-about-automated-processing). |
| `content` (decrypted) | YES | The fact itself — primary export payload. |
| `consent_at`, `last_inferred_at`, `delete_requested_at` | YES (as timestamps) | Audit-relevant; subject can verify their own action timeline. |
| **`deletion_reason`** | **NO — never in export** | The taxonomy is internal. Exporting «`minor_protection`» informs the data subject «we classified you as a minor and purged your data» — a privacy-by-disclosure violation. Soft-deleted rows are EXCLUDED from §8 export entirely, so `deletion_reason` never reaches the export pipeline. |
| `source_tenant_id` | YES (as tenant display-name) | The user has the right to know which provider relationship the fact originated from. |
| `forget_all_requested_at` (UPC level) | YES | Same audit transparency principle. |
| `minor_lock` (UPC level) | **NO — never in export** | Internal flag for the reconciliation job; «we classified you as minor» disclosure equivalent. |

**Soft-deleted rows are excluded from §8 export.** Pre-deletion exports include the entry; once deleted, the entry is gone from the user's view. This matches the user-facing memory UI («Что Ayla знает обо мне» — soft-deleted entries don't appear).

## 15. `memory.minor_detected_postfact` event schema (S14 fix)

Per ADR-0011 §13.4 + round-3 A9. Emitted by the reconciliation job when it post-facts detects a minor.

| Field | Type | Description | Sensitivity |
|---|---|---|---|
| `event_id` | ULID | Standard event envelope per event-contract.md | — |
| `event_name` | `"memory.minor_detected_postfact"` | Literal | — |
| `event_version` | `1` | Integer per event-contract.md | — |
| `occurred_at` | ISO8601 | When detection happened | — |
| **`tenant_id`** | **`null`** | Always null — minor protection is user-scoped, not tenant-scoped | Round-3 S14 fix |
| **`user_id_hash`** | string (SHA-256 hex of `user_id || pepper`) | Hashed identifier, NOT plaintext user UUID | Round-3 S14 fix — prevents minor enumeration in any analytics consumer |
| `actor` | `"system"` | Reconciliation job | — |
| `data` | `{ "queued_entry_count": N, "first_detection": false / true }` | Summary stats, no PII | — |

**Subscriber allowlist (round-3 S14 enumeration):**

| Subscriber | Internal? | Why |
|---|---|---|
| `apps.audit.minor_detection_log_consumer` | Internal | Adds to permanent minor-detection audit log (7-year retention) |
| `apps.identity.memory_purge_consumer` | Internal | Queues the soft-delete sweep with `deletion_reason='minor_protection'` |
| `apps.adminconsole.minor_review_ticket_consumer` | Internal | Creates the Ayla Pro queue ticket for human review |

**External / third-party analytics consumers are EXPLICITLY FORBIDDEN to subscribe to this event.** The subscriber registry (in `apps.events.subscriber_registry`) gates this enum; only the three above are accepted. CI test asserts no other subscriber consumes `memory.minor_detected_postfact`.

## 16. References

- **ADR-0011** (`docs/adr/ADR-0011-user-personal-context-privacy.md`) — the «why» prose, cross-cutting decisions, legal framing, follow-up tickets.
- **ADR-0009** (`docs/adr/ADR-0009-ayla-split-domain-architecture.md`) §Memory model + §Hard rule #6.
- **ADR-0006** (`docs/adr/ADR-0006-field-level-encryption.md`) — `EncryptedJSONField` + Fernet key custody.
- **event-contract.md** (`docs/architecture/event-contract.md`) — `memory.minor_detected_postfact` envelope format.
- **jwt-contract.md** (`docs/architecture/jwt-contract.md`) — verifier middleware that gates red-zone API access.

---

## Last verified

2026-05-22 — round-2 amendments addressing 2 visible AS blockers from refactor's first adversarial pass (memory `feedback_h3_waiver_pattern` N=15). AS1 — `RedZoneReader.read()` signature gains `user_id` argument (was literal ellipsis); atomic re-ordered to audit-first-then-SELECT with explicit no-orphan-log invariant + named trade-off in §10. AS2 — `RedZoneAccessLog` gains `accessor_principal` text column (concrete identity per `accessor_role`); 3 new supporting indexes; verification checklist gains 5 new tests (14.27–14.31). Pattern observation: tabular refactor (this doc) caught both findings as **table-completeness gaps** (placeholder ellipsis + missing column) — NOT prose-density gaps like the ADR rounds. Codified in N=15 rule: «tabular refactor halves count + changes character».

2026-05-22 — v1 extracted from ADR-0011 round-3 amendments per tech lead's «refactor not round-4» recommendation (memory `feedback_h3_waiver_pattern` N=6 + pattern observation that prose density was wrong tool). Addresses round-3 blockers S1 (deletion_reason backfill `unknown_legacy`), S2 (portability export excludes soft-deleted + never exposes `deletion_reason`), S3 (`SET LOCAL` + `try/finally` GUC reset + pgbouncer caveat), S4 (lint inverted to allowlist), S6 (`ayla_migrator` + `db-sensitive` label CI gate), S7 (`pg_dump` role table + accepted-residual note), S8 (no FK CASCADE — §3 RedZoneAccessLog FK with `ON DELETE NO ACTION`; ADR-0011 §11.1 «cascade» wording removed in same refactor PR), S11 (verification checklist gains owner ticket column + test location), S14 (`memory.minor_detected_postfact` event uses hashed user_id + tenant_id=null + explicit internal subscriber allowlist).
