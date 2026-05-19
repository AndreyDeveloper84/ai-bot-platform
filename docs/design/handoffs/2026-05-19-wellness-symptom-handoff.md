# Wellness Symptom Diary Module — engineering handoff

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Engineering-ready — Phase 2 wellness module №5 (after Mood + Water + Body + Sleep)
**Reads:** [`../policies/ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md), [`../policies/ayla-memory-and-personalization.md`](../policies/ayla-memory-and-personalization.md), [`../policies/ayla-emergency-fallback-policy.md`](../policies/ayla-emergency-fallback-policy.md), [`../policies/wellness-input-modules.md`](../policies/wellness-input-modules.md) §8 (Module 7 Symptom Diary), [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md), [`./2026-05-19-wellness-water-handoff.md`](./2026-05-19-wellness-water-handoff.md), [`./2026-05-19-wellness-body-handoff.md`](./2026-05-19-wellness-body-handoff.md), [`./2026-05-19-wellness-sleep-handoff.md`](./2026-05-19-wellness-sleep-handoff.md), [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md), [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md), [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md), [`../policies/customer-profile-management-ux.md`](../policies/customer-profile-management-ux.md)

> Ports [wellness-input-modules §8 Symptom Diary](../policies/wellness-input-modules.md#8-module-7--symptom-diary) to engineering-ready spec. **Most medical-adjacent module on the platform** — strictest anti-pattern enforcement + explicit medical-routing protocol. Phase 2 basic structured entry; Phase 3+ pattern detection + service correlation.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](../policies/ayla-identity-and-brand.md) memory 2026-05-19: symptom data is **red-zone** in Ayla's memory per [`ayla-memory-and-personalization §2.3`](../policies/ayla-memory-and-personalization.md) — encrypted, 90d unused → auto-delete, NEVER mentioned without customer initiation. Medical injury allegations fire [`ayla-emergency-fallback-policy §3.4`](../policies/ayla-emergency-fallback-policy.md) `legally_sensitive` tier (founder auto-engaged). AI voice samples use Ayla per [`ayla-identity-and-brand §2`](../policies/ayla-identity-and-brand.md).

---

## 0. Why this exists

### Strategic context

Chronic-care customers (back pain, skin conditions, swelling, recurring symptoms) are **highest-LTV** per [`wellness-input-modules §8.2`](../policies/wellness-input-modules.md#82-why-this-matters):
- Structured data enables real correlation with services
- AI can detect patterns customer doesn't notice
- Bridges Wellness Profile Layer 3 Body State + Layer 4 Service History

### Critical caveat

This is the **most medical-adjacent module**. Engineering MUST enforce strict anti-pattern boundaries:
- NO diagnostic terminology
- NO treatment recommendations
- NO drug names
- Explicit routing-to-specialist protocol for severe/chronic cases

### The gap

[wellness-input-modules §8](../policies/wellness-input-modules.md#8-module-7--symptom-diary) describes strategically but doesn't specify:
- Activation flow + paths
- Symptom + zone + intensity + trigger structured schema
- Mini App layout for symptom logging
- Medical-routing protocol (when AI suggests specialist; what AI never says)
- Pattern detection algorithm (Phase 3+ preview)
- Per-state behavior
- API contracts
- Cross-correlation with services + other modules

### The promise

Single source for `apps/wellness/symptom/` Phase 2 implementation. Engineering ships with explicit medical-adjacent boundaries.

---

## 1. Scope

### IN
- New sub-module `apps/wellness/symptom/`
- `WellnessSymptomEvent` model (event-driven; not daily cadence)
- Activation Paths A + B (Path C deferred Phase 2.5+)
- Consent dialog with explicit medical-disclaimer
- Mini App Самочувствие → Симптомы section (add / list / patterns view)
- Bot DM ad-hoc log via NLU («болит шея» / «головная боль») — Phase 2.5+
- 6 API endpoints
- Per-state behavior matrix
- Phase 2 basic insights (count + grouping by type/zone); Phase 3+ pattern detection
- **Medical routing protocol** §10 (when AI suggests specialist; what AI NEVER says)
- Privacy enforcement (customer-only; high-sensitivity)
- Wellness Profile Layer 3 + Layer 4 integration
- Cross-module synergy stubs (Mood / Sleep / Services Phase 3+)
- 4 NEW events for event-taxonomy

### OUT
- Diagnosis claims of any kind (FORBIDDEN per §2)
- Treatment recommendations (FORBIDDEN)
- Drug / supplement / medication references (FORBIDDEN)
- Severity grading on medical scales (e.g., VAS pain scale clinical interpretations)
- Symptom database / lookup feature
- Family / children's symptom tracking (Phase 4+ explicit family mode)
- HealthKit / Google Fit symptom sync (Phase 4+)
- Customer-pays gating (free forever per Q-WI12)
- Symptom photos (use AI Avatar module for visual; symptoms are text-only here)
- Telehealth referral integration (out of scope; we recommend customer find their own specialist)

---

## 2. Strategic constraints — non-negotiable

These are the strictest in the platform. Engineering reviewer rejects ANY PR violating.

### 2.1 No diagnostic claims (most critical)
- NEVER name specific conditions («остеохондроз», «дерматит», «мигрень», «артрит», etc.)
- NEVER infer medical conditions from patterns
- NEVER use ICD codes or medical terminology
- ONLY observational language («заметила, что отмечаете боль в шее N раз»)

### 2.2 No treatment recommendations
- NEVER suggest medications («попробуйте ибупрофен»)
- NEVER suggest supplements («магний помогает от мышечных спазмов»)
- NEVER suggest exercises («сделайте растяжку»)
- NEVER suggest dietary changes («исключите глютен»)
- Service recommendations ALLOWED only if they're salon's actual services + observational framing

### 2.3 Explicit medical-routing protocol §10
- If severity thresholds met → AI suggests «обратиться к врачу» (generic, NOT specific specialist)
- AI ALWAYS says: «Я Ayla — AI-помощник, не врач. Это наблюдение, не диагноз.»
- Customer's decision to seek medical care is theirs

### 2.4 Privacy hierarchy
Same as Body (high). Stricter than Mood/Water/Sleep. NOT as strict as AI Avatar (photos).
- Customer-only at API
- NEVER salon-side, including aggregate
- Soft-delete 30d on revoke
- Free-text note treated as PII sensitive

### 2.5 No symptom database
We don't have a «symptom checker» feature. Customer logs what they experience; system stores categorical + free text; system does NOT lookup possible causes/conditions. Anti-pattern: WebMD-style «could be X, Y, or Z» suggestions FORBIDDEN.

---

## 3. Activation flow

### 3.1 Eligibility (gates)

Customer cannot activate Symptom Diary if:
- `consent.ai_messaging = false` (exception: Path A self-discovery)
- `core_user_state ∈ {DORMANT, HUMAN_LOCKED active conversation}`
- Tenant in PAUSED / SUSPENDED state
- Customer's MAX account suspended
- Customer < 18 years old (medical data + minors = absolute red line; same as AI Avatar + Body)

### 3.2 Activation triggers (Phase 2 launch)

**Path A — Self-discovery in Mini App** (always available):
Customer navigates Профиль → Самочувствие → Симптомы card → toggle ON → consent dialog §4.

**Path B — Customer-mentioned-symptoms offer** (Phase 2.5+ requires NLU):
After customer mentions symptom words («болит», «отёк», «высыпания», «уставшая», etc.) ≥ 2 times in DM within 7 days AND `consent.ai_messaging = true`, AI sends ONE offer:
```
Заметила что вы упоминали {{symptom_word}} несколько раз.

Хотите я буду помогать отслеживать — что и когда отмечаете, что может быть связано?

Это не диагноз и не лечение — просто наблюдение, чтобы вы видели картину.

[Попробовать]   [Не сейчас]
```

Same Path B suppression: «не сейчас» → mark `customer.symptom_offer_declined_at`; never re-offer.

**Phase 2 launch: Path A only**. Path B Phase 2.5+ (NLU work required for symptom-word detection).

### 3.3 Activation events
- `wellness.consent.module.granted` with `module_name='symptom'`, `granted_via=<path>`

---

## 4. Consent dialog

### 4.1 Single-screen with EXPLICIT medical disclaimer

```
┌────────────────────────────────────────┐
│ Отслеживать симптомы?                  │
├────────────────────────────────────────┤
│ Я буду:                                │
│   • Запоминать что и когда вас         │
│     беспокоит                          │
│   • Показывать паттерны (когда         │
│     появляется чаще, после чего)       │
│   • Связывать с процедурами студии     │
│                                        │
│ ── ВАЖНО ──                             │
│                                        │
│ Я НЕ врач. Я НЕ ставлю диагнозы. Я НЕ  │
│ предлагаю лекарства или лечение.        │
│                                        │
│ Если что-то беспокоит сильно или       │
│ длительно — обратитесь к врачу.        │
│                                        │
│ ── Что важно ──                         │
│                                        │
│ ✓ Данные видите только вы              │
│ ✓ Студия НЕ видит ничего                │
│ ✓ Никаких медицинских терминов         │
│   и советов                            │
│ ✓ Просто наблюдения за паттернами       │
│                                        │
│ [Не сейчас]      [Согласна, попробуем] │
└────────────────────────────────────────┘
```

### 4.2 Critical design choices

- **Explicit «Я НЕ врач»** in VAЖНО section — pre-frames customer expectations + legal liability protection
- **Reminders default OFF** — symptoms are event-driven (no daily reminder for «отмечать ли симптомы?»)
- **No prompt time field** — symptom logging is reactive (customer logs when something happens), not scheduled

### 4.3 Outcomes

#### Tap «Согласна, попробуем»
- Create `WellnessModuleConsent(module_name='symptom', granted=True, granted_via=<path>, config={"reminders_enabled": false})`
- Emit `wellness.consent.module.granted` event
- Navigate to Самочувствие → Симптомы section §6
- First-use toast: «Готово. Добавьте запись когда что-то беспокоит.»

#### Tap «Не сейчас»
- NO record created
- Path B activation: mark `customer.symptom_offer_declined_at = NOW`; never re-offer
- Path A: customer can re-open consent dialog later

---

## 5. Data model

### 5.1 `WellnessSymptomEvent`

Event-driven (not periodic).

```python
class WellnessSymptomEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='symptom_events')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    occurred_at = models.DateTimeField()
    # When symptom occurred (can be backdated up to 30 days for retroactive logging)

    SYMPTOM_TYPE_CHOICES = [
        ('pain', 'Боль'),
        ('skin_issue', 'Кожные проблемы (высыпания, раздражение)'),
        ('swelling', 'Отёчность'),
        ('fatigue', 'Усталость / упадок сил'),
        ('hair_issue', 'Состояние волос'),
        ('digestive', 'Пищеварение'),  # cautious — could be medical, but customers want to track
        ('sleep_related', 'Проблемы со сном'),
        ('other', 'Другое'),
    ]
    symptom_type = models.CharField(max_length=32, choices=SYMPTOM_TYPE_CHOICES)

    ZONE_CHOICES = [
        ('head', 'Голова'),
        ('face', 'Лицо'),
        ('neck', 'Шея'),
        ('shoulders', 'Плечи'),
        ('back', 'Спина (верх)'),
        ('lower_back', 'Поясница'),
        ('chest', 'Грудь'),
        ('abdomen', 'Живот'),
        ('hands', 'Руки'),
        ('legs', 'Ноги'),
        ('feet', 'Стопы'),
        ('hair', 'Волосы / голова целиком'),
        ('skin_general', 'Кожа целиком'),
        ('whole_body', 'Всё тело'),
        ('other', 'Другое'),
    ]
    zone = models.CharField(max_length=32, choices=ZONE_CHOICES)

    intensity = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)])
    # 1 = легко, 10 = очень сильно. Customer's subjective.

    POSSIBLE_TRIGGER_CHOICES = [
        ('long_sitting', 'Долго сидела за компьютером'),
        ('stress_day', 'Стрессовый день'),
        ('no_sleep', 'Не выспалась'),
        ('weather', 'Погода / давление'),
        ('hormonal', 'Гормональные изменения'),
        ('workout', 'После тренировки'),
        ('food', 'После еды'),
        ('not_sure', 'Не уверена'),
        ('other', 'Другое (в заметке)'),
    ]
    possible_triggers = models.JSONField(default=list, blank=True)
    # Array of choices (multi-select). Empty if customer didn't specify.

    note = models.TextField(max_length=500, blank=True, default='')
    # Free-text additional context. 500 chars (vs 280 in other modules — symptoms warrant more).

    SOURCE_CHOICES = [
        ('mini_app_section', 'Mini App Самочувствие → Симптомы'),
        ('bot_dm_log', 'Bot DM (Phase 2.5+ via NLU)'),
    ]
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES)

    recorded_at = models.DateTimeField()
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['customer', '-occurred_at']),  # timeline
            Index(fields=['customer', 'symptom_type', 'zone']),  # pattern detection
            Index(fields=['tenant', 'created_at']),  # analytics aggregation
        ]
```

### 5.2 Reuses `WellnessModuleConsent`

Config JSON for symptom:
```python
{
  "reminders_enabled": false,  # symptoms are event-driven; no scheduled reminder
}
```

### 5.3 Anti-spam + validation

- Per-customer per-day: max 20 symptom events (anti-OCD / anti-noise; legitimate chronic-care customer rarely exceeds)
- `intensity` ∈ [1, 10]
- `occurred_at` ≥ today - 30 days (longer backdate than other modules — symptom may have started days ago)
- `occurred_at` ≤ now + 5 min tolerance
- `possible_triggers` must be subset of allowed enum
- `symptom_type` + `zone` in respective enums

---

## 6. Mini App «Самочувствие» → Симптомы section

### 6.1 Empty state (no symptoms logged yet)

```
┌────────────────────────────────────────┐
│ 🩹 Симптомы                             │
├────────────────────────────────────────┤
│ Пока нет записей.                       │
│                                        │
│ Добавьте запись когда что-то            │
│ беспокоит — будете видеть паттерны     │
│ со временем.                            │
│                                        │
│ ⚠️ Я не врач — это просто наблюдения.  │
│                                        │
│ [+ Добавить запись]                    │
└────────────────────────────────────────┘
```

### 6.2 Populated state

```
┌────────────────────────────────────────┐
│ 🩹 Симптомы                             │
├────────────────────────────────────────┤
│ Последние 7 дней: 2 записи             │
│                                        │
│ [+ Добавить запись]                    │
│                                        │
│ ── По типам за месяц ──                 │
│                                        │
│ Боль (шея)        4 раза                │
│ Отёчность (ноги)  1 раз                 │
│ Усталость         2 раза                │
│                                        │
│ ── Что заметно ──                       │
│ {{insight per §10 Phase 3+; MVP simple count}}│
│                                        │
│ [Все записи →]                          │
└────────────────────────────────────────┘
```

### 6.3 Add symptom screen

```
┌────────────────────────────────────────┐
│ ← Запись о состоянии                    │
├────────────────────────────────────────┤
│ Что сейчас:                             │
│ ⦿ Боль                                  │
│ ◯ Высыпания                             │
│ ◯ Отёчность                             │
│ ◯ Усталость                             │
│ ◯ Состояние волос                       │
│ ◯ Пищеварение                           │
│ ◯ Сон                                   │
│ ◯ Другое                                │
│                                        │
│ Зона: [Шея ▾]                           │
│ (или: Голова / Спина / Поясница /       │
│  Плечи / Руки / Ноги / Лицо / Кожа /    │
│  Всё тело / Другое)                    │
│                                        │
│ Интенсивность:                          │
│ [1 ──●──── 10]  5/10                    │
│                                        │
│ Что могло спровоцировать? (опц.)        │
│ ☐ Долго сидела за компьютером           │
│ ☐ Стрессовый день                       │
│ ☐ Не выспалась                          │
│ ☐ Погода / давление                     │
│ ☐ Гормональные изменения                │
│ ☐ После тренировки                      │
│ ☐ После еды                             │
│ ☐ Не уверена                            │
│                                        │
│ Дата / время:                           │
│ [Сейчас ▾] (или backdate up to 30 days) │
│                                        │
│ Заметка (опц.):                        │
│ [_____________________________]        │
│ 0 / 500                                │
│                                        │
│ [Сохранить]                            │
└────────────────────────────────────────┘
```

### 6.4 Post-save medical-routing check (§10)

After save, if severity thresholds met → modal §10.3 fires. Otherwise silent save + 5-sec undo toast.

### 6.5 History view

```
┌────────────────────────────────────────┐
│ ← История                               │
├────────────────────────────────────────┤
│ Фильтр: [Все типы ▾] [Все зоны ▾]      │
│                                        │
│ 19 мая · 14:30 · сейчас                 │
│ Боль (шея) · 6/10                       │
│ Триггеры: долго сидела, стресс          │
│ «после рабочего дня»                    │
│ [Изменить] [Удалить]                   │
│                                        │
│ 14 мая · 09:00 · 5 дней назад           │
│ Боль (шея) · 5/10                       │
│ Триггеры: не уверена                    │
│ [Изменить] [Удалить]                   │
│                                        │
│ 12 мая · 18:00                          │
│ Отёчность (ноги) · 3/10                 │
│ «после длинной прогулки»                │
│ [Изменить] [Удалить]                   │
│                                        │
│ ...                                    │
└────────────────────────────────────────┘
```

### 6.6 No Главная quick chip

Symptoms are not routine — they happen. No persistent home-screen chip. Customer enters intentionally when needed.

---

## 7. Per-state behavior matrix

Symptoms are customer-initiated (no proactive bot prompts). Per-state still matters for activation gating + medical-routing modal visibility.

| Customer state | Activation possible? | Medical routing modal fires? |
|---|---|---|
| DISCOVERED | NO | n/a |
| EXPLORING | YES (Path A only) | YES if criteria met §10 |
| PROBLEM_SEEKING | YES | YES |
| READY_TO_BOOK | YES | YES |
| POST_VISIT | YES | YES |
| ACTIVE_REGULAR | YES | YES |
| AT_RISK_DRIFTING | YES (already activated; no new) | YES |
| DORMANT | NO | n/a |
| HUMAN_LOCKED active | NO | NO (admin owns) |
| HUMAN_LOCKED inactive | YES | YES |

---

## 8. Bot DM ad-hoc log (Phase 2.5+)

Phase 2.5+ — requires NLU work. Customer messages bot «болит голова», «высыпания на щеках» — AI:
1. Parses symptom_type + zone candidates
2. Asks confirmation: «Записать как «{{type}} ({{zone}})»? Интенсивность?»
3. Customer responds with intensity → AI saves
4. Standard medical-routing check §10 applies

Phase 2 MVP: NLU not in scope. Customer logs via Mini App only.

---

## 9. API contracts

### 9.1 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/customer/wellness/symptom` | Customer | Save symptom event |
| GET | `/api/v1/customer/wellness/symptoms` | Customer | List (paginated; filter symptom_type / zone / date range) |
| GET | `/api/v1/customer/wellness/symptoms/summary` | Customer | Aggregate counts by type/zone for period |
| GET | `/api/v1/customer/wellness/symptoms/patterns` | Customer | Pattern detection (Phase 3+; MVP returns basic counts) |
| PATCH | `/api/v1/customer/wellness/symptom/<id>` | Customer | Edit |
| DELETE | `/api/v1/customer/wellness/symptom/<id>` | Customer | Soft-delete |

### 9.2 POST `/api/v1/customer/wellness/symptom`

**Request**:
```json
{
  "occurred_at": "2026-05-19T14:30:00Z",
  "symptom_type": "pain",
  "zone": "neck",
  "intensity": 6,
  "possible_triggers": ["long_sitting", "stress_day"],
  "note": "после рабочего дня тянет",
  "source": "mini_app_section"
}
```

**Validation**:
- Customer has `WellnessModuleConsent.granted = True` for `module_name='symptom'`
- `intensity` ∈ [1, 10]
- `occurred_at` ∈ [now - 30d, now + 5min]
- `symptom_type` + `zone` in choices
- `possible_triggers` subset of choices
- `note` ≤ 500 chars
- Anti-spam: max 20 per day → 429 with «Очень много записей сегодня. Если что-то серьёзно беспокоит — обратитесь к врачу.»

**Response** (201):
```json
{
  "id": "uuid",
  "occurred_at": "2026-05-19T14:30:00Z",
  "symptom_type": "pain",
  "zone": "neck",
  "intensity": 6,
  "medical_routing_suggested": false,
  "medical_routing_reason": null
}
```

If `medical_routing_suggested = true`:
```json
{
  "id": "uuid",
  ...,
  "medical_routing_suggested": true,
  "medical_routing_reason": "high_intensity_chronic",
  "medical_routing_message": "Это длится больше месяца с сильной интенсивностью. Если ещё не были у врача — стоит обратиться."
}
```

Mini App displays modal §10.3 based on this.

### 9.3 GET `/api/v1/customer/wellness/symptoms/summary`

**Query**: `period_days` (default 30, max 365)

**Response** (200):
```json
{
  "period_days": 30,
  "total_events": 8,
  "by_type": {
    "pain": {"count": 4, "zones": {"neck": 4}, "avg_intensity": 5.2},
    "swelling": {"count": 1, "zones": {"legs": 1}, "avg_intensity": 3},
    "fatigue": {"count": 2, "zones": {"whole_body": 2}, "avg_intensity": 6},
    "skin_issue": {"count": 1, "zones": {"face": 1}, "avg_intensity": 4}
  },
  "insights": [
    {
      "type": "frequency",
      "text": "Боль в шее — 4 раза за месяц"
    }
  ],
  "medical_routing_recommended": false
}
```

If chronic pattern detected: `medical_routing_recommended = true` + insight «{{symptom}} в {{zone}} больше {{N}} раз за {{period}}. Стоит показаться врачу если ещё не были.»

### 9.4 GET `/api/v1/customer/wellness/symptoms/patterns` (Phase 3+ deeper)

Phase 2 MVP: returns same as summary §9.3 with empty `patterns: []`.

Phase 3+ returns:
```json
{
  "patterns": [
    {
      "type": "weekday_correlation",
      "description": "Боль в шее чаще в будни (4 из 5 после рабочих дней)"
    },
    {
      "type": "trigger_correlation",
      "description": "Когда отмечаете «долго сидела» — чаще боль в шее"
    }
  ]
}
```

### 9.5 PATCH / DELETE — standard patterns per Body §9

---

## 10. Medical-routing protocol

**Critical**: this section is the platform's protection against medical-liability + customer harm.

### 10.1 When medical routing triggers

| Trigger condition | Severity | Action |
|---|---|---|
| Single event `intensity ≥ 9` | Acute severe | Immediate suggestion: «Если это сейчас сильно беспокоит — стоит обратиться к врачу, не откладывая.» |
| 3+ events same type+zone in 7d, all `intensity ≥ 7` | Severe acute | Suggestion: «Часто и сильно беспокоит — стоит показаться врачу.» |
| 5+ events same type+zone in 30d (any intensity) | Chronic | Suggestion: «Бывает регулярно — врач поможет разобраться.» |
| 10+ events same type+zone in 90d | Chronic-confirmed | Stronger suggestion: «Это длится долго — стоит показаться врачу если ещё не были.» |
| Customer mentions «давно не проходит» / «месяцами» / «годами» in note | Chronic-self-reported | Same as 90d chronic |

### 10.2 What AI ALWAYS says alongside

```
⚠️ Я не врач, и это не диагноз. Просто наблюдение.
```

This disclaimer is in EVERY medical-routing suggestion. Non-negotiable.

### 10.3 Modal UX (when triggered)

```
┌────────────────────────────────────────┐
│ ⚠️ Наблюдение                           │
├────────────────────────────────────────┤
│ {{trigger_message}}                     │
│                                        │
│ Я не врач, это не диагноз — просто     │
│ заметила паттерн.                      │
│                                        │
│ Если ещё не были у врача — стоит        │
│ показаться. Врач увидит ситуацию       │
│ целиком и поможет разобраться.         │
│                                        │
│ Я могу:                                │
│ [Сохранить заметку для врача]          │
│ [Просто понятно]                       │
└────────────────────────────────────────┘
```

«Сохранить заметку для врача» creates a downloadable PDF with last N entries customer can show doctor — Phase 3+ feature; MVP just shows toast «Запись сохранена. Можно показать врачу историю в разделе Симптомы → История.»

### 10.4 What AI NEVER says

- ❌ «У вас, возможно, {{condition}}»
- ❌ «Это могут быть симптомы {{disease}}»
- ❌ «Попробуйте {{medication}}»
- ❌ «Запишитесь к {{specialist_type}}» (we don't recommend specialist types — customer decides; we say «к врачу» generic)
- ❌ «{{Service}} вам поможет» if symptom medical-adjacent
- ❌ «Это не страшно» / «это нормально» (we're not doctors; can't reassure)
- ❌ «Нужно срочно к врачу» (we don't escalate; we suggest)

### 10.5 Medical-emergency case (intensity 10 + acute language)

If customer logs `intensity = 10` AND symptom_type ∈ {pain, fatigue, digestive} AND note contains emergency keywords (бессознание / тошнота сильная / задыхаюсь / резкая) — show modal:

```
⚠️ Если это срочно — звоните 103 (скорая помощь).

Я не врач и не могу оценить серьёзность.
Лучше перестраховаться.
```

This is the only place where AI suggests specific phone number. Acute emergency case only.

### 10.6 Events emitted on routing

- `wellness.symptom.medical_routing_suggested` (NEW) with `customer_id`, `trigger_reason`, `severity_class`, `triggered_at`
- Customer's response NOT tracked (privacy: their decision to seek care is private)

---

## 11. Insights generation (Phase 2 minimal)

### 11.1 Rule-based insight_text generator

Inputs: events in period.

Rules:
- **< 3 data points**: «Пока недостаточно данных. Отмечайте когда что-то беспокоит.»
- **Most-frequent symptom**: «Чаще всего — {{symptom_type}} ({{N}} раз).»
- **Most-frequent zone**: «Чаще всего в зоне — {{zone}} ({{N}} раз).»
- **Trigger pattern** (Phase 3+): «Когда отмечаете «{{trigger}}» — часто появляется {{symptom_type}}.»
- **Weekday/weekend pattern** (Phase 3+)

### 11.2 Forbidden phrases (auto-reject at insight generator)

- ❌ Medical condition names («артрит», «дерматит», «мигрень», «остеохондроз», «грипп», ...)
- ❌ ICD codes
- ❌ Medication names («ибупрофен», «парацетамол», «аспирин», ...)
- ❌ Supplement names («магний», «витамин Д», ...)
- ❌ «Лечится» / «вылечите» / «диагноз»
- ❌ Anatomy beyond zone labels («позвонки», «мышцы трапеции», ...)
- ❌ Body part diagnostic terms («защемление», «воспаление», ...)
- ❌ «Серьёзно» / «опасно» / «нужно срочно»

### 11.3 Service correlation (Phase 3+)

Out of scope Phase 2. Phase 3+:
- «В неделях с {{service}} — {{symptom}} меньше: {{N1}} vs {{N2}}» (observational)
- NEVER causation claim
- NEVER «продолжайте курс {{service}}»

---

## 12. Privacy enforcement

Same model as Body §11 (high-sensitivity).

### 12.1 API-level
- Customer-only access; 403 on tenant mismatch
- ZERO tenant-side endpoints
- Aggregation (Phase 3+ если когда-нибудь) strips ALL per-customer identifiers + zone identifiers

### 12.2 Master pre-arrival context
NEVER surfaces symptom data per [`master-conversational-templates §5.5`](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface). Master sees what's RELEVANT to the appointment from Service History only.

### 12.3 Logging
- API: event_id + path + outcome (no symptom values)
- Values: TRACE level only
- `note` field: treated as high-sensitivity PII; PII detector flags if forbidden patterns

### 12.4 Retention
Per Q-WI10:
- Soft-delete 30d on revoke
- OP6 cascade
- Data export includes raw symptom history

### 12.5 Founder access
NO direct read in MVP. Legal hold + 4-eye approval for fraud/legal cases.

---

## 13. Wellness Profile integration

### 13.1 Aggregator job

Daily Celery beat:
- Layer 3 Body State:
  - `symptom_event_count_30d`
  - `symptom_top_type_30d`
  - `symptom_top_zone_30d`
- Layer 4 Service History — Phase 3+:
  - `chronic_symptom_correlation_per_service` (observational only)

### 13.2 Cross-correlation (Phase 3+)

Symptom + Mood: «Когда {{symptom}} — настроение часто ниже» observation
Symptom + Sleep: «После плохого сна — {{symptom}} чаще»
Symptom + Services: «В неделях с {{service}} — {{symptom}} меньше»

All Phase 3+, customer-side observation only. NEVER salon-side. NEVER causation framing.

---

## 14. Anti-patterns specific to Symptom Diary

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Symptom-checker style («Может быть X, Y, или Z») | Medical-app / WebMD territory | NEVER lookup. Customer logs reality. |
| Specific medical condition names | Diagnosis | Use generic terms (боль / отёк / усталость) |
| Drug / supplement names | Treatment | NEVER reference |
| Exercise recommendations | Treatment | NEVER recommend |
| Sleep hygiene / diet tips | Out of scope coaching | NEVER recommend (Sleep module also forbids) |
| «Это не страшно» reassurance | We're not doctors | NEVER reassure |
| «Срочно к врачу!» urgent escalation | Customer's decision | «Стоит обратиться» / «Лучше показаться врачу» |
| Specialist-type recommendation («идите к неврологу») | Beyond our scope | «К врачу» generic only |
| Symptom severity grading on medical scale | Medical territory | 1-10 customer subjective only |
| Auto-routing to medical without modal | Bypasses customer agency | Always modal + customer decides |
| «Triple medication check» style features | Drug-app territory | NEVER reference drugs |
| Track children's symptoms | Privacy + legal | Phase 4+ family mode with strict consent |
| Display symptom data to salon side | Privacy violation | NEVER tenant access |
| Show «Здоровых: X%, симптоматических: Y%» tenant stats | Privacy + judgmental | NEVER aggregate display |
| AI calls customer «пациент» | Medical framing | «{{name}}» / «вы» only |
| «Дневник здоровья» / «Health Journal» branding | Medical-app territory | «Симптомы» neutral |
| Severity comparison («это сильнее чем у среднего») | Comparison shame | NEVER cross-customer |
| Suggest specific diagnostic tests («сдайте анализ X») | Diagnostic recommendation | NEVER |
| Predict «возможна простуда» from patterns | Diagnosis | NEVER predict conditions |

---

## 15. Acceptance criteria (engineering checklist)

- [ ] `WellnessSymptomEvent` model with all validators + JSONField for triggers
- [ ] Migration adds table; reuses `WellnessModuleConsent`
- [ ] 6 API endpoints implemented + tested
- [ ] Customer auth required; tenant boundary; 403 on mismatch
- [ ] Activation Path A implemented; Path B + Path C deferred Phase 2.5+
- [ ] Consent dialog UI per §4.1 with EXPLICIT medical disclaimer
- [ ] No reminders (event-driven module; per §4.2)
- [ ] Per-state behavior matrix §7 enforced
- [ ] Mini App Самочувствие → Симптомы section per §6
- [ ] Add symptom screen with all enum dropdowns + intensity slider + multi-select triggers + 500-char note
- [ ] Backdate up to 30 days enforced
- [ ] **Medical-routing protocol §10 implemented** with thresholds + modal + audit
- [ ] Emergency keyword detection in note + 103 modal §10.5
- [ ] Insight generator FORBIDDEN-PHRASE enforcement §11.2 at API level
- [ ] No specialist type recommendations
- [ ] No drug / supplement / medication strings anywhere in codebase
- [ ] Events emitted per §16
- [ ] Privacy enforcement per §12
- [ ] Aggregator writes Wellness Profile Layer 3 §13
- [ ] Anti-spam max 20/day enforced + 429 with medical-routing language
- [ ] Tests: unit (model + validators + insight generator forbidden words + medical-routing thresholds) + API (auth + 429 + medical_routing_suggested response) + integration (consent → log → routing modal → audit) + privacy (no master leak)
- [ ] Anti-pattern review §14 — especially no diagnostic claims + no treatment + emergency case handling
- [ ] Accessibility audit on form + modal + emergency screen (WCAG 2.2 AA)
- [ ] Documentation in `apps/wellness/symptom/README.md` referencing this handoff
- [ ] **Pre-deploy legal sign-off on §10 medical routing protocol + §11.2 forbidden phrases + §4 disclaimer copy**

---

## 16. Events emitted

Per [`event-taxonomy.md §3.6`](../policies/event-taxonomy.md#36-wellness-domain):

| Trigger | Event | Notes |
|---|---|---|
| Consent granted | `wellness.consent.module.granted` | `module_name='symptom'` |
| Consent revoked | `wellness.consent.module.revoked` | `module_name='symptom'` |
| Symptom event saved | `wellness.input.recorded` | `module_name='symptom'`, `input_type=symptom_type`, `confidence=1.0`, `source` |
| Symptom event edited | NEW: `wellness.symptom.event.edited` | audit |
| Symptom event soft-deleted | NEW: `wellness.symptom.event.deleted` | grace start |
| Medical routing suggested | NEW: `wellness.symptom.medical_routing_suggested` | `customer_id`, `trigger_reason`, `severity_class` — audit + analytics for false-positive tuning |
| Pattern detected (Phase 3+) | NEW: `wellness.symptom.pattern_detected` | analytics |
| Aggregator writes profile | `wellness.profile.layer.updated` | `layer_name='layer_3_body_state'` |

Add 4 NEW events to event-taxonomy.md §3.6.

---

## 17. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-WD1** | Anti-spam max 20/day — too restrictive for legitimate chronic-care customer with multiple zones? | 20/day MVP; if support flag triggered repeatedly for chronic-care patient, expand to 50 with manual review trigger | Eng + Policy | 🟡 |
| **Q-WD2** | Symptom type «digestive» — too medical for our scope? | KEEP — common customer concern; same anti-pattern enforcement applies | Policy | 🟡 |
| **Q-WD3** | Symptom type «sleep_related» — overlap with Sleep module? | KEEP — Sleep tracks duration/quality; Symptom tracks issues («бессонница как симптом», «сонливость днём»). Cross-module insights Phase 3+. | UX | 🟢 |
| **Q-WD4** | Backdate window 30 days — too long? | 30d MVP — symptom may have started weeks ago and customer logs retroactively. Longer = data quality concerns | UX | 🟢 |
| **Q-WD5** | Medical-routing modal at intensity=9 single event — too aggressive? | 9/10 is severe-by-definition; offering modal is appropriate. Per §10.4 we suggest, not require. Customer decides. | Policy | 🔴 before first severe-pain log |
| **Q-WD6** | Emergency 103 modal at intensity=10 + acute keywords — false positive concerns? | Acceptable false positive (mention 103 once); false negative = customer harm risk. Better safe. | Legal + Policy | 🔴 before first 10/10 log |
| **Q-WD7** | «Сохранить заметку для врача» PDF export — Phase 2 or Phase 3+? | Phase 3+ — customer can manually screenshot history in MVP | UX | 🟢 |
| **Q-WD8** | Pattern detection thresholds (5+ events / 30d) — tunable per customer? | NOT MVP — fixed thresholds. Customer-tunable Phase 4+ if data shows poor fit. | PM | 🟢 |
| **Q-WD9** | Chronic detection «давно не проходит» NLU on note field — Phase 2 or 2.5? | Phase 2.5+ (NLU work); Phase 2 detects only on count thresholds | Eng + AI | 🟢 |
| **Q-WD10** | Customer marks event as «прошло» / «вылечилось» — track resolution? | YES Phase 3+ — add `resolved_at` field; customer can mark resolved. MVP: events are point-in-time only. | UX + Eng | 🟢 |
| **Q-WD11** | Symptom + Service correlation Phase 3+ — opt-in or auto? | OPT-IN per Q-WB13 / Q-WS6 consistency. Customer enables per service category. Default OFF. | Privacy + UX | 🟡 |
| **Q-WD12** | If customer logs same symptom 10+ times same zone, no medical routing fired (e.g., 4-6 intensity all under threshold) — system response? | Per §10 chronic detection (10+ events same type+zone in 90d) → modal fires regardless of intensity. Volume = signal. | Policy | 🟡 |
| **Q-WD13** | What if customer's note contains drug names («принимаю ибупрофен»)? | Customer-entered note is preserved verbatim. AI never generates drug names. PII detector may flag for awareness; not blocked. | Eng + Policy | 🟡 |
| **Q-WD14** | Multi-symptom event (e.g., headache + nausea same time) — separate entries or composite? | Separate entries — customer logs each individually. Phase 3+ may detect «co-occurring symptoms» observation. | UX | 🟢 |
| **Q-WD15** | Should AI surface symptom history to customer in chat when they next mention symptom? | Phase 2.5+ Path B — AI recognizes recurrence + acknowledges «вы отмечали это {{N}} раз — может быть стоит к врачу?» (per §10) | UX | 🟡 |
| **Q-WD16** | Customer using Bot DM (Phase 2.5+) for symptom log — confidence in NLU vs Mini App manual? | NLU lower confidence; Bot DM requires confirmation step before saving («Записать как «{{type}} ({{zone}})»?»). Manual entry trusted at 100%. | AI + Eng | 🟢 |
| **Q-WD17** | Customer who logs 30+ symptoms per day for weeks (potential mental health signal — anxiety, hypochondria) | Per Q-WD1 + monitoring: flag CSM (NOT customer) at threshold 100 events/30d. CSM may proactively reach out (Phase 4+). MVP: just data. | Policy + CSM | 🔴 before first such pattern (mental-health-adjacent) |
| **Q-WD18** | Symptom + AI Avatar cross-reference (visible skin issues + photo) — Phase 3+? | Phase 3+ as separate cross-module insight. Customer must have both modules active AND explicit cross-consent. | Privacy + UX | 🟢 |
| **Q-WD19** | If insight generator triggers FORBIDDEN-PHRASE rejection at API — silent fail or surface? | LOG + surface generic fallback insight («Записи сохранены. Подробнее в истории.») + alert engineering for vocabulary tuning | Eng + AI | 🟡 |
| **Q-WD20** | Customer revokes Symptom module — wait 30d soft-delete OR immediate hard-delete (more sensitive)? | 30d per Q-WI10 consistency. Customer might revoke accidentally. CSM can hard-delete sooner on customer request. | Privacy | 🟢 |

---

## 18. Cross-document linkage

- [`../policies/wellness-input-modules.md §8`](../policies/wellness-input-modules.md#8-module-7--symptom-diary) — strategic spec ported
- [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md) — sibling pattern source
- [`./2026-05-19-wellness-water-handoff.md`](./2026-05-19-wellness-water-handoff.md) — sibling
- [`./2026-05-19-wellness-body-handoff.md`](./2026-05-19-wellness-body-handoff.md) — privacy + anti-pattern framework
- [`./2026-05-19-wellness-sleep-handoff.md`](./2026-05-19-wellness-sleep-handoff.md) — sibling; medical-adjacent shared concerns
- [`../policies/conversational-ux-framework.md §7.2`](../policies/conversational-ux-framework.md) — medical routing «out of compass» template referenced in §10
- [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — HUMAN_LOCKED gating + medical-routing escalation
- [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md) — no symptom reminders (event-driven)
- [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md) Layer 3 + Layer 4 — aggregator writes
- [`../policies/event-taxonomy.md §3.6`](../policies/event-taxonomy.md#36-wellness-domain) — 4 NEW events §16
- [`../policies/master-conversational-templates.md §5.5`](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface) — privacy boundary
- [`../policies/customer-profile-management-ux.md §4`](../policies/customer-profile-management-ux.md) — activation entry
- [`../policies/customer-first-touch-and-mini-app-states.md`](../policies/customer-first-touch-and-mini-app-states.md) — error/empty states
- [`../policies/tenant-suspension-pause-ux.md`](../policies/tenant-suspension-pause-ux.md) — customer-owned data preserved during PAUSED
- [`../decisions-log.md`](../decisions-log.md) — Q-WI10 retention consistency

---

## 19. What this unblocks

- **`apps/wellness/symptom/` Phase 2 implementation** — model + API + Mini App engineering-ready
- **Chronic-care customer retention** — LTV-critical segment now has tracking
- **Cross-module insights foundation Phase 3+** — symptom feeds into all wellness profile layers
- **Medical-routing protocol** — explicit liability protection
- **Wellness OS positioning** — symptom tracking distinguishes from competitors who don't dare

## 20. What this does NOT unblock

- ❌ Diagnostic claims of any kind (forbidden per §2)
- ❌ Treatment recommendations (forbidden)
- ❌ Drug / supplement references (forbidden)
- ❌ Specialist type recommendations (forbidden)
- ❌ Symptom database / lookup (out of scope)
- ❌ Family / children tracking (Phase 4+)
- ❌ Telehealth integration (out of scope)
- ❌ Tenant-side symptom analytics (privacy)
- ❌ Skip pre-deploy legal sign-off on §10 + §11.2 + §4 disclaimer
- ❌ Q-WD5 / Q-WD6 / Q-WD17 strategic decisions (medical + mental-health-adjacent)

---

## 21. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Wellness backend lead (apps/wellness/symptom/) | ☐ | |
| Mini App frontend (Симптомы section + add form + history + medical-routing modals) | ☐ | |
| AI prompt engineering (insight generator FORBIDDEN-PHRASE enforcement §11.2) | ☐ | |
| **Legal / Compliance (§10 medical routing protocol + §11.2 forbidden phrases + §4 disclaimer + Q-WD5/6/17 emergency policy)** | ☐ | 🔴 PRE-DEPLOY |
| Privacy (Q-WD13 drug-name-in-note handling + Q-WD11 cross-correlation opt-in) | ☐ | |
| Accessibility (WCAG 2.2 AA on form + modal + emergency screen) | ☐ | |
| Policy review (Q-WD17 mental-health-adjacent CSM flag policy) | ☐ | |

## Last verified
2026-05-19 (initial draft, engineering-ready for Phase 2 Wellness Symptom Diary — sibling to Mood + Water + Body + Sleep; most medical-adjacent module with explicit routing protocol)
