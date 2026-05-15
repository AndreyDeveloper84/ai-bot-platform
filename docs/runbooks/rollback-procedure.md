# Runbook: Rollback procedure

> Status: **complete**
> Last exercised: _staging X-rollback drill pending (DRF-872)_
> Owner: Lead
> Last polish: Sprint 10 — adds primary canary rollback path (X-track),
> updates contacts for Telegram-only routing.

## Purpose

Revert production to a known-good state when monitoring catches a
regression. Sprint 10 introduces a **canary rollout** (5% → 25% → 50% →
100%), so the rollback procedure now has **two distinct paths**:

| Path | Use when | Reversal time |
|---|---|---|
| **A: Canary rollback** (primary during X-track) | Canary % is non-zero AND any of the 5 X-criteria breach | ≤ 60 sec |
| **B: Full image rollback** (post-cutover OR non-canary regression) | Bad commit reached prod after a deploy, no canary in flight | ≤ 5 min |

Path A reverts traffic routing **without** touching the platform
deployment — the platform stays up, it just stops receiving live
MAX traffic. Path B redeploys the platform itself.

Most Sprint 10 rollbacks will be Path A — it's the cheap escape hatch
that the canary exists to enable.

## Trigger / when to run

- **X-criteria breach during canary** (Path A): any of intent agreement
  / latency p95 / error rate / Sentry P0 / scope-violation criteria
  from `canary-ramp.md` breaks for > 1 hour
- **Hard alert** (Path A first, then B if Path A insufficient): bot
  down > 2 minutes (`/readyz/` unhealthy or zero successful turns in
  5 minutes)
- **STRICT_TENANT_SCOPE post-flip incident** (Path A, plus `strict-scope-flip.md` §Rollback for env-var revert): any non-zero
  `tenant_scope_violation` audit row in 24h post-flip window (F2 monitor)
- **Sentry P0** (Path A if canary live, Path B if post-cutover): any
  non-recoverable error class with `tags.pipeline_step` set
- **Manual** (Lead judgement): pre-emptive revert before customer-
  impact reports start arriving

## Prerequisites

| Resource | Why | Where |
|---|---|---|
| SSH access to `app.penza.taxi` | nginx config edit + `docker compose` control | 1Password → ops vault |
| `gh` CLI auth on prod box | revert PR + image tag lookup (Path B only) | `gh auth status` |
| `/etc/ai-bot-platform/.env` write access | toggle env vars (Path B + strict-scope rollback) | sudoers list |
| Last-known-good commit SHA (Path B only) | redeploy target | `gh pr list --state merged --limit 5 --base main` |
| `nginx -t` runs clean before reload | catch syntax errors that would 502 the whole site | local check |
| Telegram alerts channel open | confirm post-rollback that pages stop | per `on-call.md` |

---

## Path A — Canary rollback (Sprint 10 X-track)

**This is the primary rollback during the 5% → 100% canary window.**
Single-line nginx edit; the platform process stays running.

### Step A.0 — Declare incident (15 sec)

Post in Telegram admin chat (NOT the alerts channel — that's automation):
```
🚨 ROLLBACK IN PROGRESS — canary
Trigger: <criterion that breached, e.g. "p95 +14% vs mysite for 1h">
Current canary: <5% | 25% | 50%>
ETA: ≤ 60 sec
```

### Step A.1 — Drop canary to 0% (30 sec)

```sh
ssh ops@app.penza.taxi
sudo nano /etc/nginx/sites-enabled/ai-bot-platform
```

Find the `split_clients` directive:
```nginx
split_clients $remote_addr $route_target {
    25% ai-bot-platform;
    * mysite_maxbot;
}
```

Change the percentage to `0%` (or comment out the whole `split_clients`
block if you want zero ambiguity — both behave identically because
`*` catches the rest):
```nginx
split_clients $remote_addr $route_target {
    0% ai-bot-platform;
    * mysite_maxbot;
}
```

Test + reload:
```sh
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -s reload` is graceful — in-flight requests complete on the old
config; new requests use the new config within ~1 second. No connection
drops.

### Step A.2 — Verify traffic drained (15 sec)

```sh
# Wait ~5 seconds for in-flight to drain, then check fresh logs.
sleep 5
sudo tail -50 /var/log/nginx/access.log | grep ai-bot-platform | wc -l
# Expected: 0 (no requests routed to platform in the new window)

# Confirm mysite is taking the traffic:
sudo tail -50 /var/log/nginx/access.log | grep mysite_maxbot | head -5
# Expected: recent timestamps, 200 responses
```

If platform requests > 0 after 30 seconds: nginx didn't pick up the
config. Re-check `nginx -t` output + `systemctl status nginx`.

### Step A.3 — Confirm + war-room (60 sec)

Post in Telegram admin chat:
```
✅ CANARY ROLLED BACK to 0%
Platform processes: still running (idle)
Traffic: 100% mysite_maxbot
Next: investigate <criterion>, fix, re-attempt after staging validation
```

Open the incident-response war-room per `incident-response.md`.
Investigate from logs + Sentry + audit log — the rollback is a
**separator**, not a fix. The actual bug fix happens after the dust
settles, in a separate PR + staging validation cycle.

---

## Path B — Full image rollback (non-canary)

**Use when the platform is already taking 100% traffic (post X-100pct)
AND a regression slipped through.** Path A doesn't apply because
there's no canary % to drop.

### Step B.0 — Declare incident (15 sec)

Post in Telegram admin chat:
```
🚨 ROLLBACK IN PROGRESS — image
Trigger: <hard alert | sentry P0 | sustained X-criteria breach post-cutover>
Last good commit: <SHA>
ETA: ≤ 5 min
```

### Step B.1 — Identify last-known-good image (30 sec)

```sh
ssh ops@app.penza.taxi
cd /home/ops/ai-bot-platform
gh pr list --state merged --base main --limit 10 \
  --json mergedAt,mergeCommit,title
```

Pick the SHA from before the regression went in (typically 1-2 commits
back; check the PR titles + your own context for what just landed).

### Step B.2 — Pin SHA in .env + roll services (90 sec)

```sh
sudo cp /etc/ai-bot-platform/.env /etc/ai-bot-platform/.env.bak.$(date +%Y%m%d-%H%M)
sudo nano /etc/ai-bot-platform/.env
# Change: IMAGE_TAG=<old-SHA>
```

Roll web + worker (database / redis / chromadb stay):
```sh
sudo docker compose --env-file /etc/ai-bot-platform/.env \
  up -d --force-recreate --no-deps web worker
```

Watch boot logs until both healthy (~30 sec):
```sh
sudo docker compose logs -f --tail=50 web worker
# Expect: '/readyz/ status: healthy' within 30s for each service
```

### Step B.3 — Smoke (60 sec)

```sh
curl -fsS http://127.0.0.1:8003/readyz/ | jq .
# Expected: {"status": "healthy", ...}
```

Replay one golden fixture set to confirm the rolled-back state holds:
```sh
sudo docker compose exec web uv run python -m apps.replay run \
  --fixture-set golden --max-count 5
```

If readyz green AND replay diff baseline holds → done. If not → escalate
to Path B + dump-and-restore from latest DB backup (out of scope of
this runbook; see `infra/README.md` § Postgres restore).

### Step B.4 — Confirm + close

Post in Telegram admin chat:
```
✅ IMAGE ROLLBACK COMPLETE
Rolled to: <good SHA>
/readyz/: green
Next: root-cause owner = <name>; bug fix → staging → re-attempt deploy
```

---

## Decision tree — which path?

| Symptom | Likely cause | Path |
|---|---|---|
| Canary criterion breach during X-bump | recent platform code, canary % > 0 | **A** |
| `/readyz/` unhealthy, ChromaDB probe failing | ChromaDB credentials drift, post-cutover | **B** + verify `CHROMA_AUTH_TOKEN` |
| Sentry: `LLMError: ratelimit` spike | OpenAI/Anthropic quota or proxy failure | **Neither** — bump proxy rotation, no rollback needed |
| Sentry: `IntegrityError` on `Message` row | Migration drift between branches | **B** — DO NOT roll DB |
| Canary diff > threshold 2 days | Prompt regression OR catalog sync stale | **A** — pause; investigate; image rollback only if root-cause confirmed |
| `tenant_scope_violation` rows post-F-flip | F-flip surfaced a code path missing `tenant_scope` | **A** + `strict-scope-flip.md` § Rollback (env var revert) |

---

## Verification (both paths)

Within 5 minutes of completing the rollback:

1. `/readyz/` returns `{"status": "healthy"}` on the prod platform host
2. End-to-end smoke from MAX channel — one real test message answered
3. `mysite/maxbot/` MAX webhook log shows traffic flowing (Path A) OR
   platform logs show rolled-back code path (Path B)
4. No new Sentry P0 events in last 2 minutes
5. F2 monitor + alerting channel — no fresh pages
6. Run `apps/replay/runner.py` against `golden/` fixture set — zero
   regressions vs baseline (Path B only; Path A doesn't change the code)

**Time-to-stable target:**
- Path A: all indicators green within 2 minutes of incident declaration
- Path B: all indicators green within 10 minutes of incident declaration

---

## Escalation

Phase 0 has single-human on-call (Lead) — there is no second-line.
See `docs/runbooks/on-call.md` § Escalation for the acknowledged-risk
rationale + Phase 1 trigger conditions.

| Severity | Action |
|---|---|
| P0 (full outage) | Path A first (instant relief), then Path B if needed; debug from audit log + Sentry after stable |
| P1 (one path failing) | Triage from logs first; rollback only if user-visible impact growing |
| Vendor: OpenAI | dashboard.openai.com — check status page first |
| Vendor: Anthropic | console.anthropic.com — check status page first |

When Phase 1 adds a 2nd human on-call, this table gets a real
escalation timer + handoff procedure.

---

## Post-mortem template

Standard 7-bullet template — see [`_template.md`](_template.md).
Specifically capture for rollbacks:

- **What happened.** Trigger + symptom.
- **What was the trigger.** Alert source + signal latency.
- **What did we expect — what actually happened.** X-criterion that
  breached, SLO breach, prior assumption that proved false.
- **How long.** Detect → mitigate (Path A or B) → resolve.
- **What we learned.** Could canary diff have caught it earlier?
  Did this runbook accurately match reality?
- **Action items.** Owner + deadline.
- **Was the staging X-rollback drill (DRF-872) exercised before this
  prod rollback?** If no, that's an action item — every prod rollback
  path MUST first be exercised on staging.

---

## Related runbooks

* [`on-call.md`](on-call.md) — who acks the page that triggers this rollback
* [`canary-ramp.md`](canary-ramp.md) — defines the 5 X-criteria whose breach
  triggers Path A automatically; documents the X-rollback drill (DRF-872)
* [`strict-scope-flip.md`](strict-scope-flip.md) — post-flip violations
  trigger Path A + strict-mode env-var revert
* [`incident-response.md`](incident-response.md) — the war-room procedure
  that runs immediately after the rollback stabilises

---

## Changelog

- 2026-05-10 — Lead — skeleton committed (DRF-414)
- 2026-05-13 — Platform Lead — Sprint 8 / R1 (DRF-727) fleshed out;
  status flipped skeleton → draft
- 2026-05-15 — Lead — Sprint 10 polish:
  added **Path A (canary rollback)** as primary path during X-track;
  retained original image-rollback as Path B for post-cutover regressions;
  Slack/PD references removed (PD canceled per DRF-862, no Slack in stack);
  Telegram-only routing per `on-call.md`; cross-links updated.
  Status: **complete** — operator-ready for X-rollback drill (DRF-872)
  and any unplanned Sprint 10 rollback. Will get a "Last exercised: <date>"
  stamp post-drill.
