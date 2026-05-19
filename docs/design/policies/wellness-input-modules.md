# Wellness Input Modules — Food / Water / Body / Sleep / Mood / AI Avatar

**Date:** 2026-05-18 r1
**Status:** Foundational — complements [`core-wellness-profile.md`](./core-wellness-profile.md)
**Reads:** [`product-ux-vision.md`](./product-ux-vision.md), [`core-wellness-profile.md`](./core-wellness-profile.md), [`information-architecture.md`](./information-architecture.md)

> 7 modules that capture wellness data from the user. Each module feeds specific Wellness Profile layers. Without these, the AI's «knowledge» of the user stays anemic — visit history only.

---

## 0. Why this exists

### The gap in earlier docs

Core Wellness Profile §7 (Layer 6 Nutrition) and §4 (Layer 3 Body State) referenced food/water/sleep data **as inputs** but did NOT design how the user gives that data. AI Avatar (before/after photo progression) was missing entirely. Without these modules:

- Layer 3 (Body State) stays empty beyond post-visit conversation
- Layer 6 (Nutrition) is theoretical only
- AI cannot make holistic recommendations
- We become «booking platform with chat», not «wellness OS»

### What this doc adds

A specification for **7 modular wellness inputs** the customer can use. Each is an opt-in feature with its own UX surface, data flow, AI behavior, and privacy class.

The modules are **decoupled** — a customer might use water tracker only, or AI avatar only, or all 7. Each adds value independently AND in combination.

---

## 1. The 7 modules — overview

```
┌──────────────────────────────────────────────────────────────────┐
│                  WELLNESS INPUT MODULES                          │
├──────────────────────────────────────────────────────────────────┤
│  1. Food Scanner       photo + AI recognition → calories/macros  │
│  2. Water Tracker      quick taps to log water intake            │
│  3. Body Tracking      weight + measurements over time           │
│  4. Sleep Tracking     subjective rating + duration              │
│  5. Mood / Energy      daily 1-tap state self-report             │
│  6. AI Avatar          before/after body photos + AI comparison  │
│  7. Symptom Diary      structured pain / state events            │
└──────────────────────────────────────────────────────────────────┘
       │
       ▼
   ALL feed →   Wellness Profile (Layers 3 + 6 + 4 + 5)
       │
       ▼
   AI uses for: recommendations, retention, holistic insights, progress framing
```

### Module summary

| # | Module | Profile layer | MVP phase | Critical for | Strategic weight |
|---|---|---|---|---|---|
| 1 | Food Scanner | Layer 6 | Phase 3 | Wellness coherence + AI insight | High |
| 2 | Water Tracker | Layer 6 | Phase 2 | Daily engagement + habit loop | Medium |
| 3 | Body Tracking | Layer 3 | Phase 2 | Long-term progress | Medium |
| 4 | Sleep Tracking | Layer 3 | Phase 2 | Cross-system correlation | Medium |
| 5 | Mood / Energy | Layer 3 + 7 | Phase 1 (lightweight) | Daily AI calibration | High |
| 6 | AI Avatar | Layer 3 + 4 | Phase 3 | Retention + WOW moment | Very high |
| 7 | Symptom Diary | Layer 3 | Phase 2 | Chronic-care customers | Medium-high |

### Surfaces (all 7 modules)

- **Mini App `Самочувствие` tab** (per IA): primary hub
- **Bot DM**: quick captures via conversation («сегодня устала», «пила мало воды»)
- **Bot proactive nudges**: «не пили воду 4 часа — поставить напоминание?»
- **Mini App home (state-adaptive)**: quick-action chips for active modules

---

## 2. Module 1 — Food Scanner

### What it does
Customer takes photo of food. AI recognizes dish, estimates portion, computes calories + macros. Saves entry to Layer 6 Nutrition.

### Why this matters
- **Closes the holistic loop**: «после плохого питания отёчность утром → массаж лимфодренаж»
- **Unique differentiation**: most beauty/wellness platforms don't offer food tracking
- **Habit anchor**: customers who track food are 3× more retained (industry benchmark)
- **NOT a diet app**: we don't moralize, don't shame, don't recommend deficits. We **observe** for wellness insight.

### UX flow

#### Entry points
1. **Mini App home / Самочувствие**: «🍽 Сканировать еду» quick action chip
2. **Bot DM**: customer sends photo unprompted → AI recognizes intent, asks «сохранить как обед?»
3. **Proactive (Phase 3 only)**: «Заметил — обычно вы обедаете в это время. Сегодня в норме?» (gentle, opt-in)

#### Capture flow (Mini App)
```
┌────────────────────────────────────┐
│ ← Скан еды                         │
├────────────────────────────────────┤
│                                    │
│   [    photo preview area    ]     │
│   [   tap to capture or       ]    │
│   [   choose from gallery     ]    │
│                                    │
│  Время приёма: [Обед ▾]            │
│  Дата: [Сегодня] [Изменить]        │
│                                    │
│  [Сделать фото]  [Из галереи]      │
│                                    │
└────────────────────────────────────┘
```

After photo:
```
┌────────────────────────────────────┐
│ ← Распознанные продукты            │
├────────────────────────────────────┤
│  [уменьшенное фото]                │
│                                    │
│  Помощник определил:               │
│  ┌──────────────────────────────┐ │
│  │ • Гречка       ~150 г        │ │
│  │ • Курица       ~100 г        │ │
│  │ • Огурец       ~50 г         │ │
│  │ [+ Добавить] [✎ Изменить]    │ │
│  └──────────────────────────────┘ │
│                                    │
│  Итого:                            │
│  Калории: ~480                     │
│  Белки: 35 г  Жиры: 8 г  Углев: 50 │
│                                    │
│  Заметка: [_______________]        │
│                                    │
│  [Не угадал? Изменить вручную]    │
│  [Сохранить]  [Удалить]            │
│                                    │
└────────────────────────────────────┘
```

#### AI inference

- Uses food recognition ML model (likely 3rd-party: Google Vision / Foodvisor / similar)
- Confidence threshold 0.7 for auto-suggest; below → ask user to confirm/correct
- Portion estimation: rough (small/medium/large) MVP, exact-grams Phase 4 with depth-camera
- Macros from food database (USDA / RU-equivalent)

#### Privacy
- Photos NOT stored long-term by default (deleted after recognition + saved log)
- Customer can opt-in to «keep photos» for AI Avatar correlation (Module 6)
- Food data: Layer 6 Nutrition retention (per Q-C3 sensitive)
- NEVER shared cross-tenant

### AI use of data
- Correlate with Body State: «после позднего ужина отёчность утром»
- Recommend wellness services: «много соли вчера — лимфодренаж?»
- Retention: «нерегулярное питание = нерегулярный сон = время на массаж»
- NEVER advice on weight loss / restriction — we're not a diet app

### Anti-patterns to prevent
- ❌ «Вы съели слишком много» — judgment
- ❌ Daily calorie target / deficit recommendations — out of scope
- ❌ Public sharing / leaderboards — privacy violation
- ❌ Food scoring («это «плохая» еда») — moralistic
- ❌ Pressure to log every meal — anxiety-inducing

### Phasing
- **Phase 3**: full feature with ML recognition
- **Phase 2 stub**: manual food entry only (no photo recognition) — useful for early data collection
- **Phase 1**: not present (Layer 6 stays empty)

---

## 3. Module 2 — Water Tracker

### What it does
Customer logs water intake in tap-glasses or ml. AI suggests timing based on patterns. Optional reminders.

### Why this matters
- **Daily engagement** — only daily-rhythm module
- **Habit loop entry point** — easiest wellness habit
- **Health-affirming**: most customers under-hydrate; small wins build trust
- **Cross-correlation**: low water + neck pain + headache → lymphatic massage recommendation

### UX flow

#### Mini App quick capture
```
┌────────────────────────────────────┐
│  💧 Вода сегодня                   │
│                                    │
│  ━━━━━━━━━░░░░░░░░  6 / 10 стаканов│
│                                    │
│  [+ Стакан 250 мл]                 │
│  [+ Большой 500 мл]                │
│  [+ Кружка]                        │
│  [Меньше...] [Изменить цель ▾]     │
│                                    │
│  ── Сегодня по часам ──            │
│  09:00 ━ 12:00 ━ 14:00 ━ 16:00     │
│  💧    💧💧   💧     💧             │
│                                    │
│  ── Тренд за неделю ──             │
│  [простой бар-чарт 7 дней]         │
│                                    │
└────────────────────────────────────┘
```

#### Bot DM quick capture
```
User: попила воды
Bot: 💧 +250 мл. Сегодня 6 из 10 стаканов. Хорошо идёт!
[или с inline]
[+ Ещё стакан] [Меньше — другая порция]
```

#### Reminders (opt-in)
- Throttled: max 2-3 nudges per day
- Smart timing: based on user pattern + last log
- Tone: gentle, never shaming
  > «Уже 3 часа без воды — пора отдохнуть и глоток.»
- Vacation mode: user can pause anytime

### AI inference
- Daily target: based on body weight (if known) + activity hints; default 2000 ml
- Pattern detection: «у вас обычно мало воды по понедельникам»
- Correlation: low-water periods + customer complaints → suggest lymphatic services

### Privacy
- Light data — water log is Layer 6 Nutrition but low sensitivity
- Same retention rules as food data

### Anti-patterns
- ❌ Gamification with badges — childish
- ❌ Public leaderboards
- ❌ Strict goal-shaming
- ❌ Excessive notifications (>3/day)

### Phasing
- **Phase 2**: full feature with reminders
- **Phase 1**: simple manual log only (no reminders) — testable with minimal effort

---

## 4. Module 3 — Body Tracking

### What it does
Customer logs weight + body measurements (waist, hips, chest if relevant). AI tracks trend.

### Why this matters
- Long-term progress is invisible without measurement
- Visual progress (AI Avatar Module 6) needs anchoring data
- Customer goals (Layer 2) like «weight_management» or «body_shape» need data to track

### UX flow

#### Mini App body tracking
```
┌────────────────────────────────────┐
│  📏 Параметры                      │
├────────────────────────────────────┤
│  Вес: 68.5 кг                      │
│  Изменение за месяц: −1.2 кг       │
│                                    │
│  Талия:  72 см                     │
│  Бёдра:  94 см                     │
│  ...                               │
│                                    │
│  [+ Замер сегодня]                 │
│                                    │
│  ── Тренд ──                       │
│  [простой line-chart за период]   │
│  Период: [3 мес ▾]                 │
│                                    │
│  ── Заметки ──                     │
│  [Свободный текст «после отпуска…»]│
└────────────────────────────────────┘
```

#### Capture frequency
- Suggested: 1× per 2 weeks (avoid daily-weight obsession)
- Customer-controlled
- Reminders: only if customer requested

### AI inference
- Trend analysis (rising / stable / declining)
- Correlation with services («после курса лимфодренажа талия уменьшается»)
- NEVER prescribe weight target — we're not a coaching app

### Privacy
- Sensitive (Layer 3 Body State)
- Highest retention restrictions

### Phasing
- **Phase 2**: weight + 3 key measurements
- **Phase 3**: full body composition if customer opts to integrate scales

---

## 5. Module 4 — Sleep Tracking

### What it does
Customer logs sleep — quality (1-10) + duration. Optional integration with wearables later.

### Why this matters
- Sleep is THE central wellness metric
- Poor sleep → high stress → need for services
- Easy to capture (1 question, morning)

### UX flow

#### Bot DM morning prompt (proactive, opt-in)
```
Bot: Как спалось этой ночью?
[😴 Отлично]  [🙂 Нормально]  [😐 Так себе]  [😣 Плохо]
[Хочу больше деталей →]
```

Detailed version (when chosen):
```
Bot:
Сон сегодня:
Длительность: [6.5 часов ▾]
Качество: ★★★☆☆ (3/5)
Просыпались? ☐ да
Заметка: [быстро уснула, но проснулась в 4]

[Сохранить] [Пропустить]
```

#### Mini App view
```
┌────────────────────────────────────┐
│  💤 Сон за неделю                  │
├────────────────────────────────────┤
│  Средняя длительность: 6.8 часов   │
│  Среднее качество: ★3.6/5          │
│                                    │
│  Пн ███████░ 7ч ★4                 │
│  Вт █████░░░ 5ч ★2                 │
│  Ср ██████░░ 6ч ★3                 │
│  Чт ████████ 8ч ★5                 │
│  ...                               │
│                                    │
│  Закономерность: «после массажа    │
│  во вторник в среду спите лучше»   │
└────────────────────────────────────┘
```

### AI inference
- Cross-correlate with visit history: «после массажа вы спите лучше»
- Surface in retention messaging: «3 ночи плохо спите — массаж шеи?»
- NEVER advise sleep hygiene (out of scope — refer to specialist)

### Privacy
- Layer 3 Body State (sensitive)

### Phasing
- **Phase 2**: subjective (quality + duration self-report)
- **Phase 4**: optional Apple Health / Google Fit integration

---

## 6. Module 5 — Mood / Energy / Stress Self-Report

### What it does
Daily 1-tap state input. Quickest module to log. Largest engagement value.

### Why this matters
- **Single most valuable signal** for state-aware AI
- Drives `core-user-states.md` PROBLEM_SEEKING + AT_RISK detection
- Tone adaptation in real-time

### UX flow

#### Bot DM morning (opt-in, throttled)
```
Bot: Доброе утро! Как себя чувствуете?
[😊 Отлично]  [🙂 Норм]  [😐 Так себе]  [😣 Тяжко]
[Подробнее →]
```

Tap-to-log: 1 tap → recorded. Customer can elaborate, but not required.

#### Detailed (Mini App)
```
┌────────────────────────────────────┐
│  Как вы сегодня?                   │
├────────────────────────────────────┤
│  Энергия:    [────●─────]  5/10    │
│  Стресс:     [───────●──]  7/10    │
│  Настроение: [─────●────]  6/10    │
│  Болит?      ☐ да [где?]           │
│                                    │
│  Заметка: [______________]         │
│                                    │
│  [Сохранить]                       │
└────────────────────────────────────┘
```

#### Quick-capture chip on Home (Mini App)
Always visible:
```
[ Как вы сегодня?  →  😊 🙂 😐 😣 ]
```

One tap saves and updates Wellness Profile Layer 3.

### AI inference

- **State-aware tone**: high stress → calm tone; high energy → playful OK
- **Service recommendation**: stress_level >7 for 3 days → relaxation services priority
- **Retention timing**: avoid promo touches when customer's mood low

### Privacy
- Layer 3 Body State + Layer 7 Emotional inference
- Sensitive
- Customer can opt-out anytime; existing data anonymized after period

### Anti-patterns
- ❌ More than 1 daily prompt (annoying)
- ❌ Demanding detailed entry every time
- ❌ «Why is your mood low?» — invasive
- ❌ Streaks / consecutive-day pressure

### Phasing
- **Phase 1**: simple 4-emoji morning prompt (opt-in)
- **Phase 2**: detailed sliders + history view
- **Phase 3**: ML pattern detection («каждый понедельник стресс выше»)

---

## 7. Module 6 — AI Avatar (Before / After)

### What it does
Customer takes periodic body photos (face, full body, or specific area). AI maintains visual timeline. **Optional**: AI-generated visual progress comparison highlighting subtle changes.

### Why this matters — THE WOW feature
- **Highest emotional moment** in wellness journeys: «вижу прогресс»
- Sticky retention driver
- Differentiator vs all booking platforms (no one does this)
- Customer-owned: their photos, their progress
- **Honest reflection**: AI never fakes — it surfaces what's actually there

### UX flow

#### Setup (one-time, careful onboarding)
```
┌────────────────────────────────────┐
│  ← Помощник прогресса              │
├────────────────────────────────────┤
│  Хотите видеть прогресс наглядно?  │
│                                    │
│  Я могу сохранить ваши фото        │
│  до и после процедур, чтобы потом  │
│  показывать как меняется состояние.│
│                                    │
│  ── Что важно ──                   │
│  ✓ Фото видите только вы           │
│  ✓ Не публикуются нигде            │
│  ✓ Удалить можно в любой момент    │
│  ✓ Только для вашего прогресса     │
│                                    │
│  Какие зоны интересны?             │
│  ☐ Лицо (anti-age, кожа)           │
│  ☐ Тело — общий вид                │
│  ☐ Талия / живот                   │
│  ☐ Бёдра                           │
│  ☐ Состояние ногтей                │
│  ☐ Состояние волос                 │
│  ...                               │
│                                    │
│  [Не сейчас]  [Согласен и начать]  │
└────────────────────────────────────┘
```

#### Capture flow
Suggested cadence: 1× per 2-4 weeks (per zone)

```
┌────────────────────────────────────┐
│  📸 Фото прогресса — лицо          │
├────────────────────────────────────┤
│                                    │
│  Совет: при дневном свете,         │
│  без макияжа, тот же ракурс        │
│                                    │
│  Прошлое фото:                     │
│  [миниатюра — для повторения]      │
│                                    │
│  [Сделать фото]                    │
│                                    │
│  Дата: [Сегодня]                   │
│  Контекст: [Опционально]           │
│  • после процедуры? которой?       │
│  • курс лечения / поездка / etc.   │
│                                    │
│  [Сохранить] [Отмена]              │
└────────────────────────────────────┘
```

#### Comparison view
```
┌────────────────────────────────────┐
│  ← Прогресс лица за 6 месяцев      │
├────────────────────────────────────┤
│  [Слайдер для сравнения двух фото] │
│  Январь   ◀────────●────▶    Июнь  │
│                                    │
│  [фото с overlay показывающим      │
│   что заметно изменилось]          │
│                                    │
│  Помощник заметил:                 │
│  • Тонус кожи лучше, особенно      │
│    в области лба и щёк             │
│  • Стало меньше тени под глазами   │
│  • Контур лица стал чуть чётче    │
│                                    │
│  За этот период было:              │
│  • 4 чистки лица                   │
│  • 2 мезотерапии                   │
│  • курс масок (3 недели)           │
│                                    │
│  [Поделиться] [Удалить] [Записаться│
│   на следующий шаг]                │
└────────────────────────────────────┘
```

### AI inference
- **Visual comparison**: simple side-by-side, AI commentary on visible changes (with care — never fake improvements)
- **NOT plastic surgery preview**: we DO NOT generate «что будет» fake images
- **Service correlation**: «после курса X заметили улучшение по Y зоне»
- **Confidence**: AI explicitly says when it can't detect change («сложно сказать — освещение разное»)

### Privacy — highest sensitivity

- **Photos NEVER shared cross-tenant** — period
- **Cannot be exported in plain form without explicit consent** (Phase 4+)
- Customer can delete entire photo set in 1 tap
- After deletion: 30-day soft-delete window, then hard-deleted (per OP6)
- Photos stored encrypted at rest, salon owner doesn't have direct access

### Anti-patterns to prevent
- ❌ AI-generated «как будете выглядеть после» fake previews — manipulative
- ❌ Filter / beautification — destroys honesty
- ❌ Public sharing prompts — privacy violation
- ❌ Body shaming framing — never
- ❌ «Не достаточно прогресса» — invalidates customer's effort
- ❌ Mandatory cadence pressure — choice always

### Phasing
- **Phase 3**: photo storage + manual side-by-side
- **Phase 4**: AI commentary on visible changes
- **Phase 5**: optional time-lapse video generation

### Special: master/practitioner role
- Master can request access to specific zone photos with customer consent (for pre-procedure planning)
- One-time access, audited
- Customer revokes anytime

---

## 8. Module 7 — Symptom Diary

### What it does
Structured logging of specific symptoms (pain, skin issues, hair changes, swelling, etc.) over time. For customers with chronic-care patterns.

### Why this matters
- Chronic-care customers (back pain, skin conditions) are highest-LTV
- Structured data enables real correlation with services
- AI can detect patterns customer doesn't notice

### UX flow

#### Add symptom event
```
┌────────────────────────────────────┐
│  ← Заметка о состоянии             │
├────────────────────────────────────┤
│  Что сейчас:                       │
│  ⦿ Боль                            │
│  ◯ Высыпания                       │
│  ◯ Отёчность                       │
│  ◯ Усталость                       │
│  ◯ Другое                          │
│                                    │
│  Зона: [Шея ▾]                     │
│  Интенсивность: [────●──────] 5/10 │
│                                    │
│  Что могло спровоцировать?         │
│  ☐ Долго сидела за компьютером     │
│  ☐ Стрессовый день                 │
│  ☐ Не спала                        │
│  ☐ Не уверена                      │
│  Другое: [______________________]  │
│                                    │
│  [Сохранить]                       │
└────────────────────────────────────┘
```

#### Pattern surfacing
After 5+ entries, AI shows pattern:
```
Помощник заметил:
«Боль в шее обычно появляется после
рабочей недели — четверг и пятница.»

Может, попробуем регулярный массаж
по средам? Часто помогает превентивно.
```

### AI inference
- Pattern detection (день недели, после события, циклическая)
- Service correlation
- Routes to medical specialist if signals suggest non-wellness scope

### Privacy
- Sensitive (Layer 3 + Layer 4 medical-adjacent)
- Tight retention; structured flags preferred over free text

### Phasing
- **Phase 2**: basic structured entry
- **Phase 3**: pattern detection + service correlation
- **Phase 4**: cross-symptom correlation

---

## 9. Cross-module integration

### Data flows to Wellness Profile

| Module | Writes to | Confidence | Frequency |
|---|---|---|---|
| Food Scanner | Layer 6 Nutrition | 0.6-0.9 per recognition | Per meal |
| Water Tracker | Layer 6 Nutrition | 0.95 (self-logged) | Multiple per day |
| Body Tracking | Layer 3 Body State | 1.0 (measured) | Per 2 weeks |
| Sleep Tracking | Layer 3 Body State | 0.7 (self-reported) | Per night |
| Mood / Energy | Layer 3 + Layer 7 | 0.9 (1-tap) | Daily |
| AI Avatar | Layer 3 + Layer 4 | 1.0 (photo) | Per 2-4 weeks |
| Symptom Diary | Layer 3 + Layer 4 | 0.9 (structured) | Event-driven |

### AI correlation examples

**Cross-module insight 1**: «Низкая вода 5 дней + плохой сон + усталость + жалоба на отёчность → лимфодренаж priority recommendation»

**Cross-module insight 2**: «После каждого массажа спины — сон лучше следующие 3 ночи → закрепляем эту связь, предлагаем регулярный курс»

**Cross-module insight 3**: «Фото лица за 3 месяца + 4 чистки + 2 мезотерапии → AI показывает заметный прогресс в зоне Y → trust moment»

### Mini App surface integration

Per [`information-architecture.md`](./information-architecture.md):
- **Самочувствие** surface = aggregated view across all active modules
- **Home (state-adaptive)** shows ONE relevant module nudge per visit («попили воды?»)
- **Profile** has master switch for each module: «использую / не использую»

---

## 10. Permissions matrix (per role)

| Action | Customer (self) | Owner | Admin | Master |
|---|---|---|---|---|
| View own input data | ✅ | ❌ | ❌ | ❌ |
| Add / edit / delete own data | ✅ | ❌ | ❌ | ❌ |
| Opt-in to module | ✅ | n/a | n/a | n/a |
| Opt-out from module | ✅ | n/a | n/a | n/a |
| View AI insights about self | ✅ | ❌ | ❌ | ❌ |
| Grant master view of body-tracking / avatar (specific zone, time-bound) | ✅ explicit | n/a | n/a | ✅ if granted, audited |

**Critical**: salon side has ZERO default visibility into customer's wellness inputs. AI may use the data for recommendations TO the customer, but salon Owner/Admin does NOT see Food/Water/Body/Sleep/Mood/Avatar/Symptom data unless customer explicitly grants access.

This is by design — privacy is the trust foundation. Without strict separation, customers won't engage.

---

## 11. Consent layering

Per [`core-wellness-profile.md`](./core-wellness-profile.md) §16:

| Module | Consent type | Default |
|---|---|---|
| Food Scanner | Health-tracking consent (explicit opt-in) | OFF |
| Water Tracker | Health-tracking consent | OFF |
| Body Tracking | Health-tracking consent | OFF |
| Sleep Tracking | Health-tracking consent | OFF |
| Mood / Energy | Adaptive AI consent (gentle opt-in) | OFF (suggested at activation) |
| AI Avatar | Photo storage consent + explicit per-zone | OFF |
| Symptom Diary | Health-tracking consent + sensitive flag | OFF |

Each module has its own clear consent dialog at first use. No bundled consents.

---

## 12. Anti-pattern guards (cross-module)

### Privacy
- ❌ Salon admin sees customer water log → violation
- ❌ Cross-tenant visibility of food logs → never
- ❌ Photo data leaked to non-customer surfaces → never

### Healthy framing
- ❌ Calorie-deficit goal tracking — we're not a diet app
- ❌ «Streak broken» pressure — anxiety-inducing
- ❌ Public leaderboards / comparison — social-pressure violation
- ❌ Body-shaming language — never
- ❌ «Fix yourself» framing → use «as you wish to feel»

### Honest AI
- ❌ AI-generated fake before/after — manipulation
- ❌ Pretending detection in noisy data
- ❌ Overstating correlation («это точно от массажа») — uncertain framing

### Frequency
- ❌ Multiple daily prompts per module — annoying
- ❌ Multiple modules prompting same day — overwhelm
- ❌ Module reminders during HUMAN_LOCKED conversation tier

---

## 13. Phasing rollout

### Phase 1 (MVP, ~3 months)
- Module 5 (Mood / Energy) — lightweight 1-tap; opt-in via Самочувствие placeholder
- No other modules active

### Phase 2 (~6 months)
- Module 2 (Water Tracker) full
- Module 3 (Body Tracking) full
- Module 4 (Sleep Tracking) full
- Module 7 (Symptom Diary) basic
- Module 5 enhanced with detailed sliders + history

### Phase 3 (~9-12 months)
- Module 1 (Food Scanner) with ML recognition
- Module 6 (AI Avatar) basic — photo storage + manual side-by-side
- Module 7 enhanced with pattern detection
- Cross-module insights begin

### Phase 4 (12+ months)
- Module 6 with AI commentary
- Wearable integrations (HealthKit / Google Fit)
- Module 1 with depth-camera portion estimation
- Predictive cross-module insights

### Phase 5 (vision)
- Module 6 time-lapse video
- Cross-customer wellness benchmarks (opt-in aggregate)
- Adjacent vertical integrations (fitness / nutrition coach)

---

## 14. Backend architecture

### Per-module storage
Each module gets its own append-only event table:
- `wellness_food_event` (photo ref, recognized items, macros, time)
- `wellness_water_event` (amount, time)
- `wellness_body_measurement` (weight, custom measurements, time)
- `wellness_sleep_event` (duration, quality, notes)
- `wellness_mood_event` (energy, stress, mood ratings)
- `wellness_photo_avatar` (encrypted blob, zone, time, consent_at)
- `wellness_symptom_event` (type, zone, intensity, triggers)

### Aggregation jobs
- Daily: compute Layer 6 metrics_7d_avg (water, calories, etc.)
- Per-event: update Layer 3 state if relevant
- Weekly: pattern detection across modules → Layer 9 recommendations

### Privacy enforcement at API
- Customer endpoints: full access to own modules
- Tenant endpoints: ZERO access to module data (read returns 403 + audit)
- Master granted-access endpoints: scoped read by customer consent token

---

## 15. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| Q-WI1 | Food scanner ML — build in-house or 3rd-party (Foodvisor, Google Vision)? | 3rd-party MVP (cost + speed); revisit at 1000+ daily scans | Eng | 🟡 |
| Q-WI2 | Water tracker units — ml or стаканы (250 ml)? | Both — stakan default, switch to ml in settings | UX | 🟢 |
| Q-WI3 | Body tracking measurements — fixed list or customer adds custom? | Fixed 5 MVP (weight, waist, hips, chest, thigh); custom v1.1 | PM | 🟢 |
| Q-WI4 | Sleep tracking wearable integration — Phase 4 OR earlier if customers ask? | Customer-requested → accelerate; default Phase 4 | PM | 🟡 |
| Q-WI5 | Mood tracking — daily prompt timing (morning vs evening)? | Morning default (sets tone for day); customer can toggle | UX | 🟢 |
| Q-WI6 | AI Avatar — should photos be processable by master/practitioner without customer consent grant? | NO — always explicit consent grant, audited | Legal + PM | 🔴 before AI Avatar ships |
| Q-WI7 | AI Avatar AI commentary — should it ever say «no visible change»? | YES — honesty mandate; we never fake. Frame as «изменения тонкие — продолжайте текущий курс» | PM | 🟡 |
| Q-WI8 | Symptom diary — escalation to medical specialist auto-trigger thresholds? | If pain >7/10 + chronic 3+ months OR sudden new severe symptom → suggest medical consult + HUMAN_LOCKED tier | PM + Legal | 🟡 |
| Q-WI9 | Cross-tenant customer who uses modules at multiple salons — separate or merged? | Per Q-CO5: separate Wellness Profile per tenant; modules are part of that profile, so separate | PM | 🟢 |
| Q-WI10 | Customer who deletes account (OP6) — what happens to all module data? | Anonymized soft-delete 30d → hard-delete; AI Avatar photos hard-deleted immediately on customer request | Legal | 🟡 |
| Q-WI11 | Modules during salon's free trial / unpaid state? | Modules belong to CUSTOMER, not salon — customer keeps even if salon downgrades. Different from salon-billing modules. | Founder | 🟡 |
| Q-WI12 | Should modules cost customer money in customer-pays tier (Phase 3 vision)? | Some modules free forever (Mood, Water, Body), premium modules (AI Avatar, advanced ML Food) in paid tier | Founder | 🟢 |
| Q-WI13 | Group features — can customer share progress with friend / partner? | NO MVP (privacy + scope creep); v1.2+ explicit opt-in 1-to-1 sharing only | PM | 🟢 |

---

## 16. Cross-document linkage

- Foundational: [`core-wellness-profile.md`](./core-wellness-profile.md) — modules WRITE to Layers 3 + 6 (and 4 for symptoms / avatar)
- Vision: [`product-ux-vision.md`](./product-ux-vision.md) — modules deliver «AI knows you» promise
- States: [`core-user-states.md`](./core-user-states.md) — module engagement affects state computation
- IA: [`information-architecture.md`](./information-architecture.md) — modules live in Самочувствие surface
- Privacy: [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) §4 + §6
- Persona: [`assistant-persona.md`](./assistant-persona.md) — module messages persona-conformed
- Decisions log: [`../decisions-log.md`](../decisions-log.md) — Q-WI1 to Q-WI13 added on review

---

## 17. What this unblocks

- **AI's «I know you» becomes real** — not anecdotal
- **Wellness OS positioning** — proves it's not «another booking bot»
- **Differentiation moat** — no competitor offers all 7
- **Retention compounding** — engagement modules drive daily touchpoints
- **Customer-pays tier unlock** — premium modules (Avatar, ML Food) for paid customers
- **Adjacent vertical extensibility** — modules transfer to fitness / nutrition coach platforms (year 3 vision)

## 18. What this does NOT unblock

- ❌ Replace MVP work — booking, conversations, billing still required
- ❌ Skip privacy/legal review — modules are sensitive data, RU юрист must review
- ❌ Auto-build all 7 — phased rollout mandatory
- ❌ Salon-side analytics on module data — privacy boundary

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| Designer | ☐ | |
| Product / Founder (vision approval) | ☐ | |
| Engineering (storage + privacy enforcement) | ☐ | |
| AI/ML (food recognition + avatar comparison) | ☐ | |
| Legal (sensitive data handling, esp. AI Avatar) | ☐ | |
| UX (anti-pattern review — anti-shame framing) | ☐ | |

## Last verified
2026-05-18 (filling gap from earlier wellness OS spec)
