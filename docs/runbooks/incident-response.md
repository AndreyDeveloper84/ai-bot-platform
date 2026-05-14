# Runbook: Incident response

> Status: **complete**
> Last exercised: _never_ — first drill scheduled for Sprint 10 / O3 smoke
> Owner: Lead

## Purpose

Define how on-call responds to a production incident — from "monitor fired" to
"post-mortem written" — so the responder doesn't reinvent the process at 02:00.

This runbook is the **dispatcher**. It tells you which specialised runbook to
open next (rollback, strict-scope-flip, replay-debug, security-incident).
Don't try to do everything from this page.

---

## Severity matrix

| Sev | Trigger | Examples | Response SLA |
|---|---|---|---|
| **Sev1** | Bot down OR data leak OR safety violation | `/readyz/` 503 >5min; STRICT scope violation post-flip; PII leaked into outbound | **15 min ack**, war-room within 30 min |
| **Sev2** | Degraded service; subset broken | Single skill failing (e.g. food_scanner Ayla 5xx); latency p95 >2× baseline | **30 min ack**, war-room only if escalates |
| **Sev3** | Cosmetic / advisory | Daily delta digest missed; one tenant's KB stale; non-critical metric drift | **End-of-business-day** triage |

When in doubt, **declare one level higher**. Downgrading mid-incident is cheap;
upgrading is socially awkward and clients suffer.

---

## Trigger / when to run

Any of:

* PagerDuty page with `severity=critical` or `error`
* Telegram alert from `apps.observability.alerting.page(...)`
* Sentry P0 capture
* Manual trigger: team member observes a problem (user complaint, customer
  escalation, support ticket > 4 in 1 hour)

If you're reading this and **none of the above happened**, you're not in an
incident. Use `replay-debugging.md` or `chromadb-auth.md` for routine
investigation.

---

## Prerequisites

* PagerDuty service `ai-bot-platform` — see `on-call.md`
* Telegram admin chat — id in `settings.ADMIN_MAX_CHAT_ID`
* War-room template: Telegram group "ai-bot-platform incident YYYY-MM-DD-HHMM"
  (created fresh per incident; archived after post-mortem)
* Status page: TBD — when first external customer onboards. Until then, direct
  Telegram updates to affected tenant's manager chat.
* On-call rotation per `on-call.md` (Sprint 10 / O3 ships this)

---

## Step-by-step procedure

### 1. Acknowledge

1. PagerDuty: tap **Acknowledge** on the page. This silences the re-page loop and
   logs your name as IC (Incident Commander) of record.
2. Telegram alert thread: react with 👀 so the team knows you saw it.
3. **15 min ack SLA on Sev1 / critical.** Past 15 min unacknowledged →
   PagerDuty re-pages; past 30 min → escalates to Lead-direct.

### 2. Declare severity

Read the symptom against the matrix above. Write the declaration in the
war-room (creating the channel if it doesn't exist yet):

```
SEV: 1
WHAT: prod bot returning 503 to MAX webhook since 03:14 МСК
WHO: <your name> as IC
NEXT UPDATE: 03:45
```

### 3. Appoint roles (Sev1 only)

For Sev1 you need three explicit roles. **One person can hold two**, but never
all three.

* **Incident Commander (IC)** — owns the response. Decides actions, calls
  rollback, declares resolution. Does NOT investigate root cause.
* **Communications Lead (Comms)** — talks to stakeholders (manager Telegram,
  affected tenants, status page). Every 30 min, even if "nothing new".
* **Subject-Matter Engineer (SME)** — investigates + applies mitigation. Reports
  to IC.

For Sev2 the same person can be IC + SME; Comms can be skipped if no external
impact. For Sev3 just write down what you're doing.

### 4. Stabilise — pick the matching specialised runbook

**Don't investigate while users are broken.** First stop bleeding, then debug.

| Symptom | Open this runbook |
|---|---|
| Recent deploy is the suspect | [`rollback-procedure.md`](rollback-procedure.md) |
| STRICT scope violation post-flip | [`strict-scope-flip.md`](strict-scope-flip.md) "Rollback" section |
| Single channel down (MAX webhook 5xx) | [`shadow-mode-launch.md`](shadow-mode-launch.md) "Rollback" — disable nginx mirror |
| ChromaDB unreachable | [`chromadb-auth.md`](chromadb-auth.md) |
| PII / safety violation in outbound | [`security-incident.md`](security-incident.md) **immediately** |
| Pipeline error storm of unknown cause | replay last 100 turns via `apps.replay` CLI — see [`replay-debugging.md`](replay-debugging.md) |

If no specialised runbook matches, the IC's default mitigation is **revert to
previous prod tag**:

```bash
ssh prod 'cd /home/taximeter/ai-bot-platform && git fetch && \
  git reset --hard origin/main~1 && docker compose up -d --force-recreate web worker'
```

### 5. Communicate (Comms only)

Cadence per severity:

| Sev | Internal (war-room) | External (manager / tenant) |
|---|---|---|
| Sev1 | every 30 min | every 30 min after impact-confirmed |
| Sev2 | every 1 h | end-of-incident summary |
| Sev3 | end-of-incident | none |

Template:

```
[YYYY-MM-DD HH:MM] UPDATE
STATUS: investigating | mitigating | monitoring | resolved
WHAT WE KNOW: ...
WHAT WE'RE DOING: ...
NEXT UPDATE: HH:MM
```

"Nothing new since last update" is a valid status. Silence is worse than a
no-news update.

### 6. Resolve

Declare **resolved** when **all** of:

* Primary symptom cleared (e.g. `/readyz/` 200 for ≥15 min)
* No new related alerts in last 15 min
* User-impact endpoint sampled: send a real `/start` to the prod bot and
  receive the canonical welcome reply

IC writes the resolution message in war-room:

```
[YYYY-MM-DD HH:MM] RESOLVED
DURATION: 1h 42m
ROOT CAUSE (preliminary): ...
NEXT: post-mortem scheduled for YYYY-MM-DD (within 5 business days)
```

PagerDuty: tap **Resolve** on the incident.

### 7. Post-mortem

Schedule within **5 business days**. Template lives in [`_template.md`](_template.md).
Mandatory rules:

* **Blameless** — write "the deploy went out without canary" not "X forgot to
  canary". The system enabled the mistake; fix the system.
* Trackable action items go to Linear with explicit owners + deadlines. "We
  should be more careful" is NOT an action item.
* Cross-link the war-room channel transcript (export Telegram chat as text
  attachment) for forensic audit trail.
* Publish in `docs/postmortems/YYYY-MM-DD-<slug>.md` so future on-call can
  search by symptom.

---

## Verification

You can call the incident **handled** when:

* SLO indicators green for ≥15 min after declared resolved
* No fresh user reports in the support channel
* Customer impact statement signed off by IC in war-room
* Post-mortem scheduled in Linear with date + owner

---

## Escalation contacts

Sprint 10 / Phase 1 carry-over — formalise per-service contacts here. Until
then:

* All severities → Lead (only on-call rotation member in Phase 0/early Phase 1)
* AI/Claude backup — read-only diagnostic queries on Lead's direction. NEVER
  destructive actions (no deploy, no rollback, no DB writes). See
  [`on-call.md`](on-call.md) for boundary.

---

## Anti-patterns — don't do these

1. **Investigating before mitigating.** "Let me check why" while bot is down —
   no. Roll back first, investigate from the postmortem.
2. **Silent fixing.** Pushing a hotfix without declaring an incident. Even if
   the fix works, the team had no chance to learn. Always declare; downgrade
   later if appropriate.
3. **Skipping post-mortem because "it was simple".** Most-recurring incidents
   come from "it was simple" causes that never got documented.
4. **Pinging Lead-direct for Sev2 / Sev3.** Use the war-room channel. Lead-direct
   is the Sev1 PagerDuty escalation path.

---

## Related runbooks

* [`on-call.md`](on-call.md) — who's on-call, ack flow, AI-backup role
* [`rollback-procedure.md`](rollback-procedure.md) — revert deploy
* [`strict-scope-flip.md`](strict-scope-flip.md) — STRICT_TENANT_SCOPE rollback
* [`security-incident.md`](security-incident.md) — data leak / 152-ФЗ obligations
* [`shadow-mode-launch.md`](shadow-mode-launch.md) — nginx mirror kill switch
* [`replay-debugging.md`](replay-debugging.md) — post-incident trace inspection
* [`chromadb-auth.md`](chromadb-auth.md) — KB store auth issues

---

## Changelog

* 2026-05-10 — Lead — skeleton committed (DRF-414)
* 2026-05-14 — Lead — full version (Sprint 10 / O5 / DRF-866)
