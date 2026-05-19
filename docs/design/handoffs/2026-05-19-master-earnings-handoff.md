# Master Earnings / Payout / Tips — Engineering Handoff

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Production-blocking for master retention — full money UX spec
**Reads:** [`../policies/ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md), [`../policies/tenant-as-provider-model.md`](../policies/tenant-as-provider-model.md), [`../policies/master-conversational-templates.md`](../policies/master-conversational-templates.md) (r2), [`../policies/master-onboarding-m0-m7.md`](../policies/master-onboarding-m0-m7.md), [`../handoffs/2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md), [`../policies/attribution-policy.md`](../policies/attribution-policy.md), [`../policies/booking-conflict-resolution-ux.md`](../policies/booking-conflict-resolution-ux.md), [`../policies/ayla-emergency-fallback-policy.md`](../policies/ayla-emergency-fallback-policy.md), [`../policies/tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md), [`../policies/contract-offer-acceptance-display-ux.md`](../policies/contract-offer-acceptance-display-ux.md)

> Master will not stay on a platform where they don't see the money. This handoff makes earnings transparent (per booking, per tip, per cycle), supports the 3 common Russian compensation models (employee / samozanyatyy / IP), surfaces disputes with full audit, and never gamifies money.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](../policies/ayla-identity-and-brand.md) memory 2026-05-19: master earnings is **Ayla Pro** tenant-side feature per [`tenant-as-provider-model §5.2`](../policies/tenant-as-provider-model.md). Master ↔ Ayla notifications (cycle summary, payout confirmations) use functional Ayla voice. Master earnings disputes fire emergency fallback per [`ayla-emergency-fallback-policy §3.1`](../policies/ayla-emergency-fallback-policy.md) `payment_dispute` tier. Deprecated `single-assistant-identity.md` + `conversation-ownership-policy.md` references in §-marked rows preserved as backend mechanic.

---

## 0. Why this exists

### 0.1 The trust foundation

Per [`master-mobile-handoff.md §3`](./2026-05-18-master-mobile-handoff.md): master ACTIVE role retention = ability to trust the salon's calculation of their earnings. Without explicit per-booking earnings UI, master:
- Doesn't know if they're underpaid until weekly reconciliation
- Has no audit trail when disputing
- Resorts to manual spreadsheets in parallel (signal of platform failure)
- Eventually leaves the salon → masters are mobile assets

### 0.2 The gap

- `master-mobile-handoff.md` mentions dashboard but no money screens
- `master-conversational-templates.md` has touchpoints but no earnings notifications
- No spec for: tips, commission transparency, payout cycles, disputes, tax export
- No model: `MasterCompensationRule`, `MasterEarning`, `Tip`, `Payout`, `EarningDispute`

### 0.3 The promise

Single source for:
- 4 master earnings screens §6
- Commission rule transparency to master §3
- Tip flow end-to-end (customer ask → master receipt → audit) §7
- Payout cycle UX (expected → confirmed → received) §8
- Dispute flow with reconciliation §9
- Tax export (CSV/PDF) §10
- 7 NEW models §11, 8 endpoints §12, 12 events §13
- Anti-gamification + anti-comparison guards §14

---

## 1. Scope

### IN
- Master earnings dashboard (Mini App tab «Доход»)
- Per-booking earnings detail (computed at booking COMPLETED state)
- Tip flow: customer-initiated via Mini App OR Bot DM → platform-passthrough OR external (salon pays out)
- Commission rules display (master's own, NEVER others')
- Payout cycles: weekly / bi-weekly / monthly (admin configures)
- Payout confirmation from salon side
- Master payout receipt confirmation
- Earning dispute flow (master raises → admin reviews → founder if needed)
- Tax-friendly export (CSV/PDF/JSON)
- 3 compensation model support: salaried-employee (commission %), samozanyatyy (44% commission default per Russian market), IP
- AI Bot DM earnings touchpoints (after each booking + cycle summary)
- Privacy: master sees only own data
- Multi-tenant master earnings (works in 2 salons → separate ledgers)

### OUT
- Payment processing (NO platform-as-merchant; salon pays via their bank/cash)
- Tax filing automation (we export data; master/salon files)
- Tip-only platforms tipping integration (CloudTips, etc.) — Phase 4+
- Loan / advance against earnings («wage on demand») — Phase 4+
- Performance-based bonus engine (gamification = anti-pattern §14)
- Cross-master comparison rankings («Anna earned more») — privacy + shame
- Real-time payments / instant payout — Phase 4+
- Crypto / non-RUB currency — Phase 4+
- IP/самозанятый registration help (separate scope)
- Customer card-on-file storage (out of scope; YC or external)
- Refunds to customer (separate booking-cancellation scope but money side here referenced)
- Discount reconciliation (separate pricing scope; master earning honors final paid amount)

---

## 2. Strategic constraints — non-negotiable

### 2.1 Master sees own data only
Per [`master-mobile-handoff.md §8`](./2026-05-18-master-mobile-handoff.md) permissions: master CANNOT see other masters' earnings, commission rates, tip rates. API enforces 403 + UI never has paths to other-master money data.

### 2.2 NO gamification
- ❌ Streaks («3 weeks of growing earnings!»)
- ❌ Badges / achievements («Top tipper recipient!»)
- ❌ Leaderboards
- ❌ Confetti on payout
- ❌ Goal targets («reach 50k/month»)

Money is operational reality, not a game.

### 2.3 NO cross-master comparison
- ❌ «You're in top 3 this month»
- ❌ «Master Anna earned X% more»
- ❌ «Salon average is Y»

If absolute privacy (master doesn't even know if they're at top or bottom). Aggregate stats reserved for admin/founder views.

### 2.4 Single-assistant identity preserved
AI Bot DM on earnings uses internal honest framing:
- ✅ «Закрыли вашу процедуру у {{customer_first_name}} — записал {{amount}} ₽ в ваш доход за смену»
- ❌ «Bot says: commission earned = X» (cold/sales)

### 2.5 Commission transparency at hire
Master MUST see their commission structure at onboarding M3+ before accepting first booking. NOT buried in app settings. Per §6.1 Welcome card during M3.

### 2.6 No mid-cycle rule changes affect closed earnings
If admin changes commission % mid-period, change applies to bookings created AFTER change timestamp. Already-completed bookings honor original rate. Audit captured.

### 2.7 Tip handling: 3 modes per tenant
Per Q-ME-1: salon configures tip mode at setup:
- **EXTERNAL:** salon receives, pays through their channel; platform records intent only
- **PLATFORM_PASSTHROUGH:** customer pays platform (Russia: YooKassa MVP or QR); platform records, salon settles
- **DISABLED:** no tip UX surfaces

### 2.8 Disputes always reviewable
Per [`booking-conflict-resolution-ux §10`](../policies/booking-conflict-resolution-ux.md) audit model: every dispute creates audit row. SLA: 48h admin response, 7d founder escalation.

### 2.9 Tax-export self-service
Master can export own data anytime (CSV/PDF/JSON) without asking admin. Privacy: data is theirs.

### 2.10 No platform commission visible to master
Salon's contract with platform (per [`contract-offer-acceptance-display-ux.md`](../policies/contract-offer-acceptance-display-ux.md)) is salon's relationship with platform. Master sees salon's commission on services, NOT salon's commission to platform. Salon's commission to platform is a salon-cost matter.

### 2.11 Russia regulatory minimums
- Samozanyatyy regime: receipt-issuance integration future Phase 4+ via «Мой налог» API
- Cash receipts: salon handles, app supplements
- KKT (cash register) compliance: salon-side, app records meta only

---

## 3. Compensation models (3 supported)

### 3.1 Model SALARIED_EMPLOYEE
Master is salon employee. Compensation = base salary + commission % on services + tips.

**App tracks:**
- Commission % per service category (configurable)
- Tip recipient: master 100% (or admin split if tenant configures §7.4)
- Base salary: tracked by salon outside app (NOT in earnings dashboard)
- Earnings dashboard shows: commission earned + tips (NOT base)

**Display label:** «Комиссия с услуг» + «Чаевые»

### 3.2 Model SAMOZANYATYY (default)
Master is self-employed under Russian samozanyatyy regime. Salon pays master per service.

**App tracks:**
- Master's percentage on services (typically 40-60%; default 44% per Russian market)
- Tips: master 100%
- Commission shown as «Доход с услуг» (master's full earned amount)

**Display label:** «Доход с услуг (моя доля)» + «Чаевые»

### 3.3 Model IP_INDIVIDUAL_ENTREPRENEUR
Master is registered IP, contracts with salon. Higher complexity, less common in beauty.

**App tracks:**
- Per-service price master receives (vs commission %)
- Tips: master 100%
- Tax responsibility on master

**Display label:** «Получаю с услуги» + «Чаевые»

### 3.4 Model selection
Set per master at onboarding M2 (profile setup, screen 2.5 NEW). Default samozanyatyy. Salon admin can override per master.

---

## 4. Master ↔ admin earnings relationship

### 4.1 Admin configures (per master)
- Compensation model §3
- Commission % per service category (or per-service override)
- Tip allocation (master 100% / split with admin)
- Payout cycle: weekly Mon / bi-weekly / monthly 1st / monthly last-day
- Payout method (informational): bank transfer / cash / Yandex Pay / Tinkoff / etc.

### 4.2 Admin confirms (per cycle)
- «Sent payout to {{master}} on {{date}}: {{amount}} ₽»
- Master confirms receipt within 7d OR raises dispute
- Auto-confirm if no master action in 7d (audit captured)

### 4.3 Admin sees (master earnings dashboard analog)
- Per-master earnings summary
- Per-master payout history
- Outstanding (next cycle preview)
- Active disputes (badge alert)

### 4.4 Bidirectional transparency
Admin sees same numbers master sees. No master-side fudging possible. Single ledger source.

---

## 5. Master ↔ AI earnings touchpoints

Per [`master-conversational-templates.md §5`](../policies/master-conversational-templates.md): add 4 NEW touchpoints to existing 15.

### 5.16 Booking completed → earnings posted

After admin marks booking COMPLETED (or auto-complete on slot+30min if no admin action):

```
{{master_first_name}}, закрыл услугу у {{customer_first_name}}.

В смену записал:
• {{service_name}} — {{master_amount}} ₽
{% if tip_amount %}• Чаевые от {{customer_first_name}} — {{tip_amount}} ₽{% endif %}

Итого за сегодня: {{day_total}} ₽
```

Sent within 5 min of COMPLETED status.

### 5.17 Tip received from customer (real-time)

When customer adds tip via Mini App OR Bot DM:

```
{{customer_first_name}} оставила вам чаевые — {{tip_amount}} ₽ 🙏
{% if mode == 'platform_passthrough' %}
Деньги придут в этом цикле выплат ({{next_payout_date}}).
{% elif mode == 'external' %}
{{customer_first_name}} сказала, что отдаст лично — на смене или картой.
{% endif %}
```

Master can react («thanks» / emoji) — relayed to customer as thank-you receipt §7.5.

### 5.18 Cycle summary (payout day)

Morning of payout day:

```
{{master_first_name}}, итоги цикла {{cycle_start}}-{{cycle_end}}:

• Услуги: {{service_total}} ₽ ({{booking_count}} процедур)
• Чаевые: {{tip_total}} ₽
• ИТОГО: {{cycle_total}} ₽

Способ выплаты: {{payout_method}}
Жду подтверждения от {{salon_owner_name}}, что отправили.
```

### 5.19 Payout confirmed by salon

When admin marks payout sent:

```
{{salon_owner_name}} подтвердила выплату — {{amount}} ₽.

Получили?
[✓ Да, всё ок]   [✗ Нет, не пришло]   [⚠ Сумма другая]
```

«Да» → close cycle.
«Нет» → opens dispute auto-typed «не пришло».
«Сумма другая» → opens dispute requesting amount input.

### 5.20 Dispute resolution updates (passive)

When admin / founder resolves dispute, AI relays neutral framing per §9.6.

---

## 6. Master Mini App «Доход» tab

New top-level tab in Mini App after «Расписание». Position 3 of 4 (Чаты / Расписание / **Доход** / Профиль).

### 6.1 Tab home — current cycle

```
┌────────────────────────────────────────┐
│ 💰 Доход                                │
├────────────────────────────────────────┤
│ ── Текущий цикл ──                     │
│ {{cycle_start}}-{{cycle_end}}          │
│                                        │
│ Услуги (10 процедур)         12 400 ₽  │
│ Чаевые (3)                     1 200 ₽  │
│ ──────────────────────────────────────  │
│ Итого                         13 600 ₽  │
│                                        │
│ Выплата ожидается {{payout_date}}      │
│                                        │
│ [Подробнее по дням]                    │
│                                        │
│ ── История ──                          │
│                                        │
│ {{prev_cycle_dates}}        18 200 ₽    │
│ ✓ Получено {{date}}                     │
│                                        │
│ {{prev_cycle_dates}}        15 800 ₽    │
│ ⚠ Спор (вы создали)                     │
│                                        │
│ [Полная история]                       │
│                                        │
│ ── ──                                   │
│                                        │
│ [⚙ Условия и комиссия]                  │
│ [📤 Экспорт для налоговой]              │
└────────────────────────────────────────┘
```

### 6.2 Per-day detail

Tap «Подробнее по дням»:

```
┌────────────────────────────────────────┐
│ ← Доход по дням                         │
├────────────────────────────────────────┤
│ ── 19 мая, понедельник ──               │
│                                        │
│ 10:00  Мария И.  Маникюр                │
│        услуга  1 100 ₽ + чай  200 ₽    │
│                                        │
│ 12:00  Олег П.  Стрижка                  │
│        услуга  900 ₽                    │
│                                        │
│ 15:30  Анна С.  Окрашивание              │
│        услуга  3 200 ₽                  │
│                                        │
│ День: 5 400 ₽                           │
│                                        │
│ ── 20 мая ──                            │
│ ...                                    │
└────────────────────────────────────────┘
```

Tap any row → §6.3 per-booking detail.

### 6.3 Per-booking earnings detail

```
┌────────────────────────────────────────┐
│ ← Запись                                │
├────────────────────────────────────────┤
│ Клиент:    Мария Иванова                │
│ Дата:      19 мая, 10:00                │
│ Услуга:    Маникюр                       │
│ Цена:      2 500 ₽                       │
│ Моя доля:  44% × 2 500 = 1 100 ₽         │
│ Чаевые:    200 ₽                         │
│ Итого:     1 300 ₽                       │
│                                        │
│ Статус:    Закрыто, готово к выплате    │
│                                        │
│ ── Расчёт ──                            │
│ Базовая ставка:   44%                   │
│ Применена с:      14 марта 2026          │
│                                        │
│ Что-то не так?                          │
│ [⚠ Не согласна с суммой]                │
└────────────────────────────────────────┘
```

«Не согласна с суммой» → §9 dispute flow.

### 6.4 Commission rules screen

```
┌────────────────────────────────────────┐
│ ← Условия и комиссия                    │
├────────────────────────────────────────┤
│ Модель: Самозанятая                     │
│                                        │
│ ── Моя доля по услугам ──              │
│                                        │
│ Маникюр                          44%    │
│ Педикюр                          44%    │
│ Окрашивание                      40%    │
│ Стрижка                          50%    │
│ Прочее (по умолчанию)            44%    │
│                                        │
│ ── Чаевые ──                            │
│ Я получаю                       100%    │
│                                        │
│ ── Цикл выплат ──                       │
│ Каждые 2 недели, в понедельник         │
│ Способ: на карту                        │
│                                        │
│ ── ──                                   │
│ Условия установил(а) {{salon_owner}}.   │
│ Хотите обсудить — напишите в чат.       │
│ [💬 Написать {{salon_owner}}]           │
└────────────────────────────────────────┘
```

«Написать {{salon_owner}}» → opens internal-admin-chat scope (Topic 6 of master UX backlog — future doc).

### 6.5 Tax export

```
┌────────────────────────────────────────┐
│ ← Экспорт для налоговой                 │
├────────────────────────────────────────┤
│ Период:                                 │
│ ⦿ Текущий месяц                         │
│ ◯ Текущий квартал                       │
│ ◯ Текущий год                           │
│ ◯ Произвольный диапазон                 │
│                                        │
│ Формат:                                 │
│ ⦿ PDF (читать глазами)                  │
│ ◯ CSV (загружать в программу)          │
│ ◯ JSON (для разработчика)              │
│                                        │
│ В файле:                                │
│ ✓ Только мои данные                     │
│ ✓ Без данных клиентов                   │
│   (только инициалы + время)            │
│                                        │
│ [Сформировать]                          │
└────────────────────────────────────────┘
```

Generated file:
- PDF: human-readable, with salon name, period, totals
- CSV: structured, columns: date, time, service, master_amount, tip_amount, status
- JSON: full structure for any tool

PII rule: customer names → initials only «М.И.»; phones never; dates intact.

### 6.6 Multi-tenant master view

If master works at 2+ salons, tab top has selector:

```
┌────────────────────────────────────────┐
│ 💰 Доход [Салон Натали ▾]              │
├────────────────────────────────────────┤
│  ...                                    │
└────────────────────────────────────────┘
```

Selector switches ledger. Each salon = separate everything. Cross-salon aggregate only in §6.5 export if master explicitly selects «все салоны».

---

## 7. Tip flow

### 7.1 Tenant config (admin one-time, at setup)

Per §2.7 — 3 modes. Admin chooses at salon setup. Locked for tenant; change requires founder approval (audit).

### 7.2 Customer-side tip prompt — Bot DM after booking completed

Per [`customer-cancellation-reschedule-spec.md`](../policies/customer-cancellation-reschedule-spec.md) pattern, after customer leaves salon (booking COMPLETED + 1h delay):

```
Спасибо, что были у нас! {{customer_first_name}}, надеюсь, всё понравилось.

{% if mode == 'platform_passthrough' %}
Если хотите оставить чаевые {{master_first_name}}, могу принять через приложение —
суммы любые, безналом.
[100 ₽]  [200 ₽]  [500 ₽]  [Другая]  [Не сейчас]

{% elif mode == 'external' %}
Если хотите оставить чаевые {{master_first_name}}, лучше прямо ей на смене или
переводом — {{master}} подскажет реквизиты, если попросите.
[Подскажете реквизиты?]  [Не сейчас]
{% endif %}
```

### 7.3 Customer-side Mini App «Visit recap» screen

After visit, customer's Mini App has «Recent visit» card with «Оставить чаевые» button (when tenant has tip enabled). Same 3 modes.

### 7.4 Tip allocation rules

Default: master receives 100%. Admin can configure split (advanced, rarely used):
- Front desk receives N%
- Admin/owner receives N%
- Master receives remainder

If split is active, master Mini App shows their net amount + «Полная сумма: X ₽ (разделено по правилам салона)» without revealing other recipients.

### 7.5 Tip thank-you receipt

Master can tap reaction (emoji) on tip notification. AI relays to customer (light, not over-effusive):

```
{{master_first_name}} ответила: 🙏 Спасибо!
```

Avoid pressure («say thanks back»). Optional, customer can ignore.

### 7.6 Tip dispute

If customer disputes («I tipped X but my charge shows Y»):
- Same dispute model §9 but tagged `dispute_type='tip'`
- Master notified passively §5.20

---

## 8. Payout cycle

### 8.1 Cycle definitions

| Cycle | Window | Payout day |
|---|---|---|
| weekly | Mon-Sun | Following Monday |
| bi_weekly | Mon-Sun × 2 | Following Monday |
| monthly_first | Calendar month | 1st of next month |
| monthly_last | Calendar month | Last day of month |
| custom | Admin-defined N days | Admin-defined offset |

### 8.2 State machine per payout

```
[ACCUMULATING] (during cycle)
       ↓
[CYCLE_CLOSED] (cycle end timestamp passed; admin can review)
       ↓
[ADMIN_REVIEWING] (admin manually triggers; SLA: review within 3 business days)
       ↓
[PAYOUT_SENT] (admin marks sent, supplies meta: method, ref)
       ↓
[MASTER_CONFIRMED] (master clicks «получено»)
       ↓
[CLOSED]

Alternative branches:
[PAYOUT_SENT] → [DISPUTED] → ...resolution...→ [CLOSED|REOPENED|ESCALATED_FOUNDER]
[CYCLE_CLOSED] + 7d no admin action → ALERT to admin
[PAYOUT_SENT] + 7d no master action → AUTO_CONFIRMED (audit)
```

### 8.3 Admin payout flow (Mini App)

```
┌────────────────────────────────────────┐
│ ← Цикл выплат 6-19 мая                  │
├────────────────────────────────────────┤
│ К выплате:                              │
│                                        │
│ Анна (мастер маникюра)        13 600 ₽  │
│ Лена (мастер стрижки)         18 200 ₽  │
│ Марина (мастер педикюра)      11 400 ₽  │
│ ──────────────────────────────────────  │
│ Итого                         43 200 ₽  │
│                                        │
│ [Открыть детали по каждой]              │
│                                        │
│ Когда выплатили — отметьте:             │
│                                        │
│ Анна                                    │
│ [✓ Отметить «выплатила»]               │
│ Способ: [на карту ▾]                    │
│ Сумма:  [13 600 ₽]                      │
│                                        │
│ ...                                    │
└────────────────────────────────────────┘
```

Admin can bulk-mark «все выплатила» if all sent same time.

### 8.4 Master confirmation

After admin marks → master Bot DM §5.19. Master clicks «✓ Да» — close.

If master clicks «✗ Нет, не пришло» — instant dispute open §9 with type `payout_not_received`.

If master clicks «⚠ Сумма другая» — input request:

```
А какая сумма у вас на руках?
[ввод поля] ₽

Мы попросим {{salon_owner}} разобраться.
```

→ dispute open with `dispute_type='amount_mismatch'`, expected_amount, claimed_amount.

### 8.5 Cycle preview during cycle

Master sees current cycle accumulating in §6.1. Updates real-time as bookings close.

### 8.6 Pause / SUSPENDED tenant

Per [`tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md): if tenant SUSPENDED mid-cycle, accumulated earnings preserved + read-only. Master sees «Цикл приостановлен — выплата после восстановления салона». Founder must approve unusual payout flows on SUSPENDED tenants (rare).

---

## 9. Dispute flow

### 9.1 Dispute types

| Type | Trigger | Severity default |
|---|---|---|
| `amount_per_booking` | Master flags per-booking amount wrong (§6.3) | MEDIUM |
| `payout_not_received` | Master says payout didn't arrive (§5.19) | HIGH |
| `amount_mismatch` | Master got different amount than expected | HIGH |
| `missing_booking` | Master worked but booking not in app | HIGH |
| `commission_rate` | Master disputes commission % applied | MEDIUM |
| `tip` | Master OR customer disputes tip amount | LOW |
| `phantom_booking` | App shows booking but master didn't work it | MEDIUM |

### 9.2 Dispute lifecycle

```
[OPENED by master] → [ADMIN_REVIEWING] (SLA: 48h) → [RESOLVED_ADMIN | ESCALATED_FOUNDER]
                                                              ↓
                                                  [RESOLVED_FOUNDER] (SLA: 7d)

Branches:
[ADMIN_REVIEWING] + admin response → [MASTER_REVIEW] (master accepts/rejects)
[MASTER_REVIEW] + master rejects → [ESCALATED_FOUNDER]
[MASTER_REVIEW] + master accepts → [RESOLVED]
[ADMIN_REVIEWING] + 48h no admin action → AUTO_ESCALATE_FOUNDER
```

### 9.3 Master raises dispute UX

From per-booking detail §6.3 «Не согласна с суммой»:

```
┌────────────────────────────────────────┐
│ ← Спор по сумме                         │
├────────────────────────────────────────┤
│ Запись: Мария И., 19 мая 10:00          │
│ В приложении: 1 100 ₽                   │
│                                        │
│ Сколько должно быть, по-вашему?         │
│ [ввод поля] ₽                           │
│                                        │
│ Почему (необязательно):                 │
│ [_____________________________]         │
│                                        │
│ {{salon_owner}} увидит ваш запрос и     │
│ ответит в течение 48 часов.             │
│                                        │
│ [Отправить]   [Отмена]                  │
└────────────────────────────────────────┘
```

### 9.4 Admin reviews

Admin Mini App «Споры по доходу» tab (separate from booking conflicts):

```
┌────────────────────────────────────────┐
│ 🔧 Споры по доходу (2)                  │
├────────────────────────────────────────┤
│ ⚠ HIGH  Анна — не пришла выплата        │
│        Цикл 6-19 мая, 13 600 ₽          │
│        SLA: 14 часов из 48              │
│        [Разобрать]                       │
│                                        │
│ 🟡 MED  Лена — сумма за окрашивание     │
│        ожидала 1 280, в приложении 1 100│
│        SLA: 32 часа из 48               │
│        [Разобрать]                       │
└────────────────────────────────────────┘
```

«Разобрать» → dispute resolution screen:
- View facts (master's claim, app's record)
- Admin response choices: accept master's claim / counter-propose / deny
- Auto-adjusts earnings ledger if accepted; master notified

### 9.5 Master review response

After admin responds:

```
┌────────────────────────────────────────┐
│ ← Ответ от {{salon_owner}}              │
├────────────────────────────────────────┤
│ Ваш спор: 1 100 ₽ за маникюр (вы        │
│ ожидали 1 280 ₽)                        │
│                                        │
│ {{salon_owner}} ответила:               │
│ ◯ Я согласна, поправим — 1 280 ₽       │
│                                        │
│ Комментарий: «Извини, я неверно         │
│ применила старую ставку, спасибо что    │
│ обратила внимание.»                     │
│                                        │
│ [✓ Принять]   [✗ Не согласна]           │
└────────────────────────────────────────┘
```

«Принять» → close. «Не согласна» → escalate to founder.

### 9.6 AI passive narration of dispute

AI doesn't lead dispute conversation — admin does. But AI relays neutral status updates:

```
{{master_first_name}}, {{salon_owner}} ответила на ваш спор. Зайдите в приложение,
посмотрите.
[Открыть]
```

NEVER takes sides:
- ❌ «Salon owner says you're wrong»
- ❌ «You're right, owner is mistaken»
- ✅ «Ответ готов — посмотрите»

### 9.7 Founder escalation

Per Q-ME9: if not resolved or master rejects admin response, founder is escalated. Founder sees both sides, full audit, can:
- Side with master (force adjustment + notify admin)
- Side with admin (close + notify master)
- Mediate (propose split)

Founder decisions logged immutably. Master and admin both see resolution.

---

## 10. Tax export

### 10.1 Self-service per §2.9

Master taps §6.5 «Экспорт для налоговой» — generates immediately.

### 10.2 Format specs

**PDF:**
- Header: salon name, master name, period, model (samozanyatyy / employee / IP)
- Table per day: date, # bookings, services subtotal, tips subtotal, total
- Footer: «Этот документ сформирован {{date}} приложением. Не является официальным платёжным документом.»
- Signature: salon name (NOT signed — informational)

**CSV columns:**
`date,time,service,customer_initials,service_price,master_share_percent,master_share_amount,tip_amount,total,status,booking_id`

**JSON:** full structure per booking + meta.

### 10.3 PII in export

- Customer first name + first letter of last → initials only («М.И.»)
- Customer phone NEVER
- Customer email NEVER
- Booking IDs (UUID OK for cross-reference)

### 10.4 Future: «Мой налог» integration

Phase 4+ direct API to register income receipts. Q-ME10 captures regulatory cycle.

---

## 11. Data models

### 11.1 `MasterCompensationProfile`

One per master per tenant.

```python
class MasterCompensationProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='compensation_profiles')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    MODEL_CHOICES = [
        ('salaried_employee', 'Salaried Employee'),
        ('samozanyatyy', 'Самозанятый'),
        ('ip', 'IP'),
    ]
    compensation_model = models.CharField(max_length=32, choices=MODEL_CHOICES, default='samozanyatyy')

    default_commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=44.00)
    # % of service price master receives

    tip_percent_to_master = models.DecimalField(max_digits=5, decimal_places=2, default=100.00)

    PAYOUT_CYCLE_CHOICES = [
        ('weekly', 'Weekly (Mon-Sun, payout following Mon)'),
        ('bi_weekly', 'Bi-weekly'),
        ('monthly_first', 'Monthly (paid 1st)'),
        ('monthly_last', 'Monthly (paid last day)'),
        ('custom', 'Custom'),
    ]
    payout_cycle = models.CharField(max_length=32, choices=PAYOUT_CYCLE_CHOICES, default='bi_weekly')

    custom_cycle_days = models.IntegerField(null=True, blank=True)
    custom_cycle_offset_days = models.IntegerField(null=True, blank=True)

    PAYOUT_METHOD_CHOICES = [
        ('bank_card', 'На карту'),
        ('cash', 'Наличные'),
        ('yandex_pay', 'Yandex Pay'),
        ('tinkoff', 'Тинькофф перевод'),
        ('sberbank', 'Сбербанк перевод'),
        ('other', 'Другое'),
    ]
    payout_method = models.CharField(max_length=32, choices=PAYOUT_METHOD_CHOICES, default='bank_card')

    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)
    # Per §2.6, rule changes create new profile; old profile gets effective_until

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            Index(fields=['master', 'tenant', '-effective_from']),
        ]
        constraints = [
            CheckConstraint(
                check=Q(effective_until__isnull=True) | Q(effective_until__gte=F('effective_from')),
                name='ck_effective_until_after_from',
            ),
        ]
```

### 11.2 `ServiceCommissionOverride`

Per service category override (e.g., okrashivanie 40% even if default 44%).

```python
class ServiceCommissionOverride(models.Model):
    profile = models.ForeignKey(MasterCompensationProfile, on_delete=CASCADE, related_name='service_overrides')
    service_category = models.CharField(max_length=128)
    # Or per-service: service = ForeignKey('catalog.Service', ...)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2)
```

### 11.3 `MasterEarning`

Computed per completed booking. Extended per [`customer-no-show-policy-ux §7.2`](../policies/customer-no-show-policy-ux.md) Q-NS11 resolution — adds `event_type` discriminator + `no_show_coverage_percent` field + idempotency unique constraint.

```python
class MasterEarning(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='earnings')
    booking = models.OneToOneField('booking.Booking', on_delete=CASCADE, related_name='master_earning')

    EVENT_TYPE_CHOICES = [
        ('regular_visit', 'Regular completed visit'),
        ('no_show_payout', 'No-show salon-side coverage (Q-NS11)'),
        ('refund_revoke', 'Refund claw-back adjustment'),
        ('manual_adjust', 'Manual admin adjustment'),
    ]
    event_type = models.CharField(max_length=32, choices=EVENT_TYPE_CHOICES, default='regular_visit')

    profile_snapshot_id = models.UUIDField()
    # MasterCompensationProfile.id at time of booking — frozen per §2.6

    service_price = models.DecimalField(max_digits=10, decimal_places=2)
    commission_percent_applied = models.DecimalField(max_digits=5, decimal_places=2)
    master_share = models.DecimalField(max_digits=10, decimal_places=2)
    # = service_price * commission_percent_applied / 100

    # Q-NS11 — populated for event_type='no_show_payout' per
    # customer-no-show-policy §7.2. 0/50/100 mapping from tenant policy mode.
    # For other event types, defaults to 100 (no reduction applied).
    no_show_coverage_percent = models.IntegerField(default=100)

    tip_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tip_percent_to_master = models.DecimalField(max_digits=5, decimal_places=2, default=100.00)
    tip_master_share = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    total_master_amount = models.DecimalField(max_digits=10, decimal_places=2)
    # For regular_visit: master_share + tip_master_share
    # For no_show_payout:
    #   service_price × commission_percent_applied × no_show_coverage_percent / 10000
    #   (tip fields always 0 for no-show — customer wasn't present)

    booking_completed_at = models.DateTimeField()
    # For no_show_payout: this is booking.slot_start (when service WOULD have happened)
    cycle_id = models.ForeignKey('PayoutCycle', null=True, on_delete=SET_NULL, related_name='earnings')
    # Assigned when cycle closes

    STATUS_CHOICES = [
        ('accumulating', 'In current cycle'),
        ('in_cycle', 'Assigned to closed cycle'),
        ('paid', 'Paid out'),
        ('disputed', 'In dispute'),
        ('adjusted', 'Adjusted post-dispute'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='accumulating')

    adjustment_history = models.JSONField(default=list)
    # [{ 'at': '...', 'from': X, 'to': Y, 'reason': '...', 'dispute_id': '...' }]

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            Index(fields=['master', 'tenant', 'booking_completed_at']),
            Index(fields=['cycle_id', 'status']),
            Index(fields=['event_type', 'booking']),  # No-show payout lookup
        ]
        constraints = [
            # Q-NS11 idempotency: one earning row per (booking, event_type).
            # Prevents double-credit on subscriber retries (regular_visit OR
            # no_show_payout OR refund_revoke each get exactly one row per booking).
            UniqueConstraint(
                fields=['master', 'booking', 'event_type'],
                name='master_earning_unique_per_booking_event',
            ),
        ]
```

**Migration note:** existing rows backfill `event_type='regular_visit'` + `no_show_coverage_percent=100`. Unique constraint added in same migration; pre-check no duplicates exist (one-to-one booking ↔ earning is the pre-Q-NS11 invariant).

### 11.4 `PayoutCycle`

```python
class PayoutCycle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='payout_cycles')
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='payout_cycles')

    cycle_start = models.DateField()
    cycle_end = models.DateField()
    expected_payout_date = models.DateField()

    STATUS_CHOICES = [
        ('accumulating', 'Accumulating'),
        ('cycle_closed', 'Cycle closed, awaiting admin review'),
        ('admin_reviewing', 'Admin reviewing'),
        ('payout_sent', 'Payout sent'),
        ('master_confirmed', 'Master confirmed receipt'),
        ('closed', 'Closed'),
        ('disputed', 'In dispute'),
        ('paused_tenant_suspended', 'Paused — tenant suspended'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='accumulating')

    services_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tips_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    booking_count = models.IntegerField(default=0)

    admin_payout_sent_at = models.DateTimeField(null=True, blank=True)
    admin_payout_sent_by = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    admin_payout_method_used = models.CharField(max_length=32, blank=True, default='')
    admin_payout_reference = models.CharField(max_length=128, blank=True, default='')
    # Transaction reference, optional

    master_confirmed_at = models.DateTimeField(null=True, blank=True)
    auto_confirmed_at = models.DateTimeField(null=True, blank=True)

    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            Index(fields=['master', 'tenant', '-cycle_end']),
            Index(fields=['tenant', 'status']),
            Index(fields=['expected_payout_date']),
        ]
```

### 11.5 `Tip`

```python
class Tip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    booking = models.ForeignKey('booking.Booking', on_delete=CASCADE, related_name='tips')
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='+')
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='tips_received')

    amount_total = models.DecimalField(max_digits=10, decimal_places=2)
    amount_to_master = models.DecimalField(max_digits=10, decimal_places=2)

    MODE_CHOICES = [
        ('external', 'Salon receives, masters pays through their channel'),
        ('platform_passthrough', 'Customer paid via platform'),
    ]
    mode = models.CharField(max_length=32, choices=MODE_CHOICES)

    STATUS_CHOICES = [
        ('intended', 'Customer expressed intent (external mode)'),
        ('captured', 'Payment captured (passthrough)'),
        ('failed', 'Payment failed'),
        ('refunded', 'Refunded'),
        ('disputed', 'In dispute'),
        ('closed', 'Closed'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES)

    payment_provider = models.CharField(max_length=64, blank=True, default='')
    # 'yookassa', 'qr', 'cash_at_salon' etc.
    payment_reference = models.CharField(max_length=128, blank=True, default='')

    captured_at = models.DateTimeField(null=True, blank=True)
    master_acknowledged_at = models.DateTimeField(null=True, blank=True)
    # When master tapped reaction

    customer_thank_you_relayed = models.BooleanField(default=False)

    class Meta:
        indexes = [
            Index(fields=['master', 'tenant', '-captured_at']),
            Index(fields=['booking']),
        ]
```

### 11.6 `EarningDispute`

```python
class EarningDispute(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='earning_disputes')

    DISPUTE_TYPE_CHOICES = [
        ('amount_per_booking', 'Amount per booking'),
        ('payout_not_received', 'Payout not received'),
        ('amount_mismatch', 'Payout amount mismatch'),
        ('missing_booking', 'Missing booking'),
        ('commission_rate', 'Commission rate applied'),
        ('tip', 'Tip amount'),
        ('phantom_booking', 'Phantom booking master didn’t work'),
    ]
    dispute_type = models.CharField(max_length=32, choices=DISPUTE_TYPE_CHOICES)

    SEVERITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)

    # Optional references
    booking = models.ForeignKey('booking.Booking', null=True, on_delete=SET_NULL, related_name='+')
    cycle = models.ForeignKey(PayoutCycle, null=True, on_delete=SET_NULL, related_name='disputes')
    earning = models.ForeignKey(MasterEarning, null=True, on_delete=SET_NULL, related_name='disputes')
    tip = models.ForeignKey(Tip, null=True, on_delete=SET_NULL, related_name='disputes')

    expected_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    claimed_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    master_comment = models.TextField(blank=True, default='', max_length=500)

    STATUS_CHOICES = [
        ('opened', 'Opened by master'),
        ('admin_reviewing', 'Admin reviewing'),
        ('master_review', 'Master reviewing admin response'),
        ('resolved_admin', 'Resolved (admin)'),
        ('resolved_founder', 'Resolved (founder escalation)'),
        ('master_rejected', 'Master rejected admin response — auto-escalating'),
        ('auto_escalated', 'Auto-escalated due to SLA breach'),
        ('closed', 'Closed'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='opened')

    admin_response_text = models.TextField(blank=True, default='', max_length=1000)
    admin_response_at = models.DateTimeField(null=True, blank=True)
    admin_responding_user = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')

    resolution_action = models.CharField(max_length=64, blank=True, default='')
    # 'accept_master', 'counter_propose', 'deny', 'founder_force_adjust', ...
    resolution_metadata = models.JSONField(default=dict)

    opened_at = models.DateTimeField(auto_now_add=True)
    sla_due_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            Index(fields=['tenant', 'status', '-opened_at']),
            Index(fields=['master', '-opened_at']),
            Index(fields=['sla_due_at']),  # SLA scanner
        ]
```

### 11.7 `EarningsExport`

Audit row per master-triggered export.

```python
class EarningsExport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='+')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    period_start = models.DateField()
    period_end = models.DateField()
    format = models.CharField(max_length=8)  # pdf / csv / json
    generated_at = models.DateTimeField(auto_now_add=True)
    file_sha256 = models.CharField(max_length=64)
    # File retention: ephemeral, deleted after 24h; sha kept for audit
```

---

## 12. API contracts

### 12.1 Master endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/master/earnings/current-cycle` | Active cycle summary §6.1 |
| GET | `/api/v1/master/earnings/cycles` | History list |
| GET | `/api/v1/master/earnings/cycles/<id>` | Per-cycle detail |
| GET | `/api/v1/master/earnings/cycles/<id>/days` | Per-day breakdown §6.2 |
| GET | `/api/v1/master/earnings/booking/<id>` | Per-booking detail §6.3 |
| GET | `/api/v1/master/earnings/compensation-rules` | Active rules §6.4 |
| POST | `/api/v1/master/earnings/disputes` | Open new dispute §9.3 |
| GET | `/api/v1/master/earnings/disputes` | List own disputes |
| GET | `/api/v1/master/earnings/disputes/<id>` | Dispute detail |
| POST | `/api/v1/master/earnings/disputes/<id>/master-review` | Accept/reject admin response §9.5 |
| POST | `/api/v1/master/earnings/cycles/<id>/confirm-received` | Confirm payout received §8.4 |
| POST | `/api/v1/master/earnings/export` | Generate export §6.5 / §10 |
| GET | `/api/v1/master/earnings/export/<id>/download` | Download file (24h TTL) |

### 12.2 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/earnings/cycles` | All masters' cycles in tenant |
| POST | `/api/v1/admin/earnings/cycles/<id>/mark-sent` | Mark payout sent §8.3 |
| GET | `/api/v1/admin/earnings/disputes` | All open disputes |
| POST | `/api/v1/admin/earnings/disputes/<id>/respond` | Respond to dispute §9.4 |
| POST | `/api/v1/admin/earnings/disputes/<id>/escalate` | Manual escalate to founder |
| GET | `/api/v1/admin/earnings/compensation-profiles` | List per-master profiles |
| PUT | `/api/v1/admin/earnings/compensation-profiles/<master_id>` | Update master's profile (creates new effective-dated row) |

### 12.3 Founder endpoints (Phase 3+)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/earnings/disputes/escalated` | Cross-tenant escalated disputes |
| POST | `/api/v1/founder/earnings/disputes/<id>/resolve` | Founder final resolution §9.7 |

### 12.4 Internal sync

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/earnings/booking-completed/<booking_id>` | Trigger MasterEarning calc |
| POST | `/internal/earnings/cycle-close/<cycle_id>` | Cron-triggered cycle close |

### 12.5 Sample request: open dispute

POST `/api/v1/master/earnings/disputes`:

```json
{
  "dispute_type": "amount_per_booking",
  "booking_id": "uuid",
  "expected_amount": 1280.00,
  "claimed_amount": 1100.00,
  "master_comment": "По окрашиванию у меня ставка 40%, а в приложении применилась дефолтная"
}
```

**Validation:**
- Master must own (be assigned to) the booking
- `expected_amount` > 0
- `master_comment` ≤ 500 chars
- Existing OPEN dispute for same booking → 409 with reference

**Response (201):**
```json
{
  "dispute_id": "uuid",
  "status": "opened",
  "sla_due_at": "2026-05-21T10:00:00Z",
  "expected_admin_response": "within 48 hours"
}
```

---

## 13. Events emitted

Per [`event-taxonomy.md`](../policies/event-taxonomy.md): add 12 NEW to section `3.7 earnings domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| Booking COMPLETED → earning computed | NEW: `earning.computed` | `master_id`, `amount`, `commission_percent_applied`, `event_type='regular_visit'` |
| Booking confirmed NO_SHOW → no-show payout credited | NEW: `earning.no_show_payout_credited` | `master_id`, `amount`, `no_show_coverage_percent`, `tenant_policy_mode` (Q-NS11 §7.2) |
| Tip captured | NEW: `tip.captured` | `mode`, `amount_to_master` |
| Tip intent recorded (external mode) | NEW: `tip.intended` | `mode='external'` |
| Master acknowledges tip | NEW: `tip.master_acknowledged` | |
| Cycle closes | NEW: `payout_cycle.closed` | total, booking_count |
| Admin marks payout sent | NEW: `payout.sent_by_admin` | method, reference |
| Master confirms receipt | NEW: `payout.master_confirmed` | |
| Auto-confirm (7d no master action) | NEW: `payout.auto_confirmed` | |
| Dispute opened | NEW: `earning_dispute.opened` | type, severity |
| Dispute admin response | NEW: `earning_dispute.admin_responded` | action |
| Dispute auto-escalated (SLA breach) | NEW: `earning_dispute.sla_breached` | |
| Dispute resolved (any level) | NEW: `earning_dispute.resolved` | resolver, action |
| Earnings export generated | NEW: `earnings.exported` | format, period |
| Compensation profile changed | NEW: `compensation_profile.updated` | what fields, audit |

12 NEW events to event-taxonomy §3.7.

---

## 14. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Streak counter for earning growth | Gamification §2.2 | NO streaks |
| Cross-master leaderboard | Privacy + shame §2.3 | NEVER |
| Confetti on payout | Trivializes work | Plain confirmation |
| «Goal: earn 50k» targets | Pressure | NO targets |
| Show salon-to-platform commission to master | Privacy §2.10 | NEVER expose |
| Allow mid-cycle commission change to affect closed earnings | Trust violation §2.6 | Effective-dated profile |
| Lock master out of earnings dashboard | Trust foundation | Always accessible |
| Allow admin to delete `MasterEarning` row | Audit gap | Soft adjust + audit trail in §11.3 |
| Show «pending» tip amount as «earned» | Mismatch reality | Status distinguishes intended vs captured |
| Export contains customer PII (phone/email/full name) | Privacy + tax-export misuse risk | Initials only §10.3 |
| Bot DM says «commission earned» (cold/sales) | Voice violation §2.4 | Natural framing §5.16 |
| Auto-resolve dispute against master without master response | Power imbalance | Master review §9.5 |
| Skip founder escalation when admin doesn't respond | SLA fail | Auto-escalate after 48h §8.2 |
| Generate export but no audit row | Compliance gap | `EarningsExport` row §11.7 |
| Show «expected» tip amount before customer paid | False promise | Only show captured/intended distinction §11.5 |
| Allow admin to «mark received» on master's behalf | Trust violation | Only master clicks §8.4 |
| Multi-tenant master sees cross-tenant aggregate by default | Privacy | Per-tenant default; cross only explicit per §6.5 |

---

## 15. Acceptance criteria (engineering checklist)

- [ ] 7 models §11 (MasterCompensationProfile, ServiceCommissionOverride, MasterEarning, PayoutCycle, Tip, EarningDispute, EarningsExport)
- [ ] Migration applies cleanly
- [ ] 14 master endpoints §12.1 + 7 admin §12.2 + 2 founder §12.3 + 2 internal §12.4
- [ ] Permissions: master sees own data only; admin tenant-scoped; founder cross-tenant scoped
- [ ] MasterEarning computed on booking COMPLETED with effective-dated profile snapshot §11.3
- [ ] Cycle closer worker (cron per cycle config)
- [ ] Auto-escalation worker (SLA breach scanner) §8.2 / §9.2
- [ ] Auto-confirm worker (7d no master action) §8.2
- [ ] 4 NEW Bot DM templates §5.16-5.20
- [ ] Mini App tab «Доход» 5 screens §6.1-6.5
- [ ] Multi-tenant master selector §6.6
- [ ] Admin payout dashboard §8.3
- [ ] Admin dispute dashboard §9.4
- [ ] Founder escalation tab §9.7
- [ ] Tip flow 3 modes §7
- [ ] Tip thank-you receipt §7.5
- [ ] Tax export PDF/CSV/JSON §10 + audit §11.7
- [ ] PII rules in export §10.3
- [ ] 12 events emitted §13
- [ ] Anti-pattern review §14 — NO streaks/leaderboards/targets in any surface
- [ ] Tenant SUSPENDED state pauses cycles correctly §8.6
- [ ] Tests: per-compensation-model calc / dispute lifecycle / SLA breach / multi-tenant master / 3 tip modes / tax export PII / cross-master 403 / commission rule change effective-dating
- [ ] Documentation in `apps/earnings/README.md` referencing this handoff

---

## 16. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-ME1** | Tip modes — all 3 supported MVP or start with EXTERNAL only? | EXTERNAL + DISABLED MVP. PLATFORM_PASSTHROUGH Phase 3 (needs payment provider integration + KKT considerations). | PM + Eng | 🟡 |
| **Q-ME2** | Auto-confirm receipt after 7d — is 7d right? | 7d MVP. If master frequently absent and missing confirmations causing audit pain, shorten to 5d. | Policy | 🟢 |
| **Q-ME3** | Dispute SLA — 48h admin / 7d founder per §9.2 — tight enough? | 48h aligns with conflict-resolution-ux. 7d founder is generous; reduce to 5 if founder bandwidth allows. | Policy | 🟢 |
| **Q-ME4** | Multi-tenant master — same compensation profile editable independently per tenant? | YES — each tenant owns master's profile in their tenant. Master sees both, can't edit (admin sets). | Eng | 🟢 |
| **Q-ME5** | Cash payouts — how to handle KKT compliance? | Out of scope MVP. Salon handles KKT. App records meta only (method='cash'). Phase 4+ KKT integration TBD. | Compliance | 🟡 |
| **Q-ME6** | Russian samozanyatyy «Мой налог» receipt issuance — auto or manual? | Manual MVP (master issues receipt outside platform). Phase 4+ integrate with «Мой налог» API for auto receipt on tip/service payment. | Compliance | 🟡 |
| **Q-ME7** | Commission % decimals — 2 (44.00%) or 4 (44.5000%)? | 2 — beauty industry doesn't typically use sub-percent precision. Round always. | PM | 🟢 |
| **Q-ME8** | Tip thank-you reaction — emoji-only or also free text? | Emoji-only MVP (low friction, no PII risk). Free text Phase 3 if requested. | UX | 🟢 |
| **Q-ME9** | Founder dispute resolution force-adjust — audit-only or also notify admin to recalibrate process? | Both — adjustment applies + admin notified «founder reviewed, suggested fix to your process». Founder optional comment. | Policy | 🔴 PRE-DEPLOY |
| **Q-ME10** | «Мой налог» integration scope — only IP/samozanyatyy or also employee taxes (НДФЛ)? | Samozanyatyy only Phase 4+. NDFL stays salon's responsibility. | Compliance | 🟡 |
| **Q-ME11** | Master can edit own service-commission override? | NO — admin sets. Master sees + disputes. Otherwise master can self-promote rate. | Policy | 🟢 |
| **Q-ME12** | Cycle closure when booking is STATUS_DIVERGENCE mid-cycle (per booking-conflict-resolution-ux) | Booking remains «accumulating» until conflict resolved. If unresolved at cycle end, cycle closes WITHOUT that booking; booking goes to next cycle once resolved. | Policy | 🟡 |
| **Q-ME13** | Master payout method override per cycle — possible? | NO MVP. Master's payout_method is fixed per profile. Cycle uses profile's method. Edge case (one-off) — admin marks manually with override comment. | Policy | 🟢 |
| **Q-ME14** | Negative earnings (refund applied) — display? | YES — refund creates `MasterEarning` adjustment row with negative amount. Cycle reflects net. Master sees clear «возврат -1100 ₽» line. | Policy + UX | 🟡 |
| **Q-ME15** | Export retention — 24h is fine? | 24h ephemeral file MVP. If support cases need older — regenerate from data (it's idempotent). Audit row §11.7 keeps record of generation. | Privacy + Eng | 🟢 |
| **Q-ME16** | Commission % change notification to master — Bot DM or silent? | Bot DM «{{admin}} обновила вашу комиссию: маникюр теперь 50% (с {{date}})». Master can dispute via §9 if didn't agree. | UX | 🟡 |
| **Q-ME17** | Master who works at 2 salons gets payout from both same day — separate confirmations or combined? | Separate. Each tenant has separate ledger; master clicks «получено» per tenant. | UX | 🟢 |
| **Q-ME18** | Bonus / extra payment from admin to master (not tied to booking) — supported? | YES — admin can add `MasterEarning` with `booking=null` + `adjustment_history` reason. Master sees in cycle as «Бонус от {{admin}}». | Policy + Eng | 🟡 |
| **Q-ME19** | Tip refund (customer disputes via §7.6) — affects already-paid master? | If tip refunded BEFORE cycle closes: subtracted from cycle. If AFTER payout: adjustment in next cycle (master sees clear deduction line). NEVER claw-back from master's bank. | Policy | 🔴 PRE-DEPLOY |
| **Q-ME20** | Customer can see master's tip total (after they tip)? | NO — privacy. Customer sees only own tip. Master sees own total. | Privacy | 🟢 |

---

## 17. Cross-document linkage

- [`master-conversational-templates.md §5`](../policies/master-conversational-templates.md) — 4 new touchpoints §5.16-5.20
- [`master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md) — new tab «Доход» added to bottom nav
- [`master-onboarding-m0-m7.md M2`](../policies/master-onboarding-m0-m7.md) — compensation profile setup added
- [`attribution-policy.md`](../policies/attribution-policy.md) — master earnings tracked independently from booking_source attribution; both audit streams coexist
- [`booking-conflict-resolution-ux.md`](../policies/booking-conflict-resolution-ux.md) — dispute audit pattern §10 mirrors; Q-ME12 cycle interaction
- [`single-assistant-identity.md`](../policies/single-assistant-identity.md) — §2.4 voice preserved
- [`tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md) — §8.6 cycle pause
- [`event-taxonomy.md §3.7`](../policies/event-taxonomy.md) — 12 NEW events §13
- [`contract-offer-acceptance-display-ux.md`](../policies/contract-offer-acceptance-display-ux.md) — §2.10 platform-to-salon contract is salon's matter; master sees only salon-to-master
- [`../decisions-log.md`](../decisions-log.md) — Q-ME1..Q-ME20 go here

---

## 18. What this unblocks

- **Master retention foundation** — masters trust the platform with money visible
- **Production-ready earnings flow** — onboarding can promise «прозрачный доход в приложении»
- **Founder-50 cohort review** — earnings data available for billing-attribution cohort analysis
- **Tax compliance helper** — masters self-serve, salon doesn't bottleneck
- **Tip-enabled tenants** — 3 modes give 80% market coverage; PLATFORM_PASSTHROUGH for sophisticated tenants
- **Multi-tenant master attractor** — masters at 2 salons see clean ledgers per salon
- **Audit foundation for disputes** — every adjustment captured immutably

## 19. What this does NOT unblock

- ❌ Wage-on-demand / advance against earnings
- ❌ Automated «Мой налог» receipt issuance (Phase 4+ Q-ME6)
- ❌ Performance-based bonus engine (Q-ME-bonus, anti-pattern per §14)
- ❌ Crypto / multi-currency
- ❌ Real-time payouts
- ❌ Skip Q-ME9 founder dispute force-adjust audit + admin re-notification (pre-deploy lock)
- ❌ Skip Q-ME19 tip-refund-after-payout policy (pre-deploy lock)
- ❌ Customer-card-on-file (out of scope)

---

## 20. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Earnings backend lead | ☐ | |
| Mini App frontend (5 master screens + 1 admin dashboard + 1 founder tab) | ☐ | |
| AI prompt eng (4 new touchpoints §5.16-5.20) | ☐ | |
| Attribution policy steward (no conflict with §15) | ☐ | |
| Privacy / Legal (PII in export §10.3 + cross-master §2.1) | ☐ | 🔴 PRE-DEPLOY |
| Compliance (Q-ME5 KKT + Q-ME6 «Мой налог» scope confirmation) | ☐ | 🔴 PRE-DEPLOY |
| Founder (Q-ME9 + Q-ME19 final adjudication path) | ☐ | 🔴 PRE-DEPLOY |
| Accessibility (WCAG 2.2 AA on 5 master screens + dispute flow) | ☐ | |
| Tax / accounting advisor (Russia samozanyatyy + IP regimes mapping per §3) | ☐ | 🔴 PRE-DEPLOY |

## Last verified
2026-05-19 (initial draft, 3 compensation models locked, tip flow 3 modes specified, dispute SLA matrix defined, multi-tenant master ledger separation enforced)
