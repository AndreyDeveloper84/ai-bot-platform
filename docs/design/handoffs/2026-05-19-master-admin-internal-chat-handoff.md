# Master ↔ Admin Internal Chat — Engineering Handoff

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Production-blocking — referenced by reviews / time-off / substitution / offboarding / earnings disputes
**Reads:** [`../policies/ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md), [`../policies/tenant-as-provider-model.md`](../policies/tenant-as-provider-model.md), [`./2026-05-19-master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md), [`./2026-05-19-master-reviews-feedback-handoff.md`](./2026-05-19-master-reviews-feedback-handoff.md), [`./2026-05-19-master-time-off-handoff.md`](./2026-05-19-master-time-off-handoff.md), [`./2026-05-19-master-substitution-handoff.md`](./2026-05-19-master-substitution-handoff.md), [`./2026-05-19-master-offboarding-handoff.md`](./2026-05-19-master-offboarding-handoff.md), [`../handoffs/2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md), [`../policies/master-conversational-templates.md`](../policies/master-conversational-templates.md) (r2), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md)

> Master needs to ask salon owner: «can I switch Thursdays off?», «I disagree with this earnings number», «I want to talk about my schedule», «I'm thinking about leaving». These conversations happen TODAY via WhatsApp/Telegram outside the platform — losing audit, losing context, losing professionalism. This handoff specifies the formal internal-admin-chat channel.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](../policies/ayla-identity-and-brand.md) memory 2026-05-19: master ↔ admin internal chat is **Ayla Pro internal** per [`tenant-as-provider-model §5`](../policies/tenant-as-provider-model.md) — humans communicating in tenant operational tone, **NOT Ayla persona**. Ayla doesn't relay these messages or have voice here. Used for emergencies escalating to founder per [`ayla-emergency-fallback-policy §3`](../policies/ayla-emergency-fallback-policy.md). Deprecated `single-assistant-identity.md` references removed (no longer applicable to this internal channel).

---

## 0. Why this exists

### 0.1 The integration gap

Multiple master-UX docs reference «обсудить со студией» / «написать {{salon_owner}}» — but there's no actual channel spec:
- [`master-reviews-feedback §5.5`](./2026-05-19-master-reviews-feedback-handoff.md) — «Обсудить со студией» on review
- [`master-earnings §6.4`](./2026-05-19-master-earnings-handoff.md) — «Написать {{salon_owner}}» on commission rules
- [`master-time-off §5.2`](./2026-05-19-master-time-off-handoff.md) — admin «Обсудить с Леной» on leave request
- [`master-substitution §4.5`](./2026-05-19-master-substitution-handoff.md) — pattern change mid-leave
- [`master-offboarding §4.2`](./2026-05-19-master-offboarding-handoff.md) — «Обсудить со студией» on notice

ALL these need the same channel. This doc defines it.

### 0.2 The gap from customer conversations

Per [`master-mobile-handoff §5 Screen M5`](./2026-05-18-master-mobile-handoff.md): master sees their CUSTOMER conversations. But master ↔ admin is NOT a customer conversation:
- Not customer-facing
- Different threading model (one master ↔ one admin team, NOT per-customer)
- Different privacy (admin team sees, NOT individual admin)
- Different AI involvement (none — AI is silent here)
- Different retention (longer, business-critical)

### 0.3 The promise

Single source for:
- Internal-admin-chat data model + threading §6
- Master-side UI §3 (new tab «Со студией» in Mini App)
- Admin-side UI §4 (master support queue)
- Topic-based threading §5 (per topic: earnings dispute, leave, review concern, general)
- AI silence policy §7
- Privacy/retention §8
- 3 NEW models, 10 endpoints, 6 events

---

## 1. Scope

### IN
- New Mini App tab «Со студией» for master
- Admin Mini App «Чаты с мастерами» tab
- Threading: master ↔ admin-team (not per-individual-admin)
- Topic types §5 (earnings_dispute / leave_request / review_concern / schedule_change / offboarding_discussion / general / other_master_complaint)
- Cross-doc «открыть обсуждение» buttons → creates thread linked to artifact
- Notifications via Bot DM + Mini App badge
- Admin team distribution (any admin can respond; assigned-admin label optional)
- File attachments (image / pdf / voice memo)
- Read receipts
- Search within thread
- Archive after 90 days inactivity
- Founder access for escalation
- 6 NEW events for event-taxonomy

### OUT
- Master ↔ master direct chat (out of scope; salons use external)
- Master ↔ customer direct chat outside booking conversations (already covered in customer conversations module)
- Group chats (master + other master + admin) — Phase 4+
- Video calls / VoIP — out of scope
- Public master forum / community — out of scope
- Cross-tenant master groups — Phase 4+
- Customer-style chatbot in internal channel — anti-pattern §2.6
- Auto-translate (regional language assumed single)
- Mass announcement from admin to multiple masters at once — separate `master-broadcast-policy.md` future
- Tenant-shutdown impact — separate scope
- HR records / formal grievance system — separate from this; this is operational chat

---

## 2. Strategic constraints — non-negotiable

### 2.1 NOT customer conversations
Master sees this clearly as «Со студией» — separate tab. NEVER mixed with customer threads.

### 2.2 NOT AI-mediated
AI is silent here. Per [`single-assistant-identity §2.4`](../policies/single-assistant-identity.md): single-assistant identity is for CUSTOMER touchpoints. Internal admin-master communication = professional, direct.

If master DM's «помощник» about an admin topic, AI redirects: «Это лучше обсудить со студией. Открываю чат?» — opens thread.

### 2.3 Topic-linked threads
Per §5 — threads carry context. A thread about earnings dispute references the specific `EarningDispute` row. Closes when artifact resolves.

### 2.4 Admin team responds
Per Q-IAC1: any admin in the tenant can respond. Master sees admin role label, not individual name unless admin explicitly identifies. Reduces master's «I want only X» dependency.

### 2.5 Audit immutable
Every message audit-retained. Master and admin both see full thread history. Founder + Q12-δ cohort review can access for sensitive cases.

### 2.6 No AI «coaching» of conversations
- ❌ AI suggesting how admin should respond
- ❌ AI auto-summarizing master's complaint to admin
- ❌ AI sentiment-scoring
- ✅ Pure human-to-human, AI only for topic-tag suggestion §3.2

### 2.7 Privacy: master sees admin team, not individual identity (default)
Master sees «Студия» (or salon name) as the sender by default. Admin can choose to sign as themselves («— Натали»). Reduces personal targeting.

### 2.8 Retention: 90d active, then archive
Active threads visible by default. After 90d inactive, archived but searchable. Hard-delete only via founder + compliance request.

### 2.9 Founder access for escalation
For sensitive topics (per Q-IAC8): masters can «escalate to founder» when admin response is unsatisfactory or topic is sensitive (e.g., harassment, hostile termination preview). Audit captured.

### 2.10 No anonymous escalation
Per Q-IAC9: founder escalation always shows master's identity. Anonymous reporting requires separate channel (out of scope MVP). Reduces noise + game-the-system risk.

### 2.11 No customer mention by name
Master can reference customers by booking_id (linked artifact) or initials. NEVER full customer name in this channel. Cross-domain privacy enforcement.

### 2.12 Multi-tenant master separated per tenant
Master at tenants A + B has separate threads with each. No cross-pollination.

### 2.13 Notification respect
Quiet hours per [`master-time-off §5.7`](./2026-05-19-master-time-off-handoff.md): no notifications 21:00-09:00 local time. Mini App always shows badge silently.

---

## 3. Master side

### 3.1 «Со студией» tab in Mini App

New top-level tab after «Чаты» (which is customer convos). Bottom nav becomes:

```
[Со студией] [Чаты] [Расписание] [Доход] [Профиль]
```

OR if too crowded, «Со студией» nested as section in «Профиль» (Q-IAC2).

MVP: top-level tab if salon has ≥ 1 active thread; nested if 0.

### 3.2 Tab home

```
┌────────────────────────────────────────┐
│ 🏛 Со студией                            │
├────────────────────────────────────────┤
│ ── Открытые ──                           │
│                                        │
│ 💰 Спор по доходу за маникюр 17 мая      │
│ Студия ответила 3 часа назад              │
│ [Открыть]                                │
│                                        │
│ 📋 Отзыв от Олег П. — обсуждаем          │
│ Вы ответили вчера                        │
│ [Открыть]                                │
│                                        │
│ ── Решённые (за месяц) ──                │
│                                        │
│ 🛌 Отпуск 10-24 июня — согласовано        │
│ Завершено 5 дней назад                    │
│ [Открыть]                                │
│                                        │
│ ── Что-то новое? ──                      │
│ [✏ Написать студии]                       │
└────────────────────────────────────────┘
```

### 3.3 «Написать студии» — start new thread

```
┌────────────────────────────────────────┐
│ ← Что обсудим?                           │
├────────────────────────────────────────┤
│ Выберите тему — это поможет быстрее     │
│ ответить:                                │
│                                        │
│ ⦿ 💰 Что-то по доходу или комиссии       │
│ ◯ 🛌 Хочу обсудить выходные или график  │
│ ◯ 📋 Вопрос по отзыву                   │
│ ◯ 🚪 Думаю об уходе из студии            │
│ ◯ 👥 Хочу что-то рассказать про          │
│      другого мастера                      │
│ ◯ ❓ Что-то другое                       │
│                                        │
│ [Дальше]                                 │
└────────────────────────────────────────┘
```

After topic selection → write field:

```
┌────────────────────────────────────────┐
│ ← Хочу обсудить выходные                 │
├────────────────────────────────────────┤
│ [_____________________________]        │
│ [_____________________________]        │
│ [_____________________________]        │
│                                        │
│ [📎]   [Отправить]                       │
└────────────────────────────────────────┘
```

### 3.4 Thread view

```
┌────────────────────────────────────────┐
│ ← 💰 Спор по доходу 17 мая               │
├────────────────────────────────────────┤
│ Тема: спор по доходу                    │
│ Связано: запись 17 мая (маникюр Мария И.)│
│                                        │
│ ── Вы, 19 мая 10:00 ──                  │
│ По окрашиванию у меня ставка 40%,        │
│ а в приложении применилась дефолтная.   │
│ Можем посмотреть?                        │
│                                        │
│ ── Студия, 19 мая 13:30 ──              │
│ Привет! Извини, я неверно применила      │
│ старую ставку. Поправили — 1 280 ₽.     │
│ — Натали                                 │
│                                        │
│ ── Вы, 19 мая 14:00 ──                  │
│ Спасибо! Принимаю.                        │
│                                        │
│ ── Студия, 19 мая 14:01 ──               │
│ ✓ Спор закрыт                            │
│                                        │
│ ── Закрыто ──                            │
│ Тема исчерпана. Если что-то ещё —       │
│ откройте новую.                          │
│                                        │
│ [📎]   [Отправить]   (нельзя — закрыто) │
└────────────────────────────────────────┘
```

### 3.5 Reopening / new thread on same topic

Master can open new thread referencing closed one. UI shows «продолжение разговора от 19 мая» metadata.

### 3.6 «Escalate to founder» button

In threads with `topic_type IN ('offboarding_discussion', 'other_master_complaint', 'general')` and master perceives admin response inadequate:

```
[Эскалировать к основателю]
```

Tap → confirmation:

```
Эскалация — это серьёзный шаг. {{founder}} увидит весь разговор + ваше
сообщение. Будет реагировать в течение 7 рабочих дней.

Эскалируем?

[Да, эскалирую]   [Нет, обсудим со студией ещё]
```

After founder reviews + responds, thread continues with founder added.

### 3.7 Attachments

- Image (JPEG/PNG ≤ 10MB)
- PDF (≤ 5MB)
- Voice memo (≤ 60s)
- Max 3 attachments per message

Note: customer photos NEVER allowed (customer privacy). Filter by EXIF / OCR / pre-upload check.

### 3.8 Search

In-thread + cross-thread (own only) search by keyword. Phase 2 MVP basic; Phase 3+ semantic.

### 3.9 Read receipts

«Прочитано {{salon_owner}} 14:30» style. Subtle, not interrupting.

---

## 4. Admin side

### 4.1 «Чаты с мастерами» tab in admin Mini App

```
┌────────────────────────────────────────┐
│ 💬 Чаты с мастерами (5)                  │
├────────────────────────────────────────┤
│ ── Требуют ответа ──                    │
│                                        │
│ Анна 🛌 — обсудить выходные              │
│ Прислала: 2 часа назад                   │
│ SLA: 22ч из 24                          │
│ [Открыть]                                │
│                                        │
│ Лена 💰 — спор по доходу                │
│ Прислала: вчера                          │
│ SLA: просрочено на 6ч                    │
│ ⚠ Приоритет                              │
│ [Открыть]                                │
│                                        │
│ ── Идёт обсуждение ──                    │
│                                        │
│ Марина 📋 — вопрос по отзыву             │
│ Вы ответили: 1 день назад                │
│ [Открыть]                                │
│                                        │
│ ── Эскалированные ──                    │
│                                        │
│ ⚠ Олеся 🚪 — эскалировала к основателю   │
│ Эскалирована 3 дня назад                 │
│ [Посмотреть]                             │
└────────────────────────────────────────┘
```

### 4.2 Thread view (admin)

Same as master's view §3.4 but:
- Admin sees master's full identity
- Admin sees linked artifact (booking, dispute, leave-request)
- Action buttons depending on topic: «закрыть спор», «согласовать отпуск», etc. that resolve linked artifact AND close thread

### 4.3 Multi-admin assignment

```
┌────────────────────────────────────────┐
│ Кто отвечает?                            │
│ ⦿ Любой админ (общий)                   │
│ ◯ Закрепить за: [Натали ▾]              │
│ ◯ Я возьму этот разговор                │
└────────────────────────────────────────┘
```

If assigned, other admins see «закреплено за Натали» status.

### 4.4 Topic-tag override

If master's topic selection is wrong (e.g., chose «выходные» but message is really about earnings), admin can re-tag. Audit captures.

### 4.5 Quick-templates for admin

Admin sees 3-5 quick templates per topic_type («извини, поправила», «понятно, сейчас посмотрю», etc.). Plain text, not AI-generated. Admin can use as starter.

### 4.6 «Close thread» action

Per topic type, closing requires resolution:
- earnings_dispute → linked `EarningDispute` must be resolved
- leave_request → linked `MasterLeaveRequest` must be approved/rejected
- review_concern → admin must mark «addressed»
- general → admin can close manually

### 4.7 Bulk inbox view

Admin sees all open threads across masters. Filter by topic_type, severity, SLA-risk.

### 4.8 Auto-close inactive

If thread has no activity for 14 days AND no open linked artifact → auto-close with audit «closed: no activity».

---

## 5. Topic taxonomy

### 5.1 Topics

| Code | Display | Default SLA | Linked artifact |
|---|---|---|---|
| `earnings_dispute` | 💰 По доходу или комиссии | 48h | `EarningDispute` |
| `leave_request` | 🛌 Выходные или график | 24h | `MasterLeaveRequest` |
| `review_concern` | 📋 По отзыву | 48h | `CustomerFeedback` |
| `schedule_change` | 📅 Изменить расписание | 48h | optional |
| `offboarding_discussion` | 🚪 Думаю об уходе | 48h | optional `MasterOffboarding` |
| `other_master_complaint` | 👥 Про другого мастера | 72h | optional |
| `general` | ❓ Что-то другое | 72h | none |

### 5.2 Topic transitions

Topic can be re-tagged mid-thread (admin §4.4). Audit captures.

### 5.3 Cross-doc «открыть обсуждение» buttons

These doc spots create pre-tagged threads:

| Source | Pre-tag | Pre-link |
|---|---|---|
| Review detail «Обсудить со студией» (master-reviews §5.3) | `review_concern` | review_id |
| Earnings rules «Написать студии» (master-earnings §6.4) | `general` | none |
| Earnings dispute response «не согласна» (master-earnings §9.5) | `earnings_dispute` | dispute_id |
| Leave admin «Обсудить с мастером» | `leave_request` | leave_id |
| Substitution pattern change (substitution §4.5) | `leave_request` | substitution_id |
| Offboarding «Обсудить со студией» | `offboarding_discussion` | offboarding_id |

### 5.4 Sensitive topic auto-flags

`other_master_complaint` + `offboarding_discussion` carry «sensitive» flag — extra audit, longer retention §8.4.

### 5.5 «Founder escalation» topic
When master escalates §3.6, thread tagged `_escalated_to_founder` (boolean), founder added as participant. Original tag preserved.

---

## 6. Data models

### 6.1 `MasterAdminThread`

```python
class MasterAdminThread(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='master_admin_threads')
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='admin_threads')

    TOPIC_CHOICES = [
        ('earnings_dispute', 'Earnings dispute'),
        ('leave_request', 'Leave request'),
        ('review_concern', 'Review concern'),
        ('schedule_change', 'Schedule change'),
        ('offboarding_discussion', 'Offboarding discussion'),
        ('other_master_complaint', 'Complaint about other master'),
        ('general', 'General'),
    ]
    topic = models.CharField(max_length=32, choices=TOPIC_CHOICES)

    # Linked artifact (one of)
    linked_earning_dispute = models.ForeignKey('earnings.EarningDispute', null=True, blank=True, on_delete=SET_NULL, related_name='admin_threads')
    linked_leave_request = models.ForeignKey('schedule.MasterLeaveRequest', null=True, blank=True, on_delete=SET_NULL, related_name='admin_threads')
    linked_review = models.ForeignKey('reviews.CustomerFeedback', null=True, blank=True, on_delete=SET_NULL, related_name='admin_threads')
    linked_offboarding = models.ForeignKey('staff.MasterOffboarding', null=True, blank=True, on_delete=SET_NULL, related_name='admin_threads')
    linked_substitution = models.ForeignKey('staff.MasterSubstitution', null=True, blank=True, on_delete=SET_NULL, related_name='admin_threads')

    subject = models.CharField(max_length=200, blank=True, default='')
    # Auto-generated from topic + first message OR admin can edit

    STATUS_CHOICES = [
        ('open', 'Open — awaiting response'),
        ('admin_responded', 'Admin responded — awaiting master'),
        ('master_responded', 'Master responded — awaiting admin'),
        ('active_discussion', 'Active discussion'),
        ('resolved', 'Resolved'),
        ('auto_closed_inactive', 'Auto-closed (14d no activity)'),
        ('escalated_to_founder', 'Escalated to founder'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='open')

    assigned_admin = models.ForeignKey('auth.User', null=True, blank=True, on_delete=SET_NULL, related_name='+')
    # Optional pinning

    is_sensitive = models.BooleanField(default=False)
    # other_master_complaint, offboarding_discussion auto-true

    founder_added_at = models.DateTimeField(null=True, blank=True)

    sla_due_at = models.DateTimeField()
    # First-response SLA: depends on topic_type

    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            Index(fields=['tenant', 'status', '-last_activity_at']),
            Index(fields=['master', '-last_activity_at']),
            Index(fields=['sla_due_at']),
        ]
        constraints = [
            CheckConstraint(
                check=Q(linked_earning_dispute__isnull=True) | (Q(topic='earnings_dispute')),
                name='ck_linked_earning_dispute_matches_topic',
            ),
            # Similar for other linkages
        ]
```

### 6.2 `MasterAdminMessage`

```python
class MasterAdminMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(MasterAdminThread, on_delete=CASCADE, related_name='messages')

    SENDER_ROLE_CHOICES = [
        ('master', 'Master'),
        ('admin', 'Admin'),
        ('founder', 'Founder'),
        ('system', 'System (auto-close, etc.)'),
    ]
    sender_role = models.CharField(max_length=16, choices=SENDER_ROLE_CHOICES)
    sender_user = models.ForeignKey('auth.User', null=True, blank=True, on_delete=SET_NULL, related_name='+')
    # null for system messages

    sender_admin_signed_name = models.CharField(max_length=64, blank=True, default='')
    # If admin explicitly signed «— Натали», stored here. Otherwise generic «Студия»

    body = models.TextField(max_length=4000)

    read_by_master_at = models.DateTimeField(null=True, blank=True)
    read_by_admin_at = models.DateTimeField(null=True, blank=True)
    read_by_founder_at = models.DateTimeField(null=True, blank=True)

    is_system_message = models.BooleanField(default=False)
    # Auto-messages like «закрыт автоматически»

    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['thread', '-sent_at']),
        ]
```

### 6.3 `MasterAdminMessageAttachment`

```python
class MasterAdminMessageAttachment(models.Model):
    message = models.ForeignKey(MasterAdminMessage, on_delete=CASCADE, related_name='attachments')

    TYPE_CHOICES = [
        ('image', 'Image'),
        ('pdf', 'PDF'),
        ('voice_memo', 'Voice memo'),
    ]
    attachment_type = models.CharField(max_length=16, choices=TYPE_CHOICES)

    file = models.FileField(upload_to='master_admin_chat/')
    file_size_bytes = models.IntegerField()
    file_sha256 = models.CharField(max_length=64)

    # Voice memo specific
    duration_seconds = models.IntegerField(null=True, blank=True)

    pii_scan_status = models.CharField(max_length=32, default='pending')
    # 'pending', 'clean', 'flagged', 'blocked'
    # Per §3.7 customer photo filter

    uploaded_at = models.DateTimeField(auto_now_add=True)
```

---

## 7. AI silence policy

### 7.1 No AI participation in threads
Per §2.2 / §2.6 — AI does NOT compose, summarize, suggest, or otherwise participate in internal-admin-chat messages.

### 7.2 AI redirect from customer-AI when master DMs about admin topic

If master DMs `/api/v1/master/ai-chat` (bot DM) with content matching admin-topic patterns («хочу обсудить зарплату», «можно мне выходной»):

```
Это про студию, не про клиентов — лучше написать админу. Открыть чат?
[Открыть]   [Нет, спасибо]
```

«Открыть» → creates new `MasterAdminThread` with topic auto-selected based on detection + pre-fills first message draft if obvious.

### 7.3 Topic detection (assist, not generate)

AI does light topic classification on first master message in new thread (helps with §3.3 topic suggestion). Customer/master text NEVER summarized or rewritten by AI in this channel.

### 7.4 «Что подсказать студии?» does NOT exist

There is NO «AI helps master phrase their request to admin» feature. Master writes as themselves. Same for admin direction.

---

## 8. Privacy + retention

### 8.1 Customer PII in messages
- Per §2.11: customer initials + booking_id only
- Pre-send check: if master types full customer name, prompt «использовать инициалы?»
- Hard block on customer phone / email patterns

### 8.2 Master's wellness data
Out of scope here. Master writes about own work, not their wellness.

### 8.3 Founder access
- For sensitive threads §5.4 / §5.5: founder has read access
- For non-sensitive: founder cannot read (privacy)
- Cross-tenant founder dashboard for sensitive metrics only Phase 3+

### 8.4 Retention
- Active: indefinite until resolution
- Resolved: 90 days visible, then archive
- Archived: searchable on demand, 7 years total
- Sensitive (§5.4): 7 years total minimum
- Founder-escalated: permanent until founder explicit deletion (post-resolution)

### 8.5 Data export
- Master can export own threads §7.5 of master-offboarding via Settings → Data export
- Admin can export per-tenant threads for compliance (admin-only)
- NEVER share thread content cross-tenant

### 8.6 Q12-δ cohort review access
Founder + Q12-δ cohort reviewers can view sensitive threads for cohort billing-attribution disputes. Audit captures.

---

## 9. API contracts

### 9.1 Master endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/master/admin-threads` | List own threads |
| GET | `/api/v1/master/admin-threads/<id>` | Thread detail with messages |
| POST | `/api/v1/master/admin-threads` | Start new thread |
| POST | `/api/v1/master/admin-threads/<id>/messages` | Send message |
| POST | `/api/v1/master/admin-threads/<id>/attachments` | Upload attachment |
| POST | `/api/v1/master/admin-threads/<id>/escalate-to-founder` | §3.6 |
| POST | `/api/v1/master/admin-threads/<id>/mark-read` | Read receipt |

### 9.2 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/master-threads` | List tenant's open threads |
| GET | `/api/v1/admin/master-threads/<id>` | Detail |
| POST | `/api/v1/admin/master-threads/<id>/messages` | Send |
| POST | `/api/v1/admin/master-threads/<id>/assign` | Pin admin §4.3 |
| POST | `/api/v1/admin/master-threads/<id>/re-tag` | Change topic §4.4 |
| POST | `/api/v1/admin/master-threads/<id>/close` | Close §4.6 |

### 9.3 Founder endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/master-threads/escalated` | Cross-tenant escalated |
| POST | `/api/v1/founder/master-threads/<id>/messages` | Founder participates |

### 9.4 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/master-threads/scan-auto-close` | 14d inactivity scan |
| POST | `/internal/master-threads/scan-sla-breach` | SLA breach alerts |

### 9.5 Validation: POST `/master/admin-threads`

```json
{
  "topic": "earnings_dispute",
  "linked_artifact_type": "earning_dispute",
  "linked_artifact_id": "uuid",
  "initial_message": "По окрашиванию у меня ставка 40%..."
}
```

- Master must own the linked artifact (e.g., dispute must be master's own)
- One open thread per (master, linked_artifact_id) at a time

---

## 10. Events emitted

Add to [`event-taxonomy.md`](../policies/event-taxonomy.md) `3.12 master-admin-chat domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| Thread created | NEW: `admin_chat.thread_created` | topic, linked_artifact_type |
| Message sent | NEW: `admin_chat.message_sent` | sender_role, message_id (NOT body content) |
| Thread resolved | NEW: `admin_chat.thread_resolved` | resolution_type |
| Auto-closed | NEW: `admin_chat.auto_closed_inactive` | |
| Escalated to founder | NEW: `admin_chat.escalated_to_founder` | reason_class |
| SLA breached | NEW: `admin_chat.sla_breached` | topic, age_hours |

6 NEW events §10.

---

## 11. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| AI participates in admin-master chat | §2.2 voice violation | AI silent except topic-detect §7.3 |
| Master ↔ customer thread shows in same place as admin chat | Confusion | Separate tab §3.1 |
| Admin can read other masters' threads | Privacy creep | Same tenant only; per-master scope |
| Customer name in thread body | PII §2.11 | Initials |
| Customer photo in attachment | PII | Filter on upload §3.7 |
| Anonymous escalation | Game-theory abuse §2.10 | Identified always |
| AI summarizes long thread for admin | Distorts | Admin reads in full |
| AI sentiment-scores master | Manipulative | NEVER |
| Auto-close while master expecting response | Bad CX | 14d MVP threshold §4.8 |
| Cross-tenant founder default access | Privacy | Sensitive only §8.3 |
| Master broadcast to multiple admins | Inbox overload | One admin team queue §4.1 |
| Mass message admin → many masters at once | Out of scope | Separate broadcast policy future |
| AI «predicts» master is about to quit and warns admin | Surveillance | NEVER |
| Reply templates auto-applied without admin click | Robotic | Manual selection §4.5 |
| Read receipts auto-mark «read» on thread open w/o explicit view | Misleading | Per-message on visibility >2s |
| Thread blocks while founder is reviewing escalation | Stalls | Thread continues; founder adds |
| Attach file > 10MB | Bandwidth | Reject pre-upload |
| Admin re-tags without audit | History loss | Audit captures §4.4 |
| Auto-create thread when AI senses tension in customer convo | Surveillance | Master initiates |
| Customer ever sees this thread | Privacy boundary | Customer-side has no awareness |

---

## 12. Acceptance criteria (engineering checklist)

- [ ] 3 models §6 + migration
- [ ] 13 endpoints across 4 roles §9
- [ ] 7 topic types §5 with SLA matrix
- [ ] Linked artifact constraints §6.1 (one of, not both)
- [ ] Master Mini App «Со студией» tab §3
- [ ] Admin Mini App «Чаты с мастерами» tab §4
- [ ] Topic-tagged thread creation from cross-doc «обсудить» buttons §5.3
- [ ] AI silent in this channel §7
- [ ] AI topic-detection on first message only (no generation)
- [ ] Pre-send PII check (customer name pattern) §8.1
- [ ] Attachment PII scan §3.7 (customer photo filter)
- [ ] Read receipts §3.9
- [ ] Search in own threads §3.8
- [ ] Founder escalation §3.6
- [ ] Sensitive topic auto-flag §5.4
- [ ] Auto-close 14d inactivity §4.8
- [ ] SLA breach scanner + alerts §11
- [ ] Admin team multi-admin §4.3 (any admin can respond; optional pinning)
- [ ] Admin sign-as-self «— Натали» optional §2.7 / §6.2
- [ ] Thread close with artifact-resolution requirement §4.6
- [ ] Quiet hours per master-time-off §5.7 alignment
- [ ] 6 events §10
- [ ] PII rules enforced
- [ ] Cross-tenant 403 (master at tenant A cannot view thread at tenant B)
- [ ] Tests: thread creation per topic / message + attach / SLA breach / escalation / auto-close / sensitive flag / cross-master 403 / cross-tenant 403 / pre-send PII check / customer-photo block
- [ ] Anti-pattern review §11

---

## 13. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-IAC1** | Multi-admin tenant — any admin responds or queue assigned? | Any admin can respond; optional pinning §4.3. Phase 3+ assignment rules per topic possible. | UX + Eng | 🟢 |
| **Q-IAC2** | Master Mini App position — top-level tab or nested in Profile? | Top-level if ≥ 1 active thread; nested otherwise §3.1. Phase 3 could adapt. | UX | 🟢 |
| **Q-IAC3** | Admin can «summon» master via this channel (admin-initiated thread)? | YES — admin can start thread to master proactively (e.g., commission rate change notification). Master sees as if any thread. | Policy + UX | 🟡 |
| **Q-IAC4** | Voice memo transcription auto-shown? | NO MVP. Admin/master listens. Phase 3+ optional auto-transcribe (privacy + accuracy concerns). | UX + Eng | 🟢 |
| **Q-IAC5** | SLA breach — admin-only notification or master too? | Admin gets escalation alert; master sees no automatic «SLA breached» message (anti-shame on admin side). Master sees natural «no response in N hours» if they check. | Policy | 🟡 |
| **Q-IAC6** | Read receipts opt-out for admin (so master can't tell admin saw)? | NO MVP. Symmetric read receipts both sides. Phase 4+ if admin needs «thinking time» can be added. | UX | 🟢 |
| **Q-IAC7** | Master can «delete» own message after sending? | Edit window 5 min after send — yes (typos). After 5 min — no edit; can send retraction message. Audit always preserved. | Privacy + UX | 🟡 |
| **Q-IAC8** | Founder escalation — what triggers founder VISIBILITY (not just escalation button)? | (a) Master tap «escalate» §3.6 OR (b) sensitive topic with no admin response within SLA × 2. (b) auto-elevates audit-only; founder can opt to participate. | Policy | 🔴 PRE-DEPLOY |
| **Q-IAC9** | Anonymous reporting channel — separate or built-in? | Separate future. MVP identified-only per §2.10. Phase 4+ when scale demands. | Policy + Privacy | 🟡 |
| **Q-IAC10** | Cross-tenant master sees admin-team identity per tenant? | YES — at tenant A sees «Студия А» (or salon name); at tenant B sees «Студия Б». No cross-tenant context. | Privacy + UX | 🟢 |
| **Q-IAC11** | Thread carries to substitute or co-master if original admin offboards? | If linked artifact is master's: stays with master. If admin offboards, thread reassigns to admin team. New admin sees thread context. | Policy | 🟡 |
| **Q-IAC12** | Admin can broadcast «announcement» to multiple masters? | Out of scope per §1; separate `master-broadcast-policy.md` future. | PM | 🟢 |
| **Q-IAC13** | When thread is closed (resolved), master can re-open? | NO — must start new thread referencing closed §3.5. Audit links. | UX | 🟢 |
| **Q-IAC14** | Sensitive `other_master_complaint` — auto-add founder for review? | NO — founder is informed (counter ticks); reviews only on demand or master escalation §3.6. Reduces noise. | Privacy + Policy | 🔴 PRE-DEPLOY |
| **Q-IAC15** | Attachments retention — same as thread? | YES — attachments retained with thread per §8.4. Hard-delete on legal request only. | Privacy | 🟢 |
| **Q-IAC16** | Master uses internal chat to dispute booking-conflict (not just earnings)? | YES — `general` topic OK. If linked artifact is BookingConflict, can wire later (Phase 3+). MVP general topic. | Eng | 🟢 |
| **Q-IAC17** | Master at HUMAN_LOCKED conversation with customer that goes wrong — escalates via internal chat? | YES — admin advice route. Topic `review_concern` or `general`. Helps with Q-MR11 dependency from reviews doc. | UX + Eng | 🟡 |
| **Q-IAC18** | Multi-tenant master starts a thread — which tenant? | Master picks tenant from selector (per multi-tenant pattern). Thread tenant-locked. | UX + Eng | 🟢 |
| **Q-IAC19** | Customer's wellness data referenced in thread? | NEVER — customer-only per `core-wellness-profile.md`. Hard block on wellness-related terms. | Privacy | 🔴 PRE-DEPLOY |
| **Q-IAC20** | Thread analytics for tenant admin — overview dashboard? | Phase 3+. MVP just count + SLA stats in admin Mini App. | PM | 🟡 |

---

## 14. Cross-document linkage

- [`master-earnings-handoff.md §6.4/§9.5`](./2026-05-19-master-earnings-handoff.md) — «Написать студии» / dispute discussion entry points
- [`master-reviews-feedback-handoff.md §5.3 Q-MR11`](./2026-05-19-master-reviews-feedback-handoff.md) — UNBLOCKS reviews doc internal-admin-chat dependency
- [`master-time-off-handoff.md §5.2`](./2026-05-19-master-time-off-handoff.md) — admin «Обсудить с {{master}}» entry point
- [`master-substitution-handoff.md §4.5`](./2026-05-19-master-substitution-handoff.md) — pattern change discussion
- [`master-offboarding-handoff.md §4.2`](./2026-05-19-master-offboarding-handoff.md) — notice discussion entry point
- [`master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md) — new tab on bottom nav §3.1
- [`master-conversational-templates.md`](../policies/master-conversational-templates.md) — voice does NOT apply (this is non-AI channel)
- [`single-assistant-identity.md §2.4`](../policies/single-assistant-identity.md) — AI silence per §2.2
- [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — HUMAN_LOCKED Q-IAC17
- [`event-taxonomy.md §3.12`](../policies/event-taxonomy.md) — 6 NEW events §10
- [`../decisions-log.md`](../decisions-log.md) — Q-IAC1..Q-IAC20

---

## 15. What this unblocks

- **Q-MR11 reviews dependency** — review «Обсудить со студией» now has channel to land in
- **All master-earnings disputes** — chat threads carry dispute conversation
- **Time-off / substitution / offboarding negotiations** — formal channel vs WhatsApp
- **Audit completeness** — master-admin communication is captured
- **Founder oversight on sensitive matters** — escalation path
- **Trust foundation** — masters know there's a formal way to raise concerns

## 16. What this does NOT unblock

- ❌ Master-to-master direct chat
- ❌ Master broadcast policy (admin → many masters)
- ❌ Anonymous reporting (Phase 4+)
- ❌ Group chats (master + master + admin)
- ❌ Video/voice calls
- ❌ Public master forum
- ❌ Skip Q-IAC8 founder visibility trigger (pre-deploy)
- ❌ Skip Q-IAC14 other-master-complaint auto-founder (pre-deploy)
- ❌ Skip Q-IAC19 wellness-data block (pre-deploy)

---

## 17. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Messaging backend lead | ☐ | |
| Mini App frontend (master Со студией tab + admin Чаты с мастерами + Founder escalated view) | ☐ | |
| AI prompt eng (§7.2 redirect; topic detection only — no generation) | ☐ | |
| Reviews steward (Q-MR11 dependency resolution) | ☐ | 🔴 PRE-DEPLOY |
| Earnings steward (dispute discussion integration) | ☐ | |
| Time-off + substitution + offboarding stewards (entry points) | ☐ | |
| Conversation ownership steward (Q-IAC17) | ☐ | |
| Privacy / Legal (§2.11 + §8 retention + Q-IAC19 wellness block + Q-IAC9 anonymous out-of-scope) | ☐ | 🔴 PRE-DEPLOY |
| Founder (Q-IAC8 founder visibility trigger + Q-IAC14) | ☐ | 🔴 PRE-DEPLOY |
| Accessibility (WCAG 2.2 AA) | ☐ | |

## Last verified
2026-05-19 (initial draft, 7 topics + 13 endpoints + 6 events + AI-silent channel + topic-linked threading — locked)
