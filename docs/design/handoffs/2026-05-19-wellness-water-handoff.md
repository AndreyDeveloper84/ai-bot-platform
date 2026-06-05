# Wellness Water Tracker Module — engineering handoff

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Engineering-ready — Phase 2 wellness module №2 (after Mood)
**Reads:** [`../policies/ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md), [`../policies/ayla-memory-and-personalization.md`](../policies/ayla-memory-and-personalization.md), [`../policies/wellness-input-modules.md`](../policies/wellness-input-modules.md) §3, [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md), [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md), [`../policies/core-user-states.md`](../policies/core-user-states.md), [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md), [`../policies/information-architecture.md`](../policies/information-architecture.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md), [`../policies/ayla-emergency-fallback-policy.md`](../policies/ayla-emergency-fallback-policy.md), [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md)

> Ports [wellness-input-modules §3](../policies/wellness-input-modules.md#3-module-2--water-tracker) Water Tracker module to engineering-ready handoff. Phase 2 (after Mood ships). Daily multi-tap habit loop; smart reminders; Mini App quick chip + Самочувствие section; cross-correlation with body state.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](../policies/ayla-identity-and-brand.md) memory 2026-05-19: water tracker data is **Ayla's memory of user** — cross-tenant. `HUMAN_LOCKED` → emergency fallback per [`ayla-emergency-fallback-policy §3`](../policies/ayla-emergency-fallback-policy.md). AI voice uses Ayla per [`ayla-identity-and-brand §2`](../policies/ayla-identity-and-brand.md).

---

## 0. Why this exists

### The gap

[`wellness-input-modules §3`](../policies/wellness-input-modules.md#3-module-2--water-tracker) describes Water Tracker strategically (what it captures, why it matters, UX sketch, anti-patterns) but doesn't specify:
- Activation flow (when customer first sees consent)
- Smart reminder algorithm (timing rules + throttling)
- Model fields + types + validators
- Mini App layout + state handling
- API contracts
- Per-state behavior matrix
- Daily target customization + units (мл vs стаканы per Q-WI2)
- Cross-correlation with body state implementation
- Events emitted

Engineering improvisation = drift from strategic intent (especially around anti-pattern «no streak shame»).

### The promise

Single source for `apps/wellness/` water module (extends existing app from Mood; same `WellnessModuleConsent` reuse). Engineering reads + ships.

---

## 1. Scope

### IN
- `WellnessWaterEvent` model (extends `apps/wellness/`)
- Activation Paths A + B (Path C emotion-trigger deferred Phase 2.5+)
- Consent dialog with daily target + reminder config
- Bot DM smart reminders (max 2-3 per day, respect DND)
- Bot DM quick log (customer types «попила воды» → AI logs)
- Mini App quick chip on Главная (state-adaptive)
- Mini App Самочувствие section with progress bar + log + history
- Per-state behavior matrix
- Daily target customization (default 2000 ml; configurable 500-5000)
- Both units: стаканы (250 ml) AND ml (per Q-WI2 lean — both supported)
- 5 API endpoints
- Events emitted per [event-taxonomy §3.6](../policies/event-taxonomy.md#36-wellness-domain)
- Privacy enforcement (customer-only at API)
- Wellness Profile integration (Layer 6 Nutrition)
- Smart reminder algorithm with throttling + non-response auto-pause

### OUT
- Hydration medical claims / kidney health framing (out of scope ethically — we're not medical)
- Pee tracking (out of scope MVP)
- Multi-fluid (coffee/tea/etc.) — Phase 3+ if customer demand
- HealthKit / Google Fit integration (Phase 4+ wearables)
- Tenant-side aggregate (privacy boundary)
- Customer-pays tier gating (free forever per Q-WI12)

---

## 2. Activation flow

### 2.1 Eligibility (gates) — same as Mood §3.1

Customer cannot activate Water if any:
- `consent.ai_messaging = false` (exception: Path A self-discovery works without proactive consent)
- `core_user_state ∈ {DORMANT, HUMAN_LOCKED active conversation}`
- Tenant in PAUSED / SUSPENDED billing state per [`tenant-suspension-pause-ux §3.1`](../policies/tenant-suspension-pause-ux.md)
- Customer's MAX account suspended

### 2.2 Activation triggers (Phase 2 launch)

**Path A — Self-discovery in Mini App** (always available):
Customer navigates Профиль → Самочувствие → Вода card → toggle ON → consent dialog §3.

**Path B — Mood module synergy** (recommended in onboarding flow):
After customer activates Mood module AND uses it ≥7 days AND has shown mood scores indicating wellness interest (any mood data point exists), AI suggests ONCE:
```
Заметила, что отмечаете самочувствие. Помощник может ещё напоминать пить воду — связано с тонусом и энергией.

[Попробовать]   [Не сейчас]
```
Same Path B pattern as Mood — one offer, no re-offer.

**Path C — Emotional trigger (Phase 2.5+)**:
Customer messages «болит голова», «устала», «вялость» → AI suggests water module among options. Same opt-out cap.

### 2.3 Activation events emitted (per [event-taxonomy §3.6](../policies/event-taxonomy.md#36-wellness-domain))

- `wellness.consent.module.granted` with `module_name='water'`, `granted_via='profile_settings' | 'mood_synergy_offer'`

---

## 3. Consent dialog

### 3.1 Single-screen dialog (simpler than Mood §3.2 because shorter)

```
┌────────────────────────────────────────┐
│ Отслеживать воду?                      │
├────────────────────────────────────────┤
│ Я буду:                                │
│   • Запоминать сколько вы выпили        │
│   • Напоминать пить (если согласны)    │
│   • Показывать прогресс к цели          │
│                                        │
│ ── Цель на день ──                      │
│                                        │
│ ◯ Стакан (250 мл)  · 8 шт = 2 л          │
│ ◉ Кружка (350 мл) · ~6 шт = 2.1 л       │
│ ◯ Большой (500 мл) · 4 шт = 2 л          │
│ ◯ Просто в мл: [_______ мл]             │
│                                        │
│ Сколько хотите выпивать в день?         │
│ [────●─────] 2000 мл                    │
│ (можно менять потом)                   │
│                                        │
│ ── Напоминания ──                       │
│                                        │
│ ⦿ Да, напоминай (умные времена)         │
│ ◯ Нет, отмечу сама(сам)                 │
│                                        │
│ Если да — буду писать максимум 2-3      │
│ раза в день между 9:00 и 21:00.         │
│                                        │
│ ── Что важно ──                         │
│                                        │
│ ✓ Данные видите только вы              │
│ ✓ Студия НЕ видит ваши данные          │
│ ✓ Выключить — в любой момент            │
│                                        │
│ [Не сейчас]      [Согласна, попробуем] │
└────────────────────────────────────────┘
```

### 3.2 Pre-selected defaults

- **Default size preset**: «Кружка 350 мл» (most common Russian household)
- **Default daily target**: 2000 мл
- **Reminders**: ON by default (sensible; can opt out)
- **Reminder window**: 9:00–21:00 customer's TZ

### 3.3 Outcomes — same pattern as Mood §3.5

#### Tap «Согласна, попробуем»
- Create `WellnessModuleConsent(customer, tenant, module_name='water', granted=True, granted_via='profile_settings', config=<chosen>)`
- Config JSON:
```json
{
  "daily_target_ml": 2000,
  "preferred_size_preset": "mug",
  "preferred_size_ml": 350,
  "reminders_enabled": true,
  "reminders_start_hour": 9,
  "reminders_end_hour": 21,
  "max_reminders_per_day": 3,
  "unit_display_preference": "glasses"
}
```
- Emit `wellness.consent.module.granted` event
- Navigate to Самочувствие → Вода section §6.1
- Show first-use toast: «Готово. Если что-то не подойдёт — выключить можно в настройках.»

#### Tap «Не сейчас»
- NO record created
- If activation triggered by Path B (mood synergy offer): mark `customer.water_offer_declined_at = NOW`; never re-offer
- If Path A self-discovery: customer can re-open consent dialog by tapping toggle again

### 3.4 Re-activation after revoke

Per Mood §3.6 same pattern.

---

## 4. Data model

### 4.1 `WellnessWaterEvent`

```python
class WellnessWaterEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='water_events')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    recorded_at = models.DateTimeField()  # customer's tap time

    amount_ml = models.IntegerField(validators=[MinValueValidator(50), MaxValueValidator(2000)])
    # Per-event cap: 2000 ml (no single «I drank 5 liters» fake entries)

    SIZE_PRESET_CHOICES = [
        ('small', 'Стакан · 250 мл'),
        ('mug', 'Кружка · 350 мл'),
        ('medium', 'Большой стакан · 500 мл'),
        ('bottle', 'Бутылка · 750 мл'),
        ('custom', 'Custom amount'),
    ]
    size_preset = models.CharField(max_length=16, choices=SIZE_PRESET_CHOICES)

    SOURCE_CHOICES = [
        ('bot_reminder', 'Bot DM reminder response'),
        ('bot_freeform', 'Bot DM free-form mention (e.g., «попила воды»)'),
        ('mini_app_quick', 'Mini App quick chip on Главная'),
        ('mini_app_section', 'Mini App Самочувствие → Вода section'),
    ]
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['customer', '-recorded_at']),  # daily + history view
            Index(fields=['tenant', 'recorded_at']),  # analytics aggregation
        ]
```

### 4.2 Reuses `WellnessModuleConsent` (from Mood handoff)

Config JSON for water module per §3.3:
```python
{
  "daily_target_ml": int,
  "preferred_size_preset": str,
  "preferred_size_ml": int,
  "reminders_enabled": bool,
  "reminders_start_hour": int,
  "reminders_end_hour": int,
  "max_reminders_per_day": int,
  "unit_display_preference": "glasses" | "ml"
}
```

### 4.3 Migration

```python
# Migration N (after Mood lands):
# - Add WellnessWaterEvent model
# - No backfill — fresh table
```

---

## 5. Per-state behavior matrix

When AI may send water reminders per customer state. All gated by `WellnessModuleConsent.granted = True` first.

| Customer state | Smart reminders fire? | Why |
|---|---|---|
| DISCOVERED | NO | No relationship; activation gate prevents |
| EXPLORING | NO (unless explicitly Path A activated) | Customer still discovering |
| PROBLEM_SEEKING | YES if activated | Water data helps overall picture |
| READY_TO_BOOK | YES if activated | Background continues |
| POST_VISIT | YES if activated | Good moment for activation OR continued use |
| ACTIVE_REGULAR | YES if activated | Steady state |
| AT_RISK_DRIFTING | YES if activated AND last water_event < 7 days | Respect drift after 7d silence |
| AT_RISK_DRIFTING + no water 7+ days | NO; auto-pause module per §7.3 | Respect deliberate silence |
| DORMANT | NO (terminal, no proactive) | Per ownership-policy |
| HUMAN_LOCKED active conversation | NO | Admin owns |
| HUMAN_LOCKED inactive (resolved) | YES if activated | Resume normal |

---

## 6. Mini App «Самочувствие» → Вода section

### 6.1 Section layout (when daily target NOT reached)

```
┌────────────────────────────────────────┐
│ 💧 Вода сегодня                         │
├────────────────────────────────────────┤
│ ━━━━━━━━━░░░░░░░░  1 750 / 2 000 мл    │
│ 87% от цели                            │
│ Осталось: 250 мл (1 стакан)             │
│                                        │
│ ── Быстрый ввод ──                      │
│                                        │
│ [+ Стакан 250 мл]                       │
│ [+ Кружка 350 мл]                       │
│ [+ Большой 500 мл]                      │
│ [+ Бутылка 750 мл]                      │
│ [+ Custom ──── мл]                       │
│                                        │
│ ── Сегодня по часам ──                  │
│                                        │
│ 09:00 ━ 12:00 ━ 14:00 ━ 16:00          │
│ 💧    💧💧   💧     💧                  │
│                                        │
│ ── Цель на день ──                      │
│                                        │
│ [2 000 мл ▾] [Изменить]                 │
│                                        │
│ ── Тренд за неделю ──                   │
│                                        │
│ [простой бар-чарт 7 дней]              │
│                                        │
│ [Подробнее →]                          │
└────────────────────────────────────────┘
```

### 6.2 When daily target REACHED

```
┌────────────────────────────────────────┐
│ 💧 Вода сегодня — готово                │
├────────────────────────────────────────┤
│ ━━━━━━━━━━━━━━━━━━━  2 100 / 2 000 мл  │
│ ✓ Цель достигнута                       │
│                                        │
│ Хотите ещё? Можно отметить:            │
│                                        │
│ [+ Стакан 250 мл]                       │
│ [+ Кружка 350 мл]                       │
│                                        │
│ ── Сегодня по часам ──                  │
│ ... как §6.1                           │
└────────────────────────────────────────┘
```

**No celebration animation, no streak counter** (anti-pattern §13). Quiet acknowledgment + invitation to keep going if customer wants.

### 6.3 Quick-chip on Главная (state-adaptive)

State-adaptive home per [`information-architecture.md`](../policies/information-architecture.md) shows quick-add chip when:
- Water module activated
- Current hour ≥ reminders_start_hour (don't pre-empt morning)
- Current daily progress < 100% target

```
┌────────────────────────────────────────┐
│ 💧 1 250 / 2 000 мл сегодня             │
│ [+ Стакан]  [+ Кружка]  [+ Большой]    │
└────────────────────────────────────────┘
```

Tap = save with `source='mini_app_quick'`; chip updates progress; remains visible (vs Mood dismisses after single tap).

### 6.4 Insights deep-dive («Подробнее →»)

```
┌────────────────────────────────────────┐
│ ← Вода — подробнее                      │
├────────────────────────────────────────┤
│ ── За 7 дней ──                         │
│                                        │
│ Среднее в день: 1 850 мл                │
│ Цель: 2 000 мл                         │
│ Дней с целью: 4 из 7                    │
│                                        │
│ [линейный chart 7 дней с целевой линией]│
│                                        │
│ ── Что заметно ──                       │
│                                        │
│ В будни пьёте больше (среднее 2 100),  │
│ в выходные меньше (среднее 1 400 мл).  │
│                                        │
│ ── Период ──                            │
│ [7 дней]  [30 дней]                    │
└────────────────────────────────────────┘
```

### 6.5 Phase 1 simple-rules «Что заметно» logic

Similar to Mood §8.3 but water-specific:
- If avg < 60% of target over period: «Воды пьётся меньше обычного. Может быть стоит вспоминать чаще.»
- If avg > 110% of target: «Цель уверенно достигаете. Можно поднять?» → [«Поднять до X мл»] button
- If max(day) - min(day) > 50% range: «Сильно разные дни. Полезно стабильнее.»
- Pattern «weekday vs weekend»: surface if delta > 30%
- Otherwise: «Стабильно за период.»

No medical claims. No fear-mongering.

---

## 7. Bot DM smart reminders

### 7.1 Trigger conditions (ALL must be true)

- `WellnessModuleConsent.granted = True`
- `config.reminders_enabled = true`
- Current time in `[reminders_start_hour, reminders_end_hour]` customer's TZ
- Per-state allowance per §5
- Daily reminder count < `max_reminders_per_day` (default 3)
- Last water_event > 2.5 hours ago AND today's remaining_ml > 500 ml
- No DND active per [`notification-preferences-ux §7`](../policies/notification-preferences-ux.md#7-dnd-do-not-disturb-windows)
- Not within 30 min of customer's last bot interaction (avoid double-pinging)

### 7.2 Bot DM template

**Voice anchor**: Calm + Warm-mild + Concise.

```
💧 {{N}} часа без воды. Глоток?

[+ Стакан 250]  [+ Кружка 350]  [+ Большой 500]
[Не сейчас]
```

After response:
- Tap «+ Стакан/Кружка/Большой» → save event + brief acknowledgement (next line)
- Tap «Не сейчас» → silent dismiss; doesn't penalize
- No response → next reminder window per throttle §7.3

### 7.3 Post-log acknowledgement (varies by progress)

| Progress after this log | Response message |
|---|---|
| ≤ 50% of daily target | `«💧 +{{amount}} мл. Сегодня {{progress}} / {{target}} — продолжайте.»` |
| 50-99% of daily target | `«💧 +{{amount}} мл. Сегодня {{progress}} / {{target}}. Уже хорошо.»` |
| Reached target this log (first time today) | `«💧 +{{amount}} мл. Цель {{target}} мл сегодня — готово.»` |
| Already past target | `«💧 +{{amount}} мл записала.»` (brief — don't celebrate further) |

**Forbidden**:
- ❌ «Молодец!» / «Так держать!»
- ❌ Streak counter («4 дня подряд!»)
- ❌ Trophies / badges
- ❌ Comparison with other customers

### 7.4 Throttle (per [`notification-preferences §6`](../policies/notification-preferences-ux.md#6-frequency-caps--throttling) extended for water)

| Consecutive day-quotas reached without response | Next-day behavior |
|---|---|
| Day 1-2 normal (full max_reminders_per_day) | Normal |
| Day 3 with 0 responses to any reminder | **Reduce to 1 reminder** |
| Day 5 with 0 responses across all reminders | **Pause reminders 7 days** + DM: «Не вижу ваших отметок — поставила напоминания на паузу. Включить обратно — в настройках Самочувствия.» |
| After 7-day pause | If no manual re-enable, stays paused |

Reset to «day 1» on any log/response.

### 7.5 Smart timing algorithm

Phase 2 MVP: **rule-based** (not ML).

Rules:
1. First reminder: 2.5h after start of reminder window (e.g., 11:30 if start=9:00)
2. Second reminder: 4h after first
3. Third reminder: 4h after second OR 1h before reminder window end
4. Adjust based on actual logs:
   - If customer logged within last 2h, skip next scheduled reminder
   - If customer's typical pattern shows mornings = 50% of intake, weight morning reminders higher

Phase 4+ ML: learn per-customer optimal timing from history.

---

## 8. Bot DM free-form quick log

Customer can message bot anytime «попила воды» / «выпила стакан» / «700 мл воды» — AI parses + logs.

### 8.1 Recognition patterns

NLU parses:
- Verb cues: «попила», «выпила», «пью», «глоток»
- Amount cues: «стакан» (250), «кружка» (350), «большой/большую» (500), «бутылка» (750), explicit numbers «{N} мл»
- Default fallback: «стакан» (250) if amount unspecified

### 8.2 Bot response after parsing

If parsed successfully:
```
💧 +{{amount}} мл. {{progress_acknowledgement_per §7.3}}
```

If amount ambiguous:
```
Сколько выпили?
[Стакан 250]  [Кружка 350]  [Большой 500]  [Другое количество]
```

If verb/intent unclear:
```
Хотите отметить воду? [Да, отметить]  [Нет, что-то другое]
```

### 8.3 Forbidden in free-form parsing

- ❌ Parse «много воды» as 5+ liters (out of plausible range; ask clarification)
- ❌ Auto-log without confirmation if low confidence
- ❌ Use this path to upsell wellness modules

---

## 9. API contracts

### 9.1 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/customer/wellness/water` | Customer | Log water event |
| GET | `/api/v1/customer/wellness/water` | Customer | List events (paginated, date range) |
| GET | `/api/v1/customer/wellness/water/today` | Customer | Today's events + progress |
| GET | `/api/v1/customer/wellness/water/summary` | Customer | Aggregate summary (7d/30d) |
| PATCH | `/api/v1/customer/wellness/consent` | Customer | Update water config (daily_target_ml, reminders_enabled, etc.) — reuses Mood pattern |

### 9.2 POST `/api/v1/customer/wellness/water`

**Request**:
```json
{
  "amount_ml": 350,
  "size_preset": "mug",
  "source": "mini_app_quick"
}
```

**Validation**:
- Customer has `WellnessModuleConsent.granted = True` for `module_name='water'`
- `amount_ml` ∈ [50, 2000]
- `size_preset` in choices
- `source` in choices
- Anti-spam: max 50 events per customer per day (Q-WW1)

**Response** (201):
```json
{
  "id": "uuid",
  "recorded_at": "2026-05-19T14:32:00Z",
  "amount_ml": 350,
  "today_progress_ml": 1850,
  "today_target_ml": 2000,
  "today_percent": 92,
  "target_reached_this_log": false
}
```

### 9.3 GET `/api/v1/customer/wellness/water/today`

**Response** (200):
```json
{
  "target_ml": 2000,
  "consumed_ml": 1850,
  "percent": 92,
  "remaining_ml": 150,
  "events": [
    {"id": "...", "recorded_at": "...", "amount_ml": 350, "size_preset": "mug"},
    {"id": "...", "recorded_at": "...", "amount_ml": 250, "size_preset": "small"},
    ...
  ],
  "hourly_buckets": {
    "09:00": [350],
    "12:00": [250, 350],
    "14:00": [500],
    "16:00": [400]
  }
}
```

### 9.4 GET `/api/v1/customer/wellness/water/summary`

**Query**: `period_days` (default 7, max 30)

**Response** (200):
```json
{
  "period_days": 7,
  "avg_ml_per_day": 1850,
  "median_ml_per_day": 1900,
  "target_ml": 2000,
  "days_target_reached": 4,
  "days_total_with_data": 7,
  "chart_series": [
    {"date": "2026-05-13", "ml": 1500},
    {"date": "2026-05-14", "ml": 2100},
    ...
  ],
  "insight_text": "В будни пьёте больше (среднее 2 100), в выходные меньше (1 400 мл).",
  "recommendation": null
}
```

If `days_total_with_data < 3`: insight returns «Пока недостаточно данных. Попробуйте отмечать ещё несколько дней.» and chart with empty days.

### 9.5 PATCH `/api/v1/customer/wellness/consent`

**Request** (water-specific config update):
```json
{
  "module_name": "water",
  "config": {
    "daily_target_ml": 2500
  }
}
```

Merges into existing config (partial update). Other consent fields unchanged.

---

## 10. Events emitted

Per [`event-taxonomy.md §3.6`](../policies/event-taxonomy.md#36-wellness-domain):

| Trigger | Event | Notes |
|---|---|---|
| Consent granted | `wellness.consent.module.granted` | `module_name='water'` |
| Consent revoked | `wellness.consent.module.revoked` | `module_name='water'` |
| Water event saved | `wellness.input.recorded` | `module_name='water'`, `input_type=size_preset`, `confidence=1.0`, `source` |
| Daily target reached (first time today) | NEW: `wellness.water.target_reached` | `customer_id`, `target_ml`, `actual_ml`, `reached_at` |
| Reminder sent | NEW: `wellness.water.reminder.sent` | `customer_id`, `sent_at`, `slot_number` (1/2/3 of day) |
| Reminder ignored (no log within 1h) | NEW: `wellness.water.reminder.ignored` | analytics for throttle algorithm tuning |
| Aggregator writes to profile | `wellness.profile.layer.updated` | `customer_id`, `layer_name='layer_6_nutrition'`, `field='avg_water_ml_7d'`, `source='water_module'` |

All envelope per [event-taxonomy §2](../policies/event-taxonomy.md#2-envelope-structure-every-event).

Add `wellness.water.target_reached` + `wellness.water.reminder.sent` + `wellness.water.reminder.ignored` to event-taxonomy §3.6.

---

## 11. Privacy enforcement

Per same model as Mood §11.

- All `/api/v1/customer/wellness/water/*` endpoints require customer auth
- Return ONLY calling customer's data
- 403 if tenant_id mismatch
- ZERO tenant-side endpoints in Phase 2
- Aggregation pipeline (Phase 3+) strips identifiers before salon-side analytics
- Master pre-arrival context per [`master-conversational-templates §5.5`](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface) does NOT show water data (Layer 6 strict customer-only)

PII rules:
- Water events are LOW sensitivity vs Mood (no emotional / physical state metadata)
- Log INFO level only event_id + amount_ml; never customer_id in logs
- Retention per [Q-WI10](../decisions-log.md) anonymized soft-delete 30d → hard-delete on customer revoke

---

## 12. Wellness Profile integration

### 12.1 Aggregator job

Daily Celery beat (consistent with Mood §12.1):
- Compute Layer 6 (Nutrition) derived fields:
  - `layer_6_nutrition.water_avg_ml_7d` — average daily ml over 7 days
  - `layer_6_nutrition.water_avg_ml_30d`
  - `layer_6_nutrition.water_target_adherence_7d` — % days target reached
  - `layer_6_nutrition.water_pattern_weekday_vs_weekend` — float (e.g., +30% weekday)
- Emit `wellness.profile.layer.updated` per aggregation

### 12.2 Cross-correlation (Phase 3+)

Per [wellness-input-modules §9.2 Cross-module insight 1](../policies/wellness-input-modules.md#92-cross-module-integration):
- «Низкая вода 5 дней + плохой сон + усталость + жалоба на отёчность → лимфодренаж priority recommendation»

Implementation pattern (Phase 3+):
- Daily cross-module insight calculator
- Surfaces to customer via [wellness-input-modules §6.4 recommendation surface](../policies/wellness-input-modules.md#64-ai-inference)
- NEVER surfaces to salon side (privacy)

Out of scope this handoff; mentioned for completeness.

---

## 13. Anti-patterns specific to water module

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Streak counter («4 дня подряд цель!») | Anxiety-inducing per [wellness-input-modules §6.6](../policies/wellness-input-modules.md) | No streaks visible anywhere |
| Trophies / badges / achievements | Childish; manipulates dopamine | None — module is utility |
| Leaderboards / social comparison | Privacy violation + competitive shame | NEVER |
| «Вы отстаёте от цели» framing | Failure-shame | «Осталось N стаканов» — neutral remaining count |
| Warn customer at >3000 ml («may be too much») | Medical territory; not our role | No upper warnings; trust customer |
| Reminder pattern «every hour» | Annoying | Max 2-3 per day; smart timing |
| Reminder during sleep / off-hours | Disrupts | Respect reminder_window + DND |
| Cross-promote with other modules pushy («активируйте все 7!») | Friction-justified opt-in violated | Module-specific consent; no bulk push |
| Allow >2000 ml per single event | Suspicious (fake data) | Cap per §4.1 + UI ceiling |
| Auto-decrement water if customer cancels event | Surprise data loss | Only customer-initiated delete |
| Tenant-side water analytics («наши клиенты пьют мало») | Privacy violation | NEVER — strict customer-only |
| «Если выпьете 2 л — скидка!» gamification | Manipulation | Module is wellness tool, not loyalty bait |
| Hydration medical claims («лечит головную боль») | Medical scope | Observational language; route to specialist for symptoms |
| Reminders ignoring customer's MAX timezone | Wakes wrong hours | Always customer TZ; double-check on edge case |

---

## 14. Acceptance criteria (engineering checklist)

- [ ] `WellnessWaterEvent` model with CheckConstraints
- [ ] Migration creates table; reuses `WellnessModuleConsent` with water config schema
- [ ] 5 API endpoints implemented + tested
- [ ] Customer auth required; tenant boundary enforced; 403 on mismatch
- [ ] Activation Paths A + B implemented; Path C deferred Phase 2.5
- [ ] Consent dialog UI in Mini App per §3.1
- [ ] Smart reminder Celery beat (or scheduled task) per algorithm §7.5
- [ ] Per-state behavior matrix §5 enforced (no reminders during DORMANT, HUMAN_LOCKED, etc.)
- [ ] Throttle §7.4 (consecutive day-no-response → pause logic)
- [ ] Mini App Самочувствие → Вода section with progress bar + quick add
- [ ] Quick-chip on Главная state-adaptive
- [ ] Free-form bot DM parsing for «попила воды» / «стакан» / N мл
- [ ] Insights view §6.4 with simple rules-based observations
- [ ] Events emitted per §10
- [ ] Privacy enforcement per §11
- [ ] Aggregator writes to Wellness Profile Layer 6 §12
- [ ] Tests: unit (model + service) + API (endpoint + auth) + integration (consent → log → progress → reminder) + privacy (cross-tenant denial)
- [ ] Anti-pattern review §13 — especially no streaks/badges/trophies
- [ ] Anti-spam rate-limiting per Q-WW1
- [ ] Accessibility audit on Mini App + reminder buttons (WCAG 2.2 AA)
- [ ] Documentation in `apps/wellness/water/README.md` referencing this handoff

---

## 15. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-WW1** | Anti-spam — max 50 events per customer per day enough? | YES MVP cap; if customer hits this it's likely fake data OR bug. Surface to support if cap reached repeatedly | Eng | 🟡 |
| **Q-WW2** | Default `max_reminders_per_day = 3` — too many / too few? | 3 MVP per [`notification-preferences §6.1`](../policies/notification-preferences-ux.md#61-per-audience-caps) customer cap of 5/day total (so water can take 60% of customer quota); tunable per customer | UX | 🟢 |
| **Q-WW3** | Should consent dialog ask «как часто пьёте обычно» to seed initial target? | NO MVP — default 2000 ml; customer can adjust. Asking baseline = friction. | UX | 🟢 |
| **Q-WW4** | Free-form parsing — what about «полстакана» / «глоток»? | «полстакана» = 125 ml; «глоток» = 50 ml; document in NLU model | AI + UX | 🟡 |
| **Q-WW5** | Customer in different TZ than tenant (travels abroad) — which TZ for reminders? | Customer's CURRENT TZ from MAX session; updates dynamically. Customer always owns when reminders come. | Eng + UX | 🟡 |
| **Q-WW6** | Smart timing algorithm — engineering rule-based MVP or LLM-suggested? | Rule-based MVP per §7.5; LLM Phase 4+ if data shows poor rule fit | Eng | 🟢 |
| **Q-WW7** | Customer reaches target consistently — proactive raise suggestion? | Per §6.4 insight line «Цель уверенно достигаете. Можно поднять?» button; one-time per change | UX | 🟢 |
| **Q-WW8** | What if customer logs > 4L in a day? | Just record (no warning per anti-pattern). Insights view may note «выше обычного» if pattern; no medical advice | Eng + Policy | 🟢 |
| **Q-WW9** | Customer who pauses module mid-day — preserve today's data or wipe? | Preserve; «paused» means no NEW logs/reminders, doesn't delete existing | Eng | 🟢 |
| **Q-WW10** | Mini App offline log queuing — Phase 2 or Phase 3? | Phase 2 — extend customer-first-touch §7.9 sync queue pattern to water events | Eng | 🟡 |
| **Q-WW11** | Display: «1 750 / 2 000 мл» vs «7 / 8 стаканов» — user preference? | Per config `unit_display_preference`; tenant can change in dialog OR settings. Default: customer chooses at consent. | UX | 🟢 |
| **Q-WW12** | Reminder text variants (avoid repetition annoyance) — N variants needed? | 3-5 variants of «{{N}} часа без воды. Глоток?» rotated randomly; doesn't degrade with use | UX + AI | 🟡 |
| **Q-WW13** | What happens if customer drinks 4L in 1 event (suspicious)? | Cap per §4.1 at 2000 per event; UI suggests «may have meant smaller»; allow but flag in analytics. | Eng | 🟢 |
| **Q-WW14** | Mood synergy offer (Path B) — fire at day 7 of Mood usage or earlier? | Day 7 sufficient for synergy confidence; not too early to feel pushy | UX | 🟢 |
| **Q-WW15** | Insights cross-correlation with sleep (Phase 3+) — surface to customer or aggregator only? | Aggregator only; cross-module surface = Phase 3 separate handoff | Eng | 🟢 |
| **Q-WW16** | Free-form «выпила литр» = 1000 ml exact? | YES; «литр» / «литра» / «литров» = 1000; «полтора литра» = 1500 | AI | 🟢 |

---

## 16. Cross-document linkage

- [`../policies/wellness-input-modules.md §3`](../policies/wellness-input-modules.md#3-module-2--water-tracker) — strategic spec this handoff ports
- [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md) — sibling Phase 1 module; shares `WellnessModuleConsent` + activation patterns
- [`../policies/notification-preferences-ux.md §6`](../policies/notification-preferences-ux.md) — throttle/DND integration
- [`../policies/core-user-states.md`](../policies/core-user-states.md) — state matrix §5
- [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md) Layer 6 Nutrition — aggregator writes
- [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md) — voice anchors
- [`../policies/information-architecture.md`](../policies/information-architecture.md) — Самочувствие tab placement
- [`../policies/event-taxonomy.md §3.6`](../policies/event-taxonomy.md#36-wellness-domain) — events emitted (3 NEW per §10)
- [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — HUMAN_LOCKED gating
- [`../policies/master-conversational-templates.md §5.5`](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface) — privacy boundary on pre-arrival
- [`../policies/customer-first-touch-and-mini-app-states.md §7.9`](../policies/customer-first-touch-and-mini-app-states.md) — offline sync queue pattern (Q-WW10)
- [`../policies/customer-profile-management-ux.md §4`](../policies/customer-profile-management-ux.md) — module activation entry from Профиль → Самочувствие
- [`../decisions-log.md`](../decisions-log.md) — Q-WI2 (units lean), Q-WI10 (revoke retention)

---

## 17. What this unblocks

- **`apps/wellness/water/` Phase 2 implementation** — model + API + reminder algorithm + Mini App engineering-ready
- **Daily engagement habit loop** — first daily-rhythm wellness module
- **Wellness Profile Layer 6 populated** — first real nutrition data
- **Pattern for habit-style modules** — sleep / food will follow same multi-tap-with-reminders structure
- **Smart timing algorithm baseline** — rule-based MVP that can be A/B tested vs ML later
- **Mood synergy demonstrates cross-module promotion** without violating consent boundaries

## 18. What this does NOT unblock

- ❌ Other wellness modules (body / sleep / food / symptom — separate handoffs)
- ❌ HealthKit / Google Fit (Phase 4+)
- ❌ Multi-fluid tracking (coffee/tea — Phase 3+)
- ❌ Tenant-side aggregate analytics (privacy boundary)
- ❌ Cross-module insights customer-side (Phase 3+ separate handoff)
- ❌ Hydration medical claims (out of scope ethically)
- ❌ ML-based reminder timing (Phase 4+ if rule-based shows poor fit)
- ❌ Skip pre-deploy privacy audit per §11

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Wellness backend lead (apps/wellness/) | ☐ | |
| Mini App frontend (Самочувствие → Вода section + Главная quick chip) | ☐ | |
| AI prompt engineering (free-form parsing + reminder text variants per Q-WW12) | ☐ | |
| Privacy / Legal (Q-WW8 — what counts as «medical territory» line) | ☐ | |
| Accessibility (WCAG 2.2 AA on progress bar + quick-add buttons) | ☐ | |

## Last verified
2026-05-19 (initial draft, engineering-ready for Phase 2 Wellness Water Tracker module — sibling to Mood)
