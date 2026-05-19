# Manual Booking — owner/admin creates bookings on customer's behalf

**Date:** 2026-05-18 r1
**Status:** Foundational — preemptive spec for Schedule MVP phase S5
**Reads:** [`attribution-policy.md`](./attribution-policy.md), [`core-user-states.md`](./core-user-states.md), [`conversation-ownership-policy.md`](./conversation-ownership-policy.md), [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`owner-conversational-templates.md`](./owner-conversational-templates.md), [`core-wellness-profile.md`](./core-wellness-profile.md)

> Manual Booking is when a salon staff member creates a booking record for a customer — by phone, walk-in, or admin entry. The customer may have never interacted with the bot before. This doc locks the UX, conversation-thread bootstrap, attribution, and customer experience after manual booking.

---

## 0. Why this exists

### The gap

Existing booking flow assumes customer DMs assistant → AI guides → booking created → `booking_source = ai_direct`. But salons in MVP get bookings several other ways:

1. Customer calls salon by phone — admin enters into Mini App
2. Customer walks in — admin enters into Mini App
3. Customer messages on Instagram / WhatsApp / another channel — admin enters into Mini App
4. Customer asks admin in MAX directly (skipping AI) — admin enters
5. Existing customer texts admin «как обычно» — admin enters
6. Master takes booking face-to-face after a visit — master enters via master-mobile

In all 6 cases: assistant didn't drive the booking, but now must:
- Record it with correct attribution (`human_direct`)
- Decide whether to message customer (confirmation? reminders?)
- If customer never DMd before — **bootstrap a conversation thread from scratch**
- Set customer's `core_user_state` correctly (may bypass DISCOVERED → EXPLORING entirely)
- Populate Wellness Profile Layer 1 (Identity) from admin-entered fields
- Handle the case where customer never wanted to use the bot — respect channel preference

Without this spec: agent improvising. Either too eager (bot DMs cold customer who never opted in → spam complaint) or too passive (bot silent → customer misses reminders → no-show).

### The promise

Every manual booking pathway has a defined customer experience matching the customer's actual relationship with the salon (cold lead vs known regular).

---

## 1. Manual Booking sources

Per §0, there are 6 source patterns. All compress into 3 entry surfaces:

| Surface | Who uses | Role permission |
|---|---|---|
| **Mini App — Booking inbox / New booking button** | Owner / Admin | Create on any master in tenant |
| **Master Mobile — «Add walk-in booking» button** | Master | Create only for self (own master_id) |
| **API endpoint `/api/v1/bookings/manual`** | YClients sync, future integrations | System actor; webhook-driven |

All 3 surfaces write the same `BookingRequest` row with `booking_source = human_direct` OR `external` per [`attribution-policy.md`](./attribution-policy.md).

---

## 2. Customer pre-existence states (the critical fork)

Before creating a manual booking, system checks: does this customer already exist in our tenant?

```
                  ┌─ existing customer with active conversation thread → A
                  │
manual booking ───┼─ existing customer, no conversation thread yet → B
   form          │
                  └─ NEW customer (no record at all) → C
```

Each fork has different UX. **This is the load-bearing branch of the whole doc.**

### A. Existing customer with active conversation

Customer has prior conversation history (regular). Booking appears in their feed naturally. Assistant sends one confirmation message. No bootstrap needed.

### B. Existing customer, no conversation thread

Customer record exists (maybe migrated from YClients or admin-created earlier) but they never DM'd the assistant. Manual booking creates the first reason for assistant to reach them — **but only if MAX handle / phone is known + customer consent rules allow**.

### C. NEW customer

No record. Admin enters name + (phone OR MAX handle) + service + slot. System creates Customer row + Wellness Profile (Layer 1 only — Identity). Conversation thread state depends on contact info available.

---

## 3. The Manual Booking entry form (owner / admin side)

**Surface**: Mini App → Bookings → «+ Создать запись» button
**Voice anchor**: [`owner-conversational-templates.md`](./owner-conversational-templates.md) — functional + concise

### Form layout

```
┌──────────────────────────────────────────────────┐
│ ← Создать запись                                 │
├──────────────────────────────────────────────────┤
│ Клиент                                           │
│ [Поиск по имени / телефону / MAX                ▾]│
│ ⤷ predictive results: 3-5 matches               │
│                                                  │
│   • Если ничего не найдено:                      │
│   [+ Новый клиент]                               │
│                                                  │
│ Услуга                                           │
│ [Выбрать услугу                                 ▾]│
│                                                  │
│ Мастер                                           │
│ [Авто — кто свободен / выбрать вручную          ▾]│
│                                                  │
│ Дата + время                                     │
│ [📅 18 мая, ср    ⏰ 14:00                       ]│
│   ⤷ free slots highlighted; conflicts greyed     │
│                                                  │
│ Источник                                         │
│ ◯ Позвонил по телефону                           │
│ ◯ Зашёл лично                                    │
│ ◯ Написал в Instagram/WhatsApp/etc.              │
│ ◯ YClients sync                                  │
│ ◯ Другое: [______________]                       │
│                                                  │
│ Комментарий мастеру (опционально)                │
│ [_______________________________]                │
│                                                  │
│ ☑ Отправить подтверждение клиенту                │
│   через помощника                                │
│   ⤷ disabled if no contact info; tooltip explains│
│                                                  │
│ [Отмена]                          [Создать]      │
└──────────────────────────────────────────────────┘
```

### Field details

| Field | Required | Constraint |
|---|---|---|
| Customer | Yes | Either pick existing OR create new (inline modal) |
| Service | Yes | From tenant's catalog |
| Master | Yes | Auto-suggest based on service + slot; admin can override |
| Date + time | Yes | Slot resolver checks availability live |
| Source | Yes | Drives `booking_source` enum value (mapping §6) |
| Comment | No | Stored on BookingRequest, surfaces to master |
| Send confirmation toggle | Auto | Default ON if contact info present; OFF if no contact |

### New customer inline modal

If admin selects «+ Новый клиент»:

```
┌──────────────────────────────────────────────────┐
│ Новый клиент                                     │
├──────────────────────────────────────────────────┤
│ Имя                  [_________________________] │
│ Фамилия (опц.)       [_________________________] │
│ Способ связи                                     │
│   ◯ MAX handle:   [@_____________________]       │
│   ◯ Телефон:      [+7 _________________]         │
│   ◯ Нет — клиент сам подойдёт                    │
│                                                  │
│ Цель / запрос (опц.) [_________________________] │
│  ⤷ помогает помощнику персонализировать          │
│                                                  │
│ Согласие клиента на коммуникацию                 │
│   ☐ Клиент согласен получать сообщения           │
│      от помощника                                │
│  ⤷ если не отмечено — только тех. подтверждение  │
│      без проактивных касаний                     │
│                                                  │
│ [Отмена]                          [Сохранить]    │
└──────────────────────────────────────────────────┘
```

**Consent checkbox is load-bearing.** Without it, system creates customer but assistant NEVER proactively messages. Confirmation message also suppressed (rely on admin's offline channel). This protects against spam complaints from customers who didn't opt into AI messaging.

---

## 4. Booking creation logic

After admin taps «Создать»:

### Step 1: validation
- Customer record selected or created
- Service + master + slot validated against slot resolver (no conflicts)
- Source enum mapped per §6
- Consent flag captured

### Step 2: BookingRequest row write
```sql
INSERT INTO booking_requests (
  customer_id,
  tenant_id,
  master_id,
  service_id,
  slot_start,
  slot_end,
  status = 'CONFIRMED',
  booking_source = <mapped>,
  ai_assist_score = NULL,
  billable = FALSE,            -- always false for manual
  billing_reason = 'manual booking — non-AI source',
  attribution_metadata = {
    "actor_type": "human_admin",
    "creator_user_id": "<admin_user_id_or_master_id>",
    "creation_surface": "mini_app" | "master_mobile" | "api",
    "manual_source_label": "<from form>",
    "send_confirmation": <bool>,
    "customer_was_new": <bool>,
    "customer_consented_at_creation": <bool>
  },
  created_at = NOW(),
  created_by = <admin_user_id>
)
```

### Step 3: emit events
- `booking.created` (per [`event-taxonomy.md §3.1`](./event-taxonomy.md#31-booking-domain))
- `booking.attribution.assigned` immediately (no async; manual is deterministic)
- `customer.created` if new customer created in flow
- `customer.consent.changed` if consent flag captured

### Step 4: customer-side touch decision
Branching on customer state + consent — see §5.

### Step 5: master-side notification
Always: `master.*` notification per [`master-conversational-templates.md §5.6`](./master-conversational-templates.md#56-new-booking-notification-someone-just-booked-you).

---

## 5. Customer-side touch decision matrix

The most subtle part of manual booking. What does the customer see?

| Customer state | Consent | Action |
|---|---|---|
| **A. Existing + active conversation** | (already opted in by virtue of prior DMs) | Send single confirmation message in existing thread |
| **B1. Existing, no thread, consent=YES** | Yes | Bootstrap thread §7; send confirmation + intro |
| **B2. Existing, no thread, consent=NO** | No | NO assistant message; rely on admin's offline channel |
| **C1. NEW customer, consent=YES** | Yes | Bootstrap thread §7; full intro + confirmation; create Wellness Profile Layer 1 |
| **C2. NEW customer, consent=NO** | No | NO assistant message; record only |
| **C3. NEW customer, no contact info** | n/a | NO assistant message; pure record-keeping |

### A: existing + active conversation

**Template:**
```
Запишу — {{date}} в {{time}}, у {{master_name}}.
Напомню накануне и за час до визита.
```

Identical voice to AI-driven confirmation per [`conversational-ux-framework.md §5.1.5`](./conversational-ux-framework.md#touchpoint-515-booking-confirmed).

Customer perceives no difference between this and an AI-driven booking. Single-assistant identity preserved.

### B1 / C1: bootstrap path (consent given)

Per §7 detailed bootstrap protocol.

### B2 / C2 / C3: silent path

System creates record. Master is notified normally. **Customer hears nothing from assistant.** Salon admin handles confirmation manually (phone callback, in-person, etc.).

This is critical. A cold customer who never opted into AI gets ZERO bot messages. Even reminders are off.

If later that customer messages the assistant themselves → state transitions normally per [`core-user-states.md`](./core-user-states.md), and reminders + post-visit flow re-activate.

---

## 6. Source enum mapping

Admin's form selection maps to `booking_source` enum (locked per [`attribution-policy.md`](./attribution-policy.md)):

| Form selection | booking_source | billable | billing_reason |
|---|---|---|---|
| «Позвонил по телефону» | `human_direct` | false | `manual booking — phone` |
| «Зашёл лично» | `human_direct` | false | `manual booking — walk-in` |
| «Написал в Instagram/WhatsApp/etc.» | `external` | false | `manual booking — external channel` |
| «YClients sync» | `external` | false | `manual booking — yclients sync` |
| «Другое» | `human_direct` | false | `manual booking — <free text from form>` |
| Master-mobile «walk-in» | `human_direct` | false | `manual booking — walk-in by master` |

`ai_assist_score = NULL` for all manual sources. NEVER set to 0.0 (that means «AI tried and contributed nothing», semantically different).

---

## 7. Conversation thread bootstrap (B1 / C1 path)

When assistant first messages a customer who never DM'd before, the experience must:
- Identify itself clearly (single-assistant identity per [`product-ux-vision.md`](./product-ux-vision.md))
- Explain why customer is hearing from it now
- Confirm the booking
- Set expectation for future communication
- Offer easy opt-out
- NOT feel like spam

### Bootstrap template (C1: new customer, consent=YES, MAX handle known)

**Surface**: First DM from assistant to customer in MAX

**Voice anchor**: Warm + Calm + Premium-but-accessible + extra-Attentive (explain context)

**Template:**
```
Здравствуйте, {{customer_first_name}}.

Я — помощник студии «{{salon_name}}». {{admin_first_name}} записал(а) вас сегодня:

📅 {{service_name}}
{{date_long}}, {{time}}
Мастер: {{master_first_name}}

Здесь я буду напоминать о визите, отвечать на вопросы по нашим услугам и помогать перенести запись, если что-то изменится.

Если не хотите получать сообщения — напишите «стоп».
```

### Bootstrap template (B1: existing customer, no thread yet)

Slightly different — acknowledge prior relationship without overclaiming:

```
Здравствуйте, {{customer_first_name}}.

Я — помощник студии «{{salon_name}}» в MAX. {{admin_first_name}} записал(а) вас на {{service_name}} {{date_long}} в {{time}}, у {{master_first_name}}.

Если удобно — буду писать сюда: напомнить, перенести, ответить на вопросы.

Если не нужно — напишите «стоп».
```

### After customer first reply

Customer's first reply transitions state from NEW/IMPORTED → `EXPLORING` or `READY_TO_BOOK` per regular state machine. Assistant continues in regular voice.

If customer replies «стоп»:
- `customer.opted_out` event emitted
- Future assistant messages suppressed
- Existing booking still happens; admin handles reminders offline
- Customer remains in tenant's customer list

**Forbidden in bootstrap:**
- ❌ «Спасибо за обращение!» — customer didn't approach us
- ❌ Marketing CTA on first message
- ❌ Bot self-introduction with personal name
- ❌ Emoji in opener
- ❌ Multiple messages in quick succession
- ❌ Skip the «напишите стоп» opt-out
- ❌ Reference data customer never gave us («помню, вы интересовались...»)

---

## 8. Wellness Profile Layer 1 initialization (C1 / C3 path)

When NEW customer is created via Manual Booking, only Layer 1 (Identity) is populated:

```json
{
  "layer_1_identity": {
    "first_name": "<from form>",
    "last_name": "<from form, may be null>",
    "preferred_contact_channel": "max" | "phone" | "none",
    "max_handle": "<if entered>",
    "phone": "<if entered>",
    "first_seen_at": "<NOW>",
    "first_seen_source": "manual_booking",
    "creator_actor": {
      "type": "human_admin",
      "id": "<admin_user_id>",
      "via_surface": "mini_app" | "master_mobile" | "api"
    },
    "consent": {
      "ai_messaging": <bool from form>,
      "captured_at": "<NOW>"
    }
  }
}
```

Other layers (Goals, Body State, Service History, Behavioral, Nutrition, etc.) remain empty until the customer engages directly or completes the first visit.

After first visit completion (`booking.completed` event):
- Layer 4 (Service History) — first procedure record + master FK + reactions if captured
- Layer 5 (Behavioral) — first booking pattern signal
- AI persona may proactively offer post-visit check-in (only if `consent.ai_messaging=true`)

---

## 9. Reminder behavior

Default reminders for manual bookings depend on consent + source:

| State | Day-before reminder | 1-hour reminder | Day-of-visit check-in |
|---|---|---|---|
| A (active conv) | YES | YES | YES |
| B1 / C1 (consent yes) | YES | YES | YES |
| B2 / C2 / C3 (consent no / no contact) | NO | NO | NO |

Admin can override per-booking: «не отправлять напоминания» flag in form (defaults off but visible).

---

## 10. Cancellation by customer of manually-booked appointment

If customer has consent + active thread (A or B1/C1 paths), customer can DM «отмени запись на завтра» — assistant handles cancellation normally per [`conversational-ux-framework.md`](./conversational-ux-framework.md).

If silent path (B2 / C2 / C3) — customer cancels via admin (phone/walk-in). No bot involvement.

Either way: `booking.cancelled` event emitted with `cancelled_by` = customer OR admin.

---

## 11. Master-mobile «walk-in» button

Per [`master-conversational-templates.md`](./master-conversational-templates.md), master can add walk-in via mobile.

**Surface**: Master Mobile → today's schedule → «+ Walk-in» button

Simplified form (master has less context than owner):

```
┌──────────────────────────────────┐
│ ← Записать пришедшего            │
├──────────────────────────────────┤
│ Услуга  [Выбрать ▾]              │
│ Время начала  [Сейчас, 14:23]    │
│ Длительность  [60 мин ▾]         │
│                                  │
│ Клиент                           │
│   ⦿ Не знаю / без записи         │
│   ◯ Постоянный — найти           │
│   ◯ Новый — записать имя         │
│                                  │
│ [Записать]                       │
└──────────────────────────────────┘
```

If «Не знаю» — `customer_id = NULL`, BookingRequest still created with `attribution_metadata.customer_anonymous = true`. Used for slot occupancy only; no Wellness Profile. Common for true walk-ins.

If «Новый» — minimal form (name only); creates Customer row + Layer 1 with `consent.ai_messaging = NULL` (not asked). Assistant silent until customer DMs directly.

**Reason for simplified flow on master side**: master is in service moment, not admin moment. They can't ask 5 consent questions; we accept partial data.

---

## 12. API endpoint (system actor, e.g., YClients sync)

`POST /api/v1/bookings/manual`

```json
{
  "tenant_id": "tnt_...",
  "external_source_id": "yclients_booking_98765",
  "customer_external_id": "yclients_customer_12345",
  "service_external_id": "yclients_service_456",
  "master_external_id": "yclients_master_22",
  "slot_start": "2026-05-19T14:00:00Z",
  "slot_end": "2026-05-19T15:00:00Z",
  "customer_consent_known": false,
  "notes": "imported from yclients"
}
```

Behavior:
- If customer with matching `external_id` exists → reuse
- Else create Customer record (Layer 1 only) with `consent.ai_messaging = NULL`
- `booking_source = 'external'`
- `billable = false`
- Assistant **silent path** by default (no bootstrap message)
- Idempotent by `external_source_id` (re-imports update existing row, not duplicate)

If salon wants AI assistant to engage these imported customers → must run separate consent-gathering campaign before flipping `consent.ai_messaging = true`.

---

## 13. Edge cases

### 13.1 Customer exists in 2 tenants

Manual Booking is tenant-scoped. Customer's record in tenant A is independent from their record in tenant B per [memory: project_attribution_extensible_model](../../../C:/Users/user/.claude/projects/C--Users-user-PycharmProjects-ai-bot-platform/memory/project_attribution_extensible_model.md). Manual booking in tenant A creates/updates tenant-A record only.

### 13.2 Customer's MAX handle changed

Bootstrap template uses last known MAX handle. If undeliverable (handle invalid), event `conversation.message.delivery.failed` emitted. Admin sees alert in dashboard. Booking still valid; reminders disabled.

### 13.3 Admin creates booking with conflict

Slot resolver returns 409 Conflict. Form shows inline error:
```
Слот занят: {{conflicting_customer_short}} в {{conflict_time}}.
Альтернативы: {{alt_slot_1}}, {{alt_slot_2}}.
```

Admin can override by force-creating overlapping slot (e.g., double-booking with master's agreement). Force-flag captured in `attribution_metadata.slot_force_override = true` + audit event.

### 13.4 Admin creates booking for archived master

Form prevents selection. If somehow API attempts: 400 Bad Request. `master.archived` events keep masters off bookable list.

### 13.5 Customer opted out earlier, admin creates booking

Customer has `consent.ai_messaging = false`. Form's «Отправить подтверждение» checkbox is disabled + tooltip explains. Admin must use offline channel.

If admin un-checks the disabled-rationale tooltip and re-enters customer via «новый» — system de-duplicates by phone/MAX handle, refuses to flip consent without explicit dialog. (Anti-abuse measure.)

### 13.6 Booking created at 23:55, slot is 08:00 next morning

Bootstrap message respects quiet hours. Per [`owner-conversational-templates.md`](./owner-conversational-templates.md) off-hours rules:
- Bootstrap message queued until customer's local 09:00
- Master notification immediate (master subscribed working-hours-only)
- Audit event timestamps preserved (event.occurred_at = creation time; delivery time separate)

### 13.7 Two admins simultaneously book same slot

Slot resolver uses SELECT FOR UPDATE / unique constraint on (master_id, slot_start). Second admin's create returns 409. UI re-renders availability.

---

## 14. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Bootstrap message without consent | Spam complaint, brand damage, MAX policy violation | Always check consent before reaching customer |
| Bootstrap assumes customer knows the bot | «Как вы помните, я ваш помощник…» — they don't | Introduce self plainly |
| Bootstrap on every booking even if thread exists | Annoying duplicate intros | Check thread existence first |
| Manual booking sets `booking_source = ai_direct` | Inflates AI attribution / billing fraud | Always `human_direct` or `external` |
| Manual booking sets `billable = true` | Charges salon for non-AI work | Always `false` for manual |
| Mass-import via API with `send_confirmation = true` | Mass spam to imported customers | Default `false` for API imports; require separate opt-in flow |
| Bootstrap voice differs from regular voice | Customer feels handoff | Same persona, same envelope |
| Consent capture buried in fine print | Legal + ethical issue | Explicit checkbox with clear language |
| Master-mobile force-bootstrap from walk-in | Customer didn't agree to AI; sudden DMs | Master path NEVER triggers bootstrap; consent gathered separately |

---

## 15. Permissions matrix

| Action | Owner | Admin | Master | Customer |
|---|---|---|---|---|
| Create manual booking (any master) | ✅ | ✅ | ❌ | n/a |
| Create manual booking (self as master) | ✅ if also master | ✅ if also master | ✅ | n/a |
| Add new customer inline | ✅ | ✅ | ✅ (name only, simplified) | n/a |
| Set `send_confirmation = true` flag | ✅ if consent yes | ✅ if consent yes | ❌ (master path silent default) | n/a |
| Override consent for existing customer | ❌ (locked once captured) | ❌ | ❌ | ✅ (own data) |
| Force-create on slot conflict | ✅ (audit-logged) | ✅ (audit-logged) | ❌ | n/a |
| Bulk import via API | ✅ via integration | ✅ via integration | ❌ | n/a |

---

## 16. Analytics implications

Manual bookings feed analytics dashboard per [`event-taxonomy.md`](./event-taxonomy.md):

| Metric | Includes manual? |
|---|---|
| Total bookings | YES |
| AI-direct bookings | NO (only `ai_direct` enum) |
| Billable bookings | NO (manual is always `billable=false`) |
| Revenue total | YES |
| Customer acquisition channel | YES — segmented by `manual_source_label` |
| Bot attribution share | NO — denominator includes manuals, numerator excludes |
| Master utilization | YES |
| Customer LTV | YES |

This way analytics show «AI is driving X% of bookings» honestly. Owner sees the gap and can prioritize AI-channel growth.

---

## 17. Onboarding implications

During [salon-onboarding-handoff](../handoffs/2026-05-17-salon-onboarding-handoff.md) Phase 4d (first bookings setup), admin is shown:

```
Как клиенты будут записываться:
• Через помощника в MAX (autocreated)
• Через ваш Mini App (autocreated)
• Через звонок — вы создаёте запись вручную  [✅ настроено]
• Из Instagram / WhatsApp / etc. — вручную   [✅ настроено]
• Из YClients — автоматически через интеграцию [Подключить]
```

Manual booking flow is positioned as **default-on**, not optional. Every salon needs it from day 1 (phone calls don't disappear).

---

## 18. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| Q-MB1 | Consent checkbox required or optional in new-customer modal? | Required — must be explicitly checked or explicitly «no contact» selected | Legal | 🔴 before S5 ships |
| Q-MB2 | When admin enters MAX handle that doesn't exist on MAX — silent fail or error? | Silent fail (queue message, log delivery failure event, surface in admin dashboard) | UX | 🟡 |
| Q-MB3 | Should bootstrap message disclose admin's name or stay generic? | Disclose admin name (warmth + trust); generic if admin opted out of identification in their profile | UX | 🟡 |
| Q-MB4 | Customer replies «кто это?» to bootstrap — same as «бот или человек?»? | Same template per conversational-ux §6.4 — «помощник студии — AI-ассистент» | UX | 🟢 |
| Q-MB5 | Manual booking after-hours — when does master see it? | Per master notification preference; default queued to next working window | UX | 🟢 |
| Q-MB6 | Walk-in customer (anonymous) gets a real booking row — how is slot occupied? | Yes — anonymous booking blocks slot like any other; just no customer_id | Eng | 🟢 |
| Q-MB7 | Manual booking by master for OTHER master — allowed? | NO — master scope is own bookings only; admin role required for cross-master | Policy | 🟢 |
| Q-MB8 | Bulk-edit manual bookings (e.g., correct 10 of yesterday's errors)? | NO MVP — single-record edit only; bulk in v1.2+ with explicit audit batch | UX | 🟢 |
| Q-MB9 | Customer asks AI for booking history — show manual ones too? | YES — single thread, single history; AI says «{{admin_name}} записал вас на …» | UX | 🟢 |
| Q-MB10 | When admin types a customer name that matches multiple people — disambiguation UX? | Show ≤5 matches with last-visit date + phone-tail; admin picks; if 0 → «+ Новый» | UX | 🟡 |
| Q-MB11 | YClients sync — what happens to bookings already in our DB that get cancelled in YClients? | Sync deletion → `booking.cancelled` event with `cancelled_by = external_system`, customer notified per consent | Eng | 🟡 |
| Q-MB12 | Should master see indicator on Mini App that a booking was manual vs AI? | Subtle indicator (e.g., 📞 icon for phone-source); master may want to call customer back | UX | 🟢 |
| Q-MB13 | Customer who later opts in («согласен на сообщения») — does AI reach out about past visits? | NO automatic recap; AI engages on next interaction normally | UX | 🟢 |
| Q-MB14 | Manual booking with `slot_force_override` — should AI eventually warn about overlap pattern? | YES — if 3+ overrides in 30 days for same master, surface insight to owner | UX | 🟢 |
| Q-MB15 | Master mobile «walk-in» customer matched to existing — show prior history? | YES if last_visit < 90 days; subtle context line «был у вас 3 раза» | UX | 🟢 |

---

## 19. Cross-document linkage

- [`attribution-policy.md`](./attribution-policy.md) — `human_direct` / `external` enum values + billable rule
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — tier rules apply to bootstrap conversations
- [`core-user-states.md`](./core-user-states.md) — state transitions on bootstrap path
- [`core-wellness-profile.md`](./core-wellness-profile.md) — Layer 1 initialization §8
- [`conversational-ux-framework.md`](./conversational-ux-framework.md) — customer-facing templates §7
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) — Mini App form copy §3
- [`master-conversational-templates.md`](./master-conversational-templates.md) — master walk-in flow §11
- [`event-taxonomy.md`](./event-taxonomy.md) — events emitted §4
- [`information-architecture.md`](./information-architecture.md) — bookings surface placement
- [`../handoffs/2026-05-18-schedule-management-handoff.md`](../handoffs/2026-05-18-schedule-management-handoff.md) — slot resolver integration
- [`../handoffs/2026-05-17-salon-onboarding-handoff.md`](../handoffs/2026-05-17-salon-onboarding-handoff.md) — onboarding Phase 4d §17

---

## 20. What this unblocks

- **Schedule MVP phase S5 implementation**: backend engineers have full UX spec for owner form + master form + API
- **Conversation thread bootstrap protocol**: first cold message templates locked
- **Attribution correctness**: prevents `booking_source = ai_direct` pollution
- **Customer consent handling**: explicit at moment of admin-created customer
- **Wellness Profile Layer 1 initialization**: admin-side data entry contract
- **YClients sync correct behavior**: silent path documented
- **Analytics honesty**: AI-attribution metrics not inflated by manuals

## 21. What this does NOT unblock

- ❌ AI-driven booking flow (existing flow, [`conversational-ux-framework.md §5.1.5`](./conversational-ux-framework.md))
- ❌ Bulk import UI (v1.2+; manual flow is one-at-a-time MVP)
- ❌ Cross-tenant customer merging (separate policy)
- ❌ Skip Legal review of consent dialog (Q-MB1 is 🔴)
- ❌ Auto-engage previously silent customer who opts in later (Q-MB13)

---

## 22. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-18 |
| Legal (consent dialog Q-MB1) | ☐ | |
| Founder (positioning vs AI-first vision) | ☐ | |
| Engineering (slot resolver integration) | ☐ | |
| AI prompt engineering (bootstrap templates) | ☐ | |

## Last verified
2026-05-18 (initial draft, manual booking pathway locked for S5)
