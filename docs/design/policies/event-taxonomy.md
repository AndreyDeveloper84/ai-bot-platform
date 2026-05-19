# Event Taxonomy — canonical event names + payload contract

**Date:** 2026-05-19 r3
**Status:** Foundational — locks event names + envelope structure across all modules
**Reads:** [`attribution-policy.md`](./attribution-policy.md), [`conversation-ownership-policy.md`](./conversation-ownership-policy.md), [`core-wellness-profile.md`](./core-wellness-profile.md)

> Every module emits events. Without a canonical taxonomy, names diverge between parallel implementations, analytics breaks, retention pipelines drift. This doc locks the names, envelope, and rules.

---

## 0. Why this exists

### The problem
We have multiple modules in flight — booking, scheduling, master management, conversations, loyalty, marketing, attribution. Each writes events to the event bus. Without a single source of truth:

- Analytics dashboard expects `booking.completed`, attribution writes `booking_completed` → silent gap
- Loyalty listens for `BookingCompleted`, marketing emits `booking.complete` → silent gap
- Wellness Profile listener subscribes to `customer_state_change`, conversations emits `customer.state_changed` → silent gap

This doc prevents that by **naming every event before it's written**.

### The promise
Any module reading or writing events:
- MUST use a name from this catalog
- MUST follow envelope structure §2
- MUST respect PII rules §6
- MUST version per §7
- Any new event MUST add an entry here before merge

---

## 1. Naming convention

### Pattern
```
<domain>.<entity>.<action>[.<modifier>]
```

- **domain** — top-level module: `booking` / `customer` / `master` / `schedule` / `campaign` / `loyalty` / `wellness` / `conversation` / `billing` / `admin` / `system`
- **entity** — the noun the action applies to (usually = domain root entity, can be sub-entity like `attribution`)
- **action** — past-tense verb: `created`, `updated`, `cancelled`, `assigned`, `triggered`, `completed`
- **modifier** (optional) — qualifies the action when one entity has many lifecycle states: `booking.status.changed.completed` vs `booking.status.changed.cancelled`

### Examples
- ✅ `booking.created`
- ✅ `booking.attribution.assigned`
- ✅ `customer.state.changed`
- ✅ `master.invite.accepted`
- ❌ `bookingCreated` (camelCase)
- ❌ `booking_attribution_assigned` (snake)
- ❌ `BookingCompleted` (PascalCase)
- ❌ `book.create` (singular noun, present tense)

### Rules
- All lowercase, dot-separated
- Past tense for completed actions (`created`, not `create`)
- Use full words, not abbreviations (`subscription`, not `sub`)
- Avoid implementation jargon (`record_inserted` is leaky)
- Avoid system/protocol prefixes (`db.row.inserted` — not domain event)

---

## 2. Envelope structure (every event)

All events share an outer envelope. Payload (`data` field) is event-specific.

```json
{
  "event_id": "evt_01HZX...",            // ULID, unique
  "event_name": "booking.attribution.assigned",
  "event_version": "1.0",                // SemVer; bump on breaking payload change
  "occurred_at": "2026-05-18T14:32:11Z", // ISO 8601 UTC
  "tenant_id": "tnt_abc123",             // salon scope; null for system events
  "actor": {
    "type": "ai" | "human" | "system" | "external",
    "id": "usr_xyz789" | "ai_persona_v2" | null,
    "role": "owner" | "admin" | "master" | "customer" | "ai_assistant" | null
  },
  "correlation_id": "cor_def456",        // trace through multi-step flow
  "causation_id": "evt_01HZW...",        // event that caused this event (optional)
  "data": { /* per-event payload */ },
  "metadata": { /* arbitrary tags, never PII */ }
}
```

### Required fields
- `event_id` — ULID, must be globally unique
- `event_name` — from this catalog
- `event_version` — start at `1.0`
- `occurred_at` — UTC ISO 8601
- `actor.type` — even if `system`
- `data` — even if empty object

### Optional fields
- `tenant_id` — required for all multi-tenant events; null for cross-tenant system events
- `actor.id` / `actor.role` — present when known
- `correlation_id` — for tracing flows
- `causation_id` — for event chains
- `metadata` — implementation tags

### Forbidden fields
- ❌ `user_email`, `phone`, `name` directly in envelope — use `actor.id` reference
- ❌ Free text from customer in envelope — payload only, with PII rules §6
- ❌ Raw bot/customer messages — store ID reference, fetch from conversation store

---

## 3. The canonical catalog

10 domains, 50+ events. New events must be added here before code merge.

### 3.1 Booking domain

| Event name | Trigger | Payload keys | Subscribers |
|---|---|---|---|
| `booking.created` | New BookingRequest row inserted | `booking_id`, `customer_id`, `master_id`, `service_id`, `slot_start`, `slot_end`, `booking_source` | analytics, attribution, master-mobile (notify) |
| `booking.attribution.assigned` | 4a writes booking_source/billable | `booking_id`, `booking_source`, `ai_assist_score`, `billable`, `billing_reason`, `attribution_metadata` | analytics, loyalty, marketing, billing |
| `booking.confirmed` | Customer confirms or auto-confirmed | `booking_id`, `confirmed_at`, `confirmation_method` (manual/auto) | conversation, master-mobile |
| `booking.cancelled` | Customer or admin cancels | `booking_id`, `cancelled_by` (actor), `cancellation_reason`, `cancelled_at` | loyalty (refund cascade), marketing (suppression) |
| `booking.completed` | Master marks visit done OR scheduled completion | `booking_id`, `completed_at`, `marked_by` | loyalty (points earn), wellness (Layer 4 update), marketing (post-visit campaign trigger), analytics |
| `booking.no_show` | Visit time passed, no completion | `booking_id`, `detected_at` | retention, marketing (suppress), analytics |
| `booking.rescheduled` | Slot change after creation | `booking_id`, `old_slot_start`, `new_slot_start`, `rescheduled_by` | master-mobile, conversation |
| `booking.refunded` | Payment refund processed | `booking_id`, `refund_amount`, `refunded_at`, `refund_reason` | loyalty (revoke points), billing |

### 3.2 Customer domain

| Event name | Trigger | Payload keys | Subscribers |
|---|---|---|---|
| `customer.created` | First customer record (any tenant) | `customer_id`, `tenant_id`, `acquisition_channel`, `first_touch_source` | analytics, marketing |
| `customer.state.changed` | core-user-states transition | `customer_id`, `old_state`, `new_state`, `reason`, `triggered_by` | marketing, retention, AI inference |
| `customer.profile.layer.updated` | Wellness Profile layer write | `customer_id`, `layer_name`, `confidence`, `source` | AI inference, retention |
| `customer.consent.changed` | Opt-in/out for any policy | `customer_id`, `consent_type`, `granted`, `granted_at`, `granted_via` | all modules (gate sends) |
| `customer.opted_out` | Customer blocks bot or full opt-out | `customer_id`, `opt_out_scope`, `reason` | marketing (suppress), conversation (lock) |
| `customer.deleted_request` | Customer requested account deletion (OP6) | `customer_id`, `requested_at` | retention pipeline |

### 3.3 Master domain

| Event name | Trigger | Payload keys | Subscribers |
|---|---|---|---|
| `master.invited` | Owner sends invite | `master_id`, `invited_by`, `invite_method` (max_handle/email) | master-mobile (deep link), analytics |
| `master.invite.accepted` | Master clicks deep link + onboards | `master_id`, `accepted_at` | scheduling (default hours apply), booking (becomes bookable) |
| `master.invite.expired` | 14d without acceptance | `master_id`, `expired_at` | owner UI (re-invite prompt) |
| `master.invite.cancelled` | Owner cancels before accept | `master_id`, `cancelled_by` | — |
| `master.archived` | `is_active=False` + `archived_at` set | `master_id`, `archived_by`, `archive_reason` | booking (un-bookable), analytics |
| `master.unarchived` | Reactivated | `master_id`, `unarchived_by` | booking (re-bookable) |
| `master.service.added` | New MasterService M2M row | `master_id`, `service_id`, `added_by` | booking (visibility), recommendations |
| `master.service.removed` | M2M row deleted | `master_id`, `service_id`, `removed_by` | booking (visibility) |

### 3.4 Schedule domain

| Event name | Trigger | Payload keys | Subscribers |
|---|---|---|---|
| `schedule.working_hours.updated` | Master/owner edits WorkingHours | `master_id`, `day_of_week`, `old`, `new`, `changed_by` | slot resolver, audit |
| `schedule.exception.added` | New ScheduleException row | `master_id`, `exception_id`, `kind`, `date_range`, `added_by` | slot resolver, customer notification |
| `schedule.exception.removed` | Exception deleted | `master_id`, `exception_id`, `removed_by` | slot resolver |
| `schedule.timeblock.added` | New TimeBlock (lunch/cleaning) | `master_id`, `block_id`, `kind`, `time_range` | slot resolver |
| `schedule.change_request.submitted` | Master proposes schedule change | `change_request_id`, `master_id`, `proposed_change` | owner UI (approval queue) |
| `schedule.change_request.approved` | Owner approves | `change_request_id`, `approved_by` | applies change |
| `schedule.change_request.rejected` | Owner rejects | `change_request_id`, `rejected_by`, `reason` | master-mobile notification |
| `schedule.slot_config.updated` | Buffer/lead/max-advance change | `tenant_id`, `old`, `new`, `changed_by` | slot resolver |

### 3.5 Conversation domain

| Event name | Trigger | Payload keys | Subscribers |
|---|---|---|---|
| `conversation.started` | First message in new thread | `conversation_id`, `customer_id`, `channel` (max/tg/web), `entry_intent` | analytics, AI |
| `conversation.message.sent` | Any message (in or out) | `conversation_id`, `message_id`, `direction` (in/out), `actor`, `intent_class`, `content_ref` (NOT raw text) | analytics, AI |
| `conversation.handoff.to_human` | AI → human transition | `conversation_id`, `tier` (AI_CONTINUITY/HUMAN_SUPERVISED/HUMAN_LOCKED), `reason`, `triggered_by` | dashboard (alert), analytics |
| `conversation.handoff.to_ai` | Human → AI release | `conversation_id`, `released_by`, `notes_summary_ref` | AI (resume context) |
| `conversation.escalated` | SLA breach or critical | `conversation_id`, `sla_tier` (15/30/60/120), `breach_minutes` | dashboard, analytics |
| `conversation.persona.violation` | Voice check failed | `conversation_id`, `message_id`, `violation_type` | persona-editor analytics |
| `conversation.satisfaction.scored` | Post-conversation CSAT | `conversation_id`, `score`, `feedback_ref` | analytics |

### 3.6 Wellness domain

| Event name | Trigger | Payload keys | Subscribers |
|---|---|---|---|
| `wellness.input.recorded` | Any module (food/water/body/sleep/mood/avatar/symptom) writes | `customer_id`, `module_name`, `input_type`, `value_ref`, `confidence` | Wellness Profile aggregator, AI inference |
| `wellness.profile.layer.updated` | Aggregator writes derived field to profile | `customer_id`, `layer_name`, `field`, `old`, `new`, `source` | AI, retention |
| `wellness.insight.generated` | AI produces new insight | `customer_id`, `insight_id`, `insight_type`, `confidence`, `evidence_refs` | conversation (suggest), retention |
| `wellness.recommendation.shown` | Recommendation surfaced to customer | `customer_id`, `recommendation_id`, `surface` (home/chat/email), `service_id` (if applicable) | analytics |
| `wellness.recommendation.acted` | Customer clicked/booked from recommendation | `recommendation_id`, `action_type`, `resulted_booking_id` (optional) | attribution (`ai_assist_score` input), analytics |
| `wellness.consent.module.granted` | Customer opted into specific input module | `customer_id`, `module_name`, `granted_at` | the module enables itself |
| `wellness.consent.module.revoked` | Opted out | `customer_id`, `module_name`, `revoked_at` | module stops collecting; soft-delete cascade |

### 3.7 Campaign domain

| Event name | Trigger | Payload keys | Subscribers |
|---|---|---|---|
| `campaign.dispatched` | Send executed | `campaign_id`, `dispatch_id`, `customer_id`, `template_id`, `dispatched_at` | analytics |
| `campaign.opened` | Read receipt (where supported) | `dispatch_id`, `opened_at` | analytics |
| `campaign.clicked` | Inline button or deep-link tap | `dispatch_id`, `button_id`, `clicked_at` | attribution (may write `attribution_metadata.campaign_id` on booking) |
| `campaign.converted` | Attribution linked back to dispatch | `dispatch_id`, `attributed_booking_id`, `attribution_window_days` | analytics, marketing dashboard |
| `campaign.suppressed` | Send blocked by frequency/opt-out | `campaign_id`, `customer_id`, `suppression_reason` | dispatch service, analytics |

### 3.8 Loyalty domain

| Event name | Trigger | Payload keys | Subscribers |
|---|---|---|---|
| `loyalty.points.earned` | Points credited on `booking.completed` | `customer_id`, `points`, `source_booking_id`, `multiplier_applied` | customer profile, conversation (notify) |
| `loyalty.points.redeemed` | Customer uses points | `customer_id`, `points`, `redemption_target_id` (booking/product), `redeemed_at` | analytics |
| `loyalty.points.revoked` | Refund or cancel cascade | `customer_id`, `points`, `reason`, `cascade_from_booking_id` | analytics |
| `loyalty.tier.changed` | Tier up/down | `customer_id`, `old_tier`, `new_tier`, `reason` | conversation (announce), marketing (eligibility) |

### 3.9 Billing domain

| Event name | Trigger | Payload keys | Subscribers |
|---|---|---|---|
| `billing.invoice.generated` | End-of-month or per-event invoice | `invoice_id`, `tenant_id`, `period`, `total_amount`, `billable_booking_count` | finance, tenant notification |
| `billing.payment.received` | Payment confirmed | `invoice_id`, `tenant_id`, `amount`, `paid_at`, `method` | dunning (clear), analytics |
| `billing.payment.failed` | Charge declined | `invoice_id`, `tenant_id`, `failure_reason` | dunning (start), tenant notification |
| `billing.dunning.escalated` | Past-due moved to next dunning stage | `tenant_id`, `dunning_stage`, `days_past_due` | account ops, tenant suspension trigger |
| `billing.tenant.suspended` | Hard pause | `tenant_id`, `suspended_at`, `suspension_reason` | all customer-facing surfaces (block), salon notification |

### 3.10 Admin / System domain

| Event name | Trigger | Payload keys | Subscribers |
|---|---|---|---|
| `admin.user.login` | Web/Mini App login | `user_id`, `tenant_id`, `role`, `ip_country` | audit, security |
| `admin.permission.changed` | Role assigned/revoked | `target_user_id`, `tenant_id`, `old_role`, `new_role`, `changed_by` | audit, security |
| `admin.settings.updated` | Tenant settings change | `tenant_id`, `setting_path`, `old`, `new`, `changed_by` | audit, downstream re-config |
| `admin.audit.event` | Any audit-worthy action not covered above | `tenant_id`, `actor`, `action`, `target`, `result` | audit log only |
| `system.module.health.degraded` | Module reports degraded state | `module_name`, `severity`, `metric` | ops alerting |
| `system.batch.completed` | Scheduled job done | `job_name`, `duration_ms`, `result` | ops, analytics |

---

## 4. Subscribers — who reads what

### Cross-cutting subscribers (read most events)
- **Analytics dashboard** — most `booking.*`, `customer.*`, `campaign.*`, `loyalty.*`, `conversation.*`
- **Audit log** — every event with `actor.type=human` or admin domain
- **AI inference engine** — `customer.*`, `wellness.*`, `conversation.message.sent` for adaptation

### Targeted subscribers
- **Loyalty processor** — `booking.completed`, `booking.refunded`, `booking.cancelled` (cascade)
- **Marketing dispatch** — `customer.state.changed`, `booking.completed`, `booking.no_show`, `customer.consent.changed`, `customer.opted_out`
- **Wellness Profile aggregator** — all `wellness.input.recorded`, `booking.completed`
- **Slot resolver** — all `schedule.*` events that affect availability
- **Billing pipeline** — `booking.attribution.assigned` (with billable=true), `billing.*`

### Subscriber contract
Every subscriber MUST:
1. Be idempotent — re-receiving same `event_id` is safe
2. Handle out-of-order delivery within reason
3. Acknowledge or retry; never silently drop
4. Reject events with unknown name (don't guess) — log and alert

---

## 5. Replay + dead letter

### Replay
The event store retains all events ≥ 90 days for replay scenarios:
- Subscriber bug → fix + replay missed events
- New analytics view → backfill from historical events
- Audit investigation → reconstruct timeline

Replay flag in envelope: `metadata.replay=true` so subscribers don't re-trigger side effects (no second email send).

### Dead letter
If a subscriber rejects an event 3× with error, it lands in dead-letter queue:
- Engineering alerted
- Manual triage
- Either fix subscriber + replay, OR mark event as «known unhandleable» and skip

---

## 6. PII rules

### Forbidden in event payloads
- Customer phone, email, full name as plain values
- Raw bot/customer message text
- Photo bytes or file content
- Geographic coordinates beyond city level
- Credit card numbers, full payment details
- Health symptom details in plain text (Layer 4 Profile sensitive)

### Allowed in event payloads
- ID references: `customer_id`, `message_id`, `content_ref` (pointer to encrypted store)
- Enums, codes, status values
- Numeric metrics (amounts, scores, counts)
- Timestamps
- Coarse geography (country, city if not enough to identify)
- Names of services, masters, tenants (publicly available within tenant)

### When sensitive data is needed downstream
Subscriber fetches it from the canonical store using the ID, with its own access control. Events are pointers, not payloads, for sensitive data.

### Example
```json
// ✅ correct
{
  "event_name": "conversation.message.sent",
  "data": {
    "conversation_id": "cnv_abc",
    "message_id": "msg_xyz",
    "direction": "in",
    "intent_class": "booking_inquiry",
    "content_ref": "msg_xyz"
  }
}

// ❌ wrong
{
  "event_name": "conversation.message.sent",
  "data": {
    "raw_text": "Здравствуйте, хочу записаться, мой телефон +7-911-...",
    "customer_phone": "+7-911-..."
  }
}
```

---

## 7. Versioning

### Rules
- Start at `event_version: "1.0"`
- **Patch bump** (`1.0.1`): doc-only change, no payload change. Rare.
- **Minor bump** (`1.1`): additive payload change — new optional fields. Old subscribers still work.
- **Major bump** (`2.0`): breaking payload change — renamed/removed fields, changed types. Old subscribers break.

### Breaking change procedure
1. Add new event name with `2.0`: emit BOTH `1.x` and `2.0` for one quarter
2. Migrate subscribers to `2.0`
3. After all subscribers migrated + 30d soak, retire `1.x` emission
4. Document deprecation in this doc

### Never
- ❌ Change semantics of an existing version (silent breaking change)
- ❌ Reuse a deprecated event name with new meaning

---

## 8. Tenancy + cross-tenant events

### Default
Every event MUST have `tenant_id`. Multi-tenant by design.

### Exceptions (tenant_id = null)
- `system.*` events (cron, batch jobs)
- Cross-tenant customer events when customer exists on multiple tenants (rare; per Q-CO5 customer-tenant is separated, so even these get one tenant_id per occurrence)
- Platform-level admin events (founder/ops actions outside tenant scope)

### Cross-tenant data leakage prevention
Subscribers MUST filter by `tenant_id` before processing. Reading another tenant's event = security incident.

---

## 9. Schema enforcement

### Where validation lives
- **At emission time**: producer validates against this doc (envelope + name + required payload keys)
- **At storage time**: event store accepts any well-formed envelope; logs unknown event names but doesn't reject (so we don't lose events during evolution)
- **At consumption time**: subscribers validate payload before processing; reject + log if mismatch

### Source of truth
This doc + a generated JSON schema file `docs/design/policies/event-schemas/*.json` (Phase 2). MVP: doc only.

---

## 10. Adding a new event

Procedure:
1. Discuss with UX Architect (this role) — does it fit existing domain or need new one?
2. Add row to §3 catalog with name, trigger, payload, subscribers
3. Add to producer code + bump emitting module version
4. Notify subscribers (analytics, etc.) so they expect it
5. Merge as one PR including doc + code

Forbidden:
- ❌ Emit event without doc entry
- ❌ Add new domain without architectural review
- ❌ Reuse existing event name for new semantics

---

## 11. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Free-form `metadata` for important fields | Field becomes critical → spreads inconsistently | Promote to typed `data` key |
| Events for every DB write | Noise; consumers can't filter | Only emit business-meaningful events |
| Customer text in payload | PII leak, retention nightmare | Use `content_ref` to encrypted store |
| Future-tense names (`will_book`) | Confusing; intent ≠ event | Past tense always |
| Synchronous chains | Brittle; event flow becomes RPC | Subscribers must be async-tolerant |
| Skipping `correlation_id` on chains | Hard to debug | Always propagate when caused by another event |

---

## 12. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| Q-EV1 | Event store technology — Kafka, Postgres outbox, EventBridge, custom? | Postgres outbox MVP (low ops); evaluate Kafka at 1M+ events/day | Eng | 🟡 |
| Q-EV2 | Per-event PII review — automated linter or manual? | Automated lint on payload schema; reject PR if forbidden field in `data` | Eng | 🟡 |
| Q-EV3 | Retention window beyond 90d? | Replay 90d hot; cold archive 365d compressed | Eng + Legal | 🟢 |
| Q-EV4 | Subscriber idempotency — enforce via dedup table or rely on subscriber? | Dedup table at infra level (every subscriber gets cheap idempotency) | Eng | 🟡 |
| Q-EV5 | Cross-tenant event aggregation for platform analytics? | Separate aggregation pipeline; events still tenant-scoped | Eng | 🟢 |
| Q-EV6 | Customer-facing events (e.g., for export per OP6)? | Customer can request a JSON export of own events; redacted version | Legal + PM | 🟡 |
| Q-EV7 | AI-emitted events — actor.type=ai, but actor.id? | Use `actor.id = ai_persona_v{N}` where N is persona version | PM | 🟢 |
| Q-EV8 | Webhook delivery to tenant systems (YClients, etc.) — replay-safe? | Tenant integrations get a curated subset of events via webhook with HMAC + idempotency key | Eng | 🟡 |
| Q-EV9 | Should `conversation.message.sent` emit on EVERY message (high volume) or batched? | Per-message; volume manageable with proper partitioning | Eng | 🟡 |
| Q-EV10 | Test/staging events — pollute prod analytics? | `metadata.environment` tag; analytics filters by env | Eng | 🟢 |

---

## 13. Cross-document linkage

- [`attribution-policy.md`](./attribution-policy.md) — booking.attribution.assigned payload contract lives here
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — conversation.handoff.* events trigger tier transitions
- [`core-wellness-profile.md`](./core-wellness-profile.md) — wellness.profile.layer.updated payload references layer names
- [`wellness-input-modules.md`](./wellness-input-modules.md) — each module emits wellness.input.recorded
- [`core-user-states.md`](./core-user-states.md) — customer.state.changed reflects state machine transitions
- [`assistant-persona.md`](./assistant-persona.md) — conversation.persona.violation feeds back

---

## 14. Scope separation from `apps/events/` (product analytics)

### 14.1 Two separate buses by deliberate design

The codebase has **two event systems by deliberate design**, NOT one with conflicting names:

| System | Path | Purpose | Naming | Delivery | Catalog size |
|---|---|---|---|---|---|
| **Product analytics events** | `apps/events/` (exists) | Tracking user actions for Mixpanel / GA4 / Warehouse funnels | `snake_case` (e.g., `consent_granted`, `message_sent`) | Sync fanout to analytics tools | 13 (Sprint 3 vocabulary) |
| **Domain event bus** | `apps/eventbus/` (NEW per Q-EV-IMPL1) | Durable domain lifecycle events for internal subscribers (billing, loyalty, retention, AI inference, audit) | `domain.entity.action` (e.g., `customer.consent.changed`, `booking.attribution.assigned`) | Postgres outbox + poller per [§5 replay](#5-replay--dead-letter) | 50+ across 10 domains |

This doc (`event-taxonomy.md`) is **authoritative for the domain bus only**. It does NOT replace `apps/events/`.

### 14.2 When to use which (decision tree)

```
Am I emitting an event because…
│
├─ I want to track a user action for product funnels / engagement analytics?
│  └─ Use apps/events/ — sync, snake_case, fast-and-forgiving delivery
│
├─ Something happened that changes business state and other internal modules
│  need to react reliably (charge billing, update loyalty, update profile,
│  trigger AI inference, audit-log)?
│  └─ Use apps/eventbus/ (NEW) — durable outbox, dot.notation, guaranteed delivery
│
└─ Both?
   └─ Emit to BOTH. Deliberately. This IS the right pattern — analytics tracks
      «user did X»; domain bus signals «X state happened, react». Different
      consumers, different SLAs.
```

### 14.3 Overlap policy

Some lifecycle moments fire on BOTH buses by design. Example:

| Moment | apps/events/ (analytics) | apps/eventbus/ (domain) |
|---|---|---|
| Customer grants wellness module consent | `consent_granted` (Mixpanel funnel «module activations per week») | `customer.consent.changed` + `wellness.module.activated` (wellness aggregator subscribes; profile layer updates) |
| Customer sends first message to bot | `message_sent` (engagement metric for product team) | `conversation.started` + `conversation.message.sent` (AI persona violation linter subscribes; state machine evaluates) |
| Booking attribution assigned | (typically NOT in analytics — internal-state only) | `booking.attribution.assigned` (billing subscribes; analytics dashboard derives KPIs) |

**Overlap is OK. Coordination is NOT required.** Emitter explicitly calls both APIs when both are relevant. No silent mirroring (anti-magical pattern).

### 14.4 Naming convention difference is intentional

- `snake_case` in `apps/events/` signals «this is analytics-side, fire-and-forget» to readers
- `dot.notation` in `apps/eventbus/` signals «this is domain-side, durable, subscriber-binding» to readers

If we unified naming, engineers couldn't tell which API to call. Different conventions = decision aid.

### 14.5 Engineering review rule

Every PR that emits events must answer in PR description:
> «This emits to: [ ] apps/events/ (product analytics) — for {{funnel/metric}} / [ ] apps/eventbus/ (domain bus) — for {{subscriber}}. Both / neither / one — and why.»

Code reviewer rejects vague «emit event» commits without this clarity.

### 14.6 Migration policy

- `apps/events/` STAYS as-is. 60+ existing call sites unaffected.
- `apps/eventbus/` is NEW work — port-as-needed, not big-bang migration.
- When a NEW domain event is needed → add to `apps/eventbus/` per §10 «Adding a new event» procedure
- When new analytics tracking is needed → add to `apps/events/vocabulary.py`
- If a domain event is found to be missing analytics-side tracking (or vice versa) → add to other bus deliberately, NEVER silently mirror via interceptor

### 14.7 Cross-bus correlation

Both buses share `correlation_id` (or `apps/events/`-equivalent `trace_id`) when fired in same request context. Allows joining «user click → analytics event + domain event» in observability tooling.

Engineering convention: pass `correlation_id` through both emitter APIs in same handler:
```python
correlation_id = generate_correlation_id()
# Analytics
product_events.emit("consent_granted", distinct_id=user.id, trace_id=correlation_id)
# Domain
domain_bus.emit("customer.consent.changed", actor=user, correlation_id=correlation_id, ...)
```

### 14.8 Implementation status (as of r2)

- **apps/events/** — exists, 13 vocabulary events, sync fanout to Mixpanel/GA4/Warehouse skeletons, ~60 call sites. Unchanged.
- **apps/eventbus/** — **shipped 2026-05-19 (Phase 2.1)**. DomainEvent outbox table + ULID generator + Envelope dataclass + emit() helper + 6 typed emit helpers + Celery beat dispatcher + 22 Phase 1 event names (booking + customer + master domains, §3.1 / §3.2 / §3.3) + NoopSubscriber default + signal-based wireup of `booking.created` only. 44 tests passing. Q-EV-IMPL1-5 → DECIDED (decisions-log r20). Phase 2.1 deviations documented in §18.

Phase 2.2 (next): real subscribers (billing / loyalty / retention / AI inference) wired by their owning modules; remaining domain events auto-emitted from their domain code as those modules touch the lifecycle.

---

## 15. What this unblocks

- **4a attribution backend** has canonical event names to emit
- **Schedule rebuild (PR A)** knows what events to fire on WorkingHours/TimeBlock/etc.
- **Master extension (PR B)** has master.* event names locked
- **Analytics dashboard** has subscriber contract
- **Loyalty / marketing** have firm event names to listen for
- **New modules** added in future can extend without renaming chaos

## 16. What this does NOT unblock

- ❌ Replace event-bus technology decision (Q-EV1 still open)
- ❌ Skip PII review on payloads (every PR with new event MUST be reviewed)
- ❌ Allow ad-hoc event emission outside this catalog
- ❌ Replace `apps/events/` (product analytics — stays per §14)
- ❌ Silently mirror events between buses via interceptor (§14.6 — explicit emission only)

---

## 17. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-18 |
| Backend lead | ☐ | |
| Analytics lead | ☐ | |
| Legal (PII rules §6) | ☐ | |
| Security (cross-tenant §8) | ☐ | |

---

## 18. Implementation deviations & transition concessions (r2, post-Phase-2.1-ship)

apps/eventbus/ Phase 2.1 shipped 2026-05-19. Five deviations from the as-designed policy are documented here per the [attribution-policy §15 pattern](./attribution-policy.md#15-implementation-deviations--transition-concessions-r3-post-4a). Each is classified ACCEPTED FINAL / TEMPORARY / DEFERRED, with the resolution path captured.

§14 (Scope separation from `apps/events/`) is the **architectural** decision and is not duplicated here. §18 covers Phase 2.1 **implementation** concessions only.

### 18.1 Phase 1 auto-wired events = `booking.created` only — TEMPORARY

**Deviation**: §3 catalog spec'd 50+ events across 10 domains; Phase 1 §3.1/§3.2/§3.3 lists 22 events across booking/customer/master. Phase 2.1 ship auto-emits only **one**: `booking.created` (via post_save signal on `BookingRequest`). All other 21 Phase 1 events are exposed as typed emit helpers in `apps/eventbus/services.py` but have no call-site wireup.

**Why**: status-transition events (`booking.cancelled` / `booking.completed` / `booking.rescheduled`) need service-layer diff detection — that's the cancellation/reschedule PR's scope, not the bus PR's. `customer.*` events need identity / consent bridges still being designed. `master.*` events need Master models that don't exist yet (Phase 2 master CRUD).

**Resolution path**: per-domain auto-wire ships in PRs that touch those lifecycle modules:
- `booking.cancelled` / `booking.rescheduled` → customer-cancellation-reschedule handoff implementation (handoff exists, Q-CR1-15 closed)
- `booking.attribution.assigned` → 4a attribution backend follow-up
- `booking.completed` / `booking.no_show` → YClients webhook + reminder factory (Q-ATT-IMPL7)
- `customer.consent.changed` → consent module audit-event integration
- `customer.state.changed` → core-user-states FSM module (deferred per Q-US pending integration)
- `master.*` → master-management implementation (Q-MM open)

**Risk**: subscribers built against future events see no traffic until the auto-wire PR ships. Acceptable; Phase 2.1 NoopSubscriber means no observable side-effect waiting on auto-wire anyway. Documented in Q-EV-IMPL2.

### 18.2 Phase 2.1 subscriber set = NoopSubscriber only — TEMPORARY

**Deviation**: §4 lists real subscribers per event (analytics, audit, AI inference, loyalty, marketing, billing, slot resolver). Phase 2.1 ships with **only** `NoopSubscriber` registered in `apps/eventbus/dispatcher.py::_subscribers()`.

**Why**: real subscribers belong to their owning modules (billing/loyalty/retention/AI), and those modules need their own integration work. Shipping the bus infra first lets each subscriber land independently without blocking on a monolithic «subscriber framework» PR.

**Resolution path**: Phase 2.2 — each real subscriber lands in its own PR per [§10 «Adding a new event»](#10-adding-a-new-event) procedure (extended to «adding a subscriber»: subscriber + tests + settings registration). Subscriber registration moves from hard-coded list to dotted-path-in-settings (`DOMAIN_EVENT_SUBSCRIBERS`) when the second real subscriber lands.

**Risk**: outbox accumulates rows that get dispatched to Noop only. Storage cost is bounded (taxonomy §5 90-day retention applies; cleanup task ships with Phase 2.2 subscribers). Acceptable in Phase 2.1.

### 18.3 ULID — inline implementation, no new dependency — ACCEPTED FINAL

**Deviation**: §2 requires ULID `event_id` but does not dictate a library. Phase 2.1 ships an inline ULID generator (`apps/eventbus/ulid.py`, ~30 LOC) instead of adding `python-ulid` to `pyproject.toml`.

**Why**: avoid a new runtime dependency for a 30-line algorithm. The Crockford-base32 ULID format is a public spec; our implementation produces compliant 26-char IDs.

**Resolution path**: ACCEPTED FINAL. If ULID requirements grow (strict monotonicity within same ms, parsing utilities, timestamp extraction APIs), revisit and swap to `python-ulid` then.

**Risk**: strict monotonicity within the same millisecond is NOT guaranteed (random suffix per call; no monotonic counter). Outbox FIFO order is correct across millisecond boundaries; same-ms collisions sort arbitrarily. Acceptable — dispatcher does not depend on intra-ms ordering.

### 18.4 PII §6 enforcement asymmetry vs `apps/events/` — ACCEPTED FINAL

**Deviation**: §6 says «forbidden in event payloads». `apps/events/` (analytics) implements §6 as warn-and-still-insert (telemetry never drops). `apps/eventbus/` (domain) implements §6 as **REJECT** — `emit()` raises `EventbusPiiViolation` and no row is written.

**Why**: the two buses have different blast radii. Analytics events flow to external warehouses where rotation/redaction is easier; domain events feed durable internal subscribers (billing, retention, audit), where PII contamination is a legal-grade issue. REJECT for the domain bus is the correct asymmetry.

**Resolution path**: ACCEPTED FINAL. Both buses honor §6 by intent — only the severity of the response differs. Engineering review rule (§14.5) catches violations at PR time; runtime REJECT is the last-line defense.

**Risk**: developer hits unexpected REJECT in production and event is lost. Mitigation: PII heuristics are conservative (forbidden key list + phone-with-+-prefix value heuristic + email regex); false positives unlikely on well-shaped payloads. Documented in test suite (`tests/test_validation.py::TestLintPii`).

### 18.5 Dead-letter — RESOLVED (Phase 2.2 PR-A)

**Original deviation (Phase 2.1)**: §5 mentions a DLQ with engineering alerting and manual triage. Phase 2.1 dispatcher implemented dead-letter as «row stops being re-claimed after `dispatch_attempts >= 3`» — no separate field, no alerting, no replay surface.

**Resolution shipped 2026-05-19 (Phase 2.2 PR-A)**:
- `DomainEvent.dead_lettered_at` field added (migration `0002_domainevent_dead_lettered_at`) + `is_dead_letter` property
- Dispatcher claim query: `is_dispatched=False AND dead_lettered_at IS NULL` (cleaner than attempts-based filter)
- On threshold crossing: `dead_lettered_at = now()` set explicitly inside the same transaction; dispatcher then emits `system.module.health.degraded` event (taxonomy §3.10, tenant-less per §8) with `metric=dlq_count_in_run=<N>` AFTER commit
- Admin: `DeadLetterFilter` + bulk «Replay selected dead-letter events» action calling `replay_dead_letter(event_ids)` which resets `dead_lettered_at=None`, `dispatch_attempts=0`, `last_error=''`
- `system.module.health.degraded` added to vocabulary (catalog §3.10 first entry) with payload `{module_name, severity, metric}`

**Tests**: 11 dispatcher tests covering happy path, retry, threshold crossing, DLQ exclusion from claim, replay idempotency, replay re-claim, alert emission, clean-run no-alert.

**Status**: ACCEPTED FINAL. No longer a deviation.

---

## Last verified
2026-05-19 r3 — Phase 2.2 PR-A shipped (DLQ + replay + health-degraded alert); §18.5 status RESOLVED. Earlier: 2026-05-19 r2 (Phase 2.1 ship), 2026-05-18 r1 (initial draft, catalog locked).
