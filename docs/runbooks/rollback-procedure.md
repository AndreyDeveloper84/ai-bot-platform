# Runbook: Rollback procedure

> Status: **draft**
> Last exercised: _staging dry-run pending Sprint 8 / N4_
> Target completion sprint: **Sprint 8** — Sprint 9 canary will exercise
> this for real and graduate to **complete** after one clean game-day.
> Owner: Platform Lead

## Purpose

Revert production to a known-good state when a release introduces a
regression that monitoring or canary diff catches. The default move
during Sprint 8–10 is **"roll all MAX traffic back to `mysite/maxbot/`"**
because that stack remains the parallel production reference until 100%
cutover (Sprint 10).

## Trigger / when to run

- **Canary diff alert (Sprint 9)**: platform reply disagrees with mysite
  beyond the agreement threshold (`intent_agreement < 95%` for ≥ 2
  consecutive ShadowDeltaSnapshot rows).
- **Hard alert**: complete bot down for > 2 minutes (`/readyz/`
  unhealthy or zero successful turns in the last 5 minutes).
- **Sentry P0**: any non-recoverable error type in production with
  `tags.pipeline_step` set.
- **STRICT_TENANT_SCOPE post-flip incident**: any non-zero
  `tenant_scope_violation` audit row in the 24h post-flip window
  (F2 / DRF-731 monitor).
- **Manual**: pre-emptive revert before customer-impact reports start
  arriving (oncall judgement call).

## Prerequisites

| Resource | Why | Where |
|---|---|---|
| SSH access to `app.penza.taxi` | nginx upstream switch + `docker compose` control | 1Password → ops vault |
| `gh` CLI auth on prod box | revert PR + image tag lookup | `gh auth status` |
| `/etc/ai-bot-platform/.env` write access | toggle `STRICT_TENANT_SCOPE` back to `audit` if F1 flip caused regression | sudoers list |
| Last-known-good commit SHA | redeploy target — pulled from `gh pr list --state merged --limit 5 --base main` | `gh` CLI |
| `nginx -t` runs clean before reload | catch syntax errors that would 502 the whole site | local check |
| Comms channel ready | Slack `#ai-bot-ops`, MAX admin chat | bookmark |

## Step-by-step procedure

### Step 0 — Declare incident (15 sec)

Post in `#ai-bot-ops`:
```
🚨 ROLLBACK IN PROGRESS
Trigger: <canary diff | hard alert | sentry P0 | strict-scope | manual>
Last good commit: <SHA>
ETA: ≤ 5 min
```

### Step 1 — Stop new traffic to the platform (60 sec)

Edge nginx routes MAX webhook to either `mysite/maxbot/` (legacy) or
`ai-bot-platform/` (platform). Rollback = route 100% back to mysite.

```sh
ssh ops@app.penza.taxi
sudo nano /etc/nginx/sites-enabled/maxbot-tee.conf
```

Replace the `proxy_pass` for `location = /api/maxbot/webhook/` with the
mysite upstream and **comment out `mirror /shadow;`** (so platform
stops receiving even the shadow copy — pure observation pause).

Test + reload:
```sh
sudo nginx -t && sudo systemctl reload nginx
```

Verify: `curl -s -o /dev/null -w "%{http_code}" https://app.penza.taxi/api/maxbot/webhook/ -X POST -H "X-Max-Bot-Api-Secret: $TOKEN" --data '{}'`
should return 200 and the log line should originate from mysite.

### Step 2 — Roll the platform image back (90 sec)

Identify the last-known-good image tag:
```sh
cd /home/ops/ai-bot-platform
gh pr list --state merged --base main --limit 10 --json mergedAt,mergeCommit,title
```

Pick the SHA from before the regression went in (typically 1–2 commits
back). Pin that SHA in `.env`:
```sh
sudo nano /etc/ai-bot-platform/.env
# Change: IMAGE_TAG=<old-SHA>
```

Roll the web + worker services (chromadb / redis / postgres stay):
```sh
sudo docker compose --env-file /etc/ai-bot-platform/.env \
  up -d --force-recreate --no-deps web worker
```

Watch the boot logs until both come up healthy (~30 sec):
```sh
sudo docker compose logs -f --tail=50 web worker
```

### Step 3 — Smoke + re-enable platform mirror (90 sec)

End-to-end smoke against the rolled-back platform:
```sh
curl -fsS http://127.0.0.1:8003/readyz/ | jq .
# expected: {"status": "healthy", ...}
```

Replay one golden fixture to confirm no regression in the rolled-back
state:
```sh
sudo docker compose exec web uv run python -m apps.replay run \
  --fixture-set golden --max-count 5
```

If readyz green AND replay diff baseline holds, re-enable the shadow
mirror by uncommenting `mirror /shadow;` in the nginx config and
reloading:
```sh
sudo nano /etc/nginx/sites-enabled/maxbot-tee.conf
# Uncomment: mirror /shadow;
sudo nginx -t && sudo systemctl reload nginx
```

### Step 4 — Confirm + close incident

In `#ai-bot-ops`:
```
✅ ROLLBACK COMPLETE
Rolled to: <good SHA>
Platform back in shadow mode @ <timestamp>
Next steps: root-cause owner = <name>
```

## Decision tree

| Symptom | Likely cause | Action |
|---|---|---|
| `/readyz/` returns `unhealthy`, ChromaDB probe failing | ChromaDB credentials drift OR collector restart | Step 2 + verify `CHROMA_AUTH_TOKEN` in env |
| Sentry: `LLMError: ratelimit` spike | OpenAI / Anthropic quota or proxy failure | Skip Step 1, just bump `OPENAI_PROXY` rotation, no image rollback needed |
| Sentry: `IntegrityError` on `Message` row | Migration drift between branches | Step 2 (roll image) — DO NOT roll DB |
| Canary diff > threshold for 2 days | New prompt regression OR catalog sync stale | Step 1 (route off platform) — investigate before re-enable; image rollback not required |
| `tenant_scope_violation` audit rows post-F1 flip | Sprint 8 IM-2 strict-scope flip surfaced a hole | Step 1 + `STRICT_TENANT_SCOPE=audit` revert in env + worker restart |

## Verification

Within 5 minutes of completing Step 3:

1. `/readyz/` returns `{"status": "healthy"}` on the prod platform host.
2. End-to-end smoke from MAX channel — one real test message answered.
3. `mysite/maxbot/` MAX webhook log shows traffic flowing as primary.
4. No new Sentry P0 events in last 2 minutes.
5. `ShadowDeltaSnapshot.intent_agreement` for the just-completed day:
   recompute via `manage.py shell` if the beat hasn't fired yet.
6. Run `apps/replay/runner.py` against `golden/` fixture set — zero
   regressions vs baseline.

Time-to-stable target: **all 6 indicators green within 10 minutes of
the incident declaration**.

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 (full outage) | Platform Lead | PagerDuty / MAX admin chat |
| P1 (one tenant) | Tenant ops | Slack `#ai-bot-ops` |
| Vendor: OpenAI | account manager | dashboard.openai.com → contact |
| Vendor: Anthropic | support | console.anthropic.com → support |
| Infra: nginx / docker | DevOps lead | Slack `#ops` |

## Post-mortem template

Standard 7-bullet template — see [`_template.md`](_template.md).
Specifically capture for rollbacks:

- **What happened.** Trigger + symptom.
- **What was the trigger.** Alert source + signal latency.
- **What did we expect — what actually happened.** Canary diff
  threshold, SLO breach, prior assumption that proved false.
- **How long.** Detect / mitigate / resolve.
- **What we learned.** Could canary diff have caught it earlier?
  Did the runbook accurately match reality?
- **Action items.** Owner + deadline.
- **Was a Sprint 8 / N4 staging dry-run completed?** If no, that's an
  action item — every prod rollback path MUST first be exercised on
  staging.

## Changelog

- 2026-05-10 — Lead — skeleton committed (DRF-414)
- 2026-05-13 — Platform Lead — Sprint 8 / R1 (DRF-727) fleshed out; status
  flipped skeleton → draft. Pending: staging game-day to graduate to
  **complete** status.
