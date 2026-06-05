# Wellness Sleep Tracker Module — engineering handoff

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Engineering-ready — Phase 2 wellness module №4 (after Mood + Water + Body)
**Reads:** [`../policies/ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md), [`../policies/ayla-memory-and-personalization.md`](../policies/ayla-memory-and-personalization.md), [`../policies/wellness-input-modules.md`](../policies/wellness-input-modules.md) §6 (Module 4 Sleep), [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md), [`./2026-05-19-wellness-water-handoff.md`](./2026-05-19-wellness-water-handoff.md), [`./2026-05-19-wellness-body-handoff.md`](./2026-05-19-wellness-body-handoff.md), [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md), [`../policies/core-user-states.md`](../policies/core-user-states.md), [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md), [`../policies/ayla-emergency-fallback-policy.md`](../policies/ayla-emergency-fallback-policy.md), [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md), [`../policies/customer-profile-management-ux.md`](../policies/customer-profile-management-ux.md), [`../policies/information-architecture.md`](../policies/information-architecture.md)

> Ports [wellness-input-modules §6 Module 4 Sleep](../policies/wellness-input-modules.md#6-module-4--sleep-tracking) to engineering-ready spec. Phase 2 sibling to Mood + Water + Body. Unique dynamics: **retroactive morning logging** (slept last night, log this morning) + **two-axis data** (duration + quality) + strong cross-correlation potential with services.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](../policies/ayla-identity-and-brand.md) memory 2026-05-19: sleep data is **Ayla's memory of user** per [`ayla-memory-and-personalization §9`](../policies/ayla-memory-and-personalization.md) — cross-tenant. NO «sleep score» anti-pattern preserved. `HUMAN_LOCKED` references — backend mechanic; customer-facing flow via emergency fallback per [`ayla-emergency-fallback-policy §3`](../policies/ayla-emergency-fallback-policy.md). AI voice uses Ayla per [`ayla-identity-and-brand §2`](../policies/ayla-identity-and-brand.md).

---

## 0. Why this exists

### Strategic context

Sleep is **the central wellness metric**. Per [`wellness-input-modules §6.2`](../policies/wellness-input-modules.md#62-why-this-matters):
- Poor sleep → high stress → need for services
- Easy to capture (1 question, morning)
- Cross-correlation with services («после массажа во вторник в среду спите лучше»)
- Bridges Layer 3 Body State + Layer 7 Emotional in Wellness Profile

### The gap

[wellness-input-modules §6](../policies/wellness-input-modules.md#6-module-4--sleep-tracking) has strategic spec + UX sketch but doesn't specify:
- Activation flow + paths
- Two-axis logging UX (duration + quality on same screen)
- Retroactive backdate window (slept Mon→Tue night, log Wed = up to 7 days)
- Model fields + validators
- Per-state behavior
- Mini App layout + retroactive flow
- Anti-pattern enforcement at API level (anti-sleep-score, anti-medical-advice)
- Cross-correlation with services (Phase 3+ preview)
- Events emitted

### The promise

Single source for `apps/wellness/sleep/` Phase 2 implementation. Engineering ships with clear contract.

---

## 1. Scope

### IN
- New sub-module `apps/wellness/sleep/` (within existing `apps/wellness/` app from Mood)
- `WellnessSleepEvent` model (1 row per night)
- Activation Paths A + B (Path C deferred Phase 2.5+)
- Consent dialog with prompt time selection
- Bot DM morning prompt (configurable time, default 9:00 customer TZ)
- Retroactive backdate up to 7 days (rare but legitimate — customer forgot to log)
- Mini App Самочувствие → Сон section with 7-day chart
- 6 API endpoints
- Per-state behavior matrix
- Phase 2 simple-rules insights (NO sleep score)
- Privacy enforcement (customer-only)
- Wellness Profile Layer 3 + Layer 7 integration
- Cross-module synergy stubs (with Mood: emotional-physical link; with services: Phase 3+)
- 5 NEW events for event-taxonomy

### OUT
- Sleep score / hypnogram visualization (NOT our design philosophy — single magic number anti-pattern)
- Sleep apnea / disorder detection (medical territory)
- HealthKit / Google Fit / wearable integration (Phase 4+)
- Smart alarm / wake-up time recommendation (medical-adjacent + scope creep)
- Sleep coaching tips / sleep hygiene advice (out of scope; route to specialist)
- Sleep cycle stages (REM / deep / light) — Phase 4+ if wearable integration
- Tenant-side aggregate (privacy boundary)
- Customer-pays gating (free forever per Q-WI12)
- Multi-night sleep (afternoon naps tracking) — Phase 3+ if customer demand
- Sleep debt calculation — medical-adjacent

---

## 2. Strategic constraints — non-negotiable

### 2.1 No «sleep score»
Sleep tracking apps like Oura, WHOOP, Fitbit calculate a single «sleep score» (0-100). We **do not**:
- Single-number sleep score is reductive (oversimplifies multi-factor reality)
- Triggers OCD-style optimization («I need 90+ tonight»)
- Frames sleep as performance metric

We display: **duration + quality (1-5 stars) separately**. Never combine into one number.

### 2.2 No medical claims
Sleep is medical-adjacent. We must:
- Never diagnose sleep disorders («у вас сонное апноэ»)
- Never recommend sleep hygiene rituals («попробуйте лавандовое масло»)
- Never advise on insomnia / chronic sleep issues
- If customer's data shows extreme patterns (Q-WS14) → route to medical specialist, don't intervene

### 2.3 Anti-pattern: streaks + gamification
- No streak counter («7 nights in a row tracked!»)
- No badges
- No XP / level / achievement
- Per Mood + Body precedent — anti-OCD principle

### 2.4 No proactive sleep prompts at night
- AI never wakes customer or pings during sleep window (defined per customer TZ)
- Reminder ONLY morning prompt
- No «time to sleep» nudges (we're not a coaching app)

### 2.5 Privacy hierarchy
Same as Mood (standard customer-only). Less strict than Body (no body measurement values). Soft-delete 30d on revoke. Layer 3 + Layer 7 derived.

---

## 3. Activation flow

### 3.1 Eligibility (gates) — same as Mood/Water/Body

Customer cannot activate Sleep if:
- `consent.ai_messaging = false` (exception: Path A self-discovery)
- `core_user_state ∈ {DORMANT, HUMAN_LOCKED active conversation}`
- Tenant in PAUSED / SUSPENDED state
- Customer's MAX account suspended
- Customer < 18 years old (consistent with Body + AI Avatar; sleep data privacy + ethical)

### 3.2 Activation triggers (Phase 2 launch)

**Path A — Self-discovery in Mini App** (always available):
Customer navigates Профиль → Самочувствие → Сон card → toggle ON → consent dialog §4.

**Path B — Post-relaxation booking offer**:
After customer completes first booking in **relaxation category** (massage, lymphatic drainage, spa, aromatherapy, etc.) AND has `consent.ai_messaging = true`, AI sends ONE offer (T+24h):
```
Расслабляющая процедура хорошо влияет на сон. Хотите я буду спрашивать утром как спалось?

Только короткий вопрос раз в день. Видите только вы.

[Попробовать]   [Не сейчас]
```

Same Path B suppression rule: «не сейчас» → mark `customer.sleep_offer_declined_at`; never re-offer.

**Path C — Symptom-language trigger** (Phase 2.5+):
Customer messages «не спала», «бессонница», «уставшая утром» → AI suggests sleep module. Phase 2.5+ NLU work.

### 3.3 Activation events
- `wellness.consent.module.granted` with `module_name='sleep'`, `granted_via=<path>`

---

## 4. Consent dialog

### 4.1 Single-screen with prompt time selection

```
┌────────────────────────────────────────┐
│ Отслеживать сон?                       │
├────────────────────────────────────────┤
│ Я буду:                                │
│   • Утром спрашивать как спалось        │
│   • Запоминать длительность и качество  │
│   • Показывать как сон связан с         │
│     процедурами (если ходите регулярно) │
│                                        │
│ ── Когда спросить ──                    │
│                                        │
│ ◯ В 8:00                                │
│ ⦿ В 9:00                                │
│ ◯ В 10:00                               │
│ ◯ Не спрашивай — отмечу сам(а)          │
│                                        │
│ ── Что важно ──                         │
│                                        │
│ ✓ Данные видите только вы              │
│ ✓ Студия НЕ видит ничего                │
│ ✓ Никаких «sleep score» — длительность  │
│   и звёзды отдельно                    │
│ ✓ Если плохо спалось — никаких поучений│
│   и советов                            │
│ ✓ Я не разбужу вас и не позову ночью    │
│                                        │
│ [Не сейчас]      [Согласна, попробуем] │
└────────────────────────────────────────┘
```

### 4.2 Critical design choices

- **Default morning prompt time**: 9:00 customer's TZ
- **Reminders default ON** (sleep module is most useful with morning prompts — unlike Body where reminders default OFF)
- **«Не спрашивай — отмечу сам(а)»** option allows pure self-tracking without bot prompts
- **Anti-sleep-score disclosure** in privacy section pre-frames customer expectations

### 4.3 Outcomes — same pattern as Mood/Water/Body

#### Tap «Согласна, попробуем»
- Create `WellnessModuleConsent(module_name='sleep', granted=True, granted_via=<path>, config=<chosen>)`
- Config JSON:
```json
{
  "morning_prompt_time": "09:00",
  "prompts_enabled": true,
  "wakeup_count_field_visible": true
}
```
- Emit `wellness.consent.module.granted` event
- Navigate to Самочувствие → Сон section §6
- First-use toast: «Готово. Утром в {{time}} спрошу как спалось.»

If customer selected «Не спрашивай»:
- `config.prompts_enabled = false`
- Toast: «Готово. Отмечать сон — в Самочувствии → Сон.»

#### Tap «Не сейчас»
- NO record created
- Path B activation: mark `customer.sleep_offer_declined_at = NOW`; never re-offer
- Path A: customer can re-open consent dialog

---

## 5. Data model

### 5.1 `WellnessSleepEvent`

One row per night.

```python
class WellnessSleepEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='sleep_events')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    night_of = models.DateField()
    # The date of the night — e.g., 2026-05-18 for the Monday→Tuesday night.
    # Customer slept ON 2026-05-18, woke up on 2026-05-19.
    # Convention: night_of = the date you went to bed.

    duration_hours = models.DecimalField(
        max_digits=4, decimal_places=1,
        validators=[MinValueValidator(Decimal('0.0')), MaxValueValidator(Decimal('24.0'))],
    )
    # Customer-reported sleep duration in hours, 0.5h precision.

    quality_score = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    # 1 = ужасно, 5 = отлично. 1-5 stars.

    wakeup_count = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(20)])
    # Number of times customer woke up. 0 = slept through. Null = customer didn't specify.

    note = models.TextField(max_length=280, blank=True, default='')

    SOURCE_CHOICES = [
        ('bot_morning_prompt', 'Bot DM morning prompt response'),
        ('mini_app_section', 'Mini App Самочувствие → Сон section'),
        ('mini_app_backdate', 'Mini App backdated entry'),
    ]
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES)

    recorded_at = models.DateTimeField()
    # When customer actually logged. Could be 1-7 days after night_of for retroactive entries.

    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    # Soft-delete marker. Hard-delete after 30d via Celery.

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=['customer', 'night_of'],
                condition=Q(deleted_at__isnull=True),
                name='uq_sleep_per_customer_per_night',
            ),
        ]
        # Anti-duplicate: 1 sleep event per night per customer (excluding soft-deleted)
        indexes = [
            Index(fields=['customer', '-night_of']),  # timeline view
            Index(fields=['tenant', 'recorded_at']),  # analytics aggregation
        ]
```

### 5.2 Reuses `WellnessModuleConsent` (from Mood)

Config JSON for sleep module:
```python
{
  "morning_prompt_time": "09:00",  # HH:MM customer TZ
  "prompts_enabled": bool,  # if false, customer-driven only
  "wakeup_count_field_visible": bool,  # show/hide optional field
}
```

### 5.3 Anti-spam + validation rules

- Max 1 sleep event per customer per night (UniqueConstraint)
- If customer tries to log for already-logged night → API returns 409 with «Запись за эту ночь уже есть. Отредактировать?»
- `night_of` window: today, yesterday, up to 7 days back (retroactive)
- `night_of` cannot be future date
- Duration 0-24 hours (validator)
- Quality 1-5 (validator)

---

## 6. Mini App «Самочувствие» → Сон section

### 6.1 Section layout (after at least 1 night logged)

```
┌────────────────────────────────────────┐
│ 🌙 Сон                                  │
├────────────────────────────────────────┤
│ Прошлая ночь (18 мая):                  │
│ 7.5 ч  ★★★★☆                            │
│ Просыпались 1 раз                       │
│                                        │
│ [+ Отметить сегодняшний сон]            │
│                                        │
│ ── За неделю ──                         │
│                                        │
│ [bar chart 7 nights:                   │
│  каждая ночь = бар, высота = часы,     │
│  цвет/прозрачность = качество]         │
│                                        │
│ Среднее: 7.2 ч, ★★★★☆ (3.8/5)            │
│                                        │
│ ── Что заметно ──                       │
│                                        │
│ Хорошо спите по средам и четвергам.    │
│ В понедельник короче обычного.          │
│                                        │
│ [Подробнее →]                          │
│ [История →]                             │
└────────────────────────────────────────┘
```

### 6.2 Empty state (no nights logged yet)

```
┌────────────────────────────────────────┐
│ 🌙 Сон                                  │
├────────────────────────────────────────┤
│ Пока нет записей сна.                  │
│                                        │
│ [+ Отметить прошлую ночь]              │
│                                        │
│ {{if prompts_enabled}}                  │
│ Утром в {{prompt_time}} буду            │
│ спрашивать как спалось.                 │
│ {{endif}}                              │
└────────────────────────────────────────┘
```

### 6.3 Add sleep entry screen

```
┌────────────────────────────────────────┐
│ ← Сон                                   │
├────────────────────────────────────────┤
│ Ночь с: [Прошлая ▾]                     │
│                                        │
│ Длительность:                           │
│ [────●────] 7.5 часов                   │
│  3.0  ──  12.0                          │
│                                        │
│ Качество:                               │
│ ★★★★☆ (4/5)                            │
│                                        │
│ Просыпались? (опц.)                    │
│ ⦿ Нет (спали всю ночь)                  │
│ ◯ 1 раз                                 │
│ ◯ 2 раза                                │
│ ◯ 3+ раз                                │
│                                        │
│ Заметка (опц.):                        │
│ [_____________________________]        │
│                                        │
│ [Сохранить]                            │
└────────────────────────────────────────┘
```

«Ночь с» dropdown:
- «Прошлая» (default — last night)
- «Позавчера»
- «Три ночи назад»
- ... up to 7 days back
- «Другая дата» → date picker

### 6.4 Backdate flow

Per §5.3 — up to 7 days back. UI surfaces this via the «Ночь с» dropdown. If customer tries to log for a night already covered → 409 with edit suggestion.

### 6.5 «Подробнее» insights screen

```
┌────────────────────────────────────────┐
│ ← Сон — подробнее                       │
├────────────────────────────────────────┤
│ Период: [30 дней ▾]                     │
│                                        │
│ Средняя длительность: 7.2 ч             │
│ Среднее качество: ★★★★☆ (3.8/5)         │
│                                        │
│ [bar chart 30 nights]                  │
│                                        │
│ ── Распределение качества ──            │
│ ★★★★★ ████░░░░░░  6 ночей               │
│ ★★★★☆ ██████░░░░  9 ночей               │
│ ★★★☆☆ █████░░░░░  7 ночей               │
│ ★★☆☆☆ ███░░░░░░░  4 ночи                │
│ ★☆☆☆☆ █░░░░░░░░░  1 ночь                │
│                                        │
│ ── Связь с услугами ──                  │
│ {{if Phase3+}}                          │
│ В неделях с массажем: ★4.1               │
│ В неделях без: ★3.7                      │
│ {{endif}}                              │
│                                        │
│ [История →]                             │
└────────────────────────────────────────┘
```

Phase 2 MVP: no «Связь с услугами» section. Phase 3+ adds (per Q-WS6).

### 6.6 «История» view

Same pattern as Body §6.5 — list of past sleep events with edit/delete.

---

## 7. Per-state behavior matrix

When AI may send morning prompt (per `config.prompts_enabled`).

| Customer state | Morning prompt fires? | Why |
|---|---|---|
| DISCOVERED | NO | No relationship; gate blocks |
| EXPLORING | NO (unless Path A) | Customer still discovering |
| PROBLEM_SEEKING | YES if activated AND prompts ON | Background continues |
| READY_TO_BOOK | YES if activated AND prompts ON | Same |
| POST_VISIT | YES if activated AND prompts ON | Good moment for reflection on procedure impact |
| ACTIVE_REGULAR | YES if activated AND prompts ON | Steady state |
| AT_RISK_DRIFTING + last log < 14d | YES if activated AND prompts ON | Active |
| AT_RISK_DRIFTING + no log 14+ days | NO; pause prompts | Respect deliberate silence |
| DORMANT | NO | Per ownership |
| HUMAN_LOCKED active | NO | Admin owns |
| HUMAN_LOCKED inactive | YES if activated AND prompts ON | Resume |

---

## 8. Bot DM morning prompt

### 8.1 Trigger conditions (ALL must be true)

- `WellnessModuleConsent.granted = True` AND `config.prompts_enabled = true`
- Per-state allowance per §7
- Current time = `config.morning_prompt_time` ±15 min in customer's TZ
- No sleep event recorded for last night yet
- Not in DND per notification-preferences
- Customer hasn't responded to today's prompt yet

### 8.2 Bot DM template

**Voice anchor**: Warm + Calm + Concise.

```
Доброе утро. Как спалось этой ночью?

[😴 Отлично]  [🙂 Хорошо]  [😐 Так себе]  [😣 Плохо]
[Подробнее →]
```

### 8.3 Post-tap response (low pressure)

| Customer tap | Action | Bot response |
|---|---|---|
| 😴 Отлично | save `quality=5, duration=auto-prompt-or-skip` | `«Здорово. {{optional duration follow-up if not set}}.»` |
| 🙂 Хорошо | save `quality=4` | `«Поняла.»` (brief) |
| 😐 Так себе | save `quality=3` | `«Поняла. Если не выспались — отдохните когда получится.»` |
| 😣 Плохо | save `quality=2, duration=auto-prompt` | `«Понимаю. Если хочется — можно ничего на сегодня не планировать.»` |
| Подробнее → | deep-link Mini App add screen | n/a |

**Critical**: response NEVER:
- ❌ Asks «почему?»
- ❌ Recommends sleep hygiene
- ❌ Suggests medical consultation
- ❌ «Завтра лучше выспитесь» pep-talk

### 8.4 Two-step bot DM for duration

Quick 1-tap captures quality. Duration is asked optionally as follow-up:

```
Bot: Доброе утро. Как спалось?
Customer: [😴 Отлично]
Bot: Здорово. Сколько часов получилось?
     [5-6]  [6-7]  [7-8]  [8-9]  [9+]
Customer: [7-8]
Bot: ★★★★★, ~7-8 часов сохранила. Если хотите точнее — в Самочувствии.
```

Customer can skip duration follow-up («Пропустить») → only quality saved, duration null.

For initial deploy keep duration follow-up as opt-in, not required.

### 8.5 Non-response throttle

Same pattern as Mood §6.4:

| Consecutive mornings no response | Next-day behavior |
|---|---|
| 1-2 | Normal next-day prompt |
| 3 | **Skip 1 day** |
| 4 | Resume normal |
| 5 | **Pause 7 days** + DM: «Не вижу ваших отметок — поставила утренние сообщения на паузу. Включить — в настройках Самочувствия.» |
| After 7d pause | If no re-enable, stays paused |

Reset on any response.

---

## 9. API contracts

### 9.1 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/customer/wellness/sleep` | Customer | Save sleep event |
| GET | `/api/v1/customer/wellness/sleep` | Customer | List events (paginated, date range) |
| GET | `/api/v1/customer/wellness/sleep/last-night` | Customer | Last night's sleep data |
| GET | `/api/v1/customer/wellness/sleep/summary` | Customer | Aggregate summary for insights view |
| PATCH | `/api/v1/customer/wellness/sleep/<id>` | Customer | Edit existing event |
| DELETE | `/api/v1/customer/wellness/sleep/<id>` | Customer | Soft-delete event |

### 9.2 POST `/api/v1/customer/wellness/sleep`

**Request**:
```json
{
  "night_of": "2026-05-18",
  "duration_hours": 7.5,
  "quality_score": 4,
  "wakeup_count": 1,
  "note": "проснулась в 4, но потом уснула",
  "source": "mini_app_section"
}
```

**Validation**:
- Consent granted for `module_name='sleep'` (else 403)
- `night_of` ≥ today - 7 days; ≤ today
- `duration_hours` ∈ [0.0, 24.0], 0.5 increments
- `quality_score` ∈ [1, 5]
- `wakeup_count` nullable, [0, 20]
- `note` max 280 chars
- `source` in enum
- UniqueConstraint: if event exists for (customer, night_of) AND not deleted → 409 with `{"error": "sleep_event_exists", "existing_id": "...", "message": "Запись за эту ночь уже есть."}`

**Response** (201):
```json
{
  "id": "uuid",
  "night_of": "2026-05-18",
  "duration_hours": 7.5,
  "quality_score": 4,
  "wakeup_count": 1,
  "summary": "Сохранено."
}
```

### 9.3 GET `/api/v1/customer/wellness/sleep/last-night`

Returns last night's event OR null.

**Response** (200):
```json
{
  "event": {
    "id": "uuid",
    "night_of": "2026-05-18",
    "duration_hours": 7.5,
    "quality_score": 4,
    "wakeup_count": 1,
    "note": ""
  },
  "logged_today": true
}
```

If no event for last night yet: `event = null, logged_today = false`.

### 9.4 GET `/api/v1/customer/wellness/sleep/summary`

**Query**: `period_days` (default 30, max 90)

**Response** (200):
```json
{
  "period_days": 30,
  "nights_with_data": 22,
  "avg_duration_hours": 7.2,
  "avg_quality_score": 3.8,
  "chart_series": [
    {"night_of": "2026-04-20", "duration_hours": 6.5, "quality_score": 3, "wakeup_count": 2},
    ...
  ],
  "quality_distribution": {
    "5": 6,
    "4": 9,
    "3": 5,
    "2": 1,
    "1": 1
  },
  "insight_text": "Хорошо спите по средам и четвергам. В понедельник короче обычного.",
  "service_correlation": null
}
```

If `nights_with_data < 5`: `insight_text = "Пока недостаточно данных. Отмечайте ещё несколько ночей."`. Quality distribution sparse.

### 9.5 PATCH / DELETE — standard edit/soft-delete patterns (per Body §9.4-9.5)

---

## 10. Phase 2 AI insights (simple rules)

### 10.1 Rule-based insight_text generator

Inputs: events in period.

Rules:
- **< 5 data points**: «Пока недостаточно данных. Отмечайте ещё несколько ночей.»
- **High consistency** (variance(duration) < 1 hour AND variance(quality) < 1): «Стабильно спите за период.»
- **Weekday vs weekend pattern** (delta > 1 hour OR > 0.5 quality): «По будням / по выходным {{better/worse}}.»
- **Trending down** (quality drops > 0.5 over period): «Качество снижается за период.»
- **Trending up** (quality rises > 0.5): «Качество улучшается.»
- **Short night detected** (any night < 5 hours): mention observation only, NO advice — «{{N}} ночей короче обычного (<5 часов).»

### 10.2 Forbidden phrases (auto-reject at insight generator)

- «Высыпайтесь» / «отдыхайте больше»
- «Лучше», «хуже» (judgmental — use «больше/меньше», «выше/ниже»)
- «Sleep score X / 100»
- «Здоровый сон», «качественный сон» (judgmental)
- «Проблемы со сном», «бессонница», «сонное апноэ»
- «Попробуйте...» / «следует...»
- «Лавандовое масло», «магний», «таблетки» — anything medical/supplement
- Sleep hygiene advice tips

### 10.3 Service correlation (Phase 3+)

Out of scope Phase 2. Phase 3+ when modules accumulate:
- If customer had ≥ 2 relaxation bookings in period: «В неделях с массажем средняя {{avg_with}} vs {{avg_without}}.»
- NEVER causation claim («благодаря массажу...»)
- NEVER «keep up with the massages!» recommendation

Phase 2 returns `service_correlation: null`.

---

## 11. Privacy enforcement

### 11.1 API-level guards
Same as Mood §11.1: customer-only access, 403 on tenant mismatch, ZERO tenant endpoints.

### 11.2 Master pre-arrival context

Sleep data NEVER surfaces per [`master-conversational-templates §5.5`](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface):
- Layer 3 derivatives: ❌ for body / weight; ❌ for sleep
- Master sees: appointment + Layer 4 reactions from prior visits with this master

### 11.3 Logging
- API calls: event_id + path + outcome (no values)
- Sleep values: TRACE level only
- PII detector: sleep duration + quality treated as Layer 3 sensitive

### 11.4 Retention

Per Q-WI10 consistency:
- Soft-delete 30d on customer revoke
- OP6 deletion cascade
- Data export per OP6 includes raw sleep history

### 11.5 Founder access

NO direct read in MVP. Legal hold + 4-eye approval for fraud/legal cases only.

---

## 12. Wellness Profile integration

### 12.1 Aggregator job

Daily Celery beat:
- Layer 3 Body State:
  - `sleep_avg_duration_hours_7d` / `_30d`
  - `sleep_avg_quality_7d` / `_30d`
  - `sleep_consistency_7d` (1 - coefficient_of_variation)
- Layer 7 Emotional (when sleep + mood both active Phase 2.5+):
  - `sleep_mood_correlation_30d` — observational correlation coefficient

### 12.2 Cross-correlation triggers

Phase 3+:
- 3+ consecutive nights with quality ≤ 2 → flag in Insights view (NOT alert)
- Pattern «poor sleep before booking days» → surface in observations

Out of scope this handoff.

---

## 13. Anti-patterns specific to Sleep

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Sleep score (0-100) | Single-magic-number framing | Duration + quality separately |
| Hypnogram visualization | Implies medical sleep stages | Simple bar chart only |
| Sleep hygiene tips («попробуйте лавандовое масло») | Medical-adjacent + unsolicited | NEVER recommend; route to specialist if customer asks |
| Wake-up alarm features | Out of scope; medical-adjacent | NEVER |
| Sleep debt counter | Triggers OCD optimization | NEVER |
| «Goal: 8 hours per night» target | Diet-app/coaching territory | NEVER set goals |
| Streak counter («10 nights tracked!») | Anti-OCD | NEVER streaks |
| «Lucid dreaming» / «REM phase» frames | Pseudo-medical territory | NEVER |
| Push notifications at bedtime («время спать!») | We're not coaching app | NEVER night-time prompts |
| Auto-detect insomnia / sleep disorder | Medical territory | If customer mentions chronic issues, route to specialist |
| Reminder during sleep hours | Disrupts | DND respect always |
| Comparison with «average adult sleeps 7.5h» | Implies value judgment | NEVER cross-customer comparison |
| «Last night's quality was below your weekly average» | Subtle negative framing | Stick to neutral facts |
| Force daily logging by hiding history if customer skips | Coercive | History always visible |
| Penalize backdated entries via «late» badges | Anti-pattern | Backdate normal, no penalty |

---

## 14. Acceptance criteria (engineering checklist)

- [ ] `WellnessSleepEvent` model with UniqueConstraint per (customer, night_of)
- [ ] Migration adds table; reuses `WellnessModuleConsent` with sleep config
- [ ] 6 API endpoints implemented + tested
- [ ] Customer auth required; tenant boundary; 403 on mismatch
- [ ] Activation Paths A + B implemented; Path C deferred Phase 2.5
- [ ] Consent dialog UI per §4.1 with time selection
- [ ] Bot DM morning prompt Celery beat per algorithm §8 (configurable time, customer TZ aware)
- [ ] Two-step prompt flow (quality first, optional duration follow-up)
- [ ] Throttle §8.5 (consecutive no-response → pause logic)
- [ ] Per-state behavior matrix §7 enforced
- [ ] Mini App Самочувствие → Сон section per §6
- [ ] Backdate up to 7 days enforced at API
- [ ] UniqueConstraint enforced: 1 sleep per night per customer; 409 with edit suggestion on duplicate
- [ ] Insights view §6.5 with Phase 2 simple rules
- [ ] Insight generator FORBIDDEN-PHRASE enforcement §10.2 at API level
- [ ] Events emitted per §15
- [ ] Privacy enforcement per §11
- [ ] Aggregator writes Wellness Profile Layer 3 §12
- [ ] Tests: unit (model + validators + dup constraint + insight generator) + API (auth + 403 + 409 dup + 429 throttle) + integration (consent → prompt → log → insights) + privacy (no master leak)
- [ ] Anti-pattern review §13 — no sleep score, no medical claims, no streaks
- [ ] Accessibility audit on Mini App + slider + star rating (WCAG 2.2 AA)
- [ ] Documentation in `apps/wellness/sleep/README.md` referencing this handoff

---

## 15. Events emitted

Per [`event-taxonomy.md §3.6`](../policies/event-taxonomy.md#36-wellness-domain):

| Trigger | Event | Notes |
|---|---|---|
| Consent granted | `wellness.consent.module.granted` | `module_name='sleep'` |
| Consent revoked | `wellness.consent.module.revoked` | `module_name='sleep'` |
| Sleep event saved | `wellness.input.recorded` | `module_name='sleep'`, `input_type='quality_duration'`, `confidence=1.0`, `source` |
| Sleep event edited | NEW: `wellness.sleep.event.edited` | audit |
| Sleep event soft-deleted | NEW: `wellness.sleep.event.deleted` | grace start |
| Morning prompt sent | NEW: `wellness.sleep.prompt.sent` | analytics |
| Morning prompt ignored (no log within day) | NEW: `wellness.sleep.prompt.ignored` | throttle feeder |
| Short night detected (< 5h) | NEW: `wellness.sleep.short_night.observed` | observational, NOT alert — surfaces in insights only |
| Aggregator writes profile layer | `wellness.profile.layer.updated` | `layer_name='layer_3_body_state'` |

Add 5 NEW events to event-taxonomy.md §3.6.

---

## 16. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-WS1** | Default morning prompt time — fixed 9:00 or per-region default? | Fixed 9:00 customer TZ MVP; per-region defaults (e.g., 8:00 for ES, 9:00 for RU) Phase 4+ | UX | 🟢 |
| **Q-WS2** | Quality scale 1-5 or 1-10? | 1-5 stars MVP — matches CSAT convention + simpler tap interaction. 1-10 felt over-precise for subjective sleep quality | UX | 🟢 |
| **Q-WS3** | Duration field — slider OR explicit hour buttons? | Slider 0.5h increments — natural feel; min 3.0h max 12.0h with extended range button if needed | UX | 🟢 |
| **Q-WS4** | Two-step bot DM (quality then duration) — pressure to complete or stop after quality? | Stop after quality OK — duration is optional follow-up. Customer can complete via Mini App if interested | UX | 🟢 |
| **Q-WS5** | Wakeup count field — show by default or hide behind «детально»? | Show by default (per [`wellness-input-modules §6.3`](../policies/wellness-input-modules.md)). Customer can hide via config `wakeup_count_field_visible` | UX | 🟢 |
| **Q-WS6** | Service correlation insights — Phase 2 OR Phase 3+? | Phase 3+ (Q-WB13 consistency — opt-in per service category). MVP: `service_correlation: null` in API. | UX + Privacy | 🟡 |
| **Q-WS7** | If duration < 3h or > 12h — accept or warn? | Accept with no warning. Per §10.2 anti-pattern — no medical claims. Customer logs reality. | Policy | 🟡 |
| **Q-WS8** | Backdate window 7 days — too short? | 7d MVP; customer who forgot a week ago might still remember vaguely. Longer = data quality concerns | UX | 🟢 |
| **Q-WS9** | UniqueConstraint per (customer, night_of) — what if customer has nap that night? Two events? | NO — one event per night_of (the «main» sleep). Nap tracking Phase 3+ (separate sub-feature) | UX | 🟢 |
| **Q-WS10** | Morning prompt missed (customer offline) — when fires next? | Next morning at scheduled time; not «catch-up» (no late-day prompts). Customer can backdate via Mini App | UX | 🟢 |
| **Q-WS11** | Phase 3+ wearable integration — which? | HealthKit (iOS) + Google Fit (Android) MVP if Phase 3+. Oura/WHOOP/Fitbit Phase 4+. | Eng + PM | 🟢 |
| **Q-WS12** | Customer who logs daily for 30 days but quality always ≤ 2 — system response? | NO automatic alert. NO recommendation. If customer asks in DM «что делать?» → route to medical specialist per conversational-ux §7.2 | Policy | 🔴 before first sleep data (mental-health-adjacent) |
| **Q-WS13** | Sleep + Mood cross-correlation insight surface (when both active) — opt-in or auto? | Auto-surface to customer (their data, their connection insight). NEVER salon-side. | UX + Privacy | 🟡 |
| **Q-WS14** | Extreme pattern detected (e.g., 5 nights < 3h in 7 days) — surface what? | Per §10 simple rules: «{{N}} ночей короче обычного». NO «consult specialist» framing per §13 anti-pattern. If pattern persists 30d, surface in Insights view text only. | Policy | 🟡 |
| **Q-WS15** | Bot DM template — variants needed (avoid robotic repetition)? | 3-5 variants of «Доброе утро. Как спалось?» rotated. Acknowledge weekend variants («Доброго субботнего утра» etc. — Q-WS15 sub-question on timing) | UX + AI | 🟢 |
| **Q-WS16** | If customer has Mood + Sleep both active — single morning DM combining or separate? | SEPARATE prompts — distinct moments. Mood = current state, Sleep = retrospective last night. Separating preserves clarity. | UX | 🟡 |
| **Q-WS17** | Customer logs sleep then forgets to fill duration (left at default) — alert or accept? | Accept; default duration is null; `quality_score` is required field, duration optional in API. | UX | 🟢 |
| **Q-WS18** | What if customer's TZ changes mid-trip (travel) — prompt firing time? | Customer's CURRENT TZ from MAX session; updates dynamically per Q-WW5 consistency | Eng + UX | 🟡 |
| **Q-WS19** | Mini App offline sleep logging — queue + sync? | YES per Q-MAS9 pattern — up to 5 queued sleep events; 24h persistence | Eng | 🟢 |
| **Q-WS20** | If customer has high sleep variance (e.g., 4-10 hour range) — insight text? | Per §10.1 rule «High consistency» negative branch: «Длительность сильно меняется ночь к ночи.» — neutral observation, no recommendation | UX | 🟢 |

---

## 17. Cross-document linkage

- [`../policies/wellness-input-modules.md §6`](../policies/wellness-input-modules.md#6-module-4--sleep-tracking) — strategic spec ported
- [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md) — sibling Phase 2; pattern source (WellnessModuleConsent, activation gates, prompt throttle)
- [`./2026-05-19-wellness-water-handoff.md`](./2026-05-19-wellness-water-handoff.md) — sibling Phase 2; smart reminder pattern (though sleep doesn't use smart timing like water)
- [`./2026-05-19-wellness-body-handoff.md`](./2026-05-19-wellness-body-handoff.md) — sibling Phase 2; privacy + anti-pattern framework
- [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md) — morning prompt integration; DND respect
- [`../policies/core-user-states.md`](../policies/core-user-states.md) — state matrix §7
- [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md) Layer 3 + Layer 7 — aggregator writes
- [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md) — voice anchors
- [`../policies/information-architecture.md`](../policies/information-architecture.md) — Самочувствие tab placement
- [`../policies/event-taxonomy.md §3.6`](../policies/event-taxonomy.md#36-wellness-domain) — 5 NEW events §15
- [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — HUMAN_LOCKED gating + medical-routing
- [`../policies/master-conversational-templates.md §5.5`](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface) — privacy boundary
- [`../policies/customer-profile-management-ux.md §4`](../policies/customer-profile-management-ux.md) — activation entry
- [`../policies/customer-first-touch-and-mini-app-states.md §7.9`](../policies/customer-first-touch-and-mini-app-states.md) — offline sync queue Q-WS19
- [`../policies/tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md) — customer-owned data preservation during PAUSED
- [`../decisions-log.md`](../decisions-log.md) — Q-WI10 (revoke retention)

---

## 18. What this unblocks

- **`apps/wellness/sleep/` Phase 2 implementation** — model + API + Mini App engineering-ready
- **Wellness Profile Layer 3 + 7 populated with quantitative sleep data**
- **Cross-correlation foundation Phase 3+** — sleep + bookings + mood/water all feed cross-module insights
- **Demonstrates wellness OS commitment without medical scope-creep** — sleep without «coach-mode» differentiates from Oura/WHOOP
- **Pattern for retroactive-logging modules** — sleep retroactive backdate pattern reusable for «forgot to log yesterday»

## 19. What this does NOT unblock

- ❌ Sleep score / hypnogram (forbidden per §13)
- ❌ Sleep coaching / hygiene tips (out of scope ethically)
- ❌ Sleep disorder detection (medical territory)
- ❌ Wearable integration (Phase 4+)
- ❌ Wake-up alarm (out of scope)
- ❌ Smart sleep timing recommendations
- ❌ Cross-tenant sleep aggregate (privacy)
- ❌ Tenant-side sleep visibility (privacy)
- ❌ Skip pre-deploy privacy audit per §11
- ❌ Q-WS12 strategic decision (mental-health-adjacent — founder + legal sign-off before first sleep data)

---

## 20. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Wellness backend lead (apps/wellness/sleep/) | ☐ | |
| Mini App frontend (Сон section + slider + star rating + chart) | ☐ | |
| AI prompt engineering (morning prompt variants + insight generator with forbidden-phrase enforcement) | ☐ | |
| Privacy / Legal (Q-WS12 mental-health-adjacent policy + Q-WS14 extreme pattern handling) | ☐ | |
| Accessibility (WCAG 2.2 AA on slider + star rating + bar chart) | ☐ | |
| Policy review (Q-WS12 + Q-WS14 sleep-disorder routing policy) | ☐ | |

## Last verified
2026-05-19 (initial draft, engineering-ready for Phase 2 Wellness Sleep Tracker — sibling to Mood + Water + Body; anti-sleep-score + anti-medical design)
