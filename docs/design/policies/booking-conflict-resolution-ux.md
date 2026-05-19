# Booking Conflict Resolution UX

**Date:** 2026-05-19 r1
**Status:** Production-critical — closes operational gap between app-side bookings and YClients admin-side reality
**Reads:** [`yclients-integration-architecture.md`](./yclients-integration-architecture.md), [`booking-policy.md`](./booking-policy.md), [`attribution-policy.md`](./attribution-policy.md), [`conversation-ownership-policy.md`](./conversation-ownership-policy.md), [`single-assistant-identity.md`](./single-assistant-identity.md), [`event-taxonomy.md`](./event-taxonomy.md), [`schedule-management-ux.md`](./schedule-management-ux.md), [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md), [`assistant-persona.md`](./assistant-persona.md), [`master-conversational-templates.md`](./master-conversational-templates.md)

> YClients is salon's source of truth for schedule reality (admin's calendar). Our app is source of truth for AI conversation context + customer identity. Conflicts between these two views WILL happen — sync delays, admin manual edits in YC, customer phone collisions, catalog drift. This policy specifies HOW we detect, classify, resolve, and surface each conflict — without ever showing raw error to customer.

---

## 0. Why this exists

### 0.1 Strategic context

Per [`yclients-integration-architecture.md`](./yclients-integration-architecture.md): YClients is one CRM integration of several, but for tenants using it, YC is the operational source of truth for the schedule. Admins manage their day in YC (manually book walk-ins, move appointments, mark no-shows). Customers + AI work through our app. **These two streams DIVERGE constantly**, and silent divergence destroys trust:

- Customer arrives at salon → master never got the booking (catastrophic CX failure)
- AI confirms slot → admin already filled it in YC (double-booking)
- Customer gets reminder → booking was cancelled in YC 3 days ago by admin
- AI references «ваша запись на завтра» → YC shows different time
- Service price changed in YC → customer paid old price quoted by AI

Without a robust conflict resolution layer, the AI assistant promise («помощник студии знает всё что важно») is hollow.

### 0.2 The gap

- `yclients-integration-architecture.md` specifies SYNC mechanics
- `booking-policy.md` specifies in-app booking
- Neither specifies WHAT HAPPENS when sync detects divergence
- No spec for customer-facing UX during conflict
- No spec for admin tools to resolve conflict
- No audit model for divergences

### 0.3 The promise

Single source for:
- 8 canonical conflict types §3 with detection + classification rules
- Per-conflict resolution path (auto / admin / founder)
- Per-conflict customer-facing UX (NEVER raw error)
- Admin conflict resolution UI (Mini App + email)
- AI behavior during conflict windows (defer to admin / lock conversation / etc.)
- Conflict audit model + retention
- Reconciliation engine specification
- SLA matrix per conflict severity

---

## 1. Scope

### IN
- 8 conflict types §3 (double-booking, phantom-YC, phantom-app, status-divergence, time-mismatch, entity-drift, identity-collision, catalog-desync)
- Detection mechanisms (real-time + batch reconciliation)
- Classification matrix (severity × ownership × customer-facing-blast)
- Resolution flows (auto / admin / founder / cancel)
- Customer-facing UX during conflict window
- Admin Mini App conflict resolution screens
- AI behavior modifiers during open conflict
- Conversation-ownership-policy tier escalation on conflict
- Attribution-policy adjustments on conflict (especially for ai_direct → ai_assisted reclassification)
- Reconciliation engine API + models
- 8 NEW events for event-taxonomy

### OUT
- Multi-CRM conflict (Phase 4+ when 2+ CRMs simultaneously per tenant) — single-CRM MVP
- Cross-tenant conflict (impossible by tenant boundary)
- Predictive conflict (ML to forecast «admin likely to manually edit» — Phase 4+)
- Customer-initiated conflict disputes («это не моя запись») — separate scope, refund-dispute policy
- Master no-show conflict resolution — separate master-payout-ux scope
- Inventory/product conflicts (we don't sync product inventory) — out of scope
- YClients API outages (graceful degradation) — separate scope `yclients-outage-policy.md` (future)
- Master-to-master booking handoff conflicts — Phase 3+ feature
- Calendar-app conflicts (Google Calendar etc.) — Phase 4+ if ever integrated

---

## 2. Strategic constraints — non-negotiable

### 2.1 YClients = source of truth for SCHEDULE
When app and YC disagree on slot reality (time, status, master assignment) → YC wins by default. Exception: §3.7 identity-collision (customer identity is OUR source of truth — YC can have duplicates).

### 2.2 Customer NEVER sees raw error
- ❌ «Sync error 503»
- ❌ «Booking ID mismatch»
- ❌ «Conflict detected — please contact admin»
- ✅ «Уточняю детали с мастером, вернусь в течение N минут»
- ✅ «Похоже, время сдвинулось — давайте подберём удобное»
- ✅ «Запись подтвердилась» (after resolution)

### 2.3 Single-assistant identity preserved
Per [`single-assistant-identity.md`](./single-assistant-identity.md) — AI NEVER says «у нас ошибка синхронизации с YClients». Customer sees one voice. Internal mechanism invisible.

### 2.4 Every conflict produces audit
- `BookingConflict` row §10
- 8 events per §12
- 30-day retention minimum for analytics
- Founder-side conflict dashboard Phase 3+

### 2.5 Tier escalation per ownership policy
Per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md):
- Conflict opened → tier escalates per severity §4.4
- Resolution closes tier back down

### 2.6 Attribution re-evaluation
Per [`attribution-policy.md`](./attribution-policy.md):
- Conflict resolution can RECLASSIFY booking_source (ai_direct → ai_assisted if admin had to intervene)
- billable / billing_reason updated atomically
- audit metadata captures reason

### 2.7 SLA per severity §4.5
- CRITICAL (double-booking customer-imminent): 15 min
- HIGH (status-divergence visible): 60 min
- MEDIUM (entity-drift): 4 hours
- LOW (informational divergence): 24 hours

### 2.8 No silent overwrites
Even if YC wins per §2.1, app does NOT silently overwrite local state. Always: detect → audit → apply with reason → events emitted.

### 2.9 Customer trust > admin convenience
When trade-off exists between «admin's clean calendar» and «customer's expectation honored» — customer wins. Example: if app booking conflicts with admin's last-minute walk-in entered in YC → app booking is HONORED, admin handles walk-in differently.

Exception: if customer hasn't been notified yet (booking <5 min old) → may favor admin.

---

## 3. Canonical conflict types

### 3.1 Type DOUBLE_BOOKING

**Definition:** Same master + overlapping time slot has 2+ bookings across app + YC.

**Detection:**
- Real-time: on app booking confirm, query YC `/book_dates`; if collision → block + classify
- Batch: hourly reconciliation diff app.bookings ∩ yc.records by master_id × time_range

**Classification:**
- Severity CRITICAL if either booking is <24h from now
- Severity HIGH if 24-72h
- Severity MEDIUM if >72h

**Resolution path:**
- Per §2.9: if app booking was confirmed to customer (notification sent) → app wins, admin handles YC entry
- If app booking <5min old + no notification yet → YC wins, app booking auto-cancels with §6.1 customer-soft message
- Admin notified via §7.2 admin Mini App «Конфликт расписания» screen

**Customer UX:** §6.1

**AI behavior:** Pause auto-confirmations for this master ±30min slot until resolved.

### 3.2 Type PHANTOM_YC

**Definition:** YC has booking that doesn't exist in our app's customer-attributed bookings (i.e., admin manually entered in YC, no app conversation).

**Detection:**
- Batch reconciliation: yc.records WHERE id NOT IN app.bookings.yc_record_id
- For each phantom: try identity match by phone → if matched, create app shadow booking with `booking_source='human_direct'` per attribution-policy

**Classification:**
- Severity LOW if customer-phone matches existing app customer
- Severity MEDIUM if no customer match (potential new customer)
- Severity HIGH if phone matches but customer has active AI conversation about same time-slot (admin double-booked over AI)

**Resolution path:**
- Identity-matched: auto-create shadow booking + emit `booking.phantom_yc_resolved`
- No identity match: skip (don't fabricate customer); admin alerted Phase 3+
- HIGH severity: §7.3 admin-required reconciliation; AI defers conversation per §5.4

**Customer UX:** Usually transparent (customer doesn't see phantom resolution). If HIGH → §6.2.

### 3.3 Type PHANTOM_APP

**Definition:** App has booking confirmed to customer that doesn't appear in YC (sync push failed).

**Detection:**
- Real-time: app booking emits sync-to-YC; if YC API 4xx/5xx after 3 retries → mark phantom_app + open conflict
- Batch: app.bookings WHERE yc_record_id IS NULL AND created_at < 10min ago

**Classification:**
- Severity CRITICAL if customer notified (`booking.confirmed` event already emitted to customer)
- Severity HIGH otherwise

**Resolution path:**
- Retry sync 3× with exponential backoff
- If still failing → admin alerted §7.4 with one-click «принять запись в YC вручную» CTA
- If admin doesn't act within SLA §4.5 → §6.3 graceful customer fallback

**Customer UX:** §6.3 — if needs to escalate, AI says «Уточняю мастера — вернусь в течение N минут», NEVER «ошибка».

### 3.4 Type STATUS_DIVERGENCE

**Definition:** Booking exists in both systems but status differs (e.g., CANCELLED in YC, ACTIVE in app, or vice versa).

**Detection:**
- Real-time webhook: YC fires status-change → app cross-checks
- Batch: SELECT bookings WHERE app.status != yc.status

**Status mapping:**
| YC status | App status | Action if mismatch |
|---|---|---|
| Confirmed (1) | confirmed | (no conflict) |
| Cancelled (-1) | cancelled | (no conflict) |
| Confirmed | cancelled | YC wins § 2.1 — re-activate app + audit |
| Cancelled | confirmed | YC wins — cancel app + notify customer §6.4 |
| No-show (2) | confirmed | YC wins — mark no-show + handoff to billing per attribution-policy |

**Resolution path:**
- Auto-apply YC status to app
- If customer was previously notified «вы записаны» and now booking is cancelled in YC → §6.4 customer message
- Emit `booking.status_divergence_resolved`

**Customer UX:** §6.4

### 3.5 Type TIME_MISMATCH

**Definition:** Booking exists in both systems but slot_start differs by >5 min.

**Detection:**
- Real-time webhook on YC time-change → app cross-checks
- Batch: SELECT WHERE abs(app.slot_start - yc.slot_start) > 5min

**Resolution path:**
- YC wins § 2.1 — app updates slot_start
- If customer notified of original time → §6.5 «расписание уточнилось — сейчас {{new_time}}, всё ещё подходит?»
- If customer doesn't confirm new time within 2h → auto-cancel + handoff per §5.5

**Customer UX:** §6.5

### 3.6 Type ENTITY_DRIFT

**Definition:** Service / master / price for an existing booking changed in YC after booking confirmation.

**Detection:**
- YC webhook on service/master/price edit → app cross-checks affected bookings
- Batch: nightly reconciliation

**Sub-types:**
- 3.6a service_id changed (service replaced / renamed)
- 3.6b master_id changed (master substitution — usually master leaves studio)
- 3.6c price changed (price updated in YC after booking)

**Resolution path:**
- 3.6a: customer notified §6.6a — «У нас немного изменилось название процедуры на {{new_name}}, суть та же»
- 3.6b: customer notified §6.6b — «{{old_master}} не сможет {{date}} — могу предложить {{alternatives}}»
- 3.6c: customer NOT notified of price change (booking honors original price per §2.9); admin sees discrepancy in admin Mini App

**Customer UX:** §6.6 sub-flows

### 3.7 Type IDENTITY_COLLISION

**Definition:** Same phone number maps to multiple customer records (app + YC, or YC has duplicates).

**Detection:**
- On any sync operation that touches customer record
- Phone normalization (E.164) → if multiple `Customer` rows match → flag

**Per §2.1 exception:** App identity is source of truth (we have rich AI context per customer). YC duplicates are merged INTO our canonical record.

**Resolution path:**
- App keeps canonical customer record
- YC duplicates linked via `yc_client_ids` array on canonical customer
- Admin alerted §7.5 to merge in YC manually (one-click button)
- AI proceeds with canonical customer profile (no customer-facing disruption)

**Customer UX:** Transparent.

### 3.8 Type CATALOG_DESYNC

**Definition:** YC service catalog has been updated (added / removed / restructured) — app's cached service catalog is stale.

**Detection:**
- YC webhook on catalog change
- Batch nightly catalog snapshot diff

**Resolution path:**
- Auto-pull fresh catalog from YC
- Re-link existing bookings if service_id changed (per §3.6a)
- If service REMOVED from YC + future bookings reference it → §7.6 admin handles per-booking
- Mini App service browse uses fresh catalog from next request

**Customer UX:** Transparent unless §3.6a triggers.

---

## 4. Conflict classification matrix

### 4.1 Severity dimensions

| Dimension | Levels |
|---|---|
| **Severity** | CRITICAL / HIGH / MEDIUM / LOW |
| **Customer-imminent** | Yes (<24h until slot) / No |
| **Customer-notified** | Yes (any push sent) / No |
| **Auto-resolvable** | Yes (system can decide) / No |

### 4.2 Severity → SLA matrix §2.7

| Severity | Detection-to-resolution SLA | Owner |
|---|---|---|
| CRITICAL | 15 min | AI auto OR admin (paged) |
| HIGH | 60 min | Admin (in-app notification) |
| MEDIUM | 4 hours | Admin (digest) |
| LOW | 24 hours | Admin (digest) |

### 4.3 Auto-resolution eligibility

| Conflict type | Auto-resolvable? |
|---|---|
| DOUBLE_BOOKING | Partial — auto if no customer notified §3.1 |
| PHANTOM_YC | Yes — auto-shadow on identity match |
| PHANTOM_APP | Partial — auto-retry; manual if persist |
| STATUS_DIVERGENCE | Yes — auto-apply YC |
| TIME_MISMATCH | Auto-apply YC but customer must confirm |
| ENTITY_DRIFT 3.6a/b | Customer must confirm |
| ENTITY_DRIFT 3.6c | Yes — app keeps original price |
| IDENTITY_COLLISION | Yes — app keeps canonical |
| CATALOG_DESYNC | Yes — auto-pull |

### 4.4 Tier escalation matrix (per `conversation-ownership-policy.md`)

| Conflict severity | Tier action |
|---|---|
| CRITICAL | Escalate to HUMAN_SUPERVISED (admin must approve all customer messages until resolved) |
| HIGH | Notify admin; AI continues but defers any time-sensitive promises |
| MEDIUM | Background — AI proceeds normally |
| LOW | Background — silent |

### 4.5 Attribution adjustments (per `attribution-policy.md`)

| Original | Conflict | Post-resolution |
|---|---|---|
| `ai_direct` | Admin had to intervene to resolve | → `ai_assisted` (ai_assist_score 0.5–0.8) |
| `ai_direct` | DOUBLE_BOOKING resolved by canceling AI's booking | → still `ai_direct` for cancelled booking; replacement booking tracked separately |
| `human_direct` | (no AI involvement) | unchanged |
| any | PHANTOM_YC matched into shadow booking | `human_direct` from origin |
| any | STATUS_DIVERGENCE auto-applied | unchanged source but `attribution_metadata` annotated |

`billing_reason` text updated atomically per resolution: «reclassified from ai_direct → ai_assisted on 2026-05-19 due to DOUBLE_BOOKING resolution requiring admin».

---

## 5. AI behavior during conflict

### 5.1 Open-conflict marker

When booking has open conflict, `booking.has_open_conflict=True` flag set. Marker checked at:
- Conversation context load (read flag before any AI message)
- Booking-reference templates (don't say «ваша запись» without checking)
- Reminder generation (skip reminder if conflict open)

### 5.2 Pause auto-confirmations

If open conflict involves master_id X within time-window T:
- AI can describe slot availability for that master/window but CANNOT auto-confirm new booking
- Customer requesting booking → «дайте уточню расписание — вернусь в течение N минут»

### 5.3 Reminder behavior during conflict

If reminder due AND booking has open conflict:
- HIGH/CRITICAL: skip reminder; escalate
- MEDIUM: send reminder with softer framing «уточняю детали — вернусь подтвердить»
- LOW: normal reminder

### 5.4 Deferred conversation on PHANTOM_YC HIGH

Per §3.2 HIGH severity (admin double-booked over AI): AI conversation locked into HUMAN_SUPERVISED tier. AI sends ONE message «уточняю с мастером — вернусь к вам как только разрешу» and waits for admin resolution. NEVER promises specific time during open conflict.

### 5.5 Auto-cancel on no-customer-confirm

§3.5 TIME_MISMATCH and §3.6a/b ENTITY_DRIFT both require customer confirmation. If customer doesn't reply within window:
- TIME_MISMATCH: 2h wait → auto-cancel + AI «не получилось согласовать новое время, запись отменена. Когда удобно вернуться?»
- ENTITY_DRIFT 3.6b: 4h wait → auto-cancel + AI «{{master}} не сможет — давайте перенесём, или попробуем другого мастера?»

### 5.6 AI never reveals conflict to customer

Phrases AI MUST NOT say:
- ❌ «У нас конфликт с YClients»
- ❌ «Произошла ошибка синхронизации»
- ❌ «Админ должен подтвердить»
- ❌ «Возникло несоответствие»

Phrases AI MUST use (per §2.2):
- ✅ «Уточняю детали с мастером — вернусь в течение N минут»
- ✅ «Расписание немного сдвинулось»
- ✅ «Подтверждение чуть задержалось, всё в порядке»

---

## 6. Customer-facing UX (per conflict type)

### 6.1 DOUBLE_BOOKING customer message (when app booking cancels)

```
┌────────────────────────────────────────┐
│ Уточнение по записи                    │
├────────────────────────────────────────┤
│ Хочу сразу написать — это была моя     │
│ оплошность с расписанием. Это время    │
│ уже занято другим клиентом.            │
│                                        │
│ Могу предложить ближайшие свободные:   │
│                                        │
│ • {{slot_1}} — у {{master_1}}          │
│ • {{slot_2}} — у {{master_2}}          │
│ • {{slot_3}} — у {{master_3}}          │
│                                        │
│ Подойдёт какой-то?                      │
└────────────────────────────────────────┘
```

- AI takes ownership («моя оплошность») — preserves single-assistant identity §2.3
- Offers 3 alternatives immediately
- NEVER blames admin / system / customer

### 6.2 PHANTOM_YC HIGH (admin double-booked over AI conversation)

```
┌────────────────────────────────────────┐
│ Уточняю детали                          │
├────────────────────────────────────────┤
│ Дайте уточню — вернусь в течение часа. │
└────────────────────────────────────────┘
```

After admin resolves:
```
┌────────────────────────────────────────┐
│ Хорошие новости                         │
├────────────────────────────────────────┤
│ {{action_summary — e.g., запись         │
│ оформилась на {{time}}, ждём вас}}      │
└────────────────────────────────────────┘
```

### 6.3 PHANTOM_APP (app booking failed to sync to YC)

After 3 retries fail + admin notified, if customer was already notified «вы записаны»:

```
┌────────────────────────────────────────┐
│ Подтверждаю — всё в силе               │
├────────────────────────────────────────┤
│ Запись на {{date}} в {{time}} к        │
│ {{master}} — всё в порядке.            │
│                                        │
│ Если что-то изменится, я обязательно   │
│ напишу заранее.                        │
└────────────────────────────────────────┘
```

Admin Mini App alert separately §7.4. AI defers to admin's manual YC entry. Customer experience preserved.

### 6.4 STATUS_DIVERGENCE — booking cancelled in YC

```
┌────────────────────────────────────────┐
│ Уточнение по записи                    │
├────────────────────────────────────────┤
│ Похоже, у мастера что-то поменялось — │
│ ваше время на {{date}} {{time}} сейчас  │
│ недоступно.                            │
│                                        │
│ Очень жаль, что так получилось. Могу   │
│ предложить ближайшие альтернативы:     │
│                                        │
│ • {{slot_1}}                           │
│ • {{slot_2}}                           │
│ • {{slot_3}}                           │
│                                        │
│ Подходит какое-то?                      │
└────────────────────────────────────────┘
```

### 6.5 TIME_MISMATCH

```
┌────────────────────────────────────────┐
│ Небольшое уточнение                    │
├────────────────────────────────────────┤
│ Расписание чуть сдвинулось — ваше      │
│ время на {{date}} теперь {{new_time}}  │
│ (было {{old_time}}).                   │
│                                        │
│ Всё ещё удобно? Или подберём другое?   │
│                                        │
│ [Да, подходит]   [Подобрать другое]    │
└────────────────────────────────────────┘
```

If no reply in 2h §5.5.

### 6.6 ENTITY_DRIFT sub-flows

**6.6a service renamed:**

Only notify if material rename. Cosmetic «классическая стрижка → классическая стрижка женская» — silent. Material «маникюр → маникюр + покрытие» — notify:

```
┌────────────────────────────────────────┐
│ Небольшое обновление                    │
├────────────────────────────────────────┤
│ Процедура, на которую вы записаны,     │
│ называется теперь {{new_name}} (раньше  │
│ {{old_name}}) — суть та же, цена та же. │
│                                        │
│ Просто чтобы вы не путались, когда     │
│ придёте.                               │
└────────────────────────────────────────┘
```

**6.6b master substitution:**

```
┌────────────────────────────────────────┐
│ Извините, не смогу подвести {{master}}  │
├────────────────────────────────────────┤
│ {{master}} в этот день не сможет.      │
│                                        │
│ Могу предложить ту же процедуру у:     │
│                                        │
│ • {{alt_1}} — {{rating}} ⭐ ({{exp}})    │
│ • {{alt_2}} — {{rating}} ⭐ ({{exp}})    │
│                                        │
│ Или перенесём дату — у {{master}}      │
│ есть свободно: {{alt_dates}}           │
│                                        │
│ [{{alt_1}}]  [{{alt_2}}]  [Перенести]  │
└────────────────────────────────────────┘
```

If customer chooses Alt master → original booking cancelled + new created + attribution `ai_assisted` (admin's master_id change triggered, AI handled customer re-confirm).

**6.6c price drift:** customer NOT notified per §2.9. Admin sees in Mini App that this booking is at «legacy price» — booking honors original quote.

### 6.7 Multi-conflict cascade

If booking has multiple conflicts (e.g., TIME_MISMATCH + MASTER_DRIFT both), AI sends ONE consolidated message addressing both — NEVER 2 separate messages 30 seconds apart («ваше время сдвинулось» then «мастер сменился»). Conflict resolution engine waits 2 min for related conflicts to land before notifying.

---

## 7. Admin-facing UX

### 7.1 «Конфликты расписания» Mini App tab

Admin Mini App has dedicated tab when ≥1 open conflict exists:

```
┌────────────────────────────────────────┐
│ 🔧 Конфликты расписания (3)            │
├────────────────────────────────────────┤
│ 🔴 CRITICAL — 1                          │
│ ⚠ HIGH — 1                             │
│ 🟡 MEDIUM — 1                          │
│                                        │
│ ── 🔴 CRITICAL ──                      │
│                                        │
│ Двойная запись                          │
│ Завтра 14:00, мастер: Анна             │
│ Клиенты: Мария Иванова (app) +         │
│          Олег Петров (YC walk-in)       │
│ SLA: 8 мин из 15                        │
│ [Разрешить]                            │
│                                        │
│ ── ⚠ HIGH ──                            │
│                                        │
│ Запись не попала в YC                  │
│ 22 мая 11:00, мастер: Лена             │
│ Клиент: Елена Сидорова                  │
│ SLA: 45 мин из 60                       │
│ [Принять в YC]                          │
│                                        │
│ ── 🟡 MEDIUM ──                        │
│                                        │
│ Цена в YC изменилась                    │
│ Стрижка: 2500 → 2800 ₽                 │
│ Затронуто записей: 4                    │
│ [Посмотреть] [Принять]                  │
└────────────────────────────────────────┘
```

### 7.2 DOUBLE_BOOKING resolution screen

```
┌────────────────────────────────────────┐
│ ← Двойная запись                       │
├────────────────────────────────────────┤
│ Завтра, 14:00, Анна                    │
│                                        │
│ ── Запись 1 (через помощника) ──       │
│ Клиент: Мария Иванова                  │
│ Создано: 19 мая, 10:23                 │
│ Уведомлена: ✓ (10:24)                  │
│ Услуга: Маникюр                         │
│                                        │
│ ── Запись 2 (вручную в YC) ──          │
│ Клиент: Олег Петров                    │
│ Создано: 19 мая, 14:15                 │
│ Уведомлен: — (walk-in)                  │
│ Услуга: Стрижка                         │
│                                        │
│ Помощник советует:                     │
│ ✓ Сохранить запись 1 (клиентка         │
│   уведомлена, ждёт)                    │
│ ✓ Переоформить запись 2 на другое      │
│   время / мастера                       │
│                                        │
│ [Принять рекомендацию]                  │
│ [Решить иначе ▾]                        │
└────────────────────────────────────────┘
```

«Решить иначе» dropdown: cancel-1 / cancel-2 / move-1 / move-2 / split-to-two-masters.

### 7.3 PHANTOM_YC HIGH screen

Surfaces when admin walks-in customer in YC over AI's active slot conversation:

```
┌────────────────────────────────────────┐
│ ⚠ Возможный конфликт                    │
├────────────────────────────────────────┤
│ Вы только что записали в YC:            │
│ {{customer}} на {{time}}                │
│                                        │
│ Помощник прямо сейчас обсуждает с      │
│ другим клиентом то же время:            │
│ {{ai_customer}}                        │
│                                        │
│ Что важнее?                             │
│ ⦿ Запись в YC (AI откажется этому      │
│   клиенту)                              │
│ ◯ Запись AI (отмените запись в YC)    │
│                                        │
│ [Подтвердить]                           │
└────────────────────────────────────────┘
```

### 7.4 PHANTOM_APP CTA

```
┌────────────────────────────────────────┐
│ ⚠ Запись не попала в YC                │
├────────────────────────────────────────┤
│ Клиентка: Елена Сидорова               │
│ Время: 22 мая 11:00                    │
│ Мастер: Лена                            │
│ Услуга: Маникюр                         │
│ Помощник уже подтвердил клиентке.       │
│                                        │
│ Действия:                               │
│ [Принять в YC]   — внесу автоматически │
│ [Открыть YC]     — внесу вручную        │
│ [Отменить]       — клиентка получит    │
│                    альтернативы          │
└────────────────────────────────────────┘
```

«Принять в YC» — admin permission triggers app-side automated YC POST. Audit captured. If still fails after admin-trigger, escalate to founder per §7.7.

### 7.5 IDENTITY_COLLISION admin notice (Phase 3+)

```
┌────────────────────────────────────────┐
│ В YC два клиента с этим номером         │
├────────────────────────────────────────┤
│ Телефон: +7 911 555 7777               │
│                                        │
│ В YC найдены:                           │
│ • Елена Сидорова (created 2024-03)     │
│ • Е.С. (created 2025-07)               │
│                                        │
│ Помощник работает с одним профилем.    │
│ Хотите объединить в YC?                │
│                                        │
│ [Объединить]   [Не сейчас]              │
└────────────────────────────────────────┘
```

### 7.6 CATALOG_DESYNC unresolvable bookings

If service removed from YC + future bookings reference it:

```
┌────────────────────────────────────────┐
│ Услуга «X» удалена из YC                │
├────────────────────────────────────────┤
│ 3 будущих записи ссылаются на неё:      │
│                                        │
│ • 21 мая 11:00 — Мария И.              │
│ • 22 мая 15:00 — Олег П.               │
│ • 25 мая 10:00 — Анна Н.                │
│                                        │
│ Что делать с этими записями?            │
│                                        │
│ [Связать с другой услугой ▾]            │
│ [Сохранить как «прочее»]                │
│ [Отменить (клиенты получат сообщения)] │
└────────────────────────────────────────┘
```

### 7.7 Founder escalation

If admin doesn't resolve within SLA × 2 + critical impact (customer arriving in <2h with unresolved conflict) — founder notified via separate channel (email + Slack if integrated). Founder Mini App tab «Просроченные конфликты» shows escalated items per tenant.

---

## 8. Reconciliation engine

### 8.1 Real-time triggers

| Event | Engine action |
|---|---|
| App `booking.created` | POST to YC; on fail → §3.3 |
| App `booking.cancelled` | DELETE in YC; on fail → audit |
| App `booking.rescheduled` | PATCH YC; on fail → §3.5 |
| YC webhook `record.created` | Match by phone + slot; if no match → §3.2 |
| YC webhook `record.updated` | Compare to app; if divergent → classify |
| YC webhook `record.deleted` | Mark app booking cancelled if matched |
| YC webhook `service.updated` | Trigger §3.6 / §3.8 |
| YC webhook `staff.updated` | Trigger §3.6b on affected bookings |

### 8.2 Batch reconciliation (nightly)

Per tenant per night at 03:00 local time:
1. SELECT yc.records WHERE updated_at > last_sync_at
2. SELECT app.bookings WHERE yc_record_id IS NULL OR last_synced_at < record.updated_at
3. Diff and classify
4. Open conflicts as needed
5. Emit `reconciliation.batch_completed` event

### 8.3 Reconciliation worker

- Idempotent (same conflict not opened twice)
- Backoff on YC API rate limits
- Skips during tenant SUSPENDED state (per `tenant-suspension-pause-ux`)
- Resumes on tenant ACTIVE

### 8.4 Confidence scoring

When matching phantom_yc to app customer:
- Phone exact match + name fuzzy match (Levenshtein) → confidence 0.95
- Phone exact + no name match → 0.8
- Phone partial (last 7 digits) → 0.6
- Confidence < 0.7 → don't auto-match; admin alerted Phase 3+

---

## 9. Audit + observability

### 9.1 Conflict audit row §10.1

Every conflict opens a `BookingConflict` row. Resolution closes it. Audit retained 1 year, then anonymized aggregate.

### 9.2 Founder dashboard (Phase 3+)

- Conflicts/day rate per tenant
- Resolution time distribution
- Conflict-type breakdown
- Customer-facing impact rate (% of conflicts that reached customer message)
- Auto-resolve rate
- Attribution adjustments triggered

### 9.3 PII rules

Conflict audit row stores:
- conflict_type, severity, customer_id (ref), booking_id (ref), resolution
- NEVER raw phone / email / name
- NEVER booking notes / customer messages
- NEVER YC raw API response (only status code + error_class)

### 9.4 Replayable

Every conflict + resolution can be replayed for QA. Conflict event payloads sufficient to reproduce decision path.

---

## 10. Data model

### 10.1 `BookingConflict`

```python
class BookingConflict(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='booking_conflicts')

    CONFLICT_TYPE_CHOICES = [
        ('double_booking', 'Double booking'),
        ('phantom_yc', 'Phantom YC (admin manual)'),
        ('phantom_app', 'Phantom app (sync to YC failed)'),
        ('status_divergence', 'Status divergence'),
        ('time_mismatch', 'Time mismatch'),
        ('entity_drift_service', 'Service drift'),
        ('entity_drift_master', 'Master drift'),
        ('entity_drift_price', 'Price drift'),
        ('identity_collision', 'Identity collision'),
        ('catalog_desync', 'Catalog desync'),
    ]
    conflict_type = models.CharField(max_length=64, choices=CONFLICT_TYPE_CHOICES)

    SEVERITY_CHOICES = [
        ('critical', 'Critical (<24h, customer-imminent)'),
        ('high', 'High (24-72h)'),
        ('medium', 'Medium (>72h or entity drift)'),
        ('low', 'Low (informational)'),
    ]
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)

    primary_booking = models.ForeignKey('booking.Booking', null=True, blank=True, on_delete=SET_NULL, related_name='conflicts_as_primary')
    secondary_booking = models.ForeignKey('booking.Booking', null=True, blank=True, on_delete=SET_NULL, related_name='conflicts_as_secondary')
    # secondary used for double-booking, phantom-yc match

    yc_record_id = models.BigIntegerField(null=True, blank=True)
    # External record involved

    customer = models.ForeignKey('customers.Customer', null=True, blank=True, on_delete=SET_NULL)
    master = models.ForeignKey('staff.Master', null=True, blank=True, on_delete=SET_NULL)

    detection_source = models.CharField(max_length=32)
    # 'yc_webhook_real_time', 'sync_push_fail', 'batch_reconciliation', 'admin_manual'

    customer_notified_at_detection = models.BooleanField(default=False)
    # Whether customer was notified of original booking before conflict opened

    STATUS_CHOICES = [
        ('open', 'Open'),
        ('resolved_auto', 'Resolved by system'),
        ('resolved_admin', 'Resolved by admin'),
        ('resolved_founder', 'Resolved by founder (escalated)'),
        ('cancelled', 'Booking cancelled to resolve'),
        ('expired_no_action', 'SLA breached without action'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='open')

    detected_at = models.DateTimeField()
    sla_due_at = models.DateTimeField()
    # detected_at + SLA per severity

    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by_user = models.ForeignKey('auth.User', null=True, blank=True, on_delete=SET_NULL)

    resolution_action = models.CharField(max_length=64, blank=True, default='')
    # 'keep_app', 'apply_yc', 'cancel_both', 'master_substitution', 'time_reschedule', etc.

    resolution_metadata = models.JSONField(default=dict)
    # Free-form: { 'new_slot': '...', 'new_master_id': ..., 'attribution_adjustment': '...' }

    attribution_adjusted = models.BooleanField(default=False)
    # Per §4.5

    customer_facing_message_sent = models.BooleanField(default=False)
    # Per §6 — was customer messaged about resolution

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            Index(fields=['tenant', 'status', 'severity', '-detected_at']),
            Index(fields=['sla_due_at']),  # SLA breach scanner
            Index(fields=['primary_booking']),
        ]
```

### 10.2 `Booking` model additions

Add fields to existing `booking.Booking`:
- `has_open_conflict: BooleanField(default=False)` — denormalized for fast check
- `last_yc_synced_at: DateTimeField(null=True)`
- `yc_sync_state: CharField(choices=['ok', 'pending', 'failed', 'manual'])`

---

## 11. API contracts

### 11.1 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/conflicts` | List open conflicts (filters: severity, type) |
| GET | `/api/v1/admin/conflicts/<id>` | Detail view |
| POST | `/api/v1/admin/conflicts/<id>/resolve` | Apply resolution |
| POST | `/api/v1/admin/conflicts/<id>/escalate` | Escalate to founder |
| GET | `/api/v1/admin/conflicts/sla-breaches` | Past-SLA list |

### 11.2 POST `/api/v1/admin/conflicts/<id>/resolve`

**Request:**
```json
{
  "action": "keep_app",  // or "apply_yc", "cancel_primary", "cancel_secondary", "master_substitution", "time_reschedule"
  "metadata": {
    "new_master_id": 123,
    "new_slot_start": "2026-05-22T11:00:00Z"
  },
  "send_customer_message": true  // default true
}
```

**Validation:**
- Admin scope check (tenant boundary)
- Action valid for conflict_type
- Required metadata per action

**Response (200):**
```json
{
  "conflict_id": "uuid",
  "status": "resolved_admin",
  "resolved_at": "...",
  "attribution_adjustments": [
    {"booking_id": "...", "from": "ai_direct", "to": "ai_assisted", "score": 0.6}
  ],
  "customer_messages_sent": 1,
  "events_emitted": ["booking.conflict.resolved"]
}
```

### 11.3 Founder endpoints (Phase 3+)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/conflicts/escalated` | Cross-tenant escalations |
| GET | `/api/v1/founder/conflicts/dashboard` | Aggregate metrics |

### 11.4 Internal sync endpoints (no public)

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/yc/webhook` | Receive YC events |
| POST | `/internal/sync/reconcile/<tenant_id>` | Trigger batch reconciliation |

---

## 12. Events emitted

Add to [`event-taxonomy.md §3.2 booking domain`](./event-taxonomy.md#32-booking-domain):

| Trigger | Event | Notes |
|---|---|---|
| Conflict opened | NEW: `booking.conflict.opened` | type, severity, primary/secondary ids |
| Auto-resolved | NEW: `booking.conflict.resolved_auto` | action, duration_ms |
| Admin-resolved | NEW: `booking.conflict.resolved_admin` | action, admin_user_id, duration_ms |
| Founder escalation | NEW: `booking.conflict.escalated_founder` | reason |
| SLA breach | NEW: `booking.conflict.sla_breached` | severity, age_minutes |
| Attribution adjusted | NEW: `booking.attribution.adjusted` | from, to, score_change, reason |
| Customer notified | NEW: `booking.conflict.customer_message_sent` | conflict_type, message_template_id |
| YC sync fail | NEW: `booking.yc_sync.failed` | http_status, retry_count |

8 NEW events §12.

---

## 13. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Show «Sync error 503» to customer | Breaks single-assistant illusion | §2.2 graceful framing |
| Silent overwrite of app booking by YC | No audit | §10 audit row always |
| Cascade message storm | 3 messages 30s apart confuses | §6.7 consolidate |
| Blame admin to customer («админ передвинул») | Internal-only language leaks | §2.3 AI takes ownership |
| Block customer message during conflict | Customer waits silently | §5.4 send «уточняю» message |
| Allow auto-confirm during open conflict | Compounds problem | §5.2 pause |
| Skip reminder on MEDIUM conflict | Customer arrives surprised | §5.3 send softer reminder |
| Mark conflict resolved without §10 audit | Lose replay capability | §9.4 always replayable |
| Auto-merge YC duplicate customers without canonical lock | Data corruption | §3.7 canonical = app, link only |
| Don't adjust attribution after admin intervention | Founder-50 cohort distorted | §4.5 always re-evaluate |
| Send «price changed» to customer | Per §2.9 honor original quote | §6.6c silent |
| Show conflict reason in admin UI as «YC error 403» | Admin can't act | §7 plain-language action CTAs |
| Auto-cancel booking on YC API outage | Trust shattered | §3.3 wait + admin manual fallback |
| Notify customer of every drift | Notification fatigue | §6.6a material-only filter |
| Re-open resolved conflict on flap | Loop | Idempotent open per §8.3 |

---

## 14. Acceptance criteria (engineering checklist)

- [ ] `BookingConflict` model + migration §10.1
- [ ] `Booking` fields added §10.2
- [ ] 8 conflict types detection logic §3 + classification §4
- [ ] Real-time + batch reconciliation §8
- [ ] Resolution engine with auto + admin paths
- [ ] Admin Mini App «Конфликты расписания» tab §7.1
- [ ] Per-type resolution screens §7.2-7.6
- [ ] Founder escalation flow §7.7
- [ ] Customer message templates §6 (8 templates)
- [ ] AI behavior modifiers §5 (has_open_conflict marker checked in all booking-reference paths)
- [ ] Conversation-ownership-policy tier escalation per §4.4
- [ ] Attribution-policy re-evaluation per §4.5 (atomic with conflict resolution)
- [ ] 8 events emitted §12
- [ ] PII rules §9.3 enforced
- [ ] SLA breach scanner §11.1 + alerts
- [ ] Idempotency on open §8.3
- [ ] Tests: each conflict type detection / classification / resolution / customer message / attribution adjustment / SLA breach / cross-tenant denial
- [ ] Replay test: every resolution reproducible from event stream §9.4
- [ ] Anti-pattern review §13

---

## 15. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-BC1** | Confidence threshold for phantom-YC identity match — 0.7 or 0.8? | 0.7 MVP per §8.4 (Levenshtein on name + exact phone); Q-WG3-style escalation if too many false positives | Eng | 🟢 |
| **Q-BC2** | DOUBLE_BOOKING admin override of «customer wins» — allow? | YES per §2.9 — admin can override with explicit acknowledgment in resolution UI («I understand customer was notified»). Audit captured. | Policy | 🟡 |
| **Q-BC3** | PHANTOM_APP retry count — 3 or 5? | 3 with exponential backoff 30s/2m/10m per §3.3. Admin notified after 3rd fail (~15 min). | Eng | 🟢 |
| **Q-BC4** | SLA escalation to founder — when admin doesn't act within SLA × 1 or × 2? | × 2 + critical impact per §7.7. Don't page founder for HIGH conflicts at HIGH × 2 unless customer impact imminent. | Policy | 🟡 |
| **Q-BC5** | Conflict-aware reminders — skip or soften? | §5.3 matrix: skip CRITICAL/HIGH, soften MEDIUM, normal LOW | UX | 🟢 |
| **Q-BC6** | Customer cancellation during open conflict — allowed? | YES (customer's right). Cancellation supersedes conflict resolution — conflict auto-closes as «cancelled by customer». | Policy | 🟢 |
| **Q-BC7** | Multi-tenant admin (CSM tooling Phase 3+) — cross-tenant conflict view? | YES Phase 3+ for founder/CSM only. Strict scoped via separate `founder/conflicts/` namespace per §11.3. | Privacy + Eng | 🟡 |
| **Q-BC8** | YC webhook delivery failure (we miss events) — how detected? | Batch reconciliation §8.2 nightly closes gap. Real-time monitoring of webhook gap > 6h triggers founder alert. | Eng | 🟡 |
| **Q-BC9** | What about non-YC tenants — does this policy apply? | YES per CRM with sync. Spec-by-CRM details deferred to per-CRM integration policy. This policy is the abstract framework. | PM | 🟡 |
| **Q-BC10** | Master substitution §6.6b — auto-pick best alternative or list 2? | List 2 alternatives. Avoid implying judgment («best»). Customer chooses. | UX | 🟢 |
| **Q-BC11** | Time mismatch >2h vs >5min — same flow? | §6.5 unified flow regardless of delta. >2h is rare but same UX. Material vs cosmetic filter not needed for time (any change matters). | UX | 🟢 |
| **Q-BC12** | Booking notes (special instructions) — preserve during conflict resolution? | YES — notes carry through resolution (new slot inherits notes). Audit captures preservation. | Policy + Eng | 🟢 |
| **Q-BC13** | Conflict-rate alerting — what % of bookings = unhealthy? | > 3% conflicts/day per tenant triggers «integration health degraded» founder alert. > 10% = critical (likely YC integration broken). | SRE | 🟡 |
| **Q-BC14** | Attribution adjustment authority — system auto OR admin must approve? | System auto per §4.5 deterministic rules. Admin can challenge in admin UI «assigned ai_assisted, I think it's still ai_direct» → founder reviews per Q12-δ cohort. | Policy | 🟡 |
| **Q-BC15** | Customer message «уточняю — вернусь в течение N минут» — exact N? | 15 min for CRITICAL severity (matches SLA §4.5); 60 min for HIGH. Use admin's actual SLA, not arbitrary. | UX | 🟢 |
| **Q-BC16** | Identity collision §3.7 — auto-link or admin approves merge? | Auto-link app-side canonical. Admin merge in YC is separate § 7.5 (admin-approved). App-side is auto because we have rich AI context. | Privacy + Eng | 🟡 |
| **Q-BC17** | Catalog desync 3.8 — service removed but linked to bookings — keep booking active until customer arrives or cancel? | Admin chooses §7.6. Default suggestion: «связать с другой услугой» (preserve booking with closest service). Cancel only if no equivalent. | Policy | 🟢 |
| **Q-BC18** | Conflict resolution by admin who isn't owner of bookings (Phase 4+ multi-admin) — permissions? | Any admin role can resolve any conflict in their tenant Phase 2. Master role CANNOT (master sees their bookings, not conflicts). Founder role can resolve across tenants. | Permissions | 🟡 |
| **Q-BC19** | Conflict tied to customer in HUMAN_LOCKED tier — does AI even send the «уточняю» message? | NO per HUMAN_LOCKED contract — admin must compose. Admin Mini App resolution UI offers «sample message» they can edit. | Policy + UX | 🔴 before HUMAN_LOCKED conflict |
| **Q-BC20** | Conflict during tenant SUSPENDED state — what happens? | Per tenant-suspension-pause-ux §5: sync paused, existing conflicts marked «paused_tenant_suspended». Resume on tenant ACTIVE. Customer messages also paused. | Policy | 🟢 |

---

## 16. Cross-document linkage

- [`yclients-integration-architecture.md`](./yclients-integration-architecture.md) — sync mechanics this policy extends
- [`booking-policy.md`](./booking-policy.md) — booking lifecycle states
- [`attribution-policy.md`](./attribution-policy.md) §15 — §4.5 adjustments stack
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — §4.4 tier escalation
- [`single-assistant-identity.md`](./single-assistant-identity.md) — §2.3 voice preservation
- [`event-taxonomy.md §3.2`](./event-taxonomy.md#32-booking-domain) — 8 NEW events §12
- [`schedule-management-ux.md`](./schedule-management-ux.md) — admin schedule view stacks with conflict tab
- [`tenant-suspension-pause-ux.md §5`](./tenant-suspension-pause-ux.md) — SUSPENDED state pauses sync
- [`assistant-persona.md`](./assistant-persona.md) — voice for §6 customer messages
- [`master-conversational-templates.md`](./master-conversational-templates.md) — master not directly involved in conflict UX but ownership relevant
- [`../decisions-log.md`](../decisions-log.md) — Q-BC* go here

---

## 17. What this unblocks

- **Production launch with YClients tenants** — conflicts no longer silent
- **Attribution accuracy** — §4.5 ensures founder-50 cohort billing is correct
- **Single-assistant identity preservation** — customer never sees raw error §2.2
- **Admin operational efficiency** — Mini App tab §7 vs. ad-hoc Slack to founder
- **Founder observability** — §9.2 dashboard surfaces integration health
- **Reconciliation audit** — every divergence captured §10
- **Multi-CRM future** — abstract framework Q-BC9 makes adding next CRM additive

## 18. What this does NOT unblock

- ❌ Multi-CRM simultaneous (Phase 4+)
- ❌ ML-predicted conflict prevention (Phase 4+)
- ❌ Master payout / no-show conflicts (separate scope)
- ❌ Customer refund disputes (separate scope)
- ❌ Skip Q-BC19 HUMAN_LOCKED handling — pre-deploy lock
- ❌ Skip Q-BC2 founder review of admin override audit
- ❌ Skip SLA-breach alerting integration (founder Slack / email channel still TBD)

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Booking backend lead | ☐ | |
| YClients integration eng | ☐ | |
| Admin Mini App frontend (§7 screens) | ☐ | |
| AI prompt eng (§5 behavior + §6 templates) | ☐ | |
| Attribution policy review (§4.5 adjustments) | ☐ | 🔴 PRE-DEPLOY |
| Conversation ownership policy review (§4.4 tier escalation) | ☐ | 🔴 PRE-DEPLOY |
| SRE (Q-BC13 conflict-rate alerting + SLA breach) | ☐ | |
| Privacy (Q-BC7 cross-tenant founder view) | ☐ | |
| Founder (Q-BC14 attribution authority + §7.7 escalation) | ☐ | |

## Last verified
2026-05-19 (initial draft, 8 canonical conflict types locked, customer-facing voice preserves single-assistant identity, admin Mini App resolution UI specified, attribution + tier policies extended)
