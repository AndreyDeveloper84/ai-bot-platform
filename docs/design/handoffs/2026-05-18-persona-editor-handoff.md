# Persona Editor — Developer Handoff Package

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Designer** | UX-architect skill |
| **Status** | Draft for review |
| **Surfaces** | Web dashboard (primary) + MAX manager-bot (notifications only) |
| **Scope** | Persona configuration UI — identity, voice/tone, forbidden phrases, greeting, explicit-human policy, live preview, history/rollback |
| **Auth** | **Owner role ONLY** per [conversation-ownership-policy §4](../policies/conversation-ownership-policy.md) |
| **Screens** | 1 main editor + 3 modals (TONE_LEARNING entry / history / preview-as-customer) |

## Foundation references (read first)

| Doc | Why it matters |
|---|---|
| [`docs/design/assistant-persona.md`](../policies/assistant-persona.md) | Voice/tone rules — this UI is the operational interface to that policy |
| [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md) | Identity is foundational — editor must preserve invariants (truthful disclosure, no «bot» in customer copy) |
| [`docs/design/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) | §4 permissions: Owner-only access; §7 identity disclosure rules drive explicit-human policy section |
| [`docs/design/2026-05-17-conversations-handoff.md`](./2026-05-17-conversations-handoff.md) | Screen C4 Learning Queue TONE_LEARNING type — accept flow opens this editor with diff |
| [`docs/design/decisions-log.md`](../decisions-log.md) | P1, P2, P4 locked; Q-CX11 confirmed (per-tenant name); Q-M10 confirmed (no «Помощник Анна» framing) |

---

## 0. Overview

### What this module is
The Owner's operational interface to tune the salon's AI-assistant voice. Per [`assistant-persona.md`](../policies/assistant-persona.md), every customer-facing message runs through persona rules. This UI is **the only place** those rules are configured.

### Why this matters
- Persona = brand voice = first impression to every customer
- Salon's voice differentiates from generic «AI bot»
- Owner controls voice without dev intervention
- TONE_LEARNING from Learning Queue feeds into this — compounding refinement
- Bad persona settings → angry customers → churn

### Primary persona — «Karina» (Owner)
- Non-technical but understands «голос важен для бренда»
- Will tune at onboarding (Phase 5) + occasionally post-launch (~1–3 times in first 3 months)
- Mobile and desktop access
- Doesn't want to read persona policy documentation — UI must guide through best practices implicitly

### Secondary access — None
Admin (Anya), Receptionist, Master roles: **read-only view** (can see current persona) but cannot edit. Per permissions matrix.

### JTBD
> «Когда я хочу настроить голос помощника под мой салон, я хочу увидеть как клиенты будут это видеть **до того как опубликую** — чтобы поправить тон до того как клиенты столкнутся с неправильным голосом.»

### Success metrics
| Metric | Target | Type |
|---|---|---|
| **Time-to-first-edit** (after Phase 5 onboarding) | < 14 days median for engaged tenants | Engagement |
| Persona edits per active tenant per month | ≥ 0.5 (active tuning) | Health |
| **TONE_LEARNING acceptance rate** (Learning Queue → applied) | ≥ 40% of proposed | Quality (feedback loop works) |
| Customer satisfaction post-tune (rating delta) | + 0.1★ within 30 days of tune | Outcome |
| Persona violation rate in customer-facing messages (post-save) | < 2% (per persona §9 quality check) | Safety |
| Rollback rate (within 24h grace) | < 10% (well-targeted tunes; high = bad UX or bad LLM previews) | UX validation |

---

## 1. State machine

```
VIEWING (current persona, read-only display) →
  Click «Изменить»
    ↓
  EDITING (form sections active, preview updates live)
    ├─→ Preview-as-customer modal (full conversation simulation)
    │
    ├─→ History modal (audit log, rollback options)
    │
    └─→ Click «Сохранить»
        ↓
      VALIDATING (pre-save quality check runs)
        ├─ FAIL → show errors inline, return to EDITING
        └─ PASS → SAVING → SAVED (toast + 24h grace banner)
            │
            └─ Within 24h: ROLLBACK_AVAILABLE
                └─ Click «Откатить» → CONFIRMING → ROLLED_BACK → VIEWING

Entry from Learning Queue (TONE_LEARNING accept) →
  TONE_DIFF_PREVIEW modal (showing proposed change) →
    ├─ Apply → EDITING (pre-populated with proposed change) →
    │          → ... → SAVED
    └─ Reject → returns to Learning Queue (no change)
```

---

## 2. Routes

- `/settings/persona` — main editor (Owner only; redirect for others with «недостаточно прав»)
- `/settings/persona/history` — full audit log + version diffs
- `/settings/persona/preview` — preview-as-customer simulation (modal route)

Entry path from Learning Queue:
- `/conversations/learning/{id}` → click «Применить» on TONE_LEARNING → `/settings/persona?from_learning=ID` (opens editor with diff overlay)

---

## 3. Screen P1 — Main Persona Editor (desktop layout)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Студия Карина   [Setup ✓]   [Karina, owner ▾]                                │
├──────────────────────────────────────────────────────────────────────────────┤
│ Dashboard │Каталог│Диалоги│Аналитика│Биллинг│ Настройки                       │
├──────────────────────────────────────────────────────────────────────────────┤
│ Настройки → Помощник                                                         │
│ ──────────────                                                               │
│ [Голос] [Политика] [История]   ← sub-tabs                                    │
├─────────────────────────────────────────┬────────────────────────────────────┤
│ ВОЛОС ПОМОЩНИКА                          │ ПРЕВЬЮ                             │
│                                          │                                    │
│ ── Имя ──                                │ Как клиент увидит вашего помощника:│
│ Как клиент видит помощника:              │                                    │
│ ┌──────────────────────────────────────┐│ ┌────────────────────────────────┐ │
│ │ Помощница студии Карина              ││ │ ── Приветствие ──              │ │
│ └──────────────────────────────────────┘│ │ Помощница студии Карина:       │ │
│ Род: ⦿ Женский ◯ Мужской ◯ Нейтральный  │ │ Здравствуйте! Я помощница      │ │
│                                          │ │ студии Карина. Помогу          │ │
│ ── Тон ──                                │ │ записаться, расскажу о ценах   │ │
│ [Сдержанный ──●──── Тёплый ──── Игривый] │ │ и услугах. С чего начнём?      │ │
│                                          │ └────────────────────────────────┘ │
│ Текущий: тёплый                          │                                    │
│                                          │ ┌────────────────────────────────┐ │
│ ── Кастомные запретные фразы ──          │ │ ── Вопрос о цене ──            │ │
│ В дополнение к платформенным правилам:   │ │ Помощница студии Карина:       │ │
│                                          │ │ Маникюр гель-лак — 2 200 ₽,    │ │
│ ┌──────────────────────────┐             │ │ 90 минут. Хотите записаться?   │ │
│ │ × «скидка»  × «акция»    │             │ └────────────────────────────────┘ │
│ │ × «спец предложение»     │             │                                    │
│ │ + Добавить фразу         │             │ ┌────────────────────────────────┐ │
│ └──────────────────────────┘             │ │ ── Сложная ситуация ──         │ │
│ ⓘ Платформенные правила всегда           │ │ Помощница студии Карина:       │ │
│   применяются — см. assistant-persona.md │ │ Передаю руководителю салона —  │ │
│                                          │ │ она свяжется с вами в течение  │ │
│ ── Кастомное приветствие ──              │ │ часа.                          │ │
│ ┌────────────────────────────────────┐  │ └────────────────────────────────┘ │
│ │ Здравствуйте! Я помощница студии   │  │                                    │
│ │ Карина. Помогу записаться,         │  │ [Развернуть все примеры →]         │
│ │ расскажу о ценах и услугах.        │  │ [Превью как клиент →]              │
│ │ С чего начнём?                     │  │                                    │
│ └────────────────────────────────────┘  │ Превью обновляется при изменениях   │
│ ⚠ Платформа проверит на запрещённые     │ ~500ms                              │
│   фразы перед сохранением               │                                    │
│                                          │                                    │
├─────────────────────────────────────────┴────────────────────────────────────┤
│ [Сбросить] [Превью как клиент] [История изменений]      [Сохранить]          │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Mobile layout (<768px)
Tabs collapse to dropdown, preview moves to fixed-bottom collapsible drawer:

```
┌────────────────────────────────────┐
│ ← Настройки → Помощник             │
├────────────────────────────────────┤
│ [Голос ▾]                          │
├────────────────────────────────────┤
│ ── Имя ──                          │
│ [ Помощница студии Карина      ]   │
│ ⦿ Женский ◯ Мужской ◯ Нейтральный  │
│                                    │
│ ── Тон ──                          │
│ Сдержанный ─●── Тёплый ─── Игривый │
│                                    │
│ ── Запретные фразы (3) ─────►      │
│ ── Приветствие ─────────────►      │
├────────────────────────────────────┤
│  ▲ Превью                          │  ← swipe-up reveal
│  Помощница: «Здравствуйте! Я...»   │
├────────────────────────────────────┤
│ [Сохранить]                        │
└────────────────────────────────────┘
```

### Section 3.1 — Identity (Имя)

**Fields:**
- **Name input** (`max-length=80`, no special chars except `,.-—`)
  - Placeholder: «Например, «Помощница студии Карина»»
  - Real-time validation: at least 3 chars, no «бот» substring (truthful disclosure mandate)
- **Gender radio**: Женский / Мужской / Нейтральный
  - Affects auto-generated examples ONLY (e.g., «я помог[ла|]» — grammar agreement)
  - Per Q-CX11 confirmed: per-tenant configuration
  - Per Q-M10 confirmed: stays as ONE identity — no master-name personalization

**Validation:**
- Empty name → error «Укажите имя помощника»
- Contains «бот» → warning «Слово «бот» нежелательно — нарушает доверие к помощнику»
- Too long (>80 chars) → error «Слишком длинное имя для отображения»

### Section 3.2 — Tone slider

**3-stop slider** per [P2 locked decision](../decisions-log.md):
- **Сдержанный** — деловой, лаконичный (для премиум-салонов, медицинских)
- **Тёплый** (default middle) — заботливый, сбалансированный
- **Игривый** — light-hearted, с эмоциями (для молодёжных салонов)

Per persona doc §6, tone modulates LLM system prompt — not random style. Examples auto-generated in preview reflect chosen tone.

**Anti-pattern guard**: «Игривый» does NOT mean emoji explosion. Persona §5 forbidden-phrases still apply.

### Section 3.3 — Custom forbidden phrases

**Chip-list editor.** Tenant adds phrases to avoid IN ADDITION to platform-wide forbidden list (per [`assistant-persona.md`](../policies/assistant-persona.md) §5).

UI:
- Existing chips: `× «скидка»` `× «акция»` `× «спец предложение»`
- Add input: «Введите фразу или слово»
- Inline validation: no duplicates with platform list (silent dedup)
- Click `×` removes

**Storage:** `Persona.custom_forbidden_phrases: string[]` — case-insensitive match at LLM filter step.

**Edge case:** tenant adds «дешевле» as forbidden → would conflict with legitimate price comparison? UI doesn't enforce semantic — saves what owner typed. Founder can revoke if affects too many tenants.

### Section 3.4 — Custom greeting

**Textarea** for B1 default greeting override. Per customer-first-time handoff §3 templates.

- Max 250 chars (enough for warm greeting, not essay)
- Placeholder default: «Здравствуйте! Я помощница студии Карина. Помогу записаться, расскажу о ценах и услугах. С чего начнём?»
- Pre-save validation: persona quality check (§9 of assistant-persona.md):
  - No forbidden platform phrases
  - No forbidden tenant phrases
  - Length within limits
  - Tone matches slider position
  - Identity check (uses tenant-configured name)

If validation fails → inline errors below textarea + Save button disabled until resolved.

### Section 3.5 — Policy sub-tab → Explicit-human disclosure rules

**Radio group** per [conversation-ownership-policy §7](../policies/conversation-ownership-policy.md):

- **⦿ Только в чувствительных темах** (default) — explicit admin name shown only for medical, refund, legal scenarios. Per ownership-policy framing table.
- **◯ Всегда показывать имя ответившего** — every admin reply prefixed with name (e.g., «Анна, администратор: ...»). For salons requiring transparency.
- **◯ Никогда** — assistant identity is total; admin replies render as assistant always. **Legal warning shown**: «Платформа сохраняет требование честного ответа на вопрос «вы бот?» — этот режим не отменяет ФЗ-152 и этики.»

Default: «Только в чувствительных темах» per single-assistant identity policy.

### Section 3.6 — History sub-tab

Lightweight log of persona changes. Each row:

```
22 мая 14:30 — Karina (owner)
  Тон: тёплый → игривый
  Запретные фразы: +«скидка»
  [Превью этой версии]  [Откатить к этой версии]
```

Shows last 20 changes; full history in `/settings/persona/history` route.

Rollback flow:
1. Click «Откатить к этой версии»
2. Confirm modal: «Откатить помощника к настройкам от 22 мая 14:30?»
3. Confirm → versions swap atomically (active conversations affected per edge case 4 below)
4. Toast: «Откачено. Новая версия → активная.»
5. Audit event logged

---

## 4. Preview pane (right side of Screen P1)

### Live preview rules

- Updates **500ms debounced** after any form field change
- Loading state: opacity 0.6 + small spinner overlay
- Failure state: «Превью временно недоступно — настройки сохраняются как есть»

### Preview intents (5 default examples)

1. **Greeting** (B1 template applied)
2. **Price question** («Сколько стоит маникюр гель-лак?»)
3. **Booking initiation** («Хочу записаться к Анне»)
4. **Sensitive topic** (complaint or medical question → shows team-framing per persona §4)
5. **Customer asks «вы бот?»** (truthful disclosure mandate test)

Each shown as message bubble in MAX-style chat preview.

### Preview-as-customer modal

`[Превью как клиент →]` opens a full simulation:

```
┌──────────────────────────────────────────────────┐
│ Превью разговора с клиентом                   ✕  │
├──────────────────────────────────────────────────┤
│ Введите сообщение, чтобы увидеть как ответит    │
│ помощник:                                        │
│                                                  │
│ [ ... type something ... ]               [Отпр.] │
│                                                  │
│ ┌──────────────────────────────────────────────┐ │
│ │ Вы: А вы делаете маникюр + педикюр в один   │ │
│ │     день?                                     │ │
│ │                                                │ │
│ │ Помощница студии Карина: Да! Маникюр + педи- │ │
│ │     кюр (комплекс) — 180 минут, 4 200 ₽.     │ │
│ │     Удобно?                                   │ │
│ └──────────────────────────────────────────────┘ │
│                                                  │
│ ⓘ Это симуляция — реальные ответы зависят от    │
│   полного контекста разговора с клиентом         │
│                                                  │
│              [Закрыть]                           │
└──────────────────────────────────────────────────┘
```

LLM generates response using current draft persona + catalog data. Owner can test «уйти как клиент» before saving.

---

## 5. TONE_LEARNING acceptance flow (modal)

Triggered when owner clicks «Применить» on a TONE_LEARNING suggestion from Learning Queue Screen C4.

### Layout

```
┌────────────────────────────────────────────────────────────────┐
│ Применить рекомендацию помощника                            ✕  │
├────────────────────────────────────────────────────────────────┤
│ Помощник заметил: в последних 5 диалогах с жалобами вы        │
│ переписывали черновик.                                         │
│                                                                │
│ ┌──────────────────────────────────────────────────────────┐  │
│ │ ПОМОЩНИК СЕЙЧАС ПИШЕТ:                                   │  │
│ │ «Понимаем вашу обеспокоенность»                          │  │
│ ├──────────────────────────────────────────────────────────┤  │
│ │ ВЫ ОБЫЧНО ПИШЕТЕ:                                        │  │
│ │ «Мне очень жаль, что это случилось»                     │  │
│ └──────────────────────────────────────────────────────────┘  │
│                                                                │
│ Помощник предлагает:                                           │
│ • Усилить эмпатию в шаблонах ответа на жалобы                │
│ • Использовать «мне жаль» вместо «понимаем» для acknowledgement│
│                                                                │
│ ── Превью ──                                                   │
│ ┌──────────────────────────────────────────────────────────┐  │
│ │ Было: «Понимаем вашу обеспокоенность. Передам руковод...» │ │
│ │ Станет: «Мне очень жаль, что это случилось. Передаю ру...» │ │
│ └──────────────────────────────────────────────────────────┘  │
│                                                                │
│ [✗ Отмена]  [✎ Изменить]  [✓ Применить]                       │
└────────────────────────────────────────────────────────────────┘
```

**Click «Применить»** → updates persona accordingly (specific config depends on what TONE_LEARNING proposed):
- Tone slider adjustment OR
- Tenant-specific tone template snippets stored in `Persona.custom_response_templates` (new field) for specific intents
- Returns to Learning Queue with success toast

**Click «Изменить»** → opens Persona Editor pre-populated with proposed change highlighted in yellow.

**Click «Отмена»** → returns to Learning Queue without change; suggestion marked REJECTED with feedback signal.

---

## 6. States (Screen P1 — main editor)

| State | Behavior |
|---|---|
| Loading | Skeleton 4 sections + skeleton preview |
| **Empty (no edits ever — post-onboarding)** | Shows default platform values, banner: «Это базовые настройки. Настройте под бренд салона.» |
| Populated | As drawn above |
| Editing (dirty) | «Сохранить» button enabled, dirty indicator on tab, beforeUnload warning |
| Validating | Save button → spinner, status «Проверяю настройки» |
| Validation error | Inline errors per field + summary banner at top |
| Saving | Save button → spinner, lock all fields |
| Saved (just) | Toast «Сохранено» + 24h rollback banner at top |
| Rollback grace (within 24h post-save) | Banner: «Откатить к версии до 22 мая 14:30 — есть 23 ч 47 мин» с counter |
| TONE_LEARNING entered | Diff overlay shown above editor with yellow highlight on proposed changes |
| Error (save fails) | Banner with retry; persists dirty state |
| Offline | Banner «Нет связи — сохранится когда вернётся» + IndexedDB queue |
| Read-only (admin opens) | Lock icon on all fields + banner «Только владелец салона может изменять. Запросить у Karina» |

---

## 7. Components inventory

| Component | Purpose |
|---|---|
| `PersonaIdentitySection` | Name input + gender radio |
| `ToneSlider` | 3-stop slider with labels |
| `ForbiddenPhrasesEditor` | Chip-list with add/remove |
| `GreetingTemplateEditor` | Textarea with live persona quality check |
| `PolicyRadioGroup` | 3 radios for explicit-human disclosure |
| `LivePreviewPane` | 5-intent preview with debounced LLM regen |
| `PreviewAsCustomerModal` | Full chat simulation |
| `HistoryTable` | Audit log with rollback action |
| `RollbackBanner` | 24h grace countdown with rollback action |
| `ToneLearningDiffModal` | Entry from Learning Queue with diff |
| `ReadOnlyOverlay` | For admin/receptionist/master viewing |

---

## 8. Backend contracts

```
GET /api/v1/persona
  Response: {
    id: uuid,
    name: str,
    gender: "female" | "male" | "neutral",
    tone: "restrained" | "warm" | "playful",
    custom_forbidden_phrases: [str],
    greeting_template: str,
    explicit_human_policy: "sensitive_only" | "always" | "never",
    last_modified_at: ISO,
    last_modified_by: { user_id, name, role },
    version: int  // increments on save
  }

PATCH /api/v1/persona
  Body: partial of above
  Pre-save: runs quality check (assistant-persona.md §9)
  Response 200: updated Persona
  Response 422: { errors: [{ field, message, rule_id }] }
  Side-effects:
    - persona_changed audit event
    - LLM re-load persona at runtime (cache invalidation)
    - 24h rollback window opens

POST /api/v1/persona/preview
  Body: {
    draft_persona: Partial<Persona>,  // not saved yet
    intent: "greeting" | "price_question" | "booking" | "sensitive" | "are_you_bot"
  }
  Response: { sample_text: str, generation_time_ms: int }
  Rate limit: 60 requests/min/tenant (preview spam protection)

POST /api/v1/persona/preview-as-customer
  Body: {
    draft_persona: Partial<Persona>,
    user_message: str (max 500 chars)
  }
  Response: { assistant_reply: str, generation_time_ms: int }

GET /api/v1/persona/history
  Query: ?limit=20&before=cursor
  Response: { history: [PersonaVersion], next_cursor }
  PersonaVersion: { version, changed_at, changed_by, diff: [{field, from, to}] }

POST /api/v1/persona/rollback
  Body: { target_version: int }
  Response 200: new active Persona (incremented version)
  Audit: persona_rolled_back event

POST /api/v1/persona/quality-check
  Body: { draft_persona }
  Response: { ok: bool, violations: [{ rule, field, message }] }
```

### Cache invalidation
After PATCH or rollback:
- LLM runtime cache evicted (within 30 seconds globally)
- In-flight LLM completions: complete with old persona (atomicity within request)
- New customer messages: use new persona immediately

---

## 9. A11y considerations

- All form sections labeled with `<fieldset><legend>`
- Tone slider: keyboard accessible (Tab + Arrow Left/Right) + `aria-valuetext` describing current stop
- Forbidden phrases chips: each chip is a button with `aria-label="Удалить фразу X"`
- Preview pane has `aria-live="polite"` so screen readers announce update
- Save button disabled state has `aria-disabled="true"` + tooltip explaining why
- Read-only mode: every input has `aria-readonly="true"` + visible lock icon
- TONE_LEARNING modal: focus trap + Esc closes + restored focus on close
- Diff view in history: semantic `<del>` / `<ins>` not just CSS color
- High-contrast: tone slider stops visible even without color

---

## 10. Edge cases

- **Save while preview LLM in flight** → previews abort, save proceeds with form state
- **Concurrent edits from 2 browsers (Owner has 2 sessions)** → optimistic UI; last-write-wins; second tab gets toast «Persona обновлён в другой вкладке — обновите страницу»
- **Active conversations during save** → atomic swap; in-flight LLM completions finish with old persona, next message in same conversation uses new
- **Rollback while 24h grace AND active conversation in progress** → new messages in active conversation use rolled-back version; toast warns owner «Активные диалоги продолжатся с новой настройкой»
- **Customer sees old greeting (cached client-side)** → cache TTL ≤ 10 min; mismatch acceptable for brief window
- **TONE_LEARNING applied, then immediately rolled back** → suggestion marked REJECTED with audit reason «applied_then_rolled_back» → AI suppresses similar suggestions for 90 days
- **Owner sets «никогда» for explicit human policy** → confirmation modal with explicit legal warning + audit event capturing the warning was shown
- **Custom greeting fails persona quality check** → save blocked with specific rule violations highlighted
- **Persona quality check fails after deploy (bug)** → fallback to last known good persona; admin notified via MAX manager-bot
- **Persona has emoji in custom greeting** → warn but allow (tenant choice); if emoji decoration in chrome positions → block with rule explanation
- **Tenant adds extremely long forbidden phrase list (>100 entries)** → soft cap warning at 50; hard cap at 100; UI shows count
- **Custom forbidden phrase coincides with master name** (e.g., adds «Анна» as forbidden) → warn at save «Слово совпадает с именем мастера — помощник не сможет упоминать её»

---

## 11. Anti-slop scan (12-point)

| # | Check | Status |
|---|---|---|
| 1 | Inter default | ✅ MAX UI lib / system fonts |
| 2 | Purple gradient | ✅ salon-warmth palette |
| 3 | Glassmorphism | ✅ no glass |
| 4 | Radius scale | ✅ 8/12 |
| 5 | Emoji decoration | ⚠ ⦿ ◯ × ⓘ ⚠ ⏷ — semantic UI icons; на проде Lucide equivalents. Tone slider examples in preview may use platform-mandated emoji (e.g., success ✓) only where semantic |
| 6 | Centered hero / single CTA | n/a — settings page |
| 7 | AI illustrations | ✅ |
| 8 | Gradient overlay | ✅ |
| 9 | Specific copy | ✅ «в последних 5 диалогах», «есть 23 ч 47 мин» |
| 10 | Avatars | n/a |
| 11 | Animation restrained | ✅ only debounced preview update + 24h grace countdown |
| 12 | Slate-on-slate | ✅ |

**11/12 ✅, 1 fix (replace UI emoji with Lucide on production).**

---

## 12. Cross-screen integration

| Source | Integration |
|---|---|
| **Onboarding Phase 5** (Train RAG + persona) | Same components reused in wizard wrapper; saves persona at Phase 5 completion. After activation, full editor accessible at `/settings/persona`. |
| **Learning Queue C4** (TONE_LEARNING accept) | Opens this editor with diff overlay via `?from_learning=ID` param |
| **Conversations C2** (admin reply box) | Pre-send check runs persona quality rules; failures warn admin (but persona itself NOT editable from there) |
| **MAX manager-bot** | Notification when persona-quality check fails on outbound message (rare but indicates persona drift) |

---

## 13. Open questions

| # | Question | Recommendation / lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-PE1** | Does persona change apply to **in-flight conversations** or only new ones? Atomic swap (new messages use new persona, but conversation history retains AI replies with old persona)? | Lean: atomic swap per next-message boundary. In-flight LLM completions finish with old. Customer sees no abrupt voice break since framings in earlier messages stay. | Eng + PM | 🟡 |
| **Q-PE2** | Should there be A/B testing of personas (50/50 split for 2 weeks)? | NO MVP — too risky for small samples + persona == brand. Defer to v1.2+ when we have data infrastructure. | PM | 🟢 |
| **Q-PE3** | Multi-language persona (RU + KZ/BY)? | Defer per P3 — RU only MVP. When other languages launched, per-language persona config needed. | PM | 🟢 |
| **Q-PE4** | Tenant can clone another tenant's persona (sharing best practices)? | NO MVP. Privacy + competitive risk. Per LQ3 cross-tenant learning principle. | Founder | 🟢 |
| **Q-PE5** | Owner can lock persona so even owner can't change without 2FA / cool-down? | NO MVP — defeats UI purpose. If owner makes bad change, 24h rollback grace is the recovery. | PM | 🟢 |
| **Q-PE6** | Preview-as-customer LLM calls — count against tenant inference cost or platform comp? | Platform-comp MVP (cost is small, helps tuning). Revisit at scale if becomes significant ($). | Founder | 🟡 |
| **Q-PE7** | Show persona effectiveness metrics in editor (e.g., «у вас CSAT 4.6★ — выше среднего»)? | NO MVP — could be vanity; bad if scores drop owner-induced anxiety. Surface in Analytics dashboard separately. | PM | 🟢 |
| **Q-PE8** | When owner sets «никогда» for explicit-human policy, who legally signs off on this risk? | Owner ticks acknowledgement checkbox at modal; legal language drafted by RU юрист (batch with Q-C3). | Legal | 🟡 |

---

## 14. Implementation roadmap

### Phase 1 — MVP (with onboarding Phase 5)
- Identity section (name + gender)
- Tone slider
- Custom greeting editor
- Live preview (5 intents)
- Save with quality check
- Read-only mode for non-owner roles

### Phase 2 — Post-launch (~ month 1)
- Forbidden phrases editor
- Policy sub-tab (explicit-human rules)
- History view + 24h rollback
- TONE_LEARNING entry from Learning Queue

### Phase 3 — Refinement (~ month 2-3)
- Preview-as-customer modal
- Advanced template snippets (custom_response_templates for specific intents — emerges from TONE_LEARNING patterns)
- Per-channel overrides if needed (currently NO per Q-PE3)

---

## 15. Components delta

Reuse existing where possible:
- `MaxUiButton`, `MaxUiInput`, `MaxUiSwitch`, `MaxUiSelect`, `MaxUiSlider`
- `Tabs` from main shell

New:
- `ChipListEditor` (forbidden phrases)
- `LivePreviewPane` (LLM-driven)
- `DiffViewer` (history rollback)
- `RollbackBanner` (countdown)
- `PolicyAcknowledgmentModal` (for «никогда» legal warning)

---

## 16. Cross-document linkage

- Voice policy: [`assistant-persona.md`](../policies/assistant-persona.md)
- Identity foundation: [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md)
- Permissions: [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4
- Learning queue entry: [`2026-05-17-conversations-handoff.md`](./2026-05-17-conversations-handoff.md) Screen C4 TONE_LEARNING
- Onboarding parent (Phase 5): [`2026-05-17-salon-onboarding-handoff.md`](./2026-05-17-salon-onboarding-handoff.md)
- Decisions log: [`decisions-log.md`](../decisions-log.md) — Q-PE1 to Q-PE8 added

---

## 17. What this UNBLOCKS

- Persona-quality enforcement at scale (UI for tuning per tenant)
- TONE_LEARNING compounding effect (LQ4 → applied changes → better assistant over time)
- Salon brand differentiation («это голос моего салона», не generic «AI bot»)
- Multi-tenant operations (each salon has distinct configured voice)
- Onboarding Phase 5 has real, reusable UI (not just sketches)

## 18. Sign-off

| Role | Approval | Date |
|---|---|---|
| Designer | ☐ | |
| Product | ☐ | |
| Engineering (FE) | ☐ | |
| Engineering (BE) | ☐ | |
| Legal (Q-PE8 risk acknowledgment text) | ☐ | |
| Founder (Q-PE6 cost approach) | ☐ | |
