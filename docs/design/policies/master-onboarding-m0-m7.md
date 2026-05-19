# Master Onboarding M0-M7 Flow — invite to first-week-complete

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Foundational — preemptive spec for Phase 2 master-mobile (Ayla Pro) implementation
**Reads:** [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md), [`tenant-as-provider-model.md`](./tenant-as-provider-model.md), [`master-conversational-templates.md`](./master-conversational-templates.md) (r2), [`product-ux-vision.md`](./product-ux-vision.md), [`information-architecture.md`](./information-architecture.md), [`../handoffs/2026-05-18-master-management-handoff.md`](../handoffs/2026-05-18-master-management-handoff.md), [`../handoffs/2026-05-18-master-mobile-handoff.md`](../handoffs/2026-05-18-master-mobile-handoff.md), [`event-taxonomy.md`](./event-taxonomy.md), [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md)

> Owner sends invite to master. What happens between «invite sent» and «master is a productive part of the team a week later»? Eight stages — M0 through M7 — with per-stage triggers, templates, Mini App states, and completion conditions. **Ayla Pro context** per [`tenant-as-provider-model §5`](./tenant-as-provider-model.md) — master onboards into tenant's provider role, not as Ayla customer.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](./ayla-identity-and-brand.md) memory 2026-05-19: master onboards into **Ayla Pro** (tenant's provider tool). Master ↔ Ayla messages during onboarding (e.g., welcome notification) use functional Ayla voice per master-conversational-templates r2. Master ↔ Admin (tenant owner) internal channel uses operational tone per [`master-admin-internal-chat-handoff`](../handoffs/2026-05-19-master-admin-internal-chat-handoff.md). Deprecated `conversation-ownership-policy.md` references preserved as backend mechanic.

---

## 0. Why this exists

### The gap

Three docs each cover a slice:
- [`../handoffs/2026-05-18-master-management-handoff.md`](../handoffs/2026-05-18-master-management-handoff.md) — owner-side master CRUD (invite / archive / services-mapping)
- [`../handoffs/2026-05-18-master-mobile-handoff.md`](../handoffs/2026-05-18-master-mobile-handoff.md) — daily work in master mobile (today / week / requests)
- [`master-conversational-templates.md`](./master-conversational-templates.md) §5.1-5.3 — first 3 touchpoint templates (invite delivery, accepted onboarding step 1, completion confirmation)

**None of them describes the END-TO-END lifecycle of a master joining a salon.** What if master taps invite but never finishes profile? What if invite expires? What if owner re-invites? What does master see day 2 vs day 7? When is onboarding «complete»?

Without this spec, master-mobile engineering improvises mid-flow handlers, edge cases drift, master experience first week feels disconnected.

### The promise

Single source of truth for:
- 8 lifecycle stages M0-M7 with transitions
- Per-stage Mini App state + bot DM template + completion criteria
- Re-invite / expiry / cancellation paths
- Multi-tenant master scenario (Q-MC7 per master-conversational-templates)
- Owner-side visibility into each master's onboarding progress
- Events emitted per stage
- Permissions matrix
- Anti-patterns (over-onboarding, demanding completion, etc.)

### Core principle — AI-first, never manual catalog entry

Per [memory: project_salon_catalog_vertical](../../../C:/Users/user/.claude/projects/C--Users-user-PycharmProjects-ai-bot-platform/memory/project_salon_catalog_vertical.md) — neither master nor salon creates services from scratch. Platform's AI proposes services + regional-parsed prices from the 11-category catalog vertical templates. Master/owner CONFIRMS or adjusts. Manual creation is an escape hatch through owner-approval queue, never a self-serve action.

This shapes M2 wizard fundamentally: it's not «build your service list» — it's «confirm what the AI prepared based on your specialty». Same principle applies to salon onboarding (Phase 4 catalog setup) per [salon-onboarding-handoff Q5 decision](../handoffs/2026-05-17-salon-onboarding-handoff.md) — 11 baseline templates + regional pricing seed.

---

## 1. Scope

### IN
- Master lifecycle from invite sent (M0) → first-week-active (M7)
- All bot DM templates per stage (master-tone per master-conversational-templates)
- Mini App master-side onboarding screens (where they differ from steady-state)
- Profile setup wizard (services + photo + bio)
- Schedule defaults application + confirm
- First-booking ramp-up signals
- First-week digest as M7 marker
- Re-invite + expiry + cancellation flows
- Multi-tenant master onboarding (separate per tenant)
- Audit + event emissions
- Owner-side onboarding progress visibility

### OUT
- Owner sending the invite (covered in master-management-handoff §M2 «Invite flow»)
- Daily master mobile UX after M7 (covered in master-mobile-handoff)
- Master ScheduleChangeRequest mechanics (covered in schedule-management-handoff §6 + master-conversational-templates §5.11)
- Master archive / reactivation (covered in master-management-handoff §M4-M5)
- Catalog-only mode masters (Q-MM5 — never go through M0-M7; they exist passively as catalog entries until promoted to invite)

---

## 2. The 8 stages overview

```
   ┌─ M0: INVITE_SENT ─────────────┐
   │   owner sent, no master open  │
   └──────────────┬────────────────┘
                  │ master taps deeplink
                  ▼
   ┌─ M1: INVITE_ACCEPTED ─────────┐
   │   deeplink resolved, account  │
   │   ready, profile incomplete   │
   └──────────────┬────────────────┘
                  │ master taps «Open» in welcome
                  ▼
   ┌─ M2: PROFILE_SETUP ───────────┐
   │   services + photo + bio      │
   │   wizard in Mini App           │
   └──────────────┬────────────────┘
                  │ profile minimum-fields complete
                  ▼
   ┌─ M3: SCHEDULE_CONFIRM ────────┐
   │   defaults applied, master    │
   │   reviews + adjusts hours      │
   └──────────────┬────────────────┘
                  │ schedule confirmed
                  ▼
   ┌─ M4: CUSTOMER_VISIBLE ────────┐
   │   master appears in customer  │
   │   catalog; bookable           │
   └──────────────┬────────────────┘
                  │ first booking received
                  ▼
   ┌─ M5: FIRST_BOOKING_PENDING ───┐
   │   booking exists, not yet     │
   │   completed                    │
   └──────────────┬────────────────┘
                  │ booking marked completed
                  ▼
   ┌─ M6: FIRST_BOOKING_DONE ──────┐
   │   first customer served,       │
   │   feedback potentially in     │
   └──────────────┬────────────────┘
                  │ 7 days post M1 elapsed
                  ▼
   ┌─ M7: FIRST_WEEK_COMPLETE ─────┐
   │   summary digest, settled in  │
   └────────────────────────────────┘
```

### Stage timing reality

- **M0 → M1**: variable. Most masters tap within 24h (per Q-MM9 lean 14-day expiry). Some take days.
- **M1 → M2**: usually same session (master continues after welcome)
- **M2 → M3**: same session if smooth; could be later if master starts wizard but doesn't finish
- **M3 → M4**: instant on schedule confirm
- **M4 → M5**: depends on customer demand. Could be hours (busy salon) or days (slow build).
- **M5 → M6**: depends on first booking's slot timing
- **M6 → M7**: 7 days post M1 (calendar time, not active time)

Total: a few days for fast onboarders; up to 2+ weeks for slower ones.

---

## 3. M0 — INVITE_SENT

### 3.1 State conditions
- `Master.invite_status = 'pending'`
- `Master.mode = 'invite'` (NOT `'catalog_only'`)
- `Master.is_active = False` (not bookable yet)
- `Master.invited_at = now()`
- `Master.max_handle` populated (target invite recipient)
- No `MaxUser` record linked yet (master hasn't authenticated)

### 3.2 Owner-side visibility
Per [master-management-handoff](../handoffs/2026-05-18-master-management-handoff.md) §M1 list view:
```
{{master_full_name}}
🟡 Приглашён · {{N}} дней назад
[Отозвать]  [Перепригласить]
```

After 7 days no response: badge changes to «🟠 Долго не отвечает». After 14 days: auto-expire transition (see §10).

### 3.3 Bot DM to master
Per [master-conversational-templates §5.1](./master-conversational-templates.md#51-invite-delivery-max-deep-link-first-contact). Sent immediately on invite creation. Customer surface: not applicable (no customer impact yet).

### 3.4 Owner can cancel
Owner taps «Отозвать» → `Master.invite_status = 'cancelled'`. Bot DM to master NOT sent (would confuse — they may not even have read the original invite).

Audit event: `master.invite.cancelled` per [event-taxonomy §3.3](./event-taxonomy.md#33-master-domain).

### 3.5 Owner can re-invite
Owner taps «Перепригласить» → if master is `pending`, re-sends bot DM (idempotent — same template, refreshes timestamp). If `expired`, restores to `pending` + sends new DM.

### 3.6 No master activity
Master remains in M0 until they tap deeplink. No proactive nudges from AI to master (we don't have their `MaxUser` row yet → no DM channel).

### 3.7 Completion criterion for M0 → M1
Master taps deeplink in MAX → MAX returns auth → backend resolves invite + creates `MaxUser` row + binds to `Master` row.

---

## 4. M1 — INVITE_ACCEPTED

### 4.1 State conditions
- `Master.invite_status = 'accepted'`
- `Master.accepted_at = now()`
- `MaxUser` row created and linked
- `Master.is_active = False` (still not bookable — profile incomplete)

### 4.2 Bot DM to master (immediately on deeplink resolve)
Per [master-conversational-templates §5.2](./master-conversational-templates.md#52-invite-accepted--first-onboarding-step). Triggers Mini App open via inline button «К услугам →».

### 4.3 Mini App welcome screen (on first open)

```
┌────────────────────────────────────────┐
│ Добро пожаловать                       │
├────────────────────────────────────────┤
│ {{owner_short_name}} пригласил(а) вас │
│ в студию «{{salon_name}}» как         │
│ мастера {{primary_specialty}}.        │
│                                        │
│ Несколько шагов, чтобы всё работало:  │
│                                        │
│   1. Подтвердить ваши услуги          │
│      ───────────────  пока пусто      │
│                                        │
│   2. Настроить рабочие часы           │
│      ───────────────  по умолчанию    │
│                                        │
│   3. Проверить как клиенты вас видят  │
│      ───────────────  готово           │
│                                        │
│ [Начать с услуг →]                    │
└────────────────────────────────────────┘
```

### 4.4 Progress indicator
3-step linear progress at top. Each step shows current status. Master can tap any to start there (non-linear), but recommended order is 1 → 2 → 3.

### 4.5 Resume from M1
If master closes Mini App mid-onboarding, returning to it lands on welcome screen with progress preserved. Bot DM doesn't re-introduce; resumes context:
```
Продолжим. Остановились на {{step_name}}.

[Открыть студию]
```

### 4.6 Owner-side visibility
Master moves from «🟡 Приглашён» to «🟢 Принял · профиль не заполнен».

### 4.7 Events emitted
- `master.invite.accepted` per event-taxonomy §3.3
- `conversation.started` between master and assistant per §3.5

### 4.8 Completion criterion for M1 → M2
Master taps «Начать с услуг» (or any step entry point) in welcome screen.

---

## 5. M2 — PROFILE_SETUP

### 5.1 State conditions
Master is in Mini App onboarding wizard, hasn't completed minimum required profile fields.

**Minimum required fields**:
- At least 1 service in `MasterService` M2M
- (Photo optional but recommended)
- (Bio optional)

### 5.2 Wizard screen 1 — Services (AI-first template-based)

**Critical principle**: master does NOT manually create services. Per [memory: project_salon_catalog_vertical](../../../C:/Users/user/.claude/projects/C--Users-user-PycharmProjects-ai-bot-platform/memory/project_salon_catalog_vertical.md) — platform AI proposes templates from 11-category baseline + regional parsed pricing. Master confirms/adjusts the AI's suggestion. Manual entry is an escape hatch, never the default flow.

```
┌────────────────────────────────────────┐
│ ← Что вы делаете?                      │
├────────────────────────────────────────┤
│ Помощник подобрал из направления       │
│ {{primary_specialty}} услуги, которые  │
│ обычно делают мастера вашего профиля.  │
│                                        │
│ Отметьте те, которые делаете вы:       │
│                                        │
│ ☑ Классический массаж · 60 мин · 2 800 ₽│
│ ☑ Лимфодренажный массаж · 60 мин · 3 000│
│ ☐ Антицеллюлитный массаж · 90 мин · 4 000│
│ ☐ Спортивный массаж · 60 мин · 3 200    │
│ ☐ Массаж шеи и плеч · 30 мин · 1 500    │
│                                        │
│ Цены — среднее по {{region}} (можно    │
│ скорректировать после).                │
│                                        │
│ Если что-то делаете, чего нет в списке:│
│ [Расскажу помощнику]                   │
│                                        │
│ ── Отмечено: 2 ──                      │
│ [Дальше]                               │
└────────────────────────────────────────┘
```

**Behavior**:
- AI pre-checks 2-3 most common services for this `primary_specialty` (e.g., for «массажист»: classical + lymph default-checked; others available unchecked). Default selections reduce friction; master can uncheck.
- Prices come from platform's regional parsing per [salon-onboarding-handoff §1 Q1 decision](../handoffs/2026-05-17-salon-onboarding-handoff.md) (Hybrid honest seed: парсинг 30-50 публичных прайсов + crowd-correct). Banner-line discloses honestly: «Цены — среднее по {{region}}».
- Master CAN edit individual price inline by tapping the row → small modal with «Подходит» / «У меня дороже» / «У меня дешевле» quick-options OR free-text input. Edit overrides for THIS master only (master's `MasterService.price_override`).
- «Расскажу помощнику» opens a small free-text input + sends to owner queue (owner approves → service added to tenant catalog → master can re-select). NOT a self-serve catalog add path.

**Constraints**:
- Selections write to `MasterService` M2M with default `price_override = NULL` (inherit from catalog) or master's edited override
- AI guarantees ≥3 candidate services for any `primary_specialty` (catalog templates ensure this — per 11-category baseline)
- Minimum 1 service checked to proceed

### 5.3 Wizard screen 2 — Photo

```
┌────────────────────────────────────────┐
│ ← Ваше фото                            │
├────────────────────────────────────────┤
│                                        │
│   [фото placeholder + Загрузить]       │
│                                        │
│ Клиенты будут видеть его при выборе.   │
│                                        │
│ Совет: при дневном свете, на нейтральном│
│ фоне, лицо или плечи в кадре.          │
│                                        │
│ [Загрузить фото]  [Пропустить пока]    │
└────────────────────────────────────────┘
```

**Optional**: master can skip. Without photo, customer-side master detail shows generic placeholder. Photo can be added later in profile settings.

### 5.4 Wizard screen 3 — Bio (short)

```
┌────────────────────────────────────────┐
│ ← Коротко о себе                       │
├────────────────────────────────────────┤
│                                        │
│ [_____________________________________]│
│                                        │
│ Например: «5 лет в массаже, специали-  │
│ зируюсь на лимфодренаже, окончила XYZ» │
│                                        │
│ Не больше 280 символов.                │
│                                        │
│ [Сохранить]  [Пропустить пока]         │
└────────────────────────────────────────┘
```

**Optional**: master can skip. Default bio template suggests their primary_specialty + years_in_practice if known from invite metadata.

### 5.5 Wizard completion
After all 3 screens (some can be skipped except services):
```
┌────────────────────────────────────────┐
│ Услуги добавлены                       │
│                                        │
│ Дальше — расписание.                   │
│                                        │
│ [К расписанию →]                       │
└────────────────────────────────────────┘
```

### 5.6 Bot DM after profile minimum complete
Per [master-conversational-templates §5.2](./master-conversational-templates.md#52-invite-accepted--first-onboarding-step) variant:
```
Услуги сохранены. Теперь расписание — оно по умолчанию: пн–пт 10:00-19:00, сб 11:00-17:00, вс выходной. Подходит? [Открыть расписание]
```

### 5.7 Edge case — master abandons wizard mid-flow
Wizard state persists in DB. Returning master sees their progress. Bot DM nudges only ONCE per 24h with:
```
Остановились на услугах. Если что-то непонятно — спросите. Если передумали — отзовитесь {{owner_short_name}}.
```

After 7 days inactivity: status badge for owner becomes «🟠 Прогресс приостановлен». Owner can intervene manually.

### 5.8 Owner-side visibility
Master in M2 shows «🟠 Профиль: услуги ({{count}}) / фото ({{has_photo}}) / био ({{has_bio}})». Owner can see what's filled.

### 5.9 Events emitted
- `master.service.added` per service selection (event per row)
- `master.profile.updated` when photo/bio saved

### 5.10 Completion criterion for M2 → M3
At minimum: `master_service` row count ≥ 1. Master taps «К расписанию».

---

## 6. M3 — SCHEDULE_CONFIRM

### 6.1 State conditions
Profile minimum done. Schedule defaults applied per [salon-onboarding §Phase 4c](../handoffs/2026-05-17-salon-onboarding-handoff.md): пн-пт 10:00-19:00, сб 11:00-17:00, вс closed. Master sees + edits.

### 6.2 Mini App schedule confirm screen

```
┌────────────────────────────────────────┐
│ ← Ваше расписание                      │
├────────────────────────────────────────┤
│ По умолчанию для студии:               │
│                                        │
│ Пн  10:00 — 19:00                      │
│ Вт  10:00 — 19:00                      │
│ Ср  10:00 — 19:00                      │
│ Чт  10:00 — 19:00                      │
│ Пт  10:00 — 19:00                      │
│ Сб  11:00 — 17:00                      │
│ Вс  выходной                           │
│                                        │
│ Обед: 13:00 — 14:00 (по будням)        │
│                                        │
│ [Подходит как есть]  [Изменить]        │
└────────────────────────────────────────┘
```

«Изменить» opens [`schedule-editor-wireframes`](./schedule-editor-wireframes.md) W2-D working-hours editor.

### 6.3 Bot DM on schedule confirm
Per [master-conversational-templates §5.3](./master-conversational-templates.md#53-onboarding-completion-confirmation):
```
Готово — вы в команде студии.
Расписание готово, клиенты могут записываться. Утром буду присылать сводку на день.
```

### 6.4 Events emitted
- `schedule.working_hours.updated` per day if master adjusts from default
- `master.onboarding.profile_complete` (NEW — add to event-taxonomy §3.3)

### 6.5 Completion criterion for M3 → M4
Master taps «Подходит как есть» OR explicitly saves edited schedule.

---

## 7. M4 — CUSTOMER_VISIBLE

### 7.1 State conditions
- `Master.is_active = True`
- `Master.invite_status = 'accepted'`
- Profile minimum complete
- Schedule confirmed
- → master now bookable per booking endpoint check per [`manual-booking-spec.md`](./manual-booking-spec.md) §6 + [`attribution-policy.md`](./attribution-policy.md)

### 7.2 Master's first view of «production state» Mini App
Master opens Mini App, sees standard daily dashboard per [master-mobile-handoff §M1](../handoffs/2026-05-18-master-mobile-handoff.md). If no bookings yet:

```
┌────────────────────────────────────────┐
│ Сегодня · {{date}}                     │
├────────────────────────────────────────┤
│                                        │
│           [иконка пустоты]              │
│                                        │
│ Пока никто не записался.               │
│                                        │
│ Когда появится запись — придёт         │
│ уведомление и она появится здесь.      │
│                                        │
│ [Расписание]  [Профиль]                │
└────────────────────────────────────────┘
```

### 7.3 Owner-side visibility
Master now «🟢 Активен · ждёт записей» (or similar progress badge replacement).

### 7.4 No bot DM at M4 transition
Important: no «Поздравляем, вы активны!» message. The transition is operational, not celebratory. Master gets the booking digest next morning if any bookings landed.

### 7.5 Onboarding tip (optional, opt-in)
Mini App home shows ONE «tip card» for master's first 3 days post-M4:

```
┌────────────────────────────────────────┐
│ 💡 Знаете?                             │
│                                        │
│ Если что-то поменяется в графике —     │
│ {{owner_short_name}} можно попросить   │
│ через расписание (запрос изменения).   │
│                                        │
│ [Понятно]                              │
└────────────────────────────────────────┘
```

3 tip cards rotate (Q-MO5 — see open questions):
1. ScheduleChangeRequest mechanic
2. «Я болен сегодня» self-mark (Q-SC5)
3. Customer arrival ping settings

After 3 days OR after 3 tips dismissed: no more tips.

### 7.6 Events emitted
- `master.active` (NEW — add to event-taxonomy §3.3, fired when invite_status='accepted' AND profile_complete=True AND schedule_confirmed=True)

### 7.7 Completion criterion for M4 → M5
First BookingRequest with `master_id = this master` AND `status = CONFIRMED` is created.

---

## 8. M5 — FIRST_BOOKING_PENDING

### 8.1 State conditions
First booking exists. Master has been notified.

### 8.2 Bot DM (first booking notification — enhanced)
Standard new booking template per [master-conversational-templates §5.6](./master-conversational-templates.md#56-new-booking-notification-someone-just-booked-you) with one-time onboarding extra:

```
+1 запись на {{date_relative}} {{time}}
{{customer_first_name}} {{customer_last_initial}}. — {{service_short}}

(Это ваша первая запись здесь — {{owner_short_name}} держит кулаки 🤞)
```

The «первая запись» softening line shows ONCE only for M5 transition. After this, all booking notifications are standard per master-templates §5.6.

**Forbidden**:
- ❌ More than 1 message celebrating the first booking
- ❌ «Не волнуйтесь, всё получится!» — projects nervousness master may not feel
- ❌ Emoji on first booking line (the 🤞 is owner-warm, single instance)

### 8.3 Pre-arrival context for first customer
Per [master-conversational-templates §5.5](./master-conversational-templates.md#55-customer-pre-arrival-context-surface) — same as steady-state. No special «first customer» treatment.

### 8.4 Events emitted
- `master.first_booking_received` (NEW — analytics-only event for owner-side first-booking dashboard)

### 8.5 Completion criterion for M5 → M6
Master marks first booking as completed (via [master-mobile-handoff §M1](../handoffs/2026-05-18-master-mobile-handoff.md) booking actions).

---

## 9. M6 — FIRST_BOOKING_DONE

### 9.1 State conditions
First booking has `status = COMPLETED`. Customer flow continues per [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) §8 (no-show edge case ruled out by COMPLETED).

### 9.2 Bot DM to master (gentle, optional)

```
Первый клиент закрыт — {{customer_first_name}} получила сегодняшнюю процедуру.

Если что-то узнаете от клиента полезного для будущего ({{customer_first_name}} вернётся через ~6 недель) — можете оставить заметку в её карте.

[Открыть карту]   [Пропустить]
```

**Forbidden**:
- ❌ «Поздравляем с первой записью!» — celebratory tone wrong for master at work
- ❌ Demand notes («обязательно оставьте впечатления»)
- ❌ Cross-sell suggestion for next customer

### 9.3 Note-taking flow
If master taps «Открыть карту» → notes input UI per master-mobile-handoff. Notes are master-private + master-only fields populated to customer's Wellness Profile Layer 4 (Service History) reactions block.

### 9.4 Events emitted
- `master.first_booking_completed`

### 9.5 Completion criterion for M6 → M7
7 calendar days post M1 (invite accepted) elapsed. NOT tied to bookings — even if no bookings happen in week 1, M7 fires for the digest.

---

## 10. M7 — FIRST_WEEK_COMPLETE

### 10.1 State conditions
7 days post `Master.accepted_at`. This stage marker is calendar-driven, not activity-driven.

### 10.2 Bot DM — first-week digest

**Voice anchor**: Functional + Calm + slight warmth (one-time moment)

```
Первая неделя в студии «{{salon_name}}»:

Записей: {{count}} (проведено: {{completed_count}})
Активные клиенты: {{unique_customers}}
{{1_pattern_insight_if_relevant}}

Дальше всё привычно — утром буду присылать сводку на день. Спросите что-то, если будут вопросы.
```

**`pattern_insight` examples** (only if data supports):
- «Самые загруженные часы — 14:00-17:00»
- «{{popular_service}} забронировали 3 раза»
- (if zero bookings) «Пока тихо — первая неделя бывает разной»
- (omit line if nothing notable; never fabricate)

### 10.3 Forbidden in first-week digest
- ❌ Performance comparison («у других мастеров больше записей»)
- ❌ Earnings/payment disclosure (separate billing flow, not in scope here)
- ❌ «Постарайтесь увеличить» — push-to-perform tone
- ❌ Marketing exuberance even if numbers good

### 10.4 Owner-side visibility
Master moves to «🟢 Активен» (no longer flagged as «onboarding»). Removed from any «новый мастер — присмотреться» owner dashboard widget.

### 10.5 Mini App tip cards stop
3-tip rotation from M4 ends if not already exhausted. No more onboarding tips after M7.

### 10.6 Events emitted
- `master.onboarding.completed` (NEW — fires at M7 transition; payload: days_to_first_booking, first_week_bookings_count, etc.)

### 10.7 Post-M7 state
Master is fully settled. Daily flows per master-mobile-handoff + master-conversational-templates apply. No onboarding-specific overrides.

---

## 11. Edge cases + alternate paths

### 11.1 Invite expires (14 days no acceptance per Q-MM9)

**State at M0 timeout**:
- `Master.invite_status` transitions `pending → expired` automatically (daily Celery beat)
- Bot DM to master: NONE (we have no MaxUser; can't reach them via MAX anyway — they never opened)
- Owner-side: badge changes to «🔴 Истёк · перепригласить»

**Owner re-invite**: tap «Перепригласить» → status returns to `pending` + new bot DM sent + `invited_at` refreshed.

### 11.2 Owner cancels invite mid-onboarding (rare)

Owner can cancel invite at any pre-M4 stage. Effects:
- `Master.invite_status = 'cancelled'`
- `Master.is_active = False`
- Bot DM to master:
  ```
  {{owner_short_name}} отозвал(а) приглашение в студию «{{salon_name}}». Если это недоразумение — свяжитесь с ним/ней напрямую.
  ```
- Master's existing wizard progress preserved in DB (not deleted) — if owner re-invites later, progress restores
- Event: `master.invite.cancelled` per event-taxonomy §3.3

### 11.3 Master quits mid-onboarding (before M4)

Master can't formally «quit» pre-M4 (not active yet). Two paths:
- Master tells owner directly via human channel → owner cancels invite per §11.2
- Master goes silent → eventually owner cancels OR system flags «🟠 Прогресс приостановлен»

### 11.4 Master quits post-M4 (active master leaves)

Out of scope here — covered by [master-management-handoff §M4 «Master archival»](../handoffs/2026-05-18-master-management-handoff.md) and [`master-conversational-templates §6.6` (master leaving exit dialog)](./master-conversational-templates.md). Just note: M7 was completed; archival flow takes over.

### 11.5 Multi-tenant master (Q-MC7)

Master accepts invite at Salon A → goes through M0-M7 for Salon A. Later receives invite from Salon B → starts SEPARATE M0-M7 for Salon B.

**State per tenant**: each `(tenant_id, max_user_id)` tuple has independent Master row, independent invite/profile/schedule. Master sees TWO separate Mini App contexts when switching salons.

**No cross-tenant data sharing**:
- Photo: separate upload per tenant (or single MAX profile photo as fallback if no per-tenant photo)
- Services: separate selection per tenant (different catalogs)
- Schedule: separate per tenant (master might work mornings at A, evenings at B)

**Onboarding completion is per-tenant**: completing M0-M7 at Salon A doesn't auto-complete at Salon B.

### 11.6 Solo master = owner

If owner is the only master in their salon (per [salon-onboarding-handoff Q6](../handoffs/2026-05-17-salon-onboarding-handoff.md)):
- M0-M1 are skipped (no invite — owner adds self as master during salon onboarding Phase 4c)
- M2 profile setup is integrated into salon onboarding wizard, not separate
- M3-M7 still apply but in owner's tone, not master-tone

This is one person wearing two hats. Onboarding deduplicates: services setup happens once (during salon onboarding), profile setup happens once.

### 11.7 Master tries to book customer service via Mini App as user

If during M0-M7 master tries to BOOK as a customer (using a different role context):
- Allowed BUT: `attribution_metadata.actor_type='master'` → `booking_source='test_admin'` → `billable=False` per [`attribution-policy.md`](./attribution-policy.md) §6
- Prevents «master tests bot → wrong charge» scenario

### 11.8 Master loses MAX access mid-onboarding

Master's MAX account deleted/locked/changed handle. Effects:
- `MaxUser` row remains, but delivery to handle fails
- Bot DM events emit `conversation.message.delivery.failed`
- Owner sees alert
- Manual recovery: owner cancels invite + re-creates with new handle

### 11.9 Master skips photo + bio entirely

Allowed. Customer-facing master detail shows:
- Placeholder photo (generic icon)
- Bio: «{{master_first_name}} — {{primary_specialty}}» (auto-generated from invite metadata)
- Suggested at end of M7 digest:
  ```
  Кстати, если добавите фото — клиенты чаще выбирают мастеров с фотографиями. Можно из настроек профиля.

  [Настройки]   [Не сейчас]
  ```

### 11.10 Mass-onboarding (chain salon, 10+ masters at once)

Owner imports/invites 10+ masters batch. Per-master flow still M0-M7 independently. Owner sees aggregate progress widget:
```
🟢 Активных: 6/10
🟡 В процессе: 3
🔴 Не отвечают: 1

[Список]
```

Master-side: no batch awareness, each master experiences independent onboarding.

---

## 12. Owner-side onboarding progress visibility

Aggregate widget for owner dashboard (per [owner-conversational-templates §6.1 daily digest](./owner-conversational-templates.md)):

```
Мастера на onboarding:

✓ Маша — на старте 5 дн назад, ждёт первой записи
✓ Ольга — первая неделя завершена, активна
🟡 Лена — застряла на услугах 3 дня назад
🔴 Татьяна — приглашение истекает завтра

[Открыть всех]
```

Detailed per-master view shows M0-M7 progress bar:
```
Маша — onboarding: [✓ ✓ ✓ ✓ ─ ─ ─ ─]
                    M0  M1  M2  M3  M4  M5  M6  M7
```

---

## 13. Permissions matrix

| Action | Owner | Admin | Master (self) | Master (other) |
|---|---|---|---|---|
| Send invite | ✅ | ✅ | ❌ | ❌ |
| Cancel invite | ✅ | ✅ | n/a | ❌ |
| Re-invite | ✅ | ✅ | ❌ | ❌ |
| View own onboarding progress | n/a | n/a | ✅ | ❌ |
| Edit own profile | n/a | n/a | ✅ | ❌ |
| Confirm own schedule | n/a | n/a | ✅ | ❌ |
| View other master's onboarding progress | ✅ | ✅ | ❌ | ❌ |
| Force-archive masters mid-onboarding | ✅ | ❌ (requires owner) | n/a | ❌ |

---

## 14. Events emitted summary

Per [`event-taxonomy.md`](./event-taxonomy.md) §3.3 (existing) + additions:

| Stage transition | Event | New / existing |
|---|---|---|
| owner creates invite | `master.invited` | existing |
| owner cancels invite | `master.invite.cancelled` | existing |
| 14-day no-accept | `master.invite.expired` | existing |
| master taps deeplink | `master.invite.accepted` | existing |
| service added during wizard | `master.service.added` (per row) | existing |
| profile minimum complete | `master.onboarding.profile_complete` | **NEW — add to taxonomy** |
| schedule confirmed | `master.onboarding.schedule_confirmed` | **NEW — add to taxonomy** |
| M3 → M4 transition | `master.active` | **NEW** |
| first booking received | `master.first_booking_received` | **NEW** |
| first booking completed | `master.first_booking_completed` | **NEW** |
| 7 days post M1 elapsed | `master.onboarding.completed` | **NEW** |

Subscribers:
- Analytics dashboard — all events for time-to-active funnel
- Owner notification engine — `master.onboarding.completed` triggers «Маша завершила первую неделю» insight
- Master-mobile UI — `master.active` enables full feature set

---

## 15. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| «Поздравляем с присоединением!» on M1 | Marketing-exuberant for work context | Functional welcome |
| Daily nudge to finish wizard | Push-to-perform | One nudge per 24h max, then patient silence |
| Photo required to proceed | Friction for shy masters | Photo always optional |
| Bio required to proceed | Same | Bio always optional |
| Schedule defaults non-editable | Master has no agency | Always editable in wizard |
| «You haven't booked yet — promote yourself!» | Owner's job, not master's | NEVER suggest self-promotion to master |
| Cross-comparison «другие мастера сделали…» | Performance pressure | No comparisons on individual basis |
| Earnings reveal during onboarding | Out of scope; complicated payouts | Separate billing flow post-M7 if at all |
| Tip cards continuing past 3 days | Becomes nag | Hard cap 3 tips OR 3 days |
| Demanding feedback after first booking | Master is at work | Optional notes invite, not requirement |
| M7 digest with negative framing on slow week | Demotivating | Neutral observation; «бывает разной» |
| Booking customer's wellness profile leaked during onboarding tip | Privacy violation | Master sees their own data only |
| Onboarding restart on profile-edit later | Drag | Profile edits post-M7 are routine, no re-onboarding |
| Multi-tenant master gets unified onboarding | Confuses contexts | Each tenant separate flow per §11.5 |

---

## 16. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-MO1** | M0 → M1 expiry — fixed 14 days or per-tenant configurable? | Fixed 14 days MVP per Q-MM9; per-tenant v1.1+ | PM | 🟢 |
| **Q-MO2** | If master accepts invite but never opens Mini App (M1 stuck), bot DM nudge? | YES — one nudge at 24h post-M1 («продолжим?»); silent after | UX | 🟢 |
| **Q-MO3** | Should photo upload work via Mini App camera OR only from gallery? | Both — Mini App invokes MAX webview file picker; covers both | Eng | 🟢 |
| **Q-MO4** | Bio AI-suggestion based on services + invite metadata? | YES generate suggested text in wizard; master edits or accepts | UX + AI | 🟡 |
| **Q-MO5** | Tip cards rotation — 3 tips, but which 3? Configurable per tenant? | Fixed 3 platform-level MVP: ScheduleChangeRequest / sick-self-mark / arrival-ping settings. v1.2+ tenant-customizable | UX | 🟢 |
| **Q-MO6** | M3 schedule confirm — explicit «Подходит» tap required even if master makes no changes? | YES — explicit confirm. Tap = «I'm aware these are my hours». Prevents passive acceptance + later «я не выбирал такие часы» disputes | UX | 🟢 |
| **Q-MO7** | First-booking ping in M5 should include «owner держит кулаки 🤞» line — appropriate or saccharine? | One-time, contextual moment. Reframes as warm but not gushing. If feedback shows cringe, remove. | UX | 🟢 |
| **Q-MO8** | M7 digest delivery time — fixed (e.g., morning of day 8) or aligned with master's typical morning? | Master's local 9:00. Aligned with morning digest §6.4 pattern. Same time predictability. | UX | 🟢 |
| **Q-MO9** | Should owner be notified on each master onboarding stage transition? | NO MVP — aggregate widget §12 sufficient. v1.1+ opt-in per-master alerts. | UX | 🟢 |
| **Q-MO10** | Mass-onboarding (chain salon, 10+ masters) — different UX or same? | Same per-master MVP; aggregate widget for owner §11.10. v1.2+ batch tools (template-apply schedule defaults to all) | PM | 🟢 |
| **Q-MO11** | Solo master = owner case — what's M7 digest content if they're also the only owner? | Combined «Первая неделя студии» digest with owner-tone (partner-tone per [owner-conversational §6.2 weekly digest](./owner-conversational-templates.md)); skip master-tone variant | UX | 🟢 |
| **Q-MO12** | Master uses «Расскажу помощнику» (escape hatch for service not in AI's proposed list) — what happens? | AI captures free-text → routes to owner approval queue (NOT auto-add to catalog). Owner reviews; if accepted, AI adds service to tenant catalog via template-match-or-new-template flow + notifies master. Never master-self-serve catalog edit. | PM | 🟡 |
| **Q-MO16** | AI «pre-checks» 2-3 services for primary_specialty — what's the rule for which to pre-check? | Use platform-level vertical-template defaults: e.g. for «массажист» — classical + lymph drainage are pre-checked (most common per market data); for «бровист» — окрашивание + ламинирование. Override per-tenant if salon's catalog skews differently. Track usage analytics — adjust defaults Phase 2+. | PM + AI | 🟡 |
| **Q-MO17** | Master rejects ALL pre-checked services — pre-check 0 services and master starts from blank? | YES — show all available services unchecked. Add gentle prompt: «Не подошло из стандартных? Что обычно делаете?» → routes to free-text → owner approval per Q-MO12. | UX | 🟢 |
| **Q-MO13** | Onboarding analytics — what metrics expose to founder (cohort analysis)? | Time-to-M4 (median, p90), time-to-M7, drop-off rate per stage, photo/bio completion rates. For founder-50 cohort review per [decisions-log r4](../decisions-log.md). | PM + Analytics | 🟡 |
| **Q-MO14** | If master never gets a booking in 7 days post-M4 — M7 still fires with «pока тихо»? | YES — M7 is calendar-driven, not activity. Even silent weeks get a digest with neutral framing. | UX | 🟢 |
| **Q-MO15** | Master's first booking is a cancel within 1h (likely test by owner) — does M5 → M6 transition count or skip? | If `actor_type=owner/admin` (test_admin booking) — DOESN'T count toward M5/M6 transition. M5 fires only on customer-initiated first booking. | Eng + UX | 🟡 |

---

## 17. Cross-document linkage

- [`master-conversational-templates.md`](./master-conversational-templates.md) §5.1-5.3 — templates for M0-M3
- [`master-conversational-templates.md`](./master-conversational-templates.md) §5.6 — M5 first-booking notification (one-time variant)
- [`master-conversational-templates.md`](./master-conversational-templates.md) §6.6 — exit dialog (post-M7 archive case)
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) §6.1 — owner daily digest with onboarding masters list
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) §6.2 — weekly digest where M7 events surface
- [`product-ux-vision.md`](./product-ux-vision.md) — single-assistant identity across M0-M7
- [`event-taxonomy.md`](./event-taxonomy.md) §3.3 — events emitted per stage, with 6 NEW events to add
- [`information-architecture.md`](./information-architecture.md) — Mini App master-side IA (master uses subset of 5 surfaces)
- [`schedule-editor-wireframes.md`](./schedule-editor-wireframes.md) — W2-D editor reused in M3 schedule confirm
- [`attribution-policy.md`](./attribution-policy.md) §6 — Q-MO15 actor_type rule (master-as-customer test)
- [`../handoffs/2026-05-18-master-management-handoff.md`](../handoffs/2026-05-18-master-management-handoff.md) — owner side of invite, archival
- [`../handoffs/2026-05-18-master-mobile-handoff.md`](../handoffs/2026-05-18-master-mobile-handoff.md) — daily UX post-M7
- [`../handoffs/2026-05-17-salon-onboarding-handoff.md`](../handoffs/2026-05-17-salon-onboarding-handoff.md) Phase 4c — invite flow originates here
- [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md) — Mini App state patterns reused throughout

---

## 18. What this unblocks

- **Phase 2 master-mobile implementation** — full lifecycle locked: invite → onboarding → first week → steady state
- **Onboarding wizard frontend** — 3-step wizard (services / photo / bio) with state machine
- **Per-stage bot DM emissions** — engineering knows what to send + when
- **Owner-side aggregate visibility** — onboarding progress widget contract
- **Event-taxonomy expansion** — 6 NEW events added with clear payload (engineering can emit before consumer needs them)
- **Cohort analytics** — founder-50 review per Q-MO13 has time-to-active baseline
- **Multi-tenant master support** — per-tenant separation rules locked
- **Solo master case** — M2 dedup with salon onboarding wizard

## 19. What this does NOT unblock

- ❌ Daily master-mobile UX after M7 (covered in master-mobile-handoff)
- ❌ Master archival/reactivation (master-management-handoff)
- ❌ Schedule change requests post-onboarding (schedule-management-handoff)
- ❌ Multi-language onboarding (Phase 4+)
- ❌ Skip persona-conformance linter on first-week digest — must pass like all generated copy
- ❌ Auto-archive masters who stall — owner discretion always

---

## 20. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-18 |
| Master-mobile frontend lead | ☐ | |
| Master-management backend lead | ☐ | |
| AI prompt engineering (per-stage templates) | ☐ | |
| Privacy / Legal (Q-MO15 actor_type rule + photo storage rules) | ☐ | |

## Last verified
2026-05-18 (initial draft, M0-M7 lifecycle locked for Phase 2 master-mobile implementation)
