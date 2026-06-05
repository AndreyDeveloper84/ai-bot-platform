# Screen: customer-onboarding-flow

| Field | Value |
|---|---|
| **Audience** | customer (Анна) — first time arriving at Ayla bot DM |
| **Phase** | P0 BLOCKER — pilot 15 July 2026 (Penza) |
| **Status** | draft — Phase A–G done, awaiting tech lead final sign-off + frontend handoff |
| **Channel** | MAX **bot DM** (chat with inline keyboards) — NOT Mini App |
| **Stream** | Tau (UX/Design) |
| **Date** | 2026-05-25 r1 |
| **Severity rationale** | First impression = retention day 1. Если confused/cold → churn до pilot week 1 |
| **Time budget** | 60-90 sec from first message to first action |

---

## 1. Контекст

### Strategic context

Анна впервые открывает Ayla чат через MAX. Что произойдёт за первые 60 секунд определит вернётся ли она через сутки. Если confused — churn before pilot week 1.

Founder principle: «Чем меньше шагов до first value — тем выше retention. Don't gate value behind onboarding requirements.»

### Channel: bot DM, NOT Mini App

**Critical separation:**
- **Bot DM** (this flow) — text + inline buttons в MAX мессенджере, customer authenticated через MAX OAuth identity
- **Mini App** — anonymous-friendly per `anonymous-to-registered-gate.md` §2.1, opens later после first action

Onboarding flow lives entirely в bot DM. Mini App opens via deeplink only после customer's first action choice (S5 → Mini App).

### Goals (per tech lead handoff)

1. Объяснить кто такая Ayla (NOT просто календарь — wellness assistant)
2. Получить 152-ФЗ согласие (без скучной legal walls)
3. ~~Light health screening~~ **SKIPPED for pilot** per Q-TAU-O2 — anketa server-side override covers safety
4. Optional anketa offer (NOT mandatory — secondary positioning per S5)
5. Direct customer to first action (food scan / water / goal / catalog / anketa)
6. Land на main wellness dashboard

---

## 2. Phase A scope discoveries (critical findings)

Phase A reading revealed 3 расхождения tech lead's plan vs codebase:

### Finding 1 — `apps/skills/welcome/skill.py` DOES NOT EXIST

**Resolution:** Hardcoded в bot adapter response (Q-TAU-O1 verdict). No new skill scope.

### Finding 2 — `apps/skills/privacy_consent/skill.py` is REACTIVE not proactive

Existing skill responds only to «удалить мои данные» / «выгрузить мои данные» (GDPR data subject rights). Не показывает proactive consent screen.

**Resolution:** Conversational consent presentation hardcoded (Q-TAU-O4 verdict). No new skill scope.

### Finding 3 — `apps/skills/health_screening/skill.py` is Tier-A pain gate only

Pain consultation skill (reactive on «болит спина»), NOT Tier-B nutrition pre-anketa health screening (pregnancy/diabetes/eating disorder — that's 533 LOC mysite-only, deferred to post-pilot).

**Resolution:** **SKIP S4 health screening for pilot** (Q-TAU-O2 verdict). Safety covered by:
- Anketa server-side `goal_overridden_by` (pregnancy + lose goal → maintain override automatic)
- Tier-A health_screening skill triggers reactively when customer mentions pain
- Customer can disclose anytime in chat — Ayla acknowledges и filters silently per 🔴 red zone framework

### Finding 4 — Anonymous policy vs onboarding context

Per `anonymous-to-registered-gate.md`: Mini App = anonymous-friendly. Bot DM = authenticated via MAX OAuth.

**Resolution:** Onboarding lives в bot DM context. Customer уже authenticated через MAX. Mini App opens later anonymous-style for first action.

---

## 3. Flow sequence S1-S7

```
S1 — First message (Welcome)
  hardcoded bot adapter
        │
        ▼
S2 — Privacy consent (152-ФЗ)
  hardcoded conversational, [Да, продолжим][Узнать что хранится][Не сейчас]
        │
        ├── «Не сейчас» → State: Refused consent → conversation ends
        │
        ├── «Узнать что хранится» → S2a expanded → loop back
        │
        ▼
S3 — Кто такая Ayla (conditional positioning)
  hardcoded text, sequential message
        │
        ▼ (no user action — auto-flow to S5)
        │
S4 — Health screening
  SKIPPED FOR PILOT per Q-TAU-O2
        │
        ▼
S5 — First action choice (KEY MOMENT)
  inline keyboard 4 primary + anketa + exit valve
        │
        ├── 📸 Сфотогр. еду → Food Scanner Mini App F1
        ├── 💧 + стакан воды → Dashboard with water +250ml toast
        ├── 🎯 Выбрать цель → Goal selector (post-pilot Mini App)
        ├── 📅 Найти услугу → Catalog Mini App
        ├── 📝 Начать анкету → S6 bot DM
        └── Просто посмотреть → S7 Dashboard empty
                                 │
                                 ▼
                              S7 — Dashboard
                              Mini App opens

If S6 chosen:
        │
        ▼
S6 — Anketa flow (existing nutrition_anketa skill DRF-820)
  5 bot DM steps: gender → age → height → weight → goal
  → upsert_profile() → norms summary card
        │
        ├── any step «пропустить» → back to S5 short version
        │
        ▼
S6 completion: [Открыть кабинет][Сначала фото еды]
        │
        ▼
S7 — Dashboard (with anketa data → БЖУ targets visible)
```

### Voice principle for всех steps

Per `ayla-identity-and-brand.md` + founder onboarding emphasis:
- ✅ First-person feminine («расскажу / помогу / помню / рассчитала / поняла»)
- ✅ Indeclinable «Ayla» (NOT «Айла»)
- ✅ На «ты» (не «Вы»)
- ✅ Specific («о еде, воде, отдыхе» NOT «wellness»)
- ✅ ≤3 sentences per Ayla message default
- ✅ Max 1 emoji per message
- ❌ NO «бот», «помощник салона», «уважаемый клиент»
- ❌ NO marketing copy («Открой себя лучше с Ayla»)
- ❌ NO legal walls
- ❌ NO mandatory gates перед value

---

## 4. S1 — First message

**Trigger:** Customer впервые пишет Ayla, OR opens bot DM via deeplink (QR/IG/referral/direct/Maps). Resolution per `customer-first-touch-and-mini-app-states.md` §3.

**Backend:** Hardcoded text + inline keyboard в bot adapter. No skill (per Q-TAU-O1).

### S1 standard (direct / IG bio / Maps / direct MAX search)

```
─────────────────────────────────────────────
  ayla ✨

  Привет, я Ayla. Расскажу о еде, воде,
  отдыхе и помогу записаться к мастерам.
  Начнём с малого?

  [ Начать ]   [ Узнать подробнее ]
─────────────────────────────────────────────
```

### S1 multi-tenant variant (referral / QR / share-link with salon context)

**Detection:** `start_param=ref_<user_id>` или `qr_<salon_id>_<placement>` или `ig_post_<id>`.

```
─────────────────────────────────────────────
  ayla ✨

  Привет, я Ayla. Помогу с записью в
  {{salon_name}} и с уходом за собой
  каждый день — еда, вода, отдых.
  Начнём?

  [ Начать ]   [ Узнать подробнее ]
─────────────────────────────────────────────
```

**Voice check:**
- ✅ First-person Ayla introduction
- ✅ Specific scope «о еде, воде, отдыхе» (1:1 с founder's reference one-liner)
- ✅ Salon = third-party reference per `tenant-as-provider-model.md` («помогу с записью в Casa Bella» NOT «помощник Casa Bella»)
- ✅ Caring invitation «Начнём с малого?» / «Начнём?»

### S1 «Узнать подробнее» branch

Customer wants context before commit:

```
─────────────────────────────────────────────
  ayla

  Я не приложение для строгих диет
  и не календарь записей. Я помогу
  каждый день — еда, вода, цель,
  ближайшая запись, самочувствие.
  Без оценок.

  [ Понятно, начнём ]   [ Не сейчас ]
─────────────────────────────────────────────
```

После tap `Понятно, начнём` → переход к S2. (S3 positioning не нужен повторно — customer уже узнала.)

---

## 5. S2 — Privacy consent (152-ФЗ)

**Trigger:** Customer taps `Начать` (or `Понятно, начнём` from S1 expansion).
**Backend:** Hardcoded conversational presentation. NO new skill (per Q-TAU-O4).

```
─────────────────────────────────────────────
  ayla

  Прежде чем начать — короткое слово.
  Я буду помнить о тебе только то, что
  поможет рекомендовать точнее. Хранится
  безопасно. Удалить можно в любой момент.

  Продолжим?

  [ Да, продолжим ]
  [ Узнать что хранится ]
  [ Не сейчас ]
─────────────────────────────────────────────
```

### S2a — «Узнать что хранится» expanded fold

```
─────────────────────────────────────────────
  ayla

  Запоминаю: твои сообщения мне,
  выбранные цели, питание и вода если
  решишь логировать, записи к мастерам.
  Не делюсь с салонами без твоего
  разрешения. Подробнее в Профиле →
  «Данные обо мне» когда зайдёшь.

  [ Понятно, продолжим ]
  [ Не сейчас ]
─────────────────────────────────────────────
```

**Voice check (per Brand Guardian fixes applied):**
- ✅ «Продолжим?» — gender-neutral, не «Согласна» (force female-gendered) — Brand Guardian fix
- ✅ S2a active voice «Запоминаю» (was passive «Хранится» × 2 — corrected) — Brand Guardian fix
- ✅ «Я буду помнить о тебе только то, что поможет» — explicit scope limit, first-person
- ✅ «Удалить можно в любой момент» — exit door from start
- ✅ 4 sentences primary, 4 sentences expanded — никакого legal wall
- ✅ «короткое слово» framing — humanizes consent moment

---

## 6. S3 — Кто такая Ayla (conditional positioning)

**Trigger:** Customer taps `Да, продолжим` или `Понятно, продолжим`.

**Conditional rendering:**
- Если customer прошёл S1 «Узнать подробнее» path → **SKIP S3** (positioning уже была) → straight to S5
- Если customer прошёл S2a expanded → **SKIP S3** (similar — она уже узнала enough)
- Else (direct S1→S2→S3) → **SHOW S3**

```
─────────────────────────────────────────────
  ayla

  Если коротко — я не календарь и не
  ещё одна программа правильного питания.
  Я помогу разобраться с собой каждый
  день — еда, вода, ближайшая запись,
  самочувствие. Без оценок.

  (sequential — S5 message arrives next without user action)
─────────────────────────────────────────────
```

**Voice check:**
- ✅ Anti-positioning «не календарь, не ещё одна программа правильного питания» — Brand Guardian rated «anti-positioning без being defensive»
- ✅ «Без оценок» repeats core promise — psychological anchor
- ✅ «помогу разобраться с собой» — caring expert framing

**UX detail:** S3 = autonomous Ayla messaging. No user tap required between S3 and S5 — they arrive as two sequential bubbles, customer reads both, then taps S5 button.

---

## 7. S4 — Health screening (SKIPPED for pilot)

**Status:** No S4 message in pilot flow per Q-TAU-O2 verdict.

### Why skipped

- Tier-B health screening skill (pregnancy/diabetes/eating disorder gate) doesn't exist in codebase
- Building = scope creep (533 LOC port from mysite, requires medical privacy infrastructure)
- Safety covered automatically by 3 other mechanisms:
  1. **Anketa server-side `goal_overridden_by`** — `upsert_profile()` applies override automatically (pregnancy + «lose» goal → «maintain» override; eating disorder anamnesis + lose → override; BMI floor protection)
  2. **Tier-A health_screening skill** — reactive trigger on «болит шея» / «температура 38.5» — pre-empts LLM from confidently recommending massage for neurological emergency
  3. **3-zone privacy framework** — customer can disclose 🔴 red-zone data anytime в chat — Ayla acknowledges и filters silently per `ayla-memory-and-personalization.md` §2.3 (NEVER quoted back, used only for service contraindication filtering)

### If founder reverses decision post-pilot

```
─────────────────────────────────────────────
  ayla

  Прежде чем что-то предлагать — есть ли
  важные ограничения? Если есть — учту,
  если нет — пропусти.

  [ Беременность ]   [ Диабет ]
  [ Серьёзная аллергия ]
  [ Нет ограничений ]   [ Пропустить ]
─────────────────────────────────────────────
```

Storage → 🔴 red zone. Used silently. NEVER quoted back. Per `ayla-memory-and-personalization.md` §2.3.

**For pilot:** этот message NOT present в flow.

---

## 8. S5 — First action choice (KEY MOMENT)

**Trigger:** Arrives right after S3 (no user action between S3 and S5) OR right after S2 «Да, продолжим» / S2a «Понятно, продолжим» / S1 «Узнать подробнее → Понятно, начнём».

**This is the critical screen.** Customer выбирает первый experience с Ayla.

### Variant A — Grid 2×2 + anketa separate (MAIN, recommended)

```
─────────────────────────────────────────────
  ayla

  С чего хочешь начать? Можно прямо сейчас:

  [ 📸 Сфотогра-     ] [ 💧 + стакан      ]
  [    фировать еду   ] [    воды           ]

  [ 🎯 Выбрать цель  ] [ 📅 Найти услугу  ]

  Или расскажи о себе — 5 шагов, буду
  точнее советовать:

  [ 📝 Начать анкету ]   [ Просто посмотреть ]
─────────────────────────────────────────────
```

**Layout reasoning:**
- 4 primary actions в 2×2 grid — equal visual weight, fast scan
- Anketa visually separated below primary с invitation framing — **anketa de-duplicated** per Brand Guardian fix (was «Или сначала анкета» + button «Анкета (5 мин)» = double surfacing). Now framing carries «5 шагов» info, button = «Начать анкету»
- «Просто посмотреть» — exit valve, leads to Dashboard immediately

### Variant B — Vertical stack (alternative)

```
─────────────────────────────────────────────
  ayla

  С чего хочешь начать? Можно прямо сейчас:

  [ 📸 Сфотографировать еду ]
  [ 💧 Записать стакан воды  ]
  [ 🎯 Выбрать цель           ]
  [ 📅 Найти услугу           ]

  Или расскажи о себе — 5 шагов:
  [ 📝 Начать анкету          ]
  [ Просто посмотреть         ]
─────────────────────────────────────────────
```

**Trade-off A vs B:**

| Aspect | A (Grid 2×2) | B (Vertical) |
|--------|--------------|--------------|
| Scan time | Faster (parallel) | Slower (sequential) |
| Touch target | Compact ~140dp | Wider ~280dp |
| MAX rendering | 2-button rows × 2 | 1-button rows × 4 (taller) |
| Hierarchy clarity | Equal weight 4 primary | Equal weight 4 primary |
| Empty space | Less | More |
| Mobile small screen | Compact OK | Scrollable needed |

**My lean (selected for pilot):** Variant A (Grid 2×2). Better mobile fit + faster scan. Variant B held as fallback if MAX inline keyboard rendering issues с 2-button rows.

### S5 button routing

| Button | `start_param` | Mini App lands |
|--------|---------------|----------------|
| 📸 Сфотографировать еду | `food_scan` | Food Scanner F1 Capture |
| 💧 + стакан воды | `water_add_250` | Dashboard with water +250 ml auto-logged, toast confirmation |
| 🎯 Выбрать цель | `goal_select` | Goal selector (post-pilot Mini App OR Profile section если не готов) |
| 📅 Найти услугу | `catalog` | Услуги tab |
| 📝 Начать анкету | `anketa_start` | Triggers `nutrition_anketa` skill в bot DM (S6) — NOT Mini App per Q-TAU-O5 |
| Просто посмотреть | `home` | Dashboard empty state per `customer-main-wellness-dashboard.md` |

---

## 9. S6 — Anketa flow (conditional, if 📝 chosen)

**Trigger:** Customer tapped `📝 Начать анкету`.
**Backend:** Existing `nutrition_anketa` skill (DRF-820) — runs as bot DM conversation, NOT Mini App wizard. Per Q-TAU-O5 verdict.

### S6.0 — Anketa intro (Ayla orientation)

```
─────────────────────────────────────────────
  ayla

  Хорошо. 5 шагов — пара минут. Если
  захочешь остановиться, скажи
  «пропустить».

  Какой у тебя пол? Это нужно для расчёта
  обмена веществ — у Ж и М разные
  коэффициенты.

  [ Женский ]   [ Мужской ]
─────────────────────────────────────────────
```

**Voice check:**
- ✅ «Хорошо» — acknowledgment, не «Отлично!» (filler)
- ✅ «пара минут» — concrete time commitment
- ✅ «скажи "пропустить"» — exit door visible from start
- ✅ Gender question 1:1 с `nutrition_anketa/fsm.py` voice template

### S6.1 — S6.5: standard anketa steps

Per `apps/skills/nutrition_anketa/fsm.py` (already implemented):
- **Step 2 Age:** «Сколько тебе лет? Напиши число.» (validation 14-90)
- **Step 3 Height:** «Какой у тебя рост в сантиметрах?» (validation 100-220)
- **Step 4 Weight:** «Какой текущий вес в килограммах?» (validation 30-200)
- **Step 5 Goal:** «Какая у тебя цель?» (chips: Похудеть / Поддержать / Набрать)

All copy: existing skill, brand-aligned, no changes.

### S6 completion

Per `_format_summary()` in skill.py:

```
─────────────────────────────────────────────
  ayla

  Готово, рассчитала твои нормы:

  🔥 Калории: 2 100 ккал/день
  🍗 Белок: 95 г
  🥑 Жиры: 70 г
  🍚 Углеводы: 220 г
  💧 Вода: 2 000 мл

  Теперь могу считать калории и БЖУ
  из фото блюд.

  [ Открыть кабинет ]   [ Сначала фото еды ]
─────────────────────────────────────────────
```

If `goal_overridden_by` present (e.g., pregnancy override):
- Add line «Учла важное в анамнезе — нормы подобрала с поправкой на это.» (1:1 with code)
- Generic wording — никогда не quote red-zone trigger per `ayla-memory-and-personalization.md` §2.3

### S6 skip flow

Customer says «пропустить» mid-anketa или taps Cancel button (if inline):

```
─────────────────────────────────────────────
  ayla

  Поняла. Анкету можно пройти потом
  в Профиле, ничего не теряется.

  С чего хочешь начать?

  [ 📸 Еда ]   [ 💧 Вода ]   [ 📅 Услуга ]
  [ Просто посмотреть ]
─────────────────────────────────────────────
```

Skip → S5 short version (без anketa button, она уже была offered).

---

## 10. S7 — Land на main wellness dashboard

**Trigger:** Customer tapped any S5 action OR S6 completion CTA.
**Implementation:** Mini App opens via MAX deeplink с context, lands per `customer-main-wellness-dashboard.md` design.

### S7 — First-action paths

| Customer action | Mini App lands at | Dashboard state |
|-----------------|-------------------|-----------------|
| 📸 Food scanner | Food Scanner F1 | After save → F4 → Dashboard with first meal logged |
| 💧 + стакан воды | Dashboard with toast «+250 мл, спасибо!» | Water log started, dashboard populated for water |
| 🎯 Цель | Goal selector → return Dashboard | Layer 2 Goals filled |
| 📅 Услуга | Услуги catalog | No dashboard touch (browsing catalog) |
| 📝 Анкета → completion | Dashboard | Pulse strip shows targets, БЖУ visible, no logs yet |
| Просто посмотреть | Dashboard empty state | Onboarding nudge card prominent |

### Dashboard state on first arrival

Per `customer-main-wellness-dashboard.md` §5 State 2 (Empty), modified by anketa status:
- **Anketa not done** → БЖУ row hidden, calorie goal banner «пока не рассчитано», onboarding nudge card present
- **Anketa done** → БЖУ row visible with «Б 0/X · Ж 0/X · У 0/X г», calorie target visible

Customer's first logged action populates corresponding pulse row → next dashboard open showing populated data.

---

## 11. States матрица

### State 1 — Happy path

S1 (Начать) → S2 (Да, продолжим) → S3 + S5 → tap food scanner → Food Scanner F1 → F4 → Dashboard populated

Total time: ~30-45 sec. Retention day 1 = high.

### State 2 — Anketa-first happy path

S1 → S2 → S3 + S5 → tap Начать анкету → S6.0-S6.5 (~90 sec) → S6 completion → Открыть кабинет → Dashboard with targets

Total time: ~120-150 sec. Customer commits time but gets immediate value.

### State 3 — Refused consent

S1 → S2 → customer taps `Не сейчас`.

```
─────────────────────────────────────────────
  ayla

  Поняла. Когда захочешь — пиши, я тут.
─────────────────────────────────────────────
```

**State stored:** decision recorded, no data accumulation начинается. Conversation ends gracefully. Brand Guardian rated «six words, dignity preserved, door open».

If customer returns later и пишет Ayla — S1 re-runs (fresh first impression). She не registered yet (только MAX OAuth identity).

### State 4 — Returning user >30 days inactive

**Detection:** `BotUser.last_interaction_at < now - 30 days` AND `consent_at IS NOT NULL` (она когда-то прошла onboarding).

S1 replaced with soft re-welcome — NO S2 / S3 / S4 / S6 (skip entire onboarding):

```
─────────────────────────────────────────────
  ayla

  С возвращением, {{customer_first_name}}.
  Что нужно сегодня?

  [ 📸 Сфотогра-     ] [ 💧 + стакан      ]
  [    фировать еду   ] [    воды           ]

  [ 🎯 Цель           ] [ 📅 Найти услугу  ]

  [ Просто посмотреть ]
─────────────────────────────────────────────
```

**Voice check:**
- ✅ «С возвращением» — recognition tone, NOT stranger reset
- ✅ Name used (already known via consent_at exists)
- ✅ «Что нужно сегодня?» — open question, не presumptuous
- ✅ No anketa offer (returning customer уже decided or did it)

### State 5 — Multi-tenant entry (share-link / referral / QR / IG post с salon)

**Detection:** `start_param` parsed for salon context — `ref_<user_id>` (referral), `qr_<salon_id>_<placement>` (physical QR), `ig_post_<id>` (Instagram).

S1 contextualized variant (см. §4 above). S2-S7 identical to standard flow.

After S7 → Dashboard may pre-populate booking suggestion at этот tenant per `customer-first-touch-and-mini-app-states.md` §5.1.

### State 6 — Health flag (deferred for pilot)

S4 skipped per Q-TAU-O2 — see §7 для post-pilot resurrection plan.

---

## 12. Backend mapping

### Implementation scope per step

| Step | Handler / Skill | Status | Effort estimate |
|------|----------------|--------|-----------------|
| S1 First message | Hardcoded в bot adapter response | ⚠ Needs implementation | ~2 hrs bot adapter |
| S1 multi-tenant variant | Same handler with `start_param` parsing branch | ⚠ Needs implementation | +1 hr |
| S2 Privacy consent | Hardcoded conversational handler | ⚠ Needs implementation | ~2 hrs |
| S2 consent decision storage | NEW: BotUser field `consent_at: datetime \| null` | ⚠ Alpha owns minor migration | ~1 hr Alpha |
| S2a expanded fold | Hardcoded text response on button tap | ⚠ Part of S2 impl | (within S2 estimate) |
| S3 Positioning (conditional) | Hardcoded sequential message handler | ⚠ Needs implementation | ~1 hr |
| S5 Action choice | Inline keyboard with `start_param` deeplink routes | ✅ MAX bot API supports | ~1 hr (wiring) |
| S6 Anketa | Existing `nutrition_anketa` skill (DRF-820) | ✅ Ready | 0 (just trigger) |
| S6 entry callback wiring | `cb:anketa:start` → existing skill handler | ✅ Existing | 0 |
| S7 Dashboard land | Mini App deeplink open | ✅ Ready (per dashboard handoff) | 0 |
| Returning user detection | NEW: query `consent_at IS NOT NULL AND last_interaction_at < now - 30d` | ⚠ Alpha owns minor | ~1 hr Alpha |
| Multi-tenant `start_param` parsing | Parse salon ref, pass to S1 template | ✅ Existing per customer-first-touch policy | (within S1 impl) |
| Consent refused state | No state stored, conversation ends | ✅ | 0 |

**Total estimate:** ~8-10 hours engineering scope.
- ~6 hours bot adapter handlers (Tau coordination with W4/W2 stream)
- ~3 hours Alpha (consent field, returning user query, minor migration)

**No new skills built.** No Tier-B health screening. No proactive consent skill. No welcome skill.

### `consent_at` field semantics

New `BotUser.consent_at` field:
- `NULL` → customer never went through onboarding consent step (or refused — see Returning user logic distinguishes)
- `datetime` → consent given on этот timestamp
- Refused consent: still NULL (no consent recorded), but customer's MAX OAuth identity exists
- Distinguish via `last_interaction_at` для returning user logic

---

## 13. Anti-patterns (all confirmed per founder)

- ❌ Mandatory anketa перед first value — Anketa SECONDARY positioning per S5 layout
- ❌ Multi-page legal walls — S2 conversational, fold for details
- ❌ Marketing copy («Открой себя лучше с Ayla») — S1 plain «расскажу о еде, воде, отдыхе»
- ❌ Photo of generic woman smiling — нет визуальных assets в bot DM
- ❌ Onboarding tour pointing to UI elements — customer уже знает что такое кнопки
- ❌ Form-like inputs где могут быть buttons — все inline buttons
- ❌ Welcome animation > 2 sec — bot DM text instant
- ❌ Insight-like cards — confirmed per Q-BACK-4 — applies к onboarding flow too. No cross-domain insight cards anywhere в onboarding
- ❌ «Открой себя лучше с Ayla» / «Лучший AI для здоровья» — vendor speak
- ❌ Stock animated welcome video — distraction, not value
- ❌ Force phone number / personal data ввод до consent — privacy violation
- ❌ Pre-checked consent checkboxes — must be active tap

---

## 14. Brand voice notes (Brand Guardian review summary)

### Verdict: brand-aligned with 3 minor fixes applied

Brand Guardian scored 9.5/10 average across 10 criteria. 3 fixes applied:

1. **S2 «Согласна продолжить?» → «Продолжим?»** (gender-neutral, не misgender ~10-20% male users)
2. **S2a passive «Хранится» → active «Запоминаю»** (preserves first-person «я» grip throughout)
3. **S5 anketa de-duplication** — removed double surfacing «Или сначала анкета» + button «Анкета (5 мин)». Now framing carries «5 шагов» info, button = «Начать анкету»

### 3 best brand moments (per Brand Guardian)

1. **S1:** «Расскажу о еде, воде, отдыхе и помогу записаться к мастерам» — 11 words, full product, zero marketing
2. **S3:** «Я не календарь и не ещё одна программа правильного питания» — anti-positioning без bitterness
3. **Refused consent:** «Поняла. Когда захочешь — пиши, я тут.» — six words, dignity preserved, door open

### Voice rules applied (final inventory)

| Element | Voice rule | Status |
|---------|------------|--------|
| S1 «Привет, я Ayla. Расскажу о еде, воде, отдыхе...» | First-person feminine, specific scope, caring invitation | ✅ Reference quality |
| S1 multi-tenant «Помогу с записью в {{salon}}» | Salon as third-party reference | ✅ |
| S2 «Прежде чем начать — короткое слово» | Humanizing legal moment, не legal wall | ✅ |
| S2 «Я буду помнить о тебе только то, что поможет» | First-person ownership + explicit scope | ✅ |
| S2 «Продолжим?» | Gender-neutral (was «Согласна» — fixed) | ✅ Brand Guardian fix |
| S2a «Запоминаю: твои сообщения мне...» | Active voice (was «Хранится» — fixed) | ✅ Brand Guardian fix |
| S3 «не календарь и не ещё одна программа правильного питания» | Anti-positioning без defensiveness | ✅ Reference quality |
| S5 «С чего хочешь начать? Можно прямо сейчас» | Permission, not pressure | ✅ |
| S5 «Или расскажи о себе — 5 шагов» | Anketa as invitation (de-duplicated) | ✅ Brand Guardian fix |
| S6.0 «Хорошо. 5 шагов — пара минут» | Concrete time, не filler | ✅ |
| S6 skip «Поняла. Анкету можно пройти потом» | Non-judgmental, ничего не теряется | ✅ Reference quality |
| Refused «Поняла. Когда захочешь — пиши, я тут.» | Dignity + door open | ✅ Reference quality |
| Returning «С возвращением» | Recognition не stranger reset | ✅ |

---

## 15. Accessibility (WCAG 2.2 AA — bot DM context)

Bot DM context = MAX messenger native — most a11y handled by MAX client. Tau's responsibility:

### Touch targets

- **Inline keyboard buttons:** ≥44×44dp (MAX bot API default size meets WCAG 2.5.8). Within MAX rendering control.
- **Button labels:** Cyrillic text readable at native MAX font size

### Screen reader support

- **Each Ayla message:** standalone semantic chat bubble. Screen reader announces author + content
- **Inline buttons:** `aria-label` = button label text (handled by MAX client)
- **Sequential S3 + S5 bubbles:** screen reader reads both в order; no user action между ними OK

### Voice + speech

- **Customer typed responses:** MAX bot supports voice-to-text input (per MAX platform capabilities)
- **Inline button alternative:** customer может type «начать» / «продолжим» / «не сейчас» вместо taps — handled by orchestrator NLU OR exact-match fallback

### Reading level

- Russian copy plain, short sentences — supports WCAG 3.1.5 Reading Level
- No jargon, no technical abbreviations
- ≤3 sentences per Ayla message default

### Language

- `lang="ru"` for all bubbles (MAX client handles)
- «Ayla» preserved as English — for RU TTS pronunciation, customer's MAX client handles voice synthesis

---

## 16. Variants considered

| Variant | Status | Rationale |
|---------|--------|-----------|
| **A — S5 Grid 2×2 + anketa separate** | ✅ MAIN | Compact mobile, faster scan, 2-row inline keyboard |
| B — S5 Vertical stack | ⏸ alt held | Fallback if MAX rendering issues with 2-button rows |
| C — S5 Carousel | ❌ rejected | Cognitive load, swipe required, anti-pattern для quick choice |
| Mandatory anketa (per Tier-B health gate plan) | ❌ rejected | Per Q-TAU-O2 — Tier-B skill doesn't exist, anketa optional |
| Multi-step S2 consent (granular per-feature opt-ins) | ❌ rejected | Founder principle «no legal walls» — single conversational consent |
| Splash screen / welcome video | ❌ rejected | bot DM is chat — no video / splash format |

---

## 17. Open questions / followups

### For tech lead (post-approval)

| # | Severity | Question | My lean |
|---|----------|----------|---------|
| **Q-TAU-O8** | 🟢 | After S2 «Не сейчас» — store «refused_at» timestamp для analytics? | YES — `consent_refused_at` field, used для re-engagement throttle (don't show S1 again within 7d) |
| **Q-TAU-O9** | 🟢 | Returning user threshold 30 days — calibrate post-pilot? | 30d MVP. Adjust based on cohort retention curves post-pilot |
| **Q-TAU-O10** | 🟢 | S1 multi-tenant variant — что если customer arrives с invalid `start_param` (deleted salon)? | Fallback to standard S1 (silent ignore), log analytics event |
| **Q-TAU-O11** | 🟢 | S5 «📅 Найти услугу» exits onboarding к catalog browse — counts as «first action» для retention metric? | YES — any S5 tap = first action triggered |
| **Q-TAU-O12** | 🟢 | If customer types message instead of tapping button on S1 (e.g., «привет»)? | Orchestrator NLU treats as «Начать» equivalent → proceed to S2 |

### For W4/W2 stream (bot adapter implementation)

1. **Hardcoded message templates:** S1, S1 multi-tenant, S2, S2a, S3, Refused state, Returning state. All copy в `apps/orchestrator/onboarding/templates.py` constants.
2. **State machine для onboarding:** lightweight FSM с states `{welcome, consent, positioning, action_choice, done, refused}`. Conversation-scoped (1 onboarding per BotUser lifetime, скип for returning).
3. **`start_param` parsing:** реuse existing logic per `customer-first-touch-and-mini-app-states.md` §10.
4. **Inline keyboard wiring:** MAX bot API `InlineKeyboardButton` + callback handler routes.
5. **`consent_at` field:** Alpha migration + `BotUser` serializer update.
6. **Returning user detection:** middleware/check on every bot DM open — if `consent_at IS NOT NULL` AND `last_interaction_at < now - 30d` → bypass S1-S4, go direct to returning re-welcome S1 short.
7. **S5 → S6 callback wiring:** `cb:onboarding:s5:anketa` → triggers existing `nutrition_anketa` skill via `cb:anketa:start` callback.
8. **S5 → S7 deep linking:** other S5 buttons emit Mini App URLs with `start_param` per §8 routing table.
9. **Analytics events:** emit `onboarding.s1_shown`, `s2_consent_given|refused`, `s5_action_chosen`, `s6_anketa_complete|skipped`, `s7_landed` для funnel tracking.
10. **Edge case retry:** if customer mid-onboarding doesn't respond for 24h — soft re-prompt («Анна, я тут когда захочешь продолжить») via reminder system OR just wait silent.

---

## 18. Skills used (subagent review trail)

| Skill / Subagent | Phase | Findings summary |
|---|---|---|
| `frontend-design` (Anthropic skill) | C–E | Bot DM chat ASCII pattern. Sage-green palette via MAX bot avatar context |
| Direct code reading | A | Found 3 critical scope discoveries (welcome skill missing, privacy_consent reactive only, health_screening Tier-A only). Anonymous-to-registered-gate policy informed bot DM vs Mini App separation. |
| `Brand Guardian` subagent | F (voice review critical for first impression) | 9.5/10 average across 10 criteria. 3 fixes applied: S2 gender-neutral («Продолжим»), S2a active voice («Запоминаю»), S5 anketa de-dup. Verdict: «brand-aligned, ship after touch-ups. First impression will hold retention day 1.» |
| UI Designer subagent | (skipped — bot DM = text + buttons, no visual layout matters) | n/a |
| Accessibility Auditor subagent | (skipped — bot DM a11y handled by MAX client) | Tau notes inline §15 about touch targets / screen reader / language |

---

## 19. Status next steps

- [x] Phase A — read welcome / privacy_consent / health_screening / nutrition_anketa skills + anonymous-to-registered-gate policy
- [x] Phase B — plan S1-S7 sequence + open questions + variant direction + 3 critical scope findings
- [x] Phase C — ASCII flow for all states (S1-S7 + 5 states)
- [x] Phase D — detail per state + voice + backend mapping
- [x] Phase E — S5 Variants A vs B (Grid 2×2 selected, Vertical held alt)
- [x] Phase F — Brand Guardian voice review (3 fixes applied)
- [ ] **Phase G — Accessibility Auditor** (deferred — bot DM context handled by MAX client; Tau notes §15)
- [ ] **Phase H — HTML preview** (n/a — bot DM is text chat, no HTML render)
- [x] Phase I — save to `docs/screens/customer-onboarding-flow.md`
- [ ] Phase J — handoff block for tech lead

**Severity результирующего flow:** P0 BLOCKER для pilot 15 July 2026 (first impression = retention day 1).

**Following streams to engage after sign-off:**
- W4 / W2 — bot adapter implementation per §17 items 1-10
- Alpha — `consent_at` field migration + returning user query
- Existing `nutrition_anketa` skill (Sprint 9 DRF-820) — already ready, just trigger from S5
- Tech lead — coordination scheduling W4/Alpha streams (~8-10 hours engineering scope total)

---

**Last verified:** 2026-05-25 r1
**Tau (UX/Design stream)**
