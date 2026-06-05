# Customer No-Show / Late Cancellation Policy — UX

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Production-blocking — master earnings + tenant economics + customer trust intersection
**Reads:** [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md), [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md), [`tenant-as-provider-model.md`](./tenant-as-provider-model.md), [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md), [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md), [`customer-loyalty-rewards-ux.md`](./customer-loyalty-rewards-ux.md), [`customer-privacy-data-closure-ux.md`](./customer-privacy-data-closure-ux.md), [`../handoffs/2026-05-19-master-earnings-handoff.md`](../handoffs/2026-05-19-master-earnings-handoff.md), [`attribution-policy.md`](./attribution-policy.md), [`booking-conflict-resolution-ux.md`](./booking-conflict-resolution-ux.md), [`event-taxonomy.md`](./event-taxonomy.md), [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md)

> Customer doesn't show up. Customer is 25 minutes late and master can't fit them in. Customer cancels 30 minutes before slot. Today no consistent UX — admin tells master via WhatsApp, master loses time, customer maybe gets penalty maybe doesn't, repeat offenders bleed the salon. This policy gives the operational + customer-trust UX.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](./ayla-identity-and-brand.md) memory 2026-05-19: no-show policy modes are **per-tenant configurable** per [`tenant-as-provider-model §3.1`](./tenant-as-provider-model.md). Customer-facing voice uses Ayla per [`ayla-identity-and-brand §2`](./ayla-identity-and-brand.md). Disputes route through emergency fallback per [`ayla-emergency-fallback-policy §3.1`](./ayla-emergency-fallback-policy.md) `payment_dispute` tier. Cross-tenant pattern visibility strictly forbidden per Doc #4 Q-CO5. Deprecated `single-assistant-identity.md` reference removed.

---

## 0. Why this exists

### 0.1 The operational gap

- [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) covers PLANNED cancellation with full window
- [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md) covers post-service complaint
- Neither covers: late arrival, no-arrival, last-minute cancel

Reality: master books 90-min slot, customer doesn't show → master loses 90 min + earnings. Without policy, salon owner absorbs as «cost of business» or yells at master → master quits.

### 0.2 What's at stake

- Master earnings: no-show = 0 earnings from that booking, but the slot was committed time
- Tenant economics: 10% no-show rate = double-digit revenue loss
- Customer trust: pattern of strict deposits = bad CX; pattern of lenient = exploited
- Master morale: bearing the cost = burnout
- Cohort attribution: ai_direct booking that no-shows → attribution policy implications

### 0.3 The promise

Single source for:
- Late arrival threshold §3 (when «late» becomes «no-show»)
- No-show detection §4 (passive auto-mark vs admin confirmation)
- Late cancellation window §5 (cancel < N hours = treated as no-show)
- Deposit / pre-pay mechanism §6 (optional per tenant)
- Master earnings handling for no-show §7 (per `master-earnings §8.2` extension)
- Repeat-offender pattern §8 (admin-only soft signal; anti-shame for customer)
- Customer-facing communication §9 (gentle vs firm framing per tenant policy)
- Per-tenant policy configuration §10
- AI Bot DM touchpoints §11
- 3 NEW models, 12 endpoints, 10 events

---

## 1. Scope

### IN
- Late-arrival threshold + handling §3
- Auto + admin-confirmed no-show detection §4
- Late-cancellation window enforcement §5
- Optional deposit mechanism §6 (per-tenant config)
- Master earnings impact §7 (per [`master-earnings §8.2`](../handoffs/2026-05-19-master-earnings-handoff.md))
- Repeat-offender admin-side signal §8
- Customer Bot DM apologetic/firm-but-fair framing §9
- 5 per-tenant policy modes §10 (lenient / standard / firm / deposit / strict)
- Customer can dispute no-show classification §12
- Attribution-policy update on confirmed no-show §13
- Anti-shame customer-facing §14
- AI Bot DM templates §11 (8 touchpoints)
- 10 NEW events

### OUT
- No-show fraud detection ML — Phase 4+
- Customer credit score / blacklist — anti-pattern §14
- Cross-tenant no-show pattern sharing — privacy boundary §16
- Court / legal collection of deposits — out of scope (salon handles)
- Anti-abuse on legitimate emergencies (customer has heart attack mid-slot) — covered by «customer can dispute» §12
- Mass no-show event (snow day) — separate `tenant-disruption-policy.md` future
- Walk-in handling (no booking, just arrives) — separate scope
- Booking-time arrival prediction ML — Phase 4+
- Per-master no-show rate analytics for the master — covered by master-reviews-feedback §6.4 master can flag «client never came»
- SMS / phone reminder integration — out of scope MVP (Bot DM only)
- Geofence-based «running late» detection — Phase 4+
- Multi-customer booking no-show (one customer of group didn't come) — Phase 4+ when group booking added

---

## 2. Strategic constraints — non-negotiable

### 2.1 Customer never accused
- ❌ «Вы не явились»
- ❌ «Третий раз не приходите!»
- ✅ «Время прошло, не получилось встретиться. Что-то случилось?»

Per [`ayla-identity-and-brand §2.2`](./ayla-identity-and-brand.md): AI tone empathetic but informational.

### 2.2 Master compensated for no-show
Per [`master-earnings §8.2`](../handoffs/2026-05-19-master-earnings-handoff.md) extension §7: no-show booking creates partial-or-full no-show earnings record. Salon (NOT customer directly) covers; tenant policy §10 determines what salon recovers from customer.

### 2.3 Pattern detection admin-only
- ❌ Customer sees «N no-shows this quarter»
- ❌ AI Bot DM «вы пропустили 3 записи, давайте обсудим»
- ✅ Admin Mini App soft signal §8

Per master-time-off pattern flag precedent.

### 2.4 No customer credit score / blacklist
- Customer is NEVER permanently labeled «unreliable»
- Customer with 10 no-shows still allowed to book (admin may decline ON-the-booking via [`manual-booking-spec.md`](./manual-booking-spec.md) or require deposit, but not platform-wide block)
- Customer profile shows no-show count to admin only — for context, not judgment

### 2.5 Deposit is optional per-tenant
Default OFF (no deposit required). Tenants can enable in Settings. Customer informed at booking time when deposit applies.

### 2.6 Refund right preserved
Even on no-show, customer can dispute via [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md) if:
- They claim they were there + master wasn't (no-show might be master's, not customer's)
- Medical emergency / force majeure
- Tenant's grace policy

### 2.7 5 tenant policy modes
Per Q-NS1: lenient / standard / firm / deposit / strict §10. Default: standard. Per-tenant configurable. Customer sees policy at first-touch + on each booking.

### 2.8 Cross-tenant pattern strict
Customer's no-show history at tenant A is invisible to tenant B per Q-CO5. Even aggregate. Salon admin at B doesn't see «customer no-showed at A».

### 2.9 Cooling-off for new customers
Per Q-NS5: first 3 bookings of new customer NEVER trigger no-show penalty. Reduces friction at acquisition. Audit captures «new customer grace».

### 2.10 Single-assistant identity preserved
Customer Bot DM uses customer voice. NEVER «у нас политика отмены» (cold policy-speak). Customer's experience feels personal.

### 2.11 Master's «client never came» recourse
Per [`master-reviews-feedback §6.4`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md): master can flag bookings where customer didn't arrive. Routes to admin for verification + no-show recording.

### 2.12 Late arrival has grace
Per §3: customers can be 5-15 min late without penalty. Master's prerogative whether to accommodate or treat as no-show after threshold (admin authorizes).

### 2.13 Per-tenant policy visible

Customer sees at booking confirmation:

```
Политика отмен в этой студии:
• До 24 часов — можно отменить без последствий
• Менее 24 часов — может быть удержано Х
• Не приход — Y
```

Transparency builds trust.

### 2.14 Customer's right to context
If customer marked no-show, AI Bot DM gentle: «Что-то случилось? Можем поговорить, если хотите». Customer can:
- Explain (admin reviews via internal channel)
- Pay penalty if applicable
- Dispute classification

### 2.15 Per-tenant SUSPENDED interaction
Per [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md): during SUSPENDED, no-show records still capture but penalties paused. Resume on tenant ACTIVE.

---

## 3. Late arrival threshold

### 3.1 Default

| Late by | Treatment |
|---|---|
| 0-10 min | On-time (no flag) |
| 10-25 min | Late — master decides whether to perform |
| 25+ min | No-show eligible (master + admin decide §4) |

Tenant configurable §10.

### 3.2 Master decision at 10-25 min mark

Master receives Bot DM:

```
{{customer_first_name}} опаздывает на запись 14:00 уже 15 минут. Что делать?

[Подождать ещё]
[Перенести на другой день]
[Отметить как не пришла]
```

Master action triggers customer Bot DM accordingly:
- «Подождать ещё» → customer notified «{{master}} ждёт вас, скоро будет?»
- «Перенести» → no-show NOT triggered; reschedule flow
- «Отметить как не пришла» → goes to admin for no-show confirmation §4

### 3.3 Customer notified at 10 min late

```
{{customer_first_name}}, ваше время к {{master}} — 14:00. Сейчас 14:10.
Вы успеваете?

[Скоро буду]   [Не получится сегодня]
```

«Не получится сегодня» → moves to late-cancellation flow §5.

### 3.4 Master can accommodate beyond 25 min

If master decides to do the service anyway (e.g., light day, customer is loyal): booking proceeds. Customer notified «{{master}} вас принимает, поторопитесь». No no-show flag.

### 3.5 Multi-late grace
Per Q-NS3: customer who arrived 20-25 min late but service completed → NOT flagged. Only «didn't perform service due to no-show» counts.

---

## 4. No-show detection

### 4.1 Auto-detection

Booking slot time + 25 min (configurable) without «customer arrived» admin check-in:
- Booking status pending → auto-flagged `possible_no_show`
- Admin Mini App «Возможные неявки» queue surfaces

### 4.2 Admin confirms or overrides

```
┌────────────────────────────────────────┐
│ ⚠ Возможная неявка                       │
├────────────────────────────────────────┤
│ Мария И. — маникюр у Анны                │
│ Запись на сегодня 14:00 (45 мин назад) │
│ Не было check-in от вас                  │
│                                        │
│ Что произошло?                           │
│ ⦿ Не пришла — отметить неявку            │
│ ◯ Пришла, я забыла отметить              │
│ ◯ Опоздала, но мы сделали (поздняя      │
│   запись)                                │
│ ◯ Я отменила запись, забыла отметить    │
│                                        │
│ [Подтвердить]                            │
└────────────────────────────────────────┘
```

### 4.3 Auto-no-show after 24h with no admin action

Per Q-NS7: if admin doesn't act within 24h post-slot, system auto-confirms no-show + emails admin. Salon-side bookkeeping protection.

### 4.4 Customer self-reports

Customer Bot DM «не получится сегодня» (late) → if < 2 hours before slot, treated as late-cancellation §5. If at/after slot time → no-show pre-confirmed (admin still reviews to handle deposit / refund).

### 4.5 Master-initiated no-show flag

Master in Mini App schedule view can mark «не пришла» on any past booking. Routes to admin for confirmation per §4.2.

### 4.6 Customer claims «я была там»

Customer can dispute no-show classification §12. AI Bot DM:

```
{{customer_first_name}}, по записи 17 мая у нас стоит «не явилась». Если
вы были там — давайте разберёмся, может быть несовпадение со стороны
студии.

[Я была там, разобраться]
[Извини, не получилось приехать]
```

---

## 5. Late cancellation window

### 5.1 Window

Per Q-NS2: default «late» = within 4 hours of slot start. Configurable per tenant §10.

Customer cancellation < 4h before slot → treated as late cancellation. Per tenant policy:
- Lenient: no penalty
- Standard: warning, third+ in 30d = no-show treatment
- Firm: same as no-show for penalty
- Deposit: deposit partially forfeit
- Strict: full no-show treatment

### 5.2 Customer Bot DM at cancel

If within window, customer Bot DM shows policy:

```
{{customer_first_name}}, отмена за 3 часа до записи — это поздно по
правилам студии.

Что произойдёт:
{{tenant-policy-specific text}}

Подтверждаете отмену?
[Да, отменяю]   [Нет, передумала]
```

### 5.3 Reschedule preferred over cancel

Customer Bot DM offers reschedule:

```
Может, не отменим, а перенесём? У {{master}} есть свободно:
{{slot_1}}, {{slot_2}}, {{slot_3}}.

[Перенести]   [Всё-таки отменить]
```

Per Q-NS9: reschedule from within-window doesn't trigger penalty if new slot is within X days (default 14d). Encourages customer retention.

### 5.4 Emergency exceptions
Customer can flag «срочно, болезнь / форс-мажор» — admin reviews via internal channel; can waive penalty. Audit captures.

---

## 6. Deposit / pre-pay (optional per tenant)

### 6.1 Tenant-enabled

Per Q-NS10: deposit OFF by default. Tenant enables in admin settings §10.

When enabled, applies:
- All bookings? Specific services? First-N-bookings only? — Tenant configures
- Default Phase 3+: «deposit on all bookings for new customers (< 3 visits)»

### 6.2 Customer booking flow

If deposit required, customer sees at booking confirmation:

```
┌────────────────────────────────────────┐
│ Подтвердить запись                       │
├────────────────────────────────────────┤
│ Маникюр у Анны, 20 мая, 14:00            │
│ Стоимость: 2500 ₽                        │
│                                        │
│ ── Предоплата ──                         │
│ Для подтверждения нужна предоплата       │
│ 500 ₽ (20%)                              │
│                                        │
│ Эта сумма:                                │
│ • Идёт в счёт услуги (вы платите 2000 ₽ │
│   на месте)                              │
│ • Возвращается при отмене за 24+ часа   │
│ • Не возвращается при неявке или        │
│   отмене менее 24ч                       │
│                                        │
│ Способ оплаты:                            │
│ [Карта (YooKassa)]                       │
│                                        │
│ [Оплатить и записаться]                  │
└────────────────────────────────────────┘
```

### 6.3 Payment processing

Phase 3+: integration with YooKassa / Tinkoff. Until then, deposits informational only — salon processes through their own POS, app records intent.

### 6.4 Deposit forfeit on no-show

Per §10 tenant policy:
- Deposit mode: deposit forfeit on confirmed no-show. Customer notified per §11.5.
- Refund via §6.5 below

### 6.5 Deposit refund

On legitimate cancellation > 24h before slot: deposit refunded via reverse payment. SLA: 5 business days.

If customer disputes no-show classification §12 → deposit held until resolution.

---

## 7. Master earnings impact

### 7.1 No-show booking earnings (per `master-earnings §8.2` extension)

| Tenant policy | `no_show_coverage_percent` | Master's no-show payout |
|---|---|---|
| Lenient | 0 | 0 (master absorbs time loss; salon doesn't claw, doesn't pay) |
| Standard | 50 | 50% of `service_price × commission_percent_applied / 100` |
| Firm | 100 | 100% of `service_price × commission_percent_applied / 100` |
| Deposit (deposit collected) | 50 | 50% (deposit funds half) |
| Strict | 100 | 100% + late-cancellation also pays master |

Configurable per tenant; admin sees per-master no-show earnings preview.

### 7.2 New `MasterEarning` event_type — `NO_SHOW_PAYOUT` (Q-NS11 RESOLVED — Option A)

Per Q-NS11 resolved decisions (sub-options A on all 4):

1. **Idempotency:** unique constraint on `(master, booking, event_type='no_show_payout')` — one no-show = one payout regardless of retry. Same pattern as REFUND_REVOKE in loyalty.

2. **Booking FK retained:** booking remains in DB with `status='no_show'`. `MasterEarning` row references it. Required for audit + dispute path.

3. **Full fields populated:** record nominal `service_price` + `commission_percent_applied` (as if service performed) + new field `no_show_coverage_percent` (0/50/100). Master sees breakdown in Mini App «Доход» — full transparency:
   ```
   total_master_amount = service_price
                       × commission_percent_applied
                       × no_show_coverage_percent
                       / 10000
   ```
   Lenient mode creates the row with `total_master_amount=0` (still tracked for audit + master sees «не пришёл клиент, оплата 0»).

4. **Post-cycle no-show:** if payout created AFTER cycle closes, applies via same machinery as refund claw-back per [`master-earnings §8.3`](../handoffs/2026-05-19-master-earnings-handoff.md): «корректировка прошлого периода» line in current cycle. NO bank-claim from master (consistent with refund flow §8.4).

5. **Tip handling:** no-show + tip impossible (tip requires customer present). `tip_amount` and `tip_master_share` are always 0 for no-show rows.

Requires `master-earnings-handoff §11.3` model extension:
- Add `event_type` field (was implicit «earn from booking»; now explicit: `regular_visit | no_show_payout | refund_revoke | manual_adjust`)
- Add `no_show_coverage_percent` field
- Add unique constraint `(master, booking, event_type)`

### 7.3 No-show doesn't affect master review aggregate
Per §master-reviews — only completed bookings can have reviews. No-show = no review.

### 7.4 No-show doesn't affect master commission profile change
Per [`master-earnings §2.6`](../handoffs/2026-05-19-master-earnings-handoff.md): commission rules effective-dated. No-show fee is per-tenant policy, doesn't change master's % share.

---

## 8. Repeat-offender pattern (admin-only)

### 8.1 Detection

Per Q-NS6: per-customer no-show count tracked. Soft signal threshold: 3 no-shows in 90 days OR 5 in 180 days.

Per master-time-off pattern flag precedent — visible ONLY to admin, never to customer or AI proactive.

### 8.2 Admin Mini App signal

```
┌────────────────────────────────────────┐
│ Customer pattern flag                    │
├────────────────────────────────────────┤
│ Мария И. — 3 неявки за последние        │
│ 90 дней.                                  │
│                                        │
│ Что можно сделать:                        │
│ [Поговорить с клиенткой]                  │
│ [Включить депозит для её записей]         │
│ [Скрыть на 30 дней]                       │
└────────────────────────────────────────┘
```

### 8.3 Admin can require deposit per-customer

For repeat offenders, admin enables «deposit required for next bookings» on customer record. Customer notified neutrally at next booking:

```
{{customer_first_name}}, для подтверждения этой записи нужна предоплата
500 ₽. Это для гарантии — возвращается при отмене за 24+ часа.

[Понятно]
```

NO «because you have N no-shows». Anti-shame.

### 8.4 Pattern flag visible 90 days

After 90 days from triggering, flag auto-hides unless new no-show triggers. Admin can manually re-trigger.

### 8.5 AI silent on pattern

AI never mentions pattern to customer. AI doesn't internally adjust tone based on pattern (would be detectable as bias).

---

## 9. Customer-facing communication

### 9.1 Late notification (10 min)

Per §3.3 above.

### 9.2 Late cancellation acknowledgment

Per §5.2 above.

### 9.3 No-show recorded (gentle)

```
{{customer_first_name}}, у нас стоит «не явилась» на запись 17 мая в
14:00. Что-то случилось?

{{tenant-policy-specific text}}

Если ошибка — могу разобрать [Разобрать ошибку]
Если что-то срочное — могу выслушать [Рассказать]
Можно записаться снова [Записаться]
```

### 9.4 Deposit forfeit notification

```
{{customer_first_name}}, по записи 17 мая 500 ₽ предоплаты не вернулись
по правилам студии. Это работало в счёт услуги, но т.к. встретиться не
получилось — осталось у студии.

Возникли вопросы? Можем обсудить.
```

### 9.5 Repeat offer (after pattern, anti-shame)

Per §8.3 — neutral deposit request, no pattern reference.

### 9.6 Lenient tenant — no penalty

```
{{customer_first_name}}, запись 17 мая отметили «не пришла». Без претензий
— но если что-то срочное, можем обсудить.

Записаться снова? [Записаться]
```

---

## 10. Per-tenant policy modes

### 10.1 Five modes

| Mode | Late cancel | No-show | Master earning | Deposit |
|---|---|---|---|---|
| **Lenient** | No penalty | No penalty | 0 (absorb) | N/A |
| **Standard** | Warning; 3+ → no-show | Loyalty -100 pts OR booking-credit-toward-master | 50% to master | N/A |
| **Firm** | Same as no-show | Charge slip / next visit prepay | 100% to master | N/A |
| **Deposit** | Deposit forfeit | Deposit forfeit | 50% (deposit funds) | YES required |
| **Strict** | Same as no-show | Deposit forfeit + 100% next visit | 100% + late-cancel pays | YES required |

### 10.2 Admin Settings

```
┌────────────────────────────────────────┐
│ ← Политика отмен и неявок                │
├────────────────────────────────────────┤
│ ⦿ Мягкая (без последствий)              │
│ ◯ Стандартная (мягко с лояльностью)     │
│ ◯ Строгая (без депозитов)                │
│ ◯ С депозитом                            │
│ ◯ Жёсткая                                 │
│                                        │
│ ── Параметры ──                          │
│                                        │
│ Опоздание = неявка после: [25] минут    │
│ Поздняя отмена: за [4] часа              │
│                                        │
│ ── Депозит ──                            │
│ Включён: ☐                                │
│ Сумма: [20] % от стоимости                │
│ Применяется к:                            │
│   ☑ Новым клиентам (первые 3 записи)    │
│   ☐ Всем                                  │
│   ☑ Клиентам с неявками в прошлом       │
│                                        │
│ ── Льготы новым клиентам ──              │
│ Не применять наказания первые [3]       │
│ записи нового клиента                    │
│                                        │
│ [Сохранить]                              │
└────────────────────────────────────────┘
```

### 10.3 Default for new tenants
Standard mode. Tenant onboarding includes policy explanation; can switch.

### 10.4 Migration / policy change
Customer existing bookings honored at policy AT BOOKING TIME (frozen). New bookings use current policy. Audit captures.

---

## 11. AI Bot DM touchpoints — 8 templates

1. **Late notification** §3.3 (10 min late)
2. **Master decision communicated** §3.2
3. **Late cancel warning** §5.2
4. **Reschedule offer** §5.3
5. **No-show recorded gentle** §9.3
6. **Deposit forfeit** §9.4
7. **Lenient acknowledgment** §9.6
8. **Master flagged «client never came» admin review** internal

All Bot DM respect customer's quiet hours + notification preferences per `customer-notification-controls-ux.md`.

---

## 12. Customer dispute path

Per [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md): customer can open dispute citing:
- «Я была там, мастера не было» (no_show_master type from refund-dispute §3.2)
- «Уведомления не получала» (procedural)
- «Срочно, болезнь» (force majeure waiver)

Refund-dispute flow handles. If dispute resolves in customer's favor → no-show flag removed, deposit refunded, master earning recalculated.

---

## 13. Attribution policy interaction (Q-NS13 RESOLVED — Option C proportional)

### 13.1 No-show billing factor per tenant mode

Per Q-NS13 resolved decision (Option C — proportional):

| Mode | `no_show_billing_factor` | Effective bill per booking (base 100₽) |
|---|---|---|
| Lenient | 0.0 | 0₽ (salon absorbs entirely; platform doesn't bill) |
| Standard | 0.5 | 50₽ (salon recovers half; platform bills half) |
| Firm | 1.0 | 100₽ (full bill — salon recovers fully via penalty) |
| Deposit (forfeit collected) | 1.0 | 100₽ |
| Deposit (no forfeit — admin waived) | 0.0 | 0₽ |
| Strict | 1.0 | 100₽ |

Rationale: «процент salon recovery = процент billable». Customer didn't receive value, but if salon recovers via penalty / deposit, attribution proportionally bills. Lenient tenants don't charge → platform doesn't either.

`booking_source` stays as original (`ai_direct` if AI created booking) — customer's commitment was real, just unfulfilled. Last-touch source rule unchanged.

### 13.2 `attribution_metadata` update on confirmed no-show

```json
{
  "no_show_recorded": true,
  "no_show_at": "2026-05-19T14:25:00Z",
  "tenant_policy": "standard",
  "no_show_billing_factor": 0.5,
  "deposit_collected": false,
  "deposit_amount": 0
}
```

`billable` field on `BookingRequest` updated to `no_show_billing_factor > 0`. Billing system multiplies base rate by factor when generating invoice line item.

### 13.3 Founder-50 cohort no-show signal (Q-NS14 RESOLVED — Option B signal-only)

Per Q-NS14 resolved decision (Option B — signal to founder, no auto-action):

- Per-cohort-customer `no_show_rate_in_cohort` computed metric added to `FounderCohortReview` aggregation
- Founder sees in Q12-δ review UI: «cohort customer Мария И. has 70% no-show rate (7 of 10)»
- NO auto-revoke from cohort
- NO auto-invalidate of billable flag
- Founder may decide per-customer to adjust attribution manually with audit reason

Rationale:
- Auto-revoke breaks customer trust («you're out because of pattern»)
- Hard threshold poorly handles edge cases (medical, life events, force majeure)
- Existing Q12-δ founder review process is the right place for nuanced decisions

Implementation: `FounderCohortReview` (existing per attribution-extensible-model memory) gets computed read-only field. No new auto-action logic.

---

## 14. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Customer blacklist for repeat no-shows | Anti-shame §2.4 | Per-customer deposit instead |
| AI Bot DM «вы пропустили 3 записи» | §2.3 admin-only pattern | Neutral deposit-request §8.3 |
| Cross-tenant no-show history | Privacy §2.8 | Per-tenant only |
| Auto-charge customer card without explicit | Trust violation | Customer must agree at booking |
| Customer can't dispute no-show | §2.6 | Refund-dispute integration §12 |
| Master loses all earnings on no-show | Burnout | Salon covers 50-100% per policy §7 |
| Customer accused «вы не явились» | Voice §2.1 | «не получилось встретиться» |
| Pattern signal visible to AI customer-facing | Bias | Admin-only §8.5 |
| Strict policy applied to first booking | Acquisition friction §2.9 | Grace for new customers |
| Late arrival auto-cancel without master input | Master autonomy | Master decides §3.2 |
| No customer notification of policy at booking | Surprise | Visible upfront §2.13 |
| Deposit non-refundable even on legit cancel | Customer trust | Refundable per timing §6.5 |
| No-show classification can't be challenged | Audit gap | Customer dispute path §12 |
| Salon-wide deposit on all bookings without warning | Friction | Configurable per tenant §10 |
| Customer's friends affected by their no-show pattern | Per-customer scope | Patterns are individual |
| Reschedule from within-cancel-window penalized | Discourages retention | Q-NS9 grace |
| Auto-confirm no-show without admin review | Trust gap | Admin confirms or 24h auto §4.3 |
| Force major emergency = same as casual no-show | Heartless | Customer can flag §5.4 |

---

## 15. Data models

### 15.1 `NoShowRecord`

```python
class NoShowRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='no_show_records')
    booking = models.OneToOneField('booking.BookingRequest', on_delete=CASCADE, related_name='no_show_record')
    customer = models.ForeignKey('identity.BotUser', on_delete=CASCADE, related_name='no_show_records')
    master = models.ForeignKey('staff.Master', null=True, on_delete=SET_NULL, related_name='+')

    DETECTION_CHOICES = [
        ('auto_after_slot', 'Auto-detected after slot time'),
        ('admin_confirmed', 'Admin confirmed'),
        ('master_flagged', 'Master flagged then admin confirmed'),
        ('customer_self_reported', 'Customer self-reported'),
    ]
    detection_source = models.CharField(max_length=64, choices=DETECTION_CHOICES)

    STATUS_CHOICES = [
        ('flagged_pending_admin', 'Flagged, awaiting admin review'),
        ('confirmed', 'Confirmed no-show'),
        ('overridden_by_admin', 'Admin overrode (customer was there / late but served / etc.)'),
        ('disputed_by_customer', 'Customer disputed via refund-dispute'),
        ('dispute_resolved_customer_favor', 'Dispute resolved: no-show removed'),
        ('dispute_resolved_no_show_stands', 'Dispute resolved: no-show stands'),
    ]
    status = models.CharField(max_length=64, choices=STATUS_CHOICES, default='flagged_pending_admin')

    tenant_policy_at_booking = models.CharField(max_length=32)
    # 'lenient' / 'standard' / 'firm' / 'deposit' / 'strict' — frozen at booking time

    deposit_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deposit_forfeit = models.BooleanField(default=False)

    master_earning_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    master_earning_event_id = models.UUIDField(null=True, blank=True)

    new_customer_grace_applied = models.BooleanField(default=False)
    # Per Q-NS5 first 3 bookings

    flagged_at = models.DateTimeField(auto_now_add=True)
    admin_reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_user = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)

    customer_facing_message_sent = models.BooleanField(default=False)

    class Meta:
        indexes = [
            Index(fields=['tenant', 'status']),
            Index(fields=['customer', '-flagged_at']),
            Index(fields=['booking']),
        ]
```

### 15.2 `LateCancellation`

```python
class LateCancellation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    booking = models.OneToOneField('booking.BookingRequest', on_delete=CASCADE, related_name='late_cancellation')
    customer = models.ForeignKey('identity.BotUser', on_delete=CASCADE, related_name='+')

    cancelled_at = models.DateTimeField()
    minutes_before_slot = models.IntegerField()
    # E.g., 180 = cancelled 180 min before slot

    tenant_policy_at_booking = models.CharField(max_length=32)
    penalty_applied = models.BooleanField(default=False)
    penalty_description = models.TextField(blank=True, default='')

    reschedule_offered = models.BooleanField(default=False)
    reschedule_accepted = models.BooleanField(default=False)

    class Meta:
        indexes = [
            Index(fields=['customer', '-cancelled_at']),
        ]
```

### 15.3 `CustomerNoShowPattern` (admin-only soft signal)

```python
class CustomerNoShowPattern(models.Model):
    customer = models.ForeignKey('identity.BotUser', on_delete=CASCADE, related_name='no_show_patterns')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    no_show_count_90d = models.IntegerField()
    no_show_count_180d = models.IntegerField()
    detected_at = models.DateTimeField(auto_now_add=True)
    admin_acknowledged = models.BooleanField(default=False)
    admin_acknowledged_at = models.DateTimeField(null=True, blank=True)
    hidden_until = models.DateTimeField(null=True, blank=True)
    deposit_required_for_future = models.BooleanField(default=False)

    class Meta:
        indexes = [
            Index(fields=['customer', 'tenant']),
        ]
```

### 15.4 `TenantNoShowPolicy`

```python
class TenantNoShowPolicy(models.Model):
    tenant = models.OneToOneField('tenancy.Tenant', on_delete=CASCADE, related_name='no_show_policy')

    MODE_CHOICES = [
        ('lenient', 'Lenient'),
        ('standard', 'Standard'),
        ('firm', 'Firm'),
        ('deposit', 'Deposit'),
        ('strict', 'Strict'),
    ]
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default='standard')

    late_arrival_no_show_after_min = models.IntegerField(default=25)
    late_cancellation_window_hours = models.IntegerField(default=4)

    deposit_enabled = models.BooleanField(default=False)
    deposit_percent = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    deposit_applies_to_new_customers = models.BooleanField(default=False)
    deposit_applies_to_all = models.BooleanField(default=False)
    deposit_applies_to_pattern_flagged = models.BooleanField(default=True)

    new_customer_grace_bookings = models.IntegerField(default=3)
    pattern_threshold_90d = models.IntegerField(default=3)
    pattern_threshold_180d = models.IntegerField(default=5)

    master_earning_share_no_show_lenient = models.IntegerField(default=0)
    master_earning_share_no_show_standard = models.IntegerField(default=50)
    master_earning_share_no_show_firm = models.IntegerField(default=100)

    reschedule_grace_window_days = models.IntegerField(default=14)
    # Q-NS9 — reschedule from late-cancel to slot within N days = no penalty

    updated_at = models.DateTimeField(auto_now=True)
```

---

## 16. API contracts

### 16.1 Customer endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/customer/booking/<id>/late-arrival-respond` | «Скоро буду» / «Не получится сегодня» §3.3 |
| POST | `/api/v1/customer/booking/<id>/dispute-no-show` | Initiate refund-dispute path §12 |
| POST | `/api/v1/customer/booking/<id>/explain-no-show` | Submit explanation (force majeure) |
| GET | `/api/v1/customer/no-show-policy` | Read current tenant's policy |
| GET | `/api/v1/customer/no-show-history` | Customer's own no-show history (transparent self-view) |

### 16.2 Master endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/master/booking/<id>/late-decision` | Wait / reschedule / mark-no-show |
| POST | `/api/v1/master/booking/<id>/flag-no-show` | Master flags |

### 16.3 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/no-shows/queue` | Pending admin review |
| POST | `/api/v1/admin/no-shows/<id>/confirm` | Confirm no-show |
| POST | `/api/v1/admin/no-shows/<id>/override` | Override (was there / served late) |
| GET | `/api/v1/admin/no-show-policy` | Get current policy |
| PATCH | `/api/v1/admin/no-show-policy` | Update policy §10.2 |
| GET | `/api/v1/admin/customer-patterns` | Pattern flags §8.2 |
| POST | `/api/v1/admin/customer-patterns/<id>/enable-deposit` | Per-customer deposit |
| POST | `/api/v1/admin/customer-patterns/<id>/hide-30d` | Hide signal |

### 16.4 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/no-show/scan-pending` | Cron post-slot-time check |
| POST | `/internal/no-show/auto-confirm-24h` | Cron 24h no-action confirm |
| POST | `/internal/no-show/pattern-recompute` | Cron daily pattern detection |

---

## 17. Events emitted

Add to [`event-taxonomy.md`](./event-taxonomy.md) `3.17 no-show domain` (NEW):

| Trigger | Event | Notes |
|---|---|---|
| Customer late notification sent | NEW: `no_show.late_notification_sent` | minutes_late |
| Master late-decision | NEW: `no_show.master_late_decision` | decision (wait/reschedule/mark_no_show) |
| No-show flagged pending | NEW: `no_show.flagged_pending_admin` | detection_source |
| Admin confirmed | NEW: `no_show.admin_confirmed` | |
| Admin overrode | NEW: `no_show.admin_overrode` | reason |
| Auto-confirmed (24h) | NEW: `no_show.auto_confirmed_24h` | |
| Customer self-reported | NEW: `no_show.customer_self_reported` | minutes_before_slot |
| Customer disputed | NEW: `no_show.disputed_by_customer` | (links to refund-dispute) |
| Deposit forfeit | NEW: `no_show.deposit_forfeit` | amount |
| Pattern detected | NEW: `no_show.pattern_detected` | count_90d |

10 NEW §17.

---

## 18. Acceptance criteria

- [ ] 4 models §15 + migration
- [ ] 19 endpoints §16
- [ ] Late arrival detection auto + master decision §3
- [ ] No-show detection auto + admin confirm §4
- [ ] 24h auto-confirm cron §4.3
- [ ] Late cancellation window enforcement §5
- [ ] Deposit flow §6 with refund logic
- [ ] Master earning impact §7 + master-earnings event type extension
- [ ] Pattern detection admin-only §8
- [ ] 5 tenant policy modes §10
- [ ] Customer dispute path integration §12
- [ ] Attribution metadata update §13
- [ ] AI Bot DM 8 templates §11 with notification-controls integration
- [ ] Cross-tenant strict isolation §2.8
- [ ] New customer grace §2.9 — first 3 bookings no penalty
- [ ] Anti-pattern review §14
- [ ] PII rules
- [ ] 10 NEW events §17
- [ ] Tests: 5 modes / 3 detection sources / customer dispute / master flag / deposit forfeit + refund / pattern detection / new customer grace / reschedule grace window / cross-tenant isolation / attribution update

---

## 19. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-NS1** | Default policy mode | Standard MVP. Lenient is too soft for sustainability; deposit/strict are too aggressive for beauty retail. | Policy + PM | 🟢 |
| **Q-NS2** | Late cancellation window default | 4 hours MVP. Beauty industry norm. Configurable per tenant. | Policy | 🟢 |
| **Q-NS3** | Late arrival no-show threshold | 25 min MVP. Configurable. Below = on-time-late. | Policy | 🟢 |
| **Q-NS4** | Customer late-arrival self-report 2h-before window — late cancel or still no-show? | If customer notifies < 2h before slot, treat as late-cancel (not no-show — they did communicate). Per §4.4. | Policy + UX | 🟡 |
| **Q-NS5** | New customer grace — first 3 bookings or different? | 3 bookings MVP §2.9. Tune based on data. | UX + PM | 🟡 |
| **Q-NS6** | Pattern threshold — 3/90d + 5/180d correct? | Yes MVP §8.1. | Data + Policy | 🟢 |
| **Q-NS7** | Auto-confirm SLA — 24h or 48h? | 24h MVP §4.3. Salon needs timely bookkeeping. | Policy | 🟢 |
| **Q-NS8** | Master flag-no-show + admin reject — same dispute path? | YES — admin clearly says «no, customer was there»; master sees rejection in internal-admin-chat. | Policy | 🟢 |
| **Q-NS9** | Reschedule from within-cancel-window — grace? | YES per §5.3. Reschedule within 14 days = no penalty. Encourages retention vs cancellation. | UX + Policy | 🟡 |
| **Q-NS10** | Deposit default ON or OFF? | OFF MVP. Tenant opts in. | PM | 🟢 |
| **Q-NS11** | Master earnings event type — extend master-earnings? | ✅ **RESOLVED** — Option A on all 4 sub-questions per §7.2: idempotency key `(master, booking, no_show_payout)`, booking FK retained, full fields populated with `no_show_coverage_percent` field, post-cycle uses claw-back machinery. Master-earnings §11.3 extended. | Eng + Policy | ✅ |
| **Q-NS12** | Deposit refund SLA — 5 days? | YES MVP. Card processor norm. | Eng | 🟢 |
| **Q-NS13** | Attribution policy on no-show booking — billable? | ✅ **RESOLVED** — Option C proportional per §13.1. `no_show_billing_factor`: Lenient 0.0 / Standard 0.5 / Firm 1.0 / Deposit-forfeit 1.0 / Deposit-waived 0.0 / Strict 1.0. Billing multiplies base by factor. | Attribution + Policy | ✅ |
| **Q-NS14** | Founder-50 cohort no-show rate threshold for billing dispute? | ✅ **RESOLVED** — Option B signal-only per §13.3. No auto-threshold, no auto-revoke. `no_show_rate_in_cohort` exposed to founder Q12-δ review UI; founder decides per-customer with audit reason. | Founder | ✅ |
| **Q-NS15** | Multi-tenant customer pattern — per-tenant only? | Per-tenant strictly per §2.8 + Q-CO5. | Privacy | 🟢 |
| **Q-NS16** | Customer who disputes no-show — admin auto-review or only refund-dispute path? | Customer initiates dispute → routes through refund-dispute flow §12. Admin reviews same channel. | Policy | 🟢 |
| **Q-NS17** | Force majeure — customer can self-report without dispute? | Customer can write «срочно, болезнь» in dispute submission. Admin discretion to waive penalty. Audit captures. | Policy | 🟡 |
| **Q-NS18** | Customer Bot DM tone — apologetic or matter-of-fact? | Matter-of-fact + empathetic per §2.1. Never dramatic. | UX | 🟢 |
| **Q-NS19** | Tenant mid-customer policy change — affects pending no-show? | NO — frozen at booking time §10.4. | Eng + Policy | 🟢 |
| **Q-NS20** | Customer can see own no-show history? | YES — full transparency per §16.1. Builds trust. | UX | 🟢 |

---

## 20. Cross-document linkage

- [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) — planned cancellation; this extends with late
- [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md) — customer dispute path §12
- [`customer-loyalty-rewards-ux.md`](./customer-loyalty-rewards-ux.md) — standard mode loyalty -100 pts penalty
- [`master-earnings-handoff §8.2`](../handoffs/2026-05-19-master-earnings-handoff.md) — earnings impact §7 + NO_SHOW_PAYOUT event type extension (Q-NS11)
- [`master-reviews-feedback-handoff §6.4`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md) — master flag-no-show recourse
- [`attribution-policy.md`](./attribution-policy.md) — §13 billable rules
- [`ayla-identity-and-brand §2.4`](./ayla-identity-and-brand.md) — voice §2.10
- [`booking-conflict-resolution-ux.md`](./booking-conflict-resolution-ux.md) — master-side no-show distinct from sync conflict
- [`customer-privacy-data-closure-ux.md`](./customer-privacy-data-closure-ux.md) — no-show records included in customer data export
- [`customer-notification-controls-ux.md`](./customer-notification-controls-ux.md) — operational category §11
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) — §2.15
- [`event-taxonomy.md §3.17`](./event-taxonomy.md) — 10 NEW events §17
- [`../decisions-log.md`](../decisions-log.md) — Q-NS1..Q-NS20

---

## 21. What this unblocks

- **Master earnings protection** — no-show payouts ensure master compensated
- **Tenant economics sustainability** — deposit + late-cancel penalties recoverable
- **Customer trust foundation** — transparent policy, gentle voice, dispute path
- **Cohort attribution accuracy** — no-show interaction with founder-50 review
- **Per-tenant business model flexibility** — 5 modes
- **Anti-customer-shame** — pattern admin-only, no public scoring
- **Cross-tenant integrity** — per-tenant isolation

## 22. What this does NOT unblock

- ❌ ML fraud detection
- ❌ Customer credit score / platform-wide blacklist (anti-pattern)
- ❌ Court collection
- ❌ Geofence detection
- ❌ Mass disruption (snow day) — separate scope
- ❌ Skip Q-NS11 master-earnings extension (pre-deploy)
- ❌ Skip Q-NS13 attribution billable rules (pre-deploy)
- ❌ Skip Q-NS14 founder cohort threshold (pre-deploy)

---

## 23. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Booking backend lead | ☐ | |
| Mini App frontend (customer + admin + master decisions) | ☐ | |
| AI prompt eng (8 Bot DM templates) | ☐ | |
| Master-earnings steward (Q-NS11 ✅ resolved Option A; verify §11.3 extension implemented) | ☐ | |
| Refund-dispute steward (§12 integration) | ☐ | |
| Attribution steward (Q-NS13 ✅ resolved Option C proportional factor; verify billing system multiplies by `no_show_billing_factor`) | ☐ | |
| Founder (Q-NS14 ✅ resolved Option B signal-only; verify `no_show_rate_in_cohort` exposed in Q12-δ review UI; 5 tenant policy mode review) | ☐ | |
| Privacy / Legal (§2.8 per-tenant pattern + Russia consumer-protection deposits) | ☐ | 🔴 PRE-DEPLOY |
| Notification-controls steward (Bot DM channel + quiet hours integration) | ☐ | |
| Accessibility (WCAG 2.2 AA) | ☐ | |

## Last verified
2026-05-19 (initial draft, 5 tenant policy modes + late arrival threshold + 24h auto-confirm + pattern detection admin-only + new-customer grace + dispute integration — locked)
