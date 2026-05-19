# Customer Wellness Goal Setting — Layer 2 UX

**Date:** 2026-05-19 r1
**Status:** Foundational — Layer 2 of Core Wellness Profile; pulls all 7 modules together
**Reads:** [`core-wellness-profile.md`](./core-wellness-profile.md) §2 (Layer 2 Goals), [`wellness-input-modules.md`](./wellness-input-modules.md), [`customer-wellness-dashboard-ux.md`](./customer-wellness-dashboard-ux.md), [`customer-profile-management-ux.md`](./customer-profile-management-ux.md), [`product-ux-vision.md`](./product-ux-vision.md), [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`assistant-persona.md`](./assistant-persona.md), [`event-taxonomy.md`](./event-taxonomy.md), [`core-user-states.md`](./core-user-states.md), [`../handoffs/2026-05-19-wellness-food-handoff.md`](../handoffs/2026-05-19-wellness-food-handoff.md), [`../handoffs/2026-05-19-wellness-body-handoff.md`](../handoffs/2026-05-19-wellness-body-handoff.md)

> Per `core-wellness-profile.md`, Layer 2 = customer's wellness GOALS. Without goals, the 7 wellness modules are «record-keeping». With goals, modules + observations become «movement toward what matters to you». This is the layer that turns wellness OS from passive tracker into companion.

---

## 0. Why this exists

### 0.1 Strategic context — the missing layer

Per [`product-ux-vision.md`](./product-ux-vision.md): wellness OS promise = «AI knows you». Modules capture WHAT (mood, water, body, sleep, symptoms, photos, food). But without WHY, the AI doesn't know what to recommend, what to surface, what tone to use.

Goals are the WHY:
- «Хочу чувствовать себя бодрее по утрам» → AI prioritizes sleep observations
- «Меньше стресса в плечах» → AI suggests massage when stress signal high
- «Лучше выглядеть к лету» → AI surfaces body + photo progress observations differently
- «Расслабиться по вечерам» → AI suggests evening-friendly services

Without Layer 2:
- Service recommendations feel generic
- Cross-module observations feel disconnected
- AI tone defaults to neutral; can't adapt to customer's frame
- «AI knows you» promise is hollow

### 0.2 The gap

[`core-wellness-profile.md §2`](./core-wellness-profile.md) defines Layer 2 conceptually but doesn't specify:
- Goal taxonomy (what categories of goals)
- Goal templates (what suggested goals exist)
- Setting flow UX
- Display + edit + archival
- Goal progress framing (CRITICAL — NOT diet-app target tracking)
- Cross-module alignment
- Service recommendation tie-in
- Privacy enforcement

### 0.3 The promise

Single source for:
- Goal taxonomy (8 categories + suggested templates per category)
- Mini App goal setting wizard
- Goal display in dashboard + profile
- Goal progress framing rules (observational only — NEVER «осталось X kg»)
- Goal-aware AI tone modulation
- Cross-module suggestion integration
- Service recommendation alignment
- Models + API + events

---

## 1. Scope

### IN
- New model `WellnessGoal` (1+ per customer; optional)
- 8 goal categories with 3-5 suggested templates each
- Custom free-text goals
- Mini App «Цели» section in «Самочувствие» tab (when customer has goals)
- Goal setting wizard (entry from dashboard, profile, or onboarding)
- Goal display: active goals + archived
- Goal edit + archive flow
- Goal progress framing (observational; NOT diet-app target tracking)
- Goal-aware service recommendations (Phase 3+; foundation in Phase 2)
- Goal-aware AI tone (use customer's goal context in proactive messages)
- Privacy enforcement (customer-only at API)
- Wellness Profile Layer 2 integration
- 4 NEW events for event-taxonomy

### OUT
- Diet-app goals («похудеть на N кг к дате») — FORBIDDEN per §2
- Medical goals («вылечить тревогу») — out of scope; route to specialist
- Habit prescriptions («больше пить воды») — that's a HABIT, not a goal
- Public sharing / accountability features (privacy + scope creep)
- Goal coaching / step-by-step plans («неделя 1: ..., неделя 2: ...») — coaching scope
- Streaks tied to goals («20 дней работы над целью!») — anti-OCD
- Reminder push for goals («не забывайте про цель») — pressure
- Multi-customer goals («наш с подругой челлендж») — Phase 4+ family/social if ever
- Specialist referrals from goals (separate scope)
- Goal-tied monetization tier (Phase 3+ if customer-pays explored)

---

## 2. Strategic constraints — non-negotiable

### 2.1 NOT diet-app goals
Per [`wellness-food-handoff.md §2.1`](../handoffs/2026-05-19-wellness-food-handoff.md): NEVER «похудеть на N кг к {date}». Same here:
- NEVER weight targets
- NEVER body size targets
- NEVER calorie deficit goals
- NEVER measurement specific targets («талия 65»)

Outcome-oriented OK: «лучше выглядеть в открытой одежде», «уверенно чувствовать в купальнике летом». Note difference: **how customer wants to FEEL**, not what **scale reads**.

### 2.2 NOT medical goals
Per [`wellness-symptom-handoff.md §2`](../handoffs/2026-05-19-wellness-symptom-handoff.md): NEVER diagnose / treat. Same here:
- NEVER «вылечить бессонницу» — route to medical
- NEVER «избавиться от хронической боли» — route to medical
- NEVER «нормализовать гормональный фон» — medical territory

Wellness-adjacent OK: «лучше отдыхать», «меньше напряжения в шее», «больше энергии днём». Note: customer's subjective wellbeing, not condition treatment.

### 2.3 NOT habit prescriptions
- «Пить 2 л воды в день» = habit (use Water module)
- «Высыпаться» = habit (use Sleep module)
- «Регулярно ходить на массаж» = habit (use booking)

Goals are OUTCOMES. Habits SUPPORT goals. Don't confuse.

### 2.4 NO streaks tied to goals
Per anti-OCD principle across all modules. NEVER «20 дней работы над целью» / «дни без срыва».

### 2.5 NO pushy reminders
- Goals are reference, not push-engine
- AI uses goal context to inform tone/recommendation
- NEVER «не забывайте про цель!»
- NEVER «вы давно не работали над {{goal}}»

### 2.6 Customer autonomy absolute
- Customer can have 0 goals (optional always)
- Customer can have 1-5 goals (cap at 5 to avoid overload)
- Customer can edit / archive any time
- AI doesn't push goals OR push goal-progress

### 2.7 Privacy hierarchy
- Customer-only
- NEVER salon side
- Service recommendations DO consume goals BUT recommendation logic returns generic «could fit your goal» framing without exposing goal text to salon

---

## 3. Goal taxonomy

### 3.1 Eight categories

| # | Category | Examples (3-5 templates) | AI use |
|---|---|---|---|
| 1 | **Самочувствие / энергия** | «Больше энергии днём», «Меньше усталости», «Бодрее по утрам» | Sleep + Mood module observation framing |
| 2 | **Расслабление / стресс** | «Меньше напряжения в шее», «Расслабляться вечером», «Меньше стресса в будни» | Mood stress + service rec (massage / spa) |
| 3 | **Внешний вид / красота** | «Здоровое сияние кожи», «Лучше выглядеть к лету», «Уверенно в открытой одежде» | Body + Avatar observation; cosmetology service rec |
| 4 | **Тело / форма** | «Подтянутая талия», «Чувствовать себя в форме», «Меньше отёчности» | Body observation (without weight targets); lymphatic / body-shaping service rec |
| 5 | **Сон / восстановление** | «Лучше спать», «Просыпаться отдохнувшей», «Высыпаться по выходным» | Sleep observation; relaxation service rec |
| 6 | **Состояние волос / кожи** | «Густые здоровые волосы», «Чистая кожа», «Гладкие руки/ноги» | Photo observation; hair/skincare service rec |
| 7 | **Уход за собой как ритуал** | «Регулярный уход за собой», «Время на себя», «Привычка заботиться» | Booking pattern + Mood + retention framing |
| 8 | **Особый случай / событие** | «К свадьбе подруги», «К отпуску в июне», «К фотосессии» | Time-bound goal with optional target date; pre-event prep service rec |

### 3.2 Custom (free-text) goals

If customer's goal doesn't fit suggested templates → free-text «своя цель». Stored as `category='custom'`. AI uses LLM-based mapping internally to inform service rec without exposing customer's text in tenant-facing logs.

### 3.3 Forbidden goal categories

Templates explicitly NOT offered (would imply medical / diet-app territory):
- ❌ «Похудеть на N кг» / weight targets
- ❌ «Снять диагноз X»
- ❌ «Принимать витамин Y»
- ❌ «Соблюдать диету»

If customer types free-text matching forbidden patterns:
- Soft redirect: «Я не диетолог и не врач. Можем переформулировать как «{{outcome-oriented suggestion}}»?»
- Examples:
  - «Похудеть на 10 кг» → suggest «Чувствовать себя в форме» / «Подтянутая талия»
  - «Вылечить мигрень» → suggest «Меньше головной боли» + route to medical
  - «Соблюдать кето» → polite decline «Я не помогаю с диетами, это лучше с врачом-диетологом»

---

## 4. Goal setting wizard

### 4.1 Entry points

Customer can set goals from:
- **Dashboard «Самочувствие» tab** — if 0 goals + 3+ modules active → invitation card §4.5
- **Profile → Самочувствие → Цели** — direct access
- **First-touch onboarding** — Phase 3+ optional post-first-visit nudge
- **Bot DM «у меня цель — выглядеть хорошо»** — Phase 3+ NLU triggers wizard

Phase 2 MVP: entry points 1 + 2 only.

### 4.2 Wizard step 1 — Category selection

```
┌────────────────────────────────────────┐
│ ← Какие у вас цели по самочувствию?    │
├────────────────────────────────────────┤
│ Выберите 1-3 направления (можно        │
│ добавить ещё потом).                   │
│                                        │
│ ☐ ⚡ Энергия и самочувствие             │
│ ☐ 🧘 Расслабление, меньше стресса      │
│ ☐ ✨ Внешний вид и красота             │
│ ☐ 💪 Тело и форма                      │
│ ☐ 😴 Сон и восстановление              │
│ ☐ 💁 Волосы и кожа                     │
│ ☐ 🌸 Уход за собой как ритуал          │
│ ☐ 🎉 К особому событию                  │
│                                        │
│ ── Или ──                              │
│                                        │
│ ☐ Своя формулировка                    │
│                                        │
│ Выбрано: 0 из 5                        │
│ [Дальше]                               │
└────────────────────────────────────────┘
```

### 4.3 Wizard step 2 — Template selection (per chosen category)

For each chosen category, show 3-5 suggested templates + «Своя формулировка»:

```
┌────────────────────────────────────────┐
│ ← 🧘 Расслабление                       │
├────────────────────────────────────────┤
│ Что подходит?                          │
│                                        │
│ ◯ Меньше напряжения в шее              │
│ ◯ Расслабляться вечером                │
│ ◯ Меньше стресса в будни               │
│ ◯ Лучше переключаться с работы         │
│                                        │
│ ── Или своими словами ──               │
│ [_____________________________]        │
│                                        │
│ [Назад]   [Дальше]                     │
└────────────────────────────────────────┘
```

### 4.4 Wizard step 3 — Optional target date (only for «Особый случай»)

For category «🎉 К особому событию»:

```
┌────────────────────────────────────────┐
│ ← К особому событию                     │
├────────────────────────────────────────┤
│ Что и когда?                           │
│                                        │
│ Что: [_____________________________]   │
│ Например: «свадьба подруги», «отпуск»  │
│                                        │
│ Когда: [10 июня 2026 ▾]                │
│                                        │
│ Любая дата OK. Я не буду подгонять —   │
│ просто буду помнить контекст.          │
│                                        │
│ [Назад]   [Дальше]                     │
└────────────────────────────────────────┘
```

For other categories: no target date (goals are open-ended outcomes).

### 4.5 Wizard step 4 — Confirmation

```
┌────────────────────────────────────────┐
│ ← Готово?                               │
├────────────────────────────────────────┤
│ Ваши цели:                             │
│                                        │
│ 1. ⚡ Больше энергии днём               │
│ 2. 🧘 Меньше напряжения в шее          │
│ 3. ✨ Лучше выглядеть к лету           │
│                                        │
│ Я буду помнить — без напоминаний и     │
│ давления.                              │
│                                        │
│ Можно изменить или убрать в любой      │
│ момент.                                │
│                                        │
│ [Назад]   [Готово]                     │
└────────────────────────────────────────┘
```

### 4.6 Dashboard invitation card

When customer has 0 goals + 3+ modules active for 7+ days:

```
┌────────────────────────────────────────┐
│ 🎯 Цели по самочувствию                 │
├────────────────────────────────────────┤
│ Я заметила что вы отмечаете несколько  │
│ модулей. Хотите задать цели — что для │
│ вас важно?                             │
│                                        │
│ Помогу подбирать процедуры и           │
│ показывать прогресс по тому что вам    │
│ ценно.                                 │
│                                        │
│ [Задать цели]   [Не сейчас]             │
└────────────────────────────────────────┘
```

«Не сейчас» dismisses for 30 days. After 30 days re-offer (one more time max). After second dismissal, never re-offer.

---

## 5. Goal display

### 5.1 «Цели» section in Самочувствие tab

When customer has ≥ 1 goal:

```
┌────────────────────────────────────────┐
│ 🎯 Ваши цели                            │
├────────────────────────────────────────┤
│ 1. ⚡ Больше энергии днём               │
│    Поставлена: 2 недели назад           │
│    Связанные модули: Сон, Настроение    │
│    [Подробнее]   [Изменить]            │
│                                        │
│ 2. 🧘 Меньше напряжения в шее          │
│    Поставлена: 1 месяц назад            │
│    Связанные модули: Симптомы           │
│    [Подробнее]   [Изменить]            │
│                                        │
│ 3. 🎉 К свадьбе 10 июня                 │
│    Поставлена: 3 недели назад           │
│    Осталось: 24 дня                     │
│    [Подробнее]   [Изменить]            │
│                                        │
│ [+ Добавить цель]                      │
│ [Архив (2)]                            │
└────────────────────────────────────────┘
```

### 5.2 Goal detail screen

```
┌────────────────────────────────────────┐
│ ← Больше энергии днём                   │
├────────────────────────────────────────┤
│ Категория: ⚡ Энергия и самочувствие     │
│ Поставлена: 5 мая 2026 (2 недели назад)│
│                                        │
│ ── Что отслеживается ──                │
│                                        │
│ 😴 Сон — длительность и качество       │
│ 🙂 Настроение — энергия                │
│                                        │
│ ── Что заметно по этой цели ──         │
│                                        │
│ За 2 недели:                           │
│ • Сон в среднем 7.2ч (норма 7-9)       │
│ • Энергия по будням ★3.5, выходные ★4.5│
│                                        │
│ Помощник заметил: бодрее когда         │
│ спите 7.5+ часов.                       │
│                                        │
│ ── Что может помочь (по вашей цели) ── │
│                                        │
│ Услуги студии для расслабления и сна:  │
│ 💆 Расслабляющий массаж                │
│ 💆 Лимфодренаж                          │
│ [Посмотреть свободное время]            │
│                                        │
│ ── Действия ──                          │
│ [Изменить цель]                         │
│ [Архивировать]                          │
└────────────────────────────────────────┘
```

### 5.3 Goal progress framing — CRITICAL anti-pattern guard

NEVER:
- ❌ «Прогресс: 67%» — implies measurable target, anti-pattern §2.1
- ❌ «Осталось N kg / часов / etc.» — diet-app
- ❌ Progress bar — implies linear completion
- ❌ «До цели X дней» (only for §3.1 category 8 «event» with explicit date)

ALWAYS:
- ✅ «Поставлена: N дней/недель назад»
- ✅ «Связанные модули: ...»
- ✅ «Что заметно по этой цели за период:» observational
- ✅ Service recommendations framed as «может помочь»

### 5.4 Goal-aware AI tone

When AI surfaces observations / recommendations from goals:
- Bot DM tone reflects goal context («помню что для вас важно расслабление...»)
- Service recommendations referenced as «по вашей цели X — может помочь»
- Mini App home (Главная state-adaptive) shows goal-aligned service first

### 5.5 Multi-goal display

If 3+ goals, show abbreviated cards. Tap to expand. Don't crowd the section.

---

## 6. Goal edit + archive

### 6.1 Edit goal

Tap «Изменить» on goal card → opens wizard step 2-4 pre-populated. Customer can:
- Reformulate text
- Change category
- Adjust target date (for event goals)
- Add/remove linked modules (Phase 3+)

### 6.2 Archive goal

Tap «Архивировать» → confirmation:

```
┌────────────────────────────────────────┐
│ Архивировать цель?                     │
├────────────────────────────────────────┤
│ «{{goal_text}}»                        │
│                                        │
│ Цель уйдёт из активных. История        │
│ наблюдений по ней сохранится.          │
│                                        │
│ Можно вернуть из архива в любой        │
│ момент.                                │
│                                        │
│ Что отметить?                          │
│ ⦿ Цель достигнута (или почти)          │
│ ◯ Уже не актуально                     │
│ ◯ Просто убираю                        │
│                                        │
│ [Отмена]   [Архивировать]              │
└────────────────────────────────────────┘
```

Reason captured for analytics (Phase 3+ aggregate to understand goal lifecycle).

### 6.3 Restore from archive

In «Архив» view:

```
┌────────────────────────────────────────┐
│ ← Архив целей                           │
├────────────────────────────────────────┤
│ 🎯 «Лучше выглядеть к зиме»             │
│    Поставлена: 6 мес назад              │
│    Архивирована: 2 мес назад            │
│    Причина: достигнута                  │
│    [Вернуть в активные]                │
│                                        │
│ 🎯 «Меньше стресса перед экзаменами»    │
│    Поставлена: 1 год назад              │
│    Архивирована: 9 мес назад            │
│    Причина: не актуально                │
│    [Вернуть в активные]                │
└────────────────────────────────────────┘
```

### 6.4 Active goals limit

Max 5 active goals. If customer tries to add 6th → soft warning:
```
У вас уже 5 активных целей. Архивируйте одну или измените существующую.

[К активным]   [Архивировать самую старую]
```

NOT a hard block. If customer pushes, allow up to 7 (with warning). Above 7 hard limit (cognitive overload protection).

---

## 7. Cross-module integration

### 7.1 Module linkage per category

| Goal category | Primary modules | Secondary modules |
|---|---|---|
| Энергия | Sleep, Mood | Water, Body |
| Расслабление | Mood, Symptom | Sleep, Avatar |
| Внешний вид | Avatar, Body | Skin tracking (via Symptom) |
| Тело / форма | Body, Avatar | Symptom (swelling) |
| Сон | Sleep | Mood, Symptom |
| Волосы / кожа | Avatar, Symptom | Mood (stress impact) |
| Уход как ритуал | (booking patterns) | Mood, Body |
| Особый случай | Avatar, Body | Sleep, Mood (event prep) |

### 7.2 Goal triggers module discovery

If customer sets goal «Лучше спать» but Sleep module NOT active:

```
┌────────────────────────────────────────┐
│ Подключите модуль для этой цели?       │
├────────────────────────────────────────┤
│ Цель «Лучше спать» лучше отслеживается │
│ через модуль «Сон» — длительность и    │
│ качество.                              │
│                                        │
│ Хотите подключить?                     │
│                                        │
│ [Да, подключить]   [Не сейчас]         │
└────────────────────────────────────────┘
```

«Не сейчас» — goal saved without module. AI uses other signals (booking patterns, conversation cues).

### 7.3 Observation generation aware of goals

Per [`customer-wellness-dashboard-ux.md §5.2`](./customer-wellness-dashboard-ux.md) cross-module observation generator — extends to use goals:
- Priority observations aligned with active goals
- Frame observations in goal context («по вашей цели X — заметила что...»)

### 7.4 Service recommendation flow

Per [`product-ux-vision.md §1`](./product-ux-vision.md): «Service recommendations are decisions outputs, not entry points.» Goals are decision input.

Service recommendation algorithm (Phase 3+):
1. Read customer's active goals
2. Match goal category → relevant service category (per tenant's catalog)
3. Surface 1-3 services per goal in:
   - Goal detail screen §5.2
   - Mini App home state-adaptive section
   - Bot DM proactive nudges (rare, contextual)
4. NEVER aggressive cross-sell

### 7.5 Anti-pattern: forcing goal-service match

If tenant's catalog has NO service matching customer's goal — DO NOT fabricate or stretch. Show «У студии нет процедуры под эту цель напрямую. Может вы узнаете у мастера какие из существующих подходят» rather than recommend wrong service.

---

## 8. Goal-aware AI tone modulation

### 8.1 Tone affects which observations bubble up

Per [`conversational-ux-framework.md`](./conversational-ux-framework.md) state-tone matrix — goal context layers on top:
- Customer in state ACTIVE_REGULAR + goal «расслабление» → AI tone slightly softer
- Customer state PROBLEM_SEEKING + goal «энергия» → AI surfaces sleep-related observation first

### 8.2 Goal context in proactive messages

When AI initiates message:
- Reference goal IF relevant («помню, для вас важно X — заметила Y»)
- NEVER reference goal if customer hasn't engaged with goal section recently (don't seem stalker-y)
- Maximum 1-in-3 proactive messages references goal explicitly

### 8.3 Goal in cross-module observations

Per dashboard §5.2 — observation text can include goal context:
- «По вашей цели «больше энергии» — заметила, что бодрее в дни с 7.5+ часов сна»
- NOT «Вы делаете прогресс по цели X» (judgmental)
- NOT «Чтобы достичь цели X — спите дольше» (prescriptive)

---

## 9. Privacy enforcement

### 9.1 API-level
- Customer-only access; 403 on tenant mismatch
- Custom goal text treated as PII sensitive (could contain personal info)
- Goal text NEVER surfaces to salon side

### 9.2 Master pre-arrival context
Master sees NO goal data. Service recommendation aligned with goal happens server-side; master sees the booking customer made, not the goal that drove it.

### 9.3 Tenant-side
NEVER. Even aggregated «N% have weight-management goals» — privacy violation in Phase 2. Phase 4+ opt-in only.

### 9.4 Founder access
NO direct read in MVP. Legal hold + 4-eye approval per AI Avatar / Body precedent.

### 9.5 Logging
- API: event_id + path + outcome (no goal text)
- Goal text: TRACE only
- PII detector treats goal_text as moderate sensitivity

### 9.6 Retention
- Active goals retained while customer has account
- Archived goals retained 1 year then anonymized aggregate
- Customer can hard-delete archived goal at any time

---

## 10. Data model

### 10.1 `WellnessGoal`

```python
class WellnessGoal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='wellness_goals')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    CATEGORY_CHOICES = [
        ('energy', 'Энергия и самочувствие'),
        ('relaxation', 'Расслабление, меньше стресса'),
        ('appearance', 'Внешний вид и красота'),
        ('body_shape', 'Тело и форма'),
        ('sleep', 'Сон и восстановление'),
        ('hair_skin', 'Волосы и кожа'),
        ('self_care_ritual', 'Уход за собой как ритуал'),
        ('event_special', 'К особому событию'),
        ('custom', 'Своя формулировка'),
    ]
    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES)

    TEMPLATE_CHOICES = [
        # Energy templates
        ('energy_more_day', 'Больше энергии днём'),
        ('energy_less_fatigue', 'Меньше усталости'),
        ('energy_morning_vigor', 'Бодрее по утрам'),
        # Relaxation templates
        ('relax_neck_tension', 'Меньше напряжения в шее'),
        ('relax_evening', 'Расслабляться вечером'),
        ('relax_workday_stress', 'Меньше стресса в будни'),
        ('relax_disconnect_work', 'Лучше переключаться с работы'),
        # Appearance templates
        ('appearance_skin_glow', 'Здоровое сияние кожи'),
        ('appearance_summer_ready', 'Лучше выглядеть к лету'),
        ('appearance_open_clothes', 'Уверенно в открытой одежде'),
        # Body templates
        ('body_waist_toned', 'Подтянутая талия'),
        ('body_feel_fit', 'Чувствовать себя в форме'),
        ('body_less_swelling', 'Меньше отёчности'),
        # Sleep templates
        ('sleep_better', 'Лучше спать'),
        ('sleep_rested_wake', 'Просыпаться отдохнувшей'),
        ('sleep_weekend_recovery', 'Высыпаться по выходным'),
        # Hair/skin templates
        ('hair_thick_healthy', 'Густые здоровые волосы'),
        ('skin_clear', 'Чистая кожа'),
        ('skin_smooth', 'Гладкие руки/ноги'),
        # Ritual templates
        ('ritual_regular_care', 'Регулярный уход за собой'),
        ('ritual_time_for_self', 'Время на себя'),
        ('ritual_caring_habit', 'Привычка заботиться'),
        # Custom
        ('custom', 'Своя формулировка'),
    ]
    template = models.CharField(max_length=64, choices=TEMPLATE_CHOICES)
    # 'custom' if free-text

    text = models.TextField(max_length=200)
    # Customer's chosen text (template value OR custom text)

    # For event-special category only
    event_what = models.CharField(max_length=200, blank=True, default='')
    target_date = models.DateField(null=True, blank=True)

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('archived_achieved', 'Archived — achieved'),
        ('archived_irrelevant', 'Archived — no longer relevant'),
        ('archived_neutral', 'Archived — just removing'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='active')

    set_at = models.DateTimeField()
    archived_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    # Soft-delete after archive

    # For analytics
    sequence_number = models.IntegerField()
    # Customer's Nth goal ever set (helps with onboarding analytics)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            Index(fields=['customer', 'status', '-set_at']),
            Index(fields=['tenant', 'category']),  # Phase 3+ aggregate (anonymized)
        ]
        constraints = [
            CheckConstraint(
                check=Q(category='event_special') | Q(target_date__isnull=True),
                name='ck_target_date_only_for_event',
            ),
        ]
```

### 10.2 Active goals limit (enforced at API)

API rejects POST when customer has 7+ active goals:
```json
{
  "error": "active_goals_limit_exceeded",
  "message": "У вас уже {{count}} активных целей. Архивируйте или измените существующую.",
  "max_active": 7,
  "current_active": 7
}
```

UI warns at 5 (soft), enforces at 7 (hard).

---

## 11. API contracts

### 11.1 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/customer/wellness/goals` | Customer | Create new goal |
| GET | `/api/v1/customer/wellness/goals` | Customer | List goals (filter status / category) |
| PATCH | `/api/v1/customer/wellness/goals/<id>` | Customer | Edit goal |
| POST | `/api/v1/customer/wellness/goals/<id>/archive` | Customer | Archive (with reason) |
| POST | `/api/v1/customer/wellness/goals/<id>/restore` | Customer | Restore from archive |
| DELETE | `/api/v1/customer/wellness/goals/<id>` | Customer | Hard-delete (only archived) |
| GET | `/api/v1/customer/wellness/goals/<id>/observations` | Customer | Goal-related observations from dashboard observation cache |

### 11.2 POST `/api/v1/customer/wellness/goals`

**Request**:
```json
{
  "category": "energy",
  "template": "energy_more_day",
  "text": "Больше энергии днём",
  "event_what": null,
  "target_date": null
}
```

For custom:
```json
{
  "category": "custom",
  "template": "custom",
  "text": "Чувствовать себя свободнее в одежде"
}
```

**Validation**:
- Customer has Wellness Profile activated (any module active OR explicit goal-only path)
- `category` in choices
- `template` matches category (or 'custom')
- `text` non-empty, ≤ 200 chars
- For event_special: `target_date` required, `event_what` required
- For other categories: target_date REJECTED (per CheckConstraint)
- Active goals count < 7 (per §10.2)
- Forbidden-pattern detection on `text`: if matches diet-app / medical patterns → 400 with suggestion

**Response** (201):
```json
{
  "id": "uuid",
  "category": "energy",
  "template": "energy_more_day",
  "text": "Больше энергии днём",
  "status": "active",
  "set_at": "2026-05-19T14:30:00Z",
  "sequence_number": 1,
  "linked_modules": ["sleep", "mood"]
}
```

### 11.3 GET `/api/v1/customer/wellness/goals`

**Query**: `status` (default 'active'), `category`, `limit` (default 10, max 50)

**Response** (200):
```json
{
  "goals": [
    {
      "id": "uuid",
      "category": "energy",
      "text": "Больше энергии днём",
      "status": "active",
      "set_at": "2026-05-05T14:30:00Z",
      "linked_modules": ["sleep", "mood"],
      "active_observations_count": 3
    },
    ...
  ],
  "active_count": 3,
  "archived_count": 2,
  "limit_warning": false
}
```

### 11.4 POST `/api/v1/customer/wellness/goals/<id>/archive`

**Request**:
```json
{
  "reason": "achieved"  // or "irrelevant" or "neutral"
}
```

### 11.5 GET `/api/v1/customer/wellness/goals/<id>/observations`

Returns dashboard observations filtered by goal's linked_modules.

**Response** (200):
```json
{
  "goal_id": "uuid",
  "goal_text": "Больше энергии днём",
  "observations": [
    {
      "id": "obs_xyz",
      "text": "Бодрее в дни с 7.5+ часов сна",
      "source_modules": ["sleep"],
      "period_days": 30,
      "generated_at": "..."
    },
    ...
  ],
  "service_recommendations": [
    {
      "service_id": "svc_abc",
      "service_name": "Расслабляющий массаж",
      "reason": "По вашей цели — может помочь расслаблению"
    },
    ...
  ]
}
```

---

## 12. Events emitted

Per [`event-taxonomy.md §3.6`](./event-taxonomy.md#36-wellness-domain):

| Trigger | Event | Notes |
|---|---|---|
| Goal created | NEW: `wellness.goal.created` | `category`, `template`, `is_custom_text`, `sequence_number` |
| Goal edited | NEW: `wellness.goal.edited` | audit (don't log new text in event; reference id) |
| Goal archived | NEW: `wellness.goal.archived` | `reason` (achieved/irrelevant/neutral) |
| Goal restored | NEW: `wellness.goal.restored` | |
| Goal hard-deleted | NEW: `wellness.goal.deleted` | |
| Forbidden-pattern in text rejected | NEW: `wellness.goal.forbidden_pattern_rejected` | analytics for taxonomy tuning + anti-pattern guard |

Add 5 NEW events to event-taxonomy.md §3.6.

---

## 13. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| «Похудеть на N кг» template | Diet-app territory §2.1 | Outcome-oriented templates only |
| «Вылечить депрессию» template | Medical territory §2.2 | Route to specialist |
| Progress bar / percent complete | Implies measurable target | Observational «что заметно» only |
| Streak counter («3 недели работаем над целью!») | Anti-OCD | NEVER streaks |
| Push reminders «не забывайте про цель» | Pressure | Goals are reference, AI doesn't push |
| Auto-generate goals from data | Removes customer agency | Customer-set always |
| Customer-pays gating on goals | Wellness OS positioning | NEVER gate; foundational |
| Tenant sees customer's goals | Privacy | NEVER |
| Force goal at first-touch | Friction | Customer-initiated; can have 0 goals forever |
| Goal-aware tone overriding customer's request | Manipulative | Customer's immediate request always wins |
| «You're not making progress on goal X» framing | Shame | NEVER assess «progress» judgmentally |
| Cross-customer goal comparison («другие достигли...») | Privacy + shame | NEVER comparative |
| Service recommendation that doesn't match tenant catalog | Frustrating | Honest «нет подходящей процедуры — спросите мастера» |
| Multiple goals competing for «top recommendation» | Confusion | One service per goal per surface; alternate per visit |
| Allow forbidden-pattern text without redirect | Quality slip | Soft redirect with suggestion |
| Goal coaching / step-by-step plans | Coaching scope | NEVER plan generation |
| Public goal sharing | Privacy + scope | NEVER MVP; Phase 4+ family/social if ever |

---

## 14. Acceptance criteria (engineering checklist)

- [ ] `WellnessGoal` model with CheckConstraint per §10.1
- [ ] Migration adds table
- [ ] 7 API endpoints implemented + tested
- [ ] Customer auth required; tenant boundary; 403 on mismatch
- [ ] Active goals limit: warn at 5 soft, hard 7 §6.4
- [ ] Wizard UI with 4 steps §4
- [ ] Goal display section in Самочувствие §5.1
- [ ] Goal detail screen §5.2 with observation pull from dashboard cache
- [ ] Goal edit + archive + restore + hard-delete flows §6
- [ ] Cross-module module-discovery prompt when goal lacks linked module §7.2
- [ ] Forbidden-pattern detection at API §11.2 with soft redirect suggestion
- [ ] Goal-aware AI tone hooks (conversational-ux-framework consumes goal context)
- [ ] Service recommendation foundation §7.4 (Phase 2 stub; Phase 3+ activation)
- [ ] Events emitted §12
- [ ] Privacy enforcement §9 (customer-only at API; PII rules on text)
- [ ] Wellness Profile Layer 2 integration
- [ ] Tests: model + API + active-limit enforcement + forbidden-pattern rejection + cross-tenant denial + archive flow + restore flow
- [ ] Anti-pattern review §13 — especially no progress bars / streaks / diet templates
- [ ] Accessibility audit on wizard + display (WCAG 2.2 AA)
- [ ] Documentation in `apps/wellness/goals/README.md` referencing this handoff

---

## 15. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-WG1** | Max active goals — 5 soft / 7 hard or stricter? | 5 / 7 MVP per §6.4 (cognitive overload research suggests 3-7 simultaneous goals). Revisit at heavy users. | UX | 🟢 |
| **Q-WG2** | Custom free-text goals — accept all length or strict 200 char? | 200 char MVP (matches mood notes etc.); customer can use longer in linked module notes if needed | UX | 🟢 |
| **Q-WG3** | Forbidden-pattern detection — list of regex / NLU model? | Regex MVP for obvious patterns («похудеть на», «вылечить», «диагноз», «диета X», «таблетки»); NLU Phase 4+ for nuanced cases | AI + Eng | 🟡 |
| **Q-WG4** | If customer adds same goal twice (e.g., «расслабиться» twice) — block? | Soft warn «у вас уже есть похожая цель X — изменить её?» but allow if customer confirms | UX | 🟢 |
| **Q-WG5** | Goal templates per category — exact list of 3-5 each? | §3.1 provides examples. Refine after first 50 customers use Phase 2; Q-WG3 helps detect missing patterns. | UX + PM | 🟡 |
| **Q-WG6** | Event-special target date — past date allowed? | NO — must be future. If past, suggest archiving as «irrelevant». | UX | 🟢 |
| **Q-WG7** | Goal-aware tone modulation — bot DM or only Mini App copy? | Both — but rate-limited per §8.2 (max 1-in-3 proactive messages reference goal) | UX | 🟡 |
| **Q-WG8** | Customer asks «как мне достичь цели X?» in DM — AI response? | Route to «у меня нет планов на достижение целей. Я помогаю замечать паттерны. Может быть это вам поможет: {{relevant observation if any}}». NEVER coaching plan. | Policy + AI | 🔴 before first such request |
| **Q-WG9** | When customer archives goal as «achieved» — celebrate? | Soft acknowledgment «отлично что получилось». NO confetti / streaks / public sharing prompts. | UX | 🟢 |
| **Q-WG10** | Multi-tenant customer Q-CO5 — goals per tenant or unified? | Per-tenant per Q-CO5 boundary. Customer maintains separate goals if they want at different salons. | Privacy | 🟢 |
| **Q-WG11** | Goal expiration — auto-archive if no linked-module activity for 90 days? | NO — customer's choice always. AI can prompt «давно не отслеживали — актуально ещё?» at 90d. | Policy | 🟢 |
| **Q-WG12** | Service recommendation — fixed mapping or dynamic AI? | Phase 2 fixed mapping (category → service categories from tenant catalog). Phase 3+ dynamic per customer context | Eng + PM | 🟡 |
| **Q-WG13** | If tenant has no services matching customer's goal — show empty section? | YES per §7.5; transparent «нет подходящей процедуры». NEVER fabricate. | UX | 🟢 |
| **Q-WG14** | Goal sharing with master — when customer books for procedure aligned with goal? | NO — master sees procedure booked; doesn't see goal motivation. Privacy boundary. | Privacy | 🟢 |
| **Q-WG15** | Wellness Profile Layer 2 — overrides single goal field with array? | Array (matches multi-goal capability). Single primary goal flag Phase 3+ if useful. | Eng | 🟡 |
| **Q-WG16** | Goal-related observations — how many in goal detail screen? | Max 3-5 most recent per goal; «Все наблюдения по цели» link for full list | UX | 🟢 |
| **Q-WG17** | If customer's tenant goes SUSPENDED — goals visible read-only? | Per [`tenant-suspension-pause-ux §11.2`](./tenant-suspension-pause-ux.md) — customer-owned wellness data preserved; goals visible read-only | Eng | 🟢 |
| **Q-WG18** | When customer activates 1st wellness module — prompt to set goal? | NO — let customer use modules first. Dashboard invitation card §4.6 kicks in after 7d + 3 modules. Avoid friction early. | UX | 🟢 |
| **Q-WG19** | Goal text in cross-module observation insight — verbatim or paraphrased? | Verbatim customer's text in their own dashboard; never in salon-side logs | Privacy + UX | 🟡 |
| **Q-WG20** | If customer tries to set forbidden-pattern goal 3 times in a row — escalate? | Polite escalation: «Я заметила, что цели в этой формулировке не подходят моей логике. Это не работа AI — может быть, диетолог / врач лучше поможет?» Then stop offering alternative | Policy + AI | 🟡 |

---

## 16. Cross-document linkage

- [`core-wellness-profile.md §2`](./core-wellness-profile.md) — Layer 2 foundational
- [`wellness-input-modules.md`](./wellness-input-modules.md) — 7 modules feed goal context
- [`customer-wellness-dashboard-ux.md §5`](./customer-wellness-dashboard-ux.md) — observation generator consumes goals
- [`product-ux-vision.md §1`](./product-ux-vision.md) — wellness OS «AI knows you» promise this completes
- [`conversational-ux-framework.md`](./conversational-ux-framework.md) — voice anchors; goal-aware tone modulation §8
- [`assistant-persona.md`](./assistant-persona.md) — persona constraints stack
- [`customer-profile-management-ux.md §4`](./customer-profile-management-ux.md) — Профиль → Самочувствие entry; goal section accessible from here
- [`event-taxonomy.md §3.6`](./event-taxonomy.md#36-wellness-domain) — 5 NEW events §12
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) — SUSPENDED state preserves goals read-only
- [`wellness-body-handoff.md §2`](../handoffs/2026-05-19-wellness-body-handoff.md) — anti-diet-app principle stacks
- [`wellness-food-handoff.md §2.1`](../handoffs/2026-05-19-wellness-food-handoff.md) — anti-diet-app strict
- [`wellness-symptom-handoff.md §2`](../handoffs/2026-05-19-wellness-symptom-handoff.md) — anti-medical principle stacks
- [`master-conversational-templates.md §5.5`](./master-conversational-templates.md#55-customer-pre-arrival-context-surface) — privacy boundary master-side
- [`../decisions-log.md`](../decisions-log.md) — Q-CO5 tenant separation

---

## 17. What this unblocks

- **Wellness OS «AI knows you» promise rendered fully** — modules + goals + observations + service rec all aligned
- **Service recommendation engine** — Phase 3+ has the input it needs (customer's goals)
- **Goal-aware AI tone** — conversations feel personal, not generic
- **Long-term retention foundation** — customer with active goal × wellness data accumulated = strong stickiness
- **Cross-module dashboard observations** become goal-relevant (not just data)
- **Differentiation vs competitors** — no salon platform has goal-aware AI yet

## 18. What this does NOT unblock

- ❌ Diet-app goal templates (forbidden per §2.1)
- ❌ Medical goal templates (forbidden per §2.2)
- ❌ Goal coaching plans (out of scope)
- ❌ Public goal sharing / accountability (Phase 4+ if ever)
- ❌ Goal-tied monetization (Q-WG-monetization Phase 3+)
- ❌ Skip pre-deploy legal sign-off on §2 anti-pattern policy + §3.3 forbidden category list
- ❌ Q-WG8 strategic decision (customer asking «как достичь цели» — coaching boundary)

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Wellness backend lead (apps/wellness/goals/) | ☐ | |
| Mini App frontend (wizard 4 steps + goal display + edit/archive) | ☐ | |
| AI prompt engineering (forbidden-pattern detection + goal-aware tone modulation) | ☐ | |
| **Legal / Compliance** (§2 anti-pattern policy + §3.3 forbidden category list + Q-WG8 coaching boundary) | ☐ | 🔴 PRE-DEPLOY |
| Privacy / Legal (Q-WG14/19 goal text handling + Q-WG10 cross-tenant) | ☐ | |
| Founder (Q-WG12 service rec mapping + Q-WG20 escalation policy) | ☐ | |
| Accessibility (WCAG 2.2 AA on wizard + display) | ☐ | |
| Policy review (Q-WG8 coaching boundary + Q-WG20 escalation policy — mental-health-adjacent) | ☐ | |

## Last verified
2026-05-19 (initial draft, Customer Wellness Goal Setting Layer 2 locked; completes wellness OS «AI knows you» promise by adding the WHY layer to module WHAT layers)
