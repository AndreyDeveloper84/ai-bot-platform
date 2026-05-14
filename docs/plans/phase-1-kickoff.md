# Phase 1 kickoff — handoff from Phase 0

> Status: **draft, awaiting Sprint 10 close**
> Created: 2026-05-14 (Sprint 10 / G-phase1 / DRF-884)
> Owner: Lead
> Activates: post Sprint 10 close-out (DRF-883 Done + freeze lift via DRF-882)

## Purpose

Phase 0 closes with 100% MAX traffic served by `ai-bot-platform` and
`mysite/maxbot/.FROZEN` retired. Phase 1 takes the **functionally
incomplete** platform (real-traffic-ready but missing whole domains
that still live in `mysite/maxbot/`) and finishes the port + hardens
prod infra for multi-tenant onboarding.

This doc is the bridge: what's left, in what order, with what
unanswered decisions. It is **not** a sprint plan — sprint planning
happens Phase 1 day 1 once the open decisions resolve.

---

## Phase 0 → Phase 1 hand-off summary

### What Phase 0 shipped (foundation)

* Multi-tenant Django platform — `apps/tenancy` + `TenantScopedManager` +
  `STRICT_TENANT_SCOPE` enforcement (Sprint 1)
* Channel adapter pattern with MAX adapter live in production (Sprint 2 / 9)
* Skill dispatcher + 11 ported skills covering nutrition, water, food
  scanning, food clarify/correction, cross-domain, health screening
  (Sprint 9)
* KB-RAG infrastructure: per-tenant ChromaDB collections, FAQ skill,
  catalog mirror (services / masters / FAQs / help articles), 15-min
  pull-side sync from mysite + sub-second HMAC push receiver
  (Sprints 7, 10 / C3)
* Observability: OTel tracing, Sentry, replay/shadow-delta diff,
  audit log with tenant scope (Sprints 5, 8)
* Prompt registry, experiments, brand voice config, holdout buckets
  (Sprint 4)
* Canary rollout 5% → 100% on prod (Sprint 10 / X-track)
* 9 runbooks (3 complete, others draft / partial) covering tenant
  onboarding, incident response, security incident, canary ramp,
  rollback, strict-scope flip, shadow-mode launch, replay debugging,
  ChromaDB auth (Sprint 10 / O-track)

### What Phase 0 explicitly did NOT do

These are NOT bugs — they're scope cuts that Phase 1 picks up. Full
inventory in [DRF-836](https://linear.app/drfproject/issue/DRF-836).

| Domain | Status | Lives in |
|---|---|---|
| Booking flow (YClients API + 4 AI tools) | not ported | `mysite/maxbot/ai_tools.py` + `ai_yclients.py` |
| YClients webhook receiver | not ported | `mysite/maxbot/yclients_webhook.py` |
| Booking reminders (T-24h + T-2h + escalation) | not ported | `mysite/maxbot/reminders_factory.py` + `tasks.py` |
| Post-visit follow-up + repeat offer | not ported | `mysite/maxbot/tasks.py::send_post_visit_followups` |
| Telegram channel adapter (production-grade) | dev-test stub only | `apps/channels/` MAX is sole real adapter |
| WhatsApp channel adapter | not started | — |
| Web-widget channel (separate from DRF-241 marketplace) | not started | — |
| Postgres backups + PITR | manual on-deploy | `.github/workflows/deploy.yml` script |
| pgBouncer pooling | not configured | direct connections only |
| Prometheus metrics | absent | Sentry + OTel only |
| Load test infrastructure | absent | — |
| KB embedding model versioning | absent | tied to one model |
| Token / cost ceiling per tenant | absent (DRF-297 dashboard shows $ only) | — |
| KB GLOBAL scope (platform-curated) | absent | `apps/kb/` is tenant-scoped only |

Effective traffic of these gaps:
* **B + R tracks** — bot answers info-questions and runs skill flows on
  prod, but the moment a user says "записаться" (~30% of sessions per
  Phase 0 analytics) we still route them to FAQ + "позову менеджера"
  handoff. Phase 1 closes this.
* **CH track** — only MAX traffic served. Telegram users who find the
  salon's Telegram link get nothing. WhatsApp dittto.
* **PI + K tracks** — platform survives 1 tenant; scaling to 2-5 is
  fine; 10+ needs the PI hardening + K-track GLOBAL scope so new
  salons don't onboard with empty KB.

---

## Sequencing recommendation

5 tracks, ~28 tickets, estimated 2-3 sprints at Sprint 9 / 10 velocity
(~14-16 tickets per 2-week sprint with AI-assisted scaffolding).

```
Sprint 11               Sprint 12               Sprint 13 (optional)
──────────              ──────────              ──────────────────────
B-track ████████████    R-track ████████        PI-track polish ███
  YClients client         BookingReminder         Prometheus
  YClients webhook        send_due_reminders      Load test
  Booking skill           escalate_stale_*        Embedding versioning
  4 booking tools         post_visit_followup     Cost ceiling
  cancel/reschedule       repeat_offer            PII filter
  promo + cert tools
                        CH-track parallel ███████
                          Telegram adapter
                          WhatsApp adapter
                          Web-widget
K-track parallel ████   K-track tail (K4-K5) ██
  K1 scope field          Seed global KB
  K2 admin                Versioning
  K3 search merge

PI-track must-haves ███████
  Postgres backups
  pgBouncer
  OpenAI retry/backoff
```

### Why this order

1. **B-track first** — closes the largest functional gap. Without
   booking, the platform is structurally incomplete; salons can
   describe value but not deliver it through the bot. Everything
   else can be marketed as "coming soon"; missing booking can't.
2. **R-track second, not parallel** — depends on B-track's `Booking`
   model + `external_record_id` field. Splitting these introduces a
   merge dependency on B's data layer.
3. **CH-track parallel with R** — channel adapters are independent
   of booking domain; they're an integration concern + UI surface,
   not business logic. Can run while R lands.
4. **K-track parallel from day 1** — KB scope split is fully
   independent of B/R/CH. K1 (scope field migration) is a 2-hour
   change; K3 (search engine merge) takes longest but is isolated
   to `apps/kb/`.
5. **PI-track split: must-haves with B, polish later** — backups +
   pgBouncer + OpenAI retry are prod-readiness blockers if we
   onboard tenant #2 in Phase 1. Load test + Prometheus + embedding
   versioning + cost ceiling are tenant-count-scaling concerns and
   can ship in Sprint 13 (optional) or get deferred to Phase 2.

### Why NOT alternative orders

* **Parallelise B and R from day 1** — R needs Booking model fields
  that B introduces. Tried this pattern in Sprint 5 (replay + audit
  parallel); lost ~1 day to rebases.
* **CH first, then B** — Telegram traffic wouldn't move the needle
  while booking is the blocker on MAX. Wrong target for first effort.
* **PI first, polish before features** — incentivises a bikeshed
  pattern. The salons we're talking to don't care about Prometheus;
  they care that "записаться" works. Hardening earns its place when
  we have a 2nd tenant signed.

---

## Open decisions (must resolve at Phase 1 day-1 kickoff)

### Decision 1: On-call rotation — single human or 2-person?

| Option | Implication |
|---|---|
| **A: Lead 24/7 + AI backup (current state)** | Cheapest; sustainable for low-volume single-tenant prod. AI backup is read-only diagnostic per `docs/runbooks/on-call.md`. Lead burnout risk if multi-tenant. |
| **B: Add 2nd human to rotation** | Doubles bus factor; needs PagerDuty paid tier; on-call handoff training. Required before tenant #3, optional for tenants 1-2. |

**Recommendation:** A through Sprint 11, re-evaluate before onboarding tenant #2.

### Decision 2: PI-track scope in Phase 1 — must-haves only or full?

| Option | Tickets in Phase 1 | Phase 2 backlog |
|---|---|---|
| **A: Must-haves only** | Backups, pgBouncer, OpenAI retry/backoff, PII filter (4 tickets) | Prometheus, load test, embedding versioning, cost ceiling (5 tickets) |
| **B: Full PI-track** | All 9 tickets | None |

**Recommendation:** A. Defers polish to when we have data on actual
scaling pain. Avoids inventing solutions for hypothetical pain.

### Decision 3: mysite catalog migration timing

* mysite is the catalog source-of-truth today; platform mirrors via
  Sprint 7 pull (DRF-575) + Sprint 10 push (DRF-879).
* Long-term goal: catalog ownership moves to platform; mysite either
  becomes a presentation layer or is fully retired.

| Option | Timeline | Risk |
|---|---|---|
| **A: Keep mysite as source through Phase 1** | mysite stays canonical, platform stays a mirror. Push + pull both live. | Two databases out of sync if push misses + pull lags; tenant edits split between two admins. |
| **B: Phase 1 ends with platform as source** | Catalog admin moves to platform admin UI; mysite becomes a read-only mirror. | ~10-15 day port effort (new admin UI for services/masters/FAQ); blocks freeze-lift unless we do this BEFORE Sprint 11 → unrealistic. |

**Recommendation:** A. Status quo through Phase 1; revisit at Phase 2
kickoff once we have a 2nd tenant where mysite's salon-specific UI
becomes a poor fit.

### Decision 4: Booking carry strategy during Sprint 11

* Sprint 10 leaves users hitting FAQ skill + "позову менеджера"
  handoff on booking intents.
* B-track lands progressively across Sprint 11; not all 7 tickets
  ship together.

| Option | Implication |
|---|---|
| **A: Ship booking skill with `confirm_booking` first; cancel/reschedule/promo/cert as follow-up tickets within Sprint 11** | Earliest user-facing win. Some flows (cancel, promo) still fall back to handoff for a week. |
| **B: Ship full B-track behind a feature flag; flip when all 7 lands** | All-or-nothing UX; cleaner but no early validation. |

**Recommendation:** A. Pattern matches Sprint 9 skill-port — landing
skills progressively, with feature-flag-gated dispatch ON from skill
1. We learned in Sprint 9 that progressive rollout surfaces edge
cases earlier than big-bang.

### Decision 5: Telegram adapter — port from `mysite/notifications/` or write fresh?

* `mysite/notifications/telegram.py` has the proxy-over-OPENAI_PROXY
  pattern (RF block of api.telegram.org) and SiteSettings-based admin
  chat config.
* Platform channel adapter pattern expects `ChannelAdapter` ABC
  (Sprint 2.5 backlog DRF-475) — not yet shipped.

| Option | Implication |
|---|---|
| **A: Port + adapt; ship ChannelAdapter ABC alongside** | Reuses proven proxy handling. Pays the ABC tax on first non-MAX channel — exactly where the abstraction was promised. |
| **B: Write Telegram adapter from scratch on existing MAX adapter as template** | Faster start. Risk: forgets RF proxy handling, breaks deploy. |

**Recommendation:** A. The ABC was deferred from Sprint 2.5 precisely
because we had no second channel to motivate the shape. Phase 1 CH
ticket #1 should be "ship ChannelAdapter ABC + retrofit MAX adapter
to it"; CH #2 is "port Telegram via ABC."

---

## Sprint 11 skeleton (Phase 1 / week 1-2)

**Theme:** Booking domain MVP — close the largest functional gap.

| Track | Tickets | Owner | Notes |
|---|---|---|---|
| B-1 YClients HTTP client port | TBD | Lead | mysite/maxbot/ai_yclients.py → apps/integrations/yclients/ |
| B-2 YClients webhook handler port | TBD | Lead | mysite/maxbot/yclients_webhook.py → apps/integrations/yclients/webhooks/ |
| B-3 Booking model + migration | TBD | Lead | apps/booking/models.py (new app) |
| B-4 Booking skill scaffolding | TBD | Lead | apps/skills/booking/ with feature flag |
| B-5 confirm_booking tool + flow | TBD | Lead | Two-step like mysite; D2 keyboards for slot pick |
| B-6 show_masters + show_slots tools | TBD | Lead | YClients live-fetch + 5-min cache |
| K-1 KB scope field migration | TBD | Lead/parallel | DRF-886 — independent of B-track |
| PI-1 Postgres backups | TBD | Lead/parallel | Daily pg_dump → S3-compatible (Yandex Object Storage) |

**Sprint 11 exit gate:**
* `записаться` user-utterance now routes to booking skill, NOT FAQ handoff
* End-to-end test: user picks master → picks slot → confirms → YClients
  record created → reminder pre-scheduled
* K1 migration ran on prod, no scope-field NULL rows
* Daily Postgres backup confirmed in S3

**Out of scope for Sprint 11:** cancel/reschedule/promo/certificate
tools; full B-track lands in Sprint 11+12 progressively.

---

## Sprint 12 skeleton (Phase 1 / week 3-4)

**Theme:** Booking polish + reminders + channels parallel.

| Track | Tickets | Owner | Notes |
|---|---|---|---|
| B-7 cancel_booking + reschedule_booking | TBD | Lead | Pairs with R-track for reminder cancellation |
| B-8 calc_price + promocode | TBD | Lead | Reads from apps/catalog Promotion mirror (Sprint 10 / C3) |
| B-9 buy_certificate via YooKassa | TBD | Lead | Port mysite/payments/services.py |
| R-1 BookingReminder model + factory | TBD | Lead | Depends on B-3 Booking model |
| R-2 send_due_reminders + escalation | TBD | Lead | Celery beat 15-min cadence |
| R-3 post_visit_followups + repeat_offer | TBD | Lead | Daily 19:00 + Mon 12:00 МСК |
| CH-1 ChannelAdapter ABC + MAX retrofit | TBD | Lead | Sprint 2.5 backlog finally lands |
| CH-2 Telegram adapter | TBD | Lead | Port mysite/notifications/telegram.py |
| K-2 admin UI for global KB | TBD | Lead/parallel | DRF-887 |
| K-3 search engine merges global + tenant | TBD | Lead/parallel | DRF-888 |
| PI-2 pgBouncer | TBD | Lead/parallel | Connection pooling |
| PI-3 OpenAI retry/backoff | TBD | Lead/parallel | ayla-ai-core gap |

**Sprint 12 exit gate:**
* Booking flow complete end-to-end including cancel + reschedule
* Reminders fire on prod for at least 1 real booking
* Telegram channel adapter accepts test webhook
* KB merge global + tenant returns ranked results

---

## Sprint 13 skeleton (Phase 1 / week 5-6, optional / contingency)

**Theme:** Polish + scaling readiness for tenant #2.

| Track | Tickets | Owner | Notes |
|---|---|---|---|
| CH-3 WhatsApp adapter | TBD | Lead | Optional based on tenant pipeline |
| CH-4 Web-widget channel | TBD | Lead | Embeddable script for salon websites |
| K-4 Seed initial global KB | TBD | Lead | ~50-100 chunks: procedures, contraindications, post-care |
| K-5 KB versioning + rollback | TBD | Lead | Bad global edit must be revertable |
| PI-4 PII filter in logs | TBD | Lead | Defense in depth over Sentry scrubber |
| PI-5 Prometheus + dashboards | TBD | Lead | Operational dashboards beyond Sentry |
| PI-6 Embedding model versioning | TBD | Lead | Tied-to-one-model fragility |
| PI-7 Token cost ceiling per tenant | TBD | Lead | Hard cap to prevent runaway spend |
| PI-8 Load test infrastructure | TBD | Lead | Locust / k6 against staging |

**Sprint 13 is dropped if:**
* Tenant #2 conversations haven't started
* Sprint 11+12 ran hot and need a stabilisation buffer

**Phase 1 exit gate (regardless of which sprints fire):**
* Booking domain on prod
* Reminders firing on prod
* Telegram channel adapter live
* KB GLOBAL scope live with seed data
* Postgres backups + pgBouncer + OpenAI retry deployed
* Two-tenant smoke test passes (even if synthetic tenant)

---

## References

* Phase 1 backlog epic: [DRF-836](https://linear.app/drfproject/issue/DRF-836)
* Phase 1 K-track epic comment + 5 tickets DRF-886..890
* Phase 0 close (target): [DRF-883](https://linear.app/drfproject/issue/DRF-883)
* Phase 0 retro (target): `docs/plans/phase-0-retro.md` (TBD, ships with G-rollup)
* Sprint 10 plan: [`docs/plans/sprint-10-canary-cutover.md`](sprint-10-canary-cutover.md)
* Sprint 9 skill-port reference pattern: [`docs/plans/sprint-9-skill-port.md`](sprint-9-skill-port.md)
* `mysite/maxbot/.FROZEN` — retires when X-100pct stable ≥24h
  ([DRF-882](https://linear.app/drfproject/issue/DRF-882))

---

## Changelog

* 2026-05-14 — Lead — initial draft (Sprint 10 / G-phase1 / DRF-884)
