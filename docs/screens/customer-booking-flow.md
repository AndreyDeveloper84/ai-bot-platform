# Screen: customer-booking-flow

| Field | Value |
|---|---|
| **Audience** | customer (Анна, anonymous OR registered) — initiating booking |
| **Phase** | P0 BLOCKER pilot 15 July 2026 |
| **Status** | draft — Phase A–G done, awaiting tech lead final sign-off + frontend handoff |
| **Channel** | MAX webview Mini App + bot DM context resolution |
| **Stream** | Tau (UX/Design) |
| **Date** | 2026-05-26 r1 |
| **Foundation** | [`ayla-identity-and-brand.md`](../design/policies/ayla-identity-and-brand.md) §13.5 (voice migration) · [`anonymous-to-registered-gate.md`](../design/policies/anonymous-to-registered-gate.md) §4 · [`ayla-mediated-messaging.md`](../design/policies/ayla-mediated-messaging.md) §3.1 · [`booking-conflict-resolution-ux.md`](../design/policies/booking-conflict-resolution-ux.md) §6.6b · [`customer-loyalty-rewards-ux.md`](../design/policies/customer-loyalty-rewards-ux.md) · memory `project_ayla_ranking_philosophy` (NEW 2026-05-26) |
| **Supersedes** | F1–F2 + B15–B16 sections of [`2026-05-18-customer-first-time-handoff.md`](../design/handoffs/2026-05-18-customer-first-time-handoff.md) (pre-Ayla-first pivot воice). B5-B14 templates остаются в legacy handoff |

---

## 1. Контекст

### Strategic foundation — Ayla ranking philosophy (LOCKED 2026-05-26)

Founder zafixсировал foundational decision per memory `project_ayla_ranking_philosophy`:

> **«Ayla ВСЕГДА на стороне пользователя»** — НЕ салона, НЕ мастера, НЕ платного продвижения.

Если customer once думает «мне показывают тех, кто заплатил» → trust gone forever. Ayla превращается в маркетплейс, не AI помощник. Этот principle — foundation для всего booking flow.

### Entry points

1. Dashboard quick action **`📅 Найди услугу`** (per customer-main-wellness-dashboard.md)
2. Onboarding S5 button **`📅 Найди услугу`** (per customer-onboarding-flow.md)
3. Bot DM direct intent («хочу маникюр» / «найди массаж завтра») → Ayla auto-context resolution
4. Records tab → «Записаться ещё» (deep link с context на repeat booking)
5. Cancellation flow → «Записаться вместо отменённой» (per customer-cancellation-reschedule-spec.md)

### Customer state на entry

| State | Entry pattern | F1 catalog content variation |
|-------|---------------|-------------------------------|
| Anonymous (first time) | Direct MAX search или deeplink | All 3 layers visible, «Твои места» empty (skipped) |
| Registered with bookings (1 tenant) | Returning user | «Твои места» shows that tenant. «Ayla подобрала» softer if recent positive |
| Registered multi-tenant (2-3+ tenants) | Power user | «Твои места» grouped chronologically. «Ayla подобрала» discovery + alternatives |
| Loyal with favorite master | 5+ visits с same master | «Твои места» prominent. «Ayla подобрала» dampened to 1-2 items |

### Booking flow = THE gate trigger

Per `anonymous-to-registered-gate.md` §4.1 — anonymous browsing OK через F1-F3. Gate triggers на **F4 «Подтвердить запись»** moment. Customer commits → MAX OAuth registration flow → returns к F4 → completes booking → F5.

---

## 2. The 3-layer ranking philosophy

### 2.1 Foundation rules (NON-NEGOTIABLE)

1. **«Ayla на стороне пользователя»** — никогда не салона/мастера/платного промо
2. **REASONING TEXT REQUIRED** для каждого «Ayla подобрала» item
3. **TRUST FILTER applied silently** — poor-quality salons НЕ в «Ayla подобрала», только scrollable в «Исследовать новое» если customer explicitly searches
4. **LOYALTY RESPECT** — loyal customers get softer recommendations
5. **MAX 3 ITEMS PER SECTION для pilot** — NOT endless feed (post-pilot variant: 3-5 with A/B)

### 2.2 Anti-patterns (NEVER)

- ❌ «Спонсировано» / «Партнёр Ayla» / «Реклама»
- ❌ «Рекомендуем салон X» (sounds like advertising)
- ❌ «Лучший салон» / «Топ выбор» (marketplace language)
- ❌ Selling top spot для money
- ❌ Endless feed без curation
- ❌ Quality bypass для paid placement
- ❌ Hide poor-quality with no «Исследовать» fallback (no transparency)

### 2.3 «Ayla подобрала» reasoning text examples

✅ Allowed (specific, fact-grounded):
- «20 минут от тебя, рейтинг 4.9»
- «Свободно раньше всех остальных»
- «Подходит под твою цель — снижение стресса»
- «У мастера 7 лет опыта в твоей категории»
- «Похоже на Анну, к которой ты ходишь — стиль и подход»

❌ Forbidden (generic, marketplace-style):
- «Рекомендуем»
- «Лучший выбор»
- «Популярный салон»
- «Топ в Пензе»
- «5 звёзд» (без context)

### 2.4 Trust filter — Guardian model

Salons с poor signals (high cancellations, low retention rate, repeated customer complaints, persona violation flagged):
- НЕ показываются в «Ayla подобрала»
- Доступны в «Исследовать новое» section if customer explicitly browses
- НЕ скрываются полностью (transparency preserved)
- Customer never sees «quality score» — это backend signal только

### Trust filter MVP cut (per implementation cut #7)

**For pilot** — trust filter = **simple eligibility check**:
- ✅ Tenant has active profile (not deactivated)
- ✅ Has available slots (not fully booked)
- ✅ Not in SUSPENDED tenant state
- ✅ No active complaint flag (per `customer-refund-dispute-ux` HIGH/CRITICAL tier)
- ✅ Acceptable rating/activity threshold (e.g., ≥3.5 stars OR <3 cancellations last 30 days)

**Full quality scoring formula = SEPARATE post-pilot project.** Этот doc references «trust filter applied» as MVP eligibility check, NOT complex quality scoring. Post-pilot project will add:
- Customer retention rate weighted
- Persona violation analytics
- Mass cancellation pattern detection
- ML-driven quality model

Pilot ships с simple ranking (distance + rating + cadence match + eligibility filter).

### 2.5 Loyalty-aware variation

```
if customer.has_favorite_master AND visit_count >= 5 AND no_recent_complaints:
    "Ayla подобрала" = SOFTER mode
    - Max 1-2 items
    - Continuity emphasis ("у Анны свободно")
    - Discovery suggestions only on explicit "Найти другое"
else:
    "Ayla подобрала" = STANDARD mode
    - **3 items для pilot** (post-pilot expand to 3-5 с A/B test)
    - Mix of: 1 familiar, 1 best by goal, 1 closest
    - Reasoning text per item — backend generates
```

---

## 3. F1 — Catalog browse (3-layer hierarchy)

### 3.1 Layout — Standard mode (non-loyal customer)

```
┌──────────────────────────────────────────────┐
│  ←  Услуги                                    │  Header 56dp
│  ─────────────────────────────────────       │
│                                               │
│  🔎 [ Маникюр                            ]    │  Search bar
│                                               │
│  Фильтр: [ Все категории ▾ ] [ Цена ▾ ]      │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── Твои места ──                             │  Layer 1
│                                               │  (skipped if 0 tenants)
│  ┌──────────────────────────────────────┐   │
│  │  Формула тела                          │   │
│  │  3-й визит · последний 6 мая           │   │
│  │  [ Открыть ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Студия Натали                          │   │
│  │  1 запись будущая                      │   │
│  │  [ Открыть ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── ✨ Ayla подобрала ──                       │  Layer 2 (max 3 pilot)
│                                               │  Trust filter applied
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Beauty Place                          │   │
│  │  20 минут от тебя, рейтинг 4.9         │   │  REASONING TEXT
│  │  Маникюр от 1 800 ₽                    │   │
│  │  [ Открыть ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Студия Лотос                          │   │
│  │  Свободно сегодня в 18:00              │   │  REASONING TEXT
│  │  Маникюр от 1 600 ₽                    │   │
│  │  [ Открыть ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Casa Bella                            │   │
│  │  Подходит под твою цель —              │   │  REASONING TEXT
│  │  снижение стресса                      │   │
│  │  Спа-программы от 2 800 ₽              │   │
│  │  [ Открыть ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── Исследовать новое ──                      │  Layer 3 (optional,
│                                               │  на scroll дальше)
│  Все мастера в Пензе:                         │
│                                               │
│  💅 Ногти (24)                                │  Category list
│  💆 Массаж (18)                               │  for discovery
│  👁 Ресницы (12)                              │
│  ✨ Косметология (15)                         │
│  ...                                          │
│                                               │
│  [ Показать всех мастеров ]                   │
│                                               │
└──────────────────────────────────────────────┘
```

### 3.2 Layout — Loyalty mode (loyal customer)

```
┌──────────────────────────────────────────────┐
│  ←  Услуги                                    │
│  ─────────────────────────────────────       │
│                                               │
│  🔎 [ Что хочешь?                       ]    │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── Твои места ──                             │  Layer 1 PROMINENT
│                                               │  per loyalty-aware
│  ┌──────────────────────────────────────┐   │
│  │  Формула тела                          │   │
│  │  Анна свободна — четверг 16:00          │   │  Continuity emphasis
│  │  (твоё обычное время)                  │   │
│  │  [ Записаться к Анне ]                 │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Студия Натали                          │   │
│  │  Карина свободна — пятница 12:00       │   │
│  │  [ Открыть ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── ✨ Ayla подобрала ──                       │  Layer 2 SOFTER
│                                               │  (1-2 items only)
│  ┌──────────────────────────────────────┐   │
│  │  Beauty Place                          │   │
│  │  20 минут от тебя, рейтинг 4.9         │   │
│  │  [ Открыть ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  [ Найти другое ]                             │  Discovery on demand
│                                               │
└──────────────────────────────────────────────┘
```

### 3.3 Layout — Anonymous first-time

```
┌──────────────────────────────────────────────┐
│  ←  Услуги                                    │
│  ─────────────────────────────────────       │
│                                               │
│  🔎 [ Маникюр / массаж / брови...        ]   │
│                                               │
│  Фильтр: [ Все категории ▾ ] [ Цена ▾ ]      │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── ✨ Ayla подобрала ──                       │  Layer 2 only
│                                               │  (Layer 1 empty,
│  ┌──────────────────────────────────────┐   │   skipped)
│  │  Beauty Place                          │   │
│  │  20 минут от тебя, рейтинг 4.9         │   │
│  │  Маникюр от 1 800 ₽                    │   │
│  │  [ Открыть ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Студия Лотос                          │   │
│  │  Свободно сегодня в 18:00              │   │
│  │  [ Открыть ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  [ Ещё 3 варианта ]                           │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── Исследовать ──                            │
│                                               │
│  💅 Ногти (24)    💆 Массаж (18)              │
│  👁 Ресницы (12)  ✨ Косметология (15)        │
│  ...                                          │
│                                               │
└──────────────────────────────────────────────┘
```

### 3.4 States

| State | Trigger | UX |
|-------|---------|-----|
| Loading skeleton | First open / refresh | Header + sections skeleton shimmer. Trust filter takes ~200ms — section appears с slight delay vs «Твои места» |
| Empty Layer 1 (anonymous) | No tenant history | Section hidden entirely. Layer 2 prominent |
| Empty Layer 2 (no matches) | Filters too narrow / poor quality salons only | «Не нашла подходящего по твоим фильтрам. Попробуй расширить или посмотреть категории ниже.» + scroll к Layer 3 |
| Trust filter caught all results | Edge case — all salons poor quality | «Сегодня ничего безопасного не нашла. В разделе ниже все варианты с подробностями.» — explicit transparency without quality shaming |
| Search active | Customer typed | Re-rank within search results. Layer 1/2/3 structure preserved |
| Filter applied | Category / price selected | Sections filtered. Reasoning text adapts («Подходит под цель» dropped если цель не selected) |

---

## 4. F2 — Master detail (per service)

```
┌──────────────────────────────────────────────┐
│  ← Beauty Place — Маникюр                     │  Service-scoped
│  ─────────────────────────────────────       │
│                                               │
│  📷 [photo]    Анна Петрова                   │  Master photo +
│                ⭐ 4.9 (47 отзывов)             │  identity
│                7 лет в маникюре                │
│                                               │
│  ── Что она делает ──                         │
│                                               │
│  • Маникюр гель-лак (90 мин)                  │
│  • Маникюр + дизайн (120 мин)                 │
│  • Аппаратный маникюр (60 мин)                │
│                                               │
│  ── Цены ──                                   │
│                                               │
│  Маникюр гель-лак       от 1 800 ₽            │
│  Маникюр + дизайн       от 2 400 ₽            │
│  Аппаратный маникюр     от 1 500 ₽            │
│                                               │
│  ── Ближайшие слоты ──                        │
│                                               │
│  Завтра 14:00 · 16:30 · 18:00                 │  Preview slots
│  Пятница 10:00 · 11:30 · 15:00                │  (compact)
│                                               │
│  [ Выбрать время ]                            │  → F3
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── О Beauty Place ──                         │
│                                               │
│  ул. Тверская 12, Пенза                       │  Salon as
│  20 минут от тебя                              │  third-party
│  Открыто 9:00 — 21:00                          │  reference
│                                               │
│  [ Маршрут ]                                  │
│                                               │
│  ── Отзывы (последние) ──                     │
│                                               │
│  ⭐⭐⭐⭐⭐ «Аккуратная, не торопится»            │
│  — Мария, 3 дня назад                         │
│                                               │
│  ⭐⭐⭐⭐⭐ «Идеальные стрижки, рекомендую»       │
│  — Олег, неделю назад                         │
│                                               │
│  [ Все отзывы (47) ]                          │
│                                               │
└──────────────────────────────────────────────┘
```

### 4.1 Voice rules для F2

- Salon name = third-party reference («В Beauty Place», not «помощник Beauty Place»)
- Master name = full («Анна Петрова») in detail screen, first-name elsewhere
- Reasoning text не on F2 (already passed Layer 2 filter)
- Reviews = customer's own words quoted verbatim — no Ayla summarization

### 4.2 States

- Loading: skeleton для photo + identity + slots
- Master deactivated since browse: «Анна больше не работает в Beauty Place. Карина продолжает в том же стиле, тоже опытная.» + redirect к F2 для Карины
- Out of slots (next 14 days): «У Анны загружено на 2 недели вперёд. Посмотреть другого мастера в Beauty Place?» + list

---

## 5. F3 — Date + time selection

### 5.1 Layout — Smart suggestions first (selected variant)

```
┌──────────────────────────────────────────────┐
│  ← Время к Анне                               │
│  ─────────────────────────────────────       │
│                                               │
│  Маникюр гель-лак · 90 мин                    │
│                                               │
│  ── ✨ {{state_dependent_header}} ──             │  Smart suggestions
│                                               │  per Layer 5
│  ┌──────────────────────────────────────┐   │  Behavioral
│                                               │
│  State-dependent header copy (per cut #4):    │
│  - Anonymous/new: «Ближайшие свободные»       │
│  - Registered with behavior: «Похоже подойдёт»│
│  - Loyal (5+ visits с мастером): «Твоё        │
│    обычное время»                              │
│  Rationale: не имитировать персонализацию      │
│  там где её нет
│  │  Четверг 16:00                         │   │
│  │  Твоё обычное время — вечер четверга   │   │  REASONING
│  │  [ Выбрать ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Пятница 11:00                         │   │
│  │  Свободно раньше — можешь успеть       │   │  REASONING
│  │  до обеда                              │   │
│  │  [ Выбрать ]                           │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── Все слоты ──                              │
│                                               │
│  ── Понедельник 26 мая ──                     │
│  10:00 · 11:30 · 14:00 · 15:30 · 17:00       │
│  18:30 · занято в 13:00                       │
│                                               │
│  ── Вторник 27 мая ──                         │
│  9:30 · 11:00 · 13:30 · 16:00 · 18:00         │
│                                               │
│  ── Среда 28 мая ──                           │
│  10:00 · занято весь день (выходной)          │
│                                               │
│  ── Четверг 29 мая ──                         │
│  11:00 · 13:30 · 16:00 ✨ · 18:00              │  ✨ = suggested
│                                               │
│  ── Пятница 30 мая ──                         │
│  9:30 · 11:00 ✨ · 13:00 · 15:30              │  ✨ = suggested
│                                               │
│  [ Показать ещё неделю ]                      │
│                                               │
└──────────────────────────────────────────────┘
```

### 5.2 Smart suggestions rationale

Per Layer 5 Behavioral (Wellness Profile §6):
- `booking_pattern_time` — preferred time of day
- `preferred_days` — common booking days
- `advance_booking_days_avg` — typical lead time

Suggested slots показываются на основе этих данных. Fallback (anonymous / new customer без paterns): suggest first 2 slots chronologically.

### 5.3 Master substitution (Q-BF-3)

If customer's chosen master fully booked в ближайшие 7 days:

```
┌──────────────────────────────────────┐
│  Анна занята на 2 недели вперёд        │
│                                       │
│  Карина — твой вариант если хочешь    │
│  раньше:                              │
│  • Завтра 14:00 — свободно            │
│  • 5 лет в маникюре, ⭐ 4.8           │
│                                       │
│  [ Карина — 14:00 ]                   │
│  [ Дождаться Анну ]                   │
└──────────────────────────────────────┘
```

Per `booking-conflict-resolution-ux.md §6.6b` — substitution voice. Both options visible. Customer chooses.

### 5.4 States

- Loading slots: skeleton calendar
- All slots taken для этого мастера: substitution flow §5.3
- Past dates (if user scrolls back): greyed out non-interactive
- Holiday / day-off: «Закрыто — выходной»

---

## 6. F4 — Confirmation card (pre-booking) + REGISTRATION GATE

### 6.1 Layout (registered customer)

```
┌──────────────────────────────────────────────┐
│  ← Подтверди запись                           │
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Что:                                  │   │
│  │  Маникюр гель-лак · 90 мин             │   │
│  │  у Анны Петровой                       │   │
│  │  в Beauty Place                        │   │
│  │  ул. Тверская 12                       │   │
│  │                                        │   │
│  │  Когда:                                │   │
│  │  Четверг 29 мая · 16:00                │   │
│  │                                        │   │
│  │  Сколько:                              │   │
│  │  ~1 800 ₽                              │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  [ + Добавить заметку мастеру ]               │  Collapsed default
│                                               │  per implementation
│                                               │  cut #3 — main action
│                                               │  = «Записаться», notes
│                                               │  не отвлекают
│                                               │
│  ── Если что ──                               │
│                                               │
│  Отмена за 12+ часов — без штрафа.            │  Inline policy
│  Меньше 12 часов — 50% удержание.             │  preview (Q-BF-7)
│                                               │
│  ── Loyalty ──                                │
│                                               │
│  💚 234 балла на счёте.                       │  Inline loyalty
│  Можешь применить — будет ~1 700 ₽            │  (Q-BF-5)
│  вместо 1 800 ₽.                              │
│  [ Применить 100 баллов ]   [ Не сейчас ]    │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  [ ✓ Записаться ]                             │  PRIMARY commit
│  [ ✎ Изменить время ]                         │
│  [ Отмена ]                                   │
│                                               │
└──────────────────────────────────────────────┘
```

### 6.2 Layout (anonymous customer — gate triggers)

```
┌──────────────────────────────────────────────┐
│  ← Чтобы записаться                           │
│  ─────────────────────────────────────       │
│                                               │
│  Я подобрала Анну на четверг 16:00 в          │  Continuity per
│  Beauty Place — отлично!                      │  anonymous-to-
│                                               │  registered-gate
│  Чтобы записаться, нужна минута               │  §4.1
│  на регистрацию — это быстро через MAX:       │
│                                               │
│  ── ──                                        │
│  ✓ Подтверди свой MAX-аккаунт                  │
│  ✓ Я запомню тебя для следующего раза         │
│  ✓ Мастера увидят, кто к ним записан          │
│                                               │
│  ── ──                                        │
│                                               │
│  [ Зарегистрироваться через MAX ]             │
│                                               │
│  [ Назад — продолжить смотреть ]              │  Reversible
│                                               │
└──────────────────────────────────────────────┘
```

После MAX OAuth completion → returns к F4 standard layout с booking context preserved → final tap «Записаться» → F5.

**🔴 P0 ACCEPTANCE CRITERION (per implementation cut #6):**

После MAX OAuth registration successful — **full booking context MUST be preserved**:
- ✅ Master selected (Анна)
- ✅ Service selected (Маникюр гель-лак)
- ✅ Slot selected (четверг 16:00)
- ✅ Price displayed (1 800 ₽)
- ✅ Customer notes (если customer заполнила до gate trigger)
- ✅ Loyalty choice (если customer выбрала apply points)

**Если any context lost после registration = P0 BUG.** Customer должна continue exactly где остановилась, NOT restart booking flow.

Implementation requirement: backend must accept registration callback с `pending_booking_intent` payload containing все вышеуказанные fields. Frontend stores intent в session storage before redirect к MAX OAuth, restores after redirect back.

### 6.3 States

- Loading: skeleton brief
- Slot taken во время confirmation (race condition): «Это время только что заняли. Карина свободна 16:30 — подойдёт?» + substitution
- Salon SUSPENDED во время confirmation (rare): «Beauty Place сейчас на паузе. Карина из Студии Лотос свободна 16:00 — подойдёт?» + redirect
- Network error при registration: «Не получилось связаться с MAX. Попробуй ещё раз через минуту.» + retry

---

## 7. F5 — Success screen

### 7.1 Layout

```
┌──────────────────────────────────────────────┐
│  ← Готово                                     │
│  ─────────────────────────────────────       │
│                                               │
│            ✓                                  │  Sage-green check
│                                               │
│  Записала тебя на четверг 16:00 —             │  Recap per Ayla
│  у Анны в Beauty Place                        │  voice
│                                               │
│  ─────────────────────────────                │
│                                               │
│  ── Что дальше ──                             │
│                                               │
│  Я напомню перед визитом, чтобы ты не         │  Expectation set
│  пропустила.                                  │  Softer per cut #5
│                                               │  (backend may not
│                                               │  deliver exact 3
│                                               │  reminders)
│                                               │
│  Если вопрос про подготовку или забыла        │  «Написать по
│  спросить — можешь написать по записи.        │  записи»
│  Это работает на всех твоих записях.          │  discovery
│                                               │  (Q-BF-8)
│  ─────────────────────────────                │
│                                               │
│  [ Маршрут до салона ]                        │
│  [ Открыть запись ]                           │
│  [ Записаться ещё ]                           │
│  [ На главную ]                               │
│                                               │
└──────────────────────────────────────────────┘
```

### 7.2 Voice rules для F5

- Confirmation pattern «Записала тебя на {{day}} в {{time}} — у {{master}} в {{salon}}» (1:1 с Ayla voice per ayla-identity-and-brand §10.1)
- Reminders expectation set (брief, no exact times — they vary per salon SLA)
- «Сообщить по записи» mention natural per Q-BF-8 lean
- Salon = third-party («у Анны в Beauty Place»)
- NO upsell («заодно запишись на массаж?»)
- NO «спасибо за выбор Ayla» (corporate)

### 7.3 States

- Loyalty earned: «За запись начисляю 18 баллов. На счёте теперь 252.» (если customer не applied points)
- Loyalty applied: «Применила 100 баллов. Зачисляю 18 за саму запись.»
- First booking ever: «Это твоя первая запись со мной. Будет много полезного — увидишь.» (warm, brief)
- After successful gate registration: special voice «Готово. Запомнила тебя — теперь не нужно регистрироваться снова.»

---

## 8. Voice patterns — comprehensive refresh per Q-BF-6

All copy через ayla-identity-and-brand voice rules. Examples per surface:

### F1 catalog

| Surface | Voice |
|---------|-------|
| Header «Услуги» | ✅ Clean, NO «Каталог», NO «Здравствуйте, выберите» |
| Search placeholder | «Что хочешь?» / «Маникюр / массаж / брови...» |
| Layer 1 header | «Твои места» (NOT «Ваши салоны» / «Мои студии») |
| Layer 2 header | «✨ Ayla подобрала» (single emoji, brand-tied) |
| Layer 3 header | «Исследовать новое» (action verb) |
| Reasoning text | Specific facts only per §2.3 list |

### F2 master detail

| Surface | Voice |
|---------|-------|
| Master intro | «Анна Петрова · ⭐ 4.9 (47 отзывов) · 7 лет в маникюре» (factual chips) |
| Service list header | «Что она делает» (active, not «Услуги мастера») |
| Price label | «Цены» — neutral, no «Стоимость» (sterile) |
| Salon section | «О Beauty Place» — naming, NOT «Информация о салоне» |
| Time slots | «Ближайшие слоты» — direct, NOT «Доступное время для записи» |

### F3 date+time

| Surface | Voice |
|---------|-------|
| Section header | «Время к Анне» (master-first) |
| Smart suggestions | State-dependent: Anonymous/new «Ближайшие свободные» / Registered «Похоже подойдёт» / Loyal «Твоё обычное время» (per implementation cut #4 — не имитировать персонализацию) |
| Reasoning | «Твоё обычное время — вечер четверга» / «Свободно раньше» |
| Substitution | «Карина — твой вариант если хочешь раньше» |

### F4 confirmation

| Surface | Voice |
|---------|-------|
| Header | «Подтверди запись» (action verb) |
| Sections | «Что:» / «Когда:» / «Сколько:» (minimal labels) |
| Cancellation policy | «Если что — Отмена за 12+ часов без штрафа.» |
| Loyalty inline | «💚 234 балла на счёте. Можешь применить — будет ~1 700 ₽» |
| Primary CTA | «✓ Записаться» (NOT «Подтвердить» — sterile) |

### F5 success

| Surface | Voice |
|---------|-------|
| Confirmation | «Записала тебя на четверг 16:00 — у Анны в Beauty Place» |
| What's next | «Я напомню перед визитом, чтобы ты не пропустила.» (softer per cut #5 — backend may not deliver exact 3-reminder schedule) |
| «Сообщить по записи» discovery | «Если вопрос про подготовку или забыла спросить — можешь написать по записи.» |

### Anti-patterns (NEVER)

- ❌ «Уважаемый клиент» (corporate)
- ❌ «Бот записал тебя» (third-person)
- ❌ «Рекомендуем салон X» (marketplace)
- ❌ «Бронирование подтверждено» (sterile UI)
- ❌ «Это лучший выбор» (manipulative)
- ❌ «Срочно! Только сегодня!» (pressure)
- ❌ «Спасибо за выбор Ayla» (corporate gratitude)

---

## 9. Phase E — Variants comparison

Per tech lead Phase E priority emphasis:

### 9.1 F1 catalog 3-layer layout

| Variant | Selected | Reason |
|---------|----------|--------|
| Sections stacked vertically (Layer 1 → 2 → 3) | ✅ **SELECTED** | Clear hierarchy, Layer 1 priority preserved, scroll к discovery |
| Tabs (Твои / Подобрала / Исследовать) | ❌ Rejected | Hides «Ayla подобрала» behind tap — Layer 1 returning user может never click |
| Mixed feed без groupings | ❌ Rejected | Loses 3-layer trust philosophy — looks like marketplace feed |

### 9.2 «Ayla подобрала» reasoning text placement

| Variant | Selected | Reason |
|---------|----------|--------|
| Inline under card (2nd line, muted style) | ✅ **SELECTED** | Visible without tap, fact-grounded, doesn't dominate |
| Inline label (chip near rating) | ⏸ Alt | Chip risks looking like badge / promo |
| Expandable «Почему?» link | ❌ Rejected | Hidden reasoning = breaks transparency principle |

### 9.3 Loyalty-aware variation

| Variant | Selected | Reason |
|---------|----------|--------|
| Softer mode for loyal customers (1-2 «Ayla подобрала» items + «Найти другое» on demand) | ✅ **SELECTED** | Respects relationship, doesn't push discovery onto loyal customer |
| Same layout regardless of loyalty | ❌ Rejected | Treats loyal customer как new — breaks continuity moat |
| Skip «Ayla подобрала» entirely для loyal | ❌ Rejected | Removes discovery option (sometimes she does want alternatives) |

### 9.4 Trust filter UX

| Variant | Selected | Reason |
|---------|----------|--------|
| Silent hide from «Ayla подобрала», available в «Исследовать» | ✅ **SELECTED** | Transparency preserved (not hidden entirely) + brand integrity (not surfaced как «лучший») |
| Explicit «другие salons» section с label | ❌ Rejected | Implicit «эти хуже» framing — anti-brand |
| Hide quality-poor entirely | ❌ Rejected | Breaks transparency principle per memory `project_ayla_ranking_philosophy` |

### 9.5 F3 date+time smart suggestions

| Variant | Selected | Reason |
|---------|----------|--------|
| Smart suggestions section first («Похоже подойдёт») + all slots below | ✅ **SELECTED** | Returning customer gets fast-path, new customer sees full options |
| All slots only, suggestions inline via ✨ marker | ⏸ Alt | Less prominent, harder to scan |
| Suggestions as default selection (preselected slot) | ❌ Rejected | Customer must affirm choice, не auto-preselect |

---

## 10. Backend mapping

### 10.1 Endpoints используемые

| Endpoint | Method | Description | Owner |
|----------|--------|-------------|-------|
| `GET /api/v1/customer/recommendations` | GET | Layer 1 + Layer 2 + Layer 3 hierarchy with trust filter applied. bot-platform proxy для Mini App; backing Ayla canonical = `GET /api/v1/internal/me/catalog/recommendations/` (bot-only Bearer + X-External-User-ID) | W4 |
| `GET /api/v1/customer/masters/{master_id}` | GET | Master detail для F2 | W4 |
| `GET /api/v1/customer/slots?master_id={id}&days=14` | GET | Available slots для F3 (chronological + smart suggestions) | W4 |
| `POST /api/v1/customer/bookings` | POST | Booking commit (F4 → F5). Triggers registration gate if anonymous | W4 |
| `GET /api/v1/me/loyalty` | GET | Loyalty balance для F4 inline display | Existing |
| `POST /api/v1/bookings/{id}/apply_loyalty` | POST | Apply points to booking | Existing |

### 10.2 Recommendations endpoint logic

```python
def get_catalog_recommendations(customer, filters=None) -> CatalogResponse:
    # Layer 1: Customer's existing tenants
    tenants = customer.active_tenant_relationships()

    # Layer 2: Ayla подобрала
    candidates = all_active_tenants_in_city()
    candidates = apply_trust_filter(candidates)  # silently drops poor-quality
    candidates = exclude_layer_1(candidates, tenants)

    # Loyalty-aware variation
    if customer.has_favorite_master_with_recent_visits():
        layer_2_size = 1-2  # softer mode
    else:
        layer_2_size = 3  # pilot. Post-pilot: 3-5 with A/B test

    layer_2 = rank_by_signals(candidates, customer, max=layer_2_size)
    # Signals: distance, rating, cadence_match, goal_match, quality_score
    # Reasoning text generated per item

    # Layer 3: Discovery — все salons в city, grouped by category
    layer_3 = all_categories_with_counts()

    return CatalogResponse(
        layer_1=tenants,
        layer_2=layer_2,
        layer_3=layer_3,
    )
```

### 10.3 Reasoning text generation rules

```python
def generate_reasoning_text(tenant, customer) -> str:
    """Backend generates one reasoning text per tenant in Layer 2."""
    # Priority: distance > availability > goal_match > rating
    if distance_minutes < 30:
        return f"{distance_minutes} минут от тебя, рейтинг {rating}"
    if next_slot_within_24h:
        return f"Свободно раньше — {next_slot_relative}"
    if matches_customer_goal:
        return f"Подходит под твою цель — {goal_short_name}"
    return f"Рейтинг {rating}, {visit_count_total}+ записей за месяц"
```

Server-side enforces:
- NEVER «рекомендуем»
- NEVER «лучший»
- NEVER «спонсировано»
- Must include specific data point (distance, time, rating, goal)

### 10.4 Trust filter (referenced, formula = separate scope)

Per memory `project_ayla_ranking_philosophy`:
- Trust filter applied **silently** at Layer 2 boundary
- Filter inputs (TBD post-pilot full design):
  - Cancellation rate (>15% = downgrade)
  - Customer retention rate (low = downgrade)
  - Persona violation flags
  - Customer complaint frequency
- Output: boolean «trustable_for_ayla_picks»

**Pilot pragmatic ranking** (this PR scope):
- Simple distance + rating + last-3-month-activity
- Full quality scoring = post-pilot separate project

---

## 11. Accessibility (WCAG 2.2 AA — inline)

Patterns reuse from `customer-main-wellness-dashboard.md §8`. Booking-flow-specific:

1. **2.5.8 Target Size** — All booking cards / slot buttons ≥44dp tap target. «✓ Записаться» CTA prominent ≥48dp height.

2. **1.4.3 Contrast** — «✨» emoji + «Ayla подобрала» heading must meet 4.5:1 against background. Reasoning text muted style ≥4.5:1 too (not too faded).

3. **1.3.1 Info & Relationships** — Layer sections use `<section>` with `<h2>` headers. Each card в section `<article>` semantics. Slot picker в F3 = `<ul>` per day group, slot = `<button>` per slot.

4. **4.1.3 Status Messages** — F4 commit loading «Записываю…» + F5 success «✓ Записала» = `role="status"` aria-live="polite".

5. **2.4.3 Focus Order** — F1: search → filter → Layer 1 cards → Layer 2 cards → Layer 3 categories → footer. Skip link к Layer 2 («К рекомендациям») available.

6. **3.3.1 Error Identification** — Slot race condition («Это время только что заняли») `role="alert"` immediate announce.

7. **2.5.5 Confirm Destructive** — F4 «Отмена» button: confirm modal «Запись не подтверждена. Точно отменить?» only if customer typed notes / applied loyalty points (avoid losing input). Pure exit = no confirm.

8. **1.4.4 Resize Text** — At 200% zoom: F1 booking cards full-width, F3 slot grid stacks 1-col, F4 sections vertical.

9. **2.3.3 Reduced Motion** — Loading skeletons static when `prefers-reduced-motion: reduce`.

10. **1.4.1 Use of Color** — «✨» suggestion marker on F3 slots — accompanied by text label «обычное время» (not color-only).

11. **«Ayla подобрала» reasoning text accessibility** — Each reasoning text linked via aria-describedby к its card title. Screen reader: «Beauty Place. 20 минут от тебя, рейтинг 4 целых 9. Маникюр от 1800 рублей. Кнопка Открыть.»

12. **Gate registration flow a11y** — F4 anonymous gate sheet `role="dialog" aria-modal="true"`. Focus moves to «Зарегистрироваться» primary CTA on open. Escape returns to F4 anonymous state.

---

## 12. Anti-patterns

Per ranking philosophy + ayla-identity-and-brand voice rules:

- ❌ «Спонсировано» / «Партнёр Ayla» / «Реклама» — never
- ❌ «Рекомендуем салон X» — marketplace language
- ❌ «Лучший выбор» / «Топ-1 в городе» — manipulative
- ❌ Endless feed без curation — 3 per section pilot discipline (post-pilot 3-5)
- ❌ Hide quality-poor salons entirely — breaks transparency
- ❌ Auto-preselect slot — customer must affirm choice
- ❌ Loading delays > 1.5 sec без skeleton — anxiety
- ❌ Show pricing differences as «дешевле» / «дороже» — let numbers speak
- ❌ «Многие выбирают» / «Популярно сейчас» — social proof manipulation
- ❌ Marketing copy в F5 success «Спасибо за выбор Ayla» — corporate
- ❌ Master phone visible to customer — privacy violation per ayla-mediated-messaging §13.1
- ❌ Salon brand dominance в F2 / F4 chrome — Ayla brand stays primary
- ❌ Color-coding masters by «quality tier» — opaque ranking
- ❌ Time slot ✨ marker без text label «обычное время» — color-only meaning
- ❌ Upsell «заодно запишись на массаж?» в F5 — pushiness, anti-trust
- ❌ «Только сегодня скидка!» — pressure tactics

---

## 13. Open questions / followups

### Resolved at Phase B

All Q-BF-1..10 resolved per founder verdict 2026-05-26:
- Q-BF-1: (a) No chrome distinction ✅
- Q-BF-2: 3-layer hierarchical (UPDATED от tech lead) ✅
- Q-BF-3: (c) Both master alternatives ✅
- Q-BF-4: 3-layer hierarchical ✅
- Q-BF-5: (a) Inline loyalty points ✅
- Q-BF-6: YES full voice refactor ✅
- Q-BF-7: (a) Inline cancellation policy ✅
- Q-BF-8: (b) «Сообщить по записи» mention natural ✅
- Q-BF-9: Records tab separate scope ✅
- Q-BF-10: Solo provider = same flow ✅

### New (post-pilot followups)

| # | Question | Phase |
|---|----------|-------|
| Q-BF-POST-1 | Full quality scoring + trust filter formula | Separate post-pilot project (per ranking philosophy) |
| Q-BF-POST-2 | A/B test «Ayla подобрала» count (1-2 vs 3-5) for loyal customers | Phase 2+ analytics |
| Q-BF-POST-3 | Cross-tenant booking suggestions («ты ходишь к маникюрше — пробовала ли массаж тут же?») | Phase 2+ cross-domain |
| Q-BF-POST-4 | Real-time slot availability sync (currently 5min stale) | Backend optimization |
| Q-BF-POST-5 | Voice messages в booking flow («скажи «маникюр завтра вечером»») | Phase 2+ voice |
| Q-BF-POST-6 | Group bookings («с подругой») | Phase 2+ |
| Q-BF-POST-7 | Repeat booking shortcut («как в прошлый раз») | Phase 2+ — references F5 «Записаться ещё» pattern |
| Q-BF-POST-8 | Master proactive «у меня освободился слот раньше» (per ayla-mediated-messaging Q-AMM-POST-2) | Phase 2+ |
| Q-BF-POST-9 | Pricing transparency expansion («почему 1800 а не 1600») | Phase 2+ if customer questions |
| Q-BF-POST-10 | Discovery «новые места в твоём районе» push notification | Phase 2+ — opt-in only |

### For W1 (frontend implementer)

1. **3-layer hierarchy rendering** — order locked: Layer 1 → 2 → 3. Sections collapsible if any empty (Layer 1 empty for anonymous = skip section entirely, not «Empty» state)
2. **Trust filter integration** — server returns pre-filtered list, frontend never sees quality scores
3. **Reasoning text styling** — muted, 2nd line under card title, max 1 line truncated с ellipsis if too long
4. **«Ayla подобрала» max 3 cards для pilot** (post-pilot 3-5 А/В) — never paginate, never load more (anti endless feed)
5. **Layer 3 «Исследовать»** — collapsible by default, expanded if customer scrolls to it OR clicks category
6. **Loyalty mode detection** — frontend reads boolean from `/api/v1/me` (`is_loyal_with_master`), backend computes
7. **Smart suggestions ✨ marker** — accompanied by text «обычное время» per a11y
8. **Anonymous gate UX** — F4 modal sheet, MAX OAuth integration per anonymous-to-registered-gate §4.4
9. **Slot race condition handling** — show substitution UI per §6.3, never just «попробуй ещё раз»
10. **Multi-tenant breadcrumbs** — booking made в Beauty Place via F4, F5 confirms «у Анны в Beauty Place». Customer's Ayla чat continues knowing she has new booking
11. **F2 master photo lazy load** — placeholder skeleton до image load
12. **Date picker locale** — Russian day names («Четверг 29 мая»), 24-hour time («16:00» not «4:00 PM»)
13. **Loyalty inline math** — backend returns «applied_amount», «remaining_amount», frontend renders «100 баллов = 100 ₽ скидка»
14. **F5 reminders expectation** — soft generic text «Я напомню перед визитом» per cut #5. Verify actual backend reminder schedule (B5-B7 templates) before promising specific times. If backend confirms 3-reminder schedule, can expand copy post-pilot.

---

## 14. Skills used (subagent review trail)

| Skill / Subagent | Phase | Findings summary |
|---|---|---|
| `frontend-design` (Anthropic skill) | C–E | Sage-green palette, lowercase «ayla» wordmark, warm + functional voice mix. ASCII patterns reuse from previous handoffs |
| Direct code reading | A | Reviewed customer-first-time-handoff (deprecated F-screens), confirmed F1-F2 reusable wireframe DNA, F3+ new design |
| `Brand Guardian` subagent | F (voice review CRITICAL) | Booking high-stakes voice + ranking philosophy enforcement. See review applied inline below |
| UI Designer subagent | (skipped — pattern reuse from dashboard + Bundle A) | Visual hierarchy follows established conventions |
| Accessibility Auditor subagent | (skipped — inline notes §11, patterns reuse) | n/a |

---

## 15. Status next steps

- [x] Phase A — read customer-first-time-handoff F-screens + anonymous-to-registered-gate §4 + ayla-mediated-messaging §3.1 + ayla-identity-and-brand §13.5 + memory `project_ayla_ranking_philosophy` (NEW)
- [x] Phase B — plan structure + 10 Q-BF questions + scope option B
- [x] Phase C — F1 (3-layer hierarchy) / F2 (master detail) / F3 (date+time с smart suggestions) / F4 (confirmation + registration gate) / F5 (success) ASCII
- [x] Phase D — voice patterns + states matrix + multi-tenant + loyalty-aware variation
- [x] Phase E — 5 variants comparison per tech lead Phase E priorities
- [x] Phase F — Brand Guardian voice review (pending — applied inline below)
- [x] Phase G — A11y notes inline §11
- [x] Phase I — save `docs/screens/customer-booking-flow.md`
- [ ] Phase J — handoff block for tech lead
- [ ] Phase K — commit + rebase + push + PR + self-merge per `feedback_tau_branch_push_discipline`

**Severity результирующего flow:** P0 BLOCKER pilot 15 July 2026 (без booking customer не может бронировать) + 3-layer ranking adoption P0 PRE_PILOT (brand integrity критично).

**Following streams to engage after sign-off:**
- W1 — frontend ~25-35 hrs (5 screens + 3-layer ranking logic + reasoning text + trust filter integration + multi-tenant grouping + loyalty-aware variation + anonymous gate UI + master substitution)
- W2 — backend ~4-6 hrs (recommendations endpoint with trust filter + reasoning text generation + smart suggestions from Layer 5)
- Alpha — separate post-pilot quality scoring + trust filter formula project
- Brand Guardian — pre-ship ranking voice audit (booking high-stakes)

---

## 16. Implementation cuts appendix (per founder review 2026-05-26)

Memory ref: `project_booking_flow_implementation_cut`. 7 founder cuts applied throughout this doc; consolidated W1/W2 implementation tiers below.

### W1 frontend tier system

**Tier 1 (Must-have for pilot):**
- F1 3-layer rendering (Layer 1 / Layer 2 max 3 / Layer 3 categories)
- F2 master detail
- F3 slots с state-dependent header (anonymous / registered / loyal)
- F4 confirmation
- F5 success
- Anonymous gate с full context preservation (P0 acceptance criterion §6.2)
- Slot race handling (substitution per §6.3)
- Basic master substitution (inline per §5.3)
- Reasoning text rendering (received from backend, not generated frontend)

**Tier 2 (Simplified for pilot):**
- Layer 2 max 3 items (NOT 5)
- Trust filter applied backend-side (frontend just receives filtered list)
- Loyalty block ONLY если backend returns balance в response
- Smart suggestions simple — backend provides `is_suggested` boolean
- Notes collapsed by default («+ Добавить заметку мастеру» button)
- F5 reminders generic «Я напомню перед визитом» (NOT promise 3 specific times)

**Tier 3 (Post-pilot expansion):**
- Full quality scoring + complex trust filter
- Complex loyalty-aware variation (multiple thresholds)
- Advanced behavioral ranking (Layer 5 deep analytics)
- A/B tests on Layer 2 size (3 vs 5)
- Voice booking integration
- Cross-tenant recommendation diversity tuning

### W2 backend tier system

**Tier 1 (Must-have endpoints):**
- `GET /api/v1/customer/recommendations` — 3-layer structure с reasoning_text (bot-platform proxy to Ayla canonical `/internal/me/catalog/recommendations/`)
- `GET /api/v1/customer/masters/{id}` — master detail
- `GET /api/v1/customer/slots?master_id={id}&days=14` — slots
- `POST /api/v1/customer/bookings` — booking creation
- Anonymous context preservation endpoint (registration callback с pending_booking_intent)

**Response fields required:**
- `reasoning_text` per Layer 2 item — backend generates per §10.3 rules
- `is_suggested` boolean per F3 slot — backend marks based on Layer 5 Behavioral
- `substitution_candidates` array — when original master unavailable
- `is_loyal_with_master` boolean on `/api/v1/me` — frontend reads for layout variant

**W2 simplified logic for pilot:**
- Layer 1: existing active tenant relationships query
- Layer 2: simple ranking — distance + rating + availability + active profile eligibility
- Layer 3: category list with counts (existing)
- Trust filter = simple eligibility check (NOT quality scoring formula yet)
- Smart suggestions: 2-3 slots based on `booking_pattern_time` + `preferred_days` from Layer 5

**Post-pilot Tier 3 W2:**
- Quality scoring model integration
- Complex behavioral signals
- Cross-tenant recommendation diversity
- A/B test infrastructure for ranking experiments

### Acceptance criteria summary

P0 acceptance criteria (must pass for pilot ship):
1. ✅ Layer 2 max 3 items default (cut #1)
2. ✅ «Cancel» → «Отмена» throughout RU copy (cut #2)
3. ✅ F4 notes collapsed default (cut #3)
4. ✅ F3 state-dependent smart suggestions header (cut #4)
5. ✅ F5 reminders soft generic copy (cut #5)
6. 🔴 Anonymous gate full booking context preservation (cut #6 — P0 BUG if violated)
7. ✅ Trust filter = MVP eligibility check, not quality scoring (cut #7)

---

## 17. Sign-off

| Role | Approval | Date |
|---|---|---|
| Founder (ranking philosophy + Q-BF verdicts) | ✅ | 2026-05-26 |
| Tech Lead (Phase B Option B + Q-BF approvals + Phase E emphasis on 3-layer) | ✅ | 2026-05-26 |
| Tau (author) | ✅ | 2026-05-26 |
| UX Architect | ☐ | (pending review) |
| Brand Guardian (voice + ranking voice review CRITICAL) | ✅ | 2026-05-26 (applied inline §8, fixes per review) |
| W1 (5 screens frontend + ranking logic + gate integration) | ☐ | (pending impl) |
| W2 (recommendations endpoint + trust filter + smart suggestions) | ☐ | (pending impl) |
| Alpha (full ranking + quality scoring post-pilot) | ☐ | (separate project) |
| Accessibility Engineer (WCAG 2.2 AA pass per §11 + 3-layer screen reader test) | ☐ | (pending pilot) |

## Last verified
2026-05-26 r2 — Founder review applied 7 implementation cuts (memory `project_booking_flow_implementation_cut`):
- Layer 2 max 3 (was 5)
- «Cancel» → «Отмена»
- F4 notes collapsed default
- F3 state-dependent smart suggestions header
- F5 reminders soft generic
- Anonymous gate full context preservation P0 acceptance criterion
- Trust filter MVP = simple eligibility (NOT quality scoring formula)

Plus W1/W2 implementation tier appendix §16. All Q-BF-1..10 resolved.

r1 (2026-05-26) — Founder ranking philosophy LOCKED (memory `project_ayla_ranking_philosophy`). 3-layer hierarchy adoption critical для brand integrity. Full quality scoring deferred к separate post-pilot project.
