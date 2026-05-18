# Customer Cancellation & Reschedule — flow + state machine + templates

**Date:** 2026-05-18 r1
**Status:** Foundational — preemptive spec for Schedule MVP phase S2 (cancellation) + S4-aligned (reschedule)
**Reads:** [`attribution-policy.md`](./attribution-policy.md), [`manual-booking-spec.md`](./manual-booking-spec.md), [`conversation-ownership-policy.md`](./conversation-ownership-policy.md), [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`master-conversational-templates.md`](./master-conversational-templates.md), [`owner-conversational-templates.md`](./owner-conversational-templates.md), [`event-taxonomy.md`](./event-taxonomy.md)

> Cancellation and reschedule share a state machine, refund rules, notification cascade, and tone constraints. This doc locks both before Schedule S2/S5 implementation.

---

## 0. Why this exists

### The gap

Existing specs cover booking CREATION (manual-booking-spec, conversational-ux-framework §5.1, Mini App F-screens). After creation, three things can change a booking's lifecycle:

1. **Customer cancels** (most common — life happens)
2. **Customer reschedules** (less destructive — keeps relationship)
3. **Master/admin cancels or reschedules** (operational — sick day, double-booking discovered, etc.)

Without this spec, engineering improvises:
- Cancel goes to bot DM with hardcoded copy → off-brand
- Refund logic written ad-hoc per call site → inconsistent with attribution-policy
- Mass cancellation (master sick day) becomes batch spam to customers
- Customer who cancels 5 times in a row treated same as first-time canceller
- Master sees customer's «cancelled because boyfriend dumped me» reason → privacy leak

### The promise

Single source of truth for:
- State machine (what states + transitions exist)
- Customer-facing templates per transition (tone-conformed)
- Master / owner notification cascade
- Refund mechanics integration with attribution-policy
- Anti-abuse caps + edge cases
- Cascading scenarios (master sick → N customer bookings)

---

## 1. Scope boundaries

### IN scope
- Customer cancels own booking (via bot DM / Mini App / external link)
- Customer reschedules own booking (via bot DM / Mini App)
- Cascading from master ScheduleChangeRequest approval → customer notifications + re-offer
- Cascading from `booking.no_show` event → billing + customer touch
- Notification + audit cascade per [`event-taxonomy.md`](./event-taxonomy.md)
- Templates per [conversational-ux-framework](./conversational-ux-framework.md), [master-templates](./master-conversational-templates.md), [owner-templates](./owner-conversational-templates.md)

### OUT of scope (separate docs)
- Owner/admin cancelling on customer's behalf without explicit consent — covered in [`owner-conversational-templates.md`](./owner-conversational-templates.md) §6
- Master cancelling mid-service — operational emergency, HUMAN_LOCKED tier per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md)
- YClients-originated cancellations sync — covered in YC webhook port (Q-ATT-IMPL7)
- Refund payment provider integration — engineering / billing scope
- Salon-side cancellation policy editor («у нас правила: за 24 часа — 100% возврат») — Settings Hub scope

---

## 2. State machine

```
                  ┌────────────┐
                  │  CONFIRMED │  ← booking lives here normally
                  └─────┬──────┘
                        │
        ┌───────────────┼──────────────────────┐
        │               │                      │
   customer       customer initiates     master/owner action
   initiates      reschedule              (sick / schedule change)
   cancel
        │               │                      │
        ▼               ▼                      ▼
  CANCEL_REQUESTED  RESCHEDULE_REQUESTED   AFFECTED_BY_SCHEDULE_CHANGE
        │               │                      │
        │       ┌───────┴───────┐               │
        │       │               │               │
        │  proposed alt    proposed alt    cascaded re-offer
        │  accepted        rejected            (per affected booking)
        │       │               │               │
        ▼       ▼               ▼               ▼
   CANCELLED  RESCHEDULED   CANCELLED       RESCHEDULED or CANCELLED
                            (no alt agreed)  (per customer reply)
        │       │               │               │
        └───────┴───────────────┴───────────────┴───▶ terminal state
```

### State definitions

| State | Trigger | Reversible? | Billing impact |
|---|---|---|---|
| `CONFIRMED` | Booking created and confirmed (any source) | n/a | Captured at creation per attribution-policy |
| `CANCEL_REQUESTED` | Customer initiated cancel but final confirmation pending (e.g., 5-sec undo window) | YES (undo) | None yet |
| `RESCHEDULE_REQUESTED` | Customer initiated reschedule; AI offered alternatives; waiting for selection | YES (abandon → stay CONFIRMED) | None yet |
| `AFFECTED_BY_SCHEDULE_CHANGE` | Master ScheduleChangeRequest approved; this booking is in the cancelled window | YES (until customer responds) | None yet |
| `CANCELLED` | Terminal. Reason + actor recorded | NO (admin can re-create, not «un-cancel») | Refund evaluated per §4 |
| `RESCHEDULED` | Terminal for OLD booking; NEW booking created with linkage | NO | New booking inherits attribution from original; `attribution_metadata.rescheduled_from = <old_booking_id>` |
| `COMPLETED` | Master marked done | NO | Billing already evaluated per attribution-policy |
| `NO_SHOW` | Auto-detected 15 min past start with no completion mark | NO | Auto refund −100₽ if billable (per Q12-β) |

### Transitions table

| From | To | Trigger | Actor types allowed |
|---|---|---|---|
| CONFIRMED | CANCEL_REQUESTED | Customer initiates cancel | customer |
| CANCEL_REQUESTED | CANCELLED | Confirm (auto after 5s or explicit tap) | customer / system |
| CANCEL_REQUESTED | CONFIRMED | Undo within 5s | customer |
| CONFIRMED | RESCHEDULE_REQUESTED | Customer initiates reschedule | customer |
| RESCHEDULE_REQUESTED | RESCHEDULED | Customer picks alternative slot | customer |
| RESCHEDULE_REQUESTED | CONFIRMED | Customer abandons | customer / system (timeout 24h) |
| RESCHEDULE_REQUESTED | CANCELLED | Customer explicitly cancels instead | customer |
| CONFIRMED | AFFECTED_BY_SCHEDULE_CHANGE | Master ScheduleChangeRequest approved | owner / admin |
| AFFECTED_BY_SCHEDULE_CHANGE | RESCHEDULED | Customer accepts re-offer | customer |
| AFFECTED_BY_SCHEDULE_CHANGE | CANCELLED | Customer declines all alternatives | customer / system (timeout 48h) |
| CONFIRMED | NO_SHOW | Auto 15 min past start | system |
| CONFIRMED | COMPLETED | Master marks done | master / owner / admin |

---

## 3. Cancellation flow

### 3.1 Entry points

| Surface | Trigger | Authority |
|---|---|---|
| **Bot DM** | Customer types «отмени запись» / «не приду завтра» / similar | Customer-initiated |
| **Mini App: Мои записи → tap booking → «Отменить»** | Customer taps action | Customer-initiated |
| **Mini App: confirmation email/notification → cancel link** (Phase 3+) | Customer follows link | Customer-initiated |
| **Owner/Admin Mini App** | Owner cancels on customer's behalf | Admin-initiated (owner-conversational-templates §6) |
| **YClients webhook** | External system cancellation | External (Q-ATT-IMPL7) |

### 3.2 Customer-initiated bot DM cancel flow

#### Step 1: Intent recognition + booking disambiguation

Customer: «отмени запись»

If customer has 1 active booking → AI selects it.
If customer has 2+ active bookings → AI asks which:

```
Какую запись отменить?

📅 {{date_1}} {{time_1}} — {{service_1_short}}
📅 {{date_2}} {{time_2}} — {{service_2_short}}

[{{date_1}} в {{time_1}}]  [{{date_2}} в {{time_2}}]
```

If 0 active bookings → AI confirms gracefully:

```
У вас сейчас нет активных записей.
Если что-то нужно — напишите.
```

#### Step 2: Confirmation prompt

**Voice anchor**: Calm + Confident — never beg, never guilt

```
{{booking_summary_one_line}}

Точно отменить?

[Да, отменить]   [Передумала]
```

**`booking_summary_one_line` examples**:
- «{{date_short}} в {{time}}, {{service_short}} у {{master_first_name}}»

**Forbidden in confirmation**:
- ❌ «Жаль терять вас!» / «Вы уверены?» (с эмоциональным давлением)
- ❌ «А может, перенесём?» (offering reschedule is fine in §3.3, but NOT as guilt-trip alternative)
- ❌ «Останется ли свободно время?» (worried-for-salon framing)

#### Step 3: Cancellation confirmation (with optional reason)

**Voice anchor**: Calm + Functional + brief

```
Отменила запись на {{date}} в {{time}}.

(Опционально) Что повлияло?
[Не успеваю]  [Изменились планы]  [Не нужна услуга сейчас]  [Другое]  [Пропустить]
```

Reason is OPTIONAL — customer can ignore the chips. If chosen, recorded in `attribution_metadata.cancellation_reason_class` (categorical) + optional free text in `cancellation_reason_text`.

**Forbidden**:
- ❌ Demanding reason (open-ended «Расскажите, почему?»)
- ❌ Multi-step reason interview
- ❌ Discount offer to keep booking («Скидка 20% если останетесь?» — manipulative; not in scope MVP)

#### Step 4: Refund acknowledgement (per §4 rules)

If cancellation triggers refund per §4, message includes line:

```
Salon-side: помощник возвратит платный учёт по этой записи студии.
```

If NO refund (e.g., cancel >24h before, never billable to begin with):
- No mention of refund in customer message — irrelevant to them

Customer never sees salon's billing internals. Customer-facing cancel confirmation is brief and final.

### 3.3 Optional «may we offer reschedule instead?» branch

**WHEN to offer**: only when customer initiated cancel AND cancellation reason chip == «Не успеваю» OR «Изменились планы» (suggests timing, not interest loss).

**WHEN NOT to offer**: reason chip == «Не нужна услуга сейчас» → respect; no upsell. Reason == «Другое» → no offer (ambiguous).

**Template**:
```
Понимаю. Если время не подходит — могу подобрать другой день?

[Подобрать другое время]  [Нет, спасибо]
```

If customer taps «Подобрать другое время» → transition to RESCHEDULE flow §5.

**Forbidden**:
- ❌ Auto-offer reschedule on every cancellation (anti-pattern, becomes nag)
- ❌ More than 1 reschedule offer per cancel (one chance, then drop)

### 3.4 Mini App «Мои записи» cancel flow

#### My Bookings list view

```
┌──────────────────────────────────┐
│ Мои записи                       │
├──────────────────────────────────┤
│ 📅 Завтра, ср · 14:00            │
│ Лимфодренаж · Маша                │
│                                  │
│ [Открыть]                        │
│                                  │
│ 📅 Через 2 недели · пт 10:00     │
│ Стрижка · Лена                    │
│                                  │
│ [Открыть]                        │
│                                  │
│ ── Прошлые ──                    │
│ ...                              │
└──────────────────────────────────┘
```

#### Booking detail screen

```
┌──────────────────────────────────┐
│ ← {{service_name}}               │
├──────────────────────────────────┤
│ 📅 {{date_long}}                 │
│ ⏰ {{time}} — {{end_time}}        │
│ 👤 Мастер: {{master_first_name}} │
│ 📍 {{salon_address}}             │
│                                  │
│ [Перенести]   [Отменить]          │
│                                  │
│ ── Подготовка ──                 │
│ {{prep_notes_if_any}}            │
└──────────────────────────────────┘
```

#### Cancel modal

```
┌──────────────────────────────────┐
│ Отменить запись?                 │
│                                  │
│ {{date_short}} в {{time}}        │
│ {{service_short}} · {{master}}    │
│                                  │
│ Что повлияло? (опционально)      │
│ [Не успеваю]                     │
│ [Изменились планы]               │
│ [Не нужна услуга сейчас]         │
│ [Другое]                         │
│                                  │
│ [Назад]            [Отменить запись] │
└──────────────────────────────────┘
```

Tap «Отменить запись» → 5-second undo toast at bottom:

```
┌──────────────────────────────────┐
│ Запись отменена · Отменить       │
└──────────────────────────────────┘
       ⤷ tap «Отменить» = undo
```

After 5s OR explicit dismiss → state transitions to `CANCELLED`. Bot DM follows with confirmation per §3.3.

### 3.5 Late cancellation (< 1 hour before slot start)

**Voice anchor**: Empathetic-mild + Calm — acknowledge urgency, don't moralize

**Customer message**:
```
Понимаю — отменила запись на {{time}}.
Мастер уже мог планировать на вас, но всё в порядке.
Если в другой день подойдёт — напишите.
```

**Master notification (per [master-templates §5.7](./master-conversational-templates.md#57-booking-cancelled-by-customer))**:
```
{{customer_first_name}} {{customer_last_initial}}. отменила за {{minutes_before}} мин до записи.
{{cancellation_reason_softened_if_any}}

[Расписание]
```

**Owner notification**: ONLY if late-cancel rate spike detected (3+ late cancels in 7d) — surface as analytics insight, not per-event alert. Per [owner-templates §6.2 weekly digest patterns](./owner-conversational-templates.md).

**Billing**: per Q15 + attribution-policy §6 — if cancel <1h AND booking was billable → auto-refund −100₽.

### 3.6 Very late cancellation (after start time)

**Different state**: this is NOT cancellation — this is potential NO_SHOW.

Customer messaging «отмени запись» AFTER start time:

**Voice anchor**: Calm + Functional — defer to admin

```
Запись уже должна была начаться. Передам {{owner_first_name}} — она/он разберётся с этим.
```

→ Transitions to HUMAN_LOCKED per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md). Admin manually marks no-show or completed per actual.

Auto-refund on no_show fires from `booking.no_show` event 15 min past start, independent of customer message.

---

## 4. Refund integration

Per [`attribution-policy.md`](./attribution-policy.md) §6 (locked Q15 + Q12-β):

| Scenario | Customer-side | Salon-side billing impact |
|---|---|---|
| Cancel <1h before slot | Confirmation w/ empathetic acknowledgement | Auto −100₽ if was billable |
| Cancel 1-24h before slot | Standard confirmation | NO refund by default; CSM discretion override |
| Cancel >24h before slot | Standard confirmation | NO refund |
| Customer no_show | Auto «как себя чувствуете?» check-in next day | Auto −100₽ if was billable |
| Reschedule (vs cancel) | Standard confirmation | NO refund event; original `billable` stays; new booking does NOT trigger new billable |

**Refund event**: emits `booking.refunded` per [event-taxonomy §3.1](./event-taxonomy.md#31-booking-domain) with `refund_reason = 'late_cancel_auto'` OR `'no_show_auto'` OR `'csm_discretion'`.

**Salon owner sees** refund line in next monthly invoice (per [owner-templates §6.11](./owner-conversational-templates.md)):
```
Возвраты: {{N}} × −100₽ ({{breakdown}})
```

**Customer never sees** the −100₽ — that's salon-side accounting. Customer's cancellation experience is independent.

---

## 5. Reschedule flow

### 5.1 Entry points

| Surface | Trigger |
|---|---|
| **Bot DM** | Customer types «перенеси запись» / «не смогу в среду, давайте на четверг» |
| **Mini App: Booking detail → «Перенести»** | Customer taps action |
| **Mini App: Cancel modal → «Подобрать другое время»** | Recovery path from §3.3 |
| **Cascade from master ScheduleChangeRequest** | Auto-triggered per §7 |

### 5.2 Customer-initiated bot DM reschedule

#### Step 1: Intent + booking selection (same disambiguation as §3.2 step 1)

#### Step 2: Get target preference

**Voice anchor**: Confident + Concise

```
Перенесём {{date}} {{time}} · {{service_short}}.

На какой день удобно?

[Завтра]  [Через неделю]  [Другая дата]
```

If «Другая дата» → date picker UI in Mini App OR free-text in bot («четверг», «после 18 февраля», «утром в субботу»).

#### Step 3: Show alternatives

AI calls slot resolver with: customer's requested day(s), same master (preferred), same service duration.

```
Свободные слоты на {{date_requested}}:

[10:00]  [11:30]  [14:00]  [16:00]

[Другой день →]
```

If preferred master has no slots that day → offer alternative master:
```
{{master_first_name}} занята весь {{date}} — ближайшее у неё {{next_slot_with_usual_master}}.

Если важно скорее — {{alternative_master}} свободна:
[{{alt_time_1}}]  [{{alt_time_2}}]

[Жду {{usual_master}}]
```

#### Step 4: Confirmation

**Voice anchor**: Confident

```
Перенесла:
было: {{old_date}} {{old_time}}
стало: {{new_date}} {{new_time}}, {{master_first_name}}

Напомню накануне как обычно.
```

Master notification per [master-templates §5.8](./master-conversational-templates.md#58-booking-rescheduled).

**Forbidden**:
- ❌ Multi-step reschedule with «оптимальное время для вас» tier-up upsell
- ❌ «Хотите попробовать другую услугу заодно?» cross-sell
- ❌ More than 6 alternative time options shown (decision paralysis)

### 5.3 Mini App reschedule flow

#### Reschedule modal

```
┌──────────────────────────────────┐
│ Перенести                        │
│                                  │
│ Сейчас: {{date}} в {{time}}      │
│ {{service}} · {{master}}          │
│                                  │
│ Выберите новую дату:             │
│ [📅 Календарь]                   │
│                                  │
│ Мастер: {{usual_master}}         │
│ [Изменить мастера]                │
│                                  │
│ [Отмена]   [Подобрать слоты →]   │
└──────────────────────────────────┘
```

Tap «Подобрать слоты» → slot grid for selected date → tap slot → confirmation:

```
┌──────────────────────────────────┐
│ Подтвердить перенос?             │
│                                  │
│ Было:  {{old_date}} {{old_time}} │
│ Стало: {{new_date}} {{new_time}} │
│        {{master}}                 │
│                                  │
│ [Назад]      [Подтвердить]       │
└──────────────────────────────────┘
```

### 5.4 Reschedule cap (anti-abuse)

- **Default cap: 3 reschedules per original booking**
- After 3 reschedules: AI declines further self-service reschedule, asks customer to message admin:
```
Эта запись уже переносилась 3 раза. Чтобы перенести ещё — нужно согласование с {{owner_first_name}}. Передам?

[Да, передать]  [Оставить как есть]
```
- HUMAN_LOCKED transition on owner confirmation
- Cap config per-tenant settings (v1.1 → adjustable; MVP fixed 3)

### 5.5 Attribution preservation

Per [`attribution-policy.md`](./attribution-policy.md) Q12-α: reschedule does NOT recompute attribution. The NEW BookingRequest row:
- `booking_source` = original
- `ai_assist_score` = original (or recomputed only if AI did substantive new work)
- `billable` = FALSE (reschedule never bills — Q12-α retention principle)
- `billing_reason` = `'NOT billable: execute_reschedule (retention not acquisition, Q12-α)'`
- `attribution_metadata.rescheduled_from` = old BookingRequest UUID
- `attribution_metadata.reschedule_count` = N (incremented from old + 1)

Old booking gets `status = RESCHEDULED` (terminal). Audit linkage via `rescheduled_to` reverse FK.

---

## 6. AFFECTED_BY_SCHEDULE_CHANGE cascading

### 6.1 When triggered
Master submits ScheduleChangeRequest (per [`schedule-management-handoff §6`](../handoffs/2026-05-18-schedule-management-handoff.md)) → owner approves with kind ∈ {`vacation`, `sick_leave`, `day_off`} → system identifies all CONFIRMED bookings in the affected window.

### 6.2 Per-booking cascade

For each affected booking, system transitions: `CONFIRMED → AFFECTED_BY_SCHEDULE_CHANGE`.

AI sends customer DM:

**Voice anchor**: Empathetic + Calm + Confident

**Template (sick / health-related):**
```
{{master_first_name}} {{date_softened}} не сможет работать.

Я могу:
• Перенести вас на {{master_first_name}} на {{nearest_alternative_slot_same_master}}
• Записать к {{alternative_master}} в это же время — {{alternative_master_slot}}

Что подходит?

[К {{master_first_name}} {{nearest_date}}]   [К {{alternative_master}} в {{original_time}}]   [Отменить]
```

**Template (vacation / planned absence):**
```
{{master_first_name}} {{date_softened}} в отпуске.
Перенесём вашу запись?

• {{master_first_name}} вернётся {{return_date}} — могу записать на {{nearest_post_return_slot}}
• Если важно раньше — {{alternative_master}} свободна {{alt_slot}}

[К {{master_first_name}}]   [К {{alternative_master}}]   [Отменить]
```

### 6.3 `date_softened` examples

Per [`master-conversational-templates §5.7`](./master-conversational-templates.md#57-booking-cancelled-by-customer) softening rules:

- DAY_OFF → «во вторник» (specific day, no reason)
- SICK_LEAVE → «во вторник» (no «болеет» — don't disclose health)
- VACATION → «с {{start_date}} по {{end_date}}» (explicit dates OK for vacation)
- EVENT (training) → «во вторник» (no «на обучении» unless tenant wants brag-rights — Q-CR3)

### 6.4 No-reply timeout

If customer doesn't reply within 48 hours:
- Auto-cancellation: `AFFECTED_BY_SCHEDULE_CHANGE → CANCELLED` with `actor_type = 'system'`, `cancellation_reason = 'no_response_to_schedule_change_offer'`
- Final customer DM:
```
По вашей записи на {{old_date}} ответа не было — отменила. Если будет нужно — напишите, найдём время.
```

### 6.5 Batch handling for many affected customers

If master ScheduleChangeRequest affects 10+ bookings:
- AI processes individually (one DM per customer, NEVER batch message)
- Throttle: max 5 messages per minute to avoid MAX rate limits
- Sequencing: nearest-date-first (most urgent re-booking pressure)
- Owner sees batch progress in their dashboard:
```
🟡 Обработка изменения расписания
{{processed}} из {{total}} клиентов уведомлены
{{accepted}} приняли альтернативу, {{declined}} отменили, {{pending}} не ответили

[Открыть]
```

---

## 7. Notification cascade per role

### 7.1 Customer cancels own booking

| Recipient | When | Channel | Template |
|---|---|---|---|
| Customer | Immediately | Bot DM + Mini App toast | §3.3 confirmation |
| Master | Immediately if subscribed to per-event; else digest | Bot DM | [master-templates §5.7](./master-conversational-templates.md#57-booking-cancelled-by-customer) |
| Owner | In daily digest OR immediate if late-cancel rate spike | Bot DM / Mini App | [owner-templates §6.1-6.2](./owner-conversational-templates.md) |

### 7.2 Customer reschedules own booking

| Recipient | When | Channel | Template |
|---|---|---|---|
| Customer | Immediately | Bot DM | §5.2 step 4 |
| Master (old slot) | Immediately if subscribed | Bot DM | [master-templates §5.8](./master-conversational-templates.md#58-booking-rescheduled) |
| Master (new slot, if different master) | Immediately | Bot DM | New booking notification per [master-templates §5.6](./master-conversational-templates.md#56-new-booking-notification-someone-just-booked-you) |
| Owner | Aggregate (weekly digest) | Mini App | — |

### 7.3 Master ScheduleChangeRequest approved → cascade

| Recipient | When | Channel | Template |
|---|---|---|---|
| Each affected customer | After owner approval, throttled | Bot DM | §6.2 |
| Master | After owner approval | Bot DM | [master-templates §5.11.2](./master-conversational-templates.md#5112-owner-approved) |
| Owner | Cascade processing progress | Mini App | §6.5 |

### 7.4 No-show detected

| Recipient | When | Channel | Template |
|---|---|---|---|
| Customer | NOT immediate. Next-day morning gentle check | Bot DM | §8 |
| Master | At 15-min past start (per [master-templates §5.9](./master-conversational-templates.md#59-no-show-notification)) | Bot DM | — |
| Owner | Aggregate weekly | Mini App | — |

### 7.5 Events emitted (all transitions)

Per [`event-taxonomy.md`](./event-taxonomy.md) §3.1:

| Transition | Event | Payload notes |
|---|---|---|
| CONFIRMED → CANCEL_REQUESTED | (none — interim state, not durable) | — |
| CANCEL_REQUESTED → CANCELLED | `booking.cancelled` | `cancelled_by`, `cancellation_reason_class`, `cancellation_reason_text`, `cancellation_minutes_before` |
| CONFIRMED → RESCHEDULED | `booking.rescheduled` | `old_slot_start`, `new_slot_start`, `rescheduled_by`, `reschedule_count` |
| CONFIRMED → AFFECTED_BY_SCHEDULE_CHANGE | `schedule.exception.added` (already exists) + per-booking `booking.affected_by_schedule_change` (NEW — add to event-taxonomy) | `triggering_exception_id`, `affected_window` |
| AFFECTED → RESCHEDULED | `booking.rescheduled` with `attribution_metadata.cascade_reason = 'schedule_exception'` | — |
| AFFECTED → CANCELLED (timeout) | `booking.cancelled` with `cancellation_reason_class = 'system_auto_no_response'` | — |
| CONFIRMED → NO_SHOW | `booking.no_show` (already in §3.1) | `detected_at` |
| Refund triggered | `booking.refunded` | `refund_amount`, `refund_reason` |

---

## 8. No-show post-event customer flow

### 8.1 Detection
Auto: `booking.no_show` event fires 15 min past start with no `booking.completed` event.

### 8.2 Same-day silence
NO immediate customer DM. Customer was already absent — pinging them mid-day is intrusive and assumes bad intent.

### 8.3 Next morning gentle check

**Voice anchor**: Calm + Empathetic + brief

```
Вчера {{date_short}} вы не пришли на {{service_short}}.

Всё ли хорошо? Если что-то нужно — рядом.
```

If customer doesn't reply within 48h → no follow-up. Move on.

If customer replies with reason → log to `attribution_metadata.no_show_reason` (if shared willingly). Never demand reason.

### 8.4 Repeated no_show handling

- 2 no_shows in 60 days → AI does NOT change tone, but `attribution_metadata.no_show_pattern_detected = true` surfaces to owner in [owner-templates §6.3 escalation alert](./owner-conversational-templates.md)
- 3+ no_shows in 60 days → owner sees pattern, can decide to mark customer as low-reliability (Settings Hub v1.1) or require deposit (out of scope MVP)
- AI does NOT moralize («снова не пришли») or auto-suspend customer

**Forbidden**:
- ❌ «Вы пропустили запись — это третий раз!» moralizing
- ❌ Auto-blacklist after N no_shows (always owner discretion)
- ❌ Public shaming surface to other customers

---

## 9. Templates summary (locked)

Customer-facing (this doc + cross-refs to conversational-ux-framework):
- §3.2 cancel confirmation prompt + step 3 confirmation + §3.3 reschedule offer
- §5.2 reschedule alternative offer + step 4 confirmation
- §3.5 late cancel acknowledgement
- §3.6 very-late cancel deflection
- §6.2 affected-by-schedule-change offer
- §6.4 no-response auto-cancellation final message
- §8.3 next-morning no-show check
- §5.4 reschedule cap exceeded handoff

Master-facing: extend [master-conversational-templates §5.7-5.9](./master-conversational-templates.md). New variants:
- 5.7-variant for late-cancel (with minutes_before context)
- 5.8-variant for cascade-driven reschedule (with `cascade_reason` flag)

Owner-facing: extend [owner-conversational-templates §6](./owner-conversational-templates.md). New surfaces:
- §6.6-variant for schedule-change cascade processing dashboard (per §6.5)
- §6.3-variant for late-cancel rate spike alert

---

## 10. Edge cases

### 10.1 Customer cancels booking that's 5 min from start
Treated as «late cancel» (<1h). Auto-refund if billable. Master gets immediate notification. Customer gets §3.5 acknowledgement template.

### 10.2 Customer cancels but already at salon (rare)
Customer message «отмени запись» while physically at salon. AI doesn't know location. Standard cancellation flow processes. Master/admin handles physical confrontation per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) HUMAN_LOCKED tier — AI escalates if customer becomes upset.

### 10.3 Reschedule to date past max_advance_days
Customer requests date > tenant's max_advance_days (e.g., wants Sept slot when max = 60 days = mid-July). AI:
```
Дальше {{max_advance_date}} запись пока недоступна. Хотите ближайшее свободное — {{nearest_alternative_slot}}?

[Да]   [Напомнить когда откроется]
```

«Напомнить когда откроется» → AI schedules reminder for `max_advance_date - 7` to ping customer.

### 10.4 Customer reschedules, then cancels new booking
Each step records its own event. Refund eligibility evaluated on FINAL cancellation per its own timing relative to the FINAL slot. Reschedule chain audit preserved via `attribution_metadata.rescheduled_from` chain.

### 10.5 Multi-tenant customer (works at salon A, books at salon B)
Cancel/reschedule scoped to per-tenant booking. Per [Q-CO5](../decisions-log.md) profiles are independent. Salon A booking cancellation has no effect on salon B.

### 10.6 Customer's preferred contact channel unavailable
If customer's MAX handle changed / chat_id stale → cancellation/reschedule confirmation message fails delivery. System emits `conversation.message.delivery.failed`. Owner sees alert. Cancellation still records (the change is server-side authoritative). Customer learns about cancellation when they next interact.

### 10.7 Customer cancels then re-books same slot 10 min later
Treated as two independent events: cancel + new booking (NOT reschedule). New booking gets fresh `booking_source` per its own creation flow. No special handling.

### 10.8 Customer cancels last-minute, master grants alternative gratis
Out of scope MVP. Master can manually create new booking with `manual_source_label = 'masterhouse_gesture'` per [`manual-booking-spec.md`](./manual-booking-spec.md).

### 10.9 ScheduleChangeRequest cascade overlaps with customer-initiated reschedule in flight
Customer has `RESCHEDULE_REQUESTED` state, then master ScheduleChangeRequest approved affecting old slot. System merges: AI sends single message acknowledging both:
```
Понимаю, переносим. Заодно {{master_first_name}} {{date_softened}} не сможет — давайте на {{new_alternative_slot}}?
```

### 10.10 No-show but customer claims they came
Customer messages «я приходила, но никого не было». Auto-escalate HUMAN_LOCKED per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md). Owner reviews access logs / master testimony. Never AI-side resolution.

---

## 11. Privacy boundaries

| Data | Customer sees | Master sees | Owner sees |
|---|---|---|---|
| Own cancellation reason | YES (they entered it) | NO | YES summary stats |
| Other customer's cancellation reason | NO | NO | YES summary stats |
| Cancellation reason free-text | YES (own) | NO — only category | YES — only category by default; full text on tap with audit log |
| Refund amount | NO (salon-side billing) | NO | YES |
| Master ScheduleChangeRequest reason | NO (softened only) | YES (own request) | YES (approval context) |
| Cascade processing progress | NO | NO | YES (operational visibility) |
| No-show pattern detection | NO | Aggregate count only | YES |
| Customer reschedule count for own booking | NO (avoid anchoring shame) | YES (aggregate) | YES |

---

## 12. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| «Жаль терять вас!» on customer cancel | Guilt-trip | Calm acknowledgement |
| «Перенесём вместо отмены?» auto-offered every cancel | Becomes nag | Offer only when reason suggests timing issue (§3.3 rule) |
| Discount offer to keep booking | Manipulative | NEVER on MVP. Customer-pays tier may explore Phase 3+. |
| Demanding cancel reason as required field | Wastes goodwill | Always optional |
| Multi-message cancel confirmation chain | Drags | Single confirmation + done |
| Showing customer how many times others cancel | Comparison stress | NEVER |
| Auto-blacklisting after N cancels | Authoritarian | Pattern surfaces to owner; owner discretion |
| Master sees free-text cancel reason verbatim | Privacy leak | Master sees only category; owner sees full with audit |
| Reschedule cap message that shames | «Вы переносили уже 3 раза!» = shame | Neutral: «Эта запись уже переносилась 3 раза. Передам {{owner}}?» |
| Cascading messages sent in batch | Spam-like | Per-customer individual, throttled, nearest-first |
| Auto-rebook customer without consent | Authority overstep | Always offer + wait for customer YES |
| Customer sees refund amount | Out of scope (their side) | NEVER mention monetary refund to customer |
| Owner notification on every cancel | Notification fatigue | Aggregate in digest; immediate only on pattern spike |
| No-show check-in same day | Assumes bad intent | Wait until next morning |
| No-show check-in pressuring «почему не пришли?» | Demanding explanation | «Всё ли хорошо?» — open and gentle |

---

## 13. Phasing

### Phase 1 (with Schedule MVP S2-S5)
- Cancel flow: §3 customer-initiated, bot DM + Mini App entry
- Reschedule flow: §5 customer-initiated
- Refund integration: §4 — uses existing attribution-policy event chain
- Master/owner notifications: per existing templates §7
- No-show: §8 detection + check-in

### Phase 2 (with master-mobile expansion)
- ScheduleChangeRequest cascade: §6 full implementation
- Batch cascade dashboard: §6.5 owner-side surface
- 48h auto-cancellation on no-response
- Reschedule cap implementation: §5.4

### Phase 3 (Settings Hub expansion)
- Tenant configurable cancellation policy (window, refund rules per tenant)
- Tenant configurable reschedule cap per booking type
- Customer reliability scoring (manual flag by owner)

### Phase 4+ (later)
- Deposit-required for low-reliability customers
- Customer-pays tier: cancellation fees paid by customer
- Predictive cancellation insight to owner («likely to cancel» score)

---

## 14. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-CR1** | Undo window after cancel — 5 sec sufficient or longer (15 sec)? | 5 sec MVP — match standard mobile undo patterns. Longer if customer feedback shows accidental cancels. | UX | 🟢 |
| **Q-CR2** | If customer cancels late + the booking was non-billable from start — any «sorry» softening? | NO — non-billable status is internal; customer doesn't see it. Same template either way. | UX | 🟢 |
| **Q-CR3** | Tenant configurable «brag» on training-related EVENT exception («Маша на тренинге в Москве»)? | NO MVP — privacy default. v1.1+ tenant toggle. Don't reveal master location/event by default. | UX + Policy | 🟢 |
| **Q-CR4** | Cancellation reason chips visible to which customer types? | All customers same chips MVP. v1.1+ might surface different chips per customer state (e.g., AT_RISK gets «Не подошло качество» chip; ACTIVE_REGULAR doesn't). | UX | 🟢 |
| **Q-CR5** | Reschedule allowed when master is `is_active=False` (archived)? | NO — UI prevents selection of archived master. Reschedule path defaults to master alternatives. | Eng | 🟢 |
| **Q-CR6** | Customer wants to reschedule to a slot blocked by `TimeBlock` (master's lunch/cleaning)? | Slot resolver excludes blocked slots → not shown. If customer messages free-text «перенеси на 13:00» (during lunch) → AI offers nearest free slot per resolver. | UX | 🟢 |
| **Q-CR7** | After 3-reschedule cap escalation, who in owner role can override — only owner or also admin? | Owner OR admin with `permission.schedule.override` (per [owner-templates §14 admin variants](./owner-conversational-templates.md#14-admin-role-variants)) | Policy | 🟡 |
| **Q-CR8** | Cascade timeout 48h — fixed or per-tenant configurable? | Fixed 48h MVP. v1.1+ tenant adjustable (legitimate range 24h–7d). | PM | 🟢 |
| **Q-CR9** | Auto-cancellation message tone when system terminates after no-reply — apologetic or neutral? | Neutral («не было ответа — отменила; будет нужно — пишите»). Apologetic implies fault, but customer's silence isn't a fault. | UX | 🟢 |
| **Q-CR10** | No-show check-in delivery time — fixed 9:00 next day or adaptive to customer's pattern? | Fixed 9:00-10:00 customer's TZ for MVP. Adaptive per Layer 5 Behavioral data v1.2+. | UX | 🟢 |
| **Q-CR11** | Master ScheduleChangeRequest of kind `custom_hours` (working but reduced hours, not full day off) — cascade only affects bookings in non-overlapping window? | YES — only bookings outside new working window get cascade; bookings within new hours stay CONFIRMED. | Eng + UX | 🟡 |
| **Q-CR12** | Reschedule analytics — show owner «N% of bookings get rescheduled»? | YES in weekly digest per [owner-templates §6.2](./owner-conversational-templates.md). Surface as neutral metric (not «proble m» framing). | UX | 🟢 |
| **Q-CR13** | Customer cancels via Mini App when offline (PWA / cached) — queue or fail? | Queue with sync-on-connect; show «изменения ждут сети» toast (per [schedule-editor-wireframes Q-SW12](./schedule-editor-wireframes.md)). | Eng | 🟢 |
| **Q-CR14** | If cancelled booking is recreated within 1 hour (same customer / master / service / slot) — treat as «I cancelled by mistake»? | YES soft-detection: don't refund the original cancel even if it was <1h (it wasn't a real cancel). Anti-abuse: customer can't cancel-then-rebook to dodge billing. Mark `attribution_metadata.likely_misclick = true`. | Eng + Policy | 🟡 |
| **Q-CR15** | Reschedule to a different SERVICE (not just different time) — supported? | NO MVP — that's a cancel + new booking. v1.2+ if usage data shows demand. Forced by data model (BookingRequest immutable service_id semantically). | UX | 🟢 |

---

## 15. Cross-document linkage

- [`attribution-policy.md`](./attribution-policy.md) §6 Q15 + Q12-β — refund rules driving §4
- [`manual-booking-spec.md`](./manual-booking-spec.md) — adjacent flow; cancel/reschedule on manual-created bookings same flow
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — HUMAN_LOCKED escalations §3.6 / §10.2 / §10.10
- [`conversational-ux-framework.md`](./conversational-ux-framework.md) — voice anchors throughout; §7.4 failure mode templates extended
- [`master-conversational-templates.md`](./master-conversational-templates.md) — §5.7/§5.8 cancel/reschedule notifications base
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) — §6.1/§6.2/§6.3 owner notification cascade
- [`event-taxonomy.md`](./event-taxonomy.md) §3.1 — `booking.cancelled` / `booking.rescheduled` / `booking.refunded` / `booking.no_show` events; new event `booking.affected_by_schedule_change` to add
- [`core-user-states.md`](./core-user-states.md) — repeated cancellation may signal state shift (POST_VISIT → AT_RISK if 3+ cancels in 60d)
- [`schedule-editor-wireframes.md`](./schedule-editor-wireframes.md) — owner cascade dashboard surface §6.5
- [`../handoffs/2026-05-18-schedule-management-handoff.md`](../handoffs/2026-05-18-schedule-management-handoff.md) §6 — ScheduleChangeRequest flow that triggers §6 cascade

---

## 16. What this unblocks

- **Schedule MVP S2 + S5 implementation** — cancellation + reschedule UX locked
- **Schedule MVP S4 ScheduleChangeRequest cascade** — per-booking customer re-offer flow locked
- **Refund event chain** — clear contract between attribution-policy + this flow + event-taxonomy
- **Anti-abuse mechanics** — reschedule cap + misclick detection (Q-CR14) prevent gaming
- **No-show retention loop** — next-morning gentle check-in template locked
- **Analytics dashboard cancellation/reschedule cards** — events + rules locked

## 17. What this does NOT unblock

- ❌ Tenant configurable cancellation policy editor (Phase 3 Settings Hub)
- ❌ Deposit-required mechanics (Phase 4+)
- ❌ Customer-pays cancellation fees (Phase 3+)
- ❌ Multi-tenant cancel coordination (different salons same customer — separate per Q-CO5)
- ❌ YClients-originated cancellation sync (Q-ATT-IMPL7)
- ❌ Skip legal review on refund language in customer-facing copy if introduced later

---

## 18. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-18 |
| Schedule MVP eng lead | ☐ | |
| Customer support / escalation lead | ☐ | |
| Legal (refund-event + договор-оферта alignment) | ☐ | |
| AI prompt engineering lead | ☐ | |

## Last verified
2026-05-18 (initial draft, customer cancellation + reschedule flow locked for Schedule MVP)
