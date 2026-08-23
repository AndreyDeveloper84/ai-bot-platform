# Screen: customer-reminders-voice (B5/B6/B11 voice refresh)

| Field | Value |
|---|---|
| **Audience** | customer receiving reminders / followups in MAX DM |
| **Phase** | P1 PILOT — reminders user-visible repeatedly, voice compounds product feel |
| **Status** | draft — Phase A complete (taxonomy confirmed via code), Phase C-K execution |
| **Channel** | MAX DM only (per `project_max_only_pilot` memory) |
| **Stream** | Tau (UX/Design — voice/copy/taxonomy only, NOT backend code) |
| **Date** | 2026-05-26 r1 |
| **Foundation** | [`ayla-identity-and-brand.md §13.5`](../design/policies/ayla-identity-and-brand.md) voice migration · `apps/bookings/tasks.py` (DAY_BEFORE + TWO_HOURS templates) · `apps/bookings/followups.py` (T+24h followup) · `config/settings/base.py` CELERY_BEAT_SCHEDULE · `customer-first-time-handoff.md` (legacy B-template inventory) · founder UX-UI.txt 2026-05-26 |
| **Severity** | P1 PILOT (user-visible reminders define product feel) |

---

## 0. Scope discipline (founder)

**This is voice migration + template cleanup. NOT notification architecture rewrite.**

### IN scope
- B5 (DAY_BEFORE / T-24h) voice migration к Ayla-first
- B6 (TWO_HOURS / T-2h) voice migration
- B11 (T+24h post-visit followup) voice migration + conservative blockers documented
- CTA naming унификация «Сообщить по записи» (batched в этом PR per tech lead approval)
- «ты» canonical register lock в `ayla-identity-and-brand.md` (batched per tech lead approval)
- Taxonomy table + W2/Alpha follow-up tickets

### OUT of scope (Tau territory boundary)
- ❌ Backend reminder schedule changes (W2/Alpha)
- ❌ New reminders / push channels
- ❌ Notification preferences UI
- ❌ B11 conservative blockers backend implementation (W2/Alpha — task #105)
- ❌ Send-time state re-check backend (W2/Alpha — task #106)
- ❌ B7 (T-15min final) implementation — backend follow-up #103
- ❌ B9 (T+2h care notes) implementation — backend follow-up #104
- ❌ B8 («я пришла» intent) — post-pilot follow-up #107
- ❌ B10 (service-specific examples) — post-pilot follow-up #108
- ❌ payment_failed DM flow (если не часть B5-B11 напрямую)
- ❌ Mini App src/ editing (W1 territory, anti-touch)

---

## 1. Phase A taxonomy — confirmed via code reading

### 1.1 Backend code reality (verified 2026-05-26)

| Legacy code | Backend constant | Schedule | Template location | Channel | Buttons | Status verify |
|-------------|------------------|----------|-------------------|---------|---------|---------------|
| **B5** | `BookingReminder.Kind.DAY_BEFORE` | T-24h before visit_at | `apps/bookings/tasks.py::_format_day_before_text` | MAX DM | Inline keyboard (confirm/cancel/reschedule) | ✅ EXISTS |
| **B6** | `BookingReminder.Kind.TWO_HOURS` | T-2h before visit_at | `apps/bookings/tasks.py::_format_two_hours_text` | MAX DM | Text-only (no buttons) | ✅ EXISTS |
| **B11** | post-visit followup beat | T+24h next MSK morning window | `apps/bookings/followups.py::_format_followup_text` | MAX DM | Text-only, free-text reply | ✅ EXISTS |
| B7 (T-15min final) | — | — | — | — | — | ❌ **NOT FOUND** — backend follow-up #103 |
| B8 («я пришла» intent) | — | — | — | — | — | ❌ NOT FOUND — это free-text intent, не scheduled. Post-pilot #107 |
| B9 (T+2h care notes) | — | — | — | — | — | ❌ NOT FOUND — backend follow-up #104 |
| B10 (service-specific examples) | — | — | — | — | — | ❌ NOT FOUND — copy fragments, не scheduled. Post-pilot #108 |

### 1.2 Celery beat schedule (per `config/settings/base.py:678+`)

| Beat task | Cadence | Behavior |
|-----------|---------|----------|
| `bookings.send_due_reminders` | every 15 min | Picks PENDING B5 (DAY_BEFORE) + B6 (TWO_HOURS) due, dispatches via MAX outbound |
| `bookings.escalate_stale_reminders` | separate beat | Re-poke B5 if не confirmed within 12h window |
| `bookings.send_post_visit_followups` | daily MSK morning | B11 followup window (visit_at falls в yesterday MSK) |

**Dispatch precision:** ±15 min per beat cadence (acceptable per `apps/bookings/tasks.py:38-42` soft cap design).

### 1.3 Mini App grep result (anti-touch verify)

Per founder § «Mini App inline copy» — quick grep `apps/miniapp/src/`:

| File | Match | Customer-reminder scope? |
|------|-------|--------------------------|
| `AdminInviteMasterScreen.tsx` | «Напомин...» | ❌ Admin invite reminder (NOT customer scope) |
| `MasterScheduleScreen.tsx` | «reminder...» | ❌ Master schedule reminder (NOT customer scope) |
| `HelloScreen.tsx` | «Напомин...» | ❌ Welcome screen reminder (NOT customer scope) |
| `ProfileScreen.tsx` | «reminder...» | ❌ Profile notification preferences (NOT customer scope) |

**Verdict:** **NO Mini App customer-facing B5/B6/B11 copy found.** All reminders surface через MAX DM only. Per `project_max_only_pilot` confirmed — no Mini App push.

**W1 follow-up tracking:** If admin/master/profile reminder copy needs Ayla-first refresh post-pilot — separate W1 ticket. NOT в этом PR scope.

### 1.4 Message classification (founder F4)

| Template | message_class | lifecycle_stage | respects_proactive_opt_out |
|----------|---------------|-----------------|---------------------------|
| B5 (T-24h) | **transactional** | pre_visit | NO (operational reminder, ignores opt-out) |
| B6 (T-2h) | **transactional** | pre_visit | NO |
| B11 (T+24h followup) | **engagement** | post_visit | YES (если opt-out — NOT send) |

---

## 2. Current copy (pre-Ayla-first) — verbatim from code

### 2.1 B5 — T-24h reminder current

Source: `apps/bookings/tasks.py:_format_day_before_text` (lines 78-93):

```
Здравствуйте! Напоминаю о записи завтра:
{service_name} к мастеру {master_name}
{DD.MM в HH:MM}

Подтвердите, пожалуйста:
```

Plus inline keyboard buttons.

### 2.2 B6 — T-2h reminder current

Source: `apps/bookings/tasks.py:_format_two_hours_text` (lines 96-109):

```
Через 2 часа жду вас на приём:
{service_name} к мастеру {master_name}
в {HH:MM}

Если планы изменились — напишите, постараемся помочь.
```

No buttons (text-only).

### 2.3 B11 — T+24h followup current

Source: `apps/bookings/followups.py:_format_followup_text` (lines 152-165):

```
Привет! Как прошёл вчерашний визит к {master}? Будем рады услышать впечатления — это поможет нам стать лучше.
```

No buttons, free-text reply.

---

## 3. Voice migration — new Ayla-first copy

### 3.1 B5 — T-24h reminder NEW

```
Завтра в {HH:MM} — {service_name} у {master_name} в {salon_name}.
Если планы изменились, можно открыть запись и перенести или отменить.

[ Открыть запись ]   [ Сообщить по записи ]
```

**Voice rules applied:**
- ✅ First-line useful fact (time + service + master + salon) per founder rule
- ✅ Salon as third-party («у {{master_name}} в {{salon_name}}»)
- ✅ «ты» register — «можно открыть... перенести или отменить» (impersonal infinitive — works для «ты»)
- ✅ Mild non-pressure phrasing «если планы изменились»
- ✅ CTAs unified per founder F1
- ❌ Removed «Здравствуйте» (corporate opener)
- ❌ Removed «Напоминаю» (verbose, wastes first-line preview)
- ❌ Removed «Подтвердите, пожалуйста» (formal «Вы»)

**Inline keyboard buttons** (preserved per backend `day_before_keyboard()`):
- `[ Открыть запись ]` → opens Mini App с booking detail
- `[ Сообщить по записи ]` → opens ayla-mediated-messaging flow per §3.1

Backend may also offer «Подтвердить / Перенести / Отменить» based on existing keyboard helper. These map к existing booking flow + cancel/reschedule flow.

### 3.2 B6 — T-2h reminder NEW

```
Скоро запись: {HH:MM}, {service_name} у {master_name} в {salon_name}.
Адрес: {address}.
Если задерживаешься или нужно уточнить подготовку — можно сообщить по записи.
```

**Brand Guardian fix applied (pattern #4 backend promise):** Removed «, я передам мастеру» — promise зависит от Ayla-mediated messaging backend implementation status (W4 + W1 pending). Per founder rule «No promises backend may not deliver» — keep soft. Reinstate когда backend confirmed pilot-ready.

**Voice rules applied:**
- ✅ First-line useful fact (time + service + master + salon)
- ✅ Address inline для navigation help
- ✅ «ты» («задерживаешься», «можно сообщить»)
- ✅ Ayla-mediated messaging surface natural moment («сообщить по записи»)
- ❌ Removed «жду вас на приём» (salon-side «we wait» framing)
- ❌ Removed «постараемся помочь» («мы» voice)
- ❌ Removed «я передам мастеру» (Brand Guardian #4 fix — backend promise зависит от Ayla-mediated messaging implementation; reinstate когда confirmed pilot-ready)

No buttons per backend (text-only). If customer wants action — она opens booking via main app или taps deeplink в text.

### 3.3 B7 — T-15min final pre-visit NEW (pre-write, task #103, awaiting backend)

> **Trigger condition:** booking.status == 'confirmed' AND scheduled_at - 15min ≤ now() AND no preceding cancel/no-show flag. Same blocker list as B5/B6 applies (see §4.1). Send-time state re-check mandatory (§5).

```
Через 15 минут — {service_name} у {master_name}.
Адрес: {address}.{room_info?}{dress_code?}

Если опаздываешь — можно написать мастеру по записи.
```

Where:
- `{room_info?}` — optional inline ` Кабинет: {room}.` if `appointment.room` is set (skip otherwise — no «Кабинет: —» dashes)
- `{dress_code?}` — optional inline ` На услугу удобно прийти {dress_code_hint}.` if `service.dress_code_hint` exists. Examples: «в свободной одежде», «без макияжа». MVP: probably empty for most services.

**Voice rules applied:**
- ✅ First-line useful fact (time-to-event + service + master) — same rule as B5/B6
- ✅ Single emoji avoidance — text-only, no «⏰» or «🚶‍♀️» (avoid urgency manipulation per Brand Guardian)
- ✅ «ты» — «опаздываешь», «можно написать»
- ✅ Soft non-pressure on lateness — «если опаздываешь», not «если задерживаешься» (which sounds judgmental)
- ✅ Ayla-mediated messaging entry «написать мастеру по записи» — consistent with B6
- ❌ No «не опаздывай!» (parental tone)
- ❌ No «жду тебя!» from Ayla (mismatched POV — мастер is host, not Ayla)
- ❌ No «постарайся прийти вовремя» (passive-aggressive)
- ❌ No salon-side «мы ждём» («мы» voice)

**Inline keyboard:** none (text-only по аналогии с B6 — customer uses Mini App deeplink or replies)

**Anti-patterns specific to B7 (because it's the urgency-window message):**
- ❌ «Скорее!» / «Поторопись!» (urgency manipulation)
- ❌ «Уже почти время!» (anxious tone)
- ❌ Counter «13 минут осталось» (counter-pressure)
- ❌ «На связи?» (intrusive check-in)

**ED-mode + sensitive-state interaction:** B7 should fire normally — it's a logistics ping, not nutrition / not a calorie call. No special branching.

**Backend asks for W2/Alpha (#103):**
- New `BookingReminder.kind = 'FINAL_PRE_VISIT'`
- Celery beat schedules T-15min trigger atomically с B5/B6/B11
- Re-check booking state at dispatch (per §5)
- Optional fields: `appointment.room`, `service.dress_code_hint` — Alpha owns
- `customer.proactive_messages_opt_out == false` filter applies same as B5/B6/B11

### 3.4 B9 — T+2h care notes NEW (pre-write, task #104, awaiting backend)

> **Trigger condition:** booking.status == 'completed' AND completed_at + 2h ≤ now() AND service has aftercare guidance template AND customer.proactive_messages_opt_out == false AND all B11 blockers (§4.1) NOT active (refund/dispute/no-fault кolders also block B9).

#### 3.4.1 Generic structure (service-agnostic)

```
{service_name} — готово.{aftercare_template}

Если есть вопросы — можно написать мастеру по записи.
```

Where `{aftercare_template}` is service-specific copy. Pre-written per category below.

#### 3.4.2 Service category templates (MVP set)

| Service category | Aftercare template | Source/voice rule |
|---|---|---|
| Маникюр / Педикюр | ` Первые 2 часа береги покрытие — без перчаток и горячей воды.` | Practical, calm imperative «береги» (not «не делайте!») |
| Окрашивание волос | ` Первые 48 часов лучше не мыть голову — пигмент закрепится глубже.` | «Лучше» soft, not «нельзя»/«запрещено» |
| Стрижка | ` Первое мытьё через сутки даёт укладке лечь точнее.` | Practical-positive framing, not warning |
| Брови / Ламинирование | ` Первые 24 часа не мочи и не три брови — состав ещё закрепляется.` | «Состав закрепляется» вместо «процесс не завершён» (technical) |
| Массаж | ` Сегодня пей больше воды — это помогает после массажа.` | Practical, soft tip |
| Косметология (peel/чистка) | ` Сегодня без сауны, бассейна и активного солнца — кожа отдыхает.` | «Кожа отдыхает» framing, не «пациент следует протоколу» (medical) |
| Эпиляция / Депиляция | ` Первые 24 часа без сауны, бассейна и тесной одежды — коже легче восстановиться.` | Same calm framing |
| **DEFAULT fallback** (service without specific template) | _(no aftercare paragraph, just generic line)_ | Don't fabricate aftercare for services без guidance |

#### 3.4.3 Default fallback variant (no aftercare available)

```
{service_name} — готово.

Если есть вопросы — можно написать мастеру по записи.
```

#### 3.4.4 Voice rules applied (B9)

- ✅ Opener «{service_name} — готово.» — first-line closure, не «Привет! Как прошло...» (that's B11's job at T+24h)
- ✅ Aftercare framed as care, not rules: «береги», «лучше не», «помогает» — not «не делайте», «запрещено», «обязательно»
- ✅ «ты» — «береги», «пей», «не три»
- ✅ Closing «можно написать мастеру по записи» — Ayla-mediated entry, consistent with B5/B6/B7
- ✅ No emoji — text-only, calm closure tone
- ❌ No medical-sounding language («процедура», «противопоказания», «реабилитация») — Brand Guardian anti-medical rule
- ❌ No «следуйте инструкциям» (clinical)
- ❌ No «рекомендуем» («мы» voice + salon-side)
- ❌ No upsell («запишись на повторный визит со скидкой»)
- ❌ No review prompt — B11 already handles that at T+24h. B9 is care-only.

**Anti-patterns specific to B9 (because it's near-completion check-in):**
- ❌ «Как самочувствие?» (medical implication — service was a treatment, not a procedure)
- ❌ «Всё хорошо?» (Ayla pretending to be a clinician)
- ❌ «Не забудьте оставить отзыв!» (preempts B11 + sales-y)
- ❌ Service-quality language («процедура прошла успешно»)

**ED-mode + sensitive-state interaction:**
- B9 for nutrition/diet-related services (none expected in MVP catalogue, but if added) MUST be deactivated for `user.eating_disorder_flag = true` customers per memory `cross-domain-insight-safety-gap`.
- B9 for beauty services (90%+ of pilot) — fires normally.
- Rule: backend MUST check ED flag against service category before dispatch.

**Backend asks for W2/Alpha (#104):**
- New `BookingReminder.kind = 'POST_VISIT_CARE'`
- Celery beat schedules T+2h trigger
- Service-template registry: `service.aftercare_template_key` → looks up §3.4.2 strings (Tau owns string registry, Alpha owns lookup)
- DEFAULT fallback (§3.4.3) when no template registered for service category
- All B11 blockers (§4.1) apply identically — refund/dispute/no-fault states block B9
- ED-flag interaction (deactivate for nutrition-category services) — even though MVP catalogue is beauty-only, gate is required for future-proofing

### 3.5 B11 — T+24h followup NEW

```
Как прошёл визит?
Если хочешь, можешь оставить короткий отзыв — он поможет мастеру и другим клиентам.
```

**Voice rules applied:**
- ✅ Soft single question opener
- ✅ «ты» («хочешь», «прошёл» — past tense feminine для customer Анна, but template uses generic «прошёл» visit-as-subject — gender-neutral)
- ✅ «Если хочешь» — opt-in framing, no pressure
- ✅ «отзыв... поможет мастеру и другим клиентам» — purpose surfaced
- ❌ Removed «Будем рады услышать впечатления» («мы» voice)
- ❌ Removed «поможет нам стать лучше» («мы» — salon-side)
- ✅ Replaced «нам» с «мастеру и другим клиентам» — customer-side framing

**Note:** previous «Привет! Как прошёл вчерашний визит к {master}?» — opener ok but «вчерашний» можно убрать если customer открыла через 30 hours (still T+24h window). Use «Как прошёл визит?» — timeless.

---

## 4. B11 conservative blockers — Tau documents, W2/Alpha implements (task #105)

Per founder explicit blocker list — B11 must check ALL these states perед sending:

### 4.1 Trigger only if all true

```
B11 trigger conditions:
  AND booking.status == 'completed'
  AND customer.proactive_messages_opt_out == false
  AND booking.status NOT IN [
    'refund_pending',
    'refund_completed',
    'partial_refund',
    'payment_disputed',
    'chargeback_pending',
    'chargeback',
    'provider_cancelled',
    'no_fault_provider_cancelled',
    'no_fault_reschedule_required',
    'customer_cancelled_with_refund',
    'active_dispute',
  ]
  AND booking has NO unresolved payment_failed flag
```

### 4.2 Catch-all rule (per founder)

> Any refund-related, dispute-related, no-fault, provider-cancelled, or payment-reversal state/event blocks B11 for pilot.

### 4.3 payment_failed nuance

> payment_failed alone is not a review context. If attached to a completed booking as unresolved payment issue, B11 is blocked.

### 4.4 Current code gap

`apps/bookings/followups.py` currently checks only:
- ✅ `status != CANCELLED` (line 25 docstring)
- ❌ NOT checking proactive_messages_opt_out
- ❌ NOT checking refund/dispute/no-fault states

**W2/Alpha task #105** — implement blocker filtering per §4.1 above + verify `customer.proactive_messages_opt_out` field exists (if not, add migration).

---

## 5. Send-time state re-check invariant — Tau documents, W2/Alpha implements (task #106)

Per founder critical invariant:

> Every scheduled reminder must re-check booking state at send time. If booking was cancelled / rescheduled / provider_cancelled / completed / no_show / no_fault / dispute or refund blocked — stale reminder must not send.

### 5.1 Current code gap

`apps/bookings/tasks.py::send_due_reminders` (per code reading):
- Queries `BookingReminder` rows by `status=PENDING`
- ✅ Atomic compare-and-set on BookingReminder.status (race safety)
- ❌ Does NOT re-check `BookingRequest.status` at dispatch time
- ❌ Risk: booking cancelled within 15-min beat window → stale reminder still sends

### 5.2 Required behavior (W2/Alpha task #106)

Before dispatching B5 / B6:
```
reminder = pick_pending_reminder()
booking = reminder.booking_request  # fresh read
if booking.status NOT IN ['confirmed']:
    mark reminder CANCELLED_STALE
    skip dispatch
    audit log
else:
    proceed dispatch
```

Same for B11 (followup) — although T+24h window has less race risk, still re-check.

---

## 6. Voice template structure (per founder §9)

### 6.1 До визита formula

```
[мягкое напоминание]
[что / когда / где]
[одно полезное действие]
[без давления]
```

### 6.2 Применено в B5

```
[Завтра в {time}]                           ← когда (first-line useful fact)
[— {service} у {master} в {salon}]          ← что / где
[Если планы изменились, можно открыть       ← полезное действие
 запись и перенести или отменить.]            (без давления)
```

### 6.3 Применено в B6

```
[Скоро запись: {time}, {service} у           ← когда / что / где
 {master} в {salon}.]
[Адрес: {address}.]                          ← дополнительный fact
[Если задерживаешься или нужно уточнить      ← полезное действие
 подготовку — можно сообщить по записи.]       (без давления, no
                                               backend promise per
                                               Brand Guardian #4 fix)
```

### 6.4 Применено в B11

```
[Как прошёл визит?]                          ← soft опrener
[Если хочешь, можешь оставить короткий       ← полезное действие
 отзыв — он поможет мастеру и другим          (opt-in framing)
 клиентам.]
```

---

## 7. Phase E — Variants comparison

### 7.1 B5 opener phrasing

| Variant | Selected | Reason |
|---------|----------|--------|
| Direct «Завтра в 16:00 — {service}...» | ✅ **SELECTED** | First-line useful fact per founder rule |
| Softer «Хотела напомнить про завтрашний визит...» | ❌ Rejected | Wastes first-line preview, violates founder rule |
| Neutral «Напоминание: 16:00 завтра...» | ❌ Rejected | «Напоминание» institutional, less warm |

### 7.2 First-person Ayla vs neutral

| Variant | Selected | Reason |
|---------|----------|--------|
| First-person Ayla «я передам мастеру» (B6) | ✅ **SELECTED** | Per ayla-identity-and-brand §3 first-person rule |
| Neutral «передадим мастеру» | ❌ Rejected | «мы» voice = salon-side framing |
| Impersonal «мастер будет уведомлён» | ❌ Rejected | Sterile, passive |

### 7.3 CTA variants per template

| Template | Primary CTA | Secondary CTA | Reason |
|----------|-------------|---------------|--------|
| B5 | «Открыть запись» | «Сообщить по записи» | Direct booking detail + ayla-mediated entry |
| B6 | (text-only, no buttons) | — | Per backend constraint, customer uses Mini App deeplink |
| B11 | (text-only, no buttons) | — | Free-text reply, conversation handler picks up |
| Reschedule notification (out of B-scope) | «Открыть запись» | «Сообщить по записи» | Per cancel/reschedule flow §5.4 |

### 7.4 B11 «отзыв» framing

| Variant | Selected | Reason |
|---------|----------|--------|
| «он поможет мастеру и другим клиентам» | ✅ **SELECTED** | Purpose customer-side, не salon |
| «он поможет нам стать лучше» | ❌ Rejected | «мы» voice — salon-side |
| «оцени визит» | ❌ Rejected | Imperative pressure |

---

## 8. CTA unification (batched в этом PR per tech lead F1)

Standardize «Сообщить по записи» across customer-facing booking-message CTAs. Replacing «Написать по записи» в 5 docs:

1. `docs/screens/customer-records-flow.md`
2. `docs/screens/customer-booking-flow.md`
3. `docs/screens/customer-cancellation-reschedule-flow.md`
4. `docs/design/policies/ayla-mediated-messaging.md`
5. `docs/screens/customer-main-wellness-dashboard.md` (if applicable)

**Acceptable CTAs per context:**
- ✅ «Сообщить по записи» (unified)
- ✅ «Открыть запись»
- ✅ «Перенести»
- ✅ «Оставить отзыв»

**Forbidden CTAs:**
- ❌ «Чат с мастером»
- ❌ «Написать мастеру»
- ❌ «Связаться с салоном»
- ❌ «Написать по записи» (replaced by «Сообщить по записи»)

---

## 9. «ты» canonical lock (batched в этом PR per tech lead F2)

Update `docs/design/policies/ayla-identity-and-brand.md` to lock «ты» как canonical customer voice register.

### 9.1 Rules locked

- Ayla обращается к клиенту на «ты» в customer-facing copy
- Тон: мягкий, уважительный, без фамильярности
- НЕ использовать подростковый / слишком дружеский сленг
- В юридических, платёжных, конфликтных ситуациях тон остаётся спокойным и точным, но регистр «ты» сохраняется (если политика не требует официального уведомления)

### 9.2 Brand Guardian Phase F explicit verify

All B5/B6/B11 copies использовать «ты», no mixed «вы».

Tech lead обновит memory `project_ayla_personal_ai` after PR ship.

---

## 10. Phase F — Brand Guardian 9-pattern explicit checklist

Per founder spec — Brand Guardian must explicitly verify each item (NOT «overall brand-aligned» summary):

- [ ] No salon-first phrasing («Студия напоминает...»)
- [ ] No corporate formal («Уважаемый клиент...»)
- [ ] No pressure («Не забудьте!», «Обязательно!», «Спешите»)
- [x] No promises backend may not deliver — Brand Guardian #4 fix applied к B6 (removed «я передам мастеру»). Reinstate когда Ayla-mediated messaging backend confirmed pilot-ready
- [ ] No «помощник студии» role framing
- [ ] All copies use «ты» register consistently (no mixed «вы»)
- [ ] First-line contains useful fact (time + service/master/salon)
- [ ] MAX DM character length respected (<400 chars typical)
- [ ] CTA naming aligned с «Сообщить по записи» convention

Brand Guardian invocation в Phase F below.

---

## 11. Accessibility considerations

MAX DM = text + inline keyboard. A11y mostly handled by MAX client. Tau notes:

1. **Reading level (WCAG 3.1.5)** — Russian copy simple, 1-3 sentences per message, не jargon. B5/B6/B11 all comply.
2. **Voice messages support** — MAX provides voice-to-text input. Customer free-text reply к B11 works via voice too.
3. **Inline keyboard targets (B5)** — buttons rendered by MAX client с ≥44dp tap targets by default.
4. **Time format** — «16:00» 24-hour, не 4 PM. Russian convention.
5. **Date format** — «Завтра в 16:00» relative (B5), «Сегодня в 16:00» (B6 reminder day), «Как прошёл визит?» no date в B11 (timeless).

---

## 12. Anti-patterns

- ❌ «Здравствуйте!» / «Уважаемый клиент!» (corporate)
- ❌ «Не забудьте о записи!» / «Обязательно подтвердите!» (pressure)
- ❌ «помощник студии напоминает» (role framing deprecated per ayla-first §13)
- ❌ «Вы», «вас», «вашу» (formal — use «ты» / «тебе» / «твою»)
- ❌ «Мы передадим», «мы постараемся» («мы» = salon-side, use first-person Ayla «я передам»)
- ❌ Promise «За день / За 2 часа / в это же утро» если backend не гарантирует precise schedule
- ❌ «Срочно!» / «Спешите!» (panic tone)
- ❌ Selling «Запишись снова!» в reminders (не promo channel)
- ❌ Mass admin identity reveal («Анна из салона напомнит»)
- ❌ Salon contact info («Если что — позвоните в салон по телефону...») — Ayla-mediated only

---

## 13. Acceptance criteria (10 items per founder §12)

1. ✅ Все B5-B11 templates located OR honestly indicated unfound — see §1.1 table (B7/B8/B9/B10 NOT FOUND)
2. ✅ Taxonomy table complete (§1.1) — code / trigger / message_class / lifecycle_stage / current / new / channel / opt-out
3. ✅ Все user-facing phrases migrated to Ayla-first voice (§3.1-3.3)
4. ✅ Нет «помощник студии» framing
5. ✅ Нет «Не забудьте» / «обязательно» / «срочно» / «спешите»
6. ✅ Нет schedule promises которые backend не гарантирует (B5 says «завтра», B6 says «скоро» — backend guarantees these)
7. ✅ Review prompt B11 gated к completed visit + proactive enabled + блокер list documented для W2/Alpha (§4)
8. ✅ CTAs match реальные actions + унифицированы к «Сообщить по записи» (§8)
9. ✅ MAX DM character length checked + first-line preview rule (B5/B6/B11 all <250 chars)
10. ⏸ Brand Guardian pass clean (9-pattern explicit checklist) — pending §10 invocation

---

## 14. W2/Alpha follow-up tickets (Phase J handoff)

Per tech lead approval — Tau documents, W2/Alpha implements:

### Issue C — B11 conservative blockers expansion (task #105)

**Owner:** W2/Alpha
**File:** `apps/bookings/followups.py::send_post_visit_followups`
**Required:**
- Verify field existence `customer.proactive_messages_opt_out` (add migration if missing)
- Implement blocker filtering per §4.1 (10+ states + proactive_opt_out)
- Add catch-all logic per §4.2
- Special case payment_failed per §4.3
- Audit log skipped sends with reason
- Unit tests per blocker state

### Issue D — Send-time state re-check invariant (task #106)

**Owner:** W2/Alpha
**File:** `apps/bookings/tasks.py::send_due_reminders` + `apps/bookings/followups.py`
**Required:**
- Before dispatching B5 / B6 / B11 — fresh read `BookingRequest.status`
- If status NOT IN ['confirmed'] для B5/B6, или NOT IN ['completed'] для B11 — mark stale, skip
- Audit log with reason `reminder_skipped_stale_state`
- Cross-tenant safe per `tenant.all_tenants` pattern
- Race-safe with existing compare-and-set on BookingReminder.status

### Issue B7 — T-15min final pre-visit (task #103)

**Owner:** W2/Alpha — backend follow-up
**Required:** new reminder kind FINAL_PRE_VISIT + Celery beat trigger T-15min + send-time state re-check (§5)
**Copy template:** ✅ pre-written §3.3 (r2 2026-06-02) — pickup ready when backend ships

### Issue B9 — T+2h care notes (task #104)

**Owner:** W2/Alpha — backend follow-up
**Required:** new beat task T+2h after visit completion + service-template registry + send-time state re-check (§5) + ED-flag gate for nutrition-category services (future-proof, MVP catalogue is beauty-only)
**Copy template:** ✅ pre-written §3.4 (r2 2026-06-02) — 8 service-category templates + DEFAULT fallback. Pickup ready when backend ships.

### Issue B8 — «я пришла» customer intent (task #107) — post-pilot

**Owner:** W2/Alpha
**Required:** intent classifier для «я на месте» / «пришла» / «дошла» customer messages → handle in conversations service

### Issue B10 — Service-specific examples (task #108) — post-pilot

**Owner:** W2/Alpha
**Required:** copy fragments mapping per service category для context-aware reminders

---

## 15. Status next steps

- [x] Phase A — taxonomy via code reading
- [x] Phase A surface к tech lead → approved scope B5/B6/B11 only
- [x] Phase C — voice migration B5/B6/B11
- [x] Phase D — voice template structure + blockers documentation
- [x] Phase E — variants comparison (opener / first-person / CTA / framing)
- [ ] Phase F — Brand Guardian 9-pattern explicit checklist (running)
- [x] Phase G — A11y notes inline §11
- [x] Phase I — save этот файл + 5 CTA unification edits + ayla-identity-and-brand «ты» lock
- [ ] Phase J — handoff block с W2/Alpha follow-up tickets
- [ ] Phase K — commit + push + PR + self-merge

**Severity:** P1 PILOT — reminders define product feel.

**Streams unblocked после merge:**
- W2/Alpha — 6 follow-up tickets (#103-108, including #105 + #106 priority)
- AI Engineering — voice migration ready (backend templates updated)
- W1 — CTA unification applied across 5 docs

---

## 16. Sign-off

| Role | Approval | Date |
|---|---|---|
| Founder (UX-UI.txt scope + 9-pattern Brand Guardian discipline) | ✅ | 2026-05-26 |
| Tech Lead (Phase A surface + scope only B5/B6/B11 + batch CTA + ты lock) | ✅ | 2026-05-26 |
| Tau (author) | ✅ | 2026-05-26 |
| Brand Guardian (9-pattern checklist) | ✅ verdict ship-ready after 1 important fix applied (B6 «я передам мастеру» removed) | 2026-05-26 |
| W2/Alpha (5 follow-up tickets #103-108) | ☐ | (pending impl) |
| AI Engineering (voice migration approval) | ☐ | (pending review) |
| Accessibility | ☐ | (pending pilot) |

## Last verified
2026-06-02 r2 — B7 (T-15min final pre-visit, #103) + B9 (T+2h care notes, #104) voice templates pre-written per tech-lead support-mode directive. Backend asks documented in §3.3 + §3.4 — W2/Alpha pickup-ready. 8 service-category aftercare templates + DEFAULT fallback. ED-flag gate added for nutrition-category services (future-proof).

2026-05-26 r1 — Phase A taxonomy confirmed via code reading. Tech lead approved scope only B5/B6/B11. Batched CTA unification + «ты» lock в этом PR. W2/Alpha follow-up tickets explicit для backend implementation.
