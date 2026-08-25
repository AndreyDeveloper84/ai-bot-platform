# Conversation Ownership Policy

**Date:** 2026-05-17 (r1) · DEPRECATED 2026-05-19
**Status:** ⚠ **DEPRECATED 2026-05-19** — superseded by [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md) per Ayla-first pivot.
**Scope (historical):** Operational policy for AI ↔ human collaboration in customer conversations under salon-owned-AI model.

> **⚠ DO NOT USE FOR NEW DESIGN.** This doc described the «3-tier customer-facing ownership» model from 2026-05-17 (`AI_CONTINUITY` / `HUMAN_SUPERVISED` / `HUMAN_LOCKED` with customer-visible state transitions).
>
> Per [`project_ayla_first_strategic_pivot`](./ayla-identity-and-brand.md) memory locked 2026-05-19: customer-facing 3-tier ownership is removed. New model = «Ayla always speaks; admin/founder work in separate UI; emergency system fallback» — see [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md).

---

## ⚠ Migration map

Engineering / docs consumers — use the new model:

| Old r1 concept | New r2 (Doc #3) replacement |
|---|---|
| `AI_CONTINUITY` tier | Default state — Ayla autonomous |
| `HUMAN_SUPERVISED` tier | Emergency fallback `payment_dispute` LOW/MEDIUM OR `booking_conflict` MEDIUM |
| `HUMAN_LOCKED` tier (refund / complaint / medical) | Emergency fallback `payment_dispute` HIGH/CRITICAL OR `legally_sensitive` |
| Customer-visible tier transitions | Customer-invisible per [`ayla-emergency-fallback-policy §2.12`](./ayla-emergency-fallback-policy.md) |
| «вам отвечает администратор Анна» framing | Ayla speaks: «передаю команде на проверку, вернусь в течение N» |
| `conversation.ownership_tier` field on Conversation | `conversation.active_emergency_event_id` (FK to EmergencyEvent) — schema migration per [`ayla-emergency-fallback-policy §12.3`](./ayla-emergency-fallback-policy.md) |
| Auto-resume rules | N/A — admin doesn't reply in customer thread anymore |
| Admin compose draft per `HUMAN_SUPERVISED` | Admin selects outcome via structured UI per Doc #3 §5.2; Ayla composes from template |
| SLA tier mapping (15/30/60/120 min) | Per-tier SLA matrix per Doc #3 §7 |

---

## ⚠ What stays valid (backend mechanics retained)

These principles remain valid as **backend / Ayla Pro internal concerns** — they describe what admin/founder see in their own UI, not customer experience:
- Permissions matrix (Owner / Admin / Receptionist / Master roles) — moved to [`tenant-as-provider-model §2.10`](./tenant-as-provider-model.md)
- Audit log events on every customer-facing message — extended in [`ayla-emergency-fallback-policy §8`](./ayla-emergency-fallback-policy.md) `EmergencyAuditLog`
- Retention policy (180d transcripts, 365d audit, 7y sensitive) — moved per data type to respective foundation docs
- SLA discipline — values changed; structure preserved in Doc #3 §7

---

## ⚠ Original r1 content preserved below for historical trace

The sections below are retained for migration trace + engineering reference for in-flight code. **DO NOT cite for new design.** New design references foundation Docs #1-5.

Historical:

Foundation: [single-assistant identity](~/.claude/projects/.../memory/project_single_assistant_identity.md) (also deprecated 2026-05-19; superseded by [`project_ayla_personal_ai`](~/.claude/projects/.../memory/project_ayla_personal_ai.md)). Customer always sees one AI-assistant; this doc is the **invisible** machinery behind it.

---

## 1. The 3-tier ownership model

A conversation is always in exactly **one** ownership tier at any moment. Tier determines:
- Whether AI can compose and auto-send replies
- Whether AI's draft requires admin approval
- Which audit events fire
- Which SLA timing applies
- Which customer-facing framings are allowed

### Tier 1 — AI_CONTINUITY
**AI fully autonomous.** Auto-resumes after admin reply.

Default for low-risk handoff reasons:
- `out_of_catalog` — клиент спросил услугу не из каталога
- `low_confidence` — AI не понял intent с уверенностью
- `booking_edge_case` — техническая сложность (например, перенос на 3+ месяцев вперёд)
- `multiple_failures` — 3+ failed intents подряд (но не emotionally charged)
- `price_question_high_intent` — конкретный price-question с покупательским намерением
- `client_ready_to_book` — flagged как готовый к покупке (для admin awareness, не блокировка)

### Tier 2 — HUMAN_SUPERVISED
**AI composes, admin approves.** AI never auto-sends after entering this tier.

Default for medium-risk handoff reasons:
- `vip_flagged` — VIP customer (LTV > tenant threshold, или manual tag)
- `returning_client` с edge — постоянный клиент в нестандартной ситуации
- `schedule_conflict` — конфликт расписания / двойная запись
- `payment_issue` (non-refund) — вопрос по оплате не требующий возврата
- `explicit_human_request` non-charged — клиент попросил человека спокойно, без эмоционального contexta

### Tier 3 — HUMAN_LOCKED
**AI silent.** AI does not compose, does not suggest, does not send.

Default for high-risk reasons:
- `complaint_sentiment` — sentiment-analysis flagged negative с intent жалоба
- `sensitive_topic` — медданные, аллергии, состояние здоровья
- `medical_contraindication` — противопоказания упомянуты или подразумеваются
- `payment_issue` (refund) — запрос на возврат денег
- `explicit_human_request` (charged) — клиент попросил человека в эмоциональном tone

## 2. Handoff reason → tier mapping (complete)

| Handoff reason | Default tier | Auto-resume after admin reply | Customer-facing framing |
|---|---|---|---|
| `out_of_catalog` | AI_CONTINUITY | ✅ | Standard assistant voice |
| `low_confidence` | AI_CONTINUITY | ✅ | Standard |
| `booking_edge_case` | AI_CONTINUITY | ✅ | Standard |
| `multiple_failures` | AI_CONTINUITY | ✅ | Standard |
| `price_question_high_intent` | AI_CONTINUITY | ✅ | Standard |
| `client_ready_to_book` | AI_CONTINUITY | ✅ | Standard (admin notified but AI proceeds) |
| `vip_flagged` | HUMAN_SUPERVISED | ⚠ draft only | «Уточнил для вас особо…» |
| `returning_client` (edge) | HUMAN_SUPERVISED | ⚠ draft only | «Помню вашу историю — уточнил детали…» |
| `schedule_conflict` | HUMAN_SUPERVISED | ⚠ draft only | «Уточнил расписание — есть нюанс…» |
| `payment_issue` (non-refund) | HUMAN_SUPERVISED | ⚠ draft only | «Передал в команду для уточнения…» |
| `explicit_human_request` (calm) | HUMAN_SUPERVISED | ⚠ draft only | «Конечно, передам команде…» |
| `complaint_sentiment` | HUMAN_LOCKED | ❌ | «Передал руководителю салона…» |
| `sensitive_topic` | HUMAN_LOCKED | ❌ | «Передал специалисту салона…» |
| `medical_contraindication` | HUMAN_LOCKED | ❌ | «Передал мастеру для уточнения…» |
| `payment_issue` (refund) | HUMAN_LOCKED | ❌ | «Передал администратору — это финансовый вопрос…» |
| `explicit_human_request` (charged) | HUMAN_LOCKED | ❌ | «Сейчас же передам команде…» |

Admin can manually override tier per conversation (audited).

## 3. SLA tiers (replaces single 2h threshold)

| Time since handoff started | State | Visual cue (admin UI) | System action |
|---|---|---|---|
| 0–14:59 min | `HANDOFF_PENDING` | Normal styling | None |
| 15 min | `WARNING` | Yellow left-border on row | Soft push to assigned admin: «Мария ждёт 15 минут» |
| 30 min | `HIGH_PRIORITY` | Orange border + ⚠ icon | Push to ALL admins + если включено: alert в CSM-чат |
| 60 min | `STALE_PENDING` | Red border + 🔴 status dot | Escalate to CSM (CSM sees conversation in their own queue) |
| 120 min | `ABANDONMENT_RISK` | Red flashing border (reduce-motion: solid) | Push to founder (если есть в platform); founder-dashboard alert |
| 24h no admin reply | `AUTO_ABANDONED` | Grayed out + tag | Assistant sends «прости что долго — давай попробуем ещё раз?»; conversation re-opens if customer replies |

Per-tenant SLA tuning (post-MVP): tenant может выставить custom thresholds (например, премиум-салон требует 5/15/30/60 минут).

## 4. Permissions matrix (by role)

Default roles + capability mapping. Tenants создают custom roles (post-MVP) с custom mapping.

### Roles
- **Owner** — владелец салона (Карина)
- **Admin** — администратор (Аня)
- **Receptionist** — ресепшен / стажёр (новый сотрудник)
- **Master** — мастер (видит только свои conversations)

### Capabilities

| Capability | Owner | Admin | Receptionist | Master |
|---|---|---|---|---|
| View conversation list (all) | ✅ | ✅ | ✅ | own only |
| View conversation transcript | ✅ | ✅ | ✅ | own only |
| View customer name | ✅ | ✅ | ✅ | own only |
| Click to reveal customer phone (audited) | ✅ | ✅ | ✅ (with audit log) | ❌ **никогда** — см. сноску ¹ |
| View customer medical notes | ✅ | only if has `medical_role` flag | ❌ | ❌ |
| View customer LTV / financial | ✅ | ✅ | ❌ | ❌ |
| Send reply | ✅ | ✅ | ✅ | own only |
| Promote conversation to HUMAN_LOCKED | ✅ | ✅ | ✅ (safety) | ✅ (safety) |
| Demote conversation from HUMAN_LOCKED | ✅ | ✅ | ❌ | ❌ |
| Approve assistant auto-resume after HUMAN_LOCKED | ✅ | ✅ | ❌ | ❌ |

> ¹ **Master = ❌ здесь окончательно, DRF-1360 / owner decision OD-W2-2 (24.08):**
> «телефон клиента исполнителю не передаётся ни в каком виде». Не «полный
> телефон запрещён, а маска можно» — исключения на последние две/четыре цифры
> решение не оставляет. Мастерская поверхность проверяется на это на бэкенде:
> `apps/master_api/pii.py` + `apps/master_api/tests/test_pii_boundary.py`; любое
> поле с телефоном клиента в мастерском ответе роняет CI. Reveal-эндпоинт для
> мастера — **out of scope, не deferred**: строить только после нового
> отдельного решения владельца по PII.
>
> ⚠️ **Открытый вопрос для владельца (не решён здесь):** в пилоте solo-мастер
> — это owner + master в одном человеке (ADR-0008, `is_solo_provider`), и с
> solo-поверхности есть вход «Салон» в `/admin/*`. Строка Owner = ✅ и строка
> Master = ❌ в этом случае описывают одного и того же человека. Сегодня утечки
> нет — салонные ручки телефон клиента тоже не отдают
> (`apps/admin_api/views_customers.py`: «Why the phone never comes back»). Но
> если телефон клиента когда-нибудь появится на админской поверхности, solo-мастер
> получит его через «Салон». Решать, следует ли запрет за ролью или за человеком,
> — владельцу.
| Add response to FAQ / KB | ✅ | ✅ | ❌ | ❌ |
| Add service to catalog | ✅ | ✅ | ❌ | ❌ |
| Snooze conversation (4h) | ✅ | ✅ | ✅ | own only |
| Escalate to CSM | ✅ | ✅ | ✅ | own only |
| Block customer (with confirm) | ✅ | ✅ (confirm required) | ❌ | ❌ |
| Manage tenant roles | ✅ | ❌ | ❌ | ❌ |
| Export conversation data | ✅ | ❌ | ❌ | ❌ |
| Tune assistant persona | ✅ | ❌ | ❌ | ❌ |
| Change assistant gender/name | ✅ | ❌ | ❌ | ❌ |
| Override SLA tiers | ✅ | ❌ | ❌ | ❌ |
| View audit log | ✅ | own actions only | own actions only | own actions only |

## 5. Mandatory audit events

Every action writes to `apps/audit` event stream:

### Conversation lifecycle
- `conversation.created` — new conversation started
- `conversation.handoff_triggered` — AI triggered handoff (with reason, confidence, intent classification)
- `conversation.tier_changed` — automatic OR manual; capture from/to + reason + actor
- `conversation.assigned` — admin took ownership
- `conversation.resolved` — admin marked resolved (with resolution type)
- `conversation.escalated_to_csm`
- `conversation.snoozed`
- `conversation.auto_abandoned` — 24h no admin reply triggered fallback flow

### Reply actions
- `conversation.message_sent` — every customer-facing message (capture: composed_by AI/admin_id, content_hash, identity_used)
- `conversation.message_failed` — send attempt failed
- `conversation.draft_approved` — admin approved AI draft (HUMAN_SUPERVISED tier)
- `conversation.draft_rejected` — admin rejected draft and wrote own
- `conversation.draft_edited` — admin edited AI draft before sending

### Sensitive access (PII / medical)
- `conversation.phone_revealed` — who clicked to unmask
- `conversation.medical_notes_viewed` — who viewed
- `conversation.note_added` — who added a note, content_hash
- `conversation.customer_blocked` — who, reason
- `conversation.export_initiated` — who exported

### AI control
- `conversation.bot_resumed` — explicit admin action (after HUMAN_LOCKED)
- `conversation.bot_locked` — explicit admin action (force lock)
- `conversation.tier_override` — admin manually changed tier
- `conversation.faq_proposed` — AI suggested learning candidate
- `conversation.faq_accepted` — admin added to KB
- `conversation.faq_rejected` — admin dismissed

### Quality / safety
- `conversation.persona_violation_warned` — pre-send check flagged tone issue
- `conversation.persona_violation_overridden` — admin sent anyway
- `conversation.forbidden_phrase_blocked` — pre-send check blocked send
- `conversation.suspicious_activity` — abuse / spam pattern detected

### Required fields per event
- `event_type`, `occurred_at` (UTC ISO), `tenant_id`, `conversation_id`, `actor_id` (admin or 'ai'), `actor_role`, structured payload

### Retention
- 365+ days for compliance review
- Queryable by founder/owner
- Export endpoint exposed in Settings → Аудит (Owner only)

## 6. Retention policy — 4-layer working model (r2)

Subject to final legal sign-off (Q-C3 execution task). Updated 2026-05-18 r4 per user product review — explicit 4-layer architecture instead of flat table:

### Layer 1 — Operational transcripts (full conversation text)
- **Retention**: 180 days
- **After 180d**: PII removed (names → UUID, phones → masked), individual messages purged, anonymized aggregate retained for analytics
- **Purpose**: salon's day-to-day operations, recent conversation context
- **Customer-deletion request**: honored within 30d (soft-delete window for reversal, then hard-delete)

### Layer 2 — Audit trail
- **Retention**: 365+ days
- **Payment/billing audit**: longer (TBD, awaits legal — possibly aligned with Layer 3)
- **Purpose**: incident investigation, security review, billing disputes, regulatory compliance
- **Stripped down**: contains event_type / actor / timestamp / hashes — no full message content

### Layer 3 — Booking and payment records (`BookingRequest`, `BillingEvent`)
- **Retention**: up to 7 years
- **Purpose**: бухгалтерия, налоговая отчётность, disputes, financial audit per ГК РФ + НК РФ
- **Includes**: attribution metadata (per attribution-policy.md), payment events, refunds, dispute resolutions
- **NOT included**: conversation content (that's Layer 1)

### Layer 4 — Sensitive/medical data
- **Default principle**: **minimize**. Prefer structured flags over full text.
- **Bad**: storing «у меня диабет, принимаю метформин 1000 мг» verbatim
- **Good**: storing `sensitive_flag=True`, `reason=medical_contraindication`, `decision=handoff_to_master` + audit who handled
- **If full text needed** (e.g., for medical compliance audit): separate explicit customer consent + 6 months full retention max + 1 year as anonymized risk-flags only
- **Access**: role-gated per [§4 permissions matrix](#4-permissions-matrix-by-role) — only roles with `medical_role` flag

### Per-tenant overrides
Allowed **longer only**, not shorter — for tenant-specific legal compliance (e.g., medical license requires longer retention). Never shorter than baseline (FZ-152 minimum applies).

### Physical infrastructure
- **RU-located storage required** per ФЗ-152 для российских персональных данных
- Backup also RU-located
- Cross-border data transfer (if any) requires explicit consent + Roskomnadzor notification

### Customer-deletion (GDPR-like) per OP6
- Process: customer e-mails support@ → CSM verifies identity via initData phone match
- Soft-delete first (30-day reversal window) → hard-delete
- Audit log retained (Layer 2) even after customer hard-delete — for dispute defense
- Customer profile fields (name, phone, notes) deleted; bookings (Layer 3) keep customer reference as UUID-only token (no PII)

## 7. Customer-facing identity disclosure rules

### Always honest
If customer asks any version of «вы бот?», «это автоответчик?», «человек или машина?»:
- Always answer truthfully
- Standard answer: «Я цифровой помощник салона. Со мной можно записаться, узнать цены, отменить или перенести визит. Если возникнет сложный вопрос — подключу команду.»

### Identity disclosure scenarios

| Scenario | Customer sees |
|---|---|
| Normal interaction | Assistant name (assistant) — no disclosure needed |
| Customer asks if AI | Truthful: «Я цифровой помощник салона…» |
| HUMAN_SUPERVISED tier | Assistant voice continues; «уточнил для вас» framing — implicit team backing |
| HUMAN_LOCKED non-regulated (complaint) | «Передал руководителю салона — она свяжется с вами» — team mentioned, no individual name |
| HUMAN_LOCKED regulated (medical, refund, legal) | Explicit: «Вам отвечает администратор Анна» — named individual, identified role |
| Long delay (>30 min handoff): | «Сейчас немного дольше, чем обычно — команда разбирается с вашим вопросом» |

## 8. Channel-specific notes

### MAX
- Single sender bot identity per tenant
- Avatar customizable; name customizable («Помощник Студии Карина»)
- Channel native features (HapticFeedback in mini-app, BackButton) work the same regardless of who composed reply

### Telegram
- Same single sender identity
- Inline keyboards rendered consistently

### Mini App (MAX or Telegram)
- Full UI rendered in assistant identity
- No bot/admin badge anywhere visible to customer
- Internal admin-facing dashboard может показывать «отвечено Anya» — but customer mini-app никогда

## 9. Edge cases

### Customer messages while in HUMAN_LOCKED tier
- AI silent
- Admin gets notification «Мария написала ещё, ждёт ответа»
- Customer doesn't get auto-acknowledgement (avoid generic «спасибо за сообщение»)
- If 5+ min: assistant sends «Передаю мастеру — ответит совсем скоро»

### Two admins try to take same conversation
- Lock-based: first admin to click «взять в работу» wins
- Second admin sees `LockBanner`: «Anya работает с этим диалогом с 14:20»
- Second admin can «перехватить» (audited) — replaces lock, original admin notified

### Admin replies as «assistant» but accidentally signs their name
- Pre-send check warns: «Имя «Anya» в ответе — отправить как explicit admin вместо assistant?» — admin chooses

### Customer asks for specific admin by name
- AI: «Передам Анне ваше сообщение, она ответит как только сможет»
- Routing logic: if requested admin available, assign; if not, notify with «Анна сейчас занята, ответит позже — или передать другому администратору?»

### Bot mis-classifies tier (e.g., AI_CONTINUITY for actual complaint)
- Admin can manually promote to HUMAN_LOCKED at any time
- Adds `tier_override` audit event with reason
- Future ML retraining: these overrides used as labeled training data

### Customer deletes their own account
- Conversation transcripts immediately hidden from non-Owner roles
- Audit retained (Owner-visible)
- Customer's actual messages anonymized within 30 days (per retention policy)

## 10. Implementation checklist

### Engineering
- [ ] `Conversation.ownership_tier` field (enum)
- [ ] `Conversation.assigned_admin_id` field
- [ ] `Conversation.handoff_reason` enum (15+ values per §2 table)
- [ ] `Conversation.sla_tier` derived field (computed from `handoff_started_at` + current time)
- [ ] Bot inference layer: check `ownership_tier` before composing/sending
- [ ] HUMAN_SUPERVISED draft queue + admin approval flow
- [ ] HUMAN_LOCKED hard-stop on AI send
- [ ] Audit event emitter on every action above (§5)
- [ ] Permission middleware in API layer — every endpoint checks role + capability
- [ ] PII reveal endpoints write audit event before returning value
- [ ] Retention cron jobs (180-day anonymizer, 30-day deletion finalizer)
- [ ] SLA escalation cron (15/30/60/120-min thresholds, push dispatch)

### UX
- [ ] Tier visible to admin (badge or border color)
- [ ] AI behavior gating reflected in ReplyBox (draft mode vs hard-stop)
- [ ] Reveal-phone click → confirm + audit (no silent reveal)
- [ ] Medical notes section role-gated in customer sidebar
- [ ] Tier history visible in conversation detail (audit-style timeline)
- [ ] Audit log viewer in Settings (Owner only)

### Persona/quality (interlocks with `assistant-persona.md`)
- [ ] Pre-send persona check (§9 of persona doc)
- [ ] Forbidden-phrase blocker
- [ ] Admin can override (audited)
- [ ] Tier-aware framing: AI knows tier → uses appropriate framing per §7 table

### Legal/compliance
- [ ] Privacy policy update (single-assistant disclosure, retention tiers)
- [ ] Terms of service: AI usage disclosure
- [ ] Customer-facing FAQ on landing: «Что такое цифровой помощник?»
- [ ] Tenant agreement: tenant accepts responsibility for AI-suggested replies they approve

## 11. Cross-document linkage

- Strategic foundation: [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md)
- Strategic foundation: [`memory/project_conversation_ownership_tiers.md`](~/.claude/projects/.../memory/project_conversation_ownership_tiers.md)
- Voice/tone rules: [`docs/design/policies/assistant-persona.md`](./assistant-persona.md)
- UX implementation: [`docs/design/handoffs/2026-05-17-conversations-handoff.md`](../handoffs/2026-05-17-conversations-handoff.md)
- Onboarding integration: [`docs/design/handoffs/2026-05-17-salon-onboarding-handoff.md`](../handoffs/2026-05-17-salon-onboarding-handoff.md) r3+

## 12. Open questions

> **📌 Authoritative status:** see [`decisions-log.md`](../decisions-log.md) for current status of OP1–OP7. Below is initial framing for reference.

| # | Question | Owner |
|---|---|---|
| OP1 | Concurrent admin model — lock-based MVP confirmed. Collaborative (Slack-style cursor visibility) — backlog | PM |
| OP2 | Custom per-tenant SLA tiers — MVP fixed, custom in v1.1? | PM |
| OP3 | Granular permission editor for custom roles — MVP fixed 4 roles? | PM + Eng |
| OP4 | Retention policy legal sign-off — must complete pre-launch | Legal |
| OP5 | Audit log export format — CSV / JSON / structured-PDF? | Founder |
| OP6 | Customer-deletion request UX (where does customer request, how does tenant verify identity?) | Legal + PM |
| OP7 | Multi-tenancy of customer profile — если клиент пишет в 2 разных салона, разные профили или один? Lean: разные (chat history per tenant), но shared phone-based linkage для analytics opt-in | Founder |
