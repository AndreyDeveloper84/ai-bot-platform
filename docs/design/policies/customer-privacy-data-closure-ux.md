# Customer Privacy / Data Export / Account Closure — UX Policy

**Date:** 2026-05-19 r1
**Status:** Production-blocking — GDPR + Russia 152-FZ alignment; customer trust foundation
**Reads:** [`customer-profile-management-ux.md`](./customer-profile-management-ux.md), [`customer-notification-controls-ux.md`](./customer-notification-controls-ux.md), [`customer-loyalty-rewards-ux.md`](./customer-loyalty-rewards-ux.md), [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md), [`customer-wellness-dashboard-ux.md`](./customer-wellness-dashboard-ux.md), [`core-wellness-profile.md`](./core-wellness-profile.md), [`wellness-input-modules.md`](./wellness-input-modules.md), [`single-assistant-identity.md`](./single-assistant-identity.md), [`attribution-policy.md`](./attribution-policy.md), [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md), [`../handoffs/2026-05-19-master-offboarding-handoff.md`](../handoffs/2026-05-19-master-offboarding-handoff.md), [`event-taxonomy.md`](./event-taxonomy.md)

> Customer has rights: see what we know, fix mistakes, export, take it elsewhere, stop being known. Today no UX exists for any of these — customer would email founder begging. This policy specifies View / Export / Rectify / Soft-Delete / Hard-Delete flows with audit, cooling-off, and cross-tenant boundaries.

---

## 0. Why this exists

### 0.1 Legal foundation

- Russia 152-FZ «Personal Data» (Article 14 — right to access; Article 21 — right to erasure)
- GDPR-style alignment (right to access, rectification, erasure, portability, restriction)
- Consumer protection: customer who paid is owed data after relationship ends

### 0.2 Operational reality

Today customer who wants to leave a salon has no path:
- Goes silent (wellness data accumulates indefinitely)
- Asks salon owner via WhatsApp («можете удалить мои данные?») — admin doesn't know how
- Loyalty balance lost on goodwill basis
- Referral chain dangling
- Reviews about masters frozen but customer can't withdraw

### 0.3 The promise

Single source for:
- View my data §3 (what platform knows)
- Export §4 (CSV / PDF / JSON downloads)
- Rectification §5 (fix wrong data)
- Soft-delete §6 (pause / hidden but recoverable)
- Hard-delete §7 (irreversible after cooling-off)
- 30-day cooling-off §8
- What survives deletion §9 (audit, financial, consent, anonymized aggregate)
- Cross-tenant boundaries §10
- Pre-deletion blockers §11 (open dispute, future booking, etc.)
- AI Bot DM touchpoints §12 (5 templates)
- 5 NEW models, 16 endpoints, 12 events

---

## 1. Scope

### IN
- Customer Mini App «Мои данные» section in Profile
- 5 customer-facing actions: View / Export / Rectify / Soft-delete / Hard-delete
- 30-day cooling-off period before hard-delete §8
- Pre-deletion blocker checks §11 (open dispute, pending booking, etc.)
- Cross-tenant: deletion at tenant A doesn't affect tenant B per Q-CO5
- Wellness data export comprehensive (all 7 modules + dashboard observations + goals)
- Loyalty balance handling on closure (per Q-CL16 forfeit)
- Referral chain handling §9.3
- Master earnings retroactive integrity (audit, anonymized booking source) §9.4
- Attribution policy interaction §9.5
- AI Bot DM acknowledgments + cooling-off reminders §12
- Founder approval for hard-delete (audit immutability vs right to erasure tension)
- 12 NEW customer-facing events

### OUT
- Anti-fraud retention beyond legal minimum (don't keep extra «just in case»)
- Cross-tenant aggregation of customer data (privacy boundary always)
- Manual admin-initiated customer deletion (admin can SUGGEST customer use flow, not bypass)
- Mass deletion (whole-tenant data wipe) — covered by tenant-shutdown-policy future
- Data portability to ANOTHER platform (out of scope; export is the customer's job)
- Right to be informed about data BREACH — separate `breach-notification-policy.md` future
- Data subject access request via lawyer / proxy — Phase 4+
- Government data request handling — separate legal process
- Photo evidence retention (AI Avatar Phase 3+) — separate spec
- Subject access by deceased customer's estate — Phase 4+
- Child / minor data (per platform rule no < 18 wellness) — see customer-onboarding for minor gate; deletion follows same flow
- Customer's IP address history surfaced to customer — out of scope (audit only per Q-MD7 master-device-reauth pattern)

---

## 2. Strategic constraints — non-negotiable

### 2.1 Discoverable, not buried
- Mini App Profile → «Мои данные» — one tap from main nav
- Bot DM «как мне посмотреть, что вы про меня знаете?» NLU → route to here
- NOT hidden in 4-menu deep settings

### 2.2 No retention-justifying friction
- Customer chose to close → respected
- Some confirmations exist (cooling-off, blocker checks) but NOT «are you SURE? you'll lose X! think again!»

### 2.3 30-day cooling-off is OPT-OUT during request
Customer can request «no cooling-off, delete now» — but operational records (financial audit, dispute) must clear first §11.

### 2.4 What's customer-owned vs operational record
- Customer-owned: wellness data, AI memory of customer, profile self-set fields, personal preferences, photo (Phase 3+)
- Operational record: booking history (purpose: salon record), financial transactions, audit logs

Customer-owned hard-deletes immediately on confirmation (post cooling-off). Operational records anonymize (booking customer_id → null, customer_name → «Удалённый клиент») but retained per consumer-protection law minimum 3 years.

### 2.5 Loyalty balance forfeits
Per [`customer-loyalty-rewards-ux Q-CL16`](./customer-loyalty-rewards-ux.md): on hard-delete, loyalty balance forfeit. Customer informed before confirming. Audit captures balance.

### 2.6 Referral chain handling
- Inviter who deletes account → their REFERRAL records remain (inviter_id captured); future referee credits not triggered (no inviter to credit)
- Referee who deletes account → their referral attribution remains in audit; inviter's reward not retroactively revoked

### 2.7 Reviews customer left
Per [`master-reviews-feedback-handoff §2.5`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md): reviews preserved on master aggregate. Customer's authorship anonymized («бывший клиент» instead of «Мария И.»). Per Q-CP7.

### 2.8 Master conversations
Master's chat history with customer (per existing customer Mini App): customer's messages anonymized (replaced with «[удалено]»), master's own messages intact. Per Q-CP8.

### 2.9 Founder approval for hard-delete
Per Q-CP10: founder approves every hard-delete request. NOT bottleneck — async approval within 7 days. Allows compliance review (e.g., open legal hold, anti-fraud check). Audit captures approval.

### 2.10 Customer can always re-create
Hard-delete is final FROM THIS SALON'S DATA. Customer can re-onboard fresh (new BotUser, same MAX-ID). No «blacklist» from re-joining.

### 2.11 Cross-tenant strict
Customer at A + B requests deletion at A → ONLY tenant A's customer record cleared. Tenant B untouched. Per [`master-substitution-handoff §2.9`](../handoffs/2026-05-19-master-substitution-handoff.md) precedent on per-tenant data.

### 2.12 Single-assistant identity in messaging
Per [`single-assistant-identity §2.4`](./single-assistant-identity.md): AI's deletion confirmations / cooling-off reminders use customer's voice — neutral, calm. NEVER «sad to see you go!» / «one more chance!».

### 2.13 No dark patterns
- ❌ «Wait! Here's 30% off if you stay»
- ❌ Pre-checked boxes «consider saving wellness data»
- ❌ Confusing terminology («pause» ≠ «delete» — clear distinction)
- ❌ Hide options
- ✅ Clear options, immediate action where appropriate

### 2.14 Transparent retention
Customer sees what's retained after hard-delete and why:
- Audit log (immutable, anonymized after 30 days post-hard-delete)
- Financial records (3 years per consumer law)
- Consent log (7 years per GDPR/152-FZ)

### 2.15 Wellness data hard-delete is irreversible
Customer wellness across 7 modules (mood / water / body / sleep / symptom / food / AI Avatar) — fully customer-owned. Hard-delete = gone. No copy retained even anonymized (privacy hierarchy strictest).

### 2.16 Pre-deletion blocker prompts
Per §11: open refund dispute / pending future booking / open earnings dispute (involving customer's review or refund) block hard-delete request. Customer guided to resolve first.

### 2.17 Soft-delete is reversible by customer self-service
Within 30 days, customer can self-undo soft-delete. After 30 days → hard-delete completes; reversal requires founder process (audit captures unusual).

### 2.18 Tenant SUSPENDED interaction
Per [`tenant-suspension-pause-ux`](./tenant-suspension-pause-ux.md): if tenant SUSPENDED, customer can still request export + rectification + initiate deletion. Hard-delete completion deferred to post-resumption (or to founder if tenant ARCHIVED).

---

## 3. View my data

### 3.1 Mini App «Мои данные» section

```
┌────────────────────────────────────────┐
│ 🔐 Мои данные                            │
├────────────────────────────────────────┤
│ ── Что мы знаем про вас ──              │
│                                        │
│ 📌 Профиль                                │
│ Имя, телефон, день рождения,             │
│ предпочтения по услугам, любимые        │
│ мастера                                  │
│ [Посмотреть]                             │
│                                        │
│ 📅 История посещений                      │
│ 47 записей за время с нами               │
│ [Посмотреть]                             │
│                                        │
│ 🎁 Бонусы                                 │
│ 250 баллов, уровень Постоянный          │
│ [Посмотреть]                             │
│                                        │
│ 💚 Самочувствие                          │
│ Подключено модулей: 3                    │
│ Записей: 142                             │
│ [Посмотреть]                             │
│                                        │
│ 📋 Отзывы и обращения                    │
│ 8 отзывов, 1 закрытое обращение         │
│ [Посмотреть]                             │
│                                        │
│ 🔔 Настройки уведомлений                 │
│ [Посмотреть]                             │
│                                        │
│ 📜 История настроек согласия             │
│ [Посмотреть]                             │
│                                        │
│ ── Действия ──                            │
│ [📥 Скачать всё одним архивом]            │
│ [✏ Исправить данные]                     │
│ [⏸ Приостановить аккаунт]                │
│ [🗑 Удалить аккаунт]                      │
└────────────────────────────────────────┘
```

### 3.2 Per-section detail

Each tap opens read-only screen showing all data of that type. Same content as export, but UI-rendered. Plain language («что записано», not «field names»).

### 3.3 Source attribution per data piece

«Откуда мы это знаем?»:
- Profile fields: «Вы заполнили в анкете 12 марта 2024»
- Booking history: «Из ваших записей»
- Wellness: «Из ваших модулей самочувствия»
- AI-derived (preferences, observations): «Помощник вывел из ваших действий»

Transparency builds trust.

### 3.4 What we DON'T know

Section: «Чего мы НЕ знаем»:
- Местоположение (не отслеживаем GPS)
- IP-адрес (для аудита, не показываем)
- Метаданные браузера (не сохраняем долгосрочно)
- ...

Per Q-CP13.

---

## 4. Export

### 4.1 «Скачать всё одним архивом» flow

```
┌────────────────────────────────────────┐
│ ← Скачать данные                          │
├────────────────────────────────────────┤
│ В архив войдёт:                          │
│                                        │
│ ✓ Профиль (имя, дата рождения и т.д.)   │
│ ✓ Все записи и услуги                     │
│ ✓ История бонусов и баллов                │
│ ✓ Данные модулей самочувствия           │
│ ✓ Отзывы (с вашей стороны)                │
│ ✓ Обращения и жалобы                      │
│ ✓ Настройки и согласия                    │
│ ✓ Сообщения с помощником                 │
│                                        │
│ Формат:                                   │
│ ⦿ ZIP с CSV + PDF (понятный человеку)   │
│ ◯ JSON (для разработчика / переноса     │
│   на другой сервис)                      │
│                                        │
│ Время подготовки: ~5 минут               │
│ Файл будет доступен 7 дней                │
│                                        │
│ [Подготовить архив]                       │
└────────────────────────────────────────┘
```

### 4.2 Background generation

Per Q-CP15: export = background job (`CustomerDataExportRequest`). Customer notified when ready:

```
{{customer_first_name}}, ваш архив готов. Можно скачать в течение 7 дней:
[Скачать (12 MB)]

После 7 дней файл удалится; повторно — формируйте новый.
```

### 4.3 What's in the archive

**Profile section (CSV + readable PDF):**
- Identity: name, date of birth (if shared), MAX username
- Contact: phone (E.164), email (if provided)
- Created date, last login
- Per-tenant: which salons

**Bookings (CSV):**
- Every booking: date, time, service, master_initials, price, status, refund if any

**Wellness (per-module CSV + observations PDF):**
- Mood logs / water counters / body measurements / sleep logs / symptoms / food entries / AI Avatar photos (Phase 3+)
- Cross-module observations from dashboard
- Wellness goals + history

**Loyalty (CSV):**
- Balance + all events (earn / redeem / tier-change / refund-revoke)
- Referrals issued + status

**Reviews + Disputes (CSV):**
- Reviews customer left (rating, text, themes)
- Refund disputes (type, status, outcome)

**Settings + Consent (JSON):**
- Notification preferences
- Snooze history
- ConsentLog full history

**AI conversations (markdown or JSON):**
- Bot DM message history
- Customer-initiated AI Q&A

**Audit (Customer-readable summary):**
- High-level «what we did with your data» — when was profile updated, when did booking complete, etc. NOT internal system audit (would be confusing).

### 4.4 PII in archive

Customer's OWN PII included — it IS their data. Other customers / masters / employees referenced by initials only.

### 4.5 Archive expiration

Per Q-CP16: 7 days from generation. Customer must download within window. Audit captures generation + download events.

### 4.6 Multiple export requests

No limit on number of exports. Customer can refresh anytime. Audit row per request.

### 4.7 Multi-tenant export

Customer at A + B → choice at start: «За одну студию или всё вместе?». Per-tenant or combined. Combined ZIP has per-tenant sub-folders.

---

## 5. Rectification

### 5.1 «Исправить данные» flow

```
┌────────────────────────────────────────┐
│ ← Исправить данные                        │
├────────────────────────────────────────┤
│ Большинство данных можно исправить       │
│ в Профиле (имя, телефон, день рождения, │
│ предпочтения).                            │
│                                        │
│ [Открыть профиль]                         │
│                                        │
│ ── Если данные неверны где-то ещё ──    │
│                                        │
│ Например: запись прошла, но в истории   │
│ указана не та услуга. Или баллы          │
│ начислены неправильно.                   │
│                                        │
│ Опишите:                                  │
│ [_____________________________]        │
│                                        │
│ Что неверно:                              │
│ [_____________________________]        │
│                                        │
│ Что должно быть:                          │
│ [_____________________________]        │
│                                        │
│ [Отправить запрос]                        │
└────────────────────────────────────────┘
```

### 5.2 Self-service rectification

For profile fields (name, birthday, phone, etc.): customer edits directly via [`customer-profile-management-ux.md`](./customer-profile-management-ux.md). No support intervention.

### 5.3 Admin-supported rectification

For non-profile data (booking records, loyalty events): admin reviews via `CustomerDataRectificationRequest` row §13.3. SLA 48h. Founder escalation if disagree.

### 5.4 Rectification audit

Every change to customer data creates `DataRectification` row §13.3 OR uses existing audit (booking edits, loyalty manual_adjust). Customer sees in own «История» view §3.

### 5.5 Cannot rectify others' data

Customer cannot edit:
- Master's reviews of THEM (Phase 4+ master-side review system; not MVP)
- Booking history beyond «what service / when» (master's notes etc. are master's)
- Tenant's own records (tenant SLA, etc.)

Customer requests admin to fix what's THEIRS only.

---

## 6. Soft-delete

### 6.1 Trigger

Customer Mini App → «Приостановить аккаунт»:

```
┌────────────────────────────────────────┐
│ ← Приостановить аккаунт                   │
├────────────────────────────────────────┤
│ Что произойдёт:                          │
│                                        │
│ Аккаунт станет «спящим»:                  │
│ ✗ Новых уведомлений не будет             │
│ ✗ Помощник перестанет писать первым      │
│ ✗ Записи нельзя будет создавать          │
│ ✗ Wellness модули поставятся на паузу    │
│                                        │
│ Что сохранится:                          │
│ ✓ Профиль и история                      │
│ ✓ Баллы и уровень                         │
│ ✓ Сообщения с помощником                 │
│                                        │
│ Восстановить — в любой момент в течение  │
│ 30 дней. Просто откройте Мини-App.       │
│                                        │
│ Через 30 дней — переходит в полное       │
│ удаление (можно отменить раньше).        │
│                                        │
│ Уверены?                                  │
│ [Приостановить]   [Передумала]            │
└────────────────────────────────────────┘
```

### 6.2 State during soft-delete

- `BotUser.is_soft_deleted = True`
- `BotUser.soft_deleted_at = now()`
- Wellness modules consent paused (per [`wellness-input-modules §11`](./wellness-input-modules.md))
- Notifications all paused (per [`customer-notification-controls-ux §11`](./customer-notification-controls-ux.md) emergency override still allowed)
- Bookings: existing future bookings auto-cancel? Or kept? Per Q-CP4 — KEPT but customer can complete OR cancel before re-engaging
- Bot DM blocked (AI silent until reactivation)
- Mini App opens to «Аккаунт приостановлен» screen with «Восстановить» CTA

### 6.3 Reactivation (self-service)

Customer opens Mini App during 30-day window → reactivation prompt:

```
┌────────────────────────────────────────┐
│ С возвращением!                          │
├────────────────────────────────────────┤
│ Ваш аккаунт был приостановлен 5 дней    │
│ назад. Хотите вернуться?                 │
│                                        │
│ ✓ Восстановится профиль                  │
│ ✓ Сохранены 250 баллов                   │
│ ✓ Wellness модули возобновятся          │
│   (Mood + Sleep)                         │
│                                        │
│ [Вернуться]   [Передумала, оставить     │
│                  на паузе]                │
└────────────────────────────────────────┘
```

### 6.4 Bot DM during soft-delete

NO Bot DM (notifications paused). Customer not prompted.

EXCEPT at day 25 — gentle 5-days-before warning Bot DM (Q-CP6):

```
{{customer_first_name}}, ваш приостановленный аккаунт через 5 дней
перейдёт в полное удаление.

Можете:
[Вернуться сейчас]
[Удалить сразу]
[Продлить паузу на 30 дней]
```

This single Bot DM bypasses notification pause (similar to emergency override).

### 6.5 Soft-delete extension

Customer can extend pause by 30 days, total cap 90 days (3 × 30). After 90d cumulative, must reactivate OR proceed to hard-delete.

### 6.6 Operational triggers during soft-delete

If customer has open booking that admin marks COMPLETED during soft-delete: loyalty subscriber checks `is_soft_deleted` → no EARN_VISIT credit. Booking record updated normally.

---

## 7. Hard-delete

### 7.1 Trigger

Customer Mini App → «Удалить аккаунт»:

```
┌────────────────────────────────────────┐
│ ← Удалить аккаунт                         │
├────────────────────────────────────────┤
│ Это серьёзный шаг — расскажу что         │
│ произойдёт.                              │
│                                        │
│ ── Удалится навсегда ──                  │
│ ✗ Профиль и контактные данные           │
│ ✗ История записей (станет анонимной)    │
│ ✗ Wellness данные (все 7 модулей)       │
│ ✗ Цели по самочувствию                  │
│ ✗ AI-память помощника                    │
│ ✗ Список любимых мастеров               │
│ ✗ Личные предпочтения                    │
│                                        │
│ ── 250 баллов сгорят ──                  │
│ Не получится использовать. Хотите —      │
│ запишитесь до удаления и используйте.    │
│ [Записаться и потратить баллы]           │
│                                        │
│ ── Что останется ──                      │
│ ✓ Аудит факт удаления (требует закон)   │
│ ✓ Финансовые записи (3 года, без имени) │
│ ✓ Согласия (7 лет, без имени)            │
│ ✓ Отзывы про мастеров (станут анонимные)│
│                                        │
│ ── Перед удалением ──                    │
│ Нужно сначала:                            │
│ ⓘ Закрыть открытую жалобу 17 мая        │
│   [Открыть жалобу]                       │
│                                        │
│ После этого можно будет удалить.         │
│                                        │
│ ──                                       │
│ Можно сначала скачать всё:                │
│ [Скачать архив]                          │
└────────────────────────────────────────┘
```

### 7.2 Pre-deletion blockers per §11

Listed inline §7.1. Customer must resolve before proceeding.

### 7.3 Confirmation step

After all blockers cleared:

```
┌────────────────────────────────────────┐
│ Подтвердите удаление                     │
├────────────────────────────────────────┤
│ Это последнее предупреждение. После      │
│ подтверждения:                            │
│                                        │
│ 1. Аккаунт станет «удаляемым» на 30 дней│
│ 2. В течение 30 дней можно отменить     │
│    в Мини-App                            │
│ 3. После 30 дней — данные удалятся       │
│    безвозвратно                          │
│                                        │
│ Можно сделать сразу (без 30 дней)?       │
│ ☐ Удалить немедленно (нельзя отменить)  │
│                                        │
│ ── Напишите «УДАЛИТЬ» для подтверждения│
│ [_____________________________]        │
│                                        │
│ [Подтвердить удаление]   [Передумала]    │
└────────────────────────────────────────┘
```

### 7.4 30-day cooling-off (default)

After confirmation:
- `CustomerAccountClosureRequest.status = 'cooling_off'`
- `BotUser.is_soft_deleted = True`, `is_hard_deleting = True`
- Customer's data preserved during 30 days
- Reactivation: same as soft-delete §6.3 — Mini App opens to «Отменить удаление» prompt
- Founder approval queued for day-30 deletion §7.6

### 7.5 Immediate hard-delete (opt-out)

If customer checks «Удалить немедленно»:
- Founder approval still required
- Founder reviews within 48h
- If approved: deletion proceeds same day
- If founder requests clarification (anti-fraud, legal hold): cooling-off extended

### 7.6 Day-30 founder review

For non-immediate flow: at day 30, request becomes `pending_founder_approval`. Founder reviews within 7 days:

```
┌────────────────────────────────────────┐
│ Account closure review                   │
├────────────────────────────────────────┤
│ Customer: {{customer_first_name}} {{initial}}│
│ Tenant: {{salon_name}}                   │
│ Closure requested: 30 days ago          │
│ Cooling-off ended: today                 │
│                                        │
│ Pre-checks:                              │
│ ✓ No open disputes                       │
│ ✓ No pending bookings                    │
│ ✓ No legal hold                          │
│ ✓ Loyalty balance forfeit notified      │
│ ✓ Customer has not reactivated          │
│                                        │
│ Will be deleted:                         │
│ • {{X}} bookings (anonymized)            │
│ • {{Y}} wellness logs (full delete)      │
│ • {{Z}} loyalty events (anonymized)      │
│ • Profile + AI memory + photos (full)    │
│                                        │
│ Will be retained:                         │
│ • Audit log (anonymized 30d post)        │
│ • Financial records (3 years)             │
│ • Consent log (7 years)                   │
│                                        │
│ [Approve deletion]                        │
│ [Request clarification (legal hold)]      │
│ [Reject (rare; reason required)]          │
└────────────────────────────────────────┘
```

### 7.7 Execution

Approved → background job:
1. Wellness data hard-delete (all 7 modules + observations + goals)
2. Profile hard-delete (name, phone, email, birthday cleared)
3. AI memory entries deleted
4. Photo Phase 3+ hard-delete
5. Booking records anonymize (customer_id → null, references → «Удалённый клиент»)
6. Loyalty events anonymize
7. Reviews anonymize («бывший клиент»)
8. Master chat messages anonymize («[удалено]»)
9. `BotUser` flagged `deletion_completed_at`
10. Audit row immutable
11. Customer's MAX/identity link broken (re-onboarding creates new BotUser)

### 7.8 Customer Bot DM confirmation

Per §12.3:

```
Ваш аккаунт удалён. Спасибо за время вместе.

Если когда-нибудь захотите вернуться — просто откройте Мини-App снова.
Будет как новый старт.
```

### 7.9 Hard-delete is final

Past day 30 + executed: no recovery. Even founder cannot restore (audit retains anonymized record but original data unrecoverable).

---

## 8. Cooling-off period

### 8.1 Default 30 days

Per Q-CP9: aligned with industry GDPR-aware platforms. Long enough for genuine reconsideration; short enough not to delay genuine wishes.

### 8.2 Customer opt-out (immediate)

§7.5 — customer can request «no cooling-off». Founder still approves (anti-fraud).

### 8.3 Day-25 reminder

Per §6.4 / §7.4: gentle Bot DM 5 days before completion. Customer can:
- Cancel deletion (reactivate)
- Confirm + complete sooner
- Extend pause (soft-delete only)

### 8.4 Founder override

If legal hold OR anti-fraud concern detected during cooling-off: founder can extend OR pause. Customer informed via Bot DM «закрытие задерживается на проверку соответствия закону».

### 8.5 Reactivation during cooling-off

Customer can reactivate at any point in 30-day window. Per §6.3 simple flow. Pre-deletion state restored. Audit captures «closure cancelled by customer day N».

---

## 9. What survives deletion

### 9.1 Audit log

Per Q-CP11: anonymized audit retained 30 days post-hard-delete then deleted. Captures fact that deletion happened, when, customer_id ref (null after 30d), tenant_id. Required for compliance verification.

### 9.2 Financial records

Per Russia consumer law: 3 years post-transaction. Booking + payment data anonymized but retained. Salon's accounting records (tax filings, etc.) include aggregated transaction data without customer identity beyond anonymized booking_id.

### 9.3 Referral chain

Per §2.6:
- Inviter deletes → REFERRAL rows retain `inviter_id` reference (but referenced BotUser is deleted; FK becomes orphan; pull-detail shows «бывший клиент»)
- Referee deletes → same handling
- Reward rewards already credited remain on the OTHER party's account

### 9.4 Master earnings retroactive integrity

Master's `MasterEarning` rows reference `booking_id`. When booking anonymized:
- `booking.customer_id` → null
- Earnings row intact (master earned legitimately)
- Audit trail preserved (master's accountant view shows «Маникюр клиент XXX» where XXX is anonymized)

Master never loses earnings due to customer's deletion.

### 9.5 Attribution policy

Per [`attribution-policy.md`](./attribution-policy.md): booking_source persists on booking row. Customer anonymization doesn't change source. Founder-50 cohort analytics use anonymized aggregate.

### 9.6 ConsentLog

Per Q-CN17 + Q-CP14: 7 years per GDPR/152-FZ. Customer-specific entries anonymized 30 days post-deletion (customer_id null). Tenant-scoped consent records retain decision history without identity.

### 9.7 Customer's reviews on masters

Per §2.7 + [`master-reviews-feedback-handoff §8`](../handoffs/2026-05-19-master-reviews-feedback-handoff.md): aggregate frozen but individual review remains attached to master with anonymized authorship («бывший клиент»). Master can still see they got that review. Customer cannot retract post-deletion (and shouldn't need to — review was their genuine feedback).

### 9.8 What does NOT survive

- Profile (name, birthday, phone, email): hard-deleted
- Wellness across 7 modules: hard-deleted (privacy hierarchy strictest)
- AI memory: hard-deleted
- Photo Phase 3+: hard-deleted
- Direct messages with AI: hard-deleted

---

## 10. Cross-tenant boundaries

### 10.1 Multi-tenant customer
Customer at salons A + B has SEPARATE `BotUser` records per tenant per Q-CO5. Deletion is per-BotUser per-tenant.

### 10.2 Tenant-scoped deletion

```
┌────────────────────────────────────────┐
│ ← Удаление аккаунта                       │
├────────────────────────────────────────┤
│ В каких студиях удалить?                 │
│                                        │
│ ⦿ Только в этой студии (Натали)         │
│   ⓘ Данные в Студии Lounge сохранятся   │
│                                        │
│ ◯ Во всех студиях (2 шт)                 │
│   Натали + Lounge                        │
│                                        │
│ [Дальше]                                 │
└────────────────────────────────────────┘
```

«Во всех студиях» triggers parallel closure requests per tenant. Each requires founder approval independently.

### 10.3 Per-MAX-identity policy

MAX identity (the underlying account) remains intact across tenant deletions. Customer can re-onboard at any tenant later via same MAX without «blacklist».

### 10.4 Founder cross-tenant view

Founder reviewing deletion sees ONLY that tenant's data. Cross-tenant aggregation not available (privacy).

---

## 11. Pre-deletion blockers

### 11.1 Block list

Cannot proceed to hard-delete if ANY of:

| Blocker | How to resolve |
|---|---|
| Open refund dispute | Close dispute first (customer accepts admin offer / withdraw) |
| Pending future booking (not yet COMPLETED or CANCELLED) | Cancel or complete |
| Open master review (within 7-day edit window) | Wait for window to close OR retract review |
| Outstanding referral pending conversion | (Auto-cleared on hard-delete §2.6) |
| Account in dispute with founder Q12-δ cohort review | Wait for cohort review to complete |
| Founder hold (anti-fraud flag, legal hold) | Founder process |
| Customer attempting multiple hard-delete in 90d | 30d cooldown between attempts |

### 11.2 Customer guided

Hard-delete UI §7.1 shows blockers inline with action CTA per blocker. Customer can't accidentally bypass.

### 11.3 Tenant SUSPENDED interaction

Per §2.18: customer can REQUEST deletion during tenant SUSPENDED but completion deferred to post-resumption. Customer informed.

### 11.4 If new booking created during cooling-off

Customer reactivates implicitly → cooling-off cancelled, account active. Per Q-CP18.

---

## 12. AI Bot DM touchpoints — 5 templates

### 12.1 Export ready

```
{{customer_first_name}}, архив с вашими данными готов. Можно скачать в
течение 7 дней:

[Скачать архив]

После — файл удалится (просто формируется новый при необходимости).
```

### 12.2 Soft-delete day 25 reminder

```
{{customer_first_name}}, ваш приостановленный аккаунт через 5 дней
перейдёт в полное удаление.

Можете:
[Вернуться сейчас]
[Удалить сразу]
[Продлить паузу на 30 дней]
```

### 12.3 Hard-delete completed

```
Ваш аккаунт удалён. Спасибо за время вместе.

Если когда-нибудь захотите вернуться — просто откройте Мини-App снова.
Будет как новый старт.
```

### 12.4 Founder hold (rare)

```
{{customer_first_name}}, удаление вашего аккаунта на проверке у нашего
основателя — это стандартная процедура соответствия. Свяжемся в течение
7 дней.

Если что-то срочное — [Связаться лично].
```

### 12.5 Rectification request acknowledged

```
{{customer_first_name}}, получили ваш запрос. {{salon_owner}}
посмотрит в течение 48 часов и напишет.
```

---

## 13. Data models

### 13.1 `CustomerAccountClosureRequest`

```python
class CustomerAccountClosureRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('identity.BotUser', on_delete=CASCADE, related_name='closure_requests')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    CLOSURE_TYPE_CHOICES = [
        ('soft_delete', 'Soft delete (pause)'),
        ('hard_delete_cooling_off', 'Hard delete with 30d cooling-off'),
        ('hard_delete_immediate', 'Hard delete immediate'),
    ]
    closure_type = models.CharField(max_length=32, choices=CLOSURE_TYPE_CHOICES)

    requested_at = models.DateTimeField(auto_now_add=True)
    cooling_off_ends_at = models.DateTimeField(null=True, blank=True)
    # For hard_delete_cooling_off only

    STATUS_CHOICES = [
        ('opened', 'Opened by customer'),
        ('cooling_off', 'In 30d cooling-off'),
        ('pending_founder_approval', 'Awaiting founder review'),
        ('founder_approved', 'Founder approved'),
        ('founder_clarification_requested', 'Founder requested more info'),
        ('founder_rejected', 'Founder rejected (rare)'),
        ('cancelled_by_customer', 'Customer cancelled'),
        ('executing', 'Deletion executing'),
        ('completed', 'Deletion completed'),
        ('legal_hold', 'On legal/compliance hold'),
    ]
    status = models.CharField(max_length=64, choices=STATUS_CHOICES, default='opened')

    founder_reviewed_at = models.DateTimeField(null=True, blank=True)
    founder_user = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    founder_decision_reason = models.TextField(blank=True, default='', max_length=1000)

    executed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by_customer = models.BooleanField(default=False)

    blocker_checklist_snapshot = models.JSONField(default=dict)
    # Snapshot of §11 blockers at request time

    affected_data_summary = models.JSONField(default=dict)
    # Counts: bookings, wellness logs, loyalty events, etc.

    customer_response_no_cooling_off = models.BooleanField(default=False)

    class Meta:
        indexes = [
            Index(fields=['customer', '-requested_at']),
            Index(fields=['tenant', 'status']),
            Index(fields=['cooling_off_ends_at']),
        ]
```

### 13.2 `CustomerDataExportRequest`

```python
class CustomerDataExportRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('identity.BotUser', on_delete=CASCADE, related_name='export_requests')
    tenant = models.ForeignKey('tenancy.Tenant', null=True, blank=True, on_delete=SET_NULL, related_name='+')
    # null = customer-global combined export

    FORMAT_CHOICES = [
        ('zip_csv_pdf', 'ZIP CSV+PDF (human-readable)'),
        ('json', 'JSON (machine)'),
    ]
    format = models.CharField(max_length=32, choices=FORMAT_CHOICES)

    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('generating', 'Generating'),
        ('ready', 'Ready for download'),
        ('downloaded', 'Customer downloaded'),
        ('expired', 'Expired (7d window)'),
        ('failed', 'Generation failed'),
    ]
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='queued')

    file_path = models.CharField(max_length=256, blank=True, default='')
    file_sha256 = models.CharField(max_length=64, blank=True, default='')
    file_size_bytes = models.IntegerField(default=0)

    requested_at = models.DateTimeField(auto_now_add=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    # ready_at + 7 days

    class Meta:
        indexes = [
            Index(fields=['customer', '-requested_at']),
            Index(fields=['expires_at']),
        ]
```

### 13.3 `CustomerDataRectificationRequest`

```python
class CustomerDataRectificationRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('identity.BotUser', on_delete=CASCADE, related_name='rectification_requests')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    description = models.TextField(max_length=2000)
    what_is_wrong = models.TextField(max_length=1000)
    what_should_be = models.TextField(max_length=1000)

    STATUS_CHOICES = [
        ('opened', 'Opened'),
        ('admin_reviewing', 'Admin reviewing'),
        ('resolved_corrected', 'Corrected'),
        ('resolved_no_change', 'No change (with reason)'),
        ('escalated_to_founder', 'Escalated'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='opened')

    admin_response = models.TextField(blank=True, default='', max_length=2000)
    admin_user = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    admin_at = models.DateTimeField(null=True, blank=True)

    affected_records_metadata = models.JSONField(default=dict)
    # What got changed (without sensitive content)

    requested_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    sla_due_at = models.DateTimeField()
```

### 13.4 `DataAnonymizationLog`

Audit row per anonymization action (per-table).

```python
class DataAnonymizationLog(models.Model):
    closure_request = models.ForeignKey(CustomerAccountClosureRequest, on_delete=CASCADE, related_name='anonymization_logs')

    TABLE_CHOICES = [
        ('booking_request', 'Booking'),
        ('master_earning', 'Master earning'),
        ('loyalty_event', 'Loyalty event'),
        ('customer_feedback', 'Review'),
        ('master_admin_message', 'Master-admin message'),
        ('refund_dispute', 'Refund dispute'),
        ('consent_log', 'Consent log'),
        ('audit_log', 'Audit log'),
    ]
    table_name = models.CharField(max_length=64, choices=TABLE_CHOICES)
    rows_anonymized_count = models.IntegerField()
    anonymized_at = models.DateTimeField(auto_now_add=True)
```

### 13.5 `BotUser` additions

```python
# Add to existing apps.identity.BotUser
is_soft_deleted = models.BooleanField(default=False)
soft_deleted_at = models.DateTimeField(null=True, blank=True)
is_hard_deleting = models.BooleanField(default=False)
# True during cooling-off period
deletion_completed_at = models.DateTimeField(null=True, blank=True)
# Set after hard-delete completion; row retained briefly for audit before being purged
```

---

## 14. API contracts

### 14.1 Customer endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/customer/data/summary` | View my data §3.1 |
| GET | `/api/v1/customer/data/section/<section>` | Per-section detail §3.2 |
| POST | `/api/v1/customer/data/export` | Request export §4.1 |
| GET | `/api/v1/customer/data/export/<id>` | Status |
| GET | `/api/v1/customer/data/export/<id>/download` | Download file (7d window) |
| POST | `/api/v1/customer/data/rectification` | Submit rectification §5 |
| GET | `/api/v1/customer/data/rectification/<id>` | Track status |
| POST | `/api/v1/customer/data/soft-delete` | §6.1 |
| POST | `/api/v1/customer/data/reactivate` | §6.3 / §7.4 — cancel closure during cooling-off |
| POST | `/api/v1/customer/data/hard-delete` | §7.1 (requires confirmation token) |
| GET | `/api/v1/customer/data/blockers` | Check pre-deletion blockers §11 |
| GET | `/api/v1/customer/data/closure-request/<id>` | Track status |

### 14.2 Admin endpoints (limited)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/customer-rectifications/queue` | Pending rectification requests |
| POST | `/api/v1/admin/customer-rectifications/<id>/respond` | Admin response |
| GET | `/api/v1/admin/customer-closure-requests` | View tenant's closure requests (counts only; no PII details) |

### 14.3 Founder endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/customer-closures/pending` | Closure requests awaiting founder approval |
| POST | `/api/v1/founder/customer-closures/<id>/approve` | Approve deletion |
| POST | `/api/v1/founder/customer-closures/<id>/request-clarification` | Request more info (delays) |
| POST | `/api/v1/founder/customer-closures/<id>/legal-hold` | Apply legal hold |
| POST | `/api/v1/founder/customer-closures/<id>/release-legal-hold` | Release hold |
| POST | `/api/v1/founder/customer-closures/<id>/reject` | Reject (rare) |

### 14.4 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/closure/scan-cooling-off-expiry` | Cron daily |
| POST | `/internal/closure/<id>/execute-deletion` | Cron-triggered after founder approve |
| POST | `/internal/export/<id>/expire` | Cron — 7d cleanup |
| POST | `/internal/closure/scan-day-25-reminder` | Cron — 5-day-before Bot DM |

### 14.5 Sample request: hard-delete

POST `/api/v1/customer/data/hard-delete`:

```json
{
  "tenant_scope": "this_tenant",  // or "all_tenants"
  "skip_cooling_off": false,
  "confirmation_text": "УДАЛИТЬ",
  "ack_blockers_resolved": true
}
```

Server-side checks blockers §11 → if any open: 409 with blocker list. Otherwise creates `CustomerAccountClosureRequest` + queues founder approval.

Response 201:
```json
{
  "request_id": "uuid",
  "status": "cooling_off",
  "cooling_off_ends_at": "...",
  "next_step": "Wait 30 days OR cancel anytime via /reactivate"
}
```

---

## 15. Events emitted

Add to [`event-taxonomy.md`](./event-taxonomy.md) `3.16 customer privacy domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| View own data | NEW: `customer.data.viewed` | section |
| Export requested | NEW: `customer.data.export_requested` | format |
| Export ready | NEW: `customer.data.export_ready` | size_bytes |
| Export downloaded | NEW: `customer.data.export_downloaded` | |
| Rectification submitted | NEW: `customer.data.rectification_submitted` | |
| Rectification resolved | NEW: `customer.data.rectification_resolved` | action |
| Soft-delete initiated | NEW: `customer.account.soft_deleted` | |
| Account reactivated | NEW: `customer.account.reactivated` | days_in_pause |
| Hard-delete requested | NEW: `customer.account.hard_delete_requested` | scope, immediate |
| Founder approved deletion | NEW: `customer.account.founder_approved_deletion` | |
| Founder legal hold | NEW: `customer.account.legal_hold_applied` | |
| Deletion executed | NEW: `customer.account.deletion_executed` | |

12 NEW events §15.

---

## 16. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Hide deletion menu deep | Discoverability §2.1 | Profile → «Мои данные» one tap |
| «Wait! Special offer to stay» | Dark pattern §2.2 | No retention bribes |
| Confusing pause vs delete | Clarity §2.13 | Distinct flows / labels |
| Auto-re-enable after pause | Sneaking back | Customer reactivates explicitly §6.3 |
| Loss-aversion framing «лишитесь 250 баллов!» | Manipulative | Informative §7.1 with «можно использовать сначала» option |
| «Are you really sure?» 5 nested confirmations | Friction §2.2 | Type «УДАЛИТЬ» = one explicit confirm |
| Export contains other customers' data | Privacy | Customer's data only + initials for others §4.4 |
| Anonymized booking shows customer name in master view | Bypasses anonymization | Hard anonymize in display layer too |
| Master sees customer's wellness data even after deletion (via cache) | Privacy hierarchy | Hard delete + cache invalidation |
| Cross-tenant accidental cascade | Strict boundary §2.11 | Per-tenant only |
| Customer's reviews scrubbed on deletion | Data integrity §2.7 | Anonymize authorship; review content remains |
| Master loses earnings due to customer deletion | Trust §9.4 | Earnings intact |
| Re-onboarding blocks customer | §2.10 | Always allowed; clean state |
| Auto-extend cooling-off without informing | Sneaking back | Founder-only extension §8.4 + Bot DM notification |
| Founder bypasses audit | Compliance | All actions logged §12.4 |
| Customer reactivates but wellness data lost | Bug, not feature | Reactivation = full restore §6.3 |
| Pre-deletion blockers buried | Friction | Inline shown §7.1 with action CTA per blocker |
| Customer requests rectification but admin doesn't respond | SLA fail | 48h SLA + founder escalation §5.3 |
| AI Bot DM «sad to see you go» | Manipulative §2.12 | Calm, neutral |
| Customer who closed account gets marketing later | Privacy violation | Hard-delete removes from all marketing |
| Export available longer than 7 days | Risk of file sharing / leakage | 7d cap §4.5 |
| Founder review takes > 30 days | Customer waiting | 7-day founder SLA §7.6 |
| Cooling-off extends without customer knowing | Trust violation | Customer informed §8.4 |
| Customer can be «un-deleted» from anonymized records | Audit corruption | Hard-delete is final §7.9 |

---

## 17. Acceptance criteria (engineering checklist)

- [ ] 4 models §13 (Closure, Export, Rectification, AnonymizationLog) + BotUser additions
- [ ] 23 endpoints §14 (12 customer + 3 admin + 6 founder + 4 internal)
- [ ] View my data §3 — all sections show + source attribution
- [ ] Export flow §4 — background job, ZIP + JSON, 7-day window, PII rules
- [ ] Rectification flow §5 — admin queue + 48h SLA
- [ ] Soft-delete §6 — pause + 30d window + reactivation + day-25 reminder
- [ ] Hard-delete §7 — confirmation text + blocker checks + founder approval queue
- [ ] 30-day cooling-off §8 default; immediate opt-out via §7.5 + founder approve
- [ ] Pre-deletion blockers §11 — inline with CTA per blocker
- [ ] Cross-tenant scoping §10 — per-BotUser per-tenant
- [ ] Anonymization on hard-delete §7.7 — booking / earnings / loyalty / reviews / messages
- [ ] Wellness HARD-delete (no anonymized retention) §9.8
- [ ] Loyalty balance forfeit §2.5 + customer informed §7.1
- [ ] AI Bot DM 5 templates §12 with notification-controls integration
- [ ] Audit immutability + retention rules §9 + §13.4
- [ ] Founder review queue §7.6 with all blocker info
- [ ] Day-25 reminder cron §6.4 (bypass notification pause)
- [ ] Export expiration cleanup cron §4.5
- [ ] Cooling-off expiry cron §14.4
- [ ] 12 NEW events §15
- [ ] PII rules: customer-only access; admin / founder limited per §14
- [ ] Tests: view sections / export with PII filter / rectification flow / soft-delete + reactivate / hard-delete with blockers / hard-delete immediate / cooling-off expiry / cross-tenant isolation / wellness hard-delete / loyalty forfeit / anonymization on referenced data / founder approval / legal hold / customer re-onboarding after delete creates new BotUser
- [ ] Anti-patterns §16 avoided

---

## 18. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-CP1** | Default cooling-off period — 30 days correct? | YES MVP. Aligns with GDPR industry norm. | Privacy + Legal | 🟢 |
| **Q-CP2** | Soft-delete max duration — 90 days correct? | YES (3 × 30d extensions). | Policy | 🟢 |
| **Q-CP3** | Customer opt-out immediate hard-delete — founder still required? | YES per §7.5. Anti-fraud + legal hold check. Founder SLA 48h. | Policy + Privacy | 🔴 PRE-DEPLOY |
| **Q-CP4** | Customer pending bookings during soft-delete — keep or cancel? | KEEP. Customer can complete OR cancel before. Don't auto-cancel future bookings. | Policy | 🟡 |
| **Q-CP5** | Open dispute blocker — customer can withdraw to unblock? | YES per §11.1 — close dispute (accept admin offer or withdraw) → unblock. | Policy | 🟢 |
| **Q-CP6** | Day-25 reminder — single Bot DM OR repeat? | Single MVP. Avoids harassment. | UX | 🟢 |
| **Q-CP7** | Reviews authorship anonymization — «бывший клиент» or completely removed? | Anonymize authorship («бывший клиент»); review CONTENT remains for master + aggregate. Per §2.7. | Privacy + Policy | 🟡 |
| **Q-CP8** | Master chat history — customer messages anonymized or hard-deleted? | Replaced with «[удалено]» MVP (master sees they got messages, content scrubbed). Hard-delete Phase 4+ if needed. | Privacy + Policy | 🔴 PRE-DEPLOY |
| **Q-CP9** | 30-day cooling-off default — too long? Survey peers? | 30d MVP. GDPR industry norm. Tune if data shows pattern. | Policy | 🟢 |
| **Q-CP10** | Founder approval required EVERY hard-delete? | YES for legal-hold / anti-fraud check. Async 7-day SLA. Per §2.9. | Policy | 🔴 PRE-DEPLOY |
| **Q-CP11** | Audit log retention — 30 days post hard-delete OR longer? | 30 days post anonymization; entries customer_id null afterward. Per §9.1. | Privacy + Legal | 🔴 PRE-DEPLOY |
| **Q-CP12** | Customer data ownership for export — first-name? full-name? | Customer's own data fully in their export (first + last name + contact). Other parties (masters, other customers) initials only. | Privacy | 🟢 |
| **Q-CP13** | «Что мы НЕ знаем» section content — what to list? | Russia 152-FZ disclosure analog: location GPS, IP, browser fingerprints, ads tracking, etc. Refine per data audit. | Privacy + Compliance | 🟡 |
| **Q-CP14** | ConsentLog retention 7y post hard-delete — confirmed? | YES per Q-CN17 confirmation. Anonymized after hard-delete. | Privacy + Legal | 🔴 PRE-DEPLOY |
| **Q-CP15** | Export background job — async required? | YES — 5-min target generation. SQS/Celery worker. | Eng | 🟡 |
| **Q-CP16** | Export file 7-day window — correct? | YES MVP. Anti-leak risk. | Privacy | 🟢 |
| **Q-CP17** | Multi-tenant customer all-tenants close — parallel or sequential? | Parallel (each tenant's flow runs separately). All require founder approval per tenant. | UX + Eng | 🟢 |
| **Q-CP18** | New booking during cooling-off → cancels closure automatically? | YES — implicit reactivation. Customer informed via Bot DM. Per §11.4. | Policy + UX | 🟡 |
| **Q-CP19** | Customer with active legal hold cannot self-soft-delete? | NO — legal hold prevents only hard-delete. Soft-delete allowed (no data change yet). | Policy + Legal | 🟡 |
| **Q-CP20** | Q-CO5 cross-tenant — customer can request global delete via founder? | YES Phase 3+ — single customer-side action triggers parallel per-tenant requests. Founder reviews each. | UX + Policy | 🟡 |

---

## 19. Cross-document linkage

- [`customer-profile-management-ux.md`](./customer-profile-management-ux.md) — rectification §5.2 routes here
- [`customer-notification-controls-ux.md §8`](./customer-notification-controls-ux.md) — ConsentLog retention §9.6
- [`customer-loyalty-rewards-ux.md Q-CL16`](./customer-loyalty-rewards-ux.md) — loyalty forfeit on hard-delete §2.5
- [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md) — open dispute blocker §11.1
- [`customer-wellness-dashboard-ux.md`](./customer-wellness-dashboard-ux.md) — wellness data fully customer-owned §9.8
- [`core-wellness-profile.md`](./core-wellness-profile.md) — wellness privacy hierarchy §2.15
- [`wellness-input-modules.md §11`](./wellness-input-modules.md) — per-module consent paused during soft-delete §6.2
- [`master-offboarding-handoff.md`](../handoffs/2026-05-19-master-offboarding-handoff.md) — pattern reuse (30d cooling-off, founder approval, audit retention)
- [`attribution-policy.md`](./attribution-policy.md) — anonymization §9.5
- [`single-assistant-identity.md §2.4`](./single-assistant-identity.md) — voice §2.12
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) — SUSPENDED interaction §2.18 / §11.3
- [`event-taxonomy.md §3.16`](./event-taxonomy.md) — 12 NEW events §15
- [`../decisions-log.md`](../decisions-log.md) — Q-CP1..Q-CP20

---

## 20. What this unblocks

- **GDPR + 152-FZ compliance** — customer rights to view / export / rectify / erase fully implemented
- **Customer trust foundation** — clear path to leave salon-relationship with audit
- **Tenant compliance posture** — formal closure flow vs ad-hoc
- **Founder oversight on edge cases** — legal hold, anti-fraud, retention questions
- **Cross-tenant integrity** — per-tenant per-BotUser closure
- **Customer can come back** — re-onboarding works; no platform-side blacklist
- **Data export portability** — customer can take data to another platform if they wish

## 21. What this does NOT unblock

- ❌ Lawyer / proxy data requests (Phase 4+)
- ❌ Government data requests (separate legal process)
- ❌ Cross-tenant aggregation export (privacy boundary)
- ❌ Mass closure (whole-tenant) — separate scope
- ❌ Customer-deceased estate access (Phase 4+)
- ❌ Skip Q-CP3 founder-required immediate hard-delete (pre-deploy)
- ❌ Skip Q-CP8 master chat anonymization policy (pre-deploy)
- ❌ Skip Q-CP10 founder approval requirement (pre-deploy)
- ❌ Skip Q-CP11 + Q-CP14 retention validations (pre-deploy)
- ❌ Anti-fraud ML

---

## 22. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Identity / BotUser backend lead | ☐ | |
| Mini App frontend (Мои данные section + export + soft-delete + hard-delete flow) | ☐ | |
| AI prompt eng (5 Bot DM templates + neutral tone) | ☐ | |
| Privacy / Legal (§9 retention + Q-CP3 / Q-CP10 / Q-CP11 / Q-CP14 / Q-CP8) | ☐ | 🔴 PRE-DEPLOY |
| Founder (Q-CP3 immediate-deletion approval + Q-CP10 always-approve + cooling-off process design) | ☐ | 🔴 PRE-DEPLOY |
| Loyalty steward (§2.5 forfeit + Q-CL16 alignment) | ☐ | |
| Refund-dispute steward (§11.1 blocker + Q-CP5) | ☐ | |
| Notification-controls steward (§9.6 + Q-CN17 ConsentLog) | ☐ | |
| Master-reviews steward (§9.7 + Q-CP7) | ☐ | |
| Wellness modules steward (§9.8 hard-delete) | ☐ | 🔴 PRE-DEPLOY |
| Russia consumer-protection legal (152-FZ alignment + retention periods) | ☐ | 🔴 PRE-DEPLOY |
| Accessibility (WCAG 2.2 AA on all flows) | ☐ | |

## Last verified
2026-05-19 (initial draft, 5 customer actions + 30d cooling-off + founder approval + pre-deletion blockers + cross-tenant boundaries + GDPR/152-FZ retention rules — locked)
