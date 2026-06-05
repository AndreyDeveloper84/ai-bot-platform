# Quality Reviewer Dashboard — operational tooling for Q-CO3 / Q12-δ

**Date:** 2026-05-19 r1
**Status:** Foundational — operational tool for Quality Reviewer role (founder cohort #1-50 → CSM Lead after)
**Reads:** [`ai-quality-observability.md`](./ai-quality-observability.md), [`attribution-policy.md`](./attribution-policy.md) §13 + Q12-δ, [`conversation-ownership-policy.md`](./conversation-ownership-policy.md), [`assistant-persona.md`](./assistant-persona.md), [`event-taxonomy.md`](./event-taxonomy.md), [`owner-conversational-templates.md`](./owner-conversational-templates.md), [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md)

> [`ai-quality-observability.md`](./ai-quality-observability.md) designs the OWNER-side dashboard (per-tenant view of their AI quality). This doc designs the **platform-side Quality Reviewer dashboard** — a different surface for the founder (cohort #1-50) and CSM Lead (cohort #51+) to actually DO the review work.

---

## 0. Why this exists

### 0.1 Two distinct surfaces, two distinct audiences

| Surface | Audience | Purpose |
|---|---|---|
| [`ai-quality-observability.md`](./ai-quality-observability.md) | Tenant owner / admin | «How is MY salon's AI doing?» Per-tenant scope. Dashboards + alerts + drift visibility. |
| **THIS DOC** (Quality Reviewer dashboard) | Founder (cohort 1-50) → CSM Lead (cohort 51+) | «Do the actual sample-based review work across all tenants.» Cross-tenant operational tool. |

### 0.2 The gap

[`ai-quality-observability.md §4.5/§4.6`](./ai-quality-observability.md) sketched the review queue + conversation review screen inside the owner dashboard. But:
- Quality Reviewer needs CROSS-TENANT view (review queue spans all tenants)
- Quality Reviewer needs PRODUCTIVITY tools (throughput, calibration, batch ops)
- Quality Reviewer needs WORKFLOW persistence (multi-day reviews, drafts)
- Quality Reviewer is platform-staff, not tenant owner — different permissions
- Founder-50 cohort review per Q12-δ requires dedicated workflow

Per [`decisions-log.md`](../decisions-log.md) Q-CO3 / LQ5: «Consolidated Quality Reviewer role merged. Founder for cohort #1-50; CSM Lead after.»

Without this dashboard:
- Founder manually digs through admin tools or DB queries
- Cohort review per Q12-δ has no efficient workflow
- 5% sample policy per [`ai-quality-observability §5.1`](./ai-quality-observability.md) has no operator surface to fulfill
- Persona linter calibration feedback loop broken (false positive marking has no place)
- CSM Lead (after cohort 50) inherits unstructured workflow

### 0.3 The promise

Operational tool that:
- Routes review queue items from ALL tenants to the active reviewer
- Locks items during review (no collision)
- Tracks reviewer throughput + skip rate + decisions
- Supports batch operations on low-severity items
- Maintains cohort-aware workflow (founder-50 vs steady-state)
- Feeds persona linter calibration via marked false-positives
- Provides reviewer-side analytics (Phase 3+ trust metrics)
- Audit-grade decision trail per [`event-taxonomy.md`](./event-taxonomy.md) §3.10

---

## 1. Scope

### IN
- New surface `apps/admin/quality_reviewer/` (founder + CSM Lead access only)
- Cross-tenant review queue (filtered by reviewer role + cohort scope)
- Conversation review screen (extends ai-quality-observability §4.6 with reviewer-side actions)
- Founder-50 cohort review workflow (per [`attribution-policy.md`](./attribution-policy.md) Q12-δ)
- Reviewer locks + collision prevention (5-min lock per item)
- Batch operations on low-severity items
- Reviewer throughput dashboard (productivity, accuracy, calibration)
- Persona linter false-positive feedback loop
- Reviewer onboarding + handoff (founder-50 → CSM Lead transition)
- Audit events for every reviewer action

### OUT
- Tenant owner's view of own AI quality (covered in [`ai-quality-observability.md`](./ai-quality-observability.md))
- Customer-side analytics (privacy boundary)
- AI training data labeling beyond «flag for retraining» (separate ML pipeline)
- Multi-language review (RU MVP; international Phase 5+)
- Reviewer 360-degree feedback / peer review (Phase 3+)
- AI training model retraining UI (engineering scope)
- Multi-reviewer collaboration on same conversation (locks prevent; Phase 4+ collaborative mode)

---

## 2. Audiences + permissions

| Audience | Cohort scope | Access level |
|---|---|---|
| **Founder** | All tenants (cohort #1-50 priority) | Full — review queue, conversation drill-in, cohort dashboards, calibration, batch ops, audit trail |
| **CSM Lead** | All tenants in cohort #51+ | Same as founder; cohort #1-50 viewable but Phase 3+ unless founder delegates |
| **CSM Member** (junior) | Limited per CSM Lead assignment | Phase 3+ — view + flag-only, no batch ops or calibration |
| Tenant Owner / Admin | NO access | Privacy boundary — they have own dashboard per [`ai-quality-observability.md`](./ai-quality-observability.md) |
| Customer / Master | NO access | Privacy boundary |
| Engineering | Read-only audit log access | For debugging + training pipeline |

---

## 3. Cohort lifecycle

Per [`ai-quality-observability.md §7`](./ai-quality-observability.md) + Q12-δ:

### 3.1 Cohort tags

| Tag | Definition | Reviewer | Sample rate |
|---|---|---|---|
| `cohort_first_50` | First 50 tenants ever onboarded | Founder | 100% of customer-driven bookings + 10% random |
| `cohort_active` | Tenants beyond first 50 | CSM Lead | 5% random + auto-flagged |
| `cohort_at_risk` | Tenants with persona violation rate > 2% OR CSAT < 4.0 | CSM Lead + founder if needed | 15% random + auto-flagged (elevated) |
| `cohort_archived` | Tenant in ARCHIVED state per [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) | None | No new samples; existing reviews preserved |

### 3.2 Transition rules

- New tenant onboarded → `cohort_first_50` if total < 50; else `cohort_active`
- `cohort_first_50` completes founder review (50 conversations + ≥ 95% accuracy) → `cohort_active`
- `cohort_active` detects elevated metrics → `cohort_at_risk` (auto + founder approval to avoid over-flagging)
- `cohort_at_risk` recovers (rate normalized 30+ days) → `cohort_active`
- Tenant ARCHIVED → `cohort_archived` terminal

### 3.3 Cohort transition events
- `tenant.cohort.changed` per [`event-taxonomy.md §3.10`](./event-taxonomy.md#310-admin--system-domain) (NEW added with ai-quality-observability)

---

## 4. Main dashboard layout

### 4.1 Reviewer home

```
┌──────────────────────────────────────────────────────────┐
│ Quality Review — Founder                                 │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ ── Сегодня ──                                            │
│                                                          │
│ ┌─ Очередь ─────────────────┐  ┌─ Прогресс cohort 1-50 ──┐│
│ │ В работе сейчас:    3     │  │ Проверено: 32 / 50      ││
│ │ Ждут проверки:     17     │  │ Точность: 87.5%         ││
│ │ Завершено сегодня:  8     │  │ (цель ≥ 95%)            ││
│ │ Среднее время:    4 мин   │  │ Прогноз авто-биллинга:  ││
│ │                            │  │ ещё ~18 проверок        ││
│ │ [Открыть очередь]          │  │ [Открыть cohort]        ││
│ └────────────────────────────┘  └────────────────────────┘│
│                                                          │
│ ┌─ Calibration метрики ──────────────────────────────────┐│
│ │ Skip-rate за 7д:    12%  (норма < 30%)                ││
│ │ Inter-rater consistency: n/a (только вы пока)         ││
│ │ Persona linter FP rate: 8%  ⚠ выше нормы 5%           ││
│ │ [Открыть подробнее]                                    ││
│ └────────────────────────────────────────────────────────┘│
│                                                          │
│ ── Подпункты ──                                          │
│                                                          │
│ [Очередь проверки]   [Cohort #1-50]   [Calibration]      │
│ [Throughput]   [Audit log]   [Help]                      │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 4.2 Key tiles

- **Очередь**: current locked items + waiting + completed today
- **Прогресс cohort 1-50**: Q12-δ workflow — number reviewed, accuracy %, projection to auto-billing enablement
- **Calibration метрики**: reviewer's own meta-quality signals (skip rate, FP rate, consistency)

---

## 5. Review queue surface

### 5.1 Queue list (cross-tenant)

```
┌──────────────────────────────────────────────────────────┐
│ ← Очередь проверки                                       │
├──────────────────────────────────────────────────────────┤
│ Фильтр: [Все cohort ▾] [Все типы ▾] [Сортировка: FIFO ▾] │
│                                                          │
│ ⓘ Cohort first_50 — приоритетные (100% выборка)          │
│                                                          │
│ ──────────────────────────────────────────────────────── │
│                                                          │
│ #1 · cohort_first_50 · 🟡 Booking attribution            │
│ Tenant: «Студия Карины» (#tnt_abc123)                    │
│ Ольга К. · 14 мая · 18 msg · ai_direct                   │
│ Booking attributed; needs Q12-δ verification              │
│ В очереди: 2ч 12м                                        │
│ [Открыть]                                                │
│                                                          │
│ #2 · cohort_first_50 · 🟡 Booking attribution            │
│ Tenant: «MIX Studio» (#tnt_def456)                       │
│ Татьяна П. · 15 мая · 7 msg · ai_assisted                │
│ Handoff + booking; multi-flag review                     │
│ В очереди: 1ч 8м                                         │
│ [Открыть]                                                │
│                                                          │
│ #3 · cohort_active · 🔴 Persona violation × 2            │
│ Tenant: «BeautyLab» (#tnt_ghi789)                        │
│ Анна М. · 16 мая · 4 msg                                 │
│ Auto-flagged: 2 violations same conversation              │
│ В очереди: 45 мин                                        │
│ [Открыть]                                                │
│                                                          │
│ #4 · cohort_active · 🟢 Random sample                    │
│ Tenant: «Маникюр Pro» (#tnt_jkl012)                      │
│ Лена С. · 16 мая · 12 msg · ai_direct                    │
│ 5% random; happy-path-looking                             │
│ В очереди: 30 мин                                        │
│ [Открыть]                                                │
│                                                          │
│ Показать ещё 13 →                                        │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 5.2 Filter + sort options

- **Cohort**: all / first_50 / active / at_risk
- **Type**: all / random / auto-flagged / cohort_first_50_booking / persona_violation
- **Sort**: FIFO (oldest first, default) / age desc / tenant / severity

### 5.3 FIFO priority

Default sort = FIFO ensures nothing rots. Reviewer can manually override if scope-justified.

### 5.4 Lock indication

Items currently locked by other reviewers show:
```
#5 · LOCKED by founder · since 12:30 (5 min)
   Освободится в 12:35 если не будет действия
```

Allows transparency without revealing identity (only «founder» / «csm_lead_<id>»).

### 5.5 Empty state

```
Очередь пуста — отличная работа.
Следующая проверка появится при следующей выборке.
```

---

## 6. Conversation review screen (reviewer-side)

Extends [`ai-quality-observability.md §4.6`](./ai-quality-observability.md) base + reviewer-specific actions.

### 6.1 Layout

```
┌──────────────────────────────────────────────────────────┐
│ ← Review · Ольга К. · 14 мая · cohort_first_50          │
├──────────────────────────────────────────────────────────┤
│ Tenant: «Студия Карины»                                  │
│ State at start: PROBLEM_SEEKING                          │
│ State at end:   READY_TO_BOOK                            │
│ Outcome:        booking confirmed (ai_direct)            │
│ CSAT:           ★★★★☆ (4)                                │
│ Persona violations: 0                                    │
│ Customer-facing AI msgs: 8                               │
│ Customer msgs:  10                                       │
│ Locked: 12:30 by founder · 5 min remaining               │
│                                                          │
│ ── Conversation transcript (8 AI / 10 customer) ──       │
│                                                          │
│ [полный chronological transcript with:                   │
│  - per-message highlighting if violation detected        │
│  - template_id badge per AI message                      │
│  - inline annotation tools                               │
│  - links to source bot DM in MAX]                        │
│                                                          │
│ ── Your verdict ──                                       │
│                                                          │
│ Attribution correct?                                     │
│ ⦿ ✅ Correct  ◯ ⚠ Marginal  ◯ ❌ Wrong                   │
│                                                          │
│ Persona quality?                                         │
│ ⦿ Strong  ◯ Acceptable  ◯ Weak  ◯ Off-brand              │
│                                                          │
│ Handoff appropriateness?                                 │
│ ⦿ N/A (no handoff)  ◯ Correct  ◯ Should have happened   │
│ ◯ Happened wrongly                                       │
│                                                          │
│ Notes (optional):                                        │
│ [многострочное поле]                                     │
│                                                          │
│ ── Actions ──                                            │
│                                                          │
│ ☐ Flag for retraining (interesting case)                 │
│ ☐ Flag as false positive (linter mistake)                │
│ ☐ Add to persona-editor examples                         │
│                                                          │
│ [Сохранить и далее]  [Сохранить и закрыть]   [Skip]      │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 6.2 Verdict structure

Three independent ratings per Q12-δ:
- **Attribution correct?** (driver for cohort-50 auto-billing enablement)
- **Persona quality?** (drives persona-editor analytics)
- **Handoff appropriateness?** (drives conversation-ownership tuning)

All required to save. «Skip» allowed but tracked per §10.4.

### 6.3 Lock semantics

- Opening conversation acquires 5-min lock
- Lock auto-extends on activity (typing notes, scrolling transcript)
- Lock releases on save / skip / explicit close / browser close (heartbeat)
- Lock collision: other reviewers see «LOCKED by founder» badge; cannot open

### 6.4 Inline annotation

Reviewer can highlight specific AI messages with annotations:
- «персона нарушена — слишком формально»
- «handoff пропущен — стоило передать»
- «attribution окей, но conversation слабая»

Saved with verdict for engineering feedback.

### 6.5 «Save and next» auto-progression

After save → loads next FIFO item from queue. Maintains reviewer flow without manual navigation.

### 6.6 «Skip» tracking

Skip preserves item in queue + records reason:
- «Слишком сложный случай — нужен второй глаз»
- «Не уверен — давай позже»
- «Технические проблемы»
- (free text)

Skip rate tracked in §11.2 calibration metrics.

---

## 7. Founder-50 cohort review workflow

### 7.1 Cohort dashboard

```
┌──────────────────────────────────────────────────────────┐
│ ← Cohort first-50 (founder)                              │
├──────────────────────────────────────────────────────────┤
│ Прогресс: 32 / 50 разговоров проверено                   │
│                                                          │
│ Атрибуция:                                               │
│   ✅ корректно:    28 (87.5%)                            │
│   ⚠ маргинально:   3 (9.4%)                              │
│   ❌ некорректно:  1 (3.1%)                              │
│                                                          │
│ Точность: 87.5% (цель ≥ 95% для авто-биллинга)           │
│                                                          │
│ Persona quality:                                         │
│   Strong:       29 (90.6%)                               │
│   Acceptable:   3 (9.4%)                                 │
│   Weak/off:     0 (0%)                                   │
│                                                          │
│ Handoff:                                                 │
│   Correct + N/A:  31 (96.9%)                             │
│   Should have:    1 (3.1%)                               │
│   Wrong:          0 (0%)                                 │
│                                                          │
│ ── Распределение по тенантам ──                          │
│                                                          │
│ tnt_abc123 «Студия Карины»: 4 проверено / 4 OK / 100%   │
│ tnt_def456 «MIX Studio»:    3 проверено / 2 OK / 67%    │
│ tnt_ghi789 «BeautyLab»:     5 проверено / 5 OK / 100%   │
│ ...                                                      │
│                                                          │
│ ── Маргинальные / некорректные ──                        │
│                                                          │
│ ⚠ Конв #42: ai_assist_score=0.7, но handoff был         │
│ ⚠ Конв #51: external bookings помечен ai_assisted        │
│ ❌ Конв #57: customer-asked-for-human, AI продолжил      │
│                                                          │
│ [Открыть очередь]   [Экспорт результатов]                │
│                                                          │
│ ── Auto-billing readiness ──                             │
│                                                          │
│ {{if accuracy >= 95% and reviewed >= 50}}                │
│ ✅ Готово к авто-биллингу                                │
│ [Enable auto-billing for cohort_first_50]                │
│ {{elif reviewed < 50}}                                   │
│ ⚪ Ждём ещё {{50 - reviewed}} проверок                   │
│ {{elif 90 <= accuracy < 95}}                             │
│ 🟡 Маргинально — extend cohort window or accept?         │
│ [Extend to first-100]  [Accept current accuracy]         │
│ {{else}}                                                 │
│ 🔴 Точность ниже порога — нужна больше работы            │
│ [Открыть некорректные]                                   │
│ {{endif}}                                                │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 7.2 Outcomes

Per Q12-δ (decisions-log r4):
- **≥ 95% accuracy at 50 reviewed** → auto-billing enabled platform-wide; cohort_first_50 → cohort_active transition
- **85-95% accuracy** → extend cohort window per Q-QO5 (continue reviewing through cohort #51-100; attempt auto-enable at 100)
- **< 85% accuracy** → pause; founder/eng investigates attribution logic; manual billing window continues

### 7.3 Per-tenant view (drill-in)

Tap a tenant row → tenant-specific cohort detail:
```
┌──────────────────────────────────────────────────────────┐
│ ← tnt_abc123 «Студия Карины»                             │
├──────────────────────────────────────────────────────────┤
│ Cohort: first_50 (added 2026-04-01)                      │
│ Reviewed: 4 / 4 (100% sample by cohort rule)             │
│ Accuracy on this tenant: 100%                            │
│ Persona quality avg: Strong                              │
│                                                          │
│ [Список разговоров (4)]                                  │
│ [Tenant settings]                                        │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 7.4 Audit + transparency

All cohort transitions + auto-billing enablement audit-logged. Tenant owner sees badge in their dashboard per [`ai-quality-observability §7.3`](./ai-quality-observability.md):
```
Ваш тенант прошёл founder cohort #1-50 review (точность {{X}}%).
Авто-биллинг включён с {{date}}.
```

---

## 8. Calibration tools (Phase 3)

### 8.1 Reviewer self-metrics

Reviewer's own meta-quality signals:
- **Skip rate** (target < 30%; > 70% triggers Quality Reviewer Lead alert per Q-QO13)
- **Inter-rater consistency** (with other reviewers; only meaningful at 2+ reviewers)
- **Decision velocity** (time per conversation; ageing alert if average > 15 min)
- **Persona linter agreement** (when linter flags violation + reviewer also marks violation = consistent; reviewer marks FP = potential linter calibration issue)

### 8.2 Persona linter feedback loop

When reviewer marks a conversation's persona-linter alert as **false positive**:
1. `wellness.qreview.linter_false_positive` event emitted
2. Aggregated across reviewers
3. If 10+ FP marks in 7d → alert: «Linter may need tuning for this pattern»
4. Engineering reviews + updates linter rules

This is the only «teaching» loop. Reviewer never directly tunes the model (out of scope).

### 8.3 Inter-rater calibration sessions (Phase 3.5+)

When 2+ reviewers active:
- Periodic «calibration round»: same N conversations reviewed by all → inter-rater consistency calculated
- Disagreement cases → review meeting (out of dashboard scope; founder facilitates)

### 8.4 Skip rate alerting

Per Q-QO13 lean: > 70% skip rate in 7d → Quality Reviewer Lead alert:
```
{{reviewer_name}} skip rate за 7д: 73% (норма < 30%).
Может быть burnout / unclear criteria / queue mismatch.
[Открыть профиль]   [Назначить разговор]
```

---

## 9. Batch operations

For low-severity items, reviewer can batch-act.

### 9.1 Eligible for batch

- Random samples (NOT auto-flagged)
- High persona confidence (linter says clean + customer CSAT ≥ 4)
- No edge cases detected in pre-screening

### 9.2 Batch UI

```
┌──────────────────────────────────────────────────────────┐
│ ← Очередь · Batch mode                                   │
├──────────────────────────────────────────────────────────┤
│ Выбрано: 5 разговоров                                    │
│                                                          │
│ ☑ #4 · Лена С. · happy path / ★4 / 0 violations          │
│ ☑ #6 · Маша П. · happy path / ★5 / 0 violations          │
│ ☑ #8 · Аня Б. · happy path / ★5 / 0 violations           │
│ ☑ #11 · Оля Д. · happy path / ★4 / 0 violations          │
│ ☑ #13 · Кат К. · happy path / ★5 / 0 violations          │
│                                                          │
│ ── Batch verdict ──                                      │
│                                                          │
│ Attribution: ⦿ Correct (для всех)                        │
│ Persona quality: ⦿ Strong (для всех)                     │
│ Handoff: ⦿ N/A no handoff (для всех)                     │
│                                                          │
│ ⚠ Batch только для low-severity. Любой ⚠/❌ — открыть    │
│ индивидуально.                                           │
│                                                          │
│ [Сохранить все 5 как «correct»]                          │
│ [Отмена]                                                 │
└──────────────────────────────────────────────────────────┘
```

### 9.3 Anti-rubber-stamp guard

- Batch is **opt-in** — reviewer must check each item
- Limit: max 10 per batch
- Audit log records batch ID for traceability
- Batch usage > 50% of reviews in 7d → Quality Reviewer Lead alert «возможно rubber-stamping»

### 9.4 Ineligible for batch

- Cohort_first_50 items (always individual per Q12-δ)
- Auto-flagged items (persona violation, low CSAT, complaint)
- Items with marginal pre-screening
- Items in cohort_at_risk tenants

---

## 10. Reviewer onboarding + handoff

### 10.1 Founder-to-CSM Lead transition

When cohort #1-50 completes + auto-billing enabled:
- Founder marks «cohort #1-50 closed»
- CSM Lead inherits review queue (cohort_active going forward)
- Founder retains read-only access for spot-checks
- Founder still reviews cohort_at_risk if escalated

### 10.2 New reviewer onboarding

When CSM Lead onboards (or new CSM Member Phase 3+):
1. Founder grants role via [`settings-hub`](../handoffs/2026-05-18-settings-hub-handoff.md) Settings → Team → Quality Reviewer
2. New reviewer shadows existing for 10 calibration conversations (training mode)
3. After ≥ 90% inter-rater consistency on calibration set → enable full queue access
4. Founder remains escalation point

### 10.3 Reviewer leaves role

- Pending reviews auto-released back to queue (locks expire)
- Audit trail preserved (their verdicts remain valid)
- Role permission revoked via Settings → Team
- New reviewer takes over (onboarding §10.2)

---

## 11. Reviewer throughput dashboard

### 11.1 Personal stats

```
┌──────────────────────────────────────────────────────────┐
│ ← My throughput (founder)                                │
├──────────────────────────────────────────────────────────┤
│ За 7 дней:                                               │
│                                                          │
│ Проверено разговоров: 47                                 │
│ Среднее время:        4.2 мин                            │
│ Skip rate:           12%                                 │
│ Batch ops:            8 (17%)                            │
│                                                          │
│ ── По типам ──                                           │
│ Cohort_first_50:     32 (68%)                            │
│ Auto-flagged:         9 (19%)                            │
│ Random sample:        6 (13%)                            │
│                                                          │
│ ── Точность ──                                           │
│ Cohort attribution: 87.5% (28/32 correct)                │
│ Inter-rater (n/a — only reviewer): —                     │
│                                                          │
│ ── Декомпозиция вердиктов ──                             │
│ Correct:    38 (81%)                                     │
│ Marginal:   6 (13%)                                      │
│ Wrong:      3 (6%)                                       │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 11.2 Team aggregate (CSM Lead view, Phase 3+)

Аналогично, но с разбивкой по reviewers + inter-rater consistency.

### 11.3 Productivity alerts

- Skip rate > 30% sustained 14d → Quality Reviewer Lead alert
- Average time > 10 min sustained → potentially queue mismatch (too complex cases for skill level)
- Velocity drop > 50% week-over-week → check-in alert

---

## 12. Audit log

Every reviewer action emits `admin.audit.event` per [`event-taxonomy.md §3.10`](./event-taxonomy.md#310-admin--system-domain) with reviewer-specific actions:

| Action | Event subtype |
|---|---|
| Open conversation review | `qreview.opened` |
| Acquire lock | `qreview.lock_acquired` |
| Save verdict | `qreview.verdict_saved` |
| Skip item | `qreview.skipped` |
| Mark false positive | `qreview.linter_false_positive` |
| Flag for retraining | `qreview.flagged_for_retraining` |
| Batch save | `qreview.batch_saved` |
| Cohort transition | `tenant.cohort.changed` (existing) |
| Auto-billing enabled | `tenant.cohort.auto_billing_enabled` (NEW) |
| Reviewer role granted/revoked | `admin.permission.changed` (existing) |

Add `tenant.cohort.auto_billing_enabled` event to event-taxonomy.md §3.10.

---

## 13. Privacy + ethical boundaries

### 13.1 Cross-tenant access scope

Quality Reviewer sees:
- Full conversation transcripts (anonymized customer name: «Ольга К.»)
- Persona violations + linter alerts
- Attribution metadata
- CSAT scores
- Tenant-level aggregate

Quality Reviewer does NOT see:
- Customer PII (phone, email, last name) — anonymized
- Customer wellness module data (strict customer-only per [`wellness-input-modules §10`](./wellness-input-modules.md))
- Customer's other-tenant data (cross-tenant boundary holds)
- AI Avatar photos (separate [`wellness-ai-avatar`](../handoffs/2026-05-19-wellness-ai-avatar-handoff.md) §11.2 boundary)
- Health screening data (per [`wellness-food-handoff §13`](../handoffs/2026-05-19-wellness-food-handoff.md))

### 13.2 What founder sees additionally vs CSM Lead

- Founder: all tenants ever; legal hold capability (with 4-eye approval)
- CSM Lead: tenant per cohort scope; no legal hold
- Both: cannot see customer wellness data

### 13.3 Anonymization rules

Per [`ai-quality-observability.md §10.3`](./ai-quality-observability.md):
- Customer name: first name + last initial
- Customer phone/email: redacted
- Wellness module data: «[customer-only — redacted]»
- Photo refs: «[photo — separate consent grant required]»

### 13.4 Reviewer-as-evidence boundary

Reviewers act on tenant's behalf for QUALITY review. They do NOT:
- Resolve customer disputes (separate flow per [`contract-offer-acceptance-display-ux.md §8`](./contract-offer-acceptance-display-ux.md))
- Reply to customer (passive review only)
- Override tenant's persona configuration
- Modify tenant's settings

If reviewer sees something concerning (abuse / fraud / customer harm), they escalate to:
- Founder for legal hold (rare; documented in audit)
- CSM Lead for outreach to tenant owner

---

## 14. Anti-patterns

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Rubber-stamp UI («Accept all» without inspection) | Defeats quality review purpose | Batch ops opt-in per §9.3; max 10 per batch; audit-tracked |
| Reviewer compensation tied to throughput | Incentivizes shallow reviews | Throughput tracked but NOT compensation factor |
| Public reviewer leaderboard | Competition over quality | Personal stats only; team aggregate to CSM Lead |
| Reviewer sees customer PII | Privacy violation | Anonymized always per §13.3 |
| Reviewer can override tenant settings | Authority overstep | NEVER — passive review only |
| Batch verdict without per-item validation | False bulk-positive risk | Each item must be opt-in checked |
| Skip without reason | Audit gap | Free-text reason required |
| Lock indefinitely | Blocking | 5-min auto-expire |
| AI auto-generates reviewer's verdict | Defeats human-in-the-loop | NEVER — reviewer always types verdict |
| «Auto-correct» — system fixes attribution if reviewer marks wrong | Loses audit; teaches wrong | Mark only; engineering investigates root cause |
| Founder can see customer wellness data | Privacy violation | Same boundaries as anyone — customer-only |
| Reviewer reviews their own tenant | Conflict of interest | Auto-exclude reviewer's own tenant from queue (rare; mostly hypothetical) |
| «Compete with other reviewers» framing | Wrong incentive | Quality > quantity always |
| Surface raw model outputs (chain-of-thought) | Engineering observability not reviewer | Reviewer sees customer-facing content only |
| Force review of every conversation | Impossibly expensive | 5% sample + auto-flagged + cohort priority per §3 |
| Tenant owner sees reviewer verdicts | Could harm relationship | Aggregate-only; specific verdicts internal |
| Customer-facing transparency of being reviewed | Self-fulfilling drift | Customer NEVER sees flag status (per [`ai-quality-observability §13`](./ai-quality-observability.md)) |

---

## 15. Acceptance criteria (engineering checklist)

- [ ] `apps/admin/quality_reviewer/` Django sub-module created (admin scope)
- [ ] RBAC: Founder + CSM Lead + (Phase 3) CSM Member roles
- [ ] Cross-tenant review queue surfaced with FIFO sort + filters §5
- [ ] Conversation review screen with 3-axis verdict + annotations §6
- [ ] 5-min lock mechanism (heartbeat-based) + collision UI
- [ ] Skip flow with required reason §6.6
- [ ] Cohort dashboard with Q12-δ auto-billing readiness state §7
- [ ] Per-tenant drill-in §7.3
- [ ] Calibration metrics dashboard §8.1
- [ ] Persona linter false-positive feedback loop §8.2
- [ ] Batch operations with anti-rubber-stamp guards §9
- [ ] Reviewer onboarding flow §10.2 (training mode, ≥90% calibration)
- [ ] Reviewer leave-role flow §10.3
- [ ] Throughput dashboard §11
- [ ] All audit events emitted §12
- [ ] Privacy enforcement §13 (PII anonymization, customer wellness data blocked, cross-tenant boundary)
- [ ] Reviewer cannot review own tenant (conflict-of-interest check)
- [ ] Tests: cross-tenant access RBAC + lock collision + verdict save + batch validation + audit trail + privacy boundary
- [ ] Tenant owner badge surfaces cohort completion per §7.4
- [ ] Documentation in `apps/admin/quality_reviewer/README.md`
- [ ] **Pre-deploy: founder approval on cohort transition thresholds** (Q-QR4)

---

## 16. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-QR1** | First reviewer = founder only? Or can founder delegate to CSM Lead immediately? | Founder only for cohort #1-50 (Q12-δ + Q-CO3 «founder for #1-50»). CSM Lead joins cohort_active after handoff per §10.1. | Founder | 🟡 |
| **Q-QR2** | Lock duration 5 min — too short / too long? | 5 min MVP (matches ai-quality-observability §5.4 default); revisit if collision data shows pattern | UX + Eng | 🟢 |
| **Q-QR3** | Reviewer reviewing own conversation history (their own tenant) — block or allow? | BLOCK auto — conflict of interest. Per §14 anti-pattern. | Policy | 🟡 |
| **Q-QR4** | Cohort transition threshold (≥95% accuracy auto-billing) — fixed or per-cohort tunable? | Fixed 95% MVP per Q12-δ. Tunable per founder decision Phase 3+. | Founder | 🔴 before first cohort completion |
| **Q-QR5** | Reviewer skip-rate alert threshold — 70% per Q-QO13 or stricter? | 70% MVP from Q-QO13; revisit after first reviewer data point | PM | 🟢 |
| **Q-QR6** | Reviewer can edit own past verdict? | YES within 24h of save (allows correction); after 24h locked + audit-tracked. Engineering may revisit older verdicts if pattern detected. | Policy + Eng | 🟡 |
| **Q-QR7** | Cohort_first_50 — strict 50 or rolling? | Strict «first 50 ever onboarded» — fixed cohort. Not rolling; new tenants join cohort_active. | Policy | 🟢 |
| **Q-QR8** | What if cohort #51 onwards shows attribution drift? | Auto-flag rate increases; if 5 consecutive new tenants show < 90% — cohort_at_risk transition; founder + eng investigate | Eng + Founder | 🟡 |
| **Q-QR9** | Founder review of cohort_at_risk — same workflow as cohort_first_50? | Similar queue priority + UI; verdict slightly different (no auto-billing gate; focus on remediation) | Founder | 🟡 |
| **Q-QR10** | Inter-rater consistency calibration — Phase 3 or 4? | Phase 3.5+ once 2+ reviewers active. MVP: single-reviewer only. | PM | 🟢 |
| **Q-QR11** | Reviewer onboarding 10 calibration convos — fixed or dynamic? | 10 fixed; raise to 20 if first reviewer shows < 80% consistency | UX | 🟢 |
| **Q-QR12** | What if reviewer's own tenant is in cohort_first_50? | Exclude reviewer from reviewing their tenant; founder reviews instead (rare case) | Policy | 🟡 |
| **Q-QR13** | Reviewer abandons review mid-conversation (browser close, network) — lock release timing? | Heartbeat-based; if no heartbeat for 2 min, lock auto-releases. Reviewer can re-acquire on return. | Eng | 🟡 |
| **Q-QR14** | Should reviewer see attribution score (ai_assist_score) numerically? | YES — helps verdict. NOT customer-facing per attribution-policy. | Eng | 🟢 |
| **Q-QR15** | Founder retire — what happens to cohort_first_50 ownership? | CSM Lead inherits cohort_first_50 read-only access; verdict authority transfers; cohort marker preserved | Founder + Legal | 🟢 |
| **Q-QR16** | Multi-language conversations (RU + KZ Phase 4+) — reviewer language requirement? | Reviewer's RU primary MVP; multi-language reviewer recruitment Phase 4+ | UX + PM | 🟢 |
| **Q-QR17** | Reviewer notes — visible to engineering for training data labeling? | YES — annotation feature §6.4 feeds ML pipeline. Privacy: notes about review, not customer PII. | Eng + Privacy | 🟡 |
| **Q-QR18** | If reviewer marks ALL cohort_first_50 conversations as «correct» — auto-billing triggers immediately at 50? | YES — passes 95% threshold trivially. Founder may want to validate sample by spot-checking 5+ before enabling. UI surfaces «выглядит слишком хорошо — проверьте sample?» heuristic at 100% in first 10. | Founder + Policy | 🟡 |
| **Q-QR19** | Batch ops max 10 — too few for power user? | 10 MVP anti-rubber-stamp; raise if abuse not observed | UX | 🟢 |
| **Q-QR20** | Tenant in PAUSED/SUSPENDED — review queue still feeds? | Auto-flagged YES (existing escalations should review); random sample NO (no new conversations active to sample from) | Policy | 🟡 |

---

## 17. Cross-document linkage

- [`ai-quality-observability.md`](./ai-quality-observability.md) §2 + §4.5 + §4.6 + §5 + §7 — owner-side companion; this doc adds platform-side
- [`attribution-policy.md`](./attribution-policy.md) §13 Q12-δ — founder cohort review workflow this implements
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — handoff appropriateness verdict basis
- [`assistant-persona.md`](./assistant-persona.md) — persona quality verdict basis
- [`event-taxonomy.md`](./event-taxonomy.md) §3.10 — admin audit events + NEW `tenant.cohort.auto_billing_enabled`
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) — cohort_archived rule + Q-QR20
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) §6 — owner's view of cohort badge (tenant-side reception)
- [`conversational-ux-framework.md`](./conversational-ux-framework.md) — voice anchors throughout
- [`wellness-input-modules.md`](./wellness-input-modules.md) §10 + [`wellness-ai-avatar-handoff.md §11.2`](../handoffs/2026-05-19-wellness-ai-avatar-handoff.md) — privacy boundaries §13
- [`../handoffs/2026-05-18-settings-hub-handoff.md`](../handoffs/2026-05-18-settings-hub-handoff.md) §18.4 — Settings → Team → Quality Reviewer role grant
- [`../decisions-log.md`](../decisions-log.md) — Q-CO3 + Q-QO5/Q-QO8/Q-QO13 / LQ5 lineage

---

## 18. What this unblocks

- **Founder Q12-δ cohort review workflow** — actual tool to do the work
- **CSM Lead handoff after cohort #1-50** — clear inheritance flow
- **Auto-billing enablement** per [`attribution-policy.md`](./attribution-policy.md) V2 validation — cohort accuracy gate is operational
- **Persona linter calibration** — feedback loop closes
- **Quality Reviewer role efficiency** — multi-day workflow with locks, batch, skip, audit
- **Inter-rater consistency tracking** — readiness for Phase 3+ multi-reviewer
- **Audit-grade decision trail** — legal + product quality oversight

## 19. What this does NOT unblock

- ❌ Replace `ai-quality-observability` owner dashboard (it's the tenant-side companion)
- ❌ Customer-facing AI quality insight (privacy boundary)
- ❌ A/B persona variant testing (Phase 2+ per Q-PE2)
- ❌ Multi-language review (Phase 4+)
- ❌ Tenant-side dispute resolution (separate flow per [`contract-offer-acceptance-display-ux.md`](./contract-offer-acceptance-display-ux.md))
- ❌ ML retraining pipeline (engineering scope; reviewer flags feed it but don't drive it)
- ❌ Skip founder approval on Q-QR4 cohort threshold before first cohort completion

---

## 20. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| **Founder** (Q-QR1/4/15 + cohort thresholds + workflow ownership) | ☐ | 🔴 PRE-DEPLOY |
| CSM Lead (workflow inheritance + reviewer-role operational details) | ☐ | |
| Backend (RBAC + cross-tenant query + lock mechanism + audit) | ☐ | |
| AI prompt engineering (linter feedback loop + retraining flag downstream) | ☐ | |
| Privacy / Legal (Q-QR12/17 + PII anonymization rules + cross-tenant boundary) | ☐ | |
| Accessibility (WCAG 2.2 AA on review screen + queue list + dashboard) | ☐ | |

## Last verified
2026-05-19 (initial draft, Quality Reviewer dashboard locked for Q-CO3 + Q12-δ workflow implementation)
