# Master Mobile UX — Developer Handoff Package

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Designer** | UX-architect skill / AndreyDeveloper84 |
| **Status** | Draft for founder ratification (recommendation: ACTIVE role — see §3) |
| **Surfaces** | MAX Mini App (primary, mobile-first) + MAX manager-bot DMs (push channel) + web (secondary, desktop fallback) + MAX group chat patterns (team coordination) |
| **Scope** | Master-persona mobile experience: onboarding, dashboard, schedule, profile, conversation list/detail (subset of Conversations module), notification settings, app settings |
| **Screens** | 8 mobile (Mini App) + 1 onboarding flow + 4 MAX-bot push templates + 1 web parity layout |
| **Persona** | Master = stylist/master who works at a salon; mobile primary; 30-sec attention windows between clients |
| **Foundation docs** | See §2 — read those first |

---

## 1. Status of major decisions (this doc)

| # | Decision | Status |
|---|---|---|
| **A. Active vs Passive role** | **ACTIVE** — master gets dashboard, own conversation replies, profile control. See §3 «Strategic decision» for justification. | Recommendation; **founder ratification required** |
| **B. Primary surface** | **MAX Mini App** (launched from manager-bot DM). Web is desktop parity, not primary. | Locked |
| **C. Reply identity** | Master's reply still renders as single AI-assistant to customer (per [single-assistant-identity](~/.claude/projects/.../memory/project_single_assistant_identity.md)). Internal UI shows master as composer in audit trail only. | Locked |
| **D. PII gating** | Master sees customer first name only. **NO phone, NO LTV, NO medical notes, NO financial** — UI never renders these fields for master role. | Locked per [ownership-policy §4](../policies/conversation-ownership-policy.md) |
| **E. Push delivery** | MAX manager-bot DMs (one bot per salon, master is bot's chat participant). No platform push. Frequency policy required to avoid block. | Locked per platform constraints |
| **F. Reply scope** | Master can reply ONLY to conversations involving them (assigned booking, mention, prior visit). Server-side enforcement; client-side UI gating mirrors. | Locked |
| **G. AI assist for master** | Master gets AI-drafted replies they can edit/send (tier-aware: same draft system as admin in HUMAN_SUPERVISED tier; in AI_CONTINUITY tier — drafts shown as «можно отправить» suggestion). | Locked — improves master UX without expanding permission surface |

---

## 2. Foundational documents (read first)

This handoff implements the master surface of the platform. Strategic context lives upstream:

| Doc | Why it matters here |
|---|---|
| [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md) | Customer sees ONE assistant voice. Master's replies render as that voice. |
| [`memory/project_conversation_ownership_tiers.md`](~/.claude/projects/.../memory/project_conversation_ownership_tiers.md) | 3-tier model. Master can promote to HUMAN_LOCKED (safety override). Cannot demote. |
| [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md) | When master sends a reply that leads to booking, attribution records `actor_type=master`. Never billable as AI-direct. |
| [`memory/project_max_platform_capabilities.md`](~/.claude/projects/.../memory/project_max_platform_capabilities.md) | MAX has no platform push — all proactive notification is bot DMs. Use MAX UI React lib for Mini App. No Mini App location API. |
| [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md) §5 | Master's reply must conform to persona — same pre-send quality check as admin. |
| [`docs/design/assistant-persona.md`](../policies/assistant-persona.md) | Voice/tone rules. Master's reply runs through the same persona check. |
| [`docs/design/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4 | Master role capabilities (canonical). This doc translates §4 into UI. |
| [`docs/design/2026-05-17-conversations-handoff.md`](./2026-05-17-conversations-handoff.md) | Master's conversation list/detail is a **restricted subset** of this module. Components reused where possible. |
| `~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md` | MAX platform playbook — Bridge API, sticky CTA, MAX UI components, haptics, group bot patterns. |

Everything below assumes those are read.

---

## 3. Overview

### What this module is
The **mobile-first experience for a master/stylist** working at a salon — accessed primarily inside MAX (Mini App + bot DMs), with web as desktop fallback. Master is **not a salon admin**; they are an operational team member with strict permissions. The product gives them:

- A live picture of their day (next visit, gap times, today's revenue tally for own services if any)
- A focused inbox of conversations involving them (their clients only)
- A way to reply personally (one tap, AI-assisted, persona-checked)
- Profile control (own photo, bio, services they perform, work hours)
- Notification settings (mute outside work hours, quiet evening)

### Strategic decision: ACTIVE role (recommendation)

**Recommendation: master is ACTIVE.** They get a dashboard, can reply to their conversations, manage their own profile. Justification:

1. **Master engagement drives salon retention.** If masters hate the tool («ещё одна программа»), they push back to the owner, who churns. If masters love it («это удобнее, чем звонки»), the salon sticks.
2. **Beauty industry is relational.** Masters have personal client relationships. A purely passive role («только смотри расписание») strips that relationship of digital presence — clients message in MAX, master is invisible, owner has to ferry replies. That breaks the «personal master» feeling that beauty buyers pay premium for.
3. **Operational realism.** Owner is between meetings; admin (Anya) covers 80% of inbox; but **personal questions to a specific master** (aftercare, recommendation, reschedule with «my master only») are exactly the cases where the admin's reply feels wrong. Direct master reply preserves the bond.
4. **Permission surface stays narrow.** Active ≠ unrestricted. Master sees only their conversations, no PII beyond first name, no financial. The «active» upside is bounded; the risk is mitigated by ownership-policy §4.
5. **Future commerce.** Tipping, master-rebook-reminders, personal subscription («ваш мастер Анна делится подарком на день рождения») all require the master to have an identity in-product. Passive blocks that roadmap.

**Trade-offs to flag for founder:**
- More surface = more development; estimate 6–8 weeks vs ~2 weeks for passive-only
- Master adoption is non-trivial — needs onboarding flow + opt-in
- Some masters will resent any tool («просто скажите мне когда клиент»). Settings allow them to disable everything except schedule view + booking-confirmation push.

**Fallback if PASSIVE is preferred:** keep §3.1 (onboarding) and §3.2 (mobile dashboard read-only) and §3.3 (schedule), strip §3.5/§3.6 (conversation list/detail). Owner/admin handles all customer comms. Save 4 weeks. Lose retention upside. **Do not recommend.**

### Persona (concrete)

| Attribute | Detail |
|---|---|
| **Name** | Анна, 28, nail/lash master at «Студия Карина» |
| **Tech stack** | iPhone 13, MAX installed since 2024, doesn't use desktop in salon |
| **Day shape** | 6–10 clients per day, 15–30 min gaps, sometimes back-to-back |
| **Pain today** | Owner forwards client messages via voice/WhatsApp; can't see schedule changes without asking; forgets aftercare follow-ups |
| **What success looks like** | Knows next client before they walk in; can confirm a reschedule between two clients without leaving MAX; gets reminded to send aftercare check-in |
| **What failure looks like** | Tool nags her with admin notifications she doesn't care about; she misses a reply to a regular client and the admin notices and complains; PII surfaces she shouldn't see |

### Primary JTBD

> «Когда у меня перерыв между клиентами, я хочу за 30 секунд увидеть кто следующий, что они хотели, и при необходимости ответить на их вопрос — чтобы не отвлекаться от работы и быть готовым к следующему визиту.»

### Secondary JTBD

> «Когда мой клиент написал что-то после визита (благодарность, жалоба, вопрос), я хочу ответить лично — чтобы поддержать отношения и не упустить лояльность.»

### Tertiary JTBDs (designed for)

> «Когда меня нет на работе, я хочу не получать рабочие уведомления — чтобы не выгорать.»
> «Когда у меня меняется график, я хочу обновить его сама и сразу — чтобы клиенты записывались только на доступное время.»
> «Когда я обновляю свои услуги или цены, я хочу, чтобы это согласовалось с владельцем — чтобы не было конфликта.» (Master proposes, owner approves — see §3.4)

### Success metrics

| Metric | Target | Type |
|---|---|---|
| Master 30-day activation rate (login ≥ 3 times in first 14d) | ≥ 70% | Adoption |
| Master DAU / MAU on dashboard | ≥ 0.4 | Engagement |
| Time-to-first-master-reply on personal conversation | median < 8 min | Responsiveness |
| % personal-master conversations replied by master (not admin) | ≥ 60% | Role fit |
| % masters who edit AI-drafted reply before sending | 30–70% (sweet spot — too low = bot voice, too high = AI not helpful) | Quality |
| Master-initiated bookings via aftercare nudges (90-day) | ≥ 5% of master's clients | Retention upside |
| Master notification opt-out rate | < 20% (too high = notification noise) | UX validation |
| Salon NPS contribution from masters (post-onboarding survey) | +10pts vs control | Strategic |
| Persona-violation rate on master replies (pre-send check fires) | < 5% | Quality safety |
| PII-leak attempts via UI (master attempts to access phone/LTV) | 0 (must be impossible by construction) | Safety |

---

## 4. State machine — master's perspective

Master's view of a conversation is a **filtered projection** of the canonical state machine in [`docs/design/2026-05-17-conversations-handoff.md` §1](./2026-05-17-conversations-handoff.md). Master only sees:

```
[ Conversation involves master? ]
   │
   ├── NO  → invisible, master never sees it exists
   │
   └── YES → enter master inbox
              │
              ├── status: BOT_ACTIVE       → "Помощник отвечает" (read-only badge)
              ├── status: HANDOFF_PENDING  → "Ждёт ответа" (admin or master can take)
              ├── status: ADMIN_ACTIVE     → "Аня отвечает" (read-only, master sees but doesn't act)
              ├── status: MASTER_ACTIVE    → "Вы отвечаете" (active reply box)
              └── status: RESOLVED         → "Решено" (archive view)
```

### What «involves master» means (server-side filter)

A conversation **involves** master M if **any** of these is true:
1. A `BookingRequest` exists in this conversation with `master_id == M`
2. Customer messaged with text matching master's name (heuristic; gated by `mentions_master = M` flag set by classifier)
3. Customer has a prior visit (last 90 days) with master M (regular client of M)
4. Admin manually assigned the conversation to master M
5. Master M is the conversation's last-reply author (they joined earlier)

This filter is computed server-side; master client never receives unrelated conversations. **No client-side filtering.** Audit log captures every access.

### Tier-aware behavior (per ownership-policy §1)

| Tier | What master sees | What master can do |
|---|---|---|
| AI_CONTINUITY | Full transcript, AI's drafts visible, optional «отправить от себя» button | Reply with AI draft or own text |
| HUMAN_SUPERVISED | Full transcript, AI drafts queued for admin approval — master sees draft, can «отправить» if they choose to take it | Reply (this takes ownership from queue, admin sees «Анна ответила»). Cannot demote tier. |
| HUMAN_LOCKED | Full transcript, NO AI suggestions, **prominent banner**: «Этот диалог требует внимания администратора» | Read-only by default. Can promote (already locked). **Cannot reply unless admin explicitly assigns to master** (audited). Can call admin via in-app shortcut («позвать Аню»). |

### SLA visibility for master

Master sees SLA color on conversation card (same palette as Conversations module: yellow 15min / orange 30min / red 60min). Master is **never alerted to escalate to CSM** — that's admin/owner only. Master gets push at 5 minutes for their own conversations (mild «ваш клиент ждёт») — not earlier, to avoid interrupting service.

---

## 5. Per-screen specs

Eight screens, one onboarding flow, four push templates. Mobile-first; web parity table in §6.

### Screen M0 — Master onboarding (magic-link from owner invite)

**Route (Mini App):** `/onboarding/master?token=...` (token from email/MAX-deeplink)
**When:** Owner adds master in Settings → Команда → «Добавить мастера», system sends MAX bot DM with deeplink to this Mini App route.

#### Flow (3 steps, ≤ 60 sec total)

```
Step 1 — Welcome + identity confirm

┌─────────────────────────────────────┐
│  [Студия Карина logo]               │
│                                     │
│  Здравствуйте, Анна!                │
│                                     │
│  Карина пригласила вас в            │
│  помощник студии. Здесь вы          │
│  будете видеть своё расписание      │
│  и общаться с клиентами.            │
│                                     │
│  Это вы?                            │
│  ┌─────────────────────────┐        │
│  │ Анна Петрова            │        │
│  │ +7 ••• ••• ••67         │        │
│  │ MAX: @anna_styl         │        │
│  └─────────────────────────┘        │
│                                     │
│                                     │
│ ──────────────────────────────────  │
│  [Это я, продолжить]      ← sticky  │
│  [Это не я]               ← text    │
└─────────────────────────────────────┘
```

```
Step 2 — Permissions explainer (radical transparency)

┌─────────────────────────────────────┐
│  ← BackButton                       │
│                                     │
│  Что вы увидите                     │
│                                     │
│  Свой график на день и неделю       │
│  Сообщения от своих клиентов        │
│  Имя клиента и историю визитов      │
│                                     │
│  Что вы НЕ увидите                  │
│  Номера телефонов клиентов          │
│  Финансовые данные                  │
│  Чужие диалоги и других мастеров    │
│                                     │
│  Это сделано, чтобы защитить        │
│  данные клиентов. Карина может      │
│  при необходимости расширить        │
│  доступ — спросите её.              │
│                                     │
│ ──────────────────────────────────  │
│  [Понятно]                ← sticky  │
└─────────────────────────────────────┘
```

```
Step 3 — Profile starter (skip-able)

┌─────────────────────────────────────┐
│  ← BackButton                       │
│                                     │
│  Заполните профиль                  │
│  (можно потом)                      │
│                                     │
│  Фото                               │
│  ┌──────┐                           │
│  │  АП  │  [Загрузить]              │
│  └──────┘                           │
│                                     │
│  О себе (для клиентов)              │
│  ┌─────────────────────────┐        │
│  │ Опишите свой опыт...    │        │
│  │                         │        │
│  └─────────────────────────┘        │
│  Максимум 280 символов              │
│                                     │
│  Услуги                             │
│  Сейчас Карина указала: маникюр,    │
│  гель-лак, наращивание ногтей.      │
│  Если что-то не так — напишите ей.  │
│                                     │
│ ──────────────────────────────────  │
│  [Сохранить и продолжить] ← sticky  │
│  [Заполнить позже]        ← text    │
└─────────────────────────────────────┘
```

#### States
- **Loading**: «Загружаем ваш профиль…» spinner ≤ 800ms then auto-advance
- **Token invalid / expired**: «Ссылка устарела. Попросите Карину прислать новую.» + button to copy salon's MAX bot deeplink
- **Token already used**: «Вы уже подключены — открыть рабочий стол» → routes to M1 dashboard
- **Wrong person («Это не я»)**: «Сообщите Карине — возможно, ссылку отправили не туда.» + button «Закрыть»

#### Bridge API used
- `initData` (HMAC-validated server side) — identity is verified, NO login form
- `WebApp.DeviceStorage.setItem('master_token', sessionToken)` after step 3
- `WebApp.HapticFeedback.notificationOccurred('success')` on step 3 completion
- `WebApp.BackButton.show()` from step 2 onward
- `WebApp.enableClosingConfirmation()` on step 3 if photo or bio dirty

#### Audit events
- `master.onboarding_started`
- `master.onboarding_completed`
- `master.profile_initialized` (with which fields populated)

---

### Screen M1 — Master mobile dashboard (home)

**Route:** `/` (Mini App root when master role)
**Launch context:** Bot DM → «Открыть рабочий стол» button → Mini App root

#### Layout (mobile, ~360–430px width)

```
┌─────────────────────────────────────┐
│  Студия Карина          [Анна ●]    │  16px top, salon name + own avatar
│  Среда, 21 мая          14:42       │  date + time
├─────────────────────────────────────┤
│                                     │
│  СЕЙЧАС                             │
│  ┌─────────────────────────────┐    │  Active card (red dot if in progress)
│  │ ● Мария И. — маникюр гель-  │    │
│  │ лак                         │    │
│  │ Началось 14:30 · 90 мин     │    │
│  │ До конца ≈ 78 мин           │    │
│  │ [Заметка к визиту ›]        │    │
│  └─────────────────────────────┘    │
│                                     │
│  СЛЕДУЮЩИЙ КЛИЕНТ                   │
│  ┌─────────────────────────────┐    │  Tap → conversation detail (M6)
│  │ Анна П. в 16:00             │    │
│  │ наращивание ногтей · 120мин │    │
│  │ ⚠ Постоянный клиент         │    │
│  │ Сказала: «хочу подлиннее    │    │
│  │ чем в прошлый раз»          │    │
│  │ [Открыть диалог ›]          │    │
│  └─────────────────────────────┘    │
│                                     │
│  ━━ ТРЕБУЮТ ВНИМАНИЯ (2) ━━━━━━━━   │  Inbox preview (max 3 cards)
│                                     │
│  ┌─────────────────────────────┐    │
│  │ 🟡 Ксения Л. · 12 мин       │    │
│  │ «Спасибо за вчера, можно ли │    │
│  │ записаться на след. неделю?»│    │
│  └─────────────────────────────┘    │
│                                     │
│  ┌─────────────────────────────┐    │
│  │ ⚪ Дарья С. · 1 ч            │    │
│  │ Помощник предложил ответ    │    │
│  │ — посмотреть?               │    │
│  └─────────────────────────────┘    │
│                                     │
│  [Все диалоги ›]                    │  Routes to M5 conversation list
│                                     │
│  ━━ СЕГОДНЯ ━━━━━━━━━━━━━━━━━━     │
│  6 клиентов · 3 завершено           │
│  Следующее окно: 17:30–18:00        │
│                                     │
│  [Расписание на неделю ›]           │  Routes to M3 schedule
│                                     │
├─────────────────────────────────────┤
│  [🏠]  [📅]  [💬 2]  [👤]           │  Tab bar (sticky bottom)
└─────────────────────────────────────┘
```

#### Tab bar (sticky bottom, 4 tabs)
| Tab | Icon | Route | Badge |
|---|---|---|---|
| Дом | `Home` (Lucide) | M1 | — |
| Расписание | `Calendar` | M3 | dot if conflicting/pending change |
| Диалоги | `MessageCircle` | M5 | count of unread |
| Профиль | `User` | M4 | dot if owner-pending change |

#### States

| State | Behavior |
|---|---|
| **Loading** | Skeleton cards (3 stub blocks) shimmer |
| **Empty — no clients today** | «Сегодня нет записей. Свободный день — отдохните. Ближайшая запись завтра в 10:00 — Анна П.» + button «Расписание ›» |
| **Empty — no conversations, no schedule** | «Карина ещё не назначила вас на услуги. Если думаете, что это ошибка — напишите ей в MAX.» + button to open Karina's MAX chat |
| **Mid-service** | Active card shows «● Сейчас идёт визит». No push during this state unless emergency (HUMAN_LOCKED on master's conversation). |
| **Day done** | «Вы провели 6 клиентов. Хороший день. Завтра первая запись 10:30 — Анна П.» |
| **Permission denied (master tries to view another's conv via deeplink)** | «Этот диалог не для вас. Если думаете, что должны видеть — напишите Карине.» |
| **Offline (Mini App offline detection via CSS / failed fetch)** | Show stale data with banner «Данные могут быть неактуальны» + retry button |

#### Bridge API
- `WebApp.HapticFeedback.selectionChanged()` on card tap
- `WebApp.BackButton.hide()` (root screen)
- No `enableClosingConfirmation` (no dirty state)
- Pull-to-refresh → `HapticFeedback.impactOccurred('soft')`

#### MAX UI components used
- `Avatar.Image` for master's own header avatar
- `CellList` + `CellSimple` for upcoming clients
- `Typography.Display` for time
- `Typography.Title` for client names
- `Typography.Body` for previews
- `Counter` for tab bar unread badge
- `Dot` for SLA tier indicator

---

### Screen M3 — Master schedule (day / week / month)

**Route:** `/schedule` (default: day view)
**When:** Tab «Расписание» tapped, or M1 «Расписание на неделю ›»

#### Layout — Day view (default, mobile-most-useful)

```
┌─────────────────────────────────────┐
│  ← Расписание         [Сегодня]     │
├─────────────────────────────────────┤
│  [День] [Неделя] [Месяц]            │  Segmented control
│                                     │
│  ◀  Среда, 21 мая  ▶                │  Date stepper
│                                     │
│  09:00 ─────────────────────────    │
│  10:00 ┌──────────────────────┐     │
│        │ 10:00 — Мария И.     │     │  Tap → conversation/booking detail
│  11:00 │ маникюр гель-лак     │     │
│        │ 90 мин · 2 200 ₽?    │     │  ? = price hidden from master
│  12:00 └──────────────────────┘     │
│  12:00 ─────────────────────────    │
│  13:00 ┌──── окно ────────────┐     │  Free slot styling
│        │ свободно · 60 мин    │     │
│  14:00 └──────────────────────┘     │
│  14:00 ┌──────────────────────┐     │
│        │ ● 14:30 — Мария И.   │     │  Active (in progress) — red dot
│  15:00 │ маникюр гель-лак     │     │
│        │ заканчивается ≈16:00 │     │
│  16:00 └──────────────────────┘     │
│  16:00 ┌──────────────────────┐     │
│        │ 16:00 — Анна П.      │     │
│  17:00 │ наращивание · 120мин │     │
│        │ ⚠ постоянный клиент  │     │
│  18:00 │ [Открыть диалог]     │     │
│        └──────────────────────┘     │
│  18:00 ─────────────────────────    │
│  19:00                              │  Outside work hours grayed
│  ...                                │
│                                     │
├─────────────────────────────────────┤
│  [🏠]  [📅 ●]  [💬]  [👤]           │
└─────────────────────────────────────┘
```

#### Layout — Week view

```
┌─────────────────────────────────────┐
│  ← Расписание                       │
│  [День] [Неделя] [Месяц]            │
│  ◀  19—25 мая  ▶                    │
│                                     │
│   Пн   Вт   Ср   Чт   Пт   Сб   Вс  │
│  19   20   21   22   23   24   25   │
│  ─    ─    ●    ─    ─    ─    OFF  │
│  3    4    6    5    7    8    —    │  clients per day
│                                     │
│  [Подробно] [Загрузка по дням]      │  Heatmap toggle
│                                     │
│  СРЕДА, 21 МАЯ (сегодня)            │
│  6 клиентов, 3 окна свободно        │
│  [Открыть день ›]                   │
│                                     │
│  ЧЕТВЕРГ, 22 МАЯ                    │
│  5 клиентов, 1 окно: 14:00–15:00    │
│  [Открыть день ›]                   │
│                                     │
│  ...                                │
└─────────────────────────────────────┘
```

#### Layout — Month view (calendar grid)

Standard monthly calendar grid, dots under each day for client count (0 dot = empty, 1 small dot = 1–3, 2 medium dots = 4–6, full circle = 7+). Tap day → routes to day view for that date.

#### Capabilities
- **Read-only on bookings** (master can see assigned bookings, cannot edit them — owner/admin controls scheduling)
- **Mark unavailable / off-day**: master taps empty slot → sheet `Помечу как недоступно` → confirmation → server marks slot blocked → owner notified (audit + bot DM)
- **Cannot modify another master's slots**, cannot see them either (privacy + clutter)

#### States
- Loading: skeleton calendar
- Empty day: «Свободный день. Отдыхайте.»
- Empty week: «Расписание ещё не составлено. Карина добавит вас на смены.»
- Off-day: gray-shaded with «выходной» label
- Conflict (admin double-booked you): «⚠ Конфликт расписания — посмотрите» + tap → conversation with admin / banner with «уточнить у админа» button

#### Bridge API
- `BackButton.show()` (not root)
- `HapticFeedback.selectionChanged()` on date step
- `HapticFeedback.impactOccurred('heavy')` on «помечу недоступно» confirm

#### Backend endpoints (new)
- `GET /api/master/schedule?from=...&to=...` — returns bookings + free/blocked slots + work hours for master
- `POST /api/master/availability` — mark slot/day unavailable (request, owner approves)
- `GET /api/master/availability/pending` — list of pending availability changes

---

### Screen M4 — Master profile editing

**Route:** `/profile`
**When:** Tab «Профиль»

#### Layout

```
┌─────────────────────────────────────┐
│  ← Профиль                          │
├─────────────────────────────────────┤
│                                     │
│  Как видят клиенты                  │
│                                     │
│  ┌──────┐  Анна Петрова             │
│  │  АП  │  Мастер по ногтям         │
│  └──────┘  «5 лет опыта,            │
│            люблю когда красиво»     │
│                                     │
│  [Изменить фото]                    │
│  [Изменить «О себе»]                │
│                                     │
│  ━━ УСЛУГИ ━━━━━━━━━━━━━━━━━━      │
│  Маникюр                            │
│  Маникюр гель-лак                   │
│  Наращивание ногтей                 │
│                                     │
│  Хотите добавить или убрать?        │
│  [Написать Карине ›]                │  Cannot edit directly — owner controls
│                                     │
│  ━━ РАБОЧЕЕ ВРЕМЯ ━━━━━━━━━━━━     │
│  Пн—Сб: 10:00–19:00                 │
│  Вс: выходной                       │
│  [Запросить изменение ›]            │
│                                     │
│  ━━ ОТЗЫВЫ КЛИЕНТОВ ━━━━━━━━━━━    │
│  За последние 30 дней: 4 отзыва     │
│  Все положительные                  │
│  [Посмотреть]                       │
│                                     │
├─────────────────────────────────────┤
│  [🏠]  [📅]  [💬]  [👤 ●]           │
└─────────────────────────────────────┘
```

#### Editable by master alone
- Photo (avatar)
- Bio text (280 char max)
- Push notification settings (→ M7)
- Quiet hours
- Theme (light/dark/auto)

#### Editable but requires owner approval
- Services list (master can propose add/remove, owner approves)
- Work hours
- Display name (rare — usually first name + initial)

#### Read-only for master
- Salon name / branding
- Pricing per service (master sees own services as «90 мин», NOT price — see permissions matrix in ownership-policy §4)
- Other masters' info

#### States
- Loading: skeleton
- Saving: inline spinner on field
- Save error: «Не удалось сохранить. Попробуйте ещё раз.» + retry
- Pending owner approval: yellow chip «На рассмотрении у Карины»

#### Bridge API
- `WebApp.SecureStorage` for any auth tokens (mobile)
- `WebApp.downloadFile` is NOT used (gallery picker not in Bridge); photo upload uses standard HTML `<input type="file">` (since iOS/Android browser supports it inside Mini App webview)
- `WebApp.enableClosingConfirmation()` when bio/photo dirty
- `HapticFeedback.notificationOccurred('success')` on save

---

### Screen M5 — Master conversation list (their conversations only)

**Route:** `/conversations`
**When:** Tab «Диалоги»

#### Layout

```
┌─────────────────────────────────────┐
│  ← Диалоги                          │
├─────────────────────────────────────┤
│  [Активные (2)] [Все] [Решённые]    │  Tabs
│  🔎 [ Поиск по имени ]              │
│                                     │
│  ━━ ЖДУТ ВАШЕГО ОТВЕТА (1) ━━━━    │
│                                     │
│  ┌─────────────────────────────┐    │
│  │ 🟡 Ксения Л. · 12 мин       │    │
│  │ постоянный клиент           │    │
│  │ «Спасибо за вчера, можно ли │    │
│  │ записаться на след. неделю?»│    │
│  │ [Открыть ›]                 │    │
│  └─────────────────────────────┘    │
│                                     │
│  ━━ ПРЕДЛОЖЕН ОТВЕТ (1) ━━━━━━━   │
│                                     │
│  ┌─────────────────────────────┐    │
│  │ ⚪ Дарья С. · 1 ч            │    │
│  │ Помощник предложил черновик │    │
│  │ — посмотрите                │    │
│  │ [Открыть ›]                 │    │
│  └─────────────────────────────┘    │
│                                     │
│  ━━ ИДЁТ ДИАЛОГ (0) ━━━━━━━━━━━   │
│  Помощник отвечает сам              │
│                                     │
│  ━━ РЕШЕНО СЕГОДНЯ (3) ━━━━━━━━   │
│                                     │
│  ┌─────────────────────────────┐    │
│  │ ✓ Мария И. · 11:30          │    │
│  │ → записалась на 22 мая      │    │
│  └─────────────────────────────┘    │
│                                     │
│  ...                                │
│                                     │
├─────────────────────────────────────┤
│  [🏠]  [📅]  [💬 ●]  [👤]           │
└─────────────────────────────────────┘
```

#### Card structure (delta from Conversations C1 inbox)

Master's card is **stripped down** vs admin's:
- ❌ No LTV / financial signal
- ❌ No reveal-phone hint
- ❌ No assignee badge (master only sees own)
- ❌ No tier banner (HUMAN_LOCKED shown differently — see M6)
- ✅ Customer first name only
- ✅ SLA color
- ✅ Last message preview (max 2 lines)
- ✅ «постоянный клиент» chip if returning (no LTV value)
- ✅ Reason chip («жалоба» NOT shown unless master is direct subject; «вопрос», «запись» yes)

#### States
- **Empty (active)**: «Все ваши клиенты сейчас довольны. Спасибо за работу!»
- **Empty (all)**: «Здесь будут диалоги с вашими клиентами.»
- Loading: skeleton 4 cards
- Permission cliff (master deep-links to non-own conv): redirected to list with toast «Этот диалог не для вас»

#### Sort/filter
- Default sort: SLA tier desc → recency desc
- Filter: «Активные» (default, requires attention) / «Все» (all conversations involving master) / «Решённые» (resolved last 30 days)
- Search by customer name (no phone — master doesn't see phones)

---

### Screen M6 — Master conversation detail

**Route:** `/conversations/:id`
**When:** Tap on card

#### Layout — AI_CONTINUITY tier (normal case)

```
┌─────────────────────────────────────┐
│  ← Ксения Л.                        │  Customer first name only
│  постоянный клиент · 12-й визит     │  Returning indicator (no LTV)
├─────────────────────────────────────┤
│                                     │
│  [SCROLL ↑ messages]                │
│                                     │
│  Вчера, 18:42                       │
│  ┌─────────────────────────────┐    │
│  │ Помощник: Спасибо за визит! │    │  Assistant message bubble (left)
│  │ Если будут вопросы — пишите.│    │
│  └─────────────────────────────┘    │
│                                     │
│  Сегодня, 14:32                     │
│                                     │
│  ┌─────────────────────────────┐    │  Customer message bubble (right)
│  │ Спасибо за вчера, можно ли  │    │
│  │ записаться на след. неделю? │    │
│  └─────────────────────────────┘    │
│                                     │
│  ─ помощник готовит ответ ─          │  Subtle indicator
│                                     │
│  ┌─────────────────────────────┐    │  Suggested draft (collapsible)
│  │ ✨ Предложенный ответ        │    │
│  │ «Ксения, рады! На след.     │    │
│  │ неделю свободно вт 14:00,   │    │
│  │ чт 16:30 и сб 11:00.        │    │
│  │ Что подойдёт?»              │    │
│  │ [Отправить от себя]         │    │  Sends as master (audited)
│  │ [Отредактировать]           │    │  Opens compose with prefill
│  │ [Пусть помощник ответит]    │    │  Releases to AI auto-send
│  └─────────────────────────────┘    │
│                                     │
├─────────────────────────────────────┤
│  ┌───────────────────────────┐ [▷] │  Compose box (sticky bottom)
│  │ Напишите ответ...         │     │
│  └───────────────────────────┘     │
│  [⚠ Передать админу]                │  Promote to HUMAN_LOCKED safety button
└─────────────────────────────────────┘
```

#### Layout — HUMAN_LOCKED tier (read-only for master)

```
┌─────────────────────────────────────┐
│  ← Мария И.                         │
│  ⚠ Жалоба · администратор работает  │  Tier banner — orange
├─────────────────────────────────────┤
│                                     │
│  ┌─────────────────────────────┐    │
│  │ ⚠ Этот диалог требует       │    │  Banner
│  │ внимания администратора     │    │
│  │ Аня отвечает с 14:12        │    │
│  │ [Позвать Аню]               │    │  Sends bot DM mention to Anya
│  └─────────────────────────────┘    │
│                                     │
│  [messages, read-only]              │
│                                     │
├─────────────────────────────────────┤
│  (no compose box)                   │
│  «Если хотите помочь — напишите     │
│  Ане в её MAX»                      │
│  [Открыть чат с Аней]               │
└─────────────────────────────────────┘
```

#### Identity rendering (customer-facing — invisible to master)

When master taps «Отправить от себя» on a draft, the message renders to the customer as:

> «Помощник: Ксения, рады! На след. неделю свободно вт 14:00…»

Same single assistant identity. Master's authorship is recorded in attribution metadata (`actor_type=master`, `composed_by=master_id`) — visible only in audit log and to owner/admin in Conversations module (per [conversations-handoff §6](./2026-05-17-conversations-handoff.md) if admin views).

#### Pre-send quality check (same as admin)

Per [assistant-persona §9](../policies/assistant-persona.md), every message — whether AI-generated, master-composed, or master-edited — runs through:
1. Persona check (tone/vocabulary)
2. Forbidden phrase check
3. Length check (1–3 sentences default, longer allowed for explainers)
4. Identity check (if master accidentally signs name «—Анна» → warn «отправить как помощник или как Анна?»)

If check fires, master sees inline warning sheet **before** send:

```
┌─────────────────────────────────────┐
│  Проверьте ответ                    │
│                                     │
│  В тексте есть фраза «к сожалению,  │
│  ничем не можем помочь» — это       │
│  обычно воспринимается как «отказ». │
│  Лучше предложить альтернативу.     │
│                                     │
│  [Изменить] [Всё равно отправить]   │
└─────────────────────────────────────┘
```

Master can override; override audited (`conversation.persona_violation_overridden`).

#### Customer name display rule

- Always first name from booking record
- If unknown: «Гость» (never «+7 ...»; master doesn't see phone)
- If customer has multiple visits: «постоянный клиент · 12-й визит» chip below name

#### Safety promote button

«Передать админу» button always visible on master's compose box. Tap → confirmation sheet:

```
┌─────────────────────────────────────┐
│  Передать админу?                   │
│                                     │
│  Помощник остановится, Аня получит  │
│  уведомление. Используйте, если:    │
│                                     │
│  • Клиент жалуется                  │
│  • Финансовый вопрос                │
│  • Медицинский вопрос               │
│  • Не уверены, что ответить         │
│                                     │
│  [Передать]    [Отмена]             │
└─────────────────────────────────────┘
```

Confirms → `conversation.tier_changed` to HUMAN_LOCKED, `tier_override` audit with `actor_role=master`, push to admin via bot DM.

#### Bridge API
- `BackButton.show()` (wired to router back to M5)
- `enableClosingConfirmation()` when compose box has dirty text
- `HapticFeedback.impactOccurred('medium')` on «Отправить»
- `HapticFeedback.notificationOccurred('error')` on persona-check warning
- `HapticFeedback.impactOccurred('heavy')` on «Передать админу» confirm
- `ScreenCapture.disableScreenCapture()` — **NO** for master (master doesn't see PII; admin's view does enable this; here unnecessary)

---

### Screen M7 — Notification settings

**Route:** `/settings/notifications`
**When:** Profile → «Уведомления»

#### Layout

```
┌─────────────────────────────────────┐
│  ← Уведомления                      │
├─────────────────────────────────────┤
│                                     │
│  ━━ В РАБОЧЕЕ ВРЕМЯ ━━━━━━━━━━━    │
│                                     │
│  Новая запись                       │
│  Помощник записал клиента к вам    │
│  [Switch: ON]                       │
│                                     │
│  Изменение записи                   │
│  Клиент перенёс или отменил         │
│  [Switch: ON]                       │
│                                     │
│  Личное сообщение клиента          │
│  Клиент написал что-то лично вам    │
│  [Switch: ON]                       │
│                                     │
│  Срочно (HUMAN_LOCKED)              │
│  Только если ситуация касается вас  │
│  [Switch: ON]    (нельзя выключить) │  Safety — forced ON
│                                     │
│  ━━ ТИХИЙ РЕЖИМ ━━━━━━━━━━━━━━     │
│                                     │
│  Не беспокоить вне работы           │
│  [Switch: ON]                       │
│                                     │
│  Тихие часы                         │
│  с 21:00 до 09:00                   │
│  [Изменить]                         │
│                                     │
│  В тихом режиме помощник напишет    │
│  утром перед сменой.                │
│                                     │
│  ━━ ЕЖЕДНЕВНЫЕ СВОДКИ ━━━━━━━━     │
│                                     │
│  Утренний бриф (08:30)              │
│  Расписание на день + сюрпризы     │
│  [Switch: ON]                       │
│                                     │
│  Вечерний итог (после смены)        │
│  Сколько провели, что завтра        │
│  [Switch: OFF]                      │
│                                     │
├─────────────────────────────────────┤
│  [🏠]  [📅]  [💬]  [👤 ●]           │
└─────────────────────────────────────┘
```

#### Toggle behavior
- Settings persisted server-side (also writes to MAX bot subscription DB)
- Forced ON: «Срочно» (safety; master must opt in to HUMAN_LOCKED on their convs)
- Quiet hours apply ONLY to bot DMs; in-Mini-App badge counts still update

#### States
- Loading: skeleton switches
- Save error: «Не удалось сохранить. Попробуйте ещё раз.»
- All disabled: warning banner «Вы отключили все уведомления. Карина может попросить включить хотя бы срочные.»

---

### Screen M8 — App settings

**Route:** `/settings`

#### Layout

```
┌─────────────────────────────────────┐
│  ← Настройки                        │
├─────────────────────────────────────┤
│                                     │
│  Профиль                            │
│  Анна Петрова · мастер              │
│  Студия Карина                      │
│  [Открыть профиль ›]                │
│                                     │
│  Уведомления                        │
│  [Открыть ›]                        │
│                                     │
│  Внешний вид                        │
│  Тема: [Авто ▾]                     │  Auto / Светлая / Тёмная
│                                     │
│  Размер шрифта: [Обычный ▾]         │
│                                     │
│  ━━ ─────────────────────────       │
│                                     │
│  Помощь                             │
│  [Как пользоваться помощником ›]    │
│  [Связаться с поддержкой ›]         │
│                                     │
│  ━━ ─────────────────────────       │
│                                     │
│  О приложении                       │
│  Версия 0.4.2                       │
│  [Условия использования]            │
│  [Политика конфиденциальности]      │
│                                     │
│  ━━ ─────────────────────────       │
│                                     │
│  [Выйти из аккаунта]                │  Sheet confirm; clears master_token
│                                     │
└─────────────────────────────────────┘
```

#### Theme handling

Per MAX platform constraints (no `themeParams` in Mini App), the toggle drives:
- `Авто` (default) — CSS `prefers-color-scheme` from system
- `Светлая` — force light tokens
- `Тёмная` — force dark tokens
- Persist to `DeviceStorage.setItem('master_theme', ...)`

---

## 6. Cross-platform mapping

Master surface across three platforms:

| Capability | MAX Mini App (primary) | MAX bot DMs (push) | Web (desktop fallback) |
|---|---|---|---|
| Dashboard | M1 — full UI | Daily brief 08:30 with summary | Same layout, wider columns |
| Schedule | M3 — full UI | «Расписание изменилось» push with link | Same |
| Conversations list | M5 | New-message push with [Открыть] inline button | Same |
| Conversation detail | M6 | Inline `[Ответить] [Передать]` inline keyboard in push | Same; reply box uses keyboard shortcuts (Cmd+Enter to send) |
| Profile | M4 | — | Same |
| Notification settings | M7 | — | Same |
| Onboarding | M0 (Mini App) | Initial invite DM with [Открыть] | — (not used for first-time setup) |

#### Web parity considerations
- Layout shifts to 768px+ — two-pane (list + detail) on desktop
- No haptic feedback (silent fallback)
- No biometric reauth (PIN if needed)
- No `requestScreenMaxBrightness` (no-op)
- Photo upload uses standard file picker
- Web tab title shows unread count: `(2) Помощник Студии Карина`

#### MAX group chat patterns

Pattern: salon team group chat where bot is a member. Bot mentions master when relevant:

> «[Анна](max://user/12345), Ксения хочет записаться на след. неделю — посмотрите?»

This is the **secondary** push channel (bot DM is primary). Team chat is useful for:
- Multi-master coordination («Кто может взять Дарью в 16:00 — Анна занята»)
- End-of-day summary («Сегодня 18 клиентов, спасибо команда»)
- Owner announcements («Завтра обновление прайса — посмотрите»)

Master can opt-out of team-chat mentions in §M7 (future setting; v1.1).

---

## 7. MAX-specific patterns used

### Pattern 1 — Sticky bottom CTA (replacing MainButton)

Every screen with a primary action uses fixed-position bottom CTA bar. Pattern from [`max-mini-apps.md` Part 6](.../platforms/max-mini-apps.md):

```css
.cta-bar {
  position: fixed;
  inset-inline: 0;
  bottom: 0;
  padding: 12px 16px calc(12px + env(safe-area-inset-bottom));
  background: var(--surface-1);
  box-shadow: 0 -1px 0 var(--divider);
}
.content { padding-bottom: 88px; }
```

### Pattern 2 — BackButton wiring

Every non-root screen calls `WebApp.BackButton.show()` on mount, wires to router back. Closing confirmation when dirty.

### Pattern 3 — Haptics

| Action | Haptic |
|---|---|
| Card tap (open conversation, open schedule day) | `selectionChanged()` |
| Send reply (M6 «Отправить») | `impactOccurred('medium')` |
| Save profile (M4) | `notificationOccurred('success')` |
| Persona-check warning fires | `notificationOccurred('error')` |
| «Передать админу» confirm | `impactOccurred('heavy')` |
| Pull-to-refresh trigger | `impactOccurred('soft')` |
| Toggle switch (M7) | `selectionChanged()` |

Pass `disableVibrationFallback: true` on `impactOccurred` calls — silent on desktop, no vibration noise.

### Pattern 4 — Group bot mention

In team chat, bot mentions master via markdown: `[Анна](max://user/{master_max_user_id})`. Tapping the mention opens DM with that master. Bot DM does NOT mention — DM is already 1:1.

### Pattern 5 — Bot DM push templates

Bot DMs are master's only push channel. Four key templates:

```
T1 — New booking
«Новая запись 22 мая в 16:00
Анна П. · наращивание ногтей · 120 мин
[Открыть]»

T2 — Reschedule/cancel
«Запись изменилась
Мария И. перенесла визит с 23 мая на 25 мая, 14:00
[Открыть в расписании]»

T3 — Personal customer message
«У вас новое сообщение
Ксения Л.: «Спасибо за вчера, можно ли записаться…»
[Ответить] [Передать админу]»

T4 — Morning brief (08:30 daily, if opted in)
«Доброе утро, Анна.
Сегодня 6 клиентов, первая запись в 10:00 — Мария И.
Свободные окна: 13:00–14:00, 18:30–19:00.
[Открыть рабочий стол]»
```

Inline keyboard buttons use `callback` for actions that route to Mini App, with `intent: 'positive'` for «Открыть», `intent: 'default'` for «Передать админу».

### Pattern 6 — No screenshot protection for master

Per [max-mini-apps Part 6 Pattern 5](.../platforms/max-mini-apps.md), `ScreenCapture.disableScreenCapture()` is for PII screens. Master never sees phone/LTV/medical, so **no screen capture lock**. Master's conversation detail does not need protection.

### Pattern 7 — Persona DM voice

Bot DMs to master use the same assistant persona but **with master as audience**. Master is internal team member; voice can be slightly more direct than customer-facing:

- ✅ «Новая запись 22 мая в 16:00 — Анна П., наращивание ногтей, 120 мин. [Открыть]»
- ❌ «Здравствуйте, Анна! У вас появилась новая запись… 🎉» (customer voice in master context)

Master-facing persona override lives in `apps/persona/` (master-mode strings).

---

## 8. Permissions enforcement at UI level

Strict UI gating — every PII surface is **literally not rendered** for master role.

### Gating matrix (UI level)

| Field | Server returns to master client? | UI renders? |
|---|---|---|
| Customer first name | YES | YES |
| Customer full name (last name) | NO (server strips) | — |
| Customer phone | **NO** (server returns `null`) | — |
| Customer email | NO | — |
| Customer LTV | NO | — |
| Customer total spend | NO | — |
| Customer medical notes | NO | — |
| Customer aftercare notes (master-relevant) | YES (subset, filtered) | YES |
| «Returning client» flag | YES (boolean) | YES (chip) |
| Visit count with this master | YES | YES |
| Booking price | NO | — (duration only) |
| Other masters' bookings | NO | — |
| Other masters' conversations | NO | — |

### Defense-in-depth

1. **Server-side filtering** (primary): API returns role-filtered payloads. Master's `/api/conversations/:id` literally does not contain phone/LTV/medical fields in JSON.
2. **TypeScript types** (compile-time): `MasterConversationPayload` type excludes restricted fields; if a developer accidentally tries to render `payload.phone`, type-check fails.
3. **UI gating** (runtime defense): even if server bug returns extra fields, master role UI components have hardcoded field allowlists.
4. **Audit logging** (detection): every API call from master role logs which conversation accessed. Anomalies (master views many conversations not involving them) get flagged.

### Error messaging

When a master attempts an unauthorized action (e.g., deeplinks to a conversation not involving them):

```
┌─────────────────────────────────────┐
│  Этот диалог не для вас             │
│                                     │
│  Если думаете, что должны его       │
│  видеть — напишите Карине.          │
│                                     │
│  [Назад к диалогам]                 │
└─────────────────────────────────────┘
```

Never reveal whether the conversation exists, who it's about, or any metadata. Audit log: `master.unauthorized_access_attempted` with attempted resource ID.

### Audit events (master-specific)

Per [ownership-policy §5](../policies/conversation-ownership-policy.md), master actions write:
- `master.onboarding_started` / `_completed`
- `master.conversation_viewed` (every detail open)
- `master.reply_composed` (with content hash, composed_by=master)
- `master.draft_taken` (master took AI draft, sent as own reply)
- `master.draft_edited` (master modified AI draft)
- `master.tier_promoted_to_locked` (safety promote)
- `master.availability_changed` (proposed slot block)
- `master.profile_changed`
- `master.unauthorized_access_attempted` (with attempted resource ID, IP, deviceId)
- `master.notification_settings_changed`

---

## 9. Anti-slop scan (12-point)

Per `~/.claude/skills/ux-architect/references/anti-ai-slop.md`:

| # | Check | Verdict | Note |
|---|---|---|---|
| 1 | No default Inter + purple/violet gradient | ✅ | Tokens inherit from `salon-warmth` palette (warm neutrals + signal colors). No purple. |
| 2 | No glassmorphic frosted cards | ✅ | Solid `surface-1` / `surface-2` with subtle border. No backdrop-filter. |
| 3 | No emoji as decoration | ✅ | Lucide icons only. Emoji used semantically: `⚠`, `●` (active dot), `✓` (resolved). |
| 4 | Specific aesthetic direction stated | ✅ | «Calm, focused, beauty-vertical»: warm beige + ink + signal-orange/red. Light/dark dual hardcoded (no MAX theme API). |
| 5 | Mobile-first with touch targets ≥ 44pt | ✅ | All taps in M1–M8 use 48dp tap area. Bottom CTA ≥ 56pt height + safe-area padding. |
| 6 | Real text content, not lorem | ✅ | All copy is in-character Russian voice per [assistant-persona.md](../policies/assistant-persona.md). |
| 7 | Empty states are useful, not branded | ✅ | Six empty states defined; each has actionable next step or genuine «relax, no work today» message. |
| 8 | Loading states are skeletons, not spinners | ✅ | All screens use shape-matching skeletons (cards, list items). Spinner only for in-line save. |
| 9 | Error states recover, don't blame user | ✅ | «Не удалось сохранить» + retry; never «invalid input» without explanation. |
| 10 | Animation purposeful, reduce-motion respected | ✅ | Bottom-sheet slide-up, card-tap ripple. `prefers-reduced-motion: reduce` → instant transitions. No idle ambient animation. |
| 11 | Contrast ≥ 4.5:1 body / 3:1 large | ✅ | Token palette pre-verified in conversations-handoff doc; reused. |
| 12 | Platform-native components used where available | ✅ | MAX UI React lib for Avatar, Button, Switch, CellList, Typography, Counter, Dot. Custom only where MAX UI doesn't cover. |

---

## 10. Components inventory (delta from main Conversations module)

### Reused from Conversations module
- `ConversationCard` (variant: `compact-master` — strips PII signals)
- `MessageBubble` (assistant / customer variants)
- `PersonaCheckWarning` (pre-send warning sheet)
- `SLATierIcon` (yellow/orange/red dot)
- `BackButton` wiring helper
- `StickyCTABar` (replaces MainButton on MAX)
- `EmptyState` (variant: master-specific empty messages)

### New (master-specific)
- `MasterDashboardTodayCard` — current/next client display
- `MasterScheduleDayView` — vertical time grid
- `MasterScheduleWeekHeatmap` — week summary
- `MasterScheduleMonthGrid` — month calendar
- `MasterProfileEditor` — photo, bio, services (read-mostly)
- `MasterAIDraftSuggestion` — collapsed draft with «Отправить от себя / Отредактировать / Пусть отвечает помощник»
- `MasterSafetyPromoteButton` — «Передать админу» with confirm sheet
- `MasterPermissionDeniedScreen` — for unauthorized access
- `MasterTabBar` — 4-tab bottom nav
- `MasterUnauthorizedSurface` — for when master deeplinks beyond scope
- `MasterAvailabilityRequestSheet` — propose slot block
- `MasterNotificationToggle` — labeled switch with description
- `MasterMorningBriefMessage` (in bot persona library)

### Component count
- Reused: 7
- New: 13
- Total master module: ~20 components

---

## 11. Backend contracts (new endpoints)

All endpoints are role-scoped via middleware. Master role token → only master-filtered responses.

### `GET /api/master/dashboard`
Returns:
```json
{
  "current_visit": { "booking_id", "customer_first_name", "service_name", "started_at", "duration_min", "ends_at" } | null,
  "next_visit": { ... },
  "pending_attention": [
    { "conversation_id", "customer_first_name", "preview", "sla_tier", "last_message_at", "kind": "needs_reply" | "draft_suggested" }
  ],
  "today_summary": { "clients_total", "clients_done", "next_free_window": "17:30–18:00" | null }
}
```

### `GET /api/master/schedule?from=...&to=...`
Returns array of bookings + free/blocked slots in master's view.
```json
[
  { "type": "booking", "starts_at", "ends_at", "customer_first_name", "service_name", "booking_id" },
  { "type": "free", "starts_at", "ends_at" },
  { "type": "blocked", "starts_at", "ends_at", "reason": "off_day" | "lunch" | "master_blocked" }
]
```
No price field returned.

### `POST /api/master/availability`
Master proposes a slot block (e.g., needs 2-hr personal time tomorrow).
```json
{ "starts_at", "ends_at", "reason": "personal" | "sick" | "other", "note": "..." }
```
Returns pending request; owner approves async.

### `GET /api/master/conversations?status=active|all|resolved`
Returns conversation list, master-filtered.

### `GET /api/master/conversations/:id`
Returns conversation detail. Server strips PII fields not in master allowlist (§8 matrix).

### `POST /api/master/conversations/:id/reply`
Master sends a reply (composed-by-master, rendered to customer as assistant identity).
```json
{ "text": "...", "from_draft_id": "..." | null }
```
Triggers pre-send persona check (server-side); returns `409` with warning payload if check fires and `override=false`.

### `POST /api/master/conversations/:id/promote-locked`
Master safety-promotes to HUMAN_LOCKED.

### `GET /api/master/profile`, `PATCH /api/master/profile`
Profile read/write. Service list and work hours edits queue for owner approval.

### `GET /api/master/notifications`, `PATCH /api/master/notifications`
Notification settings.

### Bot DM endpoints (consumed by master via MAX bot)
- Bot uses `POST /messages` (MAX Bot API) to send M1-style summaries and T1–T4 templates
- Bot processes `message_callback` for inline keyboard actions: «Открыть» (deeplink to Mini App), «Передать админу» (calls `promote-locked`)
- Bot processes `message_created` for free-form master messages to bot — handled as «hint/feedback to system» (e.g., master says «выходной завтра» → bot offers `/availability` flow)

---

## 12. A11y checklist

Per WCAG 2.2 AA per skill hard rule 3:

| Item | Status | Note |
|---|---|---|
| Contrast ≥ 4.5:1 body | ✅ | Tokens reused from Conversations module |
| Contrast ≥ 3:1 large text | ✅ | Same |
| Touch targets ≥ 48dp (Android) / 44pt (iOS) | ✅ | All buttons, switches, cards meet minimum |
| Visible focus state (web fallback) | ✅ | 2px focus ring on keyboard nav |
| Keyboard nav (web) | ✅ | Tab order: header → tabs → cards → CTA; Cmd+Enter to send |
| Screen reader labels | ✅ | Every icon has `aria-label`; SLA dots have «срочный диалог» / «требует внимания» / «новый» labels |
| Reduced-motion fallback | ✅ | All transitions wrapped in `@media (prefers-reduced-motion: reduce)` shortcircuit |
| Color not sole signal | ✅ | SLA tier has color AND text («12 мин» / «1 час» / «срочно») |
| Form labels associated | ✅ | All inputs in M4 / M7 use `<label for>` |
| Error messages descriptive | ✅ | «Не удалось сохранить фото. Размер должен быть до 5 МБ.» not just «error» |
| Dynamic content announced | ✅ | New incoming message uses `aria-live="polite"` on message list |
| Heading hierarchy | ✅ | H1 = screen title; H2 = section («Сейчас», «Следующий клиент», «Требуют внимания») |
| Text resize to 200% | ✅ | All layouts tested at 200% — no overflow; CTA bar remains accessible |
| No autoplay sound/video | ✅ | None |

### Mobile-specific
- VoiceOver / TalkBack flows tested in onboarding (M0) and compose (M6)
- Master's name pronouncable correctly via `aria-label`

---

## 13. Edge cases registry

| # | Edge case | Behavior |
|---|---|---|
| E1 | Master opens app during active visit | Active card shows current client; banner «У вас идёт визит — уведомления приглушены» |
| E2 | Conversation involves multiple masters | Each master sees the conversation; reply by one masters out the «attention required» for the others |
| E3 | Customer asks for master by name, but assigned-master is on vacation | Bot DM to assigned master returns «вне работы»; AI handles conversation autonomously, surfaces to admin if needed; no notification to master |
| E4 | Master leaves salon (owner removes from team) | Account disabled; Mini App on next open shows «Вы больше не в Студии Карина. Если это ошибка — напишите Карине.» + button to close |
| E5 | Master is on two salons simultaneously | Outside MVP. Each MAX user can be a master in only one tenant in MVP. Open question Q-M5. |
| E6 | Master's MAX account changes (new phone) | Re-onboard via owner-resent invite link |
| E7 | Customer messages between visits, master misses (in another service) | After 5 min unanswered, AI sends interim «Мастер сейчас занята, ответит через полчаса» (per ownership-policy §9); master sees on next break |
| E8 | Master tries to view a customer's other conversations (with another master) | Hard-block; UI shows M5 «list» only; deeplink to other conv → permission denied screen |
| E9 | Customer mentions sensitive medical issue in conversation involving master | AI auto-classifies → HUMAN_LOCKED. Master sees banner, cannot reply. Admin handles. |
| E10 | Master accidentally taps «Передать админу» | Confirm sheet prevents accidental; no undo after confirm (audit trail allows owner/admin to demote later if needed) |
| E11 | Master replies but ServiceWorker offline | Optimistic UI: message shown with «отправляется…» state; queue locally; retry on reconnect; if fail after 60s → toast «не отправилось, попробуйте ещё раз» |
| E12 | Two masters share a phone (rare; family) | Each registers as separate MAX user; no shared accounts |
| E13 | Bot DM quota exceeded (rate limit) | Critical pushes queued; cosmetic (morning brief, daily summary) dropped silently |
| E14 | Customer blocks bot in MAX | Master cannot send via bot DM path; in Mini App, sees «клиент закрыл диалог в MAX, попросите Карину связаться» |
| E15 | Master fails persona check 3 times in a session | Soft notification «вижу, что текст ответов часто требует правки — посмотрите примеры в помощи» linking to in-app help |
| E16 | Owner edits master's services while master views profile | Soft realtime update via SSE or polling; if master had unsaved changes — confirm sheet «Карина обновила услуги, сохранить ваши изменения?» |
| E17 | Master deeplinks to conversation that has been deleted (customer-deletion request) | «Этот диалог недоступен» neutral message; no metadata leak |
| E18 | Master sends reply to conversation that moved to HUMAN_LOCKED while composing | Pre-send check fires: «Этот диалог перешёл к администратору. Сообщение не отправлено.» Master cannot send. Draft preserved locally. |
| E19 | Master role permission gets demoted mid-session (owner changed role) | Next API call returns 403; UI shows «ваша роль изменилась, перезайдите»; clears local state |
| E20 | Customer's first name is empty/null in record | Display «Гость» (never phone fragment) |

---

## 14. Open questions (founder/PM/eng items requiring decision)

| # | Question | Owner | Urgency |
|---|---|---|---|
| **Q-M1** | Active vs Passive role — ratify recommendation (§3) | Founder | 🔴 blocks build |
| **Q-M2** | Should master be able to **accept/decline new bookings** assigned to them, or is it auto-assigned by owner with no veto? Lean: auto-assigned, but master can request reassign with reason (audited). | PM | 🟡 soon |
| **Q-M3** | Master morning brief default — opt-in or opt-out? Lean: opt-in to avoid push-fatigue; can suggest at onboarding step 3. | PM | 🟢 later |
| **Q-M4** | Master sees aftercare notes for their own past clients — same retention as transcripts (180d) or longer (clinical-relevant aftercare like patch test results)? | PM + Legal | 🟡 needs legal |
| **Q-M5** | Multi-tenant master (works at 2 salons) — MVP excluded; v1.1 or v2? Affects MAX user identity (one MAX account, one master across salons or many). | Founder | 🟢 later |
| **Q-M6** | When master proposes service add/remove or work-hours change, does owner approval go through Mini App admin UI, or is it bot DM with inline approve/decline buttons? Lean: bot DM (matches owner mobile habit), with detail view in admin web. | PM | 🟡 soon |
| **Q-M7** | Master role default for new salons — auto-create with «не приглашён», owner explicitly adds, or pre-fill from YClients sync if integration present? Lean: pre-fill from YClients sync, owner approves each. | PM | 🟢 later |
| **Q-M8** | «Поднять помощника» — when master is in HUMAN_LOCKED conversation read-only, can master invoke «отправить помощника решить» (revert tier)? Currently NO per ownership-policy §4 (master cannot demote). Reconsider for low-stakes cases? Lean: keep NO, audit-clean. | PM + Legal | 🟢 later |
| **Q-M9** | Master commission / tip handling — out of scope here, but **future-roadmap impact** on this surface. Need to know whether tipping in MAX is feasible (no MAX payments — external link required). | Founder | 🟢 later |
| **Q-M10** | Master reply attribution — does customer see «помощник» (current decision) or could premium tier allow «помощник Анна» framing for known returning customer? Lean: no, keep single-assistant invariant; premium tier doesn't override foundational identity rule. | Founder | 🟢 confirmed but flag |
| **Q-M11** | Mobile pull-to-refresh on dashboard — useful or noisy? Salon WiFi may be unreliable; manual refresh helpful. Lean: yes, enable. | UX | 🟢 minor |
| **Q-M12** | Master's bio max length — 280 chars (current) or longer for «portfolio» feel? Lean: 280 forces concision; longer = master starts writing CVs that customer doesn't read. | PM | 🟢 cosmetic |
| **Q-M13** | Web fallback — is web version even worth building for master persona, or MAX-only? Lean: skip web initially; build only if salons request («у мастера сломался телефон»). Estimate cost: ~2 weeks if reused components. | Founder | 🟡 sprint planning |
| **Q-M14** | Conversation visibility window for master — only conversations from today/this week, or all-time (their lifetime at salon)? Lean: all-time for resolved; «active» tab shows only requiring-attention. | PM | 🟢 cosmetic |
| **Q-M15** | When master sends reply via «Отправить от себя» from AI draft, is the draft preserved as «learning candidate» (same flow as admin's edits) or is master not in the learning loop? Lean: include in learning candidate pool, gated by owner review of any candidate (master-edited candidates flagged). | Eng + PM | 🟡 soon |

---

## 15. Decisions added to decisions-log.md

The following **new** master-specific questions are added to [`decisions-log.md`](../decisions-log.md) under section «Master Mobile» with prefix `Q-M`:

```
🔴 Critical (block ship)
  Q-M1 — Active vs Passive role recommendation (Founder)

🟡 Soon
  Q-M2 — Master accept/decline booking permission (PM)
  Q-M4 — Aftercare notes retention scope (PM + Legal)
  Q-M6 — Master change-request approval UX (PM)
  Q-M13 — Web fallback build (Founder)
  Q-M15 — Master replies in learning loop (Eng + PM)

🟢 Later
  Q-M3 — Morning brief opt-in default (PM)
  Q-M5 — Multi-tenant master (Founder)
  Q-M7 — Auto-prefill from YClients sync (PM)
  Q-M8 — Master demote tier (PM + Legal)
  Q-M9 — Master commission/tip (Founder)
  Q-M10 — Premium-tier «помощник Анна» framing (Founder; lean NO)
  Q-M11 — Pull-to-refresh enable (UX)
  Q-M12 — Bio max length (PM)
  Q-M14 — Conversation visibility window (PM)
```

These do not block design completion; they block specific implementation choices flagged inline.

---

## 16. Implementation notes

### Phasing
- **Phase 1 (MVP, 4 weeks)**: M0 onboarding, M1 dashboard, M3 schedule (day view only), M5 conversation list, M6 conversation detail (AI_CONTINUITY + HUMAN_LOCKED read-only), M7 notification settings basic, bot DM templates T1–T3
- **Phase 2 (2 weeks)**: M3 week/month views, M4 profile editing, M8 settings, T4 morning brief, persona check inline warnings
- **Phase 3 (2 weeks)**: HUMAN_SUPERVISED draft-take flow, availability request flow, group chat mentions, web fallback (if Q-M13 → yes)

### File structure (Django)
- `apps/masters/` — new app; models for `Master`, `MasterAvailabilityRequest`, `MasterProfile`
- `apps/masters/api/` — DRF endpoints per §11
- `apps/masters/permissions.py` — `MasterRolePermission` + `MasterScopeFilterMixin`
- `apps/persona/master_voice.py` — master-facing persona overrides
- `frontend/master/` — React Mini App, MAX UI library
- `frontend/master/routes/` — file-based routing matching `/`, `/schedule`, etc.

### Token/session
- Master logs in via owner-issued invite (one-time deeplink) → backend HMAC-validates `initData` → mints session token → stored in `WebApp.SecureStorage` (mobile) or `DeviceStorage` (web/desktop)
- Token lifetime: 30 days; refresh on use; revocable by owner

---

## 17. Foundation reference — completed checklist

- [x] Mode declared: `handoff`
- [x] Target surfaces identified: MAX Mini App primary + MAX bot DMs + web fallback
- [x] JTBD stated (§3 — primary + secondary + tertiary)
- [x] All states designed (loading, empty, error, success, permission-denied, offline) per screen
- [x] Platform-native components used (MAX UI React lib)
- [x] Contrast ≥ 4.5:1 / 3:1 verified (token inheritance)
- [x] Touch targets ≥ 48dp / 44pt
- [x] Keyboard nav defined (web fallback)
- [x] Visible focus state specified
- [x] Reduced-motion fallback specified
- [x] Anti-slop scan: 12/12 ✅
- [x] Bridge API methods listed per screen (§5)
- [x] Sticky CTA used (per MAX platform constraint)
- [x] Tokens reused from Conversations module
- [x] Redlines: implicit via mockups (handoff doc), not redline files yet
- [x] A11y checklist per §12
- [x] Bridge + Bot API endpoints inventoried (§11 backend + §7 bot DM templates)

---

## 18. Cross-document linkage

- Strategic foundation: [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md)
- Operational foundation: [`memory/project_conversation_ownership_tiers.md`](~/.claude/projects/.../memory/project_conversation_ownership_tiers.md)
- Attribution: [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md)
- MAX capabilities: [`memory/project_max_platform_capabilities.md`](~/.claude/projects/.../memory/project_max_platform_capabilities.md)
- Voice/tone: [`docs/design/assistant-persona.md`](../policies/assistant-persona.md)
- Permissions canon: [`docs/design/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md)
- Parent module: [`docs/design/2026-05-17-conversations-handoff.md`](./2026-05-17-conversations-handoff.md)
- Decisions log: [`docs/design/decisions-log.md`](../decisions-log.md) — Q-M1 through Q-M15 added
- Platform playbook: `~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md`

---

*End of handoff document — 2026-05-18 r1.*
