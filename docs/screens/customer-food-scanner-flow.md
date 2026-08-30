# Screen: customer-food-scanner-flow

| Field | Value |
|---|---|
| **Audience** | customer (Анна, любой state — first-time and returning) |
| **Phase** | P0 — pilot 15 July 2026 (Penza) |
| **Status** | r2 — handed off to W1 (#164). Adds F0 photo consent, ED-mode branch, /дневник surface, loading-card string align |
| **Channel** | MAX webview (Mini App inside MAX messenger) |
| **Stream** | Tau (UX/Design) |
| **Date** | 2026-06-02 r2 |
| **Severity** | P0 BLOCKER — main magic moment per founder, без рабочего scanner dashboard quick action «📸 Сфотографируй еду» нечем оперировать |
| **Selected variant** | **Variant A — Wizard** (4 screens with MAX BackButton navigation) |
| **Tone foundation** | approximate / calm / supportive / editable / non-medical / action-oriented |

---

## 1. Контекст

### Entry points

1. **Dashboard quick action `📸 Сфотографируй еду`** — primary entry per `customer-main-wellness-dashboard.md` Block 3
2. **Dashboard empty state CTA `[ Еда ]`** — first-time user через onboarding nudge card
3. **Day tab → Питание → `+ Добавить приём`** — explicit logging entry (Phase 2+)
4. **AI insight CTA** (when applicable) — out of scope MVP, Phase 3+

**NOT this flow:**
- Bot DM photo path — already implemented Sprint 9 / DRF-818 в `apps/skills/food_scanner/skill.py`. Bot DM продолжает работать как secondary channel, не трогаем.

### Flow diagram

> **First-scan gate:** F0 photo-consent runs ONCE before customer's first scan
> (`food_photo_consent_at IS NULL`). Subsequent scans skip F0.

```
Dashboard 📸 quick action
        │
        ▼ (first scan only)
┌──────────────────┐    Не сейчас          ┌──────────────────┐
│ F0 — Photo       │ ────────────────────► │ Dashboard         │
│   consent        │                       └──────────────────┘
│ • accept/decline │
└────────┬─────────┘
         │ Хорошо, продолжим
         ▼
┌──────────────────┐    Отменить / Back   ┌──────────────────┐
│ F1 — Capture     │ ────────────────────►│ Dashboard         │
│ • meal-type chip │                      └──────────────────┘
│ • photo picker   │
│ • date select    │
└────────┬─────────┘
         │ Submit (scan_photo API call)
         ▼
┌──────────────────┐    Отменить           ┌──────────────────┐
│ F2 — Processing  │ ────────────────────► │ F1 (photo        │
│ • Узнаю что на   │   AbortController     │  preserved)      │
│   фото           │                       └──────────────────┘
└────────┬─────────┘
         │ scan_photo() returns ScanResponse
         │
         ├── FoodNotRecognizedError ──────► State: «Не разобралась»
         │                                  + переснять / вручную
         │
         ├── NutritionUnavailableError ───► State: «Сервис недоступен»
         │                                  + retry / вручную
         │
         └── ScanResponse OK
                  │
                  ▼
        ┌──────────────────┐
        │ F3 — Recognition │
        │   result + edit  │
        ├──────────────────┤
        │ ✅ Записать ────────────► log_meal() ──► F4
        │ ✏️ Уточнить ────► modal ─► F3 edited / F1
        │ ❌ Не то ────────► toast «Поняла» ──► Dashboard
        └──────────────────┘
                  │
                  ▼
        ┌──────────────────┐
        │ F4 — Saved       │
        │  confirmation +  │
        │  cross-domain    │
        │  insight (опц.)  │
        └──────────────────┘
                  │
                  ├──► Назад на главную (Dashboard)
                  └──► Ещё фото (F1)
```

### Voice foundation (founder principle)

> Food Scanner must not feel like a strict diet app. Ayla should not shame, judge or diagnose. Tone: approximate / calm / supportive / editable / non-medical / action-oriented.

**Vocabulary inventory:**
- ✅ Use: «примерно», «похоже на», «можно уточнить», «записала», «прикинула», «узнала», «заметила»
- ❌ Avoid: «слишком много», «вредно», «так нельзя», «у вас проблема», «лечит», «устраняет», quantifiers like «много/мало» as judgment

### Safety branch — ED mode (`user.eating_disorder_flag = true`)

> Per memory `cross-domain-insight-safety-gap` + `wellness-mvp-scaled-pilot` SCALED scope. When flag is set, Ayla backend returns no calorie/macro numbers. UI mirrors that: hides ккал/БЖУ across F3/F4 and /дневник, replaces with calm acknowledgment copy. No daily total bar. Customer never sees numbers, никаких «прогресс к норме». See §6bis for variant screens.

---

## 2. F0 — Photo consent (first scan only)

```
┌──────────────────────────────────────────────┐
│  ←  Фото и приватность                        │  Header 56dp
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │           📷                            │   │  Calm illustration
│  └──────────────────────────────────────┘   │
│                                               │
│  Прежде чем сделать первое фото —             │  Hook (Ayla voice)
│  короткое слово.                              │
│                                               │
│  Чтобы узнать блюдо, я отправлю фото          │  Body §1
│  в свой сервис распознавания. После           │
│  распознавания фото удаляется — я не          │
│  храню картинки.                              │
│                                               │
│  В дневник идёт только название блюда         │  Body §2
│  и примерные цифры.                           │  (scope limit)
│                                               │
│  Поменять можно в Профиль → Приватность.      │  Exit door
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Хорошо, продолжим                     │   │  Primary
│  └──────────────────────────────────────┘   │  → F1
│  ┌──────────────────────────────────────┐   │
│  │  Не сейчас                             │   │  Secondary
│  └──────────────────────────────────────┘   │  → Dashboard
│                                               │
└──────────────────────────────────────────────┘
```

### Implementation notes

- **Trigger:** F0 shows ONCE before customer's first scan. Detection: `BotUser.food_photo_consent_at IS NULL`. Set to `now()` on `Хорошо, продолжим` tap. Skip F0 on subsequent scans.
- **Coordination with onboarding S2 consent:** S2 (per `customer-onboarding-flow.md` §5) covers general data scope. F0 is photo-specific scope override per `wellness-mvp-scaled-pilot` SCALED variant. Both flags coexist: `consent_at` (general) + `food_photo_consent_at` (photo-specific).
- **«Не сейчас» path:** no error, no shame — silent return to dashboard. Customer keeps photo CTA visible; next scan re-triggers F0 (no 7d throttle MVP — re-confirm per attempt for honesty).
- **Withdrawal:** «Поменять можно в Профиль → Приватность» — points to `customer-profile-flow.md` privacy zone. When customer revokes there, `food_photo_consent_at` returns to NULL and next scan re-triggers F0.
- **No checkbox.** Active tap on primary button = explicit consent per 152-ФЗ + Brand Guardian "no pre-checked consent" rule.
- **Backend touch:** Alpha owns `BotUser.food_photo_consent_at: datetime | null` field migration. ~1h.
- **Anti-pattern:** ❌ legal wall, ❌ scary lawyer-speak, ❌ pre-checked consent, ❌ blocking dashboard return on «Не сейчас».

### Voice rules applied (F0)

| Element | Rule | Status |
|---|---|---|
| Hook «короткое слово» | Humanizes legal moment (reuse from onboarding S2) | ✅ |
| «отправлю фото в свой сервис распознавания» | First-person Ayla owns the data flow | ✅ |
| «После распознавания фото удаляется — я не храню картинки.» | Explicit retention scope, first-person | ✅ |
| «Поменять можно в Профиль → Приватность.» | Exit door from start | ✅ |
| Primary «Хорошо, продолжим» | Soft affirmative, не «Соглашаюсь» legalese | ✅ |
| Secondary «Не сейчас» | Dignity preserved, door open (mirrors onboarding S2 refused) | ✅ |

---

## 3. F1 — Capture

```
┌──────────────────────────────────────────────┐
│  ←  Скан еды                                  │  Header 56dp
│  ─────────────────────────────────────       │
│                                               │
│  Что ешь сейчас?                              │  Greeting 28dp
│                                               │
│  ── Когда ──                                  │
│                                               │
│  ┌────────┐ ┌─────────┐ ┌──────┐ ┌─────────┐ │  Meal-type chips
│  │ 🌅     │ │ 🥗      │ │ 🍽   │ │ 🍎      │ │  Default = current
│  │Завтрак │ │ Обед ●  │ │ Ужин │ │Перекус  │ │  local time bucket
│  └────────┘ └─────────┘ └──────┘ └─────────┘ │
│                                               │
│  ── Фото ──                                   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │                                        │   │
│  │           📷                            │   │  Photo capture zone
│  │      Сделай фото                       │   │  ~240dp tap area
│  │      или выбери из галереи             │   │
│  │                                        │   │
│  │   [ 📸 Сделать фото ]                  │   │  Web file picker:
│  │   [ 🖼 Из галереи ]                    │   │  accept="image/*"
│  │                                        │   │  capture="environment"
│  └──────────────────────────────────────┘   │
│                                               │
│  ── Дата ──                                   │
│                                               │
│  Сегодня ▾                                    │  Tap → 7-day picker
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  Фото нужно только чтобы узнать блюдо —       │  Privacy note
│  удаляю сразу.                                │  (first-person Ayla
│                                               │   per Brand Guardian)
└──────────────────────────────────────────────┘
```

### Implementation notes

- **Meal-type default by local time:** 04–11 → Завтрак, 11–16 → Обед, 16–22 → Ужин, 22–04 → Перекус
- **Web file picker (Q-TAU-F2):** `<input type="file" accept="image/*" capture="environment">` для camera, `<input type="file" accept="image/*">` для gallery. Two separate inputs, two CTAs.
- **No retention checkbox** (Q-TAU-F3) — photo используется только для recognition, удаляется после
- **Date picker:** dropdown 7 days back (today + 6 предыдущих). За границы — не позволяем
- **Privacy note** в bottom muted — first-person «удаляю сразу» (не sterile «удаляется»)

---

## 4. F2 — Processing

```
┌──────────────────────────────────────────────┐
│  ←  Распознавание                             │  Back disabled
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │     [photo preview ~140dp]             │   │  Downscaled photo
│  └──────────────────────────────────────┘   │
│                                               │
│                                               │
│        👀 Распознаю…                          │  Ayla state line
│                                               │
│              ●                                │  Pulsing dots
│            ●   ●                              │  (sage-green,
│              ●                                │   reduced-motion =
│                                               │   static)
│                                               │
│  Если занимает дольше обычного, можешь        │  Shown after 3s
│  отменить — фото останется на месте.          │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Отменить                              │   │  → F1 (photo
│  └──────────────────────────────────────┘   │  preserved)
│                                               │
└──────────────────────────────────────────────┘
```

### Implementation notes

- **Loading line canon:** «👀 Распознаю…» (r2 — replaces earlier «Узнаю что на фото»). Single emoji prefix (👀), first-person feminine, present action, ellipsis signals in-flight. Aria: emoji `aria-hidden="true"`, text «Распознаю» in `role="status" aria-live="polite"`.
- **Initial state (0-3s):** только Ayla line + loading dots, без cancel button
- **After 3s delay:** auto-surface «Если занимает дольше...» + Cancel button
- **Cancel mechanics:** `AbortController.abort()` на httpx request frontend-side. If backend response returns после cancel — UI ignores (state = `cancelled`)
- **Timeout 10s** (matches `DEFAULT_TIMEOUT_S` в nutrition_client) → automatic transition to «API down» state
- **Reduced-motion:** `prefers-reduced-motion: reduce` → static dots без анимации
- **Photo preview** downscaled ~140dp — customer видит что Ayla работает с её фото

---

## 5. F3 — Recognition result + edit

### F3 — high confidence (≥0.6)

```
┌──────────────────────────────────────────────┐
│  ←  Распознанное                       ⋯     │  ⋯ → «Начать заново»
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │     [photo thumbnail ~80dp]            │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  Узнала: гречка с курицей                     │  ≥0.6 confident
│                                               │  (per skill.py)
│                                               │
│  ── Примерно ──                               │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Порция: 150 г                         │   │  −/+ portion buttons
│  │  ┌───┐         ┌───┐                  │   │  steps 0.25×
│  │  │ − │  100%   │ + │                  │   │  range 0.5×-2.0×
│  │  └───┘         └───┘                  │   │
│  ├──────────────────────────────────────┤   │
│  │  Калории: ~480 ккал                   │   │  «~» literal
│  │  Б 35 · Ж 8 · У 50 г                  │   │  approximate signal
│  └──────────────────────────────────────┘   │
│                                               │
│  ── Когда ──                                  │
│                                               │
│  ┌────────┐ ┌─────────┐ ┌──────┐ ┌─────────┐ │  Meal-type editable
│  │ 🌅     │ │ 🥗 ●     │ │ 🍽   │ │ 🍎      │ │  (preserves F1 choice
│  │Завтрак │ │ Обед    │ │ Ужин │ │Перекус  │ │   but customer can
│  └────────┘ └─────────┘ └──────┘ └─────────┘ │   change here)
│                                               │
│  Заметка (необязательно):                     │
│  ┌──────────────────────────────────────┐   │
│  │                                        │   │  Free-text optional
│  └──────────────────────────────────────┘   │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ✅ Записать в дневник                 │   │  Primary
│  └──────────────────────────────────────┘   │  → log_meal()
│  ┌──────────────────────────────────────┐   │
│  │  ✏️ Уточнить                          │   │  Secondary → modal
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │  ❌ Не то                              │   │  Tertiary → reject
│  └──────────────────────────────────────┘   │  + toast → dashboard
└──────────────────────────────────────────────┘
```

### F3 — low confidence (<0.6)

Same layout, but:
- Lead: `Похоже на: гречка с курицей.` (hedge per skill.py logic)
- Subtle sage-green border highlight on `✏️ Уточнить` button
- Tooltip near portion: «Прикинула приблизительно — давай уточним вместе» (per Brand Guardian — collaborative tone)

### Implementation notes

- **Confidence threshold** per skill.py: `< 0.6` → «Похоже на», `≥ 0.6` → «Узнала»
- **Portion buttons:**
  - Display value as «100%» (steps: 50% / 75% / 100% / 125% / 150% / 175% / 200%)
  - Underlying grams change synchronously: 150g → −25% → 113g, etc.
  - Calories recalculate locally (per founder Q1 — if backend doesn't support recalc, UI остаётся honest «~»)
- **Meal-type на F3 editable:** customer могла выбрать «Обед» на F1, на F3 может поменять. Preserves last choice.
- **Заметка optional:** placeholder «(необязательно)», empty по умолчанию
- **3 buttons stack (не horizontal):** large touch targets, clear hierarchy (primary / secondary / tertiary)

---

## 6. F3-Clarify Modal (overlay)

Triggered by tap on `✏️ Уточнить`:

```
        F3 dimmed (overlay 60% black)
┌──────────────────────────────────────────────┐
│                                               │
│        ┌──────────────────────────────┐      │
│        │                              │      │
│        │  Что поправить?              │      │
│        │                              │      │
│        │  ┌──────────────────────┐   │      │
│        │  │ ✎ Название блюда     │   │      │  → close modal
│        │  └──────────────────────┘   │      │     + dish_name field
│        │                              │      │     становится editable
│        │  ┌──────────────────────┐   │      │
│        │  │ ⚖ Вес / порцию       │   │      │  → close modal
│        │  └──────────────────────┘   │      │     + scroll to portion
│        │                              │      │     + visual highlight
│        │  ┌──────────────────────┐   │      │
│        │  │ 📷 Сделать новое     │   │      │  → navigate F1
│        │  │     фото              │   │      │     photo cleared
│        │  └──────────────────────┘   │      │     meal-type preserved
│        │                              │      │
│        │  ┌──────────────────────┐   │      │
│        │  │ Отмена                │   │      │  → close modal
│        │  └──────────────────────┘   │      │     stay on F3
│        │                              │      │
│        └──────────────────────────────┘      │
│                                               │
└──────────────────────────────────────────────┘
```

### Implementation notes

- **3 correction paths** per Q-TAU-F5 founder decision
- **Path 1 (Название):** dish_name text field на F3 → text input. On save → `log_meal(dish_name=..., scan_id=None)` per Q-TAU-F5
- **Path 2 (Вес/порцию):** auto-scroll + highlight portion buttons на F3
- **Path 3 (Новое фото):** navigate to F1, photo cleared, meal-type chip preserved
- **Backend note:** при dish_name override macros recalculation TBD — backend Q1 founder. UI remains honest with «~» framing.

---

## 7. F4 — Saved confirmation

```
┌──────────────────────────────────────────────┐
│  ←  Записано                                  │  Back → Dashboard
│  ─────────────────────────────────────       │
│                                               │
│            ✓ Записала                         │  Brief confirmation
│                                               │  sage-green check
│                                               │
│  Гречка с курицей — ~480 ккал.                │  Recap line
│                                               │
│  ── Сегодня ──                                │
│                                               │
│  1 720 / 2 100 ккал                           │  Updated daily total
│  ▓▓▓▓▓▓▓▓▓▓▓░░░  82 %                         │  after this log
│  Б 95 · Ж 48 · У 170 г                        │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Открыть дневник                       │   │  Primary
│  └──────────────────────────────────────┘   │  → День → Питание
│  ┌──────────────────────────────────────┐   │
│  │  Готово                                │   │  Secondary
│  └──────────────────────────────────────┘   │  → Dashboard
│                                               │
└──────────────────────────────────────────────┘
```

### Implementation notes

- **«✓ Записала»** — single check, first-person feminine, brief
- **Recap line** — что именно записано, с «~» literal как visual approximate signal
- **Daily total** — после log, refresh от `daily_summary()`. Customer видит her contribution.
- **NO cross-domain insight card** (Q-BACK-4 verdict 2026-05-25): bot-platform-side не рендерит cross-domain insight cards в MVP. Insight engine architecturally lives Ayla-side, anti-medical safety filter ещё не в production. Sample rule (e.g., «vit-D deficit 5d → argan-oil massage») already medical-adjacent architecturally. Re-introduce post-pilot после Alpha safety audit templates + content gates.
- **Action buttons:**
  - Primary `[ Открыть дневник ]` → navigate to День → Питание tab (full food log view)
  - Secondary `[ Готово ]` → return to dashboard

---

## 8. ED-mode variants (`user.eating_disorder_flag = true`)

> **Trigger:** Backend flag `eating_disorder_flag` set via post-pilot anketa OR explicit Профиль → Питание setting (MVP: founder Q post-pilot). When true, Ayla returns nutrition payload with `nutrition = null` AND `display_numbers = false`. UI MUST hide all calorie/macro numbers and replace metric framing with calm acknowledgment. See memory `cross-domain-insight-safety-gap` для safety lineage.

### F3 — ED variant

```
┌──────────────────────────────────────────────┐
│  ←  Распознанное                       ⋯     │
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │     [photo thumbnail ~80dp]            │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  Узнала: гречка с курицей                     │  Dish name kept
│                                               │  (recognition value)
│  Питание не оцениваю — это безопасно.         │  ED-mode line
│                                               │  (replaces metrics)
│                                               │
│  ── Порция ──                                 │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Размер: обычный   ▾                  │   │  Discrete chips:
│  │  ( поменьше · обычный · побольше )   │   │  no grams, no %
│  └──────────────────────────────────────┘   │
│                                               │
│  ── Когда ──                                  │
│                                               │
│  ┌────────┐ ┌─────────┐ ┌──────┐ ┌─────────┐ │  Meal-type editable
│  │ 🌅     │ │ 🥗 ●     │ │ 🍽   │ │ 🍎      │ │
│  │Завтрак │ │ Обед    │ │ Ужин │ │Перекус  │ │
│  └────────┘ └─────────┘ └──────┘ └─────────┘ │
│                                               │
│  Заметка (необязательно):                     │
│  ┌──────────────────────────────────────┐   │
│  │                                        │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Записать в дневник                    │   │  Primary (no ✓ chrome
│  └──────────────────────────────────────┘   │   to keep neutral)
│  ┌──────────────────────────────────────┐   │
│  │  Уточнить                              │   │  Secondary
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │  Не то                                 │   │  Tertiary
│  └──────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
```

### F4 — ED variant

```
┌──────────────────────────────────────────────┐
│  ←  Записано                                  │
│  ─────────────────────────────────────       │
│                                               │
│            Записала                           │  No ✓ chrome,
│                                               │  no celebrative
│                                               │  visual
│  Гречка с курицей.                            │  Recap WITHOUT ккал
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Открыть дневник                       │   │  Primary
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │  Готово                                │   │  Secondary
│  └──────────────────────────────────────┘   │
│                                               │
└──────────────────────────────────────────────┘
```

### Implementation notes

- **Backend signal:** `ScanResponse.display_numbers = false` (Alpha owns flag on response envelope). UI MUST honor independent of `nutrition` payload presence. If `display_numbers == false`, hide ALL of: «~480 ккал» line, «Б 35 · Ж 8 · У 50 г» line, daily total bar, percent indicator.
- **Portion control swap:** discrete chips «поменьше · обычный · побольше» (3 options) replace «−/100%/+» numeric stepper. No gram weights shown. Submit translates to `portion_multiplier` (0.75 / 1.0 / 1.25) backend-side.
- **F3 line «Питание не оцениваю — это безопасно.»** — Ayla owns the calm framing. NOT «вы попросили скрыть» (deflecting) — Ayla owns the choice.
- **F4 «Записала.»** — single word + period. NO «✓» chrome (celebrative tick reads as approval-of-amount). NO daily total bar. NO weekly progress integration.
- **/дневник entries** (см. §11) also hide ккал в ED-mode.
- **Reject toast** unchanged — REJECTED_ACK already neutral.
- **Not Recognized / API down** states unchanged — error copy is already neutral.
- **Manual entry fallback** — same ED treatment: no ккал shown anywhere.
- **Reminders (B7/B9 voice templates):** when backend ships, B7 nutrition reminders for ED-flagged users MUST be entirely deactivated, NOT just stripped of numbers. Out of scope this spec — flagged for `customer-reminders-voice.md` author.
- **Anti-patterns в ED-mode:**
  - ❌ «Я знаю, что калории — это сложная тема» (acknowledging the issue draws attention to it)
  - ❌ «Можно показать цифры в Настройках» (giving an easy path back undermines protection)
  - ❌ Progress bar / streak / daily total в любой форме
  - ❌ «Питание выглядит хорошо» (positive judgment still a judgment)
  - ❌ Insight cards про питание

### Voice rules applied (ED-mode)

| Element | Rule | Status |
|---|---|---|
| F3 «Питание не оцениваю — это безопасно.» | Ayla owns the choice; «безопасно» frames it as protection, not deprivation | ✅ |
| F3 portion chips «поменьше · обычный · побольше» | Relative, non-numeric, intuitive | ✅ |
| F4 «Записала.» | Single word, no celebration, neutral recap | ✅ |
| /дневник entry «Сегодня · 14:32 · Гречка с курицей» | Time + dish, no ккал | ✅ |

---

## 9. States

### State — Not Recognized (FoodNotRecognizedError)

```
┌──────────────────────────────────────────────┐
│  ←  Не разобралась                            │
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │     [photo thumbnail ~80dp]            │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  Фото немного сложное — не разобралась.       │  Per skill.py
│  Можешь переснять поближе или просто          │  NOT_RECOGNIZED_FALLBACK
│  написать, что было.                          │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  📸 Переснять                          │   │  → F1 camera reopen
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │  ✎ Написать вручную                   │   │  → text entry skips
│  └──────────────────────────────────────┘   │  scanner, log_meal
│  ┌──────────────────────────────────────┐   │  c dish_name only
│  │  Отменить                              │   │
│  └──────────────────────────────────────┘   │
│                                               │
└──────────────────────────────────────────────┘
```

### State — API down (NutritionUnavailableError)

```
┌──────────────────────────────────────────────┐
│  ←  Сервис недоступен                         │
│  ─────────────────────────────────────       │
│                                               │
│                  ⏱                            │
│                                               │
│  Сервис распознавания временно недоступен.    │  Per skill.py
│  Попробуй через минуту.                       │  AYLA_DOWN_FALLBACK
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ↻ Попробовать ещё раз                │   │  → re-call scan_photo
│  └──────────────────────────────────────┘   │  same photo
│  ┌──────────────────────────────────────┐   │
│  │  ✎ Написать вручную                   │   │  → bypass scanner
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │  Назад на главную                      │   │
│  └──────────────────────────────────────┘   │
│                                               │
└──────────────────────────────────────────────┘
```

### State — Photo upload failed (PHOTO_NO_BYTES)

```
┌──────────────────────────────────────────────┐
│  ←  Не получилось загрузить                   │
│  ─────────────────────────────────────       │
│                                               │
│  Фото пришло, но скачать не получилось —      │  Per skill.py
│  пришли ещё раз, пожалуйста.                  │  PHOTO_NO_BYTES
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  📸 Сделать заново                     │   │  → F1
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │  Назад на главную                      │   │
│  └──────────────────────────────────────┘   │
│                                               │
└──────────────────────────────────────────────┘
```

### State — Offline

Food scanner НЕ работает offline (no offline file queue per tech lead Q2 confirmed).

```
┌──────────────────────────────────────────────┐
│  ⚡ Без сети                                  │  Persistent banner
├──────────────────────────────────────────────┤
│                                               │
│  Для распознавания еды нужна сеть.            │  Toast on F1 open
│  Попробуй когда вернётся.                     │  if offline detected
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Назад на главную                      │   │
│  └──────────────────────────────────────┘   │
│                                               │
└──────────────────────────────────────────────┘
```

### State — Reject toast (`❌ Не то`)

Tap on `❌ Не то` from F3:

```
   ╭─────────────────────────────────╮
   │  Поняла, не записываю.          │  Per skill.py
   │  Если хочешь — пришли ещё фото. │  REJECTED_ACK
   ╰─────────────────────────────────╯

      3-sec toast → auto-navigate Dashboard
      Photo NOT saved backend-side
```

---

## 10. Manual entry fallback (no-photo path)

Activated from:
- «Не разобралась» state → `✎ Написать вручную`
- «Сервис недоступен» state → `✎ Написать вручную`

```
┌──────────────────────────────────────────────┐
│  ←  Запись вручную                            │
│  ─────────────────────────────────────       │
│                                               │
│  Что съела?                                   │  Open-ended
│  ┌──────────────────────────────────────┐   │
│  │  гречка с курицей_                    │   │  Text input
│  └──────────────────────────────────────┘   │  dish_name field
│                                               │
│  ── Когда ──                                  │
│  ┌────────┐ ┌─────────┐ ┌──────┐ ┌─────────┐ │  Meal-type chips
│  │ 🌅     │ │ 🥗 ●     │ │ 🍽   │ │ 🍎      │ │  default by time
│  │Завтрак │ │ Обед    │ │ Ужин │ │Перекус  │ │
│  └────────┘ └─────────┘ └──────┘ └─────────┘ │
│                                               │
│  ── Порция (необязательно) ──                 │
│  ┌──────────────────────────────────────┐   │  Optional g/ml input
│  │  150_                                  │   │  if customer knows
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Записать                              │   │  → log_meal(
│  └──────────────────────────────────────┘   │      dish_name=...,
│                                               │      scan_id=None)
└──────────────────────────────────────────────┘
```

**Implementation notes:**
- `log_meal()` принимает либо `scan_id` либо `dish_name` (per nutrition_client.py contract — «At least one of scan_id / dish_name must be provided»)
- Calories/macros в этом case = approximate Ayla guess based on dish name + portion (или null если не может)
- F4 confirmation после manual entry — same layout, recap «Гречка с курицей — записано» (без ~ккал если не computed)

---

## 11. /дневник surface (food diary landing)

> **Entry points:** F4 «Открыть дневник» CTA · Dashboard pulse Питание tap · «День → Питание» bottom-nav tab. All three land on this single surface. Per memory `wellness-mvp-scaled-pilot` SCALED scope — /дневник is one of the three explicit MVP surfaces.

### Populated state — standard (numbers shown)

```
┌──────────────────────────────────────────────┐
│  ←  Дневник питания                           │  Header 56dp
│  ─────────────────────────────────────       │
│                                               │
│  ── Сегодня · 2 июня ──                       │  Day group header
│                                               │
│  1 720 / 2 100 ккал                           │  Daily total
│  ▓▓▓▓▓▓▓▓▓▓▓░░░  82 %                         │  (hidden in ED-mode)
│  Б 95 · Ж 48 · У 170 г                        │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  🌅 08:14 · Завтрак                   │   │  Entry card
│  │  Овсянка с ягодами · ~340 ккал        │   │  meal-type icon
│  │  ────────────────────                  │   │  time · meal-type
│  │  🥗 13:32 · Обед                       │   │  dish · ~ккал
│  │  Гречка с курицей · ~480 ккал         │   │
│  │  ────────────────────                  │   │  Tap entry → detail
│  │  🍎 16:08 · Перекус                    │   │  (post-pilot)
│  │  Яблоко · ~75 ккал                     │   │
│  │  ────────────────────                  │   │
│  │  🍽 19:24 · Ужин                       │   │
│  │  Курица гриль · ~825 ккал              │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  📸 Записать ещё                       │   │  Primary CTA
│  └──────────────────────────────────────┘   │  → F1 (or F0 first)
│                                               │
│  ── Вчера ──                                  │  Previous day group
│                                               │  collapsed by default
│  4 записи · 2 030 ккал                        │  tap → expand
│                                               │
└──────────────────────────────────────────────┘
```

### Empty state (no entries yet)

```
┌──────────────────────────────────────────────┐
│  ←  Дневник питания                           │
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │                                        │   │
│  │           📷                            │   │  Calm illustration
│  │                                        │   │  (mirrors F0/F1)
│  └──────────────────────────────────────┘   │
│                                               │
│  Дневник пока пустой.                         │  Empty hook
│                                               │
│  Сделай первое фото — соберём                 │  Voice continuation,
│  вместе.                                      │  collaborative
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  📸 Сделать фото                       │   │  Primary CTA
│  └──────────────────────────────────────┘   │  → F1 (or F0 first)
│  ┌──────────────────────────────────────┐   │
│  │  ✎ Написать вручную                   │   │  Secondary
│  └──────────────────────────────────────┘   │  → manual entry
│                                               │
└──────────────────────────────────────────────┘
```

### Populated state — ED variant

```
┌──────────────────────────────────────────────┐
│  ←  Дневник питания                           │
│  ─────────────────────────────────────       │
│                                               │
│  ── Сегодня · 2 июня ──                       │
│                                               │
│  4 записи                                     │  Count only,
│                                               │  no totals, no bar
│  ┌──────────────────────────────────────┐   │
│  │  🌅 08:14 · Завтрак                   │   │
│  │  Овсянка с ягодами                    │   │  No ккал shown
│  │  ────────────────────                  │   │
│  │  🥗 13:32 · Обед                       │   │
│  │  Гречка с курицей                     │   │
│  │  ────────────────────                  │   │
│  │  🍎 16:08 · Перекус                    │   │
│  │  Яблоко                                │   │
│  │  ────────────────────                  │   │
│  │  🍽 19:24 · Ужин                       │   │
│  │  Курица гриль                          │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  📸 Записать ещё                       │   │
│  └──────────────────────────────────────┘   │
│                                               │
└──────────────────────────────────────────────┘
```

### Implementation notes

- **Endpoint:** `daily_summary(external_user_id, date_from?, date_to?)` returns day-grouped entries. ED-mode hides totals + macros.
- **Date scope:** today + last 7 days collapsed groups. Tap day group → expands. No infinite scroll MVP (per `customer-records-flow.md` precedent).
- **«Записать ещё» CTA wiring:** routes to F0 if `food_photo_consent_at IS NULL`, else F1 directly.
- **Empty state:** no daily total scaffold, no «Б — · Ж — · У — г» dashes (Brand Guardian rule — don't render dashes-as-data per dashboard precedent).
- **Empty state CTA:** primary photo path + secondary manual entry — same options customer has on dashboard quick action, mirrored for symmetry.
- **Manual entry → /дневник:** after manual log saves, /дневник shows entry с recap «Гречка с курицей — записано» (no ккал if not computed — same rule as F4).
- **ED-mode toggle:** if customer flag changes mid-day, /дневник re-renders без ккал immediately on next open. NO retroactive scrub — historical entries lose number display but data retained backend-side (per founder Q post-pilot).
- **Entry tap-to-detail:** post-pilot (entry card opens detail modal with edit/delete). MVP read-only list.

### Voice rules applied (/дневник)

| Element | Rule | Status |
|---|---|---|
| Header «Дневник питания» | Simple noun, не «Журнал калорий» (clinical) | ✅ |
| Day group «Сегодня · 2 июня» | Relative + absolute, calm formatting | ✅ |
| Empty hook «Дневник пока пустой.» | Factual, no apology, no «начни прямо сейчас!» pressure | ✅ |
| Empty CTA hook «Сделай первое фото — соберём вместе.» | Collaborative «соберём вместе» (mirror F3 low-conf tooltip voice) | ✅ |
| Entry recap «Гречка с курицей · ~480 ккал» | Dish first, «~» visual signal preserved | ✅ |
| Day group collapsed summary «4 записи · 2 030 ккал» | Count + total, neutral | ✅ |
| ED-mode entry «Гречка с курицей» | Dish only, no ккал, no «не оцениваю» banner (avoid drawing attention) | ✅ |
| «📸 Записать ещё» | Continuation tone — «ещё» preserves agency, не «обязательно зафиксировать» | ✅ |

### Anti-patterns avoided (NOT present)

- ❌ «Не достигли цели» / streak warnings — guilt tone
- ❌ «Поздравляем — 1 720 ккал!» — celebratory calorie tone (anxiety-inducing)
- ❌ Pie charts / breakdowns — visual diet-app aesthetic
- ❌ «Поделиться дневником» — privacy violation
- ❌ «Сравнить с друзьями» — anti-pattern на 100%
- ❌ Empty state с pre-filled fake entries («Пример: овсянка...») — fake data

---

## 12. Backend mapping (final)

| Step / action | Endpoint | Notes |
|---|---|---|
| F0 check «consent given?» | Read `BotUser.food_photo_consent_at` (Alpha owns field) | NULL → show F0 before F1. Datetime → skip F0 |
| F0 → F1 (accept) | `PATCH /api/v1/customer/me/food-photo-consent { granted: true }` (Alpha owns endpoint) | Sets `food_photo_consent_at = now()` |
| F0 → Dashboard (decline) | No backend call | Silent return; next scan re-triggers F0 |
| Profile → revoke photo consent | `PATCH /api/v1/customer/me/food-photo-consent { granted: false }` | Sets `food_photo_consent_at = NULL` |
| F1 → F2 (submit photo) | `scan_photo(external_user_id, image_bytes, filename="meal.jpg", portion_multiplier?)` | Returns `ScanResponse(scan_id, dish_name, confidence, portion_g, nutrition, provider, raw, display_numbers)`. `display_numbers=false` ⇒ ED-mode rendering |
| F2 cancel | Frontend `AbortController.abort()` | Backend response ignored if late |
| F3 portion change | Local UI recalculation (or backend if supported) | TBD per founder Q1 |
| F3 → F4 (Save «to_diary») | `log_meal(external_user_id, scan_id, meal_type, portion_multiplier, idempotency_key)` | `idempotency_key = f"diary:{external_id}:{scan_id}"` |
| F3-Clarify → dish_name override | Local edit + `log_meal(dish_name=..., scan_id=None, meal_type, portion_multiplier, idempotency_key)` | Per Q-TAU-F5. Macros recalc backend-side TBD |
| F3-Clarify → new photo | Local navigate to F1 | No backend call |
| F3 → Reject («❌ Не то») | No backend call (silent ack) | Per skill.py contract |
| Manual entry → log | `log_meal(dish_name=..., meal_type, portion_multiplier=1.0, idempotency_key)` | scan_id=None |
| F4 daily summary delta | `daily_summary(external_user_id)` | Refresh after log. Honors `display_numbers` flag — ED-mode hides totals |
| F4 «Открыть дневник» CTA | Navigate to /дневник (День → Питание tab) | No backend call |
| F4 «Готово» CTA | Navigate to Dashboard | No backend call |
| /дневник landing | `daily_summary(external_user_id, date_from=today-7d, date_to=today)` | Day-grouped entries. ED-mode hides totals + macros |
| /дневник «Записать ещё» CTA | Re-check `food_photo_consent_at` | Routes F0 if NULL, F1 if datetime |
| ED-mode signal | `display_numbers` on every nutrition-returning endpoint | Alpha owns flag propagation. UI MUST honor independent of `nutrition` presence |

### Error mapping

| Backend exception | UI state | Recovery options |
|---|---|---|
| `FoodNotRecognizedError` | «Не разобралась» | Переснять / Написать вручную / Отменить |
| `NutritionUnavailableError` (circuit open / 5xx / network) | «Сервис недоступен» | Попробовать ещё раз / Написать вручную / Назад |
| `NutritionAPIError` (other 4xx) | «Сервис недоступен» (treated same as Unavailable) | Same |
| Photo bytes missing | «Не получилось загрузить» | Сделать заново / Назад |
| Frontend timeout (>10s) | «Сервис недоступен» | Same as Unavailable |
| Frontend cancel | F1 (photo preserved) | None — customer initiated |

---

## 13. Brand notes

### Voice rules applied

| Element | Voice rule | Status |
|---|---|---|
| F1 greeting «Что ешь сейчас?» | Open question, no pressure | ✅ |
| F1 privacy note «Фото нужно только чтобы узнать блюдо — удаляю сразу.» | First-person Ayla (corrected from sterile «Удаляется...») | ✅ Brand Guardian fix |
| F2 «👀 Распознаю…» | First-person feminine, single emoji prefix, present action with ellipsis | ✅ r2 |
| F3 high-conf «Узнала: гречка с курицей» | Confident verb «Узнала» per skill.py confidence ≥0.6 | ✅ |
| F3 low-conf «Похоже на: гречка с курицей» | Hedge per skill.py confidence <0.6 | ✅ |
| F3 low-conf tooltip «Прикинула приблизительно — давай уточним вместе» | Collaborative «уточним вместе» (corrected from отстранённое «проверь сама») | ✅ Brand Guardian fix |
| F3 «~480 ккал» | Visual «~» approximate signal | ✅ |
| F3 «Примерно» section header | Frames whole metric block as approximate | ✅ |
| F4 «✓ Записала» | First-person confirmation, single visual element | ✅ |
| F4 «Открыть дневник» / «Готово» | Plain action verbs, no wellness-OS overpromise | ✅ |
| F0 «Прежде чем сделать первое фото — короткое слово.» | Humanizing consent moment (reuse from onboarding S2 voice) | ✅ r2 |
| F0 «отправлю фото в свой сервис распознавания» | First-person Ayla owns data flow | ✅ r2 |
| F0 «Хорошо, продолжим» / «Не сейчас» | Soft affirmative, dignity preserved | ✅ r2 |
| ED-mode F3 «Питание не оцениваю — это безопасно.» | Ayla owns the choice; «безопасно» frames as protection not deprivation | ✅ r2 |
| ED-mode F4 «Записала.» | Single word, neutral confirmation, no celebration chrome | ✅ r2 |
| ED-mode /дневник «Гречка с курицей» (no ккал) | Dish only, banner-free — avoids drawing attention to the omission | ✅ r2 |
| /дневник empty «Сделай первое фото — соберём вместе.» | Collaborative continuation (mirror F3 voice) | ✅ r2 |
| Reject toast «Поняла, не записываю. Если хочешь — пришли ещё фото.» | Non-judgmental recovery, door left open | ✅ Reference quality |
| Not recognized «Фото немного сложное — не разобралась» | Ayla takes blame for recognition, not customer | ✅ Reference quality |
| API down «Сервис распознавания временно недоступен. Попробуй через минуту.» | Calm, factual, not panic | ✅ |

### Anti-patterns avoided (NOT present)

- ❌ «Вы съели слишком много» (никаких quantitative judgments)
- ❌ «Это вредно для здоровья» (никаких medical claims)
- ❌ «У вас проблема с белком» (никаких diagnoses)
- ❌ «Срочно лимфодренаж!» (никакого urgency manipulation)
- ❌ «Загрузка...» (sterile UI text)
- ❌ «Ошибка!» в error states (panic tone)
- ❌ Pre-checked retention checkbox (privacy creep)
- ❌ Gamification badges / streaks / leaderboards (childish, anxiety-inducing)
- ❌ Public sharing options (privacy violation)
- ❌ Daily calorie deficit recommendations (out of scope, not a diet app)
- ❌ «Только сегодня скидка!» (никакого sales pressure)

### 3 best brand moments (per Brand Guardian)

1. **«Прикинула приблизительно — давай уточним вместе»** (F3 low-conf tooltip) — honest expert moment + collaborative tone
2. **«Поняла, не записываю. Если хочешь — пришли ещё фото.»** (Reject toast) — non-judgmental recovery, door open
3. **«Фото немного сложное — не разобралась.»** (Not recognized) — Ayla takes blame for recognition, protects customer self-esteem

---

## 14. Accessibility (WCAG 2.2 AA)

Detailed audit referenced from `customer-main-wellness-dashboard.md` §8. Food scanner specific items below.

### Food-scanner specific a11y

1. **2.5.8 Target Size** — `−/+` portion buttons на F3 must be ≥44×44dp. Visual «−/+» glyphs можно smaller, but tap area large. Same for `⋯` menu, F2 cancel, modal close.

2. **1.4.3 Contrast** — Confidence indicators (low-conf border highlight), success check «✓ Записала», disabled photo button (offline) — all must meet 4.5:1 body / 3:1 non-text.

3. **1.1.1 Non-text Content** — Photo preview thumbnails need `alt="Фото блюда"` (или `aria-label`). Loading dots на F2 — `aria-hidden="true"` (decorative) + `role="status" aria-live="polite"` для «Распознаю» text. Emoji 👀 в loading line — `aria-hidden="true"` (screen reader читает только «Распознаю»).

4. **1.3.1 Info & Relationships** — F3 portion + macros composite `aria-label`: «Примерно 150 граммов, 480 килокалорий. Белки 35, жиры 8, углеводы 50 граммов.»

5. **2.4.3 Focus Order** — F1: header → meal-type chips → photo CTAs → date → privacy note. F3: photo → dish name → portion controls → meal-type → note → action buttons (primary first).

6. **4.1.3 Status Messages** — F2 «Распознаю» = `role="status" aria-live="polite"`. F3 portion change = `aria-live="polite"` для «Калории: ~480 ккал» update. F4 «✓ Записала» = `role="status"`.

7. **3.3.1 Error Identification** — Error states (Not recognized / API down / Photo failed) headers should be `role="alert"` for screen reader announcement.

8. **2.3.3 Reduced Motion** — F2 pulsing dots animation must respect `prefers-reduced-motion: reduce` → static dots.

9. **1.4.4 Resize Text** — At 200% zoom on 360dp: F1 meal-type chips (4 in row) may overflow → wrap to 2×2 grid. F3 buttons stack: already vertical, ok.

10. **2.5.5 / 3.3.4 Confirm Disrupting Action** — `❌ Не то` button: confirmation NOT required (low-cost reversible — customer can просто send another photo). But ensure label clear.

### Modal accessibility (F3-Clarify)

- `role="dialog" aria-modal="true" aria-labelledby="modal-title"`
- Focus trap inside modal
- Escape key closes (return to F3)
- Focus returns to «✏️ Уточнить» button on close
- Background F3 dimmed but readable (no `display:none`)

---

## 15. Variants considered

| Variant | Status | Rationale |
|---|---|---|
| **A — Wizard (back arrow nav)** | ✅ MAIN | Clear context per step, 30-90 sec flow needs orientation. Founder-approved. |
| B — Modal sheet (slide-up) | ⏸ rejected for MVP | Photo capture tight в sheet, less clear navigation, может вернуться post-pilot если data shows scroll fatigue |
| C — Single scroll page | ⏸ rejected | Confusing «where am I in flow», multi-step state poorly conveyed in single scroll |

---

## 16. Open questions / followups

### For tech lead (backend investigation)

| # | Question | Status | Impact |
|---|----------|--------|--------|
| **Q-BACK-1** | Backend recalculates macros при `log_meal(dish_name=..., scan_id=None)` override? Если нет — UI keeps approximate framing | ⚠ Open | Affects F3-Clarify path 1 (Название) data accuracy |
| **Q-BACK-2** | `scan_photo()` returns scan_id and holds temp result до `log_meal()` reference? TTL? | ✅ RESOLVED 2026-05-25 | TTL owned by Ayla side, нет SCAN_NOT_FOUND error code currently. Expired scan routes to AYLA_DOWN_FALLBACK generic message — UX покрывает. Future: Alpha adds SCAN_NOT_FOUND error code (post-pilot). |
| **Q-BACK-3** | MAX webview file input `<input type="file" capture="environment">` поддержка на Android/iOS production? | ⚠ Open | If unsupported on iOS → gallery-only fallback needed |
| **Q-BACK-4** | Cross-domain insight backend `insight_text` уже проходит safety filter (anti-medical, anti-shame)? | ✅ RESOLVED 2026-05-25 | Cross-domain insight card REMOVED from MVP. bot-platform-side не рендерит, anti-medical safety filter не в production, sample rule already medical-adjacent. Post-pilot re-introduce после Alpha safety audit templates. |
| **Q-BACK-5** (r2) | `BotUser.food_photo_consent_at: datetime \| null` field + revoke endpoint owned by Alpha? Timeline для pilot? | ⚠ Open | Blocks F0 wiring. ~1h scope. |
| **Q-BACK-6** (r2) | `ScanResponse.display_numbers` flag + `daily_summary` honors it? Who flips `eating_disorder_flag` MVP — Profile setting? Anketa? | ⚠ Open | Blocks ED-mode wiring. MVP toggle source TBD founder. |

### For founder (UX choices, not blockers)

| # | Question | Lean |
|---|----------|------|
| **Q-TAU-F2-DEEP** | First-time scanner — show intro card explaining flow? Or just open F1? | Skip intro per «прыгай в продукт сразу» principle — F1 self-explanatory |
| **Q-TAU-F3-DATE** | Date picker range — 7 days back enough or longer? | 7 days MVP, longer post-pilot if customer demand |
| **Q-TAU-F4-MULTI** | If customer logs 3 meals quickly — show «Хороший темп!» encouragement? | NO — gamification anti-pattern per wellness-input-modules §2 |

### For W1/Iota (frontend implementer)

1. **Photo size limit:** check MAX webview file picker default — typically 5-10MB. Backend `scan_photo` accepts jpeg, size limit TBD on backend side
2. **Camera capture on iOS:** if `capture="environment"` ignored, fallback to gallery is automatic (browser default)
3. **Image preview rendering:** use `URL.createObjectURL(file)` для local preview before upload
4. **AbortController plumbing:** wire to httpx client request, ensure cleanup на unmount
5. **scan_id TTL routing:** Q-BACK-2 resolved — Ayla owns TTL, нет SCAN_NOT_FOUND error code currently. Expired scan_id routes to `AYLA_DOWN_FALLBACK` generic message. Post-pilot Alpha adds proper SCAN_NOT_FOUND error code.
6. **F3 portion local recalc formula:** `new_calories = original_calories * portion_multiplier`. Same for БЖУ. Display rounding: ккал → integer, БЖУ → 1 decimal
7. **Modal trap focus:** use `focus-trap-react` or equivalent — handle Tab/Shift+Tab inside modal
8. **MAX BackButton wiring:** on F2/F3/F4 → BackButton confirms навигация (no data loss prompt MVP, может add post-pilot если customers lose unsaved edits)
9. **Idempotency key уникальность:** `f"diary:{external_id}:{scan_id}"` per skill.py. If same combination retry — backend returns same log_id (idempotent)
10. **Reduced motion:** detect via CSS `@media (prefers-reduced-motion: reduce)` — apply to loading dots + transitions
11. **F0 consent gate (r2):** check `food_photo_consent_at` before EVERY entry point routing to F1 — dashboard quick action, /дневник «Записать ещё» CTA, Not Recognized «Сделать заново», Manual entry's «📸 Сделать фото» exit. If NULL → render F0 first; on accept set + navigate F1.
12. **F0 store via lightweight client method:** suggested `consentClient.grantFoodPhotoConsent()` / `revokeFoodPhotoConsent()` thin wrappers. Persist locally optimistic; reconcile с backend.
13. **ED-mode rendering (r2):** treat `display_numbers === false` as branch switch BEFORE rendering F3/F4/daily_summary. Render variant blocks per §8. ED-mode portion chips submit `portion_multiplier ∈ {0.75, 1.0, 1.25}` mapped from `поменьше · обычный · побольше`.
14. **/дневник wiring (r2):** route from F4 «Открыть дневник» CTA + dashboard pulse Питание + bottom-nav День → Питание tap — all land same component. Re-fetch `daily_summary` on every mount. Day-grouped today + last 7d (no infinite scroll MVP).
15. **/дневник CTA chain (r2):** «Записать ещё» / «📸 Сделать фото» (empty state) re-check consent gate per #11.
16. **Loading line (r2):** «👀 Распознаю…» — emoji `aria-hidden="true"`, text wrapped в `role="status" aria-live="polite"`. Ellipsis is U+2026 (single char) not three dots.

---

## 17. Skills used (subagent review trail)

| Skill / Subagent | Phase | Findings summary |
|---|---|---|
| `frontend-design` (Anthropic skill) | C–E | Sage-green palette + lowercase «ayla», avoid AI cliché. ASCII placeholders mirror real UI patterns |
| Direct code reading | A | `apps/skills/food_scanner/skill.py` for voice templates (PHOTO_NO_BYTES, REJECTED_ACK, etc.) + `apps/integrations/ayla/nutrition_client.py` for API contract |
| `Brand Guardian` subagent | F (voice review) | 3 fixes applied initially: cross-domain wording, F3 tooltip (« давай уточним вместе »), F1 privacy («удаляю сразу»). Cross-domain wording later moot — entire insight card REMOVED per Q-BACK-4 verdict 2026-05-25. Remaining 2 fixes hold. Verdict: «brand-aligned with touch-ups» |
| UI Designer subagent | (skipped — patterns reuse from dashboard) | Card system, touch targets, hierarchy follow dashboard precedent |
| Accessibility Auditor subagent | (skipped — patterns reuse from dashboard) | Same WCAG criteria, food-specific items documented inline §11 |

---

## 18. Status next steps

- [x] Phase A — read wellness-input-modules + food_scanner skill code + nutrition_client
- [x] Phase B — plan 4 screens + 7 open questions + variant direction
- [x] Phase C — ASCII skeleton all 4 screens
- [x] Phase D — detail + states matrix (loading / not recognized / API down / photo failed / offline / reject toast / manual entry fallback)
- [x] Phase E — Variant A selected per founder direction (Wizard)
- [x] Phase F — Brand Guardian voice review (3 fixes applied)
- [x] **r2 amendment** (2026-06-02) — F0 photo consent + ED-mode F3/F4 variants + /дневник surface + loading-card «👀 Распознаю…» voice align. Per tech-lead pickup directive feeding W1 #164.
- [ ] **Phase G — Accessibility Auditor** (deferred — patterns reuse from dashboard precedent, food-specific items in §14)
- [ ] **Phase H — HTML preview** (skip per dashboard precedent)
- [x] Phase I — save to `docs/screens/customer-food-scanner-flow.md`
- [ ] Phase J — handoff block for tech lead

**Severity результирующего flow:** P0 BLOCKER для pilot 15 July 2026.

**Following streams to engage after sign-off:**
- W1 / Iota — frontend implementation per §16 items 1-16 (10 original + 6 r2: F0 consent gate, consent client, ED rendering, /дневник wiring, CTA chain, loading line)
- Alpha — backend wiring: `food_photo_consent_at` field + revoke endpoint (Q-BACK-5); `display_numbers` flag propagation + ED toggle source (Q-BACK-6)
- Tech lead — backend investigations Q-BACK-1..6 + ED toggle source decision (Profile setting vs anketa flag)
- AI Engineering — cross-domain insight safety guardrails (anti-medical / anti-shame filter validation) + ED safety chain (reminders B7/B9 deactivation, not stripping)
- `customer-reminders-voice.md` author — note: B7 nutrition reminders MUST be deactivated wholesale for ED-flagged users (NOT number-stripped)
- Accessibility Engineer — WCAG 2.2 AA pass + screen reader testing (NVDA + VoiceOver iOS в MAX webview)

---

**Last verified:** 2026-06-02 r2 (F0 consent + ED-mode + /дневник + loading-card per W1 #164 pickup)
**Tau (UX/Design stream)**

**Canon provenance (r2):**
- F0 consent voice → reuses [[customer-onboarding-flow.md]] §5 S2 «короткое слово» framing
- ED-mode safety lineage → memory `cross-domain-insight-safety-gap` + `wellness-mvp-scaled-pilot` SCALED scope
- /дневник surface → memory `wellness-mvp-scaled-pilot` explicit MVP surface (1 of 3)
- Loading line «👀 Распознаю…» → tech-lead pickup directive 2026-06-02 (supersedes earlier «Узнаю что на фото»)
- Two-axis register: customer-facing → «ты» (locked customer canon, `ayla-identity-and-brand.md` §3.0)
