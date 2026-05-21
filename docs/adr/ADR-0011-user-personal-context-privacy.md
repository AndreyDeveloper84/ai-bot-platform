# ADR-0011: UserPersonalContext Privacy & Retention Policy (152-ФЗ engineering boundaries)

**Status:** Accepted — 2026-05-21
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
| `last_inferred_at` | timestamptz, nullable | When inference last updated this entry. **MUST be NULL when `source = 'explicit'`** (DB CHECK constraint enforced). Updated on every re-inference pass; the value answers «how stale is this guess?» |
| `delete_requested_at` | timestamptz, nullable | Set when the user requests deletion of this specific entry (DELETE `/api/v1/users/me/memory/{entry_id}` — #232). `soft_deleted_at` (already in #229) is set by the soft-delete job that processes the request; `delete_requested_at` records the user's intent moment for audit. |
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

The CHECK constraints called out above MUST be DB-level, not application-level. Per Phase 0 freeze allow-list (ADR-0009 §Hard rule #3): infra migration changes are allowed.

```sql
ALTER TABLE memory_entry
  ADD CONSTRAINT memory_entry_inferred_nullness CHECK (
    (source = 'explicit' AND last_inferred_at IS NULL)
    OR (source IN ('inferred', 'signal'))
  );

ALTER TABLE memory_entry
  ADD CONSTRAINT memory_entry_yellow_red_requires_consent CHECK (
    sensitivity_zone = 'green'
    OR (sensitivity_zone IN ('yellow', 'red') AND consent_at IS NOT NULL)
  );
```

Both constraints fire at INSERT and UPDATE. App-level validation is a courtesy; the DB is the truth.

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

- Health (pregnancy status, chronic conditions, mental-health flags, current medication relevance to procedures)
- Sexual orientation, religious affiliation, racial/ethnic identity (only stored if user-stated AND relevant to service contraindication)
- Biometric raw data — NOT stored as memory entries. If captured at all (e.g. avatar photos for wellness avatar), it lives in the wellness module's red-zone storage with its own rules. Memory entries point by reference only.

**Consent basis:** explicit consent. `consent_at` set when the user goes through the explicit «yes, remember this for contraindication filtering» flow. Step-up auth required for reads (per `ayla-memory-and-personalization.md` §10).

**Used:** USE-ONLY pattern — Ayla checks red-zone facts to filter contraindicated services or warn the user. Ayla NEVER speaks the red fact back to the user unprompted in mixed-company channels (mini-app screens, MAX chat thread with someone watching), and NEVER passes red facts to providers in any form. The cross-tenant reuse rule (§9) is **strictest** for red: red is per-user, never exposed beyond the user.

---

## 5. Retention per zone

| Zone | Default retention | Trigger for purge |
|---|---|---|
| Green | Until user soft-deletes (no auto-TTL) | DELETE entry endpoint OR forget-all |
| Yellow | 365 days from last use, OR until consent withdrawn, OR until user delete | `last_used_at < now() - INTERVAL '365 days'` OR `consent_at` cleared OR delete request |
| Red | 90 days from last use; immediate on user delete request | `last_used_at < now() - INTERVAL '90 days'` OR delete request OR forget-all |

Implementation note: TTL is enforced by the nightly red-zone TTL sweep job (#234) for red, and a similar yellow sweep (not yet ticketed; add as follow-up). Green has no auto-TTL — facts persist as long as the user wants them.

«Last used» = max of `last_used_at` (#229 already has this field) and `consent_at`. So a fact that the user explicitly consents to today resets its TTL — consent is itself a form of refresh.

---

## 6. Encryption at rest

Per [ADR-0006](./ADR-0006-field-level-encryption.md):

- `MemoryEntry.content` (JSONB payload) is stored via `EncryptedJSONField` from `django-cryptography-django5` for **all** zones, not just yellow/red. Green is encrypted too — the marginal cost (~microseconds per read/write) is negligible, and homogeneous encryption simplifies key-rotation tooling.
- Encryption key (`DJANGO_CRYPTOGRAPHY_KEY`) sourced from the deployment secret manager. Key rotation is supported via Fernet's multi-key bundle.
- **Plaintext snapshots are forbidden anywhere.** No log line, no admin dump, no replay fixture stores plaintext `MemoryEntry.content`. The audit table stores SHA-256 fingerprints when needed for dedup (per ADR-0006 pattern for token fingerprints).

Red-zone specifics:

- `MemoryEntry.content` for red entries is additionally **hash-pepper'd** before encryption — the hash pepper is a separate secret from the encryption key. This double-key layout means even if the encryption key leaks, red-zone plaintext is not recoverable without also obtaining the pepper from a different secret store. (Pepper management defined in the secret-manager runbook; not in code.)
- Schema MAY be revisited Phase 2+ when KMS becomes available (ADR-0006 §Alternatives — deferred).

---

## 7. Access logging — RedZoneAccessLog

Per #230, the audit table is **already specified** as append-only, 7-year retention, no FK CASCADE. This ADR adds the policy that drives those constraints:

- **Every read** of a red-zone `MemoryEntry.content` MUST write a `RedZoneAccessLog` row in the SAME DB transaction as the read. If the read happens outside a transaction or the log write fails, the read MUST abort.
- **Every write** to a red-zone entry (create/update/soft-delete/purge) MUST write a log row.
- **Accessor categories:** `ayla_llm` (LLM prompt construction reads), `system_job` (TTL sweep, forget-all), `ops_admin` (manual operator action — must include `request_id` referencing the operator's audit-trail ticket).
- **Yellow and green reads are NOT logged in `RedZoneAccessLog`** — only red. Audit volume would be prohibitive. Yellow access is captured at the cumulative `MemoryEntry.last_used_count` level, which is sufficient for the user's «show me when this was used» UX without per-read overhead.
- **No FK CASCADE** — when a `MemoryEntry` is soft-deleted or purged, its `RedZoneAccessLog` rows STAY. This preserves the audit history of pre-deletion reads, which the lawyer will want.

---

## 8. 152-ФЗ subject rights mapping

| Right | Article (152-ФЗ) | Endpoint | Notes |
|---|---|---|---|
| Right to know what's stored | §14 (right to access) | `GET /api/v1/users/me/memory` — #231 | Returns paginated entries with zone, kind, source, summary text. Red zone hidden by default; revealed only after step-up auth. |
| Right to rectify | §14 + §16 | `PATCH /api/v1/users/me/memory/{entry_id}` — to add | Not yet ticketed. **Follow-up:** add ticket for PATCH endpoint before Sprint 2 customer-surfaces work. |
| Right to erase (per-entry) | §14 + §16 | `DELETE /api/v1/users/me/memory/{entry_id}` — #232 | Sets `delete_requested_at` instantly; soft-delete job runs within minutes. |
| Right to erase (all) | §14 + §16 | `POST /api/v1/users/me/memory/forget-all` — #233 | Sets UPC `forget_all_requested_at`; async sweep soft-deletes all entries + summary. Tombstone retention 90 days for audit. |
| Right to portability | §14 | Data-export job — #269 (PRE-DEPLOY lock, closed) | Closed pre-deploy lock decided JSON + PDF formats; data export job to ship Sprint 2. |
| Right to object to processing | §14 + §15 | UI toggle in Bonuses → Memory section («Не запоминай меня») | Sets `forget_all_requested_at` and disables future writes. Not yet ticketed; follow-up. |
| Right to know about automated processing | §16 | Memory transparency UI surfaces `source` per entry («explicit» vs «inferred» vs «signal») — #236 | Customer sees provenance. |

**Gaps tracked above:** PATCH endpoint + processing-objection toggle. Both follow-up tickets created at issue close (see §13).

---

## 9. Cross-tenant reuse boundary (per ADR-0009)

The boundary established in ADR-0009 §Memory model is restated here as engineering rule:

- **Green** facts: reusable across providers without consent friction.
- **Yellow** facts: reusable across providers within the user's session, BUT the voice modulator (`apps/llm/persona/`) MUST filter them out of any string addressed to a tenant-staff identity. Yellow leaks to providers = a contract violation.
- **Red** facts: never traverse a tenant boundary. They drive contraindication filters at Ayla's side only; the tenant sees the *result* (booking allowed / booking blocked with generic reason) but never the underlying fact.

**Provider-specific facts** (visit at salon X, complaint about master Z, payment to tenant W) live in Ayla djangoproject's per-tenant scope (Layer 2 per the memory hybrid model) and **never leak across tenants regardless of zone**. Those aren't UPC memory entries; this ADR doesn't change their handling. Ayla djangoproject's own privacy ADR (when written) covers Layer 2.

---

## 10. Minor protections (users <18)

Per [Q-AYL13](../design/policies/ayla-identity-and-brand.md) and [Q-AML8](../design/policies/ayla-memory-and-personalization.md) (both resolved 2026-05-20):

- Customers under 18 may use Ayla for booking, but the memory writer guard MUST:
  - Block `sensitivity_zone IN ('yellow', 'red')` writes outright.
  - Allow `green` + `source='explicit'` only — no behavioural inference, no signal-derived entries.
  - Block all red-zone reads (returns empty; if Ayla's logic asks for a red fact, it gets «no data» and degrades gracefully).
- Minor age determination: from Ayla djangoproject's user profile (DOB). If DOB unknown, default to «adult» — the right answer is to surface the unknown-age problem in Ayla Pro queue, not silently apply minor protections to all unknowns (which would degrade the adult-default UX).
- These protections are encoded in the memory writer (`apps/identity/services/memory_writer.py` — to be created in #229's scope) with a DB CHECK as a backstop:

```sql
ALTER TABLE memory_entry
  ADD CONSTRAINT memory_entry_minor_zone_restriction CHECK (
    NOT EXISTS (
      SELECT 1 FROM "user" u WHERE u.id = memory_entry.user_id
        AND u.dob IS NOT NULL
        AND extract(YEAR FROM age(u.dob)) < 18
        AND (memory_entry.sensitivity_zone IN ('yellow', 'red')
             OR memory_entry.source != 'explicit')
    )
  );
```

(Note: CHECK with subquery is not standard Postgres; implementation MAY substitute a trigger. The semantic contract is what matters — the DB enforces minor restrictions, not just application code.)

---

## 11. Consent semantics + legal basis cheat sheet

| Zone | Legal basis for storage | How `consent_at` is set | Withdrawal effect |
|---|---|---|---|
| Green | Performance of contract (152-ФЗ §6.1.5) | NULL (no per-entry consent record needed) | Withdrawal = entry delete; basis preserved via service contract |
| Yellow | Specific consent (152-ФЗ §6.1.1) | Timestamp when user explicitly stated or confirmed inference in chat | Withdrawal → `consent_at` cleared → entry auto-deleted within 24h by yellow TTL sweep |
| Red | Explicit consent for special-category data (152-ФЗ §10.1.1) | Timestamp when user passed the explicit «remember for contraindication» dialog | Withdrawal → `consent_at` cleared → entry deleted by red TTL sweep within 24h + immediate flag in `RedZoneAccessLog` (`access_type='withdrawal'`) |

If a fact's legal basis ever changes (e.g. green → yellow because new context made it sensitive), the entry's zone is updated AND `consent_at` MUST be set if moving into yellow/red — the DB constraint enforces this.

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
- Withdrawing consent on a red entry triggers immediate deletion, which may surprise users who expected «soft remove, restore later». UX should not offer «restore deleted memory» — that would re-create the consent question and the audit trail would be a mess. UX spec'd in `ayla-memory-and-personalization.md` matches this.
- The minor-protection CHECK constraint (§10) requires `users` and `memory_entry` to be in the same DB schema. Per ADR-0009, **canonical User identity lives in Ayla djangoproject, not bot-platform**. So this constraint actually has to be enforced application-side via the memory writer + an idempotency check at every write, with a periodic reconciliation job that hits Ayla REST for DOB. **Follow-up ticket:** specify the reconciliation job. The DB constraint above is aspirational and would only fire if we later move user identity in-repo.

---

## 13. Follow-up tickets to create

These tickets DO NOT block this ADR's acceptance. They are the work that this ADR surfaces as needed-soon:

- **PATCH endpoint for memory entry rectification** (152-ФЗ right to rectify) — needed Sprint 2.
- **Yellow-zone TTL sweep job** — paralleling the existing red-zone TTL sweep (#234). Default 365-day window.
- **Processing-objection toggle** in Bonuses → Memory UI — sets `forget_all_requested_at` + disables future writes (distinct from forget-all because users may want «remember nothing further» without erasing past).
- **Minor-age reconciliation job** — hits Ayla REST for DOB on a daily cadence, applies minor protections (or unflags) to UPCs whose age status changed (rare, but birthday transitions exist).
- **Voice modulator yellow-zone non-disclosure test** — adds a regression test ensuring yellow-zone facts are filtered out of provider-addressed strings (covers `apps/llm/persona/` #280 acceptance).

---

## 14. Acceptance gate for Sprint 1 Track A

**This ADR is the hard gate for issues #228, #229, #230.** Those tickets are owned by Sprint 1 Track A (memory model implementers, currently AndreyDeveloper84 per assignment). Coordination comments on each issue link this ADR + state explicitly that the schema in their initial migration MUST match §3.

**Verification checklist** (to be tested on the PR that lands #228/#229/#230):

- [ ] `MemoryEntry` migration adds: `sensitivity_zone` (already present), `source`, `last_inferred_at`, `delete_requested_at`, `consent_at` columns + the two DB CHECK constraints from §3.4.
- [ ] `UserPersonalContext` migration adds: `forget_all_requested_at` column.
- [ ] `MemoryEntry.content` uses `EncryptedJSONField` (per ADR-0006).
- [ ] `RedZoneAccessLog` migration creates the table with INSERT-only role + no FK CASCADE (per #230 acceptance).
- [ ] Application-side memory writer (`apps/identity/services/memory_writer.py`) enforces minor protections + writes `RedZoneAccessLog` for every red read.
- [ ] Test: insert with `source='explicit' AND last_inferred_at IS NOT NULL` → DB raises.
- [ ] Test: insert yellow entry with `consent_at = NULL` → DB raises.
- [ ] Test: red read without `RedZoneAccessLog` write → memory writer raises before DB.

---

## 15. Legal audit invitation

This ADR is **engineering pre-commitment**, not a legal opinion. The Phase 1 prerequisite is external lawyer review:

- Engagement scope: 152-ФЗ + GDPR comparative review of §3–§11 of this document.
- Lawyer's expected output: either (a) sign-off as-is, or (b) an annotated diff that produces an ADR-0011-r2 amendment with the specific changes.
- Engineering commitment: if (b), implement amendments in a follow-up sprint, including any schema migrations + back-fills that the lawyer mandates.
- Conservative defaults here are designed so (a) is the likely outcome. If the lawyer demands stricter (e.g. shorter red-zone retention, additional zone, stricter consent flow), the schema can absorb most strictening without column changes — values, constants, and TTLs are tunable.

**Trigger for engagement:** Phase 1 kickoff (post Phase 0 close criteria met). Tech lead schedules.

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
