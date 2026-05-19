# Tenant Suspension / Pause UX — cross-audience

**Date:** 2026-05-19 r1
**Status:** Foundational — unblocks operational maturity when billing failures / owner-initiated pauses occur
**Reads:** [`conversation-ownership-policy.md`](./conversation-ownership-policy.md), [`attribution-policy.md`](./attribution-policy.md), [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`master-conversational-templates.md`](./master-conversational-templates.md), [`owner-conversational-templates.md`](./owner-conversational-templates.md), [`notification-preferences-ux.md`](./notification-preferences-ux.md), [`customer-profile-management-ux.md`](./customer-profile-management-ux.md), [`master-onboarding-m0-m7.md`](./master-onboarding-m0-m7.md), [`event-taxonomy.md`](./event-taxonomy.md)

> What happens when a tenant's subscription lapses, billing fails, or owner deliberately pauses? Every other doc references «PAUSED tenant» as an edge case; this doc designs the full lifecycle. State machine + per-state per-audience UX + recovery + data continuity.

---

## 0. Why this exists

### The gap

Multiple specs reference suspended/paused tenant as edge case but no spec designs it:
- [`customer-profile-management-ux.md`](./customer-profile-management-ux.md) Q-CP14 — «Customer in PAUSED tenant — show disabled with explainer»
- [`master-onboarding-m0-m7.md`](./master-onboarding-m0-m7.md) §3 — «Tenant in PAUSED billing state» as activation gate
- [`wellness-mood-handoff.md`](../handoffs/2026-05-19-wellness-mood-handoff.md) §3.1 — same gating
- [`wellness-ai-avatar-handoff.md`](../handoffs/2026-05-19-wellness-ai-avatar-handoff.md) §3.1 — same
- [`notification-preferences-ux.md`](./notification-preferences-ux.md) Q-NP17 — «Tenant suspended (billing failed) — preferences still honored for queued reminders?»
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) §3 — `PAUSED` is one of 5 owner identity states
- [`event-taxonomy.md`](./event-taxonomy.md) §3.9 — `billing.tenant.suspended` event exists; no consumer surface

Result: when first tenant hits billing failure → engineering improvises customer / master / owner behavior. Customer suddenly can't book without explanation. Master loses access mid-shift. Owner panics with no recovery flow. **Reputational risk + customer churn risk + master trust risk.**

### The promise

Single source for tenant lifecycle states + per-audience UX + recovery:
- 5-state machine (ACTIVE → AT_RISK_BILLING → PAUSED → SUSPENDED → ARCHIVED)
- Per-state behavior for customer / master / owner / admin
- Recovery / restore flow (owner self-service + CSM-mediated)
- Notification cascade with billing-specific tone
- API endpoint disablement matrix
- Data continuity rules (what stays vs degrades vs deletes)
- Events emitted

---

## 1. Scope

### IN
- Tenant lifecycle state machine (5 states)
- Per-state UX for customer / master / owner / admin sides
- Billing-failure trigger conditions + dunning stages
- Owner-initiated voluntary pause (vacation / temporary close)
- Recovery flow (self-service + CSM-mediated)
- Data continuity rules per state (preserve vs read-only vs purge)
- API endpoint disablement matrix
- Notification cascade per state transition
- Edge cases (transition mid-conversation, mid-booking, mid-onboarding)

### OUT
- Billing payment provider integration details (engineering scope; per Q13 CloudPayments / ЮKassa lean)
- Договор-оферта legal text (Q12-ε / Q14 legal scope)
- Account hard-deletion per OP6 (covered in [`customer-profile-management-ux.md`](./customer-profile-management-ux.md) §6.5; this doc handles tenant-level not customer-level)
- Founder bankruptcy / company-wide shutdown (out of operational scope)
- Multi-tenant chain salon partial-suspension (Phase 4+ per multi-location)
- Customer-side billing (no customer payments MVP)

---

## 2. The 5-state machine

```
                  ┌──────────┐
                  │  ACTIVE  │  ← normal operating state
                  └────┬─────┘
                       │
        ┌──────────────┼──────────────────┐
        │              │                  │
   billing fail    owner pauses    admin enforces
                                    (rare; CSM action)
        │              │                  │
        ▼              ▼                  ▼
┌──────────────────┐  ┌──────────┐  ┌──────────┐
│ AT_RISK_BILLING  │  │  PAUSED  │  │ SUSPENDED│
│ (grace window)   │  │ (owner-  │  │ (admin-  │
│                  │  │  driven) │  │  driven) │
└──────┬───────────┘  └────┬─────┘  └────┬─────┘
       │                   │             │
       │ 7d no payment     │ owner       │ resolution
       │                   │ resumes     │ OR escalation
       ▼                   ▼             ▼
┌──────────┐         ┌──────────┐  ┌──────────┐
│ SUSPENDED│ ◄───────┘  ACTIVE  │  │  ACTIVE  │
└──────┬───┘              ──────   │  OR       │
       │                            │ ARCHIVED │
       │ 60d unresolved             └──────────┘
       ▼
┌──────────┐
│ ARCHIVED │  ← terminal (data preserved 1y per legal then purged)
└──────────┘
```

### 2.1 State definitions

| State | Trigger | Reversible? | Customer-side experience |
|---|---|---|---|
| **ACTIVE** | Default normal operation | n/a | Full functionality |
| **AT_RISK_BILLING** | Payment failed; in 7-day grace | YES (payment success) | Full functionality + owner-side warnings |
| **PAUSED** | Owner-initiated voluntary pause (vacation, temporary close) | YES (owner taps «Возобновить») | Read-only; new bookings blocked; existing reminders OK |
| **SUSPENDED** | Billing not resolved after AT_RISK_BILLING grace OR admin/CSM enforcement | YES via owner action OR CSM lift | Read-only; ALL new actions blocked except billing recovery; existing bookings auto-cancel |
| **ARCHIVED** | SUSPENDED for 60 days unresolved OR owner deletes tenant | NO (data preserved 1y for legal then purged) | Bot unreachable; explainer + redirect |

### 2.2 Transition rules table

| From → To | Trigger | Actor | Customer-facing announcement? |
|---|---|---|---|
| ACTIVE → AT_RISK_BILLING | `billing.payment.failed` event | System | NO (internal) |
| AT_RISK_BILLING → ACTIVE | `billing.payment.received` event | System | NO (internal) |
| AT_RISK_BILLING → SUSPENDED | 7d no payment (cron) | System | YES (suspension notification) |
| ACTIVE → PAUSED | Owner taps «Поставить на паузу» | Owner | YES (gentle notice) |
| PAUSED → ACTIVE | Owner taps «Возобновить работу» | Owner | YES (welcome back) |
| SUSPENDED → ACTIVE | Owner resolves payment OR CSM lifts suspension | Owner / CSM | YES (recovery notice) |
| SUSPENDED → ARCHIVED | 60d unresolved (cron) | System | YES (final notice) |
| SUSPENDED → ARCHIVED | Owner taps «Удалить студию» | Owner | YES (planned shutdown) |

### 2.3 What CANNOT transition
- ARCHIVED is **terminal** — no path back. Owner who wants to «un-archive» creates NEW tenant from scratch (data preserved but not auto-restored).
- AT_RISK_BILLING and SUSPENDED CANNOT skip directly to ARCHIVED — must go through full grace window (legal + customer experience reasons).
- ACTIVE cannot transition directly to SUSPENDED via billing path — always AT_RISK_BILLING grace first.

---

## 3. Per-audience experience matrix

### 3.1 Customer experience by tenant state

| State | What customer sees | New bookings? | Existing bookings? | Bot DM responses? |
|---|---|---|---|---|
| ACTIVE | Normal full functionality | YES | Honored | Full AI persona per usual |
| AT_RISK_BILLING | Normal (transparent — billing issue is owner concern) | YES | Honored | Full AI persona |
| PAUSED | Mini App: read-only banner per §4.2; bot DM: «Студия временно не работает — записи приостановлены» | NO (blocked at slot resolver) | Honored if confirmed (existing); cancellation with refund per §6 if customer cancels | Limited AI (no new booking flow; reminders for existing); responds to questions about visit history |
| SUSPENDED | Mini App: «Студия не работает» banner; bot DM: «Студия временно не работает — мы напишем когда возобновится» | NO | Auto-cancelled per §6 cascade; refund per [`customer-cancellation-reschedule §4`](./customer-cancellation-reschedule-spec.md) | NO AI responses; static message only |
| ARCHIVED | Bot unreachable («This bot is no longer available») OR explainer with optional redirect to other tenant | NO | All cancelled at ARCHIVED transition | NO |

### 3.2 Master experience by tenant state

| State | Mini App access | New bookings ping? | Customer arrival ping? | Bot DM Q&A from master? |
|---|---|---|---|---|
| ACTIVE | Full | YES | YES | Full |
| AT_RISK_BILLING | Full (no master-side warning unless owner shares) | YES | YES | Full |
| PAUSED | Read-only Mini App with banner «Студия на паузе»; schedule changes blocked | NO new bookings can arrive | If existing bookings: YES until paused | Limited — can ask «когда возобновим?» (AI routes to owner) |
| SUSPENDED | Mini App read-only + persistent banner «Доступ ограничен»; pre-existing bookings auto-cancelled | NO | NO | NO AI responses; route to owner DM |
| ARCHIVED | No Mini App access | n/a | n/a | NO |

### 3.3 Owner experience by tenant state

| State | Owner Mini App access | Dashboard view | Key action |
|---|---|---|---|
| ACTIVE | Full | All KPIs + ops normal | n/a |
| AT_RISK_BILLING | Full + **persistent banner** «Платёж не прошёл — {{N}} дней до приостановки» + push DM alert | All normal + billing alert card | «Обновить способ оплаты» |
| PAUSED | Full with «На паузе» banner + restore CTA | KPIs frozen (paused timestamp shown); operations dashboard read-only | «Возобновить работу» |
| SUSPENDED | Limited — Settings → Billing accessible; rest read-only | «Студия приостановлена — заплатите чтобы возобновить» blocking modal on every page | «Оплатить» |
| ARCHIVED | Read-only export-only access for 1 year | «Архив — данные сохранены до {{date}}» | «Экспортировать всё» |

### 3.4 Admin experience by tenant state

Same as owner for ACTIVE / AT_RISK_BILLING / PAUSED. SUSPENDED + ARCHIVED — admin loses ability to invoke owner-only actions (recovery is owner-or-CSM only).

---

## 4. UI surfaces per state

### 4.1 Customer banner on Mini App (PAUSED state)

```
┌──────────────────────────────────────────┐
│ ⏸ Студия временно не работает            │
│                                          │
│ Сейчас новые записи приостановлены.      │
│ Существующие записи остаются в силе.     │
│                                          │
│ Ждите обновлений — мы напишем когда      │
│ возобновится работа.                     │
└──────────────────────────────────────────┘
```

Banner persistent at top of all Mini App surfaces. Tap «Подробнее» opens info modal §4.4.

### 4.2 Customer Mini App PAUSED state — feature gating

- Catalog (F1): browsable but every service shows greyed-out «Сейчас не работает» badge
- Master picker (F2): browsable read-only
- Slot picker (F3): shows «На паузе» message INSTEAD of slots
- Booking confirm (F4): disabled — customer cannot proceed
- My visits (F3 customer-handoff): existing bookings visible + cancellable; «Перенести» / «Повторить» disabled per [Q-CR5](../decisions-log.md) extension
- Profile: fully functional (data export + deletion still work per OP6)
- Notifications: receive only operational existing-booking reminders

### 4.3 Customer Mini App SUSPENDED state — full lockdown

- All sections show:
```
┌──────────────────────────────────────────┐
│ Студия не работает                       │
│                                          │
│ Мы напишем, когда возобновится работа.   │
│                                          │
│ Если у вас есть вопросы — напишите       │
│ владельцу студии напрямую:               │
│ {{owner_max_handle}}                     │
│                                          │
│ [Открыть профиль]                        │
│ (данные сохранены, доступны для         │
│  просмотра и экспорта)                  │
└──────────────────────────────────────────┘
```

- Profile tab: visit history + privacy controls still accessible (customer's right per OP6)
- All other tabs: replaced by above message

### 4.4 «Подробнее» info modal (PAUSED state)

```
┌──────────────────────────────────────────┐
│ Что значит «на паузе»?                   │
├──────────────────────────────────────────┤
│ Владелец студии временно остановил       │
│ запись новых клиентов. Это может быть    │
│ из-за отпуска, ремонта, или других       │
│ обстоятельств.                           │
│                                          │
│ Что останется:                           │
│ ✓ Записи, которые вы уже сделали         │
│ ✓ Ваш профиль и история визитов          │
│ ✓ Возможность связаться с владельцем     │
│                                          │
│ Что приостановлено:                      │
│ ✗ Новые записи                           │
│ ✗ Перенос существующих                   │
│ ✗ Активные программы лояльности          │
│                                          │
│ [Понятно]                                │
└──────────────────────────────────────────┘
```

### 4.5 Owner-side AT_RISK_BILLING dashboard

Persistent banner at top of Mini App owner dashboard:

```
┌──────────────────────────────────────────────────────┐
│ ⚠ Платёж за {{period}} не прошёл                     │
│                                                    │
│ {{reason_short}}. До приостановки работы — {{N}}    │
│ дней. Обновите способ оплаты, чтобы продолжить.    │
│                                                    │
│ [Обновить оплату]   [Связаться с поддержкой]        │
└──────────────────────────────────────────────────────┘
```

In addition: daily DM reminder per [`owner-conversational-templates §6.4`](./owner-conversational-templates.md) escalation template.

### 4.6 Owner pause flow

Owner Mini App → Настройки → Учётная запись → «Поставить студию на паузу»:

```
┌──────────────────────────────────────────┐
│ Поставить студию на паузу?               │
├──────────────────────────────────────────┤
│ На время паузы:                          │
│                                          │
│ • Новые записи будут заблокированы       │
│ • Существующие записи остаются в силе    │
│ • Мастера получат уведомление            │
│ • Клиенты увидят баннер на Mini App      │
│                                          │
│ Когда снимать паузу?                     │
│ ◯ В конкретную дату: [_____]             │
│ ◉ Не знаю — сниму вручную                │
│                                          │
│ Причина для клиентов (опц.):             │
│ [___________________________________]    │
│ Например: «отпуск», «ремонт»             │
│                                          │
│ [Отмена]            [Поставить на паузу] │
└──────────────────────────────────────────┘
```

Reason text is OPTIONAL but if provided, shown in customer banner instead of generic «временно не работает».

### 4.7 Owner-side SUSPENDED state — blocking modal

Every page in owner Mini App:

```
┌──────────────────────────────────────────┐
│ ⏸ Студия приостановлена                  │
│                                          │
│ Платёж не прошёл {{days_ago}} дней назад.│
│ Чтобы возобновить работу, нужно          │
│ обновить способ оплаты.                  │
│                                          │
│ ── Что заблокировано ──                  │
│ • Новые записи                           │
│ • AI-помощник для клиентов               │
│ • Уведомления и кампании                 │
│                                          │
│ ── Что доступно ──                       │
│ • Этот раздел (Биллинг)                  │
│ • Экспорт данных (история записей)       │
│ • Связаться с поддержкой                 │
│                                          │
│ [Обновить оплату]                        │
│ [Связаться с CSM]                        │
└──────────────────────────────────────────┘
```

Only the «Биллинг» section + «Экспорт» work. All other navigation triggers this modal.

---

## 5. Notification cascade per state transition

Per [`notification-preferences-ux.md`](./notification-preferences-ux.md) §2.3 N15 «escalation.urgent» is OPERATIONAL — cannot disable. Tenant suspension events fall in this class.

### 5.1 ACTIVE → AT_RISK_BILLING (billing failure)

| Recipient | Channel | Template |
|---|---|---|
| Owner | Bot DM immediate | [`owner-conversational-templates §6.4`](./owner-conversational-templates.md) payment-failed |
| Admin | Bot DM immediate (if delegated billing) | Same |
| Customer | None (internal) | n/a |
| Master | None (internal) | n/a |

### 5.2 AT_RISK_BILLING → SUSPENDED (grace expired)

| Recipient | Channel | Template (voice anchor per audience) |
|---|---|---|
| Owner | Bot DM critical + email if configured | «Студия приостановлена с {{timestamp}}. Для возобновления — обновите оплату. До архивации — {{N}} дней.» |
| Admin | Bot DM critical | Same |
| All masters | Bot DM | «{{salon_name}} временно приостановлена. Запросы клиентов и новые записи на паузе. Владелец работает над восстановлением.» |
| All customers with future bookings | Bot DM | «{{salon_name}} временно не работает. Ваша запись на {{date}} {{time}} — мы напишем когда возобновится. Если нужно отменить — напишите.» |
| All customers without future bookings | None (don't spam customers who aren't actively engaged) | n/a |

### 5.3 SUSPENDED → ACTIVE (recovery)

| Recipient | Channel | Template |
|---|---|---|
| Owner | Bot DM | «Готово — студия снова работает. Платёж получен.» |
| All masters | Bot DM | «{{salon_name}} снова работает. Расписание и записи восстановлены.» |
| All customers with reactivated bookings | Bot DM | «{{salon_name}} снова работает. Ваша запись на {{date}} {{time}} в силе — ждём вас.» |
| Other customers | None (don't broadcast — respect silence) | n/a |

### 5.4 ACTIVE → PAUSED (owner-initiated)

| Recipient | Channel | Template |
|---|---|---|
| Owner | None (they initiated) | n/a |
| All masters | Bot DM | «{{salon_name}} на паузе с {{date}}. {{reason_if_provided}}. {{owner_first_name}} напишет когда снова откроемся.» |
| Customers with future bookings | Bot DM | «{{salon_name}} ставится на паузу с {{date}}. Ваша запись на {{their_booking_date}} {{their_booking_time}} остаётся в силе (если она до {{pause_start}}). Если позже — мы свяжемся.» |
| Other customers | None | n/a |

### 5.5 PAUSED → ACTIVE (owner resumes)

| Recipient | Channel | Template |
|---|---|---|
| Owner | None (they initiated) | n/a |
| All masters | Bot DM | «{{salon_name}} снова работает. Доступ восстановлен.» |
| Customers with deferred bookings (impacted by pause window) | Bot DM | «{{salon_name}} снова работает. Если хотите записаться — рада снова услышать.» |

### 5.6 SUSPENDED → ARCHIVED (60d expired)

Per [event-taxonomy §3.9](./event-taxonomy.md#39-billing-domain) `billing.tenant.suspended` events emit when state transitions; this is final.

| Recipient | Channel | Template |
|---|---|---|
| Owner | Bot DM + email | «{{salon_name}} архивирована. Данные доступны для экспорта в течение 1 года.» |
| All masters | Bot DM | «{{salon_name}} закрылась. Если будут вопросы — напишите {{owner_short_name}}.» |
| All customers (any state) | Bot DM | «{{salon_name}} закрылась. Ваша история сохранена в профиле, можно скачать в течение года. Спасибо, что были с нами.» |

---

## 6. Existing bookings handling per state transition

### 6.1 ACTIVE → AT_RISK_BILLING
- All bookings continue as scheduled
- Reminders fire as usual
- Customer experience unchanged

### 6.2 ACTIVE → PAUSED
- Future bookings AFTER pause_start: customer offered choice via DM «остаётся в силе если возможно ИЛИ перенести / отменить»
- Future bookings BEFORE pause_start: continue as scheduled
- Existing bookings within pause window: case-by-case (customer accepts cancellation OR keeps if master agrees)
- New bookings: BLOCKED at slot resolver

### 6.3 PAUSED → ACTIVE
- Deferred bookings (customer chose «wait») prompt for reconfirmation
- Customer DM: «Возобновили работу. Подтвердить вашу запись на {{date}}?»

### 6.4 ACTIVE → SUSPENDED (billing grace expired)
- All future bookings auto-cancelled with `booking.cancelled` event, `cancellation_reason='tenant_suspended'`
- Auto-refund per [`customer-cancellation-reschedule-spec §4`](./customer-cancellation-reschedule-spec.md) policy — applied since cancellation is salon's billing-failure, not customer's fault
- Customer notified per §5.2 above
- Customer can request manual exception via owner direct contact (rare)

### 6.5 SUSPENDED → ACTIVE
- Auto-cancelled bookings DO NOT auto-restore
- Customer DM: «{{salon_name}} снова работает. Если хотите снова записаться на {{date}} {{time}} — нажмите.» with re-book quick-action
- This prevents accidental zombie-bookings

### 6.6 ARCHIVED transition
- All bookings cancelled (no «just pretend they're real»)
- Data preserved for legal retention 1 year (Q-C3 layer 3) then anonymized + purged
- Master payouts (if applicable) — out of scope; CSM handles

---

## 7. API endpoint disablement matrix

Per state, which API endpoints respond normally vs return 423 (Locked) vs 410 (Gone).

| Endpoint domain | ACTIVE | AT_RISK_BILLING | PAUSED | SUSPENDED | ARCHIVED |
|---|---|---|---|---|---|
| `/api/v1/customer/services` | 200 | 200 | 200 read-only | 410 with banner data | 410 |
| `/api/v1/customer/masters` | 200 | 200 | 200 read-only | 410 | 410 |
| `/api/v1/customer/slots` | 200 | 200 | 410 «paused» | 410 | 410 |
| `/api/v1/customer/bookings` (POST) | 201 | 201 | 423 «paused — no new bookings» | 423 | 410 |
| `/api/v1/customer/bookings/{id}/reschedule` | 200 | 200 | 423 | 423 | 410 |
| `/api/v1/customer/bookings/{id}/cancel` | 200 | 200 | 200 (customer can always cancel) | 200 | 410 |
| `/api/v1/customer/visits` (GET) | 200 | 200 | 200 | 200 (read-only history) | 200 (limited 1y window) |
| `/api/v1/customer/profile` (GET/PATCH) | 200/200 | 200/200 | 200/200 | 200/200 (customer always has profile control) | 200/200 (read-only) |
| `/api/v1/customer/wellness/*` | 200 | 200 | 200 read-only | 410 | 410 |
| `/api/v1/customer/data_export` | 200 | 200 | 200 | 200 | 200 (essential for legal data access) |
| `/api/v1/customer/deletion_request` | 200 | 200 | 200 | 200 | 200 |
| `/api/v1/master/*` | 200 | 200 | 200 read-only | 410 | 410 |
| `/api/v1/owner/billing/*` | 200 | 200 | 200 | 200 (always available — primary recovery path) | 200 |
| `/api/v1/owner/settings/*` | 200 | 200 | 200 | 410 except billing | 410 |
| `/api/v1/owner/analytics/*` | 200 | 200 | 200 | 410 | 200 (read-only export) |
| Webhooks from YClients / external | 200 | 200 | 200 logged + queued (don't crash external systems) | 200 logged + suppressed | 200 logged + return 410 metadata |

**410 (Gone) returns include**: tenant_state metadata for client-side error UX:
```json
{
  "error": "tenant_unavailable",
  "tenant_state": "suspended",
  "since": "2026-05-12T08:00:00Z",
  "user_facing_message": "Студия временно не работает. Мы напишем когда возобновится.",
  "owner_contact": "{{owner_max_handle}}"
}
```

---

## 8. Events emitted per state transition

Per [`event-taxonomy.md`](./event-taxonomy.md) §3.9 + additions:

| Transition | Event | Notes |
|---|---|---|
| Payment failed | `billing.payment.failed` (existing §3.9) | Triggers AT_RISK_BILLING entry |
| Grace 7d expired | `billing.tenant.suspended` (existing) | Updated to also handle owner-initiated PAUSED — see §8.1 |
| Owner pauses | NEW: `billing.tenant.paused_voluntarily` (add to §3.9) | Different intent from suspended — preserve flag for analytics |
| Owner resumes | NEW: `billing.tenant.resumed` | |
| 60d expired or owner deletes | NEW: `billing.tenant.archived` | Terminal |
| Booking cascade-cancelled by tenant state | `booking.cancelled` with `cancellation_reason='tenant_state_change'` (per [`customer-cancellation-reschedule-spec §7.5`](./customer-cancellation-reschedule-spec.md)) | Cascade event |
| Customer auto-refunded | `booking.refunded` with `refund_reason='tenant_suspension_auto'` (per [`attribution-policy §6`](./attribution-policy.md)) | |

### 8.1 Event payload distinction

Use single `tenant_state` field in event payload to distinguish:
- `tenant.suspended` event with payload `{ "state": "at_risk_billing" }` — entry to AT_RISK
- `tenant.suspended` event with payload `{ "state": "suspended" }` — full suspension
- `tenant.paused_voluntarily` event with `{ "until_date": "..." | null, "reason": "..." }` — owner-initiated

Or split into separate events. Engineering picks.

---

## 9. Recovery flows

### 9.1 Self-service recovery from AT_RISK_BILLING

1. Owner sees banner on Mini App + DM alert
2. Tap «Обновить оплату» → CloudPayments / ЮKassa flow per Q13
3. Successful payment → `billing.payment.received` event → AT_RISK_BILLING → ACTIVE transition
4. All masters + customers receive recovery notification per §5.3
5. No data loss

### 9.2 Self-service recovery from SUSPENDED

Same as 9.1 but:
- Owner must navigate through blocking modal to «Биллинг» section
- After payment success → owner sees confirmation modal explaining «Записи которые были отменены не восстановятся автоматически. Вы можете предложить клиентам перенос вручную из дашборда.»
- Customers DON'T see automatic restoration — they get re-book invite per §6.5

### 9.3 CSM-mediated recovery

For owners who can't recover (lost MAX access, billing dispute, etc.):
- Customer-side support email per [`customer-profile-management-ux.md`](./customer-profile-management-ux.md) §6.6 owner contact
- CSM-side workflow: validate owner identity (out of band — phone / video call) + verify resolution attempt + CSM grants temporary 30d grace by overriding state
- Audit-logged per Q-CSM action

### 9.4 ARCHIVED recovery

NOT POSSIBLE for ARCHIVED state. Owner who wants to «return» creates new tenant from scratch. Old tenant data:
- Preserved 1 year for legal access
- Owner can export via DM «Запросить экспорт» (CSM-mediated)
- After 1 year — anonymized + purged per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) §6 retention

---

## 10. Edge cases

### 10.1 Customer in active booking flow when tenant transitions to SUSPENDED
- Customer's draft booking discarded (slot resolver returns 410 on next interaction)
- Customer sees error toast: «Студия только что временно приостановила работу. Извините за неудобство.»
- Booking is NOT created
- Customer redirected to home with banner per §4.3

### 10.2 Customer DM in flight when tenant transitions
- Customer's last incoming message responded to with static «Студия временно не работает» message
- Pre-existing booking-related responses for outstanding bookings continue
- AI persona suppressed until ACTIVE

### 10.3 Master mid-shift when tenant transitions to PAUSED
- Master's existing today's bookings continue
- New booking ping suppressed
- Master receives explanation DM per §5.4

### 10.4 Master mid-shift when tenant transitions to SUSPENDED
- Same as 10.3 BUT today's bookings can complete (master + customer mid-procedure shouldn't be impacted)
- After today: master loses access until ACTIVE

### 10.5 Owner onboarding incomplete when billing fails
- If tenant in onboarding Phase 0-5 (per [`salon-onboarding-handoff`](../handoffs/2026-05-17-salon-onboarding-handoff.md)): pause is unusual; treat as «owner abandoned» → CSM follow-up
- Transition to PAUSED rather than SUSPENDED (less harsh)

### 10.6 Tenant has 0 customers / 0 bookings when transitioning
- Skip customer-facing notifications (no one to notify)
- Skip master notifications if 0 masters
- Owner still gets transitions

### 10.7 Multiple state transitions in quick succession
- E.g., billing failed → 6d later owner manually resumes → 1d later billing fails again
- All transitions logged + audited per `billing.*` events
- Customer / master DM cooldown: same notification type not sent more than once per 24h

### 10.8 Tenant has ACTIVE_REGULAR loyalty customers
- Loyalty points balance preserved across all transitions
- Points expire per [`loyalty-system-handoff`](../handoffs/2026-05-18-loyalty-system-handoff.md) Q-L3 (never MVP); not affected by tenant pause
- During SUSPENDED: points cannot be redeemed (no booking flow); balance visible in profile

### 10.9 Tenant has scheduled marketing campaigns
- All campaigns suppressed on entering AT_RISK_BILLING (anti-pattern: campaigning while can't fulfill)
- Resumed on entering ACTIVE
- DELETED on ARCHIVED transition

### 10.10 Customer with wellness module active during tenant suspension
- Module data preserved per [§7 API matrix](#7-api-endpoint-disablement-matrix) — read-only during suspension
- Customer's wellness profile remains customer-owned (not tenant-owned)
- Customer can still export per [`customer-profile-management-ux §6.3`](./customer-profile-management-ux.md)
- If tenant resumes within 14 days: module reactivates seamlessly
- If tenant ARCHIVED: customer's wellness data purged per cross-tenant boundary (data was per-tenant per Q-CO5)

### 10.11 Master has pending ScheduleChangeRequest when tenant pauses
- Request marked «outdated» on pause entry; auto-archived
- On resume: master can re-submit if still relevant

### 10.12 Customer's data export request pending when tenant suspended
- Request continues to be processed (operational priority — privacy compliance > tenant state)
- Delivery still occurs per [`customer-profile-management-ux §6.3`](./customer-profile-management-ux.md)

### 10.13 Tenant in SUSPENDED + owner doesn't act for 60 days
- Auto-transition to ARCHIVED per §2.2
- Final notification per §5.6
- Owner is gone; CSM may attempt phone outreach as last resort (1 attempt)

### 10.14 Owner taps «Поставить на паузу» with no «когда снимать»
- PAUSED is indefinite — until owner manually resumes
- Cron checks at 30d, 60d, 90d → owner DM reminder «Тарифы продолжают начисляться, студия на паузе {{N}} дней — продолжаем?»
- After 90d no response: CSM follow-up

### 10.15 Tenant in PAUSED + owner billing fails
- PAUSED → SUSPENDED transition (billing failure overrides voluntary state)
- Owner DM: «Платёж не прошёл во время паузы. Чтобы возобновить — обновите оплату.»

---

## 11. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Surprise customers with «no longer available» message | Trust breach | Always 7d AT_RISK_BILLING grace + transparent notifications |
| Hide PAUSED state from owner in dashboard | Confusion | Persistent banner + always visible state indicator |
| Auto-delete customer data on SUSPENDED | Privacy violation + customer wrath | Data preserved per [Q-C3 retention](../decisions-log.md) |
| Block customer's data export during SUSPENDED | Customer right per OP6 | Always available |
| Block customer's profile during PAUSED | Customer right | Profile + history + privacy controls always work |
| Spam all customers on every tenant state change | Notification fatigue | Only impacted customers (future bookings); avoid mass-broadcast |
| Continue charging tenant fees during PAUSED voluntarily | Trust breach | Pause fees during PAUSED; resume only on ACTIVE |
| Auto-restore cancelled bookings on resume | Customer didn't agree | Re-book invite only |
| Show owner master earnings during SUSPENDED | Out of scope; sensitive data | Hide |
| Allow new bookings during AT_RISK_BILLING | Misleading customer (booking may not be honored) | Continue normal — billing is owner's problem until grace expires |
| Punish customers for tenant's billing issue | Antagonistic | Refund auto + clear apology framing |
| Use blame-shame language («владелец не заплатил») | Public shaming | Neutral facts: «студия временно не работает» |
| Skip notification to customers who never DM'd assistant | They have bookings | They need to know about THEIR bookings |
| Treat PAUSED same as SUSPENDED in UX | Different intent | Voluntary pause is softer messaging vs forced suspension |
| Auto-archive without owner notice | Surprise loss | Always 30d / 60d cron reminders before ARCHIVED |

---

## 12. Localization

### MVP RU

- ACTIVE → «Работает» (in admin internal status display)
- AT_RISK_BILLING → «Под угрозой» / «Платёж не прошёл»
- PAUSED → «На паузе»
- SUSPENDED → «Приостановлена»
- ARCHIVED → «Архив»

For customer-facing: use plain language («Студия временно не работает», «закрылась»). Avoid technical state names.

### Phase 4+
Re-author per language with cultural sensitivity (e.g., shame-aversion stronger in some cultures — phrasing varies).

---

## 13. Accessibility (WCAG 2.2 AA)

- Banner: `role="status"` for soft alerts (AT_RISK_BILLING); `role="alert"` for critical (SUSPENDED)
- Blocking modal: `role="alertdialog"` with focus trap
- All state transitions announced via screen-reader live region
- Color-coded indicators always paired with text labels (state name visible)
- 44×44 touch targets for recovery CTAs (high-importance)
- High-contrast on SUSPENDED state lockdown screen (≥7:1)

---

## 14. Data continuity rules

### 14.1 What is preserved across all states (until ARCHIVED + 1y)

- All booking records (past + future)
- All conversation history (per [Q-C3 4-layer retention](../decisions-log.md))
- All customer profiles
- All master profiles
- All wellness module data per customer
- All loyalty points balances
- All audit events
- All settings configurations

### 14.2 What is suspended during PAUSED / SUSPENDED

- New booking creation
- AI-driven proactive messaging
- Marketing campaign dispatch
- New customer onboarding (Path B / D activations per [`wellness-mood-handoff §3.2`](../handoffs/2026-05-19-wellness-mood-handoff.md))
- New master invites

### 14.3 What is purged at ARCHIVED + 1y

- Customer PII per [Q-C3 layer 1](../decisions-log.md) — phone, email, name (anonymized)
- Free-text conversation content (anonymized)
- Photo data (hard-deleted per [`wellness-ai-avatar-handoff §12.3`](../handoffs/2026-05-19-wellness-ai-avatar-handoff.md))
- Free-text notes

### 14.4 What is retained beyond 1y at ARCHIVED

- Aggregated statistics (anonymized; for platform analytics)
- Booking financial records (7y per ФЗ accounting)
- Required compliance records per RU legal

---

## 15. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-TS1** | AT_RISK_BILLING grace window — 7 days fixed or per-tenant configurable? | Fixed 7d MVP; per-tenant v1.2+ with founder-set max 14d cap | Founder + Eng | 🟡 |
| **Q-TS2** | PAUSED voluntary — do platform fees continue accruing? | NO — paused tenant doesn't pay during pause (per Q9 hybrid pricing model spirit); resume when ACTIVE. v1.2 «paused fee» discussion if abused | Founder | 🔴 before first PAUSED tenant |
| **Q-TS3** | SUSPENDED 60d → ARCHIVED — fixed or per-tenant? | Fixed 60d MVP; provides predictability; no per-tenant configuration | Founder | 🟢 |
| **Q-TS4** | When transitioning ARCHIVED — do we delete or anonymize booking history immediately or wait 1y? | Wait 1y (legal retention); then anonymize per §14.3 | Legal | 🟢 |
| **Q-TS5** | Owner-paused tenant — can master still see schedule? | Read-only YES (they need to plan), but no new booking notifications | UX | 🟢 |
| **Q-TS6** | Customer who paid for service (loyalty redemption) right before SUSPENDED — refund? | Auto-refund per [attribution-policy §6 Q12-β](./attribution-policy.md) — same as tenant-state-cancellation; refund includes points restoration | Policy | 🟡 |
| **Q-TS7** | Multiple tenants on same business owner — independent suspension? | YES — per-tenant lifecycle; owner can have ACTIVE tenant A + SUSPENDED tenant B | Policy | 🟢 |
| **Q-TS8** | YClients sync during SUSPENDED — accept webhooks or reject? | Accept + queue (don't crash external system); log; respond with state metadata. On resume: process queue. | Eng | 🟡 |
| **Q-TS9** | Customer-side «Связаться с владельцем» link during SUSPENDED — does it open bot DM (which is in static-message-only mode) or open MAX direct chat with owner? | Open MAX direct chat with owner (bypassing bot). Per [§4.3](#43-customer-mini-app-suspended-state--full-lockdown) banner shows `owner_max_handle` directly. | UX | 🟢 |
| **Q-TS10** | Master payments / payouts during SUSPENDED — what about earnings owed? | Out of scope this doc; CSM handles per business agreement | Founder | 🟢 |
| **Q-TS11** | Owner can «cancel pause and immediately re-pause» — anti-abuse? | NO rate limit MVP; v1.2+ if observed gaming | UX | 🟢 |
| **Q-TS12** | Notification cascade timing — fire all at once OR throttle to avoid mass MAX rate limit? | Throttle per [`customer-cancellation-reschedule-spec §6.5`](./customer-cancellation-reschedule-spec.md) batch cascade pattern (max 5 / min) | Eng | 🟡 |
| **Q-TS13** | Customer with active wellness AI Avatar grant to master — what happens to grant during SUSPENDED? | Grant remains valid but master cannot access (master Mini App locked). On resume: grant auto-active. On ARCHIVED: grant + photos purged per [`wellness-ai-avatar §12.3`](../handoffs/2026-05-19-wellness-ai-avatar-handoff.md) | Eng + Privacy | 🟡 |
| **Q-TS14** | Tenant in PAUSED + an emergency happens (master injures customer during preserved appointment, etc.) — how does customer reach help? | Standard HUMAN_LOCKED escalation per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md); AI in PAUSED tenant still acknowledges critical safety messages + routes immediately | Policy | 🟡 |
| **Q-TS15** | Owner-side blocking modal during SUSPENDED — can owner dismiss to view other sections in read-only mode? | NO — modal blocks per §4.7. Forces «pay or contact CSM». Per CSM observability for low-engagement tenants who freeze in SUSPENDED. | Founder + UX | 🟡 |
| **Q-TS16** | When transitioning to SUSPENDED, who decides which existing bookings get auto-cancelled vs preserved? | Auto-cancel ALL future bookings (≥ now + 1h); preserve same-day + in-progress (don't disrupt mid-service). Master can complete today's bookings; customer notified after | Eng + UX | 🟡 |
| **Q-TS17** | Founder dashboard — show suspended tenants list with «attempt revival» action? | YES — founder-only view in [`ai-quality-observability`](./ai-quality-observability.md) extended OR new admin tool | Founder | 🟢 |

---

## 16. Cross-document linkage

- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) §6 — retention rules drive data continuity §14
- [`attribution-policy.md`](./attribution-policy.md) §6 — refund rules for tenant-cancellation per Q15
- [`conversational-ux-framework.md`](./conversational-ux-framework.md) — voice anchors throughout customer messages
- [`master-conversational-templates.md`](./master-conversational-templates.md) — voice anchors for master messages
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) §3 — owner state machine references PAUSED + SUSPENDED
- [`notification-preferences-ux.md`](./notification-preferences-ux.md) §2.3 — N15 operational class; Q-NP17 referenced
- [`customer-profile-management-ux.md`](./customer-profile-management-ux.md) §9.3 — customer in PAUSED tenant
- [`master-onboarding-m0-m7.md`](./master-onboarding-m0-m7.md) §3 — PAUSED activation gate referenced
- [`wellness-mood-handoff.md`](../handoffs/2026-05-19-wellness-mood-handoff.md) §3.1 — eligibility gate
- [`wellness-ai-avatar-handoff.md`](../handoffs/2026-05-19-wellness-ai-avatar-handoff.md) §3.1 — same; Q-TS13 cross-ref
- [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) §6.5 cascade pattern + §7.5 events
- [`event-taxonomy.md`](./event-taxonomy.md) §3.9 — billing.* events; 3 NEW (paused_voluntarily / resumed / archived)
- [`../handoffs/2026-05-18-settings-hub-handoff.md`](../handoffs/2026-05-18-settings-hub-handoff.md) §6.11 owner billing flow
- [`../handoffs/2026-05-17-salon-onboarding-handoff.md`](../handoffs/2026-05-17-salon-onboarding-handoff.md) Phase 12 billing flow
- [`ai-quality-observability.md`](./ai-quality-observability.md) §17 — founder-side dashboard for SUSPENDED tenants (Q-TS17)

---

## 17. What this unblocks

- **First salon billing failure handling** — engineering knows what to do
- **Owner-initiated voluntary pause** — UX exists for legitimate need (vacation / temporary close)
- **CSM workflow for stuck tenants** — recovery flow documented
- **Customer trust during salon issues** — transparent, kind notifications
- **Master experience during disruption** — clear status + recovery
- **Legal compliance** — data retention rules locked
- **Anti-pattern protection** — engineering review against shame language / customer punishment
- **Notification cascade per state transition** — clear cascade with throttling
- **Audit trail** — all transitions logged

## 18. What this does NOT unblock

- ❌ Billing provider integration details (Q13 engineering scope)
- ❌ Customer-pays tier suspension (different lifecycle Phase 3+)
- ❌ Multi-tenant chain partial suspension (Phase 4+)
- ❌ Master payout / earnings during SUSPENDED (Q-TS10 out of scope)
- ❌ Skip founder ratification of Q-TS2 paused-fee policy
- ❌ Skip legal review on data retention §14 + customer-facing copy in §4

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Founder (Q-TS2 paused fees + Q-TS3 archive timeline + Q-TS17 founder dashboard) | ☐ | |
| Billing / Finance lead (CloudPayments / ЮKassa integration + dunning + Q-TS2) | ☐ | |
| Legal (data retention §14 + customer-facing copy in §4 + Q-TS4) | ☐ | |
| Backend (state machine + cascade events + API matrix §7) | ☐ | |
| Mini App frontend (customer / master / owner banners + blocking modal) | ☐ | |
| CSM lead (recovery workflow §9.3 + suspended-tenant outreach) | ☐ | |
| AI prompt engineering (PAUSED-state limited AI response patterns + critical safety escalation per Q-TS14) | ☐ | |

## Last verified
2026-05-19 (initial draft, tenant lifecycle locked for operational maturity at scaling)
