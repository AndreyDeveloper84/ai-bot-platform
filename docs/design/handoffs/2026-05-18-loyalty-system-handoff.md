# Loyalty System — Developer Handoff Package (Volna 4)

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Designer** | UX-architect skill |
| **Status** | Draft for review |
| **Phase** | Volna 4 (post-MVP retention compounding moat) |
| **Surfaces** | Customer Mini App + Customer bot DM + Owner web (Settings + Analytics) + MAX manager-bot (notifications) |
| **Scope** | Hybrid points+tiers loyalty: earning / redemption / tiers / anti-abuse / customer UX / owner config |
| **Screens** | 6 (3 customer-facing, 3 owner-facing) — extends existing flows |

## Foundation references

| Doc | Why it matters |
|---|---|
| [`2026-05-18-customer-first-time-handoff.md`](./2026-05-18-customer-first-time-handoff.md) | Extends F4 profile, B9 post-visit care, B11 feedback, B12 retention, B13 birthday, F3 my visits |
| [`attribution-policy.md`](../policies/attribution-policy.md) | Per-visit points = per BookingRequest event; refund mechanics affect points |
| [`memory/project_pricing_model_hybrid.md`](~/.claude/projects/.../memory/project_pricing_model_hybrid.md) | Loyalty discount = customer-side; we still bill 100 ₽ to salon for ai_direct |
| [`2026-05-18-analytics-dashboard-handoff.md`](./2026-05-18-analytics-dashboard-handoff.md) | Adds loyalty widget to dashboard; LTV column already in master breakdown |
| [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md) | Loyalty mechanics surface as «помощник» voice, not generic |
| [`decisions-log.md`](../decisions-log.md) | Q-CX7 referral tracking confirmed — this design ACTIVATES that flag |

---

## 0. Strategic context — why beauty vertical needs loyalty

### The retention math
- Beauty customers visit 8–16 times per year (depending on services)
- Average salon retention: ~60% per year industry-wide
- **A 10% retention improvement = 25–40% LTV increase** (compound effect)
- Loyalty programs in beauty average **23% retention improvement** when well-designed
- **For our platform**: every retained customer = continuing ai_direct bookings = recurring billing

### The competitive moat
- No-code bot constructors (Salebot Beauty, Boterra) don't have loyalty — they're transaction tools
- Customer attachment to «у меня в этом салоне баллы» = high switching cost = sticky for salon = sticky for our platform
- Loyalty mechanics powered by AI: «помощник знает, что у вас осталось 120 баллов» = magic moment

### What we are NOT building
- ❌ Complex multi-tier rewards (airline-style) — overengineered for beauty
- ❌ Cross-salon transferable points — privacy + competitive
- ❌ Cashback to actual money — legal complexity + payment processor risk
- ❌ Time-limited point expiration MVP — adds anxiety, defer to v1.1 if needed

### What we ARE building
- ✅ Simple linear earning (per visit + bonuses)
- ✅ 3 visible tiers (status markers)
- ✅ One redemption mechanic (discount on next visit)
- ✅ Anti-abuse built-in
- ✅ Owner-configurable (tenant flexibility)
- ✅ AI-surfaced through persona-conformed voice

---

## 1. Persona JTBDs

### Customer JTBD
> «Когда я уже несколько раз ходила в этот салон, я хочу чувствовать что это меня замечают и ценят — чтобы продолжать ходить именно сюда, а не пробовать другие.»

### Owner JTBD
> «Когда у меня уже есть постоянные клиенты, я хочу автоматически их удерживать через систему которая работает без моего участия — чтобы они не уходили к конкурентам.»

### Anti-JTBD (what we should NOT optimize for)
- Customers actively engineering point accumulation (gaming the system)
- Owners running constant promotions (devalues regular pricing)
- Complex strategic point management (this isn't a game)

---

## 2. Success metrics

| Metric | Target | Type |
|---|---|---|
| **Day-60 retention rate** for loyalty-enrolled customers vs non | ≥ +15 percentage points lift | North Star |
| Loyalty program opt-in rate (per tenant deploying) | ≥ 80% of active customers | Adoption |
| Average tier reached within 6 months for active customers | ≥ Постоянный (intermediate) | Engagement |
| Redemption rate (points-redeeming customers / total enrolled) | 25–50% (sanity range; <25 = unused, >50 = too easy) | Health |
| Referral conversion rate (referrers → referees who book first time) | ≥ 10% | Growth |
| Anti-abuse incidents per 1000 customers | < 2 | Safety |
| Owner satisfaction with loyalty UI (NPS proxy via survey at month 1) | ≥ 8/10 | UX |
| **Salon retention improvement** (tenant-level: salons WITH loyalty vs without) | ≥ +5% | Business |

---

## 3. Loyalty model — hybrid (points + tiers)

### Points (linear, transactional)

**Default earning rules** (tenant-configurable):
- **Per visit (any service)**: 5 points OR `floor(price_rub / 100)` — whichever higher
  - Example: 1 200 ₽ visit = 12 points; 500 ₽ visit = 5 points (minimum)
- **Referral**: 50 points to referrer when referee completes first visit (one-time per referee)
- **Birthday week**: 20 points + tier multiplier (see tiers below)
- **Review left** (post-visit B11): 10 points (one-time per visit)
- **Long-time return** (3+ months gap, then visits): 30 points «welcome back»

### Tiers (3 levels)

Status-based, derived from cumulative LTV — visible to customer + owner. NOT used for redemption math.

| Tier | Threshold | Status icon | Benefits (passive) |
|---|---|---|---|
| **Стартовый** | All new customers | (none) | Earn points; redemption available |
| **Постоянный** | 4+ completed visits OR 8 000 ₽ LTV | 🌿 | Birthday bonus 2× (40 pts); priority slot if 2 customers request same time |
| **Любимый** | 12+ completed visits OR 30 000 ₽ LTV | 🌹 | Birthday bonus 3× (60 pts); first dibs on new services; exclusive seasonal promo invites |

**Tier downgrade** policy: tier stays for 6 months after threshold breach. Inactive 12+ months → return to Стартовый.

### Redemption

**ONE mechanic only**: discount on next visit.

- Conversion: **1 point = 1 ₽ discount** (tenant-configurable)
- Per-visit cap: **30% of visit price** (anti-abuse)
- Minimum redemption: 50 points (avoid micro-redemptions)
- Applied at booking confirmation step

Example:
- Маникюр + гель-лак 2 200 ₽; customer has 250 points
- Available discount: min(250, 30% × 2200) = min(250, 660) = 250 ₽ off
- Final price: 2 200 − 250 = 1 950 ₽; points: 250 → 0

---

## 4. Earning mechanics in detail

### Trigger events

| Event | Points | Notes |
|---|---|---|
| `BookingRequest` completed (status=COMPLETED) | base = max(5, price/100) | Excludes no-show, cancelled |
| `Conversation` ended with referral attribution + new customer visit completed | 50 to referrer | Q-CX7 silent flag activates here |
| Birthday week (customer.birthday within 7 days of today AND first event this period) | 20 × tier_multiplier | Auto-credit |
| Review left (B11 flow, any rating ≥ 1★) | 10 | Tracks visit_id to prevent gaming |
| Long-return (first visit after 90+ day gap) | 30 | One-time per gap |

### Tier multiplier on bonus earnings
- Стартовый: 1× (no multiplier)
- Постоянный: 2× on birthday + long-return
- Любимый: 3× on birthday + long-return

### Edge case: refunded visit
- Visit refunded within 24h (per Q15) → points revoked (−base earning)
- Visit refunded later via dispute → points revoked retroactively
- No-show → points NOT earned (and may forfeit pending points — see anti-abuse)

---

## 5. Redemption mechanics in detail

### Where redemption appears
1. **Mini App booking confirmation step** (extends existing booking flow):
   ```
   Стоимость: 2 200 ₽
   ┌─[ Использовать баллы ]──────────────┐
   │ У вас 250 баллов = до 250 ₽ скидки  │
   │ [ Применить 250 баллов ]            │
   │ [ Применить часть... ▾ ]            │
   │ [ Без скидки ]                      │
   └────────────────────────────────────┘
   Итого: 1 950 ₽
   ```

2. **Bot DM redemption suggestion** (proactive):
   - Triggered when customer asks about price OR receives reminder
   - «У вас 250 баллов — можно списать до 250 ₽ при следующей записи»

### Redemption rules
- Min 50 points per redemption
- Max 30% of visit price (or full visit if < 30% × price)
- Cannot combine multiple visits
- Cannot redeem on tipping / extras outside catalog price
- Tenant can disable redemption temporarily (e.g., during sale) with banner notice

### Owner-configurable parameters
- Conversion rate (default 1 point = 1 ₽; range 0.5–2.0)
- Per-visit cap % (default 30%; range 10–50%)
- Minimum redemption (default 50; range 10–500)
- Maximum balance ceiling (default 5000 points; prevents long hoarding)
- Disable redemption period (e.g., «during sale, not redeemable»)

---

## 6. Anti-abuse mechanics

### Built-in safeguards
- **No-show penalty**: −5 points + booking cancellation (cycle-skip)
- **Late cancellation** (1h–24h): −3 points + audit log
- **Refund cascade**: refunded visit revokes earned points retroactively
- **Multi-account detection**: same phone across «different» customer profiles → flag + admin review
- **Referral abuse**: referee must be NEW (no prior `BookingRequest`); referee's first visit must complete (not no-show); referee phone hash must not match referrer's family/connected hashes (flag for review)
- **Self-referral**: referrer phone hash == referee phone hash → reject

### Limits
- **Referrals per customer per quarter**: 10 (prevents spam-referring fake accounts)
- **Manual point adjustments per tenant per month**: 50 (audit + CSM alert if exceeded)
- **Max balance ceiling**: 5000 points (configurable; prevents hoarding indefinitely)

### Dispute handling
- Customer disputes earning/redemption → routes through HUMAN_LOCKED conversation tier (already designed in ownership-policy)
- Owner can manually adjust with required reason → audit event `loyalty.manual_adjustment` with content_hash

---

## 7. State machine — customer LTV journey

```
NEW (just signed up) → Стартовый tier, 0 points
  ↓
First visit completed → +base points → still Стартовый
  ↓ (4 visits later OR LTV ≥ 8 000 ₽)
TIER UPGRADE → Постоянный
  - Trigger: bot message «Поздравляем! Вы теперь Постоянный клиент 🌿»
  - Tier benefits unlock
  ↓
Continue accumulating → maybe redeem some
  ↓ (12 visits OR LTV ≥ 30 000 ₽)
TIER UPGRADE → Любимый
  - Bot message «Поздравляем! Любимый клиент 🌹»
  - Exclusive features unlock
  ↓
Active engagement → optional redemption → continued retention
  ↓ (90 days no visit)
DORMANT (tier preserved)
  ↓ (180 days no visit)
TIER DOWNGRADE (1 level)
  ↓ (365 days no visit)
INACTIVE — full reset to Стартовый
  ↓
Re-engagement attempt: bot DM «Скучаем! У вас всё ещё N баллов»
```

---

## 8. Per-screen specs

### Screen L1 — Customer profile loyalty section (extends F4)

```
┌────────────────────────────────────┐
│ ← Профиль                          │
├────────────────────────────────────┤
│  Мария Иванова                     │
│  +7 ••• ••• 14 67                  │
│                                    │
│  ── Программа лояльности ──        │
│  🌿 Постоянный клиент              │
│                                    │
│  234 балла                         │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━       │
│  ↗ До Любимого: 4 визита           │
│                                    │
│  [Использовать при записи →]       │
│                                    │
│  Как зарабатывать:                 │
│  • Визит: от 5 баллов              │
│  • Отзыв: 10 баллов                │
│  • Друг по приглашению: 50 баллов │
│  • День рождения: 40 баллов (×2)   │
│                                    │
│  [Подробнее о программе]           │
│                                    │
│  ── История баллов ──              │
│  +12 баллов — Маникюр 15 мая       │
│  −80 баллов — Скидка 12 мая        │
│  +50 баллов — Подруга записалась  │
│  +10 баллов — Отзыв 8 мая          │
│  ...                               │
│  [Вся история →]                   │
└────────────────────────────────────┘
```

**Key UX:**
- Tier badge prominent
- Balance + progress to next tier
- Earning rules transparent (no «hidden mechanics» feel)
- History scrollable, recent at top

### Screen L2 — Booking redemption toggle (extends booking flow)

Added at booking confirmation step:

```
┌────────────────────────────────────┐
│ Подтвердить запись                 │
├────────────────────────────────────┤
│  29 мая, 11:30                     │
│  Маникюр гель-лак, 90 мин          │
│  Анна Петрова                      │
│                                    │
│  Цена: 2 200 ₽                     │
│                                    │
│  ┌─[ Ваши баллы: 234 ]───────────┐ │
│  │ Использовать:                  │ │
│  │  ⦿ 234 балла (макс. скидка)    │ │
│  │  ◯ Без скидки                  │ │
│  │  ◯ Указать сумму [____]        │ │
│  │                                │ │
│  │  Скидка: −234 ₽                │ │
│  └────────────────────────────────┘ │
│                                    │
│  Итого к оплате: 1 966 ₽           │
│                                    │
│  [ ✓ Записаться                ]   │
└────────────────────────────────────┘
```

**Defaults:**
- If customer has ≥50 points: «use max» pre-selected
- Tap «Без скидки» to keep points (save for bigger discount later)
- «Указать сумму» enables custom partial redemption

### Screen L3 — Post-visit points earned (extends B9)

Updated B9 template after visit:

```
Помощница студии:
Спасибо, что были у нас! Чтобы маникюр прослужил дольше:

• Первые 2 часа избегайте горячей воды
• Используйте перчатки при уборке
• Маслом для кутикулы — 1 раз в день, 2 недели

✨ Вы заработали: +22 балла за визит
Ваш баланс: 256 баллов

[Inline keyboard]
[💎 Профиль и баллы] [📅 Записать на коррекцию]
[💬 У меня вопрос]
```

Tier-up message (separate, when threshold crossed):
```
Помощница студии:
🌿 Поздравляем — вы Постоянный клиент!

Что это значит:
• Приоритет в записи при пересечении слотов
• Бонусные баллы в день рождения (×2)
• Эксклюзивные приглашения на новые услуги

[Что-то ещё?] [Профиль]
```

### Screen L4 — Owner settings: Лояльность config

`/settings/loyalty` — Owner only.

```
┌──────────────────────────────────────────────────────────────────┐
│ ← Настройки → Программа лояльности                              │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ Программа: [⦿ Включена  ◯ Выключена]                            │
│                                                                  │
│ ── Заработок баллов ──                                          │
│ За визит: [5] баллов или 1% от цены (что больше)                │
│ За отзыв: [10] баллов                                            │
│ За приглашённого клиента: [50] баллов                            │
│ В день рождения: [20] баллов                                     │
│                                                                  │
│ ── Уровни ──                                                     │
│ 🌿 Постоянный: [4] визита ИЛИ [8000] ₽ LTV                       │
│ 🌹 Любимый:    [12] визитов ИЛИ [30000] ₽ LTV                   │
│                                                                  │
│ ── Списание баллов ──                                            │
│ 1 балл = [1] ₽ скидки                                            │
│ Максимум скидки за визит: [30] %                                 │
│ Минимум для списания: [50] баллов                                │
│ Максимальный баланс на счету: [5000] баллов                      │
│                                                                  │
│ ── Особые периоды ──                                             │
│ ☐ Запретить списание баллов во время акций                       │
│   (будет показано клиенту: «Скидка временно недоступна»)        │
│                                                                  │
│ ── Превью для клиента ──                                         │
│ [показывает как будет выглядеть в Mini App]                      │
│                                                                  │
│ [Сбросить к умолчаниям]              [Сохранить настройки]       │
└──────────────────────────────────────────────────────────────────┘
```

### Screen L5 — Owner analytics widget (extends Analytics dashboard)

Added card in Analytics dashboard:

```
┌─[ Программа лояльности ]──────────────────────────────┐
│ Подключено: 87% клиентов (47 из 54 за 30 дней)        │
│ Среднее накопление: 134 балла                         │
│ Списали баллы: 28% клиентов                           │
│ Приглашений: 12 (8 успешных)                          │
│ ──                                                    │
│ Постоянные (🌿): 31 клиент (+5 за месяц)              │
│ Любимые (🌹):     7 клиентов (+2 за месяц)            │
│                                                       │
│ [Подробнее в программе лояльности →]                  │
└───────────────────────────────────────────────────────┘
```

Click → drill-down to loyalty admin panel with full breakdowns + top customers list (Owner only).

### Screen L6 — Manual adjustment modal (Owner / Admin)

For exceptional cases — customer dispute resolved positively, special gesture, anti-abuse correction.

```
┌────────────────────────────────────────────────────┐
│ Корректировка баллов клиента                   ✕   │
├────────────────────────────────────────────────────┤
│ Клиент: Мария Иванова (234 балла)                  │
│                                                    │
│ Тип:                                               │
│ ⦿ Добавить                                         │
│ ◯ Списать                                          │
│                                                    │
│ Количество: [_____] баллов                         │
│                                                    │
│ Причина (обязательно, для аудита):                 │
│ [Подарок за лояльность                          ]  │
│                                                    │
│ ⚠ Это действие будет в аудит-логе                  │
│   Подобные корректировки в этом месяце: 3 / 50     │
│                                                    │
│              [Отмена]  [Применить]                 │
└────────────────────────────────────────────────────┘
```

Limit: 50 manual adjustments per tenant per month. CSM alert if approached.

---

## 9. Attribution implications

### Loyalty discount and our billing
**Key principle**: loyalty discount is customer ↔ salon. We still bill salon 100 ₽ per ai_direct booking REGARDLESS of customer-side discount.

Example:
- Customer's manicure 2 200 ₽; uses 234 points → pays 1 966 ₽
- Salon's revenue: 1 966 ₽
- Our billing to salon: 100 ₽ (for ai_direct booking creation)
- Salon's net from this visit: 1 866 ₽

### Refund cascading
- Booking cancelled <1h → 100 ₽ auto-refund to salon (per Q15) AND points refunded to customer
- Booking marked no-show → 100 ₽ auto-refund to salon (per Q12-c) AND points NOT earned + small penalty (Q-L below)
- Booking refunded due to dispute (post-visit) → 100 ₽ refunded + points revoked + retroactive in history

### Anti-abuse via attribution
- Loyalty points only earned on `BookingRequest.status=COMPLETED` (not just CREATED)
- This prevents customer from booking → cancelling → re-booking to farm points
- Status transitions emit events that loyalty processor consumes

---

## 10. Cross-screen integration

| Source | Integration |
|---|---|
| **Customer first-time F4 profile** | Loyalty section added (Screen L1) |
| **Customer booking flow Mini App** | Redemption toggle added (Screen L2) |
| **B9 post-visit care template** | Points earned displayed (Screen L3) |
| **B11 feedback template** | After rating, mention «+10 баллов за отзыв» |
| **B12 retention proactive** | Mention current balance + «можно списать» |
| **B13 birthday template** | Tier multiplier shown («+40 баллов — двойной бонус») |
| **B14 promo template** | Optionally inform «использовать с баллами или скидка по промокоду» |
| **Analytics dashboard** | Loyalty widget (Screen L5) |
| **Settings → Лояльность** | Owner config screen (L4) |
| **Conversations C2 detail** | If customer mentions loyalty in conversation, AI surfaces «У них N баллов, текущий уровень» (admin context, NOT customer-facing through admin reply) |
| **Customer profile (admin side)** | Loyalty balance + tier + history visible to admins (per permissions: receptionist+ sees balance, no manual adjust) |
| **Master mobile (own customers view)** | Master sees own clients with loyalty markers («Постоянный», «Любимый») for relationship cue |
| **MAX manager-bot** | Tier-up celebration message to admin: «Мария стала Любимой клиенткой 🌹» |

---

## 11. MAX-specific patterns used

### Pattern: `clipboard` button for referral link
B14-like template can offer:
```
[clipboard] Скопировать ссылку для подруги
```
One-tap copy → customer pastes in their preferred messenger.

### Pattern: bot DM proactive («у вас N баллов»)
Embedded in existing reminder/retention bot DMs — NOT separate messages (per frequency policy).

### Pattern: Mini App start_param with redemption pre-fill
When customer taps «Использовать при записи» from L1 profile → opens Mini App booking with `start_param=use_points=234` → booking flow opens with redemption pre-applied.

### Pattern: shareMaxContent for referrals (already Q-CX7)
B11 «Поделиться с подругой» → uses `shareMaxContent` with deeplink containing `start_param=ref=CUSTOMER_HASH`. Backend tracks referrer when referee completes first visit.

### Pattern: group bot mention for celebrations
When customer reaches Любимый tier, optional MAX manager-bot message to team chat: «[Анна](max://user/123), Мария стала Любимой клиенткой — её любимый мастер это вы!»

---

## 12. Backend contracts

```
GET /api/v1/loyalty/customer/{customer_id}
  Response: {
    balance: int,
    tier: "starter" | "regular" | "favorite",
    tier_progress: { to_next_tier: { visits_needed?, ltv_needed? } },
    earning_rules: { ... cached tenant config },
    recent_events: [...]
  }

GET /api/v1/loyalty/customer/{customer_id}/history
  Query: ?limit=50&before=cursor
  Response: { events: [LoyaltyEvent], next_cursor }
  LoyaltyEvent: { id, type, points_delta, balance_after, reason, booking_id?, occurred_at }

POST /api/v1/loyalty/redemption/preview
  Body: { customer_id, booking_amount_rub, points_to_use }
  Response: { allowed: bool, discount_rub, new_balance, error?: str }

POST /api/v1/loyalty/redemption/apply
  Body: { customer_id, booking_id, points_to_use }
  Response: 200 { discount_applied, new_balance }

POST /api/v1/loyalty/manual-adjust
  Body: { customer_id, delta: int (positive or negative), reason: str }
  Response: 200 + audit event
  Rate limit per tenant per month

GET /api/v1/loyalty/settings
  Response: tenant config

PATCH /api/v1/loyalty/settings
  Body: { earning_rules, tier_thresholds, redemption_config }
  Owner only

GET /api/v1/loyalty/analytics
  Query: ?period=30d
  Response: {
    enrollment_rate, avg_balance, redemption_rate,
    referral_count, referral_conversion_rate,
    tier_distribution: { starter, regular, favorite },
    tier_changes_period: { upgrades, downgrades }
  }

GET /api/v1/loyalty/customer-search
  Query: ?q=...&tier=favorite&inactive=true
  Owner panel — find specific customers

POST /api/v1/loyalty/customer/{customer_id}/recompute
  Admin-only — recompute balance from event log (sanity check / fix)
```

### Event-driven processor
```
booking.completed → +base points
booking.cancelled_within_1h → 0 (no earning) OR refund if pre-earned
booking.no_show → 0 + penalty
review.left → +10 points (per visit_id once)
referral.referee_first_visit_completed → +50 points to referrer
birthday.matched → scheduled task fires +20×multiplier points
tier.threshold_crossed → tier update + celebration message + admin notification
points.expire (v1.1) → scheduled task for expirations
```

---

## 13. A11y considerations

- All point amounts in `<span aria-label="234 балла">234</span>` for SR
- Tier badges (🌿🌹) paired with text label «Постоянный» / «Любимый» — never icon-only
- Slider for partial redemption: keyboard accessible with arrow keys + `aria-valuetext` showing «X баллов = Y ₽ скидки»
- Progress to next tier as `role="progressbar"` with current/max values
- History list: each event has full context in `<li>` (date / type / amount / reason)
- Owner config: changes preview live; field changes announced via `aria-live="polite"`

---

## 14. Edge cases

- **Customer's first visit with referral**: referrer points credited only when referee's first visit status=COMPLETED. Pending until then.
- **Customer rapid-books then cancels** (point farming attempt): Q12 attribution + status check prevents earning until COMPLETED. Anti-abuse triggers if pattern detected (>3 cancellations / 7 days).
- **Multi-tenant customer with same phone** (Q-CO5 separate profiles): loyalty per tenant — no cross-salon transfer.
- **Customer deletes account (OP6)**: loyalty data deleted with rest. Salon notified «N points written off» for accounting.
- **Tenant disables loyalty mid-cycle**: existing points archived (visible to customer but unusable), banner explains, tenant can re-enable.
- **Tenant changes earning rules**: applies to future events only; historical points unchanged.
- **Customer expects discount on tip/extra**: redemption limited to catalog price; if tip is part of total, that's tenant POS logic, not ours.
- **Booking partial refund** (rare): proportional points adjustment if needed; default = NO change unless explicit admin action.
- **Customer is Любимый and disagrees with downgrade after inactivity**: tier downgrade transparent in history; if special-case, owner manual adjust restores.
- **Receptionist tries to manual-adjust >50 in month**: blocked with «Лимит достигнут, обратитесь к владельцу».
- **Customer's phone changes (number ported)**: loyalty migrates if customer_id same (account-driven, not phone-driven). Different phone? Manual migration request via OP6 flow.
- **Refund chain breaks balance to negative**: balance can't go negative; cap at 0 with «недосостояние» note in history; future earnings cover.
- **Edge: customer redeems exact amount = visit price**: allowed up to 30% cap; if cap permits 660 ₽ and customer has 660 points and visit is 2200 ₽ → final 1540 ₽; this is fine.
- **Edge: referee tries to refer themselves via second account**: detected via phone hash collision + flagged.

---

## 15. Anti-slop scan (12-point)

| # | Check | Status |
|---|---|---|
| 1 | Inter default | ✅ MAX UI / system |
| 2 | Purple gradient | ✅ |
| 3 | Glassmorphism | ✅ |
| 4 | Radius scale | ✅ |
| 5 | Emoji decoration | ⚠ 🌿🌹💎✨ — semantic tier badges + earning event. На проде: 🌿 → Lucide `leaf`, 🌹 → `flower-2`, 💎 → `gem`, ✨ → `sparkles`. KEEP tier emoji or icon visually distinct as it's status signal; just standardize to Lucide. |
| 6 | Centered+CTA | n/a |
| 7 | AI illustrations | ✅ no stock |
| 8 | Gradient overlay | ✅ |
| 9 | Copy specific | ✅ «За приглашённого клиента: 50 баллов» (not vague «get rewarded»); «До Любимого: 4 визита» (concrete); «+12 баллов — Маникюр 15 мая» (specific) |
| 10 | Avatars | n/a |
| 11 | Animation restrained | ✅ tier-up celebration has brief sparkle animation 400ms (reduced-motion fallback: just message); balance counter tick-up 300ms |
| 12 | Slate-on-slate | ✅ warm palette |

**11/12 ✅, 1 fix (emoji → Lucide on production).**

---

## 16. Phased delivery

### Phase 1 (MVP for loyalty) — 3 weeks
- Points earning per visit (base + min 5)
- Customer profile loyalty section (L1) — balance + history
- Booking redemption toggle (L2)
- Post-visit points earned message in B9
- Backend: events processor + balance tracking + audit
- Default owner config (no UI yet — hardcoded reasonable defaults)

### Phase 2 — 2 weeks
- Tiers + tier multipliers + tier-up celebrations
- Referral activation (Q-CX7 was prep; this completes the loop)
- Birthday bonus + multiplier
- Review-earned bonus
- Long-return bonus
- Tier UI in customer profile

### Phase 3 — 2 weeks
- Owner config UI (L4)
- Owner analytics widget (L5)
- Manual adjustment modal (L6) with monthly limits
- Anti-abuse pattern detection alerts
- Dispute UI integration with HUMAN_LOCKED tier

### Phase 4 (v1.1+)
- Points expiration (defer until data shows hoarding is real issue)
- Gift cards (separate feature, defer)
- Subscriptions (separate feature)
- Loyalty-driven retention proactive optimization (ML-tuned send timing)
- Cross-tenant aggregate insights for owner («Любимые клиенты обычно посещают 2× per month»)

---

## 17. Open questions (Q-L prefix)

| # | Question | Recommendation / lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-L1** | Default loyalty enabled OR opt-in for new tenants? | **Opt-in MVP** (Volna 4 is enhancement; don't force). Default `enabled=false` at tenant creation. Owner activates in Settings. Show suggestion banner after first 50 bookings. | PM | 🟡 |
| **Q-L2** | Tier downgrade — soft (notify customer) or silent? | Silent for first 90 days inactive; SOFT notification at 6 months «вы по-прежнему Любимый клиент» (kind nudge to return). Hard downgrade after 12 months silent. | PM | 🟢 |
| **Q-L3** | Points expiration timeline — never, 12 months, 24 months? | NEVER MVP (don't add anxiety to customer). Re-evaluate after 6 months data — if hoarding average >1000 points unspent, consider 24-month expiration with email warning. | PM | 🟢 |
| **Q-L4** | Tier upgrade celebration message — bot DM or only UI? | BOTH MVP. Bot DM as celebration moment + UI badge persists. Per persona doc voice. | UX | 🟢 confirmed |
| **Q-L5** | Loyalty enrollment — automatic on first booking, or explicit opt-in? | Automatic (no opt-out friction). Customer can opt-out in profile preferences. Defaults align with «нет хорошего повода не давать клиенту баллы». | PM | 🟡 |
| **Q-L6** | Per-master discount preferences — can favorite-master discount be higher? | NO MVP. Single redemption rule for all masters. Per-master is v2 idea (compound complexity vs simple value). | PM | 🟢 |
| **Q-L7** | Referral cap — 10/quarter or unlimited with diminishing returns? | 10/quarter HARD cap MVP (anti-spam). v1.1: introduce diminishing curve if salons complain. | PM | 🟢 |
| **Q-L8** | Discount applied to bot-attributed billing? Customer pays less; we still bill salon 100 ₽? | YES — bot work happens regardless of customer-side discount; this is documented in attribution-policy. Salon understands they pay us for AI work, not for revenue. | Confirmed | ✅ |
| **Q-L9** | Anti-abuse detection thresholds — who tunes? | Engineering owns initial thresholds; CSM signals from false positives → eng adjusts. Founder for first 50 tenants. | Eng + CSM | 🟢 |
| **Q-L10** | Loyalty dispute UI in HUMAN_LOCKED conversation tier — extends existing flow or new ticket type? | Extends existing — when customer disputes loyalty in chat, tier escalates per ownership policy. No new ticket type. | PM | 🟢 |
| **Q-L11** | Gift cards — Volna 4 sub-feature OR separate (v1.1+)? | **Separate, defer**. Gift cards add payment + legal + refund complexity. Not loyalty mechanic; it's e-commerce. | Founder | 🟢 |
| **Q-L12** | Customer-side opt-out from loyalty entirely — exists? | YES toggle in profile («не участвовать в программе лояльности»). Edge case but legal-safe. Existing points retained, no new earnings, redemption still allowed. | PM | 🟢 |

---

## 18. Implementation notes

### Database schema additions
```
LoyaltyAccount:
  customer_id (FK, unique per tenant)
  balance (int)
  tier (enum: starter/regular/favorite)
  tier_at (datetime — when entered current tier)
  enrolled (bool, default True)
  opted_out_at (datetime, nullable)

LoyaltyEvent:
  id, customer_id, tenant_id
  type (enum: earn_visit/earn_referral/earn_birthday/earn_review/earn_return/redeem/manual_adjust/refund_revoke)
  points_delta (int, signed)
  balance_after (int)
  reason (text)
  booking_id (FK, nullable)
  referrer_id (FK, nullable)
  metadata (JSON)
  occurred_at

LoyaltyConfig (per tenant):
  earning_rules JSON
  tier_thresholds JSON
  redemption_config JSON
  enabled (bool)

ReferralPending:
  referrer_customer_id
  referee_customer_id (nullable until accepted)
  referrer_first_share_token
  status (PENDING/COMPLETED/EXPIRED/REJECTED)
  expires_at
```

### Event processor
- Synchronous fast-path for `booking.completed` → +points → in same DB transaction
- Async for tier evaluation, celebration messages, referrer credits
- Reconciliation cron daily: balance vs event log consistency

### Caching
- Customer balance: cache 5 min (frequent reads)
- Tier config: cache per tenant (rare reads)
- Analytics: cache 1 hour

---

## 19. Cross-document linkage

- Foundation: [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md) — billing-vs-loyalty separation
- Customer flows: [`2026-05-18-customer-first-time-handoff.md`](./2026-05-18-customer-first-time-handoff.md) — multiple integration points
- Owner analytics: [`2026-05-18-analytics-dashboard-handoff.md`](./2026-05-18-analytics-dashboard-handoff.md) — widget added
- Permissions: [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4 — manual adjustment requires Owner/Admin
- Persona voice: [`assistant-persona.md`](../policies/assistant-persona.md) — all loyalty messages persona-conformed
- Decisions log: [`decisions-log.md`](../decisions-log.md) — Q-L1 to Q-L12 added

---

## 20. What this UNBLOCKS

- **Compounding retention moat** — measurable LTV increase per cohort
- **Differentiation from no-code competitors** — they don't do loyalty
- **Customer attachment** — switching cost via accumulated points
- **Salon switching cost via us** — if salon leaves us, they lose the loyalty system their customers use
- **Sales conversation** — «у нас встроена программа лояльности» = clear advantage in pitch
- **Multi-channel customer view** (eventually): customer's loyalty status is portable signal of value across surfaces
- **Future Wellness/Companion features** (Volna 5) — natural extension: «у вас 200 баллов за выпитую воду» (Ayla-style)

---

## 21. Sign-off

| Role | Approval | Date |
|---|---|---|
| Designer | ☐ | |
| Product | ☐ | |
| Engineering (FE) | ☐ | |
| Engineering (BE — event processor + cron + cache) | ☐ | |
| QA (anti-abuse scenarios) | ☐ | |
| Founder (Q-L1 default enabled, Q-L11 gift cards scope) | ☐ | |
| Legal (program rules disclosure — light, no consumer-protection complexity since это «бонус», не возврат денег) | ☐ | |
