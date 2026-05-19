# Salon Platform Design Documentation

This folder contains all design documentation for the salon AI-assistant platform — persona policies, attribution/billing rules, ownership tiers, per-feature handoff specs, and meeting briefings.

## Where to find things

### `decisions-log.md` — Canonical decision tracking (start here)

Single source of truth for product/design decisions across the platform. When in doubt about a decision's current status, this file wins over any other doc. Open this first before reading individual handoffs.

### `policies/` — Persistent policy documents

Foundational policies that span all features. Handoffs reference these; updates to a policy ripple through every feature that depends on it.

**Tier 1 — Strategic foundation**
- **product-ux-vision.md** — What we build / what we don't / 5-year evolution / 5 UX shifts (read FIRST before any other design work)
- **core-wellness-profile.md** — 10-layer wellness profile data model (strategic foundation per 2026-05-18 wellness OS pivot). All customer-facing UX flows through this profile

**Tier 2 — Cross-cutting policies**
- **assistant-persona.md** — Voice / tone / vocabulary policy for every customer-facing message (P1–P4 decisions)
- **attribution-policy.md** — Booking attribution model: 5-enum actor types + billable flag + audit metadata (Q12 decisions, refund rules)
- **conversation-ownership-policy.md** — 3-tier ownership (AI_CONTINUITY / HUMAN_SUPERVISED / HUMAN_LOCKED), 4-role permissions matrix, SLA, retention policy (OP1–OP7 decisions)
- **event-taxonomy.md** — Canonical event names + envelope + payload contract across 10 domains (booking / customer / master / schedule / conversation / wellness / campaign / loyalty / billing / admin). Prevents name divergence between parallel implementations
- **core-user-states.md** — 7-state customer taxonomy (DISCOVERED / EXPLORING / PROBLEM_SEEKING / READY_TO_BOOK / POST_VISIT / ACTIVE_REGULAR / AT_RISK_DRIFTING + DORMANT)
- **user-journeys.md** — 3 foundational journey paths (Problem-Seeking / Quick Rebook / AI Reactivation)
- **information-architecture.md** — Mini App IA: 5 surfaces (Главная state-adaptive / Самочувствие / Записи / Услуги / Профиль), bottom nav, state-adaptive home layouts

**Tier 3 — Conversational template trilogy**
- **conversational-ux-framework.md** — Customer-facing message templates: tone per state, journey touchpoints, handoff transitions, failure modes, persona violation guards
- **master-conversational-templates.md** — Master-side message templates: invite, daily schedule digest, customer arrival ping, ScheduleChangeRequest dialog, AI Q&A from master (functional > warm)
- **owner-conversational-templates.md** — Owner-side templates: dashboard insights, settings copy, KPI tooltips, billing notifications, escalation alerts, AI Q&A from owner (partner-tone, never sycophantic)

**Tier 4 — Feature-specific**
- **wellness-input-modules.md** — 7 wellness input modules spec: food scanner / water tracker / body / sleep / mood / AI Avatar / symptom diary; per-module UX, AI inference, privacy boundaries, phasing
- **manual-booking-spec.md** — Owner/admin creates bookings on customer's behalf (phone, walk-in, YClients sync); conversation-thread bootstrap for cold customers; attribution = human_direct / external; Wellness Profile Layer 1 initialization
- **schedule-editor-wireframes.md** — Schedule Management S2/S3 wireframes: owner editor (8 ASCII layouts incl. weekly grid, master selector, hours inline editor, exception/TimeBlock/SlotConfig modals, pending requests inbox) + master mobile (5 layouts incl. day/week view, change-request submit, own-requests inbox, self-sick mark); reusable patterns, edge cases EC-S2-1..7 + EC-S3-1..7, WCAG 2.2 AA
- **customer-cancellation-reschedule-spec.md** — state machine (CONFIRMED → CANCEL_REQUESTED / RESCHEDULE_REQUESTED / AFFECTED_BY_SCHEDULE_CHANGE → terminal), bot DM + Mini App flows for cancel + reschedule, refund integration per attribution-policy §6 (auto −100₽ on cancel<1h + no_show), reschedule cap (3 per booking), master ScheduleChangeRequest cascade with per-customer individual offers, no-show next-morning gentle check, 15 edge cases, anti-abuse mechanics
- **customer-first-touch-and-mini-app-states.md** — 7 entry sources (QR / IG bio / IG post / Maps / direct / referral / web / CRM) with per-source first-touch templates, customer state classification on arrival (new vs returning state resume), 10-state universal Mini App catalog (loading skeleton / success / empty / network error / 5xx / disabled / partial / stale / offline / sync-pending / not found), per-screen state matrix for 4b's 6 customer screens (F1 catalog / F1-detail / F2 masters / F2-detail / F3 slots / F4 confirm / F5 success), loading + empty + error design principles, WCAG 2.2 AA baseline, offline + sync queue patterns
- **master-onboarding-m0-m7.md** — 8-stage lifecycle from invite_sent (M0) → first_week_complete (M7): INVITE_SENT / INVITE_ACCEPTED / PROFILE_SETUP / SCHEDULE_CONFIRM / CUSTOMER_VISIBLE / FIRST_BOOKING_PENDING / FIRST_BOOKING_DONE / FIRST_WEEK_COMPLETE. **AI-first service selection** (templates + regional parsed pricing, NOT manual catalog entry per project_salon_catalog_vertical memory), 3-step wizard (services/photo/bio), schedule defaults confirm, first-booking ramp-up, first-week digest. Re-invite + expiry + multi-tenant scenarios. 6 NEW events for event-taxonomy
- **notification-preferences-ux.md** — 3-axis matrix (audience × channel × event-type) consolidating notification rules across customer / master / owner. 14 event types classified (operational-always-on vs opt-out-able vs opt-in). Per-audience preferences UI (single «без проактивных» toggle for customer per Q-CX9; granular per-event for master/owner). Frequency caps per audience, DND windows, cross-channel fallback (MVP MAX-only, Phase 4+ stub-ready), privacy boundaries, audit + events for every preference change

### `handoffs/` — Per-feature design specifications

Engineering-ready handoff packages. Each contains: scope, JTBD, screens, components, backend contracts, edge cases, anti-slop scan, open questions. Naming convention: `YYYY-MM-DD-<feature>-handoff.md`.

- **2026-05-17-conversations-handoff.md** — Conversations dashboard module (admin side): inbox, detail view, learning queue (C1–C4 screens)
- **2026-05-17-salon-onboarding-handoff.md** — Owner onboarding flow: signup → template/YClients path → Phase 4c masters → Phase 5 launch
- **2026-05-18-analytics-dashboard-handoff.md** — Owner analytics: KPIs, booking source distribution, peak-hours heatmap, master breakdown
- **2026-05-18-customer-first-time-handoff.md** — Customer-facing flows: first-touch greeting, F-screens (services/booking/profile/visits), B-screens (post-visit, reminders, birthday)
- **2026-05-18-loyalty-system-handoff.md** — Points / tiers / referrals (Volna 4): customer-side earning + redemption, owner-side configuration
- **2026-05-18-marketing-campaigns-handoff.md** — Owner campaign composer: tier-targeted promos, persona-conformed messages, opt-out respect
- **2026-05-18-master-management-handoff.md** — Owner-side master CRUD: invite, roles, services-mapping, deactivation, audit (MM1–MM5)
- **2026-05-18-master-mobile-handoff.md** — Master-side MAX Mini App: today/week view, conversations subset (PII-gated), profile editor, change-requests
- **2026-05-18-persona-editor-handoff.md** — Owner UI to tune assistant persona: tone slider, forbidden phrases, explicit-human policy radio
- **2026-05-18-schedule-management-handoff.md** — Owner schedule editor: working hours, slot params, exceptions, block time, master change-request inbox
- **2026-05-18-settings-hub-handoff.md** — (To be moved when in-progress draft completes)

### `briefings/` — Meeting preparation

- **founder-session-briefing.md** — 30-min founder ratification covering Q11 (CSM headcount), Q13 (payment provider), Q12-α/β/δ (attribution edges)
- **legal-consult-briefing.md** — RU юрист consult covering Q14 (договор-оферта), Q-C3 (retention), Q12-ε (refund legal)

### `legacy/` — Pre-existing docs from before this design sprint

Not part of the current platform-design wave. Kept for reference only.

- **2026-05-18-phase-5-architecture-comparison.md** — Architecture options compared for Phase 5 launch
- **2026-05-18-shared-corpus-topic-backlog.md** — Topic backlog for shared knowledge corpus

## How decisions flow

```
                            ┌──────────────────────┐
                            │  decisions-log.md    │  ◄── canonical
                            │  (root, single SoT)  │
                            └──────────┬───────────┘
                                       │
                  ┌────────────────────┼────────────────────┐
                  ▼                    ▼                    ▼
            policies/             handoffs/            briefings/
         (persistent rules)   (per-feature specs)   (meeting prep)
                  │                    │                    │
                  └────────────────────┴────────────────────┘
                                       │
                                       ▼
                                  engineering
                              (apps/*, miniapp/*)
```

When a handoff cites a policy, the policy wins on conflicts. When a policy cites a decision (e.g. P3 multi-language), `decisions-log.md` wins on status. Handoffs are immutable per-revision (r1, r2, …) — superseding revisions get a new entry in the log.

## UX Architect supervisory role

This project has a designated Chief UX Architect (recurring agent across sessions, see `~/.claude/projects/.../memory/project_ux_architect_charter.md`). All UX-related work — own or done by parallel agents in other sessions/windows — passes through the architect's quality gates:

1. **Anti-slop 12-point scan**
2. **WCAG 2.2 AA accessibility baseline**
3. **Persona-conformance** (`policies/assistant-persona.md`)
4. **Single-assistant identity** preservation
5. **Attribution model** coherence
6. **Ownership tier** respect
7. **Wellness OS vector** alignment (`memory/project_wellness_os_vector.md`)
8. **MAX platform** constraints
9. **Naming conventions**
10. **Cross-reference completeness**

**For agents working on UX in other sessions/windows:** before any UX work, read `decisions-log.md` + relevant `policies/*.md` + the architect charter. Save output to `handoffs/YYYY-MM-DD-<feature>-handoff.md`. Report back to architect on completion (top 3-5 open questions, anything surprising). Architect integrates Q-* items into decisions-log after review.

**Conflict detection**: if a new artifact contradicts existing policy/handoff/decision, architect documents in decisions-log under `🔴 Conflict detected (date)` and escalates to founder. Do not silently accept or override.

## Naming convention

- **Handoffs:** `YYYY-MM-DD-<feature>-handoff.md` — date is first-revision date, not last-edit
- **Policies:** `<policy-name>.md` (no date — these evolve in place; revisions tracked inline)
- **Briefings:** `<meeting>-briefing.md`
- **Legacy:** preserved as-named; do not edit
