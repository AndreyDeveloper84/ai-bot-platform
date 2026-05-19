# Wellness Body Tracker Module — engineering handoff

**Date:** 2026-05-19 r1
**Status:** Engineering-ready — Phase 2 wellness module №3 (after Mood + Water)
**Reads:** [`../policies/wellness-input-modules.md`](../policies/wellness-input-modules.md) §4, [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md), [`./2026-05-19-wellness-water-handoff.md`](./2026-05-19-wellness-water-handoff.md), [`./2026-05-19-wellness-ai-avatar-handoff.md`](./2026-05-19-wellness-ai-avatar-handoff.md), [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md), [`../policies/core-user-states.md`](../policies/core-user-states.md), [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md), [`../policies/information-architecture.md`](../policies/information-architecture.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md), [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md), [`../policies/customer-profile-management-ux.md`](../policies/customer-profile-management-ux.md)

> Ports [wellness-input-modules §4](../policies/wellness-input-modules.md#4-module-3--body-tracking) Body Tracking module. Phase 2 (sibling to Mood + Water). Bi-weekly cadence, anti-OCD + anti-shame critical, privacy near-AI-Avatar tier. Simplest Phase 2 AI insights; Phase 3+ cross-correlation with services.

---

## 0. Why this exists

### Strategic context

Body Tracker = customer logs weight + body measurements (waist / hips / chest / thigh) over time. Three reasons it matters:

1. **Customer sees objective progress** — without photos (AI Avatar is Phase 3), just numbers. «Талия 72 см сегодня vs 74 см месяц назад».
2. **AI correlates with services** — «после курса 4 лимфодренажа за месяц талия −2 см» (observation, never causation claim).
3. **Anchor for AI Avatar Phase 3+** — photo + measurement same date = enhanced comparison view.
4. **Wellness Profile Layer 3 Body State** populated with real data (not inferred).

### The gap

[`wellness-input-modules §4`](../policies/wellness-input-modules.md#4-module-3--body-tracking) describes Body Tracking strategically but doesn't specify:
- Activation flow + which paths
- Model fields + validators + units
- Per-state behavior
- Mini App layout (measurement entry + trend chart + history)
- Cadence enforcement + reminder rules
- Cross-correlation with services + AI Avatar
- Privacy enforcement (this module is high-sensitivity)
- Anti-pattern enforcement at API level (anti-OCD, anti-shame)

### The promise

Single source for `apps/wellness/body/` Phase 2 implementation. Engineering ships without ambiguity. Reviewers reject anything violating anti-pattern §17.

---

## 1. Scope

### IN
- New sub-module `apps/wellness/body/` (within existing `apps/wellness/` app)
- `WellnessBodyMeasurement` model (single row per logging event)
- Activation Paths A + B (Path C deferred Phase 2.5+)
- Consent dialog with anti-OCD frame + reminder OFF default
- Bi-weekly cadence (suggested; customer-controlled)
- Opt-in reminders (NOT proactive by default — different from Mood/Water)
- 5 API endpoints
- Mini App Самочувствие → Параметры section
- Add measurement flow + edit/delete history
- Trend chart (Phase 2 minimal)
- Phase 2 simple-rules AI insights (no judgmental copy)
- Events emitted per [event-taxonomy §3.6](../policies/event-taxonomy.md#36-wellness-domain)
- Strict privacy enforcement (customer-only; soft-delete 30d on revoke)
- Wellness Profile Layer 3 integration
- Cross-module synergy stubs (AI Avatar Phase 3+; Wellness Goals Phase 3+)

### OUT
- BMI display / calculation (medical territory; forbidden per §17)
- Body fat % / muscle mass (Phase 3+ if scale integration)
- Weight target / weight loss goals (diet-app territory; forbidden)
- Imperial units lbs/inches (Phase 4+ international expansion)
- Diet / exercise recommendations (out of scope)
- Comparison with other customers (privacy violation)
- Tenant-side aggregate (privacy boundary)
- Wearable scale integration — Withings / Garmin / etc. (Phase 3+ if customer demand)
- Pregnancy weight tracking modes (Phase 4+ specific use cases)
- Customer-pays gating (free forever per Q-WI12 lean)

---

## 2. Strategic constraints — non-negotiable

These are «cannot-be-compromised» rules. Engineering review rejects any PR violating them.

### 2.1 Anti-OCD mandate (most critical)
- **Cadence**: suggested bi-weekly; daily logging allowed but NO encouragement of it
- **No streaks** of any kind (counter, badge, motivational copy)
- **No daily nudges** even if customer logs daily
- **Customer-control** over cadence is absolute — system never pushes for more frequent

### 2.2 Anti-shame frame (equally critical)
- **No judgmental copy**: «вы поправились» / «вы похудели» FORBIDDEN — neutral facts only («вес 68.5 кг vs 67 кг месяц назад»)
- **No «good/bad» direction language** — body change is neither good nor bad inherently
- **No emoji for direction** (no 📈/📉 implying judgment)
- **No motivational quotes / coaching**
- **Customer's body is theirs** — system observes, never evaluates

### 2.3 No medical claims
- BMI calculation FORBIDDEN even if customer provides height
- «ожирение» / «недовес» / «здоровый вес» categories FORBIDDEN
- Body fat percentages from formulas (Phase 3+ ONLY from real scales, never calculated)
- For any health-adjacent concerns customer mentions, AI routes to medical specialist per [`conversational-ux-framework §7.2`](../policies/conversational-ux-framework.md)

### 2.4 Privacy hierarchy (high — second only to AI Avatar)
- Strict customer-only at API
- NEVER on salon side, even aggregate
- Master pre-arrival context shows NO body data
- Soft-delete 30d → hard-delete on revoke
- Data export per OP6 includes raw body data
- Founder access only via legal hold + 4-eye approval (same standard as AI Avatar)

---

## 3. Activation flow

### 3.1 Eligibility (gates) — same as Mood/Water

Customer cannot activate Body Tracker if:
- `consent.ai_messaging = false` (exception: Path A self-discovery works)
- `core_user_state ∈ {DORMANT, HUMAN_LOCKED active conversation}`
- Tenant in PAUSED / SUSPENDED state
- Customer's MAX account suspended
- Customer < 18 years old per onboarding verification (same as AI Avatar §3.1 — body data + minors = ethical / legal red line)

### 3.2 Activation triggers (Phase 2 launch)

**Path A — Self-discovery in Mini App** (always available):
Customer navigates Профиль → Самочувствие → Параметры card → toggle ON → consent dialog §4.

**Path B — Post-cosmetology-procedure offer**:
After customer completes first booking in cosmetology category (massage, lymphatic drainage, body-shaping, anti-cellulite, etc.) AND has `consent.ai_messaging = true`, AI sends ONE offer (T+24h):
```
Хотите видеть прогресс по телу — параметры до/после?

Я могу запоминать вес и объёмы. Видите только вы.

[Попробовать]   [Не сейчас]
```

Same Path B suppression rule: «не сейчас» → never re-offer (mark `customer.body_offer_declined_at`).

**Path C — Customer initiates DM** (Phase 2.5+):
Customer messages «хочу следить за фигурой» / «как у меня с параметрами» → AI suggests module. Phase 2.5+ NLU work needed.

### 3.3 Activation events
- `wellness.consent.module.granted` with `module_name='body'`, `granted_via=<path>`

---

## 4. Consent dialog

### 4.1 Single-screen with anti-OCD framing

```
┌────────────────────────────────────────┐
│ Отслеживать параметры?                 │
├────────────────────────────────────────┤
│ Я буду:                                │
│   • Запоминать вес и объёмы            │
│   • Показывать изменения за период      │
│                                        │
│ ── Как часто отмечать ──                │
│                                        │
│ Раз в 2 недели — обычно достаточно.    │
│ Можно реже или чаще как удобно.        │
│                                        │
│ ── Напоминания ──                       │
│                                        │
│ ◯ Да, напоминай раз в 2 недели          │
│ ⦿ Нет, отмечу сама(сам)                 │
│                                        │
│ (По умолчанию выключено — параметры —  │
│  чувствительная тема, лучше когда вы   │
│  сами захотите.)                       │
│                                        │
│ ── Что важно ──                         │
│                                        │
│ ✓ Только вы видите эти данные          │
│ ✓ Студия НЕ видит ничего                │
│ ✓ Удалить — в любой момент              │
│ ✓ Без целевого веса, без оценок         │
│   «хорошо/плохо» — только факты        │
│                                        │
│ [Не сейчас]      [Согласна, попробуем] │
└────────────────────────────────────────┘
```

### 4.2 Critical design choices

- **Reminders DEFAULT OFF** — different from Mood (default ON) and Water (default ON). Body is more sensitive → opt-in only.
- **Explicit «без целевого веса, без оценок»** disclosure — pre-frames customer expectations against diet-app patterns
- **«можно реже или чаще как удобно»** — reinforces customer control
- **NO «set your goal» prompt anywhere** — diet-app territory

### 4.3 Outcomes — same pattern as Mood/Water

#### Tap «Согласна, попробуем»
- Create `WellnessModuleConsent(module_name='body', granted=True, granted_via=<path>, config=<chosen>)`
- Config JSON:
```json
{
  "reminders_enabled": false,
  "reminder_interval_days": 14,
  "preferred_unit_system": "metric"
}
```
- Emit `wellness.consent.module.granted` event
- Navigate to Самочувствие → Параметры section §6
- Show first-use toast: «Готово. Добавьте первое измерение — в Параметрах.»

#### Tap «Не сейчас»
- NO record created
- Path B activation: mark `customer.body_offer_declined_at = NOW`; never re-offer
- Path A: customer can re-open consent dialog later

---

## 5. Data models

### 5.1 `WellnessBodyMeasurement`

Single row per logging event. All measurements optional except weight (weight required when ANY measurement saved — anchor metric).

```python
class WellnessBodyMeasurement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='body_measurements')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    measured_at = models.DateTimeField()  # customer's logging time (can backdate up to 90 days for retroactive)

    weight_kg = models.DecimalField(
        max_digits=5, decimal_places=1,
        validators=[MinValueValidator(Decimal('30.0')), MaxValueValidator(Decimal('300.0'))],
    )
    # Required (anchor metric). Range covers all healthy adult humans.

    waist_cm = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(40), MaxValueValidator(200)])
    hips_cm = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(50), MaxValueValidator(200)])
    chest_cm = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(50), MaxValueValidator(200)])
    thigh_cm = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(30), MaxValueValidator(100)])

    SOURCE_CHOICES = [
        ('mini_app_manual', 'Mini App manual entry'),
        ('bot_dm_log', 'Bot DM (rare; customer types numbers)'),
    ]
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default='mini_app_manual')

    note = models.TextField(max_length=280, blank=True, default='')
    # Optional context note (e.g., «после отпуска», «после курса лимфодренажа»)

    edited_at = models.DateTimeField(null=True, blank=True)
    # If customer edits historic measurement; original measured_at preserved

    deleted_at = models.DateTimeField(null=True, blank=True)
    # Soft-delete marker — customer can delete past measurement

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['customer', '-measured_at']),  # timeline view
            Index(fields=['tenant', 'created_at']),  # analytics (privacy-respecting)
        ]
        constraints = [
            # weight_kg is always required (CheckConstraint enforces NOT NULL effectively via DecimalField required)
        ]
```

### 5.2 Reuses `WellnessModuleConsent` (from Mood handoff)

Config JSON for body module per §4.3:
```python
{
  "reminders_enabled": bool,
  "reminder_interval_days": int,  # default 14
  "preferred_unit_system": "metric",  # MVP only; Phase 4+ "imperial"
}
```

### 5.3 Anti-spam rules at model level

- Per-customer per-day: max 5 measurements per day (anti-OCD; if customer hits this, surface support flag)
- Per-customer total: no cap (long-term history valuable)
- Validators on each field reject impossible values
- `measured_at` can be in the past (backdate) up to 90 days; future = rejected

---

## 6. Mini App «Самочувствие» → Параметры section

### 6.1 Empty state (no measurements yet)

```
┌────────────────────────────────────────┐
│ 📏 Параметры                            │
├────────────────────────────────────────┤
│ Пока нет измерений.                    │
│                                        │
│ Добавьте первое — будете видеть как    │
│ меняются параметры со временем.        │
│                                        │
│ [+ Добавить первое измерение]          │
└────────────────────────────────────────┘
```

### 6.2 Populated state

```
┌────────────────────────────────────────┐
│ 📏 Параметры                            │
├────────────────────────────────────────┤
│ Последнее измерение: 12 мая            │
│                                        │
│ Вес     68.5 кг      ↓ 0.5 за период    │
│ Талия   72 см        ↓ 1 за период      │
│ Бёдра   94 см        — без изменений    │
│ Грудь   88 см        — без изменений    │
│                                        │
│ ── Тренд ──                             │
│                                        │
│ Период: [1 мес ▾]                       │
│ [простой line chart per metric]        │
│                                        │
│ [+ Новое измерение]                    │
│ [История →]                            │
└────────────────────────────────────────┘
```

**Critical**: «↓ 0.5 за период» format is **neutral observational**, NOT «−0.5 кг отлично!». Numbers + direction arrow only. No emoji, no celebration, no concern framing.

If change > 5% (large change): show same neutral format. NO «check with doctor» / «consult specialist» framing (paternalistic).

### 6.3 No «Главная» quick chip

Unlike Mood (daily) and Water (multi-daily), Body has no quick chip on Главная. Customer goes through Профиль → Самочувствие → Параметры deliberately. Anti-OCD: friction is feature here.

### 6.4 Trend chart

Phase 2: simple line chart per metric. Period selector: 1 month / 3 months / 6 months / 1 year.

**No goal line**, no target indicator, no «recommended range» bands. Just the actual data.

### 6.5 «История» view

```
┌────────────────────────────────────────┐
│ ← История измерений                     │
├────────────────────────────────────────┤
│ 12 мая · 14:30                          │
│ Вес 68.5 / Талия 72 / Бёдра 94          │
│ «после курса лимфодренажа»              │
│ [Изменить] [Удалить]                   │
│                                        │
│ 28 апреля · 10:15                       │
│ Вес 69.0 / Талия 73 / Бёдра 94          │
│ [Изменить] [Удалить]                   │
│                                        │
│ 14 апреля · 12:00                       │
│ Вес 69.5 / Талия 73 / Бёдра 95          │
│ «начало курса»                          │
│ [Изменить] [Удалить]                   │
│                                        │
│ ...                                    │
└────────────────────────────────────────┘
```

### 6.6 Add measurement screen

```
┌────────────────────────────────────────┐
│ ← Новое измерение                       │
├────────────────────────────────────────┤
│ Дата: [Сегодня ▾]                       │
│ (Можно поставить и прошлую дату)        │
│                                        │
│ ── Обязательно ──                       │
│                                        │
│ Вес: [_____ кг]                         │
│                                        │
│ ── По желанию ──                        │
│                                        │
│ Талия: [_____ см]                       │
│ Бёдра: [_____ см]                       │
│ Грудь: [_____ см]                       │
│ Бедро: [_____ см]                       │
│                                        │
│ Заметка (опц.):                        │
│ [_____________________________]        │
│                                        │
│ [Сохранить]                            │
└────────────────────────────────────────┘
```

Validation per §5.1 model validators. Inline error messages on invalid input.

### 6.7 Edit measurement

Tap «Изменить» in history → opens add screen pre-filled with current values. On save:
- Updates measurement in place
- Sets `edited_at = NOW`
- `measured_at` preserved (the date customer was actually measured)
- Audit log entry

### 6.8 Delete measurement

Tap «Удалить» → small confirmation:
```
Удалить измерение от 14 апреля?
[Удалить]  [Отмена]
```

On confirm:
- Soft-delete (`deleted_at = NOW`)
- 30-day grace; customer can recover via support (rare)
- Hard-delete after 30d via Celery beat

---

## 7. Per-state behavior matrix

When AI may prompt for measurement reminder (per customer's config.reminders_enabled).

| Customer state | Reminders fire? | Why |
|---|---|---|
| DISCOVERED | NO | No relationship; activation gate blocks |
| EXPLORING | NO (unless Path A activated) | Customer still discovering |
| PROBLEM_SEEKING | YES if activated AND reminders ON | Background continues |
| READY_TO_BOOK | YES if activated AND reminders ON | Same |
| POST_VISIT | YES if activated AND reminders ON | Good moment for reflection |
| ACTIVE_REGULAR | YES if activated AND reminders ON | Steady state |
| AT_RISK_DRIFTING | YES if activated AND reminders ON AND last_measurement < 60d | Respect drift after 60d (longer than Mood/Water) |
| AT_RISK_DRIFTING + no measurement 60+ days | NO; pause module reminders | Respect deliberate silence |
| DORMANT | NO | Per ownership policy |
| HUMAN_LOCKED active | NO | Admin owns |
| HUMAN_LOCKED inactive | YES if activated AND reminders ON | Resume normal |

---

## 8. Bot DM reminders (opt-in only)

### 8.1 Trigger conditions (ALL must be true)

- `WellnessModuleConsent.granted = True` AND `config.reminders_enabled = true`
- Per-state allowance per §7
- `reminder_interval_days` (default 14) has elapsed since last measurement
- Not in DND per notification-preferences
- Not within 1 hour of customer's last bot interaction

### 8.2 Reminder template

**Voice anchor**: Calm + Concise + Neutral (zero pressure)

```
{{interval_days_friendly}} прошло. Хотите отметить параметры?

[Открыть]   [Не сейчас]   [Не хочу напоминаний]
```

`interval_days_friendly` examples:
- 14 days → «Две недели»
- 7 days → «Неделя»
- 30 days → «Месяц»

### 8.3 Outcomes per tap

| Tap | Action |
|---|---|
| «Открыть» | Deep-link to Mini App add measurement screen §6.6 |
| «Не сейчас» | Silent dismiss; next reminder per `reminder_interval_days` |
| «Не хочу напоминаний» | Set `config.reminders_enabled = false`; confirmation toast «Готово. Открыть параметры можно из Самочувствия в любой момент.» |

### 8.4 Non-response throttle

| Consecutive non-responses to reminders | Action |
|---|---|
| 1 | Normal next reminder per interval |
| 2 | **Skip 1 interval** (give breathing room) |
| 3 | **Pause reminders 30 days** + DM: «Не вижу новых измерений — поставила напоминания на паузу. Включить обратно — в настройках Самочувствия.» |
| After 30d pause | If no re-enable, stays paused indefinitely |

Reset to «1» on any measurement save.

### 8.5 No bot DM «smart timing»

Unlike Water (smart hourly timing), Body reminders fire at simple interval cadence. Per [`wellness-input-modules §4.2`](../policies/wellness-input-modules.md#42-reminders-opt-in-only) anti-OCD principle — fancy timing is unnecessary + invites obsession.

---

## 9. API contracts

### 9.1 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/customer/wellness/body/measurement` | Customer | Add new measurement |
| GET | `/api/v1/customer/wellness/body/measurements` | Customer | List measurements (paginated, date range, includes soft-deleted? no) |
| GET | `/api/v1/customer/wellness/body/summary` | Customer | Aggregate summary for trend chart |
| PATCH | `/api/v1/customer/wellness/body/measurement/<id>` | Customer | Edit measurement |
| DELETE | `/api/v1/customer/wellness/body/measurement/<id>` | Customer | Soft-delete measurement |

### 9.2 POST `/api/v1/customer/wellness/body/measurement`

**Request**:
```json
{
  "measured_at": "2026-05-12T14:30:00Z",
  "weight_kg": 68.5,
  "waist_cm": 72,
  "hips_cm": 94,
  "chest_cm": null,
  "thigh_cm": null,
  "note": "после курса лимфодренажа",
  "source": "mini_app_manual"
}
```

**Validation**:
- Customer has `WellnessModuleConsent.granted = True` for `module_name='body'`
- `weight_kg` required (anchor metric)
- All optional measurements within range (per validators)
- `measured_at` ≤ now + 5 min tolerance; ≥ now - 90 days
- Anti-spam: max 5 measurements per customer per day → 429 with message «Вы уже добавили несколько измерений сегодня. Если что-то не так — отредактируйте существующее.»

**Response** (201):
```json
{
  "id": "uuid",
  "measured_at": "2026-05-12T14:30:00Z",
  "weight_kg": 68.5,
  "waist_cm": 72,
  "hips_cm": 94,
  "note": "после курса лимфодренажа",
  "change_from_previous": {
    "weight_kg": -0.5,
    "waist_cm": -1,
    "hips_cm": 0,
    "period_days": 14
  }
}
```

`change_from_previous` is informational only — NO «good/bad» framing in API response.

### 9.3 GET `/api/v1/customer/wellness/body/summary`

**Query**: `period_days` (default 30, max 365)

**Response** (200):
```json
{
  "period_days": 30,
  "measurements_count": 2,
  "latest": {
    "measured_at": "2026-05-12",
    "weight_kg": 68.5,
    "waist_cm": 72,
    "hips_cm": 94,
    "chest_cm": null,
    "thigh_cm": null
  },
  "trends": {
    "weight_kg": {
      "earliest_value": 69.0,
      "latest_value": 68.5,
      "delta": -0.5,
      "data_points": 2,
      "series": [{"date": "2026-04-28", "value": 69.0}, {"date": "2026-05-12", "value": 68.5}]
    },
    "waist_cm": {...},
    "hips_cm": {...}
  },
  "insight_text": "За месяц вес стабильно: 68.5 vs 69.0 (±0.5 кг).",
  "service_correlations": []
}
```

If `measurements_count < 2`: `insight_text = "Пока недостаточно данных. Добавьте ещё измерение через 2 недели."`. Trends omit `delta`.

### 9.4 PATCH `/api/v1/customer/wellness/body/measurement/<id>`

Edit existing. Sets `edited_at`. Returns updated row.

### 9.5 DELETE `/api/v1/customer/wellness/body/measurement/<id>`

Soft-delete. Returns 204. After 30 days, Celery beat hard-deletes.

---

## 10. AI insights (Phase 2 minimal)

### 10.1 Phase 2 rules-based «insight_text» generator

Inputs: latest measurements + previous measurement(s) in period.

Rules:
- **No data (< 2 points)**: «Пока недостаточно данных. Добавьте ещё измерение через 2 недели.»
- **Stable weight** (Δ weight < 1 kg over period AND no waist change > 2cm): «За {{period}} вес стабильно: {{latest}} vs {{previous}} (±{{delta}} кг).»
- **Small change** (Δ weight 1-3 kg OR waist ±2cm): «За {{period}}: вес {{delta_direction}} на {{delta}} кг, талия {{delta_direction}} на {{N}} см.»
- **Notable change** (Δ weight > 3 kg OR waist > 4cm): «За {{period}}: заметные изменения. Вес {{from}} → {{to}}, талия {{from}} → {{to}}.»
- **Mixed** (e.g., weight down + waist down): «За {{period}}: вес и талия снижаются.» (NO «отлично!» NO «keep going!»)

### 10.2 `delta_direction` words

- Positive delta: «больше» (no «прибавилось» / «прибавили» — softer)
- Negative delta: «меньше»
- Zero: «без изменений»

Never use «хорошо» / «плохо» / «лучше» / «хуже». Numbers + direction word only.

### 10.3 Forbidden phrases (auto-reject at insight generator)

- «отлично», «здорово», «молодец»
- «прогресс» (implies goal)
- «цель» (implies target customer didn't set)
- «нужно», «следует», «попробуйте»
- «вес теперь оптимальный»
- «BMI», «индекс массы тела»
- «ожирение», «недовес», «здоровый вес»
- «диета», «питание», «спорт»
- Any direction emoji (📈, 📉, ↗️, ↘️) in insight text

### 10.4 Service correlation (Phase 3+)

Out of scope Phase 2. Phase 3+ when wellness modules accumulate:
- If customer had ≥ 3 lymphatic / massage bookings in period AND waist change ≥ 1.5 cm: «За период было {{N}} массажей лимфодренажа.» (observational, never causal)
- NEVER claim «благодаря процедуре X» (causation framing forbidden)
- NEVER recommend continuing course («продолжайте курс»)

Phase 2 returns `service_correlations: []` (empty array).

---

## 11. Privacy enforcement

### 11.1 API-level guards
- All `/api/v1/customer/wellness/body/*` reject non-customer auth
- Return ONLY calling customer's data
- 403 if tenant_id mismatch
- ZERO tenant-side endpoints in Phase 2 (or ever)
- Aggregation pipeline (Phase 3+) strips per-customer identifiers AND zone identifiers before any salon-side analytics; weight/measurement values NEVER aggregated even anonymized

### 11.2 Master pre-arrival context

Per [`master-conversational-templates §5.5`](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface) — body data NEVER surfaces:
- Layer 3 Body State derivatives: ❌
- Layer 6 Nutrition derivatives: ❌
- Wellness AI Avatar photos: ❌ (separate AI Avatar grant flow)
- Master sees: appointment details + Layer 4 Service History reactions (this master's procedures only)

### 11.3 Logging
- API calls log event_id + path + outcome (no body values)
- Body values logged ONLY at TRACE level (off in prod)
- PII detector treats body values as Layer 3 sensitive

### 11.4 Retention

Per [Q-WI10](../decisions-log.md) consistency:
- Body measurements retained per Layer 3 sensitive Q-C3 4-layer policy
- On customer revoke consent: soft-delete 30d → hard-delete
- On customer deleted_request (OP6): cascade soft-delete → hard-delete per OP6 policy
- Data export via OP6: includes raw body measurements + history per customer-profile-management §6.3

### 11.5 Founder access

Founder has NO direct read access to body data in MVP. For legal hold scenarios (extremely rare): court order required + legal-hold flag + 4-eye approval (same standard as AI Avatar §14.3).

---

## 12. Wellness Profile integration

### 12.1 Aggregator job

Daily Celery beat (consistent with Mood §12.1, Water §12.1):
- Compute Layer 3 (Body State) derived fields:
  - `layer_3_body_state.weight_kg_latest`
  - `layer_3_body_state.weight_change_30d_kg` (NULL if < 2 measurements)
  - `layer_3_body_state.weight_change_90d_kg`
  - `layer_3_body_state.waist_cm_latest`
  - `layer_3_body_state.measurement_cadence_avg_days` (informational)
- Emit `wellness.profile.layer.updated` per aggregation

### 12.2 Cross-correlation (Phase 3+, out of scope this handoff)

When all wellness modules active:
- Body + Mood: AI may surface «период низкого настроения совпал с измерениями» observational note (customer-facing only)
- Body + Water: AI may surface correlation observation
- Body + AI Avatar: photo + measurement same date enhanced comparison view

All cross-correlation surfaces customer-side only. NEVER salon side.

---

## 13. Anti-patterns specific to Body module

| Anti-pattern | Why bad | Correct |
|---|---|---|
| BMI calculation displayed | Medical territory + diet-app pattern | NEVER display BMI |
| «Цель» / «target weight» field | Diet-app territory | NEVER weight goals |
| «Streak: 10 days in a row!» | Anti-OCD | No streaks anywhere |
| Encourage daily weighing | Anti-OCD | Suggested cadence bi-weekly; customer-controlled |
| «You're plateauing» language | Diet-app coaching pattern | NEVER motivational copy |
| Compare to other customers («средний клиент вашего возраста») | Privacy + shame | NEVER cross-customer |
| «Поправились» / «похудели» judgmental words | Body-shaming framing | «вес {{delta_direction}} на {{N}} кг» neutral |
| Direction emoji (📈, 📉) in copy | Implies value judgment | Numbers + neutral words only |
| «Recommend doctor» on large change | Paternalistic | Just observe; if customer asks, route to medical |
| Recommendations to «keep going» / «keep up the great work» | Coaching pattern | NEVER motivational |
| Calories burned / weight loss math | Diet-app calculation | NEVER |
| Surface body data in master pre-arrival | Privacy violation | NEVER salon side |
| Reminders > weekly | Anti-OCD | Min 14d interval (bi-weekly); customer can request weekly but discouraged |
| Auto-bookmark «new low!» | Triggers OCD weighing | NEVER «new low» events surfaced |
| Body measurement gamification (badges) | Anti-shame + OCD | NEVER badges/gamification |
| Anti-pregnancy mode (treat all customers same age 18+) | Customer privacy on conception status | Customer manages own data; system observation neutral |
| Force chest measurement for «complete profile» | Privacy violation + body autonomy | All except weight optional |

---

## 14. Acceptance criteria (engineering checklist)

- [ ] `WellnessBodyMeasurement` model with all validators
- [ ] Migration adds table; reuses `WellnessModuleConsent` with body config
- [ ] 5 API endpoints implemented + tested
- [ ] Customer auth required; tenant boundary; 403 on mismatch
- [ ] Activation Paths A + B implemented; Path C deferred Phase 2.5
- [ ] Consent dialog UI per §4.1 with reminders DEFAULT OFF
- [ ] Bi-weekly reminders Celery beat per algorithm §8 (opt-in only)
- [ ] Throttle §8.4 (non-response → pause logic)
- [ ] Per-state behavior matrix §7 enforced
- [ ] Mini App Самочувствие → Параметры section per §6
- [ ] Add measurement screen with validators inline errors
- [ ] Edit + delete history with audit
- [ ] Trend chart Phase 2 minimal (line per metric)
- [ ] Anti-pattern enforcement: insight generator rejects forbidden phrases §10.3 at API level
- [ ] NO BMI calculation anywhere in codebase
- [ ] NO «goal» / «target» fields anywhere
- [ ] NO «Главная» quick chip
- [ ] Events emitted per §15
- [ ] Privacy enforcement per §11
- [ ] Aggregator writes Wellness Profile Layer 3 §12
- [ ] Anti-spam max 5/day enforced
- [ ] Tests: unit (model + validators + insight generator) + API (auth + permissions + 429) + integration (consent → add → trend → edit → delete) + privacy (cross-tenant denial, no master leak)
- [ ] Accessibility audit on add measurement + trend chart (WCAG 2.2 AA)
- [ ] Documentation in `apps/wellness/body/README.md` referencing this handoff

---

## 15. Events emitted

Per [`event-taxonomy.md §3.6`](../policies/event-taxonomy.md#36-wellness-domain):

| Trigger | Event | Notes |
|---|---|---|
| Consent granted | `wellness.consent.module.granted` | `module_name='body'` |
| Consent revoked | `wellness.consent.module.revoked` | `module_name='body'` |
| Measurement saved | `wellness.input.recorded` | `module_name='body'`, `input_type='measurement'`, `confidence=1.0`, `source` |
| Measurement edited | NEW: `wellness.body.measurement.edited` | audit trail |
| Measurement deleted | NEW: `wellness.body.measurement.deleted` (soft-delete) | grace period start |
| Measurement hard-deleted | NEW: `wellness.body.measurement.hard_deleted` (after 30d) | retention pipeline |
| Reminder sent | NEW: `wellness.body.reminder.sent` | analytics for cadence tuning |
| Reminder ignored (no log within reminder interval) | NEW: `wellness.body.reminder.ignored` | feeds throttle |
| Aggregator writes profile layer | `wellness.profile.layer.updated` | `layer_name='layer_3_body_state'` |

Add 5 NEW events to event-taxonomy.md §3.6.

---

## 16. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-WB1** | Anti-spam max 5 measurements per customer per day enough? | YES MVP (if hit repeatedly, surface support flag to detect possible OCD pattern needing intervention) | Eng + Policy | 🟡 |
| **Q-WB2** | Default reminder interval 14 days — fixed or per-tenant? | Fixed 14d MVP per anti-OCD principle. Customer can change to 7 / 14 / 30 in their config. Reject < 7 days even if customer requests (anti-OCD hard line). | Policy + UX | 🟡 |
| **Q-WB3** | Should AI auto-detect «daily weighing pattern» and gently intervene? | YES Phase 3 — if customer logs daily for 7+ days, surface in insights: «Замечаю что отмечаете часто. Если хочется реже — пара недель между измерениями обычно достаточно.» (educational, never punitive). Phase 2: no intervention. | UX + Policy | 🟡 |
| **Q-WB4** | Backdating allowed up to 90 days — too long? | 90d MVP for retroactive logging (customer might recall last salon visit measurement). Tighter (30d) if abuse observed. | UX | 🟢 |
| **Q-WB5** | If customer enters weight 80 kg, then 30 min later 80.5 kg — system response? | Allow (water weight fluctuation; education in insights ONLY when pattern emerges per Q-WB3). | Eng | 🟢 |
| **Q-WB6** | Custom measurement types (e.g., «икры», «плечи») — Phase 2 or defer? | Defer Phase 3+ MVP. Fixed 5 fields (weight + 4 cm measurements) keep design simple. Customer demand metric tracked. | PM | 🟢 |
| **Q-WB7** | Insight «X cm waist over period» — what if measurement weeks gap (no data points)? | Show trend with available points; insight text clarifies «{{N}} измерений за период» if < 3 points | UX | 🟢 |
| **Q-WB8** | Multi-tenant customer with body module active at salon A — can they see history at salon B? | NO per Q-CO5; per-tenant separate consent + data. Customer manually re-enters at B if they want history there. (Privacy principle.) | Privacy | 🟢 |
| **Q-WB9** | Edit historic measurement — keep old value somewhere? | YES audit log; `edited_at` flag visible in history; previous value accessible via audit query (not customer-facing UI). | Eng + Privacy | 🟡 |
| **Q-WB10** | Reminder Bot DM «{{interval_days_friendly}} прошло» — variation copy needed? | 2-3 variants rotated per [conversational-ux-framework](../policies/conversational-ux-framework.md). Avoid robotic repetition. | UX | 🟢 |
| **Q-WB11** | Trend chart — Y-axis auto-scale or fixed scale (avoid emphasizing tiny changes)? | Auto-scale with min range floor (e.g., for weight: min 5 kg range even if data spans 1 kg) — avoids misleading «huge drop» visual on actually-small changes. | UX | 🟡 |
| **Q-WB12** | Show «average per period» on trend chart? | YES — simple horizontal line indicating period mean. Helps customer see «I am stable» visually. | UX | 🟢 |
| **Q-WB13** | Cross-correlation Phase 3+ — opt-in OR auto? | OPT-IN per service category. Customer enables «показывай связь с услугами по этой категории» if they want. Default OFF (privacy-paranoid + anti-correlation-bias). | UX + Privacy | 🟡 |
| **Q-WB14** | If customer's weight enters extreme range (BMI ≤ 16 or ≥ 40 from inferred height) — system response? | NO calculation, NO display. Per anti-pattern §13 no BMI. If customer mentions health concerns in DM, route to medical per conversational-ux §7.2. Don't infer from data. | Policy | 🔴 before first body data |
| **Q-WB15** | Mini App offline measurement logging — queue + sync per Q-MAS9? | YES — extend customer-first-touch §7.9 sync queue pattern; up to 5 queued measurements before warning | Eng | 🟡 |
| **Q-WB16** | Customer who logs once, never returns — auto-pause module after how long? | 90 days no new measurement AND reminders ignored → auto-pause + DM «Поставила параметры на паузу — включить обратно в настройках». | UX | 🟢 |
| **Q-WB17** | Tenant in PAUSED state — body module read-only OR full functionality? | Per [tenant-suspension-pause-ux §3.1 customer experience](../policies/tenant-suspension-pause-ux.md): full Mini App functionality preserved for own data including body. Module customer-owned, not tenant-owned. | Eng + Policy | 🟢 |

---

## 17. Cross-document linkage

- [`../policies/wellness-input-modules.md §4`](../policies/wellness-input-modules.md#4-module-3--body-tracking) — strategic spec ported
- [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md) — pattern source (WellnessModuleConsent, activation gates)
- [`./2026-05-19-wellness-water-handoff.md`](./2026-05-19-wellness-water-handoff.md) — sibling Phase 2 module
- [`./2026-05-19-wellness-ai-avatar-handoff.md`](./2026-05-19-wellness-ai-avatar-handoff.md) — privacy hierarchy reference + Phase 3 enhanced comparison synergy
- [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md) — opt-in reminders integration
- [`../policies/core-user-states.md`](../policies/core-user-states.md) — state matrix §7
- [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md) Layer 3 — aggregator writes
- [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md) — voice anchors throughout
- [`../policies/information-architecture.md`](../policies/information-architecture.md) — Самочувствие tab placement
- [`../policies/event-taxonomy.md §3.6`](../policies/event-taxonomy.md#36-wellness-domain) — 5 NEW events §15
- [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — HUMAN_LOCKED gating
- [`../policies/master-conversational-templates.md §5.5`](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface) — privacy boundary
- [`../policies/customer-profile-management-ux.md §4`](../policies/customer-profile-management-ux.md) — activation entry from Профиль → Самочувствие
- [`../policies/customer-first-touch-and-mini-app-states.md §7.9`](../policies/customer-first-touch-and-mini-app-states.md) — offline sync (Q-WB15)
- [`../policies/tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md) — Q-WB17 PAUSED-tenant behavior
- [`../decisions-log.md`](../decisions-log.md) — Q-WI3 (fixed 5 fields lean), Q-WI10 (revoke retention)

---

## 18. What this unblocks

- **`apps/wellness/body/` Phase 2 implementation** — model + API + Mini App engineering-ready
- **Wellness Profile Layer 3 populated** — first quantitative body data (vs inferred)
- **AI Avatar Phase 3 enhanced comparison** — body measurement + photo same date
- **Wellness Goals Phase 3 progress tracking** — measurements feed goals (when goals module ships)
- **Cross-correlation insights Phase 3+** — bookings + body changes (observational only)
- **Pattern for high-sensitivity module** — anti-OCD + anti-shame design template for future sensitive modules
- **Demonstrates wellness OS commitment** — body tracking without diet-app patterns differentiates from competitors

## 19. What this does NOT unblock

- ❌ BMI / Body Composition (forbidden per §13 — medical territory)
- ❌ Weight loss / fitness goals (Diet-app forbidden)
- ❌ Scale integration (Phase 3+)
- ❌ Imperial units (Phase 4+)
- ❌ Cross-tenant aggregate body analytics (privacy)
- ❌ Tenant-side body data visibility (privacy)
- ❌ AI medical advice on body data (out of scope ethically)
- ❌ Skip pre-deploy privacy audit per §11
- ❌ Q-WB14 strategic decision (founder + legal sign-off before first body data — extreme range handling)

---

## 20. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Wellness backend lead (apps/wellness/body/) | ☐ | |
| Mini App frontend (Параметры section + add/edit/delete + trend chart) | ☐ | |
| AI prompt engineering (insight generator with forbidden-phrase enforcement per §10.3) | ☐ | |
| Privacy / Legal (Q-WB9 audit retention + Q-WB14 extreme range policy + master-side boundary) | ☐ | |
| Accessibility (WCAG 2.2 AA on chart + form + numeric inputs) | ☐ | |
| Policy review (Q-WB3 daily-pattern intervention + Q-WB14 extreme range — these are mental-health-adjacent decisions) | ☐ | |

## Last verified
2026-05-19 (initial draft, engineering-ready for Phase 2 Wellness Body Tracker — sibling to Mood + Water; anti-OCD + anti-shame design)
