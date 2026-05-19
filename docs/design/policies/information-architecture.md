# Information Architecture — Mini App (Wellness OS framing)

**Date:** 2026-05-18 r1
**Status:** Foundational — defines top-level navigation + surface inventory + state-adaptive home
**Reads:** [`product-ux-vision.md`](./product-ux-vision.md), [`core-user-states.md`](./core-user-states.md), [`core-wellness-profile.md`](./core-wellness-profile.md), [`user-journeys.md`](./user-journeys.md)

> Determines where every screen lives, what's primary vs secondary, and how the home surface morphs by user state.

---

## 0. Why current IA breaks under wellness OS

### The booking-first IA (current customer first-time handoff §F1-F4)
```
Mini App nav:
[Каталог] [Мастера] [Мои визиты] [Профиль]
```

Home screen = catalog grid. Primary path = browse → service → book → confirm.

### Why this is wrong for wellness OS

1. **Front-loads transactions, hides relationships.** Catalog as home tells customer «we're a service vendor», not «we're your wellness companion».
2. **Same for all users.** ACTIVE_REGULAR sees the same first screen as DISCOVERED — wasted opportunity for «как обычно?» one-tap rebook.
3. **No surface for state.** Customer has no way to see their wellness picture — Profile is settings, not insight.
4. **Catalog dominance.** Encourages decision fatigue («какую из 22 услуг?») when AI should be recommending.
5. **Profile is afterthought.** In wellness OS, the Profile (Wellness Profile) IS the product.

### What the new IA needs to deliver

- **Home is state-aware** (per Core User States)
- **Wellness surface is primary** (state of self, recommendations)
- **Catalog is secondary** (accessed when needed, not as home)
- **Profile is rich** (loyalty + history + preferences + wellness)
- **Bookings is operational** (upcoming + past + reschedule + cancel)
- **Settings is minimal** (preferences only — the «what we know about you» is in Profile)

---

## 1. New IA model — 5 surfaces

```
┌──────────────────────────────────────────────────────────────────┐
│                    Mini App (customer-facing)                     │
├──────────────────────────────────────────────────────────────────┤
│  Surface 1: ГЛАВНАЯ (Home)                                       │
│    state-adaptive surface — different content per state          │
│                                                                   │
│  Surface 2: САМОЧУВСТВИЕ (Self / Wellness)                       │
│    Wellness Profile dashboard: state, goals, recommendations     │
│                                                                   │
│  Surface 3: ЗАПИСИ (Bookings)                                    │
│    Operational: upcoming, past, reschedule, cancel                │
│                                                                   │
│  Surface 4: УСЛУГИ (Services / Catalog)                          │
│    Browse — when needed, not as home                              │
│                                                                   │
│  Surface 5: ПРОФИЛЬ (Profile)                                    │
│    Identity + Loyalty + Preferences + Settings                    │
└──────────────────────────────────────────────────────────────────┘
```

### Why 5 (not 4, not 6)
- 4 is the minimum for distinct concerns
- 5 separates «what's my state» from «what bookings» — different mental models
- 6+ creates navigation overload on mobile

### Bottom navigation (mobile, default)
```
[🏠 Главная] [💚 Самочувствие] [📅 Записи] [💅 Услуги] [👤 Я]
```

Note: production replaces emoji with Lucide icons (`home`, `heart-pulse`, `calendar`, `scissors`/`sparkles`, `user-circle`).

### Surface count by user state
- DISCOVERED: only Home visible; others hidden until first interaction
- EXPLORING: Home + Услуги visible
- PROBLEM_SEEKING + READY_TO_BOOK: full nav
- POST_VISIT through ACTIVE_REGULAR: full nav with Самочувствие emphasized
- AT_RISK_DRIFTING: full nav, but Главная is reactivation-themed
- DORMANT: minimal — Home only with «welcome back» framing

---

## 2. Surface 1 — ГЛАВНАЯ (state-adaptive home)

The most important screen. Adapts to user state. Different content per state per [`core-user-states.md`](./core-user-states.md).

### 2.1 — Home for DISCOVERED (first encounter, no actions)

```
┌────────────────────────────────────┐
│                                    │
│  Здравствуйте!                     │
│  Я помощник студии Карина          │
│                                    │
│  Помогу записаться, расскажу       │
│  о ценах и услугах,                │
│  отвечу на вопросы.                │
│                                    │
│  С чего начнём?                    │
│                                    │
│  ┌──────────────────────────────┐ │
│  │ 📅 Записаться                │ │
│  └──────────────────────────────┘ │
│  ┌──────────────────────────────┐ │
│  │ 💅 Услуги и цены             │ │
│  └──────────────────────────────┘ │
│  ┌──────────────────────────────┐ │
│  │ 👤 Наши мастера              │ │
│  └──────────────────────────────┘ │
│  ┌──────────────────────────────┐ │
│  │ ❓ Какой-то вопрос           │ │
│  └──────────────────────────────┘ │
│                                    │
└────────────────────────────────────┘

Bottom nav: hidden (no other surfaces until first action)
```

### 2.2 — Home for EXPLORING (browsing, low intent)

```
┌────────────────────────────────────┐
│  Студия Карина                     │
│                                    │
│  Выбирайте — посмотрите что нам    │
│  получается лучше всего            │
│                                    │
│  ── Популярные услуги ──           │
│  ┌──────────────┐ ┌──────────────┐│
│  │  💅 Маникюр  │ │ 💆 Массаж    ││
│  │  от 1 200 ₽  │ │ от 2 200 ₽   ││
│  └──────────────┘ └──────────────┘│
│  ┌──────────────┐ ┌──────────────┐│
│  │ 👁 Ресницы   │ │ ✨ Косметол. ││
│  └──────────────┘ └──────────────┘│
│                                    │
│  [Все услуги →]                    │
│                                    │
│  ── Наши мастера ──                │
│  [👤 Анна] [👤 Олег] [👤 Юля]      │
│                                    │
│  ── Что-то конкретное? ──          │
│  [Записаться] [Спросить помощника] │
│                                    │
└────────────────────────────────────┘

Bottom nav: [🏠 Главная] [💅 Услуги] [👤 Я]
```

### 2.3 — Home for PROBLEM_SEEKING

(Usually mid-conversation, but if customer opens Mini App after stating concern in bot:)

```
┌────────────────────────────────────┐
│  По вашему запросу                 │
│                                    │
│  Напряжение в шее ↓                │
│                                    │
│  ── Что обычно помогает ──         │
│  ┌──────────────────────────────┐ │
│  │ Массаж шеи и плечевого пояса │ │
│  │ 60 мин • 1 800 ₽             │ │
│  │ Анна — стаж 7 лет с шеей    │ │
│  │                              │ │
│  │ [Подробнее]  [Записаться →] │ │
│  └──────────────────────────────┘ │
│  ┌──────────────────────────────┐ │
│  │ Расслабляющий массаж         │ │
│  │ 90 мин • 2 800 ₽             │ │
│  │                              │ │
│  │ [Подробнее]  [Записаться →] │ │
│  └──────────────────────────────┘ │
│                                    │
│  Не подходит? [Рассказать больше] │
│  → возврат в чат с помощником      │
│                                    │
└────────────────────────────────────┘

Bottom nav: full
```

### 2.4 — Home for READY_TO_BOOK

```
┌────────────────────────────────────┐
│  Записать вас                      │
│                                    │
│  Массаж шеи и плеч • Анна          │
│                                    │
│  Когда удобно?                     │
│  ┌──────────────────────────────┐ │
│  │  Завтра                      │ │
│  │  10:00 • 14:30 • 17:00       │ │
│  └──────────────────────────────┘ │
│  ┌──────────────────────────────┐ │
│  │  Послезавтра                 │ │
│  │  11:30 • 15:00               │ │
│  └──────────────────────────────┘ │
│                                    │
│  [Выбрать другую дату →]           │
│  [Изменить мастера / услугу]       │
│                                    │
└────────────────────────────────────┘

Bottom nav: full (with «Записи» highlighted)
```

### 2.5 — Home for POST_VISIT

```
┌────────────────────────────────────┐
│  Спасибо, что были у нас!          │
│                                    │
│  Вчера: массаж шеи и плеч, Анна    │
│                                    │
│  ── Как помогло? ──                │
│  [😌 Гораздо лучше]                 │
│  [🙂 Чуть лучше]                    │
│  [😐 Так же]                        │
│  [😕 Не помогло]                    │
│                                    │
│  ── Уход после ──                  │
│  • Первые сутки избегайте резких   │
│    движений                        │
│  • Тёплый душ вечером              │
│  • Воды побольше — поможет         │
│                                    │
│  ── Когда следующий ──             │
│  Обычно для эффекта — раз в 2-3    │
│  недели. Записать на через 2 нед.? │
│  [Записать]  [Позже напомню]       │
│                                    │
└────────────────────────────────────┘

Bottom nav: full (with «Самочувствие» highlighted)
```

### 2.6 — Home for ACTIVE_REGULAR (the gold standard surface)

```
┌────────────────────────────────────┐
│  Привет, Анна!                     │
│                                    │
│  ── Самочувствие ──                │
│  💚 Прогресс за месяц              │
│  Массаж: 4 визита, всегда лучше    │
│  Сон: стабилен                     │
│  Стресс: ↓ снижается               │
│  [Подробнее →]                      │
│                                    │
│  ── Время ──                       │
│  Прошло 3 недели — обычное время.  │
│  Анна свободна:                    │
│  Четверг 16:00 • Пятница 11:00     │
│  [✓ Четверг 16:00]                 │
│  [✓ Пятница 11:00]                 │
│  [Другое время]                    │
│                                    │
│  ── Помощник предлагает ──         │
│  «Появилась новая услуга — sca-    │
│  массаж — думаю, вам подойдёт      │
│  как продолжение лимфодренажа.»    │
│  [Подробнее]  [Не сейчас]          │
│                                    │
└────────────────────────────────────┘

Bottom nav: full
```

**Key UX:** wellness summary at top, one-tap rebook in middle, optional recommendation at bottom. No catalog. No exploration. Everything is for THIS customer's known pattern.

### 2.7 — Home for AT_RISK_DRIFTING

```
┌────────────────────────────────────┐
│  Привет, Анна                       │
│                                    │
│  Давно не виделись —                │
│  последний раз в студии был месяц  │
│  назад.                            │
│                                    │
│  Если хотите — Анна свободна       │
│  в эти выходные.                   │
│  [Посмотреть слоты]                │
│                                    │
│  Если что-то поменялось —          │
│  расскажите.                       │
│  [Написать помощнику]              │
│                                    │
│  Можно и просто посмотреть         │
│  что у нас есть:                   │
│  [Услуги]                          │
│                                    │
└────────────────────────────────────┘

Bottom nav: full
NO promotional banners.
NO "у нас скидка!" elements.
Pure care framing.
```

### 2.8 — Home for DORMANT (rare; on opening after long silence)

```
┌────────────────────────────────────┐
│                                    │
│  С возвращением!                   │
│                                    │
│  Давно не виделись.                │
│  Если что-то нужно —               │
│  я рядом.                          │
│                                    │
│  [Записаться]                      │
│  [Просто посмотреть]               │
│  [Что нового у нас]                │
│                                    │
└────────────────────────────────────┘

Bottom nav: full
```

### Home rendering rules
1. Read user state (computed per `core-user-states.md`)
2. Choose layout variant
3. Fill data from Wellness Profile relevant layers
4. Apply persona tone modulation
5. Adapt density based on Emotional Layer (short-pref customer gets fewer cards)

---

## 3. Surface 2 — САМОЧУВСТВИЕ (Self / Wellness dashboard)

The wellness picture. NEW surface in this IA. Source of differentiation vs booking-first competitors.

### MVP version (Phase 1)
Limited data — just visit history + simple aggregates.

```
┌────────────────────────────────────┐
│  ← Самочувствие                    │
├────────────────────────────────────┤
│  ── Прогресс ──                    │
│  За последние 90 дней:             │
│  • 5 визитов                       │
│  • Самая частая услуга: массаж     │
│  • Средняя оценка: ★4.8            │
│                                    │
│  ── Цели ──                        │
│  (если customer не задал цель)     │
│  [Какая ваша цель?]                │
│                                    │
│  ── Закономерности ──              │
│  «После лимфодренажа у вас часто   │
│   улучшается сон.» (auto-detected) │
│                                    │
│  ── Рекомендации помощника ──      │
│  [Recommendation card]             │
│                                    │
└────────────────────────────────────┘
```

### v1.1+ version (Phase 2-3)
Adds:
- Body State indicators (energy / stress / sleep self-reported)
- Goal progress tracking
- Trend graphs (visits, satisfaction over time)
- Predicted next-visit suggestion
- Wellness habits (water tracking if opted in)

### Privacy controls (always visible at bottom)
```
[Что обо мне знает помощник]  — full Wellness Profile reveal
[Что забыть]  — granular fact removal
[Без рекомендаций]  — pause AI suggestions
```

### UX implication
This surface is the **«AI knows you»** moment. It must feel transparent (no surveillance creep) and useful (insight, not just data).

---

## 4. Surface 3 — ЗАПИСИ (Bookings)

Operational. List + actions for visits.

```
┌────────────────────────────────────┐
│  ← Записи                          │
├────────────────────────────────────┤
│  [Предстоящие] [Прошедшие] [Все]  │
├────────────────────────────────────┤
│  ── Предстоящие (1) ──             │
│  ┌──────────────────────────────┐ │
│  │ 22 мая, четверг, 15:30       │ │
│  │ Маникюр гель-лак • Анна      │ │
│  │ Адрес: ул. Тверская 12       │ │
│  │                              │ │
│  │ [Подробнее]  [Перенести]    │ │
│  │ [Отменить]   [Маршрут]      │ │
│  └──────────────────────────────┘ │
│                                    │
│  ── Прошедшие ──                   │
│  ┌──────────────────────────────┐ │
│  │ 28 апр, среда                │ │
│  │ Маникюр • Анна  ★★★★★         │ │
│  │ [Повторить эту запись]       │ │
│  └──────────────────────────────┘ │
│  ┌──────────────────────────────┐ │
│  │ 15 апр • Маникюр • Анна      │ │
│  └──────────────────────────────┘ │
│  [Больше →]                        │
└────────────────────────────────────┘
```

**Key UX behaviors:**
- «Повторить эту запись» one-tap → opens booking flow pre-filled with same master+service
- «Отменить» / «Перенести» without leaving Mini App (per existing booking handoff)
- Past visits link to Wellness Profile reaction notes (was this helpful?)

---

## 5. Surface 4 — УСЛУГИ (Services / Catalog)

**Demoted from primary to secondary.** Browse when needed, not as home.

```
┌────────────────────────────────────┐
│  ← Услуги                          │
├────────────────────────────────────┤
│  🔎 [Поиск услуги...]              │
│  Фильтр: [Все категории ▾]         │
├────────────────────────────────────┤
│  ── Для вашей цели ──              │
│  (if Goal layer populated)         │
│  «Снижение стресса»                │
│  • Расслабляющий массаж            │
│  • Лимфодренаж                     │
│  • SPA-ритуал                      │
│                                    │
│  ── Все категории ──               │
│  💅 Ногти (22)                     │
│  💆 Массаж (18)                    │
│  👁 Ресницы (12)                   │
│  ✨ Косметология (15)              │
│  ...                               │
└────────────────────────────────────┘
```

**Key UX shift:** «Для вашей цели» section is the new top — surfaces relevant services based on Wellness Profile Layer 2. Catalog still browsable but personalized first.

For DISCOVERED / EXPLORING users with no Goal data: skip «Для вашей цели», show category grid directly.

---

## 6. Surface 5 — ПРОФИЛЬ (Profile)

Identity + Loyalty + Preferences + Settings. Per existing customer first-time §F4 + Loyalty system §L1.

```
┌────────────────────────────────────┐
│  ← Профиль                         │
├────────────────────────────────────┤
│  Мария Иванова                     │
│  +7 ••• ••• 14 67                  │
│                                    │
│  🌹 Любимый клиент                 │
│  234 балла                         │
│  [Использовать при записи →]       │
│                                    │
│  ── Любимые ──                     │
│  Мастер: Анна                      │
│  Услуга: Маникюр гель-лак          │
│                                    │
│  ── Настройки помощника ──         │
│  ☑ Напоминания о визитах           │
│  ☑ Поздравления                     │
│  ☐ Промо-предложения               │
│                                    │
│  ── Что знает помощник ──          │
│  [Открыть Wellness Profile →]      │
│  (links to Surface 2)              │
│                                    │
│  ── Приватность ──                 │
│  [Что забыть]                      │
│  [Удалить все мои данные]          │
│                                    │
└────────────────────────────────────┘
```

**Important:** «Что знает помощник» button links to Surface 2 (Самочувствие) — Profile is identity + preferences, Самочувствие is the AI's understanding.

---

## 7. Navigation patterns

### Mobile bottom nav (default)
```
[🏠 Главная] [💚 Самочувствие] [📅 Записи] [💅 Услуги] [👤 Я]
```
- 5 tabs maximum (MAX UI lib limit similar to iOS)
- Active tab highlighted with accent color + label visible
- Inactive tabs: icon + label, muted color
- Per state: tabs may collapse (DISCOVERED shows fewer)

### Desktop / wide layout
- Bottom nav becomes left sidebar
- Same 5 surfaces, vertical layout
- Optional: combine «Самочувствие» + «Записи» in single «My Wellness» group

### Sub-navigation
- Each surface has its own internal nav (tabs, segmented controls)
- E.g., Записи has [Предстоящие / Прошедшие / Все]
- Услуги has filter chips + search

### Drill-down vs replace
- Tap a card → drill-down (back button returns)
- Surface switching via bottom nav → replace (no history stack between surfaces)

### Sticky elements
- Bottom nav: always visible except in modal flows (booking confirmation, full-screen feedback)
- Safe-area-inset-bottom respected (MAX has no MainButton — our sticky CTA owns this)

---

## 8. Deep-link routing under new IA

Per [`max-mini-apps.md`](../../../.claude/skills/ux-architect/references/platforms/max-mini-apps.md): start_param max 512 chars.

### Route format
```
start_param = <surface>_<sub>_<context>
```

### Examples
| Bot intent | start_param | Mini App lands at |
|---|---|---|
| New customer first open | (empty) | Home/DISCOVERED |
| Bot says «открой запись» | `book` | Home/READY_TO_BOOK (booking direct) |
| Bot says «запись к Анне на маникюр» | `book_master-123_service-456` | Booking pre-filled |
| Customer asks about wellness | `wellness` | Самочувствие |
| Reminder T-24h tap | `booking-789` | Записи / specific booking detail |
| Post-visit care | `postvisit_booking-789` | Home/POST_VISIT |
| Reactivation tap | `home` | Home/AT_RISK_DRIFTING |
| Profile from settings link | `profile` | Профиль |

### Fallback
- Unknown / malformed `start_param` → Home (state-detected)
- Expired context (e.g., booking deleted) → Home + toast «Эта запись больше не существует»

---

## 9. Migration from booking-first IA to wellness OS IA

This is the **transition plan** for existing customer first-time handoff and engineering teams.

### What stays (no breaking change)
- Booking flow: master → date → time → confirm (per F-screens existing)
- Profile editing: name / phone / preferences (extends, doesn't replace)
- Catalog data model: services + categories + master-mapping
- Bot DM templates: B1-B16 customer first-time

### What moves
- Old «Каталог» tab → new «Услуги» (same content, demoted from primary to secondary)
- Old «Мастера» tab → merged into Услуги (filter chip + sub-view)
- Old «Мои визиты» tab → new «Записи» (renamed, same content)
- Old «Профиль» tab → new «Профиль» (extends with loyalty + wellness link)

### What's new
- **Главная** — state-adaptive home (NEW surface)
- **Самочувствие** — wellness dashboard (NEW surface, MVP minimal + Phase 2 full)
- 5-tab nav (was 4)

### What's reframed
- Default Mini App entry: lands on Главная (was Каталог)
- Booking flow: still works as before but accessed from Home, not from catalog
- Post-visit care: Home displays it for POST_VISIT state (previously was bot-message-only)

### Engineering impact
- Frontend: add Главная + Самочувствие surfaces; restructure router
- Backend: add `getUserState()` endpoint; expand customer profile API to expose Wellness Profile layers
- AI: state-adaptive home content generation
- No data model breaking change — Wellness Profile schema additive

### Phased rollout
- **Phase 0 (now)**: ship existing 4-tab IA with new naming (Главная = state-adaptive home, Самочувствие as «coming soon» placeholder)
- **Phase 1 (+3 months)**: Самочувствие MVP (visit history + simple aggregates)
- **Phase 2 (+6 months)**: Самочувствие full (Body State + Goals + recommendations + trends)
- **Phase 3 (+12 months)**: adaptive UI density per Emotional Layer

---

## 10. Anti-slop scan

| # | Check | Status |
|---|---|---|
| 1 | Inter default | ✅ MAX UI / system |
| 2 | Purple gradient | ✅ salon-warmth |
| 3 | Glassmorphism | ✅ no glass |
| 4 | Radius scale | ✅ |
| 5 | Emoji decoration | ⚠ 🏠💚📅💅👤😌🙂😐😕 (nav + feedback + tier badges) — on production Lucide for nav (`home`, `heart-pulse`, `calendar`, `scissors`, `user`); feedback bubbles can keep emoji as user-side semantic; tier badges 🌹💎 OK as user-facing status icons (per Loyalty handoff) |
| 6 | Centered+CTA | ✅ Home variants don't fall into «centered single-CTA on gradient» pattern |
| 7 | AI illustrations | ✅ |
| 8 | Gradient overlay | ✅ |
| 9 | Specific copy | ✅ «Прошло 3 недели — обычное время» (specific), «Появилась новая услуга — думаю, вам подойдёт» (rationale-driven) |
| 10 | Real avatars | n/a / inherits from master directory |
| 11 | Animation restrained | ✅ state transitions fade only, no bouncy entries |
| 12 | Slate-on-slate | ✅ warm palette throughout |

**11/12 ✅, 1 fix (nav emoji → Lucide; tier/feedback emoji acceptable since semantic + customer-facing).**

---

## 11. A11y considerations

- 5-tab bottom nav: each tab has aria-label + label text visible (no icon-only)
- Active state communicated via aria-selected + visible accent (color + indicator bar)
- State-adaptive home: content changes per state; `aria-live="polite"` for the dynamic top section
- Mini App back navigation: MAX BackButton wired per state hierarchy
- Sticky CTA on Home/READY_TO_BOOK: respects safe-area-inset-bottom
- Surface switching: focus moves to surface heading on tab change
- Hierarchy: each surface has `<h1>` matching surface name; sub-sections `<h2>`
- All interactive elements meet 44dp/44pt touch target
- Bot DM links (start_param) accessible to assistive tech: target attributes correct

---

## 12. Edge cases

- **User opens Mini App from completely unrelated context** (random share): defaults to Home/DISCOVERED, no state-adaptive content surprises
- **State changes mid-session** (customer messages bot stating concern while Mini App open): state poll on focus; home refreshes if state changed
- **Customer has Mini App open in background, books via bot DM**: when Mini App reopens, Записи tab shows new booking; Home updates to POST_VISIT (if visit happened during background)
- **Network failure mid-state-fetch**: fall back to cached state from last session; surface non-blocking; primary booking flow remains accessible
- **DISCOVERED user direct-deeplinks to /profile**: redirect to Home with friendly «сначала познакомимся?» — Profile depth not earned yet
- **Permission denied** (e.g., customer tries Self/Wellness as anonymous): redirect to Home with explanation
- **Localized state names**: state keys remain English internally; display strings localized per language

---

## 13. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| Q-IA1 | Самочувствие surface — built in Phase 1 (basic) or deferred until Phase 2 (full)? | Phase 0 ships placeholder («скоро»); Phase 1 ships MVP basic (visit aggregates only); Phase 2 full | PM | 🟡 |
| Q-IA2 | 5-tab vs 4-tab — risk of too many on small screens? | 5-tab OK on MAX (MAX UI lib supports), but monitor analytics for tap distribution; collapse if low usage on some tabs | UX | 🟢 |
| Q-IA3 | State-adaptive home — what is the «default» if state-detection fails? | Home/EXPLORING variant (most generic but functional) | UX | 🟢 |
| Q-IA4 | Should ACTIVE_REGULAR see catalog at all on Home? | No — wellness-state + cadence + recommendation only; catalog one tap away via Услуги tab | PM | 🟢 |
| Q-IA5 | Услуги surface — show «по цели» first if Goal set, OR always category grid first with «по цели» as filter? | Goal first if set (compelling personalization); category grid for unset | UX | 🟡 |
| Q-IA6 | What if customer has multiple active goals? | Surface top 1 prominently, others accessible via «другие цели →» | UX | 🟢 |
| Q-IA7 | Surface caching strategy — cache state for offline use? | Cache state + last-known surface contents for 5 min offline; refresh on reconnect | Eng | 🟢 |
| Q-IA8 | Settings overflow — at what point split «Профиль» into Profile + Settings as separate surfaces? | Now: combined. Split if Profile growth > 10 sections. Not MVP concern. | UX | 🟢 |
| Q-IA9 | Self/Wellness surface privacy — visible by default or behind explicit opt-in? | Visible by default (it's user's own data); opt-out hides | PM | 🟡 |
| Q-IA10 | Booking flow entry — always via Home, or accessible directly via shared deeplink? | Both — deeplinks land at Home/READY_TO_BOOK pre-filled, but Home routing intermediate (allows abort cleanly) | Eng | 🟢 |

---

## 14. Cross-document linkage

- Vision: [`product-ux-vision.md`](./product-ux-vision.md) — IA serves these UX shifts
- States: [`core-user-states.md`](./core-user-states.md) — Home adapts per these 7 states
- Journeys: [`user-journeys.md`](./user-journeys.md) — IA routes between journey steps
- Profile: [`core-wellness-profile.md`](./core-wellness-profile.md) — Самочувствие surface reads from here
- Customer flows: [`../handoffs/2026-05-18-customer-first-time-handoff.md`](../handoffs/2026-05-18-customer-first-time-handoff.md) — F-screens reframed
- MAX platform: [`~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md`](~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md)
- Skill: bottom-nav considerations in `references/interaction.md`

## Last verified
2026-05-18 (founder roadmap step 7)
