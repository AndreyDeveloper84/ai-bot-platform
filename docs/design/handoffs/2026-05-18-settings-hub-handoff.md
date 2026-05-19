# Settings Hub — Developer Handoff Package

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Designer** | UX-architect skill |
| **Status** | Draft for review |
| **Surfaces** | Web dashboard (primary, desktop + mobile responsive) + MAX manager-bot DMs (notification settings cross-control + audit alerts) |
| **Scope** | Settings homepage (aggregator/router) + Audit Log Viewer + Notification Preferences + Conversation Policy aggregator. Consolidates entry points to all sub-modules already designed elsewhere; designs the 3 missing pieces. |
| **Auth** | Per-role visibility per [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4 — Owner full, Admin partial, Receptionist read-only on own subset, Master own profile only |
| **Screens** | 4: SH1 homepage · SH2 audit log viewer · SH3 notification preferences · SH4 conversation policy aggregator |
| **Anti-scope** | This doc does NOT redesign sub-modules (persona, schedule, masters, loyalty, billing, onboarding). It LINKS to them. The 3 new screens (SH2/SH3/SH4) are designed in full. |

## Foundation references (read first)

| Doc | Why it matters here |
|---|---|
| [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) | §4 permissions matrix gates every section/row in SH1; §5 audit events list IS the spec for SH2 (every event must be queryable); §6 retention drives the 365d disclaimer; §3 SLA tiers drive notification toggles in SH3 |
| [`2026-05-18-persona-editor-handoff.md`](./2026-05-18-persona-editor-handoff.md) | Settings → Помощник sub-section — SH1 links here; SH4 references §Policy sub-tab |
| [`2026-05-18-schedule-management-handoff.md`](./2026-05-18-schedule-management-handoff.md) | Settings → Расписание — SH1 links here |
| [`2026-05-18-master-management-handoff.md`](./2026-05-18-master-management-handoff.md) | Settings → Команда — SH1 links here; permissions inform Owner-only badges |
| [`2026-05-18-loyalty-system-handoff.md`](./2026-05-18-loyalty-system-handoff.md) | Settings → Лояльность — SH1 links here (post-MVP Volna 4) |
| [`2026-05-17-salon-onboarding-handoff.md`](./2026-05-17-salon-onboarding-handoff.md) | Phase 12 Billing settings + Tax profile (§Screen 12 + Q14) — SH1 Биллинг section links here |
| [`assistant-persona.md`](../policies/assistant-persona.md) | Source of «помощник» vocabulary — never «бот» in customer-facing copy; settings UI follows same convention internally where it shows customer-side framing |
| [`decisions-log.md`](../decisions-log.md) | OP3 (4 fixed roles MVP), OP5 (CSV + JSON export), OP2 (SLA editable v1.1), Q-C3 (365d audit retention) |
| [`memory/project_max_platform_capabilities.md`](~/.claude/projects/.../memory/project_max_platform_capabilities.md) | MAX bot DM is primary push channel — SH3 reflects this; email is secondary |

---

## 1. Status of major decisions (this doc)

| # | Decision | Status |
|---|---|---|
| **A. Information architecture** | 6 logical groups (Помощник / Каталог·Расписание / Уведомления·Команда / Биллинг·Тарифы / Интеграции / Аккаунт) with deep links to existing sub-modules. Flat single-page over nested drill-downs. | Locked |
| **B. Audit immutability** | Audit events CANNOT be edited or deleted from UI. Read-only forever per OP3 + Layer 2 retention policy. Even Owner cannot redact (regulatory requirement). | Locked |
| **C. Export format** | **CSV + JSON only** per OP5. No PDF MVP. CSV for spreadsheet inspection, JSON for forensic / structured analysis. Format frozen for audit hash continuity. | Locked per OP5 |
| **D. Audit retention disclaimer** | Persistent banner on SH2: «Хранится 365+ дней per Layer 2 retention policy. Удалить нельзя.» | Locked per Q-C3 |
| **E. Per-admin notification settings** | Each admin owns their own SH3 record. Owner sees overview matrix of all admins' settings (read-only awareness, NOT edit-other-admins). | Locked |
| **F. Push channel hierarchy** | **MAX bot DM** = primary (real-time alerts). **Email** = secondary (digests + invoices). No SMS, no in-app web push MVP (relies on MAX DM). | Locked |
| **G. Quiet hours default** | 22:00–08:00 in tenant timezone (per Q-AD6). Customizable per admin. Override flag for «critical» (payment fail, SLA escalation 60+ min) bypasses quiet hours. | Locked |
| **H. Conversation Policy aggregator** | SH4 is mostly **read-only summary + links**. Editing happens in source-of-truth modules (Persona Editor for explicit-human, Master Management for roles). Per OP3 no custom role editor MVP. | Locked |
| **I. Per-role visibility on SH1** | Sections hidden entirely (not greyed-out) for roles lacking access. Reduces cognitive load; non-Owners don't see «Налоговый профиль» row that they can't open anyway. | Locked |
| **J. Mobile responsive** | All 4 screens reflow to 360px width — Owner often checks audit/billing on phone. SH2 audit table → vertical card stack on mobile. | Locked |

---

## 2. Overview

### What this module is
The Settings Hub is the **router and aggregator** for every tenant-level configuration in the platform. Most actual settings live in other modules (Persona Editor, Schedule, Masters, Loyalty, Billing, Onboarding Phase 12). This hub provides **one consistent entry point** + designs the **3 cross-cutting screens** that did not have a home yet:

1. **SH2 Audit Log Viewer** — referenced from `conversation-ownership-policy §5` («Export endpoint exposed in Settings → Аудит — Owner only») but never designed
2. **SH3 Notification Preferences** — referenced from multiple flows (Q-M3 daily digest, Q-M6 master change-request alerts, SLA escalation pushes, LQ6 learning summary, billing failure alerts) but never designed
3. **SH4 Conversation Policy Aggregator** — operational summary of who-can-say-what; mostly links into Persona Editor + Master Management for actual editing

### Why this matters
- **Findability**: 17 sub-modules across the platform — without a hub, owners blunder around. JTBD target: 30 seconds to find any setting.
- **Compliance posture**: Audit log viewer is required for tenant trust («I can see who did what in my salon») and for any future legal dispute (OP4 retention policy execution).
- **Notification overload**: Without SH3, every admin gets every push. Anya (high-volume admin) burns out. Karina (Owner) misses critical alerts because she muted everything. Granular per-admin control prevents the «mute-all» dead end.
- **Policy transparency**: SH4 surfaces the invisible ownership policy so Owners can build trust in it («I can see exactly when the assistant goes silent vs. continues»).

### Primary persona — Karina (Owner)
- Non-technical but operationally fluent
- Settings visits: dense at onboarding (Phase 5–12), then sparse (~1–2 times/month for billing review, 1–2 times/quarter for audit spot-check)
- Mobile-first on audit + billing; desktop on persona/schedule
- Wants to FEEL in control without needing to read manuals

### Secondary persona — Anya (Admin)
- High-volume operational user (replies to customers all day)
- Visits SH3 frequently early on to dial down notifications until tolerable
- Cares about quiet hours, vacation mode
- Owns own SH3; sees a subset of SH1 (no billing, no tax profile, no roles)

### Tertiary persona — Receptionist / Stažер
- Minimal settings access — own notification preferences + own profile
- Doesn't see audit log, doesn't see billing, doesn't see policy

### Anti-persona — Master
- Master role uses Master Mobile module, NOT this hub
- If a master clicks «Настройки» link in nav, they see only «Мой профиль» entry → routes them out to master-mobile §M4

### JTBDs

**Owner findability (primary):**
> «Когда мне нужно настроить или проверить что-то на платформе, я хочу найти это за 30 секунд по чёткой структуре — чтобы не блуждать по подменю.»

**Owner audit (high-stakes occasional):**
> «Когда я хочу проверить кто что сделал в моём салоне на платформе, я хочу видеть полную хронологию действий — для compliance и доверия.»

**Admin/Receptionist notifications (daily friction):**
> «Когда мне приходят слишком частые / слишком редкие push'и, я хочу настроить какие именно уведомления мне нужны — чтобы не отключать всё.»

**Anti-JTBD (what we should NOT optimize for):**
- Power-user customization (50 toggles for someone visiting once a month). Group, hide, default sensibly.
- Audit log as a real-time monitoring dashboard. It's a forensic tool, not a feed.
- Bypassing the dedicated sub-modules. SH1 routes, doesn't replicate.

### Success metrics

| Metric | Target | Type |
|---|---|---|
| **Time-to-find** for any setting (Owner self-report, prompted task) | median ≤ 30s, p90 ≤ 60s | North Star |
| Settings homepage bounce rate (entered, left without clicking a section) | < 10% | Health |
| Audit log opens per active Owner per month | ≥ 1 (engaged), ≤ 5 (sanity ceiling) | Engagement |
| Notification preference edit rate within first 14d of admin onboarding | ≥ 60% (proves discoverability) | Adoption |
| Push-related «turn off everything» events | < 5% of admins | Anti-burnout signal |
| Audit export downloads per month | ≥ 0.2 per Owner | Compliance use |
| Mobile usage share of SH1 (Owner) | ≥ 30% (validates responsive priority) | Surface fit |

---

## 3. State machine

Settings is largely stateless — most pages are read or route-out. The non-trivial state is in SH2 (audit filters + export) and SH3 (edit/save form). SH1 and SH4 are stateless.

```
SH1 (homepage)
  ├─→ Click section card → routes to sub-module (external state)
  └─→ stays SH1 if non-clickable hint clicked

SH2 (audit viewer)
  IDLE (default filters: last 7d, all events, all actors)
    ├─→ Edit filter chips → LOADING → IDLE (filtered)
    ├─→ Click row → DETAIL_DRAWER_OPEN
    │     └─ Close drawer → IDLE
    ├─→ Click «Экспорт» → EXPORT_MODAL_OPEN
    │     ├─ Cancel → IDLE
    │     └─ Confirm (format + range) → EXPORTING → DOWNLOAD_READY → IDLE
    └─→ Search by hash → SEARCHING → IDLE (single-row result OR empty state)

SH3 (notification preferences)
  VIEWING (current settings, all toggles + sliders displayed)
    └─→ Edit any control → DIRTY
          ├─→ Click «Сохранить» → SAVING → SAVED (toast 2s) → VIEWING
          └─→ Click «Отменить» → CONFIRM_DISCARD → VIEWING

SH4 (policy aggregator)
  Read-only sections + links — no state.
  Each «Изменить →» button routes to source-of-truth module.
```

---

## 4. Routes

| Route | Screen | Auth |
|---|---|---|
| `/settings` | SH1 homepage | All authenticated roles (but content varies) |
| `/settings/audit` | SH2 audit viewer | Owner only (redirect with «недостаточно прав» banner for others) |
| `/settings/audit/{event_id}` | SH2 with detail drawer pre-opened | Owner only |
| `/settings/notifications` | SH3 own preferences | All admins (sees own only) |
| `/settings/notifications/overview` | SH3 Owner overview of all admins | Owner only |
| `/settings/policy` | SH4 conversation policy aggregator | Owner + Admin (read-only) |

Cross-module routes (existing, linked from SH1):
- `/settings/persona` — Persona Editor (P1) — Owner only
- `/settings/persona/policy` — Persona Editor §Policy sub-tab — Owner only
- `/settings/learning` — Learning Queue (C4) — Admin + Owner
- `/settings/catalog` — Catalog (existing onboarding Phase 4b)
- `/settings/services` — Services list
- `/settings/masters` — Master Management (MM1) — Owner + Admin
- `/settings/schedule` — Schedule Management (S1) — Owner + Admin
- `/settings/loyalty` — Loyalty (Volna 4 post-MVP)
- `/settings/billing` — Onboarding Phase 12 Billing screen — Owner only
- `/settings/billing/payment-method` — Owner only
- `/settings/billing/history` — Owner only
- `/settings/billing/tax-profile` — Owner only
- `/settings/integrations/yclients` — Owner + Admin
- `/settings/integrations/channels` — Owner + Admin
- `/settings/integrations/api` — Owner only (placeholder MVP)
- `/settings/account` — Own profile (all roles see own)
- `/settings/account/tenant` — Tenant info — Owner only
- `/settings/account/deactivate` — Tenant deactivation — Owner only, gated by confirm

Breadcrumb pattern: `Настройки → {Группа} → {Раздел}` — always rendered, always clickable up the chain.

---

## 5. Screen SH1 — Settings Homepage (router/aggregator)

### Purpose
One place to find any setting. Visually grouped, role-aware, deep-linkable.

### Desktop layout (≥ 1024px)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Студия Карина   [Setup ✓]   [Karina, owner ▾]                                │
├──────────────────────────────────────────────────────────────────────────────┤
│ Dashboard │ Каталог │ Диалоги │ Аналитика │ Биллинг │ ► Настройки            │
├──────────────────────────────────────────────────────────────────────────────┤
│ Настройки                                                                    │
│ ────────                                                                     │
│ Найдите нужный раздел или начните вводить чтобы поискать.                    │
│ ┌─────────────────────────────────────────────────────┐                      │
│ │ 🔍 Поиск по настройкам…                             │                      │
│ └─────────────────────────────────────────────────────┘                      │
│                                                                              │
│ ┌─────────────────────────────────┬─────────────────────────────────┐        │
│ │ ПОМОЩНИК                        │ КАТАЛОГ И РАСПИСАНИЕ            │        │
│ │                                 │                                 │        │
│ │ ▸ Голос                         │ ▸ Каталог                       │        │
│ │   Имя, тон, запретные фразы     │   Услуги, категории             │        │
│ │                                 │                                 │        │
│ │ ▸ Политика разговоров           │ ▸ Услуги                        │        │
│ │   Кто говорит — AI или команда  │   Длительности, цены            │        │
│ │                                 │                                 │        │
│ │ ▸ Учёба                         │ ▸ Мастера                       │        │
│ │   12 новых предложений →        │   Роли, фото, услуги-мастера   │        │
│ │                                 │                                 │        │
│ │                                 │ ▸ Расписание                    │        │
│ │                                 │   Рабочие часы, исключения      │        │
│ └─────────────────────────────────┴─────────────────────────────────┘        │
│                                                                              │
│ ┌─────────────────────────────────┬─────────────────────────────────┐        │
│ │ УВЕДОМЛЕНИЯ И КОМАНДА           │ БИЛЛИНГ И ТАРИФЫ      [Owner]   │        │
│ │                                 │                                 │        │
│ │ ▸ Мои уведомления               │ ▸ Тариф                         │        │
│ │   MAX-пуши, e-mail, тихие часы  │   Founder pricing · 50 / 50 →   │        │
│ │                                 │                                 │        │
│ │ ▸ Команда             [Owner]   │ ▸ Платёжный метод               │        │
│ │   Аня, Иван, Маша + 2 мастера   │   •••• 4242 · истекает 09/27    │        │
│ │                                 │                                 │        │
│ │ ▸ Аудит               [Owner]   │ ▸ История счетов                │        │
│ │   Кто что сделал · 365+ дней    │   Май 2026: 2 290 ₽ оплачен ✓   │        │
│ │                                 │                                 │        │
│ │                                 │ ▸ Налоговый профиль ⚠ не заполнен│        │
│ │                                 │   ИП / ООО / самозанятый        │        │
│ └─────────────────────────────────┴─────────────────────────────────┘        │
│                                                                              │
│ ┌─────────────────────────────────┬─────────────────────────────────┐        │
│ │ ИНТЕГРАЦИИ                      │ АККАУНТ                         │        │
│ │                                 │                                 │        │
│ │ ▸ YClients          [✓ Connect] │ ▸ Профиль                       │        │
│ │   Синхронизация ✓ 12 мин назад  │   Имя, телефон, аватар          │        │
│ │                                 │                                 │        │
│ │ ▸ Каналы                        │ ▸ Салон               [Owner]   │        │
│ │   MAX ✓ · Telegram ✓            │   Название, адрес, часовой пояс │        │
│ │                                 │                                 │        │
│ │ ▸ API доступ           Скоро    │ ▸ Деактивация         [Owner]   │        │
│ │   Webhook + REST · v1.1         │   Приостановить или закрыть     │        │
│ └─────────────────────────────────┴─────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Mobile layout (≤ 768px)
Single-column stack. Section cards collapsible (default: all open). Search bar sticky at top. Same sections in same order. Each row is a full-tap target (≥ 56dp).

### Per-role visibility on SH1

| Section / Row | Owner | Admin | Receptionist | Master |
|---|---|---|---|---|
| Помощник → Голос | ✅ | 👁 read-only link | ❌ hidden | ❌ hidden |
| Помощник → Политика | ✅ | 👁 link | ❌ | ❌ |
| Помощник → Учёба | ✅ | ✅ | ❌ | ❌ |
| Каталог · все 4 | ✅ | ✅ | 👁 read-only | ❌ |
| Уведомления → Мои | ✅ | ✅ | ✅ | own profile only via Master Mobile |
| Уведомления → Команда | ✅ | 👁 view roster, no edit | ❌ | ❌ |
| Уведомления → Аудит | ✅ | 👁 own actions only (not full log) | 👁 own actions | 👁 own actions |
| Биллинг · все 4 | ✅ | ❌ hidden | ❌ | ❌ |
| Интеграции · все 3 | ✅ | ✅ (read+edit YC/channels) | ❌ | ❌ |
| Аккаунт → Профиль | ✅ own | ✅ own | ✅ own | routes to Master Mobile §M4 |
| Аккаунт → Салон | ✅ | 👁 read-only | ❌ | ❌ |
| Аккаунт → Деактивация | ✅ | ❌ | ❌ | ❌ |

«Hidden» means the entire row + label is not rendered (cleaner than greyed-out). Visual hint `[Owner]` badge appears next to rows visible to Admin in read-only mode but exclusive-edit by Owner.

### States

- **Default** — sections rendered per role, all data fetched
- **Loading** — skeleton for each row (label + 1-line meta), 200ms shimmer
- **Empty** — N/A; SH1 always has at least the user's own rows
- **Search-active** — sections collapse; matching rows shown flat with section label as breadcrumb
- **Search no-results** — «Ничего не нашли. Попробуйте «расписание», «налог», «уведомления».» + 3 suggested chips
- **Permission error mid-route** — if Owner permission revoked while user is in `/settings/audit`, redirect back to SH1 with banner «Раздел больше недоступен»

### Search behavior
- Fuzzy match on section name + row label + 1-line description
- Synonyms map: «налог» → Налоговый профиль; «отпуск» → notification vacation mode; «возврат» → Биллинг → История счетов; «бот» → 🚫 no-op (terminology is «помощник» — show inline hint «Используется термин помощник»)
- Search history (last 3) persisted in localStorage; cleared on logout

### Interactions
- Click section card header: no-op (header is a label, not a link)
- Click row: routes to deep link
- Right-side `[Owner]` chip: tooltip on hover «Доступно только владельцу»
- Right-side `⚠` warning (e.g., tax profile not filled): tooltip «Заполните до первого платежа»

### Tokens used
- bg-page: `--color-bg-page`
- card-bg: `--color-bg-elevated`
- card-radius: `--radius-lg` (12px)
- divider: `--color-border-subtle`
- chip-owner: `--color-warning-100` bg / `--color-warning-700` text
- icon-section: `--color-accent-500` (consistent per-section icon hue)

### A11y
- Each section card: `<section aria-labelledby="sh1-help-helper">` etc.
- Section header: `<h2>` for screen-reader hierarchy
- Each row is `<a>` (not `<div onclick>`) — keyboard nav, focus ring, right-click works
- Search input: `<input type="search">` with `aria-label="Поиск по настройкам"`; `<datalist>` for synonym suggestions
- `[Owner]` badge: visually decorative + `aria-label="Только для владельца"` on hover region

---

## 6. Screen SH2 — Audit Log Viewer (NEW, Owner only)

### Purpose
Forensic timeline of every action across the tenant. Spec source: [`conversation-ownership-policy §5`](../policies/conversation-ownership-policy.md#5-mandatory-audit-events). Every event listed there MUST be queryable here.

### Why this screen exists (in user terms)
- «Кто разблокировал диалог Марии?» → filter `event_type=conversation.bot_resumed` + `target=conv_xxx`
- «Когда Аня просматривала медзаписи Анастасии?» → filter `event_type=conversation.medical_notes_viewed` + `actor=anya`
- «Покажите всё что Иван делал в апреле» → filter `actor=ivan` + `date=2026-04-*`
- «Найди этот hash из тикета поддержки» → search by `content_hash`

### Desktop layout (≥ 1024px)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Настройки → Аудит                                                            │
│ ──────────────                                                               │
│ Хранится 365+ дней per Layer 2 retention. Удалить или изменить нельзя. ⓘ     │
├──────────────────────────────────────────────────────────────────────────────┤
│ Фильтры:                                                            [Экспорт]│
│ ┌──────────────┬──────────────┬──────────────┬──────────────┬─────────────┐ │
│ │ Дата          │ Кто           │ Тип события   │ Цель          │ Поиск hash ││
│ │ Посл. 7 дней ▾│ Все ▾         │ Все ▾         │ Все ▾         │ 🔍         ││
│ └──────────────┴──────────────┴──────────────┴──────────────┴─────────────┘ │
│ Активные фильтры: × «Посл. 7 дней»  + Очистить                               │
│                                                                              │
│ ┌─────────────┬────────────────┬─────────────────────┬─────────────────────┐│
│ │ Время        │ Кто             │ Действие             │ Цель                ││
│ ├─────────────┼────────────────┼─────────────────────┼─────────────────────┤│
│ │ 18:42:17    │ AI · помощник   │ Отправил сообщение   │ Мария Петрова       ││
│ │ 18 мая      │                 │ message_sent         │ conv_8a3f…        →││
│ ├─────────────┼────────────────┼─────────────────────┼─────────────────────┤│
│ │ 18:39:02    │ Аня · admin     │ Одобрила черновик    │ Анастасия К.        ││
│ │ 18 мая      │                 │ draft_approved       │ conv_4d12…        →││
│ ├─────────────┼────────────────┼─────────────────────┼─────────────────────┤│
│ │ 17:55:48    │ Аня · admin     │ Раскрыла телефон     │ Анастасия К.        ││
│ │ 18 мая      │                 │ phone_revealed   ⓘ  │ conv_4d12…        →││
│ ├─────────────┼────────────────┼─────────────────────┼─────────────────────┤│
│ │ 17:12:30    │ Karina · owner  │ Изменила тон голоса  │ persona (Голос)     ││
│ │ 18 мая      │                 │ persona.updated      │ —                 →││
│ ├─────────────┼────────────────┼─────────────────────┼─────────────────────┤│
│ │ 16:30:00    │ AI · system     │ Перевел в HUMAN_LOCKED│ conv_4d12 (Мед)    ││
│ │ 18 мая      │                 │ tier_changed         │ reason=medical    →││
│ ├─────────────┼────────────────┼─────────────────────┼─────────────────────┤│
│ │ … (24 events за 7 дней — показано 5 из 24)                              ││
│ └─────────────┴────────────────┴─────────────────────┴─────────────────────┘│
│                                                                              │
│ Загрузить ещё (19) ▾    Страница 1 из 1 (24 события)                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Detail drawer (right-side slide-in, ~440px wide)

Triggered by clicking `→` on a row. Shows full structured payload + related events.

```
┌─────────────────────────────────────────┐
│ ←  Событие: phone_revealed              │
│ ─────────────────────────────           │
│                                          │
│ Время:    2026-05-18 17:55:48 UTC+3     │
│ ID:       evt_01HXKZ8R9MNQ…              │
│ Кто:      Аня (admin) · anya@…           │
│ Цель:     Анастасия К.                   │
│ Диалог:   conv_4d12…              [→]    │
│                                          │
│ Контекст:                                │
│ ┌─────────────────────────────────────┐ │
│ │ {                                    │ │
│ │   "event_type": "conversation.       │ │
│ │     phone_revealed",                 │ │
│ │   "tenant_id": "studio-karina",      │ │
│ │   "actor_id": "anya",                │ │
│ │   "actor_role": "admin",             │ │
│ │   "conversation_id": "conv_4d12…",   │ │
│ │   "customer_id": "cust_a8…",         │ │
│ │   "occurred_at": "2026-05-18T14:55:48│ │
│ │     Z",                              │ │
│ │   "phone_hash": "sha256:…ab12",      │ │
│ │   "reveal_reason": "manual_unmask",  │ │
│ │   "ip_address": "85.142.…",          │ │
│ │   "user_agent": "Mozilla/5.0…"       │ │
│ │ }                                    │ │
│ └─────────────────────────────────────┘ │
│                                          │
│ Связанные события (3):                   │
│ • 17:55:42 conversation.assigned (Аня)   │
│ • 17:56:01 conversation.message_sent     │
│ • 18:02:11 conversation.resolved         │
│                                          │
│ [Скопировать JSON]  [Закрыть]            │
└─────────────────────────────────────────┘
```

### Mobile layout
Filter chips wrap to multiple lines (max 2 visible, rest behind «Ещё фильтры»). Table → vertical card stack:

```
┌─────────────────────────────────┐
│ 18:42 · 18 мая                  │
│ AI · помощник                   │
│ Отправил сообщение               │
│ → Мария Петрова                  │
│ message_sent · conv_8a3f…       │
│                            [→]   │
├─────────────────────────────────┤
│ 18:39 · 18 мая                  │
│ Аня · admin                      │
│ …                                │
```

Detail drawer becomes full-screen modal on mobile.

### Filter spec

| Filter | Control | Default | Notes |
|---|---|---|---|
| Дата | Date range picker w/ presets | Last 7 days | Presets: Today / 7d / 30d / 90d / Custom |
| Кто (actor) | Multi-select dropdown | All | Auto-completes admin names + special «AI · system» |
| Тип события | Multi-select (grouped) | All | Grouped by category per §5: Lifecycle / Reply / Sensitive / AI control / Quality |
| Цель | Search/select | All | Search by conv_id, customer name, booking_id, customer phone |
| Поиск hash | Free-text input | — | Matches `content_hash`, `phone_hash`, or `event_id` prefix — for forensic correlation from external tickets |

### Event type catalog (Кто что сделал — Russian display names)

Mapping engineering event keys → display strings. EVERY event in `conversation-ownership-policy §5` must have a row:

| event_type | Display (RU) | Category | Visible payload preview |
|---|---|---|---|
| `conversation.created` | Создан диалог | Lifecycle | channel, customer |
| `conversation.handoff_triggered` | Триггер передачи | Lifecycle | reason, confidence |
| `conversation.tier_changed` | Изменён уровень управления | Lifecycle | from → to, reason |
| `conversation.assigned` | Назначен админ | Lifecycle | admin name |
| `conversation.resolved` | Закрыт | Lifecycle | resolution type |
| `conversation.escalated_to_csm` | Эскалирован в CSM | Lifecycle | — |
| `conversation.snoozed` | Отложен | Lifecycle | until |
| `conversation.auto_abandoned` | Авто-сброс через 24ч | Lifecycle | — |
| `conversation.message_sent` | Отправил сообщение | Reply | composed_by, hash |
| `conversation.message_failed` | Сообщение не доставлено | Reply | error |
| `conversation.draft_approved` | Одобрил черновик | Reply | — |
| `conversation.draft_rejected` | Отклонил черновик | Reply | — |
| `conversation.draft_edited` | Отредактировал черновик | Reply | edit ratio |
| `conversation.phone_revealed` | Раскрыл телефон | Sensitive ⓘ | phone hash, reason |
| `conversation.medical_notes_viewed` | Просмотрел медзаписи | Sensitive ⓘ | medical_role check |
| `conversation.note_added` | Добавил заметку | Sensitive | content hash |
| `conversation.customer_blocked` | Заблокировал клиента | Sensitive | reason |
| `conversation.export_initiated` | Запустил экспорт | Sensitive | format, range |
| `conversation.bot_resumed` | Возобновил помощника | AI control | from tier |
| `conversation.bot_locked` | Заглушил помощника | AI control | reason |
| `conversation.tier_override` | Изменил уровень вручную | AI control | from → to, reason |
| `conversation.faq_proposed` | AI предложил FAQ | AI control | candidate hash |
| `conversation.faq_accepted` | Добавил FAQ в базу | AI control | — |
| `conversation.faq_rejected` | Отклонил FAQ | AI control | reason |
| `conversation.persona_violation_warned` | Предупреждение тона | Quality | rule |
| `conversation.persona_violation_overridden` | Тон-предупреждение проигнорировано | Quality | rule, admin |
| `conversation.forbidden_phrase_blocked` | Запретная фраза заблокирована | Quality | rule |
| `conversation.suspicious_activity` | Подозрительная активность | Quality | pattern |

Additional events (not in §5 but emitted by Persona Editor, Schedule, Master Management) also surface here for completeness — e.g. `persona.updated`, `master.added`, `master.deactivated`, `schedule.exception_added`, `billing.payment_failed`, `tenant.deactivated`.

The category filter UI groups them; the underlying enum is the engineering source of truth, this UI just renames for display.

### Sensitive event marker (ⓘ icon)
Events accessing PII (phone reveal, medical notes view, export initiated) get a visible ⓘ icon in the table to draw attention. Tooltip: «Доступ к чувствительным данным — зафиксировано для compliance».

### Export modal (CSV + JSON per OP5)

```
┌─────────────────────────────────────────┐
│ Экспорт аудита                          │
│ ────────────                            │
│                                          │
│ Формат:                                  │
│ ⦿ CSV     для таблиц                     │
│ ◯ JSON    структурированный, для анализа │
│                                          │
│ Диапазон:                                │
│ ⦿ Текущие фильтры (24 события)          │
│ ◯ Последние 30 дней (всё)                │
│ ◯ Произвольный диапазон…                 │
│                                          │
│ ☐ Включить хеши content_hash             │
│   (для forensic-сопоставления)          │
│                                          │
│ ⚠ Экспорт не включает полный текст       │
│   сообщений (per Layer 2 stripped-down). │
│   Тексты — в Диалогах per conversation.  │
│                                          │
│ [Отмена]              [Скачать .csv]    │
└─────────────────────────────────────────┘
```

Export itself emits an audit event: `conversation.export_initiated` with actor, format, filters, ts.

### States

- **Default IDLE** — last 7d, all events, all actors. Loads in < 1s for typical tenant (< 10K events). Shows 50 rows, paginate.
- **Loading** — table skeleton (5 rows shimmer) while query runs
- **Empty (filter no-match)** — «За выбранный период событий не найдено. Попробуйте расширить диапазон или сбросить фильтры.» + button «Сбросить»
- **Empty (no events ever)** — N/A; tenant.created is itself an audit event
- **Error** — banner «Не удалось загрузить аудит. Повторить?» + retry button
- **Permission denied** — visiting as non-Owner redirects to SH1 with banner «Журнал аудита доступен только владельцу»

### Edge cases

- **Millions of events** — at 5K+ events per tenant, pagination cursor-based (not offset). Indexed on `(tenant_id, occurred_at DESC)`. Query timeout 5s — if exceeded, banner «Сузьте фильтр» + suggest tighter range.
- **Export of 100K+ events** — sync export fails; route to background job. UI shows «Экспорт готовится — пришлём ссылку в MAX-бот когда будет готов» (NotificationPreferences-controlled).
- **Event from deleted admin** — actor_id retained; display name flagged as «Аня (удалён)». Audit immutability rules.
- **Event from anonymized customer** (180d post-retention) — target column shows `cust_a8…` UUID only, no name. Banner ⓘ on hover «Профиль клиента анонимизирован per retention policy».
- **Clock skew / ordering** — events ordered by `occurred_at` (server time), not client. Server enforces monotonic timestamps; ties broken by event_id.

### Interactions

- Click row: detail drawer (right-slide, 250ms, reduced-motion: instant)
- Click conv link in drawer: routes to `/conversations/{id}` (opens in new tab via cmd-click; same tab default)
- Copy JSON button: copies full payload to clipboard, toast «Скопировано»
- Filter chip remove (×): refreshes table
- Pagination «Загрузить ещё»: appends rows, no full reload

### Tokens

- table-row-bg: `--color-bg-page`
- table-row-bg-hover: `--color-bg-elevated`
- table-divider: `--color-border-subtle`
- icon-sensitive: `--color-warning-500`
- export-button: `--color-accent-500` (primary CTA)
- monospace-payload: `--font-mono` (JetBrains Mono fallback)

### A11y

- Table: proper `<table>` with `<thead scope="col">`, NOT divs
- Row click: `<tr>` with `tabindex="0"` and `role="button"`, Enter opens drawer
- Drawer: `<aside role="dialog" aria-modal="false">` (non-blocking, but focus-trapped on open)
- Sensitive ⓘ icon: `aria-label="Доступ к чувствительным данным"`
- JSON viewer: `<pre>` with `role="region"` and `aria-label="Структурированные данные события"`
- Date picker: native `<input type="date">` (cross-platform a11y free)
- Keyboard: ↓/↑ navigate rows, Enter opens drawer, Esc closes

---

## 7. Screen SH3 — Notification Preferences (NEW)

### Purpose
Each admin tunes which pushes/emails they receive. Default «sensible» so users don't burn out; granular enough they don't mute everything.

### Default config (per role on first onboarding)

| Setting | Owner | Admin | Receptionist |
|---|---|---|---|
| MAX: Master change-request | ✅ | ✅ if has master-approval permission | ❌ |
| MAX: Handoff SLA 15min warning | ✅ if assigned | ✅ assigned only | ✅ assigned only |
| MAX: Handoff SLA 30min high | ✅ all | ✅ all | ❌ |
| MAX: Handoff SLA 60min stale | ✅ all | ✅ all | ❌ |
| MAX: Handoff SLA 120min abandonment | ✅ | ❌ | ❌ |
| MAX: Daily digest (09:00) | ✅ | ✅ | off |
| MAX: Weekly digest (Mon 09:00) | ✅ | off | off |
| MAX: Payment failed | ✅ | ❌ (Owner-only event) | ❌ |
| MAX: YC sync errors | ✅ | ✅ | off |
| MAX: Learning Queue weekly summary | ✅ | ✅ | off |
| Email: Daily digest | off | off | off |
| Email: Weekly digest | ✅ | off | off |
| Email: Critical alerts (payment fail, security) | ✅ | ❌ | ❌ |
| Email: Monthly invoice PDF | ✅ | ❌ | ❌ |
| Quiet hours | 22:00–08:00 | 22:00–08:00 | 22:00–08:00 |
| Vacation mode | off | off | off |

### Desktop layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Настройки → Уведомления                                                      │
│ ──────────────                                                               │
│ [Мои уведомления] [Команда (Owner)]  ← sub-tabs                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ Мои уведомления — Karina                                                     │
│                                                                              │
│ ── MAX-бот (основной канал) ──                                               │
│ Уведомления приходят в личку MAX-бота. Открыть → @studio_karina_bot          │
│                                                                              │
│ ▸ Запросы мастеров на изменения                          [●─] Включено       │
│   Когда мастер просит изменить расписание или услугу                          │
│                                                                              │
│ ▸ Передачи диалогов (SLA-эскалации)                                          │
│   15 мин — мягкое напоминание                            [●─] Включено       │
│   30 мин — повышенный приоритет (всем админам)           [●─] Включено       │
│   60 мин — критично (эскалация в CSM)                    [●─] Включено       │
│   120 мин — риск потери клиента                          [●─] Включено       │
│                                                                              │
│ ▸ Сводки                                                                     │
│   Ежедневная (09:00) ⓘ                                   [●─] Включено       │
│   Еженедельная (понедельник 09:00)                       [●─] Включено       │
│                                                                              │
│ ▸ Операционные алерты                                                        │
│   Оплата не прошла                                       [●─] Включено       │
│   Ошибки синхронизации YClients                          [─○] Выключено      │
│   Сводка предложений из «Учёбы» (понедельник)            [●─] Включено       │
│                                                                              │
│ ── E-mail (резервный канал) ──                                               │
│ karina@studio.ru                                                             │
│                                                                              │
│ ▸ Сводки                                                                     │
│   Ежедневная                                             [─○] Выключено      │
│   Еженедельная                                           [●─] Включено       │
│                                                                              │
│ ▸ Критичные                                                                  │
│   Оплата не прошла / безопасность                        [●─] Включено       │
│                                                                              │
│ ▸ Документы                                                                  │
│   Ежемесячный PDF-счёт                                   [●─] Включено       │
│                                                                              │
│ ── Тихие часы ──                                                             │
│ Не отправлять обычные уведомления в это время:                               │
│ С [22:00 ▾]  По [08:00 ▾]   Часовой пояс: Москва (из настроек салона)        │
│                                                                              │
│ ☐ Критичные уведомления игнорируют тихие часы                                │
│   (оплата, SLA 60+ мин — отправятся всё равно)                               │
│                                                                              │
│ ── Режим отпуска ──                                                          │
│ Временно отключить все проактивные уведомления                               │
│ [─○] Выключено                                                               │
│                                                                              │
│ ── ──                                                                        │
│ [Сбросить к рекомендуемым]                              [Отменить] [Сохранить]│
└──────────────────────────────────────────────────────────────────────────────┘
```

### Owner overview sub-tab

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Мои уведомления │ ► Команда (Owner)                                          │
├──────────────────────────────────────────────────────────────────────────────┤
│ Обзор настроек уведомлений команды                                           │
│ Только для просмотра — каждый админ настраивает свои сам.                    │
│                                                                              │
│ ┌───────────┬─────────┬──────┬──────┬─────────────┬───────────┬──────────┐ │
│ │ Сотрудник  │ MAX SLA │Digest│Email │ Тихие часы  │ Отпуск     │ Обновлен││
│ ├───────────┼─────────┼──────┼──────┼─────────────┼───────────┼──────────┤ │
│ │ Karina (Я) │ all 4   │ d+w  │ w    │ 22-08       │ off        │ сегодня ││
│ │ Аня        │ all 4   │ d    │ off  │ 23-07       │ off        │ 14 мая  ││
│ │ Иван       │ 30/60   │ off  │ off  │ 22-08       │ ⚠ до 25.05 │ 16 мая  ││
│ │ Маша       │ 15 only │ off  │ off  │ 21-09       │ off        │ онбординг││
│ └───────────┴─────────┴──────┴──────┴─────────────┴───────────┴──────────┘ │
│                                                                              │
│ ⓘ Маша никогда не редактировала настройки — отправьте reminder?              │
│   [Отправить подсказку в MAX]                                                │
└──────────────────────────────────────────────────────────────────────────────┘
```

Owner sees matrix but CANNOT edit other admins' settings — only «remind» nudge.

### Mobile layout
Sub-tabs become segmented control sticky-top. Sections accordion-collapsed by default (only «MAX-бот» auto-open). Toggle rows 56dp tall.

### Vacation mode interaction

When toggled on:

```
┌─────────────────────────────────────────┐
│ Режим отпуска                           │
│ ────────────                            │
│                                          │
│ Все проактивные уведомления будут       │
│ выключены до:                            │
│                                          │
│ ⦿ Конкретная дата    [25 мая ▾]         │
│ ◯ До отключения вручную                  │
│                                          │
│ ☐ Перенаправлять SLA-эскалации на:       │
│   [Аню (admin) ▾]                        │
│                                          │
│ ⚠ Критичные уведомления (оплата,         │
│   безопасность) будут приходить всё      │
│   равно.                                 │
│                                          │
│ [Отмена]                  [Включить]    │
└─────────────────────────────────────────┘
```

When active, banner persists on SH3 top: «Режим отпуска активен до 25 мая · Отключить».

### States

- **VIEWING** — current settings loaded, all toggles + slider rendered
- **DIRTY** — any control changed; save bar appears at bottom with «Сохранить» + «Отменить»
- **SAVING** — toggle interaction disabled, 300–800ms spinner on save button
- **SAVED** — toast «Настройки сохранены» 2s; transitions back to VIEWING
- **DISCARD_CONFIRM** — if DIRTY + click Отменить → modal «Изменения не сохранены. Сбросить?»
- **VACATION_ACTIVE** — banner top, controls dimmed except «Отключить»
- **MAX_BOT_NOT_LINKED** — if admin hasn't bound their @MAX account, big banner «Свяжите MAX-бот чтобы получать пуши» + deeplink to bind flow
- **OFFLINE** — toggles disabled, banner «Нет связи — изменения сохранятся при восстановлении»

### Edge cases

- **Admin removed from team mid-edit** — save returns 403; redirect to SH1 with «Доступ отозван»
- **MAX bot DM blocked by admin** — show banner «MAX-бот не может вам писать. Откройте чат @studio_karina_bot и нажмите Start.» + retry-detect
- **Quiet hours wrap midnight** — UI validates from > to means «overnight»; visualises as bar diagram
- **Daily digest 09:00 falls inside quiet hours** — explicit ⓘ tooltip: «Сводка отправится в начале активных часов»
- **All notifications off** — warning banner «Вы отключили все уведомления — рискуете пропустить срочные диалоги» + nudge to enable critical-only

### Interactions

- Toggle: optimistic (visual update instant, save on Сохранить — no auto-save to allow batch edits)
- Slider for quiet hours: thumb step 15min, current value shows below
- Sub-tab: instant switch, no save prompt (dirty state per-tab? no — single save)
- «Сбросить к рекомендуемым»: confirm modal → resets to role-default table from this doc

### Tokens

- toggle-on: `--color-accent-500`
- toggle-off: `--color-neutral-400`
- toggle-track-radius: `--radius-pill`
- section-divider: `--color-border-subtle` + 32px vertical space
- save-bar-bg: `--color-bg-elevated` (sticky bottom)

### A11y

- Each toggle: `<button role="switch" aria-checked="true|false">` with visible label
- Toggle keyboard: Space toggles, Enter same
- Slider for quiet hours: native `<input type="time">` x2 (start, end)
- Section headers: `<h3>` with associated `aria-describedby` 1-liner
- Save bar: announced on appearance via `role="status"`; «Изменения не сохранены»
- Vacation banner: `role="status"` (polite live region)

---

## 8. Screen SH4 — Conversation Policy Aggregator

### Purpose
Owner-facing summary of the invisible AI ↔ human policy. **Mostly read-only** because per OP3 the 4 roles + tier mappings are fixed MVP. SH4 makes the policy *visible and trustable* without making it editable.

### Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Настройки → Политика разговоров                                              │
│ ────────────                                                                 │
│ Как помощник и команда делят ответы клиентам.                                │
│ Большая часть параметров — общие для MVP; настраиваются индивидуально через  │
│ Голос помощника. Развёрнутый редактор появится в v1.1.                       │
├──────────────────────────────────────────────────────────────────────────────┤
│ ── Идентичность помощника ──                                                 │
│ Один помощник — один голос. Имя, тон, аватар — настраиваются в Голосе.       │
│ Текущее имя: Помощница студии Карина                                         │
│ [Перейти к Голосу →]                                                         │
│                                                                              │
│ ── Уровни управления (3 tier-а) ──                                           │
│                                                                              │
│ ┌────────────────────────────────────────────────────────────────────────┐  │
│ │ Tier 1 — AI_CONTINUITY (помощник работает сам)                         │  │
│ │ Применяется к: out_of_catalog, low_confidence, booking_edge_case,      │  │
│ │ multiple_failures, price_question_high_intent, client_ready_to_book    │  │
│ │ Поведение: помощник пишет и отправляет сам; админ видит для контекста. │  │
│ ├────────────────────────────────────────────────────────────────────────┤  │
│ │ Tier 2 — HUMAN_SUPERVISED (черновик → утверждение)                     │  │
│ │ Применяется к: vip_flagged, returning_client (edge), schedule_conflict,│  │
│ │ payment_issue (non-refund), explicit_human_request (calm)              │  │
│ │ Поведение: помощник пишет черновик; админ читает и отправляет.         │  │
│ ├────────────────────────────────────────────────────────────────────────┤  │
│ │ Tier 3 — HUMAN_LOCKED (помощник молчит)                                │  │
│ │ Применяется к: complaint_sentiment, sensitive_topic, medical_*,        │  │
│ │ payment_issue (refund), explicit_human_request (charged)               │  │
│ │ Поведение: только админ; помощник не помогает даже черновиком.         │  │
│ └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│ ⓘ Маппинг причина-перевода → уровень — фиксированный в MVP.                  │
│ Индивидуальная настройка появится в v1.1 (per OP3).                          │
│                                                                              │
│ ── Сроки эскалации SLA ──                                                    │
│                                                                              │
│ 15 мин   мягкое напоминание           [фиксированно MVP]                     │
│ 30 мин   приоритет (все админы)       [фиксированно MVP]                     │
│ 60 мин   эскалация в CSM              [фиксированно MVP]                     │
│ 120 мин  риск потери клиента          [фиксированно MVP]                     │
│ 24 ч     авто-сброс с извинением      [фиксированно MVP]                     │
│                                                                              │
│ ⓘ Индивидуальные пороги для премиум-салонов — v1.1 (per OP2).                │
│                                                                              │
│ ── Политика explicit-human ──                                                │
│ Что делать когда клиент просит «человека»:                                   │
│ Текущая стратегия: tier-aware (calm → supervised, charged → locked)          │
│ [Изменить в Голосе → Политика →]                                             │
│                                                                              │
│ ── Удаление данных клиента ──                                                │
│ Клиент имеет право удалить свои данные (GDPR-like, ФЗ-152).                  │
│ Процесс: клиент пишет на support@platform.ru — мы проверяем и удаляем за 30  │
│ дней. Аудит сохраняется per Layer 2 retention policy.                        │
│ ⓘ Self-serve удаление из профиля клиента появится в v1.1.                    │
│                                                                              │
│ ── Качество персоны (review) ──                                              │
│ Каждое сообщение проверяется на запретные фразы + соответствие тону до       │
│ отправки. Нарушения логируются в Аудит.                                      │
│ [Открыть Аудит → Качество →]                                                 │
│                                                                              │
│ ── Роли в команде ──                                                         │
│ MVP: 4 фиксированных роли (Owner / Admin / Receptionist / Master).           │
│ Конструктор кастомных ролей — v1.1 (per OP3).                                │
│ [Управление командой →]                                                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

### States

- **Default** — sections rendered, all data static
- **Loading** — minimal; only the «текущее имя помощника» row + «текущая стратегия explicit-human» row fetch from API; rest is static markdown-like content
- **Empty** — N/A
- **Error** — minor; if persona fetch fails, show placeholder «Имя помощника недоступно — обновите страницу»

### Interactions
- Every «[Перейти к Х →]» button routes to source-of-truth module
- Tier cards: hover shows full reason list as tooltip (overflow); click no-op (read-only)
- ⓘ icons: tooltip explains why frozen MVP

### A11y
- Tier cards: `<article aria-labelledby>` so screen-reader reads tier name first
- «фиксированно MVP» badge: `aria-label="Не редактируется в текущей версии"`

---

## 9. Cross-screen integration

### SH1 → sub-modules (link map)

| SH1 row | Routes to | Module |
|---|---|---|
| Помощник · Голос | `/settings/persona` | Persona Editor P1 |
| Помощник · Политика | `/settings/policy` | SH4 (this doc) |
| Помощник · Учёба | `/conversations/learning` | Learning Queue C4 |
| Каталог | `/settings/catalog` | (existing onboarding 4b) |
| Услуги | `/settings/services` | (existing) |
| Мастера | `/settings/masters` | Master Management MM1 |
| Расписание | `/settings/schedule` | Schedule Management S1 |
| Уведомления · Мои | `/settings/notifications` | SH3 (this doc) |
| Уведомления · Команда | `/settings/notifications/overview` | SH3 overview tab |
| Уведомления · Аудит | `/settings/audit` | SH2 (this doc) |
| Биллинг · Тариф | `/settings/billing` | Onboarding Phase 12 Screen 12 |
| Биллинг · Платёжный метод | `/settings/billing/payment-method` | Phase 12 sub-screen |
| Биллинг · История | `/settings/billing/history` | Phase 12 sub-screen |
| Биллинг · Налоговый профиль | `/settings/billing/tax-profile` | Phase 12 + Q14 |
| Интеграции · YClients | `/settings/integrations/yclients` | Onboarding Phase 4a |
| Интеграции · Каналы | `/settings/integrations/channels` | Onboarding Phase 3 |
| Интеграции · API | `/settings/integrations/api` | placeholder v1.1 |
| Аккаунт · Профиль | `/settings/account` | new (basic CRUD, scope of onboarding) |
| Аккаунт · Салон | `/settings/account/tenant` | Onboarding Phase 2 retroactive edit |
| Аккаунт · Деактивация | `/settings/account/deactivate` | dedicated flow (Owner) |

### SH2 ↔ all modules (audit emission)
Every other module emits audit events that surface in SH2. The list above must be kept in lock-step. When a new event type is added in `conversation-ownership-policy §5`, it MUST get a display-name row in §6 of this doc (event type catalog).

### SH3 ↔ Master Management MM2 + Onboarding Phase 5
- New admin invited via MM2 → on first MAX-bot link, SH3 defaults applied per role
- Owner during onboarding Phase 5 gets a 1-screen nudge «Настроить уведомления → SH3» (skippable)

### SH4 ↔ Persona Editor + Master Management
- SH4 «Изменить в Голосе» buttons deep-link to specific Persona Editor sub-tabs
- SH4 «Управление командой» button deep-links to MM1

---

## 10. Backend contracts

### SH2 audit log queries

```
GET /api/v1/audit/events
  Query params:
    from: ISO datetime (default: now - 7d)
    to: ISO datetime (default: now)
    actor_ids[]: array of admin_id or "ai" or "system"
    event_types[]: array per §5 event catalog
    target_type: "conversation" | "customer" | "booking" | "persona" | null
    target_id: string | null
    hash_search: string | null (matches event_id prefix OR content_hash OR phone_hash)
    cursor: opaque pagination token (cursor-based, NOT offset)
    limit: int, max 100, default 50

  Auth: Owner only (403 for all others)

  Response:
    {
      "events": [
        {
          "event_id": "evt_01HXKZ8R9MNQ…",
          "event_type": "conversation.phone_revealed",
          "category": "sensitive",
          "occurred_at": "2026-05-18T14:55:48Z",
          "actor": { "id": "anya", "role": "admin", "display_name": "Аня", "deleted": false },
          "target": { "type": "conversation", "id": "conv_4d12…", "display": "Анастасия К." },
          "payload": { … structured per event_type … },
          "related_event_ids": ["evt_…", "evt_…"]
        }
      ],
      "next_cursor": "…opaque…",
      "total_estimate": 24
    }

  Performance: < 1s for ≤ 50 results on typical tenant (< 10K events); falls back to background job at > 5s.
```

```
POST /api/v1/audit/export
  Body:
    {
      "format": "csv" | "json",
      "filters": { …same as GET query… },
      "include_hashes": bool
    }
  Auth: Owner only.

  Response (sync, ≤ 100K events):
    Content-Type: application/octet-stream
    Content-Disposition: attachment; filename="audit-2026-05-18.csv"
    (body)

  Response (async, > 100K events):
    202 Accepted
    { "job_id": "exp_…", "estimated_seconds": 120, "delivery": "max_bot_dm" }

  Side effect: emits `conversation.export_initiated` audit event before responding.
```

```
GET /api/v1/audit/events/{event_id}
  Auth: Owner only.
  Response: single event object + related_events expanded (full payloads).
```

### SH3 notification preferences

```
GET /api/v1/notifications/preferences
  (returns own preferences for caller)
  Response:
    {
      "admin_id": "karina",
      "max_bot": {
        "master_change_request": bool,
        "sla_15": bool, "sla_30": bool, "sla_60": bool, "sla_120": bool,
        "daily_digest": bool, "weekly_digest": bool,
        "payment_failed": bool, "yc_sync_errors": bool,
        "learning_queue_weekly": bool
      },
      "email": {
        "daily_digest": bool, "weekly_digest": bool,
        "critical_alerts": bool, "monthly_invoice_pdf": bool
      },
      "quiet_hours": { "from": "22:00", "to": "08:00", "tz": "Europe/Moscow",
                       "bypass_critical": bool },
      "vacation": { "enabled": bool, "until": ISO date | null,
                    "redirect_sla_to_admin_id": string | null },
      "updated_at": ISO
    }
```

```
PATCH /api/v1/notifications/preferences
  Body: partial of above shape
  Auth: own preferences only (Owner cannot edit other admins')
  Side effect: emits `notifications.preferences_updated` audit event
```

```
GET /api/v1/notifications/preferences/overview
  Auth: Owner only.
  Response: array of all admins' preferences (summarized).
```

### SH4 conversation policy (read-only)

```
GET /api/v1/policy/summary
  Response:
    {
      "tiers": [ … static from §1/§2 of conversation-ownership-policy … ],
      "sla_thresholds_min": [15, 30, 60, 120, 1440],
      "explicit_human_strategy": "tier_aware",
      "persona": { "name": "Помощница студии Карина", "tone": "warm" },
      "roles": { "fixed": ["owner", "admin", "receptionist", "master"] },
      "frozen_until": "v1.1"
    }
```

---

## 11. A11y (cross-screen)

- WCAG 2.2 AA across all 4 screens
- Contrast ≥ 4.5:1 body, ≥ 3:1 large text — token palette enforces
- Touch targets ≥ 44pt iOS / 48dp Android / 24×24 CSS web
- Visible focus ring on every interactive (toggle, button, link, table row, filter chip)
- Keyboard nav:
  - SH1: Tab through section rows; Enter routes
  - SH2: ↓/↑ between table rows; Enter opens drawer; Esc closes
  - SH3: Tab through toggles; Space toggles; Tab through quiet-hour pickers
  - SH4: Tab through links; Enter routes
- Screen reader walk-throughs documented for SH2 detail drawer (most complex screen)
- Reduced motion: all transitions ≤ 200ms or removed entirely with `prefers-reduced-motion: reduce`
- Mobile reflow tested at 360px, 412px, 768px, 1024px+ breakpoints
- Russian-language ARIA labels (per `lang="ru"` document root)
- Audit log table: row count announced to screen reader on filter change («Найдено 24 события»)

---

## 12. Edge cases (cross-screen)

| Edge | Behavior |
|---|---|
| Owner permission revoked mid-session | Next API call returns 403 → redirect to SH1 with banner + force re-fetch |
| Tenant deactivated | All settings routes return 410 Gone → redirect to deactivation status page |
| MAX bot DM blocked by admin | Banner on SH3 + MAX section toggles greyed with «Откройте чат с ботом сначала» |
| Audit log query returns 0 with default filter | «За последние 7 дней нет событий» + suggest «Расширить до 30 дней» |
| Audit log millions of events | Cursor pagination; query timeout 5s → suggest filter narrowing; export → background job |
| Notification settings reset by support | Audit event `notifications.reset_by_support` with reason; admin sees banner on next visit |
| Quiet hours overlap with daily digest schedule | Tooltip explains: «Сводка отправится в 08:00 — после тихих часов» |
| Vacation mode auto-expires | Cron-driven; emits `notifications.vacation_ended` audit; banner clears on next visit |
| Tenant timezone change | SH3 quiet hours interpret in tenant TZ; banner warns if browser differs > 1h (per Q-AD6) |
| Search synonym mismatch on SH1 | Search «бот» → inline hint «Используется термин помощник» + no false matches |
| Two admins simultaneously edit own SH3 | No conflict (per-admin records); each saves independently |
| Owner tries to edit another admin's SH3 | Read-only matrix view only; «Нудж» button is the only write action |
| Audit event from anonymized customer (180d+ past) | Target shows UUID + banner «Профиль анонимизирован»; phone_hash still queryable |
| Audit event from deleted admin | Display name suffix «(удалён)»; audit retention enforced |
| Export of huge range times out | Background job + MAX-bot DM delivery (uses SH3 preferences) |
| Critical alert during quiet hours | Bypass if `bypass_critical=true` (default for Owner); regular alert delayed |
| Master role visits `/settings` URL directly | Redirect to `/master/profile` with toast «Эта страница для администраторов» |
| Receptionist visits `/settings/audit` | 403 page «Журнал аудита доступен только владельцу» + back link |

---

## 13. Anti-slop scan (12-point check)

| # | Check | Result |
|---|---|---|
| 1 | No default Inter + violet gradient + glassmorphic cards | ✅ — uses tenant design tokens (already non-slop per onboarding doc); SH cards are solid neutral elevated, no gradients |
| 2 | Aesthetic direction stated with reasoning | ✅ — utilitarian dense-info dashboard for desktop, single-column reflow on mobile; matches existing admin web (no decorative flourish) |
| 3 | No fake AI «sparkle» icons | ✅ — sparingly: ⓘ for tooltips, ⚠ for warnings, ✓ for verified state; no ✨ |
| 4 | Real data shown, no «Lorem ipsum» | ✅ — every example uses Karina/Аня/conv_4d12 etc. realistic personas |
| 5 | Hierarchy via type weight + spacing, not 6 colors | ✅ — 2-color hierarchy: ink primary + muted; accent only for CTAs |
| 6 | No card-soup (3+ nested cards) | ✅ — max 2 nesting levels (section card → tier card on SH4) |
| 7 | No «glass» translucency | ✅ — opaque surfaces only |
| 8 | Touch targets ≥ platform min | ✅ — toggles 32px, rows 56dp mobile |
| 9 | No micro-animation everywhere | ✅ — animation only on drawer slide (250ms), toast (200ms), prefers-reduced-motion respected |
| 10 | Russian copy reviewed for «AI-ese» | ✅ — passes scan: «Хранится 365+ дней per Layer 2 retention» — admin reading «per Layer 2» is intentional doc-cross-reference; could shorten to «по политике хранения» in final copy review |
| 11 | No empty-state stock illustration | ✅ — empty states use text + actionable suggestion |
| 12 | Single assistant identity preserved | ✅ — «помощник» throughout; «бот» appears only as internal engineering term in audit event names + payload — never customer-facing |

---

## 14. Permissions matrix (per screen per role)

Cross-reference to `conversation-ownership-policy §4`. SH-screen rules:

| Capability | Owner | Admin | Receptionist | Master |
|---|---|---|---|---|
| Open SH1 | ✅ full sections | ✅ filtered | ✅ minimal | redirect out |
| Open SH2 audit viewer | ✅ | ❌ (own actions visible in profile only) | ❌ | ❌ |
| Export audit | ✅ | ❌ | ❌ | ❌ |
| Edit own SH3 | ✅ | ✅ | ✅ | uses Master Mobile §M4 |
| View SH3 team overview | ✅ | ❌ | ❌ | ❌ |
| Nudge another admin's SH3 | ✅ | ❌ | ❌ | ❌ |
| Open SH4 policy aggregator | ✅ | ✅ read-only | ❌ | ❌ |
| Edit policy (none — frozen MVP) | ❌ (all frozen MVP) | ❌ | ❌ | ❌ |
| Edit persona (linked from SH4) | ✅ | ❌ | ❌ | ❌ |
| Edit team (linked from SH4) | ✅ | ❌ | ❌ | ❌ |
| Open Billing rows on SH1 | ✅ | hidden | hidden | hidden |
| Open Tax profile | ✅ | hidden | hidden | hidden |
| Open Tenant info | ✅ | read-only | hidden | hidden |
| Tenant deactivation | ✅ confirm-gated | hidden | hidden | hidden |

Sensitive note: SH2 audit log includes events involving PII (phone hash, customer name). Owner has full access; this is intentional and consistent with `conversation-ownership-policy §4` («View audit log = Owner»). Audit event for SH2 opens are not emitted (would be infinite recursive logging); export downloads ARE audited.

---

## 15. Open questions (Q-SH prefix)

| # | Question | Lean | Urgency | Owner |
|---|---|---|---|---|
| **Q-SH1** | Search synonym dictionary on SH1 — maintained where? Hardcoded MVP, admin-tunable later? | Hardcoded MVP (50 synonyms); revisit if analytics shows zero-result rate > 20% | 🟢 v1.1 | PM |
| **Q-SH2** | Audit log retention beyond 365d for payment events — what's the actual ceiling? | Batch with OP4 legal review; lean: align with Layer 3 booking retention = 7 years for payment-tagged audit events | 🔴 pre-launch | Legal + Founder |
| **Q-SH3** | Audit export of payloads with content_hash but NOT plaintext — sufficient for forensic? Or sometimes need redacted-with-context? | Hash-only MVP per Layer 2 spec; if support tickets surface need, add «restricted CSM export» with explicit reason field | 🟡 soon | Legal + Eng |
| **Q-SH4** | SH3 Owner overview matrix — does Owner need ability to *force-reset* another admin's prefs (e.g., if admin maliciously muted SLA)? | Nudge MVP; force-reset behind Owner confirm + audit; v1.1 | 🟢 v1.1 | PM |
| **Q-SH5** | Quiet hours per-day-of-week (e.g., weekend longer)? | Single range MVP; per-day v1.1 if requested | 🟢 v1.1 | PM |
| **Q-SH6** | Vacation mode auto-rerouting SLA to another admin — what if that admin also in vacation? | Cascade fallback to Owner; if Owner also in vacation → critical alerts bypass vacation entirely | 🟡 before ship | PM + Eng |
| **Q-SH7** | API access placeholder («Скоро» chip) — what's the MVP scope? Webhook outbound only? REST inbound? | Outbound webhook on key events (booking created, conversation handoff) MVP; full REST v1.2 | 🟢 v1.1+ | Eng + Founder |
| **Q-SH8** | Tenant deactivation flow — soft pause (1 month) vs hard close vs export-then-close path? | Three modes: pause (≤ 90d auto-reactivable), archive (read-only forever), full close (with 30d soft-delete grace) — needs full handoff doc | 🟡 before churn risk materializes | PM + Legal |
| **Q-SH9** | Audit log entry for SH2 viewer opens themselves — log it? | NO (infinite recursion risk + low value); log exports only | ✅ decided in this doc | — |
| **Q-SH10** | Search-by-hash forensic flow — surface case where hash is partial / wrong format? | Min 6 chars; show inline «Не похоже на ID — попробуйте полный»; soft-validate | 🟢 polish | UX |
| **Q-SH11** | Email digests when admin opted into MAX digest — duplicate or skip? | Skip if both on (de-dup at delivery layer); explicit tooltip on SH3 | 🟡 before SH3 ship | Eng + PM |
| **Q-SH12** | Master role's «Settings» visibility — single link to own profile, or completely hidden in nav? | Hidden in nav; deeplink to `/settings/account` for master routes to Master Mobile §M4 | ✅ decided in this doc | — |
| **Q-SH13** | SH4 frozen-policy badge copy — «фиксированно MVP» vs «появится в v1.1» vs other? | «фиксированно MVP» on chip + ⓘ tooltip explains v1.1 plan; final wording — copy review | 🟢 polish | UX + copy |
| **Q-SH14** | Audit log row click on conversation target — opens conv in new tab or same? | Same tab default (most common case); cmd-click for new tab (browser-native) | ✅ decided | — |
| **Q-SH15** | Export CSV column ordering convention — strict spec? | Yes: `event_id, occurred_at, event_type, actor_id, actor_role, target_type, target_id, payload_json` — frozen for hash continuity across versions | 🟡 before audit ship | Eng |

---

## 16. Phased delivery

### Phase 1 (MVP, ship with onboarding GA)
- ✅ SH1 homepage with all role-aware sections
- ✅ SH3 notification preferences (own + Owner overview)
- ✅ SH4 policy aggregator (fully read-only)
- ✅ Deep links to all existing sub-modules
- ✅ Mobile responsive on all 4 screens
- ✅ Search with synonym dict on SH1

### Phase 2 (1–2 weeks post-MVP)
- ✅ SH2 audit log viewer (full filtering)
- ✅ Audit export CSV
- ✅ Sensitive event markers + detail drawer

### Phase 3 (3–4 weeks)
- ✅ Audit export JSON
- ✅ Background export job + MAX-bot delivery
- ✅ Search-by-hash forensic
- ✅ Audit event display-name catalog completeness audit (cross-check with §5 of policy doc)

### v1.1 (post-MVP)
- Per-tenant SLA threshold editing (per OP2)
- Custom role editor (per OP3) — would unlock SH4 editable mode
- Per-day-of-week quiet hours (Q-SH5)
- Vacation mode cascade fallback (Q-SH6)
- API access activation (Q-SH7)
- Tenant deactivation full flow (Q-SH8)
- Customer self-serve data deletion (referenced in SH4)
- Owner force-reset of admin notification settings (Q-SH4)

### Deferred
- SH4 editable policy mappings (depends on OP3 custom roles)
- PDF audit export (explicitly out per OP5)
- Real-time audit feed (out of scope; SH2 is forensic, not live)

---

## 17. Sign-off

### Pre-ship completion checklist (per ux-architect SKILL §completion)

- [x] Mode declared: handoff
- [x] Target surfaces identified: web dashboard primary, MAX bot DM secondary
- [x] JTBDs stated: Owner findability, Owner audit, Admin/Receptionist notifications
- [x] All states designed: VIEWING/DIRTY/SAVING/SAVED/empty/loading/error/offline/permission-denied
- [x] Platform-native: native `<input type="date">`, `<input type="time">`, `<table>` semantics
- [x] Contrast checked via token palette (≥ 4.5:1 body, ≥ 3:1 large)
- [x] Touch targets ≥ platform min
- [x] Keyboard nav defined
- [x] Visible focus state specified
- [x] Reduced-motion fallback specified
- [x] Anti-slop scan passed (§13)
- [x] N/A — not a Mini App
- [x] Tokens referenced; redlines via measurement annotations in layouts; a11y checklist §11

### Reviewers needed before ship

| Reviewer | Focus |
|---|---|
| Founder | Q-SH2 (payment audit retention), Q-SH7 (API scope), Q-SH8 (deactivation flow scope) |
| PM | Q-SH1, Q-SH4, Q-SH5, Q-SH6, Q-SH11 (notification UX nuances) |
| Eng lead | Q-SH3 (export payload spec), Q-SH15 (CSV ordering), audit query perf + cursor pagination |
| Legal | Q-SH2 (payment audit), Q-SH3 (export redaction policy), Q-SH8 (deactivation legal flow) |
| QA | Cross-role permission matrix execution (every cell of §14 needs a test case) |

### Definition of done (Phase 1 ship)

- All 4 screens (SH1/SH3/SH4 fully; SH2 stub with «Скоро» chip OK if Phase 1 ships before Phase 2)
- Per-role visibility enforced at API + UI both
- Every event in `conversation-ownership-policy §5` is reachable via SH2 filter (when SH2 ships in Phase 2)
- CSV export schema frozen per Q-SH15 decision before first export ships
- Mobile reflow verified at 360/412/768/1024+ breakpoints
- Russian copy reviewed for «AI-ese» (post-implementation copy pass)
- Audit immutability enforced at DB level (no UPDATE/DELETE granted on audit table for any application user)

— END —
