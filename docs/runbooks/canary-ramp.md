# Runbook: Canary ramp criteria

> Status: **complete**
> Owner: Lead
> Sprint 10 / X-criteria (DRF-871) — gates DRF-874..877 bumps

## Purpose

Codify **when** the prod canary can move to the next traffic percentage.
"Looks ok" is not an answer. This runbook is the contract: if all metrics
are green for the hold time, **bump**. If any metric breaches for >1h,
**rollback**. No "let me think about it" branch.

This is the gate between **X-5pct → X-25pct → X-50pct → X-100pct** in
Sprint 10 (DRF-874..877).

---

## Trigger / when to run

Before each canary bump (4 times in Sprint 10):

* Bumping 0% → 5% (X-5pct, DRF-874)
* Bumping 5% → 25% (X-25pct, DRF-875)
* Bumping 25% → 50% (X-50pct, DRF-876)
* Bumping 50% → 100% (X-100pct, DRF-877)

Also: during each canary window — continuously check criteria; the rollback
trigger fires automatically when any criterion breaches.

---

## Pre-flight: dev-bot soak (Sprint 10 / DRF-891)

Before any X-bump (5% → 25% → 50% → 100%), the code on `main` MUST have
spent **≥24 consecutive hours on the dev-bot** (`@ai_bot_platform_dev`)
with no fresh Sentry events, no failed `smoke_alert` runs, and no
`tenant_scope_violation` audit rows.

Why: the X-criteria below catch regressions **after** they hit real
users. The dev-bot soak catches them **before**. If the criteria are
the rollback safety net, the dev-bot is the validation gate — they
serve different stages of defense in depth.

How to verify:
* `git log dev..main` should be empty (or only the merge commit) — i.e.
  every commit on `main` is already on `dev`.
* `git log -1 --format=%cI main` ≥ 24h ago (the merge commit's time).
* Dev-bot audit log: `AuditLog.all_tenants.filter(action__startswith="observability.alert", created_at__gte=now-timedelta(hours=24)).count()` is 0 OR all such rows are explainable.
* Manual smoke: at least one operator-issued message to
  `@ai_bot_platform_dev` in the last 24h produced a valid response.

If any of these fail: pause the X-bump; merge fresh code to `dev`
first; re-time the 24h soak from the new merge.

Hotfix exception: a `hotfix`-labeled PR may bypass the 24h soak with
explicit Lead approval, but **still** requires ≥15 min on dev-bot.
The bypass is logged in the PR body as the audit trail.

---

## The five criteria — ALL must be green

Each criterion has a precise measurement. No subjective evaluation.

### Criterion 1: Intent agreement ≥ 95%

**Measurement:** ratio of canary-served turns where the platform's
`IntentDecision.intent` matches the mysite-baseline reply's intent label.

**Source:** `ShadowDeltaSnapshot` (Sprint 8 / DRF-718) — the daily delta
job runs against shadow-mode rows. Once canary is live, the same job
queries canary-served rows + mysite baseline.

**Window:** rolling 24 hours of canary traffic. Minimum 100 turns sampled
(if traffic too low, extend the window to gather 100 samples).

**Why this matters:** if the canary frequently disagrees with mysite on
intent, users are getting wrong skill responses. 5% disagreement budget
covers genuine improvements + edge cases.

### Criterion 2: Latency p95 within +10% of mysite baseline

**Measurement:** `pipeline.turn` span p95 latency (from Sprint 8 / T2 OTel
instrumentation) over the current canary window.

**Baseline:** mysite/maxbot reply time over same window from access logs.

**Calculation:**

```
canary_p95 <= mysite_p95 * 1.10
```

**Window:** rolling 1 hour (latency reacts fast; doesn't need 24h
smoothing).

**Why this matters:** users notice a slower bot. +10% is the noise floor;
beyond it we're degrading UX.

### Criterion 3: Error rate within +0.5 percentage points

**Measurement:** percentage of turns ending in `error` outcome OR
`SafetyVerdict.BLOCK` OR 5xx response.

**Baseline:** mysite/maxbot error rate over same window.

**Calculation:**

```
canary_error_pct <= mysite_error_pct + 0.5
```

(NOT a ratio — additive in percentage points. If mysite is at 1.2%,
canary must stay below 1.7%.)

**Window:** rolling 1 hour.

**Why this matters:** even at 0% mysite baseline, +0.5pp means 1 user in
200 is broken. Higher than that is unacceptable.

### Criterion 4: Sentry P0 count = 0

**Measurement:** Sentry events tagged `severity=critical` (P0) in the
current canary window.

**Threshold:** zero. Not "a few", not "trending down". Zero.

**Window:** rolling 1 hour AND cumulative since canary started.

**Why this matters:** P0 = something definitely broken for a real user.
Cumulative because the user impacted by minute 1 is still impacted now.

### Criterion 5: Zero `tenant_scope_violation` audit rows

**Measurement:** `apps.audit.AuditLog` rows with
`action='tenant_scope_violation'` in the canary window.

**Threshold:** zero. ANY tenant_scope_violation post-flip is a Sev1
security incident (see `security-incident.md`).

**Source:** F2 monitor (Sprint 8 / DRF-731 `monitor_post_flip_violations`)
already pages on this — the canary check is belt-and-suspenders.

**Window:** cumulative since X-5pct started.

---

## Hold times (decision A — compressed, week-22 deadline)

After bumping to a new percentage, the canary must hold steady (all 5
criteria green continuously) for the hold time before the next bump.

| Bump | Hold time | Rationale |
|---|---|---|
| 0% → 5% | **24h** | First real-traffic exposure. Need a full day-night cycle to catch edge cases. |
| 5% → 25% | **24h** | 5× traffic increase. Still need a day to confirm scaling doesn't break anything. |
| 25% → 50% | **48h** | Halfway to full cutover. Two days because mistakes here have biggest blast radius — half users affected. |
| 50% → 100% | **24h** | Last bump. Already at 50% = production scale; 100% is mostly removing mysite as fallback. |

Total minimum canary calendar time: **5 days** (1+1+2+1 days hold + bump
windows).

If decision B (Sprint 9 timing) is picked instead: 2+3+3+0d = 8 days hold
+ bumps. Use decision B if any of the 5 criteria is close to red — buy
more soak time.

---

## Rollback trigger — automatic, not human decision

The criteria are not "fyi" — they're armed switches.

**If ANY criterion breaches for >1h continuously:**

1. The on-call (per `on-call.md`) is paged automatically (PagerDuty
   severity=critical via `apps.observability.alerting.page`)
2. The on-call **rolls back immediately** — not "investigates first"
3. Rollback procedure: per `rollback-procedure.md`. For canary specifically:

```bash
# On prod
ssh prod 'sudo nginx -t && sudo sed -i \
  "s/percentage 5%/percentage 0%/" /etc/nginx/sites-enabled/ai-bot-platform \
  && sudo nginx -s reload'
# Verify within 60s
ssh prod 'sudo tail -50 /var/log/nginx/access.log | grep ai-bot-platform | wc -l'
# Expected: 0
```

4. After rollback: open war-room per `incident-response.md`,
   investigate cause, file fix, re-validate **the criterion that
   tripped**, re-attempt the bump only after fixes verified on
   staging.

**No "wait and see" branch.** If the criterion was broken enough to
declare a threshold, it's broken enough to roll back. Compressed schedule
means we don't have time to nurse a degraded canary.

---

## Rollback authority during canary windows

| Window | Who can pull the trigger |
|---|---|
| Within first 4h of any bump | **Lead** (in war-room) |
| After 4h, monitoring window | **On-call** (per `on-call.md` rotation — Lead in Phase 0; Lead + AI backup in early Phase 1) |
| AI backup observes breach | AI backup pages Lead-direct; AI backup does NOT pull rollback trigger autonomously |

The AI-backup constraint is by design — destructive prod actions are
human-only per `on-call.md`. The breach is paged immediately; humans
decide.

---

## What "green" means in practice

The hourly check during a canary window — operator workflow:

1. Open dashboard (D1 from Sprint 8 / DRF-721; URL: TBD per tenant —
   placeholder `/admin/observability/shadow-delta/`)
2. Read the 5 metrics for the last hour
3. If all 5 green → log "T+Nh green" in war-room
4. If any red → open `incident-response.md` step 4, decide rollback vs
   wait-but-watch (default: rollback if >1h breach)

After the first 4 hours of each bump, hourly check can become every 4h
(unless something looks shaky, then back to hourly).

---

## Verification (post-canary, end of each bump's hold time)

You can call the canary bump **successful** when:

* All 5 criteria green continuously for the hold time
* No rollback triggered
* Linear comment on the X-bump ticket with metrics summary (intent
  agreement %, p95 ms, error %, P0 count, scope-violation count)
* Sign-off from Lead before initiating the next bump

---

## Anti-patterns — don't do these

1. **"Mostly green, let's bump."** If any criterion is on the line, hold
   longer. The compressed schedule (5 days total) already has zero
   buffer — don't burn what we have on guesswork.
2. **Hand-rolling a custom criterion.** "Latency feels fine even though
   p95 is +12%" — no. The criteria are the contract. Add a new
   criterion via PR (and update this runbook), don't override
   in-window.
3. **Investigating before rollback when breach >1h.** Rollback first,
   investigate from the war-room. Same rule as `incident-response.md`.
4. **Skipping the hold time because "we're already running at higher
   internally on staging".** Staging traffic patterns are not prod
   traffic patterns. Hold time exists to surface what staging can't.
5. **Negotiating the criteria thresholds during the canary window.**
   They're contracts with users (latency budget, error budget). Renegotiate
   in a separate PR with explicit Lead approval, not mid-canary.

---

## Related runbooks

* [`incident-response.md`](incident-response.md) — what to do when a
  criterion breaches
* [`rollback-procedure.md`](rollback-procedure.md) — the rollback
  mechanism (single nginx config change)
* [`shadow-mode-launch.md`](shadow-mode-launch.md) — companion runbook
  for the shadow → canary transition
* [`strict-scope-flip.md`](strict-scope-flip.md) — must complete
  successfully BEFORE any X-bump (criterion 5 depends on it)
* [`on-call.md`](on-call.md) — who's watching during canary windows
  (Sprint 10 / O3)

---

## Changelog

* 2026-05-14 — Lead — initial complete version (Sprint 10 / X-criteria / DRF-871)
