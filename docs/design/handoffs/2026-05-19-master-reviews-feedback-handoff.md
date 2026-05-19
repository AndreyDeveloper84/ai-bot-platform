# Master Reviews & Customer Feedback Receipt — Engineering Handoff

**Date:** 2026-05-19 r1
**Status:** Production-blocking for master psychological retention — customer feedback flows to master via mediated, non-shaming surfaces
**Reads:** [`../policies/master-conversational-templates.md`](../policies/master-conversational-templates.md), [`../handoffs/2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md), [`../policies/assistant-persona.md`](../policies/assistant-persona.md), [`../policies/single-assistant-identity.md`](../policies/single-assistant-identity.md), [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md), [`../policies/ai-quality-observability.md`](../policies/ai-quality-observability.md), [`../policies/customer-cancellation-reschedule-spec.md`](../policies/customer-cancellation-reschedule-spec.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md), [`./2026-05-19-master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md)

> Customer feedback reaches masters every day. WITHOUT mediated UX, this flow becomes psychologically toxic — masters quit because of one bad anonymous review. WITH it, feedback becomes a calm professional channel: aggregate trends + per-review mediated framing + master autonomy in response. Never streaks, never leaderboards, never shame.

---

## 0. Why this exists

### 0.1 The psychological foundation

Beauty industry has the highest customer-feedback-volatility per service of any service industry. One emotional customer can ruin a master's day. Salons that don't mediate this flow lose masters to burnout. Per [`master-mobile-handoff.md §3`](./2026-05-18-master-mobile-handoff.md): master retention depends on platform protecting the master from raw emotional dumps.

### 0.2 The promise

Customer feedback IS visible to master. We don't hide it (deception breaks trust). But:
- Aggregate over time, not single-review attack vector
- AI-mediated framing for emotionally-loaded reviews
- Master autonomy: can mark review «not about my work», can request admin mediation
- NEVER public master rating
- NEVER cross-master comparison
- NEVER pay-tied-to-rating

### 0.3 The gap

- `master-mobile-handoff.md` shows dashboard with no feedback surface
- `master-conversational-templates.md` has touchpoints 5.1-5.15 — no review-receipt touchpoint
- `ai-quality-observability.md` is observability layer for OWNER, not master-side feedback
- No model: `CustomerFeedback`, `MasterReviewAggregate`, `ReviewFlag`, `MasterResponseToReview`

---

## 1. Scope

### IN
- Master Mini App tab section «Отзывы» (within «Профиль» or new top-level depending on usage; default within Profile per §5.0)
- Per-review surface with mediated framing for low ratings
- Aggregate view: 30 / 90 day rolling, no historical streaks
- Master can mark «не относится к моей работе» (flag for admin)
- Master can mark «спасибо» privately (relayed as receipt to customer)
- Master can request admin mediation on troublesome review
- AI Bot DM mediated notification for new reviews (with thresholds §5)
- Owner / admin sees master's reviews PLUS aggregate trends
- Customer can retract / edit review within 7d
- 4-eye admin review for HIGH-impact-risk reviews (specific allegations §6.5)
- Multi-tenant master sees own reviews per tenant separately

### OUT
- Public master rating page (no public profile rating — privacy + anti-comparison)
- Master responds publicly («reply» under review) — out of scope
- Pay-tied-to-rating — anti-pattern §3
- Streaks / badges / achievements — anti-pattern §3
- Master ranking dashboards
- Review filtering by master to suppress («delete bad reviews») — out of scope (would corrupt aggregate)
- Customer-master direct chat about review — out of scope (mediated only)
- Review-driven service recommendation to master («try this technique») — wellness-OS feature later, not MVP
- ML-generated improvement suggestions — Phase 3+ with extensive guard-rails
- Cross-platform review aggregation (Yandex/2GIS/Google) — Phase 4+
- Photo / video reviews — Phase 3+ (privacy + storage scope)
- Anonymous reviews — out of scope (review tied to booking + customer)

---

## 2. Strategic constraints — non-negotiable

### 2.1 Master psychological safety
- Single bad review NEVER causes a notification storm
- Bot DM threshold §5.3: only specific patterns trigger immediate notification
- Aggregate FIRST, individual SECOND in UX hierarchy

### 2.2 No comparison
- ❌ «You're top 3 this month»
- ❌ «Your rating is below salon average»
- ❌ «Other masters get more reviews»
- ✅ «Ваш средний — 4.8 из последних 30 отзывов»

### 2.3 No streaks
- ❌ «10 пятёрок подряд!»
- ❌ «Лучшая неделя»
- ❌ «5 недель без жалоб»

### 2.4 No pay tie
Per [`master-earnings-handoff §2.2`](./2026-05-19-master-earnings-handoff.md) — gamification is anti-pattern. Reviews informational, NEVER input to compensation calculation.

### 2.5 AI mediation honest, not euphemistic
- ❌ Soften «работала плохо» to «было хорошо» (dishonest)
- ❌ Hide hostile review entirely (master loses signal)
- ✅ Present hostile review with calm framing + context («один отзыв с резкой формулировкой» — see §5.6)
- ✅ AI adds context («это первый негативный за 50 записей»)

### 2.6 Master autonomy in response
- Master decides if review warrants action
- Master can flag «не моя зона ответственности»
- Master can request admin step in
- Master is NEVER forced to «respond» (no public reply mechanism MVP)

### 2.7 Customer can edit / retract (7-day window)
- Within 7d of submission, customer can change their review
- Master sees latest version + audit «отзыв изменён 2 дня назад»
- After 7d, locked

### 2.8 Reviewer identity scope
- Master sees customer first name + initial («Мария И.») — NOT full name
- Master CANNOT see customer phone / email
- Linked to booking_id for context (date / service)

### 2.9 4-eye for sensitive allegations
Specific words/phrases trigger 4-eye admin review BEFORE master sees:
- Allegations of harm, racism, sexual misconduct, drug use
- Pattern detection §6.5
- Admin must categorize: «substantiated» / «misdirected» / «requires investigation»
- Master receives mediated framing post-admin review

### 2.10 Privacy: customer-only data, master sees relayed
- Customer's full feedback text is theirs
- Master sees the text (privacy-respecting display) but customer is the OWNER of the data
- Customer can delete review at any time within 30d
- After 30d, anonymized aggregation only

### 2.11 Reviews are CUSTOMER data, not master data
- Master sees about themselves
- Master cannot edit, delete, hide reviews
- Master sees raw counts + admin's aggregate

### 2.12 Voice preserved
Per [`single-assistant-identity.md`](../policies/single-assistant-identity.md) — AI delivers reviews to master via single-assistant voice, NOT bot persona.

---

## 3. Anti-pattern catalog

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Star rating prominently above master profile photo | Reduces master to number | Hide unless master opts-in for explicit display |
| Push notification «Вам поставили 2 звезды» | Emotional ambush | Mediated DM with framing §5.6 |
| List of recent reviews ordered newest first | Single bad one dominates | Aggregate FIRST §5.1; individual on tap |
| «За месяц 12 положительных и 1 негативный» — emphasis on the 1 | Negative bias | Equal weight in framing |
| Leaderboard or rank | Comparison §2.2 | NEVER |
| Streak counter | Streak anti-pattern §2.3 | NEVER |
| «Improve your rating!» CTA | Pressure | Autonomy |
| Show review BEFORE customer's 7-day edit window | Locks customer into snap judgment | 24h hold §7.4 |
| Auto-translate review to «softer» version | Dishonest §2.5 | Show original + context |
| Hide bad review from master if admin disagrees | Trust violation | Show with mediation §6.5 |
| Tie master pay to review score | Anti-pattern §2.4 | NEVER |
| Allow master to flag «delete this» | Corrupts aggregate | Flag «not my responsibility» only |
| Show full customer name | Privacy §2.8 | Initials |
| Cross-master comparison in master view | §2.2 | NEVER |
| Notify master at midnight | Timing | Quiet hours per §5.7 |
| Aggregate over all-time | Locks in old work | Rolling 30/90 §5.1 |
| Display review without service/date context | Decontextualized | Always show booking ref §5.4 |
| Public master rating page | Privacy + comparison | NEVER MVP |

---

## 4. Customer-side review prompts

### 4.1 Post-visit Bot DM (T+1h after booking COMPLETED)

```
{{customer_first_name}}, спасибо что зашли! 🌸

Как всё было?
[😍 Класс]   [🙂 Норм]   [😐 Так себе]   [😞 Не очень]

Если хотите поделиться подробнее — напишите мне в ответ. И вы, и
{{master_first_name}} это увидите.

Можно пропустить — никаких напоминаний не будет.
```

### 4.2 Post-visit Mini App «Visit recap»

```
┌────────────────────────────────────────┐
│ 🌸 Как прошёл визит?                    │
├────────────────────────────────────────┤
│ 19 мая, маникюр у Анны                 │
│                                        │
│ Оценка визиту в целом:                  │
│ ☆ ☆ ☆ ☆ ☆                              │
│                                        │
│ Хотите оставить пару слов?              │
│ [_____________________________]        │
│                                        │
│ ──                                      │
│ ✓ Мастер сможет это увидеть             │
│ Можно отредактировать в течение недели  │
│                                        │
│ [Отправить]   [Пропустить]              │
└────────────────────────────────────────┘
```

### 4.3 Detail expansion (optional, after Q-MR2 rule decided)

If customer chose 1-2 stars OR negative emoji → optional detail screen:

```
┌────────────────────────────────────────┐
│ Что было не так?                         │
├────────────────────────────────────────┤
│ Можете отметить несколько                │
│                                        │
│ ☐ Мастер                                │
│ ☐ Результат процедуры                    │
│ ☐ Время ожидания                         │
│ ☐ Чистота / условия                      │
│ ☐ Стоимость                              │
│ ☐ Что-то другое                          │
│                                        │
│ Хотите написать подробно?                │
│ [_____________________________]        │
│                                        │
│ [Готово]   [Пропустить]                  │
└────────────────────────────────────────┘
```

### 4.4 Customer edit / retract (within 7d)

```
┌────────────────────────────────────────┐
│ ← Ваш отзыв от 19 мая                   │
├────────────────────────────────────────┤
│ ☆ ☆ ☆ ☆ ☆                              │
│ «{{customer's text}}»                  │
│                                        │
│ Можете изменить пока ещё (до {{date+7d}})│
│                                        │
│ [Изменить]   [Удалить]                   │
└────────────────────────────────────────┘
```

After 7d, screen replaces edit/delete with «Отзыв опубликован».

### 4.5 Customer NEVER pressured to leave feedback

Skip is always option. After single «пропустить» tap, NO follow-up requests for that visit. Long-term cadence: no more than 1 prompt per 14 days regardless of visit frequency (anti-fatigue).

---

## 5. Master-side surfaces

### 5.0 Where in Mini App

Master Mini App «Профиль» tab gets new section «Отзывы клиентов». Position: under «Услуги» and above «Расписание».

If review volume > 30 reviews lifetime, section can be promoted to its own tab (per Q-MR1). MVP: nested in Profile.

### 5.1 Aggregate-first home

```
┌────────────────────────────────────────┐
│ ← Отзывы клиентов                       │
├────────────────────────────────────────┤
│ ── Последние 30 дней ──                 │
│                                        │
│ Средняя оценка: 4.8 из 5                │
│ (на основе 12 отзывов)                  │
│                                        │
│ ☆☆☆☆☆ — 10                              │
│ ☆☆☆☆ — 1                                │
│ ☆☆☆ — 1                                 │
│ ☆☆ — 0                                  │
│ ☆ — 0                                   │
│                                        │
│ Темы, которые чаще всего отмечают:      │
│ • аккуратность 5×                       │
│ • быстро 3×                             │
│ • уютно 3×                              │
│                                        │
│ ── Прочие периоды ──                    │
│ Последние 90 дней: 4.7 (38 отзывов)     │
│                                        │
│ ── Отзывы в подробностях ──              │
│ [Посмотреть отзывы]                     │
│                                        │
│ ── Что-то заметили? ──                  │
│ [💬 Обсудить со студией]                │
└────────────────────────────────────────┘
```

NO ALL-TIME aggregate by default (anti-pattern §3 — locks in old work).

«Обсудить со студией» → opens internal admin chat (doc #6 of master UX backlog).

### 5.2 Per-review list

```
┌────────────────────────────────────────┐
│ ← Все отзывы                            │
├────────────────────────────────────────┤
│ Фильтр: [Все ▾]  Сортировка: [Свежие ▾] │
│                                        │
│ ┌────────────────────────────────────┐ │
│ │ 17 мая, маникюр                    │ │
│ │ Мария И. ★ ★ ★ ★ ★                 │ │
│ │ «Очень аккуратно, прям счастье»    │ │
│ │ Темы: аккуратность, уютно          │ │
│ │ [Подробнее]                         │ │
│ └────────────────────────────────────┘ │
│                                        │
│ ┌────────────────────────────────────┐ │
│ │ 15 мая, стрижка                    │ │
│ │ Олег П. ★ ★ ★                       │ │
│ │ «Норм, но долго ждал у входа»      │ │
│ │ Темы: время ожидания               │ │
│ │ ⓘ Помощник видит — это про студию,  │ │
│ │   не про мастера                    │ │
│ │ [Подробнее]                         │ │
│ └────────────────────────────────────┘ │
│                                        │
│ ┌────────────────────────────────────┐ │
│ │ ⓘ В ожидании, скоро покажу         │ │
│ │ Новый отзыв сейчас проверяется.    │ │
│ └────────────────────────────────────┘ │
│  ↑ 4-eye admin review per §6.5         │
└────────────────────────────────────────┘
```

Filters: rating range, has-comment, has-flag.

### 5.3 Per-review detail

```
┌────────────────────────────────────────┐
│ ← Отзыв                                 │
├────────────────────────────────────────┤
│ 17 мая, маникюр                         │
│ Клиент: Мария И.                        │
│                                        │
│ ★ ★ ★ ★ ★                              │
│ «Очень аккуратно, прям счастье. Спасибо  │
│ Анне за внимательность.»                │
│                                        │
│ Темы: аккуратность, уютно               │
│                                        │
│ ── Ваше действие ──                     │
│ [💬 Поблагодарить (через приложение)]   │
│ [⚠ Не относится ко мне]                 │
│ [📨 Обсудить со студией]                │
│                                        │
│ ── Информация ──                        │
│ Получен 17 мая, 14:23                   │
│ Клиент может изменить до 24 мая          │
└────────────────────────────────────────┘
```

Actions:
- **Поблагодарить** — sends customer-facing receipt §5.5
- **Не относится ко мне** — flags for admin per §6.4
- **Обсудить со студией** — opens admin internal chat thread referencing review (doc #6)

### 5.4 Booking context

Tap booking line in detail → service / date / time / price (no customer phone/email). Master can confirm «yes that was me» mentally. Helps when 2 masters work on same booking (master-substitution case per [`booking-conflict-resolution-ux §3.6b`](../policies/booking-conflict-resolution-ux.md)) — booking might reference original master_id but review is about substitute.

### 5.5 Master thanks customer

After tap «Поблагодарить»:

```
┌────────────────────────────────────────┐
│ Поблагодарить                            │
├────────────────────────────────────────┤
│ Выберите:                                │
│ ⦿ 🙏 Спасибо за отзыв                    │
│ ◯ ❤ Очень рада, что понравилось         │
│ ◯ ✨ Спасибо, ждём в гости                │
│                                        │
│ Или своими словами (макс 100 знаков):   │
│ [_____________________________]        │
│                                        │
│ [Отправить]   [Отмена]                  │
└────────────────────────────────────────┘
```

Sent as Bot DM to customer:

```
{{master_first_name}} прочитала ваш отзыв и передаёт:
«{{master's text or template}}»

Спасибо что поделились!
```

Master cannot push to ALWAYS thank (no automated; manual per review). No nag if master doesn't engage.

### 5.6 AI Bot DM to master — new review notification (mediated)

#### 5.6a Positive review (rating ≥ 4)
Per Q-MR4: aggregate weekly digest, NOT per-review immediate, to avoid notification fatigue.

```
{{master_first_name}}, за прошлую неделю клиенты оставили 5 отзывов — все
тёплые. Средняя оценка 4.9. Чаще всего отмечают: аккуратность, уют.

Зайдите посмотреть, если хотите.
[Открыть]
```

#### 5.6b Mixed review (rating = 3)

Immediate but calm:

```
Получили отзыв от {{customer_first_name}}, оценка 3 из 5. Текст:
«Норм, но долго ждал у входа».

Помощник отмечает: похоже, это про студийный поток, не про вашу работу.
{{salon_owner}} тоже в курсе.

Если хотите обсудить — [Открыть отзыв].
```

#### 5.6c Negative review (rating ≤ 2) — mediated framing

If 4-eye admin per §6.5 says «misdirected» or «not master's fault»:

```
{{master_first_name}}, есть один отзыв с резкой формулировкой. {{salon_owner}}
посмотрела — это про условия в студии, не про вашу работу.

Если хотите его увидеть, [Открыть]. Можно и не смотреть — мы разберёмся
со студийной стороны.
```

If 4-eye says «substantiated» or «requires investigation»:

```
{{master_first_name}}, получили отзыв с резкой формулировкой. {{salon_owner}}
хочет обсудить с вами лично — давайте созвонимся / пересечёмся на смене.

[Открыть отзыв]   [Связаться со студией]
```

NEVER:
- ❌ «You have a 1-star review» without framing
- ❌ Sounding judgmental either toward customer or master
- ❌ Hiding review entirely

#### 5.6d Review allegations (TIER 2)
Per §6.5 — if allegations of harm/misconduct, master is NOT notified by AI. Admin contacts master via human-to-human channel. AI silent until admin resolves.

### 5.7 Quiet hours

No review notifications between 21:00-09:00 master local time. Buffer until next morning. Aggregate digest §5.6a always sent during business hours.

### 5.8 Aggregate refresh cadence

- Real-time on receipt of new review (after 4-eye + 24h customer-edit hold)
- Themes auto-extract via LLM (Phase 2 ML+heuristic; Phase 3 ML)

---

## 6. Admin-side surfaces

### 6.1 Reviews tab in admin Mini App

```
┌────────────────────────────────────────┐
│ 📋 Отзывы клиентов                       │
├────────────────────────────────────────┤
│ ── В ожидании 4-eye ──                  │
│ ⚠ 1 отзыв требует вашего внимания       │
│                                        │
│ Олег П. → Анна, 17 мая                  │
│ ★ ★ ★ (3)                              │
│ «Норм, но долго ждал у входа»           │
│ Авто-флаг: «возможно про студию»        │
│                                        │
│ [Разрешить мастеру]   [Изучить]         │
│                                        │
│ ── Аналитика по студии ──                │
│ За 30 дней:                              │
│ Получено отзывов: 42                     │
│ Средняя: 4.7                            │
│ С низкой оценкой (≤ 2): 1               │
│ ↑ требует разбора                        │
│                                        │
│ Темы:                                    │
│ • аккуратность 18                        │
│ • уютно 14                              │
│ • время ожидания 5  ← обратите внимание │
│ • цена 3                                │
│ [Посмотреть подробно]                    │
└────────────────────────────────────────┘
```

### 6.2 Admin sees all reviews per master

Per-master section in admin's view:
- Same aggregate format §5.1
- Individual reviews
- Mediation actions admin has taken

### 6.3 Admin response to «не относится ко мне» master-flag

Master flagged review → admin queue:

```
┌────────────────────────────────────────┐
│ Мастер пометил «не относится»            │
├────────────────────────────────────────┤
│ Анна о записи Олег П. 17 мая:           │
│ «Норм, но долго ждал у входа»           │
│                                        │
│ Что делать?                              │
│ ⦿ Согласна — это о студии, не об Анне  │
│   → отзыв остаётся, но Анна освобождена │
│     от ответственности                  │
│ ◯ Не согласна — это конкретно о ней    │
│   → возвращается в её список             │
│ ◯ Спорно — обсудить                     │
│   → открыть внутренний чат              │
│                                        │
│ [Подтвердить]                            │
└────────────────────────────────────────┘
```

### 6.4 4-eye admin review flow §2.9

Triggered by:
- Rating ≤ 2 + text length > 30 chars
- Pattern detection: profanity, allegations, drug references, racial language, sexual misconduct keywords (PII-redacted scan)
- Manual flag by customer-facing AI («I think this is upset, needs mediation»)

Admin sees:

```
┌────────────────────────────────────────┐
│ ⚠ Отзыв на проверке                     │
├────────────────────────────────────────┤
│ От: Олег П.                              │
│ На запись: 17 мая, стрижка к Анне       │
│                                        │
│ Оценка: ★                                │
│ Текст: «<<полный текст>>»                │
│                                        │
│ Авто-флаг: возможно содержит обвинения   │
│                                        │
│ Что это?                                  │
│ ⦿ Жалоба на качество работы             │
│   → передать мастеру с поддержкой       │
│ ◯ Жалоба на студию (не мастера)        │
│   → передать мастеру с пометкой         │
│ ◯ Серьёзное обвинение                  │
│   → НЕ передавать через бота, контакт   │
│     лично                                │
│ ◯ Эмоциональный момент клиента          │
│   → пометить, оставить мастеру с        │
│     спокойной формулировкой              │
│ ◯ Спам / неуместное                     │
│   → удалить с аудитом                   │
│                                        │
│ Ваш комментарий мастеру (опционально):   │
│ [_____________________________]        │
│                                        │
│ [Подтвердить]                            │
└────────────────────────────────────────┘
```

### 6.5 Sensitive allegation detection

Keywords trigger §6.4 TIER 2:
- Сексуальные намёки / неуместные действия
- Угрозы / агрессия
- Нанесение вреда здоровью (помимо медицинской ошибки клиника-side)
- Расизм / дискриминация
- Воровство

Master does NOT see review directly. Admin contacts master through other channel. Audit captures full path.

### 6.6 Admin can override mediation framing

If 4-eye admin disagrees with AI's automated mediation copy, admin can edit before master sees. Audit captures change.

---

## 7. Lifecycle & states

### 7.1 Review state machine

```
[SUBMITTED] (customer sends)
   ↓
[24H_EDIT_HOLD] (24h customer-edit window §7.4)
   ↓
[AUTO_SCREEN] (LLM/heuristic checks)
   ↓
[4_EYE_PENDING] (if triggered §6.5) ←──→ [PUBLISHED] (otherwise)
   ↓ admin action
[ADMIN_CLASSIFIED]
   ↓
[PUBLISHED_TO_MASTER]
   ↓ master action (optional)
[MASTER_ACKNOWLEDGED | MASTER_FLAGGED_NOT_MINE | OPEN_DISCUSSION]

Customer can edit/retract:
[any state pre-D+7] → [REVISED] or [WITHDRAWN]

Customer hard-delete window: 30d. After 30d, anonymized aggregation only §2.10.

Admin can:
- escalate to founder for sensitive
- merge duplicate reviews if customer accidentally submitted twice
- delete spam (audit captured)
```

### 7.2 Hold delay rationale

**24h hold before master sees:**
- Customer might edit on reflection
- Reduces masters reading raw heat-of-moment text
- Customer can read again next morning, soften if appropriate

**7d edit window:**
- Long enough for customer reflection
- Short enough that master sees roughly-current view
- After 7d, snapshot locks

**30d delete window:**
- GDPR-style customer right to be forgotten
- After 30d, contributes to aggregate but text removed

### 7.3 Booking-required link
- Every review references `booking_id`
- No review without prior booking COMPLETED status
- Prevents fake reviews from non-customers

### 7.4 «In waiting» visibility
Master sees count of in-pipeline reviews («Скоро увидите N новых») without text §5.2. Prevents «What did they say?!» anxiety attack from blocked text.

### 7.5 Cycle interaction with earnings
Reviews do NOT affect earnings (per §2.4). They CAN inform admin's commission-rate decision next quarter, but that's admin's call. Master gets advance notice if commission change is proposed §master-earnings §Q-ME16.

---

## 8. Data models

### 8.1 `CustomerFeedback`

```python
class CustomerFeedback(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    booking = models.ForeignKey('booking.Booking', on_delete=CASCADE, related_name='feedback')
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='+')
    master = models.ForeignKey('staff.Master', on_delete=SET_NULL, null=True, related_name='reviews')
    # SET_NULL — master might leave; aggregate analytics still relevant

    rating = models.IntegerField()  # 1-5
    text = models.TextField(blank=True, default='', max_length=2000)

    THEMES_CHOICES = [
        ('accuracy', 'Аккуратность'),
        ('comfort', 'Уютно'),
        ('speed', 'Быстро'),
        ('waiting', 'Время ожидания'),
        ('price', 'Цена'),
        ('cleanliness', 'Чистота'),
        ('result', 'Результат'),
        ('master_skill', 'Мастер'),
        ('other', 'Другое'),
    ]
    themes_customer_tagged = models.JSONField(default=list)  # subset of choices
    themes_ai_extracted = models.JSONField(default=list)  # subset; from LLM theme detection on text

    STATUS_CHOICES = [
        ('submitted', 'Submitted'),
        ('edit_hold_24h', '24h edit hold'),
        ('auto_screen', 'Auto-screening'),
        ('4_eye_pending', 'Awaiting admin 4-eye'),
        ('published', 'Published to master'),
        ('master_acknowledged', 'Master acknowledged'),
        ('flagged_not_mine', 'Master flagged not their responsibility'),
        ('revised', 'Customer revised'),
        ('withdrawn', 'Customer withdrew'),
        ('deleted_admin', 'Admin removed (spam)'),
        ('founder_review', 'Escalated to founder'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='submitted')

    submitted_at = models.DateTimeField(auto_now_add=True)
    edit_hold_until = models.DateTimeField()
    customer_edit_deadline = models.DateTimeField()  # +7d
    customer_delete_deadline = models.DateTimeField()  # +30d
    published_at = models.DateTimeField(null=True, blank=True)
    last_revised_at = models.DateTimeField(null=True, blank=True)

    SENSITIVITY_CHOICES = [
        ('routine', 'Routine'),
        ('mild_negative', 'Mild negative (rating ≤ 3)'),
        ('sensitive_keywords', 'Sensitive keywords flagged'),
        ('tier_2_allegations', 'Serious allegations (admin handles offline)'),
    ]
    sensitivity = models.CharField(max_length=32, choices=SENSITIVITY_CHOICES, default='routine')

    admin_classification = models.CharField(max_length=64, blank=True, default='')
    # 'quality_complaint', 'salon_environment', 'serious_allegation',
    # 'emotional_moment', 'spam', etc.
    admin_comment_to_master = models.TextField(blank=True, default='', max_length=500)

    admin_4_eye_user = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    admin_4_eye_at = models.DateTimeField(null=True, blank=True)

    master_acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            Index(fields=['master', 'tenant', '-published_at']),
            Index(fields=['tenant', 'status']),
            Index(fields=['customer', '-submitted_at']),
            Index(fields=['edit_hold_until']),  # screener
        ]
```

### 8.2 `MasterReviewAggregate`

Computed periodic.

```python
class MasterReviewAggregate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='review_aggregates')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    WINDOW_CHOICES = [
        ('30d', 'Last 30 days'),
        ('90d', 'Last 90 days'),
    ]
    window = models.CharField(max_length=8, choices=WINDOW_CHOICES)
    # NO 'all_time' MVP per anti-pattern §3

    computed_at = models.DateTimeField(auto_now=True)

    review_count = models.IntegerField()
    avg_rating = models.DecimalField(max_digits=3, decimal_places=2)
    rating_distribution = models.JSONField(default=dict)  # {1: n, 2: n, ..., 5: n}
    top_themes = models.JSONField(default=list)  # [{ name, count }, ...]

    class Meta:
        unique_together = [('master', 'tenant', 'window')]
```

### 8.3 `ReviewMasterAction`

Audit of master's action on a review.

```python
class ReviewMasterAction(models.Model):
    review = models.ForeignKey(CustomerFeedback, on_delete=CASCADE, related_name='master_actions')
    master = models.ForeignKey('staff.Master', on_delete=CASCADE, related_name='+')

    ACTION_CHOICES = [
        ('thanked', 'Thanked customer'),
        ('flagged_not_mine', 'Flagged not their responsibility'),
        ('requested_admin_discussion', 'Requested admin internal chat'),
    ]
    action = models.CharField(max_length=64, choices=ACTION_CHOICES)
    metadata = models.JSONField(default=dict)
    # {'thank_text': '...', 'thank_template_id': '...', 'admin_chat_thread_id': '...'}

    at = models.DateTimeField(auto_now_add=True)
```

### 8.4 `ReviewAdminAction`

```python
class ReviewAdminAction(models.Model):
    review = models.ForeignKey(CustomerFeedback, on_delete=CASCADE, related_name='admin_actions')
    admin = models.ForeignKey('auth.User', on_delete=SET_NULL, null=True, related_name='+')

    ACTION_CHOICES = [
        ('classified', '4-eye classification'),
        ('overrode_mediation', 'Override AI mediation copy'),
        ('rejected_master_flag', 'Rejected master flag (review stays on master)'),
        ('approved_master_flag', 'Approved master flag (review off master list)'),
        ('deleted_as_spam', 'Deleted as spam'),
        ('escalated_to_founder', 'Escalated to founder'),
    ]
    action = models.CharField(max_length=64, choices=ACTION_CHOICES)
    metadata = models.JSONField(default=dict)
    reason = models.TextField(blank=True, default='', max_length=500)
    at = models.DateTimeField(auto_now_add=True)
```

---

## 9. API contracts

### 9.1 Customer endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/customer/feedback` | Submit review |
| PATCH | `/api/v1/customer/feedback/<id>` | Edit (within 7d) |
| DELETE | `/api/v1/customer/feedback/<id>` | Withdraw (within 30d) |
| GET | `/api/v1/customer/feedback/<id>` | View own review |
| GET | `/api/v1/customer/feedback?booking_id=...` | List own reviews |

### 9.2 Master endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/master/reviews/aggregate` | Aggregate §5.1 (30d/90d) |
| GET | `/api/v1/master/reviews` | List own reviews (paginated, filtered) |
| GET | `/api/v1/master/reviews/<id>` | Per-review §5.3 |
| POST | `/api/v1/master/reviews/<id>/thank` | Thank customer §5.5 |
| POST | `/api/v1/master/reviews/<id>/flag-not-mine` | Flag not responsible §5.3 |
| POST | `/api/v1/master/reviews/<id>/request-discussion` | Open admin chat thread §5.3 |
| POST | `/api/v1/master/reviews/<id>/acknowledge` | Mark seen (analytic only) |

### 9.3 Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/reviews/queue/4-eye` | Pending 4-eye review |
| POST | `/api/v1/admin/reviews/<id>/4-eye-classify` | Apply classification §6.4 |
| GET | `/api/v1/admin/reviews/queue/master-flags` | Master «not mine» queue |
| POST | `/api/v1/admin/reviews/<id>/resolve-master-flag` | Approve/reject §6.3 |
| GET | `/api/v1/admin/reviews?master_id=...` | All reviews for a master |
| POST | `/api/v1/admin/reviews/<id>/delete-spam` | Spam removal §6.4 |
| POST | `/api/v1/admin/reviews/<id>/escalate-founder` | Escalate §6.5 |
| PATCH | `/api/v1/admin/reviews/<id>/override-mediation` | Edit AI's mediation copy §6.6 |

### 9.4 Founder endpoints (Phase 3+)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/reviews/escalated` | Cross-tenant escalated reviews |
| POST | `/api/v1/founder/reviews/<id>/resolve` | Final decision |

### 9.5 Validation on POST `/customer/feedback`

```json
{
  "booking_id": "uuid",
  "rating": 5,
  "text": "string ≤ 2000",
  "themes_customer_tagged": ["accuracy", "comfort"]
}
```

- Booking must be COMPLETED status
- Booking must belong to authenticated customer
- One review per booking (subsequent attempt → 409 with existing review_id)
- text length ≤ 2000
- rating in [1,5]
- themes valid choices

---

## 10. Events emitted

Add to [`event-taxonomy.md`](../policies/event-taxonomy.md) as new section `3.8 review domain`:

| Trigger | Event | Notes |
|---|---|---|
| Customer submits feedback | NEW: `review.submitted` | rating, has_text, themes |
| 24h hold elapsed | NEW: `review.hold_elapsed` | |
| 4-eye triggered | NEW: `review.4_eye_triggered` | reason |
| Admin classified | NEW: `review.admin_classified` | classification |
| Published to master | NEW: `review.published_to_master` | mediation_template |
| Master acknowledged | NEW: `review.master_acknowledged` | |
| Master thanked | NEW: `review.master_thanked` | template OR custom |
| Master flagged not-mine | NEW: `review.master_flagged_not_mine` | |
| Customer revised | NEW: `review.customer_revised` | |
| Customer withdrew | NEW: `review.customer_withdrew` | |
| Admin deleted spam | NEW: `review.admin_deleted_spam` | |
| Admin override mediation | NEW: `review.admin_overrode_mediation` | |
| Founder resolved | NEW: `review.founder_resolved` | |

13 NEW events §10.

---

## 11. AI prompt + mediation engine

### 11.1 Mediation framing decision tree

```
input: review (rating, text, themes, sensitivity)
├ rating ≥ 4 → no mediation; aggregate weekly digest §5.6a
├ rating == 3
│   ├ text contains theme «waiting / cleanliness / price» → §5.6b «about studio»
│   └ text contains theme «master_skill / accuracy / result» → §5.6b minus salon-pass mention
├ rating ≤ 2
│   ├ sensitive_keywords + tier_2 → block from master per §5.6d; admin handle offline
│   ├ sensitive_keywords + not tier_2 → §5.6c serious; ask master to discuss with admin
│   ├ admin classified as salon_environment → §5.6c «about studio not work»
│   ├ admin classified as quality_complaint → §5.6c «we'll discuss» (no euphemism)
│   ├ admin classified as emotional_moment → calm framing «один отзыв с резкой формулировкой»
│   └ admin classified as spam → not delivered
```

### 11.2 Forbidden phrases in mediation copy

AI MUST NOT use:
- «but» softening («It's harsh BUT it's just one»)
- «you might want to improve» (pressure)
- «don't worry about it» (gaslighting)
- «they were probably having a bad day» (assumes customer state)
- «this customer is...» (judgmental about customer)
- «your rating is dropping» (anti-pattern §2.2 / §2.3)
- «to keep your rating up» (pressure)

AI SHOULD use:
- Calm acknowledgment («есть отзыв с резкой формулировкой»)
- Salon-owner reference («{{salon_owner}} тоже видела»)
- Action choice handoff («Можете посмотреть. Можно и не смотреть»)
- Specific theme reference («про время ожидания»)

### 11.3 Theme extraction

Phase 2 MVP: heuristic keyword matching + small LLM call per review for theme tagging.
- Keywords map (Russian + English MVP for international)
- LLM extraction: zero-shot via small model, validated against allowlist
- Themes outside allowlist → tagged «other» (NOT stored as free-text customer label)

Phase 3+ ML training on real corpus.

### 11.4 Sensitivity detection

Layer 1: regex/keyword on Russian sensitive terms list (per [`ai-quality-observability.md`](../policies/ai-quality-observability.md) §5).

Layer 2: LLM call «is this text alleging harm / misconduct / illegal activity by master or salon?». Yes → tier_2.

Layer 3 fallback: 4-eye queue (admin sees all rating-≤2 anyway).

---

## 12. Acceptance criteria (engineering checklist)

- [ ] 4 models §8 (CustomerFeedback, MasterReviewAggregate, ReviewMasterAction, ReviewAdminAction)
- [ ] Migration applies cleanly
- [ ] 5 customer endpoints + 7 master + 8 admin + 2 founder §9
- [ ] Cross-master 403 (master cannot read other master's reviews) §2.1
- [ ] 24h edit hold + 7d edit window + 30d delete window §7.1
- [ ] 4-eye queue with classification UI §6.4
- [ ] Master view aggregate-first §5.1 (rolling 30d / 90d only, no all-time)
- [ ] Per-review detail with mediation framing §5.3
- [ ] Master actions: thank / flag / request-discussion §5.5
- [ ] Customer thank-you receipt via Bot DM §5.5
- [ ] Bot DM templates §5.6 (a/b/c/d variants)
- [ ] Quiet hours respected §5.7
- [ ] Theme extraction (heuristic+LLM) §11.3
- [ ] Sensitivity detection 3-layer §11.4
- [ ] Customer review submit Mini App §4.2 + edit/retract §4.4
- [ ] Customer Bot DM prompt §4.1
- [ ] Customer skip-without-retry §4.5
- [ ] Admin reviews tab §6.1 + per-master view §6.2 + 4-eye §6.4 + master-flag review §6.3
- [ ] Admin mediation override §6.6
- [ ] 13 events emitted §10
- [ ] PII rules §2.8 / §2.10 (master sees customer initials only)
- [ ] Anti-pattern review §3
- [ ] Tests: customer submit + edit + retract; master flag + admin resolve; 4-eye TIER 2 master never sees; aggregate calc; theme extract; thank-you delivery; spam delete; cross-master denial
- [ ] Accessibility WCAG 2.2 AA on all surfaces

---

## 13. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-MR1** | Master Mini App position — nested in Profile or standalone tab? | Nested MVP §5.0; promote to tab if usage data supports. | UX | 🟢 |
| **Q-MR2** | Negative-detail screen §4.3 — show on rating ≤ 3 or ≤ 2? | ≤ 3 MVP (broader funnel of constructive feedback) | UX | 🟢 |
| **Q-MR3** | Customer can submit review without text (rating only)? | YES — rating-only is valid. Encourages volume. Theme tags help signal even sans text. | Policy | 🟢 |
| **Q-MR4** | Positive reviews — immediate or weekly digest? | Weekly digest §5.6a (anti-fatigue). Per-review immediate ONLY for negative. | UX | 🟡 |
| **Q-MR5** | Master thank-you template — pre-set OR free-text default? | Pre-set 3 options + 100-char free-text §5.5. Low friction. | UX | 🟢 |
| **Q-MR6** | Multi-master booking (2 masters per service e.g. complex service) — review goes to whom? | Both masters see; review tagged with both `master_ids`. Master-flag-not-mine still works per-master. Phase 3+ if needed. | Policy + Eng | 🟡 |
| **Q-MR7** | Customer who deletes review — can re-submit? | NO within 30d (same booking_id). After 30d, booking is locked anyway. | Policy | 🟢 |
| **Q-MR8** | Master who is offboarding (per upcoming offboarding doc) — what happens to their reviews? | Reviews remain attributed; aggregate accessible by salon. Master's own access ends at offboarding per offboarding policy. | Privacy + Eng | 🟡 |
| **Q-MR9** | Customer review visibility to other customers (e.g., on salon page) — out of scope MVP? | YES out of scope per §1. No public review surface MVP. | PM | 🟢 |
| **Q-MR10** | Auto-translate reviews (English customer at Russian salon)? | Phase 4+ when international tenants. Master sees original. | Eng | 🟢 |
| **Q-MR11** | Review-driven admin/master conversation thread integration | Per §5.3 «Обсудить со студией» → uses internal-admin-chat (doc #6 of master UX). Pre-deploy lock waiting on that doc. | Policy + Eng | 🔴 BLOCKS UNTIL DOC #6 |
| **Q-MR12** | 4-eye admin SLA before master can see? | 24h MVP. If admin is slow, master sees aggregate count «1 в ожидании» but no text per §7.4. Cap at 72h auto-publish with auto-mediation framing. | Policy | 🟡 |
| **Q-MR13** | Customer wrote review for wrong booking (clicked wrong booking) — can they reassign? | NO MVP. Customer can withdraw + write new. Edge case. | Policy | 🟢 |
| **Q-MR14** | Anti-bot / fake review detection | Phase 3+ ML. MVP relies on booking_id link (no booking = no review). | Eng | 🟢 |
| **Q-MR15** | Customer feedback skip — does AI ask why? | NO. Per §4.5 skip is silent. Don't pressure. | UX | 🟢 |
| **Q-MR16** | Customer cancellation context — feedback after cancellation? | NO. Feedback ONLY on COMPLETED bookings. Cancelled bookings have separate cancellation feedback (different scope). | Policy | 🟢 |
| **Q-MR17** | Mediation copy language - language follows customer's locale or master's locale? | Master's locale (master reads it). Customer's review text shown in original language. | UX + Eng | 🟢 |
| **Q-MR18** | Master can «mute» reviews entirely (don't notify, just aggregate)? | YES — opt-out in profile settings. Aggregate still computed; just no Bot DM notifications. UX should respect autonomy. | UX | 🟡 |
| **Q-MR19** | Tier-2 sensitive review — does customer still get «thank you for feedback» reply? | YES — generic «спасибо, мы рассмотрим». Don't reveal that admin queue is active. Prevents customer escalation pressure. | Policy + UX | 🔴 PRE-DEPLOY |
| **Q-MR20** | Sensitive keywords list — region-specific? | YES — Russian MVP. International later requires per-language list. | Compliance + AI | 🟡 |

---

## 14. Cross-document linkage

- [`master-conversational-templates.md §5`](../policies/master-conversational-templates.md) — 4 new touchpoints (5.21 positive weekly digest, 5.22 mixed, 5.23 negative mediated, 5.24 thank-you receipt)
- [`master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md) — new section in Profile tab §5.0
- [`master-earnings-handoff.md`](./2026-05-19-master-earnings-handoff.md) — §2.4 reviews never affect compensation; cross-doc consistency
- [`ai-quality-observability.md`](../policies/ai-quality-observability.md) — sensitive-keyword detection layer §11.4
- [`single-assistant-identity.md`](../policies/single-assistant-identity.md) — voice §2.12
- [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — tier escalation possible on Q-MR11 admin discussion
- [`event-taxonomy.md §3.8`](../policies/event-taxonomy.md) — 13 NEW events §10 (new domain)
- [`assistant-persona.md`](../policies/assistant-persona.md) — mediation phrase rules §11.2
- [`customer-cancellation-reschedule-spec.md`](../policies/customer-cancellation-reschedule-spec.md) — Q-MR16 cancellation-feedback boundary
- [`../decisions-log.md`](../decisions-log.md) — Q-MR1..Q-MR20

---

## 15. What this unblocks

- **Master psychological retention** — masters don't quit over emotional review attacks
- **Salon owner gets clean signal** — themes + aggregate enable real coaching conversations
- **AI quality observability completeness** — review pipeline feeds into wider quality metrics
- **Single-assistant identity hardening** — voice consistent across feedback delivery
- **Production tip-flow integration** — customer Bot DM post-visit naturally combines tip + feedback prompt
- **Customer-side trust foundation** — customer reviews feel meaningful, not extractive

## 16. What this does NOT unblock

- ❌ Public master rating page
- ❌ Cross-platform review aggregation (Yandex/2GIS)
- ❌ Public-facing master responses to reviews
- ❌ ML-generated coaching for master («try X»)
- ❌ Photo/video reviews
- ❌ Master-comparison features in any UX
- ❌ Skip Q-MR11 internal-admin-chat dependency (blocks until doc #6 lands)
- ❌ Skip Q-MR19 sensitive review customer reply policy (pre-deploy lock)
- ❌ Anonymous review submission

---

## 17. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Reviews backend lead | ☐ | |
| Mini App frontend (master Profile section + admin reviews tab + customer review form) | ☐ | |
| AI prompt eng (mediation framing §11) | ☐ | 🔴 PRE-DEPLOY |
| AI quality steward (sensitive-keyword list + Q-MR20 region scope) | ☐ | 🔴 PRE-DEPLOY |
| Privacy / Legal (Q-MR8 offboarded-master access + Q-MR19 sensitive-customer-reply) | ☐ | 🔴 PRE-DEPLOY |
| Founder (Q-MR19 tier-2 policy + 4-eye scope on master pre-publish) | ☐ | 🔴 PRE-DEPLOY |
| Accessibility (WCAG 2.2 AA on customer review form + master surfaces) | ☐ | |
| Behavioral / clinical (anti-shame framing in §5.6c/d — review by therapist or HR-adjacent advisor) | ☐ | RECOMMENDED |

## Last verified
2026-05-19 (initial draft, 13 events new domain, 4-eye + 24h hold + 7d edit + 30d delete windows locked, mediation framing decision tree §11.1 specified)
