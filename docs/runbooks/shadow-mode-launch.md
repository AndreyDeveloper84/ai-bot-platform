# Runbook: Shadow-mode launch (Sprint 8 / N4)

> Status: **draft**
> Last exercised: _staging dry-run pending_
> Target completion sprint: **Sprint 8 / N4 (DRF-703)**
> Owner: Platform Lead

## Purpose

Stand up the edge nginx tee (N1 / DRF-700) on staging first, smoke
end-to-end, then schedule the production cutover window. The platform
runs alongside `mysite/maxbot/` in **shadow** — every webhook is
mirrored, the platform writes rows under `Conversation(is_shadow=True)`
and the orchestrator step-19 short-circuit (S2) drops outbound. Daily
delta (S3 / S4) measures intent agreement against the mysite CSV
ground truth.

This runbook gates **F1** (production STRICT_TENANT_SCOPE flip) — we
do not flip the prod scope until staging has been clean for ≥ 24h and
the delta dashboard sustains ≥ 95% intent agreement for 7 consecutive
days.

## Trigger / when to run

- First-time shadow rollout (this is the one-shot bootstrap).
- After any change to `infra/nginx/maxbot-tee.conf` — re-exercise the
  staging dry-run before reloading prod.
- Quarterly drills (in lieu of a real incident).

## Prerequisites

| Resource | Why |
|---|---|
| SSH access to staging nginx host (`stg.penza.taxi`) | reload the tee config |
| SSH access to platform-staging (`stg-platform.penza.taxi`) | observe shadow rows landing |
| `nginx -t` passes on the new config | catch syntax errors before reload |
| Platform staging healthy (`/readyz/` green) | otherwise the dry-run is testing the wrong failure mode |
| `apps/observability/tests/test_delta.py` passing locally | confirms S3 math + S4 task ready for shadow data |
| Test webhook payload prepared | a real MAX `message_created` JSON used in step 2 |

## Step-by-step procedure

### Step 1 — Deploy nginx tee config to staging (5 min)

```sh
ssh ops@stg.penza.taxi
sudo cp /home/ops/ai-bot-platform/infra/nginx/maxbot-tee.conf \
        /etc/nginx/sites-available/maxbot-tee.conf
sudo ln -sf /etc/nginx/sites-available/maxbot-tee.conf \
            /etc/nginx/sites-enabled/maxbot-tee.conf
sudo nginx -t   # MUST pass before reload
sudo systemctl reload nginx
```

Confirm:
```sh
sudo nginx -T | grep -A 5 'location = /shadow'
# Expect: internal; proxy_pass to 127.0.0.1:8003; X-Shadow set to "1".
```

### Step 2 — Synthetic webhook smoke (3 min)

POST a real MAX-shaped payload through the edge and verify both
upstreams saw it:

```sh
curl -fsS https://stg.penza.taxi/api/maxbot/webhook/ \
  -X POST \
  -H 'X-Max-Bot-Api-Secret: STAGING_TOKEN' \
  -H 'Content-Type: application/json' \
  -d @/home/ops/staging-fixtures/max_message_created.json
```

Expected: HTTP 200 from primary, response originates from mysite.

Check primary side wrote the row:
```sh
ssh ops@stg.penza.taxi 'sudo -u mysite \
  python /home/mysite/manage.py shell -c "from maxbot.models import Conversation; \
  print(Conversation.objects.order_by(\"-id\").first().id)"'
```

Check **shadow side** received the mirror:
```sh
ssh ops@stg-platform.penza.taxi 'sudo docker compose exec web \
  uv run python manage.py shell -c "from apps.conversations.models import Conversation; \
  print(Conversation.all_tenants.filter(is_shadow=True).count())"'
```

Should increment by exactly 1 row per webhook.

### Step 3 — Verify no platform → user outbound (2 min)

The platform must NOT have called `apps.channels.max.outbound.send_message`
(S2 short-circuit). Check the audit log:

```sh
ssh ops@stg-platform.penza.taxi 'sudo docker compose exec web \
  uv run python manage.py shell -c "from apps.audit.models import AuditLog; \
  print(AuditLog.all_tenants.filter(action=\"pipeline.shadow_dropped_outbound\").count())"'
```

Expected: ≥ 1 row, matching the smoke-POST count.

Also verify the MAX bot API access log on staging has NO outgoing
request from the platform side:
```sh
sudo tail -50 /var/log/ai-bot-platform/outbound.log
# Expect: empty or only pre-existing entries.
```

### Step 4 — 24h soak (passive)

Leave staging running with normal staging webhook traffic. Monitor:

- `/readyz/` stays green for 24h.
- Sentry P0 = 0 for the platform service.
- `ShadowDeltaSnapshot` row written for the day with non-zero
  `sample_count` (run S4 manually via `celery call apps.observability.tasks.compute_shadow_delta`).
- Primary mysite traffic unaffected — compare per-hour webhook counts
  before vs after tee deploy.

### Step 5 — Rollback drill (1 min)

Practice the kill switch:
```sh
ssh ops@stg.penza.taxi
sudo nano /etc/nginx/sites-enabled/maxbot-tee.conf
# Comment out: mirror /shadow;
sudo nginx -t && sudo systemctl reload nginx
```

Verify:
- Next webhook POST → primary 200, **NO** new `is_shadow=True`
  Conversation row on the platform side.
- Restore tee: uncomment + reload.

### Step 6 — Schedule prod cutover window

Once staging has been clean for ≥ 24h, file a deploy ticket:
- Window: low-traffic hour (typically 03:00 МСК on a Tuesday).
- Comms: post in `#ai-bot-ops` 24h ahead.
- Runbook: this document — operator follows steps 1, 2, 3 against prod
  hosts (`app.penza.taxi`, `app-platform.penza.taxi`).

## Verification (acceptance gate for prod cutover)

- [ ] Step 2 smoke produced 1 primary + 1 shadow row in staging.
- [ ] Step 3 confirmed zero platform → user outbound calls.
- [ ] 24h soak: zero P0 incidents, zero impact on mysite primary
      latency / response rate.
- [ ] Rollback drill in Step 5 restored normal staging traffic
      pattern in < 60 seconds.
- [ ] Daily `ShadowDeltaSnapshot` for staging has `sample_count > 0`.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Step 1 `nginx -t` fails | Syntax error in `maxbot-tee.conf` | Roll back via `git checkout` on the file; do NOT reload until clean |
| Step 2 primary returns 502 | Tee `mirror_request_body on` swallowed the body | Add `mirror_request_body off` to the location block; investigate why nginx version doesn't support it |
| Step 2 shadow row not created | `X-Shadow` header missing OR platform `MAX_WEBHOOK_SECRET` mismatch | Confirm N2 (ingress view) reads `X-Shadow` correctly; rotate platform secret if needed |
| Step 3 outbound call detected | S2 short-circuit not wired OR `tenant.shadow_mode` False AND header path didn't fire | Audit `is_shadow` propagation through `_run_under_tenant`; check `Tenant.shadow_mode=True` on staging tenant |
| 24h soak: primary latency degrades | mirror is backpressuring primary (should never happen, but) | Reduce `proxy_read_timeout` on `/shadow` to 2s; verify shadow upstream healthcheck |

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 (mysite affected) | Lead | PagerDuty |
| P1 (platform-only) | Platform team | `#ai-bot-ops` |
| Infra: nginx | DevOps lead | `#ops` |

## Changelog

- 2026-05-13 — Platform Lead — Sprint 8 / N4 (DRF-703) initial draft.
  Staging dry-run gate for the prod shadow cutover.
