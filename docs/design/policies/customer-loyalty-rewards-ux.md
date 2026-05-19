# Customer Loyalty / Rewards / Referral — UX Policy

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Production-blocking — loyalty backend (`apps/loyalty/`) ships in parallel (PRs #173, #181, #184); customer-facing UX gap fills here
**Reads:** [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md), [`tenant-as-provider-model.md`](./tenant-as-provider-model.md), `apps/loyalty/models.py`, `apps/loyalty/services.py`, [`customer-profile-management-ux.md`](./customer-profile-management-ux.md), [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md), [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md), [`assistant-persona.md`](./assistant-persona.md) (r2), [`event-taxonomy.md`](./event-taxonomy.md), [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`notification-preferences-ux.md`](./notification-preferences-ux.md), [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md), [`../handoffs/2026-05-19-master-earnings-handoff.md`](../handoffs/2026-05-19-master-earnings-handoff.md)

> Loyalty backend is in-flight: 3 tiers (Стартовый / Постоянный / Любимый), 9 event types (earn_visit / earn_referral / earn_birthday / earn_review / earn_return / redeem / manual_adjust / refund_revoke / tier_changed), append-only event log, auto-enrollment, per-tenant opt-out. Customer never sees any of this without a UX spec. This policy makes loyalty feel natural without slipping into points-grinding shame.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](./ayla-identity-and-brand.md) memory 2026-05-19: loyalty stays **per-tenant** (each salon's provider reward program) per [`tenant-as-provider-model §6.1`](./tenant-as-provider-model.md). Ayla mediates customer-facing surfaces. Customer's Ayla memory is cross-tenant; loyalty balance per-tenant — these are different scopes. Bot DM templates use Ayla voice per [`ayla-identity-and-brand §2`](./ayla-identity-and-brand.md). Deprecated `single-assistant-identity.md` reference removed.

---

## 0. Why this exists

### 0.1 The integration gap

Engineering reality:
- `apps.loyalty.models.LoyaltyAccount` + `LoyaltyEvent` shipped
- `LoyaltySubscriber` listens to `booking.completed`, credits points
- Refund subscriber reverses on `booking.cancelled` (Phase 1.b)
- Tier auto-recompute on EARN_VISIT + REFUND_REVOKE (Phase 2.a)
- Anti-abuse constants live in services.py (`MIN_REDEMPTION_POINTS=50`, `REDEMPTION_CAP_PERCENT=30`)
- Referrals (`earn_referral`), birthday (`earn_birthday`), review (`earn_review`), long-gap return (`earn_return`), redemption UI, opt-out flow — DEFERRED to future PRs

Customer reality:
- Customer is silently accruing points after every visit
- No visibility = doesn't know
- Can't redeem
- Tier transitions invisible
- No referral mechanic surfaced

Without this UX spec, loyalty backend is dead weight — no customer value, no business retention lift.

### 0.2 The promise

Single source for:
- Tier display + progression UX §4
- Balance + history surfaces §5
- 6 earning surfaces §6 (visit / referral / birthday / review / return / manual)
- Redemption flow at booking time §7
- Referral mechanics (invite + attribution + reward) §8
- Birthday bonus UX §9
- Tier-change celebration without shame §10
- Opt-out flow §11 (Q-L12 alignment)
- Refund-revoke transparency §12
- AI Bot DM touchpoints §13 (8 new templates)
- Anti-patterns specific to loyalty §14 (NO points-grinding shame, NO comparison)
- 10 endpoints + 6 events
- Open questions for backend-UX alignment

### 0.3 What loyalty IS (and IS NOT)

**IS:**
- Retention mechanism via reward
- Customer's right to know what they're earning
- Tier as positive identity signal
- Referral as growth + reward

**IS NOT:**
- Game (per master-earnings §2.2 anti-gamification — but with customer-context nuance §3)
- Status competition between customers
- Pressure to «keep streak»
- Shame for low tier
- Tracking customer down for «come back» nagging

---

## 1. Scope

### IN
- Mini App «Бонусы» section in Profile tab (or top-level if usage warrants)
- Tier display (3 tiers: Стартовый / Постоянный / Любимый)
- Balance + event history
- Tier progression visibility (without anti-shame framing)
- Redemption at booking time (UI to apply points discount)
- Referral mechanic: customer invites friend → friend's first visit triggers referral_credit (UX + share link)
- Birthday bonus: auto-credit on birthday + Bot DM acknowledgment
- Review-earned bonus: per review left, points credited
- Long-gap return bonus: customer back after N days → welcome-back points
- Manual admin adjustment visibility (audit row visible)
- Refund-revoke transparency (when points deducted due to refund/cancel)
- Tier-change celebration (calm, not confetti-shame)
- Customer opt-out flow §11
- Cross-tenant loyalty: separate per tenant per Q-CO5
- AI Bot DM touchpoints for: tier-up / birthday / referral-converted / review-credited / redemption-confirmed / opt-out-confirmed / refund-revoke / first-earn-welcome
- Tenant config: tier thresholds, point earn rates, anti-abuse caps (already in services.py)
- 10 NEW customer-facing endpoints (reads + opt-out + redemption-propose + referral-invite)
- 6 NEW customer-facing events

### OUT
- Engineering implementation (backend exists per `apps/loyalty/`)
- Multi-currency points (rubles only MVP)
- Loyalty card export / printable
- Public leaderboard (anti-pattern §14)
- Customer-to-customer point gift (Phase 4+ if viable)
- Customer can convert points to cash (anti-pattern — loyalty currency, not money)
- Customer can buy points (anti-pattern — devalues earning)
- Tier downgrade based on inactivity — Phase 4+ if needed
- Cross-tenant point aggregation — privacy boundary
- Tier-specific service access («Любимый» only services) — Phase 4+ business decision
- Wellness module integration as point earner — Phase 3+ (would need wellness-engagement events)
- Push to non-platform channels (SMS, email) — depends on notification-preferences §15
- Salon-side gamification dashboards Phase 3+
- B2B loyalty (corporate accounts) — out of scope MVP

---

## 2. Strategic constraints — non-negotiable

### 2.1 Customer is the consumer; gamification calibrated
- Tiers ARE the structure (allowed; positive identity signal)
- Earning IS the reward (allowed; transparent)
- Progress toward next tier visible (allowed if framed as «осталось N посещений», NOT «вы отстаёте»)
- BUT NO streaks («3 недели подряд!»)
- BUT NO comparison («больше остальных на 20%»)
- BUT NO sound effects / confetti / firework animations at every earn
- BUT NO pressure («не пропустите бонус, осталось 5 дней!»)

This calibration differs from master-earnings (which forbids ALL gamification because master's WORK is operational reality not game). Customer's CONSUMER experience supports limited celebration.

### 2.2 NO inter-customer comparison
- ❌ «Top 10 customers this month»
- ❌ «Vы в top 5% по баллам»
- ❌ Average customer comparison
- ✅ Self-comparison «На прошлой неделе было 200, сейчас 250»

### 2.3 NO scarcity / urgency pressure
- ❌ «Только 24 часа — двойные баллы!»
- ❌ «Ваши баллы скоро сгорят»
- ❌ Limited-time promos that force visits
- ✅ Birthday bonus (calendar-bound, gentle)
- ✅ Long-gap return (positive welcome, not «we missed you, please come»)

### 2.4 Tier downgrade matters
Tier doesn't downgrade in MVP (per backend §3 — tier ratchets up only via EARN_VISIT). Q-CL5: if downgrade introduced Phase 4+, anti-shame framing strict.

### 2.5 Single-assistant identity
Per [`single-assistant-identity §2.4`](./single-assistant-identity.md): AI delivers loyalty news in conversational voice, NOT «BOT POINTS+50»:
- ✅ «У вас 250 баллов сейчас. Можете использовать на скидку до 750 ₽ в следующем визите.»
- ❌ «🎉 EARN +50 POINTS! Total: 250. Spend now?»

### 2.6 Opt-out preserves balance
Per Q-L12: customer opts out → balance retained + redemption allowed + new accrual stops. Customer can opt back in (no penalty).

### 2.7 Refund-revoke transparency
Per Phase 1.b backend: when booking refunded, EARN_VISIT points revoked. Customer sees clearly in history, AI explains in Bot DM §13.7.

### 2.8 Privacy
- Customer's points + tier visible only to customer
- Admin can see for support purposes (with audit)
- Master sees customer's points NEVER (no value to master; privacy)
- Cross-tenant: per-tenant strictly (Q-CO5 boundary)

### 2.9 Anti-abuse rules enforced
Per services.py:
- Min redeem: 50 points
- Cap: 30% of visit price
- Idempotency on booking_id (no double-earn on retry)

Customer NEVER sees raw rules — sees them naturally: «можно использовать от 50 баллов и до 30% от стоимости визита».

### 2.10 No manipulative «about to earn» framing
- ❌ «Запишитесь сейчас и получите 50 баллов»
- ❌ «Сегодня двойные баллы»
- ✅ Mention earning naturally: «Эта запись принесёт ~50 баллов»

### 2.11 Tier names neutral-positive
- Стартовый (NOT «новичок» — patronizing)
- Постоянный (positive identity)
- Любимый (warm but not «top tier» / «VIP» / «premium» — anti-status)

### 2.12 Customer wellness data isolated
Per [`core-wellness-profile.md`](./core-wellness-profile.md): wellness data is customer-only. Loyalty NEVER references wellness in earn/redeem logic. E.g., NO «earn 10 points for tracking mood today». Wellness is wellness; loyalty is loyalty.

### 2.13 Refund dispute interaction
Per [`customer-refund-dispute-ux §9`](./customer-refund-dispute-ux.md): when dispute resolves with refund ≥ 50%, REFUND_REVOKE event fires on loyalty. Customer sees clearly «возврат к записи отменил +50 баллов из визита».

### 2.14 Booking-completion latency
Points credit on `booking.completed` event (subscriber). Customer sees balance update within 5 min of admin marking booking COMPLETED. AI Bot DM §13.1 confirms with friendly tone.

---

## 3. Tier model alignment

### 3.1 Backend tiers (per `apps/loyalty/models.py`)

| Code | Display | Threshold (visits) |
|---|---|---|
| `starter` | Стартовый | 0-2 completed visits |
| `regular` | Постоянный | 3-9 completed visits |
| `favorite` | Любимый | 10+ completed visits |

Tier thresholds per services.py logic (Phase 2.a). Tenant override Phase 3+.

### 3.2 Tier benefits (UX-visible)

| Tier | Points per visit | Birthday bonus | Referral reward | Free correction available |
|---|---|---|---|---|
| Стартовый | 1×base | 100 pts | 100 pts | NO (full price) |
| Постоянный | 1.1×base | 150 pts | 150 pts | YES (1 per year) |
| Любимый | 1.25×base | 200 pts | 200 pts | YES (2 per year) |

Per-tenant configurable Phase 3+. Defaults from `apps/loyalty/services.py`.

### 3.3 Customer-side tier framing

- «Вы — Постоянный клиент» (positive identity statement)
- NEVER «Вы пока Стартовый» (deficit framing)
- NEVER «До Любимого осталось N посещений — не пропустите!» (pressure)
- OK «Постоянный клиент с {{join_date}}» (acknowledgment)
- OK on tier-change Bot DM §13.4 (gentle celebration)

### 3.4 Tier progress visibility

If customer at Стартовый with 1 completed visit:

```
Вы — Стартовый клиент.
2 визита до уровня Постоянный.
```

Not pushy, just informative. Customer can click to see what Постоянный unlocks.

---

## 4. Mini App «Бонусы» section

### 4.1 Position

Within Profile tab, prominent section near top (right after «Самочувствие»). If usage demands, promote to bottom nav tab Phase 3+.

### 4.2 Section home

```
┌────────────────────────────────────────┐
│ 🎁 Бонусы                                │
├────────────────────────────────────────┤
│ Ваш статус: Постоянный клиент            │
│ С нами с марта 2024                      │
│                                        │
│ ── Баллы ──                              │
│                                        │
│ 250 баллов                               │
│ ≈ 750 ₽ скидки в следующем визите       │
│                                        │
│ [Подробнее]                              │
│                                        │
│ ── До следующего уровня ──               │
│                                        │
│ Любимый клиент: 5 визитов из 10          │
│ ──────●●●●●─────────                    │
│                                        │
│ На уровне Любимый:                       │
│ • Бонус за визит +25%                    │
│ • День рождения 200 баллов               │
│ • 2 бесплатные коррекции в год           │
│                                        │
│ ── Пригласить друга ──                   │
│                                        │
│ Получите 150 баллов, когда друг          │
│ придёт впервые.                          │
│                                        │
│ [📤 Поделиться]                          │
│                                        │
│ ── ──                                    │
│                                        │
│ [📊 История начислений]                   │
│ [⚙ Настройки]                            │
└────────────────────────────────────────┘
```

### 4.3 Balance detail screen

```
┌────────────────────────────────────────┐
│ ← 250 баллов                             │
├────────────────────────────────────────┤
│ Что можно сделать с балансом:            │
│                                        │
│ • Использовать на скидку при             │
│   следующем визите                       │
│ • Минимум 50 баллов в один раз          │
│ • До 30% от стоимости визита             │
│                                        │
│ ── Как считаются ──                      │
│                                        │
│ 1 балл = 3 ₽ скидки                      │
│                                        │
│ За посещение: ~50 баллов                 │
│ (1.1× от базы как Постоянный клиент)    │
│                                        │
│ Можно ещё:                                │
│ • Оставить отзыв — +20 баллов            │
│ • Пригласить друга — +150 баллов         │
│ • День рождения — +150 баллов            │
│                                        │
│ [📊 История]                              │
└────────────────────────────────────────┘
```

### 4.4 History screen

```
┌────────────────────────────────────────┐
│ ← История начислений                      │
├────────────────────────────────────────┤
│ ── Текущий баланс: 250 ──                │
│                                        │
│ 17 мая  Маникюр у Анны        +50       │
│ 12 мая  Скидка применена      -30       │
│ 8 мая   Стрижка у Лены        +50       │
│ 5 мая   День рождения!        +150      │
│ ...                                     │
│                                        │
│ ── Возврат денег ──                      │
│ 1 мая   Возврат записи        -50       │
│ 1 мая   Маникюр у Анны        +50       │
│                                        │
│ (баллы за возвращённую запись           │
│ вычитаются автоматически)                │
│                                        │
│ Показать ещё ▾                           │
└────────────────────────────────────────┘
```

### 4.5 Settings

```
┌────────────────────────────────────────┐
│ ← Настройки бонусов                      │
├────────────────────────────────────────┤
│ Уведомления:                              │
│ ☑ Когда начислили баллы                  │
│ ☑ Подсказки о использовании              │
│ ☐ Маркетинговые акции                    │
│                                        │
│ ── ──                                    │
│                                        │
│ Не хочу участвовать в программе:         │
│ [Отключить бонусы]                        │
│                                        │
│ ⓘ Накопленные баллы сохранятся.          │
│ Можно использовать. Новые не будут      │
│ начисляться.                             │
└────────────────────────────────────────┘
```

«Отключить бонусы» → §11 opt-out flow.

---

## 5. Earning surfaces

### 5.1 EARN_VISIT (after booking completed)

Customer sees in Bot DM (per §13.1) + Mini App balance update within 5 min of `booking.completed`.

### 5.2 EARN_REFERRAL (friend's first visit)

Customer who invited friend sees in Bot DM (per §13.5) + history row. Friend separately sees their own loyalty surfaces.

### 5.3 EARN_BIRTHDAY (annual on customer.birthday_date)

Bot DM on morning of birthday (per §13.3) — auto-credited. Customer doesn't need to click anything.

### 5.4 EARN_REVIEW (per review left)

Per [`master-reviews-feedback-handoff.md`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md): customer leaves review → review.submitted → loyalty subscriber credits. Customer sees in Bot DM:

```
{{customer_first_name}}, спасибо за отзыв! +20 баллов за то, что
поделились впечатлениями.
```

Per Q-CL3: capped at 1 review-credit per booking (no farming).

### 5.5 EARN_RETURN (long-gap welcome)

If customer's last booking COMPLETED was N days ago (default: 90d) AND they book again:
- On booking.completed, EARN_RETURN credits +50 bonus points
- Bot DM (per §13.6)
- Calm framing — celebrate return without «we missed you so much, please come more often»

### 5.6 MANUAL_ADJUST (admin)

Admin adjusts balance (e.g., goodwill on dispute, system reconciliation). Customer sees in history with reason text. Bot DM only if delta > 100 points (avoid notification fatigue).

---

## 6. Redemption flow

### 6.1 At booking time

Customer creating a booking with > 0 balance sees redemption opportunity:

```
┌────────────────────────────────────────┐
│ ← Подтвердить запись                     │
├────────────────────────────────────────┤
│ Маникюр у Анны, 20 мая, 14:00            │
│ Стоимость: 2500 ₽                        │
│                                        │
│ ── Скидка по баллам ──                   │
│                                        │
│ У вас 250 баллов (можно списать          │
│ до 750 ₽ — 30% от стоимости)            │
│                                        │
│ Списать сейчас:                          │
│ ◯ Без скидки                             │
│ ⦿ 250 баллов = -750 ₽ (итого 1750 ₽)   │
│ ◯ Меньше:                                │
│   [_____] баллов                         │
│                                        │
│ [Записаться]                              │
└────────────────────────────────────────┘
```

### 6.2 Per-booking redemption rules

- Min 50 points per redeem (services.py constant)
- Cap 30% of visit_price_rub
- Customer can choose less than max if wants
- Atomic: redeem event written + balance bumped down + booking.discount_amount set + audit row

### 6.3 Anti-double-spend

Backend (services.redeem_points) handles atomicity. UX: balance shown is real-time. If race condition (rare), API returns 409 with «балс изменился, проверьте текущий — {{new_balance}}» — customer reconsiders.

### 6.4 Redemption after booking

NOT allowed MVP. Redemption is at booking creation only.

If customer wants to apply discount after booking made → cancel + rebook with redemption (manual but explicit).

### 6.5 Redemption on cancelled booking

If customer cancels booking after redemption applied:
- Cancellation triggers refund flow per [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md)
- Points re-credited via REFUND_REVOKE — net zero
- Audit captures

### 6.6 Customer Bot DM on redemption confirmation

Per §13.2.

---

## 7. Referral mechanic

### 7.1 Invite generation

Customer Mini App «Пригласить друга» → generates personalized link:

```
https://{{tenant_subdomain}}.platform/r/{{referral_token}}
```

Token bound to customer + tenant + created_at. Single-use per recipient (different friends use different links, OR same link with per-share tracking — per Q-CL10).

### 7.2 Share UX

```
┌────────────────────────────────────────┐
│ ← Пригласить друга                       │
├────────────────────────────────────────┤
│ Хотите пригласить кого-то к нам?         │
│                                        │
│ Когда друг впервые запишется и           │
│ придёт — получите 150 баллов            │
│ (~450 ₽ скидки).                        │
│                                        │
│ Друг тоже получит 100 баллов на         │
│ первый визит.                            │
│                                        │
│ ── Поделиться ──                         │
│                                        │
│ [📱 MAX]   [💬 Telegram]   [📧 WhatsApp]│
│                                        │
│ Или скопировать ссылку:                  │
│ [https://nataly-studio.../r/ABC123]      │
│ [📋 Копировать]                          │
│                                        │
│ ── Как это работает ──                  │
│                                        │
│ 1. Друг открывает ссылку                │
│ 2. Записывается на любую процедуру       │
│ 3. После первого визита — баллы у вас  │
│   обоих                                  │
└────────────────────────────────────────┘
```

### 7.3 Referee (friend) flow

Friend opens link → lands on tenant's Mini App with referral context. Mini App shows:

```
┌────────────────────────────────────────┐
│ Привет! Вас пригласила {{inviter_first_name}}│
├────────────────────────────────────────┤
│ На первый визит — 100 баллов в подарок │
│ (~300 ₽ скидки).                        │
│                                        │
│ {{Salon name}} — {{short description}}   │
│                                        │
│ [Записаться на первый визит]             │
└────────────────────────────────────────┘
```

Friend creates BotUser (per [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md)) with `referred_by_customer_id` linked.

### 7.4 Referral attribution + reward trigger

On friend's first `booking.completed`:
- `earn_referral` event credits inviter +150 (or tenant config)
- `earn_referral` event credits friend +100 (welcome bonus instead of birthday)
- Audit captures referral chain

### 7.5 Anti-abuse

- One referral reward per (inviter, referee) pair
- Inviter cannot refer themselves (server-side phone/MAX-ID match check)
- If friend's first booking is cancelled before completion → no referral credit
- If friend's first booking is refunded → referral NOT revoked (Q-CL11) — friend earned legitimately first; cancellation behavior is friend's matter
- Cap N referrals per customer per month? Q-CL12 — default 5/month

### 7.6 Bot DM touchpoints

- Inviter: §13.5 (friend just visited!)
- Friend: §13.10 (welcome bonus claimed)
- Inviter: optional periodic «никто пока не воспользовался» — NOT MVP (pressure)

### 7.7 Multi-tenant referral

Customer at salon A invites friend → friend lands at salon A only. Cross-tenant referral OUT of scope.

---

## 8. Birthday bonus

### 8.1 Trigger

Customer.birthday_date stored at profile (per [`customer-profile-management-ux.md`](./customer-profile-management-ux.md)). On midnight of birthday in customer's timezone:
- `earn_birthday` event credits tier-default amount (100/150/200)
- Bot DM §13.3

### 8.2 Anti-spam

Only once per customer per year (idempotency on (customer, year, EARN_BIRTHDAY)).

### 8.3 No birthday spam from salon
- AI Bot DM only ONCE (the credit message)
- No «happy birthday from {{master}}» automation (master may DM personally if they want)
- No «here are special birthday offers!»

### 8.4 Customer without birthday in profile

If `birthday_date` not set → no bonus. Customer can add anytime; bonus accrues next birthday.

### 8.5 Customer can hide birthday

If customer prefers privacy and clears birthday_date → bonus stops. Existing pre-credited bonuses remain.

---

## 9. Tier-change celebration

### 9.1 When triggered

EARN_VISIT moves customer's tier (Starter → Regular or Regular → Favorite). Backend writes TIER_CHANGED event + emits `customer.tier.changed` envelope (per Q-L7 alignment with backend handoff).

### 9.2 Bot DM (per §13.4)

```
{{customer_first_name}}, поздравляю — вы теперь Постоянный клиент 🌸

С этого момента:
• Бонус за визит +10%
• День рождения — 150 баллов
• 1 бесплатная коррекция в год

Открыть статус — [Бонусы].
```

Calm. No confetti animation in Bot DM. Just a warm message.

### 9.3 Mini App «Бонусы» badge

For 7 days post-tier-change, Mini App «Бонусы» section shows subtle highlight «Новый уровень» without animation.

### 9.4 NO downgrade celebration
Per §2.4: tiers don't downgrade MVP. Phase 4+ if added — silent only, NO «вы потеряли уровень». Just inform once via Bot DM with «как восстановить» action.

### 9.5 NO «almost there» pressure
- ❌ «Осталось 1 визит до Любимого — записывайтесь!»
- ✅ Status surface §4.2 shows progress passively

---

## 10. Refund-revoke transparency

### 10.1 When triggered

Per Phase 1.b backend: `booking.cancelled` (after `booking.completed`) → REFUND_REVOKE event reverses EARN_VISIT.

### 10.2 Bot DM (per §13.7)

```
{{customer_first_name}}, возврат за запись 17 мая обработан. Баллы (-50)
за этот визит вычтены — это автоматическая часть процесса.

Ваш баланс сейчас: 200 баллов.
```

### 10.3 Refund-dispute interaction

Per [`customer-refund-dispute-ux §9`](./customer-refund-dispute-ux.md):
- Refund ≥ 50% → attribution `billable=false` AND loyalty REFUND_REVOKE
- Refund < 50% → attribution unchanged; loyalty proportional revoke per Q-CL14
- Refund = 0 (admin denied) → no loyalty change

### 10.4 Negative balance edge case

If customer redeemed before refund → REFUND_REVOKE could push balance negative. Handle:
- Allow negative balance temporarily
- Balance displayed «-30 баллов» honestly to customer
- Future earns close gap
- No demand for customer to pay back (per master-earnings precedent §8.4)
- Customer cannot redeem while negative

---

## 11. Customer opt-out (Q-L12 alignment)

### 11.1 Trigger

Customer Mini App «Бонусы» Settings §4.5 → «Отключить бонусы»:

```
┌────────────────────────────────────────┐
│ Отключить бонусы?                        │
├────────────────────────────────────────┤
│ Что произойдёт:                          │
│ ✓ Накопленные 250 баллов сохранятся     │
│ ✓ Можете использовать на следующий       │
│   визит                                  │
│ ✓ За новые визиты баллы не начислятся   │
│ ✓ Можете включить обратно в любой        │
│   момент                                 │
│                                        │
│ Уверены?                                  │
│                                        │
│ [Отключить]   [Передумала]                │
└────────────────────────────────────────┘
```

### 11.2 Backend state change

`LoyaltyAccount.enrolled = False` + `opted_out_at = now()`. Subscriber checks before crediting.

### 11.3 Customer who opts out

- Existing balance retained
- Can still redeem at booking time
- New earns blocked (per services.py check)
- Bot DM §13.8 confirms

### 11.4 Re-enrollment

Customer can flip back. Settings → «Включить бонусы» → `enrolled=True`, `opted_out_at=NULL`. Existing balance still there. New earns resume immediately.

### 11.5 Customer asks AI «как отказаться»

AI routes to Settings:

```
Понятно. Можно отключить бонусы в Настройках → Бонусы. Сделать сейчас?
[Открыть настройки]
```

### 11.6 Customer account closure interaction

Per future customer-data-export-handoff (Tier 1 #3 in customer backlog): on customer account closure, loyalty balance forfeit per privacy hierarchy (data customer-only owned; closing deletes). Audit captures balance at closure.

---

## 12. AI Bot DM touchpoints — 8 templates

### 12.1 First-earn welcome (customer's first EARN_VISIT)

```
{{customer_first_name}}, у нас есть бонусы — за каждый визит вы их
зарабатываете.

После сегодняшней процедуры вам начислили +50 баллов.

Их можно использовать на скидку при следующем визите. Подробнее — в
приложении [Бонусы].
```

### 12.2 Regular earn (after first; subtle)

Subsequent EARN_VISIT — only Bot DM if customer has «notify on earn» preference enabled (default ON; can turn off per §4.5 settings):

```
+50 баллов за маникюр сегодня. Баланс: 250.
```

Short. No fanfare.

### 12.3 Birthday bonus

```
{{customer_first_name}}, с днём рождения! 🌸

Подарок — +150 баллов на ваш счёт (баланс: 400). Можно использовать на
следующий визит — будем рады видеть.
```

### 12.4 Tier-up

Per §9.2 above.

### 12.5 Referral converted (inviter sees)

```
{{customer_first_name}}, ваша подруга {{referee_first_name}} впервые
была у нас сегодня! Спасибо за рекомендацию — +150 баллов вам
(баланс: 400).
```

### 12.6 Long-gap return (welcome back)

After EARN_RETURN credits on first booking after 90+ day gap:

```
С возвращением, {{customer_first_name}}! Бонус за то, что вернулись —
+50 баллов (баланс: 250).

Без давления, просто приятно, что снова вместе.
```

### 12.7 Refund-revoke

Per §10.2 above.

### 12.8 Opt-out confirmation

```
{{customer_first_name}}, отключила бонусы. Ваши 250 баллов сохранены —
можно использовать в любой момент. Включить обратно — в настройках.
```

### 12.9 Quiet hours

Per [`notification-preferences-ux.md`](./notification-preferences-ux.md): all loyalty Bot DM respects customer's quiet hours. Birthday bonus sent at 10:00 customer-local, not midnight.

---

## 13. Admin-side surfaces

### 13.1 Admin Mini App «Лояльность» section

```
┌────────────────────────────────────────┐
│ 📊 Лояльность                            │
├────────────────────────────────────────┤
│ ── Сейчас по студии ──                  │
│                                        │
│ Клиентов с балансом: 47                  │
│ Общий баланс по всем: 12 350 баллов     │
│ За месяц начислено: 4 200                │
│ За месяц списано (скидки): 1 800         │
│                                        │
│ ── По уровням ──                         │
│ Стартовый: 23                            │
│ Постоянный: 18                           │
│ Любимый: 6                               │
│                                        │
│ ── Действия ──                            │
│ [Настройки начислений]                    │
│ [Найти клиента]                          │
└────────────────────────────────────────┘
```

### 13.2 Admin sees customer's loyalty state on customer profile

In customer detail view: tier, balance, recent events. Audit-only — admin cannot directly edit (uses manual_adjust API with reason).

### 13.3 Admin manual adjustment

For dispute goodwill / system reconciliation:

```
┌────────────────────────────────────────┐
│ Изменить баллы клиенту                   │
├────────────────────────────────────────┤
│ Клиент: Мария И.                         │
│ Текущий баланс: 250                       │
│                                        │
│ Изменение:                                │
│ ⦿ Добавить                                │
│ ◯ Списать                                 │
│ Сумма: [____] баллов                     │
│                                        │
│ Причина (обязательно):                    │
│ [_____________________________]        │
│                                        │
│ ⚠ Изменения > 500 баллов требуют         │
│   подтверждения от второго админа        │
│                                        │
│ [Применить]                              │
└────────────────────────────────────────┘
```

4-eye for > 500 points (anti-collusion, similar to refund-dispute §2.3).

### 13.4 Settings (tenant level)

```
┌────────────────────────────────────────┐
│ ← Настройки бонусной программы           │
├────────────────────────────────────────┤
│ ── Включение ──                          │
│ ☑ Бонусы включены                        │
│                                        │
│ ── Начисление ──                          │
│ Базовая ставка: 1 балл за ___₽ услуги    │
│   [50] ₽                                 │
│                                        │
│ ── Уровни ──                              │
│ Постоянный: после [3] визитов            │
│ Любимый: после [10] визитов              │
│                                        │
│ ── Бонусы ──                              │
│ За отзыв: [20] баллов                    │
│ День рождения (Стартовый): [100]         │
│ День рождения (Постоянный): [150]        │
│ День рождения (Любимый): [200]           │
│ Реферал (приглашающему): [150]           │
│ Реферал (приглашённому): [100]           │
│ Возвращение через 90+ дней: [50]         │
│                                        │
│ ── Списание ──                           │
│ Минимум списания: [50] баллов            │
│ Стоимость балла в рублях: [3] ₽          │
│ Максимум скидки за визит: [30] %         │
│                                        │
│ [Сохранить]                              │
└────────────────────────────────────────┘
```

Phase 3+ per Q-CL15: tenant config UI. MVP can be platform defaults from services.py.

---

## 14. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Confetti / fireworks / sound on every earn | Devalues + grates | §2.5 calm voice |
| «Не пропустите бонус!» urgency | Pressure §2.3 | Birthday auto-credit; no FOMO |
| Customer leaderboard | Privacy + comparison §2.2 | NEVER |
| Tier downgrade with shame framing | §2.4 anti-shame | No downgrade MVP; calm if Phase 4+ |
| «До Любимого осталось N — успейте» | Pressure | Passive «N visits to next level» |
| Customer-to-customer point gift | Manipulation vector | Phase 4+ if viable + safe |
| Customer can buy points | Devalues earning | NEVER |
| Customer can convert points to cash | Becomes money laundering | NEVER (loyalty currency only) |
| Cross-tenant point aggregation | Privacy boundary §2.8 | Per-tenant strictly |
| AI Bot DM on every single earn | Notification fatigue | First-earn welcome + opt-in subsequent §12.2 |
| Birthday spam (multiple greetings) | Annoying | Single Bot DM §8.3 |
| Pressure «Skip booking and lose points!» | Coercion | Earnings reflect actual visits only |
| Master sees customer's loyalty status | Privacy §2.8 | NEVER |
| Customer's points displayed on master's view | Anti-personalization | NEVER |
| Loyalty influences master's commission | Cross-domain confusion | NEVER (separate per master-earnings) |
| Earn points for wellness module engagement | Distorts wellness intent §2.12 | NEVER MVP |
| Customer pays for tier upgrade | Pay-to-win | NEVER |
| Auto-redeem on every booking without consent | Customer autonomy | Explicit per-booking choice §6.1 |
| Negative balance demands repayment | Customer trust §10.4 | Carry negative; future earns close |
| Multi-channel loyalty spam (SMS + email + push) | Channel fatigue | Bot DM only MVP |
| Salon owner sees customer's full event history without reason | Privacy creep | Audit-only access with masked PII |

---

## 15. Data model + endpoints

### 15.1 Models — REUSE existing

`apps.loyalty.LoyaltyAccount` + `LoyaltyEvent` per backend. No new models.

Additions to wellness profile / customer profile:
- `BotUser.opt_in_loyalty_notifications` (bool, default True) — per §12.2 / §4.5 settings
- `Referral` model (NEW) for §7 tracking

### 15.2 New `Referral` model

```python
class Referral(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=PROTECT, related_name='+')
    inviter = models.ForeignKey('identity.BotUser', on_delete=CASCADE, related_name='referrals_sent')
    referee = models.ForeignKey('identity.BotUser', null=True, blank=True, on_delete=SET_NULL, related_name='referrals_received')

    token = models.CharField(max_length=64, unique=True)
    # URL-safe random; bound to (inviter, tenant)

    STATUS_CHOICES = [
        ('issued', 'Link issued'),
        ('opened', 'Referee opened link'),
        ('signed_up', 'Referee created BotUser'),
        ('booked', 'Referee created first booking'),
        ('completed', 'Referee first booking COMPLETED — referral fired'),
        ('expired', 'Expired'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='issued')

    referee_first_booking = models.ForeignKey('booking.BookingRequest', null=True, blank=True, on_delete=SET_NULL, related_name='+')

    inviter_credited = models.BooleanField(default=False)
    inviter_credited_at = models.DateTimeField(null=True, blank=True)
    referee_credited = models.BooleanField(default=False)
    referee_credited_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    # Default: 90 days from creation

    class Meta:
        indexes = [
            Index(fields=['inviter', '-created_at']),
            Index(fields=['token']),
            Index(fields=['tenant', 'status']),
        ]
```

### 15.3 Customer endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/customer/loyalty/account` | Get own account + tier + balance §4.2 |
| GET | `/api/v1/customer/loyalty/events` | List own events (history) §4.4 |
| GET | `/api/v1/customer/loyalty/tier-benefits` | Tier benefits matrix §3.2 |
| POST | `/api/v1/customer/loyalty/redeem-propose` | Propose redemption for upcoming booking §6.1 |
| POST | `/api/v1/customer/loyalty/opt-out` | §11 |
| POST | `/api/v1/customer/loyalty/opt-in` | Re-enroll §11.4 |
| PATCH | `/api/v1/customer/loyalty/notification-preferences` | §4.5 |
| POST | `/api/v1/customer/loyalty/referrals` | Generate invite §7.1 |
| GET | `/api/v1/customer/loyalty/referrals` | List own referrals + status |
| GET | `/api/v1/customer/loyalty/referrals/<token>/landing` | Public landing for referee §7.3 (no auth) |

### 15.4 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/loyalty/overview` | §13.1 |
| GET | `/api/v1/admin/loyalty/customer/<id>` | Customer detail §13.2 |
| POST | `/api/v1/admin/loyalty/manual-adjust` | §13.3 |
| POST | `/api/v1/admin/loyalty/manual-adjust/<id>/4-eye-approve` | Second admin |
| GET | `/api/v1/admin/loyalty/settings` | Read tenant config §13.4 |
| PATCH | `/api/v1/admin/loyalty/settings` | Update tenant config |

### 15.5 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/loyalty/birthday-scan` | Cron daily — check birthdays + credit |
| POST | `/internal/loyalty/long-gap-return-check` | On booking.completed — check 90+ day gap |
| POST | `/internal/loyalty/referral/check-conversion` | On booking.completed — fire referral if first |

---

## 16. Events emitted

Per [`event-taxonomy.md`](./event-taxonomy.md) — loyalty domain (extending existing if any, else NEW §3.15 customer loyalty domain):

| Trigger | Event | Notes |
|---|---|---|
| Customer first earn | NEW: `customer.loyalty.first_earn` | tier_at_event |
| Tier changed | (existing) `customer.tier.changed` per backend Q-L7 | |
| Customer opted out | NEW: `customer.loyalty.opted_out` | balance_at_opt_out |
| Customer opted in | NEW: `customer.loyalty.opted_in` | |
| Redemption proposed | NEW: `customer.loyalty.redemption_proposed` | points, discount_amount |
| Redemption applied | (existing) covered by REDEEM event in backend | |
| Birthday credited | (existing) covered by EARN_BIRTHDAY event | |
| Referral issued | NEW: `customer.loyalty.referral_issued` | |
| Referral converted | NEW: `customer.loyalty.referral_converted` | inviter, referee |
| Negative balance detected | NEW: `customer.loyalty.negative_balance` | balance |

6 NEW + 4 backend-existing events. §16.

---

## 17. Acceptance criteria (engineering checklist)

- [ ] Customer Mini App «Бонусы» section §4.2 + balance detail §4.3 + history §4.4 + settings §4.5
- [ ] Redemption flow at booking creation §6.1 with min/cap enforcement
- [ ] Referral generation + landing + conversion tracking §7
- [ ] Birthday bonus daily cron + idempotency §8
- [ ] Long-gap return detection on booking.completed §5.5
- [ ] Tier-change Bot DM §9 calm framing
- [ ] Refund-revoke transparency §10
- [ ] Opt-out flow with balance preservation §11
- [ ] AI Bot DM 8 templates §12 with quiet-hours respect
- [ ] Admin overview + manual adjust + 4-eye §13
- [ ] 10 customer endpoints + 6 admin + 3 internal §15
- [ ] Cross-tenant 403 enforcement
- [ ] Master cannot see customer loyalty §2.8
- [ ] Referral anti-abuse: self-referral block, one reward per pair, cap §7.5
- [ ] Negative balance support §10.4
- [ ] All §14 anti-patterns avoided (no confetti, no leaderboard, no pressure copy)
- [ ] 6 NEW events §16
- [ ] PII rules enforced
- [ ] Tests: earn flow / redeem with cap / referral conversion / birthday idempotency / tier transition / opt-out preserves balance / refund-revoke / negative balance carry-forward / cross-tenant isolation / 4-eye manual adjust / admin cannot direct-edit balance

---

## 18. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-CL1** | Mini App «Бонусы» placement — Profile section or top-level tab? | Profile section MVP per §4.1; promote if usage. | UX | 🟢 |
| **Q-CL2** | Points-to-rubles ratio — fixed (1 = 3₽) or tenant-configurable? | Tenant-configurable Phase 3+ per §13.4. MVP default 1:3 per services.py. | PM + Eng | 🟢 |
| **Q-CL3** | Review-earned cap — once per booking or per review-version? | Once per booking_id (idempotency on review). Customer editing review within 7d doesn't earn again. | Eng | 🟢 |
| **Q-CL4** | Long-gap threshold — 90 days correct? | 90d MVP. Tune based on tenant data. Configurable Phase 3+. | UX + PM | 🟢 |
| **Q-CL5** | Tier downgrade Phase 4+ — frame? | If introduced: silent on degrade, Bot DM only with «как восстановить» action, NO «вы потеряли уровень». | Policy | 🟡 |
| **Q-CL6** | Negative balance display — show as negative or zero? | Honest negative §10.4 («-30 баллов»). Future earns close. NEVER demand repay. | UX + Eng | 🟢 |
| **Q-CL7** | Referral cap per customer per month — 5? | 5/month MVP. Anti-abuse threshold; could go higher with anti-fraud detection Phase 3+. | Policy | 🟡 |
| **Q-CL8** | Customer who opts out then customer's booking refunded — REFUND_REVOKE still fires? | YES — event log integrity. Balance affected. Customer sees neutral notification. | Eng | 🟢 |
| **Q-CL9** | Birthday in different timezone — credit at customer-local midnight or +10:00? | Customer-local timezone determined from MAX profile. Credit 10:00 customer-local per §12.9 quiet hours. | Eng | 🟢 |
| **Q-CL10** | Single referral link reusable for multiple friends OR per-friend? | Per-friend (per-share tracking enables accurate attribution + anti-abuse). Customer's UI generates new link per «Поделиться» tap. Underlying token tied to customer + tenant. | Eng + UX | 🟡 |
| **Q-CL11** | If friend's first booking refunded — referral credit revoked too? | NO — friend showed up legitimately. Cancellation post-completion is friend's matter. Inviter keeps reward. | Policy | 🟡 |
| **Q-CL12** | Admin manual_adjust 4-eye threshold — 500 points correct? | 500 MVP §13.3. Tune based on dispute incident data. | Policy | 🟢 |
| **Q-CL13** | Customer loyalty per tenant — confirm strict isolation? | YES per Q-CO5. Multi-tenant customer has separate accounts. Confirmed. | Privacy | 🟢 |
| **Q-CL14** | Refund < 50% — REFUND_REVOKE partial or full? | Per Q-CL14 (still open): proportional to refund amount preferred. E.g., refund 30% → revoke 30% of EARN_VISIT for that booking. Need backend confirmation. | Eng + Policy | 🔴 PRE-DEPLOY |
| **Q-CL15** | Tenant config Phase — MVP defaults from services.py OR per-tenant UI? | MVP defaults; per-tenant UI Phase 3+. Migration handles existing per-platform consistency. | PM | 🟢 |
| **Q-CL16** | Customer account closure → balance forfeit OR refundable? | Forfeit (loyalty currency, not money). Audit captures balance-at-closure. Per privacy hierarchy. | Policy + Privacy | 🔴 PRE-DEPLOY |
| **Q-CL17** | Wellness integration as earner Phase 3+? | OUT of scope MVP §2.12. Phase 3+ requires explicit policy decision (could distort wellness intent). | PM | 🟡 |
| **Q-CL18** | Notification preferences granularity — per event type or grouped? | Grouped MVP (§4.5: 3 toggle groups). Granular per event type Phase 3+. | UX | 🟢 |
| **Q-CL19** | Customer in dispute — points pending? | EARN_VISIT credits normally on booking.completed. Dispute resolution may trigger REFUND_REVOKE later per §10.3. NOT pending state in between. | Policy | 🟢 |
| **Q-CL20** | Master earns loyalty for own visits to their salon? | NO — masters are workers, not customers. Master booking own salon is operational, not consumer. Per master-earnings boundary. | Policy | 🟡 |

---

## 19. Cross-document linkage

- `apps/loyalty/models.py` + `services.py` — backend reality this spec wraps
- [`customer-profile-management-ux.md`](./customer-profile-management-ux.md) — birthday_date + opt-in toggle integration
- [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md) — referral landing flow §7.3
- [`customer-refund-dispute-ux.md §9`](./customer-refund-dispute-ux.md) — attribution/refund interaction §10.3
- [`master-reviews-feedback-handoff.md`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md) — review.submitted triggers earn §5.4
- [`single-assistant-identity.md`](./single-assistant-identity.md) — §2.5 voice consistency
- [`assistant-persona.md`](./assistant-persona.md) — voice rules for §12 templates
- [`conversational-ux-framework.md`](./conversational-ux-framework.md) — tone matrix
- [`notification-preferences-ux.md`](./notification-preferences-ux.md) — Bot DM channel + quiet hours
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) — loyalty during SUSPENDED state Phase 3+ Q
- [`event-taxonomy.md §3.15`](./event-taxonomy.md) — 6 NEW events §16
- [`master-earnings-handoff §2.2`](../handoffs/2026-05-19-master-earnings-handoff.md) — anti-gamification boundary (calibrated differently for customer §2.1 vs master)
- [`../decisions-log.md`](../decisions-log.md) — Q-CL1..Q-CL20

---

## 20. What this unblocks

- **Loyalty backend monetization** — accruing-without-visibility solved
- **Customer retention foundation** — visible reward + tier identity = stickiness
- **Referral acquisition channel** — viral growth via existing customer base
- **Customer NPS feedback loop** — review-earn ties reviews to tangible reward
- **Anti-churn signal** — long-gap return bonus = positive welcome-back
- **Tenant business model** — per-tenant settings give salon owner control
- **Privacy preservation** — opt-out flow honors customer autonomy
- **Multi-tenant integrity** — per-tenant strict isolation confirmed

## 21. What this does NOT unblock

- ❌ Customer-to-customer point gifting (Phase 4+)
- ❌ Buy / convert / cashout (anti-pattern)
- ❌ Wellness as earner (out of scope MVP)
- ❌ Public leaderboard
- ❌ Multi-currency
- ❌ Skip Q-CL14 partial-refund revoke policy (pre-deploy)
- ❌ Skip Q-CL16 account closure forfeit policy (pre-deploy)
- ❌ Tier-specific service access — Phase 4+
- ❌ Loyalty card export

---

## 22. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Loyalty backend lead (apps/loyalty/) | ☐ | 🔴 PRE-DEPLOY (Q-CL14 confirmation) |
| Mini App frontend (Бонусы section + redemption + referral share) | ☐ | |
| AI prompt eng (8 Bot DM templates + first-earn welcome + tier-up + birthday + referral-converted + return + opt-out + refund-revoke) | ☐ | |
| Refund-dispute steward (§10.3 alignment) | ☐ | 🔴 PRE-DEPLOY |
| Notification preferences steward (Bot DM channel + quiet hours §12.9) | ☐ | |
| Privacy / Legal (§2.8 + Q-CL13 cross-tenant + Q-CL16 closure forfeit) | ☐ | 🔴 PRE-DEPLOY |
| Founder (Q-CL2 ratio + Q-CL15 tenant config approach) | ☐ | |
| Accessibility (WCAG 2.2 AA on all surfaces) | ☐ | |

## Last verified
2026-05-19 (initial draft, 3 tiers + 8 Bot DM templates + redemption at booking time + referral mechanic + birthday + opt-out + refund-revoke + negative balance + admin 4-eye — locked)
