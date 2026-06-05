# Договор-оферта Acceptance + Display UX

**Date:** 2026-05-19 r1
**Status:** Foundational — production launch blocker (Q12-ε per attribution-policy §13)
**Reads:** [`attribution-policy.md`](./attribution-policy.md) §13, [`conversation-ownership-policy.md`](./conversation-ownership-policy.md), [`customer-profile-management-ux.md`](./customer-profile-management-ux.md), [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md), [`owner-conversational-templates.md`](./owner-conversational-templates.md), [`../handoffs/2026-05-17-salon-onboarding-handoff.md`](../handoffs/2026-05-17-salon-onboarding-handoff.md), [`../briefings/legal-consult-briefing.md`](../briefings/legal-consult-briefing.md)

> Q12-ε per [`attribution-policy.md §13`](./attribution-policy.md#13-draft-договор-оферта-clause-8-mandatory-elements-per-q12-ε) drafted 8-element договор. UX for acceptance (tenant onboarding) + display (anytime reference) + version updates + dispute flow + customer-side transparency was missing. This doc locks the UX before first commercial billing.

---

## 0. Why this exists

### The gap

[`attribution-policy.md §13`](./attribution-policy.md#13-draft-договор-оферта-clause-8-mandatory-elements-per-q12-ε) drafts the 8-element договор clause for attribution + billing + disputes. [`legal-consult-briefing.md`](../briefings/legal-consult-briefing.md) batches Q14 + Q-C3 + Q12-ε for RU юрист review.

But **no UX exists** for:
- HOW tenant accepts договор at onboarding (currently «sign up» flow doesn't show terms)
- WHERE current terms version lives in product (Settings? Footer? Hidden?)
- WHAT happens when terms update (silent? forced re-accept? grandfathered?)
- HOW customer learns about platform-side terms (mostly NOT directly relevant — salon pays platform, not customer — but customer DOES have data rights from same terms)
- HOW dispute flow works UX-wise (Q12-ε §6 «dispute process: e-mail/dashboard, 48h CSM SLA»)

Without this spec, engineering ships onboarding without terms acceptance → first paying salon has no legal binding → CSM dispute first claim → cannot defend → trust + revenue lost.

### The promise

Single source for:
- Tenant договор acceptance flow (onboarding Phase 1)
- Settings Hub current terms display (always accessible)
- Terms version updates with explicit re-acceptance protocol
- Customer-side transparency (договор exists, customer has data rights even though they don't pay)
- Dispute flow UX per Q12-ε §6-8
- Per-state behavior (PAUSED tenant, SUSPENDED tenant, ARCHIVED)
- Anti-patterns (no dark patterns, no pre-checked, no buried terms)
- PII handling in terms (no customer PII in terms display)

---

## 1. Scope

### IN
- Tenant договор acceptance UX at onboarding (Phase 1 Settings → договор)
- Settings Hub «Договор и оферта» section (always accessible)
- Terms version control + re-acceptance protocol on material updates
- Customer-side transparency (Профиль → Помощь → «о договоре» informational)
- Dispute submission UX per Q12-ε §6-8 (email + dashboard hybrid)
- 4 audit events per acceptance / view / dispute / version-change
- Per-state behavior (acceptance valid through PAUSED / SUSPENDED; ARCHIVED freezes terms but doesn't bind new)
- Versioning policy + grandfathering rules
- Customer-pays tier preview structure (Phase 3+; customer договор will exist then)
- Anti-patterns

### OUT
- Legal text content itself (Q12-ε draft in attribution-policy §13; RU юрист finalizes per legal-consult-briefing)
- Multi-jurisdiction terms (RU MVP; international Phase 5+)
- Договор generation API for tenant white-label (Phase 4+)
- Dispute resolution logic / arbitration / court process (legal scope)
- Договор renewal economics (договор is indefinite; subscription billing separate)
- Special enterprise contracts beyond standard оферта (manual sales scope)

---

## 2. Two audiences + their relationship to the договор

### 2.1 Tenant (salon owner)
- **Active party** — accepts договор at onboarding, bound by it commercially
- Sees full договор text + version history + dispute path
- Re-accepts on material version updates

### 2.2 Customer (salon's customer)
- **Indirect party** — NOT bound by договор-оферта commercially (salon pays platform, customer doesn't pay platform in MVP)
- Has data rights derived from договор (privacy / retention / deletion per OP6)
- Can read договор if interested («о договоре» link in Профиль → Помощь)
- In Phase 3+ customer-pays tier: customer becomes paying party + separate customer-side договор needed (this doc preview-scopes that, doesn't fully design)

### 2.3 CSM / founder
- Verifies tenant acceptance + dispute resolution
- Updates terms version + manages re-acceptance rollout
- Sees aggregate acceptance + dispute stats

---

## 3. Tenant договор acceptance flow at onboarding

### 3.1 Where in onboarding

Per [`../handoffs/2026-05-17-salon-onboarding-handoff.md`](../handoffs/2026-05-17-salon-onboarding-handoff.md) — договор acceptance lands at **Phase 1.5** (between Phase 1 «account creation» and Phase 2 «catalog setup»). NOT pre-account-creation (that's a barrier to signup); NOT post-launch (that's too late — tenant has already been using platform).

### 3.2 Acceptance screen

```
┌──────────────────────────────────────────────┐
│ ← Договор-оферта                              │
├──────────────────────────────────────────────┤
│ Прежде чем продолжить, ознакомьтесь с        │
│ договором-офертой и согласитесь с условиями. │
│                                              │
│ Версия: {{contract_version}} · от {{date}}   │
│                                              │
│ Краткое содержание:                          │
│                                              │
│ • Что входит в тариф (платежи + AI-помощник) │
│ • Как платите (590₽/мес + 100₽ за каждую     │
│   запись через помощника, первые 50 клиентов)│
│ • За что не берём денег (отмена, перенос,    │
│   тесты, ручные записи)                      │
│ • Как возвращаем (отмена за 1 час, не-явка)  │
│ • Как храним данные (180 дней переписка,     │
│   3 года записи)                             │
│ • Что делать при споре (написать в течение   │
│   30 дней, ответим за 48 часов)              │
│ • Когда расторгнуть договор (когда хотите,   │
│   без штрафа)                                │
│                                              │
│ ──── Полный текст ────                       │
│ [скроллируемая область с full договор text]  │
│                                              │
│ ──── Согласие ────                           │
│                                              │
│ ☐ Я прочитал(а) договор-оферту и согласен(на)│
│   с условиями                                │
│                                              │
│ ☐ Я подтверждаю, что мне больше 18 лет и    │
│   я уполномочен(а) заключать договор от     │
│   имени студии                               │
│                                              │
│ [Не сейчас]               [Принять и продолжить]│
└──────────────────────────────────────────────┘
```

### 3.3 «Принять и продолжить» enables only when BOTH checkboxes checked

- Pre-checked checkboxes FORBIDDEN per Q12-ε requirements + RU consumer protection norms
- Must be explicit click (not just keyboard navigation tab)
- Hover state shows tooltip on hover (educational)

### 3.4 «Не сейчас» button

Allowed — tenant can defer acceptance. But:
- Tenant is in «pre-acceptance» state per §6 — limited functionality
- Cannot complete onboarding past Phase 1.5
- Cannot receive bot bookings (which would create billable events)
- Dashboard shows persistent banner «Прежде чем запустить — нужно принять договор»

### 3.5 Summary block design

The 7 bullet summary is REQUIRED — RU consumer protection prefers explicit «key terms summary» before full text. Each bullet is human-language plain Russian, not legalese. Below is the full договор-оферта text (scrollable).

### 3.6 Variables in summary

| Variable | Source |
|---|---|
| `{{contract_version}}` | Current terms version per §7 |
| `{{date}}` | Date of current version publication |
| `{{base_price}}` | Tenant pricing tier base fee |
| `{{per_booking_price}}` | Per-booking attribution fee |
| `{{founder_cap}}` | First 50 founder pricing (per Q9 hybrid) |

If tenant is in founder-50 cohort: emphasize founder pricing. Otherwise: standard pricing.

### 3.7 Events emitted on acceptance

- `tenant.contract.viewed` (NEW — add to event-taxonomy §3.10) with `contract_version`, `viewed_at`
- `tenant.contract.accepted` (NEW) with `contract_version`, `accepted_at`, `accepted_by` (user_id), `acceptance_method='wizard'`
- Audit log: per [`event-taxonomy §3.10`](./event-taxonomy.md#310-admin--system-domain) `admin.audit.event` with `action='contract.accepted'`

### 3.8 Outcome — pass / fail

#### Pass (acceptance recorded)
- `TenantContract` row created (see §10 data model)
- Tenant transitions from PRE_ACCEPTANCE → ACTIVE_ACCEPTANCE state per §6
- Onboarding wizard continues to Phase 2 (catalog setup)
- Owner DM confirmation: «Готово. Договор принят (версия {{version}}). Двигаемся дальше.»

#### Defer («Не сейчас»)
- No `TenantContract` row created
- Tenant stays in PRE_ACCEPTANCE state
- Periodic gentle reminders (per [`notification-preferences-ux.md`](./notification-preferences-ux.md) operational class): day 1, day 3, day 7
- After day 7 no acceptance: CSM follow-up

---

## 4. Settings Hub «Договор и оферта» section

### 4.1 Always-accessible reference

Per [`../handoffs/2026-05-18-settings-hub-handoff.md`](../handoffs/2026-05-18-settings-hub-handoff.md) §18.4 NEW SH1 cards — Settings → «Аккаунт» → «Договор и оферта» row.

### 4.2 Settings layout

```
┌──────────────────────────────────────────────┐
│ ← Договор и оферта                            │
├──────────────────────────────────────────────┤
│ Действующий договор                           │
│ Версия: {{current_version}} · действует с    │
│   {{accepted_at}}                            │
│ Принят: {{accepted_by_name}}                 │
│                                              │
│ [Открыть текст договора]                     │
│ [Скачать PDF]                                │
│                                              │
│ ── История версий ──                          │
│                                              │
│ {{version_2}} · {{accepted_date}} {{actor}}  │
│   [Открыть]   [Сравнить с текущей]           │
│ {{version_1}} · {{accepted_date}} {{actor}}  │
│   [Открыть]                                  │
│                                              │
│ ── Открытые вопросы / Споры ──                │
│                                              │
│ Активных споров нет                          │
│   ИЛИ                                        │
│ {{dispute_count}} активных споров            │
│   [Открыть очередь]                          │
│                                              │
│ ── Создать спор ──                            │
│                                              │
│ Если что-то по биллингу или начислениям      │
│ кажется неверным:                            │
│ [Создать спор]                               │
│                                              │
│ ── Расторжение договора ──                    │
│                                              │
│ Договор расторгается в момент архивации      │
│ студии (Настройки → Аккаунт → Архивировать). │
│ Открытые споры рассматриваются 30 дней       │
│ после расторжения.                           │
└──────────────────────────────────────────────┘
```

### 4.3 «Открыть текст договора»

Opens full договор-оферта в modal:
- Searchable text
- Highlighted version-diff if applicable
- «Скачать PDF» button → generated PDF with tenant's specific acceptance metadata embedded

### 4.4 «Сравнить с текущей»

Side-by-side diff between old version + current. Useful when terms updated and tenant wants to see what changed.

### 4.5 Permissions

Per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) §4:
- Owner: full read + can submit disputes + can accept new versions
- Admin: read + can submit disputes; cannot accept new versions on owner's behalf
- Receptionist: read only
- Master: read only (rare — they're not party to договор anyway)

---

## 5. Terms version updates + re-acceptance protocol

### 5.1 Types of updates

| Type | Definition | Re-acceptance required? |
|---|---|---|
| **Material** | Changes pricing, refund rules, retention, dispute process | YES — explicit re-accept by owner |
| **Substantial** | Changes data handling, third-party integrations, support SLA | YES — explicit re-accept by owner |
| **Clarifying** | Improves wording without changing substance | NO — version note + email notification |
| **Compliance** | Legal mandate (new law, regulator directive) | YES — explicit re-accept by owner; 30d grace per RU consumer law |

### 5.2 Re-acceptance flow (material / substantial / compliance updates)

1. New version published by platform team
2. All active tenants notified via:
   - Owner Mini App banner (persistent until acted)
   - Owner DM with version summary + diff link
   - Email if tenant configured
3. Owner has 30 days to act (per RU consumer law for material changes)
4. Within 30 days, owner can:
   - Read new terms + accept → continue with new version
   - Read new terms + reject → terminate договор (tenant archived after dispute period)
5. After 30 days no action: tenant pre-suspended (cannot make new bookings until acted)

### 5.3 Banner for re-acceptance

```
┌──────────────────────────────────────────────┐
│ ⚠ Новая версия договора                       │
│                                              │
│ Версия {{new_version}} от {{date}} — нужно   │
│ принять до {{deadline}} ({{N}} дней)         │
│                                              │
│ [Что изменилось]   [Принять]                 │
└──────────────────────────────────────────────┘
```

Tap «Что изменилось» → diff view per §4.4.

### 5.4 Clarifying updates

No re-acceptance. Tenant gets a soft notification in daily digest:
```
Договор обновлён до версии {{N}} (только уточнения формулировок). Подробнее в Настройках.
```

### 5.5 Grandfathering rules

- Tenants on accepted version V remain bound by V until they accept newer version
- Pricing per V is honored for billable events created BEFORE re-acceptance of V+1
- After re-acceptance: new pricing applies to NEW billable events only (no retroactive)

### 5.6 Events emitted on update

- `tenant.contract.version.published` (system event; tenant-id null since platform-wide)
- `tenant.contract.notification.sent` per tenant
- `tenant.contract.accepted` on each re-acceptance (with `contract_version` = new version)
- `tenant.contract.rejected_or_lapsed` if tenant doesn't act in 30d window

---

## 6. Per-tenant-state behavior

Per [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) lifecycle states:

| Tenant state | Contract acceptance binding? | Can submit dispute? | Can re-accept new version? |
|---|---|---|---|
| **PRE_ACCEPTANCE** | n/a — not yet accepted | NO | YES (this IS first acceptance) |
| **ACTIVE_ACCEPTANCE** | YES — full binding | YES | YES |
| **AT_RISK_BILLING** | YES — binding | YES | YES (encouraged — to lock new pricing before suspend) |
| **PAUSED** (voluntary) | YES — binding (договор + acceptance remain valid) | YES | YES |
| **SUSPENDED** | YES — binding for past events; restricted for new | YES (active disputes from before) | NO (can't accept new while suspended; resolve suspension first) |
| **ARCHIVED** | TERMINATED at archive moment; data retention per §14 of tenant-suspension | NO new disputes; existing 30d window | NO |

### 6.1 Contract in PRE_ACCEPTANCE state — special handling

Tenant in PRE_ACCEPTANCE cannot:
- Receive customer bot interactions (booking attribution would create billable, but no договор bound)
- Use AI-driven bookings (same)
- Run marketing campaigns

Tenant in PRE_ACCEPTANCE can:
- Complete profile setup
- Configure schedule
- Invite masters (but invites are «pending acceptance» rather than active)
- Browse the platform UI

This protects platform from billing without legal binding.

### 6.2 Договор lifecycle relative to billing

- Day 1: tenant signs up → PRE_ACCEPTANCE
- Day 1: tenant accepts договор → ACTIVE_ACCEPTANCE
- Days 1+: tenant uses platform; billable events accrue; договор version V1
- Day 60: platform releases V2 (material change)
- Days 60-90: owner sees re-acceptance banner; bills continue under V1 pricing for events created before re-acceptance
- Day 75: owner re-accepts V2 → договор version V2; events after day 75 billed under V2
- Days 76+: bills under V2 pricing

---

## 7. Customer-side transparency

### 7.1 Where customer encounters договор

NOT during normal use (customer is not a party in MVP). Customer can find договор via:
- [`customer-profile-management-ux.md §7`](./customer-profile-management-ux.md) Помощь → «О договоре» link
- [`customer-profile-management-ux.md §6`](./customer-profile-management-ux.md) Приватность и данные → «Подробнее о хранении» (which references договор retention clauses)

### 7.2 Customer-side info modal

```
┌──────────────────────────────────────────────┐
│ О договоре                                   │
├──────────────────────────────────────────────┤
│ Студия «{{salon_name}}» заключила договор    │
│ с платформой о работе помощника-AI.          │
│                                              │
│ Вы НЕ являетесь стороной этого договора.    │
│ Студия платит платформе за работу AI.        │
│                                              │
│ Что вам важно знать:                         │
│                                              │
│ • Ваши данные хранятся согласно политике     │
│   студии и платформы — переписка 180 дней,   │
│   ваши записи 3 года для бухгалтерии         │
│ • Удалить ваш аккаунт — в любое время        │
│   (Профиль → Приватность и данные)           │
│ • Если что-то не так с услугами — пишите    │
│   студии напрямую                            │
│                                              │
│ [Полный текст договора (для интересующихся)] │
│                                              │
│ [Понятно]                                    │
└──────────────────────────────────────────────┘
```

### 7.3 «Полный текст договора» from customer side

- Read-only modal with full договор text
- No checkboxes, no «accept» button (customer isn't a party)
- Disclaimer at top: «Это договор между студией и платформой. Вы можете прочитать для информации.»

### 7.4 Customer-pays tier (Phase 3+) preview

When customer-pays tier launches (Phase 3+ vision):
- Separate customer-side договор will exist
- This doc previews the pattern; full design = separate handoff
- Customer договор acceptance flow analogous to §3 tenant flow but customer-side
- Different договор content (customer pays customer-side fee; salon договор separate)

---

## 8. Dispute submission UX (per Q12-ε §6-8)

### 8.1 Per Q12-ε §6: «dispute process (e-mail/dashboard, 48h CSM SLA)»

Hybrid path:
- **Dashboard (primary)** — owner submits via Settings → Договор и оферта → «Создать спор»
- **Email (fallback)** — owner sends to `disputes@{{platform_domain}}` if dashboard unavailable

### 8.2 Dashboard submission flow

```
┌──────────────────────────────────────────────┐
│ ← Создать спор                                │
├──────────────────────────────────────────────┤
│ В чём вопрос?                                │
│                                              │
│ ⦿ Биллинг — не согласен(на) с начислением   │
│ ◯ Возврат — не получил(а) возврат           │
│ ◯ Атрибуция — запись неверно засчитана      │
│ ◯ Данные — не отображаются правильно        │
│ ◯ Другое (опишу ниже)                       │
│                                              │
│ ── Связанные события ──                       │
│                                              │
│ [Поиск по дате / записи / клиенту]           │
│ Найдено: {{N}} событий                       │
│ ☐ {{event_1_summary}}                        │
│ ☐ {{event_2_summary}}                        │
│ ☐ {{event_3_summary}}                        │
│                                              │
│ ── Описание ──                                │
│                                              │
│ Кратко опишите ситуацию (что произошло,      │
│ что ожидалось):                              │
│ [многострочное поле, max 2000 символов]      │
│                                              │
│ ── Ожидаемое разрешение ──                    │
│                                              │
│ [Что вы хотите чтобы было сделано?]          │
│                                              │
│ ── Приложения (опц.) ──                       │
│                                              │
│ [+ Прикрепить файл]                          │
│                                              │
│ ── Сроки ──                                  │
│                                              │
│ Ответим в течение 48 часов.                  │
│ Разрешим в течение 30 дней.                  │
│                                              │
│ [Отмена]            [Подать спор]            │
└──────────────────────────────────────────────┘
```

### 8.3 30-day dispute window per Q12-ε §6

Disputes must be submitted within **30 days** of the disputed event. After 30d: dispute UI shows date-range filter limited to last 30 days; older events return «Срок подачи спора истёк» error per Q12-ε §6.

CSM lead + founder can override 30d limit on case-by-case basis (audit-logged).

### 8.4 Submission confirmation

```
Спор зарегистрирован: #{{dispute_id}}

Мы ответим в течение 48 часов на ваш Mini App + email если настроен.
```

CSM notification fires immediately. SLA timer starts.

### 8.5 Dispute resolution view

After CSM responds:

```
┌──────────────────────────────────────────────┐
│ ← Спор #{{dispute_id}}                        │
├──────────────────────────────────────────────┤
│ Статус: ⏰ В обработке / ✓ Разрешён / ✗ Отказ │
│                                              │
│ Подан: {{date}}                              │
│ Категория: {{category}}                      │
│                                              │
│ ── Ваша версия ──                             │
│ {{owner_description}}                        │
│                                              │
│ ── Ответ CSM ──                               │
│ {{csm_response}}                             │
│ — {{csm_name}}, {{response_date}}            │
│                                              │
│ ── Разрешение ──                              │
│ {{resolution_action}}                        │
│ Например: «Возвращены 100₽ к следующему счёту»│
│                                              │
│ ── Дальнейшие действия ──                    │
│ Если несогласны — эскалация к founder:       │
│ [Эскалировать]                               │
└──────────────────────────────────────────────┘
```

### 8.6 Escalation to founder

Per Q12-ε §6 «final decision: CSM lead + founder for escalation».

Tap «Эскалировать»:
- Original dispute + CSM response + new owner comment captured
- Founder notified via separate channel (per [`ai-quality-observability.md`](./ai-quality-observability.md) founder dashboard)
- SLA: 7 days founder response
- Founder decision = final (договор §8)

### 8.7 Events emitted

- `tenant.dispute.submitted` (NEW — add to event-taxonomy §3.10)
- `tenant.dispute.csm_assigned`
- `tenant.dispute.csm_responded`
- `tenant.dispute.escalated_to_founder`
- `tenant.dispute.resolved` (with `resolution_action`)
- `tenant.dispute.refunded` if applicable (cascades `booking.refunded` per attribution-policy §6)

---

## 9. Anti-patterns (договор-specific)

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Pre-checked «I accept» checkbox | Q12-ε requirement + RU consumer law violation | Explicit unchecked default; tenant must check |
| Single checkbox combining «accept» + «over 18» | Bundled consent; both should be independent | Two separate checkboxes per §3.2 |
| «Accept» button enabled before checkbox | Visual misdirection | Button disabled until both checks |
| Hiding договор text behind «show more» | Burying terms | Full text scrollable in main view + summary on top |
| Auto-acceptance on tenant onboarding completion | No consent | Explicit acceptance step required |
| Pricing changes not requiring re-acceptance | Q12-ε material change rule | All material changes require explicit re-accept |
| No version history visible | Tenant can't verify what they agreed to | Always show history in Settings |
| Dispute submission requires phone call | Friction; legal risk | Dashboard + email both work |
| 48h CSM response timer hidden from tenant | Lack of transparency | Always shown in dispute UI |
| Dispute resolution decision not in writing | Audit gap | Always written response stored in audit |
| Tenant in SUSPENDED cannot file new dispute | Per-state behavior table §6 — can file for past events | Allow for past events; restrict for new |
| Customer sees «accept договор» CTA | Customer not a party | Customer sees informational only |
| Translation of legal text by AI | Liability risk | Legal-authored RU MVP; per-language re-author required |
| Договор display includes customer PII | Privacy violation | Variables only ({{salon_name}}, prices); no customer identifiers |

---

## 10. Data model

### 10.1 `ContractVersion`

Platform-managed catalog of договор versions.

```python
class ContractVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    version = models.CharField(max_length=32, unique=True)
    # SemVer: "1.0", "1.1", "2.0"

    published_at = models.DateTimeField()
    effective_from = models.DateTimeField()
    # Date when version becomes the «current» — could be future for scheduled releases

    full_text_ru = models.TextField()
    summary_ru = models.JSONField(default=list)
    # 7 bullets per §3.2

    UPDATE_TYPE_CHOICES = [
        ('material', 'Material — pricing/refund/retention/dispute changes'),
        ('substantial', 'Substantial — data handling/integrations/SLA'),
        ('clarifying', 'Clarifying — wording without substance change'),
        ('compliance', 'Compliance — legal mandate'),
    ]
    update_type = models.CharField(max_length=32, choices=UPDATE_TYPE_CHOICES)

    # Diff URL or content vs previous version (for «Что изменилось» view)
    diff_summary_ru = models.TextField(blank=True, default='')

    requires_reacceptance = models.BooleanField(default=True)
    # True for material/substantial/compliance; False for clarifying

    reacceptance_deadline_days = models.IntegerField(default=30)
    # 30 days per RU consumer law

    legal_approved_at = models.DateTimeField(null=True, blank=True)
    legal_approved_by = models.CharField(max_length=128, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-published_at']
```

### 10.2 `TenantContract`

Per-tenant acceptance records.

```python
class TenantContract(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='contract_acceptances')

    contract_version = models.ForeignKey(ContractVersion, on_delete=PROTECT, related_name='+')

    accepted_at = models.DateTimeField()
    accepted_by = models.ForeignKey('accounts.User', on_delete=PROTECT, related_name='+')
    # Owner user role; per §4.5 permissions

    ACCEPTANCE_METHOD_CHOICES = [
        ('wizard', 'Onboarding wizard'),
        ('re_acceptance_banner', 'Re-acceptance banner from update'),
        ('csm_override', 'CSM-mediated acceptance (rare)'),
    ]
    acceptance_method = models.CharField(max_length=32, choices=ACCEPTANCE_METHOD_CHOICES)

    age_verified = models.BooleanField()
    authority_verified = models.BooleanField()
    # Two checkboxes per §3.2

    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True, default='')
    # Audit trail per RU consumer law

    superseded_at = models.DateTimeField(null=True, blank=True)
    # Set when tenant accepts newer version

    class Meta:
        constraints = [
            UniqueConstraint(fields=['tenant', 'contract_version'], name='uq_tenant_version_acceptance'),
        ]
        indexes = [
            Index(fields=['tenant', '-accepted_at']),
        ]
```

### 10.3 `ContractDispute`

Dispute records.

```python
class ContractDispute(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='disputes')
    contract = models.ForeignKey(TenantContract, on_delete=PROTECT, related_name='disputes')

    submitted_by = models.ForeignKey('accounts.User', on_delete=PROTECT, related_name='+')

    CATEGORY_CHOICES = [
        ('billing', 'Биллинг — несогласие с начислением'),
        ('refund', 'Возврат — не получен'),
        ('attribution', 'Атрибуция — запись неверно засчитана'),
        ('data', 'Данные — некорректное отображение'),
        ('other', 'Другое'),
    ]
    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES)

    related_events = models.JSONField(default=list)
    # List of event IDs from `apps/events/` and/or `apps/eventbus/`

    description = models.TextField(max_length=2000)
    expected_resolution = models.TextField(max_length=2000)
    attachments_count = models.IntegerField(default=0)
    # Attachments stored separately

    STATUS_CHOICES = [
        ('submitted', 'Подан'),
        ('csm_assigned', 'Назначен CSM'),
        ('csm_responded', 'CSM ответил'),
        ('escalated', 'Эскалирован к founder'),
        ('resolved', 'Разрешён'),
        ('rejected', 'Отклонён'),
        ('lapsed', 'Истёк срок'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='submitted')

    csm_responder = models.ForeignKey('accounts.User', on_delete=SET_NULL, null=True, related_name='+')
    csm_response = models.TextField(blank=True, default='')
    csm_responded_at = models.DateTimeField(null=True, blank=True)

    resolution_action = models.TextField(blank=True, default='')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey('accounts.User', on_delete=SET_NULL, null=True, related_name='+')

    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    refund_event_id = models.CharField(max_length=64, blank=True)
    # If resolution included refund, links to attribution-policy refund event

    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            Index(fields=['tenant', '-submitted_at']),
            Index(fields=['status', '-submitted_at']),  # CSM queue
        ]
```

---

## 11. Localization

### MVP RU

- Договор-оферта (offer-contract) is the RU legal term
- Plain RU language for summary bullets
- Formal RU for full договор text (legal-approved)
- «Принять» (accept) not «согласиться» (agree to) per legal convention
- «Условия» (terms) consistent across UI

### Phase 4+
- Per-language re-author of договор + summary by legal team
- Per-jurisdiction terms variants
- KZ / BY / etc. specific laws

---

## 12. Accessibility (WCAG 2.2 AA)

- Full договор text: keyboard-navigable scrollable region
- Two checkboxes: clear `<label>` association; tab order natural
- Accept button: disabled state announced via `aria-disabled`
- Dispute submission form: per-field labels + validation messages with `role="alert"`
- Diff view: per-line ARIA labels indicating «added», «removed», «unchanged»
- Modal close: ESC key + tap outside + explicit close button
- High contrast on warning banners (≥7:1 for re-acceptance urgent state)

---

## 13. Events emitted summary

Per [`event-taxonomy.md §3.10`](./event-taxonomy.md#310-admin--system-domain) + additions:

| Action | Event | Notes |
|---|---|---|
| Tenant views договор | NEW: `tenant.contract.viewed` | Audit |
| Tenant accepts договор (first time) | NEW: `tenant.contract.accepted` | Required for billing enable |
| New version published | NEW: `tenant.contract.version.published` | Triggers all-tenant notification cascade |
| Notification sent to tenant | NEW: `tenant.contract.notification.sent` | Per-tenant; rate-limited per [`notification-preferences-ux.md`](./notification-preferences-ux.md) |
| Tenant accepts new version | `tenant.contract.accepted` (with `acceptance_method='re_acceptance_banner'`) | Same event reused |
| Tenant lapses (30d no action) | NEW: `tenant.contract.lapsed` | Tenant moves to PRE_SUSPENSION state |
| Tenant submits dispute | NEW: `tenant.dispute.submitted` | CSM SLA timer starts |
| CSM responds to dispute | NEW: `tenant.dispute.csm_responded` | |
| Dispute escalated | NEW: `tenant.dispute.escalated_to_founder` | Founder SLA timer starts |
| Dispute resolved | NEW: `tenant.dispute.resolved` | Final |
| Dispute refunded | `booking.refunded` per [`attribution-policy §6`](./attribution-policy.md) | Cascade |

All add to event-taxonomy.md §3.10.

---

## 14. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-CO1** | Acceptance can be revoked unilaterally by tenant? | NO — once accepted, only superseded by newer version OR договор termination via tenant archival per §6 | Legal | 🔴 before first paying tenant |
| **Q-CO2** | Re-acceptance deadline — 30d per RU law or shorter for clarifying updates? | 30d for material/compliance per RU consumer law; clarifying = no deadline (silent accept) | Legal | 🟡 |
| **Q-CO3** | Tenant in SUSPENDED can file dispute about past events? | YES per §6 table; new disputes for new events blocked | Policy | 🟡 |
| **Q-CO4** | Founder escalation SLA — 7d enough or longer for complex cases? | 7d MVP per Q12-ε §6 «final decision»; tunable per founder schedule | Founder | 🟢 |
| **Q-CO5** | Multi-tenant owner (same person owns several tenants) — separate acceptance per tenant or one acceptance? | Per-tenant separate per Q-CO5 (cross-tenant separation principle); owner accepts per each tenant | Legal + UX | 🟡 |
| **Q-CO6** | Договор PDF generation — backend Python (ReportLab) or templated HTML→PDF (WeasyPrint)? | WeasyPrint MVP — HTML template easier to maintain; revisit at 1000+ PDF/day | Eng | 🟢 |
| **Q-CO7** | Договор text source-of-truth — Markdown file in repo or DB table? | DB table `ContractVersion.full_text_ru` per §10.1 — versioning + per-tenant snapshot at acceptance | Eng | 🟢 |
| **Q-CO8** | Dispute attachments — what file types + size limit? | PDF / JPG / PNG; 10 MB per file; 5 files max per dispute | Eng + Legal | 🟡 |
| **Q-CO9** | Dispute SLA — 48h «respond» vs 30d «resolve» — what's the «respond» minimum? | First acknowledgement within 48h (could be «получили — посмотрим» without resolution); resolution within 30d | Policy | 🟡 |
| **Q-CO10** | Owner change mid-acceptance flow (e.g., started, gave up, came back next day) — preserve progress? | NO — fresh start each time (legal requirement: acceptance must be complete in one session) | Legal | 🟢 |
| **Q-CO11** | Договор displayed in salon-onboarding wizard Phase 1.5 — embed inline OR separate full-screen step? | Full-screen step (gravity of moment; not buried inline) | UX | 🟢 |
| **Q-CO12** | Customer reading договор should see EXACT text tenant accepted, or current platform version? | EXACT text tenant accepted (their relationship is bound by what tenant agreed to) | Legal | 🟡 |
| **Q-CO13** | Tenant who lapses 30d (doesn't re-accept) — auto-terminate or manual CSM? | Auto-pre-suspension at 30d + 60d before SUSPENDED transition; CSM may extend for legitimate reasons | Founder + Legal | 🟡 |
| **Q-CO14** | After dispute resolution — owner can request reconsideration? | NO — escalation to founder is the final step; reconsideration would require new dispute on different ground | Policy | 🟢 |
| **Q-CO15** | Customer-pays tier — customer договор drafted now or when launching Phase 3? | When launching Phase 3 (it's separate scope); this doc only previews structure per §7.4 | PM | 🟢 |
| **Q-CO16** | Договор text changes that are LEGAL-MANDATED (e.g., new law) — can tenant reject? | NO — compliance updates auto-accept after 30d if no rejection; reject = договор termination | Legal | 🔴 before first compliance update |
| **Q-CO17** | Refund triggered by dispute resolution — separate from auto-refund per attribution-policy §6? | Same refund event; `refund_reason='dispute_resolution'` distinguishes | Eng | 🟡 |

---

## 15. Cross-document linkage

- [`attribution-policy.md §13`](./attribution-policy.md#13-draft-договор-оферта-clause-8-mandatory-elements-per-q12-ε) — 8-element договор draft (legal text source)
- [`attribution-policy.md §6`](./attribution-policy.md) — refund rules referenced in §8.7
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) §4 — permissions matrix for §4.5
- [`customer-profile-management-ux.md`](./customer-profile-management-ux.md) §7 — customer-side info modal
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) — per-state behavior table §6
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) — voice for banners
- [`notification-preferences-ux.md`](./notification-preferences-ux.md) — re-acceptance notification class
- [`event-taxonomy.md`](./event-taxonomy.md) §3.10 — 9 NEW events to add
- [`../handoffs/2026-05-17-salon-onboarding-handoff.md`](../handoffs/2026-05-17-salon-onboarding-handoff.md) — Phase 1.5 acceptance step
- [`../handoffs/2026-05-18-settings-hub-handoff.md`](../handoffs/2026-05-18-settings-hub-handoff.md) §18.4 — settings section integration
- [`../briefings/legal-consult-briefing.md`](../briefings/legal-consult-briefing.md) — batch with Q14 + Q-C3 for RU юрист

---

## 16. What this unblocks

- **First commercial billing launch** — legal acceptance recorded per договор
- **Dispute resolution workflow** — CSM has tool + audit trail
- **Compliance updates handling** — re-acceptance protocol locked
- **Tenant trust through transparency** — version history + diff visible
- **Customer-side privacy clarity** — they see what data terms exist
- **Legal defense in disputes** — audit-grade acceptance records (IP, user agent, exact text snapshot)
- **Founder cohort #51+ trust** — договор enforcement consistent across cohorts
- **Auto-billing enablement** per [`attribution-policy.md`](./attribution-policy.md) V2 — договор is upstream prerequisite

## 17. What this does NOT unblock

- ❌ Legal text content (Q-CO16 etc. require RU юрист sign-off)
- ❌ Multi-jurisdiction terms (Phase 5+ international)
- ❌ Customer-pays tier customer-side договор (Phase 3+ separate doc)
- ❌ Договор generation white-label API (Phase 4+ enterprise)
- ❌ Arbitration / court process design (legal scope, not UX)
- ❌ Special enterprise contracts beyond оферта (manual sales scope)
- ❌ Skip RU юрист review on full договор text + per-version updates

---

## 18. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Founder (Q-CO1/13/16 strategic decisions) | ☐ | |
| RU юрист (Q12-ε full договор text + version review protocol + Q-CO2/3/12/16) | ☐ | |
| CSM lead (dispute workflow + SLA Q-CO4/9) | ☐ | |
| Backend (3 models + 9 events + acceptance enforcement at billing gate) | ☐ | |
| Mini App frontend (acceptance wizard + settings display + dispute submission) | ☐ | |
| Legal compliance officer (audit trail per RU consumer law + ФЗ-152 + Q-CO8) | ☐ | |
| Accessibility (WCAG 2.2 AA on acceptance form + diff view + dispute modal) | ☐ | |

## Last verified
2026-05-19 (initial draft, договор-оферта acceptance + display + dispute UX locked — pending Legal sign-off on text content)
