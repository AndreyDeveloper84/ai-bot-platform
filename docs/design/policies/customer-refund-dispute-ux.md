# Customer Refund / Charge Dispute Flow — UX Policy

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Production-blocking — customer-initiated refund / charge disputes happen weekly across portfolio
**Reads:** [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md), [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md), [`tenant-as-provider-model.md`](./tenant-as-provider-model.md), [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md), [`booking-conflict-resolution-ux.md`](./booking-conflict-resolution-ux.md), [`../handoffs/2026-05-19-master-earnings-handoff.md`](../handoffs/2026-05-19-master-earnings-handoff.md), [`../handoffs/2026-05-19-master-reviews-feedback-handoff.md`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md), [`../handoffs/2026-05-19-master-admin-internal-chat-handoff.md`](../handoffs/2026-05-19-master-admin-internal-chat-handoff.md), [`attribution-policy.md`](./attribution-policy.md), [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md), [`event-taxonomy.md`](./event-taxonomy.md), [`contract-offer-acceptance-display-ux.md`](./contract-offer-acceptance-display-ux.md)

> Customer pays for service. Customer is unhappy. Today customer calls salon, salon owner has a fight, master is confused, refund happens (or doesn't) outside any system. Audit lost, attribution stale, master earnings affected without trail, founder-50 cohort billing breaks. This policy specifies the customer-initiated dispute flow.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](./ayla-identity-and-brand.md) memory 2026-05-19: refund dispute flow fires [`ayla-emergency-fallback-policy §3.1`](./ayla-emergency-fallback-policy.md) `payment_dispute` tier. Ayla stays customer-facing voice throughout («передаю команде на проверку, вернусь в течение 48 часов»); admin works in Ayla Pro queue per [`tenant-as-provider-model §5`](./tenant-as-provider-model.md), NOT in customer chat. Removed deprecated `single-assistant-identity.md` + `conversation-ownership-policy.md` references.

---

## 0. Why this exists

### 0.1 The operational gap

Existing dispute machinery:
- [`booking-conflict-resolution-ux.md`](./booking-conflict-resolution-ux.md) — handles YClients sync conflicts
- [`master-earnings-handoff §9`](../handoffs/2026-05-19-master-earnings-handoff.md) — handles MASTER's earnings disputes against admin
- Neither handles CUSTOMER's complaint against the salon

[`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) covers planned cancellations (cancel-before-service). Doesn't cover post-service complaints.

### 0.2 What's at stake

- Customer trust: bad refund experience = lifetime churn + bad word-of-mouth
- Master psychological safety: customer complaint reaches master, must be mediated (per [`master-reviews-feedback-handoff §2.1`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md))
- Master earnings: refund may claw back earnings (per [`master-earnings §Q-ME19`](../handoffs/2026-05-19-master-earnings-handoff.md)) — needs spec
- Founder-50 cohort attribution: refunded booking → billable false? Reclassify? Per attribution policy
- Tenant unit economics: refund frequency feeds tenant health signal
- Legal: refund obligations differ by region; we don't enforce, but we document

### 0.3 The promise

Single source for:
- 6 dispute types §3 (service_quality / no_show_master / charge_amount / refund_delay / tip / damage)
- Customer-initiated flow §4 (Bot DM / Mini App)
- Admin review path §5 with 4-eye for amounts > threshold
- Master notification (mediated per master-reviews precedent) §6
- Refund channel mapping §7 (cash / card / etc. — informational)
- Master earnings claw-back rules §8
- Attribution-policy reclassification §9
- Founder escalation §10
- Customer offer / counter-offer mechanics §11
- 4 NEW models, 16 endpoints, 14 events

---

## 1. Scope

### IN
- Customer-initiated dispute over completed booking
- 6 dispute types §3
- T+14 day window from booking_completed_at (configurable per tenant)
- Customer-facing UI in Mini App + Bot DM trigger
- Admin review queue with 4-eye for high-amount
- Master-side notification (mediated framing)
- Refund execution recording (we DON'T process payment; we record decision + outcome)
- Master earnings claw-back via adjustment row (per master-earnings §11.3)
- Attribution-policy reclassification (refunded ai_direct may reclassify to ai_assisted or unbillable)
- Customer offer / counter-offer / settle mechanics
- Founder escalation for unresolved or sensitive
- Customer can withdraw dispute anytime
- Per-tenant dispute policy configuration (refund window days, auto-approve threshold, etc.)
- 14 NEW events

### OUT
- Payment processing / refund execution (out of scope; salon handles via own POS/bank)
- Legal arbitration / small-claims integration
- Customer credit-card chargeback handling (separate scope; depends on payment processor)
- Anti-fraud ML on customer dispute patterns — Phase 4+
- Mass refund (whole class cancelled, etc.) — separate `mass-refund-policy.md` future
- Customer-side dispute escalation to consumer-protection agencies — out of scope
- Product/take-home refunds (we don't sell products MVP)
- Gift-card refunds — Phase 4+ when gift cards added
- Cross-tenant dispute aggregation against same customer — out of scope (privacy)
- Customer's right to leave bad review separate from dispute — per master-reviews-feedback
- Loyalty point refunds — handled by loyalty subscriber on booking refund event
- Photo evidence handling — covered via existing attachment infra
- Dispute mediation by professional arbitrator — Phase 4+

---

## 2. Strategic constraints — non-negotiable

### 2.1 Customer trust foundation
- Dispute path is ALWAYS available; no time pressure
- No accusatory framing in any AI message
- Customer doesn't need to «justify» — admin reviews
- Withdraw anytime allowed

### 2.2 Master psychological safety
Per [`master-reviews-feedback §2.1`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md): single complaint NEVER ambushes master with notification storm. Mediated framing always.

### 2.3 4-eye for high-amount
Disputes with refund_requested_amount > tenant-configured threshold (default 5000 ₽) require 2 admins OR founder approval. Anti-collusion.

### 2.4 Master earnings claw-back ONLY post-admin-decision
Master's `MasterEarning` row NOT auto-modified on dispute opening. Only on admin resolution. Master sees «спор открыт» status; no immediate impact.

### 2.5 Master earnings claw-back has 3 modes
- **No claw-back** (default): refund is salon's loss, master keeps earnings
- **Proportional**: master's earnings reduced by master_share % of refund
- **Full claw-back**: master's earnings reduced 100% of refund (typically for proven master fault — gross misconduct)

Tenant configures default mode + admin can override per dispute. Per Q-CR3 explicit policy required.

### 2.6 Attribution-policy reclassification rules
Per [`attribution-policy.md`](./attribution-policy.md): refunded ai_direct booking → reclassified to:
- `booking_source` unchanged (audit trail)
- `billable` set to false IF refund_percentage ≥ 50%
- `attribution_metadata.refund_dispute_id` captures link
- `billing_reason` text updated

Per Q12-δ cohort review: founder-50 cohort dispute pattern feeds billing-attribution review.

### 2.7 No customer poaching pressure
Salon admin CAN'T offer customer «if you withdraw dispute, free service next time» as conditional. Standalone goodwill OK; conditional = anti-pattern §13.

### 2.8 Refund window
Default 14 days from `booking_completed_at`. Configurable per tenant. After window: dispute path closed unless «sensitive» (medical injury, etc.) — admin discretion via internal-admin-chat.

### 2.9 Customer NEVER pays for refund process
- No fees deducted
- Refund full amount per admin decision (could be partial per dispute outcome, but not «net of platform fee»)

### 2.10 Privacy
- Dispute text is customer-owned
- Customer's full name NEVER shown to master (per master-reviews privacy)
- Master sees framed version
- Founder/admin see full version for review

### 2.11 Master may dispute the dispute («не я виноват»)
Master can flag «не относится к моей работе» per master-reviews machinery §5.3 — admin's call. Audit captures.

### 2.12 Voice preserved
Per [`ayla-identity-and-brand §2.2`](./ayla-identity-and-brand.md): customer ↔ AI messages neutral, calm. Customer never accused. Master not vilified.

### 2.13 Sensitive disputes auto-flag founder
Per Q-CR8: alleged harm (medical injury, allergic reaction, sexual misconduct, harassment, racism, theft) → automatic founder notification + admin TIER-2 protocol per master-reviews §6.5.

### 2.14 No silent refunds
Even if admin approves refund without customer ever knowing dispute is open (e.g., admin proactively refunds based on something they observed): event captured, audit immutable, attribution updated.

### 2.15 Customer recourse path always exists
If admin denies refund and customer disagrees → escalate to founder. Founder decision is final from platform perspective; customer may still pursue legal externally.

### 2.16 Tenant SUSPENDED handling
Per [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md): existing disputes continue, new disputes deferred to post-resumption (UNLESS tenant ARCHIVED — then founder takes over).

---

## 3. Six dispute types

### 3.1 SERVICE_QUALITY
- Customer unhappy with service result
- Most common; subjective
- Default: partial refund 30-50% common
- Master notification: mediated framing «получили обратную связь по записи X»

### 3.2 NO_SHOW_MASTER
- Customer arrived, master didn't perform service (master absent / late beyond reason)
- Default: full refund + apology
- Master usually flagged for admin review
- Customer trust priority

### 3.3 CHARGE_AMOUNT
- Customer charged different from agreed price
- Default: refund difference (often clear from booking record)
- May trigger price-policy review

### 3.4 REFUND_DELAY
- Customer cancelled within policy + refund hasn't arrived
- Default: admin investigates payment channel delay
- Often resolved by clarification («refunds take 3-5 business days»)

### 3.5 TIP
- Customer disputes tip amount (e.g., charged tip without intending)
- Default: refund tip amount; tip flow tightening
- Often Mini App UX issue, not master fault

### 3.6 DAMAGE
- Customer alleges damage from service (skin reaction, hair damage, etc.)
- High sensitivity; auto-founder per §2.13 if injury alleged
- Default: full refund + medical referral if appropriate
- May escalate to insurance / legal (out of scope)

### 3.7 Quick-glance comparison

| Type | Default refund | Master notification | Severity | 4-eye? |
|---|---|---|---|---|
| SERVICE_QUALITY | Partial 30-50% | Mediated | MEDIUM | If > 5000₽ |
| NO_SHOW_MASTER | Full 100% | Direct (admin handles) | HIGH | YES regardless |
| CHARGE_AMOUNT | Difference | Inform | LOW-MEDIUM | If > 5000₽ |
| REFUND_DELAY | Process expected | None | LOW | NO (just clarify) |
| TIP | Tip amount | Inform | LOW | NO |
| DAMAGE | Full + escalate | Founder TIER-2 | CRITICAL | YES + founder |

---

## 4. Customer-initiated flow

### 4.1 Entry points

| Where | When |
|---|---|
| Mini App «Recent visits» card → «Что-то не так?» button | Post-visit |
| Bot DM «I have a problem with my last visit» (NLU detection) | Anytime |
| Mini App «Помощь» / «Поддержка» section | General |
| Post-rating low-score follow-up (per [`master-reviews-feedback §4.3`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md)) | Customer rated ≤ 2 |

### 4.2 Customer Mini App «Что-то не так?» flow

Step 1 — booking selection (if multiple recent):

```
┌────────────────────────────────────────┐
│ ← Что-то не так?                         │
├────────────────────────────────────────┤
│ С какой записью что-то не так?           │
│                                        │
│ ⦿ Маникюр у Анны, 17 мая                 │
│ ◯ Стрижка у Лены, 12 мая                │
│ ◯ Другая запись (выбрать)               │
│                                        │
│ [Дальше]                                 │
└────────────────────────────────────────┘
```

Step 2 — issue type:

```
┌────────────────────────────────────────┐
│ ← Маникюр у Анны, 17 мая                │
├────────────────────────────────────────┤
│ Что произошло?                          │
│                                        │
│ ⦿ Не довольна качеством услуги          │
│ ◯ Мастер не сделал то, что записывали   │
│ ◯ Списали не ту сумму                   │
│ ◯ Возврат после отмены не пришёл        │
│ ◯ Списали чаевые без моего согласия    │
│ ◯ Получила травму / неприятную реакцию  │
│                                        │
│ [Дальше]                                │
└────────────────────────────────────────┘
```

Step 3 — describe + outcome:

```
┌────────────────────────────────────────┐
│ ← Не довольна качеством                  │
├────────────────────────────────────────┤
│ Расскажите подробнее (можно кратко):     │
│ [_____________________________]        │
│ [_____________________________]        │
│ [_____________________________]        │
│                                        │
│ Что хотели бы?                          │
│ ⦿ Возврат денег полностью или частично │
│ ◯ Бесплатная корректировка             │
│ ◯ Просто рассказать, без претензий     │
│                                        │
│ ── Если возврат ──                      │
│ Желаемая сумма (необязательно):         │
│ [_____] ₽ (полная сумма услуги: 2500 ₽) │
│                                        │
│ [Отправить]                              │
└────────────────────────────────────────┘
```

Step 4 — confirmation:

```
┌────────────────────────────────────────┐
│ Жалоба отправлена                        │
├────────────────────────────────────────┤
│ Спасибо, что сообщили. {{salon_owner}}   │
│ свяжется с вами в течение 48 часов.      │
│                                        │
│ Можно отозвать жалобу в любой момент.   │
│                                        │
│ [Открыть переписку]                      │
└────────────────────────────────────────┘
```

Creates `RefundDispute` row + thread per §11.

### 4.3 Bot DM trigger

If customer DM's «не довольна», «верните деньги», «жалоба», NLU detects and offers:

```
Хочу разобраться. Расскажите подробнее? Или начнём по шагам:
[Открыть жалобу]
```

«Открыть жалобу» → opens Mini App flow §4.2 pre-filled with detected context.

### 4.4 Customer's dispute view (own dashboard)

```
┌────────────────────────────────────────┐
│ ← Мои жалобы                            │
├────────────────────────────────────────┤
│ ── Активные ──                          │
│                                        │
│ 17 мая, маникюр у Анны                  │
│ Качество услуги                          │
│ Жду ответа от салона                     │
│ Прислано вчера                           │
│ [Открыть]                                │
│                                        │
│ ── Закрытые ──                          │
│                                        │
│ 5 мая, стрижка у Лены                   │
│ Закрыто — возврат 500 ₽                  │
│ [Подробнее]                              │
└────────────────────────────────────────┘
```

### 4.5 Customer can withdraw

```
┌────────────────────────────────────────┐
│ ← Отозвать жалобу?                       │
├────────────────────────────────────────┤
│ Жалоба будет закрыта. Если потом         │
│ возникнут вопросы — можно открыть       │
│ новую.                                   │
│                                        │
│ [Да, отозвать]   [Передумала]            │
└────────────────────────────────────────┘
```

### 4.6 AI tone in dispute thread

Per [`ayla-identity-and-brand §2.2`](./ayla-identity-and-brand.md): customer's voice in own-tone. AI:
- NEVER «I understand your frustration» (assumes feelings)
- NEVER defensive of salon
- NEVER pushy («wouldn't a refund be enough?»)
- Calm acknowledgment: «Записал. {{salon_owner}} увидит и свяжется в течение 48 часов»
- Updates: «{{salon_owner}} ответила, посмотрите»

---

## 5. Admin review flow

### 5.1 Admin Mini App «Жалобы клиентов» tab

```
┌────────────────────────────────────────┐
│ 📋 Жалобы клиентов (3)                  │
├────────────────────────────────────────┤
│ ── Требуют ответа ──                    │
│                                        │
│ ⚠ HIGH Мария И. → Анна                   │
│ 17 мая, маникюр. Качество.              │
│ Просит: 1500 ₽ из 2500                  │
│ SLA: 30 ч из 48                          │
│ [Разобрать]                              │
│                                        │
│ 🔴 CRIT Олег П. → Лена                  │
│ 18 мая, маникюр. Травма (аллергия).     │
│ Просит: 2500 ₽ + мед.чек 3000 ₽         │
│ ⚠ Эскалирована к founder                 │
│ [Посмотреть]                             │
│                                        │
│ ── В обсуждении ──                      │
│                                        │
│ 🟡 MED Анна Н. → Марина                 │
│ 12 мая, окрашивание. Качество.          │
│ Предложили: 800 ₽ из 3200               │
│ Ждём ответа клиента                      │
│ [Открыть]                                │
└────────────────────────────────────────┘
```

### 5.2 Admin review screen

```
┌────────────────────────────────────────┐
│ ← Жалоба от Мария И.                     │
├────────────────────────────────────────┤
│ Запись: 17 мая, маникюр у Анны           │
│ Цена: 2500 ₽                             │
│ Просит вернуть: 1500 ₽                   │
│                                        │
│ ── Что пишет клиент ──                   │
│ «Покрытие стало отслаиваться через два   │
│ дня, обычно держится 2 недели. Не        │
│ ожидала такого качества от вашей        │
│ студии.»                                  │
│                                        │
│ Желает: возврат                          │
│                                        │
│ ── История клиента ──                    │
│ Постоянный клиент с 2024-03              │
│ Записей в студии: 14                     │
│ Жалоб ранее: 0                           │
│                                        │
│ ── О записи ──                            │
│ Мастер: Анна                              │
│ Услуга: Маникюр классический             │
│ Длительность: 90 мин                     │
│ Стоимость: 2500 ₽                         │
│                                        │
│ ── Решение ──                            │
│ ⦿ Согласиться (возврат 1500 ₽)          │
│ ◯ Предложить меньше                     │
│   [_____] ₽                              │
│ ◯ Бесплатная корректировка вместо       │
│   возврата                               │
│ ◯ Отказать (с комментарием)              │
│ ◯ Эскалировать к founder                 │
│                                        │
│ ── Мастер ──                             │
│ ☐ Списать с заработка Анны (по правилам │
│   - проп. от возврата = 660 ₽)           │
│ ☐ Списать всю сумму с Анны              │
│ ☐ Не списывать (студия покрывает)        │
│                                        │
│ ── Сообщение клиенту ──                  │
│ [_____________________________]        │
│ (опционально, шаблон ниже)                │
│                                        │
│ [Применить]                              │
│ [Обсудить с Анной (внутренний чат)]      │
└────────────────────────────────────────┘
```

### 5.3 4-eye requirement (§2.3)

If `refund_requested_amount > tenant_threshold` (default 5000 ₽) OR type IN (NO_SHOW_MASTER, DAMAGE):

```
┌────────────────────────────────────────┐
│ ⚠ Требуется второй админ                │
├────────────────────────────────────────┤
│ Эта жалоба требует подтверждения от     │
│ второго администратора (для сумм > 5000 ₽│
│ или серьёзных инцидентов).               │
│                                        │
│ Кто второй подписант?                    │
│ [Выбрать админа ▾]                       │
│                                        │
│ Или направить к основателю?              │
│ [Эскалировать к founder]                 │
└────────────────────────────────────────┘
```

### 5.4 Admin counter-offer

If admin offers different from customer-requested → goes to customer for accept/reject:

```
Customer sees:

{{salon_owner}} ответила на вашу жалобу:

«Извини, что покрытие подвело. Готова вернуть 800 ₽ + предложить
бесплатную корректировку у Анны в следующий визит. Подойдёт?»

[Согласна]   [Не согласна (укажу почему)]   [Хочу обсудить ещё]
```

### 5.5 Customer denial → next steps

If customer denies admin's counter-offer:
- Thread continues — admin can counter-counter
- Max 3 admin-customer back-and-forth before auto-escalate to founder (Q-CR12)

### 5.6 Auto-escalate triggers

- 3 round-trips without resolution
- Customer ALWAYS asks for founder (right per §2.15)
- Type = DAMAGE + alleged injury
- Type = NO_SHOW_MASTER + 4-eye disagreement
- Admin doesn't respond within 48h SLA × 2 = auto-escalate

### 5.7 Admin can resolve without customer accept (rare)

If admin processes refund unilaterally based on observation (e.g., admin saw the bad service themselves, decides to refund proactively):
- Event captured §14
- Customer notified «Refunded 2500 ₽ for {{booking}}. Sorry about that.»
- Customer can «accept» or open dispute proper

---

## 6. Master-side notification

### 6.1 Master sees dispute via internal-admin-chat thread

Per [`master-admin-internal-chat §5.3`](../handoffs/2026-05-19-master-admin-internal-chat-handoff.md): admin opens thread with master if dispute affects master.

```
{{master_first_name}}, есть жалоба от клиента по вашей записи 17 мая.
{{salon_owner}} разбирается. Хочет обсудить с вами — открываем чат?

[Да, обсудить]   [Я в курсе, спасибо]
```

### 6.2 Master sees affected earnings ONLY after admin decision

Per §2.4: «спор открыт» status visible, no `MasterEarning` change. Admin decides claw-back §5.2; only THEN earnings adjust + audit row.

### 6.3 Master Bot DM on resolution (claw-back applied)

```
{{master_first_name}}, по жалобе клиента 17 мая решено:
• Возврат клиенту: 1500 ₽
• С вашего заработка: 660 ₽ (40% маникюрная ставка пропорционально)

[Открыть детали]

Не согласны с решением? [Обсудить со студией]
```

«Обсудить со студией» → opens master-admin internal chat with link to dispute.

### 6.4 Master dispute-the-dispute right

Per §2.11: master can flag «не моя вина». Routes through master-admin-internal-chat with topic `general` + sensitive flag. Admin reviews → may re-open or amend resolution.

### 6.5 Master-side anti-shame

- NO «N жалоб этого месяца» counter
- NO leaderboard
- Pattern flag admin-only per master-reviews precedent (3+ disputes in 90d as soft signal)

---

## 7. Refund channel mapping

### 7.1 Per-booking payment method captured at booking COMPLETED

| Payment method | Refund channel |
|---|---|
| Cash at salon | Cash refund at next visit OR external means salon arranges |
| Card-on-file (Phase 4+) | Card refund via processor (3-5 business days) |
| Online prepay via YooKassa (Phase 3+) | YooKassa refund API |
| Yandex Pay / Tinkoff transfer | Reverse transfer arranged by salon |
| Gift card / store credit | Credit back to balance (Phase 4+) |

### 7.2 Platform records, doesn't execute

We capture admin's decision + refund_method + reference. Salon executes through their own systems. Customer notified «refund processed, expect within X days».

### 7.3 Refund delay communication

If refund channel takes time (e.g., card 3-5 days):
- Customer notified at resolution
- AI Bot DM check-in T+5 days: «Refund came through?»
- If not, dispute reopens as REFUND_DELAY type

### 7.4 Refund_arrived confirmation

Customer can confirm «yes, received» in Mini App → dispute closes formally. Or auto-confirms after 14d no objection.

---

## 8. Master earnings claw-back

### 8.1 Three modes (per §2.5)

Tenant configures default + admin overrides per dispute. Audit captures choice + amount.

### 8.2 Implementation via `MasterEarning.adjustment_history`

Per [`master-earnings §11.3`](../handoffs/2026-05-19-master-earnings-handoff.md): adjustment row appended:

```json
{
  "at": "2026-05-20T10:00:00Z",
  "from": 1100.00,
  "to": 440.00,
  "delta": -660.00,
  "reason": "Refund dispute resolution — 1500 ₽ refunded to customer; proportional claw-back at 44% master share",
  "dispute_id": "abc-123",
  "admin_user_id": 5
}
```

Master sees in Mini App «Доход» tab — adjustment line clearly displayed.

### 8.3 Claw-back during current cycle vs past

- Current cycle (not yet paid): adjustment applies immediately in cycle calc
- Past cycle (already paid): adjustment shows as «корректировка прошлого периода» line in CURRENT cycle, reducing current payout
- NEVER claw-back from master's bank (no debt collection mechanism per master-earnings §Q-ME19)

### 8.4 Negative current cycle

If claw-back > current cycle earnings → cycle goes negative. Carries forward to next cycle. NEVER demands master to pay salon back.

### 8.5 Claw-back disputes (master vs admin on this)

Per §6.4: master flags «не моя вина» → reopens through master-admin-internal-chat. Admin can revert claw-back; founder Q-ME9 escalation possible.

---

## 9. Attribution-policy reclassification

### 9.1 On dispute resolution with refund

Per [`attribution-policy.md`](./attribution-policy.md) extension:

| Refund % | `billable` | `attribution_metadata` update | `billing_reason` |
|---|---|---|---|
| 0% (denied or 0 refund) | unchanged | dispute_id + outcome captured | unchanged |
| 1-49% | true (still billable) | refund_dispute_id, refund_percentage | annotated |
| 50-99% | false (no longer billable) | refund_dispute_id, refund_percentage | reclassified |
| 100% | false | refund_dispute_id, refund_percentage = 100 | refund_total reclass |

### 9.2 booking_source unchanged

Per attribution-policy: `booking_source` reflects ORIGINAL booking origin. Dispute resolution doesn't rewrite history. Just updates billable + metadata.

### 9.3 Founder-50 cohort review trigger

If refund dispute reclassifies ai_direct → unbillable AND customer is in founder-50 cohort → Q12-δ cohort review path triggers (founder reviews dispute + booking attribution).

### 9.4 Attribution events emitted

Per [`event-taxonomy.md`](./event-taxonomy.md): `booking.attribution.adjusted` event already exists (per booking-conflict-resolution §12). Refund disputes emit same event with `reason='refund_dispute_resolution'`.

---

## 10. Founder escalation

### 10.1 Auto-escalation triggers

- Damage type + injury alleged §3.6
- Sensitive keywords (per [`master-reviews-feedback §6.5`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md))
- 3 round-trips without resolution
- Customer always-escalates §2.15
- Admin unavailable 48h × 2

### 10.2 Founder review screen (Phase 3+)

```
┌────────────────────────────────────────┐
│ ⚠ Refund dispute — founder review        │
├────────────────────────────────────────┤
│ Tenant: Студия Натали                   │
│ Customer: Olga P.                        │
│ Master: Anna                             │
│ Booking: 17 May, manicure (2500 ₽)      │
│                                        │
│ Type: DAMAGE (alleged allergic reaction) │
│ Refund requested: 2500 ₽ + 3000 ₽ med   │
│                                        │
│ Customer description: «...»             │
│ Admin position: «...»                   │
│ Master statement: «...»                 │
│                                        │
│ Risk: Medical injury claim — possible   │
│ insurance impact                         │
│                                        │
│ Founder decision options:                │
│ [Approve customer claim in full]         │
│ [Counter-offer mediated]                 │
│ [Approve partial]                        │
│ [Deny + customer external recourse]      │
│ [Request more info]                      │
│                                        │
│ Founder comments (audit):               │
│ [_____________________________]        │
│                                        │
│ [Decide]                                 │
└────────────────────────────────────────┘
```

### 10.3 Founder decision finality

From platform perspective, founder is last word. Customer can pursue legal externally — platform doesn't block or assist.

### 10.4 Audit trail to founder

All admin actions + master statements + customer messages captured for founder review.

---

## 11. Offer / counter-offer mechanics

### 11.1 Initial customer ask
Per §4.2 step 3 — customer can specify amount OR leave blank («let admin propose»).

### 11.2 Admin offer
Either:
- Accept customer's ask in full
- Counter-offer smaller amount + optional service credit
- Decline with reason

### 11.3 Customer accept / reject
- Accept → dispute resolves
- Reject → another round (max 3 admin offers per dispute)
- Customer-counter («хочу 1200, не 800») → admin reviews again

### 11.4 Round limit
3 admin offers max → auto-escalate founder.

### 11.5 Settlement non-monetary OK

Acceptable settlement forms:
- Full refund
- Partial refund
- Service credit (next visit free / discounted)
- Free correction service
- Combination

All recorded. Customer agrees explicitly.

### 11.6 Counter-offer must be customer-presentable

Admin can't propose «if you withdraw, we'll think about it» — per §2.7 conditional restraint. Offers must be unconditional + clear.

---

## 12. Data models

### 12.1 `RefundDispute`

```python
class RefundDispute(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='refund_disputes')
    booking = models.ForeignKey('booking.Booking', on_delete=CASCADE, related_name='refund_disputes')
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='refund_disputes_filed')
    master = models.ForeignKey('staff.Master', null=True, on_delete=SET_NULL, related_name='refund_disputes_received')

    TYPE_CHOICES = [
        ('service_quality', 'Service quality'),
        ('no_show_master', 'Master no-show'),
        ('charge_amount', 'Charge amount wrong'),
        ('refund_delay', 'Refund delay (cancellation)'),
        ('tip', 'Tip dispute'),
        ('damage', 'Damage / injury'),
    ]
    dispute_type = models.CharField(max_length=32, choices=TYPE_CHOICES)

    SEVERITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical (damage/injury)'),
    ]
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)

    customer_description = models.TextField(max_length=2000)
    customer_desired_outcome = models.CharField(max_length=32)
    # 'refund', 'free_correction', 'just_complaint', 'service_credit'
    refund_requested_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    STATUS_CHOICES = [
        ('opened', 'Opened by customer'),
        ('admin_reviewing', 'Admin reviewing'),
        ('admin_proposed', 'Admin counter-offered'),
        ('customer_reviewing_offer', 'Customer reviewing offer'),
        ('round_2_admin_reviewing', 'Round 2 admin'),
        ('founder_review', 'Founder reviewing'),
        ('resolved_full_refund', 'Resolved — full refund'),
        ('resolved_partial_refund', 'Resolved — partial refund'),
        ('resolved_free_correction', 'Resolved — free correction service'),
        ('resolved_service_credit', 'Resolved — service credit'),
        ('resolved_denied', 'Resolved — denied'),
        ('withdrawn_by_customer', 'Withdrawn by customer'),
        ('expired', 'Expired (admin/customer no action)'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='opened')

    refund_amount_final = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    refund_method = models.CharField(max_length=32, blank=True, default='')
    # 'cash', 'card_processor', 'yookassa', 'transfer', etc. — per §7.1
    refund_reference = models.CharField(max_length=128, blank=True, default='')
    refund_processed_at = models.DateTimeField(null=True, blank=True)
    refund_confirmed_received_at = models.DateTimeField(null=True, blank=True)

    free_correction_offered = models.BooleanField(default=False)
    service_credit_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # 4-eye + founder
    requires_4_eye = models.BooleanField(default=False)
    admin_first_signer = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    admin_second_signer = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    founder_reviewed_at = models.DateTimeField(null=True, blank=True)
    founder_decision_user = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')

    # Master earnings impact
    EARNING_CLAWBACK_MODE_CHOICES = [
        ('none', 'No claw-back'),
        ('proportional', 'Proportional to master share'),
        ('full', 'Full refund amount'),
    ]
    earnings_clawback_mode = models.CharField(max_length=32, choices=EARNING_CLAWBACK_MODE_CHOICES, default='none')
    earnings_clawback_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    earnings_clawback_applied_at = models.DateTimeField(null=True, blank=True)

    # Attribution impact
    attribution_billable_before = models.BooleanField(null=True, blank=True)
    attribution_billable_after = models.BooleanField(null=True, blank=True)
    attribution_metadata_update = models.JSONField(default=dict, blank=True)

    sensitive_flagged = models.BooleanField(default=False)
    # True for damage type or sensitive keywords

    opened_at = models.DateTimeField(auto_now_add=True)
    sla_due_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    withdraw_at = models.DateTimeField(null=True, blank=True)

    round_trip_count = models.IntegerField(default=0)
    # Increments per admin counter-offer

    class Meta:
        indexes = [
            Index(fields=['tenant', 'status', '-opened_at']),
            Index(fields=['customer', '-opened_at']),
            Index(fields=['master', '-opened_at']),
            Index(fields=['sla_due_at']),
        ]
```

### 12.2 `RefundDisputeMessage`

```python
class RefundDisputeMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dispute = models.ForeignKey(RefundDispute, on_delete=CASCADE, related_name='messages')

    SENDER_ROLE_CHOICES = [
        ('customer', 'Customer'),
        ('admin', 'Admin'),
        ('founder', 'Founder'),
        ('system', 'System (auto-messages)'),
    ]
    sender_role = models.CharField(max_length=16, choices=SENDER_ROLE_CHOICES)
    sender_user = models.ForeignKey('auth.User', null=True, blank=True, on_delete=SET_NULL, related_name='+')

    MESSAGE_TYPE_CHOICES = [
        ('initial_customer_description', 'Initial customer description'),
        ('admin_offer', 'Admin offer'),
        ('customer_counter', 'Customer counter'),
        ('admin_reply', 'Admin reply'),
        ('customer_accept', 'Customer accepted'),
        ('customer_reject', 'Customer rejected'),
        ('system_resolved', 'System: resolved'),
        ('system_escalated', 'System: escalated'),
        ('system_withdrawn', 'System: withdrawn'),
    ]
    message_type = models.CharField(max_length=64, choices=MESSAGE_TYPE_CHOICES)

    body = models.TextField(max_length=4000, blank=True, default='')
    proposed_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['dispute', '-sent_at']),
        ]
```

### 12.3 `RefundDisputeAuditEvent`

```python
class RefundDisputeAuditEvent(models.Model):
    dispute = models.ForeignKey(RefundDispute, on_delete=CASCADE, related_name='audit_events')

    EVENT_CHOICES = [
        ('opened', 'Customer opened'),
        ('admin_reviewed', 'Admin reviewed'),
        ('admin_offered', 'Admin offered'),
        ('customer_accepted', 'Customer accepted'),
        ('customer_rejected', 'Customer rejected'),
        ('customer_countered', 'Customer counter-offered'),
        ('4_eye_required', '4-eye triggered'),
        ('4_eye_completed', '4-eye completed'),
        ('founder_escalated', 'Escalated to founder'),
        ('founder_decided', 'Founder decided'),
        ('refund_processed', 'Refund recorded as processed'),
        ('refund_confirmed_received', 'Customer confirmed refund received'),
        ('earnings_clawback_applied', 'Master earnings claw-back applied'),
        ('attribution_updated', 'Attribution metadata updated'),
        ('master_flagged_not_responsible', 'Master flagged not their fault'),
        ('withdrawn', 'Customer withdrew'),
        ('expired_no_action', 'Expired'),
    ]
    event = models.CharField(max_length=64, choices=EVENT_CHOICES)
    actor = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    metadata = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)
```

### 12.4 `TenantRefundDisputePolicy`

```python
class TenantRefundDisputePolicy(models.Model):
    tenant = models.OneToOneField('tenancy.Tenant', on_delete=CASCADE, related_name='refund_dispute_policy')

    refund_window_days = models.IntegerField(default=14)
    four_eye_threshold_amount = models.DecimalField(max_digits=10, decimal_places=2, default=5000)
    auto_approve_under_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # If 0, no auto-approve

    default_clawback_mode = models.CharField(max_length=32, default='none', choices=RefundDispute.EARNING_CLAWBACK_MODE_CHOICES)
    admin_response_sla_hours = models.IntegerField(default=48)
    max_round_trips = models.IntegerField(default=3)

    refund_methods_available = models.JSONField(default=list, blank=True)
    # ['cash', 'card_processor', 'yookassa', 'transfer']

    updated_at = models.DateTimeField(auto_now=True)
```

---

## 13. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Auto-claw-back from master before admin decision | Trust violation §2.4 | Only post-decision §8 |
| Conditional offer «withdraw and we'll think» | Coercive §2.7 | Unconditional offers only |
| Customer fees for refund process | §2.9 | Never |
| Master sees raw negative review without mediation | Anti-shame §2.2 | Mediated framing §6.1 |
| Mass auto-deny disputes by pattern | Customer harm | Per-case review |
| Hide dispute from master entirely | Trust gap §6.4 | Master informed via internal-admin-chat |
| Master can «delete» dispute affecting them | Audit corruption | Only flag «not my fault» §6.4 |
| Customer can't reopen dispute after admin denied | §2.15 customer recourse | Founder escalation always possible |
| Pay-tied-to-refund-rate | Coerces master | NEVER |
| Cross-tenant dispute aggregation against same customer | Privacy | NEVER MVP |
| Founder reviews without master statement | Incomplete picture | Master statement collected |
| Auto-refund without customer's explicit consent | §2.14 silent refunds | Event always emitted |
| Refund executed by platform (we're not processor) | Out of scope §1 | Salon executes, we record |
| Customer can submit dispute 6 months later | Stale data | 14d window §2.8 with admin override |
| 5 round-trips without escalation | Stalling | Max 3 admin offers §11.4 |
| Master claw-back claws negative cycle into next | Confusing | Carry forward §8.4 NEVER bank-claim |
| Attribution silently changes booking_source | Audit confusion | booking_source unchanged §9.2 |
| Customer's full name shown to master | Privacy §2.10 | Initials only |
| Dispute thread mixed with customer's normal conversation | Confusion | Separate dispute thread |
| Admin can self-approve > 5000 ₽ without 4-eye | Anti-collusion fail §2.3 | 4-eye required |
| AI summarizes customer's complaint to admin | Distorts | Admin reads in full per master-reviews precedent |

---

## 14. Events emitted

Add to [`event-taxonomy.md`](./event-taxonomy.md) `3.14 refund dispute domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| Customer opened | NEW: `refund_dispute.opened` | type, severity |
| 4-eye required | NEW: `refund_dispute.4_eye_triggered` | |
| Admin reviewed | NEW: `refund_dispute.admin_reviewed` | |
| Admin offered | NEW: `refund_dispute.admin_offered` | amount, round_number |
| Customer accepted | NEW: `refund_dispute.customer_accepted` | |
| Customer rejected | NEW: `refund_dispute.customer_rejected` | |
| Customer counter-offered | NEW: `refund_dispute.customer_countered` | counter_amount |
| Auto-escalated | NEW: `refund_dispute.auto_escalated` | reason (rounds_exceeded/sensitive/sla_breach) |
| Founder decided | NEW: `refund_dispute.founder_decided` | decision |
| Refund processed | NEW: `refund_dispute.refund_processed` | method, amount |
| Refund confirmed received | NEW: `refund_dispute.refund_confirmed_received` | |
| Earnings claw-back applied | NEW: `refund_dispute.earnings_clawback_applied` | mode, amount |
| Withdrawn | NEW: `refund_dispute.withdrawn` | |
| Attribution updated | (existing) `booking.attribution.adjusted` | `reason='refund_dispute'` per §9.4 |

13 NEW events + 1 reused = 14 §14.

---

## 15. API contracts

### 15.1 Customer endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/customer/refund-disputes` | Open §4.2 |
| GET | `/api/v1/customer/refund-disputes` | List own |
| GET | `/api/v1/customer/refund-disputes/<id>` | Detail with messages |
| POST | `/api/v1/customer/refund-disputes/<id>/messages` | Send message |
| POST | `/api/v1/customer/refund-disputes/<id>/accept-offer` | Accept admin offer |
| POST | `/api/v1/customer/refund-disputes/<id>/reject-offer` | Reject + optional counter |
| POST | `/api/v1/customer/refund-disputes/<id>/withdraw` | §4.5 |
| POST | `/api/v1/customer/refund-disputes/<id>/confirm-refund-received` | §7.4 |
| POST | `/api/v1/customer/refund-disputes/<id>/escalate-to-founder` | §2.15 |

### 15.2 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/refund-disputes/queue` | Open disputes |
| GET | `/api/v1/admin/refund-disputes/<id>` | Detail §5.2 |
| POST | `/api/v1/admin/refund-disputes/<id>/offer` | Make offer |
| POST | `/api/v1/admin/refund-disputes/<id>/deny` | Deny |
| POST | `/api/v1/admin/refund-disputes/<id>/4-eye-approve` | Second admin signs |
| POST | `/api/v1/admin/refund-disputes/<id>/escalate-to-founder` | Manual escalate |
| POST | `/api/v1/admin/refund-disputes/<id>/mark-refund-processed` | Record salon-side refund |
| GET | `/api/v1/admin/refund-disputes/<id>/master-impact` | Preview claw-back amount |

### 15.3 Founder endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/refund-disputes/escalated` | Cross-tenant escalations |
| POST | `/api/v1/founder/refund-disputes/<id>/decide` | Final decision §10.2 |

### 15.4 Master endpoints (limited)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/master/refund-disputes/affecting-me` | Disputes about own work (mediated view) |
| POST | `/api/v1/master/refund-disputes/<id>/flag-not-my-fault` | §6.4 |
| POST | `/api/v1/master/refund-disputes/<id>/statement` | Add master statement to record |

### 15.5 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/refund-disputes/<id>/apply-clawback` | Apply MasterEarning adjustment §8.2 |
| POST | `/internal/refund-disputes/<id>/update-attribution` | Apply §9 changes |
| POST | `/internal/refund-disputes/scan-sla-breach` | Cron |

### 15.6 Sample request: customer open

POST `/api/v1/customer/refund-disputes`:

```json
{
  "booking_id": "uuid",
  "dispute_type": "service_quality",
  "customer_description": "Покрытие стало отслаиваться через два дня...",
  "customer_desired_outcome": "refund",
  "refund_requested_amount": 1500.00
}
```

Validation:
- Booking COMPLETED status
- Booking is customer's own
- Within refund window §2.8
- One open dispute per booking (subsequent → 409)
- description ≤ 2000 chars
- if outcome='refund', amount > 0 + ≤ booking total
- sensitive-keyword detection raises severity automatically

Response (201):
```json
{
  "id": "uuid",
  "status": "opened",
  "severity": "medium",
  "sla_due_at": "...",
  "requires_4_eye": false
}
```

---

## 16. Acceptance criteria (engineering checklist)

- [ ] 4 models §12 + migration
- [ ] 25 endpoints across 4 roles §15
- [ ] Customer Mini App flow §4.2 (3-step)
- [ ] Customer Bot DM NLU trigger §4.3
- [ ] Customer dispute dashboard §4.4
- [ ] Admin queue + review screen §5.1-5.2
- [ ] 4-eye for amount > threshold OR type ∈ {NO_SHOW_MASTER, DAMAGE} §5.3
- [ ] Counter-offer + customer review loop §5.4-5.5
- [ ] Auto-escalate at 3 rounds OR SLA × 2 OR sensitive §5.6
- [ ] Founder review screen §10.2 (Phase 3+)
- [ ] Master notification via internal-admin-chat §6.1
- [ ] Master sees affected earnings only post-decision §6.2
- [ ] Master claw-back Bot DM §6.3
- [ ] Master flag-not-my-fault §6.4
- [ ] Refund channel mapping recording §7
- [ ] Refund received confirmation §7.4
- [ ] MasterEarning adjustment via §8.2 (NEVER bank-claim per §8.4)
- [ ] Attribution-policy update §9 with billable + metadata only (booking_source unchanged §9.2)
- [ ] Sensitive auto-flag founder §2.13
- [ ] Round-trip counter §11.4
- [ ] Tenant policy config §12.4
- [ ] 14 events §14
- [ ] PII rules §2.10 (customer initials to master only)
- [ ] Cross-tenant 403
- [ ] Tests: 6 types e2e / 4-eye / sensitive auto-escalate / master flag / claw-back modes (none/proportional/full) / negative cycle carry-forward / attribution update / round-trip limit / customer withdrawal / refund confirmation / cross-tenant 403 / cross-master 403
- [ ] Anti-pattern review §13

---

## 17. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-CR1** | Refund window default — 14 days correct? | 14d MVP §2.8; tenants can configure. Aligns with industry practice. | Policy | 🟢 |
| **Q-CR2** | 4-eye threshold — 5000 ₽ default? | 5000 ₽ MVP; aligns with median service price × 2. Tenant configurable. | Policy + UX | 🟢 |
| **Q-CR3** | Claw-back default mode — none / proportional / full? | NONE default. Tenant explicitly opts-in to proportional/full via salon HR consultation. Per §2.5. | Policy | 🔴 PRE-DEPLOY |
| **Q-CR4** | Customer can dispute disputes won by admin (escalate to founder) — always allowed? | YES per §2.15. Even after admin denied. | Policy | 🟢 |
| **Q-CR5** | Refund window override for sensitive (medical injury after 14d)? | YES admin can manually open via internal-admin-chat with founder review. Per §2.8 «admin discretion». | Policy | 🟡 |
| **Q-CR6** | Customer's full text — visible to master? | NO — mediated framing per master-reviews precedent §2.10. Admin can quote specifically if discussing with master. | Privacy + UX | 🟢 |
| **Q-CR7** | Refund processing time SLA — committed to customer? | Per refund channel §7.1. Cash: «at next visit or within 7 days». Card: 3-5 business days. Transfer: same day. Customer notified at resolution. | UX + Policy | 🟡 |
| **Q-CR8** | Sensitive keyword detection — same list as master-reviews §6.5? | YES — shared list. Phase 2 regex; Phase 3+ LLM. | AI + Compliance | 🔴 PRE-DEPLOY |
| **Q-CR9** | Master statement collection — required before founder review? | RECOMMENDED but not blocking. Founder can decide without if master unavailable. Audit captures. | Policy | 🟡 |
| **Q-CR10** | Customer reviews + dispute on same booking — coexist? | YES — separate flows. Review = public-ish opinion; dispute = financial claim. Customer can do both. Review aggregate frozen if dispute results in changing master's claw-back. | Eng + Policy | 🟢 |
| **Q-CR11** | Master claw-back > current cycle — carry forward as «long-term debt»? | Carry forward to NEXT cycle. Never bank-claim. If cycles repeatedly negative → flag for tenant-level intervention (probably master ↔ admin disagreement; resolve via internal-admin-chat). | Policy + Eng | 🔴 PRE-DEPLOY |
| **Q-CR12** | Max round-trips — 3 admin offers correct? | 3 MVP. Tune based on data. Beyond 3 = escalate to founder. | Policy | 🟢 |
| **Q-CR13** | Customer no-response to admin's offer — auto-accept or auto-deny? | Auto-DENY (favors customer status quo). After 7d no response, marks `customer_no_response_expired` status, customer can reopen within original window. | Policy + UX | 🟡 |
| **Q-CR14** | Master's earnings cycle paused if dispute open? | NO — cycle continues. Adjustment lands when dispute resolves. Per §8.3. | Eng | 🟢 |
| **Q-CR15** | Multiple disputes on same booking (rare but possible — different complaints) — allowed? | One dispute per booking MVP. Customer can withdraw + open new. Edge case. | Policy + Eng | 🟢 |
| **Q-CR16** | Bot DM sensitive keyword detection from customer DM → auto-create dispute or just flag? | Just flag — offer dispute open §4.3. Don't auto-create (customer's intent unclear). | UX | 🟡 |
| **Q-CR17** | Customer who has 3+ disputes in 90 days — soft flag? | YES — admin-only signal (anti-fraud). NOT visible to customer or AI. Per master-reviews precedent. | Privacy + Policy | 🟡 |
| **Q-CR18** | If customer disputes during master's offboarding → dispute migrates? | Per [`master-offboarding §7.5/§8.2`](../handoffs/2026-05-19-master-offboarding-handoff.md): dispute attaches to MASTER (frozen on offboarding date). If post-offboarding new dispute, master_id pointer is SET_NULL via per §12.1. Admin handles refund decision. Master cannot participate post-offboarding except via founder request. | Privacy + Policy | 🔴 PRE-DEPLOY |
| **Q-CR19** | Multi-tenant customer (customer at salons A + B) — disputes per tenant or cross-aggregate? | Per tenant strictly. Cross-aggregate would violate privacy boundary per Q-CO5. | Privacy | 🟢 |
| **Q-CR20** | Loyalty points refund — auto on refund? | YES — per Phase 1.b loyalty subscriber listens to refund event. Refund event emits `loyalty.refund.applied`. Audit captures. | Eng | 🟡 |

---

## 18. Cross-document linkage

- [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) — pre-service cancellation (separate)
- [`booking-conflict-resolution-ux.md`](./booking-conflict-resolution-ux.md) — sync conflict; refund-dispute is post-completion
- [`master-earnings-handoff §11.3/§Q-ME19`](../handoffs/2026-05-19-master-earnings-handoff.md) — claw-back implementation
- [`master-reviews-feedback-handoff.md`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md) — anti-shame framing reuse
- [`master-admin-internal-chat-handoff §5.3`](../handoffs/2026-05-19-master-admin-internal-chat-handoff.md) — master-side communication channel
- [`attribution-policy.md`](./attribution-policy.md) — billable / metadata update §9
- [`master-offboarding-handoff §8.2`](../handoffs/2026-05-19-master-offboarding-handoff.md) — Q-CR18 offboarded master interaction
- [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md) — voice §2.12
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — tier escalation possible
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) — SUSPENDED state §2.16
- [`event-taxonomy.md §3.14`](./event-taxonomy.md) — 14 events §14
- [`contract-offer-acceptance-display-ux.md`](./contract-offer-acceptance-display-ux.md) — Q12-ε dispute interaction
- [`../decisions-log.md`](../decisions-log.md) — Q-CR1..Q-CR20

---

## 19. What this unblocks

- **Customer trust foundation** — formal refund channel vs ad-hoc calls
- **Master psychological safety preserved** — disputes don't ambush
- **Master earnings transparency** — claw-back rules + audit before any deduction
- **Attribution accuracy** — refunded ai_direct correctly reclassified for founder-50 cohort billing
- **Founder oversight on injuries** — auto-escalation captures medical-adjacent claims
- **Salon HR support** — formal flow vs «I think there's a complaint somewhere»
- **Multi-tenant integrity** — per-tenant disputes; no cross-leak
- **Loyalty point integrity** — refund auto-deducts points

## 20. What this does NOT unblock

- ❌ Payment processing (out of scope; salon executes)
- ❌ Customer credit-card chargeback (separate scope)
- ❌ Legal arbitration
- ❌ ML fraud detection on customer dispute patterns
- ❌ Mass refunds (whole class cancelled)
- ❌ Product/take-home refunds (no products MVP)
- ❌ Gift card refunds (no gift cards MVP)
- ❌ Skip Q-CR3 claw-back default policy (pre-deploy)
- ❌ Skip Q-CR8 sensitive keyword list (pre-deploy)
- ❌ Skip Q-CR11 negative-cycle carry-forward policy (pre-deploy)
- ❌ Skip Q-CR18 offboarded-master dispute policy (pre-deploy)

---

## 21. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Booking backend lead | ☐ | |
| Mini App frontend (customer 3-step flow + admin queue + master mediated view + founder Phase 3+) | ☐ | |
| AI prompt eng (customer Bot DM NLU trigger + Q-CR16 detection) | ☐ | |
| Master-reviews steward (mediated framing reuse §6.1) | ☐ | 🔴 PRE-DEPLOY |
| Master-earnings steward (claw-back §8 + Q-CR3 + Q-CR11) | ☐ | 🔴 PRE-DEPLOY |
| Master-admin-chat steward (§6.1 communication channel) | ☐ | |
| Attribution steward (§9 reclassification rules) | ☐ | 🔴 PRE-DEPLOY |
| Master-offboarding steward (Q-CR18) | ☐ | 🔴 PRE-DEPLOY |
| Privacy / Legal (§2.10 + Q-CR8 sensitive list + Q-CR17 customer pattern flag) | ☐ | 🔴 PRE-DEPLOY |
| Founder (Q-CR3 default policy + Q-CR11 negative-cycle + Q12-δ cohort interaction §9.3) | ☐ | 🔴 PRE-DEPLOY |
| Accessibility (WCAG 2.2 AA) | ☐ | |
| Legal (Russia consumer-protection alignment on refund window + obligations) | ☐ | 🔴 PRE-DEPLOY |

## Last verified
2026-05-19 (initial draft, 6 dispute types + 4-eye threshold + 3 claw-back modes + attribution reclassification + max 3 round-trips + sensitive auto-founder — locked)
