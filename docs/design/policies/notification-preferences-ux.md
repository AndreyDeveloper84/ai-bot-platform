# Notification Preferences UX — Customer / Master / Owner

**Date:** 2026-05-19 r1
**Status:** Foundational — unblocks Settings Hub refresh + cross-cutting opt-in/opt-out logic
**Reads:** [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`master-conversational-templates.md`](./master-conversational-templates.md), [`owner-conversational-templates.md`](./owner-conversational-templates.md), [`event-taxonomy.md`](./event-taxonomy.md), [`conversation-ownership-policy.md`](./conversation-ownership-policy.md), [`product-ux-vision.md`](./product-ux-vision.md), [`max-mini-apps`](../../../.claude/skills/ux-architect/references/platforms/max-mini-apps.md)

> Notification rules are sprinkled across 8+ specs (master-conv §8, owner-conv §10, customer-cancellation §7, customer-first-touch §3, manual-booking §5, wellness-input-modules §11, conversation-ownership §3, schedule-management §6). This doc consolidates them into one matrix, designs the settings UI per audience, and locks defaults + cap rules.

---

## 0. Why this exists

### The gap

Notifications are mentioned everywhere but never centralized:

- Customer per Q-CX9: «single «без проактивных» toggle» — decided but no UX
- Master per [master-conversational §8](./master-conversational-templates.md#8-notification-frequency-policy) — has matrix but no UI
- Owner per [owner-conversational §10](./owner-conversational-templates.md#10-off-hours) + §6.1-6.6 — has rules but no preferences screen
- Wellness modules per [wellness-input-modules §11](./wellness-input-modules.md#11-consent-layering) — per-module consent toggles but not unified
- Settings Hub handoff (legacy, pre-refresh) doesn't have this section
- Each spec assumes someone else designs the preferences screen

Result: 4b customer Mini App will improvise opt-out; master mobile will improvise notification settings; owner Mini App will improvise digest preferences. Three different mental models for the same conceptual thing.

### The promise

Single source of truth for:
- 3-axis preference matrix (audience × channel × event-type)
- Per-audience defaults rationale
- UI screens for managing preferences
- Frequency caps + DND windows
- Cross-channel fallback rules (MVP: MAX-only; future channels stub-ready)
- Privacy boundaries per role
- 14 events that emit notifications, each with on/off rule

---

## 1. Scope

### IN
- Notifications via MAX bot DM (customer + master + owner DMs)
- Mini App in-app banners + toasts (no push, but interactive UI signals)
- Per-audience opt-in/opt-out preferences UI
- Default settings per audience + rationale
- Operational notifications (cannot be disabled, listed explicitly)
- Frequency throttling rules per audience
- DND windows (off-hours)
- Wellness module consent integration (separate per-module toggles)
- Audit trail for preference changes
- Cross-doc reconciliation: this doc is the canonical reference; other specs link here

### OUT
- Push notifications outside MAX chat (MAX platform limitation — no native push beyond bot DMs)
- SMS / Email notifications (out of MVP scope; Phase 4+ if integrated)
- Web push (no PWA implementation in MVP)
- Notification content (covered in conversational template trilogy)
- Notification delivery infrastructure (engineering / event-taxonomy concern)
- Voice messages as notification channel (deferred per Q-C6 / Q-MC1 / Q-OC10)

---

## 2. The 3-axis matrix

Every notification is defined by:

```
audience × channel × event_type → enabled (bool) + throttle (rules) + DND window
```

### 2.1 Audiences (3)

- **Customer** — beauty/wellness service buyer
- **Master** — practitioner; receives operational + customer-related pings
- **Owner / Admin** — salon staff; receives operational + insight + escalation alerts

### 2.2 Channels (MVP = 2)

- **MAX bot DM** — primary channel, persistent
- **Mini App in-app** — banners, toasts, badges (shown when app open)

Phase 4+ channels stub-ready in schema but not surfaced in UI yet:
- Email (`email`)
- SMS (`sms`)
- Web push (`web_push`)

### 2.3 Event types (14)

| # | Event type ID | Audience(s) | Operational class | Default |
|---|---|---|---|---|
| **N1** | `booking.confirmed` | customer | OPERATIONAL — cannot disable | ON |
| **N2** | `booking.reminder.t24h` | customer | OPERATIONAL — cannot disable | ON |
| **N3** | `booking.reminder.t2h` | customer | OPERATIONAL — cannot disable | ON |
| **N4** | `booking.reminder.t15min` | customer | OPERATIONAL — cannot disable | ON |
| **N5** | `booking.cancelled` | customer + master + owner | OPERATIONAL — cannot disable | ON |
| **N6** | `booking.rescheduled` | customer + master + owner | OPERATIONAL — cannot disable | ON |
| **N7** | `booking.created` (new) | master + owner | OPT-OUT-able | ON |
| **N8** | `booking.completed` (post-visit) | customer | OPT-OUT-able («без проактивных») | ON |
| **N9** | `customer.no_show` (gentle check) | customer | OPT-OUT-able («без проактивных») | ON |
| **N10** | `wellness.input.reminder` (water / mood / food per module) | customer | OPT-IN per module | OFF default |
| **N11** | `campaign.dispatched` (marketing) | customer | OPT-OUT-able («без проактивных») | ON |
| **N12** | `master.schedule_change_request.response` | master | OPERATIONAL — cannot disable | ON |
| **N13** | `daily_digest.morning` | master + owner | OPT-OUT-able | ON (master) / ON (owner) |
| **N14** | `weekly_digest` | owner | OPT-OUT-able | ON |
| **N15** | `escalation.urgent` (HUMAN_LOCKED / complaint / payment failed) | owner + assigned admin | OPERATIONAL — cannot disable | ON |
| **N16** | `escalation.master_request` (ScheduleChangeRequest submitted) | owner | OPT-OUT-able (delegate to admin) | ON |
| **N17** | `analytics.insight` (pattern detected) | owner | OPT-OUT-able | ON |
| **N18** | `persona.violation.weekly` | owner | OPT-OUT-able | ON |

«Operational — cannot disable» means: notification fires regardless of preferences. Customer/master/owner sees it. Justification per category:
- Booking confirmation: customer needs to know it landed
- T-24h / T-2h / T-15min reminders: customer-side commitment + reduces no-show
- Cancellation / reschedule: all parties need to know booking changed
- ScheduleChangeRequest response: operational dialog flow
- Urgent escalation: business-critical (complaint / payment / HUMAN_LOCKED)

If user explicitly demands stopping operational: requires admin escalation per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md). NEVER UI toggle for these.

---

## 3. Customer-side notification preferences

### 3.1 What customer controls

Per Q-CX9 decision: **single master toggle «без проактивных»** + per-module wellness consent. NOT 14 individual switches (decision fatigue + low value).

```
┌────────────────────────────────────────┐
│ ← Уведомления                          │
├────────────────────────────────────────┤
│ От помощника студии «{{salon_name}}»   │
│                                        │
│ Что приходит всегда:                   │
│   ✓ Подтверждение записи               │
│   ✓ Напоминания о визите               │
│   ✓ Уведомления об изменениях          │
│   (Это нужно для записи, не отключить.)│
│                                        │
│ ── Опционально ──                      │
│                                        │
│ ☑ Помощник может писать первым         │
│   (про самочувствие, повторные         │
│    процедуры, акции)                   │
│                                        │
│ Если выключить — будете получать       │
│ только то, что выбрали сами.           │
│                                        │
│ ── Дополнительные модули ──            │
│                                        │
│ ☐ Напоминания пить воду                │
│ ☐ Ежедневный «как самочувствие?»       │
│ ☐ Прогресс по фото (раз в месяц)       │
│ ☐ Заметки по симптомам                 │
│                                        │
│ [Подробнее о модулях →]                │
└────────────────────────────────────────┘
```

### 3.2 «Без проактивных» toggle (master switch)

When OFF:
- N8 post-visit check-in: SUPPRESSED
- N9 no-show gentle check: SUPPRESSED
- N11 marketing campaigns: SUPPRESSED
- N17-equivalent customer-side insights: SUPPRESSED

Still fires:
- All transactional reminders (N1-N6)
- Customer-initiated AI Q&A responses
- Wellness module reminders (only if customer ALSO opted in to specific module)

### 3.3 Wellness module per-module consent

Per [`wellness-input-modules §11`](./wellness-input-modules.md#11-consent-layering):
- Each module = independent opt-in
- Default OFF for all (per memory project_attribution_extensible_model strict privacy)
- Customer enables at module activation (NOT bulk in this settings screen)
- This settings screen shows toggle list as MIRROR of state set elsewhere (during module activation)

Settings page CAN turn OFF here but cannot turn ON here — turning on requires going through the module activation flow with its consent dialog (privacy-first: full informed consent context).

### 3.4 «Подробнее о модулях» link

Opens read-only info screen describing what each module sends + how often + privacy stance. Pure transparency tool.

### 3.5 Save / undo behavior

- Changes save immediately on toggle (no «Save» button)
- 5-sec undo toast at bottom («Сохранено · Отменить»)
- After 5s: persisted; can be changed any time

### 3.6 Events emitted on customer preference change

Per [`event-taxonomy.md §3.2`](./event-taxonomy.md#32-customer-domain):
- `customer.consent.changed` with `consent_type` = `proactive_messaging` or specific module name
- Audit trail preserved

### 3.7 What customer sees if they tap «без проактивных» OFF

When toggle goes OFF, brief educational toast:
```
Готово. Помощник больше не будет писать первым.

Если что-то понадобится — пишите сами, отвечу.
```

When toggle goes back ON:
```
Готово. Помощник снова может писать. Можно поставить тон — поспокойнее.

[Выбрать тон →]
```

(«Выбрать тон» link opens future tone-preference; Phase 2+. MVP just acknowledgement.)

---

## 4. Master-side notification preferences

### 4.1 What master controls

Per [`master-conversational §8 frequency matrix`](./master-conversational-templates.md#8-notification-frequency-policy):

Master has more granular controls than customer because master is OPERATIONAL user with high-volume notifications.

```
┌────────────────────────────────────────┐
│ ← Уведомления                          │
├────────────────────────────────────────┤
│ Что приходит от помощника:             │
│                                        │
│ Утренняя сводка                        │
│   ⦿ Отправлять каждое утро             │
│   ◯ Открою сама в Mini App             │
│   ◯ Сводка кратко (свернутая)          │
│                                        │
│ Новая запись                           │
│   ⦿ Сразу, как появится                │
│   ◯ В вечерней сводке (раз в день)     │
│   ◯ Не отправлять                      │
│                                        │
│ Отмена / перенос записи                │
│   ⦿ Сразу (рекомендуется)              │
│   ◯ В сводке                           │
│                                        │
│ Клиент на подходе (10 мин до записи)   │
│   ⦿ Сразу                              │
│   ◯ Не нужно (вижу расписание сама)    │
│                                        │
│ ── Не отключаются ──                   │
│                                        │
│ • Ответы по запросу на изменение       │
│   расписания                           │
│ • Жалобы клиентов                      │
│ • Экстренные сообщения от владельца    │
│                                        │
│ ── Тишина ──                           │
│                                        │
│ Нерабочее время:                       │
│   ⦿ По расписанию (выходные + не-      │
│      рабочие часы)                     │
│   ◯ Никогда (всегда уведомлять)        │
│   ◯ Своё время: [с __:__ до __:__]    │
│                                        │
└────────────────────────────────────────┘
```

### 4.2 Default settings rationale per master event

- **Morning digest**: ON full because most masters want it; lightweight to opt-down to compact
- **New booking immediate**: ON because operational urgency (master may need to prepare)
- **Cancel/reschedule immediate**: ON because most operationally critical
- **Pre-arrival ping immediate**: ON for first 30 days (helps onboarding); offer toggle to OFF after master shows pattern of checking schedule manually
- **DND**: «По расписанию» = respect WorkingHours + ScheduleException; default ON. Master can override.

### 4.3 «Не отключаются» list

Operational notifications master CANNOT turn off:
- ScheduleChangeRequest responses (their own requests)
- Customer complaint escalations involving them
- Direct messages from owner via assistant

Why: each represents action master must see to function as part of team. Removing toggle prevents «I missed the complaint about me».

### 4.4 Audit + transparency

Master can see when settings changed + who (always self for master):
```
Изменено: 14 мая, 14:32
```

### 4.5 Events emitted on master preference change

- `master.notification_preferences.changed` (NEW — add to event-taxonomy §3.3)
- Audit row in audit table

---

## 5. Owner-side notification preferences

### 5.1 Owner has 3 layers of preferences

1. **Operational alerts** (always on — like master's «не отключаются»)
2. **Daily / weekly digest** (preferences for tempo + content)
3. **Insights + analytics** (frequency, severity threshold)

```
┌────────────────────────────────────────┐
│ ← Уведомления (вы — владелец)          │
├────────────────────────────────────────┤
│ Срочные (всегда):                      │
│   • Жалобы клиентов                    │
│   • Проблемы с оплатой                 │
│   • Запросы от мастеров (изменения)    │
│   • HUMAN_LOCKED-конверсации          │
│                                        │
│ ── Сводки ──                           │
│                                        │
│ Ежедневная сводка                      │
│   ⦿ В 8:30 утра                        │
│   ◯ В Mini App, без DM                 │
│   ◯ Не отправлять                      │
│                                        │
│ Недельная сводка                       │
│   ⦿ Понедельник утром                  │
│   ◯ Воскресенье вечером                │
│   ◯ Не отправлять                      │
│                                        │
│ ── Инсайты ──                          │
│                                        │
│ Замеченные паттерны                    │
│   ⦿ Только важные                      │
│   ◯ Все наблюдения                     │
│   ◯ Не присылать                       │
│                                        │
│ Нарушения голоса помощника             │
│   ⦿ Еженедельно в сводке               │
│   ◯ Сразу при нарушении (>5/неделю)    │
│   ◯ Только в Mini App                  │
│                                        │
│ ── Тишина ──                           │
│                                        │
│ Нерабочее время:                       │
│   ⦿ С 22:00 до 9:00 (срочное всё равно│
│      придёт)                            │
│   ◯ Никогда                             │
│   ◯ Своё время: [с __:__ до __:__]    │
│                                        │
│ ── Делегирование ──                    │
│                                        │
│ Запросы мастеров на расписание         │
│   ⦿ Мне + админам                      │
│   ◯ Только админам                     │
│                                        │
└────────────────────────────────────────┘
```

### 5.2 Default rationale per owner event

- **Daily digest 8:30**: most owners want morning briefing; configurable
- **Weekly Monday morning**: aligns with planning rhythm
- **Insights only-important**: signal-to-noise good; can opt to all for power users
- **Persona violations weekly**: balanced (not spam, not invisible); opt-up to per-violation for high-quality-mandate
- **DND 22-9**: protect personal time; «срочное всё равно придёт» disclaimer
- **Delegation**: owner can route ScheduleChangeRequests to admins-only (owner has admin layer; admins handle ops)

### 5.3 Admin variant (owner-templates §14 admin scoping)

Admin role sees same UI BUT:
- «Делегирование» section hidden (admin is the recipient, not delegator)
- «Срочные» section shows admin-scoped subset (admin doesn't get billing escalation; only owner does)
- «Инсайты» section: admin sees if owner enabled admin access to insights; otherwise hidden

### 5.4 Events emitted on owner preference change

- `admin.settings.updated` per [event-taxonomy §3.10](./event-taxonomy.md#310-admin--system-domain) with `setting_path = 'notification_preferences'`
- Audit row

---

## 6. Frequency caps + throttling

Even with notifications ON, system caps to avoid spam.

### 6.1 Per-audience caps

| Audience | Max bot DM per day | Max bot DM per hour | Burst exception |
|---|---|---|---|
| Customer | 5 | 2 | Booking confirmation + T-24h reminder = same day OK (separate buckets) |
| Master | 30 | 10 | First-30-day onboarding can spike, then steady-state |
| Owner | 20 | 8 | Urgent escalations exempt from cap |

### 6.2 Throttling logic

If notification would exceed cap → BATCH it into next allowed window. NEVER drop silently. Examples:

- Customer has 5 bookings today AND it's also their birthday → birthday touch BATCHED to next day morning OR suppressed if customer opted out of birthday
- Master gets 12 new bookings in 1 hour during peak → bot DMs them individually (booking notifications are operational, exempt from per-hour cap)
- Owner has spike of 5 escalations within 10 minutes → all 5 DM'd (urgent exempt), but aggregated dashboard banner provided

### 6.3 Dedup logic

Same notification type within X minutes for same recipient → coalesce.
- Customer: same booking reminder series can only fire each component once
- Master: rapid-fire bookings (3 in 2 min) coalesce into «+3 записи на завтра» single DM
- Owner: bursts of 5 escalations within 5 min coalesce into «5 жалоб за последние минуты — проверьте» single message + dashboard for detail

---

## 7. DND (Do Not Disturb) windows

### 7.1 Customer DND

Default: customer's local 22:00-9:00 → batch non-urgent messages to next 9:00. Booking confirmations and T-24h/T-2h reminders ARE delivered (operational); birthday / reactivation / wellness reminders are queued.

«Урgent customer» concept doesn't exist — there are no «urgent» customer DMs by design.

### 7.2 Master DND

Per [`master-conversational §8`](./master-conversational-templates.md#8-notification-frequency-policy):
- Default: outside `WorkingHours`
- Override: «никогда» (always disturb) or «своё время»
- Even DND-active: critical (complaint about them, owner direct message) bypasses

### 7.3 Owner DND

Default: outside tenant TZ working hours (configurable).
- Urgent exemption: payment failed, customer complaint, system down
- Always queues non-critical (insights, digests) to next working morning

### 7.4 DND UI affordance

Each DND section in settings shows preview:
```
Тишина включена с 22:00 до 9:00 (ваше время).
Срочные сообщения всё равно придут.
```

If override = «никогда» / «всегда уведомлять»:
```
Тишины нет — все сообщения приходят сразу.
```

---

## 8. Cross-channel fallback rules

**MVP: MAX-only**. If MAX delivery fails (handle invalid, account suspended) → in-app banner on next Mini App open + alert to owner.

Phase 4+ channel fallback:
- Customer: MAX → email (if customer provided + opted)
- Master: MAX-only operational (master has MAX, alternative isn't planned)
- Owner: MAX → email summary daily (Phase 4)

Banner template for next-Mini-App-open after delivery failure:
```
{{N}} сообщений не дошли — посмотрите.

[Открыть]
```

---

## 9. Privacy boundaries

| Setting visibility | Customer | Master | Owner | Admin |
|---|---|---|---|---|
| Own preferences | ✅ edit own | ✅ edit own | ✅ edit own | ✅ edit own |
| Customer's preferences | ✅ self | ❌ | ❌ | ❌ (privacy boundary) |
| Master's preferences | ❌ | ✅ self | ✅ aggregate view (audit purpose) | ✅ aggregate |
| Owner's preferences | ❌ | ❌ | ✅ self + admin can view if owner shared | ✅ self |
| Cross-tenant preferences | NEVER | NEVER | NEVER | NEVER |

Owner CAN see aggregate stats like «8 of 10 masters have digest ON» for operational planning; CANNOT see specific master's settings detail without explicit consent (privacy).

---

## 10. Settings UI navigation

### 10.1 Where preferences live per audience

- **Customer**: Mini App → Профиль → Уведомления (per [information-architecture.md](./information-architecture.md) Профиль surface)
- **Master**: Mini App → Профиль → Уведомления (master-side IA, same path conceptually)
- **Owner**: Mini App → Настройки → Уведомления (owner-side has separate Settings Hub per [owner-conversational-templates §6.8 / §6.10](./owner-conversational-templates.md))

### 10.2 Discoverability

- Customer: surfaced once at end of M-first-week customer flow («Хотите настроить как часто буду писать?»)
- Master: surfaced during M2 wizard final step («Уведомления настроены по умолчанию — изменить можно позже»)
- Owner: surfaced during onboarding Phase 5 («Настройте, как часто буду присылать сводки»)

### 10.3 Search / quick access

Mini App settings tab has «Поиск настроек» bar (Phase 2+). Phase 1: linear section list.

---

## 11. Save / undo / rollback patterns

### 11.1 Save behavior
- Per toggle / radio: save immediately on change
- Per text input (e.g., DND custom hours): save on blur or explicit Save button

### 11.2 Undo
- 5-sec toast after save
- Tap «Отменить» reverts

### 11.3 Rollback (rare)
- If user wants to revert older changes: navigate to «История изменений» in settings (Phase 2+)
- MVP: changes are atomic; no built-in rollback UI

### 11.4 Conflicting settings
If user enables «без проактивных» AND has wellness module opt-ins:
- Wellness modules continue (they're customer-initiated opt-ins; master switch doesn't override)
- Settings page shows clarifying line: «Модули, которые вы включили сами, продолжат работать»

---

## 12. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| 14 individual switches for customer | Decision fatigue | Single master toggle + module-specific opt-ins |
| «Are you sure?» before every toggle | Treats users as children | Toggle saves immediately + undo toast |
| Operational notifications disable-able | Customers miss reminders → no-shows | OPERATIONAL class never disable-able; explained |
| Hidden notification fired without user awareness | Trust violation | Every notification type listed in preferences screen |
| Master DM at 3am with non-critical | Sleep disruption | Respect DND; queue to next working window |
| Customer-side preferences include marketing «we recommend ON» nudge | Manipulative | State neutral facts; user decides |
| Toggle copy with negative phrasing («Block notifications») | Disempowering | Positive: «Помощник может писать первым» (ON = consent) |
| Owner sees master's specific settings | Privacy violation | Aggregate only |
| Preference change without audit trail | Compliance gap | Always emit event + audit row |
| Multi-step undo (modal asking «really undo?») | Friction | One-tap undo in 5s window, then committed |
| Sound effects on every notification toggle save | Annoying | Silent save + visual feedback only |

---

## 13. Localization

### MVP: RU only

### RU specifics
- «Уведомления» (notifications)
- «Тишина» (silence/DND) — softer than transliterated «DND»
- «Сводка» (digest)
- «Без проактивных» (without proactive) — terse Russian idiom
- All toggle labels in active voice («Помощник может писать»), not passive («Уведомления разрешены»)

### Phase 4+
- Per-language re-author of toggle copy
- Time format already locale-aware (24h MVP RU)

---

## 14. Accessibility (WCAG 2.2 AA)

- All toggle labels have clear `aria-label` describing on/off state
- Radio groups have proper `<fieldset>` + `<legend>` structure
- 44×44 touch targets for toggles
- 4.5:1 contrast on all labels
- Settings save toast: `role="status"` for SR announcement
- DND time inputs: keyboard accessible time picker
- Focus order: top-to-bottom logical
- Reduced motion: undo toast slide-in animation respects `prefers-reduced-motion`

---

## 15. Operational events emitted on preference change

Per [`event-taxonomy.md`](./event-taxonomy.md):

| Event | Audience | Payload key |
|---|---|---|
| `customer.consent.changed` | customer | `consent_type` (proactive_messaging / wellness_module_X / etc.) |
| `master.notification_preferences.changed` | master | `setting_path`, `old`, `new` |
| `admin.settings.updated` | owner/admin | `setting_path = 'notification_preferences'`, `old`, `new`, `changed_by` |
| `system.module.health.degraded` | system | If MAX delivery fail rate >5% per audience — operational alert |

Add to event-taxonomy.md §3.3 (master domain): `master.notification_preferences.changed`.

---

## 16. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-NP1** | Customer single «без проактивных» toggle OR per-event-type toggles? | Single per Q-CX9 decision (avoid decision fatigue) | UX | ✅ decided as single |
| **Q-NP2** | Wellness module toggles show ALL or only ones customer has activated? | Show ALL with state; lets customer turn ON via path with consent dialog (privacy-first) OR turn OFF directly | UX | 🟢 |
| **Q-NP3** | Master onboarding M2 wizard mention notification settings — full review OR «defaults applied, change later»? | Defaults applied + brief mention; full review only on master's initiative | UX | 🟢 |
| **Q-NP4** | Customer DND default 22-9 — per-tenant configurable or platform fixed? | Platform fixed MVP (most consistent customer experience); per-tenant v1.2+ | PM | 🟢 |
| **Q-NP5** | Master DND respect both `WorkingHours` AND `ScheduleException` (vacation period)? | YES — vacation = full DND for that period; customer-related notifications batched to return date | Eng + UX | 🟡 |
| **Q-NP6** | Owner DND override per-audience (e.g., always urgent from VIP customers)? | NO MVP — single owner DND with urgent exemption only. v1.2+ tiered VIP customer escalation | UX | 🟢 |
| **Q-NP7** | Quiet hours for customer = customer's local TZ, but tenant TZ might differ — which applies for delivery timing? | Customer TZ for delivery scheduling; tenant TZ only for business-context timestamps | Eng | 🟡 |
| **Q-NP8** | Frequency cap exceeded — drop notification or queue? | Queue (per §6.2) — never silent drop | Eng | 🟡 |
| **Q-NP9** | Master operational notifications «не отключаются» — list copy customer-facing? | Show explicitly as «Не отключаются:» list in preferences UI — transparency | UX | 🟢 |
| **Q-NP10** | Aggregate stats for owner («8 of 10 masters have digest ON») — show what aggregate? | Phase 2+; MVP not surfaced. Privacy + low operational value initially | PM | 🟢 |
| **Q-NP11** | Customer toggles «без проактивных» mid-conversation (in flight) — affects current conversation? | NO — current conversation continues; new state applies to NEXT proactive trigger | UX | 🟡 |
| **Q-NP12** | Owner can override customer's «без проактивных»? | NEVER — customer consent is absolute. Owner sees «N customers opted out» metric only | Policy | 🟢 |
| **Q-NP13** | Settings UI search bar — MVP or Phase 2+? | Phase 2+ — settings list short enough in MVP for linear scan | UX | 🟢 |
| **Q-NP14** | Preference change rate-limit (anti-abuse prevent flapping)? | YES — max 10 preference changes per hour per user; over → cooldown 30 min with friendly message | Eng | 🟢 |
| **Q-NP15** | Audit log retention for preference changes — tied to Layer 2 (365d) or Layer 3 (7y)? | Layer 2 (365d) for most; Layer 3 (7y) for operational-class re-enable (compliance traceability) | Legal | 🟡 |
| **Q-NP16** | Migration path — existing customers (pre-r1) get default settings retroactively or migrate from their behavioral data? | Default settings retroactively; behavior-based migration adds privacy risk and complexity | Eng + Policy | 🟡 |
| **Q-NP17** | If tenant moves to suspended (billing failed) — do customer preferences still respected for the queued reminders? | YES — operational reminders for existing bookings continue; only new dispatch suppressed | Policy | 🟡 |

---

## 17. Cross-document linkage

- [`conversational-ux-framework.md`](./conversational-ux-framework.md) §5 — customer-facing templates that get throttled here
- [`master-conversational-templates.md`](./master-conversational-templates.md) §8 — master's frequency matrix; this doc formalizes UX
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) §10 + §6 — owner DND + digest preferences; this doc adds UI
- [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) §7 — cancellation notification cascade
- [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md) §3 — silent-on-arrival rule respects DND
- [`wellness-input-modules.md`](./wellness-input-modules.md) §11 — module-level consent integration
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) §3 — operational escalation exemption from caps
- [`information-architecture.md`](./information-architecture.md) — settings paths per audience
- [`master-onboarding-m0-m7.md`](./master-onboarding-m0-m7.md) §5.7 — discoverability hook during master onboarding
- [`event-taxonomy.md`](./event-taxonomy.md) §3.2 + §3.10 — preference change events
- [`../handoffs/2026-05-18-settings-hub-handoff.md`](../handoffs/2026-05-18-settings-hub-handoff.md) — Settings Hub will reference this for notification settings section
- [`../handoffs/2026-05-18-persona-editor-handoff.md`](../handoffs/2026-05-18-persona-editor-handoff.md) §13 — persona-violations weekly digest preference

---

## 18. What this unblocks

- **Settings Hub refresh** — notification preferences section now has full UX
- **Customer Profile management UX** (future doc) — opt-out section already specified
- **Customer Mini App F-profile screen** — toggle UI rendering
- **Master Mini App Profile screen** — settings panel
- **Owner Mini App Settings → Уведомления** — full screen designed
- **Backend preference schema** — per-audience JSON shape clear
- **Throttling + DND logic** — operational rules locked
- **Cross-channel future-proofing** — Phase 4 channels stub schema-ready

## 19. What this does NOT unblock

- ❌ Push notifications outside MAX (platform limitation)
- ❌ SMS / Email channels (Phase 4+)
- ❌ Sound / vibration preferences (MAX bot DM uses MAX default)
- ❌ Tenant-level overrides for customer DND (Q-NP4 lean platform-fixed MVP)
- ❌ Tiered VIP escalation (Q-NP6 v1.2+)
- ❌ Skip persona-conformance linter on notification copy — every template must pass

---

## 20. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Mini App frontend (customer + master + owner Profile/Settings screens) | ☐ | |
| Backend (preference schema + throttling) | ☐ | |
| AI prompt engineering (default settings consistency with templates) | ☐ | |
| Privacy / Legal (Q-NP15 retention + Q-NP12 owner-can-override rule) | ☐ | |
| Accessibility (WCAG 2.2 AA review on settings UI) | ☐ | |

## Last verified
2026-05-19 (initial draft, notification preferences UX consolidated across audiences)
