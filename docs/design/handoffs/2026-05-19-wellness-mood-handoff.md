# Wellness Mood Module — engineering handoff

**Date:** 2026-05-19 r1
**Status:** Engineering-ready — Phase 1 first wellness module
**Reads:** [`../policies/wellness-input-modules.md`](../policies/wellness-input-modules.md) §6, [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md), [`../policies/core-user-states.md`](../policies/core-user-states.md), [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md), [`../policies/information-architecture.md`](../policies/information-architecture.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md), [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md), [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md)

> Ports [wellness-input-modules §6](../policies/wellness-input-modules.md) Mood / Energy / Stress module to engineering-ready handoff. Activation flow + consent dialog + model fields + API contracts + per-state behavior + Mini App screens + insights view. Independent app scope: `apps/wellness/`.

---

## 0. Why this exists

### The gap

[`wellness-input-modules.md §6`](../policies/wellness-input-modules.md) describes Mood module at strategic level (what it captures, why it matters, anti-patterns) but doesn't specify implementation details:

- When does customer FIRST see the consent dialog?
- What exact fields does the model store + ranges + nullable?
- Per-state-machine behavior: AI prompts in DISCOVERED? In AT_RISK_DRIFTING? Never?
- Throttling beyond «1/day» — what if customer skips 3 mornings?
- Mini App: where does Mood live in IA, what's the entry point?
- Insights view: chart? text? both?
- API contracts: endpoint shapes, payload formats
- Events emitted: which event_taxonomy types fire

Engineering would improvise these → drift from strategic intent + 5+ rounds of clarification.

### The promise

Single source for `apps/wellness/` Phase 1 «mood» module implementation. Engineering reads this + can ship without further design input.

---

## 1. Scope

### IN
- New Django app `apps/wellness/` (no conflict with existing apps)
- `WellnessModuleConsent` model (per-customer per-module opt-in tracking)
- `WellnessMoodEvent` model (mood data points)
- Activation flow: when + how customer first sees consent dialog
- Consent dialog full text + UX
- Bot DM morning prompt (per [conversational-ux-framework](../policies/conversational-ux-framework.md) voice anchors)
- Mini App «Самочувствие» tab — Mood section + quick-input + insights view
- API contracts: 5 endpoints
- Per-state behavior matrix (when AI prompts vs respects silence)
- Throttling + non-response rules (auto-pause logic)
- Events emitted per [event-taxonomy §3.6](../policies/event-taxonomy.md#36-wellness-domain)
- Privacy enforcement (strict customer-only at API layer)
- WCAG 2.2 AA baseline

### OUT
- Other wellness modules (water / body / sleep / food scanner / AI avatar / symptom) — separate handoffs
- ML pattern detection (Phase 3+; MVP shows data without insights beyond simple aggregates)
- Health-system integration (HealthKit / Google Fit — Phase 4+)
- Tenant-level configuration of mood prompts — platform-fixed defaults
- Cross-tenant mood aggregation — privacy boundary

---

## 2. Activation flow — when does customer first see consent

### 2.1 Activation triggers

Three paths to mood module activation, in priority order:

#### Path A — Self-discovery in Mini App (default, always available)
Customer navigates Профиль → Уведомления (or Профиль → Самочувствие placeholder) → sees «Помощник может отслеживать самочувствие» card with module-level toggle. Toggling ON triggers consent dialog §3.

#### Path B — Post-visit gentle offer (one-time, conditional)
After customer's FIRST `booking.completed` event AND state = POST_VISIT AND `consent.ai_messaging = true` (proactive messaging allowed):
- AI sends ONE bot DM offer (24-48h after visit)
- If customer accepts → consent dialog → activated
- If customer declines or ignores → mark `mood_offer_declined_at` in profile; never re-offer

#### Path C — Emotional-language trigger (Phase 2+)
Customer uses feeling-state words («устала», «нервничаю», «спокойно», «болит голова», «не спала») in DM. AI offers ONCE per customer (cap forever):
```
Хотите я начну отмечать самочувствие? Просто пару тапов утром — и потом видно, что влияет на ваше состояние.

[Попробовать]   [Не сейчас]
```
Same suppression: «не сейчас» → never re-offer.

**Phase 1: only Path A + Path B.** Path C deferred (NLU for emotional language complex).

### 2.2 Activation gates

Customer cannot activate Mood module if:
- `consent.ai_messaging = false` (master proactive toggle OFF per [notification-preferences §3.2](../policies/notification-preferences-ux.md#32-без-проактивных-toggle-master-switch))
  - Exception: Path A self-discovery WORKS even if proactive=off (customer is actively asking for the module). Bot DM morning prompts still suppressed.
- `core_user_state ∈ {DORMANT, HUMAN_LOCKED in active conversation}` (per [conversation-ownership-policy](../policies/conversation-ownership-policy.md))
- `WellnessModuleConsent.granted = false` AND `WellnessModuleConsent.revoked_at` is recent (within 30 days) — anti-spam: customer who opted out gets «paused» state, can re-activate but no re-offer

### 2.3 Activation events emitted
- `wellness.consent.module.granted` per [event-taxonomy §3.6](../policies/event-taxonomy.md#36-wellness-domain) with `module_name='mood'`, `granted_via='profile_settings'` OR `'post_visit_offer'`
- (NEW — add) `wellness.module.activated` with `module_name='mood'` aggregator-friendly

---

## 3. Consent dialog

### 3.1 When fired
First time customer opts IN via Path A or B (per §2.1).

### 3.2 UI — Mini App modal

```
┌────────────────────────────────────────┐
│ Отмечать самочувствие?                 │
├────────────────────────────────────────┤
│ Каждое утро я буду спрашивать одним    │
│ касанием как вы. Это поможет:          │
│                                        │
│   • Подобрать процедуры под состояние  │
│   • Увидеть, что влияет на самочувствие│
│   • Не пропустить момент, когда нужно  │
│     просто отдохнуть                   │
│                                        │
│ ── Что важно ──                        │
│                                        │
│ ✓ Видите только вы                     │
│ ✓ Студия НЕ видит эти данные           │
│ ✓ Можно выключить в любой момент       │
│ ✓ Удалить — в один клик                │
│                                        │
│ Утреннее сообщение приходит:           │
│ ◯ В 8:00     ◉ В 9:00     ◯ В 10:00   │
│ ◯ Не нужно — отмечу сама(сам)          │
│                                        │
│ [Не сейчас]      [Согласна, попробуем] │
└────────────────────────────────────────┘
```

### 3.3 Forbidden in consent dialog
- ❌ Pre-selected «Согласна» (must be explicit tap)
- ❌ «Sign up for free!» marketing framing
- ❌ Cross-sell («премиум модули» mention)
- ❌ Multi-page wizard (one screen, max)
- ❌ Hidden secondary opt-ins («also enable analytics?»)
- ❌ Default time other than 9:00 (sensible morning baseline)

### 3.4 Voice anchor
Per [conversational-ux-framework](../policies/conversational-ux-framework.md) — Warm + Calm + Premium-but-accessible. No exclamations, no emoji in body.

### 3.5 Outcomes

#### Tap «Согласна, попробуем»
- Create `WellnessModuleConsent(customer_id, tenant_id, module_name='mood', granted=True, granted_at=NOW, granted_via=path, morning_prompt_time=selected_time OR null)`
- If `morning_prompt_time` set: enable bot DM morning prompt per §6
- Emit `wellness.consent.module.granted` event
- Show success toast: «Готово. Если что-то не подойдёт — выключить можно в настройках.»
- Return to wherever customer came from

#### Tap «Не сейчас»
- NO record created (customer hasn't opted into anything)
- If activation triggered by Path B (post-visit offer): mark `customer.mood_offer_declined_at = NOW` in customer record metadata — prevents re-offer
- If activation triggered by Path A (self-discovery): no flag set; customer can re-open consent dialog by tapping toggle again

### 3.6 Re-activation after revoke
Customer who previously had `granted=True` then revoked (toggled OFF or in notification-preferences):
- `WellnessModuleConsent.revoked_at = revoke_time` stored
- Mood events history preserved 30 days then anonymized per [§9 privacy](../policies/wellness-input-modules.md#9-cross-module-integration)
- Customer can re-grant via Path A; consent dialog appears again (full transparency); new `granted_at` timestamp

---

## 4. Data models

### 4.1 `WellnessModuleConsent`

Tracks customer's opt-in state per wellness module. Reused for all modules (water / body / sleep / etc. — not just mood).

```python
class WellnessModuleConsent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='wellness_consents')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    MODULE_CHOICES = [
        ('mood', 'Mood / Energy / Stress'),
        ('water', 'Water Tracker'),
        ('body', 'Body Tracking'),
        ('sleep', 'Sleep Tracking'),
        ('food', 'Food Scanner'),
        ('avatar', 'AI Avatar'),
        ('symptom', 'Symptom Diary'),
    ]
    module_name = models.CharField(max_length=32, choices=MODULE_CHOICES)

    granted = models.BooleanField(default=False)
    granted_at = models.DateTimeField(null=True, blank=True)
    granted_via = models.CharField(max_length=32, null=True, blank=True)
    # values: 'profile_settings' / 'post_visit_offer' / 'emotional_trigger' / 'admin_grant' (rare)

    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=64, null=True, blank=True)
    # values: 'user_toggle' / 'no_response_pause' / 'data_export_request' / 'account_deletion'

    # Module-specific config (JSON for extensibility; mood uses morning_prompt_time only in Phase 1)
    config = models.JSONField(default=dict)
    # For mood: {"morning_prompt_time": "09:00" | null}

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=['customer', 'tenant', 'module_name'], name='uq_wellness_consent_per_tenant_module'),
        ]
        indexes = [
            Index(fields=['customer', 'granted']),
            Index(fields=['tenant', 'module_name', 'granted']),
        ]
```

### 4.2 `WellnessMoodEvent`

Each customer-recorded mood data point. Append-only.

```python
class WellnessMoodEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='mood_events')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    recorded_at = models.DateTimeField()  # customer's tap time

    # Quick-mode (Path A 1-tap morning prompt) — 4-emoji choice
    MOOD_EMOJI_CHOICES = [
        ('excellent', '😊 Отлично'),
        ('good', '🙂 Норм'),
        ('neutral', '😐 Так себе'),
        ('down', '😣 Тяжко'),
    ]
    mood_emoji = models.CharField(max_length=16, choices=MOOD_EMOJI_CHOICES, null=True, blank=True)

    # Detailed-mode (Mini App sliders) — Phase 2 detailed
    energy_score = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(10)])
    stress_score = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(10)])
    mood_score = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(10)])

    # Optional pain flag (Phase 1.5)
    pain_flag = models.BooleanField(default=False)
    pain_zones = models.JSONField(default=list, blank=True)
    # values: ['head', 'neck', 'shoulders', 'back', 'legs', 'other']

    # Free text — max 280 chars
    note = models.TextField(max_length=280, blank=True, default='')

    SOURCE_CHOICES = [
        ('bot_morning', 'Bot DM morning prompt'),
        ('mini_app_quick', 'Mini App quick chip on home'),
        ('mini_app_detailed', 'Mini App detailed sliders'),
    ]
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['customer', 'recorded_at']),  # for insights view
            Index(fields=['tenant', 'recorded_at']),  # for analytics aggregation
        ]
        constraints = [
            CheckConstraint(
                # Either quick (mood_emoji set) OR detailed (at least one score set)
                check=Q(mood_emoji__isnull=False) | Q(energy_score__isnull=False) | Q(stress_score__isnull=False) | Q(mood_score__isnull=False),
                name='ck_mood_event_has_data',
            ),
        ]
```

### 4.3 Migration

```python
# Migration 0001_initial:
# - Create WellnessModuleConsent
# - Create WellnessMoodEvent
# No backfill — fresh tables
```

---

## 5. Per-state behavior matrix

When AI prompts mood (morning prompt fires) per customer state. All gated by `WellnessModuleConsent.granted = True` first.

| Customer state (per core-user-states) | Morning prompt fires? | Why |
|---|---|---|
| DISCOVERED | NO | No relationship yet; activation gate prevents |
| EXPLORING | NO (unless explicitly activated via Path A) | Customer still discovering |
| PROBLEM_SEEKING | YES if activated | Mood data helps AI tone |
| READY_TO_BOOK | YES if activated | Background data continues |
| POST_VISIT | YES if activated | Trust moment for activation OR for continued use |
| ACTIVE_REGULAR | YES if activated | Steady-state use |
| AT_RISK_DRIFTING | YES if activated AND last `mood_event.recorded_at` < 14 days ago | Respect drifting silence; don't add noise |
| AT_RISK_DRIFTING + no mood recorded 14+ days | NO; pause module per §6.4 throttle | Respect deliberate silence |
| DORMANT | NO (terminal state, no proactive) | Per ownership-policy |
| HUMAN_LOCKED active conversation | NO (admin owns conversation) | Per ownership-policy |
| HUMAN_LOCKED inactive (resolved) | YES if activated | Resume normal |

---

## 6. Bot DM morning prompt

### 6.1 Trigger conditions (ALL must be true)
- `WellnessModuleConsent.granted = True`
- `WellnessModuleConsent.config.morning_prompt_time` is set (not null)
- Per-state allowance per §5
- Current time = customer's `morning_prompt_time` in customer's TZ (±15min window)
- No mood event recorded today (anti-double-send)
- Not in DND per [notification-preferences §7](../policies/notification-preferences-ux.md#7-dnd-do-not-disturb-windows)

### 6.2 Bot DM template

**Voice anchor**: per [conversational-ux-framework](../policies/conversational-ux-framework.md) — Warm + Calm + Concise.

```
Доброе утро. Как вы сегодня?

[😊 Отлично]  [🙂 Норм]  [😐 Так себе]  [😣 Тяжко]
[Подробнее →]
```

«Подробнее →» — deep link to Mini App detailed-mode screen §7.2.

### 6.3 Post-response feedback (per emoji choice)

| Customer tap | Response (NO follow-up question) |
|---|---|
| 😊 Отлично | `«Здорово.»` (single word; respects pleasant state) |
| 🙂 Норм | `«Поняла.»` (acknowledgement, no probing) |
| 😐 Так себе | `«Поняла. Если что-то нужно — рядом.»` (gentle door-open) |
| 😣 Тяжко | `«Понимаю. Если хочется отдохнуть — посмотрите расслабляющие процедуры.»` + inline button «[Посмотреть]» |

**Forbidden after-response**:
- ❌ «Расскажите подробнее!»
- ❌ «А что случилось?»
- ❌ Multiple follow-up questions
- ❌ Demanding journaling

«😣 Тяжко» soft-services-offer is the ONLY contextual add — based on Q-WI8 lean (low mood + chronic suggests relaxation service offer). NOT for «😐 Так себе» — that's too aggressive.

### 6.4 Non-response throttle

Per [notification-preferences §6](../policies/notification-preferences-ux.md#6-frequency-caps--throttling) extended for mood specifics:

| Consecutive mornings no response | Next-day behavior |
|---|---|
| 1 | Send normal prompt next morning |
| 2 | Send normal prompt next morning |
| 3 | **Skip 1 day** (give customer a break) |
| 4 | Resume normal prompt |
| 5 | **Pause module 7 days**, send «Не вижу ваших отметок — поставила утренние сообщения на паузу. Включить обратно — в настройках Самочувствия.» |
| After 7-day pause | If customer doesn't re-enable, module stays paused. No more auto-prompts. |

Reset to «consecutive = 0» on any response (any source — bot tap, Mini App entry).

### 6.5 First-week onboarding nudge (NEW user of module only)

Days 1-7 after activation, slightly more encouraging single-message variant ONCE:
- Day 3 (if customer has responded 1-2 times): «Здорово, что отмечаете — данные за неделю покажу.» (no CTA, just warm note)
- Day 7 (if customer has responded 3+ times): brief insights summary unlock — single message «Что заметила за неделю» + «[Открыть] [Не сейчас]»

After day 7: no more onboarding-specific messages. Steady state.

---

## 7. Mini App «Самочувствие» tab — Mood section

### 7.1 Section anatomy (within Самочувствие tab per IA)

```
┌────────────────────────────────────────┐
│ Самочувствие                           │
├────────────────────────────────────────┤
│ ── Сегодня ──                          │
│                                        │
│ Как вы сейчас?                         │
│ [😊]  [🙂]  [😐]  [😣]                  │
│ [Подробнее →]                          │
│                                        │
│ ── Активные модули ──                  │
│ ✓ Самочувствие                          │
│ ☐ Вода         [Включить →]           │
│ ☐ Сон          [Включить →]           │
│ ☐ Параметры    [Включить →]           │
│                                        │
│ ── Вижу за неделю ──                   │
│ [мини-чарт settimana]                  │
│ [Открыть инсайты →]                    │
│                                        │
└────────────────────────────────────────┘
```

«Как вы сейчас?» quick-chip is ALWAYS visible (top of screen) for activated module — saves a tap vs going to Профиль. Same options as bot DM §6.2. Source = `mini_app_quick`.

### 7.2 Detailed entry screen

```
┌────────────────────────────────────────┐
│ ← Как вы сегодня                       │
├────────────────────────────────────────┤
│                                        │
│ Энергия                                │
│ [────────────●────] 7 из 10            │
│ нет сил         бодрая(ый)             │
│                                        │
│ Стресс                                 │
│ [──●──────────────] 2 из 10            │
│ спокойно         напряжённо            │
│                                        │
│ Настроение                             │
│ [─────────●──────] 6 из 10             │
│ грустно          радостно              │
│                                        │
│ Что-то болит? ☐ да                     │
│                                        │
│ Заметка (опц.)                         │
│ [________________________]             │
│ 0/280                                  │
│                                        │
│ [Сохранить]                            │
└────────────────────────────────────────┘
```

Tap pain ☐ expands to multi-select chips: «голова», «шея», «плечи», «спина», «ноги», «другое».

### 7.3 Save behavior

- POST `/api/v1/customer/wellness/mood` with `source='mini_app_detailed'`
- 5-sec undo toast: «Сохранено · Отменить»
- After 5s: permanent; return to Самочувствие tab
- NO confetti, NO streak counter, NO «day 5 in a row!» feedback (per [wellness-input-modules §6.6](../policies/wellness-input-modules.md#66-anti-patterns) anti-streak rule)

### 7.4 Quick-chip on home (Главная)

State-adaptive home per [information-architecture](../policies/information-architecture.md) shows mood quick-chip ONLY when:
- Mood module activated
- No mood event today yet
- Current hour ≥ morning prompt time (don't pre-empt morning prompt)

```
┌────────────────────────────────────────┐
│ Как вы сегодня?                        │
│ [😊]  [🙂]  [😐]  [😣]                  │
└────────────────────────────────────────┘
```

Tap = save with `source='mini_app_quick'` + dismiss chip for the day.

---

## 8. Insights view (Phase 1 simple)

### 8.1 When unlocked
- 7+ data points recorded
- Customer taps «Открыть инсайты →» from Самочувствие tab

### 8.2 View — chart + text

```
┌────────────────────────────────────────┐
│ ← Инсайты                              │
├────────────────────────────────────────┤
│                                        │
│ ── За 7 дней ──                        │
│                                        │
│ Энергия:    среднее 6.4 / 10           │
│   [линейный chart 7 дней]              │
│                                        │
│ Стресс:     среднее 4.2 / 10           │
│   [линейный chart 7 дней]              │
│                                        │
│ Настроение: среднее 6.8 / 10           │
│   [линейный chart 7 дней]              │
│                                        │
│ ── Что заметно ──                      │
│                                        │
│ Стресс был выше во вторник и среду     │
│ (8 и 7).                               │
│                                        │
│ ── Период ──                           │
│ [7 дней]  [30 дней]                    │
│                                        │
└────────────────────────────────────────┘
```

### 8.3 Phase 1 «Что заметно» logic
Simple rules:
- If max(stress) - min(stress) ≥ 4 over period: «Стресс колебался — {{max_day}} был выше, {{min_day}} — спокойнее»
- If avg(mood) ≤ 4 over period: «Настроение за этот период ниже обычного. Если нужно — посмотрите расслабляющие процедуры.» + button
- If avg(energy) ≤ 3 over period: «Энергия низкая. Возможно, стоит больше отдыхать.»
- Otherwise: «Стабильно за период.»

No ML, no claims AI doesn't know.

### 8.4 Phase 3+ ML insights (out of scope for handoff)
- Day-of-week patterns
- Cross-module correlation (mood vs sleep vs water)
- Predictive flags

---

## 9. API contracts

### 9.1 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/customer/wellness/consent` | Customer | Grant/revoke module consent |
| GET | `/api/v1/customer/wellness/consent` | Customer | List consent state per module |
| POST | `/api/v1/customer/wellness/mood` | Customer | Save mood event |
| GET | `/api/v1/customer/wellness/mood` | Customer | List own mood events (paginated, range filter) |
| GET | `/api/v1/customer/wellness/mood/summary` | Customer | Aggregate summary for insights view |

### 9.2 POST `/api/v1/customer/wellness/consent`

**Request**:
```json
{
  "module_name": "mood",
  "granted": true,
  "granted_via": "profile_settings",
  "config": {
    "morning_prompt_time": "09:00"
  }
}
```

**Response** (200):
```json
{
  "id": "uuid",
  "module_name": "mood",
  "granted": true,
  "granted_at": "2026-05-19T07:00:00Z",
  "config": {"morning_prompt_time": "09:00"}
}
```

**Errors**:
- 400: invalid `module_name` or `morning_prompt_time` format
- 403: customer trying to grant module they're not eligible for (e.g., DORMANT state)
- 409: consent record already in target state (no-op)

### 9.3 POST `/api/v1/customer/wellness/mood`

**Request — quick mode** (1-tap):
```json
{
  "mood_emoji": "good",
  "source": "bot_morning"
}
```

**Request — detailed mode**:
```json
{
  "energy_score": 7,
  "stress_score": 2,
  "mood_score": 6,
  "pain_flag": false,
  "note": "после массажа спина отдыхает",
  "source": "mini_app_detailed"
}
```

**Validation** (server-side):
- At least one of `mood_emoji`, `energy_score`, `stress_score`, `mood_score` MUST be set (CheckConstraint at DB level too)
- `energy_score` / `stress_score` / `mood_score` in [1, 10]
- `pain_zones` if present must be subset of allowed enum
- `note` max 280 chars
- `source` must be in enum
- Customer must have `WellnessModuleConsent.granted = True` for `module_name='mood'` — else 403

**Response** (201):
```json
{
  "id": "uuid",
  "recorded_at": "2026-05-19T09:01:32Z",
  "summary": "Сохранено."
}
```

### 9.4 GET `/api/v1/customer/wellness/mood`

**Query params**:
- `from` (ISO date, optional)
- `to` (ISO date, optional)
- `limit` (default 30, max 90)

**Response** (200):
```json
{
  "events": [
    {
      "id": "...",
      "recorded_at": "...",
      "mood_emoji": "good",
      "energy_score": null,
      "stress_score": null,
      "mood_score": null,
      "pain_flag": false,
      "note": "",
      "source": "bot_morning"
    },
    ...
  ],
  "pagination": {"limit": 30, "has_more": false}
}
```

### 9.5 GET `/api/v1/customer/wellness/mood/summary`

**Query params**:
- `period_days` (7 default, 30 max in Phase 1)

**Response** (200):
```json
{
  "period_days": 7,
  "data_points_count": 6,
  "averages": {
    "energy": 6.4,
    "stress": 4.2,
    "mood": 6.8
  },
  "ranges": {
    "energy": {"min": 4, "max": 8, "min_day": "2026-05-13", "max_day": "2026-05-18"},
    "stress": {"min": 1, "max": 8, "min_day": "...", "max_day": "..."},
    "mood": {"min": 5, "max": 9, "min_day": "...", "max_day": "..."}
  },
  "chart_series": {
    "energy": [{"date": "2026-05-13", "value": 4}, ...],
    "stress": [...],
    "mood": [...]
  },
  "insight_text": "Стресс был выше во вторник и среду (8 и 7).",
  "service_recommendation": null
}
```

If `data_points_count < 3`: return summary with `insight_text: "Пока недостаточно данных. Попробуйте отмечать самочувствие ещё несколько дней."` and empty chart series for unknown days.

---

## 10. Events emitted

Per [event-taxonomy §3.6 wellness domain](../policies/event-taxonomy.md#36-wellness-domain):

| Trigger | Event name | Payload keys |
|---|---|---|
| Consent granted | `wellness.consent.module.granted` | `customer_id`, `module_name`, `granted_at`, `granted_via` |
| Consent revoked | `wellness.consent.module.revoked` | `customer_id`, `module_name`, `revoked_at`, `revoked_reason` |
| Mood event saved | `wellness.input.recorded` | `customer_id`, `module_name='mood'`, `input_type` (quick/detailed), `confidence=1.0` (self-reported), `source` |
| Aggregator writes to profile | `wellness.profile.layer.updated` | `customer_id`, `layer_name='layer_3_body_state'` or `'layer_7_emotional'`, `field`, `source='mood_module'` |
| Pattern detected (Phase 3+) | `wellness.insight.generated` | `customer_id`, `insight_id`, `insight_type='mood_pattern'`, `confidence`, `evidence_refs` |
| Recommendation shown to customer | `wellness.recommendation.shown` | `customer_id`, `recommendation_id`, `surface='bot_dm'/'mini_app_home'`, `service_id` (if applicable) |

All events include standard envelope per [event-taxonomy §2](../policies/event-taxonomy.md#2-envelope-structure-every-event) (event_id, tenant_id, actor, occurred_at).

---

## 11. Privacy enforcement at API

### 11.1 Customer-only access — hard rule

All `/api/v1/customer/wellness/*` endpoints:
- Require customer auth (MaxUser of `customer.bot_user`)
- Return ONLY the calling customer's data
- 403 if tenant_id in URL or header doesn't match customer's tenant

### 11.2 Tenant-side endpoints

NONE in Phase 1. Salon side (owner/admin/master) has ZERO access to mood data.

If salon side needs aggregate (e.g., «N% of our customers tracked mood this week») — Phase 2+ via separate analytics aggregation pipeline that strips per-customer identifiers.

Master `pre_arrival_context` per [master-conversational-templates §5.5](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface) shows:
- Layer 4 service history reactions: ✅
- Layer 6 nutrition inputs: ❌
- **Layer 7 emotional / Layer 3 body state mood inputs: ❌**

### 11.3 Logging

- API calls logged WITHOUT payload (event ID + path + outcome only)
- Mood event payload logged ONLY at TRACE level (off in prod)
- PII detector enforces per [event-taxonomy §6](../policies/event-taxonomy.md#6-pii-rules) — `note` field treated as freeform sensitive

### 11.4 Retention

Per [Q-WI10 lean](../decisions-log.md):
- Mood events retained Layer 3 sensitive per Q-C3 4-layer policy
- On `customer.deleted_request`: 30-day soft-delete → hard-delete
- No anonymization that retains stats (overly identifying for small samples)

---

## 12. Wellness Profile integration

### 12.1 Aggregator job

Daily Celery beat (or async on mood event save — engineering choice):
- Compute Layer 3 + Layer 7 derived fields from mood events:
  - `layer_3_body_state.recent_avg_energy_7d` (last 7 days avg)
  - `layer_3_body_state.recent_avg_stress_7d`
  - `layer_7_emotional.recent_mood_trend` (rising / stable / declining)
- Emit `wellness.profile.layer.updated` per aggregation

### 12.2 Customer Wellness Profile read endpoint (Phase 2)
Out of scope for this handoff. Mood module writes; profile aggregator + customer-facing dashboard separate handoff.

---

## 13. Anti-patterns specific to engineering

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Allow saving mood event when `granted = false` | Spec violation + privacy issue | 403 at API; never bypass |
| Don't enforce CheckConstraint at DB | Empty events pollute data | DB constraint AND application validator both |
| Send morning prompt before customer's TZ morning | Wakes customer at 4am | Always customer TZ; verify before send |
| Send morning prompt during DND | Anti-spam violation | Check DND per notification-preferences |
| Recompute Wellness Profile layer fields on every mood event | Performance toll | Batch in daily aggregator OR async |
| Expose mood data in salon-side admin endpoints | Privacy violation | NEVER — no tenant-side mood endpoints |
| Log mood note text at INFO level | PII leak | TRACE only |
| Allow mood event recorded_at > now | Bogus data | Validate recorded_at ≤ now + 5 sec tolerance |
| Allow concurrent duplicate mood events same minute | Data pollution | Application-level dedup (or DB unique) |
| Hard-delete mood events on customer revoke | Loses 30d soft-delete grace | Always 30d soft-delete window first |

---

## 14. Acceptance criteria (engineering checklist)

- [ ] `apps/wellness/` Django app created + registered in `LOCAL_APPS`
- [ ] Migration 0001_initial creates `WellnessModuleConsent` + `WellnessMoodEvent`
- [ ] CheckConstraint `ck_mood_event_has_data` enforced at DB
- [ ] 5 API endpoints implemented + tested
- [ ] Customer auth required; tenant boundary enforced; 403 on mismatch
- [ ] Activation Paths A + B implemented; Path C deferred
- [ ] Consent dialog UI in Mini App
- [ ] Morning prompt Celery beat (or scheduled task) per `morning_prompt_time` + customer TZ + DND check
- [ ] Per-state behavior matrix §5 enforced
- [ ] Throttle §6.4 implemented (consecutive no-response → skip → pause logic)
- [ ] Mini App «Самочувствие» tab with mood section
- [ ] Quick-chip on Главная state-adaptive
- [ ] Insights view §8 with simple rules-based insights
- [ ] Events emitted per §10
- [ ] Privacy enforcement per §11 — including pre-deployment audit «no tenant-side mood data leak»
- [ ] Tests: unit (model + service) + API (endpoint + auth) + integration (consent → mood → insights flow) + privacy (cross-tenant denial)
- [ ] Linter: persona-conformance check on all bot DM templates
- [ ] Accessibility audit on Mini App screens (WCAG 2.2 AA)
- [ ] Documentation in `apps/wellness/README.md` referencing this handoff

---

## 15. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-WM1** | Default `morning_prompt_time` if customer doesn't pick — 9:00 or null (no auto-prompt)? | 9:00 default per Q-WI5 lean; customer can change in consent dialog | UX | 🟢 |
| **Q-WM2** | Path B post-visit offer — when exactly fires? T+24h or T+48h? | T+24h next morning (catches «как самочувствие после процедуры?» moment) | UX | 🟢 |
| **Q-WM3** | Path B offer suppressed if customer already opted in via Path A? | YES — check consent state first; never offer if already granted | Eng | 🟢 |
| **Q-WM4** | If customer revokes mood module, what happens to mood events already recorded? | 30d soft-delete window (per Q-WI10), then hard-delete. Insights view returns 404 during soft-delete. | Eng + Privacy | 🟡 |
| **Q-WM5** | Bot DM 4-emoji prompt — change emojis per tenant brand or platform-fixed? | Platform-fixed (consistency + accessibility); v1.2+ tenant override | UX | 🟢 |
| **Q-WM6** | «😣 Тяжко» response — service recommendation always or only if catalog has relaxation services? | Only if `salon catalog has services tagged with 'relaxation' or 'stress-relief'`; otherwise just empathetic acknowledgement | UX + Eng | 🟡 |
| **Q-WM7** | Pain flag → does it write to Layer 4 service history reactions or stay only in mood event? | Mood event only Phase 1; cross-module pain tracking via separate Symptom Diary module (Phase 2) | Eng | 🟢 |
| **Q-WM8** | First-week onboarding nudge §6.5 — count by «days since consent» or «days since first response»? | Days since consent (continuous; works even if customer skipped some). Cleaner mental model. | UX | 🟢 |
| **Q-WM9** | Insights view chart library — Recharts (lightweight) or custom SVG? | Recharts MVP — proven, accessible; consistent with future wellness modules | Eng | 🟢 |
| **Q-WM10** | Quick-chip on Главная — show even if customer DOES have today's mood event but it's quick-mode (could upgrade to detailed)? | NO — once recorded today, dismiss for the day. Don't pressure for more data. | UX | 🟢 |
| **Q-WM11** | If customer's TZ unknown, fallback for morning prompt? | UTC+3 (Moscow) MVP — RU customer base; revisit when expanding | Eng | 🟢 |
| **Q-WM12** | Customer-side data export request for mood data — Phase 1 or defer? | Defer Phase 2+; minimal export via admin support email (OP6 process) MVP | Privacy | 🟢 |
| **Q-WM13** | Pre-arrival context master sees — should it include «had low mood last 3 days»? | NO Phase 1 — strict customer-only privacy. Phase 2+ if explicit customer-to-master share consent. | Privacy + UX | 🟡 |
| **Q-WM14** | Insights service_recommendation — auto-deeplink to booking or just show service name? | Show service name + «[Записаться]» button → deeplinks to F2 master picker for that service. Same as wellness-input-modules §6.4 recommendations. | UX | 🟢 |
| **Q-WM15** | Concurrent same-minute mood event POST — dedup at API or trust idempotency? | Application-level dedup: if existing event within 60 sec, return 200 with existing event_id (idempotent). | Eng | 🟡 |

---

## 16. Cross-document linkage

- [`../policies/wellness-input-modules.md`](../policies/wellness-input-modules.md) §6 — strategic spec this handoff ports
- [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md) — opt-in/throttle/DND rules integrated
- [`../policies/core-user-states.md`](../policies/core-user-states.md) — state matrix §5 references
- [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md) §3 Layer 7 + Layer 3 — aggregator writes here
- [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md) — voice anchors for bot DM templates
- [`../policies/information-architecture.md`](../policies/information-architecture.md) — Самочувствие tab placement
- [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md) §3.6 — events emitted
- [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — HUMAN_LOCKED gating
- [`../policies/master-conversational-templates.md`](../policies/master-conversational-templates.md) §5.5 — privacy boundary on pre-arrival context
- [`../policies/attribution-policy.md`](../policies/attribution-policy.md) — mood-driven service recommendation may set `ai_assist_score` for future booking
- [`../decisions-log.md`](../decisions-log.md) — Q-WM* to be added on next batch

---

## 17. What this unblocks

- **`apps/wellness/` Phase 1 implementation** — model + API + bot DM + Mini App + insights all engineering-ready
- **Customer Wellness Profile foundation** — first module to actually populate Layer 3 + 7
- **Notification preferences validation** — N10 event type lands real data
- **Demonstrates wellness OS vector** in shipped product (first time)
- **Pattern for other modules** — water / sleep / body follow this handoff's structure

## 18. What this does NOT unblock

- ❌ Other wellness modules (need separate handoffs per pattern here)
- ❌ Tenant-side wellness analytics (privacy boundary)
- ❌ ML pattern detection (Phase 3+)
- ❌ Wearable integration (Phase 4+)
- ❌ Cross-module insights (need water/sleep modules first)
- ❌ Customer wellness dashboard «view all my data» (Phase 2+ separate handoff)
- ❌ Skip pre-deploy privacy audit per §11

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Wellness backend lead (apps/wellness/) | ☐ | |
| Mini App frontend (Самочувствие tab + quick chip) | ☐ | |
| AI prompt engineering (4-emoji response variants + service recommendation) | ☐ | |
| Privacy / Legal (Q-WM13 master pre-arrival, Q-WM4 revoke retention) | ☐ | |
| Accessibility (WCAG 2.2 AA on consent dialog + sliders + chart) | ☐ | |

## Last verified
2026-05-19 (initial draft, engineering-ready for Phase 1 Wellness Mood module — sibling agent dispatch)
