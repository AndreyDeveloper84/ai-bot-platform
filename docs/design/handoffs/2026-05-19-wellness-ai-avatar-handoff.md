# Wellness AI Avatar Module — engineering handoff

**Date:** 2026-05-19 r2 (Ayla-first voice-sweep)
**Status:** Engineering-ready — Phase 3 wellness module (strategic «WOW» retention feature)
**Reads:** [`../policies/ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md), [`../policies/ayla-memory-and-personalization.md`](../policies/ayla-memory-and-personalization.md), [`../policies/wellness-input-modules.md`](../policies/wellness-input-modules.md) §7, [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md), [`../policies/core-user-states.md`](../policies/core-user-states.md), [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md), [`../policies/information-architecture.md`](../policies/information-architecture.md), [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md), [`../policies/ayla-emergency-fallback-policy.md`](../policies/ayla-emergency-fallback-policy.md), [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md), [`../policies/master-conversational-templates.md`](../policies/master-conversational-templates.md), [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md), [`../policies/customer-profile-management-ux.md`](../policies/customer-profile-management-ux.md)

> Ports [wellness-input-modules §7](../policies/wellness-input-modules.md) AI Avatar (до/после photo progression) module to engineering-ready handoff. THIS is the wellness OS «WOW» feature — customer sees real photo-tracked progress over time with AI commentary. Strict privacy-first design. Independent app scope: `apps/wellness_avatar/`.

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](../policies/ayla-identity-and-brand.md) memory 2026-05-19: AI Avatar photos are **Ayla's memory of user** per [`ayla-memory-and-personalization §9`](../policies/ayla-memory-and-personalization.md) — cross-tenant. Client-side AES-GCM encryption preserved; server never sees plaintext; 24h key purge on revoke. `HUMAN_LOCKED` references → emergency fallback per [`ayla-emergency-fallback-policy §3`](../policies/ayla-emergency-fallback-policy.md). AI voice uses Ayla per [`ayla-identity-and-brand §2`](../policies/ayla-identity-and-brand.md).

---

## 0. Why this exists

### Strategic context

AI Avatar is **the highest-emotional-moment** feature in wellness journeys per [wellness-input-modules §7.2](../policies/wellness-input-modules.md#7-module-6--ai-avatar-before--after) — «вижу прогресс» reframes the customer-salon relationship from transactional to outcome-driven. Sticky retention. Differentiator vs all booking platforms (no competitor offers this).

This handoff turns the strategic spec into shippable Phase 3 implementation.

### The gap

[wellness-input-modules §7](../policies/wellness-input-modules.md#7-module-6--ai-avatar-before--after) describes:
- What it does (high-level)
- Why it matters (strategic)
- UX flow (sketch ASCII)
- AI inference (rules)
- Privacy (highest sensitivity)
- Anti-patterns

But doesn't specify:
- Consent dialog full template + UX
- Model fields, types, validators
- Photo storage architecture (encryption, deletion mechanics)
- Per-zone privacy controls
- Master-grant flow detailed
- AI comparison generation pipeline
- Insights view layouts
- API contracts
- Per-state behavior
- Activation timing
- Mini App screen flow (camera, capture, review, save, compare)

Engineering improvisation here = liability (photo data + PII + privacy). Spec MUST be locked.

### The promise

Single source for `apps/wellness_avatar/` Phase 3 implementation. Engineering reads this + ships without ambiguity.

---

## 1. Scope

### IN
- New Django app `apps/wellness_avatar/` (separate from `apps/wellness/` mood module for scope isolation)
- 4 data models: `AvatarConsent`, `AvatarPhoto`, `AvatarComparison`, `AvatarMasterGrant`
- Activation flow + consent dialog with explicit privacy disclosure
- 6 zones (face / general body / waist / hips / nails / hair) with per-zone consent
- Photo capture UX (Mini App: camera + gallery, with retake / discard / save)
- Photo storage architecture (encrypted at rest, redactable on customer revoke)
- Comparison view (slider between two photos same-zone)
- AI commentary rules (honest, never fake improvements, conservative)
- Insights / progress timeline
- Master-grant flow (customer grants temporary master access for pre-procedure planning)
- 4 API endpoints (consent, photo CRUD, comparison render, master-grant)
- Per-state behavior (when AI may prompt for photo)
- Throttling (max 1 photo per zone per 14 days; anti-OCD pattern)
- Privacy enforcement at API + storage layers
- Customer revoke flow (1-tap, irreversible per zone + full)
- Events emitted per [event-taxonomy](../policies/event-taxonomy.md) §3.6
- WCAG 2.2 AA on capture + comparison UI

### OUT
- AI-generated «как будете выглядеть после» fake previews — **explicitly forbidden** per [wellness-input-modules §7.2](../policies/wellness-input-modules.md)
- Filters / beautification — forbidden (destroys honesty)
- Public sharing / social features — privacy violation
- Cross-tenant photo sharing — never
- Multi-customer comparison («другие с похожей кожей») — privacy + scope
- Time-lapse video generation — Phase 5+ per [wellness-input-modules §7](../policies/wellness-input-modules.md#phasing)
- Body composition / scale integration (Phase 4+ wearables)
- Customer-pays premium tier gating (Phase 3+ business model)
- Anti-aging / weight-loss medical claims — out of scope ethically + legally

---

## 2. Strategic constraints — non-negotiable

These are the «cannot-be-compromised» rules for AI Avatar. Engineering review rejects any PR that violates them.

### 2.1 Honesty mandate (most important)
- AI **never fabricates** visible changes when none exist
- AI **never enhances** photos via beautification / filters
- Comparison view shows **raw photos**; AI commentary is text-only annotation
- When AI cannot detect significant change → it says so explicitly («Изменения тонкие — продолжайте текущий курс»)

### 2.2 Privacy hierarchy (strict)
- Photos NEVER stored unencrypted at rest
- Photos NEVER accessible cross-tenant
- Photos NEVER accessible to salon (owner / admin / master) without explicit customer grant per-zone per-master
- Photos NEVER accessible to platform team without explicit legal hold + audit trail
- Photos hard-deleted within 24 hours of customer revoke (NO 30-day soft-delete unlike other wellness modules)
- Customer can export ALL photos in raw form via [customer-profile-management-ux §6.3](../policies/customer-profile-management-ux.md) data export request

### 2.3 Anti-shame anchor
- No body-shaming framing in any UI copy
- No «недостаточный прогресс» messaging
- No body composition KPIs (weight, BMI, body fat) on this surface
- Customer drives cadence; AI does NOT prompt for «time for next photo»

### 2.4 No medical claims
- AI commentary uses observational language («Цвет кожи в области лба более ровный»)
- AI NEVER says «лечится», «исчезла проблема», «эффективная процедура для X»
- For health-adjacent zones (e.g., visible skin issues), AI routes to medical specialist per [conversational-ux-framework §7.2](../policies/conversational-ux-framework.md)

---

## 3. Activation flow

### 3.1 Eligibility (gates)

Customer cannot activate AI Avatar if any:
- `consent.ai_messaging = false` (master switch OFF per [notification-preferences §3.2](../policies/notification-preferences-ux.md))
  - **Exception**: Path A self-discovery WORKS because customer is actively asking for the module
- `core_user_state ∈ {DORMANT, HUMAN_LOCKED active conversation}` per [conversation-ownership-policy](../policies/conversation-ownership-policy.md)
- Customer is < 18 years old (verified during salon onboarding) — **NEVER** allow AI Avatar for minors regardless of guardian consent (legal + ethical)
- Tenant in PAUSED / SUSPENDED billing state
- Customer's MAX account suspended

### 3.2 Activation triggers (Phase 3 launch — Path A only)

**Path A — Self-discovery in Mini App** (only path Phase 3):
Customer navigates Профиль → Самочувствие → AI Avatar card → toggle ON → consent dialog §4.

**Paths B, C, D — DEFERRED to Phase 4+**:
- B: Post-multi-visit gentle offer (after 3+ visits AND ≥ 1 cosmetology-category service)
- C: Master-side suggestion («покажу до/после для этого курса?») — requires customer accept
- D: Customer self-requests during DM («можешь сохранять фото моего прогресса?»)

Why Phase 3 = Path A only: privacy-first launch. Customer is the sole initiator. Reduces risk of «AI suggested it and I felt pressured».

### 3.3 Activation events emitted
- `wellness.consent.module.granted` per [event-taxonomy §3.6](../policies/event-taxonomy.md#36-wellness-domain) with `module_name='avatar'`, `granted_via='profile_settings'`
- NEW: `wellness.avatar.zone_consent_granted` (per-zone consent capture)

---

## 4. Consent dialog (multi-step — privacy-critical)

### 4.1 Why multi-step (vs single dialog)
Most modules use single consent dialog (mood §3.2). AI Avatar uses **3-step wizard** because:
1. Customer needs to understand the trust commitment (photos are highest-sensitivity data)
2. Zone selection is privacy-meaningful (each zone is independent grant)
3. Going «full all-zones in one tap» is too easy → encourages friction-justified per [wellness-input-modules §7](../policies/wellness-input-modules.md) «highest sensitivity» framing

### 4.2 Step 1 — Intro + privacy facts

```
┌──────────────────────────────────────────┐
│ Помощник прогресса                       │
├──────────────────────────────────────────┤
│ Хотите видеть прогресс наглядно?         │
│                                          │
│ Я могу сохранять ваши фото до и после    │
│ процедур, чтобы потом показывать как     │
│ меняется состояние — лица, тела, кожи.   │
│                                          │
│ ── Что важно ──                          │
│                                          │
│ ✓ Фото видите только вы                  │
│ ✓ Студия НЕ видит ваши фото              │
│   (мастер увидит только если вы          │
│   разрешите для конкретной зоны)         │
│ ✓ Шифрование при хранении                │
│ ✓ Удалить — в один клик                  │
│ ✓ Удаление в течение 24 часов            │
│   (не 30 дней как у других модулей)      │
│                                          │
│ Что я НЕ делаю:                          │
│ ✗ Не использую фильтры                   │
│ ✗ Не приукрашиваю изменения              │
│ ✗ Не показываю «как вы будете выглядеть» │
│   — никакого AI-генерируемого будущего   │
│                                          │
│ Если согласны — выберем зоны на          │
│ следующем шаге.                          │
│                                          │
│ [Не сейчас]   [Дальше →]                 │
└──────────────────────────────────────────┘
```

### 4.3 Step 2 — Zone selection

Customer must pick at least 1 zone:

```
┌──────────────────────────────────────────┐
│ ← Какие зоны интересны?                  │
├──────────────────────────────────────────┤
│ Каждую зону можно подключить и           │
│ выключить отдельно.                      │
│                                          │
│ ☐ Лицо (anti-age, состояние кожи)       │
│ ☐ Тело — общий вид                       │
│ ☐ Талия / живот                          │
│ ☐ Бёдра                                  │
│ ☐ Состояние ногтей                       │
│ ☐ Состояние волос                        │
│                                          │
│ (Можно добавить ещё зоны позже           │
│  в настройках Самочувствия.)             │
│                                          │
│ Выбрано: {{count}}                       │
│                                          │
│ [Назад]   [Дальше →]                     │
└──────────────────────────────────────────┘
```

**Constraints**:
- Minimum 1 zone selected to proceed
- Maximum 6 zones (full list shown above)
- No «выбрать все» bulk action (each zone is intentional grant)

### 4.4 Step 3 — Confirmation + first capture prompt

```
┌──────────────────────────────────────────┐
│ ← Подтверждение                          │
├──────────────────────────────────────────┤
│ Подключаемые зоны: {{N}}                 │
│ • Лицо                                   │
│ • Тело — общий вид                       │
│                                          │
│ Согласие действует до момента отзыва.    │
│ Отозвать можно в любое время.            │
│                                          │
│ Чтобы было что сравнивать — сделаем      │
│ первое фото каждой зоны прямо сейчас?    │
│                                          │
│ [Подключить и сделать первые фото]       │
│ [Подключить без фото (сделаю позже)]     │
│ [Назад]                                  │
└──────────────────────────────────────────┘
```

### 4.5 Outcomes

#### Tap «Подключить и сделать первые фото»
- Create `AvatarConsent(customer, tenant, granted=True, granted_at=NOW)`
- Create `AvatarConsent_Zone` row per selected zone (granted_at, granted_via='wizard')
- Emit events §3.3
- Navigate to Photo capture screen §5 for first zone

#### Tap «Подключить без фото»
- Same as above except no immediate capture
- Show success toast: «Подключено. Сделать первые фото — в Самочувствии → Помощник прогресса.»
- Navigate back to Самочувствие tab

#### Tap «Не сейчас» (step 1)
- NO record created (NOT even partial)
- Customer can re-open consent later

### 4.6 Voice anchor (consent dialog)
Per [conversational-ux-framework](../policies/conversational-ux-framework.md): Warm + Calm + Premium-but-accessible + **extra-Honest** (privacy stakes high).

---

## 5. Photo capture flow

### 5.1 Capture screen

```
┌──────────────────────────────────────────┐
│ ← Фото зоны: Лицо                        │
├──────────────────────────────────────────┤
│                                          │
│   [полноэкранный preview камеры]         │
│                                          │
│ Совет: при дневном свете, без макияжа,   │
│ нейтральное выражение, тот же ракурс,    │
│ что и в прошлый раз.                     │
│                                          │
│ ── Прошлое фото для повторения ──        │
│ [миниатюра 80×80 поверх preview]         │
│                                          │
│ Дата: [Сегодня]                          │
│ Контекст (опц.): [_______________]        │
│   • после процедуры? которой?            │
│   • курс лечения / поездка / etc.        │
│                                          │
│ [📷 Снять]  [📁 Из галереи]               │
│                                          │
│ [Отмена]                                 │
└──────────────────────────────────────────┘
```

### 5.2 Post-capture review

```
┌──────────────────────────────────────────┐
│ ← Проверьте фото                         │
├──────────────────────────────────────────┤
│                                          │
│   [фото full-size]                       │
│                                          │
│ ── Сравнение с прошлым ──                │
│   [если есть прошлое — side-by-side     │
│    или slider preview уменьшенный]       │
│                                          │
│ Контекст: {{context_value_or_empty}}     │
│                                          │
│ [🔄 Переснять]  [💾 Сохранить]            │
│                                          │
│ [Отмена]                                 │
└──────────────────────────────────────────┘
```

### 5.3 Save mechanics
- Photo bytes encrypted client-side via Web Crypto API (AES-GCM) before upload
- Encryption key derived from customer's MAX user_id + tenant_id + salt (per-customer per-tenant)
- Backend stores encrypted blob; encryption key NEVER stored server-side
- Customer's Mini App holds decryption capability via session
- On customer logout / MAX account loss → photos remain encrypted; recoverable only if customer reauths

**Implementation note**: this is privacy-paranoid by design. Trade-off: if customer loses MAX account permanently, photos become un-decryptable (acceptable — customer's own data was theirs to manage).

### 5.4 Throttling
- Max 1 photo per zone per 14 days (anti-OCD pattern; prevents «каждый день мониторю»)
- 14-day window resets on save; customer can retake same day (overwrites latest within window)
- Per-zone independent throttle

### 5.5 Photo metadata
Beyond encrypted blob, server stores (unencrypted, for indexing):
- `customer_id`, `tenant_id`, `zone`, `taken_at`, `context_label` (free text < 280 chars), `width_px`, `height_px`, `file_size_bytes`, `created_at`, `encryption_iv`

---

## 6. Comparison view

### 6.1 Entry points
- From Photo Library timeline view (Самочувствие → Помощник прогресса → zone → list)
- From AI insight notification («Помощник заметил изменения — посмотреть?»)

### 6.2 Side-by-side comparison

```
┌──────────────────────────────────────────┐
│ ← Прогресс: Лицо · 6 месяцев             │
├──────────────────────────────────────────┤
│ [Слайдер сравнения]                      │
│ Январь  ◀────────●────▶  Июнь           │
│                                          │
│ [фото слева]  vs  [фото справа]          │
│ (или один поверх другого с slider)       │
│                                          │
│ ── Помощник заметил ──                   │
│                                          │
│ • Тонус кожи лучше, особенно в области   │
│   лба и щёк.                             │
│ • Стало меньше тени под глазами.         │
│ • Контур лица стал чуть чётче.           │
│                                          │
│ За этот период было:                     │
│ • 4 чистки лица                          │
│ • 2 мезотерапии                          │
│ • Курс масок (3 недели)                  │
│                                          │
│ [Поделиться]   [Удалить]   [Записаться]  │
└──────────────────────────────────────────┘
```

### 6.3 AI commentary generation

#### When fires
- Customer opens comparison view (lazy: generate on-demand, cache result)
- After every NEW photo save (eager: pre-generate comparison vs previous photo for this zone)

#### Rules
- Compare ONLY two photos: most recent + customer-chosen prior
- Generate 1-3 observation lines, each ≤ 15 words
- Each line must be supported by visible difference (computer vision confidence ≥ 0.7)
- If no confident observations: «Изменения тонкие — иногда хороший знак, что текущее состояние стабильно.»
- If photos are too different in conditions (lighting, angle, lens): «Сложно сказать — освещение и ракурс разные. Постарайтесь повторить условия.»
- NEVER claim percentage improvements
- NEVER claim medical results
- ALWAYS observational tone

#### Service correlation
- Pull customer's bookings between two photo timestamps in this zone-relevant category
- List as «За этот период было» factual list
- DON'T claim service caused observation (correlation ≠ causation)

#### Forbidden in commentary
- ❌ «Кожа теперь идеальная»
- ❌ «Морщины исчезли» (medical-claim adjacent)
- ❌ «На 30% лучше» (fake precision)
- ❌ «Лучший результат за всё время»
- ❌ «Поздравляю с прогрессом!» (sycophantic)
- ❌ Service recommendation as cause («благодаря {{procedure}} ...»)

### 6.4 «Поделиться» action
Customer can share comparison via MAX system share. Generated image is:
- Composite of two photos + AI commentary text
- Customer's own name / nothing identifying (no «Customer Ольга К.»)
- Watermark «Прогресс в студии {{salon_name}}» minimal corner mark — only if customer enabled in advanced settings

Default share: silent, no studio attribution.

### 6.5 «Удалить» action
- Modal warning: «Удалить ОБА фото из сравнения навсегда? Или только одно?»
- Options: «Только левое» / «Только правое» / «Оба»
- After confirm: hard-delete within 1 hour (NOT 24 hours — explicit user-requested deletion)
- Comparison record removed; future comparison view for this period falls back to next available pair

### 6.6 «Записаться» action
- Deeplink to F2 master picker filtered by zone-relevant service category
- Per [customer-first-touch §3](../policies/customer-first-touch-and-mini-app-states.md) normal booking flow

---

## 7. Insights / Timeline view

### 7.1 Timeline view (per zone)

```
┌──────────────────────────────────────────┐
│ ← Лицо · Хронология                      │
├──────────────────────────────────────────┤
│ 12 фото за 6 месяцев                     │
│                                          │
│ ● Июнь · 18 июня                         │
│   [миниатюра]  «после мезотерапии»       │
│                                          │
│ ● Май · 30 мая                           │
│   [миниатюра]  «после чистки»            │
│                                          │
│ ● Май · 14 мая                           │
│   [миниатюра]  пусто                     │
│                                          │
│ ● Апрель · 8 апреля                      │
│   [миниатюра]  «начало курса»            │
│                                          │
│ ...                                      │
│                                          │
│ [+ Сделать новое фото]                   │
│ [Сравнить ▾] (выбрать пары)              │
└──────────────────────────────────────────┘
```

### 7.2 Cross-zone overview

Not on this screen MVP — could be added Phase 3.5+ as «Самочувствие в целом» dashboard combining mood + avatar + (water) + (sleep). Out of scope here.

---

## 8. Master-grant flow

### 8.1 Why
Master may legitimately benefit from seeing customer's photo state before procedure (e.g., dermatologist sees recent skin photos before consultation). Per [wellness-input-modules §7 «special: master/practitioner role»](../policies/wellness-input-modules.md#7-module-6--ai-avatar-before--after) — explicit customer consent required, audited.

### 8.2 Customer-initiated grant

Customer in Mini App → Профиль → Самочувствие → AI Avatar → zone → «Поделиться с мастером»:

```
┌──────────────────────────────────────────┐
│ Поделиться с мастером                    │
├──────────────────────────────────────────┤
│ Какому мастеру дать просмотр?            │
│                                          │
│ ◯ Маша (Лимфодренаж, Массаж)             │
│ ◉ Ольга (Чистка лица, Мезотерапия)       │
│ ◯ Лена (Стрижка, Окрашивание)            │
│                                          │
│ Какую зону?                              │
│ ☑ Лицо                                   │
│ ☐ Тело — общий вид                       │
│                                          │
│ Срок доступа:                            │
│ ⦿ До конца следующего визита              │
│ ◯ 30 дней                                │
│ ◯ Постоянно (до отзыва)                  │
│                                          │
│ Ольга увидит только выбранные зоны       │
│ и только эту длительность.               │
│                                          │
│ [Отмена]   [Подтвердить]                 │
└──────────────────────────────────────────┘
```

### 8.3 Master-side view
After grant, master sees in their Mini App pre-arrival context per [master-conversational-templates §5.5](../policies/master-conversational-templates.md#55-customer-pre-arrival-context-surface) extended row:
```
👤 Ольга К. в 14:00 · Чистка лица
   ...
   📸 Доступ к фото-прогрессу: лицо (до конца визита)
   [Посмотреть]
```

«Посмотреть» opens limited view of customer's lice timeline. Master CANNOT save / download / share photos. Audit logged per view event.

### 8.4 Grant expiry
- «До конца следующего визита»: auto-revoke 1 hour after customer's next `booking.completed` with this master
- «30 дней»: auto-revoke 30 days after grant_at
- «Постоянно»: indefinite until customer revokes manually

### 8.5 Audit
Every master view of customer photo emits `wellness.avatar.master_viewed_photo` event with: master_id, customer_id, zone, photo_id, viewed_at. Stored 7 years (operational + compliance audit).

Customer can view their grant history + audit at any time («Кто и когда смотрел?» in Профиль → Самочувствие → AI Avatar).

### 8.6 Customer revoke grant
1-tap revoke. Master loses access immediately. Audit event emitted.

### 8.7 Master cannot request grant (Phase 3)
Master CANNOT initiate request («Можно ли посмотреть ваши фото?»). Phase 4+ may add this with strict caps.

---

## 9. Per-state behavior matrix

When AI may prompt customer about Avatar (rare — customer-driven cadence is the default):

| Customer state | AI proactive Avatar prompt? |
|---|---|
| DISCOVERED | NEVER |
| EXPLORING | NEVER |
| PROBLEM_SEEKING | NEVER |
| READY_TO_BOOK | NEVER |
| POST_VISIT (after major cosmetology procedure) | OPTIONAL one-time prompt at T+14d «можно показать прогресс — добавить фото зоны Y?» — only if module activated AND zone matches procedure |
| ACTIVE_REGULAR | Only customer-initiated; AI silent |
| AT_RISK_DRIFTING | NEVER (don't pressure during drift) |
| DORMANT | NEVER |
| HUMAN_LOCKED | NEVER (admin owns) |

**Default**: customer drives cadence entirely. AI is silent until customer engages.

---

## 10. Data models

### 10.1 `AvatarConsent`

Tracks customer's overall AI Avatar consent + per-zone consent.

```python
class AvatarConsent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.OneToOneField('customers.Customer', on_delete=CASCADE, related_name='avatar_consent')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')

    granted = models.BooleanField(default=False)
    granted_at = models.DateTimeField(null=True, blank=True)
    granted_via = models.CharField(max_length=32, null=True, blank=True)
    # 'wizard' / 'reactivation' / 'admin_grant' (rare; e.g., support recovery)

    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=64, null=True, blank=True)
    # 'user_action' / 'account_deletion' / 'tenant_suspended'

    config = models.JSONField(default=dict)
    # {"share_watermark": false} etc. per customer

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [UniqueConstraint(fields=['customer', 'tenant'], name='uq_avatar_consent_per_tenant')]


class AvatarZoneConsent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consent = models.ForeignKey(AvatarConsent, on_delete=CASCADE, related_name='zones')

    ZONE_CHOICES = [
        ('face', 'Face / anti-age / skin'),
        ('body_general', 'Body — general view'),
        ('waist', 'Waist / abdomen'),
        ('hips', 'Hips'),
        ('nails', 'Nails'),
        ('hair', 'Hair'),
    ]
    zone = models.CharField(max_length=32, choices=ZONE_CHOICES)

    granted_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=['consent', 'zone'], name='uq_avatar_zone_per_consent'),
        ]
        indexes = [Index(fields=['consent', 'revoked_at'])]
```

### 10.2 `AvatarPhoto`

Encrypted blob + metadata.

```python
class AvatarPhoto(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='avatar_photos')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    zone = models.CharField(max_length=32, choices=AvatarZoneConsent.ZONE_CHOICES)

    encrypted_blob = models.BinaryField()
    # Encrypted via AES-GCM client-side; server only stores ciphertext + IV
    encryption_iv = models.CharField(max_length=64)
    # Base64-encoded IV

    width_px = models.IntegerField()
    height_px = models.IntegerField()
    file_size_bytes = models.IntegerField()

    context_label = models.TextField(max_length=280, blank=True, default='')
    # Free-text context like "after lymph drainage session 3"

    taken_at = models.DateTimeField()
    # Client-reported capture time

    created_at = models.DateTimeField(auto_now_add=True)
    # Server insert time

    class Meta:
        indexes = [
            Index(fields=['customer', 'zone', '-taken_at']),  # timeline view
            Index(fields=['tenant', 'created_at']),  # audit aggregation
        ]
        constraints = [
            CheckConstraint(check=Q(file_size_bytes__lte=10 * 1024 * 1024), name='ck_photo_max_10mb'),
            CheckConstraint(check=Q(width_px__lte=4096) & Q(height_px__lte=4096), name='ck_photo_max_4k'),
        ]
```

### 10.3 `AvatarComparison`

Generated comparison artifact (AI commentary + photos referenced).

```python
class AvatarComparison(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='+')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    zone = models.CharField(max_length=32, choices=AvatarZoneConsent.ZONE_CHOICES)

    photo_before = models.ForeignKey(AvatarPhoto, on_delete=CASCADE, related_name='comparisons_as_before')
    photo_after = models.ForeignKey(AvatarPhoto, on_delete=CASCADE, related_name='comparisons_as_after')

    ai_commentary = models.JSONField(default=dict)
    # {"observations": [...], "confidence": 0.0-1.0, "model_version": "v1"}

    services_in_period_count = models.IntegerField(default=0)
    services_in_period = models.JSONField(default=list)
    # [{"service_id": "...", "category": "...", "completed_at": "..."}, ...]

    generated_at = models.DateTimeField(auto_now_add=True)
    last_viewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [Index(fields=['customer', 'zone', '-generated_at'])]
```

### 10.4 `AvatarMasterGrant`

Per-master per-zone per-time-bound access grant.

```python
class AvatarMasterGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('customers.Customer', on_delete=CASCADE, related_name='avatar_master_grants')
    master = models.ForeignKey('catalog.CatalogMaster', on_delete=CASCADE, related_name='avatar_grants_received')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='+')
    zone = models.CharField(max_length=32, choices=AvatarZoneConsent.ZONE_CHOICES)

    granted_at = models.DateTimeField()
    DURATION_CHOICES = [
        ('next_visit', 'Until end of next visit'),
        ('30_days', '30 days from grant'),
        ('permanent', 'Until customer revokes'),
    ]
    duration = models.CharField(max_length=32, choices=DURATION_CHOICES)

    expires_at = models.DateTimeField(null=True, blank=True)
    # Computed at grant time for 30_days; updated when next_visit booking_completed
    # Null for 'permanent'

    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.CharField(max_length=32, null=True)
    # 'customer' / 'system_expiry' / 'admin_override'

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['master', 'revoked_at', 'expires_at']),  # master sees active grants
            Index(fields=['customer', '-granted_at']),  # customer audit log
        ]
```

---

## 11. API contracts

### 11.1 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/customer/avatar/consent` | Customer | Grant / revoke / update zones |
| GET | `/api/v1/customer/avatar/consent` | Customer | Read current consent + zones |
| POST | `/api/v1/customer/avatar/photo` | Customer | Upload encrypted photo |
| GET | `/api/v1/customer/avatar/photos` | Customer | List photos (filtered by zone, paginated) |
| DELETE | `/api/v1/customer/avatar/photo/<id>` | Customer | Hard-delete individual photo |
| POST | `/api/v1/customer/avatar/comparison` | Customer | Generate or fetch comparison for two photos |
| POST | `/api/v1/customer/avatar/master-grant` | Customer | Grant master access to zone |
| GET | `/api/v1/customer/avatar/master-grants` | Customer | List all current + historical grants |
| DELETE | `/api/v1/customer/avatar/master-grant/<id>` | Customer | Revoke active grant |
| GET | `/api/v1/master/avatar/granted-photos` | Master | List zones master currently has grant to |
| GET | `/api/v1/master/avatar/photo/<id>` | Master | View specific photo (audit-logged) |

### 11.2 POST `/api/v1/customer/avatar/photo`

**Request** (multipart):
```
form-data:
  zone: "face"
  encrypted_blob: <binary file>
  encryption_iv: "base64_iv_string"
  width_px: 1080
  height_px: 1440
  file_size_bytes: 524288
  context_label: "после чистки лица"
  taken_at: "2026-05-19T11:32:00Z"
```

**Validation**:
- Customer has `AvatarConsent.granted=True` AND `AvatarZoneConsent.granted_at` for this zone (else 403)
- File size ≤ 10 MB (CheckConstraint enforces)
- Image dimensions ≤ 4096×4096 (CheckConstraint)
- 14-day throttle for this zone: if last photo within 14d, overwrite latest (delete old + insert new); else just insert
- IV must be valid base64; encrypted_blob must be non-empty

**Response** (201):
```json
{
  "id": "uuid",
  "zone": "face",
  "taken_at": "2026-05-19T11:32:00Z",
  "comparison_available": true,
  "comparison_id": "uuid"  // null if first photo for zone
}
```

### 11.3 POST `/api/v1/customer/avatar/comparison`

**Request**:
```json
{
  "photo_before_id": "uuid",
  "photo_after_id": "uuid"
}
```

Both must belong to caller customer, same zone. Returns cached comparison if exists, generates new otherwise.

**Response** (200):
```json
{
  "id": "uuid",
  "ai_commentary": {
    "observations": [
      "Тонус кожи лучше, особенно в области лба и щёк.",
      "Стало меньше тени под глазами.",
      "Контур лица стал чуть чётче."
    ],
    "confidence": 0.78,
    "model_version": "v1"
  },
  "services_in_period_count": 6,
  "services_in_period": [...]
}
```

If confidence < 0.7 for all observations:
```json
{
  "ai_commentary": {
    "observations": [
      "Изменения тонкие — иногда хороший знак, что текущее состояние стабильно."
    ],
    "confidence": 0.5,
    "model_version": "v1"
  }
}
```

### 11.4 POST `/api/v1/customer/avatar/master-grant`

**Request**:
```json
{
  "master_id": "uuid",
  "zone": "face",
  "duration": "next_visit"
}
```

**Validation**:
- Master must be in customer's tenant
- Customer must have zone-specific consent for the zone
- Customer must have prior booking with this master (anti-spam: can't grant to random masters)

**Response** (201):
```json
{
  "id": "uuid",
  "master_id": "uuid",
  "zone": "face",
  "expires_at": "2026-05-20T14:00:00Z" // computed from next_visit
}
```

### 11.5 GET `/api/v1/master/avatar/photo/<id>` (master role)

Master fetches photo when they have active grant.

**Validation**:
- Master has active `AvatarMasterGrant` for this customer's zone matching the photo's zone
- Grant is not revoked, not expired

**Response** (200):
```
[encrypted blob + encryption_iv]
```

Master Mini App decrypts client-side using customer's key (delivered via secure ephemeral channel — see §12 storage architecture).

Emits `wellness.avatar.master_viewed_photo` event always.

---

## 12. Storage architecture (privacy-paranoid)

### 12.1 Client-side encryption flow

1. Customer captures photo via Mini App
2. Mini App generates per-photo encryption key (AES-GCM 256)
3. Photo encrypted client-side; encrypted blob + IV uploaded to backend
4. Per-photo key wrapped with customer's master key
5. Master key derived from customer's MAX session token via PBKDF2 + customer-specific salt
6. Wrapped keys stored in `AvatarKeyStore` table (separate from blob storage)

### 12.2 Master access flow

When master with valid grant fetches photo:
1. Master Mini App requests photo via API
2. Backend verifies grant, returns encrypted blob + IV + customer's per-photo key wrapped with grant-specific key
3. Grant-specific key derived from master's session + grant_id at server side
4. Master Mini App unwraps key, decrypts photo, displays in memory only (no save / download)
5. After 5 min idle or master closes view → key + decrypted bytes purged from Mini App memory

### 12.3 Customer revoke / deletion flow

When customer revokes consent or specific photo:
1. Mark `AvatarPhoto.deleted_at = now` (soft-delete marker)
2. Within 1 hour: hard-delete blob from object storage
3. Within 24 hours: hard-delete keys from AvatarKeyStore
4. Within 30 days: hard-delete row from AvatarPhoto table
5. Each step emits audit event for compliance

Per §2.2 — privacy hierarchy stricter than other modules (24h vs 30d for keys).

### 12.4 Storage tech (engineering decision)

- Encrypted blobs: S3-compatible object storage (RU-located per ФЗ-152) with bucket-level encryption at rest
- Metadata: Postgres tables per §10
- Keys: separate Postgres table with row-level encryption
- Engineering must verify storage selection meets ФЗ-152 + ФЗ-149 personal data laws

---

## 13. Events emitted

Per [event-taxonomy.md](../policies/event-taxonomy.md) §3.6 + new:

| Action | Event | Notes |
|---|---|---|
| Consent granted (overall) | `wellness.consent.module.granted` | `module_name='avatar'` |
| Per-zone consent granted | NEW: `wellness.avatar.zone_consent.granted` | `customer_id`, `zone`, `granted_at` |
| Per-zone consent revoked | NEW: `wellness.avatar.zone_consent.revoked` | Same |
| Photo uploaded | NEW: `wellness.avatar.photo.added` | `customer_id`, `zone`, `taken_at`, `file_size_bytes` |
| Photo deleted by customer | NEW: `wellness.avatar.photo.deleted` | `deleted_by='customer'` |
| Comparison generated | NEW: `wellness.avatar.comparison.generated` | `customer_id`, `zone`, `confidence`, `observations_count` |
| Master grant created | NEW: `wellness.avatar.master_grant.created` | `customer_id`, `master_id`, `zone`, `duration` |
| Master grant revoked | NEW: `wellness.avatar.master_grant.revoked` | `revoked_by` |
| Master viewed photo | NEW: `wellness.avatar.master_viewed_photo` | `master_id`, `photo_id`, `viewed_at` — 7y retention compliance |
| Consent revoked (overall) | `wellness.consent.module.revoked` | Cascade hard-delete all photos |

Add to event-taxonomy.md §3.6.

---

## 14. Privacy enforcement

### 14.1 API-level guards
- Customer endpoints reject if not authenticated as that customer
- Master endpoint rejects if no active grant
- Tenant boundary enforced: cross-tenant access returns 403
- All endpoints audit-logged

### 14.2 Customer ban-list
Customer can block specific masters from EVER receiving grants in future (Phase 4+; MVP rely on customer choice).

### 14.3 Founder access
- Founder has NO direct read access to photos in MVP
- For legal hold scenarios: court order required + legal-hold flag set on customer; founder + legal access via hard-coded admin tool with explicit audit emit + 4-eye approval

### 14.4 Photo data export per OP6

Customer can request data export via [customer-profile-management-ux §6.3](../policies/customer-profile-management-ux.md). Export INCLUDES photos:
- Customer's photos exported as raw decrypted images (customer is sole authorized to view their own)
- Delivered via MAX bot DM as ZIP attachment (per Q-CP5 — MAX delivery MVP)
- Includes JSON metadata of all photos + comparisons + grants

---

## 15. Acceptance criteria (engineering checklist)

- [ ] `apps/wellness_avatar/` Django app registered
- [ ] 4 models with constraints; all migrations idempotent
- [ ] Client-side encryption working in Mini App
- [ ] All 11 API endpoints implemented
- [ ] Per-zone consent gating at API level
- [ ] 14-day throttle implemented + tested
- [ ] Hard-delete cascade: revoke consent → blobs hard-deleted within 1h, keys within 24h
- [ ] Master-grant flow: customer grant → master can view (audited) → grant expiry auto-revokes
- [ ] AI commentary generator with confidence threshold + honest fallback
- [ ] Storage RU-located + ФЗ-152 compliance audit
- [ ] All events emit per §13
- [ ] Customer data export includes photos
- [ ] Customer photo deletion audit-trailed
- [ ] Permissions audit: no salon-side endpoints leak photo access
- [ ] Mini App Photo capture UI tested camera + gallery
- [ ] Comparison view UI tested with edge cases (same photo twice, very different lighting, missing master)
- [ ] Master Mini App pre-arrival context shows grant indicator
- [ ] Accessibility audit: WCAG 2.2 AA on capture + comparison flows
- [ ] Legal sign-off on consent dialog copy + retention disclosure
- [ ] Anti-pattern review: no body-shaming, no medical claims, no AI-generated futures
- [ ] Documentation in `apps/wellness_avatar/README.md`

---

## 16. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-AV1** | Photo encryption: client-side AES-GCM via Web Crypto API enough or need full E2E with master key escrow? | Client-side AES-GCM MVP per §12.1; full E2E escrow Phase 4+ if regulatory tightens | Eng + Legal | 🟡 |
| **Q-AV2** | AI commentary model — vision API (GPT-4V) or open-source (LLaVA) Phase 3? | OpenAI GPT-4V MVP (better quality, lower team complexity); evaluate cost at 10k+ comparisons/month | Eng + Founder | 🟡 |
| **Q-AV3** | Per-photo encryption keys stored where? | Postgres separate `AvatarKeyStore` table with row-level encryption per §12.4 | Eng | 🟡 |
| **Q-AV4** | Customer's MAX account lost — photos un-decryptable. Recovery offered? | NO — acceptable data loss per privacy-paranoid design §5.3. Customer informed in consent dialog. | Policy | 🟢 |
| **Q-AV5** | Photo upload from gallery — strip EXIF metadata (location)? | YES — server strips EXIF before encryption (location data privacy risk) | Eng | 🟡 |
| **Q-AV6** | Master Mini App: photo view duration limit per session? | 5 min idle timeout per §12.2; refresh requires re-fetch | Eng | 🟢 |
| **Q-AV7** | What happens to grants when master is archived? | Auto-revoke all active grants per `master.archived` event subscription | Eng | 🟡 |
| **Q-AV8** | Founder breakglass access in fraud / legal cases — UI tool or DB-only? | DB-only Phase 3; UI tool Phase 5+ with 4-eye approval workflow | Founder + Legal | 🟢 |
| **Q-AV9** | Customer-pays tier (Phase 3 vision) — AI Avatar is premium-only or free? | Premium MVP per [wellness-input-modules Q-WI12](../policies/wellness-input-modules.md). Free up to 2 zones; 4-6 zones premium. | Founder | 🟡 |
| **Q-AV10** | Mini App offline photo capture — queue and upload when online? | Phase 3 NO (encryption requires session); Phase 4+ explore | Eng | 🟢 |
| **Q-AV11** | Comparison view AI commentary localization (RU vs other languages)? | RU MVP; Phase 4+ per language re-author with cultural sensitivity review | UX + AI | 🟢 |
| **Q-AV12** | Share comparison externally — should AI commentary be regenerated for share (different tone for external audience)? | NO — share shows EXACT same commentary customer saw. No content rewrite for external. Honesty consistency. | Policy | 🟢 |
| **Q-AV13** | Customer who grants permanent access to master who later leaves salon (archived) | Auto-revoke per Q-AV7; if master later un-archived, grant must be re-issued by customer (not auto-restored) | Eng + Privacy | 🟡 |
| **Q-AV14** | Multi-tenant customer (works at salon A and B per Q-CO5) — photos shared? | NO — strict per-tenant. Customer maintains separate consent + photos per tenant. | Privacy | 🟢 |
| **Q-AV15** | 14-day throttle — strict limit or soft cap? | Strict for same zone; customer can capture different zones same day | UX | 🟢 |
| **Q-AV16** | Photo retention if customer hasn't visited in 12 months — auto-delete? | NO automatic deletion based on inactivity. Customer-initiated only. | Privacy | 🟡 |
| **Q-AV17** | AI commentary if customer's photos are too similar (no detectable change) | «Изменения тонкие — продолжайте текущий курс» per §6.3 honest fallback | UX | 🟢 |
| **Q-AV18** | Founder-50 cohort review of Avatar comparisons — is this in scope of AI Quality Observability? | NO — privacy boundary. Founder reviews only attribution + bookings, NOT customer photos. Per [ai-quality-observability §10.2](../policies/ai-quality-observability.md). | Founder + Privacy | 🟢 |

---

## 17. Cross-document linkage

- [`../policies/wellness-input-modules.md`](../policies/wellness-input-modules.md) §7 — strategic spec this handoff ports
- [`./2026-05-19-wellness-mood-handoff.md`](./2026-05-19-wellness-mood-handoff.md) — sibling Phase 1 module; shares activation patterns + consent model
- [`../policies/notification-preferences-ux.md`](../policies/notification-preferences-ux.md) — module consent integration §3.3
- [`../policies/customer-profile-management-ux.md`](../policies/customer-profile-management-ux.md) — activation surface §4 + revoke flow + data export §6.3
- [`../policies/core-user-states.md`](../policies/core-user-states.md) — state matrix §9
- [`../policies/core-wellness-profile.md`](../policies/core-wellness-profile.md) §3 Layer 4 + Layer 3 — comparison correlations
- [`../policies/master-conversational-templates.md`](../policies/master-conversational-templates.md) §5.5 — master pre-arrival context extension §8.3
- [`../policies/event-taxonomy.md`](../policies/event-taxonomy.md) §3.6 — events emitted (9 NEW per §13)
- [`../policies/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) — HUMAN_LOCKED gating §3.1
- [`../policies/ai-quality-observability.md`](../policies/ai-quality-observability.md) §10.2 — founder access boundary
- [`../policies/conversational-ux-framework.md`](../policies/conversational-ux-framework.md) — voice anchors throughout

---

## 18. What this unblocks

- **Phase 3 `apps/wellness_avatar/` implementation** — full stack engineering-ready
- **Wellness OS «WOW» feature** — proves «AI knows you» promise visibly to customer
- **Differentiation moat** — no competitor offers honest, privacy-paranoid before/after with master-grant flow
- **Customer retention multi-month** — visual progress is strongest retention driver
- **Adjacent vertical extensibility** — same architecture for fitness progress photos, nutrition tracking (Phase 5+)
- **Premium tier offering** — Q-AV9 lean = paid feature, supports customer-pays tier monetization

## 19. What this does NOT unblock

- ❌ Other wellness modules (separate handoffs per pattern)
- ❌ Tenant-side wellness analytics (privacy boundary)
- ❌ Time-lapse video (Phase 5+)
- ❌ Cross-customer comparison (privacy + scope)
- ❌ Multi-tenant photo sharing (NEVER)
- ❌ AI-generated futures (forbidden per §2.1)
- ❌ Body shaming or medical claims (forbidden per §2.3-2.4)
- ❌ Skip Legal review on §2 strategic constraints + consent dialog + retention disclosure

---

## 20. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Wellness Avatar backend lead (apps/wellness_avatar/) | ☐ | |
| Mini App frontend (capture + comparison + timeline + master view) | ☐ | |
| AI / ML (GPT-4V integration + commentary generator + confidence threshold) | ☐ | |
| Privacy / Legal (consent dialog + storage + retention + master-grant audit + Q-AV1/3/5/8/13) | ☐ | |
| Security (encryption architecture review + storage selection + key management) | ☐ | |
| Accessibility (WCAG 2.2 AA on capture + comparison + zone consent flows) | ☐ | |
| Founder (Q-AV9 premium tier + Q-AV2 model selection + Q-AV8 breakglass policy) | ☐ | |

## Last verified
2026-05-19 (initial draft, engineering-ready for Phase 3 Wellness AI Avatar — strategic «WOW» retention feature)
