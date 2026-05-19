# Wellness Food Scanner Module — engineering handoff

**Date:** 2026-05-19 r1
**Status:** Engineering-ready — Phase 3 wellness module №6 (most complex)
**Lifts patterns from:** `mysite/maxbot/` battle-tested implementation (Формула тела bot, Phase 3 nutrition tracker shipped 2026-05) — see §0.2 reference map
**Reads:** [`../policies/wellness-input-modules.md`](../policies/wellness-input-modules.md) §2 (Module 1 Food Scanner), [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md), [`./2026-05-19-wellness-water-handoff.md`](./2026-05-19-wellness-water-handoff.md), [`./2026-05-19-wellness-body-handoff.md`](./2026-05-19-wellness-body-handoff.md), [`./2026-05-19-wellness-sleep-handoff.md`](./2026-05-19-wellness-sleep-handoff.md), [`./2026-05-19-wellness-symptom-handoff.md`](./2026-05-19-wellness-symptom-handoff.md), [`./2026-05-19-wellness-ai-avatar-handoff.md`](./2026-05-19-wellness-ai-avatar-handoff.md), [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md), [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md), [`../policies/customer-profile-management-ux.md`](../policies/customer-profile-management-ux.md), [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md)

> Ports [wellness-input-modules §2 Food Scanner](../policies/wellness-input-modules.md#2-module-1--food-scanner) to engineering-ready spec. **Most complex wellness module + medically sensitive.** Phase 3+ launch. Lifts proven UX patterns from `mysite/maxbot` Phase 3 nutrition tracker (already shipped + customers using). Anti-pattern enforcement is strictest after Symptom Diary.

---

## 0. Why this exists

### 0.1 Strategic context

Per [`wellness-input-modules §2.2`](../policies/wellness-input-modules.md#22-why-this-matters):
- Closes holistic wellness loop («плохое питание → отёчность → лимфодренаж рекомендация»)
- Unique differentiation (most beauty / wellness platforms don't track food)
- Habit anchor (food-tracking customers 3× more retained, industry benchmark)
- **NOT a diet app** — observational only, NEVER moralize / shame / restrict

### 0.2 Reference: `mysite/maxbot` battle-tested implementation

A sister project `mysite` («Формула тела» / formula-tela-maxbot.service) shipped a Phase 3 nutrition tracker in 2026-05. This handoff **lifts proven patterns** from that implementation. Engineering should study the reference before implementing.

**Reference files** (in `mysite/maxbot/`):
- `ai_concierge.py` — orchestrator routing food photos to vision tool
- `ai_tools.py` — `recognize_food`, `add_food_entry`, `correct_food_entry`, `add_beverage`, parsers
- `ai_parsers.py` — `parse_age`, `parse_height`, `parse_weight`, `parse_allergies`, `parse_beverage`
- `nutrition_calc.py` — pure-math (BMR, daily norms, BMR floor checks)
- `handlers/nutrition_anketa.py` — health screening anketa FSM
- `handlers/nutrition_entry.py` — daily entry handler
- `services/nutrition_client.py` — backend Ayla client

**Reference design** (in `mysite/.claude/worktrees/.../docs/plans/`):
- `maxbot-phase3-nutrition-design.md` (1125 lines) — full v2 spec
- `maxbot-phase3-1-2A-photo-refactor.md` — vision integration
- `maxbot-phase3-ayla-spec.md` — backend spec
- `maxbot-phase3-reconciliation.md` — UX v1 reconciliation notes

### 0.3 Key differences vs `mysite` reference

| Aspect | `mysite` (Формула тела) | ai-bot-platform (this handoff) |
|---|---|---|
| Audience | Women 35-45, Пенза, nutrition-focused | Salon customers tracking food for wellness/service correlation |
| Bot's primary purpose | Nutrition + water + AI insights | Salon assistant; food is 1 of 7 wellness modules |
| Health screening | Required gate for advice | Required gate; same pattern |
| AI advice on food | Coach-mode (БЖУ-советы, weekly insights) | **MORE restricted** — observational only; advice off by default |
| Service cross-promo | salon-related («лимфодренаж» nudge) | salon services tied to current tenant; tenant-aware |
| Backend | Ayla shared service | `apps/wellness/food/` in ai-bot-platform; may share Ayla vision later |
| Photo cost | gpt-4o-full ~$0.01-0.02/photo, ~$0.0001 parsers | Same pattern; Phase 3 budget review per Q-WF6 |

### 0.4 The gap

Despite [wellness-input-modules §2](../policies/wellness-input-modules.md#2-module-1--food-scanner) describing strategy + UX sketch, ai-bot-platform doesn't have:
- Engineering-ready spec for `apps/wellness/food/`
- Vision API contract
- Confidence-based UX routing (high/medium/low branches)
- Eating disorder silent mode implementation
- Pregnancy / breastfeeding override
- BMR floor safety net
- Health screening gate
- Cross-correlation with Mood / Body / Sleep / Symptoms / Services
- Per-tenant context awareness (different salons = different service catalogs)

This handoff fills those gaps + lifts mysite's battle-tested patterns.

### 0.5 The promise

Single source for `apps/wellness/food/` Phase 3 implementation. Engineering can study `mysite/maxbot` reference + ship the ai-bot-platform variant without re-discovering anti-patterns.

---

## 1. Scope

### IN
- New sub-module `apps/wellness/food/`
- `WellnessFoodEvent` model
- `WellnessHealthProfile` model (health screening data — gate for advice)
- Vision recognition service via Ayla API (or direct OpenAI if Ayla unavailable)
- Activation Paths A + B (Path C deferred Phase 3.5+)
- Health screening anketa flow (TIER-A 5 steps MVP + TIER-B health-screening lazy)
- Consent dialog with EXPLICIT non-diet-app disclaimer
- Mini App Самочувствие → Еда section
- Bot DM photo capture + correction flow (confidence-based routing)
- Free-text food log («съела пасту 200г») — Phase 3.5+ via NLU
- Daily report (cached, regenerated 1×/day)
- 8 API endpoints
- Per-state behavior matrix
- Phase 3 simple-rules insights; Phase 4+ ML pattern detection
- **Eating disorder silent mode** §10 (lifted from mysite)
- **Pregnancy / breastfeeding override** §11
- **BMR floor safety net** §12
- Privacy enforcement (customer-only, high-sensitivity)
- Wellness Profile Layer 6 Nutrition integration
- Cross-module synergy stubs
- 7 NEW events for event-taxonomy

### OUT
- Calorie-deficit goals («худеть на N кг») — diet-app territory; FORBIDDEN
- Weekly meal plans / generated diets — coaching scope
- Macros target prescriptions («ешьте 150г белка в день») — coaching scope
- Drug / supplement recommendations
- Medical diet recommendations («исключите глютен») — without medical confirmation
- Children's food tracking (Phase 4+ family mode)
- Restaurant menu lookup / barcode scanning (Phase 4+)
- Calorie burn / exercise pairing — diet/fitness scope
- Customer-pays gating (free forever per Q-WI12)
- HealthKit / Apple Health / Google Fit nutrition sync (Phase 4+)
- Recipe suggestions / cooking guidance — out of scope

---

## 2. Strategic constraints — non-negotiable

Strictest in platform alongside Symptom Diary + AI Avatar. Engineering reviewer rejects ANY violation.

### 2.1 NOT a diet app
- NEVER set calorie deficit / weight loss targets
- NEVER «recommend» food choices («ешьте больше белка»)
- NEVER «good vs bad food» framing («бургер — плохо»)
- NEVER public sharing / leaderboards / streaks
- Customer's food is their business; we observe + log

### 2.2 Eating disorder silent mode (§10)
- If health screening reveals eating disorder OR customer triggers detection patterns → **silent mode**:
  - NO calorie display in any view
  - NO macro counters
  - NO «дневной отчёт» numbers
  - Supportive non-numeric tone
  - When customer mentions weight/srylv — route to specialist
- Lifted from mysite reference §12.2

### 2.3 Pregnancy / breastfeeding override (§11)
- Pregnancy detected → force calorie target = «maintain» (no deficit)
- Breastfeeding similar
- Caffeine warning if customer logs > 200mg/day
- No restrictive recommendations

### 2.4 BMR floor (§12)
- Auto safety net: customer's daily calorie target NEVER goes below BMR + 100 kcal
- If customer's «goal» would push below → auto-ladder pace → goal explanation
- Hard floor is loud — explicit message «ниже этого уровня вредно для организма»

### 2.5 Honest doctor referral (NOT salon cross-promo)
- Health warnings NEVER convert to «запишитесь в наш массаж»
- For medical concerns — recommend «к врачу» or «к диетологу» generic
- Only cross-promote services when **wellness signal**, not **medical signal**

### 2.6 Health screening = gate for ADVICE, not BASIC LOGGING
- Photo recognition + calorie display work WITHOUT screening
- Personalized advice (БЖУ rules, weekly insights, daily target) requires screening consent
- Customer can log food forever without giving health data

### 2.7 Privacy hierarchy
Same as Body / Symptom (high). Customer-only. Soft-delete 30d on revoke.

---

## 3. Activation flow

### 3.1 Eligibility (gates)

Customer cannot activate Food Scanner if:
- `consent.ai_messaging = false` (exception: Path A self-discovery)
- `core_user_state ∈ {DORMANT, HUMAN_LOCKED active conversation}`
- Tenant in PAUSED / SUSPENDED state
- Customer's MAX account suspended
- Customer < 18 years old (food tracking + minors = ethical red line)

### 3.2 Activation triggers (Phase 3 launch)

**Path A — Self-discovery in Mini App** (always available):
Customer navigates Профиль → Самочувствие → Еда card → toggle ON → consent dialog §4.

**Path B — Cross-module synergy** (after mood/body modules show interest):
After customer has Body or Mood active ≥ 14 days AND has shown wellness engagement → AI sends ONE offer:
```
Заметила что вы отмечаете {{module_name}}. Хотите ещё помочь с питанием?

Я могу распознавать еду по фото и помнить что вы ели. Без советов «правильно/неправильно» — просто запись.

[Попробовать]   [Не сейчас]
```

Same Path B suppression: «не сейчас» → never re-offer.

**Path C — Symptom trigger** (Phase 3.5+):
Customer's symptom + food correlation detected (e.g., regular bloating + meal patterns) → AI suggests. NLU work required.

### 3.3 Activation events
- `wellness.consent.module.granted` with `module_name='food'`, `granted_via=<path>`

---

## 4. Consent dialog

### 4.1 Single-screen with EXPLICIT non-diet-app disclaimer

```
┌────────────────────────────────────────┐
│ Отслеживать питание?                   │
├────────────────────────────────────────┤
│ Я буду:                                │
│   • Распознавать еду по фото           │
│   • Помнить что и когда вы ели         │
│   • Связывать с самочувствием          │
│   • Связывать с услугами студии        │
│                                        │
│ ── ВАЖНО ──                             │
│                                        │
│ Я НЕ диетолог. Я НЕ советую «что есть»  │
│ или «не есть».                         │
│                                        │
│ Я НЕ ставлю цели по снижению веса.     │
│                                        │
│ Я просто помогаю помнить — а вы решаете │
│ что с этим делать.                     │
│                                        │
│ ── Что важно ──                         │
│                                        │
│ ✓ Данные видите только вы              │
│ ✓ Студия НЕ видит ничего                │
│ ✓ Без «хорошо/плохо» — только факты    │
│ ✓ Без счётчиков целей                  │
│ ✓ Удалить — в любой момент              │
│                                        │
│ [Не сейчас]      [Согласна, попробуем] │
└────────────────────────────────────────┘
```

### 4.2 Critical design choices

- **«Я НЕ диетолог»** disclaimer pre-frames customer expectations
- **«Я НЕ ставлю цели по снижению веса»** — explicit anti-diet-app framing
- Reminders default OFF (customer-driven cadence; no pushing for «log your meal!»)

### 4.3 Outcomes

#### Tap «Согласна, попробуем»
- Create `WellnessModuleConsent(module_name='food', granted=True, ...)`
- Config initially empty (advice features disabled until health screening §5)
- Navigate to Самочувствие → Еда section §7

#### Tap «Не сейчас»
- NO record created
- Path B suppression as usual

---

## 5. Health screening (anketa) — gate for advice

### 5.1 Two tiers (per mysite §4)

**TIER-A** (5 steps, MVP): basic fields needed for any calorie display
- Gender (or skip)
- Age
- Height
- Weight (or range)
- Goal (lose / maintain / gain / tone — informational; not target)

**TIER-B** (health-screening, lazy on-demand before first advice):
- Pregnancy / breastfeeding status
- Major health flags (diabetes / hypertension / thyroid / GI / menopause / eating disorder)
- Allergies / intolerances
- Medications

### 5.2 TIER-A flow

Triggered when customer first taps «advice» or «daily target». Mini App wizard 5 steps, can skip each:

```
┌────────────────────────────────────────┐
│ Шаг 1/5: Пол                            │
│ ⦿ Ж  ◯ М  ◯ Не хочу указывать          │
└────────────────────────────────────────┘

┌────────────────────────────────────────┐
│ Шаг 2/5: Возраст                        │
│ [_______ лет]    [Пропустить]          │
└────────────────────────────────────────┘

... etc
```

Per mysite reference: free-text parser handles «35 лет», «35», «тридцать пять» via `parse_age` LLM tool.

### 5.3 TIER-B flow (lazy)

ONLY fires when customer first requests advice OR insight that would use this data. Modal:

```
┌────────────────────────────────────────┐
│ Нужно несколько фактов                  │
├────────────────────────────────────────┤
│ Чтобы советы были безопасными, скажите │
│ если что-то из этого есть:             │
│                                        │
│ ☐ Беременность                          │
│ ☐ Кормление грудью                      │
│ ☐ Диабет                                │
│ ☐ Гипертония                            │
│ ☐ Расстройство пищевого поведения      │
│ ☐ Гипотиреоз                            │
│ ☐ Проблемы с пищеварением              │
│ ☐ Климакс                               │
│ ☐ Ничего из этого                      │
│                                        │
│ [Не хочу отвечать]  [Готово]            │
└────────────────────────────────────────┘
```

**«Не хочу отвечать»** → customer continues using food logging WITHOUT personalized advice features. Crucial: NOT a barrier; consent-respecting.

### 5.4 Health flags stored in `WellnessHealthProfile`

See §6.2 model.

### 5.5 Version-aware ack

When platform updates health screening copy (legal/regulatory changes), bump version → re-ack active customers per `mysite §12.1`.

---

## 6. Data models

### 6.1 `WellnessFoodEvent`

```python
class WellnessFoodEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='food_events')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    consumed_at = models.DateTimeField()  # when customer ate; can backdate up to 7 days

    MEAL_TYPE_CHOICES = [
        ('breakfast', 'Завтрак'),
        ('lunch', 'Обед'),
        ('dinner', 'Ужин'),
        ('snack', 'Перекус'),
        ('other', 'Другое'),
    ]
    meal_type = models.CharField(max_length=16, choices=MEAL_TYPE_CHOICES, default='other')

    # Recognition data (when source='photo')
    dish_name = models.CharField(max_length=200)
    recognition_confidence = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    # 0.00–1.00; null if source=manual

    portion_estimate = models.CharField(max_length=32, default='medium')
    # 'small' / 'medium' / 'large' Phase 3 MVP; exact grams Phase 4+ depth-camera

    kcal = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5000)])
    protein_g = models.IntegerField(validators=[MinValueValidator(0), MaxValueValidator(500)])
    fat_g = models.IntegerField(validators=[MinValueValidator(0), MaxValueValidator(500)])
    carbs_g = models.IntegerField(validators=[MinValueValidator(0), MaxValueValidator(1000)])

    photo_ref = models.CharField(max_length=200, blank=True, default='')
    # Reference to encrypted photo blob (if customer opted to retain photos per §13.3)
    # Empty if photo not retained.

    note = models.TextField(max_length=280, blank=True, default='')

    SOURCE_CHOICES = [
        ('photo_recognized', 'Bot DM photo → vision recognition'),
        ('photo_corrected', 'Photo + customer correction'),
        ('manual_text', 'Free-text manual entry («съела пасту 200г»)'),
        ('mini_app_manual', 'Mini App manual food entry'),
    ]
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES)

    recorded_at = models.DateTimeField()
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['customer', '-consumed_at']),  # timeline
            Index(fields=['customer', 'meal_type', '-consumed_at']),  # daily report aggregation
            Index(fields=['tenant', 'created_at']),  # analytics aggregation
        ]
```

### 6.2 `WellnessHealthProfile`

```python
class WellnessHealthProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.OneToOneField('customers.Customer', on_delete=CASCADE, related_name='health_profile')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    # TIER-A
    GENDER_CHOICES = [('f', 'Ж'), ('m', 'М'), ('skip', 'Не указан')]
    gender = models.CharField(max_length=8, choices=GENDER_CHOICES, blank=True, default='')
    age = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(18), MaxValueValidator(120)])
    height_cm = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(100), MaxValueValidator(250)])
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, validators=[MinValueValidator(Decimal('30.0')), MaxValueValidator(Decimal('300.0'))])
    weight_range = models.CharField(max_length=16, blank=True, default='')  # e.g., "65-75" if customer prefers range

    GOAL_CHOICES = [
        ('lose', 'Похудеть'),
        ('maintain', 'Поддерживать'),
        ('gain', 'Набрать'),
        ('tone', 'Подтянуть'),
        ('not_set', 'Не задана'),
    ]
    goal = models.CharField(max_length=16, choices=GOAL_CHOICES, default='not_set')

    PACE_CHOICES = [('gentle', 'Спокойный'), ('moderate', 'Умеренный'), ('not_set', 'Не задан')]
    pace = models.CharField(max_length=16, choices=PACE_CHOICES, default='not_set')

    # TIER-B (lazy)
    health_flags = models.JSONField(default=dict, blank=True)
    # Structure (mysite-derived):
    # {
    #   "pregnant": true/false/null  (null = not asked yet),
    #   "breastfeeding": ...,
    #   "diabetes_t1": ...,
    #   "diabetes_t2": ...,
    #   "prediabetes": ...,
    #   "hypertension": ...,
    #   "gi_problems": ...,
    #   "thyroid": ...,
    #   "menopause": ...,
    #   "eating_disorder": ...,
    #   "meds": ...,
    #   "_skipped": ["pregnant", ...]  (fields customer chose «не отвечать»)
    # }

    allergies = models.JSONField(default=list, blank=True)
    # [{"item": "лактоза", "type": "intolerance"}, {"item": "глютен", "type": "allergy"}]

    # Overrides
    goal_overridden_by = models.CharField(max_length=32, blank=True, default='')
    # 'pregnancy' / 'breastfeeding' / 'low_bmi' / 'bmr_floor' / 'eating_disorder' / ''

    bmi_warning_overridden_at = models.DateTimeField(null=True, blank=True)
    # When customer explicitly overrode low BMI + lose goal warning

    # Consent / version tracking
    nutrition_disclaimer_acked_at = models.DateTimeField(null=True, blank=True)
    nutrition_disclaimer_version = models.CharField(max_length=16, blank=True, default='')
    nutrition_disclaimer_screen = models.CharField(max_length=32, blank=True, default='')

    timezone = models.CharField(max_length=64, default='Europe/Moscow')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

### 6.3 Anti-spam + validation

- Per-customer per-day: max 30 food events (legitimate heavy use; flag at 100/day for support attention)
- `kcal` ∈ [1, 5000] per event (Phase 3 MVP); per Q-WF7 review at heavy users
- `consumed_at` ≥ today - 7 days; ≤ now + 5 min tolerance
- Soft-delete 30d on revoke (per Q-WI10)

---

## 7. Mini App «Самочувствие» → Еда section

### 7.1 Empty state (no food events yet)

```
┌────────────────────────────────────────┐
│ 🍽 Еда                                  │
├────────────────────────────────────────┤
│ Пока нет записей.                      │
│                                        │
│ Сфотографируйте блюдо или опишите      │
│ текстом — я запомню.                   │
│                                        │
│ ⚠️ Я не диетолог. Это просто запись.   │
│                                        │
│ [📷 Сделать фото]                       │
│ [✏️ Написать что съели]                │
└────────────────────────────────────────┘
```

### 7.2 Populated state — daily view

```
┌────────────────────────────────────────┐
│ 🍽 Еда сегодня                          │
├────────────────────────────────────────┤
│ 3 приёма пищи                           │
│                                        │
│ Завтрак · 08:30                         │
│ Овсянка с ягодами                       │
│ ≈350 ккал · Б 12 · Ж 8 · У 55           │
│ [Изменить] [Удалить]                   │
│                                        │
│ Обед · 13:00                            │
│ Паста с курицей                         │
│ ≈520 ккал · Б 35 · Ж 18 · У 55          │
│ [Изменить] [Удалить]                   │
│                                        │
│ Перекус · 16:30                         │
│ Яблоко                                  │
│ ≈80 ккал                                │
│ [Изменить] [Удалить]                   │
│                                        │
│ ── Итого ──                             │
│ ≈950 ккал                              │
│ {{if tier_a_done}}                      │
│ Дневная норма: 1450 ккал                │
│ Осталось: 500 ккал                      │
│ {{endif}}                              │
│                                        │
│ {{if eating_disorder_silent}}           │
│ (no calorie totals shown)              │
│ {{endif}}                              │
│                                        │
│ [+ Добавить ещё]                       │
│ [📊 Дневной отчёт]                      │
│ [📅 История →]                          │
└────────────────────────────────────────┘
```

**Critical**: `eating_disorder_silent` mode hides all calorie/macro displays. See §10.

### 7.3 Add food screens

#### Photo path
1. Customer taps «📷 Сделать фото»
2. Camera opens (Mini App)
3. Capture → upload → vision API §8
4. Confidence-based UX §8.4
5. Save → daily view updates

#### Manual text path
1. Customer taps «✏️ Написать»
2. Form opens:
```
┌────────────────────────────────────────┐
│ ← Запись еды                            │
├────────────────────────────────────────┤
│ Что ели?                                │
│ [_____________________________]        │
│ (например: «паста с курицей и помидорами 250г»)│
│                                        │
│ Когда?                                  │
│ [Сейчас ▾] (или backdate 7 дней)        │
│                                        │
│ Приём пищи:                             │
│ [Обед ▾]                                │
│                                        │
│ [Распознать через AI]   [Указать вручную]│
└────────────────────────────────────────┘
```
- «Распознать через AI» → LLM parses text → returns same FoodRecognition struct as vision
- «Указать вручную» → expanded form with kcal/macros fields

### 7.4 Daily report screen

Triggered by tap «📊 Дневной отчёт».

```
┌────────────────────────────────────────┐
│ ← Дневной отчёт за {{date}}              │
├────────────────────────────────────────┤
│ 3 приёма пищи · 950 ккал                │
│                                        │
│ {{if tier_a_done}}                      │
│ Норма: 1450 ккал (умеренный темп)       │
│ Покрыто 65%                             │
│ {{endif}}                              │
│                                        │
│ ── По БЖУ ──                            │
│ {{if eating_disorder_silent}}           │
│ (skipped — silent mode)                │
│ {{else}}                                │
│ Белки: 58 / 110 г (53%)                 │
│ Жиры: 32 / 50 г (64%)                   │
│ Углеводы: 78 / 145 г (54%)              │
│ {{endif}}                              │
│                                        │
│ ── AI заметка ──                        │
│ {{ai_observation_text}}                │
│ (Phase 3: simple rules; Phase 4+ ML)   │
│                                        │
│ ── Связь с самочувствием ──             │
│ {{if cross_module_data}}                │
│ Сон прошлой ночью: 7.5ч ★4              │
│ Самочувствие сегодня: ★4                │
│ Вода: 6/8 стаканов                      │
│ {{endif}}                              │
│                                        │
│ [Поделиться отчётом]                   │
└────────────────────────────────────────┘
```

Daily report is cached in `BotUser.context["daily_report"]` (mysite pattern §2.3) — regenerated 1×/day to keep LLM costs low.

---

## 8. Vision recognition flow

### 8.1 Architecture (lifted from mysite Variant B target)

```
Customer sends photo in bot DM (or Mini App captures)
  ↓
Ayla / OpenAI gpt-4o vision API
  ↓
Structured output:
  {
    "is_food": bool,
    "dish_name": str,
    "confidence": float (0.0-1.0),
    "kcal": int,
    "protein_g": int,
    "fat_g": int,
    "carbs_g": int,
    "portion_estimate": "small" | "medium" | "large",
    "raw_response": str  // for debugging
  }
  ↓
Confidence routing §8.4
  ↓
Save to WellnessFoodEvent
  ↓
Daily report invalidation + regeneration
```

### 8.2 Cost budget (per mysite §2.3)

- gpt-4o full: ~$0.01-0.02 per photo, p50 4s / p95 10s latency
- Acceptable trade-off: accuracy > cost (Phase 3 MVP)
- Optimization deferred to Q-WF6 at >1000 active users

### 8.3 Edit-message loading pattern (lifted from mysite §5.1)

Customer sends photo → bot immediately:
```
🤖 👀 Распознаю...
```
+ `bot.send_action(typing)` indicator. Edit same message on completion. If > 10s, intermediate edit «*ещё пара секунд...*».

### 8.4 Confidence-based routing (lifted from mysite §5.2)

GPT-4o returns `confidence` 0.0-1.0. UX branches:

| Confidence | UX branch | Sample copy |
|---|---|---|
| ≥ 0.7 (high) | Best-guess + correct/confirm | «🍝 Паста с курицей и томатами ≈520 ккал · Б 35 · Ж 18 · У 55. Всё верно?» + `[👍 Да]` `[✏️ Поправить]` |
| 0.3-0.7 (medium) | Multi-choice (3-4 candidates) | «Похоже на одно из этого:» + chips: `[🥔 Картофельная]` `[🧀 Творожная]` `[🥩 Мясная]` `[🥬 Овощная]` + `[✏️ Напишу сама]` |
| < 0.3 (low) | Abort recognition; ask text | «*Не разобрала что на фото 🙈. Что было?*» + `[📸 Переснять]` `[✏️ Напишу]` |
| `is_food = false` | Polite redirect | «*Это не похоже на еду. Если хочешь записать приём — пришли фото блюда.*» |

Universal fallback `[✏️ Напишу сама]` ALWAYS available.

### 8.5 Correction flow (lifted from mysite §5.4)

Customer taps `[✏️ Поправить]`:
```
🤖 Что не так?

[📦 Размер порции]  [🔄 Это другое блюдо]
[➕ Добавить ингредиент]  [⏭ Удалить]
```

- **Размер порции** → `[Меньше]` `[Норм]` `[Больше]` × {0.7, 1.0, 1.3} multiplier on kcal/macros
- **Это другое блюдо** → free-text «*это был ризотто*» → LLM recalculates from text
- **Добавить ингредиент** → free-text → LLM adjusts
- **Удалить** → soft-delete the event

After correction → confirmation «*Поправила: ≈365 ккал. Ок?*»

### 8.6 Format spec (lifted from mysite §5.3)

- `≈` prefix for kcal (not «~», not «±», not «около»)
- Disclaimer «*Это оценка ±15-20%. Хочешь точнее? Напиши что и сколько съела.*» — **shown ONLY on customer's first photo** post-onboarding; not repeated
- Footer buttons after success: `[💧 Добавить воду]` `[📊 Посмотреть день]` (cross-module sync hooks)
- Footer NOT shown on low-confidence or non-food responses

### 8.7 Free-text food log (text → LLM)

Same flow but text → LLM parser → FoodRecognition struct.

LLM tool: `parse_food_freetext(text, customer_context)` returns same struct. customer_context includes recent meals, time of day (helps with portion inference).

---

## 9. Daily report

### 9.1 Generation
Cached in `BotUser.context["daily_report"]` (single LLM call per day per customer). Regenerated:
- On first request after midnight (customer's TZ)
- On manual «обновить» tap
- After significant event (cross-module update)

### 9.2 Content blocks (§7.4)
- Totals (kcal, BJU) — suppressed in silent mode §10
- AI observation (Phase 3 simple rules; Phase 4+ ML)
- Cross-module data (sleep/mood/water/symptoms if active)

### 9.3 Observation rules (Phase 3 MVP)
- **< 3 events today**: «Записей мало пока — добавьте ещё.»
- **Calorie within 90% of target**: «На сегодня съедено в норме.»
- **Calorie below 60% of target after 14:00**: «Получили меньше нормы — это нормально, но не забывайте про ужин.»
- **Pattern across week** (Phase 4+): «По будням обычно меньше калорий чем в выходные» observational

### 9.4 Forbidden content in observation

Per §11 generator FORBIDDEN-PHRASE enforcement.

---

## 10. Eating Disorder Silent Mode

### 10.1 Activation

Customer flagged for eating disorder if:
- TIER-B health screening checked `eating_disorder = true`
- OR pattern detection (Phase 4+): rapid weight changes + restrictive logging
- OR customer explicitly requests silent mode

### 10.2 Silent mode behavior

When `WellnessHealthProfile.health_flags.eating_disorder = true`:
- ALL calorie counters HIDDEN in Mini App
- ALL macro counters HIDDEN
- Daily report shows «3 приёма пищи» count only, NO numbers
- Bot DM responses after food recognition: dish name only, no «≈520 ккал»
- AI observations supportive: «Запись сохранена.» (no judgmental analysis)
- No daily target, no «осталось»

### 10.3 When customer mentions weight / диета / срыв

Bot responds:
```
Это сложная тема, и я не врач. Если что-то беспокоит — психотерапевт или диетолог поможет лучше.

[Поняла] (no further conversation on this thread)
```

NEVER engage in coaching on these topics.

### 10.4 Override

Customer can disable silent mode in settings (with explicit confirmation modal). Re-enable similarly. Audit-logged.

---

## 11. Pregnancy / Breastfeeding Override

### 11.1 Trigger

`WellnessHealthProfile.health_flags.pregnant = true` OR `breastfeeding = true`.

### 11.2 Effects

- `goal_overridden_by = 'pregnancy'` or `'breastfeeding'`
- Calorie target = «maintain» (no deficit)
- `pace = 'gentle'` forced
- Allergen warnings (if applicable from `allergies`) elevated
- Caffeine warning: if customer logs > 200mg/day → soft warning «*Кофеин выше 200мг/сут — врачи рекомендуют осторожность при беременности*» (route to medical)

### 11.3 NOT a medical advice

Even with overrides, Food Scanner is NOT a prenatal nutrition coach. Customer should consult doctor / гинеколог. Disclaimer in dialog.

### 11.4 Phase 4+ menopause similar
Same pattern; menopause flag → pace adjustments, no aggressive goals.

---

## 12. BMR Floor Safety Net

### 12.1 BMR formula (lifted from mysite `nutrition_calc.py`)

Standard Mifflin-St Jeor:
- Women: `BMR = 10 × weight_kg + 6.25 × height_cm - 5 × age - 161`
- Men: `BMR = 10 × weight_kg + 6.25 × height_cm - 5 × age + 5`

### 12.2 Floor check

When computing daily kcal target:
- Compute customer's daily target based on goal + pace
- If target < BMR + 100 kcal → AUTO-LADDER:
  - Drop pace to «gentle»
  - If still below floor → flip goal to «maintain»
  - Set `goal_overridden_by = 'bmr_floor'`
  - Bot DM customer:
```
Ваша цель попадёт под безопасный минимум калорий. Поставила режим «поддержание веса» — это безопаснее.

Если хотите обсудить — врач / диетолог поможет.

[Поняла]
```

### 12.3 NEVER silent

Hard floor is ALWAYS visible to customer with explanation. They have right to know.

### 12.4 BMI low + lose goal warning ladder

Customer with BMI < 18.5 sets goal=lose:
1. Soft warning #1: «*При вашем весе цель «похудеть» может быть небезопасной. Хотите обсудить с врачом?*»
2. If customer continues: warning #2 «*Понимаю. Помечу что вы знаете — но это решение лучше с врачом.*»
3. If customer continues: override flag `bmi_warning_overridden_at = NOW`; track in profile but don't block

NEVER hard-block. Customer autonomy respected.

---

## 13. Privacy enforcement

Same model as Body / Symptom Diary (high). Photos require additional rules.

### 13.1 API-level
- Customer-only; 403 on tenant mismatch
- ZERO tenant-side endpoints

### 13.2 Master pre-arrival context
Food data NEVER surfaces. Master sees nothing about customer's food.

### 13.3 Photo storage (different from AI Avatar)

Food photos are NOT as sensitive as Avatar (body) photos. Default retention:
- Photo bytes retained 7 days then auto-purged (just enough for customer to correct/edit)
- Customer can opt-in to «keep photos» for AI Avatar correlation Phase 5+
- Recognition results (kcal/macros/dish_name) retained per general retention policy

### 13.4 Logging
- API: event_id + path + outcome (no food values)
- Note field: TRACE only
- PII detector treats `dish_name` as low-sensitivity but `note` as moderate (could contain personal context)

### 13.5 Retention
- Per Q-WI10 consistency
- Soft-delete 30d on revoke
- OP6 cascade
- Data export includes raw food history + photos (within 7d retention window)

### 13.6 Founder access
NO direct read. Legal hold + 4-eye approval only.

---

## 14. API contracts

### 14.1 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/customer/wellness/food/photo` | Customer | Upload photo, trigger recognition |
| POST | `/api/v1/customer/wellness/food/event` | Customer | Save food event (after confirm OR manual) |
| POST | `/api/v1/customer/wellness/food/parse` | Customer | Parse free-text food description |
| PATCH | `/api/v1/customer/wellness/food/event/<id>` | Customer | Correct/edit |
| DELETE | `/api/v1/customer/wellness/food/event/<id>` | Customer | Soft-delete |
| GET | `/api/v1/customer/wellness/food/events` | Customer | List (paginated, date range, meal_type filter) |
| GET | `/api/v1/customer/wellness/food/daily-report` | Customer | Daily report (cached) |
| POST | `/api/v1/customer/wellness/health-profile` | Customer | Save/update health screening |

### 14.2 POST `/api/v1/customer/wellness/food/photo`

**Request** (multipart):
```
form-data:
  photo: <binary>
  caption: "обед сегодня" (optional)
  consumed_at: "2026-05-19T13:00:00Z" (optional; defaults to now)
  meal_type: "lunch" (optional; AI may infer)
```

**Validation**:
- Consent granted
- File size ≤ 10 MB
- Image dimensions ≤ 4096×4096
- Anti-spam: max 30 photo recognition requests per customer per day (cost protection)

**Response** (200):
```json
{
  "recognition": {
    "is_food": true,
    "dish_name": "Паста с курицей и томатами",
    "confidence": 0.85,
    "kcal": 520,
    "protein_g": 35,
    "fat_g": 18,
    "carbs_g": 55,
    "portion_estimate": "medium",
    "candidates": [...]  // only populated if confidence in medium range
  },
  "photo_id": "uuid",
  "photo_retention_expires_at": "2026-05-26T13:00:00Z",
  "next_step": "confirm_or_correct"
}
```

If `is_food=false`:
```json
{
  "recognition": {"is_food": false, ...},
  "next_step": "not_food_redirect"
}
```

### 14.3 POST `/api/v1/customer/wellness/food/event`

**Request**:
```json
{
  "dish_name": "Паста с курицей и томатами",
  "consumed_at": "2026-05-19T13:00:00Z",
  "meal_type": "lunch",
  "kcal": 520,
  "protein_g": 35,
  "fat_g": 18,
  "carbs_g": 55,
  "portion_estimate": "medium",
  "photo_id": "uuid",  // optional; link to retained photo
  "source": "photo_recognized",
  "note": ""
}
```

**Response** (201):
```json
{
  "id": "uuid",
  "consumed_at": "...",
  "...": "...",
  "daily_totals_today": {
    "kcal": 950,
    "protein_g": 58,
    "fat_g": 32,
    "carbs_g": 78,
    "events_count": 3
  },
  "silent_mode_active": false
}
```

If silent mode: omit `daily_totals_today` block entirely.

### 14.4 POST `/api/v1/customer/wellness/food/parse`

**Request**:
```json
{
  "text": "съела пасту с курицей и помидорами 250г на обед",
  "context": {
    "consumed_at": "now",
    "recent_meals": [...]  // optional context for portion inference
  }
}
```

**Response** (200):
```json
{
  "parsed": {
    "is_food": true,
    "dish_name": "...",
    "...": "..."
  },
  "next_step": "confirm_or_correct"
}
```

### 14.5 GET `/api/v1/customer/wellness/food/daily-report`

**Query**: `date` (defaults today)

**Response** (200):
```json
{
  "date": "2026-05-19",
  "events_count": 3,
  "totals": {
    "kcal": 950,
    "protein_g": 58,
    "fat_g": 32,
    "carbs_g": 78
  },
  "target": {
    "kcal": 1450,
    "protein_g": 110,
    "fat_g": 50,
    "carbs_g": 145
  },
  "ai_observation": "На сегодня съедено в норме.",
  "cross_module_context": {
    "sleep_last_night": {"duration_hours": 7.5, "quality": 4},
    "mood_today": 4,
    "water_today_ml": 1500
  },
  "silent_mode_active": false,
  "cached_at": "2026-05-19T18:00:00Z"
}
```

If silent_mode_active: omit `totals` and `target` blocks; ai_observation suppressed.

### 14.6 PATCH / DELETE — standard patterns

---

## 15. Cross-module integration

### 15.1 Wellness Profile Layer 6 Nutrition

Aggregator writes:
- `kcal_avg_daily_7d` / `_30d`
- `food_log_consistency_7d` (events per day average)
- `food_event_count_30d`

### 15.2 Service correlation (Phase 4+)

After 30+ food events accumulated:
- «В неделях с лимфодренажем — записи чаще, объёмы похожи» (observational)
- NEVER causal framing
- NEVER «продолжайте курс» nudge

### 15.3 Cross-module observations

Cross-module insight engine (Phase 4+) may surface in daily report:
- «3 ночи короткого сна + меньше калорий в эти дни» observation
- «Низкая вода + меньше еды» observation
- NEVER medical interpretation

---

## 16. Anti-patterns specific to Food Scanner

| Anti-pattern | Why bad | Correct |
|---|---|---|
| «Streak: 10 days logged!» | Anti-OCD; gamification | NEVER streaks |
| Daily calorie deficit target | Diet-app territory | Target = informational max, NEVER «лимит» |
| «You went over your goal today!» | Shame framing | NEVER warning on exceeding target |
| «Сегодня съели только 600 ккал — это слишком мало!» | Restrictive framing implies medical advice | Per §10 silent mode if applicable; otherwise observational «получили меньше нормы» |
| Public sharing / leaderboards | Privacy + comparison shame | NEVER |
| Recipe suggestions | Coaching scope | NEVER |
| Specific diet (keto/paleo/etc.) recommendations | Diet-app territory | NEVER |
| «Меньше углеводов!» / «Больше белка!» | Macros prescription | Display macros; NEVER prescribe |
| BMI labels (ожирение, недовес, etc.) | Medical territory | NEVER (per Body handoff §13) |
| Drug / supplement names | Treatment | NEVER |
| Gamified «meal collection» / «achievement badges» | Childish + OCD-triggering | NEVER |
| Auto-recommend specialist type («диетолог» vs «эндокринолог») | Beyond scope | «к врачу» / «к диетологу» generic only |
| Tracking minors | Privacy + legal | Phase 4+ family mode strict |
| Surface food data to salon side | Privacy violation | NEVER tenant access |
| Calorie burn calculations | Diet/fitness scope | NEVER |
| «Healthy» vs «unhealthy» labels | Moralizing | Just kcal/macros numbers |
| Force health screening before logging | Lifted from mysite §1.4 — screening = gate for ADVICE not basic logging | Photo recognition + calorie display work without screening |
| Eating disorder mode override accidentally on daily report | Privacy + safety | Triple-check at API level |
| Display previous day's report when customer requests current | Stale data | Always check cached_at; regenerate if stale |
| AI generates «meal suggestions» («попробуйте салат...») | Coaching scope | NEVER suggest specific foods |
| Comparison with other customers («средний клиент 1800 ккал») | Privacy + comparison | NEVER |

---

## 17. Acceptance criteria (engineering checklist)

- [ ] `apps/wellness/food/` Django sub-module created
- [ ] `WellnessFoodEvent` + `WellnessHealthProfile` models with validators
- [ ] Migrations idempotent
- [ ] 8 API endpoints implemented + tested
- [ ] Customer auth required; tenant boundary; 403 on mismatch
- [ ] Activation Paths A + B implemented; Path C deferred Phase 3.5
- [ ] Consent dialog UI per §4.1 with EXPLICIT non-diet-app disclaimer
- [ ] Health screening (TIER-A 5 steps) implemented per §5.2
- [ ] TIER-B lazy health screening per §5.3
- [ ] Vision API integration (Ayla / OpenAI gpt-4o)
- [ ] **Confidence-based routing §8.4** — high/medium/low/not-food branches
- [ ] Edit-message loading pattern §8.3
- [ ] Correction flow §8.5
- [ ] Free-text food log via `parse_food_freetext` LLM tool §8.7
- [ ] Daily report cached in `customer.context["daily_report"]` §9.1
- [ ] **Eating disorder silent mode §10** — ALL calorie/macro hidden when flag set
- [ ] **Pregnancy / breastfeeding override §11** — caloric target = maintain
- [ ] **BMR floor safety net §12** — auto-ladder + customer notification
- [ ] **BMI low + lose goal warning ladder §12.4**
- [ ] Anti-spam max 30/day with 429
- [ ] Photo retention 7 days then auto-purge §13.3
- [ ] Events emitted per §18
- [ ] Privacy enforcement per §13
- [ ] Aggregator writes Wellness Profile Layer 6 §15.1
- [ ] Tests:
  - unit (model + validators + nutrition_calc BMR + confidence routing)
  - API (auth + 429 + recognition + correction + silent_mode_active flag)
  - integration (consent → photo → recognition → confirm → save → daily report)
  - privacy (cross-tenant denial)
  - silent mode (eating disorder flag → no kcal in response)
  - bmr floor (low goal → auto-ladder + override flag)
- [ ] **Pre-deploy: study `mysite/maxbot` reference + adapt patterns**
- [ ] **Legal sign-off**: §4 disclaimer + §10 eating disorder + §11 pregnancy + §12 BMR floor + §16 anti-patterns
- [ ] Accessibility audit on Mini App + correction modals (WCAG 2.2 AA)
- [ ] Documentation in `apps/wellness/food/README.md` referencing this handoff

---

## 18. Events emitted

Per [`event-taxonomy.md §3.6`](../policies/event-taxonomy.md#36-wellness-domain):

| Trigger | Event | Notes |
|---|---|---|
| Consent granted | `wellness.consent.module.granted` | `module_name='food'` |
| Consent revoked | `wellness.consent.module.revoked` | `module_name='food'` |
| Food event saved | `wellness.input.recorded` | `module_name='food'`, `input_type=source`, `confidence=recognition_confidence or 1.0`, `source` |
| Food event edited | NEW: `wellness.food.event.edited` | audit |
| Food event soft-deleted | NEW: `wellness.food.event.deleted` | grace start |
| Vision recognition fired | NEW: `wellness.food.recognition.completed` | analytics: `confidence`, `is_food`, `latency_ms`, `cost_usd` (for budget tuning) |
| Silent mode activated | NEW: `wellness.food.silent_mode.activated` | `customer_id`, `reason` (eating_disorder / manual / pattern_detected) |
| BMR floor override | NEW: `wellness.food.bmr_floor.applied` | `customer_id`, `attempted_target`, `actual_floor`, `goal_overridden_to` |
| Pregnancy override | NEW: `wellness.food.pregnancy_override.applied` | `customer_id`, `applied_at` |
| Health screening TIER-A done | NEW: `wellness.food.health_screening.tier_a.completed` | analytics |
| Health screening TIER-B done | NEW: `wellness.food.health_screening.tier_b.completed` | analytics |
| Daily report regenerated | NEW: `wellness.food.daily_report.generated` | cost analytics |
| Aggregator writes profile | `wellness.profile.layer.updated` | `layer_name='layer_6_nutrition'` |

Add 7 NEW events to event-taxonomy.md §3.6.

---

## 19. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-WF1** | Vision API — Ayla shared service or direct OpenAI? | Ayla MVP if available (shared cost + observability); fallback direct OpenAI if Ayla nutrition endpoints not yet ported | Eng + Founder | 🟡 |
| **Q-WF2** | Photo retention 7 days — too short or long? | 7d enough for correction window; longer = privacy risk. Customer opt-in to keep for AI Avatar correlation Phase 5+. | Privacy + UX | 🟡 |
| **Q-WF3** | Default daily target if customer skips TIER-A — show NULL or generic 2000? | Show NULL («норма не настроена») + nudge to complete TIER-A; never default to 2000 (could be wrong for customer's needs) | UX | 🟢 |
| **Q-WF4** | Photo file size cap 10MB — too restrictive? | 10MB MVP per AI Avatar consistency; modern phone photos easily fit | Eng | 🟢 |
| **Q-WF5** | Free-text parsing «съела пасту» without portion — best-guess «medium»? | YES — `portion_estimate='medium'` default; customer can correct | AI | 🟢 |
| **Q-WF6** | Vision cost optimization at >1000 active users — gpt-4o-mini for some cases? | Defer Phase 4+ — wait for data showing accuracy hit acceptable. MVP: gpt-4o full | Eng + Founder | 🟢 |
| **Q-WF7** | Anti-spam 30/day — heavy customer hitting this often? | Cap stands; if hit, flag to support (may be data-entry error or anxiety pattern) | Eng + Policy | 🟡 |
| **Q-WF8** | Daily report cache — invalidate on edit / delete? | YES — any event change invalidates cache; regenerate on next request | Eng | 🟢 |
| **Q-WF9** | Per-tenant override of «по умолчанию советы выключены»? | NO — Food Scanner is platform-wide pattern. Tenant can't enable diet-app mode. | Policy | 🟢 |
| **Q-WF10** | Customer revokes Food Scanner — what happens to retained photos? | Hard-delete photos within 24h (similar to AI Avatar §12.3 standard); food events soft-delete 30d | Privacy | 🟡 |
| **Q-WF11** | Pregnancy override Q-WF11 — auto-detect via NLU («я беременна») OR only via health screening? | Health screening only (deliberate consent); NLU detection Phase 4+ if signal-to-noise good | Policy + AI | 🟡 |
| **Q-WF12** | Eating disorder silent mode persistence — survive consent revoke / re-grant? | YES — flag survives revoke; re-enable to ask if disabling | Privacy + Safety | 🔴 before first eating-disorder-flagged customer |
| **Q-WF13** | BMR floor formula — Mifflin-St Jeor MVP; revisit for accuracy? | Standard MVP; lift from `mysite/nutrition_calc.py` (battle-tested) | Eng | 🟢 |
| **Q-WF14** | Cross-module observation in daily report — Phase 3 MVP or Phase 4? | Phase 3 with simple rules; Phase 4+ ML cross-correlation insights | UX + Eng | 🟢 |
| **Q-WF15** | What if `recognize_food` API call fails (timeout / API error)? | Bot DM: «*Не получилось распознать сейчас. Попробуете снова или напишите текстом?*» + `[📸 Переснять]` `[✏️ Напишу]` | Eng + UX | 🟡 |
| **Q-WF16** | Customer enters macros that don't sum («20г белка, 800 ккал») — accept or warn? | Accept; customer's data, customer's responsibility. Don't moralize. | UX | 🟢 |
| **Q-WF17** | Multi-meal photo (вижу 3 блюда на столе) — recognize each separately or composite? | Composite MVP («ужин 3 блюда» one event); split functionality Phase 4+ | UX + AI | 🟡 |
| **Q-WF18** | Customer adds food event during PAUSED tenant — works? | YES — wellness data is customer-owned, not tenant-owned. Per tenant-suspension §11.2 wellness module behavior. | Eng | 🟢 |
| **Q-WF19** | Phase 4+ wearable integration — what data sources? | Apple Health / Google Fit MVP P4+; HealthKit dietary energy API specifically | Eng | 🟢 |
| **Q-WF20** | Daily report cost — 1 LLM call/day/customer at scale = expensive. Cache aggressive? | YES — single cache per customer per day; only regenerate on event invalidation OR explicit «обновить» tap. Background batch generation overnight Phase 5+. | Eng + Founder | 🟡 |
| **Q-WF21** | Allergen warning when food event has allergen — show banner? | YES — if `WellnessHealthProfile.allergies` contains item matching recognized dish ingredients (Phase 4+ when ingredient list available), show «*В блюде может быть {{allergen}} — учли в дневнике*» neutral observation | Eng + AI | 🟡 |
| **Q-WF22** | Customer-pays tier Phase 3 vision — free or paid? | Free MVP (per Q-WI12 wellness modules); revisit if costs > business model | Founder | 🟢 |

---

## 20. Cross-document linkage

- [`../policies/wellness-input-modules.md §2`](../policies/wellness-input-modules.md#2-module-1--food-scanner) — strategic spec ported
- **`mysite/maxbot/` reference implementation** (Формула тела Phase 3) — §0.2 file map
- **`mysite/.claude/worktrees/.../docs/plans/maxbot-phase3-nutrition-design.md`** — full v2 design (1125 lines)
- [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md) — sibling Phase 1 pattern
- [`./2026-05-19-wellness-water-handoff.md`](./2026-05-19-wellness-water-handoff.md) — sibling (food + water synergy hooks per §8.6 footer buttons)
- [`./2026-05-19-wellness-body-handoff.md`](./2026-05-19-wellness-body-handoff.md) — anti-OCD framework
- [`./2026-05-19-wellness-sleep-handoff.md`](./2026-05-19-wellness-sleep-handoff.md) — cross-module observation context
- [`./2026-05-19-wellness-symptom-handoff.md`](./2026-05-19-wellness-symptom-handoff.md) — medical-routing pattern; similar anti-pattern severity
- [`./2026-05-19-wellness-ai-avatar-handoff.md`](./2026-05-19-wellness-ai-avatar-handoff.md) — photo retention rules (§13.3)
- [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md) — voice anchors; §7.2 medical routing
- [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — HUMAN_LOCKED gating
- [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md) — food reminders opt-in (anti-OCD lean)
- [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md) Layer 6 Nutrition — aggregator writes
- [`../policies/event-taxonomy.md §3.6`](../policies/event-taxonomy.md#36-wellness-domain) — 7 NEW events §18
- [`../policies/master-conversational-templates.md §5.5`](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface) — privacy boundary
- [`../policies/customer-profile-management-ux.md §4`](../policies/customer-profile-management-ux.md) — activation entry
- [`../policies/customer-first-touch-and-mini-app-states.md`](../policies/customer-first-touch-and-mini-app-states.md) — error/empty states (loading skeleton for vision)
- [`../policies/tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md) — customer-owned data preservation
- [`../decisions-log.md`](../decisions-log.md) — Q-WI10 retention, Q-WI12 free tier

---

## 21. What this unblocks

- **`apps/wellness/food/` Phase 3+ implementation** — model + API + Mini App + vision integration engineering-ready
- **Wellness Profile Layer 6 populated** — first nutrition data
- **Cross-module insight foundation** — Phase 4+ food+sleep+mood+symptoms correlations
- **Salon service correlation Phase 4+** — observational links between procedures and food patterns
- **Differentiation from beauty platforms** — most competitors don't have food tracking
- **Battle-tested patterns from mysite** — confidence routing, edit-loading, correction flow proven at scale

## 22. What this does NOT unblock

- ❌ Diet app features (calorie deficits, weight loss targets, meal plans)
- ❌ Coaching scope (specific recipe / food recommendations)
- ❌ Medical scope (drug recommendations, condition-specific diets)
- ❌ Public sharing / social features
- ❌ Children tracking
- ❌ Wearable integration (Phase 4+)
- ❌ Skip mysite reference review (engineering must study battle-tested patterns)
- ❌ Skip legal sign-off on §4 disclaimer + §10/§11/§12 safety nets + §16 anti-patterns

---

## 23. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Wellness backend lead (apps/wellness/food/) | ☐ | |
| Mini App frontend (Еда section + capture + correction flow + daily report) | ☐ | |
| AI / ML lead (vision integration + free-text parsing + recognition confidence) | ☐ | |
| **Legal / Compliance** (§4 disclaimer + §10 eating disorder + §11 pregnancy + §12 BMR floor + medical routing copy) | ☐ | 🔴 PRE-DEPLOY |
| Privacy / Legal (Q-WF2/10/12 + photo retention) | ☐ | |
| Founder (Q-WF1 Ayla vs OpenAI + Q-WF22 free tier + Q-WF6 cost optimization) | ☐ | |
| Accessibility (WCAG 2.2 AA on photo + correction modals + daily report) | ☐ | |
| Policy review (Q-WF12 eating disorder persistence + Q-WF7 anti-spam mental-health-adjacent) | ☐ | |

## Last verified
2026-05-19 (initial draft, engineering-ready for Phase 3 Wellness Food Scanner — most complex wellness module; lifts mysite/maxbot battle-tested patterns; anti-diet-app + eating-disorder-silent + pregnancy-override + BMR-floor safety nets)
