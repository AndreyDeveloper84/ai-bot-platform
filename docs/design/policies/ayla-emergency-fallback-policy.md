# Ayla — Emergency Fallback Policy

**Date:** 2026-05-19 r1
**Status:** STRATEGIC FOUNDATION — Doc #3 of 5 in Ayla-first foundation set. Rewrites [`conversation-ownership-policy.md`](./conversation-ownership-policy.md). Per Ayla-first pivot: zero human handoff in customer UX; system fallback for genuine emergencies only.
**Reads:** [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md), [`ayla-memory-and-personalization.md`](./ayla-memory-and-personalization.md), memory `project_ayla_first_strategic_pivot`, memory `project_conversation_ownership_tiers` (revised), [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md), [`booking-conflict-resolution-ux.md`](./booking-conflict-resolution-ux.md), memory `project_single_assistant_identity` (deprecated; for trace only), Notion: PRD Ayla v2.0 + AI-01/AI-02/BOOK-02 user flows

> Ayla is **always** the conversational partner. Period. Admin/master/founder never "takes over" the chat. When something serious happens, Ayla collects facts, hands off to backend team via separate channels, and returns to customer with the answer. This policy specifies what counts as "serious", how admin work happens invisibly, and what Ayla says while customer waits.

---

## 0. Why this exists

### 0.1 The pivot from 3-tier ownership

Per memory `project_conversation_ownership_tiers` (r2 revised 2026-05-19):

Old model (deprecated): customer-facing 3-tier ownership (`AI_CONTINUITY` / `HUMAN_SUPERVISED` / `HUMAN_LOCKED`) with explicit handoff moments. Customer might see «вам отвечает администратор Анна» for high-risk topics.

New model per Ayla-first pivot decision #4 (locked 2026-05-19):

> Решение для MVP: zero human handoff как основной UX. Пользователь взаимодействует с Ayla, а не ждёт администратора. Но технически нужно оставить аварийную эскалацию: спор по оплате / конфликт по записи / ошибка интеграции / юридически чувствительный вопрос. Это не «живой администратор в UX», а страховочный системный контур.

This doc operationalizes the «страховочный системный контур».

### 0.2 What's deleted from old model

- ❌ 3-tier customer-visible ownership labels
- ❌ "Auto-resume after admin reply" mechanics (admin doesn't reply in customer thread)
- ❌ "Bot мode → admin takes over → bot mode" framing
- ❌ Explicit human identity reveals («вам отвечает администратор Анна»)
- ❌ HUMAN_LOCKED state surfaced to customer
- ❌ Admin composing draft replies as customer's Ayla

### 0.3 What stays

- ✅ Audit log model — every customer-facing message tracked
- ✅ Permissions matrix (admin/master/owner roles) — internal Ayla Pro UI
- ✅ SLA discipline — measured per emergency tier
- ✅ Retention policy
- ✅ Founder escalation path
- ✅ Compliance review capability

### 0.4 The promise

Single source for:
- 4 emergency fallback tiers §3 (payment_dispute / booking_conflict / integration_error / legally_sensitive)
- How customer experiences each tier §4 (Ayla voice templates)
- How admin works on each tier §5 (Ayla Pro admin surfaces)
- Founder escalation §6
- SLA matrix §7
- Audit + retention §8
- Cross-tenant emergencies §9
- Per-tenant emergency configuration §10
- Integration with refund-dispute / booking-conflict / no-show / leave docs §11
- Migration from old 3-tier model §12
- Anti-patterns §13
- 4 NEW models, 18 endpoints, 14 events

---

## 1. Scope

### IN
- 4 emergency fallback tiers with detection, classification, resolution
- Ayla customer-facing voice during emergency (calm, informational, no panic)
- Admin Ayla Pro surface (separate from customer Ayla — admin works in admin UI)
- Founder escalation per tier
- Per-tenant SLA configuration
- Audit logging (extends MemoryAccessLog pattern)
- Auto-detection triggers (e.g., sensitive keywords from refund-dispute §3.6 fire `legally_sensitive`)
- Migration mapping from old 3-tier reasons to new emergency tiers
- Customer notification respecting `customer-notification-controls-ux` quiet hours
- Emergency state on `Conversation` model — customer-facing label invisible, backend-visible

### OUT
- Routine bookings / wellness chats / preferences — Ayla handles, no emergency
- Master-admin internal communication — separate `master-admin-internal-chat-handoff`
- Tenant-side onboarding / billing dispute (salon vs platform) — separate scope
- Anti-fraud detection on customers — Phase 4+
- Government data request handling — separate legal process
- Mass incident management (whole platform outage) — separate `incident-response-policy.md` future
- Ayla refusing to talk to customer entirely — out of scope (Ayla always responds, even in emergency)
- Customer's right to demand specific human by name — Ayla doesn't name humans
- Cross-tenant emergency aggregation — privacy boundary
- Customer self-elevating «I want human now» — Ayla acknowledges + uses emergency tier if warranted; doesn't auto-escalate just on request
- Real-time human in the loop (chat with admin live) — Phase 4+ if value proven

---

## 2. Strategic constraints — non-negotiable

### 2.1 Ayla always speaks
Per [`ayla-identity-and-brand.md §5.3`](./ayla-identity-and-brand.md): customer's chat thread = Ayla messages. No exception. During emergency, Ayla collects facts + acknowledges + sets expectations + relays resolution. Admin works elsewhere.

### 2.2 No "admin takes over" framing
- ❌ «Передаю администратора Анну»
- ❌ «Вам отвечает админ»
- ❌ «Меня заменит человек»
- ✅ «Передаю команде на проверку, вернусь в течение N»
- ✅ «{{salon_owner_first_name}} разбирается с твоим запросом, я напишу как только»

### 2.3 4 emergency tiers fixed
Per memory `project_conversation_ownership_tiers` r2 + this doc §3. No 5th tier. New triggers map to existing 4. Q-AEF1.

### 2.4 SLA per tier strict
- `booking_conflict` (customer-imminent): 15 min resolution OR Ayla follows up with «извини, ещё работаем»
- `payment_dispute`: 48h admin → 7d founder
- `integration_error`: 60 min OR Ayla follows up
- `legally_sensitive`: 24h founder

### 2.5 Customer informed at right moment
Per [`customer-notification-controls-ux §11`](./customer-notification-controls-ux.md): emergency notifications bypass snooze + quiet hours. But voice stays calm — no «URGENT!!!». Per [`ayla-identity-and-brand §11.3`](./ayla-identity-and-brand.md).

### 2.6 Founder escalation has clear triggers
Not arbitrary admin decision. Each tier has specific founder-trigger criteria §6.

### 2.7 No customer fees during emergency resolution
Per [`customer-refund-dispute-ux §2.9`](./customer-refund-dispute-ux.md): customer never pays for dispute / emergency process. Same here.

### 2.8 Audit immutable
Every emergency action audit-logged. Customer's view, admin's actions, founder's decisions — all captured. 7-year retention for `legally_sensitive` tier.

### 2.9 Privacy preserved
Customer's data accessed during emergency follows same privacy rules as outside emergency. Red-zone memory still red-zone. Master can't see customer wellness data even when investigating dispute.

### 2.10 No emergency abuse
- Customer triggering false emergency repeatedly → admin flag (similar to no-show pattern); affects Q-AEF13 anti-fraud
- Admin marking false emergency → audit captures; founder review per Q-AEF14

### 2.11 Per-tenant SUSPENDED state
Per [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md): if tenant SUSPENDED mid-emergency, ongoing emergencies handed to founder; new emergencies at SUSPENDED tenant route directly to founder (no admin available).

### 2.12 Emergency labels NEVER customer-facing
Customer doesn't see tier name. Per §2.1 Ayla just speaks naturally about what's happening («передаю команде»).

### 2.13 Ayla doesn't refuse engagement
Even in emergency, Ayla replies to customer messages. May be brief («ещё разбираемся»), but never silent. No «conversation locked» UX.

---

## 3. 4 emergency fallback tiers

### 3.1 Tier 1 — `payment_dispute`

**Trigger:**
- Customer opens refund dispute via [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md) (any of 6 types)
- Customer challenges charge amount or refund delay
- Customer claims unauthorized charge

**Detection:**
- Refund dispute API explicit call
- NLU detection on customer message: «верните деньги», «деньги не пришли», «списали неправильно»
- Customer Mini App «Что-то не так?» → refund flow

**Classification:**
- Severity per `customer-refund-dispute-ux §3` (LOW / MEDIUM / HIGH / CRITICAL)
- 4-eye admin required for amounts > 5000₽ OR type ∈ {NO_SHOW_MASTER, DAMAGE}

**Customer-facing voice template:**

```
{{customer_first_name}}, записала твой вопрос — передаю команде на
проверку. {{salon_owner_first_name}} обычно отвечает в течение 48 часов.
Напишу как только узнаю.
```

If founder-escalated:

```
{{customer_first_name}}, вопрос проверяется на более высоком уровне —
это стандартная процедура для серьёзных случаев. Команда вернётся в
течение 7 дней.
```

**Backend work:**
- Admin reviews via Ayla Pro «Жалобы клиентов» tab (per refund-dispute §5)
- Admin can request more info via internal admin chat with master
- Admin's offer / counter-offer → Ayla relays in customer-friendly framing

**Resolution voice:**

```
{{customer_first_name}}, по твоему вопросу — {{outcome summary}}.
Подходит?
[Принять]   [Не согласна, обсудить дальше]
```

### 3.2 Tier 2 — `booking_conflict`

**Trigger:**
- YClients sync conflict per [`booking-conflict-resolution-ux.md`](./booking-conflict-resolution-ux.md) — 8 canonical types
- Master leave / substitution making booking impossible
- Double-booking detected
- Customer no-show vs master-claims-customer-arrived dispute (per `customer-no-show-policy-ux §12`)

**Detection:**
- Conflict resolution engine fires
- Real-time YC webhook divergence detection
- Master flag «client never came» when customer disputes
- Manual admin flag on suspicious booking

**Classification:**
- Severity per booking-conflict-resolution §4 (CRITICAL / HIGH / MEDIUM / LOW)
- CRITICAL if customer-imminent (<24h to slot)

**Customer-facing voice template:**

For master substitution (per `booking-conflict-resolution §6.6b` style — Ayla voice):

```
{{customer_first_name}}, в этот день у {{master_first_name}} планы
поменялись — {{date}} {{time}} она не сможет.

Могу предложить ту же процедуру у:
• Лена ⭐ 4.8 ({{exp_years}} лет опыта)
• Марина ⭐ 4.7

Или подобрать другую дату у {{master_first_name}} — есть свободно:
{{alt_dates}}.

[Лена]   [Марина]   [Перенести]   [Отменить]
```

For double-booking apology:

```
{{customer_first_name}}, хочу сразу написать — это была моя оплошность
с расписанием. Это время уже занято другим клиентом.

Могу предложить:
• {{slot_1}} — у {{master_1}}
• {{slot_2}} — у {{master_2}}
• {{slot_3}} — у {{master_3}}

Подойдёт?
```

For sync conflict resolution waiting:

```
{{customer_first_name}}, уточняю детали с салоном — вернусь в течение
15 минут.
```

**Backend work:**
- Admin reviews via Ayla Pro «Конфликты расписания» tab (per booking-conflict §7.1)
- Per-conflict resolution screens
- Auto vs admin-confirmed paths per §4.3 booking-conflict matrix

**Resolution voice:**

```
{{customer_first_name}}, всё уладили — {{outcome}}. Подтверждаешь?
```

### 3.3 Tier 3 — `integration_error`

**Trigger:**
- YClients sync API persistent failure (>3 retries)
- Payment processor down (YooKassa, etc.)
- MAX platform issue affecting Mini App
- Internal service outage affecting customer's view
- Database / cache layer error during customer action

**Detection:**
- Sync subscriber: 3-retry exponential backoff exhausted
- SRE alerting fires
- Customer action returns 5xx repeatedly
- Stale data detected (e.g., Mini App showing old prices)

**Classification:**
- Severity by customer-impact:
  - **CRITICAL**: customer's confirmed booking missing / payment ambiguous
  - **HIGH**: customer can't complete action (booking, redemption, etc.)
  - **MEDIUM**: customer sees outdated info
  - **LOW**: informational sync (no functional break)

**Customer-facing voice template:**

```
{{customer_first_name}}, что-то с интеграцией, разбираемся. Это бывает
редко — обычно решается в течение часа. Напишу как только всё в
порядке.
```

For Mini App degraded:

```
{{customer_first_name}}, приложение немного не в форме сейчас — кое-что
может отображаться странно. Если что-то срочно нужно сделать — напиши
мне, разберёмся вместе.
```

**Backend work:**
- SRE alerted via Ops channel
- Founder informed if tenant SUSPENDED triggered
- Admin notified via Ayla Pro alert banner
- Customer's pending actions held / queued for replay when service restored

**Resolution voice:**

```
{{customer_first_name}}, всё в порядке — починили. {{If applicable:
твоя запись подтвердилась, ждём тебя в субботу 15:00}}.
```

### 3.4 Tier 4 — `legally_sensitive`

**Trigger:**
- Customer reports medical injury / allergic reaction (per [`customer-refund-dispute-ux §3.6 DAMAGE`](./customer-refund-dispute-ux.md))
- Customer reports sexual misconduct / harassment / theft (per `master-reviews-feedback §6.5` sensitive keywords)
- Customer reports racism / discrimination
- Force majeure dispute requiring legal review
- Customer < 18 medical-adjacent issue
- Government / lawyer request for customer data

**Detection:**
- Sensitive keyword detection on customer message (per `customer-refund-dispute-ux §2.13` shared list with master-reviews §6.5)
- Customer explicitly states injury / misconduct
- Admin manually flags «serious case» via Ayla Pro
- Legal hold notification from compliance system

**Classification:**
- All `legally_sensitive` is CRITICAL by default
- Sub-tier:
  - **medical_injury** — alleged physical harm from service
  - **misconduct_allegation** — sexual/racism/theft/harassment
  - **minor_involved** — customer < 18 on medical-adjacent
  - **legal_hold** — external legal process

**Customer-facing voice template:**

```
{{customer_first_name}}, это серьёзный случай. Передаю напрямую
основателю студии для разбора. Ответ в течение 24 часов.

Если что-то срочное по здоровью — обратись к врачу не откладывая.
{{If medical scenario: link to 103 / poison control / etc.}}
```

For misconduct allegation:

```
{{customer_first_name}}, поняла. Спасибо что рассказала — это важно.
Передаю основателю. Команда отнесётся серьёзно. Вернусь в течение 24
часов.
```

For minor / parent contact:

```
{{customer_first_name}}, мне нужно обсудить это с твоим взрослым
сопровождающим. Передаю команде, они свяжутся в течение 24 часов.
```

**Backend work:**
- Founder auto-notified (cannot opt out — per `customer-refund-dispute §10.5` + `master-reviews-feedback §6.5`)
- Founder TIER-2 protocol activated
- Admin may be involved but founder leads
- External legal / medical referral if appropriate
- All data access logged separately to `LegalHoldAccessLog`

**Resolution voice:**

Per founder's decision per refund-dispute §10.3:

```
{{customer_first_name}}, по твоей ситуации команда приняла решение:
{{outcome plain language}}. {{If applicable: дальнейшие действия}}.
```

If escalation continues (legal process):

```
{{customer_first_name}}, по твоему вопросу процесс продолжается. Свяжусь
в течение {{next_check_in_period}}.
```

---

## 4. How customer experiences each tier

### 4.1 Common experience across all tiers

| Aspect | What customer sees | What customer does NOT see |
|---|---|---|
| Sender of message | Ayla (proper noun) | Admin name, role, employee ID |
| Tier label | None | «payment_dispute», «legally_sensitive» |
| Internal admin work | None | Ayla Pro screens, founder review UI |
| SLA countdown | Soft framing («в течение 48 часов») | Hard countdown «32:14 remaining» |
| Other affected customers | None | Cohort patterns, founder dashboards |
| Tier change | Soft framing if applicable («команда подключила более старшего») | Tier transition events |

### 4.2 Customer can ask «что происходит?»

Ayla replies with status update appropriate to tier:

```
{{customer_first_name}}, команда ещё разбирается — обычно в таких
случаях нужно {{expected_time}}. Как только узнаю — напишу.
```

NEVER reveals:
- Internal admin name
- Specific backend actions
- Tier classification
- Other affected customers

### 4.3 Customer can withdraw / cancel emergency

For tiers 1, 2, 3 — customer can withdraw at any time (e.g., refund dispute withdrawn per `customer-refund-dispute §4.5`).

For tier 4 (`legally_sensitive`):
- Customer can request to withdraw, but founder reviews — some categories (alleged sexual misconduct, child involvement) may require investigation regardless
- Audit captures customer's withdrawal request + founder's decision
- Customer informed: «Поняла, команда учтёт. Сообщу о решении»

### 4.4 Customer never blamed during emergency

Per [`customer-refund-dispute §2.1`](./customer-refund-dispute-ux.md) + general Ayla voice:
- No «вы неправы» / «вы ошибаетесь»
- No «это ваша вина»
- Even if customer's claim is false, Ayla informs neutrally («команда не нашла подтверждений, решили {{outcome}}»)

### 4.5 Customer follow-up if past SLA

If admin/founder doesn't resolve in time, Ayla proactively messages:

```
{{customer_first_name}}, извини за задержку — твой вопрос ещё в работе.
Команда вернётся в течение {{updated_eta}}. Если что-то срочно — напиши.
```

Sent without snooze (per `customer-notification-controls-ux §11` emergency override).

### 4.6 Customer sees outcome in conversational form

When resolution available:

```
{{customer_first_name}}, по твоему вопросу: {{salon_owner_first_name}}
извинилась за подачу — возврат 1500₽ обработан, ждём 3-5 дней до карты.
Бесплатная корректировка предложена — хочешь?

[Согласна на возврат]   [Корректировка вместо]   [Передумала]
```

Action buttons let customer accept / reject in conversational flow.

---

## 5. How admin works on each tier — Ayla Pro surfaces

### 5.1 Ayla Pro admin queue

Single «Что требует внимания» dashboard with sections per tier:

```
┌────────────────────────────────────────┐
│ 🔧 Что требует внимания (5)              │
├────────────────────────────────────────┤
│ ⚠ HIGH PRIORITY                          │
│ 🩹 Жалоба от Мария И. — травма после     │
│    маникюра (LEGALLY_SENSITIVE)          │
│    SLA: 14 ч из 24                       │
│    Founder уведомлён                     │
│    [Открыть]                              │
│                                        │
│ 🟡 NORMAL                                 │
│ 💰 Спор по доходу — Лена → Анна          │
│    PAYMENT_DISPUTE                        │
│    SLA: 32 ч из 48                       │
│    [Разобрать]                            │
│                                        │
│ 📅 Конфликт расписания — Олег П.         │
│    BOOKING_CONFLICT (мастер не работает) │
│    SLA: 8 мин из 15                       │
│    [Разрешить]                            │
│                                        │
│ ⚙ Sync error YClients                     │
│    INTEGRATION_ERROR (15 записей затронуто)│
│    SRE работает                           │
│    [Подробнее]                            │
│                                        │
│ ── Ожидание клиента ──                   │
│ 📋 Жалоба от Олег П. — ждём ответа       │
│    на нашу оффер 800 ₽                   │
│    [Открыть]                              │
└────────────────────────────────────────┘
```

### 5.2 Admin works in admin UI, not customer chat

Admin opens a case → admin UI shows:
- Customer's claim text (full)
- Booking / dispute context
- Admin's response options (per tier-specific UI)
- Audit log of actions on this case

Admin's action triggers Ayla to message customer. Admin doesn't type message to customer directly. Per Q-AEF3:

**Why:** consistency + voice quality + no «admin Anna's typo became Ayla's voice». Admin chooses outcome via structured UI; Ayla composes customer message from template + outcome data.

### 5.3 Admin can suggest custom wording

For unusual situations, admin can suggest message text → Ayla reviews vs voice rules → either uses or proposes alternative. Audit captures both versions. Per Q-AEF4 — Phase 2+ feature; MVP uses templates only.

### 5.4 Per-tier admin surfaces

| Tier | Admin surface |
|---|---|
| `payment_dispute` | [`customer-refund-dispute-ux §5`](./customer-refund-dispute-ux.md) admin queue + 4-eye flow |
| `booking_conflict` | [`booking-conflict-resolution-ux §7`](./booking-conflict-resolution-ux.md) conflict resolution screens |
| `integration_error` | SRE alert dashboard + admin info banner; admin not always primary actor |
| `legally_sensitive` | TIER-2 protocol (per `master-reviews-feedback §6.5`) — founder leads, admin assists |

### 5.5 Admin's permission per tier

| Tier | Admin can resolve alone? | 4-eye required? | Founder required? |
|---|---|---|---|
| `payment_dispute` LOW/MEDIUM | YES | NO (unless > 5000₽) | NO |
| `payment_dispute` HIGH/CRITICAL | NO | YES | If escalated |
| `booking_conflict` LOW/MEDIUM | YES | NO | NO |
| `booking_conflict` CRITICAL | YES (in critical urgency) | NO | If unresolvable |
| `integration_error` | NO (SRE-led) | NO | If tenant-wide |
| `legally_sensitive` | NO | YES | YES (always) |

### 5.6 Master involvement

Per [`master-admin-internal-chat-handoff.md`](../handoffs/2026-05-19-master-admin-internal-chat-handoff.md): master may be looped in via internal admin chat (NOT Ayla chat). Master's input feeds admin's decision; doesn't surface to customer.

### 5.7 Admin's voice in customer-facing copy

Admin can configure tenant-level template variables:
- `{{salon_owner_first_name}}` — used in «передаю {{salon_owner_first_name}}» framing
- Salon name display
- Operational SLA promises («обычно в течение 48 часов»)

NOT customizable:
- Ayla's persona voice
- Emergency framing language
- Sensitive-topic handling templates

---

## 6. Founder escalation

### 6.1 When founder auto-triggers

Automatic founder involvement:
- **All `legally_sensitive`** tier cases — founder cannot opt out
- **`payment_dispute` HIGH/CRITICAL** if admin doesn't resolve within 48h
- **`booking_conflict` CRITICAL** if customer arriving in <2h with unresolved conflict (per booking-conflict §7.7)
- **`integration_error` tenant-wide** affecting multiple customers
- Customer explicitly requests founder («хочу с основателем»)

### 6.2 Founder review surface

Phase 3+ founder dashboard tab «Эскалации» (cross-tenant):

```
┌────────────────────────────────────────┐
│ 🚨 Эскалации к founder (3 active)        │
├────────────────────────────────────────┤
│ Студия Натали — медицинский инцидент    │
│ Customer: Мария И.                       │
│ Type: legally_sensitive / medical_injury │
│ Opened: 2 hours ago                      │
│ SLA: 22 hours remaining                  │
│ [Открыть]                                 │
│                                        │
│ Lounge Salon — спор по выплате           │
│ Master: Лена П.                          │
│ Admin escalated after 48h                │
│ SLA: 5 days remaining                    │
│ [Открыть]                                 │
│                                        │
│ ...                                      │
└────────────────────────────────────────┘
```

### 6.3 Founder decision authority

Founder has final say on:
- Customer refund amounts (override admin's offer)
- Master earnings adjustments (force-adjust per [`master-earnings-handoff Q-ME9`](../handoffs/2026-05-19-master-earnings-handoff.md))
- Tenant suspension if pattern emerges
- Legal hold activation
- Cross-tenant data review

### 6.4 Founder cannot bypass audit

Every founder action audit-logged. Per `policy_deviation_pattern` memory: when founder force-overrides policy, captured with reason.

### 6.5 Founder may delegate

For high-volume scenarios, founder can pre-authorize CSM (per `quality-reviewer-dashboard-ux.md`) to handle certain tiers. Audit captures pre-authorization + each decision.

### 6.6 Customer not told founder involved

Per §2.2 — Ayla doesn't say «founder лично разбирается». Just «команда на более старшем уровне» or «команда studii». Founder's name not surfaced unless customer asks specifically AND founder authorizes.

### 6.7 Founder reviews lessons-learned

Per `quality-reviewer-dashboard-ux §4` cohort review pattern: aggregate emergency tier patterns surface in founder analytics:
- Tier distribution per tenant
- Resolution time per tenant
- Customer satisfaction post-resolution (Phase 3+)
- Recurring patterns flagged

---

## 7. SLA matrix

### 7.1 Per-tier defaults

| Tier | First admin response | Resolution target | Founder escalation | Customer follow-up cadence |
|---|---|---|---|---|
| `payment_dispute` | 8h | 48h | At 48h × 2 if no admin | Day 2, day 5 |
| `booking_conflict` CRITICAL | 5 min | 15 min | At 30 min | Every 15 min via Ayla |
| `booking_conflict` HIGH/MED | 30 min | 4h | At 8h | Every 1h |
| `integration_error` CRITICAL | 5 min | 60 min | At 30 min | Every 15 min |
| `integration_error` HIGH/MED | 30 min | 4h | At 4h tenant-wide | Per status |
| `legally_sensitive` | 2h | 24h | Immediate auto | Every 4h |
| `legally_sensitive` + minor | 30 min | 4h | Immediate auto | Every 2h |

### 7.2 Per-tenant override

Per [`customer-no-show-policy-ux §10`](./customer-no-show-policy-ux.md) precedent: tenant can shorten SLA via Ayla Pro settings. CAN'T lengthen beyond default. Audit captures tenant config.

### 7.3 SLA breach actions

If SLA breached:
1. Customer gets follow-up §4.5
2. Internal Slack/email alert to admin
3. Past SLA × 2 → founder auto-escalation
4. Past founder SLA → founder reminder + CSM notification

### 7.4 SLA frozen during customer-pending

When admin asks customer a question, SLA pauses until customer responds. If customer no-response > 7 days, dispute marked `customer_no_response_expired` (per [`customer-refund-dispute Q-CR13`](./customer-refund-dispute-ux.md)).

### 7.5 SLA paused during tenant SUSPENDED

Per §2.11 — emergencies routed to founder; SLA clock paused until tenant active again OR founder reroutes.

---

## 8. Audit + retention

### 8.1 `EmergencyEvent` model

```python
class EmergencyEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', null=True, blank=True, on_delete=SET_NULL, related_name='+')
    # null for cross-tenant emergencies handled by founder

    customer = models.ForeignKey('identity.BotUser', null=True, blank=True, on_delete=SET_NULL, related_name='+')
    conversation = models.ForeignKey('conversations.Conversation', null=True, blank=True, on_delete=SET_NULL, related_name='emergency_events')

    TIER_CHOICES = [
        ('payment_dispute', 'Payment dispute'),
        ('booking_conflict', 'Booking conflict'),
        ('integration_error', 'Integration error'),
        ('legally_sensitive', 'Legally sensitive'),
    ]
    tier = models.CharField(max_length=32, choices=TIER_CHOICES)

    SUBTIER_CHOICES = [
        # legally_sensitive sub-tiers
        ('medical_injury', 'Medical injury alleged'),
        ('misconduct_allegation', 'Misconduct allegation'),
        ('minor_involved', 'Customer < 18 medical-adjacent'),
        ('legal_hold', 'Legal hold'),
        # generic
        ('other', 'Other'),
    ]
    subtier = models.CharField(max_length=32, choices=SUBTIER_CHOICES, blank=True, default='')

    SEVERITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)

    DETECTION_CHOICES = [
        ('api_explicit', 'API explicit (refund dispute opened, conflict engine fired)'),
        ('nlu_detection', 'NLU keyword detection'),
        ('admin_manual', 'Admin manually flagged'),
        ('sensitive_keyword', 'Sensitive keyword auto-flagged'),
        ('sre_alert', 'SRE alert fired'),
        ('compliance_system', 'External compliance trigger'),
    ]
    detection_source = models.CharField(max_length=32, choices=DETECTION_CHOICES)

    STATUS_CHOICES = [
        ('opened', 'Opened'),
        ('admin_reviewing', 'Admin reviewing'),
        ('founder_review', 'Founder reviewing'),
        ('customer_pending', 'Awaiting customer response'),
        ('resolved', 'Resolved'),
        ('expired', 'Expired (customer no response > 7d)'),
        ('withdrawn', 'Withdrawn by customer'),
        ('legal_hold', 'On legal hold'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='opened')

    sla_due_at = models.DateTimeField()
    sla_paused = models.BooleanField(default=False)
    sla_pause_reason = models.CharField(max_length=64, blank=True, default='')

    customer_facing_message_sent_count = models.IntegerField(default=0)
    customer_responded_at = models.DateTimeField(null=True, blank=True)

    admin_first_response_at = models.DateTimeField(null=True, blank=True)
    admin_responsible = models.ForeignKey('auth.User', null=True, blank=True, on_delete=SET_NULL, related_name='+')

    founder_engaged_at = models.DateTimeField(null=True, blank=True)
    founder_user = models.ForeignKey('auth.User', null=True, blank=True, on_delete=SET_NULL, related_name='+')

    resolution_summary = models.TextField(blank=True, default='', max_length=2000)
    resolution_outcome_code = models.CharField(max_length=64, blank=True, default='')
    # 'refund_full', 'refund_partial', 'free_correction', 'service_credit',
    # 'denied', 'alt_master_booked', 'rebook_scheduled', etc.

    opened_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    # References to related dispute records
    linked_refund_dispute = models.ForeignKey('disputes.RefundDispute', null=True, blank=True, on_delete=SET_NULL, related_name='+')
    linked_booking_conflict = models.ForeignKey('booking.BookingConflict', null=True, blank=True, on_delete=SET_NULL, related_name='+')

    class Meta:
        indexes = [
            Index(fields=['tenant', 'status', '-opened_at']),
            Index(fields=['tier', 'status']),
            Index(fields=['sla_due_at']),
            Index(fields=['founder_engaged_at']),
        ]
```

### 8.2 `EmergencyAuditLog`

Append-only log of every action.

```python
class EmergencyAuditLog(models.Model):
    emergency = models.ForeignKey(EmergencyEvent, on_delete=CASCADE, related_name='audit_logs')

    ACTION_CHOICES = [
        ('emergency_opened', 'Emergency opened'),
        ('customer_message_sent', 'Ayla sent customer message'),
        ('admin_viewed_case', 'Admin opened case'),
        ('admin_decided', 'Admin made decision'),
        ('4_eye_requested', '4-eye admin requested'),
        ('4_eye_approved', '4-eye admin approved'),
        ('founder_engaged', 'Founder engaged'),
        ('founder_decided', 'Founder decided'),
        ('sla_warning', 'SLA approaching breach'),
        ('sla_breached', 'SLA breached'),
        ('escalated_founder', 'Escalated to founder'),
        ('customer_responded', 'Customer responded'),
        ('customer_withdrew', 'Customer withdrew'),
        ('resolved', 'Resolved'),
        ('legal_hold_applied', 'Legal hold applied'),
        ('legal_hold_lifted', 'Legal hold lifted'),
    ]
    action = models.CharField(max_length=64, choices=ACTION_CHOICES)
    actor = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    actor_role = models.CharField(max_length=32, blank=True, default='')
    # 'system', 'admin', 'founder', 'customer', 'ayla', 'sre'

    metadata = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [Index(fields=['emergency', '-at'])]
```

### 8.3 `LegalHoldAccessLog`

Separate log for tier 4 / legal hold data access.

```python
class LegalHoldAccessLog(models.Model):
    emergency = models.ForeignKey(EmergencyEvent, on_delete=CASCADE, related_name='legal_hold_logs')

    PURPOSE_CHOICES = [
        ('founder_review', 'Founder reviewing case'),
        ('legal_subpoena', 'Legal subpoena response'),
        ('regulatory_audit', 'Regulatory audit'),
        ('compliance_review', 'Internal compliance review'),
    ]
    purpose = models.CharField(max_length=32, choices=PURPOSE_CHOICES)
    accessed_by = models.ForeignKey('auth.User', on_delete=SET_NULL, null=True, related_name='+')
    accessed_at = models.DateTimeField(auto_now_add=True)
    data_categories_accessed = models.JSONField(default=list)
    # ['customer_memory', 'booking_history', 'master_earnings', 'red_zone_memory']
```

### 8.4 `EmergencyResolutionTemplate`

Customer-facing message templates per tier.

```python
class EmergencyResolutionTemplate(models.Model):
    tier = models.CharField(max_length=32)
    outcome_code = models.CharField(max_length=64)
    # Matches EmergencyEvent.resolution_outcome_code
    locale = models.CharField(max_length=8, default='ru')
    template_text = models.TextField()
    # E.g., "Зачёт {amount} ₽ обработан, ждём 3-5 дней до карты."
    requires_variables = models.JSONField(default=list)
    # ['amount', 'salon_owner_first_name']
```

### 8.5 Retention per tier

- `payment_dispute`: 7 years (financial)
- `booking_conflict`: 3 years (booking record retention per consumer-protection)
- `integration_error`: 1 year (SRE post-mortem value declines)
- `legally_sensitive`: 7 years minimum; may be indefinite per legal hold

### 8.6 Data anonymization on customer account close

Per [`customer-privacy-data-closure-ux §9.1`](./customer-privacy-data-closure-ux.md): EmergencyEvent.customer_id → null after 30 days post hard-delete; data categories anonymized. EmergencyAuditLog audit IDs preserved.

---

## 9. Cross-tenant emergencies

### 9.1 Customer at multiple tenants

Customer with emergency at tenant A: only tenant A admin involved. Other tenants don't see this emergency.

### 9.2 Pattern emerging across tenants

If founder analytics show same pattern at multiple tenants (e.g., similar misconduct allegations against different masters across tenants):
- Founder dashboard surfaces aggregate
- No automated cross-tenant action
- Founder decides whether to investigate broader

### 9.3 Customer-level pattern

If same customer opens many emergencies (especially `payment_dispute`):
- Per-tenant soft signal (similar to no-show pattern admin signal per [`customer-no-show-policy-ux §8`](./customer-no-show-policy-ux.md))
- NOT cross-tenant aggregation
- Anti-fraud Phase 4+

### 9.4 Founder cross-tenant view

Founder can see emergencies across tenants (Phase 3+) but cannot share individual emergency details cross-tenant with other admins.

### 9.5 SUSPENDED tenant during emergency

Per §2.11: existing emergency stays with founder; new emergencies route directly to founder; SLA paused until tenant active.

---

## 10. Per-tenant emergency configuration

### 10.1 Tenant Ayla Pro settings

```
┌────────────────────────────────────────┐
│ ← Настройки реагирования                  │
├────────────────────────────────────────┤
│ ── SLA по типам ──                       │
│ Спор по оплате: [48] часов              │
│   (минимум: 48ч, дефолт)                 │
│ Конфликт расписания: [15] минут (CRIT)  │
│ Конфликт расписания: [60] мин (HIGH)    │
│                                        │
│ ── Кто реагирует ──                      │
│ ☑ Натали (founder/owner)                 │
│ ☑ Алина (старший админ)                  │
│ ☐ Стажёр Юра (только младшие случаи)    │
│                                        │
│ ── Эскалация ──                          │
│ Founder уведомляется при:                │
│ ☑ Травмы / медицинские инциденты        │
│ ☑ Иски / юридические запросы            │
│ ☑ Сексуальное домогательство            │
│ ☑ Расовая дискриминация                  │
│ ☑ Жалобы на 4-eye (admin disagrees)     │
│                                        │
│ ── Шаблоны ответов ──                    │
│ Использовать дефолтные:                  │
│ ⦿ Да (рекомендуется)                    │
│ ◯ Кастом для нашей студии                │
│                                        │
│ [Сохранить]                              │
└────────────────────────────────────────┘
```

### 10.2 Cannot configure

- Customer-facing voice (Ayla persona locked)
- Customer experience («admin Anna takes over» can't be enabled)
- Founder cross-tenant visibility
- Audit retention periods
- Sensitive keyword list (shared platform-level per `master-reviews-feedback §6.5`)

### 10.3 Tenant can configure

- SLA values within bounds
- Which admins receive what tiers
- Custom resolution templates (subject to AI quality review)
- Notification preferences for admin-side alerts

---

## 11. Integration with related policies

### 11.1 `customer-refund-dispute-ux.md` integration

- All refund disputes fire `payment_dispute` emergency tier
- Severity per refund-dispute §3 matrix
- Customer-facing voice per refund-dispute §4 + this doc §3.1 (templates aligned)
- 4-eye admin per refund-dispute §5.3
- Founder per refund-dispute §10

### 11.2 `booking-conflict-resolution-ux.md` integration

- All 8 conflict types fire `booking_conflict` emergency tier
- Severity per booking-conflict §4
- Customer-facing voice per booking-conflict §6 + this doc §3.2 (templates aligned)
- Admin resolution per booking-conflict §7

### 11.3 `customer-no-show-policy-ux.md` integration

- Customer disputing no-show classification (per no-show §12) → routes to `payment_dispute` flow (refund-dispute machinery)
- Master flagging no-show that customer disputes → `booking_conflict` tier

### 11.4 `master-time-off-handoff.md` + substitution integration

- Master sick day → if customer-imminent disruption, fires `booking_conflict` for affected bookings
- Customer rebook flow per master-time-off §7 + this doc §3.2

### 11.5 `master-reviews-feedback-handoff.md` integration

- Sensitive keyword in review → may fire `legally_sensitive` per master-reviews §6.5 TIER-2 (extends to refund dispute auto-escalation per refund-dispute §3.6 DAMAGE)
- TIER-2 protocol mapping to `legally_sensitive` subtier `misconduct_allegation` or `medical_injury`

### 11.6 `customer-notification-controls-ux.md` integration

- Emergency notifications bypass snooze + quiet hours per notification-controls §11
- Voice tone per notification-controls §11.3 (calm, factual, no «URGENT»)
- Customer informed at the right cadence per §4.5 + §7.1

### 11.7 `customer-privacy-data-closure-ux.md` integration

- Open emergency blocks customer account hard-delete per privacy §11.1
- Customer can withdraw most emergencies to unblock closure
- `legally_sensitive` may keep customer account on legal hold beyond customer's deletion request

### 11.8 `tenant-suspension-pause-ux.md` integration

- Per §2.11 + §9.5: SUSPENDED tenant emergencies go to founder; SLA paused

### 11.9 `ai-quality-observability.md` integration

- Forbidden phrase enforcement applies to emergency templates
- Sensitive keyword list shared
- Quality gates check emergency messages

---

## 12. Migration from old 3-tier model

### 12.1 Mapping old → new

| Old reason | Old tier | New emergency tier |
|---|---|---|
| `out_of_catalog` | AI_CONTINUITY | None (Ayla handles, no emergency) |
| `low_confidence` | AI_CONTINUITY | None |
| `booking_edge_case` | AI_CONTINUITY | `booking_conflict` if blocking |
| `multiple_failures` | AI_CONTINUITY | `integration_error` if persistent |
| `price_question_high_intent` | AI_CONTINUITY | None (Ayla handles) |
| `client_ready_to_book` | AI_CONTINUITY | None |
| `vip_flagged` | HUMAN_SUPERVISED | None (Ayla handles; admin can monitor but not intervene) |
| `returning_client` (edge) | HUMAN_SUPERVISED | None |
| `schedule_conflict` | HUMAN_SUPERVISED | `booking_conflict` |
| `payment_issue` (non-refund) | HUMAN_SUPERVISED | `payment_dispute` LOW/MED |
| `complaint_sentiment` | HUMAN_LOCKED | None initially; if customer initiates refund-dispute or asserts injury → escalate per detection |
| `sensitive_topic` (медданные) | HUMAN_LOCKED | `legally_sensitive` IF medical injury alleged |
| `medical_contraindication` | HUMAN_LOCKED | None (Ayla handles per `wellness-symptom-handoff §10` medical routing; not emergency) |
| `payment_issue` (refund) | HUMAN_LOCKED | `payment_dispute` HIGH/CRITICAL |
| `explicit_human_request` (sentiment-charged) | HUMAN_LOCKED | Per tier classification by actual reason |

### 12.2 Migration of in-flight conversations

At deploy of this policy:
1. All conversations in old `HUMAN_LOCKED` due to `payment_issue` (refund) → reclassify as `payment_dispute` emergency
2. Old `HUMAN_LOCKED` due to `sensitive_topic` with medical injury → reclassify as `legally_sensitive`
3. Old `HUMAN_LOCKED` due to other reasons → resolve via Ayla resuming (with admin notification of legacy lock)
4. Old `HUMAN_SUPERVISED` → resolve to Ayla active
5. Old `AI_CONTINUITY` → unchanged

Per Q-AEF12 — migration script with audit trail. Customer not notified of state change.

### 12.3 Code-side cleanup

- `Conversation.ownership_tier` field deprecated (kept for migration period, removed Phase 2)
- New `Conversation.active_emergency_event_id` (FK to EmergencyEvent if any)
- Handoff-reason → emergency-tier mapping function per §12.1

### 12.4 Old docs to update

- `conversation-ownership-policy.md` — mark deprecated, point to this doc
- `single-assistant-identity.md` — already deprecated per Doc #1
- `customer-cancellation-reschedule-spec.md` — check for 3-tier references
- `customer-refund-dispute-ux.md` — already aligned; cross-link to this doc
- `customer-no-show-policy-ux.md` — already aligned via §12
- Wellness handoffs — check for «HUMAN_LOCKED» mentions

### 12.5 Existing PRs

Open PRs (#191 no-show, #189 privacy, #187 notifications, #186 loyalty) use mixed 3-tier + Ayla framing. Per [`ayla-first-strategic-pivot`](./ayla-identity-and-brand.md) migration strategy: merge as-is, fix in voice-sweep pass.

---

## 13. Anti-patterns

### 13.1 Voice violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| «Передаю администратору Анну» | §2.2 — wrong model | «Передаю команде на проверку» |
| «Вам отвечает {{admin_name}}» | §2.2 | Ayla stays voice |
| «URGENT!!! Вам ответят в течение 24 часов» | §2.5 voice rule | «Команда вернётся в течение 24 часов» |
| Showing tier label in customer text | §2.12 | Internal labels only |
| Admin's typo becomes Ayla voice | §5.2 — template-only | Admin selects outcome via UI |
| «Извини, не могу ответить — жди администратора» | §2.13 Ayla doesn't refuse | Ayla collects facts, sets expectation |
| «Я не AI, я админ» | Identity violation per Doc #1 §6.1 | Honest: «Я AI Ayla» |

### 13.2 SLA violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| SLA countdown shown to customer («00:32:14 remaining») | §4.1 hard-countdown anxiety | Soft framing «в течение 48 часов» |
| No customer follow-up if SLA breached | §4.5 silence is rude | Ayla follow-up «извини за задержку» |
| Admin can mark resolved without customer acknowledgment | Quality risk | Customer confirms outcome per §4.6 |
| Allow admin to extend SLA freely | Customer expectation broken | Tenant config has min bounds per §10.1 |
| SLA continues during legal hold | Legal process may take much longer | Pause SLA per §7.4 |

### 13.3 Tier classification violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Auto-classify all customer complaints as `legally_sensitive` (overuse) | Founder fatigue + slow resolution | Detection per specific triggers §3.4 |
| Underuse `legally_sensitive` for medical injury | Liability + safety risk | Detection per shared sensitive keyword list §3.4 |
| Admin reclassifies sensitive case as routine to avoid founder | Audit fraud risk | Audit captures reclassification + founder review |
| Founder routes everything back to admin | Defeats purpose of founder tier | Founder must make call per §6 |
| Mixing tiers in one event | Confusion | One tier per event; new event if different tier triggers later |

### 13.4 Customer manipulation

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Use guilt to discourage refund («подумайте о мастере») | Manipulative | Neutral acknowledgment |
| Offer conditional («если откажетесь — даём скидку») | Anti-pattern per refund-dispute §2.7 | Unconditional offers only |
| Blame customer during emergency | §4.4 trust violation | Neutral framing always |
| Reveal other customers' similar complaints to dissuade | Privacy + manipulation | Customer's case is their case |
| Ayla expressing «I'm sorry it's taking so long» repeatedly (excessive apologies) | Empty filler per Ayla voice §3.5 | One acknowledgment + ETA update |

### 13.5 Backend leakage

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Admin's internal notes visible to customer | Privacy + voice mixing | Admin notes stay in admin UI |
| Other customers' emergency data visible to admin viewing this case | Privacy | Per-customer scope |
| Master sees customer's wellness data during master-flag-disputed-no-show review | Privacy hierarchy per Doc #2 §9.3 | Master sees ONLY relevant booking + customer initials |
| Audit log accessible to admin without role | Audit integrity | Permission-gated; founder-only for `legally_sensitive` data |
| Sensitive memory data accessed during emergency leaks to admin UI | §2.9 + Doc #2 §2.3 | Red-zone data filtered out of emergency review UI; founder-only access logged |

### 13.6 Founder bypass

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Admin marks `legally_sensitive` as resolved without founder | Per §5.5 founder always required for tier 4 | Hard validation gate |
| Founder ignores `legally_sensitive` queue | Customer at risk | Auto-escalation reminders + CSM alert §7.3 |
| Founder decides without reviewing audit | Decision quality | Audit visible during decision; required-review checkbox |
| Founder override without audit reason | Per `policy_deviation_pattern` | Required reason field |

---

## 14. Data models — already specified §8

See §8.1-8.4 for `EmergencyEvent`, `EmergencyAuditLog`, `LegalHoldAccessLog`, `EmergencyResolutionTemplate`.

Total: 4 NEW models.

---

## 15. API contracts

### 15.1 Customer-facing

Customer doesn't directly call emergency APIs — emergencies are detected. Customer interacts via:
- [`customer-refund-dispute-ux §11`](./customer-refund-dispute-ux.md) endpoints (which internally fire `payment_dispute`)
- Booking-conflict customer choice endpoints
- Chat with Ayla (NLU detects)

If customer asks status:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/customer/emergencies/active` | List own active emergencies (status, ETA only — NO admin names) |

### 15.2 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/emergencies/queue` | «Что требует внимания» dashboard §5.1 |
| GET | `/api/v1/admin/emergencies/<id>` | Case detail |
| POST | `/api/v1/admin/emergencies/<id>/decision` | Submit decision via UI (not free text) |
| POST | `/api/v1/admin/emergencies/<id>/request-4-eye` | Request 4-eye admin |
| POST | `/api/v1/admin/emergencies/<id>/escalate-founder` | Manual escalate |
| POST | `/api/v1/admin/emergencies/<id>/extend-sla` | Within bounds; audit required |
| POST | `/api/v1/admin/emergencies/<id>/request-info-from-customer` | Triggers Ayla message to customer with specific question (pre-approved templates) |

### 15.3 Founder endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/emergencies/queue` | Cross-tenant founder queue §6.2 |
| GET | `/api/v1/founder/emergencies/<id>` | Detail with full audit |
| POST | `/api/v1/founder/emergencies/<id>/decide` | Founder decision §6.3 |
| POST | `/api/v1/founder/emergencies/<id>/legal-hold` | Apply legal hold |
| POST | `/api/v1/founder/emergencies/<id>/release-legal-hold` | Release |
| GET | `/api/v1/founder/emergencies/aggregate-patterns` | Cross-tenant pattern analytics §6.7 |

### 15.4 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/emergencies/detect-sensitive-keyword` | Called by message ingestion for tier 4 detection |
| POST | `/internal/emergencies/<id>/sla-breach-scan` | Cron — SLA breach alerts §7.3 |
| POST | `/internal/emergencies/<id>/customer-followup` | Cron — proactive follow-up §4.5 |
| POST | `/internal/emergencies/<id>/resolution-template-render` | Build customer message from template + outcome data |
| POST | `/internal/emergencies/aggregate-pattern-detection` | Cron — founder analytics input §6.7 |

### 15.5 Sample: admin decision

POST `/api/v1/admin/emergencies/<id>/decision`:

```json
{
  "outcome_code": "refund_partial",
  "outcome_amount": 800.00,
  "outcome_metadata": {
    "free_correction_offered": true
  },
  "admin_internal_notes": "Customer claimed 1500₽ but service was substantially delivered; offering goodwill 800 + free fix"
}
```

Server:
1. Validates outcome_code allowed for tier
2. Validates required_variables present
3. Writes EmergencyAuditLog action=`admin_decided`
4. Renders customer message from EmergencyResolutionTemplate
5. Sends to customer via Ayla
6. Updates EmergencyEvent status → `customer_pending`

---

## 16. Events emitted

Add to [`event-taxonomy.md`](./event-taxonomy.md) `3.19 emergency domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| Emergency opened | NEW: `emergency.opened` | tier, subtier, severity, detection_source |
| Customer message sent | NEW: `emergency.customer_message_sent` | template_id |
| Admin viewed | NEW: `emergency.admin_viewed` | admin_id |
| Admin decided | NEW: `emergency.admin_decided` | outcome_code |
| 4-eye requested | NEW: `emergency.4_eye_requested` | |
| 4-eye approved | NEW: `emergency.4_eye_approved` | |
| Founder engaged | NEW: `emergency.founder_engaged` | reason |
| Founder decided | NEW: `emergency.founder_decided` | outcome_code |
| SLA approaching breach | NEW: `emergency.sla_warning` | |
| SLA breached | NEW: `emergency.sla_breached` | tier, age_hours |
| Customer responded | NEW: `emergency.customer_responded` | |
| Customer withdrew | NEW: `emergency.customer_withdrew` | |
| Resolved | NEW: `emergency.resolved` | outcome_code, resolution_time_hours |
| Legal hold applied | NEW: `emergency.legal_hold_applied` | |

14 NEW events §16.

---

## 17. Acceptance criteria (engineering checklist)

- [ ] 4 models §8 (EmergencyEvent, EmergencyAuditLog, LegalHoldAccessLog, EmergencyResolutionTemplate)
- [ ] 18 endpoints §15 (1 customer + 7 admin + 6 founder + 4 internal)
- [ ] 4-tier classification logic per §3
- [ ] Detection triggers integrated:
  - [ ] Refund dispute → `payment_dispute`
  - [ ] Booking conflict engine → `booking_conflict`
  - [ ] SRE alert → `integration_error`
  - [ ] Sensitive keyword → `legally_sensitive` (shared list with master-reviews §6.5)
- [ ] Customer Ayla voice templates §3 per tier (per `ayla-identity-and-brand §3` voice rules)
- [ ] Admin Ayla Pro «Что требует внимания» dashboard §5.1
- [ ] Per-tier admin surfaces wired to existing dispute/conflict resolution UIs §5.4
- [ ] 4-eye admin flow for `payment_dispute > 5000₽` and `legally_sensitive`
- [ ] Founder auto-escalation for all `legally_sensitive` + tier-2 SLA breaches §6.1
- [ ] Founder dashboard «Эскалации» §6.2
- [ ] SLA matrix §7.1 enforced per tier
- [ ] SLA breach scanner cron §7.3
- [ ] Customer follow-up cron §4.5
- [ ] Customer status query endpoint §15.1 (no admin names)
- [ ] Tenant SLA configuration §10.1 with min bounds
- [ ] Customer can withdraw tier 1-3 emergencies §4.3
- [ ] `legally_sensitive` customer withdrawal handling §4.3
- [ ] EmergencyAuditLog append-only with permission gating §8.2
- [ ] LegalHoldAccessLog separate audit §8.3
- [ ] EmergencyResolutionTemplate library per tier §8.4
- [ ] Migration from old 3-tier conversation states §12.2
- [ ] Old `Conversation.ownership_tier` deprecated; new `active_emergency_event_id` FK
- [ ] 14 events §16
- [ ] PII rules: customer never sees admin names; admin sees only relevant scope; founder sees what audit shows
- [ ] Cross-tenant 403 enforcement
- [ ] Tests: 4 tiers detection + resolution / 4-eye flow / founder auto-engagement / SLA breach + auto-escalation / customer follow-up / template rendering / customer withdrawal + legally_sensitive override / tenant config bounds / migration mapping / cross-tenant isolation / audit append-only / red-zone memory not leaked to admin

---

## 18. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-AEF1** | 4 tiers fixed or 5th tier possible? | 4 MVP per §2.3. New trigger types map to existing. Re-evaluate Phase 4+ if pattern emerges. | Policy | 🟢 |
| **Q-AEF2** | Tier classification ambiguity — same event could be 2 tiers? | Use highest applicable. E.g., dispute over allegation = `legally_sensitive` overrides `payment_dispute`. | Policy | 🟡 |
| **Q-AEF3** | Admin types message directly to customer — allowed? | NO MVP per §5.2. Template-driven only. Phase 2+ Q-AEF4. | Policy + UX | 🟢 |
| **Q-AEF4** | Admin custom-wording suggestion — Phase 2+? | YES Phase 2+ with AI quality review pipeline. Template fallback if custom rejected. | UX + AI quality | 🟡 |
| **Q-AEF5** | Customer escalation request («хочу с основателем») — auto-route? | NOT auto-route. Customer's request captured + Ayla acknowledges + decision per case-by-case rules §6.1. Customer demand alone doesn't trigger founder. | Policy | 🟡 |
| **Q-AEF6** | SLA bounds — per tenant freedom to extend? | NO. Min bounds enforced. Can only SHORTEN within reasonable limits. Audit captures changes. | Policy + UX | 🟢 |
| **Q-AEF7** | Customer no-response > 7 days — auto-resolve as withdrawn? | YES per refund-dispute §11.4 alignment. `customer_no_response_expired` status. Reopens window allowed for one re-engagement. | Policy | 🟢 |
| **Q-AEF8** | Founder review queue across tenants — privacy? | Founder sees all data needed to resolve. NO cross-tenant share with other admins. Aggregate patterns only at §6.7 level. | Privacy + Founder | 🟢 |
| **Q-AEF9** | Voice templates — multi-language Phase 3+? | YES Phase 3+ per `ayla-identity-and-brand Q-AYL3/4`. MVP Russian-only. | UX + I18N | 🟢 |
| **Q-AEF10** | Customer requesting status repeatedly — rate-limit? | Soft: Ayla responds same status message; doesn't escalate or annoy. After 5 same-day repeats: «у меня нет новых данных, как только узнаю — напишу». | UX | 🟢 |
| **Q-AEF11** | Sensitive keyword false positives — what if NLU mis-detects? | Admin reviews; can reclassify before customer sees. Audit captures original detection + reclass. Per Q-AEF13. | AI quality | 🔴 PRE-DEPLOY |
| **Q-AEF12** | Migration script for in-flight conversations — when run? | Per `policy_deviation_pattern` discipline: run as part of deployment. Audit captures pre/post state per conversation. Customer not notified. | Eng | 🟡 |
| **Q-AEF13** | Customer repeatedly triggering false emergencies — pattern flag? | Admin-side soft signal at 3+ in 90d (similar to no-show pattern). Anti-fraud Phase 4+. | Policy + Founder | 🟡 |
| **Q-AEF14** | Admin mismarking tier for personal benefit — audit signal? | YES — founder reviews reclassifications + decision quality. Founder can revert + audit captures. | Policy + Founder | 🟡 |
| **Q-AEF15** | LegalHoldAccessLog retention — 7 years or longer? | 7 years minimum; founder can mark «indefinite» for active legal cases. Per Russia consumer law + medical confidentiality conventions. | Legal | 🔴 PRE-DEPLOY |
| **Q-AEF16** | Cross-tenant customer with `legally_sensitive` — both tenants notified? | Only tenant where incident happened. Founder may decide to inform other tenants if safety concern. Audit captures decision. | Privacy + Founder | 🔴 PRE-DEPLOY |
| **Q-AEF17** | Customer < 18 emergency — minor protections additional to §3.4 medical-adjacent? | YES per `ayla-identity-and-brand Q-AYL13` + `ayla-memory Q-AML8`: minor emergencies auto-escalate to founder + parent contact required. Audit logged separately. | Privacy + Legal | 🔴 PRE-DEPLOY |
| **Q-AEF18** | Wellness module emergency (e.g., suicidal mention in mood log) — emergency tier? | YES `legally_sensitive` subtier `mental_health_concern`. Per `wellness-symptom-handoff §10` medical routing — Ayla provides crisis resources immediately + founder informed. | Policy + AI | 🔴 PRE-DEPLOY |
| **Q-AEF19** | Admin sees customer's wellness data during `payment_dispute` review? | NO — privacy hierarchy. Admin sees booking details + dispute claim + financial records. Customer's wellness data customer-only per Doc #2 §9. | Privacy | 🟢 |
| **Q-AEF20** | Voice tone for `legally_sensitive` — softer than other tiers? | Slightly more grounded but NOT dramatic. Ayla acknowledges seriousness without panic. Per `ayla-identity-and-brand §3.2` situation tone modulation. | UX + AI prompt | 🟡 |

---

## 19. Cross-document linkage

### Foundation set
- [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md) — Doc #1 (voice rules applied here)
- [`ayla-memory-and-personalization.md`](./ayla-memory-and-personalization.md) — Doc #2 (memory access during emergency follows §2.9 privacy)
- **This doc** — Doc #3 (emergency fallback)
- `tenant-as-provider-model.md` — TO WRITE: Doc #4 (admin scope vs Ayla scope)
- `anonymous-to-registered-gate.md` — TO WRITE: Doc #5

### Customer-side integration
- [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md) §11.1 — `payment_dispute` integration
- [`booking-conflict-resolution-ux.md`](./booking-conflict-resolution-ux.md) §11.2 — `booking_conflict` integration
- [`customer-no-show-policy-ux.md`](./customer-no-show-policy-ux.md) §11.3 — no-show dispute routes through here
- [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) — late cancel may fire `booking_conflict`
- [`customer-notification-controls-ux.md`](./customer-notification-controls-ux.md) §11.6 — emergency notification rules
- [`customer-privacy-data-closure-ux.md`](./customer-privacy-data-closure-ux.md) §11.7 — emergency blocks closure
- [`customer-loyalty-rewards-ux.md`](./customer-loyalty-rewards-ux.md) — refund-revoke triggers via `payment_dispute`

### Master-side integration
- [`master-time-off-handoff.md`](../handoffs/2026-05-19-master-time-off-handoff.md) §11.4 — sick day may fire `booking_conflict`
- [`master-substitution-handoff.md`](../handoffs/2026-05-19-master-substitution-handoff.md) — substitution conflicts route here
- [`master-reviews-feedback-handoff.md`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md) §11.5 — TIER-2 protocol = `legally_sensitive`
- [`master-admin-internal-chat-handoff.md`](../handoffs/2026-05-19-master-admin-internal-chat-handoff.md) §5.6 — master input during admin review

### Tenant
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) §9.5 — SUSPENDED state interaction

### Quality / AI
- [`ai-quality-observability.md`](./ai-quality-observability.md) — forbidden phrase + sensitive keyword shared

### Old / deprecated
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — deprecated by this doc per §12.4
- Memory `project_single_assistant_identity` — already deprecated by Doc #1 (no policy doc; memory-only artifact)

### Memory
- `project_ayla_first_strategic_pivot` — full pivot context
- `project_conversation_ownership_tiers` (revised) — backend mechanics preserved
- `project_ayla_personal_ai` — voice rules

### Notion
- PRD Ayla v2.0 — Ayla's role in emergency
- BOOK-02 (Notion `338b0dab-2955-819e-be45-ea5aa80f90a6`) — cancel/reschedule emergency flow

---

## 20. What this unblocks

- **Customer experience locked to Ayla** — no «admin takes over» moments, consistency
- **Customer trust** — emergency framing calm + informational, never blaming
- **Admin operational tools** — Ayla Pro queue + per-tier UIs aligned
- **Founder governance** — clear auto-engagement rules + cross-tenant view
- **Audit completeness** — emergency event + audit log + legal hold log
- **SLA discipline** — measured, enforced, customer-respecting
- **Integration with refund / conflict / no-show / reviews / leave** — all routed through one framework
- **Migration path** — old 3-tier conversations cleanly migrated

## 21. What this does NOT unblock

- ❌ Real-time chat with admin (Phase 4+ if value proven)
- ❌ Customer-name-specific admin demand
- ❌ Voice (TTS/STT) emergency handling (Phase 2+)
- ❌ Multi-language (Phase 3+)
- ❌ Anti-fraud ML on emergency abuse
- ❌ Cross-tenant emergency aggregation for admin
- ❌ Skip Q-AEF11 (sensitive keyword false positives) — pre-deploy
- ❌ Skip Q-AEF15 (LegalHoldAccessLog retention) — pre-deploy
- ❌ Skip Q-AEF16 (cross-tenant `legally_sensitive` notification) — pre-deploy
- ❌ Skip Q-AEF17 (minor emergency protections) — pre-deploy
- ❌ Skip Q-AEF18 (wellness mental-health emergency) — pre-deploy

---

## 22. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| AI prompt eng (emergency templates per voice rules + Q-AEF20 tone) | ☐ | 🔴 PRE-DEPLOY |
| AI quality steward (Q-AEF11 sensitive keyword false positives + template review) | ☐ | 🔴 PRE-DEPLOY |
| Refund-dispute steward (§11.1 integration alignment) | ☐ | |
| Booking-conflict steward (§11.2 integration alignment) | ☐ | |
| Master-reviews steward (§11.5 TIER-2 mapping) | ☐ | |
| Privacy / Legal (§2.9 + §8 audit + Q-AEF15 retention + Q-AEF16 cross-tenant + Q-AEF17 minor + Q-AEF18 mental-health) | ☐ | 🔴 PRE-DEPLOY |
| Founder (§6 escalation rules + Q-AEF13/14 abuse detection + cross-tenant queue) | ☐ | 🔴 PRE-DEPLOY |
| Engineering (4 models + 18 endpoints + migration + cron scanners) | ☐ | |
| Mini App frontend (Ayla Pro queue + per-tier resolution screens + founder dashboard) | ☐ | |
| Conversation-ownership-policy.md migration owner (§12 deprecate old) | ☐ | |
| Accessibility (WCAG 2.2 AA on admin queue + founder dashboard) | ☐ | |

## Last verified
2026-05-19 (initial draft, 4 emergency tiers + customer-Ayla voice templates + admin Ayla Pro surfaces + founder escalation + SLA matrix + audit immutable + migration from 3-tier ownership — locked. Foundation Doc #3 of 5 for Ayla-first pivot. Rewrites conversation-ownership-policy.)
