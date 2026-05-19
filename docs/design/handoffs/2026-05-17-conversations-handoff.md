# Conversations & Handoff Dashboard — Developer Handoff Package

| Field | Value |
|---|---|
| **Date** | 2026-05-17 r1 |
| **Designer** | UX-architect skill / AndreyDeveloper84 |
| **Status** | Draft for review |
| **Surfaces** | Web dashboard (primary) + MAX manager-bot (secondary, notifications + 1-tap actions) |
| **Scope** | Conversations module: Inbox, Conversation detail, Customer sidebar, Settings → Conversation Policy |
| **Screens** | 6 web + 2 MAX-bot push templates |

## Foundational documents (read first)

This handoff is **operational** — it tells engineering how to build the UX. The product strategy and policies are upstream:

| Doc | Purpose |
|---|---|
| [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md) | Strategic foundation: customer sees single AI-assistant, not bot+admin toggle |
| [`memory/project_conversation_ownership_tiers.md`](~/.claude/projects/.../memory/project_conversation_ownership_tiers.md) | Operational: 3 tiers (AI_CONTINUITY / HUMAN_SUPERVISED / HUMAN_LOCKED) + SLA |
| [`docs/design/assistant-persona.md`](../policies/assistant-persona.md) | Voice / tone / vocabulary policy for all customer-facing messages |
| [`docs/design/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) | Full operational policy: reason→tier matrix, SLA, permissions, audit events, retention |

Everything below assumes those are read.

---

## 0. Overview

### What this module is
The admin's command center for monitoring AI-assistant conversations with customers and intervening when needed. **All customer-facing messages render as the salon's single AI-assistant identity** regardless of who composed them. Internal admin UI shows true authorship.

### Why this is the most complex screen in the system
- Real-time multi-conversation across multiple channels (MAX, Telegram, future)
- High-stakes: each handoff is a potential booking 2 000–10 000 ₽ or lost customer
- Identity continuity must be preserved while internal team operates flexibly
- Learning loop: every handoff is a chance to make AI smarter
- Mobile-first reality: owner answers between clients with 30 sec attention slots
- Privacy-sensitive: PII (phone, medical) must be role-gated and audited

### Success metrics (extended per user feedback)
| Metric | Target | Type |
|---|---|---|
| Time-to-first-admin-response on handoff | median < 5 min | North Star |
| Handoff resolution rate (admin replied / total) | ≥ 95% | Quality |
| Handoff → Booking conversion | ≥ 30% | Quality |
| Bot improvement rate (handoff led to FAQ/catalog edit) | ≥ 20% | Learning |
| Avg admin time per handoff | < 90 sec | Efficiency |
| % handoffs answered from mobile | ≥ 60% | UX validation |
| **% handoff без ответа > 15 минут** | < 20% | SLA |
| **Handoff lost rate** (customer ушёл после ожидания) | < 10% | Critical — revenue risk |
| **% unsafe auto-resume** (AI ответил после HUMAN_LOCKED неподобающе) | < 0.5% | Safety |
| **% handoffs feeding learning** (FAQ/catalog enriched) | ≥ 25% | Compounding |

---

## 1. Architecture: state machine + tier model

### Conversation state machine
```
NEW
  ↓ first customer message
BOT_ACTIVE
  ↓ (AI working autonomously)
  ↓
  ├──→ BOT_RESOLVED (task complete)
  │
  └──→ HANDOFF_PENDING (AI triggered handoff)
       ↓
       ↓ tier assigned per handoff_reason (see ownership-policy §2)
       ↓
       ├─→ AI_CONTINUITY    ← AI keeps replying; admin "watches"
       ├─→ HUMAN_SUPERVISED ← AI drafts, admin approves
       └─→ HUMAN_LOCKED     ← AI silent until unlock
                ↓
                ↓
       ┌────────┴────────────────┐
       ↓                          ↓
   admin replies               no admin reply over SLA
       ↓                          ↓
   ADMIN_ACTIVE             SLA escalates: WARNING (15min) → HIGH (30) → STALE (60) → ABANDONMENT_RISK (120) → AUTO_ABANDONED (24h)
       ↓
   RESOLVED ←→ may re-open if customer messages again within retention window
```

### SLA escalation (per ownership-policy §3)
| Time | State | UI signal | System action |
|---|---|---|---|
| 0–14:59 | `HANDOFF_PENDING` | normal | none |
| 15 min | `WARNING` | yellow border | soft push to assigned admin |
| 30 min | `HIGH_PRIORITY` | orange + ⚠ icon | push to ALL admins + CSM alert |
| 60 min | `STALE_PENDING` | red border + dot | escalate to CSM queue |
| 120 min | `ABANDONMENT_RISK` | red (no flashing per reduce-motion) | alert to founder |
| 24h | `AUTO_ABANDONED` | grayed + tag | assistant: «прости что долго…» recovery message |

---

## 2. Inbox priority ordering (per user feedback)

Priority order (top→bottom in default Inbox sort):

1. **medical_contraindication / sensitive_topic / red-flag safety** — top priority
2. **complaint_sentiment / negative sentiment** — emotional recovery window is short
3. **client_ready_to_book / price_question_high_intent / booking_edge_case** — hot lead, time-sensitive
4. **vip_flagged / returning_client (LTV > threshold)** — relational value
5. **explicit_human_request** — customer asked for human
6. **low_confidence / out_of_catalog / multiple_failures** — informational
7. **STALE_PENDING bucket** — separate red group at top (these jumped tier-priority because of timing)

User can override sort:
- «По времени ожидания» (oldest first)
- «По важности» (default, above)
- «По каналу» (MAX / Telegram grouped)

---

## 3. Screens

### Screen C1 — Inbox (Conversations list)

**Route:** `/conversations/inbox` (default landing); `/conversations/all`; `/conversations/archive`; `/conversations/settings`
**Tabs:** Inbox / Все / Архив / Настройки

#### Layout (desktop ≥1024px, master-detail)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Студия Карина   [Setup ✓]    [Anya, админ ▾]  [🔔 3]  [⚙]                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Dashboard │Каталог│ Диалоги ●3 │Помощник│Аналитика│Биллинг│Настройки             │
├──────────────────────────────────┬──────────────────────────────────────────────┤
│ Inbox (3) | Все | Архив | ⚙     │                                              │
│ ───────────────────────────────  │                                              │
│ 🔎 [ Поиск имени, телефона ]    │                                              │
│ Сорт: [По важности ▾]            │       Выберите диалог слева                  │
│ Вид: [Компактный ▾] (Карина)    │                                              │
│      [Расширенный ▾] (Аня)      │                                              │
│ ───────────────────────────────  │                                              │
│                                  │                                              │
│ ━━ ТРЕБУЮТ ОТВЕТА (3) ━━━━━━━━  │                                              │
│                                  │                                              │
│ 🔴 Мария И. • MAX • 8 мин       │                                              │
│   жалоба • LTV 18 400 ₽         │                                              │
│   «Я была вчера, и мне очень…»  │                                              │
│   — никто не работает —          │                                              │
│   ──────────────────────────    │                                              │
│ 🟠 Анна П. • MAX • 36 мин ⚠HIGH │                                              │
│   нет в каталоге • LTV 2 200 ₽  │                                              │
│   возможна запись ≈3 800 ₽      │                                              │
│   «А вы ботокс делаете?»        │                                              │
│   ──────────────────────────    │                                              │
│ 🟡 +7 ••• 89 • Telegram • 12мин │                                              │
│   просит человека • новый        │                                              │
│   «Можно с живым человеком…»    │                                              │
│                                  │                                              │
│ ━━ В РАБОТЕ (1) ━━━━━━━━━━━━━  │                                              │
│ 🔁 Olga K. • перенос • Anya     │                                              │
│   взято 5 мин назад              │                                              │
│                                  │                                              │
│ ━━ СЕГОДНЯ — РЕШЕНО (3) ━━━━━  │                                              │
│ ✓ Ksenia M. • 11:30 → 🎯 запись │                                              │
│ ✓ Daria S. • 10:15 → info       │                                              │
│ ✓ +7 ••• 12 • 09:45 → 🎯 запись │                                              │
└──────────────────────────────────┴──────────────────────────────────────────────┘
```

#### Card structure (per user feedback: financial signal, channel, SLA tier, assignee)

```
┌──────────────────────────────────────┐
│ [tier-icon] [name] [channel] [time] │  row 1: SLA color tier-icon (🔴🟠🟡⚪)
│ [reason chip] [LTV / potential ₽]    │  row 2: reason + financial signal
│ [«preview text…» max 2 lines]        │  row 3: last message
│ [assignee badge if taken]            │  row 4: only if in work
└──────────────────────────────────────┘
```

Tier-icon meaning:
- 🔴 STALE / HIGH_PRIORITY / urgent reason
- 🟠 WARNING (15+ min)
- 🟡 fresh handoff <15 min
- ⚪ resolved / in-work

Financial signal logic:
- If known returning customer: «LTV 18 400 ₽»
- If new but high-intent: «возможная запись ≈ 3 800 ₽» (estimated from catalog avg)
- If unknown: omit (no fake value)

#### Mode toggle (per user feedback: Karina vs Anya)
- **Compact mode** (default for owner role, mobile): 4 rows per card, 32px avatar
- **Expanded mode** (default for admin role on desktop): 6 rows per card, 40px avatar, more context

Toggle persisted per-user via `User.preferences.inbox_density`.

#### States (all 6)

| State | Behavior |
|---|---|
| Loading | Skeleton 4 cards shimmer + section headers stub |
| **Empty (zero handoffs — bot handling all)** ✨ | «Все диалоги помощник закрывает сам.» + actionable stats: «За сегодня: 12 диалогов, 4 записи, 0 ждут.» + button **«Посмотреть, чему помощник научился»** → routes to learning queue |
| Empty (zero conversations — day 1) | «Жду первых клиентов. [Поделиться ссылкой на помощника]» |
| Populated | Above |
| Error (load fails) | Section-scoped retry banner |
| Offline | Last cached + grey banner «Нет связи» + queue counter |

#### Realtime
- WebSocket connection on mount; 30s polling fallback if WS unavailable
- New handoff: row slides in from top + soft tone if enabled
- Updates in place; selected row updates immediately
- Tab badge updates: `Диалоги ●N` where N = unresolved handoffs

#### Components

`Tabs` (Inbox / Все / Архив / ⚙) · `SearchInput` (debounced 300ms) · `Select` (sort, view) · `ConversationCard` (compact/expanded variants) · `SectionHeader` · `RealtimeIndicator`

---

### Screen C2 — Conversation Detail

**Route:** `/conversations/{id}` (deep-linkable)
**Surface:** Web; on mobile collapses to full-screen view

#### Layout (desktop right pane, mobile full-screen)

```
┌──────────────────────────────────────────────────────────────────┐
│ ← Inbox    Мария И. • MAX • активна 8 мин   [tier: HUMAN_LOCKED] │
│            🔴 SLA: 8 мин из 15                                    │
│                                       [Свернуть]  [⋮ Действия]   │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ─── 14:15 ─── (timestamp section divider, day boundaries)       │
│                                                                  │
│  Мария И.                                                        │
│  Здравствуйте! Я была у вас вчера на маникюре,                  │
│  и мне не очень нравится результат. Что можно сделать?          │
│                                                                  │
│                                  Помощник студии • 14:15         │
│                Очень жаль это слышать! Уточните пожалуйста:      │
│                к какому мастеру вы записывались?                 │
│                                                                  │
│  Мария И. • 14:16                                                │
│  Анна, маникюр гель-лак                                          │
│                                                                  │
│                                  Помощник студии • 14:16         │
│                Понятно. Передаю руководителю салона —            │
│                она свяжется с вами в течение часа.                │
│                                                                  │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│  🔴 ПЕРЕДАНО ВАМ — Причина: жалоба (sentiment отрицательный)    │
│      Tier: HUMAN_LOCKED. Помощник не отвечает до резолва.        │
│      Confidence у помощника: средняя.                            │
│      [▾ Показать reasoning]                                       │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│                                                                  │
│ ┌─[ Ваш ответ ]──────────────────────────────────────────────┐ │
│ │  ⚙ Режим: обычный ответ (от имени помощника) [Сменить ▾]  │ │
│ │  ┌──────────────────────────────────────────────────────┐ │ │
│ │  │ Здравствуйте, Мария! Понимаем вашу досаду…           │ │ │
│ │  └──────────────────────────────────────────────────────┘ │ │
│ │  💡 Шаблоны: [Извинение + уточнение] [Пригласить         │ │
│ │              на бесплатное переделать] [Передать         │ │
│ │              мастеру для оценки]                          │ │
│ │  ⚠ Проверьте факты перед отправкой                       │ │
│ │  [📎 Файл]                          [Отправить] (Cmd↵)   │ │
│ └───────────────────────────────────────────────────────────┘ │
│                                                                  │
│ Быстрые действия:                                                │
│ [✓ Решено] [🔁 Помощник продолжит после ответа (LOCKED → SUPER) │
│ [📚 Добавить в FAQ] [↗ Эскалация в CSM]                          │
│ [⋮ Ещё]                                                          │
└──────────────────────────────────────────────────────────────────┘
```

#### Mobile-priority hierarchy (per user feedback)
Mobile layout shows in priority order:
1. **Short summary**: customer name + channel + waiting time
2. **Last customer message** (most recent only by default)
3. **Handoff reason** (compact chip)
4. **Reply box** (sticky bottom)
5. «Подробнее» → expands full transcript, sidebar, history

No overwhelming wall of info on mobile open. Progressive disclosure.

#### Message bubble rules

All bubbles render as **«Помощник студии»** (or tenant-configured name) **regardless of who composed**.

| Source | Render | Visible to admin (internal) | Visible to customer |
|---|---|---|---|
| AI composed | Assistant bubble | small icon: `AI` | nothing special |
| Admin replied — AI_CONTINUITY tier | Assistant bubble | `composed by Anya` chip | nothing special |
| Admin replied — HUMAN_SUPERVISED, admin approved AI draft | Assistant bubble | `AI drafted, Anya approved` | nothing special |
| Admin replied — HUMAN_LOCKED, fully custom | Assistant bubble | `composed by Anya` chip | nothing special (or explicit human identity if regulated topic — see persona §4) |
| System events (booking created, file opened) | Centered grey small text | `· · · Помощник создал запись #1234 · · ·` | same |

**Handoff divider** is admin-only — customer doesn't see this in their chat.

#### Identity policy in ReplyBox (per user feedback)

**Removed**: «Бот / Anya» toggle (this was the old model — discarded)

**New**: ReplyBox always sends as **«помощник студии»** by default. Admin sees indicator:

```
⚙ Режим: обычный ответ (от имени помощника)  [Сменить ▾]
```

`[Сменить ▾]` dropdown reveals 3 options:
- ✅ **Обычный ответ** (default) — sent as assistant; standard framing per persona doc
- ✅ **Ответ от команды** — sent as assistant but with framing prefix «Уточнил у мастера —…» / «Команда салона проверила —…» (LLM-applied)
- ⚠ **Explicit от меня** — sent with explicit name «Вам отвечает администратор Анна — …». **Only available for tenants with `explicit_human_policy_enabled`** (medical, premium spa) or for HUMAN_LOCKED + regulated reasons (refund, medical, legal)

Default for HUMAN_LOCKED + complaint: «Ответ от команды»
Default for HUMAN_LOCKED + medical/refund: «Explicit от меня»
Default for AI_CONTINUITY: «Обычный ответ»

The selection is **smart per-tier**, but admin can override.

#### Templates (per user feedback: scenario-based)

LLM-generated context-aware, **always scenario-based, never generic**:

| Scenario detected | Template offered |
|---|---|
| Complaint | «Признание + просьба уточнения» / «Предложение мастера для оценки» / «Передача руководителю» — **NO compensation offer until investigation** |
| Booking attempt | «Уточнение услуги/времени» / «Подтверждение слота» |
| Medical question | «Мягкий отказ + специалист» (only this option — never give medical advice) |
| Out-of-catalog request | «Уточнение деталей у мастера» / «Альтернатива из каталога» / «Добавить в каталог?» |
| Schedule conflict | «Альтернативные слоты» / «Перевод на следующую неделю» |

Templates run through persona-check before insertion into draft (forbidden phrases blocked).

#### Suggested reply (per user feedback: opt-in, off by default)

- **Default OFF**, opt-in setting in admin profile («Предлагать черновик ответа»)
- When on: shows below ReplyBox as collapsible section «Помощник предлагает →»
- Click expands draft text; click again inserts into input
- **Always requires human edit** before send — pre-send check «Этот ответ пришёл от помощника, проверьте факты»

#### Quick actions (per user feedback: dangerous off main)

Main bar (always visible):
- ✓ Решено
- 🔁 Помощник продолжит (visible only if tier is HUMAN_LOCKED → can be promoted to HUMAN_SUPERVISED)
- 📚 Добавить в FAQ
- ↗ Эскалация в CSM

`⋮ Ещё` overflow menu:
- ➕ Создать задачу (для команды)
- 📅 Создать запись (manual booking on customer's behalf)
- ⏰ Предложить слот (sends customer a slot suggestion via assistant voice)
- 🛒 Добавить услугу в каталог (if handoff was `out_of_catalog`)
- 🔒 Заблокировать клиента (with confirm)
- 📋 Скопировать ссылку на диалог

#### Customer sidebar (per user feedback: role-gated, audit-logged)

**Right panel** desktop, **drawer** mobile (swipe-left to open).

```
┌───────────────────────────────┐
│ Мария Иванова                 │
│ +7 ••• ••• 14 67 [показать]   │
│      ↑ click reveals + audits │
│ MAX: @maria_ivanova           │
│                               │
│ Теги: [Постоянная] [+]        │
│                               │
│ ── Прошлые визиты (Аня+) ──   │
│ 16 мая • маникюр • Анна       │
│ 02 мая • маникюр • Анна       │
│ ...                           │
│ LTV: 18 400 ₽ (Аня+) • 12 виз │
│                               │
│ ── Заметки (Аня+) ──          │
│ ⚕ «Аллергия на акрил»         │
│       (медзаметка — только    │
│        для роли medical)      │
│ «Любит чай зелёный»           │
│ — Anya, 02 мая                │
│ [+ Добавить заметку]          │
│                               │
│ ── Прошлые диалоги (3) ──     │
│ 12 мая • перенoc записи ✓    │
│ ...                           │
└───────────────────────────────┘
```

**Role-based visibility:**
- Phone, visits, LTV, notes → Admin+ (Аня)
- Medical notes (⚕ icon) → Admin with `medical_role` flag only
- Reveal phone click → confirm dialog + audit event
- View medical notes → audit event (no confirm needed but logged)
- Add note → audit event with content hash

---

### Screen C3 — Settings → Conversation Policy (admin Settings, Owner only)

**Route:** `/conversations/settings` or `/settings/conversations`
**Auth:** Owner role only

Configures per-tenant:
- Assistant identity: name, gender, avatar, custom greeting template
- Persona tone modifier slider (formal ↔ casual, default middle)
- Forbidden phrases additions
- Explicit-human policy: when does admin name get shown to customer? (default: only regulated topics)
- SLA thresholds: 15/30/60/120 default, custom allowed
- Push notification channels (which admins get which alerts)
- Roles management (custom roles + capability mapping — post-MVP)
- Audit log access + export

#### Layout (compact, settings-style)

```
┌──────────────────────────────────────────────────────┐
│ Настройки → Диалоги                                  │
├──────────────────────────────────────────────────────┤
│ ── Имя помощника ──                                  │
│ Как клиент видит вашего ассистента                  │
│ Имя: [ Помощница студии Карина             ]        │
│ Род: ◯ Мужской ⦿ Женский ◯ Нейтральный              │
│                                                      │
│ ── Голос / тон ──                                    │
│ [Сдержанный ──●───── Тёплый ─── Игривый]             │
│ Запретные фразы (доп.): [ ввод...           ]       │
│                                                      │
│ ── Когда показывать имя сотрудника ──                │
│ ⦿ Только в чувствительных темах (медицина, возврат) │
│ ◯ Всегда показывать имя ответившего                  │
│ ◯ Никогда (с предупреждением legal)                  │
│                                                      │
│ ── Время ожидания ответа (SLA) ──                    │
│ Предупреждение:    [15] минут                        │
│ Высокий приоритет: [30] минут                        │
│ Просрочка:         [60] минут                        │
│ Риск потери:      [120] минут                        │
│                                                      │
│ ── Уведомления ──                                    │
│ MAX-бот менеджера: ☑ Anya  ☑ Карина  ☐ Стажёр       │
│ E-mail digest:     ☑ ежедневно 09:00                 │
│                                                      │
│ ── Аудит ──                                          │
│ [Открыть журнал]  [Экспорт за период…]              │
└──────────────────────────────────────────────────────┘
```

---

### Screen C4 — Learning Queue (compounding intelligence)

**Route:** `/conversations/learning`
**Sub-routes:** `/conversations/learning/archive`, `/conversations/learning/insights`, `/conversations/learning/{id}`
**Auth:** Admin+ (Owner can manage; Admin reviews; Receptionist sees but cannot accept FAQ; Master cannot access)

**Purpose:** After every resolved conversation (especially handoffs), AI proposes structured learnings — new services, FAQ entries, tone refinements, contraindication notes, pattern insights. Admin reviews queue and approves only genuinely useful patterns. **This is the actual compounding moat of the product** — without admin oversight quality dies; without queue learning stagnates.

#### JTBD
> «Когда мой помощник обработал много диалогов, я хочу увидеть что он понял и одобрить только полезные паттерны, чтобы помощник стал умнее не впитав мои ошибки.»

#### Success metrics specific to this screen
| Metric | Target | Type |
|---|---|---|
| % suggestions reviewed within 48h | ≥ 70% | Hygiene |
| Acceptance rate | 40–70% (sanity range) | Quality — outside range = bad signal |
| Knowledge growth per active salon per month | ≥ 8 items accepted | North Star |
| AI confidence improvement (% similar handoffs avoided post-accept) | ≥ 40% | Compounding |
| Persona violation in accepted (post-audit) | < 2% | Safety |
| Medical suggestion accept rate | ≤ 30% — should be LOW | Safety signal |

#### 8 suggestion types (the taxonomy)

| Type | Trigger | Confidence drivers | Risk |
|---|---|---|---|
| `NEW_SERVICE` | 1+ `out_of_catalog` | unique-customer count | Low |
| `NEW_FAQ` | `low_confidence` / `multiple_failures` resolved | source count + pattern | Medium |
| `FAQ_UPDATE` | existing FAQ used, follow-up shows gap | pattern detection | Medium |
| `CONTRAINDICATION_NOTE` | 2+ `medical_contraindication` same condition | always requires medical confirm | **High — never auto-accept** |
| `PATTERN_INSIGHT` | 5+ similar topic clusters | informational only | Low |
| `PRICING_REVIEW` | 3+ price pushback | service + repeated objections | Medium |
| `SCHEDULE_INSIGHT` | repeat fails same slot | pattern across master/day | Low |
| `TONE_LEARNING` | admin frequently rewrites AI drafts | edit-distance analysis | Medium — affects persona |

#### State machine per suggestion
```
PROPOSED → IN_REVIEW (admin opened) →
                                    ├─ ACCEPTED (with edits)
                                    ├─ REJECTED (with structured reason)
                                    ├─ SNOOZED (resurfaces in 7d)
                                    └─ AUTO_ARCHIVED (30d no action)
```

#### Layout — desktop (≥1024px)

Top: subtab in Conversations module: `[ Inbox ] [ Все ] [ Архив ] [ 📚 Учёба (7) ] [ ⚙ ]`

Two-column main area:
- **Left (~70%)**: stacked LearningCards, filterable + sortable
- **Right sidebar (~30%)**: LearningInsightsPanel + occasional GoodMomentCard

```
┌─────────────────────────────────────┬───────────────────────────────────────────┐
│ Что помощник предлагает (7)         │ За эту неделю                             │
│ Фильтр: [Все типы ▾]                │                                           │
│ Сорт: [По уверенности ▾]            │ Помощник вырос за неделю                  │
│                                     │ 📚 Добавлено в знания: 12                 │
│ ╔════════════════════════════════╗  │ ✓ Принято: 9 из 13                        │
│ ║ ★★★★★ NEW_SERVICE              ║  │ ✕ Отклонено: 4                            │
│ ║ ❓ «Ботокс / нейромодуляторы»  ║  │                                           │
│ ║                                ║  │ Покрытие вопросов: 87% (+9%)              │
│ ║ Спрашивают 4 раза за неделю   ║  │                                           │
│ ║ Категория: Косметология        ║  │ Топ темы:                                 │
│ ║ Длительность: ~30 мин          ║  │ • ботокс (4)                              │
│ ║ Цена: 8 500 ₽ (медиана по 12   ║  │ • ламинирование (3)                       │
│ ║   салонам Москвы)              ║  │ • аллергия (2)                            │
│ ║                                ║  │                                           │
│ ║ Источник: 4 диалога [Посмотр.]║  │ [Открыть аналитику]                       │
│ ║ [✓ Добавить в каталог] [✎][✕] ║  │                                           │
│ ╚════════════════════════════════╝  │                                           │
│                                     │ (Conditional GoodMomentCard at milestones)│
│ ╔════════════════════════════════╗  │                                           │
│ ║ ★★★★☆ NEW_FAQ                  ║  │                                           │
│ ║ ❓ «На каких ногтях нельзя    ║  │                                           │
│ ║    делать гель-лак?»          ║  │                                           │
│ ║ ⚠ Содержит медицинскую часть   ║  │                                           │
│ ║                                ║  │                                           │
│ ║ Предлагаемый ответ: [draft]   ║  │                                           │
│ ║ Источник: 3 диалога           ║  │                                           │
│ ║ [✓ Принять] [✎ Изменить] [✕]  ║  │                                           │
│ ╚════════════════════════════════╝  │                                           │
│                                     │                                           │
│ ... more cards ...                  │                                           │
└─────────────────────────────────────┴───────────────────────────────────────────┘
```

#### Layout — mobile (<768px)
- Single column; no insights panel inline (moved to bottom-nav `📊 Прогресс`)
- Tap card → expands inline (no modal)
- Quick actions sticky at bottom of expanded card
- Swipe right → quick accept (with confirm haptic)
- Swipe left → quick reject

#### LearningCard anatomy
```
╔════════════════════════════════════╗
║  [★ confidence]    [Type badge]    ║  row 1
║  [Icon + Title gist]               ║  row 2
║  [Meta info specific to type]      ║  row 3-N
║  [Content preview / draft]         ║  row M
║  [⚠ Safety warning if applicable]  ║  row M+1
║  [Source: N conversations] [link]  ║  row M+2
║  [Primary action] [Edit] [Reject]  ║  row M+3
╚════════════════════════════════════╝
```

##### Confidence indicator (5 stars)
- ★★★★★ — 4+ sources, recent, strong pattern (safe one-click accept)
- ★★★★☆ — 3 sources OR 2 with strong pattern
- ★★★☆☆ — 2 sources OR 1 with clear context
- ★★☆☆☆ — 1 source, experimental
- ★☆☆☆☆ — informational only (PATTERN_INSIGHT default)

Tooltip: «4 диалога подтверждают эту тему. Высокая уверенность.»

##### Type badges (color-coded, Lucide icon, NO emoji in prod)
- `NEW_SERVICE` — rust accent + `sparkles` icon
- `NEW_FAQ` — teal + `help-circle` icon
- `FAQ_UPDATE` — teal-muted + `pencil` icon
- `CONTRAINDICATION_NOTE` — error red + `alert-triangle` + ⚠ banner
- `PATTERN_INSIGHT` — info blue + `lightbulb`
- `PRICING_REVIEW` — warning amber + `trending-down`
- `SCHEDULE_INSIGHT` — info blue + `calendar-clock`
- `TONE_LEARNING` — accent-2 + `message-square`

#### Per-type interaction flow

**Type 1 — NEW_SERVICE**
1. `[✓ Добавить в каталог]` → modal opens with pre-filled form (name, category, duration median, regional price)
2. Admin reviews/edits/saves
3. Service added with `source: "learned"` flag visible in catalog editor
4. AI awareness immediate; next customer asking gets answer including new service

**Type 2 — NEW_FAQ**
1. `[✓ Принять]` or `[✎ Изменить]` → edit modal with draft Q+A side-by-side
2. **Required**: at least one keystroke OR explicit «Принять без правок» click (anti-rubber-stamp)
3. Pre-save persona check (assistant-persona.md §9): tone, forbidden phrases, length
4. Safety-flagged (medical/pricing): mandatory checkbox «Я проверил факты»
5. Save → FAQ added to KB, AI re-indexes within 30s

**Type 3 — FAQ_UPDATE**
- Shows existing FAQ + proposed extension in diff view («было» / «станет»)
- Admin approves merge or rewrites
- Uses semantic `<del>` / `<ins>` (not just CSS color) for a11y

**Type 4 — CONTRAINDICATION_NOTE** ⚠ **Highest safety bar**
1. Card pre-warns: «Медицинская тема — требует особой осторожности»
2. `[Принять]` button **DISABLED** until checkbox «Я проконсультировался с мастером» checked
3. Saved entry tagged `medical=True` → triggers HUMAN_LOCKED tier for related future conversations
4. Audit event: `learning.medical_accepted` with admin_id, content_hash
5. First 10 medical accepts per tenant: notification to founder/quality reviewer for sampling

**Type 5 — PATTERN_INSIGHT** (informational, no auto-action)
- Buttons: `[Добавить услугу]` (→ NEW_SERVICE flow) / `[Добавить FAQ]` (→ NEW_FAQ flow) / `[Не сейчас]` (snooze 14d)
- No reject reason needed

**Type 6 — PRICING_REVIEW**
- Warning: «Это сигнал, не предложение. Решение о цене — за вами.»
- Shows current price + customer-pushback count
- Buttons: `[Открыть услугу в каталоге]` (no auto-edit) / `[Игнорировать]`

**Type 7 — SCHEDULE_INSIGHT**
- Action: `[Открыть мастера]` (routes to staff schedule) / `[Не нужно]`

**Type 8 — TONE_LEARNING**
- Side-by-side «помощник пишет» / «вы переписываете»
- Click `[✓ Применить]` → opens Persona settings modal with proposed change highlighted
- Confirm or cancel

#### Quality gates (anti-poison)

**Pre-acceptance checks** (every `[Принять]`):
- Persona check via `assistant-persona.md` quality rules
- Forbidden phrase blocker
- Length check
- Medical/pricing red flag → required explicit confirm
- Anti-rubber-stamp for TONE_LEARNING / FAQ_UPDATE — require ≥3 sec view before button enables

**Post-acceptance audit**:
- `learning.accepted` audit event with content_hash + admin
- 7-day delayed check: if FAQ caused new handoff → flagged for re-review
- Monthly: 10% sample audit by founder/quality reviewer

**Rejection with structured reason**:
- ⦿ Не актуально для нашего салона
- ◯ Информация неверная
- ◯ Плохой стиль
- ◯ Слишком редкий случай
- ◯ Дублирует существующее
- ◯ Другое: [text]

Feeds back into AI to suppress similar suggestions + improve confidence scoring globally.

**Anti-staleness**:
- 30d no action → auto-archive (queryable in /archive)
- Reject → no re-propose 90d unless pattern strengthens 3×
- Resurrection on stronger pattern with updated confidence

#### Learning Insights panel (right sidebar)

**Specific design rules** (anti-vanity):
- Show **growth**, not workload («помощник вырос», not «у вас 7 задач»)
- Show **impact**, not effort («покрытие 87%», not «12 принято»)
- Show **trend**, not absolute («+9% за неделю»)
- Acknowledge admin work
- Max one «Хороший момент» card per week, only at milestones (80/85/90% coverage; +10/20/30% growth)
- Never shown on negative trend

#### States (all 6)
| State | Behavior |
|---|---|
| Loading | Skeleton 3 cards + side panel placeholder |
| **Empty (caught up)** ✨ | «Помощник всё знает! Возвращайтесь позже.» + last 5 accepted + «За неделю помощник закрыл 86% диалогов сам — спасибо!» |
| Empty (never had any — day 1) | «Здесь будут предложения от помощника по мере его работы. После первых 5 диалогов вы увидите первые наблюдения.» |
| Populated | As above |
| Filtered to zero | «Нет предложений в этой категории. [Сбросить фильтр]» |
| Error | Section-scoped retry |
| Offline | Cached + banner; actions queued |

#### Components delta (added in this screen)
- `LearningCard` (8 variants per type)
- `ConfidenceStars` (5-star with tooltip)
- `TypeBadge` (color-coded + Lucide icon)
- `SafetyWarningInline` (⚠ for medical/pricing)
- `ContentDraftPreview` (mono-aligned for service spec)
- `SourceConversationsLink` («4 диалога» → drawer/modal with linked conversations)
- `AcceptAndEditModal` (per-type form + persona check)
- `MedicalConfirmGate` (disabled-by-default button)
- `RejectReasonModal` (structured reasons + free text)
- `LearningInsightsPanel` (right sidebar / mobile sheet)
- `GoodMomentCard` (conditional milestone celebration)
- `LearningArchiveTable` (`/archive` sub-route)

#### Cross-screen integration (6 entry points)
| Source | Integration |
|---|---|
| Conversations detail (C2) | Quick action «📚 Добавить в FAQ» → learning queue with auto-accept option |
| Catalog (Onboarding §11) | Header badge «4 предложения от помощника» → filtered queue (`type=NEW_SERVICE`) |
| Persona settings | Badge «2 предложения по голосу» → filtered queue (`type=TONE_LEARNING`) |
| Dashboard (Onboarding §10) | Daily widget «Помощник предлагает: 3 новых» |
| MAX manager-bot | Weekly digest «5 предложений ждут» with `[Открыть учёбу]` |
| Onboarding Phase 5 (Train RAG) | Sets expectation: «дальше помощник сам найдёт пробелы — увидите в Учёбе» |

#### A11y
- `LearningCard` is `<article>` with proper heading hierarchy
- ConfidenceStars: `aria-label="Уверенность: 4 из 5 звёзд. 3 диалога подтверждают."`
- Type badges: text + icon + color (never color alone)
- Diff view (FAQ_UPDATE): semantic `<del>` / `<ins>`
- Medical confirm checkbox: clear label + `aria-describedby`
- Reject reason modal: `<fieldset><legend>` for radio group
- Insights panel: `aria-label="Прогресс помощника за неделю"`
- Auto-archive notifications: `aria-live="polite"`
- **Keyboard shortcuts** (power users):
  - `J` / `K` — next / prev card
  - `A` — accept (with confirm if safety-flagged)
  - `R` — reject (opens modal)
  - `S` — snooze
  - `E` — edit
  - `Esc` — close modal

#### Edge cases
- Same suggestion proposed twice → dedup via content_hash; second occurrence increments source count
- Tenant accepts, later rejects related FAQ in KB → feedback loop lowers confidence for future similar
- AI confidence wrong (one-off mis-classified as pattern) → tenant rejection feedback corrects via pattern_score anti-bias
- Medical accidentally accepted without review → server-side audit + first-10-medical notification to founder
- Persona violation in admin-edited FAQ → pre-save warns; admin can override (audited)
- High-volume tenant (50+ pending) → pagination + filter + «принять без правок» bulk **only** for ★★★★★ + non-safety-flagged
- FAQ contradicts existing → diff view with conflict warning + merge option
- Tenant disables learning queue (Setting) → strong warning «Помощник перестанет улучшаться»
- Admin role changed mid-review → pending suggestions stay; if downgraded role can't accept some types, locked with explanation
- Source conversation deleted (customer GDPR) → suggestion stays valid (content preserved); source link grays out

#### Backend contracts (delta from §5 main)
```
GET    /api/v1/conversations/learning
       ?type=NEW_SERVICE|NEW_FAQ|...|all&sort=confidence|date|impact&filter[status]=pending|reviewed

LearningSuggestion = {
  id, type, confidence_stars: 1..5, confidence_reasoning,
  title, content: {type-specific JSON},
  source_conversation_ids: [uuid], source_count: int, pattern_score: float,
  created_at, expires_at (30d default),
  safety_flags: ["medical", "pricing", "tone"],
  related_existing?: { faq_id?, service_id? }
}

GET    /api/v1/conversations/learning/{id}                  — full details + source previews
POST   /api/v1/conversations/learning/{id}/accept           — { edited_content?, persona_check_passed, safety_confirmations }
POST   /api/v1/conversations/learning/{id}/reject           — { reason: enum, note? }
POST   /api/v1/conversations/learning/{id}/snooze           — { resurface_in_days }
POST   /api/v1/conversations/learning/{id}/edit-draft       — save draft without accepting
GET    /api/v1/conversations/learning/insights?period=week|month
GET    /api/v1/conversations/learning/archive               — past accepted + rejected
POST   /api/v1/conversations/learning/feedback              — 7d delayed re-check feedback
```

AI side:
- Background worker `apps/persona/learning_proposer.py`
- Triggered on every conversation `resolved` event + nightly batch for pattern detection
- Confidence model: source count + pattern similarity (embedding cluster) + recency + prior tenant acceptance rate
- Deduplicated via content_hash

#### Open questions specific to this screen
| # | Question | Owner | Urgency |
|---|---|---|---|
| LQ1 | Bulk-accept for ★★★★★? Default NO; revisit after observing queue sizes | PM | 🟢 v1.1 |
| LQ2 | Tenant can disable queue entirely? Default yes with strong warning | PM | 🟡 |
| LQ3 | Cross-tenant learning aggregation? Default NO for MVP; opt-in category-level later | Founder | 🟡 |
| LQ4 | What if salon inactive 60+ days? Pause proposer, preserve queue, resurface on login | PM | 🟢 |
| LQ5 | Founder/quality reviewer for first 50 — same role as persona reviewer | Founder | 🟡 |
| LQ6 | Notification cadence — daily MAX digest only, no per-suggestion push | PM | 🟢 |
| LQ7 | Edit history per FAQ post-accept — show «изменено помощником через учёбу X дней назад»? Yes | Design + Eng | 🟢 |

---

### MAX manager-bot — Screen MX1 — Daily Digest

(Updated from earlier handoff — terminology: «помощник» not «бот»)

```
Доброе утро, Карина!

📊 За вчера (16 мая):
• Диалогов: 14
• Записей создано: 4 ★
• Передано вам: 2

🎯 Сегодня запланировано: 7 записей

Требует внимания:
  • 2 диалога ждут ответа
    [Открыть в дашборде]

[Полная аналитика]  [Что нового помощник узнал]
```

### MAX manager-bot — Screen MX2 — Handoff alert

```
🔴 Срочно: Мария И. ждёт 30 минут

Причина: жалоба (sentiment отрицательный)
Канал: MAX
LTV клиента: 18 400 ₽
Последнее сообщение:
«Я была вчера, и мне очень…»

[Открыть диалог]  [Взять в работу]
[Отложить 15 минут]
```

MAX-bot **не отправляет** complex replies — открывает web.

---

## 4. Components inventory (delta from main handoff doc)

| Component | Variants | Purpose |
|---|---|---|
| `ConversationCard` | compact, expanded | Inbox row; financial signal + channel chip + SLA color |
| `SlaTierBadge` | normal / warning / high / stale / risk | Visual SLA tier indicator |
| `HandoffDivider` | per reason × per tier | Visible only in admin transcript view |
| `MessageBubble` | assistant / customer / system | All composed messages render as assistant identity |
| `AuthorshipChip` | ai / admin_id / ai_approved | Admin-side only — shows true composer |
| `ReplyBoxAdvanced` | normal / supervised-draft / locked-disabled | Tier-aware behavior |
| `IdentityModeSelector` | normal / team / explicit | Dropdown above input |
| `TierBadge` | continuity / supervised / locked | Header of detail view |
| `LearningCard` | faq / catalog-add | Queue items |
| `CustomerSidebar` | full / drawer | Role-gated PII visibility |
| `PiiRevealConfirm` | — | Confirm + audit on phone reveal |
| `AuditLogViewer` | — | Settings Owner-only |
| `RealtimeIndicator` | — | WS connection state + new-message pulse |

---

## 5. Backend contracts (extends main handoff doc §8)

### REST endpoints (Conversations module)
```
GET    /api/v1/conversations
GET    /api/v1/conversations/{id}
POST   /api/v1/conversations/{id}/messages
POST   /api/v1/conversations/{id}/resolve
POST   /api/v1/conversations/{id}/assign
POST   /api/v1/conversations/{id}/take-over     ← new (per feedback)
POST   /api/v1/conversations/{id}/lock          ← promote to HUMAN_LOCKED
POST   /api/v1/conversations/{id}/unlock        ← demote
POST   /api/v1/conversations/{id}/resume-bot    ← new (per feedback)
POST   /api/v1/conversations/{id}/snooze        ← new (per feedback)
POST   /api/v1/conversations/{id}/escalate
POST   /api/v1/conversations/{id}/block-customer (with confirm token)
POST   /api/v1/conversations/{id}/templates     ← LLM-generates per context
POST   /api/v1/conversations/{id}/suggested-reply ← if opt-in
POST   /api/v1/conversations/{id}/reveal-phone  ← writes audit before returning
GET    /api/v1/conversations/{id}/audit         ← who did what
GET    /api/v1/conversations/metrics            ← new (per feedback): aggregate stats
GET    /api/v1/conversations/learning           ← FAQ candidates queue
POST   /api/v1/conversations/learning/{id}/accept
POST   /api/v1/conversations/learning/{id}/reject
POST   /api/v1/conversations/learning/{id}/edit
GET    /api/v1/conversation-settings             ← tenant policy
PATCH  /api/v1/conversation-settings             ← Owner only
```

### WebSocket events (extends main handoff)
```
{type:"new_message", conversation_id, message, by:"customer|ai|admin"}
{type:"handoff_triggered", conversation_id, reason, urgency, tier}
{type:"tier_changed", conversation_id, from_tier, to_tier, actor}
{type:"sla_warning", conversation_id, level:"warning|high|stale|risk"}  ← new
{type:"send_failed", conversation_id, message_id, reason}             ← new
{type:"admin_typing", conversation_id, admin_id}                       ← v1.1 (not MVP)
{type:"customer_typing", conversation_id}                              ← v1.1 (not MVP)
{type:"presence", admin_id, active_in_conversation_id}
{type:"lock_taken", conversation_id, by_admin_id, lock_replaced?}
```

### Audit events (mandatory — see ownership-policy §5)
Every action writes audit event with `event_type`, `tenant_id`, `conversation_id`, `actor_id`, `actor_role`, payload. Implementation: middleware on REST endpoints; explicit calls from background workers.

### Offline message queue check (per user feedback)
Before flushing queued offline message:
1. Re-fetch conversation latest messages
2. If customer sent new message after queued draft was composed → warning modal: «Клиент написал ещё раз. Перечитать?»
3. Admin confirms send or cancels

---

## 6. Accessibility addendum

In addition to onboarding handoff §6:
- Real-time updates announced via `aria-live="polite"` (not assertive — would overwhelm SR)
- SLA color tier always paired with text label («15 мин» / «срочно») — never color-only
- HandoffDivider has full text content readable by SR including reason and tier
- IdentityModeSelector has `aria-label` describing current selection
- PII reveal: phone shown character-by-character announceable to SR, not just «hidden»
- Audit log table fully accessible
- Reduced-motion respects: no flashing borders, no parallax, just static colors

---

## 7. Edge cases registry (this module)

- Customer messages while in HUMAN_LOCKED tier → assistant silent; admin notified; if 5+ min: assistant sends acknowledgement framing
- Two admins try to take same conversation → lock-based, audited; second can «перехватить»
- Admin replies as «assistant» but accidentally signs name → pre-send check warns
- Customer asks for specific admin by name → routing logic + assistant framing
- AI mis-classifies tier → admin overrides (audited, used as ML training signal)
- Customer deletes account → conversation hidden from non-Owner roles; audit retained
- Offline message ready to send, but customer sent new message → warn admin before send
- Forbidden phrase detected in admin draft → pre-send check blocks
- Customer floods (10+ messages in 1 min) → spam detection; assistant: «дайте мне момент…»
- Identity disclosure: customer asks «вы бот?» → always truthful per persona §4
- Multi-conversation tab: admin opens 2 conversations in 2 tabs → realtime works for both; lock applies per-conversation
- Lock-take audit: admin A holds lock 30 min, admin B takes over → both notified, A's pending draft preserved

---

## 8. Cross-document linkage

- Foundation: [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md)
- Foundation: [`memory/project_conversation_ownership_tiers.md`](~/.claude/projects/.../memory/project_conversation_ownership_tiers.md)
- Voice spec: [`docs/design/assistant-persona.md`](../policies/assistant-persona.md)
- Operational policy: [`docs/design/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md)
- Parent onboarding: [`docs/design/2026-05-17-salon-onboarding-handoff.md`](./2026-05-17-salon-onboarding-handoff.md) r3+ (terminology aligned)
- Skill — interaction patterns: `~/.claude/skills/ux-architect/references/interaction.md`

---

## 9. Sign-off

| Role | Approval | Date |
|---|---|---|
| Designer | ☐ | |
| Product (PM) | ☐ | |
| Engineering (FE) | ☐ | |
| Engineering (BE) | ☐ | |
| Engineering (AI/ML — for tier classification) | ☐ | |
| QA lead | ☐ | |
| Legal (audit retention + identity disclosure) | ☐ | |
| Security (PII access + audit) | ☐ | |
| CSM lead (escalation flow) | ☐ | |
| Founder (assistant persona approval) | ☐ | |

---

## 10. Open questions (final state after user lock)

> **📌 Authoritative status:** see [`decisions-log.md`](../decisions-log.md) for current status of Q-C1–Q-C10, Q-CO1–Q-CO5, and LQ1–LQ7. Below is the snapshot at handoff publication.

| # | Question | Locked answer |
|---|---|---|
| Q-C1 | Identity policy | ✅ Single assistant; explicit human only for regulated topics |
| Q-C2 | Auto-resume | ✅ 3-tier model (AI_CONTINUITY / HUMAN_SUPERVISED / HUMAN_LOCKED) |
| Q-C3 | Retention | ⚠ Initial: 180d transcripts, 365+d audit, medical separate; awaiting legal sign-off |
| Q-C4 | Concurrent admins | ✅ Lock-based MVP |
| Q-C5 | Suggested reply | ✅ Opt-in, off by default |
| Q-C6 | Voice messages | ✅ Not MVP |
| Q-C7 | Templates | ✅ Hybrid (platform + tenant + LLM context-aware), scenario-based for complaints |
| Q-C8 | CSM escalation | ✅ Read-only by default |
| Q-C9 | Mobile reply UX | ✅ Full-screen detail; no swipe-between MVP |
| Q-C10 | Learning loop | ✅ Auto-suggest, admin approves (no auto-add) |

### Newly raised (need lock)

| # | Question | Owner | Urgency |
|---|---|---|---|
| Q-CO1 | Tier classification confidence threshold — AI assigns tier; at what confidence do we fall back to «default tier per reason» vs «admin must choose»? | AI/ML + PM | 🟡 |
| Q-CO2 | Per-tenant custom roles — MVP fixed 4 (Owner/Admin/Receptionist/Master), custom in v1.1 | PM | 🟢 |
| Q-CO3 | Persona quality reviewer — who audits 10% sample of learning queue weekly? Founder for first 50 customers, then CSM lead? | Founder | 🟡 |
| Q-CO4 | Identity disclosure during long-delay (>30 min) — does assistant proactively message customer «извини, чуть дольше отвечаем»? | PM | 🟡 |
| Q-CO5 | Multi-tenant customer profile — если клиент пишет в 2 разных салона из MAX, разные профили или один? | Founder | 🟢 v1.1 |
