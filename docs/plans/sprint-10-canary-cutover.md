# Plan — Sprint 10: Canary cutover 5% → 100% MAX (week 21-22)

> Theme: complete Phase 0 by routing real MAX traffic onto
> `ai-bot-platform` and lifting the `mysite/maxbot/.FROZEN` policy.
> Most of this content was scoped for Sprint 9 (cancelled 2026-05-14)
> and now resurrects under Sprint 10 after Sprint 9's skill-port pivot.
> Baseline: `main @ <Sprint-9 close SHA>` (TBD at Sprint 10 kickoff).
> Linear epic: **TBD** — `[Sprint 10] Canary cutover (week 21-22)`.

## Context

After Sprint 9 the platform owns 7 nutrition / health skills + the
foundation (tenancy, observability, KB-RAG, skills framework, Ayla
integration). It now matches `mysite/maxbot/` feature-wise for the
food / water / pain consultation domain. Sprint 10 takes the last
step: route real MAX traffic onto the new platform, lift freeze, and
close Phase 0.

The original Sprint 9 plan (`sprint-9-canary-cutover.md`, cancelled
2026-05-14) had the canary ramp + STRICT flip + on-call setup. Most
of that content lifts verbatim; this plan re-scopes for the
**compressed** week 21-22 timeline (freeze deadline moved from week
21-22 to week 22 per CLAUDE.md update).

## Scope — what we ship

### Operational gating

- **F1 STRICT flip** — `STRICT_TENANT_SCOPE=strict` in prod env. F2
  monitor (Sprint 8 / DRF-731) auto-arms via `STRICT_SCOPE_FLIP_AT`.
- **PagerDuty integration** — Sprint 9 plan called it Q3 / A1; lifts
  to Sprint 10 since canary cannot run without on-call escalation.
- **Runbook finalization** — `tenant-onboarding.md`,
  `incident-response.md` (full), `security-incident.md` (full),
  `on-call.md` (new). All required gates before X-5pct.

### Canary ramp

- **X-5pct** — 5% of inbound MAX webhooks routed to `ai-bot-platform`.
  Duration 1 day (compressed from Sprint 9 plan's 2 days because the
  week 22 deadline is hard).
- **X-25pct** — 25% on day 2-3. Same X-criteria gate as Sprint 9 plan.
- **X-50pct** — 50% on day 4-5.
- **X-100pct** — 100% on day 6. Hard cutover; mysite/maxbot drains
  (still accepts webhooks but bot replies via platform).
- **Freeze lift** — `mysite/maxbot/.FROZEN` policy retires once
  X-100pct is stable for 24h.

### Mysite carry-overs (parallel agent)

- **C-track integration** — Sprint 9 cancelled the C-track (catalog
  consumer for mysite M1/M2/M3 webhooks). Sprint 10 picks it back up:
  * `apps/integrations/mysite/catalog_client.py` (renamed from
    Sprint 9's cancelled C1 — was `apps/catalog/client.py`)
  * Daily Celery sync (C2 cancelled)
  * HMAC webhook receiver (C3 cancelled)
  * Tenant-scoped catalog tables (C4 cancelled)
  * E2E tests (C5 cancelled)

  Whether all 5 ship in Sprint 10 or split with Phase 1 depends on
  M-track timing — TBD at sprint kickoff.

### Phase 1 hand-off prep

- Sprint 10 plans the **next quarter** boundary. The Phase 1 backlog
  (DRF-836..860) is already populated; Sprint 10 / G2 will write a
  formal "what's next" doc that the Phase 1 sprint can lift.

## What's NOT in Sprint 10

Deliberately deferred to Phase 1:

* Booking domain (DRF-837..843) — 7 tickets, ~14 working days
* Reminders + follow-up (DRF-844..847) — 4 tickets, ~4 working days
* Channels: Telegram / WhatsApp / Web widget (DRF-848..850)
* Prod infra hardening: Postgres backups / pgBouncer / load test /
  PII filter / cost cap (DRF-851..860)

Phase 0 closes WITHOUT these. The platform serves Формула тела on MAX
+ runs nutrition skills against Ayla. Booking still routes to the
mysite handler via legacy webhook — that gets ported in Phase 1.

## Decisions (TBD at kickoff)

| Decision | Options | Notes |
|---|---|---|
| Canary timing | A: 5%×1d → 25%×2d → 50%×1d → 100% (5 days) | Compressed for week-22 deadline |
| | B: 5%×2d → 25%×3d → 50%×3d → 100% (9 days) | Sprint 9 plan timing — needs week-23 buffer |
| Booking carry | A: Keep on mysite via webhook bridge | Cleanest split |
| | B: Stub `apps/skills/booking` with mysite redirect | Forces booking-skill scaffold |
| C-track scope | A: All 5 in Sprint 10 (sync + webhook + tests) | ~5 days |
| | B: Client + tables only (defer sync + webhook) | ~2 days, sync runs in Phase 1 |

Decision deadline: Sprint 10 day 1 kickoff (the day after Sprint 9 close).

## Exit gate (all required)

* **X-100pct active** — 100% of inbound MAX webhooks served by
  `ai-bot-platform` for ≥24h
* **`/readyz/` green** ≥23 of 24 hours
* **Sentry P0 = 0**
* **Zero `tenant_scope_violation`** in post-flip audit window
* **`mysite/maxbot/.FROZEN` retired**
* **Sprint 10 close-out comment** on epic + memory update

## Risks

1. **Compressed canary timing** (decision A) leaves zero buffer if any
   bump fails. *Mitigation:* X-rollback drill before each bump; if a
   bump fails, fall back to Sprint 9 plan timing (decision B) and
   shift freeze-lift to week 23.
2. **C-track delay** — parallel agent's M-track must land before our
   C-track e2e tests pass. *Mitigation:* C-track unit work uses the
   mock-mysite stub; e2e is the only blocker.
3. **First-time PagerDuty** — admin friction can eat a day. *Mitigation:*
   Sprint 10 day 1 task; if it slips, Telegram-only stays viable for
   the 5%/25% steps and PD lands by 50%.
4. **Booking domain unresolved** — users may try `записаться` during
   the canary window. *Mitigation:* the FAQ skill catches "запись" /
   "забронировать" / etc. with a "позову менеджера" hand-off until
   booking skill lands in Phase 1.

## References

* `docs/plans/sprint-9-skill-port.md` — Sprint 9 plan that preceded this
* `docs/plans/sprint-9-internal-smoke.md` — manual smoke scenarios
* `tests/e2e/test_ayla_integration.py` + `docs/qa/ayla-e2e-setup.md`
  — nightly schedule wiring
* CLAUDE.md migration freeze policy — week 22 deadline
