# Tenant as Provider — Model Policy

**Date:** 2026-05-19 r1
**Status:** STRATEGIC FOUNDATION — Doc #4 of 5 in Ayla-first foundation set. Defines salon's scope as service provider in Ayla ecosystem.
**Reads:** [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md), [`ayla-memory-and-personalization.md`](./ayla-memory-and-personalization.md), [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md), memory `project_ayla_first_strategic_pivot`, memory `project_salon_catalog_vertical`, memory `project_pricing_model_hybrid` (r2), [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md), [`contract-offer-acceptance-display-ux.md`](./contract-offer-acceptance-display-ux.md), Notion: PRD Ayla v2.0 (Ayla Pro section)

> Salon is a provider in Ayla's ecosystem. Salon owns its services, masters, schedule, bookings — operational reality. Salon does NOT own the customer, the AI conversation, the wellness data, or the cross-tenant memory. This doc draws the line. Without it, salons would think they have a CRM; they actually have a provider tool.

---

## 0. Why this exists

### 0.1 The strategic frame

Per memory `project_ayla_first_strategic_pivot` (locked 2026-05-19) decision #2:

> AI принадлежит пользователю. Это сильнее стратегически. Если AI принадлежит салону, пользователь каждый раз «начинает заново». Если AI принадлежит пользователю, Ayla становится его личным помощником.

And decision #3:

> Главный бренд — Ayla. В салоне можно: «Ayla помогает подобрать услугу в салоне Формула тела». НЕЛЬЗЯ: «Помощник Формулы тела». Иначе мы теряем большую идею продукта.

These flips invert the salon-CRM mental model. **Salon is provider, not owner of relationship.**

### 0.2 The danger if we don't draw the line

Without explicit scope:
- Salon admin thinks Ayla = «our salon's chatbot» → wants to rename, customize voice, see customer's wellness data «для лучшего обслуживания»
- Salon admin asks for «list of all customers who used Ayla in our salon» — implies customers «belong to salon»
- Marketing campaigns try to scrape Ayla's customer base
- Salon assumes ability to delete customers
- Salon's data export ambitions exceed actual scope
- Pricing conversations become «we own these customers, why pay platform»

Without scope, the entire Ayla-first pivot collapses back into salon-CRM positioning.

### 0.3 The promise

Single source for:
- The mental model — Ayla provider relationships §2
- What tenant owns (data, configuration, tools) §3
- What tenant does NOT own §4
- Ayla Pro admin Mini App scope §5
- Multi-tenant customer relationship §6
- Tenant onboarding into Ayla ecosystem §7
- Tenant offboarding §8
- Tenant data export rights + limits §9
- SUSPENDED state — provider relationship pause §10
- Pricing relationship reflection §11
- Cross-tenant boundary §12
- Anti-patterns §13
- 3 NEW models, 14 endpoints, 10 events

---

## 1. Scope

### IN
- Mental model: salon = service provider, Ayla = customer relationship + AI
- Tenant data scope: services, masters, bookings, earnings, schedule, salon profile, tenant contract
- Tenant tooling scope: Ayla Pro admin Mini App, conflict resolution, dispute review, master management
- Customer-tenant relationship: per-tenant booking history; cross-tenant Ayla memory (handled by Doc #2)
- Tenant onboarding into Ayla ecosystem
- Tenant offboarding (full / partial)
- Tenant SUSPENDED interaction (per `tenant-suspension-pause-ux.md`)
- Tenant data export rights (operational data, NOT customer Ayla memory)
- Per-tenant configuration boundary (what's tenant's call, what's platform-fixed)
- Pricing relationship (pilot model per `project_pricing_model_hybrid` r2)
- 3 NEW models (`TenantOnboardingState`, `TenantOffboardingRequest`, `TenantProviderConfig`)
- 14 endpoints, 10 events

### OUT
- Specific Ayla Pro admin Mini App screens (separate `ayla-pro-admin-mini-app-handoff.md` future — voice-sweep)
- Master Mini App / master operational tools (per `master-mobile-handoff.md`)
- Master earnings / disputes / leave (per per-handoff docs)
- Salon discovery / acquisition (sales scope)
- Salon marketing tools — out of scope MVP
- Multi-location tenant (one salon, multiple physical locations) — Phase 4+
- Franchise model — Phase 4+
- Tenant ↔ tenant data sharing (anti-pattern §13)
- Tenant analytics on customer behavior (limited per §4)
- Salon-to-salon master transfer — Phase 4+
- Industry-specific tenant verticals (only beauty / wellness MVP per `project_salon_catalog_vertical`)
- Tenant's own loyalty / CRM tools integration — Phase 4+
- Tenant's payment processor selection — out of scope (salon's salon-side, per `master-earnings-handoff §7`)

---

## 2. Strategic constraints — non-negotiable

### 2.1 Salon is provider, not owner
- Salon owns: services, masters, schedule, bookings happening in their location, tenant brand, tenant contract
- Salon does NOT own: customers, Ayla memory, AI conversation history, wellness data, customer's preferences across other tenants

### 2.2 Customer's Ayla relationship is one-to-one with user
Per [`ayla-identity-and-brand §11`](./ayla-identity-and-brand.md) + [`ayla-memory-and-personalization §9`](./ayla-memory-and-personalization.md): Ayla is the user's; salons are venues. Customer at multiple salons = one Ayla, multiple provider relationships.

### 2.3 Salon never sees Ayla's chat with customer
Customer's AI conversation = customer + Ayla. Salon admin does NOT see:
- What customer asked Ayla
- Ayla's responses
- Customer's wellness module data
- Customer's preferences across other tenants
- AI memory entries (per Doc #2 §9.3)

Salon admin DOES see:
- Bookings made at their salon (with customer first name + initial per privacy)
- Reviews customer left about their masters
- Disputes customer raised about their services
- No-show pattern at their salon (per `customer-no-show-policy-ux §8` admin-only signal)

### 2.4 Customer's name visibility per tenant
Per [`customer-loyalty-rewards-ux §2.8`](./customer-loyalty-rewards-ux.md) and master-side privacy hierarchy:
- Master sees customer first name + initial only
- Admin sees full name on first booking; subsequent bookings = first name unless admin opens detail view (logged)
- Founder sees full name only when reviewing disputes / sensitive cases

### 2.5 Salon brand subordinate to Ayla brand
Per [`ayla-identity-and-brand §4`](./ayla-identity-and-brand.md):
- ✅ «Ayla помогает в Формуле тела»
- ❌ «Помощник Формулы тела»

Customer's primary brand experience = Ayla. Salon brand surfaces contextually (search results, booking confirmation, master cards).

### 2.6 Tenant cannot customize Ayla
- Persona locked (per Doc #1 §3.1 «подруга-эксперт»)
- Voice locked
- Naming locked («Ayla», not per-tenant variants)
- Memory model locked (one Ayla per user)
- Emergency tiers locked (per Doc #3)

Tenant CAN customize:
- Service catalog (per `project_salon_catalog_vertical`)
- Master profiles
- Schedule rules
- Tenant-side notification SLA (within bounds per Doc #3 §10)
- Refund / no-show / cancellation policy mode (per existing policies)

### 2.7 Tenant cannot pause Ayla per-customer
- Tenant cannot «turn off Ayla for this customer» — Ayla is customer's, not tenant's
- Tenant CAN block customer from booking at tenant (anti-fraud, abuse) — that's per-tenant booking permission, NOT Ayla relationship

### 2.8 Customer cannot be «migrated» between tenants
- Customer's Ayla is one instance globally
- Cross-tenant data isolation per Q-CO5 — what happens at tenant A stays at tenant A booking-wise
- Customer's Ayla memory persists across tenants (per Doc #2 §9)

### 2.9 Tenant cannot bulk-export customers
- Tenant CAN export own operational data: bookings, masters, schedule, earnings audit
- Tenant CANNOT export customer list with PII for external use
- Tenant CANNOT scrape customer wellness data
- Per `project_pricing_model_hybrid`: tenant pays for Ayla relationship to existing customers; cannot circumvent by exporting

### 2.10 Tenant role boundaries
Per existing `master-mobile-handoff §8` + `master-management-handoff` permissions matrix:
- Owner — top-level tenant authority
- Admin — daily operations
- Receptionist — limited operational
- Master — own work only

None of these roles see customer's Ayla memory or AI conversation.

### 2.11 Customer's right to leave tenant
Customer can stop booking at tenant any time. Cancel notifications, opt-out per-tenant notification prefs (per `customer-notification-controls-ux §7` per-tenant prefs). Tenant has NO right to «hold» customer.

### 2.12 Customer's right to leave Ayla entirely
Per [`customer-privacy-data-closure-ux.md`](./customer-privacy-data-closure-ux.md): customer can close Ayla account. Tenant data on customer anonymized per §9.

### 2.13 Tenant's contract is with platform, not customer
Per [`contract-offer-acceptance-display-ux.md`](./contract-offer-acceptance-display-ux.md): tenant signs contract with Ayla platform. Customer has NO contract with tenant via Ayla — booking confirmation is customer's agreement for that service occasion.

### 2.14 Aggregate analytics for tenant
- Tenant sees own salon's anonymized aggregate (e.g., «47 bookings this month, top service X»)
- Tenant does NOT see Ayla-platform-wide cross-tenant aggregates
- Tenant does NOT see customer-individual patterns beyond their salon's bookings

### 2.15 No tenant-to-tenant referrals via platform
- Tenant cannot say to Ayla «recommend customer to tenant B»
- Customer's choice of tenant is customer's
- Ayla may surface multiple tenants in search results per customer query, but tenants don't influence each other's customer base via Ayla

---

## 3. What tenant owns

### 3.1 Tenant operational data

| Category | Tenant ownership | Detail |
|---|---|---|
| **Service catalog** | Full ownership | Service names, prices, durations, masters who perform each, ingredients/allergens, contraindications (per `project_salon_catalog_vertical`) |
| **Master roster** | Full ownership | Hire, fire, manage masters; configure earnings; per `master-mobile-handoff` / `master-earnings-handoff` |
| **Schedule** | Full ownership | Master availability, salon hours, holiday closures |
| **Bookings at tenant** | Full ownership of operational record | Booking time, master, service, customer first name + initial, status, payment |
| **Tenant profile** | Full ownership | Salon name, address, photos, description, brand colors (per tenant-side branding scope) |
| **Tenant contract** | Tenant signs | Subscription tier, terms, founder pricing per `project_pricing_model_hybrid` r2 |
| **Per-tenant policy config** | Tenant configures | Cancellation policy mode, no-show policy mode, tip mode, refund window, deposit settings (within bounds per existing policy docs) |
| **Tenant-side communication** | Full ownership | Email customers from tenant's own systems if they want (subject to customer's notification prefs honored by Ayla; tenant's external comms outside Ayla scope) |

### 3.2 Tenant tooling — Ayla Pro

Per `project_ayla_first_strategic_pivot` decision #5 («архитектурно одна платформа», but two role interfaces):

Tenant gets **Ayla Pro** — admin Mini App (and / or web admin panel) with:
- Booking management
- Master management (per existing master-mgmt handoffs)
- Schedule management
- Catalog management (per `project_salon_catalog_vertical`)
- Dispute resolution (per `customer-refund-dispute-ux`)
- Conflict resolution (per `booking-conflict-resolution-ux`)
- Emergency queue (per Doc #3)
- Pattern flags (no-show admin-only signal per `customer-no-show-policy-ux §8`)
- Tenant analytics (own salon, anonymized aggregate)
- Tenant policy configuration
- Tenant contract / billing view
- Tenant onboarding state (per §7)

### 3.3 Tenant analytics scope

Tenant sees within own salon:
- Booking counts per period
- Top services
- Top masters (per master's own performance)
- No-show rate at own salon
- Refund dispute rate at own salon
- Master earnings analytics (per master-earnings-handoff §11.3)
- Conversion rates (Ayla search → booking) — Phase 3+ if data quality

Tenant does NOT see:
- Customer-individual behavior patterns
- Cross-tenant aggregates
- Wellness data
- AI conversation transcripts
- Other tenants' performance

### 3.4 Tenant brand presence

- Salon name in Ayla search results
- Salon address + map (when surfaced to customer)
- Master photos on master cards
- Tenant logo at booking confirmation
- Tenant promo materials per existing onboarding handoffs (in customer search context)

NOT:
- Persistent customer chrome / app branding (Ayla owns that)
- Customer chat interface customization
- Ayla persona overrides

---

## 4. What tenant does NOT own

### 4.1 Customer relationship

Customer's relationship is with Ayla. Tenant is venue.

- ❌ Tenant cannot «contact our customer» bypassing Ayla
- ❌ Tenant cannot get customer's MAX username, phone, email (customer first name + initial only at booking; full data via dispute escalation per Doc #3 §4.4)
- ❌ Tenant cannot push marketing notifications to customer (customer's marketing toggle per `customer-notification-controls-ux §4` opt-in; tenant can request Ayla to send but customer's pref wins)

### 4.2 AI conversation

Per Doc #1 §5.2 + Doc #2 §9.3 + Doc #3 §13.5:
- ❌ Tenant cannot read customer's chat with Ayla
- ❌ Tenant cannot see customer's questions asked
- ❌ Tenant cannot see Ayla's recommendations to customer
- ❌ Tenant cannot see if customer asked about competitor tenants

### 4.3 Ayla memory

Per Doc #2:
- ❌ Tenant cannot read customer's UserPersonalContext
- ❌ Tenant cannot see customer's preferences across tenants
- ❌ Tenant cannot see 3-zone classified data
- ❌ Tenant cannot edit / delete customer's memory

### 4.4 Wellness data

Per [`core-wellness-profile.md`](./core-wellness-profile.md) + all wellness module handoffs:
- ❌ Tenant cannot read wellness logs (mood, water, body, sleep, symptom, food, AI Avatar)
- ❌ Tenant cannot see wellness observations
- ❌ Tenant cannot see customer's wellness goals
- ❌ Tenant cannot use wellness data for service recommendations to other customers
- ❌ Tenant cannot target marketing based on wellness data

### 4.5 Cross-tenant data

- ❌ Tenant A cannot see customer's bookings at tenant B
- ❌ Tenant A cannot see customer's tier at tenant B (per `customer-loyalty-rewards-ux Q-CL13`)
- ❌ Tenant cannot aggregate customer behavior across tenants
- ❌ Tenant cannot see other tenants' booking volumes / earnings

### 4.6 Customer's identity beyond booking scope

- ❌ Tenant cannot demand customer's full name unless legally required (e.g., medical-adjacent service requires informed consent — handled per onboarding tenant-side, not Ayla scope)
- ❌ Tenant cannot force customer to upload ID
- ❌ Tenant cannot share customer PII with third parties
- ❌ Tenant cannot block customer from registering at Ayla (only from booking at this tenant)

### 4.7 Master-customer direct relationship via Ayla

- ❌ Master cannot DM customer through Ayla (Ayla is customer's; if master needs to communicate, goes through admin → emergency flow per Doc #3)
- ❌ Master cannot see customer's wellness data
- ❌ Master cannot see customer's reviews of OTHER masters
- ❌ Tenant cannot enable master-to-customer direct chat via Ayla platform

### 4.8 Ayla brand or experience customization

- ❌ Tenant cannot rename Ayla
- ❌ Tenant cannot customize Ayla voice / persona
- ❌ Tenant cannot add tenant branding to Ayla chrome
- ❌ Tenant cannot block Ayla from suggesting competitor tenants if relevant to customer

### 4.9 Customer data permanence

- ❌ Tenant cannot delete customer's Ayla memory
- ❌ Tenant cannot delete customer's wellness data
- ❌ Tenant cannot anonymize customer's AI conversation
- ❌ Tenant cannot block customer's account closure (which is customer's right per `customer-privacy-data-closure-ux`)

---

## 5. Ayla Pro admin Mini App scope

### 5.1 Ayla Pro = tenant's tool, not Ayla

Per `project_ayla_first_strategic_pivot` decision #5 («Ayla / Ayla Pro» two roles, one platform):
- Ayla Pro is brand variant for tenant tools
- Logo + chrome may incorporate «Pro» mark
- NOT a different AI persona (no «Ayla Pro» entity speaks to admin)
- Admin sees admin UI, not AI conversation

### 5.2 Top-level Ayla Pro navigation (engineering reference)

(Detailed Ayla Pro Mini App handoff is separate doc — voice-sweep phase. This is scope summary.)

- Dashboard (today's bookings, pending actions)
- Расписание (schedule)
- Мастера (master roster + permissions)
- Услуги (service catalog)
- Записи (bookings list)
- Что требует внимания (emergency queue per Doc #3 §5.1)
- Споры по доходу (master earnings disputes per `master-earnings-handoff §9`)
- Споры клиентов (refund-dispute admin queue per `customer-refund-dispute-ux §5`)
- Лояльность (per-tenant loyalty config + customer view per `customer-loyalty-rewards-ux §13`)
- Аналитика (own salon, anonymized aggregate)
- Настройки (per-tenant policy config)
- Договор / Биллинг (contract + subscription)

### 5.3 What admin sees per customer

When admin opens customer record (only customers who booked at this tenant):

```
┌────────────────────────────────────────┐
│ Customer record (this tenant only)        │
├────────────────────────────────────────┤
│ Имя: Мария И. (initials)                 │
│ Bookings at our salon: 23                │
│ Last booking: 17 May 2026                │
│ No-show count at our salon: 1            │
│ Reviews customer left for our masters:   │
│   3 reviews, avg 4.7                     │
│ Active disputes: 0                       │
│ Loyalty (this tenant): 250 pts, Постоянный│
│ Pattern flags: none                      │
│                                        │
│ ── НЕДОСТУПНО ──                         │
│ • Wellness data (customer-only)          │
│ • AI conversation (customer-only)        │
│ • Cross-tenant booking history           │
│ • Full name / phone / email              │
│   (доступно через legal disclosure       │
│    при споре)                             │
└────────────────────────────────────────┘
```

Admin cannot expand to see customer's full PII without legal trigger (per `customer-refund-dispute-ux §6` admin's review opens customer info ONLY for dispute scope; audit-logged).

### 5.4 Multi-master tenant — admin scope

Owner/admin sees:
- All masters at tenant
- All bookings
- All customer records (initials level)
- All disputes / conflicts / emergencies

Master sees:
- Own bookings only
- Own earnings
- Own reviews (mediated per `master-reviews-feedback-handoff`)
- Own schedule / leave requests
- No-show against own work
- Per master-side handoffs — extensive per-master scope

### 5.5 Tenant analytics dashboard

```
┌────────────────────────────────────────┐
│ 📊 Аналитика — Формула тела              │
├────────────────────────────────────────┤
│ ── За месяц ──                            │
│ Записей: 247                              │
│ Уникальных клиентов: 89                   │
│ Возвратных клиентов: 71 (80%)             │
│ Средний чек: 2 340 ₽                      │
│                                        │
│ ── Топ услуги ──                          │
│ 1. Маникюр — 87 записей                   │
│ 2. Стрижка — 62                           │
│ 3. Окрашивание — 34                       │
│                                        │
│ ── Мастера ──                             │
│ Анна П. — 64 записи, ★ 4.8                │
│ Лена С. — 53 записи, ★ 4.7                │
│ ...                                       │
│                                        │
│ ── Эмердженси ──                          │
│ Споров по доходу: 1 (resolved)            │
│ Конфликтов расписания: 3 (resolved)       │
│ Жалоб клиентов: 1 (escalated to founder) │
│                                        │
│ ⓘ Это данные только по вашему салону.   │
│ Кросс-данных или сравнения с другими     │
│ салонами на платформе нет.                │
└────────────────────────────────────────┘
```

NOT shown:
- Customer's path to discovery («came from which channel»)
- Customer's behavior at other tenants
- Other tenants' performance
- Customer's wellness signals

### 5.6 Per-tenant policy config

Tenant configures within platform-set bounds:
- Refund window (default 14d; min 7d; max 30d)
- No-show policy mode (5 options per `customer-no-show-policy-ux §10`)
- Cancellation policy (per `customer-cancellation-reschedule-spec`)
- Tip mode (external / passthrough / disabled per `master-earnings-handoff §7`)
- Loyalty thresholds (per `customer-loyalty-rewards-ux Q-CL2` — tenant configurable Phase 3+)
- Master compensation profile defaults
- Emergency SLA (per Doc #3 §10.1)
- Master review mediation thresholds (per `master-reviews-feedback-handoff`)

NOT configurable:
- Ayla persona / voice / brand
- 3-zone framework (Doc #2 §2)
- Emergency tiers list (Doc #3 §2.3)
- Customer data export rights
- Cross-tenant boundaries

---

## 6. Multi-tenant customer relationship

### 6.1 Customer at salons A + B

- One BotUser (customer identity)
- One Ayla memory (cross-tenant per Doc #2 §9.1)
- Two booking sets (per-tenant)
- Two loyalty balances (per-tenant per `customer-loyalty-rewards-ux Q-CL13`)
- Two notification preference sets (per-tenant per `customer-notification-controls-ux §7`)
- Two refund-dispute histories (per-tenant)
- Two no-show patterns (per-tenant — invisible cross-tenant per `customer-no-show-policy-ux §2.8`)

### 6.2 Ayla sees customer holistically

Ayla knows customer is at A + B. Uses memory to inform recommendations at either:
- Customer at A: «вечером доступно у Лены»
- Customer at B: «вечером доступно у Ольги»
- Ayla notes preferences across both («предпочитает вечером»)

### 6.3 Salon A sees customer only as their customer

- Salon A's admin sees customer's 23 bookings at salon A
- Salon A's admin does NOT see customer also goes to salon B
- Salon A cannot guess about salon B via Ayla data

### 6.4 Customer at multiple tenants discovery flow

Customer asks Ayla «знаешь хорошего мастера маникюра?»:
- Ayla may surface masters at multiple tenants in results (per customer's location + preferences)
- Customer chooses tenant via booking
- Per-tenant relationship begins at first successful booking

### 6.5 Customer cancels relationship with tenant A only

- Customer can stop booking at tenant A any time
- Customer can opt-out of tenant A notifications per `customer-notification-controls-ux §7`
- Tenant A's data retained for legal period (3 years per consumer law per `customer-privacy-data-closure-ux §9.2`)
- Customer's Ayla memory + tenant B relationship unaffected
- Per `customer-privacy-data-closure-ux §10.2`: customer can per-tenant close — only tenant A data anonymized; tenant B stays

### 6.6 Multi-tenant scenarios via emergency flow

Per Doc #3 §9 — cross-tenant emergencies stay isolated. Founder may aggregate patterns.

---

## 7. Tenant onboarding into Ayla ecosystem

### 7.1 Onboarding sequence (high-level)

Per existing `salon-onboarding-handoff.md` (legacy) — adapt to Ayla-first framing:

1. Salon owner discovers Ayla (sales / referral / marketing)
2. Salon owner signs contract per [`contract-offer-acceptance-display-ux.md`](./contract-offer-acceptance-display-ux.md)
3. Tenant created with status `ONBOARDING`
4. Owner sets up profile (salon name, address, photos)
5. Owner imports / configures service catalog (per `project_salon_catalog_vertical`)
6. Owner invites masters (per `master-onboarding-m0-m7.md`)
7. Masters complete onboarding M0-M7
8. Schedule configured
9. Test bookings (with `booking_source='test_admin'` per `attribution-policy.md`)
10. Pilot phase 0-590₽ subscription (per `project_pricing_model_hybrid` r2)
11. Tenant status `ACTIVE`
12. Customers can find tenant via Ayla discovery

### 7.2 `TenantOnboardingState` model

Tracks per-tenant onboarding progress.

```python
class TenantOnboardingState(models.Model):
    tenant = models.OneToOneField('tenancy.Tenant', on_delete=CASCADE, related_name='onboarding_state')

    STAGE_CHOICES = [
        ('contract_pending', 'Contract awaiting signature'),
        ('contract_signed', 'Contract signed'),
        ('profile_setup', 'Owner setting up profile'),
        ('catalog_setup', 'Importing services'),
        ('masters_inviting', 'Inviting masters'),
        ('masters_onboarding', 'Masters completing M0-M7'),
        ('schedule_configured', 'Schedule set'),
        ('test_phase', 'Test bookings'),
        ('pilot_active', 'Pilot subscription active'),
        ('full_active', 'Fully active'),
        ('paused', 'Onboarding paused by tenant'),
        ('abandoned', 'Onboarding abandoned'),
    ]
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES, default='contract_pending')

    contract_signed_at = models.DateTimeField(null=True, blank=True)
    profile_completed_at = models.DateTimeField(null=True, blank=True)
    catalog_imported_at = models.DateTimeField(null=True, blank=True)
    masters_invited_count = models.IntegerField(default=0)
    masters_active_count = models.IntegerField(default=0)
    schedule_configured_at = models.DateTimeField(null=True, blank=True)
    test_bookings_count = models.IntegerField(default=0)
    pilot_started_at = models.DateTimeField(null=True, blank=True)
    full_active_at = models.DateTimeField(null=True, blank=True)

    primary_owner = models.ForeignKey('auth.User', on_delete=SET_NULL, null=True, related_name='+')
    assigned_csm = models.ForeignKey('auth.User', null=True, blank=True, on_delete=SET_NULL, related_name='+')

    notes = models.TextField(blank=True, default='', max_length=2000)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

### 7.3 Founder-50 pilot cohort tracking

Per `project_pricing_model_hybrid` r2 — first 50 tenants in pilot tracked separately:

```python
class TenantProviderConfig(models.Model):
    tenant = models.OneToOneField('tenancy.Tenant', on_delete=CASCADE, related_name='provider_config')

    PILOT_TIER_CHOICES = [
        ('founder_50_cohort', 'Founder-50 pilot (locked terms)'),
        ('pilot_extended', 'Pilot extended post-50'),
        ('standard', 'Standard (post-pilot)'),
    ]
    pilot_tier = models.CharField(max_length=32, choices=PILOT_TIER_CHOICES, default='founder_50_cohort')

    subscription_amount_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Pilot: 0-590₽
    commission_per_booking_active = models.BooleanField(default=False)
    # Deferred MVP; activate post-pilot
    commission_rate_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    founder_50_locked = models.BooleanField(default=False)
    # If True, current terms cannot be changed without founder approval

    pilot_started_at = models.DateTimeField()
    pilot_ends_at = models.DateTimeField(null=True, blank=True)

    # Tenant policy mode configurations (references to other docs' policy IDs)
    refund_window_days = models.IntegerField(default=14)
    no_show_policy_mode = models.CharField(max_length=32, default='standard')
    tip_mode = models.CharField(max_length=32, default='external')
    emergency_sla_overrides = models.JSONField(default=dict)

    updated_at = models.DateTimeField(auto_now=True)
```

### 7.4 Onboarding voice from Ayla side

Customer doesn't see tenant onboarding directly. Tenant appears in Ayla discovery only when `pilot_active` or `full_active`.

Ayla onboarding (customer-facing) is separate per `customer-first-touch-and-mini-app-states.md`.

### 7.5 CSM involvement

Per `quality-reviewer-dashboard-ux.md`: CSM tracks tenant onboarding for founder-50 cohort. Hands off when `full_active`.

### 7.6 Onboarding analytics

Per memory `project_attribution_extensible_model`:
- Tenant onboarding funnel analytics
- Stage completion rates
- Abandonment patterns
- Time-to-first-booking
- Founder-50 cohort vs standard

Tenant sees own funnel; founder sees aggregate.

---

## 8. Tenant offboarding

### 8.1 Why offboard

- Tenant chooses to leave (cancellation)
- Tenant churns out (subscription lapses)
- Platform terminates tenant (for-cause: contract violation, abuse, anti-fraud)
- Pilot tenant doesn't convert to standard

### 8.2 `TenantOffboardingRequest` model

```python
class TenantOffboardingRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=CASCADE, related_name='offboarding_requests')

    OFFBOARDING_TYPE_CHOICES = [
        ('tenant_voluntary', 'Tenant voluntarily leaves'),
        ('subscription_lapsed', 'Subscription not renewed'),
        ('platform_terminated', 'Platform terminated for-cause'),
        ('pilot_no_conversion', 'Pilot ended without conversion'),
    ]
    offboarding_type = models.CharField(max_length=32, choices=OFFBOARDING_TYPE_CHOICES)

    requested_at = models.DateTimeField(auto_now_add=True)
    cooling_off_ends_at = models.DateTimeField()
    # 30-day cooling-off per master-offboarding precedent
    final_day_at = models.DateTimeField()

    STATUS_CHOICES = [
        ('opened', 'Opened'),
        ('cooling_off', 'In 30d cooling-off'),
        ('founder_review', 'Awaiting founder approval'),
        ('approved', 'Approved'),
        ('cancelled', 'Cancelled (tenant changed mind)'),
        ('executing', 'Data anonymization executing'),
        ('completed', 'Completed'),
        ('legal_hold', 'On legal hold'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='opened')

    requested_by_user = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')
    founder_approver = models.ForeignKey('auth.User', null=True, on_delete=SET_NULL, related_name='+')

    reason = models.TextField(blank=True, default='', max_length=2000)

    # What happens to in-flight data
    affected_bookings_count = models.IntegerField(default=0)
    affected_masters_count = models.IntegerField(default=0)
    affected_customers_count = models.IntegerField(default=0)
    pending_disputes_count = models.IntegerField(default=0)
    pending_emergencies_count = models.IntegerField(default=0)

    customer_notification_strategy = models.CharField(max_length=64, blank=True, default='')
    # 'individual_message' | 'discovery_removal' | 'no_notification'

    class Meta:
        indexes = [
            Index(fields=['tenant', '-requested_at']),
            Index(fields=['status', 'cooling_off_ends_at']),
        ]
```

### 8.3 Customer impact

When tenant offboards:
- Customers with future bookings at tenant — Ayla offers alternatives at other tenants (per `booking-conflict-resolution-ux` master-substitution machinery, adapted for whole-tenant unavailability)
- Customers' loyalty balance at tenant — forfeit (per `customer-loyalty-rewards-ux Q-CL13`)
- Customers' wellness data — unaffected (it's Ayla's)
- Customers' Ayla memory — unaffected
- Customers' bookings at OTHER tenants — unaffected
- Customer notifications — Ayla informs once: «Формула тела больше не доступна на платформе. Могу подобрать альтернативу.»

### 8.4 Master impact at offboarding tenant

Per `master-offboarding-handoff.md` precedent — but tenant-wide. Masters need offboarding from tenant:
- Active masters: their tenant relationship ends
- Earnings final-settlement per `master-earnings-handoff §8` adapted to tenant-wide
- Master can re-onboard at other tenant if they choose (separate flow)

### 8.5 Data retention after tenant offboarding

- Operational data (bookings, earnings audit, disputes): retained per consumer-law minimums (3 years financial, 7 years audit)
- Tenant brand assets: deleted (logo, descriptions)
- Tenant contract: retained per legal
- Customer's per-tenant data anonymized (customer_id → null after 30 days post hard-delete)
- Ayla memory of «customer went to tenant X»: anonymized to «customer went to a salon» (tenant_id → null on memory references)

### 8.6 Cooling-off period

30 days per master-offboarding precedent. Tenant can cancel offboarding within window. Founder approves final.

### 8.7 For-cause termination

Per Q-TP14: founder + co-founder (or 4-eye admin) approve. Audit captures reason. Customer notification stays neutral («больше не работает на платформе»).

### 8.8 Re-onboarding

Tenant can re-onboard later (new contract, new pilot terms per `project_pricing_model_hybrid` r2). Previous data either restored (per cooling-off) or fresh start (post-hard-delete).

---

## 9. Tenant data export rights

### 9.1 What tenant CAN export

Per `customer-privacy-data-closure-ux.md` analog for tenants:

- Own booking records (last N years)
- Own service catalog
- Own master roster + earnings audit
- Own schedule history
- Own salon profile
- Own contract / billing records
- Aggregate analytics (own salon)
- Own dispute / conflict / emergency records (with audit detail)
- Own pattern-flagged customers (initials + reason; not PII bulk)

Format: CSV / JSON / PDF.
Frequency: anytime.
Retention of export: 7-day download window per `customer-privacy-data-closure-ux §4.5`.

### 9.2 What tenant CANNOT export

- Customer full names / phones / emails (PII) — except via dispute escalation
- Customer Ayla memory
- Customer wellness data
- Customer AI conversation
- Customer cross-tenant data
- Other tenants' data

### 9.3 Tenant cannot use data for marketing outside Ayla

Per memory `project_pricing_model_hybrid` r2 — tenant pays for Ayla ecosystem access. Cannot circumvent by exporting customer list to market via SMS / email from own systems unless customer explicitly opted-in tenant's marketing channel (out of Ayla scope — tenant's compliance).

### 9.4 Tenant data export at offboarding

When tenant offboards (per §8.5):
- 30 days to download all exportable data
- After 30 days, retention rules apply (3-year financial / 7-year audit)
- Customer PII not exportable even at offboarding

### 9.5 Audit on tenant export

Per Q-TP9: all tenant exports audit-logged. Pattern detection on unusual export volume (e.g., 10× normal day) flags for founder review (anti-abuse signal).

---

## 10. SUSPENDED state — provider relationship pause

Per [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md):

### 10.1 What SUSPENDED means for provider relationship

- Tenant temporarily cannot perform services (e.g., payment failure, contract dispute, anti-fraud investigation)
- Tenant's data preserved
- Customer-facing impact: tenant disappears from discovery; existing bookings rebooked

### 10.2 Ayla's behavior toward SUSPENDED tenant

- Tenant not surfaced in search results
- Existing future bookings → Ayla offers alt-tenant per booking-conflict §3.6b adapted
- Customer's loyalty at SUSPENDED tenant — frozen, not lost
- Customer's reviews at SUSPENDED tenant — preserved

### 10.3 Per Doc #3 §2.11: emergencies during SUSPENDED

Existing emergencies at SUSPENDED tenant route to founder. New emergencies direct to founder. SLA paused until tenant ACTIVE OR resolution path determined.

### 10.4 SUSPENDED resolution

When SUSPENDED resolves → tenant ACTIVE:
- Tenant resurfaces in discovery
- Existing bookings re-confirmed where possible
- Customers informed via Ayla if booking previously cancelled due to SUSPENDED («Формула тела снова доступна — хочешь записаться?»)

### 10.5 Long SUSPENDED → offboarding

If SUSPENDED > 90 days, automatic offboarding consideration (per `tenant-suspension-pause-ux` policy).

---

## 11. Pricing relationship

Per memory `project_pricing_model_hybrid` r2:

### 11.1 Pilot model (current MVP)

- Subscription 0-590₽/месяц
- Limited functionality
- Commission deferred until value proven
- Founder-50 cohort locked terms

### 11.2 Post-pilot (future)

- Subscription + commission per confirmed booking (rate TBD)
- Founder-50 grandfathered

### 11.3 Customer never sees pricing

Per Doc #2 §2.10 (deprecated reference; pricing memory r2): customer never sees per-booking cost. Tenant is platform's customer; user is Ayla's customer.

### 11.4 Tenant sees own billing

Ayla Pro «Договор / Биллинг» section per §5.2.

### 11.5 Pricing changes trigger contract amendment

Per [`contract-offer-acceptance-display-ux.md`](./contract-offer-acceptance-display-ux.md): material pricing changes require contract amendment + tenant acceptance. Founder-50 protected.

---

## 12. Cross-tenant boundary (Q-CO5)

### 12.1 Strict isolation

Customer's per-tenant data isolated:
- Booking history at A invisible to tenant B
- Loyalty at A separate from loyalty at B
- Reviews at A invisible to tenant B masters
- No-show pattern at A invisible to tenant B admin

### 12.2 Exception: Ayla memory cross-tenant

Per Doc #2 §9: Ayla's memory of customer is cross-tenant (customer-only access). Tenant cannot read it. So this isn't violation of cross-tenant — it's Ayla's data, not tenant's.

### 12.3 Founder aggregate view

Per Doc #3 §6.7: founder sees cross-tenant patterns for safety / quality review. Aggregate only, anonymized. Tenant doesn't have this view.

### 12.4 Q-CO5 memory entry

Per memory `project_attribution_extensible_model`: cross-tenant separation enforced at booking attribution level. Tenant cannot infer customer's other-tenant activity through their own data.

---

## 13. Anti-patterns

### 13.1 Ownership violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Tenant rename Ayla as «помощник Формулы тела» | §2.5 brand subordination | Salon-as-venue framing only |
| Tenant sees customer's chat with Ayla | §4.2 privacy | Tenant tools = own operational data |
| Tenant claims to «own» customer | §2.1 strategic foundation | Provider model |
| Tenant exports customer PII bulk for external marketing | §9.3 | PII access only via dispute escalation |
| Tenant pauses Ayla for specific customer | §2.7 | Tenant can block booking at own salon; not Ayla relationship |
| Tenant blocks customer from registering at Ayla entirely | §4.6 | NEVER — out of tenant scope |

### 13.2 Data scope violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Tenant sees customer's wellness data | §4.4 | NEVER |
| Tenant sees customer's AI memory | §4.3 | NEVER |
| Tenant sees other tenants' performance | §3.3 + §4.5 | Per-tenant analytics only |
| Tenant aggregates customer behavior cross-tenant | §4.5 + Q-CO5 | NEVER |
| Master accesses customer wellness data «for personalization» | §2.10 + §4.7 | NEVER |
| Admin opens customer full PII without dispute trigger | §5.3 audit-logged exception | Audit-gated; reason required |

### 13.3 Configuration violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Tenant customizes Ayla persona | §2.6 brand lock | NEVER |
| Tenant disables Ayla's emergency tiers | §5.6 | NEVER configurable |
| Tenant overrides 3-zone framework | Privacy + safety | NEVER |
| Tenant configures customer-data-export rights | Privacy boundary | Platform-locked |
| Tenant disables customer's right to leave | §2.11 | NEVER |

### 13.4 Onboarding / offboarding violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Tenant onboarding pulls customer list from previous CRM | §4.6 + §9 | Customer relationships start fresh in Ayla |
| Tenant offboarding takes customers with them | §2.8 + §8.3 | Customers stay with Ayla; alt-tenant offered |
| For-cause termination without 4-eye / founder | §8.7 | Founder + co-founder required |
| Re-onboarding restores deleted customer data | Per `customer-privacy-data-closure-ux` | Customer's deletion final |

### 13.5 Multi-tenant violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Tenant sees customer's other-tenant bookings | §12.1 | NEVER |
| Cross-tenant master transfer via platform | Out of scope MVP | Tenant offboards master, master onboards at other |
| Tenant uses Ayla to recommend customer to other tenant | §2.15 | Customer chooses tenants; tenant doesn't refer |
| Customer's data shared between tenants on request | Privacy violation | NEVER |

### 13.6 Pricing violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Customer pays for Ayla | Per memory pricing r2 | Tenant pays platform; customer doesn't pay Ayla |
| Tenant rebates customer to game pricing | Anti-fraud | Audit captures; founder review |
| Founder-50 terms changed without amendment | Per `contract-offer-acceptance-display-ux` | Contract amendment required |

---

## 14. Models — already specified §7, §8

See §7.2 `TenantOnboardingState`, §7.3 `TenantProviderConfig`, §8.2 `TenantOffboardingRequest`.

Total: 3 NEW models.

---

## 15. API contracts

### 15.1 Tenant owner / admin endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/tenant/onboarding-state` | Own onboarding progress |
| GET | `/api/v1/tenant/provider-config` | Own provider config (pilot tier, policy modes) |
| PATCH | `/api/v1/tenant/provider-config` | Update policy modes (within bounds) |
| GET | `/api/v1/tenant/analytics` | Own salon analytics §5.5 |
| POST | `/api/v1/tenant/data-export` | Request export §9 |
| POST | `/api/v1/tenant/offboarding-request` | Initiate offboarding §8 |
| POST | `/api/v1/tenant/offboarding-request/<id>/cancel` | Cancel within cooling-off |

### 15.2 Founder endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/tenants/onboarding-queue` | Cross-tenant onboarding progress |
| GET | `/api/v1/founder/tenants/founder-50-cohort` | Founder-50 cohort tracker |
| POST | `/api/v1/founder/tenants/<id>/approve-offboarding` | Approve tenant offboarding |
| POST | `/api/v1/founder/tenants/<id>/for-cause-terminate` | For-cause termination (4-eye required) |
| GET | `/api/v1/founder/tenants/cross-pattern-flags` | Aggregate pattern detection across tenants |

### 15.3 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/tenants/<id>/anonymize-customer-data` | Run anonymization on offboarding completion |
| POST | `/internal/tenants/onboarding-stage-advance` | Cron — advance onboarding stages |

---

## 16. Events emitted

Add to [`event-taxonomy.md`](./event-taxonomy.md) `3.20 tenant lifecycle domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| Tenant onboarding stage advanced | NEW: `tenant.onboarding.stage_advanced` | from_stage, to_stage |
| Tenant full active | NEW: `tenant.activated` | onboarding_duration_days |
| Tenant SUSPENDED | NEW: `tenant.suspended` | reason |
| Tenant un-suspended | NEW: `tenant.unsuspended` | duration_days |
| Offboarding requested | NEW: `tenant.offboarding_requested` | type |
| Offboarding cooling-off started | NEW: `tenant.offboarding_cooling_off` | ends_at |
| Offboarding approved | NEW: `tenant.offboarding_approved` | |
| Offboarding executing | NEW: `tenant.offboarding_executing` | |
| Offboarding completed | NEW: `tenant.offboarding_completed` | |
| Tenant data exported | NEW: `tenant.data_exported` | scope, size_bytes |

10 NEW events §16.

---

## 17. Acceptance criteria

- [ ] 3 models §7-8 (TenantOnboardingState, TenantProviderConfig, TenantOffboardingRequest)
- [ ] 14 endpoints §15 (7 tenant + 5 founder + 2 internal)
- [ ] Tenant data scope §3 enforced at API level
- [ ] Tenant NEVER sees §4 violations (per-endpoint scope filter, audit log on edge accesses like dispute escalation)
- [ ] Cross-tenant boundary §12 enforced (403 on cross-tenant API attempt)
- [ ] Customer first name + initial only visible to admin at booking record §5.3
- [ ] Customer full PII gated by dispute / legal trigger §5.3 audit-logged
- [ ] Ayla Pro admin Mini App scope §5 wired (separate Mini App handoff covers screens)
- [ ] Per-tenant policy config bounds §5.6 enforced
- [ ] Tenant analytics scope §5.5 — own salon, anonymized
- [ ] Onboarding stage flow §7.1 + state machine
- [ ] Offboarding 30d cooling-off + founder approval §8.6 + 8.7
- [ ] Customer impact on tenant offboarding §8.3 (alt-tenant offered, Ayla memory preserved)
- [ ] Master impact §8.4 (per master-offboarding-handoff precedent adapted)
- [ ] Data retention §8.5 per consumer law
- [ ] Tenant data export §9 — operational only, PII gated
- [ ] SUSPENDED state behavior §10 aligned with `tenant-suspension-pause-ux`
- [ ] Founder-50 cohort tracking §7.3 + §11.1
- [ ] Cross-tenant analytics for founder only §6.6 + Doc #3 §6.7
- [ ] 10 events §16
- [ ] Anti-pattern review §13 (no Ayla rename / no customer PII bulk export / no cross-tenant viewing / no pricing manipulation)
- [ ] Tests: tenant data scope enforcement / cross-tenant 403 / onboarding stage flow / offboarding cooling-off / founder approval / customer alt-tenant flow on tenant offboard / data export scope / SUSPENDED interaction / founder-50 cohort migration

---

## 18. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-TP1** | Founder-50 cohort lock — terms permanent or N years? | Per `project_pricing_model_hybrid` r2 — «locked indefinitely for them» original. Maintain unless founder decision changes. | Founder | 🟢 |
| **Q-TP2** | Tenant data export PII gated — what triggers? | Per §5.3 + §9.2: dispute resolution + legal subpoena + admin explicit «I need to contact this customer for [X]» with audit reason. Otherwise initials only. | Privacy + Policy | 🟢 RESOLVED 2026-05-20 |
| **Q-TP3** | Tenant cancellation cooling-off — 30 days correct? | 30d per master-offboarding precedent. Founder can shorten on case-by-case. | Policy | 🟢 |
| **Q-TP4** | Customer notification on tenant offboarding — when sent? | At founder approval (post cooling-off OR immediate for-cause). Customer gets ONE Ayla message + alt-tenant offer per §8.3. | UX | 🟡 |
| **Q-TP5** | Tenant pause vs offboard — semantic difference? | SUSPENDED = temporary; offboarding = permanent intent. SUSPENDED > 90d may auto-trigger offboarding consideration (per §10.5). | Policy | 🟡 |
| **Q-TP6** | Tenant analytics — Phase 3+ conversion rate visibility? | YES if data quality + privacy preserved. «Ayla search → booking» rate per tenant. Aggregated, no customer-individual. | PM | 🟡 |
| **Q-TP7** | Master-cross-tenant — can master work at multiple tenants on Ayla? | YES per `master-substitution-handoff §2.9` + multi-tenant master pattern. Per master decision. | Policy | 🟢 |
| **Q-TP8** | Tenant policy config bounds — who sets defaults? | Platform sets defaults + bounds (e.g., refund window 7-30d range). Tenant configures within. | Policy + Eng | 🟢 |
| **Q-TP9** | Tenant export rate-limit / volume detection? | YES — unusual volume (10× normal) flags founder review (anti-abuse signal). | Privacy + Founder | 🟡 |
| **Q-TP10** | Re-onboarding period — fresh start after how long? | After hard-delete + customer data anonymized (~37 days post offboarding completion). Re-onboarding then is fresh state. | Eng | 🟢 |
| **Q-TP11** | Tenant for-cause termination — what counts as cause? | Defined per `contract-offer-acceptance-display-ux.md` contract terms. Examples: anti-fraud, customer abuse, repeat policy violations. Founder + 4-eye admin per §8.7. | Policy + Legal | 🟢 RESOLVED 2026-05-20 |
| **Q-TP12** | Customer's per-tenant loyalty balance after tenant offboard — what? | Forfeit per `customer-loyalty-rewards-ux Q-CL13` (customer informed, no compensation). Audit captures. | Policy | 🟡 |
| **Q-TP13** | Tenant marketing of customer outside Ayla — enforcement? | NOT enforced platform-side (tenant's external marketing is outside platform). But customer's `customer-notification-controls-ux §4.3` marketing opt-out applies to Ayla notifications. If tenant marketed customer outside Ayla after customer opted out — that's compliance violation per tenant's own data protection, not Ayla scope. | Legal + Policy | 🟡 |
| **Q-TP14** | For-cause termination 4-eye — admin admin + founder OR admin + admin? | Founder + co-founder (or founder + senior admin). NOT 2 same-role admins (collusion risk). Per master-offboarding §10.2 precedent. | Policy | 🟢 RESOLVED 2026-05-20 |
| **Q-TP15** | Multi-tenant analytics for tenants Phase 4+? | NO. Cross-tenant data is platform-only (founder aggregate). Anti-pattern §13.5. | PM | 🟢 |
| **Q-TP16** | Customer's wellness goal aligned with services at tenant — tenant sees? | NO. Wellness data customer-only. Service recommendation per `customer-wellness-goal-setting-ux §7.4` uses goals SERVER-side; tenant sees only booking made, not goal that drove. | Privacy + Eng | 🟡 |
| **Q-TP17** | Tenant cannot disable Ayla — confirmed? | Confirmed per §2.7. Even on tenant offboard, tenant doesn't «cancel» Ayla for customer; customer's Ayla persists. | Policy | 🟢 |
| **Q-TP18** | Master who quits offboarded tenant + already at other tenant — what? | Per `master-substitution-handoff §2.9` + `master-offboarding-handoff §11`: master can be at other tenants. Tenant A offboards → master's record at A archived; tenant B unchanged. | Policy + Eng | 🟢 |
| **Q-TP19** | Tenant founder-50 lock — affected by ownership change at tenant? | If tenant sold to new owner, contract terms transfer with audit captured. Founder-50 lock preserved if same legal entity. New legal entity = new contract. | Founder + Legal | 🟡 |
| **Q-TP20** | Per-tenant Ayla customization Phase 4+? | NO — Ayla brand locked per Doc #1 §4.3. Phase 4+ may explore co-marketing surfaces (e.g., tenant logo more prominent in tenant's booking confirmations) but never Ayla persona / voice. | Brand + Policy | 🟢 |

---

## 19. Cross-document linkage

### Foundation set
- [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md) — Doc #1 (brand co-presence §4; Ayla owns customer relationship)
- [`ayla-memory-and-personalization.md`](./ayla-memory-and-personalization.md) — Doc #2 (memory cross-tenant; tenant cannot read)
- [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md) — Doc #3 (admin Ayla Pro queue references §5.2)
- **This doc** — Doc #4 (tenant scope)
- `anonymous-to-registered-gate.md` — TO WRITE: Doc #5

### Tenant
- [`tenant-suspension-pause-ux.md`](./tenant-suspension-pause-ux.md) — §10 SUSPENDED interaction
- [`contract-offer-acceptance-display-ux.md`](./contract-offer-acceptance-display-ux.md) — tenant contract, pricing amendments
- `salon-onboarding-handoff.md` (legacy) — to be updated for Ayla-first framing in voice-sweep

### Master-side
- [`master-mobile-handoff.md`](../handoffs/2026-05-18-master-mobile-handoff.md) — Ayla Pro per-master tools
- [`master-onboarding-m0-m7.md`](./master-onboarding-m0-m7.md) — master joins tenant
- [`master-offboarding-handoff.md`](../handoffs/2026-05-19-master-offboarding-handoff.md) — precedent for offboarding patterns
- [`master-substitution-handoff.md`](../handoffs/2026-05-19-master-substitution-handoff.md) — multi-tenant master support
- [`master-earnings-handoff.md`](../handoffs/2026-05-19-master-earnings-handoff.md) — tenant earnings tooling
- [`master-management-handoff.md`](../handoffs/2026-05-18-master-management-handoff.md) — master roster admin

### Customer-side
- [`customer-privacy-data-closure-ux.md`](./customer-privacy-data-closure-ux.md) — customer-side data closure; tenant cannot block
- [`customer-loyalty-rewards-ux.md`](./customer-loyalty-rewards-ux.md) — per-tenant loyalty
- [`customer-notification-controls-ux.md`](./customer-notification-controls-ux.md) — per-tenant notification prefs
- [`customer-no-show-policy-ux.md`](./customer-no-show-policy-ux.md) — per-tenant policy modes
- [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md) — per-tenant disputes
- [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) — per-tenant cancellation policy
- [`booking-conflict-resolution-ux.md`](./booking-conflict-resolution-ux.md) — YClients sync (provider integration)

### Memory
- `project_ayla_first_strategic_pivot` — decisions #1, #2, #3, #5, #8
- `project_salon_catalog_vertical` — tenant service catalog
- `project_pricing_model_hybrid` r2 — pricing relationship
- `project_attribution_extensible_model` — cross-tenant Q-CO5

### Notion
- Ayla — Product Vision (Ayla Pro section)
- PRD Ayla v2.0 (two apps architecture)

---

## 20. What this unblocks

- **Strategic clarity** — salon understands they're provider, not owner
- **Privacy enforcement** — customer's Ayla data not accessible to tenant
- **Multi-tenant customer support** — clean per-tenant boundaries
- **Tenant tooling alignment** — Ayla Pro admin scope defined
- **Tenant onboarding flow** — staged + tracked + founder-50 cohort
- **Tenant offboarding flow** — cooling-off + founder approval + customer alt-tenant
- **Cross-tenant integrity** — Q-CO5 boundary explicit
- **Pricing model framing** — tenant pays platform; customer doesn't pay Ayla
- **Founder governance** — cross-tenant patterns + escalations visible

## 21. What this does NOT unblock

- ❌ Ayla Pro admin Mini App full UI handoff (separate, voice-sweep phase)
- ❌ Multi-location tenants (Phase 4+ franchise)
- ❌ Tenant-to-tenant master transfer flow
- ❌ B2B tier (chain salons) — Phase 4+
- ❌ Multi-currency tenants — Phase 4+
- ❌ Tenant marketing platform integration — Phase 4+
- ✅ Q-TP2 / Q-TP11 / Q-TP14 — resolved 2026-05-20 (founder confirmed provisional); implementation tickets unblocked
- ❌ Customer's per-tenant loyalty refund on tenant offboard (Q-TP12 — currently forfeit)
- ❌ Tenant-side anti-fraud ML

---

## 22. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Privacy / Legal (§4 scope + §9 export + Q-TP2 PII gated + Q-TP11 for-cause + Q-TP14 4-eye composition) | ☐ | 🟢 Q-TP2/11/14 resolved 2026-05-20 |
| Founder (Q-TP1 founder-50 lock + Q-TP4 customer notification timing + Q-TP11 for-cause + Q-TP14 4-eye + Q-TP19 ownership change) | ☐ | 🟢 Q-TP11/14 resolved 2026-05-20 |
| Brand owner (§2.5 brand subordination + Q-TP20 Phase 4+ customization scope) | ☐ | |
| Engineering (tenant data scope filters at API + cross-tenant 403 + onboarding state machine + offboarding cascade) | ☐ | |
| Mini App frontend (Ayla Pro admin scope — high-level; full handoff separate) | ☐ | |
| CSM / Sales (tenant onboarding funnel + founder-50 cohort tracking) | ☐ | |
| Tenant-contract steward (`contract-offer-acceptance-display-ux` alignment) | ☐ | |

## Last verified
2026-05-19 (initial draft, tenant scope as provider + 4 owned categories + 9 not-owned categories + Ayla Pro scope + multi-tenant customer model + onboarding/offboarding flow + cross-tenant boundary + pricing relationship + 3 models, 14 endpoints, 10 events — locked. Foundation Doc #4 of 5 for Ayla-first pivot.)
