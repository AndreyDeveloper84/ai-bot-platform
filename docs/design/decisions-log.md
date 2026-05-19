# Decisions Log

**Single source of truth** for product/design decisions across the platform. When in doubt about a decision's current status, this file wins over any other doc.

## How to use

- **Designer/eng/PM** reads this file before making implementation choices that depend on an open question
- **PM/founder** owns moving questions from Open → Decided as they're resolved
- **New questions** added here when they emerge during design/eng — same ID/owner/urgency convention
- **Source docs** keep their original sections but point here as the authoritative status
- **Sync rule**: this log is authoritative. Source docs may be stale — trust this file.

## ID conventions

ID prefix indicates origin area:
- `Q1`–`Q17` — onboarding cascade (Q1-Q9 cascade, Q10-Q17 new)
- `Q-C1`–`Q-C10` — Conversations module initial 10
- `Q-CO1`–`Q-CO5` — Conversations module raised after r1 review
- `LQ1`–`LQ7` — Learning Queue (Screen C4)
- `P1`–`P4` — Assistant persona
- `OP1`–`OP7` — Conversation ownership policy
- `V1`–`V4` — Validation tasks (hypothesis tests, not decisions)
- `Q-CX*` — Customer first-time UX
- `Q-M*` — Master-mobile handoff
- `Q-AD*` — Analytics dashboard
- `Q-PE*` — Persona editor
- `Q-SC*` — Schedule Management handoff (design questions)
- `Q-SC-IMPL*` — Schedule Management implementation questions (Phase S1 backend)
- `Q-MM*` — Master Management handoff
- `Q-L*` — Loyalty system
- `Q-WI*` — Wellness Input Modules (food/water/body/sleep/mood/avatar/symptom)
- `Q-EV*` — Event Taxonomy
- `Q-CV*` — Conversational UX Framework (customer-side)
- `Q-MC*` — Master-conversational templates
- `Q-OC*` — Owner-conversational templates
- `Q-MB*` — Manual Booking spec
- `Q-SW*` — Schedule editor wireframes (S2 owner editor + S3 master mobile)
- `Q-ATT-IMPL*` — Attribution implementation (Phase 4a post-ship questions)
- `Q-PERF*` — Performance / scalability concerns
- `Q-EV-IMPL*` — Event bus implementation (apps/eventbus/ — domain bus separate from apps/events/ analytics)
- `Q-WM*` — Wellness Mood module handoff
- `Q-CR*` — Customer cancellation + reschedule spec
- `Q-FT*` — Customer first-touch flow (entry sources + classification)
- `Q-MAS*` — Mini App states catalog (loading/empty/error/offline patterns)
- `Q-MO*` — Master onboarding M0-M7 flow
- `Q-NP*` — Notification preferences UX (customer/master/owner)
- `Q-IA*` — Information Architecture (pending integration)
- `Q-WP*` — Wellness Profile (pending integration)
- `Q-US*` — Core User States (pending integration)
- `Q-UJ*` — User Journeys (pending integration)
- `DL*` — Decisions Log meta (about the log itself)

## Status legend

- 🔴 **Critical** — blocks ship of MVP or a specific phase
- 🟡 **Soon** — needed within sprint or before a specific milestone
- 🟢 **Later** — can defer to v1.1+ or after launch
- ✅ **Decided** — locked, see Decisions section
- 🔬 **Validating** — hypothesis being tested

---

## OPEN — sorted by urgency, then by area

### 🔴 Critical (block ship)

| # | Question | Status / blocker | Owner | Source |
|---|---|---|---|---|
| **Q-WI6** | AI Avatar — master/practitioner access to specific zone photos require customer consent grant? | Lean: YES always explicit grant, audited; no implicit master access | Legal + PM | [wellness-input-modules §15](./policies/wellness-input-modules.md) (blocks AI Avatar ship Phase 3) |
| **Q-MB1** | Manual Booking — consent checkbox required or optional in new-customer modal? | Lean: REQUIRED — must be explicitly checked OR explicitly «no contact» selected. Prevents legal exposure on spam complaints. | Legal | [manual-booking §18](./policies/manual-booking-spec.md) (blocks S5 ship) |

### ⚠️ Conflict detected (2026-05-18 r11 Quality Gate sweep)

| Conflict | Files in conflict | Resolution |
|---|---|---|
| **`attributed_to_bot` legacy binary field still referenced** | [salon-onboarding-handoff §Q9/Q12/checklist](./handoffs/2026-05-17-salon-onboarding-handoff.md) 5 occurrences; [customer-first-time-handoff §F2 feedback API](./handoffs/2026-05-18-customer-first-time-handoff.md) 1 occurrence | Schema decision Q12 (r3) REJECTED binary `attributed_to_bot:bool` in favor of 5-enum `booking_source`. These handoffs predate the decision and weren't refreshed. **Per README «policy wins on conflicts» rule** — engineering follows [`attribution-policy.md`](./policies/attribution-policy.md). Action: handoff refactor when next-touched (low priority because attribution-policy is canonical source). Pending: PM owner refresh handoffs or add «superseded fields» note inline. |

### 🟡 Soon

> 11 of 12 new 🟡 closed in r6 batch. Q-M4 remains — legal-blocked.

| # | Question | Status / blocker | Owner | Source |
|---|---|---|---|---|
| **Q-M4** | Aftercare notes retention — same 180d as transcripts or longer for clinical-relevant (patch test results, allergy notes)? | **Legal-blocked**: batch with Q-C3 retention legal review. RU юрист must confirm per ФЗ-152 + healthcare data scope. | PM + Legal | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-PE1** | Persona change scope — apply to in-flight conversations or only new? | Lean: atomic swap per next-message boundary; in-flight LLM completions finish with old persona | Eng + PM | [persona §13](./handoffs/2026-05-18-persona-editor-handoff.md) |
| **Q-PE6** | Preview-as-customer LLM calls — count against tenant inference cost or platform-comp? | Platform-comp MVP (small cost, helps tuning); revisit at scale | Founder | [persona §13](./handoffs/2026-05-18-persona-editor-handoff.md) |
| **Q-PE8** | «Никогда» explicit-human policy — legal sign-off on owner's risk acknowledgement? | Owner ticks ack checkbox at modal; legal language drafted by RU юрист (batch with Q-C3) | Legal | [persona §13](./handoffs/2026-05-18-persona-editor-handoff.md) |
| **Q-AD2** | Show billing projection («ожидается N ₽ к концу месяца»)? | Show but Owner only; conditional on >10 days data (low-confidence hidden) | PM | [analytics §21](./handoffs/2026-05-18-analytics-dashboard-handoff.md) |
| **Q-AD4** | Master sees own analytics — own dashboard subset of Analytics or separate from master-mobile §M3? | Own row + KPI subset from Analytics; full owner dashboard inaccessible to master | PM | [analytics §21](./handoffs/2026-05-18-analytics-dashboard-handoff.md) |
| **Q-AD6** | Timezone — tenant configured or browser? | Tenant configured (`Tenant.timezone`); banner if browser differs > 1h | Eng | [analytics §21](./handoffs/2026-05-18-analytics-dashboard-handoff.md) |
| **Q-AD7** | Dashboard refresh — WebSocket or polling? | Pull every 5 min for KPIs; on-demand for charts. No WS overhead. | Eng | [analytics §21](./handoffs/2026-05-18-analytics-dashboard-handoff.md) |
| **Q-SC2** | Slot granularity — 15 / 30 min / configurable? | 15 min default; configurable per-tenant v1.1 | PM + Eng | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC5** | Master self-mark «сегодня болен» via bot DM without owner approval? | YES with audit + auto-notify; limit 3/quarter before requires owner approval | PM | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC6** | Auto-reassign bookings on master exception — same service same time other master? | Offer choice in exception modal; if «Перенести», ask customer first via bot DM | PM + UX | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC10** | Block time can affect multiple masters at once («корпоратив весь салон»)? | YES — multi-master select OR «весь салон» quick action | PM | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC11** | Template→YC migration of existing manual data — what happens? | Migrate where possible; flag unmapped; side-by-side reconcile view | PM + Eng | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC12** | Master vacation request flow vs sick — same as Q-M6 or separate? | YES via S6 change-request flow (same as Q-M6); sick = self-mark (Q-SC5); vacation = owner approval | PM | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-MM1** | YC-sync conflict on field with owner local edit — who wins? | Per-field opt-in lean (option c); needs founder policy call | Founder + Eng | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM4** | Hard-archive (terminal delete) — needed in MVP or INACTIVE-forever sufficient? | Remove hard-archive MVP; align with «no data loss»; GDPR via OP6 | Founder + Legal | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM6** | Multi-tenant master — warning «уже в другой студии» when adding by MAX handle? | Founder/legal call: privacy (other tenants) vs trust (collision prevention) | Founder + Legal | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM12** | Mini App parity vs web-only — costs ~30% extra dev | MM1+MM3 (read-mostly) Mini App parity; MM2/MM4/MM5 (edit-heavy) web-only with nudge | Founder | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM13** | Audit retention for `master.*` events — Layer 2 or Layer 3? | Layer 2 (365d) for most; Layer 3 (7y) for `master.role_changed` only | Legal | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-L1** | Loyalty default enabled OR opt-in for new tenants? | Opt-in MVP; enabled=false at tenant creation; banner suggestion after first 50 bookings | PM | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) |
| **Q-L5** | Loyalty enrollment automatic on first booking or explicit opt-in? | Automatic (no friction); customer can opt-out in profile preferences | PM | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) |
| **Q-WI1** | Food scanner ML — build in-house or 3rd-party (Foodvisor, Google Vision)? | 3rd-party MVP (cost + speed); revisit at 1000+ daily scans | Eng | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-WI4** | Sleep tracking wearable integration — Phase 4 or earlier on customer demand? | Customer-requested → accelerate; default Phase 4 | PM | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-WI7** | AI Avatar AI commentary — should it ever say «no visible change»? | YES — honesty mandate; frame as «изменения тонкие — продолжайте текущий курс» | PM | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-WI8** | Symptom diary — escalation to medical specialist auto-trigger thresholds? | Pain >7/10 chronic OR sudden severe → suggest medical consult + HUMAN_LOCKED | PM + Legal | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-WI10** | Customer who deletes account (OP6) — what happens to all module data? | Anonymized soft-delete 30d → hard-delete; AI Avatar photos hard-deleted immediately on customer request | Legal | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-WI11** | Modules during salon's free trial / unpaid state? | Modules belong to CUSTOMER, not salon — customer keeps even if salon downgrades | Founder | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-EV1** | Event store technology — Kafka, Postgres outbox, EventBridge, custom? | Postgres outbox MVP (low ops); evaluate Kafka at 1M+ events/day | Eng | [event-taxonomy §12](./policies/event-taxonomy.md) |
| **Q-EV2** | Per-event PII review — automated linter or manual? | Automated lint on payload schema; reject PR if forbidden field in `data` | Eng | [event-taxonomy §12](./policies/event-taxonomy.md) |
| **Q-EV4** | Subscriber idempotency — enforce via dedup table or rely on subscriber? | Dedup table at infra level (every subscriber gets cheap idempotency) | Eng | [event-taxonomy §12](./policies/event-taxonomy.md) |
| **Q-EV6** | Customer-facing events (e.g., for export per OP6)? | Customer can request JSON export of own events; redacted version | Legal + PM | [event-taxonomy §12](./policies/event-taxonomy.md) |
| **Q-EV8** | Webhook delivery to tenant systems (YClients) — replay-safe? | Curated subset of events via webhook with HMAC + idempotency key | Eng | [event-taxonomy §12](./policies/event-taxonomy.md) |
| **Q-EV9** | `conversation.message.sent` per-message or batched? | Per-message; manageable with proper partitioning | Eng | [event-taxonomy §12](./policies/event-taxonomy.md) |
| **Q-CV1** | Tenant can add custom templates outside catalog? | NO MVP — drift risk; v1.2+ tenant overlay reviewed by UX | UX | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-CV4** | Voice messages from customer — transcribe + audio response? | MVP transcribe + respond in text; v1.1+ audio if tenant-enabled | PM | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-CV5** | Negative review templates — full handoff or AI deescalate? | AI acknowledges + escalates immediately; no AI deescalation attempt | Policy | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-CV9** | AI making mistakes — apology template? | YES — acknowledge specific error + offer fix; never deflect | Policy | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-MC1** | Voice messages from master allowed? | YES — transcribe + execute; same density rules | PM | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-MC3** | DND queueing + bundling for missed booking pings? | Queue + bundle into next allowed window; never silently drop | UX | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-MC6** | Master emotional support («устала, не хочу») — how to respond? | One acknowledgment + route to non-AI resource if expressed clearly; never long emotional dialog | Policy | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-MC8** | Master sees customer's last assistant message verbatim? | NO — summary only; full thread requires owner approval | Policy | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-OC2** | Owner asks AI to draft message TO master in master's voice — allowed? | AI drafts, owner sends from own DM; AI never impersonates owner-to-master | Policy | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-OC3** | When owner is also a master (small salon), which voice context? | Context-dependent: customer/staff data → owner-tone; own schedule → master-tone | UX | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-OC7** | AI sees customer complaint patterns across masters — share rankings? | Surface aggregate facts to owner; never rank publicly | Policy | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-OC8** | When owner is CHURN_CANDIDATE — should AI try to retain? | NO retention spam; one «расскажете что не подошло?» + respect silence | Policy | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-OC10** | Voice messages from owner allowed? | YES — transcribe + execute same patterns | PM | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-OC11** | AI confidence display on insights — when «низкая уверенность»? | Window <7 days OR sample <20; mark «(данных мало)» | UX | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-MB2** | Manual booking — MAX handle doesn't exist on MAX, silent fail or error? | Silent fail (queue, log delivery failure event, surface in admin dashboard) | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB3** | Bootstrap message — disclose admin's name or stay generic? | Disclose admin name (warmth + trust); generic if admin opted out | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB10** | Admin types customer name matching multiple — disambiguation UX? | Show ≤5 matches with last-visit + phone-tail; admin picks; if 0 → «+ Новый» | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB11** | YClients sync — booking cancelled in YC, what happens here? | Sync deletion → `booking.cancelled` event with `cancelled_by = external_system`; customer notified per consent | Eng | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-ATT-IMPL1** | Port 5+ legacy writers (admin webhook, bot tools, reminders factory, YC sync, manual admin entry) to explicit attribution model — when? | Phase 1 / 4c or Phase 2; until ported, validator skip for `booking_source='external'` (per attribution-policy §15.1). When all writers send explicit `actor_type` + `booking_source`, flip validator to strict-always. | Eng | [attribution-policy §15.1](./policies/attribution-policy.md) |
| **Q-ATT-IMPL2** | `compute_ai_assisted_score(conversation_ctx)` helper — when written and where? | Add to `apps/booking/services/attribution.py` when first `ai_assisted` writer ships. Heuristic per attribution-policy §5 (tool_calls_count + bot_replies_count + human_replies). Writer owns the call; service stays decoupled from `apps/conversations`. | Eng | [attribution-policy §15.2](./policies/attribution-policy.md) |
| **Q-ATT-IMPL3** | `visit_at` validator — require for non-external bookings? | YES — add validator: if `booking_source != 'external'` AND `visit_at IS NULL` raise. External rows allowed NULL until Q-ATT-IMPL7 ports YC webhook. Add `test_visit_at_required_for_non_external_writers`. | Eng | [attribution-policy §15.4](./policies/attribution-policy.md) |
| **Q-CR7** | Reschedule cap override — only owner or also admin? | Admin with `permission.schedule.override` (per owner-templates §14 admin variants) | Policy | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR11** | `custom_hours` ScheduleChangeRequest — cascade only on bookings in non-overlapping window? | YES — bookings inside new working window stay CONFIRMED; only outside-window bookings cascade | Eng + UX | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR14** | Cancel-then-rebook within 1h same customer/master/service/slot — anti-abuse handling? | Soft-detection: don't refund original cancel (it wasn't a real cancel); mark `attribution_metadata.likely_misclick = true`. Prevents cancel-rebook to dodge billing. | Eng + Policy | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-FT2** | First-touch — include customer's MAX-known name if state ≠ DISCOVERED? | YES if state ≠ DISCOVERED. NO on DISCOVERED (don't reveal name pre-introduction; feels surveillant). | UX + Privacy | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-FT6** | Referral attribution — credit on arrival or only on booking? | Per Q-CX7: only on booking; arrival alone doesn't credit. Add `attribution_metadata.referred_by` at first-touch + `loyalty_referral_triggered_at` on booking. | PM | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-FT8** | First-touch via bot DM or direct Mini App deeplink? | Bot DM always first — establishes persona + identity. Then customer taps button to enter Mini App. Direct-to-Mini-App skips trust-building. | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-FT10** | Source 8 CRM reactivation — suppress blast if customer in HUMAN_LOCKED conversation? | YES suppress per ownership-policy HUMAN_LOCKED priority over AI proactive. | PM | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MAS4** | Offline queue conflict — customer cancelled offline but already cancelled by other path? | Toast «Эта запись уже отменена. Если что не так — напишите студии» + dismiss queued action. | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MAS9** | Sync queue persistence — survives Mini App close / refresh? | Persists 24h via localStorage; cleared on explicit sync OR 24h timeout. | Eng | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MO4** | Bio AI-suggestion based on services + invite metadata? | YES generate suggested text in wizard; master edits or accepts | UX + AI | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO12** | Master uses «Расскажу помощнику» (escape hatch for service not in AI's proposed list) — what happens? | AI captures free-text → owner approval queue (NOT auto-add); never master-self-serve catalog edit. Per project_salon_catalog_vertical AI-first principle. | PM | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO13** | Onboarding analytics for founder cohort — which metrics? | Time-to-M4 (median, p90), time-to-M7, drop-off rate per stage, photo/bio completion rates | PM + Analytics | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO15** | First booking by owner/admin (test_admin) — counts as M5 transition? | NO — M5 fires only on customer-initiated first booking (actor_type='customer'); test_admin bookings don't transition | Eng + UX | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO16** | AI pre-checks 2-3 services for primary_specialty — which? | Platform-level vertical-template defaults (e.g., массажист → classical + lymph; бровист → окрашивание + ламинирование). Per-tenant override if catalog skews. Track usage analytics for adjustment. | PM + AI | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-NP5** | Master DND respect both `WorkingHours` AND `ScheduleException`? | YES — vacation = full DND; customer-related notifications batched to return date | Eng + UX | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP7** | Quiet hours for customer = customer TZ but tenant TZ might differ — which for delivery? | Customer TZ for delivery scheduling; tenant TZ only for business-context timestamps | Eng | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP8** | Frequency cap exceeded — drop or queue? | Queue (per §6.2) — never silent drop | Eng | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP11** | Customer toggles «без проактивных» mid-conversation in flight — affects current? | NO — current conversation continues; new state applies to NEXT proactive trigger | UX | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP15** | Audit log retention for preference changes — Layer 2 (365d) or Layer 3 (7y)? | Layer 2 for most; Layer 3 for operational-class re-enable (compliance traceability) | Legal | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP16** | Migration path for existing customers — default retroactively or behavior-based? | Default settings retroactively; behavior-based adds privacy risk + complexity | Eng + Policy | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP17** | Tenant suspended (billing failed) — customer preferences still honored for queued reminders? | YES — operational reminders for existing bookings continue; only new dispatch suppressed | Policy | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-EV-IMPL1** | Create new `apps/eventbus/` Django app for domain events (separate from `apps/events/` analytics)? | YES — locked decision (A) two-bus architecture per [event-taxonomy §14](./policies/event-taxonomy.md#14-scope-separation-from-appsevents-product-analytics). Phase 2 implementation; Phase 1 domain events remain spec-only | Eng + Founder | [event-taxonomy §14](./policies/event-taxonomy.md) |
| **Q-EV-IMPL2** | `apps/eventbus/` first MVP scope — which 3-5 domains to wire first? | booking + customer + master (highest billing/loyalty impact). Schedule + wellness deferred to second tranche. | Eng + PM | [event-taxonomy §14](./policies/event-taxonomy.md) |
| **Q-EV-IMPL3** | Outbox poller technology — Celery beat, dedicated worker, or pg-pubsub LISTEN/NOTIFY? | Celery beat MVP (already in stack); evaluate pg-pubsub or Kafka at 1M+ events/day per Q-EV1 | Eng | [event-taxonomy §14](./policies/event-taxonomy.md) |
| **Q-EV-IMPL4** | Wellness Mood module (handoff shipped 2026-05-19) — emit to apps/events/ analytics OR wait for apps/eventbus/? | Both — phase 1 wellness.consent.* + wellness.input.recorded fire ONLY to apps/events/ with snake_case names (`mood_consent_granted` / `mood_event_saved`) until eventbus lands. Migrate to dot.notation when eventbus ships. Document as deviation per attribution-policy §15 pattern. | Eng | [wellness-mood-handoff §10](./handoffs/2026-05-19-wellness-mood-handoff.md) + [event-taxonomy §14](./policies/event-taxonomy.md) |
| **Q-EV-IMPL5** | Cross-bus correlation — `correlation_id` shape — UUID, ULID, or trace-context (W3C)? | ULID MVP (sortable, compact); upgrade to W3C trace-context if/when OpenTelemetry adoption | Eng | [event-taxonomy §14.7](./policies/event-taxonomy.md) |
| **Q-ATT-IMPL5** | `conversation` FK population — Mini App deeplink parser source? | Parse `start_param` at `apps/miniapp_api/views.py` request ingestion (NOT in `apps/booking/services`). Three sources × three behaviors per attribution-policy §15.5. Bot tools (Q-ATT-IMPL1 port) MUST pass conversation. | PM + Eng | [attribution-policy §15.5](./policies/attribution-policy.md) |
| **Q-ATT-IMPL6** | Customer phone snapshot — MAX often returns empty phone; how to handle reminder factory? | Graceful skip in reminder factory if both `phone` AND `chat_id` missing. Log warning + emit `system.module.health.degraded` event. Customer/admin gets follow-up via [owner-templates §6.3](./policies/owner-conversational-templates.md). Tie to [manual-booking §3](./policies/manual-booking-spec.md) explicit «no contact» selection. | Eng | 4a surprising finding #1 |
| **Q-ATT-IMPL7** | YC webhook port — copy `visit_at` from BookingReminder → BookingRequest? | YES — add to Phase 1 / B2 yclients-webhook follow-up scope. Until ported, YC bookings remain `external` + `visit_at=NULL`; slot resolver excludes them; customer-facing impact = potential double-booking on YC-only flows (workaround: master cross-check via master mobile). | Eng | 4a surprising finding #5 |
| **Q-PERF-1** | Race-safety in `create_booking` — application-side re-check adds 2-3 DB queries per POST. Right answer? | DB-level partial index `UNIQUE (master_id, visit_at) WHERE status='confirmed'`, added concurrently (no lock). Application-side re-check stays as belt-and-suspenders during migration. Add to Schedule S5 or separate perf ticket. | Eng | [attribution-policy §15.7](./policies/attribution-policy.md) |
| **Q-SW2** | Multi-master weekly overlay (Все мастера) — readable threshold? | ≤4 inline colour-coded; >4 collapses to per-master chips | UX | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |
| **Q-SW3** | TimeBlock «Сделать регулярным обедом» heuristic threshold (3 lunches/14d) — MVP or defer? | Defer to Phase 2; MVP shows hint always when reason=обед | Eng | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |
| **Q-SW11** | YClients-connected salon's owner opens S2 — show pending ScheduleChangeRequests? | YES — change-requests are our-side concept; banner «Применятся к нашей надстройке, не пушим в YClients» | PM + Eng | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |

### 🟢 Later

14 new items: 12 from customer + master UX docs (Q-CX, Q-M prefixes) + 2 added r7 from Persona Editor (Q-PE2, Q-PE3, Q-PE4, Q-PE5, Q-PE7).

| # | Question | Recommendation / lean | Owner | Source |
|---|---|---|---|---|
| **Q-CX4** | Birthday data — required field or optional in profile? | Optional, asked once; no birthday touch if skipped | PM | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-CX6** | Retention timing per service — fixed (30d nails / 45d hair / 60d facials) or per-service customizable? | Fixed by category MVP, custom v1.1 | PM | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-CX10** | First-time questionnaire (preferences, allergies) before first booking? | Skip MVP; ask post-first-visit at profile prompt | PM | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-M3** | Master morning brief — opt-in or opt-out default? | Opt-in to avoid push-fatigue; suggest at onboarding step 3 | PM | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M5** | Multi-tenant master (works at 2 salons) — v1.1 or v2? | v1.1+; affects MAX user identity model | Founder | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M7** | New-salon master default — auto-create «не приглашён», explicit add, or YClients pre-fill? | Pre-fill from YClients sync, owner approves each | PM | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M8** | Master demote HUMAN_LOCKED tier? | Keep NO per ownership-policy §4 (audit-clean) | PM + Legal | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M9** | Master commission/tip in MAX — feasible (no native payments)? | Out of scope MVP; future external-link based | Founder | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M10** | Premium tier «Помощник Анна» framing for known returning customer? | NO — keep single-assistant invariant; premium doesn't override foundational identity | Founder (confirmed lean) | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M11** | Pull-to-refresh on master dashboard? | Yes, enable (salon WiFi unreliable) | UX | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M12** | Master bio max length — 280 chars or longer? | 280 forces concision; longer = CVs no one reads | PM | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M14** | Conversation visibility for master — today/week or all-time? | All-time for resolved; «active» tab shows requiring-attention | PM | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-PE2** | A/B testing of personas (50/50 split)? | NO MVP — risky for small samples; persona == brand. Defer v1.2+ | PM | [persona §13](./handoffs/2026-05-18-persona-editor-handoff.md) |
| **Q-PE3** | Multi-language persona (RU + KZ/BY)? | Defer per P3 (RU only MVP); per-language config when languages launched | PM | [persona §13](./handoffs/2026-05-18-persona-editor-handoff.md) |
| **Q-PE4** | Tenant can clone another tenant's persona? | NO — privacy + competitive risk per LQ3 principle | Founder | [persona §13](./handoffs/2026-05-18-persona-editor-handoff.md) |
| **Q-PE5** | Owner can lock persona (require 2FA for own changes)? | NO MVP — defeats UI purpose; 24h rollback is the recovery | PM | [persona §13](./handoffs/2026-05-18-persona-editor-handoff.md) |
| **Q-PE7** | Show persona effectiveness metrics in editor («CSAT 4.6 — выше среднего»)? | NO MVP — vanity risk + anxiety if scores drop. Surface in Analytics dashboard separately | PM | [persona §13](./handoffs/2026-05-18-persona-editor-handoff.md) |
| **Q-AD1** | Time period default — 7 days or 30 days? | 30 days — stable patterns, less noise | PM | [analytics §21](./handoffs/2026-05-18-analytics-dashboard-handoff.md) |
| **Q-AD3** | Cross-tenant benchmarking opt-in («вы в топ-20% Москвы»)? | Defer v1.1; privacy + competitive risk | Founder | [analytics §21](./handoffs/2026-05-18-analytics-dashboard-handoff.md) |
| **Q-AD5** | Show A/B test indicator to owner («вы участвуете в эксперименте»)? | NO MVP — keep mechanics behind scenes, owner sees results not experiments | Founder | [analytics §21](./handoffs/2026-05-18-analytics-dashboard-handoff.md) |
| **Q-AD8** | Print-friendly view? | NO MVP — PDF export covers in Phase 3 | UX | [analytics §21](./handoffs/2026-05-18-analytics-dashboard-handoff.md) |
| **Q-AD9** | Comparison overlay style — dotted line vs side-by-side? | Dotted for time charts, side-by-side for distribution | UX | [analytics §21](./handoffs/2026-05-18-analytics-dashboard-handoff.md) |
| **Q-AD10** | Insight dismissal — per-user or per-tenant? | Per-user (Owner dismiss, Admin still sees) | PM | [analytics §21](./handoffs/2026-05-18-analytics-dashboard-handoff.md) |
| **Q-SC1** | Default working hours for newly added master | 10:00–19:00 Mon-Fri, 11:00–17:00 Sat, closed Sun | PM | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC3** | Buffer time default | 5 min — quick prep without making slots sparse | PM | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC4** | Slot params per-master or salon-wide | Salon-wide MVP; per-master v1.1 if demand | PM | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC7** | Cancel-policy display in customer booking | YES — already in customer first-time §6 reminders | PM | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC8** | Working hours version history | YES audit, rollback v1.1 if demand | PM | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC9** | Multi-week recurring exception (e.g., «every Monday for 4 weeks») | NO MVP — manual per-date; recurring v1.1+ | PM | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-MM2** | Customer notification on master deactivation — fixed template or per-tenant? | Fixed + tenant-editable «причина» phrase only | PM + Persona | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM3** | Empty services-master matrix — bot behavior when master has no services? | Phase 4c onboarding gate («у Анны нет услуг — добавить?») + silent exclusion post-launch | PM + Eng | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM5** | Catalog-only → invited promotion later — MM3 detail has «Пригласить в MAX» button? | YES confirmed | PM | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM7** | YClients name change on re-sync — auto-rename / conflict / ignore? | Conflict surface in Settings → Sync Health (quiet drift = worst) | Eng | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM8** | Default schedule for chains/nighttime salons | MVP hardcode 10-19; per-tenant default in Settings v1.1+ | PM | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM9** | Master invite expiration — 7 or 14 days? | Pick a value; lean 14 for hospitality slowness | Founder | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM10** | Reactivation — restore previous services-mapping or re-empty? | Restore as-is (sketch); customer can re-edit if changed | PM | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-MM11** | Owner deactivation flow (ownership transfer) — design here or separate? | Separate doc — needs legal/billing depth | PM | [master mgmt §11](./handoffs/2026-05-18-master-management-handoff.md) |
| **Q-L2** | Tier downgrade — soft notify or silent? | Silent first 90d; soft message at 6mo; hard downgrade at 12mo | PM | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) |
| **Q-L3** | Points expiration — never, 12mo, 24mo? | NEVER MVP; revisit at 6-month data if hoarding >1000 unspent avg | PM | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) |
| **Q-L4** | Tier celebration message — bot DM, UI, or both? | BOTH MVP per persona voice | UX | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) ✅ confirmed |
| **Q-L6** | Per-master discount preferences — higher discount on favorite master? | NO MVP; single rule for all masters; v2 idea | PM | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) |
| **Q-L7** | Referral cap — 10/quarter or unlimited with diminishing? | 10/quarter HARD cap MVP; diminishing curve v1.1 if salons complain | PM | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) |
| **Q-L8** | Discount applied — still bill salon 100₽ per ai_direct? | YES — bot work happens regardless of customer-side discount (per attribution-policy) | Confirmed | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) ✅ |
| **Q-L9** | Anti-abuse detection thresholds — who tunes? | Eng initial; CSM signals false positives; founder for first 50 | Eng + CSM | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) |
| **Q-L10** | Loyalty dispute UI — extends HUMAN_LOCKED or new ticket type? | Extends existing; no new ticket | PM | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) |
| **Q-L11** | Gift cards — Volna 4 sub-feature or separate v1.1+? | Separate, defer (payment + legal + refund complexity) | Founder | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) |
| **Q-L12** | Customer opt-out from loyalty entirely | YES toggle in profile; existing points retained, no new earning, redemption allowed | PM | [loyalty §17](./handoffs/2026-05-18-loyalty-system-handoff.md) |
| **Q-WI2** | Water tracker — ml or стаканы (250ml)? | Both — stakan default, ml in settings | UX | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-WI3** | Body tracking — fixed list or customer custom? | Fixed 5 MVP (weight, waist, hips, chest, thigh); custom v1.1 | PM | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-WI5** | Mood — morning vs evening prompt timing? | Morning default; customer can toggle | UX | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-WI9** | Cross-tenant customer using modules at multiple salons — separate or merged? | Per Q-CO5: separate Wellness Profile per tenant | PM | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-WI12** | Some modules free vs premium tier (Phase 3)? | Free forever: Mood, Water, Body. Premium tier: AI Avatar, advanced ML Food | Founder | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-WI13** | Group features — share progress with friend/partner? | NO MVP; v1.2+ explicit opt-in 1-to-1 sharing only | PM | [wellness-input-modules §15](./policies/wellness-input-modules.md) |
| **Q-EV3** | Event retention window beyond 90d? | Hot replay 90d; cold archive 365d compressed | Eng + Legal | [event-taxonomy §12](./policies/event-taxonomy.md) |
| **Q-EV5** | Cross-tenant event aggregation for platform analytics? | Separate aggregation pipeline; events still tenant-scoped | Eng | [event-taxonomy §12](./policies/event-taxonomy.md) |
| **Q-EV7** | AI-emitted events — actor.id format? | `ai_persona_v{N}` where N = persona version | PM | [event-taxonomy §12](./policies/event-taxonomy.md) |
| **Q-EV10** | Test/staging events vs prod analytics? | `metadata.environment` tag; analytics filters by env | Eng | [event-taxonomy §12](./policies/event-taxonomy.md) |
| **Q-CV2** | Off-hours customer message — tone change? | Acknowledge time («поздно вечером»); same voice; reply latency note | UX | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-CV3** | Use customer name every message or sparingly? | Sparingly — first message after activation + emotional moments | UX | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-CV6** | Customer silent 30+ days but not yet AT_RISK — proactive touch? | NO — wait for AT_RISK signal; respect silence | UX | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-CV7** | Multi-customer chat (group)? | NO MVP; v1.2+ explicit group mode | PM | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-CV8** | Customer writes in slang/transliteration — mirror? | Mirror lightly, stay literary; don't slang back fully | UX | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-CV10** | Customer-pays tier (Phase 3) — AI tone differentiation? | Slight — premium unlocks more proactive insights, same voice | Founder | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-MC2** | Group chat (multiple masters + owner) — voice? | Slightly more formal; tag addressee; per-recipient routing | UX | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-MC4** | Master override voice of customer-facing messages «through them»? | NO — voice is platform-level; flag issue to UX | UX | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-MC5** | AI proactive schedule optimization to master? | NO MVP — feels intrusive; v1.2+ opt-in | UX | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-MC7** | Master in multiple tenants — single assistant identity across? | NO — each tenant's assistant separate (cross-tenant boundary) | Architecture | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-MC9** | AI tone when master submits 3+ change requests/week? | No tone change; pattern surfaces to owner separately | UX | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-MC10** | Holiday/weekend auto-adjust of digest? | Skip digest on master's `is_working=False` days | UX | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-MC11** | Master onboarding — suggest defaults or configure from scratch? | Defaults pre-applied (10-19 weekdays per onboarding Phase 4c); master adjusts | UX | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-MC12** | Master wants to leave platform — exit dialog tone? | Calm + respectful: «передам {{owner}}» + soft confirmation; no retention attempt | Policy | [master-conversational §14](./policies/master-conversational-templates.md) |
| **Q-OC1** | Owner can override voice envelope (e.g. allow exclamations)? | NO — voice envelope platform-level brand-safety; owner dials within envelope | UX | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-OC4** | Co-owner scenario (2+ owners) — separate or shared chat? | Each owner own DM; shared dashboard view; assistant respects last-acting-owner | UX | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-OC5** | AI proactively suggest pricing changes? | Surface observations («услуга X записывается на 90% — спрос есть»); never propose price | Policy | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-OC6** | AI gives personal-business advice («стоит ли нанять второго»)? | Surface data only; never decide | Policy | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-OC9** | Owner asks for cross-salon benchmarks? | NO MVP — privacy + competitive; v2+ anonymized opt-in | Founder | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-OC12** | Owner request to «найди тон лучше» — A/B compare? | YES — show 2 alternatives, owner picks; A/B runs only if owner opts | UX | [owner-conversational §16](./policies/owner-conversational-templates.md) |
| **Q-MB4** | Customer replies «кто это?» to bootstrap — same as «бот или человек?»? | Same template per conversational-ux §6.4 — «помощник студии — AI-ассистент» | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB5** | Manual booking after-hours — when does master see it? | Per master notification preference; default queued to next working window | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB6** | Walk-in anonymous customer — how slot occupied? | Anonymous booking blocks slot like any other; no customer_id | Eng | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB7** | Manual booking by master for OTHER master allowed? | NO — master scope is own bookings; admin required for cross-master | Policy | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB8** | Bulk-edit manual bookings? | NO MVP — single-record only; bulk v1.2+ with audit batch | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB9** | Customer asks AI for booking history — show manual ones too? | YES — single thread, single history; AI says «{{admin_name}} записал вас на …» | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB12** | Master sees indicator on manual vs AI booking? | Subtle indicator (e.g., 📞 icon for phone-source) | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB13** | Customer later opts in — does AI reach out about past visits? | NO automatic recap; AI engages on next interaction normally | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB14** | `slot_force_override` — should AI warn about overlap pattern? | YES — if 3+ overrides in 30 days for same master, surface insight to owner | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-MB15** | Walk-in matched to existing customer — show prior history? | YES if last_visit < 90 days; subtle context line «был у вас 3 раза» | UX | [manual-booking §18](./policies/manual-booking-spec.md) |
| **Q-ATT-IMPL4** | Backfill perf (migration 0004) — chunked iterator OK or RAW SQL UPDATE? | NOT BLOCKING current scale. Re-evaluate before prod migration touches 1000+ tenants. RAW SQL preferred at scale with idempotent WHERE. | Eng / DBA | [attribution-policy §15.6](./policies/attribution-policy.md) |
| **Q-CR1** | Undo window after cancel — 5 sec or longer (15 sec)? | 5 sec MVP — match standard mobile undo patterns | UX | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR2** | If late-cancel + non-billable from start — any «sorry» softening? | NO — non-billable is internal; customer same template either way | UX | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR3** | Tenant configurable «brag» on EVENT exception («Маша на тренинге в Москве»)? | NO MVP — privacy default; v1.1+ tenant toggle | UX + Policy | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR4** | Cancellation reason chips per customer state? | All same chips MVP; per-state variants v1.1+ | UX | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR5** | Reschedule allowed when master `is_active=False`? | NO — UI prevents selection; reschedule defaults to alternatives | Eng | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR6** | Customer asks to reschedule to TimeBlock-blocked slot (lunch)? | Resolver excludes blocked slots; if free-text request → offer nearest free per resolver | UX | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR8** | Cascade timeout 48h — fixed or per-tenant? | Fixed 48h MVP; v1.1+ tenant adjustable (24h–7d) | PM | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR9** | Auto-cancel-after-no-reply message tone — apologetic or neutral? | Neutral («не было ответа — отменила; будет нужно — пишите») | UX | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR10** | No-show check-in delivery time — fixed 9:00 or adaptive? | Fixed 9:00-10:00 customer TZ for MVP; adaptive Layer 5 Behavioral v1.2+ | UX | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR12** | Reschedule analytics for owner — surface in weekly digest? | YES per owner-templates §6.2; neutral metric, not «problem» framing | UX | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR13** | Mini App offline cancel — queue or fail? | Queue with sync-on-connect; «изменения ждут сети» toast per Q-SW12 | Eng | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-CR15** | Reschedule to a different SERVICE supported? | NO MVP — cancel + new booking instead; v1.2+ if demand | UX | [customer-cancellation-reschedule §14](./policies/customer-cancellation-reschedule-spec.md) |
| **Q-FT1** | First-touch — instant or 1s delayed (typing indicator)? | Immediate with «typing...» indicator for 1s — feels human, less canned | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-FT3** | Repeat QR scan within 24h — send any message? | One message (§4.1 variant); then silent 24h | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-FT4** | Phase 2 IG post→service mapping — manual or auto-detect? | Manual MVP (tenant adds links with start_param); v1.2+ auto via IG API | PM + Eng | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-FT5** | Malformed start_param — fallback or silent error? | Fallback to source 5 generic; log + alert eng; never expose to customer | Eng | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-FT7** | Referral arrives but customer already tenant — acknowledge referrer? | NO — they're already our customer; process as returning per §5 | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-FT9** | DORMANT-LIGHT state between DISCOVERED-no-reply and full DORMANT — needed? | NO MVP; revisit if analytics shows confused 7-30-day silent resurrection | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MAS1** | Skeleton minimum display time — 200ms or 300ms? | 200ms MVP per industry standard; revisit on user testing | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MAS2** | Different empty state for «new tenant zero catalog» vs «filter zero results»? | YES — different copy. First: «У {{salon}} пока нет услуг»; second: «По вашему запросу ничего не нашлось» + reset filters CTA | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MAS3** | 5xx «Сообщить студии» CTA — open admin DM or pre-filled error message? | Pre-filled («У меня не работает Mini App, экран X») — gives admin context | UX + Eng | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MAS5** | Auto-retry on network error — silent or visible? | Silent first attempt (200ms-2s); explicit error UI on second fail | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MAS6** | RTL skeleton mirroring? | YES Phase 5+ with full RTL; MVP RU LTR only | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MAS7** | Disabled state tooltip explaining why? | YES on tap (mobile) / hover (desktop); accessibility win | UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MAS8** | Stale threshold — 5min or category-specific? | Per category: transactional 5min (slots); catalog 30min (less change-sensitive) | Eng + UX | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MAS10** | Maximum sync queue depth cap? | Cap at 5 queued mutations; over → reject with «слишком много несохранённых изменений» | Eng | [customer-first-touch §16](./policies/customer-first-touch-and-mini-app-states.md) |
| **Q-MO1** | M0 → M1 expiry — fixed 14 days or per-tenant? | Fixed 14 days MVP per Q-MM9; per-tenant v1.1+ | PM | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO2** | Bot DM nudge if master accepts but doesn't open Mini App in 24h? | YES — one nudge at 24h post-M1; silent after | UX | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO3** | Photo via Mini App camera OR gallery? | Both — invokes MAX webview file picker; covers both | Eng | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO5** | Tip cards rotation — fixed 3 or tenant-configurable? | Fixed 3 platform-level MVP: ScheduleChangeRequest / sick-self-mark / arrival-ping. v1.2+ tenant-customizable. | UX | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO6** | M3 schedule confirm — explicit «Подходит» tap required even if no changes? | YES — explicit confirm. Prevents passive acceptance + later disputes. | UX | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO7** | M5 «{{owner}} держит кулаки 🤞» line — appropriate? | One-time contextual. Remove if user feedback shows cringe. | UX | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO8** | M7 digest delivery time — fixed or adaptive? | Master's local 9:00 (aligned with morning digest pattern §6.4 of master-conversational-templates). | UX | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO9** | Owner notified on each onboarding stage transition? | NO MVP — aggregate widget §12 sufficient. v1.1+ opt-in per-master alerts. | UX | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO10** | Mass-onboarding (chain salon 10+ masters) — different UX? | Same per-master MVP; aggregate widget for owner §11.10. v1.2+ batch tools. | PM | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO11** | Solo master = owner case — M7 digest content? | Combined «Первая неделя студии» with owner partner-tone; skip master-tone variant | UX | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO14** | M7 fires even if no bookings in 7 days post-M4? | YES — calendar-driven not activity-driven. «Пока тихо» neutral framing. | UX | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-MO17** | Master rejects all AI pre-checked services — pre-check 0 and start blank? | YES — all unchecked; gentle prompt «Не подошло из стандартных? Что обычно делаете?» → free-text → owner approval per Q-MO12 | UX | [master-onboarding §16](./policies/master-onboarding-m0-m7.md) |
| **Q-NP2** | Wellness module toggles — show all or only activated? | Show ALL with state; turn ON requires activation flow with consent dialog; turn OFF direct | UX | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP3** | M2 wizard notification settings — full review or «defaults applied»? | Defaults + brief mention; full review on master initiative | UX | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP4** | Customer DND default 22-9 — per-tenant or platform fixed? | Platform fixed MVP (consistent CX); per-tenant v1.2+ | PM | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP6** | Owner DND tiered VIP escalation — MVP? | NO MVP — single owner DND + urgent exemption; v1.2+ tiered VIP | UX | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP9** | Master «не отключаются» list customer-facing copy? | Show explicitly «Не отключаются:» list — transparency | UX | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP10** | Aggregate stats for owner («N of M masters opted in») — surface? | Phase 2+; MVP not surfaced (privacy + low operational value initially) | PM | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP12** | Owner override customer «без проактивных»? | NEVER — customer consent absolute. Owner sees aggregate metric only | Policy | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP13** | Settings UI search bar MVP? | Phase 2+; MVP linear scan sufficient | UX | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-NP14** | Preference rate-limit (anti-flapping)? | YES — max 10 changes/hour/user; over → 30min cooldown | Eng | [notification-preferences §16](./policies/notification-preferences-ux.md) |
| **Q-SW1** | S2 default landing tab on first open after onboarding — Weekly grid or Working-hours editor? | Weekly grid if any master has hours set; else Working-hours editor for first-unset master (auto-route to setup task) | PM + UX | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |
| **Q-SW4** | Master quick-action «Я болен сегодня» reachable from where besides schedule tab? | Also from M1 dashboard top-card («Сегодня 6 клиентов · [🏥 не выхожу]») | PM + UX | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |
| **Q-SW5** | Master self-sick quarter counter — visible always or only near limit? | Always visible in W3-E modal; not in main schedule view (avoid stigma) | UX + PM | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |
| **Q-SW6** | ScheduleChangeRequest with conflicting bookings — who proposes customer reshuffle? | Owner decides (per Q-M6); master sees count only, not customer details (privacy + reduce decision-burden) | UX | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |
| **Q-SW7** | W2-D Working-hours editor — copy-row-to-row («apply Mon to all weekdays»)? | YES — `[⋯]` per row → menu «Применить пн–пт» / «Применить на все дни» | UX | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |
| **Q-SW8** | SlotConfig — `slot_granularity_minutes` visible to Admin read-only or hidden? | Visible read-only with «Только владелец может изменить»; transparency > confusion | PM | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |
| **Q-SW9** | Withdraw own ScheduleChangeRequest — owner's notification disappears or audit trail? | Owner sees «Запрос отозван мастером» note; do NOT silently disappear | PM | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |
| **Q-SW10** | Master sick-self-mark — surface in owner's MAX bot DM immediately or batch with morning brief? | Immediate (sick is operational); uses §6.5-style real-time escalation template | UX | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |
| **Q-SW12** | Mini App offline edit queue — how long hold queued mutations before warning? | 60s soft toast; 5min persistent banner; 24h drop with notification | Eng | [schedule-editor-wireframes §9](./policies/schedule-editor-wireframes.md) |

---

## 🔬 VALIDATION in progress

Hypotheses being tested before locking decisions. Not decisions themselves.

| # | Hypothesis | Method | Owner | Due | Source |
|---|---|---|---|---|---|
| **V1** | Hybrid 590+100 vs fixed 3 990 — какая модель **понятнее, честнее, вызывает больше доверия** | 5–10 sales-calls с **нейтральными** вопросами (НЕ «нравится ли вам X?»). Spec questions: «какой вариант понятнее?», «какой кажется честнее?», «где видите риск?», «при каком объёме записей модель становится дорогой?» | Founder | Pre-launch | [pricing memory](~/.claude/projects/.../memory/project_pricing_model_hybrid.md) |
| **V2** | Attribution для bot-driven bookings достижимо с **≥95% precision** (ужесточено с 90%) | Engineering feasibility audit + 50–100 historic cases manual classification → compare с auto-classifier. **Если precision <95% → automated per-booking billing нельзя включать; временно manual invoice review.** | Engineering | Before automated billing | [attribution-policy](./policies/attribution-policy.md) |
| **V3** | Competitive landscape — **на чём дифференцируемся, не «у кого сколько стоит»** | Sign up для demo + price-page review: Salebot Beauty, Boterra, Cleversite Bot, YClients add-ons, Telegram bot builders, AI assistants for salons. Document: что входит, есть ли AI/CRM/integrations/onboarding, какая модель монетизации. **Output**: positioning matrix, не price list | Marketing | Pre-launch | [pricing memory](~/.claude/projects/.../memory/project_pricing_model_hybrid.md) |
| **V4** | Unit economics при hybrid sustainable (LTV/CAC ≥ 3, payback < 6 мес, gross margin ≥ 70% **post all costs включая CSM/support/refunds/payment fees**) | Founder-CFO modeling: ARPU, gross margin after inference, **CSM/support cost per salon**, onboarding cost, payment fees, refunds, no-show refunds, churn, CAC, payback period | Founder | Pre-launch + monthly post-launch | [pricing memory](~/.claude/projects/.../memory/project_pricing_model_hybrid.md) |
| **V5** | **NEW** Tiered pricing structure (Starter / Pro / Scale) лучше single-model для разных салонов | Sales calls с показом 3 вариантов; reaction matrix per salon size & vertical. Hypothesis: маленькие/новые → Starter (590+100); активные → Pro (3990 fixed N bookings included); сети → Scale (9990+ chain features). Closes V1 «tax on success» concern для активных салонов. | Founder + Marketing | Pre-launch parallel с V1 | NEW (per 2026-05-18 r4 user analysis) |

---

## ✅ DECIDED — chronological reverse (newest first)

### 2026-05-18 r11 — Conversational trilogy + Schedule impl decisions

Decisions locked by UX Architect for conversational-template trilogy (customer / master / owner) shipping + Schedule MVP S1 implementation decisions from parallel coding agent.

| # | Question | Decision | Source |
|---|---|---|---|
| **Q-CV11** | Master-side conversational templates — separate doc? | **YES — shipped.** Master-tone differs from customer (functional > warm), full 727-line spec at [`master-conversational-templates.md`](./policies/master-conversational-templates.md). | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-CV12** | Owner Mini App templates — separate doc? | **YES — shipped.** Owner = partner-tone, strategic + accountable + info-dense, never sycophantic. 837-line spec at [`owner-conversational-templates.md`](./policies/owner-conversational-templates.md). Completes trilogy. | [conversational-ux-framework §15](./policies/conversational-ux-framework.md) |
| **Q-SC-IMPL1** | `WorkingHours.created_by` when master initiates via master-mobile but has no auth.User row? | **(c) leave NULLABLE + emit audit event with `actor.id = master_{id}` in event envelope.** Auth integration is bigger scope; don't entangle. NULLs backfill from event log when auth lands. NEVER create «bot_user» row as compromise. | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md), [event-taxonomy §2](./policies/event-taxonomy.md) |
| **Q-SC-IMPL2** | ScheduleChangeRequest 72h auto-escalation — Celery beat timing? | **Ship in S4 (ScheduleChangeRequest flow phase), not earlier.** S1-S3 don't require timer triggers. State machine (PENDING → APPROVED / REJECTED / CLARIFICATION / AUTO_ESCALATED) lives in S4. Interim: pending requests without timeout — owner sees alert, acts manually. | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC-IMPL3** | `max_advance_days` clamp — silent or explicit in slot endpoint response? | **EXPLICIT field.** Response shape: `{slots: [...], window: {from, to, requested_to, max_advance_clamped: bool, clamp_reason: 'max_advance_days'}}`. Customer-facing AI must say «больше пока нельзя записаться, показываю до {{date}}» per conversational-ux §7 anti-pattern. | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md), [conversational-ux-framework §7](./policies/conversational-ux-framework.md) |
| **Q-SC-IMPL4** | SlotConfig auto-create per Tenant on creation? | **YES — `@receiver(post_save, sender=Tenant)` auto-create with platform defaults** (buffer 0, lead_time 60min, max_advance 60d, slot_granularity 15min). Remove resolver fallback path (always reads SlotConfig). Standard Django pattern, no magic. | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |
| **Q-SC-IMPL5** | Forward-migration script for data-bearing envs (weekday 1→0 + new columns/tables)? | **Write as part of S1 PR — even if local DB empty.** RunPython migration with 8 steps (ALTER columns + UPDATE weekday + CREATE TABLE x3 + backfill SlotConfig + backfill is_working). Make idempotent. Verify with `SELECT COUNT(*) FROM working_hours` before merge. | [schedule §20](./handoffs/2026-05-18-schedule-management-handoff.md) |

### 2026-05-18 r6 — Batch close of 12 new questions (Q-M1 + 7 Q-CX + 4 Q-M)

Designer-lock with founder veto on Q-M1 business strategy. All proceed to implementation as working assumptions.

| # | Question | Decision | Source |
|---|---|---|---|
| **Q-M1** | Master role — ACTIVE vs PASSIVE | **ACTIVE locked as working assumption.** Build for ACTIVE per master handoff §3. Founder may downgrade to PASSIVE pre-Phase-1 if business case demands ~4-week cost savings — but reduces retention upside. Default proceed = ACTIVE. | [master handoff §3](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-CX1** | Customer onboarding tour | **NO explicit tour.** Value must be self-evident in first 3 messages. If post-launch feedback shows confusion, revisit. Avoid friction at first contact. | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-CX3** | Photo attachments from customer | **Accept + auto-route to HUMAN_LOCKED tier.** Admin reviews image manually. No AI image interpretation in MVP (avoid medical-image hallucination risk). | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-CX5** | Service-specific aftercare templates | **Hybrid architecture locked**: platform-curated 11-category baseline (per salon catalog vertical memory) × 3–5 services each + tenant override per-service. Content sourcing = execution task for content team (not a design block). | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-CX7** | «Поделиться с подругой» referral | **YES via shareMaxContent.** Referrer customer flagged with metadata `referred_by=customer_id` for future loyalty credit when loyalty system ships (Volna 4). No customer-visible mechanics on MVP — silent tracking. | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-CX8** | Negative rating (≤3★) routing | **Owner role only.** Privacy: master shouldn't see complaints about themselves directly. Master sees own ratings (aggregated, anonymized) via permissions matrix v1.1. | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-CX9** | Customer opt-out scope | **Single «без проактивных» toggle** in profile. Transactional reminders (T-24h / T-2h / T-15min for confirmed bookings) always on (cannot be disabled — operational necessity). | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-CX12** | Multi-location salon — customer location choice | **Per 5-bot org cap (MAX limit)**: single bot per salon network with location encoded in `start_param`. Customer filters by location in F1 «Услуги» catalog (location chip). Marketing materials per location use distinct deeplinks. | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-M2** | Master accept/decline assigned bookings | **Auto-assigned by owner; no unilateral decline.** Master can submit reassign request with reason → goes to owner with audit log entry. Prevents master from cherry-picking high-value bookings. | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M6** | Master change-request (services/hours) approval UX | **Bot DM with inline approve/decline buttons** (matches owner mobile habit). Detail view (full justification, schedule diff) in admin web. | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M13** | Master web fallback at launch | **Skip MVP — MAX-only at launch.** Build web only on explicit salon request (edge case «у мастера сломался телефон»). Estimated cost: ~2 weeks reusing components. Defers if usage data shows no demand. | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |
| **Q-M15** | Master-edited replies in learning loop | **YES include with `edited_by_master=True` flag.** Goes through normal learning queue flow with master-flag visible. Owner reviews each candidate (existing approval flow). Allows master tone to refine assistant persona over time. | [master §14](./handoffs/2026-05-18-master-mobile-handoff.md) |

### 2026-05-18 r5 — Closed by design (Q-CX2 + Q-CX11)

| # | Question | Decision | Source |
|---|---|---|---|
| **Q-CX2** | Voice messages from customer | **Decline gracefully MVP** («Голос пока не распознаю, напишите текстом»). Locked via Q-C6 (voice not MVP). | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |
| **Q-CX11** | Bot persona name in greeting | **Confirmed YES** — tenant configures name in onboarding, surfaces in first greeting. Strong product differentiation. Per assistant-persona.md. | [customer §19](./handoffs/2026-05-18-customer-first-time-handoff.md) |

### 2026-05-18 r4 — Batch close of 9 items (4 hard blockers + 5 Q12 sub-questions)

After detailed user product/legal review. Hard blockers got working policies (execution → external sign-off pending but not blocking design/eng). Q12 sub-questions all locked with explicit rules.

| # | Question | Decision | Source |
|---|---|---|---|
| **Q11** | CSM headcount model | **Founder-led for first 25 active salons.** No CSM hire unless founder physically can't keep up. At 25 active — review by **trigger metrics table**: avg onboarding time per salon (>2h founder-time triggers CSM/playbook), activation rate (<60% in 14d triggers product/wizard fix), churn first 30d (>15% triggers CSM or activation rework), support requests (>3/week/salon triggers FAQ/CSM), KB-incomplete share (>30% triggers onboarding assist). Working assumption: 1 CSM per **15–25 salons** if CSM-heavy manual ops; per **40–60 only if automated onboarding + self-serve KB**. | [onboarding §11](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q13** | Payment provider | **Working hypothesis: CloudPayments primary, ЮKassa fallback, Stripe later for non-RU.** Deep integration BLOCKED on 1–2 page provider checklist by finance — must verify: recurring base + variable per-event + refunds (full/partial) + webhook HMAC + фискализация per 54-ФЗ + УПД для ИП/ООО + sandbox + go-live timeline + commission %. If CloudPayments fails any → fallback to ЮKassa. | [onboarding §11](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q14** | Налоговый профиль fields | **MVP supports ИП / ООО / самозанятый only. Физлица NOT supported as paying tenants in MVP.** Reduces PII risk + simplifies onboarding. **Никаких паспортных данных** без явного юр.обоснования (ФЗ-152 risk). Banking details (расчётный счёт + БИК) **only when required** for refunds/документы (lazy collect). Fields per type: **ИП** — ФИО / ИНН / ОГРНИП / адрес / email / система налогообложения. **ООО** — название / ИНН / КПП / ОГРН / юр.адрес / ФИО подписанта / email. **Самозанятый** — ФИО / ИНН / подтверждение статуса (через ФНС API) / email. Legal still validates field list per ФЗ-152/54 — execution task, not open question. | [onboarding §11](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q-C3** | Retention policy | **4-layer working policy** (legal sign-off as execution): **Layer 1 — Full transcripts** 180 дней → after: PII removed, anonymous aggregate retained. **Layer 2 — Audit events** 365+ дней (billing/payment audit может дольше после legal). **Layer 3 — BookingRequest/payment** до 7 лет (бухгалтерия). **Layer 4 — Sensitive/medical** минимизация: **prefer structured flags** (`sensitive_flag=true, reason=medical_contraindication, decision=handoff`) **NOT full medical text**. If full text needed — separate consent + 6 мес full + 1 year anonymized. **Customer-deletion**: honor within 30 days unless legal hold. **Physical infrastructure**: RU-located storage required per ФЗ-152. Legal validates final wording — execution task. OP4 closed as duplicate. | [ownership-policy §6](./policies/conversation-ownership-policy.md), [conversations §10](./handoffs/2026-05-17-conversations-handoff.md) |
| **Q12-α** | Reschedule billing | **0 billable.** Retention not acquisition. Locked per user analysis: «брать деньги за перенос — мелочный биллинг, salon почувствует». Track in analytics as value metric, not paid event. | [attribution-policy §6](./policies/attribution-policy.md) |
| **Q12-β** | No-show auto-refund | **YES auto-refund −100 ₽** when YClients webhook fires `status=NO_SHOW`. **Plus anti-fraud**: anomaly detection — if salon's NO_SHOW rate >15% OR sudden spike → CSM review; if individual salon постоянно cancels bot fees → audit. Adds rule to Q15. | [attribution-policy §6](./policies/attribution-policy.md) |
| **Q12-γ** | Actor role detection at booking time | **Mandatory before billing ship.** Add `actor_type` enum to attribution_metadata: `customer / owner / admin / receptionist / master / system`. Billing rule: `billable = (actor_type == "customer")` AND other strict conditions. If role detection unavailable in current schema → engineering must add before billing enable. **Без actor_type — нельзя включать automated billing** (founder testing бота → wrong charge → trust killed). Fallback `test_mode=True` flag допустим временно но хуже (admin может забыть). | [attribution-policy §3](./policies/attribution-policy.md) |
| **Q12-δ** | Pre-launch attribution audit owner | **Founder manually reviews first 50 attributed bookings** before first commercial billing. Quality Reviewer role (same as Q-CO3/LQ5) — founder for cohort #1–50, CSM lead after. ~2 hours of manual review work; saves trust. | [attribution-policy §8](./policies/attribution-policy.md) |
| **Q12-ε** | Договор-оферта attribution clause | **Mandatory before billing ship.** Must contain 8 elements: (1) definition of `ai_direct`, (2) what's NOT billable (ai_assisted, human_direct, external, test_admin, reschedule), (3) no-show refund auto, (4) cancel <1h refund auto, (5) cancel 1h–24h CSM discretion, (6) cancel >24h no refund, (7) dispute process (e-mail/dashboard, 48h CSM SLA), (8) **30-day dispute window** + who makes final decision (CSM lead + founder for escalation). Draft в `attribution-policy.md` §13 — legal review batch с Q14 + Q-C3. | [attribution-policy §13](./policies/attribution-policy.md) |

### 2026-05-18 r3 — Q12 Attribution policy (closed 🔴 critical)

**Schema decision**: REJECTED binary `attributed_to_bot: bool`. Adopted extensible 3-field model on `BookingRequest`:
- `booking_source` enum (4 values: `ai_direct` / `ai_assisted` / `human` / `external`)
- `ai_assist_score` decimal 0.00–1.00 (internal/analytics-only)
- `attribution_metadata` JSON (full audit context)

**Billing rule**: strict — `is_billable` = `ai_direct` AND not test_mode AND not admin_role AND not `execute_reschedule`. Refund auto on cancel<1h and on no-show (per Q15 + Q12-c).

**Why extensible from day 1**: powers future ROI dashboards, sales-pitch claims, AI-vs-human comparison, commission tiers, optimization. Binary would block all of these and require painful migration later.

| # | Question | Decision | Source |
|---|---|---|---|
| **Q12** | Bot-attribution rules: which `BookingRequest` rows count? Edge cases (bot→YClients web, bot→admin manual). | **Extensible 3-field schema** (`booking_source` / `ai_assist_score` / `attribution_metadata`). **Billing**: strict `ai_direct` only (non-test, non-admin, non-reschedule). **Analytics**: full model. **20 edge cases resolved** per [`attribution-policy.md`](./policies/attribution-policy.md) §7. **Sub-decisions Q12-α through Q12-ε** awaiting founder/eng/legal sign-off but do not block schema/engineering work. | [`attribution-policy.md`](./policies/attribution-policy.md), [memory: attribution-extensible-model](~/.claude/projects/.../memory/project_attribution_extensible_model.md) |

**Sub-questions raised (need joint call)**: Q12-α (founder ratify reschedule=0), Q12-β (founder ratify no-show refund), Q12-γ (eng audit `is_admin_role` detection), Q12-δ (founder pick pre-launch audit owner), Q12-ε (legal review договор-оферта clause — batch with Q14/Q-C3). All non-blocking for schema work; engineering can proceed.

### 2026-05-18 r2 — Batch lock of 🟢 questions (13 closed)

All MVP-scope decisions; designer authority sufficient. Most are «include in v1.1+ backlog» calls.

| # | Question | Decision | Source |
|---|---|---|---|
| **Q15** | Refund rules for cancelled bookings | **Cancelled within 1h of creation → auto-credit −100 ₽ on next invoice** (salon-cancel and customer-cancel treated identically). Cancelled >24h after creation → **NO refund** (anti-gaming). Window 1h–24h → no refund by default; CSM-discretion override possible with audit log entry. | [onboarding §11](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q16** | Founder-50 cutoff communication | **NO scarcity emails.** Real-time counter in `FounderPricingBadge` on landing is honest urgency; avoid gimmicky «осталось N мест!» messaging. | [onboarding §11](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q-CO2** | Per-tenant custom roles | **4 fixed roles MVP** (Owner / Admin / Receptionist / Master). Custom roles editor deferred to v1.1. **Same decision as OP3** — confirmed consistent. | [conversations §10](./handoffs/2026-05-17-conversations-handoff.md) |
| **Q-CO5** | Multi-tenant customer profile | **Different profiles per tenant** (chat history isolated per salon). Shared phone-based linkage opt-in for cross-tenant analytics in v1.1. Prevents data leak between salons; preserves tenant privacy boundary. | [conversations §10](./handoffs/2026-05-17-conversations-handoff.md) |
| **LQ1** | Bulk-accept for ★★★★★ in learning queue | **NO bulk-accept on MVP.** Risk: rubber-stamping poisons training data. Revisit after cohort #1–50 — if median admin spends >30 min/week on queue, consider gated bulk for safety-flagged-clear items only. | [conversations §C4](./handoffs/2026-05-17-conversations-handoff.md) |
| **LQ3** | Cross-tenant learning aggregation | **NO for MVP.** Privacy + competitive risk. Opt-in category-level insights v1.1+ if tenants explicitly want «другие салоны Москвы добавили эту услугу» signal. Never share specific salon→content mapping. | [conversations §C4](./handoffs/2026-05-17-conversations-handoff.md) |
| **LQ4** | Inactive tenants (60+ days) | **Pause learning proposer; preserve queue.** On next login banner: «N предложений ждут с прошлого месяца — посмотреть?» with option to clear-all without losing data. Re-activates proposer on first new conversation. | [conversations §C4](./handoffs/2026-05-17-conversations-handoff.md) |
| **LQ6** | Learning queue notification cadence | **Daily MAX-bot digest + dashboard badge only.** No per-suggestion push (too noisy). Digest shows top-3 highest-confidence items with `[Открыть учёбу]` link. Weekly summary on Mondays. | [conversations §C4](./handoffs/2026-05-17-conversations-handoff.md) |
| **LQ7** | Edit history per FAQ entry post-acceptance | **Yes — show «изменено помощником через учёбу N дней назад» metadata line** in catalog and FAQ views. Click expands to source conversation IDs. Helps trust + rollback. | [conversations §C4](./handoffs/2026-05-17-conversations-handoff.md) |
| **P3** | Multi-language timing | **RU only MVP.** Add KZT/BYN languages when waitlist demand reaches 20+ tenants (parallel to currency Q3). Localized persona templates needed per language — significant work, not MVP. | [persona §12](./policies/assistant-persona.md) |
| **OP1** | Concurrent admin model | **Lock-based MVP** (one admin owns conversation at a time; `take-over` available with audit). Collaborative (Slack-style cursor visibility, real-time co-editing) deferred to v1.2. | [ownership-policy §12](./policies/conversation-ownership-policy.md) |
| **OP2** | Custom per-tenant SLA tiers | **Fixed 15/30/60/120 thresholds MVP.** Custom per-tenant overrides v1.1 if premium-spa segment requests faster (e.g., 5/15/30/60). | [ownership-policy §12](./policies/conversation-ownership-policy.md) |
| **OP7** | Multi-tenancy customer profile | **Closed as duplicate of Q-CO5.** Same question raised independently; tracking as Q-CO5 only. | [ownership-policy §12](./policies/conversation-ownership-policy.md) |

### 2026-05-18 — Batch lock of 🟡 questions (14 closed)

Designer-locked. Items marked «working assumption» allow implementation to proceed; founder retains explicit veto on business-strategy items.

| # | Question | Decision | Source |
|---|---|---|---|
| **Q10** | Trial-end behaviour in hybrid-pricing context | **10 free bot-attributed bookings OR 14 days, whichever first → soft read-only.** «Soft read-only» = dashboard works, assistant stops on NEW customers, in-flight conversations complete. Working assumption pending founder ratification of business strategy. | [onboarding §11](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q17** | Customer #51 pricing | **590 ₽ + 100 ₽/booking continues for #51+ (no «founder» badge). Re-evaluate at 3-month data from cohort #1–50.** Safe default to avoid wrong-price lock before data. | [pricing memory](~/.claude/projects/.../memory/project_pricing_model_hybrid.md), [onboarding §11](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q-CO1** | Tier classification confidence threshold | **0.7 threshold**: ≥0.7 → auto-assign tier per handoff reason; <0.7 → admin picks tier on first reply (UI shows recommended tier as default but allows override). Tune after first 100 conversations. | [conversations §10](./handoffs/2026-05-17-conversations-handoff.md) |
| **Q-CO3** | Persona quality reviewer ownership | **Consolidated «Quality Reviewer» role (merged with LQ5).** Founder for cohort #1–50; CSM lead after. Does both persona audits (P-section sampling) and learning queue 10% audit. Estimated 2–3 hours/week initially, scales with cohort size. | [conversations §10](./handoffs/2026-05-17-conversations-handoff.md) |
| **Q-CO4** | Long-delay proactive message | **Yes — assistant sends single acknowledgement at 30-min mark if admin hasn't replied.** Wording from `assistant-persona.md` §4: «Сейчас немного дольше, чем обычно — команда разбирается с вашим вопросом». For HUMAN_LOCKED + regulated topics, framing may shift to explicit admin name per ownership-policy §7. One message per long-delay event, not repeated. | [conversations §10](./handoffs/2026-05-17-conversations-handoff.md) |
| **LQ5** | Founder/quality reviewer cohort for learning queue | **Merged with Q-CO3 as single «Quality Reviewer» role.** Avoids context fragmentation across persona and learning audits — one human reviews both. | [conversations §C4](./handoffs/2026-05-17-conversations-handoff.md) |
| **OP4** | Retention policy legal sign-off (in ownership-policy doc) | **Closed as duplicate of Q-C3.** Same question raised independently in conversations §10 and ownership-policy §12; tracking as Q-C3 only going forward. | [ownership-policy §12](./policies/conversation-ownership-policy.md) |
| **P1** | Default assistant gender/name | **Per-tenant only.** Onboarding wizard prompts «Как клиент будет видеть вашего ассистента?» with example «Помощница студии Карина». Default if salon leaves blank: «Ассистент» (нейтрально, gender-agnostic). | [persona §12](./policies/assistant-persona.md) |
| **P2** | Voice for B2B-style tenants (premium spa, medical) | **One baseline persona + tone-modifier slider** (сдержанный / тёплый / игривый, default middle). No separate B2B template for MVP. Medical/premium tenants use «сдержанный» + `explicit_human_policy_enabled` for regulated topics. | [persona §12](./policies/assistant-persona.md) |
| **P4** | LLM-output filtering implementation | **Hybrid**: (a) prompt-injection of persona rules into system message of every LLM call; (b) post-generation regex/list filter for forbidden phrases. Both must pass; either failing triggers admin warning before send. Implementation in `apps/persona/quality.py`. | [persona §12](./policies/assistant-persona.md) |
| **OP3** | Granular permission editor for custom roles | **4 fixed roles MVP** (Owner / Admin / Receptionist / Master) per ownership-policy §4. Custom roles editor deferred to v1.1. Tenants request role changes via CSM in interim. | [ownership-policy §12](./policies/conversation-ownership-policy.md) |
| **OP5** | Audit log export format | **CSV (Excel-readable) + JSON (machine-readable)** from Settings → Аудит → Экспорт. Both available from day 1. PDF format deferred to v1.1 — only if compliance/audit demand surfaces. | [ownership-policy §12](./policies/conversation-ownership-policy.md) |
| **OP6** | Customer-deletion request UX | **Process locked**: customer e-mails support@ with deletion request; CSM verifies identity via initData phone match + manual confirmation step; 30-day soft-delete window → hard-delete; audit log retained per Q-C3 policy. Self-serve deletion UX deferred to v1.1. **Final legal approval of process gated on Q-C3.** | [ownership-policy §12](./policies/conversation-ownership-policy.md) |
| **LQ2** | Tenant can disable learning queue entirely | **Yes — toggle in Settings → Advanced → Учёба → ☐ Отключить предложения от помощника.** Strong warning shown before save: «Помощник перестанет улучшаться от диалогов. Можно включить обратно в любой момент.» Default: enabled. | [conversations §C4](./handoffs/2026-05-17-conversations-handoff.md) |

### 2026-05-17 r2 — Pricing & Conversations strategic decisions

| # | Question | Decision | Source |
|---|---|---|---|
| **Q9** | Цена подписки (1990 / 3990 / 9990 fixed)? | **REJECTED FIXED MONTHLY.** Locked hybrid: **590 ₽ base + 100 ₽ per bot-attributed booking. Founder pricing locked for first 50 customers indefinitely. Post-50 model TBD (see Q17).** | [pricing memory](~/.claude/projects/.../memory/project_pricing_model_hybrid.md), [onboarding §1](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q-C1** | Identity policy — bot vs explicit admin? | **Single AI-assistant for customer. Explicit human only for regulated topics (medical, refund, legal).** Foundation for entire platform. | [single-assistant memory](~/.claude/projects/.../memory/project_single_assistant_identity.md), [persona doc](./policies/assistant-persona.md), [conversations §10](./handoffs/2026-05-17-conversations-handoff.md) |
| **Q-C2** | Auto-resume after admin reply? | **3-tier ownership model: AI_CONTINUITY / HUMAN_SUPERVISED / HUMAN_LOCKED, determined by handoff reason.** | [ownership memory](~/.claude/projects/.../memory/project_conversation_ownership_tiers.md), [ownership-policy doc](./policies/conversation-ownership-policy.md) |
| **Q-C4** | Concurrent admins — lock or collaborative? | **Lock-based MVP. Collaborative deferred to v1.2.** | [ownership-policy §1](./policies/conversation-ownership-policy.md) |
| **Q-C5** | Suggested reply default on/off? | **Opt-in, OFF by default. Always requires human edit. Pre-send «проверьте факты» warning.** | [conversations §C2](./handoffs/2026-05-17-conversations-handoff.md) |
| **Q-C6** | Voice messages in MVP? | **NO MVP. Audio transcription + storage = +2 weeks. Deferred to v1.2.** | [conversations §10](./handoffs/2026-05-17-conversations-handoff.md) |
| **Q-C7** | Templates — fixed or LLM-generated? | **Hybrid: platform-level smart suggestions + tenant custom + LLM context-aware. Scenario-based for complaints (no auto-discount templates).** | [conversations §C2](./handoffs/2026-05-17-conversations-handoff.md) |
| **Q-C8** | CSM escalation access level? | **CSM read-only by default. Write requires per-conversation tenant approval.** | [conversations §10](./handoffs/2026-05-17-conversations-handoff.md) |
| **Q-C9** | Mobile reply UX pattern? | **Full-screen detail. No swipe-between-conversations on MVP.** | [conversations §C2](./handoffs/2026-05-17-conversations-handoff.md) |
| **Q-C10** | Learning loop — auto-add or admin approves? | **Auto-suggest, admin approves each. Never auto-add to KB.** Drives the entire Learning Queue (Screen C4). | [conversations §C4](./handoffs/2026-05-17-conversations-handoff.md) |

### 2026-05-17 r1 — Onboarding cascade (Q1-Q8)

| # | Question | Decision | Source |
|---|---|---|---|
| **Q1** | Источник средних цен на MVP при отсутствии 240 салонов? | **Hybrid honest seed: парсинг 30–50 публичных прайсов на регион + честная disclosure «основано на 32 публичных прайсах». Crowd-correct по мере роста.** | [onboarding §9](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q2** | Multi-location: одиночный или сеть? | **Single-location per tenant MVP. Forward-compatible schema (`location_id NULL`).** Олег ведёт 5 tenants через CSM. | [onboarding §1](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q3** | Валюта — только RUB или CIS? | **RUB only MVP. «Страна» поле в signup → waitlist для KZT/BYN.** | [onboarding §1](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q4** | Услуги без фиксированной цены («от 3000»)? | **3 типа цены: Fixed / From / OnRequest. Skip ranges (min-max).** | [onboarding §1](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q5** | Шаблоны — фиксированные или custom? | **11 baseline + Custom MVP. Tenant-custom v1.1. Community marketplace отложено.** | [onboarding §1](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q6** | Master↔Service mapping в template-path? | **Default 1 мастер (admin), все услуги. Прогрессивное раскрытие через Phase 4c Masters tab.** | [onboarding §1](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q7** | Услуги-комплекты как сущность? | **Bundles = обычные services с `is_bundle` tag. Никакой отдельной bundle-сущности.** | [onboarding §1](./handoffs/2026-05-17-salon-onboarding-handoff.md) |
| **Q8** | Per-master pricing? | **MVP template-path single price/duration. YC-path читает per-master из YClients read-only. Per-master multipliers v1.1.** | [onboarding §1](./handoffs/2026-05-17-salon-onboarding-handoff.md) |

---

## Cross-references in source docs

Each source doc has an «Open questions» section that historically listed its own questions. Going forward those sections should say «See [decisions-log.md](./decisions-log.md) for current status of [list of question IDs].» so this log stays canonical.

Sources currently containing question lists:
- [`2026-05-17-salon-onboarding-handoff.md`](./handoffs/2026-05-17-salon-onboarding-handoff.md) §11 — Q9 closed + Q10–Q17
- [`2026-05-17-conversations-handoff.md`](./handoffs/2026-05-17-conversations-handoff.md) §10 — Q-C1–Q-C10 closed + Q-CO1–Q-CO5
- [`2026-05-17-conversations-handoff.md`](./handoffs/2026-05-17-conversations-handoff.md) Screen C4 § Open questions — LQ1–LQ7
- [`assistant-persona.md`](./policies/assistant-persona.md) §12 — P1–P4
- [`conversation-ownership-policy.md`](./policies/conversation-ownership-policy.md) §12 — OP1–OP7
- [`memory/project_pricing_model_hybrid.md`](~/.claude/projects/.../memory/project_pricing_model_hybrid.md) — V1–V4

---

## Summary counts

**2026-05-19 r17** — Two-bus event architecture decision (A) locked. event-taxonomy.md §14 «Scope separation from apps/events/» added. `apps/events/` (existing) stays for product analytics tracking (snake_case + sync fanout). `apps/eventbus/` (NEW) for domain events per taxonomy (dot.notation + Postgres outbox). NOT a replacement — two systems by design, different concerns. Wellness Mood handoff added under handoffs/ (2026-05-19-wellness-mood-handoff.md, 827 lines). Added Q-EV-IMPL1-5 (5 new). Q-EV-IMPL1 ✅ confirmed-decided as (A) per founder sign-off.

| Status | Count | Δ from r16 |
|---|---|---|
| 🔴 Critical open | **2** (Q-WI6, Q-MB1) | — |
| 🟡 Soon open | **81** (+Q-EV-IMPL 2/3/4/5) | +4 |
| 🟢 Later open | **137** | — |
| 🔬 Validating | **5** (V1–V5) | — |
| ✅ Decided | **81** (+Q-EV-IMPL1) | +1 |
| **Total tracked** | **308** | +5 |

**2026-05-19 r16** — Notification Preferences UX (customer + master + owner). Added Q-NP1-17 (17 new; Q-NP1 ✅ confirmed-decided as single «без проактивных» toggle per Q-CX9). 3-axis matrix (audience × channel × event-type), 14 event types classified, per-audience preferences UI, frequency caps + DND windows. Unblocks Settings Hub refresh + cross-cutting opt-in/opt-out logic across customer-first-touch + cancellation/reschedule + wellness modules + escalations.

**2026-05-18 r15** — Master onboarding M0-M7 flow. Added Q-MO1-17 (17 new). 8-stage lifecycle locked. **AI-first service selection** reinforced per project_salon_catalog_vertical memory (master never manually creates services; AI proposes from 11-category templates + regional pricing). Unblocks Phase 2 master-mobile implementation.

**2026-05-18 r14** — Customer first-touch + Mini App states catalog (combined spec). Added Q-FT1-10 + Q-MAS1-10 (20 new). 7 entry sources locked, 10-state Mini App catalog locked, per-screen state matrix for 4b's 6 screens. Unblocks 4b customer Mini App implementation.

**2026-05-18 r13** — Customer cancellation + reschedule spec. Added Q-CR1-15 (15 new). State machine, refund integration, cascade flows, reschedule cap, anti-abuse mechanics locked. Unblocks Schedule S2/S5 customer-side flows.

**2026-05-18 r12** — Attribution 4a post-ship clarifications. Added Q-ATT-IMPL1-7 + Q-PERF-1 (8 new). Updated attribution-policy.md with §15 «Implementation deviations & transition concessions» documenting 3 accepted deviations (validator skip / score stub / billing_reason populate convention) + approved additions (visit_at validator Q-ATT-IMPL3) + tracked items (Q-PERF-1 / Q-ATT-IMPL4/6/7).

**2026-05-18 r11** — Conversational trilogy + Wellness OS suite + Event taxonomy + Manual booking + Schedule impl + Schedule wireframes. +91 new questions tracked, 7 decided (Q-CV11/12 + Q-SC-IMPL1-5). Open questions span 8 new doc areas.

**Doc areas now tracked**: onboarding (Q1-Q17), conversations module (Q-C, Q-CO, LQ), persona (P, Q-PE), ownership (OP), schedule (Q-SC + Q-SC-IMPL), master-mobile (Q-M), master-management (Q-MM), customer-first-time (Q-CX), analytics (Q-AD), loyalty (Q-L), **NEW r11**: wellness modules (Q-WI), event taxonomy (Q-EV), conversational-ux (Q-CV), master-conversational (Q-MC), owner-conversational (Q-OC), manual-booking (Q-MB).

**Pending integration (deferred until parallel agent finishes schedule code)**: Q-IA × 10 (information-architecture), Q-WP × ~10 (wellness-profile policy), Q-US × ~5 (user-states), Q-UJ × ~6 (user-journeys).

### Execution / external sign-off tasks (not open decisions)

| Task | Action | Owner | Est. |
|---|---|---|---|
| **Legal batch consult** | Q14 + Q-C3 + Q12-ε + Q-M4 retention scope | RU юрист | 2–4 hours |
| **Provider checklist** | CloudPayments vs ЮKassa 10-point | Finance | 1–2 hours |
| **Engineering audit** | actor_type schema + admin role detection | Engineering | 30 min |
| **Founder pre-launch attribution audit** | First 50 attributed bookings manual review | Founder (Quality Reviewer) | 2 hours post-soft-launch |
| **Content sourcing for Q-CX5** | 11-category × 3–5 services aftercare templates | Content team | 1–2 weeks |
| **Q-M1 founder ratification** (working assumption already locked) | Founder confirms ACTIVE master role before Phase 1 build | Founder | 5 min |
| **Validation V1–V5** | Sales calls, attribution audit, competitive scan, unit economics, tiered pricing | Various | Continuous pre-launch |

### Engineering can fully proceed
All schema work, all UX implementation, all module builds — no design blockers remaining. Only legal consult gates RU production launch.

### Execution tasks remaining (not open decisions — sign-off / verification work)

| Task | Action | Owner | Est. time |
|---|---|---|---|
| **Legal batch consult** | Validate Q14 (tax fields) + Q-C3 (retention 4-layer) + Q12-ε (договор-оферта 8-element clause) + Q14 паспорт-prohibition + физлица-exclusion | RU юрист по ФЗ-152/54 | 2–4 hours |
| **Provider checklist** | 1–2 page comparison: CloudPayments vs ЮKassa on 10-point criteria from Q13 | Finance lead | 1–2 hours |
| **Engineering audit** | Verify `actor_type` enum can be added to current schema; confirm role detection feasibility in `execute_confirm` context | Engineering | 30 min |
| **Founder pre-launch attribution audit** | Manual review of first 50 attributed bookings | Founder (Quality Reviewer role) | 2 hours, post-soft-launch |
| **Validation V1–V5** | Sales calls, attribution audit, competitive scan, unit economics, tiered pricing test | Various | Continuous pre-launch |

### Engineering can fully proceed on
- ✅ Attribution schema (5-enum `ai_direct / ai_assisted / human_direct / external / test_admin` + `billable` + `billing_reason` + `attribution_metadata.actor_type`)
- ✅ Conversations module (all Q-C* / Q-CO* / LQ* closed)
- ✅ Learning Queue
- ✅ Billing screen UX + schema (only payment provider integration final-blocks on Q13 finance verification)
- ✅ Persona module
- ✅ Retention layer architecture (4-layer model per Q-C3 working policy)

### Briefing artifacts (NEW r4)
- [`founder-session-briefing.md`](./briefings/founder-session-briefing.md) — 1-page summary for 30-min founder session covering Q11, Q13, Q12-α/β/δ ratification
- [`legal-consult-briefing.md`](./briefings/legal-consult-briefing.md) — pre-read for RU юрист covering Q14, Q-C3, Q12-ε

## Maintenance protocol

1. **When a question gets resolved** → move row from Open to Decided. Add date. Update source doc to point here.
2. **When a new question emerges** → add to Open with proper urgency. Assign owner. Reference source doc/conversation.
3. **When a recommendation evolves** → update inline (keep the recommendation field current); don't archive until decided.
4. **Weekly review** (recommended): founder/PM scans 🔴 + 🟡 items, attempts to lock at least 2 per week.
5. **Decision quorum**: 🔴 require founder + relevant lead; 🟡 require owner only; 🟢 can be locked by designer/PM unilaterally if no objections in 1 week.

---

## Open questions about this log itself

| # | Question | Owner |
|---|---|---|
| **DL1** | Should this live in repo (current) or migrate to Notion/Linear when team grows? | Founder |
| **DL2** | Should decisions get versioned (e.g., if Q9 hybrid pricing is revised post-50, do we keep Q9 or create Q9.1)? Lean: create Q-PR-2 for revisions, preserve history. | PM |
| **DL3** | Auto-generation — can we extract questions from source docs by regex `^\| Q\d+\|` to avoid manual sync? Lean: write a small script post-MVP. | Engineering |
