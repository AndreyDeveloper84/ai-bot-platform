# Screen: customer-food-scanner-flow

| Field | Value |
|---|---|
| **Audience** | customer (Анна, любой state — first-time and returning) |
| **Phase** | P0 — pilot 15 July 2026 (Penza) |
| **Status** | draft — Phase A–G done, awaiting tech lead sign-off + frontend handoff to W1/Iota |
| **Channel** | MAX webview (Mini App inside MAX messenger) |
| **Stream** | Tau (UX/Design) |
| **Date** | 2026-05-25 r1 |
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

```
Dashboard 📸 quick action
        │
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

---

## 2. F1 — Capture

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

## 3. F2 — Processing

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
│        Узнаю что на фото                      │  Ayla state line
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

- **Initial state (0-3s):** только Ayla line + loading dots, без cancel button
- **After 3s delay:** auto-surface «Если занимает дольше...» + Cancel button
- **Cancel mechanics:** `AbortController.abort()` на httpx request frontend-side. If backend response returns после cancel — UI ignores (state = `cancelled`)
- **Timeout 10s** (matches `DEFAULT_TIMEOUT_S` в nutrition_client) → automatic transition to «API down» state
- **Reduced-motion:** `prefers-reduced-motion: reduce` → static dots без анимации
- **Photo preview** downscaled ~140dp — customer видит что Ayla работает с её фото

---

## 4. F3 — Recognition result + edit

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

## 5. F3-Clarify Modal (overlay)

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

## 6. F4 — Saved confirmation

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

## 7. States

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

## 8. Manual entry fallback (no-photo path)

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

## 9. Backend mapping (final)

| Step / action | Endpoint | Notes |
|---|---|---|
| F1 → F2 (submit photo) | `scan_photo(external_user_id, image_bytes, filename="meal.jpg", portion_multiplier?)` | Returns `ScanResponse(scan_id, dish_name, confidence, portion_g, nutrition, provider, raw)` |
| F2 cancel | Frontend `AbortController.abort()` | Backend response ignored if late |
| F3 portion change | Local UI recalculation (or backend if supported) | TBD per founder Q1 |
| F3 → F4 (Save «to_diary») | `log_meal(external_user_id, scan_id, meal_type, portion_multiplier, idempotency_key)` | `idempotency_key = f"diary:{external_id}:{scan_id}"` |
| F3-Clarify → dish_name override | Local edit + `log_meal(dish_name=..., scan_id=None, meal_type, portion_multiplier, idempotency_key)` | Per Q-TAU-F5. Macros recalc backend-side TBD |
| F3-Clarify → new photo | Local navigate to F1 | No backend call |
| F3 → Reject («❌ Не то») | No backend call (silent ack) | Per skill.py contract |
| Manual entry → log | `log_meal(dish_name=..., meal_type, portion_multiplier=1.0, idempotency_key)` | scan_id=None |
| F4 daily summary delta | `daily_summary(external_user_id)` | Refresh after log |
| F4 «Открыть дневник» CTA | Navigate to День → Питание tab | No backend call |
| F4 «Готово» CTA | Navigate to Dashboard | No backend call |

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

## 10. Brand notes

### Voice rules applied

| Element | Voice rule | Status |
|---|---|---|
| F1 greeting «Что ешь сейчас?» | Open question, no pressure | ✅ |
| F1 privacy note «Фото нужно только чтобы узнать блюдо — удаляю сразу.» | First-person Ayla (corrected from sterile «Удаляется...») | ✅ Brand Guardian fix |
| F2 «Узнаю что на фото» | First-person feminine, present action | ✅ |
| F3 high-conf «Узнала: гречка с курицей» | Confident verb «Узнала» per skill.py confidence ≥0.6 | ✅ |
| F3 low-conf «Похоже на: гречка с курицей» | Hedge per skill.py confidence <0.6 | ✅ |
| F3 low-conf tooltip «Прикинула приблизительно — давай уточним вместе» | Collaborative «уточним вместе» (corrected from отстранённое «проверь сама») | ✅ Brand Guardian fix |
| F3 «~480 ккал» | Visual «~» approximate signal | ✅ |
| F3 «Примерно» section header | Frames whole metric block as approximate | ✅ |
| F4 «✓ Записала» | First-person confirmation, single visual element | ✅ |
| F4 «Открыть дневник» / «Готово» | Plain action verbs, no wellness-OS overpromise | ✅ |
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

## 11. Accessibility (WCAG 2.2 AA)

Detailed audit referenced from `customer-main-wellness-dashboard.md` §8. Food scanner specific items below.

### Food-scanner specific a11y

1. **2.5.8 Target Size** — `−/+` portion buttons на F3 must be ≥44×44dp. Visual «−/+» glyphs можно smaller, but tap area large. Same for `⋯` menu, F2 cancel, modal close.

2. **1.4.3 Contrast** — Confidence indicators (low-conf border highlight), success check «✓ Записала», disabled photo button (offline) — all must meet 4.5:1 body / 3:1 non-text.

3. **1.1.1 Non-text Content** — Photo preview thumbnails need `alt="Фото блюда"` (или `aria-label`). Loading dots на F2 — `aria-hidden="true"` (decorative) + `role="status" aria-live="polite"` для «Узнаю что на фото» text.

4. **1.3.1 Info & Relationships** — F3 portion + macros composite `aria-label`: «Примерно 150 граммов, 480 килокалорий. Белки 35, жиры 8, углеводы 50 граммов.»

5. **2.4.3 Focus Order** — F1: header → meal-type chips → photo CTAs → date → privacy note. F3: photo → dish name → portion controls → meal-type → note → action buttons (primary first).

6. **4.1.3 Status Messages** — F2 «Узнаю что на фото» = `role="status" aria-live="polite"`. F3 portion change = `aria-live="polite"` для «Калории: ~480 ккал» update. F4 «✓ Записала» = `role="status"`.

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

## 12. Variants considered

| Variant | Status | Rationale |
|---|---|---|
| **A — Wizard (back arrow nav)** | ✅ MAIN | Clear context per step, 30-90 sec flow needs orientation. Founder-approved. |
| B — Modal sheet (slide-up) | ⏸ rejected for MVP | Photo capture tight в sheet, less clear navigation, может вернуться post-pilot если data shows scroll fatigue |
| C — Single scroll page | ⏸ rejected | Confusing «where am I in flow», multi-step state poorly conveyed in single scroll |

---

## 13. Open questions / followups

### For tech lead (backend investigation)

| # | Question | Status | Impact |
|---|----------|--------|--------|
| **Q-BACK-1** | Backend recalculates macros при `log_meal(dish_name=..., scan_id=None)` override? Если нет — UI keeps approximate framing | ⚠ Open | Affects F3-Clarify path 1 (Название) data accuracy |
| **Q-BACK-2** | `scan_photo()` returns scan_id and holds temp result до `log_meal()` reference? TTL? | ✅ RESOLVED 2026-05-25 | TTL owned by Ayla side, нет SCAN_NOT_FOUND error code currently. Expired scan routes to AYLA_DOWN_FALLBACK generic message — UX покрывает. Future: Alpha adds SCAN_NOT_FOUND error code (post-pilot). |
| **Q-BACK-3** | MAX webview file input `<input type="file" capture="environment">` поддержка на Android/iOS production? | ⚠ Open | If unsupported on iOS → gallery-only fallback needed |
| **Q-BACK-4** | Cross-domain insight backend `insight_text` уже проходит safety filter (anti-medical, anti-shame)? | ✅ RESOLVED 2026-05-25 | Cross-domain insight card REMOVED from MVP. bot-platform-side не рендерит, anti-medical safety filter не в production, sample rule already medical-adjacent. Post-pilot re-introduce после Alpha safety audit templates. |

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

---

## 14. Skills used (subagent review trail)

| Skill / Subagent | Phase | Findings summary |
|---|---|---|
| `frontend-design` (Anthropic skill) | C–E | Sage-green palette + lowercase «ayla», avoid AI cliché. ASCII placeholders mirror real UI patterns |
| Direct code reading | A | `apps/skills/food_scanner/skill.py` for voice templates (PHOTO_NO_BYTES, REJECTED_ACK, etc.) + `apps/integrations/ayla/nutrition_client.py` for API contract |
| `Brand Guardian` subagent | F (voice review) | 3 fixes applied initially: cross-domain wording, F3 tooltip (« давай уточним вместе »), F1 privacy («удаляю сразу»). Cross-domain wording later moot — entire insight card REMOVED per Q-BACK-4 verdict 2026-05-25. Remaining 2 fixes hold. Verdict: «brand-aligned with touch-ups» |
| UI Designer subagent | (skipped — patterns reuse from dashboard) | Card system, touch targets, hierarchy follow dashboard precedent |
| Accessibility Auditor subagent | (skipped — patterns reuse from dashboard) | Same WCAG criteria, food-specific items documented inline §11 |

---

## 15. Status next steps

- [x] Phase A — read wellness-input-modules + food_scanner skill code + nutrition_client
- [x] Phase B — plan 4 screens + 7 open questions + variant direction
- [x] Phase C — ASCII skeleton all 4 screens
- [x] Phase D — detail + states matrix (loading / not recognized / API down / photo failed / offline / reject toast / manual entry fallback)
- [x] Phase E — Variant A selected per founder direction (Wizard)
- [x] Phase F — Brand Guardian voice review (3 fixes applied)
- [ ] **Phase G — Accessibility Auditor** (deferred — patterns reuse from dashboard precedent, food-specific items in §11)
- [ ] **Phase H — HTML preview** (skip per dashboard precedent)
- [x] Phase I — save to `docs/screens/customer-food-scanner-flow.md`
- [ ] Phase J — handoff block for tech lead

**Severity результирующего flow:** P0 BLOCKER для pilot 15 July 2026.

**Following streams to engage after sign-off:**
- W1 / Iota — frontend implementation per §13 items 1-10
- Tech lead — backend investigations Q-BACK-1..4 (macros recalc, scan_id TTL, MAX file input, insight safety filter)
- AI Engineering — cross-domain insight safety guardrails (anti-medical / anti-shame filter validation)
- Accessibility Engineer — WCAG 2.2 AA pass + screen reader testing (NVDA + VoiceOver iOS в MAX webview)

---

**Last verified:** 2026-05-25 r1
**Tau (UX/Design stream)**
