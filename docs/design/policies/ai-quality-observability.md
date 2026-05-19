# AI Quality Observability — owner + founder dashboard for monitoring AI behavior

**Date:** 2026-05-19 r1
**Status:** Foundational — unblocks founder-50 cohort manual review + persona-editor analytics consumption
**Reads:** [`assistant-persona.md`](./assistant-persona.md), [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`owner-conversational-templates.md`](./owner-conversational-templates.md), [`event-taxonomy.md`](./event-taxonomy.md), [`../handoffs/2026-05-18-persona-editor-handoff.md`](../handoffs/2026-05-18-persona-editor-handoff.md), [`../handoffs/2026-05-18-analytics-dashboard-handoff.md`](../handoffs/2026-05-18-analytics-dashboard-handoff.md)

> Owner + founder surface for monitoring AI quality drift, persona violations, per-template performance, model version impact, and sampling-based review. Distinct from analytics-dashboard (booking/revenue KPIs) — this is AI-behavior dashboard.

---

## 0. Why this exists

### The gap

Multiple specs reference AI quality monitoring but no surface designed:
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) §8 — persona violation weekly report template exists but no underlying dashboard
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) Q-OC11 — «AI confidence display» lean documented but no UI
- [`../handoffs/2026-05-18-persona-editor-handoff.md`](../handoffs/2026-05-18-persona-editor-handoff.md) §13 — persona editor changes need feedback loop
- [`decisions-log.md r4`](../decisions-log.md) Q12-δ — «Founder manually reviews first 50 attributed bookings» — no review tool exists
- [`decisions-log.md r6`](../decisions-log.md) Q-CO3 — Quality Reviewer role established but no review interface
- [`event-taxonomy.md`](./event-taxonomy.md) §3.5 — `conversation.persona.violation` event emits but no consumer surface
- [`../handoffs/2026-05-18-analytics-dashboard-handoff.md`](../handoffs/2026-05-18-analytics-dashboard-handoff.md) — owner KPIs cover booking metrics but NOT AI quality

Without this surface:
- Persona violations emit «в пустоту» (no human reviews)
- Founder-50 cohort review per Q12-δ has no workflow
- A/B persona tests have no result UI (per Q-PE2 deferred but eventually needed)
- Model version impact invisible (post-deployment safety hole)
- Quality Reviewer role (Q-CO3) has no tools to do their job

### The promise

Single owner + founder dashboard providing:
- Persona violation tracking (per template, per timeframe, per severity)
- Sampling-based conversation review queue (N% random + flagged conversations)
- CSAT signals correlated to templates / personas / model versions
- Model version drift detection (deploy a new model → see delta in quality KPIs)
- Founder-only «deep dive» mode for first-50 cohort granular review
- Alert thresholds (warn when quality KPIs drop)
- A/B test result view (when persona variants run, Phase 2+)

---

## 1. Scope

### IN
- Owner-facing dashboard at Mini App → Настройки → Качество помощника
- Founder-facing extended dashboard with cross-tenant aggregate (Founder role, platform level)
- Sub-pages: Violations / Reviews queue / Templates performance / Model deltas / A/B (Phase 2+)
- Real-time + 7d + 30d windows
- Sampling logic (N% random conversations / week)
- Alert configuration (which conditions ping owner immediately vs in digest)
- Review actions: rate sample / flag for retraining / mark «false positive»
- Per-template performance breakdown
- Model version comparison
- Per-persona-variant comparison (Phase 2+ for A/B)

### OUT
- Booking / revenue / retention KPIs (covered in [`analytics-dashboard-handoff.md`](../handoffs/2026-05-18-analytics-dashboard-handoff.md))
- Persona editing itself (covered in [`persona-editor-handoff.md`](../handoffs/2026-05-18-persona-editor-handoff.md))
- AI training pipeline (out of UX scope; engineering ML concern)
- Customer-side AI behavior insight (privacy: customer doesn't see violation stats)
- Multi-tenant comparison for owner (privacy boundary — owner sees own tenant only; founder sees platform aggregate)
- Real-time per-message alerts (use HUMAN_LOCKED escalation per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md))

---

## 2. Audiences + permissions

| Audience | Access scope | Use case |
|---|---|---|
| **Owner** (single tenant) | Own tenant's AI quality | Daily ops — is AI behaving on-brand for my salon? |
| **Admin** | Own tenant; same view as owner; cannot change alert thresholds | Same daily ops without policy authority |
| **Founder** | Platform-wide aggregate + per-tenant drill-in to first-50 cohort | Q12-δ founder review + product quality oversight |
| **Quality Reviewer** (Q-CO3 role; cohort-based) | First-50 tenants' AI conversations sample + violation feed | Manual review per Q-CO3 + LQ5 batch decisions |
| Customer | NO access | Privacy boundary |
| Master | NO access | Not their concern; out of role |

---

## 3. Core KPIs surfaced

### 3.1 Persona violation rate
- Definition: violations / total AI messages × 100
- Target per [`product-ux-vision.md §8`](./product-ux-vision.md) — < 2%
- Window: rolling 7d (primary), 30d (secondary)
- Breakdown: by template id, by violation type (forbidden phrase / emoji-overuse / length-violation / persona-drift), by model version

### 3.2 Conversation satisfaction (CSAT)
- Definition: post-conversation rating ≥ 4★ / total rated × 100
- Target: ≥ 4.5★ rolling average per [`product-ux-vision.md §8`](./product-ux-vision.md)
- Coverage: % conversations rated (not all customers rate; show coverage explicitly)
- Breakdown: by template family, by customer state at conversation start, by model version

### 3.3 Handoff rate
- Definition: conversations escalated to HUMAN_LOCKED per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) / total conversations × 100
- Sub-rates: by escalation reason (complaint / out-of-catalog / medical / policy / customer-requested-human)
- Why on AI quality dashboard: high handoff rate may signal AI underperforming (or correctly delegating — context matters)

### 3.4 Average AI response time
- Definition: median ms from customer message → AI response
- Window: rolling 7d
- Target: < 3 seconds per [`max-mini-apps`](../../../.claude/skills/ux-architect/references/platforms/max-mini-apps.md) channel expectations
- Alert threshold: > 5s median for 1h → owner notification

### 3.5 Model version stability
- Definition: when model version deploys, deltas in §3.1-3.4 across the deploy boundary
- Window: 7 days before vs 7 days after deploy
- Surface: «Model v2.3 deployed Oct 14 — persona violations rose 0.3%, CSAT stable» card

### 3.6 Sample review backlog
- Definition: count of conversations sampled but not yet human-reviewed
- Target: < 20 in queue
- Alert: > 50 → notify Quality Reviewer / founder

---

## 4. Dashboard surface structure

### 4.1 Main dashboard (owner home)

```
┌─────────────────────────────────────────────────────┐
│ ← Качество помощника                                │
├─────────────────────────────────────────────────────┤
│ За 7 дней                                          │
│                                                    │
│  ┌─ Нарушения голоса ───────┐                       │
│  │  0.8%                     │                       │
│  │  (цель: <2%)              │                       │
│  │  ↓ vs прошлая неделя       │                       │
│  └───────────────────────────┘                       │
│                                                    │
│  ┌─ Удовлетворённость ──────┐                       │
│  │  4.6 ★                    │                       │
│  │  (44 оценок · 32% покрытие)│                       │
│  └───────────────────────────┘                       │
│                                                    │
│  ┌─ Передачи в живую ───────┐                       │
│  │  12%                      │                       │
│  │  (норма: 8-15%)           │                       │
│  └───────────────────────────┘                       │
│                                                    │
│  ┌─ Время ответа ───────────┐                       │
│  │  1.8 сек                  │                       │
│  └───────────────────────────┘                       │
│                                                    │
│ ── На проверке ──                                  │
│                                                    │
│ 3 разговора ждут вашей оценки                       │
│ [Открыть очередь →]                                 │
│                                                    │
│ ── Подробности ──                                  │
│                                                    │
│ [Нарушения]  [Шаблоны]  [Модели]  [A/B (скоро)]    │
└─────────────────────────────────────────────────────┘
```

### 4.2 Sub-page: Violations

```
┌─────────────────────────────────────────────────────┐
│ ← Нарушения голоса · 7 дней                         │
├─────────────────────────────────────────────────────┤
│ Всего: 12 нарушений из 1 503 сообщений (0.8%)       │
│                                                    │
│ ── По типам ──                                      │
│ Запрещённая фраза:      6 (50%)                     │
│ Слишком длинно:         3 (25%)                     │
│ Восклицания:            2 (17%)                     │
│ Эмодзи спам:            1 (8%)                      │
│                                                    │
│ ── По шаблонам ──                                   │
│ booking.confirm-success     4 (33%)                 │
│ cancel.late-acknowledge     3 (25%)                 │
│ free-form ai-generation     5 (42%)                 │
│                                                    │
│ ── Примеры ──                                       │
│                                                    │
│ ┌──────────────────────────────────────────┐       │
│ │ 14 мая · 16:32 · template: booking.confirm│      │
│ │                                          │       │
│ │ «Поздравляю с записью! Так здорово!»     │       │
│ │   ⚠️ запрещённая фраза «Поздравляю»       │       │
│ │   ⚠️ восклицания (2 шт)                   │       │
│ │                                          │       │
│ │ [Открыть разговор]  [False positive]      │       │
│ └──────────────────────────────────────────┘       │
│                                                    │
│ [Показать ещё 11 →]                                 │
└─────────────────────────────────────────────────────┘
```

### 4.3 Sub-page: Templates performance

```
┌─────────────────────────────────────────────────────┐
│ ← Шаблоны · 7 дней                                  │
├─────────────────────────────────────────────────────┤
│ Сортировать: [по CSAT ↑]                            │
│                                                    │
│ ┌─ booking.confirm-success ─────────────────┐      │
│ │ Использован: 423 раза                      │      │
│ │ CSAT: 4.8 ★ (38 оценок)                    │      │
│ │ Нарушения: 4 (0.9%)                        │      │
│ │ [Открыть шаблон]  [Примеры]                 │      │
│ └────────────────────────────────────────────┘      │
│                                                    │
│ ┌─ cancel.late-acknowledge ─────────────────┐      │
│ │ Использован: 32 раза                       │      │
│ │ CSAT: 4.2 ★ (8 оценок)                     │      │
│ │ Нарушения: 3 (9.4%)  ⚠️ выше нормы          │      │
│ │ [Открыть шаблон]  [Изменить]                │      │
│ └────────────────────────────────────────────┘      │
│                                                    │
│ ┌─ reactivation.dormant-touch ──────────────┐      │
│ │ Использован: 18 раз                        │      │
│ │ CSAT: 4.7 ★ (4 оценок · мало данных)       │      │
│ │ Нарушения: 0                               │      │
│ └────────────────────────────────────────────┘      │
│                                                    │
│ [Показать все шаблоны →]                            │
└─────────────────────────────────────────────────────┘
```

### 4.4 Sub-page: Model deltas (founder-only)

```
┌─────────────────────────────────────────────────────┐
│ ← Модели · сравнение                                │
├─────────────────────────────────────────────────────┤
│ Активная: gpt-4o-2026-05-14                         │
│ Развёрнута: 8 дней назад                            │
│                                                    │
│ ── Дельта vs предыдущая ──                          │
│ Нарушения:    ↑ 0.3% (было 0.5% → стало 0.8%)      │
│ CSAT:         → стабильно (4.6 ★)                   │
│ Время ответа: ↓ 200 мс (быстрее)                    │
│ Стоимость:    ↑ +12% на 1000 запросов              │
│                                                    │
│ ⚠ Рост нарушений на 0.3 п.п. требует внимания       │
│                                                    │
│ ── История моделей ──                               │
│                                                    │
│ gpt-4o-2026-05-14  | 8 дн назад  | актуальная       │
│ gpt-4o-2026-04-12  | 30+ дн назад | предыдущая      │
│ gpt-4o-2026-03-08  | 60+ дн назад |                 │
│                                                    │
│ [Откатить на предыдущую]                            │
└─────────────────────────────────────────────────────┘
```

«Откатить» — founder-only action; requires confirmation modal explaining trade-offs.

### 4.5 Sub-page: Review queue

```
┌─────────────────────────────────────────────────────┐
│ ← Очередь на проверку                               │
├─────────────────────────────────────────────────────┤
│ Сегодня нужно посмотреть: 3 разговора                │
│ (5% случайной выборки · 1 флагнутый системой)        │
│                                                    │
│ ┌──────────────────────────────────────────┐       │
│ │ #1 · Случайная выборка                   │       │
│ │ Ольга К. · 14 мая · 18 сообщений         │       │
│ │ Состояние: PROBLEM_SEEKING → READY_TO_BOOK│      │
│ │ Закончилось бронированием.               │       │
│ │ CSAT: ★★★★☆ (4)                          │       │
│ │ [Открыть разговор]                       │       │
│ └──────────────────────────────────────────┘       │
│                                                    │
│ ┌──────────────────────────────────────────┐       │
│ │ #2 · Флагнуто системой ⚠                  │       │
│ │ Татьяна П. · 15 мая · 7 сообщений         │       │
│ │ Причина: 2 нарушения голоса в одном       │       │
│ │ разговоре                                 │       │
│ │ [Открыть разговор]  [False positive]      │       │
│ └──────────────────────────────────────────┘       │
│                                                    │
│ ┌──────────────────────────────────────────┐       │
│ │ #3 · Случайная выборка                   │       │
│ │ Анна М. · 16 мая · 4 сообщения           │       │
│ │ Состояние: DISCOVERED → DORMANT          │       │
│ │ Без бронирования.                        │       │
│ │ [Открыть разговор]                       │       │
│ └──────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────┘
```

### 4.6 Conversation review screen

```
┌─────────────────────────────────────────────────────┐
│ ← Разговор #ABC123                                  │
├─────────────────────────────────────────────────────┤
│ [conversation transcript with persona violations    │
│  highlighted inline + template used per message]    │
│                                                    │
│ ── Ваша оценка ──                                   │
│                                                    │
│ Качество ответов помощника?                         │
│ ★ ★ ★ ★ ★  (хорошо)                                 │
│                                                    │
│ Что заметили? (опц.)                                │
│ [многострочное поле]                                │
│                                                    │
│ Flag for retraining?                                │
│ ☐ Этот разговор стоит использовать для тюнинга      │
│                                                    │
│ [Сохранить и далее]                                 │
└─────────────────────────────────────────────────────┘
```

---

## 5. Sampling logic

### 5.1 Random sample rate
- Default: 5% of conversations per week
- Configurable per tenant: 1-20%
- Cap: max 50 conversations / week in queue per tenant (prevents overload)

### 5.2 Flagging rules (automatic — bypass random sample)

Auto-add to review queue:
- Conversation with ≥ 2 persona violations
- Conversation with CSAT ≤ 2★
- Conversation ending in customer block / opt-out
- Conversation with explicit «бот?» question + AI deflection (test honesty mandate)
- Conversation with HUMAN_LOCKED escalation reason ∈ {complaint, medical, regulated_topic}

### 5.3 Cohort priority (founder-50)
- For first 50 tenants (per Q12-δ): 100% review queue inclusion of conversations with bookings, plus 10% random sample of non-booking conversations
- Reviewer: founder per Q-CO3 (cohort #1-50)
- After cohort #50: standard 5% sample rate per tenant, CSM Lead reviewer per Q-CO3

### 5.4 Queue management
- Reviewers see queue sorted by «оldest first» (FIFO; oldest first ensures nothing rots)
- «Open conversation» locks it for that reviewer (5-min lock to prevent collision)
- Skip-conversation rate tracked per reviewer (anti-rubber-stamp signal)

---

## 6. Alert configuration

### 6.1 Alert types

| Trigger | Default action | Configurable? |
|---|---|---|
| Persona violation rate > 2% in last hour | Owner DM «Внимание: 2.3% нарушений за час — проверьте» | Threshold per-tenant |
| CSAT 7d rolling avg drops > 0.3★ vs prior 7d | Owner DM «CSAT упал на 0.4 — проверьте последние разговоры» | Threshold per-tenant |
| Handoff rate > 25% sustained 24h | Owner DM «Каждый 4-й разговор уходит в передачу — может AI что-то не понимает» | Threshold per-tenant |
| Sample backlog > 30 | Reviewer DM «30 разговоров ждут проверки» | Threshold + recipient |
| Review queue > 50 (cap reached) | System alert engineering + owner | Not configurable (data integrity) |
| Model version deployed | Founder DM «Развёрнута модель X — следите за дельтой» | Always on (cannot disable) |
| Cost per message anomaly (50% above baseline) | Founder DM | Founder-only alert |

### 6.2 Alert delivery

- Owner / admin alerts via bot DM per [`owner-conversational-templates.md`](./owner-conversational-templates.md) §6.3 escalation style
- Founder alerts via dedicated channel (Phase 2: separate founder MAX bot or email digest)
- Quality Reviewer alerts via batch daily digest

### 6.3 Alert acknowledgement

Owner can:
- Mark alert as «понятно» (acknowledged)
- Snooze alert 24h
- Mute alert type for 7 days (with reason captured)
- Escalate alert type to «critical» (now triggers SLA per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md))

---

## 7. Founder-50 cohort review workflow (Q12-δ + Q-CO3 implementation)

### 7.1 Onboarding the cohort
Per Q12-δ: founder manually reviews first 50 attributed bookings before first commercial billing.

Workflow:
1. New tenant lands → onboarding completes → tenant ID added to «cohort_first_50» tag
2. First customer-driven booking attribution for this tenant → conversation auto-added to founder review queue (priority flag «cohort_first_50»)
3. Founder reviews conversation in §4.6 review screen:
   - Verifies attribution logic correctness (per [`attribution-policy.md`](./attribution-policy.md))
   - Verifies persona quality (no violations missed by linter)
   - Verifies handoff appropriateness (HUMAN_LOCKED triggered where it should be)
4. Founder rates: ✅ correct / ⚠ marginal / ❌ wrong
5. Aggregates after 50 conversations: «cohort first-50 attribution accuracy %»
6. If accuracy ≥ 95% → enable automated billing per [`attribution-policy.md`](./attribution-policy.md) V2 validation
7. If accuracy < 95% → continue manual review window OR pause billing per attribution-policy

### 7.2 Founder dashboard «cohort» view

```
┌─────────────────────────────────────────────────────┐
│ ← Cohort first-50 (founder)                         │
├─────────────────────────────────────────────────────┤
│ Прогресс: 32 / 50 разговоров проверено               │
│                                                    │
│ Атрибуция:                                          │
│   ✅ корректно:    28 (87.5%)                       │
│   ⚠ маргинально:   3 (9.4%)                         │
│   ❌ некорректно:  1 (3.1%)                         │
│                                                    │
│ Точность: 87.5% (цель ≥95% для авто-биллинга)       │
│                                                    │
│ Осталось проверить: 18 разговоров                    │
│                                                    │
│ ── Маргинальные / некорректные ──                   │
│                                                    │
│ ⚠ Конв #42: ai_assist_score=0.7, но handoff был     │
│ ⚠ Конв #51: external bookings помечен ai_assisted   │
│ ❌ Конв #57: customer-asked-for-human, AI продолжил │
│                                                    │
│ [Открыть очередь]   [Экспорт результатов]            │
└─────────────────────────────────────────────────────┘
```

### 7.3 Auto-disable cohort tag
After 50 conversations reviewed + accuracy threshold cleared:
- Tenant moves from «cohort_first_50» → «cohort_active»
- Auto-review rate drops to standard 5% per §5.1
- Notification: «Тенант прошёл cohort first-50 review с точностью X% — биллинг авто-режим включён»

---

## 8. Per-template performance breakdown

### 8.1 Template id mapping
Per [`conversational-ux-framework.md`](./conversational-ux-framework.md) §5 / [`master-conversational-templates.md`](./master-conversational-templates.md) §5 / [`owner-conversational-templates.md`](./owner-conversational-templates.md) §6 — each template has a stable `template_id`.

Engineering convention: every AI message tagged with `template_id` (or `free_form_ai_generation` for non-template responses) at emit time. Stored in `conversation.message.sent` event payload (per [`event-taxonomy.md`](./event-taxonomy.md) §3.5).

### 8.2 Dashboard data
For each `template_id` in time window:
- Use count
- CSAT (if rated post-conversation)
- Violation rate
- Avg response time
- Customer state distribution at use (e.g., template X used 80% in PROBLEM_SEEKING, 20% in EXPLORING)

### 8.3 Drift signal
If template performance drops:
- Vs same template prior 30d (within tenant)
- Vs same template across tenants (founder view only)
- Surfaces as «⚠ выше нормы» badge per §4.3

### 8.4 Template-level actions
- «Open template» → deep link to persona-editor for that template (Phase 2; MVP read-only)
- «Examples» → recent uses of this template (anonymized sample)

---

## 9. A/B persona variants (Phase 2+ per Q-PE2 deferred decision)

Out of MVP scope but dashboard structure ready:
- Toggle to enable A/B comparison view
- Side-by-side metrics per variant
- Statistical confidence indicator
- Promote / rollback variant actions

For MVP: tab is shown as «A/B (скоро)» disabled-with-explanation.

---

## 10. Privacy + ethical boundaries

### 10.1 What owner sees vs not

Owner sees:
- Own tenant's aggregate AI quality
- Own tenant's conversation samples (full transcripts)
- Own tenant's violation examples (anonymized customer names: «Ольга К.»)
- Template performance for templates used in their tenant

Owner does NOT see:
- Other tenants' data (cross-tenant boundary)
- Customer's full PII (phone, email, last name)
- Customer's wellness data (Layer 6/7 strict customer-only)
- Internal AI reasoning chains (out of scope; engineering observability)

### 10.2 Founder sees additionally
- Platform aggregate KPIs
- Per-tenant drill-in for cohort review
- Model version deltas across all tenants
- Cost / token usage data
- Founder does NOT see: customer wellness inputs (still strict customer-only even from founder)

### 10.3 Anonymization in samples
- Customer name: first name + last initial («Ольга К.»)
- Customer phone / email: never shown
- Wellness module data: redacted («[wellness data — customer-only]»)
- Other PII per [`event-taxonomy.md`](./event-taxonomy.md) §6 rules

### 10.4 Audit
Every action in dashboard logged:
- View violation: audit
- Open conversation review: audit + 5-min lock
- Rate review: audit
- Configure alert: audit per [`event-taxonomy.md`](./event-taxonomy.md) §3.10 admin.settings.updated
- Founder cohort actions: audit + cross-tenant visible for cohort transparency

---

## 11. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Show owner all customer transcripts unfiltered | Privacy violation; overwhelm | 5% sample + flagged subset only |
| Real-time per-violation popup notification | Anxiety-inducing | Aggregate in alerts; per-event in dashboard |
| Auto-block model deploy on first violation | Brittle; false positives | Threshold-based + founder review |
| Hide model deploy from owner | Mistrust | Transparent in dashboard + DM alert |
| Cross-tenant comparison for owner | Privacy + competitive | Founder-only |
| Force owner to review N samples / week | Compliance burden; turns into rubber-stamp | Suggest optional; reviewer is Quality Reviewer role mostly |
| Show full PII in samples | PII leak | Anonymized name + redacted sensitive |
| Single-button «enable A/B» without explanation | Surprising results | Phase 2+ with onboarding flow |
| Aggregate KPIs in owner's daily digest mixed with booking metrics | Cognitive overload | Separate «Качество помощника» digest weekly |
| Penalize Quality Reviewer for low ratings | Bias toward over-rating | Reviewer feedback used for tuning, not for evaluation |
| Customer can see they were flagged | Self-fulfilling drift | NEVER customer-visible |
| Alerts that can't be silenced | Notification fatigue | Snooze + mute + escalate options |

---

## 12. Localization

### MVP: RU
- All UI in standard literary Russian
- «Качество помощника» (assistant quality — softer than «AI quality»)
- «Нарушения голоса» (voice violations — clearer than «persona violations»)
- «Передачи в живую» (handoffs to human)
- «На проверке» (under review)

### Phase 4+
- Per-language translations
- Cultural sensitivity in violation severity ratings

---

## 13. Accessibility (WCAG 2.2 AA)

- KPI cards have semantic structure + `aria-label`s
- Color-coded badges (red/yellow/green) always paired with text label
- Conversation review screen: keyboard-navigable transcript
- Charts: data table fallback per WCAG SC 1.1.1
- Alerts: `role="alert"` for urgent, `role="status"` for soft
- 44×44 touch targets on all interactive elements
- Focus order: top-down KPI → sub-page tabs → review queue

---

## 14. Events emitted

Per [`event-taxonomy.md`](./event-taxonomy.md) §3.5 (existing) + new admin/system events:

| Action | Event | Notes |
|---|---|---|
| Reviewer opens conversation | `admin.audit.event` with `action='ai_review.opened'` | Stores reviewer ID + lock acquired |
| Reviewer rates conversation | `admin.audit.event` with `action='ai_review.rated'` + rating | Audit |
| Reviewer flags for retraining | `admin.audit.event` with `action='ai_review.flagged_for_retraining'` | Training pipeline subscriber |
| Reviewer marks false positive | `admin.audit.event` with `action='ai_review.false_positive'` | Persona linter calibration feedback |
| Alert threshold changed | `admin.settings.updated` with `setting_path='ai_quality.alert_threshold.X'` | Standard admin audit |
| Cohort transition (first_50 → active) | NEW `tenant.cohort.changed` (add to event-taxonomy §3.10) | Founder + analytics |
| Founder cohort review milestone (50 conversations done) | NEW `quality.cohort.review_complete` | Founder pipeline notification |
| A/B variant promoted (Phase 2+) | NEW `quality.ab.variant_promoted` | Persona-editor sync |

Add to event-taxonomy.md §3.10: `tenant.cohort.changed`, `quality.cohort.review_complete`, `quality.ab.variant_promoted`.

---

## 15. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-QO1** | Default random sample rate — 5% sufficient or should be tenant-size-dependent? | 5% MVP; per-tenant configurable in alert settings; cap 50/week per §5.1 | PM | 🟢 |
| **Q-QO2** | Alert default thresholds — platform-fixed or per-tenant baseline? | Platform-fixed MVP; per-tenant tuning v1.1+ when CSM observes false-positive patterns | UX + PM | 🟡 |
| **Q-QO3** | Persona violation: linter false-positive rate — what threshold triggers persona-editor review prompt to founder? | Reviewer marks N=10 false positives in 7d → founder gets «linter may need tuning» alert | UX | 🟢 |
| **Q-QO4** | Conversation review lock duration — 5min sufficient or longer? | 5min MVP; auto-release on browser close; revisit if reviewer collision in observation | Eng | 🟢 |
| **Q-QO5** | Founder cohort review — what if accuracy is 90% (between 85% «pause» and 95% «auto-enable»)? | Continue manual review window for cohort #51-100 (extend cohort); attempt auto-enable at cohort 100. Per founder discretion. | Founder | 🟡 |
| **Q-QO6** | Model deploy rollback — owner has button to rollback own tenant's model OR only founder platform-wide? | Founder-only; tenant doesn't choose model. Owner sees deploy + alert + can request rollback via CSM. | Founder | 🟡 |
| **Q-QO7** | Dashboard refresh — real-time, polling, or on-demand? | Polling every 5min for KPIs; on-demand for sub-pages; aligned with [analytics-dashboard Q-AD7](../decisions-log.md) | Eng | 🟢 |
| **Q-QO8** | Quality Reviewer role — separate from CSM Lead per Q-CO3? | Same role per Q-CO3 (consolidated «Quality Reviewer»); founder for cohort #1-50, CSM Lead after. Already decided. | n/a | ✅ decided |
| **Q-QO9** | A/B test results MVP or Phase 2? | Phase 2 per Q-PE2; MVP shows «A/B (скоро)» disabled tab | PM | 🟢 |
| **Q-QO10** | Owner can ASK to opt their tenant out of «founder reviews us» cohort? | NO — cohort first-50 mandatory per Q12-δ for billing trust. Tenant accepts at onboarding terms. | Founder + Legal | 🟡 |
| **Q-QO11** | Founder cross-tenant aggregate — shared with investors / public benchmarks? | NEVER without explicit founder + legal sign-off; default private to platform team | Founder + Legal | 🟢 |
| **Q-QO12** | What does Quality Reviewer see when conversation is HUMAN_LOCKED (admin owns)? | Sees only the AI portion + handoff context; admin's manual replies redacted (admin privacy + scope) | UX + Privacy | 🟡 |
| **Q-QO13** | Reviewer skip-rate alerting — what threshold flags rubber-stamping concern? | > 70% skip rate in 7d → Quality Reviewer Lead gets alert «reviewer pattern concern» | PM | 🟢 |
| **Q-QO14** | Persona drift detection — beyond per-template, do we surface «AI is generally getting longer / more emoji / more apologetic»? | YES — surface drift detector chart (avg word count, emoji count, exclamation count over 30d) per persona variant | UX | 🟡 |
| **Q-QO15** | Alert «персона нарушение» — does it include template id + diff snippet or just count? | Include template id + first violation snippet excerpt (anonymized) in alert body | UX | 🟢 |

---

## 16. Cross-document linkage

- [`assistant-persona.md`](./assistant-persona.md) — voice rules being measured
- [`conversational-ux-framework.md`](./conversational-ux-framework.md) §5 — customer templates with template_ids tracked
- [`master-conversational-templates.md`](./master-conversational-templates.md) — master-side templates monitored same way
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) §8 — persona violation report (template doc) consumes this dashboard
- [`event-taxonomy.md`](./event-taxonomy.md) §3.5 — `conversation.persona.violation` events feed the dashboard
- [`event-taxonomy.md`](./event-taxonomy.md) §3.10 — admin.audit.event + new tenant.cohort.changed events
- [`attribution-policy.md`](./attribution-policy.md) — Q12-δ founder cohort review workflow
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — handoff rate KPI sourced here
- [`information-architecture.md`](./information-architecture.md) — owner Mini App navigation places this at Настройки → Качество
- [`notification-preferences-ux.md`](./notification-preferences-ux.md) — owner persona violation digest preference (N18)
- [`../handoffs/2026-05-18-persona-editor-handoff.md`](../handoffs/2026-05-18-persona-editor-handoff.md) — sister surface; this dashboard feeds, persona editor configures
- [`../handoffs/2026-05-18-analytics-dashboard-handoff.md`](../handoffs/2026-05-18-analytics-dashboard-handoff.md) — distinct dashboard; KPI overlap NONE (different concerns)
- [`../decisions-log.md`](../decisions-log.md) — Q12-δ + Q-CO3 + Q-PE1-8 referenced
- [`../briefings/founder-session-briefing.md`](../briefings/founder-session-briefing.md) — founder cohort review workflow

---

## 17. What this unblocks

- **Founder-50 cohort review** per Q12-δ — workflow + tool exist
- **Quality Reviewer role** per Q-CO3 — has actual tools to do the job
- **Persona violation events** stop emitting «в пустоту» — consumer surface exists
- **Model deploy safety** — drift detection visible
- **Owner trust** — sees their AI's actual behavior, not blind spot
- **Engineering ML feedback loop** — flagged conversations → training pipeline
- **Auto-billing enablement** per attribution-policy V2 — cohort accuracy threshold tracked
- **Persona-editor effectiveness measurement** — owner can change template → see CSAT delta
- **CSM Lead workflow** post-cohort-#50 — standard review queue

## 18. What this does NOT unblock

- ❌ Replace booking / revenue analytics dashboard (separate)
- ❌ Customer-facing AI behavior insights (privacy boundary)
- ❌ A/B persona testing (Phase 2+ per Q-PE2)
- ❌ Cross-tenant comparison for owners (privacy)
- ❌ Real-time per-message intervention (use HUMAN_LOCKED escalation path)
- ❌ Skip founder cohort review for first 50 (Q-QO10 NO)
- ❌ Public benchmark publishing (Q-QO11 NO without legal)

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Founder (Q12-δ cohort workflow + Q-QO5/6/10/11 founder decisions) | ☐ | |
| AI prompt engineering (persona linter integration + retraining pipeline) | ☐ | |
| CSM Lead (Quality Reviewer role tooling) | ☐ | |
| Privacy / Legal (Q-QO10/11/12 + anonymization rules §10.3) | ☐ | |
| Accessibility (WCAG 2.2 AA on dashboard + review screens) | ☐ | |
| Backend (sampling pipeline + event consumer + dashboard API) | ☐ | |

## Last verified
2026-05-19 (initial draft, owner + founder AI quality dashboard locked for Phase 2 implementation + founder-50 cohort review workflow ready immediately)
