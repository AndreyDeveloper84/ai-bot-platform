# Ayla — Care Runtime / Code Reconciliation Audit

Read-only audit. No code written, nothing committed, nothing changed on the pilot.
Refs read: `ai-bot-platform` **origin/dev @ 789e82b**, `beautygo_backend` (djangoproject) **origin/dev @ e07388ff**, `ayla-knowledge` **main @ deb3d0a**, `ayla-ai-core` main @ 87c461c (bot pins f773e7d).
Pilot read via `ssh taximeter@194.87.99.126`: Ayla DB `dev-db-1/beautygo`, bot DB `ayla-bot-staging-postgres-1/platform/ai_bot_platform`. Read-only SQL only.

Date: 2026-08-23.

---

## 0. Head-line answers to the two STOP conditions the orchestrator flagged as likely

**STOP #2 — "no authoritative completion fact exists": NOT triggered.** The fact exists, is
well-modelled, carries actor attribution, has an event id and end-to-end idempotency, and has a
registered consumer. It has, however, **never fired on the pilot** — and the only "completion"
data that exists today is elapsed-time inference produced by the bot itself, which OR-CARE-5
forbids as evidence. Detail in §4.

**STOP #4 — "proactive outbound requires consent bypass": NOT triggered.** A consent-gated
proactive pattern exists and is being extended right now (DRF-1285 / PR #1236) without bypassing
consent. But the *existing shipped* post-visit proactive task has **no consent gate at all** —
only an opt-out. Care must not inherit that. Detail in §6.4.

**A third STOP condition is closer than either of those: #5, "triage requires weakening existing
safety."** Not triggered — Care sits after safety and makes the risk call deterministic rather than
model-generated, which strengthens it. But the reassurance question it raises is a real owner
decision. See §7 and §11.

**The actual blocker is not on the STOP list at all.** Every mechanism Care needs exists and fits.
What does not exist is the **laser protocol content**. The "four existing research documents"
OR-CARE-3 treats as source material are four Google Docs seeded into `global_kb`; `kb_kbdocument` is
**empty on the pilot (0 rows)**; the four doc ids were never committed; and the one readable upstream
research report contains **zero** laser content (`лазер` 0, `эпиляц` 0, `фототип` 0) because it was
written against a guessed massage / LPG / RF service set while the catalog was still "unspecified".
A laser protocol cannot be normalized from material that is not there, and producing it is the mass
service research the task forbids. Detail and the two clean options in §8.

---

## 1. Existing assets

Everything Care needs already exists somewhere. Nothing below is proposed; all of it is on `origin/dev` today.

| Need | Existing mechanism | Location | Reusable? |
|---|---|---|---|
| Service completion (authoritative) | `close_booking()` → `emit_booking_completed()` → OutboxEvent `booking.completed` | `appointments/application/services/completion.py:69` / `:29` (Ayla) | **Yes** — §4 |
| Cross-service event bus | `appointments_outboxevent` → HTTP publisher → bot ingest → `IngestDedupe` | `appointments/tasks.py` (Ayla) + `apps/eventbus/ingest_dispatcher.py:243` (bot) | **Yes** — exactly-once per `event_id` |
| Completion consumer | `handle_booking_completed`, registered `("booking.completed", 1, …)` | `apps/eventbus/consumers/booking.py:1302`, registry at `:1565` | **Yes** — the aftercare seam |
| Canonical service identity | `services.ServiceCategory` UUID tree; `SalonService.category_id` | `services/models.py:13` / `:348` (Ayla) | **Yes** — §2 |
| Consultation / service selection | `discover_masters(resolve_service=True)` → `MasterCard.service_id` | `apps/marketplace/discovery.py:559`, DTO `apps/marketplace/dto.py:20` | **Yes** — §6.1 |
| Live conversation path | concierge, `skill_selected='concierge'` on 40/40 pilot AI turns | `apps/orchestrator/concierge.py` | **Yes** |
| Pre-LLM branch chain | safety → human_handoff → visit-callbacks → personal booking lookup → onboarding → discover-cb → booking-cb → deterministic show-masters → LLM | `apps/channels/max/handler.py` (chain documented at `apps/orchestrator/concierge.py:758-762`) | **Yes** — triage becomes a branch after safety |
| Proactive outbound | `bookings.send_post_visit_followups` (beat 16:00 UTC) | `apps/bookings/followups.py`; beat at `config/settings/base.py:1146` | **Partly** — no consent gate, §6.4 |
| Outbound channel | `apps.channels.max.outbound.send_message` | — | **Yes** |
| Consent gate | `has_global_consent(bot_user, ConsentType.HEALTH)` | `apps/consent/services.py:403`; used at `apps/orchestrator/nutrition_context.py:187` | **Yes** — §6.4 |
| Handoff to human | `handoff_admintask` + `apps/handoff/notify.py` | — | **Yes** — do not build a second |
| Audit / event history | `AuditLog` (tenant, actor_id, action, target, target_id, payload JSONB, retention) | `apps/audit/models.py:56`; `write_audit()` `apps/audit/services.py:42` | **Yes** — §7 CareEvent |
| Observability | `AIRequestMetric`, written synchronously from the AI hot path | `apps/observability/models.py:114` | **Yes** |
| Versioned structured data + validation + CI | `.knowledge/schema.yaml`, `scripts/validate_knowledge.py`, `03 AI System/Contracts/*.yaml` | ayla-knowledge @ main | **Yes** — §3 |
| Version-pinned runtime delivery | `ayla-ai-core` pinned by SHA in `pyproject.toml:152` | ai-bot-platform | **Yes** — §3 |
| Strict structured loader pattern | `from_dict()` with unknown-key rejection → frozen dataclass | `apps/replay/fixtures/schema.py:65`, loader `fixtures/loader.py:31` | **Yes** — copy the shape |

### Three buses, not one — and Care must pick the right one

This is the single most load-bearing structural fact in the audit.

| # | Bus | Entry point | Delivery guarantee | Care may use it? |
|---|---|---|---|---|
| 1 | **Cross-service ingest** (Ayla → bot) | `apps/eventbus/ingest_dispatcher.py:243 dispatch_envelope` | `IngestDedupe` PK insert *inside* the handler transaction (`:255-268`) → exactly-once per `event_id`; failures → DLQ | **Yes — the only correct one** |
| 2 | **Internal domain bus** | `apps.eventbus.services.emit` + `DOMAIN_EVENT_SUBSCRIBERS` chain | Subscriber chain. **Default `NoopSubscriber` (`config/settings/base.py:448`), and the env var is UNSET on the pilot → zero subscribers today** | No |
| 3 | **Telemetry fanout** | `apps.events.services.emit` + `EVENT_FANOUTS` | "Never raises"; `fanout_all` catches and swallows per-adapter (`apps/events/fanout.py:29-33`). Default `NoopFanout` | **No — swallows errors by contract** |

Buses 1 and 2 both carry a topic literally named `booking.completed`, with **incompatible payloads**:
bus 1 `{appointment_id, client_id, specialist_id, completed_at, completed_by}` (`completion.py:44-58`),
bus 2 `{booking_id, completed_at, marked_by}` (`apps/bookings/tasks.py:481-491`, required-field set declared at `apps/eventbus/vocabulary.py:67`).
A Care consumer written against the wrong one gets elapsed-time inference instead of a fact.

**Constraint on the ingest bus:** `_REGISTRY.get(key)` (`ingest_dispatcher.py:240`) allows exactly **one** handler per `(event_name, event_version)`. Care cannot register a second `booking.completed` consumer; it must be invoked from inside `handle_booking_completed`, in the same transaction as the dedupe row. That is a feature — it is what makes "duplicate event → one CareEpisode" free.

---

## 2. Service identity — laser epilation

**OR-CARE-3 STOP condition #1 is NOT triggered.** Laser epilation has a stable identity. It is not
the service name and not a single card — it is a **category UUID that already exists on both sides**.

### The orchestrator's brief was wrong about the size of the set — usefully wrong

| Counting method | Rows |
|---|---|
| `services_salonservice` where name matches `лазер|эпил` | **3** |
| `services_salonservice` where `category_id = ea63f753-b46a-40d0-8cf7-1075dc1bdb4e` | **17** |
| In the category but invisible to the name regex | **14** |

`Подмышки`, `Верхняя губа`, `Голени с коленями`, `Руки до локтя`, `Руки полностью`, `Ягодицы`,
`Бёдра полностью`, `Бикини по линии белья / глубокое / тотальное`, `Must Have`, `Классика`,
`Super`, `Всё тело` are all laser and none of them contain the word.
**Name matching is disqualified as a mapping key** — it would have silently excluded 82% of the scope.

### Which table is source of truth — settled

`services_service` = **0 rows**; `services_salonservice` = **58**; `services_specialistservice` = 232;
`services_servicetemplate` = 1223; `services_servicecategory` = 85.
`services/models.py:308-313` states it outright: the `ServiceTemplate → SalonService →
SpecialistService` layer is the S3A canonical rebuild, and `Service` / `Appointment` are
deliberately untouched under a strangler-fig plan whose cutover is a separate authorized chunk.
`Service` is the **legacy/marketplace leg**; `SalonService` is live. The empty table is not a defect.

Category shape on the pilot:

```
9f81a963  Лазерная эпиляция, депиляция и удаление волос   (root)
├── ea63f753  Лазерная эпиляция     → 17 SalonServices, 68 SpecialistServices
└── c5459470  Восковая депиляция    → 0 SalonServices
```

`ea63f753` is a leaf — the mapping is one UUID, no subtree walk.

### Recommended mapping — one row, zero migration

```
CareServiceScope registry:  ea63f753-b46a-40d0-8cf7-1075dc1bdb4e  ->  laser_epilation
```

Two resolution paths, both working today:
* Ayla side: `Appointment.salon_service_id → SalonService.category_id → scope` — resolves for **8/8** pilot appointments.
* Bot side: `RemoteBookingProxy.service_id → CatalogService.ayla_service_id → raw->>'category' → scope` — resolves for **18/24** proxies. `CatalogService` has no category column; the category rides in `raw` by design (`apps/catalog/services/upserter.py:123-137`).

`git grep -i "service_scope\|CareServiceScope\|laser_epilation"` → **0 hits in both repos**. Greenfield name, existing key.

### Bundles do NOT break the scope — but they break zone granularity

All four bundles (`Must Have`, `Классика`, `Super`, `Всё тело`) sit **inside** the laser category, and
no bundle in the 58-row catalog mixes laser with non-laser (the cross-modality complexes live in
`Комплексы для тела` and are entirely non-laser). So `service_scope=laser_epilation` is correct for
all 17 cards.

What bundles break is anything **below** the scope: `Всё тело` is one card covering 4+ zones, so a
per-zone course/interval model cannot be derived from the service id and has no existing key to
hang off. **If the Care protocol is scope-level, this is a non-issue today. If it must be
zone-level, that is a separate unresolved design question — flagged, not solved.**

### Two caveats

1. **`SalonService.category` is nullable** (`services/models.py:348`) and only guaranteed when
   `template` is NULL (`clean()` `:383-387`). All 58 pilot rows are `template=NULL` with category
   set, so it holds today; a future template-derived row needs a `template.category` fallback.
   Two-step resolver, still no migration.
2. **DRF-1103 confirmed on data, root cause not established.** All 6 `source=automation` proxies
   have `service_id` NULL; all 18 `source=mobile_app` proxies have it set. But `origin/dev` *does*
   map it (`apps/skills/booking/tools.py:3441-3444`, `provider.py:433-447`). Either staging is
   behind `origin/dev` or the Ayla create-response on that path omits `service`. Not distinguished.
   **Consequence: bot-side scope resolution is unusable for bot-made bookings until 1103 closes.
   Ayla-side resolution is unaffected.**

### Coverage tooling exists

`apps/catalog/management/commands/ayla_service_id_coverage.py` prints a per-tenant
`grounded / active / coverage` table with a threshold warning, looping tenants inside
`tenant_scope()` (`:51-54`). Swap the numerator predicate to "resolves to a known `service_scope`"
and it is a care-scope coverage report unchanged. Current pilot: `ayla_service_id` 58/58 = 100%;
`laser_epilation` would read 17/58 = 29.3%, which is correct, not a gap.

---

## 3. Protocol storage

**Recommendation: canonical source in `ayla-knowledge`, following the existing
`03 AI System/Contracts/` machine-readable-registry pattern (not the markdown-node pattern).
Reject `apps/promptreg`.**

### Why promptreg is the wrong shelf — four disqualifying facts

| Claim | Verdict | Evidence |
|---|---|---|
| Models `PromptVersion` / `ThresholdConfig` / `DisclaimerLibrary` exist | TRUE | `apps/promptreg/models.py:37`, `:116`, `:187` |
| Accessors exist | TRUE | `registry.py:44`, `:68`, `:104` |
| **Zero production readers** | **TRUE** | 27 hits inside `apps/promptreg/`, 5 outside — all five in `tests/e2e/test_prompt_live_reload.py`, only 2 of them real calls (`:100`, `:115`) |
| Has an ACTIVE gate | TRUE | `is_active` `models.py:71`, enforced atomically in `services.py:95-103` |
| Has version **pinning** | **FALSE** | `get_prompt(tenant, skill_name)` has no version parameter; it filters `is_active=True` and takes `.order_by("-published_at","-version").first()` (`registry.py:56-59`). Latest-active-wins, not pinning. **This alone fails OR-CARE test gate #5 ("pinned protocol version").** |
| Stores structured data | **FALSE** | `grep JSONField apps/promptreg/*` → **0 hits**. `PromptVersion.body` = `TextField` (`:57`), `DisclaimerLibrary.text` = `TextField` (`:236`), `ThresholdConfig.value` = scalar `DecimalField` (`:141`) |
| Has a loader/validator | **FALSE** | None in the module. DB rows in, DB rows out |
| Tenant-scoped | TRUE, **mandatorily** | non-nullable `tenant` FK on all three models (`:45`, `:131`, `:218`) |

Two of those are fatal on their own. Storing a `ServiceCareProtocol` here means **a new model +
migration + admin + cache + accessor** — precisely the new CRUD/DB service OR-CARE-2 forbids. And
the mandatory tenant FK would force one duplicated row per salon for what is a cross-salon
clinical fact.

### Why ayla-knowledge is right

* It satisfies OR-CARE-2 literally: repository-backed, version-controlled, structured.
* **The pattern already exists there.** `03 AI System/Contracts/intent-registry.yaml`,
  `slot-registry.yaml`, `intent-output.schema.json` are versioned structured YAML appendices
  carrying `registry: / registry_version: / compatible_contract_version: / source_document: /
  status: / updated:` (`intent-registry.yaml:9-15`) with a written change-control policy
  (`:6-8`: additive = minor, semantic change = major). A third registry file is a config edit.
* It is the **only** candidate with a real schema + validator + CI + lifecycle. `.knowledge/schema.yaml`
  carries `enums.status` (15 values, `:74-89`), `activation_status: [pending-infrastructure, active,
  suspended]` (`:90-93`), `canonical_status: [draft, candidate, approved, deprecated]` (`:99-103`),
  a `lifecycle_transitions` state machine (`:370-384`), and **Active Canon uniqueness** — one active
  canonical revision per `node_id` (`uniqueness_rules` `:341-356`). `red_flags` and triage rules are
  safety content; this is the only place in the contour with an approval gate for them.
* Registering a new document type is the sanctioned mechanism, not a schema-philosophy change:
  `document_type_rules` (`schema.yaml:386`) is an open registry and `schema_changes` records
  repeated precedent (`:6`, `:12`, `:19`, `:22`).

### Two gaps that must be budgeted, not glossed

**Gap 1 — there is no runtime path from ayla-knowledge into the bot. None, today.**

```
git -C ai-bot-platform grep -n "ayla-knowledge\|ayla_knowledge" origin/dev -- '*.py'   -> 0 hits
git -C ai-bot-platform grep -n "intent-registry\|slot-registry"  origin/dev            -> 0 hits (all file types)
```

ayla-knowledge is not a dependency in `pyproject.toml`. It is an **authoring-time / CI-only
artifact**. Corroboration that the two sides are already silently divergent:
`apps/orchestrator/intent_router.py:82` hardcodes six intent strings with no relationship to the
20+ intents in `intent-registry.yaml`.

*The cheapest existing pattern for closing this is the `ayla-ai-core` SHA pin* —
`pyproject.toml:152` pins `ayla-ai-core[django] @ git+…@f773e7d…` with a documented bump checklist
(`:85-150`) and 7 production import sites (`concierge.py:46`, `memory_block.py:33`,
`apps/persona/voice.py:91`, `promptreg/voice_examples.py:47`, …). That is repo-backed,
version-pinned, runtime-loaded data **already in production in two repos** — i.e. version pinning
done right. The reader itself should be copied from `apps/replay/fixtures/schema.py:65`
(`from_dict`, unknown-key rejection, frozen dataclass; loader `fixtures/loader.py:31`) — roughly
130 lines of an existing in-repo pattern, not a framework.

**Gap 2 — `validate_knowledge.py` will not validate your file.** `markdown_paths()` at `:118` is
`ROOT.rglob("*.md")`. The existing `Contracts/*.yaml` registries are **completely unvalidated** —
zero references from `scripts/`, `tests/`, `.github/`, `.knowledge/`. Follow
`scripts/validate_amd001_schemas.py` instead (it meta-validates JSON Schema 2020-12 blocks against
14 positive/negative fixtures): a third script plus one step in the existing CI workflow. That is
the repo's own convention for CI-gating structured content.

### Candidate considered and rejected on the merits

`apps/kb` / the `global_kb` system tenant is semantically the closest thing in the contour —
`apps/kb/constants.py:14-22` says `global_kb` owns *"the universal services catalog, **the
contraindication matrix, the aftercare protocol**"*. But `KbDocument` is Postgres + ChromaDB,
ingested from Google Docs and chunked for RAG; `doc_type` is locked at six values
(`apps/kb/models.py:49-63`) with a documented migration + chunker-strategy cost to extend, and it
stores **prose for semantic retrieval**, not addressable structured fields. It has a version but no
ACTIVE flag. Fails the "structured" requirement. Worth naming so the owner knows it was considered.

### Summary of the six criteria

| | owning repo | path | loader | validation | ACTIVE gate | version pinning |
|---|---|---|---|---|---|---|
| **Recommended** | ayla-knowledge | `03 AI System/Contracts/service-care-protocol.*.yaml` + a companion spec node | new, copied from `apps/replay/fixtures/schema.py:65` | new script per `validate_amd001_schemas.py`, existing CI workflow | `canonical_status` + Active Canon uniqueness (`schema.yaml:341-356`) | `registry_version` in-file **+** SHA pin at the consumer, per `pyproject.toml:152` |

---

## 4. Completion source of truth

### The fact exists and is well built

```
appointments/application/services/completion.py:69   close_booking(appointment, *, completed_by)
                                                     ├── appointment.complete(completed_by=…)   state machine re-check under the row lock
                                                     └── :29 emit_booking_completed(…)          one OutboxEvent row
```

Payload (`completion.py:44-58`):
`{appointment_id, client_id, specialist_id, completed_at, completed_by}`,
`user_id = appointment.client_id`, `tenant_id = safe_tenant_id(…)`, `actor = envelope_actor_for(completed_by)`.

**Three callers, one implementation** — deliberately, so a visit closed at the front desk and one
closed by the sweep are byte-identical to every consumer (`completion.py:1-18`):

| Caller | Location | Actor | Evidence strength |
|---|---|---|---|
| Specialist mobile API | `appointments/views.py:530` | `specialist` / `client` per `resolve_booking_operator` | **Human** |
| Salon front desk API | `tenants/appointments_api.py:722`, route `tenants/urls.py:76` `me/appointments/<uuid>/complete/` | `salon` (`OperationalActor.SALON`, added by DRF-1064) | **Human** |
| 3-hour elapsed sweep | `appointments/tasks.py:362` via `auto_complete_elapsed_bookings` (`:390`) | `system` | **Elapsed time only** |

`OperationalActor` (`appointments/domain/value_objects.py:120`) exists precisely so these are
distinguishable — its docstring says DRF-1064 added `SALON` because otherwise "the salon closed
it" and "the 3-hour sweep closed it" would have been the same value.

**This is the field OR-CARE-5 needs.** The sweep's own docstring concedes: *Elapsed time is weak
evidence — Ayla MVP Appointment Contract §5 says so outright ("elapsed time alone is not completion
evidence")… That is exactly why the attribution field exists.*

> **Ruling implied for Care: `completed_by == "system"` is NOT an authoritative completion fact
> under OR-CARE-5.** Care must accept `specialist` / `salon` / `client` and refuse `system`, or the
> owner must explicitly rule that a 3-hour-elapsed auto-closure is good enough to start a care
> episode. This is a decision, not an implementation detail — it decides whether a no-show gets an
> aftercare message.

### Idempotency and redelivery — solid

* **Event id:** the outbox row id, carried as `envelope.event_id`.
* **Producer-side once:** `Appointment.complete()` re-checks the state machine under
  `select_for_update(of=("self",))`; a racing second closure raises `ValidationError` before any
  event is written (`completion.py:69-79`, `tasks.py:352-360`).
* **Consumer-side exactly-once:** `dispatch_envelope` (`apps/eventbus/ingest_dispatcher.py:243`)
  inserts an `IngestDedupe` row **first**, inside the same `transaction.atomic()` as the handler
  (`:255-275`). PK collision → `DUPLICATE`, handler never re-runs; handler exception → the dedupe
  insert rolls back with it. Defence-in-depth second check on
  `RemoteBookingProxy.last_synced_event_id` (`consumers/booking.py:1329-1338`).
* **Redelivery:** yes, by design — the outbox publisher retries; `bot_attempt_count` /
  `bot_delivery_status` track it; failures land in the ingest DLQ (`eventbus.cleanup_ingest_dlq`).
* **Consumer exists:** `handle_booking_completed` at `apps/eventbus/consumers/booking.py:1302`,
  registered as `("booking.completed", 1, handle_booking_completed)` at `:1565`.

The Postgres-side ERROR log noise from those dedupe PK collisions is expected and caught by the
application — not a fault.

### And yet: zero completion facts have ever existed on this pilot

```sql
-- Ayla, dev-db-1/beautygo
SELECT topic, count(*) FROM appointments_outboxevent GROUP BY topic;
 booking.created 27 | booking.confirmed 20 | booking.cancelled 17
 booking.rescheduled 2 | appointment.rescheduled 2
 -- booking.completed: ABSENT

SELECT status, completed_by, count(*) FROM appointments_appointment GROUP BY 1,2;
 cancelled | (null) | 5
 confirmed | (null) | 3
 -- completed: 0 rows, ever
```

```sql
-- bot, ayla-bot-staging-postgres-1
SELECT status, count(*) FROM booking_remotebookingproxy GROUP BY 1;
 confirmed 5 | pending_payment 1 | awaiting_payment 1 | cancelled 17
 -- completed: 0
```

**The orchestrator's hypothesis about *why* is refuted.** The `booking.auto_complete.misconfigured`
branch (`tasks.py:291-297`) is not firing, because it only fires when `BOOKING_AUTO_COMPLETE_ENABLED`
is *on* and the floor is missing. On the pilot the variable is **not set at all**
(`docker exec dev-celery_beat-1 printenv | grep AUTO_COMPLETE` → empty), so it defaults to `false`
(`djangoProject/settings/base.py:421`), `_auto_complete_window()` returns `None`, and the beat task
returns `{"ran": False}` silently. The sweep is **off by env, not broken by config.** There is no
misconfiguration to fix and no log line to look for.

The real reason is DRF-1048 (In Progress since 15.08, 8 SP, parent DRF-1299): the specialist mobile
app does not exist, so nobody could call `complete()`. DRF-1064's salon endpoint has since closed
that hole in code — the route answers on the pilot (`POST …/tenants/me/appointments/<id>/complete/`
returns 400, i.e. routed, not 404) — but **no salon operator has ever used it.**

External delivery is correctly configured for the topic:
`OUTBOX_EXTERNAL_DELIVERY_TOPICS=…,booking.completed,…` is set on `dev-celery_beat-1`.

### The trap: the only "completion" data that exists is exactly what OR-CARE-5 forbids

The bot runs its **own** completion producer, `bookings.detect_completed_bookings`
(`apps/bookings/tasks.py:393`), on beat every 30 minutes (`config/settings/base.py:1065`). It
selects `status=CONFIRMED AND completed_at IS NULL AND visit_at <= now - 30min`, rechecks
`visit_at + duration + grace <= now`, CAS-stamps `completed_at`, and emits internal
`booking.completed` with `marked_by: "system"`.

```sql
SELECT status, count(*), count(completed_at) FROM booking_bookingrequest GROUP BY status;
 cancelled | 1 | 0
 confirmed | 5 | 5     <-- all five stamped
```

Every stamp is exactly `visit_at + 90 min` (duration NULL → 60 default + 30 grace). **This is
`scheduled_at < now`, which OR-CARE-5 names by name as inadmissible.**

Two mitigating facts: those five events went to **nobody** — `DOMAIN_EVENT_SUBSCRIBERS` is unset on
the pilot and defaults to `NoopSubscriber` (`config/settings/base.py:448`), and `LoyaltySubscriber`
concedes in its own docstring that it is a production no-op. And the code is honest about its own
weakness.

But the trap is live: a Care implementer who greps for `booking.completed` finds **two** producers
across **three** buses, and the wrong choice compiles, passes tests, and silently bases medical
follow-up on a clock.

### One more reliability fact Care must price in

```sql
SELECT topic, bot_delivery_status, bot_attempt_count, count(*) FROM appointments_outboxevent GROUP BY 1,2,3;
 booking.created         | dead    | 1 | 1
 booking.created         | pending | 0 | 2
 booking.cancelled       | pending | 0 | 1
 booking.rescheduled     | pending | 0 | 2
 … sent: 62 of 68
```

Six of 68 events (~9%) never reached the bot, and **five of them were never attempted at all**
(`bot_attempt_count = 0`, one row stuck ~6 weeks) — a failure mode no retry policy fixes — plus one
`dead` on `HTTP 401 {"reason":"hmac_mismatch"}`. `booking.rescheduled` has no consumer and simply
accumulates while the paired `appointment.rescheduled` delivers 200 (DRF-1291).

**Verdict — COMPLETION TRIGGER: RESOLVED, conditionally.** The mechanism is correct and idempotent;
Care can be built on it. But it has produced zero facts to date, its human producers are unused, and
the transport currently loses ~9% of events. Care must not ship its aftercare leg to real users
before DRF-1048 closes and DRF-1291's stuck-at-zero-attempts defect is understood.
---

## 5. CareEpisode owner, schema, and CareEvent

### Owner: `ai-bot-platform`, a new `apps/care` Django app. Not Ayla, not a microservice.

The criteria decide it unambiguously, and they all point the same way:

| Criterion | Ayla (`beautygo_backend`) | Bot (`ai-bot-platform`) |
|---|---|---|
| User identity Care talks to | `Appointment.client` — an Ayla `User`, no chat channel | `identity_botuser` — the person in the conversation, 18 rows, all channel `max` |
| Completion event consumption | produces it | **consumes it** (`consumers/booking.py:1302`) with exactly-once dedupe |
| Proactive follow-up machinery | none | `apps/bookings/followups.py` + beat + `apps.channels.max.outbound.send_message` |
| Handoff to a human | none | `handoff_admintask` + `apps/handoff/notify.py` |
| Safety gates | none | `apps/orchestrator/safety/*` |
| Consent | none | `apps/consent` + `has_global_consent` |
| Conversation / LLM runtime | none | the whole orchestrator |

Care must sit where the *conversation* is, not where the *booking* is. Putting `CareEpisode` in
Ayla would require Ayla to grow a conversation, a consent store, a handoff queue and an LLM — i.e.
it would make Care a second orchestrator. Putting it in the bot costs one app.

**This does not violate booking/domain ownership (STOP #3 is not triggered).** `CareEpisode` owns no
booking state; it holds a *reference* to `appointment_id` exactly the way `RemoteBookingProxy`
already does, and the proxy is the sanctioned mirror pattern (`apps/booking/models.py:737`, PK =
the Ayla appointment UUID).

**Why a new app rather than folding into `apps/bookings`:** `apps/booking` and `apps/bookings`
already coexist as two different things (the model layer and the task layer), and `followups.py`'s
own docstring argues that sibling modules with disjoint selection predicates and idempotency keys
should stay separate. A third concern inside that pair would be the worse of the two mistakes. But
this is a naming judgement, not a boundary one — if the owner prefers `apps/bookings/care.py`,
nothing in this audit breaks.

### Schema mapping against existing models

| Contract field | Existing precedent | Note |
|---|---|---|
| `id` | `models.UUIDField(primary_key=True, default=uuid.uuid4)` — universal in this repo | — |
| user reference | FK → `identity.BotUser` | The person, not the Ayla user |
| booking/domain event reference | `appointment_id` UUID + `source_event_id` UUID **UNIQUE** | The unique constraint is the idempotency key. **Precedent exists in-repo:** `identity_memoryentry.source_event_id` is already a UNIQUE constraint on exactly this idea |
| `service` / `service_scope` | `service_scope` CharField (`laser_epilation`), resolved per §2 | Store the resolved scope, not the raw id — the id can be re-keyed by catalog sync |
| `protocol_id` / `protocol_version` | CharField + CharField | Pinned at episode creation and never re-resolved; test gate #5 |
| `state` | `TextChoices`, as `BookingRequest.Status` / `RemoteBookingProxy.Status` | — |
| `current_risk_level` | `TextChoices` NORMAL/WATCH/CAUTION/URGENT/UNKNOWN | Default **UNKNOWN**, per OR-CARE-7 fail-closed |
| `started_at` / `closed_at` | `DateTimeField` | — |
| `next_followup_at` | `DateTimeField(null=True, db_index=True)` | The scheduler's selection predicate; NULL = nothing due |
| tenant | FK → `tenancy.Tenant`, `TenantScopedManager` + `all_tenants` | See below |

**Tenant semantics — a real question with a clean answer.** The pilot has exactly two tenants:
`31800354…/global_bot` ("Global Bot Identity") and `b32a057a…/formula-tela`. The concierge runs
tenant-less (`current_tenant() is None`) — which is why `nutrition_context.py` must use
`has_global_consent` rather than the tenant-scoped `has_consent` (`nutrition_context.py:31-33`).
But the completion envelope **does** carry `tenant_id`, and the procedure was performed by a
specific salon.

Recommendation: **tenant-scope `CareEpisode` to the salon that performed the service**, using the
standard `TenantScopedManager` + `all_tenants` pair, and read it on the tenant-less conversation
path via `all_tenants` filtered by `bot_user` — the same shape `_should_send_b11_ayla` already uses
(`followups.py:198-206`). Do not put care episodes under `global_bot`: aftercare is salon liability,
and the handoff has to reach *that* salon's operator.

`AuditLog` already models the retention convention Care should copy: `is_archived` +
`archived_at` (`apps/audit/models.py:109`, `:115`) with a daily `cleanup_old_audit_logs` beat.

### CareEvent: do NOT create a model in the first slice

Point 7 asks whether a separate `CareEvent` DB model is needed immediately. It is not, and there is
a better existing option.

* `apps/audit` `AuditLog` (`models.py:56`) already carries `tenant`, `actor_id`, `action` (namespaced
  verb), `target`, `target_id` (UUID — the episode id), `payload` (JSONB), `created_at`, plus the
  archive/retention pair. `write_audit()` (`apps/audit/services.py:42`) is the one-line writer, with
  a documented PII rule ("Must not contain raw PII; callers redact upstream") and a size cap.
  `apps/bookings/followups.py` already uses it to record per-user send/skip decisions with reason
  slugs — the exact shape care triage needs.
* Bus 3 (`apps.events.services.emit`) is **not** an option: it swallows exceptions by contract.
* A dedicated model buys one thing `AuditLog` cannot give: a foreign key and query-time joins for
  episode history. That is worth a model **later**, when there is history to query. It is not worth
  one before the first episode exists.

**Recommendation:** episode history via `write_audit(action="care.<verb>", target="CareEpisode",
target_id=<episode id>, payload={risk_level, reason_code, protocol_version, …})`. Idempotency lives
on `CareEpisode.source_event_id` (UNIQUE), not on the event log. Revisit a `CareEvent` model when
the first real query against episode history is asked for.

One caveat to name: `AuditLog` is subject to the retention sweep. If care history must outlive the
audit retention window, that is an argument for the model — establish the required retention with
the owner before relying on audit rows as the record.
---

## 6. Runtime seams

### 6.0 Which path is live — this changes where every seam goes

```sql
SELECT skill_selected, count(*) FROM observability_airequestmetric GROUP BY 1;
 concierge | 40      -- 40 of 40
SELECT channel, count(*) FROM identity_botuser GROUP BY 1;
 max | 18            -- 18 of 18
```

**Every AI turn on the pilot goes through the concierge, on channel MAX.** The 19-step
`apps/orchestrator/pipeline.py` (whose step 7 is the safety pre-check and step 12 the post-check)
is the *per-tenant salon* path and carries no pilot traffic. Any Care seam designed against
`pipeline.py` would be built on a path nobody uses.

The live pre-LLM branch chain is in `apps/channels/max/handler.py`, documented at
`apps/orchestrator/concierge.py:758-762`:

```
safety → human_handoff → visit-callbacks → personal booking lookup → onboarding
       → discover-callbacks → booking-callbacks → deterministic show-masters → concierge LLM
```

There is already a visit-related branch in that chain. Care triage is a **ninth branch, placed
immediately after `safety`** — see §6.5.

### 6.1 Consultation seam — exact call site, already carries a service id

The "user intent → recommendation/service selection" call site OR-CARE asks for is:

```
apps/orchestrator/concierge.py:691   cards = discover_masters(…)
apps/orchestrator/concierge.py:818   cards = discover_masters(…)
        └── apps/marketplace/discovery.py:559  discover_masters(*, city, specialization, limit, resolve_service)
              └── returns list[MasterCard]  (apps/marketplace/dto.py:20)
                    .service_id : UUID | None   <-- the catalog mirror row id
                    .service_name : str
```

`resolve_service=True` (DRF-962) stamps each card with **the one** service that matched the query,
when unambiguous. The DTO docstring is explicit that it is `None` "when the query had no service
filter or several of the master's services matched — auto-picking one of several would silently
book a service the user never chose."

Minimal insertion, no new orchestrator:

```
MasterCard.service_id
  → CatalogService.ayla_service_id → raw->>'category'   (or Ayla SalonService.category_id)
  → CareServiceScope lookup  →  service_scope
  → load ACTIVE ServiceCareProtocol for that scope (pinned version)
  → protocol.consultation → protocol-critical clarification via the EXISTING ask_clarification tool
  → protocol.contraindications → safety restriction / no-recommend
  → otherwise: render cards unchanged
```

Two things this seam gives for free and one it does not:

* The `ask_clarification` tool already exists and the concierge prompt already instructs the model
  to prefer it over free text (`concierge.py:~460`), so protocol-critical questions get a tap-answer
  UI without new UX.
* Zero-result turns now reach the model rather than dying deterministically (DRF-1283), so the
  consultation seam sees more traffic than it would have a week ago.
* **It does not fire for the laser bundles when the query is ambiguous.** `service_id` is `None`
  whenever several of a master's services matched — and a query like "лазерная эпиляция" matches
  many of the 17 laser cards at once. For laser specifically the *category* is knowable even when
  the *card* is not, so the scope resolver should accept "all matched services share one category"
  as a resolution, not just "exactly one service matched". That is a change inside
  `_matched_services`, not a new mechanism.

### 6.2 Aftercare trigger — one seam, inside the existing handler

```
Ayla close_booking()                                     completion.py:69
  → OutboxEvent booking.completed                        completion.py:29
  → publish_outbox_events_to_bot (beat 30s)              appointments/tasks.py
  → POST /api/v1/internal/events/ingest  (HMAC)
  → dispatch_envelope                                    ingest_dispatcher.py:243
      IngestDedupe.objects.create(event_id=…)            :255   <-- exactly-once boundary
      handle_booking_completed(envelope)                 consumers/booking.py:1302
        ├── proxy.status = COMPLETED, last_synced_event_id = event_id   :1339-1343
        └── [NEW]  maybe_open_care_episode(envelope)     <-- the seam
```

The seam must be **inside** `handle_booking_completed`, in the same transaction as the dedupe row.
`_REGISTRY.get(key)` permits one handler per `(name, version)` (`ingest_dispatcher.py:240`), so a
second registered consumer is not possible — and it is not wanted: being inside the dedupe
transaction is what makes "duplicate event → one CareEpisode" a property of the existing code
rather than something Care has to re-implement.

What `maybe_open_care_episode` must do, in order:

1. **Refuse `data["completed_by"] == "system"`** unless the owner rules otherwise (§4).
2. Resolve the service: the envelope does **not** carry one. Join
   `appointment_id → RemoteBookingProxy.service_id → CatalogService.ayla_service_id → raw->>'category'`.
   The proxy row exists and was just updated three lines above; `service_id` is nullable there by
   design ("update events don't repeat the service reference; the row keeps the value set at
   creation time" — `apps/booking/models.py:859`).
3. No scope → do nothing, log, exit. Unknown scope must never produce invented advice (test gate #2).
4. Load the ACTIVE protocol; pin `protocol_id` + `protocol_version` on the episode.
5. `CareEpisode.objects.create(source_event_id=envelope.event_id, …)` — UNIQUE, belt and braces.
6. Set `next_followup_at` from `protocol.follow_up`, not from a hardcoded offset.

**A cheaper alternative worth putting to the owner:** add an OPTIONAL `service_id` (or
`service_scope`) field to the `booking.completed` payload on the Ayla side. `completion.py:50-56`
quotes event-contract §4.1 to establish that adding an optional field consumers ignore by default
is non-breaking and does not bump `event_version`. That would make the Care consumer self-contained
and immune to DRF-1103. It is a three-line change in `emit_booking_completed`. **This is the single
highest-leverage change in the whole plan** and it belongs to the Ayla repo, not the bot.

### 6.3 Aftercare due → outbound

`next_followup_at` needs a sweeper. The existing shape to copy is `apps/bookings/followups.py`:
a `@shared_task` on beat, batched with a `BATCH_LIMIT`, per-user try/except, idempotency key
persisted only after a successful send, outbound through `apps.channels.max.outbound.send_message`,
and a `write_audit` row per decision with a reason slug. Register it in `CELERY_BEAT_SCHEDULE`
alongside `bookings.send_post_visit_followups` (`config/settings/base.py:1146`).

Selection predicate: `state=ACTIVE AND next_followup_at <= now AND closed_at IS NULL`.
Idempotency: advance `next_followup_at` (or NULL it) only after the send returns. The at-most-twice
semantics `followups.py` chose deliberately are the right default here too — a duplicated aftercare
check-in is mild; a silently dropped one is not.

### 6.4 Proactive follow-up and the consent gate — STOP #4 not triggered, but do not copy the incumbent

**The orchestrator's claim that the schedule contains "17 tasks, none proactive" is wrong.** The bot
beat schedule contains at least three proactive outbound tasks today
(`config/settings/base.py:1119`, `:1132`, `:1146`):

```
bookings.send_due_reminders          crontab(minute="*/15")
bookings.escalate_stale_reminders    crontab(minute="0")
bookings.send_post_visit_followups   crontab(hour="16", minute="0")   # 19:00 МСК
```

The third is a **post-visit proactive message that is shipped and running**. It is the closest
existing analogue to Care aftercare, and it is exactly the pattern Care must *not* inherit.

What it gates on (`_should_send_b11`, `followups.py:216-250`):
1. `BotUser.proactive_messages_opt_out` → skip. **The field works and is enforced** — refuting the
   "not verified" note in the brief. All 18 pilot users have it `false`.
2. `Conversation.consecutive_payment_failures >= PAYMENT_FAILED_HANDOFF_THRESHOLD` → skip.
3. Terminal booking status → skip.
4. On the Ayla path, `_should_send_b11_ayla` (`:176-213`) consults the mirror and blocks only on
   `cancelled` / `no_show` **plus** a missing mirror row.

What it does **not** gate on: **consent.** The module docstring says so outright — *"Inspection of
`apps.identity.models.BotUser` shows no explicit consent boolean — consent is recorded via
`apps.consent.models` rows, not on the BotUser itself. Per the spec rule 'do not invent one', we
send to all clients with a non-empty `chat_id`."*

And `_should_send_b11_ayla`'s docstring contains an independent, code-side confirmation of §4:
*"It does NOT require `completed` — the pilot mirror frequently stays `confirmed` after the visit
because Ayla does not always emit `booking.completed`, and demanding it would silence the follow-up
entirely."*

**The correct gate already exists and is already used for exactly this class of data.**
`apps/orchestrator/nutrition_context.py:187` requires **both**:

```python
has_global_consent(bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value)
and has_global_consent(bot_user, ConsentRecord.ConsentType.HEALTH.value)
```

`has_global_consent` is at `apps/consent/services.py:403`; `ConsentType.HEALTH` at
`apps/consent/models.py:70`. The module states the rationale: nutrition is health data, special
category under 152-ФЗ ст. 10, **not** the green zone that `memory_block` rides. Post-procedure
symptom data is at least as sensitive.

**Consent state on the pilot — the brief's claim is half right, and the half it gets wrong matters.**

```sql
SELECT consent_type, granted, source, captured_at, withdrawn_at FROM consent_consentrecord ORDER BY captured_at DESC;
 health        | t | drf1284:place      | 2026-08-23 06:42:04 | 2026-08-23 06:42:15
 personal_data | t | drf1284:place      | 2026-08-23 06:42:04 | 2026-08-23 06:42:15
 health        | t | drf1284:hdr        | 2026-08-23 06:40:46 | 2026-08-23 06:41:09
 …  (5 health rows total, all sourced drf1284:*, all withdrawn within ~20 seconds)
 personal_data | t | global_onboarding:welcome_s2 | 2026-08-20 18:41:12 | (null)   <-- the ONLY live consent
```

So: **no live `HEALTH` consent exists** (the brief is right), **but a working grant/withdraw path
for it shipped today** and left five proven round-trips in the data (the brief is wrong that it is
"nowhere granted"). The mechanism is not hypothetical. DRF-1294 is about issuing it to real users,
not about building it.

**DRF-1285 / PR #1236 (open, `feat/drf1285-nutrition-proactive`, MERGEABLE, one red check) does not
bypass consent.** Its own evidence section: *"11 candidates, 0 would receive a message. Every one
is blocked at the consent gate (`no_consent` or `no_food_consent`)."* It ships two closed switches
(`NUTRITION_PROACTIVE_ENABLED` default False, `NUTRITION_PROACTIVE_DRY_RUN` default True), quiet
hours, per-user preferences defaulting off, and — worth noting — it is the **first working setter**
for `proactive_messages_opt_out`, which until now was enforced but unsettable (the Mini App toggle
is backed by `guardProd()` stubs and `GET/POST /api/v1/me/proactive_opt_out` does not exist).

**Conclusion for OR-CARE-6.** A safe outbound gate exists; Care must compose it, not re-derive it:

```
CareEpisode.next_followup_at due
  → has_global_consent(PERSONAL_DATA) AND has_global_consent(HEALTH)   [fail-closed]
  → NOT BotUser.proactive_messages_opt_out
  → quiet hours (copy the DRF-1285 shape; do not invent a second one)
  → protocol.follow_up rule says this episode is due
  → apps.channels.max.outbound.send_message
  → write_audit("care.followup.sent" | "care.followup.skipped", payload={reason})
```

Two coordination notes: DRF-1285 and Care will both want a quiet-hours helper and both want a
proactive-send audit vocabulary — **land DRF-1285 first and reuse what it leaves**, rather than
building a parallel one. And the existing `send_post_visit_followups` and a Care aftercare message
would both fire the day after the same visit; whoever implements Care must decide whether they
merge into one message or whether the review nudge yields to the care check-in. That is a product
decision and it is not currently anybody's ticket.
### 6.5 Symptom triage seam — and a safety gap that outranks it

**There are four live inbound paths, not two.** The brief (and the module docstrings) say "both
paths", meaning per-tenant and global — but **both of those are client paths**
(`apps/channels/max/handler.py:63-64`, parity test `apps/channels/tests/test_handler_safety_parity.py:3-5`).
The full picture:

| # | Path | Entry | Inbound safety | Outbound safety |
|---|---|---|---|---|
| 1 | MAX per-tenant **client** | `handler.py:1003 _handle_max_event_inner` | ✅ `handler.py:1055` | ❌ none |
| 2 | MAX global **client** (concierge — the pilot's live path) | `handler.py:582 _handle_global_max_event_inner` | ✅ `handler.py:689` | ❌ none |
| 3 | MAX **salon/master** | `salon_handler.py:129` → `master_api/services/assistant.py:169` | ✅ `assistant.py:191` | ✅ `assistant.py:235/244/247/268/275` |
| 4 | **Telegram** per-tenant client | `telegram/handler.py:99 handle_inbound` | ❌ **NONE** | ❌ none |

> **CANONICAL CONFLICT / PRE-EXISTING SAFETY GAP — path 4 has no inbound safety gate at all.**
> `apps/channels/telegram/handler.py` never imports `evaluate_inbound`, `gate`, or `pre_check`. It
> goes from `record_message(role="user")` (`:171-177`) straight to
> `orchestrate_turn(TurnContext(surface=SURFACE_PER_TENANT, …))` (`:184-196`). It is live and
> publicly routed: `config/urls.py:64-65` → `telegram/urls.py webhook/<slug:tenant_slug>/` →
> `telegram/webhook.py:70`, which calls `handle_inbound` at `:148`. A crisis phrase arriving over
> Telegram receives no crisis reply. The only thing that catches anything is the regex
> `HealthScreeningSkill`, whose vocabulary is narrower and whose redirect is to a *massage*, not to
> the hotline.
>
> **This is not Care's defect and Care must not paper over it.** But putting symptom triage on top
> of an ungated path would place health handling in front of a missing crisis net. Either fix path 4
> first, or scope the Care vertical slice to MAX explicitly. This is a separate ticket and it is
> more urgent than Care.

**The open question about `outbound.py` is answered: it does NOT cover the client path.**
`evaluate_outbound` (`apps/orchestrator/safety/outbound.py:96`) has exactly one production call-site
file — `apps/master_api/services/assistant.py` (lines 235, 244, 247, 268, 275, applied via `_finish`
at `:278-289`), reachable only from `salon_handler.py:459`. The merge that landed it
(`fc308ef0`, "DRF-1061 step 1: master's assistant + DRF-1210 outbound check + DRF-1276 privacy
cascade") does not touch `apps/channels/max/handler.py` at all. Client sends at `handler.py:1223`
(per-tenant), `handler.py:939-943` (global) and the Telegram outbound have no check, and there is no
filter in the send layer either. **Outbound safety is a salon-path-only feature today.**

**`gate.py` short-circuit CONFIRMED.** Founder-signed `CRISIS_REPLY_TEXT` (`gate.py:50-58`, hotline
8-800-2000-122) and `BLOCK_REPLY_TEXT` (`:62-65`); control flow at `:98-116` returns
`SafetyGateOutcome(allowed=False, reply_text=…)` and both callers replace the entire reply
(`handler.py:1056-1079` returns; `handler.py:690-695` builds `DiscoveryReply(text=safety.reply_text)`).
Nothing is appended. Do not extend these texts.

Two ordering quirks Care must know:
* per-tenant, the crisis short-circuit is **skipped** when `conversation.state == HUMAN_HANDOFF`
  (`handler.py:1056`, deliberate barge-guard);
* global, `global_handoff_muted(...)` returns at `:637-646` **before** the safety gate at `:689`.

**The seam.** Insert Care triage immediately after global safety and before the brain:
* path 1 (per-tenant): between `handler.py:1079` (the safety `return`) and `:1095` (photo download);
* path 2 (global/concierge): a new `elif` between `evaluate_inbound` at `:689` and
  `matches_human_handoff_request` at `:696`.

That yields exactly the order OR-CARE-7 asks for:

```
user message
  → global safety (unchanged, short-circuit intact)
  → relevant CareEpisode?           [new — a cheap indexed lookup on bot_user + state=ACTIVE]
  → structured symptom extraction   [existing tool-calling layer, §6.7]
  → deterministic protocol evaluation
  → allowed action
  → LLM rendering from allowed facts only
  → handoff if required
```

### 6.6 Handoff — no schema change needed, and the right enum is already sitting unused

`apps/handoff/models.py:54-70`:

```python
class TaskType(models.TextChoices):
    HANDOFF = "handoff"; COMPLAINT = "complaint"
    MEDICAL_RED_FLAG = "medical_red_flag"; MANUAL = "manual"
class Priority(models.TextChoices):
    LOW; NORMAL; HIGH; URGENT
class Status(models.TextChoices):
    OPEN; IN_PROGRESS; RESOLVED; CANCELLED
```

`reason` is **not** an enum — free-text `TextField` (`:132-136`).

**`MEDICAL_RED_FLAG` is declared, migrated, tested and created by no production code.** Its gloss at
`models.py:9` is *"health-screening contraindication detected"*. `Priority.URGENT` is likewise never
passed by any caller, despite `models.py:104-105` claiming *"MEDICAL_RED_FLAG ships URGENT"*. Care
triage is what these were reserved for. **Use them; do not invent a new `task_type` string** —
`create_admin_task` does no `full_clean()` and there is no DB check constraint (`services.py:194`),
so an unknown value persists silently, and `notify.py:95` then raises `ValueError` on
`AdminTask.TaskType(task.task_type).label`, swallowed by the blanket `except` at `:170-171` — the
**operator notification disappears with only a log line.**

One deliberate choice to make, not stumble into: the global mute query filters
`task_type=AdminTask.TaskType.HANDOFF` (`apps/orchestrator/handoff.py:645`). A care task typed
`HANDOFF` **mutes the user's bot**; typed `MEDICAL_RED_FLAG` it does **not**. For an URGENT triage
result, muting the bot and putting a human in the loop is probably right; for CAUTION it probably is
not. That is a protocol decision, not a plumbing one.

**Structured context: there is no slot, and `reason` is dangerous.** `AdminTask.transcript_snapshot`
is the only JSONField (`models.py:107-114`), but `create_admin_task` overwrites it unconditionally
from `package_transcript` and exposes no parameter (`services.py:159-165`, `:191`, `:194-202`); it is
`readonly_fields` in the admin (`admin.py:56`). With zero code changes, `reason` is the only slot —
and `reason` is echoed verbatim (truncated to 200 chars) into the operator's MAX message
(`notify.py:62`, `:96`). **Symptom text in `reason` lands permanently in an external messenger,
outside 152-ФЗ deletion.**

Recommended: pass a namespaced structured block into `transcript_snapshot` via a new optional kwarg
on `create_admin_task` — a **services-layer change with no migration** (JSONField shape is
unconstrained) — carrying `{care_episode_id, service_scope, risk_level, reason_code,
protocol_id, protocol_version}`. Put a **reason code**, never a symptom, in `reason`.

Two operational facts:
* `release_conversation_to_bot` refuses to unmute while **any other** OPEN/IN_PROGRESS task exists on
  the conversation (`services.py:270-285`). A care task will hold the dialog muted until it is closed.
* Care must never `.save()` a task directly — the admin routes closes through the services precisely
  because *"a bare field save of `status` leaves the conversation in HUMAN_HANDOFF forever"*
  (`admin.py:105-107`).
* `notify.py` is MAX-only, synchronous, 5s timeout, no retries, fired via `transaction.on_commit`
  (`services.py:236-238`), and **off unless `HANDOFF_NOTIFY_MAX_CHAT_IDS` is set** — it is set on the
  pilot. PII contract at `notify.py:29-33`: no transcript, no phone.

The closest existing analogue to copy is `apps/booking/services/feedback.py:157-173` — a post-visit
client signal that escalates deterministically (`rating <= 3` → `COMPLAINT`, `HIGH` if `<= 2`),
including its degradation guard.

**No second handoff subsystem is needed. Do not build one.**

### 6.7 Deterministic evaluator and the LLM boundary

**The invariant `LLM renderer != policy evaluator` already exists in this codebase, twice.**

*Evaluator precedent* — `HealthScreeningSkill` (`apps/skills/health_screening/skill.py:72-106`) is a
pure-regex, zero-LLM, zero-network skill that classifies into a three-value enum
(`classifier.py:33-36`: `PainSignal.NONE/SOFT/RED_FLAG`, `classify()` at `:110`, red flags take
priority at `:124-128`) and returns canned text. It is registered third, deliberately ahead of every
service-suggesting skill (`apps/skills/apps.py:17-22`), and the registry docstring says the ordering
is load-bearing: *"the first matches() returning True wins"* (`registry.py:116-131`).

A `CareRuntimeSkill` whose `matches()` is "an open CareEpisode exists for this bot_user" is a
**structurally identical insertion** and needs no new dispatch machinery — for paths 1 and 4. Path 2
(the concierge, which carries all pilot traffic) does not use the skill registry, so it needs the
second insertion point described in §6.5.

*Renderer precedent* — the "render from these facts only" pattern exists in both surfaces:
* `apps/orchestrator/concierge.py:503 _build_tool_result_message`, instruction at `:553-558`:
  *«Ответь клиенту словами, опираясь ТОЛЬКО на эти данные… Ничего не выдумывай.»*
* `apps/master_api/services/assistant.py:254-263`: *«Ответь мастеру по этим данным. Ничего не
  добавляй от себя.»*

Reuse this shape verbatim, with `allowed_response_facts` as the payload. It is also the natural place
to enforce the §11 grammatical-subject constraint.

*Structured extraction* — the mechanism is **OpenAI-shaped tool calling, and nothing else**.
`git grep pydantic|BaseModel` over `origin/dev` `*.py` → **zero hits**; there is no pydantic in the
repo, and `response_format={"type":"json_object"}` appears only on the dead intent path
(`intent_router.py:360`). The contract is `apps/llm/protocol.py:197-205`
`complete(messages, *, model, temperature, tools, max_tokens) -> CompletionResult`, returning
`ToolCall(id, name, arguments: dict)` with **arguments already parsed to a dict by the provider**
(`:65-80`). Symptom extraction should be a tool spec, not a JSON-mode prompt.

Proposed minimal runtime API — no new framework, one pure function:

```python
def evaluate_care(protocol: ServiceCareProtocol,
                  episode: CareEpisode,
                  observation: CareObservation) -> CareDecision: ...

@dataclass(frozen=True)
class CareDecision:
    risk_level: RiskLevel          # NORMAL|WATCH|CAUTION|URGENT|UNKNOWN, default UNKNOWN
    action: CareAction
    reason_code: str               # stable slug; this is what goes in AdminTask.reason
    followup: datetime | None
    handoff_required: bool
    allowed_response_facts: tuple[str, ...]
```

Pure, no I/O, no LLM, no DB — so it is trivially unit-testable against the ten risk-level test gates.
Frozen dataclass matches the repo convention (`apps/replay/fixtures/schema.py:30`).

*LLM failure* — safe fallbacks already exist on every path and Care inherits them:
`concierge.py:630-661` (`except Exception` → `OUTCOME_ERROR` → deterministic card render if tool data
is in hand, else `get_fallback("ru")`), `apps/orchestrator/llm/templates.py:15/:23`, booking skill
`skill.py:604-615` → handoff, master assistant `assistant.py:222-224`. Pass budget capped at
`CONCIERGE_MAX_LLM_PASSES` (`concierge.py:490-500`, default 2, clamped). **Test gate #12 is
satisfiable by construction: if the evaluator has already decided, an LLM failure costs the phrasing,
not the decision.**

### 6.8 Memory boundary — the prohibition is convention, not enforcement

**The brief's "five approved memory keys" do not exist.** There is no key allowlist anywhere.
* The only key ever written in production is `diet` — the entire extractor is three regexes at
  `apps/persona/memory_extract.py:47-62` producing `{"key": "diet", "value": "vegan"|"vegetarian"}`.
* `apps/identity/services/memory_key_policy.py:50-64` is a **cardinality registry**, not an allowlist:
  `_KEY_CARDINALITY = {"diet": CARDINALITY_SINGLE}` and `key_cardinality()` returns
  `.get(key, CARDINALITY_SINGLE)` — unknown keys are **accepted**.
* The list the brief is thinking of is `apps/orchestrator/memory_ask.py:283-293` — **seven** parsers
  (`preferred_time_slots`, `price_range_max`, `workplace_district`, `home_district`, `busy_days`,
  `min_rating_preference`, `diet_type`), plus `favorite_masters` deliberately excluded (`:22-28`).
  It **is** a closed set — but it gates *asking*, and it PATCHes a **remote Ayla `personal-context`
  API** (`:184-187`). It never touches `MemoryEntry`.

Confirmed as stated: five CHECK constraints, all validated (three in
`migrations/0007_user_personal_context.py:75-108` — *not* the file the brief listed — and two in
`0019_memoryentry_lifecycle_constraints.py:45-46`), including
`memory_entry_yellow_red_requires_consent`. Note it does not forbid yellow/red rows; any non-NULL
`consent_at` satisfies it. All five are Postgres-only — **a SQLite dev DB has none of them.**
`provenance` has the two documented values (`models.py:725-730`) plus NULL as a de-facto third
(flagged in `0019:13-16`); `user_confirmed_inference` is never written by any code.

Read path: `memory_reader.py:53/:84/:109`, `memory_key_policy.py:113 read_current_view` (the wrapper
live consumers use), red-only via `red_zone_reader.py:76`. `memory_reader.py:6-16`: *"It NEVER
touches yellow or red."* **There is no module that declares itself "the memory boundary"** — the
nearest self-declarations are `memory_writer.py:1` ("the single sanctioned write path") and
`red_zone_reader.py:1`. The green read path claims no exclusivity.

Write path: exactly one production `MemoryEntry.objects.create()` — `memory_writer.py:197-209` inside
`write_entry(...)`. One live caller: `apps/orchestrator/memory/personal_context.py:91-101`
`record_explicit_green_facts`, from `handler.py:954`. Gates: consent
(`can_store_green_memory` → `has_global_consent(PERSONAL_DATA)`), forget-all tombstone, and the
**minor-protection hard block** (`memory_writer.py:159-174`, `_check_minor_protection` raises
unconditionally at `:74-78`, so *"yellow/red writes are silently dropped 100% of the time"*).
**Green writes bypass that entirely, and there is no key check and no provenance check on the write
path.** `write_entry` accepts any `content`, any `kind`, any zone string.

> **Consequence for OR-CARE point 15.** "Care writes nothing to MemoryEntry" currently rests on
> Care not importing four symbols. Nothing mechanically prevents it. If the prohibition must be a
> guarantee rather than a convention, extend `tools/lint/import_boundaries.py` or
> `tools/lint/red_zone_guard.py` — **both patterns already exist in the repo** and this is the
> cheapest way to convert the ruling into an enforced invariant. Recommend doing it in the same
> slice, because a symptom is exactly the kind of fact a future contributor would reasonably think
> belongs in memory.

Also note `MemoryEntry.KIND_CHOICES` already contains bare labels `"symptom"` and
`"contraindication"` (`models.py:665-673`) with **no runtime meaning** — no code branches on them.
They are an attractive nuisance for exactly this mistake.

**DRF-1290's separate allergy perimeter does not exist in code.** `git grep "DRF-1290" origin/dev`
returns **zero hits, all file types**. What exists instead: the unused `contraindication` kind label;
`UserPreferences.allergies`, a *plaintext, tenant-scoped, customer-editable* TextField
(`apps/identity/models.py:334-339`, PATCH allowlist `services/profile.py:112-119`, redacted on export
at `privacy.py:304`); the outbound regex blocking «у вас аллергия» (`outbound.py:47-49`); and an
unimplemented policy doc (`docs/design/policies/ayla-memory-and-personalization.md:183`) which
classifies allergy as 🟢 **green** and specifies a key `service_allergies` that appears nowhere else.
**That doc's rule and DRF-1290's ruling contradict each other.** Care must not rely on a perimeter
that is not there.
---

## 7. Canonical conflicts

Only real ones. Each is stated, not fixed.

### CONFLICT 1 — `booking.completed` is two different events with one name

**Severity: high. Owner decision not required; implementer discipline required.**

Topic `booking.completed` exists on the cross-service ingest bus with payload
`{appointment_id, client_id, specialist_id, completed_at, completed_by}` (`completion.py:44-58`,
Ayla), and **also** on the bot's internal domain bus with payload
`{booking_id, completed_at, marked_by}` (`apps/bookings/tasks.py:481-491`; required-field set
declared at `apps/eventbus/vocabulary.py:67`). The second is produced from
`visit_at + duration + grace < now` — the inference OR-CARE-5 forbids by name.

Both names appear in `_KNOWN_NAMES` (`ingest_dispatcher.py:170-190`) and in
`apps/eventbus/vocabulary.py`. A Care consumer written against the wrong one compiles, passes tests,
and bases medical follow-up on a clock. **This is the single most likely way to get Care wrong.**

Not proposing a rename here — that is a contract change with its own blast radius. Proposing that
the Care ticket name the correct bus and payload explicitly, and that a test assert the Care consumer
never sees a `marked_by`-shaped payload.

### CONFLICT 2 — the Telegram client path has no safety gate

**Severity: high. Pre-existing, not caused by Care. Separate ticket.**

Detail in §6.5. `apps/channels/telegram/handler.py` never calls `evaluate_inbound` / `gate` /
`pre_check`; it goes from `record_message` (`:171-177`) to `orchestrate_turn` (`:184-196`). Live and
publicly routed (`config/urls.py:64-65` → `telegram/webhook.py:148`).

This is a conflict with Safety/Governance as a canonical boundary, not with Care. It becomes a Care
blocker only if the laser vertical slice is scoped to include Telegram. **Recommendation: scope the
slice to MAX and file the Telegram gap separately, at a priority above Care.**

### CONFLICT 3 — outbound safety is a salon-path-only feature

**Severity: medium. Owner should know the invariant they think they have, they do not have.**

`evaluate_outbound` (`outbound.py:96`) guards only `apps/master_api/services/assistant.py`. Every
client-facing send is unguarded (`handler.py:1223`, `handler.py:939-943`, Telegram outbound), and
there is no filter in the send layer. The `fc308ef0` merge does not touch `handler.py`.

Consequence for Care both ways: Care cannot rely on an outbound net that is not there; and when the
net is extended to client paths, its `_MEDICAL` patterns (`outbound.py:47-54`, incl. the block on
asserting «у вас аллергия») will fire on legitimate Care copy. See §11.

### CONFLICT 4 — the memory prohibition is convention, not enforcement

**Severity: medium. Cheaply fixable inside the Care slice.**

Detail in §6.8. There is no key allowlist; the only production key is `diet`; `write_entry` accepts
any `content`/`kind`/zone; and `MemoryEntry.KIND_CHOICES` already contains inert `"symptom"` and
`"contraindication"` labels. OR-CARE point 15's prohibition currently holds by nobody importing four
symbols. The repo already has two lint-boundary patterns (`tools/lint/import_boundaries.py`,
`tools/lint/red_zone_guard.py`) — use one.

### CONFLICT 5 — DRF-1290's allergy perimeter does not exist, and a canonical doc contradicts it

**Severity: medium. Owner decision required, but not by Care.**

`git grep "DRF-1290" origin/dev` → zero hits. Meanwhile
`docs/design/policies/ayla-memory-and-personalization.md:183` classifies an allergy as 🟢 **green**
and `:357-365` specifies a key `service_allergies` that exists nowhere in code. Under that doc's rule
a lacquer allergy would be writable to green memory today. Under DRF-1290 it must not be.
**Two canonical statements, opposite answers, neither implemented.**

Care touches this because contraindication screening is part of `consultation`. Care must **not**
resolve it — it must refuse to write anything allergy-shaped anywhere and let the owner settle the
doc-versus-ruling conflict.

### CONFLICT 6 — reassurance vs DRF-1295

**Severity: high. Owner decision required. Full treatment in §11.**

Care's `expected_reactions` / NORMAL branch is functionally the negation of a health concern, which
the 23.08 owner ruling forbids as firmly as assertion («ни утверждать, ни опровергать»). This is not
a code conflict; it is a boundary conflict, and it is the one that decides whether Care's most
common output is sanctioned.

### Considered and NOT a conflict — `promptreg`'s dormant `AFTERCARE` category

`apps/promptreg/models.py:208` already declares `DisclaimerLibrary.Category.AFTERCARE`, with
`RiskLevel` low/medium/high (`:212-215`) and `get_disclaimer(tenant, category, risk_level)`
(`registry.py:104`) — an operator-owned, versioned, LLM-cannot-choose-it text store, fully built and
entirely unused. It is genuinely tempting for Care's disclaimer strings.

**Do not use it.** OR-CARE-1 says one `ServiceCareProtocol` covers consultation, preparation,
expected reactions, aftercare, triage, red flags and follow-up, and that *independent runtime policy
stores are prohibited*. Splitting the aftercare text into promptreg while the rules live in
ayla-knowledge creates exactly the second policy store the ruling forbids — and worse, the two would
version independently (promptreg cannot pin a version at all, §3). The disclaimer text belongs
**inside** the protocol document, next to the rule that selects it.

Worth telling the owner that the shelf exists, because it is a reasonable question to ask and the
answer should be on the record rather than rediscovered.

### Explicitly NOT conflicts

* **CareEpisode owner vs booking domain ownership (STOP #3).** Care holds a *reference* to
  `appointment_id`, owning no booking state — the same pattern `RemoteBookingProxy` already
  establishes (`apps/booking/models.py:737`). No conflict.
* **Protocol storage vs knowledge governance (STOP #6).** Registering a new `document_type_rules`
  entry is the sanctioned extension mechanism (`schema.yaml:386`, precedent at `:6/:12/:19/:22`),
  and `Contracts/*.yaml` is an existing pattern in that repo. No conflict — though note the existing
  registries there are unvalidated (§3, gap 2).
* **New microservice (STOP #8).** Not needed. One Django app in an existing repo.
* **Large booking/catalog rewrite (STOP #9).** Not needed. The scope mapping is one registry row over
  an existing category UUID (§2); the one recommended upstream change is an *optional* field on an
  existing event payload, explicitly non-breaking under event-contract §4.1.
---

## 8. Research-doc normalization

**This is the section that changes the verdict. There are no laser research documents.**

### The "four existing research docs" are real, and none of them is about laser

They are four **Google Docs**, seeded into the `global_kb` tenant. Named in the seed commit
`fcd1af1` ("feat(kb): seed global_kb corpus + golden-query smoke tests"):

```
doc 1 services            64 rows  doc_type=service
doc 2 contraindications   36 rows  doc_type=contraindication
doc 3 aftercare protocol  51 rows  doc_type=help_article
doc 4 symptom triage tree 36 rows  doc_type=contraindication
                         187 rows total
```

Corroborated at `ai-bot-platform/docs/design/legacy/2026-05-18-shared-corpus-topic-backlog.md:18`
(*«Итого: 4 дока, ~150 KbDocument rows, 204 ChromaDB chunks»*) and
`docs/operations/global-kb-tenant.md:150-153`.

**Their bodies are not retrievable anywhere I can reach, including the pilot:**

```sql
-- ayla-bot-staging-postgres-1 / ai_bot_platform, 2026-08-23
SELECT count(*) FROM kb_kbdocument;   ->  0
```

The table exists (12 columns) and is **empty on the pilot**. Local `db.sqlite3` and `.chroma`:
0 rows, in the main checkout and in all 14 sibling worktrees. The four Google Doc IDs were never
committed — `seed_kb_from_gdocs.py` takes `--doc-id` as a CLI argument, and the only ID in the
entire git history is a worked example in `docs/operations/google-docs-public-link.md:32`.

**So the corpus OR-CARE-3 assumes as source material is, from here, unreadable — and on the pilot,
absent.**

### The one substantive care document that IS on disk contains zero laser content

`C:/Users/user/Downloads/Contraindication_Matrix_AI_Bot.pdf` — 203,923 bytes, 2026-05-13, 8 pages
(byte-identical duplicate alongside it). This is the upstream research report that produced the doc
package, and it is itself a four-document package — *«Contraindication Matrix, Symptom Triage Tree,
Pre-Prep Protocol, Aftercare Protocol»* (p.1). Note this is a **different** four from the seeded
four, so two sets are in circulation under the same phrase.

Keyword counts over the full extracted text:

```
лазер 0   эпиляц 0   депиляц 0   фолликул 0
волос 0   фототип 0  загар 0     бритв 0
```

Its scope is declared on page 1: *«Так как точный каталог услуг пока unspecified, ниже берется
стандартный MVP-набор: ручной массаж тела, LPG/endermologie для тела и RF-лифтинг лица/тела.»*
The research was done **before the catalog existed**, against a guessed service set that does not
include the service OR-CARE-3 selected.

Everything else was searched and is empty of laser care material: `D:/Проекты/Ayla/` (only 4
incidental mentions), `ayla-knowledge` across **all five branches** (`git grep -il` for
`лазерн|эпиляц|депиляц|александрит|фолликулит|постпроцедурн|уход после` → 0 hits on every branch),
`docs/formula-tela-*.md` (catalog only), the `Формула тела` folder (30+ laser JPEGs, a price
spreadsheet, SEO clusters — the laser landing page is logged «Не начата»), and
`djangoproject/services/seeds/canonical_catalog_2026-07.json` (has laser codes 7.1.x, but
`"contraindication"` appears **0 times** in the file and laser rows carry
`"requires_health_check": "false"`).

### Coverage grid — graded for a `laser_epilation` protocol

| Section | Verdict | Basis |
|---|---|---|
| `consultation` | **MISSING_EVIDENCE** | A 16-slot screening frame exists and is excellent (p.5: `service_requested, treatment_area, age_legal_status, red_flags_now, pregnancy_lactation, implants_devices, skin_status, chronic_diseases, oncology_history, clotting_meds, recent_interventions, allergies, wellness_medical_limits, instruction_capacity, pd_consent`) — but every slot is scoped «Массаж / LPG / RF». **Not one laser-specific question exists**: no Fitzpatrick phototype, no tan/sun exposure, no photosensitizing medication, no isotretinoin, no herpes history, no tattoos/moles in zone, no recent waxing, no hormonal disorder. |
| `preparation` | **MISSING_EVIDENCE** | Pre-Prep templates exist for massage/LPG/RF/Food Scanner/Water Tracker (p.6). No laser row. The document forbids inventing one: *«Без device-specific приложения Pre-Prep остается юридически слабым.»* |
| `expected_reactions` | **MISSING_EVIDENCE** | The table has a «Что считать ожидаемой реакцией» column; the RF row is the nearest thermal analogue — *«Покраснение, умеренный отек; для микроигольчатого RF — точечные следы/чувствительность»* — transferable **in shape only**, and with **no duration attached to any expected reaction**. |
| `aftercare` | **MISSING_EVIDENCE** | Same table, same absence. Independently logged as a known gap in `shared-corpus-topic-backlog.md:29` («Что делать после X?» ⚠️ Средне) and `:40` («Как готовиться к X?» ❌ Слабо, nothing covering it). |
| `triage` | **MISSING_EVIDENCE** | A real deterministic tree exists (p.4) with a 4-level ladder that maps cleanly onto NORMAL/WATCH/CAUTION/URGENT (p.2: Н «стандартный сценарий» / У «до-уточняет; при сомнении — админ» / В «не делает auto-booking; передает врачу» / А «не записывает; при red flags — срочная медпомощь»). **But the post-procedure branch — the only one Care needs — is a stub**: `O[После процедуры] → P{Симптомы выше нормы?}`, and «выше нормы» is never defined, for any procedure. |
| `red_flags` | **SUPPORTED** for the procedure-independent URGENT set; **MISSING_EVIDENCE** for anything laser-specific | Two concrete, encodable lists. Pre-procedure (p.5, rule «Любой "да" = stop + врач»): *«одышка, боль в груди, обморок, односторонний отек/боль в ноге, высокая температура, быстро распространяющееся покраснение/боль кожи»*. Post-procedure, RF row (p.7): *«Волдыри, ожог, гной, усиливающийся отек/боль, онемение, признаки рубцевания, деформация, выраженная асимметрия»* — the burn/blister set transfers to laser directly. **No laser-specific flag anywhere**: no folliculitis, no paradoxical hypertrichosis, no PIH, no crusting. |
| `follow_up` | **UNKNOWN — nothing found** | No cadence, no course length, no session spacing, no episode close criterion, for laser or any procedure. |

### Zone granularity — the material cannot support a zone-level protocol

Every section of every document found is **procedure-level**. Zone appears in exactly two roles:
as a free-text input slot (`treatment_area`, p.5) feeding localized checks (*«Инфекция, сыпь,
открытая рана, герпес, дерматит в зоне обработки»*; *«Металл… тату с металлическими частицами в
зоне»*), and as an unfilled doctor placeholder («допустимые зоны», p.6). Nothing varies by zone.

This agrees with §2's independent finding from the catalog side: the bundles (`Всё тело`,
`Must Have`, `Классика`, `Super`) span multiple zones under one service id, so a zone key cannot be
derived from the booking either. **Both the evidence side and the data side point the same way: one
zone-independent protocol for `service_scope=laser_epilation` covering all 17 cards, or nothing.**

### The decisive answer on deterministic grading

**Named symptoms: yes. Time windows and thresholds: no — and the source explicitly forbids
inventing them.**

Searched the full extracted text for every time expression: `48` → 0, `24` → 0, `72` → 0, `дня` → 0,
`курс` → 0, `повтор` → 0. The only two numeric intervals in the whole document are RF
*pre*-procedure and both conditional on a doctor's approval (p.6). And, verbatim (p.7):

> «В Aftercare-шаблоне врач должен отдельно утвердить временные окна: сколько часов/дней допустимы
> покраснение, отек, болезненность; когда разрешены баня, спорт, сауна, активное солнце, косметика
> и heat-based процедуры после RF. **Эти интервалы нельзя "угадывать" продуктом; они должны
> приходить из device-specific протокола клиники.**»

**Consequence, stated plainly:** a fail-closed **URGENT rail** can be built today from named symptoms
with no time component. The **NORMAL → WATCH → CAUTION gradient cannot** — that gradient *is*
"redness beyond N hours", "swelling still present at day N", and every N is missing by an explicit
decision of the researcher, not by oversight.

That is the same conclusion §11 reaches from the boundary side, arrived at independently: Care's
reassurance branch is the part with no sanction and no evidence.

### One gift the material does give — the protocol field list

Page 8 hands over what is essentially the `ServiceCareProtocol` row schema, already thought through:

> «каждая строка master-матрицы должна содержать минимум: `condition_id, plain_language_name,
> procedure_id, risk_level, bot_action, human_owner, source_ref, evidence_date,
> patient_question_text, exceptions, approved_by, effective_from, version`»

`approved_by` + `effective_from` + `version` + `source_ref` + `evidence_date` are exactly the
governance fields §3's ayla-knowledge lifecycle provides. And an anti-downgrade rule that matches
OR-CARE-7 nearly word for word (p.7): *«aftercare-текст не должен содержать формулировки вроде
"процедура полностью безрисковая" или "любые реакции нормальны"»*.

The contraindication matrix itself (pp.2–5, 13 graded rows with Н/У/В/А per procedure and a
recommended action) plus the escalation contract (p.8) are directly reusable — **for massage, LPG
and RF**. They are the right shape and the wrong service.

### Verdict on §16

**OR-CARE-3's premise — that the existing four research documents can source a laser protocol —
does not hold.** The plumbing for a laser vertical slice is ready (§2, §4, §5, §6); the *content* is
not, and cannot be produced without exactly the mass service research the task forbids.

Two honest options for the owner, neither of which an implementer may pick:

1. **Retrieve the real Doc 3 / Doc 4 first.** Seeded Doc 3 (51 rows) is materially larger than the
   5-row aftercare table in the readable PDF, and the smoke test at
   `apps/kb/services/tests/test_global_fallback_smoke.py:305-317` shows it covers procedures the PDF
   never mentions (*"threads-aftercare … biorevitalization-aftercare"*). It **may** contain a laser
   card. Pull the four Google Docs, or dump `kb_kbdocument` from wherever the seed actually ran, and
   re-run this grid. Cheap, and it settles the question. *Note: the readable PDF scoped itself to
   massage/LPG/RF because the catalog was "unspecified" at the time, which makes a laser card
   unlikely — but "unlikely" is not "checked".*
2. **Re-scope the first vertical slice to a service the material actually covers** — RF-лифтинг or
   LPG. Everything in §2's mapping (category UUID → scope) works identically for another category;
   only the one registry row changes. This would trade OR-CARE-3's chosen service for a slice that
   can ship with a real, doctor-signable protocol.

If neither is acceptable, the honest third option is a doctor-authored laser protocol — which is new
research, and therefore an owner decision, not a workaround.
---

## 9. Minimal implementation plan

Seven tasks. Two of them are prerequisites that are **not Care work** and must not be smuggled into
a Care ticket. Nothing here is authorized to start — this is the shape, for the owner to sequence.

### Prerequisite P1 — owner rulings (no code)

Three questions block design, not implementation, and all three are cheap to answer and expensive to
guess:

1. **Is `completed_by == "system"` an authoritative completion fact for Care?** (§4) — decides
   whether a no-show gets an aftercare message.
2. **Is a versioned, human-authored statement about a *procedure* inside or outside «нельзя о его
   организме»?** (§11) — decides whether Care's NORMAL branch may exist at all.
3. **Which `task_type` does a care escalation use** — `MEDICAL_RED_FLAG` (bot keeps talking) or
   `HANDOFF` (bot goes silent)? (§6.6) — probably risk-level-dependent, which is itself a protocol
   decision.

### Prerequisite P2 — Telegram inbound safety gap

Not Care's work and not Care's ticket, but Care must not ship symptom handling onto an ungated path.
Either fix `apps/channels/telegram/handler.py` (§6.5 / Conflict 2) or scope the slice to MAX and say
so in writing. **File separately, at a priority above Care.**

### Prerequisite P3 — retrieve the source material, or re-scope the slice (no code)

**Blocks the protocol authoring in T1, not the machinery.** There is no laser protocol content
anywhere reachable (§8): `kb_kbdocument` is empty on the pilot, the four source Google Docs were
never committed, and the one readable upstream research report is scoped to massage / LPG / RF with
zero laser content. Either pull the four Google Docs (or dump `kb_kbdocument` from wherever the seed
actually ran) and re-run §8's coverage grid, **or** re-scope the first vertical slice to RF-лифтинг
or LPG, where the material exists. Owner's call; §8 sets out both.

Everything in T1–T5 and T7 is service-agnostic and can proceed on either answer. Only the *content*
of the first protocol document depends on it.

### T1 — Protocol schema, loader, and the runtime link

* Register a `service-care-protocol` document type in `.knowledge/schema.yaml` `document_type_rules`;
  author the protocol as a `03 AI System/Contracts/*.yaml` registry file with
  `registry_version` / `compatible_contract_version` / `status` / `updated`, following
  `intent-registry.yaml:9-15`.
* Validator script modelled on `scripts/validate_amd001_schemas.py`, wired into the existing
  `.github/workflows/validate-knowledge.yml`. (`validate_knowledge.py` is markdown-only — §3 gap 2.)
* Runtime loader in the bot: strict `from_dict` with unknown-key rejection returning a frozen
  dataclass, copied from `apps/replay/fixtures/schema.py:65`.
* **Delivery: SHA-pin, following `pyproject.toml:152`'s `ayla-ai-core` pattern.** This is the version
  pinning that satisfies test gate #5 and it is the task's largest single unknown (§3 gap 1) — there
  is *no* runtime path from ayla-knowledge into the bot today.
* ACTIVE gate at load: refuse anything not `canonical_status: approved` / `activation_status: active`.

### T2 — `service_scope` mapping

* `CareServiceScope` registry: one entry, `ea63f753-b46a-40d0-8cf7-1075dc1bdb4e → laser_epilation`.
* Resolver with the two-step category fallback (`SalonService.category` → `template.category`, §2).
* Care-scope coverage command, cloned from
  `apps/catalog/management/commands/ayla_service_id_coverage.py`.
* **Recommended, in the Ayla repo:** add an OPTIONAL `service_id` (or `service_scope`) to the
  `booking.completed` payload in `emit_booking_completed` (`completion.py:29`). Non-breaking under
  event-contract §4.1, no `event_version` bump, three lines — and it makes the Care consumer immune
  to DRF-1103. **Highest leverage-to-cost item in the plan.**

### T3 — `CareEpisode` + completion trigger

* `apps/care` app; `CareEpisode` per §5, with `source_event_id` UNIQUE.
* `maybe_open_care_episode(envelope)` called from **inside** `handle_booking_completed`
  (`consumers/booking.py:1302`), within the dedupe transaction.
* Refuse `completed_by == "system"` pending P1.1; refuse unknown scope; pin protocol id + version.
* Episode history via `write_audit("care.*", target="CareEpisode", …)` — no `CareEvent` model yet (§5).
* Lint boundary forbidding `apps/care` from importing the memory writer/reader (Conflict 4).

### T4 — Aftercare / follow-up

* Beat sweeper on `state=ACTIVE AND next_followup_at <= now`, shaped after
  `apps/bookings/followups.py`.
* Gate chain: `has_global_consent(PERSONAL_DATA)` **and** `has_global_consent(HEALTH)` →
  `not proactive_messages_opt_out` → quiet hours → protocol rule. Fail-closed.
* Outbound via `apps.channels.max.outbound.send_message`; `write_audit` per decision with a reason
  slug.
* **Sequencing: land DRF-1285 (PR #1236) first and reuse its quiet-hours helper and audit
  vocabulary** rather than building a parallel one (§6.4).
* Decide the collision with the existing 19:00 МСК `send_post_visit_followups` review nudge — one
  message or two? Currently nobody's ticket.

### T5 — Triage evaluator + handoff

* `evaluate_care(protocol, episode, observation) -> CareDecision` — pure, no I/O, no LLM, no DB (§6.7).
  Default `UNKNOWN`; red flags cannot be downgraded.
* Symptom extraction as a **tool spec** on the existing `apps/llm/protocol.py` interface — there is no
  pydantic and no JSON mode in this repo.
* Rendering via the existing "answer from these facts only" pattern (`concierge.py:503/:553-558`).
* Handoff via `create_admin_task(task_type=MEDICAL_RED_FLAG, priority=URGENT, reason=<reason code>)`
  — **reason code, never symptom text** (§6.6). Structured context via a new optional kwarg merged
  into `transcript_snapshot`: services-layer change, no migration.

### T6 — Consultation integration

* At `apps/orchestrator/concierge.py:691` / `:818`, after `discover_masters(resolve_service=True)`:
  resolve scope from `MasterCard.service_id`, load the ACTIVE protocol, surface protocol-critical
  clarification through the existing `ask_clarification` tool, apply contraindication restrictions.
* Extend `_matched_services` so "all matched services share one category" resolves a scope even when
  no single card is unambiguous — otherwise the laser bundles never trigger consultation (§6.1).

### T7 — Vertical slice wiring, gates, and the prompt-boundary rewrite

* Wire the triage branch: per-tenant between `handler.py:1079` and `:1095`; global as a new `elif`
  between `:689` and `:696`. Global safety stays first and untouched.
* The 17 test gates from the contract, plus: "Care never consumes a `marked_by`-shaped payload"
  (Conflict 1) and "Care writes nothing to `MemoryEntry`" as a lint rule, not a test.
* **The medical-boundary paragraph at `concierge.py:467-478` gets rewritten ONCE, covering nutrition
  and post-procedure symptoms together, against ONE shared regression corpus** — see §11. Two
  independent rewrites of the same paragraph is how the project ends up with two answers to "what may
  the bot say about health".

### Not in the plan, deliberately

No new microservice. No second handoff subsystem. No new orchestrator. No catalog migration. No
`CareEvent` model. No changes to `gate.py`'s founder-signed texts. No mass service research.
---

## 10. Verdict

```text
CARE CODE RECONCILIATION:    PASS
LASER VERTICAL SLICE:        BLOCKED — on protocol CONTENT, not on code
                             (identity, plumbing and seams are READY; the source
                              material for a laser protocol does not exist — §8)
PROTOCOL STORAGE:            RESOLVED (ayla-knowledge Contracts pattern + SHA-pinned runtime link)
COMPLETION TRIGGER:          RESOLVED (mechanism sound; has produced ZERO facts on the pilot)
CARE EPISODE OWNER:          RESOLVED (ai-bot-platform, new apps/care)
SAFETY/CONSENT BOUNDARIES:   PASS with caveats  (three pre-existing gaps named — §7 conflicts 2,3,4)
IMPLEMENTATION:              GO for T1–T5 and T7 plumbing;
                             NO-GO for authoring the laser protocol until §9 P3 is answered
```

**The one blocking finding, stated once, plainly:** every piece of machinery Care needs exists and
fits. What does not exist is the **laser protocol content**. The "four existing research documents"
OR-CARE-3 assumes as source material are four Google Docs seeded into `global_kb`; the one readable
upstream research report contains **zero** laser content (`лазер` 0, `эпиляц` 0, `фототип` 0) because
it was written against a guessed massage/LPG/RF service set while the catalog was still
"unspecified"; and `kb_kbdocument` is **empty on the pilot** (0 rows). A laser protocol cannot be
normalized from material that is not there, and producing it is the mass service research the task
forbids. See §8 for the two options this leaves the owner.

**No STOP condition is triggered.** All nine were checked:

| # | STOP condition | Status |
|---|---|---|
| 1 | laser lacks stable service **identity** | **Not triggered** — `ServiceCategory ea63f753`, 17 services, present on both sides (§2). Note the condition is about identity, and identity is fine; the missing thing is protocol *content* (§8), which the STOP list does not cover |
| 2 | no authoritative completion fact | **Not triggered** — exists, attributed, idempotent, has a consumer. Zero occurrences to date (§4) |
| 3 | CareEpisode owner conflicts with domain ownership | **Not triggered** — reference only, `RemoteBookingProxy` precedent (§5) |
| 4 | proactive outbound requires consent bypass | **Not triggered** — `has_global_consent(PERSONAL_DATA + HEALTH)` exists and is used (§6.4) |
| 5 | triage requires weakening existing safety | **Not triggered, but closest** — Care sits *after* safety and strengthens the risk call by making it deterministic. The reassurance question (§11) is a boundary decision, not a weakening |
| 6 | protocol storage conflicts with knowledge governance | **Not triggered** — registering a document type is the sanctioned mechanism (§3) |
| 7 | Care requires prohibited Memory reads/writes | **Not triggered** — Care needs none. But the prohibition is unenforced (§6.8) |
| 8 | new microservice necessary | **Not triggered** |
| 9 | large booking/catalog rewrite required | **Not triggered** — one registry row + one optional event field |

### What would actually make this fail, in order of likelihood

0. **There is no laser protocol to load** (§8). This is not a risk, it is the current state. It does
   not stop T1–T5 (the machinery is service-agnostic), but it stops the *vertical slice* from being
   about laser. Two clean options in §8; both are the owner's.
1. **The owner does not sanction reassurance** (§11). Care's NORMAL branch is its most common output.
   Without a ruling, the slice ships a system that can escalate but cannot calm — which is worse than
   not shipping it.
2. **Completion never fires.** The mechanism is correct and unused. DRF-1048 has been In Progress
   since 15.08 and its root cause (no specialist app) is not a Care problem. If no salon operator ever
   closes a visit, the aftercare leg is dead code no matter how well built.
3. **The ayla-knowledge → runtime link is larger than it looks** (§3, gap 1). There is no such link
   today, in either direction, and the existing hand-synced registry has already drifted
   (`intent_router.py:82`). The `ayla-ai-core` SHA-pin pattern makes it tractable, not free.
4. **DRF-1103 blocks bot-side scope resolution.** Mitigated entirely by the optional `service_id`
   field on `booking.completed` (T2).

### Three claims in the orchestrator's brief that this audit refutes

Recorded because refutation was asked for, and because each changes a decision:

1. **"3 laser matches in `services_salonservice`."** Seventeen. Name matching would have silently
   excluded 82% of the scope, including every bikini and every bundle. The stable key is the category
   UUID, not the name. (§2)
2. **"17 beat tasks, none proactive."** At least three proactive outbound tasks run today, one of
   them a **post-visit** message with an opt-out but **no consent gate** — the incumbent Care must
   not copy. And `proactive_messages_opt_out` demonstrably works. (§6.4)
3. **"Five approved memory keys."** There is no key allowlist at all; the only key ever written in
   production is `diet`, and `key_cardinality()` *accepts* unknown keys by default. The closed
   seven-item set the brief is thinking of gates a different store entirely. (§6.8)

And two smaller corrections: the auto-complete sweep is **off by env**, not silently misconfigured
(§4) — there is no `booking.auto_complete.misconfigured` line to look for; and `ConsentType.HEALTH`
has been granted five times today by a working DRF-1284 flow, all withdrawn seconds later, so the
mechanism is proven even though no live grant exists (§6.4).

### The convergence worth noticing

Two independent lines of this audit — the boundary line (§11) and the evidence line (§8) — arrive at
the same place from opposite directions:

* **§11:** the owner's 23.08 ruling forbids the negation of a health concern, so Care's NORMAL /
  "this is an expected reaction" branch currently has **no sanction**.
* **§8:** the research material contains named URGENT symptoms but **no time windows at all**, and
  explicitly forbids guessing them — so the NORMAL → WATCH → CAUTION gradient has **no evidence**.

The same branch is unsanctioned and unevidenced. The URGENT rail, by contrast, is both permitted and
supported. **That is a strong hint about the honest shape of the first slice: build the escalation
rail, not the reassurance rail.** A Care Runtime that only ever says "this needs a doctor, here is
how to reach one" or says nothing is defensible today; one that says "don't worry, that's normal" is
not — twice over, for two unrelated reasons.

### Recommended next command from the owner

Answer §9's P1 (three rulings) and P3 (retrieve or re-scope), decide the priority of §9's P2 relative
to Care, and authorize T1+T2 only. T1 and T2 are the two tasks whose unknowns are structural rather
than incremental; everything after them is ordinary work, and neither can be estimated honestly until
the runtime link in T1 is prototyped.

**Implementation is not authorized by this report.**
---

## 11. Alignment of the Care boundary with the owner's DRF-1295 ruling

This section was requested in addition to the ten the task specifies. It is not a "canonical
conflict" in the task's sense, but it is adjacent and at least as consequential.

### What DRF-1295 actually says — verified, not paraphrased

The issue itself (`DRF-1295`, priority Urgent, milestone "Controlled Pilot — 3–5 users") is still in
**Backlog** and its *description* is an open question to the owner, not a ruling. The ruling is in
the comment of 2026-08-23 07:37 UTC, titled «РЕШЕНИЕ ВЛАДЕЛЬЦА 23.08: анализировать плотнее,
рекомендовать слегка». Reading only the issue body would miss it.

**Permitted:**
* facts and arithmetic over the person's own entered data;
* a *light* recommendation anchored to a goal the person named. The owner's word is «слегка» — a
  remark that is easy to wave off, not a plan.

**Forbidden, explicitly not softened:**
* diagnosis **and its negation** — «у вас непереносимость», «это не аллергия, а просто…»,
  «похоже на дефицит железа». *Ни утверждать, ни опровергать.*
* treatment, dosages, supplements, discontinuing anything;
* inferring a health state from behaviour;
* crisis — «здесь граница не двигается вовсе, и `pre_check` работает как работал»;
* allergy — DRF-1290 stands, separate perimeter, never green memory.

**The rule everything derives from:** «Говорить можно о его данных. Нельзя — о его организме.»

**And an absolute:** «Выдумывать числа» is forbidden — numbers come only from the profile and the
logged data; if there is no data, say so rather than estimate.

The comment also states the obligation this section discharges: *«Медицинская граница записана не
только в промпте: канонический journey (сценарий N4.5 → S8) и Конституция ст. XII. Если новая
граница расходится с каноном — расхождение надо назвать и разрешить явно, а не обойти правкой
промпта.»*

### Where that boundary lives in code today

`apps/orchestrator/concierge.py:467-478`, inside the live system prompt, under «Границы
(обязательно)»:

> «Медицинские темы (диагнозы, лекарства, **боль, травмы**, опасные цели похудения): остановись,
> коротко назови границу без диагноза («я не врач и не оцениваю здоровье»), **спокойно обозначь
> риск простыми словами** и предложи безопасный шаг — обратиться к профильному специалисту или
> сформулировать новое безопасное намерение. **Не сохраняй медицинские выводы как факт о клиенте.**»

Three things follow immediately:

1. **The current boundary already covers Care's subject matter.** «боль, травмы» is precisely
   post-procedure symptom reporting. Care does not enter virgin territory; it enters territory that
   already has a rule.
2. **The current boundary already has Care's shape.** «спокойно обозначь риск простыми словами и
   предложи безопасный шаг» is, structurally, `risk_level` + `action` + `allowed_response_facts`.
3. **The last sentence is the memory boundary, stated in the prompt** — congruent with §5's
   prohibition on writing care state to `MemoryEntry`, and with DRF-1260.

### Verdict: three points of agreement, one real divergence, one gap

**Agreement 1 — Care *strengthens* rather than weakens this boundary.** Today the model itself
performs «обозначь риск», non-deterministically, unversioned, and unauditable. OR-CARE-7 moves that
judgement into a versioned protocol evaluated deterministically, with the LLM demoted to rendering
and forbidden to downgrade. Measured against DRF-1295's own concern — *«проверять надо обе стороны:
что полезное перестало блокироваться и что запрещённое по-прежнему блокируется. Вторая половина
важнее»* — Care makes the second half testable for the first time.

**Agreement 2 — the "no invented numbers" absolute maps exactly onto OR-CARE's test gate #2**
("unknown protocol → no invented advice"). Same principle, two domains: if there is no protocol for
this scope, say there is nothing to say. Neither system may estimate.

**Agreement 3 — crisis is untouched.** DRF-1295 freezes `pre_check`; OR-CARE-7 and the task's §11
both require Care to sit *after* global safety and never replace the crisis short-circuit. No gap.

**DIVERGENCE — reassurance. This is the one that must go to the owner.**

DRF-1295 forbids the negation of a diagnosis as firmly as the assertion of one: *«ни утверждать, ни
опровергать»*, with «это не аллергия, а просто…» given as the example of what is forbidden.

Care's `expected_reactions` / `NORMAL` branch **is** that sentence. "Redness and slight swelling for
24–48 hours after laser epilation is an expected reaction" is, functionally, telling a person that
what they are feeling is not a problem — a negation of a concern about their body. Under the literal
rule «нельзя о его организме», it is forbidden. Under Care's design it is the single most common
and most valuable thing the system says.

**And this is not only prose — it is already enforced in code.** `apps/orchestrator/safety/outbound.py:47-49`
carries a `_MEDICAL` pattern set that blocks the assistant from *asserting* «у вас аллергия». That is
DRF-1295's «ни утверждать, ни опровергать» implemented as a regex. Two consequences: it proves the
rule is real rather than aspirational, and it means **legitimate Care copy would trip it** — the
subagent audit flags exactly this. Today that costs nothing, because `evaluate_outbound` is wired
into the salon path only (§6.6); the moment it is wired into a client path, Care's reassurance
sentences meet it head-on.

The distinction that resolves it, if the owner accepts it, is that the statement's **source** is
neither the person's own data nor the model's opinion. It is a third category DRF-1295 does not
address:

```
his data          -> permitted   (DRF-1295)
his body          -> forbidden   (DRF-1295)
the procedure     -> NOT ADDRESSED
```

A protocol expectation is a pre-authored, human-approved, versioned statement about **what this
service typically does to skin** — a property of the procedure the salon performed, not a judgement
about this person. Saying "this service commonly causes redness for 24–48h" is the same *kind* of
claim as "this service costs 1400 ₽ and takes 60 minutes". Saying "**your** redness is normal" is
not. The distinction is real, it is enforceable in the renderer (it is a constraint on grammatical
subject and on `allowed_response_facts`), and it keeps Care inside the rule — **but it is a boundary
extension, and it is the owner's to make, not the implementer's.**

**GAP — nothing prevents the two boundaries from drifting apart.** The nutrition boundary and the
Care boundary would both be expressed as prose inside the same system prompt at
`concierge.py:467-478`, edited by different tickets (DRF-1295's own "переписать медицинскую границу"
task, and Care's consultation/triage work) with no shared test set. DRF-1295 already anticipates
this — it asks for measurement across a corpus of phrasings rather than a single try. **Recommendation:
one prompt-boundary rewrite, one shared regression corpus, covering both nutrition and post-procedure
symptoms.** Two independent rewrites of the same paragraph is how the project ends up with two
different answers to "what may the bot say about health", which is exactly the outcome this section
was asked to prevent.

### Formal answer

**Care's proposed boundary is congruent with DRF-1295 on crisis, on invented numbers, on diagnosis,
on treatment, and on memory. It diverges on exactly one point: reassurance / expected reactions,
which DRF-1295 forbids as the negation of a diagnosis and which Care requires. Resolving it needs an
owner ruling on whether a versioned, human-authored statement about a *procedure* is inside or
outside «нельзя о его организме». Until that ruling exists, Care's NORMAL branch has no sanction.**
