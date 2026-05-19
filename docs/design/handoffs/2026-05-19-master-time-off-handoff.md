# Master Time-Off / Vacation / Sick Leave — Engineering Handoff

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Production-blocking — masters need formal leave flow vs ad-hoc «убери записи»
**Reads:** [`../policies/ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md), [`../policies/tenant-as-provider-model.md`](../policies/tenant-as-provider-model.md), [`../policies/master-conversational-templates.md`](../policies/master-conversational-templates.md) (r2), [`../handoffs/2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md), [`../policies/schedule-editor-wireframes.md`](../policies/schedule-editor-wireframes.md), [`./2026-05-19-master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md), [`./2026-05-19-master-reviews-feedback-handoff.md`](./2026-05-19-master-reviews-feedback-handoff.md), [`../policies/booking-conflict-resolution-ux.md`](../policies/booking-conflict-resolution-ux.md), [`../policies/customer-cancellation-reschedule-spec.md`](../policies/customer-cancellation-reschedule-spec.md), [`../policies/ayla-emergency-fallback-policy.md`](../policies/ayla-emergency-fallback-policy.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md)

> Master is sick. Master needs 2 weeks off. Master wants every Thursday afternoon free. Today these scenarios are handled ad-hoc via Slack to admin or manual schedule edit — no audit, no automatic customer rebooking, no earnings impact transparency. This handoff specifies the formal flow.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](../policies/ayla-identity-and-brand.md) memory 2026-05-19: master leave flow is **Ayla Pro** tenant-side per [`tenant-as-provider-model §5`](../policies/tenant-as-provider-model.md). Customer rebooking on master leave uses booking-conflict-resolution machinery → routes through emergency fallback `booking_conflict` tier per [`ayla-emergency-fallback-policy §3.2`](../policies/ayla-emergency-fallback-policy.md). Customer sees Ayla framing «{{master}} планы поменялись» — never «master sick». Deprecated `single-assistant-identity.md` + `conversation-ownership-policy.md` refs preserved as backend mechanic.

---

## 0. Why this exists

### 0.1 The operational gap

`master-conversational-templates.md §5.11 ScheduleChangeRequest` covers WORKING-HOURS edits (master initiates change to «recurring availability»). But:
- Sick days = SAME DAY emergency (no time for full request flow)
- Vacation = LONG TERM (1-4 weeks; bookings already taken)
- Planned leave = ASYMMETRIC (admin approves, customer notified)
- Recurring pattern = STRUCTURAL («every Thursday after 17:00 off»)

Ad-hoc «убери записи» via Slack/WhatsApp:
- No audit trail
- No automated customer rebooking
- No payout impact tracking
- No founder-side visibility for trend analytics
- Risk of forgotten future bookings during long leave
- Conflicts with [`booking-conflict-resolution-ux.md`](../policies/booking-conflict-resolution-ux.md) reconciliation engine

### 0.2 The promise

Single source for:
- 4 leave types §3 (SICK_DAY / VACATION / PLANNED_LEAVE / RECURRING_PATTERN)
- Per-type request → approval → execution flow §4-7
- Customer notification + rebooking flow §8
- Earnings cycle interaction §9
- Admin approval queue + emergency override §10
- 4 NEW models + 16 endpoints + 14 events

---

## 1. Scope

### IN
- 4 leave types §3
- Master Mini App «Запросить выходные» flow
- SICK_DAY same-day flow with auto-admin-notification (no approval blocking)
- VACATION request → admin approval → customer rebooking
- PLANNED_LEAVE (e.g. wedding, conference) — short notice OK
- RECURRING_PATTERN — change of working hours («Thursdays off») via [`master-conversational-templates §5.11`](../policies/master-conversational-templates.md) but full handoff here
- Master availability lock during approved leave
- Customer-facing rebooking flow «{{master}} в этот день не сможет — могу предложить {{alts}}» (uses [`booking-conflict-resolution §6.6b`](../policies/booking-conflict-resolution-ux.md) machinery)
- Earnings cycle pause / continuation rules §9
- Admin queue + approval UX
- AI Bot DM touchpoints for master + admin
- Multi-tenant master leave per tenant
- Replacement master suggestion (NOT auto-replacement; admin chooses)
- Return-from-leave check-in
- 14 NEW events for event-taxonomy

### OUT
- Long-term leave / extended absence (≥ 30 days) — Phase 3+; falls into substitution doc #4
- Paid time off accounting (which days count as PTO budget) — out of scope MVP (salon's HR responsibility)
- Maternity / parental leave — Phase 4+ regulatory-specific
- Master ↔ master shift swap («покрой меня в понедельник») — Phase 4+
- Multi-master coverage planning («Anna's clients go to Lena») — admin's call, manual in MVP
- Automatic «найди замену» bot — admin manually picks
- Health-records integration (doctor's note upload) — out of scope; KKT/regulatory edge
- Calendar-app integration (Google Calendar etc.) — Phase 4+
- Mass leave (whole salon closed for holiday) — separate scope `tenant-holiday-policy.md` future

---

## 2. Strategic constraints — non-negotiable

### 2.1 Master autonomy + admin authority balance
- SICK_DAY: master decides, admin notified, no blocking approval needed (urgent)
- VACATION/PLANNED_LEAVE: admin approves
- RECURRING_PATTERN: admin approves (per [`master-conversational-templates §5.11`](../policies/master-conversational-templates.md))

Master cannot lock themselves out without admin awareness; admin cannot override an emergency SICK_DAY but can challenge after-the-fact.

### 2.2 Customer never sees raw schedule change
Per [`single-assistant-identity §2.2`](../policies/single-assistant-identity.md): customer sees rebooking offer framed naturally («у {{master}} планы поменялись на этот день») — NEVER «sick day» / «vacation» / «cancellation» / raw labels.

### 2.3 NEVER share medical reason with customer
Master can optionally note reason in admin-visible field, but customer never sees. Privacy boundary.

### 2.4 Earnings impact transparent
Per [`master-earnings-handoff §2.6`](./2026-05-19-master-earnings-handoff.md): cycle accounting unaffected unless cancellation impacts compensable bookings. Master sees clear «pause cycle» indicator §9.

### 2.5 Conflict-resolution engine wired in
Per [`booking-conflict-resolution-ux §3.6b`](../policies/booking-conflict-resolution-ux.md): leave triggers MASTER_DRIFT conflicts for impacted bookings. Per booking, customer gets alt-master / alt-time / cancellation choice.

### 2.6 Admin can emergency-override
If master abuses SICK_DAY (e.g., 3 in a month), admin can flag pattern (no system block — soft signal). Founder analytics surface §11.

### 2.7 NO master shaming
- No leaderboard of «who took most days off»
- No streak «N days without leave!»
- Sick days are health matter, not performance metric
- Pattern detection internal-admin-only, never to AI mediation

### 2.8 Earnings billing-attribution
Per [`attribution-policy.md`](../policies/attribution-policy.md): cancellations due to master leave → `booking_source` unchanged but `cancellation_reason='master_unavailable_leave'`. Doesn't reclassify ai_direct.

### 2.9 SLA matrix
- SICK_DAY: auto-applied, admin notified within 5min
- VACATION: admin should approve within 24h (auto-reminder after 18h)
- PLANNED_LEAVE: admin should approve within 48h
- RECURRING_PATTERN: admin reviews within 7d (non-urgent)

### 2.10 Return-from-leave warm-up
Master returning after vacation receives Bot DM check-in §6.5. Day 1 back has lighter recommended schedule (admin can pre-arrange).

---

## 3. Leave types

### 3.1 SICK_DAY (urgent, same-day)

- Master triggers from Mini App «Заболела»
- Effective IMMEDIATELY (no approval block)
- Affects bookings today + tomorrow (if morning report)
- Admin auto-notified within 5min
- Customer rebooking initiated automatically
- Admin can mark «restored» if master comes back next day OK
- Recommended cadence: max 4 SICK_DAYS / 12 months without admin discussion (soft, NOT enforced; pattern detection signal only)

### 3.2 VACATION (planned, 3+ days)

- Master submits request with date range + dates locked from booking
- Admin approves (default within 24h)
- Affects bookings in range
- Customer rebooking once approved
- Cycle accounting: cycle continues during vacation; just no earnings accrual for absent days
- Master receives reminders day-before vacation + day-of-return §6.4

### 3.3 PLANNED_LEAVE (short notice, 1-2 days off, non-sick)

- Examples: «wedding tomorrow», «doctor's appointment», «kids' graduation»
- Master submits + admin approves (24-48h)
- Less formal than vacation; same machinery
- Reason field optional (admin sees, NEVER customer)

### 3.4 RECURRING_PATTERN (structural change)

- Master changes working hours («каждый четверг после 17:00 не работаю»)
- Effective from future date (default 4 weeks out to clear existing bookings)
- Admin approves
- Per [`master-conversational-templates §5.11`](../policies/master-conversational-templates.md) but with model + audit added here

### 3.5 Quick-glance comparison

| Type | Notice | Approval needed | Blocks bookings | Earnings cycle impact | Customer rebook |
|---|---|---|---|---|---|
| SICK_DAY | None (urgent) | NO (auto, admin notified) | Today + maybe tomorrow | Day excluded | YES via conflict-resolution-ux |
| VACATION | 3+ days advance | YES (24h) | Date range | Days excluded | YES |
| PLANNED_LEAVE | 1-2 days advance | YES (24-48h) | Date range | Days excluded | YES |
| RECURRING_PATTERN | 4 weeks advance | YES (7d) | Future per pattern | Reflects in new cycles | YES for affected future bookings |

---

## 4. Master Mini App flows

### 4.1 Entry — Schedule tab → «Запросить выходные»

Per [`master-mobile-handoff §5 Screen M3`](./2026-05-18-master-mobile-handoff.md): Schedule screen has new button «🛌 Запросить выходные» (rest day icon).

Tap → type-selector:

```
┌────────────────────────────────────────┐
│ Что произошло?                          │
├────────────────────────────────────────┤
│ ⦿ 🤒 Заболела (нужно сегодня)           │
│ ◯ ✈ Отпуск (от 3 дней)                  │
│ ◯ 📅 Один-два дня по делам              │
│ ◯ 🔁 Изменить рабочие дни/часы          │
│                                        │
│ [Дальше]                                 │
└────────────────────────────────────────┘
```

### 4.2 SICK_DAY flow

```
┌────────────────────────────────────────┐
│ ← Сегодня не работаю                    │
├────────────────────────────────────────┤
│ Скажите, что нужно сделать:             │
│                                        │
│ ⦿ Только сегодня                        │
│ ◯ Сегодня и завтра                       │
│ ◯ Дольше — отметить отпуск через        │
│   подробный запрос                       │
│                                        │
│ Сообщить {{salon_owner}}:                │
│ ✓ Да, помощник сообщит                  │
│                                        │
│ ── Что с вашими сегодняшними записями? ─│
│ У вас сегодня 3 записи. Помощник        │
│ предложит клиентам перенести или        │
│ выбрать другого мастера.                 │
│                                        │
│ [Подтвердить]                            │
└────────────────────────────────────────┘
```

After confirm → instant:
- Master's bookings today (and tomorrow if selected) marked CONFLICT
- Booking-conflict engine §3.6b runs per booking
- Admin Bot DM + Mini App badge §10.1
- Master Bot DM confirmation §6.1

NO «reason» field for SICK_DAY (health privacy).

### 4.3 VACATION flow

```
┌────────────────────────────────────────┐
│ ← Отпуск                                 │
├────────────────────────────────────────┤
│ С: [10 июня 2026 ▾]                     │
│ По: [24 июня 2026 ▾]                    │
│ (14 дней)                                │
│                                        │
│ Причина (необязательно, только          │
│ {{salon_owner}} увидит):                 │
│ [_____________________________]         │
│                                        │
│ ── Что с записями в эти даты ──         │
│ В период попадает 12 записей. Что       │
│ предложить клиентам?                     │
│                                        │
│ ⦿ Альтернативный мастер у студии        │
│   (вы укажете, если хотите)              │
│ ◯ Перенести на до или после отпуска    │
│ ◯ Просто отменить                       │
│                                        │
│ ── Подсказать клиентам ──                │
│ Какого мастера предложить?               │
│ ◯ Не указывать                           │
│ ◯ Лена ⭐ 4.8                            │
│ ◯ Марина ⭐ 4.7                          │
│                                        │
│ [Отправить на согласование]              │
└────────────────────────────────────────┘
```

### 4.4 PLANNED_LEAVE flow

Similar to vacation but date defaults to 1-2 days, reason field shorter form. Same mechanics.

### 4.5 RECURRING_PATTERN flow

```
┌────────────────────────────────────────┐
│ ← Изменить рабочие дни/часы              │
├────────────────────────────────────────┤
│ Текущее расписание:                      │
│ Пн-Сб: 10:00-20:00                       │
│ Вс: выходной                              │
│                                        │
│ Что хотите изменить?                     │
│                                        │
│ Понедельник:    [10:00 ▾] – [20:00 ▾]   │
│ Вторник:        [10:00 ▾] – [20:00 ▾]   │
│ Среда:          [10:00 ▾] – [20:00 ▾]   │
│ Четверг:        [10:00 ▾] – [17:00 ▾] ← │
│ Пятница:        [10:00 ▾] – [20:00 ▾]   │
│ Суббота:        [10:00 ▾] – [20:00 ▾]   │
│ Воскресенье:    ☐ работаю                │
│                                        │
│ ── Когда применить ──                    │
│ С [1 июля 2026 ▾] (через 6 недель)     │
│ Это даст время разобраться с уже       │
│ имеющимися записями.                     │
│                                        │
│ В период между сегодня и 1 июля         │
│ попадает 4 записи в новые «нерабочие»   │
│ часы. С ними что?                        │
│ ⦿ Оставить как есть, выполнить           │
│ ◯ Предложить клиентам перенести          │
│                                        │
│ [Отправить на согласование]              │
└────────────────────────────────────────┘
```

### 4.6 Master sees own leave history

In Profile section:

```
┌────────────────────────────────────────┐
│ ← Мои выходные                          │
├────────────────────────────────────────┤
│ ── Текущие / запланированные ──         │
│                                        │
│ Отпуск 10-24 июня                       │
│ Статус: ⏳ ждёт согласования             │
│ [Изменить]   [Отменить запрос]           │
│                                        │
│ ── История ──                            │
│                                        │
│ Заболела 5 мая                          │
│ Восстановлена                            │
│                                        │
│ Отпуск 1-7 февраля                       │
│ Завершён                                 │
└────────────────────────────────────────┘
```

NO «days off this year» counter (per §2.7 anti-shame; admin can see, master doesn't need quota visibility unless tenant has PTO policy — Phase 4+).

---

## 5. Admin side

### 5.1 Admin Mini App «Запросы на выходные» tab

```
┌────────────────────────────────────────┐
│ 🛌 Запросы на выходные (3)              │
├────────────────────────────────────────┤
│ ── Срочные ──                            │
│                                        │
│ 🤒 Анна — заболела                       │
│ Сегодня (10:00). 3 записи затронуто.    │
│ Помощник уже переоформляет.              │
│ [Посмотреть]                             │
│                                        │
│ ── На согласование ──                    │
│                                        │
│ ✈ Лена — отпуск 10-24 июня (14 дней)    │
│ 12 записей в этих датах                  │
│ Прислано 19 мая в 10:15                  │
│ SLA: 8 ч из 24                           │
│ [Рассмотреть]                            │
│                                        │
│ 🔁 Марина — изменить четверги (с 1 июля)│
│ 4 записи в новые «нерабочие» часы        │
│ Прислано 18 мая                          │
│ SLA: 36ч из 7д                           │
│ [Рассмотреть]                            │
└────────────────────────────────────────┘
```

### 5.2 Approval screen

```
┌────────────────────────────────────────┐
│ ← Отпуск: Лена                          │
├────────────────────────────────────────┤
│ Период: 10-24 июня (14 дней)             │
│ Причина: «свадьба в Италии»              │
│ (видите только вы, клиентам не покажу)   │
│                                        │
│ ── Что предложила Лена ──                │
│ Альтернативу: Марина для замены           │
│                                        │
│ ── Что затронет ──                       │
│ В период попадает 12 записей:            │
│ • 8 можно безпроблемно перенести на      │
│   неделю до или после                    │
│ • 3 заявлены на конкретные дни в отпуске │
│ • 1 клиент уже спрашивал у Лены об       │
│   июне                                    │
│                                        │
│ ── Влияние на доход Лены ──              │
│ Цикл с 8 по 22 июня:                     │
│ Ожидаемый доход ≈ 0 ₽ (без работы)       │
│                                        │
│ ── Действия ──                           │
│ [✓ Согласовать]                          │
│ [💬 Обсудить с Леной]                    │
│ [✗ Не согласовать]                       │
└────────────────────────────────────────┘
```

«Согласовать» → leave approved + customer rebooking starts.
«Обсудить» → internal admin chat thread opened.
«Не согласовать» → leave rejected with required comment, master notified.

### 5.3 Sick-day acknowledgment

For SICK_DAY admin sees notification but no «approve» needed:

```
┌────────────────────────────────────────┐
│ 🤒 Анна заболела                         │
├────────────────────────────────────────┤
│ Сегодня — 3 записи. Помощник уже         │
│ предлагает клиентам перенести или        │
│ выбрать другого мастера.                  │
│                                        │
│ [Посмотреть детали]                      │
│ [💬 Написать Анне]                       │
│                                        │
│ ⓘ Если Анна вернётся завтра — нажмите   │
│  «Анна снова работает»                   │
│ [Анна снова работает]                    │
└────────────────────────────────────────┘
```

### 5.4 Pattern detection (admin-only)

If master has 3+ SICK_DAY in 90 days, admin sees soft signal in approval queue header:

```
┌────────────────────────────────────────┐
│ ⓘ Заметили: Анна болела 4 раза за       │
│   последние 90 дней. Может, поговорить?  │
│                                        │
│ [Открыть статистику]                     │
│ [Скрыть на 30 дней]                      │
└────────────────────────────────────────┘
```

This is for ADMIN ONLY. Master sees no such signal. AI to customer or master makes NO reference to «pattern». Per §2.7.

---

## 6. AI Bot DM touchpoints

### 6.1 Master confirmation post-SICK_DAY submission

```
{{master_first_name}}, отметила что сегодня вы не работаете. Поправляйтесь.

3 записи на сегодня — помощник предлагает клиентам перенести или выбрать
другого мастера. Если хотите узнать, как пошли переносы — напишите.
```

### 6.2 Master confirmation post-VACATION/PLANNED_LEAVE submission

```
{{master_first_name}}, запрос на отпуск 10-24 июня отправлен {{salon_owner}}.
Подтверждение обычно в течение суток. Когда {{salon_owner}} согласует —
помощник предложит клиентам перенести или выбрать другого мастера.
```

### 6.3 Master notified of admin approval

```
{{master_first_name}}, {{salon_owner}} согласовала отпуск с 10 по 24 июня.
Помощник начинает переоформлять записи. Если потребуется ваша помощь
(например, написать клиенту лично) — я напишу.
```

### 6.4 Pre-vacation reminder (T-1 day before vacation starts)

```
{{master_first_name}}, завтра начало отпуска. На вчерашний день осталось
закрыть 1 запись.

С возвращением — буду писать только по самым срочным делам.
[Открыть]
```

### 6.5 Return-from-leave check-in (day of return)

```
С возвращением, {{master_first_name}}!

На сегодня записей: 4. Первая — {{customer_first_name}} в {{time}}.

Вот короткое:
• {{customer_first_name}} в {{time}} ({{service}})
• ...

Как дела с настройкой графика — всё ок?
```

### 6.6 Rejected request

```
{{master_first_name}}, {{salon_owner}} не смогла согласовать отпуск 10-24 июня.

Комментарий: «{{admin_reason}}»

Может, обсудить даты? [Открыть чат]
```

### 6.7 Admin notified — SICK_DAY

```
🤒 Анна сообщила что сегодня не работает. На сегодня у неё 3 записи.

Помощник начал переоформлять. Если хотите посмотреть детали — [Открыть].
```

### 6.8 Admin notified — VACATION/PLANNED_LEAVE request

```
✈ Лена прислала запрос на отпуск 10-24 июня (14 дней). Затронет 12 записей.

Зайдите рассмотреть — SLA 24 часа. [Открыть]
```

---

## 7. Customer-facing rebooking

### 7.1 Reuses booking-conflict-resolution-ux §3.6b machinery

When leave approved (or SICK_DAY effective), affected bookings open MASTER_DRIFT conflict per booking. Customer receives the §6.6b master-substitution message from [`booking-conflict-resolution-ux.md`](../policies/booking-conflict-resolution-ux.md).

### 7.2 Cascade ordering

If single VACATION affects 12 bookings:
- Bookings ordered by proximity (closest date first)
- Customer messages sent staggered (max 5 / min apart) to avoid mass-message look
- Most distant bookings (2 weeks out) — message can wait 24h
- Imminent (next 48h) — immediate

### 7.3 Customer message framing

Per [`booking-conflict-resolution-ux §6.6b`](../policies/booking-conflict-resolution-ux.md): never «sick» / «vacation» / «leave» words. Use:

```
{{customer_first_name}}, в этот день у {{master}} планы поменялись и
{{date}} {{time}} она не сможет.

Могу предложить ту же процедуру у:
• Лена ⭐ 4.8 ({{exp_years}} лет опыта)
• Марина ⭐ 4.7

Или подобрать другую дату у {{master}} — есть свободно: {{alt_dates}}.

[Лена]   [Марина]   [Перенести]   [Отменить]
```

Master can mark «{{customer}} — обязательно лично напишу» on per-booking — opt-out from AI rebooking for that one customer.

### 7.4 Anti-cascade for return-customer

If customer just had a booking recently and AI just messaged them recently, throttle:
- Don't send rebooking message within 2h of last AI message to that customer
- AI scheduler buffers

### 7.5 Long-leave «прощальное» message

If leave 7+ days for a master with strong customer relationships, master can opt-in to «final message before leave» template:

```
{{master_first_name}} попросила передать: «До скорой встречи, спасибо что
были у меня. Вернусь 25 июня.»

В её отсутствие могу предложить другого мастера или перенос. Как удобнее?
```

Master writes the message themselves via Mini App; AI relays as-is (with [Edit]).

---

## 8. Earnings cycle interaction

### 8.1 Cycle accumulating during leave
- Leave doesn't pause `PayoutCycle`
- No new `MasterEarning` rows during absence (no bookings completed)
- Cycle accumulates from prior bookings + tips
- Cycle closes on schedule

### 8.2 Sick-day cancellation impact
- Bookings cancelled due to master leave → no `MasterEarning` for those bookings
- Customer rebooked with alt master → that alt master earns
- Audit captures cancellation_reason

### 8.3 Long leave (vacation) and cycle preview
Master Mini App «Доход» shows cycle preview = 0 ₽ if vacation covers full cycle. Admin Mini App shows accurately too.

### 8.4 Pay-day during vacation
- Cycle that PRECEDES vacation pays as normal (admin still triggers payout)
- Cycle DURING vacation may be 0 ₽ if all bookings cancelled
- Returning master sees clear cycle history

---

## 9. Data models

### 9.1 `MasterLeaveRequest`

```python
class MasterLeaveRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='leave_requests')

    LEAVE_TYPE_CHOICES = [
        ('sick_day', 'Sick day'),
        ('vacation', 'Vacation'),
        ('planned_leave', 'Planned leave (1-2 days)'),
        ('recurring_pattern_change', 'Recurring schedule change'),
    ]
    leave_type = models.CharField(max_length=32, choices=LEAVE_TYPE_CHOICES)

    date_from = models.DateField()
    date_to = models.DateField()
    # For SICK_DAY, date_to = date_from or date_from+1
    # For RECURRING_PATTERN_CHANGE, date_from = effective_from, date_to = null (or far future)

    days_count = models.IntegerField()

    reason_admin_only = models.TextField(blank=True, default='', max_length=500)
    # Visible to admin only, NEVER to customer

    suggested_alternative_master = models.ForeignKey('staff.Master', null=True, blank=True, on_delete=SET_NULL, related_name='+')

    RESOLUTION_PREFERENCE_CHOICES = [
        ('alt_master', 'Suggest alternative master'),
        ('alt_date', 'Suggest different date'),
        ('cancel_simple', 'Just cancel'),
    ]
    resolution_preference = models.CharField(max_length=32, choices=RESOLUTION_PREFERENCE_CHOICES, default='alt_master')

    # For RECURRING_PATTERN_CHANGE only
    new_pattern = models.JSONField(default=dict, blank=True)
    # { 'monday': {'start': '10:00', 'end': '20:00', 'working': true}, ... }

    STATUS_CHOICES = [
        ('submitted', 'Submitted'),
        ('auto_approved', 'Auto-approved (sick day)'),
        ('admin_reviewing', 'Awaiting admin review'),
        ('approved', 'Approved by admin'),
        ('rejected', 'Rejected by admin'),
        ('cancelled_by_master', 'Cancelled by master'),
        ('expired', 'Expired (admin no action)'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='submitted')

    submitted_at = models.DateTimeField(auto_now_add=True)
    sla_due_at = models.DateTimeField()
    admin_decision_at = models.DateTimeField(null=True, blank=True)
    admin_decided_by = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    admin_decision_reason = models.TextField(blank=True, default='', max_length=500)

    affected_bookings_count = models.IntegerField(default=0)
    affected_bookings_resolved = models.IntegerField(default=0)
    # Updated as customer rebooks

    sick_day_restored_at = models.DateTimeField(null=True, blank=True)
    # When admin marks «back to work»

    class Meta:
        indexes = [
            Index(fields=['tenant', 'status', '-submitted_at']),
            Index(fields=['master', 'date_from']),
            Index(fields=['sla_due_at']),
        ]
```

### 9.2 `MasterLeaveBookingImpact`

Per-booking impact log.

```python
class MasterLeaveBookingImpact(models.Model):
    leave_request = models.ForeignKey(MasterLeaveRequest, on_delete=CASCADE, related_name='booking_impacts')
    booking = models.ForeignKey('booking.Booking', on_delete=CASCADE, related_name='leave_impacts')

    RESOLUTION_CHOICES = [
        ('pending', 'Pending customer choice'),
        ('alt_master', 'Customer chose alt master'),
        ('alt_date', 'Customer chose different date'),
        ('cancelled', 'Cancelled'),
        ('master_personal_message', 'Master will message customer personally'),
    ]
    resolution = models.CharField(max_length=32, choices=RESOLUTION_CHOICES, default='pending')

    alt_master = models.ForeignKey('staff.Master', null=True, blank=True, on_delete=SET_NULL, related_name='+')
    new_slot_start = models.DateTimeField(null=True, blank=True)

    customer_messaged_at = models.DateTimeField(null=True, blank=True)
    customer_responded_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
```

### 9.3 `MasterRecurringSchedule` — for RECURRING_PATTERN

Existing or new model:

```python
class MasterRecurringSchedule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='recurring_schedules')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    pattern = models.JSONField()  # {monday: {start, end, working}, ...}

    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)
    # Allows historical reasoning + future changes

    created_via_leave_request = models.ForeignKey(MasterLeaveRequest, null=True, blank=True, on_delete=SET_NULL, related_name='+')

    class Meta:
        indexes = [
            Index(fields=['master', 'tenant', '-effective_from']),
        ]
```

### 9.4 `SickDayPatternFlag`

Admin-side soft signal per master.

```python
class SickDayPatternFlag(models.Model):
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='+')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    detected_at = models.DateTimeField(auto_now_add=True)
    sick_day_count_90d = models.IntegerField()
    admin_acknowledged = models.BooleanField(default=False)
    admin_acknowledged_at = models.DateTimeField(null=True, blank=True)
    hidden_until = models.DateTimeField(null=True, blank=True)
    # Admin can «hide for 30 days»
```

---

## 10. API contracts

### 10.1 Master endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/master/leave/sick-day` | Trigger SICK_DAY (immediate) |
| POST | `/api/v1/master/leave/vacation` | Submit vacation request |
| POST | `/api/v1/master/leave/planned` | Submit planned leave |
| POST | `/api/v1/master/leave/recurring-change` | Submit recurring pattern change |
| GET | `/api/v1/master/leave` | List own leave history |
| GET | `/api/v1/master/leave/<id>` | Detail |
| PATCH | `/api/v1/master/leave/<id>/cancel-request` | Cancel pending request |
| POST | `/api/v1/master/leave/<id>/personal-message-for-booking` | Master opt-out AI rebooking for specific customer §7.3 |

### 10.2 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/leave/queue` | Open requests across masters |
| POST | `/api/v1/admin/leave/<id>/approve` | Approve §5.2 |
| POST | `/api/v1/admin/leave/<id>/reject` | Reject with reason |
| POST | `/api/v1/admin/leave/<id>/restored` | Master restored from sick §5.3 |
| GET | `/api/v1/admin/leave/pattern-flags` | List pattern flags §5.4 |
| POST | `/api/v1/admin/leave/pattern-flags/<id>/hide` | Hide 30d |
| GET | `/api/v1/admin/leave/master/<master_id>` | Per-master leave history |

### 10.3 Validation snippets

POST `/master/leave/sick-day`:
```json
{
  "include_tomorrow": false
}
```
- Master can only have 1 active sick_day at a time (otherwise update existing)
- Auto-creates `MasterLeaveRequest` with `status='auto_approved'`

POST `/master/leave/vacation`:
```json
{
  "date_from": "2026-06-10",
  "date_to": "2026-06-24",
  "reason_admin_only": "wedding in Italy",
  "resolution_preference": "alt_master",
  "suggested_alternative_master_id": 123
}
```
- `date_from` >= today + 3 (vacations need lead time; admin can override case-by-case)
- `date_to` >= `date_from`
- days_count auto-computed
- max vacation = 90 days MVP (Phase 4+ extended leave separate)

### 10.4 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/leave/<id>/trigger-rebooking` | Cron triggers customer rebooking machinery |
| POST | `/internal/leave/sick-day-pattern-scan` | Periodic pattern detection |

---

## 11. Events emitted

Per [`event-taxonomy.md`](../policies/event-taxonomy.md) — add to `3.9 master leave domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| Sick day triggered | NEW: `leave.sick_day.triggered` | include_tomorrow, affected_bookings_count |
| Sick day restored | NEW: `leave.sick_day.restored` | duration_hours |
| Vacation submitted | NEW: `leave.vacation.submitted` | days_count |
| Planned leave submitted | NEW: `leave.planned.submitted` | |
| Recurring change submitted | NEW: `leave.recurring_change.submitted` | |
| Admin approved | NEW: `leave.approved` | leave_type, days_count |
| Admin rejected | NEW: `leave.rejected` | reason_provided |
| SLA breached (admin no action) | NEW: `leave.sla_breached` | leave_type, age_hours |
| Master cancelled request | NEW: `leave.cancelled_by_master` | |
| Pattern flag detected | NEW: `leave.pattern_flag_detected` | sick_count_90d |
| Customer rebooked due to leave | NEW: `leave.customer_rebooked` | leave_id, booking_id |
| Booking cancelled due to leave | NEW: `leave.booking_cancelled` | leave_id, booking_id |
| Master pre-vacation reminder sent | NEW: `leave.pre_vacation_reminder_sent` | |
| Master return check-in sent | NEW: `leave.return_checkin_sent` | |

14 NEW events §11.

---

## 12. AI ↔ leave touchpoint mapping

Per [`master-conversational-templates §5`](../policies/master-conversational-templates.md): add 5 new master touchpoints + 2 admin touchpoints + integrates with existing customer-facing 5.7/5.8 messaging.

### New master touchpoints
- 5.25 sick day confirmation §6.1
- 5.26 vacation submitted §6.2
- 5.27 leave approved §6.3
- 5.28 pre-vacation reminder §6.4
- 5.29 return check-in §6.5
- 5.30 leave rejected §6.6

### New admin touchpoints (in admin-facing template doc — currently nascent; placeholder until admin templates spec is written)
- ADM.1 admin notified sick day §6.7
- ADM.2 admin notified leave request §6.8

---

## 13. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Customer sees «отпуск» / «болеет» label | Privacy + framing §2.2 | «планы поменялись» §7.3 |
| Master sees «days off this year» quota | Anti-shame §2.7 | Just history list §4.6 |
| Auto-approve vacation without admin | Schedule chaos | Admin approves except SICK_DAY |
| SICK_DAY requires reason | Health privacy §2.3 | NO reason field |
| Pattern detection visible to AI (used in customer responses) | Privacy + judgmental | Admin-only signal §2.7 |
| Streak counter «N working days in a row» | Anti-streak | NEVER |
| Force replace master without customer choice | Customer autonomy | §7.3 customer chooses alt-master vs reschedule vs cancel |
| Apply pattern change retroactively | Existing bookings break | Future-dated effective; protection window §4.5 |
| Cycle payout 0 ₽ with no warning | Bad transparency | Cycle preview shows 0 + reason |
| Cascade 12 customer messages in 1 minute | Bot-stalker feel | Staggered §7.2 |
| Master submits vacation 1 hour out | Insufficient lead | 3-day minimum, admin can override |
| Leave reason shared with customer | Privacy | NEVER |
| Auto-assign alt master | Removes admin authority | Admin or customer picks |
| Auto-cancel return-check-in if no response | Pressure | Optional, master ignores OK |
| Master shaming for many sick days | Health stigma | Admin signal only, soft handling |
| Auto-revoke previously-approved vacation | Trust violation | Cancel needs explicit admin + reason + apology |

---

## 14. Acceptance criteria (engineering checklist)

- [ ] 4 models §9 (MasterLeaveRequest, MasterLeaveBookingImpact, MasterRecurringSchedule, SickDayPatternFlag)
- [ ] Migration clean
- [ ] 8 master endpoints + 7 admin + 2 internal §10
- [ ] Cross-master 403 on leave endpoints
- [ ] SICK_DAY flow auto-approves; admin notified
- [ ] VACATION/PLANNED_LEAVE flow → admin approval
- [ ] RECURRING_PATTERN flow → admin approval, future-dated effective
- [ ] Conflict-engine integration §7.1 (per [`booking-conflict-resolution-ux §3.6b`](../policies/booking-conflict-resolution-ux.md))
- [ ] Customer rebooking message framing §7.3
- [ ] Cascade throttle §7.2
- [ ] Master Bot DM templates §6.1-6.6
- [ ] Admin Bot DM templates §6.7-6.8
- [ ] Admin Mini App «Запросы на выходные» tab §5.1
- [ ] Admin approval screen §5.2 with affected-booking summary + earnings impact preview
- [ ] Pattern flag detection §5.4 + admin signal
- [ ] Cycle preview correctly reflects 0 ₽ during full-cycle vacation §8.3
- [ ] SLA breach scanner §2.9
- [ ] 14 events §11
- [ ] PII rules §2.3 leave reason never to customer
- [ ] Tests: 4 leave types end-to-end; pattern flag detection; cascade throttle; cross-master denial; cycle interaction; customer rebooking framing; admin reject + master notify; recurring pattern future-effective
- [ ] Anti-pattern review §13

---

## 15. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-MTL1** | Vacation lead time — 3 days hard minimum or soft warning? | Soft warning + admin can override on case-by-case. Hard cap might block legitimate emergencies. | Policy | 🟢 |
| **Q-MTL2** | Pattern flag threshold — 3 sick_days in 90d or different? | 3/90d MVP; tune based on data. NOT visible to master. | Policy + HR | 🟡 |
| **Q-MTL3** | Master can edit submitted-pending vacation? | YES via PATCH (re-enters admin queue with annotation). Within 24h of submission only. After admin starts review, master must cancel + resubmit. | Policy + Eng | 🟢 |
| **Q-MTL4** | RECURRING_PATTERN protection window — 4 weeks or 6 weeks? | 4 weeks MVP §3.4 (reasonable booking horizon). Admin can flex per case. | Policy | 🟢 |
| **Q-MTL5** | Master goes on long leave (> 30 days) — does this doc handle or does doc #4? | Doc #4 (substitution) handles ≥ 30 days. This doc up to 30 days. Edge: 28-32 days picks one based on master's intent. | PM + UX | 🟡 |
| **Q-MTL6** | Holidays / public closures — same flow or separate `tenant-holiday-policy`? | Separate policy doc future. Master leave doesn't include tenant-wide closures. | PM | 🟢 |
| **Q-MTL7** | Replacement master availability check before suggesting in §5.2? | YES — admin sees if Marina is even available those dates before suggesting. Adds 1 admin Mini App query. | UX + Eng | 🟢 |
| **Q-MTL8** | Customer's response time before AI auto-cancels rebooking? | Per [`booking-conflict-resolution §5.5`](../policies/booking-conflict-resolution-ux.md): 2h time-mismatch, 4h master-drift. Same applies. | Policy | 🟢 |
| **Q-MTL9** | Multi-tenant master leave — separate per tenant? | YES — master can be on leave at tenant A while working at tenant B. Each tenant's leave-request flow independent. UI shows tenant selector. | Eng | 🟡 |
| **Q-MTL10** | Sick day continuation (Anna sick again tomorrow after sick today) — extend or new? | Extend existing if same calendar day +1 morning. New record if gap. | Policy + Eng | 🟢 |
| **Q-MTL11** | What if customer in HUMAN_LOCKED tier when master leave triggers? | Per [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md): admin handles rebooking message. AI offers admin a draft. | Policy | 🔴 PRE-DEPLOY |
| **Q-MTL12** | «Master personal message» opt-out §7.3 — master OR AI sends? | Master sends own message; AI doesn't send AI-generated rebook for that one booking. Master uses Mini App «Связаться с {{customer}}» flow (from existing master-conversational-templates §5.6). | UX + Eng | 🟡 |
| **Q-MTL13** | Vacation reason field analytics — anonymized aggregate? | NO MVP — too small sample, too sensitive. Phase 4+ if useful aggregate emerges. | Privacy | 🟢 |
| **Q-MTL14** | Earnings cycle that contains BOTH working and leave days — partial accrual? | Cycle accumulates as bookings complete. Leave days just have no bookings to complete. Cycle preview accurately reflects. No special «pause» state for partial cycles. | Eng | 🟢 |
| **Q-MTL15** | Return-from-leave check-in §6.5 — opt-in or default? | Default ON; master can opt-out in notification settings. Easier to opt-out than discover-and-opt-in. | UX | 🟢 |
| **Q-MTL16** | Admin rejects vacation — master can re-submit modified? | YES — modified request enters queue. UI shows «прошлый отказ: ...» context to both. | Policy + Eng | 🟢 |
| **Q-MTL17** | Master submits vacation for date already in past (clock skew) | API rejects with 400 + helpful message. UI date picker blocks. | Eng | 🟢 |
| **Q-MTL18** | Tenant SUSPENDED state — leave requests honored? | Per [`tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md): no new requests during SUSPENDED. Approved-but-future requests honored. Pending requests freeze + resume on un-suspend. | Policy | 🟢 |
| **Q-MTL19** | Pattern flag detection on PLANNED_LEAVE too (not just sick)? | NO — only SICK_DAY frequency flagged (health pattern signal). PLANNED_LEAVE is normal. | Policy | 🟢 |
| **Q-MTL20** | Master who is owner-master (small salon, owner does masters work) — same flow? | YES — owner role can self-approve their own leave. Auditrow captures «self-approved». Customer-facing same. | Policy + Eng | 🟢 |

---

## 16. Cross-document linkage

- [`master-conversational-templates §5.11`](../policies/master-conversational-templates.md) — RECURRING_PATTERN extends 5.11 with formal model+audit
- [`master-mobile-handoff §5`](./2026-05-18-master-mobile-handoff.md) — new «Запросить выходные» button on Schedule tab
- [`master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md) — §8 cycle interaction
- [`master-reviews-feedback-handoff.md`](./2026-05-19-master-reviews-feedback-handoff.md) — leave doesn't affect reviews
- [`booking-conflict-resolution-ux §3.6b`](../policies/booking-conflict-resolution-ux.md) — customer rebooking machinery reused
- [`customer-cancellation-reschedule-spec.md`](../policies/customer-cancellation-reschedule-spec.md) — alt-date / cancellation flows extended
- [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — Q-MTL11 HUMAN_LOCKED handling
- [`single-assistant-identity.md`](../policies/single-assistant-identity.md) — §2.2 customer voice preserved
- [`event-taxonomy.md §3.9`](../policies/event-taxonomy.md) — 14 NEW events §11
- [`schedule-editor-wireframes.md`](../policies/schedule-editor-wireframes.md) — recurring pattern changes flow integrates
- [`tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md) — Q-MTL18
- [`../decisions-log.md`](../decisions-log.md) — Q-MTL1..Q-MTL20

---

## 17. What this unblocks

- **Operational maturity** — masters don't message admin via WhatsApp for sick days
- **Customer trust** — automated rebooking happens fast vs «admin called in 4 hours»
- **Earnings transparency** — cycle preview accurately reflects vacation impact
- **Admin observability** — pattern detection for soft signal on master wellbeing
- **Founder analytics** — leave frequency by tenant feeds tenant-health signal Phase 3+
- **Multi-tenant master support** — leave at one salon doesn't affect other
- **Conflict-resolution-engine extension** — proves the engine handles real-world case beyond admin manual

## 18. What this does NOT unblock

- ❌ Long-term substitution (≥ 30 days) — doc #4
- ❌ PTO accounting (which days counted as paid)
- ❌ Tenant-wide closure (separate policy doc)
- ❌ Auto-replacement bot (admin still chooses)
- ❌ Mater-to-master shift swap (Phase 4+)
- ❌ Calendar app integration
- ❌ Skip Q-MTL11 HUMAN_LOCKED handling (pre-deploy lock)
- ❌ Skip Q-MTL2 pattern threshold validation (needs early-user data review)

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Schedule backend lead | ☐ | |
| Mini App frontend (master leave flows + admin queue + approval screen) | ☐ | |
| AI prompt eng (7 new touchpoints §6) | ☐ | |
| Conflict-resolution steward (rebooking integration §7) | ☐ | 🔴 PRE-DEPLOY |
| Earnings steward (cycle interaction §8 consistency) | ☐ | 🔴 PRE-DEPLOY |
| Conversation ownership steward (Q-MTL11) | ☐ | 🔴 PRE-DEPLOY |
| Privacy / Legal (Q-MTL2 pattern flag + Q-MTL13 reason analytics) | ☐ | |
| Founder (Q-MTL5 boundary between this doc and doc #4) | ☐ | |
| Accessibility (WCAG 2.2 AA on all surfaces) | ☐ | |

## Last verified
2026-05-19 (initial draft, 4 leave types + SLA matrix + cycle interaction + customer rebooking integration + admin pattern flag — locked)
