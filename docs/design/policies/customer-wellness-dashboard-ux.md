# Customer Wellness Dashboard — Самочувствие aggregator surface

**Date:** 2026-05-19 r1
**Status:** Foundational — THE wellness OS surface; aggregator for all 7 wellness modules
**Reads:** [`wellness-input-modules.md`](./wellness-input-modules.md), [`information-architecture.md`](./information-architecture.md), [`core-wellness-profile.md`](./core-wellness-profile.md), [`customer-profile-management-ux.md`](./customer-profile-management-ux.md), [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md), [`core-user-states.md`](./core-user-states.md), [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`product-ux-vision.md`](./product-ux-vision.md), [`event-taxonomy.md`](./event-taxonomy.md), [`../handoffs/2026-05-19-wellness-mood-handoff.md`](../handoffs/2026-05-19-wellness-mood-handoff.md), [`../handoffs/2026-05-19-wellness-water-handoff.md`](../handoffs/2026-05-19-wellness-water-handoff.md), [`../handoffs/2026-05-19-wellness-body-handoff.md`](../handoffs/2026-05-19-wellness-body-handoff.md), [`../handoffs/2026-05-19-wellness-sleep-handoff.md`](../handoffs/2026-05-19-wellness-sleep-handoff.md), [`../handoffs/2026-05-19-wellness-symptom-handoff.md`](../handoffs/2026-05-19-wellness-symptom-handoff.md), [`../handoffs/2026-05-19-wellness-ai-avatar-handoff.md`](../handoffs/2026-05-19-wellness-ai-avatar-handoff.md), [`../handoffs/2026-05-19-wellness-food-handoff.md`](../handoffs/2026-05-19-wellness-food-handoff.md)

> Per-module handoffs design WHAT each module captures + module-specific UX. This doc designs the **aggregator** — Mini App «Самочувствие» tab when customer enters it (instead of going to specific module). State-adaptive layout based on what's activated. Cross-module observations. The wellness OS surface that proves «AI knows you» promise.

---

## 0. Why this exists

### 0.1 Strategic context — THE wellness OS surface

Per [`product-ux-vision.md §1`](./product-ux-vision.md): we're building **AI Wellness OS**, not booking platform. The «Самочувствие» tab is where this promise is rendered as customer experience. Without this aggregator:
- 7 wellness modules feel like 7 disconnected apps
- Cross-correlation insights (water + sleep + mood) have no surface
- Module discovery happens only via Профиль — friction-justified
- Customer has no «overview» of own wellness — feels like data entry chore, not understanding tool

### 0.2 The gap

Existing docs cover per-module experience:
- [`wellness-mood-handoff.md §7`](../handoffs/2026-05-19-wellness-mood-handoff.md) — Mood section
- [`wellness-water-handoff.md §6`](../handoffs/2026-05-19-wellness-water-handoff.md) — Water section
- [`wellness-body-handoff.md §6`](../handoffs/2026-05-19-wellness-body-handoff.md) — Параметры section
- [`wellness-sleep-handoff.md §6`](../handoffs/2026-05-19-wellness-sleep-handoff.md) — Сон section
- [`wellness-symptom-handoff.md §6`](../handoffs/2026-05-19-wellness-symptom-handoff.md) — Симптомы section
- [`wellness-ai-avatar-handoff.md §6`](../handoffs/2026-05-19-wellness-ai-avatar-handoff.md) — Avatar timeline
- [`wellness-food-handoff.md §7`](../handoffs/2026-05-19-wellness-food-handoff.md) — Еда section

But NONE designs the **«Самочувствие» tab entry point** that customer lands on when tapping the tab. [`information-architecture.md`](./information-architecture.md) names it as one of 5 Mini App surfaces but content unspecified.

### 0.3 The promise

Single source for:
- State-adaptive Самочувствие tab layout (based on which modules are active)
- Per-module summary card design
- Cross-module observation rules + UI (Phase 2 simple rules; Phase 3+ ML)
- Module discovery + activation flow from dashboard
- Today's quick-capture row (always-visible chips for active modules)
- Time period filters (today / week / month / quarter)
- Empty states + onboarding for new users
- Privacy invariants — strict customer-only at every layer
- Tone — observational, never coaching

---

## 1. Scope

### IN
- Mini App «Самочувствие» tab landing screen
- 5 state-adaptive layouts (0 / 1-2 / 3-4 / 5-6 / 7 modules active)
- Per-module summary card design
- Cross-module observation Phase 2 simple rules
- Module discovery section (activate more)
- Today's quick-capture row
- Period filters (day / week / month / quarter)
- Empty states
- Navigation between modules (Самочувствие → specific module section)
- Aggregator service backend (reads from per-module data; no new write surface)
- Privacy enforcement throughout
- Cross-module insight permission rules
- 2 NEW API endpoints (aggregate read; observations read)

### OUT
- Per-module data entry (each module's section handles its own)
- Tenant-side view (privacy — strict customer-only)
- AI Quality data (separate from wellness)
- Booking / catalog data (separate Записи tab)
- Owner / master / admin equivalents
- Cross-tenant aggregate view (privacy)
- Predictive recommendations (Phase 4+ ML)
- Goal setting / target tracking (separate Wellness Goals doc — pending)
- Wearable integration insights (Phase 4+)
- Export to PDF / share (Phase 3+)
- Customer-pays tier feature gating (Phase 3 if monetized)

---

## 2. Strategic constraints — non-negotiable

### 2.1 NO «wellness score» (single magic number)
Same anti-pattern as sleep score / BMI / sleep apps. NEVER aggregate 7 modules into one «overall wellness 78/100». Each module visible separately.

### 2.2 NO cross-module streaks
Module-specific anti-streak principles (Mood §6.3, Sleep §13, Body §13, etc.) compound. NEVER «5 days of logging all modules!» streak. Anti-OCD principle stacks.

### 2.3 NO comparison with other customers
Phase 4+ may explore opt-in aggregate benchmarks BUT MVP NEVER comparative.

### 2.4 NO «complete your profile» nag
Per [`customer-profile-management-ux.md §12`](./customer-profile-management-ux.md) anti-pattern — profile is functional. Same here — wellness modules optional, no nag.

### 2.5 Privacy hierarchy maintained
- All data customer-only
- Cross-module observations customer-only
- NEVER salon side anything
- Master pre-arrival context (which is the only salon-side surface) — does NOT include wellness data per [`master-conversational-templates §5.5`](./master-conversational-templates.md#55-customer-pre-arrival-context-surface) (only Layer 4 service history reactions in current master's procedures)

### 2.6 Voice anchor — observational
Same as per-module insights: «заметила что...» / «N раз за период» — neutral facts.
- NEVER «хорошо/плохо», «прогресс»
- NEVER «вы должны / следует»
- NEVER motivational coaching

### 2.7 Wellness data is customer-owned, NOT tenant-owned
If tenant goes ARCHIVED per [`tenant-suspension-pause-ux.md §14.3`](./tenant-suspension-pause-ux.md) — customer's wellness module data is purged with tenant (cross-tenant per Q-CO5 boundary). PAUSED / SUSPENDED tenants — wellness modules continue working for customer per [`tenant-suspension-pause-ux.md §11.2`](./tenant-suspension-pause-ux.md) wellness module behavior.

---

## 3. State-adaptive layouts

5 layouts based on count of customer's active wellness modules + last activity.

### 3.1 Layout: 0 modules active (Discovery mode)

```
┌────────────────────────────────────────┐
│ Самочувствие                            │
├────────────────────────────────────────┤
│                                        │
│ Помощник может помогать отслеживать    │
│ ваше самочувствие.                     │
│                                        │
│ Только вы видите эти данные. Студия    │
│ не видит ничего.                       │
│                                        │
│ ── Что доступно ──                     │
│                                        │
│ 🙂 Настроение                          │
│ Утром одним тапом отмечать как вы.    │
│ [Подключить]                           │
│                                        │
│ 💧 Вода                                │
│ Записывать сколько пьёте.             │
│ [Подключить]                           │
│                                        │
│ 🌙 Сон                                 │
│ Длительность и качество.              │
│ [Подключить]                           │
│                                        │
│ 📏 Параметры                            │
│ Вес и объёмы по запросу.              │
│ [Подключить]                           │
│                                        │
│ 🩹 Симптомы                             │
│ Что и когда беспокоит.                │
│ [Подключить]                           │
│                                        │
│ 📸 Фото-прогресс                       │
│ До/после для зон, выбранных вами.     │
│ [Подключить]                           │
│                                        │
│ 🍽 Еда                                 │
│ Распознавание по фото.                │
│ [Подключить]                           │
│                                        │
│ Можно подключить один или все.        │
│ Каждый — отдельное согласие.          │
└────────────────────────────────────────┘
```

### 3.2 Layout: 1-2 modules active (Light mode)

Focus on active modules. No cross-module observations yet (insufficient data).

```
┌────────────────────────────────────────┐
│ Самочувствие · {{today_date}}          │
├────────────────────────────────────────┤
│                                        │
│ [🙂 Как вы сегодня?] ← if Mood active  │
│ (only if not logged today)             │
│                                        │
│ ── Сегодня / эта неделя ──             │
│                                        │
│ 🙂 Настроение                          │
│ Сегодня: ★4                            │
│ За неделю: ★3.8 (5 / 7 дней)           │
│ [Подробнее →]                          │
│                                        │
│ 💧 Вода                                │
│ Сегодня: 1250 / 2000 мл (62%)          │
│ За неделю: 1850 мл/день в среднем     │
│ [Подробнее →]                          │
│                                        │
│ ── Можно добавить ──                   │
│                                        │
│ ☐ Сон                                  │
│ ☐ Параметры                            │
│ ☐ Симптомы                             │
│ [+ Подключить]                         │
└────────────────────────────────────────┘
```

### 3.3 Layout: 3-4 modules active (Aggregator mode)

First cross-module observations appear (need ≥ 3 modules for meaningful correlations).

```
┌────────────────────────────────────────┐
│ Самочувствие · {{today_date}}          │
├────────────────────────────────────────┤
│                                        │
│ [Quick-capture chips for today]        │
│ [🙂 Настроение] [+ Вода]               │
│                                        │
│ ── Активные модули (4) ──              │
│                                        │
│ 🙂 Настроение     Сегодня: ★4          │
│ 💧 Вода           Сегодня: 1250 мл     │
│ 🌙 Сон            Прошлая ночь: 7.5ч ★4│
│ 🩹 Симптомы       За 7 дней: 2 записи  │
│ [Открыть каждый]                       │
│                                        │
│ ── Что заметно за неделю ──            │
│                                        │
│ Период: [Неделя ▾]                     │
│                                        │
│ • Лучше спите в дни когда отмечаете    │
│   хорошее настроение                   │
│ • В среду — низкая вода + плохой сон   │
│ • Боль в шее — чаще после рабочих      │
│   дней                                 │
│                                        │
│ {{if Phase3 service correlation}}      │
│ • После массажа во вторник сон в среду │
│   и четверг был лучше                  │
│ {{endif}}                              │
│                                        │
│ ⓘ Это наблюдения, не диагноз.          │
│                                        │
│ ── Можно добавить ──                   │
│ ☐ Параметры                            │
│ ☐ Фото-прогресс                        │
│ ☐ Еда                                  │
│ [+ Подключить]                         │
└────────────────────────────────────────┘
```

### 3.4 Layout: 5-6 modules active (Power user mode)

Compact module cards + emphasis on cross-module observations.

```
┌────────────────────────────────────────┐
│ Самочувствие · {{today_date}}          │
├────────────────────────────────────────┤
│                                        │
│ [Quick-capture row]                    │
│ [🙂] [💧] [🌙 заполнить] [📷 +фото]    │
│                                        │
│ ── Сводка сегодня ──                   │
│                                        │
│ 🙂 ★4   💧 1250мл   🌙 7.5ч ★4         │
│ 📏 68кг (12 мая)  🩹 0 записей сегодня │
│ 📸 фото 2 нед назад                    │
│                                        │
│ [Все модули →]                         │
│                                        │
│ ── Что заметно за месяц ──             │
│                                        │
│ Период: [Месяц ▾]                      │
│                                        │
│ Топ-3 наблюдения:                      │
│ 1. Сон лучше после лимфодренажа        │
│    (среднее ★4.2 vs ★3.7 без)          │
│ 2. Меньше боли в шее в недели когда    │
│    больше воды                         │
│ 3. Талия −1.5 см за месяц              │
│                                        │
│ [Все наблюдения (8) →]                 │
│                                        │
│ ⓘ Это паттерны в ваших данных, не     │
│   диагноз. AI не врач.                │
│                                        │
│ ── Можно добавить ──                   │
│ ☐ Еда                                  │
│ [+ Подключить]                         │
└────────────────────────────────────────┘
```

### 3.5 Layout: 7 modules active (Full mode)

All modules + Phase 3+ ML observations + advanced filters.

```
┌────────────────────────────────────────┐
│ Самочувствие · {{today_date}}          │
├────────────────────────────────────────┤
│                                        │
│ [Quick-capture row]                    │
│ [🙂] [💧] [🌙] [🍽 +еда] [🩹 +симптом] │
│                                        │
│ ── Все 7 модулей ──                    │
│                                        │
│ 🙂 ★4 · 💧 1250мл · 🌙 7.5ч ★4         │
│ 📏 68кг · 🩹 0/7д · 📸 18 фото          │
│ 🍽 950ккал / 1450 / 65%                │
│                                        │
│ ── Что заметно ──                      │
│                                        │
│ Период: [Месяц ▾]   Фильтр: [Все ▾]   │
│                                        │
│ [Топ-3 inline] + [Все 12 наблюдений →] │
│                                        │
│ ⓘ AI не врач — это паттерны в данных. │
│                                        │
│ ── Настройки модулей ──                │
│ [Управлять модулями →]                 │
│                                        │
└────────────────────────────────────────┘
```

### 3.6 Decision logic for layout

```
def select_layout(active_modules_count: int) -> Layout:
    if active_modules_count == 0:
        return DISCOVERY
    elif active_modules_count <= 2:
        return LIGHT
    elif active_modules_count <= 4:
        return AGGREGATOR
    elif active_modules_count <= 6:
        return POWER
    else:
        return FULL
```

Transitions are SMOOTH — when customer activates 3rd module, dashboard transitions Light → Aggregator automatically; cross-module observations appear.

---

## 4. Per-module summary cards

Each active module shows a compact summary card.

### 4.1 Card structure

```
{{icon}} {{module_name}}
{{today_or_latest_metric}}
{{period_summary}}
[Подробнее →]
```

### 4.2 Per-module specifics

| Module | Today / Latest | Period summary |
|---|---|---|
| 🙂 Mood | «Сегодня: ★4» OR «[🙂 Как вы сегодня?]» if not logged | «За неделю: ★3.8 (5/7 дней)» |
| 💧 Water | «Сегодня: 1250 / 2000 мл (62%)» | «В среднем 1850 мл/день» |
| 🌙 Sleep | «Прошлая ночь: 7.5ч ★4» OR «[Записать сегодняшний сон]» | «За неделю: 7.2ч / ★3.8» |
| 📏 Body | «{{latest_date}}: 68кг / 72см» | «За месяц: вес −0.5кг / талия −1см» |
| 🩹 Symptom | «За 7 дней: 2 записи» | «За месяц: 8 записей, чаще боль (шея)» |
| 📸 Avatar | «{{N}} фото за {{period}}» | «Последнее: {{date}}, {{zone}}» |
| 🍽 Food | «Сегодня: 3 приёма / ~950 ккал» | «За неделю: ~1200 ккал/день» |

### 4.3 Silent mode awareness

If customer has eating disorder silent mode active per [`wellness-food-handoff §10`](../handoffs/2026-05-19-wellness-food-handoff.md):
- Food card shows «Сегодня: 3 приёма пищи» (count only, no calories)
- Period summary: «За неделю: 18 приёмов»
- NO «ккал» / «нормы» displayed anywhere

Aggregator respects per-module silent / privacy modes.

### 4.4 Stale data indicator

If module's last data is > 7 days old (customer hasn't logged):
```
🌙 Сон  ⚪ Нет записей за 7 дней
Последнее: 14 мая (5 дней назад)
[Записать]
```

Gentle nudge, never punitive.

---

## 5. Cross-module observations

### 5.1 Eligibility

Cross-module observations require:
- ≥ 3 modules active
- Each module has ≥ 5 data points over the period
- Customer hasn't disabled cross-correlation (Q-WB13 / Q-WS6 lean: per-module per-service-category opt-in)

If eligibility not met → no «Что заметно» section shown.

### 5.2 Phase 2 simple-rules observation generator

Inputs: aggregated per-module summaries + customer's bookings in period.

Phase 2 MVP rules:
1. **Sleep + Mood correlation**: If correlation(quality_score, mood_quality) > 0.5 over 14d → «Лучше спите в дни когда отмечаете хорошее настроение»
2. **Water + Sleep correlation**: If days_with_low_water OR days_with_bad_sleep co-occur > 50% → «В дни с низкой водой — сон хуже»
3. **Symptom + Trigger correlation**: If 70%+ of «pain» events have shared trigger (e.g., «long_sitting») → «Боль чаще после долгого сидения»
4. **Mood weekly pattern**: If variance(mood) > 1.5 over 14d AND weekly pattern (e.g., Mon < weekend) → «Настроение ниже по понедельникам»
5. **Body change observation**: If weight or measurement changed > threshold over period → «Талия −{{N}} см за {{period}}» (NEUTRAL framing per Body anti-pattern)
6. **Sleep + Service correlation** (Phase 3+ if Q-WB13 enabled): «Лучше спите после массажа» (observational, ≥ 2 service-day pairs)
7. **Symptom + Service correlation** (Phase 3+): «Меньше {{symptom}} в недели с {{service}}» (observational)

Max 5 observations shown at once. Customer can «Все наблюдения →» to see full list (up to 20 historical).

### 5.3 Anti-patterns specific to observations

- ❌ Causal claims («благодаря лимфодренажу...»)
- ❌ Recommendations («продолжайте курс»)
- ❌ Single-direction framing («лучше / хуже»)
- ❌ Comparison with «average customer»
- ❌ Medical interpretation («это может быть...»)
- ❌ Streaks across modules
- ❌ «Sleep score» / aggregate health score

### 5.4 Phase 3+ ML observations

Out of scope MVP. When data accumulates:
- Per-customer ML model identifies non-obvious correlations
- «Странное наблюдение: чаще боль в шее в дни с высоким стрессом — но только в будни»
- Always paired with «AI не врач — это паттерн в данных»

### 5.5 Observation display rules

- Limit 3-5 visible inline; expandable to «Все →»
- Each observation has source modules (small badges)
- Each observation has confidence indicator (Phase 3+; Phase 2 binary)
- Customer can DISMISS observation (it doesn't return for 30 days)

---

## 6. Quick-capture row (always-visible)

### 6.1 Purpose

For active modules with daily / multi-daily cadence (Mood, Water, Sleep, Food). Reduces friction for daily logging.

### 6.2 Chip rules

Show chip if:
- Module active
- Today's data not logged (for daily modules) OR allows multiple events (Water, Food)
- Per [`information-architecture.md`](./information-architecture.md) state-adaptive home pattern

### 6.3 Per-module chips

| Module | Chip if eligible | Action |
|---|---|---|
| Mood | `[🙂 Как вы сегодня?]` | Opens 4-emoji quick prompt |
| Water | `[💧 +Стакан]` `[💧 +Кружка]` | Quick-log; chip remains visible until target |
| Sleep | `[🌙 Записать ночь]` (only if last night not logged) | Opens detail screen |
| Symptom | `[🩹 Записать симптом]` (always when active) | Opens add screen |
| Food | `[🍽 +Фото]` `[🍽 +Текст]` (max 3 events today) | Opens capture |
| Body | (no chip — bi-weekly cadence; per Body §6.3 anti-OCD principle) | n/a |
| Avatar | (no chip — customer-driven cadence) | n/a |

### 6.4 Maximum visible

Max 4-5 chips at once in row (don't crowd). Priority: not-yet-logged-today > frequent-logging > rarer.

### 6.5 Dismiss

Customer can swipe-dismiss a chip → hidden until tomorrow / next cycle.

---

## 7. Period filters

### 7.1 Available periods

- **Сегодня** (default for active session view)
- **Неделя** (default for «Что заметно»)
- **Месяц**
- **Квартал** (Phase 3+)

### 7.2 Period applies to

- Cross-module observations
- Module summary cards' «период summary» line
- Top-level «Сводка за период» display (compact mode §3.4-3.5)

### 7.3 Period stickiness

Customer's chosen period persists per session. Resets to default on next session start. Phase 3+ remember preference.

---

## 8. Module discovery section

### 8.1 «Можно добавить» card

Shows on layouts §3.2-3.4 (when < 7 modules active).

Each inactive module shown with:
- Icon
- One-sentence description
- `[Подключить →]` deep-link to module-specific consent dialog

### 8.2 Per-module activation entries

Per each module's handoff §3.1 (eligibility) + §4 (consent dialog):
- Tap «Подключить» → opens module-specific consent dialog
- Customer reads + accepts → module activates
- Customer returns to dashboard → module now in active list

### 8.3 NOT showing modules customer's not eligible for

- Customer < 18 years old → AI Avatar / Body / Food / Symptom hidden (per per-module §3.1)
- Tenant SUSPENDED → all modules show «Доступно после возобновления» state per [`tenant-suspension-pause-ux §3.1`](./tenant-suspension-pause-ux.md)

### 8.4 «Подключенные» link

When customer has ≥ 1 module → show link «Управлять модулями →» which navigates to [`customer-profile-management-ux §4`](./customer-profile-management-ux.md) Самочувствие card section.

---

## 9. Empty states

### 9.1 0 modules + customer just landed

Per §3.1 Discovery layout — full feature list with «Подключить» buttons.

### 9.2 0 modules + customer dismissed initial discovery

```
┌────────────────────────────────────────┐
│ Самочувствие                            │
├────────────────────────────────────────┤
│                                        │
│ Не подключено ни одного модуля.        │
│                                        │
│ Когда захотите — модули доступны       │
│ в Профиле → Самочувствие.              │
│                                        │
│ [Открыть профиль]                      │
│                                        │
└────────────────────────────────────────┘
```

Customer can navigate back via Профиль any time.

### 9.3 Modules active but no data yet (just activated)

```
┌────────────────────────────────────────┐
│ Самочувствие · {{today_date}}          │
├────────────────────────────────────────┤
│                                        │
│ 🙂 Настроение  Подключено               │
│ Запишите первый раз — данные появятся │
│ [Записать]                             │
│                                        │
│ 💧 Вода  Подключено                    │
│ Добавьте первый стакан                 │
│ [+ Стакан]                              │
│                                        │
└────────────────────────────────────────┘
```

### 9.4 Tenant SUSPENDED state

Per [`tenant-suspension-pause-ux §3.1`](./tenant-suspension-pause-ux.md) customer experience matrix — wellness modules show in read-only mode for existing data; new logging blocked. Dashboard shows:

```
┌────────────────────────────────────────┐
│ Самочувствие                            │
├────────────────────────────────────────┤
│ ⏸ Студия временно не работает          │
│                                        │
│ Ваши данные сохранены. Можете          │
│ просмотреть, но новые записи           │
│ временно приостановлены.               │
│                                        │
│ [Просмотреть существующие данные →]    │
│                                        │
└────────────────────────────────────────┘
```

---

## 10. API contracts

### 10.1 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/customer/wellness/dashboard` | Customer | Aggregated dashboard data (active modules + summary cards + observations) |
| GET | `/api/v1/customer/wellness/dashboard/observations` | Customer | Detailed cross-module observations (paginated) |

### 10.2 GET `/api/v1/customer/wellness/dashboard`

**Query**: `period_days` (default 7, max 90)

**Response** (200):
```json
{
  "layout": "aggregator",
  "active_modules_count": 4,
  "today_date": "2026-05-19",
  "tenant_state": "active",
  "active_modules": [
    {
      "module": "mood",
      "today_value": {"quality": 4, "logged_today": true},
      "period_summary": {"avg": 3.8, "data_points": 5, "data_period_days": 7}
    },
    {
      "module": "water",
      "today_value": {"consumed_ml": 1250, "target_ml": 2000, "percent": 62},
      "period_summary": {"avg_ml_per_day": 1850, "data_period_days": 7}
    },
    ...
  ],
  "inactive_modules": [
    {"module": "body", "available": true, "reason": null},
    {"module": "avatar", "available": false, "reason": "under_18"}
  ],
  "quick_capture_chips": [
    {"module": "mood", "action": "log_mood_today", "label": "🙂 Как вы сегодня?"},
    {"module": "water", "action": "add_glass", "label": "💧 +Стакан"}
  ],
  "observations": [
    {
      "id": "obs_abc123",
      "type": "cross_module",
      "modules": ["sleep", "mood"],
      "text": "Лучше спите в дни когда отмечаете хорошее настроение",
      "confidence": "medium",
      "period_days": 7,
      "generated_at": "2026-05-19T03:00:00Z"
    },
    ...
  ],
  "observations_count": 3
}
```

If silent mode active for any module → that module's `today_value` / `period_summary` excludes restricted fields (kcal/macros for food, etc.).

### 10.3 GET `/api/v1/customer/wellness/dashboard/observations`

**Query**: `period_days`, `limit` (default 20, max 50), `cursor` (pagination)

**Response** (200):
```json
{
  "observations": [
    {
      "id": "obs_abc123",
      "type": "cross_module",
      "modules": ["sleep", "mood"],
      "text": "Лучше спите в дни когда отмечаете хорошее настроение",
      "confidence": "medium",
      "period_days": 7,
      "generated_at": "2026-05-19T03:00:00Z",
      "dismissed_at": null
    },
    ...
  ],
  "has_more": false,
  "next_cursor": null
}
```

### 10.4 Dashboard caching

Per [`wellness-mood-handoff §12.1`](../handoffs/2026-05-19-wellness-mood-handoff.md) aggregator pattern:
- Daily Celery beat regenerates dashboard view (1×/day per customer)
- Cache lives in `customer.context["wellness_dashboard_cache"]`
- Real-time updates on per-module data change (event-driven invalidation)
- Customer can «pull-to-refresh» to force regenerate (rate-limited 1×/5 min)

---

## 11. Privacy enforcement

Same model as per-module handoffs (high-sensitivity wellness data).

### 11.1 API-level guards
- Customer-only access; 403 on tenant mismatch
- Cross-module observations computed customer-side from per-module data (no shared aggregate cache)

### 11.2 Master pre-arrival context
NEVER surfaces dashboard or any module data per existing privacy boundary.

### 11.3 Tenant aggregate (Phase 4+, opt-in only)
If platform ever does «N% of our customers track mood» — strictly opt-in + tenant-scoped + anonymized. NOT in MVP. Customer always sees own data only.

### 11.4 Founder access
Per [`ai-quality-observability §13`](./ai-quality-observability.md) — NO direct customer wellness data access. Legal hold + 4-eye approval only for extreme cases.

### 11.5 PII rules
Dashboard never displays customer's free-text notes from per-module logs (privacy preservation; notes accessible in module-specific section only with deliberate navigation).

### 11.6 Cross-tenant boundary
Customer's data scoped per-tenant per Q-CO5. If customer uses module at multiple salons → separate dashboards per tenant. No unified view across tenants.

---

## 12. Wellness Profile integration

### 12.1 Read access

Dashboard reads from Wellness Profile aggregated layers (Layer 3 Body State, Layer 6 Nutrition, Layer 7 Emotional, etc.) per [`core-wellness-profile.md`](./core-wellness-profile.md).

### 12.2 No write access from dashboard

Dashboard is READ-ONLY surface. All wellness writes happen via per-module APIs.

### 12.3 Observation generation

Cross-module observation generator reads Wellness Profile layers + per-module event tables; writes generated observations to `WellnessObservation` table (new — see §13).

---

## 13. New model

### 13.1 `WellnessObservation` (cross-module insights cache)

```python
class WellnessObservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='wellness_observations')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    TYPE_CHOICES = [
        ('cross_module', 'Cross-module correlation'),
        ('single_module', 'Single-module pattern (rare; per-module section usually shows these)'),
        ('service_correlation', 'Service + module correlation (Phase 3+)'),
    ]
    type = models.CharField(max_length=32, choices=TYPE_CHOICES)

    source_modules = models.JSONField(default=list)
    # ["sleep", "mood"] etc.

    text = models.TextField(max_length=280)
    # Russian-language observation. Generator constrained by FORBIDDEN-PHRASE rules.

    confidence = models.CharField(max_length=16, choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High')], default='medium')

    period_days = models.IntegerField()
    # Period the observation is based on

    valid_from = models.DateField()
    valid_to = models.DateField()
    # Window of observation validity

    dismissed_at = models.DateTimeField(null=True, blank=True)
    # Customer can dismiss; observation hidden for 30 days from dismissal

    generated_at = models.DateTimeField()
    # When observation was computed by aggregator

    aggregator_version = models.CharField(max_length=16, default='v1')
    # Tracking which observation generator version produced this

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['customer', '-generated_at']),
            Index(fields=['customer', 'dismissed_at']),
        ]
```

### 13.2 Generation

Daily Celery beat per customer with ≥ 3 active modules:
1. Read per-module aggregated data (last 30 days)
2. Apply simple-rules engine §5.2
3. Filter by FORBIDDEN-PHRASE list
4. Filter by customer's dismissed observations (don't regenerate same)
5. Limit to top 5 new + 15 historical max in DB
6. Emit `wellness.observation.generated` events

### 13.3 FORBIDDEN-PHRASE filter

Same forbidden phrases as Mood / Body / Sleep / Symptom / Food insight generators (combined):
- Diet/coaching: «прогресс», «цель», «нужно», «попробуйте»
- Medical: condition names, drugs, supplements
- Judgmental: «хорошо», «плохо», «лучше», «хуже» (use «больше/меньше», «выше/ниже»)
- Streaks: «N дней подряд», «не пропускайте»
- Comparisons: «средний клиент», «другие»

Generator rejects any observation containing forbidden phrase + alerts engineering for vocabulary tuning.

---

## 14. Events emitted

Per [`event-taxonomy.md §3.6`](./event-taxonomy.md#36-wellness-domain):

| Trigger | Event | Notes |
|---|---|---|
| Dashboard rendered (customer opens) | NEW: `wellness.dashboard.viewed` | analytics for engagement |
| Cross-module observation generated | NEW: `wellness.observation.generated` | source_modules, confidence, type |
| Observation dismissed by customer | NEW: `wellness.observation.dismissed` | for 30-day cool-down |
| Module quick-capture from dashboard chip | (per-module's `wellness.input.recorded`) | source='dashboard_chip' |

Add 3 NEW events to event-taxonomy.md §3.6.

---

## 15. Acceptance criteria (engineering checklist)

- [ ] `WellnessObservation` model + migration
- [ ] 2 API endpoints implemented + tested (`/dashboard`, `/dashboard/observations`)
- [ ] Customer auth required; tenant boundary; 403 on mismatch
- [ ] State-adaptive layout per §3 (5 layouts based on active_modules_count)
- [ ] Per-module summary cards §4 with stale-data + silent-mode handling
- [ ] Cross-module observation generator §5.2 with simple-rules MVP
- [ ] Generator FORBIDDEN-PHRASE filter §13.3 enforced
- [ ] Quick-capture chips per §6 (state-aware visibility)
- [ ] Period filters §7 (день / неделя / месяц)
- [ ] Module discovery section §8 with per-customer eligibility check (under-18, tenant suspended, etc.)
- [ ] Empty states §9 for all 4 variants
- [ ] Daily Celery beat for observation regeneration §10.4
- [ ] Real-time invalidation on per-module event
- [ ] Pull-to-refresh rate-limited 1×/5 min
- [ ] Events emitted §14
- [ ] Privacy enforcement §11 (cross-module observations strictly customer-only)
- [ ] Eating disorder silent mode awareness §4.3
- [ ] Tenant SUSPENDED state empty state §9.4
- [ ] Tests:
  - state-adaptive layout selection per active_modules_count
  - observation generator + FORBIDDEN-PHRASE filter rejection
  - cross-tenant denial
  - silent mode awareness
  - dismissed observation 30-day cool-down
  - quick-capture chip rules
- [ ] Accessibility audit on dashboard layouts + cards (WCAG 2.2 AA)
- [ ] Documentation in `apps/wellness/dashboard/README.md` referencing this handoff

---

## 16. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Aggregate «wellness score 78/100» | Single magic number — anti-pattern per §2.1 | Each module shown separately |
| Cross-module streak («7 дней всё отмечено!») | Anti-OCD principle stacks | NEVER streaks |
| «Complete your profile» nag | Profile is optional per §2.4 | Module discovery section is invitation, NOT nag |
| Comparison with other customers | Privacy + shame | NEVER per-customer comparison |
| Surface salon-side aggregated wellness | Privacy violation | Customer-only always |
| AI «recommendations» from cross-module observations | Coaching scope | Observational only — «заметила что...» |
| Causal claims («благодаря {{service}}...») | Correlation ≠ causation | Observational «после {{service}} в эти дни» |
| «You missed your sleep yesterday» framing | Shame | Stale data indicator §4.4 — neutral fact |
| Force chip dismissal — chip returns next day always | OCD pressure | Customer can dismiss; respects autonomy |
| Show 20+ observations at once | Cognitive overload | Max 5 inline; expandable «Все →» |
| Generate observations every 5 min | Cost + noise | Daily Celery beat + event-driven invalidation |
| Allow tenant to see customer's dashboard | Privacy violation | API enforces customer-only |
| Customer-pays gating on dashboard view | Wellness modules free per Q-WI12 | NEVER gate dashboard |
| Auto-translate observations to other languages MVP | Quality risk on legal-adjacent copy | RU only MVP; per-language re-author Phase 4+ |
| «Tip of the day» AI generated content | Coaching pattern | NEVER tips — observations only |
| Vibrate / haptic / sound on observation | Notification fatigue | Visual only on dashboard view |
| Auto-deliver new observations as bot DM | Push fatigue | Dashboard view only; per-module DM templates handle prompts |

---

## 17. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-WD1** | Layout transition animation when customer activates 3rd module — smooth or jump? | Smooth — fade observation section in over 1s; preserves continuity sense | UX | 🟢 |
| **Q-WD2** | Quick-capture row position — top or bottom? | Top per §3.3 — most-frequent action surface | UX | 🟢 |
| **Q-WD3** | Cross-module observations require ≥3 modules — could be 2 (sleep + mood as common pair)? | Phase 2 MVP: 3 modules (broader signal). Phase 3+ test relaxing to 2 if customer demand. | PM | 🟢 |
| **Q-WD4** | Observation dismissal — 30 days hide or permanent? | 30 days MVP; permanent dismiss button Phase 3+ for «never show again» customer feedback signal | UX | 🟢 |
| **Q-WD5** | Pull-to-refresh rate limit — 5 min too restrictive? | 5 min MVP (cost protection on observation regeneration); revisit if customer complaint | Eng + UX | 🟢 |
| **Q-WD6** | Period filter «Квартал» — Phase 2 or 3? | Phase 3 — Q1 MVP shorter windows for actionable insights | UX | 🟢 |
| **Q-WD7** | Observation generator confidence levels (low/medium/high) — display to customer or internal? | Phase 2: hide from customer (binary visible). Phase 3+ surface as visual indicator («наблюдение средней уверенности — нужны ещё данные») | UX | 🟢 |
| **Q-WD8** | Dashboard caching — daily Celery beat OR on-demand only? | Daily beat + event-driven invalidation; on-demand on customer's pull-to-refresh per Q-WD5 | Eng | 🟢 |
| **Q-WD9** | What if customer activates module then deactivates same day — does dashboard remember? | Yes — `inactive_modules` lists deactivated ones with «Активировать снова» option; previous data preserved per per-module soft-delete rules | Eng + UX | 🟡 |
| **Q-WD10** | Quick-capture chip from dashboard — opens modal OR navigates to module section? | Per chip:
- 1-tap quick (mood emoji): modal in dashboard
- Multi-step (sleep duration): navigate to module section | UX | 🟡 |
| **Q-WD11** | Observation generator runs for inactive tenants (PAUSED / SUSPENDED)? | NO — wait for ACTIVE state. Per [`tenant-suspension §3.1`](./tenant-suspension-pause-ux.md) customer-owned data preserved but no new generation. | Eng + Policy | 🟡 |
| **Q-WD12** | Customer's first observation generated — surface in bot DM as «вот первое наблюдение»? | NO Phase 2 — dashboard-only. Phase 3+ explore opt-in DM digest. | UX | 🟢 |
| **Q-WD13** | Multiple observations contradict each other — display all or filter? | Display all; customer's data, customer's interpretation. Don't pick favorites. | Policy | 🟡 |
| **Q-WD14** | Service correlation observations Phase 3+ — opt-in per service category OR auto? | Opt-in per Q-WB13 / Q-WS6 consistency; default OFF | Privacy + UX | 🟡 |
| **Q-WD15** | If customer has 7 modules + 20 observations — dashboard scroll-fatigue? | Max 5 inline + «Все →» pagination per §5.2; compact module cards per §3.5 power-user layout | UX | 🟢 |
| **Q-WD16** | Phase 3+ ML observations replace Phase 2 simple rules OR augment? | Augment — simple rules baseline always; ML adds nuanced patterns above. Customer doesn't see distinction. | Eng + AI | 🟢 |
| **Q-WD17** | Observation generator failure (LLM error / API timeout) — fallback? | Cache stale observations; suppress generation alerts internal; customer sees «Подождите, готовлю наблюдения» graceful banner | Eng | 🟡 |
| **Q-WD18** | Customer-pays tier Phase 3+ — premium observation features? | NO — dashboard always free per Q-WI12. Premium = AI Avatar Phase 3 + additional modules; dashboard is the wellness OS public-good surface | Founder | 🟢 |
| **Q-WD19** | Cross-tenant customer Q-CO5 — dashboard per-tenant separately? | YES — separate dashboards per tenant; customer chooses which tenant context when in multi-tenant Mini App (Phase 4+ unified switcher) | Privacy + UX | 🟡 |
| **Q-WD20** | If customer revokes all modules but observations are cached — show or purge? | Purge — cascade soft-delete of WellnessObservation rows on last-module revoke; hard-delete 30d later per Q-WI10 | Eng + Privacy | 🟡 |
| **Q-WD21** | Module summary card «Подробнее →» — deep-link behavior? | Direct navigation to module's section per IA; preserves back-button to Самочувствие | Eng + UX | 🟢 |
| **Q-WD22** | Quick-capture chip dismissed — return next day OR until next eligible event? | Next eligible event (e.g., water chip returns when last log > 2.5h per Water §7.5 smart timing) | UX | 🟢 |

---

## 18. Cross-document linkage

- [`wellness-input-modules.md`](./wellness-input-modules.md) — strategic 7-module foundation
- [`../handoffs/2026-05-19-wellness-*.md`](../handoffs/) — 7 per-module engineering handoffs feed dashboard
- [`information-architecture.md`](./information-architecture.md) — Mini App 5-surface IA; Самочувствие is one
- [`core-wellness-profile.md`](./core-wellness-profile.md) — 10-layer foundation that aggregator reads
- [`customer-profile-management-ux.md §4`](./customer-profile-management-ux.md) — Профиль → Самочувствие entry point
- [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md) — loading / error / empty patterns
- [`core-user-states.md`](./core-user-states.md) — customer state context
- [`conversational-ux-framework.md`](./conversational-ux-framework.md) — voice anchors for observation text
- [`product-ux-vision.md §1`](./product-ux-vision.md) — wellness OS positioning this surface embodies
- [`event-taxonomy.md §3.6`](./event-taxonomy.md#36-wellness-domain) — 3 NEW events §14
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) — SUSPENDED state §9.4
- [`master-conversational-templates.md §5.5`](./master-conversational-templates.md#55-customer-pre-arrival-context-surface) — privacy boundary master-side
- [`ai-quality-observability.md`](./ai-quality-observability.md) — founder access boundary §11.4
- [`../decisions-log.md`](../decisions-log.md) — Q-WI10/12, Q-CO5, Q-WB13, Q-WS6 cross-correlation

---

## 19. What this unblocks

- **Wellness OS positioning rendered in customer experience** — the «AI knows you» promise has a visible surface
- **Module discovery + activation flow** — single entry point for all 7 modules
- **Cross-module observations operational** — Phase 2 simple rules; Phase 3+ ML on top
- **Differentiation from competitors** — no booking platform has this aggregator
- **Wellness profile layers populated AND visible** — Profile data isn't just collected, customer sees value
- **State-adaptive UX** — power users + new users both well-served
- **Foundation for Phase 4+ predictive nudges** — observation infrastructure ready

## 20. What this does NOT unblock

- ❌ Single «wellness score» (forbidden per §2.1)
- ❌ Goal setting (Wellness Goals — separate doc; pending)
- ❌ Predictive recommendations (Phase 4+ ML on observations)
- ❌ Salon-side wellness aggregate (privacy)
- ❌ Customer-pays gating on dashboard (free always per Q-WD18)
- ❌ Multi-language (Phase 4+)
- ❌ Wearable integration (Phase 4+)
- ❌ Export / share (Phase 3+)
- ❌ Skip privacy audit on cross-module observation cache

---

## 21. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Wellness backend lead (apps/wellness/dashboard/) | ☐ | |
| Mini App frontend (Самочувствие tab — 5 layouts + cards + quick-capture row) | ☐ | |
| AI prompt engineering (observation generator + FORBIDDEN-PHRASE filter §13.3) | ☐ | |
| Privacy / Legal (cross-module observation customer-only enforcement) | ☐ | |
| Accessibility (WCAG 2.2 AA on 5 layouts) | ☐ | |
| Founder (Q-WD18 customer-pays gating + Q-WD14 service correlation opt-in policy) | ☐ | |
| Policy review (anti-pattern §16 enforcement) | ☐ | |

## Last verified
2026-05-19 (initial draft, Customer Wellness Dashboard locked as THE wellness OS surface; aggregates 7 wellness modules with state-adaptive layout + cross-module observations)
