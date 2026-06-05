# Customer Notification Controls — Extended UX Policy

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Production-blocking — granular controls extending foundational notification-preferences-ux; GDPR-aligned customer consent
**Reads:** [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md), [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md), [`notification-preferences-ux.md`](./notification-preferences-ux.md), [`customer-loyalty-rewards-ux.md`](./customer-loyalty-rewards-ux.md), [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md), [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md), [`customer-profile-management-ux.md`](./customer-profile-management-ux.md), [`customer-wellness-dashboard-ux.md`](./customer-wellness-dashboard-ux.md), [`wellness-input-modules.md`](./wellness-input-modules.md), [`assistant-persona.md`](./assistant-persona.md) (r2), [`event-taxonomy.md`](./event-taxonomy.md), [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md)

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](./ayla-identity-and-brand.md) memory 2026-05-19: notification sender = «Ayla» (not «помощник {{salon_name}}»). Emergency notifications bypass snooze + quiet hours per [`ayla-emergency-fallback-policy §11`](./ayla-emergency-fallback-policy.md). `single-assistant-identity.md` reference removed (deprecated 2026-05-19).

> Existing notification-preferences-ux locked Q-CX9 «single toggle» for customer (anti-decision-fatigue). Production reality has expanded — customer now has loyalty notifications, refund disputes, AI proactive insights, wellness goals, referrals. Single toggle is too blunt. This policy adds optional ADVANCED tier (collapsed by default — simple UX preserved) + quiet hours + snooze + GDPR audit + multi-tenant prefs.

---

## 0. Why this exists

### 0.1 Q-CX9 stress test

[`notification-preferences-ux §3.1`](./notification-preferences-ux.md#31-what-customer-controls) decided single «без проактивных» master toggle for customer. Reasoning: decision fatigue + low value across 14 simple switches.

But the «14 switches» count was 2026 spring. Since then:
- Loyalty earn / redeem / tier-up / birthday / referral / refund-revoke (6 categories)
- Refund dispute status updates (1)
- AI proactive insights (per wellness-dashboard cross-module observations) (1)
- Wellness goal references (per wellness-goal-setting Layer 2) (1)
- Service recommendations (Phase 3+) (1)
- Long-gap return / re-engagement (1)
- Master leave / substitution / offboarding affecting customer (3)

= 14+ NEW categories on top of the original 14. The «single toggle» now hides too much.

Customer reality: «I want booking reminders + tier-up + birthday, BUT NOT marketing AND NOT proactive recommendations». Currently impossible without disabling everything proactive.

### 0.2 GDPR-adjacency

EU + Russia consumer-protection trends require:
- Granular consent per processing purpose
- Audit trail of consent changes
- Right to withdraw any specific consent
- Right to view what consent is currently active
- Right to export consent history

Single toggle doesn't satisfy this. Per Q-CR8 sensitive-keyword precedent — legal review needed.

### 0.3 The promise

Single source for:
- 2-tier customer settings UI §3 (Simple + Advanced) — simple is default
- 8 notification categories §4 with per-category controls
- Quiet hours customer-side §5 (currently only master has this)
- Snooze flow §6 (per-category or all)
- Multi-tenant per-tenant preferences §7
- GDPR-aligned consent audit §8
- Inline «hush this kind» on every notification §9
- AI Bot DM self-check «am I writing too much?» §10
- Emergency-always-on §11
- 4 NEW endpoints, 8 NEW events
- Q-CX9 update — Simple tier preserves single-toggle UX; Advanced is opt-in for power users

---

## 1. Scope

### IN
- Extension of `notification-preferences-ux` §3 customer section
- 2-tier UI: Simple (Q-CX9 single toggle, default) + Advanced (collapsed under «Подробнее»)
- 8 categories §4
- Quiet hours per customer (24h clock per customer timezone)
- Snooze: per-category for N days OR all-snooze for N days
- Multi-tenant: per-tenant preferences (multi-tenant customer)
- GDPR audit log with consent history
- AI Bot DM «is this too much?» periodic self-check
- Inline notification «hush» button (long-press / tap-and-hold)
- Emergency-always-on list (medical-adjacent, refund disputes, sensitive)
- 8 NEW customer-facing events
- Cross-doc reconciliation with loyalty / refund / wellness / cancellation docs

### OUT
- Master + owner notification prefs (covered by existing doc §4 + §5)
- SMS / Email / Push beyond Bot DM (channel scope unchanged from existing doc)
- Anti-fraud / spam-bot detection on notification opt-outs — Phase 4+
- Notification translation per customer language (Russian MVP per existing doc)
- A/B testing notification copy — separate scope
- Notification delivery infrastructure / reliability — engineering concern
- ML-based «when to send» optimization — Phase 4+
- Wellness-module-engagement-driven notifications (covered by per-module consent)
- Customer-broadcast notifications from salon owner (e.g., «we're closed for holidays») — separate `tenant-broadcast-policy.md` future
- Notification preferences for non-customers (visitors, browsers) — out of scope

---

## 2. Strategic constraints — non-negotiable

### 2.1 Simple-by-default
Customer landing on Notifications settings sees Simple tier first. Q-CX9 single toggle preserved as primary affordance. «Подробнее» expansion is one tap away — discoverable, not pushy.

### 2.2 Granularity is opt-in
Customer who never wants granular UX never sees it. Power users can drill in. Anti-decision-fatigue for typical case maintained.

### 2.3 Emergency notifications always sent
List §11 — cannot be disabled by any toggle. Medical-adjacent, refund-dispute critical updates, sensitive safety. Customer informed at first-touch that some notifications are operational.

### 2.4 GDPR-aligned consent records
- Every preference change creates `ConsentLog` row §12.1
- Customer can view full consent history
- Customer can export own consent log
- Audit retention: 7 years (consumer-protection statute of limitations)

### 2.5 Single-assistant voice preserved
AI Bot DM never asks «turn off notifications?». Customer initiates. AI can OFFER «не хотите ли тише?» max once per quarter §10.

### 2.6 Snooze not opt-out
Snooze = «pause N days then resume». Opt-out = «off until I re-enable». Different semantics, both supported.

### 2.7 Multi-tenant per-tenant
Customer at tenants A + B has separate notification preferences per tenant. Q-CO5 boundary. No cross-tenant inheritance.

### 2.8 Inline «hush this kind» is fast
Long-press notification → menu «Тише про X» → snoozes that category for 30 days. NO «are you sure?» confirmation dialog. Trust customer.

### 2.9 Defaults sensible
- Operational (bookings, reminders): ON, cannot turn off
- Loyalty: ON
- AI proactive insights: ON
- Marketing / promo: OFF
- Wellness modules: per-module OFF until customer activates

### 2.10 Customer can «снять всё»
Extreme escape hatch §6.4: snooze ALL non-emergency for up to 90 days. Useful during stressful life events. Bot DM acknowledges + reminds at end of snooze.

### 2.11 Quiet hours respected always
Per [`master-time-off-handoff §5.7`](../handoffs/2026-05-19-master-time-off-handoff.md): no notifications 21:00-09:00 customer-local. Customer can adjust window §5. Birthday + emergency override.

### 2.12 NEVER dark UI patterns
- ❌ Confusing checkbox states
- ❌ «Are you SURE you want to miss out?»
- ❌ Default re-enable after N days (sneaking back)
- ❌ Bury opt-out 4 menus deep
- ✅ Clear labels, 1-tap actions, immediate effect

### 2.13 Customer's wellness data never used to target notifications
Per [`core-wellness-profile.md`](./core-wellness-profile.md): wellness data customer-only. NEVER «we noticed you slept badly — book massage now» (creepy). Wellness-INFORMED suggestions allowed only in passive surfaces (dashboard observations) NOT push notifications.

### 2.14 Loyalty notifications gating
Per [`customer-loyalty-rewards-ux §4.5`](./customer-loyalty-rewards-ux.md): loyalty has own granular toggle group. This doc REUSES that, doesn't duplicate.

### 2.15 Frequency caps respected
Existing [`notification-preferences-ux §6`](./notification-preferences-ux.md): caps per audience. Customer cap: 3 proactive Bot DMs per day. This doc inherits.

---

## 3. Two-tier UI

### 3.1 Simple tier (default landing)

Q-CX9 «single toggle» preserved:

```
┌────────────────────────────────────────┐
│ ← Уведомления                            │
├────────────────────────────────────────┤
│ От Ayla (в {{salon_name}})              │
│                                        │
│ ── Всегда приходит ──                    │
│ ✓ Подтверждение и напоминания о записи  │
│ ✓ Изменения в расписании                 │
│ ✓ Важные сообщения от салона             │
│                                        │
│ ── Опционально ──                        │
│                                        │
│ ☑ Помощник может писать первым           │
│   Про самочувствие, повторные процедуры,│
│   бонусы и акции                          │
│                                        │
│ ── Тихие часы ──                         │
│ Не писать с 21:00 до 09:00 ☑             │
│ [Изменить часы]                          │
│                                        │
│ ── Подробнее ──                           │
│ [⚙ Расширенные настройки]                │
│                                        │
└────────────────────────────────────────┘
```

### 3.2 Advanced tier (collapsed; expanded on tap)

«Расширенные настройки» reveals per-category toggles:

```
┌────────────────────────────────────────┐
│ ← Расширенные настройки                  │
├────────────────────────────────────────┤
│ ── По темам ──                            │
│                                        │
│ Записи и напоминания                     │
│ ✓ Всегда (нельзя отключить)              │
│                                        │
│ Бонусы и баллы                            │
│ ☑ Начисления                              │
│ ☑ День рождения                           │
│ ☐ Приглашения друзей напомнить           │
│ ☐ Акции и скидки                         │
│ [Подробнее в бонусах →]                  │
│                                        │
│ Самочувствие (модули)                    │
│ Управление в [Wellness модулях →]        │
│                                        │
│ Подсказки и инсайты                      │
│ ☑ Помощник может писать про здоровье    │
│   (раз в неделю максимум)                │
│ ☑ Помощник может рекомендовать процедуры │
│                                        │
│ Возврат и претензии                      │
│ ✓ Всегда (нельзя отключить)              │
│                                        │
│ Изменения мастеров                       │
│ ☑ Когда мастер уходит в отпуск           │
│ ☑ Когда мастер возвращается              │
│                                        │
│ Маркетинг от салона                      │
│ ☐ Акции                                   │
│ ☐ Сезонные предложения                   │
│                                        │
│ ── Снять всё временно ──                 │
│ [Снять уведомления на 7 дней]            │
│ [Снять уведомления на 30 дней]           │
│                                        │
│ ── ──                                    │
│ [📜 История моих настроек]               │
└────────────────────────────────────────┘
```

### 3.3 Save behavior
- Toggle saves immediately
- 5-second undo toast («Сохранено — Отменить»)
- Audit row per change §12.1

### 3.4 Simple ↔ Advanced sync

When customer in Simple tier toggles master switch OFF, Advanced tier reflects:
- All «can write first» categories switch OFF
- «Operational» stays ON (cannot disable)

When customer in Advanced sets all proactive categories OFF, Simple tier shows master switch OFF.

No conflict possible; underlying state is per-category booleans, Simple tier is a derived view.

### 3.5 Reset to defaults

Bottom of Advanced tier:

```
[Сбросить к настройкам по умолчанию]
```

Confirms before reset; audit captures.

---

## 4. Eight notification categories

| # | Category code | Display | Operational? | Default | Snoozeable? |
|---|---|---|---|---|---|
| 1 | `bookings_operational` | Записи и напоминания | YES (always on) | ON | NO |
| 2 | `loyalty` | Бонусы и баллы | NO | ON | YES (delegates to loyalty section) |
| 3 | `wellness_modules` | Самочувствие модули | NO | OFF (per-module) | YES (delegates to module) |
| 4 | `ai_insights` | Подсказки Ayla | NO | ON | YES |
| 5 | `service_recommendations` | Рекомендации процедур | NO | ON | YES |
| 6 | `refund_disputes` | Возврат и претензии | YES (always on) | ON | NO |
| 7 | `master_changes` | Изменения мастеров | NO | ON | YES |
| 8 | `marketing` | Маркетинг от салона | NO | OFF | YES |

### 4.1 Per-category notification examples

**bookings_operational:**
- Booking confirmed
- T-24h reminder
- T-2h reminder
- Reschedule notice
- Cancellation by customer/admin
- Booking-conflict-resolution alt-master flow

**loyalty:**
- First-earn welcome (per loyalty §13.1)
- Tier-up (§13.4)
- Birthday bonus (§13.3)
- Referral converted (§13.5)
- Long-gap return (§13.6)
- Refund-revoke (§13.7)
- Regular earn (opt-in within §4.5 loyalty settings)

**wellness_modules:**
- Per-module reminders (per [`wellness-input-modules §11`](./wellness-input-modules.md))
- Per-module insights (mood / water / body / etc.)

**ai_insights:**
- Cross-module observations from dashboard (per [`customer-wellness-dashboard-ux §5`](./customer-wellness-dashboard-ux.md))
- Wellness goal references (per [`customer-wellness-goal-setting-ux §8`](./customer-wellness-goal-setting-ux.md))
- AI proactive suggestions

**service_recommendations:**
- Goal-aligned recommendations (per `wellness-goal-setting §7.4`)
- «Similar customers liked» suggestions (Phase 3+)

**refund_disputes:**
- Status updates on open dispute
- Admin counter-offer notification
- Founder escalation acknowledgment
- Refund processed
- Refund confirmed received

**master_changes:**
- Booking-conflict §3.6b master substitution alt-master offer
- Master leave triggering re-book
- Master substitution period start
- Master return
- Master offboarding affecting future booking

**marketing:**
- Salon promo / discount campaigns
- Seasonal offers
- New service launch

### 4.2 Operational categories (1, 6) cannot be disabled

Customer informed at first-touch:

```
{{customer_first_name}}, некоторые вещи я обязана сообщать — напоминания о
записях и важные изменения. Это нельзя отключить, потому что без этого
записи будут пропадать.

Всё остальное — на ваш выбор. Настройте как удобно: [Настройки].
```

### 4.3 Marketing default OFF

Per §2.9: marketing is opt-IN, not opt-OUT. GDPR-aligned. Customer never receives marketing unless they explicitly enable. Even when tenant has «mass promo» feature (Phase 3+), customer's marketing toggle wins.

---

## 5. Quiet hours

### 5.1 Default

Per Q-CN1: 21:00 - 09:00 customer-local timezone. Aligns with master-time-off §5.7.

### 5.2 Customizable

```
┌────────────────────────────────────────┐
│ ← Тихие часы                             │
├────────────────────────────────────────┤
│ Помощник не будет писать в эти часы:    │
│                                        │
│ С: [21:00 ▾]   До: [09:00 ▾]            │
│                                        │
│ Дни недели:                              │
│ ⦿ Все дни одинаково                      │
│ ◯ Выбрать дни (рабочие / выходные)      │
│                                        │
│ ── Исключения ──                         │
│ ☑ Срочные сообщения по записи            │
│   (отмены, изменения)                    │
│ ☑ Возврат и претензии                    │
│ ☑ Поздравление с днём рождения           │
│   (в 10:00 утра)                         │
│                                        │
│ [Сохранить]                              │
└────────────────────────────────────────┘
```

### 5.3 Timezone detection

Customer timezone derived from MAX profile. Customer can override in Profile settings (per [`customer-profile-management-ux.md`](./customer-profile-management-ux.md)).

### 5.4 Quiet hours buffer logic

If notification queued during quiet hours:
- Operational (category 1, 6): sent immediately (override)
- All others: buffered until quiet hours end + 5 min jitter (avoid 9:00 burst)

### 5.5 Birthday timing

Per [`customer-loyalty-rewards-ux §12.9`](./customer-loyalty-rewards-ux.md): birthday Bot DM at 10:00 customer-local. Quiet hours respected (if customer's quiet hours end at 10:30, birthday delays to 10:30).

---

## 6. Snooze

### 6.1 Per-category snooze

In Advanced tier (§3.2), each non-operational category has long-press menu:

```
[Бонусы и баллы ──── ☑]
        ↑ long-press

→ Меню:
   Тише на 7 дней
   Тише на 30 дней
   Тише на 90 дней
   Полностью отключить
   Изменить
```

«Тише на N дней» = `CustomerNotificationSnooze` row §12.2. After N days, auto-resume + Bot DM acknowledgment.

### 6.2 All-snooze (escape hatch)

Simple tier bottom OR Advanced tier:

```
[Снять уведомления на 7 дней]
[Снять уведомления на 30 дней]
[Снять уведомления на 90 дней]
```

Excludes operational + emergency §11. Customer sees confirmation:

```
┌────────────────────────────────────────┐
│ Тише на 30 дней                          │
├────────────────────────────────────────┤
│ С сегодня до 18 июня Ayla будет         │
│ писать только по записям и срочным      │
│ вопросам.                                │
│                                        │
│ Тёплые сообщения, бонусы, инсайты,        │
│ рекомендации — пауза.                    │
│                                        │
│ Можно снять раньше в настройках.         │
│                                        │
│ [Применить]                              │
└────────────────────────────────────────┘
```

### 6.3 Snooze re-engagement

Last day of snooze, Bot DM:

```
{{customer_first_name}}, завтра Ayla снова сможет писать первой.
Хотите оставить тихий режим — продлите в настройках.

[Открыть настройки]   [Спасибо, всё ок]
```

NO «we missed you!» framing. Just informational.

### 6.4 Snooze stacking

If customer snoozes loyalty category AND all-snoozes globally, both apply. Per-category snooze respected even after global lifts. No conflict.

### 6.5 Snooze during emergency

Per §11: emergency overrides snooze always. Snooze does not silence medical-adjacent / refund-critical / sensitive.

---

## 7. Multi-tenant customer

### 7.1 Per-tenant preferences

Customer at salons A + B has separate `CustomerNotificationPreferences` rows per tenant. Mini App tenant selector switches view.

### 7.2 Global cap interaction

Per [`notification-preferences-ux §6`](./notification-preferences-ux.md): 3 proactive Bot DMs/day customer cap. Cap is GLOBAL (sum across tenants). If tenant A sent 3 today, tenant B's proactive deferred. Operational always passes.

### 7.3 Quiet hours global

Quiet hours apply to ALL tenants for that customer (timezone is customer's, not tenant's). One customer = one timezone = one quiet hours window.

### 7.4 Per-tenant settings UI navigation

Customer Mini App tenant selector at top:

```
┌────────────────────────────────────────┐
│ ← Уведомления [Студия Натали ▾]         │
├────────────────────────────────────────┤
│ ...                                      │
```

Switching tenant changes per-tenant prefs view. Quiet hours + emergency overrides shared.

### 7.5 Account closure interaction

If customer closes account at tenant A (per future customer-data-export-handoff Tier 1 #3):
- Tenant A notification prefs deleted
- Tenant B unchanged
- Consent log retained per §8.4

---

## 8. GDPR-aligned consent audit

### 8.1 Every change logged

Per Q-CN5: `ConsentLog` model row created on every:
- Category toggle change
- Snooze applied / cancelled
- Quiet hours window changed
- All-snooze invoked
- Reset to defaults
- Customer opt-out from notification system entirely (Phase 4+ extreme case)

### 8.2 Customer can view own consent history

```
┌────────────────────────────────────────┐
│ ← История настроек                       │
├────────────────────────────────────────┤
│ 17 мая 14:30                              │
│ Отключила «Маркетинг от салона»          │
│                                        │
│ 12 мая 10:15                              │
│ Установила тихие часы 21:00-09:00       │
│                                        │
│ 5 мая 18:00                              │
│ Включила «Бонусы»                         │
│                                        │
│ ...                                      │
│                                        │
│ [📤 Скачать всю историю]                  │
└────────────────────────────────────────┘
```

### 8.3 Consent log export

CSV / PDF / JSON formats. Customer's right per GDPR-aligned design.

### 8.4 Retention

7 years per consumer-protection statute (Russia + GDPR). Per Q-CN8.

### 8.5 Cross-tenant consent log

Customer's consent log is GLOBAL (their data, their right). NOT tenant-scoped from consent perspective — even if customer leaves a tenant, consent decisions are theirs.

### 8.6 Audit integrity

Consent log append-only. Cannot be edited or deleted by admin. Customer can request hard-delete only via founder process (per future customer-data-export-handoff doc #3).

---

## 9. Inline «hush» on every notification

### 9.1 Long-press notification

In MAX Bot DM or Mini App notification view, customer long-presses → context menu:

```
[Notification: «Бонусы +50 за маникюр сегодня»]
        ↑ long-press

→ Меню:
   Тише про бонусы на 7 дней
   Тише про бонусы на 30 дней
   Полностью отключить эту тему
   Понятно (закрыть меню)
```

### 9.2 Category auto-detection

System knows which `category_code` each notification belongs to (per §12.3 metadata). Long-press surfaces that category's snooze options.

### 9.3 Audit row on inline-hush

`ConsentLog` records as if customer used Settings UI. No difference in audit.

### 9.4 Discoverability

Per Q-CN3: customer needs to know long-press works. After 3 days of customer NOT using settings, AI Bot DM mentions:

```
Если какие-то мои сообщения слишком часто — зажмите их и выберите
«Тише про эту тему». Это быстрее, чем настройки.
```

Sent ONCE per customer. Documented in audit «hush_tip_shown».

---

## 10. AI Bot DM self-check

### 10.1 Trigger

Every 3 months (per quarter), AI Bot DM self-check:

```
{{customer_first_name}}, давно не спрашивала — мои сообщения сейчас в
порядке по частоте? Иногда я могу писать слишком много.

[Всё ок]   [Чуть тише]   [Слишком много]
```

### 10.2 Customer choices

- «Всё ок» → no change, audit «self_check_ack_ok»
- «Чуть тише» → reduces proactive frequency by 50% for 30 days, audit
- «Слишком много» → all proactive snoozed 30d, AI Bot DM «понятно, паузу взяла»

### 10.3 Self-check timing

- Customer-local local 14:00 (afternoon, not morning fatigue)
- Skipped if customer snoozed-all currently
- Skipped if customer has < 3 Bot DMs received in past 30 days (under-engaged)

### 10.4 Customer can opt-out of self-check

In Advanced settings:

```
☑ Спрашивать раз в квартал, нормально ли частота
```

Default ON. Customer can opt out.

---

## 11. Emergency-always-on list

Notifications that bypass all snooze / quiet hours / category opt-out:

| Trigger | Category | Why |
|---|---|---|
| Booking cancelled by salon < 2h before | `bookings_operational` | Customer expectations |
| Booking time/master changed by admin | `bookings_operational` | Reliability |
| Refund dispute admin response | `refund_disputes` | Customer's rights |
| Refund processed | `refund_disputes` | Financial confirmation |
| Medical-adjacent symptom-tracker «contact doctor» | (varies) | Safety |
| Account security event (login from new device) | (account category, Phase 3+) | Security |
| Tenant SUSPENDED affecting customer's bookings | `bookings_operational` | Customer's bookings impacted |
| Master no-show → urgent rebook offer | `bookings_operational` | Customer expectations |

### 11.1 Customer informed upfront

At first-touch, customer sees list in onboarding (collapsed). Sets expectation.

### 11.2 Emergency audit

Each emergency-bypass notification audit-tagged. Admin can review what was sent during customer's snooze.

### 11.3 AI tone in emergency

Per [`ayla-identity-and-brand §2.2`](./ayla-identity-and-brand.md): emergency notifications use customer voice — calm, factual, no «URGENT!!» framing. Just direct: «Запись на 17 мая отменилась — давайте подберу другое время».

---

## 12. Data models

### 12.1 `CustomerNotificationPreferences`

```python
class CustomerNotificationPreferences(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('identity.BotUser', on_delete=CASCADE, related_name='notification_prefs')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    # Per-tenant prefs

    # Per-category booleans (default per §4 matrix)
    bookings_operational = models.BooleanField(default=True)  # cannot disable
    loyalty = models.BooleanField(default=True)
    wellness_modules = models.BooleanField(default=False)  # per-module managed separately
    ai_insights = models.BooleanField(default=True)
    service_recommendations = models.BooleanField(default=True)
    refund_disputes = models.BooleanField(default=True)  # cannot disable
    master_changes = models.BooleanField(default=True)
    marketing = models.BooleanField(default=False)  # opt-in

    # Quiet hours (customer-global, mirrored per tenant for convenience)
    quiet_hours_start = models.TimeField(default='21:00')
    quiet_hours_end = models.TimeField(default='09:00')
    quiet_hours_days = models.JSONField(default=lambda: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'])

    # Self-check
    quarterly_self_check_enabled = models.BooleanField(default=True)
    last_self_check_at = models.DateTimeField(null=True, blank=True)

    # Hush-tip
    hush_tip_shown_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('customer', 'tenant')]
        indexes = [
            Index(fields=['customer', 'tenant']),
        ]
```

### 12.2 `CustomerNotificationSnooze`

```python
class CustomerNotificationSnooze(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('identity.BotUser', on_delete=CASCADE, related_name='notification_snoozes')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    SCOPE_CHOICES = [
        ('all', 'All non-emergency notifications'),
        ('category', 'Specific category'),
    ]
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    category_code = models.CharField(max_length=64, blank=True, default='')  # populated if scope=category

    started_at = models.DateTimeField(auto_now_add=True)
    ends_at = models.DateTimeField()
    cancelled_at = models.DateTimeField(null=True, blank=True)

    SOURCE_CHOICES = [
        ('settings_ui', 'Settings UI'),
        ('inline_hush', 'Long-press hush'),
        ('bot_self_check', 'Bot self-check «too much»'),
        ('all_snooze_extreme', 'All-snooze escape hatch'),
    ]
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES)

    class Meta:
        indexes = [
            Index(fields=['customer', 'tenant', 'ends_at']),
        ]
```

### 12.3 `ConsentLog`

```python
class ConsentLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('identity.BotUser', on_delete=CASCADE, related_name='consent_logs')
    tenant = models.ForeignKey('tenancy.Tenant', null=True, blank=True, on_delete=SET_NULL, related_name='+')
    # null = customer-global event (e.g., quiet hours change across all tenants)

    ACTION_CHOICES = [
        ('category_toggled', 'Category toggled'),
        ('snooze_applied', 'Snooze applied'),
        ('snooze_cancelled', 'Snooze cancelled'),
        ('snooze_expired_auto', 'Snooze expired naturally'),
        ('quiet_hours_changed', 'Quiet hours changed'),
        ('reset_to_defaults', 'Reset to defaults'),
        ('self_check_response', 'Quarterly self-check response'),
        ('hush_tip_shown', 'Hush tip Bot DM shown'),
        ('all_snooze_invoked', 'All-snooze invoked'),
        ('opted_out_entirely', 'Opted out entirely (Phase 4+)'),
    ]
    action = models.CharField(max_length=64, choices=ACTION_CHOICES)

    field_changed = models.CharField(max_length=64, blank=True, default='')
    # e.g., 'loyalty', 'quiet_hours_start'

    value_before = models.JSONField(default=dict, blank=True)
    value_after = models.JSONField(default=dict, blank=True)

    source = models.CharField(max_length=32, blank=True, default='')
    # 'settings_ui', 'inline_hush', 'first_touch_default', etc.

    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['customer', '-at']),
        ]
```

### 12.4 Notification metadata addition

When sending Bot DM, attach `category_code` metadata so:
- `CustomerNotificationSnooze` enforcement
- Inline-hush long-press can identify category
- Audit per-category counters

Existing notification infra extends with `category_code` field on dispatch.

---

## 13. API contracts

### 13.1 Customer endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/customer/notification-prefs` | Read own per-tenant prefs |
| PATCH | `/api/v1/customer/notification-prefs` | Update categories / quiet hours |
| POST | `/api/v1/customer/notification-prefs/snooze` | Apply snooze (all or category) |
| DELETE | `/api/v1/customer/notification-prefs/snooze/<id>` | Cancel snooze |
| POST | `/api/v1/customer/notification-prefs/reset` | Reset to defaults |
| GET | `/api/v1/customer/consent-log` | List own consent log entries |
| POST | `/api/v1/customer/consent-log/export` | Generate consent log export (CSV/PDF/JSON) |
| GET | `/api/v1/customer/notification-prefs/active-snoozes` | List own active snoozes |

### 13.2 Admin endpoints (limited; for support only)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/customer/<customer_id>/notification-prefs` | View customer's prefs (read-only; for support context) |
| GET | `/api/v1/admin/customer/<customer_id>/consent-log` | View consent log (for compliance audit) |

### 13.3 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/notifications/dispatch` | Internal entry point; checks prefs + snoozes + quiet hours + emergency rules |
| POST | `/internal/notifications/quarterly-self-check-scan` | Cron daily — find customers due for self-check §10 |
| POST | `/internal/notifications/hush-tip-scan` | Cron — find customers due for hush-tip §9.4 |

### 13.4 Sample request: PATCH prefs

```json
{
  "category": "marketing",
  "enabled": false
}
```

OR:

```json
{
  "quiet_hours_start": "22:00",
  "quiet_hours_end": "08:00"
}
```

Validation:
- Operational categories (`bookings_operational`, `refund_disputes`) cannot be disabled — 400 error
- Quiet hours: start must be valid HH:MM; end can wrap past midnight

Response 200 + audit row created.

---

## 14. Events emitted

Add to [`event-taxonomy.md §3.2 customer-domain`](./event-taxonomy.md) — extends existing `customer.consent.changed`:

| Trigger | Event | Notes |
|---|---|---|
| Category toggled | `customer.consent.changed` (existing) | `consent_type=category_name`, before, after |
| Snooze applied | NEW: `customer.notification.snoozed` | scope, category, duration_days |
| Snooze cancelled by customer | NEW: `customer.notification.snooze_cancelled` | |
| Snooze expired naturally | NEW: `customer.notification.snooze_expired` | |
| Quiet hours changed | NEW: `customer.notification.quiet_hours_changed` | new_start, new_end |
| Reset to defaults | NEW: `customer.notification.reset_to_defaults` | |
| Quarterly self-check sent | NEW: `customer.notification.self_check_sent` | |
| Quarterly self-check answered | NEW: `customer.notification.self_check_answered` | response |
| Hush tip shown | NEW: `customer.notification.hush_tip_shown` | |
| Emergency override bypassed snooze | NEW: `customer.notification.emergency_override_applied` | original_snooze_id |

9 NEW + 1 reused. §14.

---

## 15. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Make marketing default ON | GDPR + ethics | Default OFF §2.9 |
| Hide opt-out 4 menus deep | Dark pattern §2.12 | One tap from settings |
| Default re-enable after time | Sneaking back | Opt-out is opt-out unless re-enabled by customer |
| «Are you sure?» dialog on every toggle | Friction | Save immediately + undo toast §3.3 |
| Customer wellness data used to time notifications | Creepy §2.13 | Notification timing from prefs only |
| Inline hush long-press requires confirmation | Slowness §2.8 | Trust customer; 1-step |
| Customer self-check «Did you mean to disable?» | Manipulative | Self-check is genuine — accepts «too much» without arguing |
| Auto-deescalate snooze to «just 1 day» | Customer trust | Customer's snooze choice respected fully |
| Emergency-always-on list is buried | Customer doesn't know | Surfaced at first-touch §11.1 |
| Admin can override customer snooze | Privacy creep | NEVER (audit even reads admin-only) |
| Notifications during quiet hours from non-emergency | Disrespect | Buffer until window ends §5.4 |
| Consent log editable | Audit integrity | Append-only §8.6 |
| Cross-tenant consent inheritance | Privacy §2.7 | Per-tenant separately |
| Birthday spam (multiple greetings) | Annoying | Once per year per loyalty §8.2 |
| Long-snooze followed by «we missed you!» | Re-engagement manipulation | §6.3 informational only |
| AI Bot DM «turn off notifications?» | Reverse manipulation | Customer initiates §2.5 |
| Snooze that ignores emergency | Safety risk | Emergency overrides always §11 |
| Multi-tenant customer same prefs across all | Bad UX | Per-tenant §7 |
| Category toggle changes wellness modules | Wrong abstraction | Wellness managed per-module separately §4 |
| «Тише про эту тему» but actually disables operational | Bait-and-switch | Operational protected §4.2 |

---

## 16. Acceptance criteria (engineering checklist)

- [ ] 3 models §12 (CustomerNotificationPreferences, CustomerNotificationSnooze, ConsentLog)
- [ ] 11 endpoints §13 (8 customer + 2 admin + 3 internal)
- [ ] Simple tier UI default §3.1
- [ ] Advanced tier collapsed by default; expandable §3.2
- [ ] 8 categories §4 with correct default state
- [ ] Operational + refund_disputes cannot be disabled §4.2
- [ ] Marketing default OFF §4.3
- [ ] Quiet hours customizable §5
- [ ] Quiet hours buffer logic §5.4
- [ ] Snooze: per-category + all-snooze §6
- [ ] Inline long-press hush §9
- [ ] Hush-tip Bot DM once per customer §9.4
- [ ] Quarterly self-check Bot DM §10
- [ ] Emergency-always-on enforcement §11
- [ ] Multi-tenant per-tenant prefs §7
- [ ] Quiet hours global per customer (timezone) §7.3
- [ ] Cap interaction §7.2 with notification-preferences §6
- [ ] Consent log append-only + 7-year retention §8
- [ ] Customer consent export §8.3
- [ ] Wellness modules opt-in delegation §4.1
- [ ] Loyalty delegation §4.1 / §2.14
- [ ] 9 NEW events §14
- [ ] Anti-patterns §15 avoided
- [ ] Tests: per-category toggle / operational protected / snooze respected / inline hush / quiet hours buffer / emergency override / cross-tenant isolation / consent log audit append-only / quarterly self-check timing / hush-tip once / marketing default OFF

---

## 17. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-CN1** | Default quiet hours — 21:00-09:00 same as master? | YES MVP. Customer can override. | UX | 🟢 |
| **Q-CN2** | Self-check frequency — quarterly correct? | YES quarterly MVP. Tune based on Bot DM volume per customer. | UX + Data | 🟡 |
| **Q-CN3** | Hush-tip discoverability — proactive Bot DM after 3 days OR onboarding? | Onboarding mention + day 3 reinforcer if customer hasn't engaged with settings. | UX | 🟢 |
| **Q-CN4** | Snooze duration options — 7 / 30 / 90 days correct? | 7 / 30 / 90 MVP. 90 = «I'm having a tough time» extreme. | Policy | 🟢 |
| **Q-CN5** | GDPR consent log retention — 7 years correct? | YES — Russia consumer-protection + GDPR alignment. | Privacy + Legal | 🔴 PRE-DEPLOY |
| **Q-CN6** | All-snooze max — 90 days OR unlimited? | 90 days max MVP. After 90d customer revisits explicitly. Avoids forgotten-snooze edge cases. | UX | 🟢 |
| **Q-CN7** | Quiet hours wrap past midnight — supported? | YES (e.g., 22:00-08:00). UI displays both times. | UX + Eng | 🟢 |
| **Q-CN8** | Customer with quiet hours during dispute resolution — emergency overrides? | YES per §11. Customer expects timely refund updates. | Policy | 🟢 |
| **Q-CN9** | Tenant-broadcast feature Phase 3+ — customer marketing opt-out applies? | YES per §4.3 + §2.9. Even mass campaigns respect customer marketing toggle. | PM | 🟡 |
| **Q-CN10** | Customer who never engages with notifications at all — auto-snooze proactive? | NO — customer's choice. Auto-deescalation = patronizing. Self-check Bot DM §10 surfaces «too much?» offer. | Policy | 🟢 |
| **Q-CN11** | Master broadcast (mass to many customers about master-leave) — counts as 1 cap or per-customer? | Per-customer cap. Master-broadcast to many counts 1× per customer's daily cap. | Eng + Policy | 🟡 |
| **Q-CN12** | Birthday at 10:00 vs quiet hours end — which wins? | Quiet hours end wins (e.g., if quiet hours end at 10:30, birthday at 10:30). Avoids midnight birthday. | UX | 🟢 |
| **Q-CN13** | Sensitive emergency tier-2 from review/refund — extra acknowledgment required? | YES — sensitive emergencies (e.g., medical-adjacent) ask «получили?» follow-up. Audit captures. | Policy | 🔴 PRE-DEPLOY |
| **Q-CN14** | Wellness module opt-in flow ALSO disables in advanced settings? | YES — settings is mirror per [`notification-preferences §3.3`](./notification-preferences-ux.md). Module activation has full consent dialog; settings can DISABLE but not ENABLE. | UX | 🟢 |
| **Q-CN15** | Multi-tenant customer's all-snooze affects all tenants? | YES — customer-global decision. Per-category snooze can be per-tenant. | UX | 🟡 |
| **Q-CN16** | Inline hush counts toward category disable threshold? | If customer hushes same category 3× in 90 days → suggest in Bot DM «полностью отключить эту тему?». Not auto-disable. | Policy + UX | 🟡 |
| **Q-CN17** | Customer account closure → consent log retained? | YES per §8.4 + §8.6. Even after account deleted, consent record retained per compliance. | Privacy | 🔴 PRE-DEPLOY |
| **Q-CN18** | Customer requests «удалите всю историю настроек» — allowed? | Customer can request via founder; not self-service. Audit integrity tension. Founder approves with reason. | Privacy + Legal | 🔴 PRE-DEPLOY |
| **Q-CN19** | Bot DM tone in emergency-override — same as customer voice? | YES per §11.3. Calm, factual, NO «URGENT». | UX + AI | 🟢 |
| **Q-CN20** | Customer notification fatigue detection — algorithm? | Phase 3+ — basic «> 3 hushes in 7 days = suggest broader pause» heuristic. ML phase 4+. | Eng + UX | 🟡 |

---

## 18. Cross-document linkage

- [`notification-preferences-ux.md §3`](./notification-preferences-ux.md) — foundational customer section; this doc extends with granular advanced tier + quiet hours + snooze + audit
- [`customer-loyalty-rewards-ux.md §4.5`](./customer-loyalty-rewards-ux.md) — loyalty has own granular toggle group; this doc delegates §4.1
- [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md) — refund_disputes always-on category §4.2
- [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) — booking lifecycle notifications
- [`customer-profile-management-ux.md`](./customer-profile-management-ux.md) — timezone source §5.3
- [`customer-wellness-dashboard-ux.md`](./customer-wellness-dashboard-ux.md) — ai_insights category §4
- [`wellness-input-modules.md §11`](./wellness-input-modules.md) — wellness_modules per-module opt-in §4
- [`master-time-off-handoff §5.7`](../handoffs/2026-05-19-master-time-off-handoff.md) — quiet hours alignment
- [`ayla-identity-and-brand §2.4`](./ayla-identity-and-brand.md) — voice
- [`event-taxonomy.md §3.2`](./event-taxonomy.md) — 9 NEW events §14
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) — SUSPENDED state notification behavior
- [`../decisions-log.md`](../decisions-log.md) — Q-CN1..Q-CN20 + Q-CX9 update

---

## 19. What this unblocks

- **GDPR-aligned customer consent** — granular per-purpose toggles with audit
- **Customer power-user satisfaction** — advanced tier respects sophisticated control needs
- **Customer trust foundation** — emergency-always-on clarifies what's truly mandatory
- **Multi-tenant customer support** — per-tenant prefs avoid cross-leak
- **Notification fatigue prevention** — snooze + self-check + quiet hours
- **Loyalty / refund / wellness notification governance** — categories defined; downstream specs respect
- **Russia consumer-protection compliance** — audit + retention defined

## 20. What this does NOT unblock

- ❌ Non-Bot-DM channels (SMS / email) — Phase 4+
- ❌ ML-based notification optimization
- ❌ Cross-tenant consent aggregation (privacy boundary)
- ❌ Customer broadcast from tenant (separate scope)
- ❌ Skip Q-CN5 retention validation (pre-deploy)
- ❌ Skip Q-CN13 sensitive emergency acknowledgment (pre-deploy)
- ❌ Skip Q-CN17/Q-CN18 closure / hard-delete policy (pre-deploy)
- ❌ Anti-spam-bot detection
- ❌ A/B testing notification copy

---

## 21. Q-CX9 update

This doc REVISES Q-CX9 from existing notification-preferences-ux:

**Old:** «Single toggle, NOT 14 individual switches (decision fatigue)»

**New:** «Simple tier (single toggle) default + Advanced tier (8 categories + quiet hours + snooze + consent log) opt-in. Decision fatigue avoided by collapsed default; granularity available for power users + GDPR compliance.»

Captured in `../decisions-log.md` as Q-CX9-r2.

---

## 22. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Notification infra backend lead | ☐ | |
| Mini App frontend (Simple + Advanced tier + snooze + consent log + inline hush) | ☐ | |
| AI prompt eng (hush tip + quarterly self-check + emergency framing) | ☐ | |
| Loyalty steward (§4.1 delegation alignment) | ☐ | |
| Refund-dispute steward (§4.2 always-on + Q-CN13) | ☐ | 🔴 PRE-DEPLOY |
| Wellness modules steward (§4.1 module delegation) | ☐ | |
| Privacy / Legal (§8 consent log + Q-CN5 retention + Q-CN17 closure + Q-CN18 hard-delete) | ☐ | 🔴 PRE-DEPLOY |
| Founder (Q-CN18 hard-delete approval process + Q-CN9 broadcast policy) | ☐ | 🔴 PRE-DEPLOY |
| Accessibility (WCAG 2.2 AA on Simple + Advanced + long-press affordance) | ☐ | |

## Last verified
2026-05-19 (initial draft, Simple + Advanced tier + 8 categories + quiet hours + snooze + inline hush + GDPR audit + emergency-always-on + Q-CX9-r2 update — locked)
