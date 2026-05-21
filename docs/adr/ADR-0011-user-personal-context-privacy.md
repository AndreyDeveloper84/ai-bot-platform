# ADR-0011: UserPersonalContext Privacy & Retention Policy (152-ФЗ engineering boundaries)

**Status:** Proposed — 2026-05-21 (round-3 amendments per PR #495 adversarial N=4 — addresses 4 new attack surfaces introduced by round-2 abstractions + 6 nice-to-haves)
**Companion ADRs:** [ADR-0009](./ADR-0009-ayla-split-domain-architecture.md) §Memory model · [ADR-0006](./ADR-0006-field-level-encryption.md)
**Companion policy:** [`docs/design/policies/ayla-memory-and-personalization.md`](../design/policies/ayla-memory-and-personalization.md) — foundation Doc #2 (the customer-facing UX framing)
**Blocks:** #228 (UserPersonalContext model) · #229 (MemoryEntry model) · #230 (RedZoneAccessLog model)

> **HARD GATE.** Sprint 1 Track A memory-model issues #228/#229/#230 MUST NOT merge until this ADR is Accepted and their schema includes the mandatory fields in §3. Adding privacy fields retroactively is expensive and risks silent gaps — the lawyer who arrives in Phase 1 will ask for them, and back-filling encrypted JSONB schemas in production is far harder than getting them right at first migration.

---

## 1. Context

### 1.1 Why this ADR exists

ai-bot-platform `apps/identity/UserPersonalContext` (UPC) holds the **core cross-channel memory** of every customer — what Ayla remembers about them across MAX, Mini App, future Telegram/WhatsApp, beyond any single salon visit. Some of this is innocuous (preferred district, vegetarian, evening time). Some is intimate (pregnancy status, chronic conditions, mental-health mentions).

Russian 152-ФЗ and analogous GDPR principles require concrete, auditable answers to:

- What categories of personal data do we store?
- For how long, per category?
- How is it encrypted at rest?
- Who can read it, when, and under what justification?
- Which subject rights (access, rectify, erase, portability) does the system implement?
- How is consent obtained and recorded for sensitive categories?

Engineering needs answers BEFORE coding the models, not after. If `MemoryEntry` lands without a `consent_at` field, then a yellow-zone fact stored in week 3 has no auditable consent timestamp — and the cost of back-filling that field on every production row (with the right value, since `null` would mean «no consent») is a multi-week incident. Same logic for `delete_requested_at`, `source`, and `last_inferred_at`. This ADR locks the contract.

### 1.2 Relationship to other docs

- **ADR-0009 §Memory model** locks the architectural boundary: core memory lives in ai-bot-platform, provider-specific history in Ayla djangoproject, **never cross-leak**. This ADR refines the policy *within* the core memory side.
- **`ayla-memory-and-personalization.md`** (foundation Doc #2) is the customer-facing UX framing of the 3-zone model — what zones mean to users, what the «Bonuses tab → memory» UI surfaces, what messages Ayla speaks when she uses or forgets a fact. This ADR is the **engineering pre-commitment** that doc rests on.
- **ADR-0006** locks the field-level encryption mechanism (`django-cryptography-django5` Fernet). This ADR cites ADR-0006 for the encryption-at-rest requirement on yellow + red zone payloads.

### 1.3 What this ADR is NOT

- **Not a legal audit.** Phase 1 prerequisite — external lawyer reviews this document and either signs off or directs amendments. Engineering boundaries here are conservative defaults designed to survive that review with minimal rework.
- **Not the customer-facing UX spec.** That's `ayla-memory-and-personalization.md`. This ADR is the wire/storage contract.
- **Not a key-management ADR.** Key custody + rotation procedure live in ADR-0006 + the secret-manager runbook.

---

## 2. Decision summary

1. **Five mandatory schema fields** documented in §3 — all present in the initial migration. Four on `MemoryEntry` (per-fact granularity), one on `UserPersonalContext` (user-level forget-all timestamp).
2. **Three sensitivity zones** (green/yellow/red) with explicit semantics (§4).
3. **Per-zone retention policy** (§5).
4. **Encryption at rest** for yellow + red payloads, per ADR-0006 (§6).
5. **Append-only access log** for red-zone reads and writes — `RedZoneAccessLog`, 7-year retention, no FK CASCADE (§7).
6. **152-ФЗ subject rights** mapped to specific endpoints (§8).
7. **Cross-tenant reuse boundary** rule from ADR-0009 — green reusable, yellow implicit-consent, red use-only-never-expose (§9).
8. **Minor protections** for users <18 — no yellow/red writes (§10).

---

## 3. Mandatory schema fields

The following five fields MUST appear in the initial migration for the memory models. #437 issue body used the term `sensitivity_level`; the canonical field name in this ADR + #229 + the policy doc is **`sensitivity_zone`** — they mean the same thing.

### 3.1 On `MemoryEntry` (4 fields — per-fact granularity)

| Field | Type | Description |
|---|---|---|
| `sensitivity_zone` | enum (`green` / `yellow` / `red`) | Per-fact zone. Already present in #229; this ADR locks the canonical name + values. Indexed. |
| `source` | enum (`explicit` / `inferred` / `signal`) | How the fact entered memory. `explicit` = user stated it directly. `inferred` = Ayla derived it from conversation/behaviour. `signal` = derived from booking/payment events. Indexed for filter queries. |
| `last_inferred_at` | timestamptz, nullable | When inference last updated this entry. **MUST be NULL when `source = 'explicit'`** and **MUST be NOT NULL when `source IN ('inferred', 'signal')`** (DB CHECK constraint enforced). Updated on every re-inference pass; the value answers «how stale is this guess?» |
| `delete_requested_at` | timestamptz, nullable | Set when the user requests deletion of this specific entry (DELETE `/api/v1/users/me/memory/{entry_id}` — #232). `soft_deleted_at` (already in #229) is set by the soft-delete job that processes the request; `delete_requested_at` records the user's intent moment for audit. |
| `deletion_reason` | enum (`user_delete` / `withdrawal` / `forget_all` / `ttl_purge` / `minor_protection`), nullable | **Set in the same UPDATE that sets `delete_requested_at`.** Distinguishes WHY a row is being deleted — critical for regulator audit «show me Q3 consent withdrawals» (filter `deletion_reason='withdrawal'`) vs «show me Q3 deletions» (filter `deletion_reason='user_delete'`). NULL only on live (non-deleted) rows. (Round-3 Blocker A1.) |
| `consent_at` | timestamptz, nullable | Set when the user explicitly consented to storing this entry. **MUST be NOT NULL for `sensitivity_zone IN ('yellow', 'red')`** before the entry is read or used by Ayla (DB CHECK constraint enforced at write). Green-zone entries do NOT require explicit consent (the 152-ФЗ basis for green is implied by the service contract — see §11). |

### 3.2 On `UserPersonalContext` (1 field — user-level)

| Field | Type | Description |
|---|---|---|
| `forget_all_requested_at` | timestamptz, nullable | Set when the user invokes the «forget everything» flow (POST `/api/v1/users/me/memory/forget-all` — #233). The forget-all job is async; this field records the moment the user pressed the button. Separate from `soft_deleted_at` (which marks job completion). |

### 3.3 Why these field names + locations

- **`sensitivity_zone` not `sensitivity_level`** — «zone» is the term used throughout `ayla-memory-and-personalization.md`, in ADR-0009 §Memory model, and in #229. Issue body said «level»; treat it as a draft term. Field name = `sensitivity_zone`. Enum values lowercase to match Django convention.
- **`source` not `data_sources`** — singular per entry. The memory file `project_ayla_memory_hybrid_model` referenced `data_sources` plural; that was at UPC-level aggregation. Per-entry is singular.
- **`last_inferred_at` not `inferred_at`** — preserves the «last update» semantic so re-inference passes can update it without losing first-inference-time. If we ever need first-inference, store it in `created_at` (which is the entry's birth, already there).
- **`delete_requested_at` separate from `soft_deleted_at`** — two distinct events: user intent (instantaneous) vs job completion (async, possibly minutes later). Audit needs both.
- **`consent_at` per-entry, not per-zone-per-user** — a user might consent to one yellow fact but not another. Per-entry storage matches the customer-facing «I want Ayla to remember X but not Y» UX in the Bonuses tab.
- **`forget_all_requested_at` on UPC, not derived from per-entry** — efficiency: the forget-all dispatcher reads one UPC row to know «is there pending forget-all work?» rather than scanning all entries.

### 3.4 Database constraints

The CHECK constraints below MUST be DB-level, not application-level. Per Phase 0 freeze allow-list (ADR-0009 §Hard rule #3): infra migration changes are allowed.

```sql
-- Blocker #1 fix: tightened inferred/signal must carry timestamp
ALTER TABLE memory_entry
  ADD CONSTRAINT memory_entry_inferred_nullness CHECK (
    (source = 'explicit' AND last_inferred_at IS NULL)
    OR (source IN ('inferred', 'signal') AND last_inferred_at IS NOT NULL)
  );

-- Blocker #2 fix: soft_deleted_at exemption supports the withdrawal flow
-- (§11.x zone-promotion + §11.y withdrawal-via-soft-delete)
ALTER TABLE memory_entry
  ADD CONSTRAINT memory_entry_yellow_red_requires_consent CHECK (
    sensitivity_zone = 'green'
    OR (sensitivity_zone IN ('yellow', 'red')
        AND (consent_at IS NOT NULL OR soft_deleted_at IS NOT NULL))
  );
```

Both constraints fire at INSERT and UPDATE. App-level validation is a courtesy; the DB is the truth.

**Constraint 1 semantics:** `source='explicit'` MUST have `last_inferred_at IS NULL` (explicit facts are never inferred); `source IN ('inferred','signal')` MUST have a non-null timestamp (inferred facts always carry the timestamp of their inference). Re-inference passes update the value.

**Constraint 2 semantics:** yellow/red entries require either an active consent timestamp OR a soft-delete marker. The `soft_deleted_at` exemption is critical for the withdrawal flow (§11.y): a withdrawn entry transitions to soft-deleted in the same UPDATE that flags its `delete_requested_at` — the constraint stays satisfied because the row is in tombstone state.

**Withdrawal is NOT modeled as «set `consent_at = NULL` on a live row»** — that would violate Constraint 2. Withdrawal sets `delete_requested_at = now()` + `soft_deleted_at = now()` in one transaction; the entry becomes immediately unreadable per the app-layer read gate (§11.y). Physical purge happens async via the TTL sweep.

**Constraint 3 (round-3 Blocker A1) — `deletion_reason` nullness:**

```sql
-- A row is "deleted" (has any deletion timestamp) IFF deletion_reason is set
ALTER TABLE memory_entry
  ADD CONSTRAINT memory_entry_deletion_reason_nullness CHECK (
    (delete_requested_at IS NULL AND soft_deleted_at IS NULL AND deletion_reason IS NULL)
    OR (deletion_reason IS NOT NULL
        AND (delete_requested_at IS NOT NULL OR soft_deleted_at IS NOT NULL))
  );
```

Live (non-deleted) rows MUST have `deletion_reason IS NULL`. Any row in soft-delete state MUST have a non-null `deletion_reason`. Prevents the «orphaned soft-delete with no reason» case where regulator audit asks «why was this user's entry deleted?» and the DB shrugs.

**Migration discipline.** The CHECK constraints are added with the `NOT VALID` + `VALIDATE CONSTRAINT` pattern (Blocker #10) — see §14.

---

## 4. Sensitivity zones

### 4.1 Green — innocuous, default-on

Facts whose disclosure causes no realistic harm even if leaked:

- Locations (workplace district, home district, gym address)
- Schedule preferences (preferred time slots, usually-busy-until, free-after)
- Service preferences (min rating preference, vegetarian, prefers flexible cancellation)
- Life events (wedding, move) — when stated explicitly, NOT inferred
- Aggregate inferences (favourite masters by booking count)

**Consent basis under 152-ФЗ:** service-contract basis. Storing «user prefers evening time» is necessary for Ayla to fulfill the booking-assistance contract the user accepted at registration. `consent_at` is therefore NULL for green entries.

**Used:** freely, across providers (per §9).

### 4.2 Yellow — personal context, requires implicit consent

Facts that are personal but not sensitive in the 152-ФЗ §10 «special category» sense:

- Family context (has children, children's age range, partner sensitivities) — when explicitly stated
- Finance signals (inferred price floor, declined-on-price-X behaviour)
- Skin sensitivities, diet (vegan/keto/specific allergies — when user-stated, NOT when inferred from medical context)
- Behavioural patterns inferred from booking history (always Thursday, always after 7pm)

**Consent basis:** implicit consent recorded by `consent_at` — set at the moment the user explicitly told Ayla the fact OR confirmed an inference Ayla surfaced. UX hook: when Ayla says «I'll remember that» in chat, that turn writes `consent_at = now()` on the new entry. Without `consent_at`, the entry CANNOT be written (DB CHECK §3.4).

**Used:** within the user's session and across providers per §9, but Ayla's voice rules prevent disclosing yellow facts to providers (e.g. Ayla does NOT tell salon X «this user has children» — that's UX-side filter, separately specified in voice modulator config).

### 4.3 Red — sensitive, use-only

Facts in 152-ФЗ §10 special categories OR analogous Ayla-internal high-sensitivity list:

- **Health:** pregnancy status, chronic conditions, mental-health flags, current medication relevance to procedures
- **Sexual orientation, religious / philosophical beliefs, racial / ethnic identity:** stored only if user-stated AND relevant to service contraindication
- **Biometric raw data:** NOT stored as memory entries. If captured at all (e.g. avatar photos for wellness avatar), it lives in the wellness module's red-zone storage with its own rules. Memory entries point by reference only.

**Explicitly NOT collected (no storage path exists):**

- **Political views** — not relevant to beauty/wellness service contract; no collection prompt anywhere.
- **Criminal record** — not relevant; no collection prompt; if a user volunteers this information unprompted in chat, Ayla MUST NOT extract it as a memory entry (extraction filter rule documented in voice modulator config).
- **Trade-union membership, philosophical convictions** beyond what the «religious / philosophical» bullet above covers.

The absence of a storage path is itself an audit-defence: a 152-ФЗ §10 category cannot be inappropriately retained if no code writes it.

**Consent basis:** explicit consent. `consent_at` set when the user goes through the explicit «yes, remember this for contraindication filtering» flow. Step-up auth required for reads (per `ayla-memory-and-personalization.md` §10).

**Used:** USE-ONLY pattern — Ayla checks red-zone facts to filter contraindicated services or warn the user. Ayla NEVER speaks the red fact back to the user unprompted in mixed-company channels (mini-app screens, MAX chat thread with someone watching), and NEVER passes red facts to providers in any form. The cross-tenant reuse rule (§9) is **strictest** for red: red is per-user, never exposed beyond the user.

---

## 5. Retention per zone

**ADR-0011 is the canonical retention spec.** The values below supersede earlier wording in #229's `ttl_days` defaults and in `ayla-memory-and-personalization.md`. Amendment tickets to align both surfaces are listed in §13.

| Zone | Default retention | Trigger for soft-delete | Physical purge |
|---|---|---|---|
| Green | No auto-TTL — persists until user deletes | DELETE entry endpoint OR forget-all OR user moves the fact to yellow/red | Soft-delete tombstone retained 30 days for undo, then hard-purged |
| Yellow | 365 days from last use OR withdrawal OR user delete | TTL sweep (`GREATEST(last_used_at, consent_at) < now() - INTERVAL '365 days'`) OR withdrawal flow OR delete request | Soft-delete tombstone retained 30 days, then hard-purged |
| Red | 90 days from last use OR withdrawal OR user delete | TTL sweep (`GREATEST(last_used_at, consent_at) < now() - INTERVAL '90 days'`) OR withdrawal flow OR delete request | Soft-delete tombstone retained 30 days (audit), then hard-purged; `RedZoneAccessLog` rows persist 7yr independently |

**TTL implementation contract.** The nightly TTL sweep for yellow + red MUST compute «last used» as `GREATEST(last_used_at, consent_at)` — not `last_used_at` alone. Reasoning: an explicit consent moment is itself a form of refresh; a fact a user actively re-confirmed today should not be purged tomorrow on a stale `last_used_at`. The sweep query SQL is locked here so #234 (red TTL sweep) and the yet-to-be-ticketed yellow TTL sweep (§13.2) implement identical semantics.

**Amendment notes (follow-ups, §13):**

- **#229 `ttl_days` defaults** must be updated to reflect ADR canonicals: green = `NULL` (no auto-TTL — column nullable), yellow = `365`, red = `90`. #229 currently specifies `green 1825, yellow 365, red 90` — green default needs amendment to `NULL`.
- **`ayla-memory-and-personalization.md`** uses «yellow indefinite» wording in earlier drafts — supersede with ADR-0011 §5 «365 days from last use».
- Both amendments ship in the PRs that close #229 / #228 / #230 (since this ADR is their hard-gate, fixing inline is cheaper than a separate doc PR).

---

## 6. Encryption at rest

Per [ADR-0006](./ADR-0006-field-level-encryption.md):

- `MemoryEntry.content` (JSONB payload) is stored via `EncryptedJSONField` from `django-cryptography-django5` for **all** zones, not just yellow/red. Green is encrypted too — the marginal cost (~microseconds per read/write) is negligible, and homogeneous encryption simplifies key-rotation tooling.
- Encryption key (`DJANGO_CRYPTOGRAPHY_KEY`) sourced from the deployment secret manager. Key rotation is supported via Fernet's multi-key bundle.
- **Plaintext snapshots are forbidden anywhere.** No log line, no admin dump, no replay fixture stores plaintext `MemoryEntry.content`. The audit table stores SHA-256 fingerprints when needed for dedup (per ADR-0006 pattern for token fingerprints).

Red-zone specifics:

- `MemoryEntry.content` for red entries is additionally **hash-pepper'd** before encryption — the hash pepper is a separate secret from the encryption key. Two-secret defence: even if the Fernet key leaks, red plaintext is not recoverable without also obtaining the pepper from a different secret store.
- **Pepper management is NOT yet specified in ADR-0006.** Follow-up (§13.5): amend ADR-0006 to add the `DJANGO_RED_ZONE_PEPPER` secret + its rotation procedure + the runbook entry. Until that amendment lands, the memory writer for red entries MUST **fail closed** (refuse the write) if `DJANGO_RED_ZONE_PEPPER` is not set in environment. Owner: tech lead (this ADR's owner) — fold into the same legal-audit prep window (§15).
- Schema MAY be revisited Phase 2+ when KMS becomes available (ADR-0006 §Alternatives — deferred).

---

## 7. Access logging — RedZoneAccessLog

Per #230, the audit table is **already specified** as append-only, 7-year retention, no FK CASCADE. This ADR adds the policy that drives those constraints:

- **Every read** of a red-zone `MemoryEntry.content` MUST write a `RedZoneAccessLog` row in the SAME DB transaction as the read. If the read happens outside a transaction or the log write fails, the read MUST abort.
- **Every write** to a red-zone entry (create/update/soft-delete/purge) MUST write a log row.
- **Accessor categories:** `ayla_llm` (LLM prompt construction reads), `system_job` (TTL sweep, forget-all), `ops_admin` (manual operator action — must include `request_id` referencing the operator's audit-trail ticket).
- **Yellow and green reads are NOT logged in `RedZoneAccessLog`** — only red. Audit volume would be prohibitive. Yellow access is captured at the cumulative `MemoryEntry.last_used_count` level, which is sufficient for the user's «show me when this was used» UX without per-read overhead.
- **No FK CASCADE** — when a `MemoryEntry` is soft-deleted or purged, its `RedZoneAccessLog` rows STAY. This preserves the audit history of pre-deletion reads, which the lawyer will want.

### 7.1 Audit-invariant enforcement (CRITICAL — Blocker #3 fix)

The «every red read writes a log row» rule (§7 bullet 1) is **NOT a docstring** — application code that reads red memory MUST be physically prevented from bypassing the log. Three layers:

1. **Mandatory accessor function.** All red-zone reads MUST go through `apps/identity/services/red_zone_reader.py::RedZoneReader.read(entry_id, accessor_role, request_id)`. The accessor opens a DB transaction, writes the `RedZoneAccessLog` row first, then SELECTs the entry, then COMMITs. If either step fails, the transaction rolls back — the read does not happen.

   Pseudo-code (round-3 Blocker A2: signature gains `purpose`, `try/finally` GUC reset is mandatory):

   ```python
   class RedZoneReader:
       @classmethod
       def read(cls, entry_id, accessor_role, request_id, purpose):
           # request_id MUST be a real audit reference: Celery task id, HTTP
           # request id, or ops ticket id. Validated at call site.
           # purpose is a short human-readable string ("contraindication_check",
           # "subject_access_request", "incident_debug", ...) — stored in
           # RedZoneAccessLog.purpose for after-the-fact auditability (round-3 A5).
           with transaction.atomic():
               # CRITICAL: set the session GUC inside the transaction, then
               # reset in finally. Django connection pooling reuses sessions
               # across requests — without the reset, the next request in the
               # same pooled connection bypasses the BEFORE SELECT trigger
               # silently. This is the «pool-leaked auth» bypass (A2).
               with connection.cursor() as cur:
                   cur.execute(
                       "SELECT set_config('ayla.red_zone_access_context', %s, true)",
                       [str(request_id)],  # MUST be a UUID — trigger regex-checks
                   )
               try:
                   RedZoneAccessLog.objects.create(
                       memory_entry_id=entry_id,
                       user_id=...,
                       accessor_role=accessor_role,
                       access_type='read',
                       request_id=request_id,
                       purpose=purpose,
                   )
                   entry = MemoryEntry.objects.select_for_update().get(
                       id=entry_id,
                       sensitivity_zone='red',
                       soft_deleted_at__isnull=True,
                       delete_requested_at__isnull=True,
                   )
                   return entry.content  # decryption happens here
               finally:
                   # Reset GUC even on exception — pool safety.
                   # (PostgreSQL's third arg to set_config = is_local; we use
                   #  is_local=true so the value clears at tx end anyway, but
                   #  the explicit reset is defence-in-depth against connection
                   #  reuse outside an explicit transaction context.)
                   with connection.cursor() as cur:
                       cur.execute("SELECT set_config('ayla.red_zone_access_context', '', false)")
   ```

2. **Import guard — AST-based (round-3 Blocker A2).** Regex-based lint (the round-2 spec) misses six known bypass patterns:

   - **Variable-resolved literals:** `filter(sensitivity_zone=zone_var)` where `zone_var = 'red'` is set elsewhere.
   - **`.all()` then Python-side filter:** `for entry in MemoryEntry.objects.all(): if entry.sensitivity_zone == 'red': ...`
   - **Raw SQL:** `MemoryEntry.objects.raw("SELECT * FROM memory_entry WHERE sensitivity_zone = 'red'")`.
   - **Bulk operations:** `MemoryEntry.objects.filter(...).update(...)` and `MemoryEntry.objects.bulk_update(...)` — these bypass `post_save` signals AND the `RedZoneReader` accessor. **FORBIDDEN on rows where `sensitivity_zone='red'`. Period.** If a code path needs to update many red rows (e.g. TTL sweep), it MUST iterate via `RedZoneReader` one-at-a-time, accepting the throughput cost.
   - **Django shell + `dumpdata` / `dbshell`:** developer console session reading prod red rows. Mitigated by §7.2 DB role separation (`ayla_app` cannot SELECT red without GUC) but the shell pathway needs explicit policy: developers MUST use the `break-glass` role and follow the 4-eyes protocol (§7.2 bullet 3).
   - **Signal handlers:** `@receiver(post_save, sender=MemoryEntry)` handlers receive a decrypted instance bypass the accessor. Mitigated by signal handlers that touch red rows being themselves audited — but the AST lint flags any `post_save`/`pre_save` handler on `MemoryEntry` that doesn't import from `red_zone_reader`.

   The lint rule is therefore **AST-based** (using `libcst` or `ast.NodeVisitor`), NOT regex. It lives in `tools/lint/red_zone_guard.py`, runs in pre-commit + CI, and explicitly traces variable assignments to detect literal `'red'` flowing into any `MemoryEntry.objects.*` call site outside the accessor module. `tests/test_red_zone_guard.py` exercises each of the 6 patterns above as a positive test (the lint MUST fail on each).

   The rule MUST land in the same PR that creates `RedZoneAccessLog` (#230).

3. **Future hardening (Phase 2+):** Postgres Row-Level Security (RLS) policy gating red rows behind a session-level role check. Deferred per §16.4 — the import guard + DB role separation (§7.2) is sufficient defence for MVP given the small team size and the §7.2 BEFORE SELECT trigger.

### 7.2 Production DB role separation (Blocker #4 fix)

The accessor + import guard protect application-layer reads. They do NOT protect a DBA running `psql -c "SELECT content FROM memory_entry WHERE sensitivity_zone='red'"` directly against the prod database. Without DB-role separation, §11's «every red access logged» claim is theatrical.

**Production database has two routine roles + one break-glass role:**

- **`ayla_app`** — application service role. Read+write on all tables EXCEPT no direct read on `memory_entry.content` column when `sensitivity_zone='red'` (column-level GRANT). App reads red via `RedZoneReader` → row-level read via privileged service role behind it. A `BEFORE SELECT` trigger on `memory_entry` raises an exception on a red-zone SELECT unless the session has `ayla.red_zone_access_context` set to a non-empty value matching a UUID regex (round-3 A8 — guards against empty-string GUC and against non-UUID garbage):

  ```sql
  -- Trigger body (round-3 A8 — empty-string-safe, UUID-validated)
  IF coalesce(current_setting('ayla.red_zone_access_context', true), '') !~
     '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  THEN
    RAISE EXCEPTION 'Red-zone access requires UUID context (got %)',
      coalesce(current_setting('ayla.red_zone_access_context', true), '<unset>')
      USING ERRCODE = 'check_violation';
  END IF;
  ```

  This makes direct `psql` SELECT of red rows by the app role **also** fail without a logged UUID-valid context.
- **`ayla_ops`** — read-only ops role for routine debugging / dashboards. SELECT on `memory_entry` BUT through a view (`memory_entry_safe`) that omits red rows entirely (`WHERE sensitivity_zone != 'red'`). Ops sees green/yellow only. A bare `SELECT * FROM memory_entry WHERE sensitivity_zone='red'` under this role returns zero rows.

**Break-glass procedure for ops needing red-zone read** (incident response, legal subpoena, etc):

- **Two-person SSH session (4-eyes)** into prod box.
- `sudo -u ayla_app psql ...` with full transcript logged to `/var/log/red_zone_breakglass/`.
- Mandatory written justification + ticket reference before session.
- The session also writes to `RedZoneAccessLog` with `accessor_role='ops_admin'` AND to the OS-level SSH session audit.
- §11 legal-basis claim (152-ФЗ Chapter 3) holds because audit trail exists at OS layer when DB layer is bypassed via app-role escalation.

**Migration role (round-3 Blocker A3 — `ayla_migrator`).** Django migrations need DDL privileges. `ayla_app` deliberately LACKS DDL — a compromised app must not be able to `DROP TRIGGER memory_entry_red_select_guard ON memory_entry` and proceed unaudited. So a separate role exists for migrations only:

- **`ayla_migrator`** — DDL only (CREATE / ALTER / DROP table, column, constraint, trigger, view, role). NO DML on `memory_entry.content`. Used exclusively to run `python manage.py migrate` in deploy pipelines. The CI/CD agent assumes this role for the migration step only; application boot reverts to `ayla_app`.
- **`ayla_backup`** — physical backup role for `pg_basebackup`. Has REPLICATION privilege. **Accepted residual risk per §11.1:** physical backups bypass logical roles by design — they snapshot raw page contents including encrypted `memory_entry.content`. The encryption-key + red-zone pepper layered defence (§6) is the mitigation; the role itself cannot be more restricted without breaking DR capability.

**Provisioning step in #230 acceptance:** the migration creates `ayla_app`, `ayla_ops`, `ayla_migrator`, `ayla_backup` + the `memory_entry_safe` view + the `BEFORE SELECT` trigger. The break-glass role + the 4-eyes provisioning live in the secret-manager runbook (not in code). Without these, the audit claim is unenforceable.

---

## 8. 152-ФЗ subject rights mapping

> **Specific article-number citations below are engineering best-effort, NOT a legal opinion.** Phase 1 lawyer review (§15) will confirm or amend each citation. The rights themselves (access, rectify, erase, portability, objection, automated-processing notice, consent revocation) are well-established in 152-ФЗ Chapter 3 «Rights of the data subject»; only the exact article anchoring per row is subject to legal sign-off.

| Right | 152-ФЗ basis (best-effort) | Endpoint | Notes |
|---|---|---|---|
| Right to know what's stored | Chapter 3 — access right | `GET /api/v1/users/me/memory` — #231 | Returns paginated entries with zone, kind, source, summary text. Red zone hidden by default; revealed only after step-up auth. |
| Right to rectify | Chapter 3 — rectification right | `PATCH /api/v1/users/me/memory/{entry_id}` — follow-up (§13.1) | Not yet ticketed in Sprint 1 scope. **Follow-up ticket §13.1.** |
| Right to erase (per-entry) | Chapter 3 — erasure right | `DELETE /api/v1/users/me/memory/{entry_id}` — #232 | Sets `delete_requested_at` + `soft_deleted_at` instantly in the same transaction. App-layer read gate (§11.3) makes the entry immediately unreadable; physical purge async within tombstone retention (see §11.1 erasure scope table). |
| Right to erase (all) | Chapter 3 — erasure right (mass) | `POST /api/v1/users/me/memory/forget-all` — #233 | Sets UPC `forget_all_requested_at`; async sweep soft-deletes all entries + summary. Tombstone retention 30 days for audit, then hard-purge. |
| Right to portability | Chapter 3 — copy/export right | Data-export job — #269 (PRE-DEPLOY lock, closed) | Closed pre-deploy lock decided JSON + PDF formats; data export job to ship Sprint 2. |
| Right to object to processing | Chapter 3 + §9 consent withdrawal | UI toggle in Bonuses → Memory section («Не запоминай меня») | Sets `forget_all_requested_at` and disables future writes. **Follow-up ticket §13.3.** |
| Right to know about automated processing | 152-ФЗ provisions on automated processing | Memory transparency UI surfaces `source` per entry («explicit» vs «inferred» vs «signal») — #236 | Customer sees provenance per fact. |
| Right to consent revocation | §9 (consent + withdrawal) | Per-entry withdrawal in Bonuses UI; mass withdrawal = forget-all | Withdrawal immediately gates reads (app layer, §11.3) and triggers async delete. |

**Note on article citations:** an earlier draft of this section asserted specific articles (§14, §15, §16) per right. On review, that mapping is not as clean as the draft implied — 152-ФЗ Chapter 3 contains the rights bundle, and §10 covers special-category data basis. Legal review (§15) will produce the exact article anchors per right; the engineering contract (what endpoint, what data, what timing) is what this ADR locks, not the legal text.

**Gaps tracked:** PATCH endpoint (§13.1) + processing-objection toggle (§13.3). Follow-up ticket numbers populated at issue close (see §13).

---

## 9. Cross-tenant reuse boundary (per ADR-0009)

The boundary established in ADR-0009 §Memory model is restated here as engineering rule:

- **Green** facts: reusable across providers without consent friction.
- **Yellow** facts: reusable across providers within the user's session, BUT the voice modulator (`apps/llm/persona/`) MUST filter them out of any string addressed to a tenant-staff identity. Yellow leaks to providers = a contract violation.
- **Red** facts: never traverse a tenant boundary. They drive contraindication filters at Ayla's side only; the tenant sees the *result* (booking allowed / booking blocked with generic reason) but never the underlying fact.

**Provider-specific facts** (visit at salon X, complaint about master Z, payment to tenant W) live in Ayla djangoproject's per-tenant scope (Layer 2 per the memory hybrid model) and **never leak across tenants regardless of zone**. Those aren't UPC memory entries; this ADR doesn't change their handling. Ayla djangoproject's own privacy ADR (when written) covers Layer 2.

### 9.1 Enforcement timeline (interim — STRICT_TENANT_REFUSE soak)

§9 cross-tenant boundary is declared as architectural invariant. **ACTUAL enforcement live from 2026-05-28** per `STRICT_TENANT_REFUSE=True` flip (see memory `project_strict_tenant_refuse_soak`).

**Pre-flip (2026-05-21 to 2026-05-28):** tenant-scope guard is in **log-only mode**. Cross-tenant red-zone read attempts emit `worker.tenant_required_missing` audit event but do **NOT** raise. Code review + audit log analysis are the only enforcement during soak. Tech lead reviews the audit log daily during soak; any unexpected log entry triggers immediate code review.

**Post-flip:** `tenant_required_missing` → DLQ (via #499 PEL reaper) → no read performed. Architectural invariant holds in production.

Sprint 1 Track A #228-#230 models can land before flip; they inherit log-only enforcement until 2026-05-28, then hard enforcement after. If the soak surfaces blockers that delay the flip past 2026-05-28, this ADR's §9 enforcement claim for **green and yellow zones** is degraded to «log-only + code review» until the flip lands. Tech lead updates §9.1 with the new timeline if it slips.

**Red-zone carve-out (round-3 Blocker A4 — IMPORTANT):**

> The «log-only during soak» concession applies to **green and yellow zones only**. Red-zone cross-tenant reads hard-fail (raise `TenantScopeViolation`) **from the moment #230's migration ships**, independent of the `STRICT_TENANT_REFUSE` flag. A cross-tenant leak during the soak window is a 152-ФЗ §10 special-category violation that no amount of «log-only + code review» can retroactively undo — and red-zone facts are infrequent enough that the «production observability soak» rationale for the soft mode doesn't apply.

Concretely: `apps/identity/services/red_zone_reader.py::RedZoneReader.read()` MUST validate the caller's `tenant_id` claim against the target entry's `source_tenant_id` (or `personal_context.user_id` ↔ `Request.user` linkage) **before** the DB SELECT. Mismatch raises immediately. The §7.2 `BEFORE SELECT` trigger is the second-layer defence; the app-layer check is the first.

This carve-out ships in `#230`'s scope per §13.9.

---

## 10. Minor protections (users <18)

Per [Q-AYL13](../design/policies/ayla-identity-and-brand.md) and [Q-AML8](../design/policies/ayla-memory-and-personalization.md) (both resolved 2026-05-20):

- Customers under 18 may use Ayla for booking, but the memory writer guard MUST:
  - Block `sensitivity_zone IN ('yellow', 'red')` writes outright.
  - Allow `green` + `source='explicit'` only — no behavioural inference, no signal-derived entries.
  - Block all red-zone reads (returns empty; if Ayla's logic asks for a red fact, it gets «no data» and degrades gracefully).
- Minor age determination: from Ayla djangoproject's user profile (DOB). If DOB unknown, default to «adult» — the right answer is to surface the unknown-age problem in Ayla Pro queue, not silently apply minor protections to all unknowns (which would degrade the adult-default UX).
- These protections are encoded in the memory writer (`apps/identity/services/memory_writer.py` — to be created in #229's scope).

**Enforcement mechanism (application-side, NOT a DB CHECK):**

Per ADR-0009 §Hard rules #2 and the repo-role table, canonical User identity + DOB live in **Ayla djangoproject**, not bot-platform. A DB CHECK constraint on `memory_entry` cannot reference a `users.dob` column that isn't in the same DB schema. (An earlier draft proposed such a constraint as «aspirational» — it has been removed because aspirational SQL in an Accepted ADR invites a future engineer to attempt a migration against a non-existent table.)

The actual enforcement is a 3-layer control:

1. **Memory writer (`apps/identity/services/memory_writer.py`)** — every write call MUST first fetch the user's age status via the Ayla REST DOB endpoint (NO local cache; live call per write). If status = «minor», reject yellow/red writes and non-`explicit` source writes.
2. **Daily reconciliation job (§13.4)** — sweeps UPCs whose age status changed since last sweep; primarily clears the minor flag on freshly-18 users. NOT the primary control — the writer is. The reconciliation job is hygiene, not gating. **Ticket MUST be filed BEFORE #229 merges**, not as a post-merge follow-up.
3. **Read gate** — minor-mode users get the same read filter as the §11.y withdrawal gate (no red, no yellow), enforced in `apps/identity/services/memory_reader.py`.

**Why no DB CHECK:** see ADR-0009 boundary. If user identity ever moves in-repo (Phase 2+ consideration), revisit and add a CHECK or trigger then. For now, application-side + reconciliation is the audit-defensible answer.

### 10.1 Minor age determination

From Ayla djangoproject's user profile (DOB). If DOB is **known** + < 18 → minor mode. If DOB is **known** + ≥ 18 → adult mode. If DOB is **unknown** (NULL) → default to adult mode — the right answer is to surface the unknown-age problem in Ayla Pro queue, not silently apply minor protections to all unknowns (which would degrade the adult-default UX). Surfacing strategy is product UX, separate spec.

### 10.2 Fail-mode when Ayla REST DOB lookup unavailable (Blocker #7 fix)

Memory writer MUST check `users.date_of_birth` via Ayla REST before writing yellow/red entries (§10 minor protection). When Ayla REST is unavailable (timeout, 5xx, network partition):

**Fail-closed for yellow/red:**

- Reject write with `MinorProtectionLookupFailed` exception.
- Caller retries with exponential backoff.
- Cap retries at 3; on final failure, write is dropped + audit event `memory.write_rejected_dob_lookup_failed` emitted to `apps/observability` AND a `RedZoneAccessLog` row written with `access_type='write_rejected_dob_lookup'` (round-3 Blocker A6 — the table designed for red-zone events should also log red-zone write rejections, not only successful operations).
- Customer-facing impact: AI's next turn may not have updated context. **Acceptable degradation vs 152-ФЗ §10 violation.**

**Fail-open for green-explicit only:**

- User-typed input that's clearly green (e.g. user types «я веган» → `diet=vegetarian`) bypasses DOB check because:
  - User self-identified the input,
  - Green sensitivity has no minor-protection requirement.

**Memory writer pseudo-code:**

```python
def write_entry(user_id, content, source, sensitivity_zone):
    if sensitivity_zone in ('yellow', 'red'):
        try:
            dob = ayla_rest.get_user_dob(user_id, timeout=3)
        except (Timeout, ConnectionError, ServerError):
            raise MinorProtectionLookupFailed(...)
        if is_minor(dob):
            raise MinorProtectionDenied(...)
    # green-explicit OR adult-verified yellow/red: proceed
    MemoryEntry.objects.create(...)
```

Fail-OPEN (allowing all writes when DOB unknown due to REST outage) is a **152-ФЗ §10 violation risk** if the user turns out to be a minor — even a brief outage could write red-zone data for a child. Fail-CLOSED is the audit-defensible default.

The writer logs each fail-closed event to `apps/observability` for SLO tracking (Ayla REST availability is now a hard dependency for memory writes). If REST outages exceed 1% of writes over 24h, on-call paged.

**Reconciliation job (§13.4) ticket MUST be created before #229 lands**, not as a post-merge follow-up. The writer is the primary control; the reconciliation job is the hygiene. Both need to exist before Sprint 1 Track A memory models ship.

---

## 11. Consent semantics + legal basis cheat sheet

| Zone | Legal basis for storage (best-effort, see §8 note) | How `consent_at` is set | Withdrawal effect |
|---|---|---|---|
| Green | Performance of contract / legitimate interest under service agreement | NULL (no per-entry consent record needed) | Withdrawal = entry delete; basis preserved via service contract |
| Yellow | Specific consent | Timestamp when user explicitly stated or confirmed inference in chat | Withdrawal flow per §11.3 — set `delete_requested_at` + `soft_deleted_at`, read gate immediately blocks. Physical purge within 24h. `consent_at` NOT cleared on live row. |
| Red | Explicit consent for special-category data (152-ФЗ §10) | Timestamp when user passed the explicit «remember for contraindication» dialog | Withdrawal flow per §11.3 + additional `RedZoneAccessLog` write with `access_type='withdrawal'`. Same atomicity. |

### 11.1 Erasure scope — limitations explicit (Blocker #6 fix)

Right-to-be-forgotten (152-ФЗ Chapter 3 erasure right) — engineering scope:

| Scope | Erasure mechanism | Effective when |
|---|---|---|
| Live rows in `memory_entry` | DELETE on row + cascade to RedZoneAccessLog forward-refs | Immediate (TTL sweep or explicit user action) |
| Application caches | TTL expiry or explicit cache invalidation | Within 1 cache-TTL window (≤ 1h) |
| Write-ahead log (WAL) | WAL retention rotation | ≤ 24h after erasure |
| Nightly `pg_basebackup` | Backup retention rotation | ≤ 30 days after erasure |
| DR replica (read-only) | Streaming replication catches DELETE | Within 1 replication lag window (≤ 30s) |
| Offsite cold backup (quarterly) | Backup rotation | ≤ 90 days after erasure |

The 30+90 day window between erasure-from-live and erasure-from-backup is a 152-ФЗ Chapter 3 **limitation, NOT a violation**: user's data is no longer accessible to any application path within 1 cache-TTL. Backup retention is a regulatory requirement (financial audit + DR readiness) that overrides immediate erasure per 152-ФЗ legitimate-interest carve-outs.

**Crypto-shred** (per-user encryption keys with key destruction on erasure) deferred to Phase 2. Current implementation = single org-wide Fernet key → backups remain decryptable for backup retention window.

**Lawyer-friendly summary:** erasure is «no longer used», not «physically unrecoverable». This is the industry standard 152-ФЗ interpretation.

**Backup-restore safety net (round-3 Blocker A7).** On a DR restore from any backup older than the most recent erasure, the app-layer read gate (§11.3) is applied **unconditionally**: any row whose `delete_requested_at` or `soft_deleted_at` is set (in either the restored data OR in the live audit table) remains unreadable. This means a backup restore in month 2 cannot resurrect data the user erased in month 1, even though the encrypted bytes are present on disk. The `RedZoneAccessLog` table (with 7-year retention) is the source of truth for which rows are deleted across all snapshots; the read gate consults the log on a startup reconciliation pass after any restore.

### 11.2 Zone promotion (green → yellow or green → red) — Blocker #8 fix

If a fact's sensitivity is upgraded (e.g. AI later infers yellow-level context from a green-zone fact), promotion MUST be triggered by NEW explicit user consent dialog. **Auto-promotion is FORBIDDEN.**

**Forbidden path (forgery risk):**

- AI infers user's allergy from food log → wants to promote to red.
- Writer-level code sets `sensitivity_zone='red'`, `consent_at=now()` without user interaction → CHECK #2 passes but `consent_at` is **FORGED** (user never consented at that timestamp).
- 152-ФЗ §10 violation — falsified consent record.

**Required path:**

- Writer detects promotion need → emits `memory.promotion_consent_required` event → frontend shows consent modal → user explicit «yes I consent» or «no, keep this fact at lower sensitivity» → writer updates row with user-confirmed `consent_at = consent_dialog_completed_at`.

**Code guard:** memory writer raises `ZonePromotionRequiresConsent` exception if asked to promote without a user-confirmed consent timestamp. Lint rule banning direct UPDATE that increases `sensitivity_zone` without going through a `PromotionRequest` model.

Downgrade (yellow → green) does NOT require new consent — it's strictly less sensitive than the original.

The DB CHECK §3.4 catches the «yellow/red without `consent_at`» case but cannot distinguish freshly-gathered consent from a forged one. Code-level + audit log («consent_token» recorded per promotion) is the layer that catches forgery.

### 11.3 Withdrawal flow (re-stated for clarity)

Withdrawal does **NOT** clear `consent_at` on a live row — that would violate Constraint #2 (§3.4). Withdrawal sets `delete_requested_at = now()` + `soft_deleted_at = now()` in one transaction. The app-layer read gate (`apps/identity/services/memory_reader.py`) filters `soft_deleted_at__isnull=True AND delete_requested_at__isnull=True`, so withdrawn entries are immediately invisible at the read path even before physical purge.

**Red withdrawal additionally writes** to `RedZoneAccessLog` with `access_type='withdrawal'` in the same transaction. (§13.7 amends the enum to include `'withdrawal'` — ships inline with #230's PR.)

**The withdrawal UPDATE sets `deletion_reason = 'withdrawal'`** (round-3 A1) — this distinguishes withdrawals from outright deletions in subsequent audit queries («show me Q3 consent withdrawals» = `WHERE deletion_reason='withdrawal' AND delete_requested_at >= ...`). Forget-all sets `deletion_reason = 'forget_all'`; per-entry delete sets `'user_delete'`; TTL purge sets `'ttl_purge'`; minor-protection purge sets `'minor_protection'`.

---

## 12. Consequences

### Easier

- The lawyer who arrives in Phase 1 has a single document to react to. Their review either accepts these defaults or produces an ADR-0011-r2 amendment; the engineering invariant (these fields exist, encryption is on, audit is append-only) survives.
- Subject-rights endpoints (#231/#232/#233) have a clear contract for what to do per zone.
- `MemoryEntry` schema has all the columns needed for the «memory transparency» UI (#236) without retrofitting.

### Acceptable

- The DB CHECK constraints add ~1µs to inserts on `MemoryEntry`. Negligible.
- Per-fact `consent_at` storage adds 8 bytes per row × ~50 entries per active user × current customer count = trivial storage.
- Voice modulator must enforce yellow-zone non-disclosure to provider strings. This is a real implementation cost in `apps/llm/persona/` (#280 territory) — covered by that ticket's scope; flagged here so the implementer doesn't miss it.
- Encrypting green-zone payloads (not strictly required) costs ~CPU microseconds per access. Cheaper than the operational risk of a key-rotation tool that has to distinguish green from yellow.

### Harder

- Adding a sixth zone later (if a category emerges that doesn't fit green/yellow/red) is a schema migration. We accept this — fewer zones to design around makes the policy graspable.
- Withdrawing consent on a red entry triggers immediate read-block + scheduled deletion, which may surprise users who expected «soft remove, restore later». UX should not offer «restore deleted memory» — that would re-create the consent question and the audit trail would be a mess. UX spec'd in `ayla-memory-and-personalization.md` matches this.
- Minor-protection enforcement crosses the bot-platform / Ayla djangoproject boundary (DOB lives in Ayla). 3-layer control (writer live-fetch + reconciliation job + read gate) is more code than a single DB CHECK would be, but doesn't pretend a constraint applies that physically cannot. See §10.
- Encrypted green payloads (§6) cannot be queried with PostgreSQL GIN/JSONB indexes against `MemoryEntry.content`. Filter queries on memory content (e.g. «entries containing district X») are full-scan over an encrypted column. Acceptable because memory entries are paginated reads scoped by `user_id` and `personal_context_id` (both indexed unencrypted), and the volume per user is small (low hundreds). If a future feature needs content-index queries (search across all of user's memory), we either add a sidecar unencrypted summary column (with PII guards) OR accept the scan cost. Trade-off recorded; revisit Phase 2+.
- DB role separation (§7.2) + break-glass procedure (§7.2 last bullet) add ops overhead — operators must know the 4-eyes protocol + secret-manager grant flow before they can debug red-zone incidents. Worth it for the audit-defensibility; the runbook documents the steps so first-incident learning curve is bounded.

---

## 13. Follow-up tickets to create

Most of these do not block this ADR's acceptance. **§13.4 is an exception — its ticket MUST be filed BEFORE #229 merges (Blocker #7 mandate).** Other ticket numbers populated at issue close.

- **§13.1 — PATCH endpoint for memory entry rectification** (152-ФЗ right to rectify). Needed Sprint 2. Owner: bot-platform backend.
- **§13.2 — Yellow-zone TTL sweep job.** Parallels existing red-zone TTL sweep (#234). MUST use the `GREATEST(last_used_at, consent_at)` semantic from §5 (NOT `last_used_at` alone). Default 365-day window.
- **§13.3 — Processing-objection toggle** in Bonuses → Memory UI. Sets `forget_all_requested_at` + disables future writes. Distinct from forget-all (which erases past) — this stops future without erasing.
- **§13.4 — Minor-age reconciliation job (BLOCKING #229).** Hits Ayla REST for DOB on daily cadence. Two roles:
  1. **Primary:** clears the minor flag on freshly-18 users (their birthday rolled over since last sweep).
  2. **Detect-minor-post-fact (round-3 A9):** when DOB populated later reveals an existing user as a minor (e.g. a user who registered without DOB and only filled it in months later, or whose DOB was corrected), the reconciliation job MUST:
     - Set UPC `minor_lock = true`.
     - Emit `memory.minor_detected_postfact` event with `user_id` and the timestamp of detection.
     - Queue **all yellow and red `MemoryEntry` rows for that user** for soft-delete with `deletion_reason='minor_protection'`. Async sweep purges them.
     - Create an Ayla Pro queue ticket for human review — the founder / privacy steward verifies the detection and confirms no further action needed (or escalates to lawyer if the time-window of incorrect storage is significant).

  Hygiene job; NOT the primary write-time control (the writer is — see §10.2). **MUST be filed and accepted before #229 lands.**
- **§13.5 — ADR-0006 amendment for `DJANGO_RED_ZONE_PEPPER`** secret. Specifies the secret + rotation procedure + secret-manager runbook entry. Until this lands, the memory writer fails closed on red writes if the secret is unset (§6).
- **§13.6 — Voice modulator yellow-zone non-disclosure test.** Regression test ensuring yellow facts are filtered out of provider-addressed strings (covers `apps/llm/persona/` #280 acceptance).
- **§13.7 — `RedZoneAccessLog.access_type` enum amendment** to include `'withdrawal'` value (see §11.3). Ships inline with #230's PR.
- **§13.8 — `RedZoneReader` accessor + import guard** (§7.1). Production code outside `apps/identity/services/red_zone_reader.py` may NOT query red rows directly. CI lint rule + grep test. Ships inline with #229/#230's PR.
- **§13.9 — DB role separation migration** (§7.2). Creates `ayla_app` (with `BEFORE SELECT` trigger on red rows checking session context), `ayla_ops` (with `memory_entry_safe` view filtering red), break-glass procedure docs. Ships inline with #230's migration.
- **§13.10 — Backup retention rotation policy docs** (§11.1). Confirms WAL = 24h, basebackup = 30d, offsite cold = 90d rotation windows. Lives in `docs/runbooks/`. Owner: infra.
- **§13.11 — Backup-restore reconciliation runbook** (round-3 A7). Documents the startup reconciliation pass that applies the read gate to restored data via the `RedZoneAccessLog` table. Lives in `docs/runbooks/`. Owner: infra.
- **§13.12 — `RedZoneAccessLog.access_type` enum** also gains `'write_rejected_dob_lookup'` (round-3 A6) in addition to `'withdrawal'` from §13.7. Combined enum amendment ships inline with #230.
- **§13.13 — AST-based lint `tools/lint/red_zone_guard.py`** (round-3 A2). 6 bypass-pattern regression tests live in `tests/test_red_zone_guard.py`. Ships inline with #229/#230.

---

## 14. Acceptance gate for Sprint 1 Track A

**This ADR is the hard gate for issues #228, #229, #230.** Those tickets are owned by Sprint 1 Track A (memory model implementers, currently AndreyDeveloper84 per assignment). Coordination comments on each issue link this ADR + state explicitly that the schema in their initial migration MUST match §3.

**Migration discipline (Blocker #10 fix).** The DB CHECK constraints in §3.4 are added with the `NOT VALID` + `VALIDATE CONSTRAINT` pattern, NOT in one operation, to avoid table-lock storms if `memory_entry` ever has pre-existing rows at migration time:

```sql
-- Step 1: add the constraint as NOT VALID (cheap, only validates new rows from this moment)
ALTER TABLE memory_entry
  ADD CONSTRAINT memory_entry_yellow_red_requires_consent CHECK (...)
  NOT VALID;

-- Step 2 (separate migration, run during low-traffic window): validate against existing rows
ALTER TABLE memory_entry
  VALIDATE CONSTRAINT memory_entry_yellow_red_requires_consent;
```

For a greenfield table (no pre-existing rows in #229's initial migration), the `NOT VALID` step is harmless and the `VALIDATE` step runs in microseconds. For any future schema-amendment migration that adds a tightening CHECK to an existing populated table, this pattern is the only safe path.

**Verification checklist** (to be tested on the PR that lands #228/#229/#230):

- [ ] `MemoryEntry` migration adds: `sensitivity_zone` (already present), `source`, `last_inferred_at`, `delete_requested_at`, `consent_at` columns + the two DB CHECK constraints from §3.4 using the `NOT VALID` + `VALIDATE CONSTRAINT` pattern.
- [ ] Reverse migration (`migrate <app> <prev>`) tested — constraints + columns drop cleanly.
- [ ] `UserPersonalContext` migration adds: `forget_all_requested_at` column.
- [ ] `MemoryEntry.content` uses `EncryptedJSONField` (per ADR-0006).
- [ ] `RedZoneAccessLog` migration creates the table with INSERT-only role + no FK CASCADE (per #230 acceptance) + `'withdrawal'` value in `access_type` enum (§11.3).
- [ ] DB roles + view + trigger created per §7.2: `ayla_app`, `ayla_ops` + `memory_entry_safe` view, `BEFORE SELECT` trigger on red rows.
- [ ] `RedZoneReader` accessor exists at `apps/identity/services/red_zone_reader.py` + import-guard lint rule in `pyproject.toml`.
- [ ] Application-side memory writer (`apps/identity/services/memory_writer.py`) enforces minor protections (fail-closed on REST outage per §10.2) + writes `RedZoneAccessLog` for every red read.
- [ ] Test: insert with `source='explicit' AND last_inferred_at IS NOT NULL` → DB raises.
- [ ] Test: insert with `source='inferred' AND last_inferred_at IS NULL` → DB raises.
- [ ] Test: insert yellow entry with `consent_at = NULL AND soft_deleted_at = NULL` → DB raises.
- [ ] Test: insert yellow entry with `consent_at = NULL AND soft_deleted_at = now()` → DB allows (withdrawal flow).
- [ ] Test: red read without `RedZoneReader` accessor → memory writer raises before DB.
- [ ] Test: direct `psql -U ayla_app -c "SELECT content FROM memory_entry WHERE sensitivity_zone='red'"` without setting `ayla.red_zone_access_context` → trigger raises.
- [ ] Test: zone promotion `green → yellow` without `consent_token` → memory writer raises `ZonePromotionRequiresConsent`.
- [ ] Test: minor-age reconciliation job (§13.4) sweeps freshly-18 users + clears their minor flag.
- [ ] Test (round-3 A1): insert with `delete_requested_at IS NOT NULL AND deletion_reason IS NULL` → DB raises.
- [ ] Test (round-3 A1): insert live row with `deletion_reason IS NOT NULL` → DB raises.
- [ ] Test (round-3 A2): six AST-lint regression cases (variable-resolved literal, `.all()` then Python filter, `.raw()`, `.update()`/`.bulk_update()`, signal handlers, shell pathway) — each MUST fail lint when introduced outside `red_zone_reader.py`.
- [ ] Test (round-3 A2): RedZoneReader.read() raises on missing `purpose` argument; raises on invalid `request_id` shape (non-UUID).
- [ ] Test (round-3 A2 / pool-leaked auth): two sequential reads in the same pooled connection where the second SHOULD have no red access — second read MUST be denied (GUC cleared in finally).
- [ ] Test (round-3 A3): `ayla_app` cannot `CREATE TABLE`/`ALTER TABLE`/`DROP TRIGGER`; `ayla_migrator` can. `ayla_migrator` cannot `SELECT memory_entry.content` for red rows even with GUC set.
- [ ] Test (round-3 A4): cross-tenant red SELECT raises `TenantScopeViolation` at app layer BEFORE the DB query is issued, regardless of `STRICT_TENANT_REFUSE` flag value.
- [ ] Test (round-3 A6): writer fail-closed path writes one row to `RedZoneAccessLog` with `access_type='write_rejected_dob_lookup'`.
- [ ] Test (round-3 A7): simulated backup restore — entries with `delete_requested_at IS NOT NULL` in the live log remain unreadable after restore even though encrypted bytes are recovered.
- [ ] Test (round-3 A8): GUC set to empty string OR random non-UUID string → trigger raises; GUC set to a UUID → trigger allows.
- [ ] Test (round-3 A9): mock DOB-populated-later → reconciliation job emits `memory.minor_detected_postfact` + queues all yellow/red entries for soft-delete with `deletion_reason='minor_protection'` + creates an Ayla Pro queue ticket.

---

## 15. Legal audit invitation

This ADR is **engineering pre-commitment**, not a legal opinion. The Phase 1 prerequisite is external lawyer review:

- Engagement scope: 152-ФЗ + GDPR comparative review of §3–§11 of this document.
- Lawyer's expected output: either (a) sign-off as-is, or (b) an annotated diff that produces an ADR-0011-r2 amendment with the specific changes.
- Engineering commitment: if (b), implement amendments in a follow-up sprint, including any schema migrations + back-fills that the lawyer mandates.
- Conservative defaults here are designed so (a) is the likely outcome. If the lawyer demands stricter (e.g. shorter red-zone retention, additional zone, stricter consent flow), the schema can absorb most strictening without column changes — values, constants, and TTLs are tunable.

**Trigger for engagement:** Phase 1 kickoff (post Phase 0 close criteria met).

**Owner:** tech lead (Andrey Tikhonov) — schedules engagement, escalates blockers.
**Target window:** Phase 1 Week 1 (immediately post Phase 0 close).
**Pre-engagement deliverables:** this ADR + foundation memory-and-personalization.md + ADR-0006 + ADR-0009 §Memory model. Lawyer reviews the bundle and produces either sign-off or ADR-0011-r2 redline within 2 weeks of receipt.
**Escalation:** if no engagement scheduled by Phase 1 Week 2 OR if lawyer redline blocks Sprint 2 customer surfaces (#220 customer epic), tech lead escalates to founder and Phase 1 may slip pending review.

---

## 16. Alternatives considered

### 16.1 «Skip the ADR, do legal audit first»

Rejected. Without engineering boundaries, the memory models would either (a) ship without privacy fields and need retrofit, or (b) wait weeks for legal review while Sprint 1 stalls. ADR-first lets engineering land while audit proceeds in parallel; audit changes apply as an amendment.

### 16.2 «Single zone, encrypt everything»

Rejected. Removing zones loses the «yellow / red have different UX rules» distinction that the customer-facing memory surface relies on. Encryption-everywhere we already chose (§6).

### 16.3 «Store red-zone in separate DB»

Deferred to Phase 2+. Operational overhead today (separate DB cluster, backup story, cross-DB transactions for the access log) outweighs the marginal isolation benefit. The pepper'd encryption (§6) + append-only audit (§7) is sufficient defence-in-depth for MVP.

### 16.4 «Use Postgres RLS for zone access control»

Considered. Row-Level Security gives us another defence layer but adds operational complexity (every connection needs a role context, including replay/audit jobs). Defer; revisit if a regulatory audit demands it explicitly.

**Phase 2+ migration plan (round-3 A10).** When RLS lands, it REPLACES the §7.2 `BEFORE SELECT` trigger — the two should not coexist long-term because RLS's standard pattern uses `current_setting('app.current_user')` + `SET LOCAL` per transaction, and mixing with a trigger that does its own GUC check is tooling-hostile (different connection-pool semantics for `SET LOCAL` vs `set_config(..., is_local=true)`; double-evaluation of the same access policy at two layers).

Phase 2 migration steps:

1. Add RLS policy `red_zone_access_policy` to `memory_entry` using `USING (sensitivity_zone != 'red' OR current_setting('ayla.red_zone_access_context', true) ~ '^[0-9a-f-]{36}$')`.
2. Enable RLS on `memory_entry`.
3. Verify all red-zone access paths still work (RedZoneReader sets the GUC via `SET LOCAL` inside its transaction).
4. Drop the §7.2 `BEFORE SELECT` trigger.
5. Migrate `ayla_app` to `BYPASSRLS=false`; `ayla_ops` already bypasses via the `memory_entry_safe` view, no change.

The trigger is therefore an **MVP-only construct** — call this out to the implementer of #230's migration so they don't optimize trigger internals knowing they'll be removed in Phase 2.

---

## 17. References + cross-links

- [ADR-0009 — Ayla split-domain architecture](./ADR-0009-ayla-split-domain-architecture.md) §Memory model + §Hard rules
- [ADR-0006 — Field-level encryption](./ADR-0006-field-level-encryption.md)
- [`docs/design/policies/ayla-memory-and-personalization.md`](../design/policies/ayla-memory-and-personalization.md) — customer-facing UX spec
- [`docs/design/policies/ayla-identity-and-brand.md`](../design/policies/ayla-identity-and-brand.md) — Q-AYL13 (minor onboarding) resolved 2026-05-20
- Issues this ADR gates: [#228](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/228), [#229](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/229), [#230](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/230)
- Issues this ADR informs: [#231](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/231) GET memory, [#232](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/232) DELETE entry, [#233](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/233) forget-all, [#234](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/234) red-zone TTL sweep, [#236](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/236) memory transparency UI, [#269](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/269) data export (closed pre-deploy lock)
- Memory: `project_ayla_memory_hybrid_model`

---

## Last verified

2026-05-21 — initial draft, locked engineering boundary before Sprint 1 Track A model implementation. Awaiting external legal audit (Phase 1 prerequisite).
