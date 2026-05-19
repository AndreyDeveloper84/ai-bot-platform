# Master Management — Developer Handoff Package

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Designer** | UX-architect skill |
| **Status** | Draft for review |
| **Surfaces** | Web dashboard (primary, desktop-first for table density) + MAX Mini App (parity, mobile-friendly) + MAX manager-bot DMs (invite delivery + master change-request approvals) |
| **Scope** | Owner-side UI for managing the salon's roster: add/invite, list, detail/edit, services mapping, deactivation/reactivation, audit |
| **Auth** | Role-gated per [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4 — Owner full, Admin most operations, Receptionist read-only, Master own-only |
| **Screens** | 5 (MM1 list · MM2 add modal · MM3 detail · MM4 services-master matrix · MM5 deactivation flow) |
| **Critical for** | Operational integrity — every onboarding, every customer-flow, every bot suggestion depends on a correctly populated master roster |
| **Anti-pattern** | This doc DOES NOT design schedule editing (see Schedule Management) nor master's own profile (see Master Mobile §M4) nor master analytics (see Analytics with master filter) |

---

## 1. Status of major decisions (this doc)

| # | Decision | Status |
|---|---|---|
| **A. Primary surface** | **Web dashboard** for owner/admin density; MAX Mini App parity for mobile/onboarding emergencies | Locked |
| **B. Invite mechanism** | **MAX bot-DM deeplink with magic-link token** (no password, no SMS) — mirrors onboarding consistency. Email-fallback only if MAX delivery fails | Locked |
| **C. Soft-archive semantics** | Deactivation NEVER deletes data. `is_active=False` + `archived_at` timestamp; reactivation 1-click. Audit retained per [retention policy Layer 2](../policies/conversation-ownership-policy.md#6-retention-policy--4-layer-working-model-r2) | Locked |
| **D. Photo specs** | 500×500 max 5MB JPG/PNG mirroring MAX bot avatar specs (per platform playbook Part 1). Optional — initials fallback rendered server-side | Locked |
| **E. Role assignment** | Fixed 4 roles only per OP3 (Owner/Admin/Receptionist/Master). Custom roles deferred to post-MVP | Locked per [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4 |
| **F. Services-master mapping** | Matrix UI (services × masters, checkboxes) on dedicated screen MM4 — affects bot's `show_slots` and `suggest_master` tool calls | Locked |
| **G. Bookings on deactivation** | Mandatory reassignment flow (MM5) — owner picks fallback master per booking OR mass-cancel with templated apology message. No silent orphaning | Locked |
| **H. Schedule link, not edit** | New master triggers default WorkingHours (Mon-Fri 10:00-19:00 per Q-SC1); editing happens in Schedule Management module — this doc only LINKS | Locked |

---

## 2. Foundation references

Read these before working on master management:

| Doc | Why it matters here |
|---|---|
| [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4 | Permission matrix — canonical source of who-can-do-what to master records |
| [`2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md) | The OPPOSITE side — master's own view. M0 onboarding consumes the invite issued from MM2. M4 lets master edit a subset that this doc gates. |
| [`2026-05-18-schedule-management-handoff.md`](./2026-05-18-schedule-management-handoff.md) | Where schedule editing lives. MM3 links to this; does not duplicate. |
| [`2026-05-17-salon-onboarding-handoff.md`](./2026-05-17-salon-onboarding-handoff.md) | Phase 4c Masters tab had a thin sketch — this doc replaces and expands it. |
| [`attribution-policy.md`](../policies/attribution-policy.md) | `actor_type` enum includes `master` — affects how master-created bookings (e.g. walk-ins entered by master) are billed (NEVER billable) |
| [`2026-05-18-analytics-dashboard-handoff.md`](./2026-05-18-analytics-dashboard-handoff.md) | MM3 «Эффективность» tab LINKS here with `?master_id=...` filter — not duplicated |
| `memory/project_max_platform_capabilities.md` | Invite delivery via bot DM with deeplink (no platform push); 500×500 5MB avatar spec mirrored for master photos |
| `memory/project_single_assistant_identity.md` | Master's customer-facing identity is still the single assistant; this UI is INTERNAL admin tooling only — customer never sees a «Master X joined» event |
| `~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md` | If we render any screen as Mini App parity, follow sticky-CTA + safe-area pattern |

---

## 3. Overview

### What this module is

The **owner/admin tooling for managing the salon's roster of masters**. Karina (owner) and Anya (admin) use it to:
- Add a new master and invite them via MAX
- See the current team (who's active, who's archived, who's pending invite)
- Edit master profile fields not under master's own control (role assignment, services mapping)
- Soft-archive masters when they leave, with safe handling of in-flight bookings
- Audit who did what to a master record

**Crucially, it is NOT:**
- The master's own profile editor (lives in [`master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md) §M4)
- The schedule editor (lives in [`schedule-management-handoff.md`](./2026-05-18-schedule-management-handoff.md))
- The performance dashboard (lives in [`analytics-dashboard-handoff.md`](./2026-05-18-analytics-dashboard-handoff.md) with `?master_id=...`)

This separation is load-bearing — duplicating any of those here creates two sources of truth and a guaranteed drift bug.

### Personas

| Persona | Use-case here |
|---|---|
| **Karina (Owner)** | Sets up initial roster at onboarding (Phase 4c); hires/fires; manages role assignments and service mapping |
| **Anya (Admin)** | Daily-driver: adds new junior masters, edits bios/photos, reactivates a master who came back from vacation, but cannot deactivate or assign roles |
| **New master (Анна)** | RECEIVES the invite — does NOT use this module (she uses her mobile-handoff M0 flow) |
| **Stale data observer** | Founder/CSM browsing audit log for a tenant complaining about «кто-то изменил состав мастеров»; reads MM3 audit drawer |

### JTBDs

**Primary (hiring):**
> «Когда я нанимаю нового мастера, я хочу за 5 минут добавить его в систему и быстро объяснить ему как использовать — чтобы он сразу стал получать клиентов через помощника.»

**Primary (firing/departure):**
> «Когда мастер уходит из салона, я хочу аккуратно перенести его будущие записи на других мастеров или отменить, не потерять историю — чтобы не было хаоса.»

**Secondary:**
> «Когда у мастера новые услуги или специализация, я хочу за 10 секунд отметить это в системе — чтобы бот предлагал клиентам правильно.»

> «Когда я возвращаю мастера, который уходил на 2 месяца, я хочу 1 кликом восстановить его доступ и расписание — не настраивать заново.»

> «Когда я вижу подозрительное изменение в роли или составе мастеров, я хочу за 30 сек узнать кто это сделал и когда — чтобы понять, инсайдер ли это или баг.»

### Success metrics

| Metric | Target | Type |
|---|---|---|
| **Time from MM2 «Пригласить» click → master M0 onboarding-completed** | median < 5 min (text says «5 минут» в JTBD) | Activation |
| Roster completion at Phase 4c onboarding (% salons with ≥1 master) | ≥ 95% (gate) | Activation |
| Re-invite rate (owner re-sends invite link) | < 10% (high = invite UX broken) | UX validation |
| Deactivation flow completion rate (no abandoned mid-flow) | ≥ 90% | Operational |
| Booking reassignment median latency (deactivate → all bookings handled) | < 24h | Operational |
| Audit log access on master records (admin → owner queries) | tracked, no leaks of cross-tenant master data | Safety |
| Photo upload success rate first attempt | ≥ 85% (else specs explainer broken) | UX validation |
| Service-master mapping coverage (% services with ≥1 master) | 100% (orphan service = unsuggestable) | Data quality |
| YClients pre-fill adoption (Q-M7) | ≥ 60% of YC-connected tenants accept pre-fill | Integration |

---

## 4. State machine — master record lifecycle

```
       owner clicks «Добавить мастера»
                  │
                  ▼
              ┌────────┐
              │  NEW   │ ──── drafted in MM2, not yet saved
              └────┬───┘
                   │ owner fills form + клик «Пригласить»
                   ▼
              ┌──────────┐
              │ INVITED  │ ── invite-token issued, MAX bot DM sent
              └────┬─────┘     master sees deeplink, has 7 days
                   │
        ┌──────────┼──────────┐
        │          │          │
        ▼          ▼          ▼
   token used  token   owner cancels
   in <7d    expires   invite (MM3)
        │       (7d)        │
        │          │        ▼
        │          ▼   ┌──────────┐
        │     ┌──────┐ │ CANCELLED│
        │     │EXPIRED│└──────────┘
        │     └──┬───┘   (terminal-but-restartable)
        │        │
        │        └── owner clicks «Отправить ещё раз» → re-issue token, INVITED
        ▼
   ┌─────────┐
   │ ACTIVE  │ ── master onboarded, can log in, gets bookings
   └────┬────┘
        │
        ├─── owner clicks «Деактивировать» (MM5 flow)
        │             │
        │             ▼
        │      ┌──────────┐
        │      │ INACTIVE │ ── soft-archive, login revoked,
        │      └────┬─────┘     bookings reassigned/cancelled,
        │           │           data retained, no new bookings
        │           │
        │           └── owner clicks «Восстановить» → ACTIVE
        │                  (1-click, schedule preserved)
        │
        └─── owner clicks «Удалить навсегда» (hard delete)
                      │  requires «type master's name» confirm + 30-day soft window
                      ▼
               ┌──────────┐
               │ ARCHIVED │ ── data retained per retention policy
               └──────────┘    Layer 2 audit only; PII purged from Layer 1
                               (terminal; cannot un-archive)
```

### State invariants
- **NEW** is transient (in-memory form state) — never persisted
- **INVITED** records exist server-side with `is_active=False` + `invite_token` + `invited_at` — they don't appear in customer-facing bot logic until ACTIVE
- **ACTIVE** is the only state where `BookingRequest.master_id` can be newly assigned
- **INACTIVE** masters still appear in historical bookings (read-only); cannot be assigned new bookings; their schedule is hidden from `show_slots`
- **ARCHIVED** ≠ deleted: row remains; PII fields nullified; audit trail intact
- **CANCELLED** is reachable only from INVITED (owner cancelled pending invite); the master row is then removed at next 7-day cleanup, OR owner can re-issue from CANCELLED back to INVITED within 7 days

### Audit events fired on transitions

- `master.created` (NEW → INVITED) — payload: `{master_id, invited_email_or_max_id, role, invited_by}`
- `master.invite_sent` — delivery confirmation
- `master.invite_accepted` (INVITED → ACTIVE)
- `master.invite_expired` (INVITED → EXPIRED, system)
- `master.invite_resent`
- `master.invite_cancelled` (INVITED → CANCELLED, owner action)
- `master.profile_updated` (any field change in MM3)
- `master.role_changed` (special audit — owner-only action)
- `master.services_changed` (MM4)
- `master.photo_uploaded`
- `master.deactivated` (ACTIVE → INACTIVE, includes reassignment summary)
- `master.reactivated` (INACTIVE → ACTIVE)
- `master.bookings_reassigned` (per-booking event in MM5)
- `master.bookings_cancelled` (per-booking event in MM5)
- `master.archived` (INACTIVE → ARCHIVED, hard delete)

All events carry `actor_id`, `actor_role`, `tenant_id`, `master_id`, `occurred_at`, structured payload.

---

## 5. Per-screen specs

### Screen MM1 — Masters list (roster table)

**Route (web):** `/settings/team/masters`
**Route (Mini App parity):** `/team`
**Entry points:** Settings → Команда → Мастера; deep-link from onboarding Phase 4c; from any conversation panel «открыть мастера»

#### Layout (web, desktop ≥1024px primary)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Мастера                                       [+ Добавить мастера]     │
│  Команда салона «Студия Карина»                                          │
├─────────────────────────────────────────────────────────────────────────┤
│  [Все · 5]  [Активные · 4]  [Приглашены · 1]  [Архив · 2]               │
│                                                                          │
│  Поиск по имени…              Сортировка: [Имя ▾]  Роль: [Все ▾]        │
├─────────────────────────────────────────────────────────────────────────┤
│  ☐  Мастер              Роль          Услуги    График      Статус   ⋯ │
│ ─────────────────────────────────────────────────────────────────────── │
│  ☐ [АП] Анна Петрова    Master        4 услуги  Пн-Пт       ●Active  ⋯ │
│         анна@max          Master       нейл-арт  10:00–19:00            │
│                                                                          │
│  ☐ [МС] Мария Соколова   Master        2 услуги  Пн-Сб      ●Active  ⋯ │
│         мария@max         Master       стрижки   11:00–20:00            │
│                                                                          │
│  ☐ [АН] Аня Новикова     Admin         —         Полный     ●Active  ⋯ │
│         admin@max         Admin                  доступ                  │
│                                                                          │
│  ☐ [ОВ] Ольга Васильева  Master        1 услуга  Сб-Вс      ●Active  ⋯ │
│         ольга@max         Master       массаж    10:00–18:00            │
│                                                                          │
│  ☐ [НП] Наталья Прохорова Master       3 услуги  —          ⏳Invited ⋯ │
│         (invite sent 2h ago) → ссылка истекает через 6д 22ч              │
│                                                                          │
│  ─────────────────  Архив (свернуто)  ───────────────────                │
│  ☐ [ИС] Ирина Смирнова  Master        Архив     —          ⊘Inactive ⋯ │
│         (деактивирован 12 апр 2026)                                      │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Layout (Mini App / mobile ≤480px)

Table collapses into stacked cards, one per master. Tap row → MM3.

```
┌─────────────────────────────────────┐
│  ← Мастера              [+]         │
│                                     │
│  [Все · 5] [Активные · 4] [...]     │  horizontal scroll tabs
│                                     │
│  Поиск…                             │
├─────────────────────────────────────┤
│  ┌─────────────────────────────┐    │
│  │ [АП] Анна Петрова           │    │
│  │      Master · 4 услуги      │    │
│  │      ●Active                │    │
│  └─────────────────────────────┘    │
│  ┌─────────────────────────────┐    │
│  │ [НП] Наталья Прохорова      │    │
│  │      ⏳ Приглашена 2ч назад │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
```

#### Columns + sortability (web)

| Column | Sortable | Content | Width hint |
|---|---|---|---|
| Checkbox | — | Bulk-action selector (Owner only sees; Admin hidden) | 32px |
| Avatar + name | ✓ | 40px circular avatar + full name + handle below | flex 2 |
| Роль | ✓ | Role chip (Master/Admin/Receptionist/Owner) | 120px |
| Услуги | — | «N услуг» link → opens MM4 services matrix scoped to this master | 100px |
| График | — | Brief weekly summary («Пн-Пт 10–19» or «—» for invited); click → opens Schedule Management with `?master_id=` | 140px |
| Статус | ✓ | `●Active` (green) / `⏳Invited` (yellow) / `⊘Inactive` (gray) / never «Deleted» (those don't render here) | 100px |
| `⋯` actions | — | Row-level menu (see below) | 32px |

#### Row-level actions menu (`⋯`)

| Action | Owner | Admin | Receptionist |
|---|---|---|---|
| Просмотр (MM3) | ✓ | ✓ | ✓ |
| Редактировать профиль (MM3 edit) | ✓ | ✓ | ❌ disabled |
| Услуги мастера (MM4) | ✓ | ✓ | ❌ disabled |
| График (Schedule Mgmt) | ✓ | ✓ | ❌ disabled |
| Эффективность (Analytics filtered) | ✓ | ✓ | ❌ disabled |
| Сменить роль | ✓ | ❌ disabled with tooltip «Только владелец» | ❌ |
| Отправить ещё раз приглашение (INVITED only) | ✓ | ✓ | ❌ |
| Отменить приглашение (INVITED only) | ✓ | ✓ | ❌ |
| Деактивировать (ACTIVE only) | ✓ | ❌ disabled | ❌ |
| Восстановить (INACTIVE only) | ✓ | ❌ disabled | ❌ |
| Удалить навсегда (INACTIVE only) | ✓ destructive | ❌ | ❌ |

Disabled items render dimmed with tooltip explaining required role. Hiding them entirely would hide capability discoverability.

#### Tabs and counts

- **Все** — every master in any state except hard-archived
- **Активные** — `is_active=True AND invite_status=accepted`
- **Приглашены** — `invite_status=pending` (INVITED state)
- **Архив** — `is_active=False` (INACTIVE) + ARCHIVED (rendered in subsection «Удалены навсегда» if owner)

#### Bulk actions (Owner only, when ≥2 rows selected)

- Сменить роль (only valid if all selected are Master role currently)
- Деактивировать (confirmation modal lists all selected; runs MM5 flow per master sequentially)
- Удалить навсегда (only if all selected are already INACTIVE)
- Экспортировать данные мастера(ов) — JSON download per OP5

Bulk actions are rare in practice (small salon = 3-7 masters) but supported for tenant-merge scenarios and for CSM tools.

#### States

- **Empty** (zero masters): full-page CTA «Добавьте первого мастера — это нужно, чтобы помощник предлагал клиентам запись». Button big, single CTA, no clutter. Reuses Phase 4c onboarding empty illustration.
- **Loading**: skeleton rows × 5; tabs render with `…` count.
- **Error fetching**: «Не удалось загрузить команду. [Повторить]». Retry button. Errors NEVER swallow data — show last-cached if available with «обновлено N мин назад» footer.
- **Partial permission** (Receptionist): same table, no `+ Добавить мастера` button, `⋯` shows only «Просмотр».
- **Search empty**: «Не нашлось мастера с именем «X». [Очистить поиск]».
- **Offline**: read-cached; banner «Нет связи. Изменения не сохраняются». Add button disabled.

#### Bridge / web specifics

Web: standard responsive table, sticky header on scroll, virtual-scroll if >50 rows.
Mini App: MAX UI `CellList` + `CellSimple` for stacked rows; `BackButton` shown; sticky bottom `[+ Добавить мастера]` button (FAB pattern is anti-MAX — use sticky CTA bar instead per platform playbook).

#### Audit triggers
- `master.list_viewed` — only on filter/search actions, not on every render (avoid noise)
- `master.row_action_attempted` — for permission-denied attempts (helps detect role-confusion bugs)

---

### Screen MM2 — Add new master modal (invite flow)

**Trigger:** «+ Добавить мастера» button on MM1, OR onboarding Phase 4c «Добавить мастера» button.

**Surface:** Modal on web (centered, ~520px wide). On Mini App: full-screen sheet with `BackButton`.

#### Flow (single screen, two-mode)

```
┌─────────────────────────────────────────────────────────┐
│  Новый мастер                                    [✕]    │
│                                                          │
│  Как добавить?                                          │
│  ┌──────────────────┐  ┌──────────────────┐             │
│  │ ◉ Пригласить     │  │ ○ Без приглашения│             │
│  │   через MAX      │  │   (только запись │             │
│  │                  │  │   в каталоге)    │             │
│  └──────────────────┘  └──────────────────┘             │
│                                                          │
│  ── вариант «Пригласить через MAX» ──                   │
│                                                          │
│  Имя и фамилия *                                        │
│  ┌────────────────────────────────────┐                 │
│  │ Анна Петрова                       │                 │
│  └────────────────────────────────────┘                 │
│                                                          │
│  Контакт для приглашения *                              │
│  ◉ MAX-аккаунт (username или phone)                     │
│      ┌────────────────────────────────┐                 │
│      │ @anna_styl                     │                 │
│      └────────────────────────────────┘                 │
│  ○ Email (если MAX не работает)                         │
│                                                          │
│  Роль *                                                 │
│  ◉ Мастер (видит только своих клиентов)                 │
│  ○ Администратор (видит всех)        ← только Owner     │
│  ○ Ресепшен (видит, не редактирует)  ← только Owner     │
│                                                          │
│  Услуги (можно указать сразу или потом)                 │
│  ☐ Маникюр  ☐ Гель-лак  ☐ Стрижка  ☐ Окрашивание...    │
│  [Показать все услуги (12) →]                           │
│                                                          │
│  График (применится автоматически — можно изменить)     │
│   Пн-Пт 10:00–19:00, Сб-Вс выходной                     │
│  [Изменить график →]   (открывает Schedule Management)  │
│                                                          │
│  ┌─ Что произойдёт ───────────────────────┐             │
│  │ 1. Анна получит сообщение в MAX от     │             │
│  │    помощника салона                    │             │
│  │ 2. Откроет ссылку → подтвердит профиль │             │
│  │ 3. Сможет видеть свой график и         │             │
│  │    клиентов                            │             │
│  │ Ссылка действительна 7 дней.           │             │
│  └────────────────────────────────────────┘             │
│                                                          │
│  ─────────────────────────────────────────              │
│                                                          │
│  [Отмена]                       [Пригласить →]          │
└─────────────────────────────────────────────────────────┘
```

#### Two-mode rationale

- **Пригласить через MAX**: standard path. Master gets login, sees own dashboard, replies to clients. Required for ACTIVE role with conversation access.
- **Без приглашения (catalog-only)**: special case — owner adds a master who exists in catalog (for booking) but doesn't use the app. Used for: legacy salon staff who refuse smartphones, freelance masters on temporary contract, YClients pre-fill where master is in YC system but not yet given MAX access. They appear in services-mapping, schedule, and can have bookings assigned, but cannot log in or reply. Owner can later promote to invited mode via MM3.

#### Field validation

| Field | Validation |
|---|---|
| Имя и фамилия | Required; 2–80 chars; allows кириллица + Latin + spaces + `-` `'` |
| Контакт (MAX) | Required if mode=invite; regex `@[a-z0-9_]{3,40}` OR `+7\d{10}` phone; backend lookup confirms account exists before showing «Пригласить» button (anti-typo) |
| Контакт (Email) | RFC 5322 + deliverability check |
| Роль | Required; default = Master; admin/receptionist gated to Owner-only |
| Услуги | Optional at create; can be done in MM4 later |

#### YClients pre-fill (per Q-M7)

If tenant is YC-connected:

```
┌─────────────────────────────────────────────────────────┐
│  Новый мастер                                    [✕]    │
│                                                          │
│  📋 Загрузить из YClients?                              │
│  В вашем YClients найдено 3 мастера, которых ещё        │
│  нет в платформе:                                       │
│  ☑ Анна Петрова (Мастер · маникюр, гель-лак)            │
│  ☐ Иван Сидоров (Мастер · стрижки)                      │
│  ☑ Ольга Ким (Мастер · окрашивание)                     │
│                                                          │
│  [Создать 2 мастеров из YClients] [Добавить вручную ↓]  │
└─────────────────────────────────────────────────────────┘
```

Pre-filled fields: name, services (mapped via YC→catalog), schedule (from YC working_hours). Owner still confirms each and chooses invite vs catalog-only. Tracked per Q-M7 acceptance metric.

#### CTA states

- Default: «Пригласить →» enabled when all required fields valid AND contact lookup succeeded
- Loading (server lookup): «Проверяю аккаунт…» spinner inline next to field, button disabled
- Submitting: «Отправляю приглашение…» button disabled, full modal in busy state, `enableClosingConfirmation()` on Mini App
- Submitted success: modal swaps to confirmation state (see below)
- Error: inline field error OR top-of-modal banner for systemic errors («MAX не отвечает — попробуйте позже»)

#### Confirmation state (after successful submit)

```
┌─────────────────────────────────────────────────────────┐
│                                                          │
│            ✓  Приглашение отправлено                    │
│                                                          │
│   Анна получит сообщение от помощника салона в          │
│   MAX в течение минуты.                                 │
│                                                          │
│   Ссылка действительна до 25 мая 2026 (через 7 дней).   │
│                                                          │
│   [Открыть профиль Анны]   [Добавить ещё мастера]       │
│   [Готово]                                              │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

«Добавить ещё мастера» loops back to empty form — common during onboarding Phase 4c when owner is adding 3-5 masters at once.

#### Bridge / web specifics

- Web: standard modal with focus-trap, Esc closes (with confirm if dirty), Cmd+Enter submits
- Mini App: full-screen sheet, `BackButton` ↔ close, `enableClosingConfirmation()` when dirty; haptic `notificationOccurred('success')` on submit; `BackButton` confirmation match desktop Esc
- Photo upload is NOT here (added in MM3 detail by master via M0 OR by owner editing MM3 later) — keeps MM2 fast

#### Backend contract

```
POST /api/v1/masters/invite
Headers: Authorization: Bearer <token>
Body: {
  "name": "Анна Петрова",
  "contact_method": "max_username" | "max_phone" | "email",
  "contact_value": "@anna_styl",
  "role": "master" | "admin" | "receptionist",
  "services": [12, 18, 23],          // service IDs, optional
  "schedule_preset": "default_mon_fri_10_19" | "custom" | "none",
  "mode": "invite" | "catalog_only"
}

Response 201:
{
  "master_id": "uuid",
  "invite_token": "uuid",            // for owner UI to render «copy invite link» fallback
  "invite_expires_at": "2026-05-25T17:00:00Z",
  "max_dm_delivery": "queued" | "delivered" | "failed",
  "fallback_link": "https://salon.app/invite/<token>"  // for sharing if MAX fails
}
```

Side effects on success:
- `Master` row created (`is_active=False`, `invite_status=pending`)
- Default `WorkingHours` rows seeded (10:00–19:00 Mon-Fri per Q-SC1, or per `schedule_preset`)
- Default `MasterService` mapping rows (from `services[]` array)
- MAX bot DM dispatched to invitee with deeplink `max://bot/<salon_bot>?start=master_invite_<token>`
- Audit event `master.created` + `master.invite_sent`

---

### Screen MM3 — Master detail / edit

**Route (web):** `/settings/team/masters/:id`
**Route (Mini App):** `/team/:id`
**Entry:** Click row on MM1 OR deep-link from audit log OR from any conversation's master sidebar.

#### Layout (web)

Two-column desktop; single-column stacked on mobile.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ← Мастера / Анна Петрова                            ●Active   [⋯]      │
├──────────────────────────────┬──────────────────────────────────────────┤
│  Профиль                      │  Услуги                                  │
│                               │                                          │
│  ┌──────┐                     │  Эта мастер выполняет:                   │
│  │ [фото] АП                  │  ✓ Маникюр                               │
│  └──────┘ [Загрузить]         │  ✓ Гель-лак                              │
│           500×500 max 5MB     │  ✓ Наращивание ногтей                    │
│           [Удалить фото]      │  ✓ Дизайн                                │
│                               │  [Редактировать (4 из 12) →]             │
│  Имя                          │      (открывает MM4 со scope=master)     │
│  ┌────────────────────┐       │                                          │
│  │ Анна Петрова       │       │  График                                  │
│  └────────────────────┘       │  Пн-Пт 10:00–19:00                       │
│                               │  Сб-Вс выходной                          │
│  Роль                         │  [Редактировать график →]                │
│  ◉ Master  ○ Admin  ○ Recep   │      (открывает Schedule Management)     │
│  (только Owner)               │                                          │
│                               │  Эффективность                           │
│  Контакт MAX                  │  За май: 47 записей, 26 410 ₽            │
│  @anna_styl  (verified)       │  через помощника: 18 (38%)               │
│  [Сменить →]                  │  [Открыть аналитику →]                   │
│                               │      (Analytics ?master_id=…)            │
│  О себе (видят клиенты)       │                                          │
│  «5 лет в нейл-арте,          │  Уведомления                             │
│   специализация на френче.»   │  Получает push в MAX:                    │
│  [Редактировать →]            │  ☑ Новая запись                          │
│  280 символов max             │  ☑ Отмена                                │
│                               │  ☐ Сообщение клиента (если не отвечает)  │
│  Статус                       │  (мастер настраивает сам в своём app)    │
│  ●Active с 14 янв 2026        │                                          │
│  [Деактивировать →]           │                                          │
│      (только Owner; → MM5)    │                                          │
├──────────────────────────────┴──────────────────────────────────────────┤
│  Журнал изменений (последние 10)                                         │
│  • 16 мая 14:22 — Аня изменила услуги (+ «Дизайн»)                      │
│  • 14 мая 11:08 — Анна обновила био                                      │
│  • 14 янв 09:30 — Карина пригласила, Анна приняла в тот же день         │
│  [Показать всё (24) →]                                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Edit semantics

Most fields are **inline-editable** with click-to-edit pattern:
- Click name → inline input with Save/Cancel
- Click bio → expand to textarea with char counter
- Photo upload → opens drag-and-drop area (web) or `[Загрузить]` opens file picker (Mini App via `WebApp.downloadFile` adjacent — wait, that's download; for upload Mini App must fall back to plain `<input type=file>`, which works on MAX since it's a webview)

**Role change** is destructive-feel — requires confirm dialog «Сменить роль Анны с Master на Admin? Доступ изменится при следующем входе.» + audit event.

**Contact change** (MAX handle) — high-risk: changing this could lock the master out OR redirect to wrong account. Pattern: «Сменить MAX-аккаунт» → re-sends invite to new contact → master must re-accept; old contact gets «ваш доступ передан новому аккаунту, если это не вы — обратитесь к Карине». Audited specially.

#### Permissions per field

| Field | Owner | Admin | Receptionist | Master (self, per master-mobile §M4) |
|---|---|---|---|---|
| Photo | ✓ | ✓ | view-only | ✓ (own) |
| Name | ✓ | ✓ | view-only | ✓ (own, but rename audit-flagged) |
| Role | ✓ | view-only | view-only | view-only |
| Contact (MAX/email) | ✓ | ✓ | view-only | view-only (admin manages) |
| Bio | ✓ | ✓ | view-only | ✓ (own) |
| Services (link to MM4) | ✓ | ✓ | view-only | request-only |
| Schedule (link to Schedule Mgmt) | ✓ | ✓ | view-only | request-only |
| Notifications | view-only (master controls own) | view-only | view-only | ✓ (own) |
| Deactivate | ✓ | ❌ | ❌ | ❌ |
| Audit log | ✓ full | ✓ own actions | ✓ own actions | ❌ |

#### States

- **Loading**: skeleton on all sections
- **Empty profile** (master accepted invite but hasn't filled bio/photo): show «Анна ещё не заполнила био и фото. [Напомнить →]» — sends bot DM nudge
- **Pending invite** (INVITED state): replaces left column with invite status card («Приглашение отправлено 16 мая, истекает через 6 дней. [Отправить ещё раз] [Отменить]»), right column shows what owner pre-set (services, schedule) with «применится после принятия» tag
- **Inactive**: header banner «Этот мастер временно неактивен. Можно вернуть в любой момент. [Восстановить]»; all edit controls hide, replaced with read-only view
- **Permission denied for current actor**: render read-only view with locked-padlock icons on disabled controls and tooltip «Доступно владельцу»
- **Save error**: inline field error with retry; no silent data loss
- **Photo upload fail (too big)**: clear error «Фото больше 5 МБ. Уменьшите размер.» + link to compression web tool (out of scope here)
- **Concurrent edit conflict**: if another admin saved in last 5s, show «Аня только что обновила. Перезагрузить?» banner

#### Mini App parity

Same content, single-column stack. Sticky bottom bar with `[Сохранить]` only when dirty. `BackButton` wired with closing-confirmation. Haptics: `selectionChanged()` on role radios, `notificationOccurred('success')` on save.

---

### Screen MM4 — Services-master mapping matrix

**Route (web):** `/settings/team/services-mapping`
**Route (Mini App):** `/team/services`
**Entry:** From Settings → Услуги → «Кто выполняет», OR from MM3 «Редактировать услуги», OR from MM1 row «N услуг».

#### Purpose

Drive bot's `suggest_master` and `show_slots` logic: when customer asks for «маникюр», bot needs to know **which masters perform it**. Orphan service (no master) = bot apologizes + recommends handoff. Orphan master (no services) = invisible in bot's `suggest_master` output.

#### Layout (web — primary, matrix style)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ← Услуги ⇄ Мастера                                                     │
│  Кто какие услуги выполняет в салоне                                    │
│                                                                          │
│  Фильтр: [Все услуги ▾]  Категория: [Все ▾]  Поиск услуги…              │
├─────────────────────────────────────────────────────────────────────────┤
│                          Анна  Мария  Ольга  Наталья                    │
│                          (М)   (М)    (М)    (приглаш.)                 │
│ ──────────────────────────────────────────────────────────────          │
│  Маникюр (60 мин, 1500₽)  ☑    ☑     ☐     ☐                            │
│  Гель-лак (90 мин, 2200₽) ☑    ☐     ☐     ☐                            │
│  Наращ. ногтей (120, 3500)☑    ☐     ☐     ☐                            │
│  Дизайн (15, 300₽)        ☑    ☑     ☐     ☐                            │
│  Стрижка ж. (45, 2000₽)   ☐    ☑     ☐     ☐                            │
│  Окрашивание (180, 6000)  ☐    ☐     ☐     ☑                            │
│  Массаж лица (60, 2500)   ☐    ☐     ☑     ☐                            │
│                                                                          │
│  Услуги без мастеров (1):                                                │
│  ⚠ Депиляция воском — никто не выполняет.                                │
│    [Назначить мастера] [Удалить услугу]                                  │
│                                                                          │
│  Мастера без услуг (1):                                                  │
│  ⚠ Наталья Прохорова — не выполняет ни одной услуги.                    │
│    Пока не выберете услуги, помощник не сможет её предлагать.            │
│    [Открыть профиль]                                                     │
│                                                                          │
│  Изменено: 3 ячейки   [Отменить]   [Сохранить]                          │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Interaction

- Click cell → toggle. Haptic `selectionChanged()` on Mini App. No save dialog per cell — batch-save at footer.
- Dirty cells highlight (subtle warm background). «Отменить» reverts. «Сохранить» commits all in one transaction.
- Shift+click cell extends selection (web only).
- Header click on master column → bulk-toggle column («select all services for Анна»).
- Header click on service row → bulk-toggle row («все мастера выполняют маникюр»).

#### States

- **No services in catalog yet**: empty matrix with «Сначала добавьте услуги в [Каталог →]».
- **No masters yet**: «Сначала добавьте мастеров в [Команду →]».
- **Loading**: skeleton matrix.
- **Save in progress**: «Сохраняю…» + footer disabled.
- **Conflict on save** (another admin changed cells): «Аня изменила 2 ячейки одновременно. [Объединить изменения] [Открыть заново]».
- **Save success**: toast «Сохранено» + dirty highlights clear. Haptic success.
- **Orphan warnings** (top of footer): «Услуги без мастеров» + «Мастера без услуг» always rendered when present — these are critical for bot quality.

#### Mini App layout (mobile)

Matrix becomes per-master cards (master picker top, then service list with checkboxes). Save at sticky bottom. Less efficient but mobile-functional. Owner is encouraged to do this on desktop; explainer text «Удобнее на компьютере → [скопировать ссылку]».

#### Backend contract

```
GET /api/v1/services-mapping
Response: {
  "services": [{"id": 12, "name": "Маникюр", "duration_min": 60, "price": 1500}, ...],
  "masters": [{"id": "uuid", "name": "Анна", "is_active": true}, ...],
  "mapping": [{"service_id": 12, "master_id": "uuid"}, ...]
}

POST /api/v1/services-mapping/bulk
Body: {
  "changes": [
    {"service_id": 12, "master_id": "uuid", "enabled": true},
    {"service_id": 18, "master_id": "uuid", "enabled": false}
  ]
}
Response 200: { "applied": 2, "conflicts": [] }
Response 409: { "applied": 0, "conflicts": [{"service_id": 12, "master_id": "uuid", "current_value": true, "your_value": false, "changed_by": "anya_id", "changed_at": "..."}] }
```

Audit: one `master.services_changed` event per master with diff payload.

---

### Screen MM5 — Deactivation flow (with bookings reassignment)

**Trigger:** Owner clicks «Деактивировать» on MM3 OR MM1 row menu.

**Surface:** Full-screen step-flow on web (not a modal — it's procedural). On Mini App: same flow as full-screen sheets.

#### Step 1 — Confirm intent + future bookings inventory

```
┌─────────────────────────────────────────────────────────┐
│  ← Назад                                                 │
│                                                          │
│  Деактивировать Анну Петрову?                           │
│                                                          │
│  Анна станет неактивна — войти в систему не сможет,     │
│  новых записей помощник к ней не предложит.             │
│                                                          │
│  Все данные сохранятся. Можно вернуть в любой момент    │
│  одним кликом.                                          │
│                                                          │
│  ┌─ Будущие записи Анны ──────────────────────┐         │
│  │ У Анны 7 будущих записей с 21 мая по       │         │
│  │ 12 июня. Их нужно переназначить или        │         │
│  │ отменить — иначе клиенты придут на         │         │
│  │ закрытую дверь.                            │         │
│  │                                            │         │
│  │ [Посмотреть и переназначить →]             │         │
│  └────────────────────────────────────────────┘         │
│                                                          │
│  ── ИЛИ —— если нет будущих записей: ─────              │
│  ┌────────────────────────────────────────────┐         │
│  │ У Анны нет будущих записей. ✓              │         │
│  │ Можно деактивировать без переноса.         │         │
│  └────────────────────────────────────────────┘         │
│                                                          │
│  [Отмена]   [Продолжить →]                              │
└─────────────────────────────────────────────────────────┘
```

#### Step 2 — Bookings reassignment

```
┌─────────────────────────────────────────────────────────┐
│  ← Назад · Шаг 2 из 3                                   │
│                                                          │
│  Что делать с 7 будущими записями Анны?                 │
│                                                          │
│  Применить ко всем:  [Перенести на Марию ▾] [Применить] │
│              ИЛИ выбрать по одной ↓                     │
│                                                          │
│  ─────────────────────────────────────────              │
│  21 мая 14:30 · Маникюр гель-лак · Мария И.             │
│  ┌─────────────────────────────────────────┐            │
│  │ Перенести на: [Мария Соколова ▾]         │           │
│  │ ✓ Мария тоже выполняет «маникюр гель-лак»│           │
│  │ ✓ Мария свободна 21 мая 14:30            │           │
│  │   Будет уведомление клиенту от помощника │           │
│  │   с новым именем мастера.                │           │
│  │ ○ Отменить запись                        │           │
│  │   Клиенту отправится извинение + предлож.│           │
│  │   новый слот.                            │           │
│  └─────────────────────────────────────────┘            │
│                                                          │
│  22 мая 10:00 · Наращивание ногтей · Елена П.           │
│  ┌─────────────────────────────────────────┐            │
│  │ Перенести на: [Никого нет ⚠]            │            │
│  │   Никто из мастеров не выполняет        │            │
│  │   «Наращивание ногтей».                 │            │
│  │ ○ Отменить запись (рекомендуется)       │            │
│  └─────────────────────────────────────────┘            │
│                                                          │
│  …ещё 5 записей                                          │
│                                                          │
│  Сводка: 5 переносим · 2 отменяем · 0 не решено         │
│                                                          │
│  [Назад]                          [Продолжить →]        │
│  кнопка неактивна, пока не решено все 7                 │
└─────────────────────────────────────────────────────────┘
```

#### Step 3 — Final confirm + preview customer notifications

```
┌─────────────────────────────────────────────────────────┐
│  ← Назад · Шаг 3 из 3                                   │
│                                                          │
│  Подтвердите изменения                                  │
│                                                          │
│  Анна Петрова будет деактивирована.                     │
│  Из 7 будущих записей:                                  │
│   • 5 перенесём на Марию                                │
│   • 2 отменим                                           │
│                                                          │
│  Что увидят клиенты (предпросмотр сообщения):           │
│  ┌─────────────────────────────────────────┐            │
│  │ От: Помощник Студии Карина              │            │
│  │                                          │           │
│  │ Здравствуйте, Мария! По вашей записи    │            │
│  │ на 21 мая 14:30 — Анна, к сожалению,    │            │
│  │ больше не работает в студии. Вашу       │            │
│  │ запись переведём к Марии Соколовой —    │            │
│  │ она тоже делает гель-лак, отзывы        │            │
│  │ отличные.                               │            │
│  │ Если так не подходит — напишите, я      │            │
│  │ предложу другие варианты. 🙏            │            │
│  └─────────────────────────────────────────┘            │
│  [Изменить текст]                                       │
│                                                          │
│  ☑ Уведомить переназначенных мастеров (Марию)           │
│  ☑ Записать причину в журнал                            │
│  ┌─────────────────────────────────────────┐            │
│  │ Причина (необязательно)                 │            │
│  │ ┌─────────────────────────────────┐     │            │
│  │ │ Уход с работы                   │     │            │
│  │ └─────────────────────────────────┘     │            │
│  └─────────────────────────────────────────┘            │
│                                                          │
│  [Назад]               [Деактивировать Анну]            │
│                        red destructive style             │
└─────────────────────────────────────────────────────────┘
```

#### Step 4 — Result

```
┌─────────────────────────────────────────────────────────┐
│                                                          │
│              ✓  Анна деактивирована                     │
│                                                          │
│   • 5 записей переведено на Марию                       │
│   • 2 записи отменены с уведомлением                    │
│   • Анна больше не получает push                        │
│                                                          │
│   Все данные сохранены. Можно вернуть из архива.        │
│                                                          │
│   [Открыть архив команды]   [Вернуться к мастерам]      │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

#### Reactivation flow (INACTIVE → ACTIVE)

Simpler — single confirm dialog:

```
┌─────────────────────────────────────────────────────────┐
│  Восстановить Анну Петрову?                             │
│                                                          │
│  Анна снова сможет войти в систему и получать клиентов. │
│  Её график восстановится в том виде, в котором был      │
│  до деактивации (Пн-Пт 10:00–19:00).                    │
│                                                          │
│  Её услуги: маникюр, гель-лак, наращивание, дизайн.     │
│                                                          │
│  ☑ Отправить Анне сообщение «Вы снова активны»          │
│                                                          │
│  [Отмена]                       [Восстановить]          │
└─────────────────────────────────────────────────────────┘
```

#### States

- **Loading bookings inventory**: spinner «Считаю будущие записи Анны…»
- **No future bookings**: skip Step 2, go straight to Step 3 with «Будущих записей нет» message
- **All future bookings handled**: Step 2 footer enables «Продолжить»
- **No fallback master for some services**: warning chip per row + forces «Отменить»; cannot proceed without resolving each
- **Customer notification draft fail** (persona check): «Текст уведомления требует проверки. [Открыть для редактирования]»
- **Mid-flow abandonment**: state preserved 30 min as draft; banner on MM3 «Незавершённая деактивация — продолжить?»
- **Server error mid-execution**: idempotent retry; partial completion shown with «Переведено 3 из 5, повторить?»
- **Race**: if Anna's row gets a NEW booking after Step 1 inventory (another admin created one), Step 3 final-check refreshes and shows «появилась новая запись — обновить список?»

#### Audit events
- `master.deactivation_started`
- `master.bookings_reassigned` (per booking, includes from→to master, customer notification message_hash)
- `master.bookings_cancelled` (per booking, includes customer notification)
- `master.deactivated` (terminal — includes summary counts and reason)
- `master.reactivated`

---

## 6. Cross-screen integration

### Onboarding Phase 4c → MM1/MM2

Phase 4c Masters tab (currently a thin sketch) becomes a wrapper: it embeds MM1's empty-state and MM2's modal directly. Onboarding flow has explicit gate: «Минимум 1 мастер активен» — can't progress to Phase 5 with empty roster. Owner can defer adding the rest of the team via «Закончить позже».

### Schedule Management ← MM3 «Редактировать график»

MM3 contains no inline schedule editing. Button opens `/settings/schedule?master_id=<id>` in Schedule Management module. On Mini App: opens same route within Mini App (Schedule Management has Mini App parity). Master mobile §M3 has equivalent for own schedule.

### Analytics ← MM3 «Открыть аналитику»

Per-master KPIs (revenue contribution, NPS slice, AI-attribution split per [`attribution-policy.md`](../policies/attribution-policy.md) §10) live in Analytics dashboard with master filter. MM3 surfaces a 2-line summary card («За май: N записей, M ₽»), then links out. Never duplicate analytics computations here.

### Customer first-time ← Service-master mapping

When a new customer messages bot with «хочу к Анне» (master mention), bot consults MM4 mapping to verify Анна performs the asked service. If Анна doesn't perform it → bot offers alternative master from MM4. If service has zero masters mapped → bot apologizes + handoff. MM4's orphan-warnings exist exactly to prevent this state.

### Master mobile §M0 ← MM2 invite

MM2 issues invite-token. Master receives MAX bot DM with deeplink `max://bot/<bot>?start=master_invite_<token>`. Master taps → opens Mini App at `/onboarding/master?token=<token>` (master-mobile §M0). Token is HMAC-verified server-side via `initData`. On accept, master's row transitions INVITED → ACTIVE.

### Master mobile §M4 ↔ MM3 edit

The fields master can edit on their own (per §M4): photo, bio, services-they-claim (request-mode). Owner edits via MM3 take precedence — last-write-wins with audit. Services proposals from master arrive in MM4 as a «1 предложение от Анны» chip (out of MVP scope — backlog item).

### Conversations module ← Master record state

Conversation sidebar shows assigned master from `BookingRequest.master_id`. If master is INACTIVE, sidebar shows «(деактивирован)» tag + link to MM3 archive view. AI suggesting masters in bot uses only ACTIVE masters per §3.

### Audit log ← All MM events

Settings → Аудит (Owner only, OP5) consumes all `master.*` events. MM3's «Журнал изменений» card is a filtered view of the same stream scoped to one master_id.

---

## 7. MAX-specific patterns

### Invite delivery via bot DM (Pattern 8 from playbook)

```
[salon bot] → master's MAX
─────────────────────────────────────────────
Анна, здравствуйте!

Карина из Студии Карина пригласила вас в
рабочий помощник студии. Здесь вы увидите
свой график и сможете отвечать клиентам.

Откройте, чтобы подтвердить:
[open_app] Открыть помощник Студии
     ↑ button launches Mini App with
       start_param=master_invite_<token>

Ссылка действительна 7 дней.
Если это ошибка — напишите Карине.
─────────────────────────────────────────────
```

If MAX delivery fails (master blocked the bot, or wrong handle), MM2 surfaces fallback:
- Inline error on owner UI: «MAX не доставил — может быть @anna_styl не существует или заблокировал нашего бота. [Скопировать ссылку для другого канала]»
- Owner can manually share the magic-link URL via WhatsApp/SMS/etc.

### Group chat mention pattern

When owner appoints master to a role that affects team chat (Admin), `[Анна](max://user/123) теперь администратор — может видеть все диалоги и редактировать каталог.» is auto-posted in salon team chat (per playbook Pattern 10). Optional, owner can disable.

### Mini App parity rendering

If MM1/MM3 is opened from MAX bot DM (e.g., owner on phone), Mini App uses MAX UI lib:
- `CellList` + `CellSimple` for roster rows
- `Avatar.Image` / `Avatar.Text` for master avatars (with initials fallback)
- `Switch` for active/inactive toggle (Owner-only)
- `Button` with sticky-CTA bar for primary action
- `Typography.Title` / `Body` for headers
- Haptics on every meaningful action per playbook Pattern 4

### No platform push for «invite accepted»

Owner learns invite was accepted via:
1. MAX bot DM to owner: «Анна приняла приглашение и заполнила профиль. [Открыть Анну]»
2. Real-time push inside dashboard if open (WebSocket update on MM1 row badge)

No external push beyond bot DMs (per playbook Part 8).

### Avatar specs alignment

Master photo upload mirrors MAX bot avatar specs:
- 500×500 max
- 5MB max
- JPG/PNG only
- Square crop assistance UI on upload
- Server-side validation rejects oversize with helpful error («Уменьшите до 500×500 — [инструкция]»)
- Initials fallback rendered when no photo (e.g., «АП») using same algorithm everywhere (MM1 row, MM3 detail, Master Mobile, customer-facing — but customer never sees individual master photo per single-assistant identity)

---

## 8. Backend contracts (consolidated)

```
# List masters
GET /api/v1/masters?status=active|invited|inactive|all&q=<search>
Response: { "masters": [...], "counts": {"active": 4, "invited": 1, "inactive": 2} }

# Detail
GET /api/v1/masters/:id
Response: { master fields + service_ids + recent_audit_events[:10] }

# Invite (MM2)
POST /api/v1/masters/invite
(see MM2 §)

# Update (MM3)
PATCH /api/v1/masters/:id
Body: any subset of {name, bio, role, photo_id, max_handle, notification_prefs}
Response 200 + updated row OR 409 conflict with current_value diff

# Change role (special audited endpoint)
POST /api/v1/masters/:id/change-role
Body: { "new_role": "admin", "reason": "..." }

# Re-send invite
POST /api/v1/masters/:id/resend-invite

# Cancel invite
POST /api/v1/masters/:id/cancel-invite

# Photo upload (separate endpoint to handle multipart)
POST /api/v1/masters/:id/photo
Body: multipart/form-data file
Validation: ≤5MB, ≥500×500, JPG/PNG; server resizes/crops to 500×500 square

# Delete photo
DELETE /api/v1/masters/:id/photo

# Services mapping (MM4)
GET /api/v1/services-mapping
POST /api/v1/services-mapping/bulk
(see MM4 §)

# Deactivation flow (MM5)
GET /api/v1/masters/:id/future-bookings        # for Step 2 inventory
POST /api/v1/masters/:id/deactivate
Body: {
  "reason": "...",
  "reassignments": [
    {"booking_id": "uuid", "action": "reassign_to", "target_master_id": "uuid"},
    {"booking_id": "uuid", "action": "cancel"}
  ],
  "customer_notification_template_overrides": {...},
  "notify_target_masters": true
}
Response 200: { "deactivated": true, "reassigned": 5, "cancelled": 2, "audit_event_ids": [...] }
Response 409: { "error": "new_booking_appeared", "new_booking_id": "...", "refresh_required": true }

# Reactivate
POST /api/v1/masters/:id/reactivate
Body: { "notify_master": true }

# Hard archive (last resort)
POST /api/v1/masters/:id/archive
Body: { "confirmation_name": "Анна Петрова", "reason": "..." }
Response 200 OR 400 if confirmation_name mismatch

# Audit feed
GET /api/v1/masters/:id/audit?cursor=<>&limit=20
```

### Server-side authorization

All endpoints check `role` + capability from [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4. Server returns 403 with `{"error": "role_capability_missing", "required": "Owner", "current": "Admin"}` for client to render proper UI message (no silent failures).

### Tenant scoping

All endpoints implicit-scoped to `tenant_id` from session token. Cross-tenant master access impossible by construction — even a bug in client filter cannot exfiltrate. Server validates `master.tenant_id == session.tenant_id` on every lookup.

---

## 9. Accessibility (WCAG 2.2 AA)

### General
- Contrast ≥ 4.5:1 body / 3:1 large per skill hard-rule 3
- All interactive elements keyboard-reachable; visible focus ring (2px outline at brand-primary)
- Touch targets ≥ 44pt iOS / 48dp Android / 24×24 CSS px web
- Screen-reader labels on every icon-only button («⋯» = «действия для строки Анна Петрова»)
- Color is never the only signifier of state — status uses dot+text together
- `aria-live="polite"` for save-success toasts; `assertive` for destructive confirms

### Per screen
- **MM1 table**: keyboard nav with arrow keys, Enter opens row, Space toggles checkbox, `aria-sort` on sortable headers
- **MM2 modal**: focus moves to first input on open; focus-trap; Esc closes (with confirm if dirty); on close, focus returns to «+ Добавить мастера» button
- **MM3 inline-edit**: Edit pencil icon has accessible name; saving announces «Сохранено» to SR
- **MM4 matrix**: every cell has `aria-label` like «Анна выполняет маникюр, включено» / «выключено»; Tab moves cell-to-cell; Space toggles
- **MM5 destructive**: type-to-confirm pattern for hard-archive (must type «Анна Петрова» exactly — bypassed for keyboard SR users with explicit «Я понимаю, удалить» checkbox)

### Reduced motion
- All animations (modal entry, dirty-row pulse, success toast slide) have `prefers-reduced-motion: reduce` fallback — fade only or instant
- No animation that conveys meaning (e.g., status changes) — meaning carried in state-text + color

### Internationalization (post-MVP)
- All strings in `apps/masters/locale/` per i18n pattern
- Names handle Cyrillic + Latin + diacritics; sort uses ICU collation per browser locale

---

## 10. Edge cases

| # | Case | Handling |
|---|---|---|
| 1 | Photo upload >5MB | Inline error «Уменьшите до 5 МБ» + helper link to compression; never silently truncates |
| 2 | Photo upload <500×500 | Inline error «Минимум 500×500 пикселей»; never silently upscales |
| 3 | Wrong file format (e.g., HEIC from iPhone) | Auto-convert server-side if possible; else clear error «Только JPG или PNG» |
| 4 | Master appears in TWO tenants (freelance) | Each tenancy is separate row with separate ID; UX treats them as independent; `max_handle` may collide cross-tenant but per-tenant `Master` rows are distinct; audit notes if collision detected |
| 5 | Admin tries to invite with role=Admin (permission denied) | Frontend hides Admin radio for non-Owner; server rejects with 403 if bypassed; audit logged as `master.role_escalation_attempted` |
| 6 | Invite-token used after expiration | Master sees expired-link screen (per master-mobile §M0); owner sees status as «Истёк, [Отправить ещё раз]»; system auto-cleans CANCELLED→delete at 30d |
| 7 | Master changes MAX handle externally | Bot DMs to old handle fail; system auto-flags master row «контакт не отвечает 3 дня»; owner gets nudge to verify/update |
| 8 | Bulk-deactivate during high-traffic | MM5 flow per master is sequential transaction; if owner aborts mid-bulk, completed ones stay deactivated, pending ones revert to ACTIVE |
| 9 | Service deleted from catalog while in MM4 matrix | Matrix refreshes on save; deleted service row vanishes; mapping rows cascade-deleted with audit |
| 10 | Two admins editing MM3 simultaneously | Last-write-wins with conflict banner (see MM3 states); audit captures both attempts |
| 11 | Master in INVITED state booked by mistake | Server-side: only ACTIVE masters can be assigned to BookingRequest. Frontend hides invited masters from booking pickers in admin UI |
| 12 | Master never accepts invite (7 days) | System fires `master.invite_expired`; owner sees on MM1 with «Истекло — [Отправить ещё раз]» CTA; the row stays in INVITED bucket for owner to act on |
| 13 | YClients sync changes master while owner editing in MM3 | Server-side merge: YC fields override only if owner hasn't edited that field in last 5min; conflict surfaced if both changed |
| 14 | Master self-removed their MAX bot (blocked bot) | Bot DMs queue with delivery_failed status; MM3 shows «не получает push» warning; owner can re-send invite which triggers bot re-add |
| 15 | Owner deactivates themselves (Owner role) | Hard block — UI hides «Деактивировать» for self; server 403 with «Cannot deactivate self — transfer ownership first»; ownership-transfer flow is out-of-scope here (future Settings → Передать владение) |
| 16 | Tenant has 0 active masters after deactivation | UI banner on dashboard root: «У вас нет активных мастеров — помощник не может предложить запись»; bot temporarily disabled, returns generic «свяжитесь со студией» until ≥1 active |
| 17 | Photo upload mid-flight; user closes modal | Upload aborts; partial file discarded; no orphan |
| 18 | INVITED master row exists but bot DM never delivered | After 30min retry exhausted: status badge «Не доставлено» + «Скопировать ссылку для другого канала» fallback link |
| 19 | Customer asks bot «где Анна?» after Anna deactivated | Bot per §6 cross-screen integration responds with single-assistant voice: «Анна сейчас не работает у нас. Могу предложить Марию — она тоже делает гель-лак.» No mention of «деактивирован». |
| 20 | Master record imported from YClients but YC connection later disconnected | Master row keeps `source=yclients_synced` metadata; behaves as catalog-only; owner can promote to invited via MM3 |

---

## 11. Anti-slop scan (12-point per skill ref)

1. **Generic Inter + purple gradient + glassmorphism** — ❌ none used; inherit Conversations module palette (warm professional brand)
2. **Default Material/Bootstrap chips** — ❌ none; use MAX UI on Mini App, custom design tokens on web
3. **Lorem ipsum / placeholder names** — ❌ real Cyrillic names (Анна, Мария, Ольга, Карина) consistent with whole project
4. **Emoji decoration in production UI** — ❌ none in screens (✓ ⊘ ⏳ used as state glyphs are Lucide icons, not emoji)
5. **Stock «AI badges» / sparkle icons** — ❌ none
6. **Carousel for primary content** — ❌ none; table-first dense layout
7. **Modal-stacking** — ❌ MM5 deactivation is a step-flow, not nested modals
8. **«AI-generated layouts that look the same as 100 other apps»** — design references salon-management domain (matrix mapping, archive-not-delete semantics, customer-notification preview) not generic SaaS
9. **Centered hero on every page** — ❌ MM1 is table-dense; MM3 is two-column work area; no empty centering
10. **Soft drop-shadow card grids** — ❌ flat rows with row separators; cards only where semantic (invite preview, customer-notification preview)
11. **Vacuous copy («Manage your team better»)** — ❌ copy is action-specific and references real scenarios (deactivation, reassignment, invitation)
12. **Excessive whitespace / no information density** — ❌ table density is intentional for desktop use; mobile parity collapses to cards thoughtfully

Pass: 12/12.

---

## 12. Open questions

Q-MM-prefix. Tracked in [`decisions-log.md`](../decisions-log.md) after this doc lands.

| # | Question | Owner | Why it matters |
|---|---|---|---|
| **Q-MM1** | If master row has YC-sync origin AND owner edits a field locally, who wins on next YC sync? Three options: (a) local override permanent until owner clears flag, (b) YC always wins (current sketch), (c) per-field opt-in | Founder + Eng | Affects trust — owner edits seem to «evaporate» if YC overwrites; needs explicit policy |
| **Q-MM2** | Customer notification on master deactivation — wording template fixed or per-tenant customizable? Per-tenant introduces persona-drift risk; fixed feels impersonal. Recommend: fixed template + tenant-editable «причина» phrase only | PM + Persona owner | Customer-facing copy quality; persona consistency |
| **Q-MM3** | When master has NO services mapped and owner publishes salon: bot behavior options — (a) silently exclude master from `suggest_master`, (b) bot returns «не понятно к кому записать» error, (c) gate publishing flow («у Анны нет услуг — добавить?»). Recommend (c) at onboarding + (a) post-launch | PM + Eng | Affects bot quality; matrix orphan-warning UI partly addresses but doesn't solve |
| **Q-MM4** | Hard-archive (terminal delete) — is it ever needed for a tenant in normal operation? Or is INACTIVE forever sufficient? GDPR-deletion request handled separately (per OP6). Lean: remove hard-archive from MVP, leave only INACTIVE | Founder + Legal | Reduces destructive surface; simplifies UI; aligns with «no data loss» principle |
| **Q-MM5** | Should owner be able to «промоутить» catalog-only master to invited-mode later? Likely yes — MM3 detail screen has «Пригласить в MAX» button. Confirms current sketch | PM | Minor; needed when freelance master eventually adopts MAX |
| **Q-MM6** | Multi-tenant master (freelance working at 2 salons): should we surface «этот мастер уже в другой студии» warning to owner adding by MAX handle? Tradeoffs: privacy (revealing other tenants' team) vs trust (preventing collision) | Founder + Legal | Cross-tenant disclosure policy — needs explicit decision |
| **Q-MM7** | YClients pre-fill — when YC `master_id` already mapped to our `Master`, what happens on re-sync if YC name changed? Auto-rename, conflict, or ignore? Recommend conflict surface in Settings → Sync Health | Eng | Quiet drift is worst outcome |
| **Q-MM8** | Default schedule on add-master — per Q-SC1 set 10:00–19:00 Mon-Fri. But chains across timezones / nighttime salons need flexibility. MVP: hardcode; later: per-tenant default in Settings | PM | Out-of-MVP enhancement; flag now |
| **Q-MM9** | Master invite expiration — 7 days is product judgment. Founder pref? Some onboarding research suggests 14d for hospitality industry slowness | Founder | Trivial config; pick a value |
| **Q-MM10** | Reactivation — should master's previous services-mapping restore as-is, or re-empty (forcing re-mapping)? Sketch assumes restore. Edge case: services may have changed during deactivation period | PM | UX simplicity vs data accuracy tradeoff |
| **Q-MM11** | Owner deactivation — current sketch blocks (must transfer ownership first). Should we design ownership-transfer flow here or separate doc? Recommend separate (it's rare and needs deeper legal/billing implications) | PM | Scoping |
| **Q-MM12** | Mini App parity vs web-only — supporting both costs ~30% extra. MVP could be web-only with «откройте на компьютере для управления командой» nudge in Mini App. Recommend Mini App parity for MM1+MM3 (read-mostly), web-only for MM2/MM4/MM5 (edit-heavy) | Founder | Effort vs reach |
| **Q-MM13** | Audit log retention specifically for `master.*` events — aligned with Layer 2 (365d)? OR billing-Layer-3 (7y) because role changes affect compliance? Recommend Layer 2 for most, Layer 3 for `master.role_changed` only | Legal | Compliance alignment |

These do not block design completion; they block specific policy choices flagged inline.

---

## 13. Phased delivery

### Phase 1 (MVP, 3 weeks)
- MM1 list (web only, table + tabs, no bulk actions)
- MM2 invite modal (MAX-only delivery; email fallback deferred)
- MM3 detail/edit (no inline-edit, modal-based for each field — simpler)
- Basic deactivation (MM5 Step 1 + Step 3 only, no reassignment UI — must complete all reassignments in Schedule Management module before deactivation)
- Server-side: all endpoints; audit emission

### Phase 2 (2 weeks)
- MM4 services-master matrix (web + Mini App)
- MM5 full reassignment flow (Step 2 with per-booking UI)
- Inline-editing UX on MM3
- Mini App parity for MM1 + MM3
- Bulk actions on MM1 (Owner only)

### Phase 3 (1 week)
- YClients pre-fill in MM2 (per Q-M7)
- Audit-log viewer enhancements
- Reactivation flow polish
- Email fallback for invite

### Phase 4 (deferred)
- Hard-archive (only if Q-MM4 confirms need)
- Ownership transfer flow (separate doc)
- Master-proposed services-mapping (request mode)

---

## 14. Sign-off checklist

- [x] Mode declared: `handoff`
- [x] Target surfaces identified: web primary + MAX Mini App parity + MAX bot DM for invite delivery
- [x] JTBD stated — primary (hiring) + primary (departure) + secondaries in §3
- [x] All states designed (loading, empty, error, success, permission-denied, offline) per screen
- [x] Platform-native components used (MAX UI React lib on Mini App; semantic HTML on web)
- [x] Contrast ≥ 4.5:1 body / 3:1 large (token-inherited from Conversations module)
- [x] Touch targets ≥ 44pt iOS / 48dp Android / 24×24 CSS web
- [x] Keyboard navigation defined (web)
- [x] Visible focus state specified
- [x] Reduced-motion fallback specified
- [x] Anti-slop scan: 12/12 ✅
- [x] Bridge API methods listed where Mini App used
- [x] Sticky CTA used on Mini App (per MAX platform constraint — no MainButton)
- [x] Backend contracts inventoried (§8)
- [x] A11y checklist (§9)
- [x] Edge cases (§10) — 20 scenarios resolved
- [x] Open questions (Q-MM1 … Q-MM13) — surfaced for founder/PM
- [x] Phased delivery (§13)

---

## 15. Cross-document linkage

- Permissions canon: [`docs/design/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4
- Master self-view (opposite side): [`docs/design/2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md)
- Schedule editing (linked, not duplicated): [`docs/design/2026-05-18-schedule-management-handoff.md`](./2026-05-18-schedule-management-handoff.md)
- Performance analytics (linked, not duplicated): [`docs/design/2026-05-18-analytics-dashboard-handoff.md`](./2026-05-18-analytics-dashboard-handoff.md)
- Onboarding integration: [`docs/design/2026-05-17-salon-onboarding-handoff.md`](./2026-05-17-salon-onboarding-handoff.md) Phase 4c
- Attribution model: [`docs/design/attribution-policy.md`](../policies/attribution-policy.md) — `actor_type=master` semantics
- Persona / customer-facing voice: [`docs/design/assistant-persona.md`](../policies/assistant-persona.md) — customer never sees individual master identity per single-assistant principle
- Strategic foundation: `memory/project_single_assistant_identity.md`, `memory/project_conversation_ownership_tiers.md`, `memory/project_max_platform_capabilities.md`
- Decisions log: add Q-MM1 … Q-MM13 to [`decisions-log.md`](../decisions-log.md) on doc landing
- Platform playbook: `~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md`

---
