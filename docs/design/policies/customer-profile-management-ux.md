# Customer Profile Management UX — Mini App «Профиль» tab

**Date:** 2026-05-19 r1
**Status:** Foundational — unblocks Phase 1 4d / Phase 2 customer profile screens + OP6 deletion request UX
**Reads:** [`information-architecture.md`](./information-architecture.md), [`notification-preferences-ux.md`](./notification-preferences-ux.md), [`wellness-input-modules.md`](./wellness-input-modules.md), [`conversation-ownership-policy.md`](./conversation-ownership-policy.md), [`product-ux-vision.md`](./product-ux-vision.md), [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md), [`../handoffs/2026-05-18-customer-first-time-handoff.md`](../handoffs/2026-05-18-customer-first-time-handoff.md)

> «Профиль» tab in customer Mini App. Read/edit personal info, view visit history, manage wellness modules + notifications, privacy & data controls (including OP6 deletion request + data export), help. Consolidates customer-facing self-service.

---

## 0. Why this exists

### The gap

Customer profile referenced in many specs but never designed in detail:
- [`information-architecture.md`](./information-architecture.md) — «Профиль» is one of 5 Mini App surfaces, but content unspecified
- [`../handoffs/2026-05-18-customer-first-time-handoff.md`](../handoffs/2026-05-18-customer-first-time-handoff.md) — F-screens reference profile, but no detail
- [`notification-preferences-ux.md`](./notification-preferences-ux.md) §10 — settings live at Профиль → Уведомления
- [`wellness-input-modules.md`](./wellness-input-modules.md) — modules activate from profile but flow undesigned
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) OP6 — customer-deletion process locked as «email support@» but NO customer-facing UX
- [`decisions-log.md`](../decisions-log.md) OP6 — «self-serve deletion UX deferred to v1.1» — но какой MVP minimum?

Result: 4d engineering will improvise profile structure. OP6 deletion remains hidden in support email burden. Wellness module activation discoverability poor.

### The promise

Single source for Mini App «Профиль» tab:
- 6 sections with read + edit patterns
- OP6 deletion request flow (customer-initiated, support-mediated MVP)
- Data export request flow
- Block bot / opt-out everything path
- Privacy controls hub
- Cross-doc reconciliation (this doc is canonical for profile structure; others link here)

---

## 1. Scope

### IN
- Mini App «Профиль» tab structure (6 sections)
- Personal info section (name display + optional phone editing)
- Visit history section (last 10 + load more)
- Wellness modules section (activate / deactivate per [`wellness-input-modules.md`](./wellness-input-modules.md))
- Notifications section (deeplink + summary per [`notification-preferences-ux.md`](./notification-preferences-ux.md))
- Privacy & data section (data export, account deletion, conversation history)
- Help section (FAQ link, contact studio, about assistant)
- OP6 deletion request flow (customer-initiated; support-verified per [`decisions-log.md`](../decisions-log.md))
- Data export request flow (Phase 2+ self-serve; MVP support-mediated)
- Block/unblock bot path

### OUT
- Owner / master / admin profile management (separate scope)
- Customer wellness profile detailed view (Phase 2+ — when wellness modules accumulate, separate dashboard)
- Account creation / login flow (MAX auth-handled)
- Multi-tenant profile linkage (separate per Q-CO5; v1.1+)
- Self-serve account deletion automation (Phase 2+ per OP6 lean — MVP is support-mediated)
- Payment methods / billing (customer doesn't pay in MVP; salon pays platform)
- Account recovery flow (handled by MAX platform)

---

## 2. «Профиль» tab top-level structure

```
┌──────────────────────────────────────┐
│ Профиль                               │
├──────────────────────────────────────┤
│ ┌────────────────────────────────┐   │
│ │ {{customer_name_or_max_handle}}│   │  ← header card
│ │ Клиент студии «{{salon_name}}» │   │
│ └────────────────────────────────┘   │
│                                      │
│ ──── Разделы ────                     │
│                                      │
│ 📋 Мои записи                    →    │
│    {{N}} активных, {{M}} в прошлом    │
│                                      │
│ 🌿 Самочувствие                  →    │
│    {{N_active_modules}} модулей       │
│                                      │
│ 🔔 Уведомления                   →    │
│    {{summary_one_line}}              │
│                                      │
│ 🛡 Приватность и данные          →    │
│    Управление вашими данными         │
│                                      │
│ ❓ Помощь                         →    │
│    FAQ, связаться со студией         │
│                                      │
│ ──── Помощник студии ────             │
│                                      │
│ Эту запись помогает вести AI-         │
│ помощник «{{salon_name}}».            │
│ [Подробнее →]                        │
│                                      │
└──────────────────────────────────────┘
```

### 2.1 Header card

Shows MAX-provided name OR last-known display name + tenant scope reminder («Клиент студии Х» reinforces tenant boundary per [Q-CO5](../decisions-log.md) separate profile per tenant).

NO photo on header (customer's MAX profile photo NOT shown — privacy, plus they didn't necessarily share with us). NO «edit profile» button at top — edits live in sub-sections.

### 2.2 Section icons + summary lines

Each section has a leading emoji icon (functional per [`master-conversational-templates.md`](./master-conversational-templates.md) emoji policy — allowed at start of structured cards), title, and one-line dynamic summary.

Summary lines stay short (≤ 5 words) — quick scan.

### 2.3 Footer disclosure

«Эту запись помогает вести AI-помощник» — honesty mandate per [`product-ux-vision.md §10`](./product-ux-vision.md). Single-assistant identity preserved («помощник студии», never «бот»). «Подробнее» opens info card §8.4.

---

## 3. Section 1 — Мои записи (visits)

### 3.1 Read view

```
┌──────────────────────────────────────┐
│ ← Мои записи                          │
├──────────────────────────────────────┤
│ ── Активные ──                        │
│                                      │
│ 📅 Завтра · ср · 14:00                │
│ Лимфодренаж · Маша                    │
│ [Открыть] [Перенести] [Отменить]      │
│                                      │
│ 📅 Через 2 нед · пт · 10:00           │
│ Стрижка · Лена                        │
│ [Открыть]                             │
│                                      │
│ ── Прошлые ──                         │
│                                      │
│ 📅 1 мая · вс · 14:00                 │
│ Классический массаж · Маша            │
│ [Записаться снова]                    │
│                                      │
│ 📅 14 апреля · вт · 16:00             │
│ Чистка лица · Ольга                   │
│ [Записаться снова]                    │
│                                      │
│ [Показать ещё →] (load 10 more)      │
└──────────────────────────────────────┘
```

### 3.2 Active vs past split
- Active: status ∈ {CONFIRMED, RESCHEDULE_REQUESTED, AFFECTED_BY_SCHEDULE_CHANGE}
- Past: status ∈ {COMPLETED, NO_SHOW, CANCELLED}

Each shows top 5 most recent (then «load more» pagination by 10s).

### 3.3 Per-booking actions
- Active: [Открыть] [Перенести] [Отменить] — links per [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md)
- Past: [Записаться снова] — pre-fills F1 catalog / F2 master with same service + master

### 3.4 Empty state
```
У вас пока нет записей.

[Записаться]
```

CTA deeplinks to F1 catalog. Per [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md) §7.3.

### 3.5 No edit
Customer cannot edit a booking from this list directly — they can OPEN booking detail (per [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) §3.4) which has actions. Profile is summary surface.

---

## 4. Section 2 — Самочувствие (wellness modules)

### 4.1 Read view

```
┌──────────────────────────────────────┐
│ ← Самочувствие                        │
├──────────────────────────────────────┤
│ Помощник может отмечать ваше          │
│ самочувствие и помогать подбирать     │
│ процедуры под состояние.              │
│                                      │
│ ── Активные модули ──                 │
│                                      │
│ ✓ Настроение                          │
│   Утренний 1-тап опрос                │
│   [Открыть] [Выключить]               │
│                                      │
│ ── Доступно для активации ──          │
│                                      │
│ ☐ Вода — напоминания пить             │
│   [Подключить →]                      │
│                                      │
│ ☐ Сон — оценка сна                    │
│   [Подключить →]                      │
│                                      │
│ ☐ Параметры тела — вес, талия         │
│   [Подключить →]                      │
│                                      │
│ ☐ Симптомы — дневник недомоганий      │
│   [Подключить →]                      │
│                                      │
│ ☐ Фото-прогресс — до/после            │
│   [Подключить →]                      │
│                                      │
│ ☐ Сканер еды — фото блюда → калории   │
│   [Подключить →]                      │
│                                      │
│ [Об инсайтах →]                       │
└──────────────────────────────────────┘
```

### 4.2 Activation
«Подключить →» opens module-specific consent dialog per the module's handoff (e.g., [`../handoffs/2026-05-19-wellness-mood-handoff.md`](../handoffs/2026-05-19-wellness-mood-handoff.md) §3 for Mood).

Deactivation right here in profile (one-tap «Выключить») — friction-free opt-out (consistent with notification-preferences §11.4).

### 4.3 «Об инсайтах»
Information modal explaining what insights mean + how data is used (customer-only) + privacy stance — read-only education.

### 4.4 Phase 1 reality
Only Mood module ships Phase 1. Other modules shown as «Скоро» (coming soon) with disabled state if engineering not ready, OR fully hidden in MVP and revealed as they land.

### 4.5 Forbidden
- ❌ «Включить все модули одной кнопкой» — each requires own consent
- ❌ Persuasive copy («Активируйте больше модулей для лучших инсайтов!»)
- ❌ Hide active-state count behind menu — always visible
- ❌ Module sort by «recommended for you» (could feel manipulative) — fixed alphabetical or category order

---

## 5. Section 3 — Уведомления (notifications)

### 5.1 Read view = deeplink to notification-preferences §3

This section is a small summary + entry point per [`notification-preferences-ux.md`](./notification-preferences-ux.md).

```
┌──────────────────────────────────────┐
│ ← Уведомления                         │
├──────────────────────────────────────┤
│ Помощник может писать:                │
│                                      │
│ ✓ Подтверждения и напоминания        │
│ ✓ Помощник пишет первым              │
│ ☐ Утренний опрос настроения          │
│                                      │
│ [Изменить →]                          │
└──────────────────────────────────────┘
```

«Изменить →» opens full preferences screen per [notification-preferences §3](./notification-preferences-ux.md#3-customer-side-notification-preferences).

### 5.2 Summary line on parent profile
- If «без проактивных» = OFF: «Получаю только напоминания»
- If «без проактивных» = ON: «Помощник пишет первым»
- If wellness modules also active: «Помощник пишет первым + {{N}} модулей самочувствия»

---

## 6. Section 4 — Приватность и данные (Privacy & data)

The trust-foundation section. Every privacy/data control lives here.

### 6.1 Read view

```
┌──────────────────────────────────────┐
│ ← Приватность и данные                │
├──────────────────────────────────────┤
│ ── Что хранится ──                    │
│                                      │
│ • История записей · 3 года            │
│ • Переписка с помощником · 180 дней   │
│ • Данные модулей самочувствия · видите│
│   только вы                           │
│ • Анонимные метрики · бессрочно       │
│                                      │
│ [Подробнее о хранении]                │
│                                      │
│ ── Действия ──                        │
│                                      │
│ 📥 Экспорт ваших данных               │
│    Получить копию всего, что у нас о  │
│    вас есть                           │
│    [Запросить →]                      │
│                                      │
│ 🚫 Прекратить общение                 │
│    Помощник перестанет писать первым  │
│    [Настроить →]                      │
│                                      │
│ 🗑 Удалить аккаунт                    │
│    Удалить вашу историю в этой студии │
│    [Подать запрос →]                  │
│                                      │
│ ── Связь со студией ──                │
│                                      │
│ По вопросам приватности — напишите    │
│ владельцу студии: {{owner_handle_or_  │
│ email}}                               │
│                                      │
└──────────────────────────────────────┘
```

### 6.2 «Подробнее о хранении» modal
Information-only modal explaining 4-layer retention per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) §6 in customer-friendly language. No legal jargon; plain RU.

### 6.3 Data export request flow (MVP support-mediated)

Tap «Запросить →» opens modal:

```
┌──────────────────────────────────────┐
│ Экспорт данных                        │
├──────────────────────────────────────┤
│ Мы соберём всё, что у нас о вас есть, │
│ и отправим вам в течение 30 дней.    │
│                                      │
│ Что будет в экспорте:                 │
│ • Все ваши записи и их статусы        │
│ • История переписки с помощником      │
│ • Данные модулей самочувствия         │
│ • Ваши заметки и оценки               │
│                                      │
│ Куда отправить?                       │
│ ◉ {{customer_max_handle}} (MAX)       │
│ ◯ Другой адрес                        │
│                                      │
│ [Отмена]   [Подать запрос]            │
└──────────────────────────────────────┘
```

After submit:
```
Готово. Запрос принят. Если что-то понадобится уточнить — напишу.

Срок: до 30 дней.
```

Emits event `customer.data_export.requested` (NEW — add to event-taxonomy §3.2). Routes to admin queue (per [`owner-conversational-templates.md`](./owner-conversational-templates.md) §6.3 escalation pattern). Admin / CSM exports manually MVP per [`decisions-log.md`](../decisions-log.md) OP6.

**Forbidden**:
- ❌ Auto-prepare export without rate limit (anti-abuse: 1 request per 90 days)
- ❌ Hide that processing takes time
- ❌ Make customer search email through inbox — deliver via same MAX channel as bot DM

### 6.4 «Прекратить общение» — deeplink to notification-preferences

Tap → opens [notification-preferences §3](./notification-preferences-ux.md#3-customer-side-notification-preferences) with «без проактивных» highlighted.

NOT a destructive «block bot forever» action. Calibrated opt-out via existing toggle. If customer wants TRUE bot block (no operational reminders at all), they go to next section §6.5.

### 6.5 Account deletion request flow (OP6 implementation)

Tap «Подать запрос →» opens warning modal:

```
┌──────────────────────────────────────┐
│ Удалить аккаунт?                      │
├──────────────────────────────────────┤
│ ⚠ Это действие нельзя отменить        │
│                                      │
│ Будут удалены:                        │
│ • Ваш профиль в студии «{{salon}}»   │
│ • История переписки с помощником      │
│ • Данные модулей самочувствия         │
│ • Ваши заметки и оценки               │
│                                      │
│ Останется на 30 дней (можно           │
│ восстановить, написав в студию), потом│
│ удалится навсегда.                    │
│                                      │
│ Записи в прошлом (для бухгалтерии     │
│ студии) хранятся 3 года в обезличенном│
│ виде согласно закону.                 │
│                                      │
│ Активные записи будут отменены.       │
│                                      │
│ ☐ Я понимаю, что это необратимо       │
│                                      │
│ [Не удалять]      [Подать запрос]    │
└──────────────────────────────────────┘
```

Customer must check the «Я понимаю» checkbox before «Подать запрос» enables.

After submit:
```
Запрос принят. {{owner_short_name}} получит уведомление и обработает его в течение 30 дней.

Если передумаете — напишите нам.
```

Emits event `customer.deleted_request` per [event-taxonomy §3.2](./event-taxonomy.md#32-customer-domain). Routes to admin queue for verification per OP6. After verification + 30-day grace → hard-delete.

If customer has active bookings: warning includes «Активные записи будут отменены» line. After admin processes deletion, system auto-cancels active bookings + notifies them per [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) §6.

**Forbidden**:
- ❌ Auto-delete without admin verification step
- ❌ «Точно-точно?» double-confirm modal (single confirmation + checkbox sufficient)
- ❌ Hide retention legal nuance (be transparent about 3-year operational record per ФЗ)
- ❌ Retention attempts («останьтесь, мы дадим скидку!»)

### 6.6 Owner contact line
«По вопросам приватности — напишите владельцу студии» links to bot DM with prepared «Вопрос по приватности» tag → routes to HUMAN_LOCKED per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md).

---

## 7. Section 5 — Помощь (Help)

### 7.1 Read view

```
┌──────────────────────────────────────┐
│ ← Помощь                              │
├──────────────────────────────────────┤
│ ── Частые вопросы ──                  │
│                                      │
│ • Как перенести запись?               │
│ • Как отменить запись?                │
│ • Помощник — это AI или человек?      │
│ • Где посмотреть свою историю?        │
│ • Как сменить студию?                 │
│                                      │
│ [Открыть FAQ →]                       │
│                                      │
│ ── Связаться со студией ──            │
│                                      │
│ Напишите помощнику или напрямую:      │
│ {{owner_max_handle}}                  │
│                                      │
│ [Написать в чат →]                    │
│                                      │
│ ── О помощнике ──                     │
│                                      │
│ AI-помощник студии «{{salon_name}}».  │
│ Версия: {{persona_version}}           │
│                                      │
│ [Подробнее →]                         │
└──────────────────────────────────────┘
```

### 7.2 FAQ static page
Read-only RU-text answering 5-7 most common customer questions. Per-tenant customizable (Phase 2+); platform-baseline MVP.

### 7.3 «Написать в чат» — direct bot DM
Deeplinks to MAX bot DM with empty composer. Customer just messages bot directly.

### 7.4 «Подробнее» about assistant
Modal:
```
┌──────────────────────────────────────┐
│ О помощнике                           │
├──────────────────────────────────────┤
│ Это AI-помощник студии «{{salon}}».   │
│                                      │
│ Я отвечаю на вопросы о услугах,       │
│ помогаю записаться, напоминаю о       │
│ визитах и могу подобрать процедуры    │
│ под ваше самочувствие.                │
│                                      │
│ Если что-то выходит за мои            │
│ компетенции — передаю владельцу или   │
│ мастеру.                              │
│                                      │
│ Ваши данные хранятся только в этой    │
│ студии. Подробнее — в разделе         │
│ «Приватность и данные».               │
│                                      │
│ [Понятно]                             │
└──────────────────────────────────────┘
```

Honesty mandate per [`conversational-ux-framework.md`](./conversational-ux-framework.md) §6.4.

---

## 8. Edit patterns

### 8.1 Personal info — minimal MVP

Phase 1 customer doesn't have editable personal info on profile (name comes from MAX; phone optional via [manual-booking §3.4](./manual-booking-spec.md) if admin added; no other writable fields).

Phase 2+ may add: nickname / preferred name / pronoun preferences. For now, header card is read-only.

### 8.2 Section edits live IN sections
Each section's «Изменить» / «Подключить» / «Выключить» action lives in that section. No global «Edit profile» mode.

### 8.3 Save semantics
- Toggle / radio: save immediately + 5-sec undo toast per [notification-preferences §11](./notification-preferences-ux.md#11-save--undo--rollback-patterns)
- Request flows (export, deletion): submit-only after explicit checkbox / confirmation

### 8.4 Conflict resolution
- Two-device edit conflict: last-write-wins; minor enough for customer scope
- Concurrent customer-vs-admin edit: admin changes (e.g., admin updates customer name from manual booking) override; customer sees toast «{{admin}} обновил ваши данные»

---

## 9. Edge cases

### 9.1 Customer with no booking history yet
Section 1 «Мои записи» empty state per §3.4. Section 2 «Самочувствие» shows all modules as activatable. Section 6 still functional (export of «nothing» returns empty but acknowledged).

### 9.2 Customer who opted out of bot («без проактивных» OFF + all wellness modules OFF)
Profile still works for reading visit history + privacy controls. Section 5 «Уведомления» summary shows «Получаю только напоминания». Section 4 «Самочувствие» shows all modules as inactive.

### 9.3 Customer whose tenant deactivated
If tenant is in PAUSED state (billing) → profile shows banner:
```
Сейчас студия временно не работает в системе.
Записи и история сохранены.
```
Read-only mode; no new actions until tenant restored.

### 9.4 Customer trying to delete account with active bookings
Deletion flow per §6.5 warns about active bookings cancellation. Admin reviews; deletion processes after admin verification + 30-day grace.

### 9.5 Customer in HUMAN_LOCKED conversation
Profile still accessible (it's customer's own data). Section 7 «Помощь» «Написать в чат» continues HUMAN_LOCKED conversation.

### 9.6 Customer wants to switch to different tenant (sibling salon)
Out of scope MVP (multi-tenant customer per Q-CO5). Customer must use other tenant's bot. Tells customer in Section 7 FAQ: «У каждой студии свой помощник в MAX — найдите бот нужной студии».

### 9.7 Customer's MAX account changed (handle / display name)
Profile reflects MAX-side current state on next load. History data stays as-was. No need for special UI; transparent.

### 9.8 Customer asks AI in chat «удали мои данные»
AI responds per [`conversational-ux-framework.md`](./conversational-ux-framework.md) and routes to §6.5 deletion flow:
```
Можете удалить данные из «Профиль → Приватность и данные → Удалить аккаунт». Открыть?

[Открыть]   [Не сейчас]
```

### 9.9 Customer requests data export 2x in 90 days
Rate limit per §6.3. Second request shows:
```
Прошлый запрос был {{N}} дней назад. Следующий — можно через {{M}} дней. Если срочно — напишите владельцу студии.
```

### 9.10 Customer deletes account, then returns later
After hard-delete (30+ days post-request), customer is treated as NEW DISCOVERED customer per [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md) §3. No personalization, no history.

If within 30-day soft-delete grace: customer can write owner asking to restore. Admin can undelete via admin tools.

---

## 10. Permissions matrix

| Action | Customer (self) | Customer (other) | Owner | Admin | Master |
|---|---|---|---|---|---|
| View own profile | ✅ | ❌ | ❌ | ❌ | ❌ |
| View other customer's profile | n/a | ❌ | ✅ aggregate / per-customer drill-in per owner-conversational | ✅ same | ❌ |
| Edit own toggles | ✅ | ❌ | ❌ | ❌ | ❌ |
| Request data export | ✅ | ❌ | ✅ (their own) | ✅ (their own) | ✅ (their own) |
| Request account deletion | ✅ | ❌ | ✅ self-account; n/a tenant-account | ✅ self | ✅ self |
| Approve another customer's deletion request | ❌ | ❌ | ✅ verifies | ✅ verifies | ❌ |
| See customer's wellness module data | ❌ (own only) | ❌ | ❌ strict privacy | ❌ strict | ❌ unless explicit grant |
| Cross-tenant profile linkage | n/a MVP | n/a | n/a | n/a | n/a |

---

## 11. Privacy boundaries (reinforced)

- Customer's own profile data: visible to customer + (booking summary slice) to assigned master per [`master-conversational-templates.md`](./master-conversational-templates.md) §5.5 pre-arrival context
- Customer's wellness module data: strictly customer-only — NEVER salon-side per [`wellness-input-modules.md`](./wellness-input-modules.md) §9
- Customer's conversation transcripts: customer can request export; owner sees per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) tenant ownership; master sees summarized notes only
- Customer's deletion request: visible to admin verifying + audit log
- Cross-tenant: none. Each tenant has own profile per Q-CO5.

---

## 12. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Profile screen with 20+ sections | Overwhelm | 6 sections grouped by concept |
| Persuasive «complete your profile» nag | Engagement hacking | Profile is functional; missing fields don't deserve nag |
| Show customer their wellness data on profile read | Profile is for management; data view = wellness dashboard | Section 2 lists modules + activation state, NOT data values |
| Hide deletion behind 5 menus | OP6 trust mandate | Privacy section, one-tap visible |
| Auto-process deletion without verification | Anti-abuse / accidental deletion | Always 30-day grace + admin verification per OP6 |
| Generic «Are you sure?» on deletion | Doesn't convey consequence | Explicit consequence list + checkbox + grace period |
| Data export requires email out-of-system | Friction | Deliver via same MAX channel as bot DM |
| Mock «verifying...» loading screen | Wastes customer time | Honest «обработается в течение 30 дней» — that IS the truth |
| Module activation behind 3 modals | Friction | One consent dialog per module, then immediate enable |
| Hide retention legal nuance | Customer can't trust what they don't understand | Plain-language disclosure per §6.5 / §6.2 |
| Auto-delete inactive accounts | Premature data loss | Customer-driven only |
| Cross-sell on profile («подключите ещё мастеров») | Profile is utility, not sales | NEVER |
| Different visibility rules for different sections | Mental model inconsistency | One privacy model applies everywhere (customer-only with explicit grants) |
| Owner sees customer's profile actions in real-time | Surveillance | Audit log only, not live dashboard |

---

## 13. Accessibility (WCAG 2.2 AA)

- Section cards: full-width tappable; 44×44 minimum
- Icons paired with text labels (never icon-only)
- Toggles in sub-sections per [notification-preferences §14](./notification-preferences-ux.md#14-accessibility-wcag-22-aa)
- Modal close: tap outside OR explicit close button OR ESC key
- Focus order: top-down (header → sections → footer)
- Screen reader: `aria-label` for icon-prefixed cards naming the section
- Deletion warning modal: `role="alertdialog"` with focus trap
- Customer name in header: respects MAX display preferences (right-to-left names, special characters)
- Reduced motion: no animation on section transitions if `prefers-reduced-motion`

---

## 14. Localization

### MVP: RU

### RU specifics
- «Профиль» (profile)
- «Мои записи» (my bookings)
- «Самочувствие» (wellness — softer than «здоровье» which implies clinical)
- «Уведомления» (notifications)
- «Приватность и данные» (privacy & data — established RU technical term)
- «Помощь» (help)
- «Удалить аккаунт» (delete account)
- «Запросить» (request — not «отправить» which feels less committed)

### Phase 4+
- Re-author per language
- Especially deletion warning copy — legal language varies per jurisdiction

---

## 15. Events emitted

Per [`event-taxonomy.md`](./event-taxonomy.md):

| Action | Event | Payload notes |
|---|---|---|
| Customer opens profile tab | `admin.audit.event` with `action='customer.profile.viewed'` | Low-value audit; rate-limited |
| Customer toggles wellness module | per wellness module's `wellness.consent.module.granted/revoked` | Per [`event-taxonomy §3.6`](./event-taxonomy.md#36-wellness-domain) |
| Customer toggles notification pref | `customer.consent.changed` | Per [`event-taxonomy §3.2`](./event-taxonomy.md#32-customer-domain) |
| Customer submits data export request | NEW: `customer.data_export.requested` (add to event-taxonomy §3.2) | `customer_id`, `requested_at`, `delivery_channel` |
| Admin processes data export | `admin.audit.event` with `action='data_export.processed'` | Audit trail |
| Customer submits deletion request | `customer.deleted_request` per existing §3.2 | `customer_id`, `requested_at` |
| Admin verifies deletion request | `admin.audit.event` with `action='deletion.verified'` | Required step |
| 30-day grace expires + hard-delete | `customer.hard_deleted` (NEW — add to event-taxonomy §3.2) | Cascade to retention pipeline |
| Customer opens FAQ | (no event — read-only static page) | — |
| Customer follows «связаться с владельцем» link | `conversation.handoff.to_human` per [`event-taxonomy §3.5`](./event-taxonomy.md#35-conversation-domain) | Reason: customer-initiated direct contact |

Add to event-taxonomy.md §3.2: `customer.data_export.requested`, `customer.hard_deleted`.

---

## 16. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-CP1** | Phase 1 personal info — any editable fields at all OR completely read-only? | Completely read-only Phase 1; name from MAX, phone from manual booking flow. Phase 2+ adds nickname / pronouns. | UX | 🟢 |
| **Q-CP2** | Visit history pagination — default 10 most recent or different? | 10 active + 10 past visible; «load more» pagination by 10 | UX | 🟢 |
| **Q-CP3** | Section 2 «Самочувствие» — should we show modules that aren't shipped yet («Скоро»)? | Phase 1 ship: show only «Настроение» activatable; hide rest. Phase 2+ progressively reveal as modules land. | PM | 🟢 |
| **Q-CP4** | Data export rate limit — 1 per 90 days enough or more often? | 1 per 90d MVP (anti-abuse); revisit if legit demand for more frequent | Legal + Policy | 🟡 |
| **Q-CP5** | Data export delivery channel — MVP only MAX bot DM or also email? | MAX bot DM MVP; email later if customer provides email OR if MAX delivery fails | Eng + UX | 🟢 |
| **Q-CP6** | Account deletion 30-day grace — fixed or per-tenant? | Fixed 30d MVP per OP6 lock; per-tenant v1.1+ | Legal | 🟢 |
| **Q-CP7** | If customer's deletion request is pending (within 30d grace) and they try to make new booking — block or allow? | BLOCK new bookings; show «Запрос на удаление в обработке — отмените запрос, если хотите бронировать» | UX | 🟡 |
| **Q-CP8** | Customer cancels deletion request mid-grace — restore everything or partial? | Full restore — soft-delete preserves all data until hard-delete trigger | Eng | 🟡 |
| **Q-CP9** | FAQ content authoring — platform-baseline only or tenant-customizable? | Platform baseline Phase 1; tenant-customizable Phase 2+ via Settings Hub | PM | 🟢 |
| **Q-CP10** | «Помощник — AI или человек?» FAQ answer — fixed copy or per-tenant? | Fixed platform copy per honesty mandate (single-assistant identity locked); tenant can ONLY change `salon_name` variable | Policy | 🟢 |
| **Q-CP11** | Customer Mini App opens to profile — is there a «new since last visit» indicator on sections? | NO MVP (avoid hook-like FOMO); each section's summary tells current state | UX | 🟢 |
| **Q-CP12** | Customer revokes wellness consent — what happens to existing data? | 30-day soft-delete window per [wellness-mood-handoff Q-WM4](../handoffs/2026-05-19-wellness-mood-handoff.md), then hard-delete (consistent with module-level rules) | Privacy | 🟢 |
| **Q-CP13** | Visit history «Записаться снова» CTA — pre-fill with same master/service/time-of-day? | Same master + service; date picker opens fresh (no time pre-fill — customer chooses) | UX | 🟢 |
| **Q-CP14** | Customer in PAUSED tenant — show export/delete actions or disable? | Show but disabled with explainer «Доступно когда студия снова в работе» | UX | 🟡 |
| **Q-CP15** | Customer requests export from one tenant while account also exists in another — confused dialog? | NO — export scoped to current tenant only; explainer line «Это данные из студии {{name}}» | UX | 🟢 |
| **Q-CP16** | If deletion request is initiated, can owner override and refuse? | Owner can DELAY processing (e.g., legal hold per [Q-C3 retention](../decisions-log.md)) but NOT REFUSE indefinitely. Hard-delete after 90 days even with legal hold absent fresh court order. | Legal | 🟡 |
| **Q-CP17** | Section 7 «связаться со студией» — direct DM or routed via assistant? | Direct DM (bot becomes conduit per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — assistant routes, owner replies as themselves with assistant tagging) | UX | 🟡 |
| **Q-CP18** | Browser/Mini App «back button» on profile — return to where customer came from or always to Главная? | Return to source (deep-linking pattern); fallback Главная if no history | Eng | 🟢 |

---

## 17. Cross-document linkage

- [`information-architecture.md`](./information-architecture.md) — «Профиль» is 5th surface; this doc fills the content
- [`notification-preferences-ux.md`](./notification-preferences-ux.md) §10 — section 3 deeplinks here
- [`wellness-input-modules.md`](./wellness-input-modules.md) — section 2 activates modules from here
- [`../handoffs/2026-05-19-wellness-mood-handoff.md`](../handoffs/2026-05-19-wellness-mood-handoff.md) — Mood module activation Path A originates here
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — OP6 deletion process implemented per §6.5
- [`conversational-ux-framework.md`](./conversational-ux-framework.md) — §6.4 honesty mandate enforced in §7.4 «о помощнике» modal
- [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) — visit list actions reference cancel/reschedule
- [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md) — empty states + state classification align
- [`event-taxonomy.md`](./event-taxonomy.md) §3.2 — events emitted with 2 NEW (data_export.requested, hard_deleted)
- [`manual-booking-spec.md`](./manual-booking-spec.md) §3.4 — phone field source
- [`../handoffs/2026-05-18-customer-first-time-handoff.md`](../handoffs/2026-05-18-customer-first-time-handoff.md) F-screens — profile section was placeholder; now detailed
- [`product-ux-vision.md`](./product-ux-vision.md) §10 — single-assistant identity reinforced in footer
- [`../decisions-log.md`](../decisions-log.md) — OP6 (deletion), Q-CO5 (tenant separation), Q-C3 (retention layers)

---

## 18. What this unblocks

- **Phase 1 / 4d implementation** — customer Mini App «Профиль» tab fully designed
- **OP6 deletion request UX** — customer-facing path exists (was hidden in support email)
- **Data export request flow** — customer can self-serve initiate (admin processes)
- **Wellness module discoverability** — Section 2 surfaces activation path for Mood (Phase 1) + other modules (Phase 2+)
- **Notification preferences entry** — Section 3 deeplink standardized
- **Privacy controls hub** — single «Приватность и данные» section consolidates all controls
- **Customer trust signal** — privacy controls visible without burying
- **Help/FAQ access** — common questions answered without contacting studio
- **Customer churn reduction** — explicit account deletion alternative (vs ghost-blocking the bot)
- **Audit completeness** — all customer self-service actions emit events

## 19. What this does NOT unblock

- ❌ Owner / master / admin profile management (separate scope)
- ❌ Detailed wellness data dashboard (Phase 2+ when modules accumulate)
- ❌ Self-serve automated deletion (Phase 2+ per OP6 lean)
- ❌ Customer payment management (no customer payments in MVP)
- ❌ Multi-tenant customer linkage (Q-CO5 v1.1+)
- ❌ Phase 2+ editable fields (nickname / pronoun preferences)
- ❌ Skip legal review on §6 privacy copy (especially deletion warning per Q-CP6/16)

---

## 20. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Mini App frontend (Профиль tab + sections + modals) | ☐ | |
| Backend (export/deletion request handlers + audit) | ☐ | |
| Privacy / Legal (deletion warning copy + retention disclosure + Q-CP4/6/16) | ☐ | |
| Customer support / CSM (export/deletion processing workflow) | ☐ | |
| Accessibility (WCAG 2.2 AA on full profile + modals) | ☐ | |
| AI prompt engineering (FAQ honest answers per Q-CP10) | ☐ | |

## Last verified
2026-05-19 (initial draft, customer profile management locked for Phase 1 / 4d + OP6 implementation)
