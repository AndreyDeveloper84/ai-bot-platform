# Master Substitution / Long-Term Handover — Engineering Handoff

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Production-blocking for masters who take 30+ day leave (maternity, surgery, extended vacation, sabbatical)
**Reads:** [`../policies/ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md), [`../policies/ayla-memory-and-personalization.md`](../policies/ayla-memory-and-personalization.md), [`../policies/tenant-as-provider-model.md`](../policies/tenant-as-provider-model.md), [`./2026-05-19-master-time-off-handoff.md`](./2026-05-19-master-time-off-handoff.md), [`./2026-05-19-master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md), [`./2026-05-19-master-reviews-feedback-handoff.md`](./2026-05-19-master-reviews-feedback-handoff.md), [`../handoffs/2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md), [`../policies/master-conversational-templates.md`](../policies/master-conversational-templates.md) (r2), [`../policies/booking-conflict-resolution-ux.md`](../policies/booking-conflict-resolution-ux.md), [`../policies/customer-profile-management-ux.md`](../policies/customer-profile-management-ux.md), [`../policies/ayla-emergency-fallback-policy.md`](../policies/ayla-emergency-fallback-policy.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md)

> Time-off-handoff covers leave ≤ 30 days. Long-term absence (30-180 days) is a different beast: customer relationships transfer, master's preference-data could leak between masters, returning master needs «catch-up» context. This handoff handles 30-180 day Substitution. Beyond 180 days = effective separation per offboarding-handoff (doc #5).

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](../policies/ayla-identity-and-brand.md) memory 2026-05-19: master substitution is **Ayla Pro** tenant-side per [`tenant-as-provider-model §5`](../policies/tenant-as-provider-model.md). Customer's Ayla memory (10-layer wellness profile) NEVER inherited by substitute master per [`ayla-memory-and-personalization §9.3`](../policies/ayla-memory-and-personalization.md) — only scoped service context. Customer-facing rebooking uses Ayla voice. Deprecated refs preserved as backend mechanic.

---

## 0. Why this exists

### 0.1 The gap

Per [`master-time-off-handoff §Q-MTL5`](./2026-05-19-master-time-off-handoff.md): ≥ 30 day leave needs separate flow because:
- Master's regular customers need ongoing care (not just rebooking one visit)
- Customer-preference data (allergies, AI memory, photo history) attached to master_id — needs scoped handover
- Substitute master needs CONTEXT but NOT permanent ownership of customer relationship
- Returning master needs to know what happened
- Earnings flow needs salon-vs-master clarity

### 0.2 The promise

Single source for:
- 4 substitution patterns §3 (named substitute / pool rotation / customer-choice / hold-for-return)
- Scoped context transfer §5 (substitute sees what's needed, NOT full master profile data)
- Customer notification cadence §6 (different from one-visit rebooking)
- Returning master catch-up §7
- Earnings split rules §8 (substitute earns, original master can preserve or earn from referral logic)
- 3 NEW models + 14 endpoints + 12 events

---

## 1. Scope

### IN
- Leave 30-180 days (extended vacation, surgery recovery, parental leave, sabbatical, military)
- 4 substitution patterns §3
- Customer-relationship-handover semantics §4
- Context transfer scoping §5 (what substitute can see)
- Customer notification + consent §6
- Returning master onboarding back §7
- Earnings split (substitute vs original master) §8
- Multi-tenant master substitution (each tenant independent)
- Substitute can be: another master in salon / contractor / external pickup-master
- Admin tools to manage substitution program §9
- 12 NEW events for event-taxonomy

### OUT
- ≤ 30 days (covered by time-off doc #3)
- > 180 days (covered by offboarding doc #5)
- Maternity / paternity legal accounting (Russian labor law specifics) — salon HR
- Cross-tenant substitution (other tenants of platform) — privacy boundary
- Customer can ban substitute («don't let X cover for my master») Phase 4+
- Refund flow for unhappy substitute experience — separate refund-dispute scope
- Master-to-master training during handover («show Anna's techniques») — Phase 4+
- Knowledge-base transfer (recipes, techniques) — out of scope; cultural matter
- Patient-record-style medical handover for medical-adjacent services — out of scope
- Substitute pay-for-context fee (some platforms do; we don't) — anti-pattern
- Customer chooses to «follow» master to new salon — that's separate; out of scope

---

## 2. Strategic constraints — non-negotiable

### 2.1 Customer relationship stays with original master
Even with substitute working, customer's «assigned master» is still the original. Substitute is COVERING, not REPLACING. AI tone reinforces this throughout.

### 2.2 Scoped context, NOT full handover
Substitute sees ONLY what's needed for the booking:
- Booking metadata (service, time)
- Customer's stated preferences for THIS service category
- Customer's stated allergies / safety constraints
- Customer's first name + initial
Substitute does NOT see:
- Customer's full booking history with original master
- Customer's wellness profile (per [`core-wellness-profile.md`](../policies/core-wellness-profile.md) — customer-only)
- Customer's AI memory (private to customer-only)
- Customer's tip history with original master
- Customer's reviews about original master

### 2.3 Customer always knows it's a substitute
Per [`single-assistant-identity §2.2`](../policies/single-assistant-identity.md): customer told «{{original_master}} в декрете, {{substitute}} ведёт её клиентов до возвращения». Honest framing.

### 2.4 Original master is informed of substitute's activity
Master on extended leave sees (if opted in):
- Count of customers seen by substitute (no detail)
- General feedback themes for substitute's work with master's customers
- NO substitute's earnings detail (privacy + comparison §master-earnings §2.3)

### 2.5 NO automatic relationship «migration»
Substitute working ≥ 6 months with master's customers doesn't auto-transfer ownership. Original master returns to «her» customers unless explicit re-assignment via separate admin flow.

### 2.6 Returning master is welcomed back, not penalized
- Customers DM'd «{{master}} вернулась! Записать вас к ней?»
- Earnings cycle starts fresh on return day
- NO «catch-up» quota or earnings advance

### 2.7 Substitute compensation respects original master's role
Default: substitute earns per their own compensation profile for their services. Original master earns 0 ₽ from substitute's work (no platform-mediated «referral fee»). Salon can set custom split per master pair in admin config §8.3.

### 2.8 Master cannot exclusive-lock customer
- ❌ Master setting «only I can serve {{customer}}» — anti-customer-choice
- Customer always free to choose substitute or wait or cancel

### 2.9 Customer's right to refuse substitute
Per Q-MS3: customer can decline substitute («подожду {{master}} 6 месяцев»). Future bookings held pending master's return. Customer informed about return date.

### 2.10 NO surprise reassignment
Customer must be ACTIVELY notified of substitution before substitute works on them. NEVER «showed up to salon, substitute introduced themselves». Pre-arrival notification mandatory.

### 2.11 Long-leave master is NOT shamed
- Anti-pattern «производительность мастеров за квартал»
- Substitution metrics admin-only
- Returning master has clean «welcome back» signal, not «behind on metrics»

### 2.12 Privacy hierarchy
- Customer's data: customer-only owner
- Master-attributed data (master's reviews, AI memory of customer-master pair): visible to that master only, NOT to substitute
- Booking data: visible to whoever is performing booking (incl. substitute)
- Substitute sees: minimum needed scope §2.2

---

## 3. Four substitution patterns

### 3.1 NAMED_SUBSTITUTE
Single specified substitute master. Original master picks at request time.
- Customer experience: «Лена ведёт клиентов Анны до её возвращения»
- Earnings: substitute earns own rate
- Most common pattern for trusted master pairs

### 3.2 POOL_ROTATION
Any available master in salon can cover. Admin manages.
- Customer experience: «Различные мастера ведут клиентов Анны до возвращения»
- Earnings: whoever does the work earns
- For salon-with-many-masters

### 3.3 CUSTOMER_CHOICE
Each customer chooses substitute themselves from list.
- Customer experience: «Анна в декрете — выберите кто будет с вами»
- Earnings: chosen master earns
- High-touch but respects customer autonomy

### 3.4 HOLD_FOR_RETURN
No substitute. Customers can hold for original master's return OR cancel.
- Customer experience: «Анна вернётся 1 декабря — записаться сейчас на возвращение?»
- No active substitutes
- Earnings: 0 ₽ for original master during leave
- For very-strong-loyalty masters (or master's preference)

### 3.5 Pattern selection at substitution-setup
Admin (with master input) chooses pattern at submission time. Can switch mid-leave per master request §4.5.

---

## 4. Substitution lifecycle

### 4.1 Submission (during long-leave request)

When master submits leave request §master-time-off and `days_count >= 30`:

```
┌────────────────────────────────────────┐
│ ← Длительный отпуск (45 дней)            │
├────────────────────────────────────────┤
│ Это серьёзный срок. Что хотите сделать  │
│ со своими постоянными клиентами?        │
│                                        │
│ ⦿ Передать одному мастеру               │
│   [Кому? — выбрать ▾]                   │
│                                        │
│ ◯ Пусть студия распределяет             │
│   (любой свободный мастер)              │
│                                        │
│ ◯ Каждый клиент выберет сам              │
│   (помощник предложит список)           │
│                                        │
│ ◯ Никого — пусть подождут моего         │
│   возвращения (можно отменить, можно    │
│   ждать)                                 │
│                                        │
│ ── Что увидит замена ──                  │
│ • Имя клиента (без фамилии)              │
│ • Услуги, которые делали                 │
│ • Аллергии и противопоказания            │
│ • НЕ увидит: ваши заметки, переписку,   │
│   фото клиента                          │
│                                        │
│ Что хотите передать дополнительно        │
│ (опционально)?                            │
│ [_____________________________]        │
│ (если кратко — что важно знать)          │
│                                        │
│ [Дальше]                                 │
└────────────────────────────────────────┘
```

### 4.2 Admin approval

Same flow as VACATION approval but with additional substitution-pattern UI:

```
┌────────────────────────────────────────┐
│ ← Длительный отпуск: Анна, 45 дней       │
├────────────────────────────────────────┤
│ Период: 1 июня — 15 июля                │
│                                        │
│ ── Что предложила Анна ──                │
│ Передать Лене (одному мастеру)           │
│                                        │
│ Лена согласна на это? Подтвердите —      │
│ [✓ Согласовала с Леной]                  │
│ [Спросить у Лены сначала]                │
│                                        │
│ Затронет 47 будущих записей у Анны       │
│ за следующие 6 месяцев (включая после   │
│ возвращения). Из них в период отпуска  │
│ — 32.                                   │
│                                        │
│ ── Дополнительная заметка от Анны ──     │
│ «Лена знает мои предпочтения, поможет.   │
│ С Машей И. — у неё аллергия на лак Х,    │
│ обязательно учти.»                       │
│                                        │
│ [✓ Согласовать]                          │
│ [💬 Обсудить со всеми]                   │
│ [✗ Не согласовать]                       │
└────────────────────────────────────────┘
```

### 4.3 Substitute consent (NAMED_SUBSTITUTE only)

If admin approves NAMED_SUBSTITUTE with Лена specified, substitute receives Bot DM:

```
{{substitute_first_name}}, {{original_master}} уходит в декрет с 1 июня
на 45 дней. {{salon_owner}} предлагает вам вести её постоянных клиентов
в это время.

Что нужно знать:
• Около 32 записей за период отпуска
• Вы будете видеть имя клиента + услугу + аллергии/противопоказания
• НЕ будете видеть переписку или личные заметки {{original_master}}
• Заработок по вашей обычной ставке

Согласны?
[✓ Да, согласна]   [✗ Нет, не сейчас]   [Обсудить]
```

### 4.4 Active substitution operations

Once approved + (if needed) substitute consents:
- All future bookings with `master_id=original_master` in leave window assigned to substitute path
- Customer notification §6 fires per booking + a separate «period-start» digest
- Scoped context API §5 made available

### 4.5 Mid-leave pattern change
Master can request switch via internal-admin-chat (doc #6). E.g., «Лена не справляется, переходите на customer-choice». Admin reviews. Audit captured.

### 4.6 Returning master flow §7

---

## 5. Scoped context transfer

### 5.1 Substitute booking pre-arrival surface

When substitute opens booking details (master Mini App «Сегодня» screen):

```
┌────────────────────────────────────────┐
│ ← 10:00, маникюр                         │
├────────────────────────────────────────┤
│ Клиент: Мария И. (постоянный клиент      │
│ {{original_master}}, замещение)          │
│                                        │
│ ── Услуга ──                             │
│ Маникюр, классический                    │
│                                        │
│ ── Предпочтения по этой услуге ──        │
│ • Длина: средняя                          │
│ • Форма: миндаль                         │
│ • Цвет: пастель, без блёсток             │
│                                        │
│ ── Безопасность ──                       │
│ ⚠ Аллергия на лак Х                     │
│                                        │
│ ── Заметка от {{original_master}} ──     │
│ «С Машей — у неё аллергия на лак Х,      │
│ обязательно учти.»                       │
│                                        │
│ ⓘ Подробные заметки, переписка и фото    │
│ {{original_master}} с этим клиентом      │
│ скрыты по умолчанию.                      │
└────────────────────────────────────────┘
```

### 5.2 Scope rules — what substitute CAN see

- Customer first name + initial
- Service category and specific service for THIS booking
- Customer's documented preferences FOR THIS SERVICE CATEGORY (from `CustomerServicePreference` if exists)
- Customer's allergies / contraindications (always — safety)
- Master's one-time handover note (max 500 chars at substitution setup)
- Booking date/time/duration/price

### 5.3 Scope rules — what substitute CANNOT see

- Customer's full name (privacy)
- Customer's phone / email
- Customer's photo history (AI Avatar wellness)
- Customer's wellness profile (mood / body / sleep / etc.)
- Original master's notes BEYOND handover note
- Original master's Mini App chat history with customer
- Customer's tip history with original master
- Customer's reviews of original master
- Other customers of original master (just THIS booking's customer)
- Customer's bookings OUTSIDE this leave window
- Customer's preferences for OTHER service categories

### 5.4 Per-booking access logging

Every substitute Mini App view of customer info → audit row (per §10.3). Original master can review «who saw what» when they return §7.4.

### 5.5 Customer can opt-out of substitute receiving context

Per Q-MS5 customer can mark «не передавать заметки» for specific bookings — substitute sees only minimum (name initial, service, allergies — allergies always for safety).

### 5.6 LeaveContextNote model — master's hand-off note

One-time 500-char note set at substitution submission. Substitute sees per booking with that customer. Editable until leave starts; locked after.

---

## 6. Customer notification flow

### 6.1 Period-start digest (substitution kicks in)

If customer has 1+ future booking with original master, customer receives Bot DM:

```
{{customer_first_name}}, привет.

{{original_master}} уходит в декрет с 1 июня по 15 июля. На это время
{{substitute_master}} ведёт её клиентов.

У вас записаны:
• 5 июня, маникюр 10:00
• 19 июня, маникюр 10:00

Можно:
[Записаться к {{substitute_master}}]
[Перенести на после 15 июля]
[Отменить эти записи]

Можно подумать — напишите когда определитесь.
```

### 6.2 Per-booking 24h before reminder

Standard reminder applies + substitute mention:

```
Завтра в 10:00 маникюр. Сегодня с вами будет {{substitute_master}} —
{{original_master}} в декрете до 15 июля.

До встречи!
```

### 6.3 Customer chose «hold for return»

If customer picked HOLD_FOR_RETURN pattern or declined substitute:

```
Хорошо, ждём {{original_master}}. Я напишу как только она вернётся
({{return_date}}) — запишу вас на удобное время.

Если что-то срочное — могу предложить другого мастера в любой момент.
```

### 6.4 Pattern change customer notification

If admin switches substitution pattern mid-leave (e.g., NAMED → POOL_ROTATION because substitute also went on leave):

```
{{customer_first_name}}, {{original_substitute}} тоже не сможет
обслужить вас на этой неделе. {{new_substitute}} возьмёт ваше время
(маникюр 5 июня в 10:00), если ок. Или перенесём.

[Согласна]   [Перенести]   [Отменить]
```

### 6.5 Return notification

When original master returns (T-1 day):

```
{{customer_first_name}}, {{original_master}} возвращается завтра!

Ваши следующие записи — она будет вести их сама.

Хотите записаться к ней раньше? Свободные слоты у {{original_master}}:
{{slot_1}}, {{slot_2}}, {{slot_3}}.

[Записаться]   [Подождать обычной даты]   [Спасибо, потом]
```

### 6.6 Cascade ordering same as time-off

Per [`master-time-off §7.2`](./2026-05-19-master-time-off-handoff.md): staggered messages, max 5/min.

---

## 7. Returning master onboarding

### 7.1 Day-of-return Bot DM (morning)

```
С возвращением, {{original_master}}!

За время отсутствия:
• {{substitute_master}} вёл(а) {{customers_count}} ваших клиентов
• Записей выполнено: {{bookings_count}}
• Возвратилось «к вам после возвращения»: {{returned_count}} клиентов

Сегодня у вас {{today_bookings_count}} записей. Первая — {{customer}}
в {{time}}.

Заглянуть в Мини-приложение — увидите больше.
[Открыть]
```

### 7.2 «Что произошло за моё отсутствие» Mini App screen

```
┌────────────────────────────────────────┐
│ ← С возвращением!                       │
├────────────────────────────────────────┤
│ Отпуск: 1 июня — 15 июля (45 дней)      │
│                                        │
│ ── Замещение ──                          │
│ {{substitute_master}} вёл(а) клиентов    │
│                                        │
│ ── Ваши клиенты ──                       │
│ 32 ваших клиента посетили студию.       │
│ Из них:                                  │
│ • К замещающему мастеру: 24              │
│ • Подождали вашего возвращения: 5        │
│ • Перенесли визит позже: 3               │
│                                        │
│ ── Темы обратной связи ──                │
│ Что отметили клиенты с замещением:       │
│ • Хорошо: 16 отзывов (4.7 средняя)      │
│ • Нейтрально: 4                          │
│ • Прохладно: 0                           │
│ • Замечание: 0                           │
│                                        │
│ ⓘ Все отзывы оставались на замещающем    │
│ мастере, не на вас. Не отражаются в      │
│ ваших цифрах.                            │
│                                        │
│ ── Что сегодня ──                        │
│ 4 записи. Первая — Мария И. в 10:00.    │
│ [Посмотреть день]                        │
│                                        │
│ ── Что нужно сделать ──                  │
│ [ ] Просмотреть «новых» в портфолио       │
│      (3 клиента вернулись после          │
│      замещения)                          │
│ [ ] Проверить расписание на следующие    │
│      2 недели                            │
│ [ ] Восстановить рабочий ритм             │
└────────────────────────────────────────┘
```

NO judgmental phrasing. Calm, summary, action items optional.

### 7.3 Earnings cycle reset
Per §8 — cycle starts fresh on return day. No catch-up advance.

### 7.4 Access audit of substitute activity

Original master can review (in their Profile section):

```
┌────────────────────────────────────────┐
│ ← Кто видел моих клиентов               │
├────────────────────────────────────────┤
│ За период замещения 1 июня - 15 июля:    │
│                                        │
│ {{substitute_master}}:                   │
│ • Открывал(а) записи: 32 раза            │
│ • Открывал(а) заметки: 28 раз            │
│ • Видел(а) аллергии клиентов: 24 раза    │
│                                        │
│ Все обращения — в рамках замещения,      │
│ согласно правилам §5.                    │
│                                        │
│ Если заметили что-то странное —          │
│ [Связаться с {{salon_owner}}]            │
└────────────────────────────────────────┘
```

### 7.5 Returning master's PR moment (optional)

If master opts in to «I'm back» message to customers:

```
{{original_master}} попросила передать: «Спасибо, что подождали меня
и моих клиентов, которые остались. Я вернулась — буду рада увидеть
вас снова».

Свободные слоты на этой неделе: {{slots}}.

[Записаться]
```

Master writes the body in Mini App; AI relays.

---

## 8. Earnings split

### 8.1 Default rule
Per §2.7: substitute earns at substitute's own compensation profile rate. Original master earns 0 ₽ from substitute's work during leave window.

### 8.2 Cycle continuation
Per [`master-time-off §8`](./2026-05-19-master-time-off-handoff.md): original master's cycle continues during leave; just no new earnings accrue.

### 8.3 Custom split (optional, salon config)

Admin can configure per substitute-master pair:
- Original master gets X% of substitute's master_share for these customers (referral logic)
- Default: 0% (substitute earns fully)

If split active:
- UI shows transparently in admin Mini App + master Mini App
- Audit per booking captures split
- Master Mini App shows separate «Доход от замещения» line item

### 8.4 Disputes during leave
If substitute's booking disputed (per [`master-earnings-handoff §9`](./2026-05-19-master-earnings-handoff.md)):
- Substitute is the disputant + responder
- Original master not involved
- If allegation involves original master's customer (e.g., «substitute didn't respect my preferences»), customer-facing AI handles per booking-conflict-resolution-ux flow

### 8.5 Tip flow during leave
Tips go to whoever did the work — substitute. Per [`master-earnings-handoff §7`](./2026-05-19-master-earnings-handoff.md) tip rules apply unchanged.

### 8.6 Original master's reviews are paused
Per [`master-reviews-feedback-handoff §1`](./2026-05-19-master-reviews-feedback-handoff.md): customer reviews substitute's work attach to SUBSTITUTE. Original master's aggregate doesn't update during leave (no work to review).

---

## 9. Data models

### 9.1 `MasterSubstitution`

Created from `MasterLeaveRequest` when days_count >= 30 (or admin manually escalates a shorter leave).

```python
class MasterSubstitution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    original_master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='substitutions_taken')
    leave_request = models.ForeignKey('schedule.MasterLeaveRequest', null=True, on_delete=SET_NULL, related_name='+')

    PATTERN_CHOICES = [
        ('named_substitute', 'Single named substitute'),
        ('pool_rotation', 'Any available master'),
        ('customer_choice', 'Each customer picks'),
        ('hold_for_return', 'No substitute; wait for return'),
    ]
    pattern = models.CharField(max_length=32, choices=PATTERN_CHOICES)

    named_substitute_master = models.ForeignKey('staff.Master', null=True, blank=True, on_delete=SET_NULL, related_name='substitutions_covering')
    # Only for NAMED_SUBSTITUTE

    handover_note = models.TextField(blank=True, default='', max_length=500)

    starts_on = models.DateField()
    ends_on = models.DateField()
    extended_until = models.DateField(null=True, blank=True)
    # If admin extends past planned end

    STATUS_CHOICES = [
        ('proposed', 'Proposed by master in leave request'),
        ('admin_reviewing', 'Admin reviewing'),
        ('substitute_consent_pending', 'NAMED — awaiting substitute consent'),
        ('active', 'Active'),
        ('pattern_changed', 'Pattern changed mid-leave (audit before/after)'),
        ('completed', 'Master returned'),
        ('cancelled', 'Cancelled (master returned early OR salon-side decision)'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='proposed')

    substitute_consent_at = models.DateTimeField(null=True, blank=True)
    admin_approval_at = models.DateTimeField(null=True, blank=True)
    admin_approved_by = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    active_from = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Optional earnings split for referral logic §8.3
    referral_split_to_original_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    customers_affected_count = models.IntegerField(default=0)
    customers_served_by_substitute_count = models.IntegerField(default=0)
    customers_held_for_return_count = models.IntegerField(default=0)
    customers_cancelled_count = models.IntegerField(default=0)

    class Meta:
        indexes = [
            Index(fields=['tenant', 'status', '-starts_on']),
            Index(fields=['original_master', '-starts_on']),
            Index(fields=['named_substitute_master', 'status']),
        ]
```

### 9.2 `SubstitutionBookingAssignment`

Per-booking assignment record.

```python
class SubstitutionBookingAssignment(models.Model):
    substitution = models.ForeignKey(MasterSubstitution, on_delete=CASCADE, related_name='booking_assignments')
    booking = models.OneToOneField('booking.Booking', on_delete=CASCADE, related_name='substitution_assignment')

    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='+')
    serving_master = models.ForeignKey('staff.Master', null=True, on_delete=SET_NULL, related_name='+')
    # null for HOLD_FOR_RETURN pending customer decision

    STATUS_CHOICES = [
        ('customer_pending', 'Awaiting customer decision'),
        ('substitute_accepted', 'Customer accepted substitute'),
        ('rescheduled_to_after', 'Customer rescheduled to after-leave'),
        ('cancelled_by_customer', 'Customer cancelled'),
        ('held_for_return', 'Customer awaits return; booking deferred to post-return slot'),
        ('reverted_to_original', 'Master returned early; reassigned'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='customer_pending')

    customer_notified_at = models.DateTimeField(null=True, blank=True)
    customer_decided_at = models.DateTimeField(null=True, blank=True)

    customer_opted_out_of_handover_note = models.BooleanField(default=False)
    # If true, substitute sees only minimum scope §5.5

    class Meta:
        indexes = [
            Index(fields=['substitution', 'status']),
            Index(fields=['customer', '-customer_notified_at']),
        ]
```

### 9.3 `SubstitutionContextAccessLog`

Audit row per substitute access of customer info §5.4.

```python
class SubstitutionContextAccessLog(models.Model):
    substitution = models.ForeignKey(MasterSubstitution, on_delete=CASCADE, related_name='access_logs')
    booking = models.ForeignKey('booking.Booking', on_delete=CASCADE, related_name='+')
    accessed_by = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='+')

    SCOPE_CHOICES = [
        ('booking_view', 'Viewed booking detail'),
        ('handover_note_view', 'Viewed handover note'),
        ('allergies_view', 'Viewed allergies/contraindications'),
        ('preferences_view', 'Viewed service preferences'),
    ]
    scope = models.CharField(max_length=32, choices=SCOPE_CHOICES)
    accessed_at = models.DateTimeField(auto_now_add=True)
```

---

## 10. API contracts

### 10.1 Master (original) endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/master/substitution` | Propose substitution (during long-leave request) |
| PATCH | `/api/v1/master/substitution/<id>` | Edit before admin approval |
| GET | `/api/v1/master/substitution/<id>` | View own substitution |
| GET | `/api/v1/master/substitution/<id>/return-summary` | §7.2 return screen data |
| GET | `/api/v1/master/substitution/<id>/access-log` | §7.4 audit of substitute activity |
| POST | `/api/v1/master/substitution/<id>/request-pattern-change` | Mid-leave §4.5 |
| POST | `/api/v1/master/substitution/<id>/return-message` | §7.5 broadcast «I'm back» |

### 10.2 Substitute master endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/master/substitute-invite/<id>/respond` | Accept/decline (§4.3) |
| GET | `/api/v1/master/substitute-context/booking/<booking_id>` | Scoped context per booking (§5.1) |

### 10.3 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/substitution/queue` | Pending approvals |
| POST | `/api/v1/admin/substitution/<id>/approve` | Approve §4.2 |
| POST | `/api/v1/admin/substitution/<id>/change-pattern` | Mid-leave pattern change |
| GET | `/api/v1/admin/substitution/active` | Ongoing substitutions in tenant |
| POST | `/api/v1/admin/substitution/<id>/extend-end-date` | Extend if master needs more time |
| POST | `/api/v1/admin/substitution/<id>/end-early` | Master returned early |

### 10.4 Customer endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/customer/substitution-affected-bookings` | List bookings affected |
| POST | `/api/v1/customer/booking/<id>/accept-substitute` | Accept |
| POST | `/api/v1/customer/booking/<id>/reschedule-to-after-leave` | Reschedule |
| POST | `/api/v1/customer/booking/<id>/cancel-due-to-leave` | Cancel |
| POST | `/api/v1/customer/booking/<id>/hold-for-return` | Hold per Q-MS3 |
| POST | `/api/v1/customer/booking/<id>/opt-out-handover-note` | §5.5 |

### 10.5 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/substitution/<id>/activate` | Cron-triggers on starts_on date |
| POST | `/internal/substitution/<id>/return-prep` | T-1 cron |

---

## 11. Events emitted

Add to [`event-taxonomy.md`](../policies/event-taxonomy.md) `3.9 master leave domain` (shares with time-off; or new `3.10 substitution` sub-section):

| Trigger | Event | Notes |
|---|---|---|
| Substitution proposed | NEW: `substitution.proposed` | pattern, days_count |
| Admin approved | NEW: `substitution.approved` | pattern_at_approval |
| Substitute consented | NEW: `substitution.substitute_consented` | |
| Substitute declined | NEW: `substitution.substitute_declined` | reason_provided |
| Activated (start date) | NEW: `substitution.activated` | |
| Customer accepted substitute | NEW: `substitution.customer_accepted` | booking_id |
| Customer chose hold-for-return | NEW: `substitution.customer_held_for_return` | |
| Customer rescheduled to after-leave | NEW: `substitution.customer_rescheduled` | |
| Customer cancelled due to leave | NEW: `substitution.customer_cancelled` | |
| Pattern changed mid-leave | NEW: `substitution.pattern_changed` | from, to |
| Original master returned | NEW: `substitution.completed` | |
| Substitution extended | NEW: `substitution.extended` | new_ends_on |

12 NEW events §11.

---

## 12. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Substitute sees customer's full AI chat history | Privacy §5.3 | Scoped view only |
| Customer learns of substitution at salon | Surprise §2.10 | Pre-arrival notification |
| Auto-cancel all bookings on long leave | Bad CX | Customer chooses |
| Earnings auto-transfer to substitute | Trust violation | Default 0% to original; explicit config §8.3 |
| Original master's reviews update during leave | Misleading | Reviews on substitute §8.6 |
| Substitute earns at original master's rate | Wrong-master compensation | Substitute's own profile |
| Master cannot decline being a substitute | Coercion §4.3 | Consent flow |
| Customer cannot decline substitute | Autonomy §2.9 | Customer choice always |
| Substitute can lock customer to themselves | Customer freedom | Substitution is temporary, not transfer |
| Return-day shaming («behind on metrics») | Anti-shame §2.11 | Calm welcome |
| Force substitute to take all customers | Pool overload | Admin scoping |
| Cross-tenant substitution (master from other salon) | Privacy boundary | NEVER MVP |
| Substitute sees other customers of original master not in current booking | Scope creep | Per-booking-only |
| Customer's wellness data exposed to substitute | Hard privacy boundary | NEVER |
| Original master's customer relationship «migrated» after long substitute | Trust violation §2.5 | Stay with original unless explicit reassignment |
| Substitute reads handover note as carte-blanche | Boundary creep | 500-char limit, scope §5.6 |
| Returning master shamed for «letting clients go» | §2.11 | Anti-shame |
| Customer asked «do you want to keep substitute permanently» — pressure | §2.5 | NEVER auto-offer |

---

## 13. Acceptance criteria (engineering checklist)

- [ ] 3 models §9 + migration
- [ ] 18 endpoints across 4 roles §10
- [ ] Cross-master scope enforcement (substitute sees scoped only) §5
- [ ] Customer consent flow §4.3 (NAMED only)
- [ ] Admin approval queue with affected-bookings preview
- [ ] Bot DM templates §6 (5 customer messages + 4 master messages)
- [ ] Scoped context API §5.1 (substitute booking-detail view)
- [ ] Access logging audit §5.4 + master view §7.4
- [ ] Customer choice flow §6.1 (5 options)
- [ ] Cascade throttle reuses time-off §7.2
- [ ] Return-day master summary §7.2 + Bot DM §7.1
- [ ] Returning master access-log view §7.4
- [ ] Optional return-broadcast §7.5
- [ ] Earnings: cycle accumulates normally; no auto-split unless configured §8
- [ ] Earnings split config UI in admin §8.3
- [ ] Tip flow unchanged for substitute §8.5
- [ ] Reviews attach to substitute §8.6 (verified with reviews-handoff)
- [ ] Mid-leave pattern change flow §4.5
- [ ] Master can extend / end-early per §10.3
- [ ] Customer opt-out of handover-note §5.5
- [ ] 12 events §11
- [ ] PII rules §5.2-5.3 enforced
- [ ] Tests: 4 patterns end-to-end; scope leakage check (substitute can't access denied fields); customer 5-options flow; access log audit; pattern change mid-leave; return-day summary; earnings split config
- [ ] Anti-pattern review §12

---

## 14. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-MS1** | Min days_count for substitution flow — 30 or different? | 30 MVP. Below → time-off doc handles. | Policy | 🟢 |
| **Q-MS2** | Max days_count — 180 or stricter? | 180 MVP. Beyond → offboarding doc handles. | Policy | 🟢 |
| **Q-MS3** | Customer hold-for-return — booking «virtual» kept? | Booking cancelled but customer flagged «awaiting return». When return date approaches, AI proactively offers re-book. | Eng + Policy | 🟢 |
| **Q-MS4** | Substitute can decline AFTER active? | Yes via internal-admin-chat. Triggers pattern change §4.5. | Policy | 🟢 |
| **Q-MS5** | Customer opt-out of handover note per booking or globally? | Per booking MVP (§5.5). Phase 3+ global preference. | UX | 🟢 |
| **Q-MS6** | Multi-tenant master substitution affects all tenants or per-tenant? | Per-tenant. Q-MS-cross-tenant: substitution at tenant A doesn't affect tenant B. Master Mini App tenant selector applies. | Policy + Eng | 🟡 |
| **Q-MS7** | Handover note auto-translates if substitute speaks different language? | Phase 4+ when international. MVP: same-language assumed. | Eng | 🟢 |
| **Q-MS8** | When admin doesn't approve substitution in time — auto-fallback? | If admin doesn't approve within 7d AND leave start <= 14d away → auto-falls to POOL_ROTATION (default). Audit captures auto-fallback. | Policy | 🟡 |
| **Q-MS9** | Substitute earnings cap on customer relationships (Phase 4+ business model question) | NO cap MVP. Substitute earns own rate fully. | PM | 🟢 |
| **Q-MS10** | If returning master discovers substitute «stole» their customer permanently — recourse? | Per §2.5: relationship doesn't auto-migrate. If substitute / customer agreed to switch, that's customer choice. Master can flag suspicious patterns to founder via Q12-δ founder-50 cohort review path. | Policy | 🔴 PRE-DEPLOY |
| **Q-MS11** | Substitute can write «notes» about original master's customer that original master sees on return? | NO MVP. Substitute writes own notes which substitute keeps if customer ever books substitute again. Original master sees ONLY booking record + access log + thank-you messages exchanged. | Privacy + Policy | 🟡 |
| **Q-MS12** | Tier change for customers during substitution per `conversation-ownership-policy.md` | Customer's tier with «assistant» persists; substitute master sees through that lens. Per §2.1 customer relationship stays with original. | Policy | 🟡 |
| **Q-MS13** | Wellness modules (mood, body, sleep) — substitute access? | NO — wellness data is customer-only per [`core-wellness-profile.md`](../policies/core-wellness-profile.md). Substitute cannot read at all. | Privacy | 🟢 |
| **Q-MS14** | AI behavior during substitution — voice change? | NO. AI is the same single-assistant identity. Voice neutral about substitution. NEVER says «I prefer original master» or «substitute is great». | UX | 🟢 |
| **Q-MS15** | Substitute on long leave themselves — chain breakdown? | Pattern automatically changes to POOL_ROTATION + admin notified emergency. Audit captures. | Eng | 🟡 |
| **Q-MS16** | Substitute's own reviews aggregation — separate from original master's? | YES — fully separate. Substitute's `MasterReviewAggregate` updates from substitute's work. Original master's pauses during leave. | Eng | 🟢 |
| **Q-MS17** | Anchor customer's «preferred master» when original returns — auto vs ask? | Auto-anchor BACK to original master. Customer can override if they prefer substitute permanently (separate explicit «switch master» flow Phase 3+). | UX | 🟡 |
| **Q-MS18** | Substitute's bookings count for substitute's own earnings cycle | YES — all standard rules from `master-earnings-handoff` apply to substitute. | Eng | 🟢 |
| **Q-MS19** | Master returns EARLY (before ends_on date) — auto-end substitution? | Master can request end-early via API §10.1. Admin approves. Customers re-notified per §6.4. | Policy + UX | 🟡 |
| **Q-MS20** | Substitution > 90 days — additional sign-offs? | Founder + privacy approval REQUIRED for substitutions > 90 days. Captures «extended leave» edge cases. | Founder + Privacy | 🔴 PRE-DEPLOY |

---

## 15. Cross-document linkage

- [`master-time-off-handoff.md`](./2026-05-19-master-time-off-handoff.md) — boundary at 30 days, customer rebooking patterns reused
- [`master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md) — §8 cycle interaction extended
- [`master-reviews-feedback-handoff.md`](./2026-05-19-master-reviews-feedback-handoff.md) — §8.6 reviews routing
- [`master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md) — return-day Mini App surface added
- [`master-conversational-templates.md`](../policies/master-conversational-templates.md) — 5 customer + 4 master new touchpoints
- [`booking-conflict-resolution-ux.md`](../policies/booking-conflict-resolution-ux.md) — customer rebooking machinery reused
- [`customer-profile-management-ux.md`](../policies/customer-profile-management-ux.md) — `CustomerServicePreference` reused for scoped view
- [`core-wellness-profile.md`](../policies/core-wellness-profile.md) — wellness data NEVER exposed to substitute §2.12
- [`single-assistant-identity.md`](../policies/single-assistant-identity.md) — §2.3 voice consistency
- [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — Q-MS12 tier through substitution
- [`event-taxonomy.md §3.9-3.10`](../policies/event-taxonomy.md) — 12 NEW events §11
- [`../decisions-log.md`](../decisions-log.md) — Q-MS1..Q-MS20

---

## 16. What this unblocks

- **30-180 day leave handling** — masters can take maternity, surgery, sabbatical without losing customers
- **Salon resilience** — long absences don't permanently lose customer base
- **Substitute master fairness** — substitute earns own rate, gets clear consent flow
- **Customer trust** — always informed, always has choice
- **Returning master smooth onboarding** — calm summary, no shame
- **Privacy enforcement at scale** — scoped context proven at higher complexity
- **Audit completeness** — access logging proves privacy enforcement

## 17. What this does NOT unblock

- ❌ > 180 days (doc #5 offboarding handles)
- ❌ Master-to-master training during handover
- ❌ Auto-migration of customer relationships
- ❌ Cross-tenant substitution
- ❌ Substitute training material library
- ❌ Skip Q-MS10 customer-poaching detection (pre-deploy)
- ❌ Skip Q-MS20 90+ day extra sign-offs (pre-deploy)
- ❌ Phase 4+ master-to-master shift swap and similar

---

## 18. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Schedule backend lead (substitution model integration) | ☐ | |
| Mini App frontend (substitute scoped view + return-day screen + customer 5-options flow) | ☐ | |
| AI prompt eng (9 new touchpoints) | ☐ | |
| Time-off steward (consistency with §master-time-off boundary) | ☐ | 🔴 PRE-DEPLOY |
| Earnings steward (§8 unchanged or §8.3 split implementation) | ☐ | 🔴 PRE-DEPLOY |
| Reviews steward (§8.6 routing) | ☐ | |
| Privacy / Legal (§5 scope enforcement + Q-MS10 poaching + Q-MS11 substitute notes) | ☐ | 🔴 PRE-DEPLOY |
| Conversation ownership steward (Q-MS12) | ☐ | |
| Founder (Q-MS20 > 90 days + Q-MS10 poaching policy) | ☐ | 🔴 PRE-DEPLOY |
| Accessibility (WCAG 2.2 AA) | ☐ | |

## Last verified
2026-05-19 (initial draft, 4 patterns + scoped context + access audit + earnings split optional + return-day flow — locked)
